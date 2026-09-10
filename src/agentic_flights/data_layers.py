"""Provider-neutral records for the flight-search data layers.

These models intentionally contain no fetching or provider-specific behavior.  A
record is useful only together with the provenance of the source response that
produced it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


def _iata(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError("airport must be a three-letter IATA code")
    return value


def _currency(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return value


def _country(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 2 or not value.isascii() or not value.isalpha():
        raise ValueError("country must be a two-letter ISO 3166-1 code")
    return value


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


class DataRecord(BaseModel):
    """Strict base for data-layer records."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProvenanceEnvelope(DataRecord):
    """Source, freshness, licensing, coverage, and ingestion accounting."""

    source_id: str = Field(min_length=1)
    canonical_url: AnyHttpUrl
    upstream_version: str | None = Field(default=None, min_length=1)
    snapshot_id: str | None = Field(default=None, min_length=1)
    schema_version: str = Field(min_length=1)
    retrieved_at: datetime
    source_observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    response_checksum: str = Field(min_length=1)
    cache_state: Literal["miss", "hit", "revalidated", "bypass", "unknown"]
    cache_age_seconds: float | None = Field(default=None, ge=0)
    cache_stale: bool
    license_status: Literal["permitted", "restricted", "unknown", "not_applicable"]
    records_received: int = Field(ge=0)
    records_accepted: int = Field(ge=0)
    records_rejected: int = Field(ge=0)
    coverage_limitations: tuple[str, ...] = ()
    evidence_level: Literal["primary", "derived", "inferred", "unknown"]

    @field_validator("retrieved_at", "source_observed_at", "valid_from", "valid_to")
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        if value is not None:
            return _aware(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_envelope(self) -> ProvenanceEnvelope:
        if self.upstream_version is None and self.snapshot_id is None:
            raise ValueError("upstream_version or snapshot_id is required")
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must be on or after valid_from")
        if self.source_observed_at is not None and self.source_observed_at > self.retrieved_at:
            raise ValueError("source_observed_at cannot follow retrieved_at")
        if self.records_accepted + self.records_rejected != self.records_received:
            raise ValueError("accepted and rejected counts must equal received count")
        return self


class AirportReference(DataRecord):
    provenance: ProvenanceEnvelope
    airport_iata: str
    name: str = Field(min_length=1)
    country_code: str
    timezone_id: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    served_locality: str | None = None
    airport_type: str | None = None

    @field_validator("airport_iata")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        return _iata(value)

    @field_validator("country_code")
    @classmethod
    def validate_country(cls, value: str) -> str:
        return _country(value)


class GroundScheduledEvent(DataRecord):
    provenance: ProvenanceEnvelope
    event_id: str = Field(min_length=1)
    origin_location_id: str = Field(min_length=1)
    destination_location_id: str = Field(min_length=1)
    departure_at: datetime
    arrival_at: datetime
    mode: Literal["rail", "bus", "ferry", "airport_transfer", "other"]
    origin_airport_iata: str | None = None
    destination_airport_iata: str | None = None
    operator: str | None = None
    service_number: str | None = None
    scheduled_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    booking_policy: str | None = None

    @field_validator("origin_airport_iata", "destination_airport_iata")
    @classmethod
    def validate_airports(cls, value: str | None) -> str | None:
        return _iata(value) if value is not None else None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return _currency(value) if value is not None else None

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def validate_datetimes(cls, value: datetime, info) -> datetime:
        return _aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_event(self) -> GroundScheduledEvent:
        if self.origin_location_id == self.destination_location_id:
            raise ValueError("ground event origin and destination must differ")
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must follow departure_at")
        if (self.scheduled_cost is None) != (self.currency is None):
            raise ValueError("scheduled_cost and currency must be supplied together")
        return self


class RouteTopologyRecord(DataRecord):
    provenance: ProvenanceEnvelope
    route_id: str = Field(min_length=1)
    origin_iata: str
    destination_iata: str
    operating_carrier: str | None = None
    service_type: Literal["scheduled", "seasonal", "charter", "unknown"] = "unknown"
    nonstop: bool = True
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("origin_iata", "destination_iata")
    @classmethod
    def validate_airports(cls, value: str) -> str:
        return _iata(value)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        return _aware(value, info.field_name) if value is not None else None

    @model_validator(mode="after")
    def validate_route(self) -> RouteTopologyRecord:
        if self.origin_iata == self.destination_iata:
            raise ValueError("route origin and destination must differ")
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must be on or after valid_from")
        return self


class FlightScheduledEvent(DataRecord):
    provenance: ProvenanceEnvelope
    event_id: str = Field(min_length=1)
    origin_iata: str
    destination_iata: str
    departure_at: datetime
    arrival_at: datetime
    marketing_carrier: str | None = None
    operating_carrier: str | None = None
    flight_number: str | None = None
    equipment: str | None = None
    departure_terminal: str | None = None
    arrival_terminal: str | None = None

    @field_validator("origin_iata", "destination_iata")
    @classmethod
    def validate_airports(cls, value: str) -> str:
        return _iata(value)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def validate_datetimes(cls, value: datetime, info) -> datetime:
        return _aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_event(self) -> FlightScheduledEvent:
        if self.origin_iata == self.destination_iata:
            raise ValueError("flight origin and destination must differ")
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must follow departure_at")
        return self


class OperationalObservation(DataRecord):
    provenance: ProvenanceEnvelope
    event_id: str = Field(min_length=1)
    observed_at: datetime
    status: Literal["scheduled", "delayed", "cancelled", "departed", "arrived", "unknown"]
    estimated_departure_at: datetime | None = None
    estimated_arrival_at: datetime | None = None
    actual_departure_at: datetime | None = None
    actual_arrival_at: datetime | None = None
    departure_gate: str | None = None
    arrival_gate: str | None = None
    delay_minutes: int | None = Field(default=None, ge=0)

    @field_validator(
        "observed_at",
        "estimated_departure_at",
        "estimated_arrival_at",
        "actual_departure_at",
        "actual_arrival_at",
    )
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        return _aware(value, info.field_name) if value is not None else None

    @model_validator(mode="after")
    def validate_observation(self) -> OperationalObservation:
        pairs = (
            (self.estimated_departure_at, self.estimated_arrival_at, "estimated"),
            (self.actual_departure_at, self.actual_arrival_at, "actual"),
        )
        for departure, arrival, label in pairs:
            if departure is not None and arrival is not None and arrival <= departure:
                raise ValueError(f"{label} arrival must follow departure")
        return self


class PassengerComposition(DataRecord):
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants_in_seat: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)


class BagSelection(DataRecord):
    personal_items: int = Field(default=0, ge=0)
    cabin_bags: int = Field(default=0, ge=0)
    checked_bags: int = Field(default=0, ge=0)


class WholeItineraryOfferKey(DataRecord):
    """Dimensions that must match before two offer observations are comparable."""

    itinerary_event_ids: tuple[str, ...] = Field(min_length=1)
    passengers: PassengerComposition
    cabin: Literal["economy", "premium_economy", "business", "first"]
    fare_product: str | None = None
    bags: BagSelection
    seller: str = Field(min_length=1)
    currency: str
    country: str
    point_of_sale: str = Field(min_length=1)
    priced_at: datetime
    expires_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _currency(value)

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        return _country(value)

    @field_validator("priced_at", "expires_at")
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        return _aware(value, info.field_name) if value is not None else None

    @model_validator(mode="after")
    def validate_key(self) -> WholeItineraryOfferKey:
        if len(set(self.itinerary_event_ids)) != len(self.itinerary_event_ids):
            raise ValueError("itinerary_event_ids must not contain duplicates")
        if self.expires_at is not None and self.expires_at <= self.priced_at:
            raise ValueError("expires_at must follow priced_at")
        return self


class WholeItineraryOfferRecord(DataRecord):
    provenance: ProvenanceEnvelope
    offer_id: str = Field(min_length=1)
    key: WholeItineraryOfferKey
    result_scope: Literal["whole_itinerary"] = "whole_itinerary"
    ticket_scope: Literal["complete_single_ticket", "separate_tickets"]
    whole_itinerary_matched: Literal[True] = True
    total_cost: Decimal | None = Field(default=None, ge=0)
    taxes_and_fees: Decimal | None = Field(default=None, ge=0)
    baggage_cost: Decimal | None = Field(default=None, ge=0)
    booking_url: AnyHttpUrl | None = None
    seats_remaining: int | None = Field(default=None, ge=0)
    baggage_policy: str | None = None
    change_policy: str | None = None
    cancellation_policy: str | None = None


class RulesRiskEvidence(DataRecord):
    provenance: ProvenanceEnvelope
    evidence_id: str = Field(min_length=1)
    subject_type: Literal["airport", "route", "event", "itinerary", "offer"]
    subject_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    risk_level: Literal["none", "low", "medium", "high", "unknown"] = "unknown"
    rule_text: str | None = None
    policy_text: str | None = None
    potential_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return _currency(value) if value is not None else None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        return _aware(value, info.field_name) if value is not None else None

    @model_validator(mode="after")
    def validate_evidence(self) -> RulesRiskEvidence:
        if (self.potential_cost is None) != (self.currency is None):
            raise ValueError("potential_cost and currency must be supplied together")
        if self.effective_from is not None and self.effective_to is not None:
            if self.effective_to < self.effective_from:
                raise ValueError("effective_to must be on or after effective_from")
        return self
