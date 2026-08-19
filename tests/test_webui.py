"""Tests for the local web UI's request-handling logic.

Covers the functions the HTTP handler delegates to, rather than driving the
handler itself. The traversal guard in `safe_tree_path` gets the most
attention since it's the one place untrusted input reaches the filesystem;
`bind` is the only part that touches a real socket.
"""

import logging
import re
import time
from pathlib import Path

import pytest

from cartograph.webui.jobs import JobRunner, find_sample_repo
from cartograph.webui.page import PAGE
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


# --- background jobs ----------------------------------------------------


def _wait(runner, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job is not None and job.status != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_job_runner_reports_result_and_captured_lines():
    runner = JobRunner()
    job_id = runner.start(lambda job, lock: {"answer": 42})

    job = _wait(runner, job_id)

    assert job.status == "done"
    assert job.result == {"answer": 42}
    assert job.error is None


def test_job_runner_captures_failure_instead_of_killing_the_thread():
    """A failed migration has to surface in the UI, not vanish into a thread."""
    runner = JobRunner()

    def boom(job, lock):
        raise RuntimeError("pipeline exploded")

    job = _wait(runner, runner.start(boom))

    assert job.status == "error"
    assert "pipeline exploded" in job.error


def test_job_runner_streams_pipeline_log_lines_to_the_job():
    """The live log is the point of the Migrate tab -- pipeline records must
    reach the job while it runs, and unrelated loggers must not."""
    runner = JobRunner()

    def work(job, lock):
        logging.getLogger("cartograph.pipeline.nodes").info("[tool:index_repo] symbols=18")
        logging.getLogger("cartograph.webui.server").info("noise that should not show")
        return {}

    job = _wait(runner, runner.start(work))

    assert "[tool:index_repo] symbols=18" in job.lines
    assert not any("noise" in line for line in job.lines)


def test_job_runner_is_busy_only_while_running():
    runner = JobRunner()
    assert runner.busy() is False
    job_id = runner.start(lambda job, lock: {})
    _wait(runner, job_id)
    assert runner.busy() is False


def test_get_unknown_job_returns_none():
    assert JobRunner().get("nope") is None


# --- demo fixture lookup ------------------------------------------------


def test_find_sample_repo_locates_the_bundled_fixture():
    """Found by searching upward, so it survives modules moving directories."""
    found = find_sample_repo()

    assert found.is_dir()
    assert (found / "tests" / "test_legacy_unittest.py").is_file()
    assert found == FIXTURE_REPO.resolve()


def test_bundled_fixture_is_still_pre_migration():
    """The demo has nothing to show if the fixture ships already converted --
    which is exactly how it got committed once before."""
    source = (find_sample_repo() / "tests" / "test_legacy_unittest.py").read_text(encoding="utf-8")

    assert "self.assertEqual" in source
    assert "unittest.TestCase" in source


# --- page ---------------------------------------------------------------


def test_page_references_only_elements_it_defines():
    """A typo in a getElementById would break the UI silently at runtime."""
    js = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)

    # An id can be declared in the static markup or in a JS template that
    # renders markup (the neighbourhood <svg>, for instance), so scan both.
    defined = set(re.findall(r'id="([^"]+)"', PAGE))
    used = set(re.findall(r'\$\("([^"]+)"\)', js))

    assert used - defined == set(), f"JS references missing ids: {sorted(used - defined)}"


def test_page_is_self_contained():
    """A strict no-network page: everything inline, nothing fetched remotely."""
    assert "<script>" in PAGE
    assert "src=\"http" not in PAGE
    assert "href=\"http" not in PAGE.split("<script>")[0].replace(
        'href="https://console.groq.com/keys"', ""
    )


def test_neighbourhood_arrowheads_are_sized_independently_of_stroke_width():
    """SVG markerUnits defaults to strokeWidth, which on a 1px line renders
    an arrowhead a few pixels across in the same colour as the line — there,
    but invisible, so the graph reads as undirected."""
    js = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)

    assert 'markerUnits: "userSpaceOnUse"' in js
    # One marker serves both ends: at the start it flips, which is what a
    # mutual call needs.
    assert 'orient: "auto-start-reverse"' in js
    assert 'marker-start' in js

