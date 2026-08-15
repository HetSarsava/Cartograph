"""Rewriter agent: executes a single plan step, iterating against test feedback.

Multi-turn loop (unlike Scout/Planner/Adjudicator/Reporter, which are
single-shot). For one `PlanStep`, Rewriter is expected to: attempt an edit
(via `tools.codemod.apply_codemod` or a bespoke libcst edit for steps with no
matching recipe), run `tools.test_runner.run_tests`, and if tests fail, feed
the failure back to the model for another attempt — up to `max_turns` — before
giving up and reporting failure.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cartograph.agents.planner import PlanStep
from cartograph.providers.base import Provider


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


def run_rewriter(provider: Provider, request: RewriterRequest) -> RewriterResponse:
    """Execute `request.plan_step`, retrying against test feedback up to `max_turns`.

    Args:
        provider: The LLM provider to call for edit generation/repair.
        request: The plan step to execute, its repo, and the retry budget.

    Returns:
        A `RewriterResponse` describing every attempt and the final outcome.
    """
    # TODO: implement the loop:
    #   for turn in range(1, request.max_turns + 1):
    #       - if plan_step.recipe_name: tools.codemod.apply_codemod(...)
    #         else: ask the model (via provider.complete_structured) for a
    #         bespoke libcst edit description/diff.
    #       - tools.test_runner.run_tests(request.repo_path)
    #       - if tests pass: return success=True with attempts so far.
    #       - else: feed the failure summary back into the next turn's prompt.
    #   After max_turns with no success, return success=False and consider
    #   calling tools.snapshot.rollback to undo partial edits.
    del provider  # unused until the real call is wired up
    return RewriterResponse(
        success=False,
        attempts=[
            RewriteAttempt(
                turn=1,
                description="STUB: rewriter not yet implemented; no edit attempted.",
            )
        ],
        final_summary="STUB: rewriter not yet implemented.",
    )
