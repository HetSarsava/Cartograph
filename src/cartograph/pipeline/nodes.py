"""Node functions for the Cartograph migration pipeline StateGraph.

Each node takes the current `RunState` and returns a partial dict to merge
into it (standard LangGraph node shape). Nodes that call an agent build that
agent's Pydantic request model from state, call the (currently stubbed)
agent function, and fold the response back into state — the graph's
structure and control flow are real even though most agent decisions are
still mocked.
"""

from __future__ import annotations

from cartograph.agents.adjudicator import AdjudicatorRequest, run_adjudicator
from cartograph.agents.planner import PlannerRequest, run_planner
from cartograph.agents.reporter import ReporterRequest, run_reporter
from cartograph.agents.rewriter import RewriterRequest, run_rewriter
from cartograph.agents.scout import ScoutRequest, run_scout
from cartograph.config import get_settings
from cartograph.graph.indexer import index_repo
from cartograph.pipeline.state import RunState
from cartograph.providers.base import Provider
from cartograph.providers.groq_provider import GroqProvider


def _build_provider() -> Provider:
    """Construct the configured LLM provider from settings.

    Falls back to a placeholder API key when unset — safe for now because
    every agent function is still stubbed and never actually calls the
    provider. Once agents are implemented, running without `GROQ_API_KEY`
    set should start raising `ProviderError` from real calls, which is the
    correct failure mode.
    """
    settings = get_settings()
    return GroqProvider(api_key=settings.groq_api_key or "unset", model=settings.groq_model)


def index_repo_node(state: RunState) -> dict:
    """Build the symbol graph for `state["repo_path"]`."""
    symbol_graph = index_repo(state["repo_path"])
    return {"symbol_graph": symbol_graph}


def scout_node(state: RunState) -> dict:
    """Ask Scout to narrow the full symbol set down to what's relevant."""
    symbol_graph = state["symbol_graph"]
    assert symbol_graph is not None, "scout_node requires symbol_graph (run index_repo first)"
    request = ScoutRequest(
        task_description=state["task_description"],
        candidate_symbol_ids=list(symbol_graph.symbols.keys()),
    )
    result = run_scout(_build_provider(), request)
    return {"scout_result": result}


def planner_node(state: RunState) -> dict:
    """Ask Planner to turn Scout's relevant symbols into an ordered plan."""
    scout_result = state["scout_result"]
    assert scout_result is not None, "planner_node requires scout_result (run scout first)"
    request = PlannerRequest(
        task_description=state["task_description"],
        relevant_symbol_ids=scout_result.relevant_symbol_ids,
    )
    result = run_planner(_build_provider(), request)
    return {
        "planner_result": result,
        "plan_steps_remaining": list(result.steps),
        "plan_steps_done": [],
        "current_step": None,
        "retried_once": False,
    }


def rewrite_step_node(state: RunState) -> dict:
    """Attempt the current plan step, pulling the next one if none is in progress.

    Does not advance `plan_steps_done` — that happens in `finish_step_node`,
    once `_route_after_rewrite` decides the step is done (success, or failed
    even after its one retry).
    """
    current_step = state.get("current_step")
    remaining = list(state.get("plan_steps_remaining", []))
    if current_step is None:
        if not remaining:
            return {}
        current_step = remaining.pop(0)

    request = RewriterRequest(
        task_description=state["task_description"],
        plan_step=current_step,
        repo_path=state["repo_path"],
    )
    result = run_rewriter(_build_provider(), request)
    return {
        "current_step": current_step,
        "plan_steps_remaining": remaining,
        "rewriter_result": result,
        # The stubbed rewriter never reports ambiguities yet; once
        # tools.codemod.apply_codemod is real, surface its ambiguities here.
        "pending_ambiguities": [],
    }


def retry_flag_node(state: RunState) -> dict:
    """Mark the current step as retried, so it isn't retried a second time."""
    return {"retried_once": True}


def finish_step_node(state: RunState) -> dict:
    """Move the current step from in-progress to done and reset per-step flags."""
    current_step = state.get("current_step")
    done = list(state.get("plan_steps_done", []))
    if current_step is not None:
        done.append(current_step)
    return {"plan_steps_done": done, "current_step": None, "retried_once": False}


def adjudicate_node(state: RunState) -> dict:
    """Ask Adjudicator to resolve the next pending ambiguity, if any."""
    ambiguities = state.get("pending_ambiguities", [])
    if not ambiguities:
        return {}
    request = AdjudicatorRequest(ambiguity=ambiguities[0], surrounding_context="")
    result = run_adjudicator(_build_provider(), request)
    return {"adjudication_result": result, "pending_ambiguities": ambiguities[1:]}


def reporter_node(state: RunState) -> dict:
    """Ask Reporter to summarize the completed run."""
    done_steps = state.get("plan_steps_done", [])
    rewriter_result = state.get("rewriter_result")
    request = ReporterRequest(
        task_description=state["task_description"],
        step_summaries=[f"{step.symbol_id}: {step.action}" for step in done_steps],
        tests_passed=bool(rewriter_result and rewriter_result.success),
    )
    result = run_reporter(_build_provider(), request)
    return {"reporter_result": result}
