"""Command-line entrypoint: `cartograph run <repo> <task>`."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import click

from cartograph.config import get_settings
from cartograph.graph.event_sink import JsonlEventSink
from cartograph.graph.indexer import index_repo
from cartograph.graph.models import SymbolGraph
from cartograph.graph.tree_export import export_tree_html
from cartograph.logging_setup import configure_logging
from cartograph.pipeline.graph import build_graph
from cartograph.pipeline.state import RunState
from cartograph.providers.groq_provider import call_stats
from cartograph.webui.server import DEFAULT_PORT, PortInUseError, bind, serve

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


@cli.command(name="index")
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--visualize",
    is_flag=True,
    default=False,
    help=(
        "Write a live newline-delimited-JSON event stream to ./logs/ as indexing "
        "happens, for viewer/graph_viewer.html to poll and render incrementally."
    ),
)
def index_cmd(repo_path: str, visualize: bool) -> None:
    """Index REPO_PATH and print summary counts (symbols, edges, files).

    Example:

        cartograph index ./my-repo --visualize
    """
    sink: JsonlEventSink | None = None
    if visualize:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        event_path = Path("logs") / f"graph-events-{timestamp}.jsonl"
        sink = JsonlEventSink(event_path)
        click.echo(f"Writing live index events to {event_path}")
        click.echo(
            "Open viewer/graph_viewer.html and point it at that file (see the file's "
            "header comment for how) to watch the graph build live."
        )

    try:
        graph = index_repo(repo_path, on_event=sink)
    finally:
        if sink is not None:
            sink.close()

    click.echo(_summary_line(repo_path, graph))
    if sink is not None:
        click.echo(f"Event file: {sink.path}")


def _summary_line(repo_path: str, graph: SymbolGraph) -> str:
    """One-line index summary, shared by `index` and `tree` so they can't drift."""
    resolved = sum(1 for edge in graph.edges if edge.target_id is not None)
    return (
        f"Indexed {repo_path}: {len(graph.symbols)} symbols, {len(graph.edges)} edges "
        f"({resolved} resolved, {len(graph.edges) - resolved} unresolved), "
        f"{len(graph.files_indexed)} files indexed, {len(graph.files_skipped)} skipped"
    )


@cli.command(name="tree")
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--output",
    type=click.Path(dir_okay=False),
    default=None,
    help="Where to write the HTML file. Defaults to ./logs/tree-<timestamp>.html",
)
def tree_cmd(repo_path: str, output: str | None) -> None:
    """Index REPO_PATH and write a standalone HTML dependency tree.

    The output is a single self-contained file — open it by double-clicking,
    no server needed.

    Example:

        cartograph tree ./my-repo
    """
    if output is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = str(Path("logs") / f"tree-{timestamp}.html")

    graph = index_repo(repo_path)
    written = export_tree_html(graph, output)

    click.echo(_summary_line(repo_path, graph))
    click.echo(f"\nWrote tree to: {written}")
    click.echo(f"Open it:       {written.as_uri()}")


@cli.command(name="serve")
@click.option("--port", default=DEFAULT_PORT, show_default=True, help="Port to bind on localhost.")
@click.option(
    "--no-browser", is_flag=True, default=False, help="Don't open a browser automatically."
)
@click.option("--log-level", default="INFO", show_default=True, help="Logging level for the server.")
def serve_cmd(port: int, no_browser: bool, log_level: str) -> None:
    """Start the local web UI for indexing repos and browsing trees.

    Binds to 127.0.0.1 only — this runs the indexer against local paths you
    give it, so it is not safe to expose on a network.

    Example:

        cartograph serve --port 8765
    """
    configure_logging(level=log_level)

    # Bind before announcing anything, so a taken port is a one-line message
    # rather than a success banner followed by a traceback.
    try:
        httpd = bind(port)
    except PortInUseError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Cartograph UI running at http://127.0.0.1:{port}/")
    click.echo(f"Serving from: {Path.cwd()}")
    click.echo("Press Ctrl+C to stop.")
    serve(open_browser=not no_browser, httpd=httpd)
    click.echo("\nStopped.")


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
