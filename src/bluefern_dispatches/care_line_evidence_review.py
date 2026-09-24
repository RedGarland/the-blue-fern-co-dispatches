from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from bluefern_dispatches.care_line_national_pipeline import (
    NEGATIVE_EXCLUSION_REASONS,
    _access_consequences_from_text,
    event_lead_from_raw_item,
)
from bluefern_dispatches.care_line_record import CareLineReviewedRecord, FieldProvenance, deterministic_records_json, stable_json_hash


DECISION_SCHEMA_VERSION = "bluefern.care_line.evidence_review.v1"
REVIEW_PACKET_SCHEMA_VERSION = "bluefern.care_line.phase14b_evidence_review.v1"
REVIEW_PACKET_GENERATION_REPORT_SCHEMA_VERSION = "bluefern.care_line.evidence_review_packet_generation_report.v1"
LEDGER_SCHEMA_VERSION = "bluefern.care_line.evidence_decisions_ledger.v1"
REPORT_SCHEMA_VERSION = "bluefern.care_line.evidence_review_import_report.v1"
IMPORTER_VERSION = "care-line-evidence-review-import-v1"
PACKET_GENERATOR_VERSION = "care-line-evidence-review-packet-generator-v1"

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

DEFAULT_REVIEW_ROOT = Path("data") / "dispatches" / "care-line" / "review"
DEFAULT_REVIEW_PACKET_DIR = DEFAULT_REVIEW_ROOT / "evidence-review-packets"
DEFAULT_REVIEW_PACKET_OUTPUT = DEFAULT_REVIEW_PACKET_DIR / "current-care-line-evidence-review.json"
DEFAULT_REVIEW_PACKET_REPORT = DEFAULT_REVIEW_PACKET_DIR / "current-care-line-evidence-review-report.json"
DEFAULT_PRE_REVIEW_RECORDS_OUTPUT = DEFAULT_REVIEW_PACKET_DIR / "current-care-line-pre-review-records.json"
DEFAULT_RECOVERY_ROOT = DEFAULT_REVIEW_ROOT / "evidence-recovery"
SOURCE_REGISTRY_PATH = Path("data") / "dispatches" / "care-line" / "source_registry.json"

REVIEW_PACKET_INPUT_FILES = {
    "manual_review": "current-manual-review.json",
    "failed_extractions": "current-failed-extractions.json",
    "review_queue": "current-review-queue.json",
    "review_backlog": "current-review-backlog.json",
    "candidate_registry": "candidate-registry.json",
}

SOURCE_AUTHORITY_SCORE = {
    "primary": 4,
    "regulator": 3,
    "sector": 2,
    "secondary": 1,
}

EVENT_SEVERITY_SCORE = {
    "facility_closure": 5,
    "planned_facility_closure": 5,
    "service_closure": 4,
    "service_suspension": 4,
    "temporary_facility_suspension": 4,
    "hours_reduction": 3,
    "capacity_reduction": 3,
    "service_reduction": 3,
    "facility_relocation": 2,
    "facility_conversion": 2,
    "service_restoration": 1,
    "facility_reopening": 1,
}

STATE_PATTERN = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|PR|RI|SC|SD|TN|TX|UT|VA|VI|VT|WA|WI|WV|WY)\b"
)


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


def _load_json_object(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_registry(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / SOURCE_REGISTRY_PATH
    if not path.exists():
        return {}
    payload = _json(path)
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    return {
        _text(row, "source_id"): dict(row)
        for row in rows
        if isinstance(row, Mapping) and _text(row, "source_id")
    }


def _items(payload: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _canonical_url(row: Mapping[str, Any]) -> str:
    return _text(row, "source_url", "item_url", "canonical_url", "url")


def _normalize_identity_text(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _record_identity(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "source_record_id", "producer_record_id", "care_line_record_id", "raw_item_id", "exclusion_id", "lead_id")
    if explicit:
        return explicit
    return "care-line-review-input-" + _fingerprint(
        {
            "url": _canonical_url(row),
            "title": _text(row, "title", "source_title"),
            "supporting_text": _text(row, "supporting_text", "supporting_passage"),
        }
    )[:16]


def _source_id(row: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]]) -> str:
    explicit = _text(row, "source_id")
    if explicit:
        return explicit
    source_name = _text(row, "source", "source_name")
    for source_id, source in registry.items():
        if source_name and source_name in {_text(source, "name"), _text(source, "publisher")}:
            return source_id
    return ""


def _source_metadata(row: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    source_id = _source_id(row, registry)
    source = dict(registry.get(source_id, {}))
    return {
        "source_id": source_id,
        "source_name": _text(row, "source", "source_name") or _text(source, "name"),
        "publisher": _text(row, "publisher", "source_publisher") or _text(source, "publisher") or _text(row, "source", "source_name"),
        "source_type": _text(source, "source_type"),
        "authority_level": _text(source, "authority_level"),
        "source_role": _text(source, "source_role"),
        "geographic_scope": _text(source, "geographic_scope"),
        "registry_entry_found": bool(source),
    }


def _canonical_packet_url(row: Mapping[str, Any]) -> str:
    return _text(row, "canonical_source_url", "source_url", "item_url", "canonical_url", "url")


def _source_enabled(source: Mapping[str, Any]) -> bool:
    return bool(source) and bool(source.get("enabled", True))


def _source_allows_url(source: Mapping[str, Any], canonical_url: str) -> bool:
    parsed = urlparse(canonical_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    if not _source_enabled(source):
        return False
    if source.get("item_permalink_available") is False:
        return False
    allowed = source.get("allowed_hosts")
    if isinstance(allowed, list) and allowed:
        return parsed.netloc.casefold() in {str(host).strip().casefold() for host in allowed if str(host).strip()}
    homepage = _text(source, "homepage_url")
    feed = _text(source, "feed_url")
    permitted_hosts = {urlparse(value).netloc.casefold() for value in (homepage, feed) if urlparse(value).netloc}
    return not permitted_hosts or parsed.netloc.casefold() in permitted_hosts


def _iter_recovery_attempts(recovery_root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    if not recovery_root.exists():
        return
    for path in sorted(recovery_root.glob("*/*.json"), key=lambda item: item.as_posix()):
        try:
            payload = _load_json_object(path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, Mapping):
            yield path, dict(payload)


def _attempt_sort_key(item: tuple[Path, Mapping[str, Any]]) -> tuple[str, str]:
    path, attempt = item
    return (_text(attempt, "attempted_at"), path.as_posix())


def _matching_recovery_attempts(
    *,
    row: Mapping[str, Any],
    recovery_root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    record_fp = _text(row, "record_fingerprint")
    producer_id = _text(row, "producer_record_id")
    source_id = _text(row.get("source_metadata", {}) if isinstance(row.get("source_metadata"), Mapping) else {}, "source_id") or _text(row, "source_id")
    canonical_url = _canonical_packet_url(row)
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path, attempt in _iter_recovery_attempts(recovery_root):
        if _text(attempt, "record_fingerprint") != record_fp:
            continue
        if producer_id and _text(attempt, "producer_record_id") and _text(attempt, "producer_record_id") != producer_id:
            continue
        if source_id and _text(attempt, "source_id") and _text(attempt, "source_id") != source_id:
            continue
        if canonical_url and _text(attempt, "attempted_url") and _text(attempt, "attempted_url") != canonical_url:
            continue
        matches.append((path, attempt))
    return sorted(matches, key=_attempt_sort_key)


def _recovery_feedback_payload(path: Path, attempt: Mapping[str, Any], *, disposition: str) -> dict[str, Any]:
    qualification = attempt.get("qualification_result") if isinstance(attempt.get("qualification_result"), Mapping) else {}
    payload = {
        "disposition": disposition,
        "authority": "care_line_evidence_recovery",
        "attempt_id": _text(attempt, "attempt_id"),
        "attempt_path": path.as_posix(),
        "attempted_at": _text(attempt, "attempted_at"),
        "prior_packet_fingerprint": _text(attempt, "packet_fingerprint"),
        "record_fingerprint": _text(attempt, "record_fingerprint"),
        "recovered_evidence_fingerprint": _text(attempt, "recovered_evidence_fingerprint"),
        "care_qualification_status": _text(attempt, "care_qualification_status"),
        "result_status": _text(attempt, "result_status"),
        "route": _text(attempt, "route"),
    }
    if qualification:
        payload["qualification_result"] = qualification
        if _text(qualification, "exclusion_reason"):
            payload["exclusion_reason"] = _text(qualification, "exclusion_reason")
    if _text(attempt, "carried_forward_from_attempt_id"):
        payload["carried_forward_from_attempt_id"] = _text(attempt, "carried_forward_from_attempt_id")
    return payload


def _unrecoverable_still_current(row: Mapping[str, Any], attempt: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]]) -> bool:
    route = _text(attempt, "route")
    outcome = _text(row, "extraction_outcome")
    canonical_url = _canonical_packet_url(row)
    source_id = _text(row.get("source_metadata", {}) if isinstance(row.get("source_metadata"), Mapping) else {}, "source_id") or _text(row, "source_id")
    source = registry.get(source_id, {})
    if route == "repeated_blocked_no_route":
        return outcome in {"ACCESS_BLOCKED", "PAYWALLED"}
    if route == "stale_or_invalid_url":
        return not _source_allows_url(source, canonical_url)
    if route == "not_recoverable_with_current_source":
        return not _source_enabled(source)
    return False


def _candidate_id_from_attempt(attempt: Mapping[str, Any]) -> str:
    candidate = attempt.get("candidate") if isinstance(attempt.get("candidate"), Mapping) else {}
    return _text(candidate, "candidate_id")


def _overlay_recovery_feedback(
    packet_row: dict[str, Any],
    *,
    recovery_root: Path,
    registry: Mapping[str, Mapping[str, Any]],
    applied_candidate_ids: set[str],
) -> dict[str, Any]:
    matches = _matching_recovery_attempts(row=packet_row, recovery_root=recovery_root)
    if not matches:
        return packet_row
    path, attempt = matches[-1]
    status = _text(attempt, "result_status")
    if status == "DETERMINISTIC_EXCLUSION":
        feedback = _recovery_feedback_payload(path, attempt, disposition="DETERMINISTIC_EXCLUSION")
        packet_row["resolution_bucket"] = "exclusion"
        packet_row["human_review_required_reason"] = _review_action("exclusion")
        packet_row["recommended_next_review_action"] = _review_action("exclusion")
        if feedback.get("exclusion_reason"):
            packet_row["exclusion_reason"] = feedback["exclusion_reason"]
        packet_row["recovery_feedback"] = feedback
        return packet_row
    if status == "NOT_RECOVERABLE_WITH_CURRENT_SOURCE":
        if _unrecoverable_still_current(packet_row, attempt, registry):
            packet_row["resolution_bucket"] = "unrecoverable"
            packet_row["human_review_required_reason"] = _review_action("unrecoverable")
            packet_row["recommended_next_review_action"] = _review_action("unrecoverable")
            packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition="NOT_RECOVERABLE_WITH_CURRENT_SOURCE")
        else:
            packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition="IGNORED_STALE")
        return packet_row
    if status in {"FETCH_FAILED", "STILL_ADDITIONAL_FETCH_NEEDED", "UNRESOLVED_EVIDENCE"}:
        disposition = "FETCH_FAILED" if status == "FETCH_FAILED" else "STILL_UNRESOLVED"
        packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition=disposition)
        return packet_row
    if status == "QUALIFIED_PRIVATE_CANDIDATE":
        candidate_id = _candidate_id_from_attempt(attempt)
        if candidate_id and candidate_id in applied_candidate_ids:
            packet_row["resolution_bucket"] = "recovered_private_candidate"
            packet_row["human_review_required_reason"] = "Recovered private candidate is present in the current private candidate registry."
            packet_row["recommended_next_review_action"] = packet_row["human_review_required_reason"]
            packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition="RESOLVED_PRIVATE_CANDIDATE")
        else:
            packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition="QUALIFIED_PENDING_APPLY")
        return packet_row
    packet_row["recovery_feedback"] = _recovery_feedback_payload(path, attempt, disposition="IGNORED_STALE")
    return packet_row


def _proposed_with_provenance(value: Any, *, source_field: str, method: str, source_text: str = "") -> dict[str, Any]:
    return {
        "value": value,
        "provenance": {
            "source_field": source_field,
            "method": method,
            "source_text": source_text,
        },
    }


def _list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value not in (None, "", {}, []):
        return [str(value).strip()]
    return []


def _normalized_reason(value: str) -> str:
    return str(value or "").strip().casefold()


def _raw_item_for_semantic_triage(row: Mapping[str, Any], *, title: str, supporting_text: str, canonical_url: str) -> dict[str, Any]:
    return {
        "raw_item_id": _text(row, "raw_item_id", "source_record_id", "producer_record_id", "care_line_record_id"),
        "source_id": _text(row, "source_id"),
        "source_name": _text(row, "source_name", "source", "publisher", "source_publisher"),
        "item_url": canonical_url,
        "title": title,
        "description": supporting_text or _text(row, "description", "summary", "excerpt"),
        "source_publication_date": _text(row, "source_publication_date", "publication_date", "published_at", "source_published_date"),
        "source_date_state": _text(row, "source_date_state"),
        "requires_html_followup": bool(row.get("requires_html_followup")),
    }


def _existing_event_type(row: Mapping[str, Any]) -> str:
    return _text(row, "event_type", "event_type_hint", "event_type_candidate")


def _existing_service_line(row: Mapping[str, Any]) -> str:
    return _text(row, "service_line", "service_line_hint", "service_line_candidate")


def _existing_access_consequences(row: Mapping[str, Any]) -> list[str]:
    value = row.get("access_consequences") or row.get("access_consequence") or row.get("access_consequence_candidate")
    if isinstance(value, list):
        results: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = _text(item, "value", "label", "type")
            else:
                text = str(item).strip()
            if text:
                results.append(text)
        return results
    return _list_values(value)


RECOVERABLE_FAILED_EXTRACTION_REASONS = {
    "needs_full_article",
    "needs_date",
    "missing_source_date",
    "needs_geography",
    "missing_geography",
    "needs_access_consequence",
    "missing_subject",
    "missing_event_type",
    "missing_service_line_or_facility_scope",
    "insufficient_bounded_evidence",
    "private_or_inaccessible_evidence",
}


def _recoverable_reason(exclusion_reason: str, failed_gates: set[str]) -> str:
    if exclusion_reason in RECOVERABLE_FAILED_EXTRACTION_REASONS:
        return exclusion_reason
    for reason in RECOVERABLE_FAILED_EXTRACTION_REASONS:
        if reason in failed_gates:
            return reason
    return ""


def _recoverable_semantic_result(
    *,
    authority: str,
    reason: str,
    event_type: str = "",
    service_line: str = "",
    access_consequences: list[str] | None = None,
    source_text: str = "",
    lead: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "authority": authority,
        "qualification_status": "recoverable_failed_extraction",
        "exclusion_reason": reason,
        "event_type": event_type,
        "service_line": service_line,
        "access_consequences": [
            {"value": value, "matched_text": value}
            for value in (access_consequences or [])
        ],
        "source_text": source_text,
    }
    if lead is not None:
        payload["lead"] = dict(lead)
    return payload


def _packet_semantic_triage(
    row: Mapping[str, Any],
    *,
    title: str,
    supporting_text: str,
    canonical_url: str,
) -> dict[str, Any]:
    classification = _text(row, "classification", "editorial_outcome")
    normalized_classification = _normalized_reason(classification)
    exclusion_reason = _normalized_reason(_text(row, "exclusion_reason", "normalized_reason"))
    extraction_outcome = _text(row, "extraction_outcome")
    failed_gates = set(_list_values(row.get("missing_fields")) + _list_values(row.get("failed_gates")))

    existing_event_type = _existing_event_type(row)
    existing_service_line = _existing_service_line(row)
    existing_access = _existing_access_consequences(row)
    has_existing_care_evidence = bool(existing_event_type or existing_service_line or existing_access)
    recoverable_reason = _recoverable_reason(exclusion_reason, failed_gates)

    if exclusion_reason in NEGATIVE_EXCLUSION_REASONS or normalized_classification in NEGATIVE_EXCLUSION_REASONS:
        return {
            "authority": "existing_pipeline_exclusion",
            "qualification_status": "excluded",
            "exclusion_reason": exclusion_reason or normalized_classification,
            "event_type": "",
            "service_line": "",
            "access_consequences": [],
            "source_text": _text(row, "exclusion_reason", "classification", "editorial_outcome"),
        }

    if _text(row, "prefilter_decision") == "discard":
        return {
            "authority": "existing_prefilter_discard",
            "qualification_status": "excluded",
            "exclusion_reason": exclusion_reason or "non_care_line",
            "event_type": "",
            "service_line": "",
            "access_consequences": [],
            "source_text": _text(row, "normalized_reason", "exclusion_reason", "prefilter_decision"),
        }

    if has_existing_care_evidence:
        access = existing_access
        if not access and existing_event_type and supporting_text:
            access, _access_exception = _access_consequences_from_text(supporting_text, existing_event_type)
        if recoverable_reason:
            return _recoverable_semantic_result(
                authority="existing_pipeline_fields",
                reason=recoverable_reason,
                event_type=existing_event_type,
                service_line=existing_service_line,
                access_consequences=access,
                source_text=supporting_text,
            )
        return {
            "authority": "existing_pipeline_fields",
            "qualification_status": "event_lead",
            "exclusion_reason": "",
            "event_type": existing_event_type,
            "service_line": existing_service_line,
            "access_consequences": [{"value": value, "matched_text": value} for value in access],
            "source_text": supporting_text,
        }

    lead = event_lead_from_raw_item(
        _raw_item_for_semantic_triage(row, title=title, supporting_text=supporting_text, canonical_url=canonical_url)
    )
    if _text(lead, "qualification_status") == "event_lead":
        event_type = _text(lead, "event_type_hint")
        service_line = _text(lead, "service_line_hint")
        if _weak_fallback_event_phrase(f"{title} {supporting_text}", event_type):
            return _recoverable_semantic_result(
                authority="care_line_event_lead_helper",
                reason=recoverable_reason or "insufficient_bounded_evidence",
                source_text=recoverable_reason or "weak_fallback_event_phrase",
                lead=lead,
            )
        access, _access_exception = _access_consequences_from_text(supporting_text, event_type)
        facility, _facility_match = _strict_provider_candidate(supporting_text, title)
        if not access or not facility:
            reason = recoverable_reason or ("needs_access_consequence" if not access else "missing_subject")
            return _recoverable_semantic_result(
                authority="care_line_event_lead_helper",
                reason=reason,
                source_text=reason,
                lead=lead,
            )
        if recoverable_reason:
            return _recoverable_semantic_result(
                authority="care_line_event_lead_helper",
                reason=recoverable_reason,
                event_type=event_type,
                service_line=service_line,
                access_consequences=access,
                source_text=supporting_text,
                lead=lead,
            )
        return {
            "authority": "care_line_event_lead_helper",
            "qualification_status": "event_lead",
            "exclusion_reason": "",
            "event_type": event_type,
            "service_line": service_line,
            "access_consequences": [{"value": value, "matched_text": value} for value in access],
            "source_text": supporting_text,
            "lead": lead,
        }

    reason = _normalized_reason(_text(lead, "exclusion_reason")) or exclusion_reason or "non_care_line"
    if reason in RECOVERABLE_FAILED_EXTRACTION_REASONS:
        return _recoverable_semantic_result(
            authority="existing_pipeline_failed_gates",
            reason=reason,
            source_text=", ".join(sorted(failed_gates)),
            lead=lead,
        )
    source_text = " | ".join(
        part
        for part in (
            _text(lead, "exclusion_reason"),
            ", ".join(sorted(failed_gates)),
        )
        if part
    )
    return {
        "authority": "care_line_event_lead_helper",
        "qualification_status": "excluded",
        "exclusion_reason": reason,
        "event_type": "",
        "service_line": "",
        "access_consequences": [],
        "source_text": source_text,
        "lead": lead,
    }


def _facility_candidate(text: str, title: str) -> tuple[str, str]:
    combined = " ".join(part for part in (title, text) if part)
    patterns = (
        re.compile(r"\b([A-Z][A-Za-z&.' -]+(?:Hospital|Medical Center|Clinic|Health Center|Health System|ER|Urgent Care))\b"),
        re.compile(r"\b([A-Z][A-Za-z&.' -]+(?:Health|Healthcare))\b"),
    )
    for pattern in patterns:
        match = pattern.search(combined)
        if match:
            return match.group(1).strip(), match.group(0)
    return "", ""


def _strict_provider_candidate(text: str, title: str) -> tuple[str, str]:
    combined = " ".join(part for part in (title, text) if part)
    patterns = (
        re.compile(r"\b([A-Z][A-Za-z&.' -]+(?:Hospital|Medical Center|Clinic|Health Center|Health System|ER|Urgent Care))\b"),
        re.compile(r"\b((?:emergency department|emergency room|labor and delivery|birth center|maternity unit) services? at [A-Z][A-Za-z&.' -]+)\b", re.I),
    )
    for pattern in patterns:
        match = pattern.search(combined)
        if match:
            return match.group(1).strip(), match.group(0)
    return "", ""


def _weak_fallback_event_phrase(text: str, event_type: str) -> bool:
    if event_type not in {"facility_closure", "planned_facility_closure", "service_closure"}:
        return False
    return bool(
        re.search(r"\bclos(?:e|ing) (?:the )?[^.]{0,80}\bgap\b", text, re.I)
        or re.search(r"\bat the end of the power grid\b", text, re.I)
    )


def _geography_candidate(text: str, title: str) -> tuple[dict[str, str], str]:
    combined = " ".join(part for part in (title, text) if part)
    state_match = STATE_PATTERN.search(combined)
    if not state_match:
        return {}, ""
    city_match = re.search(r"\b([A-Z][A-Za-z.' -]+),\s*" + re.escape(state_match.group(1)) + r"\b", combined)
    geo = {"state": state_match.group(1)}
    if city_match:
        geo["city"] = city_match.group(1).strip()
    return geo, city_match.group(0) if city_match else state_match.group(0)


def _date_candidate(row: Mapping[str, Any]) -> tuple[str, str]:
    for key in ("publication_date", "source_publication_date", "published_at", "source_published_date", "operative_event_date"):
        value = _text(row, key)
        if value:
            return value[:10], key
    text = " ".join(
        _text(row, key)
        for key in ("title", "supporting_text", "source_title", "supporting_passage")
        if _text(row, key)
    )
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1), "source_text"
    return "", ""


def _validation_results(row: Mapping[str, Any], *, canonical_url: str, supporting_text: str, proposed_fields: Mapping[str, Any]) -> dict[str, Any]:
    parsed = urlparse(canonical_url)
    unresolved: list[str] = []
    checks = {
        "canonical_https_url": bool(canonical_url and parsed.scheme == "https" and parsed.netloc),
        "supporting_passage_present": bool(supporting_text),
        "source_date_present": bool((proposed_fields.get("publication_date") or {}).get("value")),
        "event_type_candidate_present": bool((proposed_fields.get("event_type_candidate") or {}).get("value")),
        "source_record_id_present": bool(_record_identity(row)),
    }
    if not checks["canonical_https_url"]:
        unresolved.append("canonical_https_url")
    if not checks["supporting_passage_present"]:
        unresolved.append("supporting_passage")
    if not checks["source_date_present"]:
        unresolved.append("source_publication_date")
    if not checks["event_type_candidate_present"]:
        unresolved.append("event_type")
    if not checks["source_record_id_present"]:
        unresolved.append("stable_record_id")
    for missing in row.get("missing_fields") or row.get("failed_gates") or []:
        if isinstance(missing, str) and missing not in unresolved:
            unresolved.append(missing)
    return {
        "checks": checks,
        "unresolved_fields": sorted(unresolved),
        "lineage_complete_for_review": checks["canonical_https_url"] and checks["source_record_id_present"],
        "lineage_complete_for_universal_event": all(checks.values()),
    }


def _resolution_bucket(row: Mapping[str, Any], validation: Mapping[str, Any], *, semantic: Mapping[str, Any], duplicate_of: str = "") -> str:
    outcome = _text(row, "extraction_outcome")
    classification = _text(row, "classification", "editorial_outcome")
    unresolved = set(validation.get("unresolved_fields") or [])
    semantic_status = _text(semantic, "qualification_status")
    semantic_reason = _text(semantic, "exclusion_reason")
    if duplicate_of:
        return "duplicate"
    if semantic_status == "excluded":
        return "exclusion"
    if classification in {"NON_CARE_LINE", "GENERAL_HEALTHCARE_NEWS"} or _text(row, "exclusion_reason") in {"non_care_line", "general_healthcare_news"}:
        return "exclusion"
    if outcome in {"ACCESS_BLOCKED", "PAYWALLED", "SCRIPT_RENDERED", "PDF_REQUIRED"}:
        return "additional_fetch_needed"
    if semantic_status == "recoverable_failed_extraction":
        if semantic_reason in {"needs_full_article", "insufficient_bounded_evidence", "private_or_inaccessible_evidence"}:
            return "additional_fetch_needed"
        if not _text(semantic, "event_type"):
            return "additional_fetch_needed"
        if "source_publication_date" in unresolved or "missing_source_date" in unresolved:
            return "deterministic_resolution"
        return "human_evidence_judgment"
    if "canonical_https_url" in unresolved:
        return "canonical_source_lookup"
    if {"supporting_passage", "insufficient_bounded_evidence"} & unresolved:
        return "human_evidence_judgment"
    if "source_publication_date" in unresolved or "missing_source_date" in unresolved:
        return "deterministic_resolution"
    if _text(row, "failed_gates") or unresolved:
        return "human_evidence_judgment"
    return "bounded_human_judgment"


def _review_action(bucket: str) -> str:
    return {
        "deterministic_resolution": "Resolve objective missing fields from source-collected evidence before reviewer decision.",
        "additional_fetch_needed": "Fetch canonical source text, then decide whether quoted evidence supports a Care Line event.",
        "canonical_source_lookup": "Locate a canonical HTTPS publisher URL before evidence review.",
        "human_evidence_judgment": "Human must decide whether bounded source evidence supports the proposed structured event.",
        "editorial_judgment": "Human must decide editorial significance after evidence is complete.",
        "exclusion": "Confirm deterministic exclusion or leave excluded.",
        "duplicate": "Review representative packet only; duplicate suppressed by deterministic identity.",
        "unrecoverable": "Record source/access blocker; do not promote without new source evidence.",
        "recovered_private_candidate": "Recovered private candidate is already present in private review state.",
        "bounded_human_judgment": "Review proposed fields and choose approve, correct, defer, care-line-only, or reject.",
    }.get(bucket, "Review unresolved fields.")


def _packet_record(row: Mapping[str, Any], *, record_family: str, registry: Mapping[str, Mapping[str, Any]], duplicate_of: str = "") -> dict[str, Any]:
    canonical_url = _canonical_url(row)
    title = _text(row, "title", "source_title")
    supporting_text = _text(row, "supporting_text", "supporting_passage", "effective_evidence_text")
    date_value, date_source = _date_candidate(row)
    semantic = _packet_semantic_triage(row, title=title, supporting_text=supporting_text, canonical_url=canonical_url)
    event_type = _text(semantic, "event_type")
    service_line = _text(semantic, "service_line")
    facility, facility_match = _facility_candidate(supporting_text, title)
    geography, geography_match = _geography_candidate(supporting_text, title)
    access_hits = [dict(hit) for hit in semantic.get("access_consequences") or [] if isinstance(hit, Mapping)]
    source_metadata = _source_metadata(row, registry)
    source_artifact_paths = []
    lineage = row.get("lineage") if isinstance(row.get("lineage"), Mapping) else {}
    if _text(lineage, "source_artifact_path"):
        source_artifact_paths.append(_text(lineage, "source_artifact_path"))
    semantic_source = _text(semantic, "source_text") or supporting_text
    semantic_method = _text(semantic, "authority") or "care_line_event_lead_helper"
    proposed_fields = {
        "publication_date": _proposed_with_provenance(date_value, source_field=date_source, method="copied_or_text_date", source_text=date_value),
        "event_type_candidate": _proposed_with_provenance(event_type, source_field="care_line_national_pipeline", method=semantic_method, source_text=semantic_source),
        "service_line_candidate": _proposed_with_provenance(service_line, source_field="care_line_national_pipeline", method=semantic_method, source_text=semantic_source),
        "facility_provider_candidate": _proposed_with_provenance(facility, source_field="title/supporting_text", method="source_explicit_pattern", source_text=facility_match),
        "geography_candidate": _proposed_with_provenance(geography, source_field="title/supporting_text", method="state_city_pattern", source_text=geography_match),
        "access_consequence_candidate": _proposed_with_provenance(access_hits, source_field="care_line_national_pipeline", method=semantic_method, source_text=" | ".join(str(hit.get("matched_text") or hit.get("value") or "") for hit in access_hits)),
    }
    validation = _validation_results(row, canonical_url=canonical_url, supporting_text=supporting_text, proposed_fields=proposed_fields)
    bucket = _resolution_bucket(row, validation, semantic=semantic, duplicate_of=duplicate_of)
    record_fingerprint = _fingerprint(
        {
            "record_id": _record_identity(row),
            "record_family": record_family,
            "canonical_url": canonical_url,
            "title": title,
            "supporting_text": supporting_text,
            "proposed_fields": proposed_fields,
            "missing_requirements": validation["unresolved_fields"],
            "packet_semantic_triage": {
                "authority": _text(semantic, "authority"),
                "qualification_status": _text(semantic, "qualification_status"),
                "exclusion_reason": _text(semantic, "exclusion_reason"),
            },
        }
    )
    completeness_score = sum(1 for value in proposed_fields.values() if value.get("value") not in ("", [], {}))
    severity_score = EVENT_SEVERITY_SCORE.get(str(proposed_fields["event_type_candidate"].get("value") or ""), 0)
    authority_score = SOURCE_AUTHORITY_SCORE.get(str(source_metadata.get("authority_level") or ""), 0)
    access_score = min(len(access_hits), 3)
    return {
        "producer_record_id": _record_identity(row),
        "record_family": record_family,
        "record_fingerprint": record_fingerprint,
        "canonical_source_url": canonical_url,
        "source_title": title,
        "source_publisher": source_metadata["publisher"],
        "source_metadata": source_metadata,
        "source_artifact_paths": source_artifact_paths,
        "event_lead_id": _text(row, "lead_id"),
        "raw_item_id": _text(row, "raw_item_id"),
        "source_record_id": _text(row, "source_record_id", "producer_record_id", "care_line_record_id"),
        "extraction_outcome": _text(row, "extraction_outcome"),
        "current_qualification_state": _text(row, "classification", "editorial_outcome") or record_family,
        "supporting_passage": supporting_text,
        "supporting_passage_available": bool(supporting_text),
        "proposed_fields": proposed_fields,
        "objective_validation_results": validation,
        "unresolved_fields": validation["unresolved_fields"],
        "exact_missing_requirements": validation["unresolved_fields"],
        "exclusion_reason": _text(semantic, "exclusion_reason") or _text(row, "exclusion_reason"),
        "packet_semantic_triage": {
            "authority": _text(semantic, "authority"),
            "qualification_status": _text(semantic, "qualification_status"),
            "exclusion_reason": _text(semantic, "exclusion_reason"),
            "lead_qualification_status": _nested_text(semantic, ("lead", "qualification_status")),
            "lead_exclusion_reason": _nested_text(semantic, ("lead", "exclusion_reason")),
            "copied_existing_pipeline_fields": _text(semantic, "authority") == "existing_pipeline_fields",
        },
        "duplicate_of_producer_record_id": duplicate_of,
        "duplicate_identity": _duplicate_key(row),
        "resolution_bucket": bucket,
        "human_review_required_reason": _review_action(bucket),
        "recommended_next_review_action": _review_action(bucket),
        "priority": {
            "completeness_score": completeness_score,
            "source_authority_score": authority_score,
            "access_consequence_score": access_score,
            "event_severity_score": severity_score,
            "source_date": date_value,
        },
        "automation_limits": {
            "review_status_set": False,
            "universal_event_ready_set": False,
            "publication_invoked": False,
        },
    }


def _duplicate_key(row: Mapping[str, Any]) -> str:
    stable_source_id = _text(row, "source_record_id", "producer_record_id", "care_line_record_id")
    if stable_source_id:
        return "stable-source-id:" + stable_source_id
    event_id = _text(row, "event_instance_id", "event_identity")
    if event_id:
        return "event-id:" + event_id
    return "content:" + _fingerprint(
        {
            "canonical_url": _canonical_url(row).rstrip("/").casefold(),
            "title": _normalize_identity_text(_text(row, "title", "source_title")),
            "supporting_text": _normalize_identity_text(_text(row, "supporting_text", "supporting_passage", "effective_evidence_text")),
        }
    )[:24]


def _packet_value(packet_row: Mapping[str, Any], field_name: str) -> Any:
    field = packet_row.get("proposed_fields")
    if not isinstance(field, Mapping):
        return ""
    payload = field.get(field_name)
    if isinstance(payload, Mapping):
        return payload.get("value")
    return ""


def _field_provenance(packet_row: Mapping[str, Any], field_name: str) -> FieldProvenance:
    field = packet_row.get("proposed_fields")
    payload = field.get(field_name) if isinstance(field, Mapping) else {}
    provenance = payload.get("provenance") if isinstance(payload, Mapping) else {}
    return FieldProvenance(
        value=(payload.get("value") if isinstance(payload, Mapping) else None),
        provenance_type="deterministic_extraction",
        source_field=_text(provenance, "source_field") if isinstance(provenance, Mapping) else field_name,
        supporting_text=_text(provenance, "source_text") if isinstance(provenance, Mapping) else "",
        confidence=0.0,
        review_status="proposed",
        rule_id="care-line-evidence-review-packet-generator",
        rule_version=PACKET_GENERATOR_VERSION,
    )


def pre_review_record_from_packet_row(packet_row: Mapping[str, Any], *, packet_fingerprint: str) -> CareLineReviewedRecord:
    if _text(packet_row, "duplicate_of_producer_record_id"):
        raise ValueError("duplicate packet rows cannot be converted into pre-review records")
    source_metadata = packet_row.get("source_metadata") if isinstance(packet_row.get("source_metadata"), Mapping) else {}
    geography = _packet_value(packet_row, "geography_candidate")
    geography = geography if isinstance(geography, Mapping) else {}
    access_values = _packet_value(packet_row, "access_consequence_candidate")
    access_consequences = [str(row.get("value")) for row in access_values if isinstance(row, Mapping) and row.get("value")] if isinstance(access_values, list) else []
    producer_record_id = _text(packet_row, "producer_record_id")
    raw_payload_hash = _packet_record_fingerprint(packet_row)
    if not producer_record_id or not raw_payload_hash:
        raise ValueError("packet row must include producer_record_id and record_fingerprint")
    payload = {
        "producer_record_id": producer_record_id,
        "record_status": "needs_evidence_review",
        "review_status": "not_reviewed",
        "public_status": "not_public",
        "universal_event_status": "needs_evidence_review",
        "care_line_public_eligible": False,
        "source_url": _text(packet_row, "canonical_source_url"),
        "source_title": _text(packet_row, "source_title"),
        "source_publisher": _text(packet_row, "source_publisher"),
        "source_publication_date": str(_packet_value(packet_row, "publication_date") or ""),
        "source_type": _text(source_metadata, "source_type") if isinstance(source_metadata, Mapping) else "",
        "source_role": _text(source_metadata, "source_role") if isinstance(source_metadata, Mapping) else "",
        "supporting_passage": _text(packet_row, "supporting_passage"),
        "effective_evidence_text": _text(packet_row, "supporting_passage"),
        "evidence_provenance_type": "deterministic_extraction",
        "evidence_valid_for_universal_event": False,
        "recommended_status": "needs_evidence_review",
        "review_notes": _text(packet_row, "human_review_required_reason"),
        "raw_payload_hash": raw_payload_hash,
        "event_type": str(_packet_value(packet_row, "event_type_candidate") or ""),
        "service_line": str(_packet_value(packet_row, "service_line_candidate") or ""),
        "announcement_date": str(_packet_value(packet_row, "publication_date") or ""),
        "date_precision": "day" if _packet_value(packet_row, "publication_date") else "",
        "facility_name": str(_packet_value(packet_row, "facility_provider_candidate") or ""),
        "city": str(geography.get("city") or ""),
        "state": str(geography.get("state") or ""),
        "location_text": ", ".join(part for part in (str(geography.get("city") or ""), str(geography.get("state") or "")) if part),
        "access_consequences": access_consequences,
        "verification_state": "DISCOVERED",
        "workflow_state": "NEEDS_REVIEW",
        "authority_level": _text(source_metadata, "authority_level") if isinstance(source_metadata, Mapping) else "",
        "is_primary_source": _text(source_metadata, "authority_level") in {"primary", "official"} if isinstance(source_metadata, Mapping) else False,
        "claim_summary": _text(packet_row, "supporting_passage"),
        "evidence_level": "bounded_source_evidence" if _text(packet_row, "supporting_passage") else "insufficient_evidence",
        "originating_intake_record_id": producer_record_id,
        "field_provenance": {
            name: _field_provenance(packet_row, name)
            for name in (
                "publication_date",
                "event_type_candidate",
                "service_line_candidate",
                "facility_provider_candidate",
                "geography_candidate",
                "access_consequence_candidate",
            )
        },
        "metadata": {
            "pre_review_intake_bridge": True,
            "packet_fingerprint": packet_fingerprint,
            "packet_record_fingerprint": raw_payload_hash,
            "record_family": _text(packet_row, "record_family"),
            "source_id": _text(source_metadata, "source_id") if isinstance(source_metadata, Mapping) else "",
            "source_record_id": _text(packet_row, "source_record_id"),
            "raw_item_id": _text(packet_row, "raw_item_id"),
            "event_lead_id": _text(packet_row, "event_lead_id"),
            "source_artifact_paths": list(packet_row.get("source_artifact_paths") or []),
            "proposed_fields": packet_row.get("proposed_fields") if isinstance(packet_row.get("proposed_fields"), Mapping) else {},
            "unresolved_fields": list(packet_row.get("unresolved_fields") or []),
            "objective_validation_results": packet_row.get("objective_validation_results") if isinstance(packet_row.get("objective_validation_results"), Mapping) else {},
            "automation_limits": packet_row.get("automation_limits") if isinstance(packet_row.get("automation_limits"), Mapping) else {},
        },
    }
    return CareLineReviewedRecord.model_validate(payload)


def build_pre_review_records_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    packet_fingerprint = review_packet_fingerprint(packet)
    records = [
        pre_review_record_from_packet_row(row, packet_fingerprint=packet_fingerprint)
        for row in packet.get("records") or []
        if isinstance(row, Mapping) and not _text(row, "duplicate_of_producer_record_id")
    ]
    return {
        "schema_version": "bluefern.care_line.reviewed_record.v1",
        "records": [record.model_dump(mode="json") for record in sorted(records, key=lambda row: row.producer_record_id)],
        "metadata": {
            "pre_review_intake_bridge": True,
            "packet_fingerprint": packet_fingerprint,
            "automated_review_status_changes": False,
            "automated_universal_event_ready": False,
            "publication_invoked": False,
            "queue_release_state_changed": False,
        },
    }


def build_review_packet_from_current_state(
    repo_root: Path,
    *,
    review_root: Path | None = None,
    recovery_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    root = review_root or DEFAULT_REVIEW_ROOT
    root = root if root.is_absolute() else repo_root / root
    recovery = recovery_root or DEFAULT_RECOVERY_ROOT
    recovery = recovery if recovery.is_absolute() else repo_root / recovery
    registry = _load_registry(repo_root)
    manual_payload = _load_json_object(root / REVIEW_PACKET_INPUT_FILES["manual_review"], default={"items": []})
    failed_payload = _load_json_object(root / REVIEW_PACKET_INPUT_FILES["failed_extractions"], default={"items": []})
    queue_payload = _load_json_object(root / REVIEW_PACKET_INPUT_FILES["review_queue"], default={"items": [], "duplicates": [], "backlog": []})
    backlog_payload = _load_json_object(root / REVIEW_PACKET_INPUT_FILES["review_backlog"], default={"items": []})
    candidate_payload = _load_json_object(root / REVIEW_PACKET_INPUT_FILES["candidate_registry"], default={"candidates": []})
    applied_candidate_ids = {
        _text(row, "candidate_id")
        for row in _items(candidate_payload, "candidates")
        if _text(row, "candidate_id")
    }

    raw_inputs: list[tuple[str, dict[str, Any]]] = []
    raw_inputs.extend(("manual_review", row) for row in _items(manual_payload, "items"))
    raw_inputs.extend(("failed_extraction", row) for row in _items(failed_payload, "items"))
    raw_inputs.extend(("review_queue", row) for row in _items(queue_payload, "items"))
    raw_inputs.extend(("review_backlog", row) for row in _items(backlog_payload, "items", "backlog"))
    raw_inputs.extend(("candidate_registry", row) for row in _items(candidate_payload, "candidates"))

    representative_by_key: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    for family, row in raw_inputs:
        key = _duplicate_key(row)
        duplicate_of = representative_by_key.get(key, "")
        packet_row = _packet_record(row, record_family=family, registry=registry, duplicate_of=duplicate_of)
        if not duplicate_of:
            packet_row = _overlay_recovery_feedback(
                packet_row,
                recovery_root=recovery,
                registry=registry,
                applied_candidate_ids=applied_candidate_ids,
            )
        if not duplicate_of:
            representative_by_key[key] = packet_row["producer_record_id"]
        records.append(packet_row)

    records.sort(
        key=lambda row: (
            1 if row.get("duplicate_of_producer_record_id") else 0,
            -int(row["priority"]["completeness_score"]),
            -int(row["priority"]["source_authority_score"]),
            -int(row["priority"]["access_consequence_score"]),
            -int(row["priority"]["event_severity_score"]),
            str(row["priority"].get("source_date") or ""),
            row["producer_record_id"],
        )
    )
    return {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "packet_type": "current_care_line_evidence_review",
        "generator_version": PACKET_GENERATOR_VERSION,
        "source_review_root": root.relative_to(repo_root).as_posix() if _is_under(root, repo_root) else root.as_posix(),
        "recovery_feedback_root": recovery.relative_to(repo_root).as_posix() if _is_under(recovery, repo_root) else recovery.as_posix(),
        "records": records,
        "decision_policy": {
            "automated_review_status_changes": False,
            "automated_universal_event_ready": False,
            "publication_invoked": False,
            "human_decision_required_for_approval": True,
        },
    }


def review_packet_generation_report(packet: Mapping[str, Any]) -> dict[str, Any]:
    records = [row for row in packet.get("records") or [] if isinstance(row, Mapping)]
    bucket_counts = Counter(_text(row, "resolution_bucket") for row in records)
    family_counts = Counter(_text(row, "record_family") for row in records)
    feedback_counts = Counter()
    for row in records:
        feedback = row.get("recovery_feedback")
        if not isinstance(feedback, Mapping):
            continue
        disposition = _text(feedback, "disposition")
        if disposition == "DETERMINISTIC_EXCLUSION":
            feedback_counts["recovery_deterministic_exclusion"] += 1
        elif disposition == "NOT_RECOVERABLE_WITH_CURRENT_SOURCE":
            feedback_counts["recovery_unrecoverable"] += 1
        elif disposition == "FETCH_FAILED":
            feedback_counts["recovery_fetch_failed"] += 1
        elif disposition == "STILL_UNRESOLVED":
            feedback_counts["recovery_still_unresolved"] += 1
        elif disposition == "QUALIFIED_PENDING_APPLY":
            feedback_counts["recovery_qualified_pending_apply"] += 1
        elif disposition == "RESOLVED_PRIVATE_CANDIDATE":
            feedback_counts["recovery_resolved_private_candidate"] += 1
        elif disposition == "IGNORED_STALE":
            feedback_counts["recovery_feedback_ignored_stale"] += 1
    unique_representatives = [row for row in records if not _text(row, "duplicate_of_producer_record_id")]
    bounded = [
        row
        for row in unique_representatives
        if _text(row, "resolution_bucket") in {"human_evidence_judgment", "bounded_human_judgment"}
    ]
    return {
        "schema_version": REVIEW_PACKET_GENERATION_REPORT_SCHEMA_VERSION,
        "generator_version": PACKET_GENERATOR_VERSION,
        "review_packet_schema_version": packet.get("schema_version"),
        "review_packet_fingerprint": review_packet_fingerprint(packet),
        "records_examined": len(records),
        "unique_review_packet_count": len(unique_representatives),
        "records_automatically_deduplicated": bucket_counts.get("duplicate", 0),
        "records_automatically_excluded": bucket_counts.get("exclusion", 0),
        "records_automatically_completed_objectively": bucket_counts.get("deterministic_resolution", 0),
        "records_reduced_to_bounded_human_decision": len(bounded),
        "records_requiring_full_source_research": bucket_counts.get("additional_fetch_needed", 0) + bucket_counts.get("canonical_source_lookup", 0) + bucket_counts.get("unrecoverable", 0),
        "blocker_counts": {
            "deterministic_resolution": bucket_counts.get("deterministic_resolution", 0),
            "additional_fetch_needed": bucket_counts.get("additional_fetch_needed", 0),
            "canonical_source_lookup": bucket_counts.get("canonical_source_lookup", 0),
            "human_evidence_judgment": bucket_counts.get("human_evidence_judgment", 0),
            "editorial_judgment": bucket_counts.get("editorial_judgment", 0),
            "exclusion": bucket_counts.get("exclusion", 0),
            "duplicate": bucket_counts.get("duplicate", 0),
            "unrecoverable": bucket_counts.get("unrecoverable", 0),
            "bounded_human_judgment": bucket_counts.get("bounded_human_judgment", 0),
            "recovered_private_candidate": bucket_counts.get("recovered_private_candidate", 0),
        },
        "recovery_feedback_counts": {
            "recovery_deterministic_exclusion": feedback_counts.get("recovery_deterministic_exclusion", 0),
            "recovery_unrecoverable": feedback_counts.get("recovery_unrecoverable", 0),
            "recovery_fetch_failed": feedback_counts.get("recovery_fetch_failed", 0),
            "recovery_still_unresolved": feedback_counts.get("recovery_still_unresolved", 0),
            "recovery_qualified_pending_apply": feedback_counts.get("recovery_qualified_pending_apply", 0),
            "recovery_resolved_private_candidate": feedback_counts.get("recovery_resolved_private_candidate", 0),
            "recovery_feedback_ignored_stale": feedback_counts.get("recovery_feedback_ignored_stale", 0),
        },
        "record_family_counts": dict(sorted(family_counts.items())),
        "source_evidence_deleted": False,
        "records_approved": 0,
        "records_published": 0,
        "queue_release_state_changed": False,
    }


def write_review_packet_from_current_state(
    repo_root: Path,
    *,
    packet_path: Path,
    report_path: Path,
    pre_review_records_path: Path | None = None,
    review_root: Path | None = None,
    recovery_root: Path | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    write_paths = [packet_path, report_path]
    if pre_review_records_path is not None:
        write_paths.append(pre_review_records_path)
    for path in write_paths:
        refuse_public_or_pages_path(path if path.is_absolute() else repo_root / path, repo_root)
    packet = build_review_packet_from_current_state(repo_root, review_root=review_root, recovery_root=recovery_root)
    report = review_packet_generation_report(packet)
    pre_review_records = build_pre_review_records_from_packet(packet)
    if not check_only:
        packet_target = packet_path if packet_path.is_absolute() else repo_root / packet_path
        report_target = report_path if report_path.is_absolute() else repo_root / report_path
        packet_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(packet_target, _stable_json(packet))
        _atomic_write(report_target, _stable_json(report))
        if pre_review_records_path is not None:
            pre_review_target = pre_review_records_path if pre_review_records_path.is_absolute() else repo_root / pre_review_records_path
            pre_review_target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(pre_review_target, _stable_json(pre_review_records))
    return {"packet": packet, "report": report, "pre_review_records": pre_review_records}


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
    parser = argparse.ArgumentParser(description="Prepare or import Care Line evidence-review packets.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--generate-review-packet", action="store_true", help="Build a deterministic review packet from current Care Line review artifacts.")
    parser.add_argument("--review-root", default=str(DEFAULT_REVIEW_ROOT))
    parser.add_argument("--review-packet", default="")
    parser.add_argument("--decisions-json", default="")
    parser.add_argument("--decisions-csv", default="")
    parser.add_argument("--reviewed-records", default="")
    parser.add_argument("--pre-review-records", default="")
    parser.add_argument("--decision-ledger", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check_only and args.apply:
            raise ValueError("--check-only and --apply are mutually exclusive")
        repo_root = Path(args.repo_root).resolve()

        def resolve_repo_path(value: str) -> Path:
            path = Path(value)
            return path if path.is_absolute() else repo_root / path

        if args.generate_review_packet:
            if not args.check_only and not args.apply:
                raise ValueError("one of --check-only or --apply is required")
            packet_path = Path(args.review_packet) if args.review_packet else DEFAULT_REVIEW_PACKET_OUTPUT
            report_path = Path(args.report) if args.report else DEFAULT_REVIEW_PACKET_REPORT
            pre_review_records_path = Path(args.pre_review_records) if args.pre_review_records else DEFAULT_PRE_REVIEW_RECORDS_OUTPUT
            result = write_review_packet_from_current_state(
                repo_root=repo_root,
                packet_path=packet_path,
                report_path=report_path,
                pre_review_records_path=pre_review_records_path,
                review_root=Path(args.review_root),
                check_only=args.check_only,
            )
            print(_stable_json(result["report"]))
            return 0

        required = {
            "--review-packet": args.review_packet,
            "--decisions-json": args.decisions_json,
            "--decisions-csv": args.decisions_csv,
            "--reviewed-records": args.reviewed_records,
            "--decision-ledger": args.decision_ledger,
            "--report": args.report,
        }
        missing = [flag for flag, value in required.items() if not value]
        if missing:
            raise ValueError("missing required import argument(s): " + ", ".join(missing))
        if not args.check_only and not args.apply:
            raise ValueError("one of --check-only or --apply is required")
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
