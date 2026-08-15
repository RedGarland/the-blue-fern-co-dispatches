from __future__ import annotations

import json
from datetime import datetime, timezone
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bluefern_dispatches.food_line_signal_wire import build_current_event_eligibility_fixture
import bluefern_dispatches.food_line_signal_wire_publish as publish_module
from bluefern_dispatches.food_line_signal_wire_publish import (
    load_signal_wire_publication_state,
    publish_signal_wire_event,
    write_signal_wire_publication_state,
)


def _init_pages_repo(root: Path) -> Path:
    pages = root / "pages"
    subprocess.run(["git", "init", "-b", "gh-pages", str(pages)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(pages), "config", "user.email", "tester@example.com"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(pages), "config", "user.name", "Tester"], check=True, capture_output=True, text=True)
    (pages / "README.md").write_text("pages\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(pages), "add", "README.md"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(pages), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    return pages


def _eligible_event() -> dict[str, str]:
    event = build_current_event_eligibility_fixture(as_of="2026-08-15")
    event["bluesky_post_text"] = event["bluesky_post_text"]
    return event


def test_publish_event_writes_page_state_and_posts_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = _init_pages_repo(tmp_path)
    event = _eligible_event()
    original_run_git = __import__("bluefern_dispatches.food_line_signal_wire_publish", fromlist=["_run_git"])._run_git

    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish.post_bluesky_external_card",
        lambda **kwargs: {
            "status": "success",
            "post_uri": "at://did:plc:test/app.bsky.feed.post/1",
            "post_cid": "cid-1",
            "reason": None,
        },
    )
    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish._run_git",
        lambda repo, *args: subprocess.CompletedProcess(
            args=["git", "-C", str(repo), *args],
            returncode=0 if args and args[0] == "push" else original_run_git(repo, *args).returncode,
            stdout="" if args and args[0] == "push" else original_run_git(repo, *args).stdout,
            stderr="" if args and args[0] == "push" else original_run_git(repo, *args).stderr,
        ),
    )

    result = publish_signal_wire_event(
        tmp_path,
        event,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=True,
        post_bluesky=True,
        trace=[],
    )

    assert result["ok"] is True
    assert result["status"] == "published"
    assert result["pages_result"]["status"] == "committed"
    assert result["pages_result"]["push_performed"] is True
    assert result["bluesky_result"]["status"] == "success"
    assert result["trace"] == [
        "event_validated",
        "render",
        "pages_validate",
        "pages_commit",
        "pages_push_mock",
        "state_page_published",
        "bluesky_session_mock",
        "bluesky_blob_mock",
        "bluesky_post_mock",
        "state_social_posted",
    ]

    state = load_signal_wire_publication_state(tmp_path)
    record = state["signals"][event["signal_id"]]
    assert record["publication_status"] == "published"
    assert record["bluesky_status"] == "posted"
    assert record["post_uri"] == "at://did:plc:test/app.bsky.feed.post/1"
    assert (tmp_path / "output" / "site" / "food-line" / "wire" / event["signal_id"] / "index.html").exists()
    assert (tmp_path / "output" / "site" / "food-line" / "wire" / event["signal_id"] / "social.png").exists()

    duplicate = publish_signal_wire_event(
        tmp_path,
        event,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=False,
        post_bluesky=True,
        trace=[],
    )
    assert duplicate["reason"] == "already_posted"
    assert duplicate["trace"] == ["duplicate_detected", "no_pages_write", "no_bluesky_call"]


def test_bluesky_retry_after_page_published_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = _init_pages_repo(tmp_path)
    event = _eligible_event()

    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish.post_bluesky_external_card",
        lambda **kwargs: {
            "status": "failure",
            "post_uri": None,
            "post_cid": None,
            "reason": "mock_failure",
        },
    )
    first = publish_signal_wire_event(
        tmp_path,
        event,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=False,
        post_bluesky=True,
        trace=[],
    )
    assert first["pages_result"]["status"] == "committed"
    assert first["bluesky_result"]["status"] == "failure"
    assert first["status"] == "published_bluesky_failed"
    assert first["ok"] is True

    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish.post_bluesky_external_card",
        lambda **kwargs: {
            "status": "success",
            "post_uri": "at://did:plc:test/app.bsky.feed.post/2",
            "post_cid": "cid-2",
            "reason": None,
        },
    )
    second = publish_signal_wire_event(
        tmp_path,
        event,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=False,
        post_bluesky=True,
        trace=[],
    )
    assert second["reason"] is None
    assert "pages_validate" not in second["trace"]
    assert "pages_commit" not in second["trace"]
    assert second["bluesky_result"]["status"] == "success"
    state = load_signal_wire_publication_state(tmp_path)
    assert state["signals"][event["signal_id"]]["bluesky_status"] == "posted"
    assert second["status"] == "published"


def test_revision_requires_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = _init_pages_repo(tmp_path)
    event = _eligible_event()

    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish.post_bluesky_external_card",
        lambda **kwargs: {
            "status": "success",
            "post_uri": "at://did:plc:test/app.bsky.feed.post/3",
            "post_cid": "cid-3",
            "reason": None,
        },
    )
    publish_signal_wire_event(
        tmp_path,
        event,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=False,
        post_bluesky=True,
        trace=[],
    )

    revised = dict(event)
    revised["content_sha256"] = "different-content-sha"
    blocked = publish_signal_wire_event(
        tmp_path,
        revised,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        push=False,
        post_bluesky=True,
        trace=[],
    )
    assert blocked["reason"] == "material_update_requires_review"
    state = load_signal_wire_publication_state(tmp_path)
    assert state["signals"][event["signal_id"]]["revision_status"] == "material_update_requires_review"


def test_pages_push_failure_blocks_publication_and_bluesky(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = _init_pages_repo(tmp_path)
    event = _eligible_event()
    trace = []
    original_run_git = __import__("bluefern_dispatches.food_line_signal_wire_publish", fromlist=["_run_git"])._run_git

    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish.post_bluesky_external_card",
        lambda **kwargs: {"status": "success", "post_uri": "at://did:plc:test/app.bsky.feed.post/4", "post_cid": "cid-4", "reason": None},
    )
    monkeypatch.setattr(
        "bluefern_dispatches.food_line_signal_wire_publish._run_git",
        lambda repo, *args: subprocess.CompletedProcess(
            args=["git", "-C", str(repo), *args],
            returncode=1 if args and args[0] == "push" else original_run_git(repo, *args).returncode,
            stdout="" if args and args[0] == "push" else original_run_git(repo, *args).stdout,
            stderr="push failed" if args and args[0] == "push" else original_run_git(repo, *args).stderr,
        ),
    )
    monkeypatch.setattr("bluefern_dispatches.food_line_signal_wire_publish._repo_branch", lambda _repo: "gh-pages")

    with pytest.raises(RuntimeError, match="push failed"):
        publish_signal_wire_event(
            tmp_path,
            event,
            pages_repo=pages_repo,
            source_branch="agent/refine-care-line-signal-wire-public-rendering",
            pages_branch="gh-pages",
            push=True,
            post_bluesky=True,
            trace=trace,
        )

    state = load_signal_wire_publication_state(tmp_path)
    record = state["signals"][event["signal_id"]]
    assert record["publication_status"] == "pending"
    assert record["bluesky_status"] == "not_posted"
    assert trace == ["event_validated", "render", "pages_validate", "pages_commit"]


def test_live_pacific_date_changes_across_days(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDatetimeA:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 16, 6, 30, tzinfo=timezone.utc)

    class FakeDatetimeB:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(publish_module, "datetime", FakeDatetimeA)
    assert publish_module._pacific_date() == "2026-08-15"
    monkeypatch.setattr(publish_module, "datetime", FakeDatetimeB)
    assert publish_module._pacific_date() == "2026-08-16"
