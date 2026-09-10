from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agentic_flights.data_layers import (
    BagSelection,
    FlightScheduledEvent,
    GroundScheduledEvent,
    PassengerComposition,
    ProvenanceEnvelope,
    RulesRiskEvidence,
    WholeItineraryOfferKey,
    WholeItineraryOfferRecord,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def provenance(**updates) -> ProvenanceEnvelope:
    values = {
        "source_id": "schedule-feed",
        "canonical_url": "https://data.example.test/snapshot/42",
        "snapshot_id": "42",
        "schema_version": "1",
        "retrieved_at": NOW,
        "source_observed_at": NOW - timedelta(minutes=5),
        "valid_from": NOW,
        "valid_to": NOW + timedelta(days=1),
        "response_checksum": "sha256:abc123",
        "cache_state": "hit",
        "cache_age_seconds": 300,
        "cache_stale": False,
        "license_status": "permitted",
        "records_received": 2,
        "records_accepted": 1,
        "records_rejected": 1,
        "coverage_limitations": ("codeshares omitted",),
        "evidence_level": "primary",
    }
    values.update(updates)
    return ProvenanceEnvelope(**values)


def test_provenance_is_strict_and_accounts_for_every_received_record() -> None:
    envelope = provenance()
    assert envelope.snapshot_id == "42"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProvenanceEnvelope(**{**envelope.model_dump(), "provider_payload": {}})
    with pytest.raises(ValidationError, match="must equal received"):
        provenance(records_rejected=0)
    with pytest.raises(ValidationError, match="upstream_version or snapshot_id"):
        provenance(upstream_version=None, snapshot_id=None)


@pytest.mark.parametrize("field", ["retrieved_at", "valid_from", "valid_to"])
def test_provenance_rejects_naive_datetimes(field: str) -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        provenance(**{field: datetime(2026, 9, 10, 12)})


def test_schedule_events_normalize_iata_and_enforce_temporal_order() -> None:
    event = FlightScheduledEvent(
        provenance=provenance(),
        event_id="IB-3170-20260910",
        origin_iata=" mad ",
        destination_iata="lhr",
        departure_at=NOW,
        arrival_at=NOW + timedelta(hours=2),
    )
    assert (event.origin_iata, event.destination_iata) == ("MAD", "LHR")

    with pytest.raises(ValidationError, match="arrival_at must follow"):
        event.model_copy(update={"arrival_at": NOW - timedelta(minutes=1)}).__class__(
            **event.model_copy(update={"arrival_at": NOW - timedelta(minutes=1)}).model_dump()
        )
    with pytest.raises(ValidationError, match="three-letter IATA"):
        event.__class__(**event.model_copy(update={"destination_iata": "London"}).model_dump())


def test_ground_event_leaves_unknown_cost_and_policy_as_none() -> None:
    event = GroundScheduledEvent(
        provenance=provenance(),
        event_id="rail-1",
        origin_location_id="station-a",
        destination_location_id="station-b",
        departure_at=NOW,
        arrival_at=NOW + timedelta(minutes=30),
        mode="rail",
    )
    assert event.scheduled_cost is None
    assert event.currency is None
    assert event.booking_policy is None

    with pytest.raises(ValidationError, match="supplied together"):
        GroundScheduledEvent(**{**event.model_dump(), "scheduled_cost": Decimal("12.50")})


def test_whole_itinerary_offer_key_preserves_all_quote_dimensions() -> None:
    key = WholeItineraryOfferKey(
        itinerary_event_ids=("outbound", "return"),
        passengers=PassengerComposition(adults=2, children=1),
        cabin="economy",
        fare_product="basic",
        bags=BagSelection(cabin_bags=2, checked_bags=1),
        seller="Example Air",
        currency="eur",
        country="es",
        point_of_sale="example.es",
        priced_at=NOW,
        expires_at=NOW + timedelta(minutes=20),
    )
    offer = WholeItineraryOfferRecord(
        provenance=provenance(),
        offer_id="offer-1",
        key=key,
        ticket_scope="complete_single_ticket",
        total_cost=Decimal("499.99"),
    )

    assert offer.result_scope == "whole_itinerary"
    assert offer.whole_itinerary_matched is True
    assert offer.key.currency == "EUR"
    assert offer.baggage_policy is None
    assert offer.change_policy is None
    assert offer.cancellation_policy is None

    with pytest.raises(ValidationError, match="expires_at must follow"):
        WholeItineraryOfferKey(**{**key.model_dump(), "expires_at": NOW})
    with pytest.raises(ValidationError):
        WholeItineraryOfferRecord(
            **{**offer.model_dump(), "result_scope": "outbound_choice"}
        )


def test_rules_evidence_keeps_unknown_cost_and_policy_null() -> None:
    evidence = RulesRiskEvidence(
        provenance=provenance(),
        evidence_id="rule-1",
        subject_type="offer",
        subject_id="offer-1",
        category="change",
    )
    assert evidence.policy_text is None
    assert evidence.potential_cost is None
    assert evidence.currency is None

    with pytest.raises(ValidationError, match="supplied together"):
        RulesRiskEvidence(
            **{**evidence.model_dump(), "potential_cost": Decimal("100")}
        )
