"""Scout agent: narrows a large candidate symbol set down to what's relevant.

Single-shot call. Scout is the first agent in the pipeline: given a task
description and every symbol name the indexer found (which can be large for
a real repo), it returns the subset it believes is actually relevant to the
task, so downstream agents (Planner, Rewriter) don't have to reason over the
whole repo.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from cartograph.providers.base import LLMMessage, Provider

logger = logging.getLogger(__name__)


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
        Any symbol id the model returns that isn't actually in
        `request.candidate_symbol_ids` is dropped (and logged) rather than
        trusted, since the model can hallucinate or slightly mangle ids.
    """
    logger.debug(
        "[scout] request task=%r candidate_count=%d candidates=%s",
        request.task_description,
        len(request.candidate_symbol_ids),
        request.candidate_symbol_ids,
    )

    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are Scout, the first agent in a code-migration pipeline. You are given "
                "a task description and a list of candidate symbol ids (format: "
                "'{file_path}::{qualified_name}'). Return only the subset of "
                "candidate_symbol_ids that are relevant to the task — do not invent new ids, "
                "only choose from the given list. If none are relevant, return an empty list."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Task: {request.task_description}\n\n"
                "Candidate symbol ids:\n" + "\n".join(request.candidate_symbol_ids)
            ),
        ),
    ]

    raw_response = provider.complete_structured(messages, ScoutResponse)

    candidate_set = set(request.candidate_symbol_ids)
    valid_ids = [sid for sid in raw_response.relevant_symbol_ids if sid in candidate_set]
    dropped = [sid for sid in raw_response.relevant_symbol_ids if sid not in candidate_set]
    if dropped:
        logger.warning("[scout] dropped %d hallucinated symbol id(s): %s", len(dropped), dropped)

    response = ScoutResponse(relevant_symbol_ids=valid_ids, reasoning=raw_response.reasoning)
    logger.debug(
        "[scout] response relevant_count=%d relevant=%s reasoning=%r",
        len(response.relevant_symbol_ids),
        response.relevant_symbol_ids,
        response.reasoning,
    )
    return response
