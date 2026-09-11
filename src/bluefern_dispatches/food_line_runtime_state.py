from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


RUNTIME_ROOT = Path("status/food-line/runtime")
CURRENT_QUEUE_SEED = Path("data/dispatches/food-line/review/current-signal-review.json")
CURRENT_QUEUE_PATH = RUNTIME_ROOT / "current-signal-review.json"
SOURCE_HISTORY_SEED = Path("data/dispatches/food-line/source_performance_history.json")
SOURCE_HISTORY_PATH = RUNTIME_ROOT / "source_performance_history.json"
MIGRATION_MANIFEST_PATH = RUNTIME_ROOT / "migration.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _ensure_runtime_file(root: Path, runtime_path: Path, seed_path: Path) -> Path:
    runtime = root / runtime_path
    seed = root / seed_path
    if not runtime.exists() and seed.exists():
        _atomic_copy(seed, runtime)
    return runtime


def current_queue_path(root: Path) -> Path:
    return _ensure_runtime_file(root, CURRENT_QUEUE_PATH, CURRENT_QUEUE_SEED)


def source_performance_history_path(root: Path) -> Path:
    return _ensure_runtime_file(root, SOURCE_HISTORY_PATH, SOURCE_HISTORY_SEED)


def _head_bytes(root: Path, relative: Path) -> bytes:
    result = subprocess.run(
        ["git", "show", f"HEAD:{relative.as_posix()}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"unable to read HEAD:{relative}")
    return result.stdout


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def migrate_food_line_runtime_state(root: Path) -> dict[str, object]:
    root = root.resolve()
    migrated: list[dict[str, str]] = []
    for seed_relative, runtime_relative in (
        (CURRENT_QUEUE_SEED, CURRENT_QUEUE_PATH),
        (SOURCE_HISTORY_SEED, SOURCE_HISTORY_PATH),
    ):
        seed = root / seed_relative
        runtime = root / runtime_relative
        if not seed.exists() and not runtime.exists():
            raise FileNotFoundError(f"neither runtime state nor canonical seed exists: {seed_relative}")
        if not runtime.exists():
            _atomic_copy(seed, runtime)
        committed_seed = _head_bytes(root, seed_relative)
        migrated.append(
            {
                "seed_path": seed_relative.as_posix(),
                "runtime_path": runtime_relative.as_posix(),
                "seed_sha256": hashlib.sha256(committed_seed).hexdigest(),
                "runtime_sha256": _sha256(runtime),
                "state": "preserved",
            }
        )

    # Restore only the two known mutable tracked files to their committed seed.
    # Their current bytes have already been copied to the runtime paths above.
    for seed_relative, _runtime_relative in (
        (CURRENT_QUEUE_SEED, CURRENT_QUEUE_PATH),
        (SOURCE_HISTORY_SEED, SOURCE_HISTORY_PATH),
    ):
        seed = root / seed_relative
        committed = _head_bytes(root, seed_relative)
        if seed.read_bytes() != committed:
            _atomic_write_bytes(seed, committed)

    manifest = {
        "schema_version": "food_line_runtime_state_migration_v1",
        "state": migrated,
    }
    manifest_path = root / MIGRATION_MANIFEST_PATH
    if manifest_path.exists():
        try:
            if json.loads(manifest_path.read_text(encoding="utf-8")) == manifest:
                return {"manifest_path": manifest_path.as_posix(), "state": migrated, "idempotent": True}
        except (OSError, json.JSONDecodeError):
            pass
    _atomic_write_bytes(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"manifest_path": manifest_path.as_posix(), "state": migrated, "idempotent": False}
