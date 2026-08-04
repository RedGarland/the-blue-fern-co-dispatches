from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "bluefern.care_line.reviewed_record.v1"
PRODUCER = "Care Line"

CARE_LINE_EVENT_TYPES = {
    "facility_closure",
    "planned_facility_closure",
    "temporary_facility_suspension",
    "facility_reopening",
    "service_closure",
    "service_suspension",
    "service_reduction",
    "hours_reduction",
    "capacity_reduction",
    "ownership_change",
    "operator_change",
    "facility_relocation",
    "facility_conversion",
    "service_expansion",
    "service_restoration",
    "bankruptcy_service_impact",
}

SERVICE_LINES = {
    "emergency_care",
    "labor_and_delivery",
    "maternity",
    "inpatient_care",
    "behavioral_health",
    "psychiatric_care",
    "pediatrics",
    "dialysis",
    "oncology",
    "primary_care",
    "urgent_care",
    "surgery",
    "rehabilitation",
    "skilled_nursing",
    "pharmacy",
    "ambulance_ems",
    "specialty_care",
    "other",
    "unknown",
}

PROVENANCE_TYPES = {
    "source_explicit",
    "structured_input",
    "deterministic_extraction",
    "cross_artifact_join",
    "reviewer_confirmed",
    "reviewer_corrected",
    "unresolved",
}

SERVICE_EVENT_TYPES = {
    "service_closure",
    "service_suspension",
    "service_reduction",
    "hours_reduction",
    "service_expansion",
    "service_restoration",
}

OWNERSHIP_EVENT_TYPES = {"ownership_change", "operator_change"}
FACILITY_EVENT_TYPES = {"facility_closure", "planned_facility_closure", "temporary_facility_suspension", "facility_reopening", "facility_relocation", "facility_conversion"}
STATEWIDE_EVENT_TYPES = {"capacity_reduction", "bankruptcy_service_impact"}
NON_OPERATIONAL_EVENT_TYPES = {"financial_context", "workforce_context", "resource_context", "policy_context", "context_only", ""}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def stable_json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    provenance_type: str
    source_field: str = ""
    supporting_text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_status: Literal["proposed", "confirmed", "corrected", "rejected", "unresolved"] = "unresolved"
    reviewer: str = ""
    rule_id: str = ""
    rule_version: str = ""
    decided_at: str = ""
    decision_reason: str = ""

    @model_validator(mode="after")
    def validate_provenance_type(self) -> "FieldProvenance":
        if self.provenance_type not in PROVENANCE_TYPES:
            raise ValueError(f"unsupported provenance_type: {self.provenance_type}")
        if self.review_status in {"confirmed", "corrected"} and self.provenance_type not in {"source_explicit", "structured_input", "reviewer_confirmed", "reviewer_corrected"}:
            raise ValueError("confirmed/corrected fields must preserve reviewer or source-explicit provenance")
        return self


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    code: str
    message: str


class CareLineReviewedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    producer: str = PRODUCER
    producer_record_id: str
    version: int = 1
    version_id: str = ""
    record_status: Literal["active", "universal_event_ready", "needs_normalization_review", "needs_evidence_review", "care_line_only", "excluded", "malformed", "withdrawn", "superseded", "duplicate"] = "active"
    review_status: Literal["not_reviewed", "reviewed", "approved", "needs_review", "rejected", "corrected"] = "not_reviewed"
    public_status: Literal["public_approved", "not_public", "care_line_only"] = "not_public"
    universal_event_status: Literal["universal_event_ready", "needs_normalization_review", "needs_evidence_review", "care_line_only", "excluded", "malformed", "withdrawn", "superseded", "duplicate"] = "needs_normalization_review"
    care_line_public_eligible: bool = False

    source_url: str
    source_title: str
    source_publisher: str
    source_publication_date: str = ""
    source_type: str = ""
    source_role: str = ""
    supporting_passage: str = ""
    effective_evidence_text: str = ""
    evidence_provenance_type: str = "missing"
    evidence_valid_for_universal_event: bool = False
    recommended_status: str = "needs_evidence_review"
    review_notes: str = ""
    raw_payload_hash: str
    created_at: str = Field(default_factory=utc_now_text)
    updated_at: str = Field(default_factory=utc_now_text)

    event_type: str = ""
    event_type_raw: str = ""
    change_direction: str = ""
    permanence: str = ""
    announcement_date: str = ""
    effective_date: str = ""
    date_precision: str = ""
    service_line: str = ""
    service_line_raw: str = ""

    facility_name: str = ""
    provider_name: str = ""
    parent_organization: str = ""
    operator_name: str = ""
    former_owner: str = ""
    new_owner: str = ""
    replacement_provider: str = ""
    regulator: str = ""
    facility_type: str = ""

    address_line_1: str = ""
    address_line_2: str = ""
    city: str = ""
    county: str = ""
    state: str = ""
    postal_code: str = ""
    country_code: str = "US"
    location_text: str = ""
    geographic_scope: str = ""

    claim_summary: str = ""
    evidence_level: str = ""
    evidence_strength: str = ""
    is_primary_source: bool = False
    verification_notes: str = ""

    is_withdrawn: bool = False
    withdrawal_reason: str = ""
    supersedes_record_id: str = ""
    duplicate_of_record_id: str = ""
    correction_reason: str = ""
    correction_history: list[dict[str, Any]] = Field(default_factory=list)
    normalization_warnings: list[str] = Field(default_factory=list)
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_schema(self) -> "CareLineReviewedRecord":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if self.producer != PRODUCER:
            raise ValueError("producer must be Care Line")
        if not self.producer_record_id.strip():
            raise ValueError("producer_record_id is required")
        if not self.source_url.strip():
            raise ValueError("source_url is required")
        if self.event_type and self.event_type not in CARE_LINE_EVENT_TYPES and self.event_type not in NON_OPERATIONAL_EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type}")
        if self.service_line and self.service_line not in SERVICE_LINES:
            raise ValueError(f"unsupported service_line: {self.service_line}")
        if not self.version_id:
            object.__setattr__(self, "version_id", reviewed_record_version_id(self))
        return self

    @property
    def has_subject(self) -> bool:
        return bool(self.facility_name.strip() or self.provider_name.strip())

    @property
    def has_location(self) -> bool:
        if self.geographic_scope == "statewide":
            return bool(self.state.strip())
        return bool(self.state.strip() and (self.city.strip() or self.county.strip() or self.address_line_1.strip() or self.location_text.strip()))

    @property
    def has_event_date(self) -> bool:
        return bool(self.announcement_date.strip() or self.effective_date.strip())

    @property
    def universal_event_eligible(self) -> bool:
        return self.universal_event_status == "universal_event_ready" and not self.is_withdrawn and not self.duplicate_of_record_id and not self.validation_issues()

    def validation_profile(self) -> str:
        if self.event_type in FACILITY_EVENT_TYPES:
            return "facility_event"
        if self.event_type in SERVICE_EVENT_TYPES:
            return "service_event"
        if self.event_type in OWNERSHIP_EVENT_TYPES:
            return "ownership_event"
        if self.event_type in STATEWIDE_EVENT_TYPES:
            return "statewide_operational_event"
        return "non_operational_or_unknown"

    def validation_issues(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if self.event_type in NON_OPERATIONAL_EVENT_TYPES:
            return issues if self.universal_event_status != "universal_event_ready" else [ValidationIssue(field="event_type", code="non_operational_event", message="non-operational Care Line records cannot be Universal Events")]
        if self.event_type not in CARE_LINE_EVENT_TYPES:
            issues.append(ValidationIssue(field="event_type", code="unsupported_event_type", message="unsupported or missing event type"))
            return issues
        if not self.has_subject and self.event_type not in STATEWIDE_EVENT_TYPES:
            issues.append(ValidationIssue(field="facility_name", code="missing_subject", message="facility or provider subject is required"))
        if not self.has_location:
            issues.append(ValidationIssue(field="location", code="missing_geography", message="state plus one meaningful geography component is required"))
        if not self.has_event_date:
            issues.append(ValidationIssue(field="announcement_date", code="missing_event_date", message="announcement or effective date is required"))
        if not self.supporting_passage.strip():
            issues.append(ValidationIssue(field="supporting_passage", code="missing_evidence", message="supporting passage is required"))
        if self.event_type in SERVICE_EVENT_TYPES and self.service_line in {"", "unknown"}:
            issues.append(ValidationIssue(field="service_line", code="missing_service_line", message="service-line event requires a reviewed service line"))
        if self.event_type in OWNERSHIP_EVENT_TYPES and not (self.former_owner.strip() or self.new_owner.strip() or self.operator_name.strip()):
            issues.append(ValidationIssue(field="new_owner", code="missing_owner_or_operator", message="ownership/operator event requires former/new owner or operator"))
        if self.event_type in FACILITY_EVENT_TYPES and not self.permanence.strip():
            issues.append(ValidationIssue(field="permanence", code="missing_permanence", message="facility event requires permanence or reviewed unknown"))
        if self.event_type in STATEWIDE_EVENT_TYPES:
            if self.geographic_scope != "statewide":
                issues.append(ValidationIssue(field="geographic_scope", code="statewide_scope_required", message="statewide profile requires statewide geography"))
            if not self.change_direction.strip():
                issues.append(ValidationIssue(field="change_direction", code="missing_operational_change", message="statewide event requires documented operational access change"))
        return issues

    def deterministic_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["created_at"] = ""
        payload["updated_at"] = ""
        payload["field_provenance"] = {key: payload["field_provenance"][key] for key in sorted(payload["field_provenance"])}
        metadata = {key: payload["metadata"][key] for key in sorted(payload["metadata"])}
        for key in ("input_path", "source_path"):
            value = str(metadata.get(key) or "")
            if ":" in value or value.startswith(("/", "\\")):
                metadata[key] = ""
        payload["metadata"] = metadata
        return payload

    def to_adapter_record(self) -> dict[str, Any]:
        return {
            "source_record_id": self.producer_record_id,
            "care_line_review_status": self.review_status,
            "url": self.source_url,
            "canonical_url": self.source_url,
            "publisher": self.source_publisher,
            "title": self.source_title,
            "published_at": self.source_publication_date,
            "source_published_date": self.source_publication_date[:10],
            "retrieved_at": self.metadata.get("retrieved_at") or self.updated_at,
            "source_family": self.source_type,
            "source_role": self.source_role,
            "evidence_text": self.supporting_passage,
            "claim_supported": self.claim_summary,
            "pressure_summary": self.claim_summary,
            "facility_name": self.facility_name,
            "provider_name": self.provider_name,
            "parent_organization": self.parent_organization,
            "operator": self.operator_name,
            "former_owner": self.former_owner,
            "new_owner": self.new_owner,
            "replacement_provider": self.replacement_provider,
            "regulator": self.regulator,
            "facility_type": self.facility_type,
            "address_line_1": self.address_line_1,
            "address": self.address_line_1,
            "address_line_2": self.address_line_2,
            "city": self.city,
            "county": self.county,
            "state": self.state,
            "postal_code": self.postal_code,
            "country_code": self.country_code,
            "location_name": self.location_text or ", ".join(part for part in (self.city, self.state) if part),
            "location_scope": self.geographic_scope,
            "event_type": self.event_type,
            "universal_event_type": self.event_type,
            "service_line": self.service_line,
            "affected_service_line": self.service_line_raw,
            "announcement_date": self.announcement_date,
            "effective_date": self.effective_date,
            "event_date_precision": self.date_precision,
            "permanence": self.permanence,
            "evidence_level": self.evidence_level,
            "source_public_story_eligible": self.care_line_public_eligible,
            "qualifies_for_public_inclusion": self.public_status == "public_approved",
            "pressure_signal": self.universal_event_status == "universal_event_ready",
            "included": self.public_status == "public_approved",
            "excluded": self.universal_event_status in {"excluded", "malformed"},
            "shadow_exclusion_reason": self.metadata.get("shadow_exclusion_reason", ""),
            "duplicate_of_producer_record_id": self.duplicate_of_record_id,
            "withdrawn": self.is_withdrawn,
            "raw_payload_hash": self.raw_payload_hash,
            "_care_line_reviewed_record_contract": {
                "schema_version": self.schema_version,
                "version": self.version,
                "version_id": self.version_id,
                "supersedes_record_id": self.supersedes_record_id,
                "universal_event_status": self.universal_event_status,
                "validation_profile": self.validation_profile(),
                "field_provenance": {key: value.model_dump(mode="json") for key, value in sorted(self.field_provenance.items())},
                "correction_history": self.correction_history,
            },
        }


def reviewed_record_version_id(record: CareLineReviewedRecord) -> str:
    payload = record.model_dump(mode="json", exclude={"version_id", "created_at", "updated_at"})
    return f"care_line_reviewed_{stable_json_hash(payload)[:16]}"


def validate_reviewed_record(record: CareLineReviewedRecord) -> list[dict[str, str]]:
    return [issue.model_dump() for issue in record.validation_issues()]


def deterministic_records_json(records: list[CareLineReviewedRecord]) -> str:
    ordered = sorted(records, key=lambda row: (row.producer_record_id, row.version, row.version_id))
    return stable_json({"schema_version": SCHEMA_VERSION, "records": [row.deterministic_dict() for row in ordered]}) + "\n"


def corrected_record(record: CareLineReviewedRecord, *, updates: dict[str, Any], reviewer: str, reason: str, decided_at: str | None = None) -> CareLineReviewedRecord:
    prior = record.deterministic_dict()
    payload = record.model_dump(mode="json")
    payload.update(updates)
    payload["version"] = int(record.version) + 1
    payload["supersedes_record_id"] = record.version_id
    payload["review_status"] = "corrected"
    payload["correction_reason"] = reason
    payload["updated_at"] = decided_at or utc_now_text()
    payload["version_id"] = ""
    payload["correction_history"] = [
        *record.correction_history,
        {
            "superseded_version_id": record.version_id,
            "reviewer": reviewer,
            "reason": reason,
            "decided_at": payload["updated_at"],
            "prior_values_hash": stable_json_hash(prior),
        },
    ]
    return CareLineReviewedRecord.model_validate(payload)
