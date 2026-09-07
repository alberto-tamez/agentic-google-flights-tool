from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from reverse_google_flights.models import FlightLeg, FlightOption, SearchSpec


@pytest.fixture
def future_date() -> date:
    return date.today() + timedelta(days=60)


def make_spec(request_id: str, departure_date: date, **updates: object) -> SearchSpec:
    values: dict[str, object] = {
        "request_id": request_id,
        "origin": "MAD",
        "destination": "BCN",
        "departure_date": departure_date,
        "cabin": "economy",
        "max_stops": "non_stop",
        "adults": 1,
        "currency": "EUR",
        "language": "es-ES",
        "country": "ES",
        "max_results": 5,
    }
    values.update(updates)
    return SearchSpec.model_validate(values)


def make_option(
    price: float | None = 100.0,
    *,
    currency: str = "EUR",
    duration: int = 75,
    stops: int = 0,
    rank: int = 1,
) -> FlightOption:
    departure = datetime(2026, 11, 10, 8, tzinfo=UTC)
    return FlightOption(
        provider_rank=rank,
        price=price,
        currency=currency,
        duration_minutes=duration,
        stops=stops,
        legs=[
            FlightLeg(
                journey_index=0,
                airline_code="IB",
                airline_name="Iberia",
                flight_number="3010",
                origin="MAD",
                destination="BCN",
                departure_at=departure,
                arrival_at=departure + timedelta(minutes=duration),
                duration_minutes=duration,
            )
        ],
    )

