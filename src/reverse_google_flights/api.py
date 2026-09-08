"""Small code-first API: explicit run handles, offline comparison, fresh verification."""

from __future__ import annotations

import hashlib
import inspect as introspection
import json
from typing import Any, get_type_hints

from pydantic import create_model

from reverse_google_flights.batch import BatchExecutor
from reverse_google_flights.cache import FileCache
from reverse_google_flights.exploration import Exploration, SearchSpace
from reverse_google_flights.filtering import ShortlistSpec, collect_matches
from reverse_google_flights.models import FlightOption, RankedFlight, SearchSpec
from reverse_google_flights.provider import BrowserProvider
from reverse_google_flights.store import RUN_ID, ManagedStore
from reverse_google_flights.views import (
    _result_id,
    compact_summary,
    list_page,
    load_report,
    show_results,
)


def itinerary_signature(option: FlightOption, outbound_only: bool = False) -> tuple:
    return tuple(
        (
            leg.journey_index,
            leg.origin,
            leg.destination,
            leg.departure_at.isoformat(),
            leg.arrival_at.isoformat(),
            leg.airline_name,
            leg.flight_number,
        )
        for leg in option.legs
        if not outbound_only or leg.journey_index == 0
    )


class AgentAPI:
    def __init__(self, store: ManagedStore | None = None, executor: BatchExecutor | None = None):
        self.store = store or ManagedStore()
        self.executor = executor or BatchExecutor(
            FileCache(
                self.store.provider_cache / "browser",
                namespace=BrowserProvider.version,
            )
        )

    def _load(self, run_id: str):
        # MCP handles must never become arbitrary local filesystem reads.
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("Expected a saved rgf_ run ID")
        path, _ = self.store.resolve(run_id)
        report, checksum = load_report(path)
        return report, checksum, path

    def _save(self, report):
        encoded = report.model_dump_json()
        run_id, path = self.store.save(encoded)
        summary = compact_summary(
            report, hashlib.sha256(encoded.encode()).hexdigest(), path, reference=run_id
        )
        return self._respond(report, run_id, summary)

    def _respond(self, report, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Common state and executable next actions across every data operation."""
        from reverse_google_flights.views import _coverage_complete

        state = report.exploration_state or {}
        total = (
            SearchSpace.model_validate(state["space"]).count
            if "space" in state
            else len(report.outcomes)
        )
        remaining = max(0, total - len(report.outcomes))
        pending = sum(bool(o.coverage.continuation) for o in report.outcomes)
        blocked = sum(o.coverage.blocked for o in report.outcomes)
        failed = sum(o.error is not None for o in report.outcomes)
        can_continue = bool(
            remaining
            or any(o.coverage.continuation and not o.coverage.blocked for o in report.outcomes)
        )
        complete = bool(report.outcomes) and all(_coverage_complete(o) for o in report.outcomes)
        actions = []
        if can_continue:
            actions.append({"operation": "explore", "arguments": {"run_id": run_id}})
        if failed or blocked or (not complete and not can_continue):
            actions.append({"operation": "issues", "arguments": {"run_id": run_id}})
        matches = self._verification_matches(report).get("verification", [])
        ids = list(dict.fromkeys(i for m in matches for i in m["matching_result_ids"]))
        if ids:
            actions.append(
                {
                    "operation": "inspect",
                    "arguments": {"run_id": run_id, "result_ids": ids},
                    "reason": "Matched verification quotes",
                }
            )
        currencies = sorted({v.currency for o in report.outcomes for v in o.options})
        for currency in currencies:
            actions.append(
                {
                    "operation": "compare",
                    "arguments": {"run_id": run_id, "filters": {"currency": currency}},
                }
            )
        return {
            **payload,
            "run_id": run_id,
            "progress": {
                "phase": "verification" if "verification_targets" in state else "exploration",
                "state": "ready"
                if not report.outcomes and remaining
                else "in_progress"
                if can_continue
                else "needs_attention"
                if failed or blocked
                else "complete"
                if complete
                else "incomplete_coverage",
                "attempted_queries": len(report.outcomes),
                "remaining_queries": remaining,
                "pending_queries": pending,
                "blocked_queries": blocked,
                "failed_queries": failed,
                "coverage_complete": complete,
                "can_continue": can_continue,
                "can_retry": bool(
                    blocked or any(o.error and o.error.retryable for o in report.outcomes)
                ),
            },
            "next_actions": actions,
        }

    def schema(self, topic: str = "operations") -> dict[str, Any]:
        """Discover operation names, then request only a needed input schema."""
        schemas = {"space": SearchSpace, "search": SearchSpec, "filters": ShortlistSpec}
        if topic in schemas:
            return schemas[topic].model_json_schema()
        operations = ("plan", "explore", "compare", "alternatives", "inspect", "verify", "issues")
        if topic in operations:
            method = getattr(self, topic)
            hints = get_type_hints(method)
            fields = {
                name: (hints[name], ... if p.default is p.empty else p.default)
                for name, p in introspection.signature(method).parameters.items()
            }
            return {
                "operation": topic,
                "description": method.__doc__,
                "input_schema": create_model(topic + "Input", **fields).model_json_schema(),
            }
        if topic != "operations":
            raise ValueError("Topics: operations, space, search, filters, " + ", ".join(operations))
        return {
            "operations": {
                "plan": "space -> run_id, no flight requests",
                "explore": "run_id, work_chunk, prefer -> progress; repeat until complete",
                "compare": "run_id, filters, page_size, cursor -> offline ranked page",
                "inspect": "run_id, result_ids -> selected full evidence and original query",
                "verify": "selected IDs -> fresh quotes and itinerary matches",
                "alternatives": "run_id, filters -> tradeoffs within comparable ticket scopes",
                "issues": "run_id -> paginated errors and incomplete coverage",
            },
            "schema_topics": [*schemas, *operations],
            "response_contract": "Data responses include run_id, progress, next_actions",
            "stopping": "work_chunk_complete means continue, not best flight found",
        }

    def plan(self, space: dict[str, Any]) -> dict[str, Any]:
        """Validate a flexible trip and save its plan without contacting Google."""
        parsed = SearchSpace.model_validate(space)
        if parsed.template.continuation is not None:
            raise ValueError("Start plans without continuation; resume using the saved run ID")
        exploration = Exploration(parsed, self.executor, self.store)
        report = self.executor.summarize([])
        report.exploration_state = {
            "version": 1,
            "identity": exploration.identity,
            "space": parsed.model_dump(mode="json"),
            "failure_history": [],
        }
        saved = self._save(report)
        return {
            "run_id": saved["run_id"],
            "combinations": parsed.count,
            "next": "explore",
            "ordering": "airport pairs and dates before stay lengths",
            "progress": saved["progress"],
            "next_actions": saved["next_actions"],
        }

    def explore(
        self,
        run_id: str,
        work_chunk: int = 20,
        prefer: list[str] | None = None,
        retry_errors: bool = False,
    ) -> dict[str, Any]:
        """Advance a saved plan or pending verification; all state is in the handle."""
        report, _, _ = self._load(run_id)
        if report.exploration_state and "space" in report.exploration_state:
            explorer = Exploration.restore(run_id, self.executor, self.store)
            result = explorer.advance(
                resume_from=run_id,
                search_budget=work_chunk,
                prefer=prefer,
                retry_errors=retry_errors,
            ).model_dump(mode="json")
            summary = result.pop("summary")
            updated, _, _ = self._load(result["run_id"])
            return self._respond(updated, result["run_id"], {**summary, **result})
        pending = [
            o
            for o in report.outcomes
            if (o.coverage.continuation and not o.coverage.blocked)
            or (retry_errors and (o.error or o.coverage.blocked))
        ]
        if work_chunk < 1:
            raise ValueError("work_chunk must be positive")
        specs = []
        for outcome in pending[:work_chunk]:
            if outcome.search_spec is None:
                raise ValueError("Missing original query; cannot continue")
            specs.append(
                outcome.search_spec.model_copy(
                    update={"continuation": outcome.coverage.continuation}
                )
            )
        latest = {o.request_id: o for o in report.outcomes}
        latest.update({o.request_id: o for o in self.executor.execute(specs).outcomes})
        merged = self.executor.summarize(latest.values())
        merged.exploration_state = report.exploration_state
        return {**self._save(merged), **self._verification_matches(merged)}

    def compare(
        self,
        run_id: str,
        filters: dict[str, Any] | None = None,
        page_size: int = 5,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Filter and rank offline; select a currency for mixed-currency price comparisons."""
        report, checksum, path = self._load(run_id)
        page = list_page(
            report,
            checksum,
            path,
            ShortlistSpec.model_validate(filters or {}),
            page_size=page_size,
            cursor=cursor,
            reference=run_id,
        )
        page["comparison_order"] = "ticket scope, then requested sort keys within each scope"
        response = self._respond(report, run_id, page)
        if page["next_cursor"]:
            response["next_actions"].insert(
                0,
                {
                    "operation": "compare",
                    "arguments": {
                        "run_id": run_id,
                        "filters": filters,
                        "page_size": page_size,
                        "cursor": page["next_cursor"],
                    },
                },
            )
        return response

    def alternatives(
        self,
        run_id: str,
        filters: dict[str, Any] | None = None,
        page_size: int = 5,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Page through observed tradeoff IDs; never compare partial and complete tickets."""
        from reverse_google_flights.views import _decode_cursor, _encode_cursor

        if page_size < 1:
            raise ValueError("page_size must be positive")
        report, checksum, _ = self._load(run_id)
        parsed = ShortlistSpec.model_validate(filters or {})
        filter_hash = hashlib.sha256(
            (
                "alternatives:"
                + json.dumps(parsed.model_dump(mode="json", exclude={"limit"}), sort_keys=True)
            ).encode()
        ).hexdigest()
        offset = _decode_cursor(cursor, checksum, filter_hash) if cursor else 0
        items = collect_matches(report, parsed).matches

        def dominates(a, b):
            if (a.ticket_scope, a.result_scope, a.price_provenance) != (
                b.ticket_scope,
                b.result_scope,
                b.price_provenance,
            ):
                return False
            if a.currency != b.currency or a.price is None or b.price is None:
                return False
            # Unknown or different baggage evidence cannot dominate another fare.
            if (
                (a.baggage.status, sorted(a.baggage.applies_to_journeys)) if a.baggage else None
            ) != ((b.baggage.status, sorted(b.baggage.applies_to_journeys)) if b.baggage else None):
                return False
            av, bv = (a.price, a.duration_minutes, a.stops), (b.price, b.duration_minutes, b.stops)
            return all(x <= y for x, y in zip(av, bv, strict=True)) and av != bv

        ids = [
            _result_id(item)
            for item in items
            if not any(dominates(other.option, item.option) for other in items)
        ]
        selected = ids[offset : offset + page_size]
        next_cursor = (
            _encode_cursor(checksum, filter_hash, offset + len(selected))
            if offset + len(selected) < len(ids)
            else None
        )
        response = self._respond(
            report,
            run_id,
            {
                "run_id": run_id,
                "result_ids": selected,
                "total_alternatives": len(ids),
                "next_cursor": next_cursor,
                "evaluated": len(items),
                "meaning": "Tradeoffs grouped by comparable ticket scope and currency",
            },
        )
        if next_cursor:
            response["next_actions"].insert(
                0,
                {
                    "operation": "alternatives",
                    "arguments": {
                        "run_id": run_id,
                        "filters": filters,
                        "page_size": page_size,
                        "cursor": next_cursor,
                    },
                },
            )
        return response

    def inspect(self, run_id: str, result_ids: list[str]) -> dict[str, Any]:
        """Retrieve selected quote evidence and original queries; an empty selection returns []."""
        report, checksum, path = self._load(run_id)
        response = self._respond(
            report, run_id, show_results(report, checksum, path, result_ids, reference=run_id)
        )
        return response

    def issues(self, run_id: str, page_size: int = 5, offset: int = 0) -> dict[str, Any]:
        """Read actionable query errors and incomplete coverage without dumping the full report."""
        if page_size < 1 or offset < 0:
            raise ValueError("page_size must be positive and offset non-negative")
        report, _, _ = self._load(run_id)
        issues = []
        for outcome in report.outcomes:
            c = outcome.coverage
            if (
                outcome.error
                or c.blocked
                or c.branch_errors
                or c.source_parse_failures
                or c.source_truncated
            ):
                spec = outcome.search_spec
                issues.append(
                    {
                        "request_id": outcome.request_id,
                        "query": spec.model_dump(
                            mode="json", exclude={"continuation", "preferred_outbound"}
                        )
                        if spec
                        else None,
                        "error": outcome.error.model_dump(exclude={"details"})
                        if outcome.error
                        else None,
                        "branch_errors": [
                            e.model_dump(exclude={"details"}) for e in c.branch_error_samples
                        ],
                        "parse_failures": c.source_parse_failures,
                        "source_stop_reason": c.source_load_stop_reason,
                        "pending_branches": c.pending_branches,
                        "blocked": c.blocked,
                    }
                )
        page = issues[offset : offset + page_size]
        response = self._respond(
            report,
            run_id,
            {
                "issues": page,
                "total_issues": len(issues),
                "next_offset": offset + len(page) if offset + len(page) < len(issues) else None,
            },
        )
        if response["next_offset"] is not None:
            response["next_actions"].insert(
                0,
                {
                    "operation": "issues",
                    "arguments": {
                        "run_id": run_id,
                        "page_size": page_size,
                        "offset": response["next_offset"],
                    },
                },
            )
        return response

    def verify(
        self,
        run_id: str,
        result_ids: list[str],
        require_bag: bool = False,
        work_quotes: int | None = None,
    ) -> dict[str, Any]:
        """Fetch fresh final quotes; report whether each selected observed itinerary matched."""
        result_ids = list(dict.fromkeys(result_ids))
        if not result_ids:
            report, _, _ = self._load(run_id)
            return {**self._save(report), "verification": [], "selection_status": "empty"}
        details = self.inspect(run_id, result_ids)
        specs, targets = [], {}
        for item in details["results"]:
            if item["search_spec"] is None:
                raise ValueError("This legacy result lacks its original query")
            query = {
                **item["search_spec"],
                "request_id": item["result_id"],
                "search_mode": "verify",
                "continuation": None,
                "max_complete_quotes": work_quotes,
                "preferred_outbound": item["option"],
            }
            if require_bag:
                query.update(
                    require_overhead_cabin_bag=True,
                    overhead_cabin_bags=max(1, query["overhead_cabin_bags"]),
                )
            specs.append(SearchSpec.model_validate(query))
            targets[item["result_id"]] = item["option"]
        # Verification must observe current inventory, not replay an earlier cached price.
        fresh = BatchExecutor(
            FileCache(
                self.executor.cache.directory,
                ttl_seconds=0,
                namespace=self.executor.cache.namespace,
            ),
            max_workers=self.executor.max_workers,
            provider_factory=self.executor.provider_factory,
        )
        report = fresh.execute(specs)
        report.exploration_state = {"verification_targets": targets}
        return {**self._save(report), **self._verification_matches(report)}

    @staticmethod
    def _verification_matches(report) -> dict[str, Any]:
        targets = (report.exploration_state or {}).get("verification_targets", {})
        matches = []
        for outcome in report.outcomes:
            if outcome.request_id not in targets:
                continue
            target = FlightOption.model_validate(targets[outcome.request_id])
            outbound = target.result_scope == "outbound_choice"
            matched = [
                o
                for o in outcome.options
                if o.ticket_scope == "complete_single_ticket"
                and o.price_provenance == "provider_final_total"
                and itinerary_signature(o, outbound) == itinerary_signature(target, outbound)
            ]
            matches.append(
                {
                    "selected_result_id": outcome.request_id,
                    "status": "matched_observed_itinerary"
                    if matched
                    else "blocked"
                    if outcome.coverage.blocked
                    else "pending"
                    if outcome.coverage.continuation
                    else "not_matched",
                    "match_scope": "outbound" if outbound else "whole_itinerary",
                    "matching_quotes": len(matched),
                    "matching_result_ids": [
                        _result_id(RankedFlight(request_id=outcome.request_id, option=option))
                        for option in matched
                    ],
                    "evidence": "recorded route, local times, airline and flight number when known",
                }
            )
        return {"verification": matches} if targets else {}
