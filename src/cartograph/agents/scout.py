"""Scout agent: narrows a large candidate symbol set down to what's relevant.

Single-shot call. Scout is the first agent in the pipeline: given a task
description and every symbol name the indexer found (which can be large for
a real repo), it returns the subset it believes is actually relevant to the
task, so downstream agents (Planner, Rewriter) don't have to reason over the
whole repo.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cartograph.providers.base import Provider


class ScoutRequest(BaseModel):
    """Input to the Scout agent."""

    task_description: str = Field(description="The migration/refactor task, in plain English.")
    candidate_symbol_ids: list[str] = Field(
        description="Every symbol id the indexer found, for Scout to filter."
    )


class ScoutResponse(BaseModel):
    """Scout's judgment of which symbols matter for the task."""

    relevant_symbol_ids: list[str] = Field(
        description="Subset of candidate_symbol_ids Scout believes are relevant."
    )
    reasoning: str = Field(description="Brief explanation of the selection.")


def run_scout(provider: Provider, request: ScoutRequest) -> ScoutResponse:
    """Ask Scout to filter `request.candidate_symbol_ids` down to relevant ones.

    Args:
        provider: The LLM provider to call.
        request: Task description plus the full candidate symbol list.

    Returns:
        A `ScoutResponse` with the relevant subset and Scout's reasoning.
    """
    # TODO: build an LLMMessage list from request.task_description and
    # request.candidate_symbol_ids (likely batched/paginated for large repos)
    # and call provider.complete_structured(messages, ScoutResponse).
    del provider  # unused until the real call is wired up
    return ScoutResponse(
        relevant_symbol_ids=list(request.candidate_symbol_ids),
        reasoning="STUB: scout not yet implemented; returning all candidates unfiltered.",
    )
