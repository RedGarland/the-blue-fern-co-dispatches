from __future__ import annotations

import email.utils
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from hashlib import sha256
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_fetch import fetch_backend, fetch_public_url
from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_normalize import canonicalize_url


REGISTRY_PATH = CASCADE_DATA_ROOT / "source_registry.yml"
REGISTRY_CACHE_ROOT = CASCADE_DATA_ROOT / "cache" / "registry"
FEED_SOURCE_TYPES = {"rss", "atom", "alert_feed"}
OFFICIAL_PAGE_SOURCE_TYPES = {"official_page", "press_release_page"}
FETCHABLE_SOURCE_TYPES = FEED_SOURCE_TYPES | OFFICIAL_PAGE_SOURCE_TYPES
DEFAULT_USER_AGENT = "BlueFernDispatches/0.1 registry-source-retrieval"
SYSTEM_TERMS = [
    "infrastructure",
    "public health",
    "hospital",
    "healthcare",
    "medicaid",
    "school",
    "housing",
    "homelessness",
    "wildfire",
    "drought",
    "flood",
    "power outage",
    "utility",
    "water",
    "transportation",
    "bridge",
    "road closure",
    "rail",
    "port",
    "public safety",
    "emergency",
    "emergency declaration",
    "economy",
    "labor",
    "layoffs",
    "staffing shortage",
    "staff shortages",
    "workforce shortage",
    "school closure",
    "school closures",
    "school district",
    "school budget cut",
    "school budget cuts",
    "clinic closure",
    "clinic reductions",
    "hospital access",
    "hospital strain",
    "transit service cut",
    "transit cuts",
    "ferry disruption",
    "ferry service",
    "burn ban",
    "wildfire smoke",
    "flood recovery",
    "drought response",
    "utility shutoff",
    "power shutoff",
    "outage recovery",
    "housing displacement",
    "food bank demand",
    "rural access",
    "road closure",
    "weather-related infrastructure",
    "agriculture",
    "food insecurity",
    "environmental cleanup",
    "climate",
    "resilience",
    "government",
]
PRESSURE_EVIDENCE_TERMS = [
    "infrastructure", "bridge", "water system", "public health", "emergency management",
    "housing", "rent", "eviction", "utility", "utility shutoff", "power shutoff",
    "hospital", "clinic", "health access", "healthcare access", "health care access",
    "wildfire", "smoke", "drought", "flood", "recovery",
    "transit", "ferry", "road closure", "bridge closure", "service disruption",
    "food bank", "snap", "food support", "food insecurity", "food assistance",
    "layoff", "layoffs", "wages", "job cuts", "unemployment",
    "school closure", "school closures", "budget cuts", "school budget cuts",
    "emergency services", "public safety", "staffing shortage", "service strain",
    "government services", "government service", "access to services",
]
REGION_TERMS = {
    "WA": ["washington", " wa ", "seattle", "spokane", "tacoma", "olympia", "yakima", "puget sound"],
    "OR": ["oregon", " or ", "portland", "eugene", "salem", "medford", "bend"],
    "ID": ["idaho", " id ", "boise", "meridian", "nampa", "coeur d'alene"],
    "PNW": ["pacific northwest", "cascadia", "northwest"],
}
EXCLUDED_TERMS = {
    "sports": ["sports", "game", "coach", "tournament", "playoff", "score"],
    "entertainment": ["movie", "concert", "album", "celebrity"],
    "opinion": ["opinion", "editorial", "letter to the editor"],
    "investor_business_only": ["earnings call", "investor", "shareholder", "stock price", "merger", "acquisition"],
    "generic_politics": ["campaign rally", "election poll", "party endorsement", "fundraising event", "candidate debate"],
    "celebrity_or_lifestyle": ["celebrity", "fashion week", "red carpet", "movie premiere"],
    "unrelated_crime": ["police blotter", "mugshot", "celebrity arrest"],
}
NAV_LINK_TEXT = {
    "about",
    "accessibility",
    "back",
    "calendar",
    "careers",
    "contact",
    "facebook",
    "home",
    "instagram",
    "linkedin",
    "login",
    "menu",
    "next",
    "privacy",
    "search",
    "share",
    "subscribe",
    "twitter",
    "x",
    "youtube",
}
NAV_PATH_PARTS = {
    "/about",
    "/contact",
    "/events",
    "/facebook",
    "/instagram",
    "/linkedin",
    "/privacy",
    "/search",
    "/subscribe",
    "/twitter",
    "/x.com",
    "/youtube",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: str) -> str:
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def load_source_registry(root: Path, path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = root / (path or REGISTRY_PATH)
    if not registry_path.exists():
        return []
    text = registry_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        if isinstance(payload, dict):
            sources = payload.get("sources", [])
        else:
            sources = payload
        return [item for item in sources if isinstance(item, dict)] if isinstance(sources, list) else []
    except Exception:
        return parse_simple_registry_yaml(text)


def parse_simple_registry_yaml(text: str) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#") or raw_line.strip() == "sources:":
            continue
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            if current:
                sources.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_yaml_scalar(value)
    if current:
        sources.append(current)
    return sources


def parse_yaml_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        return [item.strip().strip("\"'") for item in text[1:-1].split(",") if item.strip()]
    try:
        return int(text)
    except ValueError:
        return text.strip("\"'")


def source_operational_state(source: dict[str, Any]) -> str:
    if source.get("enabled") is False:
        return "disabled"
    status = str(source.get("operational_status") or source.get("status") or "").strip().lower()
    if status in {
        "disabled",
        "diagnostics_only",
        "manual_only",
        "degraded",
        "replaced",
        "disabled_stale_url",
        "disabled_unrecoverable_403",
        "needs_manual_review",
        "rate_limited",
    }:
        return status
    if source.get("diagnostics_only") is True:
        return "diagnostics_only"
    if source.get("manual_only") is True:
        return "manual_only"
    return "enabled"


def enabled_sources(sources: list[dict[str, Any]], include_diagnostics: bool = False) -> list[dict[str, Any]]:
    allowed_states = {"enabled", "degraded", "needs_manual_review", "rate_limited"}
    if include_diagnostics:
        allowed_states.update({"diagnostics_only", "manual_only"})
    return [source for source in sources if source_operational_state(source) in allowed_states]


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def summarize_registry_warnings(warnings: list[str], diagnostics: list[dict[str, Any]], weak_date_count: int) -> tuple[list[str], list[str]]:
    actionable: list[str] = []
    informational: list[str] = []
    if weak_date_count:
        informational.append(f"weak date basis warnings (deduped): {weak_date_count} item(s)")
    fetch_error_sources = [str(item.get("source_id") or "unknown") for item in diagnostics if item.get("errors")]
    if fetch_error_sources:
        counts = Counter(fetch_error_sources)
        top = ", ".join(f"{source}:{count}" for source, count in sorted(counts.items()))
        actionable.append(f"registry fetch failures by source (deduped): {top}")
    unique = dedupe_preserve_order([str(item) for item in warnings if str(item).strip()])
    return actionable + informational + unique, unique


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_visible_date(value: str | None) -> datetime | None:
    text = strip_markup(value)
    if not text:
        return None
    iso_match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b", text)
    if iso_match:
        year, month, day = iso_match.groups()
        return parse_datetime(f"{int(year):04d}-{int(month):02d}-{int(day):02d}T00:00:00Z")
    month_match = re.search(
        r"\b("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        r")\.?\s+([0-3]?\d),?\s+(20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if month_match:
        return parse_datetime(month_match.group(0))
    return None


def format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def strip_markup(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def xml_text(parent: ET.Element, names: list[str]) -> str:
    for name in names:
        found = parent.find(name)
        if found is not None and found.text:
            return strip_markup(found.text)
    for child in parent:
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in {name.rsplit("}", 1)[-1].lower() for name in names} and child.text:
            return strip_markup(child.text)
    return ""


def xml_link(parent: ET.Element) -> str:
    direct = xml_text(parent, ["link"])
    if direct:
        return direct
    for child in parent:
        if child.tag.rsplit("}", 1)[-1].lower() == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def parse_feed_items(body: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return [], [f"invalid XML feed: {exc}"]
    items = root.findall(".//item")
    if not items:
        items = [entry for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")]
    records = []
    for item in items:
        title = xml_text(item, ["title"])
        url = xml_link(item)
        published_raw = xml_text(item, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"])
        summary = xml_text(item, ["description", "summary", "content", "{http://www.w3.org/2005/Atom}summary"])
        records.append(
            {
                "title": title,
                "url": url,
                "published_at": format_datetime(parse_datetime(published_raw)),
                "summary_or_snippet": summary,
                "raw_published_at": published_raw,
            }
        )
    return records, warnings


class OfficialPageLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, Any]] = []
        self._skip_depth = 0
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() in {"script", "style", "svg", "nav", "footer", "header"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag.lower() == "a" and attr_map.get("href"):
            self._current = {"href": attr_map.get("href", ""), "text": "", "attrs": attr_map}
            return
        if self._current is not None and tag.lower() == "time":
            for key in ("datetime", "title", "aria-label"):
                if attr_map.get(key):
                    self._current.setdefault("date_candidates", []).append(attr_map[key])

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current is None:
            return
        self._current["text"] = f"{self._current.get('text', '')} {data}"

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def parse_official_page_links(body: str, source_url: str) -> list[dict[str, Any]]:
    parser = OfficialPageLinkParser()
    parser.feed(body)
    base = urllib.parse.urlsplit(source_url)
    source_host = base.netloc.lower().removeprefix("www.")
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        href = str(link.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute_url = urllib.parse.urljoin(source_url, href)
        parsed = urllib.parse.urlsplit(absolute_url)
        host = parsed.netloc.lower().removeprefix("www.")
        title = strip_markup(str(link.get("text") or ""))
        if not parsed.scheme.startswith("http") or host != source_host:
            records.append({"url": absolute_url, "title": title, "excluded_reason": "off_domain"})
            continue
        normalized_url = canonicalize_url(absolute_url)
        if normalized_url in seen_urls:
            records.append({"url": absolute_url, "title": title, "excluded_reason": "duplicate_link"})
            continue
        seen_urls.add(normalized_url)
        reason = official_link_exclusion_reason(title, absolute_url, source_url)
        if reason:
            records.append({"url": absolute_url, "title": title, "excluded_reason": reason})
            continue
        attrs = link.get("attrs") if isinstance(link.get("attrs"), dict) else {}
        candidates = list(link.get("date_candidates") or [])
        candidates.extend(str(attrs.get(key) or "") for key in ("datetime", "data-date", "data-published", "aria-label", "title"))
        candidates.append(title)
        parsed_date = next((parse_visible_date(candidate) for candidate in candidates if parse_visible_date(candidate)), None)
        records.append(
            {
                "title": title,
                "url": absolute_url,
                "published_at": format_datetime(parsed_date),
                "summary_or_snippet": "",
                "raw_published_at": format_datetime(parsed_date),
            }
        )
    return records


def official_link_exclusion_reason(title: str, url: str, source_url: str) -> str | None:
    text = strip_markup(title).lower()
    if len(text) < 8:
        return "navigation_or_empty_text"
    if text in NAV_LINK_TEXT:
        return "navigation_or_footer_link"
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.lower().rstrip("/")
    source_path = urllib.parse.urlsplit(source_url).path.lower().rstrip("/")
    if path == source_path:
        return "same_page_link"
    if any(part in path for part in NAV_PATH_PARTS):
        return "navigation_or_footer_link"
    if re.search(r"\.(pdf|jpg|jpeg|png|gif|svg|zip)$", path):
        return "non_html_asset"
    return None


def cache_key(source: dict[str, Any], week_start: date, week_end: date, cache_date: str) -> str:
    payload = {
        "source_id": source.get("source_id"),
        "url": source.get("url"),
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "cache_date": cache_date,
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def cache_path(root: Path, source: dict[str, Any], week_start: date, week_end: date, retrieved_at: str) -> Path:
    cache_date = retrieved_at[:10]
    safe_source_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(source.get("source_id") or "unknown")).strip("-") or "unknown"
    return root / REGISTRY_CACHE_ROOT / safe_source_id / f"{cache_key(source, week_start, week_end, cache_date)}.json"


def read_cache(path: Path, refresh_cache: bool) -> dict[str, Any] | None:
    if refresh_cache or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cached_at = parse_datetime(str(payload.get("cached_at") or ""))
    if cached_at and datetime.now(timezone.utc) - cached_at > timedelta(days=7):
        return None
    if isinstance(payload.get("items"), list):
        return payload
    return None


def write_cache(path: Path, metadata: dict[str, Any], items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"cached_at": utc_now(), "metadata": metadata, "items": items},
            indent=2,
        ),
        encoding="utf-8",
    )


def fetch_feed(source: dict[str, Any], root: Path, week_start: date, week_end: date, retrieved_at: str, refresh_cache: bool = False, timeout_seconds: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = cache_path(root, source, week_start, week_end, retrieved_at)
    cached = read_cache(path, refresh_cache)
    diagnostics = {
        "source_id": source.get("source_id"),
        "source_name": source.get("name"),
        "url": source.get("url"),
        "source_type": source.get("source_type"),
        "geographic_scope": source.get("geographic_scope"),
        "cache_path": str(path),
        "cache_hit": bool(cached),
        "cache_miss": not bool(cached),
        "raw_count": 0,
        "warnings": [],
        "errors": [],
        "fetch_backend": "auto",
        "fallback_used": False,
        "python_fetch_error": None,
        "curl_exit_code": None,
        "curl_stderr_tail": None,
        "tls_or_revocation_hint": None,
        "recommendation": None,
        "bytes_read": 0,
        "fetch_successful": False,
        "status_code": None,
        "content_type": "",
        "failure_reason": None,
        "selected_backend": "auto",
        "parse_empty": False,
    }
    if cached:
        items = [item for item in cached.get("items", []) if isinstance(item, dict)]
        diagnostics["raw_count"] = len(items)
        diagnostics["fetch_backend"] = "cache"
        return items, diagnostics
    result = fetch_public_url(str(source.get("url")), timeout_seconds, DEFAULT_USER_AGENT)
    for key in ["fetch_backend", "fallback_used", "python_fetch_error", "curl_exit_code", "curl_stderr_tail", "tls_or_revocation_hint", "recommendation", "bytes_read"]:
        diagnostics[key] = result.diagnostics.get(key)
    diagnostics["status_code"] = result.status_code
    diagnostics["content_type"] = result.content_type
    diagnostics["failure_reason"] = result.diagnostics.get("failure_reason")
    diagnostics["selected_backend"] = result.diagnostics.get("selected_backend", diagnostics.get("fetch_backend"))
    if not result.ok:
        diagnostics["errors"].append(str(result.diagnostics.get("python_fetch_error") or result.diagnostics.get("curl_stderr_tail") or "fetch failed"))
        if result.diagnostics.get("recommendation"):
            diagnostics["warnings"].append(str(result.diagnostics.get("recommendation")))
        return [], diagnostics
    diagnostics["fetch_successful"] = True
    body = result.body
    if not body.strip():
        diagnostics["warnings"].append("empty response body")
        diagnostics["failure_reason"] = "empty_response"
        return [], diagnostics
    items, parse_warnings = parse_feed_items(body)
    diagnostics["warnings"].extend(parse_warnings)
    diagnostics["raw_count"] = len(items)
    diagnostics["parse_empty"] = bool(not items and not diagnostics["errors"])
    if not parse_warnings:
        write_cache(path, diagnostics, items)
    return items, diagnostics


def fetch_official_page(source: dict[str, Any], root: Path, week_start: date, week_end: date, retrieved_at: str, refresh_cache: bool = False, timeout_seconds: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = cache_path(root, source, week_start, week_end, retrieved_at)
    cached = read_cache(path, refresh_cache)
    diagnostics = {
        "source_id": source.get("source_id"),
        "source_name": source.get("name"),
        "url": source.get("url"),
        "source_type": source.get("source_type"),
        "geographic_scope": source.get("geographic_scope"),
        "cache_path": str(path),
        "cache_hit": bool(cached),
        "cache_miss": not bool(cached),
        "raw_count": 0,
        "included_links": [],
        "excluded_links": [],
        "warnings": [],
        "errors": [],
        "fetch_backend": "auto",
        "fallback_used": False,
        "python_fetch_error": None,
        "curl_exit_code": None,
        "curl_stderr_tail": None,
        "tls_or_revocation_hint": None,
        "recommendation": None,
        "bytes_read": 0,
        "fetch_successful": False,
        "status_code": None,
        "content_type": "",
        "failure_reason": None,
        "selected_backend": "auto",
        "parse_empty": False,
        "same_domain_links_only": True,
    }
    if cached:
        items = [item for item in cached.get("items", []) if isinstance(item, dict)]
        diagnostics["raw_count"] = len(items)
        diagnostics["fetch_backend"] = "cache"
        return items, diagnostics
    result = fetch_public_url(str(source.get("url")), timeout_seconds, DEFAULT_USER_AGENT)
    for key in ["fetch_backend", "fallback_used", "python_fetch_error", "curl_exit_code", "curl_stderr_tail", "tls_or_revocation_hint", "recommendation", "bytes_read"]:
        diagnostics[key] = result.diagnostics.get(key)
    diagnostics["status_code"] = result.status_code
    diagnostics["content_type"] = result.content_type
    diagnostics["failure_reason"] = result.diagnostics.get("failure_reason")
    diagnostics["selected_backend"] = result.diagnostics.get("selected_backend", diagnostics.get("fetch_backend"))
    if not result.ok:
        diagnostics["errors"].append(str(result.diagnostics.get("python_fetch_error") or result.diagnostics.get("curl_stderr_tail") or "fetch failed"))
        if result.diagnostics.get("recommendation"):
            diagnostics["warnings"].append(str(result.diagnostics.get("recommendation")))
        return [], diagnostics
    diagnostics["fetch_successful"] = True
    parsed_links = parse_official_page_links(result.body, str(source.get("url") or ""))
    items = []
    for link in parsed_links:
        if link.get("excluded_reason"):
            diagnostics["excluded_links"].append({"url": link.get("url"), "title": link.get("title"), "reason": link.get("excluded_reason")})
            continue
        diagnostics["included_links"].append({"url": link.get("url"), "title": link.get("title"), "published_at": link.get("published_at")})
        items.append(link)
    diagnostics["raw_count"] = len(items)
    diagnostics["parse_empty"] = bool(not items and not diagnostics["errors"])
    if not diagnostics["errors"]:
        write_cache(path, diagnostics, items)
    return items, diagnostics


def matched_region_terms(text: str) -> list[str]:
    padded = f" {text.lower()} "
    matches: list[str] = []
    for terms in REGION_TERMS.values():
        for term in terms:
            if term in padded:
                matches.append(term.strip())
    return sorted(set(matches))


def infer_state(text: str, source: dict[str, Any]) -> str | None:
    scope = str(source.get("state_scope") or "")
    if scope in {"WA", "OR", "ID", "PNW"}:
        return scope
    padded = f" {text.lower()} "
    for state, terms in REGION_TERMS.items():
        if any(term in padded for term in terms):
            return state
    return None


def infer_category(text: str, source: dict[str, Any]) -> str | None:
    lowered = text.lower()
    for hint in source.get("category_hints") or []:
        normalized = str(hint).replace("_", " ").lower()
        if normalized in lowered:
            return str(hint)
    hints = source.get("category_hints") or []
    if hints:
        return str(hints[0])
    for term in SYSTEM_TERMS:
        if term in lowered:
            return term.replace(" ", "_")
    return None


def should_keep_record(record: dict[str, Any], source: dict[str, Any]) -> str | None:
    if not record.get("url"):
        return "missing_url"
    text = f"{record.get('title', '')} {record.get('summary_or_snippet', '')} {record.get('publisher', '')} {record.get('url', '')}".lower()
    for reason, terms in EXCLUDED_TERMS.items():
        if any(term in text for term in terms):
            return reason
    source_scope = str(source.get("state_scope") or "")
    explicit_scope = source_scope in {"WA", "OR", "ID", "PNW"}
    has_region = bool(matched_region_terms(text)) or explicit_scope
    has_system = any(term in text for term in SYSTEM_TERMS)
    has_pressure_evidence = any(term in text for term in PRESSURE_EVIDENCE_TERMS)
    if not has_region:
        return "no_wa_or_id_connection"
    if not has_system:
        return "no_public_systems_term"
    if not has_pressure_evidence:
        return "no_explicit_pressure_evidence"
    return None


def normalize_registry_item(item: dict[str, Any], source: dict[str, Any], week_start: date, week_end: date, retrieved_at: str) -> tuple[dict[str, Any] | None, str | None]:
    published_at = item.get("published_at")
    parsed_published = parse_datetime(published_at)
    date_basis = "published_at"
    date_basis_confidence = "high"
    date_basis_note = "Source provided parseable published_at metadata."
    warning: str | None = None
    if parsed_published is None:
        if source.get("source_type") in OFFICIAL_PAGE_SOURCE_TYPES and str(source.get("refresh_mode") or "") != "current":
            return None, "weak_date_basis"
        date_basis = "unknown" if source.get("source_type") in OFFICIAL_PAGE_SOURCE_TYPES else "retrieved_only"
        date_basis_confidence = "low"
        date_basis_note = "published_at unavailable or unparseable; retrieved_at is not evidence of event date."
        warning = f"{source.get('source_id')} item has weak date basis; published_at unavailable or unparseable"
    elif not (week_start <= parsed_published.date() <= week_end):
        return None, "outside_date_window"
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    publisher = str(source.get("publisher") or source.get("name") or "").strip()
    summary = str(item.get("summary_or_snippet") or "").strip()
    text = f"{title} {summary} {publisher} {url}"
    record = {
        "source_record_id": f"registry-{stable_id(str(source.get('source_id') or ''), url, title)}",
        "title": title,
        "url": url,
        "publisher": publisher,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "summary_or_snippet": summary,
        "source_type": source.get("source_type") or "rss",
        "provider_id": "registry",
        "provider_name": "Cascadia source registry",
        "source_id": source.get("source_id"),
        "source_name": source.get("name"),
        "tier": source.get("tier"),
        "query_used": str(source.get("url") or ""),
        "search_start_date": week_start.isoformat(),
        "search_end_date": week_end.isoformat(),
        "region_terms_matched": matched_region_terms(text),
        "category_hint": infer_category(text, source),
        "state_hint": infer_state(text, source),
        "reliability_tier": source.get("reliability_tier") or "public-source",
        "traceability_note": "Retrieved from curated free Cascadia source registry; URL, title, publisher, and date metadata preserved when supplied.",
        "date_basis": date_basis,
        "date_basis_confidence": date_basis_confidence,
        "date_basis_note": date_basis_note,
    }
    return record, warning


def collect_registry_sources(
    root: Path,
    week_start: date,
    week_end: date,
    retrieved_at: str | None = None,
    refresh_cache: bool = False,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    retrieved_at = retrieved_at or utc_now()
    registry = load_source_registry(root)
    sources = enabled_sources(registry, include_diagnostics=include_diagnostics)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    excluded: Counter[str] = Counter()
    diagnostics: list[dict[str, Any]] = []
    planned = len([source for source in sources if source.get("source_type") in FETCHABLE_SOURCE_TYPES])
    official_pages_planned = len([source for source in sources if source.get("source_type") in OFFICIAL_PAGE_SOURCE_TYPES])
    official_pages_run = 0
    official_links_found = 0
    official_links_saved = 0
    official_links_excluded = Counter()
    weak_date_count = 0
    unsupported_source_type_count = 0
    run = 0
    cache_hits = 0
    cache_misses = 0
    fetch_errors = 0
    raw_count = 0
    for source in sources:
        source_type = str(source.get("source_type") or "")
        state = source_operational_state(source)
        if source_type not in FETCHABLE_SOURCE_TYPES:
            unsupported_source_type_count += 1
            diagnostics.append(
                {
                    "source_id": source.get("source_id"),
                    "source_name": source.get("name"),
                    "source_type": source_type,
                    "url": source.get("url"),
                    "skipped": True,
                    "skip_reason": "unsupported_source_type",
                    "source_operational_state": state,
                    "recommendation": None,
                }
            )
            continue
        run += 1
        if source_type in OFFICIAL_PAGE_SOURCE_TYPES:
            official_pages_run += 1
            items, diag = fetch_official_page(source, root, week_start, week_end, retrieved_at, refresh_cache=refresh_cache)
            official_links_found += len(diag.get("included_links", [])) + len(diag.get("excluded_links", []))
            for excluded_link in diag.get("excluded_links", []):
                official_links_excluded[str(excluded_link.get("reason") or "excluded")] += 1
        else:
            items, diag = fetch_feed(source, root, week_start, week_end, retrieved_at, refresh_cache=refresh_cache)
        diagnostics.append(diag)
        cache_hits += int(bool(diag.get("cache_hit")))
        cache_misses += int(bool(diag.get("cache_miss")))
        fetch_errors += len(diag.get("errors", []))
        warnings.extend(str(item) for item in diag.get("warnings", []))
        errors.extend(str(item) for item in diag.get("errors", []))
        raw_count += len(items)
        for item in items:
            record, warning_or_reason = normalize_registry_item(item, source, week_start, week_end, retrieved_at)
            if record is None:
                excluded[str(warning_or_reason or "excluded")] += 1
                if warning_or_reason == "weak_date_basis":
                    weak_date_count += 1
                continue
            if warning_or_reason:
                warnings.append(warning_or_reason)
                if "weak date basis" in warning_or_reason:
                    weak_date_count += 1
            reason = should_keep_record(record, source)
            if reason:
                excluded[reason] += 1
                if source_type in OFFICIAL_PAGE_SOURCE_TYPES:
                    official_links_excluded[reason] += 1
                continue
            if state in {"diagnostics_only", "degraded"}:
                continue
            if source_type in OFFICIAL_PAGE_SOURCE_TYPES:
                official_links_saved += 1
            records.append(record)
    deduped, duplicates_removed = dedupe_registry_records(records)
    if duplicates_removed:
        excluded["duplicate"] += duplicates_removed
    summary_warnings, detailed_warnings = summarize_registry_warnings(warnings, diagnostics, weak_date_count)
    source_status_counts = dict(
        sorted(Counter(source_operational_state(source) for source in registry if isinstance(source, dict)).items())
    )
    source_health_summary = {
        "sources_attempted": run,
        "sources_succeeded": max(0, run - len([item for item in diagnostics if item.get("errors")])),
        "sources_failed": len([item for item in diagnostics if item.get("errors")]),
        "disabled_or_replaced_sources": [
            {
                "source_id": source.get("source_id"),
                "operational_status": source_operational_state(source),
                "reason": source.get("status_reason") or source.get("notes"),
            }
            for source in registry
            if source_operational_state(source) in {"disabled", "replaced", "disabled_stale_url", "disabled_unrecoverable_403"}
        ],
    }
    report = {
        "fetch_backend": fetch_backend(),
        "fallback_used": any(bool(item.get("fallback_used")) for item in diagnostics),
        "python_fetch_error": next((item.get("python_fetch_error") for item in diagnostics if item.get("python_fetch_error")), None),
        "curl_exit_code": next((item.get("curl_exit_code") for item in diagnostics if item.get("curl_exit_code") is not None), None),
        "curl_stderr_tail": next((item.get("curl_stderr_tail") for item in diagnostics if item.get("curl_stderr_tail")), None),
        "tls_or_revocation_hint": next((item.get("tls_or_revocation_hint") for item in diagnostics if item.get("tls_or_revocation_hint")), None),
        "source_status_counts": source_status_counts,
        "source_health_summary": source_health_summary,
        "registry_sources_configured": len(registry),
        "registry_sources_enabled": len(sources),
        "registry_sources_planned": planned,
        "registry_sources_run": run,
        "registry_cache_hits": cache_hits,
        "registry_cache_misses": cache_misses,
        "registry_fetch_errors": fetch_errors,
        "registry_records_raw": raw_count,
        "registry_records_saved": len(deduped),
        "registry_records_excluded": sum(excluded.values()),
        "registry_exclusion_reasons": dict(sorted(excluded.items())),
        "official_pages_planned": official_pages_planned,
        "official_pages_run": official_pages_run,
        "official_links_found": official_links_found,
        "official_links_saved": official_links_saved,
        "official_links_excluded": sum(official_links_excluded.values()),
        "official_exclusion_reasons": dict(sorted(official_links_excluded.items())),
        "weak_date_count": weak_date_count,
        "same_domain_links_only": True,
        "unsupported_source_type_count": unsupported_source_type_count,
        "records_by_source_id": dict(sorted(Counter(str(record.get("source_id") or "unknown") for record in deduped).items())),
        "records_by_tier": dict(sorted(Counter(str(record.get("tier") or "unknown") for record in deduped).items())),
        "records_by_category_hint": dict(sorted(Counter(str(record.get("category_hint") or "unknown") for record in deduped).items())),
        "diagnostics": diagnostics,
        "warnings": summary_warnings,
        "warnings_detailed": detailed_warnings,
        "errors": errors,
    }
    report["recommendation"] = next((item.get("recommendation") for item in diagnostics if item.get("recommendation")), None)
    return {"records": deduped, "report": report, "warnings": summary_warnings, "warnings_detailed": detailed_warnings, "errors": errors}


def dedupe_registry_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen_urls: set[str] = set()
    kept: list[dict[str, Any]] = []
    duplicates = 0
    for record in records:
        url_key = canonicalize_url(str(record.get("url") or ""))
        if url_key in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(url_key)
        kept.append(record)
    return kept, duplicates


def write_registry_report(root: Path, week_start: date, week_end: date, report: dict[str, Any], dry_run: bool = False) -> Path:
    path = root.resolve() / CASCADE_DATA_ROOT / "sources" / f"{week_start.isoformat()}_{week_end.isoformat()}" / "registry_source_report.json"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
