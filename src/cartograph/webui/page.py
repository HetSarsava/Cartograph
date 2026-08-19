"""The single HTML page served by `cartograph serve`.

Kept out of `server.py` so the request handling stays readable — this is
markup, not logic. No build step, no framework, no external requests: the
page is inlined exactly as written here.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Cartograph</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #0b0d11; --border: #2a2e37;
    --text: #e6e8eb; --muted: #8a8f98; --accent: #5b8cff; --accent-dim: #2d4a8a;
    --ok: #4c9f70; --warn: #d9a441; --err: #d06060;
    --mono: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    --radius: 8px;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text); font-size: 14px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.55; display: flex; flex-direction: column;
  }

  /* --- header --- */
  header {
    display: flex; align-items: center; gap: 18px; padding: 0 20px;
    height: 56px; border-bottom: 1px solid var(--border); background: var(--panel);
    flex: 0 0 auto;
  }
  .brand { display: flex; align-items: baseline; gap: 10px; }
  .brand h1 { font-size: 16px; margin: 0; letter-spacing: -0.01em; }
  .brand .tag { color: var(--muted); font-size: 12.5px; }
  nav { display: flex; gap: 4px; margin-left: 8px; }
  nav button {
    background: none; border: none; color: var(--muted); padding: 7px 14px;
    border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500;
  }
  nav button:hover { color: var(--text); background: #212630; }
  nav button.active { color: var(--text); background: #212630; }
  .spacer { flex: 1; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
    color: var(--muted); border: 1px solid var(--border); border-radius: 999px;
    padding: 4px 11px; white-space: nowrap;
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: 0 0 auto; }
  .dot.on { background: var(--ok); }
  .dot.off { background: var(--warn); }

  /* --- layout --- */
  main { flex: 1 1 auto; overflow-y: auto; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 26px 20px 60px; }
  .panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px 20px; margin-bottom: 16px;
  }
  h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em;
    color: var(--muted); margin: 0 0 14px; font-weight: 600;
  }
  p.lede { margin: 0 0 18px; color: var(--muted); max-width: 70ch; }
  code {
    font-family: var(--mono); font-size: 12.5px; background: var(--panel2);
    border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px;
  }
  .mono { font-family: var(--mono); }
  a { color: var(--accent); }

  /* --- controls --- */
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  input[type=text] {
    flex: 1 1 320px; min-width: 200px; background: var(--panel2);
    border: 1px solid var(--border); color: var(--text); padding: 10px 12px;
    border-radius: 6px; font-family: var(--mono); font-size: 13px;
  }
  input[type=text]:focus { outline: none; border-color: var(--accent); }
  button.btn {
    background: var(--accent); border: 1px solid var(--accent); color: #fff;
    padding: 10px 18px; border-radius: 6px; cursor: pointer; font-size: 13px;
    font-weight: 500; white-space: nowrap;
  }
  button.btn:hover:not(:disabled) { filter: brightness(1.12); }
  button.btn:disabled { opacity: 0.5; cursor: default; }
  button.ghost {
    background: transparent; border: 1px solid var(--border); color: var(--text);
    padding: 7px 13px; border-radius: 6px; cursor: pointer; font-size: 12.5px;
  }
  button.ghost:hover:not(:disabled) { background: #212630; }
  button.ghost:disabled { opacity: 0.45; cursor: default; }
  .hint { color: var(--muted); font-size: 12.5px; margin-top: 10px; }
  kbd {
    font-family: var(--mono); font-size: 11px; border: 1px solid var(--border);
    border-bottom-width: 2px; border-radius: 4px; padding: 1px 5px;
    background: var(--panel2); color: var(--muted);
  }

  /* --- examples --- */
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }
  .card {
    text-align: left; background: var(--panel2); border: 1px solid var(--border);
    border-radius: 6px; padding: 13px 14px; cursor: pointer; color: var(--text);
    font: inherit; transition: border-color .12s, background .12s;
  }
  .card:hover { border-color: var(--accent-dim); background: #10141b; }
  .card .label { font-weight: 600; font-size: 13px; }
  .card .path { color: var(--muted); font-family: var(--mono); font-size: 11.5px; margin: 3px 0; word-break: break-all; }
  .card .desc { color: var(--muted); font-size: 12px; }

  /* --- status / stats --- */
  .status { margin-top: 14px; }
  .box { border: 1px solid var(--border); border-radius: 6px; padding: 13px 15px; background: var(--panel2); }
  .box.ok { border-color: #2f5d45; }
  .box.err { border-color: #5d3434; }
  .box.err .title { color: var(--err); font-weight: 600; margin-bottom: 4px; }
  .stats { display: flex; flex-wrap: wrap; gap: 20px; margin: 12px 0 0; }
  .stat .n { font-size: 19px; font-weight: 650; font-family: var(--mono); line-height: 1.2; }
  .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .spin {
    display: inline-block; width: 12px; height: 12px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin .7s linear infinite; vertical-align: -2px; margin-right: 7px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }


  /* --- inspector --- */
  #inspect { display: none; }
  #inspect.show { display: block; }
  /* No align-items:start -- the list should stretch to the detail panel's
     height instead of stopping short and leaving dead space beside it. */
  .split { display: grid; grid-template-columns: minmax(230px, 260px) minmax(0, 1fr); gap: 20px; }
  /* (narrow-screen overrides live with the results rules below) */

  /* Results: one scroll region, and only vertical. Long names truncate with
     an ellipsis rather than forcing the panel to scroll sideways. */
  /* The wrapper is the grid item and stretches to the row; the list fills
     it absolutely, so a long result set scrolls inside the column rather
     than making the whole row taller than the detail panel. */
  .results-wrap { position: relative; min-height: 440px; }
  .results {
    position: absolute; inset: 0; overflow-y: auto; overflow-x: hidden;
    margin: 0; padding: 0 6px 0 0; list-style: none;
  }
  @media (max-width: 900px) {
    .split { grid-template-columns: minmax(0, 1fr); }
    .results-wrap { min-height: 0; }
    .results { position: static; max-height: 320px; }
  }
  .results li {
    padding: 7px 9px; border-radius: 5px; cursor: pointer; border: 1px solid transparent;
    min-width: 0;
  }
  .results li:hover { background: #10141b; }
  .results li.sel { background: #131a29; border-color: var(--accent-dim); }
  .results .n {
    font-family: var(--mono); font-size: 12.5px; display: flex; align-items: center; gap: 6px;
  }
  .results .n span.t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .results .f {
    color: var(--muted); font-size: 11px; font-family: var(--mono);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .badge {
    font-size: 9.5px; text-transform: uppercase; letter-spacing: .05em; padding: 1px 5px;
    border-radius: 3px; border: 1px solid var(--border); color: var(--muted);
    flex: 0 0 auto; line-height: 1.5;
  }
  .badge.k-class { color: #e0a030; border-color: #5a4520; }
  .badge.k-function { color: #4c9f70; border-color: #2f5d45; }
  .badge.k-method { color: #7ba3ff; border-color: #2d4a8a; }

  /* Detail */
  .detail { min-width: 0; }
  .detail h3 {
    margin: 0 0 3px; font-size: 15px; font-family: var(--mono);
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .detail .where { color: var(--muted); font-family: var(--mono); font-size: 12px; }
  .detail .doc {
    color: var(--muted); font-size: 12.5px; border-left: 2px solid var(--border);
    padding-left: 11px; margin: 11px 0 0; white-space: pre-wrap;
  }

  /* Relationship columns: three equal tracks, no inner scrollbars. Long
     lists cap themselves and offer the rest on demand, because a scroll
     region inside a scroll region inside the page is miserable to use. */
  .cols {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px; margin-top: 18px;
  }
  @media (max-width: 860px) { .cols { grid-template-columns: minmax(0, 1fr); } }
  .col { min-width: 0; }
  .col h4 {
    margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); font-weight: 600;
  }
  .col-head { display: flex; align-items: baseline; gap: 7px; margin-bottom: 3px; }
  .col-head .n { font-family: var(--mono); font-size: 15px; font-weight: 650; color: var(--text); }
  .col-note { color: var(--muted); font-size: 11px; margin: 0 0 8px; }
  .col ul { list-style: none; margin: 0; padding: 0; }
  .col li {
    font-family: var(--mono); font-size: 11.5px; padding: 4px 0;
    border-bottom: 1px solid var(--panel2); min-width: 0;
  }
  .col li:last-child { border-bottom: none; }
  .col li .t {
    display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .col li.link .t { cursor: pointer; color: var(--accent); }
  .col li.link:hover .t { text-decoration: underline; }
  .col li.mut .t { color: var(--muted); }
  .col li .sub {
    display: block; color: var(--muted); font-size: 10.5px; font-family: inherit;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 1px;
  }
  .col .empty { color: var(--muted); font-style: italic; font-size: 12px; padding: 4px 0; }
  .more {
    background: none; border: none; color: var(--accent); font-size: 11.5px;
    padding: 6px 0 0; cursor: pointer; font-family: inherit;
  }
  .more:hover { text-decoration: underline; }

  /* Reason chips: the full sentence lives in the tooltip, so a narrow
     column shows the classification instead of wrapped prose. */
  .rz {
    display: inline-block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .05em;
    padding: 0 5px; border-radius: 3px; border: 1px solid var(--border);
    color: var(--muted); margin-left: 6px; vertical-align: 1px; cursor: help;
  }
  .rz.ambiguous { color: var(--warn); border-color: #5d4c2f; }
  .rz.external { color: var(--muted); }
  .rz.receiver { color: #9a8fbf; border-color: #40395c; }

  .nbhd { margin-top: 18px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel2); }
  .nbhd svg { display: block; width: 100%; height: 340px; }
  .nbhd .cap { padding: 8px 12px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11.5px; }
  .lg { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; }
  .lg i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }

  /* --- insights --- */
  .ins { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; }
  .ins .col ul { max-height: none; }
  .ins .row2 { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
  .ins .row2 .c { color: var(--muted); font-size: 11px; flex: 0 0 auto; }
  .bar { height: 3px; background: var(--accent-dim); border-radius: 2px; margin-top: 2px; }

  /* --- inline viewer --- */
  .viewer { margin-top: 16px; display: none; }
  .viewer.show { display: block; }
  .viewer-bar {
    display: flex; align-items: center; gap: 8px; padding: 9px 12px;
    background: var(--panel); border: 1px solid var(--border);
    border-bottom: none; border-radius: var(--radius) var(--radius) 0 0;
  }
  .viewer-bar .name { font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .viewer iframe {
    display: block; width: 100%; height: 620px; border: 1px solid var(--border);
    border-radius: 0 0 var(--radius) var(--radius); background: var(--bg);
  }
  body.zen header, body.zen .no-zen { display: none; }
  body.zen .wrap { max-width: none; padding: 0; }
  body.zen .viewer iframe { height: calc(100vh - 40px); border-radius: 0; border-left: none; border-right: none; }
  body.zen .viewer-bar { border-radius: 0; }

  /* --- migrate --- */
  .steps { counter-reset: s; margin: 0; padding: 0; list-style: none; }
  .steps li {
    counter-increment: s; position: relative; padding-left: 30px; margin-bottom: 9px;
    color: var(--muted); font-size: 13px;
  }
  .steps li::before {
    content: counter(s); position: absolute; left: 0; top: 1px;
    width: 19px; height: 19px; border-radius: 50%; background: var(--panel2);
    border: 1px solid var(--border); color: var(--muted);
    font-size: 11px; display: grid; place-items: center; font-family: var(--mono);
  }
  .steps li b { color: var(--text); font-weight: 600; }
  .log {
    margin-top: 14px; background: var(--panel2); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px 12px; height: 260px; overflow-y: auto;
    font-family: var(--mono); font-size: 11.5px; line-height: 1.65; display: none;
  }
  .log.show { display: block; }
  .log div { white-space: pre-wrap; word-break: break-word; }
  .log .t-tool { color: var(--accent); }
  .log .t-gate { color: var(--ok); }
  .log .t-warn { color: var(--warn); }
  .log .t-err { color: var(--err); }
  .log .t-dim { color: var(--muted); }

  .diff {
    margin-top: 14px; background: var(--panel2); border: 1px solid var(--border);
    border-radius: 6px; overflow-x: auto; font-family: var(--mono);
    font-size: 12px; line-height: 1.6; display: none;
  }
  .diff.show { display: block; }
  .diff div { padding: 0 12px; white-space: pre; }
  .diff .add { background: rgba(76,159,112,.13); color: #8fd6ae; }
  .diff .del { background: rgba(208,96,96,.13); color: #e29a9a; }
  .diff .hd { color: var(--muted); }

  .report {
    margin-top: 14px; border: 1px solid var(--border); border-radius: 6px;
    padding: 4px 16px 14px; background: var(--panel2); display: none;
  }
  .report.show { display: block; }
  .report h1, .report h2, .report h3 { font-size: 13px; margin: 14px 0 6px; color: var(--text); text-transform: none; letter-spacing: 0; }
  .report ul { margin: 6px 0; padding-left: 20px; }
  .report li { margin: 3px 0; font-size: 13px; }
  .report p { font-size: 13px; margin: 6px 0; }
  .report strong { color: var(--text); }
  .report hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }

  .verdict { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
  .pill {
    display: inline-flex; align-items: center; gap: 7px; border-radius: 999px;
    padding: 5px 13px; font-size: 12.5px; border: 1px solid var(--border);
  }
  .pill.ok { border-color: #2f5d45; color: #8fd6ae; }
  .pill.warn { border-color: #5d4c2f; color: #e0c184; }

  ul.recent { list-style: none; margin: 0; padding: 0; }
  ul.recent li {
    display: flex; align-items: center; gap: 12px; padding: 7px 0;
    border-bottom: 1px solid var(--border);
  }
  ul.recent li:last-child { border-bottom: none; }
  ul.recent .nm { flex: 1; font-family: var(--mono); font-size: 12px; word-break: break-all; }
  ul.recent .when { color: var(--muted); font-size: 11.5px; white-space: nowrap; }
  .empty { color: var(--muted); font-style: italic; font-size: 13px; }
  .tab { display: none; }
  .tab.active { display: block; }
</style>
</head>
<body>

<header class="no-zen">
  <div class="brand">
    <h1>Cartograph</h1>
    <span class="tag">code intelligence for Python repos</span>
  </div>
  <nav>
    <button id="tab-explore" class="active" data-tab="explore">Explore</button>
    <button id="tab-migrate" data-tab="migrate">Migrate</button>
  </nav>
  <div class="spacer"></div>
  <span class="chip" id="keychip" title="Only Migrate needs an API key."><span class="dot" id="keydot"></span><span id="keytext">checking…</span></span>
  <span class="chip mono" id="cwdchip" title="Relative paths resolve from here.">…</span>
</header>

<main>
<div class="wrap">

  <!-- ================= EXPLORE ================= -->
  <section class="tab active" id="explore">
    <div class="panel no-zen">
      <h2>Map a repository</h2>
      <p class="lede">
        Parses every <code>.py</code> file with tree-sitter and builds a call graph &mdash;
        every file, class and function, with each function's outgoing calls resolved to
        where they're defined. No API key needed.
      </p>
      <div class="row">
        <input id="path" type="text" spellcheck="false" autocomplete="off"
               placeholder="src/cartograph   or   C:\\path\\to\\your\\repo" />
        <button class="btn" id="build">Build map</button>
      </div>
      <div class="hint">
        A local directory. <kbd>Enter</kbd> to build, <kbd>/</kbd> to jump to this box.
        Remote git URLs aren't supported &mdash; clone first, then point here.
      </div>
      <div class="status" id="status"></div>
    </div>

    <div class="panel no-zen" id="examples-panel">
      <h2>Or start with an example</h2>
      <div class="cards" id="examples"><div class="empty">Loading&hellip;</div></div>
    </div>

    <div class="panel no-zen" id="inspect">
      <h2>Inspect a symbol</h2>
      <div class="row" style="margin-bottom:14px">
        <input id="q" type="text" spellcheck="false" autocomplete="off"
               placeholder="Search functions, classes, methods&hellip;  (press s)" />
      </div>
      <div class="split">
        <div class="results-wrap"><ul class="results" id="results"></ul></div>
        <div class="detail" id="detail">
          <div class="empty">Pick a symbol to see where it's defined, what it calls,
            who calls it, and what changing it would affect.</div>
        </div>
      </div>
    </div>

    <div class="panel no-zen" id="insights-panel" style="display:none">
      <h2>Insights</h2>
      <div class="ins" id="insights"></div>
    </div>

    <div class="viewer" id="viewer">
      <div class="viewer-bar">
        <span class="name" id="viewer-name"></span>
        <div class="spacer"></div>
        <button class="ghost" id="zen" title="Toggle full screen">Full screen</button>
        <button class="ghost" id="newtab">Open in new tab</button>
        <button class="ghost no-zen" id="closev">Close</button>
      </div>
      <iframe id="frame" title="Dependency map"></iframe>
    </div>

    <div class="panel no-zen">
      <h2>Previously built</h2>
      <ul class="recent" id="recent"><li class="empty">Nothing yet.</li></ul>
    </div>
  </section>

  <!-- ================= MIGRATE ================= -->
  <section class="tab" id="migrate">
    <div class="panel">
      <h2>Automated migration, verified by tests</h2>
      <p class="lede">
        Converts a <code>unittest</code> suite to plain <code>pytest</code> asserts. The model
        never writes code &mdash; it decides <em>what</em> to attempt, and every rewrite goes
        through libcst against a real syntax tree, gated on the test suite.
      </p>
      <ol class="steps">
        <li><b>Index</b> &mdash; build the symbol graph for the repo.</li>
        <li><b>Scout &amp; Plan</b> &mdash; narrow to relevant symbols, order the work.</li>
        <li><b>Rewrite</b> &mdash; snapshot, apply the codemod, run pytest. Roll back if red.</li>
        <li><b>Adjudicate</b> &mdash; anything the codemod isn't sure about gets
            <b>quarantined</b>: left byte-for-byte unchanged for a human.</li>
        <li><b>Report</b> &mdash; summarise what changed and what didn't.</li>
      </ol>
      <div class="row" style="margin-top:16px">
        <button class="btn" id="migrate-run">Run migration</button>
        <span class="hint" style="margin:0" id="migrate-note">
          Runs on a throwaway copy of the bundled sample repo &mdash; your files are never touched.
        </span>
      </div>
      <div class="status" id="migrate-status"></div>
      <div class="log" id="migrate-log"></div>
      <div class="verdict" id="migrate-verdict"></div>
      <div class="diff" id="migrate-diff"></div>
      <div class="report" id="migrate-report"></div>
    </div>

    <div class="panel">
      <h2>Run it on your own repo</h2>
      <p class="lede" style="margin-bottom:10px">
        The UI deliberately won't rewrite an arbitrary directory for you. From a terminal:
      </p>
      <div class="mono" style="font-size:12.5px">
        <code>cartograph run &lt;repo&gt; "convert unittest tests to pytest"</code>
      </div>
      <div class="hint">Commit first &mdash; it edits files in place (with snapshot/rollback per step).</div>
    </div>
  </section>

</div>
</main>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
/* Escapes for BOTH text and attribute contexts. The textContent trick
   leaves quotes alone, and this is interpolated into attributes like
   data-id="${esc(...)}" -- a symbol id embeds a file path, and a quote
   in a filename is legal on Linux and macOS. */
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

let building = false, migrating = false, currentTree = null;

/* ---------------- tabs ---------------- */
document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === b.dataset.tab));
  });
});

/* ---------------- state ---------------- */
async function loadState() {
  let s;
  try { s = await (await fetch("/api/state")).json(); } catch (e) { return; }

  $("cwdchip").textContent = s.cwd;
  $("cwdchip").title = "Relative paths resolve from " + s.cwd;

  const dot = $("keydot");
  dot.className = "dot " + (s.has_api_key ? "on" : "off");
  $("keytext").textContent = s.has_api_key ? "API key set" : "no API key";
  $("keychip").title = s.has_api_key
    ? "GROQ_API_KEY found. Migrate is available."
    : "GROQ_API_KEY is not set. Explore works fine; Migrate needs a key in .env";
  if (!s.has_api_key) {
    $("migrate-run").disabled = true;
    $("migrate-note").innerHTML =
      'Needs <code>GROQ_API_KEY</code> in <code>.env</code> &mdash; copy <code>.env.example</code> and paste a key.';
  }

  const ex = $("examples");
  if (!s.examples.length) {
    ex.innerHTML = '<div class="empty">No example directories found from here.</div>';
  } else {
    ex.innerHTML = s.examples.map((e) => `
      <button class="card" data-path="${esc(e.path)}">
        <div class="label">${esc(e.label)}</div>
        <div class="path">${esc(e.path)}</div>
        <div class="desc">${esc(e.description)}</div>
      </button>`).join("");
    ex.querySelectorAll("button[data-path]").forEach((b) => {
      b.addEventListener("click", () => { $("path").value = b.dataset.path; build(); });
    });
  }
  renderRecent(s.recent);
}

function renderRecent(recent) {
  const el = $("recent");
  if (!recent || !recent.length) { el.innerHTML = '<li class="empty">Nothing yet.</li>'; return; }
  el.innerHTML = recent.map((r) => `
    <li>
      <span class="nm">${esc(r.filename)}</span>
      <span class="when">${esc(r.modified)}</span>
      <button class="ghost" data-open="${esc(r.filename)}">View</button>
    </li>`).join("");
  el.querySelectorAll("button[data-open]").forEach((b) => {
    b.addEventListener("click", () => showTree(b.dataset.open, b.dataset.open));
  });
}

/* ---------------- explore ---------------- */
function showTree(filename, label) {
  currentTree = filename;
  $("frame").src = "/tree/" + encodeURIComponent(filename);
  $("viewer-name").textContent = label || filename;
  $("viewer").classList.add("show");
  $("viewer").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function build() {
  if (building) return;
  const repoPath = $("path").value.trim();
  const status = $("status");
  if (!repoPath) {
    status.innerHTML = '<div class="box err"><div class="title">No path given</div>Type a directory path, or pick an example below.</div>';
    return;
  }

  building = true;
  $("build").disabled = true;
  status.innerHTML = `<div class="box"><span class="spin"></span>Indexing <span class="mono">${esc(repoPath)}</span>&hellip;</div>`;

  let r;
  try {
    const res = await fetch("/api/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath }),
    });
    r = await res.json();
  } catch (e) {
    r = { ok: false, error: "Could not reach the server: " + e.message };
  }

  if (r.ok) {
    status.innerHTML = `
      <div class="box ok">
        <div>Mapped <span class="mono">${esc(r.repo_path)}</span> in ${r.duration_seconds}s</div>
        <div class="stats">
          <div class="stat"><div class="n">${r.symbols}</div><div class="k">symbols</div></div>
          <div class="stat"><div class="n">${r.edges}</div><div class="k">call edges</div></div>
          <div class="stat"><div class="n">${r.resolved}</div><div class="k">resolved</div></div>
          <div class="stat"><div class="n">${r.unresolved}</div><div class="k">unresolved</div></div>
          <div class="stat"><div class="n">${r.files_indexed}</div><div class="k">files</div></div>
          ${r.files_skipped ? `<div class="stat"><div class="n">${r.files_skipped}</div><div class="k">skipped</div></div>` : ""}
        </div>
      </div>`;
    showTree(r.tree_filename, r.repo_path);
    $("inspect").classList.add("show");
    selectedId = null;
    $("q").value = "";
    search("");
    loadInsights();
    loadState();
  } else {
    status.innerHTML = `<div class="box err"><div class="title">Couldn't build that</div>${esc(r.error || "Unknown error.")}</div>`;
    $("viewer").classList.remove("show");
  }

  building = false;
  $("build").disabled = false;
}

/* ---------------- inspector ---------------- */
let symbols = [], selectedId = null, searchTimer = null;

const LIST_CAP = 10;

function kindBadge(k) { return '<span class="badge k-' + esc(k) + '">' + esc(k) + "</span>"; }

async function search(q) {
  let data;
  try { data = await (await fetch("/api/symbols?q=" + encodeURIComponent(q))).json(); }
  catch (e) { return; }
  symbols = data.results || [];
  const el = $("results");
  if (!symbols.length) {
    el.innerHTML = '<li class="empty">No symbol matches that.</li>';
    return;
  }
  el.innerHTML = symbols.map((s) => `
    <li data-id="${esc(s.id)}" class="${s.id === selectedId ? "sel" : ""}"
        title="${esc(s.qualified_name)}  &#183;  ${esc(s.file_path)}:${s.line_start}">
      <div class="n"><span class="t">${esc(s.qualified_name)}</span>${kindBadge(s.kind)}</div>
      <div class="f">${esc(s.file_path)}:${s.line_start}</div>
    </li>`).join("");
  el.querySelectorAll("li[data-id]").forEach((li) => {
    li.addEventListener("click", () => selectSymbol(li.dataset.id));
  });
}

/* A list that caps itself instead of growing an inner scrollbar. */
function cappedList(items, render, empty) {
  if (!items.length) return '<div class="empty">' + esc(empty) + "</div>";
  const head = items.slice(0, LIST_CAP).map(render).join("");
  const rest = items.slice(LIST_CAP);
  let html = '<ul class="capped">' + head;
  if (rest.length) {
    html += '<span class="hidden-rest" hidden>' + rest.map(render).join("") + "</span>";
  }
  html += "</ul>";
  if (rest.length) html += '<button class="more">Show ' + rest.length + " more</button>";
  return html;
}

function refRow(s) {
  return '<li class="link" data-goto="' + esc(s.id) + '" title="' + esc(s.qualified_name) +
    "  ·  " + esc(s.file_path) + '">' +
    '<span class="t">' + esc(s.qualified_name) + "</span>" +
    '<span class="sub">' + esc(s.file_path) + "</span></li>";
}

const REASON_LABEL = { ambiguous: "ambiguous", external: "external", "unknown receiver": "receiver" };

function callRow(c) {
  if (c.resolved) {
    return '<li class="link" data-goto="' + esc(c.id) + '" title="' + esc(c.name) +
      "  ·  " + esc(c.file_path) + '">' +
      '<span class="t">' + esc(c.name) + "</span>" +
      '<span class="sub">line ' + c.line + " · " + esc(c.file_path) + "</span></li>";
  }
  const w = c.why || {};
  const cls = REASON_LABEL[w.reason] || "external";
  /* The explanation is long prose; it belongs in a tooltip, not wrapped
     into a narrow column three words at a time. */
  return '<li class="mut" title="' + esc(w.detail || "unresolved") + '">' +
    '<span class="t">' + esc(c.name) +
    '<span class="rz ' + esc(cls) + '">' + esc(w.reason || "unresolved") + "</span></span>" +
    '<span class="sub">line ' + c.line + "</span></li>";
}

async function selectSymbol(id) {
  selectedId = id;
  document.querySelectorAll("#results li[data-id]").forEach((li) =>
    li.classList.toggle("sel", li.dataset.id === id));

  let d;
  try { d = await (await fetch("/api/symbol?id=" + encodeURIComponent(id))).json(); }
  catch (e) { return; }
  if (d.error) { $("detail").innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }

  const resolved = d.calls.filter((c) => c.resolved).length;

  $("detail").innerHTML = `
    <h3><span>${esc(d.qualified_name)}</span>${kindBadge(d.kind)}</h3>
    <div class="where">${esc(d.file_path)}:${d.line_start}&ndash;${d.line_end}</div>
    ${d.docstring ? `<div class="doc">${esc(d.docstring.trim().slice(0, 280))}</div>` : ""}

    <div class="nbhd">
      <svg id="nbhd-svg" viewBox="0 0 640 340" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="cap">
        <span class="lg"><i style="background:#5b8cff"></i>this symbol</span>
        <span class="lg"><i style="background:#d9a441"></i>callers</span>
        <span class="lg"><i style="background:#4c9f70"></i>callees</span>
        &mdash; arrows point caller &rarr; callee, both ends when they call each other.
        Drag to rearrange, double-click to jump.
      </div>
    </div>

    <div class="cols">
      <div class="col">
        <div class="col-head"><span class="n">${d.blast_radius.length}</span><h4>Blast radius</h4></div>
        <p class="col-note">Everything that transitively reaches this.</p>
        ${cappedList(d.blast_radius, refRow, "Nothing depends on this.")}
      </div>
      <div class="col">
        <div class="col-head"><span class="n">${d.callers.length}</span><h4>Called by</h4></div>
        <p class="col-note">Direct callers, one hop.</p>
        ${cappedList(d.callers, refRow, "No direct callers found.")}
      </div>
      <div class="col">
        <div class="col-head"><span class="n">${d.calls.length}</span><h4>Calls</h4></div>
        <p class="col-note">${resolved} in this repo, ${d.calls.length - resolved} outside it.</p>
        ${cappedList(d.calls, callRow, "Calls nothing.")}
      </div>
    </div>`;

  $("detail").querySelectorAll("li[data-goto]").forEach((li) => {
    li.addEventListener("click", () => selectSymbol(li.dataset.goto));
  });
  $("detail").querySelectorAll("button.more").forEach((b) => {
    b.addEventListener("click", () => {
      const hidden = b.previousElementSibling.querySelector(".hidden-rest");
      if (!hidden) return;
      hidden.removeAttribute("hidden");
      hidden.querySelectorAll("li[data-goto]").forEach((li) => {
        li.addEventListener("click", () => selectSymbol(li.dataset.goto));
      });
      b.remove();
    });
  });

  drawNeighbourhood(d.neighbourhood, d.id);
}

/* Force-directed mini-graph: repulsion + springs + centring, settled over a
   fixed number of ticks so no animation loop is left running. */
function drawNeighbourhood(nb, centreId) {
  const svg = $("nbhd-svg");
  if (!svg || !nb || !nb.nodes.length) return;
  const W = 640, H = 340, NS = "http://www.w3.org/2000/svg";
  const COLOR = { centre: "#5b8cff", caller: "#d9a441", callee: "#4c9f70" };

  const nodes = nb.nodes.map((n, i) => {
    const a = (2 * Math.PI * i) / nb.nodes.length;
    return Object.assign({}, n, {
      x: W / 2 + Math.cos(a) * 110, y: H / 2 + Math.sin(a) * 80, vx: 0, vy: 0,
    });
  });
  const byId = {};
  nodes.forEach((n) => { byId[n.id] = n; });
  const links = nb.edges.filter((e) => byId[e.source] && byId[e.target]);

  for (let step = 0; step < 260; step++) {
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy || 0.01;
        const d = Math.sqrt(d2);
        const rep = 5200 / d2;
        a.vx += (dx / d) * rep; a.vy += (dy / d) * rep;
      }
      a.vx += (W / 2 - a.x) * 0.012;
      a.vy += (H / 2 - a.y) * 0.012;
    }
    for (const l of links) {
      const s = byId[l.source], t = byId[l.target];
      const dx = t.x - s.x, dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 95) * 0.02;
      s.vx += (dx / d) * f; s.vy += (dy / d) * f;
      t.vx -= (dx / d) * f; t.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      n.x += n.vx * 0.55; n.y += n.vy * 0.55;
      n.vx *= 0.82; n.vy *= 0.82;
      n.x = Math.max(30, Math.min(W - 30, n.x));
      n.y = Math.max(20, Math.min(H - 20, n.y));
    }
  }

  const el = (name, attrs) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  };
  svg.innerHTML = "";
  const defs = el("defs", {});
  /* markerUnits defaults to strokeWidth, which on a 1px line renders an
     arrowhead about 5px across in the same grey as the line -- present, but
     invisible in practice. Pin it to userSpaceOnUse and give it its own
     lighter fill so direction actually reads.
     orient="auto-start-reverse" lets the one marker serve both ends: at the
     start it flips, which is what a mutual call needs. */
  const marker = el("marker", {
    id: "arw", viewBox: "0 0 10 10", refX: "10", refY: "5",
    markerWidth: "9", markerHeight: "9", markerUnits: "userSpaceOnUse",
    orient: "auto-start-reverse",
  });
  marker.appendChild(el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#6b7a90" }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  const lineEls = [];
  function endpoints(l) {
    /* Stop the line short of both circles so the arrowhead sits beside the
       node rather than buried under it. */
    const s = byId[l.source], t = byId[l.target];
    const dx = t.x - s.x, dy = t.y - s.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const ux = dx / len, uy = dy / len;
    const rs = (s.id === centreId ? 11 : 8) + (l.both ? 9 : 0);
    const rt = (t.id === centreId ? 11 : 8) + 9;
    return {
      x1: s.x + ux * rs, y1: s.y + uy * rs,
      x2: t.x - ux * rt, y2: t.y - uy * rt,
    };
  }

  for (const l of links) {
    const p = endpoints(l);
    const ln = el("line", {
      x1: p.x1, y1: p.y1, x2: p.x2, y2: p.y2,
      stroke: l.both ? "#6b7a90" : "#3f4857",
      "stroke-width": l.both ? "1.6" : "1.2",
      "marker-end": "url(#arw)",
    });
    if (l.both) ln.setAttribute("marker-start", "url(#arw)");
    const lt = el("title", {});
    lt.textContent = l.both
      ? byId[l.source].full + "  <->  " + byId[l.target].full + "   (call each other)"
      : byId[l.source].full + "  calls  " + byId[l.target].full;
    ln.appendChild(lt);
    lineEls.push(ln);
    svg.appendChild(ln);
  }

  function redrawLines() {
    links.forEach(function (l, i) {
      const p = endpoints(l);
      lineEls[i].setAttribute("x1", p.x1); lineEls[i].setAttribute("y1", p.y1);
      lineEls[i].setAttribute("x2", p.x2); lineEls[i].setAttribute("y2", p.y2);
    });
  }

  for (const n of nodes) {
    const g = el("g", { transform: "translate(" + n.x + "," + n.y + ")", style: "cursor:grab" });
    g.appendChild(el("circle", {
      r: n.id === centreId ? 9 : 6, fill: COLOR[n.role] || "#8a8f98",
      stroke: "#0f1115", "stroke-width": "2",
    }));
    const label = el("text", {
      y: -13, "text-anchor": "middle", fill: "#e6e8eb",
      "font-size": "10", "font-family": "ui-monospace, Consolas, monospace",
    });
    label.textContent = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label;
    g.appendChild(label);
    const title = el("title", {});
    title.textContent = n.full + "  ·  " + n.file_path;
    g.appendChild(title);

    g.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      const box = svg.getBoundingClientRect();
      const sx = W / box.width, sy = H / box.height;
      const move = (m) => {
        n.x = Math.max(30, Math.min(W - 30, (m.clientX - box.left) * sx));
        n.y = Math.max(20, Math.min(H - 20, (m.clientY - box.top) * sy));
        g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
        redrawLines();
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
    g.addEventListener("dblclick", () => selectSymbol(n.id));
    svg.appendChild(g);
  }
}

/* ---------------- insights ---------------- */
function listBlock(title, items, render, note) {
  return `<div class="col"><h4>${esc(title)}</h4>` +
    (note ? `<div class="why" style="padding:0 0 6px">${esc(note)}</div>` : "") +
    "<ul>" + (items.length ? items.map(render).join("") : '<li class="mut">None.</li>') + "</ul></div>";
}

async function loadInsights() {
  let i;
  try { i = await (await fetch("/api/insights")).json(); } catch (e) { return; }
  if (!i || i.error) return;
  const maxCalled = Math.max(1, ...i.most_called.map((r) => r.count));
  const maxFile = Math.max(1, ...i.busiest_files.map((r) => r.count));

  $("insights").innerHTML =
    listBlock("Most referenced", i.most_called, (r) =>
      `<li class="link" data-goto="${esc(r.id)}"><div class="row2"><span>${esc(r.qualified_name)}</span>` +
      `<span class="c">${r.count}</span></div>` +
      `<div class="bar" style="width:${(r.count / maxCalled) * 100}%"></div></li>`,
      "Change these carefully.") +
    listBlock("Never referenced", i.never_called, (r) =>
      `<li class="link" data-goto="${esc(r.id)}">${esc(r.qualified_name)}` +
      `<span class="why">${esc(r.file_path)}</span></li>`,
      i.never_called_total + " total — entry points, dead code, or dynamic dispatch.") +
    listBlock("Longest", i.biggest, (r) =>
      `<li class="link" data-goto="${esc(r.id)}"><div class="row2"><span>${esc(r.qualified_name)}</span>` +
      `<span class="c">${r.lines} ln</span></div></li>`,
      "Candidates for splitting up.") +
    listBlock("Busiest files", i.busiest_files, (r) =>
      `<li class="mut"><div class="row2"><span>${esc(r.file_path)}</span>` +
      `<span class="c">${r.count}</span></div>` +
      `<div class="bar" style="width:${(r.count / maxFile) * 100}%"></div></li>`) +
    listBlock("Most external calls", i.top_unresolved, (r) =>
      `<li class="mut"><div class="row2"><span>${esc(r.name)}</span>` +
      `<span class="c">${r.count}</span></div></li>`,
      "Called but not defined here — stdlib or dependencies.");

  $("insights").querySelectorAll("li[data-goto]").forEach((li) => {
    li.addEventListener("click", () => {
      selectSymbol(li.dataset.goto);
      $("inspect").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  $("insights-panel").style.display = "";
}

$("q").addEventListener("input", (e) => {
  const v = e.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => search(v), 130);
});

$("build").addEventListener("click", build);
$("path").addEventListener("keydown", (e) => { if (e.key === "Enter") build(); });
$("closev").addEventListener("click", () => $("viewer").classList.remove("show"));
$("newtab").addEventListener("click", () => {
  if (currentTree) window.open("/tree/" + encodeURIComponent(currentTree), "_blank", "noopener");
});
$("zen").addEventListener("click", () => {
  document.body.classList.toggle("zen");
  $("zen").textContent = document.body.classList.contains("zen") ? "Exit full screen" : "Full screen";
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (document.body.classList.contains("zen")) { document.body.classList.remove("zen"); $("zen").textContent = "Full screen"; }
    else $("viewer").classList.remove("show");
  }
  const typing = /^(INPUT|TEXTAREA)$/.test((e.target.tagName || ""));
  if (e.key === "/" && !typing) { e.preventDefault(); $("path").focus(); $("path").select(); }
  if (e.key === "s" && !typing && $("inspect").classList.contains("show")) {
    e.preventDefault(); $("q").focus(); $("q").select();
  }
});

/* ---------------- migrate ---------------- */
const TAG_CLASS = [
  [/\\[tool:/, "t-tool"], [/\\[gate:/, "t-gate"], [/\\[provider:/, "t-dim"],
  [/quarantin/i, "t-warn"], [/error|failed|crash/i, "t-err"],
];

function logLine(text) {
  const el = $("migrate-log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  const d = document.createElement("div");
  let cls = "t-dim";
  for (const [re, c] of TAG_CLASS) { if (re.test(text)) { cls = c; break; } }
  d.className = cls;
  d.textContent = text;
  el.appendChild(d);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function renderDiff(diffText) {
  const el = $("migrate-diff");
  if (!diffText) { el.classList.remove("show"); return; }
  el.innerHTML = diffText.split("\\n").map((l) => {
    let c = "";
    if (l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@")) c = "hd";
    else if (l.startsWith("+")) c = "add";
    else if (l.startsWith("-")) c = "del";
    return `<div class="${c}">${esc(l) || "&nbsp;"}</div>`;
  }).join("");
  el.classList.add("show");
}

/* Minimal markdown -> HTML for the report. Headings, lists, bold, code, rules. */
function md(src) {
  const lines = String(src || "").split("\\n");
  let out = "", inList = false;
  const inline = (s) => esc(s)
    .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (let raw of lines) {
    const l = raw.trim();
    const li = l.match(/^[-*]\\s+(.*)$/);
    if (li) { if (!inList) { out += "<ul>"; inList = true; } out += "<li>" + inline(li[1]) + "</li>"; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const h = l.match(/^(#{1,6})\\s+(.*)$/);
    if (h) { out += "<h3>" + inline(h[2]) + "</h3>"; continue; }
    if (/^(-{3,}|\\*{3,})$/.test(l)) { out += "<hr />"; continue; }
    if (l) out += "<p>" + inline(l) + "</p>";
  }
  if (inList) out += "</ul>";
  return out;
}

async function runMigration() {
  if (migrating) return;
  migrating = true;
  $("migrate-run").disabled = true;
  $("migrate-log").innerHTML = "";
  $("migrate-log").classList.add("show");
  $("migrate-diff").classList.remove("show");
  $("migrate-report").classList.remove("show");
  $("migrate-verdict").innerHTML = "";
  $("migrate-status").innerHTML = '<div class="box"><span class="spin"></span>Running the pipeline&hellip;</div>';

  let jobId;
  try {
    const res = await fetch("/api/migrate", { method: "POST" });
    const j = await res.json();
    if (!j.job_id) throw new Error(j.error || "could not start");
    jobId = j.job_id;
  } catch (e) {
    $("migrate-status").innerHTML = `<div class="box err"><div class="title">Couldn't start</div>${esc(e.message)}</div>`;
    migrating = false; $("migrate-run").disabled = false;
    return;
  }

  let seen = 0;
  while (true) {
    await new Promise((r) => setTimeout(r, 450));
    let j;
    try { j = await (await fetch("/api/job/" + jobId)).json(); }
    catch (e) { continue; }

    (j.lines || []).slice(seen).forEach(logLine);
    seen = (j.lines || []).length;

    if (j.status === "running") continue;

    if (j.status === "error") {
      $("migrate-status").innerHTML =
        `<div class="box err"><div class="title">Run failed</div>${esc(j.error || "Unknown error.")}</div>`;
    } else {
      const r = j.result || {};
      $("migrate-status").innerHTML =
        `<div class="box ok">Finished in ${r.duration_seconds}s &mdash; ${r.llm_calls} LLM calls, ${r.llm_tokens} tokens.</div>`;
      $("migrate-verdict").innerHTML =
        `<span class="pill ok">${r.committed_count} rewritten &amp; tests green</span>` +
        `<span class="pill warn">${r.quarantined_count} quarantined &mdash; left unchanged</span>`;
      renderDiff(r.diff);
      if (r.report_markdown) {
        $("migrate-report").innerHTML = md(r.report_markdown);
        $("migrate-report").classList.add("show");
      }
    }
    break;
  }

  migrating = false;
  $("migrate-run").disabled = false;
}

$("migrate-run").addEventListener("click", runMigration);

loadState();
</script>
</body>
</html>
"""
