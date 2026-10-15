"""Explicit pytest plugin; records actual execution per pytest node ID."""
from __future__ import annotations

import json
import os
from pathlib import Path

import coverage
import pytest


class Recorder:
    def __init__(self, root: Path, output: Path) -> None:
        self.root, self.output = root.resolve(), output
        self.cov = coverage.Coverage(data_file=None, source=[str(root)], config_file=False)
        self.outcomes: dict[str, str] = {}
        self.collection_errors: list[str] = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item):
        self.cov.switch_context(item.nodeid)
        yield
        self.cov.switch_context("")

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            status = "failed" if report.when == "call" else "error"
            if self.outcomes.get(report.nodeid) != "error":
                self.outcomes[report.nodeid] = status
        elif report.skipped:
            self.outcomes.setdefault(report.nodeid, "skipped")
        elif report.when == "call":
            self.outcomes.setdefault(report.nodeid, "xfail" if hasattr(report, "wasxfail")
                                     else "passed")

    def pytest_sessionfinish(self, session):
        self.cov.stop()
        data = self.cov.get_data()
        contexts, executable = {}, {}
        for filename in sorted(data.measured_files()):
            file = Path(filename).resolve()
            if file.is_relative_to(self.root):
                contexts[file.relative_to(self.root).as_posix()] = {
                    line: sorted(c for c in values if c in self.outcomes)
                    for line, values in data.contexts_by_lineno(filename).items()
                }
                try:
                    executable[file.relative_to(self.root).as_posix()] = (
                        self.cov.analysis2(filename)[1]
                    )
                except coverage.exceptions.CoverageException:
                    # E.g. a syntactically invalid changed file. The baseline
                    # collection outcome still needs to be preserved as an error.
                    executable[file.relative_to(self.root).as_posix()] = []
        self.output.write_text(json.dumps({"tests": self.outcomes, "coverage": contexts,
                                           "executable_lines": executable,
                                           "collection_errors": self.collection_errors}),
                               encoding="utf-8")


def pytest_configure(config):
    if getattr(config.option, "numprocesses", None):
        raise pytest.UsageError("Cartograph review does not support xdist")
    root = Path(os.environ["CARTOGRAPH_REVIEW_ROOT"])
    output = Path(os.environ["CARTOGRAPH_REVIEW_EVIDENCE"])
    recorder = Recorder(root, output)
    config.pluginmanager.register(recorder, "cartograph-review-recorder")
    recorder.cov.start()
