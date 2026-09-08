from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from reverse_google_flights.filtering import ShortlistSpec, collect_matches
from reverse_google_flights.models import BatchReport, RankedFlight, SearchSpec


def load_report(path: Path) -> tuple[BatchReport, str]:
    raw = path.read_bytes()
    return BatchReport.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def compact_summary(
    source: BatchReport,
    checksum: str,
    artifact: Path,
    *,
    preview_limit: int = 5,
    reference: str | None = None,
) -> dict[str, Any]:
    if preview_limit < 0:
        raise ValueError("preview_limit must be non-negative")
    flattened = _flatten(source)
    grouped: dict[str, list[RankedFlight]] = {}
    for item in flattened:
        grouped.setdefault(item.option.currency, []).append(item)
    for items in grouped.values():
        items.sort(
            key=lambda x: (
                x.option.price is None,
                x.option.price if x.option.price is not None else float("inf"),
                x.option.duration_minutes,
                x.option.stops,
                x.request_id,
            )
        )
    # Round-robin currencies: prices are ordered only within their currency.
    preview = []
    for i in range(preview_limit):
        for currency in sorted(grouped):
            if i < len(grouped[currency]):
                preview.append(grouped[currency][i])
        if len(preview) >= preview_limit:
            break
    specs = {o.request_id: o.search_spec for o in source.outcomes}
    source_ref = reference or str(artifact)
    coverage = [_compact_coverage(outcome) for outcome in source.outcomes[:10]]
    error_codes: dict[str, int] = {}
    for outcome in source.outcomes:
        if outcome.error:
            error_codes[outcome.error.code] = error_codes.get(outcome.error.code, 0) + 1
    return {
        "source": source_ref,
        "dataset_checksum": checksum,
        "counts": source.counts.model_dump(),
        "total_options": len(flattened),
        "coverage": coverage,
        "coverage_outcomes_omitted": max(0, len(source.outcomes) - len(coverage)),
        "error_codes": dict(sorted(error_codes.items())),
        "currencies": sorted(grouped),
        "preview_order": "price_within_currency_round_robin",
        "coverage_totals": {
            "source_candidates_loaded": sum(
                o.coverage.source_candidates_loaded for o in source.outcomes
            ),
            "parse_failures": sum(o.coverage.source_parse_failures for o in source.outcomes),
            "truncated_queries": sum(o.coverage.source_truncated for o in source.outcomes),
            "pending_branches": sum(o.coverage.pending_branches for o in source.outcomes),
            "blocked_queries": sum(o.coverage.blocked for o in source.outcomes),
            "failed_queries": sum(o.status == "error" for o in source.outcomes),
            "branch_errors": sum(o.coverage.branch_errors for o in source.outcomes),
            "retry_attempts": sum(o.coverage.retries for o in source.outcomes),
            "fully_explored": bool(source.outcomes)
            and all(_coverage_complete(o) for o in source.outcomes),
        },
        "preview": [_slim(item, specs.get(item.request_id)) for item in preview[:preview_limit]],
        "next_actions": {
            "filter": f"agentic-flights list {source_ref} --filters FILTERS.json",
            "next_page": f"agentic-flights list {source_ref} --cursor CURSOR",
            "details": f"agentic-flights show {source_ref} RESULT_ID",
            "full": f"agentic-flights summary {source_ref} --full",
        },
    }


def list_page(
    source: BatchReport,
    checksum: str,
    artifact: Path,
    filters: ShortlistSpec,
    *,
    page_size: int,
    cursor: str | None,
    reference: str | None = None,
) -> dict[str, Any]:
    if page_size < 1:
        raise ValueError("page_size must be positive")
    filter_hash = hashlib.sha256(
        json.dumps(
            filters.model_dump(mode="json", exclude={"limit"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    offset = _decode_cursor(cursor, checksum, filter_hash) if cursor else 0
    collection = collect_matches(source, filters)
    source_ref = reference or str(artifact)
    specs = {o.request_id: o.search_spec for o in source.outcomes}
    page = collection.matches[offset : offset + page_size]
    next_offset = offset + len(page)
    next_cursor = (
        _encode_cursor(checksum, filter_hash, next_offset)
        if next_offset < len(collection.matches)
        else None
    )
    return {
        "source": source_ref,
        "dataset_checksum": checksum,
        "offline_network_requests": 0,
        "evaluated": collection.evaluated,
        "total_matches": len(collection.matches),
        "rejected_by": collection.rejected_by,
        "unknown_by": collection.unknown_by,
        "source_truncated": collection.source_truncated,
        "source_parse_failures": collection.source_parse_failures,
        "source_fully_explored": collection.source_fully_explored,
        "offset": offset,
        "page_size": page_size,
        "results": [_slim(item, specs.get(item.request_id)) for item in page],
        "next_cursor": next_cursor,
        "next_actions": {
            "next_page": (
                f"agentic-flights list {source_ref} --filters FILTERS.json "
                f"--page-size {page_size} --cursor {next_cursor}"
                if next_cursor
                else None
            ),
            "details": f"agentic-flights show {source_ref} RESULT_ID",
        },
    }


def show_results(
    source: BatchReport,
    checksum: str,
    artifact: Path,
    result_ids: list[str],
    *,
    reference: str | None = None,
) -> dict[str, Any]:
    if not result_ids:
        raise ValueError("show requires at least one result ID")
    indexed = {_result_id(item): item for item in _flatten(source)}
    specs = {o.request_id: o.search_spec for o in source.outcomes}
    missing = [result_id for result_id in result_ids if result_id not in indexed]
    if missing:
        raise ValueError(f"unknown result IDs: {', '.join(missing)}")
    return {
        "source": reference or str(artifact),
        "dataset_checksum": checksum,
        "offline_network_requests": 0,
        "results": [
            {
                "result_id": result_id,
                "request_id": indexed[result_id].request_id,
                "option": indexed[result_id].option.model_dump(mode="json"),
                "search_spec": (
                    specs[indexed[result_id].request_id].model_dump(
                        mode="json", exclude={"continuation"}
                    )
                    if specs.get(indexed[result_id].request_id)
                    else None
                ),
            }
            for result_id in result_ids
        ],
    }


def _flatten(source: BatchReport) -> list[RankedFlight]:
    return [
        RankedFlight(request_id=outcome.request_id, option=option)
        for outcome in source.outcomes
        for option in outcome.options
    ]


def _result_id(item: RankedFlight) -> str:
    option = item.option
    identity = {
        "request_id": item.request_id,
        "provider_rank": option.provider_rank,
        "legs": [
            {
                "journey": leg.journey_index,
                "origin": leg.origin,
                "destination": leg.destination,
                "departure": leg.departure_at.isoformat(),
                "arrival": leg.arrival_at.isoformat(),
                "airline": leg.airline_name,
            }
            for leg in option.legs
        ],
        "price": option.price,
        "currency": option.currency,
        "provider": option.booking_provider,
        "fare": option.fare_name,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "rf_" + hashlib.sha256(encoded).hexdigest()[:16]


def _slim(item: RankedFlight, spec: SearchSpec | None = None) -> dict[str, Any]:
    option = item.option
    return {
        "result_id": _result_id(item),
        "request_id": item.request_id,
        "route": [f"{leg.origin}-{leg.destination}" for leg in option.legs],
        "price": option.price,
        "currency": option.currency,
        "duration_minutes": option.duration_minutes,
        "total_stops": option.stops,
        "airlines": list(
            dict.fromkeys(leg.airline_name for leg in option.legs if leg.airline_name)
        ),
        "departure_at": option.legs[0].departure_at.isoformat(),
        "ticket_scope": option.ticket_scope,
        "baggage_status": option.baggage.status if option.baggage else "unknown",
        "requested_departure_date": spec.departure_date.isoformat() if spec else None,
        "requested_return_date": (
            spec.return_date.isoformat() if spec and spec.return_date else None
        ),
    }


def _coverage_complete(outcome: Any) -> bool:
    c = outcome.coverage
    return bool(
        outcome.status != "error"
        and c.fully_explored
        and not (
            c.source_truncated
            or c.source_parse_failures
            or c.budget_exhausted
            or c.continuation
            or c.branch_errors
        )
    )


def _compact_coverage(outcome: Any) -> dict[str, Any]:
    coverage = outcome.coverage
    return {
        "request_id": outcome.request_id,
        "status": outcome.status,
        "options": len(outcome.options),
        "source_candidates_loaded": coverage.source_candidates_loaded,
        "source_parse_failures": coverage.source_parse_failures,
        "source_truncated": coverage.source_truncated,
        "source_load_stop_reason": coverage.source_load_stop_reason,
        "quotes_attempted": coverage.branches_attempted,
        "quotes_completed": coverage.quotes_completed,
        "branch_errors": coverage.branch_errors_by_code,
        "browser_transitions": coverage.browser_transitions,
        "budget_exhausted": coverage.budget_exhausted,
        "fully_explored": _coverage_complete(outcome),
    }


def _encode_cursor(checksum: str, filter_hash: str, offset: int) -> str:
    raw = json.dumps(
        {"dataset": checksum, "filters": filter_hash, "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, checksum: str, filter_hash: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if payload["dataset"] != checksum or payload["filters"] != filter_hash:
            raise ValueError("cursor does not match this dataset and filter")
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError("cursor offset cannot be negative")
        return offset
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("cursor "):
            raise
        raise ValueError("invalid cursor") from exc
