from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_record import (
    CARE_LINE_EVENT_TYPES,
    SCHEMA_VERSION,
    SERVICE_LINES,
    CareLineReviewedRecord,
    FieldProvenance,
    deterministic_records_json,
    stable_json_hash,
    utc_now_text,
)


NORMALIZER_VERSION = "care-line-historical-normalizer-v1"
REVIEW_PACKAGE_SCHEMA_VERSION = "bluefern.care_line.normalization_review.v1"
DECISIONS_SCHEMA_VERSION = "bluefern.care_line.normalization_decisions.v1"


PRESSURE_EVENT_MAP = {
    "clinic_access_strain": "facility_closure",
    "maternity_care_loss": "service_suspension",
    "service_line_cut": "service_closure",
    "er_crowding_or_diversion": "service_reduction",
    "public_health_capacity_cut": "capacity_reduction",
    "behavioral_health_access_strain": "service_reduction",
    "ambulance_or_ems_strain": "service_reduction",
    "specialty_care_delay": "service_reduction",
}

NON_OPERATIONAL_PRESSURE_TYPES = {
    "hospital_closure",
    "coverage_disruption",
    "medicaid_access_pressure",
    "medical_debt_or_affordability",
    "staffing_shortage_access",
    "context_only",
}

SERVICE_LINE_TERMS = {
    "labor and delivery": "labor_and_delivery",
    "labor, delivery": "labor_and_delivery",
    "maternity": "maternity",
    "emergency": "emergency_care",
    "behavioral health": "behavioral_health",
    "psychiatric": "psychiatric_care",
    "pediatrics": "pediatrics",
    "dialysis": "dialysis",
    "oncology": "oncology",
    "primary care": "primary_care",
    "urgent care": "urgent_care",
    "surgery": "surgery",
    "rehabilitation": "rehabilitation",
    "skilled nursing": "skilled_nursing",
    "pharmacy": "pharmacy",
    "ambulance": "ambulance_ems",
    "ems": "ambulance_ems",
    "specialty": "specialty_care",
}


@dataclass(frozen=True)
class Proposal:
    field: str
    value: Any
    provenance_type: str
    source_field: str
    supporting_text: str
    confidence: float
    rule_id: str
    rule_version: str = "1"
    warning_codes: tuple[str, ...] = ()

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source_field": self.source_field,
            "supporting_text": self.supporting_text,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
        }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_source_records(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sources", "records", "claims", "reviewed_records"):
            if isinstance(payload.get(key), list):
                return [dict(row) for row in payload[key] if isinstance(row, dict)]
    raise ValueError(f"unsupported Care Line source JSON shape: {path}")


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _bool(row: Mapping[str, Any], key: str) -> bool:
    return row.get(key) is True or str(row.get(key) or "").strip().lower() == "true"


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{stable_json_hash(parts)[:16]}"


def source_payload_fingerprint(row: Mapping[str, Any]) -> str:
    payload = {
        "source_record_id": _text(row, "source_record_id", "producer_record_id", "care_line_record_id"),
        "title": _text(row, "title"),
        "url": _text(row, "url", "canonical_url", "source_url"),
        "publisher": _text(row, "publisher", "source_name"),
        "published_at": _text(row, "published_at", "source_published_date"),
        "evidence_text": _text(row, "evidence_text", "claim_supported", "summary_or_snippet"),
        "pressure_type": _text(row, "pressure_type"),
        "location_name": _text(row, "location_name"),
        "state": _text(row, "state"),
    }
    return stable_json_hash(payload)


def proposal_fingerprint(proposals: Iterable[Proposal | Mapping[str, Any]]) -> str:
    rows = []
    for proposal in proposals:
        if isinstance(proposal, Proposal):
            rows.append(proposal.fingerprint_payload())
        else:
            rows.append({key: proposal.get(key) for key in ("field", "value", "source_field", "supporting_text", "rule_id", "rule_version")})
    return stable_json_hash(sorted(rows, key=lambda item: (str(item.get("field") or ""), str(item.get("value") or ""))))


def _structured_provenance(field: str, value: Any, source_field: str) -> FieldProvenance:
    return FieldProvenance(
        value=value,
        provenance_type="structured_input",
        source_field=source_field,
        supporting_text=str(value or ""),
        confidence=1.0,
        review_status="confirmed",
    )


def _proposal_provenance(proposal: Proposal, *, review_status: str = "proposed", reviewer: str = "", reason: str = "") -> FieldProvenance:
    provenance_type = proposal.provenance_type
    if review_status == "confirmed" and reviewer:
        provenance_type = "reviewer_confirmed"
    return FieldProvenance(
        value=proposal.value,
        provenance_type=provenance_type,
        source_field=proposal.source_field,
        supporting_text=proposal.supporting_text,
        confidence=proposal.confidence,
        review_status=review_status,  # type: ignore[arg-type]
        reviewer=reviewer,
        rule_id=proposal.rule_id,
        rule_version=proposal.rule_version,
        decided_at=utc_now_text() if reviewer else "",
        decision_reason=reason,
    )


def parse_location(row: Mapping[str, Any]) -> dict[str, str]:
    location = _text(row, "location_name", "location_text")
    city = _text(row, "city")
    state = _text(row, "state")
    if location and "," in location:
        left, right = [part.strip() for part in location.split(",", 1)]
        city = city or left
        state = state or right
    return {
        "city": city,
        "county": _text(row, "county"),
        "state": state,
        "postal_code": _text(row, "postal_code", "zip"),
        "country_code": _text(row, "country_code") or "US",
        "location_text": location or ", ".join(part for part in (city, state) if part),
        "geographic_scope": _text(row, "location_scope", "geographic_scope") or ("statewide" if location and location.lower() in {state.lower(), "pennsylvania", "united states"} else "city"),
    }


def detect_service_line(row: Mapping[str, Any]) -> Proposal | None:
    explicit = _text(row, "service_line", "affected_service_line")
    blob = " ".join([explicit, _text(row, "title"), _text(row, "evidence_text"), _text(row, "summary_or_snippet")]).lower()
    for term, normalized in SERVICE_LINE_TERMS.items():
        if term in blob:
            return Proposal("service_line", normalized, "deterministic_extraction" if not explicit else "structured_input", "service_line" if explicit else "title/evidence_text", term, 0.9 if explicit else 0.82, "service_line_term_map_v1")
    return None


def extraction_proposals(row: Mapping[str, Any]) -> list[Proposal]:
    title = _text(row, "title")
    proposals: list[Proposal] = []
    match = re.search(r"^(?P<facility>.+?) announces closure of (?P<city>.+?) clinic", title, flags=re.IGNORECASE)
    if match:
        proposals.append(Proposal("facility_name", match.group("facility").strip(), "deterministic_extraction", "title", match.group(0), 0.86, "clinic_closure_title_v1"))
        proposals.append(Proposal("provider_name", match.group("facility").strip(), "deterministic_extraction", "title", match.group(0), 0.82, "clinic_closure_title_v1"))
        proposals.append(Proposal("event_type", "facility_closure", "deterministic_extraction", "title", match.group(0), 0.88, "clinic_closure_title_v1"))
        proposals.append(Proposal("city", match.group("city").strip(), "deterministic_extraction", "title", match.group(0), 0.8, "clinic_closure_title_v1"))
    match = re.search(r"after (?P<facility>.+?) halts labor, delivery services", title, flags=re.IGNORECASE)
    if match:
        proposals.append(Proposal("facility_name", match.group("facility").strip(), "deterministic_extraction", "title", match.group(0), 0.88, "labor_delivery_halt_title_v1"))
        proposals.append(Proposal("provider_name", match.group("facility").strip(), "deterministic_extraction", "title", match.group(0), 0.84, "labor_delivery_halt_title_v1"))
        proposals.append(Proposal("event_type", "service_suspension", "deterministic_extraction", "title", match.group(0), 0.9, "labor_delivery_halt_title_v1"))
        proposals.append(Proposal("service_line", "labor_and_delivery", "deterministic_extraction", "title", match.group(0), 0.92, "labor_delivery_halt_title_v1"))
    match = re.search(r"^(?P<facility>.+?) to close\b", title, flags=re.IGNORECASE)
    if match:
        proposals.append(Proposal("facility_name", match.group("facility").strip(), "deterministic_extraction", "title", match.group(0), 0.8, "facility_to_close_title_v1"))
        proposals.append(Proposal("event_type", "facility_closure", "deterministic_extraction", "title", match.group(0), 0.84, "facility_to_close_title_v1"))
    service = detect_service_line(row)
    if service and not any(item.field == "service_line" for item in proposals):
        proposals.append(service)
    return proposals


def _proposal_map(proposals: Iterable[Proposal]) -> dict[str, Proposal]:
    out: dict[str, Proposal] = {}
    for proposal in proposals:
        out.setdefault(proposal.field, proposal)
    return out


def _base_record(row: Mapping[str, Any], input_path: Path, index: int, proposals: list[Proposal]) -> CareLineReviewedRecord:
    proposal_by_field = _proposal_map(proposals)
    record_id = _text(row, "source_record_id", "producer_record_id", "care_line_record_id") or _stable_id("care_line_source", source_payload_fingerprint(row), index)
    pressure_type = _text(row, "pressure_type")
    event_type = _text(row, "event_type", "universal_event_type") or str(proposal_by_field.get("event_type").value if proposal_by_field.get("event_type") else PRESSURE_EVENT_MAP.get(pressure_type, ""))
    if pressure_type in NON_OPERATIONAL_PRESSURE_TYPES and event_type not in {"capacity_reduction", "bankruptcy_service_impact"}:
        event_type = "financial_context" if pressure_type in {"hospital_closure", "medical_debt_or_affordability"} else "resource_context"
    location = parse_location(row)
    if proposal_by_field.get("city") and not location["city"]:
        location["city"] = str(proposal_by_field["city"].value)
    field_provenance: dict[str, FieldProvenance] = {
        "producer_record_id": _structured_provenance("producer_record_id", record_id, "source_record_id"),
        "source_url": _structured_provenance("source_url", _text(row, "url", "canonical_url", "source_url"), "url"),
        "source_title": _structured_provenance("source_title", _text(row, "title"), "title"),
        "supporting_passage": _structured_provenance("supporting_passage", _text(row, "evidence_text", "claim_supported", "summary_or_snippet"), "evidence_text"),
    }
    for field in ("facility_name", "provider_name", "event_type", "service_line", "city"):
        if field in proposal_by_field:
            field_provenance[field] = _proposal_provenance(proposal_by_field[field])
    for field, source_field in (("state", "state"), ("location_text", "location_name"), ("geographic_scope", "location_scope"), ("announcement_date", "published_at")):
        value = location.get(field, "") if field in location else _text(row, source_field)
        if value:
            field_provenance[field] = _structured_provenance(field, value, source_field)
    facility = _text(row, "facility_name", "provider_name", "affected_provider", "organization_name")
    provider = _text(row, "provider_name", "affected_provider", "organization_name")
    if not facility and proposal_by_field.get("facility_name"):
        facility = str(proposal_by_field["facility_name"].value)
    if not provider and proposal_by_field.get("provider_name"):
        provider = str(proposal_by_field["provider_name"].value)
    service_line = _text(row, "service_line", "affected_service_line")
    if not service_line and proposal_by_field.get("service_line"):
        service_line = str(proposal_by_field["service_line"].value)
    status = "excluded" if _bool(row, "excluded") else "needs_normalization_review"
    public_status = "public_approved" if _bool(row, "qualifies_for_public_inclusion") else "not_public"
    universal_status = "excluded" if _bool(row, "excluded") else "needs_normalization_review"
    if pressure_type in NON_OPERATIONAL_PRESSURE_TYPES or _bool(row, "context_only"):
        universal_status = "care_line_only"
        status = "care_line_only"
        service_line = ""
    if _text(row, "freshness_status").lower() == "stale" or _text(row, "exclusion_reason") == "stale_current_signal":
        universal_status = "excluded"
        status = "excluded"
    record = CareLineReviewedRecord(
        producer_record_id=record_id,
        record_status=status,  # type: ignore[arg-type]
        review_status="reviewed" if _bool(row, "included") and not _bool(row, "excluded") else "not_reviewed",
        public_status=public_status,  # type: ignore[arg-type]
        universal_event_status=universal_status,  # type: ignore[arg-type]
        care_line_public_eligible=public_status == "public_approved",
        source_url=_text(row, "url", "canonical_url", "source_url"),
        source_title=_text(row, "title"),
        source_publisher=_text(row, "publisher", "source_name"),
        source_publication_date=_text(row, "published_at", "source_published_date"),
        source_type=_text(row, "source_family", "source_type"),
        source_role=_text(row, "source_role"),
        supporting_passage=_text(row, "evidence_text", "claim_supported", "summary_or_snippet"),
        raw_payload_hash=source_payload_fingerprint(row),
        event_type=event_type,
        event_type_raw=_text(row, "event_type", "pressure_type"),
        change_direction="reduced" if event_type not in {"facility_reopening", "service_expansion", "service_restoration"} else "expanded_or_restored",
        permanence=_text(row, "permanence", "temporary_or_permanent") or ("temporary_or_unknown" if event_type else ""),
        announcement_date=_text(row, "announcement_date", "published_at", "source_published_date"),
        effective_date=_text(row, "effective_date", "effective_date_text"),
        date_precision=_text(row, "date_precision", "event_date_precision") or "day",
        service_line=service_line,
        service_line_raw=_text(row, "service_line", "affected_service_line"),
        facility_name=facility,
        provider_name=provider,
        parent_organization=_text(row, "parent_organization"),
        operator_name=_text(row, "operator", "operator_name"),
        former_owner=_text(row, "former_owner", "ownership_change_from"),
        new_owner=_text(row, "new_owner", "ownership_change_to"),
        replacement_provider=_text(row, "replacement_provider", "replacement_provider_named"),
        regulator=_text(row, "regulator"),
        facility_type=_text(row, "facility_type") or ("clinic" if "clinic" in _text(row, "title").lower() else "hospital" if "medical center" in _text(row, "title").lower() else ""),
        city=location["city"],
        county=location["county"],
        state=location["state"],
        postal_code=location["postal_code"],
        country_code=location["country_code"],
        location_text=location["location_text"],
        geographic_scope=location["geographic_scope"],
        claim_summary=_text(row, "claim_supported", "pressure_summary", "summary_or_snippet"),
        evidence_level=_text(row, "evidence_level"),
        evidence_strength=_text(row, "confidence") or "unreviewed",
        is_primary_source=_text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"},
        verification_notes=_text(row, "limitations"),
        duplicate_of_record_id=_text(row, "duplicate_of_record_id", "duplicate_of_producer_record_id"),
        normalization_warnings=[warning for proposal in proposals for warning in proposal.warning_codes],
        field_provenance=field_provenance,
        metadata={
            "input_path": input_path.as_posix(),
            "input_index": index,
            "normalizer_version": NORMALIZER_VERSION,
            "pressure_type": pressure_type,
            "source_payload_fingerprint": source_payload_fingerprint(row),
            "proposal_fingerprint": proposal_fingerprint(proposals),
            "retrieved_at": _text(row, "retrieved_at"),
            "source_exclusion_reason": _text(row, "exclusion_reason"),
            "shadow_exclusion_reason": "no_operational_access_change" if universal_status == "care_line_only" else "",
        },
    )
    issues = record.validation_issues()
    if not issues and universal_status == "needs_normalization_review":
        payload = record.model_dump(mode="json")
        payload["universal_event_status"] = "needs_normalization_review"
        return CareLineReviewedRecord.model_validate(payload)
    return record


def normalize_historical_records(records: Iterable[Mapping[str, Any]], *, input_path: Path, sample_id: str) -> dict[str, Any]:
    reviewed_records: list[CareLineReviewedRecord] = []
    review_items: list[dict[str, Any]] = []
    for index, row in enumerate(records, start=1):
        proposals = extraction_proposals(row)
        record = _base_record(row, input_path, index, proposals)
        if record.universal_event_status == "needs_normalization_review" and any(item.field in {"facility_name", "provider_name", "event_type", "service_line", "city"} for item in proposals):
            review_items.append(_review_item(record, row, proposals))
        reviewed_records.append(record)
    return {
        "schema_version": SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "sample_id": sample_id,
        "records": reviewed_records,
        "review_items": review_items,
        "metrics": normalization_metrics(reviewed_records),
    }


def _review_item(record: CareLineReviewedRecord, source_row: Mapping[str, Any], proposals: list[Proposal]) -> dict[str, Any]:
    return {
        "review_schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
        "producer_record_id": record.producer_record_id,
        "source_payload_fingerprint": source_payload_fingerprint(source_row),
        "proposed_value_fingerprint": proposal_fingerprint(proposals),
        "source_title": record.source_title,
        "publisher": record.source_publisher,
        "url": record.source_url,
        "supporting_passage": record.supporting_passage,
        "existing_structured_fields": {
            "facility_name": _text(source_row, "facility_name", "provider_name"),
            "event_type": _text(source_row, "event_type", "pressure_type"),
            "service_line": _text(source_row, "service_line", "affected_service_line"),
            "location_name": _text(source_row, "location_name"),
            "state": _text(source_row, "state"),
            "published_at": _text(source_row, "published_at", "source_published_date"),
        },
        "proposals": [proposal.fingerprint_payload() | {"confidence": proposal.confidence, "provenance_type": proposal.provenance_type} for proposal in proposals],
        "missing_required_fields": [issue.field for issue in record.validation_issues()],
        "conflicts": [],
        "suggested_reviewer_action": "confirm_or_correct_proposed_values",
    }


def normalization_metrics(records: Iterable[CareLineReviewedRecord]) -> dict[str, Any]:
    rows = list(records)
    provenance_counts: Counter[str] = Counter()
    field_total = 0
    for record in rows:
        for provenance in record.field_provenance.values():
            field_total += 1
            provenance_counts[provenance.provenance_type] += 1
    recoverable = [row for row in rows if row.universal_event_status in {"universal_event_ready", "needs_normalization_review"}]
    return {
        "records_examined": len(rows),
        "records_already_complete": sum(1 for row in rows if row.universal_event_status == "universal_event_ready" and not row.normalization_warnings),
        "records_requiring_proposals": sum(1 for row in rows if any(p.provenance_type == "deterministic_extraction" for p in row.field_provenance.values())),
        "records_reviewer_confirmed": sum(1 for row in rows if any(p.provenance_type == "reviewer_confirmed" for p in row.field_provenance.values())),
        "records_reviewer_corrected": sum(1 for row in rows if any(p.provenance_type == "reviewer_corrected" for p in row.field_provenance.values())),
        "records_unresolved": sum(1 for row in rows if row.universal_event_status == "needs_normalization_review"),
        "records_excluded": sum(1 for row in rows if row.universal_event_status == "excluded"),
        "care_line_only": sum(1 for row in rows if row.universal_event_status == "care_line_only"),
        "field_recovery_rate": round(sum(1 for row in rows for p in row.field_provenance.values() if p.provenance_type in {"deterministic_extraction", "reviewer_confirmed", "reviewer_corrected"}) / max(1, field_total), 4),
        "facility_provider_recovery_rate": round(sum(1 for row in recoverable if row.has_subject) / max(1, len(recoverable)), 4),
        "geography_recovery_rate": round(sum(1 for row in recoverable if row.has_location) / max(1, len(recoverable)), 4),
        "date_recovery_rate": round(sum(1 for row in recoverable if row.has_event_date) / max(1, len(recoverable)), 4),
        "service_line_recovery_rate": round(sum(1 for row in recoverable if row.service_line and row.service_line != "unknown") / max(1, len([row for row in recoverable if row.event_type in {"service_closure", "service_suspension", "service_reduction"}])), 4),
        "provenance": dict(sorted(provenance_counts.items())),
    }


def write_review_package(review_dir: Path, sample_id: str, normalized: Mapping[str, Any]) -> dict[str, str]:
    review_dir.mkdir(parents=True, exist_ok=True)
    review_json = review_dir / f"{sample_id}.normalization-review.json"
    review_md = review_dir / f"{sample_id}.normalization-review.md"
    template = review_dir / f"{sample_id}.normalization-decisions-template.json"
    items = list(normalized.get("review_items") or [])
    review_payload = {
        "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "normalizer_version": NORMALIZER_VERSION,
        "review_items": items,
    }
    review_json.write_text(json.dumps(review_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    review_md.write_text(render_review_markdown(sample_id, items), encoding="utf-8")
    template.write_text(json.dumps(decisions_template(sample_id, items), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"review_json": str(review_json), "review_md": str(review_md), "decisions_template": str(template)}


def render_review_markdown(sample_id: str, items: list[Mapping[str, Any]]) -> str:
    lines = ["# Care Line Normalization Review", "", f"- Sample: `{sample_id}`", f"- Review items: `{len(items)}`", ""]
    for item in items:
        lines.append(f"## {item.get('producer_record_id')}")
        lines.append(f"- Publisher: {item.get('publisher')}")
        lines.append(f"- Title: {item.get('source_title')}")
        lines.append(f"- Suggested action: `{item.get('suggested_reviewer_action')}`")
        lines.append(f"- Missing fields: `{', '.join(item.get('missing_required_fields') or []) or 'none'}`")
        lines.append("")
    return "\n".join(lines)


def decisions_template(sample_id: str, items: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "sample_id": sample_id,
        "decisions": [
            {
                "producer_record_id": item.get("producer_record_id"),
                "source_payload_fingerprint": item.get("source_payload_fingerprint"),
                "proposed_value_fingerprint": item.get("proposed_value_fingerprint"),
                "reviewer": "",
                "decision": "",
                "reason": "",
                "field_decisions": [
                    {
                        "field": proposal.get("field"),
                        "action": "",
                        "value": proposal.get("value"),
                    }
                    for proposal in item.get("proposals") or []
                    if proposal.get("field") in {"facility_name", "provider_name", "event_type", "service_line", "city"}
                ],
            }
            for item in items
        ],
    }


def sample_decisions_from_review(sample_id: str, items: list[Mapping[str, Any]], *, reviewer: str = "phase6-reviewer") -> dict[str, Any]:
    payload = decisions_template(sample_id, items)
    for decision in payload["decisions"]:
        decision["reviewer"] = reviewer
        decision["decision"] = "confirm_proposed_value"
        decision["reason"] = "Phase 6 bounded historical sample review confirmed source-title proposals for calibration."
        for field_decision in decision["field_decisions"]:
            field_decision["action"] = "confirm"
    return payload


def import_review_decisions(source_records: Iterable[Mapping[str, Any]], *, input_path: Path, decisions_path: Path, output_path: Path, sample_id: str | None = None) -> dict[str, Any]:
    source_rows = [dict(row) for row in source_records]
    normalized = normalize_historical_records(source_rows, input_path=input_path, sample_id=sample_id or decisions_path.stem)
    record_by_id = {record.producer_record_id: record for record in normalized["records"]}
    review_by_id = {item["producer_record_id"]: item for item in normalized["review_items"]}
    decisions_payload = _load_json(decisions_path)
    if decisions_payload.get("schema_version") != DECISIONS_SCHEMA_VERSION:
        raise ValueError("unsupported decisions schema_version")
    if sample_id and decisions_payload.get("sample_id") != sample_id:
        raise ValueError("sample_id mismatch")
    accepted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    updated_records = dict(record_by_id)
    for decision in decisions_payload.get("decisions") or []:
        try:
            record_id = str(decision.get("producer_record_id") or "")
            record = record_by_id.get(record_id)
            if record is None:
                raise ValueError(f"unknown producer_record_id: {record_id}")
            review_item = review_by_id.get(record_id)
            if review_item is None:
                raise ValueError(f"record has no pending normalization review item: {record_id}")
            if decision.get("source_payload_fingerprint") != review_item["source_payload_fingerprint"]:
                raise ValueError("stale review decision: source payload fingerprint changed")
            if decision.get("proposed_value_fingerprint") != review_item["proposed_value_fingerprint"]:
                raise ValueError("stale review decision: proposed value fingerprint changed")
            reviewer = str(decision.get("reviewer") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            action = str(decision.get("decision") or "").strip()
            if not reviewer or not reason:
                raise ValueError("reviewer and reason are required")
            if action not in {"confirm_proposed_value", "replace_proposed_value", "exclude_from_universal_events", "classify_non_operational", "mark_duplicate", "mark_stale", "defer", "request_more_evidence"}:
                raise ValueError(f"unsupported review decision: {action}")
            updated = apply_decision(record, review_item, decision)
            updated_records[record_id] = updated
            accepted.append({"producer_record_id": record_id, "decision": action, "universal_event_status": updated.universal_event_status})
        except Exception as exc:  # noqa: BLE001
            errors.append({"producer_record_id": str(decision.get("producer_record_id") or ""), "error": f"{type(exc).__name__}: {exc}"})
    if errors:
        return {"accepted": [], "errors": errors, "output": ""}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(updated_records.values(), key=lambda row: (row.producer_record_id, row.version, row.version_id))
    output_path.write_text(deterministic_records_json(records), encoding="utf-8")
    return {"accepted": accepted, "errors": [], "output": str(output_path), "metrics": normalization_metrics(records)}


def apply_decision(record: CareLineReviewedRecord, review_item: Mapping[str, Any], decision: Mapping[str, Any]) -> CareLineReviewedRecord:
    payload = record.model_dump(mode="json")
    provenance = {key: FieldProvenance.model_validate(value) for key, value in record.field_provenance.items()}
    proposals = {proposal["field"]: proposal for proposal in review_item.get("proposals") or []}
    reviewer = str(decision.get("reviewer") or "")
    reason = str(decision.get("reason") or "")
    action = str(decision.get("decision") or "")
    if action in {"exclude_from_universal_events", "classify_non_operational", "mark_stale", "defer", "request_more_evidence"}:
        payload["universal_event_status"] = "care_line_only" if action == "classify_non_operational" else "excluded" if action in {"exclude_from_universal_events", "mark_stale"} else "needs_normalization_review"
        payload["record_status"] = payload["universal_event_status"]
        payload["metadata"]["shadow_exclusion_reason"] = "no_operational_access_change" if action == "classify_non_operational" else "not_review_approved"
    elif action == "mark_duplicate":
        payload["universal_event_status"] = "excluded"
        payload["record_status"] = "excluded"
        payload["duplicate_of_record_id"] = str(decision.get("duplicate_of_record_id") or "")
        payload["metadata"]["shadow_exclusion_reason"] = "duplicate_producer_record"
    else:
        for field_decision in decision.get("field_decisions") or []:
            field = str(field_decision.get("field") or "")
            field_action = str(field_decision.get("action") or "")
            value = field_decision.get("value")
            if field not in proposals:
                raise ValueError(f"unsupported field decision: {field}")
            if field_action == "confirm":
                if value in (None, ""):
                    raise ValueError(f"blank confirmed value for {field}")
                payload[field] = value
                provenance[field] = FieldProvenance(
                    value=value,
                    provenance_type="reviewer_confirmed",
                    source_field=str(proposals[field].get("source_field") or ""),
                    supporting_text=str(proposals[field].get("supporting_text") or ""),
                    confidence=float(proposals[field].get("confidence") or 0.0),
                    review_status="confirmed",
                    reviewer=reviewer,
                    rule_id=str(proposals[field].get("rule_id") or ""),
                    rule_version=str(proposals[field].get("rule_version") or ""),
                    decided_at=utc_now_text(),
                    decision_reason=reason,
                )
            elif field_action == "replace":
                if value in (None, ""):
                    raise ValueError(f"blank replacement value for {field}")
                payload[field] = value
                provenance[field] = FieldProvenance(value=value, provenance_type="reviewer_corrected", source_field=str(proposals[field].get("source_field") or ""), supporting_text=str(proposals[field].get("supporting_text") or ""), confidence=1.0, review_status="corrected", reviewer=reviewer, decided_at=utc_now_text(), decision_reason=reason)
            elif field_action in {"mark_not_present", "defer", "reject"}:
                provenance[field] = FieldProvenance(value="", provenance_type="unresolved", source_field=str(proposals[field].get("source_field") or ""), supporting_text=str(proposals[field].get("supporting_text") or ""), confidence=0.0, review_status="unresolved", reviewer=reviewer, decided_at=utc_now_text(), decision_reason=reason)
            else:
                raise ValueError(f"unsupported field action: {field_action}")
        payload["review_status"] = "approved"
        payload["record_status"] = "universal_event_ready"
        payload["universal_event_status"] = "universal_event_ready"
    payload["field_provenance"] = {key: value.model_dump(mode="json") for key, value in provenance.items()}
    payload["updated_at"] = utc_now_text()
    payload["metadata"]["normalization_review"] = {"reviewer": reviewer, "decision": action, "reason": reason, "reviewed_at": payload["updated_at"]}
    updated = CareLineReviewedRecord.model_validate(payload)
    issues = updated.validation_issues()
    if updated.universal_event_status == "universal_event_ready" and issues:
        raise ValueError("reviewed record still fails Universal Events profile: " + ", ".join(issue.code for issue in issues))
    if updated.event_type and updated.event_type not in CARE_LINE_EVENT_TYPES and updated.universal_event_status == "universal_event_ready":
        raise ValueError(f"unsupported event_type: {updated.event_type}")
    if updated.service_line and updated.service_line not in SERVICE_LINES:
        raise ValueError(f"unsupported service_line: {updated.service_line}")
    return updated


def write_normalization_report(path: Path, normalized: Mapping[str, Any], reviewed_records: list[CareLineReviewedRecord]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "sample_id": normalized.get("sample_id"),
        "metrics": normalization_metrics(reviewed_records),
        "record_status_counts": dict(sorted(Counter(row.universal_event_status for row in reviewed_records).items())),
        "five_record_remediation": five_record_remediation(reviewed_records),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def five_record_remediation(records: Iterable[CareLineReviewedRecord]) -> list[dict[str, Any]]:
    wanted = [
        "heraldstandard-hospital-funding",
        "kcrg-centerville-clinic-closure",
        "searchlightnm-labor-delivery-halt",
        "kcrg-centerville-clinic-closure-stale",
        "medicaidgov-enrollment-map",
    ]
    rows = []
    for suffix in wanted:
        match = next((record for record in records if suffix in record.producer_record_id), None)
        if match is None:
            continue
        rows.append(
            {
                "producer_record_id": match.producer_record_id,
                "operational": match.universal_event_status == "universal_event_ready",
                "universal_event_status": match.universal_event_status,
                "event_type": match.event_type,
                "facility_name": match.facility_name,
                "service_line": match.service_line,
                "reason": match.metadata.get("normalization_review", {}).get("reason") or match.metadata.get("source_exclusion_reason") or ("not an operational access event" if match.universal_event_status == "care_line_only" else ""),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize historical Care Line records into canonical reviewed records.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--review-dir", default="")
    parser.add_argument("--import-review", default="")
    parser.add_argument("--write-review-package", action="store_true")
    parser.add_argument("--write-sample-decisions", action="store_true")
    parser.add_argument("--normalization-report", default="")
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input)
        output_path = Path(args.output)
        sample_id = args.sample_id or f"care-line-normalization-{sha256(input_path.as_posix().encode('utf-8')).hexdigest()[:8]}"
        records = load_source_records(input_path)
        normalized = normalize_historical_records(records, input_path=input_path, sample_id=sample_id)
        paths: dict[str, str] = {}
        if args.review_dir or args.write_review_package or args.write_sample_decisions:
            paths.update(write_review_package(Path(args.review_dir or output_path.parent), sample_id, normalized))
        if args.write_sample_decisions:
            decisions = sample_decisions_from_review(sample_id, normalized["review_items"])
            decisions_path = Path(args.review_dir or output_path.parent) / f"{sample_id}.normalization-decisions.json"
            decisions_path.write_text(json.dumps(decisions, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
            paths["sample_decisions"] = str(decisions_path)
        if args.import_review:
            result = import_review_decisions(records, input_path=input_path, decisions_path=Path(args.import_review), output_path=output_path, sample_id=sample_id)
            if result["errors"]:
                print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False), file=sys.stderr)
                return 1
            reviewed_records = [CareLineReviewedRecord.model_validate(row) for row in (_load_json(output_path).get("records") or [])]
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            reviewed_records = list(normalized["records"])
            output_path.write_text(deterministic_records_json(reviewed_records), encoding="utf-8")
            result = {"accepted": [], "errors": [], "output": str(output_path), "metrics": normalization_metrics(reviewed_records)}
        if args.normalization_report:
            write_normalization_report(Path(args.normalization_report), normalized, reviewed_records)
            paths["normalization_report"] = args.normalization_report
        print(json.dumps({"sample_id": sample_id, "output": str(output_path), "paths": paths, "result": result}, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
