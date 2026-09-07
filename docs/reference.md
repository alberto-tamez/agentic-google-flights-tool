# Technical reference

Read this when you need field definitions, retrieval limits, storage behavior, or Python examples. For the main workflow, start with the [agent guide](agent-guide.md). CLI commands work after package installation. Commands using repository examples or scripts require a checkout. The Python API keeps its compatible `reverse_google_flights` import name.

## Install

```sh
uv sync --extra dev
```

The browser provider uses an installed Google Chrome when available. Otherwise, install Playwright Chromium with `uv run playwright install chromium`.

The optional `fli` comparison provider uses its latest published release:

```sh
uv sync --extra dev --extra fli
```

## Run a batch

Start with one search:

```sh
agentic-flights examples/search-one.json
```

Then run the 50-search example if you want to exercise batching:

```sh
agentic-flights examples/searches-50.json
```

The command stores the full report in the managed cache. Standard output contains an `rgf_...` run ID, counts, coverage, five short previews, and commands for reading the saved results. Use `--output PATH` to keep a separate export; the automatic cleanup never removes explicit exports. Use `--full` to write the complete report to standard output.

Input may also come from standard input. Set `provider` to `browser` or `fli`. Keep `max_workers` at 2 for browser searches. The `ranked_by_currency` field never compares prices across currencies.

Use `search_mode: discover` to retrieve many candidates with less browser work. The browser reads Google's Best tab and clicks the visible `View more flights` control up to `load_more_clicks` times. `retrieval_limit` defaults to 250 and caps the candidates read from the page. It does not control `max_results`, complete-quote budgets, or shortlist size. Coverage records loaded candidates, DOM duplicates, parser losses, clicks, and why retrieval stopped. Google's Best tab may omit other available flights.

Set `max_stops`, `max_price`, and `segment_filters` to filter the Google query before retrieval. Segment filters accept `airlines`, departure and arrival hour bounds, and `max_duration_minutes`. Put return-flight filters in `return_segment_filters`; each additional multi-city segment has its own `filters`. Hours are airport-local integers from 0 through 23, and a window cannot cross midnight. Airlines use two-character IATA codes or the identifiers `ONEWORLD`, `SKYTEAM`, and `STAR_ALLIANCE`.

Multi-leg discovery returns first-stage `outbound_choice` records with `ticket_scope: partial_or_unknown`. Their prices are provisional; adding one-way prices or presenting them as complete tickets would be incorrect.

Filter and rank a saved report without another Google request:

```sh
agentic-flights list RUN_ID --filters - --page-size 5 <<'JSON'
{"max_price": 150, "max_total_stops": 0, "limit": 5}
JSON
```

Local filters support request IDs, price, total duration, total stops, airline names or codes, departure hours, overhead cabin baggage, and complete-ticket scope. Missing evidence fails a hard filter and appears in `unknown_by`. The shortlist report preserves source coverage, parse losses, and truncation separately.

Inspect a saved report without printing the full JSON:

```sh
agentic-flights summary RUN_ID
agentic-flights list RUN_ID --page-size 5
agentic-flights show RUN_ID RESULT_ID
```

`list` filters before pagination and returns at most 20 slim records. Its cursor binds the dataset contents, filter, sort order, and offset. `show` accepts at most ten stable result IDs and is the only default view that returns full fare evidence and booking URLs. Use `summary SOURCE --full` for an explicit full export.

Managed runs live in the platform's user cache directory, and reading a run refreshes its idle timestamp. Cleanup keeps at most 50 runs, removes runs idle for 7 days, and limits managed reports plus the provider cache to 100 MB. Cleanup touches only regular files inside the store marked as owned by this application. System temporary folders, project artifacts, explicit exports, and explicit `--cache-dir` paths are outside its scope.

Each browser result includes numeric price, duration, stop count, airline name, local departure time, and local arrival time. A one-way result normally has `ticket_scope: partial_or_unknown` because the search page does not prove the final ticket terms. A hard cabin-bag requirement uses the same bounded final-page exploration as complete trips.

For a round trip, the browser provider explores a bounded tree of outbound and return choices. It replays every selected path in a fresh page and accepts a result only after Google shows a final booking page. Each option contains both journeys, the exact combined price from the itinerary summary, `price_provenance: provider_final_total`, and `ticket_scope: complete_single_ticket`. The provider does not add independent one-way fares or continue to a seller.

For an open-jaw or another multi-city ticket, keep the first journey in `origin`, `destination`, and `departure_date`. Add later journeys to `additional_segments` in travel order. The same bounded traversal verifies each returned path on its own final page. The input schema rejects requests that combine `additional_segments` with `return_date`.

Use `candidates_per_stage` to control the Google-ordered choices explored at each journey. Use `max_complete_quotes` and `max_browser_transitions` as total work budgets. `max_results` only limits returned options. Defaults are 2 candidates per stage, 4 completed quote attempts, and 24 browser transitions. Complete-ticket options sort by final price, total duration, stop count, and itinerary identity.

Every outcome has `coverage`. It reports candidates seen, terminal branches attempted, quotes completed and filtered, duplicates, pruned branches, branch errors by code, browser transitions, and budget exhaustion. `fully_explored` is true only when no candidate, output, error, or budget truncated the observed tree. A successful bounded result is not a guarantee of Google's global lowest fare. Cached outcomes retain the original discovery coverage while `requests_made` is zero for the cache replay.

For agent-driven multi-leg work, discover route and date queries first, apply upstream constraints, and shortlist the saved provisional results. Run `search_mode: verify` only for the two or three shortlisted trip queries that need a final seller quote and baggage evidence. Verification reruns current Google results; a provisional rank does not lock the same flight after inventory or prices change.

Set `overhead_cabin_bags` to the requested number of overhead cabin bags. With `require_overhead_cabin_bag: true`, the selected fare passes only when its seller option or selected-flight summary states that carry-on baggage is included for the entire trip. Missing evidence, extra-cost baggage, and personal-item-only allowances fail the filter with a structured error.

## Programmatic trip exploration

`SearchSpace` expands airport choices, an inclusive departure window, and optional
stay lengths into exact searches. `space.count` previews the work without opening
a browser. Airport lists are normalized and deduplicated; same-airport routes are
excluded. This first API supports one-way and round-trip spaces with at most
100,000 combinations. Supply explicit airport codes; city resolution and adaptive
search allocation are not implemented.

```python
from datetime import date, timedelta

from reverse_google_flights import BatchExecutor, Exploration, SearchSpace, SearchSpec
from reverse_google_flights.cache import FileCache
from reverse_google_flights.provider import BrowserProvider
from reverse_google_flights.store import ManagedStore

departure = date.today() + timedelta(days=60)
space = SearchSpace(
    template=SearchSpec(
        request_id="template", origin="MAD", destination="NRT",
        departure_date=departure, currency="EUR", language="en", country="ES",
        search_mode="discover",
    ),
    origins=["MAD", "BCN"], destinations=["NRT", "HND", "KIX"],
    departure_start=departure, departure_end=departure + timedelta(days=6),
    min_nights=12, max_nights=15,
)
assert space.count == 168
store = ManagedStore()
store.initialize()
executor = BatchExecutor(
    FileCache(store.provider_cache / "browser", namespace=BrowserProvider.version),
    max_workers=2,
)
exploration = Exploration(space, executor, store)
progress = exploration.advance(search_budget=20)
print(progress.model_dump_json())  # Counts, run ID, and at most five previews.

# A later process can reconstruct the same configuration and continue.
progress = exploration.advance(resume_from=progress.run_id, search_budget=20)
```

Each call attempts at most `search_budget` combinations, including cache hits,
with a maximum of 500. This is a query budget, not a browser-transition or elapsed
time limit. Template settings still bound the work inside each query. Full results
stay in the managed store; use the existing `list` and `show` commands with the
returned run ID to inspect them without another Google request.

Each completed batch produces an immutable snapshot containing all outcomes so
far. Save the latest run ID to resume. Reusing an older ID branches from that
snapshot; it does not advance a shared job. An interruption before a batch returns
can require replaying that batch, with available successful cache entries reused.
Managed-store expiration still applies, and snapshots retain their original fare
observations. Changed search-space or provider-namespace settings reject resume.

Failures count as attempted and remain in the report. `search_space_exhausted`
means every defined query was attempted, even if some failed; it does not mean
every available fare was retrieved. Retry failed queries or verify shortlisted
queries separately through `BatchExecutor`. MCP transport and automatic finalist
selection are not part of this API yet.

## Limits

Google Flights does not publish a consumer search API. Both providers depend on undocumented behavior that Google can change. Each browser result records `observed_at`; it is a dated observation and does not guarantee the fare. Confirm the price, baggage rules, and availability with the seller before purchase. The browser parser reads English result labels even when the request specifies another language tag.

Use conservative batch sizes and concurrency. You are responsible for complying with the terms, policies, and laws that apply to your use. Do not use this project to evade access controls, overload services, or automate purchases.

The input schema requires concrete travel dates, so the checked-in examples eventually expire. Update them with:

```sh
uv run python scripts/refresh_example_dates.py
```

