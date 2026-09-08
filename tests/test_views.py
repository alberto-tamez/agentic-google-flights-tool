from __future__ import annotations

import json

import pytest
from conftest import make_option

from agentic_flights import BatchExecutor
from agentic_flights.cache import FileCache
from agentic_flights.filtering import ShortlistSpec, collect_matches
from agentic_flights.models import (
    BatchCounts,
    BatchReport,
    SearchCoverage,
    SearchOutcome,
)
from agentic_flights.views import compact_summary, list_page, show_results


def test_default_summary_stays_bounded_for_a_thousand_options(tmp_path) -> None:
    source = _large_report(1_000)
    summary = compact_summary(source, "checksum", tmp_path / "full.json")
    rendered = json.dumps(summary)
    assert len(summary["preview"]) == 5
    assert len(rendered) < 8_000
    assert "source_url" not in rendered
    assert summary["total_options"] == 1_000


def test_filtered_pages_have_stable_ids_without_duplicates_or_skips(tmp_path) -> None:
    source = _large_report(30)
    filters = ShortlistSpec(max_price=10, sort_by=["price"], limit=5)
    first = list_page(source, "dataset", tmp_path / "full.json", filters, page_size=5, cursor=None)
    second = list_page(
        source,
        "dataset",
        tmp_path / "full.json",
        filters,
        page_size=3,
        cursor=first["next_cursor"],
    )
    first_ids = [item["result_id"] for item in first["results"]]
    second_ids = [item["result_id"] for item in second["results"]]
    assert first["evaluated"] == 30
    assert first["total_matches"] == 11
    assert len(second["results"]) == 3
    assert not set(first_ids) & set(second_ids)
    assert first_ids == [
        item["result_id"]
        for item in list_page(
            source, "dataset", tmp_path / "full.json", filters, page_size=5, cursor=None
        )["results"]
    ]


def test_cursor_rejects_changed_filters_or_dataset(tmp_path) -> None:
    source = _large_report(20)
    filters = ShortlistSpec(max_price=10)
    page = list_page(source, "dataset", tmp_path / "full.json", filters, page_size=5, cursor=None)
    with pytest.raises(ValueError, match="does not match"):
        list_page(
            source,
            "changed",
            tmp_path / "full.json",
            filters,
            page_size=5,
            cursor=page["next_cursor"],
        )
    with pytest.raises(ValueError, match="invalid cursor"):
        list_page(source, "dataset", tmp_path / "full.json", filters, page_size=5, cursor="bad")


def test_identical_options_with_distinct_source_ranks_have_distinct_ids(tmp_path) -> None:
    source = _large_report(2)
    source.outcomes[0].options[1] = (
        source.outcomes[0].options[0].model_copy(update={"provider_rank": 2})
    )
    page = list_page(
        source,
        "dataset",
        tmp_path / "full.json",
        ShortlistSpec(),
        page_size=2,
        cursor=None,
    )
    result_ids = [item["result_id"] for item in page["results"]]
    assert len(result_ids) == len(set(result_ids)) == 2
    details = show_results(source, "dataset", tmp_path / "full.json", result_ids)
    assert [item["option"]["provider_rank"] for item in details["results"]] == [1, 2]


def test_show_returns_full_evidence_only_for_selected_ids(tmp_path) -> None:
    source = _large_report(3)
    source.outcomes[0].options[0] = (
        source.outcomes[0]
        .options[0]
        .model_copy(update={"source_url": "https://www.google.com/travel/flights/booking?selected"})
    )
    page = list_page(
        source,
        "dataset",
        tmp_path / "full.json",
        ShortlistSpec(),
        page_size=3,
        cursor=None,
    )
    result_id = next(item["result_id"] for item in page["results"] if item["price"] == 0)
    details = show_results(source, "dataset", tmp_path / "full.json", [result_id])
    assert len(details["results"]) == 1
    assert details["results"][0]["option"]["source_url"].endswith("selected")


def _large_report(size: int) -> BatchReport:
    options = [make_option(price=index, rank=index + 1) for index in range(size)]
    return BatchReport(
        outcomes=[
            SearchOutcome(
                request_id="large",
                status="success",
                options=options,
                elapsed_ms=1,
                requests_made=1,
                coverage=SearchCoverage(
                    source_candidates_loaded=size,
                    source_load_stop_reason="ui_exhausted",
                    fully_explored=True,
                ),
            )
        ],
        counts=BatchCounts(
            total=1,
            success=1,
            empty=0,
            error=0,
            unique_searches=1,
            network_requests=1,
            cache_hits=0,
        ),
        ranked_by_currency={},
    )


def test_currency_and_ranked_preview(tmp_path):
    executor = BatchExecutor(FileCache(tmp_path / "cache"))
    report = executor.summarize(
        [
            SearchOutcome(
                request_id="r",
                status="success",
                elapsed_ms=0,
                requests_made=0,
                options=[make_option(90), make_option(1), make_option(100, currency="JPY")],
            )
        ]
    )
    with pytest.raises(ValueError, match="currency"):
        collect_matches(report, ShortlistSpec(max_price=95))
    eur = collect_matches(report, ShortlistSpec(currency="eur", max_price=95))
    assert [x.option.price for x in eur.matches] == [1, 90]
    summary = compact_summary(report, "x", tmp_path / "x")
    assert summary["preview"][0]["price"] == 1
    assert summary["currencies"] == ["EUR", "JPY"]
