# Flight strategy playbook

The playbook expands a trip into search hypotheses. It does not hide tradeoffs in one
score and it does not assume that a lower airfare is a cheaper trip.

## Decision model

1. Preserve the traveler's hard constraints.
2. Generate applicable route and ticket hypotheses.
3. Search each hypothesis independently.
4. Add access fares, ground travel, buffers, hotels, baggage, and separate-ticket risk.
5. Remove strictly dominated options within comparable evidence and currency.
6. Present the remaining choices as an unranked tradeoff set.

The agent chooses a recommendation from the traveler's stated priorities. Without
those priorities, it labels the cheapest, shortest, simplest, and highest-confidence
choices rather than inventing a weighted score.
Each candidate includes `savings_vs_direct` against the lowest observed direct fare.
That is an arithmetic comparison, not a conversion of time or risk into money.

## Strategy groups

| Group | Strategies | Default |
| --- | --- | --- |
| Search flexibility | dates, trip lengths, nearby origins, nearby destinations | On |
| Fare gateways | positioning through a cheaper long-haul origin | On when access data exists |
| Ticket construction | mixed one-ways, open jaw, useful stopover | Mixed one-ways and open jaw |
| Separate tickets | self-transfer and split-ticket connections | Off unless requested |
| Route-specific | fifth-freedom segments | Off until a current route source supplies candidates |
| Contract-sensitive | hidden city, unused return, back-to-back or nested tickets | Always off |
| External inventory | points and mistake-fare monitoring | Unsupported without another provider |

Each machine-readable entry also has an implementation status:

- `automated` means the program can generate and execute the search.
- `manual_input` means the tactic is supported after the traveler or agent supplies
  intent or ground-access facts that cannot be inferred safely.
- `external_provider` means another current inventory source is required.
- `advisory_only` means the program explains the tactic and risk but does not automate it.

Call `AgentAPI.playbook()` for the catalog. Call `AgentAPI.strategy_plan()` with the
base trip and a current route graph to derive gateway and beyond-destination candidates
without city-specific rules. Explicit candidates remain available for ground airports
or manual overrides. The plan is unranked. Ground links, entry rules, and airline
conditions still need current sources.

`AgentAPI.start_auto_strategy_plan()` obtains that graph from air-routes.com's public
airport-destinations endpoint. It makes one topology request for ordinary positioning
and a second for opt-in hidden-city candidates, then caches each airport response for
seven days. The topology says which passenger routes operate now; live fare discovery
still tests the requested date because topology is not date-specific availability.

The automatic first pass uses `gateway_candidate_mode="reciprocal"`. It keeps airports
listed from both the origin and destination, a cheap proxy for service in both
directions. This can miss asymmetric or one-direction seasonal service. Use
`gateway_candidate_mode="all_outgoing"` for the exhaustive second pass; bounded work
chunks checkpoint the much larger set instead of pretending it is free.

The gateway algorithm starts from the finite set of airports directly reachable from
the true origin. It retains gateway G when the route graph can reach the true destination
from G within the configured number of flight legs. The bounded sweep prices both the
main ticket and a conservative positioning ticket. For a round trip, the positioning
ticket reaches G the day before and leaves G the day after the main ticket. This avoids
pretending an unverified same-day connection is safe, but it can add two hotel nights.

## Positioning gateways

A gateway is useful only after adding the complete access journey in both directions.
The planner keeps access cost, access time, and minimum connection buffer separate from
the airfare. A separate feeder ticket must remain visibly separate because the main
airline may not protect a missed connection. Baggage collection, terminal changes,
entry rules, hotels, and disruption buffers can erase a headline saving.

For example, a route graph may show a Mexican origin connected to a leisure gateway
that also reaches Madrid. The program discovers that topology for any airport codes; it
does not contain a Cancun or Mexico rule. The result combines the observed main and
positioning fares, labels them as separate tickets, and keeps schedule feasibility and
hotel cost as unresolved until verified.

## Hidden-city final legs

Hidden-city evaluation is contract-sensitive and never runs by default. The caller
must explicitly enable contract-sensitive strategies and confirm carry-on-only travel.
The planner only models skipping the final flight. It creates a separate return search
so the skipped segment cannot cancel the return on the same reservation.

The result must retain these warnings:

- Checked or gate-checked baggage can continue to the ticketed destination.
- Irregular operations can reroute the traveler away from the intended connection.
- Skipping a segment can cancel every later segment on the same reservation.
- Airline contract and loyalty consequences require a current terms review.

The program may detect and explain the price difference. It must never present the
option with the same confidence or operational safety as a conventional itinerary.
Carrier rules vary and change. The machine-readable catalog includes current reference
links for [American](https://www.aa.com/i18n/customer-service/support/conditions-of-carriage.html),
[Delta](https://www.delta.com/us/en/legal/contract-of-carriage-igr), and
[Lufthansa](https://www.lufthansa.com/us/en/terms-and-conditions-lh). The agent must
check the operating and ticketing carriers rather than treating those examples as a
universal rule.

Unused-return evaluation is also opt-in. For a genuinely one-way trip, the planner can
compare round-trip fares across a configurable stay horizon. Each result remains in the
contract-sensitive risk class, and the unused return must be the final segment. The
default horizon is fourteen nights; changing it changes coverage and must be disclosed.

## Coverage boundary

The current engine can derive and execute direct, nearby-airport, positioning-gateway,
mixed one-way, and opt-in hidden-city hypotheses from a route graph. A comprehensive
automatic sweep still needs a maintained route-graph source plus current sources for
exact connection feasibility, fifth-freedom routes, ground
transport, award inventory, and airline-specific contract rules. Missing source data
must be reported as missing coverage, not silently replaced with a fixed airport list.
