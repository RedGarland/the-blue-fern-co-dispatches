from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
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
    if not host or host.endswith(SOCIAL_DOMAINS) or host.endswith((GOOGLE_NEWS_DOMAIN, "google.com")):
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").strip("/")
    return bool(path)


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
    return _normalize_url(discovered_url)


def _discovery_traceability_status(*, source_url: str, original_source_url: str, discovered_url: str, fetch_status: str) -> str:
    if not any((source_url, original_source_url, discovered_url)):
        return "missing_url"
    if any(_host(url).endswith(SOCIAL_DOMAINS) for url in (source_url, original_source_url, discovered_url) if url):
        return "social_only"
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
        path = Path(__file__).resolve().parents[2] / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_CONFIG_FILE
    if not path.exists():
        return {
            "query_families": QUERY_FAMILY_DEFINITIONS,
            "metros": DEFAULT_METROS,
        }
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    query_families = [row for row in payload.get("query_families") or [] if isinstance(row, dict)]
    metros = [row for row in payload.get("metros") or [] if isinstance(row, dict)]
    return {
        "query_families": query_families or QUERY_FAMILY_DEFINITIONS,
        "metros": metros or DEFAULT_METROS,
        "search": payload.get("search") if isinstance(payload.get("search"), dict) else {},
    }


def _parse_date_range(edition_date: str, *, lookback_days: int = 1, lookahead_days: int = 1) -> tuple[str, str]:
    day = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    return (day - timedelta(days=max(0, int(lookback_days)))).isoformat(), (day + timedelta(days=max(0, int(lookahead_days)))).isoformat()


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
    for family in config["query_families"]:
        family_name = _nonempty(family.get("query_family"))
        geographic_scope = _nonempty(family.get("geographic_scope"))
        source_family = _nonempty(family.get("source_family") or "local_news")
        templates = [str(item).strip() for item in family.get("templates") or [] if str(item).strip()]
        if family_name in {"state_territory", "metro"}:
            continue
        for template in templates:
            query_text = template.format(after=after, before=before)
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
            query_text = template.format(geo=state_name, after=after, before=before)
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
            query_text = template.format(geo=metro_name, after=after, before=before)
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


def _fetch_url(fetcher: Any, url: str) -> tuple[bytes, str]:
    try:
        return fetcher(url, timeout=15), ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


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
    candidate["source_url"] = _normalize_url(_nonempty(candidate.get("source_url") or final_trace or original_source_url or discovered))
    candidate["final_trace_url"] = final_trace or canonical or discovered
    candidate["manual_review_required"] = bool(candidate.get("manual_review_required", True))
    candidate["pressure_terms_detected"] = list(candidate.get("pressure_terms_detected") or [])
    candidate["location_terms_detected"] = list(candidate.get("location_terms_detected") or [])
    candidate["affected_groups"] = list(candidate.get("affected_groups") or [])
    candidate["review_status"] = _nonempty(candidate.get("review_status") or "needs_review")
    candidate["classification_status"] = _nonempty(candidate.get("classification_status") or "needs_review")
    candidate["exclusion_reason"] = _nonempty(candidate.get("exclusion_reason"))
    candidate["fetch_status"] = _nonempty(candidate.get("fetch_status") or "unfetched")
    candidate["fetch_error"] = _nonempty(candidate.get("fetch_error"))
    candidate["duplicate_of"] = _nonempty(candidate.get("duplicate_of"))
    candidate["public_claim_blockers"] = list(candidate.get("public_claim_blockers") or [])
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
    query_plan = build_food_line_discovery_query_plan(
        root,
        date_text,
        lookback_days=query_lookback_days,
        lookahead_days=query_lookahead_days,
    )
    if max_queries is not None and max_queries >= 0:
        query_plan = query_plan[:max_queries]
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
    seen_trace_keys: dict[str, str] = {}

    for query_row in query_plan:
        query_text = _nonempty(query_row.get("query_text"))
        query_url = _query_url(query_text)
        payload, query_error = _fetch_url(fetch, query_url)
        result_row = dict(query_row)
        result_row["query_url"] = query_url
        result_row["query_error"] = query_error
        result_row["result_count"] = 0
        if query_error or not payload:
            query_rows.append(result_row)
            continue
        try:
            rss_items = _parse_google_news_rss(payload)
        except Exception as exc:  # noqa: BLE001
            result_row["query_error"] = f"{type(exc).__name__}: {exc}"
            query_rows.append(result_row)
            continue
        result_row["result_count"] = len(rss_items)
        query_rows.append(result_row)
        for item in rss_items[:max_results_per_query]:
            discovered_url = _normalize_url(_nonempty(item.get("link")))
            google_news_url = discovered_url if GOOGLE_NEWS_DOMAIN in discovered_url else ""
            publisher_url = _normalize_url(_nonempty(item.get("source_url")))
            candidate_source_url = publisher_url or discovered_url
            final_trace_url = candidate_source_url or discovered_url
            canonical = _normalize_url(final_trace_url)
            fetch_status = "unfetched"
            fetch_error = ""
            evidence_text = _nonempty(item.get("title")) + " " + _nonempty(item.get("description"))
            if final_trace_url:
                payload2, fetch_error = _fetch_url(fetch, final_trace_url)
                if fetch_error or not payload2:
                    fetch_status = _classify_fetch_error(fetch_error or "empty response")
                else:
                    fetch_status = "ok"
                    fetchable_count += 1
                    canonical_from_page = _extract_canonical_url(payload2)
                    if _is_article_specific_url(canonical_from_page):
                        canonical = canonical_from_page
                        if not _is_article_specific_url(final_trace_url):
                            final_trace_url = canonical_from_page
                    elif canonical_from_page and not canonical:
                        canonical = canonical_from_page
                    evidence_text = " ".join(
                        part
                        for part in (
                            _nonempty(item.get("title")),
                            _nonempty(item.get("description")),
                            _nonempty(item.get("source_name")),
                            canonical_from_page,
                        )
                        if part
                    )
            classification_status, exclusion_reason = _classification_from_terms(
                evidence_text,
                fetch_status=fetch_status,
                duplicate=False,
            )
            lane = _lane_from_query(_nonempty(query_row.get("query_family")), discovered_url, publisher_url, _nonempty(item.get("source_name")))
            pressure_hits = _detect_terms(evidence_text.lower(), PRESSURE_TERMS)
            location_hits = []
            for term in [query_row.get("state_or_territory"), query_row.get("metro")]:
                term_text = _nonempty(term)
                if term_text and term_text.lower() in evidence_text.lower() and term_text not in location_hits:
                    location_hits.append(term_text)
            source_published_date = _extract_source_date(_nonempty(item.get("pubDate")))
            source_url = _choose_trace_url(final_trace_url or publisher_url, canonical, discovered_url)
            traceability_status = _discovery_traceability_status(
                source_url=source_url,
                original_source_url=final_trace_url or publisher_url,
                discovered_url=discovered_url,
                fetch_status=fetch_status,
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
                "discovery_channel": _nonempty(query_row.get("discovery_channel") or "google_news_rss"),
                "discovered_title": _nonempty(item.get("title")),
                "discovered_publisher": _nonempty(item.get("source_name") or item.get("source_url")),
                "discovered_url": discovered_url,
                "canonical_url": canonical,
                "source_url": source_url,
                "original_source_url": final_trace_url or publisher_url or "",
                "google_news_url": google_news_url,
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
            }
            trace_key = canonical or final_trace_url or discovered_url
            if trace_key and trace_key in seen_trace_keys:
                row["duplicate_of"] = seen_trace_keys[trace_key]
                row["classification_status"] = "duplicate"
                row["exclusion_reason"] = f"duplicate of {row['duplicate_of']}"
                row["public_claim_eligible"] = False
                row["public_claim_blockers"] = sorted(set([*list(row.get("public_claim_blockers") or []), "duplicate"]))
                duplicate_count += 1
            else:
                seen_trace_keys[trace_key] = row["candidate_id"]
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
            manual_reviewable_count += 1 if row["manual_review_required"] else 0
            query_family_counts[row["query_family"]] += 1
            if row["state_or_territory"]:
                state_counts[row["state_or_territory"]] += 1
            if row["metro"]:
                metro_counts[row["metro"]] += 1
            candidates.append(_normalize_candidate_row(row))

    manual_fallback_count = _append_manual_fallbacks(candidates, manual_fallback_records, date_text)
    if manual_fallback_path and manual_fallback_path.exists():
        fallback_payload = _read_json(manual_fallback_path)
        if isinstance(fallback_payload, list):
            manual_fallback_count += _append_manual_fallbacks(candidates, [row for row in fallback_payload if isinstance(row, dict)], date_text)
        else:
            raise ValueError(f"{manual_fallback_path} must contain a JSON list")

    for row in candidates:
        trace_key = row.get("canonical_url") or row.get("final_trace_url") or row.get("discovered_url")
        if trace_key and trace_key in seen_trace_keys and seen_trace_keys[trace_key] != row["candidate_id"]:
            row["duplicate_of"] = seen_trace_keys[trace_key]
            row["classification_status"] = "duplicate"
            row["exclusion_reason"] = f"duplicate of {row['duplicate_of']}"
            row["public_claim_eligible"] = False
            row["public_claim_blockers"] = sorted(set([*list(row.get("public_claim_blockers") or []), "duplicate"]))
        elif trace_key:
            seen_trace_keys[trace_key] = row["candidate_id"]
        if row.get("classification_status") == "manual_fallback":
            row["manual_review_required"] = False
            row["candidate_review_status"] = "needs_review"
        row["traceability_status"] = _discovery_traceability_status(
            source_url=_nonempty(row.get("source_url") or row.get("final_trace_url")),
            original_source_url=_nonempty(row.get("original_source_url") or row.get("final_trace_url")),
            discovered_url=_nonempty(row.get("discovered_url")),
            fetch_status=_nonempty(row.get("fetch_status")),
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
    candidate_count = len(candidates)
    query_family_counts = Counter(_nonempty(row.get("query_family")) for row in candidates if _nonempty(row.get("query_family")))
    lane_counts = Counter(_nonempty(row.get("discovery_lane")) for row in candidates if _nonempty(row.get("discovery_lane")))
    source_type_counts = Counter(_nonempty(row.get("discovery_source_type")) for row in candidates if _nonempty(row.get("discovery_source_type")))
    state_counts = Counter(_nonempty(row.get("state_or_territory")) for row in candidates if _nonempty(row.get("state_or_territory")))
    metro_counts = Counter(_nonempty(row.get("metro")) for row in candidates if _nonempty(row.get("metro")))
    duplicate_count = sum(1 for row in candidates if _nonempty(row.get("duplicate_of")))
    blocked_fetch_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) not in {"ok", "manual_fallback"})
    homepage_only_trace_count = sum(1 for row in candidates if "publisher_homepage_trace_only" in list(row.get("public_claim_blockers") or []))
    outside_window_count = sum(1 for row in candidates if "outside_backfill_date_window" in list(row.get("public_claim_blockers") or []))
    fetchable_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) == "ok")
    manual_reviewable_count = sum(1 for row in candidates if bool(row.get("manual_review_required")))
    qualified_pressure_signals = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "qualified_pressure_signal")
    context_only_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "context_only")
    manual_fallback_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "manual_fallback")
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
        "homepage_only_trace_count": homepage_only_trace_count,
        "outside_backfill_date_window_count": outside_window_count,
        "manually_reviewable_count": manual_reviewable_count,
        "qualified_pressure_signals": qualified_pressure_signals,
        "context_only_count": context_only_count,
        "manual_fallback_count": manual_fallback_count,
        "candidates_by_query_family": dict(sorted(query_family_counts.items())),
        "candidates_by_discovery_lane": dict(sorted(lane_counts.items())),
        "candidates_by_discovery_source_type": dict(sorted(source_type_counts.items())),
        "candidates_by_state_or_territory": dict(sorted(state_counts.items())),
        "candidates_by_metro": dict(sorted(metro_counts.items())),
        "query_rows": query_rows,
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
            f"- Homepage-only traces: `{homepage_only_trace_count}`",
            f"- Outside backfill date window: `{outside_window_count}`",
            f"- Manually reviewable candidates: `{manual_reviewable_count}`",
            f"- Qualified pressure signals: `{qualified_pressure_signals}`",
            f"- Context-only records: `{context_only_count}`",
            f"- Duplicate count: `{duplicate_count}`",
            f"- Manual fallback records: `{manual_fallback_count}`",
            "",
            "## Candidates by query family",
            "",
            _markdown_table(query_family_counts, "query_family"),
            "",
            "## Candidates by discovery lane",
            "",
            _markdown_table(lane_counts, "discovery_lane"),
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
