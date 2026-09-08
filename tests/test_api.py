from __future__ import annotations

from datetime import date, datetime

import pytest
from conftest import make_api, make_option, make_spec

from agentic_flights import AgentAPI, BatchExecutor, SearchSpace
from agentic_flights.cache import FileCache
from agentic_flights.models import BaggageAllowance, SearchCoverage, SearchOutcome
from agentic_flights.provider import (
    ProviderResult,
)
from agentic_flights.store import ManagedStore

DAY = date(2027, 1, 14)


def save(api, options, spec=None):
    report = api.executor.summarize(
        [
            SearchOutcome(
                request_id="q",
                search_spec=spec or make_spec("q", DAY),
                status="success",
                options=options,
                elapsed_ms=0,
                requests_made=1,
                coverage=SearchCoverage(fully_explored=True),
            )
        ]
    )
    return api._save(report)["run_id"]


def test_code_workflow_round_robin_metadata_and_handle_resume(tmp_path):
    api = make_api(tmp_path)
    space = SearchSpace(
        template=make_spec("t", DAY, search_mode="discover"),
        origins=["MAD", "BCN"],
        destinations=["LHR", "CDG"],
        departure_start=DAY,
        departure_end=DAY,
        min_nights=7,
        max_nights=8,
    )
    first_routes = {(x.origin, x.destination) for x in list(space.searches())[:4]}
    assert len(first_routes) == 4
    plan = api.plan(space.model_dump(mode="json"))
    first = api.explore(plan["run_id"], work_chunk=4)
    restarted = AgentAPI(api.store, api.executor)
    second = restarted.explore(first["run_id"], work_chunk=4)
    assert second["search_space_exhausted"] and second["attempted"] == 8
    page = api.compare(second["run_id"])
    assert page["results"][0]["requested_return_date"]
    ids = [page["results"][0]["result_id"]]
    detail = api.inspect(second["run_id"], ids)
    assert detail["results"][0]["search_spec"]["return_date"]
    verified = api.verify(second["run_id"], ids, work_quotes=1)
    assert verified["verification"][0]["status"] == "matched_observed_itinerary"
    with pytest.raises(ValueError):
        api.inspect("/etc/passwd", ids)


def test_pending_queries_resume_without_resending_space(tmp_path):
    class Provider:
        def search(self, spec):
            done = bool(spec.continuation)
            return ProviderResult(
                "success",
                [make_option(50 if done else 100)],
                1,
                SearchCoverage(
                    fully_explored=done,
                    pending_branches=0 if done else 1,
                    continuation=None if done else {"test": "next"},
                ),
            )

    api = AgentAPI(
        ManagedStore(tmp_path / "s"),
        BatchExecutor(FileCache(tmp_path / "c"), provider_factory=Provider),
    )
    space = SearchSpace(
        template=make_spec("x", DAY),
        origins=["MAD"],
        destinations=["LHR"],
        departure_start=DAY,
        departure_end=DAY,
    )
    first = api.explore(api.plan(space.model_dump(mode="json"))["run_id"], work_chunk=1)
    assert (
        first["remaining"] == 0
        and first["pending_queries"] == 1
        and not first["search_space_exhausted"]
    )
    second = api.explore(first["run_id"], work_chunk=1)
    assert second["search_space_exhausted"]
    assert api.compare(second["run_id"])["results"][0]["price"] == 50


def test_blocked_plan_does_not_implicitly_repeat(tmp_path):
    calls = []

    class Provider:
        def search(self, spec):
            calls.append(spec.request_id)
            done = bool(spec.continuation)
            return ProviderResult(
                "success",
                [make_option()],
                1,
                SearchCoverage(
                    blocked=not done,
                    pending_branches=0 if done else 1,
                    continuation=None if done else {"next": True},
                    fully_explored=done,
                ),
            )

    api = AgentAPI(
        ManagedStore(tmp_path / "s"),
        BatchExecutor(FileCache(tmp_path / "c"), provider_factory=Provider),
    )
    space = SearchSpace(
        template=make_spec("x", DAY),
        origins=["MAD"],
        destinations=["LHR"],
        departure_start=DAY,
        departure_end=DAY,
    )
    first = api.explore(api.plan(space.model_dump(mode="json"))["run_id"])
    assert first["stop_reason"] == "blocked"
    paused = api.explore(first["run_id"])
    assert len(calls) == 1 and paused["stop_reason"] == "blocked"
    resumed = api.explore(paused["run_id"], retry_errors=True)
    assert len(calls) == 2 and resumed["search_space_exhausted"]


def test_partial_never_dominates_complete(tmp_path):
    api = make_api(tmp_path)
    partial = make_option(100).model_copy(update={"result_scope": "outbound_choice"})
    complete = make_option(200, duration=150).model_copy(
        update={
            "ticket_scope": "complete_single_ticket",
            "price_provenance": "provider_final_total",
            "provider_rank": 2,
        }
    )
    run = save(api, [partial, complete])
    assert len(api.alternatives(run)["result_ids"]) == 2
    page = api.compare(run)
    assert page["results"][0]["ticket_scope"] == "complete_single_ticket"
    assert page["results"][1]["result_scope"] == "outbound_choice"


def test_baggage_is_whole_requested_trip_and_airline_falls_back(tmp_path):
    api = make_api(tmp_path)
    option = make_option(100).model_copy(
        update={
            "result_scope": "outbound_choice",
            "baggage": BaggageAllowance(status="included", applies_to_journeys=[0]),
        }
    )
    option.legs[0].airline_name = None
    option.legs[0].airline_code = "IB"
    run = save(api, [option], make_spec("q", DAY, return_date=date(2027, 1, 21)))
    row = api.compare(run)["results"][0]
    assert row["airlines"] == ["IB"]
    assert row["baggage_status"] == "unknown" and row["baggage_evidence_scope"] == "partial"
    assert api.compare(run, {"require_overhead_cabin_bag": True})["results"] == []


def test_empty_data_operations_are_noops(tmp_path):
    calls = []

    class Empty:
        def search(self, s):
            calls.append(s.request_id)
            return ProviderResult("empty", [], 1, SearchCoverage(fully_explored=True))

    api = make_api(tmp_path, Empty)
    space = SearchSpace(
        template=make_spec("q", DAY),
        origins=["MAD"],
        destinations=["LHR"],
        departure_start=DAY,
        departure_end=DAY,
    )
    plan = api.plan(space.model_dump(mode="json"))
    explored = api.explore(plan["run_id"])
    run = explored["run_id"]
    assert explored["coverage_totals"]["fully_explored"]
    assert "summary" not in explored  # same top-level coverage location as verify
    assert api.inspect(run, [])["results"] == []
    assert api.verify(run, [])["selection_status"] == "empty"
    assert len(calls) == 1
    assert api.compare(run)["next_actions"] == []


def test_verified_ids_are_precise_and_duplicates_do_not_repeat(tmp_path):
    calls = []
    target = make_option(100)

    class Provider:
        def search(self, s):
            calls.append(s.request_id)
            match = target.model_copy(
                deep=True,
                update={
                    "ticket_scope": "complete_single_ticket",
                    "price_provenance": "provider_final_total",
                },
            )
            other = match.model_copy(deep=True, update={"provider_rank": 2})
            other.legs[0].departure_at = other.legs[0].departure_at.replace(hour=12)
            return ProviderResult("success", [match, other], 1, SearchCoverage(fully_explored=True))

    api = make_api(tmp_path, Provider)
    run = save(api, [target])
    selected = api.compare(run)["results"][0]["result_id"]
    verified = api.verify(run, [selected, selected])
    assert len(calls) == 1 and verified["counts"]["total"] == 1
    ids = verified["verification"][0]["matching_result_ids"]
    assert len(ids) == 1
    quote = api.inspect(verified["run_id"], ids)["results"][0]["option"]
    assert datetime.fromisoformat(quote["legs"][0]["departure_at"]) == target.legs[0].departure_at
    assert any(
        a["operation"] == "inspect" and a["arguments"]["result_ids"] == ids
        for a in verified["next_actions"]
    )


def test_errors_are_inspectable_without_full_dump(tmp_path):
    class Broken:
        def search(self, s):
            raise RuntimeError("test provider failure")

    api = make_api(tmp_path, Broken)
    space = SearchSpace(
        template=make_spec("q", DAY),
        origins=["MAD"],
        destinations=["LHR"],
        departure_start=DAY,
        departure_end=DAY,
    )
    run = api.explore(api.plan(space.model_dump(mode="json"))["run_id"])
    issues = api.issues(run["run_id"])
    assert issues["issues"][0]["error"]["message"] == "test provider failure"
    assert issues["progress"]["state"] == "needs_attention"
    assert issues["issues"][0]["query"]["origin"] == "MAD"


def test_executable_next_actions_keep_filters_and_cursors(tmp_path):
    api = make_api(tmp_path)
    options = [make_option(i, duration=100 - i, rank=i + 1) for i in range(10)]
    run = save(api, options)
    page = api.alternatives(run, {"currency": "EUR"}, page_size=2)
    assert page["total_alternatives"] == 10 and len(page["result_ids"]) == 2
    next_page = api.alternatives(**page["next_actions"][0]["arguments"])
    assert not set(page["result_ids"]) & set(next_page["result_ids"])
    with pytest.raises(ValueError, match="cursor"):
        api.alternatives(run, {"currency": "JPY"}, cursor=page["next_cursor"])
    prices = api.compare(run, {"currency": "EUR", "max_price": 4}, page_size=2)
    next_prices = api.compare(**prices["next_actions"][0]["arguments"])
    assert next_prices["total_matches"] == 5
    assert [x["price"] for x in next_prices["results"]] == [2, 3]
