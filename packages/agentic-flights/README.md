# Agentic Flights

This is the short-name installer for
[agentic-google-flights-tool](https://pypi.org/project/agentic-google-flights-tool/).
It contains no separate flight-search implementation.

```sh
python -m pip install agentic-flights
agentic-flights guide
```

Requires Python 3.11+ and Google Chrome, or Chromium installed with
`python -m playwright install chromium`. For MCP, install `agentic-flights[mcp]`.
See the [main project](https://github.com/alberto-tamez/agentic-google-flights-tool)
for setup, examples, and limitations.

This unofficial project is not affiliated with Google. Fares can change, and
search coverage depends on what Google returns.
