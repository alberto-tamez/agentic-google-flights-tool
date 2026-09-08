from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fast_flights import FlightQuery, Passengers, create_query
from hypothesis import given
from hypothesis import strategies as st

from agentic_flights import AgentAPI, BatchExecutor, SearchSpace
from agentic_flights.cache import FileCache
from agentic_flights.cli import run
from agentic_flights.filtering import ShortlistSpec, collect_matches
from agentic_flights.mcp_server import _handler
from agentic_flights.models import (
    BaggageAllowance,
    BatchCounts,
    BatchReport,
    FlightLeg,
    FlightOption,
    SearchCoverage,
    SearchOutcome,
    SearchSpec,
)
from agentic_flights.provider import (
    ProviderResult,
    _build_browser_query,
    _parse_browser_label,
)
from agentic_flights.skill_init import install_skill
from agentic_flights.store import ManagedStore

AIRPORTS = (
    "AMS",
    "BCN",
    "CDG",
    "FCO",
    "FRA",
    "HND",
    "JFK",
    "KIX",
    "LHR",
    "LIS",
    "MAD",
    "NRT",
    "SIN",
    "SYD",
)
CURRENCIES = ("EUR", "GBP", "JPY", "USD")


@st.composite
def routes(draw):
    origin = draw(st.sampled_from(AIRPORTS))
    destination = draw(st.sampled_from([code for code in AIRPORTS if code != origin]))
    return origin, destination


def specs(*, route=None, departure=None, **updates) -> SearchSpec:
    origin, destination = route or ("MAD", "NRT")
    values = {
        "request_id": "randomized",
        "origin": origin,
        "destination": destination,
        "departure_date": departure or date(2027, 5, 12),
        "currency": "EUR",
        "language": "en-US",
        "country": "ES",
    }
    values.update(updates)
    return SearchSpec.model_validate(values)


@pytest.mark.feature("trip-planning")
@given(
    route=routes(),
    extra_origins=st.sets(st.sampled_from(AIRPORTS), max_size=2),
    extra_destinations=st.sets(st.sampled_from(AIRPORTS), max_size=2),
    start=st.dates(min_value=date(2027, 1, 1), max_value=date(2029, 12, 20)),
    days=st.integers(min_value=0, max_value=4),
    nights=st.one_of(st.none(), st.tuples(st.integers(0, 14), st.integers(0, 14))),
    adults=st.integers(1, 9),
)
def test_trip_space_visits_every_requested_choice_once(
    route, extra_origins, extra_destinations, start, days, nights, adults
):
    minimum, maximum = (None, None) if nights is None else sorted(nights)
    origins = sorted({route[0], *extra_origins})
    destinations = sorted({route[1], *extra_destinations})
    space = SearchSpace(
        template=specs(route=route, departure=start, adults=adults, search_mode="discover"),
        origins=origins,
        destinations=destinations,
        departure_start=start,
        departure_end=start + timedelta(days=days),
        min_nights=minimum,
        max_nights=maximum,
    )
    searches = list(space.searches())
    unique = {item.model_dump_json(exclude={"request_id"}) for item in searches}
    assert len(searches) == space.count == len(unique)
    assert {item.departure_date for item in searches} == {
        start + timedelta(days=offset) for offset in range(days + 1)
    }
    assert all(item.adults == adults for item in searches)
    routes_count = sum(origin != destination for origin in origins for destination in destinations)
    first_stay = searches[: routes_count * (days + 1)]
    assert {
        (item.origin, item.destination, item.departure_date) for item in first_stay
    } == {
        (origin, destination, start + timedelta(days=offset))
        for origin in origins
        for destination in destinations
        if origin != destination
        for offset in range(days + 1)
    }


@pytest.mark.feature("query-creation")
@given(
    route=routes(),
    departure=st.dates(min_value=date(2027, 1, 1), max_value=date(2029, 12, 1)),
    stay=st.integers(0, 30),
    adults=st.integers(1, 9),
    cabin=st.sampled_from(("economy", "premium_economy", "business", "first")),
    stops=st.sampled_from(("any", "non_stop", "one_stop_or_fewer", "two_or_fewer")),
    currency=st.sampled_from(CURRENCIES),
)
def test_google_query_preserves_trip_intent(route, departure, stay, adults, cabin, stops, currency):
    spec = specs(
        route=route,
        departure=departure,
        return_date=departure + timedelta(days=stay),
        adults=adults,
        cabin=cabin,
        max_stops=stops,
        currency=currency,
    )
    query, _ = _build_browser_query(spec, FlightQuery, Passengers, create_query)
    outbound, returning = query.flight_data
    assert (outbound.from_airport.airport, outbound.to_airport.airport) == route
    assert (returning.from_airport.airport, returning.to_airport.airport) == route[::-1]
    assert query.passengers == [1] * adults
    assert query.currency == currency
    assert query.seat == {
        "economy": 1,
        "premium_economy": 2,
        "business": 3,
        "first": 4,
    }[cabin]
    expected_stops = {
        "any": 0,
        "non_stop": 0,
        "one_stop_or_fewer": 1,
        "two_or_fewer": 2,
    }[stops]
    assert outbound.max_stops == returning.max_stops == expected_stops
    has_stop_filter = any(field.name == "max_stops" for field, _ in outbound.ListFields())
    assert has_stop_filter is (stops != "any")
    assert (outbound.date, returning.date) == (
        departure.isoformat(),
        (departure + timedelta(days=stay)).isoformat(),
    )


@pytest.mark.feature("browser-parsing")
@given(
    route=routes(),
    departure=st.dates(min_value=date(2027, 1, 1), max_value=date(2029, 12, 1)),
    hour=st.integers(0, 18),
    minute=st.sampled_from((0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)),
    duration=st.integers(20, 300),
    stops=st.integers(0, 2),
    price=st.integers(1, 9999),
)
def test_browser_label_parses_fare_and_itinerary(
    route, departure, hour, minute, duration, stops, price
):
    leaves = datetime.combine(departure, datetime.min.time()).replace(hour=hour, minute=minute)
    arrives = leaves + timedelta(minutes=duration)
    stop_text = "Nonstop" if stops == 0 else f"{stops} stop"
    duration_text = f"{duration // 60} hr {duration % 60} min"
    label = (
        f"From {price:,} euros one way. {stop_text} flight with Test Air. "
        f"Leaves Origin Airport at {leaves.strftime('%-I:%M %p')} on "
        f"{leaves.strftime('%A, %B %-d')} "
        f"and arrives at Destination Airport at {arrives.strftime('%-I:%M %p')} on "
        f"{arrives.strftime('%A, %B %-d')}. Total duration {duration_text}. Select flight"
    )
    option = _parse_browser_label(label, specs(route=route, departure=departure), 1)
    assert option is not None
    assert (option.price, option.duration_minutes, option.stops) == (price, duration, stops)
    assert (option.legs[0].origin, option.legs[0].destination) == route


@pytest.mark.feature("saved-comparison")
@given(
    route=routes(),
    departure=st.dates(min_value=date(2027, 1, 1), max_value=date(2029, 12, 20)),
    first_price=st.integers(20, 2000),
    second_price=st.integers(20, 2000),
)
def test_saved_progress_resumes_and_compares_by_price(route, departure, first_price, second_price):
    prices = {departure: first_price, departure + timedelta(days=1): second_price}

    class Provider:
        def search(self, spec):
            return ProviderResult(
                "success",
                [_option(spec, prices[spec.departure_date])],
                1,
                SearchCoverage(fully_explored=True),
            )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = ManagedStore(root / "store")
        executor = BatchExecutor(
            FileCache(root / "cache", namespace="random"), provider_factory=Provider
        )
        api = AgentAPI(store, executor)
        space = SearchSpace(
            template=specs(route=route, departure=departure, search_mode="discover"),
            origins=[route[0]],
            destinations=[route[1]],
            departure_start=departure,
            departure_end=departure + timedelta(days=1),
        )
        planned = api.plan(space.model_dump(mode="json"))
        partial = api.explore(planned["run_id"], work_chunk=1)
        resumed = AgentAPI(store, executor).explore(partial["run_id"], work_chunk=space.count)
        page = api.compare(resumed["run_id"], {"currency": "EUR"})
        assert resumed["progress"]["coverage_complete"] is True
        assert [item["price"] for item in page["results"]] == sorted((first_price, second_price))


@pytest.mark.feature("price-baggage")
@given(
    price=st.integers(1, 5000),
    ceiling=st.integers(1, 5000),
    status=st.sampled_from(("included", "extra_cost", "unknown")),
    covered=st.booleans(),
)
def test_price_and_baggage_rules_never_claim_missing_evidence(price, ceiling, status, covered):
    spec = specs(
        return_date=date(2027, 5, 19),
        overhead_cabin_bags=1,
        require_overhead_cabin_bag=True,
    )
    option = _option(spec, price).model_copy(
        update={
            "baggage": BaggageAllowance(
                status=status, applies_to_journeys=[0, 1] if covered else [0]
            )
        }
    )
    outcome = SearchOutcome(
        request_id="x",
        search_spec=spec,
        status="success",
        options=[option],
        elapsed_ms=0,
        requests_made=0,
        coverage=SearchCoverage(fully_explored=True),
    )
    report = BatchReport(
        outcomes=[outcome],
        counts=BatchCounts(
            total=1,
            success=1,
            empty=0,
            error=0,
            unique_searches=1,
            network_requests=0,
            cache_hits=0,
        ),
        ranked_by_currency={},
    )
    result = collect_matches(
        report,
        ShortlistSpec(
            currency="EUR", max_price=ceiling, require_overhead_cabin_bag=True
        ),
    )
    assert bool(result.matches) is (price <= ceiling and status == "included" and covered)


@pytest.mark.feature("agent-interfaces")
@given(interface=st.sampled_from(("python", "cli", "mcp", "codex", "claude")))
def test_agent_interfaces_expose_focused_guidance(interface):
    if interface == "python":
        assert AgentAPI().schema("search")["title"] == "SearchSpec"
    elif interface == "cli":
        assert run(["guide", "operations"]) == 0
    elif interface == "mcp":
        assert _handler(AgentAPI().schema)(topic="search").ok is True
    else:
        with tempfile.TemporaryDirectory() as directory:
            result = install_skill(interface, project_dir=Path(directory), dry_run=True)
            assert result["skills"][0]["harness"] == interface
            assert result["skills"][0]["action"] == "install"


def _option(spec: SearchSpec, price: int) -> FlightOption:
    departure = datetime.combine(
        spec.departure_date, datetime.min.time(), tzinfo=UTC
    ).replace(hour=9)
    legs = [
        FlightLeg(
            journey_index=index,
            airline_code="ZZ",
            origin=segment.origin,
            destination=segment.destination,
            departure_at=departure + timedelta(days=index),
            arrival_at=departure + timedelta(days=index, hours=2),
            duration_minutes=120,
        )
        for index, segment in enumerate(spec.requested_segments())
    ]
    return FlightOption(
        provider_rank=1,
        price=price,
        currency=spec.currency,
        duration_minutes=120 * len(legs),
        stops=0,
        legs=legs,
    )
