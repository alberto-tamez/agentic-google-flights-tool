from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from conftest import make_api, make_spec

from agentic_flights import SearchSpace

DAY = date(2027, 1, 14)


def test_mcp_in_memory_schema_and_plan(tmp_path):
    Client = pytest.importorskip("mcp").Client
    from agentic_flights.mcp_server import create_server

    api = make_api(tmp_path)

    async def check():
        async with Client(create_server(api)) as client:
            result = await client.call_tool("schema", {"topic": "space"})
            assert "origins" in json.dumps(result.structured_content)
            space = SearchSpace(
                template=make_spec("t", DAY),
                origins=["MAD"],
                destinations=["LHR"],
                departure_start=DAY,
                departure_end=DAY,
            )
            result = await client.call_tool("plan", {"space": space.model_dump(mode="json")})
            assert "rgf_" in json.dumps(result.structured_content)
            exact = make_spec("ignored", DAY, search_mode="discover").model_dump(
                mode="json", exclude={"request_id", "search_mode"}
            )
            result = await client.call_tool("start", {"searches": exact})
            assert result.structured_content["progress"]["remaining_queries"] == 0

    asyncio.run(check())


@pytest.mark.integration
def test_stateless_http_across_client_connections(tmp_path):
    import socket

    uvicorn = pytest.importorskip("uvicorn")
    Client = pytest.importorskip("mcp").Client
    from agentic_flights.mcp_server import create_server

    async def check():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        address = "http://127.0.0.1:" + str(sock.getsockname()[1]) + "/mcp"
        app = create_server(make_api(tmp_path)).streamable_http_app(
            stateless_http=True, json_response=True
        )
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            space = SearchSpace(
                template=make_spec("x", DAY),
                origins=["MAD"],
                destinations=["LHR"],
                departure_start=DAY,
                departure_end=DAY,
            )
            async with Client(address) as first:
                response = await first.call_tool("plan", {"space": space.model_dump(mode="json")})
                run_id = response.structured_content["run_id"]
            async with Client(address) as second:
                response = await second.call_tool("explore", {"run_id": run_id, "work_chunk": 1})
                assert response.structured_content["search_space_exhausted"]
        finally:
            server.should_exit = True
            await task
            sock.close()

    asyncio.run(check())


def test_mcp_errors_can_be_repaired_and_schemas_are_discoverable(tmp_path):
    Client = pytest.importorskip("mcp").Client
    from agentic_flights.mcp_server import create_server

    async def check():
        async with Client(create_server(make_api(tmp_path))) as c:
            bad = (await c.call_tool("plan", {"space": {}})).structured_content
            assert bad["ok"] is False and bad["error"]["validation"]
            assert bad["error"]["next_action"]["arguments"]["topic"] == "space"
            missing = (
                await c.call_tool("inspect", {"run_id": "rgf_0000000000000000", "result_ids": []})
            ).structured_content
            assert missing["error"]["code"] == "run_expired"
            schemas = (await c.call_tool("schema", {"topic": "operations"})).structured_content
            assert "start" in schemas["operations"] and "issues" in schemas["operations"]
            assert "playbook" in schemas["operations"]
            assert "strategy_plan" in schemas["operations"]
            assert "discover_route_graph" in schemas["operations"]
            assert "start_auto_strategy_plan" in schemas["operations"]
            schema = (await c.call_tool("schema", {"topic": "verify"})).structured_content
            assert "result_ids" in schema["input_schema"]["properties"]
            assert schema["input_schema"]["properties"]["work_quotes"]["default"] == 3
            assert (
                schema["input_schema"]["properties"]["max_browser_transitions"]["default"]
                == 12
            )
            tools = await c.list_tools()
            tools = getattr(tools, "tools", tools)
            assert any(t.name == "start" for t in tools)
            assert any(t.name == "strategy_plan" for t in tools)
            assert any(t.name == "start_auto_strategy_plan" for t in tools)
            tool = next(t for t in tools if t.name == "inspect")
            assert tool.description and "progress" in str(tool.output_schema)

    asyncio.run(check())
