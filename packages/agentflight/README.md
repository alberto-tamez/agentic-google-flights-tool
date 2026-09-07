# AgentFlight

Find the best flights for your trip with your AI.

```sh
python -m pip install agentflight
agentflight guide
```

AgentFlight installs [Agentic Flights](https://pypi.org/project/agentic-google-flights-tool/)
and adds the shorter `agentflight` command. It supports the same searches, saved results,
and bundled agent instructions. It requires Python 3.11 or newer and Google Chrome,
or Chromium installed with `python -m playwright install chromium`.

## Give this to your AI

```text
Install AgentFlight with `python -m pip install agentflight`, then read
`agentflight guide` and find the best flights for my trip.

My trip: [departure city, destination, dates or flexibility, travelers,
and preferences].
```

This unofficial project is not affiliated with Google. Fares can change, and
search coverage depends on what Google returns.
