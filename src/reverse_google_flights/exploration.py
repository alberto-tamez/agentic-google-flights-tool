"""Bounded trip exploration with explicit, immutable continuation handles."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date, timedelta
from itertools import islice, product
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
    origins: list[str] = Field(min_length=1, max_length=50)
    destinations: list[str] = Field(min_length=1, max_length=50)
    departure_start: date
    departure_end: date
    min_nights: int | None = Field(default=None, ge=0, le=365)
    max_nights: int | None = Field(default=None, ge=0, le=365)

    @field_validator("origins", "destinations")
    @classmethod
    def airports(cls, values: list[str]) -> list[str]:
        return sorted({SearchSpec.validate_airport(value.strip()) for value in values})

    @model_validator(mode="after")
    def validate_space(self) -> SearchSpace:
        if self.template.additional_segments or self.template.return_date is not None:
            raise ValueError("use a one-way template; min_nights/max_nights define returns")
        if not 0 <= (self.departure_end - self.departure_start).days <= 365:
            raise ValueError("departure window must be ordered and at most 366 days")
        if (self.min_nights is None) != (self.max_nights is None):
            raise ValueError("provide both min_nights and max_nights, or neither")
        if self.min_nights is not None:
            if self.min_nights > self.max_nights:
                raise ValueError("min_nights must not exceed max_nights")
            if self.departure_end > date.max - timedelta(days=self.max_nights):
                raise ValueError("return dates exceed the supported date range")
        if not 1 <= self.count <= 100_000:
            raise ValueError("search space must contain 1 through 100000 combinations")
        return self

    @property
    def count(self) -> int:
        routes = sum(a != b for a in self.origins for b in self.destinations)
        stays = 1 if self.min_nights is None else self.max_nights - self.min_nights + 1
        return routes * ((self.departure_end - self.departure_start).days + 1) * stays

    def searches(self) -> Iterator[SearchSpec]:
        """Generate combinations lazily in a reproducible order."""
        stays = [None] if self.min_nights is None else range(self.min_nights, self.max_nights + 1)
        days = range((self.departure_end - self.departure_start).days + 1)
        index = 0
        for origin, destination, day, nights in product(
            self.origins, self.destinations, days, stays
        ):
            if origin == destination:
                continue
            departure = self.departure_start + timedelta(days=day)
            yield SearchSpec.model_validate({
                **self.template.model_dump(),
                "request_id": str(index),
                "origin": origin,
                "destination": destination,
                "departure_date": departure,
                "return_date": None if nights is None else departure + timedelta(days=nights),
            })
            index += 1


class ExplorationProgress(BaseModel):
    run_id: str
    total: int
    attempted: int
    remaining: int
    failed: int
    search_space_exhausted: bool
    batch_network_requests: int
    summary: dict[str, Any]


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

    def advance(
        self, *, resume_from: str | None = None, search_budget: int = 20
    ) -> ExplorationProgress:
        if isinstance(search_budget, bool) or not isinstance(search_budget, int):
            raise ValueError("search_budget must be an integer")
        if not 1 <= search_budget <= 500:
            raise ValueError("search_budget must be between 1 and 500")
        outcomes = []
        if resume_from is not None:
            path, _ = self.store.resolve(resume_from)
            previous, _ = load_report(path)
            outcomes = previous.outcomes
            expected = [f"{self.identity}:{i}" for i in range(len(outcomes))]
            if len(outcomes) > self.space.count or [o.request_id for o in outcomes] != expected:
                raise ValueError("resume source does not match this search space and provider")
        selected = list(islice(
            self.space.searches(), len(outcomes), len(outcomes) + search_budget
        ))
        for spec in selected:
            spec.request_id = f"{self.identity}:{spec.request_id}"
        batch = self.executor.execute(selected)
        report = self.executor.summarize([*outcomes, *batch.outcomes])
        encoded = report.model_dump_json()
        run_id, path = self.store.save(encoded)
        summary = compact_summary(
            report, hashlib.sha256(encoded.encode()).hexdigest(), path, reference=run_id
        )
        attempted = len(report.outcomes)
        return ExplorationProgress(
            run_id=run_id,
            total=self.space.count,
            attempted=attempted,
            remaining=self.space.count - attempted,
            failed=report.counts.error,
            search_space_exhausted=attempted == self.space.count,
            batch_network_requests=batch.counts.network_requests,
            summary=summary,
        )
