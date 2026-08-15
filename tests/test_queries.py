"""Tests for graph.queries against the sample_repo fixture's symbol graph."""

from pathlib import Path

import pytest

from cartograph.graph.indexer import index_repo
from cartograph.graph.queries import blast_radius, find_callers, find_definition

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
def sample_graph():
    return index_repo(FIXTURE_REPO)


def _by_qname(graph, qname: str):
    for symbol in graph.symbols.values():
        if symbol.qualified_name == qname:
            return symbol
    raise AssertionError(f"no symbol with qualified_name={qname!r}")


def test_find_definition_by_bare_name(sample_graph):
    results = find_definition(sample_graph, "add")
    assert len(results) == 1
    assert results[0].qualified_name == "add"


def test_find_definition_by_qualified_name(sample_graph):
    results = find_definition(sample_graph, "Calculator.compute")
    assert len(results) == 1
    assert results[0].qualified_name == "Calculator.compute"


def test_find_definition_method_by_bare_name(sample_graph):
    results = find_definition(sample_graph, "compute")
    assert len(results) == 1
    assert results[0].qualified_name == "Calculator.compute"


def test_find_definition_unknown_returns_empty(sample_graph):
    assert find_definition(sample_graph, "does_not_exist_anywhere") == []


def test_find_callers_of_add(sample_graph):
    add_symbol = _by_qname(sample_graph, "add")
    callers = find_callers(sample_graph, add_symbol.id)
    caller_names = {c.qualified_name for c in callers}
    assert "double" in caller_names
    assert "Calculator.compute" in caller_names


def test_find_callers_of_unreferenced_symbol_is_empty(sample_graph):
    run_symbol = _by_qname(sample_graph, "run")
    # Nothing in the fixture calls `run` itself.
    assert find_callers(sample_graph, run_symbol.id) == []


def test_blast_radius_transitive(sample_graph):
    add_symbol = _by_qname(sample_graph, "add")
    affected = {s.qualified_name for s in blast_radius(sample_graph, add_symbol.id)}

    # add <- double <- Calculator.compute <- Calculator.compute_twice
    # add <- Calculator.compute (direct) <- Calculator.compute_twice
    assert "double" in affected
    assert "Calculator.compute" in affected
    assert "Calculator.compute_twice" in affected


def test_blast_radius_excludes_self(sample_graph):
    add_symbol = _by_qname(sample_graph, "add")
    affected_ids = {s.id for s in blast_radius(sample_graph, add_symbol.id)}
    assert add_symbol.id not in affected_ids


def test_blast_radius_respects_max_depth(sample_graph):
    add_symbol = _by_qname(sample_graph, "add")
    depth_one = {s.qualified_name for s in blast_radius(sample_graph, add_symbol.id, max_depth=1)}
    # Direct callers of add only; Calculator.compute_twice is two hops away
    # (it calls Calculator.compute, which calls add) so it's excluded here.
    assert "double" in depth_one
    assert "Calculator.compute" in depth_one
    assert "Calculator.compute_twice" not in depth_one

    unbounded = {s.qualified_name for s in blast_radius(sample_graph, add_symbol.id)}
    assert "Calculator.compute_twice" in unbounded


def test_blast_radius_of_uncalled_symbol_is_empty(sample_graph):
    # Nothing in the fixture calls `run` itself.
    run_symbol = _by_qname(sample_graph, "run")
    assert blast_radius(sample_graph, run_symbol.id) == []
