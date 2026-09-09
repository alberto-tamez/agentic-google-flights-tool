"""Save each completed query and resume bounded CLI batches from ordinary run IDs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentic_flights.models import BatchReport, SearchSpec


class BatchSession:
    def __init__(self, executor, store, config, *, previous=None, output=None, progress=None,
                 state=None):
        self.executor = executor
        self.store = store
        self.config = config
        self.specs = [SearchSpec.model_validate(s) for s in config["searches"]]
        self.previous = self._by_key(previous.outcomes if previous else [])
        self.output: Path | None = output
        self.progress = progress
        self.state = state or {}
        self.report = previous or executor.summarize([])
        self.run_id: str | None = None
        self.path: Path | None = None

    def _key(self, spec):
        return self.executor.cache.key(spec.model_copy(update={"continuation": None}))

    def _by_key(self, outcomes):
        result = {}
        for outcome in outcomes:
            key = self._key(outcome.search_spec)
            if key not in result or outcome.requests_made > result[key].requests_made:
                result[key] = outcome
        return result

    def checkpoint(self, current: BatchReport, event: dict[str, Any]) -> None:
        by_key = dict(self.previous)
        for key, outcome in self._by_key(current.outcomes).items():
            old = self.previous.get(key)
            by_key[key] = outcome.model_copy(update={
                "requests_made": outcome.requests_made + (old.requests_made if old else 0)
            })
        outcomes = []
        seen = set()
        for spec in self.specs:
            key = self._key(spec)
            if key in by_key:
                outcome = by_key[key]
                outcomes.append(outcome.model_copy(update={
                    "request_id": spec.request_id,
                    "search_spec": outcome.search_spec.model_copy(
                        update={"request_id": spec.request_id}
                    ),
                    "requests_made": 0 if key in seen else outcome.requests_made,
                }))
                seen.add(key)
        self.report = self.executor.summarize(outcomes)
        self.report.counts.unique_searches = len(seen)
        self.report.exploration_state = {**self.state, "batch_input": self.config}
        encoded = self.report.model_dump_json(indent=2) + "\n"
        self.run_id, self.path = self.store.save(encoded)
        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.output.with_name(f".{self.output.name}.{os.getpid()}.tmp")
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, self.output)
        if self.progress is not None:
            self.progress({**event, **self.status()})

    def status(self) -> dict[str, Any]:
        remaining = len(self.specs) - len(self.report.outcomes)
        pending = sum(bool(o.coverage.continuation and not o.coverage.blocked)
                      for o in self.report.outcomes)
        return {
            "run_id": self.run_id,
            "completed_queries": len(self.report.outcomes), "total_queries": len(self.specs),
            "remaining_queries": remaining, "pending_queries": pending,
            "failed_queries": self.report.counts.error,
            "can_continue": bool(remaining or pending),
            "resume_command": f"agentic-flights resume {self.run_id}",
        }

    def execute(self, *, retry_errors: bool = False) -> BatchReport:
        pending, continuations = [], []
        for spec in self.specs:
            old = self.previous.get(self._key(spec))
            if old is None:
                pending.append(spec)
            elif (old.coverage.continuation and not old.coverage.blocked) or (
                retry_errors and old.error
            ):
                continuations.append(spec.model_copy(update={
                    "continuation": old.coverage.continuation
                }))
        self.executor.execute(
            [*pending, *continuations], on_progress=self.checkpoint,
            work_chunk=self.config["work_chunk"], chunk_seconds=self.config["chunk_seconds"],
        )
        return self.report
