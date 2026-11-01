"""Tests for graph.indexer.index_repo against the sample_repo fixture."""

from pathlib import Path

import tree_sitter

from cartograph.graph import indexer as indexer_module
from cartograph.graph.cache import load_cache
from cartograph.graph.events import IndexEvent, IndexEventKind
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


# --- on_event: optional progress/replay stream --------------------------


def test_on_event_none_returns_identical_graph_to_before_this_change():
    """Regression check: passing no callback (the default) must not change
    index_repo()'s behavior or return value at all."""
    graph_without_callback = index_repo(FIXTURE_REPO)

    events: list[IndexEvent] = []
    graph_with_callback = index_repo(FIXTURE_REPO, on_event=events.append)

    assert graph_without_callback == graph_with_callback
    # And the existing, unmodified assertions from above still hold for a
    # plain on_event=None call — the callback path changes nothing.
    qualified_names = {s.qualified_name for s in graph_without_callback.symbols.values()}
    assert "add" in qualified_names
    assert "Calculator.compute" in qualified_names


def test_on_event_emits_file_started_and_finished_per_file():
    events: list[IndexEvent] = []
    graph = index_repo(FIXTURE_REPO, on_event=events.append)

    started_files = {e.file_path for e in events if e.kind == IndexEventKind.FILE_STARTED}
    finished_files = {e.file_path for e in events if e.kind == IndexEventKind.FILE_FINISHED}

    all_files = set(graph.files_indexed) | set(graph.files_skipped)
    assert all_files  # sanity: the fixture has files
    assert started_files == all_files
    assert finished_files == all_files


def test_on_event_emits_symbol_discovered_for_every_symbol():
    events: list[IndexEvent] = []
    graph = index_repo(FIXTURE_REPO, on_event=events.append)

    discovered_ids = {
        e.symbol.id for e in events if e.kind == IndexEventKind.SYMBOL_DISCOVERED and e.symbol
    }
    assert discovered_ids == set(graph.symbols.keys())


def test_on_event_emits_edge_discovered_for_every_edge_including_unresolved():
    events: list[IndexEvent] = []
    graph = index_repo(FIXTURE_REPO, on_event=events.append)

    edge_events = [e for e in events if e.kind == IndexEventKind.EDGE_DISCOVERED]
    emitted_edges = [e.edge for e in edge_events]
    # Same edges, same order as the returned graph — unresolved ones included,
    # not silently skipped.
    assert emitted_edges == graph.edges

    unresolved_in_graph = [edge for edge in graph.edges if edge.target_id is None]
    unresolved_in_events = [
        edge for edge in emitted_edges if edge is not None and edge.target_id is None
    ]
    assert len(unresolved_in_events) == len(unresolved_in_graph)


def test_on_event_index_finished_is_exactly_one_and_last_by_seq():
    events: list[IndexEvent] = []
    index_repo(FIXTURE_REPO, on_event=events.append)

    finished = [e for e in events if e.kind == IndexEventKind.INDEX_FINISHED]
    assert len(finished) == 1

    last_by_seq = max(events, key=lambda e: e.seq)
    assert last_by_seq.kind == IndexEventKind.INDEX_FINISHED
    assert finished[0] is last_by_seq


def test_on_event_seq_is_monotonic():
    events: list[IndexEvent] = []
    index_repo(FIXTURE_REPO, on_event=events.append)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # no duplicates


def test_on_event_edge_resolution_started_comes_after_all_files_finished():
    events: list[IndexEvent] = []
    index_repo(FIXTURE_REPO, on_event=events.append)

    last_file_finished_seq = max(e.seq for e in events if e.kind == IndexEventKind.FILE_FINISHED)
    resolution_started = next(e for e in events if e.kind == IndexEventKind.EDGE_RESOLUTION_STARTED)
    assert resolution_started.seq > last_file_finished_seq


def test_files_total_is_consistent_across_all_events_when_a_file_is_skipped(tmp_path):
    """`files_total` means "files found" everywhere it appears.

    EDGE_RESOLUTION_STARTED previously reported the *parsed* file count in
    that field, so a consumer drawing a progress bar saw the denominator
    shrink mid-run whenever a file failed to parse.
    """
    (tmp_path / "good.py").write_text("def a():\n    b()\n\n\ndef b():\n    pass\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")

    events = []
    graph = index_repo(tmp_path, on_event=events.append)

    assert graph.files_indexed == ["good.py"]
    assert graph.files_skipped == ["bad.py"]

    totals = {e.files_total for e in events if e.files_total is not None}
    assert totals == {2}, f"files_total disagreed across events: {totals}"

    resolution = next(e for e in events if e.kind is IndexEventKind.EDGE_RESOLUTION_STARTED)
    assert resolution.files_total == 2  # found
    assert resolution.files_done == 1  # parsed without error

    finished = next(e for e in events if e.kind is IndexEventKind.INDEX_FINISHED)
    assert (finished.files_done, finished.files_total) == (2, 2)


def test_dotted_call_does_not_bind_to_an_unrelated_method(tmp_path):
    """`d.get(...)` must not resolve to some class's `get` method.

    Nothing here infers the type of `d`, so binding them is a guess. It used
    to happen whenever exactly one symbol had a matching bare name, which on
    a real repo attached 71 dict lookups to a single method and made it the
    top result in "most referenced" -- and put it in everything's blast
    radius. An unresolved edge is honest; a fabricated one is not.
    """
    (tmp_path / "m.py").write_text(
        "class Cache:\n"
        "    def get(self, key):\n"
        "        return key\n"
        "\n"
        "def lookup(mapping):\n"
        "    return mapping.get('k')\n"
    )

    graph = index_repo(tmp_path)

    lookup_edges = [e for e in graph.edges if e.source_id.endswith("::lookup")]
    assert len(lookup_edges) == 1
    assert lookup_edges[0].target_name == "mapping.get"
    assert lookup_edges[0].target_id is None, "must stay unresolved rather than guess"


def test_dotted_call_still_resolves_to_a_module_level_function(tmp_path):
    """The tightened rule must not throw away `module.function()` edges,
    which are legitimate and common."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helpers.py").write_text("def transform(value):\n    return value\n")
    (pkg / "main.py").write_text("import helpers\n\ndef run(v):\n    return helpers.transform(v)\n")

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "helpers.transform")
    assert edge.target_id is not None
    assert graph.symbols[edge.target_id].qualified_name == "transform"


def test_self_method_call_still_resolves(tmp_path):
    """`self.x()` is unambiguous by construction and must keep resolving."""
    (tmp_path / "m.py").write_text(
        "class Thing:\n"
        "    def helper(self):\n"
        "        return 1\n"
        "\n"
        "    def run(self):\n"
        "        return self.helper()\n"
    )

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "self.helper")
    assert graph.symbols[edge.target_id].qualified_name == "Thing.helper"


def test_plain_call_to_a_unique_function_still_resolves(tmp_path):
    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1\n\ndef run():\n    return helper()\n"
    )

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "helper")
    assert graph.symbols[edge.target_id].qualified_name == "helper"


def test_call_through_a_self_attribute_is_not_a_call_on_self(tmp_path):
    """`self._jobs.get(k)` is a dict lookup, not the class's own `get`.

    The self-rule matched any target starting with "self.", so a call on an
    *attribute* of self was bound to a same-named method of the enclosing
    class. That fabricated self-recursion: the graph claimed `JobRunner.get`
    called itself, which then drew as a real-looking bidirectional edge in
    the neighbourhood view.
    """
    (tmp_path / "m.py").write_text(
        "class Runner:\n"
        "    def __init__(self):\n"
        "        self._jobs = {}\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._jobs.get(key)\n"
    )

    graph = index_repo(tmp_path)

    lookup = next(e for e in graph.edges if e.target_name == "self._jobs.get")
    assert lookup.target_id is None, "must not resolve to Runner.get"


def test_a_plain_self_method_call_still_resolves(tmp_path):
    """Narrowing the rule must not break the case it exists for."""
    (tmp_path / "m.py").write_text(
        "class Runner:\n"
        "    def helper(self):\n"
        "        return 1\n"
        "\n"
        "    def run(self):\n"
        "        return self.helper()\n"
    )

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "self.helper")
    assert graph.symbols[edge.target_id].qualified_name == "Runner.helper"


def test_genuine_self_recursion_is_still_recorded(tmp_path):
    """A function that really does call itself keeps that edge — the graph
    view draws it as a loop, and it is true."""
    (tmp_path / "m.py").write_text("def walk(node):\n    return walk(node)\n")

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "walk")
    assert edge.source_id == edge.target_id


# --- persistent, content-addressed cache (Part A) -------------------------


def _install_parse_spy(monkeypatch):
    """Replace indexer's `Parser` with a counting wrapper.

    Returns a zero-arg callable reporting how many `.parse()` calls the
    indexer has made since the spy was installed. tree-sitter's `Parser`
    cannot be subclassed, so the wrapper composes a real parser instead.
    """
    calls: list[bytes] = []

    class _SpyParser:
        def __init__(self, language):
            self._parser = tree_sitter.Parser(language)

        def parse(self, source, *args, **kwargs):
            calls.append(source)
            return self._parser.parse(source, *args, **kwargs)

    monkeypatch.setattr(indexer_module, "Parser", _SpyParser)
    return lambda: len(calls)


def test_cache_second_run_reparses_nothing_and_returns_identical_graph(tmp_path, monkeypatch):
    """A.6.1 — no changes between runs: zero re-parses, identical graph."""
    (tmp_path / "a.py").write_text("def foo(x):\n    return x\n")
    (tmp_path / "b.py").write_text("def bar():\n    return foo(1)\n")

    graph1 = index_repo(tmp_path)
    count = _install_parse_spy(monkeypatch)

    graph2 = index_repo(tmp_path)

    assert count() == 0, "unchanged files must not be re-parsed on a cache hit"
    assert graph1 == graph2
    cache_dir = tmp_path / ".cartograph" / "index_cache"
    assert (cache_dir / "manifest.json").is_file()
    assert load_cache(cache_dir) is not None


def test_cache_reparses_only_the_changed_file(tmp_path, monkeypatch):
    """A.6.2 — one changed file: only it is re-parsed; graph reflects it."""
    (tmp_path / "a.py").write_text("def foo(x):\n    return x\n")
    (tmp_path / "b.py").write_text("def bar():\n    return foo(1)\n")
    graph1 = index_repo(tmp_path)

    (tmp_path / "b.py").write_text("def bar():\n    return foo(1)\n\ndef extra():\n    return 0\n")
    count = _install_parse_spy(monkeypatch)
    events: list[IndexEvent] = []
    graph2 = index_repo(tmp_path, on_event=events.append)

    assert count() == 1, "only the modified file should be re-parsed"
    assert [e.file_path for e in events if e.kind is IndexEventKind.FILE_CACHE_HIT] == ["a.py"]
    assert [e.file_path for e in events if e.kind is IndexEventKind.FILE_CACHE_MISS] == ["b.py"]
    qnames2 = {s.qualified_name for s in graph2.symbols.values()}
    qnames1 = {s.qualified_name for s in graph1.symbols.values()}
    assert "extra" in qnames2 and "extra" not in qnames1


def test_cache_drops_symbols_and_edges_of_deleted_files(tmp_path):
    """A.6.3 — a deleted file's symbols and edges vanish from the result."""
    (tmp_path / "a.py").write_text("def foo(x):\n    return x\n")
    (tmp_path / "b.py").write_text("def bar():\n    return foo(1)\n")
    index_repo(tmp_path)

    (tmp_path / "b.py").unlink()
    graph2 = index_repo(tmp_path)

    assert not any(s.file_path == "b.py" for s in graph2.symbols.values())
    assert not any(e.source_id.startswith("b.py::") for e in graph2.edges)
    assert any(s.qualified_name == "foo" for s in graph2.symbols.values())


def test_cache_reresolves_edges_after_a_definition_is_deleted(tmp_path):
    """A.6.4 — pass 2 re-runs in full even for cache-hit files.

    `run`'s `foo()` is ambiguous (two `foo`s) on the first pass; deleting
    the other `foo`'s file must resolve it on the second pass even though
    the file containing `run` itself is a cache hit.
    """
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def foo():\n    pass\n\ndef run():\n    return foo()\n")

    graph1 = index_repo(tmp_path)
    edge1 = next(e for e in graph1.edges if e.source_id.endswith("::run"))
    assert edge1.target_id is None

    (tmp_path / "a.py").unlink()
    graph2 = index_repo(tmp_path)

    edge2 = next(e for e in graph2.edges if e.source_id.endswith("::run"))
    assert edge2.target_id is not None
    assert graph2.symbols[edge2.target_id].qualified_name == "foo"


def test_use_cache_false_bypasses_the_cache_entirely(tmp_path, monkeypatch):
    """A.6.5 — use_cache=False re-parses everything despite a valid cache."""
    (tmp_path / "a.py").write_text("def foo(x):\n    return x\n")
    index_repo(tmp_path)

    count = _install_parse_spy(monkeypatch)
    graph = index_repo(tmp_path, use_cache=False)

    assert count() == 1
    assert any(s.qualified_name == "foo" for s in graph.symbols.values())


def test_corrupt_cache_manifest_triggers_full_reindex(tmp_path, monkeypatch):
    """A.6.6 — a corrupt manifest must not raise; full re-index and rewrite."""
    (tmp_path / "a.py").write_text("def foo(x):\n    return x\n")
    index_repo(tmp_path)

    manifest_path = tmp_path / ".cartograph" / "index_cache" / "manifest.json"
    manifest_path.write_text("this is not json {{{{{{")

    count = _install_parse_spy(monkeypatch)
    graph = index_repo(tmp_path)  # must not raise

    assert count() == 1
    assert any(s.qualified_name == "foo" for s in graph.symbols.values())
    assert load_cache(tmp_path / ".cartograph" / "index_cache") is not None


# --- type-aware call resolution (Part B) ----------------------------------


def test_self_attr_call_resolves_to_inferred_class_method(tmp_path):
    """B.5.1 — `self._store.get(k)` resolves to `Storage.get`, not any other `get`."""
    (tmp_path / "m.py").write_text(
        "class Storage:\n"
        "    def get(self, key):\n"
        "        return key\n"
        "\n"
        "class Other:\n"
        "    def get(self, key):\n"
        "        return key\n"
        "\n"
        "def get(key):\n"
        "    return key\n"
        "\n"
        "class Cache:\n"
        "    def __init__(self):\n"
        "        self._store = Storage()\n"
        "\n"
        "    def read(self, key):\n"
        "        return self._store.get(key)\n"
    )

    graph = index_repo(tmp_path)
    by_qname = {s.qualified_name: s for s in graph.symbols.values()}
    storage_get_id = by_qname["Storage.get"].id

    edge = next(e for e in graph.edges if e.target_name == "self._store.get")
    assert edge.target_id == storage_get_id
    assert edge.target_id != by_qname["Other.get"].id


def test_self_attr_call_on_inferred_dict_stays_unresolved(tmp_path):
    """B.5.2 — `self._data = {}` makes `self._data.get(k)` a refused dict call."""
    (tmp_path / "m.py").write_text(
        "class Cache:\n"
        "    def __init__(self):\n"
        "        self._data = {}\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._data.get(key)\n"
    )

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "self._data.get")
    assert edge.target_id is None, "must not resolve to Cache.get"


def test_self_attr_call_with_unrecognized_rhs_stays_unresolved(tmp_path):
    """B.5.3 — unrecognized RHS -> no inferred type -> no guess, still unresolved."""
    (tmp_path / "m.py").write_text(
        "def compute_default():\n"
        "    return {}\n"
        "\n"
        "class Cache:\n"
        "    def __init__(self):\n"
        "        self._data = compute_default()\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._data.get(key)\n"
    )

    graph = index_repo(tmp_path)

    edge = next(e for e in graph.edges if e.target_name == "self._data.get")
    assert edge.target_id is None
