from __future__ import annotations

import argparse
import json
import socket
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from bluefern_dispatches.care_line_evidence_review import (
    DEFAULT_REVIEW_PACKET_OUTPUT,
    review_packet_fingerprint,
)
from bluefern_dispatches.care_line_national_pipeline import (
    REVIEW_QUEUE_SCHEMA_VERSION,
    _can_fetch_item_url,
    _extract_article_content,
    build_review_queue,
    event_lead_from_raw_item,
    fetch_url,
    load_reviewed_records,
    qualify_event_lead,
    update_candidate_registry_at_path,
    utc_now,
)
from bluefern_dispatches.care_line_record import stable_json_hash
from bluefern_dispatches.care_line_source_registry import CareLineSource, load_registry


RECOVERY_SCHEMA_VERSION = "bluefern.care_line.evidence_recovery.v1"
RECOVERY_TOOL_VERSION = "care-line-evidence-recovery-v1"

DEFAULT_REVIEW_ROOT = Path("data") / "dispatches" / "care-line" / "review"
DEFAULT_RECOVERY_ROOT = DEFAULT_REVIEW_ROOT / "evidence-recovery"
DEFAULT_REGISTRY_PATH = Path("data") / "dispatches" / "care-line" / "source_registry.json"
DEFAULT_CANDIDATE_REGISTRY = DEFAULT_REVIEW_ROOT / "candidate-registry.json"
DEFAULT_REVIEW_QUEUE = DEFAULT_REVIEW_ROOT / "current-review-queue.json"
DEFAULT_REVIEW_BACKLOG = DEFAULT_REVIEW_ROOT / "current-review-backlog.json"
DEFAULT_REVIEW_DUPLICATES = DEFAULT_REVIEW_ROOT / "current-duplicates.json"

TARGET_BUCKETS = {"additional_fetch_needed", "deterministic_resolution"}
NETWORK_ROUTES = {"direct_item_refetch", "parser_extraction_retry"}
TERMINAL_RESULTS = {
    "QUALIFIED_PRIVATE_CANDIDATE",
    "STILL_ADDITIONAL_FETCH_NEEDED",
    "DETERMINISTIC_EXCLUSION",
    "UNRESOLVED_EVIDENCE",
    "FETCH_FAILED",
    "NOT_RECOVERABLE_WITH_CURRENT_SOURCE",
}


def _json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _record_fingerprint(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "record_fingerprint")
    if explicit:
        return explicit
    return stable_json_hash(row)


def _source_id(row: Mapping[str, Any]) -> str:
    metadata = row.get("source_metadata") if isinstance(row.get("source_metadata"), Mapping) else {}
    return _text(metadata, "source_id") or _text(row, "source_id")


def _canonical_url(row: Mapping[str, Any]) -> str:
    return _text(row, "canonical_source_url", "source_url", "item_url", "url")


def _normalize_identity_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(value or "").strip()
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{path}{query}"


def _normalize_identity_text(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _source_publication_date(row: Mapping[str, Any]) -> str:
    proposed = row.get("proposed_fields") if isinstance(row.get("proposed_fields"), Mapping) else {}
    publication = proposed.get("publication_date") if isinstance(proposed.get("publication_date"), Mapping) else {}
    priority = row.get("priority") if isinstance(row.get("priority"), Mapping) else {}
    return _text(row, "source_publication_date") or _text(publication, "value") or _text(priority, "source_date")


def _source_evidence_fingerprint(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "source_evidence_fingerprint")
    if explicit:
        return explicit
    source_id = _source_id(row)
    canonical_url = _canonical_url(row)
    title = _text(row, "source_title", "title")
    if not (source_id and canonical_url and title):
        return ""
    return stable_json_hash(
        {
            "source_id": source_id,
            "canonical_url": _normalize_identity_url(canonical_url),
            "normalized_title": _normalize_identity_text(title),
            "source_publication_date": _source_publication_date(row),
        }
    )


def _packet_records(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in packet.get("records", [])
        if isinstance(row, Mapping)
        and _text(row, "resolution_bucket") in TARGET_BUCKETS
        and not _text(row, "duplicate_of_producer_record_id")
    ]


def _review_bucket(row: Mapping[str, Any]) -> str:
    return _text(row, "resolution_bucket")


def _route_for_record(row: Mapping[str, Any], source: CareLineSource | None) -> str:
    if _review_bucket(row) == "deterministic_resolution":
        return "deterministic_existing_evidence"
    url = _canonical_url(row)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "stale_or_invalid_url"
    if source is None:
        return "not_recoverable_with_current_source"
    if not source.enabled:
        return "not_recoverable_with_current_source"
    if not _can_fetch_item_url(source, url):
        return "stale_or_invalid_url"
    outcome = _text(row, "extraction_outcome")
    if outcome in {"ACCESS_BLOCKED", "PAYWALLED"}:
        return "repeated_blocked_no_route"
    if outcome in {"PARSE_FAILED", "EMPTY_RESPONSE", "PARTIAL_BODY", "INDEX_ONLY", "HEADLINE_ONLY", "SCRIPT_RENDERED", "PDF_REQUIRED"}:
        return "parser_extraction_retry"
    return "direct_item_refetch"


def _packet_raw_item(row: Mapping[str, Any], source: CareLineSource) -> dict[str, Any]:
    proposed = row.get("proposed_fields") if isinstance(row.get("proposed_fields"), Mapping) else {}
    publication = proposed.get("publication_date") if isinstance(proposed.get("publication_date"), Mapping) else {}
    description = _text(row, "supporting_passage", "supporting_text")
    return {
        "schema_version": "bluefern.care_line.raw_item.v1",
        "raw_item_id": _text(row, "raw_item_id", "producer_record_id"),
        "record_fingerprint": _record_fingerprint(row),
        "source_evidence_fingerprint": _source_evidence_fingerprint(row),
        "source_id": source.source_id,
        "source_name": source.name,
        "source_publisher": source.publisher,
        "source_type": source.source_type,
        "source_role": source.source_role,
        "authority_level": source.authority_level,
        "item_url": _canonical_url(row),
        "title": _text(row, "source_title", "title"),
        "description": description,
        "content_text": description,
        "source_publication_date": _source_publication_date(row),
        "source_date_state": "source_dated" if _source_publication_date(row) else "",
        "requires_html_followup": True,
        "discovery_date": utc_now().split("T", 1)[0],
        "source_artifact_path": next(iter(row.get("source_artifact_paths") or []), ""),
    }


def _status_from_qualification(status: str, payload: Mapping[str, Any]) -> str:
    if status == "qualified":
        return "QUALIFIED_PRIVATE_CANDIDATE"
    if status == "excluded":
        return "DETERMINISTIC_EXCLUSION"
    if status == "failed_extraction":
        reason = _text(payload, "exclusion_reason")
        if reason in {"needs_full_article", "needs_date", "needs_geography", "needs_access_consequence", "missing_subject", "missing_event_type", "missing_service_line_or_facility_scope", "insufficient_bounded_evidence", "private_or_inaccessible_evidence"}:
            return "STILL_ADDITIONAL_FETCH_NEEDED"
        return "UNRESOLVED_EVIDENCE"
    return "UNRESOLVED_EVIDENCE"


def _failure_class(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP_{exc.code}"
    if isinstance(exc, TimeoutError | socket.timeout):
        return "TIMEOUT"
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError | socket.timeout):
        return "TIMEOUT"
    return type(exc).__name__


def _attempt_id(packet_fp: str, record_fp: str) -> str:
    return "care_line_evidence_recovery_" + sha256(f"{packet_fp}:{record_fp}".encode("utf-8")).hexdigest()[:16]


def _attempt_path(recovery_root: Path, packet_fp: str, record_fp: str, *, today: str | None = None) -> Path:
    return recovery_root / (today or utc_now().split("T", 1)[0]) / f"{_attempt_id(packet_fp, record_fp)}.json"


def _same_day_attempts_for_record(recovery_root: Path, record_fp: str, *, today: str) -> list[tuple[Path, dict[str, Any]]]:
    day_root = recovery_root / today
    if not day_root.exists():
        return []
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(day_root.glob("*.json"), key=lambda item: item.as_posix()):
        try:
            attempt = _json(path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(attempt, Mapping) and _text(attempt, "record_fingerprint") == record_fp:
            matches.append((path, dict(attempt)))
    return matches


def _same_day_attempts_for_source_evidence(
    recovery_root: Path,
    source_evidence_fp: str,
    *,
    today: str,
) -> list[tuple[Path, dict[str, Any]]]:
    if not source_evidence_fp:
        return []
    day_root = recovery_root / today
    if not day_root.exists():
        return []
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(day_root.glob("*.json"), key=lambda item: item.as_posix()):
        try:
            attempt = _json(path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(attempt, Mapping) and _text(attempt, "source_evidence_fingerprint") == source_evidence_fp:
            matches.append((path, dict(attempt)))
    return matches


def _materially_same_attempt(attempt: Mapping[str, Any], *, row: Mapping[str, Any], route: str) -> bool:
    if _text(attempt, "route") != route:
        return False
    if _text(attempt, "source_id") and _text(attempt, "source_id") != _source_id(row):
        return False
    if _text(attempt, "attempted_url") and _text(attempt, "attempted_url") != _canonical_url(row):
        return False
    attempt_evidence_fp = _text(attempt, "source_evidence_fingerprint")
    row_evidence_fp = _source_evidence_fingerprint(row)
    if attempt_evidence_fp and row_evidence_fp and attempt_evidence_fp != row_evidence_fp:
        return False
    return True


def _carry_forward_attempt(
    prior: Mapping[str, Any],
    *,
    packet_fp: str,
    record_fp: str,
    row: Mapping[str, Any],
    route: str,
) -> dict[str, Any]:
    carried = dict(prior)
    carried.update(
        {
            "attempt_id": _attempt_id(packet_fp, record_fp),
            "packet_fingerprint": packet_fp,
            "record_fingerprint": record_fp,
            "source_evidence_fingerprint": _source_evidence_fingerprint(row),
            "producer_record_id": _text(row, "producer_record_id"),
            "raw_item_id": _text(row, "raw_item_id"),
            "source_id": _source_id(row),
            "attempted_url": _canonical_url(row),
            "attempted_at": utc_now(),
            "route": route,
            "carried_forward_from_attempt_id": _text(prior, "attempt_id"),
            "carried_forward_from_packet_fingerprint": _text(prior, "packet_fingerprint"),
            "carried_forward_from_producer_record_id": _text(prior, "producer_record_id"),
            "network_refetch": False,
            "no_publication": True,
        }
    )
    return carried


def _source_without_item_fetch(source: CareLineSource) -> CareLineSource:
    return source.model_copy(update={"item_permalink_available": False})


def _qualify_raw_item(
    source: CareLineSource,
    raw_item: Mapping[str, Any],
    *,
    artifact_path: str,
    run_id: str,
    repo_root: Path,
    fetch_timeout: int,
) -> tuple[str, dict[str, Any]]:
    lead = event_lead_from_raw_item(raw_item)
    return qualify_event_lead(
        _source_without_item_fetch(source),
        raw_item,
        lead,
        artifact_path=artifact_path,
        run_id=run_id,
        fetch_timeout=fetch_timeout,
        allow_insecure_tls=False,
        reviewed_records=load_reviewed_records(repo_root),
    )


def load_recovery_inputs(
    *,
    repo_root: Path,
    packet_path: Path = DEFAULT_REVIEW_PACKET_OUTPUT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> tuple[dict[str, Any], str, dict[str, CareLineSource], list[dict[str, Any]]]:
    packet = _json(repo_root / packet_path if not packet_path.is_absolute() else packet_path)
    packet_fp = review_packet_fingerprint(packet)
    registry = load_registry(repo_root / registry_path if not registry_path.is_absolute() else registry_path, include_disabled=True)
    sources = {source.source_id: source for source in registry.sources}
    return packet, packet_fp, sources, _packet_records(packet)


def plan_recovery(
    *,
    repo_root: Path,
    packet_path: Path = DEFAULT_REVIEW_PACKET_OUTPUT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    _packet, packet_fp, sources, records = load_recovery_inputs(
        repo_root=repo_root,
        packet_path=packet_path,
        registry_path=registry_path,
    )
    planned = []
    counts: Counter[str] = Counter()
    for row in records:
        source = sources.get(_source_id(row))
        route = _route_for_record(row, source)
        counts[route] += 1
        planned.append(
            {
                "producer_record_id": _text(row, "producer_record_id"),
                "raw_item_id": _text(row, "raw_item_id"),
                "record_fingerprint": _record_fingerprint(row),
                "source_evidence_fingerprint": _source_evidence_fingerprint(row),
                "source_id": _source_id(row),
                "registry_entry_present": source is not None,
                "canonical_url": _canonical_url(row),
                "source_feed_url": source.feed_url if source else "",
                "source_homepage_url": source.homepage_url if source else "",
                "extraction_outcome": _text(row, "extraction_outcome"),
                "failed_gates": list(row.get("exact_missing_requirements") or row.get("unresolved_fields") or []),
                "failure_reason": _text(row, "exclusion_reason"),
                "source_publication_date": _text(row, "source_publication_date") or _text(row.get("priority", {}) if isinstance(row.get("priority"), Mapping) else {}, "source_date"),
                "event_lead_id": _text(row, "event_lead_id"),
                "source_artifact_path": next(iter(row.get("source_artifact_paths") or []), ""),
                "item_permalink_permitted": bool(source and _can_fetch_item_url(source, _canonical_url(row))),
                "host": urlparse(_canonical_url(row)).netloc,
                "route": route,
            }
        )
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "tool_version": RECOVERY_TOOL_VERSION,
        "packet_fingerprint": packet_fp,
        "record_count": len(records),
        "route_counts": dict(sorted(counts.items())),
        "records": planned,
    }


def recover_records(
    *,
    repo_root: Path,
    packet_path: Path = DEFAULT_REVIEW_PACKET_OUTPUT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    recovery_root: Path = DEFAULT_RECOVERY_ROOT,
    max_records: int | None = None,
    force: bool = False,
    test_context_force: bool = False,
    write_attempts: bool = True,
    fetch_timeout: int = 20,
    expected_packet_fingerprint: str = "",
    expected_record_fingerprints: Iterable[str] = (),
) -> dict[str, Any]:
    if force and not test_context_force:
        raise ValueError("forced retry is unavailable outside explicit test context")
    _packet, packet_fp, sources, records = load_recovery_inputs(
        repo_root=repo_root,
        packet_path=packet_path,
        registry_path=registry_path,
    )
    if expected_packet_fingerprint and expected_packet_fingerprint != packet_fp:
        raise ValueError("stale packet fingerprint")
    expected_records = {value for value in expected_record_fingerprints if value}
    current_record_fps = {_record_fingerprint(row) for row in records}
    missing_expected = expected_records - current_record_fps
    if missing_expected:
        raise ValueError("stale record fingerprint")
    if max_records is not None:
        records = records[:max_records]
    recovery_root_abs = repo_root / recovery_root if not recovery_root.is_absolute() else recovery_root
    today = utc_now().split("T", 1)[0]
    attempts = []
    counters: Counter[str] = Counter()
    summary: Counter[str] = Counter()
    recovered_candidates: list[dict[str, Any]] = []
    for row in records:
        source = sources.get(_source_id(row))
        record_fp = _record_fingerprint(row)
        source_evidence_fp = _source_evidence_fingerprint(row)
        path = _attempt_path(recovery_root_abs, packet_fp, record_fp, today=today)
        route = _route_for_record(row, source)
        if path.exists() and not force:
            attempt = _json(path)
            attempts.append(attempt)
            counters["skipped_existing_attempt"] += 1
            continue
        if not force:
            carried_from = [
                (prior_path, prior)
                for prior_path, prior in _same_day_attempts_for_record(recovery_root_abs, record_fp, today=today)
                if _text(prior, "packet_fingerprint") != packet_fp and _materially_same_attempt(prior, row=row, route=route)
            ]
            if not carried_from:
                carried_from = [
                    (prior_path, prior)
                    for prior_path, prior in _same_day_attempts_for_source_evidence(recovery_root_abs, source_evidence_fp, today=today)
                    if _text(prior, "packet_fingerprint") != packet_fp and _materially_same_attempt(prior, row=row, route=route)
                ]
            if carried_from:
                _prior_path, prior = carried_from[-1]
                attempt = _carry_forward_attempt(prior, packet_fp=packet_fp, record_fp=record_fp, row=row, route=route)
                attempts.append(attempt)
                counters["carried_forward_existing_attempt"] += 1
                if write_attempts:
                    _write_json(path, attempt)
                continue
        raw_item = _packet_raw_item(row, source) if source else {}
        attempted_url = _canonical_url(row)
        attempt: dict[str, Any] = {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "tool_version": RECOVERY_TOOL_VERSION,
            "attempt_id": _attempt_id(packet_fp, record_fp),
            "packet_fingerprint": packet_fp,
            "record_fingerprint": record_fp,
            "source_evidence_fingerprint": source_evidence_fp,
            "producer_record_id": _text(row, "producer_record_id"),
            "raw_item_id": _text(row, "raw_item_id"),
            "source_id": _source_id(row),
            "attempted_url": attempted_url,
            "attempted_at": utc_now(),
            "route": route,
            "http_failure_class": "",
            "http_status": 0,
            "extraction_outcome": "",
            "result_status": "UNRESOLVED_EVIDENCE",
            "recovered_evidence_fingerprint": "",
            "care_qualification_status": "",
            "qualification_result": {},
            "candidate": {},
            "no_publication": True,
        }
        if source is None or route in {"not_recoverable_with_current_source", "stale_or_invalid_url", "repeated_blocked_no_route"}:
            attempt["result_status"] = "NOT_RECOVERABLE_WITH_CURRENT_SOURCE"
            attempt["http_failure_class"] = "route_not_fetchable"
            if route == "repeated_blocked_no_route":
                summary["access_blocked"] += 1
        elif route == "deterministic_existing_evidence":
            status, payload = _qualify_raw_item(
                source,
                raw_item,
                artifact_path=next(iter(row.get("source_artifact_paths") or []), ""),
                run_id="care-line-evidence-recovery-deterministic",
                repo_root=repo_root,
                fetch_timeout=fetch_timeout,
            )
            attempt["care_qualification_status"] = status
            attempt["qualification_result"] = payload.get("qualification_result", payload)
            attempt["result_status"] = _status_from_qualification(status, payload)
            if status == "qualified":
                attempt["candidate"] = payload
                recovered_candidates.append(payload)
            attempt["recovered_evidence_fingerprint"] = stable_json_hash({"status": status, "payload": payload})
            if attempt["result_status"] == "QUALIFIED_PRIVATE_CANDIDATE":
                summary["deterministic_resolution_completed"] += 1
        elif route in NETWORK_ROUTES:
            summary["fetch_attempted"] += 1
            try:
                payload, response_meta = fetch_url(attempted_url, timeout=min(fetch_timeout, 20), allow_insecure_tls=False)
                article = _extract_article_content(source, payload, source_url=attempted_url, response_meta=response_meta)
                attempt["http_status"] = int(response_meta.get("http_status") or 0)
                attempt["extraction_outcome"] = _text(article, "extraction_outcome")
                if attempt["extraction_outcome"] in {"BODY_EXTRACTED", "PARTIAL_BODY", "PAYWALLED", "PDF_REQUIRED", "SCRIPT_RENDERED", "HEADLINE_ONLY"}:
                    summary["fetch_success"] += 1
                if attempt["extraction_outcome"] in {"PARSE_FAILED", "EMPTY_RESPONSE"}:
                    summary["parse_failed"] += 1
                attempt["recovered_evidence_fingerprint"] = _text(article, "content_hash")
                recovered_raw_item = {
                    **raw_item,
                    "title": _text(article, "title") or _text(raw_item, "title"),
                    "description": _text(article, "text") or _text(article, "description") or _text(raw_item, "description"),
                    "content_text": _text(article, "text") or _text(raw_item, "content_text"),
                    "source_publication_date": _text(article, "published_at") or _text(raw_item, "source_publication_date"),
                    "source_date_state": "source_dated" if (_text(article, "published_at") or _text(raw_item, "source_publication_date")) else "",
                }
                status, qual_payload = _qualify_raw_item(
                    source,
                    recovered_raw_item,
                    artifact_path=f"{recovery_root.as_posix()}/{today}/{_attempt_id(packet_fp, record_fp)}.json",
                    run_id="care-line-evidence-recovery",
                    repo_root=repo_root,
                    fetch_timeout=fetch_timeout,
                )
                attempt["care_qualification_status"] = status
                attempt["qualification_result"] = qual_payload.get("qualification_result", qual_payload)
                attempt["result_status"] = _status_from_qualification(status, qual_payload)
                if status == "qualified":
                    attempt["candidate"] = qual_payload
                    recovered_candidates.append(qual_payload)
            except Exception as exc:  # noqa: BLE001
                attempt["result_status"] = "FETCH_FAILED"
                attempt["http_failure_class"] = _failure_class(exc)
                if attempt["http_failure_class"] in {"HTTP_401", "HTTP_403", "HTTP_451"}:
                    summary["access_blocked"] += 1
                elif attempt["http_failure_class"] == "TIMEOUT":
                    summary["timeout"] += 1
                else:
                    summary["fetch_failed"] += 1
        else:
            attempt["result_status"] = "UNRESOLVED_EVIDENCE"
        if attempt["result_status"] not in TERMINAL_RESULTS:
            attempt["result_status"] = "UNRESOLVED_EVIDENCE"
        attempts.append(attempt)
        counters[attempt["result_status"]] += 1
        if attempt["result_status"] == "DETERMINISTIC_EXCLUSION":
            summary["authoritative_exclusions"] += 1
        elif attempt["result_status"] == "QUALIFIED_PRIVATE_CANDIDATE":
            summary["qualified_private_candidates"] += 1
        elif attempt["result_status"] in {"STILL_ADDITIONAL_FETCH_NEEDED", "UNRESOLVED_EVIDENCE", "FETCH_FAILED", "NOT_RECOVERABLE_WITH_CURRENT_SOURCE"}:
            summary["still_unresolved"] += 1
        if write_attempts:
            _write_json(path, attempt)
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "tool_version": RECOVERY_TOOL_VERSION,
        "packet_fingerprint": packet_fp,
        "attempt_count": len(attempts),
        "result_counts": dict(sorted(counters.items())),
        "shadow_counts": {
            "fetch_attempted": summary.get("fetch_attempted", 0),
            "fetch_success": summary.get("fetch_success", 0),
            "access_blocked": summary.get("access_blocked", 0),
            "timeout": summary.get("timeout", 0),
            "parse_failed": summary.get("parse_failed", 0),
            "fetch_failed": summary.get("fetch_failed", 0),
            "authoritative_exclusions": summary.get("authoritative_exclusions", 0),
            "qualified_private_candidates": summary.get("qualified_private_candidates", 0),
            "still_unresolved": summary.get("still_unresolved", 0),
            "deterministic_resolution_completed": summary.get("deterministic_resolution_completed", 0),
        },
        "qualified_candidate_count": len(recovered_candidates),
        "attempts": attempts,
        "qualified_candidates": recovered_candidates,
        "no_publication": True,
    }


def apply_recovery(
    *,
    repo_root: Path,
    recovery_result: Mapping[str, Any] | None = None,
    packet_path: Path = DEFAULT_REVIEW_PACKET_OUTPUT,
    recovery_root: Path = DEFAULT_RECOVERY_ROOT,
    candidate_registry_path: Path = DEFAULT_CANDIDATE_REGISTRY,
    review_queue_path: Path = DEFAULT_REVIEW_QUEUE,
    review_backlog_path: Path = DEFAULT_REVIEW_BACKLOG,
    review_duplicates_path: Path = DEFAULT_REVIEW_DUPLICATES,
    edition_date: str | None = None,
    expected_packet_fingerprint: str = "",
) -> dict[str, Any]:
    if not expected_packet_fingerprint:
        raise ValueError("expected current packet fingerprint is required for apply")
    packet = _json(repo_root / packet_path if not packet_path.is_absolute() else packet_path)
    current_packet_fingerprint = review_packet_fingerprint(packet)
    if expected_packet_fingerprint != current_packet_fingerprint:
        raise ValueError("stale packet fingerprint")
    current_record_fingerprints = {_record_fingerprint(row) for row in _packet_records(packet)}
    if recovery_result is None:
        root = repo_root / recovery_root if not recovery_root.is_absolute() else recovery_root
        attempts = [_json(path) for path in sorted((root / utc_now().split("T", 1)[0]).glob("*.json"))]
        recovery_result = {"attempts": attempts}
    raw_attempts = list(recovery_result.get("attempts", [])) if isinstance(recovery_result.get("attempts", []), list) else []
    malformed_attempts_ignored = 0
    attempts: list[dict[str, Any]] = []
    for attempt in raw_attempts:
        if isinstance(attempt, Mapping):
            attempts.append(dict(attempt))
        else:
            malformed_attempts_ignored += 1
    current_attempts: list[dict[str, Any]] = []
    prior_attempts_ignored = 0
    for attempt in attempts:
        if _text(attempt, "packet_fingerprint") == expected_packet_fingerprint:
            current_attempts.append(attempt)
        else:
            prior_attempts_ignored += 1
    stale_current_record_failures = 0
    for attempt in current_attempts:
        if _text(attempt, "record_fingerprint") not in current_record_fingerprints:
            stale_current_record_failures += 1
            raise ValueError("stale recovery attempt record fingerprint")
    candidates = []
    for attempt in current_attempts:
        if attempt.get("result_status") != "QUALIFIED_PRIVATE_CANDIDATE":
            continue
        candidate = attempt.get("candidate")
        if not isinstance(candidate, Mapping) or not candidate:
            raise ValueError("malformed qualified recovery candidate")
        candidate_id = _text(candidate, "candidate_id")
        normalized = candidate.get("normalized_record")
        if not candidate_id or not isinstance(normalized, Mapping) or not normalized:
            raise ValueError("malformed qualified recovery candidate")
        candidates.append(dict(candidate))
    selection_report = {
        "attempts_examined": len(raw_attempts),
        "current_packet_attempts_selected": len(current_attempts),
        "prior_packet_attempts_ignored": prior_attempts_ignored,
        "malformed_attempts_ignored": malformed_attempts_ignored,
        "qualified_candidates_selected": len(candidates),
        "stale_current_record_failures": stale_current_record_failures,
    }
    if not candidates:
        return {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            **selection_report,
            "candidate_count": 0,
            "registry_candidate_count": 0,
            "created_this_run": 0,
            "updated_this_run": 0,
            "queue_item_count": 0,
            "backlog_item_count": 0,
            "duplicate_item_count": 0,
            "approved_count": 0,
            "universal_event_ready_count": 0,
            "publication_state_mutated": False,
            "active_review_state_mutated": False,
        }
    registry_path = repo_root / candidate_registry_path if not candidate_registry_path.is_absolute() else candidate_registry_path
    queue_path = repo_root / review_queue_path if not review_queue_path.is_absolute() else review_queue_path
    backlog_path = repo_root / review_backlog_path if not review_backlog_path.is_absolute() else review_backlog_path
    duplicates_path = repo_root / review_duplicates_path if not review_duplicates_path.is_absolute() else review_duplicates_path
    registry = update_candidate_registry_at_path(
        registry_path,
        edition_date=edition_date or datetime.now(timezone.utc).date().isoformat(),
        candidates=candidates,
    )
    queue = build_review_queue(registry.get("candidates", []), edition_date=edition_date or datetime.now(timezone.utc).date().isoformat())
    _write_json(queue_path, queue)
    _write_json(
        backlog_path,
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue.get("edition_date", ""),
            "backlog_item_count": len(queue.get("backlog", [])),
            "items": list(queue.get("backlog", [])),
        },
    )
    _write_json(
        duplicates_path,
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue.get("edition_date", ""),
            "duplicate_item_count": len(queue.get("duplicates", [])),
            "items": list(queue.get("duplicates", [])),
        },
    )
    approved = 0
    universal_ready = 0
    for candidate in registry.get("candidates", []):
        normalized = candidate.get("normalized_record") if isinstance(candidate.get("normalized_record"), Mapping) else {}
        approved += 1 if normalized.get("review_status") == "approved" else 0
        universal_ready += 1 if normalized.get("universal_event_status") == "universal_event_ready" else 0
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        **selection_report,
        "candidate_count": len(candidates),
        "registry_candidate_count": len(registry.get("candidates", [])),
        "created_this_run": registry.get("created_this_run", 0),
        "updated_this_run": registry.get("updated_this_run", 0),
        "queue_item_count": queue.get("queue_item_count", 0),
        "backlog_item_count": queue.get("backlog_item_count", 0),
        "duplicate_item_count": queue.get("duplicate_item_count", 0),
        "approved_count": approved,
        "universal_event_ready_count": universal_ready,
        "publication_state_mutated": False,
        "active_review_state_mutated": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Care Line source-evidence recovery.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--packet", default=str(DEFAULT_REVIEW_PACKET_OUTPUT))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--recovery-root", default=str(DEFAULT_RECOVERY_ROOT))
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--expected-packet-fingerprint", default="")
    parser.add_argument("--expected-record-fingerprint", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        packet = Path(args.packet)
        registry = Path(args.registry)
        recovery_root = Path(args.recovery_root)
        if args.check_only and args.apply:
            raise ValueError("--check-only and --apply are mutually exclusive")
        if args.recover and args.apply:
            raise ValueError("--recover and --apply are mutually exclusive")
        if args.apply and not args.expected_packet_fingerprint:
            raise ValueError("--apply requires --expected-packet-fingerprint")
        if args.apply:
            result = apply_recovery(
                repo_root=repo_root,
                packet_path=packet,
                recovery_root=recovery_root,
                expected_packet_fingerprint=args.expected_packet_fingerprint,
            )
        elif args.recover:
            result = recover_records(
                repo_root=repo_root,
                packet_path=packet,
                registry_path=registry,
                recovery_root=recovery_root,
                max_records=args.max_records,
                write_attempts=not args.check_only,
                expected_packet_fingerprint=args.expected_packet_fingerprint,
                expected_record_fingerprints=args.expected_record_fingerprint,
            )
        else:
            result = plan_recovery(repo_root=repo_root, packet_path=packet, registry_path=registry)
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
