from __future__ import annotations

import hashlib
import json
import re
from asyncio import Runner, wait_for
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from reverse_google_flights.models import (
    BaggageAllowance,
    BaggageStatus,
    FlightLeg,
    FlightOption,
    SearchCoverage,
    SearchError,
    SearchSpec,
)


@dataclass(frozen=True)
class ProviderResult:
    status: str
    options: list[FlightOption]
    requests_made: int
    coverage: SearchCoverage = field(default_factory=SearchCoverage)


class Provider(Protocol):
    def search(self, spec: SearchSpec) -> ProviderResult: ...


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        requests_made: int = 0,
        coverage: SearchCoverage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details
        self.requests_made = requests_made
        self.coverage = coverage or SearchCoverage()


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
                "The flights dependency is missing. Install reverse-google-flights[fli].",
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


_RESULT_SELECTOR = 'div[role="link"][aria-label*="Select flight"]'
_RESULT_RE = re.compile(
    r"^(?:From (?P<price>[\d.,]+) .+?\.|Total price is unavailable\.)\s*"
    r"(?P<stops>Nonstop|\d+ stops?) flight with (?P<airline>.+?)\.\s*"
    r"(?:Operated by .+?\.\s*)?Leaves (?P<origin_name>.+?) at "
    r"(?P<departure_time>\d{1,2}:\d{2} [AP]M) on (?P<departure_date>.+?) "
    r"and arrives at (?P<destination_name>.+?) at "
    r"(?P<arrival_time>\d{1,2}:\d{2} [AP]M) on (?P<arrival_date>.+?)\.\s*"
    r"Total duration (?:(?P<hours>\d+) hr)?\s*(?:(?P<minutes>\d+) min)?\.",
)


class BrowserProvider:
    version = "browser-playwright-v6"

    def __init__(self) -> None:
        self._runner = Runner()
        self._playwright = None
        self._browser = None

    def search(self, spec: SearchSpec) -> ProviderResult:
        # One retry recovers a transient navigation/DOM replacement; repeated
        # failures return evidence to the caller instead of looping indefinitely.
        spent = 0
        for attempt in range(2):
            current = spec
            if spec.max_browser_transitions is not None:
                current = spec.model_copy(
                    update={"max_browser_transitions": spec.max_browser_transitions - spent}
                )
            try:
                result = self._runner.run(self._search(current))
                result.coverage.retries += attempt
                result.coverage.browser_transitions += spent
                return ProviderResult(
                    result.status, result.options, result.requests_made + spent, result.coverage
                )
            except ProviderError as exc:
                exc.requests_made += spent
                exc.coverage.browser_transitions += spent
                exc.coverage.retries += attempt
                raise
            except Exception as exc:
                transient = any(
                    word in str(exc).lower()
                    for word in ("not attached", "detached", "timeout", "net::err", "closed")
                )
                failed_coverage = getattr(exc, "search_coverage", SearchCoverage())
                spent += failed_coverage.browser_transitions
                if (
                    transient
                    and attempt == 0
                    and (
                        spec.max_browser_transitions is None or spent < spec.max_browser_transitions
                    )
                ):
                    continue
                failed_coverage.browser_transitions = spent
                failed_coverage.retries += attempt
                raise ProviderError(
                    "browser_provider_error",
                    str(exc) or type(exc).__name__,
                    retryable=transient,
                    details={"exception_type": type(exc).__name__},
                    requests_made=spent,
                    coverage=failed_coverage,
                ) from exc
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._runner.run(self._close())
        self._runner.close()

    async def _close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
            self._browser = self._playwright = None

    async def _search(self, spec: SearchSpec) -> ProviderResult:
        from fast_flights import FlightQuery, Passengers, create_query
        from playwright.async_api import async_playwright

        query, segments = _build_browser_query(spec, FlightQuery, Passengers, create_query)
        kind = (
            "discovery"
            if spec.search_mode == "discover"
            or (len(segments) == 1 and not spec.require_overhead_cabin_bag)
            else "tree"
        )
        _read_continuation(spec, kind)
        if self._browser is None or not self._browser.is_connected():
            await self._close()
            self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.launch(
                    channel="chrome", headless=True
                )
            except Exception:
                self._browser = await self._playwright.chromium.launch(headless=True)
        if spec.search_mode == "discover" or (
            len(segments) == 1 and not spec.require_overhead_cabin_bag
        ):
            return await _search_discovery_browser(self._browser, query.url(), spec, segments)
        return await _explore_complete_tickets(self._browser, query.url(), spec, len(segments))


def _build_browser_query(
    spec: SearchSpec,
    flight_query: Any,
    passengers: Any,
    create_query: Any,
) -> tuple[Any, list[Any]]:
    max_stops = {
        "any": None,
        "non_stop": 0,
        "one_stop_or_fewer": 1,
        "two_or_fewer": 2,
    }[spec.max_stops.value]
    requested_segments = spec.requested_segments()
    flights = [
        flight_query(
            date=segment.departure_date.isoformat(),
            from_airport=segment.origin,
            to_airport=segment.destination,
            max_stops=max_stops,
            airlines=segment.filters.airlines or None,
            earliest_departure_hour=segment.filters.earliest_departure_hour,
            latest_departure_hour=segment.filters.latest_departure_hour,
            earliest_arrival_hour=segment.filters.earliest_arrival_hour,
            latest_arrival_hour=segment.filters.latest_arrival_hour,
            max_duration_minutes=segment.filters.max_duration_minutes,
        )
        for segment in requested_segments
    ]
    trip = (
        "multi-city"
        if spec.additional_segments
        else "round-trip"
        if spec.return_date is not None
        else "one-way"
    )
    query = create_query(
        flights=flights,
        trip=trip,
        seat=spec.cabin.value.replace("_", "-"),
        passengers=passengers(adults=spec.adults),
        currency=spec.currency,
        language="en",
        carry_on_bags=spec.overhead_cabin_bags,
        max_price=spec.max_price,
    )
    return query, requested_segments


async def _result_labels(results: Any) -> list[str]:
    return await results.evaluate_all(
        "elements => elements.map(element => element.getAttribute('aria-label'))"
    )


class _BudgetExhausted(Exception):
    pass


def _consume_transition(coverage: SearchCoverage, spec: SearchSpec) -> None:
    if (
        spec.max_browser_transitions is not None
        and coverage.browser_transitions >= spec.max_browser_transitions
    ):
        coverage.budget_exhausted = True
        raise _BudgetExhausted
    coverage.browser_transitions += 1


async def _open_search_page(
    context: Any, url: str, coverage: SearchCoverage, spec: SearchSpec
) -> Any:
    _consume_transition(coverage, spec)
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        await page.close()
        raise
    if page.url.startswith("https://consent.google.com"):
        reject = page.get_by_role("button", name="Reject all")
        if not await reject.count():
            await page.close()
            raise ProviderError(
                "consent_required",
                "The Google consent page has no Reject all button.",
                coverage=coverage,
                requests_made=coverage.browser_transitions,
            )
        _consume_transition(coverage, spec)
        await reject.click()
    return page


async def _load_source_labels(page: Any, coverage: SearchCoverage, spec: SearchSpec) -> list[str]:
    clicks = 0
    coverage.last_source_truncated = False
    while True:
        results = page.locator(_RESULT_SELECTOR)
        await results.first.wait_for(timeout=60_000)
        raw_labels = [label for label in await _result_labels(results) if label]
        labels = list(dict.fromkeys(raw_labels))
        if spec.retrieval_limit is not None and len(labels) >= spec.retrieval_limit:
            labels = labels[: spec.retrieval_limit]
            _mark_source_stop(coverage, "retrieval_limit", truncated=True)
            break
        button = page.get_by_role("button", name="View more flights", exact=False)
        visible = bool(await button.count() and await button.first.is_visible())
        if not visible:
            _mark_source_stop(coverage, "ui_exhausted", truncated=False)
            break
        if spec.load_more_clicks is not None and clicks >= spec.load_more_clicks:
            _mark_source_stop(coverage, "click_budget", truncated=True)
            break
        before = await results.count()
        try:
            _consume_transition(coverage, spec)
        except _BudgetExhausted:
            _mark_source_stop(coverage, "transition_budget", truncated=True)
            break
        for retry in range(2):
            try:
                # Re-resolve the locator after a dynamic page replacement.
                button = page.get_by_role("button", name="View more flights", exact=False)
                # click performs its own scrolling; avoid a separate stale-element step.
                await wait_for(button.first.click(), timeout=20)
                break
            except Exception:
                if retry:
                    _mark_source_stop(coverage, "load_error", truncated=True)
                    coverage.source_candidates_loaded += len(labels)
                    return labels
                coverage.retries += 1
        coverage.source_load_more_clicks += 1
        clicks += 1
        try:
            await page.wait_for_function(
                """input => {
                    const count = document.querySelectorAll(input.results).length;
                    return count > input.before;
                }""",
                arg={"results": _RESULT_SELECTOR, "before": before},
                timeout=20_000,
            )
        except Exception:
            _mark_source_stop(coverage, "load_error", truncated=True)
            break
    coverage.source_candidates_loaded += len(labels)
    coverage.source_duplicates += max(0, len(raw_labels) - len(set(raw_labels)))
    return labels


def _mark_source_stop(
    coverage: SearchCoverage,
    reason: str,
    *,
    truncated: bool,
) -> None:
    coverage.last_source_truncated = truncated
    if truncated or coverage.source_load_stop_reason == "not_applicable":
        coverage.source_load_stop_reason = reason
    coverage.source_truncated = coverage.source_truncated or truncated


async def _select_label(
    page: Any,
    label: str,
    *,
    terminal: bool,
    coverage: SearchCoverage,
    spec: SearchSpec,
) -> None:
    results = page.locator(_RESULT_SELECTOR)
    await results.first.wait_for(timeout=60_000)
    target_identity = _choice_identity(label)
    # URL changes can precede the next stage's DOM. Wait for the intended
    # observed flight, not merely for any old result to remain visible.
    parts = [part for part in target_identity[1:9] if part] if len(target_identity) > 1 else [label]
    try:
        await page.wait_for_function(
            """input => Array.from(document.querySelectorAll(input.selector)).some(node => {
                const label = (node.getAttribute('aria-label') || '').replace(/\\s+/g, ' ');
                return input.parts.every(part => label.includes(part));
            })""",
            arg={"selector": _RESULT_SELECTOR, "parts": parts},
            timeout=20_000,
        )
    except Exception as exc:
        raise ProviderError(
            "branch_changed",
            "The selected flight did not appear after navigation.",
            retryable=True,
            coverage=coverage,
        ) from exc
    current_labels = await _result_labels(results)
    choice = None
    for index, current_label in enumerate(current_labels):
        if current_label and _choice_identity(current_label) == target_identity:
            candidate = results.nth(index)
            if await candidate.is_visible():
                choice = candidate
                break
    if choice is None:
        raise ProviderError(
            "branch_changed",
            "A previously discovered Google Flights choice was unavailable during replay.",
            retryable=True,
            details={
                "requested_choice": target_identity,
                "available_choices": [_choice_identity(item) for item in current_labels if item][
                    :5
                ],
            },
            coverage=coverage,
            requests_made=coverage.browser_transitions,
        )
    _consume_transition(coverage, spec)
    previous_url = page.url
    await choice.press("Enter")
    if terminal:
        await page.wait_for_url("**/travel/flights/booking**", timeout=60_000)
        return
    await page.wait_for_function(
        "previous => window.location.href !== previous",
        arg=previous_url,
        timeout=60_000,
    )


def _choice_identity(label: str) -> tuple[str, ...]:
    match = _RESULT_RE.search(" ".join(label.split()))
    if match is None:
        return (" ".join(label.split()),)
    return tuple(
        match[name]
        for name in (
            "stops",
            "airline",
            "origin_name",
            "departure_time",
            "departure_date",
            "destination_name",
            "arrival_time",
            "arrival_date",
            "hours",
            "minutes",
        )
    )


async def _search_discovery_browser(
    browser: Any, url: str, spec: SearchSpec, requested_segments: list[Any]
) -> ProviderResult:
    if spec.continuation:
        spec = spec.model_copy(
            update={
                k: spec.continuation.get(k, getattr(spec, k))
                for k in ("retrieval_limit", "load_more_clicks")
            }
        )
    coverage = SearchCoverage()
    context = await browser.new_context(locale="en-US")
    try:
        page = await _open_search_page(context, url, coverage, spec)
        try:
            labels = await _load_source_labels(page, coverage, spec)
        finally:
            await page.close()
    except Exception as exc:
        exc.search_coverage = coverage
        raise
    finally:
        await context.close()
    _read_continuation(spec, "discovery")
    coverage.candidates_seen = len(labels)
    options = _parse_discovery_options(
        labels, spec, requested_segments, coverage, datetime.now(UTC)
    )
    coverage.fully_explored = not (coverage.source_truncated or coverage.source_parse_failures)
    if coverage.source_truncated:
        coverage.pending_branches = 1
        coverage.continuation = _continuation(
            spec,
            "discovery",
            retrieval_limit=(None if spec.retrieval_limit is None else spec.retrieval_limit * 2),
            load_more_clicks=(None if spec.load_more_clicks is None else spec.load_more_clicks + 1),
        )
    if not options:
        raise ProviderError(
            "browser_parse_error",
            "Google Flights returned results, but none matched the English result schema.",
            retryable=True,
            details={"result_labels": len(labels)},
            coverage=coverage,
            requests_made=coverage.browser_transitions,
        )
    return ProviderResult("success", options, coverage.browser_transitions, coverage=coverage)


def _parse_discovery_options(
    labels: list[str],
    spec: SearchSpec,
    requested_segments: list[Any],
    coverage: SearchCoverage,
    observed_at: datetime,
) -> list[FlightOption]:
    first = requested_segments[0]
    first_segment_spec = spec.model_copy(
        update={
            "origin": first.origin,
            "destination": first.destination,
            "departure_date": first.departure_date,
            "return_date": None,
            "additional_segments": [],
        }
    )
    options: list[FlightOption] = []
    for rank, label in enumerate(labels, start=1):
        option = _parse_browser_label(label, first_segment_spec, rank, observed_at)
        if option is None:
            coverage.source_parse_failures += 1
            if len(coverage.source_parse_failure_samples) < 3:
                coverage.source_parse_failure_samples.append(label[:1_000])
            continue
        options.append(option)
    if len(requested_segments) > 1:
        options = [
            option.model_copy(
                update={
                    "result_scope": "outbound_choice",
                    "ticket_scope": "partial_or_unknown",
                    "price_provenance": "provider_search_result",
                }
            )
            for option in options
        ]
    return options


async def _explore_complete_tickets(
    browser: Any, url: str, spec: SearchSpec, journey_count: int
) -> ProviderResult:
    if spec.continuation:
        spec = spec.model_copy(
            update={
                k: spec.continuation.get(k, getattr(spec, k))
                for k in ("retrieval_limit", "load_more_clicks")
            }
        )
    coverage = SearchCoverage()
    context = await browser.new_context(locale="en-US")

    async def replay(prefix: list[str], *, terminal: bool) -> tuple[Any, str | None]:
        page = await _open_search_page(context, url, coverage, spec)
        try:
            for index, label in enumerate(prefix):
                await _select_label(
                    page,
                    label,
                    terminal=terminal and index + 1 == len(prefix),
                    coverage=coverage,
                    spec=spec,
                )
            if terminal:
                await page.get_by_text("Itinerary summary", exact=True).first.wait_for(
                    timeout=60_000
                )
                await page.get_by_text("Selected flights", exact=True).first.wait_for(
                    timeout=60_000
                )
                await page.get_by_text("Booking options", exact=True).first.wait_for(timeout=60_000)
                return await page.locator("body").inner_text(), page.url
            return await _load_source_labels(page, coverage, spec), None
        finally:
            await page.close()

    async def discover(prefix: list[str]) -> list[str]:
        labels, _ = await replay(prefix, terminal=False)
        return labels

    async def finalize(prefix: list[str]) -> FlightOption:
        body, source_url = await replay(prefix, terminal=True)
        return _parse_complete_itinerary(prefix, body, source_url, spec, datetime.now(UTC))

    try:
        options = await _run_bounded_exploration(spec, journey_count, coverage, discover, finalize)
    finally:
        await context.close()
    return _complete_result(options, coverage)


def _search_fingerprint(spec: SearchSpec) -> str:
    data = spec.model_dump(
        mode="json",
        exclude={
            "request_id",
            "continuation",
            "max_results",
            "retrieval_limit",
            "load_more_clicks",
            "candidates_per_stage",
            "max_complete_quotes",
            "max_browser_transitions",
            "stage_candidate_offsets",
        },
    )
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _read_continuation(spec: SearchSpec, kind: str) -> dict[str, Any]:
    state = spec.continuation or {}
    if state and (
        state.get("version") != 1
        or state.get("kind") != kind
        or state.get("fingerprint") != _search_fingerprint(spec)
    ):
        raise ProviderError("invalid_continuation", "Continuation does not match this search.")
    return state


def _continuation(spec: SearchSpec, kind: str, **state: Any) -> dict[str, Any]:
    return {"version": 1, "kind": kind, "fingerprint": _search_fingerprint(spec), **state}


async def _run_bounded_exploration(
    spec: SearchSpec,
    journey_count: int,
    coverage: SearchCoverage,
    discover: Callable[[list[str]], Awaitable[list[str]]],
    finalize: Callable[[list[str]], Awaitable[FlightOption]],
) -> list[FlightOption]:
    state = _read_continuation(spec, "tree")
    pending = list(state.get("pending", [[]]))
    deferred: list[list[str]] = []
    options = [FlightOption.model_validate(item) for item in state.get("options", [])]
    seen_quotes = {_complete_quote_key(option) for option in options}
    expanded = dict(state.get("expanded", {}))
    failed_paths = set(state.get("failed_paths", []))
    while pending:
        prefix = pending.pop(0)
        terminal = len(prefix) == journey_count
        if (
            terminal
            and spec.max_complete_quotes is not None
            and (coverage.branches_attempted >= spec.max_complete_quotes)
        ):
            pending.insert(0, prefix)
            coverage.budget_exhausted = True
            break
        try:
            if terminal:
                coverage.branches_attempted += 1
                option = await finalize(prefix)
                failed_paths.discard(json.dumps(prefix))
                coverage.quotes_completed += 1
                if spec.require_overhead_cabin_bag and not _baggage_meets_requirement(
                    option.baggage, journey_count
                ):
                    coverage.quotes_filtered += 1
                elif _complete_quote_key(option) in seen_quotes:
                    coverage.duplicates += 1
                else:
                    seen_quotes.add(_complete_quote_key(option))
                    options.append(option)
            else:
                labels = await discover(prefix)
                failed_paths.discard(json.dumps(prefix))
                coverage.candidates_seen += len(labels)
                key = json.dumps(prefix)
                previous = set(expanded.get(key, []))
                fresh = [
                    label for label in labels if json.dumps(_choice_identity(label)) not in previous
                ]
                expanded[key] = list(previous | {json.dumps(_choice_identity(x)) for x in labels})
                depth = len(prefix)
                offset = (
                    spec.stage_candidate_offsets[depth]
                    if depth < len(spec.stage_candidate_offsets)
                    else 0
                )
                fresh = fresh[offset:] + fresh[:offset]
                if depth == 0 and spec.preferred_outbound:
                    target = FlightOption.model_validate(spec.preferred_outbound)

                    def preference(label, target=target):
                        candidate = _parse_browser_label(label, spec, 1)
                        if candidate is None:
                            return True

                        def identity(flight):
                            return [
                                (
                                    leg.origin,
                                    leg.destination,
                                    leg.departure_at,
                                    leg.arrival_at,
                                    leg.airline_name,
                                )
                                for leg in flight.legs
                                if leg.journey_index == 0
                            ]

                        return identity(candidate) != identity(target)

                    fresh.sort(key=preference)
                n = spec.candidates_per_stage
                chosen = fresh if n is None else fresh[:n]
                deferred.extend([*prefix, label] for label in ([] if n is None else fresh[n:]))
                pending[0:0] = [[*prefix, label] for label in chosen]
                if coverage.last_source_truncated:
                    # Revisit this expansion with a larger retrieval window next chunk.
                    deferred.append(prefix)
                    if coverage.source_load_stop_reason == "load_error":
                        failed_paths.add(json.dumps(prefix))
        except _BudgetExhausted:
            pending.insert(0, prefix)
            coverage.budget_exhausted = True
            break
        except Exception as exc:
            _record_branch_error(coverage, exc)
            failed_paths.add(json.dumps(prefix))
            deferred.append(prefix)
    pending.extend(deferred)
    coverage.pending_branches = len(pending)
    coverage.blocked = bool(pending) and all(json.dumps(x) in failed_paths for x in pending)
    coverage.branches_pruned = len(pending)  # legacy count; these branches are now retained
    if pending:
        coverage.continuation = _continuation(
            spec,
            "tree",
            pending=pending,
            expanded=expanded,
            failed_paths=sorted(failed_paths),
            options=[option.model_dump(mode="json") for option in options],
            retrieval_limit=(None if spec.retrieval_limit is None else spec.retrieval_limit * 2),
            load_more_clicks=(None if spec.load_more_clicks is None else spec.load_more_clicks + 1),
        )
    coverage.fully_explored = not (
        pending
        or coverage.budget_exhausted
        or coverage.branch_errors
        or coverage.source_truncated
        or coverage.source_parse_failures
    )
    options.sort(
        key=lambda option: (
            option.price is None,
            option.price if option.price is not None else float("inf"),
            option.duration_minutes,
            option.stops,
            _complete_quote_key(option),
        )
    )
    # Keep every quote; output pagination belongs to the saved-result views.
    return [
        option.model_copy(update={"provider_rank": i}) for i, option in enumerate(options, start=1)
    ]


def _complete_result(options: list[FlightOption], coverage: SearchCoverage) -> ProviderResult:
    if options:
        return ProviderResult("success", options, coverage.browser_transitions, coverage)
    if (
        coverage.fully_explored
        and coverage.quotes_completed
        and coverage.quotes_filtered == coverage.quotes_completed
    ):
        return ProviderResult("empty", [], coverage.browser_transitions, coverage=coverage)
    code = (
        "complete_ticket_search_incomplete"
        if coverage.budget_exhausted or coverage.branch_errors or coverage.branches_pruned
        else "complete_ticket_search_failed"
    )
    raise ProviderError(
        code,
        "No explored branch produced a validated complete ticket quote.",
        retryable=True,
        details={"coverage": coverage.model_dump(mode="json")},
        coverage=coverage,
        requests_made=coverage.browser_transitions,
    )


def _record_branch_error(coverage: SearchCoverage, exc: Exception) -> None:
    coverage.branch_errors += 1
    code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
    coverage.branch_errors_by_code[code] = coverage.branch_errors_by_code.get(code, 0) + 1
    if len(coverage.branch_error_samples) < 3:
        coverage.branch_error_samples.append(
            SearchError(
                code=code,
                message=str(exc) or type(exc).__name__,
                retryable=getattr(exc, "retryable", False),
                details=getattr(exc, "details", None),
            )
        )


def _complete_quote_key(option: FlightOption) -> tuple[Any, ...]:
    return (
        tuple(
            (
                leg.journey_index,
                leg.origin,
                leg.destination,
                leg.departure_at.isoformat(),
                leg.arrival_at.isoformat(),
                leg.airline_name,
            )
            for leg in option.legs
        ),
        option.price,
        option.currency,
        option.booking_provider,
        option.fare_name,
        option.baggage.status if option.baggage else None,
    )


def _parse_browser_label(
    label: str,
    spec: SearchSpec,
    rank: int,
    observed_at: datetime | None = None,
) -> FlightOption | None:
    match = _RESULT_RE.search(" ".join(label.split()))
    if match is None or not (match["hours"] or match["minutes"]):
        return None
    departure_at = _parse_local_datetime(
        match["departure_date"], match["departure_time"], spec.departure_date
    )
    arrival_at = _parse_local_datetime(
        match["arrival_date"], match["arrival_time"], spec.departure_date
    )
    if arrival_at < departure_at:
        arrival_at = arrival_at.replace(year=arrival_at.year + 1)
    hours = int(match["hours"] or 0)
    duration = hours * 60 + int(match["minutes"] or 0)
    stops = 0 if match["stops"] == "Nonstop" else int(match["stops"].split()[0])
    price = float(match["price"].replace(",", "")) if match["price"] else None
    return FlightOption(
        provider_rank=rank,
        price=price,
        currency=spec.currency,
        duration_minutes=duration,
        stops=stops,
        result_scope=("outbound_choice" if spec.return_date else "complete_itinerary"),
        observed_at=observed_at or datetime.now(UTC),
        legs=[
            FlightLeg(
                journey_index=0,
                airline_name=match["airline"],
                origin=spec.origin,
                destination=spec.destination,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_minutes=duration,
            )
        ],
    )


def _parse_complete_round_trip(
    outbound_label: str,
    return_label: str,
    booking_body: str,
    spec: SearchSpec,
    observed_at: datetime,
) -> FlightOption:
    return _parse_complete_itinerary(
        [outbound_label, return_label], booking_body, None, spec, observed_at
    )


def _parse_complete_itinerary(
    labels: list[str],
    booking_body: str,
    source_url: str | None,
    spec: SearchSpec,
    observed_at: datetime,
) -> FlightOption:
    normalized_body = " ".join(booking_body.split())
    required_sections = ("Itinerary summary", "Selected flights", "Booking options")
    missing_sections = [section for section in required_sections if section not in normalized_body]
    if missing_sections:
        raise ProviderError(
            "browser_parse_error",
            f"The final booking page is missing: {', '.join(missing_sections)}.",
            requests_made=1,
        )
    requested = spec.requested_segments()
    if len(labels) != len(requested):
        raise ProviderError(
            "browser_parse_error",
            "The selected journey count does not match the requested itinerary.",
            requests_made=1,
        )
    journeys: list[FlightOption] = []
    for index, (label, segment) in enumerate(zip(labels, requested, strict=True)):
        segment_spec = spec.model_copy(
            update={
                "origin": segment.origin,
                "destination": segment.destination,
                "departure_date": segment.departure_date,
                "return_date": None,
                "additional_segments": [],
            }
        )
        parsed = _parse_browser_label(label, segment_spec, index + 1, observed_at)
        if parsed is None:
            raise ProviderError(
                "browser_parse_error",
                f"Selected journey {index + 1} did not match the English result schema.",
                requests_made=1,
            )
        if parsed.legs[0].departure_at.date() != segment.departure_date:
            raise ProviderError(
                "browser_parse_error",
                f"Selected journey {index + 1} has a different departure date.",
                requests_made=1,
            )
        journeys.append(parsed)
    _validate_booking_routes(booking_body, requested)
    final_total = _parse_provider_final_total(booking_body, spec.currency)
    booking_provider, fare_name, fare_evidence = _parse_selected_booking_option(
        booking_body, final_total, spec.currency
    )
    baggage = _parse_baggage_allowance(fare_evidence, journey_count=len(journeys))
    legs = [
        leg.model_copy(update={"journey_index": index})
        for index, journey in enumerate(journeys)
        for leg in journey.legs
    ]
    return FlightOption(
        provider_rank=1,
        price=final_total,
        currency=spec.currency,
        duration_minutes=sum(journey.duration_minutes for journey in journeys),
        stops=sum(journey.stops for journey in journeys),
        result_scope="complete_itinerary",
        ticket_scope="complete_single_ticket",
        price_provenance="provider_final_total",
        observed_at=observed_at,
        baggage=baggage,
        booking_provider=booking_provider,
        fare_name=fare_name,
        source_url=source_url,
        legs=legs,
    )


def _parse_selected_booking_option(
    body: str, final_total: float, currency: str
) -> tuple[str | None, str | None, str]:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    try:
        booking_index = next(
            index for index, line in enumerate(lines) if line.lower() == "booking options"
        )
    except StopIteration:
        raise ProviderError(
            "browser_parse_error",
            "The booking page has no booking options section.",
            requests_made=1,
        ) from None
    provider_indexes = [
        index
        for index in range(booking_index + 1, len(lines))
        if lines[index].startswith("Book with ")
    ]
    matching_blocks: list[tuple[int, int, int]] = []
    for position, start in enumerate(provider_indexes):
        end = provider_indexes[position + 1] if position + 1 < len(provider_indexes) else len(lines)
        price_indexes = [
            index
            for index in range(start + 1, end)
            if _line_has_amount(lines[index], final_total, currency)
        ]
        if price_indexes:
            matching_blocks.append((start, end, price_indexes[0]))
    if len(matching_blocks) != 1:
        raise ProviderError(
            "browser_parse_error",
            "The final total does not identify exactly one booking provider option.",
            requests_made=1,
        )
    provider_index, provider_end, price_index = matching_blocks[0]
    provider = lines[provider_index].removeprefix("Book with ").removesuffix("Airline").strip()
    fare_name = None
    fare_lines: list[str] = []
    ignored = {"Hide options", "View options", "Booking options"}
    preceding = lines[price_index - 1] if price_index > provider_index else ""
    if preceding not in ignored and not preceding.startswith("Book with "):
        fare_name = preceding
    end = next(
        (index for index in range(price_index + 1, provider_end) if lines[index] == "Continue"),
        min(price_index + 12, provider_end - 1),
    )
    fare_lines = lines[max(provider_index, price_index - 2) : end + 1]
    selected_summary = lines[:booking_index]
    matched_provider = lines[provider_index:provider_end]
    selected_carry_on = [line for line in selected_summary if "carry-on" in line.lower()]
    whole_trip = [
        line
        for line in [*selected_summary, *matched_provider]
        if re.search(r"(?:entire|whole) trip", line, re.IGNORECASE)
        and re.search(r"(?:fare|baggage)", line, re.IGNORECASE)
    ]
    evidence = list(dict.fromkeys([*selected_carry_on, *fare_lines, *whole_trip]))
    return provider, fare_name, "\n".join(evidence)


def _line_has_amount(line: str, amount: float, currency: str) -> bool:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
    match = re.fullmatch(rf"{re.escape(symbol)}\s*([\d.,]+)", line, re.IGNORECASE)
    return bool(match and float(match.group(1).replace(",", "")) == amount)


def _validate_booking_routes(body: str, segments: list[Any]) -> None:
    normalized = body.replace("-", "–")
    missing = [
        f"{segment.origin}–{segment.destination}"
        for segment in segments
        if f"{segment.origin}–{segment.destination}" not in normalized
    ]
    if missing:
        raise ProviderError(
            "browser_parse_error",
            f"The booking summary is missing requested routes: {', '.join(missing)}.",
            requests_made=1,
        )


def _parse_provider_final_total(body: str, currency: str) -> float:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    try:
        start = next(index for index, line in enumerate(lines) if line == "Itinerary summary")
        end = next(
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line == "Selected flights"
        )
    except StopIteration:
        raise ProviderError(
            "browser_parse_error",
            "The booking page has no parseable itinerary summary.",
            requests_made=1,
        ) from None
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency)
    amounts: list[float] = []
    for line in lines[start + 1 : end]:
        patterns = [rf"{re.escape(currency)}\s*([\d.,]+)"]
        if symbol:
            patterns.insert(0, rf"{re.escape(symbol)}\s*([\d.,]+)")
        for pattern in patterns:
            match = re.fullmatch(pattern, line, re.IGNORECASE)
            if match:
                amounts.append(float(match.group(1).replace(",", "")))
                break
    unique = list(dict.fromkeys(amounts))
    if len(unique) == 1:
        return unique[0]
    raise ProviderError(
        "browser_parse_error",
        "The itinerary summary does not contain exactly one final total.",
        requests_made=1,
    )


def _parse_baggage_allowance(body: str, journey_count: int) -> BaggageAllowance:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    carry_on_lines = [line for line in lines if re.search(r"carry-on", line, re.IGNORECASE)]
    applies_lines = [
        line
        for line in lines
        if re.search(r"(?:entire|whole) trip", line, re.IGNORECASE)
        and re.search(r"(?:fare|baggage)", line, re.IGNORECASE)
    ]
    evidence = list(dict.fromkeys([*carry_on_lines, *applies_lines]))
    source_text = " ".join(evidence) or None
    applies = list(range(journey_count)) if applies_lines else []
    included = any(
        re.search(
            r"(?:\b1\b|\bone\b)\s+(?:(?:free|included)\s+)(?:overhead\s+)?carry-on",
            line,
            re.IGNORECASE,
        )
        and not re.search(r"(?:fee|extra|additional|purchase|not included)", line, re.IGNORECASE)
        for line in carry_on_lines
    )
    extra_cost = any(
        re.search(r"(?:fee|extra cost|additional cost|purchase|required)", line, re.IGNORECASE)
        for line in carry_on_lines
    )
    status = BaggageStatus.UNKNOWN
    if included and len(applies) == journey_count:
        status = BaggageStatus.INCLUDED
    elif extra_cost:
        status = BaggageStatus.EXTRA_COST
    return BaggageAllowance(
        status=status,
        source_text=source_text,
        applies_to_journeys=applies,
    )


def _baggage_meets_requirement(
    baggage: BaggageAllowance | None,
    journey_count: int,
) -> bool:
    return bool(
        baggage is not None
        and baggage.status == BaggageStatus.INCLUDED
        and baggage.applies_to_journeys == list(range(journey_count))
    )


def _parse_local_datetime(text_date: str, text_time: str, expected: date) -> datetime:
    parsed = datetime.strptime(f"{text_date} {text_time}", "%A, %B %d %I:%M %p")
    candidates = [parsed.replace(year=expected.year + offset) for offset in (-1, 0, 1)]
    return min(candidates, key=lambda value: abs(value.date() - expected))
