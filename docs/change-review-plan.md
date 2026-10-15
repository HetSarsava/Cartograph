# Change-verification MVP plan

Branch: `codex/change-verification-review`. Builds on the existing local graph/MCP
efficiency changes. No claim of novel mutation testing or guaranteed safe changes.

## Product and acceptance criteria

`cartograph review --base main --repo .` reviews the working tree against a Git
commit. It writes `.cartograph/review/report.json` and a standalone HTML report.
No API key or model is required. Tests execute trusted project code in disposable
copies, never by mutating the user's source checkout.

1. Resolve and record the base commit. Diff tracked changes plus non-ignored new
   files; include staged and unstaged changes. Use Tree-sitter's symbol graph to
   map new-side changed lines to functions and show resolved caller paths. Record
   removed code, module-level changes, skipped files and unresolved edges as
   limitations, not a safe verdict.
2. Run the requested pytest paths once, collecting per-test coverage contexts
   through an explicit pytest plugin. Preserve test outcomes and logs. Require a
   passing, non-empty baseline before mutation execution. Attribute setup and
   teardown execution to the current test, documenting shared-fixture limits.
3. Generate a bounded number of syntax-valid mutations on changed function lines:
   arithmetic `+`/`-`, comparison operators and boolean literals. Exclude test
   files. Run only baseline-passing tests observed executing the mutation line,
   one mutation per fresh source copy. Validate selected tests on unmutated code
   first, so a selection-dependent failure is not credited as mutation detection.
4. Distinguish detected (a previously passing test now fails), survived (the same
   tests pass), unexecuted, timeout, error, unstable control, and budget exhaustion.
   A survivor may be equivalent or intentional: it is a review prompt, not a
   proven bug. Collection failures and process errors never count as detection.
5. Export escaped, self-contained HTML with source excerpts, line anchors, the
   patch, dependency paths, per-test evidence and downloadable JSON. Preserve raw
   subprocess logs alongside it. No JavaScript interpolation of repository text.
6. Add a read-only MCP tool for the latest generated report; tests are still
   launched explicitly through the CLI. Preserve existing execution gates.
7. Supply a repeatable, explicitly synthetic checkout/discount demo: green tests
   with a covered line and surviving arithmetic mutation, then a stronger test
   that detects the same mutation. Test the actual subprocess workflow, isolation,
   classification failures, HTML escaping and MCP interoperability.

## Boundaries

Python/pytest, local Git repos, same interpreter/dependencies as Cartograph.
No xdist or subprocess coverage in this MVP. Coverage and graph evidence are
partial; runtime aliases, external services, import-time behavior, shared fixture
reuse and nondeterministic tests require human review. Do not skip the real CI
suite based on this report. Mutation count and each process have explicit bounds.
Cloning source is isolation from accidental edits, not a security sandbox for tests.

## References

- [Coverage measurement contexts](https://coverage.readthedocs.io/en/latest/contexts.html)
- [pytest hook reference](https://docs.pytest.org/en/stable/reference/reference.html)
- [mutmut](https://github.com/boxed/mutmut) (prior art)
- [pytest-testmon](https://github.com/tarpas/pytest-testmon) (prior art)

## Validation

Meaningful unit tests for diff/mutation boundaries and process classification;
integration tests using real Git, pytest and coverage; MCP discovery/report read;
full existing test suite, lint/type checks on touched modules, and a real generated
demo inspected in a browser. Preserve both weak and strengthened demo reports.

## Implementation result

Implemented CLI review and synthetic demo, versioned JSON and standalone HTML,
per-test coverage plugin, bounded mutation execution with selection controls,
source snapshots/digests, and the read-only MCP report reader. The original eight
MCP tools remain and report discovery is the ninth. The demo produced one survivor
with weak tests and one detected mutation with stronger tests; both baselines pass.

Validation: 194 tests passed and one live-provider test skipped in the full suite.
The final isolation/rendering changes were additionally checked by all 16 review
tests. Ruff and mypy pass for the changed review/MCP modules. Both real generated
reports were inspected in the in-app browser. Artifacts are under
`logs/change-review-demo/{weak,strong}/.cartograph/review/` (ignored by Git).
