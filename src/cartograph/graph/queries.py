"""Read-only queries over a `SymbolGraph`.

These functions never mutate the graph; they exist so agents/tools can ask
questions like "who defines X", "who calls X", and "what breaks if X
changes" without needing to know the graph's internal representation.
"""

from __future__ import annotations

from collections import deque

from cartograph.graph.models import Symbol, SymbolGraph


def find_definition(graph: SymbolGraph, name: str) -> list[Symbol]:
    """Find all symbols whose name or qualified name matches `name`.

    Matching is exact (case-sensitive) against `Symbol.name`,
    `Symbol.qualified_name`, and the bare (last-component) form of the
    qualified name, so both `find_definition(graph, "compute")` and
    `find_definition(graph, "Calculator.compute")` find the same method.
    Multiple results mean the name is ambiguous across the repo (e.g. two
    classes with a same-named method) — callers should disambiguate using
    `Symbol.file_path` / `Symbol.line_start`.

    Args:
        graph: The symbol graph to search.
        name: The symbol name to look up, qualified or unqualified.

    Returns:
        All matching `Symbol`s, in no particular order. Empty if none found.
    """
    matches = []
    for symbol in graph.symbols.values():
        bare = symbol.qualified_name.split(".")[-1]
        if name in (symbol.name, symbol.qualified_name, bare):
            matches.append(symbol)
    return matches


def find_callers(graph: SymbolGraph, symbol_id: str) -> list[Symbol]:
    """Find every symbol that directly calls the symbol identified by `symbol_id`.

    Only resolved edges (`Edge.target_id == symbol_id`) count; call sites the
    indexer couldn't resolve to this symbol are invisible to this query even
    if they were "really" calling it (see `graph.indexer` for why resolution
    can miss things).

    Args:
        graph: The symbol graph to search.
        symbol_id: The `Symbol.id` of the callee.

    Returns:
        Distinct caller `Symbol`s, in no particular order. Empty if the
        symbol has no known callers (or doesn't exist in the graph).
    """
    caller_ids = {edge.source_id for edge in graph.edges if edge.target_id == symbol_id}
    return [graph.symbols[cid] for cid in caller_ids if cid in graph.symbols]


def blast_radius(graph: SymbolGraph, symbol_id: str, max_depth: int | None = None) -> list[Symbol]:
    """Find every symbol transitively affected by a change to `symbol_id`.

    This is a breadth-first search over the *reverse* call graph starting at
    `symbol_id`: if A calls B calls C, then `blast_radius(graph, C.id)`
    includes B (direct caller) and A (transitive caller). The starting
    symbol itself is not included in the result.

    Args:
        graph: The symbol graph to search.
        symbol_id: The `Symbol.id` of the symbol being changed.
        max_depth: Optional cap on traversal depth. `None` (default) means
            unbounded — traverse until no new callers are found.

    Returns:
        Distinct affected `Symbol`s, in BFS discovery order (closest callers
        first). Empty if nothing calls into `symbol_id`.
    """
    reverse_adjacency: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.target_id is None:
            continue
        reverse_adjacency.setdefault(edge.target_id, set()).add(edge.source_id)

    visited = {symbol_id}
    result: list[Symbol] = []
    queue: deque[tuple[str, int]] = deque((caller, 1) for caller in reverse_adjacency.get(symbol_id, set()))
    queue_seen = {caller for caller, _ in queue}
    visited.update(queue_seen)

    while queue:
        current_id, depth = queue.popleft()
        symbol = graph.symbols.get(current_id)
        if symbol is not None:
            result.append(symbol)
        if max_depth is not None and depth >= max_depth:
            continue
        for caller_id in reverse_adjacency.get(current_id, set()):
            if caller_id not in visited:
                visited.add(caller_id)
                queue.append((caller_id, depth + 1))

    return result
