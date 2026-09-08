# Agentic Flights: agent guide

Find flights that fit the user's trip and preferences. Compare in code, then return
useful alternatives with evidence. After installation, no repository checkout is required.

## Start

Use Python 3.11+ and install `agentic-google-flights-tool`. Use an installed Google
Chrome or run `python -m playwright install chromium`. Read `agentic-flights guide reference` for the complete Python example. Load `agentic-flights guide space`,
`search`, or `filters` for only the schema you need; `operations` lists the API.

## Plan and explore

Resolve cities to explicit airport choices and relative dates to concrete dates.
Record travelers, currency, stop/timing requirements, baggage, and the user's
price-versus-time preferences. Clarify missing information that changes the trip.

Use `AgentAPI.plan` to validate and save a `SearchSpace` without making flight
requests. `AgentAPI.explore` accepts the saved run ID and returns a new snapshot.
Each chunk visits airport pairs and dates broadly and interleaves promising
unfinished queries. Save the latest run ID. All original queries and continuation
state are stored with it, so another process can continue.

A work chunk controls response latency, not total search scope. Repeat exploration
while work remains. Do not invent a combination cutoff. Respect an explicit user
budget or a user-defined satisfaction condition; otherwise report an interruption
as incomplete, not as the best possible answer. Query errors are retained; inspect
and retry retryable errors explicitly. An unresolved branch or parse failure means
coverage is incomplete even if every route/date query was attempted.

Start with `search_mode="discover"` for broad comparison. For long verification
work, set an explicit `work_quotes` execution chunk and continue its returned run
with `explore`. Finite query retrieval/candidate/quote settings retain continuation
state. Unset search limits mean no configured cutoff; service availability and page
load timeouts can still interrupt the work.

## Compare, inspect, verify

Use `compare` with filters to rank saved results offline. Select a currency before
price comparisons in a mixed-currency dataset. `alternatives` returns observed
price/duration/stops tradeoffs rather than assuming that cheapest means best.
`inspect` expands only selected result IDs, including their original query and
requested return date. Keep full reports outside the conversation.

Discovery multi-leg prices are provisional. Call `verify` on selected result IDs
for current final prices and baggage. It reports whether the observed itinerary
matched; it must not silently substitute another flight. An outbound-only match
proves only the selected outbound, not a specific return choice. Matching uses the
route, local times, airline and flight number where known, not a provider booking ID.
If a match is pending, continue its saved run. If it is absent, report that clearly.

Never sum independent one-way fares and label them a complete ticket. A complete
quote requires `complete_single_ticket` and `provider_final_total`. Hard cabin-bag
requirements reject unknown or extra-cost baggage and require whole-trip evidence.

## Return a decision

Present a concise comparison with total price/currency, dates, airports, duration,
stops, baggage evidence, and booking links when available. Explain tradeoffs.
State remaining queries/branches, errors, parse losses and observation freshness.
No tool can prove Google's entire inventory was exposed. Do not book tickets.

## MCP

Install `agentic-google-flights-tool[mcp]`. Run `agentic-flights-mcp` for stdio or
`agentic-flights-mcp --transport streamable-http` for local HTTP. The same operations
use explicit saved handles with no session dependency. Call `schema` on demand.
