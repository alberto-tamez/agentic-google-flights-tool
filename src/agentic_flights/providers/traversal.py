"""Resume flight-choice traversal within query and browser budgets."""

from __future__ import annotations

import hashlib
import json
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
from typing import Any

from agentic_flights.models import (
    FlightOption,
    SearchCoverage,
    SearchError,
    SearchSpec,
)
from agentic_flights.providers.base import (
    ProviderError,
    ProviderResult,
)
from agentic_flights.providers.budget import note_request
from agentic_flights.providers.parsing import (
    _baggage_meets_requirement,
    _choice_identity,
    _parse_browser_label,
)


class _BudgetExhausted(Exception):
    pass


def _consume_transition(coverage: SearchCoverage, spec: SearchSpec) -> None:
    if (
        spec.max_browser_transitions is not None
        and coverage.browser_transitions >= spec.max_browser_transitions
    ):
        coverage.budget_exhausted = True
        raise _BudgetExhausted
    coverage.browser_transitions += 1
    note_request()


def _search_fingerprint(spec: SearchSpec) -> str:
    data = spec.model_dump(
        mode="json",
        exclude={
            "request_id",
            "continuation",
            "max_results",
            "retrieval_limit",
            "load_more_clicks",
            "candidates_per_stage",
            "max_complete_quotes",
            "max_browser_transitions",
            "stage_candidate_offsets",
        },
    )
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _read_continuation(spec: SearchSpec, kind: str) -> dict[str, Any]:
    state = spec.continuation or {}
    if state and (
        state.get("version") != 1
        or state.get("kind") != kind
        or state.get("fingerprint") != _search_fingerprint(spec)
    ):
        raise ProviderError("invalid_continuation", "Continuation does not match this search.")
    return state


def _continuation(spec: SearchSpec, kind: str, **state: Any) -> dict[str, Any]:
    return {"version": 1, "kind": kind, "fingerprint": _search_fingerprint(spec), **state}


async def _run_bounded_exploration(
    spec: SearchSpec,
    journey_count: int,
    coverage: SearchCoverage,
    discover: Callable[[list[str]], Awaitable[list[str]]],
    finalize: Callable[[list[str]], Awaitable[FlightOption]],
) -> list[FlightOption]:
    state = _read_continuation(spec, "tree")
    pending = list(state.get("pending", [[]]))
    deferred: list[list[str]] = []
    options = [FlightOption.model_validate(item) for item in state.get("options", [])]
    seen_quotes = {_complete_quote_key(option) for option in options}
    expanded = dict(state.get("expanded", {}))
    failed_paths = set(state.get("failed_paths", []))
    while pending:
        prefix = pending.pop(0)
        terminal = len(prefix) == journey_count
        if (
            terminal
            and spec.max_complete_quotes is not None
            and (coverage.branches_attempted >= spec.max_complete_quotes)
        ):
            pending.insert(0, prefix)
            coverage.budget_exhausted = True
            break
        try:
            if terminal:
                coverage.branches_attempted += 1
                option = await finalize(prefix)
                failed_paths.discard(json.dumps(prefix))
                coverage.quotes_completed += 1
                if spec.require_overhead_cabin_bag and not _baggage_meets_requirement(
                    option.baggage, journey_count
                ):
                    coverage.quotes_filtered += 1
                elif _complete_quote_key(option) in seen_quotes:
                    coverage.duplicates += 1
                else:
                    seen_quotes.add(_complete_quote_key(option))
                    options.append(option)
            else:
                labels = await discover(prefix)
                if not labels:
                    raise ProviderError(
                        "branch_empty",
                        "The selected flight produced no onward choices.",
                        retryable=True,
                    )
                failed_paths.discard(json.dumps(prefix))
                coverage.candidates_seen += len(labels)
                key = json.dumps(prefix)
                previous = set(expanded.get(key, []))
                fresh = [
                    label for label in labels if json.dumps(_choice_identity(label)) not in previous
                ]
                expanded[key] = list(previous | {json.dumps(_choice_identity(x)) for x in labels})
                depth = len(prefix)
                offset = (
                    spec.stage_candidate_offsets[depth]
                    if depth < len(spec.stage_candidate_offsets)
                    else 0
                )
                fresh = fresh[offset:] + fresh[:offset]
                if spec.preferred_outbound:
                    target = FlightOption.model_validate(spec.preferred_outbound)
                    target_legs = [
                        leg for leg in target.legs if leg.journey_index == depth
                    ]

                    def preference(label, target_legs=target_legs, depth=depth):
                        if not target_legs:
                            return True
                        segment = spec.requested_segments()[depth]
                        segment_spec = spec.model_copy(
                            update={
                                "origin": segment.origin,
                                "destination": segment.destination,
                                "departure_date": segment.departure_date,
                                "return_date": None,
                                "additional_segments": [],
                            }
                        )
                        candidate = _parse_browser_label(label, segment_spec, 1)
                        if candidate is None:
                            return True

                        def identity(legs):
                            return (
                                legs[0].origin,
                                legs[-1].destination,
                                legs[0].departure_at,
                                legs[-1].arrival_at,
                            )

                        return identity(candidate.legs) != identity(target_legs)

                    fresh.sort(key=preference)
                n = spec.candidates_per_stage
                chosen = fresh if n is None else fresh[:n]
                deferred.extend([*prefix, label] for label in ([] if n is None else fresh[n:]))
                pending[0:0] = [[*prefix, label] for label in chosen]
                if coverage.last_source_truncated:
                    # Revisit this expansion with a larger retrieval window next chunk.
                    deferred.append(prefix)
                    if coverage.source_load_stop_reason == "load_error":
                        failed_paths.add(json.dumps(prefix))
        except CancelledError:
            pending.insert(0, prefix)
            pending.extend(deferred)
            coverage.budget_exhausted = True
            coverage.pending_branches = len(pending)
            coverage.continuation = _continuation(
                spec, "tree", pending=pending, expanded=expanded,
                failed_paths=sorted(failed_paths),
                options=[option.model_dump(mode="json") for option in options],
                retrieval_limit=spec.retrieval_limit, load_more_clicks=spec.load_more_clicks,
            )
            raise
        except _BudgetExhausted:
            pending.insert(0, prefix)
            coverage.budget_exhausted = True
            break
        except Exception as exc:
            _record_branch_error(coverage, exc)
            failed_paths.add(json.dumps(prefix))
            deferred.append(prefix)
    pending.extend(deferred)
    coverage.pending_branches = len(pending)
    coverage.blocked = bool(pending) and all(json.dumps(x) in failed_paths for x in pending)
    coverage.branches_pruned = len(pending)  # legacy count; these branches are now retained
    if pending:
        coverage.continuation = _continuation(
            spec,
            "tree",
            pending=pending,
            expanded=expanded,
            failed_paths=sorted(failed_paths),
            options=[option.model_dump(mode="json") for option in options],
            retrieval_limit=(None if spec.retrieval_limit is None else spec.retrieval_limit * 2),
            load_more_clicks=(None if spec.load_more_clicks is None else spec.load_more_clicks + 1),
        )
    coverage.fully_explored = not (
        pending
        or coverage.budget_exhausted
        or coverage.branch_errors
        or coverage.source_truncated
        or coverage.source_parse_failures
    )
    options.sort(
        key=lambda option: (
            option.price is None,
            option.price if option.price is not None else float("inf"),
            option.duration_minutes,
            option.stops,
            _complete_quote_key(option),
        )
    )
    # Keep every quote; output pagination belongs to the saved-result views.
    return [
        option.model_copy(update={"provider_rank": i}) for i, option in enumerate(options, start=1)
    ]


def _complete_result(options: list[FlightOption], coverage: SearchCoverage) -> ProviderResult:
    if options:
        return ProviderResult("success", options, coverage.browser_transitions, coverage)
    if (
        coverage.fully_explored
        and coverage.quotes_completed
        and coverage.quotes_filtered == coverage.quotes_completed
    ):
        return ProviderResult("empty", [], coverage.browser_transitions, coverage=coverage)
    code = (
        "complete_ticket_search_incomplete"
        if coverage.budget_exhausted or coverage.branch_errors or coverage.branches_pruned
        else "complete_ticket_search_failed"
    )
    raise ProviderError(
        code,
        "No explored branch produced a validated complete ticket quote.",
        retryable=True,
        details={"coverage": coverage.model_dump(mode="json")},
        coverage=coverage,
        requests_made=coverage.browser_transitions,
    )


def _record_branch_error(coverage: SearchCoverage, exc: Exception) -> None:
    coverage.branch_errors += 1
    code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
    coverage.branch_errors_by_code[code] = coverage.branch_errors_by_code.get(code, 0) + 1
    if len(coverage.branch_error_samples) < 3:
        coverage.branch_error_samples.append(
            SearchError(
                code=code,
                message=str(exc) or type(exc).__name__,
                retryable=getattr(exc, "retryable", False),
                details=getattr(exc, "details", None),
            )
        )


def _complete_quote_key(option: FlightOption) -> tuple[Any, ...]:
    return (
        tuple(
            (
                leg.journey_index,
                leg.origin,
                leg.destination,
                leg.departure_at.isoformat(),
                leg.arrival_at.isoformat(),
                leg.airline_name,
            )
            for leg in option.legs
        ),
        option.price,
        option.currency,
        option.booking_provider,
        option.fare_name,
        option.baggage.status if option.baggage else None,
    )
