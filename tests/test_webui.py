"""Tests for the local web UI's request-handling logic.

Covers the functions the HTTP handler delegates to, rather than driving the
handler itself. The traversal guard in `safe_tree_path` gets the most
attention since it's the one place untrusted input reaches the filesystem;
`bind` is the only part that touches a real socket.
"""

from pathlib import Path

import pytest

from cartograph.webui.server import (
    PortInUseError,
    bind,
    build_tree,
    discover_examples,
    safe_tree_path,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


# --- build_tree ---------------------------------------------------------


def test_build_tree_succeeds_for_fixture_repo(tmp_path):
    result = build_tree(str(FIXTURE_REPO), logs_dir=tmp_path)

    assert result.ok is True
    assert result.error is None
    assert result.symbols > 0
    assert result.edges > 0
    assert result.resolved + result.unresolved == result.edges
    assert result.files_indexed > 0
    assert result.tree_filename is not None
    assert (tmp_path / result.tree_filename).exists()


def test_build_tree_reports_missing_path_without_raising(tmp_path):
    result = build_tree(str(tmp_path / "definitely-not-here"), logs_dir=tmp_path)

    assert result.ok is False
    assert result.error is not None
    assert "does not exist" in result.error
    assert result.tree_filename is None


def test_build_tree_rejects_a_file_target(tmp_path):
    target = tmp_path / "a_file.py"
    target.write_text("x = 1\n")

    result = build_tree(str(target), logs_dir=tmp_path)

    assert result.ok is False
    assert "Not a directory" in result.error


def test_back_to_back_builds_do_not_overwrite_each_other(tmp_path):
    """The timestamp is second-resolution, so two builds in the same second
    must still land in distinct files — clicking two examples quickly is an
    easy way to hit this."""
    first = build_tree(str(FIXTURE_REPO), logs_dir=tmp_path)
    second = build_tree(str(FIXTURE_REPO), logs_dir=tmp_path)

    assert first.ok and second.ok
    assert first.tree_filename != second.tree_filename
    assert (tmp_path / first.tree_filename).exists()
    assert (tmp_path / second.tree_filename).exists()
    # And the uniquified name is still servable through the traversal guard.
    assert safe_tree_path(second.tree_filename, logs_dir=tmp_path) is not None


def test_build_tree_handles_empty_directory(tmp_path):
    empty = tmp_path / "empty_repo"
    empty.mkdir()

    result = build_tree(str(empty), logs_dir=tmp_path)

    assert result.ok is True
    assert result.symbols == 0
    assert result.files_indexed == 0


# --- safe_tree_path (traversal guard) -----------------------------------


def test_safe_tree_path_accepts_a_real_generated_tree(tmp_path):
    name = "tree-20260101T000000Z.html"
    (tmp_path / name).write_text("<html></html>")

    assert safe_tree_path(name, logs_dir=tmp_path) == (tmp_path / name).resolve()


def test_safe_tree_path_rejects_missing_file(tmp_path):
    assert safe_tree_path("tree-20260101T000000Z.html", logs_dir=tmp_path) is None


def test_safe_tree_path_rejects_traversal_and_odd_names(tmp_path):
    (tmp_path / "tree-ok.html").write_text("<html></html>")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("sensitive")

    for bad in [
        "../secret.txt",
        "../../etc/passwd",
        "tree-../../secret.txt",
        "subdir/tree-x.html",
        "tree-x.html/../../secret.txt",
        "secret.txt",
        "tree-x.txt",
        "notatree.html",
        "",
        ".",
        "tree-\\..\\secret.txt",
        "tree-%2e%2e%2fsecret.txt",
    ]:
        assert safe_tree_path(bad, logs_dir=tmp_path) is None, f"should reject {bad!r}"


# --- discover_examples --------------------------------------------------


def test_discover_examples_lists_recent_trees_newest_first(tmp_path):
    import os
    import time

    older = tmp_path / "tree-20260101T000000Z.html"
    newer = tmp_path / "tree-20260202T000000Z.html"
    older.write_text("<html></html>")
    newer.write_text("<html></html>")
    now = time.time()
    os.utime(older, (now - 500, now - 500))
    os.utime(newer, (now, now))

    state = discover_examples(logs_dir=tmp_path)

    names = [r.filename for r in state.recent]
    assert names == [newer.name, older.name]
    assert state.cwd


def test_discover_examples_ignores_non_tree_files(tmp_path):
    (tmp_path / "tree-20260101T000000Z.html").write_text("<html></html>")
    (tmp_path / "run-something.log").write_text("log")
    (tmp_path / "graph-events-x.jsonl").write_text("{}")

    state = discover_examples(logs_dir=tmp_path)

    assert [r.filename for r in state.recent] == ["tree-20260101T000000Z.html"]


def test_build_tree_rejects_an_empty_path_instead_of_indexing_the_cwd(tmp_path):
    """Path("") and Path("   ") both resolve to the CWD, so a blank input
    would otherwise quietly index whatever directory `serve` was launched
    from — a slow, confusing surprise rather than an error."""
    for blank in ["", "   ", "\t"]:
        result = build_tree(blank, logs_dir=tmp_path)
        assert result.ok is False, f"should reject {blank!r}"
        assert "No repository path" in result.error


def test_build_tree_tolerates_surrounding_whitespace(tmp_path):
    """Pasted paths often carry a stray space; that shouldn't be an error."""
    result = build_tree(f"  {FIXTURE_REPO}  ", logs_dir=tmp_path)

    assert result.ok is True
    assert result.symbols > 0


# --- port binding -------------------------------------------------------


def test_bind_raises_port_in_use_for_a_taken_port():
    """`cartograph serve` must be able to detect a taken port *before* it
    prints a success banner, so a second run gets one clean line instead of
    a false 'running at ...' followed by a traceback."""
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(PortInUseError) as excinfo:
            bind(port)
        assert str(port) in str(excinfo.value)
    finally:
        holder.close()


def test_bind_returns_a_usable_server_on_a_free_port():
    httpd = bind(0)  # 0 = let the OS pick a free port
    try:
        assert httpd.server_address[0] == "127.0.0.1"
        assert httpd.server_address[1] > 0
    finally:
        httpd.server_close()


def test_a_second_bind_on_the_same_port_is_refused_not_silently_shared():
    """Two `cartograph serve` runs on one port must not both succeed.

    `http.server` enables SO_REUSEADDR, which on Windows lets a second
    process bind a port another is actively listening on — both servers then
    compete for connections and requests land on an arbitrary one. This is
    the real-world path into `PortInUseError`, so it needs its own test.
    """
    first = bind(0)
    try:
        with pytest.raises(PortInUseError):
            bind(first.server_address[1])
    finally:
        first.server_close()
