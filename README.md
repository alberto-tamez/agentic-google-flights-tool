# Reverse Google Flights batch search

This package searches many route and date options through one JSON command. It preserves input order, limits concurrency, deduplicates identical searches, caches successful results, and keeps failures attached to the affected request.

This is an unofficial project. It is not affiliated with, endorsed by, or supported by Google. Google Flights is a trademark of Google LLC.

The default browser provider opens an installed Google Chrome in headless mode. It rejects optional Google consent when the consent page appears. It reads the same English accessibility labels shown in Google Flights search results. An optional `fli` provider calls Google's undocumented internal endpoint and is available for comparison, but that endpoint currently returns a null response envelope from this network.

## Install

```sh
uv sync --extra dev
```

The browser provider first uses an installed Google Chrome. If Chrome is absent, install Playwright Chromium with `uv run playwright install chromium`.

The optional `fli` comparison provider uses its latest published release:

```sh
uv sync --extra dev --extra fli
```

## Run a batch

Start with one search:

```sh
uv run reverse-google-flights examples/search-one.json
```

Then run the 50-search example if you want to exercise batching:

```sh
uv run reverse-google-flights examples/searches-50.json
```

The command stores the full report in the managed cache and prints a compact manifest with an `rgf_...` run ID, counts, bounded coverage, five slim previews, and follow-up commands. Add `--output PATH` only for a persistent user-owned export. Explicit exports are never auto-cleaned. Add `--full` only when full JSON on standard output is intentional.

The input can also come from standard input. Set `provider` to `browser` or `fli`. Keep `max_workers` at 2 for browser searches. `ranked_by_currency` never compares prices in different currencies.

Use `search_mode: discover` for broad, cheap retrieval. The browser reads Google’s Best tab and clicks the observed `View more flights` control up to `load_more_clicks`. `retrieval_limit` is an intentional safety cap and defaults to 250. It is separate from `max_results`, complete-quote budgets, and shortlist size. Coverage reports how many source candidates loaded, DOM duplicates, parser losses, clicks, and the source stop reason. The tool does not claim that the Best tab contains every Google result.

Apply upstream filters before retrieval with `max_stops`, `max_price`, and `segment_filters`. Segment filters support `airlines`, departure and arrival hour bounds, and `max_duration_minutes`. Use `return_segment_filters` for a round-trip return. Additional multi-city segments carry their own `filters`. Hours are airport-local integers from 0 through 23. Windows cannot wrap past midnight. Airline values are two-character IATA codes or `ONEWORLD`, `SKYTEAM`, and `STAR_ALLIANCE`.

Multi-leg discovery returns first-stage `outbound_choice` records with `ticket_scope: partial_or_unknown`. The displayed price is provisional. Do not add one-way prices or present discovery output as a complete ticket.

Filter and rank a saved report without another Google request:

```sh
uv run reverse-google-flights list RUN_ID --filters - --page-size 5 <<'JSON'
{"max_price": 150, "max_total_stops": 0, "limit": 5}
JSON
```

Local filters support request IDs, price, total duration, total stops, airline names or codes, departure hours, overhead cabin baggage, and complete-ticket scope. Missing evidence fails a hard filter and appears in `unknown_by`. The shortlist report preserves source coverage, parse losses, and truncation separately.

Inspect saved results progressively:

```sh
uv run reverse-google-flights summary RUN_ID
uv run reverse-google-flights list RUN_ID --page-size 5
uv run reverse-google-flights show RUN_ID RESULT_ID
```

`list` filters before pagination and returns at most 20 slim records. Its cursor binds the dataset contents, filter, sort order, and offset. `show` accepts at most ten stable result IDs and is the only default view that returns full fare evidence and booking URLs. Use `summary SOURCE --full` for an explicit full export.

Managed runs use the platform user cache directory. Access refreshes their idle timestamp. Automatic cleanup keeps at most 50 runs, removes runs idle for 7 days, and caps managed reports plus provider cache at 100 MB. It removes only regular files under the sentinel-owned store. It does not scan system temporary folders, project artifacts, explicit exports, or explicit `--cache-dir` paths.

Each browser result includes numeric price, duration, stop count, airline name, local departure time, and local arrival time. A one-way result normally has `ticket_scope: partial_or_unknown` because the search page does not prove the final ticket terms. A hard cabin-bag requirement uses the same bounded final-page exploration as complete trips.

For a round trip, the browser provider explores a bounded tree of outbound and return choices. It replays every selected path in a fresh page and accepts a result only after Google shows a final booking page. Each option contains both journeys, the exact combined price from the itinerary summary, `price_provenance: provider_final_total`, and `ticket_scope: complete_single_ticket`. The provider does not add independent one-way fares or continue to a seller.

For an open-jaw or another multi-city ticket, keep the first journey in `origin`, `destination`, and `departure_date`. Add later journeys to `additional_segments` in travel order. The same bounded traversal verifies each returned path on its own final page. Do not combine `additional_segments` with `return_date`.

Use `candidates_per_stage` to control the Google-ordered choices explored at each journey. Use `max_complete_quotes` and `max_browser_transitions` as total work budgets. `max_results` only limits returned options. Defaults are 2 candidates per stage, 4 completed quote attempts, and 24 browser transitions. Complete-ticket options sort by final price, total duration, stop count, and itinerary identity.

Every outcome has `coverage`. It reports candidates seen, terminal branches attempted, quotes completed and filtered, duplicates, pruned branches, branch errors by code, browser transitions, and budget exhaustion. `fully_explored` is true only when no candidate, output, error, or budget truncated the observed tree. A successful bounded result is not a guarantee of Google's global lowest fare. Cached outcomes retain the original discovery coverage while `requests_made` is zero for the cache replay.

For agent-driven multi-leg work, discover route and date queries first, apply upstream constraints, and shortlist the saved provisional results. Run `search_mode: verify` only for the two or three shortlisted trip queries that need a final seller quote and baggage evidence. Verification reruns current Google results; a provisional rank does not lock the same flight after inventory or prices change.

Set `overhead_cabin_bags` to the number of requested overhead cabin bags. Set `require_overhead_cabin_bag` to `true` to reject the selected fare unless its matching seller option or selected-flight summary states that the carry-on is included and that the terms apply to the entire trip. Missing evidence and extra-cost baggage both fail this hard filter with a structured error. A personal item does not satisfy the requirement.

## Limits

Google Flights does not publish a consumer search API. Both providers depend on undocumented behavior that Google can change. A browser result records `observed_at` and remains a dated observation, not a fare guarantee. Confirm the price, baggage rules, and availability with the seller before purchase. The browser parser uses English result labels even when the request includes another language tag.

Use conservative batch sizes and concurrency. You are responsible for complying with the terms, policies, and laws that apply to your use. Do not use this project to evade access controls, overload services, or automate purchases.

The checked-in examples use fixed dates because the input schema requires concrete travel dates. Refresh them before they expire:

```sh
uv run python scripts/refresh_example_dates.py
```

## Contributing

This is a small, best-effort project. Bug reports and focused fixes are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a large change. Report security problems as described in [SECURITY.md](SECURITY.md).

Run deterministic checks with `uv run pytest` and `uv run ruff check .`.
