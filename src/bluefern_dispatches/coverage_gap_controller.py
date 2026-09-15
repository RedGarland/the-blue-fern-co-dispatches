from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from bluefern_dispatches.operational_health import OperationalStatus, TaskExpectation, parse_timestamp


COVERAGE_GAP_SCHEMA_VERSION = "bluefern.coverage_gap.v1"
BACKFILL_QUEUE_SCHEMA_VERSION = "bluefern.coverage_gap.backfill_queue.v1"
SUPPORTED_DISPATCHES = ("food-line", "care-line", "ice")
RUNTIME_QUEUE_PATH = Path("status") / "coverage-gap-controller" / "backfill-queue.json"
DURABLE_GAP_ROOT = Path("data") / "dispatches"


class CoverageGapError(ValueError):
    pass


class ObservationStatus(StrEnum):
    OBSERVED_WITH_FINDINGS = "OBSERVED_WITH_FINDINGS"
    OBSERVED_ZERO_QUALIFYING = "OBSERVED_ZERO_QUALIFYING"
    OBSERVATION_INCOMPLETE = "OBSERVATION_INCOMPLETE"


class BackfillStatus(StrEnum):
    BACKFILL_REQUIRED = "BACKFILL_REQUIRED"
    RECOVERY_IN_REVIEW = "RECOVERY_IN_REVIEW"
    RECOVERED = "RECOVERED"
    BACKFILL_NOT_REQUIRED = "BACKFILL_NOT_REQUIRED"


class GapReasonCode(StrEnum):
    SCHEDULED_RUN_FAILED = "scheduled_run_failed"
    SCHEDULED_RUN_MISSED = "scheduled_run_missed"
    STALE_OBSERVABILITY = "stale_observability"
    UPSTREAM_BLOCKED = "upstream_blocked"
    MATERIAL_COLLECTION_DEGRADATION = "material_collection_degradation"
    COLLECTION_COMPLETENESS_UNPROVEN = "collection_completeness_unproven"
    CRITICAL_SOURCE_FAILURE = "critical_source_failure"
    TERMINAL_ACCOUNTING_INCOMPLETE = "terminal_accounting_incomplete"
    DISCOVERED_BUT_LOST_DOWNSTREAM = "discovered_but_lost_downstream"
    RECALL_AUDIT_MISS = "recall_audit_miss"
    LATE_SOURCE_DISCOVERY = "late_source_discovery"
    MISSING_ORIGINAL_ARTIFACT = "missing_original_artifact"
    HISTORICAL_EVIDENCE_INCOMPLETE = "historical_evidence_incomplete"
    HISTORICAL_RECOVERY_PENDING_REVIEW = "historical_recovery_pending_review"
    HISTORICAL_RECOVERY_COMPLETED = "historical_recovery_completed"


class Criticality(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"


TERMINAL_OK_STATUSES = {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}
TERMINAL_BAD_STATUSES = {
    OperationalStatus.FAILED.value,
    OperationalStatus.MISSED.value,
    OperationalStatus.STALE_OBSERVABILITY.value,
    OperationalStatus.UPSTREAM_BLOCKED.value,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _dates(start_date: str, end_date: str) -> Iterable[str]:
    current = _parse_date(start_date)
    end = _parse_date(end_date)
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def _string_list(values: Iterable[Any]) -> list[str]:
    return [str(value) for value in values if value is not None and str(value)]


@dataclass(frozen=True)
class MaterialDegradation:
    material: bool
    reason_codes: tuple[GapReasonCode, ...] = ()
    explanation: str = ""
    alternate_coverage: bool = False


@dataclass(frozen=True)
class EvaluationInput:
    dispatch: str
    observation_date: str
    evaluated_at: str
    receipts: tuple[dict[str, Any], ...] = ()
    expected_tasks: tuple[str, ...] = ()
    expected_instances: tuple[dict[str, Any], ...] = ()
    retained_or_reviewable_count: int | None = None
    unaccounted: int | None = None
    recovery_candidate_refs: tuple[str, ...] = ()
    recovery_candidate_count: int = 0
    recovery_reason_codes: tuple[GapReasonCode, ...] = ()
    recovered_event_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    material_degradation: MaterialDegradation | None = None
    durable_gap_record: dict[str, Any] | None = None
    notes: str | None = None


@dataclass(frozen=True)
class TransitionRecord:
    previous_observation_status: str | None
    previous_backfill_status: str | None
    new_observation_status: str
    new_backfill_status: str
    transition_at: str
    reason_code: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_observation_status": self.previous_observation_status,
            "previous_backfill_status": self.previous_backfill_status,
            "new_observation_status": self.new_observation_status,
            "new_backfill_status": self.new_backfill_status,
            "transition_at": self.transition_at,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class CoverageEvaluation:
    dispatch: str
    observation_date: str
    observation_status: ObservationStatus
    backfill_status: BackfillStatus
    reason_codes: tuple[GapReasonCode, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    recovered_event_ids: tuple[str, ...] = ()
    recovery_candidate_refs: tuple[str, ...] = ()
    observed_finding_count: int | None = None
    criticality: Criticality = Criticality.NORMAL
    operator_attention_required: bool = False
    transition_history: tuple[TransitionRecord, ...] = ()
    notes: str | None = None
    source: str = "controller"

    @property
    def unresolved(self) -> bool:
        return self.backfill_status in {BackfillStatus.BACKFILL_REQUIRED, BackfillStatus.RECOVERY_IN_REVIEW}

    def to_record(self, *, detected_at: str | None = None, recovered_at: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": COVERAGE_GAP_SCHEMA_VERSION,
            "dispatch": self.dispatch,
            "observation_date": self.observation_date,
            "observation_status": self.observation_status.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "gap_reason": "; ".join(reason.value for reason in self.reason_codes),
            "detected_at": detected_at,
            "backfill_status": self.backfill_status.value,
            "recovered_at": recovered_at,
            "recovered_event_ids": list(self.recovered_event_ids),
            "observed_finding_count": self.observed_finding_count,
            "source_refs": list(self.evidence_refs),
            "transition_history": [row.to_dict() for row in self.transition_history],
            "notes": self.notes,
        }

    def to_queue_row(self, *, last_evaluated_at: str) -> dict[str, Any]:
        first_detected = None
        for row in self.transition_history:
            if row.new_backfill_status in {BackfillStatus.BACKFILL_REQUIRED.value, BackfillStatus.RECOVERY_IN_REVIEW.value}:
                first_detected = row.transition_at
                break
        return {
            "dispatch": self.dispatch,
            "observation_date": self.observation_date,
            "observation_status": self.observation_status.value,
            "backfill_status": self.backfill_status.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "first_detected_at": first_detected or last_evaluated_at,
            "last_evaluated_at": last_evaluated_at,
            "evidence_refs": list(self.evidence_refs),
            "criticality": self.criticality.value,
            "recovery_candidate_refs": list(self.recovery_candidate_refs),
            "recovered_event_ids": list(self.recovered_event_ids),
            "observed_finding_count": self.observed_finding_count,
            "operator_attention_required": self.operator_attention_required,
            "notes": self.notes,
        }


def validate_gap_record(record: dict[str, Any]) -> None:
    required = {"schema_version", "dispatch", "observation_date", "observation_status", "backfill_status"}
    missing = sorted(required - set(record))
    if missing:
        raise CoverageGapError(f"coverage gap record missing required fields: {missing}")
    if record["dispatch"] not in SUPPORTED_DISPATCHES:
        raise CoverageGapError(f"unsupported dispatch: {record['dispatch']}")
    if record["observation_status"] not in {status.value for status in ObservationStatus}:
        raise CoverageGapError(f"unsupported observation status: {record['observation_status']}")
    if record["backfill_status"] not in {status.value for status in BackfillStatus}:
        raise CoverageGapError(f"unsupported backfill status: {record['backfill_status']}")
    for reason in record.get("reason_codes") or []:
        if reason not in {item.value for item in GapReasonCode}:
            raise CoverageGapError(f"unsupported reason code: {reason}")
    if record.get("schema_version") not in {COVERAGE_GAP_SCHEMA_VERSION, "bluefern.ice.coverage_gap.v1"}:
        raise CoverageGapError(f"unsupported coverage gap schema: {record.get('schema_version')}")


def durable_gap_path(repo_root: Path, dispatch: str, observation_date: str) -> Path:
    return repo_root / DURABLE_GAP_ROOT / dispatch / "coverage-gaps" / f"{observation_date}.json"


def load_durable_gap(repo_root: Path, dispatch: str, observation_date: str) -> dict[str, Any] | None:
    path = durable_gap_path(repo_root, dispatch, observation_date)
    if not path.exists():
        return None
    record = _read_json(path)
    validate_gap_record(record)
    return record


def _from_durable_gap(record: dict[str, Any]) -> CoverageEvaluation:
    reason_codes = tuple(
        GapReasonCode(value) for value in (record.get("reason_codes") or [])
        if value in {item.value for item in GapReasonCode}
    )
    if not reason_codes:
        reason_codes = _reason_codes_from_legacy_gap(record)
    recovered_event_ids = tuple(_string_list(record.get("recovered_event_ids") or ()))
    unresolved = record["backfill_status"] in {BackfillStatus.BACKFILL_REQUIRED.value, BackfillStatus.RECOVERY_IN_REVIEW.value}
    return CoverageEvaluation(
        dispatch=str(record["dispatch"]),
        observation_date=str(record["observation_date"]),
        observation_status=ObservationStatus(str(record["observation_status"])),
        backfill_status=BackfillStatus(str(record["backfill_status"])),
        reason_codes=reason_codes,
        evidence_refs=tuple(_string_list(record.get("source_refs") or ())),
        recovered_event_ids=recovered_event_ids,
        criticality=Criticality.HIGH if unresolved else Criticality.NORMAL,
        operator_attention_required=unresolved,
        transition_history=_transition_history_from_record(record),
        notes=record.get("notes"),
        source="durable_gap_record",
    )


def _reason_codes_from_legacy_gap(record: dict[str, Any]) -> tuple[GapReasonCode, ...]:
    text = " ".join(str(record.get(key) or "") for key in ("gap_reason", "notes")).lower()
    if "recall" in text or "missed_source" in text or "missed source" in text:
        return (GapReasonCode.RECALL_AUDIT_MISS, GapReasonCode.HISTORICAL_RECOVERY_COMPLETED)
    if record.get("backfill_status") == BackfillStatus.RECOVERED.value:
        return (GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,)
    return ()


def _transition_history_from_record(record: dict[str, Any]) -> tuple[TransitionRecord, ...]:
    rows = []
    for item in record.get("transition_history") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            TransitionRecord(
                previous_observation_status=item.get("previous_observation_status"),
                previous_backfill_status=item.get("previous_backfill_status"),
                new_observation_status=str(item.get("new_observation_status") or record["observation_status"]),
                new_backfill_status=str(item.get("new_backfill_status") or record["backfill_status"]),
                transition_at=str(item.get("transition_at") or record.get("detected_at") or ""),
                reason_code=str(item.get("reason_code") or ""),
                evidence_refs=tuple(_string_list(item.get("evidence_refs") or ())),
            )
        )
    if rows:
        return tuple(rows)
    reason_codes = _reason_codes_from_legacy_gap(record)
    return (
        TransitionRecord(
            previous_observation_status=None,
            previous_backfill_status=None,
            new_observation_status=str(record["observation_status"]),
            new_backfill_status=str(record["backfill_status"]),
            transition_at=str(record.get("recovered_at") or record.get("detected_at") or ""),
            reason_code=reason_codes[0].value if reason_codes else "",
            evidence_refs=tuple(_string_list(record.get("source_refs") or ())),
        ),
    )


def _transition(
    *,
    previous: CoverageEvaluation | None,
    new_observation_status: ObservationStatus,
    new_backfill_status: BackfillStatus,
    transition_at: str,
    reason_code: GapReasonCode,
    evidence_refs: Iterable[str],
) -> tuple[TransitionRecord, ...]:
    if previous and previous.observation_status == new_observation_status and previous.backfill_status == new_backfill_status:
        return previous.transition_history
    return tuple(previous.transition_history if previous else ()) + (
        TransitionRecord(
            previous_observation_status=previous.observation_status.value if previous else None,
            previous_backfill_status=previous.backfill_status.value if previous else None,
            new_observation_status=new_observation_status.value,
            new_backfill_status=new_backfill_status.value,
            transition_at=transition_at,
            reason_code=reason_code.value,
            evidence_refs=tuple(evidence_refs),
        ),
    )


def evaluate_observation(evidence: EvaluationInput) -> CoverageEvaluation:
    if evidence.dispatch not in SUPPORTED_DISPATCHES:
        raise CoverageGapError(f"unsupported dispatch: {evidence.dispatch}")

    previous = _from_durable_gap(evidence.durable_gap_record) if evidence.durable_gap_record else None

    if evidence.unaccounted is not None and evidence.unaccounted > 0:
        return _required(evidence, previous, GapReasonCode.TERMINAL_ACCOUNTING_INCOMPLETE, Criticality.CRITICAL)

    statuses = [str(receipt.get("status") or "") for receipt in evidence.receipts]
    classifications = [str(receipt.get("classification") or "").lower() for receipt in evidence.receipts]
    finding_count = evidence.retained_or_reviewable_count or 0
    if OperationalStatus.FAILED.value in statuses:
        return _required(evidence, previous, GapReasonCode.SCHEDULED_RUN_FAILED, Criticality.CRITICAL)
    if OperationalStatus.UPSTREAM_BLOCKED.value in statuses:
        return _required(evidence, previous, GapReasonCode.UPSTREAM_BLOCKED, Criticality.CRITICAL)
    if OperationalStatus.STALE_OBSERVABILITY.value in statuses:
        return _required(evidence, previous, GapReasonCode.STALE_OBSERVABILITY, Criticality.CRITICAL)
    if evidence.recovery_candidate_count > 0 and not evidence.recovered_event_ids:
        return _in_review(evidence, previous)
    if _missed_required_run(evidence):
        return _required(evidence, previous, GapReasonCode.SCHEDULED_RUN_MISSED, Criticality.CRITICAL)
    if any("discovered_but_lost" in item for item in classifications):
        return _required(evidence, previous, GapReasonCode.DISCOVERED_BUT_LOST_DOWNSTREAM, Criticality.CRITICAL)

    if evidence.material_degradation and evidence.material_degradation.material:
        reason = evidence.material_degradation.reason_codes[0] if evidence.material_degradation.reason_codes else GapReasonCode.MATERIAL_COLLECTION_DEGRADATION
        return _required(evidence, previous, reason, Criticality.HIGH)

    if evidence.recovered_event_ids:
        return _recovered(evidence, previous)

    if OperationalStatus.DEGRADED.value in statuses:
        if evidence.material_degradation and evidence.material_degradation.alternate_coverage:
            notes = evidence.material_degradation.explanation or evidence.notes
            if finding_count > 0:
                return _with_findings_no_backfill(evidence, previous, notes=notes)
            return _complete_zero(evidence, previous, notes=notes)
        return _required(evidence, previous, GapReasonCode.COLLECTION_COMPLETENESS_UNPROVEN, Criticality.HIGH)

    if finding_count > 0:
        return _with_findings_no_backfill(evidence, previous)

    if statuses and all(status in TERMINAL_OK_STATUSES for status in statuses):
        return _complete_zero(evidence, previous)

    if not statuses and not previous:
        return _unknown_incomplete(evidence, previous, GapReasonCode.STALE_OBSERVABILITY)

    return previous or _unknown_incomplete(evidence, previous, GapReasonCode.STALE_OBSERVABILITY)


def _missed_required_run(evidence: EvaluationInput) -> bool:
    expected_instance_tasks: set[str] = set()
    if evidence.expected_instances:
        expected_instance_tasks = {str(item.get("task_key") or "") for item in evidence.expected_instances}
        expected = {(str(item.get("task_key") or ""), str(item.get("scheduled_for") or "")) for item in evidence.expected_instances}
        observed = {(str(item.get("task_key") or ""), str(item.get("scheduled_for") or "")) for item in evidence.receipts}
        if expected - observed:
            return True
    if evidence.expected_tasks:
        observed_keys = {str(item.get("task_key") or "") for item in evidence.receipts}
        expected_tasks = set(evidence.expected_tasks) - expected_instance_tasks
        return bool(expected_tasks - observed_keys)
    return False


def _required(evidence: EvaluationInput, previous: CoverageEvaluation | None, reason: GapReasonCode, criticality: Criticality) -> CoverageEvaluation:
    return CoverageEvaluation(
        dispatch=evidence.dispatch,
        observation_date=evidence.observation_date,
        observation_status=ObservationStatus.OBSERVATION_INCOMPLETE,
        backfill_status=BackfillStatus.BACKFILL_REQUIRED,
        reason_codes=(reason,),
        evidence_refs=evidence.source_refs,
        recovery_candidate_refs=evidence.recovery_candidate_refs,
        observed_finding_count=evidence.retained_or_reviewable_count,
        criticality=criticality,
        operator_attention_required=True,
        transition_history=_transition(
            previous=previous,
            new_observation_status=ObservationStatus.OBSERVATION_INCOMPLETE,
            new_backfill_status=BackfillStatus.BACKFILL_REQUIRED,
            transition_at=evidence.evaluated_at,
            reason_code=reason,
            evidence_refs=evidence.source_refs,
        ),
        notes=evidence.notes,
    )


def _in_review(evidence: EvaluationInput, previous: CoverageEvaluation | None) -> CoverageEvaluation:
    reason_codes = evidence.recovery_reason_codes or (GapReasonCode.RECALL_AUDIT_MISS,)
    if GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW not in reason_codes:
        reason_codes = (*reason_codes, GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW)
    transition_reason = next((reason for reason in reason_codes if reason != GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW), GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW)
    return CoverageEvaluation(
        dispatch=evidence.dispatch,
        observation_date=evidence.observation_date,
        observation_status=ObservationStatus.OBSERVATION_INCOMPLETE,
        backfill_status=BackfillStatus.RECOVERY_IN_REVIEW,
        reason_codes=reason_codes,
        evidence_refs=evidence.source_refs,
        recovery_candidate_refs=evidence.recovery_candidate_refs,
        observed_finding_count=evidence.retained_or_reviewable_count,
        criticality=Criticality.HIGH,
        operator_attention_required=True,
        transition_history=_transition(
            previous=previous,
            new_observation_status=ObservationStatus.OBSERVATION_INCOMPLETE,
            new_backfill_status=BackfillStatus.RECOVERY_IN_REVIEW,
            transition_at=evidence.evaluated_at,
            reason_code=transition_reason,
            evidence_refs=evidence.source_refs,
        ),
        notes=evidence.notes,
    )


def _recovered(evidence: EvaluationInput, previous: CoverageEvaluation | None) -> CoverageEvaluation:
    return CoverageEvaluation(
        dispatch=evidence.dispatch,
        observation_date=evidence.observation_date,
        observation_status=ObservationStatus.OBSERVED_WITH_FINDINGS,
        backfill_status=BackfillStatus.RECOVERED,
        reason_codes=(GapReasonCode.RECALL_AUDIT_MISS, GapReasonCode.HISTORICAL_RECOVERY_COMPLETED),
        evidence_refs=evidence.source_refs,
        recovered_event_ids=evidence.recovered_event_ids,
        observed_finding_count=len(evidence.recovered_event_ids),
        criticality=Criticality.NORMAL,
        operator_attention_required=False,
        transition_history=_transition(
            previous=previous,
            new_observation_status=ObservationStatus.OBSERVED_WITH_FINDINGS,
            new_backfill_status=BackfillStatus.RECOVERED,
            transition_at=evidence.evaluated_at,
            reason_code=GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,
            evidence_refs=evidence.source_refs,
        ),
        notes=evidence.notes,
    )


def _with_findings_no_backfill(evidence: EvaluationInput, previous: CoverageEvaluation | None, *, notes: str | None = None) -> CoverageEvaluation:
    return CoverageEvaluation(
        dispatch=evidence.dispatch,
        observation_date=evidence.observation_date,
        observation_status=ObservationStatus.OBSERVED_WITH_FINDINGS,
        backfill_status=BackfillStatus.BACKFILL_NOT_REQUIRED,
        reason_codes=(),
        evidence_refs=evidence.source_refs,
        observed_finding_count=evidence.retained_or_reviewable_count,
        transition_history=_transition(
            previous=previous,
            new_observation_status=ObservationStatus.OBSERVED_WITH_FINDINGS,
            new_backfill_status=BackfillStatus.BACKFILL_NOT_REQUIRED,
            transition_at=evidence.evaluated_at,
            reason_code=GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,
            evidence_refs=evidence.source_refs,
        ) if previous else (),
        notes=notes or evidence.notes,
    )


def _complete_zero(evidence: EvaluationInput, previous: CoverageEvaluation | None, *, notes: str | None = None) -> CoverageEvaluation:
    return CoverageEvaluation(
        dispatch=evidence.dispatch,
        observation_date=evidence.observation_date,
        observation_status=ObservationStatus.OBSERVED_ZERO_QUALIFYING,
        backfill_status=BackfillStatus.BACKFILL_NOT_REQUIRED,
        evidence_refs=evidence.source_refs,
        transition_history=_transition(
            previous=previous,
            new_observation_status=ObservationStatus.OBSERVED_ZERO_QUALIFYING,
            new_backfill_status=BackfillStatus.BACKFILL_NOT_REQUIRED,
            transition_at=evidence.evaluated_at,
            reason_code=GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,
            evidence_refs=evidence.source_refs,
        ) if previous else (),
        notes=notes or evidence.notes,
    )


def _unknown_incomplete(evidence: EvaluationInput, previous: CoverageEvaluation | None, reason: GapReasonCode) -> CoverageEvaluation:
    return _required(evidence, previous, reason, Criticality.CRITICAL)


def _receipt_paths(repo_root: Path, dispatch: str, observation_date: str) -> list[Path]:
    root = repo_root / "status" / "operational-health" / dispatch / observation_date / "runs"
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


def _load_receipts(repo_root: Path, dispatch: str, observation_date: str) -> tuple[dict[str, Any], ...]:
    receipts: list[dict[str, Any]] = []
    for path in _receipt_paths(repo_root, dispatch, observation_date):
        try:
            value = _read_json(path)
        except json.JSONDecodeError:
            continue
        if value.get("dispatch") == dispatch:
            receipts.append(value)
    return tuple(receipts)


def _load_dispatch_status(runtime_root: Path, dispatch: str, observation_date: str) -> dict[str, Any] | None:
    candidates = [
        runtime_root / "ops" / "status" / dispatch / f"{observation_date}.json",
        runtime_root / "ops" / "status" / dispatch / "latest.json",
        runtime_root / "status" / "operational-health" / dispatch / f"{observation_date}.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        if payload.get("dispatch") == dispatch and str(payload.get("observed_date") or observation_date) == observation_date:
            return payload
    return None


def _load_ice_monitor_receipts(runtime_root: Path, observation_date: str) -> tuple[dict[str, Any], ...]:
    root = runtime_root / "data" / "dispatches" / "ice" / "monitor" / "runs" / observation_date
    receipts: list[dict[str, Any]] = []
    if not root.exists():
        return ()
    for path in sorted(root.glob("*/monitor_receipt.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        summary = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
        status = OperationalStatus.SUCCESS.value if payload.get("ok") is True or str(payload.get("status") or "").lower() == "success" else OperationalStatus.FAILED.value
        collection_health = str(summary.get("collection_health") or "")
        if status == OperationalStatus.SUCCESS.value and collection_health in {"degraded", "collection_degraded", "limited_source_update"}:
            status = OperationalStatus.DEGRADED.value
        run_id = str(payload.get("run_id") or path.parent.name)
        receipts.append(
            {
                "dispatch": "ice",
                "task_key": "ice_monitor",
                "task_name": "Daily - ICE Monitor",
                "scheduled_for": observation_date,
                "started_at": _timestamp_from_run_id(run_id) or observation_date,
                "completed_at": _timestamp_from_run_id(run_id) or observation_date,
                "observed_at": _timestamp_from_run_id(run_id) or observation_date,
                "receipt_created_at": _timestamp_from_run_id(run_id) or observation_date,
                "exit_code": 0 if status != OperationalStatus.FAILED.value else 1,
                "status": status,
                "classification": collection_health or str(payload.get("status") or ""),
                "run_id": run_id,
                "source_head": payload.get("source_commit"),
                "artifact_refs": {"monitor_receipt": str(path)},
                "publication_attempted": False,
                "publication_status": "safe_no_op",
                "public_side_effects": {"publication_side_effects": payload.get("publication_side_effects")},
                "details": {
                    "collection_health": collection_health,
                    "configured_providers": summary.get("configured_providers"),
                    "attempted_providers": summary.get("attempted_providers"),
                    "successful_providers": summary.get("successful_providers"),
                    "failed_providers": summary.get("failed_providers"),
                    "canonical_events": summary.get("canonical_events"),
                    "review_queue_additions": summary.get("review_queue_additions"),
                    "unaccounted": 0 if payload.get("ok") is True else None,
                    "critical_provider_failure": bool(summary.get("critical_provider_failure")),
                },
            }
        )
    return tuple(receipts)


def _timestamp_from_run_id(run_id: str) -> str | None:
    marker = "T"
    for token in run_id.split("-"):
        if len(token) == 16 and marker in token:
            try:
                return datetime.strptime(token, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                return None
    return None


def _elapsed_expected_instances(
    instances: Iterable[dict[str, Any]],
    *,
    evaluated_at: str,
    expectations: dict[str, TaskExpectation],
) -> tuple[dict[str, Any], ...]:
    evaluated = parse_timestamp(evaluated_at) or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for instance in instances:
        task_key = str(instance.get("task_key") or "")
        scheduled_for = str(instance.get("scheduled_for") or "")
        scheduled = parse_timestamp(scheduled_for)
        if scheduled is not None and scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        expectation = expectations.get(task_key)
        grace = expectation.grace_minutes if expectation else 0
        if scheduled is not None and evaluated < scheduled + timedelta(minutes=grace):
            continue
        rows.append({"task_key": task_key, "scheduled_for": scheduled_for})
    return tuple(rows)


def _load_recovery_candidate_evidence(repo_root: Path, dispatch: str, observation_date: str) -> tuple[tuple[str, ...], tuple[GapReasonCode, ...]]:
    refs: list[str] = []
    reasons: list[GapReasonCode] = []
    recovery_root = repo_root / "data" / "private-agent-handoff" / "discovery-recovery"
    for path in sorted(recovery_root.glob("*/*/recovery-review-intake.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        for item in payload.get("items") or []:
            if item.get("dispatch") != dispatch:
                continue
            observed_date = str(item.get("observed_date") or "")
            production_run_id = str(item.get("production_run_id") or "")
            if observed_date == observation_date or production_run_id.startswith(observation_date.replace("-", "")):
                refs.append(path.relative_to(repo_root).as_posix())
                reasons.extend(_candidate_reason_codes(item, payload))
                break
    return tuple(sorted(set(refs))), tuple(dict.fromkeys(reasons))


def _load_recovery_candidates(repo_root: Path, dispatch: str, observation_date: str) -> tuple[str, ...]:
    refs, _reasons = _load_recovery_candidate_evidence(repo_root, dispatch, observation_date)
    return refs


def _candidate_reason_codes(item: dict[str, Any], payload: dict[str, Any]) -> tuple[GapReasonCode, ...]:
    values = []
    for key in ("coverage_gap_reason_code", "gap_reason_code", "recovery_reason_code"):
        if item.get(key):
            values.append(str(item[key]))
    for key in ("coverage_gap_reason_codes", "gap_reason_codes", "recovery_reason_codes"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(str(value) for value in raw)
    reasons = [GapReasonCode(value) for value in values if value in {reason.value for reason in GapReasonCode}]
    if reasons:
        return tuple(dict.fromkeys(reasons))

    method = str(item.get("recovery_method") or payload.get("provenance_class") or "").lower()
    audit = str(item.get("discovery_audit_classification") or "").lower()
    if "late_source" in method or "late source" in method or "late_source" in audit:
        return (GapReasonCode.LATE_SOURCE_DISCOVERY,)
    if "missing_original_artifact" in method or "missing original artifact" in method:
        return (GapReasonCode.MISSING_ORIGINAL_ARTIFACT,)
    if item.get("production_artifact_present") is False and "failed" in method and "before" in method:
        return (GapReasonCode.MISSING_ORIGINAL_ARTIFACT,)
    return (GapReasonCode.RECALL_AUDIT_MISS,)


def _load_recovered_event_ids(repo_root: Path, dispatch: str, observation_date: str) -> tuple[str, ...]:
    root = repo_root / "data" / "dispatches" / dispatch / "historical-events"
    if not root.exists():
        return ()
    ids = []
    for path in sorted(root.glob("*/*.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        repaired_observation = str(payload.get("observation_date") or payload.get("recovered_observation_date") or payload.get("recovery_observation_date") or "")
        if not repaired_observation:
            repaired_observation = str(payload.get("event_date") or path.parent.name)
        if repaired_observation != observation_date:
            continue
        if payload.get("recovery_provenance") and payload.get("original_production_discovery_lineage_present") is False:
            ids.append(str(payload.get("event_id") or path.stem))
    return tuple(ids)


def _as_repo_ref(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _food_legacy_intake_root(root: Path, observation_date: str) -> Path:
    return root / "data" / "dispatches" / "food-line" / "agent-intake" / observation_date


def _load_food_legacy_intake(root: Path, observation_date: str, *, repo_root: Path) -> tuple[tuple[dict[str, Any], ...], int | None, int | None, tuple[str, ...]]:
    intake_root = _food_legacy_intake_root(root, observation_date)
    if not intake_root.exists():
        return (), None, None, ()
    receipts: list[dict[str, Any]] = []
    reviewable_counts: list[int] = []
    unaccounted_counts: list[int] = []
    refs: list[str] = []
    for path in sorted(intake_root.glob("*.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        lifecycle = payload.get("lifecycle_reconciliation") if isinstance(payload.get("lifecycle_reconciliation"), dict) else {}
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        if not isinstance(lifecycle.get("unaccounted"), int):
            continue
        discovered = lifecycle.get("discovered")
        terminal = lifecycle.get("terminal_or_handoff")
        if isinstance(discovered, int) and isinstance(terminal, int) and terminal != discovered:
            continue
        retained = 0
        for key in ("retained_for_review", "eligible_for_review", "selected", "approved", "approve", "approve_with_edit"):
            value = counts.get(key)
            if isinstance(value, int):
                retained = max(retained, value)
        unaccounted = int(lifecycle["unaccounted"])
        reviewable_counts.append(retained)
        unaccounted_counts.append(unaccounted)
        refs.append(_as_repo_ref(repo_root, path))
        receipts.append(
            {
                "dispatch": "food-line",
                "task_key": "food_line_source_watch",
                "task_name": "Food Line legacy Source Watch intake",
                "scheduled_for": observation_date,
                "started_at": observation_date,
                "completed_at": observation_date,
                "observed_at": observation_date,
                "receipt_created_at": observation_date,
                "exit_code": 0,
                "status": OperationalStatus.SUCCESS.value,
                "classification": "legacy_intake_terminal_accounting",
                "run_id": str(payload.get("agent_run_id") or payload.get("source_watch_run_id") or path.stem),
                "artifact_refs": {"legacy_intake": _as_repo_ref(repo_root, path)},
                "publication_attempted": False,
                "publication_status": "safe_no_op",
                "public_side_effects": {},
                "details": {
                    "legacy_evidence": True,
                    "discovered": discovered,
                    "terminal_or_handoff": terminal,
                    "retained_for_review": retained,
                    "unaccounted": unaccounted,
                },
            }
        )
    if not receipts:
        return (), None, None, ()
    return tuple(receipts), max(reviewable_counts) if reviewable_counts else 0, max(unaccounted_counts), tuple(refs)


def _load_food_completed_operator_recovery(repo_root: Path, observation_date: str) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    publication_state_root = repo_root / "data" / "dispatches" / "food-line" / "publication-state"
    for publication_state in sorted(publication_state_root.glob("*.json")):
        try:
            state = _read_json(publication_state)
        except json.JSONDecodeError:
            continue
        if state.get("schema_version") != "food_line_operator_recovery_publication_state_v1":
            continue
        if state.get("publication_completed") is not True or state.get("successful_live_verification") is not True:
            continue
        if state.get("september_10_original_production_status") != "failed":
            continue
        if state.get("september_10_production_reclassified") is not False:
            continue
        item_ids: list[str] = []
        matches_observation_date = False
        for item in state.get("published_items") or []:
            run_id = str(item.get("original_agent_run_id") or "")
            if run_id.startswith(f"food-line-source-watch-{observation_date.replace('-', '')}"):
                matches_observation_date = True
            item_id = str(item.get("item_id") or "")
            if item_id:
                item_ids.append(item_id)
        if not matches_observation_date or not item_ids:
            continue
        refs = [_as_repo_ref(repo_root, publication_state)]
        publication_sha = state.get("publication_authorization_sha256")
        release_ref = repo_root / "releases" / "food-line" / "operator-recovery" / "september-10-food-line-operator-recovery-release-v1.json"
        publication_auth = repo_root / "publication-authorizations" / "food-line" / "operator-recovery" / "september-10-food-line-operator-recovery-publication-v1.json"
        if release_ref.exists():
            refs.append(_as_repo_ref(repo_root, release_ref))
        if publication_auth.exists():
            refs.append(_as_repo_ref(repo_root, publication_auth))
        notes = (
            "Food Line operator recovery completed with recovery-disclosed publication; "
            "original production observation remains failed and original discovery lineage is absent."
        )
        if publication_sha:
            notes = f"{notes} Publication authorization {publication_sha}."
        return tuple(item_ids), tuple(refs), notes
    return (), (), None


def _load_care_scheduler_receipts(runtime_root: Path, observation_date: str) -> tuple[dict[str, Any], ...]:
    root = runtime_root / "status" / "care-line" / "scheduler-runs" / observation_date
    if not root.exists():
        return ()
    receipts: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            continue
        status_text = str(payload.get("status") or payload.get("pipeline_status") or "").lower()
        if status_text == "failure":
            status = OperationalStatus.FAILED.value
        elif status_text == "partial_success":
            status = OperationalStatus.DEGRADED.value
        elif status_text == "success":
            status = OperationalStatus.SUCCESS.value
        else:
            status = OperationalStatus.DEGRADED.value
        receipts.append(
            {
                "dispatch": "care-line",
                "task_key": "care_line_collection",
                "task_name": "Care Line collection scheduler",
                "scheduled_for": str(payload.get("started_at") or observation_date),
                "started_at": str(payload.get("started_at") or observation_date),
                "completed_at": str(payload.get("completed_at") or observation_date),
                "observed_at": str(payload.get("completed_at") or observation_date),
                "receipt_created_at": str(payload.get("completed_at") or observation_date),
                "exit_code": payload.get("pipeline_exit_code"),
                "status": status,
                "classification": "historical_scheduler_receipt",
                "run_id": str(payload.get("run_id") or path.stem),
                "artifact_refs": {"scheduler_receipt": str(path)},
                "publication_attempted": False,
                "publication_status": "safe_no_op",
                "public_side_effects": payload.get("publication_side_effects") or {},
                "details": {
                    "historical_scheduler_receipt": True,
                    "run_manifest_path": payload.get("run_manifest_path"),
                    "successful_attempt_count": payload.get("successful_attempt_count"),
                    "failed_source_count": payload.get("failed_source_count"),
                },
            }
        )
    return tuple(receipts)


def _load_care_authoritative_collection(runtime_root: Path, observation_date: str) -> tuple[tuple[dict[str, Any], ...], int | None, int | None, tuple[str, ...], MaterialDegradation | None]:
    root = runtime_root / "data" / "dispatches" / "care-line" / "collection-runs" / observation_date
    if not root.exists():
        return (), None, None, (), None
    receipts: list[dict[str, Any]] = []
    refs: list[str] = []
    reviewable_counts: list[int] = []
    unaccounted_values: list[int] = []
    incomplete = False
    for path in sorted(root.glob("*/run-manifest.json")):
        try:
            payload = _read_json(path)
        except json.JSONDecodeError:
            incomplete = True
            continue
        raw_count = payload.get("raw_items_retrieved_this_run")
        prefilter_count = payload.get("prefilter_decision_count")
        failed_extraction_count = int(payload.get("failed_extraction_count") or 0)
        reviewable = int(payload.get("qualified_candidates_created_this_run") or payload.get("active_review_queue_count") or 0)
        terminal_complete = isinstance(raw_count, int) and isinstance(prefilter_count, int) and raw_count == prefilter_count and failed_extraction_count == 0
        refs.append(str(path))
        if not terminal_complete:
            incomplete = True
            continue
        reviewable_counts.append(reviewable)
        unaccounted_values.append(0)
        receipts.append(
            {
                "dispatch": "care-line",
                "task_key": "care_line_collection",
                "task_name": "Care Line authoritative collection manifest",
                "scheduled_for": observation_date,
                "started_at": str(payload.get("started_at") or observation_date),
                "completed_at": str(payload.get("completed_at") or observation_date),
                "observed_at": str(payload.get("completed_at") or observation_date),
                "receipt_created_at": str(payload.get("completed_at") or observation_date),
                "exit_code": 0,
                "status": OperationalStatus.SUCCESS.value,
                "classification": "authoritative_collection_terminal_accounting",
                "run_id": str(payload.get("run_id") or path.parent.name),
                "artifact_refs": {"collection_manifest": str(path)},
                "publication_attempted": False,
                "publication_status": "safe_no_op",
                "public_side_effects": {},
                "details": {"reviewable_events": reviewable, "unaccounted": 0},
            }
        )
    if incomplete and not receipts:
        degradation = MaterialDegradation(
            material=True,
            reason_codes=(GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE,),
            explanation="Care surviving collection artifacts do not prove terminal candidate accounting.",
        )
        receipts = (
            {
                "dispatch": "care-line",
                "task_key": "care_line_collection",
                "task_name": "Care Line incomplete historical collection evidence",
                "scheduled_for": observation_date,
                "started_at": observation_date,
                "completed_at": observation_date,
                "observed_at": observation_date,
                "receipt_created_at": observation_date,
                "exit_code": 1,
                "status": OperationalStatus.DEGRADED.value,
                "classification": "historical_collection_evidence_incomplete",
                "run_id": f"care-line-{observation_date}-historical-evidence-incomplete",
                "artifact_refs": {"collection_manifest": refs[0] if refs else str(root)},
                "publication_attempted": False,
                "publication_status": "safe_no_op",
                "public_side_effects": {},
                "details": {"historical_evidence_incomplete": True},
            },
        )
    else:
        degradation = None
    return tuple(receipts), max(reviewable_counts) if reviewable_counts else None, max(unaccounted_values) if unaccounted_values else None, tuple(refs), degradation


class DispatchCoverageAdapter:
    dispatch: str
    expected_tasks: tuple[str, ...] = ()

    def build_input(self, repo_root: Path, observation_date: str, *, evaluated_at: str, runtime_root: Path | None = None) -> EvaluationInput:
        durable = load_durable_gap(repo_root, self.dispatch, observation_date)
        runtime = runtime_root
        receipts = _load_receipts(runtime, self.dispatch, observation_date) if runtime is not None else ()
        candidates, recovery_reasons = _load_recovery_candidate_evidence(repo_root, self.dispatch, observation_date)
        recovered = _load_recovered_event_ids(repo_root, self.dispatch, observation_date)
        unaccounted = self._unaccounted(receipts)
        runtime_evidence_root = runtime / "status" / "operational-health" / self.dispatch if runtime is not None else None
        expected_tasks = self.expected_tasks if receipts or (runtime_evidence_root is not None and runtime_evidence_root.exists()) else ()
        source_refs = tuple(filter(None, (
            *(path for path in candidates),
            *self._artifact_refs(receipts),
        )))
        notes = None
        if runtime_root is None and not receipts:
            notes = f"{self.dispatch} runtime root was not supplied; runtime evidence is unavailable"
        return EvaluationInput(
            dispatch=self.dispatch,
            observation_date=observation_date,
            evaluated_at=evaluated_at,
            receipts=receipts,
            expected_tasks=expected_tasks,
            unaccounted=unaccounted,
            recovery_candidate_refs=candidates,
            recovery_candidate_count=len(candidates),
            recovery_reason_codes=recovery_reasons,
            recovered_event_ids=recovered,
            source_refs=source_refs,
            durable_gap_record=durable,
            material_degradation=self._material_degradation(receipts),
            retained_or_reviewable_count=self._reviewable_count(receipts),
            notes=notes,
        )

    def _artifact_refs(self, receipts: Iterable[dict[str, Any]]) -> tuple[str, ...]:
        refs = []
        for receipt in receipts:
            artifact_refs = receipt.get("artifact_refs") if isinstance(receipt.get("artifact_refs"), dict) else {}
            refs.extend(str(value) for value in artifact_refs.values() if isinstance(value, str) and value)
        return tuple(refs)

    def _unaccounted(self, receipts: Iterable[dict[str, Any]]) -> int | None:
        values = []
        for receipt in receipts:
            details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
            for key in ("unaccounted", "unaccounted_count"):
                if isinstance(details.get(key), int):
                    values.append(details[key])
        return max(values) if values else None

    def _reviewable_count(self, receipts: Iterable[dict[str, Any]]) -> int | None:
        counts = []
        for receipt in receipts:
            details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
            for key in ("review_queue_additions", "reviewable_events", "canonical_events", "retained_for_review"):
                if isinstance(details.get(key), int):
                    counts.append(details[key])
        return max(counts) if counts else None

    def _material_degradation(self, receipts: Iterable[dict[str, Any]]) -> MaterialDegradation | None:
        material = False
        alternate = False
        reasons: list[GapReasonCode] = []
        explanations: list[str] = []
        for receipt in receipts:
            if receipt.get("status") != OperationalStatus.DEGRADED.value:
                continue
            details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
            if details.get("material_coverage_loss") is True or details.get("critical_source_failure") is True:
                material = True
                reasons.append(GapReasonCode.CRITICAL_SOURCE_FAILURE if details.get("critical_source_failure") else GapReasonCode.MATERIAL_COLLECTION_DEGRADATION)
            if details.get("alternate_coverage_complete") is True or details.get("equivalent_coverage_succeeded") is True:
                alternate = True
            if details.get("coverage_loss_reason"):
                explanations.append(str(details["coverage_loss_reason"]))
        if not material and not alternate:
            return None
        return MaterialDegradation(material=material, reason_codes=tuple(reasons), explanation="; ".join(explanations), alternate_coverage=alternate)


class FoodLineCoverageAdapter(DispatchCoverageAdapter):
    dispatch = "food-line"
    expected_tasks = ("food_line_source_watch", "food_line_source_watch_resume", "food_line_current_intake", "food_line_daily_publish")

    def build_input(self, repo_root: Path, observation_date: str, *, evaluated_at: str, runtime_root: Path | None = None) -> EvaluationInput:
        base = super().build_input(repo_root, observation_date, evaluated_at=evaluated_at, runtime_root=runtime_root)
        recovered_ids, recovery_refs, recovery_notes = _load_food_completed_operator_recovery(repo_root, observation_date)
        if recovered_ids:
            return EvaluationInput(
                **{
                    **base.__dict__,
                    "receipts": (),
                    "expected_tasks": (),
                    "recovered_event_ids": recovered_ids,
                    "source_refs": tuple(dict.fromkeys((*base.source_refs, *recovery_refs))),
                    "notes": recovery_notes,
                }
            )

        if base.receipts:
            return base

        legacy_receipts, reviewable_count, unaccounted, refs = _load_food_legacy_intake(repo_root, observation_date, repo_root=repo_root)
        if not legacy_receipts and runtime_root is not None:
            legacy_receipts, reviewable_count, unaccounted, refs = _load_food_legacy_intake(runtime_root, observation_date, repo_root=repo_root)
        if legacy_receipts:
            return EvaluationInput(
                **{
                    **base.__dict__,
                    "receipts": legacy_receipts,
                    "expected_tasks": (),
                    "retained_or_reviewable_count": reviewable_count,
                    "unaccounted": unaccounted,
                    "source_refs": tuple(dict.fromkeys((*base.source_refs, *refs))),
                    "notes": "Food Line legacy intake/reconciliation evidence certifies terminal accounting.",
                }
            )
        return base


class CareLineCoverageAdapter(DispatchCoverageAdapter):
    dispatch = "care-line"
    expected_tasks = ("care_line_collection", "care_line_reviewed_event_queue", "care_line_approved_release_publication")

    def build_input(self, repo_root: Path, observation_date: str, *, evaluated_at: str, runtime_root: Path | None = None) -> EvaluationInput:
        base = super().build_input(repo_root, observation_date, evaluated_at=evaluated_at, runtime_root=runtime_root)
        runtime = runtime_root
        if runtime is not None and not base.receipts:
            collection_receipts, reviewable_count, unaccounted, collection_refs, collection_degradation = _load_care_authoritative_collection(runtime, observation_date)
            if collection_receipts:
                base = EvaluationInput(
                    **{
                        **base.__dict__,
                        "receipts": collection_receipts,
                        "expected_tasks": (),
                        "retained_or_reviewable_count": reviewable_count,
                        "unaccounted": unaccounted,
                        "source_refs": tuple(dict.fromkeys((*base.source_refs, *collection_refs))),
                        "material_degradation": collection_degradation,
                        "notes": "Care Line authoritative collection manifest certifies terminal accounting.",
                    }
                )
            else:
                scheduler_receipts = _load_care_scheduler_receipts(runtime, observation_date)
                if scheduler_receipts:
                    degradation = collection_degradation or MaterialDegradation(
                        material=True,
                        reason_codes=(GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE,),
                        explanation="Care scheduler receipts exist, but authoritative collection/review evidence is unavailable.",
                    )
                    base = EvaluationInput(
                        **{
                            **base.__dict__,
                            "receipts": scheduler_receipts,
                            "expected_tasks": (),
                            "source_refs": tuple(dict.fromkeys((*base.source_refs, *self._artifact_refs(scheduler_receipts), *collection_refs))),
                            "material_degradation": degradation,
                            "notes": degradation.explanation,
                        }
                    )
        status = _load_dispatch_status(runtime, self.dispatch, observation_date) if runtime is not None else None
        expected_instances = _elapsed_expected_instances(
            tuple(status.get("expected_instances") or ()) if status else (),
            evaluated_at=evaluated_at,
            expectations=self._expectation_by_task(),
        )
        if not expected_instances:
            return base
        expected_instance_tasks = {str(item.get("task_key") or "") for item in expected_instances}
        expected_tasks = tuple(task for task in base.expected_tasks if task not in expected_instance_tasks)
        notes = base.notes
        collection_receipts = [receipt for receipt in base.receipts if receipt.get("task_key") == "care_line_collection"]
        collection_instances = [item for item in expected_instances if item.get("task_key") == "care_line_collection"]
        if len(collection_receipts) == 1 and len(collection_instances) > 1:
            notes = "single Care collection receipt does not certify all expected daily instances"
        return EvaluationInput(**{**base.__dict__, "expected_tasks": expected_tasks, "expected_instances": expected_instances, "notes": notes})

    def _expectation_by_task(self) -> dict[str, TaskExpectation]:
        from bluefern_dispatches.operational_health import CARE_LINE_TASK_EXPECTATIONS

        return {item.task_key: item for item in CARE_LINE_TASK_EXPECTATIONS}


class IceCoverageAdapter(DispatchCoverageAdapter):
    dispatch = "ice"
    expected_tasks = ("ice_monitor",)

    def build_input(self, repo_root: Path, observation_date: str, *, evaluated_at: str, runtime_root: Path | None = None) -> EvaluationInput:
        base = super().build_input(repo_root, observation_date, evaluated_at=evaluated_at, runtime_root=runtime_root)
        if base.receipts or runtime_root is None:
            return base
        monitor_receipts = _load_ice_monitor_receipts(runtime_root, observation_date)
        if not monitor_receipts:
            return base
        return EvaluationInput(
            **{
                **base.__dict__,
                "receipts": monitor_receipts,
                "expected_tasks": self.expected_tasks,
                "source_refs": tuple(filter(None, (*base.recovery_candidate_refs, *self._artifact_refs(monitor_receipts)))),
                "material_degradation": self._material_degradation(monitor_receipts),
                "retained_or_reviewable_count": self._reviewable_count(monitor_receipts),
                "notes": None,
            }
        )

    def _material_degradation(self, receipts: Iterable[dict[str, Any]]) -> MaterialDegradation | None:
        for receipt in receipts:
            details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
            health = str(details.get("collection_health") or receipt.get("classification") or "")
            if receipt.get("status") == OperationalStatus.DEGRADED.value or health in {"collection_degraded", "limited_source_update"}:
                failures = int(details.get("failed_providers") or 0) if isinstance(details.get("failed_providers"), int) else 0
                if failures and details.get("critical_provider_failure") is True:
                    return MaterialDegradation(
                        material=True,
                        reason_codes=(GapReasonCode.CRITICAL_SOURCE_FAILURE,),
                        explanation="ICE receipt reports critical provider failure",
                    )
                return MaterialDegradation(material=False, alternate_coverage=True, explanation="ICE degraded receipt did not prove material coverage loss")
        return None


ADAPTERS: dict[str, DispatchCoverageAdapter] = {
    "food-line": FoodLineCoverageAdapter(),
    "care-line": CareLineCoverageAdapter(),
    "ice": IceCoverageAdapter(),
}


def evaluate_dispatch_date(
    repo_root: Path,
    dispatch: str,
    observation_date: str,
    *,
    evaluated_at: str | None = None,
    runtime_root: Path | None = None,
) -> CoverageEvaluation:
    adapter = ADAPTERS.get(dispatch)
    if adapter is None:
        raise CoverageGapError(f"unsupported dispatch: {dispatch}")
    evidence = adapter.build_input(repo_root, observation_date, evaluated_at=evaluated_at or utc_now(), runtime_root=runtime_root)
    return evaluate_observation(evidence)


def evaluate_range(
    repo_root: Path,
    *,
    dispatches: Iterable[str],
    start_date: str,
    end_date: str,
    evaluated_at: str | None = None,
    runtime_roots: dict[str, Path | None] | None = None,
) -> list[CoverageEvaluation]:
    rows = []
    roots = runtime_roots or {}
    for dispatch in dispatches:
        for observation_date in _dates(start_date, end_date):
            rows.append(
                evaluate_dispatch_date(
                    repo_root,
                    dispatch,
                    observation_date,
                    evaluated_at=evaluated_at,
                    runtime_root=roots.get(dispatch),
                )
            )
    return rows


def build_backfill_queue(evaluations: Iterable[CoverageEvaluation], *, evaluated_at: str) -> dict[str, Any]:
    unresolved = [row.to_queue_row(last_evaluated_at=evaluated_at) for row in evaluations if row.unresolved]
    unresolved.sort(key=lambda row: (row["criticality"], row["dispatch"], row["observation_date"]))
    return {
        "schema_version": BACKFILL_QUEUE_SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "publication_authorized": False,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "rows": unresolved,
        "summary": {
            "unresolved_count": len(unresolved),
            "critical": sum(1 for row in unresolved if row["criticality"] == Criticality.CRITICAL.value),
            "high": sum(1 for row in unresolved if row["criticality"] == Criticality.HIGH.value),
            "normal": sum(1 for row in unresolved if row["criticality"] == Criticality.NORMAL.value),
        },
    }


def write_backfill_queue(repo_root: Path, queue: dict[str, Any], *, path: Path = RUNTIME_QUEUE_PATH) -> Path:
    target = repo_root / path
    _write_json(target, queue)
    return target


def promote_durable_gap_record(repo_root: Path, evaluation: CoverageEvaluation, *, detected_at: str | None = None, recovered_at: str | None = None) -> Path:
    if evaluation.source == "controller" and not evaluation.reason_codes:
        raise CoverageGapError("refusing to promote unreasoned transient controller state")
    path = durable_gap_path(repo_root, evaluation.dispatch, evaluation.observation_date)
    _write_json(path, evaluation.to_record(detected_at=detected_at, recovered_at=recovered_at))
    return path


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate non-public coverage-gap and backfill obligations.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dispatch", choices=SUPPORTED_DISPATCHES)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--food-runtime-root")
    parser.add_argument("--care-runtime-root")
    parser.add_argument("--ice-runtime-root")
    parser.add_argument("--write-runtime-queue", action="store_true")
    args = parser.parse_args(argv)

    if args.date:
        start = end = args.date
    else:
        start = args.start_date
        end = args.end_date
    if not start or not end:
        parser.error("provide --date or --start-date/--end-date")

    repo_root = Path(args.repo_root).resolve()
    evaluated_at = utc_now()
    dispatches = SUPPORTED_DISPATCHES if args.all else (args.dispatch,)
    runtime_roots = {
        "food-line": Path(args.food_runtime_root).resolve() if args.food_runtime_root else None,
        "care-line": Path(args.care_runtime_root).resolve() if args.care_runtime_root else None,
        "ice": Path(args.ice_runtime_root).resolve() if args.ice_runtime_root else None,
    }
    evaluations = evaluate_range(
        repo_root,
        dispatches=dispatches,
        start_date=start,
        end_date=end,
        evaluated_at=evaluated_at,
        runtime_roots=runtime_roots,
    )
    queue = build_backfill_queue(evaluations, evaluated_at=evaluated_at)
    payload = {
        "schema_version": "bluefern.coverage_gap_controller.report.v1",
        "evaluated_at": evaluated_at,
        "publication_authorized": False,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "evaluations": [
            {
                "dispatch": row.dispatch,
                "observation_date": row.observation_date,
                "observation_status": row.observation_status.value,
                "backfill_status": row.backfill_status.value,
                "reason_codes": [reason.value for reason in row.reason_codes],
                "evidence_refs": list(row.evidence_refs),
                "recovered_event_ids": list(row.recovered_event_ids),
                "recovery_candidate_refs": list(row.recovery_candidate_refs),
                "observed_finding_count": row.observed_finding_count,
                "criticality": row.criticality.value,
                "operator_attention_required": row.operator_attention_required,
                "notes": row.notes,
                "source": row.source,
            }
            for row in evaluations
        ],
        "backfill_queue": queue,
    }
    if args.write_runtime_queue:
        payload["runtime_queue_path"] = str(write_backfill_queue(repo_root, queue))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
