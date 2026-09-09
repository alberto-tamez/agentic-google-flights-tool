from __future__ import annotations

import logging
import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue
from threading import Event, Lock
from time import monotonic

from agentic_flights.cache import FileCache
from agentic_flights.models import (
    BatchCounts,
    BatchReport,
    RankedFlight,
    SearchError,
    SearchOutcome,
    SearchSpec,
)
from agentic_flights.provider import Provider, ProviderError, SmartProvider
from agentic_flights.providers.budget import consumed_requests, query_budget

ProviderFactory = Callable[[], Provider]


class BatchExecutor:
    def __init__(
        self,
        cache: FileCache,
        *,
        max_workers: int = 2,
        ranking_limit: int = 10,
        provider_factory: ProviderFactory = SmartProvider,
        query_timeout_seconds: float = 60,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if (
            isinstance(ranking_limit, bool)
            or not isinstance(ranking_limit, int)
            or ranking_limit < 1
        ):
            raise ValueError("ranking_limit must be a positive integer")
        self.cache = cache
        self.max_workers = max_workers
        self.ranking_limit = ranking_limit
        self.provider_factory = provider_factory
        if not math.isfinite(query_timeout_seconds) or query_timeout_seconds <= 0:
            raise ValueError("query_timeout_seconds must be finite and positive")
        self.query_timeout_seconds = query_timeout_seconds

    def execute(
        self, searches: Iterable[SearchSpec], *,
        on_progress: Callable[[BatchReport, dict], None] | None = None,
        work_chunk: int | None = None,
        chunk_seconds: float | None = None,
    ) -> BatchReport:
        if work_chunk is not None and work_chunk < 1:
            raise ValueError("work_chunk must be positive")
        if chunk_seconds is not None and (not math.isfinite(chunk_seconds) or chunk_seconds <= 0):
            raise ValueError("chunk_seconds must be finite and positive")
        specs = list(searches)
        grouped: OrderedDict[str, list[tuple[int, SearchSpec]]] = OrderedDict()
        for index, spec in enumerate(specs):
            grouped.setdefault(self.cache.key(spec), []).append((index, spec))

        outcomes: list[SearchOutcome | None] = [None] * len(specs)
        misses: list[tuple[str, SearchSpec]] = []
        for key, members in grouped.items():
            representative = members[0][1]
            cached = self.cache.get(representative)
            if cached is None:
                misses.append((key, representative))
                continue
            for index, spec in members:
                outcomes[index] = SearchOutcome(
                    request_id=spec.request_id,
                    search_spec=spec,
                    status=cached.status,
                    options=cached.options,
                    cached=True,
                    elapsed_ms=0,
                    requests_made=0,
                    coverage=cached.coverage,
                )

        lock = Lock()
        stop = Event()
        active: set[str] = set()
        deadline = None if chunk_seconds is None else monotonic() + chunk_seconds

        def notify(event, key=None, spec=None, outcome=None):
            with lock:
                if event == "query_started":
                    active.add(spec.request_id)
                elif outcome is not None:
                    active.discard(spec.request_id)
                    for offset, (index, member) in enumerate(grouped[key]):
                        outcomes[index] = outcome.model_copy(update={
                            "request_id": member.request_id, "search_spec": member,
                            "requests_made": outcome.requests_made if offset == 0 else 0,
                        })
                if on_progress is not None:
                    partial = self.summarize(o for o in outcomes if o is not None)
                    on_progress(partial, {
                        "event": event, "request_id": None if spec is None else spec.request_id,
                        "active_request_ids": sorted(active), "total_queries": len(specs),
                        "query_status": None if outcome is None else outcome.status,
                        "error_code": None if outcome is None or outcome.error is None
                        else outcome.error.code,
                    })

        notify("started")
        pending: SimpleQueue[tuple[str, SearchSpec]] = SimpleQueue()
        for item in misses[:work_chunk]:
            pending.put(item)
        if misses:
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(misses)),
                thread_name_prefix="reverse-flights",
            ) as executor:
                workers = [
                    executor.submit(self._run_worker, pending, notify, stop, deadline)
                    for _ in range(min(self.max_workers, len(misses)))
                ]
                try:
                    for worker in workers:
                        while True:
                            try:
                                worker.result(timeout=5)
                                break
                            except TimeoutError:
                                if worker.done():
                                    raise
                                notify("heartbeat")
                finally:
                    stop.set()

        resolved = [outcome for outcome in outcomes if outcome is not None]
        report = self.summarize(resolved)
        report.counts.unique_searches = len({self.cache.key(o.search_spec) for o in resolved})
        return report

    def summarize(self, outcomes: Iterable[SearchOutcome]) -> BatchReport:
        """Build a report from saved outcomes without making provider requests."""
        resolved = list(outcomes)
        counts = BatchCounts(
            total=len(resolved),
            success=sum(outcome.status == "success" for outcome in resolved),
            empty=sum(outcome.status == "empty" for outcome in resolved),
            error=sum(outcome.status == "error" for outcome in resolved),
            unique_searches=len(resolved),
            network_requests=sum(outcome.requests_made for outcome in resolved),
            cache_hits=sum(outcome.cached for outcome in resolved),
        )
        return BatchReport(
            outcomes=resolved,
            counts=counts,
            ranked_by_currency=self._rank(resolved),
        )

    def _run_worker(self, pending, notify, stop, deadline) -> dict[str, SearchOutcome]:
        completed: dict[str, SearchOutcome] = {}
        provider: Provider | None = None
        try:
            while True:
                if stop.is_set() or (deadline is not None and monotonic() >= deadline):
                    break
                try:
                    key, spec = pending.get_nowait()
                except Empty:
                    break
                notify("query_started", key, spec)
                if provider is None:
                    try:
                        provider = self.provider_factory()
                    except Exception:
                        # _search_one translates construction failures into per-query errors.
                        pass
                outcome = self._search_one(spec, provider)
                completed[key] = outcome
                if outcome.status in {"success", "empty"}:
                    self.cache.put(spec, outcome.status, outcome.options, outcome.coverage)
                notify("query_completed", key, spec, outcome)
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Provider cleanup failed: %s", type(exc).__name__
                        )
        return completed

    def _search_one(self, spec: SearchSpec, provider: Provider | None = None) -> SearchOutcome:
        started = monotonic()
        try:
            with query_budget(self.query_timeout_seconds):
                selected_provider = provider if provider is not None else self.provider_factory()
                result = selected_provider.search(spec)
            return SearchOutcome(
                request_id=spec.request_id,
                search_spec=spec,
                status=result.status,
                options=result.options,
                elapsed_ms=_elapsed_ms(started),
                requests_made=result.requests_made,
                coverage=result.coverage,
            )
        except ProviderError as exc:
            return SearchOutcome(
                request_id=spec.request_id,
                search_spec=spec,
                status="error",
                error=SearchError(
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    details=exc.details,
                ),
                elapsed_ms=_elapsed_ms(started),
                requests_made=max(exc.requests_made, consumed_requests()),
                coverage=exc.coverage,
            )
        except Exception as exc:
            return SearchOutcome(
                request_id=spec.request_id,
                search_spec=spec,
                status="error",
                error=SearchError(
                    code="internal_error",
                    message=str(exc) or type(exc).__name__,
                    details={"exception_type": type(exc).__name__},
                ),
                elapsed_ms=_elapsed_ms(started),
                requests_made=0,
            )

    def _rank(self, outcomes: list[SearchOutcome]) -> dict[str, list[RankedFlight]]:
        grouped: dict[str, list[RankedFlight]] = {}
        for outcome in outcomes:
            if outcome.status != "success":
                continue
            for option in outcome.options:
                grouped.setdefault(option.currency, []).append(
                    RankedFlight(request_id=outcome.request_id, option=option)
                )
        for currency, flights in grouped.items():
            flights.sort(
                key=lambda ranked: (
                    ranked.option.price is None,
                    ranked.option.price if ranked.option.price is not None else float("inf"),
                    ranked.option.duration_minutes,
                    ranked.option.stops,
                    ranked.request_id,
                    ranked.option.provider_rank,
                )
            )
            grouped[currency] = flights[: self.ranking_limit]
        return {currency: grouped[currency] for currency in sorted(grouped)}


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
