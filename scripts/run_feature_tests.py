#!/usr/bin/env python3
"""Run randomized feature tests with a replayable seed and optional JSON report."""

from __future__ import annotations

import argparse
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, help="Replay a prior randomized run")
    parser.add_argument("--report", type=Path, help="Write one JSON report to this path")
    args = parser.parse_args(argv)
    seed = args.seed if args.seed is not None else secrets.randbits(64)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/randomized",
        f"--hypothesis-seed={seed}",
    ]
    if args.report is not None:
        command.append(f"--feature-report={args.report.resolve()}")
    replay = shlex.join([sys.executable, "scripts/run_feature_tests.py", "--seed", str(seed)])
    print(f"Replay from repository root: {replay}", flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
