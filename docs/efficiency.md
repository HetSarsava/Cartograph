# Efficient dependency inspection

Cartograph's first pilot found that a fast parser alone does not imply a cheaper
agent task. Four Click graph calls took about 2.2 seconds, but caller and impact
results returned about 39 KB of wire JSON. The assisted agent still used ordinary
search and file reads. The pilot is exploratory: one run per condition, guided
tasks, differing cache reuse and validation choices, not a general speedup claim.

## Changes informed by documentation

| Guidance | Cartograph implementation |
| --- | --- |
| Return focused context; paginate large responses | Locations only by default, 20 rows/page; full definition/caller metadata is opt-in |
| Consolidate repeatedly chained operations | `blast_radius(name=...)` resolves the target and marks immediate callers in one request |
| Avoid overlapping tool choices | Instructions distinguish locations, direct callers, dependency impact and optional index diagnostics |
| Fetch context when needed | No mandatory initial indexing call; normal search remains appropriate for text-only tasks |

These choices follow [Anthropic's effective-tool guidance](https://www.anthropic.com/engineering/writing-tools-for-agents)
and [context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).
They are design hypotheses to evaluate, not a promise of a particular token reduction.

The [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
describes structured results and a serialized text fallback for compatibility.
Cartograph retains the SDK's two representations and shrinks the data returned.
It does not equate wire bytes with model tokens.

## One dependency query

```json
{"name":"blast_radius","arguments":{"name":"calculate_total","max_depth":3}}
```

The result contains `target`, a page of `symbols` with `direct` flags, `total`,
`next_offset`, and the graph's `unresolved_edges` count. If a name is ambiguous,
select a repository-relative `file_path`. Existing symbol-ID queries still work.
Do not issue `index_repo`, `find_definition`, and `find_callers` first unless
their separate outputs are actually needed. Use normal reads for function bodies.

`find_definition` and `find_callers` return five location fields by default:
`id`, `name`, `file_path`, `line_start`, `line_end`. For the previous complete
Symbol representation, request `detail="detailed"`. This changes the default
response shape and page size; clients needing old metadata must opt in.

## Graph reuse and its limits

One server process retains one graph. Every query hashes Python file names and
contents using the indexer's directory exclusions and repository boundary.
Identical content reuses the graph; edits/additions/removals/renames rebuild it.
No timestamp-only shortcut is used. Python files are still read every time;
this is not incremental parsing or a persistent disk index.

Queries share a lock. A fingerprint mismatch during rebuilding causes a retry,
up to three attempts. This reduces stale results during normal editing but does
not provide an atomic filesystem snapshot. Re-query after edits or branch changes.

Multi-change agent evaluations must keep the same MCP process open to exercise
this reuse. A separate CLI invocation per change can restart the server and
would measure a different workload. [OpenAI's evaluation guidance](https://developers.openai.com/blog/eval-skills)
supports recording actual agent traces and reported usage rather than judging
only the final answer. Report correctness, input/cached/output tokens, end-to-end
time, tool calls and tool errors; keep setup failures separate.
