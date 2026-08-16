"""Tests for graph.event_sink.JsonlEventSink."""

import json

from cartograph.graph.event_sink import JsonlEventSink
from cartograph.graph.events import IndexEvent, IndexEventKind


def test_sink_writes_one_json_line_per_call(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)

    sink(IndexEvent(kind=IndexEventKind.FILE_STARTED, seq=0, timestamp=1.0, file_path="a.py"))
    sink(IndexEvent(kind=IndexEventKind.FILE_FINISHED, seq=1, timestamp=2.0, file_path="a.py"))
    sink.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = IndexEvent.model_validate(json.loads(lines[0]))
    assert first.kind == IndexEventKind.FILE_STARTED
    assert first.seq == 0
    assert first.file_path == "a.py"

    second = IndexEvent.model_validate(json.loads(lines[1]))
    assert second.kind == IndexEventKind.FILE_FINISHED
    assert second.seq == 1


def test_sink_flushes_immediately(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    sink(IndexEvent(kind=IndexEventKind.INDEX_FINISHED, seq=0, timestamp=1.0))

    # Read back without closing the sink first — proves each write is flushed.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    sink.close()


def test_sink_truncates_on_reinit(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    sink(IndexEvent(kind=IndexEventKind.FILE_STARTED, seq=0, timestamp=1.0))
    sink(IndexEvent(kind=IndexEventKind.FILE_FINISHED, seq=1, timestamp=2.0))
    sink.close()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

    fresh_sink = JsonlEventSink(path)
    assert path.read_text(encoding="utf-8") == ""
    fresh_sink(IndexEvent(kind=IndexEventKind.INDEX_FINISHED, seq=0, timestamp=3.0))
    fresh_sink.close()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_sink_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "events.jsonl"
    sink = JsonlEventSink(path)
    sink(IndexEvent(kind=IndexEventKind.INDEX_FINISHED, seq=0, timestamp=1.0))
    sink.close()
    assert path.exists()


def test_sink_close_is_idempotent(tmp_path):
    sink = JsonlEventSink(tmp_path / "events.jsonl")
    sink.close()
    sink.close()  # must not raise
