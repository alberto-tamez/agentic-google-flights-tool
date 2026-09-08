"""Install the bundled Agentic Flights skill into supported agent harnesses."""

from __future__ import annotations

import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path
from typing import Literal

Harness = Literal["codex", "claude", "both"]
Scope = Literal["project", "user"]

_RELATIVE_ROOTS = {
    "codex": Path(".agents/skills"),
    "claude": Path(".claude/skills"),
}


def skill_destinations(
    harness: Harness,
    scope: Scope,
    *,
    project_dir: Path | None = None,
    user_home: Path | None = None,
) -> dict[str, Path]:
    """Return the documented skill location for each requested harness."""
    selected = ("codex", "claude") if harness == "both" else (harness,)
    if scope == "project":
        base = (project_dir or Path.cwd()).expanduser().resolve()
    else:
        if project_dir is not None:
            raise ValueError("--project-dir can only be used with --scope project")
        base = (user_home or Path.home()).expanduser().resolve()
    return {name: base / _RELATIVE_ROOTS[name] / "agentic-flights" for name in selected}


def install_skill(
    harness: Harness = "both",
    scope: Scope = "project",
    *,
    project_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Copy the bundled skill, preserving existing customizations unless forced."""
    destinations = skill_destinations(harness, scope, project_dir=project_dir)
    source = files("reverse_google_flights").joinpath("_skill", "agentic-flights")
    if not source.is_dir():
        source = Path(__file__).resolve().parents[2] / "skill" / "agentic-flights"
    with as_file(source.joinpath("SKILL.md")) as skill_file:
        source_path = skill_file.parent
        actions: list[dict[str, str]] = []
        for name, destination in destinations.items():
            status = _status(source_path, destination)
            if status == "conflict" and not force:
                raise FileExistsError(
                    f"Skill already exists with different content: {destination}. "
                    "Use --force to replace it."
                )
            action = (
                "unchanged"
                if status == "identical"
                else "replace"
                if status == "conflict"
                else "install"
            )
            if not dry_run and action != "unchanged":
                _copy_skill(source_path, destination, replace=action == "replace")
            actions.append({"harness": name, "path": str(destination), "action": action})
    invocations = {"codex": "$agentic-flights", "claude": "/agentic-flights"}
    return {
        "scope": scope,
        "dry_run": dry_run,
        "skills": [{**item, "invoke": invocations[item["harness"]]} for item in actions],
        "restart_note": (
            "Codex or Claude Code normally detects skill changes. Restart the harness "
            "if agentic-flights does not appear."
        ),
    }


def _status(source: Path, destination: Path) -> str:
    if not destination.exists() and not destination.is_symlink():
        return "missing"
    if destination.is_symlink() or not destination.is_dir():
        return "conflict"
    source_files = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
    destination_files = {
        path.relative_to(destination) for path in destination.rglob("*") if path.is_file()
    }
    if source_files != destination_files:
        return "conflict"
    if any(path.is_symlink() for path in destination.rglob("*")):
        return "conflict"
    return (
        "identical"
        if all(
            (source / path).read_bytes() == (destination / path).read_bytes()
            for path in source_files
        )
        else "conflict"
    )


def _copy_skill(source: Path, destination: Path, *, replace: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".agentic-flights-", dir=destination.parent) as temp:
        staged = Path(temp) / "agentic-flights"
        shutil.copytree(source, staged)
        if replace:
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        staged.rename(destination)
