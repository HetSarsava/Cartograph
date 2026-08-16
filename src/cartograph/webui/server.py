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
    POST /api/build         {"repo_path": ...} -> BuildResult JSON
    GET  /api/state         examples + recent trees + cwd, for page load
    GET  /tree/<filename>   a previously generated tree from ./logs
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

from pydantic import BaseModel, Field

from cartograph.graph.indexer import index_repo
from cartograph.graph.tree_export import export_tree_html

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

    return UiState(cwd=str(Path.cwd()), examples=examples, recent=recent)


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

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/" or self.path.startswith("/?"):
                self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
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
            if self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._send(404, b"Not found", "text/plain")
        except Exception:  # noqa: BLE001 - a bad request must not kill the server
            logger.exception("[webui] GET %s failed", self.path)
            self._send(500, b"Internal error", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        try:
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


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Cartograph</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --border: #2a2e37; --text: #e6e8eb;
    --muted: #8a8f98; --accent: #5b8cff; --ok: #4c9f70; --err: #d06060;
    --mono: ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text); font-size: 14px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.55;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 28px; }
  section { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 18px 20px; margin-bottom: 18px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 0 0 14px; }
  ol.steps { margin: 0; padding-left: 20px; }
  ol.steps li { margin-bottom: 10px; }
  code, .mono { font-family: var(--mono); font-size: 12.5px; }
  code { background: #0b0d11; border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; }
  pre { background: #0b0d11; border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; overflow-x: auto; margin: 8px 0 0; }
  pre code { background: none; border: none; padding: 0; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; }
  input[type=text] {
    flex: 1 1 340px; min-width: 240px; background: #0b0d11; border: 1px solid var(--border);
    color: var(--text); padding: 10px 12px; border-radius: 6px; font-family: var(--mono); font-size: 13px;
  }
  input[type=text]:focus { outline: none; border-color: var(--accent); }
  button {
    background: var(--accent); border: none; color: #fff; padding: 10px 18px;
    border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500;
  }
  button:hover:not(:disabled) { filter: brightness(1.1); }
  button:disabled { opacity: 0.55; cursor: default; }
  button.ghost { background: transparent; border: 1px solid var(--border); color: var(--text); font-weight: 400; }
  button.ghost:hover:not(:disabled) { background: #212630; filter: none; }
  .examples { display: grid; gap: 10px; }
  .ex { display: flex; align-items: center; justify-content: space-between; gap: 14px;
        border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
  .ex .meta { min-width: 0; }
  .ex .label { font-weight: 600; }
  .ex .path { color: var(--muted); font-family: var(--mono); font-size: 12px; }
  .ex .desc { color: var(--muted); font-size: 12.5px; }
  .hint { color: var(--muted); font-size: 12.5px; margin-top: 10px; }
  #status { margin-top: 14px; display: none; }
  #status.show { display: block; }
  .card { border: 1px solid var(--border); border-radius: 6px; padding: 14px; background: #0b0d11; }
  .card.ok { border-color: #2f5d45; }
  .card.err { border-color: #5d3434; }
  .stats { display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 12px; font-family: var(--mono); font-size: 12.5px; }
  .stats b { color: var(--text); }
  .stats span { color: var(--muted); }
  .spin { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--border);
          border-top-color: var(--accent); border-radius: 50%; animation: s 0.7s linear infinite; vertical-align: -2px; }
  @keyframes s { to { transform: rotate(360deg); } }
  a { color: var(--accent); }
  ul.recent { list-style: none; margin: 0; padding: 0; }
  ul.recent li { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); }
  ul.recent li:last-child { border-bottom: none; }
  ul.recent .when { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .empty { color: var(--muted); font-style: italic; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Cartograph</h1>
  <p class="sub">Index a Python repo and get a standalone dependency tree you can open and share.</p>

  <section>
    <h2>How to run</h2>
    <ol class="steps">
      <li>Install dependencies once: <code>uv sync</code></li>
      <li>Start this UI: <code>uv run cartograph serve</code> &mdash; you're looking at it.</li>
      <li>Enter a repository path below, or click an example. Relative paths resolve against
          <span class="mono" id="cwd">the launch directory</span>.</li>
      <li>Press <b>Build tree</b>. Cartograph parses every <code>.py</code> file with tree-sitter,
          resolves call edges, and writes one self-contained HTML file.</li>
      <li>Click <b>Open tree</b>. The file is fully standalone &mdash; move it, email it, open it
          later with no server running.</li>
    </ol>
    <div class="hint">Prefer the terminal? These do the same thing:</div>
    <pre><code>uv run cartograph tree &lt;repo&gt;              # same standalone tree
uv run cartograph index &lt;repo&gt; --visualize  # live event stream for viewer/graph_viewer.html</code></pre>
  </section>

  <section>
    <h2>Build a tree</h2>
    <div class="row">
      <input id="path" type="text" placeholder="e.g. src/cartograph  or  C:\\path\\to\\your\\repo" autocomplete="off" />
      <button id="build">Build tree</button>
    </div>
    <div class="hint">A local directory path. Remote git URLs aren't supported yet &mdash; clone first, then point here.</div>
    <div id="status"></div>
  </section>

  <section>
    <h2>Examples</h2>
    <div class="examples" id="examples"><div class="empty">Loading&hellip;</div></div>
  </section>

  <section>
    <h2>Recent trees</h2>
    <ul class="recent" id="recent"><li class="empty">None yet.</li></ul>
  </section>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
let building = false;

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

async function loadState() {
  let state;
  try {
    state = await (await fetch("/api/state")).json();
  } catch (e) {
    return;
  }
  $("cwd").textContent = state.cwd;

  const ex = $("examples");
  if (!state.examples.length) {
    ex.innerHTML = '<div class="empty">No example repos found from this directory.</div>';
  } else {
    ex.innerHTML = state.examples.map((e, i) => `
      <div class="ex">
        <div class="meta">
          <div class="label">${esc(e.label)}</div>
          <div class="path">${esc(e.path)}</div>
          <div class="desc">${esc(e.description)}</div>
        </div>
        <button class="ghost" data-path="${esc(e.path)}">Run this</button>
      </div>`).join("");
    ex.querySelectorAll("button[data-path]").forEach((b) => {
      b.addEventListener("click", () => {
        $("path").value = b.dataset.path;
        build();
      });
    });
  }
  renderRecent(state.recent);
}

function renderRecent(recent) {
  const el = $("recent");
  if (!recent || !recent.length) {
    el.innerHTML = '<li class="empty">None yet.</li>';
    return;
  }
  el.innerHTML = recent.map((r) => `
    <li>
      <a href="/tree/${encodeURIComponent(r.filename)}" target="_blank" rel="noopener">${esc(r.filename)}</a>
      <span class="when">${esc(r.modified)}</span>
    </li>`).join("");
}

async function build() {
  if (building) return;
  const repoPath = $("path").value.trim();
  const status = $("status");
  if (!repoPath) {
    status.className = "show";
    status.innerHTML = '<div class="card err">Enter a repository path first.</div>';
    return;
  }

  building = true;
  $("build").disabled = true;
  status.className = "show";
  status.innerHTML = `<div class="card"><span class="spin"></span> Indexing <span class="mono">${esc(repoPath)}</span>&hellip;</div>`;

  let r;
  try {
    const res = await fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath }),
    });
    r = await res.json();
  } catch (e) {
    r = { ok: false, error: "Could not reach the server: " + e.message };
  }

  if (r.ok) {
    status.innerHTML = `
      <div class="card ok">
        <div>Built from <span class="mono">${esc(r.repo_path)}</span> in ${r.duration_seconds}s</div>
        <div class="stats">
          <span><b>${r.symbols}</b> symbols</span>
          <span><b>${r.edges}</b> edges</span>
          <span>${r.resolved} resolved / ${r.unresolved} unresolved</span>
          <span><b>${r.files_indexed}</b> files</span>
          <span>${r.files_skipped} skipped</span>
        </div>
        <a href="/tree/${encodeURIComponent(r.tree_filename)}" target="_blank" rel="noopener">
          <button>Open tree &rarr;</button>
        </a>
        <div class="hint">Saved to <span class="mono">${esc(r.tree_path)}</span></div>
      </div>`;
    loadState();
  } else {
    status.innerHTML = `<div class="card err">${esc(r.error || "Build failed.")}</div>`;
  }

  building = false;
  $("build").disabled = false;
}

$("build").addEventListener("click", build);
$("path").addEventListener("keydown", (e) => { if (e.key === "Enter") build(); });
loadState();
</script>
</body>
</html>
"""
