from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_MIN_FREE_BYTES = 2 * 1024**3
TEST_ROOT_ENV = "DISPATCHES_TEST_ROOT"


def require_test_root_capacity(path: Path, *, min_free_bytes: int = DEFAULT_MIN_FREE_BYTES) -> Path:
    candidate = Path(path).expanduser()
    try:
        lexical = candidate.absolute()
        stat = os.lstat(lexical)
        if lexical.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400):
            raise RuntimeError(f"test root must not be a redirect: {lexical}")
        resolved = lexical.resolve(strict=True)
        if resolved != lexical or not resolved.is_dir():
            raise RuntimeError(f"test root must be a real directory: {resolved}")
        usage = shutil.disk_usage(resolved)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"test root admission failed for {candidate}: {exc}") from exc
    if usage.free < min_free_bytes:
        raise RuntimeError(
            f"test root admission refused for {resolved}: "
            f"{usage.free} bytes free, {min_free_bytes} required"
        )
    return resolved


def explicit_test_root(root: Path | None = None, *, env_name: str = TEST_ROOT_ENV) -> Path:
    raw = str(root or os.environ.get(env_name, "")).strip()
    if not raw:
        raise RuntimeError(f"{env_name} must explicitly name a writable project test root")
    return require_test_root_capacity(Path(raw))
