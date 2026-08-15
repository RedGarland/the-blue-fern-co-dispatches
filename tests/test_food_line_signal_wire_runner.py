from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bluefern_dispatches import food_line_signal_wire_publish as publish_module
from bluefern_dispatches import food_line_signal_wire_runner as runner
from bluefern_dispatches.food_line_signal_wire_runner import run_signal_wire_intraday


def test_check_only_is_success_without_collection(tmp_path: Path) -> None:
    result = run_signal_wire_intraday(tmp_path, check_only=True)
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["candidate_count"] == 0
    assert result["eligible_new_count"] == 0


def test_dry_run_uses_discovery_and_renders_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bluefern_dispatches import food_line_signal_wire_runner as runner

    def fake_discovery(*args, **kwargs):
        return {
            "ok": True,
            "generated_at": "2026-08-15T12:00:00Z",
            "_candidate_records": [
                {
                    "candidate_id": "one",
                    "public_claim_eligible": True,
                    "public_summary": "A pantry reported current surge in food demand.",
                    "pressure_summary": "A pantry reported current surge in food demand.",
                    "canonical_source_url": "https://example.com/a",
                    "source_url": "https://example.com/a",
                    "publisher": "Example",
                    "title": "Example headline",
                    "source_published_at": "2026-08-15",
                    "state": "CA",
                    "location_name": "California",
                    "location_scope": "California, United States",
                    "pressure_category": "benefit access / policy",
                    "evidence_text": "A pantry reported current surge in food demand.",
                }
            ],
            "query_rows": [{}],
            "discovery_candidate_count": 1,
            "discovery_qualified_candidate_count": 1,
            "discovery_context_candidate_count": 0,
            "discovery_blocked_candidate_count": 0,
            "discovery_confidence": "limited",
            "discovery_confidence_reason": "ok",
        }

    monkeypatch.setattr(runner, "run_food_line_discovery_expansion", fake_discovery)
    monkeypatch.setattr(runner, "build_food_line_signal_wire_preview", lambda _root: {"examples": [{"signal_id": "one"}]})
    monkeypatch.setattr(runner, "write_food_line_signal_wire_preview", lambda _root: {"json_path": tmp_path / "preview.json", "html_path": tmp_path / "index.html"})
    result = run_signal_wire_intraday(tmp_path, dry_run=True, run_id="run-1")
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["candidate_count"] == 1
    assert result["eligible_new_count"] == 1
    assert result["events"][0]["classification"] == "eligible_new"
    assert result["would_post_bluesky"] is True
    assert (tmp_path / "output" / "review" / "food-line" / "signal-wire" / "live-dry-run" / "run-1" / "run.json").exists()


def test_lock_blocks_overlap(tmp_path: Path) -> None:
    lock = tmp_path / "status" / "food-line" / "locks" / "signal-wire.lock"
    lock.mkdir(parents=True)
    with pytest.raises(RuntimeError):
        run_signal_wire_intraday(tmp_path, dry_run=True)


def test_live_publication_uses_pacific_date_and_live_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 16, 7, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(publish_module, "datetime", FakeDatetime)

    def init_repo(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "agent/refine-care-line-signal-wire-public-rendering", str(path)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(path), "config", "user.email", "tester@example.com"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "Tester"], check=True, capture_output=True, text=True)
        (path / "README.md").write_text("root\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True, text=True)
        return path

    root_repo = init_repo(tmp_path / "root")
    pages_repo = init_repo(tmp_path / "pages")

    def fake_discovery(*args, **kwargs):
        calls.append(("discovery_as_of", args[1]))
        return {
            "ok": True,
            "generated_at": "2026-08-16T07:30:00Z",
            "source_count": 1,
            "query_rows": [{}],
            "candidates": [{"public_claim_eligible": True}],
        }

    def fake_build(candidate, *, as_of):
        calls.append(("build_as_of", as_of))
        return {
            "signal_id": "signal-1",
            "wire_auto_publish_eligible": True,
            "headline": "Example",
            "public_summary": "Example",
            "publisher": "Example Publisher",
            "state": "CA",
            "pressure_category": "benefit access / policy",
            "bluesky_post_text": "FOOD LINE | CA\n\nExample\n\nSource: Example Publisher",
            "card_description": "CA - benefit access / policy",
        }

    def fake_publish(root, event, **kwargs):
        calls.append(("push_flag", kwargs["push"]))
        calls.append(("event_asof", event["bluesky_post_text"]))
        return {"ok": True, "status": "published", "reason": None, "pages_result": {"status": "committed", "push_performed": True}, "bluesky_result": {"status": "success"}, "trace": []}

    monkeypatch.setattr("bluefern_dispatches.food_line_discovery_expansion.run_food_line_discovery_expansion", fake_discovery)
    monkeypatch.setattr("bluefern_dispatches.food_line_signal_wire.build_signal_wire_event_from_candidate", fake_build)
    monkeypatch.setattr(publish_module, "publish_signal_wire_event", fake_publish)

    result = publish_module.run_signal_wire_live_publication(
        root_repo,
        pages_repo=pages_repo,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        dry_run=False,
        post_bluesky=True,
    )

    assert result["ok"] is True
    assert result["as_of"] == "2026-08-16"
    assert calls[0] == ("discovery_as_of", "2026-08-16")
    assert calls[1] == ("build_as_of", "2026-08-16")
    assert ("push_flag", True) in calls
