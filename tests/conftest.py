from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from feature_contract import FEATURES

from reverse_google_flights import AgentAPI, BatchExecutor
from reverse_google_flights.cache import FileCache
from reverse_google_flights.models import FlightLeg, FlightOption, SearchCoverage, SearchSpec
from reverse_google_flights.provider import ProviderResult
from reverse_google_flights.store import ManagedStore

RANDOMIZED_FEATURES = set(FEATURES)
_FEATURE_RESULTS: list[dict[str, object]] = []


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--feature-report", help="Write randomized feature latency as JSON")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    observed: set[str] = set()
    found_randomized = False
    for item in items:
        if Path(str(item.path)).parent.name != "randomized":
            continue
        found_randomized = True
        markers = list(item.iter_markers("feature"))
        if len(markers) != 1 or not markers[0].args:
            raise pytest.UsageError(f"{item.nodeid} must have exactly one feature marker")
        feature = str(markers[0].args[0])
        if feature not in RANDOMIZED_FEATURES:
            raise pytest.UsageError(f"{item.nodeid} has unknown feature {feature!r}")
        observed.add(feature)
        item.user_properties.append(("feature", feature))
    missing = RANDOMIZED_FEATURES - observed
    if found_randomized and missing:
        raise pytest.UsageError(
            "Randomized feature coverage is missing: " + ", ".join(sorted(missing))
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when != "call":
        return
    properties = dict(report.user_properties)
    if "feature" not in properties:
        return
    result = {
        "feature": properties["feature"],
        "test": report.nodeid,
        "outcome": report.outcome,
        "seconds": round(report.duration, 6),
    }
    if report.failed:
        result["failure"] = str(report.longrepr)
    _FEATURE_RESULTS.append(result)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    destination = session.config.getoption("--feature-report")
    if not destination:
        return
    by_feature: defaultdict[str, float] = defaultdict(float)
    for result in _FEATURE_RESULTS:
        by_feature[str(result["feature"])] += float(result["seconds"])
    payload = {
        "exit_status": exitstatus,
        "seed": session.config.getoption("hypothesis_seed", default=None),
        "tests": _FEATURE_RESULTS,
        "latency_seconds": {
            "by_feature": {key: round(value, 6) for key, value in sorted(by_feature.items())},
            "total_test_calls": round(sum(by_feature.values()), 6),
        },
    }
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


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


def make_api(tmp_path, provider_factory=None):
    class Provider:
        def search(self, spec):
            option = make_option(100)
            if spec.search_mode == "verify":
                option = option.model_copy(
                    update={
                        "ticket_scope": "complete_single_ticket",
                        "price_provenance": "provider_final_total",
                    }
                )
            return ProviderResult("success", [option], 1, SearchCoverage(fully_explored=True))

    return AgentAPI(
        ManagedStore(tmp_path / "store"),
        BatchExecutor(
            FileCache(tmp_path / "cache", namespace="fake"),
            provider_factory=provider_factory or Provider,
        ),
    )
