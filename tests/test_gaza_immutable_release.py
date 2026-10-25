import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_gaza_two_file_release import build_release
from scripts.gaza_immutable_release import ALLOWED_PATHS, assert_outside, file_sha256, git_blob_sha, release_tree_digest, verify_release_manifest
from scripts.run_gaza_immutable_release import launch, resolve_release_root, rollback_release_pointer, set_release_pointer


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(f"git {' '.join(args)} failed: {result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def _synthetic_manifest(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    files = {
        ALLOWED_PATHS[0]: (
            b"import os\n"
            b"from pathlib import Path\n"
            b"Path(os.environ['BLUEFERN_RUNTIME_ROOT'], 'operator-seen.txt').write_text(str(Path(__file__).resolve().parents[1]))\n"
        ),
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
        "tree_metadata": {
            path: {"mode": "100644", "type": "blob"}
            for path in files
        },
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


def test_manifest_trusted_digest_and_source_binding_are_required(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        verify_release_manifest(
            manifest,
            release,
            expected_manifest_sha256="0" * 64,
            expected_base_commit=payload["base_commit"],
            expected_replacement_commit=payload["replacement_commit"],
            require_unactivated=True,
        )
    with pytest.raises(ValueError, match="base_commit"):
        verify_release_manifest(
            manifest,
            release,
            expected_manifest_sha256=file_sha256(manifest),
            expected_base_commit="c" * 40,
            expected_replacement_commit=payload["replacement_commit"],
            require_unactivated=True,
        )


def test_check_only_launcher_preserves_runtime_and_writes_receipt(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    runtime = tmp_path / "runner"
    _git(tmp_path, "init", str(runtime))
    _git(runtime, "config", "user.email", "tests@example.invalid")
    _git(runtime, "config", "user.name", "immutable release tests")
    (runtime / "runtime-marker.txt").write_text("baseline\n", encoding="utf-8")
    _git(runtime, "add", ".")
    _git(runtime, "commit", "-m", "runtime baseline")
    runtime_head = _git(runtime, "rev-parse", "HEAD")
    synthetic_payload = json.loads(manifest.read_text(encoding="utf-8"))
    synthetic_payload["base_commit"] = runtime_head
    manifest.write_text(json.dumps(synthetic_payload), encoding="utf-8")
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
        expected_manifest_sha256=file_sha256(manifest),
        expected_base_commit=runtime_head,
        expected_replacement_commit="b" * 40,
        expected_runtime_head=runtime_head,
    )
    assert result["activation"] == "validated_only"
    assert result["public_side_effects"] is False
    assert receipt.is_file()
    assert not (runtime / "scripts").exists()


def test_launcher_child_execution_reports_effects_unknown_and_keeps_runtime_root(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    runtime = tmp_path / "runner"
    _git(tmp_path, "init", str(runtime))
    _git(runtime, "config", "user.email", "tests@example.invalid")
    _git(runtime, "config", "user.name", "immutable release tests")
    (runtime / "runtime-marker.txt").write_text("baseline\n", encoding="utf-8")
    _git(runtime, "add", ".")
    _git(runtime, "commit", "-m", "runtime baseline")
    runtime_head = _git(runtime, "rev-parse", "HEAD")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["base_commit"] = runtime_head
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    receipt = tmp_path / "receipts" / "activation.json"
    result = launch(
        manifest=manifest,
        release_root=release,
        runtime_root=runtime,
        test_root=tmp_path / "test-root",
        receipt=receipt,
        python_executable=sys.executable,
        operator_args=[],
        check_only=False,
        expected_manifest_sha256=file_sha256(manifest),
        expected_base_commit=runtime_head,
        expected_replacement_commit=payload["replacement_commit"],
        expected_runtime_head=runtime_head,
    )
    assert result["activation"] == "process_completed"
    assert result["public_side_effects"] is None
    assert result["collection_triggered"] is None
    assert (runtime / "operator-seen.txt").is_file()
    assert not (release / "operator-seen.txt").exists()


def test_release_pointer_resolves_only_outside_runtime_root(tmp_path):
    release, _manifest = _synthetic_manifest(tmp_path)
    runtime = tmp_path / "runner"
    (runtime / ".git").mkdir(parents=True)
    pointer = tmp_path / "active-gaza-release.txt"
    pointer.write_text(str(release), encoding="utf-8")
    assert resolve_release_root(None, pointer, runtime) == release.resolve()
    with pytest.raises(ValueError, match="outside"):
        resolve_release_root(None, runtime / "active.txt", runtime)


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
            expected_manifest_sha256="0" * 64,
            expected_base_commit="a" * 40,
            expected_replacement_commit="b" * 40,
            expected_runtime_head="c" * 40,
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


def test_builder_cli_invocation_completes_end_to_end(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _git(tmp_path, "init", str(source))
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "immutable release tests")
    _git(source, "checkout", "-b", "add/pages-repo-default")
    operator = source / ALLOWED_PATHS[0]
    bluesky = source / ALLOWED_PATHS[1]
    operator.parent.mkdir(parents=True)
    bluesky.parent.mkdir(parents=True)
    operator.write_bytes(b"SOURCE_ROOT = True\nBLUEFERN_RUNTIME_ROOT = True\nbase\n")
    bluesky.write_bytes(b"base bluesky\n")
    (source / "README.md").write_bytes(b"base readme\n")
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
    release = tmp_path / "releases" / "gaza-cli"
    manifest = tmp_path / "manifests" / "gaza-cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_gaza_two_file_release.py",
            "--source-root", str(source),
            "--base-head", base,
            "--replacement-head", replacement,
            "--operator-blob", operator_blob,
            "--bluesky-blob", bluesky_blob,
            "--release-root", str(release),
            "--manifest", str(manifest),
            "--release-id", "gaza-cli",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["manifest"] == str(manifest)
    assert manifest.is_file()


def test_pointer_activation_and_rollback_are_atomic_and_bound(tmp_path):
    release, manifest = _synthetic_manifest(tmp_path)
    runtime = tmp_path / "runner"
    runtime.mkdir()
    pointer = tmp_path / "active" / "release.txt"
    first = set_release_pointer(
        pointer=pointer,
        release_root=release,
        runtime_root=runtime,
        manifest=manifest,
        expected_manifest_sha256=file_sha256(manifest),
        expected_base_commit="a" * 40,
        expected_replacement_commit="b" * 40,
    )
    assert first["before"] is None
    assert Path(first["after"]) == release.resolve()
    second = rollback_release_pointer(
        pointer=pointer,
        release_root=release,
        runtime_root=runtime,
        manifest=manifest,
        expected_manifest_sha256=file_sha256(manifest),
        expected_base_commit="a" * 40,
        expected_replacement_commit="b" * 40,
        expected_current_release=release,
    )
    assert Path(second["after"]) == release.resolve()


def test_path_guard_rejects_external_symlink_before_resolution(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(external, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="reparse point"):
        assert_outside(link / "missing" / "artifact", [tmp_path / "runtime"], "artifact")

