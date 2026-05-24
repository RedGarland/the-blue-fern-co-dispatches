from __future__ import annotations

import shutil
from pathlib import Path

from scripts import cleanup_local_test_artifacts as cleanup


def _make_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "marker.txt").write_text("x", encoding="utf-8")


def test_cleanup_dry_run_keeps_allowlisted_dirs(tmp_path: Path):
    candidate = tmp_path / ".tmp_pytest_abc"
    _make_dir(candidate)

    actions = cleanup.cleanup_local_test_artifacts(tmp_path, apply=False)

    assert candidate.exists()
    assert any(a.path == candidate and a.status == "would-remove" for a in actions)


def test_cleanup_only_targets_allowlisted_patterns(tmp_path: Path):
    matched = tmp_path / "output" / "tmp" / "pytest-123"
    not_matched = tmp_path / "output" / "tmp" / "keep-me"
    _make_dir(matched)
    _make_dir(not_matched)

    actions = cleanup.cleanup_local_test_artifacts(tmp_path, apply=False)
    action_paths = {a.path for a in actions}

    assert matched in action_paths
    assert not_matched not in action_paths


def test_protected_reason_identifies_protected_paths(tmp_path: Path):
    protected = tmp_path / "output" / "site" / "pytest-123"
    _make_dir(protected)

    reason = cleanup._protected_reason(protected, tmp_path)

    assert reason is not None
    assert "protected path" in reason


def test_cleanup_reports_permission_error_without_crashing(tmp_path: Path, monkeypatch):
    candidate = tmp_path / ".pytest_tmp_abc"
    _make_dir(candidate)

    def _raise_permission(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "rmtree", _raise_permission)
    actions = cleanup.cleanup_local_test_artifacts(tmp_path, apply=True)

    assert any(a.path == candidate and a.status == "error" and "permission denied" in a.detail for a in actions)
    assert candidate.exists()

