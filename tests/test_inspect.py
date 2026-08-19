"""Tests for webui.inspect: symbol search, impact analysis, and insights.

Built on small hand-made graphs rather than the fixture repo, so the
expected ranking and counts are obvious from reading the test.
"""

import pytest

from cartograph.graph.models import Edge, EdgeKind, Symbol, SymbolGraph, SymbolKind
from cartograph.webui.inspect import (
    MAX_CACHED_GRAPHS,
    cache_graph,
    clear_cache,
    compute_insights,
    get_graph,
    neighbourhood,
    search_symbols,
    symbol_detail,
)


def _sym(sid, name, kind=SymbolKind.FUNCTION, file_path="a.py", line=1, parent=None, doc=None):
    return Symbol(
        id=sid,
        name=name,
        qualified_name=name,
        kind=kind,
        file_path=file_path,
        line_start=line,
        line_end=line + 4,
        docstring=doc,
        parent_id=parent,
    )


def _graph(symbols, edges=()):
    return SymbolGraph(
        root_path="/repo",
        symbols={s.id: s for s in symbols},
        edges=list(edges),
        files_indexed=sorted({s.file_path for s in symbols}),
        files_skipped=[],
    )


def _edge(source, target, name=None, line=1):
    return Edge(
        source_id=source,
        target_id=target,
        target_name=name or (target.split("::")[-1] if target else "unknown"),
        kind=EdgeKind.CALLS,
        line=line,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


# --- search -------------------------------------------------------------


def test_search_ranks_exact_then_prefix_then_substring():
    graph = _graph([
        _sym("a.py::run", "run"),
        _sym("a.py::run_reporter", "run_reporter"),
        _sym("a.py::dry_run_all", "dry_run_all"),
    ])

    names = [r["name"] for r in search_symbols(graph, "run")]

    assert names == ["run", "run_reporter", "dry_run_all"]


def test_search_is_case_insensitive():
    graph = _graph([_sym("a.py::BlastRadius", "BlastRadius")])

    assert search_symbols(graph, "blastradius")[0]["name"] == "BlastRadius"


def test_search_matches_a_scattered_subsequence():
    """Typing initials is the fastest way to reach a long name."""
    graph = _graph([_sym("a.py::find_callers", "find_callers")])

    assert [r["name"] for r in search_symbols(graph, "fcl")] == ["find_callers"]


def test_search_can_match_on_file_path():
    graph = _graph([_sym("tools/snapshot.py::take", "take", file_path="tools/snapshot.py")])

    assert [r["name"] for r in search_symbols(graph, "snapshot")] == ["take"]


def test_empty_query_returns_most_referenced_not_nothing():
    """An empty search box should still suggest where to start."""
    popular = _sym("a.py::popular", "popular")
    lonely = _sym("a.py::lonely", "lonely")
    caller = _sym("a.py::caller", "caller")
    graph = _graph(
        [popular, lonely, caller],
        [_edge(caller.id, popular.id), _edge(lonely.id, popular.id)],
    )

    assert search_symbols(graph, "")[0]["name"] == "popular"


def test_search_returns_nothing_for_a_miss():
    graph = _graph([_sym("a.py::alpha", "alpha")])

    assert search_symbols(graph, "zzzzz") == []


def test_search_respects_the_limit():
    graph = _graph([_sym(f"a.py::fn{i}", f"fn{i}") for i in range(30)])

    assert len(search_symbols(graph, "fn", limit=5)) == 5


# --- detail / blast radius ----------------------------------------------


def test_symbol_detail_reports_callers_calls_and_blast_radius():
    """The transitive chain top -> middle -> leaf means changing `leaf`
    affects both, which is the question the graph exists to answer."""
    leaf = _sym("a.py::leaf", "leaf")
    middle = _sym("a.py::middle", "middle")
    top = _sym("a.py::top", "top")
    graph = _graph(
        [leaf, middle, top],
        [_edge(middle.id, leaf.id), _edge(top.id, middle.id)],
    )

    detail = symbol_detail(graph, leaf.id)

    assert [c["name"] for c in detail["callers"]] == ["middle"]
    assert sorted(s["name"] for s in detail["blast_radius"]) == ["middle", "top"]
    assert detail["calls"] == []


def test_symbol_detail_returns_none_for_an_unknown_id():
    assert symbol_detail(_graph([_sym("a.py::x", "x")]), "nope::nope") is None


def test_symbol_detail_includes_docstring_and_location():
    sym = _sym("pkg/m.py::fn", "fn", file_path="pkg/m.py", line=12, doc="Does a thing.")

    detail = symbol_detail(_graph([sym]), sym.id)

    assert detail["docstring"] == "Does a thing."
    assert (detail["file_path"], detail["line_start"], detail["line_end"]) == ("pkg/m.py", 12, 16)


# --- why a call is unresolved -------------------------------------------


def test_unresolved_call_to_a_duplicated_name_is_reported_as_ambiguous():
    """Two symbols share a name, so the indexer refused to guess. The UI
    should say that plainly and name both candidates rather than just
    shrugging with 'unresolved'."""
    caller = _sym("a.py::caller", "caller")
    graph = _graph(
        [caller, _sym("b.py::dup", "dup", file_path="b.py"), _sym("c.py::dup", "dup", file_path="c.py")],
        [_edge(caller.id, None, name="dup")],
    )

    why = symbol_detail(graph, caller.id)["calls"][0]["why"]

    assert why["reason"] == "ambiguous"
    assert len(why["candidates"]) == 2


def test_unresolved_call_to_an_unknown_name_is_reported_as_external():
    caller = _sym("a.py::caller", "caller")
    graph = _graph([caller], [_edge(caller.id, None, name="json.dumps")])

    why = symbol_detail(graph, caller.id)["calls"][0]["why"]

    assert why["reason"] == "external"
    assert why["candidates"] == []


def test_unresolved_dotted_call_naming_one_method_explains_the_receiver():
    """`x.close()` where exactly one `close` exists: the name matches, but
    nothing knows the type of `x`, so binding them would be a guess."""
    caller = _sym("a.py::caller", "caller")
    method = _sym("b.py::Sink.close", "Sink.close", kind=SymbolKind.METHOD, file_path="b.py")
    graph = _graph([caller, method], [_edge(caller.id, None, name="sink.close")])

    why = symbol_detail(graph, caller.id)["calls"][0]["why"]

    assert why["reason"] == "unknown receiver"
    assert why["candidates"][0]["qualified_name"] == "Sink.close"


# --- neighbourhood ------------------------------------------------------


def test_neighbourhood_labels_callers_callees_and_centre():
    centre = _sym("a.py::centre", "centre")
    caller = _sym("a.py::caller", "caller")
    callee = _sym("a.py::callee", "callee")
    graph = _graph(
        [centre, caller, callee],
        [_edge(caller.id, centre.id), _edge(centre.id, callee.id)],
    )

    roles = {n["id"]: n["role"] for n in neighbourhood(graph, centre.id)["nodes"]}

    assert roles == {centre.id: "centre", caller.id: "caller", callee.id: "callee"}


def test_neighbourhood_collapses_repeated_call_sites_into_one_edge():
    """Calling the same function three times is one arrow, not three."""
    a = _sym("a.py::a", "a")
    b = _sym("a.py::b", "b")
    graph = _graph([a, b], [_edge(a.id, b.id, line=n) for n in (1, 2, 3)])

    assert len(neighbourhood(graph, a.id)["edges"]) == 1


def test_neighbourhood_of_an_unknown_symbol_is_empty_not_an_error():
    assert neighbourhood(_graph([]), "nope") == {"nodes": [], "edges": []}


# --- insights -----------------------------------------------------------


def test_insights_rank_most_referenced_symbols():
    hot = _sym("a.py::hot", "hot")
    callers = [_sym(f"a.py::c{i}", f"c{i}") for i in range(3)]
    graph = _graph([hot, *callers], [_edge(c.id, hot.id) for c in callers])

    insights = compute_insights(graph)

    assert insights["most_called"][0]["name"] == "hot"
    assert insights["most_called"][0]["count"] == 3


def test_insights_exclude_tests_from_never_referenced():
    """A test function having no callers is normal, not a finding."""
    graph = _graph([_sym("t.py::test_thing", "test_thing", file_path="t.py")])

    assert compute_insights(graph)["never_called"] == []


def test_insights_count_unresolved_calls_by_name():
    caller = _sym("a.py::caller", "caller")
    graph = _graph(
        [caller],
        [_edge(caller.id, None, name="len", line=n) for n in (1, 2)]
        + [_edge(caller.id, None, name="print", line=3)],
    )

    top = compute_insights(graph)["top_unresolved"]

    assert top[0] == {"name": "len", "count": 2}


def test_insights_totals_match_the_graph():
    graph = _graph(
        [_sym("a.py::f", "f"), _sym("a.py::C", "C", kind=SymbolKind.CLASS)],
        [_edge("a.py::f", "a.py::C"), _edge("a.py::f", None, name="ext")],
    )

    totals = compute_insights(graph)["totals"]

    assert totals["symbols"] == 2
    assert totals["classes"] == 1
    assert (totals["edges"], totals["resolved"], totals["unresolved"]) == (2, 1, 1)


# --- cache --------------------------------------------------------------


def test_get_graph_returns_the_most_recent_build_by_default():
    first, second = _graph([_sym("a.py::a", "a")]), _graph([_sym("b.py::b", "b")])
    cache_graph("first", first)
    cache_graph("second", second)

    assert get_graph() is second
    assert get_graph("first") is first


def test_cache_evicts_the_oldest_graph():
    for i in range(MAX_CACHED_GRAPHS + 2):
        cache_graph(f"g{i}", _graph([_sym(f"{i}.py::s", "s", file_path=f"{i}.py")]))

    assert get_graph("g0") is None
    assert get_graph(f"g{MAX_CACHED_GRAPHS + 1}") is not None


def test_get_graph_is_none_before_anything_is_built():
    assert get_graph() is None


def test_neighbourhood_marks_a_mutual_pair_as_bidirectional():
    """A calls B and B calls A is one link with `both`, not two stacked
    lines — the renderer puts an arrowhead on each end."""
    a = _sym("a.py::a", "a")
    b = _sym("a.py::b", "b")
    graph = _graph([a, b], [_edge(a.id, b.id), _edge(b.id, a.id)])

    edges = neighbourhood(graph, a.id)["edges"]

    assert len(edges) == 1
    assert edges[0]["both"] is True


def test_neighbourhood_keeps_one_way_calls_directional():
    """Direction is the point: source is the caller, target the callee."""
    caller = _sym("a.py::caller", "caller")
    callee = _sym("a.py::callee", "callee")
    graph = _graph([caller, callee], [_edge(caller.id, callee.id)])

    edges = neighbourhood(graph, caller.id)["edges"]

    assert len(edges) == 1
    assert edges[0]["source"] == caller.id
    assert edges[0]["target"] == callee.id
    assert edges[0]["both"] is False


def test_repeated_calls_in_both_directions_still_collapse_to_one_link():
    a = _sym("a.py::a", "a")
    b = _sym("a.py::b", "b")
    graph = _graph(
        [a, b],
        [_edge(a.id, b.id, line=1), _edge(a.id, b.id, line=2), _edge(b.id, a.id, line=3)],
    )

    edges = neighbourhood(graph, a.id)["edges"]

    assert len(edges) == 1
    assert edges[0]["both"] is True

