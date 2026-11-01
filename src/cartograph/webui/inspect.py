"""Symbol search, impact analysis, and repo insights for the web UI.

The graph queries in `graph.queries` — `find_callers`, `blast_radius` — are
the whole point of building a symbol graph, but they were only reachable
over MCP. This module puts them behind the UI: search for a symbol, then see
where it's defined, what it calls, who calls it, and what would break if you
changed it.

A built graph is cached in-process so the browser can ask follow-up
questions without re-indexing. Single user, few entries, no expiry — this is
local dev tooling.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from cartograph.graph.models import Symbol, SymbolGraph
from cartograph.graph.queries import blast_radius, find_callers

logger = logging.getLogger(__name__)

MAX_CACHED_GRAPHS = 3
"""Enough to flip between a couple of repos without unbounded growth."""

NEIGHBOURHOOD_LIMIT = 60
"""Node cap for the neighbourhood graph. Past this it's an unreadable hairball."""

_graphs: dict[str, SymbolGraph] = {}
_order: list[str] = []


def cache_graph(key: str, graph: SymbolGraph) -> None:
    """Remember `graph` under `key` so the UI can query it without re-indexing."""
    if key in _graphs:
        _order.remove(key)
    _graphs[key] = graph
    _order.append(key)
    while len(_order) > MAX_CACHED_GRAPHS:
        del _graphs[_order.pop(0)]


def get_graph(key: str | None = None) -> SymbolGraph | None:
    """Return the graph for `key`, or the most recently built one."""
    if key:
        return _graphs.get(key)
    return _graphs[_order[-1]] if _order else None


def clear_cache() -> None:
    """Drop every cached graph (used by tests)."""
    _graphs.clear()
    _order.clear()


# --- search --------------------------------------------------------------


def _score(symbol: Symbol, query: str) -> int | None:
    """Rank `symbol` against a lowercased `query`, or None if it doesn't match.

    Higher is better. Ordering intent: an exact name beats a prefix, which
    beats a substring, which beats a scattered subsequence — so typing "run"
    surfaces `run` before `run_reporter` before `_force_rmtree`.
    """
    name = symbol.name.lower()
    qualified = symbol.qualified_name.lower()
    path = symbol.file_path.lower()

    if name == query:
        return 100
    if name.startswith(query):
        return 90 - min(len(name) - len(query), 20)
    if query in name:
        return 65
    if query in qualified:
        return 55
    if query in path:
        return 35
    # Subsequence: "fcl" matches "find_callers".
    it = iter(name)
    if all(char in it for char in query):
        return 20
    return None


def search_symbols(graph: SymbolGraph, query: str, limit: int = 40) -> list[dict[str, Any]]:
    """Find symbols matching `query`, best first.

    An empty query returns the most-referenced symbols instead of nothing —
    an empty search box should still show you where to start.
    """
    query = query.strip().lower()
    if not query:
        return _most_referenced(graph, limit)

    scored: list[tuple[int, Symbol]] = []
    for symbol in graph.symbols.values():
        score = _score(symbol, query)
        if score is not None:
            scored.append((score, symbol))

    scored.sort(key=lambda pair: (-pair[0], pair[1].qualified_name))
    return [_brief(symbol, graph) for _, symbol in scored[:limit]]


def _incoming_counts(graph: SymbolGraph) -> Counter[str]:
    return Counter(edge.target_id for edge in graph.edges if edge.target_id is not None)


def _most_referenced(graph: SymbolGraph, limit: int) -> list[dict[str, Any]]:
    counts = _incoming_counts(graph)
    ranked = sorted(
        graph.symbols.values(),
        key=lambda s: (-counts.get(s.id, 0), s.qualified_name),
    )
    return [_brief(symbol, graph) for symbol in ranked[:limit]]


def _brief(symbol: Symbol, graph: SymbolGraph) -> dict[str, Any]:
    """The compact shape the results list renders."""
    return {
        "id": symbol.id,
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind.value,
        "file_path": symbol.file_path,
        "line_start": symbol.line_start,
    }


# --- detail --------------------------------------------------------------


def _bare_name_index(graph: SymbolGraph) -> dict[str, list[Symbol]]:
    index: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in graph.symbols.values():
        index[symbol.qualified_name.split(".")[-1]].append(symbol)
    return index


def _why_unresolved(target_expr: str, by_bare: dict[str, list[Symbol]]) -> dict[str, Any]:
    """Explain why a call site has no target.

    "Unresolved" covers two different situations and conflating them is
    unhelpful: a name defined twice in the repo is an *ambiguity* the indexer
    refused to guess at, while a name it has never seen is simply external
    (a stdlib or third-party call). The UI says which, and lists the
    candidates when there are any.
    """
    bare = target_expr.split(".")[-1]
    candidates = by_bare.get(bare, [])
    dotted = "." in target_expr

    if len(candidates) > 1:
        return {
            "reason": "ambiguous",
            "detail": f"{len(candidates)} symbols named {bare!r} in this repo",
            "candidates": [
                {"id": s.id, "qualified_name": s.qualified_name, "file_path": s.file_path}
                for s in candidates[:6]
            ],
        }
    if len(candidates) == 1 and dotted:
        only = candidates[0]
        return {
            "reason": "unknown receiver",
            "detail": (
                f"{bare!r} exists as {only.qualified_name}, but the type of "
                f"{target_expr.rsplit('.', 1)[0]!r} isn't known, so binding them "
                "would be a guess"
            ),
            "candidates": [
                {"id": only.id, "qualified_name": only.qualified_name, "file_path": only.file_path}
            ],
        }
    return {
        "reason": "external",
        "detail": "not defined in this repo (stdlib, a dependency, or a local variable)",
        "candidates": [],
    }


def symbol_detail(graph: SymbolGraph, symbol_id: str) -> dict[str, Any] | None:
    """Everything the detail panel shows for one symbol.

    Includes the blast radius — every symbol that transitively reaches this
    one — which is the question the whole graph exists to answer.
    """
    symbol = graph.symbols.get(symbol_id)
    if symbol is None:
        return None

    by_bare = _bare_name_index(graph)

    calls: list[dict[str, Any]] = []
    for edge in graph.edges:
        if edge.source_id != symbol_id:
            continue
        target = graph.symbols.get(edge.target_id) if edge.target_id else None
        entry: dict[str, Any] = {
            "name": target.qualified_name if target else edge.target_name,
            "id": target.id if target else None,
            "file_path": target.file_path if target else None,
            "line": edge.line,
            "resolved": target is not None,
        }
        if target is None:
            entry["why"] = _why_unresolved(edge.target_name, by_bare)
        calls.append(entry)

    callers = [_brief(s, graph) for s in find_callers(graph, symbol_id)]
    impact = [_brief(s, graph) for s in blast_radius(graph, symbol_id)]

    return {
        "id": symbol.id,
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind.value,
        "file_path": symbol.file_path,
        "line_start": symbol.line_start,
        "line_end": symbol.line_end,
        "docstring": symbol.docstring,
        "calls": calls,
        "callers": callers,
        "blast_radius": impact,
        "neighbourhood": neighbourhood(graph, symbol_id),
    }


def neighbourhood(graph: SymbolGraph, symbol_id: str) -> dict[str, Any]:
    """Nodes and edges immediately around `symbol_id`, for the mini graph.

    Direct callers and callees only. The full graph of a real repo is an
    unreadable hairball; one hop is the part a person can actually read.
    """
    centre = graph.symbols.get(symbol_id)
    if centre is None:
        return {"nodes": [], "edges": []}

    keep: dict[str, str] = {symbol_id: "centre"}
    for edge in graph.edges:
        if edge.source_id == symbol_id and edge.target_id:
            keep.setdefault(edge.target_id, "callee")
        elif edge.target_id == symbol_id:
            keep.setdefault(edge.source_id, "caller")
        if len(keep) >= NEIGHBOURHOOD_LIMIT:
            break

    nodes = [
        {
            "id": sid,
            "label": graph.symbols[sid].qualified_name.split(".")[-1],
            "full": graph.symbols[sid].qualified_name,
            "file_path": graph.symbols[sid].file_path,
            "role": role,
            "kind": graph.symbols[sid].kind.value,
        }
        for sid, role in keep.items()
        if sid in graph.symbols
    ]
    ids = {n["id"] for n in nodes}
    edges = [
        {"source": e.source_id, "target": e.target_id}
        for e in graph.edges
        if e.source_id in ids and e.target_id in ids
    ]
    # Collapse duplicate call sites: two calls to the same target draw one
    # line. A mutual pair (A calls B *and* B calls A) collapses to a single
    # link flagged `both`, so the renderer can put an arrowhead on each end
    # instead of stacking two identical lines on top of each other.
    directed = {(link["source"], link["target"]) for link in edges}
    seen: set[tuple[str, str]] = set()
    unique_edges: list[dict[str, Any]] = []
    for link in edges:
        source, target = link["source"], link["target"]
        if (source, target) in seen or (target, source) in seen:
            continue
        seen.add((source, target))
        unique_edges.append(
            {"source": source, "target": target, "both": (target, source) in directed}
        )

    return {"nodes": nodes, "edges": unique_edges}


# --- insights ------------------------------------------------------------


def compute_insights(graph: SymbolGraph) -> dict[str, Any]:
    """Repo-level observations worth surfacing without being asked.

    Everything here falls straight out of the graph, so it costs one pass and
    no model call. The point is to give someone opening an unfamiliar repo a
    place to start: what's central, what's dead, what's oversized.
    """
    incoming = _incoming_counts(graph)
    outgoing = Counter(edge.source_id for edge in graph.edges)

    functions = [s for s in graph.symbols.values() if s.kind.value in ("function", "method")]

    most_called = sorted(
        (s for s in graph.symbols.values() if incoming.get(s.id)),
        key=lambda s: (-incoming[s.id], s.qualified_name),
    )[:8]

    # Never referenced anywhere: entry points, dead code, or dynamic dispatch
    # the indexer can't see. Worth a look either way -- hence the hedge in
    # the UI copy rather than calling them "dead".
    never_called = sorted(
        (s for s in functions if not incoming.get(s.id) and not s.name.startswith("test_")),
        key=lambda s: s.qualified_name,
    )

    biggest = sorted(
        graph.symbols.values(), key=lambda s: (-(s.line_end - s.line_start), s.qualified_name)
    )[:8]

    per_file: dict[str, int] = defaultdict(int)
    for symbol in graph.symbols.values():
        per_file[symbol.file_path] += 1
    busiest_files = sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))[:8]

    resolved = sum(1 for e in graph.edges if e.target_id is not None)
    unresolved_names = Counter(
        e.target_name for e in graph.edges if e.target_id is None
    ).most_common(8)

    return {
        "totals": {
            "symbols": len(graph.symbols),
            "edges": len(graph.edges),
            "resolved": resolved,
            "unresolved": len(graph.edges) - resolved,
            "files": len(graph.files_indexed),
            "skipped": len(graph.files_skipped),
            "functions": len(functions),
            "classes": sum(1 for s in graph.symbols.values() if s.kind.value == "class"),
        },
        "most_called": [{**_brief(s, graph), "count": incoming[s.id]} for s in most_called],
        "most_calls_out": [
            {**_brief(s, graph), "count": outgoing[s.id]}
            for s in sorted(
                (s for s in graph.symbols.values() if outgoing.get(s.id)),
                key=lambda s: (-outgoing[s.id], s.qualified_name),
            )[:8]
        ],
        "never_called": [_brief(s, graph) for s in never_called[:8]],
        "never_called_total": len(never_called),
        "biggest": [{**_brief(s, graph), "lines": s.line_end - s.line_start + 1} for s in biggest],
        "busiest_files": [{"file_path": f, "count": n} for f, n in busiest_files],
        "top_unresolved": [{"name": n, "count": c} for n, c in unresolved_names],
    }
