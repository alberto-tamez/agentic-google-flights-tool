from __future__ import annotations

import pytest
from conftest import make_option, make_spec
from pydantic import ValidationError

from agentic_flights.models import SearchError, SearchOutcome


def test_search_spec_normalizes_codes_and_rejects_invalid_route(future_date) -> None:
    spec = make_spec("normal", future_date, origin="mad", currency="eur", country="es")
    assert (spec.origin, spec.currency, spec.country) == ("MAD", "EUR", "ES")

    with pytest.raises(ValidationError, match="origin and destination must differ"):
        make_spec("bad", future_date, destination="mad")


def test_open_jaw_segments_preserve_order_and_dates(future_date) -> None:
    spec = make_spec(
        "open-jaw",
        future_date,
        destination="CDG",
        additional_segments=[
            {
                "origin": "AMS",
                "destination": "MAD",
                "departure_date": future_date.isoformat(),
            }
        ],
    )
    assert [(segment.origin, segment.destination) for segment in spec.requested_segments()] == [
        ("MAD", "CDG"),
        ("AMS", "MAD"),
    ]


def test_segment_filters_reject_unknown_airline_tokens_and_wrapped_hours(future_date) -> None:
    with pytest.raises(ValidationError, match="IATA codes or alliance"):
        make_spec("bad-airline", future_date, segment_filters={"airlines": ["IBERIA"]})
    with pytest.raises(ValidationError, match="cannot wrap past midnight"):
        make_spec(
            "wrapped",
            future_date,
            segment_filters={"earliest_departure_hour": 22, "latest_departure_hour": 6},
        )


def test_discovery_cannot_silently_defer_a_hard_baggage_requirement(future_date) -> None:
    with pytest.raises(ValidationError, match="discover mode cannot require"):
        make_spec(
            "unsafe-discovery",
            future_date,
            search_mode="discover",
            overhead_cabin_bags=1,
            require_overhead_cabin_bag=True,
        )

    with pytest.raises(ValidationError, match="overhead_cabin_bags must be at least 1"):
        make_spec("bag", future_date, require_overhead_cabin_bag=True)


def test_passenger_and_layover_relationships_are_validated(future_date) -> None:
    family = make_spec(
        "family",
        future_date,
        adults=2,
        children=2,
        infants_in_seat=1,
        infants_on_lap=1,
        checked_bags=2,
    )
    assert family.adults + family.children + family.infants_in_seat + family.infants_on_lap == 6

    with pytest.raises(ValidationError, match="9 travelers"):
        make_spec("crowd", future_date, adults=8, children=2)
    with pytest.raises(ValidationError, match="lap infant"):
        make_spec("infants", future_date, adults=1, infants_on_lap=2)
    with pytest.raises(ValidationError, match="layover duration window"):
        make_spec(
            "layover",
            future_date,
            segment_filters={"min_layover_minutes": 180, "max_layover_minutes": 60},
        )


@pytest.mark.parametrize(
    ("status", "options", "error"),
    [
        ("success", [], None),
        ("empty", [make_option()], None),
        ("error", [], None),
        ("success", [make_option()], SearchError(code="x", message="x")),
    ],
)
def test_search_outcome_rejects_illegal_states(status, options, error) -> None:
    with pytest.raises(ValidationError, match="fields do not match status"):
        SearchOutcome(
            request_id="x",
            status=status,
            options=options,
            error=error,
            elapsed_ms=0,
            requests_made=0,
        )
