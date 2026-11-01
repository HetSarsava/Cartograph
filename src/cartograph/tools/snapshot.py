"""Git-plumbing-based snapshot/rollback for repos being modified by codemods.

`snapshot()` captures the full working tree (tracked + untracked, respecting
.gitignore) as a git commit object *without* touching HEAD, the branch, or
the user's real index — it builds the tree in a throwaway index file. The
resulting commit sha is the snapshot id. `rollback()` restores the working
tree to match that commit, deleting files that didn't exist at snapshot time.

This lets the pipeline snapshot before a risky codemod and cheaply roll back
if the test oracle fails, without requiring the repo's own git history (if
any) to be disturbed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

_GIT_AUTHOR = {
    "GIT_AUTHOR_NAME": "cartograph",
    "GIT_AUTHOR_EMAIL": "cartograph@local",
    "GIT_COMMITTER_NAME": "cartograph",
    "GIT_COMMITTER_EMAIL": "cartograph@local",
}


class SnapshotResult(BaseModel):
    """The outcome of a successful `snapshot()` call."""

    snapshot_id: str = Field(description="The git commit sha representing this snapshot.")
    label: str | None = Field(default=None, description="Caller-supplied human-readable label.")
    created_at: datetime = Field(description="UTC timestamp the snapshot was taken.")
    files_captured: int = Field(description="Number of files included in the snapshot tree.")


class RollbackResult(BaseModel):
    """The outcome of a successful `rollback()` call."""

    snapshot_id: str = Field(description="The snapshot that was restored to.")
    files_restored: int = Field(description="Files written/overwritten to match the snapshot.")
    files_removed: int = Field(description="Files deleted because they postdate the snapshot.")


class GitError(RuntimeError):
    """Raised when a git plumbing command fails."""


def snapshot(repo_path: str | Path, label: str | None = None) -> SnapshotResult:
    """Capture the current working tree of `repo_path` as a recoverable snapshot.

    Initializes a git repo at `repo_path` if one doesn't already exist. Safe
    to call repeatedly; each call produces an independent snapshot and never
    mutates the working tree, the current branch, or the user's real index.

    Args:
        repo_path: Root of the repo to snapshot.
        label: Optional human-readable label, stored in the commit message.

    Returns:
        A `SnapshotResult` whose `snapshot_id` can be passed to `rollback()`.

    Raises:
        GitError: If any underlying git plumbing command fails.
    """
    root = Path(repo_path).resolve()
    _ensure_git_repo(root)

    with tempfile.TemporaryDirectory(prefix="cartograph-index-") as tmp_dir:
        temp_index = Path(tmp_dir) / "index"
        env = {"GIT_INDEX_FILE": str(temp_index)}

        head_sha = _try_run_git(
            root, ["rev-parse", "--verify", "HEAD"], env=env
        )  # None if unborn HEAD
        if head_sha is not None:
            _run_git(root, ["read-tree", "HEAD"], env=env)

        _run_git(root, ["add", "-A"], env=env)
        tree_sha = _run_git(root, ["write-tree"], env=env)

        commit_args = ["commit-tree", tree_sha, "-m", label or "cartograph snapshot"]
        if head_sha is not None:
            commit_args += ["-p", head_sha]
        commit_sha = _run_git(root, commit_args, env=_GIT_AUTHOR)

        files_captured = len(
            _run_git(root, ["ls-tree", "-r", "--name-only", tree_sha]).splitlines()
        )

    return SnapshotResult(
        snapshot_id=commit_sha,
        label=label,
        created_at=datetime.now(UTC),
        files_captured=files_captured,
    )


def rollback(repo_path: str | Path, snapshot_id: str) -> RollbackResult:
    """Restore `repo_path`'s working tree to match a previously taken snapshot.

    Any file present now but absent from the snapshot is deleted; every file
    present in the snapshot is (over)written to its snapshotted content. Does
    not move HEAD or the current branch — this only affects the working tree
    (and the real index, which is synced to match afterward).

    Args:
        repo_path: Root of the repo to roll back.
        snapshot_id: A `SnapshotResult.snapshot_id` from a prior `snapshot()` call.

    Returns:
        A `RollbackResult` summarizing what changed.

    Raises:
        GitError: If `snapshot_id` doesn't resolve to a known commit, or any
            underlying git plumbing command fails.
    """
    root = Path(repo_path).resolve()
    _run_git(root, ["cat-file", "-e", snapshot_id])  # raises GitError if unknown

    current_files = set(
        _run_git(root, ["ls-files", "--cached", "--others", "--exclude-standard"]).splitlines()
    )
    snapshot_files = set(_run_git(root, ["ls-tree", "-r", "--name-only", snapshot_id]).splitlines())

    extras = current_files - snapshot_files
    for rel_path in extras:
        file_path = root / rel_path
        if file_path.exists():
            file_path.unlink()

    if snapshot_files:
        _run_git(root, ["checkout", snapshot_id, "--", "."])
    _run_git(root, ["add", "-A"])

    return RollbackResult(
        snapshot_id=snapshot_id,
        files_restored=len(snapshot_files),
        files_removed=len(extras),
    )


def _ensure_git_repo(root: Path) -> None:
    if not (root / ".git").exists():
        root.mkdir(parents=True, exist_ok=True)
        _run_git(root, ["init", "-q"])


def _run_git(root: Path, args: list[str], env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env=_merged_env(env),
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _try_run_git(root: Path, args: list[str], env: dict[str, str] | None = None) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env=_merged_env(env),
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _merged_env(extra: dict[str, str] | None) -> dict[str, str]:
    merged = dict(os.environ)
    if extra:
        merged.update(extra)
    return merged
