from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import build_care_line_operational_receipt, build_operational_receipt
from scripts.dispatch_ops import (
    DispatchStatus,
    apply_recovery_plan,
    build_recovery_plan,
    build_status,
    build_system_snapshot,
    evaluate_system_alerts,
    main,
)


DATE = "2026-09-18"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(root: Path, rel: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def _system_status_export(root: Path) -> None:
    _write_json(
        root / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-28T17:35:22Z",
            "system_status": "STALE_OBSERVABILITY",
            "dispatches": {
                "food-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "SUCCESS",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {
                        "aggregate_status": "SUCCESS",
                        "operator_assessment": "HEALTHY",
                        "primary_layer": "NONE",
                        "primary_task_key": None,
                        "receipt_completeness": "COMPLETE",
                    },
                },
                "care-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "DEGRADED",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {
                        "aggregate_status": "DEGRADED",
                        "operator_assessment": "HEALTHY_WITH_EXTERNAL_RESTRICTIONS",
                        "primary_layer": "EXTERNAL_DEPENDENCY",
                        "primary_task_key": "care_line_collection",
                        "failed_source_count": 9,
                        "external_access_restriction_count": 9,
                    },
                },
                "gaza": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "STALE_OBSERVABILITY",
                    "recovery_lifecycle": "INCIDENT_OPEN",
                    "debug_summary": {
                        "aggregate_status": "STALE_OBSERVABILITY",
                        "operator_assessment": "ACTION_REQUIRED_OBSERVABILITY",
                        "primary_layer": "OBSERVABILITY",
                        "primary_task_key": "gaza_daily_dispatch",
                    },
                },
            },
        },
    )


def _gaza_no_update(
    root: Path,
    date: str = "2026-09-19",
    *,
    pages: bool = False,
    classification: str = "no_update",
    include_daily_run_completed: bool = True,
    manifest: dict | None = None,
) -> None:
    _write_json(root / "data/dispatches/gaza/editions" / date / "run_manifest.json", manifest or {"ok": True})
    _write_json(root / "data/dispatches/gaza/editions" / date / "collection_report.json", {"public_story_count": 0})
    payload = {
        "schema_version": "gaza-no-update-status-v1",
        "date": date,
        "classification": classification,
        "public_story_count": 0,
        "run_manifest_path": f"data/dispatches/gaza/editions/{date}/run_manifest.json",
        "collection_report_path": f"data/dispatches/gaza/editions/{date}/collection_report.json",
        "normal_edition_generated": False,
    }
    if include_daily_run_completed:
        payload["daily_run_completed"] = True
    _write_json(root / "output/site/gaza/status/no-updates" / f"{date}.json", payload)
    if pages:
        _write_json(root / "bluefern-dispatches-pages/gaza/status/no-updates" / f"{date}.json", payload)
    (root / "output/site/gaza/editions/2026-09-18").mkdir(parents=True)
    (root / "output/site/gaza/editions/2026-09-18/index.html").write_text("latest real edition", encoding="utf-8")


def _food_reconstructed(root: Path, *, unresolved: int = 0) -> None:
    _write_json(
        root / "data/dispatches/food-line/coverage-gaps/2026-09-09.json",
        {
            "schema_version": "bluefern.coverage_gap.v1",
            "dispatch": "food-line",
            "observation_date": "2026-09-09",
            "observation_status": "OBSERVED_WITH_FINDINGS",
            "backfill_status": "RECOVERED",
            "gap_reason": "historical_recovery_completed",
            "notes": "Food Line date reconciliation state COMPLETE_RECONSTRUCTED.",
            "recovered_at": "2026-09-18T23:51:56Z",
        },
    )
    _write_json(
        root / "data/dispatches/food-line/historical-intake/2026-09-09/reconciliation-receipt.json",
        {
            "schema_version": "food_line_historical_reconciliation_v1",
            "historical_date": "2026-09-09",
            "final_classification": "FOOD LINE SEP 9 - HISTORICAL REPLAY BLOCKED / INSUFFICIENT EVIDENCE",
            "operational_health_handling": {
                "historical_reconciliation_status": "BLOCKED",
                "original_runtime_classification": "FAILED_UPSTREAM_BLOCKED",
            },
            "replay_result": {
                "approved": 0,
                "considered": 0,
                "duplicate": 0,
                "imported": 0,
                "pending": 0,
                "rejected": 0,
                "selected": 0,
                "unresolved": unresolved,
            },
        },
    )


def _care_partial_success(root: Path, date: str = DATE) -> None:
    rows = [
        ("care_line_collection", "collection", "partial_success", "2026-09-19T01:00:01Z"),
        ("care_line_reviewed_event_queue", "queue", "ready_for_operator_release", "2026-09-19T01:01:01Z"),
        ("care_line_approved_release_publication", "publication", "safe_no_op", "2026-09-19T01:02:01Z"),
    ]
    receipt_root = root / "status/operational-health/care-line/2026-09-19/runs"
    for task_key, run_id, task_status, started_at in rows:
        artifact = _artifact(root, f"status/care-line/scheduler-runs/2026-09-19/{run_id}.json")
        receipt = build_care_line_operational_receipt(
            task_key=task_key,
            scheduled_for=started_at.replace(":01Z", ":00Z"),
            started_at=started_at,
            completed_at=started_at.replace(":01Z", ":30Z"),
            exit_code=0,
            task_status=task_status,
            run_id=run_id,
            artifact_refs={"task_receipt": str(artifact)},
            publication_attempted=False if task_key == "care_line_approved_release_publication" else None,
            publication_status="safe_no_op" if task_key == "care_line_approved_release_publication" else None,
        )
        _write_json(receipt_root / f"{run_id}.json", receipt)


def _care_success_receipts_for_each_task_key(root: Path, date: str = DATE) -> None:
    rows = [
        ("care_line_collection", "collection", "success", "2026-09-19T01:00:01Z"),
        ("care_line_reviewed_event_queue", "queue", "ready_for_operator_release", "2026-09-19T01:01:01Z"),
        ("care_line_approved_release_publication", "publication", "publication_success", "2026-09-19T01:02:01Z"),
    ]
    receipt_root = root / "status/operational-health/care-line/2026-09-19/runs"
    for task_key, run_id, task_status, started_at in rows:
        artifact = _artifact(root, f"status/care-line/scheduler-runs/2026-09-19/{run_id}.json")
        receipt = build_care_line_operational_receipt(
            task_key=task_key,
            scheduled_for=started_at.replace(":01Z", ":00Z"),
            started_at=started_at,
            completed_at=started_at.replace(":01Z", ":30Z"),
            exit_code=0,
            task_status=task_status,
            run_id=run_id,
            artifact_refs={"task_receipt": str(artifact)},
            publication_attempted=True if task_key == "care_line_approved_release_publication" else None,
            publication_status="publication_success" if task_key == "care_line_approved_release_publication" else None,
        )
        _write_json(receipt_root / f"{run_id}.json", receipt)


def _food_status_export(
    root: Path,
    date: str,
    aggregate: str,
    *,
    task_status: str,
    classification: str = "",
    debug_summary: dict | None = None,
) -> None:
    _write_json(
        root / "ops/status/food-line/history" / f"{date}.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": "food-line",
            "observed_date": date,
            "aggregate_status": aggregate,
            "receipt_completeness": "COMPLETE",
            "recovery_lifecycle": "HEALTHY",
            "publication_status": "skipped_not_release_ready",
            "publication_attempted": False,
            "task_summaries": [
                {
                    "task_key": "food_line_source_watch",
                    "status": task_status,
                    "classification": classification or task_status.lower(),
                }
            ],
            **({"debug_summary": debug_summary} if debug_summary is not None else {}),
        },
    )


def _food_terminal_degraded_export(root: Path, date: str = "2026-09-23") -> None:
    blocked = {
        "task_key": "food_line_current_intake",
        "status": "UPSTREAM_BLOCKED",
        "classification": "source_watch_not_initialized",
    }
    effective = [
        {
            "task_key": "food_line_source_watch",
            "status": "DEGRADED",
            "classification": "completed_with_exclusions",
        },
        {
            "task_key": "food_line_current_intake",
            "status": "SUCCESS",
            "classification": "success",
        },
        {
            "task_key": "food_line_daily_publish",
            "status": "SAFE_NO_OP",
            "classification": "skipped_not_release_ready",
        },
    ]
    _write_json(
        root / "ops/status/food-line/history" / f"{date}.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": "food-line",
            "observed_date": date,
            "aggregate_status": "DEGRADED",
            "receipt_completeness": "COMPLETE",
            "recovery_lifecycle": "HEALTHY",
            "publication_status": "skipped_not_release_ready",
            "publication_attempted": False,
            "task_summaries": [blocked, *effective],
            "effective_task_summaries": effective,
        },
    )


def _food_missing_resume_not_durable_export(root: Path, date: str = "2026-09-23") -> None:
    effective = [
        {
            "task_key": "food_line_source_watch",
            "status": "DEGRADED",
            "classification": "completed_with_exclusions",
        },
        {
            "task_key": "food_line_current_intake",
            "status": "SUCCESS",
            "classification": "success",
        },
        {
            "task_key": "food_line_daily_publish",
            "status": "SAFE_NO_OP",
            "classification": "skipped_not_release_ready",
        },
    ]
    _write_json(
        root / "ops/status/food-line/history" / f"{date}.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": "food-line",
            "observed_date": date,
            "aggregate_status": "MISSED",
            "receipt_completeness": "PARTIAL",
            "recovery_lifecycle": "HEALTHY",
            "publication_status": "skipped_not_release_ready",
            "publication_attempted": False,
            "task_summaries": effective,
            "effective_task_summaries": effective,
        },
    )


def _food_safe_no_op_receipts(root: Path, date: str = "2026-09-20") -> None:
    rows = [
        ("food_line_source_watch", "source-watch"),
        ("food_line_source_watch_resume", "source-watch-resume"),
        ("food_line_current_intake", "current-intake"),
        ("food_line_daily_publish", "daily-publish"),
    ]
    receipt_root = root / "status/operational-health/food-line" / date / "runs"
    for index, (task_key, run_id) in enumerate(rows):
        artifact = _artifact(root, f"status/food-line/scheduler-runs/{date}/{run_id}.json")
        receipt = build_operational_receipt(
            dispatch="food-line",
            task_key=task_key,
            task_name=task_key,
            scheduled_for=f"{date}T12:{index:02d}:00Z",
            started_at=f"{date}T12:{index:02d}:01Z",
            completed_at=f"{date}T12:{index:02d}:30Z",
            observed_at=f"{date}T12:{index:02d}:30Z",
            exit_code=0,
            status="SAFE_NO_OP",
            classification="no_qualifying_edition",
            run_id=run_id,
            artifact_refs={"task_receipt": str(artifact)},
            publication_attempted=False if task_key == "food_line_daily_publish" else None,
            publication_status="skipped_not_release_ready" if task_key == "food_line_daily_publish" else None,
        )
        _write_json(receipt_root / f"{run_id}.json", receipt)


def _care_status_export(root: Path, date: str, aggregate: str) -> None:
    _write_json(
        root / "ops/status/care-line/history" / f"{date}.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": "care-line",
            "observed_date": date,
            "aggregate_status": aggregate,
            "receipt_completeness": "NO_PROOF" if aggregate == "MISSED" else "COMPLETE",
            "recovery_lifecycle": "HEALTHY",
            "publication_status": None,
            "publication_attempted": False,
            "task_summaries": [],
        },
    )


def _pages_edition(root: Path, dispatch: str, date: str) -> None:
    path = root / "bluefern-dispatches-pages" / dispatch / "editions" / date / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("published\n", encoding="utf-8")


def _local_output_edition(root: Path, dispatch: str, date: str) -> None:
    path = root / "output" / "site" / dispatch / "editions" / date / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("local generated\n", encoding="utf-8")


def _snapshot_files(root: Path) -> tuple[list[Path], dict[Path, tuple[str, int]]]:
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    return paths, {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in paths
    }


def _ice_receipt(root: Path, date: str = "2026-09-10", status: str = "SUCCESS", classification: str = "healthy") -> None:
    artifact = _artifact(root, f"data/dispatches/ice/monitor/runs/{date}/ice-monitor-1/monitor_receipt.json")
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
        artifact_refs={"monitor_receipt": str(artifact)},
        publication_attempted=False,
        publication_status="not_authorized_monitor_only",
    )
    _write_json(root / "status/operational-health/ice" / date / "runs/ice-monitor-1.json", receipt)


def test_gaza_sep_19_no_update_is_terminal_no_action(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "NO_UPDATE"
    assert status.collection == "COMPLETE"
    assert status.editorial == "NO_QUALIFYING_MATERIAL"
    assert status.public_state == "VERIFIED"
    assert status.next_action == "NONE"
    assert status.details["latest_real_edition_date"] == "2026-09-18"


def test_system_snapshot_reads_dispatch_debug_summaries(tmp_path: Path) -> None:
    _system_status_export(tmp_path)

    snapshot = build_system_snapshot(root=tmp_path)

    assert snapshot["system_status"] == "STALE_OBSERVABILITY"
    assert snapshot["dispatches"]["care-line"]["debug_summary"]["primary_layer"] == "EXTERNAL_DEPENDENCY"
    assert snapshot["dispatches"]["care-line"]["debug_summary"]["failed_source_count"] == 9
    assert snapshot["evidence"] == ["ops/status/system/latest.json"]


def test_system_command_renders_dispatch_debug_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _system_status_export(tmp_path)

    result = main(["system", "--root", str(tmp_path)])
    output = capsys.readouterr().out

    assert result == 1
    assert "System status: STALE_OBSERVABILITY" in output
    assert (
        "Care Line: DEGRADED / HEALTHY; assessment=HEALTHY_WITH_EXTERNAL_RESTRICTIONS; "
        "layer=EXTERNAL_DEPENDENCY; task=care_line_collection"
    ) in output
    assert "failed_sources=9" in output
    assert (
        "Gaza: STALE_OBSERVABILITY / INCIDENT_OPEN; assessment=ACTION_REQUIRED_OBSERVABILITY; "
        "layer=OBSERVABILITY; task=gaza_daily_dispatch"
    ) in output


def test_system_command_json_is_deterministic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _system_status_export(tmp_path)

    result = main(["system", "--json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["schema_version"] == "dispatch_ops_system_status_v1"
    assert payload["dispatches"]["food-line"]["debug_summary"]["primary_layer"] == "NONE"


def test_system_alerts_gate_on_operator_assessment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _system_status_export(tmp_path)

    result = main(["system", "--alerts-json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["schema_version"] == "dispatch_ops_system_alerts_v1"
    assert payload["alert_required"] is True
    assert [row["dispatch"] for row in payload["alert_dispatches"]] == ["gaza"]
    assert payload["alert_dispatches"][0]["operator_assessment"] == "ACTION_REQUIRED_OBSERVABILITY"
    assert [row["dispatch"] for row in payload["suppressed_dispatches"]] == ["care-line", "food-line"]
    assert {
        row["operator_assessment"] for row in payload["suppressed_dispatches"]
    } == {"HEALTHY", "HEALTHY_WITH_EXTERNAL_RESTRICTIONS"}


def test_system_alerts_do_not_notify_for_external_only_degradation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_json(
        tmp_path / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-28T22:19:13Z",
            "system_status": "DEGRADED",
            "dispatches": {
                "care-line": {
                    "aggregate_status": "DEGRADED",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {
                        "operator_assessment": "HEALTHY_WITH_EXTERNAL_RESTRICTIONS",
                        "primary_layer": "EXTERNAL_DEPENDENCY",
                        "primary_task_key": "care_line_collection",
                        "failed_source_count": 4,
                        "external_access_restriction_count": 4,
                        "unclassified_source_failure_count": 0,
                    },
                },
            },
        },
    )

    result = main(["system", "--alerts-json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["alert_required"] is False
    assert payload["alert_dispatches"] == []
    assert payload["suppressed_dispatches"][0]["dispatch"] == "care-line"


def test_system_alerts_notify_for_food_private_review_backlog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_json(
        tmp_path / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-29T18:00:00Z",
            "system_status": "SUCCESS",
            "dispatches": {
                "food-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "SUCCESS",
                    "recovery_lifecycle": "HEALTHY",
                    "private_review_backlog": {
                        "pending_date_count": 2,
                        "pending_item_count": 4,
                        "dispositioned_item_count": 25,
                        "unresolved_item_count": 4,
                        "count_only_gap_count": 1,
                        "oldest_pending_date": "2026-09-14",
                        "max_age_hours": 368,
                        "dates": [],
                    },
                    "debug_summary": {
                        "operator_assessment": "ACTION_REQUIRED_PENDING_REVIEW",
                        "primary_layer": "EDITORIAL_HANDOFF",
                        "primary_task_key": None,
                    },
                },
                "care-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "DEGRADED",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {
                        "operator_assessment": "HEALTHY_WITH_EXTERNAL_RESTRICTIONS",
                        "primary_layer": "EXTERNAL_DEPENDENCY",
                    },
                },
            },
        },
    )

    result = main(["system", "--alerts-json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["alert_required"] is True
    assert payload["alert_dispatches"] == [
        {
            "dispatch": "food-line",
            "migration_status": "MIGRATED",
            "aggregate_status": "SUCCESS",
            "recovery_lifecycle": "HEALTHY",
            "operator_assessment": "ACTION_REQUIRED_PENDING_REVIEW",
            "primary_layer": "EDITORIAL_HANDOFF",
            "primary_task_key": None,
            "failed_source_count": None,
            "external_access_restriction_count": None,
            "unclassified_source_failure_count": None,
            "private_review_pending_date_count": 2,
            "private_review_pending_item_count": 4,
            "private_review_dispositioned_item_count": 25,
            "private_review_unresolved_item_count": 4,
            "private_review_count_only_gap_count": 1,
            "private_review_oldest_pending_date": "2026-09-14",
        }
    ]
    assert [row["dispatch"] for row in payload["suppressed_dispatches"]] == ["care-line"]
    assert "ACTION_REQUIRED_PENDING_REVIEW" in payload["policy"]["alertable_operator_assessments"]


def test_system_alerts_ignore_unsupported_unknown_dispatches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_json(
        tmp_path / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-29T00:58:42Z",
            "system_status": "DEGRADED",
            "dispatches": {
                "food-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "SUCCESS",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {"operator_assessment": "HEALTHY", "primary_layer": "NONE"},
                },
                "care-line": {
                    "migration_status": "MIGRATED",
                    "aggregate_status": "DEGRADED",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {
                        "operator_assessment": "HEALTHY_WITH_EXTERNAL_RESTRICTIONS",
                        "primary_layer": "EXTERNAL_DEPENDENCY",
                        "failed_source_count": 4,
                        "external_access_restriction_count": 4,
                        "unclassified_source_failure_count": 0,
                    },
                },
                "american-pressure": {
                    "migration_status": "NOT_MIGRATED",
                    "aggregate_status": "UNKNOWN",
                },
                "cascadia": {
                    "migration_status": "INTENTIONALLY_INACTIVE",
                    "aggregate_status": "INTENTIONALLY_INACTIVE",
                },
            },
        },
    )

    result = main(["system", "--alerts-json", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["alert_required"] is False
    assert payload["alert_dispatches"] == []
    assert payload["unknown_dispatches"] == []
    assert [row["dispatch"] for row in payload["ignored_dispatches"]] == ["american-pressure", "cascadia"]
    assert {row["ignored_reason"] for row in payload["ignored_dispatches"]} == {"outside_active_alert_scope"}
    assert payload["policy"]["active_alert_dispatches"] == ["care-line", "food-line", "gaza", "ice"]


def test_evaluate_system_alerts_treats_missing_assessment_as_alertable() -> None:
    payload = evaluate_system_alerts(
        {
            "system_status": "FAILED",
            "exported_at": "2026-09-28T22:19:13Z",
            "dispatches": {"food-line": {"aggregate_status": "FAILED"}},
            "evidence": ["ops/status/system/latest.json"],
            "warnings": [],
        }
    )

    assert payload["alert_required"] is True
    assert payload["alert_dispatches"] == []
    assert payload["unknown_dispatches"][0]["dispatch"] == "food-line"
    assert payload["unknown_dispatches"][0]["operator_assessment"] == "UNKNOWN"


def test_system_command_defaults_to_configured_status_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import dispatch_ops

    repo_root = tmp_path / "operator"
    status_root = tmp_path / "status"
    stale_root = repo_root
    _system_status_export(status_root)
    _write_json(
        stale_root / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-11T18:07:41Z",
            "system_status": "FAILED",
            "dispatches": {},
        },
    )
    _write_json(repo_root / "ops/operator/config.json", {"operator": {"status_root": str(status_root)}})
    monkeypatch.setattr(dispatch_ops, "ROOT", repo_root)

    result = dispatch_ops.main(["system", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["exported_at"] == "2026-09-28T17:35:22Z"
    assert payload["dispatches"]["care-line"]["debug_summary"]["primary_layer"] == "EXTERNAL_DEPENDENCY"
    assert payload["evidence"] == ["ops/status/system/latest.json"]


def test_system_command_root_override_wins_over_configured_status_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import dispatch_ops

    repo_root = tmp_path / "operator"
    configured_root = tmp_path / "configured-status"
    override_root = tmp_path / "override-status"
    _system_status_export(configured_root)
    _write_json(
        override_root / "ops/status/system/latest.json",
        {
            "schema_version": "bluefern_external_system_status_v1",
            "exported_at": "2026-09-28T18:00:00Z",
            "system_status": "SUCCESS",
            "dispatches": {
                "food-line": {
                    "aggregate_status": "SUCCESS",
                    "recovery_lifecycle": "HEALTHY",
                    "debug_summary": {"primary_layer": "NONE"},
                }
            },
        },
    )
    _write_json(repo_root / "ops/operator/config.json", {"operator": {"status_root": str(configured_root)}})
    monkeypatch.setattr(dispatch_ops, "ROOT", repo_root)

    result = dispatch_ops.main(["system", "--json", "--root", str(override_root)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["exported_at"] == "2026-09-28T18:00:00Z"
    assert list(payload["dispatches"]) == ["food-line"]


def test_system_snapshot_missing_artifact_is_unknown(tmp_path: Path) -> None:
    snapshot = build_system_snapshot(root=tmp_path)

    assert snapshot["system_status"] == "UNKNOWN"
    assert snapshot["dispatches"] == {}
    assert snapshot["warnings"]


def test_food_line_sep_9_reconstructed_recovered_state(tmp_path: Path) -> None:
    _food_reconstructed(tmp_path)

    status = build_status("food-line", "2026-09-09", root=tmp_path)

    assert status.state == "COMPLETE"
    assert status.collection == "RECONSTRUCTED"
    assert status.receipts == "RECONSTRUCTED"
    assert status.recovery == "RECOVERED"
    assert status.details["unresolved_reconstructed_candidates"] == 0
    assert status.next_action == "NONE"


def test_food_line_terminal_source_exclusions_are_degraded_no_action(tmp_path: Path) -> None:
    _food_terminal_degraded_export(tmp_path)

    status = build_status("food-line", "2026-09-23", root=tmp_path)
    plan = build_recovery_plan("food-line", "2026-09-23", root=tmp_path)

    assert status.state == "DEGRADED"
    assert status.collection == "DEGRADED"
    assert status.next_action == "NONE"
    assert status.details["terminal_food_source_degradation"] is True
    assert status.details["task_statuses"] == {
        "food_line_current_intake": "SUCCESS",
        "food_line_daily_publish": "SAFE_NO_OP",
        "food_line_source_watch": "DEGRADED",
    }
    assert plan.disposition == "NO_ACTION"
    assert plan.action == "NONE"


def test_food_line_missing_resume_without_durable_source_watch_is_actionable(tmp_path: Path) -> None:
    _food_missing_resume_not_durable_export(tmp_path)

    status = build_status("food-line", "2026-09-23", root=tmp_path)
    plan = build_recovery_plan("food-line", "2026-09-23", root=tmp_path)

    assert status.state == "MISSED"
    assert status.collection == "MISSING"
    assert status.next_action == "RECOVER_MISSING_RUN"
    assert status.details["terminal_food_source_degradation"] is False
    assert plan.disposition != "NO_ACTION"
    assert plan.action != "NONE"


def test_food_line_genuine_failed_source_still_requires_attention(tmp_path: Path) -> None:
    _food_status_export(
        tmp_path,
        "2026-09-23",
        "FAILED",
        task_status="FAILED",
        classification="source_watch_failed",
    )

    status = build_status("food-line", "2026-09-23", root=tmp_path)
    plan = build_recovery_plan("food-line", "2026-09-23", root=tmp_path)

    assert status.state == "FAILED"
    assert status.next_action == "INVESTIGATE_COLLECTION"
    assert plan.action == "INVESTIGATE_COLLECTION"


def test_care_sep_18_partial_success_is_degraded_not_missed(tmp_path: Path) -> None:
    _care_partial_success(tmp_path)

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "DEGRADED"
    assert status.collection == "PARTIAL_SUCCESS"
    assert status.receipts == "OBSERVED"
    assert status.next_action == "INVESTIGATE_FAILED_SOURCES"


def test_care_sep_18_fallback_ignores_next_day_daytime_failures(tmp_path: Path) -> None:
    _care_partial_success(tmp_path)
    artifact = _artifact(tmp_path, "status/care-line/scheduler-runs/2026-09-19/daytime-failure.json")
    receipt = build_care_line_operational_receipt(
        task_key="care_line_collection",
        scheduled_for="2026-09-19T13:00:00Z",
        started_at="2026-09-19T13:00:02Z",
        completed_at="2026-09-19T13:01:00Z",
        exit_code=1,
        task_status="failure",
        run_id="daytime-failure",
        artifact_refs={"task_receipt": str(artifact)},
    )
    _write_json(tmp_path / "status/operational-health/care-line/2026-09-19/runs/daytime-failure.json", receipt)

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "DEGRADED"
    assert status.collection == "PARTIAL_SUCCESS"
    assert "care_line_collection" in status.details["observed_task_keys"]


def test_exported_care_missed_is_authoritative(tmp_path: Path) -> None:
    _care_status_export(tmp_path, "2026-09-18", "MISSED")

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "MISSED"
    assert status.next_action == "RECOVER_MISSING_RUN"


def test_exported_care_complete_receipts_are_preserved(tmp_path: Path) -> None:
    _care_status_export(tmp_path, "2026-09-18", "SUCCESS")

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.receipts == "COMPLETE"


def test_no_care_receipts_without_exported_status_is_unknown(tmp_path: Path) -> None:
    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "UNKNOWN"
    assert status.next_action == "UNKNOWN_REQUIRES_OPERATOR"


def test_one_care_receipt_does_not_imply_all_expected_runs_were_satisfied(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, "status/care-line/scheduler-runs/2026-09-19/collection.json")
    receipt = build_care_line_operational_receipt(
        task_key="care_line_collection",
        scheduled_for="2026-09-19T01:00:00Z",
        started_at="2026-09-19T01:00:01Z",
        completed_at="2026-09-19T01:01:00Z",
        exit_code=0,
        task_status="success",
        run_id="collection",
        artifact_refs={"task_receipt": str(artifact)},
    )
    _write_json(tmp_path / "status/operational-health/care-line/2026-09-19/runs/collection.json", receipt)

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "UNKNOWN"
    assert status.receipts == "OBSERVED"
    assert status.next_action == "INVESTIGATE_STATUS_EXPORT"


def test_one_care_receipt_for_each_task_key_still_has_only_observed_completeness(tmp_path: Path) -> None:
    _care_success_receipts_for_each_task_key(tmp_path)

    status = build_status("care-line", "2026-09-18", root=tmp_path)

    assert status.state == "UNKNOWN"
    assert status.receipts == "OBSERVED"
    assert status.next_action == "INVESTIGATE_STATUS_EXPORT"
    assert status.details["observed_task_keys"] == [
        "care_line_approved_release_publication",
        "care_line_collection",
        "care_line_reviewed_event_queue",
    ]


def test_food_source_watch_failure_identifies_collection_investigation(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-11", "FAILED", task_status="FAILED", classification="source_watch_failed")

    status = build_status("food-line", "2026-09-11", root=tmp_path)

    assert status.state == "FAILED"
    assert status.next_action == "INVESTIGATE_COLLECTION"


def test_exported_debug_summary_surfaces_in_status_details_and_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    debug_summary = {
        "aggregate_status": "FAILED",
        "recovery_lifecycle": "INCIDENT_OPEN",
        "primary_layer": "SOURCE",
        "primary_task_key": "food_line_source_watch",
        "primary_task_status": "FAILED",
        "primary_classification": "source_watch_failed",
        "primary_failure_stage": "source_watch",
        "failed_source_count": 3,
        "external_access_restriction_count": 1,
        "unclassified_source_failure_count": 2,
        "receipt_completeness": "COMPLETE",
    }
    _food_status_export(
        tmp_path,
        "2026-09-11",
        "FAILED",
        task_status="FAILED",
        classification="source_watch_failed",
        debug_summary=debug_summary,
    )

    status = build_status("food-line", "2026-09-11", root=tmp_path)
    assert status.details["debug_summary"] == debug_summary

    result = main(["status", "food-line", "--date", "2026-09-11", "--root", str(tmp_path)])
    output = capsys.readouterr().out

    assert result == 1
    assert "Debug summary:" in output
    assert "- Primary layer: SOURCE" in output
    assert "- Primary task: food_line_source_watch" in output
    assert "- Failed sources: 3" in output
    assert "- Unclassified source failures: 2" in output


def test_truly_missing_scheduled_receipt_is_missed(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "ops/status/ice/history/2026-09-10.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": "ice",
            "observed_date": "2026-09-10",
            "aggregate_status": "MISSED",
            "receipt_completeness": "NO_PROOF",
            "recovery_lifecycle": "HEALTHY",
            "task_summaries": [],
        },
    )

    status = build_status("ice", "2026-09-10", root=tmp_path)

    assert status.state == "MISSED"
    assert status.next_action == "RECOVER_MISSING_RUN"


def test_successful_safe_no_op_has_no_next_action(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-12", "SUCCESS", task_status="SAFE_NO_OP", classification="no_qualifying_edition")

    status = build_status("food-line", "2026-09-12", root=tmp_path)

    assert status.state == "SAFE_NO_OP"
    assert status.next_action == "NONE"


def test_success_with_pages_public_edition_is_verified_and_no_action(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-12", "SUCCESS", task_status="SUCCESS", classification="completed")
    _pages_edition(tmp_path, "food-line", "2026-09-12")

    status = build_status("food-line", "2026-09-12", root=tmp_path)

    assert status.public_state == "VERIFIED"
    assert status.next_action == "NONE"


def test_success_without_public_proof_requires_verification(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-12", "SUCCESS", task_status="SUCCESS", classification="completed")

    status = build_status("food-line", "2026-09-12", root=tmp_path)

    assert status.public_state == "NOT_VERIFIED"
    assert status.next_action == "VERIFY_PUBLIC_STATE"


def test_local_output_site_edition_only_is_not_public_proof(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-12", "SUCCESS", task_status="SUCCESS", classification="completed")
    _local_output_edition(tmp_path, "food-line", "2026-09-12")

    status = build_status("food-line", "2026-09-12", root=tmp_path)

    assert status.public_state == "NOT_VERIFIED"
    assert status.next_action == "VERIFY_PUBLIC_STATE"


def test_gaza_local_no_update_only_requires_public_verification(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "NO_UPDATE"
    assert status.public_state == "NOT_VERIFIED"
    assert status.next_action == "VERIFY_PUBLIC_STATE"


def test_gaza_pages_no_update_is_verified_no_action(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "NO_UPDATE"
    assert status.public_state == "VERIFIED"
    assert status.next_action == "NONE"


def test_gaza_pages_no_publication_needed_no_update_overrides_failed_manifest(tmp_path: Path) -> None:
    _gaza_no_update(
        tmp_path,
        pages=True,
        classification="no_publication_needed",
        include_daily_run_completed=False,
        manifest={"ok": False, "errors": ["normal edition generation blocked"]},
    )

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "NO_UPDATE"
    assert status.collection == "COMPLETE"
    assert status.editorial == "NO_QUALIFYING_MATERIAL"
    assert status.publication == "COMPLETE"
    assert status.public_state == "VERIFIED"
    assert status.receipts == "COMPLETE"
    assert status.next_action == "NONE"
    assert "bluefern-dispatches-pages/gaza/status/no-updates/2026-09-19.json" in status.evidence


def test_gaza_failed_manifest_still_fails_without_published_no_update(tmp_path: Path) -> None:
    _gaza_no_update(
        tmp_path,
        pages=False,
        classification="no_publication_needed",
        include_daily_run_completed=False,
        manifest={"ok": False, "errors": ["normal edition generation blocked"]},
    )

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "FAILED"
    assert status.collection == "FAILED"
    assert status.public_state == "NOT_VERIFIED"
    assert status.next_action == "INVESTIGATE_COLLECTION"


def test_ambiguous_incomplete_evidence_fails_closed_unknown(tmp_path: Path) -> None:
    status = build_status("gaza", "2026-09-19", root=tmp_path)

    assert status.state == "UNKNOWN"
    assert status.next_action == "UNKNOWN_REQUIRES_OPERATOR"


def test_status_command_is_read_only_for_fixture_artifacts(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)
    paths = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    before = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in paths
    }

    status = build_status("gaza", "2026-09-19", root=tmp_path)

    after = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in paths
    }
    assert status.next_action == "NONE"
    assert after == before


def test_ice_successful_monitor_run(tmp_path: Path) -> None:
    _ice_receipt(tmp_path)

    status = build_status("ice", "2026-09-10", root=tmp_path)

    assert status.state == "COMPLETE"
    assert status.collection == "COMPLETE"
    assert status.publication == "NOT_APPLICABLE"
    assert status.next_action == "NONE"


def test_recover_food_sep_9_reconstructed_recovered_has_no_action(tmp_path: Path) -> None:
    _food_reconstructed(tmp_path)

    plan = build_recovery_plan("food-line", "2026-09-09", root=tmp_path)

    assert plan.status_state == "COMPLETE"
    assert plan.disposition == "NO_ACTION"
    assert plan.action == "NONE"
    assert plan.safe_to_apply is False
    assert plan.public_side_effects is False
    assert plan.collection_rerun is False


def test_recover_care_sep_18_partial_success_investigates_failed_sources(tmp_path: Path) -> None:
    _care_partial_success(tmp_path)

    plan = build_recovery_plan("care-line", "2026-09-18", root=tmp_path)

    assert plan.status_state == "DEGRADED"
    assert plan.disposition == "OPERATOR_REVIEW_REQUIRED"
    assert plan.action == "INVESTIGATE_FAILED_SOURCES"
    assert plan.requires_operator_confirmation is True
    assert plan.collection_rerun is False


def test_recover_gaza_sep_19_published_no_update_has_no_action(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)

    plan = build_recovery_plan("gaza", "2026-09-19", root=tmp_path)

    assert plan.status_state == "NO_UPDATE"
    assert plan.disposition == "NO_ACTION"
    assert plan.action == "NONE"
    assert plan.public_side_effects is False


def test_recover_ice_successful_monitor_has_no_action(tmp_path: Path) -> None:
    _ice_receipt(tmp_path)

    plan = build_recovery_plan("ice", "2026-09-10", root=tmp_path)

    assert plan.status_state == "COMPLETE"
    assert plan.disposition == "NO_ACTION"
    assert plan.action == "NONE"


def test_recover_proven_missed_fails_closed_without_replay_target(tmp_path: Path) -> None:
    _care_status_export(tmp_path, "2026-09-18", "MISSED")

    plan = build_recovery_plan("care-line", "2026-09-18", root=tmp_path)

    assert plan.status_state == "MISSED"
    assert plan.disposition == "OPERATOR_REVIEW_REQUIRED"
    assert plan.action == "INVESTIGATE_STATUS_EXPORT"
    assert plan.collection_rerun is False
    assert "AUTHORITATIVE_EXPECTATION_PRESENT" in plan.preconditions


def test_recover_unknown_fails_closed(tmp_path: Path) -> None:
    plan = build_recovery_plan("gaza", "2026-09-19", root=tmp_path)

    assert plan.status_state == "UNKNOWN"
    assert plan.disposition == "UNKNOWN"
    assert plan.action == "INVESTIGATE_STATUS_EXPORT"
    assert plan.collection_rerun is False
    assert plan.public_side_effects is False


def test_recover_needs_review_requires_candidate_review(monkeypatch, tmp_path: Path) -> None:
    from scripts import dispatch_ops

    status = DispatchStatus(
        dispatch="food-line",
        date="2026-09-12",
        state="NEEDS_REVIEW",
        collection="COMPLETE",
        editorial="NEEDS_REVIEW",
        publication="NOT_ATTEMPTED",
        public_state="NOT_VERIFIED",
        receipts="COMPLETE",
        recovery="NONE",
        next_action="REVIEW_CANDIDATES",
        evidence=["data/dispatches/food-line/review/proposed-editions/2026-09-12.json"],
    )
    monkeypatch.setattr(dispatch_ops, "build_status", lambda *_args, **_kwargs: status)

    plan = dispatch_ops.build_recovery_plan("food-line", "2026-09-12", root=tmp_path)

    assert plan.disposition == "OPERATOR_REVIEW_REQUIRED"
    assert plan.action == "REVIEW_CANDIDATES"
    assert plan.requires_operator_confirmation is True


def test_recover_unverified_public_state_verifies_public_state(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    plan = build_recovery_plan("gaza", "2026-09-19", root=tmp_path)

    assert plan.status_state == "NO_UPDATE"
    assert plan.disposition == "PLAN_AVAILABLE"
    assert plan.action == "VERIFY_PUBLIC_STATE"
    assert "PUBLIC_STATE_NOT_VERIFIED" in plan.preconditions
    assert plan.public_side_effects is False


def test_recover_complete_receipts_missing_status_export_plans_rebuild_status(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)

    plan = build_recovery_plan("food-line", "2026-09-20", root=tmp_path)

    assert plan.status_state == "SAFE_NO_OP"
    assert plan.disposition == "PLAN_AVAILABLE"
    assert plan.action == "REBUILD_STATUS"
    assert plan.public_side_effects is False
    assert plan.collection_rerun is False
    assert plan.scheduler_changes is False
    assert plan.safe_to_apply is False
    assert plan.preconditions == [
        "AUTHORITATIVE_RECEIPTS_PRESENT",
        "NO_COLLECTION_REPLAY_REQUIRED",
        "NO_EDITORIAL_ACTION_REQUIRED",
        "NO_PUBLICATION_REQUIRED",
        "SOURCE_EVIDENCE_COMPLETE",
        "STATUS_EXPORTER_AVAILABLE",
        "STATUS_EXPORT_MISSING_OR_STALE",
    ]


def test_recover_complete_receipts_stale_status_export_plans_rebuild_status(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)
    _food_status_export(
        tmp_path,
        "2026-09-20",
        "SUCCESS",
        task_status="SAFE_NO_OP",
        classification="no_qualifying_edition",
    )

    plan = build_recovery_plan("food-line", "2026-09-20", root=tmp_path)

    assert plan.status_state == "SAFE_NO_OP"
    assert plan.disposition == "PLAN_AVAILABLE"
    assert plan.action == "REBUILD_STATUS"


def test_recover_failed_collection_investigates_collection(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-11", "FAILED", task_status="FAILED", classification="source_watch_failed")

    plan = build_recovery_plan("food-line", "2026-09-11", root=tmp_path)

    assert plan.status_state == "FAILED"
    assert plan.disposition == "PLAN_AVAILABLE"
    assert plan.action == "INVESTIGATE_COLLECTION"
    assert plan.collection_rerun is False


def test_recover_unresolved_reconstructed_candidates_requires_review(tmp_path: Path) -> None:
    _food_reconstructed(tmp_path, unresolved=2)

    status = build_status("food-line", "2026-09-09", root=tmp_path)
    plan = build_recovery_plan("food-line", "2026-09-09", root=tmp_path)

    assert status.state == "NEEDS_REVIEW"
    assert status.details["unresolved_reconstructed_candidates"] == 2
    assert plan.disposition == "OPERATOR_REVIEW_REQUIRED"
    assert plan.action == "REVIEW_CANDIDATES"
    assert plan.collection_rerun is False


def test_recover_planning_is_read_only_for_fixture_artifacts(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)
    paths = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    before = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in paths
    }

    plan = build_recovery_plan("gaza", "2026-09-19", root=tmp_path)

    after_paths = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    after = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in after_paths
    }
    assert plan.action == "NONE"
    assert after_paths == paths
    assert after == before


def test_recover_json_payload_is_deterministic(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)

    first = build_recovery_plan("gaza", "2026-09-19", root=tmp_path).to_json_payload()
    second = build_recovery_plan("gaza", "2026-09-19", root=tmp_path).to_json_payload()

    assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(second, indent=2, sort_keys=True)
    assert first["schema_version"] == "dispatch_ops_recovery_plan_v1"


def test_recover_without_apply_keeps_phase2a_planner_behavior(tmp_path: Path, capsys) -> None:
    _gaza_no_update(tmp_path, pages=False)

    rc = main(["recover", "gaza", "--date", "2026-09-19", "--root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["schema_version"] == "dispatch_ops_recovery_plan_v1"
    assert payload["disposition"] == "PLAN_AVAILABLE"
    assert payload["action"] == "VERIFY_PUBLIC_STATE"
    assert "outcome" not in payload


def test_apply_without_confirmation_is_refused(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    result = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path)

    assert result.outcome == "REFUSED"
    assert result.planned_action == "VERIFY_PUBLIC_STATE"
    assert result.changed is False
    assert result.public_side_effects is False


def test_apply_wrong_confirmation_is_refused(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    result = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="PUBLISH_NO_UPDATE")

    assert result.outcome == "REFUSED"
    assert "Missing required confirmation token" in result.warnings[0]


def test_apply_rebuild_status_without_confirmation_is_refused(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)

    result = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path)

    assert result.outcome == "REFUSED"
    assert result.planned_action == "REBUILD_STATUS"
    assert "Missing required confirmation token" in result.warnings[0]


def test_apply_rebuild_status_wrong_confirmation_is_refused(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)

    result = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "REBUILD_STATUS"
    assert result.changed is False


def test_apply_rebuild_status_rebuilds_only_operational_status_artifacts(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)
    paths, before = _snapshot_files(tmp_path)

    result = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="REBUILD_STATUS")
    after_paths, after = _snapshot_files(tmp_path)

    assert result.outcome == "REBUILT"
    assert result.changed is True
    assert result.public_side_effects is False
    assert result.scheduler_changes is False
    assert result.collection_rerun is False
    assert result.editorial_mutation is False
    assert result.publication_attempted is False
    assert result.status_artifacts_changed == [
        "ops/status/food-line/history/2026-09-20.json",
        "ops/status/food-line/latest.json",
    ]
    assert result.unexpected_changes == []
    assert result.status_after["state"] == "SAFE_NO_OP"
    new_paths = {path.relative_to(tmp_path).as_posix() for path in after_paths} - {
        path.relative_to(tmp_path).as_posix() for path in paths
    }
    assert new_paths == set(result.status_artifacts_changed)
    for path, fingerprint in before.items():
        rel = path.relative_to(tmp_path).as_posix()
        if rel not in result.status_artifacts_changed:
            assert after[path] == fingerprint


def test_apply_rebuild_status_second_run_is_deterministic_no_change(tmp_path: Path) -> None:
    _food_safe_no_op_receipts(tmp_path)

    first = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="REBUILD_STATUS")
    paths, before = _snapshot_files(tmp_path)
    second = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="REBUILD_STATUS")
    after_paths, after = _snapshot_files(tmp_path)

    assert first.outcome == "REBUILT"
    assert second.outcome == "NO_ACTION"
    assert second.changed is False
    assert after_paths == paths
    assert after == before


def test_apply_rebuild_status_refuses_incomplete_receipts_without_mutation(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, "status/food-line/scheduler-runs/2026-09-20/source-watch.json")
    receipt = build_operational_receipt(
        dispatch="food-line",
        task_key="food_line_source_watch",
        task_name="food_line_source_watch",
        scheduled_for="2026-09-20T12:00:00Z",
        started_at="2026-09-20T12:00:01Z",
        completed_at="2026-09-20T12:00:30Z",
        observed_at="2026-09-20T12:00:30Z",
        exit_code=0,
        status="SUCCESS",
        classification="completed",
        run_id="source-watch",
        artifact_refs={"task_receipt": str(artifact)},
    )
    _write_json(tmp_path / "status/operational-health/food-line/2026-09-20/runs/source-watch.json", receipt)
    paths, before = _snapshot_files(tmp_path)

    result = apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="REBUILD_STATUS")
    after_paths, after = _snapshot_files(tmp_path)

    assert result.outcome == "REFUSED"
    assert result.planned_action != "REBUILD_STATUS"
    assert after_paths == paths
    assert after == before


def test_apply_rebuild_status_detects_unrelated_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts import dispatch_ops

    _food_safe_no_op_receipts(tmp_path)
    real_rebuild = dispatch_ops.rebuild_dispatch_status_artifacts

    def mutate_extra_file(**kwargs):
        result = real_rebuild(**kwargs)
        (tmp_path / "data/unexpected.txt").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "data/unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        return result

    monkeypatch.setattr(dispatch_ops, "rebuild_dispatch_status_artifacts", mutate_extra_file)

    result = dispatch_ops.apply_recovery_plan("food-line", "2026-09-20", root=tmp_path, confirm="REBUILD_STATUS")

    assert result.outcome == "FAILED"
    assert result.unexpected_changes == ["data/unexpected.txt"]
    assert result.status_artifacts_changed == [
        "ops/status/food-line/history/2026-09-20.json",
        "ops/status/food-line/latest.json",
    ]


def test_apply_verify_public_state_with_confirmation_and_pages_proof_is_verified(monkeypatch, tmp_path: Path) -> None:
    from scripts import dispatch_ops

    before = DispatchStatus(
        dispatch="food-line",
        date="2026-09-12",
        state="COMPLETE",
        collection="COMPLETE",
        editorial="COMPLETE",
        publication="COMPLETE",
        public_state="NOT_VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action="VERIFY_PUBLIC_STATE",
        evidence=["output/site/food-line/editions/2026-09-12/index.html"],
    )
    after = DispatchStatus(**{**before.__dict__, "state": "PUBLISHED", "public_state": "VERIFIED", "next_action": "NONE"})
    statuses = iter([before, after])
    monkeypatch.setattr(dispatch_ops, "build_status", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(
        dispatch_ops,
        "_verify_public_state",
        lambda *_args, **_kwargs: (True, ["bluefern-dispatches-pages/food-line/editions/2026-09-12/index.html"]),
    )

    result = dispatch_ops.apply_recovery_plan(
        "food-line",
        "2026-09-12",
        root=tmp_path,
        confirm="VERIFY_PUBLIC_STATE",
    )

    assert result.outcome == "VERIFIED"
    assert result.changed is False
    assert result.status_before["public_state"] == "NOT_VERIFIED"
    assert result.status_after["public_state"] == "VERIFIED"
    assert result.public_side_effects is False
    assert result.publication_attempted is False


def test_apply_verify_public_state_with_missing_pages_proof_is_not_verified(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    result = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "NOT_VERIFIED"
    assert result.changed is False
    assert result.status_before["public_state"] == "NOT_VERIFIED"
    assert result.status_after["public_state"] == "NOT_VERIFIED"
    assert result.scheduler_changes is False
    assert result.collection_rerun is False


def test_apply_verify_public_state_repeated_is_deterministic_and_read_only(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)
    paths, before = _snapshot_files(tmp_path)

    first = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")
    second = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")
    after_paths, after = _snapshot_files(tmp_path)

    assert json.dumps(first.to_json_payload(), indent=2, sort_keys=True) == json.dumps(second.to_json_payload(), indent=2, sort_keys=True)
    assert first.outcome == "NOT_VERIFIED"
    assert after_paths == paths
    assert after == before


def test_apply_care_failed_sources_is_refused(tmp_path: Path) -> None:
    _care_partial_success(tmp_path)

    result = apply_recovery_plan("care-line", "2026-09-18", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "INVESTIGATE_FAILED_SOURCES"


def test_apply_care_failed_sources_is_refused_with_rebuild_status_confirmation(tmp_path: Path) -> None:
    _care_partial_success(tmp_path)

    result = apply_recovery_plan("care-line", "2026-09-18", root=tmp_path, confirm="REBUILD_STATUS")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "INVESTIGATE_FAILED_SOURCES"
    assert result.changed is False


def test_apply_failed_collection_is_refused(tmp_path: Path) -> None:
    _food_status_export(tmp_path, "2026-09-11", "FAILED", task_status="FAILED", classification="source_watch_failed")

    result = apply_recovery_plan("food-line", "2026-09-11", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "INVESTIGATE_COLLECTION"


def test_apply_missed_replay_collection_is_refused(monkeypatch, tmp_path: Path) -> None:
    from scripts import dispatch_ops

    status = DispatchStatus(
        dispatch="ice",
        date="2026-09-10",
        state="MISSED",
        collection="MISSING",
        editorial="COMPLETE",
        publication="NOT_APPLICABLE",
        public_state="NOT_APPLICABLE",
        receipts="NO_PROOF",
        recovery="HEALTHY",
        next_action="RECOVER_MISSING_RUN",
        evidence=["ops/status/ice/history/2026-09-10.json"],
        details={
            "authoritative_expected_instance": True,
            "existing_replay_tool_available": True,
            "no_later_terminal_run": True,
            "replay_has_no_public_side_effect": True,
        },
    )
    monkeypatch.setattr(dispatch_ops, "build_status", lambda *_args, **_kwargs: status)

    result = dispatch_ops.apply_recovery_plan("ice", "2026-09-10", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "REPLAY_COLLECTION"
    assert result.collection_rerun is False


def test_apply_review_candidates_is_refused(tmp_path: Path) -> None:
    _food_reconstructed(tmp_path, unresolved=2)

    result = apply_recovery_plan("food-line", "2026-09-09", root=tmp_path, confirm="VERIFY_PUBLIC_STATE")

    assert result.outcome == "REFUSED"
    assert result.planned_action == "REVIEW_CANDIDATES"


def test_apply_terminal_no_action_is_no_action_and_read_only(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=True)
    paths, before = _snapshot_files(tmp_path)

    result = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="REBUILD_STATUS")

    after_paths, after = _snapshot_files(tmp_path)
    assert result.outcome == "NO_ACTION"
    assert result.planned_action == "NONE"
    assert result.changed is False
    assert after_paths == paths
    assert after == before


def test_status_command_output_is_unchanged_by_apply_support(tmp_path: Path, capsys) -> None:
    _gaza_no_update(tmp_path, pages=True)

    rc = main(["status", "gaza", "--date", "2026-09-19", "--root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["schema_version"] == "dispatch_ops_status_v1"
    assert payload["state"] == "NO_UPDATE"
    assert "outcome" not in payload


def test_apply_json_payload_is_deterministic(tmp_path: Path) -> None:
    _gaza_no_update(tmp_path, pages=False)

    first = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="VERIFY_PUBLIC_STATE").to_json_payload()
    second = apply_recovery_plan("gaza", "2026-09-19", root=tmp_path, confirm="VERIFY_PUBLIC_STATE").to_json_payload()

    assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(second, indent=2, sort_keys=True)
    assert first["schema_version"] == "dispatch_ops_apply_v1"
    assert "status_artifacts_changed" in first
    assert "unexpected_changes" in first


def test_apply_exit_codes_are_controlled(tmp_path: Path, capsys) -> None:
    _gaza_no_update(tmp_path, pages=False)
    assert main(["recover", "gaza", "--date", "2026-09-19", "--root", str(tmp_path), "--apply", "--json"]) == 3
    capsys.readouterr()
    assert (
        main(
            [
                "recover",
                "gaza",
                "--date",
                "2026-09-19",
                "--root",
                str(tmp_path),
                "--apply",
                "--confirm",
                "VERIFY_PUBLIC_STATE",
                "--json",
            ]
        )
        == 2
    )
    capsys.readouterr()
    _write_json(tmp_path / "bluefern-dispatches-pages/gaza/status/no-updates/2026-09-19.json", json.loads((tmp_path / "output/site/gaza/status/no-updates/2026-09-19.json").read_text(encoding="utf-8")))
    assert (
        main(
            [
                "recover",
                "gaza",
                "--date",
                "2026-09-19",
                "--root",
                str(tmp_path),
                "--apply",
                "--confirm",
                "VERIFY_PUBLIC_STATE",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    _food_safe_no_op_receipts(tmp_path, date="2026-09-20")
    assert (
        main(
            [
                "recover",
                "food-line",
                "--date",
                "2026-09-20",
                "--root",
                str(tmp_path),
                "--apply",
                "--confirm",
                "REBUILD_STATUS",
                "--json",
            ]
        )
        == 0
    )
