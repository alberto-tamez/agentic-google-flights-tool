from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

SCHEMA_VERSION = "8"


class Cabin(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class MaxStops(StrEnum):
    ANY = "any"
    NON_STOP = "non_stop"
    ONE_STOP_OR_FEWER = "one_stop_or_fewer"
    TWO_OR_FEWER = "two_or_fewer"


class BaggageStatus(StrEnum):
    INCLUDED = "included"
    EXTRA_COST = "extra_cost"
    UNKNOWN = "unknown"


class SegmentFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airlines: list[str] = Field(default_factory=list)
    excluded_airlines: list[str] = Field(default_factory=list)
    earliest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    latest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    earliest_arrival_hour: int | None = Field(default=None, ge=0, le=23)
    latest_arrival_hour: int | None = Field(default=None, ge=0, le=23)
    max_duration_minutes: int | None = Field(default=None, ge=1)
    connecting_airports: list[str] = Field(default_factory=list)
    min_layover_minutes: int | None = Field(default=None, ge=0)
    max_layover_minutes: int | None = Field(default=None, ge=0)
    less_emissions_only: bool = False

    @field_validator("airlines", "excluded_airlines")
    @classmethod
    def validate_airlines(cls, values: list[str]) -> list[str]:
        normalized = [value.upper() for value in values]
        alliances = {"ONEWORLD", "SKYTEAM", "STAR_ALLIANCE"}
        if any(
            value not in alliances and not (len(value) == 2 and value.isascii() and value.isalnum())
            for value in normalized
        ):
            raise ValueError("airlines must contain IATA codes or alliance identifiers")
        return list(dict.fromkeys(normalized))

    @field_validator("connecting_airports")
    @classmethod
    def validate_connecting_airports(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(SearchSpec.validate_airport(value.strip()) for value in values))

    @model_validator(mode="after")
    def validate_windows(self) -> SegmentFilters:
        windows = (
            (self.earliest_departure_hour, self.latest_departure_hour, "departure"),
            (self.earliest_arrival_hour, self.latest_arrival_hour, "arrival"),
        )
        for earliest, latest, name in windows:
            if earliest is not None and latest is not None and earliest > latest:
                raise ValueError(f"{name} hour window cannot wrap past midnight")
        if (
            self.min_layover_minutes is not None
            and self.max_layover_minutes is not None
            and self.min_layover_minutes > self.max_layover_minutes
        ):
            raise ValueError("layover duration window must be ordered")
        return self


class SearchSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    origin: str
    destination: str
    departure_date: date
    filters: SegmentFilters = Field(default_factory=SegmentFilters)

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        value = value.upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise ValueError("airport must be a three-letter IATA code")
        return value

    @model_validator(mode="after")
    def validate_route(self) -> SearchSegment:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        return self


class SearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=128)
    origin: str
    destination: str
    departure_date: date
    return_date: date | None = None
    additional_segments: list[SearchSegment] = Field(default_factory=list)
    segment_filters: SegmentFilters = Field(default_factory=SegmentFilters)
    return_segment_filters: SegmentFilters = Field(default_factory=SegmentFilters)
    cabin: Cabin = Cabin.ECONOMY
    max_stops: MaxStops = MaxStops.ANY
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0)
    infants_in_seat: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)
    overhead_cabin_bags: int = Field(default=0, ge=0, le=9)
    checked_bags: int = Field(default=0, ge=0, le=9)
    require_overhead_cabin_bag: bool = False
    hide_separate_and_self_transfer: bool = False
    exclude_basic_economy: bool = False
    currency: str
    language: str = Field(min_length=2, max_length=35)
    country: str
    max_results: int | None = Field(default=None, ge=1)
    max_price: int | None = Field(default=None, ge=1)
    retrieval_limit: int | None = Field(default=None, ge=1)
    load_more_clicks: int | None = Field(default=None, ge=0)
    search_mode: Literal["discover", "verify"] = "verify"
    stage_candidate_offsets: list[int] = Field(default_factory=list)
    candidates_per_stage: int | None = Field(default=None, ge=1)
    max_complete_quotes: int | None = Field(default=None, ge=1)
    max_browser_transitions: int | None = Field(default=None, ge=1)

    # None means unbounded; finite limits schedule a resumable work chunk.
    continuation: dict[str, Any] | None = None
    preferred_outbound: dict[str, Any] | None = None

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        value = value.upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise ValueError("airport must be a three-letter IATA code")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise ValueError("currency must be a three-letter ISO 4217 code")
        return value

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        value = value.upper()
        if len(value) != 2 or not value.isalpha() or not value.isascii():
            raise ValueError("country must be a two-letter ISO 3166-1 code")
        return value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        parts = value.split("-")
        if not all(part.isascii() and part.isalnum() and 1 < len(part) < 9 for part in parts):
            raise ValueError("language must be a BCP 47 language tag")
        return value

    @model_validator(mode="after")
    def validate_route_and_dates(self) -> SearchSpec:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if self.return_date is not None and self.additional_segments:
            raise ValueError("return_date and additional_segments cannot be combined")
        previous_date = self.departure_date
        for segment in self.additional_segments:
            if segment.departure_date < previous_date:
                raise ValueError("additional segment dates must be chronological")
            previous_date = segment.departure_date
        if self.require_overhead_cabin_bag and self.overhead_cabin_bags < 1:
            raise ValueError(
                "overhead_cabin_bags must be at least 1 when require_overhead_cabin_bag is true"
            )
        passengers = self.adults + self.children + self.infants_in_seat + self.infants_on_lap
        if passengers > 9:
            raise ValueError("Google Flights supports at most 9 travelers in one search")
        if self.infants_on_lap > self.adults:
            raise ValueError("each lap infant requires an adult traveler")
        if self.search_mode == "discover" and self.require_overhead_cabin_bag:
            raise ValueError("discover mode cannot require verified overhead cabin baggage")
        if any(offset < 0 for offset in self.stage_candidate_offsets):
            raise ValueError("stage_candidate_offsets cannot contain negative values")
        return self

    def requested_segments(self) -> list[SearchSegment]:
        segments = [
            SearchSegment(
                origin=self.origin,
                destination=self.destination,
                departure_date=self.departure_date,
                filters=self.segment_filters,
            )
        ]
        if self.return_date is not None:
            segments.append(
                SearchSegment(
                    origin=self.destination,
                    destination=self.origin,
                    departure_date=self.return_date,
                    filters=self.return_segment_filters,
                )
            )
        return [*segments, *self.additional_segments]

    def criteria(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"request_id"})


class FlightLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_index: int = Field(ge=0)
    airline_code: str | None = None
    airline_name: str | None = None
    flight_number: str | None = None
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: PositiveInt


class FlightSegmentIdentity(BaseModel):
    """Provider-stable fields that identify a selected operating segment."""

    model_config = ConfigDict(extra="forbid")

    journey_index: int = Field(ge=0)
    origin: str
    destination: str
    departure_date: date
    airline_code: str | None = None
    flight_number: str | None = None


class BaggageAllowance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bag_type: Literal["overhead_cabin_bag"] = "overhead_cabin_bag"
    status: BaggageStatus
    source_text: str | None = None
    applies_to_journeys: list[int] = Field(default_factory=list)


class FlightOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_rank: PositiveInt
    price: float | None = Field(default=None, ge=0)
    currency: str
    duration_minutes: PositiveInt
    stops: int = Field(ge=0)
    emissions_grams: int | None = Field(default=None, ge=0)
    result_scope: Literal["complete_itinerary", "outbound_choice"] = "complete_itinerary"
    ticket_scope: Literal["complete_single_ticket", "partial_or_unknown"] = "partial_or_unknown"
    price_provenance: Literal["provider_search_result", "provider_final_total"] = (
        "provider_search_result"
    )
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    baggage: BaggageAllowance | None = None
    booking_provider: str | None = None
    fare_name: str | None = None
    source_url: str | None = None
    identity_segments: list[FlightSegmentIdentity] = Field(default_factory=list)
    legs: list[FlightLeg] = Field(min_length=1)


class SearchError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class SearchCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_candidates_loaded: int = Field(default=0, ge=0)
    source_duplicates: int = Field(default=0, ge=0)
    source_parse_failures: int = Field(default=0, ge=0)
    source_parse_failure_samples: list[str] = Field(default_factory=list, max_length=3)
    source_load_more_clicks: int = Field(default=0, ge=0)
    source_load_stop_reason: Literal[
        "not_applicable",
        "ui_exhausted",
        "click_budget",
        "retrieval_limit",
        "load_error",
        "transition_budget",
        "initial_page",
    ] = "not_applicable"
    source_truncated: bool = False
    candidates_seen: int = Field(default=0, ge=0)
    branches_attempted: int = Field(default=0, ge=0)
    quotes_completed: int = Field(default=0, ge=0)
    quotes_filtered: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    branches_pruned: int = Field(default=0, ge=0)
    branch_errors: int = Field(default=0, ge=0)
    branch_errors_by_code: dict[str, int] = Field(default_factory=dict)
    branch_error_samples: list[SearchError] = Field(default_factory=list, max_length=3)
    browser_transitions: int = Field(default=0, ge=0)
    budget_exhausted: bool = False
    fully_explored: bool = False
    last_source_truncated: bool = Field(default=False, exclude=True)
    retries: int = Field(default=0, ge=0)
    pending_branches: int = Field(default=0, ge=0)
    blocked: bool = False
    continuation: dict[str, Any] | None = None


class SearchOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    search_spec: SearchSpec | None = None
    status: Literal["success", "empty", "error"]
    options: list[FlightOption] = Field(default_factory=list)
    error: SearchError | None = None
    cached: bool = False
    elapsed_ms: int = Field(ge=0)
    requests_made: int = Field(ge=0)
    coverage: SearchCoverage = Field(default_factory=SearchCoverage)

    @model_validator(mode="after")
    def validate_status(self) -> SearchOutcome:
        legal = {
            "success": bool(self.options) and self.error is None,
            "empty": not self.options and self.error is None,
            "error": not self.options and self.error is not None,
        }
        if not legal[self.status]:
            raise ValueError(f"fields do not match status {self.status!r}")
        return self


class RankedFlight(BaseModel):
    request_id: str
    option: FlightOption


class BatchCounts(BaseModel):
    total: int = Field(ge=0)
    success: int = Field(ge=0)
    empty: int = Field(ge=0)
    error: int = Field(ge=0)
    unique_searches: int = Field(ge=0)
    network_requests: int = Field(ge=0)
    cache_hits: int = Field(ge=0)


class BatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exploration_state: dict[str, Any] | None = None
    outcomes: list[SearchOutcome]
    counts: BatchCounts
    ranked_by_currency: dict[str, list[RankedFlight]]
