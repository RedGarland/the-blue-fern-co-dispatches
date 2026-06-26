from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote_plus, unquote, urlsplit

from bluefern_dispatches import gaza_sources


ROOT = Path(__file__).resolve().parents[2]
DISPATCH_SLUG = "gaza"
REPORT_PREFIX = "gaza_wide_discovery"
DEFAULT_OUTPUT_DIR = Path("output") / "review"
GOOGLE_NEWS_BASE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
STALE_AFTER_DAYS = 3
AGGREGATOR_DOMAINS = {"news.google.com"}
DEFAULT_FETCH_TIMEOUT = 20

OFFICIAL_HUMANITARIAN_PUBLISHERS = {
    "ocha",
    "ocha opt",
    "ocha o pt",
    "unrwa",
    "who",
    "world health organization",
    "unicef",
    "wfp",
    "world food programme",
    "prcs",
    "palestinian red crescent",
    "palestine red crescent society",
    "gaza health ministry",
    "ministry of health in gaza",
    "palestinian ministry of health",
    "wafa",
}
KNOWN_REGIONAL_PUBLISHERS = {
    "trt world",
    "anadolu",
    "jerusalem post",
    "imemc",
    "times of israel",
    "haaretz",
    "middle east eye",
    "the new arab",
    "+972 magazine",
    "local call",
}
WIRE_PUBLISHERS = {
    "reuters",
    "associated press",
    "ap",
    "afp",
    "bbc",
    "bbc news",
    "guardian",
    "the guardian",
    "al jazeera",
    "al jazeera english",
}
REPUBLICATION_HINTS = (
    "syndicated",
    "syndication",
    "republished",
    "republish",
    "republication",
    "reprint",
    "mirror",
    "reposted",
    "via ",
)

GAZA_SIGNAL_TERMS: dict[str, tuple[str, ...]] = {
    "casualty_strike_signal": ("strike", "airstrike", "air strike", "bombard", "shell", "killed", "dead", "casualty", "casualties", "injured"),
    "humanitarian_care_signal": ("humanitarian", "care", "relief", "aid", "support", "medical assistance"),
    "aid_food_water_medical_access_signal": ("aid", "food", "water", "medicine", "medic", "medical access", "hospital access", "crossing", "convoy"),
    "displacement_shelter_signal": ("displac", "shelter", "evacu", "tent", "camp", "homeless", "refugee"),
    "health_system_hospital_signal": ("hospital", "clinic", "health system", "health-system", "ambulance", "paramedic", "doctor"),
    "education_children_signal": ("child", "children", "school", "education", "student", "students"),
    "official_un_ngo_signal": ("who", "unrwa", "ocha", "unicef", "wfp", "prcs", "ohchr", "human rights watch", "amnesty"),
    "diplomacy_ceasefire_context": ("ceasefire", "truce", "talks", "negotiat", "diplom", "mediator"),
    "accountability_legal_context": ("icj", "icc", "accountability", "war crime", "investigation", "legal", "human rights"),
}

KNOWN_PROVIDER_DOMAINS = {
    "trtworld.com": "TRT World",
    "aa.com.tr": "Anadolu",
    "jpost.com": "Jerusalem Post",
    "imemc.org": "IMEMC",
    "wafa.ps": "WAFA",
    "who.int": "WHO",
    "unrwa.org": "UNRWA",
    "unicef.org": "UNICEF",
    "wfp.org": "WFP",
    "ochaopt.org": "OCHA",
    "prcs.ps": "Palestinian Red Crescent",
    "reuters.com": "Reuters",
    "apnews.com": "Associated Press",
    "afp.com": "AFP",
    "bbc.com": "BBC News",
    "theguardian.com": "The Guardian",
    "aljazeera.com": "Al Jazeera",
    "news.un.org": "UN News",
    "reliefweb.int": "ReliefWeb",
    "timesofisrael.com": "Times of Israel",
    "haaretz.com": "Haaretz",
}

VISIBLE_PUBLISHER_SUFFIX_DOMAINS = {
    "trt world": "trtworld.com",
    "anadolu": "aa.com.tr",
    "jerusalem post": "jpost.com",
    "times of israel": "timesofisrael.com",
    "haaretz": "haaretz.com",
    "middle east eye": "middleeasteye.net",
    "reuters": "reuters.com",
    "ap news": "apnews.com",
    "associated press": "apnews.com",
    "ap": "apnews.com",
    "afp": "afp.com",
    "bbc": "bbc.com",
    "bbc news": "bbc.com",
    "guardian": "theguardian.com",
    "the guardian": "theguardian.com",
    "al jazeera": "aljazeera.com",
    "al jazeera english": "aljazeera.com",
    "un news": "news.un.org",
    "reliefweb": "reliefweb.int",
    "imemc": "imemc.org",
    "imemc.org": "imemc.org",
}

WEAK_CONTEXT_HINTS = (
    "live blog",
    "sports",
    "election",
    "weather",
    "score",
    "match",
    "entertainment",
    "music",
    "football",
    "soccer",
    "domestic politics",
)

DEFAULT_QUERY_SPECS = (
    ("gaza health/care", '"Gaza" health care hospital medic'),
    ("gaza strike/casualty", '"Gaza" strike casualty'),
    ("gaza humanitarian aid", '"Gaza" humanitarian aid food water'),
    ("gaza displacement/shelter", '"Gaza" displacement shelter camp'),
    ("gaza children/education", '"Gaza" children school education'),
    ("gaza ceasefire/diplomacy", '"Gaza" ceasefire talks'),
    ("gaza accountability/legal", '"Gaza" war crimes accountability'),
    ("TRT World Gaza care", 'site:trtworld.com Gaza health care hospital'),
    ("Anadolu Gaza casualty", 'site:aa.com.tr Gaza strike casualty'),
    ("Jerusalem Post Gaza shelter", 'site:jpost.com Gaza displacement shelter humanitarian'),
    ("IMEMC Gaza medic", 'site:imemc.org Gaza casualty medic'),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditFetchOptions:
    timeout: int = DEFAULT_FETCH_TIMEOUT
    allow_curl_no_revoke: bool = False


class AuditFeedFetchError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = payload


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    text = _nonempty(value).lower().replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_publisher(value: Any) -> str:
    text = _normalize_text(value)
    return re.sub(r"\b(news|media|wire|agency|press|the)\b", " ", text).strip()


def _normalize_url(url: str) -> str:
    raw = _nonempty(url)
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    query = parts.query
    return f"{scheme}://{netloc}{path}{('?' + query) if query else ''}"


def _domain_from_url(url: str) -> str:
    try:
        domain = urlsplit(_normalize_url(url)).netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except ValueError:
        return ""


def _looks_like_domain(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", _normalize_text(value)))


def _publisher_from_domain(domain: str) -> str:
    normalized = _normalize_text(domain)
    return KNOWN_PROVIDER_DOMAINS.get(normalized, domain)


def _infer_visible_publisher_domain(*values: Any) -> str:
    patterns = (
        re.compile(r"(?:^|\s)[-–—|:]\s*([a-z0-9.-]+\.[a-z]{2,})(?:\s*)$", re.I),
        re.compile(r"\b(?:via|source)\s+([a-z0-9.-]+\.[a-z]{2,})\b", re.I),
    )
    for value in values:
        text = _nonempty(value)
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return _normalize_text(match.group(1))
        suffix = re.split(r"\s[-|:]\s", text)[-1]
        normalized_suffix = _normalize_publisher(suffix)
        if normalized_suffix in VISIBLE_PUBLISHER_SUFFIX_DOMAINS:
            return VISIBLE_PUBLISHER_SUFFIX_DOMAINS[normalized_suffix]
    return ""


def _extract_embedded_http_url(text: str) -> str:
    match = re.search(r"https?://[^\s\x00<>\"']+", text)
    if not match:
        return ""
    candidate = gaza_sources.canonicalize_url(match.group(0))
    return "" if _is_google_news_url(candidate) else candidate


def _resolve_google_news_canonical_url(url: str) -> tuple[str, str]:
    raw = _nonempty(url)
    if not _is_google_news_url(raw):
        candidate = gaza_sources.canonicalize_url(raw)
        return (candidate, "direct_url") if candidate else ("", "missing_url")

    canonical_url, reason = gaza_sources.extract_canonical_from_google_wrapper(raw)
    if canonical_url and not _is_google_news_url(canonical_url):
        return canonical_url, reason

    try:
        parts = urlsplit(raw)
    except ValueError:
        return "", "invalid_wrapper_url"

    query_candidates = [unquote(str(value or "")).strip() for _key, value in parse_qsl(parts.query, keep_blank_values=False)]
    for candidate_text in query_candidates:
        candidate = _extract_embedded_http_url(candidate_text)
        if candidate:
            return candidate, "resolved_from_query_payload"

    segments = [segment for segment in parts.path.split("/") if segment]
    article_token = segments[-1] if segments else ""
    token_candidates = [unquote(article_token)]
    if article_token:
        padded = article_token + ("=" * ((4 - len(article_token) % 4) % 4))
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            decoded = ""
        if decoded:
            token_candidates.append(decoded)
    for candidate_text in token_candidates:
        candidate = _extract_embedded_http_url(candidate_text)
        if candidate:
            return candidate, "resolved_from_wrapper_payload"
    return "", "wrapper_without_extractable_canonical"


def _has_source_of_record_url(candidate: dict[str, Any]) -> bool:
    for key in ("canonical_url", "url"):
        value = _nonempty(candidate.get(key))
        if value and not _is_google_news_url(value):
            return True
    return False


def _date_prefix(value: Any) -> str:
    text = _nonempty(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _certifi_cafile() -> str:
    try:
        import certifi  # type: ignore
    except ImportError:
        return ""
    try:
        return str(certifi.where() or "")
    except Exception:  # noqa: BLE001
        return ""


def _build_verified_ssl_context(*, cafile: str | None = None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=cafile or None)


def _open_url_with_context(request: urllib.request.Request, *, timeout: int, context: ssl.SSLContext):
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _run_curl_fetch(url: str, *, timeout: int, allow_no_revoke: bool) -> tuple[bytes, str]:
    curl_cmd = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_cmd:
        raise RuntimeError("curl_not_available")
    cmd = [curl_cmd, "--silent", "--show-error", "--location", "--max-time", str(timeout), "--fail", url]
    if allow_no_revoke:
        cmd.insert(1, "--ssl-no-revoke")
    proc = subprocess.run(cmd, capture_output=True, text=False, check=False)
    if proc.returncode != 0:
        stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl_failed(rc={proc.returncode}): {stderr_text}")
    return proc.stdout or b"", "curl_no_revoke" if allow_no_revoke else "curl"


def _is_tls_error(exc_text: str) -> bool:
    lowered = _normalize_text(exc_text)
    return (
        gaza_sources._is_tls_error(exc_text)
        or "certificate verify failed" in lowered
        or "cert verification failed" in lowered
    )


def _attempt_verified_fetch(url: str, *, timeout: int, context_label: str, cafile: str | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "BlueFernDispatches/1.0"})
    try:
        with _open_url_with_context(request, timeout=timeout, context=_build_verified_ssl_context(cafile=cafile)) as response:
            return {
                "ok": True,
                "url": url,
                "status_code": int(getattr(response, "status", 200) or 200),
                "failure_reason": None,
                "exception_type": None,
                "tls_error": False,
                "backend_used": context_label,
                "content_type": str(response.headers.get("Content-Type") or ""),
                "content_encoding": str(response.headers.get("Content-Encoding") or ""),
                "content_bytes": response.read(),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "status_code": int(exc.code),
            "failure_reason": f"HTTPError: {exc}",
            "exception_type": type(exc).__name__,
            "tls_error": False,
            "backend_used": context_label,
            "content_bytes": None,
        }
    except Exception as exc:  # noqa: BLE001
        tls_error = _is_tls_error(str(exc))
        return {
            "ok": False,
            "url": url,
            "status_code": None,
            "failure_reason": gaza_sources.TLS_FAILURE_REASON if tls_error else f"{type(exc).__name__}: {exc}",
            "exception_type": type(exc).__name__,
            "tls_error": tls_error,
            "backend_used": context_label,
            "content_bytes": None,
        }


def _format_fetch_attempts(attempts: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for attempt in attempts:
        backend = _nonempty(attempt.get("backend_used")) or "unknown"
        reason = _nonempty(attempt.get("failure_reason")) or "ok"
        parts.append(f"{backend}:{reason}")
    return "; ".join(parts)


def _audit_fetch_feed_payload(url: str, *, options: AuditFetchOptions) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    system_result = _attempt_verified_fetch(url, timeout=options.timeout, context_label="python_system")
    attempts.append(system_result)
    if system_result.get("ok"):
        system_result["attempts"] = attempts
        return system_result

    certifi_path = _certifi_cafile()
    if system_result.get("tls_error") and certifi_path:
        certifi_result = _attempt_verified_fetch(
            url,
            timeout=options.timeout,
            context_label="python_certifi",
            cafile=certifi_path,
        )
        attempts.append(certifi_result)
        if certifi_result.get("ok"):
            certifi_result["attempts"] = attempts
            return certifi_result

    if options.allow_curl_no_revoke and any(bool(attempt.get("tls_error")) for attempt in attempts):
        try:
            body, backend = _run_curl_fetch(url, timeout=options.timeout, allow_no_revoke=True)
            result = {
                "ok": True,
                "url": url,
                "status_code": 200,
                "failure_reason": None,
                "exception_type": None,
                "tls_error": False,
                "backend_used": backend,
                "content_type": "",
                "content_encoding": "",
                "content_bytes": body,
                "attempts": attempts,
            }
            return result
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                {
                    "ok": False,
                    "url": url,
                    "status_code": None,
                    "failure_reason": str(exc),
                    "exception_type": type(exc).__name__,
                    "tls_error": _is_tls_error(str(exc)),
                    "backend_used": "curl_no_revoke",
                    "content_bytes": None,
                }
            )

    failure = dict(attempts[-1] if attempts else {})
    failure["attempts"] = attempts
    return failure


def _default_audit_fetch_rss_items(options: AuditFetchOptions) -> Callable[[str], list[dict[str, str]]]:
    def _fetch(url: str) -> list[dict[str, str]]:
        payload = _audit_fetch_feed_payload(url, options=options)
        if not payload.get("ok"):
            attempts = payload.get("attempts") or []
            raise AuditFeedFetchError(
                f"{payload.get('failure_reason') or 'feed_fetch_failed'}; attempts={_format_fetch_attempts(attempts)}",
                payload,
            )
        return gaza_sources.parse_rss_items(
            payload.get("content_bytes") or b"",
            content_type=str(payload.get("content_type") or ""),
            content_encoding=str(payload.get("content_encoding") or ""),
        )

    return _fetch


def _parse_dt(value: Any) -> datetime | None:
    text = _nonempty(value)
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            import email.utils

            parsed = email.utils.parsedate_to_datetime(text)
        except Exception:  # noqa: BLE001
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fingerprint_title(title: Any) -> str:
    text = _normalize_text(title)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fingerprint_publisher_title(publisher: Any, title: Any) -> str:
    pub = _fingerprint_title(publisher)
    tit = _fingerprint_title(title)
    return f"{pub}|{tit}" if pub and tit else tit or pub


def _fingerprint_cluster(candidate: dict[str, Any]) -> str:
    published = _date_prefix(candidate.get("published_at") or "")
    title_fp = _fingerprint_title(candidate.get("title") or "")
    publisher_fp = _fingerprint_title(candidate.get("publisher") or "")
    if published and title_fp:
        return f"{published}|{title_fp}"
    if publisher_fp and title_fp:
        return f"{publisher_fp}|{title_fp}"
    canonical_url = _normalize_url(candidate.get("canonical_url") or candidate.get("url") or "")
    if canonical_url:
        return canonical_url
    normalized_url = _normalize_url(candidate.get("normalized_url") or candidate.get("url") or "")
    if normalized_url:
        return normalized_url
    return title_fp or publisher_fp or _normalize_url(candidate.get("url") or "")


def _text_blob(candidate: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _nonempty(candidate.get("title")),
            _nonempty(candidate.get("summary_or_snippet")),
            _nonempty(candidate.get("publisher")),
            _nonempty(candidate.get("url")),
        )
        if part
    )


def _is_google_news_url(url: str) -> bool:
    text = _normalize_url(url).lower()
    return "news.google.com" in text


def _looks_syndicated(candidate: dict[str, Any]) -> bool:
    text = _normalize_text(candidate.get("title"))
    if any(hint in text for hint in REPUBLICATION_HINTS):
        return True
    publisher = _normalize_text(candidate.get("publisher"))
    if "syndicat" in publisher or "republish" in publisher or "reprint" in publisher:
        return True
    url = _normalize_url(candidate.get("url") or "")
    return any(hint in url for hint in ("/amp/", "/tag/", "syndicated", "republish", "reprint", "mirror"))


def _source_tier_for_candidate(publisher: Any, url: Any) -> str:
    publisher_text = _normalize_publisher(publisher)
    domain = _domain_from_url(_nonempty(url))
    if publisher_text in OFFICIAL_HUMANITARIAN_PUBLISHERS or domain in {"who.int", "unrwa.org", "unicef.org", "wfp.org", "ochaopt.org", "prcs.ps", "wafa.ps", "news.un.org", "reliefweb.int"}:
        return "official_humanitarian"
    if publisher_text in WIRE_PUBLISHERS or domain in {"reuters.com", "apnews.com", "afp.com", "bbc.com", "theguardian.com", "aljazeera.com"}:
        return "wire_and_major_international"
    if publisher_text in KNOWN_REGIONAL_PUBLISHERS or domain in KNOWN_PROVIDER_DOMAINS:
        return "region_specialist"
    if _looks_syndicated({"publisher": publisher, "url": url}):
        return "republication"
    if _is_google_news_url(_nonempty(url)):
        return "unknown_or_uncategorized"
    return "unknown_or_uncategorized"


def _source_registry_status(surface: dict[str, Any]) -> str:
    status = _normalize_text(surface.get("source_registry_status"))
    if status:
        return status
    if surface.get("surface_type") == "google_news_rss":
        return "aggregator_discovery_surface"
    if surface.get("surface_type") == "manual_seed":
        return "manual_seed"
    return "registered_provider" if surface.get("registry_source_id") else "known_provider"


def _manifest_path(root: Path, edition_date: str, manifest_path: Path | None) -> Path:
    if manifest_path is not None:
        return manifest_path
    for candidate in (
        root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "sources_manifest.json",
        root / "output" / "site" / "gaza" / "editions" / edition_date / "sources_manifest.json",
        root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date / "sources_manifest.json",
    ):
        if candidate.exists():
            return candidate
    return root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "sources_manifest.json"


def _load_json_payload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sources", "queries", "items", "records", "seeds"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _load_seed_file(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    if path.suffix.lower() in {".json", ".jsonl"} or stripped.startswith(("{", "[")):
        payload = json.loads(raw)
        rows = _records_from_payload(payload)
        if rows:
            return rows
        if isinstance(payload, list):
            return [{"url": _nonempty(item)} for item in payload if _nonempty(item)]
        if isinstance(payload, dict) and _nonempty(payload.get("url")):
            return [payload]
        return []
    rows = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rows.append({"url": text})
    return rows


def _load_query_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _records_from_payload(payload)
    if rows:
        return rows
    if isinstance(payload, list):
        return [{"query": _nonempty(item)} for item in payload if _nonempty(item)]
    if isinstance(payload, dict) and _nonempty(payload.get("query")):
        return [payload]
    return []


def _query_url(query: str) -> str:
    return GOOGLE_NEWS_BASE.format(query=quote_plus(query))


def _default_query_surfaces(root: Path, edition_date: str) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    try:
        config = gaza_sources.load_sources_config(root / "data" / "dispatches" / "gaza" / "sources.yml")
    except Exception:  # noqa: BLE001
        config = []

    for source in config:
        if source.type != "rss":
            continue
        surfaces.append(
            {
                "surface_type": "known_publisher_rss",
                "discovery_source": "known_publisher_rss",
                "discovery_query": source.url,
                "query_url": source.url,
                "publisher": source.publisher or source.name,
                "source_registry_status": source.source_state,
                "source_tier": source.source_tier or _source_tier_for_candidate(source.publisher or source.name, source.url),
                "registry_source_id": source.source_id,
            }
        )

    for label, query in DEFAULT_QUERY_SPECS:
        surfaces.append(
            {
                "surface_type": "google_news_rss",
                "discovery_source": "google_news_rss",
                "discovery_query": query,
                "query_url": _query_url(query),
                "publisher": "",
                "source_registry_status": "aggregator_discovery_surface",
                "source_tier": "unknown_or_uncategorized",
                "surface_label": label,
            }
        )
    return surfaces


def _normalize_surface_row(row: dict[str, Any], default_surface_type: str) -> dict[str, Any]:
    normalized = dict(row)
    normalized["surface_type"] = _nonempty(normalized.get("surface_type") or normalized.get("discovery_source") or default_surface_type)
    normalized["discovery_source"] = _nonempty(normalized.get("discovery_source") or normalized["surface_type"] or default_surface_type)
    discovery_query = _nonempty(normalized.get("discovery_query") or normalized.get("query") or normalized.get("query_url") or normalized.get("url") or "")
    normalized["discovery_query"] = discovery_query
    query_url = _nonempty(normalized.get("query_url") or normalized.get("url") or "")
    if not query_url and discovery_query and not _normalize_url(discovery_query).startswith("http"):
        query_url = _query_url(discovery_query)
    normalized["query_url"] = query_url
    normalized["publisher"] = _nonempty(normalized.get("publisher") or "")
    normalized["source_registry_status"] = _source_registry_status(normalized)
    normalized["source_tier"] = _nonempty(normalized.get("source_tier") or _source_tier_for_candidate(normalized.get("publisher"), normalized.get("query_url") or normalized.get("url") or "")) or "unknown_or_uncategorized"
    normalized["registry_source_id"] = _nonempty(normalized.get("registry_source_id") or normalized.get("source_id") or "")
    return normalized


def _discover_from_feed_surface(surface: dict[str, Any], *, fetch_rss_items_fn: Callable[[str], list[dict[str, str]]]) -> tuple[list[dict[str, Any]], list[str]]:
    query_url = _nonempty(surface.get("query_url"))
    if not query_url:
        return [], ["missing discovery query URL"]
    try:
        items = fetch_rss_items_fn(query_url)
    except AuditFeedFetchError as exc:
        payload = exc.payload or {}
        attempts = payload.get("attempts") or []
        context = (
            f"feed_fetch_failed source={_nonempty(surface.get('discovery_source') or surface.get('surface_type'))} "
            f"query={_nonempty(surface.get('discovery_query') or query_url)} "
            f"url={query_url} reason={_nonempty(payload.get('failure_reason') or str(exc))}"
        )
        if attempts:
            context += f" attempts={_format_fetch_attempts(attempts)}"
        return [], [context]
    except Exception as exc:  # noqa: BLE001
        context = (
            f"feed_fetch_failed source={_nonempty(surface.get('discovery_source') or surface.get('surface_type'))} "
            f"query={_nonempty(surface.get('discovery_query') or query_url)} "
            f"url={query_url} reason={type(exc).__name__}: {exc}"
        )
        return [], [context]
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = gaza_sources.clean_feed_text(item.get("title", ""))
        item_url = _nonempty(item.get("url"))
        published_at = _nonempty(item.get("published_at"))
        summary = gaza_sources.clean_feed_text(item.get("summary_or_snippet", ""))
        discovered_url = item_url
        aggregator_url = ""
        canonical_url = ""
        if _is_google_news_url(item_url):
            aggregator_url = item_url
            canonical_url, _reason = _resolve_google_news_canonical_url(item_url)
            discovered_url = canonical_url or ""
        else:
            canonical_url = gaza_sources.canonicalize_url(item_url)
            discovered_url = canonical_url or item_url
        inferred_domain = _infer_visible_publisher_domain(title, summary)
        publisher = _nonempty(item.get("publisher") or surface.get("publisher") or "")
        if not publisher and inferred_domain:
            publisher = _publisher_from_domain(inferred_domain)
        source_of_record_url = discovered_url if discovered_url and not _is_google_news_url(discovered_url) else ""
        rows.append(
            {
                "url": source_of_record_url,
                "canonical_url": canonical_url if canonical_url and not _is_google_news_url(canonical_url) else "",
                "publisher": publisher,
                "title": title,
                "published_at": published_at,
                "retrieved_at": _utc_now(),
                "summary_or_snippet": summary,
                "discovery_source": surface["discovery_source"],
                "discovery_query": surface["discovery_query"],
                "google_news_url": aggregator_url,
                "aggregator_url": aggregator_url,
                "source_registry_status": surface["source_registry_status"],
                "source_tier": surface["source_tier"],
                "surface_type": surface["surface_type"],
                "registry_source_id": surface["registry_source_id"],
                "normalized_url": gaza_sources.canonicalize_url(source_of_record_url),
                "original_url": item_url,
                "inferred_publisher_domain": inferred_domain,
            }
        )
    return rows, warnings


def _seed_to_candidate(seed: dict[str, Any], *, default_surface_type: str, retrieved_at: str) -> dict[str, Any]:
    row = dict(seed)
    url = _nonempty(row.get("url"))
    aggregator_url = _nonempty(row.get("google_news_url") or row.get("aggregator_url"))
    canonical_url = _nonempty(row.get("canonical_url"))
    if _is_google_news_url(aggregator_url or url):
        aggregator_url = aggregator_url or url
        if not canonical_url:
            canonical_url, _reason = _resolve_google_news_canonical_url(aggregator_url)
        if _is_google_news_url(url):
            url = ""
    if canonical_url and _is_google_news_url(canonical_url):
        canonical_url = ""
    if not canonical_url and url and not _is_google_news_url(url):
        canonical_url = gaza_sources.canonicalize_url(url)
    if canonical_url and not url:
        url = canonical_url
    source_of_record_url = url if url and not _is_google_news_url(url) else canonical_url
    normalized_url = gaza_sources.canonicalize_url(source_of_record_url or "")
    inferred_domain = _infer_visible_publisher_domain(row.get("title"), row.get("summary_or_snippet"), row.get("publisher"))
    publisher = _nonempty(row.get("publisher"))
    if not publisher and inferred_domain:
        publisher = _publisher_from_domain(inferred_domain)
    title = gaza_sources.clean_feed_text(row.get("title", ""))
    published_at = _nonempty(row.get("published_at"))
    source_registry_status = _nonempty(row.get("source_registry_status") or row.get("registry_status") or "")
    source_tier = _nonempty(row.get("source_tier") or _source_tier_for_candidate(publisher, source_of_record_url or inferred_domain))
    if not source_registry_status:
        domain = _domain_from_url(source_of_record_url or "") or inferred_domain
        publisher_key = _normalize_publisher(publisher)
        if aggregator_url and not source_of_record_url:
            source_registry_status = "canonical_resolution_needed" if domain else "unresolved_aggregator_candidate"
        elif publisher_key in OFFICIAL_HUMANITARIAN_PUBLISHERS or domain in KNOWN_PROVIDER_DOMAINS:
            source_registry_status = "known_provider"
        elif not url and not canonical_url and not aggregator_url:
            source_registry_status = "manual_seed"
        else:
            source_registry_status = "new_provider_candidate"
    return {
        "url": source_of_record_url,
        "canonical_url": canonical_url or source_of_record_url,
        "publisher": publisher,
        "title": title,
        "published_at": published_at,
        "retrieved_at": _nonempty(row.get("retrieved_at") or retrieved_at or _utc_now()),
        "summary_or_snippet": gaza_sources.clean_feed_text(row.get("summary_or_snippet", "")),
        "discovery_source": _nonempty(row.get("discovery_source") or default_surface_type),
        "discovery_query": _nonempty(row.get("discovery_query") or row.get("query") or source_of_record_url or aggregator_url),
        "google_news_url": aggregator_url,
        "aggregator_url": aggregator_url,
        "source_registry_status": source_registry_status,
        "source_tier": source_tier,
        "surface_type": default_surface_type,
        "normalized_url": normalized_url,
        "original_url": _nonempty(row.get("original_url") or row.get("discovered_url") or source_of_record_url or aggregator_url or ""),
        "inferred_publisher_domain": inferred_domain,
    }


def _load_manifest_index(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {
            "path": str(manifest_path),
            "exists": False,
            "record_count": 0,
            "rows": [],
            "indexes": defaultdict(dict),
            "cluster_members": defaultdict(list),
        }

    payload = _load_json_payload(manifest_path)
    rows = payload if isinstance(payload, list) else payload.get("sources") if isinstance(payload, dict) else []
    rows = [row for row in rows if isinstance(row, dict)]
    indexes: dict[str, dict[str, list[dict[str, Any]]]] = {
        "canonical_url": defaultdict(list),
        "normalized_url": defaultdict(list),
        "title_fingerprint": defaultdict(list),
        "publisher_title_fingerprint": defaultdict(list),
        "dedupe_key": defaultdict(list),
        "duplicate_cluster": defaultdict(list),
    }
    cluster_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = {
            **row,
            "canonical_url": _normalize_url(row.get("canonical_url") or row.get("url") or ""),
            "normalized_url": _normalize_url(row.get("normalized_url") or row.get("url") or ""),
            "title_fingerprint": _fingerprint_title(row.get("title") or ""),
            "publisher_title_fingerprint": _fingerprint_publisher_title(row.get("publisher") or "", row.get("title") or ""),
            "dedupe_key": _nonempty(row.get("dedupe_key") or row.get("claim_fingerprint") or ""),
            "duplicate_cluster": _nonempty(row.get("duplicate_cluster") or row.get("claim_fingerprint") or row.get("dedupe_key") or ""),
        }
        if not normalized["duplicate_cluster"]:
            normalized["duplicate_cluster"] = _fingerprint_cluster(normalized)
        normalized_rows.append(normalized)
        for key_name in indexes:
            value = _nonempty(normalized.get(key_name))
            if value:
                indexes[key_name][value].append(normalized)
        cluster_members[normalized["duplicate_cluster"]].append(normalized)
    return {
        "path": str(manifest_path),
        "exists": True,
        "record_count": len(normalized_rows),
        "rows": normalized_rows,
        "indexes": indexes,
        "cluster_members": cluster_members,
    }


def _registry_lookup(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    registry_by_publisher: dict[str, dict[str, Any]] = {}
    registry_by_domain: dict[str, dict[str, Any]] = {}
    try:
        sources = gaza_sources.load_sources_config(root / "data" / "dispatches" / "gaza" / "sources.yml")
    except Exception:  # noqa: BLE001
        return registry_by_publisher, registry_by_domain
    for source in sources:
        if source.type != "rss":
            continue
        row = {
            "source_id": source.source_id,
            "publisher": source.publisher or source.name,
            "url": source.url,
            "source_state": source.source_state,
            "source_tier": source.source_tier or _source_tier_for_candidate(source.publisher or source.name, source.url),
        }
        publisher_key = _normalize_publisher(row["publisher"])
        if publisher_key:
            registry_by_publisher[publisher_key] = row
        domain_key = _domain_from_url(source.url)
        if domain_key:
            registry_by_domain[domain_key] = row
    return registry_by_publisher, registry_by_domain


def _infer_source_registry_status(candidate: dict[str, Any], registry_by_publisher: dict[str, dict[str, Any]], registry_by_domain: dict[str, dict[str, Any]]) -> str:
    status = _normalize_text(candidate.get("source_registry_status"))
    if status:
        return status
    publisher = _normalize_publisher(candidate.get("publisher"))
    domain = _domain_from_url(candidate.get("url") or candidate.get("canonical_url") or candidate.get("original_url") or "")
    inferred_domain = _normalize_text(candidate.get("inferred_publisher_domain"))
    if domain in AGGREGATOR_DOMAINS:
        domain = ""
    if inferred_domain in AGGREGATOR_DOMAINS:
        inferred_domain = ""
    lookup_domain = domain or inferred_domain
    if _is_google_news_url(candidate.get("aggregator_url") or "") and not _has_source_of_record_url(candidate):
        if publisher in registry_by_publisher or lookup_domain in registry_by_domain or publisher in OFFICIAL_HUMANITARIAN_PUBLISHERS or lookup_domain in KNOWN_PROVIDER_DOMAINS:
            return "canonical_resolution_needed"
        return "unresolved_aggregator_candidate"
    if publisher in registry_by_publisher or lookup_domain in registry_by_domain:
        source = registry_by_publisher.get(publisher) or registry_by_domain.get(lookup_domain) or {}
        state = _normalize_text(source.get("source_state"))
        if state:
            return f"registered_{state}"
        return "registered_provider"
    if publisher in OFFICIAL_HUMANITARIAN_PUBLISHERS or lookup_domain in KNOWN_PROVIDER_DOMAINS:
        return "known_provider"
    if candidate.get("surface_type") == "google_news_rss":
        return "aggregator_discovery_surface"
    if candidate.get("surface_type") == "manual_seed":
        return "manual_seed"
    return "new_provider_candidate"


def _signal_flags(candidate: dict[str, Any]) -> tuple[list[str], int, bool, bool, bool, bool, bool]:
    text = _normalize_text(_text_blob(candidate))
    flags: list[str] = []
    score = 0
    has_gaza = "gaza" in text or any(term in text for term in ("rafah", "khan younis", "jabalia", "deir al-balah", "palestine", "palestinian"))
    for flag, terms in GAZA_SIGNAL_TERMS.items():
        if any(term in text for term in terms):
            flags.append(flag)
            score += 4 if flag in {"casualty_strike_signal", "humanitarian_care_signal", "aid_food_water_medical_access_signal", "health_system_hospital_signal"} else 3
    domain = _domain_from_url(candidate.get("url") or candidate.get("canonical_url") or candidate.get("original_url") or "") or _normalize_text(candidate.get("inferred_publisher_domain"))
    if _normalize_publisher(candidate.get("publisher")) in OFFICIAL_HUMANITARIAN_PUBLISHERS or domain in KNOWN_PROVIDER_DOMAINS:
        flags.append("official_un_ngo_source_signal")
        score += 3
    if _looks_syndicated(candidate):
        flags.append("duplicate_syndicated_copy")
        score -= 4
    published_at = _parse_dt(candidate.get("published_at"))
    audit_date = _parse_dt(candidate.get("audit_date"))
    stale = bool(published_at and audit_date and audit_date.date() - published_at.date() > timedelta(days=STALE_AFTER_DAYS))
    if stale:
        flags.append("stale_item")
        score -= 5
    outside_scope = not has_gaza and not any(term in text for term in ("gaza", "palestine", "palestinian"))
    if outside_scope:
        flags.append("outside_scope_item")
        score -= 6
    weak_signal = False
    if not outside_scope:
        accepted, reason = gaza_sources.gaza_relevance_decision(
            {
                "title": candidate.get("title") or "",
                "summary_or_snippet": candidate.get("summary_or_snippet") or "",
                "url": candidate.get("url") or "",
            }
        )
        if not accepted and reason in {"weak_liveblog_unrelated_topic", "gaza_mention_only_without_strong_topic_signal"}:
            weak_signal = True
        elif not accepted and not has_gaza:
            outside_scope = True
        elif not accepted:
            weak_signal = True
    if any(hint in text for hint in WEAK_CONTEXT_HINTS) and not any(flag in flags for flag in GAZA_SIGNAL_TERMS):
        weak_signal = True
    if weak_signal:
        flags.append("weak_context_only_item")
        score -= 4
    missing_date = not _nonempty(candidate.get("published_at"))
    if missing_date:
        flags.append("missing_date")
        score -= 2
    blocked_or_unresolved = False
    if not _nonempty(candidate.get("url")) and not _nonempty(candidate.get("canonical_url")):
        blocked_or_unresolved = True
        flags.append("blocked_or_unresolved")
        score -= 6
    return flags, score, stale, outside_scope, weak_signal, missing_date, blocked_or_unresolved


def _best_manifest_match(candidate: dict[str, Any], manifest_index: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    indexes = manifest_index.get("indexes") or {}
    for key_name in ("canonical_url", "normalized_url", "publisher_title_fingerprint", "dedupe_key"):
        value = _nonempty(candidate.get(key_name))
        if not value and key_name == "publisher_title_fingerprint":
            value = _fingerprint_publisher_title(candidate.get("publisher") or "", candidate.get("title") or "")
        if not value:
            continue
        matches = indexes.get(key_name, {}).get(value) or []
        if matches:
            return matches[0], key_name
    return None, ""


def _preferred_rank(row: dict[str, Any]) -> tuple[int, int, int, str]:
    tier = _normalize_text(row.get("source_tier"))
    tier_rank = {
        "official_humanitarian": 0,
        "official_claim_source": 0,
        "wire_and_major_international": 1,
        "region_specialist": 2,
        "manual_supplements": 3,
        "republication": 5,
        "aggregator": 6,
        "unknown_or_uncategorized": 7,
    }.get(tier, 4)
    syndication_rank = 1 if _looks_syndicated(row) else 0
    aggregator_rank = 1 if _is_google_news_url(row.get("google_news_url") or row.get("aggregator_url") or row.get("url") or "") else 0
    canonical_rank = 0 if _nonempty(row.get("canonical_url")) else 1
    return (tier_rank, syndication_rank, aggregator_rank + canonical_rank, _nonempty(row.get("publisher")) + "|" + _nonempty(row.get("title")))


def _apply_cluster_comparisons(candidates: list[dict[str, Any]], manifest_index: dict[str, Any]) -> None:
    cluster_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        cluster_members[_nonempty(row.get("duplicate_cluster"))].append(row)
    for manifest_row in manifest_index.get("rows", []):
        cluster_members[_nonempty(manifest_row.get("duplicate_cluster"))].append(manifest_row)

    for cluster_key, members in cluster_members.items():
        if not cluster_key or len(members) < 2:
            continue
        preferred = sorted(members, key=_preferred_rank)[0]
        preferred_manifest = preferred in manifest_index.get("rows", [])
        for row in members:
            if row not in candidates:
                continue
            if row is preferred:
                if _normalize_text(row.get("source_tier")) in {"official_humanitarian", "official_claim_source"}:
                    row.setdefault("comparison_flags", []).append("official_source_preferred")
                continue
            row.setdefault("comparison_flags", []).append("syndicated_duplicate")
            if _normalize_text(preferred.get("source_tier")) in {"official_humanitarian", "official_claim_source"}:
                row["comparison_flags"].append("official_source_preferred")
            if preferred_manifest:
                row["skip_or_accept_reason"] = "official source preferred over republication"


def _compare_candidate(candidate: dict[str, Any], manifest_index: dict[str, Any], registry_by_publisher: dict[str, dict[str, Any]], registry_by_domain: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidate = dict(candidate)
    if not _nonempty(candidate.get("publisher")):
        inferred_domain = _infer_visible_publisher_domain(candidate.get("title"), candidate.get("summary_or_snippet"))
        if inferred_domain:
            candidate["publisher"] = _publisher_from_domain(inferred_domain)
            candidate["inferred_publisher_domain"] = inferred_domain
    candidate["title_fingerprint"] = _fingerprint_title(candidate.get("title") or "")
    candidate["publisher_title_fingerprint"] = _fingerprint_publisher_title(candidate.get("publisher") or "", candidate.get("title") or "")
    candidate["url"] = _normalize_url(candidate.get("url") or "")
    candidate["canonical_url"] = _normalize_url(candidate.get("canonical_url") or "")
    if _is_google_news_url(candidate["url"]):
        candidate["url"] = ""
    if _is_google_news_url(candidate["canonical_url"]):
        candidate["canonical_url"] = ""
    candidate["normalized_url"] = gaza_sources.canonicalize_url(candidate.get("url") or candidate.get("canonical_url") or "")
    candidate["duplicate_cluster"] = _nonempty(candidate.get("duplicate_cluster") or _fingerprint_cluster(candidate))
    candidate["source_registry_status"] = _infer_source_registry_status(candidate, registry_by_publisher, registry_by_domain)
    candidate["source_tier"] = _nonempty(candidate.get("source_tier") or _source_tier_for_candidate(candidate.get("publisher"), candidate.get("canonical_url") or candidate.get("url") or candidate.get("inferred_publisher_domain") or ""))
    if _normalize_text(candidate["source_tier"]) == "aggregator":
        candidate["source_tier"] = "unknown_or_uncategorized"
    candidate["comparison_flags"] = []

    matched_manifest_row, matched_key_type = _best_manifest_match(candidate, manifest_index)
    candidate["manifest_match_key_type"] = matched_key_type
    candidate["already_in_manifest"] = bool(matched_manifest_row)
    if matched_manifest_row:
        candidate["comparison_flags"].append("already_in_manifest")

    signal_flags, score, stale, outside_scope, weak_signal, missing_date, blocked_or_unresolved = _signal_flags(candidate)
    candidate["signal_flags"] = signal_flags
    candidate["ground_signal_score"] = score
    candidate["stale"] = stale
    candidate["outside_scope"] = outside_scope
    candidate["weak_signal"] = weak_signal
    candidate["missing_date"] = missing_date
    candidate["blocked_or_unresolved"] = blocked_or_unresolved

    if blocked_or_unresolved:
        candidate["comparison_flags"].append("blocked_or_unresolved")
        candidate["manual_review_needed"] = True
        candidate["comparison_flags"].append("manual_review_needed")
    if stale:
        candidate["comparison_flags"].append("stale")
    if outside_scope:
        candidate["comparison_flags"].append("outside_scope")
    if weak_signal:
        candidate["comparison_flags"].append("weak_signal")

    registry_match = _normalize_text(candidate["source_registry_status"]) in {"registered_provider", "known_provider", "registered_enabled", "registered_manual_only", "registered_disabled", "registered_diagnostics_only"}
    qualifies = score >= 4 and not any(candidate.get(flag) for flag in ("blocked_or_unresolved", "stale", "outside_scope", "weak_signal"))
    candidate["would_qualify"] = bool(qualifies)

    if candidate["already_in_manifest"]:
        candidate["comparison_flags"].append("already_in_manifest")
        candidate["skip_or_accept_reason"] = "already in manifest"
        candidate["recommended_action"] = "leave in manifest"
    elif blocked_or_unresolved:
        candidate["comparison_flags"].append("blocked_or_unresolved")
        candidate["skip_or_accept_reason"] = "blocked fetch or unresolved URL"
        candidate["recommended_action"] = "resolve fetch or canonical URL"
    elif stale:
        candidate["skip_or_accept_reason"] = "stale item"
        candidate["recommended_action"] = "skip stale item"
    elif outside_scope:
        candidate["skip_or_accept_reason"] = "outside-scope item"
        candidate["recommended_action"] = "skip outside-scope item"
    elif weak_signal:
        candidate["skip_or_accept_reason"] = "weak context-only item"
        candidate["recommended_action"] = "skip weak context-only item"
    elif qualifies and registry_match:
        candidate["comparison_flags"].append("known_provider_missed")
        candidate["skip_or_accept_reason"] = "known provider item missing from manifest"
        candidate["recommended_action"] = "add to intake from known provider"
    elif qualifies and not registry_match:
        candidate["comparison_flags"].append("new_provider_candidate")
        candidate["skip_or_accept_reason"] = "new provider candidate"
        candidate["recommended_action"] = "review as new provider candidate"
    else:
        candidate["manual_review_needed"] = True
        candidate["comparison_flags"].append("manual_review_needed")
        candidate["skip_or_accept_reason"] = "manual review needed"
        candidate["recommended_action"] = "manual review needed"

    if _looks_syndicated(candidate):
        candidate["comparison_flags"].append("syndicated_duplicate")

    if _normalize_text(candidate.get("source_tier")) in {"official_humanitarian", "official_claim_source"} and qualifies:
        candidate["comparison_flags"].append("official_source_preferred")

    candidate["comparison_flags"] = list(dict.fromkeys(candidate["comparison_flags"]))
    candidate["comparison_status"] = candidate["comparison_flags"][0] if candidate["comparison_flags"] else "unclassified"
    if "official_source_preferred" in candidate["comparison_flags"] and "syndicated_duplicate" not in candidate["comparison_flags"] and candidate["already_in_manifest"] is False:
        candidate["recommended_action"] = "prefer official/source-of-record URL"
    if "syndicated_duplicate" in candidate["comparison_flags"] and not candidate["already_in_manifest"]:
        candidate["recommended_action"] = "skip syndicated duplicate; keep original/source-of-record URL"
    if candidate.get("comparison_flags") and "known_provider_missed" in candidate["comparison_flags"] and "official_source_preferred" in candidate["comparison_flags"]:
        candidate["recommended_action"] = "add qualified official source to intake"
    if candidate.get("comparison_flags") and "manual_review_needed" in candidate["comparison_flags"] and qualifies:
        candidate["recommended_action"] = "manual review needed"

    if candidate.get("google_news_url") and not _has_source_of_record_url(candidate):
        candidate["skip_or_accept_reason"] = "google news wrapper unresolved"
        candidate["blocked_or_unresolved"] = True
        if "blocked_or_unresolved" not in candidate["comparison_flags"]:
            candidate["comparison_flags"].append("blocked_or_unresolved")
        if "manual_review_needed" not in candidate["comparison_flags"]:
            candidate["comparison_flags"].append("manual_review_needed")
        candidate["recommended_action"] = "resolve canonical publisher URL before source review"
    return candidate


def _build_recommendations(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    registry_changes: list[dict[str, Any]] = []
    query_changes: list[dict[str, Any]] = []
    tier_changes: list[dict[str, Any]] = []
    seen_registry: set[str] = set()
    seen_query: set[str] = set()
    seen_tier: set[str] = set()
    for row in candidates:
        domain = _domain_from_url(row.get("url") or row.get("canonical_url") or "") or _normalize_text(row.get("inferred_publisher_domain"))
        if domain in AGGREGATOR_DOMAINS or _normalize_publisher(row.get("publisher")) == "google news":
            continue
        if "known_provider_missed" in row.get("comparison_flags", []) or "new_provider_candidate" in row.get("comparison_flags", []):
            key = _normalize_publisher(row.get("publisher")) or domain
            if key and key not in seen_registry:
                seen_registry.add(key)
                registry_changes.append(
                    {
                        "publisher_or_domain": row.get("publisher") or domain,
                        "reason": row.get("skip_or_accept_reason") or "",
                        "recommended_action": row.get("recommended_action") or "",
                    }
                )
        if "known_provider_missed" in row.get("comparison_flags", []):
            query = f'site:{domain} Gaza' if domain else f'"{row.get("publisher") or ""}" Gaza'
            if query not in seen_query:
                seen_query.add(query)
                query_changes.append(
                    {
                        "query": query,
                        "reason": "wide-discovery query gap for known provider",
                    }
                )
        if "official_source_preferred" in row.get("comparison_flags", []):
            tier_key = _normalize_publisher(row.get("publisher")) or row.get("url") or row.get("canonical_url") or domain or ""
            if tier_key not in seen_tier:
                seen_tier.add(tier_key)
                tier_changes.append(
                    {
                        "publisher": row.get("publisher") or domain,
                        "current_tier": row.get("source_tier"),
                        "recommended_tier": "official_humanitarian" if _normalize_publisher(row.get("publisher")) in OFFICIAL_HUMANITARIAN_PUBLISHERS else row.get("source_tier"),
                        "reason": "official/source-of-record URL should outrank republication",
                    }
                )
    return registry_changes, query_changes, tier_changes


def _summary_counts(candidates: list[dict[str, Any]], manifest_index: dict[str, Any]) -> dict[str, Any]:
    flag_counts = Counter(flag for row in candidates for flag in row.get("comparison_flags", []))
    tier_counts = Counter(_normalize_text(row.get("source_tier")) or "unknown" for row in candidates)
    registry_counts = Counter(_normalize_text(row.get("source_registry_status")) or "unknown" for row in candidates)
    outcome_counts = Counter(
        "qualify" if row.get("would_qualify") else "skip"
        for row in candidates
    )
    return {
        "candidate_count": len(candidates),
        "manifest_record_count": manifest_index.get("record_count", 0),
        "already_in_manifest_count": flag_counts.get("already_in_manifest", 0),
        "known_provider_missed_count": flag_counts.get("known_provider_missed", 0),
        "new_provider_candidate_count": flag_counts.get("new_provider_candidate", 0),
        "syndicated_duplicate_count": flag_counts.get("syndicated_duplicate", 0),
        "official_source_preferred_count": flag_counts.get("official_source_preferred", 0),
        "manual_review_needed_count": flag_counts.get("manual_review_needed", 0),
        "blocked_or_unresolved_count": flag_counts.get("blocked_or_unresolved", 0),
        "stale_count": flag_counts.get("stale", 0),
        "outside_scope_count": flag_counts.get("outside_scope", 0),
        "weak_signal_count": flag_counts.get("weak_signal", 0),
        "would_qualify_count": sum(1 for row in candidates if row.get("would_qualify")),
        "would_skip_count": sum(1 for row in candidates if not row.get("would_qualify")),
        "comparison_flag_counts": dict(sorted(flag_counts.items())),
        "source_tier_counts": dict(sorted(tier_counts.items())),
        "source_registry_status_counts": dict(sorted(registry_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
    }


def _filter_rows(candidates: list[dict[str, Any]], flag: str) -> list[dict[str, Any]]:
    return [row for row in candidates if flag in row.get("comparison_flags", [])]


def _published_sort_key(value: Any) -> tuple[int, str]:
    parsed = _parse_dt(value)
    if parsed is None:
        return (1, "")
    return (0, f"{-parsed.timestamp():020.6f}")


def _report_priority(row: dict[str, Any]) -> tuple[int, int, int, int, tuple[int, str], str]:
    current = not bool(row.get("stale"))
    resolved = _has_source_of_record_url(row)
    qualifies = bool(row.get("would_qualify"))
    blocked = bool(row.get("blocked_or_unresolved"))
    weak_or_outside = bool(row.get("weak_signal") or row.get("outside_scope"))
    official = 0 if _normalize_text(row.get("source_tier")) == "official_humanitarian" else 1
    if current and resolved and qualifies and not blocked:
        bucket = 0
    elif current and resolved and not weak_or_outside:
        bucket = 1
    elif current and blocked:
        bucket = 2
    elif current:
        bucket = 3
    elif resolved and not weak_or_outside:
        bucket = 4
    elif blocked:
        bucket = 5
    else:
        bucket = 6
    return (
        bucket,
        0 if current else 1,
        0 if resolved else 1,
        official,
        _published_sort_key(row.get("published_at")),
        (_nonempty(row.get("publisher")) + "|" + _nonempty(row.get("title"))).lower(),
    )


def _sort_report_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_report_priority)


def _render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(label for _key, label in columns) + " |"
    separator = "| " + " | ".join("---" for _key, _label in columns) + " |"
    lines = [header, separator]
    for row in rows:
        values: list[str] = []
        for key, _label in columns:
            value = row.get(key)
            if isinstance(value, bool):
                text = "yes" if value else "no"
            elif isinstance(value, list):
                text = ", ".join(str(item) for item in value)
            elif value is None:
                text = ""
            else:
                text = str(value)
            values.append(text.replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _report_paths(output_dir: Path, edition_date: str) -> tuple[Path, Path]:
    return (
        output_dir / f"{REPORT_PREFIX}_{edition_date}.json",
        output_dir / f"{REPORT_PREFIX}_{edition_date}.md",
    )


def _env_flag(name: str) -> bool:
    return _normalize_text(os.getenv(name)) in {"1", "true", "yes", "on"}


def build_gaza_wide_source_audit(
    root: Path,
    edition_date: str,
    *,
    manifest_path: Path | None = None,
    queries_file: Path | None = None,
    manual_urls_file: Path | None = None,
    fetch_rss_items_fn: Callable[[str], list[dict[str, str]]] | None = None,
    output_dir: Path | None = None,
    fetch_options: AuditFetchOptions | None = None,
) -> dict[str, Any]:
    edition = date.fromisoformat(edition_date)
    resolved_output_dir = (root / output_dir) if output_dir is not None and not output_dir.is_absolute() else (output_dir or (root / DEFAULT_OUTPUT_DIR))
    resolved_output_dir = resolved_output_dir.resolve() if resolved_output_dir.is_absolute() else resolved_output_dir
    manifest = _load_manifest_index(_manifest_path(root, edition_date, manifest_path))
    registry_by_publisher, registry_by_domain = _registry_lookup(root)
    surfaces = [_normalize_surface_row(row, "query_surface") for row in _default_query_surfaces(root, edition_date)]

    if queries_file is not None:
        query_rows = [_normalize_surface_row(row, "query_surface") for row in _load_query_file(queries_file)]
        surfaces.extend(query_rows)

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    fetch_rss_items_fn = fetch_rss_items_fn or _default_audit_fetch_rss_items(fetch_options or AuditFetchOptions())
    for surface in surfaces:
        surface_type = _normalize_text(surface.get("surface_type"))
        if surface.get("query_url") and surface_type in {"known_publisher_rss", "google_news_rss", "query_surface"}:
            rows, feed_warnings = _discover_from_feed_surface(surface, fetch_rss_items_fn=fetch_rss_items_fn)
            warnings.extend(feed_warnings)
            candidates.extend(rows)
        elif surface.get("query_url"):
            candidates.append(
                {
                    "url": _nonempty(surface.get("query_url")),
                    "canonical_url": _nonempty(surface.get("query_url")),
                    "publisher": _nonempty(surface.get("publisher")),
                    "title": _nonempty(surface.get("title")),
                    "published_at": _nonempty(surface.get("published_at")),
                    "retrieved_at": _utc_now(),
                    "summary_or_snippet": _nonempty(surface.get("summary_or_snippet")),
                    "discovery_source": _nonempty(surface.get("discovery_source") or surface_type or "query_surface"),
                    "discovery_query": _nonempty(surface.get("discovery_query") or surface.get("query_url")),
                    "google_news_url": _nonempty(surface.get("google_news_url") or ""),
                    "aggregator_url": _nonempty(surface.get("aggregator_url") or ""),
                    "source_registry_status": surface.get("source_registry_status") or "query_surface",
                    "source_tier": surface.get("source_tier") or _source_tier_for_candidate(surface.get("publisher"), surface.get("query_url")),
                    "surface_type": surface_type or "query_surface",
                    "registry_source_id": _nonempty(surface.get("registry_source_id")),
                    "normalized_url": gaza_sources.canonicalize_url(surface.get("query_url") or ""),
                    "original_url": _nonempty(surface.get("query_url")),
                }
            )

    retrieved_at = _utc_now()
    if manual_urls_file is not None:
        for seed in _load_seed_file(manual_urls_file):
            candidates.append(_seed_to_candidate(seed, default_surface_type="manual_seed", retrieved_at=retrieved_at))

    if not candidates:
        warnings.append("no discovery candidates were collected")

    for candidate in candidates:
        candidate["audit_date"] = edition.isoformat()
    candidates = [_compare_candidate(candidate, manifest, registry_by_publisher, registry_by_domain) for candidate in candidates]
    _apply_cluster_comparisons(candidates, manifest)

    for candidate in candidates:
        if "blocked_or_unresolved" in candidate.get("comparison_flags", []) and candidate.get("would_qualify"):
            candidate["recommended_action"] = "resolve fetch or canonical URL"

    registry_changes, query_changes, tier_changes = _build_recommendations(candidates)
    summary = _summary_counts(candidates, manifest)
    missing_but_likely = _sort_report_rows([row for row in candidates if row.get("would_qualify") and not row.get("already_in_manifest")])
    known_provider_missed = _sort_report_rows(_filter_rows(candidates, "known_provider_missed"))
    new_provider_candidates = _sort_report_rows(_filter_rows(candidates, "new_provider_candidate"))
    official_humanitarian_candidates = _sort_report_rows([row for row in candidates if _normalize_text(row.get("source_tier")) == "official_humanitarian"])
    duplicate_and_syndicated = _sort_report_rows([row for row in candidates if {"syndicated_duplicate", "official_source_preferred"} & set(row.get("comparison_flags", []))])
    blocked_or_unresolved = _sort_report_rows(_filter_rows(candidates, "blocked_or_unresolved"))
    blocked_or_unresolved_current = _sort_report_rows([row for row in blocked_or_unresolved if not row.get("stale")])
    blocked_or_unresolved_stale = _sort_report_rows([row for row in blocked_or_unresolved if row.get("stale")])
    stale_or_outside_or_weak = _sort_report_rows([row for row in candidates if {"stale", "outside_scope", "weak_signal"} & set(row.get("comparison_flags", []))])

    report = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": _utc_now(),
        "manifest": {
            "path": manifest.get("path"),
            "exists": manifest.get("exists", False),
            "record_count": manifest.get("record_count", 0),
        },
        "discovery": {
            "default_query_surface_count": len(surfaces),
            "manual_seed_count": 0 if manual_urls_file is None else len(_load_seed_file(manual_urls_file)),
            "query_file": str(queries_file) if queries_file else "",
            "manual_urls_file": str(manual_urls_file) if manual_urls_file else "",
            "fetch_timeout_seconds": (fetch_options or AuditFetchOptions()).timeout,
            "curl_no_revoke_opt_in": bool((fetch_options or AuditFetchOptions()).allow_curl_no_revoke),
        },
        "summary": summary,
        "candidates": candidates,
        "missing_but_likely_qualified": missing_but_likely,
        "known_provider_missed": known_provider_missed,
        "new_provider_candidates": new_provider_candidates,
        "official_humanitarian_candidates": official_humanitarian_candidates,
        "duplicates_and_syndications": duplicate_and_syndicated,
        "blocked_or_unresolved": blocked_or_unresolved,
        "blocked_or_unresolved_current": blocked_or_unresolved_current,
        "blocked_or_unresolved_stale": blocked_or_unresolved_stale,
        "stale_outside_scope_weak_signal": stale_or_outside_or_weak,
        "recommended_registry_changes": registry_changes,
        "recommended_query_changes": query_changes,
        "recommended_source_tier_changes": tier_changes,
        "warnings": warnings,
    }
    json_path, md_path = _report_paths(resolved_output_dir, edition_date)
    report["json_report_path"] = str(json_path)
    report["markdown_report_path"] = str(md_path)
    return report


def render_gaza_wide_source_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    warnings = report.get("warnings") or []
    lines = [
        "# Gaza Wide Source Discovery Audit",
        "",
        f"- Edition date: `{report.get('edition_date')}`",
        f"- Manifest: `{report.get('manifest', {}).get('path')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        "",
        "## Summary Counts",
        "",
        _render_table(
            [
                {"metric": "candidates", "value": summary.get("candidate_count", 0)},
                {"metric": "already_in_manifest", "value": summary.get("already_in_manifest_count", 0)},
                {"metric": "known_provider_missed", "value": summary.get("known_provider_missed_count", 0)},
                {"metric": "new_provider_candidate", "value": summary.get("new_provider_candidate_count", 0)},
                {"metric": "official_source_preferred", "value": summary.get("official_source_preferred_count", 0)},
                {"metric": "syndicated_duplicate", "value": summary.get("syndicated_duplicate_count", 0)},
                {"metric": "blocked_or_unresolved", "value": summary.get("blocked_or_unresolved_count", 0)},
                {"metric": "stale", "value": summary.get("stale_count", 0)},
                {"metric": "outside_scope", "value": summary.get("outside_scope_count", 0)},
                {"metric": "weak_signal", "value": summary.get("weak_signal_count", 0)},
            ],
            [("metric", "metric"), ("value", "count")],
        ),
        "",
        "## Warnings",
        *([f"- {warning}" for warning in warnings] if warnings else ["_None._"]),
        "",
        "## Missing But Likely Qualified",
        _render_table(
            report.get("missing_but_likely_qualified", []),
            [("publisher", "publisher"), ("title", "title"), ("canonical_url", "canonical_url"), ("comparison_status", "status"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Known Provider Misses",
        _render_table(
            report.get("known_provider_missed", []),
            [("publisher", "publisher"), ("title", "title"), ("url", "url"), ("source_registry_status", "source_registry_status"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## New Provider Candidates",
        _render_table(
            report.get("new_provider_candidates", []),
            [("publisher", "publisher"), ("title", "title"), ("url", "url"), ("source_tier", "source_tier"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Official Or Humanitarian Candidates",
        _render_table(
            report.get("official_humanitarian_candidates", []),
            [("publisher", "publisher"), ("title", "title"), ("comparison_flags", "flags"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Duplicates And Syndications",
        _render_table(
            report.get("duplicates_and_syndications", []),
            [("publisher", "publisher"), ("title", "title"), ("comparison_flags", "flags"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Current Blocked Or Unresolved",
        _render_table(
            report.get("blocked_or_unresolved_current", []),
            [("publisher", "publisher"), ("title", "title"), ("google_news_url", "google_news_url"), ("url", "url"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Stale Blocked Or Unresolved",
        _render_table(
            report.get("blocked_or_unresolved_stale", []),
            [("publisher", "publisher"), ("title", "title"), ("google_news_url", "google_news_url"), ("url", "url"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## All Blocked Or Unresolved",
        _render_table(
            report.get("blocked_or_unresolved", []),
            [("publisher", "publisher"), ("title", "title"), ("google_news_url", "google_news_url"), ("url", "url"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Stale, Outside Scope, Weak Signal",
        _render_table(
            report.get("stale_outside_scope_weak_signal", []),
            [("publisher", "publisher"), ("title", "title"), ("comparison_flags", "flags"), ("skip_or_accept_reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Recommended Registry Changes",
        _render_table(
            report.get("recommended_registry_changes", []),
            [("publisher_or_domain", "publisher_or_domain"), ("reason", "reason"), ("recommended_action", "recommended_action")],
        ),
        "",
        "## Recommended Query Changes",
        _render_table(
            report.get("recommended_query_changes", []),
            [("query", "query"), ("reason", "reason")],
        ),
        "",
        "## Recommended Source Tier Changes",
        _render_table(
            report.get("recommended_source_tier_changes", []),
            [("publisher", "publisher"), ("current_tier", "current_tier"), ("recommended_tier", "recommended_tier"), ("reason", "reason")],
        ),
    ]
    return "\n".join(lines) + "\n"


def write_gaza_wide_source_audit_report(
    root: Path,
    edition_date: str,
    *,
    manifest_path: Path | None = None,
    queries_file: Path | None = None,
    manual_urls_file: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    fetch_rss_items_fn: Callable[[str], list[dict[str, str]]] | None = None,
    fetch_options: AuditFetchOptions | None = None,
) -> dict[str, Any]:
    report = build_gaza_wide_source_audit(
        root,
        edition_date,
        manifest_path=manifest_path,
        queries_file=queries_file,
        manual_urls_file=manual_urls_file,
        output_dir=output_dir,
        fetch_rss_items_fn=fetch_rss_items_fn,
        fetch_options=fetch_options,
    )
    if dry_run:
        return report
    resolved_output_dir = (root / output_dir) if output_dir is not None and not output_dir.is_absolute() else (output_dir or (root / DEFAULT_OUTPUT_DIR))
    if not resolved_output_dir.is_absolute():
        resolved_output_dir = (root / resolved_output_dir).resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = _report_paths(resolved_output_dir, edition_date)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_gaza_wide_source_audit_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Gaza source discovery breadth against the current Gaza manifest.")
    parser.add_argument("--date", required=True, help="Audit date in YYYY-MM-DD format.")
    parser.add_argument("--manifest", help="Optional path to a Gaza sources_manifest.json file.")
    parser.add_argument("--queries-file", help="Optional JSON file of extra discovery queries.")
    parser.add_argument("--manual-urls-file", help="Optional plain text or JSON file of manual seed URLs.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for the JSON and markdown report outputs.")
    parser.add_argument("--feed-timeout", type=int, default=DEFAULT_FETCH_TIMEOUT, help=f"Per-feed fetch timeout in seconds. Default: {DEFAULT_FETCH_TIMEOUT}.")
    parser.add_argument(
        "--allow-curl-no-revoke",
        action="store_true",
        help="Audit-only opt-in Windows curl revocation workaround after verified Python TLS attempts fail. Also enabled by BLUEFERN_GAZA_AUDIT_ALLOW_CURL_NO_REVOKE=1.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build the audit without writing output files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = ROOT
    manifest_path = Path(args.manifest) if args.manifest else None
    queries_file = Path(args.queries_file) if args.queries_file else None
    manual_urls_file = Path(args.manual_urls_file) if args.manual_urls_file else None
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    fetch_options = AuditFetchOptions(
        timeout=max(1, int(args.feed_timeout or DEFAULT_FETCH_TIMEOUT)),
        allow_curl_no_revoke=bool(args.allow_curl_no_revoke or _env_flag("BLUEFERN_GAZA_AUDIT_ALLOW_CURL_NO_REVOKE")),
    )
    report = write_gaza_wide_source_audit_report(
        root,
        args.date,
        manifest_path=manifest_path,
        queries_file=queries_file,
        manual_urls_file=manual_urls_file,
        output_dir=output_dir,
        dry_run=args.dry_run,
        fetch_options=fetch_options,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
