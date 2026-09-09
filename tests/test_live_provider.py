"""One real CLI workflow. Opt in; no mocks, cache hits, or browser fallback."""

import json
import os
import subprocess
import sysconfig
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.mark.live
def test_direct_cli_search_list_and_show_return_real_fares(tmp_path):
    executable = str(Path(sysconfig.get_path("scripts")) / "agentic-flights")
    departure = date.today() + timedelta(days=63)
    searches = [
        {
            "request_id": name, "origin": "MAD", "destination": destination,
            "departure_date": departure.isoformat(), "return_date": returning,
            "currency": "EUR", "language": "en-US", "country": "ES",
            "search_mode": "discover",
        }
        for name, destination, returning in [
            ("round-trip", "AMS", (departure + timedelta(days=7)).isoformat()),
            ("one-way", "BCN", None),
        ]
    ]

    def cli(*args, payload=None):
        result = subprocess.run(
            [executable, *args], input=json.dumps(payload) if payload else None,
            text=True, capture_output=True, timeout=180,
            env={**os.environ, "AGENTIC_FLIGHTS_STORE": str(tmp_path / "store")},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    manifest = cli("-", payload={
        "provider": "direct", "cache_ttl_seconds": 0, "max_workers": 1,
        "searches": searches,
    })
    assert manifest["counts"]["success"] == len(searches), manifest
    assert manifest["counts"]["cache_hits"] == 0
    assert manifest["counts"]["network_requests"] > 0
    assert manifest["coverage_totals"]["parse_failures"] == 0, manifest
    run_id = manifest["managed_store"]["run_id"]
    report = json.loads((tmp_path / "store" / "runs" / run_id / "report.json").read_text())
    for outcome, search in zip(report["outcomes"], searches, strict=True):
        assert outcome["coverage"]["browser_transitions"] == 0
        assert outcome["options"], outcome
        for option in outcome["options"]:
            assert option["price"] is not None and option["price"] > 0
            assert option["currency"] == "EUR"
            assert option["legs"][0]["origin"] == "MAD"
            assert option["legs"][-1]["destination"] == search["destination"]
            assert option["legs"][0]["departure_at"].startswith(departure.isoformat())
    page = cli("list", run_id, "--page-size", "1")
    assert page["total_matches"] > 0
    detail = cli("show", run_id, manifest["preview"][0]["result_id"])
    assert detail
