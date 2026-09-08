from agentic_flights.google_query import FlightQuery, Passengers, create_query


def test_internal_query_matches_fast_flights_simple_vector() -> None:
    query = create_query(
        flights=[FlightQuery(date="2027-01-02", from_airport="MAD", to_airport="NRT")],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        language="en-US",
        currency="EUR",
    )
    assert query.to_str() == "GhoSCjIwMjctMDEtMDJqBRIDTUFEcgUSA05SVEIBAUgBmAEC"


def test_internal_query_matches_fast_flights_complete_filter_vector() -> None:
    query = create_query(
        flights=[
            FlightQuery(
                date="2027-01-02",
                from_airport="JFK",
                to_airport="LHR",
                max_stops=1,
                airlines=["BA", "ONEWORLD"],
                earliest_departure_hour=7,
                latest_departure_hour=18,
                earliest_arrival_hour=9,
                latest_arrival_hour=23,
                max_duration_minutes=720,
                connecting_airports=["DUB"],
                min_layover_minutes=60,
                max_layover_minutes=240,
                less_emissions_only=True,
            ),
            FlightQuery(
                date="2027-01-12", from_airport="LHR", to_airport="JFK", max_stops=1
            ),
        ],
        trip="round-trip",
        seat="business",
        passengers=Passengers(
            adults=2, children=1, infants_in_seat=1, infants_on_lap=1
        ),
        language="en-GB",
        currency="GBP",
        max_price=2500,
        carry_on_bags=2,
        checked_bags=1,
        hide_separate_and_self_transfer=True,
        exclude_basic_economy=True,
    )
    assert query.to_str() == (
        "GkUSCjIwMjctMDEtMDIoATICQkEyCE9ORVdPUkxEQAdIElAJWBdg0AVqBRIDSkZLcgUSA0xI"
        "UnoDRFVCiAE8kAHwAZoBAQEaHBIKMjAyNy0wMS0xMigBagUSA0xIUnIFEgNKRktCBQEBAgME"
        "SANgxBNqBBACGAGIAQGYAQHIAQE="
    )
