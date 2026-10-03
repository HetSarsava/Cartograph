"""Run a real MCP client without an LLM: uv run python examples/mcp_client.py PATH."""
import argparse
import asyncio
import json
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(repo: str, name: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cartograph.mcp_server.server", "--repo", repo],
    )
    async with Client(stdio_client(params), read_timeout_seconds=30) as client:
        tools = await client.list_tools()
        print("Discovered:", ", ".join(tool.name for tool in tools.tools))
        for tool, arguments in [
            ("index_repo", {}),
            ("find_definition", {"name": name}),
        ]:
            result = await client.call_tool(tool, arguments)
            if result.is_error:
                raise RuntimeError(result.content)
            print(tool, json.dumps(result.structured_content, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("--name", default="add")
    args = parser.parse_args()
    asyncio.run(main(args.repo, args.name))

