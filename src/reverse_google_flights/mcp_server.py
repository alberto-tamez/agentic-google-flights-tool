"""Optional local MCP adapter. Handles carry state; no server session is required."""

from __future__ import annotations

import argparse

from reverse_google_flights.api import AgentAPI


def create_server(api: AgentAPI | None = None):
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("Install agentic-google-flights-tool[mcp] to use MCP") from exc
    api = api or AgentAPI()
    server = MCPServer(
        "Agentic Flights",
        instructions=(
            "Call schema for focused input documentation. Use plan/explore in code, then compare "
            "and inspect saved results. Continue work chunks until the goal is met; a chunk is not "
            "a search cutoff. Verification reports itinerary matches explicitly."
        ),
    )
    for name in ("schema", "plan", "explore", "compare", "alternatives", "inspect", "verify"):
        server.tool(name=name)(getattr(api, name))
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
