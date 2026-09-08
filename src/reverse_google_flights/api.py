"""Small code-first API: explicit run handles, offline comparison, fresh verification."""

from __future__ import annotations

import hashlib
from typing import Any

from reverse_google_flights.batch import BatchExecutor
from reverse_google_flights.cache import FileCache
from reverse_google_flights.exploration import Exploration, SearchSpace
from reverse_google_flights.filtering import ShortlistSpec, collect_matches
from reverse_google_flights.models import FlightOption, SearchSpec
from reverse_google_flights.provider import BrowserProvider
from reverse_google_flights.store import RUN_ID, ManagedStore
from reverse_google_flights.views import compact_summary, list_page, load_report, show_results


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
        return {"run_id": run_id, **summary}

    def schema(self, topic: str = "operations") -> dict[str, Any]:
        """Discover operation names, then request only a needed input schema."""
        schemas = {"space": SearchSpace, "search": SearchSpec, "filters": ShortlistSpec}
        if topic in schemas:
            return schemas[topic].model_json_schema()
        if topic != "operations":
            raise ValueError("Topics: operations, space, search, filters")
        return {
            "operations": {
                "plan": "space -> run_id, no flight requests",
                "explore": "run_id, work_chunk, prefer -> progress; repeat until complete",
                "compare": "run_id, filters, page_size, cursor -> offline ranked page",
                "inspect": "run_id, result_ids -> selected full evidence and original query",
                "verify": "selected IDs -> fresh quotes and itinerary matches",
            },
            "schema_topics": list(schemas),
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
            return explorer.advance(
                resume_from=run_id,
                search_budget=work_chunk,
                prefer=prefer,
                retry_errors=retry_errors,
            ).model_dump(mode="json")
        pending = [
            o for o in report.outcomes if o.coverage.continuation or (retry_errors and o.error)
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
        return list_page(
            report,
            checksum,
            path,
            ShortlistSpec.model_validate(filters or {}),
            page_size=page_size,
            cursor=cursor,
            reference=run_id,
        )

    def alternatives(self, run_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return IDs on the price/duration/stops frontier, within one currency."""
        report, _, _ = self._load(run_id)
        items = collect_matches(report, ShortlistSpec.model_validate(filters or {})).matches
        from reverse_google_flights.views import _result_id

        def dominates(a, b):
            if a.currency != b.currency or a.price is None or b.price is None:
                return False
            # Unknown or different baggage evidence cannot dominate another fare.
            if (a.baggage.model_dump() if a.baggage else None) != (
                b.baggage.model_dump() if b.baggage else None
            ):
                return False
            av, bv = (a.price, a.duration_minutes, a.stops), (b.price, b.duration_minutes, b.stops)
            return all(x <= y for x, y in zip(av, bv, strict=True)) and av != bv

        ids = [
            _result_id(item)
            for item in items
            if not any(dominates(other.option, item.option) for other in items)
        ]
        return {
            "run_id": run_id,
            "result_ids": ids,
            "evaluated": len(items),
            "meaning": "No other observed fare improves a tradeoff without worsening another",
        }

    def inspect(self, run_id: str, result_ids: list[str]) -> dict[str, Any]:
        report, checksum, path = self._load(run_id)
        return show_results(report, checksum, path, result_ids, reference=run_id)

    def verify(
        self,
        run_id: str,
        result_ids: list[str],
        require_bag: bool = False,
        work_quotes: int | None = None,
    ) -> dict[str, Any]:
        """Fetch fresh final quotes; report whether each selected observed itinerary matched."""
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
                    else "pending"
                    if outcome.coverage.continuation
                    else "not_matched",
                    "match_scope": "outbound" if outbound else "whole_itinerary",
                    "matching_quotes": len(matched),
                    "evidence": "recorded route, local times, airline and flight number when known",
                }
            )
        return {"verification": matches} if targets else {}
