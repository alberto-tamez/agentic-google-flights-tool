"""Optional local MCP adapter. Handles carry state; no server session is required."""

from __future__ import annotations

import argparse
import inspect
from functools import wraps
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError

from agentic_flights.api import AgentAPI


class NextAction(BaseModel):
    operation: Literal[
        "schema",
        "plan",
        "start",
        "explore",
        "compare",
        "alternatives",
        "inspect",
        "verify",
        "issues",
        "playbook",
        "strategy_plan",
        "start_strategy_plan",
        "strategy_results",
        "discover_route_graph",
        "start_auto_strategy_plan",
    ]
    arguments: dict[str, Any]
    reason: str | None = None


class VerificationMatch(BaseModel):
    selected_result_id: str
    status: Literal[
        "matched_observed_itinerary",
        "pending",
        "blocked",
        "insufficient_detail",
        "not_matched",
    ]
    match_scope: Literal["outbound", "whole_itinerary"]
    whole_itinerary_matched: bool
    uncompared_journey_indexes: list[int]
    identity_basis: Literal[
        "provider_segments", "detailed_schedule", "journey_summary", "none"
    ]
    complete_quotes_found: int
    matching_quotes: int
    matching_result_ids: list[str]
    evidence: str


class ToolError(BaseModel):
    code: str
    message: str
    validation: list[dict[str, Any]] = []
    next_action: NextAction


class Progress(BaseModel):
    phase: str
    state: str
    attempted_queries: int
    remaining_queries: int
    pending_queries: int
    blocked_queries: int
    failed_queries: int
    coverage_complete: bool
    can_continue: bool
    can_retry: bool
    verification_satisfied: bool | None = None


class ToolResponse(BaseModel):
    """Successful transport can carry a repairable domain error with ok=false."""

    model_config = ConfigDict(extra="allow")
    ok: bool = True
    run_id: str | None = None
    progress: Progress | None = None
    error: ToolError | None = None
    next_actions: list[NextAction] | None = None
    verification: list[VerificationMatch] | None = None
    verification_satisfied: bool | None = None


def _handler(method):
    @wraps(method)
    def call(**arguments) -> ToolResponse:
        try:
            return ToolResponse.model_validate(method(**arguments))
        except (ValueError, OSError) as exc:
            validation = (
                exc.errors(include_input=False, include_url=False, include_context=False)
                if isinstance(exc, ValidationError)
                else []
            )
            topic = {
                "plan": "space",
                "start": "start",
                "compare": "filters",
                "alternatives": "filters",
            }.get(
                method.__name__, method.__name__ if method.__name__ != "schema" else "operations"
            )
            return ToolResponse(
                ok=False,
                error=ToolError(
                    code=getattr(exc, "code", "invalid_input"),
                    message="Input validation failed" if validation else str(exc),
                    validation=validation,
                    next_action={"operation": "schema", "arguments": {"topic": topic}},
                ),
            )

    hints = get_type_hints(method)
    signature = inspect.signature(method)
    call.__signature__ = signature.replace(
        parameters=[
            p.replace(annotation=hints.get(name, p.annotation))
            for name, p in signature.parameters.items()
        ],
        return_annotation=ToolResponse,
    )
    call.__annotations__ = {**hints, "return": ToolResponse}
    return call


def create_server(api: AgentAPI | None = None):
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install agentic-flights[mcp] to use MCP") from exc
    api = api or AgentAPI()
    server = MCPServer(
        "Agentic Flights",
        instructions=(
            "Use start for exact trips and plan for flexible date or airport searches. Continue "
            "saved runs with explore. Compare before inspecting or verifying selected results. "
            "Stop a verification run when verification_satisfied is true. Treat run IDs, result "
            "IDs, continuations, and provider diagnostics as private agent state; do not include "
            "them in user-facing recommendations unless the user requests diagnostics."
        ),
    )
    for name in (
        "schema",
        "plan",
        "start",
        "explore",
        "compare",
        "alternatives",
        "inspect",
        "verify",
        "issues",
        "playbook",
        "strategy_plan",
        "start_strategy_plan",
        "strategy_results",
        "discover_route_graph",
        "start_auto_strategy_plan",
    ):
        server.tool(name=name)(_handler(getattr(api, name)))
    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentic-flights-mcp")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server()
    if args.transport == "stdio":
        server.run()
    else:
        import uvicorn

        uvicorn.run(
            server.streamable_http_app(stateless_http=True, json_response=True),
            host="127.0.0.1",
            port=args.port,
        )


if __name__ == "__main__":
    main()
