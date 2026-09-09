"""Build Google Flights queries and control the browser session."""

from __future__ import annotations

import os
import re
from asyncio import Runner, wait_for
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from agentic_flights.models import (
    FlightOption,
    SearchCoverage,
    SearchSpec,
)
from agentic_flights.providers.base import (
    ProviderError,
    ProviderResult,
)
from agentic_flights.providers.budget import remaining
from agentic_flights.providers.parsing import (
    _choice_identity,
    _parse_complete_itinerary,
    _parse_discovery_options,
)
from agentic_flights.providers.traversal import (
    _BudgetExhausted,
    _complete_result,
    _consume_transition,
    _continuation,
    _read_continuation,
    _run_bounded_exploration,
)

_RESULT_SELECTOR = 'div[role="link"][aria-label*="Select flight"]'
_PAGE_ERROR = re.compile(r"^Oops,? something went wrong[.!]?$", re.IGNORECASE)


class BrowserProvider:
    version = "browser-playwright-v10"

    def __init__(self) -> None:
        # A visible window is an explicit debugging choice, never an automatic fallback.
        self._headless = os.environ.get("AGENTIC_FLIGHTS_HEADLESS") != "0"
        self._runner = Runner()
        self._playwright = None
        self._browser = None
        self._startup_failure: tuple[str, str, dict[str, str]] | None = None
        self._access_blocked = False

    def search(self, spec: SearchSpec) -> ProviderResult:
        # One retry recovers a transient navigation/DOM replacement; repeated
        # failures return evidence to the caller instead of looping indefinitely.
        spent = 0
        for attempt in range(2):
            self._coverage = SearchCoverage()
            current = spec
            if spec.max_browser_transitions is not None:
                current = spec.model_copy(
                    update={"max_browser_transitions": spec.max_browser_transitions - spent}
                )
            try:
                timeout = remaining()
                result = self._runner.run(wait_for(self._search(current), timeout=timeout))
                result.coverage.retries += attempt
                result.coverage.browser_transitions += spent
                return ProviderResult(
                    result.status, result.options, result.requests_made + spent, result.coverage
                )
            except TimeoutError as exc:
                raise ProviderError(
                    "query_timeout", "The query exceeded its time limit.", retryable=True,
                    requests_made=spent + self._coverage.browser_transitions,
                    coverage=self._coverage,
                ) from exc
            except ProviderError as exc:
                if exc.code == "provider_access_blocked":
                    self._access_blocked = True
                exc.requests_made += spent
                exc.coverage.browser_transitions += spent
                exc.coverage.retries += attempt
                raise
            except Exception as exc:
                if self._browser is not None and not self._browser.is_connected():
                    message, exception_type, details = _classify_startup_failure(exc)
                    if details["cause"] == "browser_startup_failure":
                        message = "The browser process disconnected unexpectedly."
                        details["cause"] = "browser_process_exit"
                    self._startup_failure = (message, exception_type, details)
                    raise self._startup_error() from exc
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
        from playwright.async_api import async_playwright

        from agentic_flights.google_query import FlightQuery, Passengers, create_query

        query, segments = _build_browser_query(spec, FlightQuery, Passengers, create_query)
        kind = (
            "discovery"
            if spec.search_mode == "discover"
            or (len(segments) == 1 and not spec.require_overhead_cabin_bag)
            else "tree"
        )
        _read_continuation(spec, kind)
        if self._access_blocked:
            raise ProviderError(
                "provider_access_blocked",
                "Google blocked requests earlier in this worker. Stop searching.",
                coverage=SearchCoverage(blocked=True),
            )
        if self._startup_failure is not None:
            raise self._startup_error()
        if self._browser is None or not self._browser.is_connected():
            await self._close()
            try:
                self._playwright = await async_playwright().start()
                if self._headless:
                    # Omitting channel uses Playwright's separate headless shell.
                    # The full Chrome-for-Testing app registers with macOS even in
                    # new-headless mode and can abort inside a restricted host.
                    self._browser = await self._playwright.chromium.launch(headless=True)
                else:
                    try:
                        self._browser = await self._playwright.chromium.launch(
                            channel="chromium", headless=False
                        )
                    except Exception as exc:
                        # Installed Chrome is only a visible-debug fallback for a
                        # missing managed executable.
                        if "executable doesn't exist" not in str(exc).lower():
                            raise
                        self._browser = await self._playwright.chromium.launch(
                            channel="chrome", headless=False
                        )
            except Exception as exc:
                self._startup_failure = _classify_startup_failure(exc)
                raise self._startup_error() from exc
        if spec.search_mode == "discover" or (
            len(segments) == 1 and not spec.require_overhead_cabin_bag
        ):
            return await _search_discovery_browser(
                self._browser, query.url(), spec, segments, self._coverage
            )
        return await _explore_complete_tickets(
            self._browser, query.url(), spec, len(segments), self._coverage
        )

    def _startup_error(self) -> ProviderError:
        message, exception_type, details = self._startup_failure
        return ProviderError(
            "browser_startup_error",
            message,
            details={"exception_type": exception_type, "phase": "browser_startup", **details},
            coverage=SearchCoverage(blocked=True),
        )


def _classify_startup_failure(exc: Exception) -> tuple[str, str, dict[str, str]]:
    raw = str(exc) or type(exc).__name__
    lower = raw.lower()
    details: dict[str, str] = {}
    if "bootstrap_check_in" in lower or "permission denied" in lower:
        details["cause"] = "host_process_restriction"
        if "sigtrap" in lower:
            details["signal"] = "SIGTRAP"
        elif "sigabrt" in lower:
            details["signal"] = "SIGABRT"
        return (
            "The host blocked the browser process during startup. Change the execution "
            "authorization before retrying.",
            type(exc).__name__,
            details,
        )
    for signal in ("SIGABRT", "SIGTRAP", "SIGSEGV"):
        if f"signal={signal.lower()}" in lower:
            return (
                f"The browser process crashed during startup with {signal}.",
                type(exc).__name__,
                {"cause": "browser_process_crash", "signal": signal},
            )
    if "executable doesn't exist" in lower:
        return (
            "Playwright's browser executable is missing. Install Chromium before retrying.",
            type(exc).__name__,
            {"cause": "missing_browser"},
        )
    first_line = raw.splitlines()[0]
    return first_line[:500], type(exc).__name__, {"cause": "browser_startup_failure"}


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
    if any(segment.filters.excluded_airlines for segment in requested_segments):
        raise ProviderError(
            "provider_unsupported",
            "Browser search cannot enforce excluded_airlines; exclusions were not dropped.",
            details={"field": "excluded_airlines"},
        )
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
            connecting_airports=segment.filters.connecting_airports or None,
            min_layover_minutes=segment.filters.min_layover_minutes,
            max_layover_minutes=segment.filters.max_layover_minutes,
            less_emissions_only=segment.filters.less_emissions_only,
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
        passengers=passengers(
            adults=spec.adults,
            children=spec.children,
            infants_in_seat=spec.infants_in_seat,
            infants_on_lap=spec.infants_on_lap,
        ),
        currency=spec.currency,
        language="en",
        carry_on_bags=spec.overhead_cabin_bags,
        checked_bags=spec.checked_bags,
        max_price=spec.max_price,
        hide_separate_and_self_transfer=spec.hide_separate_and_self_transfer,
        exclude_basic_economy=spec.exclude_basic_economy,
    )
    return query, requested_segments


async def _result_labels(results: Any) -> list[str]:
    return await results.evaluate_all(
        "elements => elements.map(element => element.getAttribute('aria-label'))"
    )


async def _new_context(browser: Any) -> Any:
    # Match the desktop Chrome HTTP client. HeadlessChrome's default UA is blocked
    # on Google Flights, including when Playwright uses its separate headless shell.
    return await browser.new_context(
        locale="en-US",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{browser.version} Safari/537.36"
        ),
    )


async def _wait_for_page_content(page: Any, target: Any, coverage: SearchCoverage) -> None:
    error = page.get_by_text(_PAGE_ERROR)
    await target.or_(error).first.wait_for(timeout=60_000)
    await _check_page_error(page, coverage)


async def _check_page_error(page: Any, coverage: SearchCoverage) -> None:
    error = page.get_by_text(_PAGE_ERROR)
    if await error.count() and await error.first.is_visible():
        raise ProviderError(
            "provider_page_error", "Google Flights displayed: Oops, something went wrong.",
            retryable=True, coverage=coverage, requests_made=coverage.browser_transitions,
        )


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
    await _check_access_block(page, coverage)
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
        await _check_access_block(page, coverage)
    return page


async def _check_access_block(page: Any, coverage: SearchCoverage) -> None:
    url = urlsplit(page.url)
    if url.hostname in {"www.google.com", "google.com"} and url.path.startswith("/sorry/"):
        coverage.blocked = True
        await page.close()
        raise ProviderError(
            "provider_access_blocked",
            "Google Flights returned an unusual-traffic block. Stop searching.",
            coverage=coverage,
            requests_made=coverage.browser_transitions,
        )


async def _load_source_labels(page: Any, coverage: SearchCoverage, spec: SearchSpec) -> list[str]:
    clicks = 0
    coverage.last_source_truncated = False
    while True:
        results = page.locator(_RESULT_SELECTOR)
        await _wait_for_page_content(page, results, coverage)
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
    await _wait_for_page_content(page, results, coverage)
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
    await page.wait_for_function(
        """input => /Oops,? something went wrong[.!]?/i.test(document.body?.innerText || '')
            || window.location.pathname.startsWith('/sorry/')
            || (input.terminal
                ? window.location.pathname.startsWith('/travel/flights/booking')
                : window.location.href !== input.previous)""",
        arg={"previous": previous_url, "terminal": terminal},
        timeout=60_000,
    )
    await _check_access_block(page, coverage)
    await _check_page_error(page, coverage)


async def _search_discovery_browser(
    browser: Any, url: str, spec: SearchSpec, requested_segments: list[Any],
    coverage: SearchCoverage | None = None,
) -> ProviderResult:
    if spec.continuation:
        spec = spec.model_copy(
            update={
                k: spec.continuation.get(k, getattr(spec, k))
                for k in ("retrieval_limit", "load_more_clicks")
            }
        )
    coverage = coverage if coverage is not None else SearchCoverage()
    context = await _new_context(browser)
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


async def _explore_complete_tickets(
    browser: Any, url: str, spec: SearchSpec, journey_count: int,
    coverage: SearchCoverage | None = None,
) -> ProviderResult:
    if spec.continuation:
        spec = spec.model_copy(
            update={
                k: spec.continuation.get(k, getattr(spec, k))
                for k in ("retrieval_limit", "load_more_clicks")
            }
        )
    coverage = coverage if coverage is not None else SearchCoverage()
    context = await _new_context(browser)

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
                await _wait_for_page_content(
                    page, page.get_by_text("Itinerary summary", exact=True), coverage
                )
                await _wait_for_page_content(
                    page, page.get_by_text("Selected flights", exact=True), coverage
                )
                await _wait_for_page_content(
                    page, page.get_by_text("Booking options", exact=True), coverage
                )
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
