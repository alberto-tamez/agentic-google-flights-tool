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

Call `AgentAPI.playbook()` for the catalog. Call `AgentAPI.strategy_plan()` with the
base trip and a current route graph to derive gateway and beyond-destination candidates
without city-specific rules. Explicit candidates remain available for ground airports
or manual overrides. The plan is unranked. Ground links, entry rules, and airline
conditions still need current sources.

The gateway algorithm starts from the finite set of airports directly reachable from
the true origin. It retains gateway G when the route graph can reach the true destination
from G within the configured number of flight legs. It then prices G to the destination
and reports the maximum feeder cost that could still beat the direct fare. Only gateways
with positive price headroom need feeder and connection testing.

## Positioning gateways

A gateway is useful only after adding the complete access journey in both directions.
The planner keeps access cost, access time, and minimum connection buffer separate from
the airfare. A separate feeder ticket must remain visibly separate because the main
airline may not protect a missed connection. Baggage collection, terminal changes,
entry rules, hotels, and disruption buffers can erase a headline saving.

For example, a route graph may show a Mexican origin connected to a leisure gateway
that also reaches Madrid. The program discovers that topology for any airport codes; it
does not contain a Cancun or Mexico rule. A lower gateway fare is only a probe until the
feeder is priced and the complete door-to-door result is feasible.

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

## Coverage boundary

The current engine can derive and execute direct, nearby-airport, positioning-gateway,
mixed one-way, and opt-in hidden-city hypotheses from a route graph. A comprehensive
automatic sweep still needs a maintained route-graph source plus current sources for
feeder schedules, fifth-freedom routes, ground
transport, award inventory, and airline-specific contract rules. Missing source data
must be reported as missing coverage, not silently replaced with a fixed airport list.
