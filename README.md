# Agentic Flights

Agentic Flights gives AI agents a command-line tool and Python API for searching Google Flights. It can compare exact itineraries or explore combinations of airports, dates, and trip lengths without dumping every raw result into the conversation.

It supports one-way, round-trip, and multi-city searches, plus filters for price, stops, airlines, times, duration, and overhead cabin baggage.

> [!IMPORTANT]
> This is an unofficial project and is not affiliated with Google. Google can change its pages without notice, and fares can change between a search and checkout. Confirm the final price, baggage rules, and availability with the seller.

## Install

Agentic Flights requires Python 3.11 or newer.

```sh
python -m pip install agentic-google-flights-tool
```

The browser search uses Google Chrome if it is installed. Otherwise, install Playwright's Chromium build:

```sh
python -m playwright install chromium
```

Check the installation and print the bundled agent instructions:

```sh
agentic-flights guide
```

Install the reusable skill into the current project for both Codex and Claude Code:

```sh
agentic-flights init-skill
```

This creates `.agents/skills/agentic-flights/SKILL.md` for Codex and
`.claude/skills/agentic-flights/SKILL.md` for Claude Code. Install it for every
project instead with `agentic-flights init-skill --scope user`. Existing customized
skills are left untouched unless you pass `--force`.

## Give it to an AI agent

Paste the following into an agent that can run terminal commands, then replace the bracketed text with your trip:

```text
Install Agentic Flights with `python -m pip install agentic-google-flights-tool`.
Run `agentic-flights guide` and follow those instructions to search for my trip.

Trip: [origin, destination, dates or flexibility, number of travelers, currency,
and preferences such as stops, baggage, price, or departure times].
```

For example:

```text
Find flights from Madrid or Barcelona to Tokyo or Osaka for 12 to 15 nights in
October 2027. One adult, economy, at most one stop, and one overhead cabin bag.
Compare price and total travel time. Show me the best three tradeoffs and verify
the final price and baggage allowance before recommending one.
```

The installed package includes both the short workflow and the full technical reference:

```sh
agentic-flights guide
agentic-flights guide reference
agentic-flights guide schema
```

No repository checkout is needed.

## Run a search yourself

The CLI accepts a JSON file or JSON from standard input. This example runs one discovery search and saves the full report in the managed local cache:

```sh
agentic-flights - <<'JSON'
{
  "provider": "browser",
  "max_workers": 1,
  "searches": [
    {
      "request_id": "mad-lis",
      "origin": "MAD",
      "destination": "LIS",
      "departure_date": "2027-01-21",
      "return_date": "2027-01-28",
      "cabin": "economy",
      "max_stops": "non_stop",
      "adults": 1,
      "currency": "EUR",
      "language": "en-US",
      "country": "ES",
      "search_mode": "discover",
      "max_results": 5
    }
  ]
}
JSON
```

The command prints a compact summary with a run ID such as `rgf_...`. Use that ID to inspect the saved report without searching Google again:

```sh
agentic-flights summary RUN_ID
agentic-flights list RUN_ID --page-size 5
agentic-flights show RUN_ID RESULT_ID
```

To keep the complete JSON report at a path you choose, add `--output flights.json`. Use `--full` only when you explicitly want the entire report on standard output.

## Discovery and verification

`search_mode: "discover"` reads a broad set of candidates with less browser work. Use it to compare routes and dates.

`search_mode: "verify"` follows selected flight combinations to Google's final itinerary page. Use it for shortlisted round trips or multi-city trips that need a complete ticket price or baggage evidence. Discovery prices for multi-leg trips can be provisional, so do not add one-way prices together and call the result a verified fare.

## Use the Python API

The distribution keeps the import name `reverse_google_flights` for compatibility:

```python
from reverse_google_flights import BatchExecutor, SearchSpec
from reverse_google_flights.cache import FileCache
from reverse_google_flights.provider import BrowserProvider
from reverse_google_flights.store import ManagedStore

store = ManagedStore()
store.initialize()

executor = BatchExecutor(
    FileCache(store.provider_cache / "browser", namespace=BrowserProvider.version),
    max_workers=1,
)

report = executor.execute(
    [
        SearchSpec(
            request_id="mad-lis",
            origin="MAD",
            destination="LIS",
            departure_date="2027-01-21",
            return_date="2027-01-28",
            currency="EUR",
            language="en-US",
            country="ES",
            search_mode="discover",
        )
    ]
)

print(report.model_dump_json(indent=2))
```

For flexible-date exploration, multi-city inputs, filtering, retrieval limits, cache behavior, and complete-ticket verification, read the [technical reference](docs/reference.md).

## Connect through MCP

```sh
python -m pip install "agentic-google-flights-tool[mcp]"
agentic-flights-mcp
```

Use stdio, or add `--transport streamable-http` for local HTTP. The adapter exposes
planning, resumable exploration, comparison, inspection, and verification through
saved run IDs. Agents can load input schemas on demand with `schema`.

## Limits and responsible use

Google Flights does not publish a consumer search API. This project depends on undocumented page behavior and may need updates when Google changes it. Search results are dated observations, not reservations or price guarantees.

Keep browser concurrency low. Do not use this project to bypass access controls, overload services, or automate purchases. You are responsible for following the terms, policies, and laws that apply to your use.

## Project links

- [Agent guide](docs/agent-guide.md)
- [Technical reference](docs/reference.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [PyPI](https://pypi.org/project/agentic-google-flights-tool/)
