"""Tests for graph.tree_export.export_tree_html.

File-content assertions only — no browser/DOM test. The one behavior worth
testing in isolation is cycle handling, since a call graph with mutual
recursion is the failure mode most likely to hang or explode on a real repo.
"""

import json
from pathlib import Path

from cartograph.graph.indexer import index_repo
from cartograph.graph.models import Edge, EdgeKind, Symbol, SymbolGraph, SymbolKind
from cartograph.graph.tree_export import export_tree_html

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_exports_self_contained_html_for_fixture_repo(tmp_path):
    graph = index_repo(FIXTURE_REPO)
    out = export_tree_html(graph, tmp_path / "tree.html")

    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert len(html) > 0
    assert "TREE_DATA" in html
    # A known symbol from the fixture made it into the embedded data.
    assert "compute_twice" in html
    # Self-contained: no external requests of any kind.
    assert "http://" not in html
    assert "https://" not in html


def test_export_creates_parent_directories(tmp_path):
    graph = index_repo(FIXTURE_REPO)
    out = export_tree_html(graph, tmp_path / "nested" / "dir" / "tree.html")
    assert out.exists()


def test_returns_resolved_path(tmp_path):
    graph = index_repo(FIXTURE_REPO)
    out = export_tree_html(graph, tmp_path / "tree.html")
    assert out.is_absolute()


def _symbol(symbol_id: str, name: str, file_path: str = "cyclic.py") -> Symbol:
    return Symbol(
        id=symbol_id,
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        file_path=file_path,
        line_start=1,
        line_end=2,
    )


def test_cycle_does_not_hang_and_is_marked(tmp_path):
    """A calls B, B calls A — must terminate and mark the back-edge."""
    a = _symbol("cyclic.py::a", "a")
    b = _symbol("cyclic.py::b", "b")
    graph = SymbolGraph(
        root_path="/tmp/cyclic",
        symbols={a.id: a, b.id: b},
        edges=[
            Edge(source_id=a.id, target_id=b.id, target_name="b", kind=EdgeKind.CALLS, line=1),
            Edge(source_id=b.id, target_id=a.id, target_name="a", kind=EdgeKind.CALLS, line=2),
        ],
        files_indexed=["cyclic.py"],
        files_skipped=[],
    )

    out = export_tree_html(graph, tmp_path / "cyclic.html")

    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "TREE_DATA" in html
    # The back-edge is rendered as a leaf marked "cycle" rather than recursing.
    assert "cycle" in html


def test_self_recursive_symbol_terminates(tmp_path):
    """A calls itself — the degenerate cycle case."""
    a = _symbol("cyclic.py::a", "a")
    graph = SymbolGraph(
        root_path="/tmp/cyclic",
        symbols={a.id: a},
        edges=[
            Edge(source_id=a.id, target_id=a.id, target_name="a", kind=EdgeKind.CALLS, line=1)
        ],
        files_indexed=["cyclic.py"],
        files_skipped=[],
    )

    out = export_tree_html(graph, tmp_path / "self.html")
    assert out.exists()
    assert "cycle" in out.read_text(encoding="utf-8")


def test_unresolved_edge_is_rendered_not_dropped(tmp_path):
    a = _symbol("mod.py::a", "a", file_path="mod.py")
    graph = SymbolGraph(
        root_path="/tmp/mod",
        symbols={a.id: a},
        edges=[
            Edge(
                source_id=a.id,
                target_id=None,
                target_name="some_external_thing",
                kind=EdgeKind.CALLS,
                line=1,
            )
        ],
        files_indexed=["mod.py"],
        files_skipped=[],
    )

    out = export_tree_html(graph, tmp_path / "unresolved.html")
    html = out.read_text(encoding="utf-8")
    assert "some_external_thing" in html
    assert '"resolved":false' in html


def test_files_without_symbols_are_omitted(tmp_path):
    a = _symbol("has_symbols.py::a", "a", file_path="has_symbols.py")
    graph = SymbolGraph(
        root_path="/tmp/mod",
        symbols={a.id: a},
        edges=[],
        files_indexed=["has_symbols.py", "empty.py"],
        files_skipped=[],
    )

    out = export_tree_html(graph, tmp_path / "tree.html")
    html = out.read_text(encoding="utf-8")
    assert "has_symbols.py" in html
    assert "empty.py" not in html


def test_placeholder_token_in_repo_content_does_not_corrupt_the_page(tmp_path):
    """The template is filled in one pass, not by chained str.replace.

    A repo containing a template token — a file named `__SUMMARY__.py`, say —
    would otherwise have the later substitution splice the summary JSON into
    the middle of the tree data and break the page's JavaScript.
    """
    symbol = _symbol("__SUMMARY__.py::f", "f", file_path="__SUMMARY__.py")
    graph = SymbolGraph(
        root_path="/repo",
        symbols={symbol.id: symbol},
        edges=[],
        files_indexed=["__SUMMARY__.py"],
        files_skipped=[],
    )

    html = export_tree_html(graph, tmp_path / "out.html").read_text(encoding="utf-8")

    tree_line = next(line for line in html.splitlines() if line.startswith("const TREE_DATA ="))
    payload = tree_line[len("const TREE_DATA = ") :].rstrip(";")
    # The tree data must still be the tree data — not the summary spliced in.
    parsed = json.loads(payload.replace(r"<\/", "</"))
    assert parsed[0]["name"] == "__SUMMARY__.py"
    assert "files_indexed" not in payload
