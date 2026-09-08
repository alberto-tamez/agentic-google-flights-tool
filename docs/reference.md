# Technical reference

Start with the [agent guide](agent-guide.md). The Python API works after installation
from any directory. CLI aliases and the `reverse_google_flights` import remain compatible.

## Complete Python workflow

```python
from datetime import date, timedelta
from reverse_google_flights import AgentAPI, SearchSpace, SearchSpec

api = AgentAPI()
departure = date.today() + timedelta(days=60)
space = SearchSpace(
    template=SearchSpec(
        request_id="template", origin="MAD", destination="NRT",
        departure_date=departure, currency="EUR", language="en-US", country="ES",
        search_mode="discover", max_stops="one_stop_or_fewer",
    ),
    origins=["MAD", "BCN"], destinations=["NRT", "KIX"],
    departure_start=departure, departure_end=departure + timedelta(days=6),
    min_nights=12, max_nights=15,
)
plan = api.plan(space.model_dump(mode="json"))
run_id = plan["run_id"]
while True:
    progress = api.explore(run_id, prefer=["price", "duration", "stops"])
    run_id = progress["run_id"]
    # Save run_id for interruption recovery. Avoid printing whole reports.
    if progress["stop_reason"] == "blocked":
        break  # Report the errors; retry_errors=True explicitly retries blocked work.
    if progress["search_space_exhausted"]:
        break
# Check failures and coverage before drawing conclusions.
page = api.compare(run_id, {"currency": "EUR", "max_total_stops": 1})
ids = [item["result_id"] for item in page["results"]]
details = api.inspect(run_id, ids)
tradeoffs = api.alternatives(run_id, {"currency": "EUR"})
# Select the options that fit the user's preferences in your own code.
selected_ids = ids
verified = api.verify(run_id, selected_ids, require_bag=True, work_quotes=1)
# work_quotes schedules one quote attempt per query per call; it is NOT a total cutoff.
while (verified["coverage_totals"]["pending_branches"]
       and not verified["coverage_totals"]["blocked_queries"]):
    verified = api.explore(verified["run_id"])
print(verified["verification"])
```

`AgentAPI(store=..., executor=...)` supports custom storage and provider injection.
Use `BatchExecutor(FileCache(...), provider_factory=...)` for direct exact-query
batches. A provider implements `search(SearchSpec) -> ProviderResult` and may expose
`close()`; the executor reuses it and closes it on its owning worker thread.
`api.schema("space")`, `"search"`, or `"filters"` returns the current input schema.

## Searching and stopping

`SearchSpace` supports explicit airport sets, inclusive departure windows, and
optional min/max nights. It does not resolve city names. Airport pairs and dates
are visited before varying stay lengths. There is no arbitrary whole-space cap.
`prefer` changes the order of unfinished queries, not their eligibility. Hard
requirements belong in the query or filters. The observed tradeoff frontier is
not a proof of global optimality.

`Exploration.advance(search_budget=20)` and `api.explore(work_chunk=20)` default to
a scheduling chunk of 20 query operations. The caller can choose any positive
chunk size and continue. Each chunk saves an immutable snapshot. Reusing an older
handle branches from it. Restoring with `Exploration.restore` needs only the handle,
executor, and store. `retry_errors=True` explicitly retries unresolved query errors.
Original failures remain in saved history and are counted in `failure_events`.

SearchSpec's `retrieval_limit`, `load_more_clicks`, `candidates_per_stage`,
`max_complete_quotes`, and `max_browser_transitions` have no configured limit when
unset. Finite values schedule resumable work. Returned `coverage.continuation`
retains unfinished branches and quote evidence. Feed it back with the same query
when using BatchExecutor directly; AgentAPI/Exploration do this for you.
`max_results` is a legacy option used by the optional fli provider; the browser
preserves all explored quotes and lets saved-result views paginate them.

Replay checks observed flight labels. Inventory changes can produce `branch_changed`,
which is retained for explicit continuation. Persistent failures need changed input
or another attempt later; continuing an error forever is not useful progress.
Full source/quote evidence is saved; model responses contain summaries and selected
records. Limits on a page or preview only affect disclosure, not stored results.

## Filters and verification

Use `currency`, `max_price`, `max_total_stops`, `max_duration_minutes`, `airlines`,
hour windows, `require_overhead_cabin_bag`, and `ticket_scope` in `compare`.
Mixed-currency price sorting/filtering without a currency raises an explicit error.
Sort keys are `price`, `duration`, `stops`, and `departure`. Filtering happens before
pagination; pass the returned cursor with the same filters. `alternatives` retains
price/duration/stops compromises and avoids comparing different baggage evidence.

Every new outcome retains `search_spec`. Slim records include requested departure
and return dates; `inspect` returns the full original query. Legacy reports without
this metadata can be read, but cannot be verified by selected result IDs.

`verify` bypasses the normal fare cache. Its `verification` entries distinguish
`matched_observed_itinerary`, `pending`, `blocked`, and `not_matched`. A result ID identifies
an immutable snapshot record; changing prices or ranks creates different IDs.
Never interpret a fresh but different returned itinerary as the selected flight.

## CLI and MCP

```sh
agentic-flights guide operations
agentic-flights guide space
agentic-flights summary RUN_ID
agentic-flights list RUN_ID --filters FILTERS.json
agentic-flights show RUN_ID RESULT_ID
```

For MCP, install the `mcp` extra. The optional adapter uses the official MCP Python
SDK v2 and supports stdio and stateless Streamable HTTP on `127.0.0.1:8000/mcp`.
It exposes `schema`, `plan`, `explore`, `compare`, `alternatives`, `inspect`, and
`verify`. It accepts saved run IDs rather than filesystem paths. It is a local
single-user service; remote multi-user hosting requires authentication and isolated
stores. Agent-generated loops run in the agent's own execution environment.

## Storage, installation, and providers

Managed runs expire after seven idle days by default; storage defaults are 50 runs
and 100 MB across managed reports/provider cache. These storage policies are
configurable with `ManagedStore(policy=StorePolicy(...))`. Export persistent results
before expiration. Missing/expired handles return errors, not invented results.

The default browser provider uses installed Chrome or Playwright Chromium. It reads
English Google Flights labels. Browsers are reused within a batch; each query gets
an isolated context. Transient DOM replacement/navigation gets a recovery attempt;
repeated failures return explicit errors or continuations. Page operation timeouts
bound a hung I/O operation, not total search scope.

Install the `fli` extra for the optional undocumented-endpoint provider. It is not
the tested default and does not implement the browser's complete-ticket traversal.
Development setup: `uv sync --extra dev --extra mcp`; run `uv run pytest` and
`uv run ruff check .`. Repository examples use concrete dates; refresh them with
`uv run python scripts/refresh_example_dates.py` before using them.
