---
name: agentic-flights
description: Search and compare Google Flights for exact or flexible trips, including nearby airports, date ranges, trip lengths, final-price verification, and baggage evidence. Use when the user wants flight options or trip planning. Do not use to book or purchase travel.
---

# Agentic Flights

Use the installed `agentic-flights` command and `reverse_google_flights.AgentAPI`.
Keep full search reports behind their `rgf_...` run IDs; return compact comparisons.

## Find flights

1. Resolve cities to explicit airport codes and relative dates to concrete dates.
   Infer ordinary preferences when reasonable; ask only when missing information
   would materially change the trip.
2. Use `AgentAPI.plan()` and `AgentAPI.explore()` in Python for flexible searches.
   Continue while `progress.can_continue` unless the user supplied a real budget
   or the observed options already satisfy their stated goal. A work chunk controls
   one call's latency; it is not a total-search limit.
3. Use `compare()` with an explicit currency and `alternatives()` for meaningful
   price, duration, and stop tradeoffs. Inspect only promising result IDs.
4. Use `verify()` for current complete-ticket prices or baggage evidence. Inspect
   `matching_result_ids`; do not treat other fresh quotes as the selected flight.
5. Report prices, dates, airports, duration, stops, baggage evidence, freshness,
   and incomplete coverage. Do not claim a global minimum or book the ticket.

## Load details only when needed

- Run `agentic-flights guide operations` to discover the API.
- Run `agentic-flights guide <operation>` for one operation's input schema.
- Run `agentic-flights guide reference` for the complete Python example.
- Use `AgentAPI.issues(run_id)` for failed or incomplete queries without loading
  the full report.

If the command is unavailable, install it with
`python -m pip install agentic-google-flights-tool`.
