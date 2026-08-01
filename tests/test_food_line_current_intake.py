from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.food_line_current_intake import process_batch


def _row(**overrides):
    row = {
        "title": "Pantry closes after supply loss",
        "publisher": "Example News",
        "source_url": "https://example.org/pantry-closes",
        "source_published_at": "2026-08-01",
        "exact_supporting_passage": "The closed pantry left clients without food after losing its remaining supply.",
        "summary": "A local food-access point closed.",
        "location_name": "Example City", "state": "CA", "location_scope": "city",
        "pressure_type": "service reduction", "source_role": "local_signal", "affected_groups": ["pantry clients"],
        "confidence": "high", "evidence_level": "direct_reporting",
    }
    row.update(overrides)
    return row


def _envelope(run_id: str, *rows):
    return {
        "schema_version": "food_line_agent_run_v1", "agent_name": "fixture",
        "agent_run_id": run_id, "started_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:01:00Z", "search_window": {"date": "2026-08-01"},
        "findings": list(rows), "coverage_notes": "test",
    }


def test_mixed_inbox_dry_runs_then_imports_only_valid_files(tmp_path: Path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "valid.json").write_text(json.dumps(_envelope("run-valid", _row())), encoding="utf-8")
    (inbox / "bad.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    result = process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True, build_proposed=True)
    assert result["status"] == "success_with_exclusions"
    assert result["dry_run_count"] == 1 and result["import_count"] == 1
    assert result["proposal"]["draft_status"] == "draft_pending_editorial_review"
    assert not (tmp_path / "output/site").exists()
    assert (tmp_path / "data/dispatches/food-line/review/reports/2026-08-01/current-intake.json").exists()


def test_duplicate_url_is_rejected_before_any_import(tmp_path: Path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "one.json").write_text(json.dumps(_envelope("run-one", _row())), encoding="utf-8")
    (inbox / "two.json").write_text(json.dumps(_envelope("run-two", _row(title="Same article"))), encoding="utf-8")
    result = process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox)
    assert result["status"] == "success_with_exclusions"
    assert result["import_count"] == 1


def test_multiple_findings_and_duplicate_run_id_are_handled_fail_closed(tmp_path: Path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "many.json").write_text(json.dumps(_envelope("run-many", _row(), _row(source_url="https://example.org/second", title="Second pantry closure"))), encoding="utf-8")
    (inbox / "same-run.json").write_text(json.dumps(_envelope("run-many", _row(source_url="https://example.org/third", title="Third pantry closure"))), encoding="utf-8")
    result = process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True, build_proposed=True)
    assert result["status"] == "success_with_exclusions"
    assert result["dry_run_count"] == 1 and result["import_count"] == 1
    assert result["proposal"]["selected_item_count"] == 2


def test_material_change_returns_decided_item_to_rereview(tmp_path: Path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    source = inbox / "run.json"
    source.write_text(json.dumps(_envelope("run-one", _row())), encoding="utf-8")
    process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True)
    queue_path = tmp_path / "data/dispatches/food-line/review/current-signal-review.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][0]["editorial_status"] = "approve"
    queue["items"][0]["editorial_note"] = "Approved before source revision."
    queue["items"][0]["decision_audit"] = {"decided_at": "2026-08-01T02:00:00Z", "decided_by": "operator", "decision": "approve"}
    from bluefern_dispatches.food_line_current_review import write_json_atomic
    write_json_atomic(queue_path, queue)
    source.write_text(json.dumps(_envelope("run-two", _row(exact_supporting_passage="The closed pantry lost all remaining supply and reopened with limited distribution."))), encoding="utf-8")
    process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True)
    changed = json.loads(queue_path.read_text(encoding="utf-8"))["items"][0]
    assert changed["review_item_id"] == queue["items"][0]["review_item_id"]
    assert changed["editorial_status"] == "pending_editorial_review"
    assert changed["rereview_required"] is True


def test_repeat_batch_is_idempotent_and_preserves_operator_decision(tmp_path: Path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    source = inbox / "run.json"; source.write_text(json.dumps(_envelope("run-one", _row())), encoding="utf-8")
    first = process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True, build_proposed=True)
    queue_path = tmp_path / "data/dispatches/food-line/review/current-signal-review.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][0]["editorial_status"] = "approve"
    queue["items"][0]["editorial_note"] = "Operator approved draft assembly."
    queue["items"][0]["decision_audit"] = {"decided_at": "2026-08-01T02:00:00Z", "decided_by": "operator", "decision": "approve"}
    queue["items"][0]["publication_eligible"] = False
    from bluefern_dispatches.food_line_current_review import write_json_atomic
    write_json_atomic(queue_path, queue)
    second = process_batch(tmp_path, edition_date="2026-08-01", inbox=inbox, build_review_queue=True, build_proposed=True)
    final = json.loads(queue_path.read_text(encoding="utf-8"))
    assert first["import_count"] == 1 and second["import_count"] == 1
    assert final["items"][0]["editorial_status"] == "approve"
    assert final["items"][0]["decision_audit"]["decided_at"] == "2026-08-01T02:00:00Z"
    assert second["proposal"]["draft_status"] == "draft_approved_pending_publication"
