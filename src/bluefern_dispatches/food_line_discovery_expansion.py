from __future__ import annotations

import argparse
import base64
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

from bluefern_dispatches.food_line_sources import (
    FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES,
    canonical_url,
    evaluate_food_line_pressure,
    resolve_food_line_fetcher,
    validate_date,
)
from bluefern_dispatches.food_line_agent_export import export_food_line_agent_run

DISPATCH_SLUG = "food-line"
DISCOVERY_DIR_NAME = "discovery"
DISCOVERY_CANDIDATES_FILE = "discovery_candidates.json"
DISCOVERY_AUDIT_JSON_FILE = "discovery_audit.json"
DISCOVERY_AUDIT_MD_FILE = "discovery_audit.md"
DISCOVERY_CONFIG_FILE = "discovery_expansion_config.json"
GOOGLE_NEWS_DOMAIN = "news.google.com"
GOOGLE_NEWS_RPC_CONTEXT = [
    ["en-US", "US", ["FINANCE_TOP_INDICES", "GENESIS_PUBLISHER_SECTION", "WEB_TEST_1_0_0"], None, None, 1, 1, "US:en", None, None, None, None, None, None, None, False, 5],
    "en-US",
    "US",
    True,
    [3, 5, 9, 19],
    1,
    True,
    "936584890",
    False,
    False,
    None,
    False,
]
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
GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT = 25
_ACTIVE_REQUEST_TIMEOUT_SECONDS = 15
COMMON_PUBLISHER_SUBDOMAINS = ("www.", "m.", "amp.")
KNOWN_PUBLISHER_CANONICAL_DOMAINS = {
    "benefitspro": {"benefitspro.com"},
    "federal reserve bank of new york": {"newyorkfed.org"},
    "indyweek": {"indyweek.com"},
    "new york fed": {"newyorkfed.org"},
    "wowt": {"wowt.com"},
}
STATIC_PATH_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".mp4", ".mp3", ".pdf", ".js", ".css", ".woff", ".woff2")
HOMEPAGE_LANDING_SEGMENTS = {
    "home",
    "homepage",
    "index",
    "welcome",
    "default",
}
LISTING_PATH_SEGMENTS = {
    "blog",
    "blogs",
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
ARCHIVE_ACTION_SEGMENTS = {
    "action",
    "actions",
    "take-action",
    "take_action",
    "advocacy",
    "advocate",
    "donate",
    "donation",
    "donations",
}
ARCHIVE_RESOURCE_SEGMENTS = {
    "program",
    "programs",
    "resource",
    "resources",
    "toolkit",
    "toolkits",
    "newsletter",
    "newsletters",
    "signup",
    "sign-up",
    "subscribe",
    "subscription",
}
ARCHIVE_NAVIGATION_TEXT = {
    "skip to content",
    "skip to main content",
    "menu",
    "main menu",
    "navigation",
    "header",
    "footer",
    "home",
    "about",
    "contact",
    "next",
    "previous",
    "older posts",
    "newer posts",
}

GENERIC_TITLE_EXACT_NORMALIZED = {
    "skip to content",
    "skip to main content",
    "donate",
    "our blog",
    "blog",
    "news",
    "all songs considered",
    "airtalk",
    "apply for calfresh",
    "home",
    "menu",
    "search",
    "press releases",
    "archive",
    "archives",
    "updates",
    "resources",
    "programs",
    "food bank",
    "hunger blog",
}
GENERIC_TITLE_SINGLE_WORDS = {
    "about",
    "archive",
    "archives",
    "blog",
    "calendar",
    "donate",
    "feed",
    "feeds",
    "home",
    "latest",
    "menu",
    "news",
    "programs",
    "resources",
    "search",
    "updates",
}
PUBLIC_PROSE_REQUIRED_FIELDS = (
    "pressure_summary",
    "pressure_type",
    "affected_groups",
    "evidence_level",
    "freshness_role",
    "source_role",
)
SAFE_AFFECTED_GROUPS_FALLBACK = "Not clearly isolated by source"
DISCOVERY_PRESSURE_TYPE_RULES: list[tuple[str, tuple[tuple[str, ...], ...]]] = [
    ("SNAP policy pressure", (("snap",), ("proposal", "rule", "eligibility", "broad-based categorical eligibility", "bbce"))),
    ("school meals pressure", (("school meals", "summer meals"), ("gap", "cut", "end", "loss", "access", "site", "sites", "hunger"))),
    (
        "school meal price pressure",
        (
            ("school meal", "school meals", "school lunch", "school breakfast", "meal price", "meal prices"),
            ("price increase", "price increases", "higher price", "higher prices", "cost", "costs", "affordability"),
        ),
    ),
    ("food bank demand pressure", (("food bank", "food pantry", "pantry"), ("demand", "surge", "rising", "rise", "strain", "need", "waitlist", "shortage"))),
    ("benefit access pressure", (("snap", "ebt", "wic", "benefit", "benefits"), ("access", "eligibility", "delay", "disruption", "application", "renewal", "recertification", "backlog"))),
    ("food affordability pressure", (("grocery prices", "food costs", "food prices", "inflation", "rent and groceries"), tuple())),
    (
        "household food insecurity pressure",
        (
            ("food insecurity", "food hardship", "food insufficiency", "food sufficiency"),
            ("rise", "rising", "increase", "increased", "higher", "worsening", "worse"),
        ),
    ),
    ("emergency food access pressure", (("emergency food assistance", "food distribution", "meal site", "meal sites"), ("access", "availability", "closure", "closed", "hours", "distance"))),
]
DISCOVERY_POLICY_SOURCE_TERMS = (
    "snap",
    "broad-based categorical eligibility",
    "bbce",
    "eligibility",
    "eligibility rules",
    "wic",
    "school meals",
    "summer meals",
    "usda proposal",
    "usda rule",
    "benefit access",
)
RESOURCE_CONTEXT_TERMS = (
    "find food",
    "find a food bank",
    "find food near you",
    "need help",
    "help with snap",
    "apply for",
    "application",
    "locator",
    "programs",
    "resources",
    "resource library",
    "meal sites",
)

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
    "household food insecurity",
    "rising food insecurity",
    "food insecurity is rising",
    "food insecurity increased",
    "food bank",
    "food pantry",
    "hunger relief",
    "demand",
    "record demand",
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
    "meal price increase",
    "meal prices",
    "school lunch price",
    "school lunch prices",
    "school meal price",
    "school meal prices",
    "summer meals",
    "WIC",
    "Meals on Wheels",
    "TEFAP",
    "grocery prices",
    "inflation",
    "food costs",
    "medical debt",
    "health care bills",
    "health-care bills",
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
            '"household food insecurity"',
            '"rising food insecurity"',
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
            '("summer food programs" OR "summer meals") "record demand"',
            '("food program" OR "food programs") "record demand"',
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
            '("school meal" OR "school meals" OR "school lunch") ("price increase" OR "higher prices" OR affordability)',
            '("school board" OR district) ("meal price increase" OR "school lunch price")',
            '("meal sites" OR "summer meals" OR "food distribution sites") (families OR children OR "emergency food assistance")',
            '(WIC OR TEFAP OR "Meals on Wheels") (cuts OR delays OR waitlist)',
            '("health care bills" OR "health-care bills" OR "medical debt") "food insecurity"',
            '("New York Fed" OR "Federal Reserve Bank of New York") "food insecurity"',
        ],
    },
    {
        "query_family": "cost_pressure",
        "geographic_scope": "national",
        "source_family": "local_news",
        "templates": [
            '("grocery prices" OR "food costs") ("food pantry" OR families)',
            '("rent and groceries" OR "utility bills and groceries") hunger',
            '("health care bills" OR "health-care bills" OR "medical debt") "food insecurity"',
        ],
    },
    {
        "query_family": "state_territory",
        "geographic_scope": "state_or_territory",
        "source_family": "local_news",
        "templates": [
            '"food bank demand" {geo} after:{after} before:{before}',
            '"food pantry demand" {geo} after:{after} before:{before}',
            '"record demand" ("food programs" OR "summer meals") {geo} after:{after} before:{before}',
            '"food banks" {geo} after:{after} before:{before}',
            '"food pantries" {geo} after:{after} before:{before}',
            '"food stamps" {geo} after:{after} before:{before}',
            '"SNAP cuts" {geo} after:{after} before:{before}',
            '"summer meals" {geo} families after:{after} before:{before}',
            '("meal price increase" OR "school lunch price") {geo} after:{after} before:{before}',
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
            '"record demand" ("food programs" OR "summer meals") {geo} after:{after} before:{before}',
            '"food banks" {geo} after:{after} before:{before}',
            '"food pantries" {geo} after:{after} before:{before}',
            '"food stamps" {geo} after:{after} before:{before}',
            '"SNAP cuts" {geo} after:{after} before:{before}',
            '"summer meals" {geo} families after:{after} before:{before}',
            '("meal price increase" OR "school lunch price") {geo} after:{after} before:{before}',
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


def set_food_line_request_timeout(seconds: int) -> None:
    """Set the request bound for one isolated discovery worker process."""
    global _ACTIVE_REQUEST_TIMEOUT_SECONDS
    _ACTIVE_REQUEST_TIMEOUT_SECONDS = max(1, int(seconds))


def _request_timeout_seconds() -> int:
    return max(1, int(_ACTIVE_REQUEST_TIMEOUT_SECONDS))


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _normalized_title_key(value: str) -> str:
    text = _nonempty(html.unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\W_]+|[\W_]+$", "", text)
    return text.lower()


def _clean_title_text(value: str) -> str:
    text = _nonempty(html.unescape(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-–—|:;,.")


def _dedupe_texts(values: list[str], *, limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_title_text(value)
        key = _normalized_title_key(cleaned)
        if not cleaned or not key or key in seen:
            continue
        out.append(cleaned[:240])
        seen.add(key)
        if len(out) >= limit:
            break
    return out


GENERIC_CHROME_TITLES_NORMALIZED = {
    _normalized_title_key(value) for value in FOOD_LINE_PUBLIC_EVIDENCE_CHROME_PHRASES
}


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


def _strip_common_publisher_subdomain(host: str) -> str:
    value = _nonempty(host).lower().strip(".")
    for prefix in COMMON_PUBLISHER_SUBDOMAINS:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _normalize_publisher_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", _nonempty(value).lower())).strip()


def _known_publisher_domains(publisher_name: str) -> set[str]:
    return set(KNOWN_PUBLISHER_CANONICAL_DOMAINS.get(_normalize_publisher_key(publisher_name), set()))


def _extract_source_date(value: str) -> str:
    text = _nonempty(value)
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    for candidate_text in (
        text,
        re.sub(r"^[A-Za-z]{3},\s*", "", text),
        re.sub(r"\s+[A-Z]{2,5}$", "", re.sub(r"^[A-Za-z]{3},\s*", "", text)),
    ):
        for fmt in ("%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%B %d %Y"):
            try:
                return datetime.strptime(candidate_text.strip(), fmt).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1)
    return ""


def _extract_url_date(value: str) -> str:
    text = _nonempty(value)
    if not text:
        return ""
    patterns = (
        r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])(?:/|$)",
        r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b",
        r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return ""


def _extract_page_body_date(payload: bytes) -> str:
    text = _page_text(payload, limit=6000)
    candidates = [
        _extract_source_date(match.group(0))
        for match in re.finditer(
            r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
            text,
            re.IGNORECASE,
        )
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return _extract_source_date(text)


def _extract_page_published_date_info(payload: bytes, source_url: str = "") -> tuple[str, str]:
    text = payload.decode("utf-8", errors="replace")
    patterns = (
        (r'<meta\b[^>]*property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)["\']', "page_meta_date"),
        (r'<meta\b[^>]*name=["\']article:published_time["\'][^>]*content=["\']([^"\']+)["\']', "page_meta_date"),
        (r'<meta\b[^>]*property=["\']og:published_time["\'][^>]*content=["\']([^"\']+)["\']', "page_meta_date"),
        (r'<meta\b[^>]*name=["\']pubdate["\'][^>]*content=["\']([^"\']+)["\']', "page_meta_date"),
        (r'<time\b[^>]*datetime=["\']([^"\']+)["\']', "page_meta_date"),
        (r'"datePublished"\s*:\s*"([^"]+)"', "page_meta_date"),
        (r'"dateModified"\s*:\s*"([^"]+)"', "page_meta_date"),
    )
    for pattern, basis in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = _extract_source_date(_nonempty(match.group(1)))
        if candidate:
            return candidate, basis
    body_date = _extract_page_body_date(payload)
    if body_date:
        return body_date, "page_body_date"
    url_date = _extract_url_date(source_url)
    if url_date:
        return url_date, "url_date"
    return "", "missing"


def _date_distance_days(target_date: str, source_published_date: str) -> int | None:
    if not _nonempty(target_date) or not _nonempty(source_published_date):
        return None
    target = datetime.strptime(validate_date(target_date), "%Y-%m-%d").date()
    published = datetime.strptime(validate_date(source_published_date), "%Y-%m-%d").date()
    return abs((published - target).days)


def _date_match_status(
    target_date: str,
    source_published_date: str,
    *,
    lookback_days: int,
    lookahead_days: int,
) -> str:
    if not _nonempty(source_published_date):
        return "missing_date"
    if validate_date(target_date) == validate_date(source_published_date):
        return "exact_date"
    if _published_date_in_window(
        target_date,
        source_published_date,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    ):
        return "within_query_window"
    return "outside_query_window"


def _date_match_sort_key(date_match_status: str, date_distance_days: int | None, date_basis: str) -> tuple[int, int, int]:
    status_rank = {
        "exact_date": 0,
        "within_query_window": 1,
        "outside_query_window": 2,
        "missing_date": 3,
    }.get(date_match_status, 4)
    basis_rank = _date_basis_rank(date_basis)
    return status_rank, date_distance_days if date_distance_days is not None else 99999, basis_rank


def _date_basis_rank(date_basis: str) -> int:
    return {
        "feed_published": 0,
        "feed_updated": 1,
        "page_meta_date": 2,
        "page_body_date": 3,
        "url_date": 4,
        "missing": 5,
    }.get(date_basis, 6)


def _historical_archive_template_vars(edition_date: str) -> dict[str, str]:
    target = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    return {
        "yyyy": f"{target.year:04d}",
        "yy": f"{target.year % 100:02d}",
        "mm": f"{target.month:02d}",
        "m": str(target.month),
        "dd": f"{target.day:02d}",
        "d": str(target.day),
        "month_name_lower": target.strftime("%B").lower(),
        "month_name_title": target.strftime("%B"),
        "month_abbrev_lower": target.strftime("%b").lower(),
        "month_abbrev_title": target.strftime("%b"),
        "iso_date": target.isoformat(),
        "yyyymm": f"{target.year:04d}{target.month:02d}",
        "yyyymmdd": f"{target.year:04d}{target.month:02d}{target.day:02d}",
    }


def _normalize_historical_archive_templates(value: Any) -> list[dict[str, str]]:
    raw_templates: list[Any] = []
    if isinstance(value, str) and _nonempty(value):
        raw_templates = [value]
    elif isinstance(value, list):
        raw_templates = list(value)
    normalized: list[dict[str, str]] = []
    for index, raw_template in enumerate(raw_templates, start=1):
        if isinstance(raw_template, str):
            url_template = _nonempty(raw_template)
            template_name = f"template_{index}"
            granularity = ""
        elif isinstance(raw_template, dict):
            url_template = _nonempty(
                raw_template.get("url_template")
                or raw_template.get("template")
                or raw_template.get("url")
            )
            template_name = _nonempty(raw_template.get("template_name") or raw_template.get("name")) or f"template_{index}"
            granularity = _nonempty(raw_template.get("archive_granularity") or raw_template.get("granularity"))
        else:
            continue
        if not url_template:
            continue
        normalized.append(
            {
                "url_template": url_template,
                "template_name": template_name,
                "archive_granularity": granularity,
            }
        )
    return normalized


def _render_historical_archive_urls(
    edition_date: str,
    templates: list[dict[str, str]],
) -> list[dict[str, str]]:
    variables = _historical_archive_template_vars(edition_date)
    rendered: list[dict[str, str]] = []
    seen: set[str] = set()
    for template in templates:
        url_template = _nonempty(template.get("url_template"))
        if not url_template:
            continue
        try:
            rendered_url = _normalize_url(url_template.format_map(variables))
        except Exception:  # noqa: BLE001
            continue
        if not rendered_url or rendered_url in seen:
            continue
        rendered.append(
            {
                "archive_url": rendered_url,
                "archive_template_used": _nonempty(template.get("template_name")),
                "archive_granularity": _nonempty(template.get("archive_granularity")),
                "archive_target_date": validate_date(edition_date),
            }
        )
        seen.add(rendered_url)
    return rendered


def _render_archive_page_url(template: str, *, edition_date: str, page: int) -> str:
    if not _nonempty(template):
        return ""
    variables = _historical_archive_template_vars(edition_date)
    variables["page"] = str(int(page))
    try:
        return _normalize_url(template.format_map(variables))
    except Exception:  # noqa: BLE001
        return ""


def _is_homepage_only_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").strip("/")
    return parsed.scheme in {"http", "https"} and not path and not parsed.query


def _homepage_or_landing_url_reason(url: str) -> str:
    value = _normalize_url(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = [segment.lower().split(".", 1)[0] for segment in (parsed.path or "").strip("/").split("/") if segment]
    if not path and not parsed.query:
        return "homepage_root"
    if not path:
        return ""
    locale_pattern = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
    first_segment = path[0]
    last_segment = path[-1]
    if first_segment in HOMEPAGE_LANDING_SEGMENTS:
        return f"landing_segment:{first_segment}"
    if last_segment in HOMEPAGE_LANDING_SEGMENTS and len(path) <= 2:
        return f"landing_segment:{last_segment}"
    if len(path) == 1:
        if first_segment in LISTING_PATH_SEGMENTS:
            return f"listing_root:{first_segment}"
        if first_segment in ARCHIVE_RESOURCE_SEGMENTS:
            return f"resource_landing:{first_segment}"
        if first_segment in ARCHIVE_ACTION_SEGMENTS:
            return f"action_landing:{first_segment}"
    if len(path) == 2 and locale_pattern.fullmatch(first_segment):
        if last_segment in LISTING_PATH_SEGMENTS:
            return f"listing_root:{last_segment}"
        if last_segment in ARCHIVE_RESOURCE_SEGMENTS:
            return f"resource_landing:{last_segment}"
        if last_segment in ARCHIVE_ACTION_SEGMENTS:
            return f"action_landing:{last_segment}"
        if last_segment in HOMEPAGE_LANDING_SEGMENTS:
            return f"landing_segment:{last_segment}"
    if len(path) == 2 and first_segment in {"category", "categories", "tag", "tags", "author"}:
        return "taxonomy_listing"
    return ""


def _is_homepage_or_landing_url(url: str) -> bool:
    return bool(_homepage_or_landing_url_reason(url))


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
    if _is_homepage_or_landing_url(value):
        return False
    return bool(path)


def _path_segments(url: str) -> list[str]:
    parsed = urllib.parse.urlsplit(_normalize_url(url))
    return [segment for segment in (parsed.path or "").strip("/").split("/") if segment]


def _path_words(value: str) -> list[str]:
    return [word for word in re.split(r"[^a-z0-9]+", value.lower()) if word]


def _cap_text(value: str, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", _nonempty(value)).strip()
    return text[:limit]


def _candidate_source_family(row: dict[str, Any]) -> str:
    family = _nonempty(row.get("source_family"))
    if family:
        return family
    lane = _nonempty(row.get("discovery_lane"))
    lane_to_family = {
        "public_radio": "public_radio",
        "food_bank_provider": "food_bank_provider",
        "feeding_america_affiliate": "food_bank_provider",
        "school_meals_child_nutrition": "nonprofit_news",
        "nonprofit_report": "nonprofit_news",
        "news_article": "local_news",
    }
    return lane_to_family.get(lane, "local_news")


def _candidate_location_name(row: dict[str, Any]) -> str:
    for key in ("metro", "state_or_territory", "state_hint", "discovered_publisher", "direct_source_name"):
        value = _nonempty(row.get(key))
        if value:
            return value
    return "United States"


def _infer_supported_state_or_territory(row: dict[str, Any]) -> tuple[str, str]:
    existing_name = _nonempty(row.get("state_or_territory"))
    existing_abbrev = _nonempty(row.get("state_abbrev") or row.get("state_hint"))
    if existing_name or existing_abbrev:
        return existing_name, existing_abbrev
    texts = [
        _nonempty(row.get("selected_title")),
        _nonempty(row.get("discovered_title")),
        _nonempty(row.get("summary_or_snippet")),
        _nonempty(row.get("evidence_text")),
    ]
    combined = " ".join(part for part in texts if part)
    if not combined:
        return "", ""
    matches: list[tuple[str, str]] = []
    lowered = combined.lower()
    for state_name, abbrev in STATE_TERRITORIES:
        if re.search(rf"\b{re.escape(state_name.lower())}\b", lowered):
            matches.append((state_name, abbrev))
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    return "", ""


def _candidate_public_evidence_text(row: dict[str, Any]) -> str:
    for key in ("evidence_text", "pressure_evidence_summary", "manually_reviewed_summary", "summary_or_snippet"):
        value = _cap_text(_nonempty(row.get(key)), limit=1200)
        if value:
            return value
    return ""


def _candidate_source_text_fields(row: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("selected_title", "discovered_title", "summary_or_snippet", "pressure_evidence_summary", "manually_reviewed_summary", "evidence_text"):
        value = _cap_text(_nonempty(row.get(key)), limit=1200)
        if value:
            fields[key] = value
    return fields


def _publisher_attribution_name(row: dict[str, Any]) -> str:
    publisher = _nonempty(row.get("discovered_publisher") or row.get("direct_source_name") or row.get("publisher"))
    if publisher.endswith(" News") and len(publisher) > 5:
        return publisher[:-5].strip()
    return publisher or "The source"


def _derive_pressure_type_from_source_fields(source_fields: dict[str, str]) -> tuple[str, str, list[str]]:
    ordered_fields = ["selected_title", "discovered_title", "summary_or_snippet", "pressure_evidence_summary", "manually_reviewed_summary", "evidence_text"]
    for pressure_type, groups in DISCOVERY_PRESSURE_TYPE_RULES:
        group_matches: list[str] = []
        for group in groups:
            if not group:
                continue
            matched_field = ""
            for field_name in ordered_fields:
                text = source_fields.get(field_name, "")
                lowered = text.lower()
                if any(needle in lowered for needle in group):
                    matched_field = field_name
                    break
            if not matched_field:
                group_matches = []
                break
            group_matches.append(matched_field)
        if group_matches and len(group_matches) == len(groups):
            used_fields = list(dict.fromkeys(group_matches))
            if len(used_fields) == 1:
                return pressure_type, f"derived_from_{used_fields[0]}", used_fields
            return pressure_type, "derived_from_multi_field_source_text", used_fields
    return "", "insufficient_source_support", []


def _derive_pressure_summary_from_source_fields(
    row: dict[str, Any],
    *,
    pressure_type: str,
    source_fields: dict[str, str],
) -> tuple[str, str, list[str]]:
    existing = _nonempty(row.get("pressure_summary") or row.get("pressure_evidence_summary"))
    if existing:
        return existing, "existing", ["pressure_evidence_summary" if _nonempty(row.get("pressure_evidence_summary")) else "pressure_summary"]
    title = _strip_site_suffix(_nonempty(source_fields.get("selected_title") or source_fields.get("discovered_title")))
    summary = _nonempty(source_fields.get("summary_or_snippet") or source_fields.get("pressure_evidence_summary") or source_fields.get("manually_reviewed_summary"))
    evidence = _nonempty(source_fields.get("evidence_text"))
    combined = " ".join(part for part in (title, summary, evidence) if part).lower()
    source_support = " ".join(part for part in (summary, evidence) if part).lower()
    publisher = _publisher_attribution_name(row)

    if pressure_type == "SNAP policy pressure":
        if all(token in combined for token in ("snap", "broad-based categorical eligibility")) and "would increase hunger" in combined:
            return (
                f"{publisher} warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
                "derived_from_title_and_source_text",
                ["selected_title", "evidence_text"] if evidence else ["selected_title"],
            )
        if "snap" in combined and any(token in combined for token in ("proposal", "rule", "eligibility", "bbce")):
            return (
                f"{publisher} warned that a SNAP eligibility proposal would tighten access to benefits for some households.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "food bank demand pressure":
        if re.search(r"food (?:bank|pantry).*?(?:demand|need).{0,30}(?:rise|rising|rose|surge|surging|increase)", combined):
            noun = "food pantry" if "food pantry" in combined or "pantry" in combined else "food bank"
            return (
                f"{publisher} reported rising {noun} demand.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
        record_spending_amount = re.search(r"(\$\s?\d+(?:\.\d+)?\s*(?:[mb]|million|billion))", title, re.IGNORECASE)
        record_spending_subject = re.search(r"^(.+?)\s+(?:to spend|is set to invest)\b", title, re.IGNORECASE)
        if (
            source_support
            and ("food bank" in combined or "pantry" in combined)
            and any(token in source_support for token in ("need grows", "need grow", "growing need", "as need grows", "need is growing"))
            and any(token in combined for token in ("record-breaking", "record breaking", "record"))
            and record_spending_amount
            and record_spending_subject
        ):
            subject = _nonempty(record_spending_subject.group(1))
            amount = _nonempty(record_spending_amount.group(1))
            if subject and amount:
                return (
                    f"{publisher} reported that {subject} expects to spend a record {amount} on food in 2026 as need grows.",
                    "derived_from_title_and_source_text",
                    ["selected_title", "summary_or_snippet"] if summary else ["selected_title", "evidence_text"],
                )
    if pressure_type == "school meals pressure":
        if ("school meals" in combined or "summer meals" in combined) and any(token in combined for token in ("gap", "end", "cut", "loss", "access")):
            meal_term = "summer meals" if "summer meals" in combined else "school meals"
            return (
                f"{publisher} reported pressure on {meal_term} access.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "school meal price pressure":
        if any(token in combined for token in ("meal price increase", "meal prices", "school lunch price", "school meal price")):
            return (
                f"{publisher} reported school meal price pressure for families.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "benefit access pressure":
        if any(token in combined for token in ("snap", "ebt", "wic", "benefit", "benefits")) and any(
            token in combined for token in ("access", "eligibility", "delay", "disruption", "application", "renewal", "recertification", "backlog")
        ):
            return (
                f"{publisher} reported pressure on access to food assistance benefits.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "food affordability pressure":
        if any(token in combined for token in ("grocery prices", "food costs", "food prices", "inflation", "rent and groceries")):
            return (
                f"{publisher} reported food affordability pressure for households.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "household food insecurity pressure":
        if any(token in combined for token in ("food insecurity", "food hardship", "food insufficiency", "food sufficiency")):
            return (
                f"{publisher} reported rising household food insecurity pressure.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    if pressure_type == "emergency food access pressure":
        if any(token in combined for token in ("emergency food assistance", "food distribution", "meal site", "meal sites")) and any(
            token in combined for token in ("access", "availability", "closure", "closed", "hours", "distance")
        ):
            return (
                f"{publisher} reported pressure on access to emergency food assistance.",
                "derived_from_source_text",
                ["summary_or_snippet"] if summary else ["evidence_text"],
            )
    return "", "insufficient_source_support", []


def _derive_source_role_from_source_fields(
    row: dict[str, Any],
    *,
    source_fields: dict[str, str],
) -> tuple[str, str, list[str]]:
    existing = _nonempty(row.get("source_role"))
    if existing:
        return existing, "existing", ["source_role"]
    combined = " ".join(value.lower() for value in source_fields.values() if value)
    discovery_lane = _nonempty(row.get("discovery_lane"))
    source_family = _candidate_source_family(row)
    trace_url = _nonempty(row.get("source_url") or row.get("final_trace_url"))
    article_specific = _is_probable_article_slug(trace_url) or _is_document_specific_url(trace_url)
    title_and_snippet = " ".join(
        value.lower()
        for value in (
            _nonempty(source_fields.get("selected_title")),
            _nonempty(source_fields.get("summary_or_snippet")),
        )
        if value
    )
    path_segments = set(_path_segments(trace_url))
    resource_path = bool(path_segments & {"program", "programs", "resource", "resources", "find-food", "find-food-near-you", "locator"})
    resource_page = resource_path or (
        _nonempty(row.get("classification_status")) == "context_only" and any(token in title_and_snippet for token in RESOURCE_CONTEXT_TERMS)
    )
    if article_specific and source_family in {"nonprofit_report", "institutional_update"} and any(
        token in combined for token in DISCOVERY_POLICY_SOURCE_TERMS
    ):
        fields = [field for field in ("selected_title", "summary_or_snippet", "evidence_text") if _nonempty(source_fields.get(field))]
        return "policy_analysis", "derived_from_policy_source_text", fields or ["evidence_text"]
    if resource_page:
        fields = [field for field in ("selected_title", "summary_or_snippet", "evidence_text") if _nonempty(source_fields.get(field))]
        return "resource_context", "derived_as_resource_context", fields or ["evidence_text"]
    if article_specific and discovery_lane == "public_radio":
        return "public_radio_report", "derived_from_discovery_lane", ["source_family"]
    if article_specific and source_family in {"local_news", "local_news_direct_rss", "state_policy_news"}:
        return "local_news_report", "derived_from_source_family", ["source_family"]
    if article_specific and discovery_lane in {"food_bank_provider", "feeding_america_affiliate"}:
        return "food_bank_update", "derived_from_discovery_lane", ["source_family"]
    if article_specific and source_family in {"nonprofit_report", "institutional_update"}:
        return "institutional_report", "derived_from_source_family", ["source_family"]
    return "", "insufficient_source_support", []


def _candidate_public_evidence_basis(row: dict[str, Any]) -> str:
    basis = _nonempty(row.get("evidence_text_basis"))
    if basis:
        return basis
    if _nonempty(row.get("classification_status")) == "manual_fallback":
        return "manual_source_text"
    if _nonempty(row.get("fetch_status")) == "ok":
        return "page_text_excerpt"
    if _nonempty(row.get("discovery_channel")) in {"direct_rss", "google_news_rss"}:
        return "rss_item_text"
    return "manual_source_text"


def _derive_candidate_public_prose(row: dict[str, Any], *, edition_date: str) -> dict[str, Any]:
    evidence_text = _candidate_public_evidence_text(row)
    basis = _candidate_public_evidence_basis(row)
    source_fields = _candidate_source_text_fields(row)
    eval_row = {
        "title": _nonempty(row.get("selected_title") or row.get("discovered_title")),
        "summary_or_snippet": _nonempty(row.get("summary_or_snippet") or row.get("manually_reviewed_summary") or row.get("pressure_evidence_summary")),
        "summary_fallback": _nonempty(row.get("pressure_evidence_summary") or row.get("manually_reviewed_summary")),
        "evidence_text": evidence_text,
        "evidence_text_basis": basis if evidence_text else "",
        "collector_source_type": "page" if basis == "page_text_excerpt" else ("rss" if basis == "rss_item_text" else "manual"),
        "source_family": _candidate_source_family(row),
        "source_name": _nonempty(row.get("direct_source_name") or row.get("discovered_publisher")),
        "publisher": _nonempty(row.get("discovered_publisher")),
        "location_name": _candidate_location_name(row),
        "published_at": _nonempty(row.get("source_published_date")),
        "state": _nonempty(row.get("state_abbrev")),
        "url": _nonempty(row.get("source_url") or row.get("final_trace_url")),
    }
    pressure_required = _nonempty(row.get("classification_status")) in {"qualified_pressure_signal", "manual_fallback"}
    evaluated = evaluate_food_line_pressure(
        eval_row,
        edition_date=edition_date,
        pressure_required=pressure_required,
    )
    existing_pressure_type = _nonempty(row.get("pressure_type"))
    evaluated_pressure_type = _nonempty(evaluated.get("pressure_type"))
    derived_pressure_type, pressure_type_status, pressure_type_fields = ("", "insufficient_source_support", [])
    if not existing_pressure_type and (not evaluated_pressure_type or evaluated_pressure_type == "context only"):
        derived_pressure_type, pressure_type_status, pressure_type_fields = _derive_pressure_type_from_source_fields(source_fields)
    elif existing_pressure_type:
        pressure_type_status = "existing"
        pressure_type_fields = ["pressure_type"]
    else:
        pressure_type_status = "existing_from_evaluator"
        pressure_type_fields = ["evidence_text"]
    final_pressure_type = existing_pressure_type or (evaluated_pressure_type if evaluated_pressure_type != "context only" else "") or derived_pressure_type

    existing_pressure_summary = _nonempty(row.get("pressure_summary") or row.get("pressure_evidence_summary"))
    derived_pressure_summary, pressure_summary_status, pressure_summary_fields = ("", "insufficient_source_support", [])
    if existing_pressure_summary:
        pressure_summary_status = "existing"
        pressure_summary_fields = ["pressure_evidence_summary" if _nonempty(row.get("pressure_evidence_summary")) else "pressure_summary"]
    elif _nonempty(evaluated.get("pressure_summary")):
        pressure_summary_status = "existing_from_evaluator"
        pressure_summary_fields = ["evidence_text"]
    else:
        derived_pressure_summary, pressure_summary_status, pressure_summary_fields = _derive_pressure_summary_from_source_fields(
            row,
            pressure_type=final_pressure_type,
            source_fields=source_fields,
        )
    final_pressure_summary = existing_pressure_summary or _nonempty(evaluated.get("pressure_summary")) or derived_pressure_summary
    source_role, source_role_status, source_role_fields = _derive_source_role_from_source_fields(row, source_fields=source_fields)

    affected_groups = list(row.get("affected_groups") or evaluated.get("affected_groups") or [])
    if pressure_required and not affected_groups:
        affected_groups = [SAFE_AFFECTED_GROUPS_FALLBACK]
    pressure_derivation_fields = list(dict.fromkeys([*pressure_type_fields, *pressure_summary_fields]))
    derivation_fields = list(dict.fromkeys([*pressure_derivation_fields, *source_role_fields]))
    public_prose_derivation_status = "insufficient_source_support"
    if final_pressure_type and final_pressure_summary and pressure_derivation_fields:
        if pressure_type_status == "existing" and pressure_summary_status == "existing":
            public_prose_derivation_status = "existing_complete"
        elif "insufficient_source_support" not in {pressure_type_status, pressure_summary_status}:
            public_prose_derivation_status = "derived_complete"
    elif pressure_derivation_fields:
        public_prose_derivation_status = "partial_derived_missing_fields"
    return {
        "pressure_signal": bool(row.get("pressure_signal")) or bool(evaluated.get("pressure_signal")) or pressure_required,
        "pressure_type": final_pressure_type,
        "pressure_summary": final_pressure_summary,
        "affected_groups": affected_groups,
        "evidence_level": _nonempty(row.get("evidence_level") or evaluated.get("evidence_level")),
        "freshness_role": _nonempty(row.get("freshness_role") or evaluated.get("freshness_role")),
        "source_role": source_role or _nonempty(evaluated.get("source_role")),
        "evidence_text": evidence_text,
        "evidence_text_basis": basis if evidence_text else _nonempty(evaluated.get("evidence_text_basis")),
        "public_prose_derivation_status": public_prose_derivation_status,
        "public_prose_derivation_source_fields": derivation_fields,
        "pressure_summary_derivation_status": pressure_summary_status,
        "pressure_type_derivation_status": pressure_type_status,
        "source_role_derivation_status": source_role_status,
        "source_role_derivation_source_fields": source_role_fields,
    }


def _missing_public_prose_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _nonempty(row.get("pressure_summary")):
        missing.append("pressure_summary")
    pressure_type = _nonempty(row.get("pressure_type"))
    if not pressure_type or pressure_type == "context only":
        missing.append("pressure_type")
    affected_groups = [_nonempty(item) for item in list(row.get("affected_groups") or []) if _nonempty(item)]
    if not affected_groups:
        missing.append("affected_groups")
    if not _nonempty(row.get("evidence_level")):
        missing.append("evidence_level")
    if not _nonempty(row.get("freshness_role")):
        missing.append("freshness_role")
    if not _nonempty(row.get("source_role")):
        missing.append("source_role")
    return missing


def _apply_public_readiness_gate(row: dict[str, Any], *, edition_date: str) -> None:
    row.update(_derive_candidate_public_prose(row, edition_date=edition_date))
    missing = _missing_public_prose_fields(row)
    row["missing_public_prose_fields"] = missing
    blockers = list(row.get("public_claim_blockers") or [])
    if missing and "missing_public_prose_fields" not in blockers:
        blockers.append("missing_public_prose_fields")
    if missing:
        row["public_claim_eligible"] = False
    row["public_claim_blockers"] = blockers


def _is_probable_article_slug(url: str) -> bool:
    value = _normalize_url(url)
    if not value or _is_document_specific_url(value):
        return False
    segments = _path_segments(value)
    if not segments:
        return False
    parsed = urllib.parse.urlsplit(value)
    if parsed.query and any(key in urllib.parse.parse_qs(parsed.query) for key in ("page", "paged")):
        return False
    lowered_segments = [segment.lower() for segment in segments]
    if len(lowered_segments) == 1 and lowered_segments[0].split(".", 1)[0] in LISTING_PATH_SEGMENTS:
        return False
    last_segment = lowered_segments[-1].split(".", 1)[0]
    if last_segment in LISTING_PATH_SEGMENTS or last_segment in ARCHIVE_ACTION_SEGMENTS or last_segment in ARCHIVE_RESOURCE_SEGMENTS:
        return False
    words = _path_words(last_segment)
    if len(words) >= 4:
        return True
    if len(words) >= 3 and any(any(char.isdigit() for char in segment) for segment in lowered_segments):
        return True
    if len(words) >= 2 and len(lowered_segments) >= 2:
        return True
    return bool(_extract_url_date(value))


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
    source_url: str = "",
    original_source_url: str = "",
    title_quality_status: str = "",
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
    if _is_homepage_or_landing_url(source_url) or _is_homepage_or_landing_url(original_source_url):
        blockers.append("homepage_or_landing_url")
    if traceability_status == "publisher_homepage_trace_only":
        blockers.append("publisher_homepage_trace_only")
    elif traceability_status == "non_article_trace_url":
        blockers.append("non_article_trace_url")
    elif traceability_status != "traceable":
        blockers.append(traceability_status or "traceability_incomplete")
    if title_quality_status in {"generic_or_invalid_title", "missing_title"}:
        blockers.append("generic_or_invalid_title")
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
    source_url: str = "",
    original_source_url: str = "",
    title_quality_status: str = "",
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
        source_url=source_url,
        original_source_url=original_source_url,
        title_quality_status=title_quality_status,
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
                "historical_capable": bool(direct_source.get("historical_capable", False)),
                "historical_archive_templates": _normalize_historical_archive_templates(
                    direct_source.get("historical_archive_templates")
                    or direct_source.get("archive_url_templates")
                    or direct_source.get("archive_url_template")
                ),
                "historical_archive_pagination_enabled": bool(direct_source.get("historical_archive_pagination_enabled", False)),
                "archive_page_url_template": _nonempty(direct_source.get("archive_page_url_template")),
                "archive_page_start": int(direct_source.get("archive_page_start") or 1),
                "archive_page_max_pages": int(direct_source.get("archive_page_max_pages") or 0),
                "archive_page_increment": int(direct_source.get("archive_page_increment") or 1),
                "archive_pagination_notes": _nonempty(direct_source.get("archive_pagination_notes")),
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
        return fetcher(url, timeout=_request_timeout_seconds()), ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


def _fetch_url_with_metadata(fetcher: Any, url: str) -> tuple[bytes, str, dict[str, Any]]:
    try:
        if getattr(fetcher, "__module__", "").endswith("food_line_sources") and getattr(fetcher, "__name__", "") == "_fetch":
            payload, meta = _project_fetch_with_metadata(url, timeout=_request_timeout_seconds())
            return payload, "", meta
        result = fetcher(url, timeout=_request_timeout_seconds())
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


def _meta_content(text: str, *, attr: str, value: str) -> str:
    pattern = rf'<meta\b[^>]*{attr}=["\']{re.escape(value)}["\'][^>]*content=["\']([^"\']+)["\']'
    match = re.search(pattern, text, re.IGNORECASE)
    return _clean_title_text(match.group(1)) if match else ""


def _extract_json_ld_headlines(text: str) -> list[str]:
    candidates: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("@type") or "")
            type_values = {part.strip().lower() for part in re.split(r"[, ]+", node_type) if part.strip()}
            if any(value in type_values for value in {"article", "newsarticle", "report"}):
                headline = _clean_title_text(str(node.get("headline") or ""))
                if headline:
                    candidates.append(headline)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw_json = html.unescape(match.group(1)).strip()
        if not raw_json:
            continue
        try:
            visit(json.loads(raw_json))
        except Exception:  # noqa: BLE001
            continue
    return _dedupe_texts(candidates)


def _extract_scoped_h1_titles(text: str) -> list[str]:
    candidates: list[str] = []
    for scope_pattern in (
        r"<article\b[^>]*>(.*?)</article>",
        r"<main\b[^>]*>(.*?)</main>",
        r'<div\b[^>]*(?:class|id)=["\'][^"\']*(?:content|article|post|entry|story)[^"\']*["\'][^>]*>(.*?)</div>',
    ):
        for scope_match in re.finditer(scope_pattern, text, flags=re.IGNORECASE | re.DOTALL):
            scope = scope_match.group(1)
            h1_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", scope, flags=re.IGNORECASE | re.DOTALL)
            if h1_match:
                candidates.append(_clean_title_text(h1_match.group(1)))
    for h1_match in re.finditer(r"<h1\b[^>]*>(.*?)</h1>", text, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(_clean_title_text(h1_match.group(1)))
    return _dedupe_texts(candidates)


def _strip_site_suffix(title: str) -> str:
    cleaned = _clean_title_text(title)
    if not cleaned:
        return ""
    for separator in (" | ", " - ", " — ", " :: ", " / "):
        if separator not in cleaned:
            continue
        parts = [part.strip() for part in cleaned.split(separator) if part.strip()]
        if not parts:
            continue
        first = parts[0]
        if _normalized_title_key(first) and first != cleaned:
            return first
    return cleaned


def _title_quality_status(title: str, *, publisher: str = "") -> str:
    cleaned = _clean_title_text(title)
    normalized = _normalized_title_key(cleaned)
    publisher_normalized = _normalized_title_key(_clean_title_text(publisher))
    if not normalized:
        return "missing_title"
    if cleaned.lower().startswith(("http://", "https://")):
        return "generic_or_invalid_title"
    if normalized in GENERIC_TITLE_EXACT_NORMALIZED:
        return "generic_or_invalid_title"
    if normalized in GENERIC_CHROME_TITLES_NORMALIZED:
        return "generic_or_invalid_title"
    if publisher_normalized and normalized == publisher_normalized:
        return "generic_or_invalid_title"
    if re.match(r"^(home|homepage|welcome|index)\b", normalized):
        return "generic_or_invalid_title"
    words = [word for word in re.split(r"[\s/|:;,_-]+", normalized) if word]
    if len(words) <= 2 and all(word in GENERIC_TITLE_SINGLE_WORDS for word in words):
        return "generic_or_invalid_title"
    if len(words) == 1 and len(words[0]) <= 3:
        return "generic_or_invalid_title"
    return "valid_article_title"


def _pick_best_title(
    payload: bytes,
    *,
    fallback_title: str = "",
    publisher: str = "",
) -> tuple[str, str, list[str], str]:
    text = payload.decode("utf-8", errors="replace")
    raw_candidates: list[tuple[str, str]] = []

    def add(method: str, value: str) -> None:
        cleaned = _clean_title_text(value)
        if cleaned:
            raw_candidates.append((method, cleaned))

    add("og_title", _meta_content(text, attr="property", value="og:title"))
    for headline in _extract_json_ld_headlines(text):
        add("json_ld_headline", headline)
    add("twitter_title", _meta_content(text, attr="name", value="twitter:title"))
    h1_candidates = _extract_scoped_h1_titles(text)
    if h1_candidates:
        add("article_h1", h1_candidates[0])
        for extra in h1_candidates[1:]:
            add("page_h1", extra)
    document_title = _strip_site_suffix(_page_title(payload))
    add("document_title", document_title)
    add("feed_or_listing_title", fallback_title)

    selected_title = ""
    selected_method = ""
    for method, candidate in raw_candidates:
        if _title_quality_status(candidate, publisher=publisher) == "valid_article_title":
            selected_title = candidate
            selected_method = method
            break
    if not selected_title and raw_candidates:
        selected_method, selected_title = raw_candidates[0]

    title_quality_status = _title_quality_status(selected_title, publisher=publisher)
    return selected_title, selected_method, _dedupe_texts([value for _, value in raw_candidates]), title_quality_status


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
    return _extract_page_published_date_info(payload)[0]


def _is_document_specific_url(url: str) -> bool:
    value = _normalize_url(url)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    path = (parsed.path or "").lower()
    return any(path.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"))


def _archive_link_filter_decision(
    url: str,
    *,
    anchor_text: str,
    link_context: str,
    listing_context_date: str,
) -> tuple[str, str]:
    normalized_url = _normalize_url(url)
    if not normalized_url:
        return "rejected_unknown_nonarticle", "empty_or_invalid_url"
    landing_reason = _homepage_or_landing_url_reason(normalized_url)
    if landing_reason in {"homepage_root", "landing_segment:home", "landing_segment:homepage", "landing_segment:index", "landing_segment:welcome", "landing_segment:default"}:
        return "rejected_navigation_link", landing_reason
    host = _host(normalized_url)
    if host.endswith(SOCIAL_DOMAINS):
        return "rejected_navigation_link", "social_domain"
    parsed = urllib.parse.urlsplit(normalized_url)
    segments = [segment.lower() for segment in _path_segments(normalized_url)]
    path_text = "/".join(segments)
    last_segment = segments[-1].split(".", 1)[0] if segments else ""
    anchor_key = _normalized_title_key(anchor_text)
    combined_text = " ".join(part for part in (anchor_key, normalized_url.lower()) if part)
    query_map = urllib.parse.parse_qs(parsed.query)
    if any(token in combined_text for token in ("legislative action center", "take action", "take-action")):
        return "rejected_action_link", "action_anchor_text"
    if last_segment in ARCHIVE_ACTION_SEGMENTS or any(f"/{segment}" in parsed.path.lower() for segment in ARCHIVE_ACTION_SEGMENTS):
        return "rejected_action_link", f"path_segment:{last_segment or 'action'}"
    if "advocacy" in combined_text or "donate" in combined_text:
        return "rejected_action_link", "action_or_donate_text"
    if any(key in query_map for key in ("s", "search", "q")) or "/search" in parsed.path.lower():
        return "rejected_listing_link", "search_listing"
    if any(segment in {"category", "categories", "tag", "tags", "author"} for segment in segments):
        return "rejected_listing_link", "taxonomy_listing"
    if any(key in query_map for key in ("tag", "category", "author")):
        return "rejected_listing_link", "taxonomy_query"
    if any(key in query_map for key in ("page", "paged")) or re.search(r"/page/\d+(?:/|$)", parsed.path.lower()):
        return "rejected_navigation_link", "pagination_link"
    if anchor_key in ARCHIVE_NAVIGATION_TEXT:
        return "rejected_navigation_link", f"anchor_text:{anchor_key}"
    if any(token in combined_text for token in ("share", "facebook", "twitter", "instagram", "linkedin")):
        return "rejected_navigation_link", "share_or_social_link"
    if len(segments) <= 1 and last_segment in LISTING_PATH_SEGMENTS:
        return "rejected_listing_link", f"listing_root:{last_segment}"
    if len(segments) <= 2 and any(segment in ARCHIVE_RESOURCE_SEGMENTS for segment in segments):
        return "rejected_resource_landing", f"resource_segment:{last_segment or segments[-1]}"
    if _is_document_specific_url(normalized_url):
        return "accepted_article_link", "document_url"
    if listing_context_date:
        return "accepted_article_link", "listing_context_date"
    if _extract_url_date(normalized_url):
        return "accepted_article_link", "url_date"
    if _is_probable_article_slug(normalized_url):
        return "accepted_article_link", "article_slug"
    if _is_feed_or_listing_url(normalized_url):
        return "rejected_listing_link", "listing_url"
    if _is_article_specific_url(normalized_url):
        return "rejected_unknown_nonarticle", "weak_article_path_without_date_or_slug"
    return "rejected_unknown_nonarticle", "non_article_path"


def _extract_listing_context_date(text: str, start: int, end: int) -> str:
    before = text[max(0, start - 200) : start]
    after = text[end : min(len(text), end + 80)]
    before_matches = list(
        re.finditer(
            r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
            before,
            re.IGNORECASE,
        )
    )
    if before_matches:
        candidate = _extract_source_date(before_matches[-1].group(0))
        if candidate:
            return candidate
    return _extract_page_body_date(after.encode("utf-8"))


def _extract_listing_links(
    payload: bytes,
    *,
    base_url: str,
    allowed_domains: list[str],
    source_name: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    text = payload.decode("utf-8", errors="replace")
    rows: list[dict[str, str]] = []
    accepted_by_reason: Counter[str] = Counter()
    rejected_by_reason: Counter[str] = Counter()
    seen: set[str] = set()
    rejected_links: list[dict[str, str]] = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.IGNORECASE | re.DOTALL):
        href = urllib.parse.urljoin(base_url, html.unescape(match.group(1)).strip())
        normalized_href = _normalize_url(href)
        if not normalized_href or normalized_href in seen:
            continue
        if allowed_domains and not _domain_allowed(normalized_href, allowed_domains):
            continue
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        title = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not title:
            title = normalized_href.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        contextual_date = _extract_listing_context_date(text, match.start(), match.end())
        link_context = _cap_text(text[max(0, match.start() - 120) : min(len(text), match.end() + 120)])
        filter_status, filter_reason = _archive_link_filter_decision(
            normalized_href,
            anchor_text=title,
            link_context=link_context,
            listing_context_date=contextual_date,
        )
        if filter_status != "accepted_article_link":
            rejected_by_reason[filter_reason] += 1
            rejected_links.append(
                {
                    "link": normalized_href,
                    "status": filter_status,
                    "reason": filter_reason,
                    "anchor_text": _cap_text(title),
                    "link_context": link_context,
                }
            )
            seen.add(normalized_href)
            continue
        rows.append(
            {
                "title": title[:240],
                "link": normalized_href,
                "description": "",
                "pubDate": "",
                "published_date": contextual_date,
                "source_url": normalized_href,
                "source_name": source_name,
                "date_basis_hint": "page_body_date" if contextual_date else "",
                "archive_link_filter_status": filter_status,
                "archive_link_filter_reason": filter_reason,
                "archive_source_anchor_text": _cap_text(title),
                "archive_source_link_context": link_context,
            }
        )
        accepted_by_reason[filter_reason] += 1
        seen.add(normalized_href)
        if len(rows) >= 25:
            break
    return rows, {
        "archive_links_accepted_count": len(rows),
        "archive_links_rejected_count": len(rejected_links),
        "archive_links_accepted_by_reason": dict(sorted(accepted_by_reason.items())),
        "archive_links_rejected_by_reason": dict(sorted(rejected_by_reason.items())),
        "rejected_links": rejected_links[:25],
    }


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
    diagnostics = {
        "attempted": bool(source_url),
        "success": False,
        "error": "",
        "item_count": 0,
        "response_status": None,
        "final_response_url": source_url,
        "content_type": "",
        "redirect_chain": [source_url] if source_url else [],
        "failure_reason": "",
        "exception_message": "",
        "parser_attempted": "",
        "historical_archive_source_has_templates": False,
        "historical_archive_urls": [],
        "historical_archive_fetch_attempt_count": 0,
        "historical_archive_fetch_success_count": 0,
        "historical_archive_fetch_failure_count": 0,
        "historical_archive_url_count": 0,
        "historical_archive_candidates_extracted_count": 0,
        "historical_archive_fetch_failure_reasons": {},
        "historical_archive_pagination_enabled": False,
        "historical_archive_page_fetch_attempt_count": 0,
        "historical_archive_page_fetch_success_count": 0,
        "historical_archive_page_fetch_failure_count": 0,
        "historical_archive_pages_fetched": [],
        "historical_archive_links_extracted_count": 0,
        "historical_archive_in_window_candidates": 0,
        "historical_archive_stop_reason": "",
        "historical_archive_stop_context": "",
        "historical_archive_duplicate_link_count": 0,
        "archive_links_accepted_count": 0,
        "archive_links_rejected_count": 0,
        "archive_links_accepted_by_reason": {},
        "archive_links_rejected_by_reason": {},
        "archive_rejected_links_sample": [],
    }
    source_name = _nonempty(query_row.get("direct_source_name"))

    def parse_payload(payload: bytes, *, current_url: str, content_type: str, final_response_url: str) -> tuple[list[dict[str, str]], str]:
        if _looks_like_xml_payload(payload, content_type):
            return _parse_direct_feed(payload), "rss_or_atom"
        if _looks_like_json_payload(payload, content_type):
            return _parse_json_feed(payload), "json_feed"
        if _looks_like_html_payload(payload, content_type):
            page_url = _normalize_url(_extract_canonical_url(payload) or final_response_url or current_url)
            extracted, listing_meta = _extract_listing_links(
                payload,
                base_url=page_url or current_url,
                allowed_domains=allowed_domains,
                source_name=source_name,
            )
            diagnostics["archive_links_accepted_count"] = int(diagnostics.get("archive_links_accepted_count", 0)) + int(listing_meta.get("archive_links_accepted_count", 0))
            diagnostics["archive_links_rejected_count"] = int(diagnostics.get("archive_links_rejected_count", 0)) + int(listing_meta.get("archive_links_rejected_count", 0))
            accepted_reason_counts = Counter(dict(diagnostics.get("archive_links_accepted_by_reason") or {}))
            accepted_reason_counts.update(dict(listing_meta.get("archive_links_accepted_by_reason") or {}))
            diagnostics["archive_links_accepted_by_reason"] = dict(sorted(accepted_reason_counts.items()))
            rejected_reason_counts = Counter(dict(diagnostics.get("archive_links_rejected_by_reason") or {}))
            rejected_reason_counts.update(dict(listing_meta.get("archive_links_rejected_by_reason") or {}))
            diagnostics["archive_links_rejected_by_reason"] = dict(sorted(rejected_reason_counts.items()))
            rejected_sample = list(diagnostics.get("archive_rejected_links_sample") or [])
            if len(rejected_sample) < 25:
                rejected_sample.extend(list(listing_meta.get("rejected_links") or [])[: max(0, 25 - len(rejected_sample))])
            diagnostics["archive_rejected_links_sample"] = rejected_sample[:25]
            if extracted:
                return extracted, "html_listing"
            fallback_title, _, _, _ = _pick_best_title(payload, fallback_title=source_name)
            return (
                [
                    {
                        "title": fallback_title or source_name,
                        "link": page_url,
                        "description": _page_summary(payload),
                        "pubDate": "",
                        "source_url": current_url,
                        "source_name": source_name,
                    }
                ],
                "html_listing",
            )
        raise ValueError("unsupported direct source payload type")

    archive_items: list[dict[str, str]] = []
    archive_templates = _normalize_historical_archive_templates(query_row.get("historical_archive_templates"))
    rendered_archives = _render_historical_archive_urls(_nonempty(query_row.get("edition_date")), archive_templates)
    diagnostics["historical_archive_source_has_templates"] = bool(rendered_archives)
    diagnostics["historical_archive_urls"] = rendered_archives
    diagnostics["historical_archive_url_count"] = len(rendered_archives)
    for archive in rendered_archives:
        archive_url = _nonempty(archive.get("archive_url"))
        if not archive_url:
            continue
        diagnostics["historical_archive_fetch_attempt_count"] += 1
        archive_payload, archive_error, archive_meta = _fetch_url_with_metadata(fetcher, archive_url)
        if archive_error or not archive_payload:
            reason = _classify_fetch_error(archive_error or "empty response")
            diagnostics["historical_archive_fetch_failure_count"] += 1
            diagnostics["historical_archive_fetch_failure_reasons"][reason] = (
                int(diagnostics["historical_archive_fetch_failure_reasons"].get(reason, 0)) + 1
            )
            continue
        try:
            parsed_archive_items, _ = parse_payload(
                archive_payload,
                current_url=archive_url,
                content_type=_nonempty(archive_meta.get("content_type")),
                final_response_url=_nonempty(archive_meta.get("final_response_url") or archive_url),
            )
        except Exception as exc:  # noqa: BLE001
            reason = _classify_fetch_error(f"{type(exc).__name__}: {exc}")
            diagnostics["historical_archive_fetch_failure_count"] += 1
            diagnostics["historical_archive_fetch_failure_reasons"][reason] = (
                int(diagnostics["historical_archive_fetch_failure_reasons"].get(reason, 0)) + 1
            )
            continue
        diagnostics["historical_archive_fetch_success_count"] += 1
        for rank, item in enumerate(parsed_archive_items, start=1):
            item_copy = dict(item)
            item_copy["archive_url_used"] = archive_url
            item_copy["archive_template_used"] = _nonempty(archive.get("archive_template_used"))
            item_copy["archive_granularity"] = _nonempty(archive.get("archive_granularity"))
            item_copy["archive_target_date"] = _nonempty(archive.get("archive_target_date"))
            item_copy["archive_candidate_rank"] = rank
            archive_items.append(item_copy)
        diagnostics["historical_archive_candidates_extracted_count"] += len(parsed_archive_items)

    pagination_enabled = bool(query_row.get("historical_archive_pagination_enabled")) and bool(_nonempty(query_row.get("archive_page_url_template")))
    diagnostics["historical_archive_pagination_enabled"] = pagination_enabled
    if pagination_enabled:
        page_template = _nonempty(query_row.get("archive_page_url_template"))
        start_page = max(1, int(query_row.get("archive_page_start") or 1))
        max_pages = max(0, int(query_row.get("archive_page_max_pages") or 0))
        page_increment = max(1, int(query_row.get("archive_page_increment") or 1))
        seen_pagination_links: set[str] = set()
        exact_or_window_hits = 0
        duplicate_link_count = 0
        target_date = _nonempty(query_row.get("edition_date"))
        lookback_days = max(0, int(query_row.get("query_lookback_days") or 0))
        lookahead_days = max(0, int(query_row.get("query_lookahead_days") or 0))
        older_margin_days = max(lookback_days + 3, int(query_row.get("max_age_days") or 0))
        for page_index in range(max_pages):
            page_number = start_page + (page_index * page_increment)
            page_url = _render_archive_page_url(page_template, edition_date=target_date, page=page_number)
            if not page_url:
                diagnostics["historical_archive_stop_reason"] = "invalid_page_template"
                diagnostics["historical_archive_stop_context"] = f"page={page_number}"
                break
            diagnostics["historical_archive_page_fetch_attempt_count"] += 1
            page_payload, page_error, page_meta = _fetch_url_with_metadata(fetcher, page_url)
            if page_error or not page_payload:
                reason = _classify_fetch_error(page_error or "empty response")
                diagnostics["historical_archive_page_fetch_failure_count"] += 1
                diagnostics["historical_archive_fetch_failure_reasons"][reason] = (
                    int(diagnostics["historical_archive_fetch_failure_reasons"].get(reason, 0)) + 1
                )
                diagnostics["historical_archive_stop_reason"] = "page_fetch_failure"
                diagnostics["historical_archive_stop_context"] = f"page={page_number} reason={reason}"
                continue
            diagnostics["historical_archive_page_fetch_success_count"] += 1
            diagnostics["historical_archive_pages_fetched"].append(page_url)
            try:
                page_items, parser_attempted = parse_payload(
                    page_payload,
                    current_url=page_url,
                    content_type=_nonempty(page_meta.get("content_type")),
                    final_response_url=_nonempty(page_meta.get("final_response_url") or page_url),
                )
            except Exception as exc:  # noqa: BLE001
                reason = _classify_fetch_error(f"{type(exc).__name__}: {exc}")
                diagnostics["historical_archive_page_fetch_failure_count"] += 1
                diagnostics["historical_archive_fetch_failure_reasons"][reason] = (
                    int(diagnostics["historical_archive_fetch_failure_reasons"].get(reason, 0)) + 1
                )
                diagnostics["historical_archive_stop_reason"] = "page_parse_failure"
                diagnostics["historical_archive_stop_context"] = f"page={page_number} parser={reason}"
                continue
            if parser_attempted != "html_listing":
                diagnostics["historical_archive_stop_reason"] = "non_listing_page"
                diagnostics["historical_archive_stop_context"] = f"page={page_number} parser={parser_attempted}"
                break
            new_page_items: list[dict[str, str]] = []
            page_dates: list[str] = []
            for rank, item in enumerate(page_items, start=1):
                normalized_link = _normalize_url(_nonempty(item.get("link")) or _nonempty(item.get("source_url")))
                if normalized_link and normalized_link in seen_pagination_links:
                    duplicate_link_count += 1
                    continue
                if normalized_link:
                    seen_pagination_links.add(normalized_link)
                item_copy = dict(item)
                item_copy["archive_page_url_used"] = page_url
                item_copy["archive_page_number"] = page_number
                item_copy["archive_pagination_rank"] = rank
                item_copy["archive_stop_context"] = ""
                item_copy["archive_url_used"] = page_url
                if _nonempty(item_copy.get("published_date")):
                    page_dates.append(_nonempty(item_copy.get("published_date")))
                    status = _date_match_status(
                        target_date,
                        _nonempty(item_copy.get("published_date")),
                        lookback_days=lookback_days,
                        lookahead_days=lookahead_days,
                    )
                    if status in {"exact_date", "within_query_window"}:
                        exact_or_window_hits += 1
                new_page_items.append(item_copy)
            diagnostics["historical_archive_links_extracted_count"] += len(new_page_items)
            archive_items.extend(new_page_items)
            if not new_page_items:
                diagnostics["historical_archive_stop_reason"] = "no_new_links"
                diagnostics["historical_archive_stop_context"] = f"page={page_number}"
                break
            if exact_or_window_hits >= max(1, int(query_row.get("direct_source_candidate_cap") or 1)):
                diagnostics["historical_archive_stop_reason"] = "enough_in_window_hits"
                diagnostics["historical_archive_stop_context"] = f"page={page_number} hits={exact_or_window_hits}"
                break
            older_than_margin = False
            if page_dates:
                oldest_page_date = min(page_dates)
                distance = _date_distance_days(target_date, oldest_page_date)
                if distance is not None and oldest_page_date < validate_date(target_date) and distance > older_margin_days:
                    older_than_margin = True
            if older_than_margin and exact_or_window_hits > 0:
                diagnostics["historical_archive_stop_reason"] = "older_than_target_margin"
                diagnostics["historical_archive_stop_context"] = f"page={page_number} margin_days={older_margin_days}"
                break
        if not diagnostics["historical_archive_stop_reason"]:
            diagnostics["historical_archive_stop_reason"] = "max_pages_reached" if max_pages > 0 else "pagination_disabled"
        diagnostics["historical_archive_duplicate_link_count"] = duplicate_link_count
        diagnostics["historical_archive_in_window_candidates"] = exact_or_window_hits

    payload, fetch_error, fetch_meta = _fetch_url_with_metadata(fetcher, source_url)
    diagnostics["response_status"] = fetch_meta.get("response_status")
    diagnostics["final_response_url"] = _nonempty(fetch_meta.get("final_response_url") or source_url)
    diagnostics["content_type"] = _nonempty(fetch_meta.get("content_type"))
    diagnostics["redirect_chain"] = list(fetch_meta.get("redirect_chain") or ([source_url] if source_url else []))
    direct_items: list[dict[str, str]] = []
    if not fetch_error and payload:
        try:
            direct_items, parser_attempted = parse_payload(
                payload,
                current_url=source_url,
                content_type=diagnostics["content_type"],
                final_response_url=diagnostics["final_response_url"],
            )
            diagnostics["parser_attempted"] = parser_attempted
        except Exception as exc:  # noqa: BLE001
            if _looks_like_xml_payload(payload, diagnostics["content_type"]):
                diagnostics["parser_attempted"] = "rss_or_atom"
            elif _looks_like_json_payload(payload, diagnostics["content_type"]):
                diagnostics["parser_attempted"] = "json_feed"
            elif _looks_like_html_payload(payload, diagnostics["content_type"]):
                diagnostics["parser_attempted"] = "html_listing"
            else:
                diagnostics["parser_attempted"] = "unknown"
            fetch_error = f"{type(exc).__name__}: {exc}"
    items: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for item in archive_items + direct_items:
        normalized_link = _normalize_url(_nonempty(item.get("link")) or _nonempty(item.get("source_url")))
        if normalized_link and normalized_link in seen_links:
            continue
        if normalized_link:
            seen_links.add(normalized_link)
        items.append(item)
    diagnostics["success"] = bool(items) or (not fetch_error and bool(payload)) or diagnostics["historical_archive_fetch_success_count"] > 0
    diagnostics["error"] = "" if diagnostics["success"] else _nonempty(fetch_error)
    diagnostics["failure_reason"] = "" if diagnostics["success"] else _classify_fetch_error(fetch_error or "empty response")
    diagnostics["exception_message"] = "" if diagnostics["success"] else _nonempty(fetch_error)
    diagnostics["item_count"] = len(items)
    return items, diagnostics


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


def _google_news_resolution_match_type(candidate_url: str, *, publisher_url: str = "", publisher_name: str = "") -> str:
    candidate_host = _host(candidate_url)
    if not candidate_host:
        return ""
    if publisher_url and _same_host_family(candidate_url, publisher_url):
        return "same_domain"
    candidate_base = _strip_common_publisher_subdomain(candidate_host)
    for expected_host in _known_publisher_domains(publisher_name):
        if candidate_base == expected_host or candidate_base.endswith(f".{expected_host}") or expected_host.endswith(f".{candidate_base}"):
            return "known_alias"
    return ""


def _same_host_family(candidate_url: str, publisher_url: str) -> bool:
    candidate_host = _strip_common_publisher_subdomain(_host(candidate_url))
    publisher_host = _strip_common_publisher_subdomain(_host(publisher_url))
    if not candidate_host or not publisher_host:
        return False
    return candidate_host == publisher_host or candidate_host.endswith(f".{publisher_host}") or publisher_host.endswith(f".{candidate_host}")


def _rejected_candidate_url_reason(candidate_url: str, *, publisher_url: str, publisher_name: str = "") -> str:
    url = _normalize_url(candidate_url)
    if not url:
        return "empty_url"
    if _is_google_news_wrapper(url) or _host(url).endswith(("google.com",)):
        return "google_domain"
    if _is_static_or_namespace_url(url):
        return "static_or_namespace_url"
    if (publisher_url or publisher_name) and not _google_news_resolution_match_type(
        url,
        publisher_url=publisher_url,
        publisher_name=publisher_name,
    ):
        return "not_same_publisher_family"
    landing_reason = _homepage_or_landing_url_reason(url)
    if landing_reason:
        if landing_reason.startswith(("listing_root:", "taxonomy_listing", "action_landing:", "resource_landing:")):
            return "listing_or_action_url"
        return "homepage_or_landing_url"
    if not _is_article_specific_url(url):
        return "not_article_specific"
    return ""


def _google_news_rejected_candidate_sample(
    candidates: list[str],
    *,
    publisher_url: str,
    publisher_name: str = "",
    limit: int = GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    expected_domains = sorted(_known_publisher_domains(publisher_name))
    if publisher_url:
        publisher_host = _strip_common_publisher_subdomain(_host(publisher_url))
        if publisher_host and publisher_host not in expected_domains:
            expected_domains.append(publisher_host)
            expected_domains.sort()
    sample: list[dict[str, Any]] = []
    for candidate in candidates[: max(limit, 0)]:
        normalized = _normalize_url(candidate)
        landing_reason = _homepage_or_landing_url_reason(normalized)
        sample.append(
            {
                "candidate_url": normalized,
                "normalized_domain": _strip_common_publisher_subdomain(_host(normalized)),
                "candidate_match_type": _google_news_resolution_match_type(
                    normalized,
                    publisher_url=publisher_url,
                    publisher_name=publisher_name,
                ),
                "expected_publisher_name": _nonempty(publisher_name),
                "expected_publisher_url": _nonempty(_normalize_url(publisher_url)),
                "expected_publisher_domain": _strip_common_publisher_subdomain(_host(publisher_url)),
                "expected_publisher_family_domains": expected_domains,
                "rejection_reason": _rejected_candidate_url_reason(
                    normalized,
                    publisher_url=publisher_url,
                    publisher_name=publisher_name,
                ),
                "homepage_or_landing_reason": landing_reason,
                "homepage_or_landing_filter_applied": bool(landing_reason),
            }
        )
    return sample, len(candidates) > len(sample)


def _google_news_article_id(url: str) -> str:
    raw = _normalize_url(url)
    if not _is_google_news_wrapper(raw):
        return ""
    path = urllib.parse.urlsplit(raw).path or ""
    if "/articles/" not in path:
        return ""
    return path.rsplit("/articles/", 1)[-1].strip("/")


def _decode_google_news_article_id_url(article_id: str) -> str:
    token = str(article_id or "").strip()
    if not token:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:  # noqa: BLE001
        return ""
    match = re.search(rb"https?://[^\s\"'<>\\\x00]+", decoded)
    if not match:
        return ""
    return _normalize_url(match.group(0).decode("utf-8", errors="ignore"))


def _extract_google_news_rpc_metadata(text: str, *, article_id: str = "") -> tuple[str, str, str]:
    token = _nonempty(article_id)
    if token:
        match = re.search(
            rf'data-n-a-id="{re.escape(token)}"[^>]*data-n-a-ts="([^"]+)"[^>]*data-n-a-sg="([^"]+)"',
            text,
        )
        if match:
            return token, _nonempty(match.group(1)), _nonempty(match.group(2))
        match = re.search(
            rf'data-p="[^"]*&quot;{re.escape(token)}&quot;,[^"]*?(\d+),&quot;([^"]+)&quot;\]"',
            text,
        )
        if match:
            return token, _nonempty(match.group(1)), _nonempty(match.group(2))
    match = re.search(r'data-n-a-id="([^"]+)"[^>]*data-n-a-ts="([^"]+)"[^>]*data-n-a-sg="([^"]+)"', text)
    if match:
        return _nonempty(match.group(1)), _nonempty(match.group(2)), _nonempty(match.group(3))
    match = re.search(r'data-p="[^"]*&quot;([^"]+)&quot;,[^"]*?(\d+),&quot;([^"]+)&quot;\]"', text)
    if match:
        return _nonempty(match.group(1)), _nonempty(match.group(2)), _nonempty(match.group(3))
    return "", "", ""


def _google_news_rpc_request(article_id: str, timestamp: str, signature: str) -> tuple[str, str]:
    inner_payload = json.dumps(
        ["garturlreq", GOOGLE_NEWS_RPC_CONTEXT, article_id, int(timestamp), signature],
        separators=(",", ":"),
    )
    payload = json.dumps(
        [[["Fbv4je", inner_payload, None, "generic"]]],
        separators=(",", ":"),
    )
    body = urllib.parse.urlencode({"f.req": payload}).encode("utf-8")
    request = urllib.request.Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=_request_timeout_seconds(),
            context=ssl._create_unverified_context(),
        ) as response:  # noqa: S310
            text = response.read(500_000).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return "", f"{type(exc).__name__}: {exc}"
    match = re.search(r'\[\\"garturlres\\",\\"(https?://[^"]+)\\",\d+\]', text)
    if not match:
        match = re.search(r'\["garturlres","(https?://[^"]+)",\d+\]', text)
    if not match:
        return "", "rpc_without_article_url"
    return _normalize_url(match.group(1)), ""


def _extract_google_news_article_url(payload: bytes, *, publisher_url: str = "", publisher_name: str = "") -> tuple[str, str]:
    text = payload.decode("utf-8", errors="replace")
    candidates = _extract_candidate_urls(text)
    for candidate in candidates:
        match_type = _google_news_resolution_match_type(candidate, publisher_url=publisher_url, publisher_name=publisher_name)
        if _is_article_specific_url(candidate) and match_type and not _is_google_news_wrapper(candidate):
            return candidate, match_type
    if publisher_url or publisher_name:
        return "", ""
    for candidate in candidates:
        if _is_article_specific_url(candidate) and not _is_google_news_wrapper(candidate):
            return candidate, "same_domain"
    return "", ""


def _extract_google_news_homepage_url(payload: bytes, *, publisher_url: str = "", publisher_name: str = "") -> tuple[str, str]:
    text = payload.decode("utf-8", errors="replace")
    candidates = _extract_candidate_urls(text)
    for candidate in candidates:
        match_type = _google_news_resolution_match_type(candidate, publisher_url=publisher_url, publisher_name=publisher_name)
        if _is_homepage_or_landing_url(candidate) and match_type and not _is_google_news_wrapper(candidate):
            return candidate, _rejected_candidate_url_reason(candidate, publisher_url=publisher_url, publisher_name=publisher_name)
    if publisher_url or publisher_name:
        return "", ""
    for candidate in candidates:
        if _is_homepage_or_landing_url(candidate) and not _is_google_news_wrapper(candidate):
            return candidate, _rejected_candidate_url_reason(candidate, publisher_url=publisher_url, publisher_name=publisher_name)
    return "", ""


def _resolve_google_news_wrapper(fetcher: Any, google_news_url: str, *, publisher_url: str = "", publisher_name: str = "") -> tuple[str, str, bool, dict[str, Any]]:
    url = _normalize_url(google_news_url)
    if not _is_google_news_wrapper(url):
        return "", "", False, {}
    article_id = _google_news_article_id(url)
    payload, fetch_error, fetch_meta = _fetch_url_with_metadata(fetcher, url)
    debug: dict[str, Any] = {
        "response_status": fetch_meta.get("response_status"),
        "final_response_url": _nonempty(fetch_meta.get("final_response_url") or url),
        "content_type": _nonempty(fetch_meta.get("content_type")),
        "redirect_chain": list(fetch_meta.get("redirect_chain") or ([url] if url else [])),
        "candidate_url_count_extracted": 0,
        "accepted_candidate_url": "",
        "accepted_candidate_match_type": "",
        "decoded_google_news_url": "",
        "google_news_rpc_url": "",
        "redirect_url_found": "",
        "canonical_url_found": "",
        "html_candidate_url_found": "",
        "google_news_article_id": article_id,
        "google_news_rpc_attempted": False,
        "google_news_rpc_error": "",
        "static_or_google_noise_only": False,
        "fallback_to_publisher_homepage": False,
        "rejection_reason": "",
        "google_news_resolution_status": "",
        "rejected_candidate_urls_sample": [],
        "rejected_candidate_urls_sample_limit": GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT,
        "rejected_candidate_urls_sample_truncated": False,
        "debug_snippet": "",
    }
    decoded_url = _decode_google_news_article_id_url(article_id)
    if decoded_url:
        debug["decoded_google_news_url"] = decoded_url
        decoded_match_type = _google_news_resolution_match_type(decoded_url, publisher_url=publisher_url, publisher_name=publisher_name)
        decoded_reason = _rejected_candidate_url_reason(decoded_url, publisher_url=publisher_url, publisher_name=publisher_name)
        if not decoded_reason:
            debug["accepted_candidate_url"] = decoded_url
            debug["accepted_candidate_match_type"] = decoded_match_type or "same_domain"
            debug["google_news_resolution_status"] = "resolved_known_alias" if decoded_match_type == "known_alias" else "resolved_same_domain"
            return decoded_url, "", True, debug
    if fetch_error or not payload:
        debug["rejection_reason"] = fetch_error or "empty response"
        debug["google_news_resolution_status"] = "failed_fetch_error"
        return "", fetch_error or "empty response", True, debug
    redirect_candidate = _normalize_url(_nonempty(fetch_meta.get("final_response_url")))
    if redirect_candidate and redirect_candidate != url and not _is_google_news_wrapper(redirect_candidate):
        debug["redirect_url_found"] = redirect_candidate
        redirect_match_type = _google_news_resolution_match_type(redirect_candidate, publisher_url=publisher_url, publisher_name=publisher_name)
        redirect_reason = _rejected_candidate_url_reason(redirect_candidate, publisher_url=publisher_url, publisher_name=publisher_name)
        if not redirect_reason:
            debug["accepted_candidate_url"] = redirect_candidate
            debug["accepted_candidate_match_type"] = redirect_match_type or "same_domain"
            debug["google_news_resolution_status"] = "resolved_known_alias" if redirect_match_type == "known_alias" else "resolved_same_domain"
            return redirect_candidate, "", True, debug
    text = payload.decode("utf-8", errors="replace")
    rpc_article_id, rpc_timestamp, rpc_signature = _extract_google_news_rpc_metadata(text, article_id=article_id)
    if rpc_article_id and rpc_timestamp and rpc_signature:
        debug["google_news_rpc_attempted"] = True
        rpc_url, rpc_error = _google_news_rpc_request(rpc_article_id, rpc_timestamp, rpc_signature)
        debug["google_news_rpc_error"] = _nonempty(rpc_error)
        if rpc_url:
            debug["google_news_rpc_url"] = rpc_url
            rpc_match_type = _google_news_resolution_match_type(rpc_url, publisher_url=publisher_url, publisher_name=publisher_name)
            rpc_reason = _rejected_candidate_url_reason(rpc_url, publisher_url=publisher_url, publisher_name=publisher_name)
            if not rpc_reason:
                debug["accepted_candidate_url"] = rpc_url
                debug["accepted_candidate_match_type"] = rpc_match_type or "same_domain"
                debug["google_news_resolution_status"] = "resolved_known_alias" if rpc_match_type == "known_alias" else "resolved_same_domain"
                return rpc_url, "", True, debug
    candidates = _extract_candidate_urls(text)
    meta_refresh = _extract_meta_refresh_target(text)
    if meta_refresh and meta_refresh not in candidates:
        candidates.append(meta_refresh)
    debug["candidate_url_count_extracted"] = len(candidates)
    rejected_sample, rejected_sample_truncated = _google_news_rejected_candidate_sample(
        candidates,
        publisher_url=publisher_url,
        publisher_name=publisher_name,
    )
    debug["rejected_candidate_urls_sample"] = rejected_sample
    debug["rejected_candidate_urls_sample_truncated"] = rejected_sample_truncated
    debug["debug_snippet"] = _extract_wrapper_debug_snippet(text)
    canonical = _extract_canonical_url(payload)
    debug["canonical_url_found"] = _nonempty(canonical)
    canonical_match_type = _google_news_resolution_match_type(canonical, publisher_url=publisher_url, publisher_name=publisher_name)
    if canonical and (canonical_match_type or (not publisher_url and not publisher_name)):
        reason = _rejected_candidate_url_reason(canonical, publisher_url=publisher_url, publisher_name=publisher_name)
        if not reason:
            debug["accepted_candidate_url"] = canonical
            debug["accepted_candidate_match_type"] = canonical_match_type or "same_domain"
            debug["google_news_resolution_status"] = "resolved_canonical_domain"
            return canonical, "", True, debug
    resolved, match_type = _extract_google_news_article_url(payload, publisher_url=publisher_url, publisher_name=publisher_name)
    if resolved:
        debug["html_candidate_url_found"] = resolved
        debug["accepted_candidate_url"] = resolved
        debug["accepted_candidate_match_type"] = match_type
        debug["google_news_resolution_status"] = "resolved_known_alias" if match_type == "known_alias" else "resolved_same_domain"
        return resolved, "", True, debug
    homepage, homepage_reason = _extract_google_news_homepage_url(payload, publisher_url=publisher_url, publisher_name=publisher_name)
    if homepage:
        debug["fallback_to_publisher_homepage"] = True
        debug["accepted_candidate_url"] = homepage
        debug["rejection_reason"] = homepage_reason
        debug["google_news_resolution_status"] = "failed_listing_or_action_url" if homepage_reason == "listing_or_action_url" else "failed_homepage_or_landing_url"
        return homepage, "", True, debug
    if not candidates:
        debug["rejection_reason"] = "no candidate urls extracted"
        debug["google_news_resolution_status"] = "failed_no_resolved_url"
        return "", "unresolved google news wrapper", True, debug
    rejected_reasons = [_rejected_candidate_url_reason(candidate, publisher_url=publisher_url, publisher_name=publisher_name) for candidate in candidates]
    if any(reason == "listing_or_action_url" for reason in rejected_reasons):
        debug["rejection_reason"] = "listing or action url only"
        debug["google_news_resolution_status"] = "failed_listing_or_action_url"
    elif any(reason == "homepage_or_landing_url" for reason in rejected_reasons):
        debug["rejection_reason"] = "homepage or landing url only"
        debug["google_news_resolution_status"] = "failed_homepage_or_landing_url"
    elif rejected_reasons and all(reason in {"google_domain", "static_or_namespace_url"} for reason in rejected_reasons if reason):
        debug["rejection_reason"] = "static or google noise only"
        debug["google_news_resolution_status"] = "failed_static_or_google_noise_only"
        debug["static_or_google_noise_only"] = True
    elif (publisher_url or publisher_name) and all(reason in {"google_domain", "static_or_namespace_url", "not_same_publisher_family"} for reason in rejected_reasons if reason):
        debug["rejection_reason"] = "no same publisher family candidate url"
        debug["google_news_resolution_status"] = "failed_no_same_publisher_family"
    else:
        debug["rejection_reason"] = "no resolved candidate url"
        debug["google_news_resolution_status"] = "failed_no_resolved_url"
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
    if _homepage_or_landing_url_reason(value):
        return True
    parsed = urllib.parse.urlsplit(value)
    path = [segment for segment in parsed.path.lower().split("/") if segment]
    if parsed.query and any(key in urllib.parse.parse_qs(parsed.query) for key in ("s", "search", "q", "tag", "category")):
        return True
    if not path:
        return True
    if len(path) >= 2 and path[-2] == "page" and path[-1].isdigit():
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
    title_quality_status = _title_quality_status(headline, publisher=publisher)
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
        source_url=trace_url or canonical,
        original_source_url=trace_url,
        title_quality_status=title_quality_status,
    )
    pressure_summary = _nonempty(record.get("pressure_summary") or record.get("pressure_evidence_summary"))
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
            "source_family": _nonempty(record.get("source_family") or "local_news"),
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
            "pressure_signal": bool(pressure_summary),
            "pressure_type": _nonempty(record.get("pressure_type")),
            "pressure_summary": pressure_summary,
            "traceability_status": traceability_status,
            "public_claim_eligible": public_claim_eligible,
            "public_claim_blockers": public_claim_blockers,
            "title_extraction_method": "manual_fallback",
            "raw_title_candidates": _dedupe_texts([headline]),
            "selected_title": headline,
            "title_quality_status": title_quality_status,
            "title_quality_blocker_applied": title_quality_status in {"generic_or_invalid_title", "missing_title"},
            "manually_reviewed_summary": _nonempty(record.get("manually_reviewed_summary")),
            "pressure_evidence_summary": _nonempty(record.get("pressure_evidence_summary")),
            "affected_groups": list(record.get("affected_groups") or []),
            "evidence_level": _nonempty(record.get("evidence_level")),
            "freshness_role": _nonempty(record.get("freshness_role")),
            "source_role": _nonempty(record.get("source_role")),
            "summary_or_snippet": _nonempty(record.get("pressure_evidence_summary") or record.get("manually_reviewed_summary")),
            "evidence_text": _cap_text(_nonempty(record.get("pressure_evidence_summary") or record.get("manually_reviewed_summary")), limit=1200),
            "evidence_text_basis": "manual_source_text",
            "missing_public_prose_fields": [],
            "public_prose_derivation_status": "existing_complete" if pressure_summary and _nonempty(record.get("pressure_type")) else "partial_derived_missing_fields",
            "public_prose_derivation_source_fields": ["pressure_evidence_summary"] if pressure_summary else [],
            "pressure_summary_derivation_status": "existing" if pressure_summary else "insufficient_source_support",
            "pressure_type_derivation_status": "existing" if _nonempty(record.get("pressure_type")) else "insufficient_source_support",
            "source_role_derivation_status": "existing" if _nonempty(record.get("source_role")) else "insufficient_source_support",
            "source_role_derivation_source_fields": ["source_role"] if _nonempty(record.get("source_role")) else [],
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
    candidate["title_extraction_method"] = _nonempty(candidate.get("title_extraction_method"))
    candidate["raw_title_candidates"] = list(candidate.get("raw_title_candidates") or [])
    candidate["selected_title"] = _nonempty(candidate.get("selected_title") or candidate.get("discovered_title"))
    candidate["title_quality_status"] = _nonempty(
        candidate.get("title_quality_status") or _title_quality_status(candidate["selected_title"], publisher=_nonempty(candidate.get("discovered_publisher")))
    )
    candidate["title_quality_blocker_applied"] = bool(candidate.get("title_quality_blocker_applied")) or candidate["title_quality_status"] in {
        "generic_or_invalid_title",
        "missing_title",
    }
    candidate["source_family"] = _nonempty(candidate.get("source_family"))
    candidate["pressure_signal"] = bool(candidate.get("pressure_signal"))
    candidate["pressure_type"] = _nonempty(candidate.get("pressure_type"))
    candidate["pressure_summary"] = _nonempty(candidate.get("pressure_summary"))
    candidate["evidence_level"] = _nonempty(candidate.get("evidence_level"))
    candidate["freshness_role"] = _nonempty(candidate.get("freshness_role"))
    candidate["source_role"] = _nonempty(candidate.get("source_role"))
    candidate["summary_or_snippet"] = _nonempty(candidate.get("summary_or_snippet"))
    candidate["evidence_text"] = _cap_text(_nonempty(candidate.get("evidence_text")), limit=1200)
    candidate["evidence_text_basis"] = _nonempty(candidate.get("evidence_text_basis"))
    candidate["missing_public_prose_fields"] = [item for item in list(candidate.get("missing_public_prose_fields") or []) if _nonempty(item)]
    candidate["public_prose_derivation_status"] = _nonempty(candidate.get("public_prose_derivation_status"))
    candidate["public_prose_derivation_source_fields"] = [
        item for item in list(candidate.get("public_prose_derivation_source_fields") or []) if _nonempty(item)
    ]
    candidate["pressure_summary_derivation_status"] = _nonempty(candidate.get("pressure_summary_derivation_status"))
    candidate["pressure_type_derivation_status"] = _nonempty(candidate.get("pressure_type_derivation_status"))
    candidate["source_role_derivation_status"] = _nonempty(candidate.get("source_role_derivation_status"))
    candidate["source_role_derivation_source_fields"] = [
        item for item in list(candidate.get("source_role_derivation_source_fields") or []) if _nonempty(item)
    ]
    if not _nonempty(candidate.get("state_or_territory")) and not _nonempty(candidate.get("state_abbrev")):
        inferred_state, inferred_abbrev = _infer_supported_state_or_territory(candidate)
        if inferred_state:
            candidate["state_or_territory"] = inferred_state
        if inferred_abbrev:
            candidate["state_abbrev"] = inferred_abbrev
        if inferred_state and inferred_state not in candidate["location_terms_detected"]:
            candidate["location_terms_detected"].append(inferred_state)
    else:
        candidate["state_or_territory"] = _nonempty(candidate.get("state_or_territory"))
        candidate["state_abbrev"] = _nonempty(candidate.get("state_abbrev"))
    candidate["state_hint"] = _nonempty(candidate.get("state_hint") or candidate.get("state_abbrev") or candidate.get("state_or_territory"))
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
                "pressure_signal",
                "pressure_type",
                "pressure_summary",
                "affected_groups",
                "evidence_level",
                "freshness_role",
                "source_role",
                "summary_or_snippet",
                "evidence_text",
                "evidence_text_basis",
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
    export_agent_inbox: bool = False,
    agent_inbox_dir: Path | None = None,
    query_plan_override: list[dict[str, Any]] | None = None,
    include_candidate_records: bool = False,
) -> dict[str, Any]:
    date_text = validate_date(edition_date)
    fetch = resolve_food_line_fetcher(fetcher)
    config = load_food_line_discovery_expansion_config(root)
    configured_direct_sources = [row for row in config.get("direct_sources") or [] if isinstance(row, dict)]
    query_plan = (
        [dict(row) for row in query_plan_override]
        if query_plan_override is not None
        else build_food_line_discovery_query_plan(
            root,
            date_text,
            lookback_days=query_lookback_days,
            lookahead_days=query_lookahead_days,
        )
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
    direct_candidates_by_date_match_status: Counter[str] = Counter()
    direct_candidates_by_date_basis: Counter[str] = Counter()
    direct_sources_with_in_window_items: set[str] = set()
    direct_sources_with_no_in_window_items: set[str] = set()
    historical_source_names: set[str] = set()
    historical_sources_with_exact_date_items: set[str] = set()
    historical_sources_with_url_date_items: set[str] = set()
    historical_sources_with_page_body_date_items: set[str] = set()
    historical_archive_source_names: set[str] = set()
    historical_archive_sources_with_templates: set[str] = set()
    historical_archive_sources_without_templates: set[str] = set()
    historical_archive_fetch_attempt_count = 0
    historical_archive_fetch_success_count = 0
    historical_archive_fetch_failure_count = 0
    historical_archive_url_count = 0
    historical_archive_candidates_extracted_count = 0
    historical_archive_selected_before_broad_count = 0
    historical_archive_fetch_failure_reasons_by_source: dict[str, Counter[str]] = {}
    historical_archive_candidates_by_source: dict[str, int] = {}
    historical_archive_exact_date_candidates_by_source: dict[str, int] = {}
    historical_archive_pagination_source_names: set[str] = set()
    historical_archive_page_fetch_attempt_count = 0
    historical_archive_page_fetch_success_count = 0
    historical_archive_page_fetch_failure_count = 0
    historical_archive_pages_fetched_by_source: dict[str, int] = {}
    historical_archive_links_extracted_by_source: dict[str, int] = {}
    archive_links_rejected_by_source: dict[str, int] = {}
    archive_links_accepted_by_source: dict[str, int] = {}
    archive_links_rejected_by_reason: Counter[str] = Counter()
    historical_archive_in_window_candidates_by_source: dict[str, int] = {}
    historical_archive_stop_reason_by_source: dict[str, str] = {}
    historical_archive_duplicate_link_count_by_source: dict[str, int] = {}
    historical_archive_pagination_sources_without_hits: set[str] = set()
    in_window_direct_candidate_count = 0
    out_of_window_direct_candidate_count = 0
    missing_date_direct_candidate_count = 0
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
        direct_meta: dict[str, Any] = {}
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
            if bool(query_row.get("historical_capable")) and source_name:
                historical_source_names.add(source_name)
            if bool(query_row.get("historical_capable")) and discovery_channel == "direct_page" and source_name:
                historical_archive_source_names.add(source_name)
                if bool(direct_meta.get("historical_archive_source_has_templates")):
                    historical_archive_sources_with_templates.add(source_name)
                else:
                    historical_archive_sources_without_templates.add(source_name)
                historical_archive_fetch_attempt_count += int(direct_meta.get("historical_archive_fetch_attempt_count") or 0)
                historical_archive_fetch_success_count += int(direct_meta.get("historical_archive_fetch_success_count") or 0)
                historical_archive_fetch_failure_count += int(direct_meta.get("historical_archive_fetch_failure_count") or 0)
                historical_archive_url_count += int(direct_meta.get("historical_archive_url_count") or 0)
                historical_archive_candidates_extracted_count += int(direct_meta.get("historical_archive_candidates_extracted_count") or 0)
                failure_reasons = dict(direct_meta.get("historical_archive_fetch_failure_reasons") or {})
                if failure_reasons:
                    source_counter = historical_archive_fetch_failure_reasons_by_source.setdefault(source_name, Counter())
                    for reason, count in failure_reasons.items():
                        source_counter[str(reason)] += int(count)
            if source_name:
                archive_links_rejected_by_source[source_name] = int(
                    archive_links_rejected_by_source.get(source_name, 0)
                ) + int(direct_meta.get("archive_links_rejected_count") or 0)
                archive_links_accepted_by_source[source_name] = int(
                    archive_links_accepted_by_source.get(source_name, 0)
                ) + int(direct_meta.get("archive_links_accepted_count") or 0)
                archive_links_rejected_by_reason.update(dict(direct_meta.get("archive_links_rejected_by_reason") or {}))
            if bool(direct_meta.get("historical_archive_pagination_enabled")) and source_name:
                historical_archive_pagination_source_names.add(source_name)
                historical_archive_page_fetch_attempt_count += int(direct_meta.get("historical_archive_page_fetch_attempt_count") or 0)
                historical_archive_page_fetch_success_count += int(direct_meta.get("historical_archive_page_fetch_success_count") or 0)
                historical_archive_page_fetch_failure_count += int(direct_meta.get("historical_archive_page_fetch_failure_count") or 0)
                historical_archive_pages_fetched_by_source[source_name] = int(
                    historical_archive_pages_fetched_by_source.get(source_name, 0)
                ) + len(list(direct_meta.get("historical_archive_pages_fetched") or []))
                historical_archive_links_extracted_by_source[source_name] = int(
                    historical_archive_links_extracted_by_source.get(source_name, 0)
                ) + int(direct_meta.get("historical_archive_links_extracted_count") or 0)
                historical_archive_in_window_candidates_by_source[source_name] = int(
                    historical_archive_in_window_candidates_by_source.get(source_name, 0)
                ) + int(direct_meta.get("historical_archive_in_window_candidates") or 0)
                historical_archive_duplicate_link_count_by_source[source_name] = int(
                    historical_archive_duplicate_link_count_by_source.get(source_name, 0)
                ) + int(direct_meta.get("historical_archive_duplicate_link_count") or 0)
                stop_reason = _nonempty(direct_meta.get("historical_archive_stop_reason"))
                if stop_reason:
                    historical_archive_stop_reason_by_source[source_name] = stop_reason
                if int(direct_meta.get("historical_archive_in_window_candidates") or 0) <= 0:
                    historical_archive_pagination_sources_without_hits.add(source_name)
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
            result_row["historical_archive_source_has_templates"] = bool(direct_meta.get("historical_archive_source_has_templates"))
            result_row["historical_archive_url_count"] = int(direct_meta.get("historical_archive_url_count") or 0)
            result_row["historical_archive_fetch_attempt_count"] = int(direct_meta.get("historical_archive_fetch_attempt_count") or 0)
            result_row["historical_archive_fetch_success_count"] = int(direct_meta.get("historical_archive_fetch_success_count") or 0)
            result_row["historical_archive_fetch_failure_count"] = int(direct_meta.get("historical_archive_fetch_failure_count") or 0)
            result_row["historical_archive_candidates_extracted_count"] = int(direct_meta.get("historical_archive_candidates_extracted_count") or 0)
            result_row["historical_archive_fetch_failure_reasons"] = dict(direct_meta.get("historical_archive_fetch_failure_reasons") or {})
            result_row["historical_archive_pagination_enabled"] = bool(direct_meta.get("historical_archive_pagination_enabled"))
            result_row["historical_archive_page_fetch_attempt_count"] = int(direct_meta.get("historical_archive_page_fetch_attempt_count") or 0)
            result_row["historical_archive_page_fetch_success_count"] = int(direct_meta.get("historical_archive_page_fetch_success_count") or 0)
            result_row["historical_archive_page_fetch_failure_count"] = int(direct_meta.get("historical_archive_page_fetch_failure_count") or 0)
            result_row["historical_archive_pages_fetched"] = list(direct_meta.get("historical_archive_pages_fetched") or [])
            result_row["historical_archive_links_extracted_count"] = int(direct_meta.get("historical_archive_links_extracted_count") or 0)
            result_row["historical_archive_in_window_candidates"] = int(direct_meta.get("historical_archive_in_window_candidates") or 0)
            result_row["historical_archive_stop_reason"] = _nonempty(direct_meta.get("historical_archive_stop_reason"))
            result_row["historical_archive_stop_context"] = _nonempty(direct_meta.get("historical_archive_stop_context"))
            result_row["historical_archive_duplicate_link_count"] = int(direct_meta.get("historical_archive_duplicate_link_count") or 0)
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
                    "historical_archive_source_has_templates": bool(direct_meta.get("historical_archive_source_has_templates")),
                    "historical_archive_url_count": int(direct_meta.get("historical_archive_url_count") or 0),
                    "historical_archive_fetch_attempt_count": int(direct_meta.get("historical_archive_fetch_attempt_count") or 0),
                    "historical_archive_fetch_success_count": int(direct_meta.get("historical_archive_fetch_success_count") or 0),
                    "historical_archive_fetch_failure_count": int(direct_meta.get("historical_archive_fetch_failure_count") or 0),
                    "historical_archive_candidates_extracted_count": int(direct_meta.get("historical_archive_candidates_extracted_count") or 0),
                    "historical_archive_fetch_failure_reasons": dict(direct_meta.get("historical_archive_fetch_failure_reasons") or {}),
                    "historical_archive_pagination_enabled": bool(direct_meta.get("historical_archive_pagination_enabled")),
                    "historical_archive_page_fetch_attempt_count": int(direct_meta.get("historical_archive_page_fetch_attempt_count") or 0),
                    "historical_archive_page_fetch_success_count": int(direct_meta.get("historical_archive_page_fetch_success_count") or 0),
                    "historical_archive_page_fetch_failure_count": int(direct_meta.get("historical_archive_page_fetch_failure_count") or 0),
                    "historical_archive_pages_fetched": list(direct_meta.get("historical_archive_pages_fetched") or []),
                    "historical_archive_links_extracted_count": int(direct_meta.get("historical_archive_links_extracted_count") or 0),
                    "historical_archive_in_window_candidates": int(direct_meta.get("historical_archive_in_window_candidates") or 0),
                    "historical_archive_stop_reason": _nonempty(direct_meta.get("historical_archive_stop_reason")),
                    "historical_archive_stop_context": _nonempty(direct_meta.get("historical_archive_stop_context")),
                    "historical_archive_duplicate_link_count": int(direct_meta.get("historical_archive_duplicate_link_count") or 0),
                    "source_disabled_or_skipped": False,
                    "recommended_action": _nonempty(result_row["recommended_action"]),
                }
            )
            rss_items = items
        result_row["result_count"] = len(rss_items)
        query_rows.append(result_row)
        if discovery_channel != "google_news_rss":
            ranked_direct_items: list[dict[str, Any]] = []
            source_has_in_window_item = False
            historical_prefetch_cap = 5
            for index, item in enumerate(rss_items):
                item_copy = dict(item)
                source_published_date = _extract_source_date(_nonempty(item.get("pubDate")))
                date_basis = "feed_published" if source_published_date else "missing"
                if not source_published_date:
                    source_published_date = _extract_source_date(_nonempty(item.get("updated")))
                    if source_published_date:
                        date_basis = "feed_updated"
                if (
                    not source_published_date
                    and bool(query_row.get("historical_capable"))
                    and discovery_channel == "direct_page"
                    and index < historical_prefetch_cap
                ):
                    prefetched_payload, prefetched_error, _ = _fetch_url_with_metadata(fetch, _nonempty(item.get("link")))
                    if prefetched_payload and not prefetched_error:
                        prefetched_date, prefetched_basis = _extract_page_published_date_info(
                            prefetched_payload,
                            _normalize_url(_nonempty(item.get("link"))),
                        )
                        if prefetched_date:
                            item_copy["published_date"] = prefetched_date
                            item_copy["date_basis_hint"] = prefetched_basis
                if not source_published_date:
                    hinted_date = _extract_source_date(_nonempty(item.get("published_date") or item.get("pubDate")))
                    hinted_basis = _nonempty(item.get("date_basis_hint"))
                    if not hinted_date:
                        hinted_date = _extract_source_date(_nonempty(item_copy.get("published_date")))
                        hinted_basis = _nonempty(item_copy.get("date_basis_hint"))
                    if hinted_date:
                        source_published_date = hinted_date
                        date_basis = hinted_basis or "page_body_date"
                if not source_published_date:
                    inferred_url = _normalize_url(_nonempty(item.get("link")) or _nonempty(item.get("source_url")))
                    source_published_date = _extract_url_date(inferred_url)
                    if source_published_date:
                        date_basis = "url_date"
                match_status = _date_match_status(
                    date_text,
                    source_published_date,
                    lookback_days=query_lookback_days,
                    lookahead_days=query_lookahead_days,
                )
                if match_status in {"exact_date", "within_query_window"}:
                    source_has_in_window_item = True
                item_copy["_date_match_status"] = match_status
                item_copy["_source_published_date"] = source_published_date
                item_copy["_date_basis"] = date_basis
                item_copy["_date_distance_days"] = _date_distance_days(date_text, source_published_date)
                ranked_direct_items.append(item_copy)
                if bool(query_row.get("historical_capable")) and source_name:
                    if match_status == "exact_date":
                        historical_sources_with_exact_date_items.add(source_name)
                    if date_basis == "url_date":
                        historical_sources_with_url_date_items.add(source_name)
                    if date_basis == "page_body_date":
                        historical_sources_with_page_body_date_items.add(source_name)
            ranked_direct_items.sort(
                key=lambda item: (
                    0
                    if _nonempty(item.get("archive_url_used")) and _nonempty(item.get("_date_match_status")) in {"exact_date", "within_query_window"}
                    else 1,
                    _date_match_sort_key(
                        _nonempty(item.get("_date_match_status")),
                        item.get("_date_distance_days"),
                        _nonempty(item.get("_date_basis")),
                    ),
                    int(item.get("archive_candidate_rank") or 99999),
                    int(item.get("archive_pagination_rank") or 99999),
                    _nonempty(item.get("title")).lower(),
                    _nonempty(item.get("link")),
                )
            )
            rss_items = ranked_direct_items
            if source_name:
                if source_has_in_window_item:
                    direct_sources_with_in_window_items.add(source_name)
                else:
                    direct_sources_with_no_in_window_items.add(source_name)
            broad_candidate_exists = any(not _nonempty(item.get("archive_url_used")) for item in rss_items)
            for selected_item in rss_items[:max_results_per_query]:
                selected_item["_selected_after_date_filter"] = True
                if (
                    broad_candidate_exists
                    and _nonempty(selected_item.get("archive_url_used"))
                    and _nonempty(selected_item.get("_date_match_status")) in {"exact_date", "within_query_window"}
                ):
                    historical_archive_selected_before_broad_count += 1
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
                        publisher_name=_nonempty(item.get("source_name")),
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
            title_extraction_method = ""
            raw_title_candidates: list[str] = _dedupe_texts([_nonempty(item.get("title"))])
            selected_title = _nonempty(item.get("title"))
            title_quality_status = _title_quality_status(selected_title)
            page_summary = ""
            page_text = ""
            page_published_date = ""
            page_date_basis = "missing"
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
                        page_title, title_extraction_method, raw_title_candidates, title_quality_status = _pick_best_title(
                            payload2,
                            fallback_title=_nonempty(item.get("title")),
                            publisher=_nonempty(item.get("source_name") or direct_source_name),
                        )
                        selected_title = page_title or _nonempty(item.get("title"))
                        page_summary = _page_summary(payload2)
                        page_text = _page_text(payload2)
                        page_published_date, page_date_basis = _extract_page_published_date_info(payload2, final_trace_url)
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
                    page_title, title_extraction_method, raw_title_candidates, title_quality_status = _pick_best_title(
                        payload2,
                        fallback_title=_nonempty(item.get("title")),
                        publisher=_nonempty(item.get("source_name")),
                    )
                    selected_title = page_title or _nonempty(item.get("title"))
                    page_summary = _page_summary(payload2)
                    page_text = _page_text(payload2)
                    page_published_date, page_date_basis = _extract_page_published_date_info(payload2, final_trace_url)
                    if _is_article_specific_url(canonical_from_page):
                        canonical = canonical_from_page
                        if not _is_article_specific_url(final_trace_url):
                            final_trace_url = canonical_from_page
                    elif _is_homepage_or_landing_url(canonical_from_page) and _is_article_specific_url(final_trace_url):
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
            source_published_date = _nonempty(item.get("_source_published_date"))
            date_basis = _nonempty(item.get("_date_basis")) or "missing"
            if not source_published_date:
                source_published_date = _extract_source_date(_nonempty(item.get("pubDate")))
                date_basis = "feed_published" if source_published_date else "missing"
            if not source_published_date:
                source_published_date = _extract_source_date(_nonempty(item.get("updated")))
                if source_published_date:
                    date_basis = "feed_updated"
            if not source_published_date:
                inferred_url = _extract_url_date(final_trace_url or publisher_url or discovered_url)
                if inferred_url:
                    source_published_date = inferred_url
                    date_basis = "url_date"
            if page_published_date and (
                not source_published_date or _date_basis_rank(page_date_basis) < _date_basis_rank(date_basis)
            ):
                source_published_date = page_published_date
                date_basis = page_date_basis
            elif not source_published_date:
                source_published_date = page_published_date
                date_basis = page_date_basis if source_published_date else "missing"
            date_distance_days = _date_distance_days(date_text, source_published_date)
            date_match_status = _date_match_status(
                date_text,
                source_published_date,
                lookback_days=query_lookback_days,
                lookahead_days=query_lookahead_days,
            )
            if discovery_channel != "google_news_rss" and direct_source_name and date_match_status in {"exact_date", "within_query_window"}:
                direct_sources_with_in_window_items.add(direct_source_name)
                direct_sources_with_no_in_window_items.discard(direct_source_name)
            archive_url_used = _nonempty(item.get("archive_url_used"))
            archive_template_used = _nonempty(item.get("archive_template_used"))
            archive_granularity = _nonempty(item.get("archive_granularity"))
            archive_target_date = _nonempty(item.get("archive_target_date"))
            archive_candidate_rank = int(item.get("archive_candidate_rank") or 0)
            archive_page_url_used = _nonempty(item.get("archive_page_url_used"))
            archive_page_number = int(item.get("archive_page_number") or 0)
            archive_pagination_rank = int(item.get("archive_pagination_rank") or 0)
            archive_stop_context = _nonempty(item.get("archive_stop_context") or direct_meta.get("historical_archive_stop_context"))
            selected_after_date_filter = bool(item.get("_selected_after_date_filter")) or discovery_channel == "google_news_rss"
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
                source_url=source_url,
                original_source_url=final_trace_url or publisher_url,
                title_quality_status=title_quality_status,
            )
            row = {
                "candidate_id": _candidate_id(final_trace_url or discovered_url, _nonempty(item.get("source_name") or item.get("source_url")), _nonempty(query_row.get("query_family"))),
                "discovery_date": date_text,
                "discovered_at": discovered_at,
                "discovery_lane": lane,
                "discovery_query": query_text,
                "discovery_source_type": _discovery_source_type(_nonempty(query_row.get("query_family")), discovered_url, publisher_url, fetch_status),
                "query_family": _nonempty(query_row.get("query_family")),
                "source_family": _nonempty(query_row.get("source_family")),
                "query_text": query_text,
                "geographic_scope": _nonempty(query_row.get("geographic_scope")),
                "state_or_territory": _nonempty(query_row.get("state_or_territory")),
                "state_abbrev": _nonempty(query_row.get("state_abbrev")),
                "state_hint": _nonempty(query_row.get("state_abbrev") or query_row.get("state_or_territory")),
                "metro": _nonempty(query_row.get("metro")),
                "discovery_channel": discovery_channel,
                "discovered_title": selected_title,
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
                "target_date": date_text,
                "source_published_date": source_published_date,
                "date_distance_days": date_distance_days,
                "date_match_status": date_match_status,
                "date_basis": date_basis,
                "selected_after_date_filter": selected_after_date_filter,
                "archive_url_used": archive_url_used,
                "archive_template_used": archive_template_used,
                "archive_granularity": archive_granularity,
                "archive_target_date": archive_target_date,
                "archive_candidate_rank": archive_candidate_rank,
                "archive_page_url_used": archive_page_url_used,
                "archive_page_number": archive_page_number,
                "archive_pagination_rank": archive_pagination_rank,
                "archive_stop_context": archive_stop_context,
                "archive_link_filter_status": _nonempty(item.get("archive_link_filter_status")),
                "archive_link_filter_reason": _nonempty(item.get("archive_link_filter_reason")),
                "archive_source_anchor_text": _nonempty(item.get("archive_source_anchor_text")),
                "archive_source_link_context": _cap_text(_nonempty(item.get("archive_source_link_context"))),
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
                "pressure_signal": False,
                "pressure_type": "",
                "pressure_summary": "",
                "traceability_status": traceability_status,
                "public_claim_eligible": public_claim_eligible,
                "public_claim_blockers": public_claim_blockers,
                "title_extraction_method": title_extraction_method or ("feed_or_listing_title" if selected_title else ""),
                "raw_title_candidates": raw_title_candidates,
                "selected_title": selected_title,
                "title_quality_status": title_quality_status,
                "title_quality_blocker_applied": title_quality_status in {"generic_or_invalid_title", "missing_title"},
                "affected_groups": [],
                "evidence_level": "",
                "freshness_role": "",
                "source_role": "",
                "summary_or_snippet": _nonempty(page_summary or item.get("description")),
                "evidence_text": _cap_text(evidence_text, limit=1200),
                "evidence_text_basis": "page_text_excerpt" if fetch_status == "ok" else ("rss_item_text" if discovery_channel in {"direct_rss", "google_news_rss"} else "manual_source_text"),
                "missing_public_prose_fields": [],
                "query_url": query_url,
                "retrieved_at": discovered_at,
                "_google_news_resolution_attempted": google_news_resolution_attempted,
                "_google_news_resolution_error": google_news_resolution_error,
                "_google_news_resolved_url": resolved_wrapper_url,
                "_google_news_resolution_status": google_news_resolution_status,
            }
            if archive_url_used and direct_source_name:
                historical_archive_candidates_by_source[direct_source_name] = historical_archive_candidates_by_source.get(direct_source_name, 0) + 1
                if date_match_status == "exact_date":
                    historical_archive_exact_date_candidates_by_source[direct_source_name] = (
                        historical_archive_exact_date_candidates_by_source.get(direct_source_name, 0) + 1
                    )
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
                    "decoded_google_news_url": _nonempty(google_news_debug.get("decoded_google_news_url")),
                    "google_news_rpc_url": _nonempty(google_news_debug.get("google_news_rpc_url")),
                    "redirect_url_found": _nonempty(google_news_debug.get("redirect_url_found")),
                    "canonical_url_found": _nonempty(google_news_debug.get("canonical_url_found")),
                    "html_candidate_url_found": _nonempty(google_news_debug.get("html_candidate_url_found")),
                    "google_news_article_id": _nonempty(google_news_debug.get("google_news_article_id")),
                    "google_news_rpc_attempted": bool(google_news_debug.get("google_news_rpc_attempted")),
                    "google_news_rpc_error": _nonempty(google_news_debug.get("google_news_rpc_error")),
                    "static_or_google_noise_only": bool(google_news_debug.get("static_or_google_noise_only")),
                    "fallback_to_publisher_homepage": bool(google_news_debug.get("fallback_to_publisher_homepage")),
                    "rejection_reason": _nonempty(google_news_debug.get("rejection_reason")),
                    "rejected_candidate_urls_sample": list(google_news_debug.get("rejected_candidate_urls_sample") or []),
                    "rejected_candidate_urls_sample_limit": int(
                        google_news_debug.get("rejected_candidate_urls_sample_limit") or GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT
                    ),
                    "rejected_candidate_urls_sample_truncated": bool(
                        google_news_debug.get("rejected_candidate_urls_sample_truncated")
                    ),
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
            if discovery_channel != "google_news_rss":
                direct_candidates_by_date_match_status[date_match_status] += 1
                direct_candidates_by_date_basis[date_basis] += 1
                if date_match_status == "missing_date":
                    missing_date_direct_candidate_count += 1
                elif date_match_status in {"exact_date", "within_query_window"}:
                    in_window_direct_candidate_count += 1
                else:
                    out_of_window_direct_candidate_count += 1
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
            source_url=_nonempty(row.get("source_url") or row.get("final_trace_url")),
            original_source_url=_nonempty(row.get("original_source_url") or row.get("final_trace_url")),
            title_quality_status=_nonempty(row.get("title_quality_status")),
        )
        if row.get("classification_status") == "manual_fallback":
            row["candidate_review_status"] = "needs_review"
        _apply_public_readiness_gate(row, edition_date=date_text)
        row["title_quality_blocker_applied"] = "generic_or_invalid_title" in list(row.get("public_claim_blockers") or [])
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
    generic_or_invalid_title_count = sum(1 for row in candidates if _nonempty(row.get("title_quality_status")) == "generic_or_invalid_title")
    missing_title_count = sum(1 for row in candidates if _nonempty(row.get("title_quality_status")) == "missing_title")
    missing_public_prose_fields_count = sum(1 for row in candidates if list(row.get("missing_public_prose_fields") or []))
    missing_public_prose_fields_by_field = dict(
        sorted(
            Counter(
                field
                for row in candidates
                for field in list(row.get("missing_public_prose_fields") or [])
                if _nonempty(field)
            ).items()
        )
    )
    public_prose_derivation_status_counts = dict(
        sorted(
            Counter(
                _nonempty(row.get("public_prose_derivation_status")) or "insufficient_source_support"
                for row in candidates
            ).items()
        )
    )
    pressure_summary_derivation_status_counts = dict(
        sorted(
            Counter(
                _nonempty(row.get("pressure_summary_derivation_status")) or "insufficient_source_support"
                for row in candidates
            ).items()
        )
    )
    pressure_type_derivation_status_counts = dict(
        sorted(
            Counter(
                _nonempty(row.get("pressure_type_derivation_status")) or "insufficient_source_support"
                for row in candidates
            ).items()
        )
    )
    source_role_derivation_status_counts = dict(
        sorted(
            Counter(
                _nonempty(row.get("source_role_derivation_status")) or "insufficient_source_support"
                for row in candidates
            ).items()
        )
    )
    source_role_counts = dict(
        sorted(
            Counter(_nonempty(row.get("source_role")) for row in candidates if _nonempty(row.get("source_role"))).items()
        )
    )
    public_eligible_blocked_by_title_count = sum(
        1
        for row in candidates
        if "generic_or_invalid_title" in list(row.get("public_claim_blockers") or []) and not bool(row.get("public_claim_eligible"))
    )
    public_eligible_blocked_by_missing_public_prose_count = sum(
        1
        for row in candidates
        if "missing_public_prose_fields" in list(row.get("public_claim_blockers") or []) and not bool(row.get("public_claim_eligible"))
    )
    title_extraction_methods = dict(
        sorted(
            Counter(_nonempty(row.get("title_extraction_method")) for row in candidates if _nonempty(row.get("title_extraction_method"))).items()
        )
    )
    fetchable_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) == "ok")
    manual_reviewable_count = sum(1 for row in candidates if bool(row.get("manual_review_required")))
    google_news_url_count = sum(1 for row in candidates if _nonempty(row.get("google_news_url")))
    google_news_resolution_attempt_count = sum(int(count) for status, count in resolution_status_counts.items())
    google_news_resolution_success_count = sum(
        int(count) for status, count in resolution_status_counts.items() if _nonempty(status).startswith("resolved_")
    )
    google_news_resolution_failure_count = google_news_resolution_attempt_count - google_news_resolution_success_count
    google_news_resolved_article_url_count = google_news_resolution_success_count
    google_news_resolved_homepage_only_count = int(resolution_status_counts.get("failed_homepage_or_landing_url", 0)) + int(
        resolution_status_counts.get("failed_listing_or_action_url", 0)
    )
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
        "generic_or_invalid_title_count": generic_or_invalid_title_count,
        "missing_title_count": missing_title_count,
        "missing_public_prose_fields_count": missing_public_prose_fields_count,
        "missing_public_prose_fields_by_field": missing_public_prose_fields_by_field,
        "public_prose_derivation_status_counts": public_prose_derivation_status_counts,
        "pressure_summary_derivation_status_counts": pressure_summary_derivation_status_counts,
        "pressure_type_derivation_status_counts": pressure_type_derivation_status_counts,
        "source_role_derivation_status_counts": source_role_derivation_status_counts,
        "source_role_counts": source_role_counts,
        "title_extraction_methods": title_extraction_methods,
        "public_eligible_blocked_by_title_count": public_eligible_blocked_by_title_count,
        "public_eligible_blocked_by_missing_public_prose_count": public_eligible_blocked_by_missing_public_prose_count,
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
        "in_window_direct_candidate_count": in_window_direct_candidate_count,
        "out_of_window_direct_candidate_count": out_of_window_direct_candidate_count,
        "missing_date_direct_candidate_count": missing_date_direct_candidate_count,
        "direct_candidates_by_date_match_status": dict(sorted(direct_candidates_by_date_match_status.items())),
        "direct_candidates_by_date_basis": dict(sorted(direct_candidates_by_date_basis.items())),
        "direct_sources_with_no_in_window_items": sorted(direct_sources_with_no_in_window_items),
        "direct_sources_with_in_window_items": sorted(direct_sources_with_in_window_items),
        "historical_source_count": len(historical_source_names),
        "historical_sources": sorted(historical_source_names),
        "historical_sources_with_exact_date_items": sorted(historical_sources_with_exact_date_items),
        "historical_sources_with_url_date_items": sorted(historical_sources_with_url_date_items),
        "historical_sources_with_page_body_date_items": sorted(historical_sources_with_page_body_date_items),
        "historical_archive_source_count": len(historical_archive_source_names),
        "historical_archive_fetch_attempt_count": historical_archive_fetch_attempt_count,
        "historical_archive_fetch_success_count": historical_archive_fetch_success_count,
        "historical_archive_fetch_failure_count": historical_archive_fetch_failure_count,
        "historical_archive_url_count": historical_archive_url_count,
        "historical_archive_candidates_extracted_count": historical_archive_candidates_extracted_count,
        "historical_archive_sources_with_templates": sorted(historical_archive_sources_with_templates),
        "historical_archive_sources_without_templates": sorted(historical_archive_sources_without_templates),
        "historical_archive_fetch_failure_reasons_by_source": {
            key: dict(sorted(value.items()))
            for key, value in sorted(historical_archive_fetch_failure_reasons_by_source.items())
        },
        "historical_archive_candidates_by_source": dict(sorted(historical_archive_candidates_by_source.items())),
        "historical_archive_exact_date_candidates_by_source": dict(sorted(historical_archive_exact_date_candidates_by_source.items())),
        "historical_archive_selected_before_broad_count": historical_archive_selected_before_broad_count,
        "historical_archive_pagination_source_count": len(historical_archive_pagination_source_names),
        "historical_archive_page_fetch_attempt_count": historical_archive_page_fetch_attempt_count,
        "historical_archive_page_fetch_success_count": historical_archive_page_fetch_success_count,
        "historical_archive_page_fetch_failure_count": historical_archive_page_fetch_failure_count,
        "historical_archive_pages_fetched_by_source": dict(sorted(historical_archive_pages_fetched_by_source.items())),
        "historical_archive_links_extracted_by_source": dict(sorted(historical_archive_links_extracted_by_source.items())),
        "archive_links_rejected_count": sum(archive_links_rejected_by_source.values()),
        "archive_links_rejected_by_reason": dict(sorted(archive_links_rejected_by_reason.items())),
        "archive_links_accepted_count": sum(archive_links_accepted_by_source.values()),
        "archive_links_rejected_by_source": dict(sorted(archive_links_rejected_by_source.items())),
        "archive_links_accepted_by_source": dict(sorted(archive_links_accepted_by_source.items())),
        "historical_archive_in_window_candidates_by_source": dict(sorted(historical_archive_in_window_candidates_by_source.items())),
        "historical_archive_stop_reason_by_source": dict(sorted(historical_archive_stop_reason_by_source.items())),
        "historical_archive_duplicate_link_count_by_source": dict(sorted(historical_archive_duplicate_link_count_by_source.items())),
        "historical_archive_pagination_sources_without_hits": sorted(historical_archive_pagination_sources_without_hits),
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
        "candidates": candidates,
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
            f"- Generic or invalid titles: `{generic_or_invalid_title_count}`",
            f"- Missing titles: `{missing_title_count}`",
            f"- Candidates missing public prose fields: `{missing_public_prose_fields_count}`",
            f"- Public-eligible blocked by title: `{public_eligible_blocked_by_title_count}`",
            f"- Public-eligible blocked by missing public prose: `{public_eligible_blocked_by_missing_public_prose_count}`",
            f"- Title extraction methods: `{title_extraction_methods}`",
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
            f"- Historical direct sources: `{audit_summary['historical_sources']}`",
            f"- Historical direct sources with exact-date items: `{audit_summary['historical_sources_with_exact_date_items']}`",
            f"- Historical direct sources with URL-date items: `{audit_summary['historical_sources_with_url_date_items']}`",
            f"- Historical direct sources with page-body-date items: `{audit_summary['historical_sources_with_page_body_date_items']}`",
            f"- Historical archive sources with templates: `{audit_summary['historical_archive_sources_with_templates']}`",
            f"- Historical archive sources without templates: `{audit_summary['historical_archive_sources_without_templates']}`",
            f"- Historical archive fetch attempts: `{audit_summary['historical_archive_fetch_attempt_count']}`",
            f"- Historical archive fetch successes: `{audit_summary['historical_archive_fetch_success_count']}`",
            f"- Historical archive fetch failures: `{audit_summary['historical_archive_fetch_failure_count']}`",
            f"- Historical archive URLs rendered: `{audit_summary['historical_archive_url_count']}`",
            f"- Historical archive candidates extracted: `{audit_summary['historical_archive_candidates_extracted_count']}`",
            f"- Historical archive failures by source: `{audit_summary['historical_archive_fetch_failure_reasons_by_source']}`",
            f"- Historical archive candidates by source: `{audit_summary['historical_archive_candidates_by_source']}`",
            f"- Historical archive exact-date candidates by source: `{audit_summary['historical_archive_exact_date_candidates_by_source']}`",
            f"- Historical archive selections before broad: `{audit_summary['historical_archive_selected_before_broad_count']}`",
            f"- Historical archive pagination source count: `{audit_summary['historical_archive_pagination_source_count']}`",
            f"- Historical archive page fetch attempts: `{audit_summary['historical_archive_page_fetch_attempt_count']}`",
            f"- Historical archive page fetch successes: `{audit_summary['historical_archive_page_fetch_success_count']}`",
            f"- Historical archive page fetch failures: `{audit_summary['historical_archive_page_fetch_failure_count']}`",
            f"- Historical archive pages fetched by source: `{audit_summary['historical_archive_pages_fetched_by_source']}`",
            f"- Historical archive links extracted by source: `{audit_summary['historical_archive_links_extracted_by_source']}`",
            f"- Archive links rejected count: `{audit_summary['archive_links_rejected_count']}`",
            f"- Archive links rejected by reason: `{audit_summary['archive_links_rejected_by_reason']}`",
            f"- Archive links accepted count: `{audit_summary['archive_links_accepted_count']}`",
            f"- Archive links rejected by source: `{audit_summary['archive_links_rejected_by_source']}`",
            f"- Archive links accepted by source: `{audit_summary['archive_links_accepted_by_source']}`",
            f"- Historical archive in-window candidates by source: `{audit_summary['historical_archive_in_window_candidates_by_source']}`",
            f"- Historical archive stop reason by source: `{audit_summary['historical_archive_stop_reason_by_source']}`",
            f"- Historical archive duplicate link count by source: `{audit_summary['historical_archive_duplicate_link_count_by_source']}`",
            f"- Historical archive pagination sources without hits: `{audit_summary['historical_archive_pagination_sources_without_hits']}`",
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
    if export_agent_inbox:
        if dry_run:
            agent_export_result = {
                "status": "dry_run_no_write",
                "finding_count": sum(1 for row in candidates if bool(row.get("public_claim_eligible"))),
                "excluded_count": sum(1 for row in candidates if not bool(row.get("public_claim_eligible"))),
            }
        else:
            agent_export_result = export_food_line_agent_run(
                candidates,
                edition_date=date_text,
                destination=agent_inbox_dir or root / "status" / "food-line" / "runtime" / "agent-inbox",
                started_at=discovered_at,
                completed_at=_utc_now(),
                coverage_notes=(
                    f"Food Line source watch checked {len(query_rows)} query/source rows for {date_text}; "
                    f"discovery candidates={len(candidates)}."
                ),
            )
        audit_summary["agent_inbox_export"] = agent_export_result
        if not dry_run:
            _write_json(audit_json_path, audit_summary)
    if include_candidate_records:
        audit_summary["_candidate_records"] = candidates
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
    parser.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--manual-fallback-file", help="Optional JSON list of manual fallback records.")
    parser.add_argument("--edition-mode", default="current_update", choices=("current_update", "no_current_update"))
    parser.add_argument("--max-results-per-query", type=int, default=None)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--query-lookback-days", type=int, default=None)
    parser.add_argument("--query-lookahead-days", type=int, default=None)
    parser.add_argument("--public-claim-lookback-days", type=int, default=None)
    parser.add_argument("--public-claim-lookahead-days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--export-agent-inbox", action="store_true", help="Write one private food_line_agent_run_v1 envelope after discovery.")
    parser.add_argument("--agent-inbox-dir", default="status/food-line/runtime/agent-inbox", help="Private agent inbox destination.")
    parser.add_argument("--profile", choices=("daily-current", "supplemental", "smoke"), help="Use durable bounded execution.")
    parser.add_argument("--run-id", help="Explicit ID for a new bounded run.")
    parser.add_argument("--resume-run", help="Resume an existing bounded run ID.")
    parser.add_argument("--status-run", help="Inspect an existing bounded run ID without collection.")
    parser.add_argument("--legacy-unbounded", action="store_true", help="Explicit compatibility mode; not for production.")
    parser.add_argument("--priority-tier", action="append", choices=("tier1", "tier2", "tier3"), help="Tier to execute; repeat as needed.")
    parser.add_argument("--max-run-minutes", type=float)
    parser.add_argument("--max-partition-minutes", type=float)
    parser.add_argument("--max-query-seconds", type=float)
    parser.add_argument("--per-request-timeout-seconds", type=int)
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--partition-size", type=int)
    parser.add_argument("--max-partitions", type=int)
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--progress-interval-seconds", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.status_run:
        from bluefern_dispatches.food_line_bounded_discovery import inspect_bounded_run

        result = inspect_bounded_run(root, args.status_run, args.date)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    bounded = not bool(args.legacy_unbounded)
    if bounded:
        from bluefern_dispatches.food_line_bounded_discovery import run_bounded_food_line_discovery

        if args.manual_fallback_file or args.dry_run:
            parser.error("manual fallback and legacy dry-run require --legacy-unbounded")
        agent_inbox = Path(args.agent_inbox_dir)
        if not agent_inbox.is_absolute():
            agent_inbox = root / agent_inbox
        result = run_bounded_food_line_discovery(
            root,
            args.date,
            profile=args.profile or "daily-current",
            run_id=args.run_id,
            resume_run=args.resume_run,
            priority_tiers=args.priority_tier,
            export_agent_inbox=bool(args.export_agent_inbox),
            agent_inbox_dir=agent_inbox.resolve(),
            max_partitions=args.max_partitions,
            overrides={
                "max_run_minutes": args.max_run_minutes,
                "max_partition_minutes": args.max_partition_minutes,
                "max_query_seconds": args.max_query_seconds,
                "per_request_timeout_seconds": args.per_request_timeout_seconds,
                "max_retries": args.max_retries,
                "partition_size": args.partition_size,
                "max_workers": args.max_workers,
                "progress_interval_seconds": args.progress_interval_seconds,
                "max_results_per_query": args.max_results_per_query,
                "query_lookback_days": args.query_lookback_days,
                "query_lookahead_days": args.query_lookahead_days,
                "public_claim_lookback_days": args.public_claim_lookback_days,
                "public_claim_lookahead_days": args.public_claim_lookahead_days,
            },
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") in {"completed", "completed_with_exclusions"} else 2
    if not args.date:
        parser.error("--date is required unless --status-run is used")
    manual_path = Path(args.manual_fallback_file).resolve() if args.manual_fallback_file else None
    result = run_food_line_discovery_expansion(
        root,
        args.date,
        manual_fallback_path=manual_path,
        edition_mode=args.edition_mode,
        max_results_per_query=args.max_results_per_query or 10,
        max_queries=args.max_queries,
        query_lookback_days=args.query_lookback_days if args.query_lookback_days is not None else 1,
        query_lookahead_days=args.query_lookahead_days if args.query_lookahead_days is not None else 1,
        public_claim_lookback_days=args.public_claim_lookback_days if args.public_claim_lookback_days is not None else 0,
        public_claim_lookahead_days=args.public_claim_lookahead_days if args.public_claim_lookahead_days is not None else 0,
        dry_run=bool(args.dry_run),
        export_agent_inbox=bool(args.export_agent_inbox),
        agent_inbox_dir=(root / args.agent_inbox_dir).resolve() if not Path(args.agent_inbox_dir).is_absolute() else Path(args.agent_inbox_dir).resolve(),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
