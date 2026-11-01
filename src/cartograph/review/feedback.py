"""Compact, actionable agent feedback derived from a full review report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cartograph.review.models import ReviewReport


class ReviewFinding(BaseModel):
    kind: Literal["baseline_failure", "uncovered_lines", "surviving_mutation", "inconclusive"]
    file: str
    line: int | None = None
    function_id: str | None = None
    summary: str
    tests: list[str] = Field(default_factory=list)
    caller_paths: list[list[str]] = Field(default_factory=list)
    suggested_action: str


class ReviewLoopMetrics(BaseModel):
    round_number: int
    repair_rounds_remaining: int
    elapsed_seconds: float
    pytest_processes: int
    test_executions: int
    mutation_attempts: int
    total_elapsed_seconds: float
    total_pytest_processes: int
    total_test_executions: int
    total_mutation_attempts: int
    model_tokens: int | None = None


class ReviewFeedback(BaseModel):
    session_id: str
    state: Literal["reviewed", "no_source_changes", "repair_limit_reached"]
    review_status: str
    baseline_status: str
    baseline_failures: list[str] = Field(default_factory=list)
    baseline_diagnostic: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    findings_truncated: bool = False
    source_matches_current: bool | None = None
    report_html: str
    report_json: str
    metrics: ReviewLoopMetrics
    next_step: str


def findings_for(report: ReviewReport, *, limit: int = 30) -> tuple[list[ReviewFinding], bool]:
    """Return compact findings without source bodies, raw logs, or unrelated tests."""
    findings: list[ReviewFinding] = []
    if report.baseline is not None and report.baseline.status != "passed":
        failing = sorted(
            name for name, status in report.baseline.tests.items() if status in ("failed", "error")
        )
        findings.append(
            ReviewFinding(
                kind="baseline_failure",
                file="",
                summary="The ordinary test baseline did not pass.",
                tests=failing,
                suggested_action=(
                    "Inspect the baseline diagnostic, fix the failing patch or tests, "
                    "then request another review. Mutation results are unavailable."
                ),
            )
        )
    else:
        functions = {item.symbol.id: item for item in report.functions}
        for function in report.functions:
            for line in function.uncovered_lines:
                findings.append(
                    ReviewFinding(
                        kind="uncovered_lines",
                        file=function.symbol.file_path,
                        line=line,
                        function_id=function.symbol.id,
                        summary="No passing baseline test was observed executing this changed line.",
                        caller_paths=function.caller_paths[:5],
                        suggested_action=(
                            "Add or select a test that exercises the changed behavior; "
                            "do not treat coverage as proof of an assertion."
                        ),
                    )
                )
        for mutation in report.mutations:
            changed_function = functions.get(mutation.symbol_id)
            if mutation.status == "survived":
                findings.append(
                    ReviewFinding(
                        kind="surviving_mutation",
                        file=mutation.file,
                        line=mutation.line,
                        function_id=mutation.symbol_id,
                        summary=(
                            f"The tests passed after {mutation.original!r} was changed to "
                            f"{mutation.replacement!r}. This may be equivalent or insufficiently tested."
                        ),
                        tests=mutation.selected_tests,
                        caller_paths=changed_function.caller_paths[:5] if changed_function else [],
                        suggested_action=(
                            "Review the intended behavior and strengthen tests with an "
                            "assertion or input that distinguishes it. Keep this repair "
                            "test-focused; a survivor alone does not prove a code bug."
                        ),
                    )
                )
            elif mutation.status in {
                "error",
                "timeout",
                "unstable_control",
                "budget_exhausted",
                "baseline_unavailable",
            }:
                findings.append(
                    ReviewFinding(
                        kind="inconclusive",
                        file=mutation.file,
                        line=mutation.line,
                        function_id=mutation.symbol_id,
                        summary=f"Mutation evidence is inconclusive: {mutation.status}.",
                        tests=mutation.selected_tests,
                        caller_paths=changed_function.caller_paths[:5] if changed_function else [],
                        suggested_action=(
                            "Inspect this case manually. Do not count it as either "
                            "detected or survived."
                        ),
                    )
                )
    if report.source_matches_current is False:
        findings.insert(
            0,
            ReviewFinding(
                kind="inconclusive",
                file="",
                summary="The checkout changed while this review was running; its evidence is stale.",
                suggested_action=(
                    "Wait until editing stops, then request another review with the "
                    "same session_id. Do not repair code based on this stale report."
                ),
            ),
        )
    return findings[:limit], len(findings) > limit


def feedback_for(
    report: ReviewReport,
    *,
    session_id: str,
    state: Literal["reviewed", "no_source_changes", "repair_limit_reached"],
    round_number: int,
    repair_rounds_remaining: int,
    report_html: str,
    report_json: str,
    totals: dict[str, float | int],
    baseline_diagnostic: str = "",
) -> ReviewFeedback:
    findings, truncated = findings_for(report)
    baseline = report.baseline
    failures = sorted(
        name
        for name, status in (baseline.tests.items() if baseline else [])
        if status in ("failed", "error")
    )
    if state == "no_source_changes":
        next_step = (
            "No source changed since the previous review. Stop and inspect the existing report."
        )
    elif state == "repair_limit_reached":
        next_step = "The repair-round limit is exhausted. Stop automation and ask for human review."
    elif report.source_matches_current is False:
        next_step = (
            "The checkout changed during review. Wait for edits to stop and call this tool "
            "again with the same session_id; do not act on stale evidence."
        )
    elif baseline is not None and baseline.status != "passed":
        next_step = (
            "Use the baseline failures to repair the patch or tests, then call this tool "
            "again with the same session_id. The review loop is bounded to two repair rounds."
        )
    elif findings:
        next_step = (
            "Review the findings, make a focused test-only repair for uncovered lines or "
            "survivors, then call this tool again with the same session_id. Stop after the "
            "remaining repair rounds; report inconclusive findings to the developer."
        )
    else:
        next_step = "No actionable coverage or mutation gaps were found in the checked scope. Report the limits."
    metrics = report.metrics
    total_elapsed = float(totals.get("elapsed_seconds", 0))
    return ReviewFeedback(
        session_id=session_id,
        state=state,
        review_status=report.status,
        baseline_status=baseline.status if baseline else "unavailable",
        baseline_failures=failures,
        baseline_diagnostic=baseline_diagnostic[-5000:],
        findings=findings,
        findings_truncated=truncated,
        source_matches_current=report.source_matches_current,
        report_html=report_html,
        report_json=report_json,
        metrics=ReviewLoopMetrics(
            round_number=round_number,
            repair_rounds_remaining=repair_rounds_remaining,
            elapsed_seconds=metrics.elapsed_seconds,
            pytest_processes=metrics.pytest_processes,
            test_executions=metrics.test_executions,
            mutation_attempts=metrics.mutation_attempts,
            total_elapsed_seconds=round(total_elapsed, 3),
            total_pytest_processes=int(totals.get("pytest_processes", 0)),
            total_test_executions=int(totals.get("test_executions", 0)),
            total_mutation_attempts=int(totals.get("mutation_attempts", 0)),
            model_tokens=None,
        ),
        next_step=next_step,
    )
