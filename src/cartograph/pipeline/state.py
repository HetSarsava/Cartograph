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
    thread_id: str
    """Purely for log correlation — not used for control flow."""

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

    # --- populated by finish_step_node, for the final run summary ---
    committed_symbol_ids: list[str]
    quarantined_symbol_ids: list[str]

    # --- ambiguity path ---
    pending_ambiguities: list[CodemodAmbiguity]
    adjudication_result: AdjudicatorResponse | None
    adjudicated_this_step: bool
    """Whether adjudicate_node has already run for the *current* step.

    Prevents rewrite_step from re-attempting a codemod that's already been
    escalated (or otherwise resolved by Adjudicator without producing a new
    edit to try), which would just re-detect the same ambiguity forever —
    apply_codemod is deterministic, so re-running it against unchanged
    source reproduces the same ambiguity every time.
    """

    # --- per-step LLM call budget, independent safety net ---
    llm_calls_this_step: int
    """Agent calls made for the *current* step (rewrite_step's diagnosis
    call + adjudicate's call). Reset per step; capped by
    CARTOGRAPH_MAX_LLM_CALLS_PER_STEP so a routing bug fails fast and cheap
    instead of looping until a provider-side quota cuts it off."""

    # --- reporter node ---
    reporter_result: ReporterResponse | None

    # --- bookkeeping ---
    error: str | None

