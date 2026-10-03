"""Runs pytest and returns structured, per-test results instead of raw stdout.

This is the pipeline's test oracle: agents should never have to scrape
pytest's terminal output to decide "did it pass". `run_tests()` shells out to
`pytest --json-report` (via the `pytest-json-report` plugin) and parses the
resulting JSON into Pydantic models.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

_DEFAULT_TIMEOUT_SECONDS = 120


class TestOutcome(str, Enum):
    """Per-test outcome, as reported by pytest-json-report."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class TestCaseResult(BaseModel):
    """The result of a single test case."""

    node_id: str = Field(description="Pytest node id, e.g. 'tests/test_foo.py::test_bar'.")
    outcome: TestOutcome
    duration_seconds: float = Field(ge=0.0)
    message: str | None = Field(
        default=None, description="Failure/error summary (longrepr), if outcome != passed."
    )


class TestRunResult(BaseModel):
    """The structured outcome of a full `run_tests()` invocation."""

    command: list[str] = Field(description="The exact command that was run.")
    return_code: int
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    tests: list[TestCaseResult] = Field(default_factory=list)
    collection_error: str | None = Field(
        default=None,
        description=(
            "Set when pytest could not produce a JSON report at all (crash, "
            "timeout, missing plugin, collection failure) — in that case "
            "the count fields are all 0 and `tests` is empty."
        ),
    )

    @property
    def all_passed(self) -> bool:
        """True if at least one test ran and none failed or errored."""
        return (
            self.return_code == 0 and self.collection_error is None
            and self.passed > 0 and self.failed == 0 and self.errors == 0
        )


def run_tests(
    repo_path: str | Path,
    test_path: str = ".",
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> TestRunResult:
    """Run pytest against `test_path` (relative to `repo_path`) and return structured results.

    Args:
        repo_path: Root directory to run pytest from (its cwd for the subprocess).
        test_path: Path or node id to test, relative to `repo_path`. Defaults
            to the whole repo.
        timeout_seconds: Kill the pytest subprocess if it runs longer than this.

    Returns:
        A `TestRunResult`. Check `collection_error` before trusting the count
        fields — a `None` value there means pytest ran to completion and
        produced a real report (tests may still have failed; that's normal).
    """
    root = Path(repo_path).resolve()

    with tempfile.TemporaryDirectory(prefix="cartograph-testrun-") as tmp_dir:
        report_path = Path(tmp_dir) / "report.json"
        command = [
            sys.executable,
            "-m",
            "pytest",
            test_path,
            "--json-report",
            f"--json-report-file={report_path}",
            "-q",
        ]

        try:
            proc = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(
                command=command,
                return_code=-1,
                total=0,
                passed=0,
                failed=0,
                errors=0,
                skipped=0,
                duration_seconds=float(timeout_seconds),
                collection_error=f"pytest timed out after {timeout_seconds}s",
            )

        if not report_path.exists():
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
            return TestRunResult(
                command=command,
                return_code=proc.returncode,
                total=0,
                passed=0,
                failed=0,
                errors=0,
                skipped=0,
                duration_seconds=0.0,
                collection_error=tail or "pytest produced no JSON report and no output",
            )

        report = json.loads(report_path.read_text())

    return _to_result(command, proc.returncode, report)


def _to_result(command: list[str], return_code: int, report: dict) -> TestRunResult:
    summary = report.get("summary", {})
    tests = [_to_test_case(t) for t in report.get("tests", [])]
    return TestRunResult(
        command=command,
        return_code=return_code,
        total=summary.get("total", len(tests)),
        passed=summary.get("passed", 0),
        failed=summary.get("failed", 0),
        errors=summary.get("error", 0),
        skipped=summary.get("skipped", 0),
        duration_seconds=report.get("duration", 0.0),
        tests=tests,
    )


def _to_test_case(test: dict) -> TestCaseResult:
    outcome_raw = test.get("outcome", "error")
    try:
        outcome = TestOutcome(outcome_raw)
    except ValueError:
        outcome = TestOutcome.ERROR

    message = None
    if outcome != TestOutcome.PASSED:
        for phase in ("call", "setup", "teardown"):
            phase_data = test.get(phase)
            if isinstance(phase_data, dict) and "longrepr" in phase_data:
                message = str(phase_data["longrepr"])
                break

    return TestCaseResult(
        node_id=test.get("nodeid", "<unknown>"),
        outcome=outcome,
        duration_seconds=test.get("duration", 0.0),
        message=message,
    )
