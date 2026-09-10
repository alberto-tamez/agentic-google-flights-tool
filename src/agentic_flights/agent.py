from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_flights.api import AgentAPI
from agentic_flights.models import FlightOption, MaxStops, RankedFlight, SearchSpec
from agentic_flights.views import _result_id, _slim

AGENT_REPLY_MAX_BYTES = 8192
MAX_DECISION_ROWS = 4
AGENT_CONTRACT = "flight-decision-v1"


class Priority(StrEnum):
    PRICE = "price"
    DURATION = "duration"
    STOPS = "stops"
    DEPARTURE = "departure"


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaggageIntent(AgentModel):
    overhead_cabin_bags: int = Field(default=0, ge=0, le=9)
    checked_bags: int = Field(default=0, ge=0, le=9)
    require_overhead_cabin_bag: bool = False

    @model_validator(mode="after")
    def validate_requirement(self) -> BaggageIntent:
        if self.require_overhead_cabin_bag and self.overhead_cabin_bags < 1:
            raise ValueError("required overhead baggage needs at least one bag")
        return self


class AgentTripRequest(AgentModel):
    origins: tuple[str, ...] = Field(min_length=1)
    destinations: tuple[str, ...] = Field(min_length=1)
    departure_start: date
    departure_end: date
    min_nights: int | None = Field(default=None, ge=0)
    max_nights: int | None = Field(default=None, ge=0)
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    cabin: Literal["economy", "premium_economy", "business", "first"] = "economy"
    max_stops: MaxStops = MaxStops.ANY
    currency: str
    language: str = "en-US"
    country: str = "ES"
    baggage: BaggageIntent = BaggageIntent()
    priorities: tuple[Priority, ...] = ()

    @field_validator("origins", "destinations")
    @classmethod
    def validate_airports(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(SearchSpec.validate_airport(value) for value in values))

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return SearchSpec.validate_currency(value)

    @model_validator(mode="after")
    def validate_scope(self) -> AgentTripRequest:
        if self.departure_end < self.departure_start:
            raise ValueError("departure window must be ordered")
        if (self.min_nights is None) != (self.max_nights is None):
            raise ValueError("provide both stay bounds or neither")
        if self.min_nights is not None and self.min_nights > self.max_nights:
            raise ValueError("stay bounds must be ordered")
        if len(set(self.priorities)) != len(self.priorities):
            raise ValueError("priorities must be unique")
        return self


class AgentCoverage(AgentModel):
    attempted_queries: int
    remaining_queries: int
    failed_queries: int
    search_complete: bool
    provider_truncated: bool
    parse_failures: int
    unresolved: tuple[str, ...] = ()


class DecisionJourney(AgentModel):
    origin: str
    destination: str
    departure_at: str = Field(max_length=40)
    arrival_at: str = Field(max_length=40)


class DecisionRow(AgentModel):
    candidate_ref: str = Field(pattern=r"^rf_[0-9a-f]{16}$")
    route: tuple[str, ...] = Field(max_length=8)
    price: float | None
    currency: str
    price_evidence: Literal["observed", "provider_final_total"]
    ticket_scope: Literal["complete_single_ticket", "partial_or_unknown"]
    journeys: tuple[DecisionJourney, ...] = Field(max_length=4)
    duration_minutes: int
    stops: int
    destination_stay_minutes: int | None
    overnight_journeys: tuple[int, ...]
    baggage: Literal["not_requested", "to_verify", "included", "extra_cost", "unknown"]
    strengths: tuple[str, ...] = Field(max_length=6)
    eligibility: Literal["verify_before_recommending", "confirmed"]
    reason_codes: tuple[str, ...] = Field(max_length=8)


class AgentSearchReply(AgentModel):
    kind: Literal["search"] = "search"
    contract: Literal["flight-decision-v1"] = AGENT_CONTRACT
    search_ref: str = Field(pattern=r"^rgf_[0-9a-f]{16}$")
    options: tuple[DecisionRow, ...] = Field(max_length=MAX_DECISION_ROWS)
    coverage: AgentCoverage
    omitted_options: int = Field(ge=0)


class AgentVerificationConfirmed(AgentModel):
    kind: Literal["confirmed"] = "confirmed"
    candidate_ref: str
    option: DecisionRow
    baggage_confirmed: bool


class AgentVerificationNotConfirmed(AgentModel):
    kind: Literal["not_confirmed"] = "not_confirmed"
    candidate_ref: str
    reason_codes: tuple[str, ...]


class AgentVerificationPending(AgentModel):
    kind: Literal["pending"] = "pending"
    candidate_ref: str


class AgentVerificationBlocked(AgentModel):
    kind: Literal["blocked"] = "blocked"
    candidate_ref: str
    retry: Literal["after_environment_change"] = "after_environment_change"


class AgentFailureReply(AgentModel):
    kind: Literal["failure"] = "failure"
    code: Literal[
        "invalid_input",
        "network_environment",
        "provider_failure",
        "expired_search",
        "no_options",
    ]
    retry: Literal["fix_input", "after_environment_change", "never"]
    message: str = Field(max_length=240)


AgentReply = Annotated[
    AgentSearchReply
    | AgentVerificationConfirmed
    | AgentVerificationNotConfirmed
    | AgentVerificationPending
    | AgentVerificationBlocked
    | AgentFailureReply,
    Field(discriminator="kind"),
]


class AgentRuntimeManifest(AgentModel):
    contract: Literal["flight-decision-v1"] = AGENT_CONTRACT
    capabilities: frozenset[Literal["search", "verify", "bounded_replies"]] = frozenset(
        {"search", "verify", "bounded_replies"}
    )
    max_reply_bytes: Literal[8192] = AGENT_REPLY_MAX_BYTES


def agent_runtime() -> AgentRuntimeManifest:
    return AgentRuntimeManifest()


def encode_agent_reply(reply: AgentReply) -> str:
    encoded = reply.model_dump_json()
    if len(encoded.encode()) > AGENT_REPLY_MAX_BYTES:
        raise ValueError("agent reply exceeds 8192 bytes")
    forbidden = {
        "report",
        "search_spec",
        "url",
        "continuation",
        "cursor",
        "next_actions",
        "per_query_coverage",
    }
    if _contains_forbidden(json.loads(encoded), forbidden):
        raise ValueError("agent reply contains an internal field")
    return encoded


def whole_itinerary_confirmed(match: dict[str, Any], option: FlightOption) -> bool:
    return bool(
        option.ticket_scope == "complete_single_ticket"
        and option.price_provenance == "provider_final_total"
        and match.get("whole_itinerary_matched")
        and not match.get("uncompared_journey_indexes")
    )


class FlightAgent:
    def __init__(self, api: AgentAPI | None = None) -> None:
        self.api = api or AgentAPI()

    def search(
        self, request: AgentTripRequest | dict[str, Any]
    ) -> AgentSearchReply | AgentFailureReply:
        parsed = AgentTripRequest.model_validate(request)
        template = {
            "request_id": "agent",
            "origin": parsed.origins[0],
            "destination": parsed.destinations[0],
            "departure_date": parsed.departure_start,
            "adults": parsed.adults,
            "children": parsed.children,
            "cabin": parsed.cabin,
            "max_stops": parsed.max_stops,
            "currency": parsed.currency,
            "language": parsed.language,
            "country": parsed.country,
            "overhead_cabin_bags": parsed.baggage.overhead_cabin_bags,
            "checked_bags": parsed.baggage.checked_bags,
            "search_mode": "discover",
        }
        response = self.api.search_flexible(
            {
                "template": template,
                "origins": parsed.origins,
                "destinations": parsed.destinations,
                "departure_start": parsed.departure_start,
                "departure_end": parsed.departure_end,
                "min_nights": parsed.min_nights,
                "max_nights": parsed.max_nights,
            },
            {
                "currency": parsed.currency,
                "sort_by": list(parsed.priorities) or ["price", "duration", "stops"],
            },
            page_size=20,
            prefer=list(parsed.priorities) or None,
            work_chunk=64,
        )
        report, _, _ = self.api._load(response["run_id"])
        report.exploration_state = {
            **(report.exploration_state or {}),
            "agent_request": parsed.model_dump(mode="json"),
        }
        saved = self.api._save(report)
        search_ref = saved["run_id"]
        alternatives = self.api.alternatives(
            search_ref, {"currency": parsed.currency}, page_size=MAX_DECISION_ROWS
        )
        strengths = {
            item["result_id"]: tuple(item["strengths"]) for item in alternatives["alternatives"]
        }
        details = self.api.inspect(search_ref, alternatives["result_ids"])
        rows = tuple(
            self._row(item, parsed, strengths.get(item["result_id"], ()))
            for item in details["results"]
        )
        progress = response["progress"]
        unresolved = []
        if response.get("source_truncated"):
            unresolved.append("provider_truncated")
        if progress["failed_queries"]:
            unresolved.append("query_failures")
        if not rows and progress["failed_queries"]:
            issues = self.api.issues(search_ref, page_size=1).get("issues", [])
            message = (
                issues[0].get("message", "Network access failed")
                if issues
                else "Network access failed"
            )
            network = any(
                text in message.casefold()
                for text in ("resolve host", "dns", "network is unreachable", "name resolution")
            )
            return AgentFailureReply(
                code="network_environment" if network else "provider_failure",
                retry="after_environment_change" if network else "never",
                message=message[:240],
            )
        reply = AgentSearchReply(
            search_ref=search_ref,
            options=rows,
            coverage=AgentCoverage(
                attempted_queries=progress["attempted_queries"],
                remaining_queries=progress["remaining_queries"],
                failed_queries=progress["failed_queries"],
                search_complete=progress["coverage_complete"],
                provider_truncated=bool(response.get("source_truncated")),
                parse_failures=response.get("source_parse_failures", 0),
                unresolved=tuple(unresolved),
            ),
            omitted_options=max(0, alternatives["total_alternatives"] - len(rows)),
        )
        encode_agent_reply(reply)
        return reply

    def verify(self, search_ref: str, candidate_ref: str) -> AgentReply:
        try:
            report, _, _ = self.api._load(search_ref)
        except ValueError:
            return AgentFailureReply(
                code="expired_search",
                retry="never",
                message="Search evidence is missing or expired",
            )
        saved_request = (report.exploration_state or {}).get("agent_request")
        if saved_request is None:
            return AgentFailureReply(
                code="invalid_input",
                retry="fix_input",
                message="Search was not created by FlightAgent",
            )
        request = AgentTripRequest.model_validate(saved_request)
        candidate_refs = {
            _result_id(RankedFlight(request_id=outcome.request_id, option=option))
            for outcome in report.outcomes
            for option in outcome.options
        }
        if candidate_ref not in candidate_refs:
            return AgentFailureReply(
                code="invalid_input",
                retry="fix_input",
                message="Candidate does not belong to this search",
            )
        response = self.api.verify(
            search_ref, [candidate_ref], require_bag=request.baggage.require_overhead_cabin_bag
        )
        match = response.get("verification", [{}])[0]
        if match.get("status") == "blocked":
            return AgentVerificationBlocked(candidate_ref=candidate_ref)
        if match.get("status") == "pending":
            return AgentVerificationPending(candidate_ref=candidate_ref)
        matching = match.get("matching_result_ids", [])
        if not matching:
            return AgentVerificationNotConfirmed(
                candidate_ref=candidate_ref, reason_codes=(match.get("status", "not_matched"),)
            )
        detail = self.api.inspect(response["run_id"], [matching[0]])["results"][0]
        option = FlightOption.model_validate(detail["option"])
        if not whole_itinerary_confirmed(match, option):
            return AgentVerificationNotConfirmed(
                candidate_ref=candidate_ref, reason_codes=("whole_itinerary_unconfirmed",)
            )
        row = self._row(detail, request, (), verified=True)
        reply = AgentVerificationConfirmed(
            candidate_ref=candidate_ref,
            option=row.model_copy(update={"eligibility": "confirmed"}),
            baggage_confirmed=row.baggage == "included",
        )
        encode_agent_reply(reply)
        return reply

    @staticmethod
    def _row(
        item: dict[str, Any],
        request: AgentTripRequest,
        strengths: tuple[str, ...],
        *,
        verified: bool = False,
    ) -> DecisionRow:
        slim = _slim(
            RankedFlight(
                request_id=item["request_id"], option=FlightOption.model_validate(item["option"])
            ),
            SearchSpec.model_validate(item["search_spec"]),
        )
        reasons = []
        if slim["price_provenance"] != "provider_final_total":
            reasons.append("price_unverified")
        if slim["result_scope"] != "complete_itinerary":
            reasons.append("whole_itinerary_unverified")
        if request.baggage.require_overhead_cabin_bag:
            reasons.append("baggage_to_verify")
        return DecisionRow(
            candidate_ref=item["result_id"],
            route=tuple(slim["route"][:8]),
            price=slim["price"],
            currency=slim["currency"],
            price_evidence="provider_final_total"
            if slim["price_provenance"] == "provider_final_total"
            else "observed",
            ticket_scope=slim["ticket_scope"],
            journeys=tuple(
                DecisionJourney(
                    origin=j["origin"],
                    destination=j["destination"],
                    departure_at=j["departure_at"],
                    arrival_at=j["arrival_at"],
                )
                for j in slim["journeys"][:4]
            ),
            duration_minutes=slim["duration_minutes"],
            stops=slim["total_stops"],
            destination_stay_minutes=slim["destination_stay_minutes"],
            overnight_journeys=tuple(slim["overnight_journey_indexes"]),
            baggage=(
                slim["baggage_status"]
                if verified
                else "to_verify"
                if request.baggage.require_overhead_cabin_bag
                else slim["baggage_status"]
            ),
            strengths=strengths,
            eligibility="verify_before_recommending",
            reason_codes=tuple(reasons),
        )


def _contains_forbidden(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_forbidden(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item, forbidden) for item in value)
    return False
