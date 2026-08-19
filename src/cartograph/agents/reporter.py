"""Reporter agent: summarizes a completed pipeline run for a human reader.

Single-shot, called once at the end of a run. Reporter receives the plan,
what the Rewriter did for each step (including retries and any adjudication
decisions), and the final test outcome, and produces a human-readable
Markdown report — what changed, what didn't, and why.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from cartograph.providers.base import LLMMessage, Provider

logger = logging.getLogger(__name__)


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
    logger.debug(
        "[reporter] request task=%r step_count=%d tests_passed=%s notes=%s",
        request.task_description,
        len(request.step_summaries),
        request.tests_passed,
        request.notes,
    )

    status = "GREEN" if request.tests_passed else "BROKEN"
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are Reporter, the final agent in a code-migration pipeline. Write a "
                "concise Markdown report of a completed run for a human reader: what changed, "
                "what didn't, and why. Use headings and bullet points. Do not invent details "
                "not present in the input. A QUARANTINED target is a success, not a "
                "failure: it means the codemod found genuine ambiguity and correctly "
                "declined to guess, leaving the file untouched for a human. Never call "
                "the run failed because something was quarantined."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Task: {request.task_description}\n"
                f"Repository test status after the run: {status}\n\n"
                "Step summaries:\n" + ("\n".join(f"- {s}" for s in request.step_summaries) or "(none)")
                + "\n\nNotes:\n"
                + ("\n".join(f"- {n}" for n in request.notes) or "(none)")
            ),
        ),
    ]

    report_markdown = provider.complete_text(messages)
    response = ReporterResponse(report_markdown=report_markdown)
    logger.debug("[reporter] response length=%d chars", len(response.report_markdown))
    return response
