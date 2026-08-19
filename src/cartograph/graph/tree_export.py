"""Renders a `SymbolGraph` as a single self-contained HTML dependency tree.

This is the simple, static counterpart to `viewer/graph_viewer.html`: no
server, no polling, no event stream. `export_tree_html` bakes the whole tree
into one file as a JSON literal with inline CSS/JS, so it renders correctly
when opened directly via `file://`.

Tree shape:

    file
      class
        method
          -> call        (resolved: labeled with the target's qualified_name)
          -> call        (unresolved: labeled with Edge.target_name)
      function
        -> call ...

Files come first because `files_indexed` is a stable, comprehensible root
layer — unlike picking an arbitrary graph entry point. Under a file,
symbols nest by `Symbol.parent_id` (classes own their methods). Under a
function/method, its outgoing call edges become children, expanded
recursively so you can follow a call chain without re-navigating.

That recursion is bounded three ways, because real call graphs have cycles
and high fan-out:

  * path-based cycle detection — a target already in the current ancestor
    chain renders as a leaf marked "cycle" instead of recursing
  * `MAX_CALL_DEPTH` — caps how deep a single chain expands
  * `MAX_TOTAL_NODES` — a global backstop so a densely-connected repo can't
    produce a multi-megabyte file; expansion stops and the node is marked
    "truncated"
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from cartograph.graph.models import Edge, Symbol, SymbolGraph

MAX_CALL_DEPTH = 5
"""How many levels of call edges to expand beneath a symbol."""

MAX_TOTAL_NODES = 20_000
"""Global cap on emitted nodes, as a backstop against fan-out blowup."""


def export_tree_html(graph: SymbolGraph, output_path: str | Path) -> Path:
    """Render `graph` as a standalone HTML dependency tree at `output_path`.

    The returned file is fully self-contained: all data is embedded as a
    JSON literal in a <script> tag, all CSS/JS is inline. It must render
    correctly when opened directly via file:// with no server running.

    Args:
        graph: The symbol graph to render.
        output_path: Where to write the HTML file. Parent directories are
            created if missing.

    Returns:
        The resolved path to the written file.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tree = _build_tree(graph)
    summary = _build_summary(graph)

    html = _fill_template(
        _HTML_TEMPLATE,
        {
            "__TITLE__": _escape_html(Path(graph.root_path).name or "repo"),
            "__ROOT_PATH__": _escape_html(graph.root_path),
            "__TREE_DATA__": _json_literal(tree),
            "__SUMMARY__": _json_literal(summary),
        },
    )
    path.write_text(html, encoding="utf-8")
    return path.resolve()


# --- data building -------------------------------------------------------


class _Budget:
    """Mutable node counter shared across the recursive build."""

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _build_summary(graph: SymbolGraph) -> dict[str, Any]:
    resolved = sum(1 for edge in graph.edges if edge.target_id is not None)
    return {
        "symbols": len(graph.symbols),
        "edges": len(graph.edges),
        "resolved": resolved,
        "unresolved": len(graph.edges) - resolved,
        "files_indexed": len(graph.files_indexed),
        "files_skipped": len(graph.files_skipped),
    }


def _build_tree(graph: SymbolGraph) -> list[dict[str, Any]]:
    """Build the nested node structure embedded as TREE_DATA."""
    edges_by_source: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        edges_by_source[edge.source_id].append(edge)

    symbols_by_file: dict[str, list[Symbol]] = defaultdict(list)
    children_by_parent: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in graph.symbols.values():
        symbols_by_file[symbol.file_path].append(symbol)
        if symbol.parent_id is not None:
            children_by_parent[symbol.parent_id].append(symbol)

    budget = _Budget(MAX_TOTAL_NODES)
    file_nodes: list[dict[str, Any]] = []

    for file_path in graph.files_indexed:
        symbols = symbols_by_file.get(file_path)
        if not symbols:
            continue
        roots = sorted(
            (s for s in symbols if s.parent_id is None), key=lambda s: s.line_start
        )
        children = [
            node
            for symbol in roots
            if (
                node := _symbol_node(
                    symbol, graph, edges_by_source, children_by_parent, budget
                )
            )
            is not None
        ]
        file_nodes.append(
            {
                "name": file_path,
                "kind": "file",
                "children": children,
            }
        )

    return file_nodes


def _symbol_node(
    symbol: Symbol,
    graph: SymbolGraph,
    edges_by_source: dict[str, list[Edge]],
    children_by_parent: dict[str, list[Symbol]],
    budget: _Budget,
) -> dict[str, Any] | None:
    """Build a structural node for a definition (class / function / method)."""
    if not budget.take():
        return None

    node: dict[str, Any] = {
        # The hierarchy already conveys the enclosing class, so display just
        # the final component (Symbol.name is pre-qualified for methods).
        "name": symbol.qualified_name.split(".")[-1],
        "kind": symbol.kind.value,
        "file_path": symbol.file_path,
        "line_start": symbol.line_start,
        "line_end": symbol.line_end,
    }

    children: list[dict[str, Any]] = []

    # Nested definitions (a class's methods, or a nested class).
    for child in sorted(children_by_parent.get(symbol.id, []), key=lambda s: s.line_start):
        child_node = _symbol_node(child, graph, edges_by_source, children_by_parent, budget)
        if child_node is not None:
            children.append(child_node)

    # Outgoing calls, expanded recursively (bounded — see module docstring).
    for edge in edges_by_source.get(symbol.id, []):
        call_node = _call_node(
            edge,
            graph,
            edges_by_source,
            ancestors=(symbol.id,),
            depth=1,
            source_file=symbol.file_path,
            budget=budget,
        )
        if call_node is not None:
            children.append(call_node)

    if children:
        node["children"] = children
    return node


def _call_node(
    edge: Edge,
    graph: SymbolGraph,
    edges_by_source: dict[str, list[Edge]],
    ancestors: tuple[str, ...],
    depth: int,
    source_file: str,
    budget: _Budget,
) -> dict[str, Any] | None:
    """Build a node for one outgoing call edge, recursing into the target."""
    if not budget.take():
        return None

    target = graph.symbols.get(edge.target_id) if edge.target_id is not None else None

    if target is None:
        # Unresolved: we know a call happened, not what it hit. That's real
        # information, so surface it rather than dropping the edge.
        return {
            "name": edge.target_name,
            "kind": "call",
            "resolved": False,
            "line_start": edge.line,
        }

    node: dict[str, Any] = {
        "name": target.qualified_name,
        "kind": "call",
        "resolved": True,
        "line_start": target.line_start,
        "line_end": target.line_end,
    }
    # Only note the file when the call leaves the current one — otherwise
    # every row repeats the file it's already nested under.
    if target.file_path != source_file:
        node["file_path"] = target.file_path

    if target.id in ancestors:
        node["note"] = "cycle"
        return node
    if depth >= MAX_CALL_DEPTH:
        outgoing = edges_by_source.get(target.id)
        if outgoing:
            node["note"] = "depth limit"
        return node

    children: list[dict[str, Any]] = []
    for next_edge in edges_by_source.get(target.id, []):
        child = _call_node(
            next_edge,
            graph,
            edges_by_source,
            ancestors=(*ancestors, target.id),
            depth=depth + 1,
            source_file=target.file_path,
            budget=budget,
        )
        if child is None:
            node["note"] = "truncated"
            break
        children.append(child)

    if children:
        node["children"] = children
    return node


# --- serialization -------------------------------------------------------


_PLACEHOLDER_RE = re.compile(r"__(?:TITLE|ROOT_PATH|TREE_DATA|SUMMARY)__")


def _fill_template(template: str, values: dict[str, str]) -> str:
    """Substitute every placeholder in one pass.

    Chained `str.replace` calls would rescan already-substituted text, so a
    repo containing one of the placeholder tokens — a file named
    `__SUMMARY__.py`, a root path with `__TREE_DATA__` in it — would have the
    later substitution corrupt the earlier one's JSON and break the page.
    A single regex pass only ever sees the template's own placeholders.
    """
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(0)], template)


def _json_literal(data: Any) -> str:
    """Serialize `data` for safe embedding inside a <script> tag."""
    # A literal "</script>" inside any string would close the tag early;
    # escaping the slash is invisible to JSON.parse but neuters that.
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Cartograph tree — __TITLE__</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --border: #2a2e37;
    --text: #e6e8eb;
    --muted: #8a8f98;
    --accent: #5b8cff;
    --file: #cfd3da;
    --class: #e0a030;
    --function: #4c9f70;
    --method: #4c7cf0;
    --call: #9aa3b0;
    --unresolved: #7a6b6b;
    --hit: #6a4c00;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
  }
  header {
    position: sticky;
    top: 0;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 12px 16px;
    z-index: 10;
  }
  h1 { margin: 0 0 6px; font-size: 15px; font-weight: 600; }
  h1 .path { color: var(--muted); font-weight: 400; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  #summary { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  #summary b { color: var(--text); font-weight: 600; }
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  #filter {
    flex: 1 1 260px;
    min-width: 200px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 9px;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 13px;
  }
  #filter:focus { outline: none; border-color: var(--accent); }
  button {
    background: var(--border);
    border: none;
    color: var(--text);
    padding: 6px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
  }
  button:hover { background: #343945; }
  #count { color: var(--muted); font-size: 12px; }
  main { padding: 10px 16px 40px; }
  /* --- view toggle + diagram --- */
  .viewsel { display: inline-flex; border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
  .viewsel button {
    border: none; border-radius: 0; margin: 0; background: transparent;
    color: var(--muted); padding: 5px 12px; font-size: 12px; cursor: pointer;
  }
  .viewsel button.on { background: var(--panel); color: var(--text); }
  .viewsel button + button { border-left: 1px solid var(--border); }
  #diagram-wrap { display: none; position: relative; height: calc(100vh - 210px); min-height: 340px; }
  #diagram { display: block; width: 100%; height: 100%; cursor: grab; background: var(--bg); }
  #diagram .dnode.foldable { cursor: pointer; }
  #diagram .dnode.foldable:hover circle { stroke-width: 2.5; }
  #diagram-note {
    position: absolute; left: 12px; bottom: 10px; font-size: 11.5px; color: var(--muted);
    background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 4px 10px;
    pointer-events: none;
  }
  #diagram-tools { position: absolute; right: 12px; top: 10px; display: flex; gap: 6px; }
  #diagram-tools button { margin: 0; font-size: 11.5px; padding: 4px 10px; }

  ul { list-style: none; margin: 0; padding-left: 15px; }
  main > ul { padding-left: 0; }
  li.hidden { display: none; }
  .row {
    display: flex;
    align-items: baseline;
    gap: 6px;
    padding: 2px 4px;
    border-radius: 3px;
    white-space: nowrap;
  }
  .row:hover { background: #1c2029; }
  .tw {
    width: 12px;
    flex: 0 0 12px;
    color: var(--muted);
    cursor: pointer;
    user-select: none;
    font-size: 10px;
  }
  .tw.leaf { cursor: default; visibility: hidden; }
  .name {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    cursor: default;
  }
  .badge {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    flex: 0 0 auto;
  }
  .meta { color: var(--muted); font-size: 11px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  .note {
    font-size: 10px;
    color: #d08770;
    border: 1px solid #5a3f36;
    border-radius: 3px;
    padding: 0 4px;
  }
  .k-file .name { color: var(--file); font-weight: 600; }
  .k-class .name { color: var(--class); }
  .k-function .name { color: var(--function); }
  .k-method .name { color: var(--method); }
  .k-call .name { color: var(--call); }
  .k-call.unresolved .name { color: var(--unresolved); font-style: italic; }
  mark { background: var(--hit); color: var(--text); border-radius: 2px; }
  .legend { color: var(--muted); font-size: 11px; margin-top: 8px; display: flex; gap: 12px; flex-wrap: wrap; }
  .legend i { font-style: normal; }
</style>
</head>
<body>
<header>
  <h1>Cartograph dependency tree <span class="path">__ROOT_PATH__</span></h1>
  <div id="summary"></div>
  <div id="controls">
    <input id="filter" type="text" placeholder="Filter by name (substring)…" autocomplete="off" />
    <button id="expand">Expand all</button>
    <button id="collapse">Collapse all</button>
    <span class="viewsel">
      <button id="view-list" class="on">List</button>
      <button id="view-diagram">Diagram</button>
    </span>
    <span id="count"></span>
  </div>
  <div class="legend">
    <i style="color:var(--file)">file</i>
    <i style="color:var(--class)">class</i>
    <i style="color:var(--function)">function</i>
    <i style="color:var(--method)">method</i>
    <i style="color:var(--call)">&rarr; call</i>
    <i style="color:var(--unresolved);font-style:italic">&rarr; unresolved ?</i>
  </div>
</header>
<main>
  <ul id="tree"></ul>
  <div id="diagram-wrap">
    <svg id="diagram"></svg>
    <div id="diagram-tools">
      <button id="diagram-in">+</button>
      <button id="diagram-out">&minus;</button>
      <button id="diagram-fit">Fit</button>
    </div>
    <div id="diagram-note"></div>
  </div>
</main>

<script>
"use strict";
const TREE_DATA = __TREE_DATA__;
const SUMMARY = __SUMMARY__;

document.getElementById("summary").innerHTML =
  "<b>" + SUMMARY.symbols + "</b> symbols &middot; " +
  "<b>" + SUMMARY.edges + "</b> edges (" + SUMMARY.resolved + " resolved, " +
  SUMMARY.unresolved + " unresolved) &middot; " +
  "<b>" + SUMMARY.files_indexed + "</b> files indexed &middot; " +
  SUMMARY.files_skipped + " skipped";

// Rows are built once; filtering only toggles classes on them.
const rows = [];

function isOpenByDefault(kind) {
  // Expanded down to symbol level; a symbol's call children start collapsed,
  // otherwise a real repo opens as a wall of text.
  return kind === "file" || kind === "class";
}

function buildNode(node) {
  const li = document.createElement("li");
  const kids = node.children || [];

  const row = document.createElement("div");
  row.className = "row k-" + node.kind + (node.resolved === false ? " unresolved" : "");

  const tw = document.createElement("span");
  tw.className = "tw" + (kids.length ? "" : " leaf");
  row.appendChild(tw);

  const name = document.createElement("span");
  name.className = "name";
  const prefix = node.kind === "call" ? "\\u2192 " : "";
  name.textContent = prefix + node.name + (node.resolved === false ? " ?" : "");
  row.appendChild(name);

  if (node.kind !== "file" && node.kind !== "call") {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = node.kind;
    row.appendChild(badge);
  }

  const metaBits = [];
  if (node.file_path && node.kind === "call") metaBits.push(node.file_path);
  if (node.line_start) {
    metaBits.push(node.line_end && node.line_end !== node.line_start
      ? ":" + node.line_start + "-" + node.line_end
      : ":" + node.line_start);
  }
  if (metaBits.length) {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = metaBits.join(" ");
    row.appendChild(meta);
  }

  if (node.note) {
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = node.note;
    row.appendChild(note);
  }

  li.appendChild(row);

  const rec = {
    li: li, row: row, nameEl: name, raw: node.name, kind: node.kind,
    children: [], sub: null, tw: tw
  };
  rows.push(rec);

  if (kids.length) {
    const sub = document.createElement("ul");
    for (const child of kids) rec.children.push(buildNode(child));
    for (const c of rec.children) sub.appendChild(c.li);
    li.appendChild(sub);
    rec.sub = sub;
    setOpen(rec, isOpenByDefault(node.kind));
    tw.addEventListener("click", function () {
      setOpen(rec, rec.sub.style.display === "none");
      syncDiagram();
    });
    row.addEventListener("click", function (e) {
      if (e.target === tw) return;
      setOpen(rec, rec.sub.style.display === "none");
      syncDiagram();
    });
  }
  return rec;
}

function setOpen(rec, open) {
  if (!rec.sub) return;
  rec.sub.style.display = open ? "" : "none";
  rec.tw.textContent = open ? "\\u25be" : "\\u25b8";
}

const treeRoot = document.getElementById("tree");
const roots = TREE_DATA.map(function (n) { return buildNode(n); });
for (const r of roots) treeRoot.appendChild(r.li);
document.getElementById("count").textContent = rows.length + " nodes";

// --- filtering: show a row if it matches or has a matching descendant ---
function clearHighlight(rec) {
  if (rec.nameEl.querySelector("mark")) rec.nameEl.textContent = rec.nameEl.textContent;
}

function applyFilter(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    for (const rec of rows) {
      rec.li.classList.remove("hidden");
      clearHighlight(rec);
    }
    for (const r of roots) resetOpen(r);
    document.getElementById("count").textContent = rows.length + " nodes";
    syncDiagram();
    return;
  }
  let hits = 0;
  function walk(rec) {
    const text = rec.nameEl.textContent;
    const idx = rec.raw.toLowerCase().indexOf(q);
    const self = idx !== -1;
    if (self) hits++;
    let anyChild = false;
    for (const child of rec.children) if (walk(child)) anyChild = true;
    const visible = self || anyChild;
    rec.li.classList.toggle("hidden", !visible);
    // Open the path down to every match so hits aren't buried.
    if (anyChild) setOpen(rec, true);
    if (self) {
      const start = text.toLowerCase().indexOf(q);
      if (start !== -1) {
        rec.nameEl.textContent = "";
        rec.nameEl.appendChild(document.createTextNode(text.slice(0, start)));
        const mk = document.createElement("mark");
        mk.textContent = text.slice(start, start + q.length);
        rec.nameEl.appendChild(mk);
        rec.nameEl.appendChild(document.createTextNode(text.slice(start + q.length)));
      }
    } else {
      clearHighlight(rec);
    }
    return visible;
  }
  for (const r of roots) walk(r);
  document.getElementById("count").textContent = hits + " matching";
  syncDiagram();
}

function resetOpen(rec) {
  setOpen(rec, isOpenByDefault(rec.kind));
  for (const c of rec.children) resetOpen(c);
}

let filterTimer = null;
document.getElementById("filter").addEventListener("input", function (e) {
  const v = e.target.value;
  clearTimeout(filterTimer);
  filterTimer = setTimeout(function () { applyFilter(v); }, 120);
});
document.getElementById("expand").addEventListener("click", function () {
  for (const rec of rows) setOpen(rec, true);
  syncDiagram();
});
document.getElementById("collapse").addEventListener("click", function () {
  for (const rec of rows) setOpen(rec, false);
  for (const r of roots) setOpen(r, true);
  syncDiagram();
});

/* ---------------- diagram view ----------------
   A left-to-right node-link tree over the same records the list builds, so
   the two views share expand state and the active filter with no second
   source of truth: `rec.sub.style.display` says expanded, `.hidden` on the
   <li> says filtered out. Clicking a node calls the list's own setOpen, so
   switching views never loses your place.

   Layout is the classic tidy-tree simplification: x is depth, and a node's y
   is the midpoint of its children (leaves take the next free row). One pass,
   no force simulation, deterministic. */

var VIEW_NS = "http://www.w3.org/2000/svg";
var COL_W = 210;      /* px between depth levels  */
var ROW_H = 20;       /* px between sibling rows  */
var MAX_DRAWN = 3000; /* refuse to draw past this rather than hang the tab */

var view = { k: 1, tx: 0, ty: 0 };
var viewMode = "list";
var lastLayout = null;

function isExpanded(rec) {
  return !!rec.sub && rec.sub.style.display !== "none";
}
function isVisible(rec) {
  return !rec.li.classList.contains("hidden");
}

function layoutDiagram() {
  var nodes = [], links = [], row = 0;

  function visit(rec, depth, parent) {
    if (!isVisible(rec)) return null;
    var me = { rec: rec, depth: depth, x: depth * COL_W, y: 0 };
    nodes.push(me);
    if (parent) links.push({ s: parent, t: me });

    var placed = [];
    if (isExpanded(rec)) {
      for (var i = 0; i < rec.children.length; i++) {
        var child = visit(rec.children[i], depth + 1, me);
        if (child) placed.push(child);
      }
    }
    if (placed.length) {
      me.y = (placed[0].y + placed[placed.length - 1].y) / 2;
    } else {
      me.y = row * ROW_H;
      row += 1;
    }
    return me;
  }

  for (var i = 0; i < roots.length; i++) visit(roots[i], 0, null);
  return { nodes: nodes, links: links, rows: row };
}

function nodeColor(rec) {
  if (rec.kind === "file") return "var(--file)";
  if (rec.kind === "class") return "var(--class)";
  if (rec.kind === "function") return "var(--function)";
  if (rec.kind === "method") return "var(--method)";
  if (rec.kind === "call") {
    return rec.row.classList.contains("unresolved") ? "var(--unresolved)" : "var(--call)";
  }
  return "var(--muted)";
}

function svgEl(name, attrs) {
  var e = document.createElementNS(VIEW_NS, name);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function renderDiagram() {
  var svg = document.getElementById("diagram");
  var note = document.getElementById("diagram-note");
  var layout = layoutDiagram();
  lastLayout = layout;
  svg.innerHTML = "";

  if (!layout.nodes.length) {
    note.textContent = "Nothing to draw — the filter matched no rows.";
    return;
  }
  if (layout.nodes.length > MAX_DRAWN) {
    note.textContent =
      layout.nodes.length + " nodes expanded — too many to draw legibly. " +
      "Collapse some, or filter, and it will render.";
    return;
  }
  note.textContent =
    layout.nodes.length + " nodes drawn · drag to pan, scroll to zoom, click a node to fold";

  var g = svgEl("g", { id: "diagram-g" });
  svg.appendChild(g);

  var linkLayer = svgEl("g", { fill: "none", stroke: "var(--border)", "stroke-width": "1" });
  g.appendChild(linkLayer);

  for (var i = 0; i < layout.links.length; i++) {
    var s = layout.links[i].s, t = layout.links[i].t;
    var mid = (s.x + t.x) / 2;
    linkLayer.appendChild(svgEl("path", {
      d: "M " + s.x + " " + s.y + " C " + mid + " " + s.y + ", " + mid + " " + t.y +
         ", " + t.x + " " + t.y,
    }));
  }

  for (var n = 0; n < layout.nodes.length; n++) {
    var item = layout.nodes[n];
    var rec = item.rec;
    var hasKids = rec.children.length > 0;
    var open = isExpanded(rec);
    var colour = nodeColor(rec);

    var group = svgEl("g", {
      transform: "translate(" + item.x + "," + item.y + ")",
      class: "dnode" + (hasKids ? " foldable" : ""),
    });

    /* A filled dot means "collapsed, there is more under here". */
    group.appendChild(svgEl("circle", {
      r: hasKids ? 4.5 : 3,
      fill: hasKids && !open ? colour : "var(--bg)",
      stroke: colour,
      "stroke-width": "1.5",
    }));

    var label = svgEl("text", {
      x: 9, y: 3.5, "font-size": "11",
      "font-family": "ui-monospace, SFMono-Regular, Consolas, monospace",
      fill: colour,
    });
    var text = rec.raw;
    if (rec.kind === "call") text = "→ " + text;
    label.textContent = text.length > 30 ? text.slice(0, 29) + "…" : text;
    group.appendChild(label);

    if (hasKids && !open) {
      var badge = svgEl("text", {
        x: 9 + Math.min(text.length, 30) * 6.2 + 6, y: 3.5,
        "font-size": "9.5", fill: "var(--muted)",
        "font-family": "ui-monospace, SFMono-Regular, Consolas, monospace",
      });
      badge.textContent = "+" + rec.children.length;
      group.appendChild(badge);
    }

    var tip = svgEl("title", {});
    tip.textContent = rec.raw + (rec.kind ? "  ·  " + rec.kind : "");
    group.appendChild(tip);

    if (hasKids) {
      group.addEventListener("click", foldHandler(rec));
    }
    g.appendChild(group);
  }

  applyTransform();
}

function foldHandler(rec) {
  return function (ev) {
    ev.stopPropagation();
    setOpen(rec, !isExpanded(rec));
    renderDiagram();
  };
}

function applyTransform() {
  var g = document.getElementById("diagram-g");
  if (!g) return;
  g.setAttribute(
    "transform",
    "translate(" + view.tx + "," + view.ty + ") scale(" + view.k + ")"
  );
}

function fitDiagram() {
  if (!lastLayout || !lastLayout.nodes.length) return;
  var svg = document.getElementById("diagram");
  var box = svg.getBoundingClientRect();
  var maxX = 0, maxY = 0;
  for (var i = 0; i < lastLayout.nodes.length; i++) {
    if (lastLayout.nodes[i].x > maxX) maxX = lastLayout.nodes[i].x;
    if (lastLayout.nodes[i].y > maxY) maxY = lastLayout.nodes[i].y;
  }
  maxX += 220; /* room for the widest label */
  maxY += ROW_H;
  var k = Math.min(box.width / Math.max(maxX, 1), box.height / Math.max(maxY, 1), 1);
  view.k = Math.max(k, 0.08);
  view.tx = 24;
  view.ty = 18;
  applyTransform();
}

function setView(mode) {
  viewMode = mode;
  var listOn = mode === "list";
  document.getElementById("tree").style.display = listOn ? "" : "none";
  document.getElementById("diagram-wrap").style.display = listOn ? "none" : "";
  document.getElementById("view-list").classList.toggle("on", listOn);
  document.getElementById("view-diagram").classList.toggle("on", !listOn);
  if (!listOn) {
    renderDiagram();
    fitDiagram();
  }
}

/* Re-render the diagram after anything that changes what should be shown. */
function syncDiagram() {
  if (viewMode === "diagram") renderDiagram();
}

document.getElementById("view-list").addEventListener("click", function () { setView("list"); });
document.getElementById("view-diagram").addEventListener("click", function () { setView("diagram"); });
document.getElementById("diagram-fit").addEventListener("click", fitDiagram);
document.getElementById("diagram-in").addEventListener("click", function () {
  view.k = Math.min(view.k * 1.25, 4); applyTransform();
});
document.getElementById("diagram-out").addEventListener("click", function () {
  view.k = Math.max(view.k / 1.25, 0.05); applyTransform();
});

(function () {
  var svg = document.getElementById("diagram");
  var dragging = false, lastX = 0, lastY = 0;

  svg.addEventListener("mousedown", function (e) {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    svg.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", function () {
    dragging = false; svg.style.cursor = "";
  });
  window.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    view.tx += e.clientX - lastX;
    view.ty += e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    applyTransform();
  });
  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    var box = svg.getBoundingClientRect();
    var px = e.clientX - box.left, py = e.clientY - box.top;
    var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    var next = Math.max(0.05, Math.min(4, view.k * factor));
    /* Keep the point under the cursor fixed while zooming. */
    view.tx = px - (px - view.tx) * (next / view.k);
    view.ty = py - (py - view.ty) * (next / view.k);
    view.k = next;
    applyTransform();
  }, { passive: false });
})();
</script>
</body>
</html>
"""
