"""Tests that the LangGraph StateGraph itself compiles and runs end-to-end.

Every agent call in this run is still a stub (hardcoded/mock response), so
this doesn't test real migration behavior — it tests that the graph
structure (nodes, conditional edges, checkpointer) is wired correctly and
that a full run reaches the reporter node without raising.
"""

from pathlib import Path

from cartograph.pipeline.graph import build_graph
from cartograph.pipeline.state import RunState

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_build_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_pipeline_runs_end_to_end_with_stub_agents():
    graph = build_graph()
    initial_state: RunState = {
        "repo_path": str(FIXTURE_REPO),
        "task_description": "demo task for the scaffolded pipeline",
    }
    config = {"configurable": {"thread_id": "test-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["symbol_graph"] is not None
    assert final_state["scout_result"] is not None
    assert final_state["planner_result"] is not None
    assert final_state["reporter_result"] is not None
    assert "STUB" in final_state["reporter_result"].report_markdown
