from __future__ import annotations

__test__ = False

import argparse
import csv
import hashlib
import html
import json
import re
import socket
import ssl
import sys
import time
from functools import lru_cache
from http.client import IncompleteRead
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_sources import (  # noqa: E402
    DEFAULT_AFFECTED_GROUP_KEYWORDS,
    DEFAULT_NEGATIVE_KEYWORDS,
    INVALID_XML_ENTITY_RE,
    CURRENT_PRESSURE_EVIDENCE_TERMS,
    DISCOVERY_CONTEXT_TERMS,
    SOURCE_PURPOSE_DONATION_TERMS,
    SOURCE_PURPOSE_EVERGREEN_TERMS,
    SOURCE_PURPOSE_RESOURCE_TERMS,
    _extract_page_evidence,
    _extract_page_metadata_date,
    _fetch,
    _normalize_source_text,
    _parse_rss_items,
    canonical_url,
    classify_food_line_source_purpose,
    load_food_line_candidate_registry,
    load_food_line_registry,
    load_food_line_source_performance_history,
    resolve_food_line_fetcher,
    validate_date,
)

STATES = ["WA", "OR", "ID", "CA", "TX", "FL", "NY", "PA", "OH", "MS", "KY", "SC"]
VALID_SOURCE_TYPES = {"rss", "page", "api"}
VALID_STATUSES = {"candidate", "tested_good", "tested_weak", "tested_failed", "enabled", "rejected", "promoted"}
PRESSURE_TERMS = list(dict.fromkeys([*DISCOVERY_CONTEXT_TERMS, *CURRENT_PRESSURE_EVIDENCE_TERMS]))
NEGATIVE_TERMS = [
    "recipe",
    "restaurant",
    "menu",
    "festival",
    "gala",
    "chef",
    "cooking",
    "donation drive",
    "volunteer",
]
DATE_BOUNDED_QUERY_ROOTS = (
    ('"food insecurity"', "local_news", "date_bounded"),
    ('"food banks"', "local_news", "date_bounded"),
    ('"food pantries"', "food_bank_provider", "date_bounded"),
    ('"pantry demand"', "food_bank_provider", "date_bounded"),
    ('"food pantries" "increased need"', "food_bank_provider", "date_bounded"),
    ('"food bank" "increased demand"', "food_bank_provider", "date_bounded"),
    ('"families turn to food banks"', "local_news", "date_bounded"),
    ('"food stamps" OR "SNAP cuts" OR "SNAP benefits" OR "SNAP rolls"', "state_policy_news", "date_bounded"),
    ('"SNAP" "food insecurity"', "state_policy_news", "date_bounded"),
    ('"food distribution sites" OR "hunger relief" OR "emergency food assistance"', "nonprofit_news", "date_bounded"),
    ('"meal sites" OR "summer meals"', "school_meals_child_nutrition", "date_bounded"),
    ('"food donations" "food insecurity"', "nonprofit_news", "date_bounded"),
    ('"school meals" "food pantry"', "school_meals_child_nutrition", "date_bounded"),
    ('"summer meals" "food bank"', "school_meals_child_nutrition", "date_bounded"),
    ('"food pantry" "working families"', "local_news", "date_bounded"),
    ('"rural" "food pantry" "food insecurity"', "public_radio", "date_bounded"),
)
GAP_QUERY_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
GAP_TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
GAP_RESOURCE_ONLY_TERMS = (
    "food drive",
    "fill-a-bus",
    "food for fines",
    "stock the shelves",
    "stock-the-shelves",
    "donate",
    "donations",
    "fundraiser",
    "charity drive",
    "team up",
    "launches campaign",
    "food distribution schedule",
    "where families can find food",
    "where families can get food",
    "find food",
    "find a food bank",
    "free meals",
    "get help",
    "apply for benefits",
    "hours",
    "locations",
    "food pantry locator",
)
GAP_DIRECT_PRESSURE_TERMS = (
    "record demand",
    "demand is rising",
    "rising demand",
    "demand rising",
    "demand continues to rise",
    "demand surges",
    "surge in demand",
    "higher demand",
    "empty shelves",
    "shelves are bare",
    "low inventory",
    "critical shortage",
    "temporarily closes",
    "can't keep food on the shelf",
    "cannot keep food on the shelf",
    "snap benefits were halted",
    "snap benefits halted",
    "snap cuts",
    "snap reductions",
    "benefits were halted",
    "benefit reductions",
    "fuel costs",
    "deliveries affected",
    "meals reduced",
    "increased need",
    "food insecurity is rising",
    "food insecurity percent",
    "food insecurity percentage",
)


def _gap_wrapper_kind(candidate: dict[str, Any]) -> str:
    text = _gap_text_blob(candidate)
    donation_terms = (
        *SOURCE_PURPOSE_DONATION_TERMS,
        "food drive",
        "fill-a-bus",
        "stock the shelves",
        "stock-the-shelves",
        "charity drive",
        "team up",
        "launches campaign",
    )
    if any(term in text for term in donation_terms):
        return "donation_page"
    if any(term in text for term in SOURCE_PURPOSE_RESOURCE_TERMS):
        return "resource_page"
    if any(term in text for term in SOURCE_PURPOSE_EVERGREEN_TERMS):
        return "evergreen_context"
    if any(term in text for term in ("our programs", "program description", "programs and services", "eligibility", "how to apply")):
        return "program_description"
    return ""


def _gap_entity_phrases(candidate: dict[str, Any]) -> list[str]:
    text = _normalize_source_text(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("source_name") or ""),
                str(candidate.get("publisher") or ""),
                str(candidate.get("location_name") or ""),
                str(candidate.get("county_name") or ""),
                str(candidate.get("state") or ""),
            )
            if part
        ),
        limit=700,
    )
    if not text:
        return []
    phrases: list[str] = []
    pattern = re.compile(r"\b(?:[A-Z][\w&.'-]*|[A-Z]{2,})(?:\s+(?:[A-Z][\w&.'-]*|[A-Z]{2,})){1,5}\b")
    for match in pattern.finditer(text):
        phrase = _normalize_source_text(match.group(0))
        lowered = phrase.lower()
        if not phrase or len(phrase) < 4:
            continue
        if lowered in GAP_WRAPPER_GENERIC_PHRASES:
            continue
        tokens = [token for token in re.findall(r"[a-z0-9]+", lowered) if token]
        if tokens and all(token in GAP_WRAPPER_ENTITY_STOPWORDS for token in tokens):
            continue
        if phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _gap_secondary_queries_from_wrapper(candidate: dict[str, Any]) -> list[str]:
    wrapper_kind = _gap_wrapper_kind(candidate)
    if not wrapper_kind:
        return []
    text = _gap_text_blob(candidate)
    pressure_hits = _gap_direct_pressure_hits(text)
    if not pressure_hits:
        pressure_hits = _gap_relevance_terms(text)
    if not pressure_hits:
        pressure_hits = ["food insecurity", "food bank demand", "empty shelves"]
    entities = _gap_entity_phrases(candidate)
    location_phrases: list[str] = []
    for part in (
        candidate.get("location_name"),
        candidate.get("county_name"),
        candidate.get("state"),
    ):
        phrase = _normalize_source_text(str(part or ""))
        if phrase and phrase.lower() != "none" and phrase not in location_phrases:
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
            queries.append(f'"{lead}" food bank demand')
    elif wrapper_kind == "resource_page":
        lead = location_phrases[0] if location_phrases else _normalize_source_text(str(candidate.get("publisher") or candidate.get("source_name") or ""))
        if lead:
            queries.append(f'"{lead}" food pantry demand')
    elif wrapper_kind == "evergreen_context":
        lead = entities[0] if entities else _normalize_source_text(str(candidate.get("source_name") or candidate.get("publisher") or ""))
        if lead:
            queries.append(f'"{lead}" food insecurity')
    elif wrapper_kind == "program_description":
        lead = entities[0] if entities else _normalize_source_text(str(candidate.get("source_name") or candidate.get("publisher") or ""))
        if lead:
            queries.append(f'"{lead}" hunger relief')
    return list(dict.fromkeys(query for query in queries if query))

GAP_WRAPPER_ENTITY_STOPWORDS = {
    "donate",
    "donation",
    "donations",
    "fundraiser",
    "campaign",
    "resource",
    "resources",
    "find",
    "help",
    "food",
    "bank",
    "pantry",
    "pantries",
    "shelves",
    "monthly",
    "recurring",
    "giving",
    "program",
    "programs",
    "hunger",
    "relief",
    "summer",
    "meals",
    "meal",
    "drive",
    "stock",
    "shelves",
    "community",
    "news",
    "report",
    "story",
}

GAP_WRAPPER_GENERIC_PHRASES = {
    "donate now",
    "monthly giving",
    "recurring donations",
    "ways to give",
    "find food",
    "find a food bank",
    "get help",
    "apply for benefits",
    "food distribution schedule",
    "food pantry locator",
    "our programs",
    "hunger facts",
    "hunger and poverty",
    "hunger & poverty",
    "research overview",
    "issue explainer",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    return [row for row in payload if isinstance(row, dict)]


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return canonical_url(value)
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def _discovery_queries_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"


def _discovery_blocklist_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_blocklist.json"


def _discovery_priority_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json"


def _query_metrics_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_query_performance.json"


def _load_food_line_registry_rows(root: Path) -> list[dict[str, Any]]:
    registry_dir = root / "data" / "dispatches" / "food-line"
    paths = [
        registry_dir / "source_registry.json",
        registry_dir / "pressure_source_registry.json",
        registry_dir / "candidate_source_registry.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            rows.extend(_read_json_list(path))
    return rows


def load_food_line_source_discovery_queries(root: Path) -> list[dict[str, Any]]:
    path = _discovery_queries_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"
    if not path.exists():
        path = repo_path
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        template = _nonempty(row.get("query_template") or row.get("template"))
        if not template:
            continue
        normalized.append(
            {
                "template": template,
                "query_template": template,
                "category": _nonempty(row.get("category")),
                "source_family": _nonempty(row.get("source_family")),
                "runs": int(row.get("runs") or 0),
                "candidates_found": int(row.get("candidates_found") or 0),
                "candidates_inserted": int(row.get("candidates_inserted") or 0),
                "candidates_promoted": int(row.get("candidates_promoted") or 0),
                "candidates_verified_pressure": int(row.get("candidates_verified_pressure") or 0),
                "rejects": int(row.get("rejects") or 0),
                "rolling_query_quality_score": float(row.get("rolling_query_quality_score") or 0),
            }
        )
    return normalized


def _load_discovery_query_rows(root: Path) -> list[dict[str, Any]]:
    path = _discovery_queries_path(root)
    if not path.exists():
        repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"
        if not repo_path.exists():
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(repo_path.read_text(encoding="utf-8"), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        template = _nonempty(row.get("query_template") or row.get("template"))
        if not template:
            continue
        normalized.append(
            {
                "template": template,
                "query_template": template,
                "category": _nonempty(row.get("category")),
                "source_family": _nonempty(row.get("source_family")),
                "runs": int(row.get("runs") or 0),
                "candidates_found": int(row.get("candidates_found") or 0),
                "candidates_inserted": int(row.get("candidates_inserted") or 0),
                "candidates_promoted": int(row.get("candidates_promoted") or 0),
                "candidates_verified_pressure": int(row.get("candidates_verified_pressure") or 0),
                "rejects": int(row.get("rejects") or 0),
                "rolling_query_quality_score": float(row.get("rolling_query_quality_score") or 0),
            }
        )
    return normalized


def _save_discovery_query_rows(root: Path, rows: list[dict[str, Any]]) -> Path:
    path = _discovery_queries_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _discovery_gap_queries_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json"


def load_food_line_discovery_gap_queries(root: Path) -> dict[str, Any]:
    path = _discovery_gap_queries_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json"
    if not path.exists():
        path = repo_path
    if not path.exists():
        return {"queries": [], "exclude_domains": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    queries = [str(item).strip() for item in payload.get("queries") or [] if str(item).strip()]
    exclude_domains = [str(item).strip().lower() for item in payload.get("exclude_domains") or [] if str(item).strip()]
    return {"queries": queries, "exclude_domains": exclude_domains}


def _gap_query_url(query: str) -> str:
    return GAP_QUERY_RSS_TEMPLATE.format(query=urllib.parse.quote_plus(str(query or "").strip()))


def _gap_domain(url: str) -> str:
    try:
        return urllib.parse.urlsplit(str(url or "").strip()).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _gap_normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(_normalize_url(value))
    query_items = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in GAP_TRACKING_QUERY_PARAMS and not key.lower().startswith("utm_")
    ]
    cleaned_query = urllib.parse.urlencode(query_items, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), cleaned_query, ""))


def _gap_extract_article_url_candidates(text: str) -> list[str]:
    if not text:
        return []
    patterns = (
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*name=["\']twitter:url["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*property=["\']article:url["\'][^>]*content=["\']([^"\']+)["\']',
    )
    candidates: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            url = _gap_normalize_url(html.unescape(match.group(1)))
            if url and url not in candidates:
                candidates.append(url)
    for match in re.finditer(r"https?://[^\s\"'<>]+", text, re.IGNORECASE):
        url = _gap_normalize_url(html.unescape(match.group(0)))
        if url and url not in candidates:
            candidates.append(url)
    return candidates


def _gap_title_terms(text: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "amid",
        "before",
        "during",
        "from",
        "into",
        "that",
        "this",
        "these",
        "those",
        "with",
        "without",
        "more",
        "than",
        "over",
        "under",
        "again",
        "still",
        "while",
        "where",
        "when",
        "what",
        "why",
        "how",
        "food",
        "bank",
        "pantry",
        "news",
        "report",
        "reports",
        "said",
    }
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 4 or token in stopwords:
            continue
        if token not in terms:
            terms.append(token)
    return terms


FOOD_LINE_RESOLUTION_PRESSURE_TERMS = (
    "food bank",
    "food pantry",
    "food insecurity",
    "snap",
    "demand",
    "shelves",
    "shortage",
    "cost",
    "costs",
    "families",
    "family",
    "children",
    "pantry",
    "inventory",
    "fuel",
    "inflation",
    "need",
    "visits",
    "assistance",
)


def _gap_pressure_term_hits(text: str) -> list[str]:
    lowered = _normalize_source_text(text).lower()
    hits: list[str] = []
    for term in FOOD_LINE_RESOLUTION_PRESSURE_TERMS:
        if term in lowered and term not in hits:
            hits.append(term)
    for hit in _gap_direct_pressure_hits(lowered):
        if hit not in hits:
            hits.append(hit)
    return hits


def _gap_relevance_terms(text: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "amid",
        "area",
        "bank",
        "before",
        "between",
        "children",
        "county",
        "counties",
        "could",
        "data",
        "during",
        "families",
        "family",
        "food",
        "from",
        "gives",
        "going",
        "have",
        "higher",
        "hours",
        "into",
        "just",
        "last",
        "local",
        "lower",
        "meet",
        "meets",
        "meeting",
        "more",
        "news",
        "pantry",
        "report",
        "reports",
        "said",
        "say",
        "says",
        "shortage",
        "still",
        "struggle",
        "struggles",
        "struggling",
        "that",
        "their",
        "these",
        "those",
        "this",
        "through",
        "today",
        "under",
        "until",
        "visit",
        "visits",
        "what",
        "when",
        "where",
        "while",
        "with",
        "without",
        "would",
        "year",
        "yesterday",
        "york",
        "your",
        "demand",
        "rising",
        "rise",
        "rises",
        "rose",
        "low",
        "new",
        "high",
        "highs",
        "push",
        "pushing",
    }
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 4 or token in stopwords:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _gap_extract_page_title(text: str) -> str:
    if not text:
        return ""
    patterns = (
        r"<title\b[^>]*>(.*?)</title>",
        r'<meta\b[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta\b[^>]*name=["\']twitter:title["\'][^>]*content=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            value = _normalize_source_text(html.unescape(match.group(1)))
            if value:
                return value
    return ""


def _gap_url_terms(url: str) -> list[str]:
    path = urllib.parse.urlsplit(_gap_normalize_url(url)).path.lower()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", path):
        if len(token) < 4:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _gap_resolved_url_relevance(url: str, *, title: str = "", summary: str = "", body: str = "") -> tuple[bool, str]:
    candidate_text = " ".join(part for part in (title, summary) if part)
    candidate_terms = _gap_relevance_terms(candidate_text)
    if not candidate_terms:
        return False, "insufficient title terms for relevance check"

    page_title = _gap_title_core_text(_gap_extract_page_title(body))
    page_terms = _gap_relevance_terms(page_title) if page_title else []
    url_terms = _gap_url_terms(url)
    candidate_keys = _gap_exact_title_key_variants(candidate_text)
    page_keys = _gap_exact_title_key_variants(page_title) if page_title else []
    title_overlap = [term for term in candidate_terms if term in page_terms] if page_terms else []
    slug_overlap = [term for term in candidate_terms if term in url_terms]
    page_pressure_hits = _gap_pressure_term_hits(" ".join(part for part in (page_title, body) if part))
    candidate_pressure_hits = _gap_pressure_term_hits(candidate_text)
    exact_title_match = bool(
        candidate_keys
        and page_keys
        and any(
            candidate_key == page_key or candidate_key in page_key or page_key in candidate_key
            for candidate_key in candidate_keys
            for page_key in page_keys
        )
    )

    if page_terms:
        min_shared_terms = max(2, min(len(candidate_terms), len(page_terms)) // 2)
        if not exact_title_match and len(title_overlap) < min_shared_terms:
            return False, f"title overlap too weak after outlet normalization: {', '.join(title_overlap[:4]) or 'none'}"
        if not page_pressure_hits:
            return False, "page title/body lacks Food Line pressure terms"
        if len(slug_overlap) < 2 and not exact_title_match:
            return False, f"resolved URL slug does not preserve enough candidate terms: {', '.join(slug_overlap[:4]) or 'none'}"
        return True, ""

    if not candidate_pressure_hits:
        return False, "candidate title/summary lacks Food Line pressure terms"
    if len(slug_overlap) < 2 and not exact_title_match:
        return False, f"resolved URL slug does not preserve enough candidate terms: {', '.join(slug_overlap[:4]) or 'none'}"
    return True, ""


def _gap_new_resolver_state(
    *,
    max_candidates: int | None,
    timeout_seconds: int,
    skip_sitemap_fallback: bool,
    max_sitemap_lookups_per_domain: int,
    max_sitemap_urls_per_domain: int,
) -> dict[str, Any]:
    return {
        "max_candidates": max_candidates,
        "timeout_seconds": timeout_seconds,
        "skip_sitemap_fallback": skip_sitemap_fallback,
        "max_sitemap_lookups_per_domain": max_sitemap_lookups_per_domain,
        "max_sitemap_urls_per_domain": max_sitemap_urls_per_domain,
        "resolution_cache": {},
        "resolution_cache_hit_count": 0,
        "resolution_diagnostics": {},
        "sitemap_cache": {},
        "sitemap_lookup_counts": {},
        "sitemap_lookup_count": 0,
        "sitemap_cache_hit_count": 0,
        "resolved_url_count": 0,
        "unresolved_url_count": 0,
        "rejected_unrelated_resolved_url_count": 0,
        "url_resolution_timeout_count": 0,
        "resolution_attempt_count": 0,
        "general_resolution_attempt_count": 0,
        "reserved_soft_block_resolution_attempt_count": 0,
        "reserved_soft_block_resolution_skipped_count": 0,
        "reserved_soft_block_exact_title_attempt_count": 0,
    }


@lru_cache(maxsize=512)
def _gap_fetch_url_text(url: str, *, timeout_seconds: int = 20) -> str:
    value = _gap_normalize_url(url)
    if not value:
        return ""
    try:
        req = urllib.request.Request(
            value,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://news.google.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=ssl._create_unverified_context()) as resp:  # noqa: S310
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = resp.read(8192)
                except IncompleteRead as exc:  # noqa: PERF203
                    if exc.partial:
                        chunks.append(bytes(exc.partial))
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
    except IncompleteRead as exc:  # noqa: PERF203
        try:
            return bytes(exc.partial).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""
    except Exception:  # noqa: BLE001
        return ""


def _gap_extract_sitemap_urls(payload: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", payload, re.IGNORECASE):
        url = _gap_normalize_url(html.unescape(match.group(1)))
        if url and url not in urls:
            urls.append(url)
    return urls


def _gap_score_sitemap_candidate_url(url: str, *, title_terms: list[str], summary_terms: list[str]) -> int:
    lowered = url.lower()
    path = urllib.parse.urlsplit(url).path.lower()
    score = 0
    if len([part for part in path.split("/") if part]) >= 2:
        score += 1
    for term in title_terms:
        if term in lowered:
            score += 3
    for term in summary_terms:
        if term in lowered:
            score += 1
    if any(term in lowered for term in ("food insecurity", "food bank", "food pantry", "snap", "wic", "demand", "shortage", "fuel", "cost", "inflation", "children", "families")):
        score += 2
    if re.search(r"/20\d{2}[-/]\d{2}[-/]\d{2}/", path) or re.search(r"/\d{4}/\d{2}/\d{2}/", path):
        score += 2
    return score


def _gap_exact_text_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_source_text(text).lower())


def _gap_title_core_text(text: str) -> str:
    normalized = _normalize_source_text(text)
    if " - " not in normalized:
        return normalized
    head, tail = normalized.rsplit(" - ", 1)
    tail_lower = tail.lower()
    tail_terms = re.findall(r"[a-z0-9]+", tail_lower)
    if not tail_terms or len(tail_terms) > 5:
        return normalized
    outlet_hints = (
        "news",
        "abc",
        "cbs",
        "nbc",
        "fox",
        "pbs",
        "post",
        "times",
        "tribune",
        "herald",
        "journal",
        "press",
        "daily",
        "magazine",
        "flyer",
        "republic",
        "sun",
        "union",
        "newsnow",
        "ktal",
        "ksbw",
        "wdrb",
        "kiiitv",
        "wmar",
        "kmtv",
        "koaa",
        "aol",
    )
    if any(hint in tail_lower for hint in outlet_hints):
        return head
    return normalized


def _gap_exact_title_key_variants(text: str) -> list[str]:
    variants: list[str] = []
    for candidate in (_normalize_source_text(text), _gap_title_core_text(text)):
        key = _gap_exact_text_key(candidate)
        if key and key not in variants:
            variants.append(key)
    return variants


def _gap_candidate_resolution_priority(candidate: dict[str, Any], *, known_local_domain: bool = False) -> tuple[int, int, int]:
    text = _gap_text_blob(candidate)
    direct_hits = _gap_direct_pressure_hits(text)
    resource_hits = _gap_resource_only_hits(text)
    score, _, penalties = score_food_line_discovery_gap_candidate(candidate, known_local_domain=known_local_domain)
    classification = classify_food_line_discovery_gap_candidate(
        candidate,
        known_status=str(candidate.get("known_status") or "unknown_domain_new_article"),
        known_local_domain=known_local_domain,
    ).get("classification")
    classification_rank = {
        "likely_qualifying": 3,
        "needs_review": 2,
        "likely_resource_only": 1,
        "duplicate_or_known": 0,
    }.get(str(classification or ""), 0)
    priority = classification_rank * 1000 + int(score) * 50
    priority += len(direct_hits) * 60
    priority += min(40, len(_gap_title_terms(str(candidate.get("title") or ""))) * 2)
    if _nonempty(candidate.get("published_at")):
        priority += 20
    if _nonempty(candidate.get("wrapper_kind")):
        priority -= 150
    if resource_hits and not direct_hits:
        priority -= 120
    if penalties:
        priority -= 20 * len(penalties)
    return priority, classification_rank, int(score)


def _gap_reserved_soft_block_resolution_budget(max_candidates: int | None) -> int:
    if max_candidates is None:
        return 0
    try:
        candidate_budget = int(max_candidates)
    except (TypeError, ValueError):
        candidate_budget = 0
    if candidate_budget <= 0:
        return 1
    return min(10, max(1, candidate_budget // 2))


def _gap_candidate_uses_reserved_soft_block_budget(candidate: dict[str, Any]) -> bool:
    if _nonempty(candidate.get("wrapper_kind")):
        return False
    if str(candidate.get("classification") or "").strip() != "likely_qualifying":
        return False
    text = _gap_text_blob(candidate)
    score = int(candidate.get("resolution_score") or 0)
    if bool(candidate.get("resolution_direct_pressure")):
        return True
    if score >= 6:
        return True
    return any(
        term in text
        for term in (
            "children",
            "child",
            "kids",
            "household",
            "households",
            "families",
            "family",
            "numeric",
        )
    )


def _gap_manual_action_for_severity(severity: str) -> str:
    if severity == "hard_block":
        return "Add this story only if the direct publisher URL and date are verified."
    if severity == "soft_block":
        return "Verify the direct publisher URL and publication date before allowing no-current-update."
    return "Keep for follow-up review; it does not block no-current-update on its own."


def _gap_verify_direct_url_date(
    url: str,
    *,
    published_at: str = "",
    edition_date: str = "",
    timeout_seconds: int = 15,
) -> tuple[bool, str, str]:
    value = _gap_normalize_url(url)
    if not value or _gap_is_google_news_url(value):
        return False, "", ""
    page_text = _gap_fetch_url_text(value, timeout_seconds=timeout_seconds)
    page_metadata_date = ""
    if page_text:
        try:
            page_metadata_date = _extract_page_metadata_date(page_text)
        except Exception:  # noqa: BLE001
            page_metadata_date = ""
    url_date = _extract_url_date(value)
    published_date = _gap_parse_published_at(published_at)[:10] if published_at else ""
    verified_date = ""
    if page_metadata_date:
        verified_date = _extract_source_date(page_metadata_date)
    if not verified_date and url_date:
        verified_date = url_date
    if not verified_date and published_date:
        verified_date = published_date
    if published_date and verified_date and published_date == verified_date:
        return True, verified_date, "published date matches direct URL"
    if edition_date and verified_date and verified_date == edition_date:
        return True, verified_date, "edition date matches direct URL"
    if published_date and url_date and published_date == url_date:
        return True, verified_date or url_date, "URL date matches published date"
    return False, verified_date, "direct URL date could not be verified"


def _gap_date_confidence(
    *,
    direct_url_date_verified: bool,
    published_at: str,
    verified_date: str,
    edition_date: str,
) -> int:
    published_date = _gap_parse_published_at(published_at)[:10] if published_at else ""
    if direct_url_date_verified:
        if published_date and verified_date and published_date == verified_date:
            return 95
        if edition_date and verified_date and edition_date == verified_date:
            return 90
        if verified_date:
            return 85
        return 80
    if published_date and edition_date and published_date == edition_date:
        return 70
    if published_date:
        return 50
    if verified_date:
        return 40
    return 10


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


def _gap_blocking_candidate_severity(candidate: dict[str, Any]) -> str:
    classification = str(candidate.get("classification") or "").strip()
    direct_url = _gap_normalize_url(_nonempty(candidate.get("direct_url") or candidate.get("resolved_url") or candidate.get("url")))
    direct_url_date_verified = bool(candidate.get("direct_url_date_verified"))
    traceable_review_url = _gap_traceable_review_url(candidate)
    direct_pressure = bool(_gap_direct_pressure_hits(_gap_text_blob(candidate)))
    score = int(candidate.get("score") or 0)
    if classification == "likely_qualifying" and direct_url and traceable_review_url == direct_url and direct_url_date_verified and direct_pressure:
        return "hard_block"
    if classification == "likely_qualifying" and direct_pressure and score >= 4:
        return "soft_block"
    return "review_only"


def _gap_collect_sitemap_urls(
    origin: str,
    *,
    resolver_state: dict[str, Any] | None = None,
    timeout_seconds: int = 20,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
) -> tuple[str, ...]:
    if not origin:
        return ()
    cache_key = origin.rstrip("/")
    state = resolver_state if resolver_state is not None else {}
    sitemap_cache = state.setdefault("sitemap_cache", {})
    if cache_key in sitemap_cache:
        state["sitemap_cache_hit_count"] = int(state.get("sitemap_cache_hit_count") or 0) + 1
        return tuple(sitemap_cache[cache_key])
    domain_lookup_counts = state.setdefault("sitemap_lookup_counts", {})
    lookup_count = int(domain_lookup_counts.get(cache_key) or 0)
    if lookup_count >= max_sitemap_lookups_per_domain:
        return ()
    domain_lookup_counts[cache_key] = lookup_count + 1
    state["sitemap_lookup_count"] = int(state.get("sitemap_lookup_count") or 0) + 1
    sitemap_urls = [
        urllib.parse.urljoin(origin, "/sitemap.xml"),
        urllib.parse.urljoin(origin, "/sitemap_index.xml"),
        urllib.parse.urljoin(origin, "/wp-sitemap.xml"),
        urllib.parse.urljoin(origin, "/news-sitemap.xml"),
    ]
    discovered_urls: list[str] = []
    seen_sitemaps: set[str] = set()
    for sitemap_url in sitemap_urls:
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        payload = _gap_fetch_url_text(sitemap_url, timeout_seconds=timeout_seconds)
        if not payload:
            continue
        locs = _gap_extract_sitemap_urls(payload)
        if not locs:
            continue
        if max_sitemap_urls_per_domain > 0:
            locs = locs[:max_sitemap_urls_per_domain]
        if "<sitemapindex" in payload.lower():
            for nested_url in locs[:20]:
                if nested_url in seen_sitemaps:
                    continue
                seen_sitemaps.add(nested_url)
                if max_sitemap_urls_per_domain > 0 and len(discovered_urls) >= max_sitemap_urls_per_domain:
                    break
                nested_payload = _gap_fetch_url_text(nested_url, timeout_seconds=timeout_seconds)
                if nested_payload:
                    discovered_urls.extend(_gap_extract_sitemap_urls(nested_payload)[:max_sitemap_urls_per_domain])
                if max_sitemap_urls_per_domain > 0 and len(discovered_urls) >= max_sitemap_urls_per_domain:
                    break
        else:
            discovered_urls.extend(locs)
        if max_sitemap_urls_per_domain > 0 and len(discovered_urls) >= max_sitemap_urls_per_domain:
            discovered_urls = discovered_urls[:max_sitemap_urls_per_domain]
            break
    sitemap_cache[cache_key] = tuple(discovered_urls)
    return tuple(discovered_urls)


def _gap_resolve_publisher_sitemap_url(
    source_url: str,
    *,
    title: str = "",
    summary: str = "",
    resolver_state: dict[str, Any] | None = None,
    timeout_seconds: int = 20,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
) -> str:
    base = _gap_normalize_url(source_url)
    if not base:
        return ""
    parsed = urllib.parse.urlsplit(base)
    if not parsed.scheme or not parsed.netloc:
        return ""
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    discovered_urls = _gap_collect_sitemap_urls(
        origin,
        resolver_state=resolver_state,
        timeout_seconds=timeout_seconds,
        max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
        max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
    )
    if not discovered_urls:
        return ""
    title_terms = _gap_title_terms(title)
    summary_terms = _gap_title_terms(summary)
    best_url = ""
    best_score = 0
    for url in discovered_urls:
        if not _gap_is_plausible_article_url(url, seed_url=base):
            continue
        score = _gap_score_sitemap_candidate_url(url, title_terms=title_terms, summary_terms=summary_terms)
        if score > best_score:
            best_url = url
            best_score = score
    return best_url


def _gap_resolve_publisher_exact_title_url(
    source_url: str,
    *,
    title: str = "",
    summary: str = "",
    resolver_state: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    timeout_seconds: int = 20,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
) -> str:
    diag = diagnostics if diagnostics is not None else {}
    base = _gap_normalize_url(source_url)
    if not base:
        diag["exact_title_failure_cause"] = "missing_origin_url"
        return ""
    parsed = urllib.parse.urlsplit(base)
    if not parsed.scheme or not parsed.netloc:
        diag["exact_title_failure_cause"] = "missing_origin_url"
        return ""
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    diag["exact_title_origin"] = origin
    diag["exact_title_source_url"] = base
    before_lookup_count = int((resolver_state or {}).get("sitemap_lookup_count") or 0)
    discovered_urls = _gap_collect_sitemap_urls(
        origin,
        resolver_state=resolver_state,
        timeout_seconds=timeout_seconds,
        max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
        max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
    )
    after_lookup_count = int((resolver_state or {}).get("sitemap_lookup_count") or 0)
    diag["sitemap_lookup_count_delta"] = max(0, after_lookup_count - before_lookup_count)
    diag["sitemap_urls_checked_count"] = len(discovered_urls)
    if not discovered_urls:
        diag["exact_title_failure_cause"] = "no_sitemap_urls_found"
        return ""
    title_keys = _gap_exact_title_key_variants(title)
    title_terms = _gap_title_terms(title)
    summary_terms = _gap_title_terms(summary)
    scored_urls: list[tuple[int, str]] = []
    for url in discovered_urls:
        if not _gap_is_plausible_article_url(url, seed_url=base) or "news.google.com" in url.lower():
            continue
        path = urllib.parse.urlsplit(url).path.lower()
        slug_key = _gap_exact_text_key(path)
        score = 0
        if title_keys and any(title_key == slug_key or title_key in slug_key or slug_key in title_key for title_key in title_keys):
            score += 100
        if title_terms and all(term in slug_key for term in title_terms[:6]):
            score += 50
        elif any(term in slug_key for term in title_terms[:6]):
            score += 20
        if any(term in slug_key for term in summary_terms[:6]):
            score += 5
        if score:
            scored_urls.append((score, url))
    diag["exact_title_scored_url_count"] = len(scored_urls)
    scored_urls.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    page_fetch_attempts = 0
    for _, url in scored_urls[:8]:
        page_fetch_attempts += 1
        page_text = _gap_fetch_url_text(url, timeout_seconds=timeout_seconds)
        if not page_text:
            continue
        if _gap_resolved_url_relevance(url, title=title, summary=summary, body=page_text)[0]:
            diag["exact_title_resolution_mode"] = "page_relevance_match"
            return url
    diag["exact_title_page_fetch_attempts"] = page_fetch_attempts
    if scored_urls:
        diag["exact_title_failure_cause"] = "candidate_returned_without_page_match"
    else:
        diag["exact_title_failure_cause"] = "no_matching_slug"
    return ""


def _gap_pick_best_article_url(urls: list[str], *, seed_url: str = "") -> str:
    best_url = ""
    best_score = -1
    for url in urls:
        if not _gap_is_plausible_article_url(url, seed_url=seed_url):
            continue
        if "news.google.com" in url.lower():
            continue
        score = 0
        path = urllib.parse.urlsplit(url).path.lower()
        lowered = url.lower()
        if re.search(r"/20\d{2}[-/]\d{2}[-/]\d{2}/", path):
            score += 20
        if re.search(r"/regional-news/20\d{2}-\d{2}-\d{2}/", path):
            score += 20
        if re.search(r"/\d{4}/\d{2}/\d{2}/", path):
            score += 18
        if any(term in lowered for term in ("food bank", "food pantry", "snap", "wic", "food insecurity", "food assistance", "demand", "need", "inflation", "federal cuts", "fuel costs", "shortage")):
            score += 8
        if score > best_score:
            best_url = url
            best_score = score
    return best_url


def _gap_is_plausible_article_url(url: str, *, seed_url: str = "") -> bool:
    url = _gap_normalize_url(url)
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path:
        return False
    lowered = f"{url.lower()} {parsed.netloc.lower()}"
    if any(
        token in lowered
        for token in (
            "#comment",
            "/tag/",
            "/category/",
            "/author/",
            "/search?",
            "/feed",
            "/rss",
            "/atom",
            "javascript:",
            "mailto:",
            "/wp-json/",
            "google.com",
            "gstatic.com",
            "ytimg.com",
            "doubleclick.net",
            "googletagmanager.com",
            "googleusercontent.com",
        )
    ):
        return False
    if re.search(r"\.(?:png|jpe?g|gif|svg|webp|ico|css|js|json|xml)(?:$|\?)", path, re.IGNORECASE):
        return False
    if url.rstrip("/") == str(seed_url or "").strip().rstrip("/"):
        return False
    if _is_article_like_url(url, seed_url=seed_url):
        return True
    path_parts = [part for part in path.split("/") if part]
    if len(path_parts) >= 2:
        return True
    return bool(re.search(r"\b(news|story|article|post|briefing|update)\b", lowered))


def _gap_resolve_google_news_url(
    url: str,
    *,
    title: str = "",
    summary: str = "",
    source_url: str = "",
    resolver_state: dict[str, Any] | None = None,
    timeout_seconds: int = 15,
    skip_sitemap_fallback: bool = False,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
    allow_exact_title_fallback: bool = False,
) -> tuple[str, str, str]:
    value = _gap_normalize_url(url)
    if not value:
        return "", "empty_url", "empty url"
    state = resolver_state if resolver_state is not None else {}
    cache_key = value
    cache = state.setdefault("resolution_cache", {})
    diagnostics = state.setdefault("resolution_diagnostics", {})
    diag = diagnostics.setdefault(cache_key, {})
    if cache_key in cache:
        resolved_url, status, reason = cache[cache_key]
        state["resolution_cache_hit_count"] = int(state.get("resolution_cache_hit_count") or 0) + 1
        diag["cache_hit"] = True
        diag["cached_status"] = status
        diag["cached_reason"] = reason
        return resolved_url, status, reason
    parsed = urllib.parse.urlsplit(value)
    diag.update(
        {
            "input_url": value,
            "source_url": _gap_normalize_url(source_url),
            "source_url_is_google_news": _gap_is_google_news_url(source_url or ""),
            "resolution_budget_mode": str(state.get("_current_resolution_budget_mode") or "general"),
            "allow_exact_title_fallback": bool(allow_exact_title_fallback),
            "skip_sitemap_fallback": bool(skip_sitemap_fallback),
            "resolution_mode": "google_news_url",
            "exact_title_attempted": False,
            "sitemap_fallback_attempted": False,
            "sitemap_lookup_count_before": int(state.get("sitemap_lookup_count") or 0),
        }
    )
    if parsed.netloc.lower() not in {"news.google.com", "www.news.google.com"} and "news.google.com" not in value.lower():
        result = (value, "direct_article_url", "")
        cache[cache_key] = result
        diag["resolution_mode"] = "direct_article_url"
        return result
    resolved = ""
    body = ""
    final_url = ""
    try:
        req = urllib.request.Request(value, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=ssl._create_unverified_context()) as resp:  # noqa: S310
            final_url = _gap_normalize_url(resp.geturl())
            if final_url and _gap_is_plausible_article_url(final_url, seed_url=value):
                relevant, reason = _gap_resolved_url_relevance(final_url, title=title, summary=summary)
                if relevant:
                    result = (final_url, "resolved_google_news_url", "")
                    cache[cache_key] = result
                    diag["resolution_mode"] = "google_news_redirect"
                    diag["resolved_url"] = final_url
                    return result
                result = ("", "rejected_unrelated_resolved_url", reason)
                cache[cache_key] = result
                diag["resolution_mode"] = "google_news_redirect_rejected"
                diag["failure_reason"] = reason
                return result
            try:
                body = resp.read(200_000).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        reason = str(exc).strip() or "resolver timed out"
        result = ("", "url_resolution_timeout", reason)
        cache[cache_key] = result
        diag["resolution_mode"] = "google_news_timeout"
        diag["failure_reason"] = reason
        return result
    except Exception:  # noqa: BLE001
        final_url = ""
    if body:
        resolved = _gap_pick_best_article_url(_gap_extract_article_url_candidates(body), seed_url=value)
        if resolved:
            relevant, reason = _gap_resolved_url_relevance(resolved, title=title, summary=summary, body=body)
            if relevant:
                result = (resolved, "resolved_google_news_url", "")
                cache[cache_key] = result
                diag["resolution_mode"] = "google_news_body_match"
                diag["resolved_url"] = resolved
                return result
            result = ("", "rejected_unrelated_resolved_url", reason)
            cache[cache_key] = result
            diag["resolution_mode"] = "google_news_body_rejected"
            diag["failure_reason"] = reason
            return result
    if final_url and _gap_is_plausible_article_url(final_url, seed_url=value):
        relevant, reason = _gap_resolved_url_relevance(final_url, title=title, summary=summary, body=body)
        if relevant:
            result = (final_url, "resolved_google_news_url", "")
            cache[cache_key] = result
            diag["resolution_mode"] = "google_news_redirect"
            diag["resolved_url"] = final_url
            return result
        result = ("", "rejected_unrelated_resolved_url", reason)
        cache[cache_key] = result
        diag["resolution_mode"] = "google_news_redirect_rejected"
        diag["failure_reason"] = reason
        return result
    if skip_sitemap_fallback:
        if allow_exact_title_fallback:
            diag["exact_title_attempted"] = True
            if str(state.get("_current_resolution_budget_mode") or "") == "reserved_soft_block":
                state["reserved_soft_block_exact_title_attempt_count"] = int(state.get("reserved_soft_block_exact_title_attempt_count") or 0) + 1
            exact_title_url = _gap_resolve_publisher_exact_title_url(
                source_url or value,
                title=title,
                summary=summary,
                resolver_state=resolver_state,
                diagnostics=diag,
                timeout_seconds=timeout_seconds,
                max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
                max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
            )
            if exact_title_url:
                result = (exact_title_url, "resolved_publisher_exact_title_url", "")
                cache[cache_key] = result
                diag["resolution_mode"] = "publisher_exact_title"
                diag["resolved_url"] = exact_title_url
                return result
        else:
            diag["failure_reason"] = "exact title fallback not attempted: confidence gate not met"
        failure_reason = "sitemap fallback disabled"
        if allow_exact_title_fallback:
            failure_cause = str(diag.get("exact_title_failure_cause") or "").strip()
            if failure_cause:
                failure_reason = f"sitemap fallback disabled; exact title fallback failed: {failure_cause}"
        result = ("", "unresolved_google_news_url", failure_reason)
        cache[cache_key] = result
        diag["failure_reason"] = failure_reason
        return result
    if allow_exact_title_fallback:
        diag["exact_title_attempted"] = True
        if str(state.get("_current_resolution_budget_mode") or "") == "reserved_soft_block":
            state["reserved_soft_block_exact_title_attempt_count"] = int(state.get("reserved_soft_block_exact_title_attempt_count") or 0) + 1
        exact_title_url = _gap_resolve_publisher_exact_title_url(
            source_url or value,
            title=title,
            summary=summary,
            resolver_state=resolver_state,
            diagnostics=diag,
            timeout_seconds=timeout_seconds,
            max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
            max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
        )
        if exact_title_url:
            result = (exact_title_url, "resolved_publisher_exact_title_url", "")
            cache[cache_key] = result
            diag["resolution_mode"] = "publisher_exact_title"
            diag["resolved_url"] = exact_title_url
            return result
        diag["failure_reason"] = str(diag.get("exact_title_failure_cause") or "no_exact_title_match")
    sitemap_url = _gap_resolve_publisher_sitemap_url(
        source_url or value,
        title=title,
        summary=summary,
        resolver_state=resolver_state,
        timeout_seconds=timeout_seconds,
        max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
        max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
    )
    diag["sitemap_fallback_attempted"] = True
    if sitemap_url:
        relevant, reason = _gap_resolved_url_relevance(sitemap_url, title=title, summary=summary)
        if relevant:
            result = (sitemap_url, "resolved_publisher_sitemap_url", "")
            cache[cache_key] = result
            diag["resolution_mode"] = "publisher_sitemap"
            diag["resolved_url"] = sitemap_url
            return result
        result = ("", "rejected_unrelated_resolved_url", reason)
        cache[cache_key] = result
        diag["resolution_mode"] = "publisher_sitemap_rejected"
        diag["failure_reason"] = reason
        return result
    failure_reason = str(diag.get("failure_reason") or "no acceptable article URL found")
    if not failure_reason:
        failure_reason = "no acceptable article URL found"
    result = ("", "unresolved_google_news_url", failure_reason)
    cache[cache_key] = result
    diag["failure_reason"] = failure_reason
    return result


def _gap_direct_pressure_hits(text: str) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    patterns = (
        (r"\bempty shelves\b", "empty shelves"),
        (r"\bshelves are bare\b", "shelves are bare"),
        (r"\brecord demand\b", "record demand"),
        (r"\brecord number of visits\b", "record number of visits"),
        (r"\bdemand is rising\b", "demand is rising"),
        (r"\brising demand\b", "rising demand"),
        (r"\bdemand rising\b", "demand rising"),
        (r"\bdemand continues to rise\b", "demand continues to rise"),
        (r"\bdemand surges\b", "demand surges"),
        (r"\bsurge in demand\b", "surge in demand"),
        (r"\bhigher demand\b", "higher demand"),
        (r"\brising fuel prices put further strain\b", "rising fuel prices put further strain"),
        (r"\bfuel prices? (?:are|is) hitting .*?food bank hard\b", "fuel prices hitting food bank hard"),
        (r"\balready reeling from federal cuts\b", "already reeling from federal cuts"),
        (r"\blow inventory\b", "low inventory"),
        (r"\bcritical shortage\b", "critical shortage"),
        (r"\btemporarily closes?\b", "temporarily closes"),
        (r"can(?:'|’|â€™)?t keep food on the shelf", "can't keep food on the shelf"),
        (r"\bcannot keep food on the shelf\b", "cannot keep food on the shelf"),
        (r"\bsnap benefits? (?:were )?halted\b", "SNAP benefits halted"),
        (r"\bsnap cuts\b", "SNAP cuts"),
        (r"\bsnap reductions\b", "SNAP reductions"),
        (r"\bbenefits? were halted\b", "benefits were halted"),
        (r"\bbenefit reductions\b", "benefit reductions"),
        (r"\bfuel costs?\b", "fuel costs"),
        (r"\bdeliver(?:y|ies) affected\b", "deliveries affected"),
        (r"\bmeals? reduced\b", "meals reduced"),
        (r"\bincreased need\b", "increased need"),
        (r"\bmore families rely on food pantries\b", "more families rely on food pantries"),
        (r"\bfamilies rely on food pantries\b", "families rely on food pantries"),
        (r"\bsummer need\b", "summer need"),
        (r"\brural distance\b", "rural distance"),
        (r"\btransportation barrier(?:s)?\b", "transportation barrier"),
        (r"\bworking families\b", "working families"),
        (r"\bfood insecurity (?:percent|percentage)\b", "food insecurity percentage"),
        (r"\bfood insecurity (?:is|was|at) \d", "food insecurity percentage"),
        (r"\bout of food\b", "out of food"),
        (r"\brunning out\b", "running out"),
    )
    for pattern, label in patterns:
        if re.search(pattern, lowered):
            hits.append(label)
    return hits


def _gap_resource_only_hits(text: str) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for term in GAP_RESOURCE_ONLY_TERMS:
        if term in lowered and term not in hits:
            hits.append(term)
    return hits


def _gap_parse_published_at(value: str) -> str:
    raw = _nonempty(value)
    if not raw:
        return ""
    for candidate in (raw, raw[:10]):
        try:
            parsed = parsedate_to_datetime(candidate)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            return parsed.isoformat()
    return raw


def _gap_parse_rss_items(
    payload: bytes,
    *,
    resolver_state: dict[str, Any] | None = None,
    resolver_timeout_seconds: int = 15,
    skip_sitemap_fallback: bool = False,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
    edition_date: str = "",
) -> list[dict[str, str]]:
    text = INVALID_XML_ENTITY_RE.sub("&amp;", payload.decode("utf-8", errors="replace"))
    root = ET.fromstring(text)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{*}item")
    state = resolver_state if resolver_state is not None else {}
    max_candidates = state.get("max_candidates")
    provisional_rows: list[dict[str, Any]] = []
    for item in items:
        source_el = item.find("source")
        if source_el is None:
            source_el = item.find("{*}source")
        source_url = _nonempty(source_el.attrib.get("url") if source_el is not None else "")
        publisher = _normalize_source_text(source_el.text or "") if source_el is not None else ""
        link = _nonempty(item.findtext("link") or "")
        if not link:
            link_el = item.find("link")
            if link_el is not None:
                link = _nonempty(link_el.attrib.get("href"))
        google_news_url = _gap_normalize_url(link)
        title = _nonempty(item.findtext("title") or "")
        summary = _normalize_source_text(item.findtext("description") or item.findtext("summary") or item.findtext("content") or "")
        candidate_url = google_news_url or _gap_normalize_url(source_url)
        candidate_domain = _gap_domain(candidate_url)
        publisher_domain = _gap_domain(source_url)
        provisional_candidate = {
            "title": _normalize_source_text(item.findtext("title") or ""),
            "publisher": publisher,
            "source_url": _gap_normalize_url(source_url),
            "publisher_url": _gap_normalize_url(source_url),
            "candidate_url": candidate_url,
            "resolved_url": "",
            "google_news_url": google_news_url,
            "url_resolution_status": "direct_article_url",
            "url_resolution_reason": "",
            "link_url": google_news_url,
            "published_at": _gap_parse_published_at(item.findtext("pubDate") or item.findtext("published") or item.findtext("updated") or ""),
            "summary_or_snippet": _normalize_source_text(item.findtext("description") or item.findtext("summary") or item.findtext("content") or ""),
            "domain": candidate_domain or publisher_domain or publisher,
            "publisher_domain": publisher_domain or candidate_domain or publisher,
        }
        provisional_rows.append(provisional_candidate)

    sortable_rows: list[dict[str, Any]] = []
    for row in provisional_rows:
        candidate_url = str(row.get("candidate_url") or "").strip()
        source_url = str(row.get("publisher_url") or "").strip()
        wrapper_kind = _gap_wrapper_kind(row)
        text = _gap_text_blob(row)
        direct_pressure = bool(_gap_direct_pressure_hits(text))
        score, reasons, penalties = score_food_line_discovery_gap_candidate(
            {
                **row,
                "wrapper_kind": wrapper_kind,
            },
            known_local_domain=bool(_gap_domain(source_url) and _gap_domain(source_url) == _gap_domain(candidate_url)),
        )
        classification = classify_food_line_discovery_gap_candidate(
            {
                **row,
                "wrapper_kind": wrapper_kind,
            },
            known_status="unknown_domain_new_article",
            known_local_domain=bool(_gap_domain(source_url) and _gap_domain(source_url) == _gap_domain(candidate_url)),
        )
        priority, classification_rank, score_value = _gap_candidate_resolution_priority(
            {
                **row,
                "wrapper_kind": wrapper_kind,
                "known_status": "unknown_domain_new_article",
            },
            known_local_domain=bool(_gap_domain(source_url) and _gap_domain(source_url) == _gap_domain(candidate_url)),
        )
        row["wrapper_kind"] = wrapper_kind
        row["pressure_clues_found"] = _gap_direct_pressure_hits(text)
        row["secondary_queries_generated"] = _gap_secondary_queries_from_wrapper(row)
        row["resolution_priority"] = priority
        row["resolution_classification_rank"] = classification_rank
        row["resolution_score"] = score_value
        row["resolution_reasons"] = reasons
        row["resolution_penalties"] = penalties
        row["resolution_direct_pressure"] = direct_pressure
        row["classification"] = classification.get("classification")
        row["known_status"] = "unknown_domain_new_article"
        sortable_rows.append(row)

    sortable_rows.sort(
        key=lambda row: (
            -int(row.get("resolution_priority") or 0),
            -int(row.get("resolution_score") or 0),
            str(row.get("published_at") or ""),
            str(row.get("title") or ""),
            str(row.get("candidate_url") or ""),
        )
    )

    reserved_soft_block_budget = _gap_reserved_soft_block_resolution_budget(max_candidates)
    general_resolution_budget: int | None
    if max_candidates is None:
        general_resolution_budget = None
    else:
        general_resolution_budget = max(0, int(max_candidates) - reserved_soft_block_budget)
    state["reserved_soft_block_resolution_budget"] = reserved_soft_block_budget
    state["general_resolution_budget"] = general_resolution_budget
    reserved_soft_block_candidate_urls: set[str] = set()
    reserved_soft_block_candidate_rank = 0
    for row in sortable_rows:
        candidate_url = _gap_normalize_url(_nonempty(row.get("candidate_url")))
        if not candidate_url or not _gap_is_google_news_url(candidate_url):
            continue
        if not _gap_candidate_uses_reserved_soft_block_budget(row):
            continue
        reserved_soft_block_candidate_rank += 1
        row["reserved_soft_block_candidate_rank"] = reserved_soft_block_candidate_rank
        if reserved_soft_block_candidate_rank <= reserved_soft_block_budget:
            reserved_soft_block_candidate_urls.add(candidate_url)
        else:
            row["reserved_soft_block_candidate_rank"] = reserved_soft_block_candidate_rank

    rows: list[dict[str, str]] = []
    for row in sortable_rows:
        candidate_url = _gap_normalize_url(_nonempty(row.get("candidate_url")))
        google_news_url = _gap_normalize_url(_nonempty(row.get("google_news_url")))
        source_url = _gap_normalize_url(_nonempty(row.get("publisher_url")))
        resolved_url = ""
        url_resolution_status = str(row.get("url_resolution_status") or "direct_article_url")
        url_resolution_reason = ""
        is_google_news_url = _gap_is_google_news_url(candidate_url or google_news_url)
        if is_google_news_url:
            row["resolution_diagnostics"] = dict(row.get("resolution_diagnostics") or {})
            is_reserved_soft_block_candidate = candidate_url in reserved_soft_block_candidate_urls
            row["resolution_diagnostics"]["reserved_soft_block_candidate"] = is_reserved_soft_block_candidate
            row["resolution_diagnostics"]["reserved_soft_block_candidate_rank"] = int(row.get("reserved_soft_block_candidate_rank") or 0)
            row["resolution_diagnostics"]["reserved_soft_block_budget"] = reserved_soft_block_budget
            row["resolution_diagnostics"]["general_resolution_budget"] = -1 if general_resolution_budget is None else int(general_resolution_budget)
            row["resolution_diagnostics"]["resolution_budget_mode"] = "reserved_soft_block" if is_reserved_soft_block_candidate else "general"
            row["resolution_diagnostics"]["resolution_budget_status"] = "pending"
            if is_reserved_soft_block_candidate:
                if int(state.get("reserved_soft_block_resolution_attempt_count") or 0) >= reserved_soft_block_budget:
                    url_resolution_status = "resolution_skipped_reserved_budget"
                    url_resolution_reason = "reserved budget exhausted"
                    row["resolution_diagnostics"]["resolution_budget_status"] = "skipped"
                    row["resolution_diagnostics"]["resolution_skip_reason"] = url_resolution_reason
                    state["reserved_soft_block_resolution_skipped_count"] = int(state.get("reserved_soft_block_resolution_skipped_count") or 0) + 1
                    state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                else:
                    state["reserved_soft_block_resolution_attempt_count"] = int(state.get("reserved_soft_block_resolution_attempt_count") or 0) + 1
                    state["resolution_attempt_count"] = int(state.get("resolution_attempt_count") or 0) + 1
                    state["reserved_soft_block_resolution_budget_remaining"] = max(0, reserved_soft_block_budget - int(state.get("reserved_soft_block_resolution_attempt_count") or 0))
                    allow_exact_title_fallback = bool(
                        int(row.get("resolution_classification_rank") or 0) >= 3
                        or int(row.get("resolution_score") or 0) >= 6
                        or bool(row.get("resolution_direct_pressure"))
                    )
                    state["_current_resolution_budget_mode"] = "reserved_soft_block"
                    try:
                        resolved_url, url_resolution_status, url_resolution_reason = _gap_resolve_google_news_url(
                            google_news_url or candidate_url,
                            title=str(row.get("title") or ""),
                            summary=str(row.get("summary_or_snippet") or ""),
                            source_url=source_url,
                            resolver_state=state,
                            timeout_seconds=resolver_timeout_seconds,
                            skip_sitemap_fallback=skip_sitemap_fallback,
                            max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
                            max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
                            allow_exact_title_fallback=allow_exact_title_fallback,
                        )
                    finally:
                        state.pop("_current_resolution_budget_mode", None)
                    row["resolution_diagnostics"] = dict(
                        (state.get("resolution_diagnostics") or {}).get(_gap_normalize_url(google_news_url or candidate_url), {})
                    )
                    if url_resolution_status in {"resolved_google_news_url", "resolved_publisher_sitemap_url", "resolved_publisher_exact_title_url"}:
                        state["resolved_url_count"] = int(state.get("resolved_url_count") or 0) + 1
                    elif url_resolution_status == "rejected_unrelated_resolved_url":
                        state["rejected_unrelated_resolved_url_count"] = int(state.get("rejected_unrelated_resolved_url_count") or 0) + 1
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    elif url_resolution_status == "url_resolution_timeout":
                        state["url_resolution_timeout_count"] = int(state.get("url_resolution_timeout_count") or 0) + 1
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    elif url_resolution_status in {"unresolved_google_news_url", "resolution_skipped_max_candidates", "resolution_skipped_reserved_budget"}:
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    if url_resolution_status == "resolved_publisher_exact_title_url" and is_reserved_soft_block_candidate:
                        row["resolution_diagnostics"]["reserved_soft_block_exact_title_resolved"] = True
            else:
                if general_resolution_budget is not None and int(state.get("general_resolution_attempt_count") or 0) >= int(general_resolution_budget):
                    url_resolution_status = "resolution_skipped_max_candidates"
                    url_resolution_reason = "general budget exhausted"
                    row["resolution_diagnostics"]["resolution_budget_status"] = "skipped"
                    row["resolution_diagnostics"]["resolution_skip_reason"] = url_resolution_reason
                    state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    if _gap_candidate_uses_reserved_soft_block_budget(row):
                        state["reserved_soft_block_resolution_skipped_count"] = int(state.get("reserved_soft_block_resolution_skipped_count") or 0) + 1
                        row["resolution_diagnostics"]["resolution_skip_reason"] = "reserved budget exhausted"
                        row["resolution_diagnostics"]["resolution_budget_status"] = "skipped"
                        row["resolution_diagnostics"]["reserved_soft_block_candidate"] = True
                        row["resolution_diagnostics"]["reserved_soft_block_candidate_rank"] = int(row.get("reserved_soft_block_candidate_rank") or 0)
                        url_resolution_reason = "reserved budget exhausted"
                    state["general_resolution_budget_remaining"] = 0
                else:
                    state["general_resolution_attempt_count"] = int(state.get("general_resolution_attempt_count") or 0) + 1
                    state["resolution_attempt_count"] = int(state.get("resolution_attempt_count") or 0) + 1
                    state["general_resolution_budget_remaining"] = (
                        -1 if general_resolution_budget is None else max(0, int(general_resolution_budget) - int(state.get("general_resolution_attempt_count") or 0))
                    )
                    allow_exact_title_fallback = bool(
                        int(row.get("resolution_classification_rank") or 0) >= 3
                        or int(row.get("resolution_score") or 0) >= 6
                        or bool(row.get("resolution_direct_pressure"))
                    )
                    state["_current_resolution_budget_mode"] = "general"
                    try:
                        resolved_url, url_resolution_status, url_resolution_reason = _gap_resolve_google_news_url(
                            google_news_url or candidate_url,
                            title=str(row.get("title") or ""),
                            summary=str(row.get("summary_or_snippet") or ""),
                            source_url=source_url,
                            resolver_state=state,
                            timeout_seconds=resolver_timeout_seconds,
                            skip_sitemap_fallback=skip_sitemap_fallback,
                            max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
                            max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
                            allow_exact_title_fallback=allow_exact_title_fallback,
                        )
                    finally:
                        state.pop("_current_resolution_budget_mode", None)
                    row["resolution_diagnostics"] = dict(
                        (state.get("resolution_diagnostics") or {}).get(_gap_normalize_url(google_news_url or candidate_url), {})
                    )
                    if url_resolution_status in {"resolved_google_news_url", "resolved_publisher_sitemap_url", "resolved_publisher_exact_title_url"}:
                        state["resolved_url_count"] = int(state.get("resolved_url_count") or 0) + 1
                    elif url_resolution_status == "rejected_unrelated_resolved_url":
                        state["rejected_unrelated_resolved_url_count"] = int(state.get("rejected_unrelated_resolved_url_count") or 0) + 1
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    elif url_resolution_status == "url_resolution_timeout":
                        state["url_resolution_timeout_count"] = int(state.get("url_resolution_timeout_count") or 0) + 1
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
                    elif url_resolution_status in {"unresolved_google_news_url", "resolution_skipped_max_candidates", "resolution_skipped_reserved_budget"}:
                        state["unresolved_url_count"] = int(state.get("unresolved_url_count") or 0) + 1
            if resolved_url:
                candidate_url = resolved_url
            elif google_news_url:
                candidate_url = google_news_url
            else:
                candidate_url = source_url
        else:
            candidate_url = candidate_url or source_url
        if not candidate_url:
            continue
        candidate_domain = _gap_domain(candidate_url)
        publisher_domain = _gap_domain(source_url)
        direct_url = resolved_url or (candidate_url if not is_google_news_url else "")
        direct_url_date_verified = False
        verified_date = ""
        date_verification_reason = ""
        should_verify_direct_date = bool(direct_url) and (
            bool(row.get("resolution_direct_pressure"))
            or int(row.get("resolution_classification_rank") or 0) >= 2
            or int(row.get("resolution_score") or 0) >= 4
        )
        if should_verify_direct_date:
            direct_url_date_verified, verified_date, date_verification_reason = _gap_verify_direct_url_date(
                direct_url,
                published_at=str(row.get("published_at") or ""),
                edition_date=edition_date,
                timeout_seconds=resolver_timeout_seconds,
            )
        date_confidence = _gap_date_confidence(
            direct_url_date_verified=direct_url_date_verified,
            published_at=str(row.get("published_at") or ""),
            verified_date=verified_date,
            edition_date=edition_date,
        )
        row_payload = {
            "title": str(row.get("title") or ""),
            "publisher": str(row.get("publisher") or ""),
            "source_url": _gap_normalize_url(_nonempty(source_url) or str((row.get("resolution_diagnostics") or {}).get("source_url") or "")),
            "publisher_url": publisher_domain and source_url or source_url,
            "candidate_url": candidate_url,
            "resolved_url": resolved_url,
            "google_news_url": google_news_url,
            "url_resolution_status": url_resolution_status,
            "url_resolution_reason": url_resolution_reason,
            "link_url": google_news_url,
            "published_at": str(row.get("published_at") or ""),
            "summary_or_snippet": str(row.get("summary_or_snippet") or ""),
            "domain": candidate_domain or publisher_domain or str(row.get("publisher") or ""),
            "publisher_domain": publisher_domain or candidate_domain or str(row.get("publisher") or ""),
            "date_confidence": date_confidence,
            "direct_url_date_verified": direct_url_date_verified,
            "verified_date": verified_date,
            "date_verification_reason": date_verification_reason,
            "resolution_diagnostics": row.get("resolution_diagnostics") or {},
        }
        rows.append(row_payload)
    return rows


def _gap_text_blob(candidate: dict[str, Any]) -> str:
    return _normalize_source_text(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("summary_or_snippet") or ""),
                str(candidate.get("publisher") or ""),
                str(candidate.get("candidate_url") or ""),
            )
            if part
        ),
        limit=1200,
    )


def _gap_resource_only_hit(text: str) -> bool:
    lowered = text.lower()
    resource_only_terms = (
        "where to get food",
        "free meals",
        "distribution schedule",
        "hours",
        "locations",
        "find food",
        "find a food bank",
        "get help",
        "apply for benefits",
    )
    return any(term in lowered for term in resource_only_terms)


FOOD_LINE_DISCOVERY_GAP_UNRESOLVED_DIRECT_PRESSURE_BLOCK_SCORE = 8


def score_food_line_discovery_gap_candidate(
    candidate: dict[str, Any],
    *,
    known_local_domain: bool = False,
) -> tuple[int, list[str], list[str]]:
    text = _gap_text_blob(candidate)
    lowered = text.lower()
    score = 0
    reasons: list[str] = []
    penalties: list[str] = []
    direct_hits = _gap_direct_pressure_hits(lowered)
    resource_hits = _gap_resource_only_hits(lowered)
    if "food insecurity" in lowered:
        score += 3
        reasons.append("food insecurity")
    if "food bank" in lowered and any(term in lowered for term in ("demand", "shortage", "inventory", "shelves", "cost", "inflation", "snap")):
        score += 3
        reasons.append("food bank pressure")
    if "pantry" in lowered and any(term in lowered for term in ("demand", "shortage", "empty", "line", "shelves")):
        score += 2
        reasons.append("pantry pressure")
    if (
        re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent)\b", lowered)
        or re.search(r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?", lowered)
        or re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b", lowered)
    ) and any(term in lowered for term in ("famil", "people", "meal", "dollar", "cost", "county", "counties", "household", "families", "children", "percent", "%")):
        score += 2
        reasons.append("numeric pressure")
    if any(term in lowered for term in ("children", "summer meals", "school meals", "families")):
        score += 2
        reasons.append("households or children")
    if direct_hits:
        score += 4
        reasons.append("direct pressure signal: " + ", ".join(direct_hits[:4]))
    if resource_hits:
        if not direct_hits:
            score -= 4
            penalties.append("resource/donation framing")
        else:
            reasons.append("resource wrapper with direct pressure signal")
    if known_local_domain:
        score += 1
        reasons.append("local or news domain")
    return score, reasons, penalties


def classify_food_line_discovery_gap_candidate(
    candidate: dict[str, Any],
    *,
    known_status: str,
    known_local_domain: bool = False,
) -> dict[str, Any]:
    score, reasons, penalties = score_food_line_discovery_gap_candidate(candidate, known_local_domain=known_local_domain)
    text = _gap_text_blob(candidate)
    direct_pressure = bool(_gap_direct_pressure_hits(text))
    wrapper_kind = _nonempty(candidate.get("wrapper_kind") or _gap_wrapper_kind(candidate))
    secondary_queries_generated = list(candidate.get("secondary_queries_generated") or _gap_secondary_queries_from_wrapper(candidate))
    resource_only = "resource/donation framing" in penalties
    donation_wrapper = bool(wrapper_kind)
    source_role = "discovery_lead" if donation_wrapper else "discovery_candidate"
    public_eligible = bool(direct_pressure and score >= 4 and not donation_wrapper)
    if known_status in {"already_included", "already_excluded", "duplicate"}:
        classification = "duplicate_or_known"
    elif wrapper_kind and (direct_pressure or secondary_queries_generated):
        classification = "needs_review"
    elif direct_pressure and score >= 4:
        classification = "likely_qualifying"
    elif resource_only:
        classification = "likely_resource_only"
    elif score <= 1:
        classification = "likely_resource_only"
    elif score >= 4 and not direct_pressure:
        classification = "needs_review"
    else:
        classification = "needs_review"
    reason_bits = list(reasons)
    reason_bits.extend(penalties)
    if known_status == "already_included":
        reason_bits.append("already included")
    elif known_status == "already_excluded":
        reason_bits.append("already excluded")
    elif known_status == "duplicate":
        reason_bits.append("duplicate of known included source")
    elif known_status == "known_domain_new_article":
        reason_bits.append("known domain new article")
    elif known_status == "unknown_domain_new_article":
        reason_bits.append("unknown domain new article")
    if wrapper_kind:
        reason_bits.append(f"wrapper lead detected: {wrapper_kind}")
    if classification == "likely_resource_only" and not direct_pressure:
        reason_bits.append("resource/donation framing without direct pressure evidence")
    elif classification == "needs_review" and wrapper_kind:
        if direct_pressure:
            reason_bits.append("wrapper framing with pressure clues; use secondary queries instead of publication")
        elif secondary_queries_generated:
            reason_bits.append("wrapper framing generated secondary queries for follow-up discovery")
    elif classification == "needs_review" and resource_only and direct_pressure:
        reason_bits.append("mixed resource and pressure signals; needs review")
    return {
        "classification": classification,
        "score": score,
        "reason": "; ".join(reason_bits) if reason_bits else "no strong pressure markers",
        "known_status": known_status,
        "source_role": source_role,
        "donation_wrapper": donation_wrapper,
        "public_eligible": public_eligible,
        "wrapper_kind": wrapper_kind,
        "secondary_queries_generated": secondary_queries_generated,
    }


def _gap_is_google_news_url(url: str) -> bool:
    value = _gap_normalize_url(url)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.netloc.lower() in {"news.google.com", "www.news.google.com"} or "news.google.com" in value.lower()


def _gap_traceable_review_url(candidate: dict[str, Any]) -> str:
    for key in ("resolved_url", "url", "normalized_url"):
        value = _gap_normalize_url(_nonempty(candidate.get(key)))
        if value and not _gap_is_google_news_url(value):
            return value
    return ""


def _gap_review_traceability_status(candidate: dict[str, Any]) -> str:
    traceable_url = _gap_traceable_review_url(candidate)
    if traceable_url:
        return "traceable_article_url"
    google_news_url = _gap_normalize_url(_nonempty(candidate.get("google_news_url") or candidate.get("url")))
    if google_news_url and _gap_is_google_news_url(google_news_url):
        return "unresolved_google_news"
    if _gap_normalize_url(_nonempty(candidate.get("url") or candidate.get("normalized_url"))):
        return "source_wrapper_only"
    return "missing_review_url"


def _gap_candidate_is_publication_blocking(candidate: dict[str, Any]) -> bool:
    if str(candidate.get("classification") or "").strip() != "likely_qualifying":
        return False
    if not _gap_traceable_review_url(candidate):
        return False
    if not bool(candidate.get("direct_url_date_verified")):
        return False
    return bool(_nonempty(candidate.get("title")) and _nonempty(candidate.get("publisher")))


def _gap_candidate_is_high_confidence_unresolved_direct_pressure(candidate: dict[str, Any]) -> bool:
    if str(candidate.get("classification") or "").strip() != "likely_qualifying":
        return False
    if bool(candidate.get("publication_blocking_candidate")):
        return False
    if str(candidate.get("blocking_candidate_severity") or "").strip() != "soft_block":
        return False
    if str(candidate.get("review_traceability_status") or "").strip() != "unresolved_google_news":
        return False
    if _nonempty(candidate.get("wrapper_kind")):
        return False
    try:
        score = int(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score < FOOD_LINE_DISCOVERY_GAP_UNRESOLVED_DIRECT_PRESSURE_BLOCK_SCORE:
        return False
    reason_text = str(candidate.get("reason") or "").lower()
    return "direct pressure signal" in reason_text


def _gap_markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_None._"
    lines = [
        "| Title | Publisher/domain | Direct URL | Google News wrapper | Date confidence | Block severity | Recommended manual action | URL resolution | Query | Score | Reason | Known status |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        publisher = _normalize_source_text(str(row.get("publisher") or "")).replace("|", "\\|")
        publisher_domain = _normalize_source_text(str(row.get("publisher_domain") or "")).replace("|", "\\|")
        publisher_cell = publisher
        if publisher_domain and publisher_domain.lower() not in publisher.lower():
            publisher_cell = f"{publisher} ({publisher_domain})" if publisher else publisher_domain
        direct_url = _normalize_source_text(str(row.get("direct_url") or "")).replace("|", "\\|")
        google_news_url = _normalize_source_text(
            str(row.get("google_news_url") or (row.get("url") if _gap_is_google_news_url(str(row.get("url") or "")) else ""))
        ).replace("|", "\\|")
        date_confidence = _normalize_source_text(str(row.get("date_confidence") or "")).replace("|", "\\|")
        block_severity = _normalize_source_text(str(row.get("blocking_candidate_severity") or "review_only")).replace("|", "\\|")
        recommended_manual_action = _normalize_source_text(str(row.get("recommended_manual_action") or "")).replace("|", "\\|")
        url_resolution = _normalize_source_text(str(row.get("url_resolution_status") or "")).replace("|", "\\|")
        url_resolution_reason = _normalize_source_text(str(row.get("url_resolution_reason") or "")).replace("|", "\\|")
        if url_resolution_reason:
            url_resolution = f"{url_resolution}: {url_resolution_reason}" if url_resolution else url_resolution_reason
        row_values = [
            _normalize_source_text(str(row.get("title") or "")).replace("|", "\\|"),
            publisher_cell,
            direct_url,
            google_news_url,
            date_confidence,
            block_severity,
            recommended_manual_action,
            url_resolution,
            _normalize_source_text(str(row.get("discovered_query") or "")).replace("|", "\\|"),
            _normalize_source_text(str(row.get("score") or "")).replace("|", "\\|"),
            _normalize_source_text(str(row.get("reason") or "")).replace("|", "\\|"),
            _normalize_source_text(str(row.get("known_status") or "")).replace("|", "\\|"),
        ]
        lines.append("| " + " | ".join(row_values) + " |")
    return "\n".join(lines)


def _gap_known_status_sets(root: Path) -> dict[str, set[str]]:
    registry_rows = load_food_line_registry(root)
    candidate_rows = load_food_line_candidate_registry(root)
    priority = _load_discovery_priority(root)
    priority_domains = {str(item).strip().lower() for item in priority.get("priority_domains") or [] if str(item).strip()}
    included_urls: set[str] = set()
    excluded_urls: set[str] = set()
    known_urls: set[str] = set()
    known_domains: set[str] = set(priority_domains)
    known_publishers: set[str] = set()
    for row in registry_rows:
        url = _gap_normalize_url(_nonempty(row.get("url") or row.get("candidate_url")))
        if url:
            known_urls.add(url)
            known_domains.add(_gap_domain(url))
            if _truthy(row.get("enabled"), default=True) or str(row.get("status") or "").lower() in {"enabled", "promoted"}:
                included_urls.add(url)
            else:
                excluded_urls.add(url)
        publisher = _nonempty(row.get("publisher") or row.get("source_name") or row.get("name"))
        if publisher:
            known_publishers.add(publisher.lower())
    for row in candidate_rows:
        url = _gap_normalize_url(_nonempty(row.get("candidate_url") or row.get("url")))
        if url:
            known_urls.add(url)
            known_domains.add(_gap_domain(url))
            status = str(row.get("status") or "").strip().lower()
            if status in {"rejected", "quarantined", "archived", "tested_failed"}:
                excluded_urls.add(url)
        publisher = _nonempty(row.get("publisher") or row.get("source_name") or row.get("name"))
        if publisher:
            known_publishers.add(publisher.lower())
    return {
        "included_urls": included_urls,
        "excluded_urls": excluded_urls,
        "known_urls": known_urls,
        "known_domains": {domain for domain in known_domains if domain},
        "known_publishers": known_publishers,
    }


def run_food_line_discovery_gap_check(
    root: Path,
    date: str,
    *,
    fetcher: Any | None = None,
    max_results_per_query: int = 10,
    max_queries: int | None = None,
    max_candidates: int | None = None,
    resolver_timeout_seconds: int = 15,
    skip_sitemap_fallback: bool = False,
    max_sitemap_lookups_per_domain: int = 2,
    max_sitemap_urls_per_domain: int = 50,
    fast: bool = False,
) -> dict[str, Any]:
    start = time.monotonic()
    edition_date = validate_date(date)
    config = load_food_line_discovery_gap_queries(root)
    query_terms = list(config.get("queries") or [])
    query_terms.extend(row["query"] for row in _date_bounded_queries(date))
    query_terms = list(dict.fromkeys(query_terms))
    initial_query_terms = list(query_terms)
    pending_queries = list(query_terms)
    executed_queries: list[str] = []
    secondary_query_terms: list[str] = []
    max_secondary_queries = 12
    if fast:
        resolver_timeout_seconds = min(resolver_timeout_seconds, 8)
        skip_sitemap_fallback = True
        if max_candidates is None:
            max_candidates = 25
        max_sitemap_lookups_per_domain = min(max_sitemap_lookups_per_domain, 1)
        max_sitemap_urls_per_domain = min(max_sitemap_urls_per_domain, 20)
    resolver_state = _gap_new_resolver_state(
        max_candidates=max_candidates,
        timeout_seconds=resolver_timeout_seconds,
        skip_sitemap_fallback=skip_sitemap_fallback,
        max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
        max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
    )
    exclude_domains = {str(item).strip().lower() for item in config.get("exclude_domains") or [] if str(item).strip()}
    fetch = resolve_food_line_fetcher(fetcher)
    known = _gap_known_status_sets(root)
    query_errors: list[dict[str, str]] = []
    raw_candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    discovered_at = _utc_now()
    while pending_queries:
        query = pending_queries.pop(0)
        if query in executed_queries:
            continue
        if max_queries is not None and max_queries >= 0 and len(executed_queries) >= max_queries:
            break
        executed_queries.append(query)
        rss_url = _gap_query_url(query)
        payload, fetch_error = _fetch_url(fetch, rss_url)
        if fetch_error or not payload:
            query_errors.append({"query": query, "url": rss_url, "error": fetch_error or "empty response"})
            continue
        try:
            rss_items = _gap_parse_rss_items(
                payload,
                resolver_state=resolver_state,
                resolver_timeout_seconds=resolver_timeout_seconds,
                skip_sitemap_fallback=skip_sitemap_fallback,
                max_sitemap_lookups_per_domain=max_sitemap_lookups_per_domain,
                max_sitemap_urls_per_domain=max_sitemap_urls_per_domain,
                edition_date=edition_date,
            )
        except Exception as exc:  # noqa: BLE001
            query_errors.append({"query": query, "url": rss_url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for item in rss_items[:max_results_per_query]:
            candidate_url = _gap_normalize_url(_nonempty(item.get("candidate_url")))
            if not candidate_url:
                continue
            publisher = _nonempty(item.get("publisher") or _gap_domain(candidate_url))
            publisher_url = _gap_normalize_url(_nonempty(item.get("publisher_url")))
            wrapper_kind = _gap_wrapper_kind(item)
            wrapper_secondary_queries = _gap_secondary_queries_from_wrapper(item)
            for secondary_query in wrapper_secondary_queries:
                if len(secondary_query_terms) >= max_secondary_queries:
                    break
                if max_queries is not None and max_queries >= 0 and len(executed_queries) + len(pending_queries) >= max_queries:
                    break
                if secondary_query not in executed_queries and secondary_query not in pending_queries and secondary_query not in secondary_query_terms:
                    pending_queries.append(secondary_query)
                    secondary_query_terms.append(secondary_query)
            normalized_url = candidate_url
            known_status = "unknown_domain_new_article"
            if normalized_url in known["included_urls"]:
                known_status = "already_included"
            elif normalized_url in known["excluded_urls"]:
                known_status = "already_excluded"
            elif normalized_url in seen_urls or normalized_url in known["known_urls"]:
                known_status = "duplicate"
            else:
                candidate_domain = _gap_domain(publisher_url or candidate_url)
                if candidate_domain in exclude_domains or _gap_domain(candidate_url) in exclude_domains:
                    known_status = "already_excluded"
                elif candidate_domain in known["known_domains"] or publisher.lower() in known["known_publishers"]:
                    known_status = "known_domain_new_article"
                else:
                    known_status = "unknown_domain_new_article"
            seen_urls.add(normalized_url)
            raw_candidates.append(
                {
                    "title": _nonempty(item.get("title")),
                    "publisher": publisher,
                    "publisher_domain": _gap_domain(publisher_url) or _gap_domain(candidate_url) or publisher,
                    "domain": _gap_domain(str(item.get("resolved_url") or candidate_url)) or _gap_domain(candidate_url) or publisher,
                    "publisher_url": publisher_url,
            "google_news_url": _nonempty(item.get("google_news_url") or item.get("link_url") or ""),
            "resolved_url": _nonempty(item.get("resolved_url") or ""),
            "url_resolution_status": _nonempty(item.get("url_resolution_status") or ""),
            "url_resolution_reason": _nonempty(item.get("url_resolution_reason") or ""),
            "url": candidate_url,
            "normalized_url": normalized_url,
                    "discovered_query": query,
                    "discovered_at": discovered_at,
                    "published_at": _nonempty(item.get("published_at")),
                    "summary_or_snippet": _nonempty(item.get("summary_or_snippet")),
                    "date_confidence": int(item.get("date_confidence") or 0),
                    "direct_url_date_verified": bool(item.get("direct_url_date_verified")),
                    "verified_date": _nonempty(item.get("verified_date")),
                    "date_verification_reason": _nonempty(item.get("date_verification_reason")),
                    "known_status": known_status,
                    "query_url": rss_url,
                    "raw_candidate": dict(item),
                    "wrapper_kind": wrapper_kind,
                    "pressure_clues_found": _gap_direct_pressure_hits(_gap_text_blob(item)),
                    "secondary_queries_generated": wrapper_secondary_queries,
                }
            )
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in raw_candidates:
        normalized_url = str(candidate.get("normalized_url") or "").strip()
        if not normalized_url:
            continue
        current = grouped.get(normalized_url)
        if current is None:
            current = dict(candidate)
            current["discovered_queries"] = [candidate["discovered_query"]]
            grouped[normalized_url] = current
        else:
            current["discovered_queries"].append(candidate["discovered_query"])
            if current.get("published_at") and not candidate.get("published_at"):
                pass
            elif candidate.get("published_at") and not current.get("published_at"):
                current["published_at"] = candidate.get("published_at")
            if len(str(candidate.get("summary_or_snippet") or "")) > len(str(current.get("summary_or_snippet") or "")):
                current["summary_or_snippet"] = candidate.get("summary_or_snippet")
            if current.get("known_status") == "unknown_domain_new_article" and candidate.get("known_status") != "unknown_domain_new_article":
                current["known_status"] = candidate.get("known_status")
            if not current.get("wrapper_kind") and candidate.get("wrapper_kind"):
                current["wrapper_kind"] = candidate.get("wrapper_kind")
            current["pressure_clues_found"] = list(
                dict.fromkeys([*(current.get("pressure_clues_found") or []), *(candidate.get("pressure_clues_found") or [])])
            )
            current["secondary_queries_generated"] = list(
                dict.fromkeys([*(current.get("secondary_queries_generated") or []), *(candidate.get("secondary_queries_generated") or [])])
            )
        current["discovered_queries"] = list(dict.fromkeys(current.get("discovered_queries") or []))
    candidates: list[dict[str, Any]] = []
    known_local_domains = known["known_domains"]
    for candidate in grouped.values():
        normalized_url = str(candidate.get("normalized_url") or "").strip()
        candidate_domain = _gap_domain(str(candidate.get("publisher_url") or "")) or _gap_domain(normalized_url)
        known_status = "unknown_domain_new_article"
        publisher_name = str(candidate.get("publisher") or "").strip().lower()
        if normalized_url in known["included_urls"]:
            known_status = "already_included"
        elif normalized_url in known["excluded_urls"]:
            known_status = "already_excluded"
        elif normalized_url in known["known_urls"]:
            known_status = "duplicate"
        elif candidate_domain and (candidate_domain in exclude_domains):
            known_status = "already_excluded"
        elif candidate_domain in known["known_domains"] or publisher_name in known["known_publishers"]:
            known_status = "known_domain_new_article"
        classification = classify_food_line_discovery_gap_candidate(
            candidate,
            known_status=known_status,
            known_local_domain=bool(candidate_domain and candidate_domain in known_local_domains),
        )
        row = {
            "title": candidate.get("title") or "",
            "publisher": candidate.get("publisher") or "",
            "source_url": candidate.get("source_url") or (candidate.get("resolution_diagnostics") or {}).get("source_url") or "",
            "publisher_domain": candidate.get("publisher_domain") or candidate_domain or "",
            "domain": candidate.get("domain") or candidate_domain or "",
            "url": normalized_url,
            "normalized_url": normalized_url,
            "google_news_url": candidate.get("google_news_url") or "",
            "resolved_url": candidate.get("resolved_url") or "",
            "url_resolution_status": candidate.get("url_resolution_status") or "",
            "url_resolution_reason": candidate.get("url_resolution_reason") or "",
            "direct_url": candidate.get("resolved_url") or (normalized_url if not _gap_is_google_news_url(normalized_url) else ""),
            "discovered_query": candidate.get("discovered_queries", [candidate.get("discovered_query") or ""])[0] or "",
            "discovered_queries": candidate.get("discovered_queries") or [],
            "discovered_at": candidate.get("discovered_at") or discovered_at,
            "published_at": candidate.get("published_at") or "",
            "summary_or_snippet": candidate.get("summary_or_snippet") or "",
            "known_status": known_status,
            "wrapper_kind": candidate.get("wrapper_kind") or "",
            "pressure_clues_found": candidate.get("pressure_clues_found") or [],
            "secondary_queries_generated": candidate.get("secondary_queries_generated") or [],
            "date_confidence": int(candidate.get("date_confidence") or 0),
            "direct_url_date_verified": bool(candidate.get("direct_url_date_verified")),
            "verified_date": candidate.get("verified_date") or "",
            "date_verification_reason": candidate.get("date_verification_reason") or "",
            "resolution_diagnostics": candidate.get("resolution_diagnostics") or {},
            "source_role": classification["source_role"],
            "donation_wrapper": bool(classification["donation_wrapper"]),
            "public_eligible": bool(classification["public_eligible"]),
            "score": classification["score"],
            "reason": classification["reason"],
            "classification": classification["classification"],
        }
        row["blocking_candidate_severity"] = _gap_blocking_candidate_severity(row)
        row["recommended_manual_action"] = _gap_manual_action_for_severity(str(row.get("blocking_candidate_severity") or "review_only"))
        row["traceable_review_url"] = _gap_traceable_review_url(row)
        row["review_traceability_status"] = _gap_review_traceability_status(row)
        row["publication_blocking_candidate"] = _gap_candidate_is_publication_blocking(row)
        candidates.append(row)
    severity_rank = {"hard_block": 3, "soft_block": 2, "review_only": 1}
    candidates.sort(
        key=lambda row: (
            -severity_rank.get(str(row.get("blocking_candidate_severity") or "review_only"), 1),
            -int(row.get("date_confidence") or 0),
            -int(row.get("score") or 0),
            str(row.get("title") or ""),
            str(row.get("url") or ""),
        )
    )
    wrapper_candidate_count = sum(1 for row in candidates if _nonempty(row.get("wrapper_kind")))
    grouped_by_class = {
        "likely_qualifying": [row for row in candidates if row["classification"] == "likely_qualifying"],
        "needs_review": [row for row in candidates if row["classification"] == "needs_review"],
        "likely_resource_only": [row for row in candidates if row["classification"] == "likely_resource_only"],
        "duplicate_or_known": [row for row in candidates if row["classification"] == "duplicate_or_known"],
    }
    blocking_likely_qualifying = [row for row in grouped_by_class["likely_qualifying"] if bool(row.get("publication_blocking_candidate"))]
    unresolved_likely_qualifying = [row for row in grouped_by_class["likely_qualifying"] if not bool(row.get("publication_blocking_candidate"))]
    unresolved_high_confidence_direct_pressure = [
        row for row in unresolved_likely_qualifying if _gap_candidate_is_high_confidence_unresolved_direct_pressure(row)
    ]
    unresolved_high_confidence_direct_pressure_titles = [
        str(row.get("title") or "").strip()
        for row in unresolved_high_confidence_direct_pressure[:5]
        if str(row.get("title") or "").strip()
    ]
    hard_block_rows = [row for row in candidates if str(row.get("blocking_candidate_severity") or "") == "hard_block"]
    soft_block_rows = [row for row in candidates if str(row.get("blocking_candidate_severity") or "") == "soft_block"]
    review_only_rows = [row for row in candidates if str(row.get("blocking_candidate_severity") or "") == "review_only"]
    direct_url_date_verified_count = sum(1 for row in candidates if bool(row.get("direct_url_date_verified")))
    high_confidence_attempt_rows = soft_block_rows[:10]
    report_dir = root / "data" / "dispatches" / "food-line" / "discovery_gap" / edition_date
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "discovery_gap_report.json"
    report_md_path = report_dir / "discovery_gap_report.md"
    report = {
        "date": edition_date,
        "generated_at": discovered_at,
        "query_source": "google_news_rss",
        "query_count": len(executed_queries),
        "queries": executed_queries,
        "initial_queries": initial_query_terms,
        "secondary_query_count": len(secondary_query_terms),
        "secondary_queries": secondary_query_terms,
        "wrapper_candidate_count": wrapper_candidate_count,
        "exclude_domains": sorted(exclude_domains),
        "candidate_count": len(candidates),
        "likely_qualifying_count": len(grouped_by_class["likely_qualifying"]),
        "blocking_likely_qualifying_count": len(blocking_likely_qualifying),
        "hard_block_count": len(hard_block_rows),
        "soft_block_count": len(soft_block_rows),
        "review_only_count": len(review_only_rows),
        "unresolved_likely_qualifying_count": len(unresolved_likely_qualifying),
        "unresolved_high_confidence_direct_pressure_count": len(unresolved_high_confidence_direct_pressure),
        "unresolved_high_confidence_direct_pressure_titles": unresolved_high_confidence_direct_pressure_titles,
        "manual_review_only_count": len(grouped_by_class["needs_review"]),
        "needs_review_count": len(grouped_by_class["needs_review"]),
        "likely_resource_only_count": len(grouped_by_class["likely_resource_only"]),
        "duplicate_or_known_count": len(grouped_by_class["duplicate_or_known"]),
        "date_confidence_values": [int(row.get("date_confidence") or 0) for row in candidates],
        "direct_url_date_verified_count": direct_url_date_verified_count,
        "resolved_url_count": int(resolver_state.get("resolved_url_count") or 0),
        "unresolved_url_count": int(resolver_state.get("unresolved_url_count") or 0),
        "rejected_unrelated_resolved_url_count": int(resolver_state.get("rejected_unrelated_resolved_url_count") or 0),
        "url_resolution_timeout_count": int(resolver_state.get("url_resolution_timeout_count") or 0),
        "sitemap_lookup_count": int(resolver_state.get("sitemap_lookup_count") or 0),
        "sitemap_cache_hit_count": int(resolver_state.get("sitemap_cache_hit_count") or 0),
        "general_resolution_attempt_count": int(resolver_state.get("general_resolution_attempt_count") or 0),
        "reserved_soft_block_resolution_attempt_count": int(resolver_state.get("reserved_soft_block_resolution_attempt_count") or 0),
        "reserved_soft_block_resolution_skipped_count": int(resolver_state.get("reserved_soft_block_resolution_skipped_count") or 0),
        "reserved_soft_block_exact_title_attempt_count": int(resolver_state.get("reserved_soft_block_exact_title_attempt_count") or 0),
        "high_confidence_url_resolution_attempt_count": len(high_confidence_attempt_rows),
        "high_confidence_url_resolution_attempts": [
            {
                "title": row.get("title") or "",
                "publisher": row.get("publisher") or "",
                "publisher_domain": row.get("publisher_domain") or "",
                "source_url": row.get("source_url") or row.get("publisher_url") or (row.get("resolution_diagnostics") or {}).get("source_url") or "",
                "publisher_url": row.get("publisher_url") or "",
                "google_news_url": row.get("google_news_url") or "",
                "url_resolution_status": row.get("url_resolution_status") or "",
                "url_resolution_reason": row.get("url_resolution_reason") or "",
                "resolution_budget_mode": (row.get("resolution_diagnostics") or {}).get("resolution_budget_mode") or "",
                "resolution_budget_status": (row.get("resolution_diagnostics") or {}).get("resolution_budget_status") or "",
                "resolution_skip_reason": (row.get("resolution_diagnostics") or {}).get("resolution_skip_reason") or "",
                "reserved_soft_block_candidate": bool((row.get("resolution_diagnostics") or {}).get("reserved_soft_block_candidate")),
                "reserved_soft_block_candidate_rank": int((row.get("resolution_diagnostics") or {}).get("reserved_soft_block_candidate_rank") or 0),
                "exact_title_attempted": bool((row.get("resolution_diagnostics") or {}).get("exact_title_attempted")),
                "exact_title_failure_cause": (row.get("resolution_diagnostics") or {}).get("exact_title_failure_cause") or "",
                "exact_title_origin": (row.get("resolution_diagnostics") or {}).get("exact_title_origin") or "",
                "sitemap_lookup_count_delta": int((row.get("resolution_diagnostics") or {}).get("sitemap_lookup_count_delta") or 0),
                "sitemap_urls_checked_count": int((row.get("resolution_diagnostics") or {}).get("sitemap_urls_checked_count") or 0),
                "exact_title_scored_url_count": int((row.get("resolution_diagnostics") or {}).get("exact_title_scored_url_count") or 0),
                "resolution_mode": (row.get("resolution_diagnostics") or {}).get("resolution_mode") or "",
                "failure_reason": (row.get("resolution_diagnostics") or {}).get("failure_reason") or "",
                "blocking_candidate_severity": row.get("blocking_candidate_severity") or "",
                "date_confidence": int(row.get("date_confidence") or 0),
                "score": int(row.get("score") or 0),
            }
            for row in high_confidence_attempt_rows
        ],
        "public_no_qualifying_update_validated": len(hard_block_rows) == 0 and len(soft_block_rows) == 0,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "query_errors": query_errors,
        "candidates": candidates,
        "summary": {
            "candidates_reviewed": len(candidates),
            "likely_qualifying": len(grouped_by_class["likely_qualifying"]),
            "blocking_likely_qualifying": len(blocking_likely_qualifying),
            "hard_block": len(hard_block_rows),
            "soft_block": len(soft_block_rows),
            "review_only": len(review_only_rows),
            "unresolved_likely_qualifying": len(unresolved_likely_qualifying),
            "unresolved_high_confidence_direct_pressure": len(unresolved_high_confidence_direct_pressure),
            "manual_review_only": len(grouped_by_class["needs_review"]),
            "needs_review": len(grouped_by_class["needs_review"]),
            "already_known": len(grouped_by_class["duplicate_or_known"]),
            "likely_resource_only": len(grouped_by_class["likely_resource_only"]),
        },
    }
    _write_json_object(report_json_path, report)
    md_lines = [
        f"# Food Line Discovery Gap Check — {edition_date}",
        "",
        "## Likely qualifying candidates",
        "",
        _gap_markdown_table(grouped_by_class["likely_qualifying"]),
        "",
        "## Needs review",
        "",
        _gap_markdown_table(grouped_by_class["needs_review"]),
        "",
        "## Likely resource-only",
        "",
        _gap_markdown_table(grouped_by_class["likely_resource_only"]),
        "",
        "## Duplicate or already known",
        "",
        _gap_markdown_table(grouped_by_class["duplicate_or_known"]),
        "",
    ]
    if high_confidence_attempt_rows:
        md_lines.extend(
            [
                "## High-confidence URL resolution attempts",
                "",
                "| Title | Publisher/domain | Source URL | Google News wrapper | Budget mode | Skip reason | Exact-title attempted | Failure cause | Sitemap URLs checked | URL resolution |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for row in high_confidence_attempt_rows:
            diag = row.get("resolution_diagnostics") or {}
            publisher_cell = _normalize_source_text(str(row.get("publisher") or "")).replace("|", "\\|")
            publisher_domain = _normalize_source_text(str(row.get("publisher_domain") or "")).replace("|", "\\|")
            source_url = _normalize_source_text(
                str(row.get("source_url") or row.get("publisher_url") or (row.get("resolution_diagnostics") or {}).get("source_url") or "")
            ).replace("|", "\\|")
            if publisher_domain and publisher_domain.lower() not in publisher_cell.lower():
                publisher_cell = f"{publisher_cell} ({publisher_domain})" if publisher_cell else publisher_domain
            md_lines.append(
                "| "
                + " | ".join(
                    [
                        _normalize_source_text(str(row.get("title") or "")).replace("|", "\\|"),
                        publisher_cell,
                        source_url,
                        _normalize_source_text(str(row.get("google_news_url") or "")).replace("|", "\\|"),
                        _normalize_source_text(str(diag.get("resolution_budget_mode") or "")).replace("|", "\\|"),
                        _normalize_source_text(str(diag.get("resolution_skip_reason") or "")).replace("|", "\\|"),
                        str(bool(diag.get("exact_title_attempted"))).lower(),
                        _normalize_source_text(str(diag.get("failure_reason") or row.get("url_resolution_reason") or "")).replace("|", "\\|"),
                        str(int(diag.get("sitemap_urls_checked_count") or 0)),
                        _normalize_source_text(
                            f"{row.get('url_resolution_status') or ''}"
                            f"{(': ' + str(row.get('url_resolution_reason') or '') if row.get('url_resolution_reason') else '')}"
                        ).replace("|", "\\|"),
                    ]
                )
                + " |"
            )
        md_lines.extend(["", "## Summary"])
    else:
        md_lines.extend(["## Summary"])
    md_lines.extend([
        f"- candidates reviewed: {len(candidates)}",
        f"- likely qualifying: {len(grouped_by_class['likely_qualifying'])}",
        f"- blocking likely qualifying: {len(blocking_likely_qualifying)}",
        f"- hard block: {len(hard_block_rows)}",
        f"- soft block: {len(soft_block_rows)}",
        f"- review only: {len(review_only_rows)}",
        f"- unresolved likely qualifying: {len(unresolved_likely_qualifying)}",
        f"- unresolved high-confidence direct-pressure: {len(unresolved_high_confidence_direct_pressure)}",
        f"- direct URL date verified: {direct_url_date_verified_count}",
        f"- general resolution attempts: {int(resolver_state.get('general_resolution_attempt_count') or 0)}",
        f"- reserved soft-block resolution attempts: {int(resolver_state.get('reserved_soft_block_resolution_attempt_count') or 0)}",
        f"- reserved soft-block resolution skipped: {int(resolver_state.get('reserved_soft_block_resolution_skipped_count') or 0)}",
        f"- reserved soft-block exact-title attempts: {int(resolver_state.get('reserved_soft_block_exact_title_attempt_count') or 0)}",
        f"- manual-review-only: {len(grouped_by_class['needs_review'])}",
        f"- needs review: {len(grouped_by_class['needs_review'])}",
        f"- already known: {len(grouped_by_class['duplicate_or_known'])}",
        f"- likely resource-only: {len(grouped_by_class['likely_resource_only'])}",
    ])
    report_md_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")
    summary = {
        "ok": True,
        "date": edition_date,
        "query_count": len(executed_queries),
        "candidate_count": len(candidates),
        "wrapper_candidate_count": wrapper_candidate_count,
        "resolved_url_count": int(resolver_state.get("resolved_url_count") or 0),
        "unresolved_url_count": int(resolver_state.get("unresolved_url_count") or 0),
        "rejected_unrelated_resolved_url_count": int(resolver_state.get("rejected_unrelated_resolved_url_count") or 0),
        "url_resolution_timeout_count": int(resolver_state.get("url_resolution_timeout_count") or 0),
        "sitemap_lookup_count": int(resolver_state.get("sitemap_lookup_count") or 0),
        "sitemap_cache_hit_count": int(resolver_state.get("sitemap_cache_hit_count") or 0),
        "general_resolution_attempt_count": int(resolver_state.get("general_resolution_attempt_count") or 0),
        "reserved_soft_block_resolution_attempt_count": int(resolver_state.get("reserved_soft_block_resolution_attempt_count") or 0),
        "reserved_soft_block_resolution_skipped_count": int(resolver_state.get("reserved_soft_block_resolution_skipped_count") or 0),
        "reserved_soft_block_exact_title_attempt_count": int(resolver_state.get("reserved_soft_block_exact_title_attempt_count") or 0),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "likely_qualifying_count": len(grouped_by_class["likely_qualifying"]),
        "blocking_likely_qualifying_count": len(blocking_likely_qualifying),
        "unresolved_likely_qualifying_count": len(unresolved_likely_qualifying),
        "unresolved_high_confidence_direct_pressure_count": len(unresolved_high_confidence_direct_pressure),
        "unresolved_high_confidence_direct_pressure_titles": unresolved_high_confidence_direct_pressure_titles,
        "manual_review_only_count": len(grouped_by_class["needs_review"]),
        "needs_review_count": len(grouped_by_class["needs_review"]),
        "likely_resource_only_count": len(grouped_by_class["likely_resource_only"]),
        "duplicate_or_known_count": len(grouped_by_class["duplicate_or_known"]),
        "report_path": str(report_json_path),
        "report_markdown_path": str(report_md_path),
        "query_errors": query_errors,
        "queries": executed_queries,
        "initial_queries": initial_query_terms,
        "secondary_query_count": len(secondary_query_terms),
        "secondary_queries": secondary_query_terms,
        "query_source": "google_news_rss",
        "published_pages": False,
        "bluesky_posted": False,
    }
    print(json.dumps(summary, indent=2))
    return summary


def _expand_queries(queries: list[dict[str, Any]], states: list[str]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for query in queries:
        template = str(query.get("query_template") or query.get("template") or "")
        for state in states:
            expanded.append(
                {
                    "state": state,
                    "template": template,
                    "query_template": query.get("query_template") or template,
                    "query": template.format(state=state),
                    "category": query.get("category") or "",
                    "source_family": query.get("source_family") or "",
                }
            )
    return expanded


def _date_bounded_queries(edition_date: str) -> list[dict[str, Any]]:
    try:
        day = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    except ValueError:
        return []
    after = (day - timedelta(days=1)).isoformat()
    before = (day + timedelta(days=1)).isoformat()
    rows: list[dict[str, Any]] = []
    for query, source_family, category in DATE_BOUNDED_QUERY_ROOTS:
        rows.append(
            {
                "template": f"{query} after:{after} before:{before}",
                "query_template": f"{query} after:{{after}} before:{{before}}",
                "query": f"{query} after:{after} before:{before}",
                "category": category,
                "source_family": source_family,
                "after": after,
                "before": before,
                "date_bounded": True,
            }
        )
    return rows


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    return payload


def _load_discovery_blocklist(root: Path) -> dict[str, list[str]]:
    path = _discovery_blocklist_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_blocklist.json"
    if not path.exists():
        path = repo_path
    payload = _load_json_object(path)
    return {
        "blocked_domains": [str(item).strip().lower() for item in payload.get("blocked_domains") or [] if str(item).strip()],
        "blocked_url_patterns": [str(item).strip().lower() for item in payload.get("blocked_url_patterns") or [] if str(item).strip()],
        "blocked_title_patterns": [str(item).strip().lower() for item in payload.get("blocked_title_patterns") or [] if str(item).strip()],
        "blocked_purposes": [str(item).strip().lower() for item in payload.get("blocked_purposes") or [] if str(item).strip()],
    }


def _load_discovery_priority(root: Path) -> dict[str, list[str]]:
    path = _discovery_priority_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json"
    if not path.exists():
        path = repo_path
    payload = _load_json_object(path)
    return {
        "priority_domains": [str(item).strip().lower() for item in payload.get("priority_domains") or [] if str(item).strip()],
        "priority_source_families": [str(item).strip().lower() for item in payload.get("priority_source_families") or [] if str(item).strip()],
        "priority_states": [str(item).strip().upper() for item in payload.get("priority_states") or [] if str(item).strip()],
    }


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _pattern_hit(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(pattern and pattern in lowered for pattern in patterns)


def _blocked_by_discovery_rules(candidate: dict[str, Any], blocklist: dict[str, list[str]]) -> tuple[bool, str]:
    url = str(candidate.get("candidate_url") or candidate.get("url") or "")
    title = " ".join(
        part
        for part in (
            str(candidate.get("source_name") or ""),
            str(candidate.get("candidate_reason") or ""),
            str(candidate.get("notes") or ""),
        )
        if part
    )
    source_purpose = str(candidate.get("source_purpose") or "unknown").strip().lower()
    domain = _domain_from_url(url)
    if source_purpose in {purpose.lower() for purpose in blocklist.get("blocked_purposes") or []}:
        return True, f"blocked source purpose: {source_purpose}"
    if domain and any(blocked in domain for blocked in blocklist.get("blocked_domains") or []):
        return True, f"blocked domain: {domain}"
    if _pattern_hit(url.lower(), blocklist.get("blocked_url_patterns") or []):
        return True, "blocked url pattern"
    if _pattern_hit(title.lower(), blocklist.get("blocked_title_patterns") or []):
        return True, "blocked title pattern"
    return False, ""


def _priority_bonus(candidate: dict[str, Any], priority: dict[str, list[str]]) -> int:
    bonus = 0
    url = str(candidate.get("candidate_url") or candidate.get("url") or "").lower()
    domain = _domain_from_url(url)
    family = str(candidate.get("source_family") or "").strip().lower()
    state = str(candidate.get("state") or "").strip().upper()
    priority_domains = {item.lower() for item in priority.get("priority_domains") or [] if item}
    if domain and (domain in priority_domains or any(domain.endswith(f".{item}") for item in priority_domains)):
        bonus += 15
    if family in {item.lower() for item in priority.get("priority_source_families") or [] if item}:
        bonus += 10
    if state in {item.upper() for item in priority.get("priority_states") or [] if item}:
        bonus += 5
    source_id = str(candidate.get("source_id") or "").strip().lower()
    if source_id == "miami-herald-local-news":
        bonus += 30
    return bonus


def _query_quality_score(row: dict[str, Any]) -> float:
    runs = int(row.get("runs") or 0)
    inserted = int(row.get("candidates_inserted") or 0)
    verified = int(row.get("candidates_verified_pressure") or 0)
    promoted = int(row.get("candidates_promoted") or 0)
    rejects = int(row.get("rejects") or 0)
    found = int(row.get("candidates_found") or 0)
    score = 0.0
    if runs:
        score += min(50.0, (inserted / runs) * 30.0)
        score += min(20.0, (verified / runs) * 20.0)
        score += min(10.0, (promoted / runs) * 10.0)
        score += min(10.0, (found / runs) * 5.0)
        score -= min(40.0, (rejects / runs) * 8.0)
    return max(0.0, min(100.0, round(score, 2)))


def _query_recommendation(row: dict[str, Any]) -> str:
    score = float(row.get("rolling_query_quality_score") or 0)
    runs = int(row.get("runs") or 0)
    if runs >= 3 and score < 20:
        return "skip"
    if score >= 55 or int(row.get("candidates_verified_pressure") or 0) > 0:
        return "prioritize"
    return "keep"


def _template_terms(template: str) -> list[str]:
    raw = re.sub(r"\{[^}]+\}", " ", template.lower())
    terms = []
    for token in re.findall(r"[a-z0-9]+", raw):
        if token in {"rss", "alert", "news", "update", "updates", "feed"}:
            continue
        if len(token) < 3:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _find_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for term in terms:
        if term and term.lower() in lowered and term not in hits:
            hits.append(term)
    return hits


def _resolve_url(base_url: str, href: str) -> str:
    href = html.unescape(str(href or "").strip())
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        parsed = urllib.parse.urlsplit(base_url)
        return f"{parsed.scheme}:{href}"
    return urllib.parse.urljoin(base_url, href)


def _is_article_like_url(url: str, *, seed_url: str = "", label: str = "") -> bool:
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path:
        return False
    lowered = f"{url.lower()} {label.lower()}"
    if any(token in lowered for token in ("#comment", "/tag/", "/category/", "/author/", "/search?", "/feed", "/rss", "/atom", "javascript:", "mailto:", "/wp-json/")):
        return False
    if url.rstrip("/") == str(seed_url or "").strip().rstrip("/"):
        return False
    if re.search(r"/20\d{2}[-/]\d{2}[-/]\d{2}/", path):
        return True
    if re.search(r"/regional-news/20\d{2}-\d{2}-\d{2}/", path):
        return True
    if re.search(r"/regional-news/\d{4}-\d{2}-\d{2}/", path):
        return True
    if re.search(r"/\d{4}/\d{2}/\d{2}/", path):
        return True
    return any(term in lowered for term in ("food bank", "food pantry", "snap", "wic", "food insecurity", "food assistance", "increased need", "rising demand"))


def _rank_discovered_link(link: dict[str, str], *, pressure_terms: list[str], query_terms: list[str], seed_url: str) -> int:
    url = str(link.get("url") or "").strip().lower()
    label = str(link.get("label") or "").strip().lower()
    kind = str(link.get("kind") or "").strip().lower()
    score = 0
    if url and url != seed_url.rstrip("/"):
        score += 5
    if kind == "sitemap":
        score += 10
    if re.search(r"/regional-news/20\d{2}-\d{2}-\d{2}/", url):
        score += 60
    if re.search(r"/20\d{2}/\d{2}/\d{2}/", url):
        score += 50
    if any(term in url for term in ("food-bank", "food-banks", "food pantry", "food-pantries", "snap", "wic", "food insecurity", "food assistance", "demand", "need", "federal cut", "increased")):
        score += 20
    if any(term.lower() in label for term in ("food", "snap", "wic", "pantry", "demand", "need", "increased", "banks")):
        score += 15
    if any(term.lower() in url for term in pressure_terms[:10]):
        score += 10
    if any(term.lower() in label for term in query_terms[:10]):
        score += 8
    return score


def _parse_html_links(payload: bytes, base_url: str) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    results: list[dict[str, str]] = []
    for match in re.finditer(r'<link\b[^>]*rel=["\']alternate["\'][^>]*href=["\']([^"\']+)["\'][^>]*>', text, re.IGNORECASE):
        href = html.unescape(match.group(1)).strip()
        if href.startswith(("http://", "https://")):
            results.append({"url": href, "kind": "rss_or_atom"})
    if re.search(r"<(?:urlset|sitemapindex)\b", text, re.IGNORECASE):
        for match in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.IGNORECASE):
            href = html.unescape(match.group(1)).strip()
            if href.startswith(("http://", "https://")):
                results.append({"url": href, "kind": "sitemap"})
    for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.IGNORECASE | re.DOTALL):
        href = html.unescape(match.group(1)).strip()
        label = _normalize_source_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))))
        resolved = _resolve_url(base_url, href)
        if not resolved:
            continue
        if _is_article_like_url(resolved, seed_url=base_url, label=label):
            results.append({"url": resolved, "kind": "link", "label": label})
    if "sitemap.xml" in text.lower():
        base = urllib.parse.urlsplit(base_url)
        results.append({"url": urllib.parse.urlunsplit((base.scheme, base.netloc, "/sitemap.xml", "", "")), "kind": "sitemap", "label": ""})
    return results


def _candidate_id(url: str, publisher: str, source_family: str) -> str:
    digest = hashlib.sha1(_normalize_url(url).encode("utf-8")).hexdigest()[:12]
    prefix = _slugify(publisher or source_family or "food-line")
    return f"{prefix}-{digest}"


def _inspect_candidate_page(fetcher: Any, url: str, *, seed_url: str = "") -> dict[str, str]:
    if not url or url == seed_url:
        return {
            "retrieved_at": _utc_now(),
            "published_at": "",
            "page_metadata_date": "",
            "page_title": "",
            "page_summary_or_snippet": "",
            "page_evidence_text": "",
            "page_evidence_text_basis": "",
            "page_fetch_error": "",
        }
    payload, fetch_error = _fetch_url(fetcher, url)
    retrieved_at = _utc_now()
    if fetch_error or not payload:
        return {
            "retrieved_at": retrieved_at,
            "published_at": "",
            "page_metadata_date": "",
            "page_title": "",
            "page_summary_or_snippet": "",
            "page_evidence_text": "",
            "page_evidence_text_basis": "",
            "page_fetch_error": fetch_error,
        }
    evidence = _extract_page_evidence(payload)
    page_metadata_date = _extract_page_metadata_date(payload)
    published_at = page_metadata_date[:10] if page_metadata_date else ""
    return {
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "page_metadata_date": page_metadata_date,
        "page_title": evidence.get("title") or "",
        "page_summary_or_snippet": evidence.get("summary_or_snippet") or "",
        "page_evidence_text": evidence.get("evidence_text") or "",
        "page_evidence_text_basis": evidence.get("evidence_text_basis") or "",
        "page_fetch_error": "",
    }


def _discovery_seed_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for row in _load_food_line_registry_rows(root):
        url = _nonempty(row.get("url") or row.get("candidate_url"))
        if not url:
            continue
        normalized = _normalize_url(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            {
                "source_id": _nonempty(row.get("source_id")),
                "source_name": _nonempty(row.get("source_name") or row.get("name") or row.get("title") or normalized),
                "publisher": _nonempty(row.get("publisher")),
                "candidate_url": url,
                "source_family": _nonempty(row.get("source_family")),
                "source_type": _nonempty(row.get("source_type") or "page"),
                "state": _nonempty(row.get("state") or "US").upper(),
                "location_name": _nonempty(row.get("location_name") or "United States"),
                "location_scope": _nonempty(row.get("location_scope") or ("national" if _nonempty(row.get("state") or "US").upper() in {"", "US"} else "state_local")),
                "status": _nonempty(row.get("status") or "candidate"),
                "notes": _nonempty(row.get("notes")),
            }
        )
    return rows


def _fetch_url(fetcher: Any, url: str) -> tuple[bytes, str]:
    try:
        payload = fetcher(url, timeout=15)
        return payload, ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


def _score_discovery(*, useful_text_available: bool, rss_or_atom_detected: bool, pressure_terms: list[str], negative_terms: list[str], source_type: str, fetched: bool) -> tuple[int, int]:
    score = 0
    if fetched:
        score += 15
    if useful_text_available:
        score += 20
    if rss_or_atom_detected:
        score += 25
    if pressure_terms:
        score += min(30, 10 + 5 * len(pressure_terms))
    if negative_terms:
        score -= min(35, 10 + 5 * len(negative_terms))
    if source_type == "rss":
        score += 10
    score = max(0, min(100, score))
    noise = max(0, min(100, 100 - score))
    return score, noise


def _prefilter_discovery_candidate(
    *,
    source_purpose: str,
    source_type: str,
    useful_text_available: bool,
    rss_or_atom_detected: bool,
    pressure_terms: list[str],
    negative_terms: list[str],
    source_family: str,
    blocked: bool = False,
) -> tuple[bool, str]:
    if blocked:
        return False, "rejected by discovery blocklist"
    if source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"}:
        return False, f"rejected by source purpose: {source_purpose}"
    if negative_terms and len(negative_terms) >= 2:
        return False, "rejected by noise: recipe/menu/restaurant/festival content"
    if not useful_text_available and not rss_or_atom_detected and source_type not in {"rss", "api"}:
        return False, "rejected by prefilter: no useful text or feed structure"
    if not pressure_terms and source_family not in {"state_official", "federal_official", "disaster_emergency", "local_news", "public_radio", "nonprofit_news", "food_bank_provider"}:
        return False, "rejected by prefilter: weak source structure"
    return True, ""


def _discovery_quality_score(
    *,
    discovery_score: int,
    source_type: str,
    useful_text_available: bool,
    pressure_terms: list[str],
    negative_terms: list[str],
    source_family: str,
    priority_bonus: int = 0,
) -> tuple[int, dict[str, int]]:
    purpose_score = 20 if source_family in {"local_news", "public_radio", "nonprofit_news", "food_bank_provider", "state_official", "federal_official", "disaster_emergency"} else 5
    text_quality_score = 25 if useful_text_available else 5
    pressure_topic_score = min(25, len(pressure_terms) * 5)
    noise_score = max(0, 30 - len(negative_terms) * 6)
    priority_adjustment = priority_bonus if priority_bonus else -45
    source_quality_score = max(0, min(100, int(round((discovery_score * 0.4) + purpose_score + text_quality_score + pressure_topic_score + noise_score + priority_adjustment))))
    return source_quality_score, {
        "purpose_score": purpose_score,
        "text_quality_score": text_quality_score,
        "pressure_topic_score": pressure_topic_score,
        "noise_score": noise_score,
        "priority_bonus": priority_bonus,
    }


def _normalize_candidate_status(value: Any, default: str = "candidate") -> str:
    status = str(value or default or "candidate").strip().lower()
    return status if status in {"candidate", "tested_good", "tested_weak", "tested_failed", "enabled", "rejected", "quarantined", "archived", "promoted"} else default


def _source_quality_tier(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    if score >= 15:
        return "low"
    return "quarantine"


def _candidate_fields_from_discovery(
    *,
    discovered_url: str,
    source_name: str,
    publisher: str,
    source_family: str,
    source_type: str,
    state: str,
    location_name: str,
    location_scope: str,
    reason: str,
    pressure_terms: list[str],
    notes: str,
    source_purpose: str,
    current_or_evergreen: str,
    promotable: bool,
    non_promotable_reason: str,
    source_quality_score: int,
    source_quality_tier: str,
    auto_discovered: bool,
    first_discovered_at: str,
    last_discovered_at: str,
    discovery_count: int,
    last_recommendation: str,
    last_recommendation_reason: str,
    source_seed_url: str = "",
    discovery_seed_url: str = "",
    discovered_from: str = "",
    retrieved_at: str = "",
    published_at: str = "",
    page_metadata_date: str = "",
    evidence_text: str = "",
    evidence_text_basis: str = "",
    source_role: str = "",
    donation_wrapper: bool = False,
    public_eligible: bool = True,
) -> dict[str, Any]:
    return {
        "source_id": _candidate_id(discovered_url, publisher, source_family),
        "source_name": source_name or publisher or discovered_url,
        "publisher": publisher or source_name,
        "candidate_url": discovered_url,
        "source_seed_url": source_seed_url or discovery_seed_url or "",
        "discovery_seed_url": discovery_seed_url or source_seed_url or "",
        "discovered_from": discovered_from or "",
        "source_family": source_family or "local_news",
        "source_type": source_type,
        "state": state or "US",
        "location_name": location_name or ("United States" if (state or "US").upper() in {"", "US"} else state),
        "location_scope": location_scope or ("national" if (state or "US").upper() in {"", "US"} else "state_local"),
        "candidate_reason": reason,
        "expected_text_basis": "rss_summary" if source_type == "rss" else "page_text",
        "extraction_quality_guess": "high" if source_type == "rss" else "medium",
        "pressure_topics_expected": pressure_terms,
        "status": "candidate",
        "notes": notes,
        "source_purpose": source_purpose,
        "current_or_evergreen": current_or_evergreen,
        "promotable": promotable,
        "non_promotable_reason": non_promotable_reason,
        "source_quality_score": source_quality_score,
        "source_quality_tier": source_quality_tier,
        "auto_discovered": auto_discovered,
        "first_discovered_at": first_discovered_at,
        "last_discovered_at": last_discovered_at,
        "discovery_count": discovery_count,
        "last_recommendation": last_recommendation,
        "last_recommendation_reason": last_recommendation_reason,
        "source_origin": "google_news_discovery",
        "registry_status": "non_registry_discovered_source",
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "page_metadata_date": page_metadata_date,
        "evidence_text": evidence_text,
        "evidence_text_basis": evidence_text_basis,
        "source_role": source_role or ("discovery_lead" if donation_wrapper else "discovery_candidate"),
        "donation_wrapper": bool(donation_wrapper),
        "public_eligible": bool(public_eligible),
        "test_count": 0,
        "enable_count": 0,
        "reject_count": 0,
        "keep_candidate_count": 0,
    }


def _merge_candidate(existing: dict[str, Any], discovered: dict[str, Any], discovery_meta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    preserve_status = _nonempty(existing.get("status"))
    if preserve_status in {"enabled", "rejected", "promoted"}:
        merged["status"] = preserve_status
    for key in (
        "source_name",
        "publisher",
        "candidate_url",
        "source_seed_url",
        "discovery_seed_url",
        "discovered_from",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "notes",
    ):
        if not _nonempty(merged.get(key)) and _nonempty(discovered.get(key)):
            merged[key] = discovered[key]
    if not _nonempty(merged.get("status")):
        merged["status"] = discovered.get("status") or "candidate"
    merged["pressure_topics_expected"] = discovered.get("pressure_topics_expected") or merged.get("pressure_topics_expected") or []
    for key in (
        "discovery_method",
        "discovery_query",
        "discovered_at",
        "discovery_score",
        "url_status",
        "rss_or_atom_detected",
        "useful_text_available",
        "likely_noise_level",
        "preliminary_pressure_terms_found",
        "preliminary_negative_terms_found",
        "source_purpose",
        "current_or_evergreen",
        "promotable",
        "non_promotable_reason",
        "source_quality_score",
        "source_quality_tier",
        "auto_discovered",
        "first_discovered_at",
        "last_discovered_at",
        "last_recommendation",
        "last_recommendation_reason",
        "retrieved_at",
        "published_at",
        "page_metadata_date",
        "evidence_text",
        "evidence_text_basis",
    ):
        if key in discovery_meta:
            merged[key] = discovery_meta[key]
    for key in ("discovery_count", "test_count", "enable_count", "reject_count", "keep_candidate_count"):
        if key in discovery_meta:
            merged[key] = discovery_meta[key]
    return merged


def _discover_candidates_from_seed(
    seed: dict[str, Any],
    queries: list[dict[str, Any]],
    *,
    fetcher: Any,
    max_results_per_query: int,
    blocklist: dict[str, list[str]],
    priority: dict[str, list[str]],
) -> list[dict[str, Any]]:
    seed_url = _nonempty(seed.get("candidate_url"))
    if not seed_url:
        return []
    payload, fetch_error = _fetch_url(fetcher, seed_url)
    discovered: list[dict[str, Any]] = []
    if fetch_error:
        purpose_info = classify_food_line_source_purpose(seed)
        discovered.append(
            {
                "source_id": seed.get("source_id") or _candidate_id(seed_url, seed.get("publisher") or "", seed.get("source_family") or ""),
                "source_name": seed.get("source_name") or seed.get("publisher") or seed_url,
                "publisher": seed.get("publisher") or "",
                "candidate_url": seed_url,
                "source_family": seed.get("source_family") or "local_news",
                "source_type": seed.get("source_type") or "page",
                "state": seed.get("state") or "US",
                "location_name": seed.get("location_name") or "United States",
                "location_scope": seed.get("location_scope") or "national",
                "candidate_reason": f"Discovery fetch failed: {fetch_error}",
                "expected_text_basis": "manual",
                "extraction_quality_guess": "unknown",
                "pressure_topics_expected": [],
                "status": seed.get("status") or "candidate",
                "notes": seed.get("notes") or "",
                "discovery_method": "seed_fetch",
                "discovery_query": "",
                "discovered_at": "",
                "discovery_score": 0,
                "url_status": "error",
                "rss_or_atom_detected": False,
                "useful_text_available": False,
                "likely_noise_level": 100,
                "preliminary_pressure_terms_found": [],
                "preliminary_negative_terms_found": [],
                "source_purpose": purpose_info["source_purpose"],
                "current_or_evergreen": purpose_info["current_or_evergreen"],
                "promotable": purpose_info["promotable"] == "true",
                "non_promotable_reason": purpose_info["non_promotable_reason"],
                "source_quality_score": 0,
                "source_quality_tier": "quarantine",
                "auto_discovered": True,
                "first_discovered_at": _utc_now(),
                "last_discovered_at": _utc_now(),
                "discovery_count": 1,
                "last_recommendation": "rejected_discovery",
                "last_recommendation_reason": fetch_error,
                "inserted_after_prefilter": False,
                "rejected_by_prefilter": True,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": purpose_info["source_purpose"] in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": False,
                "purpose_score": 0,
                "text_quality_score": 0,
                "pressure_topic_score": 0,
                "noise_score": 100,
                "quality_score_components": {"purpose_score": 0, "text_quality_score": 0, "pressure_topic_score": 0, "noise_score": 100},
                "action": "rejected_discovery",
                "reason": fetch_error,
                "raw_diagnostics": {},
            }
        )
        return discovered

    payload_text = payload.decode("utf-8", errors="replace")
    rss_items: list[dict[str, str]] = []
    rss_or_atom_detected = bool(re.search(r"<(?:rss|feed)\b", payload_text, re.IGNORECASE))
    if rss_or_atom_detected:
        try:
            rss_items = _parse_rss_items(payload)
        except Exception:  # noqa: BLE001
            rss_items = []
    page_evidence = _extract_page_evidence(payload)
    discovered_links = _parse_html_links(payload, seed_url)
    query_terms = []
    for query in queries:
        query_terms.extend(_template_terms(query["query"]))
    text_blob = " ".join(
        part for part in (
            page_evidence.get("title") or "",
            page_evidence.get("summary_or_snippet") or "",
            page_evidence.get("evidence_text") or "",
            payload_text[:4000],
        )
        if part
    )
    pressure_terms = _find_terms(text_blob, PRESSURE_TERMS + query_terms)
    negative_terms = _find_terms(text_blob, NEGATIVE_TERMS)
    useful_text_available = bool(_normalize_source_text(text_blob))
    discovered_links = sorted(
        discovered_links,
        key=lambda link: (
            _rank_discovered_link(link, pressure_terms=pressure_terms, query_terms=query_terms, seed_url=seed_url),
            len(str(link.get("url") or "")),
        ),
        reverse=True,
    )
    discovery_score, likely_noise_level = _score_discovery(
        useful_text_available=useful_text_available,
        rss_or_atom_detected=rss_or_atom_detected,
        pressure_terms=pressure_terms,
        negative_terms=negative_terms,
        source_type=_nonempty(seed.get("source_type") or "page"),
        fetched=True,
    )
    discovered_at = _utc_now()
    review_rows: list[dict[str, Any]] = []
    seed_purpose = classify_food_line_source_purpose(seed)
    seed_blocked, seed_block_reason = _blocked_by_discovery_rules(seed, blocklist)

    def add_discovery(
        *,
        discovered_url: str,
        source_name: str,
        source_type: str,
        reason: str,
        query_string: str,
        query_template: str,
        discovery_method: str,
        extra_terms: list[str] | None = None,
        source_purpose: str,
        current_or_evergreen: str,
        promotable: bool,
        non_promotable_reason: str,
    ) -> None:
        nonlocal discovery_score, likely_noise_level
        terms = list(pressure_terms)
        if extra_terms:
            for term in extra_terms:
                if term not in terms:
                    terms.append(term)
        candidate_profile = _inspect_candidate_page(fetcher, discovered_url, seed_url=seed_url)
        discovered_title = candidate_profile.get("page_title") or source_name
        discovered_summary = candidate_profile.get("page_summary_or_snippet") or ""
        discovered_evidence = candidate_profile.get("page_evidence_text") or ""
        discovered_evidence_basis = candidate_profile.get("page_evidence_text_basis") or ("page_text_excerpt" if source_type != "rss" else "rss_item_text")
        blocked, block_reason = _blocked_by_discovery_rules(
            {
                "candidate_url": discovered_url,
                "source_name": discovered_title,
                "candidate_reason": reason,
                "notes": _nonempty(seed.get("notes") or ""),
                "source_purpose": source_purpose,
            },
            blocklist,
        )
        blocked = blocked or seed_blocked
        block_reason = block_reason or seed_block_reason
        priority_bonus = _priority_bonus(
            {
                "candidate_url": discovered_url,
                "source_family": _nonempty(seed.get("source_family") or "local_news"),
                "state": _nonempty(seed.get("state") or "US"),
            },
            priority,
        )
        quality_score, score_components = _discovery_quality_score(
            discovery_score=discovery_score,
            source_type=source_type,
            useful_text_available=useful_text_available,
            pressure_terms=terms,
            negative_terms=negative_terms,
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            priority_bonus=priority_bonus,
        )
        prefilter_allowed, prefilter_reason = _prefilter_discovery_candidate(
            source_purpose=source_purpose,
            source_type=source_type,
            useful_text_available=useful_text_available,
            rss_or_atom_detected=rss_or_atom_detected,
            pressure_terms=terms,
            negative_terms=negative_terms,
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            blocked=blocked,
        )
        high_value_family = _nonempty(seed.get("source_family") or "").strip().lower() in {
            "local_news",
            "public_radio",
            "nonprofit_news",
            "food_bank_provider",
            "state_official",
            "federal_official",
            "disaster_emergency",
            "school_meals_child_nutrition",
            "senior_meals",
        }
        inserted_after_prefilter = bool(prefilter_allowed and (quality_score >= 35 or high_value_family))
        rejected_by_prefilter = not prefilter_allowed
        rejected_by_noise = bool(not inserted_after_prefilter and quality_score < 30 and likely_noise_level >= 70)
        action = "inserted_candidate" if inserted_after_prefilter else "rejected_discovery"
        reason_text = reason if inserted_after_prefilter else (prefilter_reason or block_reason or "insufficient discovery quality")
        candidate = _candidate_fields_from_discovery(
            discovered_url=discovered_url,
            source_name=discovered_title,
            publisher=_nonempty(seed.get("publisher")),
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            source_type=source_type,
            state=_nonempty(seed.get("state") or "US").upper(),
            location_name=_nonempty(seed.get("location_name") or "United States"),
            location_scope=_nonempty(seed.get("location_scope") or "national"),
            reason=reason,
            pressure_terms=terms[:6],
            notes=f"Discovered from {seed_url}",
            source_purpose=source_purpose,
            current_or_evergreen=current_or_evergreen,
            promotable=promotable,
            non_promotable_reason=non_promotable_reason,
            source_quality_score=quality_score,
            source_quality_tier=("high" if quality_score >= 75 else "medium" if quality_score >= 45 else "low" if quality_score >= 15 else "quarantine"),
            auto_discovered=True,
            first_discovered_at=discovered_at,
            last_discovered_at=discovered_at,
            discovery_count=1,
            last_recommendation="candidate",
            last_recommendation_reason=reason_text,
            source_seed_url=seed_url,
            discovery_seed_url=seed_url,
            discovered_from=discovery_method,
            retrieved_at=candidate_profile.get("retrieved_at") or discovered_at,
            published_at=candidate_profile.get("published_at") or "",
            page_metadata_date=candidate_profile.get("page_metadata_date") or "",
            evidence_text=discovered_evidence or discovered_summary,
            evidence_text_basis=discovered_evidence_basis,
        )
        candidate.update(
            {
                "discovery_method": discovery_method,
                "discovery_query": query_string,
                "query_template": query_template,
                "discovered_at": discovered_at,
                "discovery_score": discovery_score,
                "url_status": "ok",
                "rss_or_atom_detected": rss_or_atom_detected,
                "useful_text_available": useful_text_available,
                "likely_noise_level": likely_noise_level,
                "preliminary_pressure_terms_found": terms,
                "preliminary_negative_terms_found": negative_terms,
                "source_purpose": source_purpose,
                "current_or_evergreen": current_or_evergreen,
                "promotable": promotable,
                "non_promotable_reason": non_promotable_reason,
                "source_quality_score": quality_score,
                "source_quality_tier": "high" if quality_score >= 75 else "medium" if quality_score >= 45 else "low" if quality_score >= 15 else "quarantine",
                "auto_discovered": True,
                "first_discovered_at": discovered_at,
                "last_discovered_at": discovered_at,
                "discovery_count": 1,
                "last_recommendation": action,
                "last_recommendation_reason": reason_text,
                "inserted_after_prefilter": inserted_after_prefilter,
                "rejected_by_prefilter": rejected_by_prefilter,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": rejected_by_noise,
                "purpose_score": score_components["purpose_score"],
                "text_quality_score": score_components["text_quality_score"],
                "pressure_topic_score": score_components["pressure_topic_score"],
                "noise_score": score_components["noise_score"],
                "priority_bonus": score_components.get("priority_bonus", 0),
                "action": action,
                "reason": reason_text,
                "quality_score_components": score_components,
                "source_seed_url": seed_url,
                "discovery_seed_url": seed_url,
                "discovered_from": discovery_method,
                "retrieved_at": candidate_profile.get("retrieved_at") or discovered_at,
                "published_at": candidate_profile.get("published_at") or "",
                "page_metadata_date": candidate_profile.get("page_metadata_date") or "",
                "evidence_text": discovered_evidence or discovered_summary,
                "evidence_text_basis": discovered_evidence_basis,
            }
        )
        review_rows.append(candidate)

    if rss_items:
        for item in rss_items[:max_results_per_query]:
            item_title = _nonempty(item.get("title")) or page_evidence.get("title") or _nonempty(seed.get("source_name"))
            item_url = _nonempty(item.get("url")) or seed_url
            item_text = " ".join(part for part in (item_title, _nonempty(item.get("summary_or_snippet")), _nonempty(item.get("evidence_text"))) if part)
            item_terms = _find_terms(item_text, PRESSURE_TERMS + query_terms)
            item_negative = _find_terms(item_text, NEGATIVE_TERMS)
            if not item_terms and not item_negative and not useful_text_available:
                continue
            query_string = next((q.get("query", "") for q in queries if q.get("state") == _nonempty(seed.get("state") or "US").upper()), "")
            add_discovery(
                discovered_url=item_url if item_url.startswith(("http://", "https://")) else seed_url,
                source_name=item_title,
                source_type="page" if item_url != seed_url else "rss",
                reason=f"Discovered from feed item on {seed.get('source_name') or seed_url}",
                query_string=query_string,
                query_template=next(
                    (
                        q.get("query_template", q.get("template", query_string))
                        for q in queries
                        if q.get("query") == query_string
                    ),
                    query_string,
                ),
                discovery_method="rss_item_link",
                extra_terms=item_terms,
                source_purpose=seed_purpose["source_purpose"],
                current_or_evergreen=seed_purpose["current_or_evergreen"],
                promotable=seed_purpose["promotable"] == "true",
                non_promotable_reason=seed_purpose["non_promotable_reason"],
            )
    if discovered_links:
        for link in discovered_links[:max_results_per_query]:
            link_url = _normalize_url(link["url"])
            if not link_url:
                continue
            title = page_evidence.get("title") or _nonempty(seed.get("source_name") or seed_url)
            query_string = next((q.get("query", "") for q in queries if q.get("state") == _nonempty(seed.get("state") or "US").upper()), "")
            add_discovery(
                discovered_url=link_url,
                source_name=title,
                source_type="rss" if link.get("kind") == "rss_or_atom" else "page",
                reason=f"Discovered from {link.get('kind') or 'page link'} on {seed.get('source_name') or seed_url}",
                query_string=query_string,
                query_template=next((q["query_template"] for q in queries if q["query"] == query_string), query_string),
                discovery_method=link.get("kind") or "page_link",
                source_purpose=seed_purpose["source_purpose"],
                current_or_evergreen=seed_purpose["current_or_evergreen"],
                promotable=seed_purpose["promotable"] == "true",
                non_promotable_reason=seed_purpose["non_promotable_reason"],
            )
    if not discovered_links and useful_text_available and (pressure_terms or not negative_terms):
        query_string = next((q.get("query", "") for q in queries if q.get("state") == _nonempty(seed.get("state") or "US").upper()), "")
        add_discovery(
            discovered_url=seed_url,
            source_name=page_evidence.get("title") or _nonempty(seed.get("source_name") or seed_url),
            source_type="page" if seed.get("source_type") != "rss" else "rss",
            reason=f"Seed page text supports manual review from {seed.get('source_name') or seed_url}",
            query_string=query_string,
            query_template=next((q["query_template"] for q in queries if q["query"] == query_string), query_string),
            discovery_method="seed_page",
            source_purpose=seed_purpose["source_purpose"],
            current_or_evergreen=seed_purpose["current_or_evergreen"],
            promotable=seed_purpose["promotable"] == "true",
            non_promotable_reason=seed_purpose["non_promotable_reason"],
        )
    if not review_rows:
        review_rows.append(
            {
                "source_id": seed.get("source_id") or _candidate_id(seed_url, seed.get("publisher") or "", seed.get("source_family") or ""),
                "source_name": seed.get("source_name") or seed_url,
                "publisher": seed.get("publisher") or "",
                "candidate_url": seed_url,
                "source_family": seed.get("source_family") or "local_news",
                "source_type": seed.get("source_type") or "page",
                "discovery_method": "seed_page",
                "discovery_query": "",
                "query_template": "",
                "discovery_score": discovery_score,
                "url_status": "ok",
                "rss_or_atom_detected": rss_or_atom_detected,
                "useful_text_available": useful_text_available,
                "likely_noise_level": likely_noise_level,
                "preliminary_pressure_terms_found": pressure_terms,
                "preliminary_negative_terms_found": negative_terms,
                "source_purpose": seed_purpose["source_purpose"],
                "current_or_evergreen": seed_purpose["current_or_evergreen"],
                "promotable": seed_purpose["promotable"] == "true",
                "non_promotable_reason": seed_purpose["non_promotable_reason"],
                "inserted_after_prefilter": False,
                "rejected_by_prefilter": False,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": seed_purpose["source_purpose"] in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": False,
                "source_quality_score": discovery_score,
                "source_quality_tier": _source_quality_tier(discovery_score),
                "purpose_score": 0,
                "text_quality_score": 0,
                "pressure_topic_score": 0,
                "noise_score": likely_noise_level,
                "action": "rejected_discovery" if not pressure_terms and negative_terms else "skipped_duplicate",
                "reason": "insufficient discovery evidence" if not pressure_terms and negative_terms else "duplicate or already represented",
            }
        )
    return review_rows


def discover_food_line_sources(
    root: Path,
    date: str,
    *,
    states: list[str] | None = None,
    max_results_per_query: int = 10,
    max_candidates_total: int = 250,
    max_insertions: int = 100,
    families: list[str] | None = None,
    exclude_families: list[str] | None = None,
    min_source_quality_score: float = 0.0,
    skip_known_bad: bool = True,
    skip_quarantined: bool = True,
    skip_archived: bool = True,
    write_candidates: bool = False,
    dry_run: bool = False,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    states = [state.strip().upper() for state in (states or STATES) if state.strip()]
    family_filter = {family.strip().lower() for family in (families or []) if family.strip()}
    excluded_families = {family.strip().lower() for family in (exclude_families or []) if family.strip()}
    blocklist = _load_discovery_blocklist(root)
    priority = _load_discovery_priority(root)
    query_rows = _load_discovery_query_rows(root)
    date_bounded_queries = _date_bounded_queries(date)
    expanded_queries = _expand_queries(query_rows, states)
    query_rows_for_metrics = list(query_rows) + list(date_bounded_queries)
    query_by_template: dict[str, dict[str, Any]] = {}
    for row in query_rows_for_metrics:
        template_key = str(row.get("template") or "").strip()
        query_template_key = str(row.get("query_template") or template_key).strip()
        if template_key and template_key not in query_by_template:
            query_by_template[template_key] = row
        if query_template_key and query_template_key not in query_by_template:
            query_by_template[query_template_key] = row
    query_results: dict[str, Counter[str]] = {str(row["query_template"]): Counter() for row in query_rows_for_metrics if str(row.get("query_template") or "").strip()}
    queries = []
    for query in expanded_queries:
        template = query["template"]
        row = query_by_template.get(template, {})
        rolling_score = float(row.get("rolling_query_quality_score") or 0)
        if int(row.get("runs") or 0) >= 3 and rolling_score < 20:
            continue
        if family_filter and str(query.get("source_family") or "").lower() not in family_filter:
            continue
        if excluded_families and str(query.get("source_family") or "").lower() in excluded_families:
            continue
        queries.append(query)
    queries.extend(date_bounded_queries)
    fetch = resolve_food_line_fetcher(fetcher)
    discovery_review_path = root / "output" / "review" / "food-line" / date / "source_discovery_review.csv"
    discovery_audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "source_discovery_audit.json"
    skipped_known_bad_count = 0
    skipped_quarantined_count = 0
    skipped_archived_count = 0
    seed_rows = _discovery_seed_rows(root)
    seed_rows = sorted(
        seed_rows,
        key=lambda row: (
            _priority_bonus(
                {
                    "candidate_url": _nonempty(row.get("candidate_url") or row.get("url")),
                    "source_family": _nonempty(row.get("source_family")),
                    "state": _nonempty(row.get("state") or "US"),
                },
                priority,
            ),
            1 if _nonempty(row.get("source_family")) in {"public_radio", "nonprofit_news"} else 0,
            1 if _nonempty(row.get("source_id")) in {"nepm-regional-news", "maine-monitor-post-sitemap"} else 0,
        ),
        reverse=True,
    )
    review_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    seen_run_urls: set[str] = set()
    discovered_candidate_rows: list[dict[str, Any]] = []
    discovered_at = _utc_now()
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    rejected_count = 0
    discovered_candidate_count = 0

    def _seed_allowed(seed: dict[str, Any]) -> tuple[bool, str]:
        nonlocal skipped_known_bad_count, skipped_quarantined_count, skipped_archived_count
        source_family = str(seed.get("source_family") or "").strip().lower()
        status = str(seed.get("status") or "candidate").strip().lower()
        if family_filter and source_family not in family_filter:
            return False, "family filtered"
        if excluded_families and source_family in excluded_families:
            return False, "family excluded"
        if skip_quarantined and status == "quarantined":
            skipped_quarantined_count += 1
            return False, "quarantined candidate skipped"
        if skip_archived and status == "archived":
            skipped_archived_count += 1
            return False, "archived candidate skipped"
        if skip_known_bad and status in {"rejected", "tested_failed"}:
            skipped_known_bad_count += 1
            return False, f"known bad candidate skipped: {status}"
        blocked, reason = _blocked_by_discovery_rules(seed, blocklist)
        if blocked:
            return False, reason
        return True, ""
    eligible_seed_rows: list[dict[str, Any]] = []
    for seed in seed_rows:
        allowed, reason = _seed_allowed(seed)
        if not allowed:
            seed_purpose = classify_food_line_source_purpose(seed)
            review_rows.append(
                {
                    "source_id": _nonempty(seed.get("source_id")),
                    "source_name": _nonempty(seed.get("source_name") or seed.get("name") or seed.get("title")),
                    "publisher": _nonempty(seed.get("publisher")),
                    "candidate_url": _nonempty(seed.get("url") or seed.get("candidate_url")),
                    "state": _nonempty(seed.get("state") or "US").upper(),
                    "source_family": _nonempty(seed.get("source_family")),
                    "source_type": _nonempty(seed.get("source_type") or "page"),
                    "source_purpose": seed_purpose["source_purpose"],
                    "current_or_evergreen": seed_purpose["current_or_evergreen"],
                    "promotable": seed_purpose["promotable"] == "true",
                    "non_promotable_reason": seed_purpose["non_promotable_reason"],
                    "source_quality_score": 0,
                    "source_quality_tier": "low",
                    "action": "rejected_discovery",
                    "reason": reason,
                    "rejected_by_prefilter": "true",
                }
            )
            audit_rows.append(
                {
                    "source_id": _nonempty(seed.get("source_id")),
                    "source_name": _nonempty(seed.get("source_name") or seed.get("name") or seed.get("title")),
                    "candidate_url": _nonempty(seed.get("url") or seed.get("candidate_url")),
                    "action": "rejected_discovery",
                    "reason": reason,
                    "source_purpose": seed_purpose["source_purpose"],
                }
            )
            rejected_count += 1
            continue
        eligible_seed_rows.append(seed)
    seed_rows = eligible_seed_rows
    seed_rows.sort(key=lambda seed: (
        -_priority_bonus(seed, priority),
        str(seed.get("source_family") or ""),
        str(seed.get("source_name") or ""),
    ))
    candidate_registry_path = root / "data" / "dispatches" / "food-line" / "candidate_source_registry.json"
    candidate_registry = _read_json_list(candidate_registry_path)
    candidate_by_source_id = {str(row.get("source_id") or "").strip(): row for row in candidate_registry if _nonempty(row.get("source_id"))}
    candidate_by_url = {_normalize_url(_nonempty(row.get("candidate_url"))): row for row in candidate_registry if _normalize_url(_nonempty(row.get("candidate_url")))}
    candidate_by_pub_url = {
        (str(row.get("publisher") or "").strip().lower(), _normalize_url(_nonempty(row.get("candidate_url")))): row
        for row in candidate_registry
        if _normalize_url(_nonempty(row.get("candidate_url")))
    }
    existing_discovery_urls = set(candidate_by_url.keys())

    def _source_quality_ratio(value: Any) -> float:
        try:
            score = float(value or 0)
        except Exception:  # noqa: BLE001
            score = 0.0
        return score if score <= 1 else score / 100.0

    for seed in seed_rows:
        if discovered_candidate_count >= max_candidates_total or inserted_count >= max_insertions:
            break
        discovery_rows = _discover_candidates_from_seed(
            seed,
            queries,
            fetcher=fetch,
            max_results_per_query=max_results_per_query,
            blocklist=blocklist,
            priority=priority,
        )
        for row in discovery_rows:
            source_id = _nonempty(row.get("source_id"))
            candidate_url = _normalize_url(_nonempty(row.get("candidate_url")))
            publisher = _nonempty(row.get("publisher"))
            pub_url_key = (publisher.lower(), candidate_url)
            query_template = _nonempty(row.get("query_template") or row.get("discovery_query"))
            if not candidate_url or not candidate_url.startswith(("http://", "https://")):
                row["action"] = "rejected_discovery"
                row["reason"] = "candidate_url must use http or https"
                rejected_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                    query_results[query_template]["rejects"] += 1
                continue
            if _source_quality_ratio(row.get("source_quality_score")) < min_source_quality_score:
                row["action"] = "rejected_discovery"
                row["reason"] = "below minimum source quality score"
                rejected_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                    query_results[query_template]["rejects"] += 1
                continue
            if candidate_url in seen_run_urls or source_id in {r["source_id"] for r in review_rows if _nonempty(r.get("source_id"))}:
                row["action"] = "skipped_duplicate"
                row["reason"] = "duplicate discovered source"
                skipped_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                continue
            seen_run_urls.add(candidate_url)
            existing = candidate_by_url.get(candidate_url) or candidate_by_source_id.get(source_id) or candidate_by_pub_url.get(pub_url_key)
            if existing:
                preserved_status = _nonempty(existing.get("status"))
                if preserved_status in {"enabled", "promoted"}:
                    row["action"] = "skipped_existing_enabled"
                    row["reason"] = "enabled candidate preserved"
                    skipped_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    if query_template in query_results:
                        query_results[query_template]["runs"] += 1
                        query_results[query_template]["candidates_found"] += 1
                        query_results[query_template]["rejects"] += 1
                    continue
                if preserved_status in {"rejected", "quarantined", "archived"} and _normalize_url(_nonempty(existing.get("candidate_url") or existing.get("url"))) == candidate_url:
                    row["action"] = "skipped_duplicate"
                    row["reason"] = f"already {preserved_status}"
                    skipped_count += 1
                    if preserved_status == "rejected" and int(existing.get("reject_count") or 0) > 2:
                        existing["status"] = "quarantined"
                        existing["source_quality_tier"] = "quarantine"
                        existing["last_recommendation"] = "quarantined"
                        existing["last_recommendation_reason"] = "rejected more than 2 times"
                    review_rows.append(row)
                    audit_rows.append(row)
                    if query_template in query_results:
                        query_results[query_template]["runs"] += 1
                        query_results[query_template]["candidates_found"] += 1
                        query_results[query_template]["rejects"] += 1
                    continue
                merged = _merge_candidate(existing, row, row)
                merged["candidate_url"] = candidate_url
                merged["source_id"] = _nonempty(existing.get("source_id") or source_id or row["source_id"])
                merged["status"] = _normalize_candidate_status(existing.get("status") or row.get("status") or "candidate")
                merged["auto_discovered"] = bool(existing.get("auto_discovered", True) or row.get("auto_discovered", True))
                merged["discovery_count"] = int(existing.get("discovery_count") or 0) + 1
                merged["last_discovered_at"] = discovered_at
                merged["first_discovered_at"] = _nonempty(existing.get("first_discovered_at")) or discovered_at
                merged["last_recommendation"] = row.get("action") or "updated_candidate"
                merged["last_recommendation_reason"] = row.get("reason") or "existing candidate updated with discovery metadata"
                merged["source_quality_score"] = max(int(existing.get("source_quality_score") or 0), int(row.get("source_quality_score") or 0))
                merged["source_quality_tier"] = row.get("source_quality_tier") or existing.get("source_quality_tier") or "low"
                merged["source_purpose"] = row.get("source_purpose") or existing.get("source_purpose") or "unknown"
                merged["current_or_evergreen"] = row.get("current_or_evergreen") or existing.get("current_or_evergreen") or "unknown"
                merged["promotable"] = row.get("promotable", existing.get("promotable", False))
                merged["non_promotable_reason"] = row.get("non_promotable_reason") or existing.get("non_promotable_reason") or ""
                candidate_by_source_id[merged["source_id"]] = merged
                candidate_by_url[candidate_url] = merged
                candidate_by_pub_url[pub_url_key] = merged
                row["action"] = "updated_candidate"
                row["reason"] = "existing candidate updated with discovery metadata"
                updated_count += 1
                if int(row.get("source_quality_score") or 0) < 30 and not row.get("inserted_after_prefilter"):
                    row["action"] = "rejected_discovery"
                    row["reason"] = row.get("reason") or "insufficient discovery quality"
                    rejected_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    continue
                discovered_candidate_rows.append(merged)
            else:
                if not row.get("inserted_after_prefilter") or int(row.get("source_quality_score") or 0) < 35:
                    row["action"] = "rejected_discovery"
                    row["reason"] = row.get("reason") or "insufficient discovery quality"
                    rejected_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    continue
                row["action"] = "inserted_candidate"
                row["reason"] = "discovered candidate added to registry"
                inserted_count += 1
                row["discovery_count"] = int(row.get("discovery_count") or 1)
                discovered_candidate_rows.append(row)
                candidate_by_source_id[source_id] = row
                candidate_by_url[candidate_url] = row
                candidate_by_pub_url[pub_url_key] = row
            discovered_candidate_count += 1
            review_rows.append(row)
            audit_rows.append({**row, "discovery_queries": queries})
            if query_template in query_results:
                query_results[query_template]["runs"] += 1
                query_results[query_template]["candidates_found"] += 1
                if row.get("action") == "inserted_candidate":
                    query_results[query_template]["candidates_inserted"] += 1
                if row.get("action") == "inserted_candidate" and _source_quality_ratio(row.get("source_quality_score")) >= 0.7:
                    query_results[query_template]["candidates_verified_pressure"] += 0
            if discovered_candidate_count >= max_candidates_total or inserted_count >= max_insertions:
                break

    if write_candidates and not dry_run and discovered_candidate_rows:
        merged_rows = list(candidate_by_source_id.values())
        merged_rows.sort(key=lambda row: str(row.get("source_id") or ""))
        for row in merged_rows:
            row["source_quality_tier"] = str(row.get("source_quality_tier") or _source_quality_tier(int(row.get("source_quality_score") or 0))).lower()
            row["status"] = _normalize_candidate_status(row.get("status"))
            row["auto_discovered"] = bool(row.get("auto_discovered", False))
            row["discovery_count"] = int(row.get("discovery_count") or 0)
        _write_json(candidate_registry_path, merged_rows)

    review_rows.sort(key=lambda row: (str(row.get("action") or ""), str(row.get("source_id") or ""), str(row.get("candidate_url") or "")))
    audit_rows.sort(key=lambda row: (str(row.get("action") or ""), str(row.get("source_id") or ""), str(row.get("candidate_url") or "")))
    review_rows_for_csv = []
    for row in review_rows:
        csv_row = dict(row)
        if "rejected_by_prefilter" in csv_row:
            csv_row["rejected_by_prefilter"] = str(csv_row["rejected_by_prefilter"]).lower()
        review_rows_for_csv.append(csv_row)
    _write_csv(
        discovery_review_path,
        [
            "source_id",
            "source_name",
            "publisher",
            "candidate_url",
            "state",
            "source_family",
            "source_type",
            "source_purpose",
            "current_or_evergreen",
            "promotable",
            "non_promotable_reason",
            "inserted_after_prefilter",
            "rejected_by_prefilter",
            "rejected_by_duplicate",
            "rejected_by_source_purpose",
            "rejected_by_noise",
            "source_quality_score",
            "source_quality_tier",
            "purpose_score",
            "text_quality_score",
            "pressure_topic_score",
            "noise_score",
            "priority_bonus",
            "discovery_method",
            "discovery_query",
            "discovery_score",
            "url_status",
            "rss_or_atom_detected",
            "useful_text_available",
            "likely_noise_level",
            "preliminary_pressure_terms_found",
            "preliminary_negative_terms_found",
            "action",
            "reason",
        ],
        review_rows_for_csv,
    )
    discovery_audit_path.parent.mkdir(parents=True, exist_ok=True)
    discovery_audit_path.write_text(json.dumps(audit_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    updated_query_rows: list[dict[str, Any]] = []
    for row in query_rows:
        template = row["query_template"]
        stats = query_results.get(template, Counter())
        merged = dict(row)
        merged["runs"] = int(merged.get("runs") or 0) + int(stats.get("runs") or 0)
        merged["candidates_found"] = int(merged.get("candidates_found") or 0) + int(stats.get("candidates_found") or 0)
        merged["candidates_inserted"] = int(merged.get("candidates_inserted") or 0) + int(stats.get("candidates_inserted") or 0)
        merged["candidates_promoted"] = int(merged.get("candidates_promoted") or 0) + int(stats.get("candidates_promoted") or 0)
        merged["candidates_verified_pressure"] = int(merged.get("candidates_verified_pressure") or 0) + int(stats.get("candidates_verified_pressure") or 0)
        merged["rejects"] = int(merged.get("rejects") or 0) + int(stats.get("rejects") or 0)
        merged["rolling_query_quality_score"] = _query_quality_score(merged)
        updated_query_rows.append(merged)
    query_performance_path = _save_discovery_query_rows(root, updated_query_rows)
    query_report_path = root / "output" / "review" / "food-line" / "discovery_query_performance_report.csv"
    _write_csv(
        query_report_path,
        [
            "query_template",
            "runs",
            "candidates_found",
            "candidates_inserted",
            "candidates_promoted",
            "candidates_verified_pressure",
            "rejects",
            "rolling_query_quality_score",
            "recommended_action",
        ],
        [
            {
                **row,
                "recommended_action": _query_recommendation(row),
            }
            for row in updated_query_rows
        ],
    )
    history = load_food_line_source_performance_history(root)
    health_rows = []
    latest_review_by_source_id = {str(row.get("source_id") or ""): row for row in review_rows if str(row.get("source_id") or "").strip()}
    for candidate in sorted(candidate_by_source_id.values(), key=lambda row: str(row.get("source_id") or "")):
        source_id = str(candidate.get("source_id") or "").strip()
        history_row = history.get(source_id, {})
        latest_review = latest_review_by_source_id.get(source_id, {})
        source_quality_score = int(candidate.get("source_quality_score") or history_row.get("rolling_quality_score") or 0)
        source_quality_tier = str(candidate.get("source_quality_tier") or "").strip().lower() or _source_quality_tier(source_quality_score)
        useful_text_available = str(latest_review.get("useful_text_available") or "").lower() == "true" or source_quality_score >= 20
        recommended_action = "preserve_enabled" if str(candidate.get("status") or "").lower() == "enabled" else (
            "archive" if int(history_row.get("fetch_failures") or 0) >= 3 and not useful_text_available else (
                "quarantine" if int(candidate.get("reject_count") or 0) >= 2 or int(history_row.get("fetch_failures") or 0) >= 2 else "keep_candidate"
            )
        )
        health_rows.append(
            {
                "source_id": source_id,
                "source_name": candidate.get("source_name") or "",
                "status": candidate.get("status") or "",
                "source_family": candidate.get("source_family") or "",
                "state": candidate.get("state") or "",
                "source_quality_score": source_quality_score,
                "source_quality_tier": source_quality_tier,
                "test_count": int(candidate.get("test_count") or 0),
                "reject_count": int(candidate.get("reject_count") or 0),
                "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
                "enable_count": int(candidate.get("enable_count") or 0),
                "fetch_failures": int(history_row.get("fetch_failures") or 0),
                "useful_text_available": str(useful_text_available).lower(),
                "last_recommendation": candidate.get("last_recommendation") or "",
                "recommended_action": recommended_action,
            }
        )
    health_report_path = root / "output" / "review" / "food-line" / "source_registry_health_report.csv"
    _write_csv(
        health_report_path,
        [
            "source_id",
            "source_name",
            "status",
            "source_family",
            "state",
            "source_quality_score",
            "source_quality_tier",
            "test_count",
            "reject_count",
            "keep_candidate_count",
            "enable_count",
            "fetch_failures",
            "useful_text_available",
            "last_recommendation",
            "recommended_action",
        ],
        health_rows,
    )
    summary = {
        "ok": True,
        "discovered_candidate_count": discovered_candidate_count,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "rejected_count": rejected_count,
        "discovered_count": discovered_candidate_count,
        "prefilter_rejected_count": sum(1 for row in review_rows if str(row.get("action") or "") == "rejected_discovery" and str(row.get("rejected_by_prefilter") or "").lower() == "true"),
        "duplicate_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate"),
        "quarantined_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate" and str(row.get("reason") or "").startswith("already quarantined")),
        "archived_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate" and str(row.get("reason") or "").startswith("already archived")),
        "skipped_known_bad_count": skipped_known_bad_count,
        "skipped_quarantined_count": skipped_quarantined_count,
        "skipped_archived_count": skipped_archived_count,
        "source_quality_tier_counts": dict(sorted(Counter(str(row.get("source_quality_tier") or "quarantine") for row in review_rows).items())),
        "query_performance_path": str(query_performance_path),
        "query_performance_report_path": str(query_report_path),
        "source_registry_health_report_path": str(health_report_path),
        "review_path": str(discovery_review_path),
        "audit_path": str(discovery_audit_path),
        "candidate_registry_path": str(candidate_registry_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover Food Line candidate sources")
    parser.add_argument("--date", required=True)
    parser.add_argument("--states", default=",".join(STATES))
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--resolver-timeout-seconds", type=int, default=15)
    parser.add_argument("--skip-sitemap-fallback", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--max-candidates-total", type=int, default=250)
    parser.add_argument("--max-insertions", type=int, default=100)
    parser.add_argument("--families", default="")
    parser.add_argument("--exclude-families", default="")
    parser.add_argument("--min-source-quality-score", type=float, default=0.0)
    parser.add_argument("--skip-known-bad", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-quarantined", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-archived", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-candidates", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gap-check", action="store_true", help="Run the Food Line discovery gap diagnostic only.")
    args = parser.parse_args(argv)
    if args.gap_check:
        result = run_food_line_discovery_gap_check(
            ROOT,
            args.date,
            max_results_per_query=args.max_results_per_query,
            max_queries=args.max_queries,
            max_candidates=args.max_candidates,
            resolver_timeout_seconds=args.resolver_timeout_seconds,
            skip_sitemap_fallback=args.skip_sitemap_fallback,
            fast=args.fast,
        )
        return 0 if result.get("ok") else 1
    states = [state.strip().upper() for state in args.states.split(",") if state.strip()]
    families = [family.strip() for family in args.families.split(",") if family.strip()]
    exclude_families = [family.strip() for family in args.exclude_families.split(",") if family.strip()]
    result = discover_food_line_sources(
        ROOT,
        args.date,
        states=states,
        max_results_per_query=args.max_results_per_query,
        max_candidates_total=args.max_candidates_total,
        max_insertions=args.max_insertions,
        families=families or None,
        exclude_families=exclude_families or None,
        min_source_quality_score=args.min_source_quality_score,
        skip_known_bad=args.skip_known_bad,
        skip_quarantined=args.skip_quarantined,
        skip_archived=args.skip_archived,
        write_candidates=args.write_candidates,
        dry_run=args.dry_run,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
