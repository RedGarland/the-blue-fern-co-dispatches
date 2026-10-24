from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import test_storage_admission


ROOT = Path(__file__).resolve().parents[1]


def test_low_space_admission_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=test_storage_admission.DEFAULT_MIN_FREE_BYTES - 1),
    )
    with pytest.raises(RuntimeError, match="admission refused"):
        test_storage_admission.require_test_root_capacity(tmp_path)


def test_unknown_capacity_fails_closed(tmp_path, monkeypatch):
    def denied(path):
        raise OSError("capacity unavailable")

    monkeypatch.setattr(test_storage_admission.shutil, "disk_usage", denied)
    with pytest.raises(RuntimeError, match="admission failed"):
        test_storage_admission.require_test_root_capacity(tmp_path)


def test_project_test_root_requires_explicit_setting(monkeypatch):
    monkeypatch.delenv(test_storage_admission.TEST_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match="must explicitly name"):
        test_storage_admission.explicit_test_root()


def test_project_test_root_uses_configured_root(tmp_path, monkeypatch):
    monkeypatch.setenv(test_storage_admission.TEST_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=test_storage_admission.DEFAULT_MIN_FREE_BYTES),
    )
    assert test_storage_admission.explicit_test_root() == tmp_path.resolve()


def test_legacy_allocators_use_registered_tmp_path_lifecycle():
    for relative in ("tests/test_story_dedupe.py", "tests/test_gaza_backfill.py", "tests/test_gaza_sources.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'repo / "output" / "test-runs"' not in text
        assert "register_fixture_root" in text
        assert "tmp_path_factory" in text


def test_validation_entrypoints_have_no_temp_fallback():
    validation = (ROOT / "scripts" / "validation_profiles.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts" / "run_pytest_safe.ps1").read_text(encoding="utf-8")
    assert "DISPATCHES_TEST_ROOT" in validation
    assert "DISPATCHES_TEST_ROOT" in wrapper
    assert "tempfile.gettempdir" not in validation
    assert "$env:TEMP" not in wrapper
