# Cartograph architecture

Cartograph's patch review joins Git changes, Tree-sitter functions, per-test
coverage and bounded mutations into an evidence report. MCP is its integration
boundary; LangGraph remains a separate small migration example.

```mermaid
flowchart LR
    Host["AI host + MCP client"] -->|"JSON-RPC 2.0 over stdio"| SDK["Official MCP SDK"]
    SDK --> Tools["Nine Cartograph tools"]
    Tools --> Index["tree-sitter symbol graph"]
    Tools --> Actions["libcst / pytest / Git"]
    Workflow["Minimal LangGraph workflow"] -->|"ordinary Python calls"| Actions
    Workflow --> Index
    Workflow --> Groq["Groq for selection, planning, advice and reporting"]
    Review["review CLI"] --> Index
    Review --> Evidence["Disposable pytest baseline, contexts, controls, mutants"]
    Evidence --> Report["HTML + JSON report"]
    Tools -->|"read_review_report; no execution"| Report
```

## MCP: what is actually implemented

`mcp_server/server.py` constructs the official SDK's `MCPServer`.
Decorated functions become tools with generated input/output schemas.
The SDK implements JSON-RPC framing, protocol negotiation, tool discovery,
validation, dispatch and protocol errors. Cartograph implements the functions.

The lockfile uses MCP SDK **2.0.0**; the dependency requirement is
`mcp>=2.0.0,<3`, matching the imported API.
The server uses local **stdio**. It is not a deployed HTTP service.
Modern discovery and the legacy initialize handshake are both exercised with
a separate SDK client process. A raw subprocess test also checks JSON-RPC
version fields, correlated IDs, tool calls and a `-32601` unknown-method error.

Nine tools are exposed:

| Tool | Purpose |
| --- | --- |
| index_repo | Return graph counts and unresolved-call coverage |
| find_definition | Find symbols by exact name |
| find_callers | Find known direct callers |
| blast_radius | Find known transitive callers |
| run_tests | Execute pytest and return structured outcomes |
| snapshot | Capture tracked and non-ignored files in a Git object |
| rollback | Restore files and the index from a snapshot |
| apply_codemod | Apply a registered deterministic libcst recipe |
| read_review_report | Read the CLI-generated evidence report and flag changed source |

The previous ninth registration, `pack_context`, raised `NotImplementedError`.
It is no longer advertised. Its Python prototype is still explicitly deferred.

Each server is bound to one repository at startup. Query calls hash the names
and contents of the current Python files. An unchanged fingerprint reuses the
graph in memory; source edits, additions, renames and removals trigger a full
rebuild. Nothing is cached on disk by MCP queries. This is not incremental
parsing: every query still scans/reads source, and edits require a full parse.
A lock shares the cache between simultaneous queries. Changes during a rebuild
are retried up to three times; repeated changes produce an actionable error.

Location queries return concise symbol IDs, names, paths and line ranges by
default; `detail="detailed"` restores full symbol metadata and docstrings.
`blast_radius(name="helper")` resolves a unique name, indexes on demand, and
returns compact caller locations with a `direct` flag. `file_path` disambiguates
names. The existing `symbol_id` input remains supported. It therefore avoids
requiring index → definition → callers → impact as four separate round trips.
`index_repo` is optional diagnostics, not a query prerequisite. The original eight tool
names remain unchanged. Symbol pages default to 20, have a maximum of 100 and
`next_offset`; impact pages put immediate callers first.
Pagination is not an atomic snapshot across concurrent external edits.
Callers should re-query if files change.

Tests and write tools require `--allow-execution`. Tests execute repository
code; this is a trusted local tool, not a sandbox. Rollback removes newer
non-ignored files and updates the real Git index; it does not move HEAD.
Query containment excludes Python symlinks resolving outside the repository.
These boundaries do not isolate malicious code or concurrent filesystem changes.

## Patch review

`review/changes.py` maps Git new-side hunks to functions and generates syntax-valid
operator mutations. `coverage_plugin.py` associates executable lines with actual
pytest node IDs. `engine.py` runs a baseline, verifies each selected test set on
unmutated source, and checks one mutant per fresh disposable copy. Failed controls,
timeouts and collection errors are inconclusive. `render.py` writes escaped HTML
and a versioned JSON report; the MCP reader recalculates the source digest.
No model or LangGraph stage is involved. [Full behavior and limits](change-review.md).

## LangGraph: four sequential stages, five roles

```text
START -> inspect -> plan -> execute -> report -> END
```

- **inspect:** index the repository, then Scout selects relevant symbols.
- **plan:** Planner chooses ordered recipe-based steps.
- **execute:** a regular Python loop invokes Rewriter once per step.
  Rewriter snapshots, applies a codemod, tests changes, and restores failures.
  Adjudicator offers advice if a recipe reports ambiguity; candidate
  recommendations are not automatically executed. Ambiguous work is quarantined.
  A final real pytest run determines the report's test status.
- **report:** Reporter summarizes the recorded outcomes.

There are five roles: Scout, Planner, Rewriter, Adjudicator and Reporter.
They are not five autonomous workers and not five graph nodes.
Only state, nodes, edges, `compile()` and `invoke()` are needed to understand
the LangGraph integration. There are no graph cycles, checkpoint stores,
resume threads, subgraphs or parallel scheduling.

The pipeline calls the same underlying Python functions as the MCP adapter.
It is **not an MCP client**, and it does not send JSON-RPC between its nodes.
`examples/mcp_client.py` demonstrates the actual MCP client path separately.

A failed test restores files before requesting an optional LLM diagnosis,
so a provider outage cannot prevent rollback. Exceptions during a codemod
or test execution also trigger restoration. This is a local best-effort
transaction; filesystem/Git failures can still make rollback fail.

## Claims and limits

- Static call edges are estimates, not complete runtime dependency analysis.
  Dynamic calls and unknown receivers can remain unresolved.
- The bundled codemod handles supported unittest assertions. It is not a
  general autonomous refactoring engine.
- `scripts/benchmark_context.py` measures payload bytes on named queries.
  It does not establish tokenizer-level savings, model quality or speed.
- No live Groq result is claimed by offline tests. The live-provider test
  runs only when a key is configured.
- Legacy context-budget and run-budget settings remain advisory/deferred;
  there is no enforced model token budget. The per-step routing budget was
  removed with the retry graph.

See [MCP learning guide](mcp-guide.md) for setup and protocol examples.
