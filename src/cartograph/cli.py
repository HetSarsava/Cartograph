"""Command-line entrypoint: `cartograph run <repo> <task>`."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
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
from cartograph.providers.base import ProviderError
from cartograph.providers.groq_provider import call_stats
from cartograph.webui.jobs import find_sample_repo
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
    if not settings.groq_api_key:
        raise click.ClickException(
            "GROQ_API_KEY is not set, and `run` needs it to call the agents.\n"
            "  cp .env.example .env   # then paste a key from https://console.groq.com/keys\n"
            "Everything else (index / tree / serve) works without a key."
        )
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

    start = time.monotonic()
    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        # A provider failure is nearly always a wrong model id, a bad key, or a
        # spent quota. Say which, in one line -- a traceback adds nothing and
        # buries the actionable part.
        cause = _find_provider_error(exc)
        if cause is not None:
            logger.error("[orchestrator] run thread=%s failed: %s", resolved_thread_id, cause)
            raise click.ClickException(
                f"{cause}"
                + chr(10)
                + f"Check GROQ_MODEL in .env (currently {settings.groq_model!r}); "
                f"run `cartograph models` to list the ids your key can use."
                + chr(10)
                + f"Full log: {log_path}"
            ) from exc
        logger.exception("[orchestrator] run thread=%s crashed", resolved_thread_id)
        click.echo(f"Run crashed. Full log: {log_path}", err=True)
        raise
    duration_seconds = time.monotonic() - start

    reporter_result = final_state.get("reporter_result")
    if reporter_result is not None:
        logger.info("[reporter] final report:\n%s", reporter_result.report_markdown)
        click.echo(reporter_result.report_markdown)
    else:
        logger.warning(
            "[orchestrator] run thread=%s did not reach the reporter node", resolved_thread_id
        )
        click.echo("Run did not reach the reporter node.", err=True)

    _print_summary(final_state, duration_seconds, resolved_thread_id)
    click.echo(f"\nFull log: {log_path}")


def _force_rmtree(path: Path) -> None:
    """Delete `path`, including read-only files.

    The pipeline's snapshot tool creates a .git directory inside the repo it
    works on, and git marks its object files read-only. On Windows that makes
    plain shutil.rmtree fail with PermissionError -- which would break the
    demo on its *second* run, the one you do in front of an audience.
    """

    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=on_error)


@cli.command(name="demo")
@click.option(
    "--keep",
    is_flag=True,
    default=False,
    help="Keep the previous demo copy instead of starting from a fresh one.",
)
@click.pass_context
def demo_cmd(ctx: click.Context, keep: bool) -> None:
    """Run the full pipeline against a throwaway copy of the bundled sample repo.

    The pipeline rewrites files in place, so pointing it at the committed
    fixture would spend the demo after one run -- and a converted fixture has
    nothing left to convert. This copies the sample repo to ./logs/demo-repo
    first, so the demo is repeatable and the fixture is never touched.

    Example:

        cartograph demo
    """
    try:
        source = find_sample_repo()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    target = Path("logs") / "demo-repo"
    if target.exists() and not keep:
        _force_rmtree(target)
    if not target.exists():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".git"))

    before = (target / "tests" / "test_legacy_unittest.py").read_text(encoding="utf-8")

    click.echo("Cartograph demo")
    click.echo(f"  sample repo copied to: {target}")
    click.echo("  task: convert unittest tests to pytest")
    click.echo("")
    click.echo("Before (tests/test_legacy_unittest.py):")
    for line in before.splitlines():
        if "self.assert" in line:
            click.echo(f"    {line.strip()}")
    click.echo("")

    ctx.invoke(
        run,
        repo_path=str(target),
        task="convert unittest tests to pytest",
        thread_id=None,
        log_level="INFO",
    )

    after = (target / "tests" / "test_legacy_unittest.py").read_text(encoding="utf-8")
    click.echo("")
    click.echo("After (tests/test_legacy_unittest.py):")
    for line in after.splitlines():
        if line.strip().startswith("assert ") or "pytest.raises" in line:
            click.echo(f"    {line.strip()}")
    click.echo("")
    click.echo(f"Full converted repo: {target.resolve()}")


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


@cli.command(name="review")
@click.option("--base", required=True, help="Git commit/ref to compare with the working tree.")
@click.option("--repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option(
    "--test-path",
    multiple=True,
    default=("tests",),
    help="Pytest path/node ID inside the repo; repeat to specify several.",
)
@click.option("--output", type=click.Path(file_okay=False), default=None)
@click.option("--max-mutations", type=click.IntRange(0, 100), default=5, show_default=True)
@click.option(
    "--timeout",
    type=click.IntRange(1, 600),
    default=60,
    show_default=True,
    help="Seconds per pytest process, including baseline and controls.",
)
@click.option("--fail-on-survivor", is_flag=True, help="Exit 1 when a mutation survives.")
def review_cmd(base, repo, test_path, output, max_mutations, timeout, fail_on_survivor) -> None:
    """Review a Python patch using actual pytest execution and bounded mutations.

    Executes trusted repository tests in disposable source copies. Requires the
    project's test dependencies in this interpreter. Does not edit source files.
    """
    import subprocess
    from collections import Counter

    from cartograph.review.engine import review

    root = Path(repo).resolve()
    destination = Path(output).resolve() if output else root / ".cartograph" / "review"
    click.echo("Reviewing patch; running trusted pytest code in disposable copies...")
    try:
        report = review(
            root, base, destination, list(test_path), max_mutations=max_mutations, timeout=timeout
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise click.ClickException(str(exc)) from exc
    counts = Counter(m.status for m in report.mutations)
    baseline_status = report.baseline.status if report.baseline is not None else "unavailable"
    click.echo(f"Review: {report.status}; baseline: {baseline_status}")
    click.echo(f"Changed functions: {len(report.functions)}; mutations: {dict(counts)}")
    click.echo(f"HTML: {destination / 'report.html'}")
    click.echo(f"JSON: {destination / 'report.json'}")
    click.echo("Coverage is execution evidence. Survivors need review; this is not a safe verdict.")
    if report.status == "baseline_failed":
        raise click.exceptions.Exit(2)
    if fail_on_survivor and counts["survived"]:
        raise click.exceptions.Exit(1)


@cli.command(name="review-demo")
@click.option(
    "--output",
    type=click.Path(file_okay=False),
    required=True,
    help="New directory for two synthetic repos and their reports.",
)
def review_demo_cmd(output: str) -> None:
    """Show a covered mutation surviving weak tests, then detected by a stronger test."""
    from cartograph.review.demo import create_demo

    destination = Path(output).resolve()
    click.echo("Creating synthetic discount examples; running real pytest mutation checks...")
    try:
        reports = create_demo(destination)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    for name, report in reports.items():
        baseline_status = report.baseline.status if report.baseline is not None else "unavailable"
        click.echo(
            f"{name}: baseline {baseline_status}; mutations {[m.status for m in report.mutations]}"
        )
        click.echo(f"  {destination / name / '.cartograph/review/report.html'}")


def _find_provider_error(exc: BaseException) -> ProviderError | None:
    """Find a ProviderError anywhere in `exc`'s cause chain.

    The chain runs ProviderError -> the underlying SDK error, so walking all
    the way to the root would step straight past the one type we can give a
    useful message for.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ProviderError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


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
@click.option(
    "--log-level", default="INFO", show_default=True, help="Logging level for the server."
)
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


@cli.command(name="models")
def models_cmd() -> None:
    """List the Groq model ids this API key can actually use.

    Model ids go stale as providers retire them, which otherwise shows up as
    a 404 partway through a run. Run this and copy one into GROQ_MODEL.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        raise click.ClickException("GROQ_API_KEY is not set; nothing to list.")

    from groq import Groq

    try:
        available = sorted(m.id for m in Groq(api_key=settings.groq_api_key).models.list().data)
    except Exception as exc:
        raise click.ClickException(f"Could not reach Groq: {exc}") from exc

    click.echo(f"GROQ_MODEL is currently {settings.groq_model!r}")
    if settings.groq_model not in available:
        click.echo("  ^ that id is NOT in your key's list -- `run` will fail until you change it.")
    click.echo("")
    click.echo("Available to this key:")
    for model_id in available:
        marker = " <- current" if model_id == settings.groq_model else ""
        click.echo(f"  {model_id}{marker}")


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
            (
                f"llm_tokens:          prompt={call_stats.total_prompt_tokens} "
                f"completion={call_stats.total_completion_tokens} total={call_stats.total_tokens}"
            ),
            f"targets_attempted:   {attempted}",
            f"targets_committed:   {len(committed)} {committed}",
            f"targets_quarantined: {len(quarantined)} {quarantined}",
        ]
    )
    click.echo(summary)


def _force_utf8_output() -> None:
    """Make stdout/stderr UTF-8 so model output can't crash the CLI.

    Windows consoles default to a legacy code page (cp1252), and the agents
    routinely emit arrows, em dashes, and box-drawing characters in their
    reports. Printing one of those would otherwise raise UnicodeEncodeError
    *after* a successful run -- the pipeline works, then the CLI dies on the
    last line. `errors="replace"` keeps that a cosmetic fallback rather than
    a crash on terminals that genuinely can't render the character.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Console-script entrypoint (see [project.scripts] in pyproject.toml)."""
    _force_utf8_output()
    cli()


if __name__ == "__main__":
    main()
