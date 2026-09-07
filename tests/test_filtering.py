from __future__ import annotations

import json

from conftest import make_option

from reverse_google_flights.cli import run
from reverse_google_flights.filtering import ShortlistSpec, build_shortlist
from reverse_google_flights.models import (
    BatchCounts,
    BatchReport,
    SearchCoverage,
    SearchOutcome,
)


def test_local_shortlist_filters_and_ranks_without_changing_source() -> None:
    source = _source_report([make_option(120), make_option(80, duration=140), make_option(90)])
    original = source.model_dump_json()
    result = build_shortlist(
        source,
        ShortlistSpec(max_price=100, max_duration_minutes=100, airlines=["Iberia"], limit=2),
    )
    assert [item.option.price for item in result.shortlist] == [90]
    assert result.evaluated == 3
    assert result.rejected_by == {"max_duration_minutes": 1, "max_price": 1}
    assert source.model_dump_json() == original


def test_missing_baggage_never_satisfies_a_hard_local_filter() -> None:
    result = build_shortlist(
        _source_report([make_option(50)]),
        ShortlistSpec(require_overhead_cabin_bag=True),
    )
    assert result.shortlist == []
    assert result.unknown_by == {"overhead_cabin_bag": 1}


def test_stop_filter_is_explicitly_total_across_journeys() -> None:
    result = build_shortlist(
        _source_report([make_option(80, stops=2)]),
        ShortlistSpec(max_total_stops=1),
    )
    assert result.shortlist == []
    assert result.rejected_by == {"max_total_stops": 1}


def test_filter_cli_uses_saved_json_and_makes_no_provider_call(
    tmp_path, monkeypatch
) -> None:
    source_path = tmp_path / "source.json"
    filters_path = tmp_path / "filters.json"
    output_path = tmp_path / "shortlist.json"
    source_path.write_text(_source_report([make_option(75)]).model_dump_json(), encoding="utf-8")
    filters_path.write_text(json.dumps({"max_price": 100, "limit": 1}), encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("offline filter called the provider")

    monkeypatch.setattr(
        "reverse_google_flights.provider.BrowserProvider.search", fail_if_called
    )
    assert run(
        ["filter", str(source_path), str(filters_path), "--output", str(output_path)]
    ) == 0
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(output["results"]) == 1
    assert output["total_matches"] == 1
    assert output["evaluated"] == 1
    assert output["offline_network_requests"] == 0


def _source_report(options):
    coverage = SearchCoverage(
        source_candidates_loaded=len(options),
        source_load_stop_reason="ui_exhausted",
        fully_explored=True,
    )
    return BatchReport(
        outcomes=[
            SearchOutcome(
                request_id="saved",
                status="success",
                options=options,
                elapsed_ms=1,
                requests_made=1,
                coverage=coverage,
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
