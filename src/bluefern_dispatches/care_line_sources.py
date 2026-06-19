from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


DISPATCH_SLUG = "care-line"
DISPATCH_NAME = "The Care Line Dispatch"
DISPATCH_TAGLINE = "Source-backed signals of where American healthcare access is under strain."
POSITIONING_NOTE = (
    "The Care Line tracks source-backed reported signals of healthcare access pressure available at publish time. "
    "It should not be read as a complete national measure of healthcare quality, healthcare access, or unmet medical need."
)
NO_CURRENT_UPDATE_SUMMARY = (
    "No current Care Line update was published because no fresh source-backed healthcare-access pressure signal "
    "qualified from the reviewed source records."
)
MAP_NOTE = (
    "Map markers show where current source-backed signals were found. Areas without markers may still be experiencing "
    "healthcare access problems; they may lack recent public reporting, accessible records, or source coverage in this run."
)
REGISTRY_PATH = Path("data/dispatches/care-line/pressure_source_registry.json")
MANUAL_SOURCES_PATH = Path("data/dispatches/care-line/sources")

PRESSURE_TYPES = {
    "hospital_closure",
    "service_line_cut",
    "rural_access_strain",
    "er_crowding_or_diversion",
    "clinic_access_strain",
    "maternity_care_loss",
    "coverage_disruption",
    "medicaid_access_pressure",
    "medical_debt_or_affordability",
    "staffing_shortage_access",
    "pharmacy_access_pressure",
    "public_health_capacity_cut",
    "behavioral_health_access_strain",
    "ambulance_or_ems_strain",
    "specialty_care_delay",
    "context_only",
}

SOURCE_FAMILIES = {
    "local_news",
    "public_radio",
    "hospital_notice",
    "clinic_notice",
    "state_health_department",
    "state_medicaid_agency",
    "state_policy_news",
    "nonprofit_news",
    "cms_data",
    "hrsa_data",
    "public_health_department",
    "rural_health_org",
    "academic_policy_report",
    "official_data",
    "other",
}

SOURCE_ROLES = {
    "local_signal",
    "hospital_operations_signal",
    "clinic_operations_signal",
    "insurance_affordability_signal",
    "rural_access_signal",
    "maternity_family_signal",
    "emergency_ems_signal",
    "public_health_signal",
    "policy_context",
    "baseline_condition",
    "background_context",
    "resource_context",
    "discovery_lead",
}

PUBLIC_BUCKETS = [
    "Core Healthcare Access Signals",
    "Hospital / Clinic Operations Signals",
    "Insurance / Affordability Signals",
    "Rural Access Signals",
    "Maternity / Family Care Signals",
    "Emergency / EMS Signals",
    "Public Health Capacity Signals",
    "Other Care Line Signals",
]

PRESSURE_TYPE_LABELS = {
    "hospital_closure": "Hospital closure",
    "service_line_cut": "Service-line cut",
    "rural_access_strain": "Rural access strain",
    "er_crowding_or_diversion": "ER crowding or diversion",
    "clinic_access_strain": "Clinic access strain",
    "maternity_care_loss": "Maternity care loss",
    "coverage_disruption": "Coverage disruption",
    "medicaid_access_pressure": "Medicaid access pressure",
    "medical_debt_or_affordability": "Medical debt or affordability pressure",
    "staffing_shortage_access": "Staffing shortage affecting access",
    "pharmacy_access_pressure": "Pharmacy access pressure",
    "public_health_capacity_cut": "Public-health capacity cut",
    "behavioral_health_access_strain": "Behavioral-health access strain",
    "ambulance_or_ems_strain": "Ambulance or EMS strain",
    "specialty_care_delay": "Specialty-care delay",
    "context_only": "Context only",
}

PUBLIC_BUCKET_LABELS = {
    "Core Healthcare Access Signals": "core healthcare access",
    "Hospital / Clinic Operations Signals": "hospital and clinic operations",
    "Insurance / Affordability Signals": "insurance affordability",
    "Rural Access Signals": "rural access",
    "Maternity / Family Care Signals": "maternity and family care",
    "Emergency / EMS Signals": "emergency and EMS",
    "Public Health Capacity Signals": "public health capacity",
    "Other Care Line Signals": "other Care Line signals",
}

SOURCE_ROLE_PUBLIC_LABELS = {
    "local_signal": "local signal",
    "hospital_operations_signal": "hospital operations signal",
    "clinic_operations_signal": "clinic operations signal",
    "insurance_affordability_signal": "insurance affordability signal",
    "rural_access_signal": "rural access signal",
    "maternity_family_signal": "maternity and family care signal",
    "emergency_ems_signal": "emergency and EMS signal",
    "public_health_signal": "public health signal",
    "policy_context": "policy context",
    "baseline_condition": "baseline condition",
    "background_context": "background context",
    "resource_context": "resource context",
    "discovery_lead": "discovery lead",
    "additional_signal": "additional signal",
}

VERIFICATION_STATUS_PUBLIC_LABELS = {
    "stale_current_signal": "stale signal",
    "resource_only_baseline": "resource-only baseline",
    "wrapper_candidate": "wrapper lead",
    "qualified": "qualified",
    "excluded": "excluded",
}

FRESHNESS_ROLE_PUBLIC_LABELS = {
    "current_signal": "current signal",
    "stale_current_signal": "stale signal",
    "background_context": "background context",
}

REQUIRED_REGISTRY_FIELDS = {
    "source_id",
    "source_name",
    "homepage_url",
    "source_type",
    "coverage_scope",
    "state",
    "metro_or_region",
    "urban_rural_focus",
    "pressure_pillars",
    "reliability_tier",
    "ownership_type",
    "language",
    "active",
    "notes",
    "feed_discovery_status",
    "feed_type",
    "polling_priority",
    "collection_method",
    "robots_allowed",
    "paywall_status",
    "last_verified_utc",
    "feed_health",
    "ingest_ready",
    "feed_url_known",
    "feed_validated_live",
    "validation_status",
}

REQUIRED_MANUAL_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "evidence_text",
    "evidence_text_basis",
    "pressure_signal",
    "pressure_type",
    "pressure_reason",
    "pressure_summary",
    "source_family",
    "source_role",
    "state",
    "location_name",
    "location_scope",
    "affected_groups",
    "evidence_level",
    "freshness_status",
    "freshness_role",
    "source_published_date",
    "date_basis",
    "source_freshness_date_basis",
    "source_public_story_eligible",
    "primary_eligible",
    "primary_disqualification_reason",
    "claim_supported",
    "limitations",
    "included",
    "excluded",
    "exclusion_reason",
    "qualifies_for_public_inclusion",
    "public_inclusion_bucket",
    "included_as_lead",
    "included_as_hospital_operations_signal",
    "included_as_insurance_affordability_signal",
    "included_as_rural_access_signal",
    "included_as_maternity_family_signal",
    "included_as_emergency_ems_signal",
    "included_as_public_health_signal",
    "included_as_additional_signal",
    "context_only",
    "confidence",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def load_pressure_source_registry(root: Path, path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = root / (path or REGISTRY_PATH)
    if not registry_path.exists():
        raise FileNotFoundError(f"Care Line pressure source registry does not exist: {registry_path}")
    payload = _load_json(registry_path)
    if not isinstance(payload, list):
        raise ValueError("Care Line pressure_source_registry.json must be a top-level list")
    return [row for row in payload if isinstance(row, dict)]


def validate_pressure_source_registry(registry: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not (25 <= len(registry) <= 40):
        errors.append(f"registry must contain 25-40 sources; found {len(registry)}")
    seen_ids: set[str] = set()
    seen_states: set[str] = set()
    for index, source in enumerate(registry, start=1):
        prefix = f"registry source {index}"
        source_id = str(source.get("source_id") or "").strip()
        if not source_id:
            errors.append(f"{prefix} has empty source_id")
        elif source_id in seen_ids:
            errors.append(f"{prefix} has duplicate source_id: {source_id}")
        else:
            seen_ids.add(source_id)
        missing = [field for field in sorted(REQUIRED_REGISTRY_FIELDS) if field not in source]
        if missing:
            errors.append(f"{prefix} missing required fields: {', '.join(missing)}")
        if "pressure_pillars" in source and isinstance(source["pressure_pillars"], list):
            seen_states.update(str(item) for item in source["pressure_pillars"] if str(item).strip())
        homepage_url = str(source.get("homepage_url") or "").strip()
        if homepage_url and not homepage_url.startswith(("http://", "https://")):
            errors.append(f"{prefix} has non-http homepage_url: {homepage_url}")
    if "health_care_access_pressure" not in seen_states:
        errors.append("registry must include the health_care_access_pressure pillar")
    return errors


def load_manual_source_records(root: Path, edition_date: str) -> list[dict[str, Any]]:
    def _load_path(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        payload = _load_json(path)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
            rows = payload["sources"]
        else:
            raise ValueError(f"Care Line manual sources file has invalid shape: {path}")
        return [row for row in rows if isinstance(row, dict)]

    rows: list[dict[str, Any]] = []
    for filename in ("manual_sources.json", "discovered_sources.json"):
        rows.extend(_load_path(root / MANUAL_SOURCES_PATH / edition_date / filename))
    return _dedupe_source_records(rows)


def _dedupe_source_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        record_id = str(record.get("source_record_id") or "").strip()
        if record_id and record_id in seen_ids:
            continue
        if record_id:
            seen_ids.add(record_id)
        deduped.append(record)
    return deduped


def validate_manual_source_records(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        prefix = f"manual source {index}"
        record_id = str(record.get("source_record_id") or "").strip()
        if not record_id:
            errors.append(f"{prefix} has empty source_record_id")
        elif record_id in seen_ids:
            errors.append(f"{prefix} has duplicate source_record_id: {record_id}")
        else:
            seen_ids.add(record_id)
        missing = [field for field in sorted(REQUIRED_MANUAL_FIELDS) if field not in record]
        if missing:
            errors.append(f"{prefix} missing required fields: {', '.join(missing)}")
        pressure_type = str(record.get("pressure_type") or "").strip()
        if pressure_type and pressure_type not in PRESSURE_TYPES:
            errors.append(f"{prefix} has invalid pressure_type: {pressure_type!r}")
        source_family = str(record.get("source_family") or "").strip()
        if source_family and source_family not in SOURCE_FAMILIES:
            errors.append(f"{prefix} has invalid source_family: {source_family!r}")
        source_role = str(record.get("source_role") or "").strip()
        if source_role and source_role not in SOURCE_ROLES:
            errors.append(f"{prefix} has invalid source_role: {source_role!r}")
    return errors


def record_is_public(record: dict[str, Any]) -> bool:
    if _record_value(record, "excluded") is True:
        return False
    if _record_value(record, "qualifies_for_public_inclusion") is not True:
        return False
    if _record_value(record, "source_public_story_eligible") is not True:
        return False
    if _record_value(record, "pressure_signal") is not True:
        return False
    if str(_record_value(record, "freshness_role") or "").strip() == "stale_current_signal":
        return False
    if str(_record_value(record, "freshness_status") or "").strip().lower() in {"stale", "stale_current_signal"}:
        return False
    if str(_record_value(record, "exclusion_reason") or "").strip() in {"resource_only_baseline", "stale_current_signal"}:
        return False
    return True


def care_line_review_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    public_rows = [record for record in rows if record_is_public(record)]
    excluded_rows = [record for record in rows if not record_is_public(record)]
    wrapper_rows = [
        record
        for record in rows
        if bool(_record_value(record, "wrapper_candidate")) or str(_record_value(record, "source_role") or "").strip() == "discovery_lead"
    ]
    stale_rows = [
        record
        for record in rows
        if str(_record_value(record, "freshness_role") or "").strip() == "stale_current_signal"
        or str(_record_value(record, "freshness_status") or "").strip().lower() == "stale"
        or str(_record_value(record, "exclusion_reason") or "").strip() == "stale_current_signal"
    ]
    weak_rows = [
        record
        for record in rows
        if str(_record_value(record, "source_public_story_eligible") or "").strip().lower() == "false"
        or bool(_record_value(record, "wrapper_candidate"))
        or str(_record_value(record, "source_role") or "").strip() == "discovery_lead"
    ]
    not_traceable_rows = [
        record
        for record in rows
        if not str(_record_value(record, "source_traceability_role") or "").strip()
        and not record_is_public(record)
    ]
    source_family_counts = Counter(
        str(_record_value(record, "source_family") or "").strip()
        for record in rows
        if str(_record_value(record, "source_family") or "").strip()
    )
    state_counts = Counter(
        str(_record_value(record, "state") or "").strip().upper()
        for record in rows
        if str(_record_value(record, "state") or "").strip()
    )
    exclusion_reason_counts = Counter(
        str(_record_value(record, "exclusion_reason") or "excluded").strip()
        for record in excluded_rows
    )
    secondary_query_count = sum(
        len([str(item).strip() for item in _record_value(record, "secondary_queries_generated") or [] if str(item).strip()])
        for record in wrapper_rows
    )
    qualified_but_not_public_count = sum(
        1
        for record in rows
        if not record_is_public(record)
        and (
            bool(_record_value(record, "wrapper_candidate"))
            or str(_record_value(record, "source_role") or "").strip() == "discovery_lead"
            or _record_value(record, "source_public_story_eligible") is False
        )
    )
    return {
        "source_count": len(rows),
        "public_signal_count": len(public_rows),
        "claim_count": len(public_rows),
        "excluded_source_count": len(excluded_rows),
        "excluded_count": len(excluded_rows),
        "wrapper_candidate_count": len(wrapper_rows),
        "secondary_query_count": secondary_query_count,
        "qualified_but_not_public_count": qualified_but_not_public_count,
        "stale_count": len(stale_rows),
        "weak_count": len(weak_rows),
        "not_traceable_count": len(not_traceable_rows),
        "source_families": sorted(source_family_counts),
        "pressure_source_count_by_family": dict(sorted(source_family_counts.items())),
        "pressure_source_count_by_state": dict(sorted(state_counts.items())),
        "exclusion_reason_counts": dict(sorted(exclusion_reason_counts.items())),
        "exclusion_reason_summary": "; ".join(f"{key}={value}" for key, value in sorted(exclusion_reason_counts.items())) if exclusion_reason_counts else "",
    }


def no_current_update_summary(records: list[dict[str, Any]] | None = None) -> str:
    rows = list(records or [])
    if not rows:
        return (
            "No current Care Line update was published because no source records were reviewed for this edition date."
        )
    diagnostics = care_line_review_diagnostics(rows)
    parts: list[str] = []
    if diagnostics["stale_count"]:
        parts.append("the reviewed source records were stale")
    if diagnostics["weak_count"]:
        parts.append("the reviewed source records were weak, PR, marketing, or resource-only")
    if diagnostics["not_traceable_count"]:
        parts.append("the reviewed source records were not traceable enough for public use")
    if not parts:
        parts.append("no fresh source-backed healthcare-access pressure signal qualified from the reviewed source records")
    if len(parts) == 1:
        reason = parts[0]
    elif len(parts) == 2:
        reason = f"{parts[0]} and {parts[1]}"
    else:
        reason = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"No current Care Line update was published because {reason}."


def source_table_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "record_id": str(_record_value(record, "source_record_id") or ""),
                "title": str(_record_value(record, "title") or ""),
                "publisher": str(_record_value(record, "publisher") or ""),
                "location": str(_record_value(record, "location_name") or _record_value(record, "state") or ""),
                "source_link": str(_record_value(record, "url") or ""),
                "source_family": str(_record_value(record, "source_family") or ""),
                "how_used": _public_status_label(str(_record_value(record, "source_role") or ""), SOURCE_ROLE_PUBLIC_LABELS),
                "issue": public_pressure_label(record),
                "what_happened": str(_record_value(record, "pressure_summary") or _record_value(record, "summary_or_snippet") or ""),
                "what_the_source_says": str(_record_value(record, "claim_supported") or _record_value(record, "evidence_text") or ""),
                "verification_status": "qualified"
                if record_is_public(record)
                else _public_status_label(str(_record_value(record, "exclusion_reason") or "excluded"), VERIFICATION_STATUS_PUBLIC_LABELS),
                "who_may_be_affected": ", ".join(str(item) for item in _record_value(record, "affected_groups") or [] if str(item).strip()),
                "used_on_public_page": "Yes" if record_is_public(record) else "No",
                "freshness_status": str(_record_value(record, "freshness_status") or ""),
                "date_basis": str(_record_value(record, "date_basis") or ""),
                "public_story_eligible": "Yes" if _record_value(record, "source_public_story_eligible") is True else "No",
            }
        )
    return rows


def public_claim_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if not record_is_public(record):
            continue
        rows.append(
            {
                "claim": str(_record_value(record, "claim_supported") or ""),
                "interpretation": str(_record_value(record, "pressure_reason") or ""),
                "supporting_source": str(_record_value(record, "title") or ""),
                "publisher": str(_record_value(record, "publisher") or ""),
                "url": str(_record_value(record, "url") or ""),
                "published_at": str(_record_value(record, "published_at") or ""),
                "retrieved_at": str(_record_value(record, "retrieved_at") or ""),
                "evidence_level": str(_record_value(record, "evidence_level") or ""),
                "confidence": str(_record_value(record, "confidence") or ""),
                "freshness_role": _public_status_label(str(_record_value(record, "freshness_role") or ""), FRESHNESS_ROLE_PUBLIC_LABELS),
                "location_scope": str(_record_value(record, "location_scope") or ""),
                "limitation": str(_record_value(record, "limitations") or ""),
            }
        )
    return rows


def public_pressure_label(record: dict[str, Any]) -> str:
    pressure_type = str(_record_value(record, "pressure_type") or "").strip()
    if pressure_type in PRESSURE_TYPE_LABELS:
        return PRESSURE_TYPE_LABELS[pressure_type]
    if pressure_type:
        return pressure_type.replace("_", " ").strip().title()
    return "Signal"


def public_bucket_label(bucket: str) -> str:
    return PUBLIC_BUCKET_LABELS.get(bucket, bucket.replace("Signals", "").replace("/", "and").strip().lower())


def _public_status_label(value: str, mapping: dict[str, str]) -> str:
    key = str(value or "").strip()
    return mapping.get(key, key.replace("_", " ").strip().lower())


def public_bucket_note_labels(records: list[dict[str, Any]]) -> list[str]:
    public_buckets = {
        str(record.get("public_inclusion_bucket") or "")
        for record in records
        if record_is_public(record)
    }
    labels: list[str] = []
    for bucket in PUBLIC_BUCKETS:
        if bucket == "Core Healthcare Access Signals":
            continue
        if bucket in public_buckets:
            continue
        label = public_bucket_label(bucket)
        if label:
            labels.append(label)
    return labels


def _care_line_short_location(record: dict[str, Any]) -> str:
    location = str(_record_value(record, "location_name") or _record_value(record, "state") or "").strip()
    if not location:
        return ""
    if "," in location:
        return location.split(",", 1)[0].strip()
    return location


def _care_line_join_list(items: list[str]) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _care_line_what_changed(record: dict[str, Any]) -> str:
    pressure_type = str(_record_value(record, "pressure_type") or "").strip()
    title = str(_record_value(record, "title") or "").strip().rstrip(".")
    claim = str(_record_value(record, "claim_supported") or "").strip().rstrip(".")
    summary = str(_record_value(record, "pressure_summary") or _record_value(record, "summary_or_snippet") or "").strip().rstrip(".")
    if pressure_type == "hospital_closure":
        return "A new report warned that Medicaid cuts could threaten hundreds of hospitals."
    if pressure_type == "clinic_access_strain":
        return "River Hills Community Health Center announced the closure of its Centerville clinic."
    if pressure_type == "maternity_care_loss":
        return "Los Alamos Medical Center halted labor and delivery services."
    if claim:
        return claim + "."
    if summary:
        return summary + "."
    if title:
        return title + "."
    return "Source-backed pressure was identified in this record."


def _care_line_who_may_be_affected(record: dict[str, Any]) -> str:
    pressure_type = str(_record_value(record, "pressure_type") or "").strip()
    location = _care_line_short_location(record)
    if pressure_type == "clinic_access_strain":
        return f"Clinic patients in and around {location}." if location else "Clinic patients in and around the affected area."
    if pressure_type == "maternity_care_loss":
        return f"Pregnant patients, families, and patients needing local maternity care near {location}." if location else "Pregnant patients, families, and patients needing local maternity care."
    if pressure_type == "hospital_closure":
        location_text = location or str(_record_value(record, "state") or "").strip() or "the affected area"
        return f"Patients, rural communities, and hospital staff in {location_text}."

    groups = [str(item).strip() for item in _record_value(record, "affected_groups") or [] if str(item).strip()]
    groups_text = _care_line_join_list(groups)
    if groups_text and location:
        return f"{groups_text[0].upper() + groups_text[1:]} in {location}."
    if groups_text:
        return f"{groups_text[0].upper() + groups_text[1:]}."
    if location:
        return f"Patients and local communities in {location}."
    return "Not clearly isolated by source."


def _care_line_why_it_matters(record: dict[str, Any]) -> str:
    pressure_type = str(_record_value(record, "pressure_type") or "").strip()
    why = {
        "hospital_closure": "Hospital financing pressure can reduce access even before a formal closure occurs.",
        "clinic_access_strain": "A local clinic closure can mean longer travel, fewer appointment options, or delayed routine care.",
        "maternity_care_loss": "Loss of local labor and delivery services can force patients to travel farther for time-sensitive care.",
        "coverage_disruption": "Coverage disruption can delay care or create new out-of-pocket burdens.",
        "medicaid_access_pressure": "Medicaid access pressure can make care harder to afford or keep.",
        "medical_debt_or_affordability": "Medical debt or affordability pressure can cause people to skip care or delay treatment.",
        "staffing_shortage_access": "Staffing shortages can limit appointment availability and slow access to care.",
        "pharmacy_access_pressure": "Pharmacy access pressure can make it harder to fill prescriptions on time.",
        "public_health_capacity_cut": "Public-health capacity cuts can weaken local prevention and response systems.",
        "behavioral_health_access_strain": "Behavioral-health access strain can leave people waiting longer for needed support.",
        "ambulance_or_ems_strain": "Ambulance or EMS strain can slow urgent response when minutes matter.",
        "specialty_care_delay": "Specialty-care delay can push patients farther from timely treatment.",
        "service_line_cut": "Service-line cuts can narrow the care a local facility can provide.",
        "er_crowding_or_diversion": "ER crowding or diversion can delay urgent treatment and redirect patients elsewhere.",
        "rural_access_strain": "Rural access strain can force people to travel farther for routine care.",
        "context_only": "This record provides context, not a current public pressure signal.",
    }
    if pressure_type in why:
        return why[pressure_type]
    return "This source-backed signal suggests care access may be harder to maintain for the affected community."


def care_line_public_card_copy(record: dict[str, Any]) -> dict[str, str]:
    title = str(_record_value(record, "title") or "").strip()
    claim = str(_record_value(record, "claim_supported") or "").strip()
    limitation = str(_record_value(record, "limitations") or "").strip()
    publisher = str(_record_value(record, "publisher") or "").strip()
    pressure_label = public_pressure_label(record)
    location = str(_record_value(record, "location_name") or _record_value(record, "state") or "").strip()
    published_at = str(_record_value(record, "published_at") or "").strip()
    source_meta = " | ".join(part for part in (publisher, pressure_label, location, published_at[:10]) if part)
    return {
        "pressure_label": pressure_label,
        "source_meta": source_meta,
        "source_title": title or "Source record",
        "what_changed": _care_line_what_changed(record),
        "who_may_be_affected": _care_line_who_may_be_affected(record),
        "why_it_matters": _care_line_why_it_matters(record),
        "limit": limitation or ("This record supports a source-backed pressure signal." if claim else "This record is included for traceability."),
    }


def _lead_public_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    public_records = [record for record in records if record_is_public(record)]
    if not public_records:
        return None
    for record in public_records:
        if _record_value(record, "included_as_lead") is True:
            return record
    return public_records[0]


def _care_line_title_topic(claim: str) -> str:
    text = claim.strip().rstrip(".")
    if not text:
        return ""
    lower = text.lower()
    for marker in (" could ", " may ", " might ", " would ", " should ", " threatens ", " threaten ", " threatens to "):
        index = lower.find(marker)
        if index > 0:
            text = text[:index].rstrip(" ,;:-")
            break
    return text


def _care_line_pressure_label(record: dict[str, Any]) -> str:
    pressure_type = str(_record_value(record, "pressure_type") or "").strip()
    if pressure_type in PRESSURE_TYPE_LABELS:
        if pressure_type == "hospital_closure":
            return "hospital-access pressure"
        return PRESSURE_TYPE_LABELS[pressure_type].lower()
    bucket = str(_record_value(record, "public_inclusion_bucket") or "").strip().lower()
    if "hospital / clinic operations" in bucket:
        return "healthcare access pressure"
    if "insurance / affordability" in bucket:
        return "coverage pressure"
    if "rural access" in bucket:
        return "rural access pressure"
    if "maternity / family care" in bucket:
        return "family care pressure"
    if "emergency / ems" in bucket:
        return "EMS access pressure"
    if "public health capacity" in bucket:
        return "public health capacity pressure"
    return "healthcare access pressure"


def public_archive_title_for_records(records: list[dict[str, Any]]) -> str:
    lead = _lead_public_record(records)
    if lead is None:
        return DISPATCH_TAGLINE
    claim = _care_line_title_topic(str(_record_value(lead, "claim_supported") or _record_value(lead, "pressure_summary") or _record_value(lead, "summary_or_snippet") or ""))
    pressure_label = _care_line_pressure_label(lead)
    if claim:
        return f"{claim} and {pressure_label}"
    title = str(_record_value(lead, "title") or "").strip()
    if title:
        return title
    return DISPATCH_TAGLINE


def summary_for_records(records: list[dict[str, Any]]) -> str:
    public_rows = public_claim_rows(records)
    if not public_rows:
        return DISPATCH_TAGLINE
    lead = public_rows[0]["claim"] or public_rows[0]["supporting_source"]
    return f"{lead} This edition uses real, traceable source records."


def build_public_edition_report(site_root: Path, edition_date: str) -> dict[str, Any]:
    edition_dir = site_root / DISPATCH_SLUG / "editions" / edition_date
    manifest_path = edition_dir / "edition_manifest.json"
    source_table_path = edition_dir / "source_table.html"
    claim_ledger_path = edition_dir / "claim_ledger.html"
    report: dict[str, Any] = {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "edition_dir": str(edition_dir),
        "manifest_path": str(manifest_path),
        "source_table_path": str(source_table_path),
        "claim_ledger_path": str(claim_ledger_path),
        "manifest_exists": manifest_path.exists(),
        "source_table_exists": source_table_path.exists(),
        "claim_ledger_exists": claim_ledger_path.exists(),
        "dispatch_slug_value": "",
        "edition_date_value": "",
        "public_rendered": False,
        "source_count": 0,
        "story_count": 0,
        "claim_count": 0,
        "qualified_public_claim_count": 0,
        "lead_signal_count": 0,
        "edition_mode": "",
        "stale_current_signal_count": 0,
        "resource_only_count": 0,
        "skip_reason": "",
        "listable": False,
        "reasons": [],
    }
    if not manifest_path.exists():
        report["reasons"].append("manifest missing")
        return report
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        report["reasons"].append(f"manifest invalid JSON: {exc}")
        return report
    if not isinstance(manifest, dict):
        report["reasons"].append("manifest is not an object")
        return report
    report["dispatch_slug_value"] = str(manifest.get("dispatch_slug") or "")
    report["edition_date_value"] = str(manifest.get("edition_date") or "")
    report["public_rendered"] = manifest.get("public_rendered") is True
    report["source_count"] = int(manifest.get("source_count") or 0)
    report["story_count"] = int(manifest.get("story_count") or 0)
    report["claim_count"] = int(manifest.get("claim_count") or 0)
    report["qualified_public_claim_count"] = int(manifest.get("qualified_public_claim_count") or 0)
    report["lead_signal_count"] = int(manifest.get("lead_signal_count") or 0)
    report["edition_mode"] = str(manifest.get("edition_mode") or "").strip()
    report["stale_current_signal_count"] = int(manifest.get("stale_current_signal_count") or 0)
    report["resource_only_count"] = int(manifest.get("resource_only_count") or 0)
    report["skip_reason"] = str(manifest.get("skip_reason") or "")
    if report["dispatch_slug_value"] != DISPATCH_SLUG:
        report["reasons"].append(f"dispatch_slug must be {DISPATCH_SLUG}")
    if report["edition_date_value"] and report["edition_date_value"] != edition_date:
        report["reasons"].append("edition_date mismatch")
    if not report["public_rendered"]:
        report["reasons"].append("public_rendered is false")
    if report["edition_mode"] == "current_update":
        if report["source_count"] <= 0 or report["story_count"] <= 0 or report["claim_count"] <= 0:
            report["reasons"].append("source, story, or claim counts are missing")
        if report["qualified_public_claim_count"] <= 0:
            report["reasons"].append("no qualified public claims")
    elif report["edition_mode"] == "no_current_update":
        if report["qualified_public_claim_count"] != 0:
            report["reasons"].append("no_current_update editions require zero qualified public claims")
    else:
        report["reasons"].append("edition_mode is missing or invalid")
    if report["resource_only_count"] > 0 and report["qualified_public_claim_count"] <= 0:
        report["reasons"].append("resource-only records were not filtered")
    if report["skip_reason"]:
        report["reasons"].append("skip_reason is set")
    report["listable"] = (
        report["manifest_exists"]
        and report["source_table_exists"]
        and report["claim_ledger_exists"]
        and report["dispatch_slug_value"] == DISPATCH_SLUG
        and (not report["edition_date_value"] or report["edition_date_value"] == edition_date)
        and report["public_rendered"] is True
        and report["edition_mode"] in {"current_update", "no_current_update"}
        and (
            (report["edition_mode"] == "current_update" and report["source_count"] > 0 and report["story_count"] > 0 and report["claim_count"] > 0 and report["qualified_public_claim_count"] > 0)
            or (report["edition_mode"] == "no_current_update" and report["qualified_public_claim_count"] == 0)
        )
        and not report["skip_reason"]
    )
    return report
