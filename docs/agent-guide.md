# Agent guide

Use this tool to answer a trip-planning question with a small, useful shortlist. Run searches and comparisons in code; return only the evidence the user needs to choose.

## Turn the goal into a search

Identify origin and destination airport choices, departure dates, trip lengths, passengers, currency, and hard requirements. Resolve relative dates to concrete dates and cities to explicit airport codes. Clarify only missing information that changes the search.

For flexible one-way or round-trip searches, use `SearchSpace` and check `space.count` before executing. Set a query budget consistent with the user's instructions. For exact or multi-city itineraries, supply `SearchSpec` requests to `BatchExecutor` or the CLI.

The [Python example](reference.md#programmatic-trip-exploration) shows the API setup. Read it when implementing the search; consult the rest of the reference only for fields you need.

## Explore, compare, verify

1. Start with `search_mode="discover"` and apply the user's price, stop, and timing constraints. Keep browser concurrency at two workers.
2. Execute bounded batches. With `Exploration.advance`, save the returned `run_id` and pass it as `resume_from` to continue the same space. Stop at the agreed budget. Failures count as attempted; inspect them before claiming coverage.
3. Filter and rank saved results in code. Compare prices within the same currency and preserve useful price-versus-duration choices. Keep full result JSON outside model context.
4. Run `search_mode="verify"` for the shortlisted trip queries that need complete prices or baggage evidence. Verification searches current inventory again; it does not lock a discovery result. Never present provisional multi-leg prices or summed one-way fares as a verified complete ticket.

## Read only what you need

Run these commands from the repository root, replacing the IDs with returned values:

```sh
uv run advanced-google-flights-tool summary RUN_ID
uv run advanced-google-flights-tool list RUN_ID --page-size 5
uv run advanced-google-flights-tool show RUN_ID RESULT_ID
```

Use `list --filters` to narrow results offline. Expand only finalists with `show`; it includes fare evidence and booking URLs. Avoid `--full` in conversational tool output. Saved runs can expire; export results the user needs to keep.

## Return a decision

Present a short comparison with total price and currency, dates, airports, duration, stops, and verified baggage where relevant. Explain why each option is worth considering. Include booking links when available and distinguish verified totals from provisional prices.

State how many combinations were attempted, failures, remaining queries, and any retrieval limits that affect the recommendation. Exhausting the defined search space does not prove the global lowest fare. Do not book tickets.
