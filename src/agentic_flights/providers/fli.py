"""Optional flights-library provider and response normalization."""

from __future__ import annotations

import json
from typing import Any

from agentic_flights.models import (
    FlightLeg,
    FlightOption,
    SearchSpec,
)
from agentic_flights.providers.base import (
    ProviderError,
    ProviderResult,
)


class _CapturingClient:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.requests_made = 0
        self.responses: list[str] = []

    def post(self, *args: Any, **kwargs: Any) -> Any:
        self.requests_made += 1
        response = self.client.post(*args, **kwargs)
        self.responses.append(str(getattr(response, "text", "")))
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


class FliProvider:
    def search(self, spec: SearchSpec) -> ProviderResult:
        if spec.additional_segments:
            raise ProviderError(
                "provider_unsupported",
                "The fli provider cannot search additional_segments; use the browser provider.",
            )
        try:
            from fli.models import (
                Airport,
                FlightSearchFilters,
                FlightSegment,
                MaxStops,
                PassengerInfo,
                SeatType,
                SortBy,
                TripType,
            )
            from fli.search import SearchFlights
        except ImportError as exc:
            raise ProviderError(
                "provider_unavailable",
                "The flights dependency is missing. Install agentic-flights[fli].",
            ) from exc

        try:
            segments = [
                FlightSegment(
                    departure_airport=[[Airport[spec.origin], 0]],
                    arrival_airport=[[Airport[spec.destination], 0]],
                    travel_date=spec.departure_date.isoformat(),
                )
            ]
            trip_type = TripType.ONE_WAY
            if spec.return_date is not None:
                trip_type = TripType.ROUND_TRIP
                segments.append(
                    FlightSegment(
                        departure_airport=[[Airport[spec.destination], 0]],
                        arrival_airport=[[Airport[spec.origin], 0]],
                        travel_date=spec.return_date.isoformat(),
                    )
                )
            filters = FlightSearchFilters(
                trip_type=trip_type,
                passenger_info=PassengerInfo(adults=spec.adults),
                flight_segments=segments,
                seat_type=SeatType[spec.cabin.name],
                stops={
                    "any": MaxStops.ANY,
                    "non_stop": MaxStops.NON_STOP,
                    "one_stop_or_fewer": MaxStops.ONE_STOP_OR_FEWER,
                    "two_or_fewer": MaxStops.TWO_OR_FEWER_STOPS,
                }[spec.max_stops.value],
                sort_by=SortBy.CHEAPEST,
            )
            search = SearchFlights()
            client = _CapturingClient(search.client)
            search.client = client
            raw_results = search.search(
                filters,
                top_n=1,
                currency=spec.currency,
                language=spec.language,
                country=spec.country,
            )
            if raw_results is None:
                if not client.responses or not _has_wrb_payload(client.responses[-1]):
                    raise ProviderError(
                        "provider_response_error",
                        "Google Flights returned a null or unreadable response envelope.",
                        retryable=True,
                        requests_made=client.requests_made,
                    )
                return ProviderResult("empty", [], client.requests_made)
            options = [
                _normalize_option(item, rank, spec.currency)
                for rank, item in enumerate(raw_results[: spec.max_results], start=1)
            ]
            status = "success" if options else "empty"
            return ProviderResult(status, options, client.requests_made)
        except ProviderError:
            raise
        except Exception as exc:
            count = client.requests_made if "client" in locals() else 0
            raise ProviderError(
                "provider_error",
                str(exc) or type(exc).__name__,
                retryable=True,
                details={"exception_type": type(exc).__name__},
                requests_made=count,
            ) from exc


def _has_wrb_payload(body: str) -> bool:
    raw = body.lstrip()
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip()
    candidates = [raw]
    lines = raw.splitlines()
    candidates.extend(line for line in lines if line.lstrip().startswith("["))
    for candidate in candidates:
        try:
            outer = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(outer, list):
            continue
        for row in outer:
            if (
                isinstance(row, list)
                and len(row) > 2
                and row[0] == "wrb.fr"
                and isinstance(row[2], str)
                and bool(row[2])
            ):
                try:
                    json.loads(row[2])
                except ValueError:
                    continue
                return True
    return False


def _enum_code(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.removeprefix("_")
    return str(value)


def _normalize_option(raw: Any, rank: int, fallback_currency: str) -> FlightOption:
    journeys = list(raw) if isinstance(raw, tuple) else [raw]
    legs: list[FlightLeg] = []
    for journey_index, journey in enumerate(journeys):
        for leg in journey.legs:
            airline_name = getattr(leg.airline, "value", None)
            if not isinstance(airline_name, str):
                airline_name = None
            legs.append(
                FlightLeg(
                    journey_index=journey_index,
                    airline_code=_enum_code(leg.airline),
                    airline_name=airline_name,
                    flight_number=str(leg.flight_number),
                    origin=_enum_code(leg.departure_airport),
                    destination=_enum_code(leg.arrival_airport),
                    departure_at=leg.departure_datetime,
                    arrival_at=leg.arrival_datetime,
                    duration_minutes=leg.duration,
                )
            )
    priced = [journey for journey in journeys if journey.price is not None]
    price_source = priced[-1] if priced else None
    emissions = [journey.co2_emissions_g for journey in journeys if journey.co2_emissions_g]
    return FlightOption(
        provider_rank=rank,
        price=float(price_source.price) if price_source else None,
        currency=(price_source.currency if price_source else None) or fallback_currency,
        duration_minutes=sum(journey.duration for journey in journeys),
        stops=sum(journey.stops for journey in journeys),
        emissions_grams=sum(emissions) if emissions else None,
        legs=legs,
    )
