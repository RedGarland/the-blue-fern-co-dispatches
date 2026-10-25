import json
import subprocess
from pathlib import Path

import pytest

from scripts.build_gaza_two_file_release import build_release
from scripts.gaza_immutable_release import ALLOWED_PATHS, git_blob_sha, release_tree_digest, verify_release_manifest
from scripts.run_gaza_immutable_release import launch


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(f"git {' '.join(args)} failed: {result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def _synthetic_manifest(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    files = {
        ALLOWED_PATHS[0]: b"SOURCE_ROOT = True\nBLUEFERN_RUNTIME_ROOT = True\n",
        ALLOWED_PATHS[1]: b"approved bluesky bytes\n",
        "README.md": b"pinned base\n",
    }
    for relative, data in files.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    entries = {path: git_blob_sha(data) for path, data in files.items()}
    manifest = {
        "schema_version": "bluefern.gaza_immutable_release.v1",
        "release_id": "fixture-release",
        "base_commit": "a" * 40,
        "replacement_commit": "b" * 40,
        "changed_paths": list(ALLOWED_PATHS),
        "replacement_blobs": {path: entries[path] for path in ALLOWED_PATHS},
        "tree_entries": entries,
        "tree_verification": {
            "base_tree_entries": len(entries),
            "unchanged_entries_verified": True,
            "release_tree_sha256": release_tree_digest(entries),
        },
        "dispatch": "gaza",
        "runtime_root_contract": "BLUEFERN_RUNTIME_ROOT",
        "activation": "unactivated",
    }
    manifest_path = tmp_path / "fixture-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return release, manifest_path


def test_manifest_verification_rejects_tampering_and_extra_files(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    verify_release_manifest(manifest, release, require_unactivated=True)
    (release / "README.md").write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="blob mismatch"):
        verify_release_manifest(manifest, release, require_unactivated=True)


def test_check_only_launcher_preserves_runtime_and_writes_receipt(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    runtime = tmp_path / "runner"
    (runtime / ".git").mkdir(parents=True)
    test_root = tmp_path / "test-root"
    receipt = tmp_path / "receipts" / "activation.json"
    result = launch(
        manifest=manifest,
        release_root=release,
        runtime_root=runtime,
        test_root=test_root,
        receipt=receipt,
        python_executable="unused-in-check-only",
        operator_args=[],
        check_only=True,
    )
    assert result["activation"] == "validated_only"
    assert result["public_side_effects"] is False
    assert receipt.is_file()
    assert not (runtime / "scripts").exists()


def test_launcher_rejects_release_inside_runtime_root(tmp_path):
    runtime = tmp_path / "runner"
    (runtime / ".git").mkdir(parents=True)
    release = runtime / "release"
    manifest = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="outside"):
        launch(
            manifest=manifest,
            release_root=release,
            runtime_root=runtime,
            test_root=tmp_path / "test-root",
            receipt=tmp_path / "receipt.json",
            python_executable="unused",
            operator_args=[],
            check_only=True,
        )


def test_builder_uses_git_objects_and_replaces_only_two_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _git(tmp_path, "init", str(source))
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "immutable release tests")
    _git(source, "checkout", "-b", "add/pages-repo-default")
    operator = source / ALLOWED_PATHS[0]
    bluesky = source / ALLOWED_PATHS[1]
    readme = source / "README.md"
    operator.parent.mkdir(parents=True)
    bluesky.parent.mkdir(parents=True)
    operator.write_bytes(b"SOURCE_ROOT = True\nBLUEFERN_RUNTIME_ROOT = True\nbase\n")
    bluesky.write_bytes(b"base bluesky\n")
    readme.write_bytes(b"base readme\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    base = _git(source, "rev-parse", "HEAD")
    operator.write_bytes(b"SOURCE_ROOT = True\nBLUEFERN_RUNTIME_ROOT = True\nreplacement\n")
    bluesky.write_bytes(b"replacement bluesky\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "replacement")
    replacement = _git(source, "rev-parse", "HEAD")
    operator_blob = _git(source, "rev-parse", f"{replacement}:{ALLOWED_PATHS[0]}")
    bluesky_blob = _git(source, "rev-parse", f"{replacement}:{ALLOWED_PATHS[1]}")
    release = tmp_path / "releases" / "gaza-1"
    manifest = tmp_path / "manifests" / "gaza-1.json"
    payload = build_release(
        source_root=source,
        base_head=base,
        replacement_head=replacement,
        operator_blob=operator_blob,
        bluesky_blob=bluesky_blob,
        release_root=release,
        manifest_path=manifest,
        release_id="gaza-1",
    )
    assert payload["tree_verification"]["unchanged_entries_verified"] is True
    assert manifest.is_file()
    assert (release / "README.md").read_bytes() == b"base readme\n"
    assert (release / ALLOWED_PATHS[0]).read_bytes().endswith(b"replacement\n")
    verify_release_manifest(manifest, release, require_unactivated=True)

