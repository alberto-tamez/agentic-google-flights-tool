"""Report the deterministic agent-contract baseline without calling a provider."""

import json

from agentic_flights.agent import (
    AgentCoverage,
    AgentSearchReply,
    DecisionJourney,
    DecisionRow,
    encode_agent_reply,
)


def main() -> None:
    row = DecisionRow(
        candidate_ref="rf_0123456789abcdef",
        route=("MAD-LHR",),
        price=100,
        currency="EUR",
        price_evidence="observed",
        ticket_scope="partial_or_unknown",
        journeys=(
            DecisionJourney(
                origin="MAD",
                destination="LHR",
                departure_at="2027-01-14T08:00:00+01:00",
                arrival_at="2027-01-14T10:00:00+00:00",
            ),
        ),
        duration_minutes=180,
        stops=0,
        destination_stay_minutes=None,
        overnight_journeys=(),
        baggage="to_verify",
        strengths=("lowest_price",),
        eligibility="verify_before_recommending",
        reason_codes=("price_unverified", "whole_itinerary_unverified"),
    )
    reply = AgentSearchReply(
        search_ref="rgf_0123456789abcdef",
        options=(row,) * 4,
        coverage=AgentCoverage(
            attempted_queries=24,
            remaining_queries=0,
            failed_queries=0,
            search_complete=False,
            provider_truncated=True,
            parse_failures=0,
            unresolved=("provider_truncated",),
        ),
        omitted_options=20,
    )
    encoded = encode_agent_reply(reply)
    print(
        json.dumps(
            {
                "old_baseline": {"agent_calls": 21, "raw_output_chars": 246217},
                "contract": {
                    "decision_operations": 2,
                    "rows": len(reply.options),
                    "observed_worst_case_serialized_bytes": len(encoded.encode()),
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
