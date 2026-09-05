"""On-disk, content-addressed cache for `graph.indexer.index_repo`.

Stores one entry per indexed `.py` file keyed by a SHA-256 hash of the file's
raw bytes. A second `index_repo` call against an unchanged repo reuses the
cached `Symbol`s and pending-call records instead of re-parsing every file.
The cache lives under `{repo_path}/.cartograph/index_cache/` — a directory
already excluded from the source walk (see `_SKIP_DIR_NAMES` in indexer.py),
so the cache is never indexed as source.

Only *pass 1* output (symbols + raw pending calls) is cached. Edge resolution
runs against the complete symbol set on every invocation and is never cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pydantic import BaseModel

from cartograph.graph.models import Symbol

logger = logging.getLogger(__name__)

_CACHE_VERSION: int = 1
_MANIFEST_FILENAME = "manifest.json"


class CachedPendingCall(BaseModel):
    """A serialized `_PendingCall` record, as stored per file in the cache.

    Only the fields needed to re-run edge resolution are kept; pass-2-only
    state (e.g. inferred attribute types) is deliberately not persisted and
    is recomputed fresh on every run.
    """

    source_id: str
    target_expr: str
    line: int
    self_class_id: str | None


class CachedFileEntry(BaseModel):
    """Everything the cache stores for a single `.py` file."""

    content_hash: str
    """SHA-256 hex digest of the file's raw bytes. An entry is only reused
    when the file's current hash matches this exactly — mtime is never
    consulted, since it can lie after git checkouts, clock skew, or copies."""
    symbols: list[Symbol]
    pending_calls: list[CachedPendingCall]


class CacheManifest(BaseModel):
    """Top-level cache manifest: version stamp plus per-file entries."""

    cache_version: int
    files: dict[str, CachedFileEntry]


def load_cache(cache_dir: Path) -> CacheManifest | None:
    """Load and validate the cache manifest at `cache_dir/manifest.json`.

    Returns None if the file doesn't exist, fails to parse, fails schema
    validation, or has a mismatched `cache_version` — in every such case
    the caller should proceed as if no cache exists.
    """
    manifest_path = cache_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = CacheManifest.model_validate(json.loads(raw))
    except (OSError, ValueError) as exc:
        # JSONDecodeError and pydantic.ValidationError are both ValueErrors.
        logger.warning(
            "[indexer] ignoring unreadable cache manifest %s: %s", manifest_path, exc
        )
        return None
    if manifest.cache_version != _CACHE_VERSION:
        logger.warning(
            "[indexer] discarding cache manifest %s: version %s != expected %s",
            manifest_path,
            manifest.cache_version,
            _CACHE_VERSION,
        )
        return None
    return manifest


def save_cache(cache_dir: Path, manifest: CacheManifest) -> None:
    """Write `manifest` to `cache_dir/manifest.json`, creating `cache_dir` if needed.

    Raises no exception on a non-writable directory; instead this function
    is only ever called after the caller has already confirmed writability
    (see `indexer.py`'s soft-fail handling) — if writing still fails here
    for any other IO reason, log a warning and return without raising.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / _MANIFEST_FILENAME).write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("[indexer] failed to write cache manifest: %s", exc)


def hash_file_content(content: bytes) -> str:
    """Return the SHA-256 hex digest of `content`."""
    return hashlib.sha256(content).hexdigest()