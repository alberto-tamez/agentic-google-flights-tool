# Agentic Flights

Give an AI agent a trip goal and let it search Google Flights across airports,
dates, and trip lengths, compare the useful tradeoffs, and verify the finalists.

## Give this to your AI

Copy this prompt into Codex or Claude Code and replace the trip details:

```text
Install Agentic Flights with:
`python -m pip install -U agentic-google-flights-tool`

Install its reusable skill for the harness you are running:
- Codex: `agentic-flights init-skill --scope user --harness codex`
- Claude Code: `agentic-flights init-skill --scope user --harness claude`

Invoke the installed Agentic Flights skill and use it to find the best flights
for my trip. Explore flexible dates, nearby airports, and trip lengths when they
could improve the result. Compare meaningful price and travel-time tradeoffs,
verify the current complete price and baggage evidence for the finalists, and
tell me where search coverage remains incomplete. Do not book anything.

Trip: [origin, destination, dates or flexibility, trip length, travelers,
currency, baggage, stop limits, and timing or airline preferences].
```

## Install it yourself

Agentic Flights requires Python 3.11 or newer and uses an installed Google Chrome.
If Chrome is unavailable, install Playwright Chromium after the package:

```sh
python -m pip install -U agentic-google-flights-tool
python -m playwright install chromium
```

Install the skill in the current project for both Codex and Claude Code:

```sh
agentic-flights init-skill
```

This writes `.agents/skills/agentic-flights/SKILL.md` for Codex and
`.claude/skills/agentic-flights/SKILL.md` for Claude Code. Add `--scope user` to
make it available in every project. The initializer preserves an existing modified
skill unless `--force` is explicit.

## What it can do

- Search one-way, round-trip, open-jaw, and multi-city itineraries.
- Explore combinations of origin airports, destinations, dates, and stay lengths.
- Filter by price, stops, airlines, local times, duration, and cabin baggage.
- Save full results behind compact `rgf_...` run IDs and resume unfinished work.
- Compare currencies safely and retain useful price, duration, and stop tradeoffs.
- Verify final whole-ticket prices, itinerary matches, booking links, and baggage
  evidence without continuing to purchase.
- Report parsing losses, truncated searches, retryable failures, and unexplored work.

## Ways agents can use it

The installed skill is the easiest entry point. `agentic-flights guide` prints the
workflow, and `agentic-flights guide <operation>` reveals one schema at a time.

The package also includes the code-first `AgentAPI`, a JSON CLI for exact batches,
and an optional local MCP server:

```sh
python -m pip install -U "agentic-google-flights-tool[mcp]"
agentic-flights-mcp
```

MCP supports stdio and local stateless HTTP. Saved runs live in the local managed
store; the project does not operate a hosted flight-search service. Managed runs
expire after seven idle days by default, so export anything that must be retained.

## Know before relying on a result

This is an unofficial project and is not affiliated with Google. Google Flights has
no supported consumer search API, so page changes can break retrieval. Results are
dated observations rather than reservations or price guarantees. Search coverage is
reported but cannot prove that Google exposed every available fare. Confirm price,
baggage, and availability with the seller. The tool does not book travel.

[Agent guide](docs/agent-guide.md) · [Technical reference](docs/reference.md) ·
[PyPI](https://pypi.org/project/agentic-google-flights-tool/) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
