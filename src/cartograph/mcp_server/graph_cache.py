"""Process-local graph reuse, invalidated by source content rather than timestamps."""

from __future__ import annotations

import hashlib
from pathlib import Path
from threading import RLock

from cartograph.graph.indexer import _iter_python_files, index_repo
from cartograph.graph.models import SymbolGraph


def _fingerprint(root: Path) -> bytes:
    # Share the indexer's exclusions and symlink boundary. Names participate so
    # additions, renames and deletions invalidate even when contents are equal.
    digest = hashlib.sha256()
    for path in sorted(_iter_python_files(root)):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.digest()


class RepositoryGraph:
    """Cache one graph without writing to the queried repository.

    Every lookup reads/hashes the current Python files. A changed fingerprint
    triggers a full rebuild, not an incremental parse. Concurrent edits during
    a rebuild are detected and retried; this is not a filesystem transaction.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._graph: SymbolGraph | None = None
        self._digest: bytes | None = None
        self._lock = RLock()

    def get(self) -> SymbolGraph:
        with self._lock:
            for _ in range(3):
                try:
                    before = _fingerprint(self.root)
                    if self._graph is not None and before == self._digest:
                        return self._graph
                    graph = index_repo(self.root, use_cache=False)
                    if before == _fingerprint(self.root):
                        self._graph, self._digest = graph, before
                        return graph
                except FileNotFoundError:
                    # Files may be added/removed by an editor during scanning.
                    continue
            raise ValueError(
                "Repository changed repeatedly while indexing; retry after edits settle"
            )
