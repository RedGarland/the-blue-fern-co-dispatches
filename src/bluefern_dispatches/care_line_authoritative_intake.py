from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from bluefern_dispatches.care_line_record import CARE_LINE_EVENT_TYPES, SERVICE_EVENT_TYPES, SERVICE_LINES, stable_json_hash
from bluefern_dispatches.care_line_reviewed_export import refuse_public_or_pages_path
from bluefern_dispatches.care_line_source_recovery import deterministic_json, discovery_inventory, merge_manual_pack
from bluefern_dispatches.story_dedupe import normalize_url


INTAKE_SCHEMA_VERSION = "bluefern.care_line.authoritative_source_intake.v1"
INTAKE_BATCH_SCHEMA_VERSION = "bluefern.care_line.authoritative_source_intake_batch.v1"
INTAKE_TEMPLATE_SCHEMA_VERSION = "bluefern.care_line.authoritative_source_intake_template.v1"
VALIDATION_SCHEMA_VERSION = "bluefern.care_line.authoritative_source_validation.v1"
OPERATOR_VERSION = "care-line-authoritative-intake-phase10-v1"
PHASE11_SCHEMA_VERSION = "bluefern.care_line.phase11_research_batch.v1"

DISALLOWED_HOSTS = {
    "news.google.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "search.yahoo.com",
}
AGGREGATOR_HOSTS = {"news.yahoo.com"}
INTAKE_STATUSES = {"accepted", "rejected", "deferred", "duplicate", "superseded", "withdrawn", "invalid", "stale"}
REJECTION_REASONS = {
    "wrapper_url",
    "invalid_url",
    "missing_publisher",
    "missing_evidence",
    "unsupported_event_type",
    "missing_facility_or_provider",
    "missing_geography",
    "missing_date",
    "missing_service_line",
    "evidence_event_mismatch",
    "stale_discovery_fingerprint",
    "stale_source_fingerprint",
    "duplicate_source",
    "invalid_supersession",
    "reviewer_missing",
    "review_reason_missing",
    "invalid_review_status",
}
REVIEW_OUTCOME_ALIASES = {
    "approved": "approved",
    "deferred": "deferred",
    "rejected": "rejected",
    "non_operational": "non_operational",
    "non-operational": "non_operational",
}
PROVENANCE_VALUES = {
    "reviewer_supplied",
    "reviewer_corrected",
    "discovery_metadata",
    "structured_existing",
    "source_explicit",
    "unresolved",
}
EVIDENCE_PROVENANCE_VALUES = {
    "source_explicit",
    "reviewer_transcribed",
    "reviewer_paraphrased",
    "reviewer_analysis",
    "feed_description",
    "headline_only",
    "missing",
}
VALID_UNIVERSAL_EVENT_EVIDENCE = {"source_explicit", "reviewer_transcribed"}
NULLABLE_FIELDS = {
    "parent_organization",
    "operator_name",
    "former_owner",
    "new_owner",
    "facility_type",
    "address_line_1",
    "address_line_2",
    "county",
    "postal_code",
    "effective_date",
    "duplicate_of_record_id",
    "supersedes_intake_record_id",
    "review_notes",
    "effective_evidence_text",
    "evidence_provenance_type",
    "evidence_valid_for_universal_event",
    "recommended_status",
}
REQUIRED_FIELDS = [
    "schema_version",
    "intake_record_id",
    "discovery_record_id",
    "discovery_date",
    "reviewer",
    "review_reason",
    "reviewed_at",
    "canonical_source_url",
    "source_title",
    "publisher",
    "publication_date",
    "source_type",
    "source_role",
    "supporting_passage",
    "event_type",
    "service_line",
    "facility_name",
    "provider_name",
    "parent_organization",
    "operator_name",
    "former_owner",
    "new_owner",
    "facility_type",
    "address_line_1",
    "address_line_2",
    "city",
    "county",
    "state",
    "postal_code",
    "country_code",
    "announcement_date",
    "effective_date",
    "date_precision",
    "permanence",
    "evidence_level",
    "evidence_strength",
    "is_primary_source",
    "care_line_public_eligible",
    "universal_event_eligible",
    "duplicate_of_record_id",
    "supersedes_intake_record_id",
    "withdrawal_status",
    "review_notes",
    "effective_evidence_text",
    "evidence_provenance_type",
    "evidence_valid_for_universal_event",
    "recommended_status",
]
EDITABLE_FIELDS = [field for field in REQUIRED_FIELDS if field not in {"schema_version", "discovery_record_id", "discovery_date"}]
CSV_FIELDS = [
    "discovery_record_id",
    "discovery_date",
    "source_payload_fingerprint",
    "proposal_fingerprint",
    "headline",
    "snippet",
    "wrapper_url",
    "reported_publisher",
    "reported_publication_date",
    *EDITABLE_FIELDS,
    "override_reason",
]
RESEARCH_FIELDS = [
    "batch_id",
    "discovery_record_id",
    "headline",
    "snippet",
    "reported_publisher",
    "reported_publication_date",
    "wrapper_url",
    "potential_event_type",
    "potential_service_line",
    "potential_facility",
    "potential_geography",
    "selection_reason",
    "priority",
    "source_payload_fingerprint",
    "proposal_fingerprint",
    "canonical_source_url",
    "source_title",
    "publisher",
    "publication_date",
    "supporting_passage",
    "event_type",
    "service_line",
    "facility_name",
    "provider_name",
    "parent_organization",
    "operator_name",
    "former_owner",
    "new_owner",
    "facility_type",
    "address_line_1",
    "city",
    "county",
    "state",
    "postal_code",
    "country_code",
    "announcement_date",
    "effective_date",
    "date_precision",
    "permanence",
    "evidence_level",
    "evidence_strength",
    "is_primary_source",
    "care_line_public_eligible",
    "universal_event_eligible",
    "reviewer",
    "review_reason",
    "review_notes",
]


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_hash(parts)[:16]}"


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _bool(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def normalize_review_status(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    prefix = text.split(":", 1)[0].strip().casefold().replace(" ", "_")
    normalized = REVIEW_OUTCOME_ALIASES.get(prefix)
    if normalized:
        return normalized
    if ":" in text:
        return "unknown"
    return ""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write_path = _long_path(tmp)
    target_path = _long_path(path)
    write_path.write_text(content, encoding="utf-8")
    write_path.replace(target_path)


def _long_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\") or len(resolved) < 240:
        return path
    return Path("\\\\?\\" + resolved)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def canonical_hostname(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().removeprefix("www.")


def normalize_source_url(url: str) -> str:
    return normalize_url(url) or url.strip()


def is_disallowed_url(url: str, *, override_reason: str = "") -> tuple[bool, str]:
    if not url.strip():
        return True, "invalid_url"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True, "invalid_url"
    host = canonical_hostname(url)
    if host in DISALLOWED_HOSTS or host.endswith(".google.com") or host.endswith(".bing.com"):
        return True, "wrapper_url"
    if host in AGGREGATOR_HOSTS and not override_reason.strip():
        return True, "wrapper_url"
    if "/search" in parsed.path.lower() and host in {"yahoo.com", "duckduckgo.com"}:
        return True, "wrapper_url"
    return False, ""


def _proposal_by_id(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("discovery_record_id")): dict(row) for row in inventory.get("proposals") or []}


def template_from_inventory(inventory: Mapping[str, Any], *, sample_id: str, max_records: int = 100) -> dict[str, Any]:
    rows = []
    for proposal in list(inventory.get("proposals") or [])[:max_records]:
        rows.append(
            {
                "read_only": {
                    "discovery_record_id": proposal.get("discovery_record_id", ""),
                    "discovery_date": proposal.get("discovery_date", ""),
                    "headline": proposal.get("headline", ""),
                    "snippet": proposal.get("snippet", ""),
                    "wrapper_url": proposal.get("wrapper_url", ""),
                    "reported_publisher": proposal.get("reported_publisher", ""),
                    "reported_publication_date": proposal.get("reported_publication_date", ""),
                    "potential_event_type": proposal.get("proposed_event_type", ""),
                    "potential_service_line": proposal.get("proposed_service_line", ""),
                    "potential_facility": proposal.get("proposed_facility", ""),
                    "potential_geography": proposal.get("proposed_geography", ""),
                    "source_payload_fingerprint": proposal.get("source_payload_fingerprint", ""),
                    "proposal_fingerprint": proposal.get("proposal_fingerprint", ""),
                },
                "reviewer_editable": {
                    **{field: "" for field in EDITABLE_FIELDS},
                    "schema_version": INTAKE_SCHEMA_VERSION,
                    "intake_record_id": _stable_id("care-line-intake", proposal.get("discovery_record_id"), sample_id),
                    "reviewed_at": "",
                    "source_type": "reviewer_supplied_authoritative_source",
                    "source_role": "clinic_operations_signal",
                    "date_precision": "day",
                    "country_code": "US",
                    "evidence_level": "publisher_source",
                    "evidence_strength": "reviewed",
                    "is_primary_source": False,
                    "care_line_public_eligible": False,
                    "universal_event_eligible": False,
                    "withdrawal_status": "",
                    "override_reason": "",
                    "expected_source_payload_fingerprint": proposal.get("source_payload_fingerprint", ""),
                    "expected_proposal_fingerprint": proposal.get("proposal_fingerprint", ""),
                },
            }
        )
    return {
        "schema_version": INTAKE_TEMPLATE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "operator_version": OPERATOR_VERSION,
        "record_count": len(rows),
        "rows": rows,
    }


def render_guide(template: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Care Line Phase 10 Authoritative Source Intake Guide",
            "",
            "Discovery rows are context only. Do not use wrapper URLs or snippets as evidence.",
            "",
            "Required reviewer steps:",
            "",
            "- Supply a canonical publisher or primary-source URL.",
            "- Supply a passage from that source that directly supports the operational healthcare-access change.",
            "- Fill reviewer, review reason, publication date, event type, geography, and facility/provider fields.",
            "- Leave a row blank or mark it deferred/rejected if evidence is not available.",
            "",
            f"- Template sample: `{template.get('sample_id')}`",
            f"- Rows: `{template.get('record_count')}`",
            "",
        ]
    ) + "\n"


def write_templates(inventory: Mapping[str, Any], *, sample_id: str, output_dir: Path, max_records: int = 100) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    template = template_from_inventory(inventory, sample_id=sample_id, max_records=max_records)
    json_path = output_dir / f"{sample_id}.authoritative-source-intake-template.json"
    csv_path = output_dir / f"{sample_id}.authoritative-source-intake-template.csv"
    guide_path = output_dir / f"{sample_id}.authoritative-source-intake-guide.md"
    _write_atomic(json_path, deterministic_json(template) + "\n")
    _write_atomic(guide_path, render_guide(template))
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in template["rows"]:
            flat = {**row["read_only"], **row["reviewer_editable"]}
            writer.writerow({field: flat.get(field, "") for field in CSV_FIELDS})
    return {"json_template": str(json_path), "csv_template": str(csv_path), "guide": str(guide_path)}


def _research_priority(proposal: Mapping[str, Any]) -> tuple[int, str]:
    event_type = _text(proposal, "proposed_event_type")
    service_line = _text(proposal, "proposed_service_line")
    status = _text(proposal, "recovery_status")
    current = _text(proposal, "reported_publication_date") >= "2026-06-01"
    if event_type in {"facility_closure", "planned_facility_closure", "service_closure", "service_suspension", "service_reduction", "facility_reopening"}:
        return 1, "potential concrete operational event type"
    if service_line:
        return 2, "potential service-line impact"
    if current and status != "stale":
        return 3, "current wrapper lead requiring authoritative source review"
    return 4, "lower-priority stale or weak wrapper lead"


def select_research_batch(inventory: Mapping[str, Any], *, batch_id: str, min_records: int = 25, max_records: int = 40) -> dict[str, Any]:
    if min_records < 1 or max_records < min_records:
        raise ValueError("invalid research batch bounds")
    ranked = []
    for index, proposal in enumerate(inventory.get("proposals") or []):
        priority, reason = _research_priority(proposal)
        ranked.append((priority, str(proposal.get("discovery_record_id") or ""), index, reason, proposal))
    selected = []
    for priority, _record_id, _index, reason, proposal in sorted(ranked)[:max_records]:
        selected.append(
            {
                "batch_id": batch_id,
                "discovery_record_id": proposal.get("discovery_record_id", ""),
                "headline": proposal.get("headline", ""),
                "snippet": proposal.get("snippet", ""),
                "reported_publisher": proposal.get("reported_publisher", ""),
                "reported_publication_date": proposal.get("reported_publication_date", ""),
                "wrapper_url": proposal.get("wrapper_url", ""),
                "potential_event_type": proposal.get("proposed_event_type", ""),
                "potential_service_line": proposal.get("proposed_service_line", ""),
                "potential_facility": proposal.get("proposed_facility", ""),
                "potential_geography": proposal.get("proposed_geography", ""),
                "selection_reason": reason,
                "priority": priority,
                "source_payload_fingerprint": proposal.get("source_payload_fingerprint", ""),
                "proposal_fingerprint": proposal.get("proposal_fingerprint", ""),
            }
        )
    if len(selected) < min_records:
        raise ValueError(f"not enough discovery leads for research batch: {len(selected)}")
    return {
        "schema_version": PHASE11_SCHEMA_VERSION,
        "batch_id": batch_id,
        "record_count": len(selected),
        "selection_policy": "prioritize locally flagged concrete operational leads; wrapper-only rows still require human source research",
        "records": selected,
    }


def research_workbook_from_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for row in batch.get("records") or []:
        rows.append(
            {
                **dict(row),
                "canonical_source_url": "",
                "source_title": "",
                "publisher": "",
                "publication_date": "",
                "supporting_passage": "",
                "event_type": row.get("potential_event_type") or "",
                "service_line": row.get("potential_service_line") or "",
                "facility_name": row.get("potential_facility") or "",
                "provider_name": row.get("potential_facility") or "",
                "parent_organization": "",
                "operator_name": "",
                "former_owner": "",
                "new_owner": "",
                "facility_type": "",
                "address_line_1": "",
                "city": "",
                "county": "",
                "state": "",
                "postal_code": "",
                "country_code": "US",
                "announcement_date": "",
                "effective_date": "",
                "date_precision": "day",
                "permanence": "",
                "evidence_level": "publisher_source",
                "evidence_strength": "reviewed",
                "is_primary_source": False,
                "care_line_public_eligible": False,
                "universal_event_eligible": False,
                "reviewer": "",
                "review_reason": "",
                "review_notes": "",
            }
        )
    return {
        "schema_version": PHASE11_SCHEMA_VERSION,
        "batch_id": batch.get("batch_id"),
        "record_count": len(rows),
        "human_review_status": "required",
        "records": rows,
    }


def workbook_completion_status(workbook: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(workbook.get("records") or [])
    completed = [
        row
        for row in rows
        if _text(row, "canonical_source_url")
        and _text(row, "supporting_passage")
        and _text(row, "publisher")
        and _text(row, "reviewer")
        and _text(row, "review_reason")
    ]
    return {
        "schema_version": PHASE11_SCHEMA_VERSION,
        "batch_id": workbook.get("batch_id", ""),
        "record_count": len(rows),
        "completed_record_count": len(completed),
        "decision": "HUMAN SOURCE REVIEW REQUIRED" if not completed else "READY_FOR_VALIDATION",
    }


def write_research_packet(batch: Mapping[str, Any], *, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_id = str(batch.get("batch_id") or "care-line-phase11-review-batch")
    workbook = research_workbook_from_batch(batch)
    report_path = output_dir / f"{batch_id}.batch-selection-report.json"
    workbook_json = output_dir / f"{batch_id}.research-workbook.json"
    workbook_csv = output_dir / f"{batch_id}.research-workbook.csv"
    guide = output_dir / f"{batch_id}.research-guide.md"
    _write_atomic(report_path, deterministic_json(batch) + "\n")
    _write_atomic(workbook_json, deterministic_json(workbook) + "\n")
    with workbook_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESEARCH_FIELDS)
        writer.writeheader()
        for row in workbook["records"]:
            writer.writerow({field: row.get(field, "") for field in RESEARCH_FIELDS})
    _write_atomic(guide, render_research_guide(workbook))
    return {"batch_selection_report": str(report_path), "research_workbook_json": str(workbook_json), "research_workbook_csv": str(workbook_csv), "research_guide": str(guide)}


def render_research_guide(workbook: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Care Line Phase 11 Research Guide",
            "",
            f"- Batch: `{workbook.get('batch_id')}`",
            f"- Records: `{workbook.get('record_count')}`",
            "",
            "Complete only rows where you can verify an authoritative publisher or primary-source URL and a supporting passage.",
            "",
            "Required for operational events: canonical URL, title, publisher, publication date, passage, event type, service line where required, facility/provider, geography, dates, reviewer, and review reason.",
            "",
            "Do not use Google News wrappers, search results, headlines alone, snippets alone, or inferred evidence.",
            "",
        ]
    ) + "\n"


def load_intake(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
        return {
            "schema_version": INTAKE_BATCH_SCHEMA_VERSION,
            "batch_id": _stable_id("care-line-intake-batch", path.name, rows),
            "reviewer": "",
            "date_from": "",
            "date_to": "",
            "records": rows,
        }
    payload = _load_json(path)
    if isinstance(payload, list):
        return {"schema_version": INTAKE_BATCH_SCHEMA_VERSION, "batch_id": _stable_id("care-line-intake-batch", payload), "records": payload}
    if isinstance(payload, dict) and "records" in payload:
        return dict(payload)
    if isinstance(payload, dict) and "rows" in payload:
        records = []
        for row in payload.get("rows") or []:
            records.append({**(row.get("read_only") or {}), **(row.get("reviewer_editable") or {})})
        return {"schema_version": INTAKE_BATCH_SCHEMA_VERSION, "batch_id": _stable_id("care-line-intake-batch", records), "records": records}
    raise ValueError("unsupported authoritative intake input shape")


def source_fingerprint(row: Mapping[str, Any]) -> str:
    return stable_json_hash(
        {
            "canonical_source_url": normalize_source_url(_text(row, "canonical_source_url", "source_url", "url")),
            "source_title": _text(row, "source_title", "title"),
            "publisher": _text(row, "publisher"),
            "publication_date": _text(row, "publication_date", "published_at"),
            "supporting_passage": _text(row, "supporting_passage", "evidence_text"),
            "event_type": _text(row, "event_type"),
            "service_line": _text(row, "service_line"),
            "facility_name": _text(row, "facility_name"),
            "provider_name": _text(row, "provider_name"),
            "city": _text(row, "city"),
            "state": _text(row, "state"),
        }
    )


def validate_intake_record(row: Mapping[str, Any], proposal: Mapping[str, Any] | None, existing_ids: set[str], existing_urls: set[str]) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    reviewer = _text(row, "reviewer")
    reason = _text(row, "review_reason")
    review_status = normalize_review_status(reason)
    if not reviewer:
        reasons.append("reviewer_missing")
    if not reason:
        reasons.append("review_reason_missing")
    if review_status == "unknown":
        reasons.append("invalid_review_status")
    if row.get("schema_version") and row.get("schema_version") != INTAKE_SCHEMA_VERSION:
        reasons.append("invalid_supersession")
    if proposal is None:
        reasons.append("stale_discovery_fingerprint")
    else:
        expected_source = _text(row, "expected_source_payload_fingerprint", "source_payload_fingerprint")
        expected_proposal = _text(row, "expected_proposal_fingerprint", "proposal_fingerprint")
        if expected_source and expected_source != proposal.get("source_payload_fingerprint"):
            reasons.append("stale_discovery_fingerprint")
        if expected_proposal and expected_proposal != proposal.get("proposal_fingerprint"):
            reasons.append("stale_source_fingerprint")
    url = _text(row, "canonical_source_url", "source_url", "url")
    normalized = normalize_source_url(url)
    disallowed, url_reason = is_disallowed_url(url, override_reason=_text(row, "override_reason"))
    if disallowed:
        reasons.append(url_reason)
    record_id = stable_producer_id(row, proposal)
    if normalized in existing_urls and record_id not in existing_ids and not _text(row, "supersedes_intake_record_id"):
        reasons.append("duplicate_source")
    publisher = _text(row, "publisher")
    if not publisher or publisher.casefold() == "google news":
        reasons.append("missing_publisher")
    evidence = _text(row, "supporting_passage")
    evidence_provenance = _text(row, "evidence_provenance_type") or ("source_explicit" if evidence and (_text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"} or _text(row, "source_type") in {"publisher_article", "official_notice", "official_data"}) else "missing")
    transcribed = _text(row, "reviewer_transcribed_evidence")
    event_type = _text(row, "event_type")
    non_operational = event_type in {"financial_context", "resource_context", "workforce_context", "policy_context", "context_only"}
    service_line = _text(row, "service_line")
    explicit_status = review_status if review_status in {"approved", "deferred", "rejected", "non_operational"} else ""
    if explicit_status in {"", "approved"}:
        if not evidence and evidence_provenance == "reviewer_transcribed":
            evidence = transcribed
        if evidence_provenance not in EVIDENCE_PROVENANCE_VALUES or not evidence:
            reasons.append("missing_evidence")
        if evidence_provenance not in VALID_UNIVERSAL_EVENT_EVIDENCE:
            reasons.append("missing_evidence")
        if "<" in evidence and ">" in evidence:
            reasons.append("missing_evidence")
        if proposal is not None and evidence:
            discovery_texts = {
                _text(proposal, "headline").casefold(),
                _text(proposal, "snippet").casefold(),
                _text(proposal, "reported_publisher").casefold(),
            }
            if evidence.casefold() in {item for item in discovery_texts if item}:
                reasons.append("missing_evidence")
                warnings.append("discovery_text_is_not_authoritative_evidence")
        if evidence and len(evidence) < 20:
            warnings.append("short_supporting_passage")
        if evidence and len(evidence) > 2500:
            warnings.append("long_supporting_passage")
        if event_type and event_type not in CARE_LINE_EVENT_TYPES and not non_operational:
            reasons.append("unsupported_event_type")
        if not event_type:
            reasons.append("unsupported_event_type")
        if not non_operational:
            if not (_text(row, "facility_name") or _text(row, "provider_name")):
                reasons.append("missing_facility_or_provider")
            if not _text(row, "state") or not (_text(row, "city") or _text(row, "county") or _text(row, "address_line_1")):
                reasons.append("missing_geography")
            if not (_text(row, "announcement_date") or _text(row, "effective_date")):
                reasons.append("missing_date")
            if event_type in SERVICE_EVENT_TYPES and _text(row, "service_line") in {"", "unknown"}:
                reasons.append("missing_service_line")
            if event_type in {"facility_closure", "planned_facility_closure", "temporary_facility_suspension"} and not _text(row, "permanence"):
                reasons.append("missing_date")
        if service_line and service_line not in SERVICE_LINES:
            reasons.append("missing_service_line")
    else:
        if explicit_status == "non_operational" and not review_status:
            reasons.append("invalid_review_status")
        if explicit_status and not review_status:
            reasons.append("invalid_review_status")
    if explicit_status not in {"deferred", "rejected", "non_operational"} and service_line and service_line not in SERVICE_LINES:
        reasons.append("missing_service_line")
    status = "accepted"
    withdrawal = _text(row, "withdrawal_status")
    if withdrawal in {"withdrawn", "withdraw"}:
        status = "withdrawn"
    elif _text(row, "duplicate_of_record_id"):
        status = "duplicate"
    elif _text(row, "supersedes_intake_record_id"):
        status = "superseded"
    elif explicit_status in {"approved", "deferred", "rejected", "non_operational"} and not reasons:
        status = explicit_status
    elif explicit_status in {"approved", "deferred", "rejected", "non_operational"}:
        status = "invalid" if any(item in reasons for item in {"invalid_url", "stale_discovery_fingerprint", "stale_source_fingerprint", "invalid_review_status"}) else "rejected"
    elif reasons:
        status = "invalid" if any(item in reasons for item in {"invalid_url", "stale_discovery_fingerprint", "stale_source_fingerprint", "invalid_review_status"}) else "rejected"
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "discovery_record_id": _text(row, "discovery_record_id"),
        "intake_record_id": _text(row, "intake_record_id"),
        "producer_record_id": record_id,
        "canonical_url": normalized,
        "canonical_hostname": canonical_hostname(url),
        "publisher": publisher,
        "normalized_publisher": " ".join(publisher.split()),
        "event_type": event_type,
        "facility_provider": _text(row, "facility_name") or _text(row, "provider_name"),
        "location": ", ".join(part for part in (_text(row, "city"), _text(row, "state")) if part),
        "evidence_status": "present" if evidence and evidence_provenance in VALID_UNIVERSAL_EVENT_EVIDENCE and "missing_evidence" not in reasons else "missing",
        "effective_evidence_text": evidence if evidence_provenance in VALID_UNIVERSAL_EVENT_EVIDENCE else "",
        "evidence_provenance_type": evidence_provenance,
        "evidence_valid_for_universal_event": bool(evidence and evidence_provenance in VALID_UNIVERSAL_EVENT_EVIDENCE and "missing_evidence" not in reasons),
        "recommended_status": "universal_event_ready" if evidence and evidence_provenance in VALID_UNIVERSAL_EVENT_EVIDENCE and "missing_evidence" not in reasons else "needs_evidence_review",
        "url_status": "accepted" if not disallowed else "rejected",
        "taxonomy_status": "accepted" if "unsupported_event_type" not in reasons and "missing_service_line" not in reasons else "rejected",
        "validation_profile": validation_profile(event_type),
        "final_decision": status,
        "rejection_reasons": sorted(set(reasons), key=reasons.index),
        "warnings": warnings,
        "source_fingerprint": source_fingerprint(row),
    }


def validation_profile(event_type: str) -> str:
    if event_type in SERVICE_EVENT_TYPES:
        return "service_event"
    if event_type in {"facility_closure", "planned_facility_closure", "temporary_facility_suspension", "facility_reopening", "facility_relocation", "facility_conversion"}:
        return "facility_event"
    if event_type in {"ownership_change", "operator_change"}:
        return "ownership_event"
    if event_type in {"capacity_reduction", "bankruptcy_service_impact"}:
        return "statewide_operational_event"
    return "non_operational_or_unknown"


def stable_producer_id(row: Mapping[str, Any], proposal: Mapping[str, Any] | None) -> str:
    return (
        _text(row, "producer_record_id")
        or _text(row, "source_record_id")
        or (str(proposal.get("discovery_record_id")) if proposal else "")
        or _stable_id("care-line-authoritative", normalize_source_url(_text(row, "canonical_source_url")), _text(row, "publication_date"))
    )


def reviewed_source_from_intake(row: Mapping[str, Any], proposal: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    producer_id = str(validation["producer_record_id"])
    accepted = validation.get("final_decision") in {"accepted", "approved"}
    withdrawn = validation.get("final_decision") == "withdrawn"
    duplicate = validation.get("final_decision") == "duplicate"
    superseded = validation.get("final_decision") == "superseded"
    source_url = str(validation["canonical_url"])
    field_provenance = {
        field: {
            "value": row.get(field, ""),
            "provenance_type": "reviewer_supplied" if field not in {"discovery_record_id", "discovery_date"} else "discovery_metadata",
            "reviewer": _text(row, "reviewer"),
            "reason": _text(row, "review_reason"),
            "prior_value": proposal.get(field, ""),
        }
        for field in REQUIRED_FIELDS
    }
    return {
        "schema_version": "bluefern.care_line.reviewed_source.v1",
        "producer": "Care Line",
        "source_record_id": producer_id,
        "producer_record_id": producer_id,
        "intake_record_id": _text(row, "intake_record_id"),
        "review_status": "reviewed" if accepted else "rejected",
        "care_line_review_status": "reviewed" if accepted else "rejected",
        "record_status": "superseded" if superseded else "duplicate" if duplicate else "withdrawn" if withdrawn else "active",
        "public_status": "public_approved" if accepted and _bool(row.get("care_line_public_eligible")) else "not_public",
        "source_url": source_url,
        "canonical_url": source_url,
        "url": source_url,
        "source_title": _text(row, "source_title"),
        "title": _text(row, "source_title"),
        "publisher": _text(row, "publisher"),
        "published_at": _text(row, "publication_date"),
        "publication_date": _text(row, "publication_date"),
        "source_type": _text(row, "source_type") or "reviewer_supplied_authoritative_source",
        "source_family": _text(row, "source_type") or "reviewer_supplied_authoritative_source",
        "source_role": _text(row, "source_role") or "clinic_operations_signal",
        "supporting_passage": _text(row, "supporting_passage"),
        "evidence_text": _text(row, "supporting_passage"),
        "claim_supported": _text(row, "supporting_passage"),
        "effective_evidence_text": validation.get("effective_evidence_text", ""),
        "evidence_provenance_type": validation.get("evidence_provenance_type", "missing"),
        "evidence_valid_for_universal_event": bool(validation.get("evidence_valid_for_universal_event")),
        "recommended_status": validation.get("recommended_status", "needs_evidence_review"),
        "review_notes": _text(row, "review_notes"),
        "event_type": _text(row, "event_type"),
        "universal_event_type": _text(row, "event_type"),
        "service_line": _text(row, "service_line"),
        "facility_name": _text(row, "facility_name"),
        "provider_name": _text(row, "provider_name"),
        "parent_organization": _text(row, "parent_organization"),
        "operator_name": _text(row, "operator_name"),
        "operator": _text(row, "operator_name"),
        "former_owner": _text(row, "former_owner"),
        "new_owner": _text(row, "new_owner"),
        "facility_type": _text(row, "facility_type"),
        "address_line_1": _text(row, "address_line_1"),
        "address_line_2": _text(row, "address_line_2"),
        "city": _text(row, "city"),
        "county": _text(row, "county"),
        "state": _text(row, "state"),
        "postal_code": _text(row, "postal_code"),
        "country_code": _text(row, "country_code") or "US",
        "location_name": ", ".join(part for part in (_text(row, "city"), _text(row, "state")) if part),
        "announcement_date": _text(row, "announcement_date"),
        "effective_date": _text(row, "effective_date"),
        "date_precision": _text(row, "date_precision") or "day",
        "permanence": _text(row, "permanence"),
        "evidence_level": _text(row, "evidence_level") or "publisher_source",
        "evidence_strength": _text(row, "evidence_strength") or "reviewed",
        "confidence": _text(row, "evidence_strength") or "reviewed",
        "is_primary_source": _bool(row.get("is_primary_source")),
        "included": accepted and _bool(row.get("care_line_public_eligible")),
        "excluded": not accepted,
        "qualifies_for_public_inclusion": accepted and _bool(row.get("care_line_public_eligible")),
        "source_public_story_eligible": accepted and _bool(row.get("care_line_public_eligible")),
        "pressure_signal": accepted and _bool(row.get("universal_event_eligible")),
        "context_only": validation.get("validation_profile") == "non_operational_or_unknown",
        "duplicate_of_record_id": _text(row, "duplicate_of_record_id"),
        "supersedes_record_id": _text(row, "supersedes_intake_record_id"),
        "is_withdrawn": withdrawn,
        "withdrawn": withdrawn,
        "reviewer": _text(row, "reviewer"),
        "review_reason": _text(row, "review_reason"),
        "reviewed_at": _text(row, "reviewed_at"),
        "discovery_provenance": {
            "discovery_record_id": proposal.get("discovery_record_id"),
            "discovery_date": proposal.get("discovery_date"),
            "discovery_file": proposal.get("discovery_file"),
            "wrapper_url": proposal.get("wrapper_url"),
            "headline": proposal.get("headline"),
            "snippet": proposal.get("snippet"),
            "reported_publisher": proposal.get("reported_publisher"),
            "reported_publication_date": proposal.get("reported_publication_date"),
            "source_payload_fingerprint": proposal.get("source_payload_fingerprint"),
            "proposal_fingerprint": proposal.get("proposal_fingerprint"),
        },
        "authoritative_intake": {
            "schema_version": INTAKE_SCHEMA_VERSION,
            "intake_record_id": _text(row, "intake_record_id"),
            "source_fingerprint": validation.get("source_fingerprint"),
            "canonical_hostname": validation.get("canonical_hostname"),
            "override_reason": _text(row, "override_reason"),
            "validation_warnings": validation.get("warnings") or [],
        },
        "field_provenance": field_provenance,
    }


def _existing(source_root: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    urls: set[str] = set()
    for path in sorted(source_root.glob("*/manual_sources.json")):
        payload = _load_json(path)
        rows = payload if isinstance(payload, list) else payload.get("records") or payload.get("sources") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _text(row, "source_record_id", "producer_record_id"):
                ids.add(_text(row, "source_record_id", "producer_record_id"))
            if _text(row, "source_url", "canonical_url", "url"):
                urls.add(normalize_source_url(_text(row, "source_url", "canonical_url", "url")))
    return ids, urls


def validate_batch(inventory: Mapping[str, Any], intake: Mapping[str, Any], *, source_root: Path, max_records: int | None = None) -> dict[str, Any]:
    proposals = _proposal_by_id(inventory)
    existing_ids, existing_urls = _existing(source_root)
    rows = list(intake.get("records") or [])
    if max_records:
        rows = rows[:max_records]
    results = []
    for row in rows:
        proposal = proposals.get(_text(row, "discovery_record_id"))
        results.append(validate_intake_record(row, proposal, existing_ids, existing_urls))
    counts = Counter(row["final_decision"] for row in results)
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "operator_version": OPERATOR_VERSION,
        "batch_id": intake.get("batch_id") or _stable_id("care-line-intake-batch", rows),
        "record_count": len(results),
        "status_counts": dict(sorted(counts.items())),
        "results": results,
        "input_hash": stable_json_hash(rows),
    }


def import_intake(
    inventory: Mapping[str, Any],
    intake: Mapping[str, Any],
    *,
    source_root: Path,
    repo_root: Path,
    report: Path | None = None,
    apply: bool = False,
    check_only: bool = False,
    strict: bool = False,
    allow_partial: bool = False,
    reviewer: str = "",
    max_records: int | None = None,
) -> dict[str, Any]:
    source_root_abs = source_root if source_root.is_absolute() else repo_root / source_root
    if report:
        refuse_public_or_pages_path(report if report.is_absolute() else repo_root / report, repo_root)
    refuse_public_or_pages_path(source_root_abs, repo_root)
    if apply and check_only:
        raise ValueError("--apply and --check-only cannot both be used")
    if not apply and not check_only:
        raise ValueError("--apply is required for writes; use --check-only for validation")
    if reviewer:
        for row in intake.get("records") or []:
            row.setdefault("reviewer", reviewer)
    if strict and not reviewer and not _text(intake, "reviewer"):
        raise ValueError("reviewer identity is required")
    validation = validate_batch(inventory, intake, source_root=source_root_abs, max_records=max_records)
    proposals = _proposal_by_id(inventory)
    rows = list(intake.get("records") or [])
    if max_records:
        rows = rows[:max_records]
    by_validation = {row["intake_record_id"]: row for row in validation["results"]}
    fatal_reasons = {"invalid_url", "wrapper_url", "stale_discovery_fingerprint", "stale_source_fingerprint", "invalid_review_status", "reviewer_missing", "review_reason_missing"}
    invalid = [row for row in validation["results"] if any(reason in fatal_reasons for reason in row.get("rejection_reasons") or [])]
    accepted, rejected, deferred = [], [], []
    writes: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result = by_validation.get(_text(row, "intake_record_id")) or {}
        decision = result.get("final_decision")
        if decision in {"accepted", "approved"}:
            proposal = proposals[_text(row, "discovery_record_id")]
            record = reviewed_source_from_intake(row, proposal, result)
            date_key = _text(row, "source_pack_date", "discovery_date") or proposal.get("discovery_date")
            writes.setdefault(str(date_key), []).append(record)
            accepted.append({"intake_record_id": _text(row, "intake_record_id"), "producer_record_id": record["producer_record_id"], "source_url": record["source_url"]})
        elif decision == "deferred":
            deferred.append(result)
        else:
            rejected.append(result)
    manifests = []
    write_allowed = apply and not check_only and (allow_partial or not invalid)
    if write_allowed:
        for date_key, records in sorted(writes.items()):
            manifests.append(merge_manual_pack(source_root_abs / date_key / "manual_sources.json", records))
    result = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "operator_version": OPERATOR_VERSION,
        "batch_id": validation["batch_id"],
        "check_only": check_only,
        "apply": apply,
        "allow_partial": allow_partial,
        "validation": validation,
        "accepted": accepted if write_allowed or check_only or allow_partial else [],
        "rejected": rejected,
        "deferred": deferred,
        "write_manifests": manifests,
        "all_or_nothing_blocked": bool(invalid and not allow_partial),
    }
    if report:
        report_path = report if report.is_absolute() else repo_root / report
        write_validation_outputs(report_path, result)
    return result


def write_validation_outputs(report_path: Path, result: Mapping[str, Any]) -> None:
    batch_id = str(result.get("batch_id") or "care-line-intake")
    stem = report_path.with_suffix("")
    _write_atomic(report_path, deterministic_json(result) + "\n")
    rows = list((result.get("validation") or {}).get("results") or [])
    groups = {
        "accepted": [row for row in rows if row.get("final_decision") in {"accepted", "approved"}],
        "rejected": [row for row in rows if row.get("final_decision") in {"rejected", "invalid"}],
        "deferred": [row for row in rows if row.get("final_decision") == "deferred"],
        "non_operational": [row for row in rows if row.get("final_decision") == "non_operational"],
    }
    for name, payload in groups.items():
        _write_atomic(stem.with_name(f"{batch_id}.{name}.json"), deterministic_json({"schema_version": VALIDATION_SCHEMA_VERSION, "records": payload}) + "\n")
    _write_atomic(stem.with_name(f"{batch_id}.validation.md"), render_validation_markdown(result))


def render_validation_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Care Line Authoritative Intake Validation",
        "",
        f"- Batch: `{result.get('batch_id')}`",
        f"- Apply: `{result.get('apply')}`",
        f"- Check only: `{result.get('check_only')}`",
        f"- Status counts: `{json.dumps((result.get('validation') or {}).get('status_counts') or {}, sort_keys=True)}`",
        "",
        "| discovery_id | URL | publisher | event_type | decision | reasons |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in (result.get("validation") or {}).get("results") or []:
        lines.append(
            f"| {row.get('discovery_record_id')} | {row.get('canonical_url')} | {row.get('publisher')} | {row.get('event_type')} | {row.get('final_decision')} | {', '.join(row.get('rejection_reasons') or [])} |"
        )
    return "\n".join(lines) + "\n"


def difference_from_phase9(intake_result: Mapping[str, Any], canonical_export: Mapping[str, Any] | None = None, shadow_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    validation = intake_result.get("validation") or {}
    counts = (shadow_result or {}).get("counts") or {}
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "reviewer_intake_rows": validation.get("record_count", 0),
        "accepted_authoritative_sources": len(intake_result.get("accepted") or []),
        "rejected_sources": len(intake_result.get("rejected") or []),
        "deferred_sources": len(intake_result.get("deferred") or []),
        "new_reviewed_source_packs": len(intake_result.get("write_manifests") or []),
        "new_canonical_reviewed_records": (canonical_export or {}).get("reviewed_record_count", 0),
        "new_ue_ready_records": (canonical_export or {}).get("universal_event_ready_count", 0),
        "new_candidates": counts.get("candidate_count", 0),
        "new_mentions": counts.get("mention_count", 0),
        "new_entities": counts.get("canonical_entity_count", 0),
        "new_match_candidates": counts.get("match_candidate_count", 0),
        "new_effective_decisions": counts.get("effective_reviewed_mentions", 0),
        "new_promotion_eligible_candidates": ((shadow_result or {}).get("promotion_readiness_preview") or {}).get("metrics", {}).get("promotion_eligible_candidates", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and import reviewer-supplied Care Line authoritative source intake.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--discovery-inventory", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--source-root", default="data/dispatches/care-line/sources")
    parser.add_argument("--report", default="")
    parser.add_argument("--template-dir", default="")
    parser.add_argument("--research-packet-dir", default="")
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        if args.discovery_inventory:
            inventory = _load_json(Path(args.discovery_inventory))
        else:
            date_from = args.date or args.date_from or None
            date_to = args.date or args.date_to or None
            inventory = discovery_inventory(repo_root, date_from=date_from, date_to=date_to, max_records=args.max_records)
        if args.research_packet_dir:
            batch_id = args.batch_id or f"care_line_phase11_{args.date_from or args.date or 'all'}_{args.date_to or args.date or 'all'}_{_hash([inventory.get('lead_count'), inventory.get('status_counts')])[:12]}"
            batch = select_research_batch(inventory, batch_id=batch_id, max_records=args.max_records)
            result = {"schema_version": PHASE11_SCHEMA_VERSION, "batch_id": batch_id, "paths": write_research_packet(batch, output_dir=Path(args.research_packet_dir)), "completion": workbook_completion_status(research_workbook_from_batch(batch))}
        elif args.template_dir:
            sample_id = args.sample_id or f"care_line_phase10_{args.date_from or args.date or 'all'}_{args.date_to or args.date or 'all'}_{_hash([inventory.get('lead_count'), inventory.get('status_counts')])[:12]}"
            result = {"schema_version": INTAKE_TEMPLATE_SCHEMA_VERSION, "sample_id": sample_id, "paths": write_templates(inventory, sample_id=sample_id, output_dir=Path(args.template_dir), max_records=args.max_records)}
        else:
            if not args.input:
                raise ValueError("explicit --input is required unless --template-dir is used")
            intake = load_intake(Path(args.input))
            result = import_intake(
                inventory,
                intake,
                source_root=Path(args.source_root),
                repo_root=repo_root,
                report=Path(args.report) if args.report else None,
                apply=args.apply,
                check_only=args.check_only,
                strict=args.strict,
                allow_partial=args.allow_partial,
                reviewer=args.reviewer,
                max_records=args.max_records,
            )
        print(deterministic_json(result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
