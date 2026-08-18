"""Private normalization, matching, and reporting for historical ICE findings."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ICE_EVENT_CATEGORIES = {
    "enforcement_operation",
    "arrest_or_apprehension",
    "detention_transfer",
    "detention_capacity_change",
    "detention_facility_opening",
    "detention_facility_closure",
    "detention_overcrowding",
    "removal_or_deportation",
    "removal_flight",
    "death_in_custody",
    "serious_injury",
    "hospitalization",
    "medical_emergency",
    "suicide_or_self_harm",
    "shooting_or_firearm_discharge",
    "taser_use",
    "physical_force",
    "pursuit",
    "tactical_deployment",
    "delayed_or_denied_care",
    "legal_ruling",
    "lawsuit_or_settlement",
    "civil_rights_investigation",
    "misconduct_investigation",
    "policy_change",
    "287g_action",
    "sanctuary_or_local_response",
    "demonstration_or_community_disruption",
    "workforce_or_business_disruption",
    "school_or_agricultural_disruption",
    "humanitarian_response",
    "archived_context",
}

ICE_SEVERITIES = {"critical", "high", "medium", "low", "context"}

ICE_SCHEMA_FIELDS = (
    "finding_id",
    "agent_name",
    "agent_run_id",
    "source_url",
    "canonical_source_url",
    "publisher",
    "source_published_at",
    "event_date",
    "detection_date",
    "title",
    "exact_supporting_passage",
    "summary",
    "event_category",
    "event_subtype",
    "severity",
    "location_name",
    "city",
    "county",
    "state_or_territory",
    "facility_name",
    "agency",
    "affected_population",
    "fatalities",
    "serious_injuries",
    "hospitalizations",
    "detention_activity",
    "removal_activity",
    "enforcement_activity",
    "use_of_force",
    "legal_action",
    "policy_action",
    "investigation",
    "community_impact",
    "evidence_level",
    "confidence",
    "verification_status",
    "historical_backfill",
    "review_status",
    "publication_eligible",
    "publication_approval",
    "exclusion_reason",
    "raw_finding_reference",
)

TERRITORY_ALIASES = {
    "pr": "Puerto Rico",
    "puerto rico": "Puerto Rico",
    "gu": "Guam",
    "guam": "Guam",
    "vi": "U.S. Virgin Islands",
    "usvi": "U.S. Virgin Islands",
    "u.s. virgin islands": "U.S. Virgin Islands",
    "us virgin islands": "U.S. Virgin Islands",
    "united states virgin islands": "U.S. Virgin Islands",
    "mp": "Northern Mariana Islands",
    "cnmi": "Northern Mariana Islands",
    "northern mariana islands": "Northern Mariana Islands",
    "commonwealth of the northern mariana islands": "Northern Mariana Islands",
    "as": "American Samoa",
    "american samoa": "American Samoa",
    "dc": "District of Columbia",
    "d.c.": "District of Columbia",
    "district of columbia": "District of Columbia",
    "washington dc": "District of Columbia",
    "washington, dc": "District of Columbia",
    "washington, d.c.": "District of Columbia",
}

INCIDENT_IDENTIFIER_FIELDS = (
    "incident_id",
    "event_id",
    "normalized_incident_fingerprint",
    "incident_fingerprint",
)
LEGAL_IDENTIFIER_FIELDS = (
    "legal_record_id",
    "case_id",
    "case_number",
    "docket_id",
    "docket_number",
)
SOURCE_IDENTIFIER_FIELDS = ("source_record_id", "source_id", "manual_source_identifier")
FACILITY_IDENTIFIER_FIELDS = ("facility_id", "contract_id", "facility_identifier", "contract_identifier")
MATCH_CONFLICT_FIELDS = (
    "event_date",
    "event_category",
    "location_name",
    "facility_name",
    "agency",
    "removal_destination",
)
FORCE_CATEGORIES = {
    "shooting_or_firearm_discharge",
    "taser_use",
    "physical_force",
    "pursuit",
    "tactical_deployment",
}
DETENTION_CHANGE_CATEGORIES = {
    "detention_transfer",
    "detention_capacity_change",
    "detention_facility_opening",
    "detention_facility_closure",
    "detention_overcrowding",
}
REMOVAL_CATEGORIES = {"removal_or_deportation", "removal_flight"}
LEGAL_CATEGORIES = {
    "legal_ruling",
    "lawsuit_or_settlement",
    "civil_rights_investigation",
    "misconduct_investigation",
}
POLICY_CATEGORIES = {"policy_change", "287g_action", "sanctuary_or_local_response"}
COMMUNITY_CATEGORIES = {
    "demonstration_or_community_disruption",
    "workforce_or_business_disruption",
    "school_or_agricultural_disruption",
    "humanitarian_response",
}


def clean_url(value: Any) -> str:
    return str(value or "").strip().lower().split("#", 1)[0].split("?", 1)[0].rstrip("/")


def normalized_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def normalize_territory(value: Any) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    return TERRITORY_ALIASES.get(token.lower(), token)


def _json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _json_dicts(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            rows.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return rows


def _placeholder_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return True
    return host in {"example.com", "example.org", "example.net"} or host.endswith((".example.com", ".example.org", ".example.net"))


def _first(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        token = str(row.get(field) or "").strip()
        if token:
            return token
    return ""


def _date(value: Any) -> str:
    token = str(value or "").strip()
    match = re.search(r"20\d{2}-\d{2}-\d{2}", token)
    return match.group(0) if match else token[:10]


def normalize_detection_date(value: Any) -> str | None:
    """Normalize only an explicitly supplied ICE detection date or timestamp."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("ICE detection_date must be an ISO date, ISO timestamp, or null")
    token = value.strip()
    if not token:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        try:
            return date.fromisoformat(token).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid ICE detection_date: {token}") from exc
    if re.search(r"[T ]\d{2}:\d{2}", token):
        try:
            parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ICE detection_date timestamp: {token}") from exc
        normalized = parsed.isoformat()
        return normalized[:-6] + "Z" if normalized.endswith("+00:00") else normalized
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(token, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"invalid ICE detection_date: {token}")


def explicit_detection_date_text(raw_text: str) -> str | None:
    """Return an explicit Detection Date field value without inferring one."""
    values: list[str] = []
    for line in raw_text.splitlines():
        plain = line.replace("*", "").replace("`", "").strip()
        match = re.match(r"(?i)^[-+]\s*", plain)
        if match:
            plain = plain[match.end() :].strip()
        field = re.match(r'(?i)^["\']?detection[ _-]date["\']?\s*:\s*(.+?)\s*,?$', plain)
        if not field:
            continue
        value = field.group(1).strip().strip('"\'').rstrip(",").strip()
        if value:
            values.append(value)
    normalized = {normalize_detection_date(value) for value in values}
    if len(normalized) > 1:
        raise ValueError("conflicting explicit ICE detection dates in preserved alert")
    return values[0] if values else None


def extract_detection_date(raw_text: str) -> str | None:
    value = explicit_detection_date_text(raw_text)
    return normalize_detection_date(value)


def _incident_fingerprint(row: dict[str, Any]) -> str:
    supplied = _first(row, ("normalized_incident_fingerprint", "incident_fingerprint"))
    if supplied:
        return supplied
    identity = {
        "event_date": _date(row.get("event_date")),
        "event_category": str(row.get("event_category") or "").strip().lower(),
        "location": normalized_text(row.get("location_name") or row.get("location")),
        "facility": normalized_text(row.get("facility_name") or row.get("facility")),
        "agency": normalized_text(row.get("agency")),
        "affected": normalized_text(
            row.get("affected_person")
            or row.get("named_person")
            or row.get("affected_group")
            or row.get("affected_population")
        ),
        "removal_destination": normalized_text(row.get("removal_destination") or row.get("destination")),
    }
    if not identity["event_date"] or not identity["event_category"]:
        return ""
    if not any(identity[key] for key in ("location", "facility", "agency", "affected", "removal_destination")):
        return ""
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ice_historical_identity(row: dict[str, Any]) -> str:
    return json.dumps(
        {
            "url": clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url")),
            "event_date": _date(row.get("event_date")),
            "event_category": str(row.get("event_category") or "").strip().lower(),
            "facility": normalized_text(row.get("facility_name") or row.get("facility")),
            "location": normalized_text(row.get("location_name") or row.get("location")),
            "agency": normalized_text(row.get("agency")),
            "legal_id": _first(row, LEGAL_IDENTIFIER_FIELDS).lower(),
            "incident_id": _first(row, INCIDENT_IDENTIFIER_FIELDS).lower(),
            "removal_destination": normalized_text(row.get("removal_destination") or row.get("destination")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _target_kind(path: str, row: dict[str, Any]) -> str:
    lower_path = path.replace("\\", "/").lower()
    if _first(row, LEGAL_IDENTIFIER_FIELDS) or str(row.get("source_type") or "").lower() == "court_record" or "/legal" in lower_path:
        return "legal"
    if _first(row, INCIDENT_IDENTIFIER_FIELDS) or row.get("event_category") or "/incident" in lower_path:
        return "incident"
    return "source"


def _ice_scoped_document(rows: list[dict[str, Any]]) -> bool:
    fields = (
        "domain",
        "event_domain",
        "dispatch",
        "dispatch_name",
        "record_type",
        "event_type",
        "event_category",
        "source_domain",
        "producer_domain",
    )
    tokens = " ".join(str(row.get(field) or "") for row in rows for field in fields).lower()
    return bool(
        re.search(
            r"\bice\b|immigration[_ ]enforcement|\bdetention\b|\bdeportation\b|"
            r"\bremoval\b|\b287g\b|287\(g\)",
            tokens,
        )
    )


def ice_match_targets(root: Path) -> dict[str, Any]:
    """Index private ICE-adjacent records; rendered/public output is never consulted."""
    bases = (
        root / "data" / "dispatches" / "ice",
        root / "data" / "dispatches" / "cascadia" / "detention_watch",
        root / "data" / "universal_events",
    )
    targets: list[dict[str, Any]] = []
    for base in bases:
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            relative = path.relative_to(root).as_posix()
            if "/seed/" in f"/{relative.lower()}/":
                continue
            rows = _json_dicts(_json_value(path))
            if base == root / "data" / "universal_events" and not _ice_scoped_document(rows):
                continue
            for row in rows:
                url = clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url"))
                if url and _placeholder_url(url):
                    url = ""
                incident_id = _first(row, INCIDENT_IDENTIFIER_FIELDS)
                legal_id = _first(row, LEGAL_IDENTIFIER_FIELDS)
                source_id = _first(row, SOURCE_IDENTIFIER_FIELDS)
                facility_id = _first(row, FACILITY_IDENTIFIER_FIELDS)
                fingerprint = _incident_fingerprint(row)
                removal_date = _date(row.get("removal_flight_date") or row.get("event_date")) if row.get("removal_destination") or row.get("destination") else ""
                removal_destination = normalized_text(row.get("removal_destination") or row.get("destination"))
                if not any((url, incident_id, legal_id, source_id, facility_id, fingerprint, removal_date and removal_destination)):
                    continue
                targets.append(
                    {
                        "path": relative,
                        "kind": _target_kind(relative, row),
                        "url": url,
                        "incident_id": incident_id,
                        "legal_id": legal_id,
                        "source_id": source_id,
                        "facility_id": facility_id,
                        "fingerprint": fingerprint,
                        "removal_identity": f"{removal_date}|{removal_destination}" if removal_date and removal_destination else "",
                        "event_date": _date(row.get("event_date")),
                        "event_category": str(row.get("event_category") or "").strip().lower(),
                        "location_name": str(row.get("location_name") or row.get("location") or "").strip(),
                        "facility_name": str(row.get("facility_name") or row.get("facility") or row.get("name") or "").strip(),
                        "agency": str(row.get("agency") or row.get("publisher") or "").strip(),
                        "removal_destination": str(row.get("removal_destination") or row.get("destination") or "").strip(),
                    }
                )

    historical_identities: set[str] = set()
    historical_root = root / "data" / "agent-history" / "ice" / "normalized"
    if historical_root.exists():
        for path in historical_root.rglob("*.json"):
            for row in _json_dicts(_json_value(path)):
                if row.get("domain") not in (None, "", "ice"):
                    continue
                if any(row.get(field) for field in ("source_url", "canonical_source_url", "finding_id", "event_date")):
                    historical_identities.add(ice_historical_identity(row))
    return {"records": targets, "historical_identities": historical_identities}


def _count(value: Any) -> tuple[int | None, bool]:
    if value is None or value == "":
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, int) and value >= 0:
        return value, True
    token = str(value).strip()
    if token.isdigit():
        return int(token), True
    return None, False


def _categories(row: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    primary = str(row.get("event_category") or "").strip().lower()
    values = row.get("secondary_event_categories")
    if values is None:
        values = row.get("event_categories")
    if isinstance(values, str):
        values = [values]
    secondary: list[str] = []
    if isinstance(values, list):
        for item in values:
            token = str(item or "").strip().lower()
            if token and token != primary and token not in secondary:
                secondary.append(token)
    invalid = [item for item in ([primary] if primary else []) + secondary if item not in ICE_EVENT_CATEGORIES]
    return primary, secondary, invalid


def _severity(row: dict[str, Any], categories: set[str], fatalities: int | None, serious_injuries: int | None, hospitalizations: int | None) -> tuple[str | None, str | None]:
    explicit = str(row.get("severity") or "").strip().lower() or None
    if explicit and explicit not in ICE_SEVERITIES:
        return None, "unsupported severity"
    subtype = str(row.get("event_subtype") or "").strip().lower()
    shooting_critical = "shooting_or_firearm_discharge" in categories and (
        subtype == "officer_involved_shooting"
        or row.get("firearm_injury") is True
        or bool(fatalities and fatalities > 0)
        or bool(serious_injuries and serious_injuries > 0)
        or bool(hospitalizations and hospitalizations > 0)
    )
    critical = (
        bool(fatalities and fatalities > 0)
        or "death_in_custody" in categories
        or shooting_critical
        or subtype
        in {
            "officer_involved_shooting",
            "mass_casualty",
            "serious_use_of_force",
            "urgent_system_wide_crisis",
            "urgent_humanitarian_crisis",
        }
        or row.get("serious_use_of_force") is True
        or row.get("urgent_system_wide_crisis") is True
        or bool(row.get("use_of_force") and serious_injuries and serious_injuries > 0)
    )
    high = (
        bool(serious_injuries and serious_injuries > 0)
        or bool(hospitalizations and hospitalizations > 0)
        or bool(categories & {"serious_injury", "hospitalization", "suicide_or_self_harm"})
        or subtype
        in {
            "major_facility_overcrowding",
            "large_enforcement_operation",
            "major_removal_action",
            "broad_operational_injunction",
            "widespread_community_disruption",
        }
        or row.get("major_facility_overcrowding") is True
        or row.get("large_enforcement_operation") is True
        or row.get("major_removal_action") is True
        or row.get("broad_operational_injunction") is True
        or row.get("widespread_community_disruption") is True
    )
    if explicit == "critical" and not critical:
        return None, "critical severity lacks a documented critical-severity fact"
    if explicit == "high" and not (critical or high):
        return None, "high severity lacks a documented high-severity fact"
    if explicit == "context" and "archived_context" not in categories and row.get("context_only") is not True:
        return None, "context severity requires explicit context-only classification"
    if critical:
        return "critical", None
    if high:
        return "high", None
    if "archived_context" in categories:
        return "context", None
    return explicit, None


def _weak_evidence(value: Any) -> bool:
    token = " ".join(str(value or "").strip().lower().split())
    if len(token) < 20:
        return True
    return token in {
        "general background only.",
        "general background only",
        "background information only.",
        "background information only",
        "context only.",
        "context only",
    }


def _conflicts(row: dict[str, Any], target: dict[str, Any]) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    for field in MATCH_CONFLICT_FIELDS:
        candidate = row.get(field)
        if field == "removal_destination":
            candidate = row.get("removal_destination") or row.get("destination")
        existing = target.get(field)
        if not candidate or not existing:
            continue
        left = _date(candidate) if field == "event_date" else normalized_text(candidate)
        right = _date(existing) if field == "event_date" else normalized_text(existing)
        if left and right and left != right:
            conflicts.append({"field": field, "finding": str(candidate), "existing": str(existing)})
    return conflicts


def _matches(row: dict[str, Any], targets: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    url = clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url"))
    incident_id = _first(row, INCIDENT_IDENTIFIER_FIELDS)
    legal_id = _first(row, LEGAL_IDENTIFIER_FIELDS)
    source_id = _first(row, SOURCE_IDENTIFIER_FIELDS)
    facility_id = _first(row, FACILITY_IDENTIFIER_FIELDS)
    fingerprint = _incident_fingerprint(row)
    removal_date = _date(row.get("removal_flight_date") or row.get("event_date"))
    removal_destination = normalized_text(row.get("removal_destination") or row.get("destination"))
    checks = (
        ("incident_identifier", "incident_id", incident_id),
        ("legal_identifier", "legal_id", legal_id),
        ("source_identifier", "source_id", source_id),
        ("facility_or_contract_identifier", "facility_id", facility_id),
        ("normalized_incident_fingerprint", "fingerprint", fingerprint),
        ("removal_flight_identity", "removal_identity", f"{removal_date}|{removal_destination}" if removal_date and removal_destination else ""),
        ("canonical_source_url", "url", url),
    )
    for basis, field, value in checks:
        if not value:
            continue
        found = [target for target in targets["records"] if str(target.get(field) or "").lower() == value.lower()]
        if found:
            return found, basis
    return [], ""


def normalize_ice_record(
    row: dict[str, Any],
    *,
    payload: Any,
    raw_sha256: str,
    targets: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    detection_value = row.get("detection_date")
    if detection_value in (None, "") and isinstance(row.get("raw_text"), str):
        detection_value = extract_detection_date(row["raw_text"])
    detection_date = normalize_detection_date(detection_value)
    primary, secondary, invalid_categories = _categories(row)
    category_set = set(secondary)
    if primary:
        category_set.add(primary)
    fatalities, fatalities_valid = _count(row.get("fatalities"))
    serious_injuries, serious_injuries_valid = _count(row.get("serious_injuries"))
    hospitalizations, hospitalizations_valid = _count(row.get("hospitalizations"))
    severity, severity_error = _severity(row, category_set, fatalities, serious_injuries, hospitalizations)
    evidence = str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("evidence_text") or "").strip()
    source_url = str(row.get("source_url") or row.get("url") or "").strip()
    canonical_source_url = str(row.get("canonical_source_url") or clean_url(source_url) or "").strip()
    matches, match_basis = _matches(row, targets)
    match_conflicts = [item for target in matches for item in _conflicts(row, target)]
    historical_outcome = "new_historical_candidate"
    candidate_created = True
    provenance_only = False
    review_status = "pending_review"
    exclusion_reason: str | None = None
    ambiguity_reason: str | None = None
    matched_record_id = ""

    if ice_historical_identity(row) in targets["historical_identities"]:
        historical_outcome, match_basis = "duplicate_historical", "historical_identity"
        candidate_created, review_status = False, "excluded"
    elif _weak_evidence(evidence):
        historical_outcome, match_basis = "archived_invalid", "insufficient_exact_evidence"
        candidate_created, review_status = False, "excluded"
        exclusion_reason = "missing or insufficient exact supporting evidence"
    elif invalid_categories:
        historical_outcome, match_basis = "archived_invalid", "unsupported_event_category"
        candidate_created, review_status = False, "excluded"
        exclusion_reason = f"unsupported ICE event category: {', '.join(invalid_categories)}"
    elif not all((fatalities_valid, serious_injuries_valid, hospitalizations_valid)):
        historical_outcome, match_basis = "archived_invalid", "invalid_casualty_count"
        candidate_created, review_status = False, "excluded"
        exclusion_reason = "fatality, injury, and hospitalization counts must be non-negative integers or null"
    elif severity_error:
        historical_outcome, match_basis = "archived_invalid", "unsupported_severity"
        candidate_created, review_status = False, "excluded"
        exclusion_reason = severity_error
    elif primary == "archived_context" or row.get("context_only") is True:
        historical_outcome, match_basis = "archived_context", "explicit_context_only"
        candidate_created, review_status = False, "historical_context"
        exclusion_reason = "context-only ICE material retained for historical reference"
    elif matches and match_conflicts:
        historical_outcome, match_basis = "needs_manual_review", f"{match_basis}_with_conflicts"
        candidate_created = False
        ambiguity_reason = "matching identifier conflicts with existing fields"
    elif len(matches) > 1 and len({(item["path"], item.get("incident_id"), item.get("legal_id"), item.get("source_id")) for item in matches}) > 1:
        historical_outcome, match_basis = "needs_manual_review", f"ambiguous_{match_basis}"
        candidate_created = False
        ambiguity_reason = "multiple private ICE records match the supplied identifier"
    elif matches:
        selected = sorted(matches, key=lambda item: (item["kind"], item["path"]))[0]
        historical_outcome = {
            "incident": "matched_existing_incident",
            "legal": "matched_existing_legal_record",
            "source": "matched_existing_source",
        }[selected["kind"]]
        candidate_created, provenance_only, review_status = False, True, "excluded"
        matched_record_id = str(
            selected.get("incident_id")
            or selected.get("legal_id")
            or selected.get("source_id")
            or selected.get("facility_id")
            or selected.get("path")
        )
    elif not primary:
        historical_outcome, match_basis = "needs_manual_review", "missing_event_category"
        candidate_created = False
        ambiguity_reason = "ICE finding lacks a controlled primary event category"
    elif not (_date(row.get("event_date")) or _date(row.get("source_published_at") or row.get("published_at"))):
        historical_outcome, match_basis = "needs_manual_review", "missing_event_or_source_date"
        candidate_created = False
        ambiguity_reason = "ICE finding lacks both event and source dates"
    elif not canonical_source_url and not any(_first(row, fields) for fields in (INCIDENT_IDENTIFIER_FIELDS, LEGAL_IDENTIFIER_FIELDS, SOURCE_IDENTIFIER_FIELDS)):
        historical_outcome, match_basis = "needs_manual_review", "missing_traceable_identity"
        candidate_created = False
        ambiguity_reason = "ICE finding lacks a canonical source URL or stable private identifier"
    else:
        match_basis = "unmatched_traceable_finding"

    provenance_links = [
        {
            "path": target["path"],
            "record_type": target["kind"],
            "incident_id": target.get("incident_id") or "",
            "legal_record_id": target.get("legal_id") or "",
            "source_record_id": target.get("source_id") or "",
        }
        for target in matches
    ]
    payload_agent_name = payload.get("agent_name") if isinstance(payload, dict) else None
    payload_agent_run_id = payload.get("agent_run_id") if isinstance(payload, dict) else None
    record = dict(row)
    for field in ICE_SCHEMA_FIELDS:
        record.setdefault(field, None)
    record.update(
        {
            "domain": "ice",
            "finding_id": row.get("finding_id") or None,
            "agent_name": row.get("agent_name") or payload_agent_name or None,
            "agent_run_id": row.get("agent_run_id") or payload_agent_run_id or None,
            "source_url": source_url or None,
            "canonical_source_url": canonical_source_url or None,
            "publisher": row.get("publisher") or None,
            "source_published_at": row.get("source_published_at") or row.get("published_at") or None,
            "event_date": row.get("event_date") or None,
            "detection_date": detection_date,
            "title": row.get("title") or row.get("headline") or None,
            "exact_supporting_passage": evidence or None,
            "summary": row.get("summary") or None,
            "event_category": primary or None,
            "secondary_event_categories": secondary,
            "event_subtype": row.get("event_subtype") or None,
            "severity": severity,
            "location_name": row.get("location_name") or row.get("location") or None,
            "city": row.get("city") or None,
            "county": row.get("county") or None,
            "state_or_territory": normalize_territory(row.get("state_or_territory") or row.get("state")),
            "facility_name": row.get("facility_name") or row.get("facility") or None,
            "agency": row.get("agency") or None,
            "affected_population": row.get("affected_population") or None,
            "fatalities": fatalities,
            "serious_injuries": serious_injuries,
            "hospitalizations": hospitalizations,
            "verification_status": row.get("verification_status") or "pending_review",
            "historical_backfill": True,
            "review_status": review_status,
            "publication_eligible": False,
            "publication_approval": False,
            "exclusion_reason": exclusion_reason,
            "raw_finding_reference": row.get("raw_finding_reference"),
            "raw_sha256": raw_sha256,
            "historical_outcome": historical_outcome,
            "deduplication_outcome": historical_outcome,
            "match_basis": match_basis,
            "matched_record_id": matched_record_id or None,
            "conflicting_fields": match_conflicts,
            "ambiguity_reason": ambiguity_reason,
            "candidate_created": candidate_created,
            "provenance_only": provenance_only,
            "queue_action": "none",
            "provenance_links": provenance_links,
        }
    )
    if historical_outcome in {"archived_context", "archived_invalid"}:
        record["archive_status"] = "archived"
    return record, historical_outcome


def ice_report(record: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "raw_sha256",
        "finding_id",
        "agent_name",
        "agent_run_id",
        "source_url",
        "canonical_source_url",
        "publisher",
        "source_published_at",
        "event_date",
        "detection_date",
        "captured_at",
        "imported_at",
        "last_normalized_at",
        "title",
        "event_category",
        "secondary_event_categories",
        "event_subtype",
        "severity",
        "location_name",
        "city",
        "county",
        "state_or_territory",
        "facility_name",
        "agency",
        "fatalities",
        "serious_injuries",
        "hospitalizations",
        "detention_activity",
        "removal_activity",
        "enforcement_activity",
        "use_of_force",
        "legal_action",
        "policy_action",
        "investigation",
        "community_impact",
        "confidence",
        "verification_status",
        "evidence_level",
        "historical_outcome",
        "match_basis",
        "matched_record_id",
        "conflicting_fields",
        "candidate_created",
        "provenance_only",
        "review_status",
        "exclusion_reason",
        "publication_eligible",
        "publication_approval",
        "queue_action",
        "provenance_links",
    )
    return {field: record.get(field) for field in fields}


def _activity(value: Any) -> bool:
    if value in (None, False, "", [], {}):
        return False
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "none", "not_applicable"}:
        return False
    return True


def ice_aggregate_metrics(records: list[dict[str, Any]], *, raw_runs: int | None = None) -> dict[str, int]:
    categories = [set([str(row.get("event_category") or "")] + [str(item) for item in row.get("secondary_event_categories") or []]) for row in records]
    return {
        "raw_runs": int(raw_runs if raw_runs is not None else len({str(row.get("raw_sha256") or "") for row in records if row.get("raw_sha256")})),
        "normalized_findings": len(records),
        "critical_findings": sum(1 for row in records if row.get("severity") == "critical"),
        "high_findings": sum(1 for row in records if row.get("severity") == "high"),
        "fatalities": sum(int(row.get("fatalities") or 0) for row in records),
        "deaths_in_custody": sum(1 for value in categories if "death_in_custody" in value),
        "serious_injuries": sum(int(row.get("serious_injuries") or 0) for row in records),
        "hospitalizations": sum(int(row.get("hospitalizations") or 0) for row in records),
        "use_of_force_incidents": sum(1 for row, value in zip(records, categories) if bool(value & FORCE_CATEGORIES) or _activity(row.get("use_of_force"))),
        "enforcement_operations": sum(1 for row, value in zip(records, categories) if "enforcement_operation" in value or _activity(row.get("enforcement_activity"))),
        "detention_changes": sum(1 for row, value in zip(records, categories) if bool(value & DETENTION_CHANGE_CATEGORIES) or _activity(row.get("detention_activity"))),
        "removal_actions": sum(1 for row, value in zip(records, categories) if bool(value & REMOVAL_CATEGORIES) or _activity(row.get("removal_activity"))),
        "legal_actions": sum(1 for row, value in zip(records, categories) if bool(value & LEGAL_CATEGORIES) or _activity(row.get("legal_action"))),
        "policy_actions": sum(1 for row, value in zip(records, categories) if bool(value & POLICY_CATEGORIES) or _activity(row.get("policy_action"))),
        "community_disruptions": sum(1 for row, value in zip(records, categories) if bool(value & COMMUNITY_CATEGORIES) or _activity(row.get("community_impact"))),
        "duplicates": sum(1 for row in records if row.get("historical_outcome") == "duplicate_historical"),
        "invalid_findings": sum(1 for row in records if row.get("historical_outcome") == "archived_invalid"),
        "pending_review": sum(1 for row in records if row.get("review_status") == "pending_review"),
        "pending_substantive_review": sum(
            1
            for row in records
            if row.get("historical_outcome") == "new_historical_candidate"
            and row.get("review_status") == "pending_review"
        ),
        "substantively_reviewed": sum(
            1 for row in records if row.get("review_status") == "substantively_reviewed"
        ),
        "queue_entries": sum(
            1
            for row in records
            if row.get("queue_action")
            not in {None, "", "none", "provenance_only", "historical_review_candidate"}
        ),
        "publication_ready_count": 0,
    }
