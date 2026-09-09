from __future__ import annotations

import json
import subprocess
import sysconfig
from datetime import date
from importlib.metadata import version
from pathlib import Path

import pytest
from conftest import make_option, make_spec

from agentic_flights.cli import run
from agentic_flights.provider import ProviderError, ProviderResult


def test_documented_executable_is_installed_and_reports_version() -> None:
    executable = Path(sysconfig.get_path("scripts")) / "agentic-flights"
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"agentic-flights {version('agentic-flights')}"


@pytest.mark.parametrize("blocked", [False, True])
def test_cli_reads_json_and_prints_machine_readable_report(
    tmp_path, monkeypatch, capsys, blocked
) -> None:
    monkeypatch.setenv("AGENTIC_FLIGHTS_STORE", str(tmp_path / "store"))
    class FakeProvider:
        def search(self, spec):
            if blocked:
                raise ProviderError("provider_access_blocked", "Google blocked requests.")
            return ProviderResult("success", [make_option()], 1)

    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "provider": "browser",
                "searches": [make_spec("cli", date(2026, 11, 28)).model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )
    result = run(
        [
            str(input_path),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output",
            str(tmp_path / "full.json"),
        ],
        provider_factory=FakeProvider,
    )
    output = json.loads(capsys.readouterr().out)
    assert result == (1 if blocked else 0)
    assert output["counts"]["error" if blocked else "success"] == 1
    if blocked:
        assert output["error_codes"] == {"provider_access_blocked": 1}
        assert output["counts"]["empty"] == 0
    else:
        assert output["preview"][0]["request_id"] == "cli"
    assert (tmp_path / "full.json").exists()
