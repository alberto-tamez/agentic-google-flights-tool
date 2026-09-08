# Changelog

## 0.5.0

- Made `agentic-flights` the standalone distribution, with no dependency on the
  former package. Removed the alias package and duplicate publishing workflow.
- Renamed the Python import to `agentic_flights`. Existing scripts must update
  imports from `reverse_google_flights`.
- Removed the `reverse-google-flights` and `advanced-google-flights-tool` CLI
  aliases. Use `agentic-flights`; the optional MCP command is `agentic-flights-mcp`.
- Kept existing local storage paths so saved searches remain accessible.
- Added tests and lint checks to the release workflow before publication.

## 0.4.4

- Split flight providers into focused modules while preserving existing imports.
- Added 21 previously local regression tests to the maintained test suite.
- Made randomized test reports opt-in and removed accumulated evaluation output.
- Simplified examples, corrected outbound discovery settings, and removed ignored
  browser options.
- Fixed alias MCP dependency pins and added a release-time consistency check.
- Shared test setup, fixed month-boundary test dates, and avoided redundant example
  date rewrites.

## 0.4.3

- Removed saved-run implementation details from the user-facing capability list.

## 0.4.2

- Replaced travel and agent jargon in the README, installed skill, and agent guide
  with direct descriptions of what the tool does.
- Explained trips that return from another city or airport in plain language.

## 0.4.1

- Matched the frontmatter accepted by Codex and Claude Code and documented Claude's
  live-reload and Agent SDK behavior.
- Preflighted multi-harness skill installs so conflicts cannot leave a partial setup.
- Reworked the README around the AI handoff, user-visible capabilities, interfaces,
  requirements, local storage, and reliability limits.

## 0.4.0

- Added `agentic-flights init-skill` to install one bundled Agent Skill for Codex,
  Claude Code, or both at project or user scope.
- Kept existing customized skills intact unless the user passes `--force`.

## 0.3.0

- Added consistent API progress and executable next actions; exploration summaries
  are now top-level, matching verification responses.
- Exposed actionable MCP validation errors, operation schemas, and query diagnostics.
- Kept partial outbound observations separate from complete-ticket comparisons.
- Labeled baggage coverage for the whole requested trip and preserved airline codes.
- Returned precise matching quote IDs and deduplicated verification selections.
- Made empty selections no-ops and paginated tradeoff results.
- Updated the installed workflow to handle empty, failed, and blocked searches.

## 0.2.1

- Return blocked state for failed branch frontiers instead of retrying them forever.
- Give unfinished queries fair turns while preserving preference-based ordering.

## 0.2.0

- Fixed whole-hour duration parsing and retained flights with unavailable prices.
- Made price filtering currency-explicit and ranked default previews correctly.
- Preserved original queries, return dates, and aggregate coverage in saved results.
- Added resumable query frontiers and removed arbitrary search-space/branch caps.
- Spread exploration across airport/date choices and preserved observed tradeoffs.
- Added AgentAPI planning, comparison, inspection, and identity-aware fresh verification.
- Added the optional official MCP v2 adapter with stateless HTTP and stdio transports.
- Reused browsers per worker and recovered transient result-page changes.
- Expanded the bundled guide with a complete installed-package Python workflow.

## 0.1.0

- Added bounded batch search through browser automation.
- Added round-trip and multi-city ticket verification.
- Added caching, local filtering, ranking, and progressive result views.
- Added structured coverage and error reporting.
