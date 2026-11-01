"""Pydantic models for the symbol graph.

The symbol graph is a read-only, whole-repo view of "what is defined where"
(`Symbol`) and "what calls what" (`Edge`). It is built once per run by
`graph.indexer.index_repo` and then queried (never mutated) by
`graph.queries`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SymbolKind(str, Enum):
    """The kind of definition a `Symbol` represents."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"


class EdgeKind(str, Enum):
    """The kind of relationship an `Edge` represents between two symbols."""

    CALLS = "calls"


class Symbol(BaseModel):
    """A single definition (function, method, class, or module) in the repo.

    `id` is a stable, globally unique identifier of the form
    ``"{relative_file_path}::{qualified_name}"`` so it can be used as a dict
    key and referenced by `Edge.source_id` / `Edge.target_id` without holding
    onto a Python object reference.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable unique id: '{relative_file_path}::{qualified_name}'.")
    name: str = Field(description="Unqualified name, e.g. 'helper' or 'MyClass.method'.")
    qualified_name: str = Field(description="Dotted name within the module, e.g. 'MyClass.method'.")
    kind: SymbolKind
    file_path: str = Field(description="Path to the defining file, relative to the repo root.")
    line_start: int = Field(ge=1, description="1-indexed line the definition starts on.")
    line_end: int = Field(ge=1, description="1-indexed line the definition ends on (inclusive).")
    docstring: str | None = Field(
        default=None, description="The symbol's docstring, if any, verbatim."
    )
    parent_id: str | None = Field(
        default=None,
        description="Id of the enclosing class symbol, for methods. None otherwise.",
    )


class Edge(BaseModel):
    """A directed relationship between two symbols (currently: call edges).

    `target_id` may be `None` when the call target could not be resolved to a
    known symbol (e.g. it's an attribute call on an external object, or a
    dynamic/aliased call the indexer doesn't attempt to resolve). Unresolved
    edges are kept, with `target_name` populated, so callers of the graph can
    see "this function calls something named X we couldn't resolve" rather
    than the edge silently vanishing.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(description="Id of the Symbol that contains the call site.")
    target_id: str | None = Field(
        default=None, description="Id of the called Symbol, or None if unresolved."
    )
    target_name: str = Field(description="Name as written at the call site, e.g. 'helper'.")
    kind: EdgeKind = EdgeKind.CALLS
    line: int = Field(ge=1, description="1-indexed line the call occurs on.")


class SymbolGraph(BaseModel):
    """The full symbol graph for an indexed repo: all symbols and all edges.

    This is a plain data container — deliberately no query methods live here.
    All traversal logic (`find_definition`, `find_callers`, `blast_radius`,
    ...) lives in `graph.queries` so the model stays a stable serialization
    boundary (e.g. for handing the graph to an MCP client as JSON).
    """

    root_path: str = Field(description="Absolute or repo-relative path that was indexed.")
    symbols: dict[str, Symbol] = Field(
        default_factory=dict, description="All symbols, keyed by Symbol.id."
    )
    edges: list[Edge] = Field(default_factory=list, description="All call edges found.")
    files_indexed: list[str] = Field(
        default_factory=list, description="Relative paths of all files successfully parsed."
    )
    files_skipped: list[str] = Field(
        default_factory=list,
        description="Relative paths of files that failed to parse, e.g. syntax errors.",
    )
