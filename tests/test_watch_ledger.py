from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.watch_ledger import (
    RECONCILIATION_SCHEMA,
    SCHEMA_VERSION,
    persist_watch_run,
    reconcile_day,
    validate_watch_run,
)


def _run(*, dispatch: str = "care-line", outcome: str = "findings", status: str = "SUCCESS") -> dict:
    findings = [] if outcome != "findings" else [
        {
            "finding_id": "care-123",
            "canonical_source_url": "https://example.org/closure",
            "title": "Clinic closes",
        }
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "dispatch": dispatch,
        "watch_name": "Care Line Watch",
        "run_id": "20260925T120000Z-care",
        "scheduled_for": "2026-09-25T12:00:00Z",
        "started_at": "2026-09-25T12:00:02Z",
        "completed_at": "2026-09-25T12:01:00Z",
        "status": status,
        "outcome": outcome,
        "search_window": {"date_from": "2026-09-25", "date_to": "2026-09-25", "edition_date": "2026-09-25"},
        "findings": findings,
        "coverage_notes": "fixture",
    }


def test_zero_result_run_is_valid_and_durable(tmp_path: Path) -> None:
    payload = _run(outcome="no_findings")
    assert validate_watch_run(payload) == []
    result = persist_watch_run(tmp_path, payload)
    assert result["status"] == "SUCCESS"
    retry = persist_watch_run(tmp_path, payload)
    assert retry["status"] == "SAFE_NO_OP"


def test_conflicting_same_run_id_fails_closed(tmp_path: Path) -> None:
    payload = _run(outcome="no_findings")
    persist_watch_run(tmp_path, payload)
    payload["coverage_notes"] = "changed"
    with pytest.raises(FileExistsError):
        persist_watch_run(tmp_path, payload)


def test_failed_run_requires_failed_outcome() -> None:
    payload = _run(outcome="no_findings", status="FAILED")
    assert any("FAILED status requires failed outcome" in item for item in validate_watch_run(payload))


def test_reconciliation_flags_unaccounted_then_accounts_matching_production(tmp_path: Path) -> None:
    payload = _run()
    persist_watch_run(tmp_path, payload)

    report = reconcile_day(tmp_path, "care-line", "2026-09-25")
    assert report["schema_version"] == RECONCILIATION_SCHEMA
    assert report["run_count"] == 1
    assert report["unreconciled_count"] == 1
    assert report["publication_authorized"] is False

    production = tmp_path / "data/dispatches/care-line/review"
    production.mkdir(parents=True)
    (production / "candidate-registry.json").write_text(
        json.dumps({"source": "https://example.org/closure"}),
        encoding="utf-8",
    )
    report = reconcile_day(tmp_path, "care-line", "2026-09-25", write=False)
    assert report["accounted_count"] == 1
    assert report["unreconciled_count"] == 0


def test_reconciliation_records_zero_finding_heartbeat(tmp_path: Path) -> None:
    persist_watch_run(tmp_path, _run(dispatch="gaza", outcome="no_findings"))
    report = reconcile_day(tmp_path, "gaza", "2026-09-25", write=False)
    assert report["run_count"] == 1
    assert report["zero_finding_run_count"] == 1
    assert report["finding_count"] == 0
