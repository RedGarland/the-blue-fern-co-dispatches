from __future__ import annotations

import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_curate import curate_sources
from bluefern_dispatches.cascadia_fetch import fetch_public_url
from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_normalize import canonicalize_url, normalize_sources
from bluefern_dispatches.cascadia_source_registry import collect_registry_sources, write_registry_report


DEFAULT_CONFIG_PATH = CASCADE_DATA_ROOT / "historical_sources.yml"
CACHE_ROOT = CASCADE_DATA_ROOT / "cache" / "gdelt"
MANUAL_SOURCE_FILENAME = "manual_sources.json"
MANUAL_SOURCE_EXAMPLE_FILENAME = "manual_sources.example.json"

REGION_TERMS = {
    "WA": ["washington", "seattle", "spokane", "tacoma"],
    "OR": ["oregon", "portland", "eugene", "salem"],
    "ID": ["idaho", "boise"],
    "regional": ["pacific northwest", "cascadia"],
}
EXCLUDED_CONTEXT_TERMS = {
    "sports": ["sports", "game", "coach", "tournament", "playoff", "score"],
    "entertainment": ["movie", "concert", "album", "celebrity", "festival"],
    "opinion": ["opinion", "editorial", "letter to the editor"],
}
CATEGORY_HINTS = {
    "Infrastructure": ["infrastructure", "bridge", "road closure", "water"],
    "Healthcare": ["public health", "hospital", "healthcare", "medicaid"],
    "Housing and homelessness": ["housing", "homelessness"],
    "Environment and climate": ["wildfire", "drought", "flood", "climate", "environmental cleanup", "resilience"],
    "Energy and utilities": ["power outage", "utility"],
    "Transportation": ["transportation", "rail", "port", "road closure", "bridge"],
    "Public safety": ["public safety", "emergency"],
    "Economy and labor": ["economy", "labor", "layoffs"],
    "Food and agriculture": ["agriculture", "food insecurity"],
}
KNOWN_REGIONAL_DOMAINS = [
    "seattletimes.com",
    "spokesman.com",
    "oregonlive.com",
    "opb.org",
    "boisedev.com",
    "idahostatesman.com",
    "katu.com",
    "king5.com",
    "kgw.com",
    "kxly.com",
]
PROVIDER_BACKOFF_UNTIL: dict[str, float] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_provider_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{14}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}T{text[8:10]}:{text[10:12]}:{text[12:14]}Z"
    if re.fullmatch(r"\d{8}T\d{6}Z", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}T{text[9:11]}:{text[11:13]}:{text[13:15]}Z"
    return text


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            pass
        return text.strip("\"'")


def load_historical_config(root: Path, path: Path | None = None) -> dict[str, Any]:
    config_path = root / (path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return default_config()
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        if isinstance(payload, dict):
            merged = default_config()
            merged.update(payload)
            for section in ["query_groups", "region_filters"]:
                merged[section] = {**default_config().get(section, {}), **payload.get(section, {})}
            merged["providers"] = sorted(merged.get("providers", []), key=lambda item: int(item.get("priority", 999)))
            return merged
    except Exception:
        pass
    config: dict[str, Any] = {}
    current_section: str | None = None
    current_item: dict[str, Any] | None = None
    current_list: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1]
            config[current_section] = [] if current_section == "providers" else {}
            current_item = None
            current_list = None
            continue
        if current_section == "providers":
            if stripped.startswith("- "):
                current_item = {}
                config["providers"].append(current_item)
                stripped = stripped[2:].strip()
                current_list = None
                if stripped and ":" in stripped:
                    key, value = stripped.split(":", 1)
                    current_item[key.strip()] = parse_scalar(value)
                continue
            if current_item is not None and ":" in stripped:
                key, value = stripped.split(":", 1)
                current_item[key.strip()] = parse_scalar(value)
            continue
        if current_section and current_section != "providers":
            section = config[current_section]
            if stripped.endswith(":"):
                current_list = stripped[:-1]
                section[current_list] = []
                continue
            if stripped.startswith("- ") and current_list:
                section[current_list].append(stripped[2:].strip().strip("\"'"))
                continue
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                section[key.strip()] = parse_scalar(value)
    merged = default_config()
    merged.update(config)
    for section in ["query_groups", "region_filters"]:
        merged[section] = {**default_config().get(section, {}), **config.get(section, {})}
    merged["providers"] = sorted(merged.get("providers", []), key=lambda item: int(item.get("priority", 999)))
    return merged


def default_config() -> dict[str, Any]:
    return {
        "providers": [
            {
                "provider_id": "gdelt",
                "provider_name": "GDELT 2.1 Document API",
                "enabled": True,
                "base_url": "https://api.gdeltproject.org/api/v2/doc/doc",
                "max_results_per_query": 12,
                "timeout_seconds": 6,
                "delay_seconds": 2,
                "max_retries": 2,
                "backoff_base_seconds": 5,
                "backoff_max_seconds": 60,
                "cache_enabled": True,
                "cache_ttl_days": 14,
                "user_agent": "BlueFernDispatches/0.1 historical-source-retrieval",
                "reliability_tier": "unknown",
                "priority": 2,
            },
            {"provider_id": "manual", "provider_name": "Project-local manual historical sources", "enabled": True, "reliability_tier": "editorial-record", "priority": 1},
        ],
        "query_groups": {
            "region_terms": ["Washington", "Oregon", "Idaho", "Pacific Northwest", "Cascadia", "Seattle", "Portland", "Spokane", "Boise", "Tacoma", "Eugene", "Salem"],
            "system_groups": [
                "infrastructure/utilities|infrastructure|utility|utilities|power|water|transportation|bridge|road closure|rail|port",
                "health/public services|public health|hospital|healthcare|Medicaid|school|public services",
                "environment/climate/wildfire|wildfire|drought|flood|climate|environmental cleanup|resilience|air quality",
                "economy/labor/housing|economy|labor|layoffs|housing|homelessness|agriculture|food insecurity",
                "public safety/emergency management|public safety|emergency|emergency management|evacuation|disaster",
            ],
            "systems_terms": [
                "infrastructure",
                "public health",
                "hospital",
                "healthcare",
                "Medicaid",
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
            ],
            "max_queries": 3,
            "max_queries_per_week": 3,
            "max_region_terms_per_query": 8,
            "system_terms_per_query": 12,
            "target_records_per_week": 8,
        },
        "region_filters": {"states": ["WA", "OR", "ID"], "known_regional_domains": KNOWN_REGIONAL_DOMAINS},
    }


def build_queries(config: dict[str, Any]) -> list[str]:
    groups = config.get("query_groups", {})
    regions = list(groups.get("region_terms", []))
    systems = list(groups.get("systems_terms", []))
    max_queries = max(1, int(groups.get("max_queries_per_week") or groups.get("max_queries", 3)))
    max_region_terms = max(1, int(groups.get("max_region_terms_per_query", 8)))
    system_terms_per_query = max(1, int(groups.get("system_terms_per_query", 12)))
    priority_regions = regions[:max_region_terms]
    if not priority_regions:
        return []
    region_query = " OR ".join(f'"{term}"' if " " in term else term for term in priority_regions)
    system_groups = groups.get("system_groups") or []
    queries = []
    for raw_group in system_groups:
        group_text = str(raw_group)
        parts = [part.strip() for part in group_text.split("|") if part.strip()]
        terms = parts[1:] if len(parts) > 1 else parts
        if not terms:
            continue
        systems_query = " OR ".join(f'"{term}"' if " " in term else term for term in terms)
        queries.append(f"({region_query}) AND ({systems_query})")
        if len(queries) >= max_queries:
            return queries
    if not systems:
        return queries
    for offset in range(0, len(systems), system_terms_per_query):
        system_chunk = systems[offset : offset + system_terms_per_query]
        systems_query = " OR ".join(f'"{term}"' if " " in term else term for term in system_chunk)
        queries.append(f"({region_query}) AND ({systems_query})")
        if len(queries) >= max_queries:
            break
    return queries


def query_group_label(query: str, config: dict[str, Any]) -> str:
    for raw_group in config.get("query_groups", {}).get("system_groups") or []:
        parts = [part.strip() for part in str(raw_group).split("|") if part.strip()]
        if len(parts) < 2:
            continue
        label = parts[0]
        terms = parts[1:]
        if all(term in query for term in terms[: min(2, len(terms))]):
            return label
    return "combined public-systems"


class HistoricalProviderRateLimited(RuntimeError):
    pass


class HistoricalSearchProvider:
    provider_id: str
    provider_name: str

    def search(self, start_date: date, end_date: date, query_terms: str, max_results: int) -> list[dict[str, Any]]:
        raise NotImplementedError


def gdelt_datetime(value: date, end: bool = False) -> str:
    suffix = "235959" if end else "000000"
    return value.strftime("%Y%m%d") + suffix


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def compact_body_sample(body: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", body.strip())[:limit]


class GDELTProvider(HistoricalSearchProvider):
    def __init__(self, config: dict[str, Any], root: Path | None = None, refresh_cache: bool = False):
        self.provider_id = str(config.get("provider_id", "gdelt"))
        self.provider_name = str(config.get("provider_name", "GDELT 2.1 Document API"))
        self.base_url = str(config.get("base_url", "https://api.gdeltproject.org/api/v2/doc/doc"))
        self.timeout_seconds = int(config.get("timeout_seconds", 15))
        self.reliability_tier = str(config.get("reliability_tier", "unknown"))
        self.delay_seconds = float(config.get("delay_seconds") or 0)
        self.max_retries = max(0, int(config.get("max_retries", 2)))
        self.backoff_base_seconds = float(config.get("backoff_base_seconds", 5))
        self.backoff_max_seconds = float(config.get("backoff_max_seconds", 60))
        self.cache_enabled = bool(config.get("cache_enabled", True))
        self.cache_ttl_days = int(config.get("cache_ttl_days", 14))
        self.user_agent = str(config.get("user_agent") or "BlueFernDispatches/0.1 historical-source-retrieval")
        self.root = root.resolve() if root else None
        self.refresh_cache = refresh_cache
        self.last_request_at = 0.0
        self.last_diagnostics: dict[str, Any] = {}

    def cache_key(self, query_terms: str, start_date: date, end_date: date, mode: str, fmt: str, max_results: int) -> str:
        payload = {
            "provider_id": self.provider_id,
            "query": query_terms,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "mode": mode,
            "format": fmt,
            "max_records": max_results,
        }
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def cache_path(self, cache_key: str) -> Path | None:
        if not self.root:
            return None
        return self.root / CACHE_ROOT / f"{cache_key}.json"

    def read_cache(self, path: Path | None) -> dict[str, Any] | None:
        if not path or not path.exists() or self.refresh_cache:
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        cached_at = str(cached.get("cached_at") or "")
        try:
            parsed = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if datetime.now(timezone.utc) - parsed > timedelta(days=self.cache_ttl_days):
            return None
        if not isinstance(cached.get("payload"), dict):
            return None
        return cached

    def write_cache(self, path: Path | None, metadata: dict[str, Any], payload: dict[str, Any]) -> None:
        if not path or not self.cache_enabled:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        cache_payload = {"cached_at": utc_now(), "metadata": metadata, "payload": payload}
        path.write_text(json.dumps(cache_payload, indent=2), encoding="utf-8")

    def throttle(self) -> None:
        if self.delay_seconds <= 0 or self.last_request_at <= 0:
            return
        remaining = self.delay_seconds - (time.monotonic() - self.last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def request_payload(self, url: str, cache_path: Path | None, metadata: dict[str, Any]) -> dict[str, Any] | None:
        cached = self.read_cache(cache_path) if self.cache_enabled else None
        attempts: list[dict[str, Any]] = []
        diagnostics = {
            **metadata,
            "request_url": url,
            "cache_path": str(cache_path) if cache_path else None,
            "cache_hit": bool(cached),
            "cache_miss": not bool(cached),
            "retry_count": 0,
            "rate_limit_count": 0,
            "attempts": attempts,
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
        }
        if cached:
            diagnostics["fetch_backend"] = "cache"
            self.last_diagnostics = diagnostics
            return cached["payload"]
        for attempt_number in range(self.max_retries + 1):
            self.throttle()
            result = fetch_public_url(url, self.timeout_seconds, self.user_agent)
            fetch_diag = result.diagnostics
            for key in ["fetch_backend", "fallback_used", "python_fetch_error", "curl_exit_code", "curl_stderr_tail", "tls_or_revocation_hint", "recommendation", "bytes_read"]:
                diagnostics[key] = fetch_diag.get(key)
            self.last_request_at = time.monotonic()
            if not result.ok and result.status_code:
                retry_after = parse_retry_after(str(fetch_diag.get("retry_after") or "")) if fetch_diag.get("retry_after") else None
                attempt = {
                    "attempt": attempt_number + 1,
                    "status_code": result.status_code,
                    "content_type": result.content_type,
                    "retry_after_seconds": retry_after,
                }
                attempts.append(attempt)
                if result.status_code == 429:
                    diagnostics["rate_limit_count"] += 1
                    if attempt_number >= self.max_retries:
                        diagnostics["errors"].append("HTTP 429 Too Many Requests after max retries")
                        self.last_diagnostics = diagnostics
                        raise HistoricalProviderRateLimited("HTTP 429 Too Many Requests after max retries")
                    diagnostics["retry_count"] += 1
                    sleep_for = retry_after if retry_after is not None else min(self.backoff_max_seconds, self.backoff_base_seconds * (2**attempt_number))
                    attempt["sleep_seconds"] = sleep_for
                    time.sleep(sleep_for)
                    continue
                diagnostics["errors"].append(str(fetch_diag.get("python_fetch_error") or f"HTTP Error {result.status_code}"))
                self.last_diagnostics = diagnostics
                return None
            if not result.ok:
                attempts.append(
                    {
                        "attempt": attempt_number + 1,
                        "status_code": None,
                        "content_type": "",
                        "error": str(fetch_diag.get("python_fetch_error") or fetch_diag.get("curl_stderr_tail") or "fetch failed"),
                        "fetch_backend": fetch_diag.get("fetch_backend"),
                        "fallback_used": bool(fetch_diag.get("fallback_used")),
                        "curl_exit_code": fetch_diag.get("curl_exit_code"),
                    }
                )
                diagnostics["warnings"].append(str(fetch_diag.get("python_fetch_error") or fetch_diag.get("curl_stderr_tail") or "fetch failed"))
                if fetch_diag.get("recommendation"):
                    diagnostics["warnings"].append(str(fetch_diag.get("recommendation")))
                self.last_diagnostics = diagnostics
                raise OSError(str(fetch_diag.get("python_fetch_error") or fetch_diag.get("curl_stderr_tail") or "fetch failed"))
            body = result.body
            attempt = {
                "attempt": attempt_number + 1,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "fetch_backend": fetch_diag.get("fetch_backend"),
                "fallback_used": bool(fetch_diag.get("fallback_used")),
            }
            attempts.append(attempt)
            if not body.strip():
                diagnostics["warnings"].append("empty response body")
                self.last_diagnostics = diagnostics
                return None
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                diagnostics["warnings"].append(f"invalid JSON response: {exc}")
                attempt["body_sample"] = compact_body_sample(body)
                self.last_diagnostics = diagnostics
                return None
            self.write_cache(cache_path, metadata, payload)
            self.last_diagnostics = diagnostics
            return payload
        self.last_diagnostics = diagnostics
        return None

    def search(self, start_date: date, end_date: date, query_terms: str, max_results: int) -> list[dict[str, Any]]:
        mode = "ArtList"
        fmt = "json"
        params = {
            "query": query_terms,
            "mode": mode,
            "format": fmt,
            "sort": "HybridRel",
            "maxrecords": str(max_results),
            "startdatetime": gdelt_datetime(start_date),
            "enddatetime": gdelt_datetime(end_date, end=True),
        }
        url = self.base_url + "?" + urllib.parse.urlencode(params)
        key = self.cache_key(query_terms, start_date, end_date, mode, fmt, max_results)
        metadata = {
            "provider_id": self.provider_id,
            "query": query_terms,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "mode": mode,
            "format": fmt,
            "max_records": max_results,
            "cache_key": key,
        }
        payload = self.request_payload(url, self.cache_path(key), metadata)
        if payload is None:
            return []
        records = []
        for item in payload.get("articles", []):
            records.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "publisher": item.get("domain") or "",
                    "published_at": normalize_provider_date(item.get("seendate")),
                    "summary_or_snippet": item.get("snippet") or item.get("summary") or "",
                    "raw_payload": item,
                    "reliability_tier": self.reliability_tier,
                }
            )
        return records


class ManualProvider(HistoricalSearchProvider):
    provider_id = "manual"
    provider_name = "Project-local manual historical sources"

    def search(self, start_date: date, end_date: date, query_terms: str, max_results: int) -> list[dict[str, Any]]:
        return []


def provider_from_config(config: dict[str, Any], root: Path | None = None, refresh_cache: bool = False) -> HistoricalSearchProvider | None:
    provider_id = str(config.get("provider_id"))
    if provider_id == "gdelt":
        return GDELTProvider(config, root=root, refresh_cache=refresh_cache)
    if provider_id == "manual":
        return ManualProvider()
    return None


def infer_state(text: str) -> str | None:
    lowered = text.lower()
    for state, terms in REGION_TERMS.items():
        if any(term in lowered for term in terms):
            return state
    return None


def matched_region_terms(text: str) -> list[str]:
    lowered = text.lower()
    matches = []
    for terms in REGION_TERMS.values():
        for term in terms:
            if term in lowered and term not in matches:
                matches.append(term)
    return matches


def infer_category(text: str) -> str:
    lowered = text.lower()
    for category, terms in CATEGORY_HINTS.items():
        if any(term.lower() in lowered for term in terms):
            return category
    return "Government and public services"


def exclusion_reason(record: dict[str, Any], system_terms: list[str]) -> str | None:
    url = record.get("url") or ""
    title = record.get("title") or ""
    publisher = record.get("publisher") or ""
    snippet = record.get("summary_or_snippet") or ""
    text = f"{title} {snippet} {publisher} {url}".lower()
    if not url:
        return "missing_url"
    if not title and not publisher:
        return "missing_title_and_publisher"
    for reason, terms in EXCLUDED_CONTEXT_TERMS.items():
        if any(term in text for term in terms):
            return reason
    has_region = bool(matched_region_terms(text))
    domain = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    has_regional_domain = any(domain.endswith(known) for known in KNOWN_REGIONAL_DOMAINS)
    has_system = any(term.lower() in text for term in system_terms)
    if not has_region and not (has_regional_domain and has_system):
        return "no_wa_or_id_connection"
    if not has_system:
        return "no_public_systems_term"
    return None


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def dedupe_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    seen_compound: set[tuple[str, str, str]] = set()
    kept = []
    duplicates = 0
    for record in records:
        url_key = canonicalize_url(record.get("url", ""))
        title_key = normalize_title(record.get("title", ""))
        compound = (title_key, normalize_title(record.get("publisher", "")), str(record.get("published_at") or "")[:10])
        if url_key in seen_urls or title_key in seen_titles or compound in seen_compound:
            duplicates += 1
            continue
        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        seen_compound.add(compound)
        kept.append(record)
    return kept, duplicates


def parse_provider_mode(value: str | None) -> list[str]:
    if not value or value == "all":
        return ["manual", "registry", "gdelt"]
    providers = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = [item for item in providers if item not in {"manual", "registry", "gdelt"}]
    if unknown:
        raise ValueError(f"unsupported historical provider(s): {', '.join(unknown)}")
    ordered = []
    for provider_id in ["manual", "registry", "gdelt"]:
        if provider_id in providers and provider_id not in ordered:
            ordered.append(provider_id)
    return ordered


def source_folder(root: Path, week_start: date, week_end: date) -> Path:
    return root / CASCADE_DATA_ROOT / "sources" / f"{week_start.isoformat()}_{week_end.isoformat()}"


def manual_sources_path(root: Path, week_start: date, week_end: date) -> Path:
    return source_folder(root, week_start, week_end) / MANUAL_SOURCE_FILENAME


def manual_sources_example_path(root: Path, week_start: date, week_end: date) -> Path:
    return source_folder(root, week_start, week_end) / MANUAL_SOURCE_EXAMPLE_FILENAME


def parse_record_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def valid_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def manual_example_records(week_start: date, week_end: date) -> list[dict[str, Any]]:
    return [
        {
            "source_record_id": f"manual-{week_start.isoformat()}-001",
            "title": "Replace with exact source title",
            "url": "https://example.org/source-story",
            "publisher": "Replace with publisher",
            "published_at": f"{week_start.isoformat()}T12:00:00Z",
            "retrieved_at": utc_now(),
            "summary_or_snippet": "",
            "source_type": "manual",
            "provider_id": "manual",
            "region_terms_matched": ["washington"],
            "category_hint": "Infrastructure",
            "state_hint": "WA",
            "reliability_tier": "editorial-record",
            "traceability_note": "Project-local manual source supplement; URL and supplied metadata preserved.",
        }
    ]


def create_manual_source_template(root: Path, week_start: date, week_end: date, force: bool = False) -> dict[str, Any]:
    folder = source_folder(root.resolve(), week_start, week_end)
    manual_path = folder / MANUAL_SOURCE_FILENAME
    example_path = folder / MANUAL_SOURCE_EXAMPLE_FILENAME
    written: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    folder.mkdir(parents=True, exist_ok=True)
    if manual_path.exists() and not force:
        warnings.append(f"manual source file already exists and was not overwritten: {manual_path}")
    else:
        manual_path.write_text("[]\n", encoding="utf-8")
        written.append(str(manual_path))
    if example_path.exists() and not force:
        warnings.append(f"manual source example already exists and was not overwritten: {example_path}")
    else:
        example_path.write_text(json.dumps(manual_example_records(week_start, week_end), indent=2) + "\n", encoding="utf-8")
        written.append(str(example_path))
    return {
        "ok": not errors,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "source_folder": str(folder),
        "manual_sources_path": str(manual_path),
        "manual_sources_example_path": str(example_path),
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }


def validate_manual_sources(root: Path, week_start: date, week_end: date) -> dict[str, Any]:
    path = manual_sources_path(root.resolve(), week_start, week_end)
    warnings: list[str] = []
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    duplicate_urls: list[str] = []
    duplicate_ids: list[str] = []
    if not path.exists():
        errors.append(f"manual source file not found: {path}")
        return {"ok": False, "manual_sources_path": str(path), "manual_sources_exists": False, "source_count": 0, "warnings": warnings, "errors": errors}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON in manual source file {path}: {exc}")
        return {"ok": False, "manual_sources_path": str(path), "manual_sources_exists": True, "source_count": 0, "warnings": warnings, "errors": errors}
    if not isinstance(payload, list):
        errors.append("manual source file must contain a top-level JSON list")
        return {"ok": False, "manual_sources_path": str(path), "manual_sources_exists": True, "source_count": 0, "warnings": warnings, "errors": errors}
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    system_terms = [term for terms in CATEGORY_HINTS.values() for term in terms]
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            errors.append(f"record {index} must be a JSON object")
            continue
        records.append(item)
        url = str(item.get("url") or item.get("source_url") or item.get("canonical_url") or "").strip()
        title = str(item.get("title") or item.get("source_title") or "").strip()
        publisher = str(item.get("publisher") or item.get("source_name") or "").strip()
        source_record_id = str(item.get("source_record_id") or "").strip()
        if not url:
            errors.append(f"record {index} missing required url")
        elif not valid_url(url):
            errors.append(f"record {index} has invalid url: {url}")
        else:
            url_key = canonicalize_url(url)
            if url_key in seen_urls:
                duplicate_urls.append(url)
            seen_urls.add(url_key)
        if not title and not publisher:
            errors.append(f"record {index} must include title or publisher")
        if not source_record_id:
            warnings.append(f"record {index} missing source_record_id; one will be generated during retrieval")
        elif source_record_id in seen_ids:
            duplicate_ids.append(source_record_id)
        seen_ids.add(source_record_id)
        published = parse_record_date(item.get("published_at"))
        if item.get("published_at") and published is None:
            warnings.append(f"record {index} has unparseable published_at: {item.get('published_at')}")
        elif published is not None and not (week_start <= published <= week_end):
            warnings.append(f"record {index} published_at is outside coverage window: {item.get('published_at')}")
        text = f"{title} {publisher} {item.get('summary_or_snippet') or ''} {url}"
        reason = exclusion_reason({"url": url, "title": title, "publisher": publisher, "summary_or_snippet": item.get("summary_or_snippet") or ""}, system_terms)
        if reason in {"no_wa_or_id_connection", "no_public_systems_term"}:
            warnings.append(f"record {index} has weak Cascadia relevance: {reason}")
        elif not matched_region_terms(text):
            warnings.append(f"record {index} has no explicit WA/OR/ID region term")
    if duplicate_urls:
        warnings.append(f"duplicate URLs found: {', '.join(sorted(set(duplicate_urls)))}")
    if duplicate_ids:
        errors.append(f"duplicate source_record_ids found: {', '.join(sorted(set(duplicate_ids)))}")
    return {
        "ok": not errors,
        "manual_sources_path": str(path),
        "manual_sources_exists": True,
        "source_count": len(records),
        "duplicate_urls": sorted(set(duplicate_urls)),
        "duplicate_source_record_ids": sorted(set(duplicate_ids)),
        "warnings": warnings,
        "errors": errors,
    }


def load_manual_sources(root: Path, week_start: date, week_end: date, retrieved_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = manual_sources_path(root, week_start, week_end)
    validation = validate_manual_sources(root, week_start, week_end) if path.exists() else {
        "ok": True,
        "manual_sources_path": str(path),
        "manual_sources_exists": False,
        "source_count": 0,
        "warnings": [],
        "errors": [],
    }
    if not path.exists():
        return [], validation
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        validation["errors"] = list(validation.get("errors", [])) or [f"failed to parse manual historical source file {path}: {exc}"]
        validation["ok"] = False
        return [], validation
    warnings = list(validation.get("warnings", []))
    records = []
    for item in payload if isinstance(payload, list) else []:
        url = item.get("url") or item.get("source_url") or item.get("canonical_url") or ""
        title = item.get("title") or item.get("source_title") or ""
        publisher = item.get("publisher") or item.get("source_name") or ""
        if not url:
            warnings.append(f"manual source skipped without URL: {title or publisher or 'untitled'}")
            continue
        text = f"{title} {item.get('summary_or_snippet', '')} {publisher} {url}"
        record = {
            "source_record_id": item.get("source_record_id") or f"manual-{stable_id(url, title, publisher)}",
            "title": title,
            "url": url,
            "publisher": publisher,
            "published_at": item.get("published_at"),
            "retrieved_at": item.get("retrieved_at") or retrieved_at,
            "summary_or_snippet": item.get("summary_or_snippet") or item.get("text") or "",
            "source_type": item.get("source_type") or "manual",
            "provider_id": item.get("provider_id") or "manual",
            "provider_name": item.get("provider_name") or "Project-local manual historical sources",
            "query_used": item.get("query_used") or MANUAL_SOURCE_FILENAME,
            "search_start_date": week_start.isoformat(),
            "search_end_date": week_end.isoformat(),
            "region_terms_matched": item.get("region_terms_matched") or matched_region_terms(text),
            "category_hint": item.get("category_hint") or infer_category(text),
            "state_hint": item.get("state_hint") or infer_state(text),
            "reliability_tier": item.get("reliability_tier") or "editorial-record",
            "traceability_note": item.get("traceability_note") or "Project-local manual source supplement; URL and supplied metadata preserved.",
        }
        records.append(record)
    validation["warnings"] = warnings
    validation["source_count"] = len(records)
    return records, validation


def retrieve_historical_sources(
    root: Path,
    week_start: date,
    week_end: date,
    edition_date: str | None = None,
    run_date: str | None = None,
    dry_run: bool = False,
    refresh_cache: bool = False,
    historical_provider: str | None = None,
    max_historical_queries: int | None = None,
    historical_delay_seconds: float | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    edition_date = edition_date or week_end.isoformat()
    run_date = run_date or edition_date
    config = load_historical_config(root)
    if max_historical_queries is not None:
        config.setdefault("query_groups", {})["max_queries_per_week"] = max_historical_queries
    provider_mode = historical_provider or "all"
    provider_order = parse_provider_mode(provider_mode)
    retrieved_at = utc_now()
    queries = build_queries(config)
    warnings: list[str] = []
    errors: list[str] = []
    raw_candidates: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    providers_used: list[str] = []
    queries_run: list[dict[str, Any]] = []
    provider_request_diagnostics: list[dict[str, Any]] = []
    query_limit = max(1, int(max_historical_queries or config.get("query_groups", {}).get("max_queries_per_week") or config.get("query_groups", {}).get("max_queries", len(queries) or 1)))
    queries_planned = [{"query_group": query_group_label(query, config), "query": query} for query in queries]
    limited_queries = queries[:query_limit]
    queries_skipped_due_to_limit = max(0, len(queries) - len(limited_queries))
    cache_hits = 0
    cache_misses = 0
    retry_count = 0
    rate_limit_count = 0
    system_terms = list(config.get("query_groups", {}).get("systems_terms", []))
    target_records = int(config.get("query_groups", {}).get("target_records_per_week", 8))
    folder = source_folder(root, week_start, week_end)
    manual_path = manual_sources_path(root, week_start, week_end)
    registry_report: dict[str, Any] = {
        "registry_sources_planned": 0,
        "registry_sources_run": 0,
        "registry_cache_hits": 0,
        "registry_cache_misses": 0,
        "registry_fetch_errors": 0,
        "registry_records_raw": 0,
        "registry_records_saved": 0,
        "registry_records_excluded": 0,
        "registry_exclusion_reasons": {},
        "records_by_source_id": {},
        "records_by_tier": {},
        "records_by_category_hint": {},
        "warnings": [],
        "errors": [],
    }
    registry_report_path: Path | None = None

    manual_records: list[dict[str, Any]] = []
    manual_validation: dict[str, Any] = {
        "ok": True,
        "manual_sources_path": str(manual_path),
        "manual_sources_exists": manual_path.exists(),
        "source_count": 0,
        "warnings": [],
        "errors": [],
    }
    if "manual" in provider_order:
        manual_records, manual_validation = load_manual_sources(root, week_start, week_end, retrieved_at)
        warnings.extend(str(item) for item in manual_validation.get("warnings", []))
        errors.extend(str(item) for item in manual_validation.get("errors", []))
        if manual_records:
            providers_used.append("manual")
        for record in manual_records:
            reason = exclusion_reason(record, system_terms)
            if reason:
                excluded[reason] += 1
                continue
            raw_candidates.append(record)

    if "registry" in provider_order:
        registry_result = collect_registry_sources(root, week_start, week_end, retrieved_at=retrieved_at, refresh_cache=refresh_cache)
        registry_report = dict(registry_result.get("report") or {})
        registry_report_path = write_registry_report(root, week_start, week_end, registry_report, dry_run=dry_run)
        registry_records = [record for record in registry_result.get("records", []) if isinstance(record, dict)]
        warnings.extend(str(item) for item in registry_result.get("warnings", []))
        warnings.extend(f"registry source fetch error: {item}" for item in registry_result.get("errors", []))
        if registry_records:
            providers_used.append("registry")
        for record in registry_records:
            raw_candidates.append(record)

    for provider_config in config.get("providers", []):
        if not provider_config.get("enabled", False):
            continue
        provider_id = str(provider_config.get("provider_id"))
        if provider_id == "manual":
            continue
        if provider_id not in provider_order:
            continue
        if historical_delay_seconds is not None and str(provider_config.get("provider_id")) == "gdelt":
            provider_config = {**provider_config, "delay_seconds": historical_delay_seconds}
        provider = provider_from_config(provider_config, root=root, refresh_cache=refresh_cache)
        if provider is None:
            warnings.append(f"unsupported historical provider: {provider_config.get('provider_id')}")
            continue
        providers_used.append(provider.provider_id)
        max_results = int(provider_config.get("max_results_per_query", 20))
        backoff_seconds = float(provider_config.get("backoff_max_seconds") or provider_config.get("rate_limit_backoff_seconds") or 180)
        backoff_until = PROVIDER_BACKOFF_UNTIL.get(provider.provider_id, 0)
        if time.monotonic() < backoff_until:
            warnings.append(f"historical provider {provider.provider_id} skipped because a recent rate limit is still cooling down")
            continue
        for query in limited_queries:
            try:
                results = provider.search(week_start, week_end, query, max_results)
                diagnostics = getattr(provider, "last_diagnostics", {}) or {}
                provider_request_diagnostics.append(diagnostics)
                cache_hits += int(bool(diagnostics.get("cache_hit")))
                cache_misses += int(bool(diagnostics.get("cache_miss")))
                retry_count += int(diagnostics.get("retry_count") or 0)
                rate_limit_count += int(diagnostics.get("rate_limit_count") or 0)
                warnings.extend(str(item) for item in diagnostics.get("warnings", []))
                errors.extend(str(item) for item in diagnostics.get("errors", []) if "429" not in str(item))
                queries_run.append(
                    {
                        "provider_id": provider.provider_id,
                        "query_group": query_group_label(query, config),
                        "query": query,
                        "result_count": len(results),
                        "request_url": diagnostics.get("request_url"),
                        "status_code": (diagnostics.get("attempts") or [{}])[-1].get("status_code") if diagnostics.get("attempts") else None,
                        "content_type": (diagnostics.get("attempts") or [{}])[-1].get("content_type") if diagnostics.get("attempts") else None,
                        "retry_count": diagnostics.get("retry_count", 0),
                        "cache_hit": bool(diagnostics.get("cache_hit")),
                        "cache_miss": bool(diagnostics.get("cache_miss")),
                        "fetch_backend": diagnostics.get("fetch_backend"),
                        "fallback_used": bool(diagnostics.get("fallback_used")),
                    }
                )
            except HistoricalProviderRateLimited as exc:  # pragma: no cover - network behavior is environment-dependent
                PROVIDER_BACKOFF_UNTIL[provider.provider_id] = time.monotonic() + backoff_seconds
                diagnostics = getattr(provider, "last_diagnostics", {}) or {}
                provider_request_diagnostics.append(diagnostics)
                cache_hits += int(bool(diagnostics.get("cache_hit")))
                cache_misses += int(bool(diagnostics.get("cache_miss")))
                retry_count += int(diagnostics.get("retry_count") or 0)
                rate_limit_count += int(diagnostics.get("rate_limit_count") or 1)
                warnings.append(f"historical provider {provider.provider_id} rate limited for query {query}: {exc}")
                queries_run.append(
                    {
                        "provider_id": provider.provider_id,
                        "query_group": query_group_label(query, config),
                        "query": query,
                        "result_count": 0,
                        "error": str(exc),
                        "rate_limited": True,
                        "request_url": diagnostics.get("request_url"),
                        "retry_count": diagnostics.get("retry_count", 0),
                        "cache_hit": bool(diagnostics.get("cache_hit")),
                        "cache_miss": bool(diagnostics.get("cache_miss")),
                        "fetch_backend": diagnostics.get("fetch_backend"),
                        "fallback_used": bool(diagnostics.get("fallback_used")),
                    }
                )
                break
            except Exception as exc:  # pragma: no cover - network behavior is environment-dependent
                diagnostics = getattr(provider, "last_diagnostics", {}) or {}
                if diagnostics:
                    provider_request_diagnostics.append(diagnostics)
                    cache_hits += int(bool(diagnostics.get("cache_hit")))
                    cache_misses += int(bool(diagnostics.get("cache_miss")))
                    retry_count += int(diagnostics.get("retry_count") or 0)
                    rate_limit_count += int(diagnostics.get("rate_limit_count") or 0)
                warnings.append(f"historical provider {provider.provider_id} failed for query {query}: {exc}")
                queries_run.append(
                    {
                        "provider_id": provider.provider_id,
                        "query_group": query_group_label(query, config),
                        "query": query,
                        "result_count": 0,
                        "error": str(exc),
                        "request_url": diagnostics.get("request_url"),
                        "retry_count": diagnostics.get("retry_count", 0),
                        "cache_hit": bool(diagnostics.get("cache_hit")),
                        "cache_miss": bool(diagnostics.get("cache_miss")),
                        "fetch_backend": diagnostics.get("fetch_backend"),
                        "fallback_used": bool(diagnostics.get("fallback_used")),
                    }
                )
                continue
            for item in results:
                text = f"{item.get('title', '')} {item.get('summary_or_snippet', '')} {item.get('publisher', '')} {item.get('url', '')}"
                record = {
                    "source_record_id": f"hist-{stable_id(provider.provider_id, item.get('url', ''), item.get('title', ''))}",
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "publisher": item.get("publisher") or "",
                    "published_at": item.get("published_at"),
                    "retrieved_at": retrieved_at,
                    "summary_or_snippet": item.get("summary_or_snippet") or "",
                    "source_type": "historical_search",
                    "provider_id": provider.provider_id,
                    "provider_name": provider.provider_name,
                    "query_used": query,
                    "search_start_date": week_start.isoformat(),
                    "search_end_date": week_end.isoformat(),
                    "region_terms_matched": matched_region_terms(text),
                    "category_hint": infer_category(text),
                    "state_hint": infer_state(text),
                    "reliability_tier": item.get("reliability_tier") or provider_config.get("reliability_tier") or "unknown",
                    "traceability_note": "Retrieved from public historical search provider; title, URL, publisher, date, snippet, and query metadata preserved when supplied.",
                    "raw_payload": item.get("raw_payload"),
                }
                reason = exclusion_reason(record, system_terms)
                if reason:
                    excluded[reason] += 1
                    continue
                raw_candidates.append(record)
            provider_candidate_count = sum(1 for record in raw_candidates if record.get("provider_id") == provider.provider_id)
            if provider_candidate_count >= target_records:
                break

    saved_records, duplicates_removed = dedupe_records(raw_candidates)
    source_count_by_provider = Counter(record.get("provider_id") or "unknown" for record in saved_records)
    records_by_state = Counter(record.get("state_hint") or "unknown" for record in saved_records)
    records_by_category = Counter(record.get("category_hint") or "unknown" for record in saved_records)
    records_by_source_type = Counter(record.get("source_type") or "unknown" for record in saved_records)
    providers_used = sorted(set(providers_used), key=providers_used.index)
    if not saved_records:
        if rate_limit_count:
            warnings.append("sparse week: GDELT rate limits prevented usable provider results")
        elif any("invalid JSON" in warning or "empty response" in warning for warning in warnings):
            warnings.append("sparse week: provider returned empty or non-JSON responses")
        elif sum(item.get("result_count", 0) for item in queries_run) == 0:
            warnings.append("sparse week: no provider results")
        elif sum(excluded.values()) > 0:
            warnings.append("sparse week: provider results were excluded by source standards")
    recommendation = "enough source records available"
    if not saved_records:
        recommendation = "add manual_sources.json" if not manual_path.exists() else "provider sparse; manual supplement recommended"
    elif len(saved_records) < max(3, min(target_records, 4)):
        recommendation = "provider sparse; manual supplement recommended"
    if provider_mode == "gdelt" and not saved_records:
        recommendation = "rerun historical search with refresh-cache"
    if "registry" in provider_order and not saved_records and registry_report.get("registry_records_raw") and registry_report.get("registry_records_excluded"):
        recommendation = "registry found records but curation excluded them; inspect diagnostics"
    fetch_diagnostics = provider_request_diagnostics + list(registry_report.get("diagnostics", []))
    fetch_recommendations = [str(item.get("recommendation")) for item in fetch_diagnostics if item.get("recommendation")]
    curl_success_recommendation = next((item for item in fetch_recommendations if "curl fallback succeeded" in item), None)
    if curl_success_recommendation:
        recommendation = curl_success_recommendation
    elif fetch_recommendations:
        recommendation = fetch_recommendations[0]
    report = {
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "historical_provider_mode": provider_mode,
        "providers_used": providers_used,
        "queries_planned": queries_planned,
        "queries_run": queries_run,
        "queries_skipped_due_to_limit": queries_skipped_due_to_limit,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "gdelt_queries_run": sum(1 for item in queries_run if item.get("provider_id") == "gdelt"),
        "gdelt_cache_hits": sum(1 for item in queries_run if item.get("provider_id") == "gdelt" and item.get("cache_hit")),
        "gdelt_rate_limit_count": rate_limit_count,
        "retry_count": retry_count,
        "rate_limit_count": rate_limit_count,
        "fetch_backend": next((item.get("fetch_backend") for item in fetch_diagnostics if item.get("fetch_backend") not in {None, "cache"}), None) or ("cache" if any(item.get("fetch_backend") == "cache" for item in fetch_diagnostics) else None),
        "fallback_used": any(bool(item.get("fallback_used")) for item in fetch_diagnostics),
        "python_fetch_error": next((item.get("python_fetch_error") for item in fetch_diagnostics if item.get("python_fetch_error")), None),
        "curl_exit_code": next((item.get("curl_exit_code") for item in fetch_diagnostics if item.get("curl_exit_code") is not None), None),
        "curl_stderr_tail": next((item.get("curl_stderr_tail") for item in fetch_diagnostics if item.get("curl_stderr_tail")), None),
        "tls_or_revocation_hint": next((item.get("tls_or_revocation_hint") for item in fetch_diagnostics if item.get("tls_or_revocation_hint")), None),
        "raw_results_count": sum(item.get("result_count", 0) for item in queries_run),
        "records_saved": len(saved_records),
        "merged_source_count": len(raw_candidates),
        "final_saved_source_count": len(saved_records),
        "records_excluded": sum(excluded.values()),
        "exclusion_reasons": dict(sorted(excluded.items())),
        "duplicates_removed": duplicates_removed,
        "source_count_by_provider": dict(sorted(source_count_by_provider.items())),
        "source_count_by_type": dict(sorted(records_by_source_type.items())),
        "source_count_by_provider_planned": {provider_id: provider_order.count(provider_id) for provider_id in provider_order},
        "registry_sources_planned": registry_report.get("registry_sources_planned", 0),
        "registry_sources_run": registry_report.get("registry_sources_run", 0),
        "registry_cache_hits": registry_report.get("registry_cache_hits", 0),
        "registry_cache_misses": registry_report.get("registry_cache_misses", 0),
        "registry_fetch_errors": registry_report.get("registry_fetch_errors", 0),
        "registry_records_raw": registry_report.get("registry_records_raw", 0),
        "registry_records_saved": registry_report.get("registry_records_saved", 0),
        "registry_records_excluded": registry_report.get("registry_records_excluded", 0),
        "registry_exclusion_reasons": registry_report.get("registry_exclusion_reasons", {}),
        "official_pages_planned": registry_report.get("official_pages_planned", 0),
        "official_pages_run": registry_report.get("official_pages_run", 0),
        "official_links_found": registry_report.get("official_links_found", 0),
        "official_links_saved": registry_report.get("official_links_saved", 0),
        "official_links_excluded": registry_report.get("official_links_excluded", 0),
        "official_exclusion_reasons": registry_report.get("official_exclusion_reasons", {}),
        "weak_date_count": registry_report.get("weak_date_count", 0),
        "same_domain_links_only": registry_report.get("same_domain_links_only", True),
        "unsupported_source_type_count": registry_report.get("unsupported_source_type_count", 0),
        "records_by_source_id": registry_report.get("records_by_source_id", {}),
        "records_by_tier": registry_report.get("records_by_tier", {}),
        "records_by_category_hint": dict(sorted(records_by_category.items())),
        "registry_source_report_path": str(registry_report_path) if registry_report_path else None,
        "manual_sources_path": str(manual_path),
        "manual_sources_loaded": len(manual_records),
        "manual_sources_valid": bool(manual_validation.get("ok")),
        "manual_sources_errors": list(manual_validation.get("errors", [])),
        "manual_sources_warnings": list(manual_validation.get("warnings", [])),
        "records_by_source_type": dict(sorted(records_by_source_type.items())),
        "records_by_state_hint": dict(sorted(records_by_state.items())),
        "provider_request_diagnostics": provider_request_diagnostics,
        "registry_source_diagnostics": registry_report.get("diagnostics", []),
        "recommendation": recommendation,
        "warnings": warnings,
        "errors": errors,
    }
    historical_path = folder / "historical_sources.json"
    report_path = folder / "historical_search_report.json"
    raw_path = root / CASCADE_DATA_ROOT / "raw" / edition_date / "raw_sources.json"
    if not dry_run:
        folder.mkdir(parents=True, exist_ok=True)
        historical_path.write_text(json.dumps(saved_records, indent=2), encoding="utf-8")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_records = []
        for record in saved_records:
            raw_records.append(
                {
                    **record,
                    "source_id": record.get("source_id") or record.get("provider_id"),
                    "source_name": record.get("source_name") or record.get("publisher") or record.get("provider_name"),
                    "region_scope": record.get("state_hint") or ("regional" if record.get("region_terms_matched") else None),
                }
            )
        raw_path.write_text(json.dumps(raw_records, indent=2), encoding="utf-8")
        normalize_sources(root, edition_date, dry_run=False)
        curate_sources(root, edition_date, dry_run=False)
    return {
        "ok": not errors,
        "edition_date": edition_date,
        "run_date": run_date,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "historical_search": True,
        "providers_used": providers_used,
        "query_count": len(queries_run),
        "source_count": len(saved_records),
        "included_source_count": len(saved_records),
        "excluded_source_count": sum(excluded.values()),
        "source_record_ids": [record["source_record_id"] for record in saved_records],
        "source_urls": [record["url"] for record in saved_records if record.get("url")],
        "historical_sources_path": str(historical_path),
        "historical_search_report_path": str(report_path),
        "raw_path": str(raw_path),
        "report": report,
        "warnings": warnings,
        "errors": errors,
    }
