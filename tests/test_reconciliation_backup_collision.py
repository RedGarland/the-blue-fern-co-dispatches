from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import bluefern_dispatches.generator as generator
from bluefern_dispatches.generator import (
    _preserve_conflicting_gaza_correction,
    _reconciliation_inventory,
)


EDITION_DATE = "2026-10-14"


@pytest.fixture
def review_root():
    base = Path(__file__).parents[1] / ".test-tmp"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="reconciliation-", dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _primary_backup(backup_root: Path, target: Path) -> Path:
    inventory = _reconciliation_inventory(target)
    digest = hashlib.sha256(
        "\n".join(f"{name}|{sha}|{size}" for name, (sha, size) in sorted(inventory.items())).encode("utf-8")
    ).hexdigest()
    return backup_root / "gaza" / "conflicting-corrections" / (
        f"{EDITION_DATE}-{digest[:20]}"
    )


@pytest.mark.skipif(os.name != "nt", reason="positive collision recovery requires Windows rename no-clobber semantics")
def test_matching_empty_collision_preserves_both_directories(review_root: Path) -> None:
    target = review_root / "target"
    target.mkdir()
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    primary.mkdir(parents=True)

    preserved = _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert preserved.name == f"{EDITION_DATE}-e3b0c44298fc1c149afb-recovery-1"
    assert not target.exists()
    assert primary.is_dir()
    assert preserved.is_dir()
    assert _reconciliation_inventory(primary) == {}
    assert _reconciliation_inventory(preserved) == {}


@pytest.mark.skipif(os.name != "nt", reason="positive collision recovery requires Windows rename no-clobber semantics")
def test_matching_nonempty_collision_uses_a_unique_sibling(review_root: Path) -> None:
    target = review_root / "target"
    files = {"index.html": b"same bytes", "nested/source.json": b"same source"}
    _write_tree(target, files)
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    _write_tree(primary, files)
    before = _reconciliation_inventory(target)

    preserved = _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert preserved.name.endswith("-recovery-1")
    assert not target.exists()
    assert _reconciliation_inventory(primary) == before
    assert _reconciliation_inventory(preserved) == before


def test_nonempty_target_without_primary_keeps_existing_backup_behavior(review_root: Path) -> None:
    target = review_root / "target"
    files = {"index.html": b"local bytes", "nested/source.json": b"local source"}
    _write_tree(target, files)
    backup_root = review_root / "backups"
    before = _reconciliation_inventory(target)

    preserved = _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert preserved.parent == backup_root / "gaza" / "conflicting-corrections"
    assert preserved.name.startswith(f"{EDITION_DATE}-")
    assert "-recovery-" not in preserved.name
    assert not target.exists()
    assert _reconciliation_inventory(preserved) == before


def test_different_collision_fails_closed_without_mutation(review_root: Path) -> None:
    target = review_root / "target"
    _write_tree(target, {"index.html": b"target bytes"})
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    _write_tree(primary, {"index.html": b"different bytes"})
    target_before = _reconciliation_inventory(target)
    primary_before = _reconciliation_inventory(primary)

    with pytest.raises(ValueError, match="conflicting reconciliation backup collision"):
        _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert target.is_dir()
    assert primary.is_dir()
    assert _reconciliation_inventory(target) == target_before
    assert _reconciliation_inventory(primary) == primary_before


def test_dangling_primary_reparse_is_rejected_before_exists_check(
    review_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = review_root / "target"
    target.mkdir()
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    primary.parent.mkdir(parents=True)
    original_is_reparse = generator._reconciliation_is_reparse

    def mark_dangling_reparse(path: Path) -> bool:
        return Path(path) == primary or original_is_reparse(path)

    monkeypatch.setattr(generator, "_reconciliation_is_reparse", mark_dangling_reparse)

    with pytest.raises(ValueError, match="reparse or symlink"):
        _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert target.is_dir()
    assert not primary.exists()


def test_dangling_recovery_sibling_is_rejected_before_exists_check(
    review_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = review_root / "target"
    target.mkdir()
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    primary.mkdir(parents=True)
    candidate = primary.parent / f"{primary.name}-recovery-1"
    original_is_reparse = generator._reconciliation_is_reparse

    def mark_dangling_reparse(path: Path) -> bool:
        return Path(path) == candidate or original_is_reparse(path)

    monkeypatch.setattr(generator, "_reconciliation_is_reparse", mark_dangling_reparse)

    with pytest.raises(ValueError, match="reparse or symlink"):
        _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert target.is_dir()
    assert primary.is_dir()
    assert not candidate.exists()


def test_destination_created_between_selection_and_move_fails_closed(
    review_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = review_root / "target"
    _write_tree(target, {"index.html": b"local bytes"})
    backup_root = review_root / "backups"
    created_destination: Path | None = None

    def create_destination_then_fail(source: str, destination: str) -> None:
        nonlocal created_destination
        created_destination = Path(destination)
        created_destination.mkdir(parents=True)
        (created_destination / "concurrent.txt").write_bytes(b"concurrent bytes")
        raise FileExistsError(destination)

    monkeypatch.setattr(generator.os, "rename", create_destination_then_fail)

    with pytest.raises(RuntimeError, match="occupied backup destination"):
        _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert target.is_dir()
    assert created_destination is not None
    assert (created_destination / "concurrent.txt").read_bytes() == b"concurrent bytes"


def test_collision_recovery_fails_closed_before_mutation_on_unsupported_platform(
    review_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = review_root / "target"
    target.mkdir()
    backup_root = review_root / "backups"
    primary = _primary_backup(backup_root, target)
    primary.mkdir(parents=True)
    monkeypatch.setattr(generator, "_reconciliation_collision_recovery_supported", lambda: False)

    with pytest.raises(RuntimeError, match="unsupported platform refused before preservation mutation"):
        _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert target.is_dir()
    assert primary.is_dir()
    assert not (primary.parent / f"{primary.name}-recovery-1").exists()


def test_noncollision_preservation_remains_compatible_when_guarded(
    review_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = review_root / "target"
    _write_tree(target, {"index.html": b"local bytes"})
    backup_root = review_root / "backups"
    before = _reconciliation_inventory(target)
    monkeypatch.setattr(generator, "_reconciliation_collision_recovery_supported", lambda: False)

    preserved = _preserve_conflicting_gaza_correction(target, backup_root, EDITION_DATE)

    assert not target.exists()
    assert _reconciliation_inventory(preserved) == before
