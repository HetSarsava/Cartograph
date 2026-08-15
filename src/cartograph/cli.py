"""Command-line entrypoint: `cartograph run <repo> <task>`."""

from __future__ import annotations

import logging
import time
import uuid

import click

from cartograph.config import get_settings
from cartograph.logging_setup import configure_logging
from cartograph.pipeline.graph import build_graph
from cartograph.pipeline.state import RunState
from cartograph.providers.groq_provider import call_stats

logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """Cartograph: a code-intelligence MCP server and LangGraph migration pipeline."""


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.argument("task", type=str)
@click.option("--thread-id", default=None, help="Checkpointer thread id; random if omitted.")
@click.option(
    "--log-level",
    default="DEBUG",
    show_default=True,
    help=(
        "Overrides CARTOGRAPH_LOG_LEVEL for this run. Defaults to DEBUG (not whatever's in "
        ".env) since `cartograph run` is a debugging-focused command; pass e.g. --log-level "
        "INFO to quiet it down."
    ),
)
def run(repo_path: str, task: str, thread_id: str | None, log_level: str) -> None:
    """Run the migration pipeline against REPO_PATH for TASK.

    Example:

        cartograph run ./my-repo "convert unittest tests to pytest"
    """
    resolved_thread_id = thread_id or str(uuid.uuid4())
    log_path = configure_logging(level=log_level, thread_id=resolved_thread_id)
    click.echo(f"Logging to {log_path} (level={log_level})")

    call_stats.reset()
    settings = get_settings()
    logger.info(
        "[orchestrator] starting run thread=%s repo_path=%s model=%s task=%r",
        resolved_thread_id,
        repo_path,
        settings.groq_model,
        task,
    )

    graph = build_graph()
    initial_state: RunState = {
        "repo_path": repo_path,
        "task_description": task,
        "thread_id": resolved_thread_id,
    }
    config = {"configurable": {"thread_id": resolved_thread_id}}

    start = time.monotonic()
    try:
        final_state = graph.invoke(initial_state, config=config)
    except Exception:
        logger.exception("[orchestrator] run thread=%s crashed", resolved_thread_id)
        click.echo(f"\nRun crashed — see {log_path} for the full traceback.", err=True)
        click.echo(f"Full log: {log_path}")
        raise
    duration_seconds = time.monotonic() - start

    reporter_result = final_state.get("reporter_result")
    if reporter_result is not None:
        logger.info("[reporter] final report:\n%s", reporter_result.report_markdown)
        click.echo(reporter_result.report_markdown)
    else:
        logger.warning("[orchestrator] run thread=%s did not reach the reporter node", resolved_thread_id)
        click.echo("Run did not reach the reporter node.", err=True)

    _print_summary(final_state, duration_seconds, resolved_thread_id)
    click.echo(f"\nFull log: {log_path}")


def _print_summary(final_state: dict, duration_seconds: float, thread_id: str) -> None:
    committed = final_state.get("committed_symbol_ids", [])
    quarantined = final_state.get("quarantined_symbol_ids", [])
    attempted = len(final_state.get("plan_steps_done", []))

    logger.info(
        "[orchestrator] run summary thread=%s duration_s=%.1f llm_calls=%d "
        "llm_calls_failed=%d llm_tokens=%d attempted=%d committed=%d quarantined=%d",
        thread_id,
        duration_seconds,
        call_stats.call_count,
        call_stats.failed_call_count,
        call_stats.total_tokens,
        attempted,
        len(committed),
        len(quarantined),
    )

    summary = "\n".join(
        [
            "",
            "=== Run summary ===",
            f"thread_id:           {thread_id}",
            f"wall_clock:          {duration_seconds:.1f}s",
            f"llm_calls:           {call_stats.call_count} ({call_stats.failed_call_count} failed)",
            f"llm_tokens:          prompt={call_stats.total_prompt_tokens} "
            f"completion={call_stats.total_completion_tokens} total={call_stats.total_tokens}",
            f"targets_attempted:   {attempted}",
            f"targets_committed:   {len(committed)} {committed}",
            f"targets_quarantined: {len(quarantined)} {quarantined}",
        ]
    )
    click.echo(summary)


def main() -> None:
    """Console-script entrypoint (see [project.scripts] in pyproject.toml)."""
    cli()


if __name__ == "__main__":
    main()
