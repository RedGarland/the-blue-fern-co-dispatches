"""Build an immutable Gaza release tree from Git objects only.

The builder never reads the source checkout's working tree and never writes to
it. The resulting tree is a complete pinned base tree with exactly two
reviewed replacement blobs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gaza_immutable_release import (
    ALLOWED_PATHS,
    SCHEMA_VERSION,
    SUPPORTED_TREE_MODES,
    SUPPORTED_TREE_TYPE,
    assert_outside,
    assert_safe_existing_ancestors,
    git_blob_sha,
    release_tree_digest,
    sha256,
    verify_release_manifest,
)


def _git_bytes(source_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def _git_text(source_root: Path, *args: str) -> str:
    return _git_bytes(source_root, *args).decode("utf-8").strip()


def _validate_sha(value: str, label: str) -> str:
    value = value.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a full 40-character SHA")
    return value


def _parse_tree_line(line: str) -> tuple[str, dict[str, str]]:
    header, path = line.split("\t", 1)
    mode, entry_type, blob = header.split(" ", 2)
    return path, {"mode": mode, "type": entry_type, "blob": blob}


def _base_tree(source_root: Path, base_head: str) -> dict[str, dict[str, str]]:
    lines = _git_text(source_root, "ls-tree", "-r", "--full-tree", base_head).splitlines()
    tree: dict[str, dict[str, str]] = {}
    for line in lines:
        if not line.strip():
            continue
        path, entry = _parse_tree_line(line)
        if entry["type"] != SUPPORTED_TREE_TYPE or entry["mode"] not in SUPPORTED_TREE_MODES:
            raise ValueError(f"unsupported Git tree entry mode/type: {path}")
        tree[path] = entry
    return tree


def _materialize_git_tree(source_root: Path, tree: dict[str, dict[str, str]], destination: Path) -> None:
    # Read blobs directly instead of using `git archive`: Windows Git can
    # apply checkout newline conversion to archive output, which would break
    # exact blob verification.
    for path in tree:
        relative = Path(path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe Git tree path: {path}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git_bytes(source_root, "cat-file", "blob", tree[path]["blob"]))


def _write_replacement(source_root: Path, head: str, path: str, destination: Path) -> bytes:
    data = _git_bytes(source_root, "cat-file", "blob", f"{head}:{path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return data


def build_release(
    *,
    source_root: Path,
    base_head: str,
    replacement_head: str,
    operator_blob: str,
    bluesky_blob: str,
    release_root: Path,
    manifest_path: Path,
    release_id: str,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    release_root = assert_outside(release_root, [source_root], "release root")
    manifest_path = assert_outside(manifest_path, [source_root, release_root], "manifest path")
    if release_root.exists():
        raise ValueError(f"release root already exists: {release_root}")
    if manifest_path.exists():
        raise ValueError(f"manifest already exists: {manifest_path}")
    assert_safe_existing_ancestors(source_root)
    base_head = _validate_sha(base_head, "base_head")
    replacement_head = _validate_sha(replacement_head, "replacement_head")
    operator_blob = _validate_sha(operator_blob, "operator_blob")
    bluesky_blob = _validate_sha(bluesky_blob, "bluesky_blob")
    _git_text(source_root, "cat-file", "-e", f"{base_head}^{{commit}}")
    _git_text(source_root, "cat-file", "-e", f"{replacement_head}^{{commit}}")

    tree = _base_tree(source_root, base_head)
    if any(path not in tree for path in ALLOWED_PATHS):
        raise ValueError("base tree is missing an allowlisted runtime path")
    staging = release_root.parent / f".{release_root.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    _materialize_git_tree(source_root, tree, staging)
    replacements = {
        ALLOWED_PATHS[0]: _write_replacement(source_root, replacement_head, ALLOWED_PATHS[0], staging / ALLOWED_PATHS[0]),
        ALLOWED_PATHS[1]: _write_replacement(source_root, replacement_head, ALLOWED_PATHS[1], staging / ALLOWED_PATHS[1]),
    }
    expected_replacements = {ALLOWED_PATHS[0]: operator_blob, ALLOWED_PATHS[1]: bluesky_blob}
    for path, data in replacements.items():
        if git_blob_sha(data) != expected_replacements[path]:
            raise ValueError(f"replacement blob mismatch: {path}")
    operator_text = replacements[ALLOWED_PATHS[0]].decode("utf-8")
    if "BLUEFERN_RUNTIME_ROOT" not in operator_text or "SOURCE_ROOT" not in operator_text:
        raise ValueError("replacement operator lacks the immutable-release runtime-root interface")
    replacement_tree = _base_tree(source_root, replacement_head)
    for path in ALLOWED_PATHS:
        if path not in replacement_tree:
            raise ValueError(f"replacement tree is missing an allowlisted runtime path: {path}")
        if replacement_tree[path]["type"] != SUPPORTED_TREE_TYPE or replacement_tree[path]["mode"] != tree[path]["mode"]:
            raise ValueError(f"replacement changes unsupported Git entry mode/type: {path}")

    # The complete tree must be exactly the base tree except for the two blobs.
    actual_tree: dict[str, str] = {}
    for path in tree:
        file_path = staging / path
        if not file_path.is_file():
            raise ValueError(f"Git archive did not produce tracked file: {path}")
        actual_tree[path] = git_blob_sha(file_path.read_bytes())
    for path in tree:
        if path in expected_replacements:
            if actual_tree[path] != expected_replacements[path]:
                raise ValueError(f"replacement was not installed: {path}")
        elif actual_tree[path] != tree[path]["blob"]:
            raise ValueError(f"unapproved tree change: {path}")
    if set(actual_tree) != set(tree):
        raise ValueError("release tree file set differs from the pinned base tree")

    release_root.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(release_root)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": base_head,
        "replacement_commit": replacement_head,
        "changed_paths": list(ALLOWED_PATHS),
        "replacement_blobs": expected_replacements,
        "tree_entries": actual_tree,
        "tree_metadata": {
            path: {"mode": tree[path]["mode"], "type": tree[path]["type"]}
            for path in tree
        },
        "tree_verification": {
            "base_tree_entries": len(tree),
            "unchanged_entries_verified": True,
            "release_tree_sha256": release_tree_digest(actual_tree),
        },
        "dispatch": "gaza",
        "runtime_root_contract": "BLUEFERN_RUNTIME_ROOT",
        "activation": "unactivated",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    verify_release_manifest(
        manifest_path,
        release_root,
        require_unactivated=True,
        expected_base_commit=base_head,
        expected_replacement_commit=replacement_head,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--replacement-head", required=True)
    parser.add_argument("--operator-blob", required=True)
    parser.add_argument("--bluesky-blob", required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--manifest", dest="manifest_path", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    payload = build_release(**vars(args))
    print(json.dumps({"ok": True, "release_id": payload["release_id"], "manifest": str(args.manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

