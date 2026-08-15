"""Tests for tools.test_runner.run_tests, against ad-hoc temp repos and the fixture repo."""

from pathlib import Path

from cartograph.tools.test_runner import TestOutcome, run_tests

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_run_tests_all_passing(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_one():\n    assert 1 + 1 == 2\n")

    result = run_tests(tmp_path)

    assert result.collection_error is None
    assert result.total == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.all_passed is True
    assert result.tests[0].outcome == TestOutcome.PASSED


def test_run_tests_with_a_failure(tmp_path):
    (tmp_path / "test_mixed.py").write_text(
        "def test_pass():\n    assert True\n\n\ndef test_fail():\n    assert 1 == 2\n"
    )

    result = run_tests(tmp_path)

    assert result.collection_error is None
    assert result.total == 2
    assert result.passed == 1
    assert result.failed == 1
    assert result.all_passed is False

    failing = next(t for t in result.tests if t.outcome == TestOutcome.FAILED)
    assert "test_fail" in failing.node_id
    assert failing.message is not None


def test_run_tests_scoped_to_a_single_file(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "test_b.py").write_text("def test_b():\n    assert False\n")

    result = run_tests(tmp_path, test_path="test_a.py")

    assert result.total == 1
    assert result.passed == 1


def test_run_tests_against_sample_repo_fixture():
    result = run_tests(FIXTURE_REPO)

    assert result.collection_error is None
    assert result.all_passed is True
    assert result.total >= 4
    assert result.failed == 0
    assert result.errors == 0
