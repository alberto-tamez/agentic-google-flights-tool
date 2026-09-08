"""Encode Google Flights ``tfs`` queries without an external flight library.

Derived from fast-flights 3.1.0 querying and protobuf definitions, MIT licensed.
Copyright (c) 2024-PRESENT fast-flights Contributors. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("protobuf integers must be non-negative")
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _scalar(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _text(field: int, value: str) -> bytes:
    return _bytes(field, value.encode())


@dataclass(frozen=True)
class Airport:
    airport: str

    def encode(self) -> bytes:
        return _text(2, self.airport)


@dataclass
class FlightQuery:
    date: str
    from_airport: Airport | str
    to_airport: Airport | str
    max_stops: int | None = None
    airlines: list[str] | None = None
    earliest_departure_hour: int | None = None
    latest_departure_hour: int | None = None
    earliest_arrival_hour: int | None = None
    latest_arrival_hour: int | None = None
    max_duration_minutes: int | None = None
    connecting_airports: list[str] | None = None
    min_layover_minutes: int | None = None
    max_layover_minutes: int | None = None
    less_emissions_only: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.from_airport, str):
            self.from_airport = Airport(self.from_airport)
        if isinstance(self.to_airport, str):
            self.to_airport = Airport(self.to_airport)

    def encode(self) -> bytes:
        result = _text(2, self.date)
        if self.max_stops is not None:
            result += _scalar(5, self.max_stops)
        for airline in self.airlines or []:
            result += _text(6, airline)
        for field, value in (
            (8, self.earliest_departure_hour),
            (9, self.latest_departure_hour),
            (10, self.earliest_arrival_hour),
            (11, self.latest_arrival_hour),
            (12, self.max_duration_minutes),
        ):
            if value is not None:
                result += _scalar(field, value)
        result += _bytes(13, self.from_airport.encode())
        result += _bytes(14, self.to_airport.encode())
        for airport in self.connecting_airports or []:
            result += _text(15, airport)
        if self.min_layover_minutes is not None:
            result += _scalar(17, self.min_layover_minutes)
        if self.max_layover_minutes is not None:
            result += _scalar(18, self.max_layover_minutes)
        if self.less_emissions_only:
            result += _bytes(19, _varint(1))
        return result

    def ListFields(self) -> list[tuple[object, object]]:
        """Small compatibility view used by callers that inspect optional stop presence."""
        fields = []
        for name in (
            "date",
            "max_stops",
            "airlines",
            "earliest_departure_hour",
            "latest_departure_hour",
            "earliest_arrival_hour",
            "latest_arrival_hour",
            "max_duration_minutes",
            "from_airport",
            "to_airport",
            "connecting_airports",
            "min_layover_minutes",
            "max_layover_minutes",
        ):
            value = getattr(self, name)
            if value is not None and value != []:
                fields.append((type("Field", (), {"name": name})(), value))
        return fields


@dataclass(frozen=True)
class Passengers:
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0

    def values(self) -> list[int]:
        return [
            *([1] * self.adults),
            *([2] * self.children),
            *([3] * self.infants_in_seat),
            *([4] * self.infants_on_lap),
        ]


@dataclass
class Query:
    flight_data: list[FlightQuery]
    seat: int
    trip: int
    passengers: list[int]
    language: str
    currency: str
    max_price: int | None = None
    carry_on_bags: int = 0
    checked_bags: int = 0
    hide_separate_and_self_transfer: bool = False
    exclude_basic_economy: bool = False

    def to_bytes(self) -> bytes:
        result = b"".join(_bytes(3, flight.encode()) for flight in self.flight_data)
        if self.passengers:
            result += _bytes(8, b"".join(_varint(value) for value in self.passengers))
        result += _scalar(9, self.seat)
        if self.max_price is not None:
            result += _scalar(12, self.max_price)
        if self.carry_on_bags or self.checked_bags:
            baggage = b""
            if self.carry_on_bags:
                baggage += _scalar(2, self.carry_on_bags)
            if self.checked_bags:
                baggage += _scalar(3, self.checked_bags)
            result += _bytes(13, baggage)
        if self.hide_separate_and_self_transfer:
            result += _scalar(17, 1)
        result += _scalar(19, self.trip)
        if self.exclude_basic_economy:
            result += _scalar(25, 1)
        return result

    def to_str(self) -> str:
        return b64encode(self.to_bytes()).decode()

    def url(self) -> str:
        return (
            f"https://www.google.com/travel/flights/search?tfs={self.to_str()}"
            f"&hl={self.language}&curr={self.currency}"
        )


def create_query(
    *,
    flights: list[FlightQuery],
    seat: str = "economy",
    trip: str = "one-way",
    passengers: Passengers | None = None,
    language: str = "",
    currency: str = "",
    max_price: int | None = None,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    hide_separate_and_self_transfer: bool = False,
    exclude_basic_economy: bool = False,
) -> Query:
    return Query(
        flight_data=flights,
        seat={"economy": 1, "premium-economy": 2, "business": 3, "first": 4}[seat],
        trip={"round-trip": 1, "one-way": 2, "multi-city": 3}[trip],
        passengers=(passengers or Passengers()).values(),
        language=language,
        currency=currency,
        max_price=max_price,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
    )
