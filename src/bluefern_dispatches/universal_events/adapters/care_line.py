from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from bluefern_dispatches.care_line_sources import record_is_public
from bluefern_dispatches.story_dedupe import normalize_url
from bluefern_dispatches.universal_events import CandidateStatus, EventDomain, EventStatus, SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.normalization import normalize_name
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EventRow,
    SourceItemRow,
    SourceRow,
)
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION, ResolverThresholds, can_auto_match


ADAPTER_VERSION = "care-line-shadow-v1"
PRODUCER = "Care Line"
PRODUCER_SLUG = "care-line"

SUPPORTED_EVENT_TYPES = {
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

CARE_LINE_PRESSURE_TYPE_MAP = {
    "hospital_closure": "planned_facility_closure",
    "clinic_access_strain": "facility_closure",
    "maternity_care_loss": "service_suspension",
    "service_line_cut": "service_closure",
    "er_crowding_or_diversion": "service_reduction",
    "pharmacy_access_pressure": "facility_closure",
    "public_health_capacity_cut": "capacity_reduction",
    "behavioral_health_access_strain": "service_reduction",
    "ambulance_or_ems_strain": "service_reduction",
    "specialty_care_delay": "service_reduction",
    "staffing_shortage_access": "service_reduction",
}

SERVICE_LINE_MAP = {
    "labor and delivery": "labor_and_delivery",
    "maternity": "maternity",
    "emergency": "emergency_care",
    "emergency department": "emergency_care",
    "ed": "emergency_care",
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
    "ambulance": "ambulance_or_ems",
    "ems": "ambulance_or_ems",
    "specialty": "specialty_care",
}

EXCLUSION_REASONS = {
    "no_operational_access_change",
    "unsupported_event_type",
    "insufficient_source_traceability",
    "missing_source_url",
    "missing_provider_or_facility",
    "missing_geography",
    "missing_change_date",
    "ambiguous_service_change",
    "policy_only",
    "financial_context_only",
    "workforce_only",
    "duplicate_producer_record",
    "not_review_approved",
    "malformed_record",
}

HEALTHCARE_ATTRIBUTE_KEYS = {
    "facility_name_raw",
    "service_line",
    "service_line_normalized",
    "facility_type",
    "change_direction",
    "previous_status",
    "new_status",
    "licensed_beds_before",
    "licensed_beds_after",
    "operating_hours_before",
    "operating_hours_after",
    "temporary_or_permanent",
    "closure_reason",
    "replacement_provider_named",
    "replacement_service_named",
    "ownership_change_from",
    "ownership_change_to",
    "effective_date_text",
    "affected_population_text",
    "travel_impact_reported",
    "care_line_classification",
    "care_line_evidence_level",
}


@dataclass(frozen=True)
class ShadowIngestionConfig:
    allow_create_canonical_entities: bool = False
    auto_resolve_matches: bool = False
    resolver_thresholds: ResolverThresholds = ResolverThresholds()


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(json.dumps(part, sort_keys=True, default=str) if isinstance(part, (dict, list, tuple)) else str(part or "").strip() for part in parts)
    return f"{prefix}_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _stable_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(record: Mapping[str, Any], *keys: str) -> str:
    value = _value(record, *keys)
    return str(value or "").strip()


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            text += "T00:00:00+00:00"
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(record, key)
        if value:
            return value
    return ""


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        rows = payload["sources"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        rows = payload["records"]
    else:
        raise ValueError("Care Line shadow input must be a list, {'sources': [...]}, or {'records': [...]}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _is_review_approved(record: Mapping[str, Any]) -> bool:
    status = _text(record, "care_line_review_status", "review_status", "editorial_review_status").lower()
    if status:
        return status in {"approved", "reviewed", "corrected", "public_approved", "shadow_approved"}
    return record_is_public(dict(record))


def _event_type(record: Mapping[str, Any]) -> tuple[str, str]:
    explicit = _text(record, "universal_event_type", "healthcare_event_type", "event_type").lower()
    if explicit:
        if explicit in SUPPORTED_EVENT_TYPES:
            return explicit, ""
        return "", "unsupported_event_type"
    pressure_type = _text(record, "pressure_type").lower()
    mapped = CARE_LINE_PRESSURE_TYPE_MAP.get(pressure_type, "")
    if not mapped:
        return "", "unsupported_event_type"
    title_blob = " ".join(_text(record, key).lower() for key in ("title", "summary_or_snippet", "evidence_text", "pressure_summary"))
    if pressure_type == "clinic_access_strain" and "relocat" in title_blob:
        mapped = "facility_relocation"
    if pressure_type == "clinic_access_strain" and "reopen" in title_blob:
        mapped = "facility_reopening"
    if pressure_type == "hospital_closure" and any(term in title_blob for term in ("closed", "closes", "closing", "shut down", "shutting down")):
        mapped = "facility_closure"
    return mapped, ""


def _service_line(record: Mapping[str, Any]) -> tuple[str, str]:
    raw = _text(record, "service_line", "affected_service_line")
    raw_lower = raw.lower()
    if raw_lower in set(SERVICE_LINE_MAP.values()):
        return raw, raw_lower
    for term, normalized in SERVICE_LINE_MAP.items():
        if raw_lower and term in raw_lower:
            return raw or term, normalized
    blob = " ".join([_text(record, "title"), _text(record, "summary_or_snippet"), _text(record, "pressure_type")]).lower()
    for term, normalized in SERVICE_LINE_MAP.items():
        if term == "ed":
            continue
        if term in blob:
            return raw or term, normalized
    return raw, ""


def _operational_access_change(record: Mapping[str, Any], event_type: str) -> bool:
    if event_type in SUPPORTED_EVENT_TYPES:
        return True
    blob = " ".join(_text(record, key).lower() for key in ("title", "summary_or_snippet", "evidence_text", "pressure_summary", "claim_supported"))
    return any(
        term in blob
        for term in (
            "closure",
            "closing",
            "shutting down",
            "suspend",
            "reopen",
            "relocat",
            "reduced hours",
            "capacity",
            "service line",
            "labor and delivery",
            "ownership",
            "operator",
            "bankruptcy",
        )
    )


def _facility_name(record: Mapping[str, Any]) -> str:
    return _text(record, "facility_name", "provider_name", "affected_provider", "organization_name", "source_name")


def _location_name(record: Mapping[str, Any]) -> str:
    return _text(record, "location_name", "city", "county", "state", "service_area")


def _exclusion_reason(record: Mapping[str, Any], seen_ids: set[str]) -> str:
    record_id = _text(record, "source_record_id", "producer_record_id", "care_line_record_id")
    if not isinstance(record, dict):
        return "malformed_record"
    if record_id and record_id in seen_ids:
        return "duplicate_producer_record"
    if not _is_review_approved(record):
        return "not_review_approved"
    explicit_exclusion = _text(record, "shadow_exclusion_reason")
    if explicit_exclusion in EXCLUSION_REASONS:
        return explicit_exclusion
    if _text(record, "policy_only").lower() == "true":
        return "policy_only"
    if _text(record, "financial_context_only").lower() == "true":
        return "financial_context_only"
    if _text(record, "workforce_only").lower() == "true":
        return "workforce_only"
    if not _text(record, "url", "canonical_url", "source_url"):
        return "missing_source_url"
    event_type, event_error = _event_type(record)
    if event_error:
        return event_error
    if _text(record, "source_family").lower() in {"opinion", "advocacy"}:
        return "no_operational_access_change"
    if not _facility_name(record):
        return "missing_provider_or_facility"
    if not _location_name(record):
        return "missing_geography"
    if not _date_text(record, "announcement_date", "published_at", "source_published_date", "effective_date", "effective_date_text"):
        return "missing_change_date"
    if not _operational_access_change(record, event_type):
        return "no_operational_access_change"
    if event_type in {"service_closure", "service_suspension", "service_reduction", "service_expansion", "service_restoration"}:
        _, normalized_service = _service_line(record)
        if not normalized_service and _text(record, "service_line", "affected_service_line").lower() not in {"other", "unknown"}:
            return "ambiguous_service_change"
    if not _text(record, "evidence_text", "summary_or_snippet", "claim_supported"):
        return "insufficient_source_traceability"
    return ""


def _state_from_location(record: Mapping[str, Any]) -> str:
    state = _text(record, "state")
    if state:
        return state
    location = _text(record, "location_name")
    if "," in location:
        return location.rsplit(",", 1)[-1].strip()
    return ""


def _city_from_location(record: Mapping[str, Any]) -> str:
    city = _text(record, "city")
    if city:
        return city
    location = _text(record, "location_name")
    if "," in location:
        return location.split(",", 1)[0].strip()
    return ""


def _source_type(record: Mapping[str, Any]) -> str:
    return _text(record, "source_type", "source_family") or "care_line_record"


def _source_item_payload(record: Mapping[str, Any], source_id: str, source_item_id: str, content_hash: str) -> dict[str, Any]:
    published_at = _parse_dt(_value(record, "published_at", "source_published_date"))
    retrieved_at = _parse_dt(_value(record, "retrieved_at"))
    discovered_at = retrieved_at or published_at
    if discovered_at is None:
        raise ValueError("eligible records require a source publication or retrieval timestamp")
    return {
        "source_item_id": source_item_id,
        "source_id": source_id,
        "canonical_url": normalize_url(_text(record, "url", "canonical_url", "source_url")) or _text(record, "url", "canonical_url", "source_url"),
        "source_url": _text(record, "url", "canonical_url", "source_url"),
        "content_hash": content_hash,
        "title": _text(record, "title") or source_item_id,
        "supporting_passage": _text(record, "evidence_text", "claim_supported", "summary_or_snippet"),
        "discovered_at": discovered_at,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "metadata": {
            "producer": PRODUCER,
            "producer_record_id": _text(record, "source_record_id", "producer_record_id", "care_line_record_id"),
            "care_line_review_status": _text(record, "care_line_review_status", "review_status"),
            "evidence_text_basis": _text(record, "evidence_text_basis"),
        },
    }


def _source_payload(record: Mapping[str, Any], source_id: str) -> dict[str, Any]:
    published_at = _parse_dt(_value(record, "published_at", "source_published_date"))
    retrieved_at = _parse_dt(_value(record, "retrieved_at"))
    discovered_at = retrieved_at or published_at
    if discovered_at is None:
        raise ValueError("eligible records require a source publication or retrieval timestamp")
    url = normalize_url(_text(record, "url", "canonical_url", "source_url")) or _text(record, "url", "canonical_url", "source_url")
    return {
        "source_id": source_id,
        "name": _text(record, "publisher") or _text(record, "source_name") or "Unknown source",
        "publisher": _text(record, "publisher") or "Unknown source",
        "canonical_url": url,
        "source_type": _source_type(record),
        "content_hash": None,
        "discovered_at": discovered_at,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "metadata": {
            "producer": PRODUCER,
            "producer_record_id": _text(record, "source_record_id", "producer_record_id", "care_line_record_id"),
            "producer_source_family": _text(record, "source_family"),
        },
    }


def _healthcare_attributes(record: Mapping[str, Any], event_type: str) -> dict[str, Any]:
    service_raw, service_normalized = _service_line(record)
    attrs: dict[str, Any] = {
        "facility_name_raw": _facility_name(record),
        "service_line": service_raw,
        "service_line_normalized": service_normalized,
        "facility_type": _text(record, "facility_type"),
        "change_direction": _text(record, "change_direction") or _default_change_direction(event_type),
        "previous_status": _text(record, "previous_status"),
        "new_status": _text(record, "new_status"),
        "licensed_beds_before": _value(record, "licensed_beds_before"),
        "licensed_beds_after": _value(record, "licensed_beds_after"),
        "operating_hours_before": _text(record, "operating_hours_before"),
        "operating_hours_after": _text(record, "operating_hours_after"),
        "temporary_or_permanent": _text(record, "temporary_or_permanent", "permanence"),
        "closure_reason": _text(record, "closure_reason"),
        "replacement_provider_named": _text(record, "replacement_provider_named", "replacement_provider"),
        "replacement_service_named": _text(record, "replacement_service_named", "replacement_service"),
        "ownership_change_from": _text(record, "ownership_change_from", "former_owner"),
        "ownership_change_to": _text(record, "ownership_change_to", "new_owner"),
        "effective_date_text": _text(record, "effective_date_text", "effective_date"),
        "affected_population_text": ", ".join(str(item).strip() for item in (_value(record, "affected_groups") or []) if str(item).strip())
        if isinstance(_value(record, "affected_groups"), list)
        else _text(record, "affected_population_text", "affected_groups"),
        "travel_impact_reported": _text(record, "travel_impact_reported"),
        "care_line_classification": _text(record, "pressure_type"),
        "care_line_evidence_level": _text(record, "evidence_level"),
    }
    return {key: value for key, value in attrs.items() if key in HEALTHCARE_ATTRIBUTE_KEYS and value not in (None, "", [], {})}


def _default_change_direction(event_type: str) -> str:
    if event_type in {"facility_reopening", "service_expansion", "service_restoration"}:
        return "expanded_or_restored"
    if event_type in {"ownership_change", "operator_change", "facility_relocation"}:
        return "changed"
    return "reduced"


def _event_status(event_type: str, record: Mapping[str, Any]) -> EventStatus:
    status = _text(record, "event_status").lower()
    if status in {item.value for item in EventStatus}:
        return EventStatus(status)
    if event_type.startswith("planned_"):
        return EventStatus.PLANNED
    if event_type in {"facility_reopening", "service_restoration"}:
        return EventStatus.COMPLETED
    if event_type == "temporary_facility_suspension":
        return EventStatus.ACTIVE
    return EventStatus.ANNOUNCED


def _candidate_payload(record: Mapping[str, Any], candidate_id: str, source_item_id: str, content_hash: str, event_type: str) -> dict[str, Any]:
    published_at = _parse_dt(_value(record, "published_at", "source_published_date"))
    retrieved_at = _parse_dt(_value(record, "retrieved_at"))
    announcement_at = _parse_dt(_value(record, "announcement_date", "published_at", "source_published_date")) or retrieved_at or published_at
    if announcement_at is None:
        raise ValueError("eligible records require an announcement date")
    producer_record_id = _text(record, "source_record_id", "producer_record_id", "care_line_record_id")
    metadata = {
        "producer": PRODUCER,
        "producer_record_id": producer_record_id,
        "event_type": event_type,
        "announcement_date": _date_text(record, "announcement_date", "published_at", "source_published_date"),
        "effective_date": _date_text(record, "effective_date"),
        "event_date_precision": _text(record, "event_date_precision") or ("day" if _date_text(record, "effective_date") else "unknown"),
        "permanence": _text(record, "permanence", "temporary_or_permanent") or "unknown",
        "geographic_scope": _text(record, "location_scope") or "unknown",
        "raw_payload_hash": content_hash,
        "ingestion_adapter_version": ADAPTER_VERSION,
        "healthcare_attributes": _healthcare_attributes(record, event_type),
        "producer_payload_versions": [
            {
                "payload_hash": content_hash,
                "title": _text(record, "title"),
                "summary": _text(record, "pressure_summary", "summary_or_snippet"),
                "announcement_date": _date_text(record, "announcement_date", "published_at", "source_published_date"),
                "effective_date": _date_text(record, "effective_date", "effective_date_text"),
                "service_line": _text(record, "service_line", "affected_service_line"),
                "provider_name": _facility_name(record),
                "source_url": _text(record, "url", "canonical_url", "source_url"),
                "withdrawn": bool(_value(record, "withdrawn")),
                "duplicate_of_producer_record_id": _text(record, "duplicate_of_producer_record_id"),
            }
        ],
    }
    if bool(_value(record, "withdrawn")):
        metadata["shadow_withdrawn"] = True
    duplicate_of = _text(record, "duplicate_of_producer_record_id")
    if duplicate_of:
        metadata["duplicate_of_producer_record_id"] = duplicate_of
    if isinstance(record.get("_care_line_phase5"), Mapping):
        metadata["care_line_phase5"] = dict(record["_care_line_phase5"])
    if isinstance(record.get("_care_line_reviewed_record_contract"), Mapping):
        metadata["care_line_reviewed_record_contract"] = dict(record["_care_line_reviewed_record_contract"])
    return {
        "candidate_id": candidate_id,
        "source_item_id": source_item_id,
        "domain": EventDomain.HEALTHCARE_ACCESS,
        "title": _text(record, "candidate_title", "title") or f"Care Line candidate {producer_record_id}",
        "summary": _text(record, "candidate_summary", "pressure_summary", "summary_or_snippet", "claim_supported"),
        "candidate_status": CandidateStatus.NEEDS_REVIEW,
        "verification_status": "unverified",
        "event_status": _event_status(event_type, record),
        "source_item_ids": [source_item_id],
        "discovered_at": announcement_at,
        "published_at": published_at,
        "metadata": metadata,
    }


def _candidate_id(record: Mapping[str, Any]) -> str:
    producer_record_id = _text(record, "source_record_id", "producer_record_id", "care_line_record_id")
    return _stable_id("candidate", PRODUCER_SLUG, producer_record_id)


def _source_ids(record: Mapping[str, Any]) -> tuple[str, str, str]:
    url = normalize_url(_text(record, "url", "canonical_url", "source_url")) or _text(record, "url", "canonical_url", "source_url")
    producer_record_id = _text(record, "source_record_id", "producer_record_id", "care_line_record_id")
    source_id = _stable_id("source", url, _text(record, "publisher"))
    source_item_id = _stable_id("source_item", PRODUCER_SLUG, producer_record_id, url)
    content_hash = _stable_hash(
        {
            "producer": PRODUCER_SLUG,
            "producer_record_id": producer_record_id,
            "url": url,
            "title": _text(record, "title"),
            "summary_or_snippet": _text(record, "summary_or_snippet", "pressure_summary"),
            "supporting_passage": _text(record, "evidence_text", "claim_supported", "summary_or_snippet"),
            "published_at": _text(record, "published_at", "source_published_date"),
        }
    )
    return source_id, source_item_id, content_hash


def _mention_payloads(record: Mapping[str, Any], candidate_id: str, source_item_id: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    state = _state_from_location(record)
    city = _city_from_location(record)
    country = _text(record, "country_code") or "US"
    base_location = {
        "address_line_1": _text(record, "address", "address_line_1"),
        "address_line_2": _text(record, "address_line_2"),
        "locality": city,
        "region": state,
        "postal_code": _text(record, "postal_code", "zip"),
        "country_code": country,
    }

    def add_org(role: str, raw_name: str, identifiers: dict[str, Any] | None = None) -> None:
        if raw_name:
            payloads.append(
                {
                    "candidate_id": candidate_id,
                    "source_item_id": source_item_id,
                    "entity_kind": "organization",
                    "mention_role": role,
                    "raw_name": raw_name,
                    "raw_address": _text(record, "address", "raw_address"),
                    **base_location,
                    "external_identifiers": {key: value for key, value in (identifiers or {}).items() if value not in (None, "")},
                }
            )

    def add_loc(role: str, raw_name: str, extra: Mapping[str, Any] | None = None) -> None:
        if raw_name:
            merged = dict(base_location)
            merged.update({key: value for key, value in (extra or {}).items() if value not in (None, "")})
            payloads.append(
                {
                    "candidate_id": candidate_id,
                    "source_item_id": source_item_id,
                    "entity_kind": "location",
                    "mention_role": role,
                    "raw_name": raw_name,
                    "raw_address": _text(record, "address", "raw_address"),
                    **merged,
                }
            )

    identifiers = {
        "cms_ccn": _text(record, "cms_identifier", "cms_ccn"),
        "npi": _text(record, "npi"),
        "state_license": _text(record, "state_license_identifier"),
        "care_line_organization_id": _text(record, "care_line_organization_id"),
    }
    facility = _facility_name(record)
    add_org("affected_provider", facility, identifiers)
    add_org("facility", facility, identifiers)
    add_org("operator", _text(record, "operator"))
    add_org("owner", _text(record, "owner"))
    add_org("former_owner", _text(record, "former_owner", "ownership_change_from"))
    add_org("new_owner", _text(record, "new_owner", "ownership_change_to"))
    add_org("parent", _text(record, "parent_organization"))
    add_org("replacement_provider", _text(record, "replacement_provider", "replacement_provider_named"))
    add_org("regulator", _text(record, "regulator"))

    add_loc("event_location", _location_name(record), {"locality": city, "region": state})
    add_loc("facility_address", facility or _location_name(record), {"locality": city, "region": state})
    add_loc("city", city, {"locality": city, "region": state})
    add_loc("county", _text(record, "county"), {"region": state})
    add_loc("state", state, {"region": state})
    add_loc("service_area", _text(record, "service_area"))
    add_loc("replacement_service_location", _text(record, "replacement_service_location"))

    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for payload in payloads:
        key = (
            str(payload["entity_kind"]),
            str(payload["mention_role"]),
            normalize_name(str(payload["raw_name"])),
            str(payload.get("raw_address") or ""),
        )
        unique[key] = payload
    return [unique[key] for key in sorted(unique)]


def _append_candidate_observation(service: UniversalEventService, candidate_id: str, record: Mapping[str, Any], content_hash: str, source_item_id: str) -> bool:
    with service.repository.session_scope() as session:
        row = session.get(CandidateEventRow, candidate_id)
        if row is None:
            return False
        metadata = dict(row.metadata_json or {})
        versions = list(metadata.get("producer_payload_versions") or [])
        changed_metadata = False
        if bool(_value(record, "withdrawn")) and metadata.get("shadow_withdrawn") is not True:
            metadata["shadow_withdrawn"] = True
            changed_metadata = True
        duplicate_of = _text(record, "duplicate_of_producer_record_id")
        if duplicate_of and metadata.get("duplicate_of_producer_record_id") != duplicate_of:
            metadata["duplicate_of_producer_record_id"] = duplicate_of
            changed_metadata = True
        if any(item.get("payload_hash") == content_hash for item in versions if isinstance(item, dict)):
            if source_item_id not in list(row.source_item_ids_json or []):
                row.source_item_ids_json = list(dict.fromkeys([*(row.source_item_ids_json or []), source_item_id]))
                changed_metadata = True
            if changed_metadata:
                row.metadata_json = metadata
            return changed_metadata
        versions.append(
            {
                "payload_hash": content_hash,
                "title": _text(record, "title"),
                "summary": _text(record, "pressure_summary", "summary_or_snippet"),
                "announcement_date": _date_text(record, "announcement_date", "published_at", "source_published_date"),
                "effective_date": _date_text(record, "effective_date", "effective_date_text"),
                "service_line": _text(record, "service_line", "affected_service_line"),
                "provider_name": _facility_name(record),
                "source_url": _text(record, "url", "canonical_url", "source_url"),
                "withdrawn": bool(_value(record, "withdrawn")),
                "duplicate_of_producer_record_id": _text(record, "duplicate_of_producer_record_id"),
            }
        )
        metadata["producer_payload_versions"] = sorted(versions, key=lambda item: str(item.get("payload_hash") or ""))
        metadata["last_shadow_observed_payload_hash"] = content_hash
        row.metadata_json = metadata
        row.source_item_ids_json = list(dict.fromkeys([*(row.source_item_ids_json or []), source_item_id]))
        return True


def _count_rows(service: UniversalEventService) -> dict[str, int]:
    with service.repository.session_scope() as session:
        return {
            "sources": len(session.execute(select(SourceRow)).scalars().all()),
            "source_items": len(session.execute(select(SourceItemRow)).scalars().all()),
            "candidates": len(session.execute(select(CandidateEventRow)).scalars().all()),
            "mentions": len(session.execute(select(EntityMentionRow)).scalars().all()),
            "matches": len(session.execute(select(EntityMatchCandidateRow)).scalars().all()),
            "events": len(session.execute(select(EventRow)).scalars().all()),
        }


def _summarize_matches(matches: list[Any]) -> tuple[int, int, int]:
    if not matches:
        return 0, 0, 1
    class _MatchView:
        def __init__(self, row: Any):
            self.score = float(getattr(row, "match_score", getattr(row, "score", 0.0)) or 0.0)
            self.method = str(getattr(row, "match_method", getattr(row, "method", "")) or "")

    auto = 1 if can_auto_match([_MatchView(row) for row in matches]) else 0
    ambiguous = 1 if len(matches) > 1 and not auto else 0
    unresolved = 0
    return auto, ambiguous, unresolved


def ingest_care_line_shadow(
    records: Iterable[Mapping[str, Any]],
    service: UniversalEventService,
    *,
    check_only: bool = False,
    config: ShadowIngestionConfig = ShadowIngestionConfig(),
) -> dict[str, Any]:
    rows = [dict(record) for record in records]
    before = _count_rows(service) if not check_only else {"sources": 0, "source_items": 0, "candidates": 0, "mentions": 0, "matches": 0, "events": 0}
    seen_ids: set[str] = set()
    report: dict[str, Any] = {
        "run_summary": {
            "adapter_version": ADAPTER_VERSION,
            "producer": PRODUCER,
            "input_record_count": len(rows),
            "eligible_count": 0,
            "excluded_count": 0,
            "created_source_count": 0,
            "created_source_item_count": 0,
            "created_candidate_count": 0,
            "existing_candidate_count": 0,
            "mention_count": 0,
            "match_candidate_count": 0,
            "unresolved_mention_count": 0,
            "ambiguous_mention_count": 0,
            "automatically_matchable_mention_count": 0,
            "error_count": 0,
            "run_status": "ok",
            "error_summaries": [],
            "check_only": check_only,
        },
        "eligible_records": [],
        "excluded_records": [],
        "created_candidates": [],
        "existing_candidates": [],
        "organization_mentions": [],
        "location_mentions": [],
        "automatic_match_candidates": [],
        "ambiguous_matches": [],
        "unresolved_mentions": [],
        "normalization_warnings": [],
        "mapping_warnings": [],
        "errors": [],
    }
    for record in sorted(rows, key=lambda item: _text(item, "source_record_id", "producer_record_id", "care_line_record_id")):
        producer_record_id = _text(record, "source_record_id", "producer_record_id", "care_line_record_id")
        try:
            reason = _exclusion_reason(record, seen_ids)
            if producer_record_id:
                seen_ids.add(producer_record_id)
            if reason:
                report["excluded_records"].append(
                    {
                        "producer_record_id": producer_record_id,
                        "source_url": _text(record, "url", "canonical_url", "source_url"),
                        "reason": reason,
                    }
                )
                continue
            event_type, _ = _event_type(record)
            source_id, source_item_id, content_hash = _source_ids(record)
            candidate_id = _candidate_id(record)
            eligible = {
                "producer_record_id": producer_record_id,
                "candidate_id": candidate_id,
                "event_type": event_type,
                "source_item_id": source_item_id,
            }
            report["eligible_records"].append(eligible)
            if check_only:
                continue
            existing_candidate = service.repository.get_candidate(candidate_id)
            service.create_source(_source_payload(record, source_id))
            service.create_source_item(_source_item_payload(record, source_id, source_item_id, content_hash))
            candidate = service.submit_candidate(_candidate_payload(record, candidate_id, source_item_id, content_hash, event_type))
            changed = _append_candidate_observation(service, candidate.candidate_id, record, content_hash, source_item_id)
            candidate_row = {
                "candidate_id": candidate.candidate_id,
                "producer_record_id": producer_record_id,
                "event_type": event_type,
                "payload_hash": content_hash,
                "metadata_updated": changed,
            }
            if existing_candidate is None:
                report["created_candidates"].append(candidate_row)
            else:
                report["existing_candidates"].append(candidate_row)
            for mention_payload in _mention_payloads(record, candidate.candidate_id, source_item_id):
                mention = service.ingest_entity_mention(mention_payload)
                mention_row = {
                    "mention_id": mention.mention_id,
                    "candidate_id": candidate.candidate_id,
                    "producer_record_id": producer_record_id,
                    "entity_kind": mention.entity_kind,
                    "mention_role": mention.mention_role,
                    "raw_name": mention.raw_name,
                    "locality": mention.locality,
                    "region": mention.region,
                    "external_identifiers": dict(mention.external_identifiers),
                }
                matches = service.generate_match_candidates(mention.mention_id, thresholds=config.resolver_thresholds)
                auto, ambiguous, unresolved = _summarize_matches(matches)
                if mention.entity_kind == "organization":
                    report["organization_mentions"].append(mention_row)
                else:
                    report["location_mentions"].append(mention_row)
                if auto:
                    report["automatic_match_candidates"].append(
                        {
                            "mention_id": mention.mention_id,
                            "match_candidate_id": matches[0].match_candidate_id,
                            "target_id": matches[0].organization_id or matches[0].location_id,
                            "match_method": matches[0].match_method,
                            "resolver_version": RESOLVER_VERSION,
                        }
                    )
                if ambiguous:
                    report["ambiguous_matches"].append(
                        {
                            "mention_id": mention.mention_id,
                            "match_candidate_ids": [match.match_candidate_id for match in matches],
                            "resolver_version": RESOLVER_VERSION,
                        }
                    )
                if unresolved:
                    report["unresolved_mentions"].append(mention_row)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"producer_record_id": producer_record_id, "error": f"{type(exc).__name__}: {exc}"})

    after = _count_rows(service) if not check_only else before
    summary = report["run_summary"]
    summary["eligible_count"] = len(report["eligible_records"])
    summary["excluded_count"] = len(report["excluded_records"])
    summary["created_source_count"] = max(0, after["sources"] - before["sources"])
    summary["created_source_item_count"] = max(0, after["source_items"] - before["source_items"])
    summary["created_candidate_count"] = len(report["created_candidates"])
    summary["existing_candidate_count"] = len(report["existing_candidates"])
    summary["mention_count"] = len(report["organization_mentions"]) + len(report["location_mentions"])
    summary["match_candidate_count"] = max(0, after["matches"] - before["matches"])
    summary["unresolved_mention_count"] = len(report["unresolved_mentions"])
    summary["ambiguous_mention_count"] = len(report["ambiguous_matches"])
    summary["automatically_matchable_mention_count"] = len(report["automatic_match_candidates"])
    summary["error_count"] = len(report["errors"])
    summary["error_summaries"] = [row["error"] for row in report["errors"]]
    summary["run_status"] = "failed" if report["errors"] else "ok"
    report["excluded_records"] = sorted(report["excluded_records"], key=lambda item: (item["reason"], item["producer_record_id"]))
    for key in (
        "eligible_records",
        "created_candidates",
        "existing_candidates",
        "organization_mentions",
        "location_mentions",
        "automatic_match_candidates",
        "ambiguous_matches",
        "unresolved_mentions",
        "errors",
    ):
        report[key] = sorted(report[key], key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return report


def deterministic_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)


def markdown_report(report: Mapping[str, Any]) -> str:
    summary = dict(report.get("run_summary") or {})
    lines = [
        "# Care Line Universal Events Shadow Ingestion",
        "",
        f"- Adapter version: `{summary.get('adapter_version')}`",
        f"- Producer: `{summary.get('producer')}`",
        f"- Run status: `{summary.get('run_status')}`",
        f"- Input records: `{summary.get('input_record_count')}`",
        f"- Eligible records: `{summary.get('eligible_count')}`",
        f"- Excluded records: `{summary.get('excluded_count')}`",
        f"- Created candidates: `{summary.get('created_candidate_count')}`",
        f"- Existing candidates: `{summary.get('existing_candidate_count')}`",
        f"- Mentions: `{summary.get('mention_count')}`",
        f"- Match candidates: `{summary.get('match_candidate_count')}`",
        "",
        "## Exclusions",
    ]
    exclusions = list(report.get("excluded_records") or [])
    if exclusions:
        for row in exclusions:
            lines.append(f"- `{row.get('producer_record_id')}`: `{row.get('reason')}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Candidates"])
    candidates = list(report.get("created_candidates") or []) + list(report.get("existing_candidates") or [])
    if candidates:
        for row in sorted(candidates, key=lambda item: str(item.get("candidate_id") or "")):
            lines.append(f"- `{row.get('candidate_id')}` from `{row.get('producer_record_id')}`: `{row.get('event_type')}`")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _is_under(path: Path, possible_parent: Path) -> bool:
    try:
        path.resolve().relative_to(possible_parent.resolve())
        return True
    except ValueError:
        return False


def _refuse_pages_path(path: Path, root: Path) -> None:
    pages_repo = root / "bluefern-dispatches-pages"
    if pages_repo.exists() and _is_under(path, pages_repo):
        raise ValueError(f"refusing to use nested Pages repository path: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow-ingest Care Line records into Universal Events candidates.")
    parser.add_argument("--database", required=True, help="Explicit temporary or shadow SQLite database path.")
    parser.add_argument("--input", required=True, help="Structured Care Line JSON input.")
    parser.add_argument("--report", required=True, help="JSON report output path.")
    parser.add_argument("--shadow", action="store_true", help="Required. Confirms this is a shadow-only run.")
    parser.add_argument("--check-only", action="store_true", help="Validate and report without database writes.")
    parser.add_argument("--markdown-report", default="", help="Optional Markdown review report path.")
    args = parser.parse_args(argv)

    if not args.shadow:
        print("--shadow is required; this adapter never publishes or writes verified events", file=sys.stderr)
        return 2
    root = Path.cwd()
    database_path = Path(args.database)
    input_path = Path(args.input)
    report_path = Path(args.report)
    try:
        _refuse_pages_path(database_path, root)
        _refuse_pages_path(report_path, root)
        if args.markdown_report:
            _refuse_pages_path(Path(args.markdown_report), root)
        records = _load_records(input_path)
        repo = SQLiteUniversalEventRepository(database_path)
        if not args.check_only:
            repo.initialize_schema()
        service = UniversalEventService(repo)
        report = ingest_care_line_shadow(records, service, check_only=args.check_only)
        repo.close()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(deterministic_json(report) + "\n", encoding="utf-8")
        if args.markdown_report:
            md_path = Path(args.markdown_report)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(markdown_report(report), encoding="utf-8")
        return 0 if report["run_summary"]["run_status"] == "ok" else 1
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
