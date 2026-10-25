from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import run_daily_gaza as daily
from scripts import run_gaza_daily_operator as operator


def _patch_repo_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator, "_git_status_lines", lambda _repo: [])
    monkeypatch.setattr(operator, "_split_source_dirty_state", lambda *_args: ([], [], []))
    monkeypatch.setattr(
        operator,
        "_pages_repo_snapshot",
        lambda _repo: {
            "status_branch": "## gh-pages...origin/gh-pages",
            "ahead": 0,
            "behind": 0,
            "head_sha": "pages-base",
            "head_subject": "fixture pages base",
        },
    )
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda _repo, _branch: (True, None))
    monkeypatch.setattr(operator, "_git_branch", lambda _repo: "add/pages-repo-default")
    monkeypatch.setattr(operator, "_git_status_branch", lambda _repo: "## clean")
    monkeypatch.setattr(
        operator,
        "validate_or_repair_manual_sources",
        lambda _date: {"ok": True, "status": "not_present", "changes": [], "errors": []},
    )
    monkeypatch.setattr(
        operator,
        "_sync_pages_repo",
        lambda _repo, _branch: {"ok": True, "commands": []},
    )
    monkeypatch.setattr(
        operator,
        "_clean_source_generated_artifacts",
        lambda: {"ok": True, "status": "skipped_evidence_preserved", "commands": []},
    )


def _args(tmp_path: Path, *extra: str):
    return operator.parse_args(
        [
            "--date",
            "2026-10-25",
            "--dry-run",
            "--pages-repo",
            str(tmp_path / "pages"),
            *extra,
        ]
    )


def test_missing_root_blocks_before_collection_and_no_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_repo_preflight(monkeypatch)
    monkeypatch.delenv(operator.TEST_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda _args: pytest.fail("daily collection must not start without an explicit test root"),
    )

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is False
    assert result["operator_status"] == "TEST_ROOT_BLOCKED"
    assert result["test_root_status"] == "missing"
    assert result["source_collection_started"] is False
    assert result["daily_pipeline_started"] is False
    assert result["pages_push_ok"] is None
    assert "must explicitly name a writable project test root" in result["next_action"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("invalid", "redirect"),
        ("invalid", "admission refused"),
    ],
)
def test_invalid_or_low_space_root_blocks_without_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    error: str,
) -> None:
    _patch_repo_preflight(monkeypatch)
    root = tmp_path / "test-root"
    root.mkdir()
    monkeypatch.setenv(operator.TEST_ROOT_ENV, str(root))
    monkeypatch.setattr(
        operator,
        "admit_test_root",
        lambda **_kwargs: {
            "ok": False,
            "status": status,
            "path": str(root),
            "error": f"test-root admission failed: {error}",
        },
    )
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda _args: pytest.fail("daily collection must not start after root admission failure"),
    )

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is False
    assert result["operator_status"] == "TEST_ROOT_BLOCKED"
    assert result["test_root_status"] == status
    assert result["source_collection_started"] is False
    assert error in result["test_root_error"]


def test_valid_inherited_root_is_canonicalized_before_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_repo_preflight(monkeypatch)
    root = tmp_path / "test-root"
    root.mkdir()
    monkeypatch.setenv(operator.TEST_ROOT_ENV, str(root / "."))
    monkeypatch.setattr(
        operator,
        "admit_test_root",
        lambda **_kwargs: {
            "ok": True,
            "status": "validated",
            "path": str(root.resolve()),
            "error": None,
        },
    )
    seen: list[str] = []
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda _args: (
            seen.append(os.environ[operator.TEST_ROOT_ENV]) or
            (1, {"errors": ["bounded fixture failure"]}, ""),
        ),
    )

    result = operator.run_operator(_args(tmp_path))

    assert result["test_root_status"] == "validated"
    assert result["test_root_path"] == str(root.resolve())
    assert seen == [str(root.resolve())]
    assert result["source_collection_started"] is True
    assert result["daily_pipeline_started"] is True


def test_missing_root_emits_structured_failed_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    pages = tmp_path / "pages"
    pages.mkdir()
    monkeypatch.setattr(operator, "ROOT", root)
    _patch_repo_preflight(monkeypatch)
    monkeypatch.delenv(operator.TEST_ROOT_ENV, raising=False)
    monkeypatch.setattr(operator, "_git_output", lambda *_args: "source-head")

    assert operator.main(["--date", "2026-10-25", "--pages-repo", str(pages)]) == 1

    receipts = list((root / "status" / "operational-health" / "gaza" / "2026-10-25").rglob("*.json"))
    assert len(receipts) == 1
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["operator_status"] == "TEST_ROOT_BLOCKED"
    assert payload["public_side_effects"] == {
        "pages": False,
        "bluesky": False,
        "audio": False,
        "public_output": False,
    }
    assert payload["details"]["source_collection_started"] is False
    assert payload["details"]["test_root_status"] == "missing"
    assert "DISPATCHES_TEST_ROOT" in payload["details"]["test_root_error"]


def test_daily_missing_root_is_recorded_before_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "daily-root"
    root.mkdir()
    monkeypatch.setattr(daily, "ROOT", root)
    monkeypatch.delenv(operator.TEST_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        daily,
        "collect_or_load_sources",
        lambda *_args: pytest.fail("collection must not start before test-root admission"),
    )

    code = daily.main(
        [
            "--date",
            "2026-10-25",
            "--dry-run",
            "--pages-repo",
            str(tmp_path / "pages"),
        ]
    )

    manifest = root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "run_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["failure_stage"] == "test_root_admission"
    assert payload["collection_started"] is False
    assert payload["generation_started"] is False
    assert payload["validation_ok"] is False
    assert payload["no_update_eligible"] is False
    assert payload["publish_blocked_reason"] == "test-root-admission-failed"
    assert "DISPATCHES_TEST_ROOT" in payload["errors"][0]


def test_daily_valid_root_reaches_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "daily-root"
    root.mkdir()
    test_root = tmp_path / "test-root"
    test_root.mkdir()
    monkeypatch.setattr(daily, "ROOT", root)
    monkeypatch.setenv(operator.TEST_ROOT_ENV, str(test_root))
    called: list[str] = []
    monkeypatch.setattr(
        daily,
        "collect_or_load_sources",
        lambda _args, _summary, _log: (
            called.append(os.environ[operator.TEST_ROOT_ENV]) or (None, [], None)
        ),
    )

    code = daily.main(
        [
            "--date",
            "2026-10-25",
            "--dry-run",
            "--pages-repo",
            str(tmp_path / "pages"),
        ]
    )

    manifest = root / "data" / "dispatches" / "gaza" / "editions" / "2026-10-25" / "run_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert code == 1
    assert called == [str(test_root.resolve())]
    assert payload["test_root_status"] == "validated"
    assert payload["collection_started"] is True
    assert payload["generation_started"] is False


def test_skipped_tests_are_not_validation_success() -> None:
    summary = {
        "tests_ok": None,
        "validation_ok": None,
        "publish_blocked": False,
        "publish_blocked_reason": None,
        "errors": [],
        "warnings": [],
    }
    daily._record_skipped_validation(summary, Path("ignored.log"))
    assert summary["tests_ok"] is False
    assert summary["validation_ok"] is False
    assert summary["publish_blocked"] is True
    assert summary["publish_blocked_reason"] == "dispatch_validation_skipped"
    assert summary["errors"]
