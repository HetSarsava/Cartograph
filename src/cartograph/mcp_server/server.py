"""Registers every Cartograph tool and read-only graph query as an MCP tool.

Uses the official `mcp` Python SDK's high-level `MCPServer` server so any MCP
client — not just this repo's LangGraph pipeline — can call these tools.
This module is a thin adapter: all real logic lives in `graph/` and
`tools/`, and none of it depends on LangGraph state. The one still-stubbed
tool (`pack_context`) is registered too, so the server is fully wired up
even though calling it currently raises `NotImplementedError`.

The server is stateless across calls: query tools take a `SymbolGraph` as an
explicit argument rather than caching one server-side keyed by repo path.
That's a deliberate simplification for this pass (see TODO below) — a
client is expected to call `index_repo` once and pass the result into
subsequent `find_definition` / `find_callers` / `blast_radius` calls.

TODO: consider an optional server-side cache (repo_path -> SymbolGraph, with
mtime-based invalidation) so clients aren't forced to resend the whole graph
on every query.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from cartograph.graph.indexer import index_repo as _index_repo
from cartograph.graph.models import Symbol, SymbolGraph
from cartograph.graph.queries import blast_radius as _blast_radius
from cartograph.graph.queries import find_callers as _find_callers
from cartograph.graph.queries import find_definition as _find_definition
from cartograph.tools import recipes as _recipes  # noqa: F401 - registers built-in recipes
from cartograph.tools.codemod import CodemodResult
from cartograph.tools.codemod import apply_codemod as _apply_codemod
from cartograph.tools.context_packer import ContextPackRequest, PackedContext
from cartograph.tools.context_packer import pack_context as _pack_context
from cartograph.tools.snapshot import RollbackResult, SnapshotResult
from cartograph.tools.snapshot import rollback as _rollback
from cartograph.tools.snapshot import snapshot as _snapshot
from cartograph.tools.test_runner import TestRunResult
from cartograph.tools.test_runner import run_tests as _run_tests

mcp = MCPServer("cartograph")


# --- graph: indexing & queries (real) -----------------------------------


@mcp.tool()
def index_repo(repo_path: str) -> SymbolGraph:
    """Build and return the symbol graph for a Python repo at `repo_path`."""
    return _index_repo(repo_path)


@mcp.tool()
def find_definition(symbol_graph: SymbolGraph, name: str) -> list[Symbol]:
    """Find all symbols whose name matches `name` in an already-built symbol graph."""
    return _find_definition(symbol_graph, name)


@mcp.tool()
def find_callers(symbol_graph: SymbolGraph, symbol_id: str) -> list[Symbol]:
    """Find every symbol that directly calls `symbol_id` in an already-built symbol graph."""
    return _find_callers(symbol_graph, symbol_id)


@mcp.tool()
def blast_radius(
    symbol_graph: SymbolGraph, symbol_id: str, max_depth: int | None = None
) -> list[Symbol]:
    """Find every symbol transitively affected by a change to `symbol_id`."""
    return _blast_radius(symbol_graph, symbol_id, max_depth)


# --- tools: test oracle & snapshots (real) -------------------------------


@mcp.tool()
def run_tests(repo_path: str, test_path: str = ".", timeout_seconds: int = 120) -> TestRunResult:
    """Run pytest in `repo_path` and return structured pass/fail/error results."""
    return _run_tests(repo_path, test_path, timeout_seconds)


@mcp.tool()
def snapshot(repo_path: str, label: str | None = None) -> SnapshotResult:
    """Capture the working tree of `repo_path` as a recoverable snapshot."""
    return _snapshot(repo_path, label)


@mcp.tool()
def rollback(repo_path: str, snapshot_id: str) -> RollbackResult:
    """Restore `repo_path`'s working tree to a previously taken snapshot."""
    return _rollback(repo_path, snapshot_id)


# --- tools: codemods & context packing -----------------------------------


@mcp.tool()
def apply_codemod(
    repo_path: str, recipe_name: str, target_files: list[str] | None = None
) -> CodemodResult:
    """Apply a registered codemod recipe to `repo_path`, writing changes to disk.

    `recipe_name` must be one of the registered recipes (e.g.
    "unittest_to_pytest"). `target_files` are repo-relative paths; omit it to
    let the recipe scan the whole repo. Paths resolving outside `repo_path`
    are refused and reported as ambiguities.

    A file whose rewrite is ambiguous is left untouched on disk and reported
    in `ambiguities` — so an empty `ambiguities` means every candidate was
    handled confidently. Take a `snapshot` first if you want to be able to
    `rollback`.
    """
    return _apply_codemod(repo_path, recipe_name, target_files)


@mcp.tool()
def pack_context(symbol_graph: SymbolGraph, request: ContextPackRequest) -> PackedContext:
    """Pack token-budgeted context for a set of symbols for an LLM call.

    Not yet implemented — see `tools.context_packer.pack_context`. Calling
    this currently raises `NotImplementedError`.
    """
    return _pack_context(symbol_graph, request)


def main() -> None:
    """Run the MCP server over stdio (the standard transport for local MCP clients)."""
    mcp.run()


if __name__ == "__main__":
    main()
