from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


NOW = operator._parse_time("2026-09-26T12:00:00Z")


def _policy(**modes: str) -> operator.RemediationPolicy:
    return operator.RemediationPolicy(modes=modes)


def _status(*, dispatch: str = "food-line", state: str = "COMPLETE", next_action: str = "NONE") -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date="2026-09-26",
        state=state,
        collection=state,
        editorial="COMPLETE",
        publication="SAFE_NO_OP",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=["status/operational-health/food-line/2026-09-26/receipt.json"],
        details={"task_statuses": {"collection": state}},
    )


def _incident(
    *,
    classification: str = "STALE_OBSERVABILITY",
    recovery_action: str = "INVESTIGATE_STATUS_EXPORT",
    remediation: dict | None = None,
) -> operator.Incident:
    return operator.Incident(
        incident_id="bfo-test",
        incident_key=f"food-line|{classification}|2026-09-26|collection",
        dispatch="food-line",
        detected_at="2026-09-26T12:00:00Z",
        updated_at="2026-09-26T12:00:00Z",
        state=operator.IncidentState.OPEN.value,
        classification=classification,
        status_state="DEGRADED",
        recovery_disposition="OPERATOR_REVIEW_REQUIRED",
        recovery_action=recovery_action,
        evidence=["ops/status/food-line/latest.json"],
        recommended_action=recovery_action,
        affected_date="2026-09-26",
        remediation=remediation or {},
    )


def _dispatch_result(source_summary: dict[str, object]) -> operator.DispatchResult:
    return operator.DispatchResult(
        dispatch="food-line",
        state="DEGRADED",
        classification="DEGRADED_RUN",
        recommended_action="INVESTIGATE_FAILED_SOURCES",
        status_state="DEGRADED",
        observed_date="2026-09-26",
        recovery_disposition="OPERATOR_REVIEW_REQUIRED",
        recovery_action="INVESTIGATE_FAILED_SOURCES",
        incident_id="bfo-test",
        evidence=[],
        exported_status={"source_failure_summary": source_summary},
    )


def _config(runner_root: Path, status_root: Path) -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        status_root=status_root,
        dispatches={
            dispatch: operator.DispatchConfig(
                dispatch=dispatch,
                runner_root=runner_root / dispatch,
                enabled=dispatch == "food-line",
            )
            for dispatch in operator.DISPATCH_ORDER
        },
    )


def _write_operator_policy(operator_root: Path) -> None:
    operator_root.mkdir(parents=True, exist_ok=True)
    (operator_root / "remediation-policy.yaml").write_text(
        "\n".join(
            [
                "REFRESH_STATUS_EXPORT:",
                "  mode: automatic",
                "INVESTIGATE_STATUS_EXPORT:",
                "  mode: recommend",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_exported_status(status_root: Path, *, exported_at: str) -> None:
    path = status_root / "ops" / "status" / "food-line" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "dispatch": "food-line",
                "observed_date": "2026-09-26",
                "aggregate_status": "COMPLETE",
                "last_exported_at": exported_at,
            }
        ),
        encoding="utf-8",
    )


def test_persistent_external_restriction_waits_without_human_approval() -> None:
    incident = _incident(classification="DEGRADED_RUN", recovery_action="INVESTIGATE_FAILED_SOURCES")
    enriched = operator.enrich_incident_for_autonomy(
        incident,
        dispatch_result=_dispatch_result(
            {
                "all_current_failures_external": True,
                "external_access_restriction_count": 4,
                "unclassified_source_failure_count": 0,
            }
        ),
        policy=_policy(INVESTIGATE_FAILED_SOURCES="recommend"),
    )

    assert enriched.root_cause_classification == operator.RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    assert enriched.lifecycle_state == operator.AutonomyLifecycle.WAITING_EXTERNAL.value
    assert enriched.approval_required is False
    assert enriched.next_action == "INVESTIGATE_FAILED_SOURCES"


def test_mixed_external_and_unclassified_source_failure_stays_actionable() -> None:
    incident = _incident(classification="DEGRADED_RUN", recovery_action="INVESTIGATE_FAILED_SOURCES")
    enriched = operator.enrich_incident_for_autonomy(
        incident,
        dispatch_result=_dispatch_result(
            {
                "all_current_failures_external": False,
                "external_access_restriction_count": 1,
                "unclassified_source_failure_count": 1,
            }
        ),
        policy=_policy(INVESTIGATE_FAILED_SOURCES="automatic"),
    )

    assert enriched.root_cause_classification != operator.RootCauseClassification.PERSISTENT_EXTERNAL_ACCESS_RESTRICTION.value
    assert enriched.lifecycle_state == operator.AutonomyLifecycle.DIAGNOSING.value
    assert enriched.approval_required is False


def test_missing_receipt_proof_classifies_as_missing_proof() -> None:
    incident = _incident(classification="MISSED_RUN", recovery_action="INVESTIGATE_COLLECTION")
    enriched = operator.enrich_incident_for_autonomy(
        incident,
        dispatch_result=None,
        policy=_policy(INVESTIGATE_COLLECTION="recommend"),
    )

    assert enriched.root_cause_classification == operator.RootCauseClassification.MISSING_RECEIPT_PROOF.value
    assert enriched.lifecycle_state == operator.AutonomyLifecycle.APPROVAL_REQUIRED.value


def test_retry_limit_blocks_repeated_status_refresh() -> None:
    incident = _incident(
        recovery_action="REFRESH_STATUS_EXPORT",
        remediation=operator._remediation_payload(action="REFRESH_STATUS_EXPORT", attempted=True, attempt_count=2),
    )
    enriched = operator.enrich_incident_for_autonomy(
        incident,
        dispatch_result=None,
        policy=_policy(REFRESH_STATUS_EXPORT="automatic"),
    )

    assert enriched.retry_state["attempt_count"] == 2
    assert enriched.retry_state["max_attempts"] == 2
    assert enriched.retry_state["next_eligible_action"] is None
    assert enriched.retry_state["terminal_reason"] == "retry_limit_reached"


def test_check_operator_embeds_autonomy_state_and_run_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    operator_root = repo_root / "ops" / "operator"
    runner_root = tmp_path / "runners"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    (runner_root / "food-line").mkdir(parents=True)
    _write_operator_policy(operator_root)
    _write_exported_status(status_root, exported_at="2026-09-26T08:00:00Z")
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status())

    def fake_refresh(
        plan: operator.RemediationActionPlan,
        **_kwargs: object,
    ) -> operator.RemediationReceipt:
        return operator.RemediationReceipt(
            dispatch=plan.dispatch,
            incident_id=plan.incident_id,
            action=plan.proposed_action,
            accepted=True,
            outcome="REFRESHED",
            reason="status export refreshed from durable runner evidence",
            started_at="2026-09-26T12:00:00Z",
            completed_at="2026-09-26T12:00:00Z",
            changed_paths=["ops/status/food-line/latest.json"],
            validation={
                "stale_observability_after": False,
                "underlying_status_after": _status().to_json_payload(),
                "commit_created": False,
                "push_attempted": False,
            },
        )

    monkeypatch.setattr(operator, "_apply_refresh_status_export", fake_refresh)

    result = operator.check_operator(
        repo_root=repo_root,
        operator_root=operator_root,
        config=_config(runner_root, status_root),
        now=NOW,
        write_ledger=True,
    )

    assert result.autonomy_state["schema_version"] == operator.AUTONOMY_STATE_SCHEMA_VERSION
    assert result.autonomy_state["active_incident_count"] == 0
    assert result.autonomy_state["recovered_incidents"]
    assert result.incidents[0].root_cause_classification == operator.RootCauseClassification.STALE_OBSERVABILITY.value
    assert result.incidents[0].lifecycle_state == operator.AutonomyLifecycle.RECOVERED.value
    assert result.incidents[0].remediation["action"] == "REFRESH_STATUS_EXPORT"
    assert result.incidents[0].remediation["attempt_count"] == 1

    receipts = list((operator_root / "runs" / "2026-09-26").glob("operator-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["autonomy_state"]["schema_version"] == operator.AUTONOMY_STATE_SCHEMA_VERSION
    assert receipt["automatic_remediations"] == [result.incidents[0].incident_id]
