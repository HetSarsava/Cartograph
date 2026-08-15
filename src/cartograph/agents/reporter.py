"""Reporter agent: summarizes a completed pipeline run for a human reader.

Single-shot, called once at the end of a run. Reporter receives the plan,
what the Rewriter did for each step (including retries and any adjudication
decisions), and the final test outcome, and produces a human-readable
Markdown report — what changed, what didn't, and why.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cartograph.providers.base import Provider


class ReporterRequest(BaseModel):
    """Input to the Reporter agent."""

    task_description: str = Field(description="The overall migration/refactor task.")
    step_summaries: list[str] = Field(
        description="One short summary per plan step (from Rewriter/Adjudicator outcomes)."
    )
    tests_passed: bool = Field(description="Whether the final test run was green.")
    notes: list[str] = Field(
        default_factory=list, description="Any additional notable events (retries, escalations)."
    )


class ReporterResponse(BaseModel):
    """Reporter's final output."""

    report_markdown: str = Field(description="Human-readable Markdown summary of the run.")


def run_reporter(provider: Provider, request: ReporterRequest) -> ReporterResponse:
    """Ask Reporter to summarize a completed run described by `request`.

    Args:
        provider: The LLM provider to call.
        request: Task description, per-step summaries, and final test status.

    Returns:
        A `ReporterResponse` containing the Markdown report.
    """
    # TODO: build an LLMMessage list from request and call
    # provider.complete_text(...) (free-form Markdown, no schema needed).
    del provider  # unused until the real call is wired up
    status = "PASSED" if request.tests_passed else "FAILED"
    return ReporterResponse(
        report_markdown=(
            f"# Run Report (STUB)\n\n"
            f"**Task:** {request.task_description}\n\n"
            f"**Final tests:** {status}\n\n"
            f"**Steps:** {len(request.step_summaries)}\n\n"
            "_Reporter agent not yet implemented; this is a placeholder summary._"
        )
    )
