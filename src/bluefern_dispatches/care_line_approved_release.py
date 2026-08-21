from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from bluefern_dispatches.care_line_record import CareLineReviewedRecord
from bluefern_dispatches.care_line_release_render import PROPOSAL_SCHEMA, REVIEW_SCHEMA
from bluefern_dispatches.care_line_sources import summary_for_records


APPROVED_REVIEW_STATUSES = {"approved", "reviewed", "corrected"}
REVIEW_ROOT = Path("data") / "dispatches" / "care-line" / "review"
REVIEWED_ROOT = Path("data") / "dispatches" / "care-line" / "reviewed"
APPROVED_PROPOSAL_DIR = REVIEW_ROOT / "proposed-editions"
APPROVED_REVIEW_DIR = REVIEW_ROOT / "signal-reviews"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_reviewed_release_records(repo_root: Path, edition_date: str) -> tuple[Path, list[CareLineReviewedRecord]]:
    reviewed_path = repo_root / REVIEWED_ROOT / edition_date / "reviewed_records.json"
    if not reviewed_path.exists():
        return reviewed_path, []
    payload = _read_json(reviewed_path)
    records = [CareLineReviewedRecord.model_validate(dict(row)) for row in payload.get("records") or [] if isinstance(row, dict)]
    return reviewed_path, records


def _approved_record_key(record: CareLineReviewedRecord) -> str:
    for value in (
        getattr(record, "event_instance_id", ""),
        getattr(record, "event_identity", ""),
        record.producer_record_id,
        record.source_url,
    ):
        text = str(value or "").strip()
        if text:
            return text
    return record.producer_record_id


def select_approved_release_records(records: list[CareLineReviewedRecord]) -> list[CareLineReviewedRecord]:
    selected: list[CareLineReviewedRecord] = []
    seen: set[str] = set()
    for record in sorted(
        records,
        key=lambda row: (
            str(row.source_publication_date or row.review_date or row.announcement_date or ""),
            str(row.source_title or ""),
            str(row.producer_record_id or ""),
        ),
        reverse=True,
    ):
        if record.review_status not in APPROVED_REVIEW_STATUSES:
            continue
        if record.public_status != "public_approved" or record.care_line_public_eligible is not True:
            continue
        if not str(record.source_url or "").strip() or not str(record.source_title or "").strip():
            continue
        key = _approved_record_key(record)
        if key in seen:
            continue
        seen.add(key)
        selected.append(record)
    return selected


def _approved_item(record: CareLineReviewedRecord, *, edition_date: str) -> dict[str, Any]:
    location = (
        str(record.location_text or "").strip()
        or str(record.public_location_label or "").strip()
        or str(record.jurisdiction_display or "").strip()
        or str(record.state or "").strip()
    )
    service_line = str(record.service_line or record.service_line_canonical or "").strip()
    event_type = str(record.event_type or record.canonical_event_type or "").strip()
    access_consequence = str(record.access_consequences[0] if record.access_consequences else "").strip()
    evidence = str(record.supporting_passage or record.effective_evidence_text or "").strip()
    claim = str(record.claim_summary or evidence or record.source_title or "").strip()
    summary = claim
    reviewed_at = str(record.updated_at or record.review_date or record.metadata.get("reviewed_at") or "").strip()
    item = {
        "candidate_id": str(record.producer_record_id or "").strip(),
        "review_item_id": str(record.producer_record_id or "").strip(),
        "source_record_id": str(record.producer_record_id or "").strip(),
        "source_name": str(record.source_publisher or "").strip(),
        "source_title": str(record.source_title or "").strip(),
        "source_url": str(record.source_url or "").strip(),
        "source_date": str(record.source_publication_date or record.announcement_date or edition_date).strip(),
        "reviewed_at": reviewed_at,
        "title": str(record.source_title or "").strip(),
        "url": str(record.source_url or "").strip(),
        "canonical_source_url": str(record.source_url or "").strip(),
        "publisher": str(record.source_publisher or "").strip(),
        "published_at": str(record.source_publication_date or record.announcement_date or edition_date).strip(),
        "source_published_date": str((record.source_publication_date or record.announcement_date or edition_date)[:10]).strip(),
        "retrieved_at": reviewed_at,
        "approved_geography": location,
        "approved_public_claim": claim,
        "bounded_public_summary": summary,
        "claim_supported": claim,
        "summary_or_snippet": summary,
        "pressure_summary": summary,
        "approved_service_line": service_line,
        "approved_event_type": event_type or "service_line_closure",
        "approved_access_consequence": access_consequence,
        "exact_supporting_passage": evidence,
        "evidence_level": str(record.evidence_level or "article_excerpt"),
        "review_status": str(record.review_status or ""),
        "public_status": str(record.public_status or ""),
        "reviewer_identity": str(record.metadata.get("reviewer_identity") or record.metadata.get("reviewer") or "care-line-reviewed-release"),
        "reviewer_rationale": str(record.review_notes or record.verification_notes or record.claim_summary or "").strip(),
        "role_in_edition": "core_access_signal",
        "notes": str(record.review_notes or record.verification_notes or "").strip(),
        "source_role": str(record.source_role or "").strip(),
        "source_family": str(record.source_publisher or "").strip(),
        "source_type": str(record.source_type or "").strip(),
        "source_purpose": "current_news",
        "collector_source_type": "manual_review",
        "traceability_status": "traceable",
        "included": True,
        "included_as_lead": True,
        "public_claim_eligible": True,
        "source_public_story_eligible": True,
        "qualifies_for_public_inclusion": True,
        "public_inclusion_reason": "",
        "public_inclusion_bucket": "Hospital / Clinic Operations Signals",
        "supported_product_geography": True,
        "primary_eligible": True,
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
        "source_adequacy_label": "Limited-source update",
        "public_edition_title": "Care Line limited-source update",
        "public_summary": summary,
        "pressure_signal": True,
        "pressure_reason": summary or claim or "approved source-backed access-pressure signal",
        "pressure_type": event_type or "service_line_closure",
        "pressure_verification_status": "source_text_verified",
        "confidence": "high",
        "location_name": location,
        "location_scope": location,
        "state": str(record.state or "").strip(),
        "freshness_status": "current",
        "source_freshness_status": "current",
        "source_freshness_date_basis": "source_published_at",
        "freshness_role": "current",
        "map_eligible": False,
        "exclusion_reason": "",
        "public_claim_blockers": [],
    }
    return item


def build_approved_release_artifacts(repo_root: Path, edition_date: str) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    reviewed_path, reviewed_records = load_reviewed_release_records(repo_root, edition_date)
    approved_records = select_approved_release_records(reviewed_records)
    approved_items = [_approved_item(record, edition_date=edition_date) for record in approved_records]
    if not approved_items:
        return {
            "ok": True,
            "status": "no_approved_release",
            "release_ready": False,
            "edition_date": edition_date,
            "reviewed_record_path": reviewed_path.as_posix(),
            "reviewed_record_sha256": _sha256(reviewed_path) if reviewed_path.exists() else None,
            "approved_signal_ids": [],
            "approved_item_count": 0,
            "publisher_count": 0,
            "source_count": 0,
            "proposal_path": None,
            "proposal_sha256": None,
            "review_snapshot_path": None,
            "review_snapshot_sha256": None,
            "proposal": None,
            "review_snapshot": None,
            "approved_items": [],
        }
    approved_signal_ids = [str(item["candidate_id"]) for item in approved_items]
    publishers = sorted({str(item.get("source_name") or "") for item in approved_items if str(item.get("source_name") or "").strip()})
    summary = summary_for_records(approved_items)
    proposal = {
        "schema_version": PROPOSAL_SCHEMA,
        "edition_date": edition_date,
        "edition_mode": "current_update",
        "headline": "Care Line limited-source update",
        "edition_summary": summary,
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
        "source_adequacy_label": "Limited-source update",
        "release_ready": True,
        "source_count": len(approved_items),
        "public_developments": len(approved_items),
        "publisher_count": len(publishers),
        "approved_signal_ids": approved_signal_ids,
        "reviewed_record_path": reviewed_path.as_posix(),
        "reviewed_record_sha256": _sha256(reviewed_path) if reviewed_path.exists() else None,
    }
    reviewed_at = approved_items[0]["reviewed_at"]
    if not reviewed_at and reviewed_records:
        reviewed_at = reviewed_records[0].updated_at
    review_snapshot = {
        "schema_version": REVIEW_SCHEMA,
        "edition_date": edition_date,
        "reviewed_at": reviewed_at,
        "release_ready": True,
        "reviewed_record_path": reviewed_path.as_posix(),
        "reviewed_record_sha256": _sha256(reviewed_path) if reviewed_path.exists() else None,
        "review_payload": {
            "edition_date": edition_date,
            "items": approved_items,
        },
    }
    proposal_path = repo_root / APPROVED_PROPOSAL_DIR / f"{edition_date}.json"
    review_snapshot_path = repo_root / APPROVED_REVIEW_DIR / f"{edition_date}.json"
    _write_json(proposal_path, proposal)
    _write_json(review_snapshot_path, review_snapshot)
    return {
        "ok": True,
        "status": "approved_release_written",
        "release_ready": True,
        "edition_date": edition_date,
        "reviewed_record_path": reviewed_path.as_posix(),
        "reviewed_record_sha256": proposal["reviewed_record_sha256"],
        "approved_signal_ids": approved_signal_ids,
        "approved_item_count": len(approved_items),
        "publisher_count": len(publishers),
        "source_count": len(approved_items),
        "proposal_path": proposal_path.as_posix(),
        "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "review_snapshot_path": review_snapshot_path.as_posix(),
        "review_snapshot_sha256": hashlib.sha256(review_snapshot_path.read_bytes()).hexdigest(),
        "proposal": proposal,
        "review_snapshot": review_snapshot,
        "approved_items": approved_items,
    }
