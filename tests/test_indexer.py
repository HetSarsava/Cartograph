"""Tests for graph.indexer.index_repo against the sample_repo fixture."""

from pathlib import Path

from cartograph.graph.indexer import index_repo
from cartograph.graph.models import SymbolKind

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_finds_all_expected_symbols():
    graph = index_repo(FIXTURE_REPO)
    qualified_names = {s.qualified_name for s in graph.symbols.values()}

    assert "add" in qualified_names
    assert "double" in qualified_names
    assert "Calculator" in qualified_names
    assert "Calculator.compute" in qualified_names
    assert "Calculator.compute_twice" in qualified_names
    assert "run" in qualified_names


def test_symbol_kinds_and_parent_ids():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}

    assert by_qname["add"].kind == SymbolKind.FUNCTION
    assert by_qname["add"].parent_id is None

    assert by_qname["Calculator"].kind == SymbolKind.CLASS

    compute = by_qname["Calculator.compute"]
    assert compute.kind == SymbolKind.METHOD
    assert compute.parent_id == by_qname["Calculator"].id


def test_symbol_line_ranges_are_sane():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    add = by_qname["add"]
    assert add.line_start >= 1
    assert add.line_end >= add.line_start


def test_docstrings_extracted():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    assert by_qname["add"].docstring == "Return the sum of two integers."
    assert by_qname["Calculator"].docstring == "Performs a couple of toy arithmetic operations."


def test_resolves_plain_call_edges():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    add_id = by_qname["add"].id
    double_id = by_qname["double"].id

    double_targets = {e.target_id for e in graph.edges if e.source_id == double_id}
    assert add_id in double_targets


def test_resolves_self_call_edges():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    compute_id = by_qname["Calculator.compute"].id
    compute_twice_id = by_qname["Calculator.compute_twice"].id

    compute_twice_targets = {e.target_id for e in graph.edges if e.source_id == compute_twice_id}
    assert compute_id in compute_twice_targets


def test_resolves_cross_file_call_edges():
    graph = index_repo(FIXTURE_REPO)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    add_id = by_qname["add"].id
    double_id = by_qname["double"].id
    compute_id = by_qname["Calculator.compute"].id

    compute_targets = {e.target_id for e in graph.edges if e.source_id == compute_id}
    assert add_id in compute_targets
    assert double_id in compute_targets


def test_files_indexed_and_none_skipped():
    graph = index_repo(FIXTURE_REPO)
    assert "sample_pkg/utils.py" in graph.files_indexed
    assert "sample_pkg/service.py" in graph.files_indexed
    assert "sample_pkg/main.py" in graph.files_indexed
    assert graph.files_skipped == []


def test_index_single_file():
    graph = index_repo(FIXTURE_REPO / "sample_pkg" / "utils.py")
    qualified_names = {s.qualified_name for s in graph.symbols.values()}
    assert qualified_names == {"add", "double"}
