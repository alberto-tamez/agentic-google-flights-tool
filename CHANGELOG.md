# Changelog

This project uses semantic versioning once releases begin.

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
