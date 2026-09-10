from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_flights import AgentAPI
from agentic_flights.strategy_engine import (
    EvidenceClass,
    JourneyRisk,
    OfferQuote,
    PricingObservation,
    ScheduledEvent,
    SearchBoundary,
    StrategyEngine,
    VerificationObservation,
    exhaustive_price,
)

START = datetime(2027, 1, 14, 6, tzinfo=UTC)


def event(
    event_id: str,
    origin: str,
    destination: str,
    depart_hour: int,
    arrive_hour: int,
    *,
    lower_bound: float | None,
    ticket_group: str | None = "T1",
    risk: JourneyRisk = JourneyRisk.STANDARD,
) -> ScheduledEvent:
    return ScheduledEvent(
        event_id=event_id,
        origin=origin,
        destination=destination,
        departure_at=START.replace(hour=depart_hour),
        arrival_at=START.replace(hour=arrive_hour),
        price_lower_bound=lower_bound,
        currency="EUR" if lower_bound is not None else None,
        ticket_group=ticket_group,
        baggage_state="through_checked",
        connection_protection="protected",
        risk=risk,
    )


def boundary(**updates) -> SearchBoundary:
    values = {
        "origin": "AAA",
        "destination": "DDD",
        "depart_after": START,
        "depart_before": START.replace(hour=12),
        "arrive_before": START.replace(hour=23),
        "max_transfers": 2,
        "minimum_connection_minutes": 30,
        "currency": "EUR",
        "pricing_budget": 20,
        "verification_budget": 0,
        "route_snapshot_id": "fixture-2027-01-14",
    }
    values.update(updates)
    return SearchBoundary.model_validate(values)


def quote(candidate, price: float, *, final: bool = False) -> OfferQuote:
    return OfferQuote(
        candidate_id=candidate.candidate_id,
        total_price=price,
        currency="EUR",
        evidence_class=(
            EvidenceClass.PROVIDER_FINAL_TOTAL if final else EvidenceClass.SEARCH_OBSERVATION
        ),
        ticket_scope=(
            "complete_single_ticket"
            if candidate.risk == JourneyRisk.STANDARD
            else "separate_tickets"
        ),
        whole_itinerary_matched=final,
        baggage_complete=final,
        seller="Fixture Travel" if final else None,
        observed_at=START,
    )


def test_round_search_matches_exhaustive_frontier_on_finite_fixture() -> None:
    schedule = [
        event("direct", "AAA", "DDD", 8, 13, lower_bound=None),
        event("via-b-1", "AAA", "BBB", 7, 8, lower_bound=35),
        event("via-b-2", "BBB", "DDD", 9, 12, lower_bound=45),
        event("via-c-1", "AAA", "CCC", 7, 8, lower_bound=40),
        event("via-c-2", "CCC", "DDD", 9, 10, lower_bound=60),
    ]
    prices = {
        ("direct",): 120,
        ("via-b-1", "via-b-2"): 90,
        ("via-c-1", "via-c-2"): 150,
    }
    engine = StrategyEngine()
    structures, _ = engine.generate(schedule, boundary())

    def price(candidate, remaining=None):
        return quote(candidate, prices[candidate.event_ids])

    exhaustive, exhaustive_requests = exhaustive_price(structures, price)
    result = engine.search(schedule, boundary(), price)

    assert {item.structure.event_ids for item in result.priced_frontier} == {
        item.structure.event_ids for item in exhaustive
    }
    assert exhaustive_requests == 3
    assert result.metrics.provider_requests <= exhaustive_requests
    assert result.structural_coverage_complete is True
    assert result.global_minimum_claimed is False


def test_lazy_complete_pricing_reduces_requests_without_frontier_loss() -> None:
    schedule = [event("direct", "AAA", "DDD", 7, 9, lower_bound=None)]
    prices = {("direct",): 100}
    for index in range(12):
        gateway = f"{chr(66 + index // 26)}{chr(65 + index % 26)}A"
        first = f"branch-{index}-1"
        second = f"branch-{index}-2"
        schedule.extend(
            [
                event(first, "AAA", gateway, 7, 8, lower_bound=100),
                event(second, gateway, "DDD", 9, 10, lower_bound=100),
            ]
        )
        prices[(first, second)] = 250 + index
    calls = []

    def price(candidate, remaining=None):
        calls.append(candidate.event_ids)
        return quote(candidate, prices[candidate.event_ids])

    engine = StrategyEngine()
    structures, _ = engine.generate(schedule, boundary(max_transfers=1))
    exhaustive, exhaustive_requests = exhaustive_price(structures, price)
    calls.clear()
    result = engine.search(schedule, boundary(max_transfers=1), price)

    assert {item.structure.event_ids for item in result.priced_frontier} == {
        item.structure.event_ids for item in exhaustive
    }
    assert calls == [("direct",)]
    assert result.metrics.provider_requests == 1
    assert result.metrics.lower_bound_candidates_pruned == 12
    assert exhaustive_requests == 13


def test_unknown_cost_and_risk_classes_prevent_unsafe_dominance() -> None:
    schedule = [
        event("known", "AAA", "DDD", 8, 11, lower_bound=100),
        event("unknown", "AAA", "DDD", 9, 12, lower_bound=None),
        event(
            "separate",
            "AAA",
            "DDD",
            9,
            12,
            lower_bound=50,
            risk=JourneyRisk.SEPARATE_TICKET,
        ),
    ]
    structures, metrics = StrategyEngine().generate(
        schedule,
        boundary(allowed_risks={JourneyRisk.STANDARD, JourneyRisk.SEPARATE_TICKET}),
    )

    assert {item.event_ids for item in structures} == {
        ("known",),
        ("unknown",),
        ("separate",),
    }
    assert metrics.partial_labels_pruned == 0


def test_connections_use_absolute_times_and_may_leave_after_departure_window() -> None:
    schedule = [
        event("first", "AAA", "BBB", 11, 12, lower_bound=40),
        event("too-tight", "BBB", "DDD", 12, 13, lower_bound=40),
        ScheduledEvent(
            event_id="valid-next-day",
            origin="BBB",
            destination="DDD",
            departure_at=START + timedelta(days=1, hours=7),
            arrival_at=START + timedelta(days=1, hours=10),
            price_lower_bound=50,
            currency="EUR",
            ticket_group="T1",
            baggage_state="through_checked",
            connection_protection="protected",
        ),
    ]
    result, _ = StrategyEngine().generate(
        schedule,
        boundary(
            depart_before=START.replace(hour=11),
            arrive_before=START + timedelta(days=2),
            max_transfers=1,
        ),
    )

    assert [item.event_ids for item in result] == [("first", "valid-next-day")]


def test_contract_sensitive_search_requires_an_explicit_strategy() -> None:
    with pytest.raises(ValueError, match="explicitly enabled"):
        boundary(allowed_risks={JourneyRisk.STANDARD, JourneyRisk.CONTRACT_SENSITIVE})


def test_verification_accepts_only_complete_matched_final_evidence() -> None:
    schedule = [event("direct", "AAA", "DDD", 8, 12, lower_bound=90)]
    search_boundary = boundary(verification_budget=3)

    def price(candidate, remaining):
        assert remaining > 0
        return quote(candidate, 100)

    def verify(candidate, remaining):
        assert remaining == 3
        final = quote(candidate.structure, 105, final=True)
        return VerificationObservation(quote=final, requests_made=1, browser_transitions=3)

    result = StrategyEngine().search(schedule, search_boundary, price, verify)

    assert len(result.verified_frontier) == 1
    assert result.verified_frontier[0].quote.total_price == 105
    assert result.metrics.verified_finalists == 1
    assert result.metrics.browser_transitions == 3
    assert result.verification_coverage_complete is True


def test_pricing_budget_reports_incomplete_coverage_instead_of_no_inventory() -> None:
    schedule = [
        event("first", "AAA", "DDD", 8, 11, lower_bound=None),
        event("second", "AAA", "DDD", 9, 12, lower_bound=None),
    ]
    result = StrategyEngine().search(
        schedule,
        boundary(pricing_budget=1),
        lambda candidate, remaining: quote(candidate, 100),
    )

    assert len(result.priced_frontier) == 1
    assert len(result.unpriced_candidate_ids) == 1
    assert result.pricing_coverage_complete is False


def test_pricing_failure_is_incomplete_not_no_inventory() -> None:
    schedule = [event("direct", "AAA", "DDD", 8, 11, lower_bound=90)]
    result = StrategyEngine().search(
        schedule,
        boundary(),
        lambda candidate, remaining: None,
    )

    assert result.priced_frontier == []
    assert len(result.pricing_failure_candidate_ids) == 1
    assert result.pricing_coverage_complete is False


def test_mixed_currency_lower_bounds_require_explicit_conversion() -> None:
    usd = event("usd", "AAA", "DDD", 8, 11, lower_bound=90).model_copy(
        update={"currency": "USD"}
    )
    with pytest.raises(ValueError, match="convert them explicitly"):
        StrategyEngine().generate([usd], boundary())


def test_verification_rejects_a_quote_for_another_candidate() -> None:
    schedule = [event("direct", "AAA", "DDD", 8, 11, lower_bound=90)]

    def verify(candidate, remaining):
        assert remaining == 1
        return quote(
            candidate.structure.model_copy(update={"candidate_id": "journey_wrong"}),
            105,
            final=True,
        )

    result = StrategyEngine().search(
        schedule,
        boundary(verification_budget=1),
        lambda candidate, remaining: quote(candidate, 100),
        verify,
    )

    assert result.verified_frontier == []
    assert result.verification_failures[0]["code"] == "verification_not_confirmed"


def test_pricing_evidence_partition_is_explicit() -> None:
    schedule = [event("direct", "AAA", "DDD", 8, 11, lower_bound=90)]
    with pytest.raises(ValueError, match="evidence"):
        StrategyEngine().search(
            schedule,
            boundary(),
            lambda candidate, remaining: quote(candidate, 100, final=True),
        )


def test_callback_cannot_silently_overrun_request_budget() -> None:
    schedule = [event("direct", "AAA", "DDD", 8, 11, lower_bound=90)]
    with pytest.raises(ValueError, match="request budget"):
        StrategyEngine().search(
            schedule,
            boundary(pricing_budget=1),
            lambda candidate, remaining: PricingObservation(
                quote=quote(candidate, 100), requests_made=2
            ),
        )


def test_agent_api_exposes_a_plain_structural_frontier_without_handles() -> None:
    response = AgentAPI().schedule_frontier(
        [event("direct", "AAA", "DDD", 8, 11, lower_bound=90).model_dump(mode="json")],
        boundary().model_dump(mode="json"),
    )

    assert len(response["candidates"]) == 1
    assert response["pricing_required"] is True
    assert response["global_minimum_claimed"] is False
    assert "run_id" not in response
    assert "result_id" not in response["candidates"][0]
