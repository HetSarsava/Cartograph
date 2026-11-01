"""Behavior tests for the four-stage pipeline; LLM responses are mocked unless marked live."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from cartograph.agents.adjudicator import AdjudicationDecision, AdjudicatorResponse
from cartograph.agents.planner import PlannerResponse, PlanStep
from cartograph.agents.reporter import ReporterResponse
from cartograph.agents.rewriter import run_rewriter as real_run_rewriter
from cartograph.agents.scout import ScoutResponse
from cartograph.config import get_settings
from cartograph.pipeline.graph import build_graph
from cartograph.pipeline.state import RunState

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


# --- end-to-end regression test, mocked LLM-backed agents ---------------


@pytest.mark.parametrize("decision", list(AdjudicationDecision))
def test_pipeline_quarantines_ambiguous_step_without_looping(sample_repo, decision):
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
        decision=decision,
        chosen_candidate=None,
        reasoning="mock escalation",
    )
    mock_report = ReporterResponse(report_markdown="# mock report")

    with (
        patch("cartograph.pipeline.nodes.run_scout", return_value=mock_scout) as scout_mock,
        patch("cartograph.pipeline.nodes.run_planner", return_value=mock_planner) as planner_mock,
        patch("cartograph.pipeline.nodes.run_rewriter", wraps=real_run_rewriter) as rewriter_mock,
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
        final_state = graph.invoke(initial_state)

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

    final_state = graph.invoke(initial_state)

    assert final_state["symbol_graph"] is not None
    assert final_state["scout_result"] is not None
    assert final_state["planner_result"] is not None
    assert final_state["reporter_result"] is not None
    assert final_state["reporter_result"].report_markdown.strip() != ""
    # Every planned step should end up in exactly one of these two buckets.
    total_steps = len(final_state.get("plan_steps_done", []))
    assert (
        len(final_state.get("committed_symbol_ids", []))
        + len(final_state.get("quarantined_symbol_ids", []))
        == total_steps
    )


@pytest.mark.parametrize("count", [0, 40])
def test_empty_and_long_plans_need_no_graph_loop(sample_repo, count):
    from cartograph.agents.rewriter import RewriterResponse

    steps = [PlanStep(symbol_id=f"a.py::f{i}", action="review") for i in range(count)]
    with (
        patch(
            "cartograph.pipeline.nodes.run_scout",
            return_value=ScoutResponse(relevant_symbol_ids=[], reasoning="test"),
        ),
        patch(
            "cartograph.pipeline.nodes.run_planner",
            return_value=PlannerResponse(steps=steps, summary="test"),
        ),
        patch(
            "cartograph.pipeline.nodes.run_rewriter",
            return_value=RewriterResponse(success=False, final_summary="no recipe"),
        ) as rewrite,
        patch(
            "cartograph.pipeline.nodes.run_reporter",
            return_value=ReporterResponse(report_markdown="test"),
        ),
    ):
        result = build_graph().invoke({"repo_path": str(sample_repo), "task_description": "test"})
    assert rewrite.call_count == count
    assert len(result["quarantined_symbol_ids"]) == count
    assert result["reporter_result"] is not None


def test_successful_step_rewrites_and_tests(sample_repo):
    symbol_id = "tests/test_legacy_unittest.py::ArithmeticTests.test_add"
    with (
        patch(
            "cartograph.pipeline.nodes.run_scout",
            return_value=ScoutResponse(relevant_symbol_ids=[symbol_id], reasoning="test"),
        ),
        patch(
            "cartograph.pipeline.nodes.run_planner",
            return_value=PlannerResponse(
                steps=[
                    PlanStep(
                        symbol_id=symbol_id, action="convert", recipe_name="unittest_to_pytest"
                    )
                ],
                summary="test",
            ),
        ),
        patch("cartograph.pipeline.nodes.run_adjudicator") as adjudicate,
        patch(
            "cartograph.pipeline.nodes.run_reporter",
            return_value=ReporterResponse(report_markdown="test"),
        ),
    ):
        result = build_graph().invoke(
            {"repo_path": str(sample_repo), "task_description": "convert"}
        )
    assert symbol_id in result["committed_symbol_ids"]
    assert not result["quarantined_symbol_ids"]
    adjudicate.assert_not_called()
    text = (sample_repo / "tests" / "test_legacy_unittest.py").read_text()
    assert "self.assertEqual" not in text


@pytest.mark.parametrize("failure", ["diagnosis", "test_runner", "codemod"])
def test_rewriter_restores_even_when_a_dependency_raises(tmp_path, failure):
    from unittest.mock import Mock

    from cartograph.agents.rewriter import RewriterRequest
    from cartograph.providers.base import ProviderError
    from cartograph.tools.test_runner import TestRunResult

    target = tmp_path / "test_a.py"
    original = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 2)\n"
    target.write_text(original)
    request = RewriterRequest(
        repo_path=str(tmp_path),
        task_description="convert",
        plan_step=PlanStep(
            symbol_id="test_a.py::T.test_a", action="convert", recipe_name="unittest_to_pytest"
        ),
    )
    provider = Mock()
    provider.complete_structured.side_effect = ProviderError("offline")
    bad_tests = TestRunResult(
        command=[],
        return_code=1,
        total=1,
        passed=0,
        failed=1,
        errors=0,
        skipped=0,
        duration_seconds=0,
    )

    def corrupt_then_raise(*args, **kwargs):
        target.write_text("partially written")
        raise RuntimeError("tool crashed")

    if failure == "codemod":
        context = patch("cartograph.agents.rewriter.apply_codemod", side_effect=corrupt_then_raise)
    elif failure == "test_runner":
        context = patch(
            "cartograph.agents.rewriter.run_tests", side_effect=RuntimeError("tool crashed")
        )
    else:
        context = patch("cartograph.agents.rewriter.run_tests", return_value=bad_tests)
    with context, pytest.raises((ProviderError, RuntimeError)):
        real_run_rewriter(provider, request)
    assert target.read_text() == original
