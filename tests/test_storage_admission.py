from __future__ import annotations

import ast
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixture_lifecycle import FixtureRun, FixtureRunRegistry, _identity
from scripts import test_storage_admission, validation_profiles
from scripts.test_storage_admission import estimate_tree_bytes


ROOT = Path(__file__).resolve().parents[1]


def test_low_space_admission_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=test_storage_admission.DEFAULT_MIN_FREE_BYTES - 1),
    )
    with pytest.raises(RuntimeError, match="admission refused"):
        test_storage_admission.require_test_root_capacity(tmp_path)


def test_projected_copy_space_is_added_to_reserve(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "asset.bin"
    payload.write_bytes(b"x" * 1024)
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=test_storage_admission.DEFAULT_MIN_FREE_BYTES + 1023),
    )
    with pytest.raises(RuntimeError, match="projected"):
        test_storage_admission.require_copy_capacity(tmp_path, source)


def test_cumulative_two_copy_admission_preserves_one_reserve(tmp_path, monkeypatch):
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    source_a.mkdir()
    source_b.mkdir()
    (source_a / "a.bin").write_bytes(b"a" * 400)
    (source_b / "b.bin").write_bytes(b"b" * 400)
    free = test_storage_admission.DEFAULT_MIN_FREE_BYTES + 799
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=free),
    )

    # Each independent admission fits, but the two copies together do not.
    test_storage_admission.require_copy_capacity(tmp_path, source_a)
    test_storage_admission.require_copy_capacity(tmp_path, source_b)
    projected = estimate_tree_bytes(source_a) + estimate_tree_bytes(source_b)
    with pytest.raises(RuntimeError, match="projected"):
        test_storage_admission.require_test_root_capacity(
            tmp_path,
            projected_bytes=projected,
        )


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


def test_validation_profile_requires_explicit_root(monkeypatch):
    monkeypatch.delenv(test_storage_admission.TEST_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match="must explicitly name"):
        validation_profiles.make_pytest_basetemp("profile")


def test_validation_profile_uses_configured_root(tmp_path, monkeypatch):
    monkeypatch.setenv(test_storage_admission.TEST_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(
        test_storage_admission.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=test_storage_admission.DEFAULT_MIN_FREE_BYTES),
    )
    base = validation_profiles.make_pytest_basetemp("profile")
    assert base.parent == tmp_path.resolve()


def test_launcher_validates_both_test_and_repository_volumes():
    wrapper = (ROOT / "scripts" / "run_pytest_safe.ps1").read_text(encoding="utf-8")
    assert "ReparsePoint" in wrapper
    assert "[guid]::NewGuid()" in wrapper
    assert "DISPATCHES_TEST_ROOT" in wrapper
    assert "Repository-volume admission refused" in wrapper


def test_validation_profile_admits_repository_and_selected_roots(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.mkdir()
    monkeypatch.setenv(test_storage_admission.TEST_ROOT_ENV, str(selected))
    calls = []


    def fake_admit(path, **kwargs):
        calls.append(Path(path).resolve())
        return Path(path).resolve()


    monkeypatch.setattr(validation_profiles, "require_test_root_capacity", fake_admit)
    monkeypatch.setattr(validation_profiles, "explicit_test_root", lambda root=None: selected.resolve())
    base = validation_profiles.make_pytest_basetemp("profile")
    assert base.parent == selected.resolve()
    assert Path(__file__).resolve().parents[1] in calls
    assert selected.resolve() in calls


def test_legacy_allocators_use_registered_tmp_path_lifecycle():
    for relative in (
        "tests/test_story_dedupe.py",
        "tests/test_gaza_backfill.py",
        "tests/test_gaza_sources.py",
        "tests/test_dispatches_site.py",
        "tests/test_gaza_dispatch_generation.py",
        "tests/test_run_daily_gaza.py",
        "tests/test_care_line_dispatch.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'repo / "output" / "test-runs"' not in text
        assert "register_fixture_root" in text
        assert "tmp_path_factory" in text
        assert "mark_fixture_setup_complete" in text
    backfill = (ROOT / "tests" / "test_gaza_backfill.py").read_text(encoding="utf-8")
    story = (ROOT / "tests" / "test_story_dedupe.py").read_text(encoding="utf-8")
    dispatch_site = (ROOT / "tests" / "test_dispatches_site.py").read_text(encoding="utf-8")
    care_dispatch = (ROOT / "tests" / "test_care_line_dispatch.py").read_text(encoding="utf-8")
    assert "require_copy_capacity" in backfill
    assert "require_copy_capacity" in story
    assert "require_copy_capacity" in dispatch_site
    assert "require_copy_capacity" in care_dispatch


def _has_repository_test_run_path(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        parts = []
        current = node
        while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
            if not isinstance(current.right, ast.Constant) or not isinstance(current.right.value, str):
                parts = []
                break
            parts.insert(0, current.right.value)
            current = current.left
        if parts[-2:] == ["output", "test-runs"]:
            return True
    return False


def test_remaining_repository_local_allocators_match_dynamic_inventory():
    expected = {
        "tests/test_cascadia_pipeline.py",
        "tests/test_care_line_discovery.py",
        "tests/test_american_pressure_dispatch.py",
        "tests/test_american_pressure_backfill.py",
        "tests/test_american_pressure_sources.py",
        "tests/test_gaza_operator_status.py",
        "tests/test_add_gaza_manual_source.py",
        "tests/test_gaza_manual_source_repair.py",
    }
    found = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
        if path != Path(__file__).resolve() and _has_repository_test_run_path(
            path.read_text(encoding="utf-8")
        )
    }
    assert found == expected, (
        "repository-local test-runs allocator inventory changed; "
        f"review new paths before relying on the repository-volume stopgap: {sorted(found)}"
    )


def test_direct_pytest_session_gate_checks_both_volumes():
    source = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "def pytest_sessionstart" in source
    assert "require_test_root_capacity(ROOT)" in source
    assert "validate_pytest_basetemp" in source


def test_direct_pytest_basetemp_rejects_other_volume(tmp_path):
    configured = tmp_path / "configured"
    configured.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="inside configured test root"):
        test_storage_admission.validate_pytest_basetemp(outside, configured)


def test_direct_pytest_basetemp_requires_explicit_path(tmp_path):
    configured = tmp_path / "configured"
    configured.mkdir()
    with pytest.raises(RuntimeError, match="--basetemp"):
        test_storage_admission.validate_pytest_basetemp(None, configured)


def _recorded_fixture(tmp_path, *, setup_complete: bool, call_outcome: str):
    basetemp = tmp_path
    root = basetemp / "fixture"
    root.mkdir()
    reports = {
        phase: SimpleNamespace(outcome=("passed" if phase != "call" else call_outcome), wasxfail=False)
        for phase in ("setup", "call", "teardown")
    }
    registry = FixtureRunRegistry()
    registry.basetemp = basetemp
    registry.basetemp_identity = _identity(basetemp)
    record = FixtureRun(
        root=root,
        nodeid="tests/test_storage_admission.py::fixture",
        basetemp=basetemp,
        setup_complete=setup_complete,
        reports=reports,
        root_identity=_identity(root),
        ancestor_identities=((basetemp, _identity(basetemp)), (root, _identity(root))),
        cleanup=shutil.rmtree,
    )
    registry.records.append(record)
    return registry, root


def test_completed_fixture_is_cleaned(tmp_path):
    registry, root = _recorded_fixture(tmp_path, setup_complete=True, call_outcome="passed")
    registry.finalize()
    assert not root.exists()


def test_failed_fixture_is_retained(tmp_path):
    registry, root = _recorded_fixture(tmp_path, setup_complete=True, call_outcome="failed")
    registry.finalize()
    assert root.exists()


def test_setup_incomplete_fixture_is_retained(tmp_path):
    registry, root = _recorded_fixture(tmp_path, setup_complete=False, call_outcome="passed")
    registry.finalize()
    assert root.exists()
