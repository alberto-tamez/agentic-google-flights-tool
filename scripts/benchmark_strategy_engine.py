"""Deterministic strategy-engine benchmark against exhaustive fixture pricing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter

from agentic_flights.strategy_engine import (
    OfferQuote,
    ScheduledEvent,
    SearchBoundary,
    StrategyEngine,
    exhaustive_price,
)


def main() -> None:
    start = datetime(2027, 1, 14, 6, tzinfo=UTC)
    events = [
        ScheduledEvent(
            event_id="direct",
            origin="AAA",
            destination="ZZZ",
            departure_at=start.replace(hour=7),
            arrival_at=start.replace(hour=9),
            baggage_state="through_checked",
            connection_protection="protected",
            ticket_group="direct",
        )
    ]
    exact_prices = {("direct",): 100.0}
    for index in range(100):
        gateway = f"{chr(66 + index // 26)}{chr(65 + index % 26)}A"
        first = f"branch-{index}-1"
        second = f"branch-{index}-2"
        events.extend(
            [
                ScheduledEvent(
                    event_id=first,
                    origin="AAA",
                    destination=gateway,
                    departure_at=start.replace(hour=7),
                    arrival_at=start.replace(hour=8),
                    price_lower_bound=100,
                    currency="EUR",
                    baggage_state="through_checked",
                    connection_protection="protected",
                    ticket_group=f"ticket-{index}",
                ),
                ScheduledEvent(
                    event_id=second,
                    origin=gateway,
                    destination="ZZZ",
                    departure_at=start.replace(hour=10),
                    arrival_at=start.replace(hour=12),
                    price_lower_bound=100,
                    currency="EUR",
                    baggage_state="through_checked",
                    connection_protection="protected",
                    ticket_group=f"ticket-{index}",
                ),
            ]
        )
        exact_prices[(first, second)] = 250.0 + index
    boundary = SearchBoundary(
        origin="AAA",
        destination="ZZZ",
        depart_after=start,
        depart_before=start.replace(hour=12),
        arrive_before=start.replace(hour=23),
        max_transfers=1,
        currency="EUR",
        pricing_budget=101,
        route_snapshot_id="benchmark-fixture-v1",
    )

    def price(candidate, remaining=None):
        if remaining is not None and remaining < 1:
            raise RuntimeError("benchmark received no pricing budget")
        return OfferQuote(
            candidate_id=candidate.candidate_id,
            total_price=exact_prices[candidate.event_ids],
            currency="EUR",
            ticket_scope="complete_single_ticket",
            observed_at=start,
        )

    engine = StrategyEngine()
    structures, generation = engine.generate(events, boundary)
    before = perf_counter()
    exhaustive, exhaustive_requests = exhaustive_price(structures, price)
    exhaustive_ms = (perf_counter() - before) * 1000
    lazy = engine.search(events, boundary, price)
    recall = len(
        {item.structure.candidate_id for item in exhaustive}
        & {item.structure.candidate_id for item in lazy.priced_frontier}
    ) / max(1, len(exhaustive))
    print(
        json.dumps(
            {
                "fixture": boundary.route_snapshot_id,
                "structural_candidates": len(structures),
                "partial_labels_pruned": generation.partial_labels_pruned,
                "exhaustive_provider_requests": exhaustive_requests,
                "lazy_provider_requests": lazy.metrics.provider_requests,
                "provider_requests_saved": exhaustive_requests
                - lazy.metrics.provider_requests,
                "lower_bound_candidates_pruned": lazy.metrics.lower_bound_candidates_pruned,
                "frontier_recall": recall,
                "exhaustive_elapsed_ms": round(exhaustive_ms, 3),
                "lazy_elapsed_ms": round(lazy.metrics.elapsed_ms, 3),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
