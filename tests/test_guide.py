import json

from agentic_flights.cli import run


def test_guide_explains_installed_workflow(capsys):
    assert run(["guide"]) == 0
    text = capsys.readouterr().out
    assert "agentic-flights guide reference" in text
    assert "no repository checkout is required" in text


def test_reference_and_schema_are_available_on_demand(capsys):
    assert run(["guide", "reference"]) == 0
    assert "SearchSpace" in capsys.readouterr().out
    assert run(["guide", "schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "searches" in schema["properties"]
    assert "SearchSpec" in schema["$defs"]
