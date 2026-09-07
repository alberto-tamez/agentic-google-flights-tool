# Agentic Flights

Find the best flights for your trip with your AI. Compare flexible dates, nearby airports, and trip lengths, then get a shortlist of the options worth booking.

## Give this to your AI

Copy this into an AI agent with terminal access and add your trip details:

```text
Install and use Agentic Flights from
https://github.com/alberto-tamez/agentic-google-flights-tool.
Follow its README and the bundled agent guide to find the best flights for my trip.

My trip: [departure city, destination, dates or flexibility, travelers,
and preferences].
```

For example: “Madrid or Barcelona to Japan for about two weeks in October 2027. One adult, an overhead cabin bag, and at most one stop. I'd like a good balance of price and travel time.”

## Install

Requires Python 3.11 or newer. Install the Python package from PyPI:

```sh
python -m pip install agentic-google-flights-tool
```

Then read the included instructions:

```sh
agentic-flights guide
```

The tool uses Google Chrome if installed. Otherwise:

```sh
python -m playwright install chromium
```

## For agents

Start with `agentic-flights guide`. Load `agentic-flights guide reference` or
`agentic-flights guide schema` only when needed. Explore and compare results in
code, keep full reports outside the conversation, and verify the strongest
options before recommending them.

The package includes a Python API and CLI. An MCP server is not included yet.
Search coverage is limited by what Google returns, and fares can change.
This unofficial project is not affiliated with Google.

[Agent guide](docs/agent-guide.md) · [Technical reference](docs/reference.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
