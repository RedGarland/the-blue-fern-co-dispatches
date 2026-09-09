from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import (
    FOOD_LINE_TASK_EXPECTATIONS,
    MIGRATION_TASK_EXPECTATIONS,
    OperationalStatus,
    RecoveryContext,
    RecoveryState,
    build_food_line_operational_receipt,
    build_operational_receipt,
    evaluate_dispatch_health,
    evaluate_system_health,
    load_operational_receipts,
    ops_status_is_excluded_from_pages,
    proposed_external_status_paths,
    public_site_ignored_context,
    validate_operational_receipt,
    write_operational_receipt,
)


def _receipt(task_key: str, status: OperationalStatus, *, observed_at: str = "2026-09-10T13:00:00Z") -> dict[str, object]:
    return build_operational_receipt(
        dispatch="food-line",
        task_key=task_key,
        task_name=next(task.task_name for task in FOOD_LINE_TASK_EXPECTATIONS if task.task_key == task_key),
        scheduled_for="2026-09-10",
        started_at=observed_at,
        completed_at=observed_at,
        observed_at=observed_at,
        receipt_created_at=observed_at,
        exit_code=0 if status in {OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP, OperationalStatus.UPSTREAM_BLOCKED} else 10,
        status=status,
        classification=status.value.lower(),
        run_id=f"{task_key}-run",
        runner_path="C:\\BlueFernRunner\\FoodLineCurrent6",
        branch="add/pages-repo-default",
        source_head="abc123",
        public_side_effects={},
        details={"fixture": True},
    )


def test_common_success_receipt_validates() -> None:
    receipt = _receipt("food_line_source_watch", OperationalStatus.SUCCESS)

    validate_operational_receipt(receipt)

    assert receipt["schema_version"] == "bluefern_operational_health_receipt_v1"
    assert receipt["status"] == "SUCCESS"
    assert receipt["details"] == {"fixture": True}


@pytest.mark.parametrize(
    ("task_status", "expected"),
    [
        ("skipped_not_release_ready", OperationalStatus.SAFE_NO_OP),
        ("completed_with_exclusions", OperationalStatus.DEGRADED),
        ("failed", OperationalStatus.FAILED),
        ("source_watch_not_initialized", OperationalStatus.UPSTREAM_BLOCKED),
    ],
)
def test_food_line_status_mappings(task_status: str, expected: OperationalStatus) -> None:
    action = "daily_publish" if task_status == "skipped_not_release_ready" else "source_watch"
    if task_status == "source_watch_not_initialized":
        action = "status_resume"
    receipt = build_food_line_operational_receipt(
        action=action,
        scheduled_for="2026-09-10",
        started_at="2026-09-10T12:30:00Z",
        completed_at="2026-09-10T12:31:00Z",
        exit_code=0 if expected != OperationalStatus.FAILED else 10,
        task_status=task_status,
        run_id="run-1",
        public_side_effects={},
    )

    assert receipt["status"] == expected.value


def test_september_9_publish_noop_does_not_mask_upstream_failure() -> None:
    receipts = [
        _receipt("food_line_source_watch", OperationalStatus.FAILED),
        _receipt("food_line_source_watch_resume", OperationalStatus.UPSTREAM_BLOCKED),
        _receipt("food_line_current_intake", OperationalStatus.UPSTREAM_BLOCKED),
        _receipt("food_line_daily_publish", OperationalStatus.SAFE_NO_OP),
    ]

    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=receipts,
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-09T17:00:00Z",
        recovery=RecoveryContext(incident_opened_at="2026-09-09T13:00:00Z"),
    )

    assert aggregate["overall_health"] == "FAILED"
    assert aggregate["recovery_state"] == "INCIDENT_OPEN"
    assert aggregate["failed_tasks"] == ["food_line_source_watch"]


def test_recovery_pending_after_deployment_requires_runtime_proof() -> None:
    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[],
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-09T22:00:00Z",
        recovery=RecoveryContext(incident_opened_at="2026-09-09T13:00:00Z", fix_deployed_at="2026-09-09T21:00:00Z"),
    )

    assert aggregate["recovery_state"] == RecoveryState.RECOVERY_PENDING_RUNTIME_PROOF.value
    assert aggregate["overall_health"] == "MISSED"


def test_recovery_only_after_successful_runtime_proof() -> None:
    receipts = [_receipt(task.task_key, OperationalStatus.SUCCESS, observed_at="2026-09-10T13:00:00Z") for task in FOOD_LINE_TASK_EXPECTATIONS]

    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=receipts,
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T14:00:00Z",
        recovery=RecoveryContext(
            incident_opened_at="2026-09-09T13:00:00Z",
            fix_deployed_at="2026-09-09T21:00:00Z",
            runtime_proof_after="2026-09-09T21:00:00Z",
        ),
    )

    assert aggregate["overall_health"] == "SUCCESS"
    assert aggregate["recovery_state"] == RecoveryState.RECOVERED.value


def test_missed_and_stale_observability_are_not_healthy() -> None:
    missed = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[],
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T18:00:00Z",
    )
    stale = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[_receipt(task.task_key, OperationalStatus.SUCCESS, observed_at="2026-09-08T13:00:00Z") for task in FOOD_LINE_TASK_EXPECTATIONS],
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T18:00:00Z",
    )

    assert missed["overall_health"] == "MISSED"
    assert stale["overall_health"] == "STALE_OBSERVABILITY"


def test_public_site_independence() -> None:
    context = public_site_ignored_context(public_edition_date="2026-09-10", pages_commit="deadbeef")
    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[],
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T18:00:00Z",
    )

    assert context["authoritative"] is False
    assert aggregate["overall_health"] == "MISSED"


def test_deterministic_dispatch_and_system_aggregation() -> None:
    food = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[_receipt(task.task_key, OperationalStatus.SUCCESS) for task in FOOD_LINE_TASK_EXPECTATIONS],
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at="2026-09-10T14:00:00Z",
    )
    gaza = {
        "dispatch": "gaza",
        "overall_health": "DEGRADED",
        "recovery_state": "HEALTHY",
    }

    system = evaluate_system_health([food, gaza], evaluated_at="2026-09-10T14:05:00Z")

    assert food["expected_tasks"] == [task.task_key for task in FOOD_LINE_TASK_EXPECTATIONS]
    assert system["system_health"] == "DEGRADED"
    assert sorted(system["dispatch_states"]) == ["food-line", "gaza"]


def test_extension_fields_live_under_details() -> None:
    receipt = build_operational_receipt(
        dispatch="ice",
        task_key="ice_monitor",
        task_name="Daily - ICE Monitor",
        scheduled_for="2026-09-10",
        started_at="2026-09-10T04:15:00Z",
        completed_at="2026-09-10T04:16:00Z",
        exit_code=0,
        status=OperationalStatus.SUCCESS,
        classification="healthy",
        run_id="ice-run",
        public_side_effects={},
        details={"review_queue_additions": 2, "canonical_events": 5},
    )

    validate_operational_receipt(receipt)
    assert receipt["details"]["review_queue_additions"] == 2


def test_atomic_receipt_writing_and_loading(tmp_path: Path) -> None:
    receipt = _receipt("food_line_source_watch", OperationalStatus.SUCCESS)

    result = write_operational_receipt(tmp_path, receipt)
    loaded = load_operational_receipts(tmp_path, "food-line", "2026-09-10")

    assert result.receipt_path.exists()
    assert result.latest_path.exists()
    assert loaded == [receipt]
    assert not list(result.receipt_path.parent.glob("*.tmp"))


def test_ops_status_external_paths_are_excluded_from_pages() -> None:
    paths = proposed_external_status_paths("food-line", "2026-09-10")

    assert paths["dispatch_latest"] == "ops/status/food-line/latest.json"
    assert all(ops_status_is_excluded_from_pages(path) for path in paths.values())
    assert not ops_status_is_excluded_from_pages("food-line/archive.html")


def test_external_sync_failure_is_modeled_separately_from_task_success() -> None:
    receipt = _receipt("food_line_source_watch", OperationalStatus.SUCCESS)
    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=[receipt],
        expectations=[FOOD_LINE_TASK_EXPECTATIONS[0]],
        evaluated_at="2026-09-10T14:00:00Z",
    )

    external_sync = {"status": "failed", "affects_task_health": False, "observability": OperationalStatus.STALE_OBSERVABILITY.value}

    assert aggregate["overall_health"] == "SUCCESS"
    assert external_sync["affects_task_health"] is False


def test_backward_compatibility_with_existing_food_line_artifact_refs(tmp_path: Path) -> None:
    legacy_path = tmp_path / "logs" / "food-line" / "source-watch" / "2026-09-10" / "legacy.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text('{"schema_version":"food_line_source_watch_receipt_v1"}\n', encoding="utf-8")
    receipt = build_food_line_operational_receipt(
        action="source_watch",
        scheduled_for="2026-09-10",
        started_at="2026-09-10T12:30:00Z",
        completed_at="2026-09-10T12:31:00Z",
        exit_code=0,
        task_status="completed",
        run_id="run-1",
        artifact_refs={"task_receipt": str(legacy_path)},
        details={"legacy_schema_version": "food_line_source_watch_receipt_v1"},
    )

    assert receipt["artifact_refs"]["task_receipt"] == str(legacy_path)
    assert receipt["details"]["legacy_schema_version"] == "food_line_source_watch_receipt_v1"


def test_migration_contract_lists_expected_dispatches() -> None:
    assert sorted(MIGRATION_TASK_EXPECTATIONS) == ["american-pressure", "care-line", "cascadia", "food-line", "gaza", "ice"]
    assert [task.task_key for task in MIGRATION_TASK_EXPECTATIONS["care-line"]] == [
        "care_line_collection",
        "care_line_reviewed_event_queue",
        "care_line_approved_release_publication",
    ]
