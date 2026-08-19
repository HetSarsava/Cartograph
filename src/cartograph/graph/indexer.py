"""Builds a `SymbolGraph` for a Python repo using tree-sitter.

This is intentionally simple: each `.py` file is parsed independently (no
incremental caching, no cross-run persistence), symbols are collected first,
and call edges are resolved in a second pass against a global name index.
Resolution is best-effort — see `_resolve_call_target` for the exact rules —
and unresolved edges are kept (with `target_id=None`) rather than dropped, so
downstream consumers can see what *couldn't* be figured out.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from cartograph.graph.events import IndexEvent, IndexEventKind
from cartograph.graph.models import Edge, EdgeKind, Symbol, SymbolGraph, SymbolKind

_PY_LANGUAGE = Language(tspython.language())

# Directories we never descend into when walking a repo for .py files.
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".cartograph",
}


@dataclass
class _PendingCall:
    """A call site discovered during the first pass, awaiting target resolution."""

    source_id: str
    target_expr: str
    """The call target as written, e.g. 'add' or 'self.compute'."""
    line: int
    self_class_id: str | None
    """Id of the class Symbol that 'self' refers to at the call site, if any."""


@dataclass
class _IndexAccumulator:
    symbols: dict[str, Symbol] = field(default_factory=dict)
    pending_calls: list[_PendingCall] = field(default_factory=list)
    files_indexed: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)


def index_repo(
    repo_path: str | Path,
    on_event: Callable[[IndexEvent], None] | None = None,
) -> SymbolGraph:
    """Walk `repo_path`, parse every `.py` file, and build a `SymbolGraph`.

    For each file: extract top-level and nested function/class definitions as
    `Symbol`s, and every call expression inside a function/method body as a
    candidate `Edge`. Call targets are resolved by name against the full set
    of symbols found across the repo (see `_resolve_call_target`); this is a
    best-effort static resolution, not a real Python name-resolution engine,
    so it can both miss valid edges (e.g. calls through aliased imports) and
    over-resolve (e.g. two unrelated functions sharing a name are ambiguous
    and left unresolved).

    Args:
        repo_path: Path to the root of the repo (or subtree) to index.
        on_event: Optional callback invoked with an `IndexEvent` at each
            notable point during indexing (file/symbol/edge discovery), for
            progress visualization or debugging. Purely an observability
            hook — indexing behavior and the returned `SymbolGraph` are
            identical whether or not this is passed.

    Returns:
        A `SymbolGraph` with all symbols and resolved/unresolved edges.
    """
    root = Path(repo_path).resolve()
    acc = _IndexAccumulator()
    seq = itertools.count()

    all_files = sorted(_iter_python_files(root))
    files_total = len(all_files)

    for files_done, file_path in enumerate(all_files):
        rel_path = file_path.relative_to(root).as_posix()
        if on_event:
            on_event(
                IndexEvent(
                    kind=IndexEventKind.FILE_STARTED,
                    seq=next(seq),
                    timestamp=time.time(),
                    file_path=rel_path,
                    files_total=files_total,
                    files_done=files_done,
                )
            )

        source = file_path.read_bytes()
        parser = Parser(_PY_LANGUAGE)
        tree = parser.parse(source)
        if tree.root_node.has_error:
            # Best-effort: a syntax error shouldn't take down the whole index.
            acc.files_skipped.append(rel_path)
        else:
            _walk_module(tree.root_node, source, rel_path, acc, on_event, seq)
            acc.files_indexed.append(rel_path)

        if on_event:
            on_event(
                IndexEvent(
                    kind=IndexEventKind.FILE_FINISHED,
                    seq=next(seq),
                    timestamp=time.time(),
                    file_path=rel_path,
                )
            )

    if on_event:
        on_event(
            IndexEvent(
                kind=IndexEventKind.EDGE_RESOLUTION_STARTED,
                seq=next(seq),
                timestamp=time.time(),
                # files_total always means "files found", never "files
                # successfully parsed" — a consumer rendering progress must
                # not see the denominator shrink because a file was skipped.
                files_total=files_total,
                files_done=len(acc.files_indexed),
            )
        )

    by_bare_name = _build_bare_name_index(acc.symbols)
    edges: list[Edge] = []
    for call in acc.pending_calls:
        edge = _resolve_call_target(call, acc.symbols, by_bare_name)
        edges.append(edge)
        if on_event:
            on_event(
                IndexEvent(
                    kind=IndexEventKind.EDGE_DISCOVERED,
                    seq=next(seq),
                    timestamp=time.time(),
                    edge=edge,
                )
            )

    files_done_total = len(acc.files_indexed) + len(acc.files_skipped)
    if on_event:
        on_event(
            IndexEvent(
                kind=IndexEventKind.INDEX_FINISHED,
                seq=next(seq),
                timestamp=time.time(),
                files_total=files_total,
                files_done=files_done_total,
            )
        )

    return SymbolGraph(
        root_path=str(root),
        symbols=acc.symbols,
        edges=edges,
        files_indexed=acc.files_indexed,
        files_skipped=acc.files_skipped,
    )


def _iter_python_files(root: Path):
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def _walk_module(
    root_node: Node,
    source: bytes,
    rel_path: str,
    acc: _IndexAccumulator,
    on_event: Callable[[IndexEvent], None] | None,
    seq: Iterator[int],
) -> None:
    """Recursively visit a module's AST, recording symbols and pending calls.

    Two different "which class" facts are threaded separately, because they
    have different lifetimes:

    - `class_scope_id`: the class whose body we are *directly* inside, with
      no intervening function. Only used to decide whether a newly found
      `function_definition` is a METHOD (and what its `parent_id` is). Reset
      to None the moment we descend into any function body, since defs
      nested inside a method are not themselves methods of that class.
    - `self_class_id`: the class that `self` refers to in the current
      executable scope. Set when descending into a *method's* body (and
      only then), used purely to resolve `self.foo(...)` call sites.
    """

    def _emit_symbol_discovered(symbol: Symbol) -> None:
        if on_event:
            on_event(
                IndexEvent(
                    kind=IndexEventKind.SYMBOL_DISCOVERED,
                    seq=next(seq),
                    timestamp=time.time(),
                    symbol=symbol,
                    file_path=rel_path,
                )
            )

    def visit(
        node: Node,
        name_stack: list[str],
        class_scope_id: str | None,
        self_class_id: str | None,
        current_symbol_id: str | None,
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                _handle_function_def(child, name_stack, class_scope_id)
            elif child.type == "class_definition":
                _handle_class_def(child, name_stack, self_class_id, current_symbol_id)
            elif child.type == "call" and current_symbol_id is not None:
                _handle_call(child, current_symbol_id, self_class_id)
            else:
                visit(child, name_stack, class_scope_id, self_class_id, current_symbol_id)

    def _handle_function_def(
        node: Node,
        name_stack: list[str],
        class_scope_id: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = _text(name_node, source)
        qualified_name = ".".join([*name_stack, func_name])
        symbol_id = f"{rel_path}::{qualified_name}"
        is_method = class_scope_id is not None
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        body = node.child_by_field_name("body")
        symbol = Symbol(
            id=symbol_id,
            name=qualified_name if is_method else func_name,
            qualified_name=qualified_name,
            kind=kind,
            file_path=rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            docstring=_extract_docstring(body, source) if body is not None else None,
            parent_id=class_scope_id if is_method else None,
        )
        acc.symbols[symbol_id] = symbol
        _emit_symbol_discovered(symbol)
        if body is not None:
            visit(
                body,
                [*name_stack, func_name],
                None,  # nested defs are not methods, regardless of this function's own kind
                class_scope_id if is_method else None,  # 'self' in this body means this class
                symbol_id,
            )

    def _handle_class_def(
        node: Node,
        name_stack: list[str],
        self_class_id: str | None,
        current_symbol_id: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = _text(name_node, source)
        qualified_name = ".".join([*name_stack, class_name])
        symbol_id = f"{rel_path}::{qualified_name}"
        body = node.child_by_field_name("body")
        symbol = Symbol(
            id=symbol_id,
            name=class_name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            file_path=rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            docstring=_extract_docstring(body, source) if body is not None else None,
            parent_id=None,
        )
        acc.symbols[symbol_id] = symbol
        _emit_symbol_discovered(symbol)
        if body is not None:
            visit(body, [*name_stack, class_name], symbol_id, self_class_id, current_symbol_id)

    def _handle_call(
        node: Node,
        current_symbol_id: str,
        self_class_id: str | None,
    ) -> None:
        function_node = node.child_by_field_name("function")
        if function_node is not None:
            target_expr = _text(function_node, source)
            acc.pending_calls.append(
                _PendingCall(
                    source_id=current_symbol_id,
                    target_expr=target_expr,
                    line=node.start_point[0] + 1,
                    self_class_id=self_class_id,
                )
            )
        # Still recurse into the call so calls nested in its arguments
        # (e.g. `f(g(x))`) are also captured.
        for child in node.children:
            visit(child, [], None, self_class_id, current_symbol_id)

    visit(root_node, [], None, None, None)


def _extract_docstring(body_node: Node, source: bytes) -> str | None:
    """Return the docstring of a function/class body, if the first statement is one."""
    for child in body_node.children:
        if child.type == "expression_statement":
            string_node = child.children[0] if child.children else None
            if string_node is not None and string_node.type == "string":
                return _string_literal_value(string_node, source)
            return None
        if child.type in ("comment",):
            continue
        return None
    return None


def _string_literal_value(string_node: Node, source: bytes) -> str:
    raw = _text(string_node, source)
    stripped = raw.strip()
    for prefix in ("r", "R", "b", "B", "u", "U", "f", "F", "rb", "Rb", "rB", "RB"):
        if stripped.lower().startswith(prefix.lower()) and len(stripped) > len(prefix):
            stripped = stripped[len(prefix) :]
            break
    for quote in ('"""', "'''", '"', "'"):
        if stripped.startswith(quote) and stripped.endswith(quote) and len(stripped) >= 2 * len(
            quote
        ):
            return stripped[len(quote) : -len(quote)].strip()
    return stripped


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf8")


def _build_bare_name_index(symbols: dict[str, Symbol]) -> dict[str, list[str]]:
    """Map a bare (unqualified) name to every symbol id that could match it.

    Functions are indexed by their plain name. Methods are indexed by the
    last component of their qualified name (e.g. 'Calculator.compute' -> also
    reachable via 'compute'), since call sites rarely spell out the class.
    """
    index: dict[str, list[str]] = {}
    for symbol_id, symbol in symbols.items():
        bare = symbol.qualified_name.split(".")[-1]
        index.setdefault(bare, []).append(symbol_id)
    return index


def _resolve_call_target(
    call: _PendingCall,
    symbols: dict[str, Symbol],
    by_bare_name: dict[str, list[str]],
) -> Edge:
    """Resolve a pending call's target expression to a symbol id, if possible.

    Resolution order:
      1. `self.<name>` inside a class: look for a method named `<name>` whose
         `parent_id` is the enclosing class. Unambiguous by construction.
      2. Otherwise, treat the last dotted component as a bare name and look
         it up in `by_bare_name`. Resolve only if exactly one symbol matches;
         ambiguous or unknown names are left unresolved.
      3. A *dotted* call on an unknown receiver (`x.foo()`) may only resolve
         to a top-level symbol, never to a method. Nothing here infers the
         type of `x`, so binding `d.get(...)` to some class's `get` method is
         a guess -- and it is wrong far more often than right: on this repo it
         attached 71 dict lookups to a single unrelated method, which then
         dominated `blast_radius`. A missing edge is a documented limitation;
         a fabricated one silently corrupts the question the graph exists to
         answer, so this refuses rather than guesses.

         (A denylist of builtin method names -- get/items/append/... -- would
         recover a handful of genuine `obj.method()` edges this drops. It was
         considered and rejected: an open-ended list of special cases to
         maintain, in exchange for edges that are still guesses.)
    """
    bare_name = call.target_expr.split(".")[-1]
    is_dotted = "." in call.target_expr

    if call.target_expr.startswith("self.") and call.self_class_id is not None:
        for symbol_id, symbol in symbols.items():
            if symbol.parent_id == call.self_class_id and symbol.qualified_name.endswith(
                f".{bare_name}"
            ):
                return Edge(
                    source_id=call.source_id,
                    target_id=symbol_id,
                    target_name=call.target_expr,
                    kind=EdgeKind.CALLS,
                    line=call.line,
                )

    candidates = by_bare_name.get(bare_name, [])
    target_id = candidates[0] if len(candidates) == 1 else None
    if target_id is not None and is_dotted and symbols[target_id].parent_id is not None:
        target_id = None
    return Edge(
        source_id=call.source_id,
        target_id=target_id,
        target_name=call.target_expr,
        kind=EdgeKind.CALLS,
        line=call.line,
    )
