"""Builds and compiles the Cartograph migration pipeline as a LangGraph StateGraph.

Flow::

    START -> index_repo -> scout -> planner -> rewrite_step
                                                    |
                          (conditional: _route_after_rewrite)
                            /            |            \\
                   pending          failed &&        succeeded, or
                  ambiguity      not retried yet     failed-after-retry
                       |                |                    |
                  adjudicate -+   retry_flag -+          finish_step
                               \\_____________/________________/
                                              |
                                       rewrite_step (loop)
                                              |
                        (conditional: _route_after_finish_step, from finish_step)
                                 /                          \\
                       steps remaining                  no steps left
                              |                                |
                        rewrite_step (loop)                reporter -> END

Only `MemorySaver` is wired up for now; swap the `checkpointer` argument for
a persistent implementation (e.g. SQLite/Postgres-backed) without touching
graph structure.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

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


def _route_after_rewrite(state: RunState) -> str:
    """Decide what happens after one `rewrite_step` attempt.

    Priority: unresolved ambiguities beat everything (they must be resolved
    before the step can be judged done); otherwise a failed step gets
    exactly one retry before being accepted as done (success or not).
    """
    if state.get("pending_ambiguities"):
        return "adjudicate"

    rewriter_result = state.get("rewriter_result")
    step_failed = rewriter_result is not None and not rewriter_result.success
    if step_failed and not state.get("retried_once", False):
        return "retry"

    return "finish_step"


def _route_after_finish_step(state: RunState) -> str:
    """After a step is finalized, either move to the next one or wrap up."""
    if state.get("plan_steps_remaining"):
        return "next_step"
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
