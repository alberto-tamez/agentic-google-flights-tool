from __future__ import annotations

import json
from datetime import date

from conftest import make_option, make_spec

from agentic_flights.cli import run
from agentic_flights.provider import ProviderResult


def test_cli_reads_json_and_prints_machine_readable_report(tmp_path, capsys) -> None:
    class FakeProvider:
        def search(self, spec):
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
    assert result == 0
    assert output["counts"]["success"] == 1
    assert output["preview"][0]["request_id"] == "cli"
    assert (tmp_path / "full.json").exists()
