"""Real Git/pytest/coverage regressions for patch evidence, not mocked success."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cartograph.cli import cli
from cartograph.graph.indexer import index_repo
from cartograph.review.changes import candidates, changed_functions, diff_lines, git
from cartograph.review.engine import classify_mutation, review
from cartograph.review.models import RunEvidence
from cartograph.review.runner import run_pytest


def make_repo(root: Path, *, strong: bool = False) -> Path:
    root.mkdir()
    (root / "tests").mkdir()
    (root / ".gitignore").write_text(".cartograph/\n__pycache__/\n.pytest_cache/\n")
    (root / "shop.py").write_text(
        "def total(subtotal, discount=0):\n    return subtotal\n\n"
        "def checkout(subtotal, discount=0):\n    return total(subtotal, discount)\n",
        encoding="utf-8",
    )
    test = ("from shop import checkout\nfrom pathlib import Path\n"
            "def test_total():\n    Path('runtime_state.txt').write_text('isolated')\n"
            "    assert checkout(100, 0) == 100\n")
    if strong:
        test += "    assert checkout(100, 20) == 80\n"
    (root / "tests/test_shop.py").write_text(test, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "base")
    target = root / "shop.py"
    target.write_text(target.read_text().replace("return subtotal\n", "return subtotal - discount\n"))
    return root


@pytest.mark.parametrize("strong,expected", [(False, "survived"), (True, "detected")])
def test_real_review_distinguishes_execution_from_detection(tmp_path, strong, expected):
    repo = make_repo(tmp_path / "repo", strong=strong)
    before = (repo / "shop.py").read_bytes()
    patch_before = git(repo, "diff")
    output = repo / ".cartograph/review"
    test_paths = [str(repo / "tests")] if strong else ["tests"]
    report = review(repo, "HEAD", output, test_paths, timeout=20)
    assert report.baseline.status == "passed"
    assert report.status == "complete"
    assert report.source_matches_current
    assert report.functions[0].tests_by_line[2] == ["tests/test_shop.py::test_total"]
    assert report.functions[0].uncovered_lines == []
    assert [m.status for m in report.mutations] == [expected]
    mutation = report.mutations[0]
    assert mutation.control.status == "passed"
    assert mutation.selected_tests == ["tests/test_shop.py::test_total"]
    assert mutation.failing_tests == (mutation.selected_tests if strong else [])
    assert ["shop.py::checkout", "shop.py::total"] in report.functions[0].caller_paths
    assert ["tests/test_shop.py::test_total", "shop.py::checkout", "shop.py::total"] in (
        report.functions[0].caller_paths
    )
    assert (repo / "shop.py").read_bytes() == before
    assert git(repo, "diff") == patch_before
    assert not (repo / "runtime_state.txt").exists()
    saved = json.loads((output / "report.json").read_text())
    assert saved["mutations"][0]["status"] == expected
    assert Path(saved["mutations"][0]["run"]["log"]).is_file()
    html = (output / "report.html").read_text(encoding="utf-8")
    assert "No passing test attributed" not in html
    assert "Partial caller paths" in html


def test_baseline_failure_never_credits_mutations(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "tests/test_shop.py").write_text("def test_bad():\n    assert False\n")
    report = review(repo, "HEAD", repo / ".cartograph/review", ["tests"], timeout=20)
    assert report.status == "baseline_failed"
    assert report.baseline.status == "failed"
    assert all(m.status == "baseline_unavailable" and m.run is None for m in report.mutations)
    html = (repo / ".cartograph/review/report.html").read_text(encoding="utf-8")
    assert "Baseline needs attention" in html and "Your tests passed" not in html


def test_unexecuted_and_budget_exhaustion(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "shop.py").write_text((repo / "shop.py").read_text()
                                 + "\ndef unused(x):\n    return x + 1\n")
    report = review(repo, "HEAD", repo / ".cartograph/review", ["tests"],
                    max_mutations=0, timeout=20)
    assert {m.status for m in report.mutations} == {"budget_exhausted", "unexecuted"}
    assert all(m.run is None for m in report.mutations)
    assert report.status == "incomplete"


def test_git_diff_new_deleted_staged_and_module_changes(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "add", "shop.py")
    (repo / "new.py").write_text("def added(x):\n    return x + 1\n")
    (repo / "config.py").write_text("ENABLED = True\n")
    lines, patch, warnings = diff_lines(repo, "HEAD")
    assert lines["shop.py"] == {2}
    assert lines["new.py"] == {1, 2}
    assert "New file: new.py" in patch
    graph = index_repo(repo, use_cache=False)
    functions, notes = changed_functions(graph, lines)
    assert {f.symbol.name for f in functions} == {"total", "added"}
    assert any("outside an indexed function" in x for x in notes)
    assert len(candidates(repo, functions)) == 2
    (repo / "shop.py").unlink()
    _, _, warnings = diff_lines(repo, "HEAD")
    assert any("Deletion-only" in x for x in warnings)


def test_mutations_preserve_unicode_and_ignore_comments_strings_and_other_lines(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "shop.py").write_text(
        "# café + True\ndef total(x, discount=0):\n"
        "    text = '+ True'\n    return x >= discount\n",
        encoding="utf-8",
    )
    graph = index_repo(repo, use_cache=False)
    functions, _ = changed_functions(graph, {"shop.py": {1, 3, 4}})
    mutations = candidates(repo, functions)
    assert [(m.line, m.original, m.replacement) for m in mutations] == [(4, ">=", "<")]
    source = (repo / "shop.py").read_bytes()
    m = mutations[0]
    assert source[m.start_byte:m.end_byte] == b">="


@pytest.mark.parametrize("run,expected", [
    (RunEvidence(status="passed", tests={"t": "passed"}), "survived"),
    (RunEvidence(status="failed", tests={"t": "failed"}), "detected"),
    (RunEvidence(status="error", tests={"t": "failed"}), "error"),
    (RunEvidence(status="timeout"), "timeout"),
    (RunEvidence(status="passed", tests={"other": "passed"}), "error"),
    (RunEvidence(status="passed", tests={"t": "skipped", "other": "passed"}), "error"),
])
def test_process_failures_and_missing_tests_are_not_detection(run, expected):
    assert classify_mutation(run, ["t"]) == expected


def test_runner_timeout_and_collection_error(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "test_sleep.py").write_text("import time\ndef test_slow():\n    time.sleep(30)\n")
    run = run_pytest(repo, ["test_sleep.py"], tmp_path / "timeout", 2)
    assert run.status == "timeout"
    (repo / "test_bad.py").write_text("def invalid(:\n")
    run = run_pytest(repo, ["test_bad.py"], tmp_path / "collection", 20)
    assert run.status == "error"


def test_cli_and_html_escape_repository_content(tmp_path):
    repo = make_repo(tmp_path / "repo")
    target = repo / "shop.py"
    target.write_text(target.read_text() + "# <script>alert('x')</script>\n")
    result = CliRunner().invoke(cli, ["review", "--base", "HEAD", "--repo", str(repo),
                                      "--fail-on-survivor", "--timeout", "20"])
    assert result.exit_code == 1, result.output
    html = (repo / ".cartograph/review/report.html").read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    result = CliRunner().invoke(cli, ["review", "--base", "--not-a-ref", "--repo", str(repo)])
    assert result.exit_code != 0


def test_selected_test_control_must_pass_before_mutation(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "tests/test_shop.py").write_text(
        "from shop import checkout\nstate = []\n"
        "def test_first():\n    state.append(1)\n"
        "def test_second():\n    assert state\n    assert checkout(100, 0) == 100\n"
    )
    report = review(repo, "HEAD", repo / ".cartograph/review", ["tests"], timeout=20)
    assert report.baseline.status == "passed"
    assert report.mutations[0].status == "unstable_control"
    assert report.mutations[0].run is None


def test_reject_outside_test_paths_before_execution(tmp_path):
    repo = make_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="inside the repository"):
        review(repo, "HEAD", repo / ".cartograph/review", ["../"], timeout=20)
