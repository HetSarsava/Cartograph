"""Builds and compiles the Cartograph migration pipeline as a LangGraph StateGraph.

Flow::

    START -> index_repo -> scout -> planner -> rewrite_step
                                                    |
                          (conditional: _route_after_rewrite, checked in order)
                            1. budget exceeded         -> finish_step (quarantined)
                            2. already adjudicated      -> finish_step (quarantined)
                            3. new pending ambiguity     -> adjudicate -> rewrite_step (loop)
                            4. failed, retry available   -> retry_flag -> rewrite_step (loop)
                            5. succeeded, or failed-after-retry -> finish_step
                                                    |
                        (conditional: _route_after_finish_step, from finish_step)
                                 /                          \\
                       steps remaining                  no steps left
                              |                                |
                        rewrite_step (loop)                reporter -> END

Checks 1 and 2 both route straight to `finish_step` (quarantined) rather
than back into the adjudicate/rewrite loop — see `_route_after_rewrite`
for why re-entering that loop for the same ambiguity is exactly the bug
this guards against.

Only `MemorySaver` is wired up for now; swap the `checkpointer` argument for
a persistent implementation (e.g. SQLite/Postgres-backed) without touching
graph structure.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cartograph.config import get_settings
from cartograph.pipeline.nodes import (
    adjudicate_node,
    finish_step_node,
    index_repo_node,
    planner_node,
    reporter_node,
    retry_flag_node,
    rewrite_step_node,
    scout_node,
)
from cartograph.pipeline.state import RunState

logger = logging.getLogger(__name__)


def _route_after_rewrite(state: RunState) -> str:
    """Decide what happens after one `rewrite_step` attempt.

    Priority, highest first:
      1. Per-step LLM call budget exceeded — independent safety net. Forces
         `finish_step` regardless of anything else, so a routing bug that
         slips past check #2 (or any other future bug) fails fast and cheap
         instead of looping until a provider-side quota does.
      2. This step's ambiguity has already been through Adjudicator with a
         decision that gives us nothing new to try (SKIP/ESCALATE_TO_HUMAN)
         — `finish_step` (quarantined). Re-running a deterministic codemod
         against unchanged source would just re-detect the same ambiguity
         and loop with `adjudicate` forever; see `adjudicate_node`.
      3. A *new* unresolved ambiguity — `adjudicate`.
      4. A failed step gets exactly one retry before being accepted as done.
    """
    current_step = state.get("current_step")
    symbol_id = current_step.symbol_id if current_step else "?"

    llm_calls_this_step = state.get("llm_calls_this_step", 0)
    max_calls = get_settings().max_llm_calls_per_step
    if llm_calls_this_step >= max_calls:
        logger.warning(
            "[gate:budget] target=%s exceeded max LLM calls per step (%d >= %d) "
            "-> finish_step (quarantined)",
            symbol_id,
            llm_calls_this_step,
            max_calls,
        )
        return "finish_step"

    if state.get("adjudicated_this_step"):
        logger.info(
            "[gate:ambiguity] target=%s already adjudicated -> finish_step (quarantined)",
            symbol_id,
        )
        return "finish_step"

    if state.get("pending_ambiguities"):
        logger.info(
            "[gate:ambiguity] target=%s FAIL (%d pending) -> adjudicate",
            symbol_id,
            len(state["pending_ambiguities"]),
        )
        return "adjudicate"

    rewriter_result = state.get("rewriter_result")
    step_failed = rewriter_result is not None and not rewriter_result.success
    if step_failed and not state.get("retried_once", False):
        logger.info("[gate:tests] target=%s FAIL, retry not yet used -> retry", symbol_id)
        return "retry"

    if step_failed:
        logger.info("[gate:tests] target=%s FAIL after retry -> finish_step (quarantined)", symbol_id)
    else:
        logger.info("[gate:tests] target=%s PASS -> finish_step", symbol_id)
    return "finish_step"


def _route_after_finish_step(state: RunState) -> str:
    """After a step is finalized, either move to the next one or wrap up."""
    remaining = state.get("plan_steps_remaining", [])
    if remaining:
        logger.info("[gate:plan] %d step(s) remaining -> next_step", len(remaining))
        return "next_step"
    logger.info("[gate:plan] no steps remaining -> done")
    return "done"


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Build and compile the Cartograph migration pipeline.

    Args:
        checkpointer: Checkpointer implementation to compile with. Defaults
            to an in-memory `MemorySaver`, suitable for tests and local runs.

    Returns:
        A compiled LangGraph graph, invocable via `.invoke(initial_state, config)`.
    """
    graph = StateGraph(RunState)

    graph.add_node("index_repo", index_repo_node)
    graph.add_node("scout", scout_node)
    graph.add_node("planner", planner_node)
    graph.add_node("rewrite_step", rewrite_step_node)
    graph.add_node("adjudicate", adjudicate_node)
    graph.add_node("retry_flag", retry_flag_node)
    graph.add_node("finish_step", finish_step_node)
    graph.add_node("reporter", reporter_node)

    graph.add_edge(START, "index_repo")
    graph.add_edge("index_repo", "scout")
    graph.add_edge("scout", "planner")
    graph.add_edge("planner", "rewrite_step")

    graph.add_conditional_edges(
        "rewrite_step",
        _route_after_rewrite,
        {
            "adjudicate": "adjudicate",
            "retry": "retry_flag",
            "finish_step": "finish_step",
        },
    )
    graph.add_edge("adjudicate", "rewrite_step")
    graph.add_edge("retry_flag", "rewrite_step")

    graph.add_conditional_edges(
        "finish_step",
        _route_after_finish_step,
        {
            "next_step": "rewrite_step",
            "done": "reporter",
        },
    )
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
