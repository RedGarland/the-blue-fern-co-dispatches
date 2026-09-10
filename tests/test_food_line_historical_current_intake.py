from __future__ import annotations

import json
from pathlib import Path

from scripts.complete_food_line_historical_current_intake import run_replay


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _finding(source_url: str, title: str, rank: int = 1) -> dict[str, object]:
    return {
        "finding_id": f"finding-{rank}",
        "title": title,
        "headline": title,
        "final_trace_url": source_url,
        "source_url": source_url,
        "canonical_source_url": source_url,
        "publisher": "Example News",
        "discovered_publisher": "Example News",
        "source_published_at": "2026-09-08",
        "location_name": "Example County",
        "state": "OR",
        "pressure_type": "pantry capacity strain",
        "summary": "The pantry closed permanently after reporting it could not meet current demand.",
        "exact_supporting_passage": "The pantry closed permanently after reporting it could not meet current demand.",
        "evidence_text": "The pantry closed permanently after reporting it could not meet current demand.",
        "eligible_for_review": True,
        "freshness_check": {"status": "current", "basis": "newly_published", "basis_date": "2026-09-08", "age_days": 0, "edition_date": "2026-09-08", "reason": "same-day retained artifact"},
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "proposed_rank": rank,
    }


def _seed_retained_artifacts(root: Path) -> None:
    run_id = "food-line-scheduled-20260908-fixture"
    _write_json(root / "data/dispatches/food-line/discovery-runs/2026-09-08" / run_id / "run-state.json", {"run_id": run_id, "edition_date": "2026-09-08", "status": "completed_with_exclusions"})
    _write_json(root / "data/dispatches/food-line/discovery-runs/2026-09-08" / run_id / "query-plan.json", {"edition_date": "2026-09-08", "queries": []})
    _write_json(root / "data/dispatches/food-line/discovery/2026-09-08/discovery_candidates.json", {"edition_date": "2026-09-08", "candidates": []})
    inbox = root / "data/dispatches/food-line/agent-inbox"
    _write_json(inbox / "food-line-source-watch-2026-09-07.json", {"edition_date": "2026-09-07", "agent_run_id": "wrong-day", "findings": [_finding("https://example.com/wrong", "Wrong day")]})
    _write_json(inbox / "food-line-source-watch-2026-09-08.json", {"edition_date": "2026-09-08", "agent_name": "Food Line Source Watch", "agent_run_id": run_id, "search_window": {"date_from": "2026-09-08", "date_to": "2026-09-08", "edition_date": "2026-09-08"}, "findings": [_finding("https://example.com/a", "Pantry demand strain", 1)]})


def test_historical_intake_replay_preserves_date_lineage_and_no_publication(tmp_path: Path) -> None:
    _seed_retained_artifacts(tmp_path)
    receipt = run_replay(tmp_path, historical_date="2026-09-08", inbox=tmp_path / "data/dispatches/food-line/agent-inbox", replayed_at="2026-09-10T00:00:00Z")

    assert receipt["schema_version"] == "food_line_historical_current_intake_replay_v1"
    assert receipt["historical_date"] == "2026-09-08"
    assert receipt["replayed_at"] == "2026-09-10T00:00:00Z"
    assert receipt["replay_reason"] == "missing_current_intake"
    assert receipt["network_access"] is False
    assert receipt["approved"] == 0
    assert receipt["publication_side_effects"] == {"public_output": False, "pages": False, "bluesky": False, "audio": False, "maps": False, "schedule": False}
    assert not (tmp_path / "output/site").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()

    report = json.loads((tmp_path / "data/dispatches/food-line/review/reports/2026-09-08/current-intake.json").read_text(encoding="utf-8"))
    assert report["historical_replay"] is True
    assert report["historical_date"] == "2026-09-08"
    assert report["selected_input_count"] == 1
    assert report["selected_inputs"][0]["path"].endswith("food-line-source-watch-2026-09-08.json")
    assert "2026-09-07" not in report["selected_inputs"][0]["path"]


def test_historical_intake_replay_is_idempotent_and_does_not_duplicate(tmp_path: Path) -> None:
    _seed_retained_artifacts(tmp_path)
    kwargs = {"historical_date": "2026-09-08", "inbox": tmp_path / "data/dispatches/food-line/agent-inbox", "replayed_at": "2026-09-10T00:00:00Z"}
    first = run_replay(tmp_path, **kwargs)
    second = run_replay(tmp_path, **kwargs)
    assert first["imported"] == second["imported"] == 1
    queue = json.loads((tmp_path / "data/dispatches/food-line/historical-intake/2026-09-08/current-signal-review.json").read_text(encoding="utf-8"))
    assert len(queue["items"]) == 1
    assert not (tmp_path / "data/dispatches/food-line/review/current-signal-review.json").exists()
    retained = list((tmp_path / "data/dispatches/food-line/historical-intake/2026-09-08/retained-inputs").glob("*.json"))
    assert len(retained) == 1


def test_historical_intake_replay_rejects_missing_retained_boundary(tmp_path: Path) -> None:
    _write_json(tmp_path / "data/dispatches/food-line/discovery-runs/2026-09-08/run/run-state.json", {"run_id": "run"})
    _write_json(tmp_path / "data/dispatches/food-line/discovery-runs/2026-09-08/run/query-plan.json", {"queries": []})
    _write_json(tmp_path / "data/dispatches/food-line/discovery/2026-09-08/discovery_candidates.json", {"candidates": []})
    try:
        run_replay(tmp_path, historical_date="2026-09-08", inbox=tmp_path / "data/dispatches/food-line/agent-inbox")
    except ValueError as exc:
        assert "no retained source-watch handoff" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected retained-boundary failure")





def test_historical_intake_replay_preserves_existing_current_queue(tmp_path: Path) -> None:
    _seed_retained_artifacts(tmp_path)
    current_queue = tmp_path / "data/dispatches/food-line/review/current-signal-review.json"
    current_queue.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"schema_version":"existing-current-state"}\n'
    current_queue.write_bytes(original)

    receipt = run_replay(tmp_path, historical_date="2026-09-08", inbox=tmp_path / "data/dispatches/food-line/agent-inbox", replayed_at="2026-09-10T00:00:00Z")

    assert current_queue.read_bytes() == original
    historical_queue = tmp_path / receipt["artifacts"]["historical_queue_snapshot"]
    assert historical_queue.exists()
    report = json.loads((tmp_path / "data/dispatches/food-line/review/reports/2026-09-08/current-intake.json").read_text(encoding="utf-8"))
    assert report["queue"]["path"] == "data/dispatches/food-line/historical-intake/2026-09-08/current-signal-review.json"

