from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ice_current_review import (
    IceReviewError,
    load_queue,
    payload_sha256,
)

BUNDLE_SCHEMA = "bluefern.ice.monitor.release_bundle.v1"
RELEASE_CANDIDATE_SCHEMA = "bluefern.ice.monitor.release_candidate.v1"
ALLOWED_REVIEW_STATUSES = {"REVIEWED"}


class IceReleaseBundleError(IceReviewError):
    pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_repo_path(root: Path, value: Any, *, prefix: str, file_required: bool = True) -> Path:
    text = str(value or "").replace("\\", "/").strip()
    if not text or ".." in Path(text).parts:
        raise IceReleaseBundleError("unsafe ICE bundle evidence reference")
    candidate = Path(text)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    allowed_root = (root / prefix).resolve()
    if allowed_root != path and allowed_root not in path.parents:
        raise IceReleaseBundleError("ICE bundle evidence reference is outside allowed root")
    if file_required and not path.is_file():
        raise IceReleaseBundleError(f"ICE bundle evidence file does not exist: {text}")
    return path


def _load_release_candidate(root: Path, item: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    ref = item.get("release_candidate_ref")
    path = _safe_repo_path(root, ref, prefix="data/dispatches/ice/monitor/release-candidates")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != RELEASE_CANDIDATE_SCHEMA:
        raise IceReleaseBundleError("unsupported ICE release candidate schema")
    if payload.get("event_fingerprint") != item.get("event_fingerprint"):
        raise IceReleaseBundleError("ICE release candidate fingerprint mismatch")
    if payload.get("canonical_event_id") != item.get("canonical_event_id"):
        raise IceReleaseBundleError("ICE release candidate event identity mismatch")
    if payload.get("review_decision_id") != item.get("review_decision_id"):
        raise IceReleaseBundleError("ICE release candidate review decision mismatch")
    if payload.get("publication_authorized") is not False or payload.get("publication_approval") is not False:
        raise IceReleaseBundleError("ICE release candidate unexpectedly carries publication authority")
    return path, payload


def _load_reviewed_event(root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    refs = candidate.get("evidence_references") if isinstance(candidate.get("evidence_references"), dict) else {}
    path = _safe_repo_path(root, refs.get("canonical_events"), prefix="data/dispatches/ice/monitor/runs")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise IceReleaseBundleError("ICE canonical event evidence must be a list")
    event_id = str(candidate.get("canonical_event_id") or "")
    matches = [row for row in payload if isinstance(row, dict) and str(row.get("event_id") or "") == event_id]
    if len(matches) != 1:
        raise IceReleaseBundleError("ICE release candidate does not resolve to exactly one canonical event")
    event = dict(matches[0])
    if payload_sha256(event) != candidate.get("canonical_event_snapshot_sha256"):
        raise IceReleaseBundleError("ICE canonical event changed since review approval")

    relationship = str(candidate.get("relationship_to_previous") or "")
    lineage = event.get("lineage") if isinstance(event.get("lineage"), dict) else {}
    if lineage.get("supersedes"):
        review_status = "CORRECTION"
    elif relationship in {"update_to_existing_event", "follow_up_with_new_material_facts"}:
        review_status = "UPDATE_TO_EXISTING_EVENT"
    else:
        review_status = "QUALIFIES"
    event["review_status"] = review_status
    event["review_decision_id"] = candidate.get("review_decision_id")
    return event


def build_release_bundle(
    root: Path,
    queue_path: Path,
    *,
    event_fingerprints: Iterable[str],
    edition_date: str,
    expected_queue_sha256: str,
    output_root: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    queue_path = queue_path.resolve()
    queue = load_queue(queue_path)
    queue_sha = payload_sha256(queue)
    if queue_sha != expected_queue_sha256:
        raise IceReleaseBundleError("ICE review queue changed since bundle selection")

    requested = [str(value).strip() for value in event_fingerprints if str(value).strip()]
    if not requested or len(requested) != len(set(requested)):
        raise IceReleaseBundleError("ICE bundle requires one or more unique event fingerprints")

    by_fp = {str(row.get("event_fingerprint") or ""): row for row in queue.get("items") or []}
    reviewed_events: list[dict[str, Any]] = []
    candidate_refs: list[str] = []
    decision_ids: list[str] = []
    for fingerprint in requested:
        item = by_fp.get(fingerprint)
        if item is None:
            raise IceReleaseBundleError(f"ICE bundle target not found: {fingerprint}")
        if item.get("review_status") not in ALLOWED_REVIEW_STATUSES:
            raise IceReleaseBundleError(f"ICE bundle target is not REVIEWED: {fingerprint}")
        if item.get("review_decision") != "APPROVE_FOR_RELEASE_REVIEW":
            raise IceReleaseBundleError(f"ICE bundle target lacks release-review approval: {fingerprint}")
        candidate_path, candidate = _load_release_candidate(root, item)
        reviewed_events.append(_load_reviewed_event(root, candidate))
        candidate_refs.append(candidate_path.relative_to(root).as_posix())
        decision_ids.append(str(candidate.get("review_decision_id") or ""))

    generated_at = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    identity = {
        "edition_date": edition_date,
        "queue_sha256": queue_sha,
        "event_fingerprints": sorted(requested),
        "review_decision_ids": sorted(decision_ids),
    }
    bundle_id = "ice-release-bundle-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    output_root = (output_root or (root / "data/dispatches/ice/monitor/release-bundles")).resolve()
    expected_root = (root / "data/dispatches/ice/monitor/release-bundles").resolve()
    if output_root != expected_root and expected_root not in output_root.parents:
        raise IceReleaseBundleError("ICE release bundle output must remain under the private release-bundles root")
    bundle_dir = output_root / edition_date / bundle_id
    manifest_path = bundle_dir / "manifest.json"
    reviewed_path = bundle_dir / "reviewed-events.json"

    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "queue_sha256": queue_sha,
        "event_count": len(reviewed_events),
        "event_fingerprints": requested,
        "review_decision_ids": decision_ids,
        "release_candidate_refs": candidate_refs,
        "reviewed_events_sha256": payload_sha256(reviewed_events),
        "edition_candidate_input_ready": True,
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
    }

    if manifest_path.exists() or reviewed_path.exists():
        if (
            manifest_path.is_file()
            and reviewed_path.is_file()
            and json.loads(manifest_path.read_text(encoding="utf-8-sig")) == manifest
            and json.loads(reviewed_path.read_text(encoding="utf-8-sig")) == reviewed_events
        ):
            return {
                "ok": True,
                "status": "idempotent_noop",
                "bundle_id": bundle_id,
                "manifest_ref": manifest_path.relative_to(root).as_posix(),
                "reviewed_events_ref": reviewed_path.relative_to(root).as_posix(),
                "publication_authorized": False,
            }
        raise IceReleaseBundleError("conflicting ICE release bundle already exists")

    _write_json(reviewed_path, reviewed_events)
    _write_json(manifest_path, manifest)
    return {
        "ok": True,
        "status": "bundle_created",
        "bundle_id": bundle_id,
        "event_count": len(reviewed_events),
        "manifest_ref": manifest_path.relative_to(root).as_posix(),
        "reviewed_events_ref": reviewed_path.relative_to(root).as_posix(),
        "reviewed_events_sha256": manifest["reviewed_events_sha256"],
        "edition_candidate_input_ready": True,
        "publication_authorized": False,
    }
