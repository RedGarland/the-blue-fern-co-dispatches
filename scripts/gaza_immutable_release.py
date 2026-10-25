"""Shared verification for the Gaza immutable two-file release contract.

This module does not activate a release. It builds a small, auditable boundary
between a source-only release tree and the existing Gaza runtime checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Iterable


ALLOWED_PATHS = (
    "scripts/run_gaza_daily_operator.py",
    "src/bluefern_dispatches/bluesky_post.py",
)
SCHEMA_VERSION = "bluefern.gaza_immutable_release.v1"
SUPPORTED_TREE_MODE = "100644"
SUPPORTED_TREE_TYPE = "blob"


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _resolved(path: Path) -> Path:
    return _lexical(path).resolve(strict=False)


def _has_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = getattr(path.stat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def assert_safe_existing_ancestors(path: Path) -> None:
    """Reject symlink/reparse ancestors, including above missing leaf paths."""
    probe = _lexical(path)
    while not probe.exists() and not probe.is_symlink():
        parent = probe.parent
        if parent == probe:
            return
        probe = parent
    while True:
        if _has_reparse_or_symlink(probe):
            raise ValueError(f"path resolves through a reparse point: {probe}")
        if probe.parent == probe:
            return
        probe = probe.parent


def assert_outside(path: Path, forbidden: Iterable[Path], label: str) -> Path:
    lexical = _lexical(path)
    assert_safe_existing_ancestors(lexical)
    resolved = lexical.resolve(strict=False)
    for root in forbidden:
        base = _resolved(root)
        try:
            resolved.relative_to(base)
        except ValueError:
            continue
        raise ValueError(f"{label} must be outside {base}: {resolved}")
    assert_safe_existing_ancestors(resolved)
    return resolved


def _safe_relative(path: str) -> str:
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("/"):
        raise ValueError(f"unsafe release path: {path}")
    return normalized


def _read_tree_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or _has_reparse_or_symlink(path):
            raise ValueError(f"release tree contains a reparse/symlink path: {relative}")
        if path.is_file():
            files[relative] = path.read_bytes()
    return files


def release_tree_digest(entries: dict[str, str]) -> str:
    lines = "".join(f"{path}|{entries[path]}\n" for path in sorted(entries))
    return sha256(lines.encode("utf-8"))


def verify_release_manifest(
    manifest_path: Path,
    release_root: Path,
    *,
    runtime_root: Path | None = None,
    require_unactivated: bool = False,
    expected_manifest_sha256: str | None = None,
    expected_base_commit: str | None = None,
    expected_replacement_commit: str | None = None,
) -> dict[str, Any]:
    manifest_path = _lexical(manifest_path)
    release_root = _lexical(release_root)
    if runtime_root is not None:
        assert_outside(release_root, [runtime_root], "release root")
        assert_outside(runtime_root, [release_root], "runtime root")
    if not manifest_path.is_file():
        raise ValueError(f"manifest is missing: {manifest_path}")
    if not release_root.is_dir():
        raise ValueError(f"release root is missing: {release_root}")
    assert_safe_existing_ancestors(manifest_path)
    assert_safe_existing_ancestors(release_root)
    if expected_manifest_sha256 is not None:
        actual_manifest_sha256 = file_sha256(manifest_path)
        if actual_manifest_sha256 != expected_manifest_sha256.lower():
            raise ValueError(
                f"manifest digest mismatch: expected {expected_manifest_sha256}, got {actual_manifest_sha256}"
            )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported immutable-release manifest schema")
    if payload.get("dispatch") != "gaza":
        raise ValueError("immutable release is not scoped to Gaza")
    if expected_base_commit is not None and payload.get("base_commit") != expected_base_commit:
        raise ValueError("manifest base_commit does not match the trusted expected base")
    if expected_replacement_commit is not None and payload.get("replacement_commit") != expected_replacement_commit:
        raise ValueError("manifest replacement_commit does not match the trusted expected source")
    if require_unactivated and payload.get("activation") != "unactivated":
        raise ValueError("release manifest is already activated or has an invalid state")
    if payload.get("changed_paths") != list(ALLOWED_PATHS):
        raise ValueError("manifest changed_paths do not match the exact two-file allowlist")

    tree_entries = payload.get("tree_entries")
    if not isinstance(tree_entries, dict) or not tree_entries:
        raise ValueError("manifest tree_entries are missing")
    normalized_entries = {_safe_relative(str(k)): str(v) for k, v in tree_entries.items()}
    tree_metadata = payload.get("tree_metadata")
    if not isinstance(tree_metadata, dict) or set(tree_metadata) != set(tree_entries):
        raise ValueError("manifest tree_metadata is missing or does not cover the exact tree")
    for path in sorted(tree_entries):
        metadata = tree_metadata.get(path)
        if not isinstance(metadata, dict):
            raise ValueError(f"tree metadata is invalid: {path}")
        if metadata.get("type") != SUPPORTED_TREE_TYPE or metadata.get("mode") != SUPPORTED_TREE_MODE:
            raise ValueError(f"unsupported Git tree entry mode/type: {path}")
    actual = _read_tree_files(release_root)
    actual_paths = set(actual)
    expected_paths = set(normalized_entries)
    if actual_paths != expected_paths:
        extra = sorted(actual_paths - expected_paths)
        missing = sorted(expected_paths - actual_paths)
        raise ValueError(f"release tree file set mismatch; extra={extra}, missing={missing}")

    actual_blobs = {path: git_blob_sha(data) for path, data in actual.items()}
    if actual_blobs != normalized_entries:
        mismatches = [path for path in sorted(expected_paths) if actual_blobs[path] != normalized_entries[path]]
        raise ValueError(f"release tree blob mismatch: {mismatches}")
    if payload.get("tree_verification", {}).get("release_tree_sha256") != release_tree_digest(actual_blobs):
        raise ValueError("release tree digest mismatch")
    replacements = payload.get("replacement_blobs") or {}
    for path in ALLOWED_PATHS:
        if normalized_entries.get(path) != replacements.get(path):
            raise ValueError(f"replacement blob binding mismatch: {path}")
    return payload

