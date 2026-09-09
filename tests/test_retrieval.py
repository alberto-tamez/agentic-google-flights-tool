from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from conftest import make_spec

from agentic_flights.google_query import FlightQuery, Passengers, create_query
from agentic_flights.models import SearchCoverage
from agentic_flights.provider import (
    _build_browser_query,
    _load_source_labels,
    _parse_discovery_options,
)


def test_upstream_filters_are_serialized_for_outbound_and_return() -> None:
    spec = make_spec(
        "upstream",
        date(2027, 1, 15),
        destination="LIS",
        return_date=date(2027, 1, 22),
        max_stops="one_stop_or_fewer",
        max_price=400,
        segment_filters={
            "airlines": ["ib"],
            "earliest_departure_hour": 7,
            "latest_departure_hour": 18,
            "max_duration_minutes": 240,
        },
        return_segment_filters={
            "airlines": ["oneworld"],
            "earliest_arrival_hour": 9,
            "latest_arrival_hour": 22,
        },
    )
    query, _ = _build_browser_query(spec, FlightQuery, Passengers, create_query)
    outbound, returning = query.flight_data
    assert list(outbound.airlines) == ["IB"]
    assert outbound.earliest_departure_hour == 7
    assert outbound.latest_departure_hour == 18
    assert outbound.max_duration_minutes == 240
    assert list(returning.airlines) == ["ONEWORLD"]
    assert returning.earliest_arrival_hour == 9
    assert returning.latest_arrival_hour == 22
    assert query.max_price == 400


def test_view_more_loads_and_deduplicates_until_ui_exhaustion() -> None:
    page = _FakePage([["a", "a", "b"], ["a", "b", "c", "c"]])
    spec = make_spec("load", date(2027, 1, 15), retrieval_limit=250, load_more_clicks=1)
    coverage = SearchCoverage()
    labels = asyncio.run(_load_source_labels(page, coverage, spec))
    assert labels == ["a", "b", "c"]
    assert coverage.source_candidates_loaded == 3
    assert coverage.source_duplicates == 1
    assert coverage.source_load_more_clicks == 1
    assert coverage.source_load_stop_reason == "ui_exhausted"
    assert coverage.source_truncated is False


def test_retrieval_limit_stops_before_view_more() -> None:
    page = _FakePage([["a", "b", "c"]], button_visible=True)
    spec = make_spec("limit", date(2027, 1, 15), retrieval_limit=2, load_more_clicks=3)
    coverage = SearchCoverage()
    labels = asyncio.run(_load_source_labels(page, coverage, spec))
    assert labels == ["a", "b"]
    assert coverage.source_load_more_clicks == 0
    assert coverage.source_load_stop_reason == "retrieval_limit"
    assert coverage.source_truncated is True


def test_mixed_parse_loss_is_reported_separately_from_pruning() -> None:
    spec = make_spec("parse", date(2027, 1, 15), destination="LIS")
    valid = (
        "From 29 euros one way. Nonstop flight with Iberia. Leaves Madrid Airport at "
        "8:00 AM on Friday, January 15 and arrives at Lisbon Airport at 8:25 AM on "
        "Friday, January 15. Total duration 1 hr 25 min. Select flight"
    )
    coverage = SearchCoverage()
    options = _parse_discovery_options(
        [valid, "unparseable result"],
        spec,
        spec.requested_segments(),
        coverage,
        datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert len(options) == 1
    assert coverage.source_parse_failures == 1
    assert coverage.branches_pruned == 0


class _FakeWaiter:
    async def wait_for(self, **kwargs) -> None:
        return None


class _FakeResults:
    def __init__(self, page) -> None:
        self.page = page
        self.first = _FakeWaiter()

    def or_(self, other):
        return self

    async def evaluate_all(self, script):
        return self.page.states[self.page.index]

    async def count(self) -> int:
        return len(self.page.states[self.page.index])


class _FakeButton:
    def __init__(self, page) -> None:
        self.page = page
        self.first = self

    async def count(self) -> int:
        return int(self.page.button_visible and self.page.index + 1 < len(self.page.states))

    async def is_visible(self) -> bool:
        return bool(await self.count())

    async def scroll_into_view_if_needed(self) -> None:
        return None

    async def click(self) -> None:
        self.page.index += 1


class _FakePage:
    def __init__(self, states, button_visible=True) -> None:
        self.states = states
        self.index = 0
        self.button_visible = button_visible

    def locator(self, selector):
        return _FakeResults(self)

    def get_by_role(self, role, name, exact=False):
        return _FakeButton(self)

    def get_by_text(self, text):
        class Missing:
            async def count(self):
                return 0
        return Missing()

    async def wait_for_function(self, script, arg, timeout) -> None:
        return None


DAY = date(2027, 1, 14)


def test_detached_more_button_recovers():
    from test_retrieval import _FakePage

    from agentic_flights.provider import _load_source_labels

    page = _FakePage([["a"], ["a", "b"]])
    original = page.get_by_role
    calls = [0]

    def role(*args, **kwargs):
        button = original(*args, **kwargs)
        old = button.click

        async def click():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("Element is not attached to DOM")
            await old()

        button.click = click
        return button

    page.get_by_role = role
    coverage = SearchCoverage()
    labels = asyncio.run(_load_source_labels(page, coverage, make_spec("x", DAY)))
    assert labels == ["a", "b"] and coverage.retries == 1
