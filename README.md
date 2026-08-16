# Cartograph

Cartograph is a code-intelligence MCP server, plus a small LangGraph
migration pipeline that exists to prove the server's tools actually work
against a real repo.

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
│       ├── webui/
│       │   └── server.py            # stdlib-only local UI (`cartograph serve`)
│       └── cli.py                   # run / index / tree / serve
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
- `webui/server.py` — a stdlib-only local UI for indexing a repo from the
  browser (`cartograph serve`)

**Still stubbed:**

- `tools/context_packer.py` — `pack_context()`: the degradation-stage
  concept is documented, the loop raises `NotImplementedError`

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in GROQ_API_KEY
uv run pytest
```

## Common tasks

A `Makefile` covers the day-to-day commands; on Windows without `make`,
`make.ps1` mirrors every target for PowerShell (`.\make.ps1 <target>`).

```bash
make setup       # uv sync
make test        # full pytest suite, verbose
make check       # lint + typecheck + test
make run         # cartograph run against the sample fixture
make serve       # local web UI at http://127.0.0.1:8765
make tree        # standalone HTML dependency tree for the fixture repo
make mcp         # start the MCP server (stdio transport)
make help        # full list
```

## Commands

```bash
cartograph run <repo> "<task>"     # the full migration pipeline
cartograph index <repo>            # index only, print summary counts
cartograph index <repo> --visualize  # + live JSONL event stream to ./logs
cartograph tree <repo>             # one self-contained HTML dependency tree
cartograph serve                   # local web UI for the above
```

`cartograph tree` is the quickest way to see what the indexer found: it
writes a single HTML file you can open by double-clicking, with no server
running. `--visualize` is the live counterpart — it streams events to
`./logs/graph-events-*.jsonl` as indexing happens, for
`viewer/graph_viewer.html` to poll and animate.

`cartograph serve` binds to `127.0.0.1` only. It runs the indexer against
whatever local path you hand it, so it is not safe to expose on a network.
