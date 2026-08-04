"""Durable, operator-controlled queue for reviewed Care Line events."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from bluefern_dispatches.universal_events.care_line_signal_wire import (
    PUBLICATION_STATE_PATH,
    _find_latest_reviewed_records_path,
    _find_phase14e_paths,
    _load_json,
    _load_rows,
    _text,
)


QUEUE_PATH = Path("data/universal_events/publication-state/care-line-reviewed-event-queue.json")
QUEUE_SCHEMA_VERSION = 1
QUEUE_STATES = {
    "pending_review", "review_ready", "approved_for_release", "queued", "publishing",
    "published", "deferred", "rejected", "failed",
}
REVIEW_READY_STATUSES = {"approved", "corrected", "confirmed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _load_queue(root: Path) -> dict[str, Any]:
    path = root / QUEUE_PATH
    if not path.exists():
        return {"schema_version": QUEUE_SCHEMA_VERSION, "queue": []}
    payload = _load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("queue"), list):
        raise ValueError(f"invalid Care Line queue artifact: {path}")
    return {"schema_version": QUEUE_SCHEMA_VERSION, "queue": [dict(row) for row in payload["queue"] if isinstance(row, dict)]}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_stable_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _record_id(row: dict[str, Any]) -> str:
    return _text(row, "producer_record_id", "care_line_record_id", "source_record_id")


def _review_fingerprint_status(row: dict[str, Any]) -> tuple[bool, str, str]:
    raw = _text(row, "raw_payload_hash")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    evidence = metadata.get("evidence_review") if isinstance(metadata.get("evidence_review"), dict) else {}
    stored = _text(evidence, "record_fingerprint") or _text(row, "record_fingerprint")
    if not raw or not stored:
        return False, "missing review fingerprint", stored or raw
    if raw != stored:
        return False, "stale review fingerprint", stored
    return True, "review fingerprint matches reviewed content", stored


def _canonical_url_status(row: dict[str, Any], proposed: dict[str, Any]) -> tuple[bool, str, str]:
    source_url = _text(row, "source_url")
    links = proposed.get("evidence_links") if isinstance(proposed.get("evidence_links"), list) else []
    evidence_url = _text(links[0], "source_url") if links and isinstance(links[0], dict) else ""
    parsed = urlparse(source_url)
    if not source_url or parsed.scheme != "https" or not parsed.netloc:
        return False, "missing or non-canonical HTTPS source URL", source_url
    if evidence_url and evidence_url.rstrip("/") != source_url.rstrip("/"):
        return False, "reviewed source URL differs from evidence URL", source_url
    return True, "canonical traceable source URL", source_url


def _event_inputs(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], str | None]:
    reviewed_path = _find_latest_reviewed_records_path(root)
    phase_paths = _find_phase14e_paths(root)
    if reviewed_path is None or phase_paths is None:
        return {}, {}, None
    reviewed = {_record_id(row): row for row in _load_rows(reviewed_path) if _record_id(row)}
    proposed = {_record_id(row): row for row in _load_rows(phase_paths["proposed"]) if _record_id(row)}
    return reviewed, proposed, str(reviewed_path)


def _publication_events(root: Path) -> dict[str, dict[str, Any]]:
    path = root / PUBLICATION_STATE_PATH
    if not path.exists():
        return {}
    payload = _load_json(path)
    return payload.get("events", {}) if isinstance(payload, dict) and isinstance(payload.get("events"), dict) else {}


def _queue_id(record_id: str, event_id: str) -> str:
    return "care_queue_" + sha256(f"{record_id}|{event_id}".encode()).hexdigest()[:16]


def _eligibility(root: Path, record_id: str, row: dict[str, Any], proposed: dict[str, Any], queue_rows: list[dict[str, Any]]) -> tuple[bool, str, dict[str, Any]]:
    event_payload = proposed.get("proposed_event_payload") if isinstance(proposed.get("proposed_event_payload"), dict) else {}
    event_id = _text(event_payload, "event_id")
    if event_id and event_id in _publication_events(root):
        return False, "event is already published", {}
    status = _text(row, "review_status")
    event_status = _text(row, "universal_event_status", "evidence_review_current_status", "record_status")
    workflow_state = _text(row, "workflow_state")
    verification_state = _text(row, "verification_state")
    if status not in REVIEW_READY_STATUSES or event_status != "universal_event_ready":
        return False, "record is not review-approved/event-ready", {}
    if workflow_state and workflow_state != "APPROVED":
        return False, "workflow state is not approved", {}
    if verification_state in {"DISPUTED", "INSUFFICIENT_EVIDENCE"}:
        return False, "verification state is not publishable", {}
    if not bool(row.get("evidence_valid_for_universal_event")):
        return False, "required evidence is not valid for a universal event", {}
    fingerprint_ok, fingerprint_reason, fingerprint = _review_fingerprint_status(row)
    if not fingerprint_ok:
        return False, fingerprint_reason, {}
    url_ok, url_reason, source_url = _canonical_url_status(row, proposed)
    if not url_ok:
        return False, url_reason, {}
    if not record_id:
        return False, "missing source record ID", {}
    if not event_id:
        return False, "missing stable event ID", {}
    links = proposed.get("evidence_links") if isinstance(proposed.get("evidence_links"), list) else []
    if not links or not _text(links[0], "supporting_passage"):
        return False, "required evidence lineage is incomplete", {}
    for existing in queue_rows:
        if _text(existing, "event_id") == event_id:
            return False, f"event is already queued ({_text(existing, 'state') or 'unknown state'})", {}
    payload = {
        "queue_id": _queue_id(record_id, event_id),
        "source_record_id": record_id,
        "event_id": event_id,
        "headline": _text(row, "source_title", "title"),
        "review_status": status,
        "review_fingerprint": fingerprint,
        "source_url": source_url,
        "approved_at": (
            _text((row.get("metadata") or {}).get("evidence_review") or {}, "reviewed_at")
            if isinstance(row.get("metadata"), dict)
            else ""
        ) or _text(row, "updated_at") or _now(),
        "queued_at": _now(),
        "release_after": None,
        "attempt_count": 0,
        "last_attempt_at": None,
        "failure_reason": None,
        "state": "queued",
        "publication": {},
    }
    return True, "eligible", payload


def inspect_queue(root: Path) -> dict[str, Any]:
    queue = _load_queue(root)["queue"]
    reviewed, proposed, reviewed_path = _event_inputs(root)
    published = _publication_events(root)
    rows = []
    for entry in queue:
        row = reviewed.get(_text(entry, "source_record_id"), {})
        fp_ok, fp_reason, _ = _review_fingerprint_status(row) if row else (False, "reviewed record missing", "")
        item = dict(entry)
        item.update({"review_status": _text(row, "review_status"), "fingerprint_status": "valid" if fp_ok else fp_reason, "publication_status": "published" if item.get("event_id") in published else item.get("state")})
        rows.append(item)
    return {"schema_version": QUEUE_SCHEMA_VERSION, "queue_path": str((root / QUEUE_PATH).as_posix()), "reviewed_records_path": reviewed_path, "counts_by_state": dict(sorted(Counter(_text(row, "state") or "unknown" for row in rows).items())), "queue": rows}


def enqueue(root: Path, *, source_record_ids: Iterable[str] = (), event_ids: Iterable[str] = (), dry_run: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    current = _load_queue(root)
    queue_rows = list(current["queue"])
    reviewed, proposed, reviewed_path = _event_inputs(root)
    wanted_records = {str(value) for value in source_record_ids if str(value).strip()}
    wanted_events = {str(value) for value in event_ids if str(value).strip()}
    candidates = sorted(set(reviewed))
    if wanted_records or wanted_events:
        candidates = [
            record_id for record_id in candidates
            if record_id in wanted_records
            or (record_id in proposed and _text(proposed[record_id].get("proposed_event_payload", {}), "event_id") in wanted_events)
        ]
    additions: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    for record_id in candidates:
        if record_id not in proposed:
            exclusions.append({"source_record_id": record_id, "event_id": "", "reason": "missing stable event lineage"})
            continue
        ok, reason, entry = _eligibility(root, record_id, reviewed[record_id], proposed[record_id], queue_rows + additions)
        if ok:
            additions.append(entry)
        else:
            exclusions.append({"source_record_id": record_id, "event_id": _text(proposed[record_id].get("proposed_event_payload", {}), "event_id"), "reason": reason})
    result = {"operation": "enqueue", "dry_run": dry_run, "reviewed_records_path": reviewed_path, "added": additions, "excluded": exclusions, "queue_before": len(queue_rows), "queue_after": len(queue_rows) + len(additions)}
    if not dry_run and additions:
        _atomic_write(root / QUEUE_PATH, {"schema_version": QUEUE_SCHEMA_VERSION, "queue": queue_rows + additions})
    return result


def select_release_set(root: Path, *, max_events: int | None = None, event_ids: Iterable[str] = (), release_after: str | None = None) -> dict[str, Any]:
    queue_rows = _load_queue(Path(root).resolve())["queue"]
    wanted = {str(value) for value in event_ids if str(value).strip()}
    cutoff = datetime.fromisoformat(release_after.replace("Z", "+00:00")) if release_after else datetime.now(timezone.utc)
    eligible, excluded = [], []
    for row in queue_rows:
        state = _text(row, "state")
        if wanted and _text(row, "event_id") not in wanted:
            continue
        if state != "queued" and state != "approved_for_release":
            excluded.append({"queue_id": _text(row, "queue_id"), "event_id": _text(row, "event_id"), "reason": f"state is {state or 'missing'}"})
            continue
        if _text(row, "release_after") and datetime.fromisoformat(_text(row, "release_after").replace("Z", "+00:00")) > cutoff:
            excluded.append({"queue_id": _text(row, "queue_id"), "event_id": _text(row, "event_id"), "reason": "release-after is later than requested cutoff"})
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: (_text(row, "approved_at"), _text(row, "queue_id")))
    if max_events is not None:
        excluded.extend({"queue_id": _text(row, "queue_id"), "event_id": _text(row, "event_id"), "reason": "max-event limit"} for row in eligible[max_events:])
        eligible = eligible[:max_events]
    return {"operation": "release-set", "selected_queue_ids": [_text(row, "queue_id") for row in eligible], "selected_event_ids": [_text(row, "event_id") for row in eligible], "selected_source_record_ids": [_text(row, "source_record_id") for row in eligible], "excluded": excluded, "proposed_publication_scope": {"dispatch": "care-line", "event_ids": [_text(row, "event_id") for row in eligible]}, "proposed_files": [], "timestamp_decisions": "publication state retains first public publication timestamps; no timestamps are assigned during selection"}


def update_state(root: Path, *, queue_id: str, state: str, reapprove: bool = False, failure_reason: str | None = None) -> dict[str, Any]:
    if state not in QUEUE_STATES:
        raise ValueError(f"unsupported queue state: {state}")
    root = Path(root).resolve()
    payload = _load_queue(root)
    found = next((row for row in payload["queue"] if _text(row, "queue_id") == queue_id), None)
    if found is None:
        raise ValueError(f"unknown queue_id: {queue_id}")
    old = _text(found, "state")
    if state in {"queued", "approved_for_release"} and old in {"deferred", "rejected"} and not reapprove:
        raise ValueError("deferred or rejected entries require explicit reapproval")
    if state == "queued" and old == "failed" and not reapprove:
        raise ValueError("failed entries require explicit retry approval")
    found["state"] = state
    if state == "queued":
        found["queued_at"] = found.get("queued_at") or _now()
    if state == "publishing":
        found["attempt_count"] = int(found.get("attempt_count") or 0) + 1
        found["last_attempt_at"] = _now()
    if state == "failed":
        found["failure_reason"] = failure_reason or "publish failed"
    _atomic_write(root / QUEUE_PATH, payload)
    return {"queue_id": queue_id, "from": old, "to": state}


def mark_published(root: Path, *, event_ids: Iterable[str], publication: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _load_queue(Path(root).resolve())
    selected = {str(value) for value in event_ids}
    changed = []
    for row in payload["queue"]:
        if _text(row, "event_id") in selected:
            row["state"] = "published"
            row["publication"] = dict(publication or {})
            row["published_at"] = _text(row.get("publication", {}), "published_at") or _now()
            for key in ("first_publication_timestamp", "last_updated_at", "pages_commit_sha", "release_batch_id", "canonical_event_url"):
                if key in row["publication"]:
                    row[key] = row["publication"][key]
            changed.append(_text(row, "event_id"))
    if changed:
        _atomic_write(Path(root).resolve() / QUEUE_PATH, payload)
    return {"published_event_ids": sorted(changed)}
