import json
import stat
from datetime import date

import pytest
from conftest import make_api

from agentic_flights import AirRoutesClient, RouteEdge, RouteGraph, RouteSourceError


def test_air_routes_client_uses_directional_connections_and_preserves_metadata(
    tmp_path, monkeypatch
) -> None:
    payload = {
        "source_observed_at": "2026-09-09T12:00:00+00:00",
        "options": [
            {
                "total_flying_min": 700,
                "stops": 1,
                "legs": [
                    {
                        "route_id": "MEX-CUN",
                        "from": "MEX",
                        "to": "CUN",
                        "flying_min": 130,
                        "distance_km": 1284.5,
                        "seasonality_label": "winter",
                        "service_type": "scheduled",
                        "airlines": [
                            {
                                "airline_code": "AM",
                                "schedule": [{"day": "Mon", "times": ["09:00"]}],
                            }
                        ],
                    },
                    {"from": "CUN", "to": "MAD", "flying_min": 570, "airlines": []},
                ],
            }
        ]
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    client = AirRoutesClient(tmp_path)
    graph = client.build_graph("mex", "mad")

    assert [(route.origin, route.destination) for route in graph.routes] == [
        ("MEX", "CUN"),
        ("CUN", "MAD"),
    ]
    assert graph.observed_at == date(2026, 9, 9)
    assert client.route_metadata["MEX-CUN"]["airlines"][0]["airline_code"] == "AM"
    assert client.route_metadata["MEX-CUN"]["seasonality_label"] == "winter"
    assert client.route_metadata["MEX-CUN"]["distance_km"] == 1284.5
    assert client.route_metadata["MEX-CUN"]["service_type"] == "scheduled"
    assert client.route_metadata["MEX-CUN"]["provenance"]["response_checksum"]
    assert client.diagnostics["seasonal"] == 1
    assert client.diagnostics["missing_schedule"] == 1
    assert client.provenance["license_status"] == "experimental/unclear"
    assert client.provenance["checksum"]
    assert client.provenance["records_received"] == 2
    assert client.provenance["records_accepted"] == 2
    assert client.provenance["records_rejected"] == 0
    assert client.provenance["coverage"]["complete_for_requested_pair"] is False
    assert client.network_requests == 1

    cached = client.build_graph("MEX", "MAD")
    assert cached.routes == graph.routes
    assert client.cache_hits == 1


def test_cached_route_graph_preserves_its_fetch_date(tmp_path) -> None:
    payload = {
        "fetched_at": "2026-09-04",
        "payload": {
            "options": [
                {"legs": [{"from": "MEX", "to": "CUN"}, {"from": "CUN", "to": "MAD"}]}
            ]
        },
    }
    (tmp_path / "MEX-MAD.json").write_text(json.dumps(payload), encoding="utf-8")
    graph = AirRoutesClient(tmp_path).build_graph("MEX", "MAD")

    assert graph.observed_at == date(2026, 9, 4)


def test_auto_strategy_plan_uses_route_source_without_city_rules(tmp_path) -> None:
    class Source:
        network_requests = 2
        cache_hits = 0

        def build_graph(self, origin, destination, *, include_beyond=False):
            assert (origin, destination, include_beyond) == ("AAA", "DDD", False)
            return RouteGraph(
                source="test source",
                observed_at=date.today(),
                    routes=[
                        {"origin": "AAA", "destination": "BBB"},
                        {"origin": "AAA", "destination": "CCC"},
                        {"origin": "BBB", "destination": "DDD"},
                        {"origin": "CCC", "destination": "DDD"},
                ],
            )

    api = make_api(tmp_path)
    api.route_source = Source()
    response = api.start_auto_strategy_plan(
        {
            "origin": "AAA",
            "destination": "DDD",
            "departure_date": "2027-01-14",
            "currency": "EUR",
            "language": "en-US",
            "country": "ES",
        },
        work_chunk=20,
    )

    assert response["route_discovery"]["routes_loaded"] == 4
    assert response["route_discovery"]["gateway_candidate_mode"] == "path"
    assert response["strategy_progress"]["hypotheses"] == 3
    assert response["strategy_progress"]["component_searches"] == 5


def test_default_route_cache_initializes_the_managed_store_before_writing(
    tmp_path, monkeypatch
) -> None:
    connections = {"options": [{"legs": [{"from": "AAA", "to": "DDD"}]}]}
    destinations = {
            "destinations": [
                {"from": "DDD", "to": "EEE", "status": "active"},
            ]
    }

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def get(url, **kwargs):
        return Response(connections if "/api/connections?" in url else destinations)

    monkeypatch.setattr("curl_cffi.requests.get", get)
    api = make_api(tmp_path)
    response = api.start_auto_strategy_plan(
        {
            "origin": "AAA",
            "destination": "DDD",
            "departure_date": "2027-01-14",
            "currency": "EUR",
            "language": "en-US",
            "country": "ES",
        },
        work_chunk=10,
    )

    assert response["progress"]["coverage_complete"] is True
    assert (api.store.root / ".owned-by-agentic-flights").is_file()
    assert (api.store.root / "route-cache/AAA-DDD.json").is_file()
    assert not (api.store.root / "route-cache/DDD.json").exists()


def test_all_outgoing_mode_is_labeled_as_empirical_candidate_generation(tmp_path) -> None:
    class Source:
        network_requests = 2
        cache_hits = 0

        def build_graph(self, origin, destination, *, include_beyond=False):
            return RouteGraph(
                source="test source",
                observed_at=date.today(),
                routes=[{"origin": "AAA", "destination": "DDD"}],
            )

        def destinations(self, origin):
            assert origin == "AAA"
            return [
                RouteEdge(origin="AAA", destination="BBB"),
                RouteEdge(origin="AAA", destination="CCC"),
            ]

    api = make_api(tmp_path)
    api.route_source = Source()
    response = api.start_auto_strategy_plan(
        {
            "origin": "AAA",
            "destination": "DDD",
            "departure_date": "2027-01-14",
            "currency": "EUR",
            "language": "en-US",
            "country": "ES",
        },
        gateway_candidate_mode="all_outgoing",
        work_chunk=1,
    )
    report, _, _ = api._load(response["run_id"])
    hypotheses = report.exploration_state["strategy_plan"]["hypotheses"]
    gateways = [
        item for item in hypotheses if item["strategy_id"] == "positioning_gateway"
    ]

    assert {item["ticketed_origin"] for item in gateways} == {"BBB", "CCC"}
    assert {item["validation_basis"] for item in gateways} == {
        "empirical_fare_required"
    }


def test_route_diagnostics_reject_bad_inactive_duplicate_and_charter_rows(
    tmp_path, monkeypatch
) -> None:
    payload = {
        "options": [
            {
                "legs": [
                    {"from": "AAA", "to": "BBB"},
                    {"from": "AAA", "to": "BBB"},
                    {"from": "AAA", "to": "CCC", "status": "inactive"},
                    {"from": "AAA", "to": "DDD", "service_type": "charter"},
                    {"from": "AAA", "to": "B4D"},
                ]
            }
        ]
    }

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    client = AirRoutesClient(tmp_path)
    graph = client.build_graph("AAA", "ZZZ")

    assert [(route.origin, route.destination) for route in graph.routes] == [("AAA", "BBB")]
    assert client.diagnostics["received"] == 5
    assert client.diagnostics["duplicate"] == 1
    assert client.diagnostics["inactive"] == 1
    assert client.diagnostics["charter"] == 1
    assert client.diagnostics["invalid"] == 1
    assert set(client.diagnostics["rejection_codes"]) == {
        "duplicate_route",
        "inactive_route",
        "charter_service",
        "invalid_route",
    }
    assert len(client.diagnostics["rejection_samples"]) <= 5


def test_nonempty_unparseable_connection_response_fails_schema_drift(
    tmp_path, monkeypatch
) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"options": [{"unexpected": []}]}

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(RouteSourceError) as error:
        AirRoutesClient(tmp_path).build_graph("AAA", "DDD")
    assert error.value.code == "schema_drift"


def test_temporary_failure_uses_stale_cache_with_true_age(tmp_path, monkeypatch) -> None:
    path = tmp_path / "AAA-DDD.json"
    payload = {
        "fetched_at": "2026-09-04",
        "payload": {"options": [{"legs": [{"from": "AAA", "to": "DDD"}]}]},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    class Response:
        status_code = 503

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    client = AirRoutesClient(tmp_path, ttl_seconds=1)
    graph = client.build_graph("AAA", "DDD")

    assert graph.routes[0].destination == "DDD"
    assert client.stale_cache_hits == 1
    assert client.provenance["stale"] is True
    assert client.provenance["age_seconds"] > 24 * 60 * 60
    assert client.provenance["replayed_at"]
    assert client.provenance["latest_attempt_at"] != client.provenance["first_retrieved_at"]


def test_schema_failure_does_not_fall_back_to_stale_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "AAA-DDD.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-04",
                "payload": {"options": [{"legs": [{"from": "AAA", "to": "DDD"}]}]},
            }
        ),
        encoding="utf-8",
    )

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"changed": "shape"}

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(RouteSourceError) as error:
        AirRoutesClient(tmp_path, ttl_seconds=1).build_graph("AAA", "DDD")
    assert error.value.code == "schema_drift"


def test_temporary_failure_rejects_cache_beyond_fallback_limit(tmp_path, monkeypatch) -> None:
    (tmp_path / "AAA-DDD.json").write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-04",
                "payload": {"options": [{"legs": [{"from": "AAA", "to": "DDD"}]}]},
            }
        ),
        encoding="utf-8",
    )

    class Response:
        status_code = 503

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(RouteSourceError) as error:
        AirRoutesClient(
            tmp_path, ttl_seconds=1, stale_if_error_seconds=60
        ).build_graph("AAA", "DDD")
    assert error.value.code == "temporary_source_failure"


def test_route_cache_permissions_and_symlink_rejection(tmp_path, monkeypatch) -> None:
    payload = {"options": [{"legs": [{"from": "AAA", "to": "DDD"}]}]}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr("curl_cffi.requests.get", lambda *args, **kwargs: Response())
    cache_dir = tmp_path / "routes"
    AirRoutesClient(cache_dir).build_graph("AAA", "DDD")
    assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((cache_dir / "AAA-DDD.json").stat().st_mode) == 0o600

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RouteSourceError) as error:
        AirRoutesClient(link).build_graph("AAA", "DDD")
    assert error.value.code == "unsafe_cache"
