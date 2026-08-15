"""Planner agent: turns a relevant symbol set into an ordered edit plan.

Single-shot call. Planner receives the task description and the symbols
Scout flagged as relevant, and produces an ordered list of steps — each
naming a symbol, what needs to change and why, and (when one applies) which
registered codemod recipe should handle it. The Rewriter agent executes this
plan one step at a time.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from cartograph.providers.base import LLMMessage, Provider
from cartograph.tools import recipes as _recipes  # noqa: F401 - registers built-in recipes
from cartograph.tools.codemod import list_recipes

logger = logging.getLogger(__name__)


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
        A `PlannerResponse` with an ordered list of `PlanStep`s. Steps whose
        `symbol_id` isn't actually in `request.relevant_symbol_ids`, or whose
        `recipe_name` isn't a registered recipe, are dropped/cleared (and
        logged) rather than trusted, since the model can hallucinate either.
    """
    available_recipes = list_recipes()
    logger.debug(
        "[planner] request task=%r relevant_count=%d available_recipes=%s",
        request.task_description,
        len(request.relevant_symbol_ids),
        available_recipes,
    )

    if not request.relevant_symbol_ids:
        response = PlannerResponse(steps=[], summary="No relevant symbols; nothing to plan.")
        logger.debug("[planner] response steps=0 summary=%r", response.summary)
        return response

    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are Planner, the second agent in a code-migration pipeline. You are "
                "given a task description and a list of symbol ids Scout flagged as "
                "relevant. Produce an ordered list of steps, one per symbol (skip symbols "
                "that don't actually need a change). For each step give: symbol_id (must be "
                "exactly one of the given ids), action (a short plain-English description of "
                "the required change), and recipe_name — set it to one of these registered "
                f"codemod recipe names if it applies: {available_recipes}, or null if none "
                "applies and the step needs a bespoke edit."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Task: {request.task_description}\n\n"
                "Relevant symbol ids:\n" + "\n".join(request.relevant_symbol_ids)
            ),
        ),
    ]

    raw_response = provider.complete_structured(messages, PlannerResponse)

    relevant_set = set(request.relevant_symbol_ids)
    recipe_set = set(available_recipes)
    steps: list[PlanStep] = []
    for step in raw_response.steps:
        if step.symbol_id not in relevant_set:
            logger.warning("[planner] dropped step for hallucinated symbol id: %s", step.symbol_id)
            continue
        if step.recipe_name is not None and step.recipe_name not in recipe_set:
            logger.warning(
                "[planner] step for %s named unknown recipe %r; clearing to None",
                step.symbol_id,
                step.recipe_name,
            )
            step = step.model_copy(update={"recipe_name": None})
        steps.append(step)

    response = PlannerResponse(steps=steps, summary=raw_response.summary)
    logger.debug(
        "[planner] response step_count=%d summary=%r steps=%s",
        len(response.steps),
        response.summary,
        [(s.symbol_id, s.recipe_name) for s in response.steps],
    )
    return response
