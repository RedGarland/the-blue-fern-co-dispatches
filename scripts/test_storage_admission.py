from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_MIN_FREE_BYTES = 2 * 1024**3
TEST_ROOT_ENV = "DISPATCHES_TEST_ROOT"


def require_test_root_capacity(
    path: Path,
    *,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    projected_bytes: int = 0,
) -> Path:
    if isinstance(projected_bytes, bool) or not isinstance(projected_bytes, int) or projected_bytes < 0:
        raise ValueError("projected_bytes must be a non-negative integer")
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
    required_bytes = min_free_bytes + projected_bytes
    if usage.free < required_bytes:
        raise RuntimeError(
            f"test root admission refused for {resolved}: "
            f"{usage.free} bytes free, {required_bytes} required "
            f"({min_free_bytes} reserve + {projected_bytes} projected)"
        )
    return resolved


def estimate_tree_bytes(source_tree: Path) -> int:
    source = Path(source_tree).expanduser()
    try:
        resolved = source.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_dir():
            raise RuntimeError(f"copy source must be a real directory: {source}")
        total = 0
        for candidate in resolved.rglob("*"):
            if candidate.is_symlink() or bool(getattr(os.lstat(candidate), "st_file_attributes", 0) & 0x400):
                raise RuntimeError(f"copy source contains a redirect: {candidate}")
            if candidate.is_file():
                total += candidate.stat().st_size
        return total
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"copy-size estimation failed for {source}: {exc}") from exc


def require_copy_capacity(
    destination_root: Path,
    source_tree: Path,
    *,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
) -> Path:
    source = Path(source_tree).expanduser()
    # The source may also host repository-local test output, so admit both
    # volumes before estimating and copying.
    require_test_root_capacity(source, min_free_bytes=min_free_bytes)
    projected_bytes = estimate_tree_bytes(source)
    return require_test_root_capacity(
        destination_root,
        min_free_bytes=min_free_bytes,
        projected_bytes=projected_bytes,
    )


def explicit_test_root(root: Path | None = None, *, env_name: str = TEST_ROOT_ENV) -> Path:
    raw = str(root or os.environ.get(env_name, "")).strip()
    if not raw:
        raise RuntimeError(f"{env_name} must explicitly name a writable project test root")
    return require_test_root_capacity(Path(raw))


def validate_pytest_basetemp(basetemp: Path | None, configured_root: Path) -> Path:
    """Validate an explicit pytest basetemp before pytest allocates fixtures."""
    if basetemp is None or not str(basetemp).strip():
        raise RuntimeError("pytest --basetemp must explicitly name a child of DISPATCHES_TEST_ROOT")
    root = Path(configured_root).expanduser().resolve(strict=True)
    candidate = Path(basetemp).expanduser().absolute().resolve(strict=False)
    if candidate == root or root not in candidate.parents:
        raise RuntimeError(
            f"pytest --basetemp must be inside configured test root {root}: {candidate}"
        )
    admission_target = candidate if candidate.exists() else candidate.parent
    require_test_root_capacity(admission_target)
    return candidate
