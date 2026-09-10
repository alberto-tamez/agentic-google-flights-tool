import json
from datetime import date

from conftest import make_api

from agentic_flights import AirRoutesClient, RouteGraph


def test_air_routes_client_keeps_only_active_valid_routes(tmp_path, monkeypatch) -> None:
    payload = {
        "destinations": [
            {"from": "MEX", "to": "CUN", "status": "active"},
            {"from": "MEX", "to": "MAD", "status": "dropped"},
            {"from": "MEX", "to": "B4D", "status": "active"},
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

    assert [(route.origin, route.destination) for route in graph.routes] == [("MEX", "CUN")]
    assert graph.observed_at == date.today()
    assert client.network_requests == 1

    cached = client.build_graph("MEX", "MAD")
    assert cached.routes == graph.routes
    assert client.cache_hits == 1


def test_cached_route_graph_preserves_its_fetch_date(tmp_path) -> None:
    payload = {
        "fetched_at": "2026-09-04",
        "payload": {
            "destinations": [
                {"from": "MEX", "to": "CUN", "status": "active"},
            ]
        },
    }
    (tmp_path / "MEX.json").write_text(json.dumps(payload), encoding="utf-8")
    graph = AirRoutesClient(tmp_path).build_graph("MEX", "MAD")

    assert graph.observed_at == date(2026, 9, 4)


def test_auto_strategy_plan_uses_route_source_without_city_rules(tmp_path) -> None:
    class Source:
        network_requests = 2
        cache_hits = 0

        def build_graph(self, origin, destination, *, include_beyond=False):
            assert (origin, destination, include_beyond) == ("AAA", "DDD", True)
            return RouteGraph(
                source="test source",
                observed_at=date.today(),
                routes=[
                    {"origin": "AAA", "destination": "BBB"},
                    {"origin": "AAA", "destination": "CCC"},
                    {"origin": "DDD", "destination": "BBB"},
                    {"origin": "DDD", "destination": "CCC"},
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
    assert response["route_discovery"]["gateway_candidate_mode"] == "reciprocal"
    assert response["strategy_progress"]["hypotheses"] == 3
    assert response["strategy_progress"]["component_searches"] == 5


def test_default_route_cache_initializes_the_managed_store_before_writing(
    tmp_path, monkeypatch
) -> None:
    payloads = {
        "AAA": {
            "destinations": [
                {"from": "AAA", "to": "BBB", "status": "active"},
            ]
        },
        "DDD": {
            "destinations": [
                {"from": "DDD", "to": "BBB", "status": "active"},
            ]
        },
    }

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def get(url, **kwargs):
        code = url.split("/api/airport/")[1].split("/")[0]
        return Response(payloads[code])

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
    assert (api.store.root / "route-cache/AAA.json").is_file()
