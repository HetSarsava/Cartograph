# Cartograph

Cartograph is a code-intelligence MCP server, plus a small LangGraph
migration pipeline that exists to prove the server's tools actually work
against a real repo.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # install
uv run cartograph tree src/cartograph    # no API key needed
```

That writes a standalone HTML map of the codebase you can open by
double-clicking. For the full migration pipeline, add a Groq key:

```bash
cp .env.example .env                     # paste your key from console.groq.com/keys
uv run cartograph demo                   # migrate a bundled sample repo, end to end
```

`cartograph demo` works on a throwaway copy, so you can run it as many times
as you like and always see the same before/after.

## What you can use it for

**1. Understand an unfamiliar Python repo.**
`cartograph tree <repo>` parses every `.py` file with tree-sitter and gives
you one self-contained HTML page: every file, class, and function, with each
function's outgoing calls nested underneath it and resolved to their
definitions. No server, no API key, no account. Useful for onboarding, code
review prep, or working out what a dependency actually does.

**2. Answer "what breaks if I change this?"**
The symbol graph backs `find_definition`, `find_callers`, and
`blast_radius` (a BFS over the reverse call graph). These are exposed over
MCP, so any MCP client — Claude Code included — can call them instead of
grepping and guessing.

**3. Run a mechanical migration you can actually trust.**
`cartograph run <repo> "<task>"` migrates a codebase with an LLM in the
loop for judgment only. The rewrites themselves go through libcst against a
real syntax tree, every change is gated on the test suite, and anything the
codemod isn't certain about is **quarantined** — left byte-for-byte
unchanged for a human — rather than guessed at. The bundled example converts
`unittest` assertions to plain `pytest` asserts.

**4. Give a coding agent better tools.**
`cartograph` is an MCP server first. `uv run python -m
cartograph.mcp_server.server` exposes indexing, the graph queries, codemods,
git snapshot/rollback, and a structured pytest wrapper to any MCP client.

## The problem

Coding agents typically understand a repo the way a person would with only
`grep` and `cat`: they read files, guess at what calls what, and either burn
a huge amount of context re-reading source to stay oriented, or miss a
caller three files away and break something they never looked at. Neither
failure mode is really about the model being weak — it's that the model is
doing structural work (parse this file, find every caller of this function,
check whether these tests still pass) that a deterministic tool could do
faster, more cheaply, and without hallucinating.

## The approach

Cartograph pushes the structural understanding and the verification into
tools, and keeps the LLM's job narrow: make small, bounded decisions given
context a tool already assembled. A tree-sitter-built symbol graph answers
"who calls this" and "what breaks if I change it" without a model in the
loop. `libcst`-based codemods rewrite code through a real syntax tree, and
report *ambiguity* explicitly instead of guessing. A pytest wrapper returns
structured pass/fail/error results instead of terminal output an agent has
to parse. All of it is exposed over MCP, so it's usable by any MCP client —
not just the LangGraph pipeline in this repo, which exists mainly as a
worked example: Scout narrows down what's relevant, Planner sequences the
work, Rewriter executes one step at a time against real test feedback,
Adjudicator steps in only when a codemod reports genuine ambiguity, and
Reporter summarizes what happened.

## Directory structure

```
cartograph/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── cartograph/
│       ├── config.py                # env/config loading, provider keys, budgets
│       ├── providers/               # thin, swappable LLM provider abstraction
│       │   ├── base.py              # Provider protocol
│       │   └── groq_provider.py     # Groq implementation
│       ├── graph/                   # read-only symbol graph
│       │   ├── indexer.py           # index_repo() — tree-sitter symbol graph builder
│       │   ├── queries.py           # find_definition, find_callers, blast_radius
│       │   ├── models.py            # Symbol, Edge, SymbolGraph (Pydantic)
│       │   ├── events.py            # IndexEvent schema for index_repo's event stream
│       │   ├── event_sink.py        # JsonlEventSink — writes events as JSONL
│       │   └── tree_export.py       # export_tree_html() — standalone HTML tree
│       ├── tools/                   # deterministic tools, exposed via MCP
│       │   ├── context_packer.py    # pack_context() — token-budgeted context assembly
│       │   ├── codemod.py           # apply_codemod() driver + Recipe protocol
│       │   ├── recipes/
│       │   │   └── unittest_to_pytest.py
│       │   ├── test_runner.py       # run_tests() — structured pytest wrapper
│       │   └── snapshot.py          # snapshot() / rollback() via git plumbing
│       ├── mcp_server/
│       │   └── server.py            # registers tools/ and graph/queries as MCP tools
│       ├── agents/                  # LLM-backed agents (Provider in, Pydantic out)
│       │   ├── scout.py             # narrows candidate symbols to relevant ones
│       │   ├── planner.py           # turns relevant symbols into an ordered plan
│       │   ├── rewriter.py          # executes one plan step, retries against tests
│       │   ├── adjudicator.py       # resolves ambiguities codemods can't
│       │   └── reporter.py          # summarizes a completed run
│       ├── pipeline/                # the LangGraph StateGraph
│       │   ├── state.py             # RunState TypedDict
│       │   ├── nodes.py             # node functions wrapping agents/tools
│       │   └── graph.py             # builds and compiles the StateGraph
│       ├── webui/                  # stdlib-only local UI (`cartograph serve`)
│       │   ├── server.py            # routes and request handling
│       │   ├── page.py              # the single HTML page
│       │   └── jobs.py              # background runs with a pollable live log
│       └── cli.py                   # demo / run / index / tree / serve / models
├── viewer/
│   └── graph_viewer.html            # live viewer for --visualize event streams
├── tests/
│   └── fixtures/sample_repo/        # tiny throwaway Python repo used by tool tests
└── docs/
    └── architecture.md
```

## Status

The pipeline runs end to end for real: it indexes a repo, plans a migration,
applies libcst codemods, verifies with pytest, and reports — calling Groq at
each agent step.

**Implemented (real logic, covered by passing tests):**

- `graph/models.py` — `Symbol`, `Edge`, `SymbolGraph` (Pydantic v2)
- `graph/indexer.py` — `index_repo()`: tree-sitter-based extraction of
  function/class definitions and call edges, with an optional event stream
  (`on_event`) for watching an index run happen
- `graph/queries.py` — `find_definition()`, `find_callers()`,
  `blast_radius()` (BFS over the reverse call graph)
- `graph/tree_export.py` — `export_tree_html()`: renders a graph as one
  self-contained HTML file (cycle-, depth-, and size-bounded)
- `tools/snapshot.py` — `snapshot()` / `rollback()` via git plumbing
  (commit-tree/read-tree against a throwaway index; never touches HEAD)
- `tools/test_runner.py` — `run_tests()`: shells out to
  `pytest --json-report` and returns structured `TestRunResult`
- `tools/codemod.py` + `tools/recipes/unittest_to_pytest.py` — libcst
  rewriting of unittest assertions to plain `assert`, reporting *ambiguity*
  rather than guessing. Targets resolving outside the repo root are refused.
- `agents/` — Scout, Planner, Rewriter, Adjudicator, Reporter, all calling
  Groq through `Provider` and validating responses into Pydantic models
- `mcp_server/server.py` — all of the above registered as MCP tools via the
  official `mcp` SDK
- `pipeline/` — a compiled `StateGraph` with the ambiguity and retry-once
  branches wired up, plus a per-step LLM-call budget so an unresolvable
  ambiguity can't loop
- `webui/` — a stdlib-only local UI: index a repo from the browser and
  watch a migration run live (`cartograph serve`)

**Still stubbed:**

- `tools/context_packer.py` — `pack_context()`: the degradation-stage
  concept is documented, the loop raises `NotImplementedError`

## Setup notes

Only `run` and `demo` need `GROQ_API_KEY`; `index`, `tree`, and `serve` work
without one. If a run fails with a 404 on the model id, the provider has
retired it — `uv run cartograph models` lists what your key can currently
use, and you set the winner as `GROQ_MODEL` in `.env`.

## Common tasks

A `Makefile` covers the day-to-day commands; on Windows without `make`,
`make.ps1` mirrors every target for PowerShell (`.\make.ps1 <target>`).

```bash
make setup       # uv sync
make test        # full pytest suite, verbose
make check       # lint + typecheck + test
make demo        # full pipeline on a throwaway copy of the sample repo
make run         # cartograph run against the sample fixture
make serve       # local web UI at http://127.0.0.1:8765
make tree        # standalone HTML dependency tree for the fixture repo
make mcp         # start the MCP server (stdio transport)
make help        # full list
```

## Commands

```bash
cartograph demo                    # the whole pipeline on a bundled sample repo
cartograph run <repo> "<task>"     # the full migration pipeline
cartograph index <repo>            # index only, print summary counts
cartograph index <repo> --visualize  # + live JSONL event stream to ./logs
cartograph tree <repo>             # one self-contained HTML dependency tree
cartograph serve                   # local web UI for the above
cartograph models                  # list Groq model ids your key can use
```

`cartograph tree` is the quickest way to see what the indexer found: it
writes a single HTML file you can open by double-clicking, with no server
running. `--visualize` is the live counterpart — it streams events to
`./logs/graph-events-*.jsonl` as indexing happens, for
`viewer/graph_viewer.html` to poll and animate.

### The web UI

`cartograph serve` is the easiest way in — one page, two tabs:

- **Explore** — type a path or click an example, and the dependency map
  renders *inline* (full-screen toggle, or pop it into its own tab). Every
  map you build stays listed under "Previously built" so you can flip
  between them. Press <kbd>/</kbd> to jump to the path box, <kbd>Enter</kbd>
  to build, <kbd>Esc</kbd> to close the viewer.
- **Migrate** — runs the sample migration end to end with the pipeline's own
  log streaming live, then shows the unified diff, a committed/quarantined
  verdict, and the agent's report. It works on a throwaway copy, so it never
  touches your files; the header shows whether your API key is set, and the
  tab explains what to do if it isn't.

It binds to `127.0.0.1` only. It runs the indexer against whatever local
path you hand it, so it is not safe to expose on a network. It deliberately
won't rewrite an arbitrary directory — that stays a `cartograph run` decision
you make in a terminal.
