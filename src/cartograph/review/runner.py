"""Bounded pytest processes against disposable source snapshots."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from cartograph.review.models import RunEvidence, RunStatus


def copy_snapshot(source: Path, target: Path, files: list[str]) -> list[str]:
    warnings = []
    target.mkdir(parents=True, exist_ok=False)
    for name in files:
        path = source / name
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            warnings.append(f"Symlink/outside file excluded from test snapshot: {name}")
            continue
        if ".git" in Path(name).parts or ".cartograph" in Path(name).parts:
            continue
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return warnings


def run_pytest(root: Path, selection: list[str], evidence: Path, timeout: int) -> RunEvidence:
    evidence.mkdir(parents=True, exist_ok=False)
    output = evidence / "execution.json"
    log = evidence / "pytest.log"
    temporary = evidence / "tmp"
    temporary.mkdir()
    env = os.environ.copy()
    # Do not let a caller's benchmark options or PYTHONPATH import the original checkout.
    package_root = Path(__file__).resolve().parents[2]
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(map(str, [root / "src", root, package_root])),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "",
            "CARTOGRAPH_REVIEW_ROOT": str(root),
            "CARTOGRAPH_REVIEW_EVIDENCE": str(output),
        }
    )
    for name in ("TMP", "TEMP", "TMPDIR"):
        env[name] = str(temporary)
    cmd = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-p",
        "cartograph.review.coverage_plugin",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "--rootdir",
        str(root),
        "--basetemp",
        str(evidence / "pytest-tmp"),
        "-q",
        "--",
        *selection,
    ]
    (evidence / "command.json").write_text(json.dumps(cmd, indent=2), encoding="utf-8")
    start = time.monotonic()
    timed_out = False
    with log.open("wb") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=15)
    raw = {}
    try:
        raw = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    tests = raw.get("tests", {})
    status: RunStatus
    if timed_out:
        status = "timeout"
    elif raw.get("collection_errors") or not tests or "error" in tests.values():
        status = "error"
    elif proc.returncode == 0 and "passed" in tests.values():
        status = "passed"
    elif proc.returncode == 1 and "failed" in tests.values():
        status = "failed"
    else:
        status = "error"
    return RunEvidence(
        status=status,
        exit_code=proc.returncode,
        seconds=round(time.monotonic() - start, 3),
        tests=tests,
        coverage=raw.get("coverage", {}),
        log=str(log),
        executable_lines=raw.get("executable_lines", {}),
        message="" if raw else "No usable pytest execution evidence was produced",
    )
