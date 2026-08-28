from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Literal, Mapping

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
JURISDICTION_TYPES = {"STATE", "FEDERAL_DISTRICT", "TERRITORY"}
GEOGRAPHIC_SCOPE_VALUES = {
    "facility",
    "locality",
    "county_equivalent",
    "multi_county",
    "service_region",
    "jurisdiction_wide",
    "multi_jurisdiction",
    "national",
    "tribal_service_area",
}
ACCESS_CONSEQUENCE_VALUES = {
    "LOSS_OF_LOCAL_ACCESS",
    "LONGER_TRAVEL_DISTANCE",
    "REDUCED_SERVICE_AVAILABILITY",
    "REDUCED_OPERATING_HOURS",
    "REDUCED_BED_OR_APPOINTMENT_CAPACITY",
    "EMERGENCY_DIVERSION",
    "DELAYED_CARE_RISK",
    "TRANSFER_DEPENDENCE",
    "WORKFORCE_RELATED_RESTRICTION",
    "SUBSTITUTE_SERVICE_OFFERED",
    "NO_CONFIRMED_ACCESS_CONSEQUENCE",
}
VERIFICATION_STATE_VALUES = {
    "DISCOVERED",
    "SOURCE_VERIFIED",
    "CORROBORATED",
    "AUTHORITY_CONFIRMED",
    "DISPUTED",
    "INSUFFICIENT_EVIDENCE",
}
WORKFLOW_STATE_VALUES = {
    "NEW",
    "NEEDS_REVIEW",
    "APPROVED",
    "EXCLUDED",
    "DUPLICATE",
    "SUPERSEDED",
    "PUBLISHED",
}
DATE_PRECISION_VALUES = {"day", "month", "unknown"}
DATE_KIND_VALUES = {"source_publication", "announcement", "effective", "observed", "review", "publication"}
CARE_LINE_LIFECYCLE_STATUSES = {
    "ANNOUNCED",
    "PENDING_EFFECTIVE_DATE",
    "EFFECTIVE",
    "DELAYED",
    "CANCELLED",
    "RESTORED",
    "SUPERSEDED",
}
CARE_LINE_REVIEWED_LIFECYCLE_STATUS = Literal[
    "",
    "ANNOUNCED",
    "PENDING_EFFECTIVE_DATE",
    "EFFECTIVE",
    "DELAYED",
    "CANCELLED",
    "RESTORED",
    "SUPERSEDED",
]
CARE_LINE_REVIEWED_FOLLOW_UP_STATUS = Literal[
    "",
    "pending",
    "effective_date_reached",
    "post_effective_follow_up",
]
EFFECTIVE_DATE_FOLLOW_UP_LOOKBACK_DAYS = 14
EFFECTIVE_DATE_FOLLOW_UP_LOOKAHEAD_DAYS = 7
AUTHORITY_LEVEL_VALUES = {
    "primary",
    "official",
    "sector",
    "secondary",
    "reviewed",
    "unknown",
}
RURALITY_VALUES = {"rural", "urban", "frontier", "mixed", "unknown"}

LEGACY_TO_CANONICAL_EVENT_TYPE = {
    "facility_closure": "FACILITY_CLOSURE",
    "planned_facility_closure": "FACILITY_CLOSURE",
    "temporary_facility_suspension": "TEMPORARY_FACILITY_CLOSURE",
    "facility_reopening": "REOPENING",
    "service_closure": "SERVICE_LINE_CLOSURE",
    "service_suspension": "SERVICE_SUSPENSION",
    "service_reduction": "REDUCED_CAPACITY",
    "hours_reduction": "REDUCED_HOURS",
    "capacity_reduction": "REDUCED_CAPACITY",
    "ownership_change": "OWNERSHIP_TRANSITION",
    "operator_change": "OWNERSHIP_TRANSITION",
    "facility_relocation": "RELOCATION",
    "facility_conversion": "CONSOLIDATION",
    "service_expansion": "SERVICE_RESTORATION",
    "service_restoration": "SERVICE_RESTORATION",
    "bankruptcy_service_impact": "BANKRUPTCY_RELATED_SERVICE_LOSS",
}
CANONICAL_TO_LEGACY_EVENT_TYPE = {
    "FACILITY_CLOSURE": "facility_closure",
    "TEMPORARY_FACILITY_CLOSURE": "temporary_facility_suspension",
    "SERVICE_LINE_CLOSURE": "service_closure",
    "SERVICE_SUSPENSION": "service_suspension",
    "REDUCED_HOURS": "hours_reduction",
    "REDUCED_CAPACITY": "capacity_reduction",
    "STAFFING_RESTRICTION": "service_reduction",
    "DIVERSION": "service_reduction",
    "RELOCATION": "facility_relocation",
    "CONSOLIDATION": "facility_conversion",
    "BANKRUPTCY_RELATED_SERVICE_LOSS": "bankruptcy_service_impact",
    "OWNERSHIP_TRANSITION": "ownership_change",
    "ACCESS_RESTRICTION": "service_reduction",
    "REOPENING": "facility_reopening",
    "SERVICE_RESTORATION": "service_restoration",
}
EVENT_TYPE_ALIASES = {
    "facility closure": "FACILITY_CLOSURE",
    "full facility closure": "FACILITY_CLOSURE",
    "planned facility closure": "FACILITY_CLOSURE",
    "temporary facility closure": "TEMPORARY_FACILITY_CLOSURE",
    "temporary suspension": "TEMPORARY_FACILITY_CLOSURE",
    "service line closure": "SERVICE_LINE_CLOSURE",
    "service closure": "SERVICE_LINE_CLOSURE",
    "service suspension": "SERVICE_SUSPENSION",
    "reduced hours": "REDUCED_HOURS",
    "reduced capacity": "REDUCED_CAPACITY",
    "staffing restriction": "STAFFING_RESTRICTION",
    "diversion": "DIVERSION",
    "relocation": "RELOCATION",
    "consolidation": "CONSOLIDATION",
    "bankruptcy related service loss": "BANKRUPTCY_RELATED_SERVICE_LOSS",
    "ownership transition": "OWNERSHIP_TRANSITION",
    "access restriction": "ACCESS_RESTRICTION",
    "reopening": "REOPENING",
    "service restoration": "SERVICE_RESTORATION",
}
EVENT_TYPE_DEFINITIONS = {
    "FACILITY_CLOSURE": "A full closure of a healthcare facility or campus with evidence of access loss.",
    "TEMPORARY_FACILITY_CLOSURE": "A temporary facility shutdown or suspension with a stated reopening or temporary basis.",
    "SERVICE_LINE_CLOSURE": "A permanent end to a specific service line while the facility may remain open.",
    "SERVICE_SUSPENSION": "A temporary halt of a specific service line.",
    "REDUCED_HOURS": "A reduction in operating hours without evidence of full closure.",
    "REDUCED_CAPACITY": "A reduction in beds, appointment capacity, staffing-supported throughput, or comparable service availability.",
    "STAFFING_RESTRICTION": "A staffing-driven operational restriction that limits access.",
    "DIVERSION": "An emergency or patient diversion away from the usual point of care.",
    "RELOCATION": "A move of a facility or service to another location; not itself a closure unless evidence says access is lost.",
    "CONSOLIDATION": "A merger or conversion that reduces distinct access points or service availability.",
    "BANKRUPTCY_RELATED_SERVICE_LOSS": "A service loss directly tied to bankruptcy, receivership, or similar financial failure.",
    "OWNERSHIP_TRANSITION": "An ownership or operator change with an evidenced access consequence.",
    "ACCESS_RESTRICTION": "An explicit access limitation not better captured by a more specific event type.",
    "REOPENING": "A facility reopening after a prior closure or temporary shutdown.",
    "SERVICE_RESTORATION": "A service restoration or extension after a prior loss, suspension, or threatened disruption.",
}

LEGACY_TO_CANONICAL_SERVICE_LINE = {
    "emergency_care": "EMERGENCY",
    "labor_and_delivery": "LABOR_AND_DELIVERY",
    "maternity": "MATERNITY",
    "inpatient_care": "INPATIENT",
    "behavioral_health": "BEHAVIORAL_HEALTH",
    "psychiatric_care": "BEHAVIORAL_HEALTH",
    "pediatrics": "PEDIATRICS",
    "dialysis": "DIALYSIS",
    "oncology": "ONCOLOGY",
    "primary_care": "PRIMARY_CARE",
    "urgent_care": "URGENT_CARE",
    "surgery": "SURGERY",
    "rehabilitation": "REHABILITATION",
    "skilled_nursing": "SKILLED_NURSING",
    "pharmacy": "PHARMACY",
    "ambulance_ems": "AMBULANCE_EMS",
    "specialty_care": "SPECIALTY_CARE",
    "other": "MULTIPLE_SERVICES",
    "unknown": "ENTIRE_FACILITY",
}
CANONICAL_TO_LEGACY_SERVICE_LINE = {
    "EMERGENCY": "emergency_care",
    "MATERNITY": "maternity",
    "LABOR_AND_DELIVERY": "labor_and_delivery",
    "INPATIENT": "inpatient_care",
    "OUTPATIENT": "other",
    "PRIMARY_CARE": "primary_care",
    "URGENT_CARE": "urgent_care",
    "BEHAVIORAL_HEALTH": "behavioral_health",
    "SUBSTANCE_USE_TREATMENT": "behavioral_health",
    "PEDIATRICS": "pediatrics",
    "ONCOLOGY": "oncology",
    "DIALYSIS": "dialysis",
    "PHARMACY": "pharmacy",
    "DENTAL": "other",
    "IMAGING": "other",
    "LABORATORY": "other",
    "SURGERY": "surgery",
    "REHABILITATION": "rehabilitation",
    "HOME_HEALTH": "other",
    "HOSPICE": "other",
    "AMBULANCE_EMS": "ambulance_ems",
    "LONG_TERM_CARE": "other",
    "SKILLED_NURSING": "skilled_nursing",
    "TRIBAL_HEALTH": "other",
    "VETERANS_HEALTH": "other",
    "SPECIALTY_CARE": "specialty_care",
    "MULTIPLE_SERVICES": "other",
    "ENTIRE_FACILITY": "unknown",
}
SERVICE_LINE_ALIASES = {
    "er": "EMERGENCY",
    "ed": "EMERGENCY",
    "emergency": "EMERGENCY",
    "emergency care": "EMERGENCY",
    "emergency department": "EMERGENCY",
    "labor & delivery": "LABOR_AND_DELIVERY",
    "labor and delivery": "LABOR_AND_DELIVERY",
    "l&d": "LABOR_AND_DELIVERY",
    "mental health": "BEHAVIORAL_HEALTH",
    "behavioral health": "BEHAVIORAL_HEALTH",
    "urgent care": "URGENT_CARE",
    "primary care": "PRIMARY_CARE",
    "specialty care": "SPECIALTY_CARE",
    "pediatrics": "PEDIATRICS",
    "dialysis": "DIALYSIS",
    "oncology": "ONCOLOGY",
    "pharmacy": "PHARMACY",
    "rehabilitation": "REHABILITATION",
    "surgery": "SURGERY",
    "skilled nursing": "SKILLED_NURSING",
    "substance use treatment": "SUBSTANCE_USE_TREATMENT",
    "substance-use treatment": "SUBSTANCE_USE_TREATMENT",
    "ems": "AMBULANCE_EMS",
    "ambulance": "AMBULANCE_EMS",
    "ambulance ems": "AMBULANCE_EMS",
    "entire facility": "ENTIRE_FACILITY",
}
SERVICE_LINE_DEFINITIONS = {
    "EMERGENCY": "Emergency department or equivalent emergency care services.",
    "MATERNITY": "General maternity services not limited to delivery itself.",
    "LABOR_AND_DELIVERY": "Labor, delivery, and birth-unit services.",
    "INPATIENT": "General inpatient hospital services.",
    "OUTPATIENT": "General outpatient services.",
    "PRIMARY_CARE": "Primary care or family medicine services.",
    "URGENT_CARE": "Urgent care services.",
    "BEHAVIORAL_HEALTH": "Mental or behavioral healthcare services excluding clearly separate substance-use treatment.",
    "SUBSTANCE_USE_TREATMENT": "Substance-use treatment and recovery services.",
    "PEDIATRICS": "Pediatric services.",
    "ONCOLOGY": "Cancer diagnosis or treatment services.",
    "DIALYSIS": "Dialysis services.",
    "PHARMACY": "Pharmacy dispensing services.",
    "DENTAL": "Dental services.",
    "IMAGING": "Diagnostic imaging services.",
    "LABORATORY": "Laboratory testing services.",
    "SURGERY": "Surgical services.",
    "REHABILITATION": "Rehabilitation services.",
    "HOME_HEALTH": "Home health services.",
    "HOSPICE": "Hospice services.",
    "AMBULANCE_EMS": "Ambulance and emergency medical services.",
    "LONG_TERM_CARE": "Long-term care services.",
    "SKILLED_NURSING": "Skilled nursing services.",
    "TRIBAL_HEALTH": "Tribal health services.",
    "VETERANS_HEALTH": "Veterans health services.",
    "SPECIALTY_CARE": "Specialty care not otherwise classified.",
    "MULTIPLE_SERVICES": "Multiple distinct service lines are affected.",
    "ENTIRE_FACILITY": "The entire facility is affected rather than a named service line.",
}

LEGACY_TO_CANONICAL_SCOPE = {
    "city": "LOCALITY",
    "locality": "LOCALITY",
    "county": "COUNTY_EQUIVALENT",
    "county_equivalent": "COUNTY_EQUIVALENT",
    "multi_county": "MULTI_COUNTY",
    "region": "SERVICE_REGION",
    "service_region": "SERVICE_REGION",
    "statewide": "JURISDICTION_WIDE",
    "jurisdiction_wide": "JURISDICTION_WIDE",
    "multi_jurisdiction": "MULTI_JURISDICTION",
    "national": "NATIONAL",
    "facility": "FACILITY",
    "tribal_service_area": "TRIBAL_SERVICE_AREA",
}
CANONICAL_TO_LEGACY_SCOPE = {
    "FACILITY": "facility",
    "LOCALITY": "city",
    "COUNTY_EQUIVALENT": "county_equivalent",
    "MULTI_COUNTY": "multi_county",
    "SERVICE_REGION": "service_region",
    "JURISDICTION_WIDE": "statewide",
    "MULTI_JURISDICTION": "multi_jurisdiction",
    "NATIONAL": "national",
    "TRIBAL_SERVICE_AREA": "tribal_service_area",
}

JURISDICTION_ROWS = (
    ("AL", "Alabama", "STATE", ("AL",)),
    ("AK", "Alaska", "STATE", ("AK",)),
    ("AZ", "Arizona", "STATE", ("AZ",)),
    ("AR", "Arkansas", "STATE", ("AR",)),
    ("CA", "California", "STATE", ("CA",)),
    ("CO", "Colorado", "STATE", ("CO",)),
    ("CT", "Connecticut", "STATE", ("CT",)),
    ("DE", "Delaware", "STATE", ("DE",)),
    ("FL", "Florida", "STATE", ("FL",)),
    ("GA", "Georgia", "STATE", ("GA",)),
    ("HI", "Hawaii", "STATE", ("HI",)),
    ("ID", "Idaho", "STATE", ("ID",)),
    ("IL", "Illinois", "STATE", ("IL",)),
    ("IN", "Indiana", "STATE", ("IN",)),
    ("IA", "Iowa", "STATE", ("IA",)),
    ("KS", "Kansas", "STATE", ("KS",)),
    ("KY", "Kentucky", "STATE", ("KY",)),
    ("LA", "Louisiana", "STATE", ("LA",)),
    ("ME", "Maine", "STATE", ("ME",)),
    ("MD", "Maryland", "STATE", ("MD",)),
    ("MA", "Massachusetts", "STATE", ("MA",)),
    ("MI", "Michigan", "STATE", ("MI",)),
    ("MN", "Minnesota", "STATE", ("MN",)),
    ("MS", "Mississippi", "STATE", ("MS",)),
    ("MO", "Missouri", "STATE", ("MO",)),
    ("MT", "Montana", "STATE", ("MT",)),
    ("NE", "Nebraska", "STATE", ("NE",)),
    ("NV", "Nevada", "STATE", ("NV",)),
    ("NH", "New Hampshire", "STATE", ("NH",)),
    ("NJ", "New Jersey", "STATE", ("NJ",)),
    ("NM", "New Mexico", "STATE", ("NM",)),
    ("NY", "New York", "STATE", ("NY",)),
    ("NC", "North Carolina", "STATE", ("NC",)),
    ("ND", "North Dakota", "STATE", ("ND",)),
    ("OH", "Ohio", "STATE", ("OH",)),
    ("OK", "Oklahoma", "STATE", ("OK",)),
    ("OR", "Oregon", "STATE", ("OR",)),
    ("PA", "Pennsylvania", "STATE", ("PA",)),
    ("RI", "Rhode Island", "STATE", ("RI",)),
    ("SC", "South Carolina", "STATE", ("SC",)),
    ("SD", "South Dakota", "STATE", ("SD",)),
    ("TN", "Tennessee", "STATE", ("TN",)),
    ("TX", "Texas", "STATE", ("TX",)),
    ("UT", "Utah", "STATE", ("UT",)),
    ("VT", "Vermont", "STATE", ("VT",)),
    ("VA", "Virginia", "STATE", ("VA",)),
    ("WA", "Washington", "STATE", ("WA", "Washington State")),
    ("WV", "West Virginia", "STATE", ("WV",)),
    ("WI", "Wisconsin", "STATE", ("WI",)),
    ("WY", "Wyoming", "STATE", ("WY",)),
    ("DC", "District of Columbia", "FEDERAL_DISTRICT", ("DC", "D.C.", "District Of Columbia", "Washington, DC", "Washington D.C.", "Washington DC")),
    ("PR", "Puerto Rico", "TERRITORY", ("PR",)),
    ("GU", "Guam", "TERRITORY", ("GU",)),
    ("VI", "U.S. Virgin Islands", "TERRITORY", ("VI", "USVI", "Virgin Islands", "U.S. Virgin Islands", "United States Virgin Islands")),
    ("MP", "Northern Mariana Islands", "TERRITORY", ("MP", "CNMI", "Commonwealth of the Northern Mariana Islands")),
    ("AS", "American Samoa", "TERRITORY", ("AS",)),
)
JURISDICTIONS_BY_CODE = {
    code: {
        "code": code,
        "name": name,
        "display": name,
        "type": jurisdiction_type,
        "country": "United States",
        "aliases": tuple(aliases),
    }
    for code, name, jurisdiction_type, aliases in JURISDICTION_ROWS
}
JURISDICTION_ALIASES = {
    alias.casefold(): entry
    for entry in JURISDICTIONS_BY_CODE.values()
    for alias in (entry["name"], entry["code"], *entry["aliases"])
}
JURISDICTION_ALIASES["washington"] = JURISDICTIONS_BY_CODE["WA"]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def stable_json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _normalized_key(value: str) -> str:
    return " ".join(str(value or "").strip().replace("_", " ").replace("-", " ").split()).casefold()


def _looks_private_path(value: str) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return False
    if ":/" in text or text.startswith("/") or text.startswith("\\"):
        return True
    return "/bluefern-dispatches-pages/" in text or "/output/site/" in text or text.startswith("output/site/")


def _care_line_value(row: Any, *keys: str) -> str:
    for key in keys:
        value = row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
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
    row: Any,
    *,
    lookback_days: int = EFFECTIVE_DATE_FOLLOW_UP_LOOKBACK_DAYS,
    lookahead_days: int = EFFECTIVE_DATE_FOLLOW_UP_LOOKAHEAD_DAYS,
) -> tuple[str, str]:
    effective = _parse_iso_date_text(_care_line_value(row, "effective_date", "effective_date_text"))
    if effective is None:
        return "", ""
    start = effective - timedelta(days=max(0, int(lookback_days)))
    end = effective + timedelta(days=max(0, int(lookahead_days)))
    return start.isoformat(), end.isoformat()


def care_line_event_identity(row: Any) -> str:
    text = " ".join(
        part
        for part in (
            _care_line_value(row, "source_title", "title"),
            _care_line_value(row, "supporting_passage", "effective_evidence_text", "claim_summary"),
            _care_line_value(row, "review_notes", "verification_notes"),
            _care_line_value(row, "reviewer_note"),
        )
        if part
    ).casefold()
    event_type = _care_line_value(row, "event_type", "event_type_raw", "canonical_event_type").casefold()
    effective = _care_line_value(row, "effective_date", "effective_date_text")
    if not effective and not _care_line_value(row, "supersedes_record_id") and not any(
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
        "facility_name": _care_line_identity_text(_care_line_value(row, "facility_name", "provider_name", "affected_provider", "organization_name")),
        "provider_name": _care_line_identity_text(_care_line_value(row, "provider_name", "affected_provider", "organization_name")),
        "parent_organization": _care_line_identity_text(_care_line_value(row, "parent_organization")),
        "operator_name": _care_line_identity_text(_care_line_value(row, "operator_name", "operator")),
        "city": _care_line_identity_text(_care_line_value(row, "city", "locality_name")),
        "county": _care_line_identity_text(_care_line_value(row, "county", "county_equivalent_name")),
        "state": _care_line_identity_text(_care_line_value(row, "state", "jurisdiction_display")),
        "service_line": _care_line_identity_text(_care_line_value(row, "service_line", "service_line_canonical", "service_line_raw", "affected_service_line")),
        "event_type": _care_line_identity_text(_care_line_value(row, "event_type", "event_type_raw", "canonical_event_type")),
    }
    if not any(payload.values()):
        return ""
    return f"care_line_event_{stable_json_hash(payload)[:16]}"


def care_line_event_instance_id(row: Any) -> str:
    event_identity = care_line_event_identity(row)
    if not event_identity:
        return ""
    payload = {
        "event_identity": event_identity,
        "announcement_date": _care_line_value(row, "announcement_date", "published_at", "source_publication_date"),
        "effective_date": _care_line_value(row, "effective_date", "effective_date_text"),
        "source_publication_date": _care_line_value(row, "source_publication_date", "publication_date", "source_published_date"),
    }
    return f"{event_identity}_{stable_json_hash(payload)[:12]}"


def care_line_lifecycle_status(row: Any) -> str:
    text = " ".join(
        part
        for part in (
            _care_line_value(row, "source_title", "title"),
            _care_line_value(row, "supporting_passage", "effective_evidence_text", "claim_summary"),
            _care_line_value(row, "review_notes", "verification_notes"),
            _care_line_value(row, "reviewer_note"),
        )
        if part
    ).casefold()
    event_type = _care_line_value(row, "event_type", "event_type_raw", "canonical_event_type").casefold()
    has_effective_date = bool(_care_line_value(row, "effective_date", "effective_date_text"))
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
    if _care_line_value(row, "supersedes_record_id") or _care_line_value(row, "record_status").casefold() == "superseded":
        return "SUPERSEDED"
    if has_lifecycle_terms and any(term in text for term in ("cancel", "cancelled", "canceled", "withdraw", "withdrawn", "revers", "abandon", "rescind")):
        return "CANCELLED"
    if has_lifecycle_terms and any(term in text for term in ("delay", "delayed", "postpon", "resched", "pushed back", "extended to")):
        return "DELAYED"
    if has_lifecycle_terms and (event_type in {"facility_reopening", "service_restoration"} or any(term in text for term in ("reopen", "reopened", "reopening", "restore", "restored", "resumed", "resume", "reinstat"))):
        return "RESTORED"
    effective = _parse_iso_date_text(_care_line_value(row, "effective_date", "effective_date_text"))
    if effective is not None:
        source_date = _parse_iso_date_text(_care_line_value(row, "source_publication_date", "published_at", "source_published_date", "announcement_date"))
        if source_date is not None and source_date < effective:
            return "PENDING_EFFECTIVE_DATE"
        return "EFFECTIVE"
    if has_effective_terms:
        return "EFFECTIVE"
    return ""


def care_line_effective_follow_up_status(row: Any, *, reference_date: str | None = None) -> str:
    lifecycle_status = care_line_lifecycle_status(row)
    if lifecycle_status not in {"PENDING_EFFECTIVE_DATE", "EFFECTIVE"}:
        return ""
    effective = _parse_iso_date_text(_care_line_value(row, "effective_date", "effective_date_text"))
    if effective is None:
        return ""
    reference = _parse_iso_date_text(reference_date or _care_line_value(row, "source_publication_date", "published_at", "source_published_date", "announcement_date"))
    if reference is None:
        return lifecycle_status
    if reference < effective:
        return "pending"
    if reference == effective:
        return "effective_date_reached"
    return "post_effective_follow_up"


def canonical_event_taxonomy() -> list[str]:
    return list(CANONICAL_TO_LEGACY_EVENT_TYPE)


def canonical_service_line_taxonomy() -> list[str]:
    return list(CANONICAL_TO_LEGACY_SERVICE_LINE)


def canonical_access_consequence_taxonomy() -> list[str]:
    return sorted(ACCESS_CONSEQUENCE_VALUES)


def normalize_event_type(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if text in LEGACY_TO_CANONICAL_EVENT_TYPE:
        canonical = LEGACY_TO_CANONICAL_EVENT_TYPE[text]
        return CANONICAL_TO_LEGACY_EVENT_TYPE.get(canonical, text), canonical
    if text in CANONICAL_TO_LEGACY_EVENT_TYPE:
        return CANONICAL_TO_LEGACY_EVENT_TYPE[text], text
    alias = EVENT_TYPE_ALIASES.get(_normalized_key(text), "")
    if alias:
        return CANONICAL_TO_LEGACY_EVENT_TYPE[alias], alias
    raise ValueError(f"unsupported event_type: {value}")


def normalize_service_line(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if text in LEGACY_TO_CANONICAL_SERVICE_LINE:
        canonical = LEGACY_TO_CANONICAL_SERVICE_LINE[text]
        return text, canonical
    if text in CANONICAL_TO_LEGACY_SERVICE_LINE:
        return CANONICAL_TO_LEGACY_SERVICE_LINE[text], text
    alias = SERVICE_LINE_ALIASES.get(_normalized_key(text), "")
    if alias:
        return CANONICAL_TO_LEGACY_SERVICE_LINE[alias], alias
    raise ValueError(f"unsupported service_line: {value}")


def normalize_geographic_scope(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if text in LEGACY_TO_CANONICAL_SCOPE:
        canonical = LEGACY_TO_CANONICAL_SCOPE[text]
        return CANONICAL_TO_LEGACY_SCOPE.get(canonical, text), canonical
    if text in CANONICAL_TO_LEGACY_SCOPE:
        return CANONICAL_TO_LEGACY_SCOPE[text], text
    raise ValueError(f"unsupported geographic_scope: {value}")


def normalize_access_consequences(values: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if values in (None, "", []):
        return []
    if isinstance(values, str):
        items = [value for value in (part.strip() for part in values.split(",")) if value]
    else:
        items = [str(value).strip() for value in values if str(value).strip()]
    normalized: list[str] = []
    for item in items:
        key = item.upper().replace("-", "_").replace(" ", "_")
        if key not in ACCESS_CONSEQUENCE_VALUES:
            raise ValueError(f"unsupported access consequence: {item}")
        if key not in normalized:
            normalized.append(key)
    if "NO_CONFIRMED_ACCESS_CONSEQUENCE" in normalized and len(normalized) > 1:
        normalized = [value for value in normalized if value != "NO_CONFIRMED_ACCESS_CONSEQUENCE"]
    return normalized


def normalize_verification_state(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.upper().replace("-", "_").replace(" ", "_")
    if normalized not in VERIFICATION_STATE_VALUES:
        raise ValueError(f"unsupported verification_state: {value}")
    return normalized


def normalize_workflow_state(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.upper().replace("-", "_").replace(" ", "_")
    if normalized not in WORKFLOW_STATE_VALUES:
        raise ValueError(f"unsupported workflow_state: {value}")
    return normalized


def normalize_jurisdiction(value: str, *, allow_national: bool = False) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        if allow_national:
            return {"code": "US", "name": "United States", "display": "United States", "type": "", "country": "United States"}
        raise ValueError("unknown or missing jurisdiction")
    if allow_national and text.upper() in {"US", "USA", "UNITED STATES"}:
        return {"code": "US", "name": "United States", "display": "United States", "type": "", "country": "United States"}
    entry = JURISDICTION_ALIASES.get(text.casefold())
    if entry is None:
        raise ValueError(f"unknown or ambiguous jurisdiction: {value}")
    return {
        "code": str(entry["code"]),
        "name": str(entry["name"]),
        "display": str(entry["display"]),
        "type": str(entry["type"]),
        "country": "United States",
    }


def deterministic_public_location_label(
    *,
    facility_name: str = "",
    locality: str = "",
    county_equivalent: str = "",
    jurisdiction_display: str = "",
    service_region: str = "",
    tribal_nation: str = "",
    tribal_service_area: str = "",
    island: str = "",
) -> str:
    if facility_name and locality and jurisdiction_display:
        return f"{facility_name}, {locality}, {jurisdiction_display}"
    if facility_name and island and jurisdiction_display:
        return f"{facility_name}, {island}, {jurisdiction_display}"
    if facility_name and county_equivalent and jurisdiction_display:
        return f"{facility_name}, {county_equivalent}, {jurisdiction_display}"
    if facility_name and tribal_nation and jurisdiction_display:
        return f"{facility_name}, {tribal_nation}, {jurisdiction_display}"
    if facility_name and jurisdiction_display:
        return f"{facility_name}, {jurisdiction_display}"
    if locality and jurisdiction_display:
        return f"{locality}, {jurisdiction_display}"
    if island and jurisdiction_display:
        return f"{island}, {jurisdiction_display}"
    if county_equivalent and jurisdiction_display:
        return f"{county_equivalent}, {jurisdiction_display}"
    if service_region and jurisdiction_display:
        return f"{service_region}, {jurisdiction_display}"
    if tribal_service_area and jurisdiction_display:
        return f"{tribal_service_area}, {jurisdiction_display}"
    if tribal_nation and jurisdiction_display:
        return f"{tribal_nation}, {jurisdiction_display}"
    return jurisdiction_display or locality or island or county_equivalent or service_region or tribal_service_area or tribal_nation or facility_name


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
    announcement_date_precision: str = ""
    effective_date_precision: str = ""
    event_identity: str = ""
    event_instance_id: str = ""
    lifecycle_status: CARE_LINE_REVIEWED_LIFECYCLE_STATUS = ""
    effective_follow_up_status: CARE_LINE_REVIEWED_FOLLOW_UP_STATUS = ""
    follow_up_window_start: str = ""
    follow_up_window_end: str = ""
    observed_date: str = ""
    observed_date_precision: str = ""
    review_date: str = ""
    review_date_precision: str = ""
    publication_date: str = ""
    publication_date_precision: str = ""
    source_publication_date_precision: str = ""
    source_timezone: str = ""
    service_line: str = ""
    service_line_raw: str = ""
    service_line_canonical: str = ""
    additional_service_lines: list[str] = Field(default_factory=list)
    canonical_event_type: str = ""
    access_consequences: list[str] = Field(default_factory=list)
    verification_state: str = ""
    workflow_state: str = ""

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
    jurisdiction_name: str = ""
    jurisdiction_type: str = ""
    jurisdiction_display: str = ""
    county_equivalent_name: str = ""
    locality_name: str = ""
    service_region: str = ""
    tribal_nation: str = ""
    tribal_service_area: str = ""
    island_name: str = ""
    rural_urban_designation: str = ""
    postal_code: str = ""
    country_code: str = "US"
    location_text: str = ""
    geographic_scope: str = ""
    geographic_scope_canonical: str = ""
    public_location_label: str = ""
    map_eligible: bool = False
    latitude: float | None = None
    longitude: float | None = None

    claim_summary: str = ""
    evidence_level: str = ""
    evidence_strength: str = ""
    is_primary_source: bool = False
    verification_notes: str = ""
    authority_level: str = ""
    retrieval_date: str = ""
    source_content_hash: str = ""
    review_decision: str = ""
    reviewer_note: str = ""
    duplicate_cluster_id: str = ""
    prior_access_loss_event_id: str = ""
    originating_intake_record_id: str = ""
    reviewed_record_path: str = ""
    publication_state_link: str = ""
    public_event_id: str = ""
    release_provenance_id: str = ""

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
        if self.event_type:
            if self.event_type in NON_OPERATIONAL_EVENT_TYPES:
                object.__setattr__(self, "canonical_event_type", "")
            else:
                raw_event_type = self.event_type_raw or self.event_type
                normalized_event_type, canonical_event_type = normalize_event_type(self.event_type)
                object.__setattr__(self, "event_type_raw", raw_event_type)
                object.__setattr__(self, "event_type", normalized_event_type)
                object.__setattr__(self, "canonical_event_type", canonical_event_type)
        if self.service_line:
            normalized_service_line, canonical_service_line = normalize_service_line(self.service_line)
            object.__setattr__(self, "service_line", normalized_service_line)
            object.__setattr__(self, "service_line_canonical", canonical_service_line)
        elif self.service_line_raw:
            normalized_service_line, canonical_service_line = normalize_service_line(self.service_line_raw)
            object.__setattr__(self, "service_line", normalized_service_line)
            object.__setattr__(self, "service_line_canonical", canonical_service_line)
        object.__setattr__(self, "additional_service_lines", [normalize_service_line(value)[1] for value in self.additional_service_lines if str(value).strip()])
        object.__setattr__(self, "access_consequences", normalize_access_consequences(self.access_consequences))
        if self.verification_state:
            object.__setattr__(self, "verification_state", normalize_verification_state(self.verification_state))
        else:
            object.__setattr__(self, "verification_state", self._default_verification_state())
        if self.workflow_state:
            object.__setattr__(self, "workflow_state", normalize_workflow_state(self.workflow_state))
        else:
            object.__setattr__(self, "workflow_state", self._default_workflow_state())
        if self.geographic_scope:
            normalized_scope, canonical_scope = normalize_geographic_scope(self.geographic_scope)
            object.__setattr__(self, "geographic_scope", normalized_scope)
            object.__setattr__(self, "geographic_scope_canonical", canonical_scope)
        if not self.country_code:
            object.__setattr__(self, "country_code", "US")
        if self.geographic_scope == "national" and self.state.strip().upper() in {"", "US"}:
            object.__setattr__(self, "state", "US")
            object.__setattr__(self, "jurisdiction_name", "United States")
            object.__setattr__(self, "jurisdiction_display", "United States")
            object.__setattr__(self, "jurisdiction_type", "")
        elif self.state.strip():
            jurisdiction = normalize_jurisdiction(self.state, allow_national=self.geographic_scope == "national")
            object.__setattr__(self, "state", jurisdiction["code"])
            object.__setattr__(self, "jurisdiction_name", self.jurisdiction_name or jurisdiction["name"])
            object.__setattr__(self, "jurisdiction_display", self.jurisdiction_display or jurisdiction["display"])
            object.__setattr__(self, "jurisdiction_type", self.jurisdiction_type or jurisdiction["type"])
        for field_name in (
            "date_precision",
            "announcement_date_precision",
            "effective_date_precision",
            "observed_date_precision",
            "review_date_precision",
            "publication_date_precision",
            "source_publication_date_precision",
        ):
            value = str(getattr(self, field_name) or "").strip().lower()
            if value and value not in DATE_PRECISION_VALUES:
                raise ValueError(f"unsupported {field_name}: {value}")
        if self.authority_level and self.authority_level not in AUTHORITY_LEVEL_VALUES:
            raise ValueError(f"unsupported authority_level: {self.authority_level}")
        if self.rural_urban_designation and self.rural_urban_designation not in RURALITY_VALUES:
            raise ValueError(f"unsupported rural_urban_designation: {self.rural_urban_designation}")
        if self.reviewed_record_path and _looks_private_path(self.reviewed_record_path):
            raise ValueError("public serialization containing private reviewed_record_path is not allowed")
        if self.publication_state_link and _looks_private_path(self.publication_state_link):
            raise ValueError("public serialization containing private publication_state_link is not allowed")
        object.__setattr__(
            self,
            "public_location_label",
            deterministic_public_location_label(
                facility_name=self.facility_name,
                locality=self.locality_name or self.city,
                county_equivalent=self.county_equivalent_name or self.county,
                jurisdiction_display=self.jurisdiction_display or self.state,
                service_region=self.service_region,
                tribal_nation=self.tribal_nation,
                tribal_service_area=self.tribal_service_area,
                island=self.island_name,
            ),
        )
        object.__setattr__(self, "map_eligible", self._map_eligible_default())
        event_identity = care_line_event_identity(self)
        if self.event_identity not in {"", event_identity}:
            raise ValueError(f"unsupported event_identity: {self.event_identity}")
        object.__setattr__(self, "event_identity", event_identity)
        event_instance_id = care_line_event_instance_id(self) if event_identity else ""
        if self.event_instance_id not in {"", event_instance_id}:
            raise ValueError(f"unsupported event_instance_id: {self.event_instance_id}")
        object.__setattr__(self, "event_instance_id", event_instance_id)
        lifecycle_status = care_line_lifecycle_status(self)
        if self.lifecycle_status not in {"", lifecycle_status}:
            raise ValueError(f"unsupported lifecycle_status: {self.lifecycle_status}")
        object.__setattr__(self, "lifecycle_status", lifecycle_status)
        effective_follow_up_status = care_line_effective_follow_up_status(self)
        if self.effective_follow_up_status not in {"", effective_follow_up_status}:
            raise ValueError(f"unsupported effective_follow_up_status: {self.effective_follow_up_status}")
        object.__setattr__(self, "effective_follow_up_status", effective_follow_up_status)
        follow_up_start, follow_up_end = care_line_follow_up_window(self)
        if self.follow_up_window_start not in {"", follow_up_start}:
            raise ValueError(f"unsupported follow_up_window_start: {self.follow_up_window_start}")
        if self.follow_up_window_end not in {"", follow_up_end}:
            raise ValueError(f"unsupported follow_up_window_end: {self.follow_up_window_end}")
        object.__setattr__(self, "follow_up_window_start", follow_up_start)
        object.__setattr__(self, "follow_up_window_end", follow_up_end)
        if not self.version_id:
            object.__setattr__(self, "version_id", reviewed_record_version_id(self))
        return self

    def _default_verification_state(self) -> str:
        if self.review_status in {"rejected"}:
            return "INSUFFICIENT_EVIDENCE"
        if self.is_primary_source and self.supporting_passage.strip():
            return "SOURCE_VERIFIED"
        if self.review_status in {"approved", "corrected"} and self.supporting_passage.strip():
            return "AUTHORITY_CONFIRMED"
        if self.review_status in {"reviewed"} and self.supporting_passage.strip():
            return "CORROBORATED"
        if self.evidence_valid_for_universal_event is False and not self.supporting_passage.strip():
            return "INSUFFICIENT_EVIDENCE"
        return "DISCOVERED"

    def _default_workflow_state(self) -> str:
        if self.public_status == "public_approved":
            return "APPROVED"
        if self.duplicate_of_record_id:
            return "DUPLICATE"
        if self.supersedes_record_id:
            return "SUPERSEDED"
        if self.record_status in {"excluded", "malformed", "withdrawn"} or self.review_status == "rejected":
            return "EXCLUDED"
        return "NEEDS_REVIEW"

    def _map_eligible_default(self) -> bool:
        if self.latitude is None or self.longitude is None:
            return False
        return bool(
            self.geographic_scope_canonical in {"FACILITY", "LOCALITY", "COUNTY_EQUIVALENT"}
            and (self.locality_name or self.city or self.facility_name or self.county_equivalent_name or self.county)
        )

    @property
    def has_subject(self) -> bool:
        return bool(self.facility_name.strip() or self.provider_name.strip())

    @property
    def has_location(self) -> bool:
        if self.geographic_scope_canonical == "JURISDICTION_WIDE":
            return bool(self.state.strip())
        if self.geographic_scope_canonical == "NATIONAL":
            return True
        if self.geographic_scope_canonical == "TRIBAL_SERVICE_AREA":
            return bool(self.tribal_service_area.strip() and (self.state.strip() or self.jurisdiction_display.strip()))
        return bool(
            (self.state.strip() or self.jurisdiction_display.strip())
            and (
                self.locality_name.strip()
                or self.city.strip()
                or self.county_equivalent_name.strip()
                or self.county.strip()
                or self.address_line_1.strip()
                or self.location_text.strip()
                or self.service_region.strip()
                or self.island_name.strip()
            )
        )

    @property
    def has_event_date(self) -> bool:
        return bool(self.announcement_date.strip() or self.effective_date.strip())

    @property
    def universal_event_eligible(self) -> bool:
        return self.universal_event_status == "universal_event_ready" and not self.is_withdrawn and not self.duplicate_of_record_id and not self.validation_issues()

    @property
    def service_expansion_requires_prior_loss_link(self) -> bool:
        return (self.event_type_raw == "service_expansion" or self.event_type == "service_expansion") and not self.prior_access_loss_event_id.strip()

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
        is_statewide_capacity_event = self.event_type in STATEWIDE_EVENT_TYPES and self.event_type_raw != "service_reduction"
        is_service_reduction_like = self.event_type in SERVICE_EVENT_TYPES or self.event_type_raw == "service_reduction"
        if self.event_type in NON_OPERATIONAL_EVENT_TYPES:
            return issues if self.universal_event_status != "universal_event_ready" else [ValidationIssue(field="event_type", code="non_operational_event", message="non-operational Care Line records cannot be Universal Events")]
        if self.event_type not in CARE_LINE_EVENT_TYPES:
            issues.append(ValidationIssue(field="event_type", code="unsupported_event_type", message="unsupported or missing event type"))
            return issues
        if not self.has_subject and not is_statewide_capacity_event:
            issues.append(ValidationIssue(field="facility_name", code="missing_subject", message="facility or provider subject is required"))
        if not self.has_location:
            issues.append(ValidationIssue(field="location", code="missing_geography", message="state plus one meaningful geography component is required"))
        if not self.has_event_date:
            issues.append(ValidationIssue(field="announcement_date", code="missing_event_date", message="announcement or effective date is required"))
        if not self.supporting_passage.strip():
            issues.append(ValidationIssue(field="supporting_passage", code="missing_evidence", message="supporting passage is required"))
        if is_service_reduction_like and self.service_line in {"", "unknown"}:
            issues.append(ValidationIssue(field="service_line", code="missing_service_line", message="service-line event requires a reviewed service line"))
        if self.service_expansion_requires_prior_loss_link and (
            self.universal_event_status == "universal_event_ready" or self.care_line_public_eligible or self.workflow_state == "APPROVED"
        ):
            issues.append(
                ValidationIssue(
                    field="prior_access_loss_event_id",
                    code="service_expansion_requires_prior_loss_link",
                    message="legacy service_expansion is compatibility-only and requires a linked prior access-loss event before it can qualify as a pressure/restoration signal",
                )
            )
        if self.event_type in OWNERSHIP_EVENT_TYPES and not (self.former_owner.strip() or self.new_owner.strip() or self.operator_name.strip()):
            issues.append(ValidationIssue(field="new_owner", code="missing_owner_or_operator", message="ownership/operator event requires former/new owner or operator"))
        if self.event_type in FACILITY_EVENT_TYPES and not self.permanence.strip():
            issues.append(ValidationIssue(field="permanence", code="missing_permanence", message="facility event requires permanence or reviewed unknown"))
        if is_statewide_capacity_event:
            if self.geographic_scope_canonical != "JURISDICTION_WIDE":
                issues.append(ValidationIssue(field="geographic_scope", code="statewide_scope_required", message="statewide profile requires statewide geography"))
            if not self.change_direction.strip():
                issues.append(ValidationIssue(field="change_direction", code="missing_operational_change", message="statewide event requires documented operational access change"))
        if not self.jurisdiction_display and self.geographic_scope_canonical != "NATIONAL":
            issues.append(ValidationIssue(field="state", code="unknown_jurisdiction", message="jurisdiction must normalize to one of the 56 supported US jurisdictions"))
        if self.workflow_state == "APPROVED" and self.verification_state in {"DISPUTED", "INSUFFICIENT_EVIDENCE"}:
            issues.append(ValidationIssue(field="workflow_state", code="approved_with_insufficient_evidence", message="approved workflow state requires sufficient evidence"))
        if self.map_eligible and not (self.latitude is not None and self.longitude is not None):
            issues.append(ValidationIssue(field="map_eligible", code="map_precision_missing", message="map-eligible records require verified coordinates"))
        if "LONGER_TRAVEL_DISTANCE" in self.access_consequences and "travel" not in self.claim_summary.lower() and "travel" not in self.supporting_passage.lower() and "travel" not in self.verification_notes.lower():
            issues.append(ValidationIssue(field="access_consequences", code="travel_claim_not_sourced", message="longer-travel-distance claims require sourced support"))
        if self.workflow_state == "APPROVED" and "NO_CONFIRMED_ACCESS_CONSEQUENCE" in self.access_consequences and not self.care_line_public_eligible:
            issues.append(ValidationIssue(field="access_consequences", code="no_access_consequence_for_publishable_event", message="no confirmed access consequence cannot support publication without another qualifying public basis"))
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
        if _looks_private_path(str(payload.get("reviewed_record_path") or "")):
            payload["reviewed_record_path"] = ""
        if _looks_private_path(str(payload.get("publication_state_link") or "")):
            payload["publication_state_link"] = ""
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
            "jurisdiction_name": self.jurisdiction_name,
            "jurisdiction_type": self.jurisdiction_type,
            "jurisdiction_display": self.jurisdiction_display,
            "county_equivalent_name": self.county_equivalent_name,
            "locality_name": self.locality_name,
            "service_region": self.service_region,
            "tribal_nation": self.tribal_nation,
            "tribal_service_area": self.tribal_service_area,
            "island_name": self.island_name,
            "rural_urban_designation": self.rural_urban_designation,
            "postal_code": self.postal_code,
            "country_code": self.country_code,
            "location_name": self.location_text or self.public_location_label or ", ".join(part for part in (self.city, self.state) if part),
            "location_scope": self.geographic_scope,
            "canonical_geographic_scope": self.geographic_scope_canonical,
            "event_type": self.event_type,
            "canonical_event_type": self.canonical_event_type,
            "universal_event_type": self.event_type,
            "service_line": self.service_line,
            "service_line_canonical": self.service_line_canonical,
            "effective_follow_up_status": self.effective_follow_up_status,
            "additional_service_lines": list(self.additional_service_lines),
            "affected_service_line": self.service_line_raw,
            "announcement_date": self.announcement_date,
            "effective_date": self.effective_date,
            "event_date_precision": self.date_precision,
            "announcement_date_precision": self.announcement_date_precision or self.date_precision,
            "effective_date_precision": self.effective_date_precision or self.date_precision,
            "observed_date": self.observed_date,
            "observed_date_precision": self.observed_date_precision,
            "review_date": self.review_date,
            "review_date_precision": self.review_date_precision,
            "publication_date": self.publication_date,
            "publication_date_precision": self.publication_date_precision,
            "source_publication_date_precision": self.source_publication_date_precision,
            "source_timezone": self.source_timezone,
            "permanence": self.permanence,
            "evidence_level": self.evidence_level,
            "authority_level": self.authority_level,
            "retrieval_date": self.retrieval_date,
            "source_content_hash": self.source_content_hash,
            "access_consequences": list(self.access_consequences),
            "verification_state": self.verification_state,
            "workflow_state": self.workflow_state,
            "source_public_story_eligible": self.care_line_public_eligible,
            "qualifies_for_public_inclusion": self.public_status == "public_approved",
            "pressure_signal": self.universal_event_status == "universal_event_ready",
            "included": self.public_status == "public_approved",
            "excluded": self.universal_event_status in {"excluded", "malformed"},
            "shadow_exclusion_reason": self.metadata.get("shadow_exclusion_reason", ""),
            "duplicate_of_producer_record_id": self.duplicate_of_record_id,
            "prior_access_loss_event_id": self.prior_access_loss_event_id,
            "withdrawn": self.is_withdrawn,
            "map_eligible": self.map_eligible,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "raw_payload_hash": self.raw_payload_hash,
            "_care_line_reviewed_record_contract": {
                "schema_version": self.schema_version,
                "version": self.version,
                "version_id": self.version_id,
                "supersedes_record_id": self.supersedes_record_id,
                "universal_event_status": self.universal_event_status,
                "validation_profile": self.validation_profile(),
                "canonical_event_type": self.canonical_event_type,
                "service_line_canonical": self.service_line_canonical,
                "event_identity": self.event_identity,
                "event_instance_id": self.event_instance_id,
                "lifecycle_status": self.lifecycle_status,
                "effective_follow_up_status": self.effective_follow_up_status,
                "follow_up_window_start": self.follow_up_window_start,
                "follow_up_window_end": self.follow_up_window_end,
                "verification_state": self.verification_state,
                "workflow_state": self.workflow_state,
                "access_consequences": list(self.access_consequences),
                "public_location_label": self.public_location_label,
                "field_provenance": {key: value.model_dump(mode="json") for key, value in sorted(self.field_provenance.items())},
                "correction_history": self.correction_history,
            },
        }


def reviewed_record_version_id(record: CareLineReviewedRecord) -> str:
    payload = record.model_dump(mode="json", exclude={"version_id", "created_at", "updated_at"})
    for key in (
        "event_identity",
        "event_instance_id",
        "lifecycle_status",
        "effective_follow_up_status",
        "follow_up_window_start",
        "follow_up_window_end",
    ):
        if not str(payload.get(key) or "").strip():
            payload.pop(key, None)
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
