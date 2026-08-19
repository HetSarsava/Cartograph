"""Tests that the LangGraph StateGraph itself compiles and runs end-to-end.

`test_build_graph_compiles` and the `_route_after_rewrite` unit tests are
pure structural/routing checks (no network, no mocking needed for the
routing tests). `test_pipeline_quarantines_ambiguous_step_without_looping`
is a regression test for a real bug: after Adjudicator escalates an
ambiguity, nothing stopped `rewrite_step` from re-running the (deterministic)
codemod, re-detecting the identical ambiguity, and routing back to
`adjudicate` forever — see `_route_after_rewrite` and `adjudicate_node` for
the fix. It mocks Scout/Planner/Adjudicator/Reporter (all real LLM calls)
but lets the real `run_rewriter` execute against the real ambiguous fixture,
since that path never calls an LLM — the ambiguity is detected by the
deterministic `apply_codemod` alone. This keeps the regression test fast,
deterministic, and network-free while still exercising the real routing fix.

`test_pipeline_runs_end_to_end_with_real_agents` makes real Groq API calls
and is skipped when `GROQ_API_KEY` isn't set (e.g. CI without secrets).
"""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from cartograph.agents.adjudicator import AdjudicationDecision, AdjudicatorResponse
from cartograph.agents.planner import PlanStep, PlannerResponse
from cartograph.agents.reporter import ReporterResponse
from cartograph.agents.rewriter import run_rewriter as real_run_rewriter
from cartograph.agents.scout import ScoutResponse
from cartograph.config import get_settings
from cartograph.pipeline.graph import _route_after_rewrite, build_graph
from cartograph.pipeline.state import RunState
from cartograph.tools.codemod import CodemodAmbiguity

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def sample_repo(tmp_path):
    """A disposable copy of the fixture repo.

    The pipeline rewrites files in place, so pointing a test at FIXTURE_REPO
    directly edits the committed fixture -- `make test` would leave the repo
    dirty and, worse, consume the unittest-style code the demo depends on.
    """
    dest = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE_REPO, dest)
    return dest

AMBIGUOUS_SYMBOL_ID = (
    "tests/test_legacy_unittest_ambiguous.py::AmbiguousAssertionTests.test_add_in_expected_set"
)

_requires_groq_key = pytest.mark.skipif(
    not get_settings().groq_api_key, reason="GROQ_API_KEY not set; skipping live-provider test"
)


def test_build_graph_compiles():
    graph = build_graph()
    assert graph is not None


# --- _route_after_rewrite: routing unit tests (the crux of the bug) -----


def _make_step() -> PlanStep:
    return PlanStep(symbol_id="a.py::Foo.test_x", action="convert", recipe_name="unittest_to_pytest")


def test_route_after_rewrite_stops_looping_once_adjudicated():
    """Once a step has been adjudicated, routing must go straight to
    finish_step even though pending_ambiguities is (again) non-empty —
    the exact condition that caused the infinite adjudicate<->rewrite_step
    loop."""
    state: RunState = {
        "current_step": _make_step(),
        "adjudicated_this_step": True,
        "pending_ambiguities": [CodemodAmbiguity(file_path="a.py", line=1, description="still ambiguous")],
        "rewriter_result": None,
        "retried_once": False,
        "llm_calls_this_step": 1,
    }
    assert _route_after_rewrite(state) == "finish_step"


def test_route_after_rewrite_still_routes_a_new_ambiguity_to_adjudicate():
    state: RunState = {
        "current_step": _make_step(),
        "adjudicated_this_step": False,
        "pending_ambiguities": [CodemodAmbiguity(file_path="a.py", line=1, description="ambiguous")],
        "rewriter_result": None,
        "retried_once": False,
        "llm_calls_this_step": 0,
    }
    assert _route_after_rewrite(state) == "adjudicate"


def test_route_after_rewrite_budget_exceeded_forces_finish_step():
    """Independent safety net: even with a fresh, unadjudicated ambiguity,
    exceeding the per-step call budget forces finish_step regardless."""
    state: RunState = {
        "current_step": _make_step(),
        "adjudicated_this_step": False,
        "pending_ambiguities": [CodemodAmbiguity(file_path="a.py", line=1, description="ambiguous")],
        "rewriter_result": None,
        "retried_once": False,
        "llm_calls_this_step": get_settings().max_llm_calls_per_step,
    }
    assert _route_after_rewrite(state) == "finish_step"


# --- end-to-end regression test, mocked LLM-backed agents ---------------


def test_pipeline_quarantines_ambiguous_step_without_looping(sample_repo):
    mock_scout = ScoutResponse(relevant_symbol_ids=[AMBIGUOUS_SYMBOL_ID], reasoning="mock")
    mock_planner = PlannerResponse(
        steps=[
            PlanStep(
                symbol_id=AMBIGUOUS_SYMBOL_ID,
                action="convert unittest assertions to pytest asserts",
                recipe_name="unittest_to_pytest",
            )
        ],
        summary="mock",
    )
    mock_adjudication = AdjudicatorResponse(
        decision=AdjudicationDecision.ESCALATE_TO_HUMAN,
        chosen_candidate=None,
        reasoning="mock escalation",
    )
    mock_report = ReporterResponse(report_markdown="# mock report")

    with (
        patch("cartograph.pipeline.nodes.run_scout", return_value=mock_scout) as scout_mock,
        patch("cartograph.pipeline.nodes.run_planner", return_value=mock_planner) as planner_mock,
        patch(
            "cartograph.pipeline.nodes.run_rewriter", wraps=real_run_rewriter
        ) as rewriter_mock,
        patch(
            "cartograph.pipeline.nodes.run_adjudicator", return_value=mock_adjudication
        ) as adjudicator_mock,
        patch("cartograph.pipeline.nodes.run_reporter", return_value=mock_report) as reporter_mock,
    ):
        graph = build_graph()
        initial_state: RunState = {
            "repo_path": str(sample_repo),
            "task_description": "convert unittest-style assertions to plain pytest asserts",
            "thread_id": "test-ambiguity-loop",
        }
        config = {"configurable": {"thread_id": "test-ambiguity-loop"}}
        final_state = graph.invoke(initial_state, config=config)

    # Reached the end — didn't loop.
    assert final_state["reporter_result"] is not None

    # Quarantined, not silently dropped and not committed.
    assert AMBIGUOUS_SYMBOL_ID in final_state["quarantined_symbol_ids"]
    assert AMBIGUOUS_SYMBOL_ID not in final_state["committed_symbol_ids"]

    # Each agent called a small, fixed number of times for this one step —
    # the crux of the fix: exactly once each, not dozens of times.
    scout_mock.assert_called_once()
    planner_mock.assert_called_once()
    assert rewriter_mock.call_count == 1
    assert adjudicator_mock.call_count == 1
    reporter_mock.assert_called_once()


@_requires_groq_key
def test_pipeline_runs_end_to_end_with_real_agents(sample_repo):
    graph = build_graph()
    initial_state: RunState = {
        "repo_path": str(sample_repo),
        "task_description": "demo task for the scaffolded pipeline",
        "thread_id": "test-thread",
    }
    config = {"configurable": {"thread_id": "test-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["symbol_graph"] is not None
    assert final_state["scout_result"] is not None
    assert final_state["planner_result"] is not None
    assert final_state["reporter_result"] is not None
    assert final_state["reporter_result"].report_markdown.strip() != ""
    # Every planned step should end up in exactly one of these two buckets.
    total_steps = len(final_state.get("plan_steps_done", []))
    assert len(final_state.get("committed_symbol_ids", [])) + len(
        final_state.get("quarantined_symbol_ids", [])
    ) == total_steps
