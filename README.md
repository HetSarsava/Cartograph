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
│       │   └── models.py            # Symbol, Edge, SymbolGraph (Pydantic)
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
│       └── cli.py                   # `cartograph run <repo> <task>`
├── tests/
│   └── fixtures/sample_repo/        # tiny throwaway Python repo used by tool tests
└── docs/
    └── architecture.md
```

## Status

This is a scaffolding pass: a clean, importable, test-runnable skeleton —
not a working pipeline yet.

**Implemented (real logic, covered by passing tests):**

- `graph/models.py` — `Symbol`, `Edge`, `SymbolGraph` (Pydantic v2)
- `graph/indexer.py` — `index_repo()`: tree-sitter-based extraction of
  function/class definitions and call edges for a Python repo
- `graph/queries.py` — `find_definition()`, `find_callers()`,
  `blast_radius()` (BFS over the reverse call graph)
- `tools/snapshot.py` — `snapshot()` / `rollback()` via git plumbing
  (commit-tree/read-tree against a throwaway index; never touches HEAD)
- `tools/test_runner.py` — `run_tests()`: shells out to
  `pytest --json-report` and returns structured `TestRunResult`
- `mcp_server/server.py` — all of the above registered as MCP tools via the
  official `mcp` SDK's `FastMCP`
- `pipeline/` — a real, compiled `StateGraph` (with the ambiguity branch and
  the retry-once branch wired up) that runs end to end using the stubbed
  agents below

**Stubbed (signatures, Pydantic schemas, and docstrings are final; bodies
raise `NotImplementedError` or return a hardcoded response — see inline
`# TODO`s):**

- `agents/` — Scout, Planner, Rewriter, Adjudicator, Reporter all have real
  request/response models and are wired into the pipeline, but each returns
  a hardcoded mock response instead of calling the provider
- `tools/context_packer.py` — `pack_context()`: degradation-stage concept
  is documented, loop isn't implemented
- `tools/codemod.py` + `tools/recipes/unittest_to_pytest.py` — the `Recipe`
  protocol and the `apply_codemod()` driver signature are final; the
  libcst rewrite logic isn't implemented
- `providers/groq_provider.py` — implemented against the `groq` SDK, but
  untested end-to-end since no agent calls it yet

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in GROQ_API_KEY
uv run pytest
```
