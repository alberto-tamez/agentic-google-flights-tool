"""Launch one real Playwright browser in an isolated process and report compact JSON."""

from __future__ import annotations

import asyncio
import json
import sys

from playwright.async_api import async_playwright


async def probe() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, timeout=10_000)
        disconnected = asyncio.Event()
        browser.on("disconnected", lambda _: disconnected.set())
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.set_content("<p>browser probe</p>")
            if not browser.is_connected() or disconnected.is_set():
                raise RuntimeError("browser disconnected during startup probe")
            if await page.text_content("p") != "browser probe":
                raise RuntimeError("browser page operation failed")
            await context.close()
        finally:
            if browser.is_connected():
                await browser.close()


def main() -> int:
    try:
        asyncio.run(probe())
    except Exception as exc:
        raw = str(exc)
        signal = next(
            (name for name in ("SIGABRT", "SIGTRAP", "SIGSEGV") if name in raw), None
        )
        cause = (
            "host_process_restriction"
            if "bootstrap_check_in" in raw or "Permission denied" in raw
            else "browser_process_crash"
            if signal
            else "browser_probe_failure"
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "cause": cause,
                    "signal": signal,
                    "exception_type": type(exc).__name__,
                    "message": raw.splitlines()[0][:500],
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
