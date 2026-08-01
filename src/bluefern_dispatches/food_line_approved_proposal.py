from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit


APPROVED_DRAFT_STATUS = "draft_approved_pending_publication"
APPROVED_DECISIONS = {"approve", "approve_with_edit"}
ACCEPTED_FRESHNESS_STATUSES = {"current", "accepted", "within_window"}
PROPOSAL_SCHEMA = "food_line_proposed_edition_v1"
QUEUE_SCHEMA = "food_line_current_signal_review_v1"
RELEASE_SCHEMA = "food_line_release_manifest_v1"


@dataclass(frozen=True)
class ApprovedProposalBundle:
    proposal_path: Path
    proposal_sha256: str
    queue_path: Path
    queue_sha256: str
    proposal: dict[str, Any]
    queue: dict[str, Any]
    matched_items: tuple[tuple[dict[str, Any], dict[str, Any]], ...]
    source_rows: tuple[dict[str, Any], ...]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _relative_private_path(root: Path, raw_path: str, *, expected_prefix: str, label: str) -> tuple[Path, str]:
    normalized = str(raw_path or "").strip().replace("\\", "/").lstrip("./")
    if not normalized or not normalized.startswith(expected_prefix):
        raise ValueError(f"{label} must be under {expected_prefix}")
    if any(part in {"agent-history", "agent-history-staging", "history"} for part in normalized.split("/")):
        raise ValueError(f"{label} must not reference historical data")
    resolved_root = root.resolve()
    resolved = (resolved_root / normalized).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside the source repository") from exc
    return resolved, normalized


def _require_https(value: Any, label: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be a canonical HTTPS URL")
    return url


def _url_identity(value: Any) -> str:
    url = _require_https(value, "source URL")
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return f"https://{parsed.netloc.lower()}{path}"


def _require_iso_timestamp(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def _require_count(payload: dict[str, Any], name: str, expected: int) -> None:
    if payload.get(name) != expected:
        raise ValueError(f"approved proposal {name} must equal {expected}")


def _matching_queue_item(proposal_item: dict[str, Any], queue_items: Sequence[Any]) -> dict[str, Any]:
    matches = [
        item
        for item in queue_items
        if isinstance(item, dict)
        and item.get("proposed_rank") == proposal_item.get("rank")
        and str(item.get("proposed_public_headline") or "").strip() == str(proposal_item.get("headline") or "").strip()
        and _url_identity(item.get("canonical_source_url") or item.get("source_url")) == _url_identity(proposal_item.get("source_url"))
    ]
    if len(matches) != 1:
        raise ValueError("approved proposal item does not resolve to exactly one review-queue identity")
    return matches[0]


def _validate_item(proposal_item: dict[str, Any], queue_item: dict[str, Any]) -> None:
    decision = str(queue_item.get("editorial_status") or "").strip()
    audit = queue_item.get("decision_audit")
    if decision not in APPROVED_DECISIONS:
        raise ValueError("every selected item must have editorial_status approve or approve_with_edit")
    if not isinstance(audit, dict) or str(audit.get("decision") or "").strip() != decision:
        raise ValueError("selected item decision audit must match editorial_status")
    if not str(audit.get("decided_by") or "").strip():
        raise ValueError("selected item decision operator is required")
    _require_iso_timestamp(audit.get("decided_at"), "selected item decision timestamp")
    if not str(queue_item.get("review_item_id") or "").strip():
        raise ValueError("selected item review identity is required")
    if not str(queue_item.get("source_finding_or_intake_id") or "").strip():
        raise ValueError("selected item finding identity is required")
    _require_https(queue_item.get("source_url"), "selected item source_url")
    _require_https(queue_item.get("canonical_source_url"), "selected item canonical_source_url")
    if not str(queue_item.get("exact_supporting_passage") or "").strip():
        raise ValueError("selected item exact supporting evidence is required")
    duplicate = queue_item.get("duplicate_check")
    if not isinstance(duplicate, dict) or duplicate.get("status") != "not_published":
        raise ValueError("selected item duplicate status must be not_published")
    freshness = queue_item.get("freshness_check")
    if not isinstance(freshness, dict) or str(freshness.get("status") or "").strip() not in ACCEPTED_FRESHNESS_STATUSES:
        raise ValueError("selected item freshness status is not accepted")
    artifact = str(queue_item.get("source_artifact_path") or "").replace("\\", "/").lstrip("./")
    if not artifact.startswith("data/dispatches/food-line/agent-intake/") or "history" in artifact.lower():
        raise ValueError("selected item must come from current nonhistorical Food Line intake")
    if queue_item.get("publication_eligible") is not False:
        raise ValueError("selected item publication_eligible must remain false before final publication")
    exact_pairs = (
        ("headline", "proposed_public_headline"),
        ("summary", "proposed_public_summary"),
        ("why_it_matters", "why_it_matters"),
        ("uncertainty_note", "uncertainty_note"),
        ("source_published_at", "source_published_at"),
    )
    for proposal_key, queue_key in exact_pairs:
        if str(proposal_item.get(proposal_key) or "").strip() != str(queue_item.get(queue_key) or "").strip():
            raise ValueError(f"approved proposal {proposal_key} does not match its review-queue record")


def _source_row(proposal_item: dict[str, Any], queue_item: dict[str, Any]) -> dict[str, Any]:
    source_url = _require_https(queue_item.get("source_url"), "selected item source_url")
    canonical_source_url = _require_https(queue_item.get("canonical_source_url"), "selected item canonical_source_url")
    public_id = "food_line_source_" + hashlib.sha256(_url_identity(canonical_source_url).encode("utf-8")).hexdigest()[:16]
    summary = str(proposal_item.get("summary") or "").strip()
    evidence = str(queue_item.get("exact_supporting_passage") or "").strip()
    location = str(queue_item.get("location_scope") or "").strip()
    return {
        "source_record_id": public_id,
        "title": str(proposal_item.get("headline") or "").strip(),
        "url": source_url,
        "canonical_source_url": canonical_source_url,
        "publisher": str(proposal_item.get("source") or queue_item.get("publisher") or "").strip(),
        "published_at": str(proposal_item.get("source_published_at") or "").strip(),
        "source_published_date": str(proposal_item.get("source_published_at") or "").strip(),
        "retrieved_at": str(queue_item.get("decision_audit", {}).get("decided_at") or "").strip(),
        "summary_or_snippet": summary,
        "pressure_summary": summary,
        "approved_public_summary": summary,
        "approved_why_it_matters": str(proposal_item.get("why_it_matters") or "").strip(),
        "approved_uncertainty_note": str(proposal_item.get("uncertainty_note") or "").strip(),
        "evidence_text": evidence,
        "exact_supporting_passage": evidence,
        "evidence_text_basis": "operator_reviewed_exact_passage",
        "evidence_level": str(queue_item.get("evidence_level") or "direct reporting").strip(),
        "confidence": str(queue_item.get("confidence") or "high").strip(),
        "pressure_signal": True,
        "pressure_verification_status": "source_text_verified",
        "pressure_type": str(queue_item.get("pressure_type") or "service reduction").strip(),
        "pressure_reason": "approved current-review source documents a food-access pressure signal",
        "pressure_match_terms": ["pantry", "closed", "distributed"],
        "source_role": "local_signal",
        "source_family": "local_news",
        "source_type": "approved_proposal",
        "source_purpose": "current_news",
        "collector_source_type": "manual_review",
        "location_name": location,
        "state": str(proposal_item.get("state") or queue_item.get("state") or "").strip(),
        "location_scope": "local",
        "affected_groups": list(queue_item.get("affected_groups") or []),
        "supported_product_geography": True,
        "source_public_story_eligible": True,
        "freshness_status": "current",
        "source_freshness_status": "current",
        "source_freshness_date_basis": "source_published_at",
        "freshness_role": "current",
        "primary_eligible": True,
        "qualifies_for_public_inclusion": True,
        "public_inclusion_reason": "",
        "public_inclusion_bucket": "included_as_lead",
        "included": True,
        "included_as_lead": True,
        "review_status": "approved",
        "traceability_status": "traceable",
        "public_claim_eligible": True,
        "public_claim_blockers": [],
        "map_eligible": False,
    }


def load_approved_proposal(root: Path, proposal_path: Path | str, edition_date: str) -> ApprovedProposalBundle:
    root = root.resolve()
    raw_proposal = str(proposal_path)
    candidate = Path(raw_proposal)
    if candidate.is_absolute():
        try:
            proposal_rel = candidate.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("approved proposal must be inside the source repository") from exc
    else:
        proposal_rel = raw_proposal.replace("\\", "/").lstrip("./")
    resolved_proposal, _ = _relative_private_path(
        root,
        proposal_rel,
        expected_prefix="data/dispatches/food-line/review/proposed-editions/",
        label="approved proposal",
    )
    proposal = _read_object(resolved_proposal, "approved proposal")
    if proposal.get("schema_version") != PROPOSAL_SCHEMA:
        raise ValueError(f"approved proposal schema_version must be {PROPOSAL_SCHEMA}")
    if proposal.get("edition_date") != edition_date:
        raise ValueError("approved proposal date must equal the requested edition date")
    if proposal.get("draft_status") != APPROVED_DRAFT_STATUS:
        raise ValueError(f"approved proposal draft_status must be {APPROVED_DRAFT_STATUS}")
    if proposal.get("publication_approval") is not False or proposal.get("published") is not False:
        raise ValueError("approved proposal must remain unpublished and lack final publication approval")
    if proposal.get("publication_eligible") is not False:
        raise ValueError("approved proposal publication_eligible must remain false before final publication")
    items = proposal.get("items")
    if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
        raise ValueError("approved proposal must contain at least one selected item")
    _require_count(proposal, "selected_item_count", len(items))
    _require_count(proposal, "approved_item_count", len(items))
    _require_count(proposal, "pending_item_count", 0)
    _require_count(proposal, "rejected_item_count", 0)

    queue_path, _ = _relative_private_path(
        root,
        str(proposal.get("source_queue_path") or ""),
        expected_prefix="data/dispatches/food-line/review/",
        label="review queue",
    )
    if queue_path.name != "current-signal-review.json":
        raise ValueError("approved proposal must reference the current Food Line review queue")
    queue_sha = sha256_file(queue_path)
    if str(proposal.get("source_queue_sha256") or "").lower() != queue_sha:
        raise ValueError("approved proposal review-queue SHA-256 is stale")
    queue = _read_object(queue_path, "review queue")
    if queue.get("schema_version") != QUEUE_SCHEMA or queue.get("edition_date") != edition_date:
        raise ValueError("review queue schema or edition date does not match the approved proposal")
    if queue.get("production_scope") != "current_nonhistorical_only":
        raise ValueError("review queue must be current_nonhistorical_only")
    queue_items = queue.get("items")
    if not isinstance(queue_items, list):
        raise ValueError("review queue items must be a list")

    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    rows: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    seen_finding_ids: set[str] = set()
    for proposal_item in items:
        queue_item = _matching_queue_item(proposal_item, queue_items)
        _validate_item(proposal_item, queue_item)
        review_id = str(queue_item.get("review_item_id") or "").strip()
        finding_id = str(queue_item.get("source_finding_or_intake_id") or "").strip()
        if review_id in seen_review_ids or finding_id in seen_finding_ids:
            raise ValueError("approved proposal contains duplicate review or finding identities")
        seen_review_ids.add(review_id)
        seen_finding_ids.add(finding_id)
        matched.append((proposal_item, queue_item))
        rows.append(_source_row(proposal_item, queue_item))

    return ApprovedProposalBundle(
        proposal_path=resolved_proposal,
        proposal_sha256=sha256_file(resolved_proposal),
        queue_path=queue_path,
        queue_sha256=queue_sha,
        proposal=proposal,
        queue=queue,
        matched_items=tuple(matched),
        source_rows=tuple(rows),
    )


def build_release_manifest(
    *,
    root: Path,
    pages_root: Path,
    edition_date: str,
    source_commit: str,
    source_paths: Sequence[Path],
) -> dict[str, Any]:
    root = root.resolve()
    pages_root = pages_root.resolve()
    entries: list[dict[str, Any]] = []
    for source_path in sorted({path.resolve() for path in source_paths}, key=lambda path: path.as_posix()):
        source_rel = source_path.relative_to(root).as_posix()
        if not source_rel.startswith("output/site/food-line/"):
            raise ValueError(f"release source path is outside Food Line public output: {source_rel}")
        pages_rel = source_rel.removeprefix("output/site/")
        target = pages_root / pages_rel
        source_sha = sha256_file(source_path)
        if not target.exists():
            action = "add"
            target_sha = None
        else:
            target_sha = sha256_file(target)
            action = "unchanged" if target_sha == source_sha else "modify"
        entries.append(
            {
                "source_path": source_rel,
                "pages_path": pages_rel,
                "action": action,
                "source_sha256": source_sha,
                "pages_sha256_before": target_sha,
            }
        )
    return {
        "schema_version": RELEASE_SCHEMA,
        "dispatch": "food-line",
        "edition_date": edition_date,
        "source_commit": source_commit,
        "entries": entries,
        "deletions": [],
        "shared_files": [],
    }


def write_json_deterministic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
