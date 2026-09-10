from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import RecoveryContext, build_operational_receipt
from bluefern_dispatches.operational_status_exporter import (
    ExportError,
    build_food_line_status,
    exporter_lock,
    export_status,
    validate_status_paths,
)


DATE = "2026-09-10"
EVALUATED = "2026-09-10T16:00:00Z"
TASKS = {
    "food_line_source_watch": ("source_watch", "failed", 10),
    "food_line_source_watch_resume": ("status_resume", "upstream_blocked", 0),
    "food_line_current_intake": ("current_intake", "upstream_blocked", 0),
    "food_line_daily_publish": ("daily_publish", "skipped_not_release_ready", 0),
}


def _write_day(tmp_path: Path, statuses: dict[str, tuple[str, str, int]] | None = None) -> Path:
    source = tmp_path / "source"
    receipt_root = source / "status" / "operational-health" / "food-line" / DATE / "runs"
    receipt_root.mkdir(parents=True)
    statuses = statuses or TASKS
    for index, (task_key, (action, task_status, exit_code)) in enumerate(statuses.items()):
        task_path = source / "legacy" / f"{task_key}.json"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text("{}\n", encoding="utf-8")
        receipt = build_operational_receipt(
            dispatch="food-line",
            task_key=task_key,
            task_name=task_key,
            scheduled_for=DATE,
            started_at=f"2026-09-10T15:{index:02d}:00Z",
            completed_at=f"2026-09-10T15:{index:02d}:30Z",
            observed_at=f"2026-09-10T15:{index:02d}:30Z",
            exit_code=exit_code,
            status={
                "failed": "FAILED",
                "upstream_blocked": "UPSTREAM_BLOCKED",
                "skipped_not_release_ready": "SAFE_NO_OP",
                "completed": "SUCCESS",
            }.get(task_status, task_status),
            classification=task_status,
            run_id=f"run-{index}",
            runner_path=r"C:\BlueFernRunner\FoodLineCurrent6",
            source_head="5f5f3e84a967219e953876a6eafca1119e448912",
            artifact_refs={"task_receipt": str(task_path)},
            publication_attempted=False if task_key == "food_line_daily_publish" else None,
            publication_status="skipped" if task_key == "food_line_daily_publish" else None,
            public_side_effects={"absolute_path": r"C:\BlueFernRunner\private", "ok": True},
            details={"source_body": "private article body", "safe_detail": "kept local"},
        )
        (receipt_root / f"{task_key}-run-{index}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return source


def test_failed_day_is_complete_and_publish_noop_does_not_mask_failure(tmp_path: Path) -> None:
    source = _write_day(tmp_path)
    status = build_food_line_status(
        source_root=source,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T16:01:00Z",
        recovery=RecoveryContext(
            incident_opened_at="2026-09-10T12:30:00Z",
            fix_deployed_at="2026-09-10T14:00:00Z",
            runtime_proof_after="2026-09-10T14:00:00Z",
        ),
    )
    assert status["aggregate_status"] == "FAILED"
    assert status["receipt_completeness"] == "COMPLETE"
    assert status["recovery_lifecycle"] == "RECOVERY_PENDING_RUNTIME_PROOF"
    assert status["publication_attempted"] is False


def test_complete_success_day_is_healthy(tmp_path: Path) -> None:
    statuses = {key: (action, "completed", 0) for key, (action, _, _) in TASKS.items()}
    source = _write_day(tmp_path, statuses)
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["aggregate_status"] == "SUCCESS"
    assert status["receipt_completeness"] == "COMPLETE"


def test_missing_task_is_partial_and_missing_file_linkage_is_inconsistent(tmp_path: Path) -> None:
    statuses = dict(TASKS)
    statuses.pop("food_line_daily_publish")
    source = _write_day(tmp_path, statuses)
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["receipt_completeness"] == "PARTIAL"

    receipt = next((source / "status" / "operational-health" / "food-line" / DATE / "runs").glob("*.json"))
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["artifact_refs"]["task_receipt"] = str(source / "missing.json")
    receipt.write_text(json.dumps(value), encoding="utf-8")
    status = build_food_line_status(source_root=source, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert status["receipt_completeness"] == "INCONSISTENT"


def test_stale_observability_is_distinct_from_failed(tmp_path: Path) -> None:
    source = _write_day(tmp_path, {"food_line_source_watch": TASKS["food_line_source_watch"]})
    status = build_food_line_status(
        source_root=source,
        date=DATE,
        evaluated_at="2026-09-11T12:00:00Z",
        exported_at="2026-09-11T12:01:00Z",
    )
    assert status["aggregate_status"] == "STALE_OBSERVABILITY"
    assert status["stale_observability"] is True


def test_recovery_requires_later_successful_runtime_proof(tmp_path: Path) -> None:
    context = RecoveryContext(
        incident_opened_at="2026-09-10T12:30:00Z",
        fix_deployed_at="2026-09-10T14:00:00Z",
        runtime_proof_after="2026-09-10T14:00:00Z",
    )
    failed = build_food_line_status(source_root=_write_day(tmp_path), date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED, recovery=context)
    assert failed["recovery_lifecycle"] == "RECOVERY_PENDING_RUNTIME_PROOF"

    success_statuses = {key: (action, "completed", 0) for key, (action, _, _) in TASKS.items()}
    success = build_food_line_status(
        source_root=_write_day(tmp_path / "success", success_statuses),
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at=EVALUATED,
        recovery=RecoveryContext(**{**context.__dict__, "recovered_at": "2026-09-10T16:00:00Z"}),
    )
    assert success["recovery_lifecycle"] == "RECOVERED"


def test_export_is_idempotent_and_atomic(tmp_path: Path) -> None:
    source = _write_day(tmp_path)
    checkout = tmp_path / "status-checkout"
    export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    paths = [checkout / "ops" / "status" / "food-line" / "latest.json", checkout / "ops" / "status" / "food-line" / "history" / f"{DATE}.json", checkout / "ops" / "status" / "system" / "latest.json"]
    before = [path.read_bytes() for path in paths]
    export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T17:00:00Z")
    assert before == [path.read_bytes() for path in paths]
    assert not list(checkout.rglob("*.tmp"))


def test_lock_prevents_overlap_and_status_scope_is_enforced(tmp_path: Path) -> None:
    with exporter_lock(tmp_path):
        with pytest.raises(ExportError):
            with exporter_lock(tmp_path):
                pass
    validate_status_paths(["ops/status/food-line/latest.json"])
    with pytest.raises(ExportError):
        validate_status_paths(["output/site/index.html"])


def test_system_marks_non_migrated_dispatches_without_calling_them_failed(tmp_path: Path) -> None:
    result = export_status(source_root=_write_day(tmp_path), status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    dispatches = result["system"]["dispatches"]
    assert dispatches["food-line"]["migration_status"] == "MIGRATED"
    assert dispatches["gaza"]["migration_status"] == "NOT_MIGRATED"
    assert dispatches["gaza"]["aggregate_status"] == "UNKNOWN"


def test_sanitizer_excludes_local_paths_and_private_material(tmp_path: Path) -> None:
    result = export_status(source_root=_write_day(tmp_path), status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    text = json.dumps(result)
    assert "C:\\BlueFernRunner" not in text
    assert "private article body" not in text
    assert "source_body" not in text
    assert "5f5f3e84a967219e953876a6eafca1119e448912" in text


def test_publication_state_is_separate_from_health(tmp_path: Path) -> None:
    result = export_status(source_root=_write_day(tmp_path), status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    assert result["food_line"]["aggregate_status"] == "FAILED"
    assert result["food_line"]["publication_attempted"] is False
