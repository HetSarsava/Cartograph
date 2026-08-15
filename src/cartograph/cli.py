"""Command-line entrypoint: `cartograph run <repo> <task>`."""

from __future__ import annotations

import uuid

import click

from cartograph.pipeline.graph import build_graph
from cartograph.pipeline.state import RunState


@click.group()
def cli() -> None:
    """Cartograph: a code-intelligence MCP server and LangGraph migration pipeline."""


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.argument("task", type=str)
@click.option("--thread-id", default=None, help="Checkpointer thread id; random if omitted.")
def run(repo_path: str, task: str, thread_id: str | None) -> None:
    """Run the migration pipeline against REPO_PATH for TASK.

    Example:

        cartograph run ./my-repo "convert unittest tests to pytest"
    """
    graph = build_graph()
    initial_state: RunState = {"repo_path": repo_path, "task_description": task}
    config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}

    final_state = graph.invoke(initial_state, config=config)

    reporter_result = final_state.get("reporter_result")
    if reporter_result is not None:
        click.echo(reporter_result.report_markdown)
    else:
        click.echo("Run did not reach the reporter node.", err=True)


def main() -> None:
    """Console-script entrypoint (see [project.scripts] in pyproject.toml)."""
    cli()


if __name__ == "__main__":
    main()
