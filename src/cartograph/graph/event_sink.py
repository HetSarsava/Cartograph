"""File-based sink for `graph.indexer.index_repo`'s optional event stream.

Writes newline-delimited JSON (one `IndexEvent` per line), flushed
immediately after every write, so a separate process polling/tailing the
file sees events as they happen — no socket, no server, just a plain file.
"""

from __future__ import annotations

from pathlib import Path

from cartograph.graph.events import IndexEvent


class JsonlEventSink:
    """Callable `IndexEvent` sink: appends one JSON line per call.

    Usage::

        sink = JsonlEventSink("logs/graph-events.jsonl")
        index_repo(repo_path, on_event=sink)
        sink.close()
    """

    def __init__(self, path: str | Path) -> None:
        """Create (truncating any existing content) the file at `path`.

        Args:
            path: Where to write events. Parent directories are created if
                missing. Truncated fresh on init so a previous run's events
                never bleed into this one.
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8")

    def __call__(self, event: IndexEvent) -> None:
        """Append `event` as one JSON line and flush immediately."""
        self._file.write(event.model_dump_json())
        self._file.write("\n")
        self._file.flush()

    def close(self) -> None:
        """Close the underlying file. Safe to call more than once."""
        if not self._file.closed:
            self._file.close()

    @property
    def path(self) -> Path:
        return self._path
