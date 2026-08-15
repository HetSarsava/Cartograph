"""Tests for tools.snapshot: snapshot()/rollback() via git plumbing, in scratch repos."""

from cartograph.tools.snapshot import rollback, snapshot


def test_snapshot_and_rollback_restores_modified_file(tmp_path):
    file_path = tmp_path / "a.txt"
    file_path.write_text("original\n")

    snap = snapshot(tmp_path, label="before edit")
    assert snap.files_captured >= 1

    file_path.write_text("modified\n")
    assert file_path.read_text() == "modified\n"

    result = rollback(tmp_path, snap.snapshot_id)

    assert file_path.read_text() == "original\n"
    assert result.files_restored >= 1


def test_rollback_removes_files_created_after_snapshot(tmp_path):
    (tmp_path / "keep.txt").write_text("keep\n")
    snap = snapshot(tmp_path, label="baseline")

    (tmp_path / "new_file.txt").write_text("should be removed\n")
    result = rollback(tmp_path, snap.snapshot_id)

    assert (tmp_path / "keep.txt").exists()
    assert not (tmp_path / "new_file.txt").exists()
    assert result.files_removed >= 1


def test_multiple_snapshots_are_independently_restorable(tmp_path):
    file_path = tmp_path / "a.txt"
    file_path.write_text("v1\n")
    snap1 = snapshot(tmp_path)

    file_path.write_text("v2\n")
    snap2 = snapshot(tmp_path)

    file_path.write_text("v3\n")

    rollback(tmp_path, snap1.snapshot_id)
    assert file_path.read_text() == "v1\n"

    file_path.write_text("v3-again\n")
    rollback(tmp_path, snap2.snapshot_id)
    assert file_path.read_text() == "v2\n"


def test_snapshot_initializes_git_repo_if_missing(tmp_path):
    assert not (tmp_path / ".git").exists()
    snapshot(tmp_path, label="auto-init")
    assert (tmp_path / ".git").exists()
