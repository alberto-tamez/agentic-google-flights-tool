# Contributing

This is a small, best-effort project. Focused bug reports and pull requests are welcome, but response times may vary.

Before starting a large change, open an issue to check whether it fits the project. For a code change:

1. Use Python 3.11 or newer.
2. Install the development environment with `uv sync --extra dev`.
3. Add or update tests for changed behavior.
4. Run `uv run pytest` and `uv run ruff check .`.
5. Keep generated search results, browser caches, and personal travel data out of commits.

Tests should be offline by default. Mark tests that contact Google with `pytest.mark.live` and run them only when the external request is the point of the test.

Run `uv run python scripts/run_feature_tests.py` for fresh randomized feature cases and latency. Its ignored report includes the seed and replay command.

By contributing, you agree that your contribution will be licensed under the MIT License.
