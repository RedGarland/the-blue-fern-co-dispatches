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
    build_care_line_status,
    build_food_line_status,
    build_ice_status,
)


SCHEMA_VERSION = "dispatch_ops_status_v1"
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
    return (root / "output" / "site" / dispatch / "editions" / date / "index.html").is_file()


def _public_state(root: Path, dispatch: str, date: str, *, no_update: bool = False) -> str:
    if no_update:
        candidates = [
            root / "output" / "site" / dispatch / "status" / "no-updates" / f"{date}.json",
            root / "bluefern-dispatches-pages" / dispatch / "status" / "no-updates" / f"{date}.json",
        ]
        return "VERIFIED" if any(path.is_file() for path in candidates) else "UNKNOWN"
    return "VERIFIED" if _has_public_edition(root, dispatch, date) else "CURRENT"


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
        if gap and receipt and str(gap.get("backfill_status")) == "BACKFILL_NOT_REQUIRED" and gap.get("recovered_at"):
            replay = receipt.get("replay_result") if isinstance(receipt.get("replay_result"), dict) else {}
            unresolved = int(replay.get("unresolved") or 0)
            if unresolved == 0:
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


class CareLineAdapter(DispatchAdapter):
    dispatch = "care-line"

    def status(self, date: str) -> DispatchStatus:
        exported, evidence = _load_exported_status(self, date)
        if exported:
            return _normalize_from_exported(self, exported, date, evidence)
        expected = self._expected_instances_from_receipts(date)
        try:
            built = build_care_line_status(
                source_root=self.root,
                date=date,
                evaluated_at=_evaluated_next_morning(date),
                exported_at=_evaluated_next_morning(date),
                expected_instances=expected if expected else None,
            )
        except Exception as exc:  # noqa: BLE001
            return self.unknown(date, evidence, f"Could not evaluate Care Line receipts: {exc}")
        evidence.extend(self.existing(_receipts_root(self.root, self.dispatch, date).glob("*.json")))
        if built.get("task_summaries") or built.get("aggregate_status") == "MISSED":
            return _normalize_from_exported(self, built, date, evidence)
        return self.unknown(date, evidence)

    def _expected_instances_from_receipts(self, date: str) -> list[dict[str, str]]:
        instances: list[dict[str, str]] = []
        receipt_dates = [
            date,
            (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat(),
        ]
        for receipt_date in receipt_dates:
            paths = sorted(_receipts_root(self.root, self.dispatch, receipt_date).glob("*.json"))
            for path in paths:
                payload = self.read_json(path)
                if not payload:
                    continue
                task_key = str(payload.get("task_key") or "")
                scheduled_for = str(payload.get("scheduled_for") or "")
                if task_key and scheduled_for and scheduled_for != date:
                    instances.append({"task_key": task_key, "scheduled_for": scheduled_for})
        return instances


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
        no_update = self.read_json(no_update_path) or self.read_json(pages_no_update_path)
        if no_update and self._is_public_no_update(no_update, date):
            ev = self.existing([
                no_update_path,
                pages_no_update_path,
                self.root / str(no_update.get("run_manifest_path") or ""),
                self.root / str(no_update.get("collection_report_path") or ""),
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

        edition = self.root / "output" / "site" / "gaza" / "editions" / date / "index.html"
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
        if run_manifest:
            return self._from_run_manifest(date, run_manifest, manifest)
        return self.unknown(date, self.existing([no_update_path, pages_no_update_path, edition, manifest]))

    def _is_public_no_update(self, payload: dict[str, Any], date: str) -> bool:
        classification = str(payload.get("classification") or payload.get("status") or payload.get("outcome") or "").lower()
        return (
            str(payload.get("date") or payload.get("edition_date") or "") == date
            and int(payload.get("public_story_count") or 0) == 0
            and ("no_update" in classification or "no-update" in classification)
            and payload.get("daily_run_completed") is not False
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only unified dispatch operations status.")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Show normalized lifecycle status for one dispatch/date.")
    status.add_argument("dispatch", choices=SUPPORTED_DISPATCHES)
    status.add_argument("--date", required=True, help="Date in YYYY-MM-DD format.")
    status.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    status.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not DATE_RE.fullmatch(args.date):
        parser.error("--date must use YYYY-MM-DD")
    return args


def build_status(dispatch: str, date: str, *, root: Path = ROOT) -> DispatchStatus:
    adapter = ADAPTERS[dispatch](root.resolve())
    return adapter.status(date)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = build_status(args.dispatch, args.date, root=Path(args.root))
    if args.json:
        print(json.dumps(status.to_json_payload(), indent=2, sort_keys=True))
    else:
        print(render_text(status))
    return 0 if status.next_action == NextAction.NONE.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
