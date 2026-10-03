# MCP, using Cartograph as the example

## The four concepts to learn first

1. **Host:** the AI application you use.
2. **Client:** the host's connection to a tool server.
3. **Server:** Cartograph, which advertises tools and executes requested functions.
4. **Transport:** stdio carries messages between the local processes.

An LLM can decide which tool to request, but MCP itself does not require an
LLM. The example client below works without an API key.
[MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)

JSON-RPC is the message envelope. A request has a protocol marker,
method, arguments and an ID. The response repeats the ID and contains a result
or an error. Notifications omit the ID.
[JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification)

## Where the JSON-RPC code lives

`src/cartograph/mcp_server/server.py` imports `MCPServer` from the official
Python SDK. `@server.tool()` registers your functions; `server.run(transport="stdio")`
starts the wire protocol. The installed SDK's
`mcp/shared/jsonrpc_dispatcher.py` and `mcp/server/stdio.py` implement framing
and dispatch. You do not need to reimplement them.
[Official Python SDK](https://github.com/modelcontextprotocol/python-sdk)

For example, after a legacy initialization handshake, a client can send:

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"add"}}}
```

Cartograph's SDK routes that to `find_definition(name="add")` and serializes
the result. This exact transport behavior is tested in `tests/test_mcp.py`.

Protocol versions matter: the older `2025-11-25` lifecycle uses
`initialize` then `notifications/initialized`; newer `2026-07-28` discovery
uses `server/discover` and per-request metadata. The SDK negotiates this.
The raw example above is deliberately the legacy form; let the SDK client
construct current-version requests.
[Legacy lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[current architecture](https://modelcontextprotocol.io/docs/learn/architecture)

## Run a real client without a model

From the repository:

```bash
uv sync --frozen --extra dev
uv run python examples/mcp_client.py tests/fixtures/sample_repo --name add
uv run pytest -q tests/test_mcp.py
```

The example starts a separate Cartograph server, discovers eight tools,
indexes the sample and queries a definition. The tests additionally exercise
callers, impact, invalid arguments, write gating, a real codemod, pytest,
snapshot/rollback and raw JSON-RPC messages.

For an MCP host that accepts an `mcpServers` configuration, adapt these absolute
paths (the configuration file location depends on your host):

```json
{
  "mcpServers": {
    "cartograph": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/Cartograph",
        "run", "--frozen", "cartograph-mcp",
        "--repo", "/absolute/path/to/trusted-python-repository"
      ]
    }
  }
}
```

On Windows, use forward slashes in the JSON paths, such as `C:/Projects/Cartograph`.
Append `--allow-execution` to the argument array only when you want the client
to execute tests and mutate that repository. No Groq key is needed for MCP tools.
A client must support local stdio; cloud-only HTTP clients cannot directly
connect to this server.

## A simple demo you can explain

1. Call `index_repo`: see symbol and unresolved-edge counts.
2. Call `find_definition` with a function name: get its stable symbol ID.
3. Call `find_callers` or `blast_radius` with that ID: inspect only relevant symbols.
4. For a disposable trusted repo in execution mode, snapshot, apply a codemod,
   run tests and rollback. The bare MCP tools do not automatically sequence
   these actions; the pipeline does.

The old adapter accepted the whole graph as a query argument. The new adapter
binds the repo at startup and accepts small query arguments. Existing clients
using the old signatures must update their calls.

## Only the LangGraph basics you need

Read `pipeline/graph.py`, then `execute_plan_node` in `pipeline/nodes.py`.
A state dictionary carries values. A node reads it and returns updates.
Edges say which stage runs next. Compilation builds an executable workflow.
Invocation starts it. Cartograph uses four sequential stages and ordinary
Python for per-step work.
[LangGraph's Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)

Study in this order: this demo, the official MCP architecture overview,
the JSON-RPC request/response sections, then the small graph file.
You do not need advanced LangGraph persistence, subgraphs or agent frameworks
to explain this implementation.
