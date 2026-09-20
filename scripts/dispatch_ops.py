from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.operational_status_exporter import (  # noqa: E402
    build_food_line_status,
    build_ice_status,
)


SCHEMA_VERSION = "dispatch_ops_status_v1"
RECOVERY_PLAN_SCHEMA_VERSION = "dispatch_ops_recovery_plan_v1"
APPLY_SCHEMA_VERSION = "dispatch_ops_apply_v1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUPPORTED_DISPATCHES = ("food-line", "care-line", "gaza", "ice")


class Lifecycle(StrEnum):
    COMPLETE = "COMPLETE"
    PUBLISHED = "PUBLISHED"
    NO_UPDATE = "NO_UPDATE"
    SAFE_NO_OP = "SAFE_NO_OP"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    MISSED = "MISSED"
    UNKNOWN = "UNKNOWN"


class NextAction(StrEnum):
    NONE = "NONE"
    INVESTIGATE_COLLECTION = "INVESTIGATE_COLLECTION"
    INVESTIGATE_FAILED_SOURCES = "INVESTIGATE_FAILED_SOURCES"
    REVIEW_CANDIDATES = "REVIEW_CANDIDATES"
    PUBLISH_APPROVED_RELEASE = "PUBLISH_APPROVED_RELEASE"
    PUBLISH_NO_UPDATE = "PUBLISH_NO_UPDATE"
    RECOVER_MISSING_RUN = "RECOVER_MISSING_RUN"
    VERIFY_PUBLIC_STATE = "VERIFY_PUBLIC_STATE"
    INVESTIGATE_STATUS_EXPORT = "INVESTIGATE_STATUS_EXPORT"
    UNKNOWN_REQUIRES_OPERATOR = "UNKNOWN_REQUIRES_OPERATOR"


class RecoveryDisposition(StrEnum):
    NO_ACTION = "NO_ACTION"
    PLAN_AVAILABLE = "PLAN_AVAILABLE"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class RecoveryAction(StrEnum):
    NONE = "NONE"
    REPLAY_COLLECTION = "REPLAY_COLLECTION"
    REBUILD_STATUS = "REBUILD_STATUS"
    REVIEW_CANDIDATES = "REVIEW_CANDIDATES"
    PUBLISH_APPROVED_RELEASE = "PUBLISH_APPROVED_RELEASE"
    PUBLISH_NO_UPDATE = "PUBLISH_NO_UPDATE"
    VERIFY_PUBLIC_STATE = "VERIFY_PUBLIC_STATE"
    RECONSTRUCT_DATE = "RECONSTRUCT_DATE"
    INVESTIGATE_FAILED_SOURCES = "INVESTIGATE_FAILED_SOURCES"
    INVESTIGATE_COLLECTION = "INVESTIGATE_COLLECTION"
    INVESTIGATE_STATUS_EXPORT = "INVESTIGATE_STATUS_EXPORT"


class ApplyOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    NO_ACTION = "NO_ACTION"
    REFUSED = "REFUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DispatchStatus:
    dispatch: str
    date: str
    state: str
    collection: str
    editorial: str
    publication: str
    public_state: str
    receipts: str
    recovery: str
    next_action: str
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_json_payload(self) -> dict[str, Any]:
        payload = {"schema_version": SCHEMA_VERSION}
        payload.update(asdict(self))
        return payload


@dataclass(frozen=True)
class ApplyResult:
    dispatch: str
    date: str
    planned_action: str
    outcome: str
    changed: bool
    public_side_effects: bool
    scheduler_changes: bool
    collection_rerun: bool
    editorial_mutation: bool
    publication_attempted: bool
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status_before: dict[str, Any] = field(default_factory=dict)
    status_after: dict[str, Any] = field(default_factory=dict)

    def to_json_payload(self) -> dict[str, Any]:
        payload = {"schema_version": APPLY_SCHEMA_VERSION}
        payload.update(asdict(self))
        return payload


@dataclass(frozen=True)
class RecoveryPlan:
    dispatch: str
    date: str
    status_state: str
    disposition: str
    action: str
    safe_to_apply: bool
    requires_operator_confirmation: bool
    public_side_effects: bool
    scheduler_changes: bool
    collection_rerun: bool
    reason: str
    evidence: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json_payload(self) -> dict[str, Any]:
        payload = {"schema_version": RECOVERY_PLAN_SCHEMA_VERSION}
        payload.update(asdict(self))
        return payload


class DispatchAdapter:
    dispatch: str

    def __init__(self, root: Path) -> None:
        self.root = root

    def status(self, date: str) -> DispatchStatus:
        raise NotImplementedError

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def existing(self, paths: Iterable[Path]) -> list[str]:
        return [self.rel(path) for path in paths if path.exists()]

    def unknown(self, date: str, evidence: list[str] | None = None, warning: str | None = None) -> DispatchStatus:
        return DispatchStatus(
            dispatch=self.dispatch,
            date=date,
            state=Lifecycle.UNKNOWN.value,
            collection="UNKNOWN",
            editorial="UNKNOWN",
            publication="UNKNOWN",
            public_state="UNKNOWN",
            receipts="UNKNOWN",
            recovery="UNKNOWN",
            next_action=NextAction.UNKNOWN_REQUIRES_OPERATOR.value,
            evidence=sorted(evidence or []),
            warnings=[warning] if warning else ["No authoritative terminal evidence found."],
        )


def _evaluated_after(date: str) -> str:
    return (datetime.fromisoformat(date).replace(tzinfo=timezone.utc) + timedelta(days=2)).isoformat().replace("+00:00", "Z")


def _evaluated_next_morning(date: str) -> str:
    return (datetime.fromisoformat(f"{date}T05:00:00").replace(tzinfo=timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")


def _status_history_path(root: Path, dispatch: str, date: str) -> Path:
    return root / "ops" / "status" / dispatch / "history" / f"{date}.json"


def _status_latest_path(root: Path, dispatch: str) -> Path:
    return root / "ops" / "status" / dispatch / "latest.json"


def _load_exported_status(adapter: DispatchAdapter, date: str) -> tuple[dict[str, Any] | None, list[str]]:
    evidence: list[str] = []
    history = _status_history_path(adapter.root, adapter.dispatch, date)
    payload = adapter.read_json(history)
    if payload:
        return payload, [adapter.rel(history)]
    latest = _status_latest_path(adapter.root, adapter.dispatch)
    payload = adapter.read_json(latest)
    if payload and str(payload.get("observed_date") or "") == date:
        evidence.append(adapter.rel(latest))
        return payload, evidence
    return None, evidence


def _receipts_root(root: Path, dispatch: str, date: str) -> Path:
    return root / "status" / "operational-health" / dispatch / date / "runs"


def _has_public_edition(root: Path, dispatch: str, date: str) -> bool:
    return (root / "bluefern-dispatches-pages" / dispatch / "editions" / date / "index.html").is_file()


def _has_public_no_update(root: Path, dispatch: str, date: str) -> bool:
    return (root / "bluefern-dispatches-pages" / dispatch / "status" / "no-updates" / f"{date}.json").is_file()


def _public_state(root: Path, dispatch: str, date: str, *, no_update: bool = False) -> str:
    if no_update:
        return "VERIFIED" if _has_public_no_update(root, dispatch, date) else "NOT_VERIFIED"
    return "VERIFIED" if _has_public_edition(root, dispatch, date) else "NOT_VERIFIED"


def _normalize_from_exported(adapter: DispatchAdapter, payload: dict[str, Any], date: str, evidence: list[str]) -> DispatchStatus:
    aggregate = str(payload.get("aggregate_status") or "UNKNOWN")
    tasks = payload.get("task_summaries") if isinstance(payload.get("task_summaries"), list) else []
    task_statuses = {str(row.get("task_key") or ""): str(row.get("status") or "") for row in tasks if isinstance(row, dict)}
    classifications = {str(row.get("task_key") or ""): str(row.get("classification") or "") for row in tasks if isinstance(row, dict)}
    publication_status = str(payload.get("publication_status") or "")
    receipt_state = str(payload.get("receipt_completeness") or "UNKNOWN")
    recovery = str(payload.get("recovery_lifecycle") or "UNKNOWN")

    if aggregate == "MISSED":
        state = Lifecycle.MISSED.value
        next_action = NextAction.RECOVER_MISSING_RUN.value
    elif aggregate == "FAILED":
        state = Lifecycle.FAILED.value
        next_action = NextAction.INVESTIGATE_COLLECTION.value
    elif aggregate == "DEGRADED":
        state = Lifecycle.DEGRADED.value
        next_action = NextAction.INVESTIGATE_FAILED_SOURCES.value
    elif aggregate == "SUCCESS":
        state = Lifecycle.PUBLISHED.value if _has_public_edition(adapter.root, adapter.dispatch, date) else Lifecycle.COMPLETE.value
        next_action = NextAction.NONE.value
    elif aggregate == "SAFE_NO_OP":
        state = Lifecycle.SAFE_NO_OP.value
        next_action = NextAction.NONE.value
    else:
        state = Lifecycle.UNKNOWN.value
        next_action = NextAction.UNKNOWN_REQUIRES_OPERATOR.value

    if all(status == "SAFE_NO_OP" for status in task_statuses.values()) and task_statuses:
        state = Lifecycle.SAFE_NO_OP.value
        next_action = NextAction.NONE.value
    if "partial_success" in classifications.values():
        state = Lifecycle.DEGRADED.value
        next_action = NextAction.INVESTIGATE_FAILED_SOURCES.value

    collection = "UNKNOWN"
    if aggregate == "MISSED":
        collection = "MISSING"
    elif aggregate == "FAILED":
        collection = "FAILED"
    elif "partial_success" in classifications.values():
        collection = "PARTIAL_SUCCESS"
    elif aggregate in {"SUCCESS", "SAFE_NO_OP"}:
        collection = "COMPLETE"
    elif aggregate == "DEGRADED":
        collection = "DEGRADED"

    editorial = "READY" if adapter.dispatch == "care-line" and state == Lifecycle.DEGRADED.value else "COMPLETE"
    if state == Lifecycle.SAFE_NO_OP.value:
        editorial = "NO_QUALIFYING_MATERIAL"
    publication = "SAFE_NO_OP" if publication_status in {"safe_no_op", "skipped_not_release_ready", "no_approved_release", ""} and state in {Lifecycle.SAFE_NO_OP.value, Lifecycle.DEGRADED.value} else "COMPLETE"
    if adapter.dispatch == "ice":
        publication = "NOT_APPLICABLE" if not payload.get("publication_attempted") else publication
    public_state = _public_state(adapter.root, adapter.dispatch, date)
    if (
        state in {Lifecycle.COMPLETE.value, Lifecycle.PUBLISHED.value}
        and publication != "NOT_APPLICABLE"
        and public_state != "VERIFIED"
    ):
        next_action = NextAction.VERIFY_PUBLIC_STATE.value

    return DispatchStatus(
        dispatch=adapter.dispatch,
        date=date,
        state=state,
        collection=collection,
        editorial=editorial,
        publication=publication,
        public_state=public_state,
        receipts=receipt_state,
        recovery=recovery,
        next_action=next_action,
        evidence=sorted(evidence),
        warnings=[],
        details={"aggregate_status": aggregate, "task_statuses": task_statuses},
    )


class FoodLineAdapter(DispatchAdapter):
    dispatch = "food-line"

    def status(self, date: str) -> DispatchStatus:
        exported, evidence = _load_exported_status(self, date)
        if exported:
            return _normalize_from_exported(self, exported, date, evidence)

        gap_path = self.root / "data" / "dispatches" / "food-line" / "coverage-gaps" / f"{date}.json"
        receipt_path = self.root / "data" / "dispatches" / "food-line" / "historical-intake" / date / "reconciliation-receipt.json"
        gap = self.read_json(gap_path)
        receipt = self.read_json(receipt_path)
        if gap and receipt and self._is_reconstructed_recovered(date, gap, receipt):
            replay = receipt.get("replay_result") if isinstance(receipt.get("replay_result"), dict) else {}
            unresolved = int(replay.get("unresolved") or 0)
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.COMPLETE.value,
                collection="RECONSTRUCTED",
                editorial="COMPLETE",
                publication="NOT_REQUIRED",
                public_state="NOT_APPLICABLE",
                receipts="RECONSTRUCTED",
                recovery="RECOVERED",
                next_action=NextAction.NONE.value,
                evidence=sorted(self.existing([gap_path, receipt_path])),
                details={
                    "coverage_gap_status": gap.get("observation_status"),
                    "backfill_status": gap.get("backfill_status"),
                    "unresolved_reconstructed_candidates": unresolved,
                    "replay_result": replay,
                },
            )
        if gap and receipt and self._has_unresolved_reconstructed_candidates(date, gap, receipt):
            replay = receipt.get("replay_result") if isinstance(receipt.get("replay_result"), dict) else {}
            unresolved = int(replay.get("unresolved") or 0)
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.NEEDS_REVIEW.value,
                collection="RECONSTRUCTED",
                editorial="NEEDS_REVIEW",
                publication="NOT_REQUIRED",
                public_state="NOT_APPLICABLE",
                receipts="RECONSTRUCTED",
                recovery="REVIEW_REQUIRED",
                next_action=NextAction.REVIEW_CANDIDATES.value,
                evidence=sorted(self.existing([gap_path, receipt_path])),
                details={
                    "coverage_gap_status": gap.get("observation_status"),
                    "backfill_status": gap.get("backfill_status"),
                    "unresolved_reconstructed_candidates": unresolved,
                    "replay_result": replay,
                },
            )

        try:
            built = build_food_line_status(
                source_root=self.root,
                date=date,
                evaluated_at=_evaluated_after(date),
                exported_at=_evaluated_after(date),
            )
        except Exception as exc:  # noqa: BLE001
            return self.unknown(date, self.existing([gap_path, receipt_path]), f"Could not evaluate Food Line receipts: {exc}")
        evidence.extend(self.existing(_receipts_root(self.root, self.dispatch, date).glob("*.json")))
        if built.get("task_summaries"):
            return _normalize_from_exported(self, built, date, evidence)
        return self.unknown(date, evidence)

    def _is_reconstructed_recovered(self, date: str, gap: dict[str, Any], receipt: dict[str, Any]) -> bool:
        replay = receipt.get("replay_result") if isinstance(receipt.get("replay_result"), dict) else {}
        backfill_status = str(gap.get("backfill_status") or "")
        return (
            str(gap.get("dispatch") or "") == self.dispatch
            and str(gap.get("observation_date") or "") == date
            and str(receipt.get("historical_date") or "") == date
            and backfill_status in {"BACKFILL_NOT_REQUIRED", "RECOVERED", "COMPLETE_RECONSTRUCTED"}
            and bool(gap.get("recovered_at"))
            and int(replay.get("unresolved") or 0) == 0
        )

    def _has_unresolved_reconstructed_candidates(self, date: str, gap: dict[str, Any], receipt: dict[str, Any]) -> bool:
        replay = receipt.get("replay_result") if isinstance(receipt.get("replay_result"), dict) else {}
        return (
            str(gap.get("dispatch") or "") == self.dispatch
            and str(gap.get("observation_date") or "") == date
            and str(receipt.get("historical_date") or "") == date
            and int(replay.get("unresolved") or 0) > 0
        )


class CareLineAdapter(DispatchAdapter):
    dispatch = "care-line"

    def status(self, date: str) -> DispatchStatus:
        exported, evidence = _load_exported_status(self, date)
        if exported:
            return _normalize_from_exported(self, exported, date, evidence)
        receipts = self._available_receipts(date)
        if not receipts:
            return self.unknown(date, evidence)
        evidence.extend(path for path, _receipt in receipts)
        return self._from_available_receipts(date, receipts, evidence)

    def _available_receipts(self, date: str) -> list[tuple[str, dict[str, Any]]]:
        receipt_dates = {date, (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()}
        receipts: list[tuple[str, dict[str, Any]]] = []
        for receipt_date in sorted(receipt_dates):
            for path in sorted(_receipts_root(self.root, self.dispatch, receipt_date).glob("*.json")):
                payload = self.read_json(path)
                if payload and payload.get("dispatch") == self.dispatch and self._receipt_belongs_to_local_date(date, receipt_date, payload):
                    receipts.append((self.rel(path), payload))
        return receipts

    def _receipt_belongs_to_local_date(self, date: str, receipt_date: str, receipt: dict[str, Any]) -> bool:
        if receipt_date == date:
            return True
        observed_at = str(receipt.get("started_at") or receipt.get("scheduled_for") or "")
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return observed.date().isoformat() == receipt_date and observed.hour < 5

    def _from_available_receipts(
        self,
        date: str,
        receipts: list[tuple[str, dict[str, Any]]],
        evidence: list[str],
    ) -> DispatchStatus:
        statuses = [str(receipt.get("status") or "UNKNOWN") for _path, receipt in receipts]
        classifications = [str(receipt.get("classification") or "") for _path, receipt in receipts]
        task_keys = {str(receipt.get("task_key") or "") for _path, receipt in receipts}
        has_partial = "partial_success" in classifications or "DEGRADED" in statuses
        has_failed = "FAILED" in statuses
        all_safe_noop = all(status == "SAFE_NO_OP" for status in statuses)
        receipts_state = "OBSERVED"
        if has_failed:
            state = Lifecycle.FAILED.value
            collection = "FAILED"
            next_action = NextAction.INVESTIGATE_COLLECTION.value
        elif has_partial:
            state = Lifecycle.DEGRADED.value
            collection = "PARTIAL_SUCCESS"
            next_action = NextAction.INVESTIGATE_FAILED_SOURCES.value
        elif all_safe_noop:
            state = Lifecycle.SAFE_NO_OP.value
            collection = "COMPLETE"
            next_action = NextAction.NONE.value
        else:
            state = Lifecycle.UNKNOWN.value
            collection = "COMPLETE" if "SUCCESS" in statuses else "UNKNOWN"
            next_action = NextAction.INVESTIGATE_STATUS_EXPORT.value
        return DispatchStatus(
            dispatch=self.dispatch,
            date=date,
            state=state,
            collection=collection,
            editorial="READY" if state == Lifecycle.DEGRADED.value else "COMPLETE",
            publication="SAFE_NO_OP" if any(str(receipt.get("publication_status") or "") == "safe_no_op" for _path, receipt in receipts) else "UNKNOWN",
            public_state="CURRENT" if state in {Lifecycle.DEGRADED.value, Lifecycle.SAFE_NO_OP.value} else "NOT_VERIFIED",
            receipts=receipts_state,
            recovery="HEALTHY",
            next_action=next_action,
            evidence=sorted(evidence),
            details={
                "observed_task_keys": sorted(task_keys),
                "receipt_statuses": statuses,
                "receipt_classifications": classifications,
            },
        )


class IceAdapter(DispatchAdapter):
    dispatch = "ice"

    def status(self, date: str) -> DispatchStatus:
        exported, evidence = _load_exported_status(self, date)
        if exported:
            return _normalize_from_exported(self, exported, date, evidence)
        try:
            built = build_ice_status(
                source_root=self.root,
                date=date,
                evaluated_at=_evaluated_next_morning(date),
                exported_at=_evaluated_next_morning(date),
            )
        except Exception as exc:  # noqa: BLE001
            return self.unknown(date, evidence, f"Could not evaluate ICE receipts: {exc}")
        evidence.extend(self.existing(_receipts_root(self.root, self.dispatch, date).glob("*.json")))
        if built.get("task_summaries") or built.get("aggregate_status") == "MISSED":
            status = _normalize_from_exported(self, built, date, evidence)
            if status.collection == "COMPLETE" and status.state == Lifecycle.COMPLETE.value:
                return DispatchStatus(**{**asdict(status), "state": Lifecycle.COMPLETE.value, "editorial": "NO_QUALIFYING_MATERIAL" if self._no_update(built) else status.editorial})
            return status
        return self.unknown(date, evidence)

    def _no_update(self, payload: dict[str, Any]) -> bool:
        tasks = payload.get("task_summaries") if isinstance(payload.get("task_summaries"), list) else []
        return any(str(row.get("status") or "") == "SAFE_NO_OP" for row in tasks if isinstance(row, dict))


class GazaAdapter(DispatchAdapter):
    dispatch = "gaza"

    def status(self, date: str) -> DispatchStatus:
        exported, evidence = _load_exported_status(self, date)
        if exported:
            return _normalize_from_exported(self, exported, date, evidence)

        no_update_path = self.root / "output" / "site" / "gaza" / "status" / "no-updates" / f"{date}.json"
        pages_no_update_path = self.root / "bluefern-dispatches-pages" / "gaza" / "status" / "no-updates" / f"{date}.json"
        pages_no_update = self.read_json(pages_no_update_path)
        if pages_no_update and self._is_public_no_update(pages_no_update, date):
            ev = self.existing([
                no_update_path,
                pages_no_update_path,
                self.root / str(pages_no_update.get("run_manifest_path") or ""),
                self.root / str(pages_no_update.get("collection_report_path") or ""),
            ])
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.NO_UPDATE.value,
                collection="COMPLETE",
                editorial="NO_QUALIFYING_MATERIAL",
                publication="COMPLETE",
                public_state="VERIFIED",
                receipts="COMPLETE",
                recovery="NONE",
                next_action=NextAction.NONE.value,
                evidence=sorted(dict.fromkeys(ev)),
                details={"latest_real_edition_date": self._latest_real_edition_before(date)},
            )

        edition = self.root / "bluefern-dispatches-pages" / "gaza" / "editions" / date / "index.html"
        local_edition = self.root / "output" / "site" / "gaza" / "editions" / date / "index.html"
        manifest = self.root / "data" / "dispatches" / "gaza" / "editions" / date / "run_manifest.json"
        run_manifest = self.read_json(manifest)
        if edition.exists():
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.PUBLISHED.value,
                collection="COMPLETE",
                editorial="COMPLETE",
                publication="COMPLETE",
                public_state="VERIFIED",
                receipts="COMPLETE" if run_manifest else "UNKNOWN",
                recovery="NONE",
                next_action=NextAction.NONE.value if run_manifest else NextAction.INVESTIGATE_STATUS_EXPORT.value,
                evidence=sorted(self.existing([edition, manifest])),
            )
        if local_edition.exists():
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.COMPLETE.value,
                collection="COMPLETE",
                editorial="COMPLETE",
                publication="COMPLETE",
                public_state="NOT_VERIFIED",
                receipts="COMPLETE" if run_manifest else "UNKNOWN",
                recovery="NONE",
                next_action=NextAction.VERIFY_PUBLIC_STATE.value,
                evidence=sorted(self.existing([local_edition, manifest])),
            )
        if run_manifest:
            manifest_status = str(run_manifest.get("status") or run_manifest.get("operator_status") or "").lower()
            if run_manifest.get("ok") is False or manifest_status in {"failed", "failure"}:
                return self._from_run_manifest(date, run_manifest, manifest)
        local_no_update = self.read_json(no_update_path)
        if local_no_update and self._is_public_no_update(local_no_update, date):
            ev = self.existing([
                no_update_path,
                self.root / str(local_no_update.get("run_manifest_path") or ""),
                self.root / str(local_no_update.get("collection_report_path") or ""),
            ])
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.NO_UPDATE.value,
                collection="COMPLETE",
                editorial="NO_QUALIFYING_MATERIAL",
                publication="COMPLETE",
                public_state="NOT_VERIFIED",
                receipts="COMPLETE",
                recovery="NONE",
                next_action=NextAction.VERIFY_PUBLIC_STATE.value,
                evidence=sorted(dict.fromkeys(ev)),
                details={"latest_real_edition_date": self._latest_real_edition_before(date)},
            )
        if run_manifest:
            return self._from_run_manifest(date, run_manifest, manifest)
        return self.unknown(date, self.existing([no_update_path, pages_no_update_path, edition, manifest]))

    def _is_public_no_update(self, payload: dict[str, Any], date: str) -> bool:
        classification = str(payload.get("classification") or payload.get("status") or payload.get("outcome") or "").lower()
        completed = payload.get("daily_run_completed", payload.get("run_completed_successfully", True))
        return (
            str(payload.get("date") or payload.get("edition_date") or "") == date
            and int(payload.get("public_story_count") or 0) == 0
            and (
                "no_update" in classification
                or "no-update" in classification
                or classification in {"no_publication_needed", "no_publication_required"}
            )
            and completed is not False
        )

    def _from_run_manifest(self, date: str, payload: dict[str, Any], path: Path) -> DispatchStatus:
        status = str(payload.get("status") or payload.get("operator_status") or "").lower()
        ok = payload.get("ok")
        if ok is False or status in {"failed", "failure"}:
            validation = str(payload.get("validation_status") or "").lower()
            next_action = NextAction.REVIEW_CANDIDATES.value if "validation" in validation or "blocked" in status else NextAction.INVESTIGATE_COLLECTION.value
            return DispatchStatus(
                dispatch=self.dispatch,
                date=date,
                state=Lifecycle.FAILED.value,
                collection="FAILED",
                editorial="VALIDATION_BLOCKED" if next_action == NextAction.REVIEW_CANDIDATES.value else "UNKNOWN",
                publication="NOT_ATTEMPTED",
                public_state="NOT_VERIFIED",
                receipts="COMPLETE",
                recovery="NONE",
                next_action=next_action,
                evidence=[self.rel(path)],
            )
        return self.unknown(date, [self.rel(path)], "Run manifest exists but has no recognized terminal publication/no-update state.")

    def _latest_real_edition_before(self, date: str) -> str | None:
        editions = self.root / "output" / "site" / "gaza" / "editions"
        dates = sorted(path.name for path in editions.glob("????-??-??") if path.name < date and (path / "index.html").is_file())
        return dates[-1] if dates else None


ADAPTERS = {
    "food-line": FoodLineAdapter,
    "care-line": CareLineAdapter,
    "gaza": GazaAdapter,
    "ice": IceAdapter,
}


TERMINAL_NO_ACTION_STATES = {
    Lifecycle.COMPLETE.value,
    Lifecycle.PUBLISHED.value,
    Lifecycle.NO_UPDATE.value,
    Lifecycle.SAFE_NO_OP.value,
}


def _base_plan(
    status: DispatchStatus,
    *,
    disposition: RecoveryDisposition,
    action: RecoveryAction,
    reason: str,
    preconditions: Iterable[str] = (),
    warnings: Iterable[str] = (),
    requires_operator_confirmation: bool | None = None,
    public_side_effects: bool = False,
    scheduler_changes: bool = False,
    collection_rerun: bool = False,
) -> RecoveryPlan:
    if requires_operator_confirmation is None:
        requires_operator_confirmation = disposition == RecoveryDisposition.OPERATOR_REVIEW_REQUIRED
    return RecoveryPlan(
        dispatch=status.dispatch,
        date=status.date,
        status_state=status.state,
        disposition=disposition.value,
        action=action.value,
        safe_to_apply=False,
        requires_operator_confirmation=requires_operator_confirmation,
        public_side_effects=public_side_effects,
        scheduler_changes=scheduler_changes,
        collection_rerun=collection_rerun,
        reason=reason,
        evidence=sorted(status.evidence),
        preconditions=sorted(preconditions),
        warnings=sorted(warnings),
    )


def build_recovery_plan(dispatch: str, date: str, *, root: Path = ROOT) -> RecoveryPlan:
    status = build_status(dispatch, date, root=root)
    return build_recovery_plan_from_status(status)


def build_recovery_plan_from_status(status: DispatchStatus) -> RecoveryPlan:
    if status.state in TERMINAL_NO_ACTION_STATES and status.next_action == NextAction.NONE.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.NO_ACTION,
            action=RecoveryAction.NONE,
            reason="Current state is already terminal and has no pending operational action.",
            preconditions=("SOURCE_EVIDENCE_PRESENT",) if status.evidence else (),
            requires_operator_confirmation=False,
        )

    if status.next_action == NextAction.VERIFY_PUBLIC_STATE.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.PLAN_AVAILABLE,
            action=RecoveryAction.VERIFY_PUBLIC_STATE,
            reason="Operational state is complete, but durable public-state proof is not verified.",
            preconditions=("PUBLIC_STATE_NOT_VERIFIED",),
            requires_operator_confirmation=False,
        )

    if status.next_action == NextAction.REVIEW_CANDIDATES.value or status.state == Lifecycle.NEEDS_REVIEW.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.OPERATOR_REVIEW_REQUIRED,
            action=RecoveryAction.REVIEW_CANDIDATES,
            reason="Editorial or validation evidence requires operator review before any recovery can proceed.",
            preconditions=("SOURCE_EVIDENCE_PRESENT",) if status.evidence else (),
        )

    if status.state == Lifecycle.DEGRADED.value and status.next_action == NextAction.INVESTIGATE_FAILED_SOURCES.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.OPERATOR_REVIEW_REQUIRED,
            action=RecoveryAction.INVESTIGATE_FAILED_SOURCES,
            reason="Partial-success receipts require investigation of failed sources, not a full cycle replay.",
            preconditions=("SOURCE_EVIDENCE_PRESENT", "NO_PUBLIC_SIDE_EFFECT"),
        )

    if status.state == Lifecycle.FAILED.value and status.next_action == NextAction.INVESTIGATE_COLLECTION.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.PLAN_AVAILABLE,
            action=RecoveryAction.INVESTIGATE_COLLECTION,
            reason="Failure evidence identifies collection investigation as the next read-only recovery step.",
            preconditions=("SOURCE_EVIDENCE_PRESENT", "NO_PUBLIC_SIDE_EFFECT"),
        )

    if status.state == Lifecycle.MISSED.value:
        if _can_plan_collection_replay(status):
            return _base_plan(
                status,
                disposition=RecoveryDisposition.PLAN_AVAILABLE,
                action=RecoveryAction.REPLAY_COLLECTION,
                reason="Authoritative scheduler expectation and replay preconditions identify a non-public collection replay target.",
                preconditions=(
                    "AUTHORITATIVE_EXPECTATION_PRESENT",
                    "EXISTING_REPLAY_TOOL_AVAILABLE",
                    "NO_LATER_TERMINAL_RUN",
                    "NO_PUBLIC_SIDE_EFFECT",
                ),
                collection_rerun=True,
            )
        return _base_plan(
            status,
            disposition=RecoveryDisposition.OPERATOR_REVIEW_REQUIRED,
            action=RecoveryAction.INVESTIGATE_STATUS_EXPORT,
            reason="A missed run is authoritative, but this status does not prove an exact safe replay target.",
            preconditions=("AUTHORITATIVE_EXPECTATION_PRESENT",) if status.evidence else (),
            warnings=("Replay was not proposed without authoritative instance and tooling evidence.",),
        )

    if status.state == Lifecycle.UNKNOWN.value:
        return _base_plan(
            status,
            disposition=RecoveryDisposition.UNKNOWN,
            action=RecoveryAction.INVESTIGATE_STATUS_EXPORT
            if status.next_action == NextAction.UNKNOWN_REQUIRES_OPERATOR.value
            else RecoveryAction.NONE,
            reason="Evidence is ambiguous or incomplete; no recovery plan was fabricated.",
            warnings=status.warnings,
        )

    return _base_plan(
        status,
        disposition=RecoveryDisposition.UNSUPPORTED,
        action=RecoveryAction.NONE,
        reason="No supported Phase 2A recovery plan exists for the current normalized status.",
        warnings=("Phase 2A is read-only and does not apply recovery.",),
    )


def apply_recovery_plan(dispatch: str, date: str, *, root: Path = ROOT, confirm: str | None = None) -> ApplyResult:
    root = root.resolve()
    status_before = build_status(dispatch, date, root=root)
    plan = build_recovery_plan_from_status(status_before)
    if plan.disposition == RecoveryDisposition.NO_ACTION.value and plan.action == RecoveryAction.NONE.value:
        return _apply_result(
            plan,
            outcome=ApplyOutcome.NO_ACTION,
            status_before=status_before,
            status_after=status_before,
            evidence=plan.evidence,
            warnings=("No executable recovery action is pending.",),
        )
    if plan.action != RecoveryAction.VERIFY_PUBLIC_STATE.value:
        return _apply_result(
            plan,
            outcome=ApplyOutcome.REFUSED,
            status_before=status_before,
            status_after=status_before,
            evidence=plan.evidence,
            warnings=(f"Phase 2B only allows {RecoveryAction.VERIFY_PUBLIC_STATE.value}; refused {plan.action}.",),
        )
    if plan.disposition != RecoveryDisposition.PLAN_AVAILABLE.value:
        return _apply_result(
            plan,
            outcome=ApplyOutcome.REFUSED,
            status_before=status_before,
            status_after=status_before,
            evidence=plan.evidence,
            warnings=("Recovery plan is not available for execution.",),
        )
    if confirm != RecoveryAction.VERIFY_PUBLIC_STATE.value:
        return _apply_result(
            plan,
            outcome=ApplyOutcome.REFUSED,
            status_before=status_before,
            status_after=status_before,
            evidence=plan.evidence,
            warnings=("Missing required confirmation token: --confirm VERIFY_PUBLIC_STATE.",),
        )
    verified, verification_evidence = _verify_public_state(root, status_before)
    status_after = build_status(dispatch, date, root=root)
    return _apply_result(
        plan,
        outcome=ApplyOutcome.VERIFIED if verified else ApplyOutcome.NOT_VERIFIED,
        status_before=status_before,
        status_after=status_after,
        evidence=sorted(dict.fromkeys([*plan.evidence, *verification_evidence])),
        warnings=() if verified else ("Durable public-state proof was not found.",),
    )


def _apply_result(
    plan: RecoveryPlan,
    *,
    outcome: ApplyOutcome,
    status_before: DispatchStatus,
    status_after: DispatchStatus,
    evidence: Iterable[str],
    warnings: Iterable[str] = (),
) -> ApplyResult:
    return ApplyResult(
        dispatch=plan.dispatch,
        date=plan.date,
        planned_action=plan.action,
        outcome=outcome.value,
        changed=False,
        public_side_effects=False,
        scheduler_changes=False,
        collection_rerun=False,
        editorial_mutation=False,
        publication_attempted=False,
        evidence=sorted(evidence),
        warnings=sorted(warnings),
        status_before=status_before.to_json_payload(),
        status_after=status_after.to_json_payload(),
    )


def _verify_public_state(root: Path, status: DispatchStatus) -> tuple[bool, list[str]]:
    if status.dispatch == "gaza" and status.state == Lifecycle.NO_UPDATE.value:
        path = root / "bluefern-dispatches-pages" / "gaza" / "status" / "no-updates" / f"{status.date}.json"
    else:
        path = root / "bluefern-dispatches-pages" / status.dispatch / "editions" / status.date / "index.html"
    return path.is_file(), [path.relative_to(root).as_posix()] if path.is_file() else []


def _apply_exit_code(result: ApplyResult) -> int:
    if result.outcome in {ApplyOutcome.VERIFIED.value, ApplyOutcome.NO_ACTION.value}:
        return 0
    if result.outcome == ApplyOutcome.NOT_VERIFIED.value:
        return 2
    if result.outcome in {ApplyOutcome.REFUSED.value, ApplyOutcome.BLOCKED.value}:
        return 3
    return 1


def _can_plan_collection_replay(status: DispatchStatus) -> bool:
    details = status.details if isinstance(status.details, dict) else {}
    return (
        status.state == Lifecycle.MISSED.value
        and bool(details.get("authoritative_expected_instance"))
        and bool(details.get("existing_replay_tool_available"))
        and bool(details.get("no_later_terminal_run"))
        and bool(details.get("replay_has_no_public_side_effect"))
    )


def render_text(status: DispatchStatus) -> str:
    label = status.dispatch.replace("-", " ").title().replace("Gaza", "Gaza").replace("Ice", "ICE")
    lines = [
        f"Dispatch: {label}",
        f"Date: {status.date}",
        f"State: {status.state}",
        "",
        f"Collection: {status.collection}",
        f"Editorial: {status.editorial}",
        f"Publication: {status.publication}",
        f"Public state: {status.public_state}",
        f"Receipts: {status.receipts}",
        f"Recovery: {status.recovery}",
        "",
        f"Next action: {status.next_action}",
    ]
    if status.evidence:
        lines.extend(["", "Evidence:"])
        lines.extend(f"- {path}" for path in status.evidence)
    if status.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in status.warnings)
    return "\n".join(lines)


def render_recovery_text(plan: RecoveryPlan) -> str:
    label = plan.dispatch.replace("-", " ").title().replace("Gaza", "Gaza").replace("Ice", "ICE")
    lines = [
        f"Dispatch: {label}",
        f"Date: {plan.date}",
        f"Current state: {plan.status_state}",
        "",
        f"Recovery disposition: {plan.disposition}",
        f"Action: {plan.action}",
        "",
        "Reason:",
        plan.reason,
        "",
        f"Public side effects: {str(plan.public_side_effects).lower()}",
        f"Scheduler changes: {str(plan.scheduler_changes).lower()}",
        f"Collection rerun: {str(plan.collection_rerun).lower()}",
        f"Operator confirmation required: {str(plan.requires_operator_confirmation).lower()}",
    ]
    if plan.preconditions:
        lines.extend(["", "Preconditions:"])
        lines.extend(f"- {precondition}" for precondition in plan.preconditions)
    if plan.evidence:
        lines.extend(["", "Evidence:"])
        lines.extend(f"- {path}" for path in plan.evidence)
    if plan.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def render_apply_text(result: ApplyResult) -> str:
    label = result.dispatch.replace("-", " ").title().replace("Gaza", "Gaza").replace("Ice", "ICE")
    lines = [
        f"Dispatch: {label}",
        f"Date: {result.date}",
        f"Planned action: {result.planned_action}",
        "",
        f"Apply outcome: {result.outcome}",
        f"Changed: {str(result.changed).lower()}",
        "",
        f"Public side effects: {str(result.public_side_effects).lower()}",
        f"Scheduler changes: {str(result.scheduler_changes).lower()}",
        f"Collection rerun: {str(result.collection_rerun).lower()}",
        f"Editorial mutation: {str(result.editorial_mutation).lower()}",
        f"Publication attempted: {str(result.publication_attempted).lower()}",
    ]
    if result.evidence:
        lines.extend(["", "Evidence:"])
        lines.extend(f"- {path}" for path in result.evidence)
    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only unified dispatch operations status.")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Show normalized lifecycle status for one dispatch/date.")
    status.add_argument("dispatch", choices=SUPPORTED_DISPATCHES)
    status.add_argument("--date", required=True, help="Date in YYYY-MM-DD format.")
    status.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    status.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    recover = sub.add_parser("recover", help="Plan read-only recovery for one dispatch/date.")
    recover.add_argument("dispatch", choices=SUPPORTED_DISPATCHES)
    recover.add_argument("--date", required=True, help="Date in YYYY-MM-DD format.")
    recover.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    recover.add_argument("--apply", action="store_true", help="Execute a supported recovery action.")
    recover.add_argument("--confirm", help="Required confirmation token for --apply.")
    recover.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not DATE_RE.fullmatch(args.date):
        parser.error("--date must use YYYY-MM-DD")
    return args


def build_status(dispatch: str, date: str, *, root: Path = ROOT) -> DispatchStatus:
    adapter = ADAPTERS[dispatch](root.resolve())
    return adapter.status(date)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "recover":
        if args.confirm and not args.apply:
            print("--confirm is only valid with --apply", file=sys.stderr)
            return 3
        if args.apply:
            result = apply_recovery_plan(args.dispatch, args.date, root=Path(args.root), confirm=args.confirm)
            if args.json:
                print(json.dumps(result.to_json_payload(), indent=2, sort_keys=True))
            else:
                print(render_apply_text(result))
            return _apply_exit_code(result)
        plan = build_recovery_plan(args.dispatch, args.date, root=Path(args.root))
        if args.json:
            print(json.dumps(plan.to_json_payload(), indent=2, sort_keys=True))
        else:
            print(render_recovery_text(plan))
        return 0
    status = build_status(args.dispatch, args.date, root=Path(args.root))
    if args.json:
        print(json.dumps(status.to_json_payload(), indent=2, sort_keys=True))
    else:
        print(render_text(status))
    return 0 if status.next_action == NextAction.NONE.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
