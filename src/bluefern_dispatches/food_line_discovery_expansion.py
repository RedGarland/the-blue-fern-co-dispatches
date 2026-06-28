from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import urllib.request
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from bluefern_dispatches.food_line_sources import canonical_url, resolve_food_line_fetcher, validate_date

DISPATCH_SLUG = "food-line"
DISCOVERY_DIR_NAME = "discovery"
DISCOVERY_CANDIDATES_FILE = "discovery_candidates.json"
DISCOVERY_AUDIT_JSON_FILE = "discovery_audit.json"
DISCOVERY_AUDIT_MD_FILE = "discovery_audit.md"
DISCOVERY_CONFIG_FILE = "discovery_expansion_config.json"
GOOGLE_NEWS_DOMAIN = "news.google.com"
SOCIAL_DOMAINS = ("x.com", "twitter.com", "facebook.com", "instagram.com", "tiktok.com")
GOOGLE_ASSET_DOMAINS = (
    "googleusercontent.com",
    "gstatic.com",
    "googleapis.com",
    "googlevideo.com",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "w3.org",
    "schema.org",
    "ogp.me",
)
STATIC_PATH_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".mp4", ".mp3", ".pdf", ".js", ".css", ".woff", ".woff2")
LISTING_PATH_SEGMENTS = {
    "category",
    "categories",
    "tag",
    "tags",
    "search",
    "topic",
    "topics",
    "section",
    "sections",
    "news",
    "stories",
    "latest",
    "updates",
    "archive",
    "archives",
    "calendar",
    "calendars",
    "feed",
    "feeds",
    "rss",
    "atom",
}

STATE_TERRITORIES: list[tuple[str, str]] = [
    ("Alabama", "AL"),
    ("Alaska", "AK"),
    ("Arizona", "AZ"),
    ("Arkansas", "AR"),
    ("California", "CA"),
    ("Colorado", "CO"),
    ("Connecticut", "CT"),
    ("Delaware", "DE"),
    ("Florida", "FL"),
    ("Georgia", "GA"),
    ("Hawaii", "HI"),
    ("Idaho", "ID"),
    ("Illinois", "IL"),
    ("Indiana", "IN"),
    ("Iowa", "IA"),
    ("Kansas", "KS"),
    ("Kentucky", "KY"),
    ("Louisiana", "LA"),
    ("Maine", "ME"),
    ("Maryland", "MD"),
    ("Massachusetts", "MA"),
    ("Michigan", "MI"),
    ("Minnesota", "MN"),
    ("Mississippi", "MS"),
    ("Missouri", "MO"),
    ("Montana", "MT"),
    ("Nebraska", "NE"),
    ("Nevada", "NV"),
    ("New Hampshire", "NH"),
    ("New Jersey", "NJ"),
    ("New Mexico", "NM"),
    ("New York", "NY"),
    ("North Carolina", "NC"),
    ("North Dakota", "ND"),
    ("Ohio", "OH"),
    ("Oklahoma", "OK"),
    ("Oregon", "OR"),
    ("Pennsylvania", "PA"),
    ("Rhode Island", "RI"),
    ("South Carolina", "SC"),
    ("South Dakota", "SD"),
    ("Tennessee", "TN"),
    ("Texas", "TX"),
    ("Utah", "UT"),
    ("Vermont", "VT"),
    ("Virginia", "VA"),
    ("Washington", "WA"),
    ("West Virginia", "WV"),
    ("Wisconsin", "WI"),
    ("Wyoming", "WY"),
    ("District of Columbia", "DC"),
    ("Puerto Rico", "PR"),
    ("Guam", "GU"),
    ("U.S. Virgin Islands", "VI"),
    ("American Samoa", "AS"),
    ("Northern Mariana Islands", "MP"),
]

PRESSURE_TERMS = [
    "food insecurity",
    "food bank",
    "food pantry",
    "hunger relief",
    "demand",
    "strain",
    "shortage",
    "surge",
    "increased need",
    "waitlist",
    "funding gap",
    "cuts",
    "reduced benefits",
    "SNAP",
    "EBT",
    "school meals",
    "summer meals",
    "WIC",
    "Meals on Wheels",
    "TEFAP",
    "grocery prices",
    "inflation",
    "food costs",
    "rent and groceries",
    "utility bills and groceries",
]

QUERY_FAMILY_DEFINITIONS: list[dict[str, Any]] = [
    {
        "query_family": "core_hunger",
        "geographic_scope": "national",
        "source_family": "local_news",
        "templates": [
            '"food insecurity"',
            '"food bank"',
            '"food pantry"',
            '"food banks"',
            '"food pantries"',
            '"hunger relief"',
            '"emergency food assistance"',
        ],
    },
    {
        "query_family": "pressure",
        "geographic_scope": "national",
        "source_family": "local_news",
        "templates": [
            '("food bank" OR "food pantry") (demand OR strain OR shortage OR surge)',
            '("food bank" OR "food pantry") ("increased need" OR waitlist OR "funding gap")',
            '"pantry demand"',
            '"families turn to food banks"',
        ],
    },
    {
        "query_family": "policy_program",
        "geographic_scope": "national",
        "source_family": "state_policy_news",
        "templates": [
            '(SNAP OR EBT) (cuts OR changes OR benefits OR families)',
            '("food stamps" OR "SNAP cuts" OR "SNAP benefits" OR "SNAP rolls") ("food bank" OR pantry OR families)',
            '("summer meals" OR "school meals") (families OR children OR hunger)',
            '("meal sites" OR "summer meals" OR "food distribution sites") (families OR children OR "emergency food assistance")',
            '(WIC OR TEFAP OR "Meals on Wheels") (cuts OR delays OR waitlist)',
        ],
    },
    {
        "query_family": "cost_pressure",
        "geographic_scope": "national",
        "source_family": "local_news",
        "templates": [
            '("grocery prices" OR "food costs") ("food pantry" OR families)',
            '("rent and groceries" OR "utility bills and groceries") hunger',
        ],
    },
    {
        "query_family": "state_territory",
        "geographic_scope": "state_or_territory",
        "source_family": "local_news",
        "templates": [
            '"food bank demand" {geo} after:{after} before:{before}',
            '"food pantry demand" {geo} after:{after} before:{before}',
            '"food banks" {geo} after:{after} before:{before}',
            '"food pantries" {geo} after:{after} before:{before}',
            '"food stamps" {geo} after:{after} before:{before}',
            '"SNAP cuts" {geo} after:{after} before:{before}',
            '"summer meals" {geo} families after:{after} before:{before}',
            '"emergency food assistance" {geo} after:{after} before:{before}',
            '"grocery prices" {geo} food pantry after:{after} before:{before}',
        ],
    },
    {
        "query_family": "metro",
        "geographic_scope": "metro",
        "source_family": "local_news",
        "templates": [
            '"food bank demand" {geo} after:{after} before:{before}',
            '"food pantry demand" {geo} after:{after} before:{before}',
            '"food banks" {geo} after:{after} before:{before}',
            '"food pantries" {geo} after:{after} before:{before}',
            '"food stamps" {geo} after:{after} before:{before}',
            '"SNAP cuts" {geo} after:{after} before:{before}',
            '"summer meals" {geo} families after:{after} before:{before}',
            '"emergency food assistance" {geo} after:{after} before:{before}',
            '"grocery prices" {geo} food pantry after:{after} before:{before}',
        ],
    },
]

DEFAULT_METROS: list[dict[str, str]] = [
    {"name": "Charlotte"},
    {"name": "Phoenix"},
    {"name": "Seattle"},
    {"name": "Portland"},
    {"name": "Boise"},
    {"name": "Spokane"},
    {"name": "Los Angeles"},
    {"name": "San Francisco Bay Area"},
    {"name": "Denver"},
    {"name": "Dallas"},
    {"name": "Houston"},
    {"name": "San Antonio"},
    {"name": "Chicago"},
    {"name": "Detroit"},
    {"name": "Cleveland"},
    {"name": "Pittsburgh"},
    {"name": "Philadelphia"},
    {"name": "New York"},
    {"name": "Boston"},
    {"name": "Atlanta"},
    {"name": "Miami"},
    {"name": "Tampa"},
    {"name": "St. Louis"},
    {"name": "Kansas City"},
    {"name": "Minneapolis"},
    {"name": "Nashville"},
    {"name": "New Orleans"},
    {"name": "Baltimore"},
    {"name": "Washington DC"},
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(canonical_url(value) if not value.startswith(("http://", "https://")) else value)
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(_normalize_url(url)).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _extract_source_date(value: str) -> str:
    text = _nonempty(value)
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _is_homepage_only_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").strip("/")
    return parsed.scheme in {"http", "https"} and not path and not parsed.query


def _is_article_specific_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    host = _host(value)
    if not host or host.endswith(SOCIAL_DOMAINS) or host.endswith((GOOGLE_NEWS_DOMAIN, "google.com")) or host.endswith(GOOGLE_ASSET_DOMAINS):
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").strip("/")
    lowered_path = path.lower()
    if any(lowered_path.endswith(ext) for ext in STATIC_PATH_SUFFIXES):
        return False
    return bool(path)


def _is_static_or_namespace_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    host = _host(value)
    if not host:
        return True
    if host.endswith(SOCIAL_DOMAINS) or host.endswith((GOOGLE_NEWS_DOMAIN, "google.com")) or host.endswith(GOOGLE_ASSET_DOMAINS):
        return True
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").strip("/").lower()
    if any(path.endswith(ext) for ext in STATIC_PATH_SUFFIXES):
        return True
    if value in {"http://www.w3.org/2000/svg", "https://www.w3.org/2000/svg", "http://schema.org", "https://schema.org"}:
        return True
    return False


def _choose_trace_url(original_trace_url: str, canonical_url: str, discovered_url: str) -> str:
    if _is_article_specific_url(canonical_url):
        return _normalize_url(canonical_url)
    if _is_article_specific_url(original_trace_url):
        return _normalize_url(original_trace_url)
    if _is_article_specific_url(discovered_url):
        return _normalize_url(discovered_url)
    if canonical_url:
        return _normalize_url(canonical_url)
    if original_trace_url:
        return _normalize_url(original_trace_url)
    if _is_google_news_wrapper(discovered_url):
        return ""
    return _normalize_url(discovered_url)


def _discovery_traceability_status(
    *,
    source_url: str,
    original_source_url: str,
    discovered_url: str,
    fetch_status: str,
    google_news_resolution_failed: bool = False,
) -> str:
    if not any((source_url, original_source_url, discovered_url)):
        return "missing_url"
    if any(_host(url).endswith(SOCIAL_DOMAINS) for url in (source_url, original_source_url, discovered_url) if url):
        return "social_only"
    if google_news_resolution_failed:
        return "unresolved_google_news"
    if _host(discovered_url).endswith((GOOGLE_NEWS_DOMAIN, "google.com")) and not source_url and not original_source_url:
        return "source_wrapper_only"
    if fetch_status == "manual_fallback" and (_is_article_specific_url(source_url) or _is_article_specific_url(original_source_url)):
        return "traceable"
    if _is_article_specific_url(source_url) or _is_article_specific_url(original_source_url):
        return "traceable"
    if _is_homepage_only_url(source_url) or _is_homepage_only_url(original_source_url):
        return "publisher_homepage_trace_only"
    if source_url or original_source_url:
        return "non_article_trace_url"
    return "weak_traceability"


def _lane_from_query(query_family: str, discovered_url: str, publisher_url: str, publisher: str) -> str:
    family = _nonempty(query_family)
    publisher_text = " ".join((_nonempty(publisher), _host(discovered_url), _host(publisher_url))).lower()
    direct = {
        "public_radio",
        "food_bank_provider",
        "feeding_america_affiliate",
        "school_meals_child_nutrition",
        "county_city_agenda",
        "snap_state_notice",
        "united_way_211",
        "nonprofit_report",
        "social_watchlist",
        "institutional_update",
    }
    if family in direct:
        return family
    if "npr" in publisher_text or "public radio" in publisher_text:
        return "public_radio"
    if "feedingamerica" in publisher_text or "feeding america" in publisher_text:
        return "feeding_america_affiliate"
    if any(term in publisher_text for term in ("211", "united way")):
        return "united_way_211"
    if any(term in publisher_text for term in ("school", "nutrition", "summer meal")):
        return "school_meals_child_nutrition"
    if any(term in publisher_text for term in ("agenda", "county", "city council", "school board")):
        return "county_city_agenda"
    if any(term in publisher_text for term in ("snap", "ebt", "wic", "benefits")):
        return "snap_state_notice"
    if any(term in publisher_text for term in ("food bank", "food pantry", "pantry")):
        return "food_bank_provider"
    if any(_host(url).endswith(SOCIAL_DOMAINS) for url in (discovered_url, publisher_url) if url):
        return "social_watchlist"
    return "news_article"


def _discovery_source_type(query_family: str, discovered_url: str, publisher_url: str, fetch_status: str) -> str:
    if fetch_status == "manual_fallback":
        return "manual_fallback"
    if any(_host(url).endswith(SOCIAL_DOMAINS) for url in (discovered_url, publisher_url) if url):
        return "social_post"
    family = _nonempty(query_family)
    if family in {"county_city_agenda", "snap_state_notice", "united_way_211", "nonprofit_report", "institutional_update"}:
        return "institutional_page"
    return "rss_discovery"


def _published_date_in_window(edition_date: str, source_published_date: str, *, lookback_days: int, lookahead_days: int) -> bool:
    if not _nonempty(source_published_date):
        return False
    edition = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    published = datetime.strptime(validate_date(source_published_date), "%Y-%m-%d").date()
    return edition - timedelta(days=max(0, int(lookback_days))) <= published <= edition + timedelta(days=max(0, int(lookahead_days)))


def _public_claim_blockers(
    *,
    edition_date: str,
    source_published_date: str,
    lookback_days: int,
    lookahead_days: int,
    lane: str,
    classification_status: str,
    fetch_status: str,
    traceability_status: str,
    duplicate_of: str,
) -> list[str]:
    blockers: list[str] = []
    if duplicate_of:
        blockers.append("duplicate")
    if fetch_status != "ok" and fetch_status != "manual_fallback":
        blockers.append("blocked_fetch")
    if fetch_status == "blocked_listing_url":
        blockers.append("non_article_trace_url")
    if classification_status == "context_only":
        blockers.append("context_only")
    elif classification_status not in {"qualified_pressure_signal", "manual_fallback"}:
        blockers.append("classification_not_current_pressure_signal")
    if lane == "social_watchlist":
        blockers.append("social_watchlist_only")
    if traceability_status == "publisher_homepage_trace_only":
        blockers.append("publisher_homepage_trace_only")
    elif traceability_status == "non_article_trace_url":
        blockers.append("non_article_trace_url")
    elif traceability_status != "traceable":
        blockers.append(traceability_status or "traceability_incomplete")
    if not _published_date_in_window(
        edition_date,
        source_published_date,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    ):
        blockers.append("outside_backfill_date_window")
    deduped: list[str] = []
    for blocker in blockers:
        if blocker and blocker not in deduped:
            deduped.append(blocker)
    return deduped


def _candidate_review_defaults(
    *,
    edition_date: str,
    source_published_date: str,
    lookback_days: int,
    lookahead_days: int,
    lane: str,
    classification_status: str,
    fetch_status: str,
    traceability_status: str,
    duplicate_of: str,
) -> tuple[str, bool, list[str]]:
    review_status = "watchlist" if lane == "social_watchlist" else "needs_review"
    blockers = _public_claim_blockers(
        edition_date=edition_date,
        source_published_date=source_published_date,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
        lane=lane,
        classification_status=classification_status,
        fetch_status=fetch_status,
        traceability_status=traceability_status,
        duplicate_of=duplicate_of,
    )
    eligible = not blockers
    return review_status, eligible, blockers


def _candidate_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(_nonempty(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"food-line-discovery-{digest}"


def _food_line_discovery_config_path(root: Path) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_CONFIG_FILE


def load_food_line_discovery_expansion_config(root: Path) -> dict[str, Any]:
    path = _food_line_discovery_config_path(root)
    if not path.exists():
        repo_path = Path(__file__).resolve().parents[2] / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_CONFIG_FILE
        if not repo_path.exists():
            return {
                "query_families": QUERY_FAMILY_DEFINITIONS,
                "metros": DEFAULT_METROS,
                "direct_sources": [],
            }
        path = repo_path
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    query_families = [row for row in payload.get("query_families") or [] if isinstance(row, dict)]
    metros = [row for row in payload.get("metros") or [] if isinstance(row, dict)]
    return {
        "query_families": query_families or QUERY_FAMILY_DEFINITIONS,
        "metros": metros or DEFAULT_METROS,
        "direct_sources": [row for row in payload.get("direct_sources") or [] if isinstance(row, dict)] if _food_line_discovery_config_path(root).exists() else [],
        "search": payload.get("search") if isinstance(payload.get("search"), dict) else {},
    }


def _parse_date_range(edition_date: str, *, lookback_days: int = 1, lookahead_days: int = 1) -> tuple[str, str]:
    day = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    return (day - timedelta(days=max(0, int(lookback_days)))).isoformat(), (day + timedelta(days=max(0, int(lookahead_days)))).isoformat()


def _apply_query_date_bounds(query_text: str, *, after: str, before: str) -> str:
    text = _nonempty(query_text)
    if not text:
        return ""
    if "after:" not in text:
        text = f"{text} after:{after}"
    if "before:" not in text:
        text = f"{text} before:{before}"
    return text


def _sample_query_plan_across_families(query_plan: list[dict[str, Any]], max_queries: int | None) -> list[dict[str, Any]]:
    if max_queries is None or max_queries < 0 or len(query_plan) <= max_queries:
        return list(query_plan)
    direct_rows = [row for row in query_plan if _nonempty(row.get("discovery_channel")) not in {"", "google_news_rss"}]
    google_rows = [row for row in query_plan if _nonempty(row.get("discovery_channel")) in {"", "google_news_rss"}]
    direct_rows = _sample_rows_round_robin(
        direct_rows,
        max_queries=max_queries,
        bucket_key=lambda row: (_effective_lane(row), _nonempty(row.get("direct_source_name") or row.get("query_family"))),
        sort_key=lambda row: (
            int(row.get("sampling_priority") or 100),
            0 if _nonempty(row.get("discovery_channel")) == "direct_rss" else 1,
            _effective_lane(row),
            _nonempty(row.get("direct_source_name") or row.get("query_family")),
        ),
    )
    if len(direct_rows) >= max_queries:
        return list(direct_rows[:max_queries])
    grouped: dict[str, list[dict[str, Any]]] = {}
    family_order: list[str] = []
    for row in google_rows:
        family = _nonempty(row.get("query_family")) or "unknown"
        if family not in grouped:
            grouped[family] = []
            family_order.append(family)
        grouped[family].append(row)
    sampled: list[dict[str, Any]] = list(direct_rows)
    index = 0
    while len(sampled) < max_queries:
        added = False
        for family in family_order:
            family_rows = grouped.get(family) or []
            if index < len(family_rows):
                sampled.append(family_rows[index])
                added = True
                if len(sampled) >= max_queries:
                    break
        if not added:
            break
        index += 1
    return sampled


def _sample_rows_round_robin(
    rows: list[dict[str, Any]],
    *,
    max_queries: int,
    bucket_key: Any,
    sort_key: Any,
) -> list[dict[str, Any]]:
    if max_queries <= 0 or not rows:
        return []
    ordered_rows = sorted(rows, key=sort_key)
    grouped: dict[tuple[Any, ...] | Any, list[dict[str, Any]]] = {}
    group_order: list[tuple[Any, ...] | Any] = []
    for row in ordered_rows:
        key = bucket_key(row)
        if key not in grouped:
            grouped[key] = []
            group_order.append(key)
        grouped[key].append(row)
    sampled: list[dict[str, Any]] = []
    index = 0
    while len(sampled) < max_queries:
        added = False
        for key in group_order:
            bucket = grouped.get(key) or []
            if index < len(bucket):
                sampled.append(bucket[index])
                added = True
                if len(sampled) >= max_queries:
                    break
        if not added:
            break
        index += 1
    return sampled


def build_food_line_discovery_query_plan(
    root: Path,
    edition_date: str,
    *,
    lookback_days: int = 1,
    lookahead_days: int = 1,
) -> list[dict[str, Any]]:
    config = load_food_line_discovery_expansion_config(root)
    after, before = _parse_date_range(edition_date, lookback_days=lookback_days, lookahead_days=lookahead_days)
    rows: list[dict[str, Any]] = []
    for direct_source in [row for row in config.get("direct_sources") or [] if isinstance(row, dict) and bool(row.get("enabled", True))]:
        source_name = _nonempty(direct_source.get("source_name"))
        discovery_lane = _nonempty(direct_source.get("discovery_lane") or direct_source.get("source_family") or "news_article")
        discovery_channel = _nonempty(direct_source.get("discovery_channel") or ("direct_rss" if _nonempty(direct_source.get("feed_url")) else "direct_page"))
        rows.append(
            {
                "query_family": discovery_lane,
                "query_text": source_name or _nonempty(direct_source.get("source_url") or direct_source.get("feed_url")),
                "query_template": "",
                "geographic_scope": _nonempty(direct_source.get("geographic_scope") or "national"),
                "state_or_territory": _nonempty(direct_source.get("state_or_territory")),
                "state_abbrev": "",
                "metro": _nonempty(direct_source.get("metro")),
                "discovery_channel": discovery_channel,
                "search_provider": discovery_channel,
                "source_family": _nonempty(direct_source.get("source_family") or "local_news"),
                "edition_date": edition_date,
                "after": after,
                "before": before,
                "direct_source_name": source_name,
                "direct_source_feed_url": _nonempty(direct_source.get("feed_url")),
                "direct_source_url": _nonempty(direct_source.get("source_url")),
                "direct_source_enabled": bool(direct_source.get("enabled", True)),
                "allowed_domains": [str(item).strip().lower() for item in direct_source.get("allowed_domains") or [] if str(item).strip()],
                "max_age_days": int(direct_source.get("max_age_days") or 0),
                "pressure_terms": [str(item).strip() for item in direct_source.get("pressure_terms") or [] if str(item).strip()],
                "exclusion_terms": [str(item).strip() for item in direct_source.get("exclusion_terms") or [] if str(item).strip()],
                "sampling_priority": int(direct_source.get("sampling_priority") or 100),
                "direct_source_candidate_cap": int(
                    direct_source.get("direct_source_candidate_cap")
                    or (1 if discovery_channel == "direct_page" else 2)
                ),
                "direct_lane_candidate_cap": int(direct_source.get("direct_lane_candidate_cap") or 0),
                "notes": _nonempty(direct_source.get("notes")),
            }
        )
    for family in config["query_families"]:
        family_name = _nonempty(family.get("query_family"))
        geographic_scope = _nonempty(family.get("geographic_scope"))
        source_family = _nonempty(family.get("source_family") or "local_news")
        templates = [str(item).strip() for item in family.get("templates") or [] if str(item).strip()]
        if family_name in {"state_territory", "metro"}:
            continue
        for template in templates:
            query_text = _apply_query_date_bounds(template.format(after=after, before=before), after=after, before=before)
            rows.append(
                {
                    "query_family": family_name,
                    "query_text": query_text,
                    "query_template": template,
                    "geographic_scope": geographic_scope or "national",
                    "state_or_territory": "",
                    "metro": "",
                    "discovery_channel": "google_news_rss",
                    "search_provider": "google_news_rss",
                    "source_family": source_family,
                    "edition_date": edition_date,
                    "after": after,
                    "before": before,
                }
            )

    for state_name, abbrev in STATE_TERRITORIES:
        family = next((row for row in config["query_families"] if _nonempty(row.get("query_family")) == "state_territory"), {})
        for template in [str(item).strip() for item in family.get("templates") or [] if str(item).strip()]:
            query_text = _apply_query_date_bounds(template.format(geo=state_name, after=after, before=before), after=after, before=before)
            rows.append(
                {
                    "query_family": "state_territory",
                    "query_text": query_text,
                    "query_template": template,
                    "geographic_scope": "state_or_territory",
                    "state_or_territory": state_name,
                    "state_abbrev": abbrev,
                    "metro": "",
                    "discovery_channel": "google_news_rss",
                    "search_provider": "google_news_rss",
                    "source_family": _nonempty(family.get("source_family") or "local_news"),
                    "edition_date": edition_date,
                    "after": after,
                    "before": before,
                }
            )

    metros = [row for row in config.get("metros") or DEFAULT_METROS if isinstance(row, dict)]
    family = next((row for row in config["query_families"] if _nonempty(row.get("query_family")) == "metro"), {})
    for metro in metros:
        metro_name = _nonempty(metro.get("name"))
        if not metro_name:
            continue
        for template in [str(item).strip() for item in family.get("templates") or [] if str(item).strip()]:
            query_text = _apply_query_date_bounds(template.format(geo=metro_name, after=after, before=before), after=after, before=before)
            rows.append(
                {
                    "query_family": "metro",
                    "query_text": query_text,
                    "query_template": template,
                    "geographic_scope": "metro",
                    "state_or_territory": "",
                    "metro": metro_name,
                    "discovery_channel": "google_news_rss",
                    "search_provider": "google_news_rss",
                    "source_family": _nonempty(family.get("source_family") or "local_news"),
                    "edition_date": edition_date,
                    "after": after,
                    "before": before,
                }
            )
    return rows


def _query_url(query_text: str) -> str:
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(query_text) + "&hl=en-US&gl=US&ceid=US:en"


def _query_family_to_lane(query_family: str) -> str:
    family = _nonempty(query_family)
    if family in {
        "public_radio",
        "food_bank_provider",
        "feeding_america_affiliate",
        "school_meals_child_nutrition",
        "county_city_agenda",
        "snap_state_notice",
        "united_way_211",
        "nonprofit_report",
        "social_watchlist",
        "institutional_update",
    }:
        return family
    return "news_article"


def _effective_lane(row: dict[str, Any]) -> str:
    return _nonempty(row.get("discovery_lane") or _query_family_to_lane(_nonempty(row.get("query_family"))))


def _project_fetch_with_metadata(url: str, *, timeout: int = 15) -> tuple[bytes, dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)

    def _read(timeout_seconds: int, *, context: ssl.SSLContext | None = None) -> tuple[bytes, dict[str, Any]]:
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as resp:  # noqa: S310
            payload = resp.read(2_000_000)
            final_url = _nonempty(resp.geturl())
            redirect_chain = [url] if not final_url or final_url == url else [url, final_url]
            return payload, {
                "response_status": int(getattr(resp, "status", 200) or 200),
                "final_response_url": final_url or url,
                "content_type": _nonempty(resp.headers.get("Content-Type")),
                "redirect_chain": redirect_chain,
            }

    try:
        return _read(timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            longer_timeout = max(timeout * 3, timeout + 15)
            try:
                return _read(longer_timeout)
            except urllib.error.URLError as retry_exc:
                retry_reason = getattr(retry_exc, "reason", None)
                if isinstance(retry_reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(retry_exc):
                    return _read(longer_timeout, context=ssl._create_unverified_context())
                raise
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return _read(timeout, context=ssl._create_unverified_context())
        raise


def _fetch_url(fetcher: Any, url: str) -> tuple[bytes, str]:
    try:
        return fetcher(url, timeout=15), ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


def _fetch_url_with_metadata(fetcher: Any, url: str) -> tuple[bytes, str, dict[str, Any]]:
    try:
        if getattr(fetcher, "__module__", "").endswith("food_line_sources") and getattr(fetcher, "__name__", "") == "_fetch":
            payload, meta = _project_fetch_with_metadata(url, timeout=15)
            return payload, "", meta
        result = fetcher(url, timeout=15)
        if isinstance(result, dict):
            payload = result.get("payload")
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            return payload if isinstance(payload, (bytes, bytearray)) else b"", _nonempty(result.get("error")), {
                "response_status": result.get("response_status"),
                "final_response_url": _nonempty(result.get("final_response_url") or url),
                "content_type": _nonempty(result.get("content_type")),
                "redirect_chain": list(result.get("redirect_chain") or []),
            }
        return result if isinstance(result, (bytes, bytearray)) else b"", "", {}
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}", {}


def _looks_like_json_payload(payload: bytes, content_type: str) -> bool:
    lowered = _nonempty(content_type).lower()
    if "json" in lowered:
        return True
    prefix = payload.lstrip()[:1]
    return prefix in {b"{", b"["}


def _looks_like_html_payload(payload: bytes, content_type: str) -> bool:
    lowered = _nonempty(content_type).lower()
    if "html" in lowered:
        return True
    snippet = payload[:400].decode("utf-8", errors="replace").lower()
    return "<html" in snippet or "<!doctype html" in snippet


def _looks_like_xml_payload(payload: bytes, content_type: str) -> bool:
    lowered = _nonempty(content_type).lower()
    if any(token in lowered for token in ("xml", "rss", "atom")):
        return True
    snippet = payload[:200].decode("utf-8", errors="replace").lstrip().lower()
    return snippet.startswith("<?xml") or snippet.startswith("<rss") or snippet.startswith("<feed")


def _parse_google_news_rss(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    root = ET.fromstring(text)
    rows: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        source = item.find("source")
        source_url = _nonempty(source.attrib.get("url") if source is not None else "")
        rows.append(
            {
                "title": _nonempty(item.findtext("title")),
                "link": _nonempty(item.findtext("link")),
                "description": _nonempty(item.findtext("description")),
                "pubDate": _nonempty(item.findtext("pubDate")),
                "source_url": source_url,
                "source_name": _nonempty(source.text if source is not None else ""),
            }
        )
    return rows


def _parse_direct_feed(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    root = ET.fromstring(text)
    rows: list[dict[str, str]] = []
    if root.tag.lower().endswith("feed"):
        for entry in root.findall(".//{*}entry"):
            link = ""
            for link_node in entry.findall("{*}link"):
                href = _nonempty(link_node.attrib.get("href"))
                rel = _nonempty(link_node.attrib.get("rel") or "alternate")
                if href and rel in {"alternate", ""}:
                    link = href
                    break
            rows.append(
                {
                    "title": _nonempty(entry.findtext("{*}title")),
                    "link": _nonempty(link),
                    "description": _nonempty(entry.findtext("{*}summary") or entry.findtext("{*}content")),
                    "pubDate": _nonempty(entry.findtext("{*}published") or entry.findtext("{*}updated")),
                    "source_url": "",
                    "source_name": _nonempty(root.findtext("{*}title")),
                }
            )
        return rows
    for item in root.findall(".//item"):
        source = item.find("source")
        rows.append(
            {
                "title": _nonempty(item.findtext("title")),
                "link": _nonempty(item.findtext("link")),
                "description": _nonempty(item.findtext("description")),
                "pubDate": _nonempty(item.findtext("pubDate")),
                "source_url": _nonempty(source.attrib.get("url") if source is not None else ""),
                "source_name": _nonempty(source.text if source is not None else ""),
            }
        )
    return rows


def _parse_json_feed(payload: bytes) -> list[dict[str, str]]:
    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        return []
    rows: list[dict[str, str]] = []
    source_name = _nonempty(parsed.get("title"))
    items = parsed.get("items")
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": _nonempty(item.get("title")),
                "link": _nonempty(item.get("url") or item.get("external_url") or item.get("id")),
                "description": _nonempty(item.get("summary") or item.get("content_text") or item.get("content_html")),
                "pubDate": _nonempty(item.get("date_published") or item.get("date_modified")),
                "source_url": "",
                "source_name": source_name,
            }
        )
    return rows


def _page_title(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    return _nonempty(html.unescape(match.group(1))) if match else ""


def _page_summary(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    for pattern in (
        r'<meta\b[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _nonempty(html.unescape(match.group(1)))
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()[:400]


def _page_text(payload: bytes, *, limit: int = 3000) -> str:
    text = payload.decode("utf-8", errors="replace")
    cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()[:limit]


def _extract_page_published_date(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    patterns = (
        r'<meta\b[^>]*property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*name=["\']article:published_time["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']og:published_time["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*name=["\']pubdate["\'][^>]*content=["\']([^"\']+)["\']',
        r'<time\b[^>]*datetime=["\']([^"\']+)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = _extract_source_date(_nonempty(match.group(1)))
        if candidate:
            return candidate
    return ""


def _is_document_specific_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").lower()
    return any(path.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"))


def _extract_listing_links(
    payload: bytes,
    *,
    base_url: str,
    allowed_domains: list[str],
    source_name: str,
) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.IGNORECASE | re.DOTALL):
        href = urllib.parse.urljoin(base_url, html.unescape(match.group(1)).strip())
        normalized_href = _normalize_url(href)
        if not normalized_href or normalized_href in seen:
            continue
        if allowed_domains and not _domain_allowed(normalized_href, allowed_domains):
            continue
        if _is_homepage_only_url(normalized_href) or _is_feed_or_listing_url(normalized_href):
            continue
        if not (_is_article_specific_url(normalized_href) or _is_document_specific_url(normalized_href)):
            continue
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        title = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not title:
            title = normalized_href.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        rows.append(
            {
                "title": title[:240],
                "link": normalized_href,
                "description": "",
                "pubDate": "",
                "source_url": normalized_href,
                "source_name": source_name,
            }
        )
        seen.add(normalized_href)
        if len(rows) >= 25:
            break
    return rows


def _recommended_direct_source_action(
    *,
    discovery_channel: str,
    enabled: bool,
    direct_fetch_status: str,
    parser_attempted: str,
    item_count: int,
    content_type: str,
) -> str:
    if not enabled:
        return "disable_source"
    if direct_fetch_status in {"blocked_401", "blocked_403"}:
        return "blocked_by_site"
    if direct_fetch_status == "not_found_404":
        return "replace_url"
    if direct_fetch_status == "parse_failure":
        if discovery_channel == "direct_rss" and "html" in _nonempty(content_type).lower():
            return "replace_url"
        return "fix_parser"
    if item_count <= 0 and parser_attempted == "html_listing":
        return "disable_source"
    return "keep"


def _collect_direct_source_items(
    fetcher: Any,
    query_row: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    source_url = _nonempty(query_row.get("direct_source_url") or query_row.get("direct_source_feed_url"))
    discovery_channel = _nonempty(query_row.get("discovery_channel") or "direct_page")
    allowed_domains = [str(item).strip().lower() for item in query_row.get("allowed_domains") or [] if str(item).strip()]
    payload, fetch_error, fetch_meta = _fetch_url_with_metadata(fetcher, source_url)
    diagnostics = {
        "attempted": bool(source_url),
        "success": False,
        "error": fetch_error,
        "item_count": 0,
        "response_status": fetch_meta.get("response_status"),
        "final_response_url": _nonempty(fetch_meta.get("final_response_url") or source_url),
        "content_type": _nonempty(fetch_meta.get("content_type")),
        "redirect_chain": list(fetch_meta.get("redirect_chain") or ([source_url] if source_url else [])),
        "failure_reason": "",
        "exception_message": "",
        "parser_attempted": "",
    }
    if fetch_error or not payload:
        diagnostics["failure_reason"] = _classify_fetch_error(fetch_error or "empty response")
        diagnostics["exception_message"] = _nonempty(fetch_error)
        return [], diagnostics
    try:
        source_name = _nonempty(query_row.get("direct_source_name"))
        if _looks_like_xml_payload(payload, diagnostics["content_type"]):
            diagnostics["parser_attempted"] = "rss_or_atom"
            items = _parse_direct_feed(payload)
        elif _looks_like_json_payload(payload, diagnostics["content_type"]):
            diagnostics["parser_attempted"] = "json_feed"
            items = _parse_json_feed(payload)
        elif _looks_like_html_payload(payload, diagnostics["content_type"]):
            diagnostics["parser_attempted"] = "html_listing"
            page_url = _normalize_url(_extract_canonical_url(payload) or diagnostics["final_response_url"] or source_url)
            extracted = _extract_listing_links(
                payload,
                base_url=page_url or source_url,
                allowed_domains=allowed_domains,
                source_name=source_name,
            )
            if extracted:
                items = extracted
            else:
                items = [
                    {
                        "title": _page_title(payload) or source_name,
                        "link": page_url,
                        "description": _page_summary(payload),
                        "pubDate": "",
                        "source_url": source_url,
                        "source_name": source_name,
                    }
                ]
        else:
            diagnostics["parser_attempted"] = "unknown"
            raise ValueError("unsupported direct source payload type")
        diagnostics["success"] = True
        diagnostics["item_count"] = len(items)
        return items, diagnostics
    except Exception as exc:  # noqa: BLE001
        diagnostics["success"] = False
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"
        diagnostics["failure_reason"] = _classify_fetch_error(diagnostics["error"])
        diagnostics["exception_message"] = diagnostics["error"]
        diagnostics["item_count"] = 0
        return [], diagnostics


def _extract_canonical_url(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace")
    patterns = (
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*name=["\']twitter:url["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']article:url["\'][^>]*content=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _normalize_url(html.unescape(match.group(1)))
    return ""


def _extract_candidate_urls(text: str) -> list[str]:
    if not text:
        return []
    candidates: list[str] = []
    patterns = (
        r'https?://[^\s"\'<>\\]+',
        r'https?:\\?/\\?/[^\s"\'<>]+',
    )
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            candidate = html.unescape(match).replace("\\/", "/")
            for _ in range(2):
                candidate = urllib.parse.unquote(candidate)
            parsed = urllib.parse.urlsplit(candidate)
            if parsed.scheme not in {"http", "https"}:
                continue
            normalized = _normalize_url(candidate)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
            for key in ("url", "u", "q", "continue", "redirect", "target"):
                for value in urllib.parse.parse_qs(parsed.query).get(key, []):
                    nested = _normalize_url(html.unescape(urllib.parse.unquote(value)))
                    if nested and nested not in candidates:
                        candidates.append(nested)
    return candidates


def _extract_meta_refresh_target(text: str) -> str:
    match = re.search(r'<meta\b[^>]*http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)["\']', text, re.IGNORECASE)
    if not match:
        return ""
    content = html.unescape(match.group(1))
    url_match = re.search(r'url\s*=\s*(.+)$', content, re.IGNORECASE)
    if not url_match:
        return ""
    return _normalize_url(url_match.group(1).strip(" '\""))


def _extract_wrapper_debug_snippet(text: str, *, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:limit]


def _same_host_family(candidate_url: str, publisher_url: str) -> bool:
    candidate_host = _host(candidate_url)
    publisher_host = _host(publisher_url)
    if not candidate_host or not publisher_host:
        return False
    return candidate_host == publisher_host or candidate_host.endswith(f".{publisher_host}") or publisher_host.endswith(f".{candidate_host}")


def _rejected_candidate_url_reason(candidate_url: str, *, publisher_url: str) -> str:
    url = _normalize_url(candidate_url)
    if not url:
        return "empty_url"
    if _is_google_news_wrapper(url) or _host(url).endswith(("google.com",)):
        return "google_domain"
    if _is_static_or_namespace_url(url):
        return "static_or_namespace_url"
    if publisher_url and not _same_host_family(url, publisher_url):
        return "not_same_publisher_family"
    if _is_homepage_only_url(url):
        return "publisher_homepage_only"
    if not _is_article_specific_url(url):
        return "not_article_specific"
    return ""


def _extract_google_news_article_url(payload: bytes, *, publisher_url: str = "") -> str:
    text = payload.decode("utf-8", errors="replace")
    candidates = _extract_candidate_urls(text)
    for candidate in candidates:
        if _is_article_specific_url(candidate) and _same_host_family(candidate, publisher_url) and not _is_google_news_wrapper(candidate):
            return candidate
    if publisher_url:
        return ""
    for candidate in candidates:
        if _is_article_specific_url(candidate) and not _is_google_news_wrapper(candidate):
            return candidate
    return ""


def _extract_google_news_homepage_url(payload: bytes, *, publisher_url: str = "") -> str:
    text = payload.decode("utf-8", errors="replace")
    candidates = _extract_candidate_urls(text)
    for candidate in candidates:
        if _is_homepage_only_url(candidate) and _same_host_family(candidate, publisher_url) and not _is_google_news_wrapper(candidate):
            return candidate
    if publisher_url:
        return ""
    for candidate in candidates:
        if _is_homepage_only_url(candidate) and not _is_google_news_wrapper(candidate):
            return candidate
    return ""


def _resolve_google_news_wrapper(fetcher: Any, google_news_url: str, *, publisher_url: str = "") -> tuple[str, str, bool, dict[str, Any]]:
    url = _normalize_url(google_news_url)
    if not _is_google_news_wrapper(url):
        return "", "", False, {}
    payload, fetch_error, fetch_meta = _fetch_url_with_metadata(fetcher, url)
    debug: dict[str, Any] = {
        "response_status": fetch_meta.get("response_status"),
        "final_response_url": _nonempty(fetch_meta.get("final_response_url") or url),
        "content_type": _nonempty(fetch_meta.get("content_type")),
        "redirect_chain": list(fetch_meta.get("redirect_chain") or ([url] if url else [])),
        "candidate_url_count_extracted": 0,
        "accepted_candidate_url": "",
        "rejection_reason": "",
        "google_news_resolution_status": "",
        "debug_snippet": "",
    }
    if fetch_error or not payload:
        debug["rejection_reason"] = fetch_error or "empty response"
        debug["google_news_resolution_status"] = "failed_fetch_error"
        return "", fetch_error or "empty response", True, debug
    text = payload.decode("utf-8", errors="replace")
    candidates = _extract_candidate_urls(text)
    meta_refresh = _extract_meta_refresh_target(text)
    if meta_refresh and meta_refresh not in candidates:
        candidates.append(meta_refresh)
    debug["candidate_url_count_extracted"] = len(candidates)
    debug["debug_snippet"] = _extract_wrapper_debug_snippet(text)
    resolved = _extract_google_news_article_url(payload, publisher_url=publisher_url)
    if resolved:
        debug["accepted_candidate_url"] = resolved
        debug["google_news_resolution_status"] = "success_article"
        return resolved, "", True, debug
    homepage = _extract_google_news_homepage_url(payload, publisher_url=publisher_url)
    if homepage:
        debug["accepted_candidate_url"] = homepage
        debug["google_news_resolution_status"] = "success_homepage_only"
        return homepage, "", True, debug
    canonical = _extract_canonical_url(payload)
    if canonical and (not publisher_url or _same_host_family(canonical, publisher_url)):
        reason = _rejected_candidate_url_reason(canonical, publisher_url=publisher_url)
        if not reason:
            debug["accepted_candidate_url"] = canonical
            debug["google_news_resolution_status"] = "success_article" if _is_article_specific_url(canonical) else "success_homepage_only"
            return canonical, "", True, debug
    if not candidates:
        debug["rejection_reason"] = "no candidate urls extracted"
        debug["google_news_resolution_status"] = "failed_no_candidate_urls"
        return "", "unresolved google news wrapper", True, debug
    rejected_reasons = [_rejected_candidate_url_reason(candidate, publisher_url=publisher_url) for candidate in candidates]
    if publisher_url and all(reason in {"google_domain", "static_or_namespace_url", "not_same_publisher_family"} for reason in rejected_reasons if reason):
        debug["rejection_reason"] = "no same publisher family candidate url"
        debug["google_news_resolution_status"] = "failed_no_same_publisher_family"
    else:
        debug["rejection_reason"] = "only rejected candidate urls"
        debug["google_news_resolution_status"] = "failed_only_rejected_urls"
    return "", "unresolved google news wrapper", True, debug


def _classify_fetch_error(error: str) -> str:
    lowered = error.lower()
    if "403" in lowered:
        return "blocked_403"
    if "401" in lowered:
        return "blocked_401"
    if "404" in lowered:
        return "not_found_404"
    if "timeout" in lowered:
        return "timeout"
    if "paywall" in lowered:
        return "paywall"
    if "script" in lowered or "javascript" in lowered:
        return "script_blocked"
    if "parse" in lowered:
        return "parse_failure"
    if "empty response" in lowered:
        return "empty_response"
    return "fetch_failed"


def _is_google_news_wrapper(url: str) -> bool:
    host = _host(url)
    return bool(host) and (host == GOOGLE_NEWS_DOMAIN or host.endswith(f".{GOOGLE_NEWS_DOMAIN}"))


def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    host = _host(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains if domain)


def _is_feed_or_listing_url(url: str, *, feed_url: str = "") -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    if feed_url and value == _normalize_url(feed_url):
        return True
    if _is_homepage_only_url(value):
        return True
    parsed = urllib.parse.urlsplit(value)
    path = [segment for segment in parsed.path.lower().split("/") if segment]
    if parsed.query and any(key in urllib.parse.parse_qs(parsed.query) for key in ("s", "search", "q", "tag", "category")):
        return True
    if not path:
        return True
    last_segment = path[-1].split(".", 1)[0]
    if last_segment in LISTING_PATH_SEGMENTS:
        return True
    first_segment = path[0].split(".", 1)[0]
    return len(path) == 1 and first_segment in LISTING_PATH_SEGMENTS


def _candidate_preference_key(row: dict[str, Any]) -> tuple[int, int, int]:
    channel = _nonempty(row.get("discovery_channel"))
    direct_preferred = 0 if channel not in {"", "google_news_rss"} else 1
    traceable_preferred = 0 if _nonempty(row.get("traceability_status")) == "traceable" else 1
    manual_preferred = 0 if _nonempty(row.get("classification_status")) == "manual_fallback" else 1
    return (manual_preferred, direct_preferred, traceable_preferred)


def _title_publisher_date_key(row: dict[str, Any]) -> str:
    title = re.sub(r"\s+", " ", _nonempty(row.get("discovered_title")).lower())
    publisher = re.sub(r"\s+", " ", _nonempty(row.get("discovered_publisher")).lower())
    published = _nonempty(row.get("source_published_date"))
    if not (title and publisher and published):
        return ""
    return f"{title}|{publisher}|{published}"


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> int:
    for row in candidates:
        row["duplicate_of"] = ""
    preferred_direct = 0
    seen_trace: dict[str, str] = {}
    seen_title_keys: dict[str, str] = {}
    id_to_row = {str(row.get("candidate_id") or ""): row for row in candidates}
    for row in sorted(candidates, key=_candidate_preference_key):
        candidate_id = _nonempty(row.get("candidate_id"))
        trace_key = _normalize_url(_nonempty(row.get("canonical_url") or row.get("final_trace_url") or row.get("discovered_url")))
        title_key = _title_publisher_date_key(row)
        duplicate_of = ""
        if trace_key and trace_key in seen_trace:
            duplicate_of = seen_trace[trace_key]
        elif title_key and title_key in seen_title_keys:
            duplicate_of = seen_title_keys[title_key]
        if duplicate_of:
            row["duplicate_of"] = duplicate_of
            winner = id_to_row.get(duplicate_of, {})
            if _nonempty(row.get("discovery_channel")) == "google_news_rss" and _nonempty(winner.get("discovery_channel")) not in {"", "google_news_rss"}:
                preferred_direct += 1
            continue
        if trace_key:
            seen_trace[trace_key] = candidate_id
        if title_key:
            seen_title_keys[title_key] = candidate_id
    return preferred_direct


def _initial_trace_url(discovered_url: str, publisher_url: str) -> str:
    if _is_article_specific_url(publisher_url):
        return publisher_url
    if _is_article_specific_url(discovered_url) and not _is_google_news_wrapper(discovered_url):
        return discovered_url
    if _is_google_news_wrapper(discovered_url):
        return publisher_url
    return publisher_url or discovered_url


def _search_text_blob(row: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _nonempty(row.get("title")),
            _nonempty(row.get("description")),
            _nonempty(row.get("source_name")),
            _nonempty(row.get("source_url")),
            _nonempty(row.get("link")),
        )
        if part
    ).lower()


def _detect_terms(text: str, terms: list[str]) -> list[str]:
    hits: list[str] = []
    for term in terms:
        lowered = term.lower()
        if lowered in text and term not in hits:
            hits.append(term)
    return hits


def _normalize_manual_fallback_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors = validate_food_line_manual_fallback_record(record)
    if errors:
        return {}, errors
    trace_url = _normalize_url(_nonempty(record.get("final_trace_url") or record.get("canonical_url")))
    canonical = _normalize_url(_nonempty(record.get("canonical_url") or trace_url))
    headline = _nonempty(record.get("headline"))
    publisher = _nonempty(record.get("publisher"))
    location = _nonempty(record.get("location"))
    candidate_id = _candidate_id("manual_fallback", trace_url, headline, publisher)
    text_blob = " ".join(
        part
        for part in (
            headline,
            _nonempty(record.get("manually_reviewed_summary")),
            _nonempty(record.get("pressure_evidence_summary")),
            location,
        )
        if part
    ).lower()
    pressure_terms_detected = _detect_terms(text_blob, PRESSURE_TERMS)
    lane = _nonempty(record.get("discovery_lane")) or _lane_from_query("manual_fallback", trace_url, canonical, publisher)
    traceability_status = _discovery_traceability_status(
        source_url=trace_url or canonical,
        original_source_url=trace_url,
        discovered_url=canonical or trace_url,
        fetch_status="manual_fallback",
    )
    candidate_review_status, public_claim_eligible, public_claim_blockers = _candidate_review_defaults(
        edition_date=_nonempty(record.get("date")),
        source_published_date=_nonempty(record.get("date")),
        lookback_days=0,
        lookahead_days=0,
        lane=lane,
        classification_status="manual_fallback",
        fetch_status="manual_fallback",
        traceability_status=traceability_status,
        duplicate_of="",
    )
    return (
        {
            "candidate_id": candidate_id,
            "discovery_date": _nonempty(record.get("date")),
            "discovered_at": _utc_now(),
            "discovery_lane": lane,
            "discovery_query": _nonempty(record.get("discovery_query")),
            "discovery_source_type": "manual_fallback",
            "query_family": "manual_fallback",
            "query_text": "",
            "geographic_scope": _nonempty(record.get("geographic_scope") or "manual"),
            "state_or_territory": _nonempty(record.get("state_or_territory")),
            "state_hint": _nonempty(record.get("state_hint") or record.get("state_or_territory")),
            "metro": _nonempty(record.get("metro")),
            "discovery_channel": "manual_fallback",
            "discovered_title": headline,
            "discovered_publisher": publisher,
            "discovered_url": canonical or trace_url,
            "canonical_url": canonical,
            "source_url": trace_url or canonical,
            "original_source_url": trace_url or canonical,
            "google_news_url": "",
            "publication_date": _nonempty(record.get("date")),
            "source_published_date": _nonempty(record.get("date")),
            "date_basis": "manual_reviewed_date",
            "fetch_status": "manual_fallback",
            "fetch_error": "",
            "final_trace_url": trace_url or canonical,
            "duplicate_of": "",
            "review_status": "manual_reviewed",
            "candidate_review_status": candidate_review_status,
            "classification_status": "manual_fallback",
            "exclusion_reason": "",
            "pressure_terms_detected": pressure_terms_detected,
            "location_terms_detected": [location] if location else [],
            "manual_review_required": False,
            "location_scope": _nonempty(record.get("location_scope") or record.get("geographic_scope") or "manual"),
            "pressure_signal_hint": _nonempty(record.get("pressure_signal_hint") or record.get("pressure_evidence_summary")),
            "pressure_signal_type_hint": _nonempty(record.get("pressure_signal_type_hint")),
            "traceability_status": traceability_status,
            "public_claim_eligible": public_claim_eligible,
            "public_claim_blockers": public_claim_blockers,
            "manually_reviewed_summary": _nonempty(record.get("manually_reviewed_summary")),
            "pressure_evidence_summary": _nonempty(record.get("pressure_evidence_summary")),
            "affected_groups": list(record.get("affected_groups") or []),
            "limitations": _nonempty(record.get("limitations")),
            "extraction_quality": _nonempty(record.get("extraction_quality")) or "manual_fallback",
            "reviewer_or_source_note": _nonempty(record.get("reviewer_or_source_note")),
            "source_note": _nonempty(record.get("reviewer_or_source_note")),
        },
        [],
    )


def validate_food_line_manual_fallback_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_nonempty = (
        "publisher",
        "canonical_url",
        "headline",
        "date",
        "location",
        "manually_reviewed_summary",
        "pressure_evidence_summary",
        "limitations",
        "reviewer_or_source_note",
        "final_trace_url",
    )
    for key in required_nonempty:
        if not _nonempty(record.get(key)):
            errors.append(f"missing required field: {key}")
    if _nonempty(record.get("extraction_quality")) not in {"manual_fallback", "manual_fallback_reviewed"}:
        errors.append("extraction_quality must be manual_fallback or manual_fallback_reviewed")
    for key in ("canonical_url", "final_trace_url"):
        value = _normalize_url(_nonempty(record.get(key)))
        if not value.startswith(("http://", "https://")):
            errors.append(f"{key} must be an http or https URL")
    if not list(record.get("affected_groups") or []):
        errors.append("affected_groups must be a non-empty list")
    if _nonempty(record.get("location")) and len(_nonempty(record.get("location"))) < 2:
        errors.append("location must be a meaningful string")
    try:
        validate_date(_nonempty(record.get("date")))
    except Exception:
        errors.append("date must be YYYY-MM-DD")
    return errors


def _normalize_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    canonical = _normalize_url(_nonempty(row.get("canonical_url")))
    original_source_url = _normalize_url(_nonempty(row.get("original_source_url") or row.get("final_trace_url") or canonical))
    discovered = _normalize_url(_nonempty(row.get("discovered_url") or original_source_url))
    google_news_url = _normalize_url(_nonempty(row.get("google_news_url")))
    if google_news_url and not discovered:
        discovered = google_news_url
    final_trace = _choose_trace_url(original_source_url, canonical, discovered)
    if not canonical:
        canonical = _normalize_url(final_trace)
    candidate = dict(row)
    candidate["candidate_id"] = _nonempty(candidate.get("candidate_id")) or _candidate_id(final_trace or discovered, _nonempty(candidate.get("discovered_publisher")), _nonempty(candidate.get("query_family")))
    candidate["discovered_url"] = discovered
    candidate["canonical_url"] = canonical
    candidate["google_news_url"] = google_news_url
    candidate["original_source_url"] = original_source_url
    candidate["source_url"] = _normalize_url(
        _nonempty(candidate.get("source_url") or final_trace or original_source_url or ("" if _is_google_news_wrapper(discovered) else discovered))
    )
    candidate["final_trace_url"] = final_trace or canonical or ("" if _is_google_news_wrapper(discovered) else discovered)
    candidate["manual_review_required"] = bool(candidate.get("manual_review_required", True))
    candidate["pressure_terms_detected"] = list(candidate.get("pressure_terms_detected") or [])
    candidate["location_terms_detected"] = list(candidate.get("location_terms_detected") or [])
    candidate["affected_groups"] = list(candidate.get("affected_groups") or [])
    candidate["review_status"] = _nonempty(candidate.get("review_status") or "needs_review")
    candidate["classification_status"] = _nonempty(candidate.get("classification_status") or "needs_review")
    candidate["exclusion_reason"] = _nonempty(candidate.get("exclusion_reason"))
    candidate["fetch_status"] = _nonempty(candidate.get("fetch_status") or "unfetched")
    candidate["fetch_error"] = _nonempty(candidate.get("fetch_error"))
    candidate["direct_source_name"] = _nonempty(candidate.get("direct_source_name"))
    candidate["feed_url"] = _normalize_url(_nonempty(candidate.get("feed_url")))
    candidate["direct_fetch_status"] = _nonempty(candidate.get("direct_fetch_status"))
    candidate["direct_fetch_error"] = _nonempty(candidate.get("direct_fetch_error"))
    candidate["duplicate_of"] = _nonempty(candidate.get("duplicate_of"))
    candidate["public_claim_blockers"] = list(candidate.get("public_claim_blockers") or [])
    candidate["canonical_homepage_collapse_ignored"] = bool(candidate.get("canonical_homepage_collapse_ignored"))
    return candidate


def _classification_from_terms(text: str, *, fetch_status: str, duplicate: bool) -> tuple[str, str]:
    if duplicate:
        return "duplicate", "duplicate article trace"
    if fetch_status != "ok":
        return "blocked_fetch", "fetch failed before qualification"
    pressure_hits = _detect_terms(text, PRESSURE_TERMS)
    if pressure_hits:
        return "qualified_pressure_signal", ""
    return "context_only", "no current pressure evidence"


def _summary_reason_text(summary: dict[str, Any]) -> str:
    if summary["discovery_confidence"] == "high":
        return "Expanded queries retained multiple fetchable pressure signals and no major retention gaps."
    if summary["discovery_confidence"] == "moderate":
        return "Expanded queries retained pressure signals, with some blocked or review-needed candidates."
    if summary["discovery_confidence"] == "limited":
        return "Candidates were retained, but blocked fetches or context-only results limited confidence."
    return "No candidates were retained or query coverage was too thin to support a stronger conclusion."


def _discovery_confidence(summary: dict[str, Any], *, edition_mode: str) -> tuple[str, str]:
    candidate_count = int(summary.get("candidate_count") or 0)
    qualified = int(summary.get("qualified_pressure_signals") or 0)
    blocked = int(summary.get("blocked_fetch_count") or 0)
    manual_reviewable = int(summary.get("manual_reviewable_count") or 0)
    if candidate_count <= 0:
        return "low", "No retained candidates were discovered after running the expanded query families."
    if qualified > 0 and blocked == 0 and manual_reviewable <= candidate_count:
        return "high" if candidate_count >= 5 else "moderate", "At least one fetchable pressure signal was retained and no major fetch failures were recorded."
    if qualified > 0:
        return "moderate", "Pressure signals were found, but some candidates still need manual review or had fetch problems."
    if blocked > 0:
        if edition_mode == "no_current_update":
            return "limited", "No current update was qualified; blocked fetches kept the discovery set from supporting a stronger conclusion."
        return "limited", "Candidates were discovered, but blocked fetches prevented qualification."
    if manual_reviewable > 0:
        return "limited", "Candidates were discovered, but none were strong enough to avoid manual review."
    return "low", "Expanded searches returned only weak or context-only discovery items."


def _append_manual_fallbacks(candidates: list[dict[str, Any]], manual_fallback_records: list[dict[str, Any]] | None, edition_date: str) -> int:
    if not manual_fallback_records:
        return 0
    added = 0
    for record in manual_fallback_records:
        candidate, errors = _normalize_manual_fallback_record({**record, "date": _nonempty(record.get("date") or edition_date)})
        if errors:
            raise ValueError("Invalid manual fallback record: " + "; ".join(errors))
        trace_key = candidate.get("final_trace_url") or candidate.get("canonical_url") or candidate.get("discovered_url")
        existing = None
        if trace_key:
            for row in candidates:
                row_trace_key = row.get("final_trace_url") or row.get("canonical_url") or row.get("discovered_url")
                if _normalize_url(_nonempty(row_trace_key)) == _normalize_url(_nonempty(trace_key)) and not _nonempty(row.get("duplicate_of")):
                    existing = row
                    break
        if existing is not None:
            existing_candidate_id = _nonempty(existing.get("candidate_id"))
            if existing_candidate_id:
                existing["candidate_id"] = existing_candidate_id
            existing["classification_status"] = "manual_fallback"
            existing["review_status"] = "manual_reviewed"
            existing["manual_review_required"] = False
            existing["exclusion_reason"] = ""
            for key in (
                "manually_reviewed_summary",
                "pressure_evidence_summary",
                "affected_groups",
                "limitations",
                "extraction_quality",
                "reviewer_or_source_note",
                "source_note",
                "discovery_date",
                "geographic_scope",
                "state_or_territory",
                "metro",
            ):
                existing[key] = candidate.get(key, existing.get(key))
        else:
            candidates.append(candidate)
        added += 1
    return added


def run_food_line_discovery_expansion(
    root: Path,
    edition_date: str,
    *,
    fetcher: Any | None = None,
    manual_fallback_records: list[dict[str, Any]] | None = None,
    manual_fallback_path: Path | None = None,
    edition_mode: str = "current_update",
    max_results_per_query: int = 10,
    max_queries: int | None = None,
    query_lookback_days: int = 1,
    query_lookahead_days: int = 1,
    public_claim_lookback_days: int = 0,
    public_claim_lookahead_days: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    date_text = validate_date(edition_date)
    fetch = resolve_food_line_fetcher(fetcher)
    config = load_food_line_discovery_expansion_config(root)
    configured_direct_sources = [row for row in config.get("direct_sources") or [] if isinstance(row, dict)]
    query_plan = build_food_line_discovery_query_plan(
        root,
        date_text,
        lookback_days=query_lookback_days,
        lookahead_days=query_lookahead_days,
    )
    configured_lanes = sorted({_query_family_to_lane(_nonempty(row.get("query_family"))) for row in query_plan if _nonempty(row.get("query_family"))})
    query_plan = _sample_query_plan_across_families(query_plan, max_queries)
    candidates: list[dict[str, Any]] = []
    query_rows: list[dict[str, Any]] = []
    discovered_at = _utc_now()
    query_family_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    metro_counts: Counter[str] = Counter()
    duplicate_count = 0
    blocked_fetch_count = 0
    homepage_only_trace_count = 0
    outside_window_count = 0
    qualified_pressure_signals = 0
    context_only_count = 0
    manual_reviewable_count = 0
    fetchable_count = 0
    google_news_url_count = 0
    google_news_resolution_attempt_count = 0
    google_news_resolution_success_count = 0
    google_news_resolution_failure_count = 0
    google_news_resolved_article_url_count = 0
    google_news_resolved_homepage_only_count = 0
    canonical_homepage_collapse_ignored_count = 0
    article_specific_url_count = 0
    unresolved_google_news_count = 0
    direct_source_count = 0
    direct_source_fetch_attempt_count = 0
    direct_source_fetch_success_count = 0
    direct_source_fetch_failure_count = 0
    direct_article_url_count = 0
    direct_homepage_or_feed_blocked_count = 0
    google_news_fallback_count = 0
    duplicate_preferred_direct_count = 0
    resolution_status_counts: Counter[str] = Counter()
    resolution_debug_by_candidate: dict[str, dict[str, Any]] = {}
    direct_source_counts: Counter[str] = Counter()
    direct_source_lane_counts: Counter[str] = Counter()
    direct_source_fetch_failure_reasons: Counter[str] = Counter()
    direct_source_candidate_cap_hits: Counter[str] = Counter()
    accepted_direct_source_counts: Counter[str] = Counter()
    accepted_direct_source_lane_counts: Counter[str] = Counter()
    direct_source_fetch_failure_reasons_by_source: dict[str, Counter[str]] = {}
    direct_source_success_by_source: Counter[str] = Counter()
    direct_source_item_counts: Counter[str] = Counter()
    direct_source_zero_item_sources: set[str] = set()
    disabled_direct_sources = sorted(_nonempty(row.get("source_name")) for row in configured_direct_sources if not bool(row.get("enabled", True)) and _nonempty(row.get("source_name")))
    direct_source_diagnostics: list[dict[str, Any]] = []
    for source_row in configured_direct_sources:
        source_name = _nonempty(source_row.get("source_name"))
        if not source_name or bool(source_row.get("enabled", True)):
            continue
        direct_source_diagnostics.append(
            {
                "direct_source_name": source_name,
                "lane": _nonempty(source_row.get("discovery_lane") or source_row.get("source_family") or "news_article"),
                "discovery_channel": _nonempty(source_row.get("discovery_channel") or ("direct_rss" if _nonempty(source_row.get("feed_url")) else "direct_page")),
                "configured_url": _nonempty(source_row.get("feed_url") or source_row.get("source_url")),
                "final_response_url": "",
                "http_status": None,
                "content_type": "",
                "failure_reason": "disabled_source",
                "exception_message": "",
                "parser_attempted": "",
                "item_count_extracted": 0,
                "source_disabled_or_skipped": True,
                "recommended_action": "disable_source",
            }
        )

    for query_row in query_plan:
        query_text = _nonempty(query_row.get("query_text"))
        result_row = dict(query_row)
        result_row["result_count"] = 0
        discovery_channel = _nonempty(query_row.get("discovery_channel") or "google_news_rss")
        if discovery_channel == "google_news_rss":
            query_url = _query_url(query_text)
            payload, query_error = _fetch_url(fetch, query_url)
            result_row["query_url"] = query_url
            result_row["query_error"] = query_error
            if query_error or not payload:
                query_rows.append(result_row)
                continue
            try:
                rss_items = _parse_google_news_rss(payload)
            except Exception as exc:  # noqa: BLE001
                result_row["query_error"] = f"{type(exc).__name__}: {exc}"
                query_rows.append(result_row)
                continue
        else:
            query_url = _nonempty(query_row.get("direct_source_feed_url") or query_row.get("direct_source_url"))
            items, direct_meta = _collect_direct_source_items(fetch, query_row)
            source_name = _nonempty(query_row.get("direct_source_name"))
            lane = _effective_lane(query_row)
            result_row["query_url"] = query_url
            result_row["query_error"] = _nonempty(direct_meta.get("error"))
            result_row["direct_source_name"] = source_name
            result_row["direct_fetch_status"] = "ok" if direct_meta.get("success") else _classify_fetch_error(_nonempty(direct_meta.get("error")) or "fetch_failed")
            result_row["direct_source_lane"] = lane
            result_row["configured_url"] = query_url
            result_row["final_response_url"] = _nonempty(direct_meta.get("final_response_url"))
            result_row["response_status"] = direct_meta.get("response_status")
            result_row["content_type"] = _nonempty(direct_meta.get("content_type"))
            result_row["parser_attempted"] = _nonempty(direct_meta.get("parser_attempted"))
            result_row["item_count"] = int(direct_meta.get("item_count") or 0)
            result_row["direct_source_enabled"] = bool(query_row.get("direct_source_enabled", True))
            result_row["exception_message"] = _nonempty(direct_meta.get("exception_message"))
            result_row["failure_reason"] = _nonempty(direct_meta.get("failure_reason") or result_row["direct_fetch_status"])
            result_row["direct_source_skipped"] = False
            result_row["recommended_action"] = _recommended_direct_source_action(
                discovery_channel=discovery_channel,
                enabled=bool(query_row.get("direct_source_enabled", True)),
                direct_fetch_status=_nonempty(result_row["direct_fetch_status"]),
                parser_attempted=_nonempty(result_row.get("parser_attempted")),
                item_count=int(result_row.get("item_count") or 0),
                content_type=_nonempty(result_row.get("content_type")),
            )
            direct_source_count += 1
            if direct_meta.get("attempted"):
                direct_source_fetch_attempt_count += 1
            if direct_meta.get("success"):
                direct_source_fetch_success_count += 1
                if source_name:
                    direct_source_success_by_source[source_name] += 1
            else:
                direct_source_fetch_failure_count += 1
                direct_source_fetch_failure_reasons[result_row["direct_fetch_status"]] += 1
                if source_name:
                    direct_source_fetch_failure_reasons_by_source.setdefault(source_name, Counter())[result_row["direct_fetch_status"]] += 1
            if source_name:
                direct_source_item_counts[source_name] += int(direct_meta.get("item_count") or 0)
                if direct_meta.get("success") and not int(direct_meta.get("item_count") or 0):
                    direct_source_zero_item_sources.add(source_name)
            direct_source_diagnostics.append(
                {
                    "direct_source_name": source_name,
                    "lane": lane,
                    "discovery_channel": discovery_channel,
                    "configured_url": query_url,
                    "final_response_url": _nonempty(direct_meta.get("final_response_url")),
                    "http_status": direct_meta.get("response_status"),
                    "content_type": _nonempty(direct_meta.get("content_type")),
                    "failure_reason": _nonempty(direct_meta.get("failure_reason") or result_row["direct_fetch_status"]),
                    "exception_message": _nonempty(direct_meta.get("exception_message")),
                    "parser_attempted": _nonempty(direct_meta.get("parser_attempted")),
                    "item_count_extracted": int(direct_meta.get("item_count") or 0),
                    "source_disabled_or_skipped": False,
                    "recommended_action": _nonempty(result_row["recommended_action"]),
                }
            )
            rss_items = items
        result_row["result_count"] = len(rss_items)
        query_rows.append(result_row)
        for item in rss_items[:max_results_per_query]:
            discovered_url = _normalize_url(_nonempty(item.get("link")))
            direct_source_name = _nonempty(query_row.get("direct_source_name"))
            feed_url = _normalize_url(_nonempty(query_row.get("direct_source_feed_url")))
            direct_fetch_status = ""
            direct_fetch_error = ""
            direct_source_lane_key = ""
            google_news_url = discovered_url if GOOGLE_NEWS_DOMAIN in discovered_url else ""
            publisher_url = _normalize_url(_nonempty(item.get("source_url")))
            resolved_wrapper_url = ""
            google_news_resolution_error = ""
            google_news_resolution_attempted = False
            google_news_resolution_failed = False
            google_news_resolution_status = ""
            google_news_debug: dict[str, Any] = {}
            if discovery_channel == "google_news_rss":
                google_news_fallback_count += 1
                if google_news_url and not _is_article_specific_url(publisher_url):
                    resolved_wrapper_url, google_news_resolution_error, google_news_resolution_attempted, google_news_debug = _resolve_google_news_wrapper(
                        fetch,
                        google_news_url,
                        publisher_url=publisher_url,
                    )
                    google_news_resolution_status = _nonempty(google_news_debug.get("google_news_resolution_status"))
                    if google_news_resolution_attempted:
                        google_news_resolution_attempt_count += 1
                        if _is_article_specific_url(resolved_wrapper_url):
                            google_news_resolution_success_count += 1
                            google_news_resolved_article_url_count += 1
                        elif _is_homepage_only_url(resolved_wrapper_url):
                            google_news_resolution_failure_count += 1
                            google_news_resolved_homepage_only_count += 1
                            google_news_resolution_failed = True
                        else:
                            google_news_resolution_failure_count += 1
                            google_news_resolution_failed = True
            candidate_source_url = _initial_trace_url(discovered_url, resolved_wrapper_url or publisher_url)
            if discovery_channel != "google_news_rss":
                candidate_source_url = discovered_url or publisher_url or _normalize_url(_nonempty(query_row.get("direct_source_url")))
            final_trace_url = candidate_source_url or ""
            canonical = _normalize_url(final_trace_url)
            fetch_status = "unfetched"
            fetch_error = google_news_resolution_error
            canonical_from_page = ""
            page_title = ""
            page_summary = ""
            page_text = ""
            page_published_date = ""
            evidence_text = _nonempty(item.get("title")) + " " + _nonempty(item.get("description"))
            if discovery_channel != "google_news_rss":
                direct_fetch_status = "ok"
                direct_fetch_error = ""
                if not _domain_allowed(candidate_source_url, list(query_row.get("allowed_domains") or [])):
                    fetch_status = "blocked_domain_mismatch"
                    fetch_error = "direct source item URL outside allowed_domains"
                    direct_fetch_status = fetch_status
                    direct_fetch_error = fetch_error
                elif _is_feed_or_listing_url(candidate_source_url, feed_url=feed_url):
                    fetch_status = "blocked_listing_url"
                    fetch_error = "direct source item resolved to homepage, feed, or listing page"
                    direct_fetch_status = fetch_status
                    direct_fetch_error = fetch_error
                    direct_homepage_or_feed_blocked_count += 1
                elif final_trace_url:
                    payload2, fetch_error = _fetch_url(fetch, final_trace_url)
                    if fetch_error or not payload2:
                        fetch_status = _classify_fetch_error(fetch_error or "empty response")
                        direct_fetch_status = fetch_status
                        direct_fetch_error = fetch_error
                    else:
                        fetch_status = "ok"
                        direct_fetch_status = "ok"
                        fetchable_count += 1
                        canonical_from_page = _extract_canonical_url(payload2)
                        page_title = _page_title(payload2)
                        page_summary = _page_summary(payload2)
                        page_text = _page_text(payload2)
                        page_published_date = _extract_page_published_date(payload2)
                        if _is_article_specific_url(canonical_from_page):
                            canonical = canonical_from_page
                            final_trace_url = canonical_from_page
                        evidence_text = " ".join(
                            part
                            for part in (
                                _nonempty(item.get("title")),
                                _nonempty(item.get("description")),
                                _nonempty(item.get("source_name") or direct_source_name),
                                page_title,
                                page_summary,
                                page_text,
                                canonical_from_page,
                            )
                            if part
                        )
            elif final_trace_url:
                payload2, fetch_error = _fetch_url(fetch, final_trace_url)
                if fetch_error or not payload2:
                    fetch_status = _classify_fetch_error(fetch_error or "empty response")
                else:
                    fetch_status = "ok"
                    fetchable_count += 1
                    canonical_from_page = _extract_canonical_url(payload2)
                    page_title = _page_title(payload2)
                    page_summary = _page_summary(payload2)
                    page_text = _page_text(payload2)
                    page_published_date = _extract_page_published_date(payload2)
                    if _is_article_specific_url(canonical_from_page):
                        canonical = canonical_from_page
                        if not _is_article_specific_url(final_trace_url):
                            final_trace_url = canonical_from_page
                    elif _is_homepage_only_url(canonical_from_page) and _is_article_specific_url(final_trace_url):
                        canonical_homepage_collapse_ignored_count += 1
                    elif canonical_from_page and not canonical:
                        canonical = canonical_from_page
                    evidence_text = " ".join(
                        part
                        for part in (
                            _nonempty(item.get("title")),
                            _nonempty(item.get("description")),
                            _nonempty(item.get("source_name")),
                            page_title,
                            page_summary,
                            page_text,
                            canonical_from_page,
                        )
                        if part
                    )
            classification_status, exclusion_reason = _classification_from_terms(
                evidence_text,
                fetch_status=fetch_status,
                duplicate=False,
            )
            lane = _effective_lane(query_row) if discovery_channel != "google_news_rss" else _lane_from_query(_nonempty(query_row.get("query_family")), discovered_url, publisher_url, _nonempty(item.get("source_name")))
            direct_source_lane_key = f"{direct_source_name} | {lane}" if direct_source_name else lane
            if discovery_channel != "google_news_rss":
                source_cap = max(0, int(query_row.get("direct_source_candidate_cap") or 0))
                lane_cap = max(0, int(query_row.get("direct_lane_candidate_cap") or 0))
                if source_cap and accepted_direct_source_counts[direct_source_name] >= source_cap:
                    direct_source_candidate_cap_hits[direct_source_name] += 1
                    continue
                if lane_cap and accepted_direct_source_lane_counts[direct_source_lane_key] >= lane_cap:
                    direct_source_candidate_cap_hits[direct_source_lane_key] += 1
                    continue
            pressure_hits = _detect_terms(evidence_text.lower(), PRESSURE_TERMS)
            if discovery_channel != "google_news_rss":
                pressure_hits = _detect_terms(evidence_text.lower(), list(query_row.get("pressure_terms") or PRESSURE_TERMS))
                exclusion_hits = _detect_terms(evidence_text.lower(), list(query_row.get("exclusion_terms") or []))
                if exclusion_hits and classification_status == "qualified_pressure_signal":
                    classification_status = "context_only"
                    exclusion_reason = "matched direct-source exclusion terms"
            location_hits = []
            for term in [query_row.get("state_or_territory"), query_row.get("metro")]:
                term_text = _nonempty(term)
                if term_text and term_text.lower() in evidence_text.lower() and term_text not in location_hits:
                    location_hits.append(term_text)
            source_published_date = _extract_source_date(_nonempty(item.get("pubDate"))) or page_published_date
            source_url = _choose_trace_url(final_trace_url or publisher_url, canonical, discovered_url)
            traceability_status = _discovery_traceability_status(
                source_url=source_url,
                original_source_url=final_trace_url or publisher_url,
                discovered_url=discovered_url,
                fetch_status=fetch_status,
                google_news_resolution_failed=google_news_resolution_failed and not source_url and not (final_trace_url or publisher_url),
            )
            candidate_review_status, public_claim_eligible, public_claim_blockers = _candidate_review_defaults(
                edition_date=date_text,
                source_published_date=source_published_date,
                lookback_days=public_claim_lookback_days,
                lookahead_days=public_claim_lookahead_days,
                lane=lane,
                classification_status=classification_status,
                fetch_status=fetch_status,
                traceability_status=traceability_status,
                duplicate_of="",
            )
            row = {
                "candidate_id": _candidate_id(final_trace_url or discovered_url, _nonempty(item.get("source_name") or item.get("source_url")), _nonempty(query_row.get("query_family"))),
                "discovery_date": date_text,
                "discovered_at": discovered_at,
                "discovery_lane": lane,
                "discovery_query": query_text,
                "discovery_source_type": _discovery_source_type(_nonempty(query_row.get("query_family")), discovered_url, publisher_url, fetch_status),
                "query_family": _nonempty(query_row.get("query_family")),
                "query_text": query_text,
                "geographic_scope": _nonempty(query_row.get("geographic_scope")),
                "state_or_territory": _nonempty(query_row.get("state_or_territory")),
                "state_abbrev": _nonempty(query_row.get("state_abbrev")),
                "state_hint": _nonempty(query_row.get("state_abbrev") or query_row.get("state_or_territory")),
                "metro": _nonempty(query_row.get("metro")),
                "discovery_channel": discovery_channel,
                "discovered_title": _nonempty(item.get("title")),
                "discovered_publisher": _nonempty(item.get("source_name") or item.get("source_url") or direct_source_name),
                "discovered_url": discovered_url,
                "canonical_url": canonical,
                "source_url": source_url,
                "original_source_url": final_trace_url or publisher_url or "",
                "google_news_url": google_news_url,
                "direct_source_name": direct_source_name,
                "feed_url": feed_url,
                "direct_fetch_status": direct_fetch_status,
                "direct_fetch_error": direct_fetch_error,
                "google_news_resolution_attempted": google_news_resolution_attempted,
                "google_news_resolution_error": google_news_resolution_error,
                "google_news_resolved_url": resolved_wrapper_url,
                "canonical_homepage_collapse_ignored": bool(
                    _is_homepage_only_url(canonical_from_page) and _is_article_specific_url(final_trace_url)
                ) if fetch_status == "ok" else False,
                "publication_date": _nonempty(item.get("pubDate")),
                "source_published_date": source_published_date,
                "date_basis": "pubDate" if source_published_date else "unknown",
                "fetch_status": fetch_status,
                "fetch_error": fetch_error,
                "final_trace_url": final_trace_url,
                "duplicate_of": "",
                "review_status": "needs_review",
                "candidate_review_status": candidate_review_status,
                "classification_status": classification_status,
                "exclusion_reason": exclusion_reason,
                "pressure_terms_detected": pressure_hits,
                "location_terms_detected": location_hits,
                "manual_review_required": True,
                "location_scope": _nonempty(query_row.get("geographic_scope")),
                "pressure_signal_hint": ", ".join(pressure_hits[:4]),
                "pressure_signal_type_hint": _nonempty(classification_status),
                "traceability_status": traceability_status,
                "public_claim_eligible": public_claim_eligible,
                "public_claim_blockers": public_claim_blockers,
                "query_url": query_url,
                "retrieved_at": discovered_at,
                "_google_news_resolution_attempted": google_news_resolution_attempted,
                "_google_news_resolution_error": google_news_resolution_error,
                "_google_news_resolved_url": resolved_wrapper_url,
                "_google_news_resolution_status": google_news_resolution_status,
            }
            if google_news_resolution_status:
                resolution_status_counts[google_news_resolution_status] += 1
                resolution_debug_by_candidate[row["candidate_id"]] = {
                    "google_news_resolution_status": google_news_resolution_status,
                    "response_status": google_news_debug.get("response_status"),
                    "final_response_url": _nonempty(google_news_debug.get("final_response_url")),
                    "content_type": _nonempty(google_news_debug.get("content_type")),
                    "redirect_chain": list(google_news_debug.get("redirect_chain") or []),
                    "candidate_url_count_extracted": int(google_news_debug.get("candidate_url_count_extracted") or 0),
                    "accepted_candidate_url": _nonempty(google_news_debug.get("accepted_candidate_url")),
                    "rejection_reason": _nonempty(google_news_debug.get("rejection_reason")),
                    "debug_snippet": _nonempty(google_news_debug.get("debug_snippet")),
                }
            if row["fetch_status"] != "ok":
                blocked_fetch_count += 1
                row["manual_review_required"] = True
                if row["discovery_lane"] != "social_watchlist":
                    row["candidate_review_status"] = "needs_review"
            if row["classification_status"] == "qualified_pressure_signal":
                qualified_pressure_signals += 1
            elif row["classification_status"] == "context_only":
                context_only_count += 1
            if "publisher_homepage_trace_only" in row["public_claim_blockers"]:
                homepage_only_trace_count += 1
            if "outside_backfill_date_window" in row["public_claim_blockers"]:
                outside_window_count += 1
            if row["google_news_url"]:
                google_news_url_count += 1
                if row["traceability_status"] == "unresolved_google_news":
                    unresolved_google_news_count += 1
            if _is_article_specific_url(_nonempty(row.get("source_url") or row.get("final_trace_url"))):
                article_specific_url_count += 1
                if discovery_channel != "google_news_rss":
                    direct_article_url_count += 1
            manual_reviewable_count += 1 if row["manual_review_required"] else 0
            query_family_counts[row["query_family"]] += 1
            if row["state_or_territory"]:
                state_counts[row["state_or_territory"]] += 1
            if row["metro"]:
                metro_counts[row["metro"]] += 1
            candidates.append(_normalize_candidate_row(row))
            if discovery_channel != "google_news_rss":
                accepted_direct_source_counts[direct_source_name] += 1
                accepted_direct_source_lane_counts[direct_source_lane_key] += 1

    manual_fallback_count = _append_manual_fallbacks(candidates, manual_fallback_records, date_text)
    if manual_fallback_path and manual_fallback_path.exists():
        fallback_payload = _read_json(manual_fallback_path)
        if isinstance(fallback_payload, list):
            manual_fallback_count += _append_manual_fallbacks(candidates, [row for row in fallback_payload if isinstance(row, dict)], date_text)
        else:
            raise ValueError(f"{manual_fallback_path} must contain a JSON list")

    duplicate_preferred_direct_count = _dedupe_candidates(candidates)
    for row in candidates:
        if _nonempty(row.get("duplicate_of")):
            row["classification_status"] = "duplicate"
            row["exclusion_reason"] = f"duplicate of {row['duplicate_of']}"
            row["public_claim_eligible"] = False
            row["public_claim_blockers"] = sorted(set([*list(row.get("public_claim_blockers") or []), "duplicate"]))
        if row.get("classification_status") == "manual_fallback":
            row["manual_review_required"] = False
            row["candidate_review_status"] = "needs_review"
        row["traceability_status"] = _discovery_traceability_status(
            source_url=_nonempty(row.get("source_url") or row.get("final_trace_url")),
            original_source_url=_nonempty(row.get("original_source_url") or row.get("final_trace_url")),
            discovered_url=_nonempty(row.get("discovered_url")),
            fetch_status=_nonempty(row.get("fetch_status")),
            google_news_resolution_failed=(
                bool(row.get("_google_news_resolution_attempted"))
                and _nonempty(row.get("_google_news_resolution_status")).startswith("failed_")
                and not bool(_nonempty(row.get("source_url") or row.get("final_trace_url")))
                and not bool(_nonempty(row.get("original_source_url")))
            ),
        )
        row["candidate_review_status"], row["public_claim_eligible"], row["public_claim_blockers"] = _candidate_review_defaults(
            edition_date=date_text,
            source_published_date=_nonempty(row.get("source_published_date")),
            lookback_days=public_claim_lookback_days,
            lookahead_days=public_claim_lookahead_days,
            lane=_nonempty(row.get("discovery_lane")),
            classification_status=_nonempty(row.get("classification_status")),
            fetch_status=_nonempty(row.get("fetch_status")),
            traceability_status=_nonempty(row.get("traceability_status")),
            duplicate_of=_nonempty(row.get("duplicate_of")),
        )
        if row.get("classification_status") == "manual_fallback":
            row["candidate_review_status"] = "needs_review"
    for row in candidates:
        for key in (
            "_google_news_resolution_attempted",
            "_google_news_resolution_error",
            "_google_news_resolved_url",
            "_google_news_resolution_status",
        ):
            row.pop(key, None)
    candidate_count = len(candidates)
    query_family_counts = Counter(_nonempty(row.get("query_family")) for row in candidates if _nonempty(row.get("query_family")))
    lane_counts = Counter(_nonempty(row.get("discovery_lane")) for row in candidates if _nonempty(row.get("discovery_lane")))
    discovery_channel_counts = Counter(_nonempty(row.get("discovery_channel")) for row in candidates if _nonempty(row.get("discovery_channel")))
    direct_source_counts = Counter(_nonempty(row.get("direct_source_name")) for row in candidates if _nonempty(row.get("direct_source_name")))
    direct_source_lane_counts = Counter(
        f"{_nonempty(row.get('direct_source_name'))} | {_nonempty(row.get('discovery_lane'))}"
        for row in candidates
        if _nonempty(row.get("direct_source_name")) and _nonempty(row.get("discovery_lane"))
    )
    source_type_counts = Counter(_nonempty(row.get("discovery_source_type")) for row in candidates if _nonempty(row.get("discovery_source_type")))
    state_counts = Counter(_nonempty(row.get("state_or_territory")) for row in candidates if _nonempty(row.get("state_or_territory")))
    metro_counts = Counter(_nonempty(row.get("metro")) for row in candidates if _nonempty(row.get("metro")))
    duplicate_count = sum(1 for row in candidates if _nonempty(row.get("duplicate_of")))
    blocked_fetch_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) not in {"ok", "manual_fallback"})
    homepage_only_trace_count = sum(1 for row in candidates if "publisher_homepage_trace_only" in list(row.get("public_claim_blockers") or []))
    outside_window_count = sum(1 for row in candidates if "outside_backfill_date_window" in list(row.get("public_claim_blockers") or []))
    fetchable_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) == "ok")
    manual_reviewable_count = sum(1 for row in candidates if bool(row.get("manual_review_required")))
    google_news_url_count = sum(1 for row in candidates if _nonempty(row.get("google_news_url")))
    google_news_resolution_attempt_count = sum(int(count) for status, count in resolution_status_counts.items())
    google_news_resolution_success_count = int(resolution_status_counts.get("success_article", 0))
    google_news_resolution_failure_count = google_news_resolution_attempt_count - google_news_resolution_success_count
    google_news_resolved_article_url_count = google_news_resolution_success_count
    google_news_resolved_homepage_only_count = int(resolution_status_counts.get("success_homepage_only", 0))
    article_specific_url_count = sum(
        1 for row in candidates if _is_article_specific_url(_nonempty(row.get("source_url") or row.get("final_trace_url")))
    )
    direct_source_count = len(query_rows) - sum(1 for row in query_rows if _nonempty(row.get("discovery_channel")) == "google_news_rss")
    direct_source_fetch_attempt_count = sum(1 for row in query_rows if _nonempty(row.get("discovery_channel")) != "google_news_rss")
    direct_source_fetch_success_count = sum(
        1 for row in query_rows if _nonempty(row.get("discovery_channel")) != "google_news_rss" and not _nonempty(row.get("query_error"))
    )
    direct_source_fetch_failure_count = max(0, direct_source_fetch_attempt_count - direct_source_fetch_success_count)
    for row in candidates:
        if _nonempty(row.get("discovery_channel")) != "google_news_rss":
            fetch_status = _nonempty(row.get("fetch_status"))
            if fetch_status not in {"", "ok", "manual_fallback"}:
                direct_source_fetch_failure_reasons[fetch_status] += 1
    direct_article_url_count = sum(
        1
        for row in candidates
        if _nonempty(row.get("discovery_channel")) != "google_news_rss"
        and _is_article_specific_url(_nonempty(row.get("source_url") or row.get("final_trace_url")))
    )
    direct_homepage_or_feed_blocked_count = sum(
        1
        for row in candidates
        if _nonempty(row.get("discovery_channel")) != "google_news_rss"
        and (
            "non_article_trace_url" in list(row.get("public_claim_blockers") or [])
            or "publisher_homepage_trace_only" in list(row.get("public_claim_blockers") or [])
        )
    )
    google_news_fallback_count = sum(1 for row in candidates if _nonempty(row.get("discovery_channel")) == "google_news_rss")
    unresolved_google_news_count = sum(
        1
        for row in candidates
        if _nonempty(row.get("traceability_status")) == "unresolved_google_news"
    )
    canonical_homepage_collapse_ignored_count = sum(1 for row in candidates if bool(row.get("canonical_homepage_collapse_ignored")))
    qualified_pressure_signals = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "qualified_pressure_signal")
    context_only_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "context_only")
    manual_fallback_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "manual_fallback")
    in_window_candidate_count = sum(
        1
        for row in candidates
        if _published_date_in_window(
            date_text,
            _nonempty(row.get("source_published_date")),
            lookback_days=public_claim_lookback_days,
            lookahead_days=public_claim_lookahead_days,
        )
    )
    public_eligible_candidate_count = sum(1 for row in candidates if bool(row.get("public_claim_eligible")))
    dominant_source_warning = ""
    if candidate_count > 0 and direct_source_counts:
        top_source, top_count = max(direct_source_counts.items(), key=lambda item: item[1])
        if top_count > candidate_count / 2:
            dominant_source_warning = f"{top_source} contributed {top_count} of {candidate_count} candidates."
    direct_sources_recommended_for_disable = sorted(
        {
            _nonempty(row.get("direct_source_name"))
            for row in direct_source_diagnostics
            if _nonempty(row.get("recommended_action")) == "disable_source" and _nonempty(row.get("direct_source_name"))
        }
    )
    direct_sources_recommended_for_url_refresh = sorted(
        {
            _nonempty(row.get("direct_source_name"))
            for row in direct_source_diagnostics
            if _nonempty(row.get("recommended_action")) == "replace_url" and _nonempty(row.get("direct_source_name"))
        }
    )
    direct_sources_recommended_for_parser_fix = sorted(
        {
            _nonempty(row.get("direct_source_name"))
            for row in direct_source_diagnostics
            if _nonempty(row.get("recommended_action")) == "fix_parser" and _nonempty(row.get("direct_source_name"))
        }
    )
    executed_lanes = sorted({_query_family_to_lane(_nonempty(row.get("query_family"))) for row in query_rows if _nonempty(row.get("query_family"))})
    skipped_lanes = [lane for lane in configured_lanes if lane not in executed_lanes]
    discovery_confidence, discovery_confidence_reason = _discovery_confidence(
        {
            "candidate_count": candidate_count,
            "qualified_pressure_signals": qualified_pressure_signals,
            "blocked_fetch_count": blocked_fetch_count,
            "manual_reviewable_count": manual_reviewable_count,
        },
        edition_mode=edition_mode,
    )
    candidate_dir = root / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_DIR_NAME / date_text
    audit_dir = root / "output" / "review" / DISPATCH_SLUG / date_text
    candidate_path = candidate_dir / DISCOVERY_CANDIDATES_FILE
    audit_json_path = audit_dir / DISCOVERY_AUDIT_JSON_FILE
    audit_md_path = audit_dir / DISCOVERY_AUDIT_MD_FILE
    audit_summary = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": date_text,
        "generated_at": discovered_at,
        "edition_mode": edition_mode,
        "query_count": len(query_rows),
        "candidate_count": candidate_count,
        "duplicate_count": duplicate_count,
        "fetchable_count": fetchable_count,
        "blocked_fetch_count": blocked_fetch_count,
        "direct_source_count": direct_source_count,
        "direct_source_fetch_attempt_count": direct_source_fetch_attempt_count,
        "direct_source_fetch_success_count": direct_source_fetch_success_count,
        "direct_source_fetch_failure_count": direct_source_fetch_failure_count,
        "direct_article_url_count": direct_article_url_count,
        "direct_homepage_or_feed_blocked_count": direct_homepage_or_feed_blocked_count,
        "google_news_fallback_count": google_news_fallback_count,
        "google_news_url_count": google_news_url_count,
        "google_news_resolution_attempt_count": google_news_resolution_attempt_count,
        "google_news_resolution_success_count": google_news_resolution_success_count,
        "google_news_resolution_failure_count": google_news_resolution_failure_count,
        "google_news_resolved_article_url_count": google_news_resolved_article_url_count,
        "google_news_resolved_homepage_only_count": google_news_resolved_homepage_only_count,
        "google_news_resolution_status_counts": dict(sorted(resolution_status_counts.items())),
        "canonical_homepage_collapse_ignored_count": canonical_homepage_collapse_ignored_count,
        "article_specific_url_count": article_specific_url_count,
        "homepage_only_trace_count": homepage_only_trace_count,
        "publisher_homepage_trace_only_count": homepage_only_trace_count,
        "unresolved_google_news_count": unresolved_google_news_count,
        "outside_backfill_date_window_count": outside_window_count,
        "in_window_candidate_count": in_window_candidate_count,
        "out_of_window_candidate_count": max(0, candidate_count - in_window_candidate_count),
        "public_eligible_candidate_count": public_eligible_candidate_count,
        "manually_reviewable_count": manual_reviewable_count,
        "qualified_pressure_signals": qualified_pressure_signals,
        "context_only_count": context_only_count,
        "manual_fallback_count": manual_fallback_count,
        "duplicate_preferred_direct_count": duplicate_preferred_direct_count,
        "direct_source_fetch_failure_reasons": dict(sorted(direct_source_fetch_failure_reasons.items())),
        "direct_source_fetch_failure_reasons_by_source": {
            key: dict(sorted(value.items()))
            for key, value in sorted(direct_source_fetch_failure_reasons_by_source.items())
        },
        "direct_source_success_by_source": dict(sorted(direct_source_success_by_source.items())),
        "direct_source_item_counts": dict(sorted(direct_source_item_counts.items())),
        "direct_source_zero_item_sources": sorted(direct_source_zero_item_sources),
        "disabled_direct_sources": disabled_direct_sources,
        "direct_sources_recommended_for_disable": direct_sources_recommended_for_disable,
        "direct_sources_recommended_for_url_refresh": direct_sources_recommended_for_url_refresh,
        "direct_sources_recommended_for_parser_fix": direct_sources_recommended_for_parser_fix,
        "direct_source_candidate_cap_hits": dict(sorted(direct_source_candidate_cap_hits.items())),
        "dominant_source_warning": dominant_source_warning,
        "configured_lanes": configured_lanes,
        "executed_lanes": executed_lanes,
        "skipped_lanes": skipped_lanes,
        "candidates_by_lane": dict(sorted(lane_counts.items())),
        "candidates_by_discovery_channel": dict(sorted(discovery_channel_counts.items())),
        "candidates_by_direct_source": dict(sorted(direct_source_counts.items())),
        "candidates_by_direct_source_lane": dict(sorted(direct_source_lane_counts.items())),
        "candidates_by_query_family": dict(sorted(query_family_counts.items())),
        "candidates_by_discovery_lane": dict(sorted(lane_counts.items())),
        "candidates_by_discovery_source_type": dict(sorted(source_type_counts.items())),
        "candidates_by_state_or_territory": dict(sorted(state_counts.items())),
        "candidates_by_metro": dict(sorted(metro_counts.items())),
        "query_rows": query_rows,
        "direct_source_diagnostics": direct_source_diagnostics,
        "google_news_resolution_debug_by_candidate": resolution_debug_by_candidate,
        "query_lookback_days": int(query_lookback_days),
        "query_lookahead_days": int(query_lookahead_days),
        "public_claim_lookback_days": int(public_claim_lookback_days),
        "public_claim_lookahead_days": int(public_claim_lookahead_days),
        "discovery_candidates_path": str(candidate_path),
        "discovery_audit_json_path": str(audit_json_path),
        "discovery_audit_md_path": str(audit_md_path),
        "discovery_confidence": discovery_confidence,
        "discovery_confidence_reason": discovery_confidence_reason,
        "discovery_confidence_summary": _summary_reason_text(
            {
                "discovery_confidence": discovery_confidence,
                "candidate_count": candidate_count,
                "qualified_pressure_signals": qualified_pressure_signals,
                "blocked_fetch_count": blocked_fetch_count,
                "manual_reviewable_count": manual_reviewable_count,
            }
        ),
        "no_current_update": edition_mode == "no_current_update",
        "no_current_update_reason": discovery_confidence_reason if edition_mode == "no_current_update" else "",
    }
    if not dry_run:
        _write_json(candidate_path, candidates)
        _write_json(audit_json_path, audit_summary)
        audit_md_path.parent.mkdir(parents=True, exist_ok=True)
        md_lines = [
            f"# Food Line Discovery Audit - {date_text}",
            "",
            f"- Edition mode: `{edition_mode}`",
            f"- Discovery confidence: `{discovery_confidence}`",
            f"- Discovery confidence reason: {discovery_confidence_reason}",
            f"- Total queries run: `{len(query_rows)}`",
            f"- Total candidates discovered: `{candidate_count}`",
            f"- Fetchable candidates: `{fetchable_count}`",
            f"- Blocked or failed fetches: `{blocked_fetch_count}`",
            f"- Direct sources scanned: `{direct_source_count}`",
            f"- Direct source fetch attempts: `{direct_source_fetch_attempt_count}`",
            f"- Direct source fetch successes: `{direct_source_fetch_success_count}`",
            f"- Direct source fetch failures: `{direct_source_fetch_failure_count}`",
            f"- Direct article URLs: `{direct_article_url_count}`",
            f"- Direct homepage/feed blocks: `{direct_homepage_or_feed_blocked_count}`",
            f"- Google News fallback candidates: `{google_news_fallback_count}`",
            f"- Direct source fetch failure reasons: `{dict(sorted(direct_source_fetch_failure_reasons.items()))}`",
            f"- Direct source fetch failure reasons by source: `{audit_summary['direct_source_fetch_failure_reasons_by_source']}`",
            f"- Direct source successes by source: `{audit_summary['direct_source_success_by_source']}`",
            f"- Direct source item counts: `{audit_summary['direct_source_item_counts']}`",
            f"- Direct source zero-item sources: `{audit_summary['direct_source_zero_item_sources']}`",
            f"- Disabled direct sources: `{disabled_direct_sources}`",
            f"- Recommended direct-source disables: `{direct_sources_recommended_for_disable}`",
            f"- Recommended direct-source URL refreshes: `{direct_sources_recommended_for_url_refresh}`",
            f"- Recommended direct-source parser fixes: `{direct_sources_recommended_for_parser_fix}`",
            f"- Direct source candidate cap hits: `{dict(sorted(direct_source_candidate_cap_hits.items()))}`",
            f"- Dominant source warning: {dominant_source_warning or 'none'}",
            f"- Google News wrapper URLs: `{google_news_url_count}`",
            f"- Google News resolution attempts: `{google_news_resolution_attempt_count}`",
            f"- Google News resolution successes: `{google_news_resolution_success_count}`",
            f"- Google News resolution failures: `{google_news_resolution_failure_count}`",
            f"- Google News resolved article URLs: `{google_news_resolved_article_url_count}`",
            f"- Google News resolved homepage-only URLs: `{google_news_resolved_homepage_only_count}`",
            f"- Canonical homepage collapse ignored: `{canonical_homepage_collapse_ignored_count}`",
            f"- Article-specific trace URLs: `{article_specific_url_count}`",
            f"- Homepage-only traces: `{homepage_only_trace_count}`",
            f"- Unresolved Google News traces: `{unresolved_google_news_count}`",
            f"- Outside backfill date window: `{outside_window_count}`",
            f"- In-window candidates: `{in_window_candidate_count}`",
            f"- Public-eligible candidates: `{public_eligible_candidate_count}`",
            f"- Manually reviewable candidates: `{manual_reviewable_count}`",
            f"- Qualified pressure signals: `{qualified_pressure_signals}`",
            f"- Context-only records: `{context_only_count}`",
            f"- Duplicate count: `{duplicate_count}`",
            f"- Manual fallback records: `{manual_fallback_count}`",
            f"- Duplicate records preferring direct-source candidates: `{duplicate_preferred_direct_count}`",
            f"- Configured lanes: {', '.join(configured_lanes) if configured_lanes else 'none'}",
            f"- Executed lanes: {', '.join(executed_lanes) if executed_lanes else 'none'}",
            f"- Skipped lanes: {', '.join(skipped_lanes) if skipped_lanes else 'none'}",
            "",
            "## Candidates by query family",
            "",
            _markdown_table(query_family_counts, "query_family"),
            "",
            "## Candidates by discovery lane",
            "",
            _markdown_table(lane_counts, "discovery_lane"),
            "",
            "## Candidates by discovery channel",
            "",
            _markdown_table(discovery_channel_counts, "discovery_channel"),
            "",
            "## Candidates by direct source",
            "",
            _markdown_table(direct_source_counts, "direct_source_name"),
            "",
            "## Candidates by direct source and lane",
            "",
            _markdown_table(direct_source_lane_counts, "direct_source_lane"),
            "",
            "## Candidates by state or territory",
            "",
            _markdown_table(state_counts, "state_or_territory"),
            "",
            "## Candidates by metro",
            "",
            _markdown_table(metro_counts, "metro"),
            "",
        ]
        audit_md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return audit_summary


def _markdown_table(counter: Counter[str], column_name: str) -> str:
    if not counter:
        return "_No records._"
    lines = [f"| {column_name} | count |", "| --- | ---: |"]
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


def read_food_line_discovery_expansion_audit(root: Path, edition_date: str) -> dict[str, Any]:
    date_text = validate_date(edition_date)
    path = root / "output" / "review" / DISPATCH_SLUG / date_text / DISCOVERY_AUDIT_JSON_FILE
    if not path.exists():
        return {}
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Food Line discovery expansion layer.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--manual-fallback-file", help="Optional JSON list of manual fallback records.")
    parser.add_argument("--edition-mode", default="current_update", choices=("current_update", "no_current_update"))
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--query-lookback-days", type=int, default=1)
    parser.add_argument("--query-lookahead-days", type=int, default=1)
    parser.add_argument("--public-claim-lookback-days", type=int, default=0)
    parser.add_argument("--public-claim-lookahead-days", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    manual_path = Path(args.manual_fallback_file).resolve() if args.manual_fallback_file else None
    result = run_food_line_discovery_expansion(
        root,
        args.date,
        manual_fallback_path=manual_path,
        edition_mode=args.edition_mode,
        max_results_per_query=args.max_results_per_query,
        max_queries=args.max_queries,
        query_lookback_days=args.query_lookback_days,
        query_lookahead_days=args.query_lookahead_days,
        public_claim_lookback_days=args.public_claim_lookback_days,
        public_claim_lookahead_days=args.public_claim_lookahead_days,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
