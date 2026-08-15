"""LangGraph state schema for a Cartograph migration run.

`RunState` is a `TypedDict`, per LangGraph convention — node functions
receive it (or a subset of its keys) and return a partial dict that gets
merged into it. `total=False` because a freshly started run only has
`repo_path` and `task_description` populated; everything else fills in as
the graph executes.
"""

from __future__ import annotations

from typing import TypedDict

from cartograph.agents.adjudicator import AdjudicatorResponse
from cartograph.agents.planner import PlanStep, PlannerResponse
from cartograph.agents.reporter import ReporterResponse
from cartograph.agents.rewriter import RewriterResponse
from cartograph.agents.scout import ScoutResponse
from cartograph.graph.models import SymbolGraph
from cartograph.tools.codemod import CodemodAmbiguity


class RunState(TypedDict, total=False):
    """Shared state threaded through every node of the pipeline StateGraph."""

    # --- Inputs, set once at graph invocation ---
    repo_path: str
    task_description: str

    # --- index_repo node ---
    symbol_graph: SymbolGraph | None

    # --- scout node ---
    scout_result: ScoutResponse | None

    # --- planner node ---
    planner_result: PlannerResponse | None
    plan_steps_remaining: list[PlanStep]
    plan_steps_done: list[PlanStep]

    # --- rewrite_step node (one step at a time, current_step tracks which) ---
    current_step: PlanStep | None
    rewriter_result: RewriterResponse | None
    retried_once: bool
    """Whether the *current* step has already been retried once."""

    # --- ambiguity path ---
    pending_ambiguities: list[CodemodAmbiguity]
    adjudication_result: AdjudicatorResponse | None

    # --- reporter node ---
    reporter_result: ReporterResponse | None

    # --- bookkeeping ---
    error: str | None
