from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import (
    RecoveryContext,
    build_care_line_operational_receipt,
    build_operational_receipt,
)
from bluefern_dispatches.operational_status_exporter import (
    ExportError,
    build_care_line_status,
    build_ice_status,
    build_food_line_status,
    classify_status_checkout_state,
    commit_and_push_status,
    exporter_lock,
    export_status,
    prepare_status_checkout,
    rebuild_dispatch_status_artifacts,
    validate_status_paths,
    _status_file_hashes,
)


DATE = "2026-09-10"
EVALUATED = "2026-09-10T16:00:00Z"
CARE_EXPECTED_INSTANCES = [
    {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T15:00:00Z"},
    {"task_key": "care_line_reviewed_event_queue", "scheduled_for": "2026-09-10T15:01:00Z"},
    {"task_key": "care_line_approved_release_publication", "scheduled_for": "2026-09-10T15:02:00Z"},
]
CARE_EXPECTED_WITH_OLDER_COLLECTION = [
    {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T08:00:00Z"},
    *CARE_EXPECTED_INSTANCES,
]
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
        (receipt_root / f"run-{index}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return source


def _write_food_sep23_recovery_sequence(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    date = "2026-09-23"
    run_id = "food-line-scheduled-sep23"
    receipt_root = source / "status" / "operational-health" / "food-line" / date / "runs"
    receipt_root.mkdir(parents=True)

    export_path = source / "data" / "dispatches" / "food-line" / "agent-inbox" / "food-line-source-watch-2026-09-23-107adc7ac7.json"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text('{"schema_version":"food_line_source_watch_agent_export_v1","findings":[1,2,3]}\n', encoding="utf-8")
    state_path = source / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_bounded_run_state_v1",
                "edition_date": date,
                "run_id": run_id,
                "status": "completed_with_exclusions",
                "final_error": "",
                "agent_export": {
                    "status": "success_with_exclusions",
                    "path": str(export_path),
                },
            }
        ),
        encoding="utf-8",
    )
    record_path = source / "status" / "food-line" / "runs" / f"{date}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_scheduled_run_record_v1",
                "edition_date": date,
                "run_id": run_id,
                "source_watch_status": "completed_with_exclusions",
                "last_status": "completed_with_exclusions",
                "run_state_path": str(state_path),
            }
        ),
        encoding="utf-8",
    )

    rows = [
        (
            "z-resume-blocked.json",
            "food_line_source_watch_resume",
            "Blue Fern Food Line Source Watch Resume",
            "UPSTREAM_BLOCKED",
            "source_watch_not_initialized",
            "",
            "2026-09-23T15:07:37Z",
            {"final_status": "source_watch_not_initialized", "resume_status": "source_watch_not_initialized", "run_id": None},
        ),
        (
            "y-intake-blocked.json",
            "food_line_current_intake",
            "Blue Fern Food Line Current Intake",
            "UPSTREAM_BLOCKED",
            "source_watch_not_initialized",
            "",
            "2026-09-23T15:07:38Z",
            {"status": "source_watch_not_initialized", "qualifying_discovery_run_id": None},
        ),
        (
            "a-source-watch-ready.json",
            "food_line_source_watch",
            "Blue Fern Food Line Daily Source Watch",
            "DEGRADED",
            "completed_with_exclusions",
            run_id,
            "2026-09-23T15:16:02Z",
            {
                "action": "source_watch",
                "edition_date": date,
                "run_id": run_id,
                "final_status": "completed_with_exclusions",
                "export_status": "success_with_exclusions",
            },
        ),
        (
            "b-current-intake-recovered.json",
            "food_line_current_intake",
            "Blue Fern Food Line Current Intake",
            "SUCCESS",
            "success",
            run_id,
            "2026-09-23T15:45:18Z",
            {"status": "draft_pending_editorial_review", "qualifying_discovery_run_id": run_id},
        ),
        (
            "c-daily-publish-noop.json",
            "food_line_daily_publish",
            "Blue Fern Food Line Daily Publish",
            "SAFE_NO_OP",
            "skipped_not_release_ready",
            "20260923T153002Z-32396-2295029d",
            "2026-09-23T15:30:02Z",
            {"status": "skipped_not_release_ready", "publication_attempted": False},
        ),
    ]
    for filename, task_key, task_name, status, classification, receipt_run_id, completed_at, task_payload in rows:
        artifact = source / "legacy" / filename
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(task_payload), encoding="utf-8")
        receipt = build_operational_receipt(
            dispatch="food-line",
            task_key=task_key,
            task_name=task_name,
            scheduled_for=date,
            started_at=completed_at,
            completed_at=completed_at,
            observed_at=completed_at,
            exit_code=0,
            status=status,
            classification=classification,
            run_id=receipt_run_id,
            runner_path=r"C:\BlueFernRunner\FoodLineCurrent6",
            source_head="50599cc68e613a4a286896fc2247b9492e1e8262",
            artifact_refs={"task_receipt": str(artifact)},
            publication_attempted=False if task_key == "food_line_daily_publish" else None,
            publication_status="skipped_not_release_ready" if task_key == "food_line_daily_publish" else None,
            public_side_effects={"public_output": False},
            details={
                "edition_date": date,
                "source_status": "completed_with_exclusions" if task_key == "food_line_source_watch" else classification,
                "source_export_status": "success_with_exclusions" if task_key == "food_line_source_watch" else "",
            },
        )
        (receipt_root / filename).write_text(json.dumps(receipt), encoding="utf-8")
    return source


def _write_care_day(tmp_path: Path, *, include_older_collection: bool = False) -> Path:
    source = tmp_path / "care-source"
    receipt_root = source / "status" / "operational-health" / "care-line" / DATE / "runs"
    receipt_root.mkdir(parents=True)
    rows = [
        ("care_line_collection", "collection", "partial_success", None, "2026-09-10T15:00:00Z"),
        ("care_line_reviewed_event_queue", "queue", "nothing_to_publish", None),
        ("care_line_approved_release_publication", "publication", "safe_no_op", "safe_no_op"),
    ]
    if include_older_collection:
        rows.insert(0, ("care_line_collection", "old-collection", "partial_success", None, "2026-09-10T08:00:00Z"))
    for index, row in enumerate(rows):
        task_key, run_id, task_status, publication_status, *times = row
        started_at = times[0] if times else f"2026-09-10T15:{index:02d}:00Z"
        completed_at = started_at.replace(":00Z", ":30Z")
        artifact = source / "status" / "care-line" / "scheduler-runs" / DATE / f"{run_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")
        receipt = build_care_line_operational_receipt(
            task_key=task_key,
            scheduled_for=DATE,
            started_at=started_at,
            completed_at=completed_at,
            exit_code=0,
            task_status=task_status,
            run_id=run_id,
            runner_path=r"C:\BlueFernRunner\CareLineNationalCurrent8",
            branch="add/pages-repo-default",
            source_head="6dd7e79411c11078dca4272075058c80ac8d2198",
            artifact_refs={"task_receipt": str(artifact)},
            publication_attempted=False if task_key == "care_line_approved_release_publication" else None,
            publication_status=publication_status,
            public_side_effects={"pages_sync": False, "source_text": "private"},
            details={"source_body": "private care body", "safe_detail": "kept local"},
        )
        (receipt_root / f"{run_id}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return source


def _write_care_receipt(
    source: Path,
    *,
    receipt_date: str,
    task_key: str,
    run_id: str,
    task_status: str,
    scheduled_for: str,
    started_at: str,
    completed_at: str,
) -> None:
    artifact = source / "status" / "care-line" / "scheduler-runs" / receipt_date / f"{run_id}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}\n", encoding="utf-8")
    receipt = build_care_line_operational_receipt(
        task_key=task_key,
        scheduled_for=scheduled_for,
        started_at=started_at,
        completed_at=completed_at,
        exit_code=0,
        task_status=task_status,
        run_id=run_id,
        runner_path=r"C:\BlueFernRunner\CareLineNationalCurrent8",
        branch="add/pages-repo-default",
        source_head="6dd7e79411c11078dca4272075058c80ac8d2198",
        artifact_refs={"task_receipt": str(artifact)},
        public_side_effects={"pages_sync": False},
    )
    receipt_root = source / "status" / "operational-health" / "care-line" / receipt_date / "runs"
    receipt_root.mkdir(parents=True, exist_ok=True)
    (receipt_root / f"{run_id}.json").write_text(json.dumps(receipt), encoding="utf-8")


def _write_ice_day(
    tmp_path: Path,
    *,
    status: str = "SUCCESS",
    classification: str = "healthy",
    unaccounted: int = 0,
    date: str = DATE,
    include_task_receipt: bool = True,
) -> Path:
    source = tmp_path / "ice-source"
    receipt_root = source / "status" / "operational-health" / "ice" / date / "runs"
    receipt_root.mkdir(parents=True)
    artifact = source / "data" / "dispatches" / "ice" / "monitor" / "runs" / date / "ice-monitor-1" / "monitor_receipt.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}\n", encoding="utf-8")
    artifact_refs = {
        "monitor_receipt": str(artifact),
        "operator_summary": str(artifact.with_name("operator_summary.json")),
        "terminal_reconciliation": str(artifact.with_name("terminal_reconciliation.json")),
    }
    if include_task_receipt:
        artifact_refs["task_receipt"] = str(artifact)
    receipt = build_operational_receipt(
        dispatch="ice",
        task_key="ice_monitor",
        task_name="Daily - ICE Monitor",
        scheduled_for=date,
        started_at="2026-09-11T04:15:00Z",
        completed_at="2026-09-11T04:16:00Z",
        observed_at="2026-09-11T04:16:00Z",
        exit_code=0 if status != "FAILED" else 2,
        status=status,
        classification=classification,
        run_id="ice-monitor-1",
        runner_path=r"C:\BlueFernRunner\ICEMonitorCurrent",
        branch="add/pages-repo-default",
        source_head="26ab691dfeac1a2d94682d9205f76a245cbf92ad",
        artifact_refs=artifact_refs,
        public_side_effects={"pages": False, "publication": False, "rss": False, "audio": False, "social": False, "source_text": "private"},
        publication_attempted=False,
        publication_status="not_authorized_monitor_only",
        details={
            "configured_providers": 16,
            "attempted_providers": 13,
            "successful_providers": 13 if status != "FAILED" else 0,
            "failed_providers": 0 if status != "FAILED" else 13,
            "canonical_events": 0 if status == "SAFE_NO_OP" else 8,
            "unaccounted": unaccounted,
            "source_body": "private source body",
        },
    )
    (receipt_root / "ice-monitor-1.json").write_text(json.dumps(receipt), encoding="utf-8")
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


def test_food_line_recovered_source_watch_sequence_uses_effective_timestamp_state(tmp_path: Path) -> None:
    source = _write_food_sep23_recovery_sequence(tmp_path)

    status = build_food_line_status(
        source_root=source,
        date="2026-09-23",
        evaluated_at="2026-09-23T16:00:00Z",
        exported_at="2026-09-23T16:01:00Z",
    )

    assert status["receipt_completeness"] == "COMPLETE"
    assert status["aggregate_status"] == "DEGRADED"
    assert len(status["task_summaries"]) == 5
    assert any(row["classification"] == "source_watch_not_initialized" for row in status["task_summaries"])
    effective = {row["task_key"]: row for row in status["effective_task_summaries"]}
    assert effective["food_line_source_watch"]["classification"] == "completed_with_exclusions"
    assert effective["food_line_current_intake"]["status"] == "SUCCESS"
    assert effective["food_line_daily_publish"]["status"] == "SAFE_NO_OP"
    assert "food_line_source_watch_resume" not in effective


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


def test_rebuild_dispatch_status_artifacts_writes_only_local_dispatch_status(tmp_path: Path) -> None:
    success_statuses = {key: (action, "completed", 0) for key, (action, _, _) in TASKS.items()}
    source = _write_day(tmp_path, success_statuses)

    result = rebuild_dispatch_status_artifacts(
        source_root=source,
        dispatch="food-line",
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at=EVALUATED,
    )

    assert result["paths"] == [
        "ops/status/food-line/latest.json",
        f"ops/status/food-line/history/{DATE}.json",
    ]
    assert (source / "ops/status/food-line/latest.json").is_file()
    assert (source / "ops/status/food-line/history" / f"{DATE}.json").is_file()
    assert not (source / "ops/status/system/latest.json").exists()
    assert result["status"]["dispatch"] == "food-line"


def test_export_reuses_food_timestamp_but_advances_changed_system_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import bluefern_dispatches.operational_status_exporter as exporter

    source = _write_day(tmp_path)
    checkout = tmp_path / "status-checkout"
    first = "2026-09-10T16:01:00Z"
    second = "2026-09-10T17:01:00Z"
    monkeypatch.setattr(exporter, "NON_MIGRATED_DISPATCHES", ("gaza", "care-line", "ice", "american-pressure", "cascadia"))
    monkeypatch.setattr(exporter, "INTENTIONALLY_INACTIVE_DISPATCHES", ())
    export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at=first)

    monkeypatch.setattr(exporter, "NON_MIGRATED_DISPATCHES", ("gaza", "care-line", "ice", "american-pressure"))
    monkeypatch.setattr(exporter, "INTENTIONALLY_INACTIVE_DISPATCHES", ("cascadia",))
    result = export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at=second)

    assert result["food_line"]["last_exported_at"] == first
    assert result["system"]["exported_at"] == second
    assert result["system"]["dispatches"]["cascadia"]["migration_status"] == "INTENTIONALLY_INACTIVE"


def test_export_reuses_system_timestamp_when_payload_is_unchanged(tmp_path: Path) -> None:
    source = _write_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    first = export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T16:01:00Z")
    second = export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T17:01:00Z")

    assert second["food_line"]["last_exported_at"] == first["food_line"]["last_exported_at"]
    assert second["system"]["exported_at"] == first["system"]["exported_at"]


def test_forced_food_refresh_advances_unchanged_food_timestamp(tmp_path: Path) -> None:
    source = _write_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    first = export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T16:01:00Z")
    second = export_status(
        source_root=source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T17:01:00Z",
        force_refresh_dispatches={"food-line"},
    )

    assert second["food_line"]["last_exported_at"] == "2026-09-10T17:01:00Z"
    assert second["food_line"]["last_exported_at"] != first["food_line"]["last_exported_at"]
    assert json.loads((checkout / f"ops/status/food-line/history/{DATE}.json").read_text(encoding="utf-8"))["last_exported_at"] == "2026-09-10T17:01:00Z"


def test_forced_care_refresh_advances_care_without_forcing_food(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    care_source = _write_care_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    first = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T16:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )
    second = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T17:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
        force_refresh_dispatches={"care-line"},
    )

    assert second["care_line"]["last_exported_at"] == "2026-09-10T17:01:00Z"
    assert second["care_line"]["last_exported_at"] != first["care_line"]["last_exported_at"]
    assert second["food_line"]["last_exported_at"] == first["food_line"]["last_exported_at"]
    assert json.loads((checkout / f"ops/status/care-line/history/{DATE}.json").read_text(encoding="utf-8"))["last_exported_at"] == "2026-09-10T17:01:00Z"


def test_forced_ice_refresh_advances_ice_without_forcing_food(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    ice_source = _write_ice_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    first = export_status(
        source_root=source,
        ice_source_root=ice_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T05:01:00Z",
    )
    second = export_status(
        source_root=source,
        ice_source_root=ice_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T06:01:00Z",
        force_refresh_dispatches={"ice"},
    )

    assert second["ice"]["last_exported_at"] == "2026-09-11T06:01:00Z"
    assert second["ice"]["last_exported_at"] != first["ice"]["last_exported_at"]
    assert second["food_line"]["last_exported_at"] == first["food_line"]["last_exported_at"]
    assert json.loads((checkout / f"ops/status/ice/history/{DATE}.json").read_text(encoding="utf-8"))["last_exported_at"] == "2026-09-11T06:01:00Z"


def test_force_refresh_rejects_unsupported_dispatch(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="unsupported force_refresh_dispatches: gaza"):
        export_status(
            source_root=_write_day(tmp_path),
            status_checkout=tmp_path / "status-checkout",
            date=DATE,
            evaluated_at=EVALUATED,
            exported_at="2026-09-10T16:01:00Z",
            force_refresh_dispatches={"gaza"},
        )


def test_export_advances_food_and_system_timestamps_when_food_changes(tmp_path: Path) -> None:
    checkout = tmp_path / "status-checkout"
    first_source = _write_day(tmp_path / "first")
    success_statuses = {key: (action, "completed", 0) for key, (action, _, _) in TASKS.items()}
    second_source = _write_day(tmp_path / "second", success_statuses)

    export_status(source_root=first_source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T16:01:00Z")
    result = export_status(source_root=second_source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at="2026-09-10T17:01:00Z")

    assert result["food_line"]["last_exported_at"] == "2026-09-10T17:01:00Z"
    assert result["system"]["exported_at"] == "2026-09-10T17:01:00Z"
    assert result["food_line"]["aggregate_status"] == "SUCCESS"


def test_export_advances_system_timestamp_for_care_style_system_only_change(tmp_path: Path) -> None:
    source = _write_day(tmp_path)
    care_source = tmp_path / "care-source"
    checkout = tmp_path / "status-checkout"
    first = "2026-09-10T16:01:00Z"
    second = "2026-09-10T17:01:00Z"

    export_status(source_root=source, status_checkout=checkout, date=DATE, evaluated_at=EVALUATED, exported_at=first)
    result = export_status(
        source_root=source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at=second,
        care_source_root=care_source,
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )

    assert result["food_line"]["last_exported_at"] == first
    assert result["care_line"]["last_exported_at"] == second
    assert result["system"]["exported_at"] == second
    assert result["system"]["dispatches"]["care-line"]["scheduled_health_available"] is False
    assert result["system"]["dispatches"]["care-line"]["migration_status"] == "MIGRATED"


def test_care_export_with_care_source_root_creates_migrated_latest_and_history(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    care_source = _write_care_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    result = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at="2026-09-10T18:00:00Z",
        exported_at="2026-09-10T16:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )

    care = result["care_line"]
    system_care = result["system"]["dispatches"]["care-line"]
    assert (checkout / "ops/status/care-line/latest.json").is_file()
    assert (checkout / f"ops/status/care-line/history/{DATE}.json").is_file()
    assert care["migration_status"] == "MIGRATED"
    assert care["aggregate_status"] == "DEGRADED"
    assert care["receipt_completeness"] == "COMPLETE"
    assert care["latest_runtime_proof_date"] == "2026-09-10T15:02:30Z"
    assert system_care["migration_status"] == "MIGRATED"
    assert system_care["aggregate_status"] == "DEGRADED"
    assert system_care["latest_runtime_proof_date"] == care["latest_runtime_proof_date"]
    assert system_care["aggregate_status"] != "UNKNOWN"
    assert system_care["scheduled_health_available"] is True


def test_care_safe_no_op_queue_and_publication_do_not_become_failures(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    care_source = _write_care_day(tmp_path)

    result = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=tmp_path / "status-checkout",
        date=DATE,
        evaluated_at="2026-09-10T18:00:00Z",
        exported_at="2026-09-10T16:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )

    summaries = {row["task_key"]: row for row in result["care_line"]["task_summaries"]}
    assert summaries["care_line_reviewed_event_queue"]["status"] == "SAFE_NO_OP"
    assert summaries["care_line_reviewed_event_queue"]["classification"] == "nothing_to_publish"
    assert summaries["care_line_approved_release_publication"]["status"] == "SAFE_NO_OP"
    assert summaries["care_line_approved_release_publication"]["publication_attempted"] is False
    assert summaries["care_line_approved_release_publication"]["publication_status"] == "safe_no_op"
    assert result["care_line"]["aggregate_status"] == "DEGRADED"


def test_care_export_remains_sanitized_and_reuses_timestamp(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    care_source = _write_care_day(tmp_path)
    checkout = tmp_path / "status-checkout"

    first = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T16:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )
    second = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T17:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )

    assert second["care_line"]["last_exported_at"] == first["care_line"]["last_exported_at"]
    exported = json.dumps(second)
    assert "C:\\BlueFernRunner" not in exported
    assert "private care body" not in exported
    assert "source_text" not in exported


def test_older_same_day_care_collection_does_not_make_latest_chain_stale(tmp_path: Path) -> None:
    source = _write_day(tmp_path / "food")
    care_source = _write_care_day(tmp_path, include_older_collection=True)

    result = export_status(
        source_root=source,
        care_source_root=care_source,
        status_checkout=tmp_path / "status-checkout",
        date=DATE,
        evaluated_at="2026-09-10T16:01:00Z",
        exported_at="2026-09-10T16:01:30Z",
        care_expected_instances=CARE_EXPECTED_WITH_OLDER_COLLECTION,
    )

    assert result["care_line"]["aggregate_status"] == "DEGRADED"
    assert result["care_line"]["stale_observability"] is False
    assert result["system"]["dispatches"]["care-line"]["stale_observability"] is False


def test_care_local_evening_collection_finds_next_utc_date_receipt(tmp_path: Path) -> None:
    care_source = tmp_path / "care-source"
    _write_care_receipt(
        care_source,
        receipt_date="2026-09-19",
        task_key="care_line_collection",
        run_id="evening-collection",
        task_status="partial_success",
        scheduled_for="2026-09-19T01:00:01Z",
        started_at="2026-09-19T01:00:01Z",
        completed_at="2026-09-19T01:03:09Z",
    )

    status = build_care_line_status(
        source_root=care_source,
        date="2026-09-18",
        evaluated_at="2026-09-19T04:00:00Z",
        exported_at="2026-09-19T04:01:00Z",
        expected_instances=[
            {
                "task_key": "care_line_collection",
                "scheduled_for": "2026-09-19T01:00:00Z",
                "schedule_source": "windows_task_scheduler",
            }
        ],
    )

    assert status["aggregate_status"] == "DEGRADED"
    assert status["receipt_completeness"] == "PARTIAL"
    assert status["task_summaries"][0]["run_id"] == "evening-collection"
    assert status["task_summaries"][0]["scheduled_for"] == "2026-09-19T01:00:01Z"


def test_care_morning_collection_same_utc_date_still_matches(tmp_path: Path) -> None:
    care_source = tmp_path / "care-source"
    _write_care_receipt(
        care_source,
        receipt_date="2026-09-18",
        task_key="care_line_collection",
        run_id="morning-collection",
        task_status="partial_success",
        scheduled_for="2026-09-18T13:00:02Z",
        started_at="2026-09-18T13:00:02Z",
        completed_at="2026-09-18T13:02:00Z",
    )

    status = build_care_line_status(
        source_root=care_source,
        date="2026-09-18",
        evaluated_at="2026-09-18T16:00:00Z",
        exported_at="2026-09-18T16:01:00Z",
        expected_instances=[
            {
                "task_key": "care_line_collection",
                "scheduled_for": "2026-09-18T13:00:00Z",
                "schedule_source": "windows_task_scheduler",
            }
        ],
    )

    assert status["aggregate_status"] == "DEGRADED"
    assert status["task_summaries"][0]["run_id"] == "morning-collection"


def test_care_missing_due_instance_remains_missed(tmp_path: Path) -> None:
    status = build_care_line_status(
        source_root=tmp_path / "care-source",
        date="2026-09-18",
        evaluated_at="2026-09-19T04:00:00Z",
        exported_at="2026-09-19T04:01:00Z",
        expected_instances=[
            {
                "task_key": "care_line_collection",
                "scheduled_for": "2026-09-19T01:00:00Z",
                "schedule_source": "windows_task_scheduler",
            }
        ],
    )

    assert status["aggregate_status"] == "MISSED"
    assert status["receipt_completeness"] == "NO_PROOF"
    assert status["task_summaries"] == []


def test_care_adjacent_collection_receipt_does_not_satisfy_wrong_instance(tmp_path: Path) -> None:
    care_source = tmp_path / "care-source"
    _write_care_receipt(
        care_source,
        receipt_date="2026-09-18",
        task_key="care_line_collection",
        run_id="noon-collection",
        task_status="partial_success",
        scheduled_for="2026-09-18T19:00:00Z",
        started_at="2026-09-18T19:00:00Z",
        completed_at="2026-09-18T19:03:00Z",
    )

    status = build_care_line_status(
        source_root=care_source,
        date="2026-09-18",
        evaluated_at="2026-09-19T04:00:00Z",
        exported_at="2026-09-19T04:01:00Z",
        expected_instances=[
            {
                "task_key": "care_line_collection",
                "scheduled_for": "2026-09-19T01:00:00Z",
                "schedule_source": "windows_task_scheduler",
            }
        ],
    )

    assert status["aggregate_status"] == "MISSED"
    assert status["task_summaries"][0]["run_id"] == "noon-collection"


def test_care_dst_fall_back_evening_receipt_uses_absolute_instant(tmp_path: Path) -> None:
    care_source = tmp_path / "care-source"
    _write_care_receipt(
        care_source,
        receipt_date="2026-11-02",
        task_key="care_line_collection",
        run_id="dst-evening-collection",
        task_status="partial_success",
        scheduled_for="2026-11-02T02:00:01Z",
        started_at="2026-11-02T02:00:01Z",
        completed_at="2026-11-02T02:04:00Z",
    )

    status = build_care_line_status(
        source_root=care_source,
        date="2026-11-01",
        evaluated_at="2026-11-02T05:00:00Z",
        exported_at="2026-11-02T05:01:00Z",
        expected_instances=[
            {
                "task_key": "care_line_collection",
                "scheduled_for": "2026-11-02T02:00:00Z",
                "schedule_source": "windows_task_scheduler",
            }
        ],
    )

    assert status["aggregate_status"] == "DEGRADED"
    assert status["task_summaries"][0]["run_id"] == "dst-evening-collection"


def test_omitting_care_source_root_does_not_fabricate_authoritative_care_status(tmp_path: Path) -> None:
    checkout = tmp_path / "status-checkout"

    result = export_status(
        source_root=_write_day(tmp_path),
        status_checkout=checkout,
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at="2026-09-10T16:01:00Z",
    )

    assert "care_line" not in result
    assert not (checkout / "ops/status/care-line/latest.json").exists()
    assert result["system"]["dispatches"]["care-line"]["migration_status"] == "NOT_MIGRATED"
    assert result["system"]["dispatches"]["care-line"]["aggregate_status"] == "UNKNOWN"
    assert result["system"]["dispatches"]["care-line"]["scheduled_health_available"] is False


def test_omitting_ice_source_root_preserves_not_migrated_status(tmp_path: Path) -> None:
    result = export_status(
        source_root=_write_day(tmp_path),
        status_checkout=tmp_path / "status",
        date=DATE,
        evaluated_at=EVALUATED,
        exported_at=EVALUATED,
    )

    assert "ice" not in result
    assert result["system"]["dispatches"]["ice"]["migration_status"] == "NOT_MIGRATED"
    assert result["system"]["dispatches"]["ice"]["aggregate_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("status", "classification", "expected"),
    [
        ("SUCCESS", "healthy", "SUCCESS"),
        ("DEGRADED", "provider_timeout", "DEGRADED"),
        ("FAILED", "collection_failed", "FAILED"),
        ("SAFE_NO_OP", "healthy_zero_new_events", "SUCCESS"),
    ],
)
def test_ice_source_root_exports_migrated_status(tmp_path: Path, status: str, classification: str, expected: str) -> None:
    checkout = tmp_path / "status"
    result = export_status(
        source_root=_write_day(tmp_path / "food"),
        ice_source_root=_write_ice_day(tmp_path, status=status, classification=classification),
        status_checkout=checkout,
        date=DATE,
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T05:01:00Z",
    )

    assert (checkout / "ops/status/ice/latest.json").is_file()
    assert (checkout / f"ops/status/ice/history/{DATE}.json").is_file()
    assert result["ice"]["migration_status"] == "MIGRATED"
    assert result["ice"]["aggregate_status"] == expected
    assert result["ice"]["receipt_completeness"] == "COMPLETE"
    assert result["ice"]["task_summaries"][0]["artifact_id"] == "monitor_receipt.json"
    assert result["system"]["dispatches"]["ice"]["migration_status"] == "MIGRATED"
    assert result["system"]["dispatches"]["ice"]["aggregate_status"] == expected
    exported = json.dumps(result)
    assert "private source body" not in exported
    assert "source_text" not in exported
    assert "BlueFernRunner" not in exported


def test_ice_terminal_unaccounted_cannot_be_silently_healthy(tmp_path: Path) -> None:
    status = build_ice_status(
        source_root=_write_ice_day(tmp_path, unaccounted=1),
        date=DATE,
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T05:01:00Z",
    )

    assert status["aggregate_status"] == "FAILED"


def test_ice_legacy_monitor_receipt_linkage_is_complete_when_file_exists(tmp_path: Path) -> None:
    status = build_ice_status(
        source_root=_write_ice_day(tmp_path, include_task_receipt=False),
        date=DATE,
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T05:01:00Z",
    )

    assert status["receipt_completeness"] == "COMPLETE"
    assert status["task_summaries"][0]["artifact_id"] == "monitor_receipt.json"


def test_ice_status_before_due_does_not_reuse_yesterday_or_mark_stale(tmp_path: Path) -> None:
    source = _write_ice_day(tmp_path, date="2026-09-09")
    status = build_ice_status(
        source_root=source,
        date="2026-09-10",
        evaluated_at="2026-09-11T03:00:00Z",
        exported_at="2026-09-11T03:01:00Z",
    )

    assert status["aggregate_status"] == "UNKNOWN"
    assert status["receipt_completeness"] == "NO_PROOF"
    assert status["stale_observability"] is False
    assert status["task_summaries"] == []


def test_ice_status_after_local_schedule_run_is_healthy_and_complete(tmp_path: Path) -> None:
    status = build_ice_status(
        source_root=_write_ice_day(tmp_path, date="2026-09-10"),
        date="2026-09-10",
        evaluated_at="2026-09-11T05:00:00Z",
        exported_at="2026-09-11T05:01:00Z",
    )

    assert status["aggregate_status"] == "SUCCESS"
    assert status["receipt_completeness"] == "COMPLETE"
    assert status["stale_observability"] is False
    assert status["runner_source_head"] == "26ab691dfeac1a2d94682d9205f76a245cbf92ad"


def test_ice_status_missing_after_grace_becomes_missed(tmp_path: Path) -> None:
    status = build_ice_status(
        source_root=tmp_path / "empty-ice-source",
        date="2026-09-10",
        evaluated_at="2026-09-11T08:30:00Z",
        exported_at="2026-09-11T08:31:00Z",
    )

    assert status["aggregate_status"] == "MISSED"
    assert status["stale_observability"] is False


def test_care_source_root_without_expected_instances_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="Care scheduler expected_instances are required"):
        export_status(
            source_root=_write_day(tmp_path / "food"),
            care_source_root=tmp_path / "care-source",
            status_checkout=tmp_path / "status-checkout",
            date=DATE,
            evaluated_at=EVALUATED,
            exported_at="2026-09-10T16:01:00Z",
        )


def test_lock_prevents_overlap_and_status_scope_is_enforced(tmp_path: Path) -> None:
    with exporter_lock(tmp_path):
        with pytest.raises(ExportError):
            with exporter_lock(tmp_path):
                pass
    validate_status_paths(["ops/status/food-line/latest.json"])
    with pytest.raises(ExportError):
        validate_status_paths(["output/site/index.html"])


def _init_status_git_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "status-test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Status Test"], cwd=path, check=True)
    (path / ".gitignore").write_text("status/\nops/status/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def _commit_file(repo: Path, relative: str, content: str, message: str) -> str:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--force", relative], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _init_status_checkout_with_remote(tmp_path: Path, branch: str = "ops/status/food-line-2026-09-10") -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", remote], check=True, capture_output=True, text=True)
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    subprocess.run(["git", "checkout", "-b", branch], cwd=checkout, check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=checkout, check=True, capture_output=True, text=True)
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=checkout, check=True, capture_output=True, text=True)
    return checkout, remote


def _clone_writer(tmp_path: Path, remote: Path) -> Path:
    writer = tmp_path / "writer"
    subprocess.run(["git", "clone", str(remote), str(writer)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "status-test@example.invalid"], cwd=writer, check=True)
    subprocess.run(["git", "config", "user.name", "Status Test"], cwd=writer, check=True)
    return writer


def test_prepare_status_checkout_uses_fetch_head_when_remote_tracking_ref_is_stale(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, remote = _init_status_checkout_with_remote(tmp_path, branch)
    writer = _clone_writer(tmp_path, remote)
    subprocess.run(["git", "checkout", branch], cwd=writer, check=True, capture_output=True, text=True)
    remote_tip = _commit_file(writer, "ops/status/system/latest.json", "{}\n", "advance status")
    subprocess.run(["git", "push", "origin", branch], cwd=writer, check=True, capture_output=True, text=True)

    prepare_status_checkout(checkout, branch=branch)

    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip() == remote_tip
    assert subprocess.run(["git", "rev-parse", "FETCH_HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip() == remote_tip


def test_prepare_status_checkout_does_not_consult_stale_remote_tracking_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import bluefern_dispatches.operational_status_exporter as exporter

    branch = "ops/status/food-line-2026-09-10"
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:] == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{branch}\n", stderr="")
        if args[1:] == ["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(exporter.subprocess, "run", fake_run)

    prepare_status_checkout(tmp_path, branch=branch)

    assert ["git", "fetch", "--no-tags", "origin", f"refs/heads/{branch}"] in calls
    assert ["git", "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"] in calls
    assert ["git", "merge", "--ff-only", "FETCH_HEAD"] in calls
    assert ["git", "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"] not in calls


def test_prepare_status_checkout_succeeds_when_head_already_equals_fetch_head(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()

    prepare_status_checkout(checkout, branch=branch)

    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip() == head_before


def test_prepare_status_checkout_fails_closed_when_fetch_head_diverged(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, remote = _init_status_checkout_with_remote(tmp_path, branch)
    writer = _clone_writer(tmp_path, remote)
    subprocess.run(["git", "checkout", branch], cwd=writer, check=True, capture_output=True, text=True)
    _commit_file(writer, "ops/status/system/latest.json", "remote\n", "remote advance")
    subprocess.run(["git", "push", "origin", branch], cwd=writer, check=True, capture_output=True, text=True)
    _commit_file(checkout, "ops/status/food-line/latest.json", "local\n", "local advance")

    with pytest.raises(ExportError, match="cannot fast-forward"):
        prepare_status_checkout(checkout, branch=branch)


def test_prepare_status_checkout_rejects_dirty_checkout_before_fetch(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    _commit_file(checkout, "ops/status/food-line/latest.json", "{}\n", "track status")
    (checkout / "ops" / "status" / "food-line" / "latest.json").write_text("{\"dirty\":true}\n", encoding="utf-8")

    with pytest.raises(ExportError, match="clean before fast-forward"):
        prepare_status_checkout(checkout, branch=branch)


def test_status_checkout_state_classifies_clean_checkout(tmp_path: Path) -> None:
    checkout, _remote = _init_status_checkout_with_remote(tmp_path)

    state = classify_status_checkout_state(checkout)

    assert state.state == "CLEAN"
    assert state.tracked_status_paths == []
    assert state.untracked_status_paths == []
    assert state.unexpected_paths == []


def test_prepare_status_checkout_allows_sanctioned_tracked_status_changes_when_opted_in(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    _commit_file(checkout, "ops/status/food-line/latest.json", "{}\n", "track status")
    subprocess.run(["git", "push", "origin", branch], cwd=checkout, check=True, capture_output=True, text=True)
    (checkout / "ops/status/food-line/latest.json").write_text("{\"local\":true}\n", encoding="utf-8")

    prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)

    state = classify_status_checkout_state(checkout)
    assert state.state == "SANCTIONED_STATUS_ONLY"
    assert state.tracked_status_paths == ["ops/status/food-line/latest.json"]


def test_prepare_status_checkout_allows_sanctioned_untracked_status_changes_when_opted_in(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    path = checkout / "ops/status/care-line/latest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")

    prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)

    state = classify_status_checkout_state(checkout)
    assert state.state == "SANCTIONED_STATUS_ONLY"
    assert state.untracked_status_paths == ["ops/status/care-line/latest.json"]


def test_prepare_status_checkout_refuses_unrelated_tracked_dirty_path_even_when_opted_in(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    _commit_file(checkout, "README.md", "clean\n", "track readme")
    (checkout / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)


def test_prepare_status_checkout_refuses_unrelated_untracked_path_even_when_opted_in(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    (checkout / "notes.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)


def test_prepare_status_checkout_refuses_remote_overlap_with_local_status_changes(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, remote = _init_status_checkout_with_remote(tmp_path, branch)
    _commit_file(checkout, "ops/status/food-line/latest.json", "{}\n", "track local status")
    subprocess.run(["git", "push", "origin", branch], cwd=checkout, check=True, capture_output=True, text=True)
    writer = _clone_writer(tmp_path, remote)
    subprocess.run(["git", "checkout", branch], cwd=writer, check=True, capture_output=True, text=True)
    _commit_file(writer, "ops/status/food-line/latest.json", "{\"remote\":true}\n", "remote status")
    subprocess.run(["git", "push", "origin", branch], cwd=writer, check=True, capture_output=True, text=True)
    (checkout / "ops/status/food-line/latest.json").write_text("{\"local\":true}\n", encoding="utf-8")

    with pytest.raises(ExportError, match="overlap local ops/status changes"):
        prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)


def test_prepare_status_checkout_allows_non_overlapping_fast_forward_with_local_status_changes(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, remote = _init_status_checkout_with_remote(tmp_path, branch)
    _commit_file(checkout, "ops/status/food-line/latest.json", "{}\n", "track local status")
    subprocess.run(["git", "push", "origin", branch], cwd=checkout, check=True, capture_output=True, text=True)
    writer = _clone_writer(tmp_path, remote)
    subprocess.run(["git", "checkout", branch], cwd=writer, check=True, capture_output=True, text=True)
    remote_tip = _commit_file(writer, "ops/status/system/latest.json", "{}\n", "remote system")
    subprocess.run(["git", "push", "origin", branch], cwd=writer, check=True, capture_output=True, text=True)
    (checkout / "ops/status/food-line/latest.json").write_text("{\"local\":true}\n", encoding="utf-8")

    prepare_status_checkout(checkout, branch=branch, allow_local_status_changes=True)

    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip() == remote_tip
    assert (checkout / "ops/status/food-line/latest.json").read_text(encoding="utf-8") == "{\"local\":true}\n"


def test_prepare_status_checkout_rejects_wrong_local_branch(tmp_path: Path) -> None:
    branch = "ops/status/food-line-2026-09-10"
    checkout, _remote = _init_status_checkout_with_remote(tmp_path, branch)
    subprocess.run(["git", "checkout", "-b", "other-status-branch"], cwd=checkout, check=True, capture_output=True, text=True)

    with pytest.raises(ExportError, match="branch mismatch"):
        prepare_status_checkout(checkout, branch=branch)


def test_prepare_status_checkout_does_not_use_force_reset_or_rebase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import bluefern_dispatches.operational_status_exporter as exporter

    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:] == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(args, 0, stdout="ops/status/food-line-2026-09-10\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(exporter.subprocess, "run", fake_run)

    prepare_status_checkout(tmp_path, branch="ops/status/food-line-2026-09-10")

    flattened = [part for call in calls for part in call]
    assert "--force" not in flattened
    assert "reset" not in flattened
    assert "restore" not in flattened
    assert "clean" not in flattened
    assert "stash" not in flattened
    assert "rebase" not in flattened
    assert ["git", "merge", "--ff-only", "FETCH_HEAD"] in calls


def test_commit_and_push_force_stages_ignored_ops_status_artifact(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    target = checkout / "ops" / "status" / "food-line" / "latest.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")

    commit = commit_and_push_status(
        checkout,
        paths=["ops/status/food-line/latest.json"],
        message="export status",
        remote=".",
        branch="HEAD",
    )

    assert commit
    staged = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout.strip() == "ops/status/food-line/latest.json"


def test_commit_and_push_preserves_allowed_status_carryover_with_real_git(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    baseline = {
        "ops/status/food-line/latest.json": "{\"baseline\":\"food\"}\n",
        "ops/status/food-line/history/2026-09-10.json": "{\"baseline\":\"food-history\"}\n",
        "ops/status/care-line/latest.json": "{\"baseline\":\"care\"}\n",
        "ops/status/care-line/history/2026-09-10.json": "{\"baseline\":\"care-history\"}\n",
        "ops/status/ice/latest.json": "{\"baseline\":\"ice\"}\n",
        "ops/status/ice/history/2026-09-10.json": "{\"baseline\":\"ice-history\"}\n",
        "ops/status/system/latest.json": "{\"baseline\":\"system\"}\n",
    }
    for path, text in baseline.items():
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "--force", "ops/status"], cwd=checkout, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline status"], cwd=checkout, check=True, capture_output=True, text=True)
    care_ice = [
        "ops/status/care-line/latest.json",
        "ops/status/care-line/history/2026-09-10.json",
        "ops/status/ice/latest.json",
        "ops/status/ice/history/2026-09-10.json",
    ]
    for path in care_ice:
        (checkout / path).write_text(f"{{\"operator\":\"{path}\"}}\n", encoding="utf-8")
    carryover_hashes = _status_file_hashes(checkout, care_ice)
    current = [
        "ops/status/food-line/latest.json",
        "ops/status/food-line/history/2026-09-10.json",
        "ops/status/system/latest.json",
    ]
    for path in current:
        (checkout / path).write_text(f"{{\"scheduled\":\"{path}\"}}\n", encoding="utf-8")

    details = commit_and_push_status(
        checkout,
        paths=current,
        message="scheduled food export",
        remote=".",
        branch="HEAD",
        allowed_unstaged_status_paths=care_ice,
        return_details=True,
    )

    assert isinstance(details, dict)
    assert details["commit"]
    assert details["staged_paths"] == sorted(current)
    assert details["committed_paths"] == sorted(current)
    assert details["preserved_carryover_paths"] == sorted(care_ice)
    assert details["carryover_hashes_before"] == carryover_hashes
    assert details["carryover_hashes_after"] == carryover_hashes
    committed = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True)
    assert sorted(line.strip() for line in committed.stdout.splitlines() if line.strip()) == sorted(current)
    state = classify_status_checkout_state(checkout)
    assert state.unexpected_paths == []
    assert state.tracked_status_paths == sorted(care_ice)
    assert _status_file_hashes(checkout, care_ice) == carryover_hashes


def test_commit_and_push_stages_overlapping_status_carryover_as_current_export(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    for path in ("ops/status/food-line/latest.json", "ops/status/care-line/latest.json"):
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{\"baseline\":true}\n", encoding="utf-8")
    subprocess.run(["git", "add", "--force", "ops/status"], cwd=checkout, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline status"], cwd=checkout, check=True, capture_output=True, text=True)
    (checkout / "ops/status/food-line/latest.json").write_text("{\"scheduled\":\"food\"}\n", encoding="utf-8")
    (checkout / "ops/status/care-line/latest.json").write_text("{\"operator\":\"care\"}\n", encoding="utf-8")

    details = commit_and_push_status(
        checkout,
        paths=["ops/status/food-line/latest.json"],
        message="scheduled food export",
        remote=".",
        branch="HEAD",
        allowed_unstaged_status_paths=["ops/status/food-line/latest.json", "ops/status/care-line/latest.json"],
        return_details=True,
    )

    assert isinstance(details, dict)
    assert details["committed_paths"] == ["ops/status/food-line/latest.json"]
    assert details["preserved_carryover_paths"] == ["ops/status/care-line/latest.json"]
    state = classify_status_checkout_state(checkout)
    assert state.tracked_status_paths == ["ops/status/care-line/latest.json"]


def test_commit_and_push_rejects_unrelated_tracked_dirty_with_carryover(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    _commit_file(checkout, "README.md", "clean\n", "track readme")
    target = checkout / "ops/status/food-line/latest.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    (checkout / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        commit_and_push_status(
            checkout,
            paths=["ops/status/food-line/latest.json"],
            message="scheduled food export",
            remote=".",
            branch="HEAD",
            allowed_unstaged_status_paths=[],
        )


def test_commit_and_push_rejects_unrelated_untracked_dirty_with_carryover(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    target = checkout / "ops/status/food-line/latest.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    (checkout / "notes.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        commit_and_push_status(
            checkout,
            paths=["ops/status/food-line/latest.json"],
            message="scheduled food export",
            remote=".",
            branch="HEAD",
            allowed_unstaged_status_paths=[],
        )


def test_commit_and_push_does_not_use_cleanup_or_force_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import bluefern_dispatches.operational_status_exporter as exporter

    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        command = args[1:]
        if command[:2] == ["status", "--porcelain=v1"] and "--ignored=matching" not in command:
            return subprocess.CompletedProcess(args, 0, stdout=" M ops/status/food-line/latest.json\n", stderr="")
        if command[:2] == ["status", "--porcelain=v1"] and "--ignored=matching" in command:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == ["diff", "--cached", "--name-only"]:
            return subprocess.CompletedProcess(args, 0, stdout="ops/status/food-line/latest.json\n", stderr="")
        if command == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(args, 0, stdout="ops/status/food-line-2026-09-10\n", stderr="")
        if command == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc123\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(exporter.subprocess, "run", fake_run)

    commit_and_push_status(
        tmp_path,
        paths=["ops/status/food-line/latest.json"],
        message="scheduled food export",
        remote="origin",
        branch="ops/status/food-line-2026-09-10",
        return_details=True,
    )

    flattened = [part for call in calls for part in call]
    assert "reset" not in flattened
    assert "restore" not in flattened
    assert "clean" not in flattened
    assert "stash" not in flattened
    assert "rebase" not in flattened
    assert ["git", "push", "origin", "ops/status/food-line-2026-09-10"] in calls
    assert ["git", "push", "--force", "origin", "ops/status/food-line-2026-09-10"] not in calls
    assert ["git", "push", "-f", "origin", "ops/status/food-line-2026-09-10"] not in calls


def test_commit_and_push_still_rejects_paths_outside_ops_status(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    outside = checkout / "status" / "food-line" / "latest.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        commit_and_push_status(
            checkout,
            paths=["status/food-line/latest.json"],
            message="bad status",
            remote=".",
            branch="HEAD",
        )


def test_commit_and_push_rejects_unapproved_dirty_production_or_pages_paths(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    (checkout / "ops" / "status" / "food-line").mkdir(parents=True)
    (checkout / "ops" / "status" / "food-line" / "latest.json").write_text("{}\n", encoding="utf-8")
    (checkout / "bluefern-dispatches-pages").mkdir()
    (checkout / "bluefern-dispatches-pages" / "index.html").write_text("public\n", encoding="utf-8")

    with pytest.raises(ExportError, match="outside ops/status"):
        commit_and_push_status(
            checkout,
            paths=["ops/status/food-line/latest.json"],
            message="export status",
            remote=".",
            branch="HEAD",
        )


def test_local_runtime_status_remains_ignored_without_force_add(tmp_path: Path) -> None:
    checkout = tmp_path / "status"
    _init_status_git_repo(checkout)
    runtime = checkout / "status" / "food-line" / "runtime" / "state.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("{}\n", encoding="utf-8")

    listed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status/food-line/runtime/state.json" not in listed.stdout


def test_system_marks_non_migrated_dispatches_without_calling_them_failed(tmp_path: Path) -> None:
    result = export_status(source_root=_write_day(tmp_path), status_checkout=tmp_path / "status", date=DATE, evaluated_at=EVALUATED, exported_at=EVALUATED)
    dispatches = result["system"]["dispatches"]
    assert dispatches["food-line"]["migration_status"] == "MIGRATED"
    assert dispatches["gaza"]["migration_status"] == "NOT_MIGRATED"
    assert dispatches["gaza"]["aggregate_status"] == "UNKNOWN"
    assert dispatches["cascadia"]["migration_status"] == "INTENTIONALLY_INACTIVE"
    assert dispatches["cascadia"]["aggregate_status"] == "INTENTIONALLY_INACTIVE"
    assert dispatches["cascadia"]["expected_active_schedule"] is False


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
