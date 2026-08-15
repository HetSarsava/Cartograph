# Architecture

Placeholder — to be filled in as the real logic lands.

## Planned sections

- **Symbol graph**: how `graph/indexer.py` builds it with tree-sitter, and
  the resolution rules `graph/queries.py` uses (and where they're
  best-effort rather than exact).
- **MCP server**: the tool surface in `mcp_server/server.py`, and why query
  tools are stateless (take a `SymbolGraph` in, return one out) rather than
  caching server-side.
- **Pipeline**: the LangGraph `StateGraph` in `pipeline/graph.py` — node
  responsibilities, the ambiguity branch, the retry-once branch, and how
  `RunState` flows through them.
- **Agents**: what each of Scout / Planner / Rewriter / Adjudicator /
  Reporter is asked to do, its input/output contracts, and the prompting
  strategy once it's implemented (currently stubbed — see `README.md`
  Status section).
- **Codemods**: the `Recipe` protocol, the ambiguity-reporting contract, and
  how `apply_codemod` decides what to write back.
- **Provider abstraction**: why `providers/` exists, and what it takes to
  add a second provider.
