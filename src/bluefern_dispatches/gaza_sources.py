from __future__ import annotations

import email.utils
import gzip
import hashlib
import json
import mimetypes
import re
import string
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REQUIRED_SOURCE_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
}
GAZA_TERMS = re.compile(r"\b(gaza|rafah|khan younis|deir al-balah|jabalia|palestinian territories|occupied palestinian territory)\b", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDER_RE = re.compile(r"^(replace with|actual source|actual publisher|actual-source-url)", re.I)
WHITESPACE_RE = re.compile(r"\s+")
PUNCT_TRANS = str.maketrans({char: " " for char in string.punctuation})
SMART_CHAR_TRANS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\xa0": " ",
    }
)
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "ocid",
    "ref",
    "ref_src",
    "igshid",
}


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    name: str
    url: str
    type: str
    enabled: bool
    publisher: str
    reliability_tier: str
    category_hint: str
    region_scope: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_title(title: str) -> str:
    value = str(title or "").strip().translate(SMART_CHAR_TRANS).lower()
    value = value.translate(PUNCT_TRANS)
    return WHITESPACE_RE.sub(" ", value).strip()


def normalize_publisher(publisher: str) -> str:
    value = normalize_title(publisher)
    value = re.sub(r"\b(news|media|wire|agency|press|the)\b", " ", value)
    return WHITESPACE_RE.sub(" ", value).strip()


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    query_pairs = parse_qsl(parts.query, keep_blank_values=False)
    filtered = [
        (key, value)
        for key, value in query_pairs
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    query = urlencode(filtered)
    return urlunsplit((scheme, netloc, path, query, ""))


def canonical_source_key(source: dict[str, Any]) -> dict[str, str]:
    title = normalize_title(str(source.get("title") or ""))
    publisher = normalize_publisher(str(source.get("publisher") or ""))
    canonical_url = canonicalize_url(str(source.get("canonical_url") or source.get("url") or ""))
    normalized_url = canonicalize_url(str(source.get("url") or ""))
    publisher_title = f"{publisher}|{title}" if publisher and title else ""
    return {
        "canonical_url": canonical_url,
        "normalized_url": normalized_url,
        "publisher_title": publisher_title,
        "title_fingerprint": title,
    }


def story_claim_fingerprint(source_or_story: dict[str, Any]) -> str:
    title = normalize_title(str(source_or_story.get("title") or ""))
    publisher = normalize_publisher(str(source_or_story.get("publisher") or ""))
    category = normalize_title(str(source_or_story.get("category_hint") or source_or_story.get("category") or ""))
    return "|".join(part for part in (publisher, title, category) if part)


def _safe_parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_google_news_rss(url: str) -> bool:
    text = str(url or "").lower()
    return "news.google.com" in text and "/rss/" in text


def _iter_prior_source_manifests(root: Path, edition_date: str, lookback_days: int) -> list[tuple[str, Path]]:
    target = date.fromisoformat(edition_date)
    manifests: list[tuple[str, Path]] = []
    for days_back in range(1, lookback_days + 1):
        prior = (target - timedelta(days=days_back)).isoformat()
        for manifest in (
            root / "output" / "dispatches" / "gaza" / "editions" / prior / "sources_manifest.json",
            root / "output" / "site" / "gaza" / "editions" / prior / "sources_manifest.json",
        ):
            if manifest.exists():
                manifests.append((prior, manifest))
    return manifests


def filter_recent_duplicate_sources(
    root: Path,
    edition_date: str,
    candidates: list[dict[str, Any]],
    lookback_days: int = 7,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    checked_editions: set[str] = set()
    for prior_date, manifest_path in _iter_prior_source_manifests(root, edition_date, lookback_days):
        checked_editions.add(prior_date)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue
        for prior in payload:
            if not isinstance(prior, dict):
                continue
            keys = canonical_source_key(prior)
            claim = story_claim_fingerprint(prior)
            for key_type in ("canonical_url", "normalized_url", "publisher_title", "title_fingerprint"):
                value = keys.get(key_type) or ""
                if value:
                    seen_by_key.setdefault((key_type, value), {"edition_date": prior_date, "source": prior, "key_type": key_type})
            if claim:
                seen_by_key.setdefault(("claim_fingerprint", claim), {"edition_date": prior_date, "source": prior, "key_type": "claim_fingerprint"})

    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    stale_risk: list[dict[str, Any]] = []
    for source in candidates:
        keys = canonical_source_key(source)
        claim = story_claim_fingerprint(source)
        source["dedupe_key"] = keys.get("publisher_title") or keys.get("canonical_url") or keys.get("normalized_url") or keys.get("title_fingerprint") or ""
        source["title_fingerprint"] = keys.get("title_fingerprint") or ""
        source["claim_fingerprint"] = claim

        matches: list[tuple[str, str, dict[str, Any]]] = []
        for key_type in ("canonical_url", "normalized_url", "publisher_title", "title_fingerprint"):
            value = keys.get(key_type) or ""
            if value and (key_type, value) in seen_by_key:
                matches.append((key_type, value, seen_by_key[(key_type, value)]))
        if claim and ("claim_fingerprint", claim) in seen_by_key:
            matches.append(("claim_fingerprint", claim, seen_by_key[("claim_fingerprint", claim)]))

        if not matches:
            kept.append(source)
            continue

        published_at = _safe_parse_dt(str(source.get("published_at") or ""))
        suppress = True
        reason = "matched recent prior edition"
        matched = matches[0]
        prior_src = matched[2]["source"]
        prior_published_at = _safe_parse_dt(str(prior_src.get("published_at") or ""))
        if published_at and prior_published_at and published_at > prior_published_at and matched[0] in {"canonical_url", "normalized_url"}:
            suppress = False
            reason = "newer publication timestamp than prior url match"
        if str(source.get("published_at") or "").strip() == "":
            stale_risk.append({"title": source.get("title"), "publisher": source.get("publisher"), "url": source.get("url")})
        if _is_google_news_rss(str(source.get("url") or "")) and not str(source.get("canonical_url") or "").strip():
            source["google_news_wrapper_url"] = keys.get("normalized_url") or ""
        if suppress:
            source["repeated_from_edition_date"] = matched[2]["edition_date"]
            suppressed.append(
                {
                    "title": source.get("title"),
                    "publisher": source.get("publisher"),
                    "url": source.get("url"),
                    "published_at": source.get("published_at"),
                    "retrieved_at": source.get("retrieved_at"),
                    "matched_prior_edition": matched[2]["edition_date"],
                    "matched_key_type": matched[0],
                    "matched_prior_title": prior_src.get("title"),
                    "matched_prior_url": prior_src.get("url"),
                    "reason": reason,
                }
            )
        else:
            kept.append(source)

    report = {
        "edition_date": edition_date,
        "lookback_days": lookback_days,
        "prior_editions_checked": sorted(checked_editions),
        "input_candidate_count": len(candidates),
        "kept_candidate_count": len(kept),
        "suppressed_candidate_count": len(suppressed),
        "suppressed_candidates": suppressed,
        "stale_risk_candidates": stale_risk,
        "warnings": [],
    }
    if candidates and not kept:
        report["warnings"].append("all candidates were suppressed as repeated or stale-risk")
    return kept, report


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> Any:
    value = _strip_quotes(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Load the small list-of-maps schema used by data/dispatches/gaza/sources.yml."""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_key = line[:-1].strip()
            result[current_key] = []
            current_item = None
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_key is None:
                raise ValueError("YAML item found before a list key")
            current_item = {}
            result[current_key].append(current_item)
            rest = stripped[2:].strip()
            if rest:
                key, value = rest.split(":", 1)
                current_item[key.strip()] = _parse_scalar(value.strip())
            continue
        if current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = _parse_scalar(value.strip())
            continue
        raise ValueError(f"Unsupported YAML line: {raw_line}")
    return result


def load_sources_config(path: Path) -> list[SourceDefinition]:
    if not path.exists():
        raise FileNotFoundError(f"Gaza sources config does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
    except Exception:
        payload = _minimal_yaml_load(text)
    raw_sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list):
        raise ValueError("sources.yml must contain a sources list")
    definitions: list[SourceDefinition] = []
    for index, item in enumerate(raw_sources, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"sources.yml item {index} is not an object")
        definitions.append(
            SourceDefinition(
                source_id=str(item.get("source_id") or "").strip(),
                name=str(item.get("name") or "").strip(),
                url=str(item.get("url") or "").strip(),
                type=str(item.get("type") or "").strip().lower(),
                enabled=bool(item.get("enabled")),
                publisher=str(item.get("publisher") or item.get("name") or "").strip(),
                reliability_tier=str(item.get("reliability_tier") or "").strip(),
                category_hint=str(item.get("category_hint") or "").strip(),
                region_scope=str(item.get("region_scope") or "").strip(),
            )
        )
    return definitions


def parse_feed_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value[: len(fmt)], fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except Exception:
            continue
    return value


def _text(element: ET.Element, child_name: str) -> str:
    child = element.find(child_name)
    if child is not None and child.text:
        return child.text.strip()
    for item in element:
        if item.tag.lower().endswith(child_name.lower()) and item.text:
            return item.text.strip()
    return ""


def fetch_rss_items(url: str, timeout: int = 20) -> list[dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "BlueFernDispatches/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        content_encoding = str(response.headers.get("Content-Encoding") or "").lower()
    if content_encoding == "gzip" or data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    stripped = data.lstrip(b"\xef\xbb\xbf\r\n\t ")
    if not stripped:
        raise ValueError("empty feed response")
    if not stripped.startswith(b"<"):
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else None
        detail = f"content-type={content_type}" if content_type else "response does not start with XML"
        if guessed:
            detail = f"{detail}, guessed_extension={guessed}"
        raise ValueError(f"non-XML feed response ({detail})")
    root = ET.fromstring(data)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    records: list[dict[str, str]] = []
    for item in items:
        title = _text(item, "title")
        link = _text(item, "link")
        if not link:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = str(link_el.attrib.get("href") or "").strip()
        records.append(
            {
                "title": title,
                "url": link,
                "published_at": parse_feed_date(_text(item, "pubDate") or _text(item, "published") or _text(item, "updated")),
                "summary_or_snippet": _text(item, "description") or _text(item, "summary"),
            }
        )
    return records


def is_gaza_relevant(item: dict[str, str]) -> bool:
    haystack = " ".join([item.get("title", ""), item.get("summary_or_snippet", ""), item.get("url", "")])
    return bool(GAZA_TERMS.search(haystack))


def is_on_requested_date(published_at: str, edition_date: str) -> bool:
    if not published_at:
        return True
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", published_at)
    if not match:
        return True
    return match.group(1) == edition_date


def source_record_id(source_id: str, title: str, url: str, edition_date: str) -> str:
    digest = hashlib.sha1(f"{source_id}|{title}|{url}".encode("utf-8")).hexdigest()[:12]
    return f"gaza-{edition_date}-{source_id}-{digest}"


def normalize_rss_item(item: dict[str, str], source: SourceDefinition, edition_date: str, retrieved_at: str) -> dict[str, Any] | None:
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    if not title or not url or not url.startswith(("http://", "https://")):
        return None
    published_at = item.get("published_at") or ""
    return {
        "source_record_id": source_record_id(source.source_id, title, url, edition_date),
        "title": title,
        "url": url,
        "publisher": source.publisher,
        "published_at": published_at or f"{edition_date}T00:00:00+00:00",
        "retrieved_at": retrieved_at,
        "summary_or_snippet": item.get("summary_or_snippet", ""),
        "source_type": "rss",
        "region_scope": source.region_scope,
        "category_hint": source.category_hint,
        "reliability_tier": source.reliability_tier,
    }


def validate_source_records(records: list[dict[str, Any]], min_sources: int = 1) -> list[str]:
    errors: list[str] = []
    if len(records) < min_sources:
        errors.append(f"source count {len(records)} is below minimum {min_sources}")
    for index, record in enumerate(records, start=1):
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
        url = str(record.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
        if "example.com" in url.lower():
            errors.append(f"source record {index} uses placeholder URL: {url}")
        if PLACEHOLDER_RE.search(url.replace("https://", "").replace("http://", "")):
            errors.append(f"source record {index} uses placeholder URL: {url}")
        if str(record.get("title") or "").strip().lower().startswith("replace with"):
            errors.append(f"source record {index} uses placeholder title")
        if PLACEHOLDER_RE.search(str(record.get("title") or "").strip()):
            errors.append(f"source record {index} uses placeholder title")
        if str(record.get("publisher") or "").strip().lower().startswith("replace with"):
            errors.append(f"source record {index} uses placeholder publisher")
        if PLACEHOLDER_RE.search(str(record.get("publisher") or "").strip()):
            errors.append(f"source record {index} uses placeholder publisher")
    return errors


def manual_source_path(root: Path, edition_date: str) -> Path:
    if not DATE_RE.match(edition_date):
        raise ValueError(f"date must use YYYY-MM-DD: {edition_date}")
    return root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def load_manual_source_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def write_source_records(root: Path, edition_date: str, records: list[dict[str, Any]], filename: str = "manual_sources.json") -> Path:
    if not DATE_RE.match(edition_date):
        raise ValueError(f"date must use YYYY-MM-DD: {edition_date}")
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def collect_gaza_sources(
    root: Path,
    edition_date: str,
    max_sources: int = 12,
    min_sources: int = 1,
    output_filename: str = "manual_sources.json",
    prefer_manual: bool = True,
) -> dict[str, Any]:
    retrieved_at = utc_now()
    warnings: list[str] = []
    errors: list[str] = []
    failed_source_ids: list[dict[str, str]] = []
    manual_path = manual_source_path(root, edition_date)
    if prefer_manual and manual_path.exists():
        try:
            manual_records = load_manual_source_records(manual_path)
            manual_errors = validate_source_records(manual_records, min_sources=min_sources)
        except Exception as exc:
            manual_records = []
            manual_errors = [str(exc)]
        if not manual_errors:
            return {
                "ok": True,
                "date": edition_date,
                "source_file": str(manual_path),
                "source_count": len(manual_records),
                "sources": manual_records[:max_sources],
                "warnings": warnings,
                "errors": errors,
                "failed_source_ids": failed_source_ids,
                "source_mode_used": "manual",
            }
        warnings.append(f"manual_sources.json was present but invalid: {'; '.join(manual_errors)}")

    config_path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    definitions = load_sources_config(config_path)
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source in definitions:
        if len(records) >= max_sources:
            break
        if not source.enabled or source.type != "rss":
            continue
        source_record_start = len(records)
        try:
            items = fetch_rss_items(source.url)
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            warnings.append(f"{source.source_id}: {reason}")
            failed_source_ids.append({"source_id": source.source_id, "reason": reason})
            continue
        for item in items:
            if len(records) >= max_sources:
                break
            if not is_gaza_relevant(item):
                continue
            if not is_on_requested_date(item.get("published_at", ""), edition_date):
                continue
            record = normalize_rss_item(item, source, edition_date, retrieved_at)
            if record is None:
                continue
            url_key = record["url"].lower()
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            records.append(record)
        if len(records) == source_record_start:
            failed_source_ids.append({"source_id": source.source_id, "reason": f"no matching Gaza items for {edition_date}"})

    validation_errors = validate_source_records(records, min_sources=min_sources)
    errors.extend(validation_errors)
    source_file = None
    if not errors:
        source_file = write_source_records(root, edition_date, records, output_filename)
    return {
        "ok": not errors,
        "date": edition_date,
        "source_file": str(source_file) if source_file else None,
        "source_count": len(records),
        "sources": records,
        "warnings": warnings,
        "errors": errors,
        "failed_source_ids": failed_source_ids,
        "source_mode_used": "auto",
    }
