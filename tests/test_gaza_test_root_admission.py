from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

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
        "explicit_test_root",
        lambda: (_ for _ in ()).throw(RuntimeError(error)),
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
    monkeypatch.setattr(operator, "explicit_test_root", lambda: root.resolve())
    monkeypatch.setattr(operator, "require_test_root_capacity", lambda _path: root.resolve())
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
