from datetime import date

import pytest

from agentic_flights import (
    AgentAPI,
    BatchExecutor,
    FlightLeg,
    FlightOption,
    RouteGraph,
    SearchCoverage,
    StrategyRisk,
    build_strategy_plan,
    list_strategies,
)
from agentic_flights.cache import FileCache
from agentic_flights.cli import run
from agentic_flights.provider import ProviderResult
from agentic_flights.store import ManagedStore


def test_strategy_catalog_has_unique_ids_and_safe_defaults() -> None:
    strategies = list_strategies(include_contract_sensitive=True, include_unsupported=True)

    assert len({strategy.strategy_id for strategy in strategies}) == len(strategies)
    assert all(
        not strategy.default_enabled
        for strategy in strategies
        if strategy.risk in {StrategyRisk.CONTRACT_SENSITIVE, StrategyRisk.UNSUPPORTED}
    )


def test_ground_access_requires_a_complete_estimate() -> None:
    with pytest.raises(ValueError, match="ground access requires"):
        build_strategy_plan(
            {
                "origin": "MAD",
                "destination": "LHR",
                "departure_date": "2027-01-14",
                "currency": "EUR",
                "language": "en-US",
                "country": "ES",
            },
            nearby_origins=[{"airport": "VLC", "access_mode": "ground"}],
        )


def test_default_playbook_excludes_contract_sensitive_and_unsupported_tactics() -> None:
    response = AgentAPI().playbook()
    risks = {strategy["risk"] for strategy in response["strategies"]}

    assert "contract_sensitive" not in risks
    assert "unsupported" not in risks
    assert "positioning_gateway" in response["default_strategy_ids"]


def test_hidden_city_requires_opt_in_and_carry_on_only() -> None:
    response = AgentAPI().playbook(include_contract_sensitive=True)
    hidden_city = next(
        strategy
        for strategy in response["strategies"]
        if strategy["strategy_id"] == "hidden_city_final_leg"
    )

    assert hidden_city["default_enabled"] is False
    assert hidden_city["risk"] == "contract_sensitive"
    assert {"explicit_user_opt_in", "carry_on_only", "separate_return_reservation"} <= set(
        hidden_city["requirements"]
    )
    assert len(hidden_city["failure_modes"]) >= 4


def test_positioning_gateway_keeps_access_cost_and_time_separate() -> None:
    plan = build_strategy_plan(
        {
            "origin": "MTY",
            "destination": "MAD",
            "departure_date": "2027-01-14",
            "return_date": "2027-01-21",
            "currency": "EUR",
            "language": "es-MX",
            "country": "MX",
        },
        positioning_origins=[
            {
                "airport": "CUN",
                "access_mode": "separate_flight",
                "estimated_cost": 85,
                "estimated_minutes": 150,
                "minimum_buffer_minutes": 360,
            }
        ],
    )
    gateway = next(
        item for item in plan["hypotheses"] if item["strategy_id"] == "positioning_gateway"
    )

    assert gateway["searches"][0]["origin"] == "CUN"
    assert gateway["true_origin"] == "MTY"
    assert gateway["access_cost"] == 85
    assert gateway["minimum_buffer_minutes"] == 360
    assert gateway["separate_tickets"] is True
    assert plan["ordering"] == "unranked"


def test_route_graph_finds_positioning_gateways_without_city_rules() -> None:
    graph = {
        "source": "test schedule",
        "observed_at": "2026-09-09",
        "routes": [
            {"origin": "MEX", "destination": "CUN"},
            {"origin": "CUN", "destination": "MAD"},
            {"origin": "MEX", "destination": "JFK"},
            {"origin": "JFK", "destination": "LHR"},
        ],
    }
    plan = build_strategy_plan(
        {
            "origin": "MEX",
            "destination": "MAD",
            "departure_date": "2027-01-14",
            "return_date": "2027-01-21",
            "currency": "EUR",
            "language": "es-MX",
            "country": "MX",
        },
        route_graph=graph,
    )
    gateways = [
        item for item in plan["hypotheses"] if item["strategy_id"] == "positioning_gateway"
    ]

    assert [item["ticketed_origin"] for item in gateways] == ["CUN"]
    assert gateways[0]["access_cost"] is None
    assert gateways[0]["access_priced_by_search"] is True
    assert gateways[0]["component_roles"] == ["main_ticket", "positioning_ticket"]
    assert gateways[0]["searches"][1]["origin"] == "MEX"
    assert gateways[0]["searches"][1]["destination"] == "CUN"
    assert gateways[0]["searches"][1]["departure_date"] == "2027-01-13"
    assert gateways[0]["searches"][1]["return_date"] == "2027-01-22"
    assert plan["route_graph"] == {
        "source": "test schedule",
        "observed_at": "2026-09-09",
        "age_days": (date.today() - date(2026, 9, 9)).days,
    }


def test_route_graph_can_generate_opt_in_hidden_city_candidates() -> None:
    plan = build_strategy_plan(
        {
            "origin": "MEX",
            "destination": "MAD",
            "departure_date": "2027-01-14",
            "currency": "EUR",
            "language": "es-MX",
            "country": "MX",
            "overhead_cabin_bags": 1,
        },
        route_graph={
            "source": "test schedule",
            "observed_at": "2026-09-09",
            "routes": [
                {"origin": "MAD", "destination": "LIS"},
                {"origin": "MAD", "destination": "FCO"},
            ],
        },
        auto_hidden_city=True,
        allow_contract_sensitive=True,
        carry_on_only=True,
    )
    ticketed = {
        item["ticketed_destination"]
        for item in plan["hypotheses"]
        if item["strategy_id"] == "hidden_city_final_leg"
    }

    assert ticketed == {"LIS", "FCO"}


def test_route_graph_search_is_generic() -> None:
    graph = RouteGraph.model_validate(
        {
            "source": "test",
            "observed_at": "2026-09-09",
            "routes": [
                {"origin": "AAA", "destination": "BBB"},
                {"origin": "BBB", "destination": "CCC"},
                {"origin": "CCC", "destination": "DDD"},
            ],
        }
    )

    assert graph.positioning_gateways("AAA", "DDD", max_main_legs=2) == ["BBB"]


def test_automatic_strategy_sweep_surfaces_gateway_price_headroom(tmp_path) -> None:
    class Provider:
        def search(self, spec):
            price = (
                85
                if (spec.origin, spec.destination) == ("CUN", "MAD")
                else 80
                if (spec.origin, spec.destination) == ("MEX", "CUN")
                else 355
                if spec.return_date
                else 220
            )
            option = FlightOption(
                provider_rank=1,
                price=price,
                currency=spec.currency,
                duration_minutes=600,
                stops=0,
                legs=[
                    FlightLeg(
                        journey_index=0,
                        origin=spec.origin,
                        destination=spec.destination,
                        departure_at=f"{spec.departure_date.isoformat()}T10:00:00",
                        arrival_at=f"{spec.departure_date.isoformat()}T20:00:00",
                        duration_minutes=600,
                    )
                ],
            )
            return ProviderResult(
                "success", [option], 1, SearchCoverage(fully_explored=True)
            )

    api = AgentAPI(
        ManagedStore(tmp_path / "store"),
        BatchExecutor(
            FileCache(tmp_path / "cache", namespace="strategy-test"),
            provider_factory=Provider,
        ),
    )
    run = api.start_strategy_plan(
        {
            "origin": "MEX",
            "destination": "MAD",
            "departure_date": "2027-01-14",
            "return_date": "2027-01-21",
            "currency": "EUR",
            "language": "es-MX",
            "country": "MX",
        },
        route_graph={
            "source": "test schedule",
            "observed_at": "2026-09-09",
            "routes": [
                {"origin": "MEX", "destination": "CUN"},
                {"origin": "CUN", "destination": "MAD"},
            ],
        },
        work_chunk=10,
    )
    results = api.strategy_results(run["run_id"])
    gateway = next(
        item for item in results["frontier"] if item["strategy_id"] == "positioning_gateway"
    )

    assert run["progress"]["coverage_complete"] is True
    assert gateway["airfare"] == 165
    assert gateway["total_price"] == 165
    assert gateway["ticket_count"] == 2
    assert gateway["buffer_nights"] == 2
    assert gateway["components"] == [
        {"role": "main_ticket", "price": 85, "currency": "EUR"},
        {"role": "positioning_ticket", "price": 80, "currency": "EUR"},
    ]
    assert results["gateway_probes"] == []
    assert results["hidden_weights"] is False


def test_cli_exposes_strategy_operation_guides(capsys) -> None:
    assert run(["guide", "strategy_plan"]) == 0
    assert "route_graph" in capsys.readouterr().out
    assert run(["guide", "strategy_results"]) == 0
    assert "max_options_per_search" in capsys.readouterr().out


def test_round_trip_also_generates_two_independent_one_way_searches() -> None:
    plan = build_strategy_plan(
        {
            "origin": "MEX",
            "destination": "MAD",
            "departure_date": "2027-01-14",
            "return_date": "2027-01-21",
            "currency": "EUR",
            "language": "es-MX",
            "country": "MX",
        }
    )
    mixed = next(
        item for item in plan["hypotheses"] if item["strategy_id"] == "mixed_one_way_tickets"
    )

    assert [(search["origin"], search["destination"]) for search in mixed["searches"]] == [
        ("MEX", "MAD"),
        ("MAD", "MEX"),
    ]
    assert all(search["return_date"] is None for search in mixed["searches"])


def test_hidden_city_plan_is_opt_in_carry_on_only_and_uses_a_separate_return() -> None:
    trip = {
        "origin": "MEX",
        "destination": "MAD",
        "departure_date": "2027-01-14",
        "return_date": "2027-01-21",
        "currency": "EUR",
        "language": "es-MX",
        "country": "MX",
        "overhead_cabin_bags": 1,
    }
    excluded = build_strategy_plan(trip, hidden_city_destinations=["LIS"])
    assert excluded["excluded"][0]["strategy_id"] == "hidden_city_final_leg"

    plan = build_strategy_plan(
        trip,
        hidden_city_destinations=["LIS"],
        allow_contract_sensitive=True,
        carry_on_only=True,
    )
    hidden = next(
        item for item in plan["hypotheses"] if item["strategy_id"] == "hidden_city_final_leg"
    )

    assert hidden["ticketed_destination"] == "LIS"
    assert hidden["true_destination"] == "MAD"
    assert hidden["risk"] == "contract_sensitive"
    assert hidden["separate_tickets"] is True
    assert hidden["searches"][0]["segment_filters"]["connecting_airports"] == ["MAD"]
    assert (hidden["searches"][1]["origin"], hidden["searches"][1]["destination"]) == (
        "MAD",
        "MEX",
    )
