# Contributing

This is a small, best-effort project. Focused bug reports and pull requests are welcome, but response times may vary.

Before starting a large change, open an issue to check whether it fits the project. For a code change:

1. Use Python 3.11 or newer.
2. Install the development environment with `uv sync --extra dev`.
3. Add or update tests for changed behavior.
4. Run `uv run pytest` and `uv run ruff check .`.
5. Keep generated search results, browser caches, and personal travel data out of commits.

Tests should be offline by default. Mark tests that contact Google with `pytest.mark.live` and run them only when the external request is the point of the test.

Run `uv run python scripts/run_feature_tests.py` for fresh randomized feature cases.
The runner prints a seed and replay command without saving reports. To retain a run,
pass `--report /tmp/flight-tests.json`. The JSON includes the seed, failures, and
per-feature timings. Use `--seed NUMBER` to replay a run.

MCP tests require `uv sync --extra dev --extra mcp`. The local HTTP server test is
opt-in: `uv run pytest -m integration tests/test_mcp.py`. It does not contact Google.

By contributing, you agree that your contribution will be licensed under the MIT License.

## Repository layout

- `src/agentic_flights/` contains the implementation. `api.py`, `cli.py`, and
  `mcp_server.py` expose it; `exploration.py` and `batch.py` coordinate searches;
  `providers/` retrieves and parses flights; `models.py`, `filtering.py`, and
  `views.py` define and compare results; `cache.py` and `store.py` persist them.
- `tests/` contains offline unit tests. `tests/randomized/` checks the six
  capabilities listed in `tests/feature_contract.py` with Hypothesis.
- `docs/` contains the agent guide and technical reference, also bundled into the
  installed package for `agentic-flights guide`.
- `skill/agentic-flights/` is the source for the bundled skill installed by
  `agentic-flights init-skill`.
- `examples/` contains JSON inputs. Its [index](examples/README.md) explains each.
- `scripts/` contains the randomized test runner and example-date updater.
- `.github/workflows/` tests, builds, and publishes `agentic-flights` on release.
  The `publish-alias.yml` filename is retained because PyPI's trusted publisher
  is registered to that filename; it now publishes the standalone package.

Provider code lives in `src/agentic_flights/providers/`:

- `base.py` defines the shared protocol, result, and error types.
- `direct.py` performs fast broad searches through Google's frontend service.
- `browser.py` manages browser sessions and final-price verification.
- `parsing.py` reads flight labels, booking totals, and baggage evidence.
- `traversal.py` tracks flight choices, work budgets, and resumable searches.

`google_query.py` contains the internal Google Flights URL encoder used by the
browser provider.

`provider.py` keeps existing imports working. Provider implementations import each
other directly, without going through this compatibility module.

The PyPI distribution and CLI are `agentic-flights`; the Python import is
`agentic_flights`. Local storage names retain their original spelling so existing
saved searches remain accessible.

`dist/`, `.venv/`, and test/tool caches are generated locally and ignored by Git.
Keep one-off evaluations and search exports outside the checkout. Reusable
regression tests belong in `tests/`.
