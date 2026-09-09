import re
import tomllib
from pathlib import Path

import pytest

from agentic_flights.cli import run
from agentic_flights.skill_init import install_skill, skill_destinations


def test_bundled_skill_uses_declared_cli_commands() -> None:
    project_root = Path(__file__).parents[1]
    skill_text = (project_root / "skill/agentic-flights/SKILL.md").read_text(encoding="utf-8")
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    declared_commands = set(project["project"]["scripts"])

    documented_commands = set(re.findall(r"`([a-z][a-z0-9-]+)(?:\s|`)", skill_text))
    project_commands = {command for command in documented_commands if "flight" in command}

    assert project_commands
    assert project_commands <= declared_commands


def test_bundled_skill_keeps_agent_handles_out_of_user_answers() -> None:
    project_root = Path(__file__).parents[1]
    skill_text = (project_root / "skill/agentic-flights/SKILL.md").read_text(encoding="utf-8")

    assert "Treat run IDs, result IDs, request IDs" in skill_text
    assert "Never include" in skill_text
    assert "unless the user asks for diagnostics" in skill_text
    assert "verification_satisfied" in skill_text
    assert "strategy_plan()" in skill_text
    assert "start_strategy_plan()" in skill_text
    assert "strategy_results()" in skill_text
    assert "contract-sensitive strategies off" in skill_text


def test_project_skill_installs_for_both_harnesses(tmp_path: Path) -> None:
    result = install_skill(project_dir=tmp_path)

    installed = {item["harness"]: Path(item["path"]) for item in result["skills"]}
    assert installed["codex"] == tmp_path / ".agents/skills/agentic-flights"
    assert installed["claude"] == tmp_path / ".claude/skills/agentic-flights"
    for destination in installed.values():
        text = (destination / "SKILL.md").read_text()
        assert "name: agentic-flights" in text
        assert "license: MIT" in text
        assert "Python 3.11+" in text
        assert "AgentAPI.plan()" in text

    repeated = install_skill(project_dir=tmp_path)
    assert {item["action"] for item in repeated["skills"]} == {"unchanged"}


def test_existing_custom_skill_requires_force(tmp_path: Path) -> None:
    destination = tmp_path / ".agents/skills/agentic-flights"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("custom\n")

    with pytest.raises(FileExistsError, match="--force"):
        install_skill("codex", project_dir=tmp_path)

    result = install_skill("codex", project_dir=tmp_path, force=True)
    assert result["skills"][0]["action"] == "replace"
    assert (destination / "SKILL.md").read_text().startswith("---\n")


def test_multi_harness_conflict_is_checked_before_any_write(tmp_path: Path) -> None:
    claude = tmp_path / ".claude/skills/agentic-flights"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("custom\n")

    preview = install_skill(project_dir=tmp_path, dry_run=True)
    assert [item["action"] for item in preview["skills"]] == ["install", "conflict"]

    with pytest.raises(FileExistsError, match="--force"):
        install_skill(project_dir=tmp_path)
    assert not (tmp_path / ".agents").exists()


def test_dry_run_and_user_destinations_do_not_write(tmp_path: Path) -> None:
    destinations = skill_destinations("both", "user", user_home=tmp_path)
    assert destinations["codex"] == tmp_path / ".agents/skills/agentic-flights"
    assert destinations["claude"] == tmp_path / ".claude/skills/agentic-flights"

    result = install_skill("claude", project_dir=tmp_path, dry_run=True)
    assert result["skills"][0]["action"] == "install"
    assert not (tmp_path / ".claude").exists()


def test_cli_outputs_install_manifest(tmp_path: Path, capsys) -> None:
    assert run(["init-skill", "--harness", "codex", "--project-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '"invoke": "$agentic-flights"' in output
    assert "detects skill changes automatically" in output
    assert (tmp_path / ".agents/skills/agentic-flights/SKILL.md").is_file()
