from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROPOSAL_SCHEMA = "bluefern.care_line.proposed_edition.v1"
REVIEW_SCHEMA = "bluefern.care_line.review_snapshot.v2"


@dataclass(frozen=True)
class CareLineApprovedReleaseBundle:
    proposal_path: Path
    proposal_sha256: str
    review_snapshot_path: Path
    review_snapshot_sha256: str
    proposal: dict[str, Any]
    review_snapshot: dict[str, Any]
    approved_items: tuple[dict[str, Any], ...]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_approved_release(root: Path, edition_date: str) -> CareLineApprovedReleaseBundle | None:
    root = root.resolve()
    proposal_path = root / "data" / "dispatches" / "care-line" / "review" / "proposed-editions" / f"{edition_date}.json"
    review_snapshot_path = root / "data" / "dispatches" / "care-line" / "review" / "signal-reviews" / f"{edition_date}.json"
    if not proposal_path.exists() or not review_snapshot_path.exists():
        return None
    proposal = _read_json(proposal_path)
    review_snapshot = _read_json(review_snapshot_path)
    if proposal.get("schema_version") != PROPOSAL_SCHEMA:
        raise ValueError(f"approved Care Line proposal schema_version must be {PROPOSAL_SCHEMA}")
    if review_snapshot.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError(f"Care Line review snapshot schema_version must be {REVIEW_SCHEMA}")
    if proposal.get("edition_date") != edition_date or review_snapshot.get("edition_date") != edition_date:
        raise ValueError("Care Line approved release artifacts must match the requested edition date")
    approved_ids = [str(item) for item in proposal.get("approved_signal_ids") or [] if str(item).strip()]
    review_items = review_snapshot.get("review_payload", {}).get("items")
    if not isinstance(review_items, list):
        raise ValueError("Care Line review snapshot must contain review_payload.items")
    indexed = {
        str(item.get("candidate_id") or ""): item
        for item in review_items
        if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
    }
    approved_items: list[dict[str, Any]] = []
    for candidate_id in approved_ids:
        item = indexed.get(candidate_id)
        if item is None:
            raise ValueError(f"approved Care Line proposal references missing candidate_id: {candidate_id}")
        approved_items.append(dict(item))
    if not approved_items:
        raise ValueError("approved Care Line proposal must contain at least one approved signal")
    return CareLineApprovedReleaseBundle(
        proposal_path=proposal_path,
        proposal_sha256=_sha256(proposal_path),
        review_snapshot_path=review_snapshot_path,
        review_snapshot_sha256=_sha256(review_snapshot_path),
        proposal=proposal,
        review_snapshot=review_snapshot,
        approved_items=tuple(approved_items),
    )


def build_public_rows(bundle: CareLineApprovedReleaseBundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in bundle.approved_items:
        source_name = str(item.get("source_name") or "").strip()
        source_title = str(item.get("source_title") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        source_date = str(item.get("source_date") or item.get("review_date") or bundle.proposal.get("edition_date") or "").strip()
        geographic = str(item.get("approved_geography") or "").strip()
        claim = str(item.get("approved_public_claim") or "").strip()
        summary = str(item.get("bounded_public_summary") or "").strip()
        service_line = str(item.get("approved_service_line") or "").strip()
        event_type = str(item.get("approved_event_type") or "").strip()
        access_consequence = str(item.get("approved_access_consequence") or "").strip()
        rows.append(
            {
                "source_record_id": str(item.get("candidate_id") or ""),
                "title": source_title,
                "url": source_url,
                "canonical_source_url": source_url,
                "publisher": source_name,
                "published_at": source_date,
                "retrieved_at": str(item.get("reviewed_at") or bundle.review_snapshot.get("reviewed_at") or ""),
                "summary_or_snippet": summary or claim,
                "pressure_summary": summary or claim,
                "claim_supported": claim,
                "approved_public_claim": claim,
                "approved_why_it_matters": summary,
                "approved_uncertainty_note": str(item.get("notes") or "").strip(),
                "evidence_text": str(item.get("exact_supporting_passage") or "").strip(),
                "exact_supporting_passage": str(item.get("exact_supporting_passage") or "").strip(),
                "evidence_text_basis": "operator_reviewed_exact_passage",
                "evidence_level": str(item.get("evidence_level") or "article_excerpt"),
                "confidence": "high",
                "pressure_signal": True,
                "pressure_verification_status": "source_text_verified",
                "pressure_type": event_type or "service_line_closure",
                "pressure_reason": summary or claim or "approved source-backed access-pressure signal",
                "pressure_match_terms": [service_line, geographic, access_consequence],
                "source_role": "hospital_operations_signal",
                "source_family": source_name,
                "source_type": "approved_proposal",
                "source_purpose": "current_news",
                "collector_source_type": "manual_review",
                "location_name": geographic,
                "state": geographic.split(",")[-1].strip() if "," in geographic else geographic,
                "location_scope": geographic,
                "affected_groups": [geographic] if geographic else [],
                "supported_product_geography": True,
                "source_public_story_eligible": True,
                "freshness_status": "current",
                "source_freshness_status": "current",
                "source_freshness_date_basis": "source_published_at",
                "freshness_role": "current",
                "primary_eligible": True,
                "qualifies_for_public_inclusion": True,
                "public_inclusion_reason": "",
                "public_inclusion_bucket": "Hospital / Clinic Operations Signals",
                "included": True,
                "included_as_lead": True,
                "review_status": "approved",
                "traceability_status": "traceable",
                "public_claim_eligible": True,
                "public_claim_blockers": [],
                "map_eligible": False,
                "exclusion_reason": "",
                "limitations": str(item.get("notes") or "").strip() or "Bounded public claim approved from the Care Line review snapshot.",
                "public_summary": summary or claim,
                "public_story_title": source_title,
                "public_edition_title": str(bundle.proposal.get("headline") or "Care Line limited-source update"),
                "source_adequacy_status": str(bundle.proposal.get("source_adequacy_status") or "LIMITED_SOURCE_UPDATE"),
                "source_adequacy_label": str(bundle.proposal.get("source_adequacy_label") or "Limited-source update"),
            }
        )
    return rows


def is_limited_source_release(bundle: CareLineApprovedReleaseBundle) -> bool:
    return str(bundle.proposal.get("source_adequacy_status") or "").strip().upper() == "LIMITED_SOURCE_UPDATE"
