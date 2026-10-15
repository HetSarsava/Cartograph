"""Repository tools over SDK-managed JSON-RPC 2.0 and stdio.

Bind one trusted repository at startup. Queries check source content and reuse
an unchanged graph in memory. The SDK owns
discovery, schemas, dispatch and errors.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from cartograph.graph.models import Symbol, SymbolGraph
from cartograph.graph.queries import blast_radius as query_impact
from cartograph.graph.queries import find_callers as query_callers
from cartograph.graph.queries import find_definition as query_definition
from cartograph.mcp_server.graph_cache import RepositoryGraph
from cartograph.review.models import ReviewReport
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


class SymbolLocation(BaseModel):
    id: str
    name: str
    file_path: str
    line_start: int
    line_end: int


class SymbolPage(BaseModel):
    symbols: list[SymbolLocation | Symbol]
    total: int
    next_offset: int | None
    unresolved_edges: int


class CallerLocation(SymbolLocation):
    direct: bool


class ImpactPage(BaseModel):
    target: SymbolLocation
    symbols: list[CallerLocation]
    total: int
    next_offset: int | None
    unresolved_edges: int


def _location(symbol: Symbol) -> SymbolLocation:
    return SymbolLocation.model_validate(symbol.model_dump())


def _page(
    graph: SymbolGraph, symbols: list[Symbol], limit: int, offset: int,
    detail: Literal["concise", "detailed"],
) -> SymbolPage:
    ordered = sorted(symbols, key=lambda symbol: symbol.id)
    end = offset + limit
    return SymbolPage(
        symbols=[s if detail == "detailed" else _location(s) for s in ordered[offset:end]],
        total=len(ordered),
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
            "Python code intelligence for one configured repository. For dependency questions, "
            "use blast_radius(name=...) directly: it indexes on demand and labels direct callers. "
            "No preliminary index_repo/find_definition/find_callers calls are needed for that flow. "
            "Use find_definition for locations only; index_repo is optional diagnostics. "
            "Results are concise and paginated. Reuse results until files change; "
            "ordinary search is preferable for documentation/configuration or simple text edits. "
            "Unresolved calls are unknown, not proof of no impact. "
            "Tests and mutations require startup with --allow-execution. "
            "Take a snapshot before applying a codemod."
        ),
    )
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
    repository = RepositoryGraph(root)

    def require_execution() -> None:
        if not allow_execution:
            raise ValueError("Restart with --allow-execution to permit tests and mutations")

    def graph_for(symbol_id: str | None = None) -> SymbolGraph:
        graph = repository.get()
        if symbol_id is not None and symbol_id not in graph.symbols:
            raise ValueError(f"Unknown symbol ID: {symbol_id}")
        return graph

    @server.tool(annotations=read)
    def read_review_report() -> ReviewReport:
        """Read the latest CLI-generated patch evidence; never executes tests.

        First run cartograph review --base REF --repo PATH in a terminal.
        source_matches_current flags stale evidence. Survivors are review prompts,
        not proven bugs; complete describes execution, not safety.
        """
        from cartograph.review.engine import source_digest

        path = root / ".cartograph" / "review" / "report.json"
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("Review report must stay inside the configured repository")
        if not path.is_file():
            raise ValueError("No report; first run cartograph review --base REF --repo PATH")
        if path.stat().st_size > 10_000_000:
            raise ValueError("Report exceeds 10 MB; inspect the local artifact directly")
        report = ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))
        if Path(report.repo).resolve() != root:
            raise ValueError("Report belongs to a different repository")
        report.source_matches_current = source_digest(root) == report.source_digest
        return report

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
    def find_definition(
        name: str, limit: Limit = 20, offset: Offset = 0,
        detail: Literal["concise", "detailed"] = "concise",
    ) -> SymbolPage:
        """Find exact definitions and source locations. Detailed mode includes docstrings."""
        graph = graph_for()
        return _page(graph, query_definition(graph, name), limit, offset, detail)

    @server.tool(annotations=read)
    def find_callers(
        symbol_id: str, limit: Limit = 20, offset: Offset = 0,
        detail: Literal["concise", "detailed"] = "concise",
    ) -> SymbolPage:
        """List direct callers known to static analysis (unresolved calls are excluded)."""
        graph = graph_for(symbol_id)
        return _page(graph, query_callers(graph, symbol_id), limit, offset, detail)

    @server.tool(annotations=read)
    def blast_radius(
        symbol_id: str | None = None, max_depth: Annotated[int, Field(ge=1, le=100)] = 10,
        limit: Limit = 20, offset: Offset = 0,
        name: str | None = None, file_path: str | None = None,
    ) -> ImpactPage:
        """Inspect impact in ONE call by name OR symbol_id; direct=true marks immediate callers.

        Optional file_path disambiguates a name. No preceding index/definition query needed.
        Partial static estimate, not proof of every dependency. Follow next_offset for more.
        """
        if (symbol_id is None) == (name is None):
            raise ValueError("Provide exactly one of name or symbol_id")
        graph = graph_for(symbol_id)
        if name is not None:
            matches = query_definition(graph, name)
            if file_path is not None:
                path = file_path.replace("\\", "/").removeprefix("./")
                matches = [s for s in matches if s.file_path == path]
            if len(matches) != 1:
                candidates = ", ".join(s.id for s in matches[:10]) or "none"
                raise ValueError(f"Expected one definition; provide file_path to disambiguate. Candidates: {candidates}")
            symbol_id = matches[0].id
        elif file_path is not None:
            raise ValueError("file_path is only used with name")
        assert symbol_id is not None
        direct = {s.id for s in query_callers(graph, symbol_id)}
        symbols = sorted(query_impact(graph, symbol_id, max_depth),
                         key=lambda s: (s.id not in direct, s.id))
        end = offset + limit
        return ImpactPage(
            target=_location(graph.symbols[symbol_id]),
            symbols=[CallerLocation(**_location(s).model_dump(), direct=s.id in direct)
                     for s in symbols[offset:end]],
            total=len(symbols), next_offset=end if end < len(symbols) else None,
            unresolved_edges=sum(e.target_id is None for e in graph.edges),
        )

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
