# Agentic Flights

Give an AI your trip plans. It searches Google Flights across airports, dates, and
trip lengths, compares the best options, and checks current prices before you choose.

## Give this to your AI

Copy this prompt into Codex or Claude Code and replace the trip details:

```text
Install Agentic Flights with:
`python -m pip install -U agentic-flights`

Install its reusable skill for the AI app you are using:
- Codex: `agentic-flights init-skill --scope user --harness codex`
- Claude Code: `agentic-flights init-skill --scope user --harness claude`

Use the installed Agentic Flights skill to find the best flights for my trip.
Explore flexible dates, nearby airports, and trip lengths when they
could improve the result. Include airports that are realistically reachable by train
or bus when the full door-to-door saving justifies the extra time. Show options that
balance total price, travel time, and comfort. Check the current total price and baggage
rules for the best options. Briefly mention close alternatives instead of hiding them.
Tell me if any part of the search was incomplete. Keep internal run IDs and tool
diagnostics out of the answer. Do not book anything.

Trip: [origin, destination, dates or flexibility, trip length, travelers,
currency, baggage, stop limits, and timing or airline preferences].
```

## Install it yourself

Agentic Flights requires Python 3.11 or newer. Discovery uses direct HTTP requests.
Final-price verification uses a tool-managed browser. Install Chromium for it:

```sh
python -m pip install -U agentic-flights
python -m playwright install chromium
```

Install the skill in the current project for both Codex and Claude Code:

```sh
agentic-flights init-skill
```

This adds the skill to `.agents/skills` for Codex and `.claude/skills` for Claude
Code. Add `--scope user` to use it in every project. It will not replace a changed
skill unless you pass `--force`.

## What it can do

- Search one-way, round, and multi-city trips, including trips that return from a
  different city or to a different airport.
- Explore nearby airports, dates, and stay lengths; filter by price, stops, airline,
  connection, layover, time, duration, emissions, and baggage.
- Search for adults, children, and infants, including lap infants.
- Compare price against travel time, departure hour, and time at the destination
  without mixing different currencies.
- Check the current total price, connection-level same-flight match, baggage rules,
  and booking links.
- Say when it could not read a flight, stopped early, or still has work to do.

## Ways agents can use it

The skill is the easiest way to start. Broad searches use a fast direct request;
final-price and baggage checks use a background browser. The package also has a Python API, a
command-line interface, and an optional local MCP connection for AI apps:

```sh
python -m pip install -U "agentic-flights[mcp]"
agentic-flights-mcp
```

Large CLI searches return resumable work chunks. Progress and checkpoint run IDs
appear on stderr; the final JSON includes `batch_progress` and a `resume_command`.
Completed queries survive interruption. Run `agentic-flights resume RUN_ID` to continue.
These handles are for agents and diagnostics; they should not appear in a travel
recommendation unless the user asks for technical details.

Agents can pass an exact search directly with `AgentAPI.start({...})`; no temporary
request file is needed. Discovery stays HTTP-only. A headless browser starts only
when final prices or baggage are verified.

Run `agentic-flights guide` for the workflow or `agentic-flights guide <operation>`
for one operation. Saved searches stay on your computer and expire after seven days
without use by default. The project does not run a hosted search service.

## Know before relying on a result

This unofficial project is not affiliated with Google. Google Flights has no
supported public search API, so page changes can break the tool. Results show what
was visible at search time; they are not reservations or price guarantees. The tool
cannot prove that Google showed every fare. Confirm the price, baggage, and seats
with the seller. The tool does not book travel.

[Agent guide](docs/agent-guide.md) · [Technical reference](docs/reference.md) ·
[PyPI](https://pypi.org/project/agentic-flights/) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Third-party notices](THIRD_PARTY_NOTICES.md)
