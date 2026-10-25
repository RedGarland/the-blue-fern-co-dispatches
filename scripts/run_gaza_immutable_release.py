"""Guarded launcher for a verified Gaza immutable release.

The launcher selects source code from the release tree but keeps all runtime
state in the original Gaza checkout through BLUEFERN_RUNTIME_ROOT. It writes a
receipt outside both roots and supports --check-only for non-mutating proof.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gaza_immutable_release import (
    assert_outside,
    assert_safe_existing_ancestors,
    file_sha256,
    verify_release_manifest,
)


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_output(runtime_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(runtime_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"runtime Git identity check failed: {result.stderr.strip()}")
    return result.stdout.strip()


def validate_runtime_identity(runtime_root: Path, expected_runtime_head: str) -> str:
    runtime_root = runtime_root.resolve()
    top = Path(_git_output(runtime_root, "rev-parse", "--show-toplevel")).resolve()
    if top != runtime_root:
        raise ValueError(f"runtime root is not the checkout top-level: {runtime_root}")
    if _git_output(runtime_root, "rev-parse", "--is-inside-work-tree") != "true":
        raise ValueError("runtime root is not inside a Git work tree")
    actual_head = _git_output(runtime_root, "rev-parse", "HEAD")
    if actual_head != expected_runtime_head:
        raise ValueError(f"runtime HEAD mismatch: expected {expected_runtime_head}, got {actual_head}")
    return actual_head


def _read_pointer(pointer: Path) -> Path | None:
    if not pointer.exists():
        return None
    value = pointer.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("release pointer is empty")
    return Path(value).resolve()


_EXPECTED_CURRENT_UNSET = object()


@contextmanager
def _activation_lock(lock: Path):
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"release pointer update is already in progress: {lock}") from exc
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _pointer_lock(pointer: Path):
    return _activation_lock(pointer.with_name(pointer.name + ".lock"))


def _require_activation_lock(activation_lock: Path | None, *, label: str) -> Path:
    if activation_lock is None:
        raise ValueError(f"{label} requires an explicit activation_lock shared by pointer updates and launches")
    return activation_lock


def _assert_distinct_paths(named_paths: dict[str, Path]) -> None:
    canonical: dict[Path, str] = {}
    for name, path in named_paths.items():
        resolved = path.resolve(strict=False)
        prior = canonical.get(resolved)
        if prior is not None:
            raise ValueError(f"{name} must be distinct from {prior}: {resolved}")
        canonical[resolved] = name


POST_ONLY_FLAG_OPTIONS = frozenset({"--post-bluesky-only", "--dry-run"})
POST_ONLY_VALUE_OPTIONS = frozenset(
    {
        "--date",
        "--bluesky-approved-text-file",
        "--bluesky-approved-text-sha256",
        "--bluesky-expected-handle",
        "--bluesky-expected-did",
    }
)
POST_ONLY_REQUIRED_OPTIONS = POST_ONLY_VALUE_OPTIONS


def validate_post_only_operator_args(operator_args: list[str]) -> None:
    """Fail closed unless the child can enter only the approved post-only mode."""
    seen: set[str] = set()
    index = 0
    while index < len(operator_args):
        token = operator_args[index]
        if not token.startswith("--"):
            raise ValueError(f"post-only operator args cannot contain positional values: {token}")
        if token in seen:
            raise ValueError(f"duplicate post-only operator option: {token}")
        if token in POST_ONLY_FLAG_OPTIONS:
            seen.add(token)
            index += 1
            continue
        if token in POST_ONLY_VALUE_OPTIONS:
            if index + 1 >= len(operator_args) or operator_args[index + 1].startswith("--"):
                raise ValueError(f"post-only operator option requires one value: {token}")
            seen.add(token)
            index += 2
            continue
        raise ValueError(f"operator option is outside the immutable post-only allowlist: {token}")
    if "--post-bluesky-only" not in seen:
        raise ValueError("immutable release activation requires --post-bluesky-only")
    missing = POST_ONLY_REQUIRED_OPTIONS - seen
    if missing:
        raise ValueError(f"post-only operator args missing trusted bindings: {sorted(missing)}")


def set_release_pointer(
    *,
    pointer: Path,
    release_root: Path,
    runtime_root: Path,
    manifest: Path,
    expected_manifest_sha256: str,
    expected_base_commit: str,
    expected_replacement_commit: str,
    expected_current_release: Path | None | object = _EXPECTED_CURRENT_UNSET,
    outcome_receipt: Path,
    activation_lock: Path,
) -> dict[str, Any]:
    activation_lock = _require_activation_lock(activation_lock, label="set_release_pointer")
    pointer = assert_outside(pointer, [runtime_root, release_root], "release pointer")
    release_root = assert_outside(release_root, [runtime_root], "release root")
    manifest = assert_outside(manifest, [runtime_root, release_root], "manifest")
    outcome_receipt = assert_outside(outcome_receipt, [runtime_root, release_root], "pointer outcome receipt")
    activation_lock = assert_outside(activation_lock, [runtime_root, release_root], "activation lock")
    _assert_distinct_paths({
        "pointer": pointer,
        "manifest": manifest,
        "outcome_receipt": outcome_receipt,
        "activation_lock": activation_lock,
    })
    if expected_current_release is _EXPECTED_CURRENT_UNSET:
        raise ValueError("expected_current_release must be supplied, including None for an expected-absent pointer")
    assert_safe_existing_ancestors(pointer)
    assert_safe_existing_ancestors(activation_lock)
    payload = verify_release_manifest(
        manifest,
        release_root,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_base_commit=expected_base_commit,
        expected_replacement_commit=expected_replacement_commit,
        require_unactivated=True,
    )
    with _activation_lock(activation_lock):
        before = _read_pointer(pointer)
        expected_before = None if expected_current_release is None else expected_current_release.resolve()
        if before != expected_before:
            raise ValueError("release pointer changed from the trusted expected value")
        intent = {
            "schema_version": "bluefern.gaza_pointer_update.v1",
            "phase": "intent",
            "release_id": payload["release_id"],
            "before": str(before) if before else None,
            "after": str(release_root),
        }
        _write_receipt(outcome_receipt, intent)
        pointer.parent.mkdir(parents=True, exist_ok=True)
        temporary = pointer.with_name(f".{pointer.name}.{os.getpid()}.tmp")
        temporary.write_text(str(release_root) + "\n", encoding="utf-8")
        temporary.replace(pointer)
        after = _read_pointer(pointer)
        if after != release_root:
            raise ValueError("release pointer verification failed after atomic replacement")
        result = {"release_id": payload["release_id"], "before": str(before) if before else None, "after": str(after)}
        try:
            _write_receipt(outcome_receipt, {"schema_version": "bluefern.gaza_pointer_update.v1", "phase": "complete", **result})
        except Exception as exc:
            raise RuntimeError(f"release pointer changed but outcome receipt finalization is uncertain: {exc}") from exc
        return result


def rollback_release_pointer(**kwargs: Any) -> dict[str, Any]:
    return set_release_pointer(**kwargs)


def resolve_release_root(release_root: Path | None, pointer: Path | None, runtime_root: Path) -> Path:
    if (release_root is None) == (pointer is None):
        raise ValueError("provide exactly one of --release-root or --release-pointer")
    if pointer is not None:
        pointer = assert_outside(pointer, [runtime_root], "release pointer")
        assert_safe_existing_ancestors(pointer)
        if not pointer.is_file():
            raise ValueError(f"release pointer is missing: {pointer}")
        value = pointer.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("release pointer is empty")
        release_root = Path(value)
    assert_safe_existing_ancestors(release_root)
    return release_root.resolve()


def validate_runtime_root(
    release_root: Path,
    runtime_root: Path,
    test_root: Path,
    expected_runtime_head: str,
) -> tuple[Path, Path, str]:
    runtime_root = assert_outside(runtime_root, [release_root], "runtime root")
    test_root = assert_outside(test_root, [release_root], "test root")
    if not runtime_root.is_dir() or not (runtime_root / ".git").exists():
        raise ValueError(f"runtime root is not a Git checkout: {runtime_root}")
    actual_head = validate_runtime_identity(runtime_root, expected_runtime_head)
    test_root.mkdir(parents=True, exist_ok=True)
    return runtime_root, test_root, actual_head


def launch(
    *,
    manifest: Path,
    release_root: Path | None,
    runtime_root: Path,
    test_root: Path,
    receipt: Path,
    python_executable: str,
    operator_args: list[str],
    check_only: bool,
    expected_manifest_sha256: str,
    expected_base_commit: str,
    expected_replacement_commit: str,
    expected_runtime_head: str,
    activation_lock: Path,
    release_pointer: Path | None = None,
) -> dict[str, Any]:
    validate_post_only_operator_args(operator_args)
    activation_lock = _require_activation_lock(activation_lock, label="launch")
    if (release_root is None) == (release_pointer is None):
        raise ValueError("provide exactly one of release_root or release_pointer")
    if release_root is not None:
        release_root = assert_outside(release_root, [runtime_root], "release root")
    if release_pointer is not None:
        release_pointer = assert_outside(release_pointer, [runtime_root], "release pointer")
    manifest = assert_outside(manifest, [runtime_root], "manifest")
    receipt = assert_outside(receipt, [runtime_root], "receipt")
    activation_lock = assert_outside(activation_lock, [runtime_root], "activation lock")
    _assert_distinct_paths({
        "manifest": manifest,
        "receipt": receipt,
        "activation_lock": activation_lock,
    })
    assert_safe_existing_ancestors(activation_lock)
    with _activation_lock(activation_lock):
        if release_pointer is not None:
            release_root = resolve_release_root(None, release_pointer, runtime_root)
            release_root = assert_outside(release_root, [runtime_root], "release root")
        assert release_root is not None
        manifest = assert_outside(manifest, [release_root, runtime_root], "manifest")
        receipt = assert_outside(receipt, [release_root, runtime_root], "receipt")
        _assert_distinct_paths({
            "manifest": manifest,
            "receipt": receipt,
            "activation_lock": activation_lock,
        })
        runtime_root, test_root, actual_runtime_head = validate_runtime_root(
            release_root, runtime_root, test_root, expected_runtime_head
        )
        payload = verify_release_manifest(
            manifest,
            release_root,
            runtime_root=runtime_root,
            require_unactivated=True,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_base_commit=expected_base_commit,
            expected_replacement_commit=expected_replacement_commit,
        )
        if payload["base_commit"] != actual_runtime_head:
            raise ValueError("release base_commit does not match the actual runtime baseline")
        operator = release_root / "scripts" / "run_gaza_daily_operator.py"
        if not operator.is_file():
            raise ValueError(f"immutable release operator is missing: {operator}")
        started = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {
            "schema_version": "bluefern.gaza_immutable_activation.v1",
            "release_id": payload["release_id"],
            "manifest": str(manifest.resolve()),
            "release_root": str(release_root),
            "runtime_root": str(runtime_root),
            "runtime_head": actual_runtime_head,
            "activation_lock": str(activation_lock),
            "expected_manifest_sha256": expected_manifest_sha256,
            "test_root": str(test_root),
            "operator": str(operator),
            "operator_mode": "post_bluesky_only",
            "started_at": started,
            "check_only": check_only,
            "activation": "validated_only" if check_only else "process_started",
            "exit_code": None,
            "public_side_effects": False if check_only else None,
            "scheduler_mutation": False if check_only else None,
            "collection_triggered": False if check_only else None,
            "publication_triggered": False if check_only else None,
        }
        if not check_only:
            env = os.environ.copy()
            env["BLUEFERN_RUNTIME_ROOT"] = str(runtime_root)
            env["DISPATCHES_TEST_ROOT"] = str(test_root)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            python_path = os.pathsep.join([str(release_root), str(release_root / "src"), env.get("PYTHONPATH", "")])
            env["PYTHONPATH"] = python_path
            process = subprocess.run(
                [python_executable, str(operator), *operator_args],
                cwd=str(runtime_root),
                env=env,
                check=False,
            )
            result["exit_code"] = process.returncode
            result["activation"] = "process_completed"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_receipt(receipt, result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-base-commit", required=True)
    parser.add_argument("--expected-replacement-commit", required=True)
    parser.add_argument("--expected-runtime-head", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--release-root", type=Path)
    group.add_argument("--release-pointer", type=Path)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--activation-lock", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("operator_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.operator_args and args.operator_args[0] == "--":
        args.operator_args = args.operator_args[1:]
    result = launch(
        manifest=args.manifest,
        release_root=args.release_root,
        runtime_root=args.runtime_root,
        test_root=args.test_root,
        receipt=args.receipt,
        python_executable=args.python_executable,
        operator_args=args.operator_args,
        check_only=args.check_only,
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_base_commit=args.expected_base_commit,
        expected_replacement_commit=args.expected_replacement_commit,
        expected_runtime_head=args.expected_runtime_head,
        activation_lock=args.activation_lock,
        release_pointer=args.release_pointer,
    )
    print(json.dumps(result, indent=2))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())

