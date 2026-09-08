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
    if harness not in {"codex", "claude", "both"}:
        raise ValueError("harness must be codex, claude, or both")
    if scope not in {"project", "user"}:
        raise ValueError("scope must be project or user")
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
    source = files("agentic_flights").joinpath("_skill", "agentic-flights")
    if not source.is_dir():
        source = Path(__file__).resolve().parents[2] / "skill" / "agentic-flights"
    with as_file(source.joinpath("SKILL.md")) as skill_file:
        source_path = skill_file.parent
        statuses = {
            name: _status(source_path, destination) for name, destination in destinations.items()
        }
        conflicts = [
            str(destinations[name]) for name, status in statuses.items() if status == "conflict"
        ]
        if conflicts and not force and not dry_run:
            raise FileExistsError(
                "Skills already exist with different content: "
                + ", ".join(conflicts)
                + ". Use --force to replace them."
            )
        actions: list[dict[str, str]] = []
        for name, destination in destinations.items():
            status = statuses[name]
            action = (
                "unchanged"
                if status == "identical"
                else "conflict"
                if status == "conflict" and not force
                else "replace"
                if status == "conflict"
                else "install"
            )
            if not dry_run and action in {"install", "replace"}:
                _copy_skill(source_path, destination, replace=action == "replace")
            actions.append({"harness": name, "path": str(destination), "action": action})
    invocations = {"codex": "$agentic-flights", "claude": "/agentic-flights"}
    activation = {
        "codex": "Codex detects skill changes automatically; restart Codex if it does not appear.",
        "claude": (
            "Claude Code detects changes inside an existing skills directory. Restart Claude Code "
            "if this command created the top-level .claude/skills directory during the session."
        ),
    }
    return {
        "scope": scope,
        "dry_run": dry_run,
        "skills": [
            {
                **item,
                "invoke": invocations[item["harness"]],
                "activation": activation[item["harness"]],
            }
            for item in actions
        ],
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
