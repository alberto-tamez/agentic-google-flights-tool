"""Fetch embedded flight JSON using fast-flights' HTTP approach.

Derived from fast-flights 3.1.0 fetcher.py and parser.py, MIT licensed.
Copyright (c) 2024-PRESENT fast-flights Contributors. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from agentic_flights.models import SearchCoverage, SearchSpec
from agentic_flights.providers.base import ProviderError
from agentic_flights.providers.budget import note_request, remaining


class _FlightPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script: list[str] = []
        self.in_script = False
        self.form: dict[str, str] | None = None
        self.reject: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            self.in_script = "ds:1" in (attributes.get("class") or "").split()
        if tag == "form" and attributes.get("action") == "https://consent.google.com/save":
            self.form = {}
        if tag == "input" and self.form is not None and attributes.get("name"):
            self.form[attributes["name"]] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_script = False
        if tag == "form":
            if self.form is not None and self.form.get("set_eom") == "true":
                self.reject = self.form
            self.form = None

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.script.append(data)

    def payload(self) -> Any:
        try:
            # Decode JSON only. Never evaluate the containing JavaScript callback.
            data = "".join(self.script).split("data:", 1)[1]
            return json.JSONDecoder().raw_decode(data.lstrip())[0]
        except (IndexError, ValueError) as exc:
            raise ProviderError(
                "provider_response_error", "Google Flights returned no readable ds:1 flight data."
            ) from exc


class FlightPageClient:
    def __init__(self) -> None:
        from primp import Client

        self.client = Client(
            impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True
        )

    def fetch(self, spec: SearchSpec) -> tuple[Any, int]:
        from agentic_flights.google_query import FlightQuery, Passengers, create_query
        from agentic_flights.providers.browser import _build_browser_query

        query, _ = _build_browser_query(spec, FlightQuery, Passengers, create_query)
        url = query.url().replace("/flights/search?", "/flights?") + f"&gl={spec.country}"
        requests_made = 0

        def request(method: str, target: str, **kwargs: Any) -> Any:
            nonlocal requests_made
            remaining()
            note_request()
            requests_made += 1
            response = getattr(self.client, method)(target, timeout=remaining(60), **kwargs)
            location = urlsplit(response.url)
            if response.status_code == 429 or (
                location.hostname in {"www.google.com", "google.com"}
                and location.path.startswith("/sorry/")
            ):
                raise ProviderError(
                    "provider_access_blocked", "Google Flights blocked HTTP access.",
                    coverage=SearchCoverage(blocked=True),
                )
            response.raise_for_status()
            return response

        try:
            response = request("get", url)
            page = _FlightPage()
            page.feed(response.text)
            if urlsplit(response.url).hostname == "consent.google.com":
                if page.reject is None:
                    raise ProviderError("consent_required", "Google has no reject-cookies form.")
                request("post", "https://consent.google.com/save", data=page.reject)
                # Consent's continue URL can omit currency. Re-fetch the original query.
                response = request("get", url)
                page = _FlightPage()
                page.feed(response.text)
            return page.payload(), requests_made
        except ProviderError as exc:
            exc.requests_made += requests_made
            raise
        except Exception as exc:
            remaining()
            raise ProviderError(
                "direct_provider_error", str(exc), requests_made=requests_made,
                details={"exception_type": type(exc).__name__},
            ) from exc
