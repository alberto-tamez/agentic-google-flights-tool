from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reverse_google_flights.models import BatchReport, FlightOption, RankedFlight


class SortKey(StrEnum):
    PRICE = "price"
    DURATION = "duration"
    STOPS = "stops"
    DEPARTURE = "departure"


class ShortlistSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_ids: list[str] = Field(default_factory=list, max_length=500)
    max_price: float | None = Field(default=None, ge=0)
    max_duration_minutes: int | None = Field(default=None, ge=1)
    max_total_stops: int | None = Field(default=None, ge=0, le=20)
    airlines: list[str] = Field(default_factory=list, max_length=50)
    earliest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    latest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    require_overhead_cabin_bag: bool = False
    ticket_scope: Literal["any", "complete_single_ticket"] = "any"
    sort_by: list[SortKey] = Field(
        default_factory=lambda: [SortKey.PRICE, SortKey.DURATION, SortKey.STOPS],
        min_length=1,
        max_length=4,
    )
    limit: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def validate_window(self) -> ShortlistSpec:
        if (
            self.earliest_departure_hour is not None
            and self.latest_departure_hour is not None
            and self.earliest_departure_hour > self.latest_departure_hour
        ):
            raise ValueError("departure hour window cannot wrap past midnight")
        return self


class ShortlistReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_counts: dict[str, int]
    offline_network_requests: Literal[0] = 0
    source_coverage: dict[str, dict]
    source_truncated: bool
    source_parse_failures: int
    source_parse_incomplete: bool
    source_fully_explored: bool
    evaluated: int
    accepted: int
    rejected_by: dict[str, int]
    unknown_by: dict[str, int]
    shortlist_count: int
    shortlist: list[RankedFlight]


@dataclass
class FilterCollection:
    matches: list[RankedFlight]
    source_coverage: dict[str, dict]
    source_truncated: bool
    source_parse_failures: int
    source_fully_explored: bool
    evaluated: int
    rejected_by: dict[str, int]
    unknown_by: dict[str, int]


def collect_matches(source: BatchReport, filters: ShortlistSpec) -> FilterCollection:
    rejected: dict[str, int] = {}
    unknown: dict[str, int] = {}
    accepted: list[RankedFlight] = []
    evaluated = 0
    airline_filters = {value.casefold() for value in filters.airlines}
    request_filter = set(filters.request_ids)
    selected_outcomes = [
        outcome
        for outcome in source.outcomes
        if not request_filter or outcome.request_id in request_filter
    ]

    for outcome in selected_outcomes:
        for option in outcome.options:
            evaluated += 1
            failures, missing = _evaluate(option, filters, airline_filters)
            for name in failures:
                rejected[name] = rejected.get(name, 0) + 1
            for name in missing:
                unknown[name] = unknown.get(name, 0) + 1
            if not failures and not missing:
                accepted.append(RankedFlight(request_id=outcome.request_id, option=option))

    accepted.sort(key=lambda item: _sort_key(item, filters.sort_by))
    coverage = {
        outcome.request_id: outcome.coverage.model_dump(mode="json")
        for outcome in selected_outcomes
    }
    source_truncated = any(outcome.coverage.source_truncated for outcome in selected_outcomes)
    source_parse_failures = sum(
        outcome.coverage.source_parse_failures for outcome in selected_outcomes
    )
    source_fully_explored = all(
        outcome.coverage.fully_explored for outcome in selected_outcomes
    )
    return FilterCollection(
        matches=accepted,
        source_coverage=coverage,
        source_truncated=source_truncated,
        source_parse_failures=source_parse_failures,
        source_fully_explored=source_fully_explored,
        evaluated=evaluated,
        rejected_by=dict(sorted(rejected.items())),
        unknown_by=dict(sorted(unknown.items())),
    )


def build_shortlist(source: BatchReport, filters: ShortlistSpec) -> ShortlistReport:
    collection = collect_matches(source, filters)
    shortlist = collection.matches[: filters.limit]
    return ShortlistReport(
        source_counts=source.counts.model_dump(),
        source_coverage=collection.source_coverage,
        source_truncated=collection.source_truncated,
        source_parse_failures=collection.source_parse_failures,
        source_parse_incomplete=collection.source_parse_failures > 0,
        source_fully_explored=collection.source_fully_explored,
        evaluated=collection.evaluated,
        accepted=len(collection.matches),
        rejected_by=collection.rejected_by,
        unknown_by=collection.unknown_by,
        shortlist_count=len(shortlist),
        shortlist=shortlist,
    )


def _evaluate(
    option: FlightOption,
    filters: ShortlistSpec,
    airline_filters: set[str],
) -> tuple[set[str], set[str]]:
    failures: set[str] = set()
    unknown: set[str] = set()
    if filters.max_price is not None:
        if option.price is None:
            unknown.add("max_price")
        elif option.price > filters.max_price:
            failures.add("max_price")
    if filters.max_duration_minutes is not None and (
        option.duration_minutes > filters.max_duration_minutes
    ):
        failures.add("max_duration_minutes")
    if filters.max_total_stops is not None and option.stops > filters.max_total_stops:
        failures.add("max_total_stops")
    if airline_filters:
        carriers = [
            {value.casefold() for value in (leg.airline_code, leg.airline_name) if value}
            for leg in option.legs
        ]
        if any(not carrier for carrier in carriers):
            unknown.add("airlines")
        elif any(not (carrier & airline_filters) for carrier in carriers):
            failures.add("airlines")
    for name, boundary, comparison in (
        ("earliest_departure_hour", filters.earliest_departure_hour, "minimum"),
        ("latest_departure_hour", filters.latest_departure_hour, "maximum"),
    ):
        if boundary is None:
            continue
        hours = [leg.departure_at.hour for leg in option.legs]
        if comparison == "minimum" and any(hour < boundary for hour in hours):
            failures.add(name)
        if comparison == "maximum" and any(hour > boundary for hour in hours):
            failures.add(name)
    if filters.require_overhead_cabin_bag:
        baggage = option.baggage
        journey_count = len({leg.journey_index for leg in option.legs})
        if baggage is None or baggage.status == "unknown":
            unknown.add("overhead_cabin_bag")
        elif baggage.status != "included" or baggage.applies_to_journeys != list(
            range(journey_count)
        ):
            failures.add("overhead_cabin_bag")
    if filters.ticket_scope == "complete_single_ticket" and (
        option.ticket_scope != "complete_single_ticket"
    ):
        failures.add("ticket_scope")
    return failures, unknown


def _sort_key(item: RankedFlight, sort_by: list[SortKey]) -> tuple:
    option = item.option
    values = {
        SortKey.PRICE: (option.price is None, option.price or 0),
        SortKey.DURATION: option.duration_minutes,
        SortKey.STOPS: option.stops,
        SortKey.DEPARTURE: option.legs[0].departure_at.isoformat(),
    }
    return (*[values[key] for key in sort_by], item.request_id, option.provider_rank)
