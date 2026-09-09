# Technical reference

Start with the [agent guide](agent-guide.md). The Python API works after installation
from any directory. Import `agentic_flights` and run the `agentic-flights` command.

## Install the harness skill

```sh
# Current project, both harnesses
agentic-flights init-skill

# Every project, one harness
agentic-flights init-skill --scope user --harness codex
agentic-flights init-skill --scope user --harness claude
```

Project installs use `.agents/skills/agentic-flights` for Codex and
`.claude/skills/agentic-flights` for Claude Code. User installs use the corresponding
folders under the home directory. Run with `--dry-run` to inspect destinations.
The command is idempotent. It refuses to overwrite a different existing skill unless
`--force` is explicit.

The bundled skill uses only the cross-harness fields accepted by both current
validators: `name`, `description`, and `license`. Claude Code invokes it as `/agentic-flights`; Codex
uses `$agentic-flights`. Both harnesses can also select it automatically from its
description. Claude Code watches an existing skills directory for changes, but needs
a restart when `.claude/skills` is first created during an active session. Claude
Agent SDK callers must enable the `project` or `user` setting source containing the
installed skill.

## Complete Python workflow

```python
from datetime import date, timedelta
import json
import sys
from agentic_flights import AgentAPI, SearchSpace, SearchSpec

api = AgentAPI(on_progress=lambda event: print(json.dumps(event), file=sys.stderr, flush=True))
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
run = api.plan(space.model_dump(mode="json"))
while run["progress"]["can_continue"]:
    run = api.explore(run["run_id"], prefer=["price", "duration", "stops"])
    # Persist run_id. Stop explicitly if the user's satisfaction condition is met.
run_id = run["run_id"]
if not run["progress"]["coverage_complete"]:
    diagnostics = api.issues(run_id)  # errors and retryability, without a full dump
page = api.compare(run_id, {"currency": "EUR", "max_total_stops": 1})
ids = [item["result_id"] for item in page["results"]]
if not ids:
    print("No matching observed options", page["progress"])
else:
    # Choose according to the user's preferences; this example verifies the page.
    verified = api.verify(run_id, ids, require_bag=True)
    while verified["progress"]["can_continue"]:
        verified = api.explore(verified["run_id"])
    matched_ids = list(dict.fromkeys(
        result_id for match in verified["verification"]
        for result_id in match["matching_result_ids"]
    ))
    matching_quotes = api.inspect(verified["run_id"], matched_ids)
    print(verified["verification"])

```

`AgentAPI(store=..., executor=...)` supports custom storage and provider injection.
Use `BatchExecutor(FileCache(...), provider_factory=...)` for direct exact-query
batches. A provider implements `search(SearchSpec) -> ProviderResult` and may expose
`close()`; the executor reuses it and closes it on its owning worker thread.
`api.schema("space")`, `"search"`, or `"filters"` returns data-model schemas.
Use `api.schema("verify")` or another operation name for its inputs and description;
no class or source inspection is needed. `schema("operations")` lists all operations.

`api.playbook()` returns the strategy catalog with risk, requirements, and default
status. `api.strategy_plan()` accepts a normal trip dictionary and a current route graph.
It derives gateways from graph reachability and beyond destinations from outgoing routes;
explicit airport candidates remain available for ground access and overrides.
`start_strategy_plan()` executes the bounded component searches, while
`strategy_results()` returns the direct Pareto frontier and gateway price probes. A
probe's `maximum_access_cost_to_beat_direct` is the feeder-cost headroom, not a complete
trip price. Access cost and time remain separate for door-to-door comparison.
Contract-sensitive hypotheses require an explicit flag; hidden-city generation also
requires carry-on-only confirmation.

For an exact trip, call the API without a request file:

```python
run = api.start({
    "origin": "MAD", "destination": "LHR", "departure_date": "2027-01-14",
    "return_date": "2027-01-21", "currency": "EUR", "language": "en-US",
    "country": "ES",
})
```

`start()` supplies a numeric `request_id` and `search_mode="discover"` when omitted.
Pass only fields needed by the trip. The returned run resumes through `explore()`.

## Response handling

### Exact trips, open jaw, and multi-city

`plan()` expands one-way templates into one-way or round-trip searches. It rejects
both `return_date` and `additional_segments` in the template. For exact trips,
including open jaw and multi-city, pass complete search dictionaries to
`AgentAPI.start()`. Use
`additional_segments` for every flight after the first; leave `return_date` unset
when using those segments. This preserves a different return origin in an open jaw.

`segment_filters` applies to the outbound journey, `return_segment_filters` to the
return, and `additional_segments[].filters` to the corresponding journey. Repeat
trip-wide airline, time, connection, and emissions requirements on each journey.
Planner acceptance checks input shape; it does not prove live flight availability.

### Progress and errors

Every data operation returns `run_id`, `progress`, and executable `next_actions`
with operation names and real arguments. `progress.can_continue` is consistent
for exploration and verification. A verification run stops advertising continuation
once every selected itinerary has a matching complete quote, even when unrelated
provider branches remain. `verification_satisfied` records that stopping condition.
`coverage_complete` distinguishes exhausted
query attempts from trustworthy source coverage. Use `issues` for errors,
retryability, affected queries, and incomplete-source details. Each issue exposes
top-level `code`, `codes`, `message`, and `retryable` fields. Its nested `error` may
be null when the issue is incomplete coverage rather than a failed query. Empty selections
are valid no-ops. Duplicate selections are deduplicated.

MCP responses additionally contain `ok`. When false, `error.code`, `message`,
`validation`, and `next_action` explain how to repair the call. Validation messages
include field locations, without dumping inputs or exposing a traceback.

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

CLI batches default to eight uncached queries per call and stop starting new work
after 20 seconds. The full input is saved before the first query, then checkpointed
after each completion, including when other workers are still busy. Read
`batch_progress.remaining_queries`, `pending_queries`, and `can_continue`, then run
the returned `resume_command`, such as `agentic-flights resume RUN_ID`. Resuming
skips completed queries and retains their results even when the fare cache is disabled.
Use `--retry-errors` to explicitly retry failed queries without a continuation.

Progress is newline-delimited JSON on stderr, including query starts, completions,
active request IDs, checkpoint run IDs, and five-second heartbeats. Stdout remains
one final JSON response. `--output PATH` is replaced atomically with each checkpoint.
If the process is terminated before stdout is produced, use the last stderr run ID
or the output file. SIGINT returns the partial report with exit status 130 after
in-flight work and cleanup finish; completed checkpoints also survive SIGTERM.

Set `work_chunk`, `chunk_seconds`, and `query_timeout_seconds` in batch input, or
override them with `--work-chunk`, `--chunk-seconds`, and `--query-timeout-seconds`.
Built-in providers share a 60-second query deadline across HTTP and browser operations.
A chunk's 20-second limit controls scheduling, not total
wall time: already-running queries may use their remaining allowance plus cleanup.
Timeouts produce `query_timeout`, never empty inventory. Verification keeps completed
quotes and unfinished branches in the timeout continuation.

`api.explore(chunk_seconds=20)` uses the same scheduling rule. API exploration and
verification also checkpoint each completion. Supply `AgentAPI(on_progress=callback)`
to receive event dictionaries and the latest resumable run ID. Callbacks run from
worker threads under a lock and should return promptly. The normal API return value
still contains the final snapshot for that work chunk. Custom provider implementations
must honor their own I/O deadlines; arbitrary blocking Python code cannot be killed
safely by the thread executor. Configure built-in query time limits with
`BatchExecutor(..., query_timeout_seconds=120)` when needed.

SearchSpec's `retrieval_limit`, `load_more_clicks`, `candidates_per_stage`,
`max_complete_quotes`, and `max_browser_transitions` have no configured limit when
unset. `verify()` defaults to three quote attempts and twelve browser transitions per
selected itinerary. Pass `None` explicitly for an unbounded verification chunk.
Finite values schedule resumable work. Returned `coverage.continuation`
retains unfinished branches and quote evidence. Feed it back with the same query
when using BatchExecutor directly; AgentAPI/Exploration do this for you.
`max_results` is retained for input compatibility; saved-result views paginate the
quotes that were explored.

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
price, duration, stop, departure-time, and destination-time compromises within the
same ticket and price scope. It returns an unranked Pareto frontier with no hidden
weights and labels each option's strengths. Transport order is not a best-to-worst
ranking. Slim results expose per-journey times,
`destination_stay_minutes`, and `overnight_journey_indexes`. Partial
outbound observations cannot dominate complete tickets. Both `compare` and
`alternatives` paginate; follow their returned cursor or executable next action.
Baggage status in a slim result refers to the whole requested trip; partial evidence
is labeled separately. Airline codes are shown when full names are unavailable.

Every new outcome retains `search_spec`. Slim records include requested departure
and return dates; `inspect` returns the full original query. Legacy reports without
this metadata can be read, but cannot be verified by selected result IDs.

`verify` bypasses the normal fare cache. Its `verification` entries distinguish
`matched_observed_itinerary`, `pending`, `blocked`, `insufficient_detail`, and
`not_matched`. A result ID identifies
an immutable snapshot record; changing prices or ranks creates different IDs.
Use `matching_result_ids` to inspect only the quotes that matched the selection.
Other quotes in that verification run are alternatives, not verified matches.
Never interpret a fresh but different returned itinerary as the selected flight.
Provider segment identity retains the route, date, carrier, and flight number for each
connection from Google's booking URL. `insufficient_detail` means only a matching
journey summary was available. `not_matched` means the available evidence conflicted.
The top-level `evidence_status` counts complete quotes separately from selected-flight
matches. Each preview or result row has a `selection_verification` label.
The same `verification` entries accompany comparison, inspection, and issues responses
for a verification run. For example, an outbound-only match on a round trip reports
`match_scope: "outbound"`, `whole_itinerary_matched: false`, and
`uncompared_journey_indexes: [1]`. Journey 0 is outbound; later indexes are return
or onward journeys. Those later flights are newly quoted choices and have not been
matched against a prior selection, even when the total and baggage cover the whole ticket.
`whole_itinerary_matched: true` requires a whole-itinerary comparison with at least
one matching quote; only its `matching_result_ids` are matches. If a previously
selected return changes, that quote is not a whole-itinerary match.

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
It exposes `schema`, `plan`, `explore`, `compare`, `alternatives`, `inspect`,
`verify`, and `issues`. It accepts saved run IDs rather than filesystem paths. It is a local
single-user service; remote multi-user hosting requires authentication and isolated
stores. Agent-generated loops run in the agent's own execution environment.

## Storage, installation, and providers

Managed runs expire after seven idle days by default; storage defaults are 50 runs
and 100 MB across managed reports/provider cache. These storage policies are
configurable with `ManagedStore(policy=StorePolicy(...))`. Export persistent results
before expiration. Missing/expired handles return errors, not invented results.
If the default cache directory is not writable, set `AGENTIC_FLIGHTS_STORE`
to a dedicated writable directory and keep that value when resuming saved runs.
`AgentAPI()` reads this override; its optional `store` argument takes a `ManagedStore`
object, not a path string.
Use the same Python environment for the CLI and API; check its installed version
with `python -c 'import importlib.metadata; print(importlib.metadata.version("agentic-flights"))'`.

Browser verification uses Playwright's separate Chromium headless shell with a
desktop Chrome user-agent. It does not launch the full Chrome-for-Testing app in
background mode. Searches do not open visible windows by default.
`AGENTIC_FLIGHTS_HEADLESS=0` explicitly enables visible windows for debugging;
only that mode may fall back to installed Chrome when managed Chromium is missing.
The provider never switches to visible mode automatically after a failure.
A startup failure blocks further launches
in that worker; fix the environment before explicitly retrying. It reads English
Google Flights labels. Browsers are reused within a batch; each query gets
an isolated context. Transient DOM replacement/navigation gets a recovery attempt;
repeated failures return explicit errors or continuations. Page operation timeouts
bound a hung I/O operation, not total search scope.

Before releasing a Playwright or macOS change, run the opt-in browser stability test:

```sh
uv run pytest -m integration \
  tests/test_browser_lifecycle.py::test_background_browser_survives_repeated_isolated_launches
```

It starts three independent browser processes and performs a context and page operation.
Each attempt runs in a child Python process, so `SIGABRT`, `SIGTRAP`, early disconnects,
and launch errors become test failures with compact diagnostics. This tests the current
machine, Playwright build, and execution permissions; it cannot guarantee another host.

Google's "Oops, something went wrong" page is detected during result, navigation,
and booking waits and reported as `provider_page_error` without waiting for the
ordinary 60-second result timeout. Failed verification branches retain this code
in their coverage diagnostics.
Google unusual-traffic pages return `provider_access_blocked` immediately and stop
further requests in that worker. Stop the search rather than retrying the block.
Browser searches currently reject `excluded_airlines` with `provider_unsupported`;
the direct provider encodes exclusions, but browser verification cannot silently
drop them. Retaining a field in a plan is not evidence that every provider supports it.

The default smart provider uses direct Google requests for broad discovery and the
browser only for final-price and baggage verification. A discovery failure never
launches a browser. `direct` and `browser` remain
available when a caller needs to select one explicitly.
If the shopping RPC returns no payload, direct discovery uses fast-flights' HTTP
approach to read embedded JSON from the public Flights page. It rejects optional
cookies when prompted and preserves the original currency and country parameters.
This path does not launch a browser. It reports `initial_page` coverage as truncated;
the initial response does not establish that every available flight was retrieved.
Airline exclusions require the RPC; the HTTP path reports unsupported exclusions
instead of dropping them. CLI searches return status 1 on any failed query and still
write the normal JSON report. An empty or blocked response is not a live-test pass.
Development setup: `uv sync --extra dev --extra mcp`; run `uv run pytest` and
`uv run ruff check .`. Repository examples use concrete dates; refresh them with
`uv run python scripts/refresh_example_dates.py` before using them.
