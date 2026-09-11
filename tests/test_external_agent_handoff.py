from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.external_agent_handoff import import_envelope


def _envelope(dispatch: str = "food-line", run_id: str = "manual-run-1") -> dict:
    finding = {
        "finding_id": "finding-1",
        "final_trace_url": "https://example.org/pantry",
        "discovered_title": "Pantry distribution suspended",
        "discovered_publisher": "Example Food Bank",
        "evidence_text": "The pantry suspended distribution indefinitely and cannot serve every household seeking food.",
        "source_published_date": "2026-09-10",
        "retrieved_at": "2026-09-10T12:00:00Z",
        "location_name": "Example City",
        "state_abbrev": "CA",
    }
    if dispatch == "care-line":
        finding = {
            "finding_id": "care-1",
            "canonical_source_url": "https://example.org/clinic",
            "title": "Clinic suspends emergency service",
            "publisher": "Example Health",
            "exact_supporting_passage": "The clinic suspended emergency care indefinitely, reducing local access.",
            "source_published_date": "2026-09-10",
            "state": "CA",
            "city": "Example City",
            "event_type": "service_suspension",
            "service_line": "emergency_care",
            "access_consequences": ["REDUCED_SERVICE_AVAILABILITY"],
        }
    return {
        "schema_version": 1,
        "agent_name": "Manual External Source Watch",
        "agent_run_id": run_id,
        "started_at": "2026-09-10T12:00:00Z",
        "completed_at": "2026-09-10T12:05:00Z",
        "search_window": {"date_from": "2026-09-10", "date_to": "2026-09-10", "edition_date": "2026-09-10"},
        "findings": [finding],
        "coverage_notes": "manual fixture",
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_food_accepts_and_archives_then_identical_retry_is_safe_noop(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    _write(source, _envelope())
    code, receipt = import_envelope(tmp_path, source, dispatch="food-line")
    assert code == 0
    assert receipt["status"] == "SUCCESS"
    assert receipt["unaccounted"] == 0
    assert (tmp_path / receipt["archive_ref"]).read_bytes() == source.read_bytes()

    retry_code, retry = import_envelope(tmp_path, source, dispatch="food-line")
    assert retry_code == 0
    assert retry["status"] == "SAFE_NO_OP"


def test_same_run_different_hash_is_conflict_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    _write(source, _envelope())
    assert import_envelope(tmp_path, source, dispatch="food-line")[0] == 0
    changed = _envelope()
    changed["coverage_notes"] = "changed"
    _write(source, changed)
    code, result = import_envelope(tmp_path, source, dispatch="food-line")
    assert code != 0
    assert result["status"] == "FAILED"
    assert result["classification"] == "IDEMPOTENCY_CONFLICT"
    assert result["existing_archive_sha256"]
    assert Path(tmp_path / result["receipt_ref"]).exists()


def test_malformed_and_unsupported_dispatch_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    _write(source, {"schema_version": 2})
    code, result = import_envelope(tmp_path, source, dispatch="food-line")
    assert code != 0
    assert result["classification"] == "MALFORMED"
    _write(source, _envelope())
    code, result = import_envelope(tmp_path, source, dispatch="gaza")
    assert code != 0
    assert result["classification"] == "MALFORMED"


def test_care_import_uses_normalized_registry_and_review_queue(tmp_path: Path) -> None:
    source = tmp_path / "care.json"
    _write(source, _envelope("care-line", "care-run-1"))
    code, receipt = import_envelope(tmp_path, source, dispatch="care-line")
    assert code == 0
    assert receipt["imported_count"] == 1
    assert receipt["reconciliation"][0]["candidate_id"]
    registry = json.loads((tmp_path / "data/dispatches/care-line/review/candidate-registry.json").read_text(encoding="utf-8"))
    queue = json.loads((tmp_path / "data/dispatches/care-line/review/current-review-queue.json").read_text(encoding="utf-8"))
    assert registry["candidates"][0]["metadata"]["review_status"] == "pending_review"
    assert queue["queue_item_count"] == 1


def test_receipt_is_symbolic_and_reconciliation_is_terminal(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    _write(source, _envelope())
    code, receipt = import_envelope(tmp_path, source, dispatch="food-line")
    assert code == 0
    rendered = (tmp_path / "data/private-agent-handoff/receipts/food-line/2026-09-10/manual-run-1.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in rendered
    assert "raw_payload" not in rendered
    assert all(row["unaccounted"] is False for row in receipt["reconciliation"])
