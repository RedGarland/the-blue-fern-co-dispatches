from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_record import (
    CareLineReviewedRecord,
    stable_json_hash,
)


FOLLOW_UP_STATE_SCHEMA_VERSION = "bluefern.care_line.effective_date_follow_up_state.v1"
FOLLOW_UP_STATE_PATH = Path("status/care-line/effective-date-follow-up-state.json")
FOLLOW_UP_TERMINAL_STATUSES = {"CHECKED_NO_CHANGE", "MATERIAL_UPDATE_FOUND", "COMPLETED"}
FOLLOW_UP_LOOKBACK_DAYS = 14
FOLLOW_UP_LOOKAHEAD_DAYS = 7


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _care_line_identity_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _parse_iso_date_text(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def care_line_follow_up_window(
    row: Mapping[str, Any] | CareLineReviewedRecord,
    *,
    lookback_days: int = FOLLOW_UP_LOOKBACK_DAYS,
    lookahead_days: int = FOLLOW_UP_LOOKAHEAD_DAYS,
) -> tuple[str, str]:
    mapping = row if isinstance(row, Mapping) else row.model_dump(mode="json")
    effective = _parse_iso_date_text(_text(mapping, "effective_date", "effective_date_text"))
    if effective is None:
        return "", ""
    start = effective - timedelta(days=max(0, int(lookback_days)))
    end = effective + timedelta(days=max(0, int(lookahead_days)))
    return start.isoformat(), end.isoformat()


def care_line_event_identity(row: Mapping[str, Any] | CareLineReviewedRecord) -> str:
    mapping = row if isinstance(row, Mapping) else row.model_dump(mode="json")
    text = " ".join(
        part
        for part in (
            _text(mapping, "source_title", "title"),
            _text(mapping, "supporting_passage", "effective_evidence_text", "claim_summary"),
            _text(mapping, "review_notes", "verification_notes"),
            _text(mapping, "reviewer_note"),
        )
        if part
    ).casefold()
    event_type = _text(mapping, "event_type", "event_type_raw", "canonical_event_type").casefold()
    effective = _text(mapping, "effective_date", "effective_date_text")
    if not effective and not _text(mapping, "supersedes_record_id") and not any(
        term in text
        for term in (
            "cancel",
            "cancelled",
            "canceled",
            "withdraw",
            "withdrawn",
            "revers",
            "abandon",
            "rescind",
            "delay",
            "delayed",
            "postpon",
            "resched",
            "pushed back",
            "extended to",
            "reopen",
            "reopened",
            "reopening",
            "restore",
            "restored",
            "resumed",
            "resume",
            "reinstat",
            "effective today",
            "takes effect today",
            "begins today",
            "starts today",
            "implemented today",
            "goes into effect",
            "as scheduled",
            "closed today",
        )
    ):
        return ""
    payload = {
        "facility_name": _care_line_identity_text(_text(mapping, "facility_name", "provider_name", "affected_provider", "organization_name")),
        "provider_name": _care_line_identity_text(_text(mapping, "provider_name", "affected_provider", "organization_name")),
        "parent_organization": _care_line_identity_text(_text(mapping, "parent_organization")),
        "operator_name": _care_line_identity_text(_text(mapping, "operator_name", "operator")),
        "city": _care_line_identity_text(_text(mapping, "city", "locality_name")),
        "county": _care_line_identity_text(_text(mapping, "county", "county_equivalent_name")),
        "state": _care_line_identity_text(_text(mapping, "state", "jurisdiction_display")),
        "service_line": _care_line_identity_text(_text(mapping, "service_line", "service_line_canonical", "service_line_raw", "affected_service_line")),
        "event_type": _care_line_identity_text(_text(mapping, "event_type", "event_type_raw", "canonical_event_type")),
    }
    if not any(payload.values()):
        return ""
    return f"care_line_event_{stable_json_hash(payload)[:16]}"


def care_line_event_instance_id(row: Mapping[str, Any] | CareLineReviewedRecord) -> str:
    mapping = row if isinstance(row, Mapping) else row.model_dump(mode="json")
    event_identity = care_line_event_identity(mapping)
    if not event_identity:
        return ""
    payload = {
        "event_identity": event_identity,
        "announcement_date": _text(mapping, "announcement_date", "published_at", "source_publication_date"),
        "effective_date": _text(mapping, "effective_date", "effective_date_text"),
        "source_publication_date": _text(mapping, "source_publication_date", "publication_date", "source_published_date"),
    }
    return f"{event_identity}_{stable_json_hash(payload)[:12]}"


def care_line_lifecycle_status(row: Mapping[str, Any] | CareLineReviewedRecord) -> str:
    mapping = row if isinstance(row, Mapping) else row.model_dump(mode="json")
    text = " ".join(
        part
        for part in (
            _text(mapping, "source_title", "title"),
            _text(mapping, "supporting_passage", "effective_evidence_text", "claim_summary"),
            _text(mapping, "review_notes", "verification_notes"),
            _text(mapping, "reviewer_note"),
        )
        if part
    ).casefold()
    event_type = _text(mapping, "event_type", "event_type_raw", "canonical_event_type").casefold()
    has_lifecycle_terms = any(
        term in text
        for term in (
            "cancel",
            "cancelled",
            "canceled",
            "withdraw",
            "withdrawn",
            "revers",
            "abandon",
            "rescind",
            "delay",
            "delayed",
            "postpon",
            "resched",
            "pushed back",
            "extended to",
            "reopen",
            "reopened",
            "reopening",
            "restore",
            "restored",
            "resumed",
            "resume",
            "reinstat",
            "effective today",
            "takes effect today",
            "begins today",
            "starts today",
            "implemented today",
            "goes into effect",
            "as scheduled",
            "closed today",
        )
    )
    has_effective_terms = any(
        term in text
        for term in (
            "effective today",
            "takes effect today",
            "begins today",
            "starts today",
            "implemented today",
            "goes into effect",
            "as scheduled",
            "closed today",
        )
    )
    if _text(mapping, "supersedes_record_id") or _text(mapping, "record_status").casefold() == "superseded":
        return "SUPERSEDED"
    if has_lifecycle_terms and any(term in text for term in ("cancel", "cancelled", "canceled", "withdraw", "withdrawn", "revers", "abandon", "rescind")):
        return "CANCELLED"
    if has_lifecycle_terms and any(term in text for term in ("delay", "delayed", "postpon", "resched", "pushed back", "extended to")):
        return "DELAYED"
    if has_lifecycle_terms and (event_type in {"facility_reopening", "service_restoration"} or any(term in text for term in ("reopen", "reopened", "reopening", "restore", "restored", "resumed", "resume", "reinstat"))):
        return "RESTORED"
    effective = _parse_iso_date_text(_text(mapping, "effective_date", "effective_date_text"))
    if effective is not None:
        source_date = _parse_iso_date_text(_text(mapping, "source_publication_date", "published_at", "source_published_date", "announcement_date"))
        if source_date is not None and source_date < effective:
            return "PENDING_EFFECTIVE_DATE"
        return "EFFECTIVE"
    if has_effective_terms:
        return "EFFECTIVE"
    return ""


def care_line_effective_follow_up_status(row: Mapping[str, Any] | CareLineReviewedRecord, *, reference_date: str | None = None) -> str:
    mapping = row if isinstance(row, Mapping) else row.model_dump(mode="json")
    lifecycle_status = care_line_lifecycle_status(mapping)
    if lifecycle_status not in {"PENDING_EFFECTIVE_DATE", "EFFECTIVE"}:
        return ""
    effective = _parse_iso_date_text(_text(mapping, "effective_date", "effective_date_text"))
    if effective is None:
        return ""
    reference = _parse_iso_date_text(reference_date or _text(mapping, "source_publication_date", "published_at", "source_published_date", "announcement_date"))
    if reference is None:
        return lifecycle_status
    if reference < effective:
        return "pending"
    if reference == effective:
        return "effective_date_reached"
    return "post_effective_follow_up"


def load_reviewed_records(repo_root: Path) -> list[CareLineReviewedRecord]:
    reviewed_root = repo_root / "data" / "dispatches" / "care-line" / "reviewed"
    records: list[CareLineReviewedRecord] = []
    if not reviewed_root.exists():
        return records
    for path in sorted(reviewed_root.glob("*/reviewed_records.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if not isinstance(payload, dict):
            continue
        for row in payload.get("records") or []:
            if isinstance(row, Mapping):
                records.append(CareLineReviewedRecord.model_validate(dict(row)))
    return records


def _follow_up_state_path(repo_root: Path, *, state_root: Path | None = None) -> Path:
    if state_root is not None:
        return state_root / FOLLOW_UP_STATE_PATH.name
    return repo_root / FOLLOW_UP_STATE_PATH


def load_follow_up_state(repo_root: Path, *, state_root: Path | None = None) -> dict[str, Any]:
    path = _follow_up_state_path(repo_root, state_root=state_root)
    if not path.exists():
        return {"schema_version": FOLLOW_UP_STATE_SCHEMA_VERSION, "items": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"invalid Care Line follow-up state file: {path}")
    items = [dict(row) for row in payload["items"] if isinstance(row, Mapping)]
    return {"schema_version": payload.get("schema_version") or FOLLOW_UP_STATE_SCHEMA_VERSION, "items": items}


def write_follow_up_state(repo_root: Path, payload: Mapping[str, Any], *, state_root: Path | None = None) -> Path:
    path = _follow_up_state_path(repo_root, state_root=state_root)
    _atomic_write(path, dict(payload))
    return path


def _query_tokens(record: Mapping[str, Any] | CareLineReviewedRecord) -> list[str]:
    row = record if isinstance(record, Mapping) else record.model_dump(mode="json")
    tokens = [
        _text(row, "facility_name", "provider_name", "affected_provider", "organization_name"),
        _text(row, "provider_name", "affected_provider", "organization_name"),
        _text(row, "service_line", "service_line_raw", "affected_service_line"),
        _text(row, "city", "locality_name"),
        _text(row, "county", "county_equivalent_name"),
        _text(row, "state", "jurisdiction_display"),
        _text(row, "effective_date", "effective_date_text"),
        _text(row, "event_type", "event_type_raw", "canonical_event_type"),
    ]
    unique = []
    for token in tokens:
        normalized = " ".join(token.split())
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def build_follow_up_query(record: Mapping[str, Any] | CareLineReviewedRecord) -> dict[str, Any]:
    row = record if isinstance(record, Mapping) else record.model_dump(mode="json")
    query = " ".join(f'"{token}"' if " " in token else token for token in _query_tokens(row))
    start, end = care_line_follow_up_window(row)
    return {
        "query": query,
        "source_family": "follow_up",
        "category": "effective_date_follow_up",
        "event_identity": care_line_event_identity(row),
        "event_instance_id": care_line_event_instance_id(row),
        "follow_up_status": care_line_effective_follow_up_status(row, reference_date=_text(row, "source_publication_date", "published_at", "announcement_date")),
        "follow_up_window_start": start,
        "follow_up_window_end": end,
        "lifecycle_status": care_line_lifecycle_status(row),
        "facility_name": _text(row, "facility_name", "provider_name", "affected_provider", "organization_name"),
        "provider_name": _text(row, "provider_name", "affected_provider", "organization_name"),
        "service_line": _text(row, "service_line", "service_line_raw", "affected_service_line"),
        "city": _text(row, "city", "locality_name"),
        "county": _text(row, "county", "county_equivalent_name"),
        "state": _text(row, "state", "jurisdiction_display"),
        "effective_date": _text(row, "effective_date", "effective_date_text"),
        "event_type": _text(row, "event_type", "event_type_raw", "canonical_event_type"),
        "reviewed_record_id": _text(row, "producer_record_id", "source_record_id", "care_line_record_id"),
    }


def build_follow_up_queries(
    repo_root: Path,
    run_date: str,
    *,
    reviewed_records: Iterable[CareLineReviewedRecord] | None = None,
    state: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current_date = date.fromisoformat(run_date)
    state_by_identity = {str(row.get("event_identity") or ""): dict(row) for row in (state or {}).get("items", []) if str(row.get("event_identity") or "")}
    records = list(reviewed_records) if reviewed_records is not None else load_reviewed_records(repo_root)
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        event_identity = care_line_event_identity(record)
        if not event_identity or event_identity in seen:
            continue
        seen.add(event_identity)
        lifecycle_status = care_line_lifecycle_status(record)
        if lifecycle_status not in {"PENDING_EFFECTIVE_DATE", "EFFECTIVE"}:
            continue
        start_text, end_text = care_line_follow_up_window(record)
        if not start_text or not end_text:
            continue
        start_date = date.fromisoformat(start_text)
        end_date = date.fromisoformat(end_text)
        if current_date < start_date or current_date > end_date:
            continue
        prior = state_by_identity.get(event_identity, {})
        if str(prior.get("status") or "").strip() in FOLLOW_UP_TERMINAL_STATUSES:
            continue
        query = build_follow_up_query(record)
        query["follow_up_due_status"] = care_line_effective_follow_up_status(record, reference_date=run_date)
        queries.append(query)
    return queries


def update_follow_up_state(
    repo_root: Path,
    *,
    run_date: str,
    follow_up_queries: Iterable[Mapping[str, Any]],
    discovery_query_rows: Iterable[Mapping[str, Any]] = (),
    state_root: Path | None = None,
) -> dict[str, Any]:
    state = load_follow_up_state(repo_root, state_root=state_root)
    state_by_identity = {str(row.get("event_identity") or ""): dict(row) for row in state["items"] if str(row.get("event_identity") or "")}
    results_by_query = {str(row.get("query") or ""): dict(row) for row in discovery_query_rows if str(row.get("query") or "")}
    items: list[dict[str, Any]] = []
    updated_at = _now()
    for query in follow_up_queries:
        event_identity = str(query.get("event_identity") or "").strip()
        if not event_identity:
            continue
        query_text = str(query.get("query") or "").strip()
        query_row = results_by_query.get(query_text, {})
        result_count = int(query_row.get("results") or 0)
        error = str(query_row.get("error") or "").strip()
        if result_count > 0:
            status = "MATERIAL_UPDATE_FOUND"
        else:
            status = "CHECKED_NO_CHANGE"
        start = str(query.get("follow_up_window_start") or "")
        end = str(query.get("follow_up_window_end") or "")
        if start and end and run_date > end and status == "CHECKED_NO_CHANGE":
            status = "COMPLETED"
        item = {
            "event_identity": event_identity,
            "event_instance_id": str(query.get("event_instance_id") or ""),
            "status": status,
            "last_checked_at": updated_at,
            "last_run_date": run_date,
            "follow_up_window_start": start,
            "follow_up_window_end": end,
            "follow_up_status": str(query.get("follow_up_status") or ""),
            "lifecycle_status": str(query.get("lifecycle_status") or ""),
            "query": query_text,
            "result_count": result_count,
            "query_error": error,
        }
        state_by_identity[event_identity] = item
        items.append(item)
    payload = {"schema_version": FOLLOW_UP_STATE_SCHEMA_VERSION, "updated_at": updated_at, "run_date": run_date, "items": [state_by_identity[key] for key in sorted(state_by_identity)]}
    write_follow_up_state(repo_root, payload, state_root=state_root)
    return {
        "schema_version": FOLLOW_UP_STATE_SCHEMA_VERSION,
        "updated_at": updated_at,
        "run_date": run_date,
        "items": items,
        "state_path": str(_follow_up_state_path(repo_root, state_root=state_root).as_posix()),
    }
