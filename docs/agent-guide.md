# Agentic Flights: agent guide

Find flights that fit the user's trip and preferences. Compare in code, then return
useful alternatives with evidence. After installation, no repository checkout is required.

## Start

Use Python 3.11+ and agentic-flights 0.6.8 or newer. Check the runtime version once
before the first search so an older package cannot silently follow a newer skill.
An installed Google Chrome or Playwright Chromium is needed only for verification. Read
`agentic-flights guide reference` for the complete Python example. Load `agentic-flights guide space`,
`search`, or `filters` for only the schema you need; `operations` lists the API. For operation inputs, use `agentic-flights guide verify`
or the corresponding operation name; class introspection is unnecessary.

To make the skill available to an AI app, run `agentic-flights init-skill` in a project. It
installs this workflow for both Codex and Claude Code. Use `--scope user` for every
project, or `--harness codex` / `--harness claude` to install only one copy.

## Plan and explore

Resolve cities to explicit airport choices and relative dates to concrete dates.
Record travelers, currency, stop and time requirements, baggage, and how the user
balances price against travel time. Clarify missing information that changes the trip.
Preserve children and infants separately from adults, and preserve checked bags,
airline exclusions, connection or layover requirements, emissions preferences,
self-transfer rules, and basic-economy exclusions when supplied.

Use `AgentAPI.plan` to validate and save a `SearchSpace` without making flight
requests. `AgentAPI.explore` accepts the saved run ID and returns a new snapshot.
For an exact trip, pass one compact dictionary directly to
`AgentAPI.start`; it assigns omitted request IDs and starts in discovery mode.
Do not create a temporary JSON input file for an agent-driven search. The default
provider uses HTTP for discovery and browser traversal only for verification.
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

Use the host's normal authorization flow for network or browser access. Do not ask
for broad permission in chat, switch to a visible browser after a failure, or retry
an unchanged blocked environment. A failed query remains an error, never inventory.

Start with `search_mode="discover"` for broad comparison. For long verification
work, use the bounded defaults or set explicit `work_quotes` and
`max_browser_transitions` execution chunks. Continue only while the selected itinerary
still needs evidence. Finite query retrieval/candidate/quote settings retain continuation
state. Unset search limits mean no configured cutoff; service availability and page
load timeouts can still interrupt the work.

## Choose a practical trip

Apply the user's hard constraints first. Use `alternatives` to remove options that are
strictly worse across comparable price, time, stops, departure convenience, and
destination time. The remaining Pareto frontier is a tradeoff set, not a best-to-worst
ranking, and it uses no hidden weights. Label why each option remains, such as lowest
price, shortest travel time, easiest departure, or most destination time. Recommend
one from the user's stated priorities. Without them, show a small tradeoff set and make
any personal call explicit instead of presenting it as algorithmic fact.

Prefer the shorter and easier itinerary when the user says fares are close. A large
saving may justify extra connections, an overnight, or more ground travel, but the
threshold depends on the user's budget, trip length, schedule, and tolerance for
hassle. Do not use one fixed savings threshold; compare the saving with the added hours.

Call `playbook` when route construction could materially change the result. Pass the
base trip to `start_auto_strategy_plan`; it loads current scheduled destinations, then
derives and executes
unranked hypotheses for nearby airports, positioning gateways, mixed one-way tickets,
and any explicitly enabled contract-sensitive strategy. For graph-derived gateways,
the sweep prices the main ticket and a conservative positioning ticket dated one day
outside each end of the main trip. `strategy_results` sums those observed fares but
keeps the separate-ticket, hotel, and connection caveats. Candidate discovery uses
graph connectivity, never a permanent or city-specific hub list.

Automatic route discovery uses the public air-routes.com destinations endpoint and a
seven-day local cache. The source describes passenger routes operating now and includes
seasonality, but it is not a fare or date-specific availability source. Google fare
discovery still validates each generated hypothesis for the requested travel date.
The default reciprocal gateway pass is smaller but can miss asymmetric service. Use
`gateway_candidate_mode="all_outgoing"` when the user wants exhaustive coverage or the
first pass finds no satisfactory tradeoff. State that expansion without listing its
internal search handles.

For positioning, add both access journeys, fares, buffers, baggage handling, possible
hotels, and separate-ticket disruption risk. Hidden-city searches never run by default.
They require explicit user opt-in, carry-on-only travel, a final skipped segment, a
separate return reservation, and current airline-terms review. Keep the risk label next
to the price difference.

Check plausible nearby airports when rail, bus, or driving makes them realistic. For
example, a traveler in Valencia might consider Madrid or Alicante. Compare the full
ground trip in both directions, including its fare, duration, transfer buffer, and
separate-ticket risk. Report net savings and added travel time. Verify current ground
schedules and prices when they could change the recommendation; label rough estimates.

Compare total door-to-door time. Include overnight travel, airport changes, baggage
friction, extra hotel nights, arrival and return times, and useful time at the
destination. Always check for a meaningfully faster, easier, or cheaper alternative.
Show it when it gives the user a real choice. Do not hide close alternatives. Mention
near-ties briefly with their price and practical difference, especially when the
airport, schedule, connection, baggage, or comfort changes. Reserve full comparisons
for meaningfully distinct choices or alternatives the user asks to inspect.

## Compare, inspect, verify

Use `compare` with filters to rank saved results without searching again. Select a
currency before comparing prices from different currencies. `alternatives` returns
choices that balance price, travel time, stops, departure inconvenience, and time at
the destination instead of assuming cheapest is best. Slim results include each
journey's departure and arrival, destination stay minutes, and overnight journeys.
`inspect` expands only selected result IDs, including their original query and
requested return date. Keep full reports outside the conversation.

Early prices for trips with more than one flight are not confirmed totals. Call
`verify` on selected result IDs for current total prices and baggage. It reports
whether it found the same flights; it must not silently substitute another flight.
A match for the departure flight confirms only that flight, not a specific return.
Check `whole_itinerary_matched` and `uncompared_journey_indexes` alongside `match_scope`.
On a round trip, `[1]` means the return was not compared with an earlier selection.
Matching uses provider segment references when Google supplies them. These references
retain every connection's route, date, carrier, and flight number even when the visible
booking summary collapses a journey to its endpoints. `insufficient_detail` means the
journey summary matched but the connection identity could not be proved. It is different
from `not_matched`, which records conflicting itinerary evidence. Inspect
`matching_result_ids` to retrieve the exact verified results.
If a match is pending, continue its saved run. If it is absent, report that clearly.
When `verification_satisfied` is true, stop that verification run and inspect its
matching quote. An outbound-only selection still requires a second verification of
the resulting complete itinerary before the round trip can be called a match.

Never sum independent one-way fares and label them a complete ticket. A complete
quote requires `complete_single_ticket` and `provider_final_total`. Hard cabin-bag
requirements reject unknown or extra-cost baggage and require whole-trip evidence.

## Handle responses

Use the common `progress.can_continue` and `progress.coverage_complete` fields.
Follow executable `next_actions` when helpful. Read `issues` for failed or blocked
queries instead of dumping all results. Each issue has stable top-level `code`,
`message`, and `retryable` fields; `error` may be null for incomplete coverage.
Empty selections are valid no-ops; if no
options match, report that rather than assuming a booking is available. MCP errors
return `ok=false` with field-level validation and a suggested repair action.
Verification responses also include `evidence_status`. It counts complete quotes and
matched selected itineraries separately. Preview and result rows label a quote as
`matched_selection`, `other_complete_quote`, or `not_a_complete_quote`.

## Return a decision

Present a concise comparison with total price/currency, dates, airports, duration,
stops, baggage evidence, and booking links when available. Explain why each option
may suit the user.
Translate internal state into decision language such as verified, recently observed,
or could not be reconfirmed. Keep run IDs, result IDs, request IDs, cursors, branch
counts, provider codes, commands, and parser details out of progress updates and final
answers unless the user explicitly asks for diagnostics. State material coverage gaps
and observation freshness without exposing the bookkeeping used to track them.
No tool can prove Google's entire inventory was exposed. Do not book tickets.

## MCP

Install `agentic-flights[mcp]`. Run `agentic-flights-mcp` for stdio or
`agentic-flights-mcp --transport streamable-http` for local HTTP. The same operations
use explicit saved handles with no session dependency. Call `schema` on demand.
