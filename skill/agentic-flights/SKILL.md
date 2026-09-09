---
name: agentic-flights
description: Find and compare Google Flights for exact or flexible trips, including nearby airports, date ranges, trip lengths, current total prices, and baggage rules. Use when the user wants flight options or help planning a trip. Do not use to book or buy travel.
license: MIT
---

# Agentic Flights

Use Python 3.11+ and the installed `agentic_flights.AgentAPI`. Keep full reports behind their
`rgf_...` run IDs and return only useful comparisons.

## Search

- Resolve cities and relative dates before calling the API. Preserve the user's
  travelers, cabin, bags, stops, timing, airline, fare, and emissions constraints.
  Ask only when missing information would change the trip.
- Pass compact in-memory dictionaries. Never create a JSON request file for an
  agent-driven call. Omit default fields and invented limits.
- Exact trips use `AgentAPI.start(search)`; pass a list only for multiple searches.
  Open-jaw and multi-city trips are one search with `additional_segments`. Flexible
  airports, dates, or trip lengths use `AgentAPI.plan()` followed by `explore()`.
  `start()` supplies the request ID and discovery mode when omitted.
- Continue the returned run while `progress.can_continue`, unless the user's goal
  is already met. A work chunk limits one call, not the whole search. Read
  `issues()` after errors; retry only after the input, authorization, or environment
  changed.
- Use `compare()` with an explicit currency, then `alternatives()` and `inspect()`
  only for promising result IDs.
- `alternatives()` keeps useful time tradeoffs, including later returns that add
  destination time. Compare the per-journey departure and arrival fields.

## Verify and report

Use `verify()` on shortlisted IDs for current total price and cabin-bag evidence.
Only `complete_single_ticket` plus `provider_final_total` confirms a ticket total.
Never sum one-way fares or substitute an unmatched flight. An outbound match does
not confirm the selected return; check `whole_itinerary_matched` and
`uncompared_journey_indexes`. Treat `insufficient_detail`, `pending`, and `blocked`
as unverified. In verification responses, only rows marked `matched_selection`
confirm the selected flight.

Report price, dates, airports, duration, stops, baggage evidence, freshness, and
incomplete coverage. Do not claim a global minimum or book.

## Side effects and failures

Discovery uses HTTP and must not launch a browser. Verification may launch
Playwright's isolated Chromium headless shell; it does not use the user's browser.
Use the harness's normal authorization prompt only when needed. Never switch to a
visible browser, sign in, book, or submit traveler
or payment data. Keep retry narration out of chat unless it changes the result.
Failed or blocked rows are errors, not flight results.

Use `agentic-flights guide <topic>` only for an unfamiliar schema or recovery path.
If the command is missing, install it with `python -m pip install agentic-flights`.
