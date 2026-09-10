from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from conftest import make_api, make_option
from pydantic import ValidationError

from agentic_flights.agent import (
    AGENT_REPLY_MAX_BYTES,
    AgentFailureReply,
    AgentSearchReply,
    AgentTripRequest,
    FlightAgent,
    agent_runtime,
    encode_agent_reply,
    whole_itinerary_confirmed,
)
from agentic_flights.models import SearchCoverage
from agentic_flights.provider import ProviderError, ProviderResult


def request(**updates):
    values = {
        "origins": ["MAD"],
        "destinations": ["LHR"],
        "departure_start": "2027-01-14",
        "departure_end": "2027-01-14",
        "min_nights": 2,
        "max_nights": 2,
        "currency": "EUR",
        "baggage": {"overhead_cabin_bags": 1, "require_overhead_cabin_bag": True},
        "priorities": ["price", "duration"],
    }
    values.update(updates)
    return values


def test_agent_request_excludes_provider_state_and_runtime_manifest_is_bounded():
    parsed = AgentTripRequest.model_validate(request())
    assert parsed.baggage.require_overhead_cabin_bag
    for field in ("search_mode", "continuation", "provider"):
        with pytest.raises(ValidationError):
            AgentTripRequest.model_validate(request(**{field: "x"}))
    manifest = agent_runtime()
    assert manifest.contract == "flight-decision-v1"
    assert manifest.capabilities == {"search", "verify", "bounded_replies"}


def test_agent_search_is_small_and_preserves_baggage_as_verification_intent(tmp_path):
    class Provider:
        def search(self, spec):
            assert spec.search_mode == "discover"
            assert not spec.require_overhead_cabin_bag
            return ProviderResult(
                "success", [make_option()], 1, SearchCoverage(fully_explored=True)
            )

    reply = FlightAgent(make_api(tmp_path, Provider)).search(request())
    assert isinstance(reply, AgentSearchReply)
    assert len(reply.options) == 1
    assert reply.options[0].baggage == "to_verify"
    assert "baggage_to_verify" in reply.options[0].reason_codes
    encoded = encode_agent_reply(reply)
    assert len(encoded.encode()) <= AGENT_REPLY_MAX_BYTES
    assert not ({"search_spec", "url", "continuation", "next_actions"} & _keys(json.loads(encoded)))


def test_agent_network_failure_is_compact_and_requires_environment_change(tmp_path):
    class Provider:
        def search(self, spec):
            raise ProviderError(
                "direct_provider_error",
                "Could not resolve host",
                retryable=True,
                requests_made=1,
            )

    reply = FlightAgent(make_api(tmp_path, Provider)).search(request())
    assert reply == AgentFailureReply(
        code="network_environment",
        retry="after_environment_change",
        message="Could not resolve host",
    )
    assert len(encode_agent_reply(reply).encode()) <= AGENT_REPLY_MAX_BYTES


def test_candidate_must_belong_to_search(tmp_path):
    class Provider:
        def search(self, spec):
            return ProviderResult(
                "success", [make_option()], 1, SearchCoverage(fully_explored=True)
            )

    agent = FlightAgent(make_api(tmp_path, Provider))
    found = agent.search(request())
    result = agent.verify(found.search_ref, "rf_0000000000000000")
    assert result == AgentFailureReply(
        code="invalid_input",
        retry="fix_input",
        message="Candidate does not belong to this search",
    )


@pytest.mark.parametrize(
    ("ticket_scope", "provenance", "matched", "uncompared", "expected"),
    [
        ("complete_single_ticket", "provider_final_total", True, [], True),
        ("partial_or_unknown", "provider_final_total", True, [], False),
        ("complete_single_ticket", "provider_search_result", True, [], False),
        ("complete_single_ticket", "provider_final_total", False, [], False),
        ("complete_single_ticket", "provider_final_total", True, [1], False),
    ],
)
def test_confirmation_is_one_strict_predicate(
    ticket_scope, provenance, matched, uncompared, expected
):
    option = make_option().model_copy(
        update={"ticket_scope": ticket_scope, "price_provenance": provenance}
    )
    assert (
        whole_itinerary_confirmed(
            {"whole_itinerary_matched": matched, "uncompared_journey_indexes": uncompared},
            option,
        )
        is expected
    )


def test_start_cannot_be_switched_to_verification_by_input(tmp_path):
    seen = []

    class Provider:
        def search(self, spec):
            seen.append(spec.search_mode)
            return ProviderResult(
                "success", [make_option()], 1, SearchCoverage(fully_explored=True)
            )

    api = make_api(tmp_path, Provider)
    api.start(
        {
            "origin": "MAD",
            "destination": "LHR",
            "departure_date": date(2027, 1, 14),
            "currency": "EUR",
            "language": "en-US",
            "country": "ES",
            "search_mode": "verify",
        }
    )
    assert seen == ["discover"]


def test_default_mcp_exposes_only_two_decision_operations(tmp_path):
    Client = pytest.importorskip("mcp").Client
    from agentic_flights.mcp_server import create_server

    async def check():
        async with Client(create_server(make_api(tmp_path))) as client:
            tools = await client.list_tools()
            tools = getattr(tools, "tools", tools)
            assert {tool.name for tool in tools} == {"search", "verify"}

    asyncio.run(check())


def _keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value), set())
    return set()
