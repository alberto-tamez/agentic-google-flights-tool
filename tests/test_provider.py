from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from conftest import make_spec

from reverse_google_flights.models import BaggageStatus
from reverse_google_flights.provider import (
    ProviderError,
    _baggage_meets_requirement,
    _has_wrb_payload,
    _parse_baggage_allowance,
    _parse_browser_label,
    _parse_complete_itinerary,
    _parse_complete_round_trip,
    _parse_selected_booking_option,
)


def test_null_wrb_envelope_is_not_a_trustworthy_empty() -> None:
    body = ')]}\'\n\n[["wrb.fr",null,null,null,null,[13]],["di",37]]'
    assert _has_wrb_payload(body) is False


def test_parses_and_deduplicates_browser_result_shape() -> None:
    spec = make_spec(
        "browser",
        date(2026, 11, 28),
        destination="LIS",
        return_date=date(2026, 12, 6),
    )
    label = (
        "From 40 euros round trip total. Nonstop flight with Ryanair. "
        "Leaves Adolfo Suárez Madrid-Barajas Airport at 4:15 PM on Saturday, November 28 "
        "and arrives at Humberto Delgado Airport at 4:40 PM on Saturday, November 28. "
        "Total duration 1 hr 25 min. Select flight"
    )
    option = _parse_browser_label(label, spec, 1)
    assert option is not None
    assert (option.price, option.currency, option.duration_minutes, option.stops) == (
        40,
        "EUR",
        85,
        0,
    )
    assert option.result_scope == "outbound_choice"
    assert option.legs[0].airline_name == "Ryanair"
    assert option.legs[0].departure_at == option.legs[0].departure_at.replace(
        year=2026, month=11, day=28, hour=16, minute=15
    )


def test_browser_one_way_is_a_complete_itinerary() -> None:
    spec = make_spec("one-way", date(2026, 11, 28), destination="LIS")
    label = (
        "From 29 euros one way. 1 stop flight with Iberia. Leaves Madrid Airport at "
        "8:00 AM on Saturday, November 28 and arrives at Lisbon Airport at 11:10 AM "
        "on Saturday, November 28. Total duration 3 hr 10 min. Select flight"
    )
    option = _parse_browser_label(label, spec, 1)
    assert option is not None
    assert option.result_scope == "complete_itinerary"
    assert option.ticket_scope == "partial_or_unknown"
    assert option.price_provenance == "provider_search_result"
    assert (option.duration_minutes, option.stops) == (190, 1)


def test_booking_body_produces_complete_round_trip_with_provider_total() -> None:
    spec = make_spec(
        "round-trip",
        date(2027, 1, 15),
        destination="LIS",
        return_date=date(2027, 1, 22),
        overhead_cabin_bags=1,
        require_overhead_cabin_bag=True,
    )
    outbound = (
        "From 59 euros round trip total. Nonstop flight with Ryanair. "
        "Leaves Adolfo Suárez Madrid-Barajas Airport at 4:15 PM on Friday, January 15 "
        "and arrives at Humberto Delgado Airport at 4:40 PM on Friday, January 15. "
        "Total duration 1 hr 25 min. Select flight"
    )
    returning = (
        "From 59 euros round trip total. Nonstop flight with Ryanair. "
        "Leaves Humberto Delgado Airport at 6:10 PM on Friday, January 22 "
        "and arrives at Adolfo Suárez Madrid-Barajas Airport at 8:30 PM on Friday, January 22. "
        "Total duration 1 hr 20 min. Select flight"
    )
    body = """
    Itinerary summary
    €59
    Lowest total price
    Selected flights
    MAD–LIS
    LIS–MAD
    Booking options
    Book with IberiaAirline
    Hide options
    Economy Basic
    €59
    1 free carry-on
    Continue
    Fare and baggage conditions apply to the entire trip.
    """
    observed_at = datetime(2026, 9, 7, 12, tzinfo=UTC)

    option = _parse_complete_round_trip(outbound, returning, body, spec, observed_at)

    assert option.price == 59
    assert option.price_provenance == "provider_final_total"
    assert option.ticket_scope == "complete_single_ticket"
    assert option.result_scope == "complete_itinerary"
    assert option.duration_minutes == 165
    assert option.stops == 0
    assert option.observed_at == observed_at
    assert [(leg.journey_index, leg.origin, leg.destination) for leg in option.legs] == [
        (0, "MAD", "LIS"),
        (1, "LIS", "MAD"),
    ]
    assert option.baggage.status == BaggageStatus.INCLUDED
    assert option.baggage.bag_type == "overhead_cabin_bag"
    assert option.baggage.applies_to_journeys == [0, 1]
    assert option.booking_provider == "Iberia"
    assert option.fare_name == "Economy Basic"
    assert _baggage_meets_requirement(option.baggage, 2)


def test_booking_body_produces_complete_open_jaw_ticket() -> None:
    spec = make_spec(
        "open-jaw",
        date(2026, 11, 12),
        destination="CDG",
        additional_segments=[
            {"origin": "AMS", "destination": "MAD", "departure_date": "2026-11-19"}
        ],
        overhead_cabin_bags=1,
        require_overhead_cabin_bag=True,
    )
    first = (
        "From 179 euros total. Nonstop flight with Air France. Leaves Madrid Airport at "
        "12:35 PM on Thursday, November 12 and arrives at Paris Airport at 2:45 PM on "
        "Thursday, November 12. Total duration 2 hr 10 min. Select flight"
    )
    second = (
        "From 179 euros total. Nonstop flight with KLM. Leaves Amsterdam Airport at "
        "7:20 AM on Thursday, November 19 and arrives at Madrid Airport at 9:55 AM on "
        "Thursday, November 19. Total duration 2 hr 35 min. Select flight"
    )
    body = """
    Itinerary summary
    Multi-city trip
    €179
    Selected flights
    MAD–CDG
    AMS–MAD
    1 free carry-on
    Baggage conditions apply to your entire trip.
    Booking options
    Book with KLMAirline
    €179
    Continue
    """
    option = _parse_complete_itinerary(
        [first, second],
        body,
        "https://www.google.com/travel/flights/booking?test",
        spec,
        datetime(2026, 9, 7, 12, tzinfo=UTC),
    )
    assert option.price == 179
    assert option.booking_provider == "KLM"
    assert option.ticket_scope == "complete_single_ticket"
    assert option.baggage.status == BaggageStatus.INCLUDED
    assert option.baggage.applies_to_journeys == [0, 1]
    assert [(leg.origin, leg.destination) for leg in option.legs] == [
        ("MAD", "CDG"),
        ("AMS", "MAD"),
    ]


def test_baggage_evidence_does_not_leak_from_a_more_expensive_fare() -> None:
    spec = make_spec(
        "round-trip",
        date(2027, 1, 15),
        destination="LIS",
        return_date=date(2027, 1, 22),
    )
    outbound = (
        "From 59 euros round trip total. Nonstop flight with Iberia. Leaves Madrid Airport at "
        "7:15 AM on Friday, January 15 and arrives at Lisbon Airport at 7:40 AM on Friday, "
        "January 15. Total duration 1 hr 25 min. Select flight"
    )
    returning = (
        "From 59 euros round trip total. Nonstop flight with Iberia. Leaves Lisbon Airport at "
        "4:45 PM on Friday, January 22 and arrives at Madrid Airport at 7:10 PM on Friday, "
        "January 22. Total duration 1 hr 25 min. Select flight"
    )
    body = """
    Itinerary summary
    €59
    Selected flights
    MAD–LIS
    LIS–MAD
    Booking options
    Book with IberiaAirline
    Basic
    €59
    Carry-on available for a fee
    Continue
    Comfort
    €127
    1 free carry-on
    Continue
    Fare and baggage conditions apply to the entire trip.
    """
    option = _parse_complete_round_trip(
        outbound, returning, body, spec, datetime(2026, 9, 7, 12, tzinfo=UTC)
    )
    assert option.fare_name == "Basic"
    assert option.baggage.status == BaggageStatus.EXTRA_COST


def test_booking_evidence_is_scoped_to_the_matching_seller() -> None:
    body = """
    Booking options
    Book with Seller A Airline
    Basic
    €120
    Carry-on available for an additional cost
    Continue
    Book with Seller B Airline
    Flex
    €100
    1 free carry-on bag
    Fare and baggage conditions apply to the entire trip.
    Continue
    """
    provider, fare, evidence = _parse_selected_booking_option(body, 100, "EUR")
    baggage = _parse_baggage_allowance(evidence, 2)
    assert (provider, fare) == ("Seller B", "Flex")
    assert baggage.status == BaggageStatus.INCLUDED
    assert "additional cost" not in evidence


def test_ambiguous_matching_sellers_raise_instead_of_mixing_evidence() -> None:
    body = """
    Booking options
    Book with Seller A Airline
    Basic
    €100
    Carry-on available for an additional cost
    Continue
    Book with Seller B Airline
    Flex
    €100
    1 free carry-on bag
    Continue
    """
    with pytest.raises(ProviderError, match="exactly one booking provider"):
        _parse_selected_booking_option(body, 100, "EUR")


def test_whole_trip_evidence_does_not_leak_from_another_seller() -> None:
    body = """
    Booking options
    Book with Seller A Airline
    Basic
    €100
    1 free carry-on bag
    Continue
    Book with Seller B Airline
    Flex
    €120
    Carry-on available for an additional cost
    Fare and baggage conditions apply to the entire trip.
    Continue
    """
    provider, _, evidence = _parse_selected_booking_option(body, 100, "EUR")
    baggage = _parse_baggage_allowance(evidence, 2)
    assert provider == "Seller A"
    assert baggage.status == BaggageStatus.UNKNOWN
    assert baggage.applies_to_journeys == []


def test_baggage_filter_rejects_unknown_and_extra_cost_evidence() -> None:
    unknown = _parse_baggage_allowance("Booking options available", journey_count=2)
    extra = _parse_baggage_allowance(
        "Carry-on bag available for an additional cost. Conditions apply to the entire trip.",
        journey_count=2,
    )
    assert unknown.status == BaggageStatus.UNKNOWN
    assert extra.status == BaggageStatus.EXTRA_COST
    assert not _baggage_meets_requirement(unknown, 2)
    assert not _baggage_meets_requirement(extra, 2)


def test_baggage_inclusion_without_whole_trip_evidence_remains_unknown() -> None:
    baggage = _parse_baggage_allowance("1 free carry-on bag", journey_count=2)
    assert baggage.status == BaggageStatus.UNKNOWN
    assert baggage.applies_to_journeys == []
    assert not _baggage_meets_requirement(baggage, 2)


def test_carry_on_count_without_explicit_inclusion_remains_unknown() -> None:
    baggage = _parse_baggage_allowance(
        "1 carry-on bag. Baggage conditions apply to your entire trip.", journey_count=2
    )
    assert baggage.status == BaggageStatus.UNKNOWN
    assert not _baggage_meets_requirement(baggage, 2)


DAY = date(2027, 1, 14)


def test_actual_whole_hour_fare():
    raw = (
        "From 35 euros.This price does not include overhead bin access. Nonstop flight "
        "with Vueling. Leaves Barcelona at 3:40 PM on Thursday, January 14 and arrives "
        "at Paris at 5:40 PM on Thursday, January 14. Total duration 2 hr. Select flight"
    )
    option = _parse_browser_label(raw, make_spec("x", DAY), 1)
    assert option.price == 35 and option.duration_minutes == 120


def test_unknown_price_is_preserved():
    label = (
        "Total price is unavailable. Nonstop flight with Iberia. Leaves Madrid at 3:40 PM "
        "on Thursday, January 14 and arrives at London at 5:40 PM on Thursday, January 14. "
        "Total duration 2 hr. Select flight"
    )
    option = _parse_browser_label(label, make_spec("x", DAY), 1)
    assert option is not None and option.price is None
