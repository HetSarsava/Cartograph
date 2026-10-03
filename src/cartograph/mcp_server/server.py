"""Eight repository tools over SDK-managed JSON-RPC 2.0 and stdio.

Bind one trusted repository at startup. Queries refresh from disk, so clients
never send a graph or receive stale results after edits. The SDK owns
discovery, schemas, dispatch and errors.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from cartograph.graph.indexer import index_repo as build_index
from cartograph.graph.models import Symbol, SymbolGraph
from cartograph.graph.queries import blast_radius as query_impact
from cartograph.graph.queries import find_callers as query_callers
from cartograph.graph.queries import find_definition as query_definition
from cartograph.tools import recipes as _recipes  # noqa: F401
from cartograph.tools.codemod import CodemodResult
from cartograph.tools.codemod import apply_codemod as codemod
from cartograph.tools.snapshot import RollbackResult, SnapshotResult
from cartograph.tools.snapshot import rollback as restore
from cartograph.tools.snapshot import snapshot as capture
from cartograph.tools.test_runner import TestRunResult
from cartograph.tools.test_runner import run_tests as pytest_run

Limit = Annotated[int, Field(ge=1, le=100)]
Offset = Annotated[int, Field(ge=0)]


class IndexSummary(BaseModel):
    root_path: str
    symbols: int
    edges: int
    unresolved_edges: int
    files_indexed: int
    files_skipped: list[str]


class SymbolPage(BaseModel):
    symbols: list[Symbol]
    total: int
    next_offset: int | None
    unresolved_edges: int


def _page(graph: SymbolGraph, symbols: list[Symbol], limit: int, offset: int) -> SymbolPage:
    ordered = sorted(symbols, key=lambda symbol: symbol.id)
    end = offset + limit
    return SymbolPage(
        symbols=ordered[offset:end], total=len(ordered),
        next_offset=end if end < len(ordered) else None,
        unresolved_edges=sum(edge.target_id is None for edge in graph.edges),
    )


def create_server(repo_path: str | Path, *, allow_execution: bool = False) -> MCPServer:
    root = Path(repo_path).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository must be a directory")
    server = MCPServer(
        "cartograph",
        instructions=(
            "Python code intelligence for one configured repository. Start with index_repo, "
            "then find_definition to obtain symbol IDs. Query results are paginated. "
            "Unresolved calls are unknown, not proof of no impact. "
            "Tests and mutations require startup with --allow-execution. "
            "Take a snapshot before applying a codemod."
        ),
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

    def require_execution() -> None:
        if not allow_execution:
            raise ValueError("Restart with --allow-execution to permit tests and mutations")

    def graph_for(symbol_id: str | None = None) -> SymbolGraph:
        graph = build_index(root, use_cache=False)
        if symbol_id is not None and symbol_id not in graph.symbols:
            raise ValueError(f"Unknown symbol ID: {symbol_id}")
        return graph

    @server.tool(annotations=read)
    def index_repo() -> IndexSummary:
        """Index the configured Python repository; return counts, not the entire graph."""
        graph = graph_for()
        return IndexSummary(
            root_path=str(root), symbols=len(graph.symbols), edges=len(graph.edges),
            unresolved_edges=sum(edge.target_id is None for edge in graph.edges),
            files_indexed=len(graph.files_indexed), files_skipped=graph.files_skipped,
        )

    @server.tool(annotations=read)
    def find_definition(name: str, limit: Limit = 50, offset: Offset = 0) -> SymbolPage:
        """Find exact name matches and their symbol IDs; follow next_offset for more."""
        graph = graph_for()
        return _page(graph, query_definition(graph, name), limit, offset)

    @server.tool(annotations=read)
    def find_callers(symbol_id: str, limit: Limit = 50, offset: Offset = 0) -> SymbolPage:
        """List direct callers known to static analysis (unresolved calls are excluded)."""
        graph = graph_for(symbol_id)
        return _page(graph, query_callers(graph, symbol_id), limit, offset)

    @server.tool(annotations=read)
    def blast_radius(
        symbol_id: str, max_depth: Annotated[int, Field(ge=1, le=100)] = 10,
        limit: Limit = 50, offset: Offset = 0,
    ) -> SymbolPage:
        """List known transitive callers up to max_depth; a partial static estimate."""
        graph = graph_for(symbol_id)
        return _page(graph, query_impact(graph, symbol_id, max_depth), limit, offset)

    @server.tool(annotations=write)
    def run_tests(
        test_path: str = ".", timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 120,
    ) -> TestRunResult:
        """Execute trusted repository pytest code; requires --allow-execution."""
        require_execution()
        path, *node = test_path.split("::", 1)
        target = (root / path).resolve(strict=True)
        if not path or not target.is_relative_to(root):
            raise ValueError("Test path must stay inside the configured repository")
        normalized = str(target) + ("::" + node[0] if node else "")
        return pytest_run(root, normalized, timeout_seconds)

    @server.tool(annotations=write)
    def snapshot(label: str | None = None) -> SnapshotResult:
        """Capture tracked and non-ignored files without moving HEAD; execution mode only."""
        require_execution()
        return capture(root, label)

    @server.tool(annotations=write)
    def rollback(snapshot_id: str) -> RollbackResult:
        """Restore a snapshot, removing newer non-ignored files and updating the Git index."""
        require_execution()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", snapshot_id):
            raise ValueError("snapshot_id must be a full Git object SHA from snapshot")
        return restore(root, snapshot_id)

    @server.tool(annotations=write)
    def apply_codemod(recipe_name: str, target_files: list[str] | None = None) -> CodemodResult:
        """Apply a deterministic recipe; ambiguous files remain unchanged. Snapshot first."""
        require_execution()
        return codemod(root, recipe_name, target_files)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Trusted repository (default: working directory)")
    parser.add_argument(
        "--allow-execution", action="store_true",
        help="Allow pytest execution, codemods, snapshots and rollback in the trusted repository",
    )
    args = parser.parse_args()
    create_server(args.repo, allow_execution=args.allow_execution).run(transport="stdio")


if __name__ == "__main__":
    main()
