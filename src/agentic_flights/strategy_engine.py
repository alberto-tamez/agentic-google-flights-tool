"""Bounded, round-based search over dated transport events.

This module finds feasible journey structures. Providers still price complete ticket
constructions because airline fares are not additive edge weights.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_flights.models import SearchSpec


class JourneyRisk(StrEnum):
    STANDARD = "standard"
    SEPARATE_TICKET = "separate_ticket"
    CONTRACT_SENSITIVE = "contract_sensitive"


class EvidenceClass(StrEnum):
    STRUCTURAL = "structural"
    SEARCH_OBSERVATION = "search_observation"
    PROVIDER_FINAL_TOTAL = "provider_final_total"


class ScheduledEvent(BaseModel):
    """One dated flight or ground movement from a schedule snapshot."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    mode: Literal["flight", "ground", "airport_transfer"] = "flight"
    operating_carrier: str | None = None
    marketing_carrier: str | None = None
    flight_number: str | None = None
    equipment: str | None = None
    terminal_from: str | None = None
    terminal_to: str | None = None
    codeshare: bool | None = None
    service_type: str | None = None
    ticket_group: str | None = None
    price_lower_bound: float | None = Field(default=None, ge=0)
    currency: str | None = None
    connection_protection: Literal["protected", "unprotected", "unknown"] = "unknown"
    baggage_state: Literal["through_checked", "recheck", "carry_on_only", "unknown"] = (
        "unknown"
    )
    risk: JourneyRisk = JourneyRisk.STANDARD
    source_record_id: str | None = None

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        return SearchSpec.validate_airport(value.strip())

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return SearchSpec.validate_currency(value.strip())

    @model_validator(mode="after")
    def validate_event(self) -> ScheduledEvent:
        if self.origin == self.destination:
            raise ValueError("event origin and destination must differ")
        if self.departure_at.tzinfo is None or self.arrival_at.tzinfo is None:
            raise ValueError("event times must include UTC offsets")
        if self.arrival_at <= self.departure_at:
            raise ValueError("event arrival must follow departure")
        if (self.price_lower_bound is None) != (self.currency is None):
            raise ValueError("lower bound and currency must be supplied together")
        return self


class SearchBoundary(BaseModel):
    """The finite scope within which a search may call itself exhaustive."""

    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str
    depart_after: datetime
    depart_before: datetime
    arrive_before: datetime | None = None
    max_transfers: int = Field(default=2, ge=0)
    minimum_connection_minutes: int = Field(default=45, ge=0)
    enabled_strategies: frozenset[str] = frozenset({"conventional"})
    allowed_risks: frozenset[JourneyRisk] = frozenset({JourneyRisk.STANDARD})
    currency: str
    pricing_evidence_class: EvidenceClass = EvidenceClass.SEARCH_OBSERVATION
    pricing_budget: int = Field(ge=0)
    verification_budget: int = Field(default=0, ge=0)
    route_snapshot_id: str = Field(min_length=1)
    traveler_constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        return SearchSpec.validate_airport(value.strip())

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return SearchSpec.validate_currency(value.strip())

    @model_validator(mode="after")
    def validate_boundary(self) -> SearchBoundary:
        if self.origin == self.destination:
            raise ValueError("boundary origin and destination must differ")
        if self.depart_after.tzinfo is None or self.depart_before.tzinfo is None:
            raise ValueError("boundary times must include UTC offsets")
        if self.arrive_before is not None and self.arrive_before.tzinfo is None:
            raise ValueError("arrive_before must include a UTC offset")
        if self.depart_before < self.depart_after:
            raise ValueError("departure window must be ordered")
        if JourneyRisk.CONTRACT_SENSITIVE in self.allowed_risks and not any(
            name in self.enabled_strategies
            for name in {"hidden_city", "throwaway_return", "nested_ticket"}
        ):
            raise ValueError("contract-sensitive risk requires an explicitly enabled strategy")
        return self

    def scope(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class JourneyLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_location: str
    arrival_time: datetime
    event_ids: tuple[str, ...]
    true_origin: str
    true_destination: str
    ticketed_origin: str
    ticketed_destination: str
    ticket_boundaries: tuple[str, ...]
    airfare_lower_bound: float | None
    currency: str
    elapsed_minutes: int = Field(ge=0)
    ground_minutes: int = Field(ge=0)
    stops: int = Field(ge=0)
    baggage_state: str
    connection_protection: str
    hotel_cost: float | None = Field(default=None, ge=0)
    risk: JourneyRisk
    unresolved_evidence: frozenset[str] = frozenset()
    eligibility_key: str = "default"


class StructuralCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    event_ids: tuple[str, ...]
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    airfare_lower_bound: float | None
    currency: str
    total_minutes: int
    ground_minutes: int
    stops: int
    ticket_count: int
    risk: JourneyRisk
    baggage_state: str
    connection_protection: str
    unresolved_evidence: frozenset[str]
    eligibility_key: str = "default"


class OfferQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    total_price: float = Field(ge=0)
    currency: str
    evidence_class: EvidenceClass = EvidenceClass.SEARCH_OBSERVATION
    ticket_scope: Literal["complete_single_ticket", "separate_tickets", "partial_or_unknown"]
    whole_itinerary_matched: bool = False
    baggage_complete: bool = False
    seller: str | None = None
    observed_at: datetime

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return SearchSpec.validate_currency(value.strip())

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a UTC offset")
        return value


class PricingObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: OfferQuote
    requests_made: int = Field(default=1, ge=0)
    cache_hit: bool = False


class VerificationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: OfferQuote | None = None
    requests_made: int = Field(default=1, ge=0)
    browser_transitions: int = Field(default=0, ge=0)
    code: str | None = None


class PricedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure: StructuralCandidate
    quote: OfferQuote


class EngineMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structural_candidates_generated: int = 0
    partial_labels_generated: int = 0
    partial_labels_pruned: int = 0
    lower_bound_candidates_pruned: int = 0
    provider_requests: int = 0
    browser_transitions: int = 0
    cache_hits: int = 0
    verified_finalists: int = 0
    elapsed_ms: float = 0


class StrategySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundary: SearchBoundary
    structural_frontier: list[StructuralCandidate]
    priced_frontier: list[PricedCandidate]
    verified_frontier: list[PricedCandidate]
    unpriced_candidate_ids: list[str]
    pricing_failure_candidate_ids: list[str]
    verification_failures: list[dict[str, str | None]]
    metrics: EngineMetrics
    structural_coverage_complete: bool
    pricing_coverage_complete: bool
    verification_coverage_complete: bool
    global_minimum_claimed: bool = False
    ordering: str = "unranked_pareto_frontier"


PriceFunction = Callable[
    [StructuralCandidate, int], OfferQuote | PricingObservation | None
]
VerifyFunction = Callable[
    [PricedCandidate, int], OfferQuote | VerificationObservation | None
]
ReferencePriceFunction = Callable[
    [StructuralCandidate], OfferQuote | PricingObservation | None
]


class StrategyEngine:
    """Generate feasible labels, price lazily, and verify the resulting frontier."""

    def generate(
        self, events: Iterable[ScheduledEvent | dict[str, Any]], boundary: SearchBoundary
    ) -> tuple[list[StructuralCandidate], EngineMetrics]:
        started = perf_counter()
        schedule = sorted(
            (ScheduledEvent.model_validate(event) for event in events),
            key=lambda event: (event.departure_at, event.arrival_at, event.event_id),
        )
        mismatched_currencies = sorted(
            {
                event.currency
                for event in schedule
                if event.currency is not None and event.currency != boundary.currency
            }
        )
        if mismatched_currencies:
            raise ValueError(
                "event lower-bound currencies do not match the boundary; "
                "convert them explicitly before searching"
            )
        by_origin: dict[str, list[ScheduledEvent]] = {}
        for event in schedule:
            by_origin.setdefault(event.origin, []).append(event)

        seed = JourneyLabel(
            current_location=boundary.origin,
            arrival_time=boundary.depart_after,
            event_ids=(),
            true_origin=boundary.origin,
            true_destination=boundary.destination,
            ticketed_origin=boundary.origin,
            ticketed_destination=boundary.origin,
            ticket_boundaries=(),
            airfare_lower_bound=0,
            currency=boundary.currency,
            elapsed_minutes=0,
            ground_minutes=0,
            stops=0,
            baggage_state="not_applicable",
            connection_protection="not_applicable",
            hotel_cost=0,
            risk=JourneyRisk.STANDARD,
        )
        metrics = EngineMetrics()
        labels_by_location: dict[str, list[JourneyLabel]] = {boundary.origin: [seed]}
        previous_round = [seed]
        completed: list[JourneyLabel] = []
        for _round in range(boundary.max_transfers + 1):
            next_round: list[JourneyLabel] = []
            for label in previous_round:
                for event in by_origin.get(label.current_location, []):
                    earliest = label.arrival_time
                    if label.event_ids:
                        earliest += timedelta(minutes=boundary.minimum_connection_minutes)
                    if event.departure_at < earliest:
                        continue
                    if not label.event_ids and event.departure_at > boundary.depart_before:
                        continue
                    if boundary.arrive_before and event.arrival_at > boundary.arrive_before:
                        continue
                    if event.risk not in boundary.allowed_risks:
                        continue
                    candidate = self._extend(label, event, boundary)
                    metrics.partial_labels_generated += 1
                    existing = labels_by_location.setdefault(event.destination, [])
                    if any(_label_dominates(other, candidate) for other in existing):
                        metrics.partial_labels_pruned += 1
                        continue
                    dominated = [other for other in existing if _label_dominates(candidate, other)]
                    if dominated:
                        metrics.partial_labels_pruned += len(dominated)
                        existing[:] = [other for other in existing if other not in dominated]
                        next_round[:] = [other for other in next_round if other not in dominated]
                        completed[:] = [other for other in completed if other not in dominated]
                    existing.append(candidate)
                    next_round.append(candidate)
                    if event.destination == boundary.destination:
                        completed.append(candidate)
            previous_round = next_round
            if not previous_round:
                break
        structures = sorted(
            (self._structure(label, schedule) for label in completed),
            key=_structure_sort_key,
        )
        metrics.structural_candidates_generated = len(structures)
        metrics.elapsed_ms = (perf_counter() - started) * 1000
        return structures, metrics

    def search(
        self,
        events: Iterable[ScheduledEvent | dict[str, Any]],
        boundary: SearchBoundary,
        price: PriceFunction,
        verify: VerifyFunction | None = None,
    ) -> StrategySearchResult:
        started = perf_counter()
        structures, metrics = self.generate(events, boundary)
        direct = [item for item in structures if len(item.event_ids) == 1]
        indirect = [item for item in structures if len(item.event_ids) != 1]
        queue = [*direct, *indirect]
        priced: list[PricedCandidate] = []
        unpriced: list[str] = []
        pricing_failures: list[str] = []
        for index, candidate in enumerate(queue):
            if _bound_cannot_enter(
                candidate, priced, boundary.pricing_evidence_class
            ):
                metrics.lower_bound_candidates_pruned += 1
                continue
            if metrics.provider_requests >= boundary.pricing_budget:
                unpriced.extend(item.candidate_id for item in queue[index:])
                break
            remaining_pricing_requests = boundary.pricing_budget - metrics.provider_requests
            observation = _pricing_observation(
                price(candidate, remaining_pricing_requests)
            )
            if observation is None:
                pricing_failures.append(candidate.candidate_id)
                continue
            metrics.provider_requests += observation.requests_made
            metrics.cache_hits += int(observation.cache_hit)
            if metrics.provider_requests > boundary.pricing_budget:
                raise ValueError("pricing callback exceeded the remaining request budget")
            if observation.quote.candidate_id != candidate.candidate_id:
                raise ValueError("pricing result does not match the requested candidate")
            if observation.quote.currency != candidate.currency:
                raise ValueError("pricing result currency does not match the candidate")
            if observation.quote.evidence_class != boundary.pricing_evidence_class:
                raise ValueError("pricing result evidence does not match the boundary")
            priced.append(PricedCandidate(structure=candidate, quote=observation.quote))
        priced_frontier = _priced_frontier(priced)

        verified: list[PricedCandidate] = []
        failures: list[dict[str, str | None]] = []
        verification_requests = 0
        if verify is not None and not unpriced and not pricing_failures:
            for finalist in priced_frontier:
                if verification_requests >= boundary.verification_budget:
                    break
                remaining_verification_requests = (
                    boundary.verification_budget - verification_requests
                )
                observation = _verification_observation(
                    verify(finalist, remaining_verification_requests)
                )
                if observation is None:
                    failures.append({"candidate_id": finalist.structure.candidate_id, "code": None})
                    continue
                verification_requests += observation.requests_made
                if verification_requests > boundary.verification_budget:
                    raise ValueError("verification callback exceeded the remaining request budget")
                metrics.provider_requests += observation.requests_made
                metrics.browser_transitions += observation.browser_transitions
                quote = observation.quote
                if (
                    quote is None
                    or quote.candidate_id != finalist.structure.candidate_id
                    or quote.currency != finalist.quote.currency
                    or not _confirmed_quote(quote, finalist.structure.risk)
                ):
                    failures.append(
                        {
                            "candidate_id": finalist.structure.candidate_id,
                            "code": observation.code or "verification_not_confirmed",
                        }
                    )
                    continue
                verified.append(PricedCandidate(structure=finalist.structure, quote=quote))
        verified_frontier = _priced_frontier(verified)
        metrics.verified_finalists = len(verified_frontier)
        metrics.elapsed_ms = (perf_counter() - started) * 1000
        attempted_verifications = len(verified) + len(failures)
        return StrategySearchResult(
            boundary=boundary,
            structural_frontier=structures,
            priced_frontier=priced_frontier,
            verified_frontier=verified_frontier,
            unpriced_candidate_ids=unpriced,
            pricing_failure_candidate_ids=pricing_failures,
            verification_failures=failures,
            metrics=metrics,
            structural_coverage_complete=True,
            pricing_coverage_complete=not unpriced and not pricing_failures,
            verification_coverage_complete=(
                verify is None
                or (
                    not unpriced
                    and not pricing_failures
                    and attempted_verifications == len(priced_frontier)
                )
            ),
        )

    @staticmethod
    def _extend(
        label: JourneyLabel, event: ScheduledEvent, boundary: SearchBoundary
    ) -> JourneyLabel:
        bound = (
            None
            if label.airfare_lower_bound is None or event.price_lower_bound is None
            else label.airfare_lower_bound + event.price_lower_bound
        )
        ticket = event.ticket_group or "unresolved"
        tickets = label.ticket_boundaries
        if not tickets or tickets[-1] != ticket:
            tickets = (*tickets, ticket)
        elapsed = int((event.arrival_at - boundary.depart_after).total_seconds() // 60)
        ground = label.ground_minutes
        if event.mode != "flight":
            ground += int((event.arrival_at - event.departure_at).total_seconds() // 60)
        baggage = _merge_state(label.baggage_state, event.baggage_state)
        protection = _merge_state(label.connection_protection, event.connection_protection)
        unresolved = set(label.unresolved_evidence)
        if event.baggage_state == "unknown":
            unresolved.add("baggage")
        if event.connection_protection == "unknown" and label.event_ids:
            unresolved.add("connection_protection")
        if event.mode != "flight" and event.price_lower_bound is None:
            unresolved.add("ground_cost")
        if event.ticket_group is None:
            unresolved.add("ticket_structure")
        return JourneyLabel(
            current_location=event.destination,
            arrival_time=event.arrival_at,
            event_ids=(*label.event_ids, event.event_id),
            true_origin=label.true_origin,
            true_destination=label.true_destination,
            ticketed_origin=label.ticketed_origin,
            ticketed_destination=event.destination,
            ticket_boundaries=tickets,
            airfare_lower_bound=bound,
            currency=boundary.currency,
            elapsed_minutes=elapsed,
            ground_minutes=ground,
            stops=max(0, len(label.event_ids)),
            baggage_state=baggage,
            connection_protection=protection,
            hotel_cost=label.hotel_cost,
            risk=max(label.risk, event.risk, key=_risk_order),
            unresolved_evidence=frozenset(unresolved),
            eligibility_key=label.eligibility_key,
        )

    @staticmethod
    def _structure(
        label: JourneyLabel, schedule: list[ScheduledEvent]
    ) -> StructuralCandidate:
        by_id = {event.event_id: event for event in schedule}
        path = [by_id[event_id] for event_id in label.event_ids]
        digest = hashlib.sha256("\0".join(label.event_ids).encode()).hexdigest()[:16]
        return StructuralCandidate(
            candidate_id=f"journey_{digest}",
            event_ids=label.event_ids,
            origin=label.true_origin,
            destination=label.true_destination,
            departure_at=path[0].departure_at,
            arrival_at=path[-1].arrival_at,
            airfare_lower_bound=label.airfare_lower_bound,
            currency=label.currency,
            total_minutes=label.elapsed_minutes,
            ground_minutes=label.ground_minutes,
            stops=label.stops,
            ticket_count=len(label.ticket_boundaries),
            risk=label.risk,
            baggage_state=label.baggage_state,
            connection_protection=label.connection_protection,
            unresolved_evidence=label.unresolved_evidence,
            eligibility_key=label.eligibility_key,
        )


def exhaustive_price(
    candidates: Iterable[StructuralCandidate], price: ReferencePriceFunction
) -> tuple[list[PricedCandidate], int]:
    """Reference implementation for fixture correctness tests and benchmarks."""
    priced = []
    requests = 0
    for candidate in candidates:
        observation = _pricing_observation(price(candidate))
        if observation is None:
            continue
        requests += observation.requests_made
        priced.append(PricedCandidate(structure=candidate, quote=observation.quote))
    return _priced_frontier(priced), requests


def _label_dominates(left: JourneyLabel, right: JourneyLabel) -> bool:
    if (
        left.current_location,
        left.risk,
        left.currency,
        left.eligibility_key,
        left.baggage_state,
        left.connection_protection,
        left.unresolved_evidence,
    ) != (
        right.current_location,
        right.risk,
        right.currency,
        right.eligibility_key,
        right.baggage_state,
        right.connection_protection,
        right.unresolved_evidence,
    ):
        return False
    left_values = (
        left.arrival_time,
        left.airfare_lower_bound,
        left.elapsed_minutes,
        left.ground_minutes,
        left.stops,
        len(left.ticket_boundaries),
        left.hotel_cost,
    )
    right_values = (
        right.arrival_time,
        right.airfare_lower_bound,
        right.elapsed_minutes,
        right.ground_minutes,
        right.stops,
        len(right.ticket_boundaries),
        right.hotel_cost,
    )
    return _dominates_values(left_values, right_values)


def _priced_frontier(candidates: list[PricedCandidate]) -> list[PricedCandidate]:
    frontier = [
        candidate
        for candidate in candidates
        if not any(
            other is not candidate and _priced_dominates(other, candidate)
            for other in candidates
        )
    ]
    return sorted(frontier, key=lambda item: _structure_sort_key(item.structure))


def _priced_dominates(left: PricedCandidate, right: PricedCandidate) -> bool:
    if (
        left.structure.risk,
        left.quote.currency,
        left.quote.evidence_class,
        left.structure.eligibility_key,
    ) != (
        right.structure.risk,
        right.quote.currency,
        right.quote.evidence_class,
        right.structure.eligibility_key,
    ):
        return False
    return _dominates_values(
        (
            left.quote.total_price,
            left.structure.total_minutes,
            left.structure.ground_minutes,
            left.structure.stops,
            left.structure.ticket_count,
        ),
        (
            right.quote.total_price,
            right.structure.total_minutes,
            right.structure.ground_minutes,
            right.structure.stops,
            right.structure.ticket_count,
        ),
    )


def _bound_cannot_enter(
    candidate: StructuralCandidate,
    frontier_source: list[PricedCandidate],
    evidence_class: EvidenceClass,
) -> bool:
    if candidate.airfare_lower_bound is None:
        return False
    for priced in _priced_frontier(frontier_source):
        if (
            priced.structure.risk,
            priced.quote.currency,
            priced.quote.evidence_class,
            priced.structure.eligibility_key,
        ) != (
            candidate.risk,
            candidate.currency,
            evidence_class,
            candidate.eligibility_key,
        ):
            continue
        if _dominates_values(
            (
                priced.quote.total_price,
                priced.structure.total_minutes,
                priced.structure.ground_minutes,
                priced.structure.stops,
                priced.structure.ticket_count,
            ),
            (
                candidate.airfare_lower_bound,
                candidate.total_minutes,
                candidate.ground_minutes,
                candidate.stops,
                candidate.ticket_count,
            ),
            allow_equal=True,
        ):
            return True
    return False


def _dominates_values(
    left: tuple[Any, ...], right: tuple[Any, ...], *, allow_equal: bool = False
) -> bool:
    if any(value is None for value in (*left, *right)):
        return False
    no_worse = all(a <= b for a, b in zip(left, right, strict=True))
    return no_worse and (allow_equal or left != right)


def _pricing_observation(
    value: OfferQuote | PricingObservation | None,
) -> PricingObservation | None:
    if value is None:
        return None
    if isinstance(value, PricingObservation):
        return value
    return PricingObservation(quote=value)


def _verification_observation(
    value: OfferQuote | VerificationObservation | None,
) -> VerificationObservation | None:
    if value is None:
        return None
    if isinstance(value, VerificationObservation):
        return value
    return VerificationObservation(quote=value)


def _confirmed_quote(quote: OfferQuote, risk: JourneyRisk) -> bool:
    expected_scope = (
        "complete_single_ticket" if risk == JourneyRisk.STANDARD else "separate_tickets"
    )
    return (
        quote.evidence_class == EvidenceClass.PROVIDER_FINAL_TOTAL
        and quote.ticket_scope == expected_scope
        and quote.whole_itinerary_matched
        and quote.baggage_complete
        and quote.seller is not None
    )


def _merge_state(left: str, right: str) -> str:
    if left == "not_applicable":
        return right
    if right == "not_applicable":
        return left
    if "unknown" in {left, right}:
        return "unknown"
    if left == right:
        return left
    return "mixed"


def _risk_order(value: JourneyRisk) -> int:
    return {
        JourneyRisk.STANDARD: 0,
        JourneyRisk.SEPARATE_TICKET: 1,
        JourneyRisk.CONTRACT_SENSITIVE: 2,
    }[value]


def _structure_sort_key(candidate: StructuralCandidate) -> tuple[Any, ...]:
    return (
        candidate.risk,
        candidate.currency,
        candidate.arrival_at,
        candidate.total_minutes,
        candidate.stops,
        candidate.ticket_count,
        candidate.event_ids,
    )
