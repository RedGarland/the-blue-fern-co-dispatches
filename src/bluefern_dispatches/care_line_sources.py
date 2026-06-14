from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DISPATCH_SLUG = "care-line"
DISPATCH_NAME = "The Care Line Dispatch"
DISPATCH_TAGLINE = "Source-backed signals of where American healthcare access is under strain."
POSITIONING_NOTE = (
    "The Care Line tracks source-backed reported signals of healthcare access pressure available at publish time. "
    "It should not be read as a complete national measure of healthcare quality, healthcare access, or unmet medical need."
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
    path = root / MANUAL_SOURCES_PATH / edition_date / "manual_sources.json"
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
                "how_used": str(_record_value(record, "source_role") or ""),
                "issue": str(_record_value(record, "pressure_type") or ""),
                "what_happened": str(_record_value(record, "pressure_summary") or _record_value(record, "summary_or_snippet") or ""),
                "what_the_source_says": str(_record_value(record, "claim_supported") or _record_value(record, "evidence_text") or ""),
                "verification_status": "qualified" if record_is_public(record) else str(_record_value(record, "exclusion_reason") or "excluded"),
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
                "freshness_role": str(_record_value(record, "freshness_role") or ""),
                "location_scope": str(_record_value(record, "location_scope") or ""),
                "limitation": str(_record_value(record, "limitations") or ""),
            }
        )
    return rows


def summary_for_records(records: list[dict[str, Any]]) -> str:
    public_rows = public_claim_rows(records)
    if not public_rows:
        return DISPATCH_TAGLINE
    lead = public_rows[0]["claim"] or public_rows[0]["supporting_source"]
    return f"{lead} This pilot edition uses real, traceable source records."


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
    report["stale_current_signal_count"] = int(manifest.get("stale_current_signal_count") or 0)
    report["resource_only_count"] = int(manifest.get("resource_only_count") or 0)
    report["skip_reason"] = str(manifest.get("skip_reason") or "")
    if report["dispatch_slug_value"] != DISPATCH_SLUG:
        report["reasons"].append(f"dispatch_slug must be {DISPATCH_SLUG}")
    if report["edition_date_value"] and report["edition_date_value"] != edition_date:
        report["reasons"].append("edition_date mismatch")
    if not report["public_rendered"]:
        report["reasons"].append("public_rendered is false")
    if report["source_count"] <= 0 or report["story_count"] <= 0 or report["claim_count"] <= 0:
        report["reasons"].append("source, story, or claim counts are missing")
    if report["qualified_public_claim_count"] <= 0:
        report["reasons"].append("no qualified public claims")
    if report["stale_current_signal_count"] > 0:
        report["reasons"].append("stale current signals remain in the public edition")
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
        and report["source_count"] > 0
        and report["story_count"] > 0
        and report["claim_count"] > 0
        and report["qualified_public_claim_count"] > 0
        and report["stale_current_signal_count"] == 0
        and not report["skip_reason"]
    )
    return report
