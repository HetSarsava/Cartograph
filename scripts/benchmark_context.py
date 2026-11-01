"""Measure source bytes versus complete MCP impact-query request+response bytes.

Usage: uv run python scripts/benchmark_context.py --repo src/cartograph
This is a payload-size measurement, not a token, quality or latency benchmark.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _git_output(repo: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip())
    return stdout.decode().strip()


async def benchmark(repo: Path, names: list[str]) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cartograph.mcp_server.server", "--repo", str(repo.resolve())],
    )
    rows = []
    async with Client(stdio_client(params), read_timeout_seconds=60) as client:
        summary = (await client.call_tool("index_repo", {})).structured_content
        for name in names:
            found = await client.call_tool("find_definition", {"name": name})
            if found.is_error or found.structured_content["total"] != 1:
                raise ValueError(f"Choose a unique symbol name: {name}")
            symbol_id = found.structured_content["symbols"][0]["id"]
            offset = 0
            payload_bytes = 0
            affected = 0
            while True:
                arguments = {
                    "symbol_id": symbol_id,
                    "max_depth": 100,
                    "limit": 100,
                    "offset": offset,
                }
                result = await client.call_tool("blast_radius", arguments)
                if result.is_error:
                    raise RuntimeError(result.content)
                # Count both text and structured copies in the SDK result, not just the symbols.
                payload_bytes += len(result.model_dump_json(by_alias=True).encode("utf-8"))
                payload_bytes += len(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": "blast_radius", "arguments": arguments},
                        }
                    ).encode("utf-8")
                )
                affected = result.structured_content["total"]
                offset = result.structured_content["next_offset"]
                if offset is None:
                    break
            rows.append(
                {
                    "symbol": symbol_id,
                    "affected_symbols": affected,
                    "query_payload_bytes": payload_bytes,
                }
            )
    from cartograph.graph.indexer import index_repo

    graph = index_repo(repo, use_cache=False)
    source_bytes = sum(
        len((repo / name).read_text(encoding="utf-8").encode("utf-8"))
        for name in graph.files_indexed
    )
    for row in rows:
        row["source_to_query_byte_ratio"] = round(source_bytes / row["query_payload_bytes"], 2)
    return {
        "method": "newline-normalized UTF-8 Python source bytes / impact request plus full SDK result JSON bytes",
        "limitations": [
            "Bytes are not model tokens or cost savings.",
            "Excludes initial discovery, tool schemas, symbol lookup and request metadata.",
            "Includes all result pages; traversal capped at depth 100; unresolved calls excluded.",
            "Workload-specific comparison, not a guarantee for every query or repository.",
        ],
        "git_head": await _git_output(repo, "rev-parse", "HEAD"),
        "working_tree_modified": bool(await _git_output(repo, "status", "--porcelain")),
        "repo": repo.as_posix(),
        "source_bytes": source_bytes,
        "index": {key: value for key, value in summary.items() if key != "root_path"},
        "queries": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("src/cartograph"))
    parser.add_argument(
        "--names", nargs="+", default=["build_graph", "run_rewriter", "get_settings"]
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.repo, args.names))
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
