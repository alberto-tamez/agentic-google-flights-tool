from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentic_flights.models import SCHEMA_VERSION, FlightOption, SearchCoverage, SearchSpec

DEFAULT_NAMESPACE = "agentic-flights-v8"


class CacheRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: float
    status: str
    options: list[FlightOption]
    coverage: SearchCoverage = SearchCoverage()


class FileCache:
    def __init__(
        self,
        directory: Path,
        ttl_seconds: int = 3600,
        clock: Callable[[], float] = time.time,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.namespace = namespace

    def key(self, spec: SearchSpec) -> str:
        payload = {
            "provider_version": self.namespace,
            "schema_version": SCHEMA_VERSION,
            "criteria": spec.criteria(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, spec: SearchSpec) -> CacheRecord | None:
        path = self._path(spec)
        if self.directory.is_symlink() or path.is_symlink():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            record = CacheRecord.model_validate_json(raw)
        except (OSError, ValueError):
            return None
        age = self.clock() - record.created_at
        if age < 0 or age > self.ttl_seconds:
            return None
        if record.status not in {"success", "empty"}:
            return None
        if record.status == "success" and not record.options:
            return None
        if record.status == "empty" and record.options:
            return None
        if record.status == "empty" and not self._observation_is_complete(record.coverage):
            return None
        return record

    def put(
        self,
        spec: SearchSpec,
        status: str,
        options: list[FlightOption],
        coverage: SearchCoverage | None = None,
    ) -> None:
        if status not in {"success", "empty"}:
            return
        if status == "success" and not options:
            return
        if status == "empty" and options:
            return
        if status == "empty" and (
            coverage is None or not self._observation_is_complete(coverage)
        ):
            # An unexplained empty response may be a blocked or truncated provider page.
            return
        self._prepare_directory()
        path = self._path(spec)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("cache entry path is unsafe")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        record = CacheRecord(
            created_at=self.clock(),
            status=status,
            options=options,
            coverage=coverage or SearchCoverage(),
        )
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(record.model_dump_json())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, spec: SearchSpec) -> Path:
        return self.directory / f"{self.key(spec)}.json"

    def _prepare_directory(self) -> None:
        if self.directory.is_symlink():
            raise ValueError("cache directory cannot be a symlink")
        if self.directory.exists() and not self.directory.is_dir():
            raise ValueError("cache directory path is not a directory")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    @staticmethod
    def _observation_is_complete(coverage: SearchCoverage) -> bool:
        has_incomplete_marker = (
            coverage.source_truncated
            or coverage.blocked
            or coverage.budget_exhausted
            or coverage.continuation is not None
        )
        if has_incomplete_marker:
            return False
        return coverage.fully_explored
