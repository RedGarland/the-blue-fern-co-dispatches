from __future__ import annotations

from pathlib import Path

import pytest

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
