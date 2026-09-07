# Advanced Google Flights Tool

Help your AI find a better flight by exploring airports, dates, and trip lengths together.

Give your agent a trip goal. It can search combinations, compare the results in code, and bring back a shortlist with prices, travel times, and tradeoffs. Full results stay in saved reports, so hundreds of searches do not have to fill the conversation.

## Give this to your AI

Copy this prompt, replace the bracketed trip details, and paste it into an AI agent with terminal or Python access. The agent can handle setup and searching.

```text
Use Advanced Google Flights Tool to find flights for my trip.

Repository: https://github.com/alberto-tamez/reverse-google-flights
Start by reading the agent guide:
https://github.com/alberto-tamez/reverse-google-flights/blob/main/docs/agent-guide.md

Set up the project if needed, then follow the guide.
My trip: [where I'm flying from and to, dates or flexibility, trip length,
number of travelers, and any budget, baggage, or stop requirements].

Search up to 100 route/date combinations. Compare results in code and keep
full reports out of the chat. Show me a short list of the cheapest options
and worthwhile time-saving alternatives. Verify the finalists, include
available booking links, and tell me what remains unchecked. Do not book.
```

For example, your trip could be: “Madrid or Barcelona to Tokyo or Osaka, 12–15 nights in October 2027, one adult, at most one stop, with an overhead cabin bag.”

The project provides a Python API and CLI; an MCP server is not included yet.

## Get started

From a checkout of this repository:

```sh
uv sync
```

Use an installed Google Chrome, or install the browser with:

```sh
uv run playwright install chromium
```

Then give your agent the [agent guide](docs/agent-guide.md) and your trip requirements.

## What it can do

- Compare airport, date, and trip-length combinations in bounded batches.
- Resume an exploration using its saved run ID.
- Filter saved results without searching again.
- Check complete round-trip and multi-city prices, including available baggage evidence.

Searches cover a defined set of queries, not every fare Google might offer. Prices can change; confirm the final terms with the seller. This unofficial project is not affiliated with Google and does not book tickets.

[Agent guide](docs/agent-guide.md) · [Technical reference](docs/reference.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
