# Agent patch review loop

## Product goal

When an agent changes code, Cartograph should give the agent and developer
evidence about whether the changed behavior is tested, then let the agent make
limited test repairs before reporting unresolved findings. The developer should
not have to interpret a raw test failure and manually translate it into the
next prompt.

## One bounded workflow

1. The coding agent calls Cartograph's review tool after a code change.
2. Cartograph maps changed lines to Tree-sitter functions and partial caller
   paths, runs the requested pytest baseline in a disposable copy, attributes
   changed-line execution to individual tests, then runs bounded mutations.
3. Cartograph returns a compact structured result and local HTML/JSON report.
   A red baseline includes failing test names and a bounded log excerpt. A
   surviving mutation or uncovered line includes the function, changed line,
   relevant callers/tests, and a test-focused repair suggestion.
4. The agent may edit tests and request another review. The MCP server allows an
   initial review plus at most two repair rounds for the same review session.
   It refuses another run when the source has not changed or the round budget is
   exhausted. If ordinary tests fail, the agent may repair the patch, then
   re-review; mutation evidence is withheld until the baseline passes.
5. The final result goes to both the agent and developer. It says whether the
   baseline passed, what evidence was collected, what was detected or survived,
   what remains unknown, and whether the source still matches the report.

The review tool never edits code. The host agent remains the only code editor.
For mutation findings, its repair scope is tests only. For a genuine baseline
failure, it may fix the patch or tests, guided by the ordinary pytest failure.
The loop is advisory and bounded, not a merge-safety verdict.

## Measures

Each review round records elapsed wall time, pytest process count, test outcomes,
changed lines/functions, mutation counts by outcome, and repair round number.
This measures Cartograph's execution overhead and the evidence produced. Model
tokens are host-specific telemetry; report them only when the agent host supplies
them, otherwise mark them unavailable. Compare baseline and Cartograph workflows
on the same task, model, repository revision, and test command before claiming
that this reduces time, tokens, or failures.

## Boundaries and failure handling

- Require explicit `--allow-execution` before the MCP server runs repository tests.
- Keep the existing local disposable-copy execution behavior and mutation budget.
- Reuse one caller-supplied review session ID across repair rounds. Track the
  current source digest and reject same-source reruns to avoid waste.
- Return failed test names and a bounded tail of their logs; do not send full
  repository logs or source code to an external service.
- Stop on baseline errors, mutation errors/timeouts, stale evidence, unsupported
  changes, or exhausted rounds with a clear human-readable reason.
- Preserve manual CLI use and the read-only report tool.

## Differentiation to test

Coverage and mutation checking already exist. Cartograph's candidate distinction
is the structural evidence trail: changed function, partial caller paths, the
tests that executed changed lines, and mutation results in one agent-readable
repair loop. This is a hypothesis, not a uniqueness or efficiency claim. Compare
the report and time-to-action against diff-cover, Stryker, and patch-scoped tools
such as Tautest on the same repositories.

## Acceptance criteria

- An enabled MCP client can start a review without a separate terminal command.
- Baseline failures reach the agent as actionable test diagnostics rather than
  terminating the coding task without explanation.
- Surviving and uncovered findings produce scoped test-repair tasks.
- At most two changed-source re-reviews are allowed per session; no-op calls do
  not rerun tests, and exhausted sessions return a clear stop result.
- The report includes wall time, process/test/mutation counts, and iteration
  number, with token usage explicitly unavailable unless host-provided.
- The agent receives concise evidence; the developer retains a detailed report.
- Tests verify execution gating, session bounds, no-op detection, stale reports,
  failure feedback, and unchanged existing CLI/MCP behavior.
