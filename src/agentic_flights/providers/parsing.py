"""Parse Google Flights labels, booking totals, and baggage evidence."""

from __future__ import annotations

import re
from base64 import urlsafe_b64decode
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agentic_flights.models import (
    BaggageAllowance,
    BaggageStatus,
    FlightLeg,
    FlightOption,
    FlightSegmentIdentity,
    SearchCoverage,
    SearchSpec,
)
from agentic_flights.providers.base import (
    ProviderError,
)

_RESULT_RE = re.compile(
    r"^(?:From (?P<price>[\d.,]+) .+?\.|Total price is unavailable\.)\s*"
    r"(?P<stops>Nonstop|\d+ stops?) flight with (?P<airline>.+?)\.\s*"
    r"(?:Operated by .+?\.\s*)?Leaves (?P<origin_name>.+?) at "
    r"(?P<departure_time>\d{1,2}:\d{2} [AP]M) on (?P<departure_date>.+?) "
    r"and arrives at (?P<destination_name>.+?) at "
    r"(?P<arrival_time>\d{1,2}:\d{2} [AP]M) on (?P<arrival_date>.+?)\.\s*"
    r"Total duration (?:(?P<hours>\d+) hr)?\s*(?:(?P<minutes>\d+) min)?\.",
)


def _choice_identity(label: str) -> tuple[str, ...]:
    match = _RESULT_RE.search(" ".join(label.split()))
    if match is None:
        return (" ".join(label.split()),)
    return tuple(
        match[name]
        for name in (
            "stops",
            "airline",
            "origin_name",
            "departure_time",
            "departure_date",
            "destination_name",
            "arrival_time",
            "arrival_date",
            "hours",
            "minutes",
        )
    )


def _parse_discovery_options(
    labels: list[str],
    spec: SearchSpec,
    requested_segments: list[Any],
    coverage: SearchCoverage,
    observed_at: datetime,
) -> list[FlightOption]:
    first = requested_segments[0]
    first_segment_spec = spec.model_copy(
        update={
            "origin": first.origin,
            "destination": first.destination,
            "departure_date": first.departure_date,
            "return_date": None,
            "additional_segments": [],
        }
    )
    options: list[FlightOption] = []
    for rank, label in enumerate(labels, start=1):
        option = _parse_browser_label(label, first_segment_spec, rank, observed_at)
        if option is None:
            coverage.source_parse_failures += 1
            if len(coverage.source_parse_failure_samples) < 3:
                coverage.source_parse_failure_samples.append(label[:1_000])
            continue
        options.append(option)
    if len(requested_segments) > 1:
        options = [
            option.model_copy(
                update={
                    "result_scope": "outbound_choice",
                    "ticket_scope": "partial_or_unknown",
                    "price_provenance": "provider_search_result",
                }
            )
            for option in options
        ]
    return options


def _parse_browser_label(
    label: str,
    spec: SearchSpec,
    rank: int,
    observed_at: datetime | None = None,
) -> FlightOption | None:
    match = _RESULT_RE.search(" ".join(label.split()))
    if match is None or not (match["hours"] or match["minutes"]):
        return None
    departure_at = _parse_local_datetime(
        match["departure_date"], match["departure_time"], spec.departure_date
    )
    arrival_at = _parse_local_datetime(
        match["arrival_date"], match["arrival_time"], spec.departure_date
    )
    if arrival_at < departure_at:
        arrival_at = arrival_at.replace(year=arrival_at.year + 1)
    hours = int(match["hours"] or 0)
    duration = hours * 60 + int(match["minutes"] or 0)
    stops = 0 if match["stops"] == "Nonstop" else int(match["stops"].split()[0])
    price = float(match["price"].replace(",", "")) if match["price"] else None
    return FlightOption(
        provider_rank=rank,
        price=price,
        currency=spec.currency,
        duration_minutes=duration,
        stops=stops,
        result_scope=("outbound_choice" if spec.return_date else "complete_itinerary"),
        observed_at=observed_at or datetime.now(UTC),
        legs=[
            FlightLeg(
                journey_index=0,
                airline_name=match["airline"],
                origin=spec.origin,
                destination=spec.destination,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_minutes=duration,
            )
        ],
    )


def _parse_complete_round_trip(
    outbound_label: str,
    return_label: str,
    booking_body: str,
    spec: SearchSpec,
    observed_at: datetime,
) -> FlightOption:
    return _parse_complete_itinerary(
        [outbound_label, return_label], booking_body, None, spec, observed_at
    )


def _parse_complete_itinerary(
    labels: list[str],
    booking_body: str,
    source_url: str | None,
    spec: SearchSpec,
    observed_at: datetime,
) -> FlightOption:
    normalized_body = " ".join(booking_body.split())
    required_sections = ("Itinerary summary", "Selected flights", "Booking options")
    missing_sections = [section for section in required_sections if section not in normalized_body]
    if missing_sections:
        raise ProviderError(
            "browser_parse_error",
            f"The final booking page is missing: {', '.join(missing_sections)}.",
            requests_made=1,
        )
    requested = spec.requested_segments()
    if len(labels) != len(requested):
        raise ProviderError(
            "browser_parse_error",
            "The selected journey count does not match the requested itinerary.",
            requests_made=1,
        )
    journeys: list[FlightOption] = []
    for index, (label, segment) in enumerate(zip(labels, requested, strict=True)):
        segment_spec = spec.model_copy(
            update={
                "origin": segment.origin,
                "destination": segment.destination,
                "departure_date": segment.departure_date,
                "return_date": None,
                "additional_segments": [],
            }
        )
        parsed = _parse_browser_label(label, segment_spec, index + 1, observed_at)
        if parsed is None:
            raise ProviderError(
                "browser_parse_error",
                f"Selected journey {index + 1} did not match the English result schema.",
                requests_made=1,
            )
        if parsed.legs[0].departure_at.date() != segment.departure_date:
            raise ProviderError(
                "browser_parse_error",
                f"Selected journey {index + 1} has a different departure date.",
                requests_made=1,
            )
        journeys.append(parsed)
    _validate_booking_routes(booking_body, requested)
    final_total = _parse_provider_final_total(booking_body, spec.currency)
    booking_provider, fare_name, fare_evidence = _parse_selected_booking_option(
        booking_body, final_total, spec.currency
    )
    baggage = _parse_baggage_allowance(fare_evidence, journey_count=len(journeys))
    identity_segments = _parse_booking_segment_identities(source_url, requested)
    legs = [
        leg.model_copy(update={"journey_index": index})
        for index, journey in enumerate(journeys)
        for leg in journey.legs
    ]
    return FlightOption(
        provider_rank=1,
        price=final_total,
        currency=spec.currency,
        duration_minutes=sum(journey.duration_minutes for journey in journeys),
        stops=sum(journey.stops for journey in journeys),
        result_scope="complete_itinerary",
        ticket_scope="complete_single_ticket",
        price_provenance="provider_final_total",
        observed_at=observed_at,
        baggage=baggage,
        booking_provider=booking_provider,
        fare_name=fare_name,
        source_url=source_url,
        identity_segments=identity_segments,
        legs=legs,
    )


def _parse_booking_segment_identities(
    source_url: str | None, requested: list[Any] | None = None
) -> list[FlightSegmentIdentity]:
    """Read exact selected segments from the booking URL's protobuf query."""
    if not source_url:
        return []
    values = parse_qs(urlsplit(source_url).query).get("tfs", [])
    if len(values) != 1:
        return []
    try:
        encoded = values[0].replace(" ", "+")
        payload = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        journeys = [
            value
            for field, wire, value in _protobuf_fields(payload)
            if (field, wire) == (3, 2)
        ]
        parsed: list[FlightSegmentIdentity] = []
        for journey_index, journey in enumerate(journeys):
            segments = [
                value
                for field, wire, value in _protobuf_fields(journey)
                if (field, wire) == (4, 2)
            ]
            for segment in segments:
                fields = {
                    field: value.decode("ascii")
                    for field, wire, value in _protobuf_fields(segment)
                    if wire == 2 and field in {1, 2, 3, 5, 6}
                }
                parsed.append(
                    FlightSegmentIdentity(
                        journey_index=journey_index,
                        origin=fields[1],
                        departure_date=date.fromisoformat(fields[2]),
                        destination=fields[3],
                        airline_code=fields.get(5),
                        flight_number=fields.get(6),
                    )
                )
    except (KeyError, UnicodeDecodeError, ValueError):
        return []
    if not parsed:
        return []
    if requested is None:
        return parsed
    if len(journeys) != len(requested):
        return []
    for index, segment in enumerate(requested):
        selected = [item for item in parsed if item.journey_index == index]
        if (
            not selected
            or selected[0].origin != segment.origin
            or selected[-1].destination != segment.destination
            or selected[0].departure_date != segment.departure_date
        ):
            return []
    return parsed


def _protobuf_fields(payload: bytes) -> list[tuple[int, int, int | bytes]]:
    """Decode only the protobuf wire types used by Google Flights booking URLs."""
    fields: list[tuple[int, int, int | bytes]] = []
    offset = 0
    while offset < len(payload):
        key, offset = _read_varint(payload, offset)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, offset = _read_varint(payload, offset)
        elif wire == 1:
            value, offset = payload[offset : offset + 8], offset + 8
        elif wire == 2:
            size, offset = _read_varint(payload, offset)
            value, offset = payload[offset : offset + size], offset + size
            if len(value) != size:
                raise ValueError("truncated protobuf field")
        elif wire == 5:
            value, offset = payload[offset : offset + 4], offset + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        if offset > len(payload):
            raise ValueError("truncated protobuf field")
        fields.append((field, wire, value))
    return fields


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload) and shift < 70:
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _parse_selected_booking_option(
    body: str, final_total: float, currency: str
) -> tuple[str | None, str | None, str]:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    try:
        booking_index = next(
            index for index, line in enumerate(lines) if line.lower() == "booking options"
        )
    except StopIteration:
        raise ProviderError(
            "browser_parse_error",
            "The booking page has no booking options section.",
            requests_made=1,
        ) from None
    provider_indexes = [
        index
        for index in range(booking_index + 1, len(lines))
        if lines[index].startswith("Book with ")
    ]
    matching_blocks: list[tuple[int, int, int]] = []
    for position, start in enumerate(provider_indexes):
        end = provider_indexes[position + 1] if position + 1 < len(provider_indexes) else len(lines)
        price_indexes = [
            index
            for index in range(start + 1, end)
            if _line_has_amount(lines[index], final_total, currency)
        ]
        if price_indexes:
            matching_blocks.append((start, end, price_indexes[0]))
    if len(matching_blocks) != 1:
        raise ProviderError(
            "browser_parse_error",
            "The final total does not identify exactly one booking provider option.",
            requests_made=1,
        )
    provider_index, provider_end, price_index = matching_blocks[0]
    provider = lines[provider_index].removeprefix("Book with ").removesuffix("Airline").strip()
    fare_name = None
    fare_lines: list[str] = []
    ignored = {"Hide options", "View options", "Booking options"}
    preceding = lines[price_index - 1] if price_index > provider_index else ""
    if preceding not in ignored and not preceding.startswith("Book with "):
        fare_name = preceding
    end = next(
        (index for index in range(price_index + 1, provider_end) if lines[index] == "Continue"),
        min(price_index + 12, provider_end - 1),
    )
    fare_lines = lines[max(provider_index, price_index - 2) : end + 1]
    selected_summary = lines[:booking_index]
    matched_provider = lines[provider_index:provider_end]
    selected_carry_on = [line for line in selected_summary if "carry-on" in line.lower()]
    whole_trip = [
        line
        for line in [*selected_summary, *matched_provider]
        if re.search(r"(?:entire|whole) trip", line, re.IGNORECASE)
        and re.search(r"(?:fare|baggage)", line, re.IGNORECASE)
    ]
    evidence = list(dict.fromkeys([*selected_carry_on, *fare_lines, *whole_trip]))
    return provider, fare_name, "\n".join(evidence)


def _line_has_amount(line: str, amount: float, currency: str) -> bool:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
    match = re.fullmatch(rf"{re.escape(symbol)}\s*([\d.,]+)", line, re.IGNORECASE)
    return bool(match and float(match.group(1).replace(",", "")) == amount)


def _validate_booking_routes(body: str, segments: list[Any]) -> None:
    normalized = body.replace("-", "–")
    missing = [
        f"{segment.origin}–{segment.destination}"
        for segment in segments
        if f"{segment.origin}–{segment.destination}" not in normalized
    ]
    if missing:
        raise ProviderError(
            "browser_parse_error",
            f"The booking summary is missing requested routes: {', '.join(missing)}.",
            requests_made=1,
        )


def _parse_provider_final_total(body: str, currency: str) -> float:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    try:
        start = next(index for index, line in enumerate(lines) if line == "Itinerary summary")
        end = next(
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line == "Selected flights"
        )
    except StopIteration:
        raise ProviderError(
            "browser_parse_error",
            "The booking page has no parseable itinerary summary.",
            requests_made=1,
        ) from None
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency)
    amounts: list[float] = []
    for line in lines[start + 1 : end]:
        patterns = [rf"{re.escape(currency)}\s*([\d.,]+)"]
        if symbol:
            patterns.insert(0, rf"{re.escape(symbol)}\s*([\d.,]+)")
        for pattern in patterns:
            match = re.fullmatch(pattern, line, re.IGNORECASE)
            if match:
                amounts.append(float(match.group(1).replace(",", "")))
                break
    unique = list(dict.fromkeys(amounts))
    if len(unique) == 1:
        return unique[0]
    raise ProviderError(
        "browser_parse_error",
        "The itinerary summary does not contain exactly one final total.",
        requests_made=1,
    )


def _parse_baggage_allowance(body: str, journey_count: int) -> BaggageAllowance:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    carry_on_lines = [line for line in lines if re.search(r"carry-on", line, re.IGNORECASE)]
    applies_lines = [
        line
        for line in lines
        if re.search(r"(?:entire|whole) trip", line, re.IGNORECASE)
        and re.search(r"(?:fare|baggage)", line, re.IGNORECASE)
    ]
    evidence = list(dict.fromkeys([*carry_on_lines, *applies_lines]))
    source_text = " ".join(evidence) or None
    applies = list(range(journey_count)) if applies_lines else []
    included = any(
        re.search(
            r"(?:\b1\b|\bone\b)\s+(?:(?:free|included)\s+)(?:overhead\s+)?carry-on",
            line,
            re.IGNORECASE,
        )
        and not re.search(r"(?:fee|extra|additional|purchase|not included)", line, re.IGNORECASE)
        for line in carry_on_lines
    )
    extra_cost = any(
        re.search(r"(?:fee|extra cost|additional cost|purchase|required)", line, re.IGNORECASE)
        for line in carry_on_lines
    )
    status = BaggageStatus.UNKNOWN
    if included and len(applies) == journey_count:
        status = BaggageStatus.INCLUDED
    elif extra_cost:
        status = BaggageStatus.EXTRA_COST
    return BaggageAllowance(
        status=status,
        source_text=source_text,
        applies_to_journeys=applies,
    )


def _baggage_meets_requirement(
    baggage: BaggageAllowance | None,
    journey_count: int,
) -> bool:
    return bool(
        baggage is not None
        and baggage.status == BaggageStatus.INCLUDED
        and baggage.applies_to_journeys == list(range(journey_count))
    )


def _parse_local_datetime(text_date: str, text_time: str, expected: date) -> datetime:
    parsed = datetime.strptime(f"{text_date} {text_time}", "%A, %B %d %I:%M %p")
    candidates = [parsed.replace(year=expected.year + offset) for offset in (-1, 0, 1)]
    return min(candidates, key=lambda value: abs(value.date() - expected))
