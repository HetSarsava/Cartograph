"""Adjudicator agent: resolves a codemod ambiguity the Rewriter can't.

Single-shot, called on demand — only when `tools.codemod.apply_codemod`
returns one or more `CodemodAmbiguity` entries for a step. Adjudicator looks
at the specific ambiguity plus surrounding context and either picks one of
the reported candidate resolutions, decides to skip that particular edit, or
escalates to a human reviewer when it isn't confident either way.
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from cartograph.providers.base import LLMMessage, Provider
from cartograph.tools.codemod import CodemodAmbiguity

logger = logging.getLogger(__name__)


class AdjudicationDecision(str, Enum):
    """What Adjudicator decided to do about an ambiguity."""

    APPLY_CANDIDATE = "apply_candidate"
    SKIP = "skip"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class AdjudicatorRequest(BaseModel):
    """Input to the Adjudicator agent."""

    ambiguity: CodemodAmbiguity = Field(description="The ambiguity reported by a codemod recipe.")
    surrounding_context: str = Field(
        description="Source snippet or other context around the ambiguous construct."
    )


class AdjudicatorResponse(BaseModel):
    """Adjudicator's decision."""

    decision: AdjudicationDecision
    chosen_candidate: str | None = Field(
        default=None, description="Set when decision == APPLY_CANDIDATE."
    )
    reasoning: str = Field(description="Brief explanation of the decision.")


def run_adjudicator(provider: Provider, request: AdjudicatorRequest) -> AdjudicatorResponse:
    """Ask Adjudicator to resolve `request.ambiguity`.

    Args:
        provider: The LLM provider to call.
        request: The ambiguity and surrounding context to reason about.

    Returns:
        An `AdjudicatorResponse` with a decision and reasoning. The safe
        default when uncertain is `ESCALATE_TO_HUMAN`, never a silent guess.
        If the model picks `APPLY_CANDIDATE` with a `chosen_candidate` that
        isn't actually one of `request.ambiguity.candidates`, the decision
        is downgraded to `ESCALATE_TO_HUMAN` rather than trusted.
    """
    logger.debug(
        "[adjudicator] request file=%s line=%s description=%r candidates=%s",
        request.ambiguity.file_path,
        request.ambiguity.line,
        request.ambiguity.description,
        request.ambiguity.candidates,
    )

    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are Adjudicator, called only when a deterministic codemod recipe hit an "
                "ambiguous construct it refused to guess at. Decide: APPLY_CANDIDATE (only if "
                "one of the given candidates is clearly correct — set chosen_candidate to it "
                "exactly), SKIP (leave the code as-is, it's fine unconverted), or "
                "ESCALATE_TO_HUMAN (you're not confident either way — this is the safe default)."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"File: {request.ambiguity.file_path}\n"
                f"Line: {request.ambiguity.line}\n"
                f"Ambiguity: {request.ambiguity.description}\n"
                f"Known candidate resolutions: {request.ambiguity.candidates or '(none given)'}\n\n"
                f"Surrounding context:\n{request.surrounding_context or '(none provided)'}"
            ),
        ),
    ]

    response = provider.complete_structured(messages, AdjudicatorResponse)

    if response.decision == AdjudicationDecision.APPLY_CANDIDATE and (
        not response.chosen_candidate or response.chosen_candidate not in request.ambiguity.candidates
    ):
        logger.warning(
            "[adjudicator] APPLY_CANDIDATE with invalid/unlisted chosen_candidate=%r "
            "(known candidates=%s); downgrading to ESCALATE_TO_HUMAN",
            response.chosen_candidate,
            request.ambiguity.candidates,
        )
        response = AdjudicatorResponse(
            decision=AdjudicationDecision.ESCALATE_TO_HUMAN,
            chosen_candidate=None,
            reasoning=(
                f"Model chose APPLY_CANDIDATE with an invalid candidate; escalated instead. "
                f"Original reasoning: {response.reasoning}"
            ),
        )

    logger.debug(
        "[adjudicator] response decision=%s chosen_candidate=%r reasoning=%r",
        response.decision,
        response.chosen_candidate,
        response.reasoning,
    )
    return response
