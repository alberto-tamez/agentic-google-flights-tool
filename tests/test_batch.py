from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from conftest import make_option, make_spec

from agentic_flights.batch import BatchExecutor
from agentic_flights.cache import FileCache
from agentic_flights.provider import ProviderError, ProviderResult


class ConcurrencyTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.instances: list[int] = []

    def factory(self):
        tracker = self
        instance_id = len(self.instances)
        self.instances.append(instance_id)

        class FakeProvider:
            def search(self, spec):
                with tracker.lock:
                    tracker.active += 1
                    tracker.peak = max(tracker.peak, tracker.active)
                time.sleep(0.01)
                with tracker.lock:
                    tracker.active -= 1
                return ProviderResult("success", [make_option(price=100 + instance_id)], 1)

        return FakeProvider()


def test_fifty_items_are_bounded_and_isolated(tmp_path, future_date) -> None:
    tracker = ConcurrencyTracker()
    specs = [
        make_spec(
            f"req-{index:02d}",
            future_date + timedelta(days=index),
            destination="BCN" if index % 2 == 0 else "LHR",
        )
        for index in range(50)
    ]
    report = BatchExecutor(
        FileCache(tmp_path), max_workers=5, provider_factory=tracker.factory
    ).execute(specs)

    assert [item.request_id for item in report.outcomes] == [item.request_id for item in specs]
    assert tracker.peak == 5
    assert len(tracker.instances) == 5  # one reusable provider per worker
    assert report.counts.network_requests == 50


def test_deduplicates_criteria_while_preserving_request_ids(tmp_path, future_date) -> None:
    calls = []

    class FakeProvider:
        def search(self, spec):
            calls.append(spec.request_id)
            return ProviderResult("success", [make_option()], 1)

    specs = [make_spec(f"copy-{index}", future_date) for index in range(8)]
    report = BatchExecutor(FileCache(tmp_path), provider_factory=FakeProvider).execute(specs)

    assert calls == ["copy-0"]
    assert [item.request_id for item in report.outcomes] == [item.request_id for item in specs]
    assert [item.requests_made for item in report.outcomes] == [1, 0, 0, 0, 0, 0, 0, 0]
    assert report.counts.unique_searches == 1


def test_contains_partial_provider_errors(tmp_path, future_date) -> None:
    class FakeProvider:
        def search(self, spec):
            if spec.destination == "LHR":
                raise ProviderError("rate_limited", "blocked", retryable=True, requests_made=3)
            return ProviderResult("success", [make_option()], 1)

    report = BatchExecutor(FileCache(tmp_path), provider_factory=FakeProvider).execute(
        [
            make_spec("good", future_date),
            make_spec("bad", future_date, destination="LHR"),
            make_spec("also-good", future_date + timedelta(days=1)),
        ]
    )

    assert [item.status for item in report.outcomes] == ["success", "error", "success"]
    assert report.outcomes[1].error.code == "rate_limited"
    assert report.counts.network_requests == 5


def test_ranking_is_bounded_and_deterministic(tmp_path, future_date) -> None:
    prices = {"z": 90.0, "a": 90.0, "cheap": 20.0, "unknown": None}

    class FakeProvider:
        def search(self, spec):
            return ProviderResult("success", [make_option(prices[spec.request_id])], 1)

    specs = [
        make_spec("z", future_date),
        make_spec("unknown", future_date + timedelta(days=1)),
        make_spec("cheap", future_date + timedelta(days=2)),
        make_spec("a", future_date + timedelta(days=3)),
    ]
    report = BatchExecutor(
        FileCache(tmp_path), ranking_limit=3, provider_factory=FakeProvider
    ).execute(specs)
    assert [item.request_id for item in report.ranked_by_currency["EUR"]] == [
        "cheap",
        "a",
        "z",
    ]


def test_second_batch_is_served_from_cache(tmp_path, future_date) -> None:
    class FakeProvider:
        def search(self, spec):
            return ProviderResult("success", [make_option()], 1)

    cache = FileCache(tmp_path)
    executor = BatchExecutor(cache, provider_factory=FakeProvider)
    executor.execute([make_spec("first", future_date)])
    report = executor.execute([make_spec("second", future_date)])
    assert report.outcomes[0].cached is True
    assert report.counts.cache_hits == 1
    assert report.counts.network_requests == 0


def test_cache_replay_preserves_all_retrieved_options(tmp_path, future_date) -> None:
    class BroadProvider:
        def search(self, spec):
            return ProviderResult(
                "success",
                [make_option(price=100 + index, rank=index + 1) for index in range(12)],
                1,
            )

    cache = FileCache(tmp_path)
    executor = BatchExecutor(cache, provider_factory=BroadProvider)
    spec = make_spec("broad", future_date, max_results=3, retrieval_limit=20)
    fresh = executor.execute([spec])
    replay = executor.execute([spec.model_copy(update={"request_id": "cached"})])
    assert len(fresh.outcomes[0].options) == 12
    assert len(replay.outcomes[0].options) == 12
    assert replay.outcomes[0].cached is True


DAY = date(2027, 1, 14)


def test_worker_lifecycle_same_thread(tmp_path):
    opens, closes = [], []

    class Provider:
        def __init__(self):
            opens.append(threading.get_ident())

        def search(self, spec):
            return ProviderResult("success", [make_option()], 1)

        def close(self):
            closes.append(threading.get_ident())

    e = BatchExecutor(FileCache(tmp_path / "cache"), max_workers=1, provider_factory=Provider)
    e.execute([make_spec(str(i), date(2027, 1, 14 + i)) for i in range(4)])
    assert len(opens) == 1 and closes == opens


def test_cleanup_failure_does_not_discard_results(tmp_path, caplog):
    class Provider:
        def search(self, spec):
            return ProviderResult("success", [make_option()], 1)

        def close(self):
            raise RuntimeError("cleanup")

    report = BatchExecutor(FileCache(tmp_path / "c"), provider_factory=Provider).execute(
        [make_spec("x", DAY)]
    )
    assert report.counts.success == 1
    assert "cleanup failed" in caplog.text
