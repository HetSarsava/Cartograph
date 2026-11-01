"""Standalone, escaped HTML. Repository content is never executable markup."""

from __future__ import annotations

import hashlib
from collections import Counter
from html import escape
from pathlib import Path

from cartograph.review.models import ReviewReport, RunEvidence


def _anchor(file: str, line: int | None = None) -> str:
    key = "source-" + hashlib.sha256(file.encode()).hexdigest()[:12]
    return key if line is None else f"{key}-L{line}"


def _link(file: str, line: int | None = None, label: str | None = None) -> str:
    return f'<a href="#{_anchor(file, line)}">{escape(label or file)}</a>'


def _run(run: RunEvidence | None, output: Path) -> str:
    if run is None:
        return "Not run"
    log = Path(run.log)
    try:
        href = log.relative_to(output).as_posix()
    except ValueError:
        href = log.as_uri() if log.is_absolute() else log.as_posix()
    return (
        f"{escape(run.status)} · {run.seconds:.2f}s · "
        f'<a href="{escape(href, quote=True)}">pytest log</a>'
    )


def write_report(report: ReviewReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    counts = Counter(m.status for m in report.mutations)
    cards = "".join(
        f'<div class="stat"><strong>{counts[status]}</strong>{status.replace("_", " ")}</div>'
        for status in ("detected", "survived", "unexecuted", "budget_exhausted")
    )
    functions = []
    for f in report.functions:
        s = f.symbol
        paths = "".join(
            "<li>" + " → ".join(_link(sid.split("::", 1)[0], label=sid) for sid in path) + "</li>"
            for path in f.caller_paths
        )
        rows = "".join(
            f"<tr><td>{_link(s.file_path, line, str(line))}</td><td>"
            + ("<br>".join(escape(t) for t in tests) or "No passing test attributed")
            + "</td></tr>"
            for line, tests in f.tests_by_line.items()
        )
        functions.append(
            f"<article><h3>{_link(s.file_path, s.line_start, s.id)}</h3>"
            f"<p>Changed lines: {escape(str(f.changed_lines))}</p>"
            "<table><tr><th>Changed executable line</th><th>Observed passing tests</th></tr>"
            f"{rows}</table><details><summary>Partial caller paths ({len(f.caller_paths)})</summary>"
            f"<ul>{paths or '<li>No resolved caller path; not proof of no callers.</li>'}</ul>"
            "</details></article>"
        )
    mutations = []
    for m in report.mutations:
        tests = "<br>".join(escape(t) for t in m.selected_tests) or "None observed"
        failed = "<br>".join(escape(t) for t in m.failing_tests) or "None"
        mutations.append(
            f'<article class="mutation {m.status}"><h3>{escape(m.id)} · '
            f"{escape(m.status.replace('_', ' '))}</h3><p>"
            f"{_link(m.file, m.line, f'{m.file}:{m.line}')} · "
            f"<code>{escape(m.original)}</code> → <code>{escape(m.replacement)}</code></p>"
            f"<p>Observed tests: {tests}</p><p>Failing selected tests: {failed}</p>"
            f"<p>Unmutated control: {_run(m.control, output)}<br>"
            f"Mutated run: {_run(m.run, output)}</p></article>"
        )
    sources = []
    for file, text in sorted(report.source.items()):
        lines = "".join(
            f'<span class="line" id="{_anchor(file, i)}"><a href="#{_anchor(file, i)}">'
            f"{i:4}</a> {escape(line)}</span>"
            for i, line in enumerate(text.splitlines(), 1)
        )
        sources.append(
            f'<details id="{_anchor(file)}" open><summary>{escape(file)}</summary>'
            f"<pre>{lines}</pre></details>"
        )
    warnings = "".join(f"<li>{escape(w)}</li>" for w in report.limitations)
    html = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cartograph · Change verification</title><style>
:root{color-scheme:light;font:16px/1.55 system-ui,sans-serif;color:#192b3c;background:#f3f6fa}
body{max-width:1100px;margin:40px auto;padding:0 24px}h1{font-size:2.5rem;letter-spacing:-1px}
h2{margin-top:38px}h3{font-size:1rem}a{color:#174eab;overflow-wrap:anywhere}
.eyebrow{color:#506780;letter-spacing:2px;font-size:.8rem;font-weight:700}.stats{display:flex;gap:14px;flex-wrap:wrap}
.stat,article{background:white;border:1px solid #d7e0ea;border-radius:12px;padding:18px;margin:14px 0}
.stat{min-width:130px}.stat strong{display:block;font-size:2rem}.note{background:#e5edf8;padding:16px;border-radius:10px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e5eaf1;vertical-align:top}
pre{background:#142338;color:#e6edf8;padding:16px;overflow:auto;font:13px/1.65 ui-monospace,monospace;border-radius:8px}
pre a{color:#9fb8d7;text-decoration:none}.line{display:block;min-height:1.65em}.line:target{background:#4a4421}
p,td{overflow-wrap:anywhere}
.survived{border-left:5px solid #c28320}.detected{border-left:5px solid #39816c}summary{cursor:pointer;font-weight:600}
code{background:#e9eef5;padding:2px 5px;border-radius:4px}footer{color:#52667b;font-size:.85rem;margin:30px 0}
</style><div class="eyebrow">CARTOGRAPH / CHANGE VERIFICATION</div>"""
    if report.baseline is not None and report.baseline.status == "passed":
        html += "<h1>Your tests passed.<br>What did they check?</h1>"
    else:
        html += "<h1>Baseline needs attention.<br>Mutation evidence is unavailable.</h1>"
    html += (
        f"<p>{escape(report.repo)}<br>Base: <code>{escape(report.base_ref)}</code> "
        f"({escape(report.base_commit[:12])}) · {escape(report.created_utc)}</p>"
        f'<div class="note">Review status: <strong>{escape(report.status)}</strong>. '
        "This describes completed checks, not whether the patch is safe. "
        f"Checkout matches recorded source: {report.source_matches_current}. "
        '<a href="report.json" download>Download evidence JSON</a></div>'
        f"<h2>Baseline</h2><p>{_run(report.baseline, output)}</p>"
        f"<p>Review runtime: {report.metrics.elapsed_seconds:.2f}s · "
        f"pytest processes: {report.metrics.pytest_processes} · "
        f"test executions: {report.metrics.test_executions} · "
        f"mutations executed: {report.metrics.mutation_attempts}</p>"
        f"<p>{len(report.functions)} changed functions · {len(report.changed_files)} changed files · "
        f'{len(report.mutations)} mutation candidates</p><div class="stats">{cards}</div>'
        "<p>Detected = selected tests failed for this mutation after passing the unmodified control. "
        "Survived = those tests passed; investigate equivalence or a missing assertion. "
        "Unexecuted = no baseline-passing test was attributed to this mutation line.</p>"
        "<h2>Changed functions and execution evidence</h2>"
        + "".join(functions)
        + "<h2>Mutation checks</h2>"
        + ("".join(mutations) or "<p>No supported candidates.</p>")
        + f"<h2>What remains unknown</h2><ul>{warnings}</ul>"
        + f"<h2>Patch under review</h2><pre>{escape(report.patch)}</pre>"
        + "<h2>Source snapshot</h2>"
        + "".join(sources)
        + f"<footer>Schema {report.schema_version} · Source SHA-256 "
        f"{escape(report.source_digest)} · Local analysis, no LLM required.</footer></html>"
    )
    (output / "report.html").write_text(html, encoding="utf-8")
