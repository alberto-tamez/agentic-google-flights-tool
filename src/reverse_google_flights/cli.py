from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from reverse_google_flights.batch import BatchExecutor
from reverse_google_flights.cache import FileCache
from reverse_google_flights.filtering import ShortlistSpec
from reverse_google_flights.models import SearchSpec
from reverse_google_flights.provider import BrowserProvider, FliProvider
from reverse_google_flights.store import ManagedStore, StoreError
from reverse_google_flights.views import compact_summary, list_page, load_report, show_results


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    searches: list[SearchSpec] = Field(min_length=1)
    max_workers: int = Field(default=2, ge=1)
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    ranking_limit: int = Field(default=10, ge=1)
    provider: str = "browser"


def run(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Any = None,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "init-skill":
        return _run_init_skill(arguments[1:])
    if arguments and arguments[0] == "guide":
        return _run_guide(arguments[1:])
    if arguments and arguments[0] == "filter":
        return _run_filter(arguments[1:])
    if arguments and arguments[0] == "summary":
        return _run_summary(arguments[1:])
    if arguments and arguments[0] == "list":
        return _run_list(arguments[1:])
    if arguments and arguments[0] == "show":
        return _run_show(arguments[1:])
    parser = argparse.ArgumentParser(
        prog="agentic-flights",
        description="Search Google Flights in bounded batches.",
        epilog=(
            "Start with: agentic-flights guide. Install an agent skill with init-skill. "
            "Other commands: filter, summary, list, show. "
            "Run agentic-flights COMMAND --help for details."
        ),
    )
    parser.add_argument("input", nargs="?", default="-", help="JSON file, or - for stdin")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="provider cache directory; defaults to the app-managed cache",
    )
    parser.add_argument("--output", type=Path, help="save the full JSON report to this file")
    parser.add_argument("--full", action="store_true", help="write the full report to stdout")
    args = parser.parse_args(arguments)
    try:
        text = (
            sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        )
        raw = json.loads(text)
        if isinstance(raw, list):
            raw = {"searches": raw}
        batch_input = BatchInput.model_validate(raw)
        providers = {"browser": BrowserProvider, "fli": FliProvider}
        if batch_input.provider not in providers:
            raise ValueError("provider must be 'browser' or 'fli'")
        selected_factory = provider_factory or providers[batch_input.provider]
        namespace = getattr(selected_factory, "version", batch_input.provider)
        store = ManagedStore()
        if args.cache_dir is None:
            store.initialize()
            cache_root = store.provider_cache
        else:
            cache_root = args.cache_dir
        cache = FileCache(
            cache_root / batch_input.provider,
            ttl_seconds=batch_input.cache_ttl_seconds,
            namespace=namespace,
        )
        kwargs: dict[str, Any] = {}
        kwargs["provider_factory"] = selected_factory
        report = BatchExecutor(
            cache,
            max_workers=batch_input.max_workers,
            ranking_limit=batch_input.ranking_limit,
            **kwargs,
        ).execute(batch_input.searches)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        error = {"error": {"code": "invalid_input", "message": str(exc)}}
        print(json.dumps(error), file=sys.stderr)
        return 2
    report_json = report.model_dump_json(indent=2) + "\n"
    run_id = None
    if args.output:
        artifact = args.output
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(report_json, encoding="utf-8")
        reference = str(artifact)
    else:
        run_id, artifact = store.save(report_json)
        reference = run_id
    if args.full:
        print(report_json, end="")
    else:
        checksum = hashlib.sha256(report_json.encode()).hexdigest()
        manifest = compact_summary(report, checksum, artifact, reference=reference)
        if run_id:
            manifest["managed_store"] = {
                "run_id": run_id,
                "idle_ttl_days": 7,
                "max_runs": 50,
                "max_bytes": 100 * 1024 * 1024,
            }
        print(json.dumps(manifest, indent=2))
    return 0


def _run_init_skill(argv: Sequence[str]) -> int:
    from reverse_google_flights.skill_init import install_skill

    parser = argparse.ArgumentParser(
        prog="agentic-flights init-skill",
        description="Install the Agentic Flights skill for Codex, Claude Code, or both.",
    )
    parser.add_argument("--harness", choices=["codex", "claude", "both"], default="both")
    parser.add_argument("--scope", choices=["project", "user"], default="project")
    parser.add_argument(
        "--project-dir",
        type=Path,
        help="project receiving the skill; defaults to the current directory",
    )
    parser.add_argument("--force", action="store_true", help="replace a different existing skill")
    parser.add_argument("--dry-run", action="store_true", help="show destinations without writing")
    args = parser.parse_args(argv)
    try:
        result = install_skill(
            args.harness,
            args.scope,
            project_dir=args.project_dir,
            force=args.force,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps({"error": {"code": "skill_install_failed", "message": str(exc)}}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _run_guide(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentic-flights guide")
    parser.add_argument(
        "topic",
        nargs="?",
        default="start",
        choices=[
            "start",
            "reference",
            "schema",
            "space",
            "search",
            "filters",
            "operations",
            "plan",
            "explore",
            "compare",
            "alternatives",
            "inspect",
            "verify",
            "issues",
        ],
    )
    args = parser.parse_args(argv)
    if args.topic not in {"start", "reference", "schema"}:
        from reverse_google_flights.api import AgentAPI

        print(json.dumps(AgentAPI().schema(args.topic), indent=2))
    elif args.topic == "schema":
        print(json.dumps(BatchInput.model_json_schema(), indent=2))
    else:
        name = "reference.md" if args.topic == "reference" else "agent-guide.md"
        resource = files("reverse_google_flights").joinpath("_docs", name)
        if resource.is_file():
            print(resource.read_text(encoding="utf-8"))
        else:
            # Source checkouts use the same canonical files that the wheel bundles.
            print((Path(__file__).resolve().parents[2] / "docs" / name).read_text())
    return 0


def _run_filter(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="reverse-google-flights filter",
        description="Filter and rank a saved flight report without network access.",
    )
    parser.add_argument("source", help="managed run ID or saved batch report JSON")
    parser.add_argument("filters", help="shortlist filter JSON file, or - for stdin")
    parser.add_argument("--output", type=Path, help="write the shortlist JSON to this file")
    args = parser.parse_args(argv)
    try:
        source_path, reference = ManagedStore().resolve(args.source)
        source, checksum = load_report(source_path)
        filters = ShortlistSpec.model_validate_json(_read_text(args.filters))
        report = list_page(
            source,
            checksum,
            source_path,
            filters,
            page_size=filters.limit,
            cursor=None,
            reference=reference,
        )
        report_json = json.dumps(report, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        error = {"error": {"code": "invalid_filter_input", "message": str(exc)}}
        print(json.dumps(error), file=sys.stderr)
        return 2


def _run_summary(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="reverse-google-flights summary")
    parser.add_argument("source")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    try:
        source_path, reference = ManagedStore().resolve(args.source)
        source, checksum = load_report(source_path)
        if args.full:
            print(source_path.read_text(encoding="utf-8"), end="")
        else:
            print(
                json.dumps(
                    compact_summary(source, checksum, source_path, reference=reference),
                    indent=2,
                )
            )
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        return _print_view_error(exc)


def _run_list(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="reverse-google-flights list")
    parser.add_argument("source")
    parser.add_argument("--filters", help="filter JSON file, or - for stdin")
    parser.add_argument("--page-size", type=int, default=5)
    parser.add_argument("--cursor")
    args = parser.parse_args(argv)
    try:
        source_path, reference = ManagedStore().resolve(args.source)
        source, checksum = load_report(source_path)
        filters = (
            ShortlistSpec.model_validate_json(_read_text(args.filters))
            if args.filters
            else ShortlistSpec()
        )
        report = list_page(
            source,
            checksum,
            source_path,
            filters,
            page_size=args.page_size,
            cursor=args.cursor,
            reference=reference,
        )
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        return _print_view_error(exc)


def _run_show(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="reverse-google-flights show")
    parser.add_argument("source")
    parser.add_argument("result_ids", nargs="+")
    args = parser.parse_args(argv)
    try:
        source_path, reference = ManagedStore().resolve(args.source)
        source, checksum = load_report(source_path)
        print(
            json.dumps(
                show_results(
                    source,
                    checksum,
                    source_path,
                    args.result_ids,
                    reference=reference,
                ),
                indent=2,
            )
        )
        return 0
    except (OSError, ValidationError, ValueError) as exc:
        return _print_view_error(exc)


def _print_view_error(exc: Exception) -> int:
    code = exc.code if isinstance(exc, StoreError) else "invalid_view_request"
    print(
        json.dumps({"error": {"code": code, "message": str(exc)}}),
        file=sys.stderr,
    )
    return 2


def _read_text(source: str) -> str:
    return sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
