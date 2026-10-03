"""Rewriter agent: executes a single plan step, iterating against test feedback.

Multi-turn loop (unlike Scout/Planner/Adjudicator/Reporter, which are
single-shot). For one `PlanStep`, Rewriter: snapshots the repo, applies the
step's codemod recipe (scoped to that step's own file), and runs the test
oracle. If `apply_codemod` reports ambiguity, Rewriter stops immediately and
surfaces it via `RewriterResponse.ambiguities` — that's the pipeline's job to
route to Adjudicator, not Rewriter's to guess at.

If the edit applies cleanly but tests then fail, there is no second
*deterministic* action to take (re-running the same recipe against the same
file produces the same result), so the second turn is a genuine LLM call
that diagnoses the failure from the test output — not a blind retry of an
identical tool call — after which the edit is rolled back via
`tools.snapshot.rollback` so a failed step never leaves the repo dirty for
the next one.

Steps with no matching recipe (`plan_step.recipe_name` is `None`) are
reported as unactionable: a bespoke, free-form LLM-authored libcst edit is
out of scope for this pass (only recipe-driven, deterministic edits are
executable right now).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from cartograph.agents.planner import PlanStep
from cartograph.config import get_settings
from cartograph.providers.base import LLMMessage, Provider
from cartograph.tools import recipes as _recipes  # noqa: F401 - registers built-in recipes
from cartograph.tools.codemod import CodemodAmbiguity, apply_codemod, get_recipe
from cartograph.tools.snapshot import rollback, snapshot
from cartograph.tools.test_runner import TestOutcome, TestRunResult, run_tests

logger = logging.getLogger(__name__)


class RewriteAttempt(BaseModel):
    """A single turn of the Rewriter's edit/test loop."""

    turn: int = Field(ge=1)
    description: str = Field(description="What was attempted this turn.")
    files_changed: list[str] = Field(default_factory=list)
    tests_passed: bool | None = Field(
        default=None, description="None if tests weren't run this turn."
    )
    test_summary: str | None = Field(default=None, description="Short summary of test output.")


class RewriterRequest(BaseModel):
    """Input to the Rewriter agent."""

    task_description: str = Field(description="The overall migration/refactor task.")
    plan_step: PlanStep = Field(description="The single step being executed.")
    repo_path: str = Field(description="Root of the repo being edited.")
    max_turns: int = Field(default=3, ge=1, description="Cap on edit/test retry attempts.")


class RewriterResponse(BaseModel):
    """The outcome of executing one `PlanStep`, across all attempts made."""

    success: bool = Field(description="True if the step's tests passed by the final attempt.")
    attempts: list[RewriteAttempt] = Field(default_factory=list)
    final_summary: str = Field(description="Human-readable summary of the outcome.")
    ambiguities: list[CodemodAmbiguity] = Field(
        default_factory=list,
        description=(
            "Populated when apply_codemod reported ambiguities for this step's file. "
            "Non-empty here means the pipeline should route to Adjudicator next, not "
            "retry Rewriter."
        ),
    )


class _FailureDiagnosis(BaseModel):
    """Internal only: Rewriter's read on why tests failed after a clean edit."""

    root_cause: str = Field(description="Best guess at why the tests are failing.")
    related_to_this_change: bool = Field(
        description="Whether the failure looks caused by this step's edit vs. pre-existing."
    )


def run_rewriter(provider: Provider, request: RewriterRequest) -> RewriterResponse:
    """Execute `request.plan_step`: snapshot, apply its recipe, run tests, roll back on failure.

    Args:
        provider: The LLM provider to call for failure diagnosis.
        request: The plan step to execute, its repo, and the retry budget.

    Returns:
        A `RewriterResponse` describing every attempt and the final outcome.
        `ambiguities` is non-empty exactly when `apply_codemod` reported some
        for this step's file — in that case `success` is always `False` and
        no edit was written.
    """
    step = request.plan_step

    if not step.recipe_name:
        logger.info("[rewriter] step %s has no recipe_name; nothing executable this pass", step.symbol_id)
        attempt = RewriteAttempt(
            turn=1,
            description=(
                "No codemod recipe specified for this step; bespoke LLM-driven edits are "
                "out of scope for this pass."
            ),
        )
        return RewriterResponse(
            success=False, attempts=[attempt], final_summary="No recipe specified; nothing to apply."
        )

    recipe = get_recipe(step.recipe_name)
    if recipe is None:
        logger.warning("[rewriter] step %s named unknown recipe %r", step.symbol_id, step.recipe_name)
        attempt = RewriteAttempt(turn=1, description=f"Recipe {step.recipe_name!r} is not registered.")
        return RewriterResponse(
            success=False, attempts=[attempt], final_summary=f"Unknown recipe {step.recipe_name!r}."
        )

    target_file = step.symbol_id.split("::", 1)[0]

    logger.debug(
        "[rewriter] request symbol_id=%s recipe=%s target_file=%s repo_path=%s max_turns=%d",
        step.symbol_id,
        step.recipe_name,
        target_file,
        request.repo_path,
        request.max_turns,
    )

    snap = snapshot(request.repo_path, label=f"before {step.recipe_name} on {target_file}")
    logger.info(
        "[tool:snapshot] taken id=%s label=%r files_captured=%d (safety net before editing %s)",
        snap.snapshot_id,
        snap.label,
        snap.files_captured,
        target_file,
    )

    try:
        codemod_result = apply_codemod(request.repo_path, step.recipe_name, target_files=[target_file])
    except Exception:
        rollback(request.repo_path, snap.snapshot_id)
        raise
    logger.info(
        "[tool:apply_codemod] recipe=%s target=%s applied=%s files_changed=%s ambiguity_count=%d",
        step.recipe_name,
        target_file,
        codemod_result.applied,
        codemod_result.files_changed,
        len(codemod_result.ambiguities),
    )

    if codemod_result.ambiguities:
        attempt = RewriteAttempt(
            turn=1,
            description=(
                f"apply_codemod reported {len(codemod_result.ambiguities)} ambiguity(ies) in "
                f"{target_file}; routing to Adjudicator."
            ),
        )
        return RewriterResponse(
            success=False,
            attempts=[attempt],
            final_summary="Codemod produced ambiguous results; routed to Adjudicator.",
            ambiguities=codemod_result.ambiguities,
        )

    if not codemod_result.applied:
        attempt = RewriteAttempt(
            turn=1,
            description=f"Recipe {step.recipe_name!r} made no changes to {target_file} (nothing to convert).",
        )
        return RewriterResponse(
            success=True, attempts=[attempt], final_summary="Nothing to change; file already compliant."
        )

    try:
        test_result = run_tests(
            request.repo_path, timeout_seconds=get_settings().test_timeout_seconds
        )
    except Exception:
        rollback(request.repo_path, snap.snapshot_id)
        raise
    logger.info(
        "[gate:tests] target=%s passed=%d failed=%d errors=%d total=%d all_passed=%s",
        target_file,
        test_result.passed,
        test_result.failed,
        test_result.errors,
        test_result.total,
        test_result.all_passed,
    )

    attempts = [
        RewriteAttempt(
            turn=1,
            description=f"Applied recipe {step.recipe_name!r} to {target_file}.",
            files_changed=codemod_result.files_changed,
            tests_passed=test_result.all_passed,
            test_summary=_summarize_tests(test_result),
        )
    ]

    if test_result.all_passed:
        return RewriterResponse(
            success=True,
            attempts=attempts,
            final_summary=f"Applied {step.recipe_name!r} to {target_file}; tests passed.",
        )

    # Tests failed after a deterministic, ambiguity-free edit: re-running the
    # same recipe against the same file would reproduce the same result, so
    # there's no productive automated retry — the second turn is a genuine
    # LLM diagnosis of the failure instead of a pointless repeat tool call.
    rollback_result = rollback(request.repo_path, snap.snapshot_id)
    logger.info(
        "[tool:rollback] snapshot=%s files_restored=%d files_removed=%d "
        "reason='tests failed after applying %s to %s'",
        snap.snapshot_id,
        rollback_result.files_restored,
        rollback_result.files_removed,
        step.recipe_name,
        target_file,
    )
    attempts.append(
        RewriteAttempt(
            turn=len(attempts) + 1,
            description=f"Rolled back to pre-edit snapshot {snap.snapshot_id} because tests failed.",
        )
    )

    diagnosis: _FailureDiagnosis | None = None
    if request.max_turns >= 2:
        diagnosis = _diagnose_failure(provider, request, target_file, test_result)
        attempts.append(RewriteAttempt(turn=len(attempts) + 1, description=f"Diagnosis: {diagnosis.root_cause}"))

    final_summary = (
        f"Tests failed after applying {step.recipe_name!r} to {target_file}; changes were rolled back."
    )
    if diagnosis is not None:
        final_summary += f" Diagnosis: {diagnosis.root_cause}"

    return RewriterResponse(success=False, attempts=attempts, final_summary=final_summary)


def _diagnose_failure(
    provider: Provider, request: RewriterRequest, target_file: str, test_result: TestRunResult
) -> _FailureDiagnosis:
    logger.debug("[rewriter] diagnosing test failure for %s", target_file)
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are Rewriter's failure-diagnosis step. A deterministic codemod recipe "
                "was applied to a file and its tests then failed. You cannot make further "
                "automated edits this turn — just diagnose, briefly, what's likely wrong and "
                "whether it looks related to the edit that was just made."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Task: {request.task_description}\n"
                f"File edited: {target_file}\n"
                f"Recipe applied: {request.plan_step.recipe_name}\n\n"
                f"Test results: {_summarize_tests(test_result)}"
            ),
        ),
    ]
    diagnosis = provider.complete_structured(messages, _FailureDiagnosis)
    logger.debug(
        "[rewriter] diagnosis root_cause=%r related_to_this_change=%s",
        diagnosis.root_cause,
        diagnosis.related_to_this_change,
    )
    return diagnosis


def _summarize_tests(result: TestRunResult) -> str:
    if result.collection_error:
        return f"collection error: {result.collection_error[:300]}"
    if result.all_passed:
        return f"{result.passed}/{result.total} passed"
    failing = [t.node_id for t in result.tests if t.outcome != TestOutcome.PASSED]
    return f"{result.passed}/{result.total} passed; failing: {failing[:10]}"
