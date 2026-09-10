"""Flight-search strategies with explicit applicability and risk boundaries."""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from itertools import product
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_flights.models import BatchReport, RankedFlight, SearchSpec, SegmentFilters
from agentic_flights.views import _result_id


class StrategyRisk(StrEnum):
    STANDARD = "standard"
    SEPARATE_TICKET = "separate_ticket"
    CONTRACT_SENSITIVE = "contract_sensitive"
    UNSUPPORTED = "unsupported"


class FlightStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    name: str
    purpose: str
    search_pattern: str
    risk: StrategyRisk
    default_enabled: bool
    requirements: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)


class AirportAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport: str
    access_mode: Literal["ground", "separate_flight"]
    estimated_cost: float | None = Field(default=None, ge=0)
    estimated_minutes: int | None = Field(default=None, ge=0)
    minimum_buffer_minutes: int = Field(default=0, ge=0)

    @field_validator("airport")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        return SearchSpec.validate_airport(value.strip())

    @model_validator(mode="after")
    def validate_estimate(self) -> AirportAccess:
        if (self.estimated_cost is None) != (self.estimated_minutes is None):
            raise ValueError("provide both estimated_cost and estimated_minutes, or neither")
        if self.access_mode == "ground" and self.estimated_cost is None:
            raise ValueError("ground access requires estimated cost and time")
        return self


class StrategyHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    strategy_id: str
    risk: StrategyRisk
    searches: list[dict[str, Any]] = Field(min_length=1)
    component_roles: list[str] = Field(min_length=1)
    true_origin: str
    true_destination: str
    ticketed_origin: str
    ticketed_destination: str
    access_cost: float | None = Field(default=None, ge=0)
    access_minutes: int | None = Field(default=None, ge=0)
    minimum_buffer_minutes: int = Field(default=0, ge=0)
    separate_tickets: bool = False
    access_priced_by_search: bool = False
    buffer_nights: int = Field(default=0, ge=0)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_components(self) -> StrategyHypothesis:
        if len(self.searches) != len(self.component_roles):
            raise ValueError("component_roles must align with searches")
        return self


class RouteEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport(cls, value: str) -> str:
        return SearchSpec.validate_airport(value.strip())

    @model_validator(mode="after")
    def validate_route(self) -> RouteEdge:
        if self.origin == self.destination:
            raise ValueError("route origin and destination must differ")
        return self


class RouteGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[RouteEdge]
    source: str
    observed_at: date

    def outgoing(self, origin: str) -> set[str]:
        return {route.destination for route in self.routes if route.origin == origin}

    def can_reach(self, origin: str, destination: str, max_legs: int) -> bool:
        frontier = {origin}
        visited = set(frontier)
        for _ in range(max_legs):
            frontier = {
                airport
                for current in frontier
                for airport in self.outgoing(current)
                if airport not in visited
            }
            if destination in frontier:
                return True
            visited.update(frontier)
        return False

    def positioning_gateways(
        self,
        origin: str,
        destination: str,
        max_main_legs: int | None = 2,
        candidate_mode: Literal["path", "reciprocal", "all_outgoing"] = "path",
    ) -> list[str]:
        if max_main_legs is not None and max_main_legs < 1:
            raise ValueError("max_main_legs must be positive")
        outgoing = self.outgoing(origin) - {destination}
        if candidate_mode == "all_outgoing":
            return sorted(outgoing)
        if candidate_mode == "reciprocal":
            return sorted(outgoing & self.outgoing(destination))
        return sorted(
            gateway
            for gateway in outgoing
            if (
                max_main_legs is None
                or self.can_reach(gateway, destination, max_main_legs)
            )
        )

    def beyond_destinations(self, destination: str, origin: str) -> list[str]:
        return sorted(self.outgoing(destination) - {origin})


STRATEGIES = (
    FlightStrategy(
        strategy_id="flexible_dates_and_stays",
        name="Flexible dates and trip lengths",
        purpose="Find fare changes around the requested dates without changing the trip itself.",
        search_pattern="Sweep departure dates and stay lengths before narrowing the shortlist.",
        risk=StrategyRisk.STANDARD,
        default_enabled=True,
    ),
    FlightStrategy(
        strategy_id="nearby_origin_airports",
        name="Nearby departure airports",
        purpose="Compare airports reachable by practical ground transport.",
        search_pattern="Search each reachable origin and add round-trip ground cost and time.",
        risk=StrategyRisk.STANDARD,
        default_enabled=True,
        requirements=["ground_access_cost", "ground_access_time", "transfer_buffer"],
    ),
    FlightStrategy(
        strategy_id="nearby_destination_airports",
        name="Nearby arrival airports",
        purpose="Compare flying near the destination and completing the trip by ground transport.",
        search_pattern=(
            "Search each viable destination airport and add onward ground cost and time."
        ),
        risk=StrategyRisk.STANDARD,
        default_enabled=True,
        requirements=["ground_transfer_cost", "ground_transfer_time", "service_schedule"],
    ),
    FlightStrategy(
        strategy_id="positioning_gateway",
        name="Positioning through a fare gateway",
        purpose="Reach a potentially cheaper long-haul origin before starting the main ticket.",
        search_pattern=(
            "Price the feeder and main ticket separately, then combine door-to-door cost."
        ),
        risk=StrategyRisk.SEPARATE_TICKET,
        default_enabled=True,
        requirements=["feeder_fare", "main_fare", "connection_buffer", "baggage_recheck"],
        failure_modes=[
            "The main airline does not protect a missed connection from the feeder ticket.",
            "Baggage may need collection and recheck.",
            "An overnight buffer or hotel can erase the saving.",
        ],
    ),
    FlightStrategy(
        strategy_id="split_ticket_connection",
        name="Split-ticket connection",
        purpose="Combine independently priced airlines or tickets at an intermediate airport.",
        search_pattern=(
            "Search each ticket separately and join only connections with a safe buffer."
        ),
        risk=StrategyRisk.SEPARATE_TICKET,
        default_enabled=False,
        requirements=["connection_buffer", "terminal_change", "entry_rules", "baggage_recheck"],
        failure_modes=[
            "Neither airline must protect the onward journey after a disruption.",
            "Transit may require immigration, a visa, or a terminal change.",
        ],
    ),
    FlightStrategy(
        strategy_id="mixed_one_way_tickets",
        name="Different airlines each way",
        purpose="Compare two one-way tickets with a conventional round trip.",
        search_pattern="Search outbound and return independently and combine exact totals.",
        risk=StrategyRisk.STANDARD,
        default_enabled=True,
        requirements=["two_complete_one_way_totals"],
    ),
    FlightStrategy(
        strategy_id="open_jaw_surface_sector",
        name="Open jaw with a surface sector",
        purpose="Fly into one city and home from another when ground travel connects the trip.",
        search_pattern="Search one multi-city ticket and price the surface sector separately.",
        risk=StrategyRisk.STANDARD,
        default_enabled=True,
        requirements=["surface_sector_cost", "surface_sector_time"],
    ),
    FlightStrategy(
        strategy_id="long_stopover",
        name="Useful long layover or stopover",
        purpose="Turn connection time into a second destination when the schedule permits.",
        search_pattern="Compare ordinary connections with multi-city pricing around the hub.",
        risk=StrategyRisk.STANDARD,
        default_enabled=False,
        requirements=["entry_rules", "airport_transfer_time", "minimum_useful_stopover"],
    ),
    FlightStrategy(
        strategy_id="fifth_freedom_segment",
        name="Fifth-freedom segment",
        purpose="Check international segments sold by an airline based in a third country.",
        search_pattern="Include known fifth-freedom operators when they serve the requested route.",
        risk=StrategyRisk.STANDARD,
        default_enabled=False,
        requirements=["current_route_schedule"],
    ),
    FlightStrategy(
        strategy_id="truthful_point_of_sale",
        name="Eligible point-of-sale comparison",
        purpose="Compare lawful local pricing when the traveler genuinely qualifies to buy it.",
        search_pattern=(
            "Repeat the same itinerary only for truthful country and residency contexts."
        ),
        risk=StrategyRisk.CONTRACT_SENSITIVE,
        default_enabled=False,
        requirements=["truthful_residency", "accepted_payment_method", "fare_eligibility"],
        failure_modes=["A fare can require local residency, payment, or documentation."],
    ),
    FlightStrategy(
        strategy_id="hidden_city_final_leg",
        name="Hidden-city final leg",
        purpose="Detect a longer ticket that connects at the traveler's actual destination.",
        search_pattern=(
            "Search beyond destinations while requiring the intended city as a connection."
        ),
        risk=StrategyRisk.CONTRACT_SENSITIVE,
        default_enabled=False,
        requirements=[
            "explicit_user_opt_in",
            "carry_on_only",
            "final_leg_only",
            "separate_return_reservation",
            "current_airline_terms_review",
        ],
        failure_modes=[
            "Checked or gate-checked baggage normally continues to the ticketed destination.",
            "Skipping a segment can cancel every later segment on the same reservation.",
            "Irregular operations can reroute the traveler away from the intended connection.",
            "The airline may apply remedies under its current contract or loyalty rules.",
        ],
    ),
    FlightStrategy(
        strategy_id="throwaway_return",
        name="Unused return segment",
        purpose="Detect when a round trip is cheaper than the needed one-way journey.",
        search_pattern=(
            "Compare one-way and round-trip totals without assuming the unused leg is safe."
        ),
        risk=StrategyRisk.CONTRACT_SENSITIVE,
        default_enabled=False,
        requirements=["explicit_user_opt_in", "final_segment_only", "current_airline_terms_review"],
        failure_modes=["Skipping a segment can cancel later travel on the same reservation."],
    ),
    FlightStrategy(
        strategy_id="back_to_back_or_nested_tickets",
        name="Back-to-back or nested tickets",
        purpose="Detect overlapping tickets used to change fare construction.",
        search_pattern="Model only for explicit diagnostics; never include in an automatic sweep.",
        risk=StrategyRisk.CONTRACT_SENSITIVE,
        default_enabled=False,
        requirements=["explicit_user_opt_in", "current_airline_terms_review"],
        failure_modes=["Overlapping ticket use can conflict with airline contract terms."],
    ),
    FlightStrategy(
        strategy_id="award_inventory",
        name="Cash versus points",
        purpose="Compare cash fares with airline or bank-program award inventory.",
        search_pattern="Requires authenticated award inventory and account-specific balances.",
        risk=StrategyRisk.UNSUPPORTED,
        default_enabled=False,
        requirements=["award_inventory_provider", "points_balance", "taxes_and_fees"],
    ),
    FlightStrategy(
        strategy_id="mistake_fare_monitoring",
        name="Mistake-fare monitoring",
        purpose="Watch for short-lived fares that cannot be discovered reliably in one search.",
        search_pattern="Requires recurring monitoring and a notification policy.",
        risk=StrategyRisk.UNSUPPORTED,
        default_enabled=False,
        requirements=["recurring_monitor", "notification_policy"],
    ),
)


def list_strategies(
    *, include_contract_sensitive: bool = False, include_unsupported: bool = False
) -> list[FlightStrategy]:
    return [
        strategy
        for strategy in STRATEGIES
        if (include_contract_sensitive or strategy.risk != StrategyRisk.CONTRACT_SENSITIVE)
        and (include_unsupported or strategy.risk != StrategyRisk.UNSUPPORTED)
    ]


def build_strategy_plan(
    trip: dict[str, Any],
    *,
    nearby_origins: list[dict[str, Any]] | None = None,
    positioning_origins: list[dict[str, Any]] | None = None,
    nearby_destinations: list[dict[str, Any]] | None = None,
    hidden_city_destinations: list[str] | None = None,
    route_graph: dict[str, Any] | None = None,
    auto_positioning: bool = True,
    auto_hidden_city: bool = False,
    max_gateway_main_legs: int | None = 2,
    gateway_candidate_mode: Literal["path", "reciprocal", "all_outgoing"] = "path",
    allow_contract_sensitive: bool = False,
    carry_on_only: bool = False,
) -> dict[str, Any]:
    raw = {
        "request_id": "strategy-template",
        "search_mode": "discover",
        **trip,
        "continuation": None,
        "preferred_outbound": None,
    }
    base = SearchSpec.model_validate(raw)
    origins = [AirportAccess.model_validate(item) for item in nearby_origins or []]
    gateways = [AirportAccess.model_validate(item) for item in positioning_origins or []]
    destinations = [AirportAccess.model_validate(item) for item in nearby_destinations or []]
    beyond = [
        SearchSpec.validate_airport(value.strip()) for value in hidden_city_destinations or []
    ]
    graph = RouteGraph.model_validate(route_graph) if route_graph is not None else None
    if graph and auto_positioning:
        known_gateways = {access.airport for access in gateways}
        gateways.extend(
            AirportAccess(airport=airport, access_mode="separate_flight")
            for airport in graph.positioning_gateways(
                base.origin,
                base.destination,
                max_gateway_main_legs,
                gateway_candidate_mode,
            )
            if airport not in known_gateways
        )
    if graph and auto_hidden_city:
        beyond = list(
            dict.fromkeys([*beyond, *graph.beyond_destinations(base.destination, base.origin)])
        )
    hypotheses = [
        _hypothesis(
            "direct",
            "flexible_dates_and_stays",
            StrategyRisk.STANDARD,
            [base],
            base,
        )
    ]
    for index, access in enumerate(origins):
        hypotheses.append(
            _hypothesis(
                f"nearby-origin-{index}",
                "nearby_origin_airports",
                StrategyRisk.STANDARD,
                [base.model_copy(update={"origin": access.airport})],
                base,
                access=access,
            )
        )
    for index, access in enumerate(gateways):
        main = base.model_copy(update={"origin": access.airport})
        access_priced_by_search = (
            access.access_mode == "separate_flight"
            and access.estimated_cost is None
            and access.estimated_minutes is None
        )
        searches = [main]
        roles = ["main_ticket"]
        buffer_nights = 0
        if access_priced_by_search:
            searches.append(_positioning_ticket(base, access.airport))
            roles.append("positioning_ticket")
            buffer_nights = 2 if base.return_date is not None else 1
        hypotheses.append(
            _hypothesis(
                f"positioning-origin-{index}",
                "positioning_gateway",
                StrategyRisk.SEPARATE_TICKET,
                searches,
                base,
                access=access,
                component_roles=roles,
                separate_tickets=True,
                access_priced_by_search=access_priced_by_search,
                buffer_nights=buffer_nights,
                caveats=[
                    "Add the positioning journey in both directions before comparing totals.",
                    "Use a disruption buffer; the main airline may not protect a separate feeder.",
                    "Conservative positioning dates can add hotel nights.",
                ],
            )
        )
    for index, access in enumerate(destinations):
        hypotheses.append(
            _hypothesis(
                f"nearby-destination-{index}",
                "nearby_destination_airports",
                StrategyRisk.STANDARD,
                [base.model_copy(update={"destination": access.airport})],
                base,
                access=access,
            )
        )
    if base.return_date is not None and not base.additional_segments:
        outbound = base.model_copy(update={"return_date": None})
        inbound = base.model_copy(
            update={
                "origin": base.destination,
                "destination": base.origin,
                "departure_date": base.return_date,
                "return_date": None,
                "segment_filters": base.return_segment_filters,
                "return_segment_filters": SegmentFilters(),
            }
        )
        hypotheses.append(
            _hypothesis(
                "mixed-one-ways",
                "mixed_one_way_tickets",
                StrategyRisk.STANDARD,
                [outbound, inbound],
                base,
                component_roles=["outbound_ticket", "return_ticket"],
                separate_tickets=True,
                caveats=["Compare the sum of two verified one-way totals with the round trip."],
            )
        )
    excluded = []
    for index, ticketed_destination in enumerate(beyond):
        reason = _hidden_city_exclusion(base, allow_contract_sensitive, carry_on_only)
        if reason:
            excluded.append(
                {
                    "strategy_id": "hidden_city_final_leg",
                    "ticketed_destination": ticketed_destination,
                    "reason": reason,
                }
            )
            continue
        connections = list(
            dict.fromkeys([*base.segment_filters.connecting_airports, base.destination])
        )
        outbound = base.model_copy(
            update={
                "destination": ticketed_destination,
                "return_date": None,
                "additional_segments": [],
                "checked_bags": 0,
                "overhead_cabin_bags": max(1, base.overhead_cabin_bags),
                "segment_filters": base.segment_filters.model_copy(
                    update={"connecting_airports": connections}
                ),
            }
        )
        searches = [outbound]
        if base.return_date is not None:
            searches.append(
                base.model_copy(
                    update={
                        "origin": base.destination,
                        "destination": base.origin,
                        "departure_date": base.return_date,
                        "return_date": None,
                        "additional_segments": [],
                        "segment_filters": base.return_segment_filters,
                        "return_segment_filters": SegmentFilters(),
                    }
                )
            )
        hypotheses.append(
            _hypothesis(
                f"hidden-city-{index}",
                "hidden_city_final_leg",
                StrategyRisk.CONTRACT_SENSITIVE,
                searches,
                base,
                component_roles=(
                    ["hidden_city_outbound", "return_ticket"]
                    if len(searches) == 2
                    else ["hidden_city_outbound"]
                ),
                ticketed_destination=ticketed_destination,
                separate_tickets=base.return_date is not None,
                caveats=next(
                    strategy.failure_modes
                    for strategy in STRATEGIES
                    if strategy.strategy_id == "hidden_city_final_leg"
                ),
            )
        )
    return {
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "excluded": excluded,
        "ordering": "unranked",
        "gateway_candidate_mode": gateway_candidate_mode,
        "route_graph": (
            {
                "source": graph.source,
                "observed_at": graph.observed_at.isoformat(),
                "age_days": (date.today() - graph.observed_at).days,
            }
            if graph
            else None
        ),
        "comparison": (
            "Add access cost, access time, buffers, and separate-ticket risk before applying "
            "the Pareto comparison."
        ),
    }


def evaluate_strategy_results(
    report: BatchReport,
    plan: dict[str, Any],
    request_map: dict[str, dict[str, Any]],
    *,
    max_options_per_search: int = 5,
) -> dict[str, Any]:
    if max_options_per_search < 1:
        raise ValueError("max_options_per_search must be positive")
    outcomes = {outcome.request_id: outcome for outcome in report.outcomes}
    candidates = []
    gateway_probes = []
    incomplete = []
    for hypothesis in plan["hypotheses"]:
        mapped = sorted(
            (
                (int(request_id), outcomes.get(request_id))
                for request_id, metadata in request_map.items()
                if metadata["hypothesis_id"] == hypothesis["hypothesis_id"]
            ),
            key=lambda item: item[0],
        )
        if len(mapped) != len(hypothesis["searches"]) or any(
            outcome is None or outcome.status != "success" for _, outcome in mapped
        ):
            incomplete.append(
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "strategy_id": hypothesis["strategy_id"],
                    "reason": "One or more component searches are unfinished or failed.",
                }
            )
            continue
        option_groups = [outcome.options[:max_options_per_search] for _, outcome in mapped]
        for combination in product(*option_groups):
            currencies = {option.currency for option in combination}
            if len(currencies) != 1 or any(option.price is None for option in combination):
                continue
            airfare = sum(option.price for option in combination if option.price is not None)
            access_cost = hypothesis["access_cost"]
            access_minutes = hypothesis["access_minutes"]
            if hypothesis["access_priced_by_search"]:
                access_cost = 0
                access_minutes = 0
            if access_cost is None or access_minutes is None:
                gateway_probes.append(
                    {
                        "hypothesis_id": hypothesis["hypothesis_id"],
                        "strategy_id": hypothesis["strategy_id"],
                        "risk": hypothesis["risk"],
                        "currency": next(iter(currencies)),
                        "main_ticket_price": airfare,
                        "access_cost": access_cost,
                        "access_minutes": hypothesis["access_minutes"],
                        "result_ids": [
                            _result_id(RankedFlight(request_id=str(index), option=option))
                            for (index, _), option in zip(mapped, combination, strict=True)
                        ],
                        "caveats": hypothesis["caveats"],
                    }
                )
                continue
            candidates.append(
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "strategy_id": hypothesis["strategy_id"],
                    "risk": hypothesis["risk"],
                    "currency": next(iter(currencies)),
                    "airfare": airfare,
                    "access_cost": access_cost,
                    "total_price": airfare + access_cost,
                    "flight_minutes": sum(option.duration_minutes for option in combination),
                    "access_minutes": access_minutes,
                    "buffer_minutes": hypothesis["minimum_buffer_minutes"],
                    "buffer_nights": hypothesis["buffer_nights"],
                    "total_minutes": (
                        sum(option.duration_minutes for option in combination)
                        + access_minutes
                        + hypothesis["minimum_buffer_minutes"]
                    ),
                    "total_stops": sum(option.stops for option in combination),
                    "ticket_count": len(combination),
                    "evidence_level": (
                        "provider_final_total"
                        if len(combination) == 1
                        and combination[0].ticket_scope == "complete_single_ticket"
                        and combination[0].price_provenance == "provider_final_total"
                        else "observed_composite"
                        if len(combination) > 1
                        else "observed_price"
                    ),
                    "result_ids": [
                        _result_id(RankedFlight(request_id=str(index), option=option))
                        for (index, _), option in zip(mapped, combination, strict=True)
                    ],
                    "components": [
                        {
                            "role": role,
                            "price": option.price,
                            "currency": option.currency,
                        }
                        for role, option in zip(
                            hypothesis["component_roles"], combination, strict=True
                        )
                    ],
                    "caveats": hypothesis["caveats"],
                }
            )

    def dominates(left, right):
        if (left["risk"], left["currency"]) != (right["risk"], right["currency"]):
            return False
        left_values = (
            left["total_price"],
            left["total_minutes"],
            left["total_stops"],
            left["ticket_count"],
        )
        right_values = (
            right["total_price"],
            right["total_minutes"],
            right["total_stops"],
            right["ticket_count"],
        )
        return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and (
            left_values != right_values
        )

    frontier = [
        candidate
        for candidate in candidates
        if not any(dominates(other, candidate) for other in candidates)
    ]
    direct_prices = {}
    for candidate in candidates:
        if candidate["hypothesis_id"] != "direct":
            continue
        current = direct_prices.get(candidate["currency"])
        if current is None or candidate["total_price"] < current:
            direct_prices[candidate["currency"]] = candidate["total_price"]
    promising_probes = []
    for probe in gateway_probes:
        baseline = direct_prices.get(probe["currency"])
        headroom = None if baseline is None else baseline - probe["main_ticket_price"]
        promising_probes.append(
            {
                **probe,
                "direct_price_baseline": baseline,
                "maximum_access_cost_to_beat_direct": headroom,
                "price_opportunity": bool(headroom is not None and headroom > 0),
            }
        )
    return {
        "frontier": frontier,
        "gateway_probes": promising_probes,
        "evaluated_candidates": len(candidates),
        "incomplete_hypotheses": incomplete,
        "ordering": "unranked_pareto_frontier_by_risk_and_currency",
        "hidden_weights": False,
        "comparison_dimensions": [
            "total_price",
            "total_minutes",
            "total_stops",
            "ticket_count",
        ],
    }


def _hypothesis(
    hypothesis_id: str,
    strategy_id: str,
    risk: StrategyRisk,
    searches: list[SearchSpec],
    base: SearchSpec,
    *,
    access: AirportAccess | None = None,
    component_roles: list[str] | None = None,
    ticketed_destination: str | None = None,
    separate_tickets: bool = False,
    access_priced_by_search: bool = False,
    buffer_nights: int = 0,
    caveats: list[str] | None = None,
) -> StrategyHypothesis:
    return StrategyHypothesis(
        hypothesis_id=hypothesis_id,
        strategy_id=strategy_id,
        risk=risk,
        searches=[_agent_search(search) for search in searches],
        component_roles=component_roles or ["main_ticket"] * len(searches),
        true_origin=base.origin,
        true_destination=base.destination,
        ticketed_origin=searches[0].origin,
        ticketed_destination=ticketed_destination or searches[0].destination,
        access_cost=access.estimated_cost if access else 0,
        access_minutes=access.estimated_minutes if access else 0,
        minimum_buffer_minutes=access.minimum_buffer_minutes if access else 0,
        separate_tickets=separate_tickets,
        access_priced_by_search=access_priced_by_search,
        buffer_nights=buffer_nights,
        caveats=caveats or [],
    )


def _positioning_ticket(base: SearchSpec, gateway: str) -> SearchSpec:
    return base.model_copy(
        update={
            "origin": base.origin,
            "destination": gateway,
            "departure_date": base.departure_date - timedelta(days=1),
            "return_date": (
                base.return_date + timedelta(days=1) if base.return_date is not None else None
            ),
            "additional_segments": [],
            "segment_filters": SegmentFilters(),
            "return_segment_filters": SegmentFilters(),
        }
    )


def _agent_search(spec: SearchSpec) -> dict[str, Any]:
    return spec.model_dump(
        mode="json",
        exclude={
            "request_id",
            "search_mode",
            "continuation",
            "preferred_outbound",
            "max_complete_quotes",
            "max_browser_transitions",
            "stage_candidate_offsets",
            "candidates_per_stage",
        },
    )


def _hidden_city_exclusion(
    base: SearchSpec, allow_contract_sensitive: bool, carry_on_only: bool
) -> str | None:
    if not allow_contract_sensitive:
        return "Explicit user opt-in is required for contract-sensitive strategies."
    if not carry_on_only or base.checked_bags:
        return "Hidden-city evaluation requires carry-on-only travel and no checked bags."
    if base.additional_segments:
        return "Hidden-city evaluation is limited to a final leg, not a multi-city reservation."
    return None
