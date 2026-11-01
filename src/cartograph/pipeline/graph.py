"""Minimal LangGraph orchestration: inspect -> plan -> execute -> report.

Five roles remain: Scout, Planner, Rewriter, Adjudicator and Reporter.
Per-step execution is an ordinary bounded Python loop in nodes.execute_plan_node.
There are no graph cycles, checkpoint stores, reducers or nested graphs.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cartograph.pipeline.nodes import (
    execute_plan_node,
    inspect_node,
    planner_node,
    reporter_node,
)
from cartograph.pipeline.state import RunState


def build_graph() -> CompiledStateGraph:
    """Compile four sequential stages; invoke with repo_path and task_description."""
    graph = StateGraph(RunState)
    stages = [
        ("inspect", inspect_node),
        ("plan", planner_node),
        ("execute", execute_plan_node),
        ("report", reporter_node),
    ]
    previous = START
    for name, node in stages:
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()
