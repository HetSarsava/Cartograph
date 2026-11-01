"""Node functions for the Cartograph migration pipeline StateGraph.

Each node takes the current `RunState` and returns a partial dict to merge
into it (standard LangGraph node shape). Nodes that call an agent build that
agent's Pydantic request model from state, call the agent function, and fold
the response back into state.

Every node logs its own entry/exit tagged `[orchestrator]`, plus the run's
`thread_id` for correlation across a multi-node transcript.
"""

from __future__ import annotations

import logging
from typing import cast

from cartograph.agents.adjudicator import AdjudicatorRequest, run_adjudicator
from cartograph.agents.planner import PlannerRequest, run_planner
from cartograph.agents.reporter import ReporterRequest, run_reporter
from cartograph.agents.rewriter import RewriterRequest, run_rewriter
from cartograph.agents.scout import ScoutRequest, run_scout
from cartograph.config import get_settings
from cartograph.graph.indexer import index_repo
from cartograph.graph.queries import blast_radius
from cartograph.pipeline.state import RunState
from cartograph.providers.base import Provider
from cartograph.providers.groq_provider import GroqProvider
from cartograph.tools.test_runner import run_tests

logger = logging.getLogger(__name__)


def _build_provider() -> Provider:
    """Construct the configured LLM provider from settings.

    Falls back to a placeholder API key when unset; in that case any real
    agent call will fail with `ProviderError`, which is the correct failure
    mode (no silent stubbing at this layer anymore).
    """
    settings = get_settings()
    return GroqProvider(api_key=settings.groq_api_key or "unset", model=settings.groq_model)


def index_repo_node(state: RunState) -> dict:
    """Build the symbol graph for `state["repo_path"]`."""
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter index_repo", thread_id)

    symbol_graph = index_repo(state["repo_path"])
    logger.info(
        "[tool:index_repo] repo_path=%s symbols=%d edges=%d files_indexed=%d files_skipped=%d",
        state["repo_path"],
        len(symbol_graph.symbols),
        len(symbol_graph.edges),
        len(symbol_graph.files_indexed),
        len(symbol_graph.files_skipped),
    )

    logger.info("[orchestrator] thread=%s exit index_repo", thread_id)
    return {"symbol_graph": symbol_graph}


def scout_node(state: RunState) -> dict:
    """Ask Scout to narrow the full symbol set down to what's relevant."""
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter scout", thread_id)

    symbol_graph = state["symbol_graph"]
    assert symbol_graph is not None, "scout_node requires symbol_graph (run index_repo first)"
    request = ScoutRequest(
        task_description=state["task_description"],
        candidate_symbol_ids=list(symbol_graph.symbols.keys()),
    )
    result = run_scout(_build_provider(), request)

    logger.info(
        "[orchestrator] thread=%s exit scout relevant=%d",
        thread_id,
        len(result.relevant_symbol_ids),
    )
    return {"scout_result": result}


def planner_node(state: RunState) -> dict:
    """Ask Planner to turn Scout's relevant symbols into an ordered plan."""
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter planner", thread_id)

    scout_result = state["scout_result"]
    assert scout_result is not None, "planner_node requires scout_result (run scout first)"
    request = PlannerRequest(
        task_description=state["task_description"],
        relevant_symbol_ids=scout_result.relevant_symbol_ids,
    )
    result = run_planner(_build_provider(), request)

    symbol_graph = state.get("symbol_graph")
    if symbol_graph is not None:
        for step in result.steps:
            affected = blast_radius(symbol_graph, step.symbol_id)
            logger.info(
                "[tool:blast_radius] symbol_id=%s affected_count=%d affected=%s",
                step.symbol_id,
                len(affected),
                [s.qualified_name for s in affected],
            )

    logger.info("[orchestrator] thread=%s exit planner steps=%d", thread_id, len(result.steps))
    return {
        "planner_result": result,
        "plan_steps_remaining": list(result.steps),
        "plan_steps_done": [],
        "current_step": None,
        "committed_symbol_ids": [],
        "quarantined_symbol_ids": [],
    }


def rewrite_step_node(state: RunState) -> dict:
    """Attempt the current plan step, pulling the next one if none is in progress.

    Does not advance `plan_steps_done` — that happens in `finish_step_node`,
    after the single deterministic attempt and optional ambiguity review.
    """
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter rewrite_step", thread_id)

    current_step = state.get("current_step")
    remaining = list(state.get("plan_steps_remaining", []))
    if current_step is None:
        if not remaining:
            logger.info("[orchestrator] thread=%s exit rewrite_step (no steps left)", thread_id)
            return {}
        current_step = remaining.pop(0)

    logger.info("[orchestrator] rewrite_step target=%s", current_step.symbol_id)

    request = RewriterRequest(
        task_description=state["task_description"],
        plan_step=current_step,
        repo_path=state["repo_path"],
    )
    result = run_rewriter(_build_provider(), request)

    logger.info(
        "[orchestrator] thread=%s exit rewrite_step target=%s success=%s ambiguities=%d",
        thread_id,
        current_step.symbol_id,
        result.success,
        len(result.ambiguities),
    )
    return {
        "current_step": current_step,
        "plan_steps_remaining": remaining,
        "rewriter_result": result,
        "pending_ambiguities": result.ambiguities,
    }


def finish_step_node(state: RunState) -> dict:
    """Move the current step from in-progress to done and reset per-step flags."""
    current_step = state.get("current_step")
    done = list(state.get("plan_steps_done", []))
    committed = list(state.get("committed_symbol_ids", []))
    quarantined = list(state.get("quarantined_symbol_ids", []))

    if current_step is not None:
        done.append(current_step)
        rewriter_result = state.get("rewriter_result")
        succeeded = bool(rewriter_result and rewriter_result.success)
        if succeeded:
            committed.append(current_step.symbol_id)
        else:
            quarantined.append(current_step.symbol_id)
            logger.warning(
                "[orchestrator] quarantining target=%s: step did not succeed (see rewriter_result "
                "for what was attempted); pipeline continues with remaining steps",
                current_step.symbol_id,
            )

    return {
        "plan_steps_done": done,
        "current_step": None,
        "committed_symbol_ids": committed,
        "quarantined_symbol_ids": quarantined,
        "rewriter_result": None,
        "pending_ambiguities": [],
    }


def adjudicate_node(state: RunState) -> dict:
    """Ask Adjudicator to resolve the next pending ambiguity, if any."""
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter adjudicate", thread_id)

    ambiguities = state.get("pending_ambiguities", [])
    if not ambiguities:
        logger.info("[orchestrator] thread=%s exit adjudicate (nothing pending)", thread_id)
        return {}

    request = AdjudicatorRequest(ambiguity=ambiguities[0], surrounding_context="")
    result = run_adjudicator(_build_provider(), request)

    # Candidate recommendations are advisory: this implementation only executes
    # registered deterministic recipes. Never replay an unchanged ambiguous edit.
    logger.info("[orchestrator] adjudication=%s; target remains quarantined", result.decision)
    return {
        "adjudication_result": result,
        "pending_ambiguities": ambiguities[1:],
    }


def inspect_node(state: RunState) -> dict:
    """Index once, then let Scout select relevant symbols."""
    indexed = index_repo_node(state)
    return {**indexed, **scout_node({**state, **indexed})}


def execute_plan_node(state: RunState) -> dict:
    """Attempt each planned step once; review ambiguity, then record its outcome.

    Rewriter already snapshots, tests and restores failures. Retrying the same
    deterministic recipe cannot repair it. Adjudicator gives advice only.
    """
    working: dict[str, object] = dict(state)
    for _step in list(state.get("plan_steps_remaining", [])):
        working.update(rewrite_step_node(cast(RunState, working)))
        if working.get("pending_ambiguities"):
            working.update(adjudicate_node(cast(RunState, working)))
        working.update(finish_step_node(cast(RunState, working)))
    working["final_tests"] = run_tests(
        state["repo_path"], timeout_seconds=get_settings().test_timeout_seconds
    )
    return working


def reporter_node(state: RunState) -> dict:
    """Ask Reporter to summarize the completed run."""
    thread_id = state.get("thread_id", "?")
    logger.info("[orchestrator] thread=%s enter reporter", thread_id)

    done_steps = state.get("plan_steps_done", [])
    committed = set(state.get("committed_symbol_ids", []))
    quarantined = set(state.get("quarantined_symbol_ids", []))

    # Describe what actually happened per step, not what the Planner intended.
    # Feeding the planned `step.action` here let the Reporter narrate rewrites
    # the codemod never performed (and call quarantined files "converted").
    step_summaries = []
    for step in done_steps:
        if step.symbol_id in quarantined:
            outcome = "QUARANTINED - left unchanged, needs a human"
        elif step.symbol_id in committed:
            outcome = "COMMITTED - rewritten, tests green"
        else:
            outcome = "no change needed"
        step_summaries.append(f"{step.symbol_id}: {outcome}")

    request = ReporterRequest(
        task_description=state["task_description"],
        step_summaries=step_summaries,
        tests_passed=bool(state.get("final_tests") and state["final_tests"].all_passed),
        notes=[
            f"{len(committed)} target(s) committed, {len(quarantined)} quarantined.",
            "Quarantined targets were left byte-for-byte unchanged on disk.",
            "Committed targets had only their assertions rewritten; class structure and test names are unchanged.",
        ],
    )
    result = run_reporter(_build_provider(), request)

    logger.info("[orchestrator] thread=%s exit reporter", thread_id)
    return {"reporter_result": result}
