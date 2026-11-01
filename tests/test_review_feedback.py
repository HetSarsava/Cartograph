from cartograph.graph.models import Symbol, SymbolKind
from cartograph.review.feedback import feedback_for
from cartograph.review.models import (
    ChangedFunction,
    Mutation,
    ReviewMetrics,
    ReviewReport,
    RunEvidence,
)


def make_report(**updates):
    values = {
        "repo": ".",
        "base_ref": "main",
        "base_commit": "base",
        "head_commit": "head",
        "source_digest": "digest",
        "created_utc": "2026-10-15T00:00:00Z",
    }
    values.update(updates)
    return ReviewReport(**values)


def test_baseline_failure_is_returned_as_actionable_repair_feedback():
    report = make_report(
        baseline=RunEvidence(
            status="failed",
            tests={"tests/test_shop.py::test_total": "failed"},
        )
    )
    feedback = feedback_for(
        report,
        session_id="session123",
        state="reviewed",
        round_number=0,
        repair_rounds_remaining=2,
        report_html="report.html",
        report_json="report.json",
        totals={"elapsed_seconds": 1.5, "pytest_processes": 1, "test_executions": 1},
        baseline_diagnostic="assert total(100, 20) == 80",
    )
    assert feedback.baseline_failures == ["tests/test_shop.py::test_total"]
    assert "assert total" in feedback.baseline_diagnostic
    assert feedback.findings[0].kind == "baseline_failure"
    assert "call this tool again" in feedback.next_step
    assert feedback.metrics.model_tokens is None


def test_coverage_and_survivor_findings_include_structural_context():
    function = ChangedFunction(
        symbol=Symbol(
            id="shop.py::total",
            name="total",
            qualified_name="total",
            kind=SymbolKind.FUNCTION,
            file_path="shop.py",
            line_start=1,
            line_end=3,
        ),
        changed_lines=[2, 3],
        caller_paths=[["shop.py::checkout", "shop.py::total"]],
        tests_by_line={2: ["tests/test_shop.py::test_zero_discount"], 3: []},
        uncovered_lines=[3],
    )
    report = make_report(
        baseline=RunEvidence(
            status="passed", tests={"tests/test_shop.py::test_zero_discount": "passed"}
        ),
        functions=[function],
        mutations=[
            Mutation(
                id="M001",
                symbol_id="shop.py::total",
                file="shop.py",
                line=2,
                start_byte=0,
                end_byte=1,
                original="-",
                replacement="+",
                status="survived",
                selected_tests=["tests/test_shop.py::test_zero_discount"],
            )
        ],
        metrics=ReviewMetrics(
            elapsed_seconds=2.25, pytest_processes=3, test_executions=3, mutation_attempts=1
        ),
    )
    feedback = feedback_for(
        report,
        session_id="session123",
        state="reviewed",
        round_number=1,
        repair_rounds_remaining=1,
        report_html="report.html",
        report_json="report.json",
        totals={
            "elapsed_seconds": 3.0,
            "pytest_processes": 4,
            "test_executions": 4,
            "mutation_attempts": 1,
        },
    )
    assert [item.kind for item in feedback.findings] == ["uncovered_lines", "surviving_mutation"]
    assert all(
        item.caller_paths == [["shop.py::checkout", "shop.py::total"]] for item in feedback.findings
    )
    assert "test-focused" in feedback.findings[1].suggested_action
    assert feedback.metrics.total_elapsed_seconds == 3.0
    assert feedback.metrics.repair_rounds_remaining == 1


def test_stale_review_tells_agent_to_wait_and_rerun_without_repairing():
    report = make_report(
        baseline=RunEvidence(status="passed", tests={"tests/test_shop.py::test_total": "passed"}),
        source_matches_current=False,
    )
    feedback = feedback_for(
        report,
        session_id="session123",
        state="reviewed",
        round_number=0,
        repair_rounds_remaining=2,
        report_html="report.html",
        report_json="report.json",
        totals={},
    )
    assert feedback.findings[0].summary.startswith("The checkout changed")
    assert "do not act on stale evidence" in feedback.next_step
