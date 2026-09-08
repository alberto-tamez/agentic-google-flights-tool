from __future__ import annotations

import asyncio
from datetime import date

import pytest
from conftest import make_option, make_spec

from agentic_flights.models import BaggageAllowance, BaggageStatus, SearchCoverage
from agentic_flights.provider import (
    ProviderError,
    _BudgetExhausted,
    _complete_result,
    _consume_transition,
    _run_bounded_exploration,
)


def test_bounded_tree_ranks_a_non_first_complete_quote_first() -> None:
    spec = make_spec(
        "tree",
        date(2027, 1, 15),
        destination="LIS",
        return_date=date(2027, 1, 22),
        candidates_per_stage=2,
        max_complete_quotes=4,
        max_results=4,
    )
    children = {
        (): ["outbound-a", "outbound-b"],
        ("outbound-a",): ["return-a", "return-b"],
        ("outbound-b",): ["return-a", "return-b"],
    }
    prices = {
        ("outbound-a", "return-a"): 100,
        ("outbound-a", "return-b"): 90,
        ("outbound-b", "return-a"): 70,
        ("outbound-b", "return-b"): 80,
    }

    async def discover(prefix: list[str]) -> list[str]:
        return children[tuple(prefix)]

    async def finalize(prefix: list[str]):
        return make_option(prices[tuple(prefix)])

    coverage = SearchCoverage()
    options = asyncio.run(_run_bounded_exploration(spec, 2, coverage, discover, finalize))
    assert [option.price for option in options] == [70, 80, 90, 100]
    assert coverage.candidates_seen == 6
    assert coverage.branches_attempted == 4
    assert coverage.quotes_completed == 4
    assert coverage.fully_explored is True


def test_partial_errors_duplicates_and_later_baggage_match_are_preserved() -> None:
    spec = _strict_bag_spec(max_complete_quotes=4)
    coverage = SearchCoverage()
    options = asyncio.run(
        _run_bounded_exploration(spec, 2, coverage, _discover_tree, _finalize_tree)
    )
    result = _complete_result(options, coverage)
    assert result.status == "success"
    assert [option.price for option in result.options] == [90]
    assert coverage.branches_attempted == 4
    assert coverage.quotes_completed == 3
    assert coverage.quotes_filtered == 1
    assert coverage.duplicates == 1
    assert coverage.branch_errors == 1
    assert coverage.branch_errors_by_code == {"parse_error": 1}


def test_truncated_failed_search_is_an_error_and_cannot_be_cached_as_empty() -> None:
    spec = _strict_bag_spec(max_complete_quotes=2)
    coverage = SearchCoverage()
    options = asyncio.run(
        _run_bounded_exploration(spec, 2, coverage, _discover_tree, _finalize_tree)
    )
    assert options == []
    assert coverage.budget_exhausted is True
    assert coverage.branch_errors == 1
    with pytest.raises(ProviderError) as caught:
        _complete_result(options, coverage)
    assert caught.value.code == "complete_ticket_search_incomplete"


def test_transition_budget_never_exceeds_the_configured_limit() -> None:
    spec = _strict_bag_spec(max_complete_quotes=2).model_copy(update={"max_browser_transitions": 2})
    coverage = SearchCoverage()
    _consume_transition(coverage, spec)
    _consume_transition(coverage, spec)
    with pytest.raises(_BudgetExhausted):
        _consume_transition(coverage, spec)
    assert coverage.browser_transitions == 2
    assert coverage.budget_exhausted is True


def _strict_bag_spec(max_complete_quotes: int):
    return make_spec(
        "bags",
        date(2027, 1, 15),
        destination="LIS",
        return_date=date(2027, 1, 22),
        require_overhead_cabin_bag=True,
        overhead_cabin_bags=1,
        candidates_per_stage=2,
        max_complete_quotes=max_complete_quotes,
        max_results=1,
    )


async def _discover_tree(prefix: list[str]) -> list[str]:
    children = {
        (): ["a", "b"],
        ("a",): ["x", "y"],
        ("b",): ["x", "z"],
    }
    return children[tuple(prefix)]


async def _finalize_tree(prefix: list[str]):
    path = tuple(prefix)
    if path == ("a", "x"):
        raise ProviderError("parse_error", "captured branch did not parse")
    if path == ("a", "y"):
        return make_option(100).model_copy(
            update={
                "baggage": BaggageAllowance(status=BaggageStatus.UNKNOWN),
                "booking_provider": "A",
            }
        )
    included = BaggageAllowance(
        status=BaggageStatus.INCLUDED,
        source_text="1 free carry-on. Conditions apply to the entire trip.",
        applies_to_journeys=[0, 1],
    )
    return make_option(90).model_copy(update={"baggage": included, "booking_provider": "B"})


DAY = date(2027, 1, 14)


def test_tree_continuation_retains_cheap_third_candidate():
    spec = make_spec("x", DAY, candidates_per_stage=2)

    async def discover(prefix):
        return ["a", "b", "c"]

    async def finalize(prefix):
        return make_option({"a": 100, "b": 90, "c": 1}[prefix[0]])

    coverage = SearchCoverage()
    first = asyncio.run(_run_bounded_exploration(spec, 1, coverage, discover, finalize))
    assert [x.price for x in first] == [90, 100]
    assert coverage.pending_branches == 1 and not coverage.fully_explored
    next_spec = spec.model_copy(update={"continuation": coverage.continuation})
    second_coverage = SearchCoverage()
    second = asyncio.run(
        _run_bounded_exploration(next_spec, 1, second_coverage, discover, finalize)
    )
    assert [x.price for x in second] == [1, 90, 100]
    assert second_coverage.fully_explored and second_coverage.continuation is None
    with pytest.raises(ProviderError, match="Continuation"):
        asyncio.run(
            _run_bounded_exploration(
                next_spec.model_copy(update={"currency": "USD"}),
                1,
                SearchCoverage(),
                discover,
                finalize,
            )
        )


def test_truncated_source_never_complete():
    async def discover(prefix):
        return ["a"]

    async def finalize(prefix):
        return make_option()

    coverage = SearchCoverage(source_truncated=True, source_parse_failures=1)
    asyncio.run(_run_bounded_exploration(make_spec("x", DAY), 1, coverage, discover, finalize))
    assert not coverage.fully_explored


def test_failed_frontier_blocks_until_explicit_retry():
    spec = make_spec("blocked", DAY)

    async def discover(prefix):
        return ["flight"]

    async def fail(prefix):
        raise RuntimeError("unavailable")

    coverage = SearchCoverage()
    assert asyncio.run(_run_bounded_exploration(spec, 1, coverage, discover, fail)) == []
    assert coverage.blocked and coverage.continuation

    async def succeed(prefix):
        return make_option(35)

    next_coverage = SearchCoverage()
    options = asyncio.run(
        _run_bounded_exploration(
            spec.model_copy(update={"continuation": coverage.continuation}),
            1,
            next_coverage,
            discover,
            succeed,
        )
    )
    assert options[0].price == 35 and next_coverage.fully_explored and not next_coverage.blocked
