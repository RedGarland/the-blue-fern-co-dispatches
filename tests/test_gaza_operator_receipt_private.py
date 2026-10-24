import argparse
import json
from pathlib import Path

import pytest

from scripts import run_gaza_daily_operator as operator


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _args(tmp_path: Path, *, push: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        date="2026-10-23",
        pages_repo=str(tmp_path / "pages"),
        pages_branch="gh-pages",
        remote_url="https://example.test/dispatches.git",
        expected_source_branch="add/pages-repo-default",
        push=push,
        dry_run=False,
        generate_audio=False,
        skip_audio=True,
        tts_provider="none",
        audio_format="mp3",
        audio_model="test",
        audio_voice="test",
        audio_alternate_voices=False,
        audio_voices="",
        audio_segue_chime="none",
        tts_price_per_1m_chars=None,
        allow_listing_shrink=False,
        skip_tests=True,
        manual_source_check_only=False,
        post_bluesky_only=False,
        post_bluesky=False,
        skip_bluesky=True,
        force_bluesky_post=False,
    )


def _patch_operator_shell(monkeypatch, summary, *, publish_calls):
    monkeypatch.setattr(operator, "_git_status_lines", lambda _root: [])
    monkeypatch.setattr(operator, "_git_branch", lambda _root: "add/pages-repo-default")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda _repo, _branch: (True, None))
    monkeypatch.setattr(
        operator,
        "validate_or_repair_manual_sources",
        lambda _date: {"ok": True, "status": "not_present", "changes": [], "errors": []},
    )
    monkeypatch.setattr(
        operator,
        "_pages_repo_snapshot",
        lambda _repo: {"status_branch": "gh-pages", "ahead": 0, "behind": 0, "head_sha": "base", "head_subject": "base"},
    )
    monkeypatch.setattr(operator, "_split_source_dirty_state", lambda *_args: ([], [], []))
    monkeypatch.setattr(operator, "_sync_pages_repo", lambda _repo, _branch: {"ok": True, "commands": []})
    monkeypatch.setattr(operator, "_daily_args", lambda **_kwargs: [])
    monkeypatch.setattr(operator, "_capture_daily_run", lambda _args: (1, summary, ""))
    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", lambda: {"commands": [], "status": "not_needed"})
    monkeypatch.setattr(operator, "_git_status_branch", lambda _root: "gh-pages clean")

    def fake_publish_args(**_kwargs):
        publish_calls.append("publish_args")
        return ["private-no-update-preview"]

    monkeypatch.setattr(operator, "_no_update_pages_publish_args", fake_publish_args)
    monkeypatch.setattr(
        operator,
        "_run_command",
        lambda _args, **_kwargs: _Completed(stdout=json.dumps({"local_pages_copy_ok": True, "pages_commit_ok": True})),
    )


def test_incomplete_threshold_block_fails_closed_without_no_update_publish(tmp_path, monkeypatch):
    summary = {
        "source_count": 1,
        "publisher_count": 1,
        "public_story_count": 1,
        "generation_ok": False,
        "validation_ok": None,
        "source_coverage_status": "limited_source_update",
        "no_update_eligible": False,
        "no_update_status_written": True,
        "errors": ["No substantive Gaza/Palestinian ground-development story cleared threshold; publication blocked."],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is False
    assert result["operator_status"] == "UPSTREAM_INCOMPLETE"
    assert result["generation_ok"] is False
    assert result["validation_ok"] is False
    assert result["pages_push_ok"] is None
    assert publish_calls == []


def test_complete_no_qualifying_records_preserves_no_update_path(tmp_path, monkeypatch):
    summary = {
        "source_count": 0,
        "publisher_count": 0,
        "public_story_count": 0,
        "generation_ok": False,
        "validation_ok": None,
        "source_coverage_status": "complete_no_qualifying_records",
        "collection_completion_status": "complete_no_qualifying_records",
        "no_update_eligible": True,
        "no_update_status_written": True,
        "errors": ["No substantive Gaza/Palestinian ground-development story cleared threshold."],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is True
    assert result["operator_status"] == "NO_UPDATE_LOCAL_PUBLISH_READY"
    assert result["generation_ok"] is True
    assert result["validation_ok"] is True
    assert result["pages_push_ok"] is None
    assert publish_calls == ["publish_args"]


def test_threshold_block_with_complete_source_but_retained_records_fails_closed(tmp_path, monkeypatch):
    summary = {
        "source_count": 1,
        "publisher_count": 1,
        "public_story_count": 1,
        "generation_ok": False,
        "validation_ok": None,
        "source_coverage_status": "complete_with_qualifying_records",
        "collection_completion_status": "complete_with_qualifying_records",
        "no_update_eligible": False,
        "no_update_status_written": True,
        "errors": ["No substantive Gaza/Palestinian ground-development story cleared threshold."],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is False
    assert result["operator_status"] == "UPSTREAM_INCOMPLETE"
    assert result["pages_push_ok"] is None
    assert publish_calls == []


def test_manual_input_success_path_remains_local_publish_ready(tmp_path, monkeypatch):
    summary = {
        "source_count": 1,
        "publisher_count": 1,
        "public_story_count": 1,
        "generation_ok": True,
        "validation_ok": True,
        "source_coverage_status": "complete_with_qualifying_records",
        "collection_completion_status": "complete_with_qualifying_records",
        "no_update_eligible": False,
        "no_update_status_written": False,
        "errors": [],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)
    monkeypatch.setattr(operator, "_capture_daily_run", lambda _args: (0, summary, ""))

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is True
    assert result["operator_status"] == "LOCAL_PUBLISH_READY"
    assert result["generation_ok"] is True
    assert result["validation_ok"] is True
    assert result["pages_push_ok"] is None
    assert publish_calls == []


def test_failed_no_update_artifact_write_never_claims_publication(tmp_path, monkeypatch):
    summary = {
        "source_count": 0,
        "publisher_count": 0,
        "public_story_count": 0,
        "source_coverage_status": "complete_no_qualifying_records",
        "collection_completion_status": "complete_no_qualifying_records",
        "no_update_eligible": True,
        "no_update_status_written": True,
        "errors": ["No substantive Gaza/Palestinian ground-development story cleared threshold."],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)
    monkeypatch.setattr(
        operator,
        "_run_command",
        lambda _args, **_kwargs: _Completed(
            returncode=1,
            stdout=json.dumps({"errors": ["no-update artifact write failed"]}),
            stderr="write failed",
        ),
    )

    result = operator.run_operator(_args(tmp_path))

    assert result["ok"] is False
    assert result["operator_status"] == "NO_UPDATE_PAGES_UPDATE_FAILED"
    assert result["pages_push_ok"] is None
    assert publish_calls == ["publish_args"]


def test_failed_no_update_push_never_claims_publication(tmp_path, monkeypatch):
    summary = {
        "source_count": 0,
        "publisher_count": 0,
        "public_story_count": 0,
        "source_coverage_status": "complete_no_qualifying_records",
        "collection_completion_status": "complete_no_qualifying_records",
        "no_update_eligible": True,
        "no_update_status_written": True,
        "errors": ["No substantive Gaza/Palestinian ground-development story cleared threshold."],
    }
    publish_calls = []
    _patch_operator_shell(monkeypatch, summary, publish_calls=publish_calls)
    command_calls = []

    def fake_commands(args, **_kwargs):
        command_calls.append(args)
        if len(command_calls) == 1:
            return _Completed(stdout=json.dumps({"local_pages_copy_ok": True, "pages_commit_ok": True}))
        return _Completed(returncode=1, stderr="push denied")

    monkeypatch.setattr(operator, "_run_command", fake_commands)

    result = operator.run_operator(_args(tmp_path, push=True))

    assert result["ok"] is False
    assert result["operator_status"] == "NO_UPDATE_PUSH_FAILED"
    assert result["pages_push_ok"] is False
    assert publish_calls == ["publish_args"]
    assert len(command_calls) == 2


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"source_coverage_status": "complete_no_qualifying_records"},
        {"source_coverage_status": "complete_no_qualifying_records", "no_update_eligible": True},
        {
            "source_coverage_status": "complete_no_qualifying_records",
            "no_update_eligible": "true",
            "no_update_status_written": True,
        },
        {
            "source_coverage_status": "complete_no_qualifying_records",
            "no_update_eligible": True,
            "no_update_status_written": True,
            "errors": ["Source collection incomplete (limited_source_update)"],
        },
        {
            "source_coverage_status": "complete_no_qualifying_records",
            "collection_completion_status": "incomplete_provider_failures",
            "no_update_eligible": True,
            "no_update_status_written": True,
            "errors": [],
        },
    ],
)
def test_missing_or_malformed_no_update_contract_fails_closed(summary):
    assert operator._verified_no_update_summary(summary) is False


def test_verified_no_update_requires_explicit_eligibility():
    base = {
        "source_coverage_status": "complete_no_qualifying_records",
        "collection_completion_status": "complete_no_qualifying_records",
        "no_update_status_written": True,
        "errors": [],
    }
    assert operator._verified_no_update_summary({**base, "no_update_eligible": True}) is True
    assert operator._verified_no_update_summary({**base, "no_update_eligible": False}) is False
    assert operator._verified_no_update_summary({**base, "no_update_eligible": True, "errors": ["Source collection incomplete (limited_source_update)"]}) is False

