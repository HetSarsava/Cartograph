"""Adjudicator agent: resolves a codemod ambiguity the Rewriter can't.

Single-shot, called on demand — only when `tools.codemod.apply_codemod`
returns one or more `CodemodAmbiguity` entries for a step. Adjudicator looks
at the specific ambiguity plus surrounding context and either picks one of
the reported candidate resolutions, decides to skip that particular edit, or
escalates to a human reviewer when it isn't confident either way.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cartograph.providers.base import Provider
from cartograph.tools.codemod import CodemodAmbiguity


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
    """
    # TODO: build an LLMMessage list from request.ambiguity and
    # request.surrounding_context and call
    # provider.complete_structured(messages, AdjudicatorResponse).
    del provider  # unused until the real call is wired up
    return AdjudicatorResponse(
        decision=AdjudicationDecision.ESCALATE_TO_HUMAN,
        chosen_candidate=None,
        reasoning="STUB: adjudicator not yet implemented; defaulting to escalation.",
    )
