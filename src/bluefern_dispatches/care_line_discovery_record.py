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
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_authoritative_intake import INTAKE_SCHEMA_VERSION
from bluefern_dispatches.care_line_reviewed_export import refuse_public_or_pages_path
from bluefern_dispatches.care_line_source_recovery import is_wrapper_url


DISCOVERY_RECORD_V2_SCHEMA_VERSION = "bluefern.care_line.discovery_record.v2"
DISCOVERY_COLLECTION_SCHEMA_VERSION = "bluefern.care_line.discovery_collection.v2"
DISCOVERY_QUALITY_SCHEMA_VERSION = "bluefern.care_line.discovery_quality.v2"
PHASE12_REVIEW_PACKET_SCHEMA_VERSION = "bluefern.care_line.phase12_authoritative_review_packet.v1"
PHASE12_LEGACY_ASSESSMENT_SCHEMA_VERSION = "bluefern.care_line.phase12_legacy_discovery_assessment.v1"

DISCOVERY_STATUSES = {
    "reviewer_usable",
    "resolution_pending",
    "wrapper_only",
    "malformed",
    "duplicate",
    "stale",
    "out_of_scope",
}
REASON_CODES = {
    "missing_article_title",
    "missing_publisher",
    "missing_article_url",
    "generic_wrapper_metadata",
    "unresolved_redirect",
    "invalid_provider_payload",
    "duplicate_article",
    "stale_publication_date",
    "out_of_scope_topic",
}
TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "msclkid",
}
GENERIC_GOOGLE_TEXT = {"", "google news", "news.google.com", "google"}
OPERATIONAL_TERMS = (
    "closing",
    "closure",
    "closes",
    "closed",
    "shutting down",
    "cuts services",
    "cut services",
    "reduced hours",
    "suspend",
    "suspension",
    "ending labor",
    "labor and delivery",
    "maternity",
    "emergency department",
    "er diversion",
    "diverting ambulances",
    "appointment backlog",
    "no longer accepting",
    "staffing shortage",
    "pharmacy closure",
    "ambulance delay",
)
DEFAULT_GOOGLE_NEWS_QUERIES = [
    '"hospital closure" "patients"',
    '"clinic closure" "patients"',
    '"maternity ward closing"',
    '"ER diversion" hospital',
    '"pharmacy closure" prescription access',
]
REVIEW_PACKET_FIELDS = [
    "discovery_record_id",
    "discovery_date",
    "source_payload_fingerprint",
    "proposal_fingerprint",
    "article_title",
    "article_publisher",
    "article_publication_date",
    "article_url",
    "article_hostname",
    "article_description",
    "discovery_provider",
    "discovery_query",
    "discovery_feed_url",
    "discovery_wrapper_url",
    "schema_version",
    "intake_record_id",
    "canonical_source_url",
    "source_title",
    "publisher",
    "publication_date",
    "source_type",
    "source_role",
    "supporting_passage",
    "event_type",
    "service_line",
    "facility_name",
    "provider_name",
    "parent_organization",
    "operator_name",
    "former_owner",
    "new_owner",
    "facility_type",
    "address_line_1",
    "address_line_2",
    "city",
    "county",
    "state",
    "postal_code",
    "country_code",
    "announcement_date",
    "effective_date",
    "date_precision",
    "permanence",
    "evidence_level",
    "evidence_strength",
    "is_primary_source",
    "care_line_public_eligible",
    "universal_event_eligible",
    "duplicate_of_record_id",
    "supersedes_intake_record_id",
    "withdrawal_status",
    "reviewer",
    "review_reason",
    "review_notes",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{stable_hash(parts)[:16]}"


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def hostname(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_google_news_url(url: str) -> bool:
    host = hostname(url)
    path = urllib.parse.urlsplit(url).path.lower()
    return host in {"news.google.com", "google.com", "www.google.com"} and (
        "/rss/articles/" in path or "/read/" in path or host == "news.google.com"
    )


def normalize_article_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_QUERY_PARAMS],
        doseq=True,
    )
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def generic_google_text(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return normalized in GENERIC_GOOGLE_TEXT


def meaningful_title(value: str) -> bool:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return len(title) >= 12 and not generic_google_text(title) and "google news" not in title.casefold()


def has_description(value: str) -> bool:
    text = _strip_html(value)
    return len(text) >= 25 and not generic_google_text(text)


def likely_operational(record: Mapping[str, Any]) -> bool:
    blob = " ".join(
        _text(record, key).casefold()
        for key in ("article_title", "article_description", "raw_provider_summary", "article_publisher")
    )
    return any(term in blob for term in OPERATIONAL_TERMS)


def split_google_news_title(title: str, reported_source: str = "") -> tuple[str, str]:
    raw = re.sub(r"\s+", " ", str(title or "")).strip()
    if not raw or generic_google_text(raw):
        return "", reported_source.strip()
    if reported_source and raw.endswith(f" - {reported_source}"):
        return raw[: -len(f" - {reported_source}")].strip(), reported_source.strip()
    if " - " in raw:
        headline, publisher = raw.rsplit(" - ", 1)
        return headline.strip(), (reported_source or publisher).strip()
    return raw, reported_source.strip()


def raw_metadata_subset(item: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "title",
        "link",
        "url",
        "source",
        "publisher",
        "published",
        "published_at",
        "pubDate",
        "summary",
        "description",
        "id",
        "guid",
        "query",
        "feed_url",
        "article_url",
        "canonical_url",
        "source_url",
    )
    subset: dict[str, Any] = {}
    for key in allowed:
        if key in item:
            value = item[key]
            if isinstance(value, str):
                subset[key] = value[:2000]
            elif isinstance(value, Mapping):
                subset[key] = {
                    str(k): (str(v)[:1000] if not isinstance(v, (dict, list)) else str(v)[:1000])
                    for k, v in value.items()
                    if str(k).lower() not in {"token", "secret", "password", "authorization"}
                }
            else:
                subset[key] = value
    return subset


def _source_title(item: Mapping[str, Any]) -> str:
    source = item.get("source")
    if isinstance(source, Mapping):
        return _text(source, "title", "name")
    return _text(item, "source", "publisher")


def _published(item: Mapping[str, Any]) -> str:
    return _text(item, "article_publication_date", "published_at", "published", "pubDate")


def classify_discovery_record(record: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not (_text(record, "raw_provider_metadata") or _text(record, "wrapper_url") or _text(record, "article_url")):
        return "malformed", ["invalid_provider_payload"]
    if not meaningful_title(_text(record, "article_title")):
        reasons.append("missing_article_title")
    if not (_text(record, "article_publisher") or _text(record, "article_hostname")):
        reasons.append("missing_publisher")
    if not _text(record, "article_url"):
        reasons.append("missing_article_url")
    if _text(record, "url_resolution_status") == "unresolved":
        reasons.append("unresolved_redirect")
    if (
        generic_google_text(_text(record, "article_title"))
        or generic_google_text(_text(record, "article_publisher"))
        or generic_google_text(_text(record, "article_description"))
    ):
        reasons.append("generic_wrapper_metadata")
    reasons = sorted(set(reason for reason in reasons if reason in REASON_CODES))
    if "generic_wrapper_metadata" in reasons and "missing_article_url" in reasons:
        return "wrapper_only", reasons
    if _text(record, "wrapper_url") and not _text(record, "article_url"):
        return "resolution_pending" if meaningful_title(_text(record, "article_title")) and _text(record, "article_publisher") else "wrapper_only", reasons
    if reasons:
        return "malformed" if "invalid_provider_payload" in reasons else "resolution_pending"
    if not has_description(_text(record, "article_description")):
        return "resolution_pending", ["unresolved_redirect"] if _text(record, "wrapper_url") else []
    return "reviewer_usable", []


def record_from_provider_item(
    item: Mapping[str, Any],
    *,
    discovery_date: str,
    provider: str = "google_news",
    query: str = "",
    feed_url: str = "",
    rank: int = 0,
    collected_at: str = "",
) -> dict[str, Any]:
    collected_at = collected_at or utc_now()
    raw_title = _text(item, "title")
    raw_link = _text(item, "link", "url")
    raw_source = _source_title(item)
    description = _strip_html(_text(item, "article_description", "description", "summary", "summary_or_snippet"))
    explicit_article_url = normalize_article_url(_text(item, "article_url", "canonical_url", "source_url"))
    link_url = normalize_article_url(raw_link)
    wrapper_url = link_url if is_google_news_url(link_url) or is_wrapper_url(link_url) else ""
    article_url = explicit_article_url or ("" if wrapper_url else link_url)
    if provider == "google_news":
        article_title, article_publisher = split_google_news_title(_text(item, "article_title") or raw_title, _text(item, "article_publisher") or raw_source)
    else:
        article_title = _text(item, "article_title") or raw_title
        article_publisher = _text(item, "article_publisher", "publisher") or raw_source
    if generic_google_text(article_publisher) and raw_source and not generic_google_text(raw_source):
        article_publisher = raw_source
    article_hostname = hostname(article_url)
    if article_hostname and not article_publisher:
        article_publisher = article_hostname
    if article_url:
        resolution_status = "resolved" if wrapper_url else "not_needed"
        resolution_method = "provider_article_url" if explicit_article_url else "provider_direct_url"
    elif wrapper_url:
        resolution_status = "unresolved"
        resolution_method = "none"
    else:
        resolution_status = "invalid"
        resolution_method = "none"
    fingerprint_payload = {
        "provider": provider,
        "query": query,
        "raw_title": raw_title,
        "raw_link": raw_link,
        "raw_source": raw_source,
        "published": _published(item),
    }
    record_id = stable_id("care-line-discovery-v2", article_url or raw_link, article_title, article_publisher, _published(item))
    record: dict[str, Any] = {
        "schema_version": DISCOVERY_RECORD_V2_SCHEMA_VERSION,
        "discovery_record_id": record_id,
        "discovery_provider": provider,
        "discovery_date": discovery_date,
        "query": query,
        "wrapper_url": wrapper_url,
        "article_url": article_url,
        "article_title": article_title,
        "article_publisher": "" if generic_google_text(article_publisher) else article_publisher,
        "article_publication_date": _published(item),
        "article_description": "" if generic_google_text(description) else description,
        "article_hostname": article_hostname,
        "url_resolution_status": resolution_status,
        "resolved_url": article_url,
        "resolved_hostname": article_hostname,
        "resolution_method": resolution_method,
        "resolution_warning": "google_news_wrapper_unresolved" if wrapper_url and not article_url else "",
        "raw_provider_metadata": raw_metadata_subset({**dict(item), "query": query, "feed_url": feed_url}),
        "record_fingerprint": stable_hash(fingerprint_payload),
        "created_at": collected_at,
        "discovery_query": query,
        "discovery_feed_url": feed_url,
        "discovery_wrapper_url": wrapper_url,
        "discovery_collected_at": collected_at,
        "discovery_rank": rank,
        "discovery_raw_id": _text(item, "id", "guid"),
        "review_status": "not_started",
        "source_traceability_status": "",
        "universal_event_review_status": "not_reviewed",
        "google_news_entry_title": raw_title if provider == "google_news" else "",
        "google_news_reported_source": raw_source if provider == "google_news" else "",
        "google_news_published_date": _published(item) if provider == "google_news" else "",
        "google_news_wrapper_url": wrapper_url if provider == "google_news" else "",
    }
    status, reasons = classify_discovery_record(record)
    record["discovery_status"] = status
    record["status_reasons"] = reasons
    record["source_traceability_status"] = "reviewer_usable" if status == "reviewer_usable" else "quarantined" if status in {"wrapper_only", "malformed"} else "needs_resolution"
    record["likely_operational_event"] = likely_operational(record)
    return record


def deduplicate_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_articles: dict[str, str] = {}
    for record in records:
        row = dict(record)
        article_url = normalize_article_url(_text(row, "article_url"))
        if article_url and article_url in seen_articles:
            row["discovery_status"] = "duplicate"
            row["duplicate_of_record_id"] = seen_articles[article_url]
            row["status_reasons"] = sorted(set([*list(row.get("status_reasons") or []), "duplicate_article"]))
            row["source_traceability_status"] = "quarantined"
        elif article_url:
            seen_articles[article_url] = _text(row, "discovery_record_id")
        out.append(row)
    return out


def quality_report(records: Iterable[Mapping[str, Any]], *, thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    count = len(rows)
    statuses = Counter(_text(row, "discovery_status") for row in rows)
    distinct_publishers = {
        (_text(row, "article_publisher") or _text(row, "article_hostname")).casefold()
        for row in rows
        if _text(row, "article_publisher") or _text(row, "article_hostname")
    }
    metrics = {
        "schema_version": DISCOVERY_QUALITY_SCHEMA_VERSION,
        "records_collected": count,
        "reviewer_usable_count": statuses.get("reviewer_usable", 0),
        "resolution_pending_count": statuses.get("resolution_pending", 0),
        "wrapper_only_count": statuses.get("wrapper_only", 0),
        "malformed_count": statuses.get("malformed", 0),
        "duplicate_count": statuses.get("duplicate", 0),
        "stale_count": statuses.get("stale", 0),
        "out_of_scope_count": statuses.get("out_of_scope", 0),
        "usable_rate": round(statuses.get("reviewer_usable", 0) / max(1, count), 4),
        "wrapper_only_rate": round(statuses.get("wrapper_only", 0) / max(1, count), 4),
        "publisher_present_rate": round(sum(1 for row in rows if _text(row, "article_publisher") or _text(row, "article_hostname")) / max(1, count), 4),
        "article_url_present_rate": round(sum(1 for row in rows if _text(row, "article_url")) / max(1, count), 4),
        "meaningful_title_rate": round(sum(1 for row in rows if meaningful_title(_text(row, "article_title"))) / max(1, count), 4),
        "publication_date_present_rate": round(sum(1 for row in rows if _text(row, "article_publication_date")) / max(1, count), 4),
        "description_present_rate": round(sum(1 for row in rows if has_description(_text(row, "article_description"))) / max(1, count), 4),
        "distinct_publisher_or_hostname_count": len(distinct_publishers),
        "article_url_count": sum(1 for row in rows if _text(row, "article_url")),
        "meaningful_title_count": sum(1 for row in rows if meaningful_title(_text(row, "article_title"))),
        "description_count": sum(1 for row in rows if has_description(_text(row, "article_description"))),
        "likely_operational_event_count": sum(1 for row in rows if likely_operational(row)),
        "status_counts": dict(sorted(statuses.items())),
        "thresholds": dict(thresholds or default_quality_thresholds()),
    }
    metrics["quality_gate_passed"] = passes_quality_gate(metrics, thresholds=metrics["thresholds"])
    return metrics


def default_quality_thresholds() -> dict[str, float]:
    return {
        "reviewer_usable_count": 1,
        "usable_rate": 0.70,
        "wrapper_only_rate_max": 0.20,
        "meaningful_title_rate": 0.90,
        "publisher_present_rate": 0.90,
        "article_url_present_rate": 0.80,
    }


def passes_quality_gate(report: Mapping[str, Any], *, thresholds: Mapping[str, float] | None = None) -> bool:
    limits = dict(thresholds or default_quality_thresholds())
    return (
        int(report.get("reviewer_usable_count") or 0) >= int(limits.get("reviewer_usable_count", 1))
        and float(report.get("usable_rate") or 0.0) >= float(limits.get("usable_rate", 0.70))
        and float(report.get("wrapper_only_rate") or 0.0) <= float(limits.get("wrapper_only_rate_max", 0.20))
        and float(report.get("meaningful_title_rate") or 0.0) >= float(limits.get("meaningful_title_rate", 0.90))
        and float(report.get("publisher_present_rate") or 0.0) >= float(limits.get("publisher_present_rate", 0.90))
        and float(report.get("article_url_present_rate") or 0.0) >= float(limits.get("article_url_present_rate", 0.80))
    )


def _write_atomic_json(path: Path, payload: Any) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        tmp = Path(handle.name)
    tmp.replace(path)


def write_discovery_v2(
    records: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    strict: bool = False,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = deduplicate_records(records)
    report = quality_report(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_payload = {
        "schema_version": DISCOVERY_QUALITY_SCHEMA_VERSION,
        "quality_gate_passed": report["quality_gate_passed"],
        "records": rows,
        "diagnostics": dict(diagnostics or {}),
    }
    quality_path = output_dir / "discovery_quality.json"
    diagnostics_path = output_dir / "discovery_diagnostics.json"
    records_path = output_dir / "discovered_sources_v2.json"
    _write_atomic_json(quality_path, report)
    _write_atomic_json(diagnostics_path, diagnostics_payload)
    if report["quality_gate_passed"] or not strict:
        _write_atomic_json(
            records_path,
            {
                "schema_version": DISCOVERY_COLLECTION_SCHEMA_VERSION,
                "record_count": len(rows),
                "records": rows,
            },
        )
    return {"records_path": str(records_path), "quality_path": str(quality_path), "diagnostics_path": str(diagnostics_path), "quality": report}


def load_discovery_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("schema_version") == DISCOVERY_COLLECTION_SCHEMA_VERSION:
        return [dict(row) for row in payload.get("records") or [] if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [record_from_legacy(row, discovery_file=path) for row in payload["records"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [record_from_legacy(row, discovery_file=path) for row in payload if isinstance(row, dict)]
    return []


def record_from_legacy(row: Mapping[str, Any], *, discovery_file: Path) -> dict[str, Any]:
    date = discovery_file.parent.name if discovery_file.parent else ""
    record = record_from_provider_item(
        {
            "title": _text(row, "title"),
            "link": _text(row, "url", "canonical_url", "source_url"),
            "publisher": _text(row, "publisher", "source_name"),
            "published_at": _text(row, "published_at", "source_published_date"),
            "description": _text(row, "summary_or_snippet", "evidence_text"),
            "id": _text(row, "source_record_id", "source_id"),
        },
        discovery_date=date,
        provider="legacy_discovered_sources",
        query="",
        feed_url=str(discovery_file),
        rank=0,
        collected_at=_text(row, "retrieved_at") or utc_now(),
    )
    if is_wrapper_url(_text(row, "url", "canonical_url", "source_url")):
        record["discovery_status"] = "legacy_wrapper_only"
        record["source_traceability_status"] = "quarantined"
        record["status_reasons"] = ["generic_wrapper_metadata", "missing_article_url", "unresolved_redirect"]
    return record


def legacy_wrapper_assessment(repo_root: Path, *, date_from: str, date_to: str) -> dict[str, Any]:
    source_root = repo_root / "data" / "dispatches" / "care-line" / "sources"
    rows: list[dict[str, Any]] = []
    for path in sorted(source_root.glob("*/discovered_sources.json")):
        if not (date_from <= path.parent.name <= date_to):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload if isinstance(payload, list) else payload.get("records", []):
            if not isinstance(item, dict):
                continue
            wrapper = _text(item, "url", "canonical_url", "source_url")
            if is_wrapper_url(wrapper):
                new_status = "legacy_wrapper_only"
                reason = "google_news_wrapper_without_canonical_article_identity"
                recoverability = "unrecoverable_without_reviewer_or_fresh_collection"
            else:
                new_status = "legacy_non_wrapper"
                reason = "not_a_google_news_wrapper"
                recoverability = "assess_individually"
            rows.append(
                {
                    "record_id": _text(item, "source_record_id", "source_id") or stable_id("legacy-care-line", wrapper, _text(item, "title")),
                    "date": path.parent.name,
                    "wrapper_url": wrapper,
                    "original_status": _text(item, "recovery_status", "classification", "freshness_status"),
                    "new_status": new_status,
                    "reason": reason,
                    "recoverability": recoverability,
                    "replacement_record_id": "",
                }
            )
    counts = Counter(row["new_status"] for row in rows)
    return {
        "schema_version": PHASE12_LEGACY_ASSESSMENT_SCHEMA_VERSION,
        "date_from": date_from,
        "date_to": date_to,
        "record_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "records": rows,
    }


def reviewer_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for record in records:
        row = dict(record)
        if row.get("discovery_status") != "reviewer_usable":
            continue
        article_url = _text(row, "article_url")
        if not article_url or is_wrapper_url(article_url):
            continue
        fingerprint = stable_hash(
            {
                "discovery_record_id": row.get("discovery_record_id"),
                "article_url": article_url,
                "title": row.get("article_title"),
                "publisher": row.get("article_publisher"),
                "publication_date": row.get("article_publication_date"),
            }
        )
        out.append(
            {
                "discovery_record_id": row["discovery_record_id"],
                "discovery_date": row["discovery_date"],
                "source_payload_fingerprint": row["record_fingerprint"],
                "proposal_fingerprint": fingerprint,
                "article_title": row["article_title"],
                "article_publisher": row["article_publisher"],
                "article_publication_date": row["article_publication_date"],
                "article_url": article_url,
                "article_hostname": row["article_hostname"],
                "article_description": row["article_description"],
                "discovery_provider": row["discovery_provider"],
                "discovery_query": row["discovery_query"],
                "discovery_feed_url": row["discovery_feed_url"],
                "discovery_wrapper_url": row["discovery_wrapper_url"],
                "schema_version": INTAKE_SCHEMA_VERSION,
                "intake_record_id": stable_id("care-line-phase12-intake", row["discovery_record_id"]),
                "canonical_source_url": article_url,
                "source_title": row["article_title"],
                "publisher": row["article_publisher"],
                "publication_date": row["article_publication_date"],
                "source_type": "publisher_article",
                "source_role": "clinic_operations_signal",
                "supporting_passage": "",
                "event_type": "",
                "service_line": "",
                "facility_name": "",
                "provider_name": "",
                "parent_organization": "",
                "operator_name": "",
                "former_owner": "",
                "new_owner": "",
                "facility_type": "",
                "address_line_1": "",
                "address_line_2": "",
                "city": "",
                "county": "",
                "state": "",
                "postal_code": "",
                "country_code": "US",
                "announcement_date": "",
                "effective_date": "",
                "date_precision": "day",
                "permanence": "",
                "evidence_level": "publisher_source",
                "evidence_strength": "reviewed",
                "is_primary_source": False,
                "care_line_public_eligible": False,
                "universal_event_eligible": False,
                "duplicate_of_record_id": "",
                "supersedes_intake_record_id": "",
                "withdrawal_status": "",
                "reviewer": "",
                "review_reason": "",
                "review_notes": "",
            }
        )
    return out


def reviewer_packet_from_records(records: Iterable[Mapping[str, Any]], *, sample_id: str) -> dict[str, Any]:
    rows = reviewer_records(records)
    return {
        "schema_version": PHASE12_REVIEW_PACKET_SCHEMA_VERSION,
        "sample_id": sample_id,
        "record_count": len(rows),
        "human_review_status": "required",
        "selection_policy": "include only fresh discovery_record.v2 rows with reviewer_usable status and non-wrapper article URLs",
        "records": rows,
    }


def write_reviewer_packet(packet: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_id = _text(packet, "sample_id") or "care-line-phase12-review"
    json_path = output_dir / f"{sample_id}.authoritative-review-workbook.json"
    csv_path = output_dir / f"{sample_id}.authoritative-review-workbook.csv"
    guide_path = output_dir / f"{sample_id}.authoritative-review-guide.md"
    _write_atomic_json(json_path, packet)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_dir, delete=False, newline="", suffix=".tmp") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_PACKET_FIELDS)
        writer.writeheader()
        for row in packet.get("records") or []:
            writer.writerow({field: row.get(field, "") for field in REVIEW_PACKET_FIELDS})
        tmp = Path(handle.name)
    tmp.replace(csv_path)
    guide = [
        "# Care Line Phase 12 Authoritative Review Guide",
        "",
        f"- Sample: `{sample_id}`",
        f"- Records: `{packet.get('record_count', 0)}`",
        "",
        "Use only the article URL and publisher metadata as starting points.",
        "Do not treat discovery descriptions, Google News wrappers, or search snippets as verified evidence.",
        "Add a supporting passage only after opening the canonical publisher or primary-source page.",
        "",
    ]
    guide_path.write_text("\n".join(guide), encoding="utf-8")
    return {"workbook_json": str(json_path), "workbook_csv": str(csv_path), "guide": str(guide_path)}


def packet_row_to_completed_intake(row: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
    completed = dict(row)
    completed.update(
        {
            "reviewer": "phase12-fixture-reviewer",
            "review_reason": "Reviewer verified canonical source and supporting passage.",
            "reviewed_at": "2026-07-22T12:00:00Z",
            "supporting_passage": "The source says the clinic will close and patients will be directed to another care site.",
            "event_type": "facility_closure",
            "service_line": "",
            "facility_name": "Example Clinic",
            "provider_name": "Example Clinic",
            "city": "Example City",
            "county": "Example County",
            "state": "IA",
            "announcement_date": completed.get("publication_date", "2026-07-22") or "2026-07-22",
            "permanence": "permanent",
            "universal_event_eligible": True,
        }
    )
    completed.update(updates)
    return completed


def google_news_rss_url(query: str) -> str:
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"


def collect_google_news_rss(
    *,
    discovery_date: str,
    queries: Iterable[str],
    max_records: int = 200,
    timeout: int = 20,
    allow_insecure_tls: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    collected_at = utc_now()
    for query in queries:
        if len(records) >= max_records:
            break
        feed_url = google_news_rss_url(query)
        try:
            with urllib.request.urlopen(feed_url, timeout=timeout, context=context) as response:  # noqa: S310
                payload = response.read()
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({"query": query, "feed_url": feed_url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            diagnostics.append({"query": query, "feed_url": feed_url, "error": f"ParseError: {exc}"})
            continue
        for rank, item in enumerate(root.findall(".//item"), start=1):
            if len(records) >= max_records:
                break
            source_el = item.find("source")
            source = {
                "title": (source_el.text or "").strip() if source_el is not None else "",
                "url": source_el.attrib.get("url", "") if source_el is not None else "",
            }
            guid_el = item.find("guid")
            raw = {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published_at": (item.findtext("pubDate") or "").strip(),
                "description": (item.findtext("description") or "").strip(),
                "source": source,
                "guid": (guid_el.text or "").strip() if guid_el is not None else "",
            }
            records.append(
                record_from_provider_item(
                    raw,
                    discovery_date=discovery_date,
                    provider="google_news",
                    query=query,
                    feed_url=feed_url,
                    rank=rank,
                    collected_at=collected_at,
                )
            )
    return deduplicate_records(records), diagnostics


def source_mix_assessment(repo_root: Path) -> dict[str, Any]:
    query_path = repo_root / "data" / "dispatches" / "care-line" / "discovery_queries.json"
    direct_rss = publisher_feeds = government_feeds = health_system_newsrooms = state_regulator_feeds = local_news_feeds = other = 0
    google_news = 0
    if query_path.exists():
        payload = json.loads(query_path.read_text(encoding="utf-8"))
        google_news = len(payload.get("queries") or []) + len(payload.get("date_bounded_queries") or [])
    if not google_news:
        google_news = len(DEFAULT_GOOGLE_NEWS_QUERIES)
    return {
        "direct_rss": direct_rss,
        "publisher_feeds": publisher_feeds,
        "government_feeds": government_feeds,
        "health_system_newsrooms": health_system_newsrooms,
        "state_regulator_feeds": state_regulator_feeds,
        "local_news_feeds": local_news_feeds,
        "google_news": google_news,
        "other_discovery_providers": other,
        "assessment": "current Care Line discovery is Google News query driven; direct publisher, regulator, and health-system feeds are not configured in the collector",
    }


def run_phase12_collection(
    repo_root: Path,
    *,
    discovery_date: str,
    sample_id: str,
    output_dir: Path,
    review_dir: Path,
    max_records: int,
    queries: list[str],
    strict: bool = False,
    allow_insecure_tls: bool = False,
) -> dict[str, Any]:
    refuse_public_or_pages_path(output_dir if output_dir.is_absolute() else repo_root / output_dir, repo_root)
    refuse_public_or_pages_path(review_dir if review_dir.is_absolute() else repo_root / review_dir, repo_root)
    records, diagnostics = collect_google_news_rss(
        discovery_date=discovery_date,
        queries=queries,
        max_records=max_records,
        allow_insecure_tls=allow_insecure_tls,
    )
    output_root = output_dir if output_dir.is_absolute() else repo_root / output_dir
    review_root = review_dir if review_dir.is_absolute() else repo_root / review_dir
    paths = write_discovery_v2(records, output_root, strict=strict, diagnostics={"provider_errors": diagnostics})
    packet = reviewer_packet_from_records(records, sample_id=sample_id)
    packet_paths: dict[str, str] = {}
    if packet["record_count"] and paths["quality"]["quality_gate_passed"]:
        packet_paths = write_reviewer_packet(packet, review_root)
    summary = {
        "schema_version": DISCOVERY_COLLECTION_SCHEMA_VERSION,
        "sample_id": sample_id,
        "discovery_date": discovery_date,
        "queries": queries,
        "max_records": max_records,
        "collection_paths": paths,
        "review_packet": {"record_count": packet["record_count"], "paths": packet_paths},
        "quality": paths["quality"],
        "provider_diagnostics": diagnostics,
    }
    _write_atomic_json(output_root / "phase12_collection_summary.json", summary)
    if strict and not paths["quality"]["quality_gate_passed"]:
        raise SystemExit(2)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Care Line discovery record v2 collection and quality tooling.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--date", required=True)
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--review-dir", default="data/universal_events/shadow/care-line/phase12-review")
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--strict-quality", action="store_true")
    parser.add_argument("--allow-insecure-tls", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir or f"data/dispatches/care-line/sources/{args.date}")
    sample_id = args.sample_id or f"care_line_phase12_{args.date}"
    try:
        result = run_phase12_collection(
            repo_root,
            discovery_date=args.date,
            sample_id=sample_id,
            output_dir=output_dir,
            review_dir=Path(args.review_dir),
            max_records=args.max_records,
            queries=args.query or DEFAULT_GOOGLE_NEWS_QUERIES,
            strict=args.strict_quality,
            allow_insecure_tls=args.allow_insecure_tls,
        )
    except SystemExit as exc:
        return int(exc.code)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
