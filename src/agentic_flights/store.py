from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RUN_ID = re.compile(r"^rgf_[0-9a-f]{16}$")
SENTINEL = ".owned-by-agentic-flights"
SENTINEL_TEXT = "agentic-flights managed store v1\n"


class StoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StorePolicy:
    ttl_seconds: int = 7 * 24 * 60 * 60
    max_runs: int = 50
    max_bytes: int = 100 * 1024 * 1024
    max_provider_cache_entries: int = 2_000
    max_route_cache_entries: int = 1_000


class ManagedStore:
    def __init__(self, root: Path | None = None, policy: StorePolicy | None = None) -> None:
        self.root = (root or _default_root()).expanduser()
        self.policy = policy or StorePolicy()
        self.runs = self.root / "runs"
        self.provider_cache = self.root / "provider-cache"
        self.route_cache = self.root / "route-cache"

    def initialize(self) -> None:
        if self.root.is_symlink():
            raise StoreError("unsafe_store", "managed store root cannot be a symlink")
        if self.root.exists() and not (self.root / SENTINEL).exists():
            if any(self.root.iterdir()):
                raise StoreError("unsafe_store", "managed store root has no ownership sentinel")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        sentinel = self.root / SENTINEL
        if sentinel.exists() and sentinel.is_symlink():
            raise StoreError("unsafe_store", "managed store sentinel cannot be a symlink")
        if not sentinel.exists():
            sentinel.write_text(SENTINEL_TEXT, encoding="utf-8")
            sentinel.chmod(0o600)
        elif sentinel.read_text(encoding="utf-8") != SENTINEL_TEXT:
            raise StoreError("unsafe_store", "managed store sentinel is invalid")
        self.root.chmod(0o700)
        for directory in (self.runs, self.provider_cache, self.route_cache):
            if directory.is_symlink():
                raise StoreError("unsafe_store", "managed store directories cannot be symlinks")
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)

    def save(self, report_json: str) -> tuple[str, Path]:
        self.initialize()
        encoded = report_json.encode()
        if len(encoded) > self.policy.max_bytes:
            raise StoreError("dataset_too_large", "dataset exceeds the managed store byte limit")
        run_id = "rgf_" + hashlib.sha256(encoded).hexdigest()[:16]
        run_dir = self.runs / run_id
        if run_dir.is_symlink():
            raise StoreError("unsafe_store", "managed run path cannot be a symlink")
        run_dir.mkdir(mode=0o700, exist_ok=True)
        report = run_dir / "report.json"
        temporary = run_dir / f".report.{os.getpid()}.{time.time_ns()}.tmp"
        temporary.write_bytes(encoded)
        temporary.chmod(0o600)
        os.replace(temporary, report)
        os.utime(report, None)
        self.cleanup(keep_run=run_id)
        return run_id, report

    def resolve(self, source: str) -> tuple[Path, str]:
        if RUN_ID.fullmatch(source):
            self.initialize()
            report = self.runs / source / "report.json"
            if report.is_symlink() or not report.is_file():
                raise StoreError("run_expired", f"managed run is missing or expired: {source}")
            if time.time() - report.stat().st_mtime > self.policy.ttl_seconds:
                self._delete_run(source)
                raise StoreError("run_expired", f"managed run is missing or expired: {source}")
            os.utime(report, None)
            return report, source
        path = Path(source).expanduser()
        if path.exists():
            return path, source
        raise StoreError("source_not_found", f"result source does not exist: {source}")

    def cleanup(self, *, keep_run: str | None = None) -> dict[str, int]:
        self.initialize()
        now = time.time()
        removed_runs = 0
        removed_cache = 0
        removed_route_cache = 0
        runs = self._valid_runs()
        for _, run_id, report in list(runs):
            if run_id != keep_run and now - report.stat().st_mtime > self.policy.ttl_seconds:
                removed_runs += self._delete_run(run_id)
        runs = self._valid_runs()
        for _, run_id, _ in runs[self.policy.max_runs :]:
            if run_id != keep_run:
                removed_runs += self._delete_run(run_id)

        cache_files = self._provider_cache_files()
        for modified, path in list(cache_files):
            if now - modified > self.policy.ttl_seconds:
                path.unlink(missing_ok=True)
                removed_cache += 1
        cache_files = self._provider_cache_files()
        for _, path in cache_files[self.policy.max_provider_cache_entries :]:
            path.unlink(missing_ok=True)
            removed_cache += 1

        route_cache_files = self._route_cache_files()
        for modified, path in list(route_cache_files):
            if now - modified > self.policy.ttl_seconds:
                path.unlink(missing_ok=True)
                removed_route_cache += 1
        route_cache_files = self._route_cache_files()
        for _, path in route_cache_files[self.policy.max_route_cache_entries :]:
            path.unlink(missing_ok=True)
            removed_route_cache += 1

        while self._managed_bytes() > self.policy.max_bytes:
            cache_files = self._provider_cache_files()
            route_cache_files = self._route_cache_files()
            oldest_cache = [
                *((modified, path, "provider") for modified, path in cache_files),
                *((modified, path, "route") for modified, path in route_cache_files),
            ]
            if oldest_cache:
                _, path, cache_kind = min(oldest_cache)
                path.unlink(missing_ok=True)
                if cache_kind == "provider":
                    removed_cache += 1
                else:
                    removed_route_cache += 1
                continue
            removable = [item for item in self._valid_runs() if item[1] != keep_run]
            if not removable:
                break
            removed_runs += self._delete_run(removable[-1][1])
        return {
            "runs": removed_runs,
            "provider_cache_entries": removed_cache,
            "route_cache_entries": removed_route_cache,
        }

    def _valid_runs(self) -> list[tuple[float, str, Path]]:
        if self.runs.is_symlink():
            raise StoreError("unsafe_store", "managed runs directory cannot be a symlink")
        found: list[tuple[float, str, Path]] = []
        for run_dir in self.runs.iterdir():
            if not RUN_ID.fullmatch(run_dir.name) or run_dir.is_symlink() or not run_dir.is_dir():
                continue
            report = run_dir / "report.json"
            if report.is_symlink() or not report.is_file():
                continue
            found.append((report.stat().st_mtime, run_dir.name, report))
        return sorted(found, reverse=True)

    def _provider_cache_files(self) -> list[tuple[float, Path]]:
        found: list[tuple[float, Path]] = []
        if self.provider_cache.is_symlink():
            raise StoreError("unsafe_store", "provider cache directory cannot be a symlink")
        for provider in self.provider_cache.iterdir():
            if provider.is_symlink() or not provider.is_dir():
                continue
            for path in provider.glob("*.json"):
                if path.is_symlink() or not path.is_file():
                    continue
                found.append((path.stat().st_mtime, path))
        return sorted(found, reverse=True)

    def _route_cache_files(self) -> list[tuple[float, Path]]:
        if self.route_cache.is_symlink():
            raise StoreError("unsafe_store", "route cache directory cannot be a symlink")
        found = [
            (path.stat().st_mtime, path)
            for path in self.route_cache.glob("*.json")
            if not path.is_symlink() and path.is_file()
        ]
        return sorted(found, reverse=True)

    def _delete_run(self, run_id: str) -> int:
        if not RUN_ID.fullmatch(run_id):
            return 0
        run_dir = self.runs / run_id
        if run_dir.is_symlink() or not run_dir.is_dir():
            return 0
        report = run_dir / "report.json"
        if report.is_symlink() or not report.is_file():
            return 0
        report.unlink()
        try:
            run_dir.rmdir()
        except OSError:
            return 0
        return 1

    def _managed_bytes(self) -> int:
        return sum(report.stat().st_size for _, _, report in self._valid_runs()) + sum(
            path.stat().st_size for _, path in self._provider_cache_files()
        ) + sum(path.stat().st_size for _, path in self._route_cache_files())


def _default_root() -> Path:
    configured = os.environ.get("AGENTIC_FLIGHTS_STORE")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "agentic-flights"
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "agentic-flights"
