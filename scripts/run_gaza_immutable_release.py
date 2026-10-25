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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.gaza_immutable_release import assert_outside, assert_safe_existing_ancestors, verify_release_manifest


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve_release_root(release_root: Path | None, pointer: Path | None) -> Path:
    if (release_root is None) == (pointer is None):
        raise ValueError("provide exactly one of --release-root or --release-pointer")
    if pointer is not None:
        pointer = pointer.resolve()
        assert_safe_existing_ancestors(pointer)
        if not pointer.is_file():
            raise ValueError(f"release pointer is missing: {pointer}")
        value = pointer.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("release pointer is empty")
        release_root = Path(value)
    assert_safe_existing_ancestors(release_root)
    return release_root.resolve()


def validate_runtime_root(release_root: Path, runtime_root: Path, test_root: Path) -> tuple[Path, Path]:
    runtime_root = assert_outside(runtime_root, [release_root], "runtime root")
    test_root = assert_outside(test_root, [release_root], "test root")
    if not runtime_root.is_dir() or not (runtime_root / ".git").exists():
        raise ValueError(f"runtime root is not a Git checkout: {runtime_root}")
    test_root.mkdir(parents=True, exist_ok=True)
    return runtime_root, test_root


def launch(
    *,
    manifest: Path,
    release_root: Path,
    runtime_root: Path,
    test_root: Path,
    receipt: Path,
    python_executable: str,
    operator_args: list[str],
    check_only: bool,
) -> dict[str, Any]:
    receipt = assert_outside(receipt, [release_root, runtime_root], "receipt")
    runtime_root, test_root = validate_runtime_root(release_root, runtime_root, test_root)
    payload = verify_release_manifest(manifest, release_root, runtime_root=runtime_root, require_unactivated=True)
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
        "test_root": str(test_root),
        "operator": str(operator),
        "started_at": started,
        "check_only": check_only,
        "activation": "validated_only" if check_only else "process_started",
        "exit_code": None,
        "public_side_effects": False,
        "scheduler_mutation": False,
        "collection_triggered": False,
        "publication_triggered": False,
    }
    if not check_only:
        env = os.environ.copy()
        env["BLUEFERN_RUNTIME_ROOT"] = str(runtime_root)
        env["DISPATCHES_TEST_ROOT"] = str(test_root)
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--release-root", type=Path)
    group.add_argument("--release-pointer", type=Path)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("operator_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.operator_args and args.operator_args[0] == "--":
        args.operator_args = args.operator_args[1:]
    root = resolve_release_root(args.release_root, args.release_pointer)
    result = launch(
        manifest=args.manifest,
        release_root=root,
        runtime_root=args.runtime_root,
        test_root=args.test_root,
        receipt=args.receipt,
        python_executable=args.python_executable,
        operator_args=args.operator_args,
        check_only=args.check_only,
    )
    print(json.dumps(result, indent=2))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())

