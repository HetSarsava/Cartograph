"""Graph reuse must not trade freshness for speed or write into the repository."""

import os
from concurrent.futures import ThreadPoolExecutor

from cartograph.mcp_server import graph_cache


def test_content_freshness_and_reuse(tmp_path, monkeypatch):
    target = tmp_path / "module.py"
    target.write_text("def alpha(): pass\n")
    original = graph_cache.index_repo
    builds = []

    def build(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_cache, "index_repo", build)
    cache = graph_cache.RepositoryGraph(tmp_path)
    first = cache.get()
    assert cache.get() is first
    assert len(builds) == 1
    (tmp_path / "README.md").write_text("Not a Python source change")
    assert cache.get() is first

    # Same size and timestamp: stat-only invalidation would miss this edit.
    stamp = target.stat()
    target.write_text("def bravo(): pass\n")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = cache.get()
    assert second is not first
    assert "module.py::bravo" in second.symbols
    assert "module.py::alpha" not in second.symbols
    target.rename(tmp_path / "renamed.py")
    assert "renamed.py::bravo" in cache.get().symbols
    (tmp_path / "added.py").write_text("def extra(): pass\n")
    assert "added.py::extra" in cache.get().symbols
    (tmp_path / "added.py").unlink()
    assert "added.py::extra" not in cache.get().symbols
    assert not (tmp_path / ".cartograph").exists()


def test_simultaneous_queries_share_one_build(tmp_path, monkeypatch):
    (tmp_path / "module.py").write_text("def target(): pass\n")
    original = graph_cache.index_repo
    builds = []

    def build(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_cache, "index_repo", build)
    cache = graph_cache.RepositoryGraph(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.get(), range(8)))
    assert all(graph is results[0] for graph in results)
    assert len(builds) == 1
