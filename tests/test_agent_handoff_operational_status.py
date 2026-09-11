from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.operational_health import build_operational_receipt
from bluefern_dispatches.operational_status_exporter import build_food_line_status, export_status


DATE = "2026-09-10"
EVALUATED = "2026-09-10T16:00:00Z"


def _write_handoff(root: Path, dispatch: str, *, status: str, classification: str, run_id: str = "handoff-1", created: str = "2026-09-10T15:00:00Z", unaccounted: int = 0) -> None:
    path = root / "data/private-agent-handoff/receipts" / dispatch / DATE / f"{run_id}-{status.lower()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "bluefern.external_agent_handoff_receipt.v1",
                "dispatch": dispatch,
                "agent_run_id": run_id,
                "status": status,
                "classification": classification,
                "receipt_created_at": created,
                "unaccounted_count": unaccounted,
                "raw_agent_payload": "must not export",
                "supporting_passage": "must not export",
                "absolute_path": r"C:\private\payload.json",
            }
        ),
        encoding="utf-8",
    )


def _write_inbox(root: Path, dispatch: str, run_id: str) -> None:
    path = root / "data/private-agent-handoff/inbox" / dispatch / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agent_run_id": run_id, "raw_agent_payload": "private"}), encoding="utf-8")


def _scheduled_source(root: Path, *, failed: bool = False) -> Path:
    receipt_root = root / "status/operational-health/food-line" / DATE / "runs"
    receipt_root.mkdir(parents=True, exist_ok=True)
    tasks = (
        "food_line_source_watch",
        "food_line_source_watch_resume",
        "food_line_current_intake",
        "food_line_daily_publish",
    )
    for index, task_key in enumerate(tasks):
        artifact = root / "legacy" / f"{task_key}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")
        status = "FAILED" if failed and task_key == "food_line_source_watch" else "SUCCESS"
        receipt = build_operational_receipt(
            dispatch="food-line",
            task_key=task_key,
            task_name=task_key,
            scheduled_for=DATE,
            started_at=f"2026-09-10T15:{index:02d}:00Z",
            completed_at=f"2026-09-10T15:{index:02d}:30Z",
            observed_at=f"2026-09-10T15:{index:02d}:30Z",
            exit_code=1 if status == "FAILED" else 0,
            status=status,
            classification=status.lower(),
            run_id=f"scheduled-{index}",
            artifact_refs={"task_receipt": str(artifact)},
            public_side_effects={},
        )
        (receipt_root / f"{task_key}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return root


def test_no_receipt_is_not_expected_and_does_not_change_food_completeness(tmp_path: Path) -> None:
    source = _scheduled_source(tmp_path / "source")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "NO_EXTERNAL_HANDOFF_EXPECTED"
    assert status["receipt_completeness"] == "COMPLETE"
    assert status["aggregate_status"] == "SUCCESS"


def test_success_and_safe_noop_are_received_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_handoff(source, "food-line", status="SUCCESS", classification="ACCEPTED")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "HANDOFF_RECEIVED_SUCCESS"
    _write_handoff(source, "food-line", status="SAFE_NO_OP", classification="IDEMPOTENT_RETRY", run_id="handoff-2", created="2026-09-10T15:30:00Z")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "HANDOFF_RECEIVED_SUCCESS"


def test_failed_and_malformed_receipts_are_failed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_handoff(source, "food-line", status="FAILED", classification="IDEMPOTENCY_CONFLICT")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "HANDOFF_FAILED"
    _write_handoff(source, "food-line", status="FAILED", classification="MALFORMED", run_id="malformed-1", created="2026-09-10T16:00:00Z")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["latest_classification"] == "MALFORMED"


def test_unaccounted_success_is_not_reported_as_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_handoff(source, "food-line", status="SUCCESS", classification="ACCEPTED", unaccounted=1)
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "HANDOFF_FAILED"
    assert status["agent_handoff"]["unaccounted_count"] == 1


def test_delivered_without_terminal_receipt_is_stale(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_inbox(source, "food-line", "delivered-1")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "HANDOFF_STALE_UNPROCESSED"
    assert status["agent_handoff"]["stale"] is True


def test_failed_scheduled_task_remains_failed_when_handoff_succeeds(tmp_path: Path) -> None:
    source = _scheduled_source(tmp_path / "source", failed=True)
    _write_handoff(source, "food-line", status="SUCCESS", classification="ACCEPTED")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["aggregate_status"] == "FAILED"
    assert status["agent_handoff"]["state"] == "HANDOFF_RECEIVED_SUCCESS"


def test_care_is_not_migrated_but_system_exposes_handoff(tmp_path: Path) -> None:
    source = _scheduled_source(tmp_path / "source")
    _write_handoff(source, "care-line", status="FAILED", classification="MALFORMED")
    result = export_status(source_root=source, status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    care = result["system"]["dispatches"]["care-line"]
    assert care["migration_status"] == "NOT_MIGRATED"
    assert care["agent_handoff"]["state"] == "HANDOFF_FAILED"


def test_retired_artifacts_do_not_pollute_live_status(tmp_path: Path) -> None:
    source = tmp_path / "source"
    retired = source / "data/private-agent-handoff/retired/food-line/synthetic-proof/audit"
    retired.mkdir(parents=True)
    (retired / "receipt.json").write_text(json.dumps({"status": "FAILED", "dispatch": "food-line"}), encoding="utf-8")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["agent_handoff"]["state"] == "NO_EXTERNAL_HANDOFF_EXPECTED"


def test_handoff_status_exports_only_sanitized_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_handoff(source, "food-line", status="FAILED", classification="MALFORMED")
    result = export_status(source_root=source, status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    text = json.dumps(result)
    assert "raw_agent_payload" not in text
    assert "supporting_passage" not in text
    assert "must not export" not in text
    assert "C:\\private" not in text
    assert result["food_line"]["agent_handoff"]["latest_classification"] == "MALFORMED"
