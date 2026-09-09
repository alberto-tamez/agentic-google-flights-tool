import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_spec

from agentic_flights.models import SearchCoverage
from agentic_flights.providers.base import ProviderError, ProviderResult
from agentic_flights.providers.browser import BrowserProvider, _classify_startup_failure


def fake_playwright(monkeypatch, errors):
    import playwright.async_api

    from agentic_flights.providers import browser

    launches = []
    stopped = []
    closed = []

    async def browser_close():
        closed.append(True)

    async def launch(**kwargs):
        launches.append(kwargs)
        if errors:
            raise RuntimeError(errors.pop(0))
        return SimpleNamespace(is_connected=lambda: True, close=browser_close)

    async def stop():
        stopped.append(True)

    async def start():
        return SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=stop)

    async def discovery(*args):
        return ProviderResult("empty", [], 1, SearchCoverage(fully_explored=True))

    monkeypatch.setattr(
        playwright.async_api, "async_playwright", lambda: SimpleNamespace(start=start)
    )
    monkeypatch.setattr(browser, "_search_discovery_browser", discovery)
    return launches, stopped, closed


@pytest.mark.parametrize("headless", [None, False, True])
def test_managed_browser_is_reused_without_launching_installed_chrome(
    monkeypatch, future_date, headless
):
    if headless is None:
        monkeypatch.delenv("AGENTIC_FLIGHTS_HEADLESS", raising=False)
    else:
        monkeypatch.setenv("AGENTIC_FLIGHTS_HEADLESS", "1" if headless else "0")
    launches, stopped, closed = fake_playwright(monkeypatch, [])
    provider = BrowserProvider()
    try:
        spec = make_spec("reuse", future_date, search_mode="discover")
        provider.search(spec)
        provider.search(spec)
    finally:
        provider.close()
    assert launches == (
        [{"headless": True}]
        if headless is not False
        else [{"channel": "chromium", "headless": False}]
    )
    assert len(stopped) == len(closed) == 1


@pytest.mark.parametrize("missing_managed", [False, True])
def test_startup_crash_blocks_same_worker_relaunch(monkeypatch, future_date, missing_managed):
    errors = ["Executable doesn't exist at /missing"] if missing_managed else []
    errors.append(
        "Target closed: bootstrap_check_in failed: Permission denied; signal=SIGTRAP"
    )
    launches, stopped, closed = fake_playwright(monkeypatch, errors)
    provider = BrowserProvider()
    try:
        for _ in range(3):
            with pytest.raises(ProviderError) as caught:
                provider.search(make_spec("blocked", future_date, search_mode="discover"))
            assert caught.value.code == "browser_startup_error"
            assert not caught.value.retryable
            assert caught.value.requests_made == 0
            assert caught.value.coverage.blocked
            cause = caught.value.details["cause"]
            assert cause == ("missing_browser" if missing_managed else "host_process_restriction")
            if not missing_managed:
                assert caught.value.details["signal"] == "SIGTRAP"
    finally:
        provider.close()
    assert launches == [{"headless": True}]
    assert len(stopped) == 1
    assert not closed


def test_installed_chrome_fallback_only_for_visible_debugging(monkeypatch, future_date):
    monkeypatch.setenv("AGENTIC_FLIGHTS_HEADLESS", "0")
    launches, _, _ = fake_playwright(monkeypatch, ["Executable doesn't exist at /missing"])
    provider = BrowserProvider()
    try:
        provider.search(make_spec("missing", future_date, search_mode="discover"))
    finally:
        provider.close()
    assert launches == [
        {"channel": "chromium", "headless": False},
        {"channel": "chrome", "headless": False},
    ]


def test_browser_process_exit_after_launch_is_not_retried(monkeypatch, future_date):
    import playwright.async_api

    from agentic_flights.providers import browser as browser_module

    launches = []

    class ExitedBrowser:
        def is_connected(self):
            return False

        async def close(self):
            pass

    async def launch(**kwargs):
        launches.append(kwargs)
        return ExitedBrowser()

    async def stop():
        pass

    async def start():
        return SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=stop)

    async def fail_after_launch(*args):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(
        playwright.async_api, "async_playwright", lambda: SimpleNamespace(start=start)
    )
    monkeypatch.setattr(browser_module, "_search_discovery_browser", fail_after_launch)
    provider = BrowserProvider()
    try:
        with pytest.raises(ProviderError) as caught:
            provider.search(make_spec("exit", future_date, search_mode="discover"))
    finally:
        provider.close()
    assert caught.value.code == "browser_startup_error"
    assert caught.value.details["cause"] == "browser_process_exit"
    assert launches == [{"headless": True}]


@pytest.mark.parametrize("signal", ["SIGABRT", "SIGTRAP", "SIGSEGV"])
def test_native_browser_crash_signals_are_compact_and_structured(signal):
    message, exception_type, details = _classify_startup_failure(
        RuntimeError(f"browser exited: signal={signal}")
    )
    assert message == f"The browser process crashed during startup with {signal}."
    assert exception_type == "RuntimeError"
    assert details == {"cause": "browser_process_crash", "signal": signal}


def test_unusual_traffic_page_is_blocked_without_waiting_for_results(future_date):
    import asyncio

    from agentic_flights.providers.browser import _open_search_page

    closed = []

    class Page:
        url = "https://www.google.com/sorry/index?continue=flights"

        async def goto(self, *args, **kwargs):
            pass

        async def close(self):
            closed.append(True)

    async def new_page():
        return Page()

    coverage = SearchCoverage()
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            _open_search_page(
                SimpleNamespace(new_page=new_page),
                "https://www.google.com/travel/flights",
                coverage,
                make_spec("blocked-page", future_date),
            )
        )
    assert caught.value.code == "provider_access_blocked"
    assert not caught.value.retryable
    assert coverage.blocked
    assert caught.value.requests_made == 1
    assert closed == [True]


@pytest.mark.parametrize("field", ["segment_filters", "return_segment_filters"])
def test_browser_does_not_silently_drop_airline_exclusions(monkeypatch, future_date, field):
    launches, _, _ = fake_playwright(monkeypatch, [])
    provider = BrowserProvider()
    try:
        spec = make_spec(
            "exclude",
            future_date,
            return_date=future_date,
            **{field: {"excluded_airlines": ["FR"]}},
        )
        with pytest.raises(ProviderError) as caught:
            provider.search(spec)
        assert caught.value.code == "provider_unsupported"
        assert not caught.value.retryable
    finally:
        provider.close()
    assert not launches


def test_access_block_stops_later_queries_in_worker(monkeypatch, future_date):
    from agentic_flights.providers import browser

    launches, _, _ = fake_playwright(monkeypatch, [])
    requests = []

    async def blocked(*args):
        requests.append(True)
        raise ProviderError(
            "provider_access_blocked",
            "unusual traffic",
            requests_made=1,
            coverage=SearchCoverage(blocked=True),
        )

    monkeypatch.setattr(browser, "_search_discovery_browser", blocked)
    provider = BrowserProvider()
    try:
        for index in range(3):
            with pytest.raises(ProviderError) as caught:
                provider.search(make_spec(str(index), future_date, search_mode="discover"))
            assert caught.value.code == "provider_access_blocked"
            assert caught.value.requests_made == (1 if index == 0 else 0)
    finally:
        provider.close()
    assert len(requests) == len(launches) == 1


@pytest.mark.integration
def test_background_context_and_google_error_page_fail_fast():
    import asyncio

    from playwright.async_api import async_playwright

    from agentic_flights.providers.browser import _new_context, _wait_for_page_content

    async def check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await _new_context(browser)
                page = await context.new_page()
                user_agent = await page.evaluate("navigator.userAgent")
                assert f"Chrome/{browser.version}" in user_agent
                assert "HeadlessChrome" not in user_agent
                await page.set_content("<main><h1>Oops, something went wrong.</h1></main>")
                coverage = SearchCoverage(browser_transitions=1)
                # A real DOM regression: the old result-only wait takes 60 seconds.
                async with asyncio.timeout(3):
                    with pytest.raises(ProviderError) as caught:
                        await _wait_for_page_content(
                            page, page.locator(".missing-result"), coverage
                        )
                assert caught.value.code == "provider_page_error"
                assert caught.value.retryable
                assert caught.value.requests_made == 1
            finally:
                await browser.close()

    asyncio.run(check())


@pytest.mark.integration
def test_background_browser_survives_repeated_isolated_launches():
    probe = Path(__file__).with_name("browser_probe.py")
    failures = []
    for attempt in range(3):
        result = subprocess.run(
            [sys.executable, str(probe)], capture_output=True, text=True, timeout=20
        )
        if result.returncode:
            try:
                diagnostic = json.loads(result.stderr.splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                diagnostic = {"stderr": result.stderr[-1000:]}
            failures.append({"attempt": attempt + 1, **diagnostic})
    assert not failures, json.dumps(failures, indent=2)
