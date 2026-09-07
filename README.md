# Advanced Google Flights Tool

Help your AI find a better flight by exploring airports, dates, and trip lengths together.

Give your agent a trip goal. It can search combinations, compare the results in code, and bring back a shortlist with prices, travel times, and tradeoffs. Full results stay in saved reports, so hundreds of searches do not have to fill the conversation.

## Give this to your AI

> Use [Advanced Google Flights Tool](https://github.com/alberto-tamez/reverse-google-flights) and follow its [agent guide](https://github.com/alberto-tamez/reverse-google-flights/blob/main/docs/agent-guide.md).
>
> Find flights from Madrid or Barcelona to Japan for 12–15 nights next October. Compare Tokyo and Osaka, with at most one stop. Show me the cheapest options and any worthwhile upgrades in travel time. Search up to 100 route/date combinations. Verify the finalists and tell me what you searched and what remains unchecked.

Replace the airports, dates, and preferences with your own. Your agent needs a terminal or Python execution environment. The project provides a Python API and CLI; an MCP server is not included yet.

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
