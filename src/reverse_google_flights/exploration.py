"""Bounded trip exploration with explicit, immutable continuation handles."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reverse_google_flights.batch import BatchExecutor
from reverse_google_flights.models import SCHEMA_VERSION, SearchSpec
from reverse_google_flights.store import ManagedStore
from reverse_google_flights.views import compact_summary, load_report


class SearchSpace(BaseModel):
    """Expand explicit airports, inclusive departure dates, and optional stay lengths.

    The template supplies passenger, fare, locale, and retrieval settings. Its
    route/date/request ID are replaced. Multi-city templates are not supported.
    """

    model_config = ConfigDict(extra="forbid")

    template: SearchSpec
    origins: list[str] = Field(min_length=1)
    destinations: list[str] = Field(min_length=1)
    departure_start: date
    departure_end: date
    min_nights: int | None = Field(default=None, ge=0)
    max_nights: int | None = Field(default=None, ge=0)

    @field_validator("origins", "destinations")
    @classmethod
    def airports(cls, values: list[str]) -> list[str]:
        return sorted({SearchSpec.validate_airport(value.strip()) for value in values})

    @model_validator(mode="after")
    def validate_space(self) -> SearchSpace:
        if self.template.additional_segments or self.template.return_date is not None:
            raise ValueError("use a one-way template; min_nights/max_nights define returns")
        if self.departure_end < self.departure_start:
            raise ValueError("departure window must be ordered")
        if (self.min_nights is None) != (self.max_nights is None):
            raise ValueError("provide both min_nights and max_nights, or neither")
        if self.min_nights is not None:
            if self.min_nights > self.max_nights:
                raise ValueError("min_nights must not exceed max_nights")
            if self.max_nights > (date.max - self.departure_end).days:
                raise ValueError("return dates exceed the supported date range")
        if self.count < 1:
            raise ValueError("search space must contain at least one combination")
        return self

    @property
    def count(self) -> int:
        routes = sum(a != b for a in self.origins for b in self.destinations)
        stays = 1 if self.min_nights is None else self.max_nights - self.min_nights + 1
        return routes * ((self.departure_end - self.departure_start).days + 1) * stays

    def search_at(self, index: int) -> SearchSpec:
        if not 0 <= index < self.count:
            raise IndexError(index)
        routes = [(a, b) for a in self.origins for b in self.destinations if a != b]
        days = (self.departure_end - self.departure_start).days + 1
        origin, destination = routes[index % len(routes)]
        date_index = index // len(routes)
        departure = self.departure_start + timedelta(days=date_index % days)
        nights = None if self.min_nights is None else self.min_nights + date_index // days
        return SearchSpec.model_validate(
            {
                **self.template.model_dump(),
                "request_id": str(index),
                "origin": origin,
                "destination": destination,
                "departure_date": departure,
                "return_date": None if nights is None else departure + timedelta(days=nights),
            }
        )

    def searches(self) -> Iterator[SearchSpec]:
        """Visit all airport pairs and dates before advancing stay lengths."""
        for index in range(self.count):
            yield self.search_at(index)


class ExplorationProgress(BaseModel):
    run_id: str
    total: int
    attempted: int
    remaining: int
    failed: int
    search_space_exhausted: bool
    batch_network_requests: int
    summary: dict[str, Any]
    pending_queries: int = 0
    stop_reason: str = "work_chunk_complete"
    failure_events: int = 0


class Exploration:
    """Advance one bounded batch at a time; keep full outcomes behind a run ID.

    Resume with the returned run ID and identical space/provider settings.
    Old IDs remain immutable snapshots; reusing one creates a branch. Failed
    queries count as attempted and remain visible, rather than retrying forever.
    """

    def __init__(self, space: SearchSpace, executor: BatchExecutor, store: ManagedStore):
        self.space = space.model_copy(deep=True)
        self.executor = executor
        self.store = store
        # Bind continuation IDs to search semantics and provider cache namespace.
        payload = self.space.model_dump_json() + executor.cache.namespace + SCHEMA_VERSION
        self.identity = hashlib.sha256(payload.encode()).hexdigest()[:24]

    @classmethod
    def restore(cls, run_id: str, executor: BatchExecutor, store: ManagedStore) -> Exploration:
        report, _ = load_report(store.resolve(run_id)[0])
        state = report.exploration_state
        if not state or "space" not in state:
            raise ValueError("This report has no saved exploration plan")
        return cls(SearchSpace.model_validate(state["space"]), executor, store)

    def advance(
        self,
        *,
        resume_from: str | None = None,
        search_budget: int = 20,
        prefer: list[str] | None = None,
        retry_errors: bool = False,
    ) -> ExplorationProgress:
        if (
            isinstance(search_budget, bool)
            or not isinstance(search_budget, int)
            or search_budget < 1
        ):
            raise ValueError("search_budget must be a positive integer")
        priorities = prefer or ["price", "duration", "stops"]
        if not priorities or any(x not in {"price", "duration", "stops"} for x in priorities):
            raise ValueError("prefer must contain price, duration, or stops")
        outcomes = []
        history = []
        if resume_from is not None:
            previous, _ = load_report(self.store.resolve(resume_from)[0])
            state = previous.exploration_state or {}
            if state.get("identity") != self.identity:
                raise ValueError("resume source does not match this search space and provider")
            outcomes = previous.outcomes
            history = list(state.get("failure_history", []))
            if prefer is None:
                priorities = state.get("priorities", priorities)
        by_id = {o.request_id: o for o in outcomes}
        next_index = len(outcomes)
        pending = [o for o in outcomes if o.coverage.continuation or (retry_errors and o.error)]

        def priority(outcome):
            def key(option):
                values = {
                    "price": option.price if option.price is not None else float("inf"),
                    "duration": option.duration_minutes,
                    "stops": option.stops,
                }
                return tuple(values[x] for x in priorities)

            return min((key(option) for option in outcome.options), default=(float("inf"),))

        # This space has one currency. Give promising unfinished queries attention
        # while interleaving unseen airport/date combinations; never discard either.
        pending.sort(key=priority)
        selected = []
        while len(selected) < search_budget and (pending or next_index < self.space.count):
            if pending and (len(selected) % 2 == 0 or next_index == self.space.count):
                old = pending.pop(0)
                spec = old.search_spec
                if spec is None:
                    raise ValueError("Cannot resume a query without its saved search_spec")
                spec = spec.model_copy(update={"continuation": old.coverage.continuation})
            else:
                spec = self.space.search_at(next_index)
                spec.request_id = f"{self.identity}:{next_index}"
                next_index += 1
            selected.append(spec)
        batch = self.executor.execute(selected)
        for outcome in batch.outcomes:
            if outcome.error:
                history.append(
                    {"request_id": outcome.request_id, "error": outcome.error.model_dump()}
                )
            old = by_id.get(outcome.request_id)
            if old:
                outcome.requests_made += old.requests_made
            by_id[outcome.request_id] = outcome
        report = self.executor.summarize(by_id.values())
        report.exploration_state = {
            "version": 1,
            "identity": self.identity,
            "space": self.space.model_dump(mode="json"),
            "failure_history": history,
            "priorities": priorities,
        }
        encoded = report.model_dump_json()
        run_id, path = self.store.save(encoded)
        summary = compact_summary(
            report, hashlib.sha256(encoded.encode()).hexdigest(), path, reference=run_id
        )
        attempted = len(report.outcomes)
        pending_count = sum(bool(o.coverage.continuation) for o in report.outcomes)
        exhausted = attempted == self.space.count and not pending_count
        return ExplorationProgress(
            run_id=run_id,
            total=self.space.count,
            attempted=attempted,
            remaining=self.space.count - attempted,
            failed=report.counts.error,
            search_space_exhausted=exhausted,
            pending_queries=pending_count,
            batch_network_requests=batch.counts.network_requests,
            stop_reason=(
                "queries_attempted_with_errors"
                if exhausted and report.counts.error
                else "search_space_exhausted"
                if exhausted
                else "work_chunk_complete"
            ),
            failure_events=len(history),
            summary=summary,
        )
