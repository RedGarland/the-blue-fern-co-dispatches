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
from hashlib import sha256
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_normalize import canonicalize_url


REGISTRY_PATH = CASCADE_DATA_ROOT / "source_registry.yml"
REGISTRY_CACHE_ROOT = CASCADE_DATA_ROOT / "cache" / "registry"
FETCHABLE_SOURCE_TYPES = {"rss", "atom", "alert_feed"}
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
    "economy",
    "labor",
    "layoffs",
    "agriculture",
    "food insecurity",
    "environmental cleanup",
    "climate",
    "resilience",
    "government",
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


def enabled_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [source for source in sources if source.get("enabled", False)]


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
    path.write_text(json.dumps({"cached_at": utc_now(), "metadata": metadata, "items": items}, indent=2), encoding="utf-8")


def fetch_feed(source: dict[str, Any], root: Path, week_start: date, week_end: date, retrieved_at: str, refresh_cache: bool = False, timeout_seconds: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = cache_path(root, source, week_start, week_end, retrieved_at)
    cached = read_cache(path, refresh_cache)
    diagnostics = {
        "source_id": source.get("source_id"),
        "source_name": source.get("name"),
        "url": source.get("url"),
        "source_type": source.get("source_type"),
        "cache_path": str(path),
        "cache_hit": bool(cached),
        "cache_miss": not bool(cached),
        "raw_count": 0,
        "warnings": [],
        "errors": [],
    }
    if cached:
        items = [item for item in cached.get("items", []) if isinstance(item, dict)]
        diagnostics["raw_count"] = len(items)
        return items, diagnostics
    request = urllib.request.Request(str(source.get("url")), headers={"User-Agent": DEFAULT_USER_AGENT})  # noqa: S310 - curated public source URL
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        diagnostics["errors"].append(str(exc))
        return [], diagnostics
    if not body.strip():
        diagnostics["warnings"].append("empty response body")
        return [], diagnostics
    items, parse_warnings = parse_feed_items(body)
    diagnostics["warnings"].extend(parse_warnings)
    diagnostics["raw_count"] = len(items)
    if not parse_warnings:
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
    for term in SYSTEM_TERMS:
        if term in lowered:
            return term.replace(" ", "_")
    hints = source.get("category_hints") or []
    return str(hints[0]) if hints else None


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
    has_system = any(term in text for term in SYSTEM_TERMS) or int(source.get("tier") or 0) == 1
    if not has_region:
        return "no_wa_or_id_connection"
    if not has_system:
        return "no_public_systems_term"
    return None


def normalize_registry_item(item: dict[str, Any], source: dict[str, Any], week_start: date, week_end: date, retrieved_at: str) -> tuple[dict[str, Any] | None, str | None]:
    published_at = item.get("published_at")
    parsed_published = parse_datetime(published_at)
    date_basis = "published_at"
    warning: str | None = None
    if parsed_published is None:
        date_basis = "retrieved_at_weak"
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
        "traceability_note": "Retrieved from curated free Cascadia source registry; URL and feed metadata preserved when supplied.",
        "date_basis": date_basis,
    }
    return record, warning


def collect_registry_sources(root: Path, week_start: date, week_end: date, retrieved_at: str | None = None, refresh_cache: bool = False) -> dict[str, Any]:
    root = root.resolve()
    retrieved_at = retrieved_at or utc_now()
    registry = load_source_registry(root)
    sources = enabled_sources(registry)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    excluded: Counter[str] = Counter()
    diagnostics: list[dict[str, Any]] = []
    planned = len([source for source in sources if source.get("source_type") in FETCHABLE_SOURCE_TYPES])
    run = 0
    cache_hits = 0
    cache_misses = 0
    fetch_errors = 0
    raw_count = 0
    for source in sources:
        source_type = str(source.get("source_type") or "")
        if source_type not in FETCHABLE_SOURCE_TYPES:
            diagnostics.append(
                {
                    "source_id": source.get("source_id"),
                    "source_name": source.get("name"),
                    "source_type": source_type,
                    "url": source.get("url"),
                    "skipped": True,
                    "skip_reason": "non_feed_registry_entry",
                }
            )
            continue
        run += 1
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
                continue
            if warning_or_reason:
                warnings.append(warning_or_reason)
            reason = should_keep_record(record, source)
            if reason:
                excluded[reason] += 1
                continue
            records.append(record)
    deduped, duplicates_removed = dedupe_registry_records(records)
    if duplicates_removed:
        excluded["duplicate"] += duplicates_removed
    report = {
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
        "records_by_source_id": dict(sorted(Counter(str(record.get("source_id") or "unknown") for record in deduped).items())),
        "records_by_tier": dict(sorted(Counter(str(record.get("tier") or "unknown") for record in deduped).items())),
        "records_by_category_hint": dict(sorted(Counter(str(record.get("category_hint") or "unknown") for record in deduped).items())),
        "diagnostics": diagnostics,
        "warnings": warnings,
        "errors": errors,
    }
    return {"records": deduped, "report": report, "warnings": warnings, "errors": errors}


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
