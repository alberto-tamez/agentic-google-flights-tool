"""Fast direct Google Flights discovery using its undocumented frontend service.

Request and response structures are derived from Fli 0.10.0, MIT licensed.
Copyright (c) 2025 Punit Arani. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from agentic_flights.models import FlightLeg, FlightOption, SearchCoverage, SearchSpec
from agentic_flights.providers.base import ProviderError, ProviderResult
from agentic_flights.providers.budget import note_request, remaining

_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
)


class DirectProvider:
    """Retrieve broad shopping results without starting a browser."""

    version = "direct-google-http-v2"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client
        self._page_client: Any | None = None

    def search(self, spec: SearchSpec) -> ProviderResult:
        if spec.search_mode != "discover":
            raise ProviderError(
                "provider_unsupported",
                "Direct search is for discovery; use the browser provider for verification.",
            )
        inner = None
        requests_made = 0
        # The flat RPC format cannot express this flag; the tfs HTTP query can.
        if not spec.hide_separate_and_self_transfer:
            try:
                requests_made = 1
                response = self._post(_localized_url(spec), data=f"f.req={_encode_request(spec)}")
                response.raise_for_status()
            except ProviderError:
                raise
            except Exception as exc:
                remaining()  # Preserve an exhausted overall deadline as query_timeout.
                raise ProviderError(
                    "direct_provider_error",
                    str(exc) or type(exc).__name__,
                    retryable=True,
                    details={"exception_type": type(exc).__name__},
                    requests_made=1,
                ) from exc
            inner = _first_payload(response.text)
        from_page = inner is None
        if inner is None:
            from agentic_flights.providers.http_page import FlightPageClient

            if self._page_client is None:
                self._page_client = FlightPageClient()
            try:
                inner, page_requests = self._page_client.fetch(spec)
                requests_made += page_requests
            except ProviderError as exc:
                exc.requests_made += requests_made
                raise
        try:
            rows = _flight_rows(inner)
        except ProviderError as exc:
            exc.requests_made += requests_made
            raise
        options: list[FlightOption] = []
        failures: list[str] = []
        for row in rows:
            try:
                options.append(_parse_row(row, spec, len(options) + 1))
            except (IndexError, TypeError, ValueError) as exc:
                if len(failures) < 3:
                    failures.append(f"{type(exc).__name__}: {exc}")
        coverage = SearchCoverage(
            source_candidates_loaded=len(rows),
            source_parse_failures=len(rows) - len(options),
            source_parse_failure_samples=failures,
            fully_explored=not failures and not from_page,
            source_truncated=from_page,
            source_load_stop_reason="initial_page" if from_page else "not_applicable",
        )
        if rows and not options:
            raise ProviderError(
                "browser_parse_error",
                "Google Flights returned results in an unreadable direct-search shape.",
                retryable=True,
                details={"samples": failures},
                requests_made=requests_made,
                coverage=coverage,
            )
        return ProviderResult("success" if options else "empty", options, requests_made, coverage)

    def _post(self, url: str, **kwargs: Any) -> Any:
        remaining()
        note_request()
        if self.client is not None:
            return self.client.post(url, **kwargs)
        from curl_cffi import requests

        return requests.post(
            url,
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
            impersonate="chrome",
            timeout=remaining(60),
            **kwargs,
        )


class SmartProvider:
    """Use HTTP for discovery and a browser only for explicit verification."""

    version = "smart-http-browser-v7"

    def __init__(self) -> None:
        self.provider: Any | None = None
        self._direct = DirectProvider()
        self._browser: Any | None = None
        self._access_blocked = False

    def _browser_provider(self) -> Any:
        from agentic_flights.providers.browser import BrowserProvider

        if self._browser is None:
            self._browser = BrowserProvider()
        return self._browser

    def search(self, spec: SearchSpec) -> ProviderResult:
        if spec.search_mode == "verify":
            if self._access_blocked:
                raise ProviderError(
                    "provider_access_blocked",
                    "Google blocked browser requests earlier in this worker. Stop verifying.",
                    coverage=SearchCoverage(blocked=True),
                )
            self.provider = self._browser_provider()
            return self._search_browser(spec)
        self.provider = self._direct
        return self.provider.search(spec)

    def _search_browser(self, spec: SearchSpec) -> ProviderResult:
        try:
            return self.provider.search(spec)
        except ProviderError as exc:
            if exc.code == "provider_access_blocked":
                self._access_blocked = True
            raise

    def close(self) -> None:
        try:
            close = getattr(self._browser, "close", None)
            if close is not None:
                close()
        finally:
            self._browser = self.provider = None
            close = getattr(self._direct, "close", None)
            if close is not None:
                close()


def _encode_request(spec: SearchSpec) -> str:
    segments = []
    for index, segment in enumerate(spec.requested_segments()):
        filters = segment.filters
        times = (
            filters.earliest_departure_hour,
            filters.latest_departure_hour,
            filters.earliest_arrival_hour,
            filters.latest_arrival_hour,
        )
        segments.append(
            [
                [[[segment.origin, 0]]],
                [[[segment.destination, 0]]],
                list(times) if any(value is not None for value in times) else None,
                {"any": 0, "non_stop": 1, "one_stop_or_fewer": 2, "two_or_fewer": 3}[
                    spec.max_stops.value
                ],
                filters.airlines or None,
                filters.excluded_airlines or None,
                segment.departure_date.isoformat(),
                [filters.max_duration_minutes] if filters.max_duration_minutes else None,
                None,
                filters.connecting_airports or None,
                None,
                filters.min_layover_minutes,
                filters.max_layover_minutes,
                [1] if filters.less_emissions_only else None,
                1 if spec.return_date is not None and index > 0 else 3,
            ]
        )
    main = [
        None,
        None,
        3 if spec.additional_segments else 1 if spec.return_date is not None else 2,
        None,
        [],
        {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}[
            spec.cabin.value
        ],
        [spec.adults, spec.children, spec.infants_on_lap, spec.infants_in_seat],
        [None, spec.max_price] if spec.max_price else None,
        None,
        None,
        [spec.checked_bags, int(spec.overhead_cabin_bags > 0)]
        if spec.checked_bags or spec.overhead_cabin_bags
        else None,
        None,
        None,
        segments,
        None,
        None,
        None,
        1,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        int(spec.exclude_basic_economy),
    ]
    formatted = [[], main, 2, 1, 0, 1]
    wrapped = [None, json.dumps(formatted, separators=(",", ":"))]
    return urllib.parse.quote(json.dumps(wrapped, separators=(",", ":")))


def _localized_url(spec: SearchSpec) -> str:
    return (
        f"{_URL}?curr={urllib.parse.quote(spec.currency)}"
        f"&hl={urllib.parse.quote(spec.language)}&gl={urllib.parse.quote(spec.country)}"
    )


def _first_payload(body: str) -> Any | None:
    raw = body.lstrip()
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip()
    candidates = [line for line in raw.splitlines() if line.lstrip().startswith("[")]
    for candidate in [raw, *candidates]:
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
                and row[2]
            ):
                return json.loads(row[2])
    return None


def _has_wrb_payload(body: str) -> bool:
    return _first_payload(body) is not None


def _flight_rows(inner: Any) -> list[Any]:
    if not isinstance(inner, list) or len(inner) < 4:
        raise ProviderError("provider_response_error", "Google Flights response schema changed.")
    rows = []
    for group in inner[2:4]:
        if group is None:
            continue
        if not isinstance(group, list) or not group or (
            group[0] is not None and not isinstance(group[0], list)
        ):
            raise ProviderError("provider_response_error", "Google Flights result group changed.")
        rows.extend(group[0] or [])
    return rows


def _parse_time(value: list[int | None] | None) -> tuple[int, int]:
    # From fast-flights: Google omits trailing zeroes, e.g. [8] means 08:00.
    padded = [*(value or []), None, None]
    return padded[0] or 0, padded[1] or 0


def _parse_row(row: list[Any], spec: SearchSpec, rank: int) -> FlightOption:
    detail = row[0]
    legs = []
    for raw in detail[2]:
        airline = raw[22] or []
        departure = datetime(*raw[20], *_parse_time(raw[8]))
        arrival = datetime(*raw[21], *_parse_time(raw[10]))
        legs.append(
            FlightLeg(
                journey_index=0,
                airline_code=airline[0] if airline else None,
                airline_name=airline[3] if len(airline) > 3 else None,
                flight_number=airline[1] if len(airline) > 1 else None,
                origin=raw[3],
                destination=raw[6],
                departure_at=departure,
                arrival_at=arrival,
                duration_minutes=raw[11],
            )
        )
    price_block = row[1]
    price = float(price_block[0][-1]) if price_block and price_block[0] else None
    emissions = detail[22] if len(detail) > 22 and isinstance(detail[22], list) else []
    return FlightOption(
        provider_rank=rank,
        price=price,
        currency=spec.currency,
        duration_minutes=detail[9],
        stops=max(len(legs) - 1, 0),
        emissions_grams=emissions[7] if len(emissions) > 7 else None,
        result_scope=(
            "complete_itinerary" if len(spec.requested_segments()) == 1 else "outbound_choice"
        ),
        observed_at=datetime.now(UTC),
        legs=legs,
    )
