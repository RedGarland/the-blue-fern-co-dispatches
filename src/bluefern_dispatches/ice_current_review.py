from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_SCHEMA = "bluefern.ice.monitor.review_queue.v1"
DECISION_SCHEMA = "bluefern.ice.monitor.review_decision.v1"
RELEASE_CANDIDATE_SCHEMA = "bluefern.ice.monitor.release_candidate.v1"
ALLOWED_DECISIONS = {
    "APPROVE_FOR_RELEASE_REVIEW",
    "REJECT",
    "DEFER",
    "NEEDS_CORROBORATION",
}
TARGET_STATUS = {
    "APPROVE_FOR_RELEASE_REVIEW": "REVIEWED",
    "REJECT": "REJECTED",
    "DEFER": "NEEDS_REVIEW",
    "NEEDS_CORROBORATION": "NEEDS_CORROBORATION",
}
ACTIVE_STATUSES = {"NEW", "NEEDS_REVIEW", "NEEDS_CORROBORATION", "DUPLICATE_UPDATED"}


class IceReviewError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_queue(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != QUEUE_SCHEMA:
        raise IceReviewError("unsupported ICE review queue schema")
    items = payload.get("items")
    if not isinstance(items, list):
        raise IceReviewError("ICE review queue items must be a list")
    fingerprints: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            raise IceReviewError("ICE review queue item must be an object")
        fingerprint = str(row.get("event_fingerprint") or "").strip()
        if not fingerprint:
            raise IceReviewError("ICE review queue item missing event_fingerprint")
        if fingerprint in fingerprints:
            raise IceReviewError(f"duplicate ICE review queue fingerprint: {fingerprint}")
        fingerprints.add(fingerprint)
    return payload


def queue_summary(queue: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in queue.get("items") or []:
        status = str(row.get("review_status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return {
        "ok": True,
        "queue_sha256": payload_sha256(queue),
        "item_count": len(queue.get("items") or []),
        "status_counts": dict(sorted(counts.items())),
        "publication_authorized": False,
    }


def _safe_evidence_path(root: Path, value: Any) -> Path:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ".." in Path(text).parts:
        raise IceReviewError("unsafe ICE evidence reference")
    if not text.startswith("data/dispatches/ice/monitor/runs/"):
        raise IceReviewError("ICE evidence reference is outside monitor runs")
    path = (root / text).resolve()
    monitor_root = (root / "data/dispatches/ice/monitor/runs").resolve()
    if monitor_root not in path.parents:
        raise IceReviewError("ICE evidence reference escapes monitor runs")
    if not path.is_file():
        raise IceReviewError(f"ICE evidence reference does not exist: {text}")
    return path


def _verify_canonical_event(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    refs = item.get("evidence_references") if isinstance(item.get("evidence_references"), dict) else {}
    canonical_path = _safe_evidence_path(root, refs.get("canonical_events"))
    payload = json.loads(canonical_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise IceReviewError("canonical_events evidence must be a list")
    event_id = str(item.get("canonical_event_id") or "").strip()
    matches = [row for row in payload if isinstance(row, dict) and str(row.get("event_id") or "").strip() == event_id]
    if len(matches) != 1:
        raise IceReviewError("ICE queue item does not resolve to exactly one canonical event")
    return matches[0]


def _release_candidate(root: Path, item: dict[str, Any], decision: dict[str, Any], event: dict[str, Any]) -> Path:
    event_id = str(item["canonical_event_id"])
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in event_id)[:180]
    path = root / "data/dispatches/ice/monitor/release-candidates" / f"{safe_id}.json"
    payload = {
        "schema_version": RELEASE_CANDIDATE_SCHEMA,
        "canonical_event_id": event_id,
        "event_fingerprint": item["event_fingerprint"],
        "review_decision_id": decision["decision_id"],
        "reviewed_at": decision["decided_at"],
        "reviewed_by": decision["decided_by"],
        "category": item.get("category"),
        "severity": item.get("severity"),
        "event_date": item.get("event_date"),
        "geography": item.get("geography") or {},
        "source_set": item.get("source_set") or [],
        "source_tiers": item.get("source_tiers") or [],
        "corroboration_status": item.get("corroboration_status"),
        "canonical_event_snapshot_sha256": payload_sha256(event),
        "evidence_references": item.get("evidence_references") or {},
        "release_review_ready": True,
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        if existing == payload:
            return path
        raise IceReviewError("conflicting ICE release candidate already exists")
    _write_json(path, payload)
    return path


def apply_decision(
    root: Path,
    queue_path: Path,
    *,
    event_fingerprint: str,
    decision: str,
    decided_by: str,
    rationale: str,
    expected_queue_sha256: str,
    decided_at: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    queue_path = queue_path.resolve()
    queue = load_queue(queue_path)
    before_sha = payload_sha256(queue)
    if before_sha != expected_queue_sha256:
        raise IceReviewError("ICE review queue changed since inspection")

    normalized = decision.strip().upper()
    if normalized not in ALLOWED_DECISIONS:
        raise IceReviewError(f"unsupported ICE review decision: {decision}")
    if not decided_by.strip() or not rationale.strip():
        raise IceReviewError("decided_by and rationale are required")

    matches = [row for row in queue["items"] if row.get("event_fingerprint") == event_fingerprint]
    if len(matches) != 1:
        raise IceReviewError("ICE review target must resolve to exactly one queue item")
    item = matches[0]
    target = TARGET_STATUS[normalized]

    if (
        item.get("review_status") == target
        and item.get("review_decision") == normalized
        and item.get("reviewed_by") == decided_by.strip()
        and item.get("review_rationale") == rationale.strip()
    ):
        return {
            "ok": True,
            "status": "idempotent_noop",
            "queue_sha256": before_sha,
            "review_status": target,
            "publication_authorized": False,
        }

    current = str(item.get("review_status") or "")
    if current not in ACTIVE_STATUSES:
        raise IceReviewError(f"ICE queue item is already terminal: {current}")

    event = _verify_canonical_event(root, item)
    if normalized == "APPROVE_FOR_RELEASE_REVIEW":
        if item.get("editorial_eligibility_candidate") is not True:
            raise IceReviewError("ICE item is not an editorial eligibility candidate")
        if item.get("corroboration_status") != "corroborated":
            raise IceReviewError("ICE item requires corroboration before release review")
        if not item.get("source_set"):
            raise IceReviewError("ICE item has no traceable source set")

    decided_at = decided_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    decision_id = hashlib.sha256(
        f"{event_fingerprint}|{normalized}|{decided_by.strip()}|{rationale.strip()}".encode("utf-8")
    ).hexdigest()[:24]
    audit = {
        "schema_version": DECISION_SCHEMA,
        "decision_id": decision_id,
        "canonical_event_id": item.get("canonical_event_id"),
        "event_fingerprint": event_fingerprint,
        "previous_review_status": current,
        "decision": normalized,
        "resulting_review_status": target,
        "decided_by": decided_by.strip(),
        "decided_at": decided_at,
        "rationale": rationale.strip(),
        "queue_sha256_before": before_sha,
        "canonical_event_snapshot_sha256": payload_sha256(event),
        "release_candidate_created": normalized == "APPROVE_FOR_RELEASE_REVIEW",
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
    }

    item["review_status"] = target
    item["review_decision"] = normalized
    item["reviewed_by"] = decided_by.strip()
    item["reviewed_at"] = decided_at
    item["review_rationale"] = rationale.strip()
    item["review_decision_id"] = decision_id
    if normalized != "APPROVE_FOR_RELEASE_REVIEW":
        item["release_candidate_ref"] = None

    release_path = None
    if normalized == "APPROVE_FOR_RELEASE_REVIEW":
        release_path = _release_candidate(root, item, audit, event)
        item["release_candidate_ref"] = release_path.relative_to(root).as_posix()

    audit_root = root / "data/dispatches/ice/monitor/reviews" / decided_at[:10]
    audit_path = audit_root / f"{decision_id}.json"
    if audit_path.exists():
        existing = json.loads(audit_path.read_text(encoding="utf-8-sig"))
        if existing != audit:
            raise IceReviewError("conflicting ICE review audit already exists")
    else:
        _write_json(audit_path, audit)

    _write_json(queue_path, queue)
    return {
        "ok": True,
        "status": "decision_recorded",
        "review_status": target,
        "decision_id": decision_id,
        "audit_ref": audit_path.relative_to(root).as_posix(),
        "release_candidate_ref": release_path.relative_to(root).as_posix() if release_path else None,
        "queue_sha256_before": before_sha,
        "queue_sha256_after": payload_sha256(queue),
        "publication_authorized": False,
    }
