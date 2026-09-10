---
name: agentic-flights
description: Find and compare Google Flights for exact or flexible trips, including nearby airports, date ranges, trip lengths, current total prices, and baggage rules. Use when the user wants flight options or help planning a trip. Do not use to book or buy travel.
license: MIT
---

# Agentic Flights

Use Python 3.11+ and agentic-flights 0.7.1 or newer. Import `FlightAgent` and call its
typed `search()` method directly. If that import fails, update the installed package.
Do not inspect the repository, API source, schemas, or guides before searching.

## Search

- Resolve cities and relative dates before calling the API. Preserve the user's
  travelers, cabin, bags, stops, timing, airline, fare, and emissions constraints.
  Ask only when missing information would change the trip.
- Pass compact in-memory dictionaries. Never create a JSON request file for an
  agent-driven call. Omit default fields and invented limits.
- Use `FlightAgent.search(request)` for exact or flexible one-way and round trips. It
  returns at most four decision rows and keeps full provider evidence outside context.
  Choose one `candidate_ref`, then call `FlightAgent.verify(search_ref, candidate_ref)`.
  These are the only operations in the normal agent workflow.
- Do not manually continue, compare, inspect, or query issues in the normal workflow.
  The bounded reply states incomplete coverage and whether an environment change is
  required. Retry only after that change.
- Treat `observed` prices as shortlist evidence. Only a `confirmed` verification reply
  proves the selected whole itinerary and final total. Baggage marked `to_verify` is not
  included evidence.
- `alternatives()` keeps useful time tradeoffs, including later returns that add
  destination time. Compare the per-journey departure and arrival fields.
- Call `playbook()` when routing or ticket construction could materially change the
  result. Use `start_auto_strategy_plan()` so the program loads current scheduled
  destinations and derives gateways from actual connectivity rather than city-specific
  rules. Explicit airport candidates are only for ground access or manual overrides.
  Do not invent a universal gateway list or assume a historically cheap hub is still cheap.
- Use the default directionally validated path mode. `all_outgoing` only generates
  hypotheses; it does not prove that a gateway reaches the destination. Do not describe
  topology as requested-date availability or call a truncated connection set exhaustive.
  Because the source returns only its fewest-stop tier, path mode may yield no gateway
  when a direct route exists. Use `all_outgoing` to test extra gateways empirically and
  keep `validation_basis="empirical_fare_required"` visible in saved diagnostics.
- Read `strategy_results()` after the bounded sweep. Route-graph gateways include a
  conservatively dated positioning ticket and the main ticket. Treat the summed fare as
  an observed separate-ticket composite until both tickets and connection logistics are
  verified. Never present an entry from `gateway_probes` as a complete alternative.
- Use `schedule_frontier()` only when you have dated event records and can state the
  route snapshot, departure window, maximum transfers, enabled strategies, traveler
  constraints, and pricing budget. Price complete ticket constructions after structural
  search. Verify only the resulting frontier.

## Choose a practical trip

- Apply the user's hard constraints first, then use `alternatives()` to remove options
  that are strictly worse across comparable price, time, stops, departure convenience,
  and destination time. Its remaining options are an unranked Pareto frontier. Never
  turn their order into a hidden score or claim an objective best.
- Use the returned strengths to label the tradeoffs, such as lowest price, shortest
  travel time, easiest departure, or most destination time. Recommend one only from
  the user's stated priorities. Without a stated priority, present a small tradeoff set
  and make any personal call explicit instead of disguising it as algorithmic fact.
- Prefer the shorter, easier itinerary when the user says fares are close. Recommend
  extra connections, an overnight, or more ground travel only when the savings are
  meaningful for this user and trip. Do not use a fixed savings threshold.
- Check plausible nearby departure and arrival airports unless the user wants a
  specific airport. Include an airport reached by train, bus, or car only after
  accounting for the ground fare, travel time in both directions, transfer buffer,
  and the risk of separate tickets. Do not treat a lower airfare as a saving if the
  ground trip consumes it.
- Compare total door-to-door time, not flight duration alone. Count early departures,
  overnight travel, long layovers, self-transfers, airport changes, extra hotel nights,
  baggage friction, and how much useful time remains at the destination.
- Always check for a meaningfully faster, easier, or cheaper alternative. Show it when
  it gives the user a real choice. State the extra time or hassle alongside the saving,
  and do not pad the answer with full writeups of near-duplicates. Briefly mention close
  alternatives instead of hiding them, especially when they change the airport,
  departure time, arrival time, connection, baggage, or comfort. Give the price and
  practical difference in one line unless the user asks for more detail.
- Verify current ground schedules and prices when they could change the recommendation.
  Label estimates instead of presenting them as confirmed connections.
- Add positioning fares, ground costs, travel time in both directions, disruption
  buffers, baggage recheck, and any hotel before calling a gateway option cheaper.
- Keep contract-sensitive strategies off unless the user explicitly opts in. Hidden-city
  evaluation additionally requires carry-on-only travel, a final skipped segment, a
  separate return reservation, and a current review of the airline's terms. Present it
  as contract-sensitive and operationally fragile, never as an ordinary itinerary.

## Verify and report

Use `verify()` on shortlisted IDs for current total price and cabin-bag evidence.
Stop advancing that verification run when `verification_satisfied` is true. Inspect
its matching quote instead of exhausting unrelated branches. If `match_scope` is
`outbound`, verify the resulting complete itinerary once more before claiming the
round trip matched.
Only `complete_single_ticket` plus `provider_final_total` confirms a ticket total.
Never sum one-way fares or substitute an unmatched flight. An outbound match does
not confirm the selected return; check `whole_itinerary_matched` and
`uncompared_journey_indexes`. Treat `insufficient_detail`, `pending`, and `blocked`
as unverified. In verification responses, only rows marked `matched_selection`
confirm the selected flight.

Report price, dates, airports, duration, stops, baggage evidence, freshness, and
incomplete coverage. Do not claim a global minimum or book.

## Keep internal state private

- Treat run IDs, result IDs, request IDs, cursors, continuation data, branch counts,
  provider error codes, and `next_actions` as private working state. Never include
  them in progress updates or the final answer unless the user asks for diagnostics.
- Tell the user what matters to the decision: what was found, what is being checked,
  and whether a price is verified, recently observed, or could not be reconfirmed.
  Do not narrate commands, saved runs, parser behavior, or terminal states.
- Prefer short prose or a small, valid Markdown table. Do not expose raw tool output.

## Side effects and failures

Discovery uses HTTP and must not launch a browser. Verification may launch
Playwright's isolated Chromium headless shell; it does not use the user's browser.
Use the harness's normal authorization prompt only when needed. Never switch to a
visible browser, sign in, book, or submit traveler
or payment data. Do not call handled timeouts or blocked checks successful. Keep
retry narration out of chat unless it changes the decision.
Failed or blocked rows are errors, not flight results.

Use `agentic-flights guide <topic>` only for an unfamiliar schema or recovery path.
If the command is missing, install it with `python -m pip install agentic-flights`.
