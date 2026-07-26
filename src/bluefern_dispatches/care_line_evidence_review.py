from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_record import CareLineReviewedRecord, deterministic_records_json, stable_json_hash


DECISION_SCHEMA_VERSION = "bluefern.care_line.evidence_review.v1"
REVIEW_PACKET_SCHEMA_VERSION = "bluefern.care_line.phase14b_evidence_review.v1"
LEDGER_SCHEMA_VERSION = "bluefern.care_line.evidence_decisions_ledger.v1"
REPORT_SCHEMA_VERSION = "bluefern.care_line.evidence_review_import_report.v1"
IMPORTER_VERSION = "care-line-evidence-review-import-v1"

ALLOWED_DECISIONS = {"approved", "rejected", "deferred", "care_line_only", "excluded", "corrected"}
DECISION_COLUMNS = [
    "schema_version",
    "producer_record_id",
    "record_fingerprint",
    "evidence_decision",
    "evidence_text",
    "evidence_provenance_type",
    "evidence_source_url",
    "evidence_source_field",
    "evidence_source_artifact",
    "reviewer",
    "review_reason",
    "reviewed_at",
    "supersedes_decision_id",
]


@dataclass(frozen=True)
class EvidenceDecision:
    schema_version: str
    producer_record_id: str
    record_fingerprint: str
    evidence_decision: str
    evidence_text: str
    evidence_provenance_type: str
    evidence_source_url: str
    evidence_source_field: str
    evidence_source_artifact: str
    reviewer: str
    review_reason: str
    reviewed_at: str
    supersedes_decision_id: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer_record_id": self.producer_record_id,
            "record_fingerprint": self.record_fingerprint,
            "evidence_decision": self.evidence_decision,
            "evidence_text": self.evidence_text,
            "evidence_provenance_type": self.evidence_provenance_type,
            "evidence_source_url": self.evidence_source_url,
            "evidence_source_field": self.evidence_source_field,
            "evidence_source_artifact": self.evidence_source_artifact,
            "reviewer": self.reviewer,
            "review_reason": self.review_reason,
            "reviewed_at": self.reviewed_at,
            "supersedes_evidence_decision_id": self.supersedes_decision_id,
        }

    def signature(self, review_packet_fingerprint: str) -> str:
        payload = self.identity_dict() | {"review_packet_fingerprint": review_packet_fingerprint}
        return stable_json_hash(payload)

    def identity_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload.pop("supersedes_evidence_decision_id", None)
        return payload


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _nested_text(row: Mapping[str, Any], path: tuple[str, ...]) -> str:
    current: Any = row
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return "" if current in (None, "", [], {}) else str(current).strip()


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def refuse_public_or_pages_path(path: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    forbidden = [repo_root / "output" / "site", repo_root / "bluefern-dispatches-pages"]
    for root in forbidden:
        if root.exists() and _is_under(resolved, root):
            raise ValueError(f"refusing path inside protected public/Pages location: {path}")


def _fingerprint(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def load_review_packet(path: Path) -> dict[str, Any]:
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"review packet must be a JSON object: {path}")
    if payload.get("schema_version") != REVIEW_PACKET_SCHEMA_VERSION:
        raise ValueError("unsupported review packet schema_version")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("review packet records must be a list")
    return payload


def load_decisions_json(path: Path) -> dict[str, Any]:
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"decision JSON must be an object: {path}")
    if payload.get("schema_version") != DECISION_SCHEMA_VERSION:
        raise ValueError("unsupported decision schema_version")
    return payload


def load_decisions_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return rows


def _normalize_decision_row(row: Mapping[str, Any], *, schema_version: str = DECISION_SCHEMA_VERSION) -> EvidenceDecision:
    decision = EvidenceDecision(
        schema_version=_text(row, "schema_version") or schema_version,
        producer_record_id=_text(row, "producer_record_id"),
        record_fingerprint=_text(row, "record_fingerprint"),
        evidence_decision=_text(row, "evidence_decision"),
        evidence_text=_text(row, "evidence_text"),
        evidence_provenance_type=_text(row, "evidence_provenance_type"),
        evidence_source_url=_text(row, "evidence_source_url"),
        evidence_source_field=_text(row, "evidence_source_field"),
        evidence_source_artifact=_text(row, "evidence_source_artifact"),
        reviewer=_text(row, "reviewer"),
        review_reason=_text(row, "review_reason"),
        reviewed_at=_text(row, "reviewed_at"),
        supersedes_decision_id=_text(row, "supersedes_decision_id", "supersedes_evidence_decision_id"),
    )
    if decision.schema_version != DECISION_SCHEMA_VERSION:
        raise ValueError("unsupported decision schema_version")
    if not decision.producer_record_id:
        raise ValueError("producer_record_id is required")
    if not decision.record_fingerprint:
        raise ValueError("record_fingerprint is required")
    if decision.evidence_decision not in ALLOWED_DECISIONS:
        raise ValueError(f"unsupported evidence_decision: {decision.evidence_decision}")
    if not decision.reviewer:
        raise ValueError("reviewer is required")
    if not decision.review_reason:
        raise ValueError("review_reason is required")
    if not decision.reviewed_at:
        raise ValueError("reviewed_at is required")
    if decision.evidence_decision in {"approved", "corrected"} and decision.evidence_provenance_type not in {"source_explicit", "reviewer_transcribed", "missing"}:
        raise ValueError("approved decisions require source-explicit or reviewer-transcribed provenance")
    return decision


def load_decisions_payloads(json_path: Path, csv_path: Path, *, strict: bool = True) -> tuple[list[EvidenceDecision], dict[str, Any]]:
    json_payload = load_decisions_json(json_path)
    csv_rows = load_decisions_csv(csv_path)
    json_rows = json_payload.get("decisions") or []
    if not isinstance(json_rows, list):
        raise ValueError("decision JSON must include a decisions list")
    json_schema_version = _text(json_payload, "schema_version")
    json_decisions = [_normalize_decision_row(row, schema_version=json_schema_version) for row in json_rows if isinstance(row, dict)]
    csv_decisions = [_normalize_decision_row(row) for row in csv_rows if isinstance(row, dict)]
    _validate_equivalence(json_decisions, csv_decisions, strict=strict)
    return json_decisions, json_payload


def _validate_equivalence(json_decisions: list[EvidenceDecision], csv_decisions: list[EvidenceDecision], *, strict: bool) -> None:
    if len(json_decisions) != len(csv_decisions):
        raise ValueError("JSON/CSV decision row counts differ")
    json_index = {row.producer_record_id: row for row in json_decisions}
    csv_index = {row.producer_record_id: row for row in csv_decisions}
    if json_index.keys() != csv_index.keys():
        raise ValueError("JSON/CSV producer_record_id sets differ")
    for producer_record_id in sorted(json_index):
        left = json_index[producer_record_id].canonical_dict()
        right = csv_index[producer_record_id].canonical_dict()
        for field in DECISION_COLUMNS:
            if left.get(field, "") != right.get(field, ""):
                raise ValueError(f"JSON/CSV decision mismatch for {producer_record_id}: {field}")
    if strict:
        return


def load_reviewed_records(path: Path) -> list[CareLineReviewedRecord]:
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"reviewed records must be an object: {path}")
    if payload.get("schema_version") != "bluefern.care_line.reviewed_record.v1":
        raise ValueError("unsupported reviewed-record schema_version")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("reviewed records payload must contain a list")
    return [CareLineReviewedRecord.model_validate(row) for row in records if isinstance(row, dict)]


def review_packet_fingerprint(packet: Mapping[str, Any]) -> str:
    return _fingerprint(packet)


def _review_packet_record_index(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for row in packet.get("records") or []:
        if isinstance(row, Mapping):
            record_id = _text(row, "producer_record_id")
            if record_id:
                index[record_id] = row
    return index


def _packet_record_fingerprint(row: Mapping[str, Any]) -> str:
    return _text(row, "record_fingerprint")


def _decision_id(decision: EvidenceDecision, packet_fingerprint: str) -> str:
    return f"care_line_evidence_review_{_fingerprint(decision.identity_dict() | {'review_packet_fingerprint': packet_fingerprint})[:16]}"


def _existing_ledger(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"decision ledger must be an object: {path}")
    if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise ValueError("unsupported decision ledger schema_version")
    return payload


def _ledger_index(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        _text(row, "decision_id"): row
        for row in (payload.get("entries") or payload.get("decisions") or [])
        if isinstance(row, Mapping) and _text(row, "decision_id")
    }


def _status_for_decision(decision: EvidenceDecision) -> str:
    if decision.evidence_decision in {"approved", "corrected"}:
        if decision.evidence_provenance_type in {"source_explicit", "reviewer_transcribed"} and decision.evidence_text:
            return "universal_event_ready"
        return "needs_evidence_review"
    if decision.evidence_decision in {"rejected", "excluded"}:
        return "excluded"
    if decision.evidence_decision == "care_line_only":
        return "care_line_only"
    return "needs_evidence_review"


def _review_status_for_decision(decision: EvidenceDecision) -> str:
    if decision.evidence_decision == "approved":
        return "approved"
    if decision.evidence_decision in {"rejected", "excluded"}:
        return "rejected"
    if decision.evidence_decision == "care_line_only":
        return "reviewed"
    if decision.evidence_decision == "corrected":
        return "corrected"
    return "needs_review"


def _recommendation_for_status(status: str) -> str:
    return {
        "universal_event_ready": "none",
        "needs_evidence_review": "source_transcription_pending",
        "excluded": "none",
        "care_line_only": "none",
    }.get(status, "source_transcription_pending")


def _current_decision_snapshot(record: CareLineReviewedRecord) -> dict[str, Any]:
    evidence_review = record.metadata.get("evidence_review") if isinstance(record.metadata, Mapping) else {}
    if not isinstance(evidence_review, Mapping):
        evidence_review = {}
    return {
        "producer_record_id": record.producer_record_id,
        "record_fingerprint": record.raw_payload_hash,
        "decision": _nested_text(record.metadata, ("evidence_review", "decision")),
        "decision_id": _nested_text(record.metadata, ("evidence_review", "decision_id")) or _text(record.metadata, "evidence_review_decision_id"),
        "reviewed_at": _nested_text(record.metadata, ("evidence_review", "reviewed_at")) or _text(record.metadata, "evidence_review_reviewed_at"),
        "reviewer": _nested_text(record.metadata, ("evidence_review", "reviewer")) or _text(record.metadata, "evidence_review_reviewer"),
        "review_reason": _nested_text(record.metadata, ("evidence_review", "review_reason")) or _text(record.metadata, "evidence_review_review_reason"),
        "source_url": record.source_url,
        "supersedes_decision_id": _nested_text(record.metadata, ("evidence_review", "supersedes_decision_id")),
    }


def _apply_decision(record: CareLineReviewedRecord, decision: EvidenceDecision, *, decision_id: str, packet_fingerprint: str) -> tuple[CareLineReviewedRecord, bool]:
    desired_status = _status_for_decision(decision)
    desired_review_status = _review_status_for_decision(decision)
    current_snapshot = _current_decision_snapshot(record)
    if current_snapshot["decision_id"] == decision_id and record.universal_event_status == desired_status and record.review_status == desired_review_status:
        return record, False
    if (
        record.universal_event_status == desired_status
        and record.review_status == desired_review_status
        and current_snapshot["decision"] == decision.evidence_decision
        and current_snapshot["review_reason"] == decision.review_reason
        and current_snapshot["reviewer"] == decision.reviewer
        and decision.evidence_text == _nested_text(record.metadata, ("evidence_review", "evidence_text"))
    ):
        return record, False

    payload = record.model_dump(mode="json")
    payload["version"] = int(record.version) + 1
    payload["version_id"] = ""
    payload["supersedes_record_id"] = record.version_id
    payload["record_status"] = desired_status
    payload["universal_event_status"] = desired_status
    payload["review_status"] = desired_review_status
    payload["evidence_provenance_type"] = decision.evidence_provenance_type
    payload["evidence_valid_for_universal_event"] = desired_status == "universal_event_ready"
    payload["supporting_passage"] = decision.evidence_text if decision.evidence_decision in {"approved", "corrected"} else ""
    payload["effective_evidence_text"] = decision.evidence_text if decision.evidence_decision in {"approved", "corrected"} else ""
    payload["recommended_status"] = desired_status
    payload["updated_at"] = decision.reviewed_at
    payload["review_notes"] = record.review_notes
    payload["metadata"] = dict(payload.get("metadata") or {})
    payload["metadata"]["evidence_review"] = {
        "decision": decision.evidence_decision,
        "decision_id": decision_id,
        "prior_version_id": record.version_id,
        "record_fingerprint": decision.record_fingerprint,
        "review_reason": decision.review_reason,
        "reviewed_at": decision.reviewed_at,
        "reviewer": decision.reviewer,
        "source_url": decision.evidence_source_url,
        "review_packet_fingerprint": packet_fingerprint,
        "supersedes_decision_id": decision.supersedes_decision_id,
    }
    payload["metadata"]["evidence_review_decision_id"] = decision_id
    payload["metadata"]["evidence_review_reviewed_at"] = decision.reviewed_at
    payload["metadata"]["evidence_review_reviewer"] = decision.reviewer
    payload["metadata"]["evidence_review_review_reason"] = decision.review_reason
    payload["metadata"]["evidence_review_packet_fingerprint"] = packet_fingerprint
    payload["metadata"]["evidence_provenance_type"] = decision.evidence_provenance_type
    payload["metadata"]["evidence_valid_for_universal_event"] = desired_status == "universal_event_ready"
    payload["metadata"]["canonical_export_reason"] = {
        "universal_event_ready": "",
        "needs_evidence_review": "insufficient_evidence",
        "excluded": "evidence_rejected",
        "care_line_only": "non_operational_context",
    }.get(desired_status, "")
    payload["metadata"]["evidence_review_current_status"] = desired_status
    payload["correction_history"] = [
        *record.correction_history,
        {
            "decision": decision.evidence_decision,
            "decision_id": decision_id,
            "prior_version_id": record.version_id,
            "record_fingerprint": decision.record_fingerprint,
            "review_reason": decision.review_reason,
            "reviewed_at": decision.reviewed_at,
            "reviewer": decision.reviewer,
            "source_url": decision.evidence_source_url,
        },
    ]
    updated = CareLineReviewedRecord.model_validate(payload)
    return updated, True


def _validate_supersession(decision: EvidenceDecision, decision_id: str, known_decisions: Mapping[str, str]) -> None:
    if not decision.supersedes_decision_id:
        return
    if decision.supersedes_decision_id == decision_id:
        raise ValueError("self supersession is not allowed")
    if decision.supersedes_decision_id not in known_decisions:
        raise ValueError("invalid supersession target")
    seen = {decision_id}
    current = decision.supersedes_decision_id
    while current:
        if current in seen:
            raise ValueError("supersession cycle detected")
        seen.add(current)
        current = known_decisions.get(current, "")


def _manifest_hash(records: Iterable[CareLineReviewedRecord]) -> str:
    return stable_json_hash([record.deterministic_dict() for record in sorted(records, key=lambda row: (row.producer_record_id, row.version, row.version_id))])


def _build_ledger(
    *,
    packet_path: Path,
    packet: Mapping[str, Any],
    packet_fingerprint: str,
    reviewed_records_path: Path,
    current_records: list[CareLineReviewedRecord],
    decisions: list[EvidenceDecision],
    existing_ledger: Mapping[str, Any] | None,
    applied_records: list[CareLineReviewedRecord],
    changed_count: int,
    duplicate_decisions: int,
    new_decisions_count: int,
    existing_decisions_count: int,
) -> dict[str, Any]:
    current_status_counts = Counter(record.universal_event_status for record in applied_records)
    current_review_status_counts = Counter(record.review_status for record in applied_records)
    ledgers = _ledger_index(existing_ledger or {})
    entries = list(ledgers.values())
    existing_ids = {row.get("decision_id", "") for row in entries}
    for decision in decisions:
        decision_id = _decision_id(decision, packet_fingerprint)
        if decision_id not in existing_ids:
            entries.append(
                {
                    "decision_id": decision_id,
                    "producer_record_id": decision.producer_record_id,
                    "record_fingerprint": decision.record_fingerprint,
                    "evidence_decision": decision.evidence_decision,
                    "evidence_text": decision.evidence_text,
                    "evidence_provenance_type": decision.evidence_provenance_type,
                    "evidence_source_url": decision.evidence_source_url,
                    "evidence_source_field": decision.evidence_source_field,
                    "evidence_source_artifact": decision.evidence_source_artifact,
                    "reviewer": decision.reviewer,
                    "review_reason": decision.review_reason,
                    "reviewed_at": decision.reviewed_at,
                    "supersedes_decision_id": decision.supersedes_decision_id,
                    "review_packet_fingerprint": packet_fingerprint,
                    "effective_universal_event_status": _status_for_decision(decision),
                }
            )
            existing_ids.add(decision_id)
    entries = sorted(entries, key=lambda row: (row["producer_record_id"], row["reviewed_at"], row["decision_id"]))
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "importer_version": IMPORTER_VERSION,
        "review_packet_schema_version": packet.get("schema_version"),
        "review_packet_path": packet_path.as_posix(),
        "review_packet_fingerprint": packet_fingerprint,
        "reviewed_records_path": reviewed_records_path.as_posix(),
        "decision_count": len(entries),
        "decision_counts": dict(sorted(Counter(row["evidence_decision"] for row in entries).items())),
        "current_status_counts": dict(sorted(current_status_counts.items())),
        "current_review_status_counts": dict(sorted(current_review_status_counts.items())),
        "records_examined": len(current_records),
        "new_decisions_count": new_decisions_count,
        "duplicate_decisions_count": duplicate_decisions,
        "existing_decisions_count": existing_decisions_count,
        "new_reviewed_record_versions_count": changed_count,
        "entries": entries,
    }


def _write_report(report_path: Path, report: Mapping[str, Any]) -> None:
    _atomic_write(report_path, _stable_json(report))
    md_path = report_path.with_suffix(".md")
    lines = [
        "# Care Line Phase 14C Evidence Decision Import Report",
        "",
        f"- Schema: `{report.get('schema_version')}`",
        f"- Packet fingerprint: `{report.get('review_packet_fingerprint')}`",
        f"- Reviewed records: `{report.get('records_examined')}`",
        f"- New decisions: `{report.get('new_decisions_count')}`",
        f"- Existing decisions: `{report.get('existing_decisions_count')}`",
        f"- Duplicate decisions: `{report.get('duplicate_decisions_count')}`",
        f"- New reviewed-record versions: `{report.get('new_reviewed_record_versions_count')}`",
        "",
        "## Status counts",
        "",
    ]
    for key, value in sorted((report.get("current_status_counts") or {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Decisions", "", "| producer_record_id | evidence_decision | effective_status | reviewer | review_reason |", "| --- | --- | --- | --- | --- |"])
    for row in report.get("entries") or []:
        lines.append(
            f"| {row.get('producer_record_id')} | {row.get('evidence_decision')} | {row.get('effective_universal_event_status')} | {row.get('reviewer')} | {row.get('review_reason')} |"
        )
    _atomic_write(md_path, "\n".join(lines) + "\n")


def import_evidence_decisions(
    *,
    repo_root: Path,
    review_packet_path: Path,
    decisions_json_path: Path,
    decisions_csv_path: Path,
    reviewed_records_path: Path,
    decision_ledger_path: Path,
    report_path: Path,
    check_only: bool,
    strict: bool,
) -> dict[str, Any]:
    for path in (review_packet_path, decisions_json_path, decisions_csv_path, reviewed_records_path, decision_ledger_path, report_path):
        refuse_public_or_pages_path(path if path.is_absolute() else repo_root / path, repo_root)

    review_packet = load_review_packet(review_packet_path)
    packet_fingerprint = review_packet_fingerprint(review_packet)
    decisions, decisions_payload = load_decisions_payloads(decisions_json_path, decisions_csv_path, strict=strict)
    reviewed_records = load_reviewed_records(reviewed_records_path)
    record_by_id = {record.producer_record_id: record for record in reviewed_records}
    packet_records = _review_packet_record_index(review_packet)
    existing_ledger = _existing_ledger(decision_ledger_path)
    if existing_ledger and existing_ledger.get("review_packet_fingerprint") not in {packet_fingerprint, ""}:
        raise ValueError("stale review packet fingerprint")

    duplicates: list[str] = []
    by_record: dict[str, EvidenceDecision] = {}
    for decision in decisions:
        current = by_record.get(decision.producer_record_id)
        if current is None:
            by_record[decision.producer_record_id] = decision
            continue
        if current.canonical_dict() != decision.canonical_dict():
            raise ValueError("conflicting duplicate decision")
        duplicates.append(decision.producer_record_id)

    current_to_decision_id: dict[str, str] = {}
    for decision in by_record.values():
        current_to_decision_id[decision.producer_record_id] = _decision_id(decision, packet_fingerprint)

    existing_ledger_index = _ledger_index(existing_ledger or {})
    existing_decision_ids = set(existing_ledger_index)
    new_decision_count = sum(1 for decision_id in current_to_decision_id.values() if decision_id not in existing_decision_ids)
    existing_decision_count = sum(1 for decision_id in current_to_decision_id.values() if decision_id in existing_decision_ids)

    for decision in by_record.values():
        record = record_by_id.get(decision.producer_record_id)
        if record is None:
            raise ValueError(f"unknown reviewed record: {decision.producer_record_id}")
        if record.raw_payload_hash != decision.record_fingerprint:
            raise ValueError(f"stale reviewed record fingerprint: {decision.producer_record_id}")
        if existing_ledger_index:
            prior = next((row for row in existing_ledger_index.values() if _text(row, "producer_record_id") == decision.producer_record_id), None)
            if prior and _text(prior, "record_fingerprint") != decision.record_fingerprint:
                raise ValueError("stale record fingerprint")
        packet_row = packet_records.get(decision.producer_record_id)
        if packet_row is None:
            raise ValueError(f"review packet missing producer_record_id: {decision.producer_record_id}")
        if decision.record_fingerprint != _packet_record_fingerprint(packet_row):
            raise ValueError("stale review packet fingerprint")
        if decision.evidence_source_url and _text(packet_row, "canonical_source_url", "evidence_source_url") and decision.evidence_source_url != _text(packet_row, "canonical_source_url", "evidence_source_url"):
            raise ValueError("review packet evidence source URL mismatch")
        if decision.evidence_source_artifact and _text(packet_row, "candidate_source_artifact", "evidence_source_artifact") and decision.evidence_source_artifact not in set(packet_row.get("source_artifact_paths") or []) | {_text(packet_row, "candidate_source_artifact", "evidence_source_artifact")}:
            raise ValueError("review packet evidence source artifact mismatch")

    decision_graph = {decision_id: decision.supersedes_decision_id for decision, decision_id in ((decision, current_to_decision_id[decision.producer_record_id]) for decision in by_record.values())}
    for decision in by_record.values():
        _validate_supersession(decision, current_to_decision_id[decision.producer_record_id], {**{row.get("decision_id", ""): row.get("supersedes_decision_id", "") for row in (existing_ledger or {}).get("decisions") or []}, **decision_graph})

    applied_records = [record for record in reviewed_records]
    changed_count = 0
    effective_decisions: list[dict[str, Any]] = []
    for producer_record_id in sorted(by_record):
        decision = by_record[producer_record_id]
        record = record_by_id[producer_record_id]
        decision_id = current_to_decision_id[producer_record_id]
        updated_record, changed = _apply_decision(record, decision, decision_id=decision_id, packet_fingerprint=packet_fingerprint)
        if changed:
            changed_count += 1
        applied_records = [updated_record if row.producer_record_id == producer_record_id else row for row in applied_records]
        effective_decisions.append(
            {
                "producer_record_id": producer_record_id,
                "decision_id": decision_id,
                "record_fingerprint": decision.record_fingerprint,
                "evidence_decision": decision.evidence_decision,
                "effective_universal_event_status": updated_record.universal_event_status,
                "reviewer": decision.reviewer,
                "review_reason": decision.review_reason,
                "reviewed_at": decision.reviewed_at,
                "evidence_provenance_type": decision.evidence_provenance_type,
                "evidence_source_url": decision.evidence_source_url,
                "supersedes_decision_id": decision.supersedes_decision_id,
                "remaining_action_required": _recommendation_for_status(updated_record.universal_event_status),
                "changed": changed,
            }
        )

    ledger = _build_ledger(
        packet_path=review_packet_path,
        packet=review_packet,
        packet_fingerprint=packet_fingerprint,
        reviewed_records_path=reviewed_records_path,
        current_records=reviewed_records,
        decisions=list(by_record.values()),
        existing_ledger=existing_ledger,
        applied_records=applied_records,
        changed_count=changed_count,
        duplicate_decisions=len(duplicates),
        new_decisions_count=new_decision_count,
        existing_decisions_count=existing_decision_count,
    )

    statuses_after = Counter(record.universal_event_status for record in applied_records)
    statuses_before = Counter(record.universal_event_status for record in reviewed_records)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "importer_version": IMPORTER_VERSION,
        "review_packet_schema_version": review_packet.get("schema_version"),
        "review_packet_fingerprint": packet_fingerprint,
        "reviewed_records_path": reviewed_records_path.as_posix(),
        "decision_ledger_path": decision_ledger_path.as_posix(),
        "report_generated_at": next(iter(by_record.values())).reviewed_at if by_record else "",
        "check_only": check_only,
        "strict": strict,
        "records_examined": len(reviewed_records),
        "decisions_examined": len(decisions),
        "new_decisions_count": 0 if existing_ledger and existing_ledger.get("review_packet_fingerprint") == packet_fingerprint else len(by_record),
        "existing_decisions_count": len(by_record) if existing_ledger else 0,
        "duplicate_decisions_count": len(duplicates),
        "new_reviewed_record_versions_count": changed_count,
        "manifest_hash_before": _manifest_hash(reviewed_records),
        "manifest_hash_after": _manifest_hash(applied_records),
        "statuses_unchanged": statuses_before == statuses_after,
        "status_counts": dict(sorted(statuses_after.items())),
        "reviewed_record_count": len(applied_records),
        "universal_event_ready_count": statuses_after.get("universal_event_ready", 0),
        "needs_evidence_review_count": statuses_after.get("needs_evidence_review", 0),
        "excluded_count": statuses_after.get("excluded", 0),
        "care_line_only_count": statuses_after.get("care_line_only", 0),
        "malformed_count": statuses_after.get("malformed", 0),
        "approved_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "approved"),
        "rejected_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "rejected"),
        "deferred_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "deferred"),
        "care_line_only_decision_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "care_line_only"),
        "excluded_decision_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "excluded"),
        "corrected_count": sum(1 for item in effective_decisions if item["evidence_decision"] == "corrected"),
        "records": sorted(effective_decisions, key=lambda row: row["producer_record_id"]),
        "ledger": ledger,
    }

    if not check_only:
        if changed_count:
            _atomic_write(reviewed_records_path, deterministic_records_json(applied_records))
        _atomic_write(decision_ledger_path, _stable_json(ledger))
    _write_report(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Care Line Phase 14C evidence decisions into reviewed records and an append-only ledger.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--review-packet", required=True)
    parser.add_argument("--decisions-json", required=True)
    parser.add_argument("--decisions-csv", required=True)
    parser.add_argument("--reviewed-records", required=True)
    parser.add_argument("--decision-ledger", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check_only and args.apply:
            raise ValueError("--check-only and --apply are mutually exclusive")
        if not args.check_only and not args.apply:
            raise ValueError("one of --check-only or --apply is required")
        repo_root = Path(args.repo_root).resolve()
        def resolve_repo_path(value: str) -> Path:
            path = Path(value)
            return path if path.is_absolute() else repo_root / path
        report = import_evidence_decisions(
            repo_root=repo_root,
            review_packet_path=resolve_repo_path(args.review_packet),
            decisions_json_path=resolve_repo_path(args.decisions_json),
            decisions_csv_path=resolve_repo_path(args.decisions_csv),
            reviewed_records_path=resolve_repo_path(args.reviewed_records),
            decision_ledger_path=resolve_repo_path(args.decision_ledger),
            report_path=resolve_repo_path(args.report),
            check_only=args.check_only,
            strict=args.strict,
        )
        print(_stable_json(report))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
