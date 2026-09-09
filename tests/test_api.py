from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from conftest import make_api, make_option, make_spec

from agentic_flights import AgentAPI, BatchExecutor, SearchSpace
from agentic_flights.cache import FileCache
from agentic_flights.models import (
    BaggageAllowance,
    FlightLeg,
    FlightSegmentIdentity,
    SearchCoverage,
    SearchOutcome,
)
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


def test_exact_search_starts_from_compact_in_memory_dicts_and_resumes(tmp_path):
    seen = []

    class Provider:
        def search(self, spec):
            seen.append(spec)
            return ProviderResult(
                "success", [make_option()], 1, SearchCoverage(fully_explored=True)
            )

    api = make_api(tmp_path, Provider)
    searches = [
        {
            "origin": "MAD",
            "destination": destination,
            "departure_date": DAY.isoformat(),
            "currency": "EUR",
            "language": "en-US",
            "country": "ES",
        }
        for destination in ("LHR", "CDG")
    ]
    first = api.start(searches, work_chunk=1)
    assert first["progress"]["remaining_queries"] == 1
    assert seen[0].request_id == "0" and seen[0].search_mode == "discover"

    second = api.explore(first["run_id"], work_chunk=1)
    assert second["progress"]["remaining_queries"] == 0
    assert [spec.request_id for spec in seen] == ["0", "1"]

    single = api.start(searches[0])
    assert single["progress"]["remaining_queries"] == 0
    with pytest.raises(ValueError, match="at least one"):
        api.start([])


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


@pytest.mark.parametrize("observed_number", ["3010", None, "9999"])
def test_verified_ids_are_precise_and_duplicates_do_not_repeat(tmp_path, observed_number):
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
            match.legs[0].flight_number = observed_number
            other = match.model_copy(deep=True, update={"provider_rank": 2})
            other.legs[0].departure_at = other.legs[0].departure_at.replace(hour=12)
            return ProviderResult("success", [match, other], 1, SearchCoverage(fully_explored=True))

    api = make_api(tmp_path, Provider)
    run = save(api, [target])
    selected = api.compare(run)["results"][0]["result_id"]
    verified = api.verify(run, [selected, selected])
    assert len(calls) == 1 and verified["counts"]["total"] == 1
    ids = verified["verification"][0]["matching_result_ids"]
    if observed_number == "9999":
        assert ids == []
        assert verified["verification"][0]["status"] == "not_matched"
        return
    assert len(ids) == 1
    quote = api.inspect(verified["run_id"], ids)["results"][0]["option"]
    assert datetime.fromisoformat(quote["legs"][0]["departure_at"]) == target.legs[0].departure_at
    assert any(
        a["operation"] == "inspect" and a["arguments"]["result_ids"] == ids
        for a in verified["next_actions"]
    )


@pytest.mark.parametrize("journeys", [2, 3])
@pytest.mark.parametrize(
    "outbound_only,changed_return", [(True, True), (False, True), (False, False)]
)
def test_return_changes_cannot_be_presented_as_a_whole_itinerary_match(
    tmp_path, journeys, outbound_only, changed_return
):
    from agentic_flights.mcp_server import ToolResponse

    airports = ["MAD", "BCN", "MAD"] if journeys == 2 else ["MAD", "BCN", "LHR", "MAD"]
    full = make_option(200).model_copy(update={
        "ticket_scope": "complete_single_ticket", "price_provenance": "provider_final_total",
        "result_scope": "complete_itinerary",
    })
    full.legs = [
        full.legs[0].model_copy(update={
            "journey_index": index, "origin": origin, "destination": destination,
            "departure_at": datetime.combine(DAY + timedelta(days=index * 3), time(8)),
            "arrival_at": datetime.combine(DAY + timedelta(days=index * 3), time(10)),
        })
        for index, (origin, destination) in enumerate(zip(airports[:-1], airports[1:], strict=True))
    ]
    selected = full.model_copy(deep=True)
    if outbound_only:
        selected.legs = selected.legs[:1]
        selected.result_scope = "outbound_choice"
        selected.ticket_scope = "partial_or_unknown"
        selected.price_provenance = "provider_search_result"
    fresh = full.model_copy(deep=True)
    if changed_return:
        fresh.legs[-1].departure_at += timedelta(hours=1)
        fresh.legs[-1].arrival_at += timedelta(hours=1)

    class Provider:
        def search(self, spec):
            return ProviderResult("success", [fresh], 1, SearchCoverage(fully_explored=True))

    api = make_api(tmp_path, Provider)
    spec = make_spec("q", DAY, additional_segments=[
        {"origin": leg.origin, "destination": leg.destination,
         "departure_date": leg.departure_at.date()}
        for leg in full.legs[1:]
    ])
    original = save(api, [selected], spec)
    selected_id = api.compare(original)["results"][0]["result_id"]
    verified = api.verify(original, [selected_id])
    match = verified["verification"][0]
    expected_match = outbound_only or not changed_return
    assert bool(match["matching_result_ids"]) is expected_match
    assert match["status"] == ("matched_observed_itinerary" if expected_match else "not_matched")
    assert match["match_scope"] == ("outbound" if outbound_only else "whole_itinerary")
    assert match["whole_itinerary_matched"] is (not outbound_only and not changed_return)
    assert match["uncompared_journey_indexes"] == (
        list(range(1, journeys)) if outbound_only else []
    )
    if outbound_only:
        assert "Return/onward flights were not compared" in match["evidence"]
    compared = api.compare(verified["run_id"])
    fresh_id = compared["results"][0]["result_id"]
    for response in [verified, compared, api.inspect(verified["run_id"], [fresh_id]),
                     api.issues(verified["run_id"])]:
        assert response["verification"][0] == match
        serialized = ToolResponse.model_validate(response).model_dump()
        assert serialized["verification"][0]["uncompared_journey_indexes"] == match[
            "uncompared_journey_indexes"
        ]


@pytest.mark.parametrize(
    "segment_state,expected_status",
    [
        ("matching", "matched_observed_itinerary"),
        ("changed", "not_matched"),
        ("missing", "insufficient_detail"),
    ],
)
def test_verification_uses_connection_identity_from_the_booking_provider(
    tmp_path, segment_state, expected_status
):
    outbound = make_option(280, stops=1).model_copy(
        update={"result_scope": "outbound_choice"}
    )
    outbound.legs = [
        FlightLeg(
            journey_index=0,
            airline_code="LO",
            airline_name="LOT",
            flight_number=number,
            origin=origin,
            destination=destination,
            departure_at=departure,
            arrival_at=arrival,
            duration_minutes=int((arrival - departure).total_seconds() // 60),
        )
        for origin, destination, number, departure, arrival in [
            (
                "MAD",
                "WAW",
                "434",
                datetime(2027, 1, 14, 15, 30),
                datetime(2027, 1, 14, 19, 5),
            ),
            (
                "WAW",
                "TBS",
                "725",
                datetime(2027, 1, 14, 22, 25),
                datetime(2027, 1, 15, 4, 20),
            ),
        ]
    ]
    quoted = outbound.model_copy(
        deep=True,
        update={
            "result_scope": "complete_itinerary",
            "ticket_scope": "complete_single_ticket",
            "price_provenance": "provider_final_total",
            "stops": 2,
        },
    )
    quoted.legs = [
        FlightLeg(
            journey_index=0,
            airline_name="LOT",
            origin="MAD",
            destination="TBS",
            departure_at=outbound.legs[0].departure_at,
            arrival_at=outbound.legs[-1].arrival_at,
            duration_minutes=770,
        ),
        FlightLeg(
            journey_index=1,
            airline_name="LOT",
            origin="TBS",
            destination="MAD",
            departure_at=datetime(2027, 1, 17, 5, 20),
            arrival_at=datetime(2027, 1, 17, 14, 35),
            duration_minutes=555,
        ),
    ]
    if segment_state != "missing":
        quoted.identity_segments = [
            FlightSegmentIdentity(
                journey_index=index,
                origin=origin,
                destination=destination,
                departure_date=departure,
                airline_code="LO",
                flight_number=number,
            )
            for index, origin, destination, departure, number in [
                (0, "MAD", "WAW", DAY, "434"),
                (0, "WAW", "TBS", DAY, "999" if segment_state == "changed" else "725"),
                (1, "TBS", "WAW", date(2027, 1, 17), "724"),
                (1, "WAW", "MAD", date(2027, 1, 17), "433"),
            ]
        ]

    class Provider:
        def search(self, spec):
            return ProviderResult(
                "success", [quoted], 1, SearchCoverage(fully_explored=True)
            )

    api = make_api(tmp_path, Provider)
    original = save(
        api,
        [outbound],
        make_spec("q", DAY, destination="TBS", return_date=date(2027, 1, 17)),
    )
    selected = api.compare(original)["results"][0]["result_id"]
    verified = api.verify(original, [selected])
    match = verified["verification"][0]

    assert match["status"] == expected_status
    assert bool(match["matching_result_ids"]) is (segment_state == "matching")
    assert match["identity_basis"] == (
        "provider_segments" if segment_state != "missing" else "journey_summary"
    )
    assert verified["evidence_status"]["complete_quotes_found"] == 1
    assert verified["evidence_status"]["matched_complete_quotes"] == (
        1 if segment_state == "matching" else 0
    )
    assert verified["preview"][0]["selection_verification"] == (
        "matched_selection" if segment_state == "matching" else "other_complete_quote"
    )


def test_alternatives_keep_a_later_return_with_more_destination_time(tmp_path):
    early = make_option(348, duration=1200, stops=2).model_copy(
        update={
            "ticket_scope": "complete_single_ticket",
            "price_provenance": "provider_final_total",
        }
    )
    early.legs = [
        early.legs[0].model_copy(
            update={
                "journey_index": 0,
                "origin": "VLC",
                "destination": "TBS",
                "departure_at": datetime(2027, 1, 14, 11, 55),
                "arrival_at": datetime(2027, 1, 15, 1, 40),
            }
        ),
        early.legs[0].model_copy(
            update={
                "journey_index": 1,
                "origin": "TBS",
                "destination": "VLC",
                "departure_at": datetime(2027, 1, 17, 4, 40),
                "arrival_at": datetime(2027, 1, 17, 10, 55),
            }
        ),
    ]
    later = early.model_copy(deep=True, update={"duration_minutes": 1355, "provider_rank": 2})
    later.legs[1].departure_at = datetime(2027, 1, 17, 8, 40)
    later.legs[1].arrival_at = datetime(2027, 1, 17, 17, 30)

    api = make_api(tmp_path)
    run = save(
        api,
        [early, later],
        make_spec("q", DAY, destination="TBS", return_date=date(2027, 1, 17)),
    )
    alternatives = api.alternatives(run, {"currency": "EUR"})
    listed = api.compare(run, {"currency": "EUR"}, page_size=2)["results"]

    assert alternatives["total_alternatives"] == 2
    assert {item["journeys"][1]["departure_at"][11:16] for item in listed} == {
        "04:40",
        "08:40",
    }
    assert listed[1]["destination_stay_minutes"] > listed[0]["destination_stay_minutes"]


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
