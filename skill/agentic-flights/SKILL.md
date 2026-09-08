---
name: agentic-flights
description: Find and compare Google Flights for exact or flexible trips, including nearby airports, date ranges, trip lengths, current total prices, and baggage rules. Use when the user wants flight options or help planning a trip. Do not use to book or buy travel.
license: MIT
---

# Agentic Flights

Use the installed `agentic-flights` command and `agentic_flights.AgentAPI`.
Keep full search reports behind their `rgf_...` run IDs; return compact comparisons.
The tool requires Python 3.11+. Chrome or Playwright Chromium is needed when it
checks final prices and baggage or falls back from direct search.

## Find flights

1. Resolve cities to explicit airport codes and relative dates to concrete dates.
   Preserve adults, children, seated infants, lap infants, cabin, bags, airline
   inclusions or exclusions, connections, layover windows, emissions preferences,
   and fare restrictions when the user supplies them. Infer ordinary preferences
   when reasonable; ask only when missing information would materially change the trip.
2. Use `AgentAPI.plan()` and `AgentAPI.explore()` in Python for flexible searches.
   Use the default provider: it chooses fast direct requests for discovery and the
   browser for final-price and baggage verification.
   Continue while `progress.can_continue` unless the user supplied a real budget
   or the observed options already satisfy their stated goal. A work chunk controls
   one call's latency; it is not a total-search limit.
3. Use `compare()` with an explicit currency. Use `alternatives()` to find choices
   that balance price, travel time, and stops. Inspect only promising result IDs.
4. Use `verify()` to check the current total trip price or baggage rules. Inspect
   `matching_result_ids`; do not present different fresh flights as the selected one.
5. Report prices, dates, airports, duration, stops, baggage evidence, freshness,
   and incomplete coverage. Do not claim a global minimum or book the ticket.

## Load details only when needed

- Run `agentic-flights guide operations` to discover the API.
- Run `agentic-flights guide <operation>` for one operation's input schema.
- Run `agentic-flights guide reference` for the complete Python example.
- Use `AgentAPI.issues(run_id)` for failed or incomplete queries without loading
  the full report.

If the command is unavailable, install it with
`python -m pip install agentic-flights`.
