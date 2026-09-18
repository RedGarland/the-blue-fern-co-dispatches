from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from bluefern_dispatches.coverage_gap_controller import (
    BackfillStatus,
    CoverageEvaluation,
    Criticality,
    GapReasonCode,
    ObservationStatus,
    RecoveryInvestigationStatus,
    TransitionRecord,
    evaluate_dispatch_date,
    load_durable_gap,
    promote_durable_gap_record,
)

try:
    from scripts.complete_food_line_historical_current_intake import run_replay as run_historical_current_intake_replay
except Exception:  # pragma: no cover
    run_historical_current_intake_replay = None  # type: ignore[assignment]

SCHEMA_VERSION = "bluefern_food_line_date_completeness_v1"
RECONSTRUCTION_SCHEMA_VERSION = "food_line_historical_reconstruction_v1"
RECONSTRUCTION_INPUT_SCHEMA_VERSION = "food_line_historical_reconstruction_input_v1"
DISPATCH = "food-line"
SOURCE_WATCH = "source_watch"
SOURCE_WATCH_RESUME = "source_watch_resume"
CURRENT_INTAKE = "current_intake"
DAILY_PUBLISH_DECISION = "daily_publish_decision"
COVERAGE_GAP_STATE = "coverage_gap_state"
HISTORICAL_RECOVERY_STATE = "historical_recovery_state"
STAGE_ORDER = (SOURCE_WATCH, SOURCE_WATCH_RESUME, CURRENT_INTAKE, DAILY_PUBLISH_DECISION, COVERAGE_GAP_STATE, HISTORICAL_RECOVERY_STATE)
OK = {"SUCCESS", "SAFE_NO_OP"}
BAD = {"FAILED", "MISSED", "STALE_OBSERVABILITY", "UPSTREAM_BLOCKED"}
DISPOSITIONS = {"RETAINED_FOR_REVIEW", "DUPLICATE_EXISTING", "ALREADY_PUBLISHED", "EXCLUDED_RULE", "INSUFFICIENT_EVIDENCE", "OUTSIDE_DATE", "OUTSIDE_SCOPE"}


class DateReconciliationError(ValueError):
    pass


class StageStatus(StrEnum):
    PRESENT_VALID = "PRESENT_VALID"
    SAFE_NO_OP = "SAFE_NO_OP"
    MISSING = "MISSING"
    INCOMPLETE = "INCOMPLETE"
    CONTRADICTORY = "CONTRADICTORY"
    REPAIRED = "REPAIRED"
    RECONSTRUCTED = "RECONSTRUCTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EVIDENCE_EXHAUSTED = "EVIDENCE_EXHAUSTED"


class DateState(StrEnum):
    COMPLETE_ORIGINAL = "COMPLETE_ORIGINAL"
    COMPLETE_REPAIRED = "COMPLETE_REPAIRED"
    COMPLETE_RECONSTRUCTED = "COMPLETE_RECONSTRUCTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EVIDENCE_EXHAUSTED = "EVIDENCE_EXHAUSTED"
    BLOCKED_ERROR = "BLOCKED_ERROR"


@dataclass(frozen=True)
class EvidenceRef:
    path: str
    sha256: str | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {"path": self.path, "sha256": self.sha256, "kind": self.kind}.items() if v is not None}


@dataclass
class StageResult:
    stage: str
    status: StageStatus
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    run_id: str | None = None
    timestamps: dict[str, str] = field(default_factory=dict)
    original_vs_reconstructed: str = "original"
    repair_action: str | None = None
    unresolved_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "run_id": self.run_id,
            "timestamps": self.timestamps,
            "original_vs_reconstructed": self.original_vs_reconstructed,
            "repair_action": self.repair_action,
            "unresolved_reason": self.unresolved_reason,
            "details": self.details,
        }


@dataclass(frozen=True)
class ReconstructionFinding:
    candidate_id: str
    disposition: str
    canonical_url: str | None = None
    source_published_at: str | None = None
    event_date: str | None = None
    pressure_type: str | None = None
    geography: str | None = None
    supporting_passage: str | None = None
    title: str | None = None
    confidence: str | None = None
    duplicate_of: str | None = None
    exclusion_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class ReconstructionResult:
    findings: tuple[ReconstructionFinding, ...] = ()
    queries_attempted: tuple[str, ...] = ()
    sources_attempted: tuple[str, ...] = ()
    sources_succeeded: tuple[str, ...] = ()
    sources_failed: tuple[str, ...] = ()
    coverage_by_source_family: dict[str, str] = field(default_factory=dict)
    coverage_by_pressure_type: dict[str, str] = field(default_factory=dict)
    coverage_by_geography: dict[str, str] = field(default_factory=dict)
    coverage_sufficient: bool = False
    limitations: tuple[str, ...] = ()
    research_mode: str | None = None
    network_access: bool | None = None
    researched_at: str | None = None
    source_path: Path | None = None
    source_sha256: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DateReconciliationError(f"invalid --date {value!r}; expected YYYY-MM-DD") from exc
    if parsed > date.today():
        raise DateReconciliationError(f"future dates are not reconcilable: {value}")
    return parsed


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _ref(root: Path, path: Path, kind: str | None = None) -> EvidenceRef:
    return EvidenceRef(_rel(root, path), _sha256(path) if path.is_file() else None, kind)


def _source_head(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    except Exception:
        return None
    return out.stdout.strip() or None


def _receipt_rows(root: Path, target_date: str) -> list[tuple[Path, dict[str, Any]]]:
    runs = root / "status" / "operational-health" / DISPATCH / target_date / "runs"
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(runs.glob("*.json")) if runs.exists() else []:
        try:
            payload = _read_json(path)
            if not isinstance(payload, dict):
                payload = {"_malformed": "receipt is not an object", "status": "FAILED"}
        except Exception as exc:
            payload = {"_malformed": str(exc), "status": "FAILED"}
        rows.append((path, payload))
    return rows


def _latest(rows: list[tuple[Path, dict[str, Any]]], task_key: str) -> tuple[Path, dict[str, Any]] | None:
    filtered = [row for row in rows if str(row[1].get("task_key") or "") == task_key]
    if not filtered:
        return None
    return sorted(filtered, key=lambda row: str(row[1].get("completed_at") or row[1].get("receipt_created_at") or row[0].name))[-1]


def _payload_date(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    window = payload.get("search_window") if isinstance(payload.get("search_window"), dict) else {}
    return str(payload.get("edition_date") or payload.get("date") or window.get("edition_date") or "")


def _json_files(base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    return sorted(path for path in base.rglob("*.json") if path.is_file()) if base.exists() else []


def _agent_handoffs(root: Path, target_date: str) -> list[Path]:
    paths: list[Path] = []
    for path in _json_files(root / "data" / "dispatches" / "food-line" / "agent-inbox"):
        if "processed" in path.parts:
            continue
        try:
            payload = _read_json(path)
        except Exception:
            continue
        if _payload_date(payload) == target_date:
            paths.append(path)
    return paths


def _has_qualifying_handoff(root: Path, target_date: str) -> bool:
    for path in _agent_handoffs(root, target_date):
        try:
            payload = _read_json(path)
        except Exception:
            continue
        findings = payload.get("findings") if isinstance(payload, dict) else None
        if isinstance(findings, list) and findings:
            return True
        if int(payload.get("qualifying_count") or payload.get("retained_count") or 0) > 0:
            return True
    return False


def _retained_source_run(root: Path, target_date: str) -> tuple[str | None, list[EvidenceRef], str | None]:
    run_root = root / "data" / "dispatches" / "food-line" / "discovery-runs" / target_date
    if not run_root.exists():
        return None, [], "missing discovery-runs directory"
    dirs = sorted(path for path in run_root.iterdir() if path.is_dir())
    valid = [path for path in dirs if (path / "run-state.json").is_file() and (path / "query-plan.json").is_file()]
    refs = [_ref(root, path, "source_watch_run_dir") for path in dirs]
    if len(valid) != 1:
        return None, refs, f"expected exactly one retained source-watch run; found {len(valid)}"
    run = valid[0]
    refs = [_ref(root, run / "run-state.json", "run_state"), _ref(root, run / "query-plan.json", "query_plan")]
    candidates = root / "data" / "dispatches" / "food-line" / "discovery" / target_date / "discovery_candidates.json"
    if candidates.is_file():
        refs.append(_ref(root, candidates, "discovery_candidates"))
    run_id = run.name
    try:
        payload = _read_json(run / "run-state.json")
        run_id = str(payload.get("run_id") or run_id)
    except Exception:
        pass
    return run_id, refs, None


def _receipt_stage(root: Path, stage: str, receipt: tuple[Path, dict[str, Any]] | None) -> StageResult:
    if receipt is None:
        return StageResult(stage, StageStatus.MISSING, unresolved_reason="missing operational-health receipt")
    path, payload = receipt
    if payload.get("_malformed"):
        return StageResult(stage, StageStatus.CONTRADICTORY, [_ref(root, path, "malformed_receipt")], unresolved_reason=str(payload["_malformed"]))
    status = str(payload.get("status") or "")
    if status == "SAFE_NO_OP":
        stage_status = StageStatus.SAFE_NO_OP
    elif status == "SUCCESS":
        stage_status = StageStatus.PRESENT_VALID
    elif status in BAD:
        stage_status = StageStatus.INCOMPLETE
    else:
        stage_status = StageStatus.INCOMPLETE
    return StageResult(
        stage,
        stage_status,
        [_ref(root, path, "operational_health_receipt")],
        run_id=str(payload.get("run_id") or "") or None,
        timestamps={key: str(payload[key]) for key in ("started_at", "completed_at", "observed_at", "receipt_created_at") if payload.get(key)},
        unresolved_reason=None if stage_status in {StageStatus.PRESENT_VALID, StageStatus.SAFE_NO_OP} else f"terminal status {status or 'missing'}",
        details={"task_key": payload.get("task_key"), "status": status, "classification": payload.get("classification")},
    )


def _source_watch_stage(root: Path, target_date: str, rows: list[tuple[Path, dict[str, Any]]]) -> StageResult:
    stage = _receipt_stage(root, SOURCE_WATCH, _latest(rows, "food_line_source_watch"))
    run_id, refs, problem = _retained_source_run(root, target_date)
    stage.evidence_refs.extend(refs)
    if not stage.run_id:
        stage.run_id = run_id
    elif run_id and stage.run_id != run_id:
        stage.status = StageStatus.CONTRADICTORY
        stage.unresolved_reason = f"source-watch run_id mismatch: receipt={stage.run_id} retained={run_id}"
    if stage.status in {StageStatus.PRESENT_VALID, StageStatus.SAFE_NO_OP} and problem:
        stage.status = StageStatus.INCOMPLETE
        stage.unresolved_reason = problem
    if stage.status == StageStatus.MISSING and problem:
        stage.unresolved_reason = problem
    handoffs = _agent_handoffs(root, target_date)
    stage.evidence_refs.extend(_ref(root, path, "source_watch_handoff") for path in handoffs)
    stage.details["handoff_count"] = len(handoffs)
    stage.details["qualifying_handoff_present"] = _has_qualifying_handoff(root, target_date)
    return stage


def _current_intake_refs(root: Path, target_date: str) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    targets = [
        root / "data" / "dispatches" / "food-line" / "review" / "reports" / target_date / "current-intake.json",
        root / "data" / "dispatches" / "food-line" / "agent-intake" / target_date,
        root / "data" / "dispatches" / "food-line" / "historical-intake" / target_date / "replay-receipt.json",
        root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{target_date}.json",
    ]
    for target in targets:
        for path in _json_files(target):
            refs.append(_ref(root, path, "current_intake"))
    return refs


def _current_intake_stage(root: Path, target_date: str, rows: list[tuple[Path, dict[str, Any]]], source_watch: StageResult) -> StageResult:
    stage = _receipt_stage(root, CURRENT_INTAKE, _latest(rows, "food_line_current_intake"))
    refs = _current_intake_refs(root, target_date)
    stage.evidence_refs.extend(refs)
    if not source_watch.details.get("qualifying_handoff_present") and stage.status == StageStatus.MISSING:
        stage.status = StageStatus.NOT_APPLICABLE
        stage.unresolved_reason = None
        stage.details["basis"] = "no qualifying Source Watch handoff found"
    elif refs and stage.status == StageStatus.MISSING:
        stage.status = StageStatus.REPAIRED if any("historical-intake" in ref.path for ref in refs) else StageStatus.PRESENT_VALID
        stage.original_vs_reconstructed = "repaired" if stage.status == StageStatus.REPAIRED else "original"
        stage.repair_action = "historical_current_intake_replay" if stage.status == StageStatus.REPAIRED else None
        stage.unresolved_reason = None
    return stage


def _historical_paths(root: Path, target_date: str) -> list[Path]:
    paths: list[Path] = []
    for target in (
        root / "data" / "dispatches" / "food-line" / "historical-intake" / target_date,
        root / "data" / "dispatches" / "food-line" / "historical-events" / target_date,
        root / "data" / "dispatches" / "food-line" / "historical-reconstruction" / target_date,
    ):
        paths.extend(_json_files(target))
    gap = root / "data" / "dispatches" / "food-line" / "coverage-gaps" / f"{target_date}.json"
    if gap.is_file():
        paths.append(gap)
    archive = root / "data" / "agent-history" / "food-line"
    for path in _json_files(archive):
        if target_date in path.as_posix():
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _reconstruction_record(root: Path, target_date: str) -> dict[str, Any] | None:
    path = root / "data" / "dispatches" / "food-line" / "historical-reconstruction" / target_date / "reconstruction.json"
    if not path.is_file():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != RECONSTRUCTION_SCHEMA_VERSION:
        raise DateReconciliationError(f"invalid historical reconstruction record: {path}")
    return payload


def _history_stage(root: Path, target_date: str) -> StageResult:
    paths = _historical_paths(root, target_date)
    refs = [_ref(root, path, "historical_evidence") for path in paths if path.is_file()]
    reconstruction = _reconstruction_record(root, target_date)
    if reconstruction:
        retained = [item for item in reconstruction.get("findings", []) if item.get("disposition") == "RETAINED_FOR_REVIEW"]
        status = StageStatus.REVIEW_REQUIRED if retained else StageStatus.RECONSTRUCTED if reconstruction.get("coverage_sufficient") else StageStatus.EVIDENCE_EXHAUSTED
        return StageResult(
            HISTORICAL_RECOVERY_STATE,
            status,
            refs,
            original_vs_reconstructed="reconstructed",
            unresolved_reason="reconstructed candidates await review" if retained else None if status == StageStatus.RECONSTRUCTED else "historical reconstruction coverage is insufficient",
            details={"candidate_count": len(retained), "coverage_sufficient": reconstruction.get("coverage_sufficient")},
        )
    pending = []
    for path in paths:
        try:
            text = json.dumps(_read_json(path), sort_keys=True).lower()
        except Exception:
            continue
        if "pending_review" in text or "retained_for_review" in text or "review_required" in text:
            pending.append(path)
    if pending:
        return StageResult(HISTORICAL_RECOVERY_STATE, StageStatus.REVIEW_REQUIRED, refs, original_vs_reconstructed="repaired", unresolved_reason="historical candidates await editorial disposition", details={"candidate_count": len(pending)})
    if refs:
        return StageResult(HISTORICAL_RECOVERY_STATE, StageStatus.REPAIRED, refs, original_vs_reconstructed="repaired")
    return StageResult(HISTORICAL_RECOVERY_STATE, StageStatus.NOT_APPLICABLE)


def _coverage_stage(root: Path, target_date: str, evaluated_at: str) -> tuple[StageResult, Any | None]:
    try:
        evaluation = evaluate_dispatch_date(root, DISPATCH, target_date, evaluated_at=evaluated_at, runtime_root=root)
    except Exception as exc:
        return StageResult(COVERAGE_GAP_STATE, StageStatus.CONTRADICTORY, unresolved_reason=str(exc)), None
    if evaluation.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED:
        status = StageStatus.SAFE_NO_OP
    elif evaluation.backfill_status == BackfillStatus.RECOVERED:
        status = StageStatus.REPAIRED
    elif evaluation.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW:
        status = StageStatus.REVIEW_REQUIRED
    elif evaluation.recovery_investigation_status == RecoveryInvestigationStatus.EVIDENCE_EXHAUSTED:
        status = StageStatus.EVIDENCE_EXHAUSTED
    else:
        status = StageStatus.INCOMPLETE
    return StageResult(
        COVERAGE_GAP_STATE,
        status,
        [EvidenceRef(path=ref, kind="coverage_gap_evidence") for ref in evaluation.evidence_refs],
        unresolved_reason=None if status in {StageStatus.SAFE_NO_OP, StageStatus.REPAIRED} else ";".join(reason.value for reason in evaluation.reason_codes),
        details={
            "observation_status": evaluation.observation_status.value,
            "backfill_status": evaluation.backfill_status.value,
            "reason_codes": [reason.value for reason in evaluation.reason_codes],
            "recovered_event_ids": list(evaluation.recovered_event_ids),
            "observed_finding_count": evaluation.observed_finding_count,
            "recovery_investigation_status": evaluation.recovery_investigation_status.value,
        },
    ), evaluation


def audit_food_line_date(root: Path, target_date: str, *, evaluated_at: str | None = None) -> tuple[dict[str, StageResult], Any | None]:
    parse_date(target_date)
    evaluated_at = evaluated_at or utc_now()
    rows = _receipt_rows(root, target_date)
    source_watch = _source_watch_stage(root, target_date, rows)
    resume = _receipt_stage(root, SOURCE_WATCH_RESUME, _latest(rows, "food_line_source_watch_resume"))
    if resume.status == StageStatus.MISSING:
        resume.status = StageStatus.NOT_APPLICABLE
        resume.unresolved_reason = None
        resume.details["basis"] = "resume not required unless Source Watch blocks or defers"
    publish = _receipt_stage(root, DAILY_PUBLISH_DECISION, _latest(rows, "food_line_daily_publish"))
    if publish.status in {StageStatus.PRESENT_VALID, StageStatus.SAFE_NO_OP}:
        publish.details["publication_decision"] = publish.details.get("classification") or publish.details.get("status")
    coverage, evaluation = _coverage_stage(root, target_date, evaluated_at)
    current = _current_intake_stage(root, target_date, rows, source_watch)
    core = (source_watch, resume, current, publish)
    if coverage.status == StageStatus.INCOMPLETE and all(stage.status in {StageStatus.PRESENT_VALID, StageStatus.SAFE_NO_OP, StageStatus.NOT_APPLICABLE, StageStatus.REPAIRED} for stage in core):
        coverage.status = StageStatus.SAFE_NO_OP
        coverage.unresolved_reason = None
        coverage.details["backfill_status"] = "BACKFILL_NOT_REQUIRED"
        coverage.details["controller_override"] = "core_date_stages_accounted_for"
    stages = {
        SOURCE_WATCH: source_watch,
        SOURCE_WATCH_RESUME: resume,
        CURRENT_INTAKE: current,
        DAILY_PUBLISH_DECISION: publish,
        COVERAGE_GAP_STATE: coverage,
        HISTORICAL_RECOVERY_STATE: _history_stage(root, target_date),
    }
    return stages, evaluation


def _state(stages: dict[str, StageResult]) -> DateState:
    statuses = {stage.status for stage in stages.values()}
    if StageStatus.CONTRADICTORY in statuses:
        return DateState.BLOCKED_ERROR
    if stages[HISTORICAL_RECOVERY_STATE].status == StageStatus.REVIEW_REQUIRED:
        return DateState.REVIEW_REQUIRED
    if stages[HISTORICAL_RECOVERY_STATE].status == StageStatus.RECONSTRUCTED:
        return DateState.COMPLETE_RECONSTRUCTED
    if stages[HISTORICAL_RECOVERY_STATE].status == StageStatus.EVIDENCE_EXHAUSTED:
        return DateState.EVIDENCE_EXHAUSTED
    if StageStatus.REVIEW_REQUIRED in statuses:
        return DateState.REVIEW_REQUIRED
    if StageStatus.EVIDENCE_EXHAUSTED in statuses:
        return DateState.EVIDENCE_EXHAUSTED
    if any(status in statuses for status in (StageStatus.MISSING, StageStatus.INCOMPLETE)):
        if stages[SOURCE_WATCH].status in {StageStatus.MISSING, StageStatus.INCOMPLETE}:
            return DateState.EVIDENCE_EXHAUSTED
        return DateState.BLOCKED_ERROR
    if StageStatus.REPAIRED in statuses:
        return DateState.COMPLETE_REPAIRED
    return DateState.COMPLETE_ORIGINAL


def _replay_available(root: Path, target_date: str, stages: dict[str, StageResult]) -> tuple[bool, str | None]:
    if stages[SOURCE_WATCH].status not in {StageStatus.PRESENT_VALID, StageStatus.SAFE_NO_OP}:
        return False, None
    if stages[CURRENT_INTAKE].status not in {StageStatus.MISSING, StageStatus.INCOMPLETE}:
        return False, None
    if not stages[SOURCE_WATCH].details.get("qualifying_handoff_present"):
        return False, None
    if not (root / "data" / "dispatches" / "food-line" / "discovery" / target_date / "discovery_candidates.json").is_file():
        return False, None
    return True, stages[SOURCE_WATCH].run_id


def _validate_reconstruction_findings(findings: tuple[ReconstructionFinding, ...], target_date: str) -> None:
    seen: set[str] = set()
    for finding in findings:
        if finding.disposition not in DISPOSITIONS:
            raise DateReconciliationError(f"unsupported reconstruction disposition: {finding.disposition}")
        if not finding.candidate_id:
            raise DateReconciliationError("reconstruction finding missing candidate_id")
        if finding.candidate_id in seen:
            raise DateReconciliationError(f"duplicate reconstruction candidate_id: {finding.candidate_id}")
        seen.add(finding.candidate_id)
        if finding.event_date and finding.event_date != target_date and finding.disposition not in {"OUTSIDE_DATE", "DUPLICATE_EXISTING", "ALREADY_PUBLISHED"}:
            raise DateReconciliationError(f"candidate {finding.candidate_id} has contradictory event_date {finding.event_date}")
        if finding.source_published_at:
            parse_date(finding.source_published_at[:10])


def _reconstruction_packet_path(root: Path, target_date: str) -> Path:
    return root / "data" / "dispatches" / "food-line" / "historical-reconstruction-inputs" / f"{target_date}.json"


def _default_reconstruction_provider(root: Path, target_date: str) -> ReconstructionResult | None:
    packet = _reconstruction_packet_path(root, target_date)
    if not packet.is_file():
        return None
    payload = _read_json(packet)
    if not isinstance(payload, dict):
        raise DateReconciliationError(f"historical reconstruction input is not an object: {packet}")
    if payload.get("schema_version") != RECONSTRUCTION_INPUT_SCHEMA_VERSION:
        raise DateReconciliationError(f"unsupported historical reconstruction input schema: {payload.get('schema_version')}")
    if payload.get("target_date") != target_date:
        raise DateReconciliationError(f"historical reconstruction input target_date mismatch: {payload.get('target_date')} != {target_date}")
    parse_date(str(payload["target_date"]))
    required = {
        "researched_at",
        "research_mode",
        "network_access",
        "queries_attempted",
        "sources_attempted",
        "sources_succeeded",
        "sources_failed",
        "coverage_by_source_family",
        "coverage_by_pressure_type",
        "coverage_by_geography",
        "coverage_sufficient",
        "findings",
        "limitations",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise DateReconciliationError(f"historical reconstruction input missing required fields: {missing}")
    if payload["research_mode"] != "codex_bounded_historical_web_research":
        raise DateReconciliationError(f"unsupported historical research_mode: {payload['research_mode']}")
    if not isinstance(payload["network_access"], bool):
        raise DateReconciliationError("historical reconstruction input network_access must be boolean")
    if not isinstance(payload.get("findings"), list):
        raise DateReconciliationError("historical reconstruction input findings must be a list")
    findings = tuple(ReconstructionFinding(**item) for item in payload.get("findings", []))
    _validate_reconstruction_findings(findings, target_date)
    return ReconstructionResult(
        findings=findings,
        queries_attempted=tuple(payload.get("queries_attempted") or ()),
        sources_attempted=tuple(payload.get("sources_attempted") or ()),
        sources_succeeded=tuple(payload.get("sources_succeeded") or ()),
        sources_failed=tuple(payload.get("sources_failed") or ()),
        coverage_by_source_family=dict(payload.get("coverage_by_source_family") or {}),
        coverage_by_pressure_type=dict(payload.get("coverage_by_pressure_type") or {}),
        coverage_by_geography=dict(payload.get("coverage_by_geography") or {}),
        coverage_sufficient=bool(payload.get("coverage_sufficient")),
        limitations=tuple(payload.get("limitations") or ()),
        research_mode=str(payload.get("research_mode")),
        network_access=bool(payload.get("network_access")),
        researched_at=str(payload.get("researched_at")),
        source_path=packet,
        source_sha256=_sha256(packet),
    )

def _source_registry(root: Path) -> dict[str, Any]:
    for path in (
        root / "data" / "dispatches" / "food-line" / "source_registry.json",
        root / "data" / "dispatches" / "food-line" / "sources" / "source_registry.json",
        root / "data" / "source_registry" / "food_line_sources.json",
    ):
        if path.is_file():
            return _ref(root, path, "source_registry").to_dict()
    return {"status": "not_found"}


def _archive_research_input(root: Path, target_date: str, result: ReconstructionResult) -> dict[str, Any] | None:
    if result.source_path is None:
        return None
    source_sha = result.source_sha256 or _sha256(result.source_path)
    target = root / "data" / "dispatches" / "food-line" / "historical-reconstruction" / target_date / "research-input.json"
    source_bytes = result.source_path.read_bytes()
    if target.exists():
        existing_sha = _sha256(target)
        if existing_sha == source_sha:
            return {
                "path": _rel(root, target),
                "sha256": existing_sha,
                "original_input_path": _rel(root, result.source_path),
                "target_date": target_date,
                "researched_at": result.researched_at,
                "research_mode": result.research_mode,
                "idempotent_noop": True,
            }
        raise DateReconciliationError(f"conflicting historical research packet for {target_date}; existing archive differs")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(source_bytes)
    tmp.replace(target)
    return {
        "path": _rel(root, target),
        "sha256": _sha256(target),
        "original_input_path": _rel(root, result.source_path),
        "target_date": target_date,
        "researched_at": result.researched_at,
        "research_mode": result.research_mode,
        "idempotent_noop": False,
    }


def _write_reconstruction(root: Path, target_date: str, result: ReconstructionResult, evaluated_at: str, source_head: str | None) -> Path:
    _validate_reconstruction_findings(result.findings, target_date)
    seen: set[str] = set()
    findings = []
    counts = {key: 0 for key in sorted(DISPOSITIONS)}
    for finding in result.findings:
        seen.add(finding.candidate_id)
        counts[finding.disposition] += 1
        findings.append(finding.to_dict())
    if len(findings) != sum(counts.values()):
        raise DateReconciliationError("historical reconstruction terminal accounting mismatch")
    out = root / "data" / "dispatches" / "food-line" / "historical-reconstruction" / target_date / "reconstruction.json"
    research_archive = _archive_research_input(root, target_date, result)
    network_access = result.network_access if result.network_access is not None else False
    research_mode = result.research_mode or "operator_supplied_reconstruction_result"
    researched_at = result.researched_at
    payload = {
        "schema_version": RECONSTRUCTION_SCHEMA_VERSION,
        "target_date": target_date,
        "reconstructed_at": evaluated_at,
        "source_head": source_head,
        "reconstruction_mode": "nonoriginal_historical_research",
        "research_mode": research_mode,
        "researched_at": researched_at,
        "original_source_watch_present": False,
        "network_access": network_access,
        "research_input": research_archive,
        "source_registry": _source_registry(root),
        "query_plan": {"target_date": target_date, "date_bounded": True},
        "queries_attempted": list(result.queries_attempted),
        "fetch_outcomes": {"sources_attempted": list(result.sources_attempted), "sources_succeeded": list(result.sources_succeeded), "sources_failed": list(result.sources_failed)},
        "sources_discovered": sorted({f.canonical_url for f in result.findings if f.canonical_url}),
        "findings": findings,
        "disposition_counts": counts,
        "discovered_count": len(findings),
        "unaccounted": 0,
        "coverage_by_source_family": result.coverage_by_source_family,
        "coverage_by_pressure_type": result.coverage_by_pressure_type,
        "coverage_by_geography": result.coverage_by_geography,
        "coverage_sufficient": result.coverage_sufficient,
        "historical_zero_basis": "bounded coverage attempted with zero retained qualifying findings" if not findings and result.coverage_sufficient else None,
        "limitations": list(result.limitations),
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
    }
    if out.exists():
        existing = _read_json(out)
        comparable = {key: existing.get(key) for key in payload}
        if comparable != payload:
            raise DateReconciliationError(f"conflicting historical reconstruction record for {target_date}")
        return out
    _write_json(out, payload)
    retained = [item for item in findings if item.get("disposition") == "RETAINED_FOR_REVIEW"]
    if retained:
        _write_json(out.parent / "review" / "candidates.json", {"schema_version": "food_line_historical_reconstruction_review_v1", "target_date": target_date, "created_at": evaluated_at, "publication_eligible": False, "approval": False, "publication_approval": False, "pages_authorized": False, "candidates": retained})
    return out


def _verify_existing_research_packet(root: Path, target_date: str) -> None:
    packet = _reconstruction_packet_path(root, target_date)
    archived = root / "data" / "dispatches" / "food-line" / "historical-reconstruction" / target_date / "research-input.json"
    if packet.is_file() and archived.is_file() and _sha256(packet) != _sha256(archived):
        raise DateReconciliationError(f"conflicting historical research packet for {target_date}; existing archive differs")
def _prior_record(root: Path, target_date: str) -> dict[str, Any] | None:
    path = root / "data" / "dispatches" / "food-line" / "date-reconciliation" / f"{target_date}.json"
    if not path.is_file():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise DateReconciliationError(f"invalid date reconciliation record: {path}")
    return payload


def _coverage_metrics(root: Path, target_date: str) -> dict[str, Any]:
    reconstruction = _reconstruction_record(root, target_date)
    if not reconstruction:
        return {}
    fetch = reconstruction.get("fetch_outcomes") or {}
    return {
        "queries_attempted": len(reconstruction.get("queries_attempted") or []),
        "sources_attempted": len(fetch.get("sources_attempted") or []),
        "sources_succeeded": len(fetch.get("sources_succeeded") or []),
        "sources_failed": len(fetch.get("sources_failed") or []),
        "coverage_by_source_family": reconstruction.get("coverage_by_source_family") or {},
        "coverage_by_pressure_type": reconstruction.get("coverage_by_pressure_type") or {},
        "coverage_by_geography": reconstruction.get("coverage_by_geography") or {},
        "coverage_sufficient": reconstruction.get("coverage_sufficient"),
    }


def _transition_history_from_gap(record: dict[str, Any] | None) -> tuple[TransitionRecord, ...]:
    rows: list[TransitionRecord] = []
    if not record:
        return ()
    for item in record.get("transition_history") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            TransitionRecord(
                previous_observation_status=item.get("previous_observation_status"),
                previous_backfill_status=item.get("previous_backfill_status"),
                new_observation_status=str(item.get("new_observation_status") or record.get("observation_status") or ""),
                new_backfill_status=str(item.get("new_backfill_status") or record.get("backfill_status") or ""),
                transition_at=str(item.get("transition_at") or record.get("recovered_at") or record.get("detected_at") or ""),
                reason_code=str(item.get("reason_code") or ""),
                evidence_refs=tuple(str(ref) for ref in (item.get("evidence_refs") or ()) if ref),
            )
        )
    return tuple(rows)


def _with_gap_transition(
    previous: dict[str, Any] | None,
    *,
    observation_status: ObservationStatus,
    backfill_status: BackfillStatus,
    evaluated_at: str,
    reason_code: str,
    evidence_refs: tuple[str, ...],
) -> tuple[TransitionRecord, ...]:
    history = _transition_history_from_gap(previous)
    if previous and previous.get("observation_status") == observation_status.value and previous.get("backfill_status") == backfill_status.value:
        return history
    return history + (
        TransitionRecord(
            previous_observation_status=str(previous.get("observation_status")) if previous else None,
            previous_backfill_status=str(previous.get("backfill_status")) if previous else None,
            new_observation_status=observation_status.value,
            new_backfill_status=backfill_status.value,
            transition_at=evaluated_at,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
        ),
    )


def _reconstruction_discovered_count(root: Path, target_date: str) -> int:
    reconstruction = _reconstruction_record(root, target_date)
    if not reconstruction:
        return 0
    return int(reconstruction.get("discovered_count") or len(reconstruction.get("findings") or []))


def _coverage_gap_promote(root: Path, target_date: str, state: DateState, stages: dict[str, StageResult], evaluated_at: str) -> str | None:
    refs = tuple(dict.fromkeys(ref.path for stage in stages.values() for ref in stage.evidence_refs))
    previous = load_durable_gap(root, DISPATCH, target_date)
    discovered = _reconstruction_discovered_count(root, target_date)
    recovered_ids = tuple(str(item) for item in (stages[COVERAGE_GAP_STATE].details.get("recovered_event_ids") or ()) if item)
    observed_count = discovered or (int(stages[COVERAGE_GAP_STATE].details.get("observed_finding_count") or 0) if stages[COVERAGE_GAP_STATE].details.get("observed_finding_count") is not None else None)
    investigation = RecoveryInvestigationStatus.ACTIVE
    investigation_reason = None
    recovered_at = evaluated_at if state in {DateState.COMPLETE_ORIGINAL, DateState.COMPLETE_REPAIRED, DateState.COMPLETE_RECONSTRUCTED} else None

    if state == DateState.COMPLETE_ORIGINAL:
        observation = ObservationStatus.OBSERVED_WITH_FINDINGS if observed_count else ObservationStatus.OBSERVED_ZERO_QUALIFYING
        backfill = BackfillStatus.BACKFILL_NOT_REQUIRED
        reason_codes: tuple[GapReasonCode, ...] = ()
        reason = "date_reconciliation_complete_original"
    elif state == DateState.COMPLETE_REPAIRED:
        observation = ObservationStatus.OBSERVED_WITH_FINDINGS if observed_count or recovered_ids else ObservationStatus.OBSERVED_ZERO_QUALIFYING
        backfill = BackfillStatus.RECOVERED
        reason_codes = (GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,)
        reason = GapReasonCode.HISTORICAL_RECOVERY_COMPLETED.value
    elif state == DateState.COMPLETE_RECONSTRUCTED:
        observation = ObservationStatus.OBSERVED_WITH_FINDINGS if discovered else ObservationStatus.OBSERVED_ZERO_QUALIFYING
        backfill = BackfillStatus.RECOVERED if discovered else BackfillStatus.BACKFILL_NOT_REQUIRED
        reason_codes = (GapReasonCode.HISTORICAL_RECOVERY_COMPLETED,) if discovered else ()
        reason = GapReasonCode.HISTORICAL_RECOVERY_COMPLETED.value if discovered else "historical_reconstructed_zero_complete"
    elif state == DateState.REVIEW_REQUIRED:
        observation = ObservationStatus.OBSERVATION_INCOMPLETE
        backfill = BackfillStatus.RECOVERY_IN_REVIEW
        reason_codes = (GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW,)
        reason = GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW.value
    elif state == DateState.EVIDENCE_EXHAUSTED:
        observation = ObservationStatus.OBSERVATION_INCOMPLETE
        backfill = BackfillStatus.BACKFILL_REQUIRED
        reason_codes = (GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE,)
        investigation = RecoveryInvestigationStatus.EVIDENCE_EXHAUSTED
        investigation_reason = "historical_evidence_exhausted"
        reason = investigation_reason
    else:
        return None

    evaluation = CoverageEvaluation(
        dispatch=DISPATCH,
        observation_date=target_date,
        observation_status=observation,
        backfill_status=backfill,
        reason_codes=reason_codes,
        evidence_refs=refs,
        recovered_event_ids=recovered_ids,
        observed_finding_count=observed_count,
        criticality=Criticality.HIGH if backfill in {BackfillStatus.BACKFILL_REQUIRED, BackfillStatus.RECOVERY_IN_REVIEW} else Criticality.NORMAL,
        operator_attention_required=backfill in {BackfillStatus.BACKFILL_REQUIRED, BackfillStatus.RECOVERY_IN_REVIEW},
        transition_history=_with_gap_transition(previous, observation_status=observation, backfill_status=backfill, evaluated_at=evaluated_at, reason_code=reason, evidence_refs=refs),
        notes=f"Food Line date reconciliation state {state.value}.",
        source="food_line_date_reconciliation",
        recovery_investigation_status=investigation,
        recovery_investigation_reason_code=investigation_reason,
        recovered_at=recovered_at,
    )
    path = promote_durable_gap_record(root, evaluation, detected_at=evaluated_at, recovered_at=recovered_at)
    return _rel(root, path)

def _next_action(state: DateState, repair_available: list[str], stages: dict[str, StageResult]) -> str:
    if state in {DateState.COMPLETE_ORIGINAL, DateState.COMPLETE_REPAIRED, DateState.COMPLETE_RECONSTRUCTED}:
        return "none"
    if state == DateState.REVIEW_REQUIRED:
        return "review_reconstructed_candidates"
    if repair_available:
        return "rerun_with_apply"
    if state == DateState.EVIDENCE_EXHAUSTED:
        return "perform_bounded_historical_research"
    return next((stage.unresolved_reason for stage in stages.values() if stage.unresolved_reason), "resolve_blocked_error")


def _build_record(root: Path, target_date: str, *, mode: str, evaluated_at: str, stages: dict[str, StageResult], state: DateState, repair_available: list[str], repair_performed: list[str], reconstruction_performed: bool, prior: dict[str, Any] | None, source_head: str | None, coverage_gap_record: str | None) -> dict[str, Any]:
    complete = state in {DateState.COMPLETE_ORIGINAL, DateState.COMPLETE_REPAIRED, DateState.COMPLETE_RECONSTRUCTED}
    unresolved = sum(1 for stage in stages.values() if stage.status in {StageStatus.MISSING, StageStatus.INCOMPLETE, StageStatus.CONTRADICTORY, StageStatus.REVIEW_REQUIRED, StageStatus.EVIDENCE_EXHAUSTED})
    candidates = sum(int(stage.details.get("candidate_count") or 0) for stage in stages.values())
    transitions = list(prior.get("transition_history") or []) if prior else []
    transition = {"transition_at": evaluated_at, "previous_state": prior.get("state") if prior else None, "new_state": state.value, "mode": mode, "repair_actions_performed": repair_performed, "network_reconstruction_performed": reconstruction_performed}
    if not transitions or transitions[-1] != transition:
        transitions.append(transition)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": target_date,
        "evaluated_at": evaluated_at,
        "mode": mode,
        "state": state.value,
        "date_complete": complete,
        "original_evidence_complete": state == DateState.COMPLETE_ORIGINAL,
        "original_vs_repaired_vs_reconstructed": state.value.removeprefix("COMPLETE_").lower() if complete else "incomplete",
        "stages": {stage: stages[stage].to_dict() for stage in STAGE_ORDER},
        "repair_actions_available": repair_available,
        "repair_actions_performed": repair_performed,
        "network_reconstruction_performed": reconstruction_performed,
        "candidate_count": candidates,
        "unresolved_count": unresolved,
        "publication_review_required": state == DateState.REVIEW_REQUIRED,
        "coverage_gap_status": stages[COVERAGE_GAP_STATE].details.get("backfill_status") or stages[COVERAGE_GAP_STATE].status.value,
        "coverage_gap_record": coverage_gap_record,
        "evidence_exhausted": state == DateState.EVIDENCE_EXHAUSTED,
        "historical_coverage_metrics": _coverage_metrics(root, target_date),
        "limitations": [stage.unresolved_reason for stage in stages.values() if stage.unresolved_reason],
        "source_head": source_head,
        "transition_history": transitions,
        "publication_authorized": False,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "next_action": _next_action(state, repair_available, stages),
    }


def reconcile_food_line_date(root: Path, target_date: str, *, apply: bool = False, evaluated_at: str | None = None, reconstruction_provider: Callable[[Path, str], ReconstructionResult | None] | None = None) -> dict[str, Any]:
    parse_date(target_date)
    root = root.resolve()
    evaluated_at = evaluated_at or utc_now()
    source_head = _source_head(root)
    prior = _prior_record(root, target_date)
    if apply:
        _verify_existing_research_packet(root, target_date)
    stages, _ = audit_food_line_date(root, target_date, evaluated_at=evaluated_at)
    repair_available: list[str] = []
    repair_performed: list[str] = []
    reconstruction_performed = False
    replay_available, replay_run_id = _replay_available(root, target_date, stages)
    if replay_available:
        repair_available.append("historical_current_intake_replay")
    if parse_date(target_date) == date.today() and stages[COVERAGE_GAP_STATE].status in {StageStatus.INCOMPLETE, StageStatus.REVIEW_REQUIRED}:
        repair_available.append("same_day_recovery_available")
    if apply and replay_available:
        if run_historical_current_intake_replay is None:
            raise DateReconciliationError("historical current-intake replay helper is unavailable")
        run_historical_current_intake_replay(root, historical_date=target_date, inbox=root / "data" / "dispatches" / "food-line" / "agent-inbox", source_watch_run_id=replay_run_id, replayed_at=evaluated_at)
        repair_performed.append("historical_current_intake_replay")
        stages, _ = audit_food_line_date(root, target_date, evaluated_at=evaluated_at)
    state = _state(stages)
    if apply and state == DateState.EVIDENCE_EXHAUSTED and stages[SOURCE_WATCH].status in {StageStatus.MISSING, StageStatus.INCOMPLETE}:
        provider = reconstruction_provider or _default_reconstruction_provider
        reconstruction_result = provider(root, target_date)
        if reconstruction_result is not None:
            reconstruction_path = _write_reconstruction(root, target_date, reconstruction_result, evaluated_at, source_head)
            repair_performed.append("historical_reconstruction")
            reconstruction_performed = True
            stages, _ = audit_food_line_date(root, target_date, evaluated_at=evaluated_at)
            stages[HISTORICAL_RECOVERY_STATE].evidence_refs.append(_ref(root, reconstruction_path, "historical_reconstruction"))
            state = _state(stages)
    coverage_gap_record = _coverage_gap_promote(root, target_date, state, stages, evaluated_at) if apply else None
    record = _build_record(root, target_date, mode="apply" if apply else "dry-run", evaluated_at=evaluated_at, stages=stages, state=state, repair_available=repair_available, repair_performed=repair_performed, reconstruction_performed=reconstruction_performed, prior=prior, source_head=source_head, coverage_gap_record=coverage_gap_record)
    if apply:
        _write_json(root / "data" / "dispatches" / "food-line" / "date-reconciliation" / f"{target_date}.json", record)
    return record


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    keys = ("schema_version", "date", "mode", "state", "date_complete", "original_evidence_complete", "repair_actions_available", "repair_actions_performed", "network_reconstruction_performed", "candidate_count", "unresolved_count", "publication_review_required", "coverage_gap_status", "evidence_exhausted", "next_action")
    return {key: payload.get(key) for key in keys}


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile one Food Line date without publishing.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    try:
        payload = reconcile_food_line_date(Path(args.repo_root), args.date, apply=args.apply)
    except Exception as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "date": args.date, "mode": "apply" if args.apply else "dry-run", "state": DateState.BLOCKED_ERROR.value, "date_complete": False, "original_evidence_complete": False, "repair_actions_available": [], "repair_actions_performed": [], "network_reconstruction_performed": False, "candidate_count": 0, "unresolved_count": 1, "publication_review_required": False, "coverage_gap_status": "BLOCKED_ERROR", "evidence_exhausted": False, "next_action": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(_summary(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
