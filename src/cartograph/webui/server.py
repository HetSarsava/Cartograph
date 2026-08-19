"""A small local web UI for running the indexer and opening the result.

Deliberately built on the standard library's `http.server` — no new
dependency, no framework, no build step, consistent with the rest of the
project. It exists because a browser page cannot run `index_repo` against
your filesystem on its own: the page collects a repo path, this server does
the actual work, and hands back a standalone tree HTML file.

Binds to 127.0.0.1 only. It indexes whatever local path you hand it and
serves files back out of `./logs`, which is fine as single-user local dev
tooling but is *not* safe to expose on a network — hence the hard-coded
loopback host and the filename validation in `safe_tree_path`.

Routes:
    GET  /                  the UI page
    GET  /api/state         examples, recent trees, cwd, whether a key is set
    POST /api/build         {"repo_path": ...} -> BuildResult JSON (sync)
    POST /api/migrate       start the migration demo -> {"job_id": ...}
    GET  /api/job/<id>      poll a background job's status and log lines
    GET  /api/symbols?q=    search the last built graph
    GET  /api/symbol?id=    one symbol: callers, calls, blast radius
    GET  /api/insights      repo-level hotspots from the last built graph
    GET  /tree/<filename>   a previously generated tree from ./logs

The page itself lives in `page.py` and background work in `jobs.py`, so this
module stays about request handling.
"""

from __future__ import annotations

import errno
import json
import logging
import re
import sys
import time
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from cartograph.config import get_settings
from cartograph.graph.indexer import index_repo
from cartograph.graph.tree_export import export_tree_html
from cartograph.webui.inspect import (
    cache_graph,
    compute_insights,
    get_graph,
    search_symbols,
    symbol_detail,
)
from cartograph.webui.jobs import JobRunner, run_migration_demo
from cartograph.webui.page import PAGE

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765
LOGS_DIR = Path("logs")

_TREE_NAME_RE = re.compile(r"^tree-[0-9A-Za-z_\-]+\.html$")
"""Generated tree filenames only — no separators, no traversal."""

_EXAMPLE_CANDIDATES: list[tuple[str, str, str]] = [
    (
        "Sample fixture repo",
        "tests/fixtures/sample_repo",
        "Tiny 4-file package — a couple of functions calling each other.",
    ),
    (
        "Cartograph's own source",
        "src/cartograph",
        "This project indexing itself — a realistic mid-size example.",
    ),
]


class BuildResult(BaseModel):
    """Outcome of one index-and-export request from the UI."""

    ok: bool
    repo_path: str
    error: str | None = None
    tree_filename: str | None = Field(
        default=None, description="Basename under ./logs, for the /tree/<name> route."
    )
    tree_path: str | None = Field(default=None, description="Absolute path on disk.")
    symbols: int = 0
    edges: int = 0
    resolved: int = 0
    unresolved: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    duration_seconds: float = 0.0


class Example(BaseModel):
    """A one-click example repo, offered only when the path actually exists."""

    label: str
    path: str
    description: str


class RecentTree(BaseModel):
    """A previously generated tree file still sitting in ./logs."""

    filename: str
    modified: str


class UiState(BaseModel):
    """Everything the page needs on load."""

    cwd: str
    examples: list[Example]
    recent: list[RecentTree]
    has_api_key: bool = False
    """Whether Migrate is usable. Explore never needs a key."""


def build_tree(repo_path: str, logs_dir: Path = LOGS_DIR) -> BuildResult:
    """Index `repo_path` and write a standalone tree HTML into `logs_dir`.

    Never raises for ordinary user error (missing path, path is a file, an
    unreadable repo) — those come back as `BuildResult(ok=False, error=...)`
    so the UI can show them inline instead of the server 500ing.

    Args:
        repo_path: Directory to index. Relative paths resolve against the
            process CWD, which is whatever directory `cartograph serve` was
            launched from (the UI displays it so this isn't ambiguous).
        logs_dir: Where to write the generated HTML.

    Returns:
        A `BuildResult` describing the run, or the failure.
    """
    # Path("") and Path("   ") resolve to the CWD, so an empty box would
    # quietly index the whole directory `serve` was launched from.
    if not repo_path.strip():
        return BuildResult(ok=False, repo_path=repo_path, error="No repository path given")

    candidate = Path(repo_path.strip()).expanduser()
    if not candidate.exists():
        return BuildResult(ok=False, repo_path=repo_path, error=f"Path does not exist: {candidate}")
    if not candidate.is_dir():
        return BuildResult(
            ok=False, repo_path=repo_path, error=f"Not a directory: {candidate}"
        )

    start = time.monotonic()
    try:
        graph = index_repo(candidate)
    except Exception as exc:  # noqa: BLE001 - surface any indexer failure in the UI
        logger.exception("[webui] indexing failed for %s", candidate)
        return BuildResult(ok=False, repo_path=repo_path, error=f"Indexing failed: {exc}")

    filename = _unique_tree_filename(logs_dir)
    try:
        written = export_tree_html(graph, logs_dir / filename)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[webui] tree export failed for %s", candidate)
        return BuildResult(ok=False, repo_path=repo_path, error=f"Export failed: {exc}")

    cache_graph(str(candidate.resolve()), graph)

    resolved = sum(1 for edge in graph.edges if edge.target_id is not None)
    return BuildResult(
        ok=True,
        repo_path=str(candidate),
        tree_filename=filename,
        tree_path=str(written),
        symbols=len(graph.symbols),
        edges=len(graph.edges),
        resolved=resolved,
        unresolved=len(graph.edges) - resolved,
        files_indexed=len(graph.files_indexed),
        files_skipped=len(graph.files_skipped),
        duration_seconds=round(time.monotonic() - start, 2),
    )


def _unique_tree_filename(logs_dir: Path) -> str:
    """Pick a tree filename that doesn't already exist in `logs_dir`.

    The timestamp is only second-resolution, so two builds started within
    the same second would otherwise write to the same file and the second
    would silently clobber the first — easy to trigger by clicking two
    examples in quick succession. Suffix with a counter when that happens.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"tree-{timestamp}"
    candidate = f"{base}.html"
    counter = 2
    while (logs_dir / candidate).exists():
        candidate = f"{base}-{counter}.html"
        counter += 1
    return candidate


def safe_tree_path(filename: str, logs_dir: Path = LOGS_DIR) -> Path | None:
    """Resolve `filename` to a tree file inside `logs_dir`, or None if unsafe.

    Guards the `/tree/<filename>` route against path traversal: the name must
    look like a generated tree filename, and the resolved path must actually
    sit directly inside `logs_dir`.
    """
    if not _TREE_NAME_RE.match(filename):
        return None
    resolved = (logs_dir / filename).resolve()
    if resolved.parent != logs_dir.resolve():
        return None
    if not resolved.is_file():
        return None
    return resolved


def discover_examples(logs_dir: Path = LOGS_DIR) -> UiState:
    """Collect page-load state: cwd, existing example repos, recent trees."""
    examples = [
        Example(label=label, path=path, description=desc)
        for label, path, desc in _EXAMPLE_CANDIDATES
        if Path(path).is_dir()
    ]

    recent: list[RecentTree] = []
    if logs_dir.is_dir():
        files = sorted(
            (p for p in logs_dir.glob("tree-*.html") if _TREE_NAME_RE.match(p.name)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        recent = [
            RecentTree(
                filename=p.name,
                modified=datetime.fromtimestamp(p.stat().st_mtime, UTC).strftime(
                    "%Y-%m-%d %H:%M UTC"
                ),
            )
            for p in files[:10]
        ]

    return UiState(
        cwd=str(Path.cwd()),
        examples=examples,
        recent=recent,
        has_api_key=bool(get_settings().groq_api_key),
    )


_jobs = JobRunner()


class _Handler(BaseHTTPRequestHandler):
    server_version = "Cartograph"

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default stderr spam
        logger.debug("[webui] " + fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: BaseModel) -> None:
        self._send(status, payload.model_dump_json().encode("utf-8"), "application/json")

    def _send_dict(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/" or self.path.startswith("/?"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/api/state":
                self._send_json(200, discover_examples())
                return
            if self.path.startswith("/tree/"):
                name = self.path[len("/tree/") :].split("?", 1)[0]
                target = safe_tree_path(name)
                if target is None:
                    self._send(404, b"Not found", "text/plain")
                    return
                self._send(200, target.read_bytes(), "text/html; charset=utf-8")
                return
            if self.path.startswith("/api/symbols"):
                query = parse_qs(urlparse(self.path).query)
                graph = get_graph()
                if graph is None:
                    self._send_dict(409, {"error": "Build a map first."})
                    return
                self._send_dict(
                    200,
                    {"results": search_symbols(graph, (query.get("q") or [""])[0])},
                )
                return
            if self.path.startswith("/api/symbol?"):
                query = parse_qs(urlparse(self.path).query)
                graph = get_graph()
                if graph is None:
                    self._send_dict(409, {"error": "Build a map first."})
                    return
                detail = symbol_detail(graph, (query.get("id") or [""])[0])
                if detail is None:
                    self._send_dict(404, {"error": "No such symbol."})
                    return
                self._send_dict(200, detail)
                return
            if self.path == "/api/insights":
                graph = get_graph()
                if graph is None:
                    self._send_dict(409, {"error": "Build a map first."})
                    return
                self._send_dict(200, compute_insights(graph))
                return
            if self.path.startswith("/api/job/"):
                job_id = self.path[len("/api/job/") :].split("?", 1)[0]
                job = _jobs.get(job_id)
                if job is None:
                    self._send_dict(404, {"status": "error", "error": "No such job"})
                    return
                self._send_dict(
                    200,
                    {
                        "status": job.status,
                        "lines": job.lines,
                        "result": job.result,
                        "error": job.error,
                    },
                )
                return
            if self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._send(404, b"Not found", "text/plain")
        except Exception:  # noqa: BLE001 - a bad request must not kill the server
            logger.exception("[webui] GET %s failed", self.path)
            self._send(500, b"Internal error", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/api/migrate":
                if not get_settings().groq_api_key:
                    self._send_dict(400, {"error": "GROQ_API_KEY is not set."})
                    return
                if _jobs.busy():
                    self._send_dict(409, {"error": "A migration is already running."})
                    return
                logger.info("[webui] starting migration demo")
                job_id = _jobs.start(
                    lambda job, lock: run_migration_demo(job, lock, LOGS_DIR)
                )
                self._send_dict(200, {"job_id": job_id})
                return
            if self.path != "/api/build":
                self._send(404, b"Not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, BuildResult(ok=False, repo_path="", error="Invalid JSON body"))
                return

            repo_path = (payload.get("repo_path") or "").strip()
            if not repo_path:
                self._send_json(
                    400, BuildResult(ok=False, repo_path="", error="No repository path given")
                )
                return

            logger.info("[webui] building tree for %s", repo_path)
            result = build_tree(repo_path)
            self._send_json(200 if result.ok else 400, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[webui] POST %s failed", self.path)
            self._send_json(500, BuildResult(ok=False, repo_path="", error=str(exc)))


class PortInUseError(RuntimeError):
    """Raised by `bind` when the requested port is already taken."""


class _UiServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` that won't silently share a port on Windows.

    `http.server` sets `allow_reuse_address = 1`, which means SO_REUSEADDR.
    On POSIX that only permits rebinding a port stuck in TIME_WAIT, which is
    what you want. On Windows it permits binding a port another process is
    *actively listening on* — so a second `cartograph serve` would start
    successfully and the two servers would then compete for connections,
    with requests landing on whichever one the OS picked. Turning it off on
    Windows makes that case a clean bind failure instead.
    """

    allow_reuse_address = sys.platform != "win32"


def bind(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Bind the UI server on 127.0.0.1:`port` without serving yet.

    Split out from `serve` so a caller can find out whether the port is
    actually available *before* telling the user the UI is up.

    Raises:
        PortInUseError: If something is already listening on `port`.
        OSError: For any other bind failure.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return _UiServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        # Windows reports a taken port as WSAEACCES/EACCES rather than
        # EADDRINUSE, so match on either.
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            raise PortInUseError(
                f"Port {port} is already in use — is `cartograph serve` "
                f"already running? Try --port {port + 1}."
            ) from exc
        raise


def serve(
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    httpd: ThreadingHTTPServer | None = None,
) -> None:
    """Run the UI on 127.0.0.1:`port` until interrupted.

    Args:
        port: TCP port to bind on loopback. Ignored if `httpd` is given.
        open_browser: Whether to try to open the page automatically.
        httpd: An already-bound server from `bind`. Pass one when you need to
            confirm the port was free before printing anything to the user.
    """
    if httpd is None:
        httpd = bind(port)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - never fail the server over this
            logger.debug("[webui] could not open a browser automatically")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
