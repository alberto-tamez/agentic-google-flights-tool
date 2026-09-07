from datetime import timedelta

import pytest
from conftest import make_option, make_spec

from reverse_google_flights import BatchExecutor, Exploration, SearchSpace
from reverse_google_flights.cache import FileCache
from reverse_google_flights.provider import ProviderError, ProviderResult
from reverse_google_flights.store import ManagedStore
from reverse_google_flights.views import load_report


def space_for(day, **updates):
    return SearchSpace.model_validate({
        "template": make_spec("template", day, search_mode="discover"),
        "origins": ["MAD"],
        "destinations": ["BCN", "LHR"],
        "departure_start": day,
        "departure_end": day + timedelta(days=1),
        **updates,
    })


def test_expansion_normalizes_deduplicates_and_counts_returns(future_date):
    space = space_for(
        future_date, origins=["mad", " MAD "], destinations=["MAD", "BCN", "bcn"],
        min_nights=7, max_nights=9,
    )
    specs = list(space.searches())
    assert space.count == len(specs) == 6
    assert {s.destination for s in specs} == {"BCN"}
    assert {(s.return_date - s.departure_date).days for s in specs} == {7, 8, 9}
    assert all(s.search_mode == "discover" for s in specs)
    assert [s.request_id for s in specs] == [str(i) for i in range(6)]


@pytest.mark.parametrize("updates", [
    {"min_nights": 3}, {"min_nights": 5, "max_nights": 2},
    {"destinations": ["MAD"]}, {"origins": ["bad-code"]},
    {"departure_end": "2000-01-01"},
    {"min_nights": 0, "max_nights": 365, "departure_end": "9999-12-31"},
])
def test_invalid_spaces(future_date, updates):
    with pytest.raises(ValueError):
        space_for(future_date, **updates)


def test_rejects_oversized_space_and_multi_city_template(future_date):
    with pytest.raises(ValueError, match="100000"):
        space_for(future_date, departure_end=future_date + timedelta(days=365),
                  min_nights=0, max_nights=365)
    with pytest.raises(ValueError, match="one-way template"):
        space_for(future_date, template=make_spec("t", future_date, return_date=future_date))


def test_resume_survives_new_instances_and_preserves_failures(tmp_path, future_date):
    calls = []

    class Provider:
        def search(self, spec):
            calls.append(spec.request_id)
            if spec.destination == "LHR":
                raise ProviderError("blocked", "try later", retryable=True, requests_made=1)
            return ProviderResult("success", [make_option()], 1)

    store = ManagedStore(tmp_path / "store")
    space = space_for(future_date)

    def runner():
        return Exploration(space, BatchExecutor(
            FileCache(tmp_path / "cache", namespace="test"), provider_factory=Provider,
        ), store)

    first = runner().advance(search_budget=1)
    assert (first.attempted, first.remaining, len(calls)) == (1, 3, 1)
    second = runner().advance(resume_from=first.run_id, search_budget=2)
    assert (second.attempted, second.remaining, second.failed, len(calls)) == (3, 1, 1, 3)
    final = runner().advance(resume_from=second.run_id, search_budget=1)
    assert final.search_space_exhausted
    assert (final.failed, final.remaining, len(calls)) == (2, 0, 4)
    repeat = runner().advance(resume_from=final.run_id)
    assert repeat.batch_network_requests == 0
    assert repeat.run_id == final.run_id
    assert len(calls) == 4
    old, _ = load_report(store.resolve(first.run_id)[0])
    assert len(old.outcomes) == 1
    report, _ = load_report(store.resolve(final.run_id)[0])
    assert len(report.outcomes) == report.counts.unique_searches == 4
    assert len(final.summary["preview"]) <= 5

    changed = space_for(future_date, destinations=["CDG"])
    with pytest.raises(ValueError, match="does not match"):
        Exploration(changed, runner().executor, store).advance(resume_from=first.run_id)
    other_executor = BatchExecutor(FileCache(tmp_path / "other", namespace="other"))
    with pytest.raises(ValueError, match="does not match"):
        Exploration(space, other_executor, store).advance(resume_from=first.run_id)


def test_budget_validation_before_execution(tmp_path, future_date):
    runner = Exploration(space_for(future_date), BatchExecutor(FileCache(tmp_path / "c")),
                         ManagedStore(tmp_path / "s"))
    for budget in [0, 501, True, 1.5]:
        with pytest.raises(ValueError, match="search_budget"):
            runner.advance(search_budget=budget)
