"""Progress/replay event schema for `graph.indexer.index_repo`.

This is a feed for watching an index run happen — file by file, symbol by
symbol, then edge by edge — not a general logging system. It reuses the
existing `Symbol`/`Edge` models verbatim rather than duplicating their
fields; `IndexEvent` just says which one appeared and when.

Kept separate from `graph.models` so that module stays focused on the
`SymbolGraph` data shape itself.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from cartograph.graph.models import Edge, Symbol


class IndexEventKind(str, Enum):
    """What happened at a given point during `index_repo`."""

    FILE_STARTED = "file_started"
    FILE_CACHE_HIT = "file_cache_hit"
    FILE_CACHE_MISS = "file_cache_miss"
    SYMBOL_DISCOVERED = "symbol_discovered"
    FILE_FINISHED = "file_finished"
    EDGE_RESOLUTION_STARTED = "edge_resolution_started"
    EDGE_DISCOVERED = "edge_discovered"
    INDEX_FINISHED = "index_finished"


class IndexEvent(BaseModel):
    """A single event emitted during `index_repo`, in emission order."""

    kind: IndexEventKind
    seq: int
    """Monotonic counter, for ordering — events may arrive out of order at
    a consumer (e.g. read from a polled file) but `seq` disambiguates."""
    timestamp: float
    """`time.time()` at emission."""
    file_path: str | None = None
    symbol: Symbol | None = None
    edge: Edge | None = None
    files_total: int | None = None
    """How many `.py` files were found. Set on FILE_STARTED,
    EDGE_RESOLUTION_STARTED and INDEX_FINISHED, and constant across all three
    within a run — it counts files *found*, not files parsed."""
    files_done: int | None = None
    """Progress against `files_total`. Set on FILE_STARTED (files completed
    before this one), EDGE_RESOLUTION_STARTED (files parsed without error)
    and INDEX_FINISHED (all files, parsed or skipped)."""
