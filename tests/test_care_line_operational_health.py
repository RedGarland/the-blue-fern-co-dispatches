from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.operational_health import (
    CARE_LINE_TASK_EXPECTATIONS,
    OperationalStatus,
    build_care_line_operational_receipt,
    evaluate_dispatch_health,
)
from bluefern_dispatches.operational_status_exporter import build_care_line_status, export_status


DATE = "2026-09-10"
CARE_EXPECTED_INSTANCES = [
    {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T13:00:00Z"},
    {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T19:00:00Z"},
    {"task_key": "care_line_collection", "scheduled_for": "2026-09-11T01:00:00Z"},
    {"task_key": "care_line_reviewed_event_queue", "scheduled_for": "2026-09-10T15:00:00Z"},
    {"task_key": "care_line_approved_release_publication", "scheduled_for": "2026-09-10T15:30:00Z"},
]


def _receipt(
    task_key: str,
    run_id: str,
    status: str = "success",
    scheduled_for: str = DATE,
    *,
    started_at: str = "2026-09-10T15:00:00Z",
    completed_at: str = "2026-09-10T15:01:00Z",
    exit_code: int = 0,
) -> dict[str, object]:
    return build_care_line_operational_receipt(
        task_key=task_key,
        scheduled_for=scheduled_for,
        started_at=started_at,
        completed_at=completed_at,
        exit_code=exit_code,
        task_status=status,
        run_id=run_id,
        runner_path=r"C:\BlueFernRunner\CareLineNationalCurrent8",
        branch="add/pages-repo-default",
        source_head="6dd7e79411c11078dca4272075058c80ac8d2198",
        artifact_refs={"task_receipt": f"status/care-line/scheduler-runs/{DATE}/{run_id}.json"},
        public_side_effects={"pages_sync": False, "source_text": "private"},
        details={"fixture": True},
    )


def test_completed_care_instances_do_not_become_stale_later_same_day() -> None:
    receipts = [
        _receipt(
            "care_line_collection",
            "morning",
            "partial_success",
            scheduled_for="2026-09-10T13:00:02Z",
            started_at="2026-09-10T13:00:02Z",
            completed_at="2026-09-10T13:01:42Z",
        ),
        _receipt("care_line_reviewed_event_queue", "queue", "nothing_to_publish", scheduled_for=DATE),
        _receipt(
            "care_line_approved_release_publication",
            "publication",
            "safe_no_op",
            scheduled_for=DATE,
            started_at="2026-09-10T15:30:01Z",
            completed_at="2026-09-10T15:30:03Z",
        ),
    ]
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=receipts,
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T18:15:03Z",
        expected_instances=CARE_EXPECTED_INSTANCES,
    )

    assert aggregate["overall_health"] == OperationalStatus.DEGRADED.value
    assert aggregate["stale_observability"] == []
    assert aggregate["degraded_tasks"] == ["care_line_collection:2026-09-10T13:00:00Z"]
    assert aggregate["missed_tasks"] == []


def test_care_future_instance_is_not_missed_before_grace() -> None:
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=[],
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T20:00:00Z",
        expected_instances=[
            {"task_key": "care_line_collection", "scheduled_for": "2026-09-11T01:00:00Z"}
        ],
    )

    assert aggregate["missed_tasks"] == []
    assert aggregate["stale_observability"] == []


def test_care_elapsed_instance_without_receipt_is_missed() -> None:
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=[],
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T16:30:00Z",
        expected_instances=[
            {"task_key": "care_line_reviewed_event_queue", "scheduled_for": "2026-09-10T14:00:00Z"}
        ],
    )

    assert aggregate["overall_health"] == OperationalStatus.MISSED.value
    assert aggregate["missed_tasks"] == ["care_line_reviewed_event_queue:2026-09-10T14:00:00Z"]


def test_care_failed_receipt_remains_failed_with_expected_instance() -> None:
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=[
            _receipt("care_line_collection", "failed", "failed", scheduled_for="2026-09-10T13:00:01Z", exit_code=2)
        ],
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T16:00:00Z",
        expected_instances=[
            {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T13:00:00Z"}
        ],
    )

    assert aggregate["overall_health"] == OperationalStatus.FAILED.value
    assert aggregate["failed_tasks"] == ["care_line_collection:2026-09-10T13:00:00Z"]


def test_multiple_care_collection_instances_are_evaluated_independently() -> None:
    receipts = [
        _receipt("care_line_collection", "morning", "partial_success", scheduled_for="2026-09-10T13:00:02Z"),
        _receipt("care_line_collection", "midday", "partial_success", scheduled_for="2026-09-10T19:00:02Z"),
    ]
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=receipts,
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T22:00:00Z",
        expected_instances=[
            {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T13:00:00Z"},
            {"task_key": "care_line_collection", "scheduled_for": "2026-09-10T19:00:00Z"},
            {"task_key": "care_line_collection", "scheduled_for": "2026-09-11T01:00:00Z"},
        ],
    )

    assert aggregate["missed_tasks"] == []
    assert aggregate["stale_observability"] == []
    assert aggregate["degraded_tasks"] == [
        "care_line_collection:2026-09-10T13:00:00Z",
        "care_line_collection:2026-09-10T19:00:00Z",
    ]


def test_care_receipt_contract_maps_safe_no_op_and_shared_fields() -> None:
    receipt = _receipt("care_line_approved_release_publication", "no-release", "no_approved_release")
    assert receipt["schema_version"] == "bluefern_operational_health_receipt_v1"
    assert receipt["status"] == OperationalStatus.SAFE_NO_OP.value
    assert receipt["task_key"] == "care_line_approved_release_publication"


def test_expected_future_collection_instance_is_not_missed() -> None:
    receipt = _receipt("care_line_collection", "morning", scheduled_for="2026-09-10T13:00:00Z")
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=[],
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T12:00:00Z",
        expected_instances=[{"task_key": "care_line_collection", "scheduled_for": "2026-09-10T13:00:00Z"}],
    )
    assert aggregate["missed_tasks"] == []

    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=[receipt],
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T16:00:00Z",
        expected_instances=[{"task_key": "care_line_collection", "scheduled_for": "2026-09-10T13:00:00Z"}],
    )
    assert aggregate["failed_tasks"] == []
    assert aggregate["completed_tasks"] == ["care_line_collection:2026-09-10T13:00:00Z"]


def test_care_export_promotes_migrated_system_status_and_sanitizes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    status = tmp_path / "status"
    source.mkdir()
    (source / "status" / "operational-health" / "care-line" / DATE / "runs").mkdir(parents=True)
    receipt_root = source / "status" / "operational-health" / "care-line" / DATE / "runs"
    for receipt in (
        _receipt("care_line_collection", "collection-1"),
        _receipt("care_line_collection", "collection-2"),
        _receipt("care_line_reviewed_event_queue", "queue-1"),
        _receipt("care_line_approved_release_publication", "publication-1", "no_approved_release"),
    ):
        (receipt_root / f"{receipt['run_id']}.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
        artifact = source / str(receipt["artifact_refs"]["task_receipt"])
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")

    payload = build_care_line_status(
        source_root=source,
        date=DATE,
        evaluated_at="2026-09-10T16:00:00Z",
        exported_at="2026-09-10T16:01:00Z",
        expected_instances=CARE_EXPECTED_INSTANCES,
    )
    assert payload["migration_status"] == "MIGRATED"
    assert payload["receipt_completeness"] == "COMPLETE"
    assert len([row for row in payload["task_summaries"] if row["task_key"] == "care_line_collection"]) == 2

    result = export_status(
        source_root=source,
        care_source_root=source,
        status_checkout=status,
        date=DATE,
        evaluated_at="2026-09-10T16:00:00Z",
        exported_at="2026-09-10T16:01:00Z",
        care_expected_instances=CARE_EXPECTED_INSTANCES,
    )
    assert result["system"]["dispatches"]["care-line"]["migration_status"] == "MIGRATED"
    assert result["system"]["dispatches"]["care-line"]["aggregate_status"] == payload["aggregate_status"]
    assert result["system"]["dispatches"]["care-line"]["scheduled_health_available"] is True
    exported = json.dumps(result)
    assert "BlueFernRunner" not in exported
    assert "source_text" not in exported
    assert (status / "ops/status/care-line/latest.json").is_file()
    assert (status / f"ops/status/care-line/history/{DATE}.json").is_file()
