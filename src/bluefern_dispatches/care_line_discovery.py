from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from bluefern_dispatches.care_line_sources import (
    care_line_review_diagnostics,
    load_manual_source_records,
    no_current_update_summary,
    record_is_public,
    validate_manual_source_records,
)
from bluefern_dispatches.incident_discovery import build_incident_follow_up_queries
from bluefern_dispatches.food_line_sources import (
    _extract_page_evidence,
    _extract_page_metadata_date,
    _fetch,
    _normalize_source_text,
    _parse_rss_items,
    canonical_url,
    validate_date,
)

DISPATCH_SLUG = "care-line"
DISCOVERY_OUTPUT_FILE = "discovered_sources.json"
DISCOVERY_REPORT_FILE = "discovery_report.json"

DEFAULT_EXCLUDE_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "youtube.com",
)

DEFAULT_QUERY_CONFIG = {
    "queries": [
        {"query_template": '"hospital closure" "patients"', "source_family": "local_news", "category": "hospital_closure"},
        {"query_template": '"hospital cuts services" "patients"', "source_family": "local_news", "category": "service_line_cut"},
        {"query_template": '"rural hospital" "financial crisis" "access"', "source_family": "local_news", "category": "rural_access_strain"},
        {"query_template": '"emergency room boarding" "hospital" "patients"', "source_family": "local_news", "category": "er_crowding_or_diversion"},
        {"query_template": '"ER diversion" "hospital"', "source_family": "local_news", "category": "er_crowding_or_diversion"},
        {"query_template": '"clinic closure" "patients"', "source_family": "local_news", "category": "clinic_access_strain"},
        {"query_template": '"appointment backlog" "clinic"', "source_family": "local_news", "category": "clinic_access_strain"},
        {"query_template": '"maternity ward closing"', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"labor and delivery unit closing"', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"OB unit closure"', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"Medicaid cuts" "patients" "clinic"', "source_family": "state_policy_news", "category": "coverage_disruption"},
        {"query_template": '"insurance coverage loss" "patients"', "source_family": "state_policy_news", "category": "coverage_disruption"},
        {"query_template": '"medical debt" "hospital" "patients"', "source_family": "local_news", "category": "medical_debt_or_affordability"},
        {"query_template": '"health care staffing shortage" "clinic access"', "source_family": "local_news", "category": "staffing_shortage_access"},
        {"query_template": '"pharmacy closure" "prescription access"', "source_family": "local_news", "category": "pharmacy_access_pressure"},
        {"query_template": '"public health department cuts"', "source_family": "state_policy_news", "category": "public_health_capacity_cut"},
        {"query_template": '"ambulance delay" "EMS coverage"', "source_family": "local_news", "category": "ambulance_or_ems_strain"},
        {"query_template": 'site:.org "hospital closure" "patients"', "source_family": "nonprofit_news", "category": "hospital_closure"},
        {"query_template": 'site:.org "maternity ward closing"', "source_family": "nonprofit_news", "category": "maternity_care_loss"},
        {"query_template": 'site:.org "pharmacy closure" "prescription"', "source_family": "nonprofit_news", "category": "pharmacy_access_pressure"},
        {"query_template": 'site:.org "Medicaid" "coverage loss" "patients"', "source_family": "state_policy_news", "category": "coverage_disruption"},
    ],
    "date_bounded_queries": [
        {"query_template": '"hospital closure" "patients" after:{after} before:{before}', "source_family": "local_news", "category": "hospital_closure"},
        {"query_template": '"hospital cuts services" "patients" after:{after} before:{before}', "source_family": "local_news", "category": "service_line_cut"},
        {"query_template": '"rural hospital" "financial crisis" "access" after:{after} before:{before}', "source_family": "local_news", "category": "rural_access_strain"},
        {"query_template": '"emergency room boarding" "hospital" "patients" after:{after} before:{before}', "source_family": "local_news", "category": "er_crowding_or_diversion"},
        {"query_template": '"ER diversion" "hospital" after:{after} before:{before}', "source_family": "local_news", "category": "er_crowding_or_diversion"},
        {"query_template": '"clinic closure" "patients" after:{after} before:{before}', "source_family": "local_news", "category": "clinic_access_strain"},
        {"query_template": '"appointment backlog" "clinic" after:{after} before:{before}', "source_family": "local_news", "category": "clinic_access_strain"},
        {"query_template": '"maternity ward closing" after:{after} before:{before}', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"labor and delivery unit closing" after:{after} before:{before}', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"OB unit closure" after:{after} before:{before}', "source_family": "local_news", "category": "maternity_care_loss"},
        {"query_template": '"Medicaid cuts" "patients" "clinic" after:{after} before:{before}', "source_family": "state_policy_news", "category": "coverage_disruption"},
        {"query_template": '"insurance coverage loss" "patients" after:{after} before:{before}', "source_family": "state_policy_news", "category": "coverage_disruption"},
        {"query_template": '"medical debt" "hospital" "patients" after:{after} before:{before}', "source_family": "local_news", "category": "medical_debt_or_affordability"},
        {"query_template": '"health care staffing shortage" "clinic access" after:{after} before:{before}', "source_family": "local_news", "category": "staffing_shortage_access"},
        {"query_template": '"pharmacy closure" "prescription access" after:{after} before:{before}', "source_family": "local_news", "category": "pharmacy_access_pressure"},
        {"query_template": '"public health department cuts" after:{after} before:{before}', "source_family": "state_policy_news", "category": "public_health_capacity_cut"},
        {"query_template": '"ambulance delay" "EMS coverage" after:{after} before:{before}', "source_family": "local_news", "category": "ambulance_or_ems_strain"},
        {"query_template": 'site:.org "hospital closure" "patients" after:{after} before:{before}', "source_family": "nonprofit_news", "category": "hospital_closure"},
        {"query_template": 'site:.org "maternity ward closing" after:{after} before:{before}', "source_family": "nonprofit_news", "category": "maternity_care_loss"},
        {"query_template": 'site:.org "pharmacy closure" "prescription" after:{after} before:{before}', "source_family": "nonprofit_news", "category": "pharmacy_access_pressure"},
        {"query_template": 'site:.org "Medicaid" "coverage loss" "patients" after:{after} before:{before}', "source_family": "state_policy_news", "category": "coverage_disruption"},
    ],
    "exclude_domains": list(DEFAULT_EXCLUDE_DOMAINS),
}

PRESSURE_TYPE_RULES: list[tuple[str, tuple[str, ...], str, str]] = [
    ("hospital_closure", ("hospital closure", "hospital closes", "hospital shutting down"), "hospital_operations_signal", "Hospital / Clinic Operations Signals"),
    ("maternity_care_loss", ("maternity ward", "labor and delivery", "ob unit", "obstetrics", "maternity care"), "maternity_family_signal", "Maternity / Family Care Signals"),
    ("public_health_capacity_cut", ("public health department cuts", "reduced services", "capacity cuts"), "public_health_signal", "Public Health Capacity Signals"),
    ("service_line_cut", ("cuts services", "service line", "ending labor and delivery", "suspended services", "reduced services"), "hospital_operations_signal", "Hospital / Clinic Operations Signals"),
    ("pharmacy_access_pressure", ("pharmacy closure", "prescription access", "drugstore closure"), "clinic_operations_signal", "Hospital / Clinic Operations Signals"),
    ("clinic_access_strain", ("clinic closure", "reduced hours", "appointment backlog", "no longer accepting patients", "unable to staff"), "clinic_operations_signal", "Hospital / Clinic Operations Signals"),
    ("rural_access_strain", ("rural hospital", "travel farther", "rural access"), "rural_access_signal", "Rural Access Signals"),
    ("er_crowding_or_diversion", ("er diversion", "diverting ambulances", "er boarding", "crowding", "long wait times"), "emergency_ems_signal", "Emergency / EMS Signals"),
    ("coverage_disruption", ("coverage loss", "coverage disruption", "insurance coverage loss"), "insurance_affordability_signal", "Insurance / Affordability Signals"),
    ("medicaid_access_pressure", ("medicaid cuts", "medicaid access", "medicaid coverage"), "insurance_affordability_signal", "Insurance / Affordability Signals"),
    ("medical_debt_or_affordability", ("medical debt", "medical bills", "out-of-pocket", "affordability"), "insurance_affordability_signal", "Insurance / Affordability Signals"),
    ("staffing_shortage_access", ("staffing shortage", "unable to staff", "short staffed"), "clinic_operations_signal", "Hospital / Clinic Operations Signals"),
    ("ambulance_or_ems_strain", ("ambulance delay", "ems coverage", "ems strain"), "emergency_ems_signal", "Emergency / EMS Signals"),
]

PRESSURE_REASON_BY_TYPE = {
    "hospital_closure": "Hospital financing pressure can reduce access even before a formal closure occurs.",
    "service_line_cut": "Service-line cuts can narrow the care a local facility can provide.",
    "rural_access_strain": "Rural access strain can force people to travel farther for routine care.",
    "er_crowding_or_diversion": "ER crowding or diversion can delay urgent treatment and redirect patients elsewhere.",
    "clinic_access_strain": "A local clinic closure can mean longer travel, fewer appointment options, or delayed routine care.",
    "maternity_care_loss": "Loss of local labor and delivery services can force patients to travel farther for time-sensitive care.",
    "coverage_disruption": "Coverage disruption can delay care or create new out-of-pocket burdens.",
    "medicaid_access_pressure": "Medicaid access pressure can make care harder to afford or keep.",
    "medical_debt_or_affordability": "Medical debt or affordability pressure can cause people to skip care or delay treatment.",
    "staffing_shortage_access": "Staffing shortages can limit appointment availability and slow access to care.",
    "pharmacy_access_pressure": "Pharmacy access pressure can make it harder to fill prescriptions on time.",
    "public_health_capacity_cut": "Public-health capacity cuts can weaken local prevention and response systems.",
    "ambulance_or_ems_strain": "Ambulance or EMS strain can slow urgent response when minutes matter.",
}

PRESSURE_AFFECTION_GROUPS = {
    "hospital_closure": ["patients", "rural communities", "hospital staff"],
    "service_line_cut": ["patients", "families", "care teams"],
    "rural_access_strain": ["patients", "rural communities"],
    "er_crowding_or_diversion": ["patients", "emergency responders"],
    "clinic_access_strain": ["patients", "families", "clinic staff"],
    "maternity_care_loss": ["pregnant patients", "families", "maternity patients"],
    "coverage_disruption": ["patients", "insured households"],
    "medicaid_access_pressure": ["Medicaid enrollees", "patients"],
    "medical_debt_or_affordability": ["patients", "households with medical bills"],
    "staffing_shortage_access": ["patients", "clinic staff"],
    "pharmacy_access_pressure": ["patients", "people needing prescriptions"],
    "public_health_capacity_cut": ["patients", "local public health teams"],
    "ambulance_or_ems_strain": ["patients", "emergency responders"],
}

WRAPPER_TERMS = (
    "donation",
    "donations",
    "fundraiser",
    "fundraising",
    "charity",
    "gala",
    "ribbon cutting",
    "ribbon-cutting",
    "grand opening",
    "marketing",
    "sponsored",
    "advertorial",
    "award",
    "recognition",
    "honor",
    "grant",
)

NEGATIVE_TERMS = (
    "health tips",
    "symptoms",
    "recipe",
    "wellness",
    "new technology",
    "award",
    "honor",
    "recognition",
    "fundraiser",
    "donation",
    "gala",
    "ribbon cutting",
    "grand opening",
    "sponsored",
    "advertorial",
    "marketing",
)

POSITIVE_TERMS = (
    "closing",
    "closure",
    "will close",
    "closes on",
    "closing on",
    "shutting down",
    "cutting services",
    "ending labor and delivery",
    "suspended services",
    "diverting ambulances",
    "er boarding",
    "long wait times",
    "reduced hours",
    "appointment backlog",
    "no longer accepting patients",
    "patients travel farther",
    "coverage loss",
    "medicaid cut",
    "medical debt",
    "staffing shortage",
    "unable to staff",
    "pharmacy desert",
    "public health cuts",
    "public health department cuts",
    "reduce services",
    "waitlist",
    "reduced service",
    "longer travel",
    "same-day opening",
    "opens today",
    "opening today",
    "opened today",
    "reopens today",
    "reopening today",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _query_url(query: str) -> str:
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(canonical_url(value) if not value.startswith(("http://", "https://")) else value)
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _text_blob(candidate: dict[str, Any]) -> str:
    return _normalize_source_text(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("summary_or_snippet") or ""),
                str(candidate.get("evidence_text") or ""),
                str(candidate.get("publisher") or ""),
                str(candidate.get("source_name") or ""),
                str(candidate.get("location_name") or ""),
                str(candidate.get("state") or ""),
            )
            if part
        ),
        limit=1200,
    )


def _positive_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [term for term in POSITIVE_TERMS if term in lowered]


def _negative_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [term for term in NEGATIVE_TERMS if term in lowered]


def _wrapper_kind(candidate: dict[str, Any]) -> str:
    text = _text_blob(candidate)
    if any(term in text for term in ("donation", "fundraiser", "fundraising", "charity", "grant")):
        return "donation_page"
    if any(term in text for term in ("award", "recognition", "honor")):
        return "award_page"
    if any(term in text for term in ("ribbon cutting", "ribbon-cutting", "grand opening", "marketing", "sponsored", "advertorial")):
        return "marketing_page"
    return ""


def _is_google_news_wrapper(candidate: dict[str, Any]) -> bool:
    url = _normalize_url(_nonempty(candidate.get("url")))
    if not url:
        return False
    return urllib.parse.urlsplit(url).netloc.lower() == "news.google.com"


def _entity_phrases(candidate: dict[str, Any]) -> list[str]:
    text = _normalize_source_text(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("source_name") or ""),
                str(candidate.get("publisher") or ""),
                str(candidate.get("location_name") or ""),
                str(candidate.get("state") or ""),
            )
            if part
        ),
        limit=700,
    )
    phrases: list[str] = []
    pattern = re.compile(r"\b(?:[A-Z][\w&.'-]*|[A-Z]{2,})(?:\s+(?:[A-Z][\w&.'-]*|[A-Z]{2,})){1,5}\b")
    for match in pattern.finditer(text):
        phrase = _normalize_source_text(match.group(0))
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _secondary_queries_from_wrapper(candidate: dict[str, Any]) -> list[str]:
    wrapper_kind = _wrapper_kind(candidate)
    if not wrapper_kind:
        return []
    text = _text_blob(candidate)
    pressure_hits = _positive_hits(text)
    if not pressure_hits:
        pressure_hits = ["patients travel farther", "clinic closing", "ER diversion"]
    entities = _entity_phrases(candidate)
    location_phrases = []
    for part in (candidate.get("location_name"), candidate.get("state")):
        phrase = _normalize_source_text(str(part or ""))
        if phrase and phrase not in location_phrases:
            location_phrases.append(phrase)
    queries: list[str] = []
    for entity in entities[:3]:
        for clue in pressure_hits[:2]:
            queries.append(f'"{entity}" "{clue}"')
    for location in location_phrases[:2]:
        for clue in pressure_hits[:2]:
            queries.append(f'"{location}" "{clue}"')
    if wrapper_kind == "donation_page":
        lead = entities[0] if entities else _normalize_source_text(str(candidate.get("publisher") or candidate.get("source_name") or ""))
        if lead:
            queries.append(f'"{lead}" hospital closure')
    elif wrapper_kind == "award_page":
        lead = entities[0] if entities else _normalize_source_text(str(candidate.get("publisher") or candidate.get("source_name") or ""))
        if lead:
            queries.append(f'"{lead}" patients travel farther')
    elif wrapper_kind == "marketing_page":
        lead = location_phrases[0] if location_phrases else _normalize_source_text(str(candidate.get("publisher") or candidate.get("source_name") or ""))
        if lead:
            queries.append(f'"{lead}" reduced hours')
    return list(dict.fromkeys(query for query in queries if query))


def _pressure_type_from_text(text: str) -> str:
    lowered = text.lower()
    for pressure_type, terms, _role, _bucket in PRESSURE_TYPE_RULES:
        if any(term in lowered for term in terms):
            return pressure_type
    return "clinic_access_strain" if "clinic" in lowered else "hospital_closure"


def _pressure_role_and_bucket(pressure_type: str) -> tuple[str, str]:
    for row_pressure_type, _terms, role, bucket in PRESSURE_TYPE_RULES:
        if row_pressure_type == pressure_type:
            return role, bucket
    return "clinic_operations_signal", "Hospital / Clinic Operations Signals"


def _pressure_reason(pressure_type: str) -> str:
    return PRESSURE_REASON_BY_TYPE.get(pressure_type, "This source-backed signal suggests care access may be harder to maintain for the affected community.")


def _pressure_groups(pressure_type: str, candidate: dict[str, Any]) -> list[str]:
    groups = list(PRESSURE_AFFECTION_GROUPS.get(pressure_type, []))
    if groups:
        return groups
    text = _text_blob(candidate)
    if "patient" in text.lower():
        return ["patients"]
    return ["patients", "local communities"]


def _freshness_status(published_at: str, edition_date: str, max_age_days: int = 7) -> tuple[str, str, str]:
    if not published_at:
        return "unknown", "retrieved_at", ""
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown", "published_at", published_at[:10]
    try:
        edition = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        edition = datetime.now(timezone.utc)
    days_old = (edition - published.astimezone(timezone.utc)).days
    if days_old <= max_age_days:
        return "current", "published_at", published.astimezone(timezone.utc).date().isoformat()
    return "stale", "published_at", published.astimezone(timezone.utc).date().isoformat()


def load_care_line_discovery_queries(root: Path) -> dict[str, Any]:
    path = root / "data" / "dispatches" / "care-line" / "discovery_queries.json"
    repo_path = Path(__file__).resolve().parents[2] / "data" / "dispatches" / "care-line" / "discovery_queries.json"
    if not path.exists():
        path = repo_path
    if not path.exists():
        return DEFAULT_QUERY_CONFIG
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    queries = [row for row in payload.get("queries") or [] if isinstance(row, dict)]
    date_bounded_queries = [row for row in payload.get("date_bounded_queries") or [] if isinstance(row, dict)]
    exclude_domains = [str(item).strip().lower() for item in payload.get("exclude_domains") or [] if str(item).strip()]
    if not queries and not date_bounded_queries:
        return DEFAULT_QUERY_CONFIG
    return {
        "queries": queries,
        "date_bounded_queries": date_bounded_queries,
        "exclude_domains": exclude_domains or list(DEFAULT_EXCLUDE_DOMAINS),
    }


def _expand_date_bounded_queries(date: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        day = datetime.strptime(validate_date(date), "%Y-%m-%d").date()
    except ValueError:
        return []
    after = (day - timedelta(days=1)).isoformat()
    before = (day + timedelta(days=1)).isoformat()
    expanded: list[dict[str, Any]] = []
    for row in rows:
        template = str(row.get("query_template") or "").strip()
        if not template:
            continue
        expanded.append(
            {
                "query_template": template,
                "query": template.format(after=after, before=before),
                "source_family": str(row.get("source_family") or "local_news"),
                "category": str(row.get("category") or "current"),
                "after": after,
                "before": before,
                "date_bounded": True,
            }
        )
    return expanded


def _search_queries(date: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    queries = [row for row in config.get("queries") or [] if isinstance(row, dict)]
    if not queries:
        queries = [row for row in DEFAULT_QUERY_CONFIG["queries"] if isinstance(row, dict)]
    expanded: list[dict[str, Any]] = []
    for row in queries:
        template = str(row.get("query_template") or "").strip()
        if not template:
            continue
        expanded.append(
            {
                "query_template": template,
                "query": template,
                "source_family": str(row.get("source_family") or "local_news"),
                "category": str(row.get("category") or "current"),
                "date_bounded": False,
            }
        )
    expanded.extend(_expand_date_bounded_queries(date, [row for row in config.get("date_bounded_queries") or [] if isinstance(row, dict)]))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in expanded:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for row in rows:
        record_id = str(row.get("source_record_id") or "").strip()
        url = _normalize_url(str(row.get("url") or ""))
        if record_id and record_id in seen_ids:
            continue
        if url and url in seen_urls:
            continue
        if record_id:
            seen_ids.add(record_id)
        if url:
            seen_urls.add(url)
        deduped.append(row)
    return deduped


def _known_source_sets(root: Path, edition_date: str) -> dict[str, set[str]]:
    rows = load_manual_source_records(root, edition_date)
    known_urls: set[str] = set()
    known_ids: set[str] = set()
    known_domains: set[str] = set()
    for row in rows:
        url = _normalize_url(str(row.get("url") or ""))
        if url:
            known_urls.add(url)
            known_domains.add(urllib.parse.urlsplit(url).netloc.lower())
        record_id = str(row.get("source_record_id") or "").strip()
        if record_id:
            known_ids.add(record_id)
    return {"known_urls": known_urls, "known_ids": known_ids, "known_domains": known_domains}


def classify_care_line_discovery_candidate(
    candidate: dict[str, Any],
    *,
    known_status: str,
    known_local_domain: bool = False,
) -> dict[str, Any]:
    text = _text_blob(candidate)
    positive_hits = _positive_hits(text)
    negative_hits = _negative_hits(text)
    wrapper_kind = _nonempty(candidate.get("wrapper_kind") or _wrapper_kind(candidate))
    secondary_queries_generated = list(candidate.get("secondary_queries_generated") or _secondary_queries_from_wrapper(candidate))
    pressure_type = str(candidate.get("pressure_type") or _pressure_type_from_text(text)).strip()
    source_role, public_bucket = _pressure_role_and_bucket(pressure_type)
    direct_pressure = bool(positive_hits)
    google_news_wrapper = _is_google_news_wrapper(candidate)
    wrapper_candidate = bool(wrapper_kind or google_news_wrapper)
    source_traceability_role = "article_url"
    extraction_quality = str(candidate.get("extraction_quality") or "high")
    source_freshness_status = str(candidate.get("source_freshness_status") or "current")
    if wrapper_candidate:
        source_role = "discovery_lead"
        source_traceability_role = "wrapper_url"
    if negative_hits and not direct_pressure:
        extraction_quality = "low"
    score = 0
    if direct_pressure:
        score += 6
    score += min(4, len(positive_hits))
    if known_local_domain:
        score += 1
    if wrapper_candidate:
        score -= 2
    if negative_hits and not direct_pressure:
        score -= 3
    if known_status in {"already_included", "already_excluded", "duplicate"}:
        classification = "duplicate_or_known"
    elif wrapper_candidate:
        classification = "needs_review"
    elif direct_pressure and score >= 4:
        classification = "likely_qualifying"
    elif negative_hits and not direct_pressure:
        classification = "likely_resource_only"
    elif score <= 1:
        classification = "likely_resource_only"
    else:
        classification = "needs_review"
    if direct_pressure and any(term in text.lower() for term in ("health tips", "symptoms", "recipe", "new technology")):
        classification = "likely_resource_only"
    if wrapper_candidate and direct_pressure:
        classification = "needs_review"
    current_signal = direct_pressure and not wrapper_candidate and source_freshness_status == "current" and known_status not in {"already_included", "already_excluded", "duplicate"}
    public_eligible = bool(current_signal)
    reason_bits = []
    if positive_hits:
        reason_bits.append("positive pressure terms: " + ", ".join(positive_hits[:4]))
    if negative_hits:
        reason_bits.append("hard negative terms: " + ", ".join(negative_hits[:4]))
    if wrapper_kind:
        reason_bits.append(f"wrapper lead detected: {wrapper_kind}")
    if google_news_wrapper:
        reason_bits.append("Google News wrapper lead detected")
    if known_status == "already_included":
        reason_bits.append("already included")
    elif known_status == "already_excluded":
        reason_bits.append("already excluded")
    elif known_status == "duplicate":
        reason_bits.append("duplicate of known source")
    elif known_status == "known_domain_new_article":
        reason_bits.append("known domain new article")
    elif known_status == "unknown_domain_new_article":
        reason_bits.append("unknown domain new article")
    if wrapper_candidate and secondary_queries_generated:
        reason_bits.append("wrapper framing generated secondary queries for follow-up discovery")
    if classification == "likely_resource_only" and not direct_pressure:
        reason_bits.append("resource/marketing/advice framing without direct pressure evidence")
    return {
        "classification": classification,
        "score": score,
        "reason": "; ".join(reason_bits) if reason_bits else "no strong pressure markers",
        "known_status": known_status,
        "source_role": source_role,
        "wrapper_candidate": wrapper_candidate,
        "public_eligible": public_eligible,
        "wrapper_kind": wrapper_kind,
        "secondary_queries_generated": secondary_queries_generated,
        "pressure_type": pressure_type,
        "public_inclusion_bucket": public_bucket,
        "source_traceability_role": source_traceability_role,
        "extraction_quality": extraction_quality,
        "source_freshness_status": source_freshness_status,
    }


def _fetch_url(fetcher: Any, url: str) -> tuple[bytes, str]:
    try:
        return fetcher(url, timeout=15), ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


def _inspect_article(fetcher: Any, url: str) -> dict[str, str]:
    payload, error = _fetch_url(fetcher, url)
    if error or not payload:
        return {
            "retrieved_at": _utc_now(),
            "published_at": "",
            "page_metadata_date": "",
            "page_title": "",
            "page_summary_or_snippet": "",
            "page_evidence_text": "",
            "page_evidence_text_basis": "",
            "page_fetch_error": error,
        }
    evidence = _extract_page_evidence(payload)
    page_metadata_date = _extract_page_metadata_date(payload)
    published_at = page_metadata_date[:10] if page_metadata_date else ""
    return {
        "retrieved_at": _utc_now(),
        "published_at": published_at,
        "page_metadata_date": page_metadata_date,
        "page_title": evidence.get("title") or "",
        "page_summary_or_snippet": evidence.get("summary_or_snippet") or "",
        "page_evidence_text": evidence.get("evidence_text") or "",
        "page_evidence_text_basis": evidence.get("evidence_text_basis") or "",
        "page_fetch_error": "",
    }


def _candidate_id(url: str, publisher: str, source_family: str) -> str:
    digest = hashlib.sha1(_normalize_url(url).encode("utf-8")).hexdigest()[:12]
    prefix = re.sub(r"[^a-z0-9]+", "-", (publisher or source_family or "care-line").lower()).strip("-")
    return f"{prefix or 'care-line'}-{digest}"


def _parse_pub_date(value: str) -> str:
    raw = _nonempty(value)
    if not raw:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def discover_care_line_sources(
    root: Path,
    date: str,
    *,
    fetcher: Any | None = None,
    max_results_per_query: int = 10,
    max_queries: int | None = None,
    max_candidates: int | None = None,
    follow_up_queries: Iterable[Mapping[str, Any]] | None = None,
    incident_seeds: Iterable[Mapping[str, Any]] | None = None,
    write: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    edition_date = validate_date(date)
    config = load_care_line_discovery_queries(root)
    queries = [dict(row) for row in list(follow_up_queries or []) if str(row.get("query") or "").strip()]
    incident_seed_reports: list[dict[str, Any]] = []
    incident_seed_rows: list[dict[str, Any]] = []
    for seed in list(incident_seeds or []):
        if not isinstance(seed, Mapping):
            continue
        incident_result = build_incident_follow_up_queries(seed, dispatch_slug="care-line")
        incident_seed_reports.append({k: incident_result.get(k) for k in ("seed_id", "place", "incident_type", "source_url", "source_date", "trigger_reason", "query_count", "ok")})
        if incident_result.get("ok"):
            incident_seed_rows.extend([dict(row) for row in incident_result.get("queries") or [] if str(row.get("query") or "").strip()])
    queries.extend(incident_seed_rows)
    queries.extend(_search_queries(edition_date, config))
    exclude_domains = {str(item).strip().lower() for item in config.get("exclude_domains") or [] if str(item).strip()}
    known = _known_source_sets(root, edition_date)
    fetch = fetcher or _fetch
    discovered_at = _utc_now()
    query_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    secondary_query_terms: list[str] = []
    executed_queries: list[str] = []
    query_counts: Counter[str] = Counter()
    source_family_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    exclusion_reason_counts: Counter[str] = Counter()
    wrapper_candidate_count = 0
    current_signal_count = 0
    public_signal_count = 0
    public_rows: list[dict[str, Any]] = []
    discovered_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for query_row in queries:
        if max_queries is not None and len(executed_queries) >= max_queries:
            break
        query = str(query_row.get("query") or "").strip()
        if not query or query in executed_queries:
            continue
        executed_queries.append(query)
        query_url = _query_url(query)
        payload, error = _fetch_url(fetch, query_url)
        if error or not payload:
            query_rows.append({"query": query, "url": query_url, "error": error or "empty response", "results": 0})
            continue
        try:
            rss_items = _parse_rss_items(payload)
        except Exception as exc:  # noqa: BLE001
            query_rows.append({"query": query, "url": query_url, "error": f"{type(exc).__name__}: {exc}", "results": 0})
            continue
        query_rows.append({"query": query, "url": query_url, "error": "", "results": len(rss_items)})
        for item in rss_items[:max_results_per_query]:
            if max_candidates is not None and len(raw_rows) >= max_candidates:
                break
            candidate_url = _normalize_url(_nonempty(item.get("url")))
            if not candidate_url:
                continue
            domain = urllib.parse.urlsplit(candidate_url).netloc.lower()
            if domain in exclude_domains:
                continue
            title = _nonempty(item.get("title")) or candidate_url
            summary = _nonempty(item.get("summary_or_snippet"))
            publisher = _nonempty(item.get("publisher") or domain)
            published_at = _parse_pub_date(_nonempty(item.get("published_at")))
            article = _inspect_article(fetch, candidate_url)
            page_title = _nonempty(article.get("page_title"))
            page_summary = _nonempty(article.get("page_summary_or_snippet"))
            page_evidence = _nonempty(article.get("page_evidence_text"))
            evidence_text = page_evidence or summary or title
            article_published_at = _nonempty(article.get("published_at")) or published_at[:10]
            source_freshness_status, date_basis, source_published_date = _freshness_status(article_published_at or published_at, edition_date)
            pressure_type = _pressure_type_from_text(" ".join(part for part in (title, summary, page_title, page_summary, page_evidence) if part))
            wrapper_kind = _wrapper_kind({"title": title, "summary_or_snippet": summary, "evidence_text": page_evidence, "publisher": publisher, "source_name": publisher})
            secondary_queries = _secondary_queries_from_wrapper({"title": title, "summary_or_snippet": summary, "evidence_text": page_evidence, "publisher": publisher, "source_name": publisher, "location_name": domain})
            if candidate_url in known["known_urls"]:
                known_status = "already_included"
            elif domain in known["known_domains"]:
                known_status = "known_domain_new_article"
            else:
                known_status = "unknown_domain_new_article"
            if candidate_url in seen_urls:
                known_status = "duplicate"
            known_local_domain = bool(domain) and domain in known["known_domains"]
            classification = classify_care_line_discovery_candidate(
                {
                    "url": candidate_url,
                    "title": title,
                    "summary_or_snippet": summary,
                    "evidence_text": evidence_text,
                    "publisher": publisher,
                    "source_name": publisher,
                    "location_name": domain,
                    "state": "",
                    "wrapper_kind": wrapper_kind,
                    "secondary_queries_generated": secondary_queries,
                    "pressure_type": pressure_type,
                "source_freshness_status": source_freshness_status,
            },
                known_status=known_status,
                known_local_domain=known_local_domain,
            )
            row = {
                "source_record_id": _candidate_id(candidate_url, publisher, str(query_row.get("source_family") or "local_news")),
                "title": page_title or title,
                "url": candidate_url,
                "publisher": publisher,
                "published_at": article_published_at or published_at,
                "retrieved_at": article.get("retrieved_at") or discovered_at,
                "summary_or_snippet": page_summary or summary,
                "evidence_text": evidence_text,
                "evidence_text_basis": article.get("page_evidence_text_basis") or "rss_item_text",
                "pressure_signal": classification["classification"] == "likely_qualifying",
                "pressure_type": classification["pressure_type"],
                "pressure_reason": _pressure_reason(classification["pressure_type"]),
                "pressure_summary": _pressure_reason(classification["pressure_type"]),
                "source_family": str(query_row.get("source_family") or "local_news"),
                "source_role": classification["source_role"],
                "source_origin": "live_discovery",
                "registry_status": "non_registry_discovered_source",
                "source_traceability_role": classification["source_traceability_role"],
                "extraction_quality": classification["extraction_quality"],
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "affected_groups": _pressure_groups(classification["pressure_type"], {"title": title, "summary_or_snippet": summary, "evidence_text": evidence_text, "publisher": publisher}),
                "evidence_level": "reported_story" if classification["classification"] == "likely_qualifying" else "background context",
                "freshness_status": source_freshness_status,
                "freshness_role": "current_signal" if source_freshness_status == "current" and classification["classification"] == "likely_qualifying" else "stale_current_signal" if source_freshness_status == "stale" else "background_context",
                "source_published_date": source_published_date,
                "date_basis": date_basis,
                "source_freshness_date_basis": date_basis,
                "source_public_story_eligible": classification["public_eligible"],
                "primary_eligible": classification["public_eligible"],
                "primary_disqualification_reason": "" if classification["public_eligible"] else classification["reason"],
                "claim_supported": page_title or title,
                "limitations": "Traceable article URL reviewed from live discovery.",
                "included": classification["public_eligible"],
                "excluded": not classification["public_eligible"],
                "exclusion_reason": "" if classification["public_eligible"] else ("stale_current_signal" if source_freshness_status == "stale" else "wrapper_candidate" if classification["wrapper_candidate"] else "resource_only_baseline"),
                "qualifies_for_public_inclusion": classification["public_eligible"],
                "public_inclusion_bucket": classification["public_inclusion_bucket"],
                "included_as_lead": False,
                "included_as_hospital_operations_signal": classification["source_role"] == "hospital_operations_signal",
                "included_as_insurance_affordability_signal": classification["source_role"] == "insurance_affordability_signal",
                "included_as_rural_access_signal": classification["source_role"] == "rural_access_signal",
                "included_as_maternity_family_signal": classification["source_role"] == "maternity_family_signal",
                "included_as_maternity_signal": classification["source_role"] == "maternity_family_signal",
                "included_as_emergency_ems_signal": classification["source_role"] == "emergency_ems_signal",
                "included_as_public_health_signal": classification["source_role"] == "public_health_signal",
                "included_as_additional_signal": classification["source_role"] not in {"hospital_operations_signal", "insurance_affordability_signal", "rural_access_signal", "maternity_family_signal", "emergency_ems_signal", "public_health_signal"},
                "context_only": not classification["public_eligible"],
                "confidence": "high" if classification["classification"] == "likely_qualifying" else "medium" if classification["classification"] == "needs_review" else "low",
                "wrapper_candidate": classification["wrapper_candidate"],
                "secondary_queries_generated": secondary_queries,
            }
            row["source_id"] = row["source_record_id"]
            seen_urls.add(candidate_url)
            raw_rows.append(row)
            source_family_counts[row["source_family"]] += 1
            if wrapper_kind:
                wrapper_candidate_count += 1
            if source_freshness_status == "current" and classification["classification"] == "likely_qualifying":
                current_signal_count += 1
            if classification["public_eligible"]:
                public_signal_count += 1
                public_rows.append(row)
            else:
                exclusion_reason_counts[row["exclusion_reason"] or "excluded"] += 1
            if classification["wrapper_candidate"]:
                for secondary_query in secondary_queries:
                    if secondary_query not in secondary_query_terms:
                        secondary_query_terms.append(secondary_query)
            discovered_rows.append(row)
    discovered_rows = _dedupe_rows(discovered_rows)
    validation_errors = validate_manual_source_records(discovered_rows)
    if validation_errors:
        raise ValueError("Care Line discovered sources failed validation: " + "; ".join(validation_errors))
    if write and not dry_run:
        output_dir = root / "data" / "dispatches" / "care-line" / "sources" / edition_date
        _write_json(output_dir / DISCOVERY_OUTPUT_FILE, discovered_rows)
    diagnostics = care_line_review_diagnostics(discovered_rows)
    report = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "discovered_sources_path": str(root / "data" / "dispatches" / "care-line" / "sources" / edition_date / DISCOVERY_OUTPUT_FILE),
        "discovery_report_path": str(root / "data" / "dispatches" / "care-line" / "sources" / edition_date / DISCOVERY_REPORT_FILE),
        "query_count": len(executed_queries),
        "query_rows": query_rows,
        "incident_seed_count": len(incident_seed_reports),
        "incident_seed_query_count": len(incident_seed_rows),
        "incident_seed_diagnostics": incident_seed_reports,
        "source_count": len(discovered_rows),
        "public_signal_count": public_signal_count,
        "claim_count": public_signal_count,
        "wrapper_candidate_count": wrapper_candidate_count,
        "secondary_query_count": len(secondary_query_terms),
        "source_families": diagnostics["source_families"],
        "pressure_source_count_by_family": diagnostics["pressure_source_count_by_family"],
        "pressure_source_count_by_state": diagnostics["pressure_source_count_by_state"],
        "exclusion_reason_counts": diagnostics["exclusion_reason_counts"],
        "exclusion_reason_summary": diagnostics["exclusion_reason_summary"],
        "qualified_but_not_public_count": diagnostics["qualified_but_not_public_count"],
        "discovery_gap_check": diagnostics,
        "no_current_update_summary": no_current_update_summary(discovered_rows),
    }
    if write and not dry_run:
        output_dir = root / "data" / "dispatches" / "care-line" / "sources" / edition_date
        _write_json(output_dir / DISCOVERY_REPORT_FILE, report)
    return report


def run_care_line_discovery_gap_check(
    root: Path,
    date: str,
    *,
    fetcher: Any | None = None,
    max_results_per_query: int = 10,
    max_queries: int | None = None,
    max_candidates: int | None = None,
    follow_up_queries: Iterable[Mapping[str, Any]] | None = None,
    incident_seeds: Iterable[Mapping[str, Any]] | None = None,
    write: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    return discover_care_line_sources(
        root,
        date,
        fetcher=fetcher,
        max_results_per_query=max_results_per_query,
        max_queries=max_queries,
        max_candidates=max_candidates,
        follow_up_queries=follow_up_queries,
        incident_seeds=incident_seeds,
        write=write,
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Discover Care Line source-backed healthcare access signals.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = discover_care_line_sources(
        Path.cwd(),
        args.date,
        max_results_per_query=args.max_results_per_query,
        max_queries=args.max_queries,
        max_candidates=args.max_candidates,
        write=not args.no_write,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
