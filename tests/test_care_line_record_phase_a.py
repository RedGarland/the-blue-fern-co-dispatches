from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.care_line_evidence_review import load_reviewed_records
from bluefern_dispatches.care_line_record import (
    CareLineReviewedRecord,
    corrected_record,
    deterministic_records_json,
)
from bluefern_dispatches.universal_events.care_line_signal_wire import READY_RECORD_IDS


REPO = Path(__file__).resolve().parents[1]
REVIEWED_2026_07_22 = REPO / "data" / "dispatches" / "care-line" / "reviewed" / "2026-07-22" / "reviewed_records.json"


def test_phase_a_reviewed_record_module_imports_and_parses_repo_snapshot() -> None:
    records = load_reviewed_records(REVIEWED_2026_07_22)
    assert records
    assert all(isinstance(record, CareLineReviewedRecord) for record in records)
    assert READY_RECORD_IDS <= {record.producer_record_id for record in records}


def test_phase_a_reviewed_records_serialize_deterministically() -> None:
    records = load_reviewed_records(REVIEWED_2026_07_22)
    first = deterministic_records_json(records)
    second = deterministic_records_json(records)
    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == "bluefern.care_line.reviewed_record.v1"
    assert len(payload["records"]) == len(records)


def test_phase_a_corrected_record_preserves_review_contract_and_queue_publication_keys() -> None:
    record = load_reviewed_records(REVIEWED_2026_07_22)[0]
    updated = corrected_record(
        record,
        updates={"claim_summary": "Corrected summary for Phase A"},
        reviewer="phase-a",
        reason="phase a compatibility test",
        decided_at="2026-08-04T00:00:00Z",
    )

    assert updated.version == record.version + 1
    assert updated.supersedes_record_id == record.version_id
    assert updated.review_status == "corrected"
    assert updated.producer_record_id == record.producer_record_id
    assert updated.to_adapter_record()["source_record_id"] == record.producer_record_id
    assert updated.to_adapter_record()["_care_line_reviewed_record_contract"]["schema_version"] == "bluefern.care_line.reviewed_record.v1"
