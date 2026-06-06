from __future__ import annotations

import hashlib
import html
import json
import os
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INVALID_XML_ENTITY_RE = re.compile(r"&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9A-Fa-f]+);)")
RSS_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)
ATOM_ENTRY_RE = re.compile(r"<entry\b[^>]*>(.*?)</entry>", re.IGNORECASE | re.DOTALL)
TEST_MODE_ENV_VAR = "BLUEFERN_TEST_MODE"

DEFAULT_LOCATION = "United States"
DEFAULT_STATE = "US"
LOCAL_FAMILIES = {
    "local_news",
    "public_radio",
    "nonprofit_news",
    "state_policy_news",
    "state_official",
    "local_reporting",
    "food_bank_provider",
    "school_meals_child_nutrition",
    "senior_meals",
    "disaster_emergency",
    "rural_access",
}
BASELINE_FAMILIES = {"economic_data"}
PRESSURE_FAMILIES = {
    "national_news",
    "local_news",
    "public_radio",
    "nonprofit_news",
    "state_policy_news",
    "food_bank_provider",
    "school_meals_child_nutrition",
    "senior_meals",
    "state_official",
    "federal_official",
    "disaster_emergency",
}

FAMILY_TO_CATEGORY: dict[str, str] = {
    "national_news": "context / monitoring only",
    "local_news": "elevated demand",
    "public_radio": "elevated demand",
    "nonprofit_news": "elevated demand",
    "state_policy_news": "benefit disruption",
    "federal_official": "benefit disruption",
    "local_reporting": "elevated demand",
    "school_meals_child_nutrition": "summer meal / child nutrition",
    "senior_meals": "senior hunger",
    "rural_access": "rural access",
    "disaster_emergency": "acute strain / service disruption",
    "economic_data": "context / monitoring only",
}

TAG_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("snap", "benefit", "ebt"), ("SNAP", "benefits")),
    (("summer", "school meal", "child nutrition"), ("summer meals", "child hunger", "school meals")),
    (("senior", "older adult", "meals on wheels"), ("senior hunger", "meal delivery")),
    (("food bank", "pantry"), ("food banks", "pantry capacity")),
    (("disaster", "emergency", "hurricane", "flood", "wildfire"), ("disaster response", "service disruption")),
    (("rural",), ("rural access",)),
    (("insecurity", "hardship", "poverty"), ("household food insecurity",)),
]

PRESSURE_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
        (
            "demand strain",
            (
                "demand is up",
                "demand increased",
                "increased demand",
                "preparing for increased demand",
                "rising demand",
                "record demand",
                "more families",
                "longer lines",
                "pantry lines",
                "food bank demand",
                "food pantry demand",
                "emergency food demand",
                "struggling to meet demand",
            ),
        ),
    (
        "service reduction",
        (
            "reduced hours",
            "cut hours",
            "limited distribution",
            "fewer distributions",
                "closed pantry",
                "pantry closure",
                "reduced capacity",
                "smaller boxes",
                "supply shortage",
                "shelves bare",
                "empty shelves",
                "low inventory",
                "federal cuts",
                "food programs",
            ),
        ),
    (
        "benefit disruption",
        (
            "snap delay",
            "benefit delay",
            "benefits delay",
            "snap benefits delayed",
            "snap benefits face shutdown pause",
            "benefits face shutdown pause",
            "shutdown pause",
            "ebt outage",
            "benefits cut",
            "snap cut",
            "wic disruption",
            "recertification backlog",
            "application backlog",
        ),
    ),
    (
        "child meal gap",
        (
            "summer meal gap",
            "school meal gap",
            "free meals ended",
            "meal site closure",
            "sun bucks delay",
            "children missing meals",
        ),
    ),
    (
        "senior meal strain",
        (
            "meals on wheels waitlist",
            "senior meal waitlist",
            "home-delivered meal waitlist",
            "senior hunger",
            "unable to serve seniors",
        ),
    ),
    (
        "access gap",
        (
            "food desert",
            "grocery closure",
            "rural grocery closure",
            "no nearby grocery",
            "transportation barrier",
            "rural food access",
        ),
    ),
    (
        "household hardship",
        (
            "skipping meals",
            "unable to afford food",
            "food hardship",
            "food insecurity",
            "hunger increased",
            "families going hungry",
            "medical bills",
            "medical cost",
            "medical costs",
            "medical debt",
            "health care bills",
            "health-care bills",
            "out-of-pocket",
            "insurance burden",
            "prescription costs",
        ),
    ),
    (
        "disaster disruption",
        (
            "emergency food distribution",
            "disaster food assistance",
            "d-snap",
            "food/water distribution",
            "storm disrupted food access",
            "wildfire food assistance",
            "flood food assistance",
        ),
    ),
]

NEGATIVE_FILTERS = (
    "recipe",
    "restaurant review",
    "menu",
    "cooking tips",
    "chef",
    "grocery sale",
    "food festival",
    "charity gala",
    "fundraiser only",
    "volunteer opportunity only",
    "generic donation drive without demand",
)

DEFAULT_POSITIVE_KEYWORDS = {
    "national_news": ["food insecurity", "hunger", "food bank", "pantry", "SNAP", "EBT", "WIC"],
    "local_news": ["food bank", "pantry", "hunger", "food insecurity", "SNAP", "meal site", "grocery closure"],
    "public_radio": ["food insecurity", "hunger", "SNAP", "pantry", "food bank"],
    "nonprofit_news": ["food bank", "pantry", "demand", "shortage", "waitlist", "hunger"],
    "state_policy_news": ["SNAP", "WIC", "benefit", "delay", "outage", "application backlog"],
    "food_bank_provider": ["demand", "shortage", "waitlist", "hours", "capacity", "inventory"],
    "school_meals_child_nutrition": ["summer meals", "school meals", "meal site", "children", "SUN Bucks"],
    "senior_meals": ["Meals on Wheels", "senior", "waitlist", "home-delivered", "meal delivery"],
    "state_official": ["SNAP", "WIC", "benefit", "delay", "outage", "D-SNAP", "summer meal"],
    "federal_official": ["SNAP", "WIC", "benefit", "delay", "outage", "summer meal", "D-SNAP"],
    "disaster_emergency": ["D-SNAP", "disaster", "food assistance", "emergency food", "distribution"],
    "economic_data": ["food insecurity", "food sufficiency", "household hardship"],
}

DEFAULT_NEGATIVE_KEYWORDS = [
    "recipe",
    "restaurant",
    "menu",
    "chef",
    "sale",
    "festival",
    "gala",
    "volunteer",
    "donation drive",
]

DEFAULT_AFFECTED_GROUP_KEYWORDS = {
    "children": ["children", "child", "students", "families with children"],
    "seniors": ["senior", "older adult", "home-delivered"],
    "SNAP households": ["snap", "ebt"],
    "WIC households": ["wic"],
    "low-income households": ["low-income", "low income", "poverty", "families"],
    "rural residents": ["rural"],
    "disaster-affected households": ["disaster", "hurricane", "flood", "wildfire", "storm"],
}


def food_line_test_mode_enabled() -> bool:
    value = os.getenv(TEST_MODE_ENV_VAR, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def food_line_network_guard(url: str, timeout: int = 15) -> bytes:
    raise urllib.error.URLError(f"Food Line network access disabled in test mode: {url}")


def resolve_food_line_fetcher(fetcher: Any | None) -> Any:
    if fetcher is not None:
        return fetcher
    if food_line_test_mode_enabled():
        return food_line_network_guard
    return _fetch

DEFAULT_MAX_AGE_DAYS = {
    "rss": 7,
    "feed": 7,
    "api": 14,
    "page": 14,
}

DEFAULT_EXTRACTION_QUALITY = {
    "rss": "high",
    "feed": "high",
    "api": "high",
    "page": "medium",
}

DEFAULT_EXPECTED_TEXT_BASIS = {
    "rss": "rss_summary",
    "feed": "rss_summary",
    "api": "api_json",
    "page": "page_text",
}

VALID_CANDIDATE_STATUSES = {
    "candidate",
    "tested_good",
    "tested_weak",
    "tested_failed",
    "enabled",
    "rejected",
    "quarantined",
    "archived",
    "promoted",
}

SOURCE_QUALITY_TIER_THRESHOLDS = (
    ("high", 75),
    ("medium", 45),
    ("low", 15),
)

GENERIC_PRESSURE_SUMMARIES = {
    "source-backed food insecurity context signal",
    "food insecurity context signal",
    "source-backed pressure signal",
    "elevated demand signal",
    "context signal",
}

SUPPORTED_EVIDENCE_BASIS = {
    "manual_source_text",
    "page_text_excerpt",
    "page_title_and_meta",
    "page_title_only",
    "rss_item_text",
}

SOURCE_PURPOSE_VALUES = {
    "current_news",
    "official_notice",
    "provider_update",
    "data_release",
    "research_report",
    "disaster_alert",
    "evergreen_context",
    "donation_page",
    "resource_page",
    "program_description",
    "unknown",
}
SOURCE_PURPOSE_CURRENT_VALUES = {
    "current_news",
    "official_notice",
    "provider_update",
    "data_release",
    "research_report",
    "disaster_alert",
}
SOURCE_PURPOSE_EVERGREEN_VALUES = {
    "evergreen_context",
    "donation_page",
    "resource_page",
    "program_description",
}
SOURCE_PURPOSE_NON_PROMOTABLE_REASONS = {
    "donation_page": "donation page is not current pressure evidence",
    "evergreen_context": "evergreen context is not current pressure evidence",
    "resource_page": "resource page is not current pressure evidence",
    "program_description": "program description is not current pressure evidence",
    "unknown": "source purpose is unclear",
}
SOURCE_PURPOSE_DONATION_TERMS = (
    "donate",
    "donation",
    "monthly giving",
    "recurring donation",
    "recurring donations",
    "memorial donation",
    "memorial donations",
    "tribute gift",
    "tribute gifts",
    "give now",
    "ways to give",
    "fundraiser",
    "charity campaign",
)
SOURCE_PURPOSE_EVERGREEN_TERMS = (
    "hunger & poverty in the united states",
    "hunger and poverty in the united states",
    "hunger facts",
    "what is food insecurity",
    "about hunger",
    "research overview",
    "issue explainer",
    "evergreen context",
    "food insecurity facts",
)
SOURCE_PURPOSE_RESOURCE_TERMS = (
    "find food",
    "find a food bank",
    "food bank directory",
    "food bank locator",
    "find help",
    "get help",
    "eligibility",
    "how to apply",
    "apply for benefits",
    "benefit information",
    "benefits information",
    "program description",
    "our programs",
)
SOURCE_PURPOSE_CURRENT_TERMS = (
    "update",
    "notice",
    "alert",
    "delay",
    "outage",
    "closure",
    "closed",
    "shortage",
    "disruption",
    "suspended",
    "temporarily closed",
    "waitlist",
    "limited hours",
    "reduced hours",
    "rising demand",
    "report",
    "release",
    "article",
    "story",
)
SOURCE_PURPOSE_DISASTER_TERMS = (
    "d-snap",
    "disaster",
    "emergency",
    "hurricane",
    "flood",
    "wildfire",
    "storm",
    "evacuation",
)


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n;]", value) if part.strip()]
    return []


def _keyword_hit(text: str, keywords: list[str] | tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(str(keyword).strip().lower() in lowered for keyword in keywords if str(keyword).strip())


def _pressure_type_for_text(text: str) -> str:
    lowered = text.lower()
    for pressure_type, needles in PRESSURE_TYPE_RULES:
        if any(needle in lowered for needle in needles):
            return pressure_type
    return "context only"


def _infer_affected_groups(text: str) -> list[str]:
    lowered = text.lower()
    groups: list[str] = []
    for group, keywords in DEFAULT_AFFECTED_GROUP_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            groups.append(group)
    return groups


def _freshness_role_for_dates(edition_date: str, published_at: str, max_age_days: int) -> str:
    if not published_at:
        return "dated_recent_signal" if max_age_days <= 7 else "fresh_daily_signal"
    try:
        edition_dt = datetime.strptime(edition_date, "%Y-%m-%d").date()
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
    except ValueError:
        return "dated_recent_signal"
    age_days = max(0, (edition_dt - published_dt).days)
    if age_days <= 1:
        return "fresh_daily_signal"
    return "dated_recent_signal"


def _freshness_status_for_dates(edition_date: str, published_at: str, max_age_days: int) -> tuple[str, str]:
    if not published_at:
        return "missing_source_published_date", "missing source published date"
    try:
        edition_dt = datetime.strptime(edition_date, "%Y-%m-%d").date()
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
    except ValueError:
        return "unparsed_source_published_date", "could not parse source published date"
    age_days = max(0, (edition_dt - published_dt).days)
    if age_days <= 1:
        return "fresh_daily_signal", ""
    if age_days <= max_age_days:
        return "fresh_recent_signal", ""
    return (
        "stale_outside_daily_window",
        f"published {published_dt.isoformat()} is {age_days} days before edition {edition_date}, outside the {max_age_days}-day window",
    )


def _normalize_registry_keywords(value: Any, default: list[str]) -> list[str]:
    items = _coerce_list(value)
    return items or list(default)


def _normalize_quality(value: Any, fallback: str) -> str:
    text = str(value or fallback or "unknown").strip().lower()
    return text if text in {"high", "medium", "low", "unknown"} else "unknown"


def _normalize_expected_text_basis(value: Any, fallback: str) -> str:
    text = str(value or fallback or "manual").strip().lower()
    return text if text in {"rss_summary", "rss_title", "page_text", "api_json", "manual"} else "manual"


def _normalize_source_purpose(value: Any, fallback: str = "unknown") -> str:
    text = str(value or fallback or "unknown").strip().lower()
    return text if text in SOURCE_PURPOSE_VALUES else "unknown"


def _current_or_evergreen_for_purpose(source_purpose: str) -> str:
    if source_purpose in SOURCE_PURPOSE_CURRENT_VALUES:
        return "current"
    if source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES:
        return "evergreen"
    return "unknown"


def _source_purpose_promotable(source_purpose: str) -> bool:
    return source_purpose in SOURCE_PURPOSE_CURRENT_VALUES


def _append_note(existing: str, note: str) -> str:
    current = str(existing or "").strip()
    addition = str(note or "").strip()
    if not addition:
        return current
    if not current:
        return addition
    if addition in current:
        return current
    return f"{current} | {addition}"


def _normalize_candidate_status(value: Any, default: str = "candidate") -> str:
    status = str(value or default or "candidate").strip().lower()
    return status if status in VALID_CANDIDATE_STATUSES else default


def _source_quality_tier(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    if score >= 15:
        return "low"
    return "quarantine"


def _source_quality_score(
    *,
    pressure_hits: int,
    item_count: int,
    useful_text_available: bool,
    fetch_failures: int,
    noise_score: int,
    rejection_count: int,
) -> int:
    score = 40
    score += min(25, pressure_hits * 12)
    score += 15 if useful_text_available else -20
    score += 12 if item_count else -20
    score -= min(20, fetch_failures * 8)
    score -= min(25, rejection_count * 4)
    score -= min(20, max(0, noise_score - 30) // 3)
    return max(0, min(100, score))


def _ensure_candidate_lifecycle_fields(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["status"] = _normalize_candidate_status(normalized.get("status"))
    for key in ("discovery_count", "test_count", "enable_count", "reject_count", "keep_candidate_count"):
        try:
            normalized[key] = int(normalized.get(key) or 0)
        except Exception:  # noqa: BLE001
            normalized[key] = 0
    normalized["first_discovered_at"] = str(normalized.get("first_discovered_at") or "").strip()
    normalized["last_discovered_at"] = str(normalized.get("last_discovered_at") or "").strip()
    normalized["last_tested_at"] = str(normalized.get("last_tested_at") or "").strip()
    normalized["last_recommendation"] = str(normalized.get("last_recommendation") or "").strip()
    normalized["last_recommendation_reason"] = str(normalized.get("last_recommendation_reason") or "").strip()
    try:
        normalized["source_quality_score"] = int(normalized.get("source_quality_score") or 0)
    except Exception:  # noqa: BLE001
        normalized["source_quality_score"] = 0
    normalized["source_quality_tier"] = str(normalized.get("source_quality_tier") or _source_quality_tier(int(normalized["source_quality_score"]))).strip().lower()
    normalized["auto_discovered"] = bool(normalized.get("auto_discovered", False))
    return normalized


def load_food_line_source_performance_history(root: Path) -> dict[str, dict[str, Any]]:
    data_root = root / "data" / "dispatches" / "food-line"
    repo_root = Path(__file__).resolve().parents[2] / "data" / "dispatches" / "food-line"
    path = data_root / "source_performance_history.json"
    if not path.exists():
        if food_line_test_mode_enabled():
            return {}
        path = repo_root / "source_performance_history.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for source_id, row in payload.items():
        if not isinstance(row, dict):
            continue
        normalized[str(source_id)] = {
            "runs_seen": int(row.get("runs_seen") or 0),
            "runs_fetched": int(row.get("runs_fetched") or 0),
            "fetch_failures": int(row.get("fetch_failures") or 0),
            "items_seen": int(row.get("items_seen") or 0),
            "verified_pressure_records": int(row.get("verified_pressure_records") or 0),
            "demoted_records": int(row.get("demoted_records") or 0),
            "rejected_records": int(row.get("rejected_records") or 0),
            "last_verified_pressure_at": str(row.get("last_verified_pressure_at") or "").strip(),
            "last_fetch_error": str(row.get("last_fetch_error") or "").strip(),
            "rolling_quality_score": int(row.get("rolling_quality_score") or 0),
        }
    return normalized


def save_food_line_source_performance_history(root: Path, payload: dict[str, dict[str, Any]]) -> Path:
    data_root = root / "data" / "dispatches" / "food-line"
    path = data_root / "source_performance_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def upsert_food_line_source_performance_history(
    root: Path,
    source_id: str,
    *,
    items_seen: int = 0,
    runs_fetched: int = 0,
    fetch_failure: str = "",
    verified_pressure_records: int = 0,
    demoted_records: int = 0,
    rejected_records: int = 0,
) -> dict[str, Any]:
    history = load_food_line_source_performance_history(root)
    row = dict(history.get(source_id) or {})
    row["runs_seen"] = int(row.get("runs_seen") or 0) + 1
    row["runs_fetched"] = int(row.get("runs_fetched") or 0) + int(runs_fetched)
    row["fetch_failures"] = int(row.get("fetch_failures") or 0) + (1 if fetch_failure else 0)
    row["items_seen"] = int(row.get("items_seen") or 0) + int(items_seen)
    row["verified_pressure_records"] = int(row.get("verified_pressure_records") or 0) + int(verified_pressure_records)
    row["demoted_records"] = int(row.get("demoted_records") or 0) + int(demoted_records)
    row["rejected_records"] = int(row.get("rejected_records") or 0) + int(rejected_records)
    if fetch_failure:
        row["last_fetch_error"] = fetch_failure
    if verified_pressure_records:
        row["last_verified_pressure_at"] = utc_now()
    row["rolling_quality_score"] = max(
        0,
        min(
            100,
            int(row.get("rolling_quality_score") or 0)
            + (verified_pressure_records * 12)
            - (demoted_records * 5)
            - (rejected_records * 3)
            - (8 if fetch_failure else 0),
        ),
    )
    history[source_id] = row
    save_food_line_source_performance_history(root, history)
    return row


def classify_food_line_source_purpose(row: dict[str, Any]) -> dict[str, str]:
    source_name = _normalize_source_text(" ".join(
        part
        for part in (
            str(row.get("source_name") or ""),
            str(row.get("title") or ""),
            str(row.get("title_fallback") or ""),
            str(row.get("summary_or_snippet") or ""),
            str(row.get("summary_fallback") or ""),
            str(row.get("evidence_text") or ""),
            str(row.get("candidate_reason") or ""),
            str(row.get("notes") or ""),
            str(row.get("publisher") or ""),
            str(row.get("url") or ""),
            str(row.get("candidate_url") or ""),
        )
        if part
    ), limit=1200).lower()
    family = str(row.get("source_family") or "").strip().lower()
    source_type = str(row.get("source_type") or "").strip().lower()
    url = str(row.get("url") or row.get("candidate_url") or "").strip().lower()
    source_purpose = "unknown"
    non_promotable_reason = SOURCE_PURPOSE_NON_PROMOTABLE_REASONS["unknown"]

    if any(term in source_name or term in url for term in SOURCE_PURPOSE_DONATION_TERMS):
        source_purpose = "donation_page"
    elif any(term in source_name or term in url for term in SOURCE_PURPOSE_EVERGREEN_TERMS):
        source_purpose = "evergreen_context"
    else:
        resource_hit = any(term in source_name or term in url for term in SOURCE_PURPOSE_RESOURCE_TERMS)
        current_hit = any(term in source_name or term in url for term in SOURCE_PURPOSE_CURRENT_TERMS)
        disaster_hit = any(term in source_name or term in url for term in SOURCE_PURPOSE_DISASTER_TERMS)
        if family == "food_bank_provider":
            if resource_hit and not current_hit:
                source_purpose = "resource_page"
            elif current_hit or any(term in source_name for term in ("update", "news", "report", "press release", "shortage", "waitlist", "closure", "hours", "demand")):
                source_purpose = "provider_update"
            else:
                source_purpose = "resource_page"
        elif family in {"state_official", "federal_official", "state_policy_news"}:
            source_purpose = "disaster_alert" if disaster_hit else "official_notice"
        elif family in {"national_news", "local_news", "public_radio", "nonprofit_news"}:
            source_purpose = "current_news"
        elif family == "economic_data":
            source_purpose = "data_release" if source_type == "api" or any(term in source_name for term in ("data release", "dataset", "statistics", "dashboard")) else "research_report"
        elif disaster_hit:
            source_purpose = "disaster_alert"
        elif current_hit:
            source_purpose = "current_news"
        elif resource_hit:
            if any(term in source_name or term in url for term in ("apply", "eligibility", "benefit", "program")):
                source_purpose = "program_description"
            else:
                source_purpose = "resource_page"

    current_or_evergreen = _current_or_evergreen_for_purpose(source_purpose)
    promotable = _source_purpose_promotable(source_purpose)
    if not promotable:
        non_promotable_reason = SOURCE_PURPOSE_NON_PROMOTABLE_REASONS.get(source_purpose, SOURCE_PURPOSE_NON_PROMOTABLE_REASONS["unknown"])
    else:
        non_promotable_reason = ""
    explicit = _normalize_source_purpose(row.get("source_purpose"), "unknown")
    if source_purpose == "unknown" and explicit != "unknown":
        source_purpose = explicit
        current_or_evergreen = _current_or_evergreen_for_purpose(source_purpose)
        promotable = _source_purpose_promotable(source_purpose)
        non_promotable_reason = "" if promotable else SOURCE_PURPOSE_NON_PROMOTABLE_REASONS.get(source_purpose, SOURCE_PURPOSE_NON_PROMOTABLE_REASONS["unknown"])
    return {
        "source_purpose": source_purpose,
        "current_or_evergreen": current_or_evergreen,
        "promotable": "true" if promotable else "false",
        "non_promotable_reason": non_promotable_reason,
    }


def _registry_role_allowed(pressure_required: bool, source_family: str) -> str:
    if source_family in BASELINE_FAMILIES:
        return "baseline_condition"
    return "pressure_evidence" if pressure_required else "context_only"


def _is_generic_pressure_summary(summary: str) -> bool:
    text = re.sub(r"\s+", " ", str(summary or "").strip().lower()).strip(" .:")
    if not text:
        return True
    return text in GENERIC_PRESSURE_SUMMARIES


def _normalize_source_text(text: str, *, limit: int | None = None) -> str:
    clean = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    clean = re.sub(r"\s+", " ", clean).strip()
    if limit is not None and len(clean) > limit:
        clean = clean[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return clean


FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES = (
    "Skip to main content",
    "Skip to content",
    "Hereâ€™s how you know",
    "Here's how you know",
    "Advertise With Us",
    "Teacher Tribute",
    "Health Update",
    "Aging Untold",
    "Local News Video",
    "Extra Community",
    "We the People",
    "Watch Live",
    "Weather Extra",
    "Sports",
    "Contests",
    "Closings & Delays",
    "Reception Issues",
    "About Us",
    "Election Results",
    "Pet Project",
    "Watch East Texas Now",
    "Watch Newscasts",
    "Big Red Box",
    "See it, Snap it, Send it",
    "An official website of the United States government",
    "Official websites use .gov",
    "A .gov website belongs to an official government organization in the United States",
    "A .gov website belongs to an official government organization",
    "Secure .gov websites use HTTPS",
    "A lock ( Lock Locked padlock ) or https:// means youâ€™ve safely connected to the .gov website",
    "A lock ( Lock Locked padlock ) or https:// means you've safely connected to the .gov website",
    "Share sensitive information only on official, secure websites",
    "member.metadata",
    "KLTV.com - Channel 7 News, Weather, Sports for East Texas - KLTV.com - Tyler, Longview, Jacksonville",
    "ETX News",
)
FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK = "See source table and review record for evidence excerpt."


def _strip_food_line_public_chrome(text: str, *, title: str = "", limit: int = 420) -> str:
    raw = _normalize_source_text(text, limit=1200)
    if not raw:
        return ""
    clean = raw
    title_text = _normalize_source_text(title)
    if title_text:
        clean = re.sub(re.escape(title_text), " ", clean, flags=re.IGNORECASE)
    lowered = clean.lower()
    cut_points = [lowered.find(phrase.lower()) for phrase in FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES if lowered.find(phrase.lower()) >= 0]
    if cut_points:
        clean = clean[: min(cut_points)]
    for phrase in FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES:
        clean = re.sub(re.escape(phrase), " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—:;,.{}")
    return _normalize_source_text(clean, limit=limit)


def clean_food_line_public_evidence_excerpt(text: str, *, title: str = "", limit: int = 420) -> str:
    raw = _normalize_source_text(text, limit=1200)
    if not raw:
        return ""
    raw = _strip_food_line_public_chrome(raw, title=title, limit=limit)
    clean = raw
    title_text = _normalize_source_text(title)
    if title_text:
        clean = re.sub(re.escape(title_text), " ", clean, flags=re.IGNORECASE)
    for phrase in FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES:
        clean = re.sub(re.escape(phrase), " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—:;,.{}")
    clean = _normalize_source_text(clean, limit=limit)
    if not clean:
        return FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    lowered = clean.lower()
    if any(phrase.lower() in lowered for phrase in FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES):
        return FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    if len(clean) < 60 and len(raw) > 180:
        return FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    return clean


def _pressure_match_terms(text: str) -> list[str]:
    lowered = str(text or "").lower()
    terms: list[str] = []
    for _, needles in PRESSURE_TYPE_RULES:
        for needle in needles:
            if needle in lowered and needle not in terms:
                terms.append(needle)
    return terms


def _extract_html_text(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


def _extract_html_title(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    match = re.search(r"<title\b[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _normalize_source_text(match.group(1))


def _extract_meta_description(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    patterns = [
        r'<meta\b[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description["\']',
        r'<meta\b[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:description["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return _normalize_source_text(match.group(1))
    return ""


def _extract_page_evidence(payload: bytes) -> dict[str, str]:
    title = _extract_html_title(payload)
    description = _extract_meta_description(payload)
    body_text = _extract_html_text(payload)
    body_excerpt = _normalize_source_text(body_text, limit=700) if body_text else ""
    evidence_parts = [part for part in (title, description, body_excerpt) if part]
    evidence_text = _normalize_source_text(" ".join(evidence_parts), limit=900)
    if title and body_excerpt:
        basis = "page_text_excerpt"
    elif title and description:
        basis = "page_title_and_meta"
    elif title:
        basis = "page_title_only"
    elif description or body_excerpt:
        basis = "page_text_excerpt"
    else:
        basis = "insufficient_evidence"
    return {
        "title": title,
        "summary_or_snippet": body_excerpt or description or title,
        "evidence_text": evidence_text,
        "evidence_text_basis": basis,
    }


def _build_pressure_summary(
    *,
    source_name: str,
    publisher: str,
    location_name: str,
    pressure_type: str,
    affected_groups: list[str],
    text: str,
) -> str:
    lowered = text.lower()
    place = str(location_name or "").strip()
    subject = str(publisher or source_name or "").strip() or "The source"
    groups_text = ", ".join(affected_groups[:2])

    if pressure_type == "demand strain":
        if any(term in lowered for term in ("demand", "lines", "wait", "families", "pantry")):
            sentence = f"{subject} reported rising food-assistance demand across its service area"
            if place and place != "United States":
                sentence += f" in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "service reduction":
        if any(term in lowered for term in ("reduced hours", "cut hours", "limited distribution", "closed", "capacity", "inventory", "fewer distributions")):
            sentence = f"{subject} reduced distribution hours because of low inventory"
            if place and place != "United States":
                sentence += f" in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "benefit disruption":
        if any(term in lowered for term in ("snap", "benefit", "ebt", "wic", "delay", "outage", "backlog")):
            sentence = f"State officials reported a SNAP benefit delay"
            if place and place != "United States":
                sentence += f" affecting households in {place}"
            elif place == "United States":
                sentence += " affecting households"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "child meal gap":
        if any(term in lowered for term in ("summer meal", "school meal", "meal site", "sun bucks", "children", "missing meals")):
            sentence = f"A summer meal site closure is leaving children without meals"
            if place and place != "United States":
                sentence += f" in {place}"
            return sentence + "."
    elif pressure_type == "senior meal strain":
        if any(term in lowered for term in ("meals on wheels", "senior", "waitlist", "home-delivered", "unable to serve seniors")):
            sentence = f"A senior meal provider reported a waitlist for home-delivered meals"
            if place and place != "United States":
                sentence += f" in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "access gap":
        if any(term in lowered for term in ("food desert", "grocery closure", "no nearby grocery", "transportation barrier", "rural food access")):
            sentence = f"A grocery closure is widening food access gaps"
            if place and place != "United States":
                sentence += f" for residents in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "household hardship":
        if any(term in lowered for term in ("skipping meals", "unable to afford food", "food hardship", "food insecurity", "hunger increased", "going hungry", "medical bills", "medical cost", "medical costs", "medical debt", "health care bills", "health-care bills", "out-of-pocket", "insurance burden", "prescription costs")):
            if any(term in lowered for term in ("medical bills", "medical cost", "medical costs", "medical debt", "health care bills", "health-care bills", "out-of-pocket", "insurance burden", "prescription costs")):
                sentence = f"Households are reporting food hardship tied to health-care costs"
            else:
                sentence = f"Households are reporting food hardship"
            if place and place != "United States":
                sentence += f" in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    elif pressure_type == "disaster disruption":
        if any(term in lowered for term in ("emergency food", "disaster", "d-snap", "storm", "flood", "wildfire")):
            sentence = f"Emergency food distribution and disaster assistance are responding to disruption"
            if place and place != "United States":
                sentence += f" in {place}"
            if groups_text:
                sentence += f", affecting {groups_text}"
            return sentence + "."
    return ""


def _derive_evidence_context(row: dict[str, Any]) -> tuple[str, str]:
    evidence_text = _normalize_source_text(str(row.get("evidence_text") or ""))
    evidence_text_basis = str(row.get("evidence_text_basis") or "").strip()
    source_kind = str(row.get("collector_source_type") or row.get("source_type") or "").strip().lower()
    if evidence_text and evidence_text_basis:
        return evidence_text, evidence_text_basis
    title = _normalize_source_text(str(row.get("title") or ""))
    summary = _normalize_source_text(str(row.get("summary_or_snippet") or ""))
    summary_fallback = _normalize_source_text(str(row.get("summary_fallback") or ""))
    if source_kind in {"rss", "feed"}:
        if title or summary:
            evidence_text = _normalize_source_text(" ".join(part for part in (title, summary) if part), limit=900)
            return evidence_text, "rss_item_text"
        if summary_fallback:
            return summary_fallback, "registry_summary_only"
        return "", "insufficient_evidence"
    if source_kind in {"page", "api"}:
        if evidence_text:
            return evidence_text, evidence_text_basis or "page_text_excerpt"
        if summary_fallback:
            return summary_fallback, "registry_summary_only"
        return "", "insufficient_evidence"
    if title or summary:
        return _normalize_source_text(" ".join(part for part in (title, summary) if part), limit=900), "manual_source_text"
    if summary_fallback:
        return summary_fallback, "registry_summary_only"
    return "", "insufficient_evidence"


def evaluate_food_line_pressure(
    row: dict[str, Any],
    *,
    edition_date: str,
    pressure_required: bool = False,
    max_age_days: int = 14,
    positive_keywords: list[str] | tuple[str, ...] | None = None,
    negative_keywords: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    title = _normalize_source_text(str(row.get("title") or ""))
    summary = _normalize_source_text(str(row.get("summary_or_snippet") or ""))
    purpose_info = classify_food_line_source_purpose(row)
    source_purpose = purpose_info["source_purpose"]
    current_or_evergreen = purpose_info["current_or_evergreen"]
    promotable = purpose_info["promotable"] == "true"
    non_promotable_reason = purpose_info["non_promotable_reason"]
    evidence_text, evidence_text_basis = _derive_evidence_context(row)
    cleaned_evidence_text = _strip_food_line_public_chrome(evidence_text, title=title, limit=900)
    raw_text = evidence_text or _normalize_source_text(" ".join(part for part in (title, summary) if part), limit=900)
    text = cleaned_evidence_text if cleaned_evidence_text and cleaned_evidence_text != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK else raw_text
    classification_text = " ".join(part for part in (cleaned_evidence_text, raw_text) if part and part != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK).strip() or raw_text
    lowered = classification_text.lower()
    positives = list(positive_keywords or _normalize_registry_keywords(row.get("positive_keywords"), DEFAULT_POSITIVE_KEYWORDS.get(str(row.get("source_family") or "").lower(), [])))
    negatives = list(negative_keywords or _normalize_registry_keywords(row.get("negative_keywords"), DEFAULT_NEGATIVE_KEYWORDS))
    negative_hit = next((term for term in negatives if term.lower() in lowered), "")
    pressure_type = "context only" if source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES else _pressure_type_for_text(classification_text)
    affected_groups = [] if source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES else _infer_affected_groups(classification_text)
    match_terms = [] if source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES else _pressure_match_terms(classification_text)
    pressure_summary = ""
    pressure_signal = False
    pressure_reason = "insufficient specific pressure evidence"
    pressure_verification_status = "insufficient_evidence"
    blocked_by_source_purpose = source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES
    if blocked_by_source_purpose:
        pressure_verification_status = "demoted_context"
        pressure_reason = non_promotable_reason
    elif evidence_text_basis == "registry_summary_only":
        pressure_verification_status = "registry_summary_only"
    elif evidence_text:
        pressure_verification_status = "demoted_context"
    if not blocked_by_source_purpose and pressure_type != "context only" and not negative_hit and evidence_text and evidence_text_basis in SUPPORTED_EVIDENCE_BASIS:
        pressure_summary = _build_pressure_summary(
            source_name=str(row.get("source_name") or row.get("publisher") or row.get("title") or "The source"),
            publisher=str(row.get("publisher") or ""),
            location_name=str(row.get("location_name") or DEFAULT_LOCATION),
            pressure_type=pressure_type,
            affected_groups=affected_groups,
            text=classification_text,
        )
        if pressure_summary and not _is_generic_pressure_summary(pressure_summary) and match_terms:
            pressure_signal = True
            pressure_reason = f"matched {pressure_type}"
            pressure_verification_status = "source_text_verified"
            if negative_hit:
                pressure_reason += f"; negative filter {negative_hit} ignored because pressure evidence was explicit"
        else:
            affected_groups = []
    if not pressure_signal:
        pressure_summary = ""
        if blocked_by_source_purpose:
            pressure_reason = non_promotable_reason
        elif evidence_text_basis == "registry_summary_only":
            pressure_reason = "registry summary only; insufficient specific pressure evidence"
        elif evidence_text:
            pressure_reason = "insufficient specific pressure evidence"
        elif not text:
            pressure_reason = "insufficient specific pressure evidence"
    published_at = str(row.get("published_at") or row.get("published_date") or "").strip()
    freshness_role = _freshness_role_for_dates(edition_date, published_at, int(max_age_days or 14))
    freshness_status, freshness_disqualification_reason = _freshness_status_for_dates(edition_date, published_at, int(max_age_days or 14))
    source_published_date = published_at[:10] if len(published_at) >= 10 else published_at
    evidence_level = "background context"
    family = str(row.get("source_family") or "").strip().lower()
    if pressure_signal:
        if family == "food_bank_provider":
            evidence_level = "provider reported strain"
        elif family in {"state_official", "federal_official", "state_policy_news"}:
            evidence_level = "official notice"
        elif family in {"local_news", "national_news", "public_radio", "nonprofit_news"}:
            evidence_level = "news report"
        elif family == "economic_data":
            evidence_level = "official data/statistic"
        else:
            evidence_level = "direct reported hardship"
    state = str(row.get("state") or "").strip().upper()
    if family in BASELINE_FAMILIES:
        source_role = "baseline_condition"
    elif family == "food_bank_provider":
        source_role = "provider_signal" if pressure_signal else "resource_context"
    elif blocked_by_source_purpose:
        source_role = "resource_context"
    elif state not in {"", "US"} and family in LOCAL_FAMILIES:
        source_role = "local_signal"
    elif family in {"state_official", "federal_official", "state_policy_news"}:
        source_role = "policy_context"
    elif family in {"national_news", "local_news", "public_radio", "nonprofit_news"}:
        source_role = "daily_signal" if pressure_signal else "map_signal"
    else:
        source_role = "pressure_evidence" if pressure_signal else "resource_context"
    if pressure_required and not pressure_signal and negative_hit and family not in BASELINE_FAMILIES:
        source_role = "rejected_context"
    location_scope = "state_local" if str(row.get("state") or "").strip().upper() not in {"", "US"} else "national"
    return {
        "pressure_signal": bool(pressure_signal),
        "pressure_type": pressure_type,
        "pressure_summary": pressure_summary,
        "pressure_reason": pressure_reason,
        "evidence_text": evidence_text,
        "evidence_text_basis": evidence_text_basis,
        "pressure_match_terms": match_terms,
        "pressure_verification_status": pressure_verification_status,
        "affected_groups": affected_groups,
        "evidence_level": evidence_level,
        "freshness_role": freshness_role,
        "freshness_status": freshness_status,
        "freshness_disqualification_reason": freshness_disqualification_reason,
        "source_published_date": source_published_date,
        "collected_date": edition_date,
        "source_role": source_role,
        "location_scope": location_scope,
        "source_role_allowed": _registry_role_allowed(bool(pressure_required), family),
        "pressure_required": bool(pressure_required),
        "positive_keywords": positives,
        "negative_keywords": negatives,
        "affected_group_keywords": sorted(DEFAULT_AFFECTED_GROUP_KEYWORDS.keys()),
        "max_age_days": int(max_age_days or 14),
        "source_purpose": source_purpose,
        "current_or_evergreen": current_or_evergreen,
        "promotable": promotable,
        "non_promotable_reason": non_promotable_reason,
        "rejected": bool(
            (negative_hit and pressure_required and family not in BASELINE_FAMILIES and not blocked_by_source_purpose)
            or source_purpose == "donation_page"
        ),
        "rejection_reason": (
            "donation page is not current pressure evidence"
            if source_purpose == "donation_page"
            else (f"excluded by negative filter: {negative_hit}" if negative_hit and pressure_required and family not in BASELINE_FAMILIES else "")
        ),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def canonical_url(url: str) -> str:
    value = str(url or "").strip().lower()
    if value.endswith("/"):
        return value[:-1]
    return value


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def infer_issue_tags(*parts: str) -> list[str]:
    text = " ".join(parts).lower()
    tags: list[str] = []
    for needles, values in TAG_RULES:
        if any(needle in text for needle in needles):
            for value in values:
                if value not in tags:
                    tags.append(value)
    return tags or ["household food insecurity"]


def infer_map_category(tags: list[str], source_family: str) -> str:
    lowered = " ".join(tags).lower()
    if "service disruption" in lowered or "disaster response" in lowered:
        return "acute strain / service disruption"
    if "summer meals" in lowered or "child hunger" in lowered:
        return "summer meal / child nutrition"
    if "senior hunger" in lowered:
        return "senior hunger"
    if "rural access" in lowered:
        return "rural access"
    if "snap" in lowered or "benefits" in lowered:
        return "benefit disruption"
    if "food banks" in lowered or "pantry capacity" in lowered:
        return "elevated demand"
    return FAMILY_TO_CATEGORY.get(source_family, "context / monitoring only")


def _parse_rss_items(payload: bytes) -> list[dict[str, str]]:
    text = INVALID_XML_ENTITY_RE.sub("&amp;", payload.decode("utf-8", errors="replace"))
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        rows: list[dict[str, str]] = []
        for block in RSS_ITEM_RE.findall(text):
            rows.append(
                {
                    "title": _extract_rss_field(block, "title"),
                    "url": _extract_feed_link(block),
                    "published_at": _extract_rss_field(block, "pubDate"),
                    "summary_or_snippet": _extract_rss_field(block, "description"),
                    "evidence_text": _normalize_source_text(
                        " ".join(
                            part
                            for part in (
                                _extract_rss_field(block, "title"),
                                _extract_rss_field(block, "description"),
                            )
                            if part
                        ),
                        limit=900,
                    ),
                    "evidence_text_basis": "rss_item_text",
                }
            )
        if not rows:
            for block in ATOM_ENTRY_RE.findall(text):
                summary = _extract_rss_field(block, "summary") or _extract_rss_field(block, "content")
                rows.append(
                    {
                        "title": _extract_rss_field(block, "title"),
                        "url": _extract_feed_link(block),
                        "published_at": _extract_rss_field(block, "updated") or _extract_rss_field(block, "published"),
                        "summary_or_snippet": summary,
                        "evidence_text": _normalize_source_text(
                            " ".join(
                                part
                                for part in (
                                    _extract_rss_field(block, "title"),
                                    summary,
                                )
                                if part
                            ),
                            limit=900,
                        ),
                        "evidence_text_basis": "rss_item_text",
                    }
                )
        if rows:
            return rows
        raise
    rows: list[dict[str, str]] = []
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{*}entry")
    for item in items:
        title = (item.findtext("title") or "").strip()
        summary = (item.findtext("description") or item.findtext("summary") or item.findtext("content") or "").strip()
        published = (item.findtext("pubDate") or item.findtext("published") or item.findtext("updated") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            link_el = item.find("link")
            if link_el is not None:
                link = str(link_el.attrib.get("href") or "").strip()
        rows.append(
            {
                "title": title,
                "url": link,
                "published_at": published,
                "summary_or_snippet": summary,
                "evidence_text": _normalize_source_text(
                    " ".join(
                        part for part in (title, summary) if part
                    ),
                    limit=900,
                ),
                "evidence_text_basis": "rss_item_text",
            }
        )
    return rows


def _extract_rss_field(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    value = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
    return re.sub(r"\s+", " ", value).strip()


def _extract_feed_link(block: str) -> str:
    link = _extract_rss_field(block, "link")
    if link.startswith(("http://", "https://")):
        return link
    match = re.search(r'<link\b[^>]*href=["\']([^"\']+)["\']', block, re.IGNORECASE)
    if match:
        href = html.unescape(match.group(1)).strip()
        if href.startswith(("http://", "https://")):
            return href
    return ""


def _iso_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            return ""


def _fetch(url: str, timeout: int = 15) -> bytes:
    if food_line_test_mode_enabled():
        raise urllib.error.URLError(f"Food Line network access disabled in test mode: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "BlueFernFoodLineCollector/1.0", "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as resp:  # noqa: S310
                return resp.read()
        raise


def load_food_line_registry(root: Path) -> list[dict[str, Any]]:
    data_root = root / "data" / "dispatches" / "food-line"
    repo_root = Path(__file__).resolve().parents[2] / "data" / "dispatches" / "food-line"
    paths = [data_root / "source_registry.json", data_root / "pressure_source_registry.json"]
    if not paths[0].exists():
        if food_line_test_mode_enabled():
            return []
        paths = [repo_root / "source_registry.json", repo_root / "pressure_source_registry.json"]
    normalized: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path.name} must be a list")
        for row in payload:
            if not isinstance(row, dict):
                continue
            if not row.get("enabled", True):
                continue
            source_family = str(row.get("source_family") or "policy_research").strip()
            source_type = str(row.get("source_type") or "page").strip().lower()
            pressure_required = bool(row.get("pressure_verification_required", row.get("pressure_required", source_family not in BASELINE_FAMILIES)))
            location_scope = str(row.get("location_scope") or ("national" if str(row.get("state") or DEFAULT_STATE).strip().upper() in {"", "US"} else "state_local")).strip()
            max_age_days = int(row.get("max_age_days") or DEFAULT_MAX_AGE_DAYS.get(source_type, 14))
            source_name = str(row.get("source_name") or row.get("name") or row.get("source_id") or "").strip()
            purpose = classify_food_line_source_purpose(row)
            normalized.append(
                {
                    "source_id": str(row.get("source_id") or "").strip(),
                    "source_name": source_name,
                    "name": source_name,
                    "source_family": source_family,
                    "source_type": source_type,
                    "url": str(row.get("url") or "").strip(),
                    "publisher": str(row.get("publisher") or row.get("source_name") or row.get("name") or "").strip(),
                    "state": str(row.get("state") or DEFAULT_STATE).strip().upper(),
                    "location_name": str(row.get("location_name") or DEFAULT_LOCATION).strip(),
                    "location_scope": location_scope,
                    "source_role_allowed": str(row.get("source_role_allowed") or _registry_role_allowed(pressure_required, source_family)).strip(),
                    "pressure_required": pressure_required,
                    "pressure_verification_required": pressure_required,
                    "freshness_mode": str(row.get("freshness_mode") or ("baseline" if source_family in BASELINE_FAMILIES else "pressure")).strip(),
                    "max_age_days": max_age_days,
                    "positive_keywords": _normalize_registry_keywords(row.get("positive_keywords"), DEFAULT_POSITIVE_KEYWORDS.get(source_family, [])),
                    "negative_keywords": _normalize_registry_keywords(row.get("negative_keywords"), DEFAULT_NEGATIVE_KEYWORDS),
                    "affected_group_keywords": _normalize_registry_keywords(row.get("affected_group_keywords"), list(DEFAULT_AFFECTED_GROUP_KEYWORDS.keys())),
                    "extraction_quality": _normalize_quality(row.get("extraction_quality"), DEFAULT_EXTRACTION_QUALITY.get(source_type, "unknown")),
                    "expected_text_basis": _normalize_expected_text_basis(row.get("expected_text_basis"), DEFAULT_EXPECTED_TEXT_BASIS.get(source_type, "manual")),
                    "default_issue_tags": [str(tag).strip() for tag in list(row.get("default_issue_tags") or []) if str(tag).strip()],
                    "default_map_category": str(row.get("default_map_category") or "").strip(),
                    "enabled": True,
                    "notes": str(row.get("notes") or "").strip(),
                    "summary_fallback": str(row.get("summary_fallback") or "").strip(),
                    "title_fallback": str(row.get("title_fallback") or row.get("source_name") or row.get("name") or row.get("source_id") or "").strip(),
                    "latitude": row.get("latitude"),
                    "longitude": row.get("longitude"),
                    "county_name": str(row.get("county_name") or "").strip(),
                    "source_purpose": purpose["source_purpose"],
                    "current_or_evergreen": purpose["current_or_evergreen"],
                    "promotable": purpose["promotable"] == "true",
                    "non_promotable_reason": purpose["non_promotable_reason"],
                }
            )
    return normalized


def load_food_line_candidate_registry(root: Path) -> list[dict[str, Any]]:
    data_root = root / "data" / "dispatches" / "food-line"
    repo_root = Path(__file__).resolve().parents[2] / "data" / "dispatches" / "food-line"
    path = data_root / "candidate_source_registry.json"
    if not path.exists():
        if food_line_test_mode_enabled():
            return []
        path = repo_root / "candidate_source_registry.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        normalized.append(
            _ensure_candidate_lifecycle_fields(
            {
                "source_id": str(row.get("source_id") or "").strip(),
                "source_name": str(row.get("source_name") or "").strip(),
                "publisher": str(row.get("publisher") or "").strip(),
                "candidate_url": str(row.get("candidate_url") or "").strip(),
                "source_family": str(row.get("source_family") or "").strip(),
                "state": str(row.get("state") or DEFAULT_STATE).strip().upper(),
                "location_name": str(row.get("location_name") or DEFAULT_LOCATION).strip(),
                "location_scope": str(row.get("location_scope") or ("national" if str(row.get("state") or DEFAULT_STATE).strip().upper() in {"", "US"} else "state_local")).strip(),
                "candidate_reason": str(row.get("candidate_reason") or "").strip(),
                "expected_text_basis": _normalize_expected_text_basis(row.get("expected_text_basis"), "manual"),
                "extraction_quality_guess": _normalize_quality(row.get("extraction_quality_guess"), "unknown"),
                "pressure_topics_expected": _coerce_list(row.get("pressure_topics_expected")),
                "status": str(row.get("status") or "candidate").strip().lower(),
                "notes": str(row.get("notes") or "").strip(),
                "source_purpose": _normalize_source_purpose(row.get("source_purpose"), "unknown"),
                "current_or_evergreen": _current_or_evergreen_for_purpose(_normalize_source_purpose(row.get("source_purpose"), "unknown")),
                "promotable": _source_purpose_promotable(_normalize_source_purpose(row.get("source_purpose"), "unknown")),
                "non_promotable_reason": SOURCE_PURPOSE_NON_PROMOTABLE_REASONS.get(_normalize_source_purpose(row.get("source_purpose"), "unknown"), ""),
                "first_discovered_at": str(row.get("first_discovered_at") or "").strip(),
                "last_discovered_at": str(row.get("last_discovered_at") or "").strip(),
                "last_tested_at": str(row.get("last_tested_at") or "").strip(),
                "discovery_count": int(row.get("discovery_count") or 0),
                "test_count": int(row.get("test_count") or 0),
                "enable_count": int(row.get("enable_count") or 0),
                "reject_count": int(row.get("reject_count") or 0),
                "keep_candidate_count": int(row.get("keep_candidate_count") or 0),
                "last_recommendation": str(row.get("last_recommendation") or "").strip(),
                "last_recommendation_reason": str(row.get("last_recommendation_reason") or "").strip(),
                "source_quality_score": int(row.get("source_quality_score") or 0),
                "source_quality_tier": str(row.get("source_quality_tier") or "").strip().lower() or _source_quality_tier(int(row.get("source_quality_score") or 0)),
                "auto_discovered": bool(row.get("auto_discovered", False)),
            }
            )
        )
    return normalized


def refresh_food_line_pressure_registry_source_purpose(root: Path) -> dict[str, Any]:
    data_root = root / "data" / "dispatches" / "food-line"
    repo_root = Path(__file__).resolve().parents[2] / "data" / "dispatches" / "food-line"
    path = data_root / "pressure_source_registry.json"
    if not path.exists():
        if food_line_test_mode_enabled():
            return {"path": str(path), "changed": False, "blocked_count": 0, "source_purpose_counts": {}, "current_or_evergreen_counts": {}}
        path = repo_root / "pressure_source_registry.json"
    if not path.exists():
        return {"path": str(path), "changed": False, "blocked_count": 0, "source_purpose_counts": {}, "current_or_evergreen_counts": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    changed = False
    blocked_count = 0
    source_purpose_counts: Counter[str] = Counter()
    current_or_evergreen_counts: Counter[str] = Counter()
    for row in payload:
        if not isinstance(row, dict):
            continue
        classification = classify_food_line_source_purpose(row)
        source_purpose = classification["source_purpose"]
        current_or_evergreen = classification["current_or_evergreen"]
        promotable = classification["promotable"] == "true"
        non_promotable_reason = classification["non_promotable_reason"]
        source_purpose_counts[source_purpose] += 1
        current_or_evergreen_counts[current_or_evergreen] += 1
        if row.get("source_purpose") != source_purpose:
            row["source_purpose"] = source_purpose
            changed = True
        if row.get("current_or_evergreen") != current_or_evergreen:
            row["current_or_evergreen"] = current_or_evergreen
            changed = True
        if row.get("promotable") != promotable:
            row["promotable"] = promotable
            changed = True
        if row.get("non_promotable_reason") != non_promotable_reason:
            row["non_promotable_reason"] = non_promotable_reason
            changed = True
        if source_purpose in SOURCE_PURPOSE_EVERGREEN_VALUES and bool(row.get("enabled", True)):
            row["enabled"] = False
            row["notes"] = _append_note(str(row.get("notes") or ""), non_promotable_reason)
            changed = True
            blocked_count += 1
    if changed:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "path": str(path),
        "changed": changed,
        "blocked_count": blocked_count,
        "source_purpose_counts": dict(sorted(source_purpose_counts.items())),
        "current_or_evergreen_counts": dict(sorted(current_or_evergreen_counts.items())),
    }


def collect_food_line_auto_sources(root: Path, date: str, *, fetcher: Any | None = None) -> dict[str, Any]:
    edition_date = validate_date(date)
    registry_purpose_refresh = refresh_food_line_pressure_registry_source_purpose(root)
    registry = load_food_line_registry(root)
    fetch = resolve_food_line_fetcher(fetcher)
    retrieved_at = utc_now()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    rejected_news: list[dict[str, Any]] = []
    source_audit_rows: list[dict[str, Any]] = []
    collected_count_by_extraction_quality: Counter[str] = Counter()
    verified_pressure_count_by_extraction_quality: Counter[str] = Counter()
    demoted_count_by_extraction_quality: Counter[str] = Counter()
    fetch_failure_count_by_source_id: Counter[str] = Counter()
    no_evidence_count_by_source_id: Counter[str] = Counter()
    rejected_by_source_purpose_count = int(registry_purpose_refresh.get("blocked_count") or 0)
    demoted_by_source_purpose_count = int(registry_purpose_refresh.get("blocked_count") or 0)
    for source in registry:
        source_id = str(source.get("source_id") or "")
        source_url = str(source.get("url") or "")
        source_family = str(source.get("source_family") or "policy_research")
        source_kind = str(source.get("source_type") or "rss")
        extraction_quality = str(source.get("extraction_quality") or "unknown")
        audit_entry = {
            "source_id": source_id,
            "source_name": str(source.get("source_name") or source.get("name") or source_id),
            "source_family": source_family,
            "url": source_url,
            "fetched": False,
            "item_count": 0,
            "accepted_pressure_count": 0,
            "demoted_count": 0,
            "rejected_count": 0,
            "top_rejection_reasons": [],
            "extraction_basis_used": [],
        }
        source_purpose = str(source.get("source_purpose") or "unknown")
        try:
            if source_kind == "rss":
                payload = fetch(source_url, timeout=15)
                items = _parse_rss_items(payload)
                published_basis = "source_published"
            else:
                payload = fetch(source_url, timeout=15)
                evidence = _extract_page_evidence(payload)
                items = [
                    {
                        "title": evidence.get("title") or str(source.get("title_fallback") or source.get("name") or source_id),
                        "url": source_url,
                        "published_at": "",
                        "summary_or_snippet": evidence.get("summary_or_snippet") or "",
                        "evidence_text": evidence.get("evidence_text") or "",
                        "evidence_text_basis": evidence.get("evidence_text_basis") or "insufficient_evidence",
                    }
                ]
                published_basis = "retrieved_at_fallback"
            audit_entry["fetched"] = True
            audit_entry["item_count"] = len(items[:5])
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError, ValueError) as exc:
            failures.append({"source_id": source_id, "reason": f"{type(exc).__name__}: {exc}"})
            fetch_failure_count_by_source_id[source_id] += 1
            audit_entry["top_rejection_reasons"] = [f"{type(exc).__name__}: {exc}"]
            source_audit_rows.append(audit_entry)
            continue
        rejection_reasons: Counter[str] = Counter()
        extraction_basis_used: set[str] = set()
        for item in items[:5]:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url.startswith(("http://", "https://")):
                continue
            summary = str(item.get("summary_or_snippet") or "").strip()
            evidence_text = str(item.get("evidence_text") or "").strip()
            evidence_text_basis = str(item.get("evidence_text_basis") or "").strip()
            extraction_basis_used.add(evidence_text_basis or "insufficient_evidence")
            tags = list(source.get("default_issue_tags") or []) + infer_issue_tags(title, summary, url, source_family)
            tags = [tag for i, tag in enumerate(tags) if tag and tag not in tags[:i]]
            record_id = "food-line-auto-" + hashlib.sha1(f"{source_id}|{url}|{title}".encode("utf-8")).hexdigest()[:16]
            published_at = _iso_date(str(item.get("published_at") or ""))
            pressure_eval = evaluate_food_line_pressure(
                {
                    "title": title,
                    "summary_or_snippet": summary,
                    "url": url,
                    "evidence_text": evidence_text,
                    "evidence_text_basis": evidence_text_basis,
                    "source_family": source_family,
                    "source_type": source_kind,
                    "state": source.get("state") or DEFAULT_STATE,
                    "published_at": published_at,
                    "positive_keywords": source.get("positive_keywords") or [],
                    "negative_keywords": source.get("negative_keywords") or [],
                },
                edition_date=edition_date,
                pressure_required=bool(source.get("pressure_required")),
                max_age_days=int(source.get("max_age_days") or DEFAULT_MAX_AGE_DAYS.get(source_kind, 14)),
                positive_keywords=source.get("positive_keywords") or [],
                negative_keywords=source.get("negative_keywords") or [],
            )
            collected_count_by_extraction_quality[extraction_quality] += 1
            if pressure_eval.get("rejected"):
                rejection_reason = str(pressure_eval.get("rejection_reason") or "rejected")
                rejection_reasons[rejection_reason] += 1
                rejected_news.append(
                    {
                        "source_record_id": record_id,
                        "source_id": source_id,
                        "title": title,
                        "url": url,
                        "reason": pressure_eval.get("rejection_reason") or "rejected",
                        "source_family": source_family,
                        "source_type": source_kind,
                    }
                )
                continue
            if not bool(pressure_eval.get("pressure_signal")):
                demoted_count_by_extraction_quality[extraction_quality] += 1
                if str(pressure_eval.get("pressure_verification_status") or "") in {"insufficient_evidence", "demoted_context"} and not str(pressure_eval.get("evidence_text") or "").strip():
                    no_evidence_count_by_source_id[source_id] += 1
            if bool(pressure_eval.get("pressure_signal")) and str(pressure_eval.get("pressure_verification_status") or "") == "source_text_verified":
                verified_pressure_count_by_extraction_quality[extraction_quality] += 1
            rows.append(
                {
                    "source_record_id": record_id,
                    "title": title,
                    "url": url,
                    "source_id": source_id,
                    "source_name": str(source.get("source_name") or source.get("name") or source_id),
                    "publisher": str(source.get("publisher") or source.get("source_name") or source.get("name") or "Unknown publisher"),
                    "published_at": published_at or f"{edition_date}T00:00:00+00:00",
                    "retrieved_at": retrieved_at,
                    "summary_or_snippet": summary or "Source-backed food insecurity context signal.",
                    "source_type": source_kind,
                    "collector_source_type": source_kind,
                    "extraction_quality": extraction_quality,
                    "expected_text_basis": str(source.get("expected_text_basis") or "manual"),
                    "pressure_verification_required": bool(source.get("pressure_verification_required")),
                    "published_date_basis": "source_published" if published_at else published_basis,
                    "source_family": source_family,
                    "location_name": str(source.get("location_name") or DEFAULT_LOCATION),
                    "state": str(source.get("state") or DEFAULT_STATE),
                    "issue_tags": tags,
                    "map_category": str(source.get("default_map_category") or "").strip() or infer_map_category(tags, source_family),
                    "latitude": source.get("latitude"),
                    "longitude": source.get("longitude"),
                    "county_name": str(source.get("county_name") or "").strip(),
                    "location_scope": str(source.get("location_scope") or ("national" if str(source.get("state") or DEFAULT_STATE).strip().upper() == "US" else "state_local")),
                    "source_role_allowed": str(source.get("source_role_allowed") or _registry_role_allowed(bool(source.get("pressure_required")), source_family)),
                    "pressure_required": bool(source.get("pressure_required")),
                    "freshness_mode": str(source.get("freshness_mode") or "pressure"),
                    "max_age_days": int(source.get("max_age_days") or DEFAULT_MAX_AGE_DAYS.get(source_kind, 14)),
                    "positive_keywords": list(source.get("positive_keywords") or []),
                    "negative_keywords": list(source.get("negative_keywords") or []),
                    "affected_group_keywords": list(source.get("affected_group_keywords") or []),
                    "is_state_local_source": bool(str(source.get("state") or DEFAULT_STATE).strip().upper() != "US" and source_family in LOCAL_FAMILIES),
                    "pressure_signal": bool(pressure_eval.get("pressure_signal")),
                    "pressure_type": str(pressure_eval.get("pressure_type") or "context only"),
                    "pressure_reason": str(pressure_eval.get("pressure_reason") or ""),
                    "pressure_summary": str(pressure_eval.get("pressure_summary") or ""),
                    "evidence_text": str(pressure_eval.get("evidence_text") or evidence_text or summary or ""),
                    "evidence_text_basis": str(pressure_eval.get("evidence_text_basis") or evidence_text_basis or "insufficient_evidence"),
                    "pressure_match_terms": list(pressure_eval.get("pressure_match_terms") or []),
                    "pressure_verification_status": str(pressure_eval.get("pressure_verification_status") or "insufficient_evidence"),
                    "affected_groups": list(pressure_eval.get("affected_groups") or []),
                    "evidence_level": str(pressure_eval.get("evidence_level") or "background context"),
                    "freshness_role": str(pressure_eval.get("freshness_role") or "dated_recent_signal"),
                    "source_role": str(pressure_eval.get("source_role") or "resource_context"),
                    "map_eligible": bool(pressure_eval.get("pressure_signal")),
                }
            )
        audit_entry["accepted_pressure_count"] = sum(1 for row in rows if str(row.get("source_id") or "") == source_id and bool(row.get("pressure_signal")))
        audit_entry["demoted_count"] = sum(1 for row in rows if str(row.get("source_id") or "") == source_id and str(row.get("pressure_verification_status") or "") == "demoted_context")
        audit_entry["rejected_count"] = sum(1 for row in rejected_news if str(row.get("source_id") or "") == source_id)
        audit_entry["top_rejection_reasons"] = [reason for reason, _count in rejection_reasons.most_common(3)]
        audit_entry["extraction_basis_used"] = sorted(extraction_basis_used)
        source_audit_rows.append(audit_entry)
        upsert_food_line_source_performance_history(
            root,
            source_id,
            items_seen=len(items[:5]),
            runs_fetched=1,
            verified_pressure_records=audit_entry["accepted_pressure_count"],
            demoted_records=audit_entry["demoted_count"],
            rejected_records=audit_entry["rejected_count"],
        )
    for failure in failures:
        upsert_food_line_source_performance_history(
            root,
            str(failure.get("source_id") or ""),
            fetch_failure=str(failure.get("reason") or ""),
        )
    auto_path = root / "data" / "dispatches" / "food-line" / "sources" / edition_date / "auto_sources.json"
    auto_path.parent.mkdir(parents=True, exist_ok=True)
    auto_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    audit_path = root / "data" / "dispatches" / "food-line" / "sources" / edition_date / "collector_audit.json"
    audit_path.write_text(json.dumps(source_audit_rows, indent=2), encoding="utf-8")
    collected_source_count_by_source_id = dict(sorted(Counter(str(row.get("source_id") or "") for row in rows if str(row.get("source_id") or "")).items()))
    rejected_news_by_source = dict(sorted(Counter(str(row.get("source_id") or "") for row in rejected_news if str(row.get("source_id") or "")).items()))
    pressure_verified_count = sum(1 for row in rows if str(row.get("pressure_verification_status") or "") == "source_text_verified")
    pressure_demoted_unverified_count = sum(1 for row in rows if str(row.get("pressure_verification_status") or "") == "demoted_context")
    pressure_registry_only_count = sum(1 for row in rows if str(row.get("pressure_verification_status") or "") == "registry_summary_only")
    pressure_evidence_basis_counts = dict(sorted(Counter(str(row.get("evidence_text_basis") or "insufficient_evidence") for row in rows).items()))
    pressure_signal_count = sum(1 for row in rows if bool(row.get("pressure_signal")))
    pressure_marker_count = sum(1 for row in rows if bool(row.get("pressure_signal")) and bool(row.get("map_eligible")))
    return {
        "ok": True,
        "auto_sources_path": str(auto_path),
        "collector_audit_path": str(audit_path),
        "source_count": len(rows),
        "news_item_count": sum(1 for row in rows if str(row.get("collector_source_type") or "").lower() == "rss"),
        "provider_pressure_count": sum(1 for row in rows if str(row.get("source_family") or "") == "food_bank_provider" and bool(row.get("pressure_signal"))),
        "official_pressure_count": sum(1 for row in rows if str(row.get("source_family") or "") in {"state_official", "federal_official", "state_policy_news"} and bool(row.get("pressure_signal"))),
        "baseline_source_count": sum(1 for row in rows if str(row.get("source_role") or "") == "baseline_condition"),
        "rejected_news_count": len(rejected_news),
        "rejected_news_reasons": [row["reason"] for row in rejected_news[:10]],
        "rejected_news_by_source": rejected_news_by_source,
        "collected_source_count_by_source_id": collected_source_count_by_source_id,
        "pressure_verified_count": pressure_verified_count,
        "pressure_demoted_unverified_count": pressure_demoted_unverified_count,
        "pressure_registry_only_count": pressure_registry_only_count,
        "pressure_evidence_basis_counts": pressure_evidence_basis_counts,
        "pressure_signal_count": pressure_signal_count,
        "pressure_marker_count": pressure_marker_count,
        "rejected_by_source_purpose_count": rejected_by_source_purpose_count,
        "demoted_by_source_purpose_count": demoted_by_source_purpose_count,
        "registry_source_purpose_refresh": registry_purpose_refresh,
        "collected_count_by_extraction_quality": dict(sorted(collected_count_by_extraction_quality.items())),
        "verified_pressure_count_by_extraction_quality": dict(sorted(verified_pressure_count_by_extraction_quality.items())),
        "demoted_count_by_extraction_quality": dict(sorted(demoted_count_by_extraction_quality.items())),
        "fetch_failure_count_by_source_id": dict(sorted(fetch_failure_count_by_source_id.items())),
        "no_evidence_count_by_source_id": dict(sorted(no_evidence_count_by_source_id.items())),
        "rejected_news": rejected_news,
        "failed_sources": failures,
    }

