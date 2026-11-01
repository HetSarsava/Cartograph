"""Join patch, call graph, execution contexts and isolated mutation evidence."""

from __future__ import annotations

import hashlib
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cartograph.graph.indexer import index_repo
from cartograph.review.changes import (
    candidates,
    changed_functions,
    diff_lines,
    files_to_copy,
    git,
)
from cartograph.review.models import MutationStatus, ReviewReport, RunEvidence
from cartograph.review.runner import copy_snapshot, run_pytest


def source_digest(root: Path, files: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    for name in files if files is not None else files_to_copy(root):
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or any(p in (".git", ".cartograph") for p in Path(name).parts)
        ):
            continue
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _selection_passed(run: RunEvidence, tests: list[str]) -> bool:
    return run.status == "passed" and all(run.tests.get(t) == "passed" for t in tests)


def classify_mutation(run: RunEvidence, tests: list[str]) -> MutationStatus:
    if run.status == "timeout":
        return "timeout"
    if run.status == "error" or not all(t in run.tests for t in tests):
        return "error"
    if run.status == "failed" and any(run.tests.get(t) == "failed" for t in tests):
        return "detected"
    if _selection_passed(run, tests):
        return "survived"
    return "error"


def review(
    repo: Path,
    base: str,
    output: Path,
    test_paths: list[str],
    *,
    max_mutations: int = 5,
    timeout: int = 60,
) -> ReviewReport:
    started = time.monotonic()
    root = repo.resolve(strict=True)
    top = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != top:
        raise ValueError("--repo must name the Git repository root")
    if not 0 <= max_mutations <= 100 or not 1 <= timeout <= 600:
        raise ValueError("Mutation budget must be 0..100 and timeout 1..600 seconds")
    if not test_paths:
        raise ValueError("Provide at least one pytest path")
    normalized_tests = []
    for item in test_paths:
        file, *nodes = item.split("::")
        path = root / file
        if item.startswith("-") or not path.resolve().is_relative_to(root) or not path.exists():
            raise ValueError(f"Test path must exist inside the repository: {item}")
        relative = path.resolve().relative_to(root).as_posix()
        normalized_tests.append("::".join([relative, *nodes]))
    # Absolute input paths must never make a child pytest process execute the
    # original checkout instead of its disposable copy.
    test_paths = normalized_tests
    output = output.resolve()
    if output.is_relative_to(root / ".git"):
        raise ValueError("Reports cannot be written inside .git")
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("report.json", "report.html"):
        if (output / filename).is_symlink():
            raise ValueError("Report destinations must not be symlinks")
    resolved = git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    base_commit = resolved.decode().strip()
    head = git(root, "rev-parse", "HEAD").decode().strip()
    files = files_to_copy(root)
    before = source_digest(root, files)
    changed, patch, warnings = diff_lines(root, base_commit)
    run_dir = output / "runs" / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True)
    report = ReviewReport(
        repo=str(root),
        base_ref=base,
        base_commit=base_commit,
        head_commit=head,
        source_digest=before,
        created_utc=datetime.now(UTC).isoformat(),
        patch=patch,
        changed_files=sorted(changed),
        limitations=[
            "Partial static call graph: unresolved/dynamic calls are unknown; caller paths capped at 30.",
            (
                "Coverage is execution evidence, not assertion evidence. Import/collection-time execution "
                "has no test context. Shared fixture execution is attributed only to the test that ran it."
            ),
            (
                "Only selected operators on new-side changed function lines are mutated. A survivor may "
                "be equivalent or intentional; detection is not proof that the patch is correct."
            ),
            (
                "Serial pytest in disposable source copies, using the current interpreter/dependencies. "
                "Ignored files, editable installs pointing outside the copy, external services and "
                "subprocess execution are not isolated or fully measured. This is not a security sandbox."
            ),
            (
                "Project addopts and inherited PYTEST_ADDOPTS are cleared for controlled serial runs. "
                "A passing report never substitutes for the project's full CI configuration."
            ),
        ],
    )
    with tempfile.TemporaryDirectory(prefix="cg-review-") as temp:
        scratch = Path(temp)
        frozen = scratch / "snapshot"
        warnings.extend(copy_snapshot(root, frozen, files))
        if source_digest(root) != before or source_digest(frozen, files) != before:
            raise ValueError("Source changed during snapshot; retry when editing has stopped")
        graph = index_repo(frozen, use_cache=False)
        report.functions, mapping_warnings = changed_functions(graph, changed)
        warnings.extend(mapping_warnings)
        if not report.functions:
            warnings.append(
                "No changed production functions mapped; no mutation evidence available."
            )
        warnings.extend(f"File could not be indexed: {name}" for name in graph.files_skipped)
        unknown = sum(edge.target_id is None for edge in graph.edges)
        report.limitations.append(f"Graph contains {unknown} unresolved call edges.")
        report.source = {
            name: (frozen / name).read_text(encoding="utf-8", errors="replace")
            for name in changed
            if name.endswith(".py") and (frozen / name).is_file()
        }
        for function in report.functions:
            for caller_path in function.caller_paths:
                for sid in caller_path:
                    name = graph.symbols[sid].file_path
                    if name not in report.source:
                        report.source[name] = (frozen / name).read_text(
                            encoding="utf-8", errors="replace"
                        )
        report.mutations = candidates(frozen, report.functions)
        if len(report.mutations) > 500:
            report.mutations = report.mutations[:500]
            warnings.append("Candidate listing truncated at 500 mutations.")
        if report.functions and not report.mutations:
            warnings.append("No supported operators found on changed function lines.")
        baseline_repo = scratch / "baseline"
        copy_snapshot(frozen, baseline_repo, files)
        report.baseline = run_pytest(baseline_repo, test_paths, run_dir / "baseline", timeout)
        baseline = report.baseline
        for function in report.functions:
            file = function.symbol.file_path
            measured = baseline.coverage.get(file, {})
            executable = baseline.executable_lines.get(file, [])
            function.tests_by_line = {
                line: sorted(t for t in measured.get(line, []) if baseline.tests.get(t) == "passed")
                for line in function.changed_lines
                if line in executable
            }
            function.uncovered_lines = [
                line for line, tests in function.tests_by_line.items() if not tests
            ]
        controls: dict[tuple[str, ...], RunEvidence] = {}
        attempted = 0
        for mutation in report.mutations:
            mutation.selected_tests = sorted(
                t
                for t in baseline.coverage.get(mutation.file, {}).get(mutation.line, [])
                if baseline.tests.get(t) == "passed"
            )
            if baseline.status != "passed":
                mutation.status = "baseline_unavailable"
                continue
            if not mutation.selected_tests:
                mutation.status = "unexecuted"
                continue
            if attempted >= max_mutations:
                mutation.status = "budget_exhausted"
                continue
            attempted += 1
            key = tuple(mutation.selected_tests)
            if key not in controls:
                control_repo = scratch / f"control-{mutation.id}"
                copy_snapshot(frozen, control_repo, files)
                controls[key] = run_pytest(
                    control_repo, list(key), run_dir / f"control-{mutation.id}", timeout
                )
            mutation.control = controls[key]
            if not _selection_passed(mutation.control, mutation.selected_tests):
                mutation.status = "unstable_control"
                continue
            mutant_repo = scratch / mutation.id
            copy_snapshot(frozen, mutant_repo, files)
            target = mutant_repo / mutation.file
            source = target.read_bytes()
            target.write_bytes(
                source[: mutation.start_byte]
                + mutation.replacement.encode()
                + source[mutation.end_byte :]
            )
            mutation.run = run_pytest(
                mutant_repo, mutation.selected_tests, run_dir / mutation.id, timeout
            )
            mutation.status = classify_mutation(mutation.run, mutation.selected_tests)
            mutation.failing_tests = sorted(
                t for t in mutation.selected_tests if mutation.run.tests.get(t) == "failed"
            )
        report.source_matches_current = source_digest(root) == before
        if not report.source_matches_current:
            warnings.append(
                "Checkout changed after snapshot; this report describes the recorded digest."
            )
        if baseline.status != "passed":
            report.status = "baseline_failed"
        elif warnings or any(
            m.status not in ("detected", "survived", "unexecuted") for m in report.mutations
        ):
            report.status = "incomplete"
        else:
            report.status = "complete"
    runs = {}
    if report.baseline is not None:
        runs[report.baseline.log] = report.baseline
    for mutation in report.mutations:
        if mutation.control is not None:
            runs[mutation.control.log] = mutation.control
        if mutation.run is not None:
            runs[mutation.run.log] = mutation.run
    report.metrics.elapsed_seconds = round(time.monotonic() - started, 3)
    report.metrics.pytest_processes = len(runs)
    report.metrics.test_executions = sum(len(run.tests) for run in runs.values())
    report.metrics.mutation_attempts = sum(m.run is not None for m in report.mutations)
    report.limitations.extend(sorted(set(warnings)))
    from cartograph.review.render import write_report

    write_report(report, output)
    return report
