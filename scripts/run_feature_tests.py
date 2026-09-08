#!/usr/bin/env python3
"""Run the randomized feature contract and retain replayable latency evidence."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "tdd"
sys.path.insert(0, str(ROOT / "tests"))
from feature_contract import FEATURES  # noqa: E402


def _write_feature_list() -> None:
    lines = [
        "# Agentic Flights feature contract",
        "",
        "This local file is intentionally ignored by git. Each capability below has one",
        "randomized property test; ordinary unit tests retain the detailed regressions.",
        "",
    ]
    lines.extend(f"- **{title}** — `{feature_id}`" for feature_id, title in FEATURES.items())
    lines.extend(
        [
            "",
            "Every run records its seed, generated failure example, per-feature latency,",
            "total latency, and change from the previous run. Latency is evidence, not an",
            "invented pass/fail threshold.",
            "",
        ]
    )
    (ARTIFACTS / "FEATURES.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, help="Replay a prior randomized run")
    args = parser.parse_args(argv)
    seed = args.seed if args.seed is not None else secrets.randbits(64)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    _write_feature_list()

    previous_path = ARTIFACTS / "latest.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = ARTIFACTS / "runs" / f"{stamp}-{seed}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/randomized",
        f"--hypothesis-seed={seed}",
        f"--feature-report={report_path}",
    ]
    started = monotonic()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    wall_seconds = round(monotonic() - started, 6)

    if report_path.exists():
        report = json.loads(report_path.read_text())
        report["seed"] = seed
        report["replay"] = f"{sys.executable} scripts/run_feature_tests.py --seed {seed}"
        report["latency_seconds"]["full_run"] = wall_seconds
        if previous:
            old = previous.get("latency_seconds", {}).get("by_feature", {})
            current = report["latency_seconds"]["by_feature"]
            report["latency_seconds"]["change_from_previous"] = {
                name: round(current[name] - old[name], 6)
                for name in current.keys() & old.keys()
            }
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        previous_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"seed": seed, **report["latency_seconds"]}, indent=2))
    if completed.returncode:
        print(f"Replay: {sys.executable} scripts/run_feature_tests.py --seed {seed}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
