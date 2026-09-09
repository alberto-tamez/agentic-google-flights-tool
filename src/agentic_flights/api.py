"""Small code-first API: explicit run handles, offline comparison, fresh verification."""

from __future__ import annotations

import hashlib
import inspect as introspection
import json
from typing import Any, get_type_hints

from pydantic import create_model

from agentic_flights.batch import BatchExecutor
from agentic_flights.batch_session import BatchSession
from agentic_flights.cache import FileCache
from agentic_flights.exploration import Exploration, SearchSpace
from agentic_flights.filtering import ShortlistSpec, collect_matches
from agentic_flights.models import (
    FlightOption,
    FlightSegmentIdentity,
    RankedFlight,
    SearchSpec,
)
from agentic_flights.provider import SmartProvider
from agentic_flights.store import RUN_ID, ManagedStore
from agentic_flights.views import (
    _departure_inconvenience_minutes,
    _destination_stay_minutes,
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


def _detailed_segments(
    option: FlightOption, outbound: bool
) -> tuple[list[FlightSegmentIdentity], bool]:
    identities = option.identity_segments
    if not identities and option.source_url:
        from agentic_flights.providers.parsing import _parse_booking_segment_identities

        identities = _parse_booking_segment_identities(option.source_url)
    if identities:
        return (
            [
                segment
                for segment in identities
                if not outbound or segment.journey_index == 0
            ],
            True,
        )
    journey_count = len({leg.journey_index for leg in option.legs})
    if len(option.legs) != option.stops + journey_count:
        return [], False
    return (
        [
            FlightSegmentIdentity(
                journey_index=leg.journey_index,
                origin=leg.origin,
                destination=leg.destination,
                departure_date=leg.departure_at.date(),
                airline_code=leg.airline_code,
                flight_number=leg.flight_number,
            )
            for leg in option.legs
            if not outbound or leg.journey_index == 0
        ],
        False,
    )


def _journey_signature(option: FlightOption, outbound: bool) -> tuple:
    journeys = sorted({leg.journey_index for leg in option.legs})
    if outbound:
        journeys = [index for index in journeys if index == 0]
    return tuple(
        (
            legs[0].origin,
            legs[-1].destination,
            legs[0].departure_at.isoformat(),
            legs[-1].arrival_at.isoformat(),
        )
        for index in journeys
        if (legs := [leg for leg in option.legs if leg.journey_index == index])
    )


def _compare_itineraries(
    observed: FlightOption, target: FlightOption, outbound: bool
) -> tuple[str, str]:
    """Return match, different, or insufficient_detail and the comparison basis."""
    left, left_from_provider = _detailed_segments(observed, outbound)
    right, right_from_provider = _detailed_segments(target, outbound)
    if left and right:
        if len(left) != len(right):
            return "different", "provider_segments"
        for a, b in zip(left, right, strict=True):
            if (
                (a.journey_index, a.origin, a.destination, a.departure_date)
                != (b.journey_index, b.origin, b.destination, b.departure_date)
                or (a.airline_code and b.airline_code and a.airline_code != b.airline_code)
                or (a.flight_number and b.flight_number and a.flight_number != b.flight_number)
            ):
                return "different", "provider_segments"
        if (
            (left_from_provider or right_from_provider)
            and all(a.flight_number and b.flight_number for a, b in zip(left, right, strict=True))
        ):
            return "match", "provider_segments"
        observed_signature = itinerary_signature(observed, outbound)
        target_signature = itinerary_signature(target, outbound)
        if len(observed_signature) == len(target_signature) and all(
            a[:-1] == b[:-1] and (not a[-1] or not b[-1] or a[-1] == b[-1])
            for a, b in zip(observed_signature, target_signature, strict=True)
        ):
            return "match", "detailed_schedule"
        if not left_from_provider and not right_from_provider:
            return "different", "detailed_schedule"
    if _journey_signature(observed, outbound) != _journey_signature(target, outbound):
        return "different", "journey_summary"
    return "insufficient_detail", "journey_summary"


class AgentAPI:
    def __init__(self, store: ManagedStore | None = None, executor: BatchExecutor | None = None,
                 on_progress=None):
        self.store = store or ManagedStore()
        self.on_progress = on_progress
        self.executor = executor or BatchExecutor(
            FileCache(
                self.store.provider_cache / "smart",
                namespace=SmartProvider.version,
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
        from agentic_flights.views import _coverage_complete

        state = report.exploration_state or {}
        total = (
            SearchSpace.model_validate(state["space"]).count
            if "space" in state
            else len(state["batch_input"]["searches"])
            if "batch_input" in state
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
        complete = not remaining and bool(report.outcomes) and all(
            _coverage_complete(o) for o in report.outcomes
        )
        matches = self._verification_matches(report).get("verification", [])
        verification_active = "verification_targets" in state
        verification_satisfied = bool(matches) and all(
            match["status"] == "matched_observed_itinerary" for match in matches
        )
        if verification_satisfied:
            can_continue = False
        actions = []
        if can_continue:
            actions.append({"operation": "explore", "arguments": {"run_id": run_id}})
        if not verification_satisfied and (
            failed or blocked or (not complete and not can_continue)
        ):
            actions.append({"operation": "issues", "arguments": {"run_id": run_id}})
        ids = list(dict.fromkeys(i for m in matches for i in m["matching_result_ids"]))
        evidence_status = None
        if verification_active:
            status_counts: dict[str, int] = {}
            for match in matches:
                status_counts[match["status"]] = status_counts.get(match["status"], 0) + 1
            complete_ids = {
                _result_id(RankedFlight(request_id=outcome.request_id, option=option))
                for outcome in report.outcomes
                for option in outcome.options
                if option.ticket_scope == "complete_single_ticket"
                and option.price_provenance == "provider_final_total"
            }
            matched_ids = set(ids)
            evidence_status = {
                "selected_itineraries": len(matches),
                "selected_status_counts": dict(sorted(status_counts.items())),
                "complete_quotes_found": len(complete_ids),
                "matched_complete_quotes": len(matched_ids),
                "other_complete_quotes": len(complete_ids - matched_ids),
                "coverage_complete": complete,
                "verification_satisfied": verification_satisfied,
                "claim_scope": (
                    "matched_selected_itineraries_only"
                    if matched_ids
                    else "no_selected_itinerary_confirmed"
                ),
            }
            status_by_request = {
                match["selected_result_id"]: match["status"] for match in matches
            }
            payload = {**payload}
            for key in ("preview", "results"):
                if key not in payload:
                    continue
                payload[key] = [
                    {
                        **item,
                        "selection_verification": (
                            "matched_selection"
                            if item["result_id"] in matched_ids
                            else "other_complete_quote"
                            if item["result_id"] in complete_ids
                            else "not_a_complete_quote"
                        ),
                        "selected_itinerary_status": status_by_request.get(item["request_id"]),
                    }
                    for item in payload[key]
                ]
        if ids:
            actions.append(
                {
                    "operation": "inspect",
                    "arguments": {"run_id": run_id, "result_ids": ids},
                    "reason": "Matched verification quotes",
                }
            )
        if not verification_satisfied:
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
            **({"verification": matches} if "verification_targets" in state else {}),
            **({"evidence_status": evidence_status} if evidence_status is not None else {}),
            **(
                {"verification_satisfied": verification_satisfied}
                if verification_active
                else {}
            ),
            "run_id": run_id,
            "progress": {
                "phase": "verification" if "verification_targets" in state else "exploration",
                "state": "selection_matched"
                if verification_satisfied
                else "ready"
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
                "can_retry": not verification_satisfied and bool(
                    blocked or any(o.error and o.error.retryable for o in report.outcomes)
                ),
                **(
                    {"verification_satisfied": verification_satisfied}
                    if verification_active
                    else {}
                ),
            },
            "next_actions": actions,
        }

    def schema(self, topic: str = "operations") -> dict[str, Any]:
        """Discover operation names, then request only a needed input schema."""
        schemas = {"space": SearchSpace, "search": SearchSpec, "filters": ShortlistSpec}
        if topic in schemas:
            return schemas[topic].model_json_schema()
        operations = (
            "plan",
            "start",
            "explore",
            "compare",
            "alternatives",
            "inspect",
            "verify",
            "issues",
        )
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
                "start": "exact search dicts -> run_id and first bounded work chunk",
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

    def start(
        self,
        searches: dict[str, Any] | list[dict[str, Any]],
        work_chunk: int = 8,
        chunk_seconds: float = 20,
    ) -> dict[str, Any]:
        """Start exact one-way, round-trip, open-jaw, or multi-city searches."""
        if not isinstance(searches, (dict, list)):
            raise ValueError("searches must be a search dictionary or list of dictionaries")
        items = [searches] if isinstance(searches, dict) else searches
        if not items:
            raise ValueError("searches must contain at least one search")
        specs = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                raise ValueError(f"searches[{index}] must be a search dictionary")
            query = {"request_id": str(index), "search_mode": "discover", **raw}
            query["continuation"] = None
            specs.append(SearchSpec.model_validate(query))
        config = {
            "searches": [spec.model_dump(mode="json") for spec in specs],
            "provider": "smart",
            "work_chunk": work_chunk,
            "chunk_seconds": chunk_seconds,
            "query_timeout_seconds": self.executor.query_timeout_seconds,
            "max_workers": self.executor.max_workers,
            "cache_ttl_seconds": self.executor.cache.ttl_seconds,
            "ranking_limit": self.executor.ranking_limit,
        }
        session = BatchSession(self.executor, self.store, config, progress=self.on_progress)
        report = session.execute()
        return self._save(report)

    def explore(
        self,
        run_id: str,
        work_chunk: int = 20,
        prefer: list[str] | None = None,
        retry_errors: bool = False,
        chunk_seconds: float = 20,
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
                chunk_seconds=chunk_seconds,
                on_progress=self.on_progress,
            ).model_dump(mode="json")
            summary = result.pop("summary")
            updated, _, _ = self._load(result["run_id"])
            return self._respond(updated, result["run_id"], {**summary, **result})
        if report.exploration_state and "batch_input" in report.exploration_state:
            config = {**report.exploration_state["batch_input"],
                      "work_chunk": work_chunk, "chunk_seconds": chunk_seconds}
            executor = BatchExecutor(
                FileCache(self.executor.cache.directory,
                          ttl_seconds=config.get("cache_ttl_seconds", 3600),
                          namespace=self.executor.cache.namespace),
                max_workers=self.executor.max_workers,
                ranking_limit=self.executor.ranking_limit,
                provider_factory=self.executor.provider_factory,
                query_timeout_seconds=config.get("query_timeout_seconds", 60),
            )
            session = BatchSession(
                executor, self.store, config, previous=report,
                progress=self.on_progress, state=report.exploration_state,
            )
            updated = session.execute(retry_errors=retry_errors)
            return {**self._save(updated), **self._verification_matches(updated)}
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
        from agentic_flights.views import _decode_cursor, _encode_cursor

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
            av = [a.price, a.duration_minutes, a.stops, _departure_inconvenience_minutes(a)]
            bv = [b.price, b.duration_minutes, b.stops, _departure_inconvenience_minutes(b)]
            a_stay, b_stay = _destination_stay_minutes(a), _destination_stay_minutes(b)
            if a_stay is not None and b_stay is not None:
                av.append(-a_stay)
                bv.append(-b_stay)
            return all(x <= y for x, y in zip(av, bv, strict=True)) and av != bv

        frontier = [
            item
            for item in items
            if not any(dominates(other.option, item.option) for other in items)
        ]
        ids = [_result_id(item) for item in frontier]
        selected = ids[offset : offset + page_size]
        indexed = {_result_id(item): item for item in frontier}

        def tradeoff_labels(item):
            option = item.option
            comparable = [
                candidate.option
                for candidate in frontier
                if (
                    candidate.option.ticket_scope,
                    candidate.option.result_scope,
                    candidate.option.price_provenance,
                    candidate.option.currency,
                )
                == (
                    option.ticket_scope,
                    option.result_scope,
                    option.price_provenance,
                    option.currency,
                )
            ]
            labels = []
            prices = [candidate.price for candidate in comparable if candidate.price is not None]
            if option.price is not None and prices and option.price == min(prices):
                labels.append("lowest_price")
            if option.duration_minutes == min(
                candidate.duration_minutes for candidate in comparable
            ):
                labels.append("shortest_travel_time")
            if option.stops == min(candidate.stops for candidate in comparable):
                labels.append("fewest_stops")
            if _departure_inconvenience_minutes(option) == min(
                _departure_inconvenience_minutes(candidate) for candidate in comparable
            ):
                labels.append("least_departure_inconvenience")
            stays = [
                stay
                for candidate in comparable
                if (stay := _destination_stay_minutes(candidate)) is not None
            ]
            if (
                stays
                and (stay := _destination_stay_minutes(option)) is not None
                and stay == max(stays)
            ):
                labels.append("most_destination_time")
            return labels

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
                "alternatives": [
                    {
                        "result_id": result_id,
                        "strengths": tradeoff_labels(indexed[result_id]),
                        "evidence_level": (
                            "provider_final_total"
                            if indexed[result_id].option.ticket_scope == "complete_single_ticket"
                            and indexed[result_id].option.price_provenance
                            == "provider_final_total"
                            else "observed_price"
                        ),
                    }
                    for result_id in selected
                ],
                "total_alternatives": len(ids),
                "next_cursor": next_cursor,
                "evaluated": len(items),
                "decision_policy": "unranked_pareto_frontier",
                "hidden_weights": False,
                "meaning": (
                    "Strictly dominated options removed within comparable evidence and currency; "
                    "remaining options are tradeoffs, not a best-to-worst ranking"
                ),
                "tradeoff_dimensions": [
                    "price",
                    "travel_duration",
                    "stops",
                    "early_or_late_departures",
                    "destination_time",
                ],
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
                or c.budget_exhausted
                or c.continuation
            ):
                spec = outcome.search_spec
                codes = []
                if outcome.error:
                    codes.append(outcome.error.code)
                if c.blocked:
                    codes.append("blocked")
                codes.extend(c.branch_errors_by_code)
                if c.source_parse_failures:
                    codes.append("source_parse_failure")
                if c.source_truncated:
                    codes.append("source_truncated")
                if c.budget_exhausted:
                    codes.append("work_budget_reached")
                if c.continuation and not c.blocked:
                    codes.append("continuation_pending")
                codes = list(dict.fromkeys(codes))
                primary = codes[0]
                message = (
                    outcome.error.message
                    if outcome.error
                    else c.branch_error_samples[0].message
                    if c.branch_error_samples
                    else "The provider stopped before this search was fully explored."
                    if c.blocked
                    else "Google returned only part of the available result set."
                    if c.source_truncated
                    else "This work chunk reached its limit before the search finished."
                    if c.budget_exhausted
                    else "The search has saved work that can be continued."
                    if c.continuation
                    else "Some provider results could not be parsed."
                )
                issues.append(
                    {
                        "request_id": outcome.request_id,
                        "code": primary,
                        "codes": codes,
                        "message": message,
                        "retryable": bool(
                            (outcome.error and outcome.error.retryable)
                            or c.blocked
                            or c.budget_exhausted
                            or c.continuation
                            or any(error.retryable for error in c.branch_error_samples)
                        ),
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
        work_quotes: int | None = 3,
        max_browser_transitions: int | None = 12,
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
                "max_browser_transitions": max_browser_transitions,
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
            query_timeout_seconds=self.executor.query_timeout_seconds,
        )
        config = {
            "searches": [spec.model_dump(mode="json") for spec in specs],
            "provider": "smart", "work_chunk": len(specs), "chunk_seconds": 20,
            "query_timeout_seconds": fresh.query_timeout_seconds,
            "max_workers": fresh.max_workers, "cache_ttl_seconds": 0,
            "ranking_limit": fresh.ranking_limit,
        }
        session = BatchSession(
            fresh, self.store, config, progress=self.on_progress,
            state={"verification_targets": targets},
        )
        report = session.execute()
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
            requested_journeys = (
                set(range(len(outcome.search_spec.requested_segments())))
                if outcome.search_spec is not None
                else {leg.journey_index for option in [target, *outcome.options]
                      for leg in option.legs}
            )
            compared_journeys = {0} if outbound else {leg.journey_index for leg in target.legs}
            uncompared_journeys = sorted(requested_journeys - compared_journeys)
            complete_quotes = [
                option
                for option in outcome.options
                if option.ticket_scope == "complete_single_ticket"
                and option.price_provenance == "provider_final_total"
            ]
            comparisons = [
                (option, *_compare_itineraries(option, target, outbound))
                for option in complete_quotes
            ]
            matched = [option for option, result, _ in comparisons if result == "match"]
            insufficient = [
                option for option, result, _ in comparisons if result == "insufficient_detail"
            ]
            basis = next(
                (basis for _, result, basis in comparisons if result == "match"),
                next(
                    (basis for _, result, basis in comparisons if result == "insufficient_detail"),
                    next((basis for _, _, basis in comparisons), "none"),
                ),
            )
            status = (
                "matched_observed_itinerary"
                if matched
                else "blocked"
                if outcome.coverage.blocked
                else "pending"
                if outcome.coverage.continuation
                else "insufficient_detail"
                if insufficient
                else "not_matched"
            )
            matches.append(
                {
                    "selected_result_id": outcome.request_id,
                    "status": status,
                    "match_scope": "outbound" if outbound else "whole_itinerary",
                    "whole_itinerary_matched": bool(matched) and not outbound
                    and not uncompared_journeys,
                    "uncompared_journey_indexes": uncompared_journeys,
                    "identity_basis": basis,
                    "complete_quotes_found": len(complete_quotes),
                    "matching_quotes": len(matched),
                    "matching_result_ids": [
                        _result_id(RankedFlight(request_id=outcome.request_id, option=option))
                        for option in matched
                    ],
                    "evidence": _verification_evidence(status, outbound, basis),
                }
            )
        return {"verification": matches} if targets else {}


def _verification_evidence(status: str, outbound: bool, basis: str) -> str:
    scope = (
        "Return/onward flights were not compared; only the outbound is verified. "
        if outbound
        else "The whole selected itinerary was compared. "
    )
    if status == "matched_observed_itinerary" and basis == "provider_segments":
        return scope + (
            "Every selected connection matched provider route, date, carrier, and flight number."
        )
    if status == "matched_observed_itinerary":
        return scope + (
            "The detailed route, local times, airline, and available flight numbers matched."
        )
    if status == "insufficient_detail":
        return scope + "Journey summaries matched, but connection-level identity was unavailable."
    if status == "not_matched":
        return scope + (
            "At least one route, date, time, carrier, flight number, or connection changed."
        )
    if status == "pending":
        return scope + "Verification still has unexplored quote branches."
    return scope + "The provider blocked verification before the selection could be confirmed."
