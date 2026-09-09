# Changelog

## 0.6.8

- Stop verification exploration after every selected itinerary has a matching complete
  quote, while preserving unused provider branches in the saved diagnostic record.
- Bound verification calls to three quote attempts and twelve browser transitions by
  default. Callers can override either limit or pass `None` for an unbounded chunk.
- Prioritize the selected schedule at every journey, including the return, so a
  whole-itinerary check does not search unrelated return branches first.
- Return an unranked Pareto frontier with explicit tradeoff strengths instead of a
  hidden composite score or an implied objective best option.
- Give every issue stable top-level code, message, and retryability fields, including
  incomplete coverage where the nested query error is null.
- Report saved continuation and exhausted work budgets as explicit issues instead of
  returning an empty issue list for unfinished work.
- Preserve a selected flight that yields no onward choices as a retryable branch error
  instead of silently discarding it.
- Keep run IDs, result IDs, continuation state, branch counts, and provider diagnostics
  out of user-facing progress and recommendations unless the user requests them.
- Require the bundled agent skill to check for a compatible installed runtime before
  searching.

## 0.6.7

- Preserve connection-level carrier and flight-number identity from Google booking
  URLs, including when the visible quote collapses a connection to journey endpoints.
- Separate insufficient itinerary detail from a confirmed mismatch. Label complete
  quotes that do not verify the selected flight in compact API responses.
- Keep later-return and destination-time tradeoffs in alternatives, and expose
  per-journey times, destination stay, and overnight travel in slim results.
- Limit default per-query coverage detail to three incomplete queries; aggregate
  coverage counts and `issues()` retain the full status.
- Remove the stale tag-triggered PyPI workflow. Releases remain manual.

## 0.6.6

- Add `AgentAPI.start()` and an MCP `start` operation for compact, in-memory exact
  searches without temporary request files.
- Keep discovery HTTP-only. Browser startup is now limited to explicit final-price
  or baggage verification.
- Use Playwright's separate headless shell for verification so background work does
  not register the full Chrome-for-Testing application with macOS.
- Add a subprocess-isolated browser stability test and compact classification for
  native browser crashes and macOS process restrictions.
- Shorten the bundled skill and state its authorization, retry, and side-effect limits.
- Include verification match scope in comparison, inspection, and issues responses.
- Explicitly report whether the whole selected itinerary matched and which
  journeys were not compared. A complete fare is not proof of a matching return.
- Test changed returns for round trips and three-journey selections, including
  preservation of the scope fields through MCP serialization.

## 0.6.5

- Keep no-self-transfer discovery on HTTP instead of forcing browser processing.
- Checkpoint batches before searching and after each query; emit progress and
  heartbeats on stderr while keeping stdout as one JSON response.
- Add `resume RUN_ID` and automatic bounded CLI work chunks without dropping
  the original search scope. Explicit exports are updated atomically.
- Share per-query time limits across built-in HTTP and browser providers;
  preserve unfinished verification branches and completed quotes on timeout.
- Checkpoint API exploration and verification, with optional progress callbacks.
- Test early completion, interruption/resume, and timeout state preservation.

## 0.6.4

- Run verification in Chromium's newer headless mode with a desktop Chrome
  user-agent. Visible windows require explicit debugging opt-in.
- Detect Google's "Oops, something went wrong" page during result and booking
  navigation instead of waiting for missing elements to time out.
- Test the default launch mode and error-page behavior against a real local DOM.

## 0.6.3

- Recover direct discovery from empty RPC envelopes using fast-flights' HTTP
  retrieval and embedded JSON parsing, including Google's reject-cookies flow.
- Parse omitted zero time components and report schema changes as errors.
- Mark initial-page coverage as incomplete and invalidate old provider caches.
- Use a visible tool-managed browser for verification; headless mode is opt-in.
- Return CLI exit status 1 when searches fail while retaining the JSON report.
- Preserve HTTP airline names and match verification on shared flight evidence;
  reject conflicting flight numbers when both sources provide them.
- Test the installed command, real HTTP-response replay, and a live browser-free
  search/list/show workflow that requires priced flights.

## 0.6.2

- Reuse and close browser providers; prefer managed Chromium and stop repeating
  failed launches or requests blocked by Google within a worker.
- Report unsupported browser airline exclusions instead of silently dropping them,
  and preserve direct-provider errors when browser fallback also fails.
- Clarify exact, multi-city, and directional-filter workflows in the skill.
- Add `agentic-flights --version` and exclude local experiments from Git and builds.
- Remove retired skill copies and legacy `reverse-google-flights` storage names.

## 0.6.1

- Updated the bundled Codex and Claude Code skill to use smart provider selection
  and preserve the expanded passenger, baggage, connection, and fare preferences.

## 0.6.0

- Replaced the required fast-flights package with an attributed internal Google
  Flights query encoder.
- Replaced the optional Fli adapter with an attributed native direct-search provider.
- Added a smart provider that uses direct requests for discovery and browser traversal
  for current total-price and baggage verification.
- Added children, infants, checked bags, airline exclusions, connection airports,
  layover windows, lower-emissions filtering, self-transfer hiding, and basic-economy
  exclusion to search inputs.

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
