from __future__ import annotations

import argparse
import csv
import html
import json
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_discovery_record import (
    DISCOVERY_COLLECTION_SCHEMA_VERSION,
    DISCOVERY_QUALITY_SCHEMA_VERSION,
    has_description,
    hostname,
    is_google_news_url,
    likely_operational,
    meaningful_title,
    normalize_article_url,
    packet_row_to_completed_intake,
    reviewer_packet_from_records,
    write_reviewer_packet,
)
from bluefern_dispatches.care_line_sources import ADAPTER_PARSERS, source_health_markdown
from bluefern_dispatches.care_line_reviewed_export import refuse_public_or_pages_path
from bluefern_dispatches.care_line_source_registry import CareLineSource, load_registry, registry_markdown
from bluefern_dispatches.care_line_source_recovery import is_wrapper_url


DIRECT_DISCOVERY_OPERATOR_VERSION = "care-line-direct-discovery-phase13-v1"
DIRECT_QUALITY_SCHEMA_VERSION = "bluefern.care_line.direct_discovery_quality.v1"
SOURCE_HEALTH_SCHEMA_VERSION = "bluefern.care_line.source_health.v1"

SEARCH_PATH_HINTS = ("/search", "/tag/", "/tags/", "/category/", "/categories/")
TRACKING_HOST_HINTS = ("feedproxy.google.com", "news.google.com", "google.com")
TOPIC_TERMS = (
    "hospital",
    "clinic",
    "medical center",
    "emergency department",
    "labor and delivery",
    "maternity",
    "behavioral health",
    "psychiatric",
    "pharmacy",
    "nursing home",
    "dialysis",
    "urgent care",
    "ambulance",
    "health center",
    "closure",
    "suspension",
    "reopening",
    "reduction",
    "relocation",
    "conversion",
    "service loss",
)
DIRECT_QUALITY_THRESHOLDS = {
    "reviewer_usable_count": 30,
    "article_url_present_rate": 0.80,
    "meaningful_title_rate": 0.95,
    "publisher_present_rate": 0.95,
    "publication_date_present_rate": 0.80,
    "description_present_rate": 0.60,
    "wrapper_only_rate_max": 0.0,
    "malformed_rate_max": 0.05,
    "distinct_article_url_count": 20,
    "distinct_publisher_or_hostname_count": 15,
    "likely_operational_event_count": 10,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(payload: Any) -> str:
    from bluefern_dispatches.care_line_discovery_record import stable_hash as _stable_hash

    return _stable_hash(payload)


def stable_id(prefix: str, *parts: Any) -> str:
    from bluefern_dispatches.care_line_discovery_record import stable_id as _stable_id

    return _stable_id(prefix, *parts)


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_publication_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    return match.group(0) if match else raw[:10]


def _within_date_window(value: str, date_from: str = "", date_to: str = "") -> bool:
    published = normalize_publication_date(value)
    if not published:
        return False if date_from or date_to else True
    if date_from and published < date_from:
        return False
    if date_to and published > date_to:
        return False
    return True


def allowed_hosts(source: CareLineSource) -> set[str]:
    hosts = {hostname(source.homepage_url), hostname(source.feed_url), *(host.lower().removeprefix("www.") for host in source.allowed_hosts)}
    return {host for host in hosts if host}


def validate_article_url(url: str, source: CareLineSource) -> tuple[str, str, str]:
    normalized = normalize_article_url(url)
    if not normalized:
        return "", "rejected", "invalid_url"
    parsed = urllib.parse.urlsplit(normalized)
    host = hostname(normalized)
    if is_google_news_url(normalized) or is_wrapper_url(normalized):
        return "", "rejected", "google_news_wrapper"
    if host in TRACKING_HOST_HINTS or "feedproxy" in host:
        return "", "rejected", "tracking_redirect"
    if any(parsed.path.lower().startswith(path) for path in SEARCH_PATH_HINTS):
        return "", "rejected", "search_or_listing_url"
    allowed = allowed_hosts(source)
    if allowed and host not in allowed and not any(host.endswith("." + item) for item in allowed):
        return normalized, "warning", "unexpected_hostname"
    return normalized, "accepted", ""


def publisher_warnings(source: CareLineSource, feed_publisher: str, article_url: str) -> list[str]:
    warnings: list[str] = []
    if not feed_publisher:
        warnings.append("missing_feed_publisher")
    elif feed_publisher.casefold() != source.publisher.casefold():
        warnings.append("publisher_mismatch")
    url_host = hostname(article_url)
    if article_url and url_host and url_host not in allowed_hosts(source):
        warnings.append("unexpected_hostname")
    if url_host and url_host in {"prnewswire.com", "globenewswire.com"}:
        warnings.append("third_party_distribution_host")
    return sorted(set(warnings))


def relevant_to_care_line(title: str, description: str) -> bool:
    blob = f"{title} {description}".casefold()
    return any(term in blob for term in TOPIC_TERMS)


def raw_metadata_subset(item: Mapping[str, Any], source: CareLineSource) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "source_name": source.name,
        "source_publisher": source.publisher,
        "source_type": source.source_type,
        "feed_url": source.feed_url,
        "raw_title": _text(item, "title"),
        "raw_url": _text(item, "url", "link", "id"),
        "raw_source": _text(item, "source", "publisher", "author"),
        "raw_publication_date": _text(item, "published_at", "published", "updated", "pubDate"),
        "raw_description": strip_html(_text(item, "description", "summary", "content"))[:2000],
        "raw_id": _text(item, "id", "guid"),
    }


def discovery_record_from_direct_item(item: Mapping[str, Any], source: CareLineSource, *, discovery_date: str, rank: int, collected_at: str) -> dict[str, Any]:
    raw_url = _text(item, "url", "link", "id")
    normalized_url, url_status, url_reason = validate_article_url(raw_url, source)
    title = _text(item, "title")
    description = strip_html(_text(item, "description", "summary", "content"))
    publication_date = normalize_publication_date(_text(item, "published_at", "published", "updated", "pubDate"))
    feed_publisher = _text(item, "source", "publisher", "author")
    publisher = source.publisher
    article_hostname = hostname(normalized_url)
    warnings = publisher_warnings(source, feed_publisher, normalized_url)
    status = "reviewer_usable"
    reasons: list[str] = []
    if url_status != "accepted":
        status = "resolution_pending" if url_status == "warning" else "malformed"
        reasons.append(url_reason)
    if not meaningful_title(title):
        status = "malformed"
        reasons.append("missing_article_title")
    if not (publisher or article_hostname):
        status = "malformed"
        reasons.append("missing_publisher")
    if not normalized_url:
        status = "resolution_pending" if raw_url else "malformed"
        reasons.append("missing_article_url")
    if normalized_url and is_wrapper_url(normalized_url):
        status = "wrapper_only"
        reasons.append("generic_wrapper_metadata")
    if not has_description(description):
        status = "resolution_pending" if status == "reviewer_usable" else status
    if not relevant_to_care_line(title, description):
        status = "out_of_scope" if status == "reviewer_usable" else status
        reasons.append("out_of_scope_topic")
    source_payload = {
        "source_id": source.source_id,
        "raw_url": raw_url,
        "title": title,
        "publisher": publisher,
        "publication_date": publication_date,
    }
    record = {
        "schema_version": "bluefern.care_line.discovery_record.v2",
        "discovery_record_id": stable_id("care-line-direct-discovery", normalized_url or raw_url, source.source_id, title, publication_date),
        "discovery_provider": f"direct_{source.adapter_type}",
        "discovery_date": discovery_date,
        "query": "",
        "wrapper_url": "",
        "article_url": normalized_url,
        "article_title": title,
        "article_publisher": publisher,
        "article_publication_date": publication_date,
        "article_description": description,
        "article_hostname": article_hostname,
        "url_resolution_status": "not_needed" if normalized_url else "unresolved",
        "resolved_url": normalized_url,
        "resolved_hostname": article_hostname,
        "resolution_method": "direct_feed_url",
        "resolution_warning": url_reason if url_status != "accepted" else "",
        "raw_provider_metadata": raw_metadata_subset(item, source),
        "record_fingerprint": stable_hash(source_payload),
        "created_at": collected_at,
        "discovery_query": "",
        "discovery_feed_url": source.feed_url,
        "discovery_wrapper_url": "",
        "discovery_collected_at": collected_at,
        "discovery_rank": rank,
        "discovery_raw_id": _text(item, "id", "guid") or stable_id("feed-item", raw_url, title),
        "review_status": "not_started",
        "source_traceability_status": "reviewer_usable" if status == "reviewer_usable" else "quarantined" if status in {"wrapper_only", "malformed"} else "needs_resolution",
        "universal_event_review_status": "not_reviewed",
        "discovery_status": status,
        "status_reasons": sorted(set(reason for reason in reasons if reason)),
        "source_id": source.source_id,
        "source_type": source.source_type,
        "source_name": source.name,
        "source_registry_publisher": source.publisher,
        "feed_reported_publisher": feed_publisher,
        "publisher_warning_codes": warnings,
        "geographic_scope": source.geographic_scope,
        "state": source.state,
        "raw_article_url": raw_url,
        "normalized_article_url": normalized_url,
        "url_validation_status": url_status,
        "url_validation_reason": url_reason,
        "likely_operational_event": likely_operational({"article_title": title, "article_description": description, "article_publisher": publisher}),
    }
    return record


def deduplicate_direct_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_url: dict[str, str] = {}
    seen_source_item: dict[tuple[str, str], str] = {}
    seen_title_date: dict[tuple[str, str, str], str] = {}
    for record in records:
        row = dict(record)
        source_key = (_text(row, "source_id"), _text(row, "discovery_raw_id"))
        title_key = (_text(row, "article_publisher").casefold(), _text(row, "article_title").casefold(), _text(row, "article_publication_date"))
        url = normalize_article_url(_text(row, "article_url"))
        duplicate_of = ""
        if url and url in seen_url:
            duplicate_of = seen_url[url]
        elif all(source_key) and source_key in seen_source_item:
            duplicate_of = seen_source_item[source_key]
        elif all(title_key) and title_key in seen_title_date:
            duplicate_of = seen_title_date[title_key]
        if duplicate_of:
            row["discovery_status"] = "duplicate"
            row["duplicate_of_record_id"] = duplicate_of
            row["source_traceability_status"] = "quarantined"
            row["status_reasons"] = sorted(set([*list(row.get("status_reasons") or []), "duplicate_article"]))
        else:
            if url:
                seen_url[url] = _text(row, "discovery_record_id")
            if all(source_key):
                seen_source_item[source_key] = _text(row, "discovery_record_id")
            if all(title_key):
                seen_title_date[title_key] = _text(row, "discovery_record_id")
        out.append(row)
    return out


def _rss_items(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    items = []
    for node in root.findall(".//item"):
        guid = node.find("guid")
        items.append(
            {
                "title": node.findtext("title") or "",
                "url": node.findtext("link") or "",
                "published_at": node.findtext("pubDate") or node.findtext("published") or "",
                "description": node.findtext("description") or "",
                "source": node.findtext("source") or "",
                "guid": (guid.text or "") if guid is not None else "",
            }
        )
    return items


def _atom_items(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    nodes = root.findall(".//atom:entry", ns) or root.findall(".//entry")
    items = []
    for node in nodes:
        link = ""
        for link_node in node.findall("atom:link", ns) or node.findall("link"):
            rel = link_node.attrib.get("rel", "alternate")
            if rel == "alternate" and link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        items.append(
            {
                "title": node.findtext("atom:title", default="", namespaces=ns) or node.findtext("title") or "",
                "url": link or node.findtext("atom:id", default="", namespaces=ns) or node.findtext("id") or "",
                "published_at": node.findtext("atom:published", default="", namespaces=ns) or node.findtext("atom:updated", default="", namespaces=ns) or "",
                "description": node.findtext("atom:summary", default="", namespaces=ns) or node.findtext("atom:content", default="", namespaces=ns) or "",
                "source": "",
                "id": node.findtext("atom:id", default="", namespaces=ns) or "",
            }
        )
    return items


def _json_feed_items(payload: bytes) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8"))
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "title": _text(item, "title"),
                "url": _text(item, "url", "external_url", "id"),
                "published_at": _text(item, "date_published", "date_modified"),
                "description": strip_html(_text(item, "summary", "content_text", "content_html")),
                "source": _text(data, "title"),
                "id": _text(item, "id"),
            }
        )
    return items


def fetch_source(source: CareLineSource, *, timeout: int = 20, allow_insecure_tls: bool = False) -> tuple[bytes, dict[str, Any]]:
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    request = urllib.request.Request(source.feed_url, headers={"User-Agent": "BlueFernCareLineDirectDiscovery/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        return response.read(), {"http_status": getattr(response, "status", 0) or 0}


def parse_source_items(source: CareLineSource, payload: bytes) -> list[dict[str, Any]]:
    parser = ADAPTER_PARSERS.get(source.adapter_type)
    if parser is None:
        raise ValueError(f"unsupported adapter: {source.adapter_type}")
    return parser(payload)


def collect_from_sources(
    sources: Iterable[CareLineSource],
    *,
    discovery_date: str,
    date_from: str = "",
    date_to: str = "",
    max_records: int = 200,
    per_source_limit: int = 25,
    allow_insecure_tls: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    health_rows: list[dict[str, Any]] = []
    collected_at = utc_now()
    for source in sources:
        if len(records) >= max_records:
            break
        health: dict[str, Any] = {
            "schema_version": SOURCE_HEALTH_SCHEMA_VERSION,
            "source_id": source.source_id,
            "source_name": source.name,
            "source_type": source.source_type,
            "state": source.state,
            "adapter_type": source.adapter_type,
            "publisher": source.publisher,
            "fetch_status": "failed",
            "http_status": 0,
            "parse_status": "not_started",
            "records_returned": 0,
            "new_records": 0,
            "duplicate_records": 0,
            "records_with_urls": 0,
            "records_with_titles": 0,
            "records_with_dates": 0,
            "records_with_descriptions": 0,
            "last_successful_fetch": "",
            "last_error": "",
        }
        try:
            payload, fetch_meta = fetch_source(source, allow_insecure_tls=allow_insecure_tls)
            health.update(fetch_meta)
            health["fetch_status"] = "ok"
            items = parse_source_items(source, payload)
            health["parse_status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            health["last_error"] = f"{type(exc).__name__}: {exc}"
            health_rows.append(health)
            continue
        source_records = []
        for rank, item in enumerate(items[:per_source_limit], start=1):
            row = discovery_record_from_direct_item(item, source, discovery_date=discovery_date, rank=rank, collected_at=collected_at)
            if date_from or date_to:
                if not _within_date_window(row.get("article_publication_date") or "", date_from, date_to):
                    continue
            source_records.append(row)
        health["records_returned"] = len(source_records)
        health["records_with_urls"] = sum(1 for row in source_records if row.get("article_url"))
        health["records_with_titles"] = sum(1 for row in source_records if meaningful_title(_text(row, "article_title")))
        health["records_with_dates"] = sum(1 for row in source_records if _text(row, "article_publication_date"))
        health["records_with_descriptions"] = sum(1 for row in source_records if has_description(_text(row, "article_description")))
        health["reviewer_usable_count"] = sum(1 for row in source_records if row.get("discovery_status") == "reviewer_usable")
        health["likely_operational_event_count"] = sum(1 for row in source_records if row.get("likely_operational_event"))
        health["last_successful_fetch"] = collected_at
        available = max_records - len(records)
        records.extend(source_records[:available])
        health["new_records"] = min(len(source_records), available)
        health_rows.append(health)
    deduped = deduplicate_direct_records(records)
    duplicate_ids = {row.get("discovery_record_id") for row in deduped if row.get("discovery_status") == "duplicate"}
    for health in health_rows:
        health["duplicate_records"] = sum(1 for row in deduped if row.get("source_id") == health["source_id"] and row.get("discovery_record_id") in duplicate_ids)
    return deduped, health_rows


def direct_quality_report(records: Iterable[Mapping[str, Any]], health_rows: Iterable[Mapping[str, Any]], *, thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    health = [dict(row) for row in health_rows]
    count = len(rows)
    statuses = Counter(_text(row, "discovery_status") for row in rows)
    article_urls = {normalize_article_url(_text(row, "article_url")) for row in rows if _text(row, "article_url")}
    publishers = {(_text(row, "article_publisher") or _text(row, "article_hostname")).casefold() for row in rows if _text(row, "article_publisher") or _text(row, "article_hostname")}
    limits = dict(thresholds or DIRECT_QUALITY_THRESHOLDS)
    report = {
        "schema_version": DIRECT_QUALITY_SCHEMA_VERSION,
        "operator_version": DIRECT_DISCOVERY_OPERATOR_VERSION,
        "records_collected": count,
        "sources_attempted": len(health),
        "source_success_count": sum(1 for row in health if row.get("fetch_status") == "ok" and row.get("parse_status") == "ok"),
        "source_failure_count": sum(1 for row in health if row.get("fetch_status") != "ok" or row.get("parse_status") != "ok"),
        "reviewer_usable_count": statuses.get("reviewer_usable", 0),
        "resolution_pending_count": statuses.get("resolution_pending", 0),
        "wrapper_only_count": statuses.get("wrapper_only", 0),
        "malformed_count": statuses.get("malformed", 0),
        "duplicate_count": statuses.get("duplicate", 0),
        "out_of_scope_count": statuses.get("out_of_scope", 0),
        "article_url_count": sum(1 for row in rows if _text(row, "article_url")),
        "distinct_article_url_count": len(article_urls),
        "distinct_publisher_or_hostname_count": len(publishers),
        "meaningful_title_count": sum(1 for row in rows if meaningful_title(_text(row, "article_title"))),
        "publication_date_count": sum(1 for row in rows if _text(row, "article_publication_date")),
        "description_count": sum(1 for row in rows if has_description(_text(row, "article_description"))),
        "likely_operational_event_count": sum(1 for row in rows if row.get("likely_operational_event")),
        "article_url_present_rate": round(sum(1 for row in rows if _text(row, "article_url")) / max(1, count), 4),
        "meaningful_title_rate": round(sum(1 for row in rows if meaningful_title(_text(row, "article_title"))) / max(1, count), 4),
        "publisher_present_rate": round(sum(1 for row in rows if _text(row, "article_publisher") or _text(row, "article_hostname")) / max(1, count), 4),
        "publication_date_present_rate": round(sum(1 for row in rows if _text(row, "article_publication_date")) / max(1, count), 4),
        "description_present_rate": round(sum(1 for row in rows if has_description(_text(row, "article_description"))) / max(1, count), 4),
        "wrapper_only_rate": round(statuses.get("wrapper_only", 0) / max(1, count), 4),
        "malformed_rate": round(statuses.get("malformed", 0) / max(1, count), 4),
        "status_counts": dict(sorted(statuses.items())),
        "thresholds": limits,
    }
    failures = []
    if report["reviewer_usable_count"] < limits["reviewer_usable_count"]:
        failures.append("reviewer_usable_count")
    if report["article_url_present_rate"] < limits["article_url_present_rate"]:
        failures.append("article_url_present_rate")
    if report["meaningful_title_rate"] < limits["meaningful_title_rate"]:
        failures.append("meaningful_title_rate")
    if report["publisher_present_rate"] < limits["publisher_present_rate"]:
        failures.append("publisher_present_rate")
    if report["publication_date_present_rate"] < limits["publication_date_present_rate"]:
        failures.append("publication_date_present_rate")
    if report["description_present_rate"] < limits["description_present_rate"]:
        failures.append("description_present_rate")
    if report["wrapper_only_rate"] > limits["wrapper_only_rate_max"]:
        failures.append("wrapper_only_rate")
    if report["malformed_rate"] > limits["malformed_rate_max"]:
        failures.append("malformed_rate")
    if report["distinct_article_url_count"] < limits["distinct_article_url_count"]:
        failures.append("distinct_article_url_count")
    if report["distinct_publisher_or_hostname_count"] < limits["distinct_publisher_or_hostname_count"]:
        failures.append("distinct_publisher_or_hostname_count")
    if report["likely_operational_event_count"] < limits["likely_operational_event_count"]:
        failures.append("likely_operational_event_count")
    report["quality_gate_passed"] = not failures
    report["failing_metrics"] = failures
    failing_source_ids = {row["source_id"] for row in health if row.get("fetch_status") != "ok" or row.get("parse_status") != "ok"}
    if not failing_source_ids and failures:
        failing_source_ids = {row["source_id"] for row in health if row.get("records_returned", 0) == 0 or row.get("reviewer_usable_count", 0) == 0}
    report["failing_source_ids"] = sorted(failing_source_ids)
    return report


def source_health_report(health_rows: Iterable[Mapping[str, Any]], quality: Mapping[str, Any]) -> dict[str, Any]:
    rows = [dict(row) for row in health_rows]
    by_source = []
    by_state = Counter()
    by_source_type = Counter()
    by_adapter = Counter()
    by_publisher = Counter()
    for row in rows:
        by_state[row.get("state") or ""] += int(row.get("records_returned") or 0)
        by_source_type[row.get("source_type") or ""] += int(row.get("records_returned") or 0)
        by_adapter[row.get("adapter_type") or ""] += int(row.get("records_returned") or 0)
        by_publisher[row.get("publisher") or ""] += int(row.get("records_returned") or 0)
        article_url_rate = round((int(row.get("records_with_urls") or 0) / max(1, int(row.get("records_returned") or 0))), 4)
        date_rate = round((int(row.get("records_with_dates") or 0) / max(1, int(row.get("records_returned") or 0))), 4)
        description_rate = round((int(row.get("records_with_descriptions") or 0) / max(1, int(row.get("records_returned") or 0))), 4)
        by_source.append(
            {
                "source_id": row.get("source_id", ""),
                "source_name": row.get("source_name", ""),
                "source_type": row.get("source_type", ""),
                "state": row.get("state", ""),
                "adapter_type": row.get("adapter_type", ""),
                "publisher": row.get("publisher", ""),
                "fetch_status": row.get("fetch_status", ""),
                "parse_status": row.get("parse_status", ""),
                "records_returned": row.get("records_returned", 0),
                "new_records": row.get("new_records", 0),
                "duplicate_records": row.get("duplicate_records", 0),
                "records_with_urls": row.get("records_with_urls", 0),
                "records_with_dates": row.get("records_with_dates", 0),
                "records_with_descriptions": row.get("records_with_descriptions", 0),
                "likely_operational_event_count": row.get("likely_operational_event_count", 0),
                "article_url_rate": article_url_rate,
                "date_rate": date_rate,
                "description_rate": description_rate,
                "relevance_rate": round((int(row.get("likely_operational_event_count") or 0) / max(1, int(row.get("records_returned") or 0))), 4),
                "last_successful_fetch": row.get("last_successful_fetch", ""),
                "last_error": row.get("last_error", ""),
            }
        )
    return {
        "schema_version": SOURCE_HEALTH_SCHEMA_VERSION,
        "records": by_source,
        "summary": {
            "source_count": len(rows),
            "state_counts": dict(sorted(by_state.items())),
            "source_type_counts": dict(sorted(by_source_type.items())),
            "adapter_counts": dict(sorted(by_adapter.items())),
            "publisher_counts": dict(sorted(by_publisher.items())),
            "quality_gate_passed": bool(quality.get("quality_gate_passed")),
        },
    }


def write_outputs(records: list[dict[str, Any]], quality: Mapping[str, Any], health_rows: list[dict[str, Any]], output_dir: Path, *, strict: bool) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "direct_discovered_sources.json"
    quality_path = output_dir / "direct_discovery_quality.json"
    diagnostics_path = output_dir / "direct_discovery_diagnostics.json"
    _write_json_atomic(quality_path, quality)
    _write_json_atomic(
        diagnostics_path,
        {
            "schema_version": SOURCE_HEALTH_SCHEMA_VERSION,
            "source_health": health_rows,
            "quality_gate_passed": quality.get("quality_gate_passed"),
        },
    )
    if quality.get("quality_gate_passed") or not strict:
        _write_json_atomic(records_path, {"schema_version": DISCOVERY_COLLECTION_SCHEMA_VERSION, "record_count": len(records), "records": records})
    return {"records": str(records_path), "quality": str(quality_path), "diagnostics": str(diagnostics_path)}


def _write_json_atomic(path: Path, payload: Any) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        tmp = Path(handle.name)
    tmp.replace(path)


def source_health_markdown(health_rows: Iterable[Mapping[str, Any]], quality: Mapping[str, Any]) -> str:
    lines = [
        "# Care Line Source Health Phase 13",
        "",
        f"- Sources attempted: `{quality.get('sources_attempted')}`",
        f"- Successes: `{quality.get('source_success_count')}`",
        f"- Failures: `{quality.get('source_failure_count')}`",
        "",
        "| source_id | type | state | status | records | URLs | dates | descriptions | usable | error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in health_rows:
        status = f"{row.get('fetch_status')}/{row.get('parse_status')}"
        lines.append(
            f"| {row.get('source_id')} | {row.get('source_type')} | {row.get('state')} | {status} | {row.get('records_returned')} | {row.get('records_with_urls')} | {row.get('records_with_dates')} | {row.get('records_with_descriptions')} | {row.get('reviewer_usable_count', 0)} | {row.get('last_error', '')} |"
        )
    return "\n".join(lines) + "\n"


def filter_sources(
    sources: Iterable[CareLineSource],
    *,
    source_ids: set[str] | None = None,
    states: set[str] | None = None,
    source_types: set[str] | None = None,
) -> list[CareLineSource]:
    out = []
    for source in sources:
        if source_ids and source.source_id not in source_ids:
            continue
        if states and source.state not in states:
            continue
        if source_types and source.source_type not in source_types:
            continue
        out.append(source)
    return out


def run_direct_discovery(
    *,
    repo_root: Path,
    registry_path: Path,
    discovery_date: str,
    date_from: str = "",
    date_to: str = "",
    output_root: Path,
    review_dir: Path,
    max_records: int = 200,
    strict: bool = False,
    check_only: bool = False,
    include_disabled: bool = False,
    source_ids: set[str] | None = None,
    states: set[str] | None = None,
    source_types: set[str] | None = None,
    allow_insecure_tls: bool = False,
    quality_report_path: Path | None = None,
    diagnostics_report_path: Path | None = None,
    source_health_report_path: Path | None = None,
    quality_threshold_overrides: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    refuse_public_or_pages_path(output_root if output_root.is_absolute() else repo_root / output_root, repo_root)
    refuse_public_or_pages_path(review_dir if review_dir.is_absolute() else repo_root / review_dir, repo_root)
    registry = load_registry(registry_path if registry_path.is_absolute() else repo_root / registry_path, include_disabled=include_disabled)
    sources = filter_sources(registry.sources, source_ids=source_ids, states=states, source_types=source_types)
    records, health = collect_from_sources(
        sources,
        discovery_date=discovery_date,
        date_from=date_from,
        date_to=date_to,
        max_records=max_records,
        allow_insecure_tls=allow_insecure_tls,
    )
    quality = direct_quality_report(records, health, thresholds=quality_threshold_overrides)
    report = source_health_report(health, quality)
    output_tag = date_to or discovery_date
    output_dir = (output_root if output_root.is_absolute() else repo_root / output_root) / output_tag
    paths: dict[str, str] = {}
    packet_paths: dict[str, str] = {}
    if not check_only:
        paths = write_outputs(records, quality, health, output_dir, strict=strict)
        docs_dir = repo_root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        health_by_source = {row["source_id"]: row for row in health}
        (docs_dir / "care-line-direct-source-registry-phase13.md").write_text(registry_markdown(registry, health_by_source), encoding="utf-8")
        (docs_dir / "care-line-source-health-phase13.md").write_text(source_health_markdown(health, quality), encoding="utf-8")
        if quality_report_path:
            _write_json_atomic(quality_report_path if quality_report_path.is_absolute() else repo_root / quality_report_path, quality)
        if diagnostics_report_path:
            _write_json_atomic(diagnostics_report_path if diagnostics_report_path.is_absolute() else repo_root / diagnostics_report_path, report)
        if source_health_report_path:
            _write_json_atomic(source_health_report_path if source_health_report_path.is_absolute() else repo_root / source_health_report_path, report)
        if quality["quality_gate_passed"]:
            packet = reviewer_packet_from_records(records, sample_id=f"care_line_phase13_{discovery_date}_direct_sources")
            packet_paths = write_reviewer_packet(packet, review_dir if review_dir.is_absolute() else repo_root / review_dir)
    summary = {
        "schema_version": DIRECT_QUALITY_SCHEMA_VERSION,
        "operator_version": DIRECT_DISCOVERY_OPERATOR_VERSION,
        "discovery_date": discovery_date,
        "date_from": date_from,
        "date_to": date_to,
        "source_count": len(sources),
        "quality": quality,
        "paths": paths,
        "review_packet": {"record_count": len([row for row in records if row.get("discovery_status") == "reviewer_usable"]), "paths": packet_paths},
        "source_health": health,
        "source_health_report": report,
    }
    if strict and not quality["quality_gate_passed"]:
        raise SystemExit(2)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Care Line direct-source discovery records.")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--date", default="")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--output-root", default="data/dispatches/care-line/sources")
    parser.add_argument("--review-dir", default="data/universal_events/shadow/care-line/phase13-review")
    parser.add_argument("--quality-report", default="")
    parser.add_argument("--diagnostics-report", default="")
    parser.add_argument("--source-health-report", default="")
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--state", action="append", default=[])
    parser.add_argument("--source-type", action="append", default=[])
    parser.add_argument("--quality-threshold", action="append", default=[])
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--allow-insecure-tls", action="store_true")
    args = parser.parse_args(argv)
    date_value = args.date or args.date_to or args.date_from
    if not date_value:
        raise SystemExit("--date or --date-from/--date-to is required")
    threshold_overrides: dict[str, float] = {}
    for item in args.quality_threshold:
        if "=" not in item:
            raise SystemExit("--quality-threshold must be key=value")
        key, value = item.split("=", 1)
        threshold_overrides[key.strip()] = float(value)
    try:
        result = run_direct_discovery(
            repo_root=Path.cwd(),
            registry_path=Path(args.registry),
            discovery_date=date_value,
            date_from=args.date_from,
            date_to=args.date_to,
            output_root=Path(args.output_root),
            review_dir=Path(args.review_dir),
            max_records=args.max_records,
            strict=args.strict,
            check_only=args.check_only,
            include_disabled=args.include_disabled,
            source_ids=set(args.source_id) or None,
            states=set(args.state) or None,
            source_types=set(args.source_type) or None,
            allow_insecure_tls=args.allow_insecure_tls,
            quality_report_path=Path(args.quality_report) if args.quality_report else None,
            diagnostics_report_path=Path(args.diagnostics_report) if args.diagnostics_report else None,
            source_health_report_path=Path(args.source_health_report) if args.source_health_report else None,
            quality_threshold_overrides=threshold_overrides or None,
        )
    except SystemExit as exc:
        return int(exc.code)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
