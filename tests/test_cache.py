from __future__ import annotations

from datetime import timedelta

from conftest import make_option, make_spec

from agentic_flights.cache import FileCache
from agentic_flights.models import SearchCoverage


def test_cache_key_includes_every_search_criterion(tmp_path, future_date) -> None:
    base = make_spec("base", future_date)
    variants = [
        make_spec("x", future_date, origin="BCN", destination="MAD"),
        make_spec("x", future_date, destination="LHR"),
        make_spec("x", future_date - timedelta(days=1)),
        make_spec("x", future_date, return_date=future_date),
        make_spec("x", future_date, cabin="business"),
        make_spec("x", future_date, max_stops="any"),
        make_spec("x", future_date, adults=2),
        make_spec("x", future_date, overhead_cabin_bags=1),
        make_spec(
            "x",
            future_date,
            overhead_cabin_bags=1,
            require_overhead_cabin_bag=True,
        ),
        make_spec("x", future_date, currency="USD"),
        make_spec("x", future_date, language="en-US"),
        make_spec("x", future_date, country="US"),
        make_spec("x", future_date, max_results=4),
    ]
    cache = FileCache(tmp_path)
    keys = {cache.key(base), *(cache.key(spec) for spec in variants)}
    assert len(keys) == len(variants) + 1
    assert cache.key(base) == cache.key(base.model_copy(update={"request_id": "other"}))


def test_cache_namespace_isolates_providers(tmp_path, future_date) -> None:
    spec = make_spec("same", future_date)
    assert FileCache(tmp_path, namespace="fli").key(spec) != FileCache(
        tmp_path, namespace="browser"
    ).key(spec)


def test_cache_ttl_and_status_policy(tmp_path, future_date) -> None:
    now = [100.0]
    cache = FileCache(tmp_path, ttl_seconds=10, clock=lambda: now[0])
    spec = make_spec("ttl", future_date)
    cache.put(spec, "success", [make_option()])
    assert cache.get(spec) is not None
    now[0] = 111.0
    assert cache.get(spec) is None
    cache.put(spec, "error", [])
    assert cache.get(spec) is None


def test_cache_preserves_original_exploration_coverage(tmp_path, future_date) -> None:
    cache = FileCache(tmp_path)
    spec = make_spec("coverage", future_date)
    coverage = SearchCoverage(
        candidates_seen=6,
        branches_attempted=4,
        quotes_completed=3,
        branch_errors=1,
        branch_errors_by_code={"timeout": 1},
        browser_transitions=17,
    )
    cache.put(spec, "success", [make_option()], coverage)
    record = cache.get(spec)
    assert record is not None
    assert record.coverage == coverage
