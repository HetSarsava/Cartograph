# Run me

Cartograph maps a Python repo's call graph and answers *"what breaks if I
change this?"* — then uses that graph to run a test-verified migration with
an LLM in the loop for judgment only.

**Needs:** Python 3.12+ and [uv](https://docs.astral.sh/uv/).
An API key is optional — most of it runs without one.

```bash
git clone https://github.com/HetSarsava/Cartographer.git
cd Cartographer
uv sync --extra dev
```

---

## 1. No API key needed (~1 minute)

```bash
uv run pytest -q                      # 154 passed
uv run cartograph serve               # opens http://127.0.0.1:8765
```

In the browser, on the **Explore** tab, click **Cartograph's own source**:

- `217 symbols · 1094 edges · 265 resolved · 829 unresolved · 38 files`
- A dependency map renders inline. Toggle **List / Diagram** for the
  node-link view — drag to pan, scroll to zoom, click a node to fold it.

Then the **Inspect** tab — this is the point of the project:

1. Press <kbd>s</kbd>, search `_run_git`, click it.
2. **Blast radius: 3** — every symbol that transitively reaches it. That's a
   graph query, not a model call.
3. The graph below shows callers (amber) → this symbol (blue) → callees
   (green). Arrows point caller → callee, both ends when they call each other.
4. In **Calls**, greyed entries carry a reason chip: `external`, `ambiguous`,
   `receiver`. Hover for the full explanation. The indexer reports what it
   *couldn't* resolve rather than guessing — search `index_repo_node` for an
   `ambiguous` one, where two symbols share a name and it names both.

Prefer the terminal?

```bash
uv run cartograph tree src/cartograph   # one self-contained HTML file, opens offline
uv run cartograph index src/cartograph  # just the counts
```

---

## 2. The full pipeline (needs a free key, ~15 seconds)

Get one at [console.groq.com/keys](https://console.groq.com/keys), then:

```bash
cp .env.example .env                  # paste the key into GROQ_API_KEY
uv run cartograph models              # confirm the model id is still live
uv run cartograph demo
```

It migrates a bundled sample repo from `unittest` to `pytest` and prints
before/after:

```
Before:  self.assertEqual(add(2, 3), 5)      After:  assert add(2, 3) == 5
         self.assertTrue(add(2, 3) > 0)              assert add(2, 3) > 0
         with self.assertRaises(TypeError):          with pytest.raises(TypeError):
```

Ends with **6 committed, 2 quarantined** in ~11 seconds and 3–5 LLM calls.

The quarantine is the interesting part. One file uses `self.assertIn`, which
the codemod doesn't recognise — so it's left **byte-for-byte unchanged** for
a human instead of guessed at. The model never writes code: libcst does the
rewrite against a real syntax tree, and every change is gated on the test
suite, with git snapshot/rollback per step.

`cartograph demo` works on a throwaway copy under `logs/`, so it's repeatable
and never touches your files. The same thing is on the **Migrate** tab in the
web UI, with the pipeline's log streaming live.

To run it against your own repo (edits files in place — commit first):

```bash
uv run cartograph run <repo> "convert unittest tests to pytest"
```

---

## 3. It's an MCP server too

Every tool above — indexing, `find_callers`, `blast_radius`, the codemods,
git snapshot/rollback, a structured pytest wrapper — is exposed over MCP for
any client to call:

```bash
uv run python -m cartograph.mcp_server.server   # stdio transport
```

---

## If something breaks

**`uv : The term 'uv' is not recognized`** — uv isn't on PATH. Per session:

```powershell
$env:Path += ";$env:APPDATA\Python\Python312\Scripts"
```

**A run fails with a 404 on the model** — the provider retired that id.
`uv run cartograph models` lists what your key can use; put one in
`GROQ_MODEL` in `.env`. Worth checking before a demo.

**`Port 8765 is already in use`** — a server is already running. Use
`--port 8766`, or stop the other one.

---

## Things that look wrong but aren't

- **829 of 1094 edges unresolved.** Deliberate. `x.foo()` on a receiver whose
  type isn't known is left unresolved rather than bound to whichever `foo`
  happens to be unique. Guessing once attached 71 dict `.get()` calls to one
  unrelated method and put it in everything's blast radius. Most unresolved
  entries are stdlib calls — `len`, `logger.info`.
- **`blast_radius` appears twice in search.** Both are real: the
  implementation in `graph/queries.py` and the MCP tool wrapper in
  `mcp_server/server.py`.
- **The migration always leaves one file unconverted.** That's the demo.

Full detail is in [README.md](README.md).
