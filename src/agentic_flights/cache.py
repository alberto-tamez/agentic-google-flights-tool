from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentic_flights.models import SCHEMA_VERSION, FlightOption, SearchCoverage, SearchSpec

DEFAULT_NAMESPACE = "fli-0.10.0"


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
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(spec)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        record = CacheRecord(
            created_at=self.clock(),
            status=status,
            options=options,
            coverage=coverage or SearchCoverage(),
        )
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        os.replace(temporary, path)

    def _path(self, spec: SearchSpec) -> Path:
        return self.directory / f"{self.key(spec)}.json"
