"""Versioned, serializable evidence shared by CLI, HTML and MCP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cartograph.graph.models import Symbol

RunStatus = Literal["passed", "failed", "error", "timeout"]
MutationStatus = Literal[
    "pending",
    "detected",
    "survived",
    "unexecuted",
    "timeout",
    "error",
    "unstable_control",
    "budget_exhausted",
    "baseline_unavailable",
]


class RunEvidence(BaseModel):
    status: RunStatus
    exit_code: int | None = None
    seconds: float = 0
    tests: dict[str, str] = Field(default_factory=dict)
    coverage: dict[str, dict[int, list[str]]] = Field(default_factory=dict)
    executable_lines: dict[str, list[int]] = Field(default_factory=dict)
    log: str = ""
    message: str = ""


class ChangedFunction(BaseModel):
    symbol: Symbol
    changed_lines: list[int]
    # Each path starts at a caller and ends at the changed function.
    caller_paths: list[list[str]] = Field(default_factory=list)
    tests_by_line: dict[int, list[str]] = Field(default_factory=dict)
    uncovered_lines: list[int] = Field(default_factory=list)


class Mutation(BaseModel):
    id: str
    symbol_id: str
    file: str
    line: int
    start_byte: int
    end_byte: int
    original: str
    replacement: str
    status: MutationStatus = "pending"
    selected_tests: list[str] = Field(default_factory=list)
    failing_tests: list[str] = Field(default_factory=list)
    control: RunEvidence | None = None
    run: RunEvidence | None = None


class ReviewMetrics(BaseModel):
    elapsed_seconds: float = 0
    pytest_processes: int = 0
    test_executions: int = 0
    mutation_attempts: int = 0


class ReviewReport(BaseModel):
    schema_version: int = 1
    repo: str
    base_ref: str
    base_commit: str
    head_commit: str
    source_digest: str
    created_utc: str
    source_matches_current: bool | None = None
    status: Literal["complete", "incomplete", "baseline_failed"] = "incomplete"
    changed_files: list[str] = Field(default_factory=list)
    patch: str = ""
    functions: list[ChangedFunction] = Field(default_factory=list)
    mutations: list[Mutation] = Field(default_factory=list)
    baseline: RunEvidence | None = None
    metrics: ReviewMetrics = Field(default_factory=ReviewMetrics)
    source: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
