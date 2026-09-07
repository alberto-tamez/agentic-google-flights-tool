from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from time import monotonic

from reverse_google_flights.cache import FileCache
from reverse_google_flights.models import (
    BatchCounts,
    BatchReport,
    RankedFlight,
    SearchError,
    SearchOutcome,
    SearchSpec,
)
from reverse_google_flights.provider import BrowserProvider, Provider, ProviderError

ProviderFactory = Callable[[], Provider]


class BatchExecutor:
    def __init__(
        self,
        cache: FileCache,
        *,
        max_workers: int = 2,
        ranking_limit: int = 10,
        provider_factory: ProviderFactory = BrowserProvider,
    ) -> None:
        if not 1 <= max_workers <= 5:
            raise ValueError("max_workers must be between 1 and 5")
        if not 1 <= ranking_limit <= 50:
            raise ValueError("ranking_limit must be between 1 and 50")
        self.cache = cache
        self.max_workers = max_workers
        self.ranking_limit = ranking_limit
        self.provider_factory = provider_factory

    def execute(self, searches: Iterable[SearchSpec]) -> BatchReport:
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
                    status=cached.status,
                    options=cached.options,
                    cached=True,
                    elapsed_ms=0,
                    requests_made=0,
                    coverage=cached.coverage,
                )

        completed: dict[str, SearchOutcome] = {}
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="reverse-flights",
        ) as executor:
            futures: dict[Future[SearchOutcome], tuple[str, SearchSpec]] = {
                executor.submit(self._search_one, spec): (key, spec) for key, spec in misses
            }
            for future, (key, spec) in futures.items():
                outcome = future.result()
                completed[key] = outcome
                if outcome.status in {"success", "empty"}:
                    self.cache.put(spec, outcome.status, outcome.options, outcome.coverage)

        for key, members in grouped.items():
            source = completed.get(key)
            if source is None:
                continue
            for offset, (index, spec) in enumerate(members):
                outcomes[index] = source.model_copy(
                    update={
                        "request_id": spec.request_id,
                        "requests_made": source.requests_made if offset == 0 else 0,
                    }
                )

        resolved = [outcome for outcome in outcomes if outcome is not None]
        counts = BatchCounts(
            total=len(resolved),
            success=sum(outcome.status == "success" for outcome in resolved),
            empty=sum(outcome.status == "empty" for outcome in resolved),
            error=sum(outcome.status == "error" for outcome in resolved),
            unique_searches=len(grouped),
            network_requests=sum(outcome.requests_made for outcome in resolved),
            cache_hits=sum(outcome.cached for outcome in resolved),
        )
        return BatchReport(
            outcomes=resolved,
            counts=counts,
            ranked_by_currency=self._rank(resolved),
        )

    def _search_one(self, spec: SearchSpec) -> SearchOutcome:
        started = monotonic()
        try:
            result = self.provider_factory().search(spec)
            return SearchOutcome(
                request_id=spec.request_id,
                status=result.status,
                options=result.options,
                elapsed_ms=_elapsed_ms(started),
                requests_made=result.requests_made,
                coverage=result.coverage,
            )
        except ProviderError as exc:
            return SearchOutcome(
                request_id=spec.request_id,
                status="error",
                error=SearchError(
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    details=exc.details,
                ),
                elapsed_ms=_elapsed_ms(started),
                requests_made=exc.requests_made,
                coverage=exc.coverage,
            )
        except Exception as exc:
            return SearchOutcome(
                request_id=spec.request_id,
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
