"""Real subprocess interoperability plus tool behavior; no API key required."""
import asyncio
import json
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
TOOLS = {"index_repo", "find_definition", "find_callers", "blast_radius",
         "run_tests", "snapshot", "rollback", "apply_codemod"}


@asynccontextmanager
async def connect(repo, *, execution=False, mode="auto"):
    args = ["-m", "cartograph.mcp_server.server", "--repo", str(repo)]
    if execution:
        args.append("--allow-execution")
    params = StdioServerParameters(command=sys.executable, args=args)
    async with Client(stdio_client(params), mode=mode, read_timeout_seconds=20) as client:
        yield client


async def call(client, name, arguments=None):
    result = await client.call_tool(name, arguments or {})
    assert not result.is_error, result
    return result.structured_content


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stdio_discovery_and_queries(mode):
    async with connect(FIXTURE, mode=mode) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == TOOLS
        assert all(tool.input_schema and tool.output_schema for tool in listing.tools)
        summary = await call(client, "index_repo")
        assert isinstance(summary["symbols"], int) and summary["symbols"] > 0
        page = await call(client, "find_definition", {"name": "add"})
        symbol_id = page["symbols"][0]["id"]
        callers = await call(client, "find_callers", {"symbol_id": symbol_id})
        assert any(symbol["name"] == "double" for symbol in callers["symbols"])
        impact = await call(client, "blast_radius", {"symbol_id": symbol_id})
        assert impact["total"] >= callers["total"]
        result = await client.call_tool("snapshot", {})
        assert result.is_error
        assert "--allow-execution" in result.content[0].text


@pytest.mark.asyncio
async def test_pagination_freshness_and_invalid_arguments(tmp_path):
    for name in ["a", "b", "c"]:
        (tmp_path / f"{name}.py").write_text("def same():\n    pass\n")
    async with connect(tmp_path) as client:
        first = await call(client, "find_definition", {"name": "same", "limit": 2})
        second = await call(client, "find_definition",
                            {"name": "same", "limit": 2, "offset": first["next_offset"]})
        assert first["total"] == 3
        assert len(first["symbols"]) == 2 and len(second["symbols"]) == 1
        assert second["next_offset"] is None
        (tmp_path / "c.py").unlink()
        assert (await call(client, "find_definition", {"name": "same"}))["total"] == 2
        for tool, args in [
            ("find_definition", {"name": "same", "limit": 0}),
            ("find_definition", {"name": "same", "offset": -1}),
            ("find_callers", {"symbol_id": "missing"}),
            ("blast_radius", {"symbol_id": "a.py::same", "max_depth": 0}),
        ]:
            assert (await client.call_tool(tool, args)).is_error
    assert not (tmp_path / ".cartograph").exists()  # read-only queries do not write caches


@pytest.mark.asyncio
async def test_all_mutating_tools_through_stdio(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    target = repo / "tests" / "test_legacy_unittest.py"
    before = target.read_bytes()
    async with connect(repo, execution=True) as client:
        saved = await call(client, "snapshot", {"label": "protocol test"})
        edit = await call(client, "apply_codemod", {
            "recipe_name": "unittest_to_pytest",
            "target_files": ["tests/test_legacy_unittest.py"],
        })
        assert edit["applied"] and target.read_bytes() != before
        tests = await call(client, "run_tests")
        assert tests["passed"] > 0 and tests["return_code"] == 0
        await call(client, "rollback", {"snapshot_id": saved["snapshot_id"]})
        assert target.read_bytes() == before
        outside = tmp_path / "outside.py"
        outside.write_text("def test_outside():\n    assert False\n")
        result = await client.call_tool("run_tests", {"test_path": str(outside)})
        assert result.is_error
        assert (await client.call_tool("rollback", {"snapshot_id": "--help"})).is_error


@pytest.mark.asyncio
async def test_jsonrpc_20_wire_messages():
    """Bypass the SDK client: prove stdout really carries JSON-RPC 2.0."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "cartograph.mcp_server.server", "--repo", str(FIXTURE),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    async def request(number, method, params):
        message = {"jsonrpc": "2.0", "id": number, "method": method, "params": params}
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            assert line, "Server exited before responding"
            response = json.loads(line)
            assert response["jsonrpc"] == "2.0"
            if response.get("id") == number:
                return response
    try:
        hello = await request(1, "initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "raw-wire-test", "version": "1.0"},
        })
        assert "result" in hello
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await proc.stdin.drain()
        listing = await request(2, "tools/list", {})
        assert {t["name"] for t in listing["result"]["tools"]} == TOOLS
        result = await request(3, "tools/call", {"name": "index_repo", "arguments": {}})
        assert result["result"]["structuredContent"]["symbols"] > 0
        error = await request(4, "nonexistent/method", {})
        assert error["error"]["code"] == -32601
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()

