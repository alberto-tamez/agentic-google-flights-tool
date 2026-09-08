# Agentic Flights: agent guide

Find flights that fit the user's trip and preferences. Compare in code, then return
useful alternatives with evidence. After installation, no repository checkout is required.

## Start

Use Python 3.11+ and install `agentic-google-flights-tool`. Use an installed Google
Chrome or run `python -m playwright install chromium`. Read `agentic-flights guide reference` for the complete Python example. Load `agentic-flights guide space`,
`search`, or `filters` for only the schema you need; `operations` lists the API. For operation inputs, use `agentic-flights guide verify`
or the corresponding operation name; class introspection is unnecessary.

To make the skill available to an AI app, run `agentic-flights init-skill` in a project. It
installs this workflow for both Codex and Claude Code. Use `--scope user` for every
project, or `--harness codex` / `--harness claude` to install only one copy.

## Plan and explore

Resolve cities to explicit airport choices and relative dates to concrete dates.
Record travelers, currency, stop and time requirements, baggage, and how the user
balances price against travel time. Clarify missing information that changes the trip.

Use `AgentAPI.plan` to validate and save a `SearchSpace` without making flight
requests. `AgentAPI.explore` accepts the saved run ID and returns a new snapshot.
Each chunk visits airport pairs and dates broadly and interleaves promising
unfinished queries. Save the latest run ID. All original queries and continuation
state are stored with it, so another process can continue.

A work chunk controls response latency, not total search scope. Repeat exploration
while work remains. Do not invent a combination cutoff. Respect an explicit user
budget or a user-defined satisfaction condition; otherwise report an interruption
as incomplete, not as the best possible answer. A `blocked` state requires an explicit retry or changed input; do not loop on it.
Query errors are retained; inspect
and retry retryable errors explicitly. An unresolved branch or parse failure means
coverage is incomplete even if every route/date query was attempted.

Start with `search_mode="discover"` for broad comparison. For long verification
work, set an explicit `work_quotes` execution chunk and continue its returned run
with `explore`. Finite query retrieval/candidate/quote settings retain continuation
state. Unset search limits mean no configured cutoff; service availability and page
load timeouts can still interrupt the work.

## Compare, inspect, verify

Use `compare` with filters to rank saved results without searching again. Select a
currency before comparing prices from different currencies. `alternatives` returns
choices that balance price, travel time, and stops instead of assuming cheapest is best.
`inspect` expands only selected result IDs, including their original query and
requested return date. Keep full reports outside the conversation.

Early prices for trips with more than one flight are not confirmed totals. Call
`verify` on selected result IDs for current total prices and baggage. It reports
whether it found the same flights; it must not silently substitute another flight.
A match for the departure flight confirms only that flight, not a specific return.
Matching uses the route, local times, airline, and flight number where known, not a
booking-system ID. Inspect `matching_result_ids` to retrieve the exact verified results.
If a match is pending, continue its saved run. If it is absent, report that clearly.

Never sum independent one-way fares and label them a complete ticket. A complete
quote requires `complete_single_ticket` and `provider_final_total`. Hard cabin-bag
requirements reject unknown or extra-cost baggage and require whole-trip evidence.

## Handle responses

Use the common `progress.can_continue` and `progress.coverage_complete` fields.
Follow executable `next_actions` when helpful. Read `issues` for failed or blocked
queries instead of dumping all results. Empty selections are valid no-ops; if no
options match, report that rather than assuming a booking is available. MCP errors
return `ok=false` with field-level validation and a suggested repair action.

## Return a decision

Present a concise comparison with total price/currency, dates, airports, duration,
stops, baggage evidence, and booking links when available. Explain why each option
may suit the user.
State remaining queries/branches, errors, parse losses and observation freshness.
No tool can prove Google's entire inventory was exposed. Do not book tickets.

## MCP

Install `agentic-google-flights-tool[mcp]`. Run `agentic-flights-mcp` for stdio or
`agentic-flights-mcp --transport streamable-http` for local HTTP. The same operations
use explicit saved handles with no session dependency. Call `schema` on demand.
