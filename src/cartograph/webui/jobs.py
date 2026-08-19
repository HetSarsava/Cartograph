"""Background jobs for the web UI, with a live log the browser can poll.

The migration pipeline takes ten-odd seconds and the interesting part is
*watching it work* — the snapshot, the codemod, the test gate, the
quarantine decision. So it runs on a worker thread while the page polls
`/api/job/<id>` for new log lines.

Deliberately tiny: one job at a time, kept in memory, no persistence. This
is single-user local tooling, not a job queue.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Only messages from these loggers reach the UI. The rest (HTTP chatter,
# library internals) would drown the pipeline's own narration.
_UI_LOG_SOURCES = (
    "cartograph.pipeline",
    "cartograph.agents",
    "cartograph.providers",
)


@dataclass
class Job:
    """One background run and everything the page needs to render it."""

    id: str
    status: str = "running"  # running | done | error
    lines: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class _JobLogHandler(logging.Handler):
    """Feeds pipeline log records into a job's line buffer."""

    def __init__(self, job: Job, lock: threading.Lock) -> None:
        super().__init__(level=logging.INFO)
        self._job = job
        self._lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith(_UI_LOG_SOURCES):
            return
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not break the run
            return
        with self._lock:
            self._job.lines.append(message)


class JobRunner:
    """Runs at most one background job, exposing its progress by id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: str | None = None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            # Hand back a snapshot: the worker thread keeps mutating the real
            # one while the response is being serialized.
            return Job(
                id=job.id,
                status=job.status,
                lines=list(job.lines),
                result=job.result,
                error=job.error,
            )

    def busy(self) -> bool:
        with self._lock:
            if self._active is None:
                return False
            active = self._jobs.get(self._active)
            return active is not None and active.status == "running"

    def start(self, target) -> str:
        """Run `target(job, lock)` on a worker thread; return the job id."""
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
            self._active = job.id
            # Keep memory flat over a long-lived server.
            if len(self._jobs) > 20:
                for stale in sorted(self._jobs)[:10]:
                    if self._jobs[stale].status != "running":
                        del self._jobs[stale]

        def worker() -> None:
            handler = _JobLogHandler(job, self._lock)
            root = logging.getLogger("cartograph")
            # The live log is the whole point of watching a run, so make sure
            # records actually reach us: a child's .info() is dropped at the
            # source if the effective level is higher, which would leave the
            # UI showing an empty log under `serve --log-level WARNING`.
            previous_level = root.level
            if not root.isEnabledFor(logging.INFO):
                root.setLevel(logging.INFO)
            root.addHandler(handler)
            try:
                result = target(job, self._lock)
                with self._lock:
                    job.result = result
                    job.status = "done"
            except Exception as exc:
                logger.exception("[webui] job %s failed", job.id)
                with self._lock:
                    job.error = str(exc)
                    job.status = "error"
            finally:
                root.removeHandler(handler)
                root.setLevel(previous_level)

        threading.Thread(target=worker, daemon=True, name=f"cartograph-job-{job.id}").start()
        return job.id


def find_sample_repo() -> Path:
    """Locate the bundled sample repo by walking up from this file.

    Counting `parents[n]` is easy to get wrong and silently breaks when a
    module moves between directories, so search for the directory instead.

    Raises:
        RuntimeError: If it isn't found (e.g. a wheel install without the
            test fixtures).
    """
    relative = Path("tests") / "fixtures" / "sample_repo"
    for base in Path(__file__).resolve().parents:
        candidate = base / relative
        if candidate.is_dir():
            return candidate
    raise RuntimeError(
        "Bundled sample repo not found. It ships in the git checkout at "
        "tests/fixtures/sample_repo; run this from a clone rather than an "
        "installed wheel."
    )


def run_migration_demo(job: Job, lock: threading.Lock, logs_dir: Path) -> dict[str, Any]:
    """Migrate a throwaway copy of the bundled sample repo, end to end.

    Copies first, deliberately: the UI must never rewrite a directory the
    user didn't explicitly hand to the CLI, and copying also makes the demo
    repeatable instead of single-use.
    """
    import difflib

    from langchain_core.runnables import RunnableConfig

    from cartograph.pipeline.graph import build_graph
    from cartograph.pipeline.state import RunState
    from cartograph.providers.groq_provider import call_stats

    def say(text: str) -> None:
        with lock:
            job.lines.append(text)

    source = find_sample_repo()
    target = logs_dir / "demo-repo"
    if target.exists():
        _force_rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    say(f"[setup] copied sample repo to {target}")

    demo_file = target / "tests" / "test_legacy_unittest.py"
    before = demo_file.read_text(encoding="utf-8")

    call_stats.reset()
    task = "convert unittest tests to pytest"
    initial: RunState = {
        "repo_path": str(target),
        "task_description": task,
        "thread_id": f"webui-{job.id}",
    }
    config: RunnableConfig = {"configurable": {"thread_id": f"webui-{job.id}"}}

    start = time.monotonic()
    final_state = build_graph().invoke(initial, config=config)
    duration = time.monotonic() - start

    after = demo_file.read_text(encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/tests/test_legacy_unittest.py",
            tofile="b/tests/test_legacy_unittest.py",
        )
    )

    committed = final_state.get("committed_symbol_ids", [])
    quarantined = final_state.get("quarantined_symbol_ids", [])
    reporter_result = final_state.get("reporter_result")
    say(f"[done] {len(committed)} committed, {len(quarantined)} quarantined")

    return {
        "duration_seconds": round(duration, 1),
        "llm_calls": call_stats.call_count,
        "llm_tokens": call_stats.total_tokens,
        "committed_count": len(committed),
        "quarantined_count": len(quarantined),
        "committed": committed,
        "quarantined": quarantined,
        "diff": diff,
        "repo_path": str(target),
        "report_markdown": reporter_result.report_markdown if reporter_result else "",
    }


def _force_rmtree(path: Path) -> None:
    """Delete `path`, including git's read-only object files (Windows)."""
    import os
    import stat

    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=on_error)
