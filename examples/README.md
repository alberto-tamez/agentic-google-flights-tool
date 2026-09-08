# Example inputs

Start with `search-one.json`. These files contain inputs, not saved flight results.
Search examples contact Google when executed; they are not offline tests.

| File | Purpose |
| --- | --- |
| `search-one.json` | Small single-search example. |
| `search-batch.json` | Three searches in one batch. |
| `round-trip-outbound-choice.json` | Provisional outbound choices for a round trip. |
| `complete-tickets-live.json` | Complete-ticket and cabin-bag searches. |
| `branching-combinations-live.json` | Resume bounded round-trip and open-jaw searches. |
| `retrieval-filtering-live.json` | Retrieve candidates with different search filters. |
| `shortlist-filters.json` | Local result filters for the retrieval example. |

Dates are fixed examples. Before running searches, refresh them from the repository
root with `uv run python scripts/refresh_example_dates.py`. The script updates all
example dates while preserving the gaps between them.

Browser searches preserve all retrieved options. `ranking_limit` limits the ranked
preview; use result pagination for the rest. Finite retrieval and quote limits can
leave work unfinished, so inspect coverage and continue the saved search.

See the [technical reference](../docs/reference.md) for supported operations and
`agentic-flights --help` for CLI usage. Save outputs outside the checkout.
