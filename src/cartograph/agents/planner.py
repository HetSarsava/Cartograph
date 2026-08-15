"""Planner agent: turns a relevant symbol set into an ordered edit plan.

Single-shot call. Planner receives the task description and the symbols
Scout flagged as relevant, and produces an ordered list of steps — each
naming a symbol, what needs to change and why, and (when one applies) which
registered codemod recipe should handle it. The Rewriter agent executes this
plan one step at a time.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cartograph.providers.base import Provider


class PlanStep(BaseModel):
    """A single unit of work in the plan."""

    symbol_id: str = Field(description="The symbol this step primarily concerns.")
    action: str = Field(description="Plain-English description of the required change.")
    recipe_name: str | None = Field(
        default=None,
        description="Name of a registered tools.codemod recipe to use, if one applies.",
    )


class PlannerRequest(BaseModel):
    """Input to the Planner agent."""

    task_description: str = Field(description="The migration/refactor task, in plain English.")
    relevant_symbol_ids: list[str] = Field(description="Symbols Scout flagged as relevant.")


class PlannerResponse(BaseModel):
    """Planner's proposed ordered plan."""

    steps: list[PlanStep] = Field(description="Ordered edit steps for the Rewriter to execute.")
    summary: str = Field(description="Brief explanation of the overall approach.")


def run_planner(provider: Provider, request: PlannerRequest) -> PlannerResponse:
    """Ask Planner to turn `request.relevant_symbol_ids` into an ordered edit plan.

    Args:
        provider: The LLM provider to call.
        request: Task description plus the relevant symbol set from Scout.

    Returns:
        A `PlannerResponse` with an ordered list of `PlanStep`s.
    """
    # TODO: build an LLMMessage list from request and call
    # provider.complete_structured(messages, PlannerResponse). The prompt
    # should mention tools.codemod.list_recipes() so the model can name a
    # real recipe rather than inventing one.
    del provider  # unused until the real call is wired up
    return PlannerResponse(
        steps=[
            PlanStep(
                symbol_id=symbol_id,
                action="STUB: planner not yet implemented; no real action determined.",
                recipe_name=None,
            )
            for symbol_id in request.relevant_symbol_ids
        ],
        summary="STUB: planner not yet implemented.",
    )
