# Agentic Flights

Let your AI find the best flights for your trip. Explore flexible dates, nearby airports, and trip lengths, then compare price, travel time, and convenience.

## Copy this to your AI

Paste this into an AI agent with terminal access and add your trip details:

```text
Install Agentic Flights with `python -m pip install agentic-google-flights-tool`.
Read `agentic-flights guide`, then use it to find the best flights for my trip.

My trip: [where I'm flying from and to, dates or flexibility, travelers,
and preferences].
```

For example: “Madrid or Barcelona to Japan for about two weeks in October 2027. One adult, an overhead cabin bag, and at most one stop. I'd like a good balance of price and travel time.”

## Install it yourself

Requires Python 3.11 or newer:

```sh
python -m pip install agentic-google-flights-tool
agentic-flights guide
```

Use an installed Google Chrome, or install Chromium:

```sh
python -m playwright install chromium
```

## For agents

Start with `agentic-flights guide`. Read `agentic-flights guide reference` for
Python examples or `agentic-flights guide schema` for the input schema when needed.
Compare results in code, keep full reports outside the conversation, and verify
the strongest options before recommending them.

The package includes a Python API and CLI. Search coverage depends on what Google
returns, and fares can change. This unofficial project is not affiliated with Google.

[Agent guide](docs/agent-guide.md) · [Technical reference](docs/reference.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
