from __future__ import annotations

import json
import os
import re
import ssl
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from bluefern_dispatches.care_line_record import (
    AUTHORITY_LEVEL_VALUES,
    FieldProvenance,
    JURISDICTION_ALIASES,
    JURISDICTIONS_BY_CODE,
    CareLineReviewedRecord,
    stable_json_hash,
)
from bluefern_dispatches.care_line_discovery import discover_care_line_sources
from bluefern_dispatches.care_line_effective_date_follow_up import (
    build_follow_up_queries,
    _care_line_identity_text,
    care_line_event_identity,
    care_line_lifecycle_status,
    load_follow_up_state,
    load_reviewed_records,
    update_follow_up_state,
)
from bluefern_dispatches.care_line_source_registry import (
    CareLineSource,
    CareLineSourceRegistry,
    load_registry,
    source_readiness_reason,
    source_readiness_status,
)
from bluefern_dispatches.care_line_sources import load_pressure_source_registry


COLLECTION_RUNS_ROOT = Path("data/dispatches/care-line/collection-runs")
REVIEW_ROOT = Path("data/dispatches/care-line/review")
SMOKE_COLLECTION_RUNS_ROOT = COLLECTION_RUNS_ROOT / "smoke"
SMOKE_REVIEW_ROOT = REVIEW_ROOT / "smoke"
WORKING_REVIEW_QUEUE_PATH = REVIEW_ROOT / "current-review-queue.json"
WORKING_BACKLOG_PATH = REVIEW_ROOT / "current-review-backlog.json"
WORKING_EXCLUSIONS_PATH = REVIEW_ROOT / "current-exclusions.json"
WORKING_DUPLICATES_PATH = REVIEW_ROOT / "current-duplicates.json"
WORKING_FAILED_EXTRACTIONS_PATH = REVIEW_ROOT / "current-failed-extractions.json"
WORKING_MANUAL_REVIEW_PATH = REVIEW_ROOT / "current-manual-review.json"
REVIEW_SNAPSHOT_ROOT = REVIEW_ROOT / "signal-reviews"
LEGACY_PRESSURE_REGISTRY_PATH = Path("data/dispatches/care-line/pressure_source_registry.json")
CANONICAL_REGISTRY_PATH = Path("data/dispatches/care-line/source_registry.json")
CANDIDATE_REGISTRY_PATH = REVIEW_ROOT / "candidate-registry.json"
SMOKE_SOURCE_LIMIT_CEILING = 3
SMOKE_ITEMS_PER_SOURCE_CEILING = 3

PIPELINE_SCHEMA_VERSION = "bluefern.care_line.national_pipeline.v2"
RAW_ITEM_SCHEMA_VERSION = "bluefern.care_line.raw_item.v1"
EVENT_LEAD_SCHEMA_VERSION = "bluefern.care_line.event_lead.v1"
QUALIFICATION_SCHEMA_VERSION = "bluefern.care_line.qualification_result.v1"
EXCLUSION_SCHEMA_VERSION = "bluefern.care_line.exclusion_record.v1"
REVIEW_QUEUE_SCHEMA_VERSION = "bluefern.care_line.national_review_queue.v2"
SNAPSHOT_SCHEMA_VERSION = "bluefern.care_line.review_snapshot.v1"
CANDIDATE_REGISTRY_SCHEMA_VERSION = "bluefern.care_line.candidate_registry.v2"
CLUSTERING_SCHEMA_VERSION = "bluefern.care_line.cluster_summary.v2"
PARSER_VERSION = "care-line-national-pipeline-v2"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_PARTIAL_SUCCESS = "partial_success"
RUN_STATUS_FAILURE = "failure"

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "STANDARD": 2, "LOW": 3}
QUEUE_PRIORITY_VALUES = set(PRIORITY_ORDER)

NEGATIVE_EXCLUSION_REASONS = {
    "background_only",
    "resource_listing",
    "general_healthcare_news",
    "non_care_line",
    "marketing_announcement",
    "award_or_fundraising",
    "research_or_health_advice",
    "hiring_or_biography",
    "policy_commentary_only",
    "construction_without_access_consequence",
    "routine_operations_only",
    "financial_distress_without_access_consequence",
    "service_expansion_without_prior_loss_context",
}
FAILED_EXTRACTION_REASONS = {
    "needs_full_article",
    "needs_date",
    "needs_geography",
    "needs_access_consequence",
    "missing_subject",
    "missing_event_type",
    "missing_service_line_or_facility_scope",
    "insufficient_bounded_evidence",
    "private_or_inaccessible_evidence",
}
EXTRACTION_OUTCOMES = {
    "BODY_EXTRACTED",
    "PARTIAL_BODY",
    "INDEX_ONLY",
    "HEADLINE_ONLY",
    "PAYWALLED",
    "SCRIPT_RENDERED",
    "PDF_REQUIRED",
    "ACCESS_BLOCKED",
    "PARSE_FAILED",
    "EMPTY_RESPONSE",
}
EDITORIAL_QUALIFICATION_OUTCOMES = {
    "QUALIFIED",
    "EXCLUDED",
    "NEEDS_FULL_ARTICLE",
    "NEEDS_DATE",
    "NEEDS_GEOGRAPHY",
    "NEEDS_ACCESS_CONSEQUENCE",
    "NEEDS_SERVICE_CLASSIFICATION",
    "NEEDS_HUMAN_REVIEW",
}
CURRENTNESS_CLASSES = {
    "CURRENT_EVENT",
    "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE",
    "CURRENT_UPDATE_TO_PRIOR_EVENT",
    "CURRENT_RESTORATION",
    "RECENT_BACKGROUND",
    "HISTORICAL_BACKGROUND",
    "RETROSPECTIVE_ANALYSIS",
    "DATE_UNRESOLVED",
}
FRESHNESS_ROLES = {
    "BREAKING",
    "CURRENT",
    "RECENT_CONTEXT",
    "HISTORICAL_CONTEXT",
    "FUTURE_EFFECTIVE",
    "ONGOING_EVENT_UPDATE",
    "RESTORATION_UPDATE",
    "UNKNOWN",
}

DEFAULT_ARTICLE_SELECTORS = (
    "article",
    "entry-content",
    "article-content",
    "story-content",
    "storytext",
    "main-content",
)
SOURCE_ARTICLE_SELECTOR_HINTS: dict[str, tuple[str, ...]] = {
    "kff-health-news": ("article", "entry-content", "story-content"),
    "npr-health": ("storytext", "article", "story-content"),
    "texas-tribune-health": ("article-content", "story-content", "article"),
}
SOURCE_FORMAT_FAMILIES: dict[str, str] = {
    "rss": "RSS_OR_ATOM_SUMMARY",
    "atom": "RSS_OR_ATOM_SUMMARY",
    "json_feed": "JSON_LISTING",
    "sitemap": "SITEMAP",
    "structured_index": "STRUCTURED_HTML_INDEX",
}
PAYWALL_HINTS = (
    "subscribe to continue",
    "subscription required",
    "already a subscriber",
    "sign in to continue",
    "this content is for subscribers",
)
BOILERPLATE_HINTS = (
    "skip to main content",
    "republish this article",
    "share this:",
    "read more",
    "article first appeared on",
    "become a member",
    "donate",
    "all rights reserved",
)
MAX_EXTRACTED_TEXT_CHARS = 24000

SOURCE_FAILURE_CLASSES = {"HTTPError", "ValueError", "ParseError", "TimeoutError", "URLError"}

POSITIVE_EVENT_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("facility_closure", "closure", re.compile(r"\b(close|closed|closing|closure|remain(?:s)? closed|still closed|shut(?:ting)? down|cease(?:s|d)? operations?)\b", re.I)),
    ("planned_facility_closure", "closure", re.compile(r"\b(will close|plans? to close|set to close|scheduled to close)\b", re.I)),
    ("temporary_facility_suspension", "suspension", re.compile(r"\b(temp(?:orary|orarily)? (?:close|closure|shut(?:down)?|suspend)|temporarily halt)\b", re.I)),
    ("service_closure", "service", re.compile(r"\b(end(?:ing)?|stop(?:ping)?|discontinu(?:e|ing)|eliminat(?:e|ing))\b", re.I)),
    ("service_suspension", "service", re.compile(r"\b(suspend(?:ed|ing|s)?|remain(?:s)? suspended|still suspended|halt(?:ed|ing|s)?|pause(?:d|s|ing)? services?|stop admissions|divert(?:ed|ing|s)?)\b", re.I)),
    ("hours_reduction", "hours", re.compile(r"\b(reduc(?:e|es|ed|ing) hours?|cut(?:s|ting)? hours?|shorter hours?)\b", re.I)),
    ("capacity_reduction", "capacity", re.compile(r"\b(reduc(?:e|es|ed|ing) beds?|cut(?:s|ting)? beds?|capacity reduction|fewer beds?|reduce capacity)\b", re.I)),
    ("service_reduction", "restriction", re.compile(r"\b(limit(?:ed|ing|s)? services?|staffing restriction|service unavailable|restricted access)\b", re.I)),
    ("bankruptcy_service_impact", "bankruptcy", re.compile(r"\b(bankruptcy|receivership|insolvency)\b", re.I)),
    ("facility_relocation", "relocation", re.compile(r"\b(relocat(?:e|es|ed|ing)|moving to)\b", re.I)),
    ("facility_conversion", "consolidation", re.compile(r"\b(consolidat(?:e|es|ed|ing)|convert(?:ed|ing|s)? to)\b", re.I)),
    ("facility_reopening", "restoration", re.compile(r"\b(reopen(?:ed|ing|s)?|resume(?:d|s|ing)? operations?)\b", re.I)),
    ("service_restoration", "restoration", re.compile(r"\b(restore(?:d|s|ing)? services?|service restored|resume(?:d|s|ing)? service)\b", re.I)),
]

HEALTHCARE_CONTEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hospital", re.compile(r"\b(hospital|medical center|health system)\b", re.I)),
    ("clinic", re.compile(r"\b(clinic|health center|care center)\b", re.I)),
    ("emergency_care", re.compile(r"\b(emergency department|emergency room|ER|ED)\b", re.I)),
    ("labor_and_delivery", re.compile(r"\b(labor and delivery|labor & delivery|birth center|maternity)\b", re.I)),
    ("behavioral_health", re.compile(r"\b(behavioral health|mental health|psychiatric)\b", re.I)),
    ("dialysis", re.compile(r"\b(dialysis)\b", re.I)),
    ("pharmacy", re.compile(r"\b(pharmacy)\b", re.I)),
    ("ambulance_ems", re.compile(r"\b(ambulance|EMS|emergency medical services)\b", re.I)),
    ("inpatient_care", re.compile(r"\b(inpatient|admissions?)\b", re.I)),
    ("primary_care", re.compile(r"\b(primary care|family medicine)\b", re.I)),
    ("urgent_care", re.compile(r"\burgent care\b", re.I)),
    ("pediatrics", re.compile(r"\bpediatric(?:s)?\b", re.I)),
    ("oncology", re.compile(r"\boncology|cancer care\b", re.I)),
    ("rehabilitation", re.compile(r"\brehabilitation\b", re.I)),
    ("surgery", re.compile(r"\bsurgery|surgical\b", re.I)),
]

NEGATIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("research_or_health_advice", re.compile(r"\b(study|research|wellness|health tips|advice|prevention|disease education)\b", re.I)),
    ("award_or_fundraising", re.compile(r"\b(award|honor|fundraiser|gala|donation|grant awarded)\b", re.I)),
    ("marketing_announcement", re.compile(r"\b(celebrates|campaign|launches new brand|brand campaign|anniversary)\b", re.I)),
    ("hiring_or_biography", re.compile(r"\b(hiring|job posting|career opportunity|appoints|named as|biography)\b", re.I)),
    ("resource_listing", re.compile(r"\b(directory|find a provider|resource guide|provider finder|calendar|event listing)\b", re.I)),
    ("policy_commentary_only", re.compile(r"\b(op-ed|opinion|commentary|analysis only)\b", re.I)),
    ("policy_commentary_only", re.compile(r"\b(court ruling|appeals court|lawsuit|settlement|proposed rule|proposed bill|bill act|under .* law|policy proposal)\b", re.I)),
    ("construction_without_access_consequence", re.compile(r"\b(groundbreaking|construction|renovation|new building|facility opening)\b", re.I)),
    ("financial_distress_without_access_consequence", re.compile(r"\b(earnings|quarterly results|margin pressure|balance sheet|bond rating|funding round)\b", re.I)),
    ("general_healthcare_news", re.compile(r"\b(vaccine|infection|epidemic|lawsuit settlement|copay settlement|researchers found)\b", re.I)),
]

ACCESS_CONSEQUENCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("LOSS_OF_LOCAL_ACCESS", re.compile(r"\b(no longer offer|will close|closed|ending services|service will end|patients will lose access)\b", re.I)),
    ("REDUCED_SERVICE_AVAILABILITY", re.compile(r"\b(suspended|halted|paused|service unavailable|limited service|reduced service)\b", re.I)),
    ("REDUCED_OPERATING_HOURS", re.compile(r"\b(reduced hours|shorter hours|hours cut)\b", re.I)),
    ("REDUCED_BED_OR_APPOINTMENT_CAPACITY", re.compile(r"\b(reduced beds|fewer beds|capacity reduction|fewer appointments|reduced capacity)\b", re.I)),
    ("EMERGENCY_DIVERSION", re.compile(r"\b(divert(?:ed|ing|s)? ambulances?|emergency diversion)\b", re.I)),
    ("TRANSFER_DEPENDENCE", re.compile(r"\b(transfer patients|patients will be transferred|redirect patients)\b", re.I)),
    ("LONGER_TRAVEL_DISTANCE", re.compile(r"\b(longer travel|farther travel|travel farther|drive farther)\b", re.I)),
    ("WORKFORCE_RELATED_RESTRICTION", re.compile(r"\b(staffing shortage|staff shortage|lack of staff|workforce shortage)\b", re.I)),
    ("SUBSTITUTE_SERVICE_OFFERED", re.compile(r"\b(reopen|restored|resume service|replacement provider|alternative site)\b", re.I)),
]

SERVICE_LINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("labor_and_delivery", re.compile(r"\b(labor and delivery|labor & delivery|birth center)\b", re.I)),
    ("maternity", re.compile(r"\bmaternity\b", re.I)),
    ("emergency_care", re.compile(r"\b(emergency department|emergency room|ER|ED)\b", re.I)),
    ("behavioral_health", re.compile(r"\b(behavioral health|mental health)\b", re.I)),
    ("psychiatric_care", re.compile(r"\bpsychiatric\b", re.I)),
    ("dialysis", re.compile(r"\bdialysis\b", re.I)),
    ("pharmacy", re.compile(r"\bpharmacy\b", re.I)),
    ("ambulance_ems", re.compile(r"\b(ambulance|EMS|emergency medical services)\b", re.I)),
    ("inpatient_care", re.compile(r"\b(inpatient|admissions?)\b", re.I)),
    ("primary_care", re.compile(r"\b(primary care|family medicine)\b", re.I)),
    ("urgent_care", re.compile(r"\burgent care\b", re.I)),
    ("pediatrics", re.compile(r"\bpediatric(?:s)?\b", re.I)),
    ("oncology", re.compile(r"\boncology|cancer care\b", re.I)),
    ("rehabilitation", re.compile(r"\brehabilitation\b", re.I)),
    ("surgery", re.compile(r"\bsurgery|surgical\b", re.I)),
    ("specialty_care", re.compile(r"\bspecialty care\b", re.I)),
]

FACILITY_TYPE_HINTS: list[tuple[str, re.Pattern[str]]] = [
    ("hospital", re.compile(r"\b(hospital|medical center)\b", re.I)),
    ("clinic", re.compile(r"\b(clinic|health center)\b", re.I)),
    ("pharmacy", re.compile(r"\bpharmacy\b", re.I)),
    ("dialysis_center", re.compile(r"\bdialysis\b", re.I)),
    ("behavioral_health_center", re.compile(r"\bbehavioral health|psychiatric\b", re.I)),
]

NAVIGATION_URL_PATTERNS = (
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/author/",
    "/topic/",
    "/topics/",
    "/search",
    "/feed",
    "/wp-json/",
)
MONTH_NAME_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
HISTORICAL_MARKERS = (
    "last year",
    "years after",
    "since the closure",
    "since the hospital closed",
    "previously closed",
    "a decade ago",
    "one of the first to",
    "earlier lifeline",
    "earlier closure",
    "at the time",
    "back in ",
)
RECENT_BACKGROUND_MARKERS = (
    "last month",
    "earlier this year",
    "earlier this spring",
    "earlier this summer",
    "earlier this winter",
    "earlier this fall",
)
CURRENT_ANNOUNCEMENT_MARKERS = (
    "will close",
    "plans to close",
    "planned closure",
    "set to close",
    "scheduled to close",
    "announced it will",
    "announced plans to",
    "effective ",
    "beginning ",
    "starting ",
)
ONGOING_UPDATE_MARKERS = (
    "remains closed",
    "remain closed",
    "still closed",
    "continues to be closed",
    "continues to suspend",
    "still suspended",
    "fight to save",
    "working to reopen",
)
RETROSPECTIVE_MARKERS = (
    "trend",
    "over the years",
    "history of",
    "has become a case study",
    "under the law",
    "faces test under",
    "examples include",
    "explainer",
)
ACTIONABLE_EVENT_PATTERN = re.compile(
    r"\b("
    r"will close|will end|will suspend|will reduce|will reopen|will restore|plans? to close|set to close|scheduled to close|proposed closure|proposed closing|moving forward|vote to close|vote on closing|stop vote|closed|closing|"
    r"remain(?:s)? closed|still closed|reopen(?:ed|ing|s)?|restore(?:d|s|ing)?|resume(?:d|s|ing)?|"
    r"suspend(?:ed|ing|s)?|remain(?:s)? suspended|still suspended|halt(?:ed|ing|s)?|"
    r"reduce(?:d|s|ing)? hours?|cut(?:s|ting)? beds?|shut(?:ting)? down|stop admissions|divert(?:ed|ing|s)?"
    r")\b",
    re.I,
)
ALIGNMENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "its", "of", "on", "or",
    "that", "the", "their", "this", "to", "was", "were", "will", "with", "under", "after", "about", "into",
    "hospital", "hospitals", "clinic", "clinics", "health", "healthcare", "care", "center", "centers", "system",
    "services", "service", "closure", "closures", "close", "closed", "closing", "reopen", "reopened", "reopening",
    "program", "model", "rural", "officials", "residents", "patients", "facility", "facilities",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_json_text(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{stable_json_hash(parts)[:16]}"


def _registry_path(root: Path, registry_path: Path | None = None) -> Path:
    return root / (registry_path or CANONICAL_REGISTRY_PATH)


def _review_state_paths(review_root: Path) -> dict[str, Path]:
    return {
        "review_queue": review_root / "current-review-queue.json",
        "backlog": review_root / "current-review-backlog.json",
        "exclusions": review_root / "current-exclusions.json",
        "duplicates": review_root / "current-duplicates.json",
        "failed_extractions": review_root / "current-failed-extractions.json",
        "manual_review": review_root / "current-manual-review.json",
        "follow_up_state": review_root / "effective-date-follow-up-state.json",
        "snapshot_root": review_root / "signal-reviews",
        "candidate_registry": review_root / "candidate-registry.json",
    }


def _review_state_mode(*, smoke_test: bool, review_root: Path) -> str:
    return "isolated_smoke" if smoke_test and review_root == SMOKE_REVIEW_ROOT else "custom" if review_root != REVIEW_ROOT else "production"


def _sorted_smoke_source_rows(source_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    readiness_rank = {"AUTOMATED_READY": 0, "AUTOMATED_PARTIAL": 1, "MANUAL_REVIEW_ONLY": 2}
    normalized = [dict(row) for row in source_rows]
    normalized.sort(
        key=lambda row: (
            readiness_rank.get(str(row.get("readiness") or ""), 9),
            str(row.get("source_id") or ""),
            getattr(row.get("source"), "feed_url", ""),
        )
    )
    return normalized


def load_canonical_registry(root: Path, *, include_disabled: bool = True, registry_path: Path | None = None) -> CareLineSourceRegistry:
    return load_registry(_registry_path(root, registry_path), include_disabled=include_disabled)


def adapt_pressure_registry(root: Path, *, path: Path | None = None) -> dict[str, Any]:
    rows = load_pressure_source_registry(root, path or LEGACY_PRESSURE_REGISTRY_PATH)
    adapted = []
    for row in rows:
        feed_url = _text(row, "rss_url", "atom_url", "json_feed_url", "sitemap_url", "homepage_url")
        adapter_type = (
            "rss" if _text(row, "rss_url")
            else "atom" if _text(row, "atom_url")
            else "json_feed" if _text(row, "json_feed_url")
            else "sitemap" if _text(row, "sitemap_url")
            else "structured_index"
        )
        collection_method = str(row.get("collection_method") or "").strip() or (
            "feed_polling" if adapter_type in {"rss", "atom", "json_feed"} else "sitemap_polling" if adapter_type == "sitemap" else "structured_index_polling"
        )
        adapted.append(
            {
                "source_id": _text(row, "source_id"),
                "name": _text(row, "source_name") or _text(row, "source_id"),
                "publisher": _text(row, "source_name") or _text(row, "source_id"),
                "source_type": "local_publisher" if _text(row, "source_type") in {"local_news", "nonprofit_newsroom"} else "regional_publisher" if _text(row, "source_type") else "regional_publisher",
                "feed_url": feed_url,
                "homepage_url": _text(row, "homepage_url") or feed_url,
                "state": _text(row, "state"),
                "geographic_scope": "national" if _text(row, "coverage_scope") == "state_filtered_national_dataset" else "state" if _text(row, "state") else "regional",
                "organization_type": _text(row, "ownership_type") or "legacy_compatibility",
                "authority_level": "secondary",
                "enabled": bool(row.get("active")) and bool(row.get("ingest_ready")),
                "adapter_type": adapter_type,
                "source_role": "legacy_pressure_compatibility",
                "source_category": "local_health_reporting",
                "collection_method": collection_method,
                "searchability": "feed" if adapter_type in {"rss", "atom", "json_feed"} else "mixed",
                "legacy_source_type": _text(row, "source_type"),
                "legacy_feed_health": _text(row, "feed_health"),
                "legacy_validation_status": _text(row, "validation_status"),
                "legacy_registry_path": str((path or LEGACY_PRESSURE_REGISTRY_PATH).as_posix()),
            }
        )
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "compatibility_role": "compatibility_input_only",
        "source_count": len(adapted),
        "sources": adapted,
    }


def _normalize_authority_level(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if lowered == "regulator":
        return "official"
    if lowered in AUTHORITY_LEVEL_VALUES:
        return lowered
    return "secondary"


def parse_source_date(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", "missing"
    try:
        return parsedate_to_datetime(text).date().isoformat(), "source_dated"
    except Exception:  # noqa: BLE001
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat(), "source_dated"
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1), "source_dated"
    return "", "unparseable"


def fetch_url(url: str, *, timeout: int = 20, allow_insecure_tls: bool = False, user_agent: str = "BlueFernCareLineNationalPipeline/2.0") -> tuple[bytes, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        headers = {key.lower(): value for key, value in response.headers.items()}
        return response.read(), {
            "http_status": getattr(response, "status", 0) or 0,
            "content_type": headers.get("content-type", ""),
            "final_url": response.geturl(),
        }


def fetch_source(source: CareLineSource, *, timeout: int = 20, allow_insecure_tls: bool = False) -> tuple[bytes, dict[str, Any]]:
    return fetch_url(source.feed_url, timeout=timeout, allow_insecure_tls=allow_insecure_tls)


def _rss_items(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    items = []
    for node in root.findall(".//item"):
        items.append(
            {
                "title": node.findtext("title") or "",
                "url": node.findtext("link") or "",
                "published_at": node.findtext("pubDate") or "",
                "description": node.findtext("description") or "",
                "source": node.findtext("source") or "",
                "id": node.findtext("guid") or "",
            }
        )
    return items


def _atom_items(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []
    for node in root.findall(".//atom:entry", ns) or root.findall(".//entry"):
        link = ""
        for link_node in node.findall("atom:link", ns) or node.findall("link"):
            rel = link_node.attrib.get("rel", "alternate")
            href = link_node.attrib.get("href", "")
            if rel == "alternate" and href:
                link = href
                break
        items.append(
            {
                "title": node.findtext("atom:title", default="", namespaces=ns) or node.findtext("title") or "",
                "url": link,
                "published_at": node.findtext("atom:published", default="", namespaces=ns) or node.findtext("atom:updated", default="", namespaces=ns) or "",
                "description": node.findtext("atom:summary", default="", namespaces=ns) or node.findtext("atom:content", default="", namespaces=ns) or "",
                "source": "",
                "id": node.findtext("atom:id", default="", namespaces=ns) or node.findtext("id") or "",
            }
        )
    return items


def _json_feed_items(payload: bytes) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8"))
    out = []
    for item in data.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        out.append(
            {
                "title": _text(item, "title"),
                "url": _text(item, "url", "external_url", "id"),
                "published_at": _text(item, "date_published", "date_modified"),
                "description": _text(item, "summary", "content_text", "content_html"),
                "source": _text(data, "title"),
                "id": _text(item, "id"),
            }
        )
    return out


def _looks_like_sitemap_xml(payload: bytes) -> bool:
    probe = payload[:200].decode("utf-8", errors="ignore").lower()
    return "<urlset" in probe or "<sitemapindex" in probe


def _parse_sitemap_xml(payload: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    root = ET.fromstring(payload)
    namespace = ""
    if root.tag.startswith("{") and "}" in root.tag:
        namespace = root.tag.split("}", 1)[0] + "}"
    if root.tag.endswith("sitemapindex"):
        nested = []
        for node in root.findall(f".//{namespace}sitemap"):
            loc = node.findtext(f"{namespace}loc") or ""
            if loc:
                nested.append(loc.strip())
        return [], nested
    items = []
    for node in root.findall(f".//{namespace}url"):
        loc = (node.findtext(f"{namespace}loc") or "").strip()
        if not loc:
            continue
        items.append(
            {
                "title": "",
                "url": loc,
                "published_at": (node.findtext(f"{namespace}lastmod") or "").strip(),
                "description": "",
                "source": "",
                "id": loc,
            }
        )
    return items, []


class _StructuredIndexHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._current_href = ""
        self._current_text: list[str] = []
        self._current_time = ""
        self._last_meta_description = ""
        self._in_title = False
        self.document_title = ""
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        self._stack.append(tag.lower())
        if tag.lower() == "a":
            self._current_href = urljoin(self.base_url, attrs_map.get("href", ""))
            self._current_text = []
        elif tag.lower() == "time":
            self._current_time = attrs_map.get("datetime", "")
        elif tag.lower() == "meta":
            name = attrs_map.get("name", "").lower()
            prop = attrs_map.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self._last_meta_description = attrs_map.get("content", "")
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a":
            text = " ".join(part.strip() for part in self._current_text if part.strip()).strip()
            if self._current_href and text:
                self.links.append({"url": self._current_href, "title": unescape(text), "published_at": self._current_time, "description": ""})
            self._current_href = ""
            self._current_text = []
        elif lowered == "time":
            self._current_time = ""
        elif lowered == "title":
            self._in_title = False
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._current_href:
            self._current_text.append(text)
        if self._in_title:
            if self.document_title:
                self.document_title += " "
            self.document_title += text


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self.title = ""
        self.meta_description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth += 1
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if lowered == "meta":
            name = attrs_map.get("name", "").lower()
            prop = attrs_map.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.meta_description = attrs_map.get("content", "")
        elif lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if lowered == "title":
            self._in_title = False
        if lowered in {"p", "div", "li", "section", "article", "br", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            if self.title:
                self.title += " "
            self.title += text
        self._parts.append(text)

    def text(self) -> str:
        combined = " ".join(self._parts)
        combined = re.sub(r"\s+\n", "\n", combined)
        combined = re.sub(r"\n\s+", "\n", combined)
        combined = re.sub(r"\n{2,}", "\n\n", combined)
        return combined.strip()


class _ScopedArticleExtractor(HTMLParser):
    def __init__(self, selectors: Iterable[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._capture_depth = 0
        self._capture_key = ""
        self._capture_parts: list[str] = []
        self._article_depth = 0
        self._article_parts: list[str] = []
        self._full_parts: list[str] = []
        self._json_ld_parts: list[str] = []
        self._in_title = False
        self._in_json_ld = False
        self.title = ""
        self.meta_description = ""
        self.meta_title = ""
        self.og_description = ""
        self.og_title = ""
        self.selector_hits: Counter[str] = Counter()
        self._selectors = tuple(selector.casefold() for selector in selectors if selector)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth += 1
            script_type = attrs_map.get("type", "").casefold()
            if script_type == "application/ld+json":
                self._in_json_ld = True
            return
        if self._skip_depth:
            return
        attr_text = " ".join(value for key, value in attrs_map.items() if key in {"id", "class", "role", "data-testid"}).casefold()
        if lowered in {"article", "main"}:
            self._article_depth += 1
        for selector in self._selectors:
            if selector and selector in attr_text:
                self._capture_depth += 1
                if not self._capture_key:
                    self._capture_key = selector
                self.selector_hits[selector] += 1
                break
        if lowered == "meta":
            name = attrs_map.get("name", "").casefold()
            prop = attrs_map.get("property", "").casefold()
            content = attrs_map.get("content", "")
            if name == "description":
                self.meta_description = content
            if name == "title":
                self.meta_title = content
            if prop == "og:description":
                self.og_description = content
            if prop == "og:title":
                self.og_title = content
        elif lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            if self._in_json_ld and lowered == "script":
                self._in_json_ld = False
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if lowered == "title":
            self._in_title = False
        if lowered in {"article", "main"} and self._article_depth:
            self._article_depth -= 1
        if self._capture_depth and lowered in {"div", "section", "article", "main"}:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                self._capture_key = ""
        if lowered in {"p", "div", "li", "section", "article", "br", "h1", "h2", "h3"}:
            self._full_parts.append("\n")
            if self._article_depth:
                self._article_parts.append("\n")
            if self._capture_depth:
                self._capture_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth and not self._in_json_ld:
            return
        if self._in_json_ld:
            self._json_ld_parts.append(data)
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        self._full_parts.append(text)
        if self._article_depth:
            self._article_parts.append(text)
        if self._capture_depth:
            self._capture_parts.append(text)

    def scoped_text(self) -> str:
        return _normalize_text(" ".join(self._capture_parts))

    def article_text(self) -> str:
        return _normalize_text(" ".join(self._article_parts))

    def full_text(self) -> str:
        return _normalize_text(" ".join(self._full_parts))

    def json_ld_text(self) -> str:
        return "\n".join(part for part in self._json_ld_parts if part.strip())


def _is_probable_article_url(url: str, *, source_host: str = "") -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url)
    if source_host and parsed.hostname and parsed.hostname.lower() != source_host.lower():
        return False
    lowered = parsed.path.lower()
    if any(token in lowered for token in NAVIGATION_URL_PATTERNS):
        return False
    if lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf", ".xml", ".rss")):
        return False
    return True


def _bounded_text(value: str, *, max_chars: int = MAX_EXTRACTED_TEXT_CHARS) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rsplit(" ", 1)[0].strip()


def _clean_extracted_text(value: str) -> str:
    if not value:
        return ""
    lines = []
    for raw_line in re.split(r"(?:\n|\r)+", value):
        line = _normalize_text(raw_line)
        if not line:
            continue
        lowered = line.casefold()
        if any(hint in lowered for hint in BOILERPLATE_HINTS):
            continue
        if len(line) < 4:
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return _bounded_text(cleaned)


def _first_nonempty(*values: str) -> str:
    for value in values:
        if _normalize_text(value):
            return _normalize_text(value)
    return ""


def _parse_json_ld_payload(raw_text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    try:
        top_level = json.loads(raw_text)
    except json.JSONDecodeError:
        top_level = None
    if isinstance(top_level, dict):
        payloads.append(top_level)
        return payloads
    if isinstance(top_level, list):
        payloads.extend(item for item in top_level if isinstance(item, dict))
        return payloads
    for chunk in re.split(r"\n+", raw_text):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            payloads.append(data)
        elif isinstance(data, list):
            payloads.extend(item for item in data if isinstance(item, dict))
    return payloads


def _json_ld_article_fields(raw_text: str) -> dict[str, str]:
    title = ""
    description = ""
    body = ""
    for payload in _parse_json_ld_payload(raw_text):
        type_value = str(payload.get("@type") or payload.get("type") or "")
        if type_value and not re.search(r"article|newsarticle|report", type_value, re.I):
            continue
        body = _first_nonempty(body, _text(payload, "articleBody", "text"))
        description = _first_nonempty(description, _text(payload, "description"))
        title = _first_nonempty(title, _text(payload, "headline", "name"))
    return {
        "title": _bounded_text(title, max_chars=500),
        "description": _bounded_text(description, max_chars=1200),
        "text": _clean_extracted_text(body),
    }


def _source_selector_hints(source: CareLineSource) -> tuple[str, ...]:
    extra = getattr(source, "model_extra", None) or {}
    configured = extra.get("article_selector_hints")
    selectors: list[str] = []
    if isinstance(configured, list):
        selectors.extend(str(item) for item in configured if item)
    selectors.extend(SOURCE_ARTICLE_SELECTOR_HINTS.get(source.source_id, ()))
    selectors.extend(DEFAULT_ARTICLE_SELECTORS)
    deduped: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        lowered = selector.casefold()
        if lowered not in seen:
            seen.add(lowered)
            deduped.append(selector)
    return tuple(deduped)


def _parse_structured_listing_json(data: Any, *, source_host: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            url = _text(value, "url", "href", "link", "permalink")
            title = _text(value, "title", "headline", "name")
            published_at = _text(value, "date", "published_at", "published", "pubDate", "lastmod")
            description = _text(value, "summary", "description", "excerpt")
            if url and _is_probable_article_url(url, source_host=source_host):
                items.append(
                    {
                        "title": title,
                        "url": url,
                        "published_at": published_at,
                        "description": description,
                        "source": "",
                        "id": url,
                    }
                )
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(data)
    return items


def parse_structured_index_items(source: CareLineSource, payload: bytes, *, source_url: str, max_items: int) -> list[dict[str, Any]]:
    source_host = (urlparse(source_url).hostname or "").lower()
    if _looks_like_sitemap_xml(payload):
        items, _ = _parse_sitemap_xml(payload)
        return items[:max_items]
    decoded = payload.decode("utf-8", errors="ignore").strip()
    if decoded.startswith("{") or decoded.startswith("["):
        try:
            parsed = json.loads(decoded)
            return _parse_structured_listing_json(parsed, source_host=source_host)[:max_items]
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"structured index JSON parse failed: {exc}") from exc
    parser = _StructuredIndexHTMLParser(source_url)
    parser.feed(decoded)
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for row in parser.links:
        url = row["url"]
        if url in seen_urls or not _is_probable_article_url(url, source_host=source_host):
            continue
        title = row["title"]
        if len(title.split()) < 3:
            continue
        seen_urls.add(url)
        items.append(
            {
                "title": title,
                "url": url,
                "published_at": row.get("published_at", ""),
                "description": parser._last_meta_description,
                "source": source.publisher,
                "id": url,
            }
        )
        if len(items) >= max_items:
            break
    return items


def parse_sitemap_items(source: CareLineSource, payload: bytes, *, source_url: str, max_items: int, fetch_timeout: int, allow_insecure_tls: bool, max_requests: int = 3) -> list[dict[str, Any]]:
    items, nested = _parse_sitemap_xml(payload)
    if items:
        return items[:max_items]
    discovered: list[dict[str, Any]] = []
    requests_left = max_requests
    seen: set[str] = set()
    for nested_url in nested:
        if requests_left <= 0 or nested_url in seen:
            continue
        seen.add(nested_url)
        requests_left -= 1
        nested_payload, _ = fetch_url(nested_url, timeout=fetch_timeout, allow_insecure_tls=allow_insecure_tls)
        nested_items, _ = _parse_sitemap_xml(nested_payload)
        discovered.extend(nested_items)
        if len(discovered) >= max_items:
            break
    return discovered[:max_items]


def parse_source_items(
    source: CareLineSource,
    payload: bytes,
    *,
    source_url: str,
    fetch_timeout: int,
    allow_insecure_tls: bool,
    max_items_per_source: int,
) -> list[dict[str, Any]]:
    if source.adapter_type == "rss":
        return _rss_items(payload)[:max_items_per_source]
    if source.adapter_type == "atom":
        return _atom_items(payload)[:max_items_per_source]
    if source.adapter_type == "json_feed":
        return _json_feed_items(payload)[:max_items_per_source]
    if source.adapter_type == "structured_index":
        return parse_structured_index_items(source, payload, source_url=source_url, max_items=max_items_per_source)
    if source.adapter_type == "sitemap":
        return parse_sitemap_items(
            source,
            payload,
            source_url=source_url,
            max_items=max_items_per_source,
            fetch_timeout=fetch_timeout,
            allow_insecure_tls=allow_insecure_tls,
        )
    raise ValueError(f"unsupported adapter: {source.adapter_type}")


def discovery_record_from_direct_item(
    item: Mapping[str, Any],
    source: CareLineSource,
    *,
    discovery_date: str,
    rank: int,
    collected_at: str,
    collection_run_id: str,
    source_artifact_path: str,
) -> dict[str, Any]:
    raw_url = _text(item, "url", "link", "id")
    title = _text(item, "title")
    publication_date_raw = _text(item, "published_at", "published", "updated")
    publication_date, source_date_state = parse_source_date(publication_date_raw)
    return {
        "schema_version": RAW_ITEM_SCHEMA_VERSION,
        "raw_item_id": _stable_id("care-line-raw-item", source.source_id, raw_url, title, publication_date or publication_date_raw, rank),
        "discovery_provider": f"canonical_{source.adapter_type}",
        "discovery_date": discovery_date,
        "collection_run_id": collection_run_id,
        "source_artifact_path": source_artifact_path,
        "rank": rank,
        "source_id": source.source_id,
        "source_name": source.name,
        "source_type": source.source_type,
        "source_publisher": source.publisher,
        "source_category": source.source_category,
        "authority_level": _normalize_authority_level(source.authority_level),
        "source_state": source.state,
        "source_geographic_scope": source.geographic_scope,
        "source_role": source.source_role,
        "adapter_type": source.adapter_type,
        "collection_method": source.collection_method,
        "item_url": raw_url,
        "title": title,
        "description": _text(item, "description", "summary", "content"),
        "source_item_id": _text(item, "id") or _stable_id("feed-item", raw_url, title),
        "source_publication_date": publication_date,
        "source_publication_date_raw": publication_date_raw,
        "source_date_state": source_date_state,
        "collected_at": collected_at,
        "item_permalink_available": source.item_permalink_available,
        "requires_html_followup": source.requires_html_followup,
        "archives_distinguishable_from_current": source.archives_distinguishable_from_current,
        "record_fingerprint": stable_json_hash({"source_id": source.source_id, "url": raw_url, "title": title, "published_at": publication_date or publication_date_raw}),
    }


def collectable_sources(
    registry: CareLineSourceRegistry,
    *,
    include_partial: bool = True,
    include_manual_review: bool = False,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_national: set[str] = set()
    for source in registry.sources:
        readiness = source_readiness_status(source)
        if readiness == "DISABLED":
            continue
        if readiness == "MANUAL_REVIEW_ONLY" and not include_manual_review:
            continue
        if readiness == "AUTOMATED_PARTIAL" and not include_partial:
            continue
        once_key = source.source_id if source.geographic_scope != "national" else source.feed_url
        if source.geographic_scope == "national" and once_key in seen_national:
            continue
        if source.geographic_scope == "national":
            seen_national.add(once_key)
        selected.append(
            {
                "source": source,
                "source_id": source.source_id,
                "readiness": readiness,
                "readiness_reason": source_readiness_reason(source),
                "national_single_execution": source.geographic_scope == "national",
            }
        )
    return selected


def _source_attempt_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.attempt.json"


def _source_raw_items_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.raw-items.json"


def _source_event_leads_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.event-leads.json"


def _source_candidates_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.qualified-candidates.json"


def _source_exclusions_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.exclusions.json"


def _source_failed_extractions_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.failed-extractions.json"


def _source_failure_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.failure.json"


def _source_prefilter_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.prefilter.json"


def build_run_key(*, run_date: str, source_ids: Iterable[str]) -> str:
    normalized = sorted({str(value).strip() for value in source_ids if str(value).strip()})
    return stable_json_hash({"run_date": run_date, "source_ids": normalized})[:12]


def build_run_id(root: Path, *, run_date: str, source_ids: Iterable[str], collection_runs_root: Path = COLLECTION_RUNS_ROOT) -> str:
    run_key = build_run_key(run_date=run_date, source_ids=source_ids)
    run_root = root / collection_runs_root / run_date
    existing = sorted(path.name for path in run_root.glob(f"{run_date.replace('-', '')}-{run_key}-*") if path.is_dir())
    suffix = len(existing) + 1
    return f"{run_date.replace('-', '')}-{run_key}-{suffix:02d}"


def begin_collection_run(
    root: Path,
    *,
    run_date: str,
    source_rows: Iterable[dict[str, Any]],
    settings: Mapping[str, Any],
    run_id: str | None = None,
    collection_runs_root: Path = COLLECTION_RUNS_ROOT,
) -> dict[str, Any]:
    source_ids = [_text(row, "source_id") for row in source_rows]
    run_id = run_id or build_run_id(root, run_date=run_date, source_ids=source_ids, collection_runs_root=collection_runs_root)
    run_dir = root / collection_runs_root / run_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "run_key": build_run_key(run_date=run_date, source_ids=source_ids),
        "run_date": run_date,
        "started_at": utc_now(),
        "source_count": len(source_ids),
        "source_ids": source_ids,
        "status": "running",
        "settings": dict(settings),
        "attempts": [],
    }
    _atomic_write(run_dir / "run-manifest.json", manifest)
    return manifest


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def _parse_iso_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _month_number(name: str) -> int:
    months = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    return months.get(name.strip().casefold().rstrip("."), 0)


def _explicit_dates_from_text(text: str, *, source_date: date | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b(20\d{2})\b", text):
        year = int(match.group(1))
        key = f"year:{year}"
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "label": match.group(0),
                "date": f"{year:04d}-01-01",
                "kind": "year_reference",
                "relation": "historical" if source_date and year < source_date.year else "current_or_future",
            }
        )
    month_day_pattern = re.compile(rf"\b({MONTH_NAME_PATTERN})\.?\s+(\d{{1,2}})(?:,\s*(20\d{{2}}))?\b", re.I)
    for match in month_day_pattern.finditer(text):
        month = _month_number(match.group(1))
        day = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else (source_date.year if source_date else datetime.now(timezone.utc).year)
        try:
            resolved = date(year, month, day)
        except ValueError:
            continue
        key = f"date:{resolved.isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        relation = "current_or_future"
        if source_date and resolved < source_date - timedelta(days=120):
            relation = "historical"
        elif source_date and resolved < source_date:
            relation = "recent_past"
        elif source_date and resolved > source_date:
            relation = "future"
        elif source_date and resolved == source_date:
            relation = "current"
        refs.append(
            {
                "label": match.group(0),
                "date": resolved.isoformat(),
                "kind": "calendar_date",
                "relation": relation,
            }
        )
    return refs


def _relative_date_references(text: str, *, source_date: date | None) -> list[dict[str, Any]]:
    lowered = text.casefold()
    refs: list[dict[str, Any]] = []
    if not source_date:
        return refs
    if "last year" in lowered:
        refs.append({"label": "last year", "date": f"{source_date.year - 1}-01-01", "kind": "relative_year", "relation": "historical"})
    if re.search(r"\byears? after\b", lowered):
        refs.append({"label": "years after", "date": "", "kind": "relative_year", "relation": "historical"})
    if "a decade ago" in lowered:
        refs.append({"label": "a decade ago", "date": f"{source_date.year - 10}-01-01", "kind": "relative_year", "relation": "historical"})
    if "last month" in lowered:
        recent = (source_date.replace(day=1) - timedelta(days=1)).replace(day=1)
        refs.append({"label": "last month", "date": recent.isoformat(), "kind": "relative_month", "relation": "recent_past"})
    if "next month" in lowered:
        future_anchor = (source_date.replace(day=28) + timedelta(days=10)).replace(day=1)
        refs.append({"label": "next month", "date": future_anchor.isoformat(), "kind": "relative_month", "relation": "future"})
    if "earlier this year" in lowered:
        refs.append({"label": "earlier this year", "date": f"{source_date.year}-01-01", "kind": "relative_year", "relation": "recent_past"})
    if "today" in lowered or "this week" in lowered:
        refs.append({"label": "current_period", "date": source_date.isoformat(), "kind": "relative_current", "relation": "current"})
    return refs


def _all_date_references(text: str, *, source_date: date | None) -> list[dict[str, Any]]:
    return _explicit_dates_from_text(text, source_date=source_date) + _relative_date_references(text, source_date=source_date)


def _sentence_date_references(text: str, *, source_date: date | None) -> list[str]:
    return [ref["date"] for ref in _all_date_references(text, source_date=source_date) if ref.get("date")]


def _title_body_event_agreement(title: str, text: str, service_line: str) -> bool:
    title_event = _event_type_from_text(title, service_line=service_line)
    body_event = _event_type_from_text(text, service_line=service_line)
    if not title_event or not body_event:
        return True
    if title_event == body_event:
        return True
    closureish = {"facility_closure", "planned_facility_closure", "service_closure", "service_suspension", "temporary_facility_suspension"}
    restorationish = {"facility_reopening", "service_restoration"}
    if title_event in closureish and body_event in closureish:
        return True
    if title_event in restorationish and body_event in restorationish:
        return True
    return False


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.casefold())
        if token not in ALIGNMENT_STOPWORDS
    }


def _alignment_score(reference_text: str, sentence: str) -> int:
    return len(_meaningful_tokens(reference_text) & _meaningful_tokens(sentence))


def _keyword_hits(text: str, patterns: Iterable[tuple[str, re.Pattern[str]]]) -> list[str]:
    hits = []
    for label, pattern in patterns:
        if pattern.search(text):
            hits.append(label)
    return hits


def _sentence_candidates(text: str) -> list[str]:
    prepared = re.sub(r"[\r\n]+", " ", text)
    prepared = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.\s+(\d)", r"\1 \2", prepared)
    sentences = re.split(r"(?<=[.!?])\s+", prepared)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _service_line_from_text(text: str) -> str:
    for service_line, pattern in SERVICE_LINE_PATTERNS:
        if pattern.search(text):
            return service_line
    return ""


def _facility_type_from_text(text: str) -> str:
    for facility_type, pattern in FACILITY_TYPE_HINTS:
        if pattern.search(text):
            return facility_type
    return ""


def _event_type_from_text(text: str, *, service_line: str) -> str:
    hits = _keyword_hits(text, [(event_type, pattern) for event_type, _, pattern in POSITIVE_EVENT_PATTERNS])
    if not hits:
        return ""
    for preferred in ("facility_reopening", "service_restoration", "temporary_facility_suspension", "service_suspension", "hours_reduction", "capacity_reduction", "service_reduction"):
        if preferred in hits:
            return preferred
    for event_type in hits:
        if event_type in {"service_closure", "service_suspension", "service_reduction", "hours_reduction"} and service_line:
            return event_type
        if event_type in {"facility_closure", "planned_facility_closure", "temporary_facility_suspension", "facility_reopening", "facility_relocation", "facility_conversion"}:
            return event_type
    return hits[0]


def _is_restoration_without_prior_loss_context(text: str, event_type: str) -> bool:
    if event_type not in {"facility_reopening", "service_restoration"}:
        return False
    return not re.search(r"\b(after|following|resume|restore|reopen|reopened|reopening)\b", text, re.I) or not re.search(r"\b(close|closed|closure|shut|shutdown|halt|halted|suspend|suspended|loss)\b", text, re.I)


def _is_service_expansion_only(text: str) -> bool:
    return bool(re.search(r"\b(expand(?:ed|ing|s)?|open(?:ed|ing|s)? new|launch(?:ed|es|ing)? new)\b", text, re.I))


CARE_LINE_PREFILTER_NOISE_REASONS = {
    "award_or_fundraising",
    "construction_without_access_consequence",
    "general_healthcare_news",
    "hiring_or_biography",
    "marketing_announcement",
    "non_care_line",
    "policy_commentary_only",
    "resource_listing",
    "routine_operations_only",
}
CARE_LINE_PREFILTER_PRIOR_LOSS_EVENT_TYPES = {
    "bankruptcy_service_impact",
    "capacity_reduction",
    "facility_closure",
    "facility_conversion",
    "facility_relocation",
    "hours_reduction",
    "planned_facility_closure",
    "service_closure",
    "service_reduction",
    "service_suspension",
    "temporary_facility_suspension",
}
CARE_LINE_PREFILTER_ACCESS_TERMS = (
    "access",
    "closure",
    "closed",
    "closing",
    "emergency department",
    "emergency room",
    "er ",
    " ed ",
    "hospital",
    "clinic",
    "maternity",
    "ob ",
    "labor and delivery",
    "dialysis",
    "oncology",
    "behavioral health",
    "health center",
    "service reduction",
    "service suspension",
    "service closure",
    "longer travel",
    "farther travel",
    "replacement",
    "reopen",
    "reopening",
    "restore",
    "restoration",
    "relocat",
    "delay",
    "effective date",
    "effective",
)


def _care_line_context_signature(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    facility = _care_line_identity_text(_text(mapping, "facility_name", "provider_name", "affected_provider", "organization_name"))
    provider = _care_line_identity_text(_text(mapping, "provider_name", "affected_provider", "organization_name"))
    city = _care_line_identity_text(_text(mapping, "city", "locality_name"))
    county = _care_line_identity_text(_text(mapping, "county", "county_equivalent_name"))
    state = _care_line_identity_text(_text(mapping, "state", "jurisdiction_display"))
    service_line = _care_line_identity_text(_text(mapping, "service_line", "service_line_raw", "affected_service_line"))
    variants = [
        tuple(part for part in ("facility", facility, city, county, state) if part),
        tuple(part for part in ("facility", facility, city, county, state, service_line) if part),
        tuple(part for part in ("provider", provider, city, county, state) if part),
        tuple(part for part in ("provider", provider, city, county, state, service_line) if part),
        tuple(part for part in ("geography", city, county, state) if part),
        tuple(part for part in ("geography", city, county, state, service_line) if part),
    ]
    return tuple("::".join(parts) for parts in variants if len(parts) > 1)


def _care_line_history_matches(
    reviewed_records: Iterable[CareLineReviewedRecord | Mapping[str, Any]],
    raw_item: Mapping[str, Any],
    *,
    service_line: str = "",
    event_type: str = "",
) -> list[dict[str, Any]]:
    candidate = dict(raw_item)
    if service_line and not _text(candidate, "service_line", "service_line_raw", "affected_service_line"):
        candidate["service_line"] = service_line
    if event_type and not _text(candidate, "event_type", "event_type_raw", "canonical_event_type"):
        candidate["event_type"] = event_type
    candidate_keys = set(_care_line_context_signature(candidate))
    if not candidate_keys:
        return []
    matches: list[dict[str, Any]] = []
    for record in reviewed_records:
        mapping = record if isinstance(record, Mapping) else record.model_dump(mode="json")
        record_event_type = _text(mapping, "event_type", "event_type_raw", "canonical_event_type")
        if record_event_type not in CARE_LINE_PREFILTER_PRIOR_LOSS_EVENT_TYPES:
            continue
        record_keys = set(_care_line_context_signature(mapping))
        if not record_keys.intersection(candidate_keys):
            continue
        matches.append(
            {
                "reviewed_record_id": _text(mapping, "producer_record_id", "source_record_id", "care_line_record_id"),
                "event_identity": care_line_event_identity(mapping),
                "event_type": record_event_type,
                "lifecycle_status": care_line_lifecycle_status(mapping),
                "facility_name": _text(mapping, "facility_name", "provider_name", "affected_provider", "organization_name"),
                "service_line": _text(mapping, "service_line", "service_line_raw", "affected_service_line"),
                "city": _text(mapping, "city", "locality_name"),
                "county": _text(mapping, "county", "county_equivalent_name"),
                "state": _text(mapping, "state", "jurisdiction_display"),
            }
        )
    return matches


def _care_line_prefilter_signals(raw_item: Mapping[str, Any], lead: Mapping[str, Any], history_matches: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    positive_signals = list(dict.fromkeys([str(value) for value in (lead.get("positive_hits") or []) if str(value).strip()] + [str(value) for value in (lead.get("context_hits") or []) if str(value).strip()]))
    negative_reason = _text(lead, "exclusion_reason")
    negative_signals = [negative_reason] if negative_reason else []
    if history_matches:
        positive_signals.append("prior_loss_context")
    title = f"{_text(raw_item, 'title')} {_text(raw_item, 'description')}".casefold()
    if any(term in title for term in CARE_LINE_PREFILTER_ACCESS_TERMS):
        positive_signals.append("access_impact_terms")
    positive_signals = list(dict.fromkeys(value for value in positive_signals if value))
    negative_signals = list(dict.fromkeys(value for value in negative_signals if value))
    return positive_signals, negative_signals


def _care_line_access_prefilter(
    raw_item: Mapping[str, Any],
    lead: Mapping[str, Any],
    *,
    reviewed_records: Iterable[CareLineReviewedRecord | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    service_line = _text(lead, "service_line_hint")
    event_type = _text(lead, "event_type_hint")
    history_matches = _care_line_history_matches(reviewed_records, raw_item, service_line=service_line, event_type=event_type)
    positive_signals, negative_signals = _care_line_prefilter_signals(raw_item, lead, history_matches)
    exclusion_reason = _text(lead, "exclusion_reason")
    prefilter_decision = "escalate_to_full_review"
    normalized_reason = "access_impact_plausible"
    confidence = 0.9 if history_matches else 0.75 if positive_signals else 0.35
    title_text = f"{_text(raw_item, 'title')} {_text(raw_item, 'description')}".casefold()
    if _is_service_expansion_only(title_text) and not history_matches:
        prefilter_decision = "discard"
        normalized_reason = "service_expansion_without_prior_loss_context"
        confidence = 0.18
    elif _is_service_expansion_only(title_text) and history_matches:
        normalized_reason = "prior_loss_context_detected"
        confidence = 0.92
    if exclusion_reason == "service_expansion_without_prior_loss_context":
        if history_matches:
            normalized_reason = "prior_loss_context_detected"
            confidence = 0.92
        else:
            prefilter_decision = "discard"
            normalized_reason = "service_expansion_without_prior_loss_context"
            confidence = 0.18
    elif exclusion_reason in CARE_LINE_PREFILTER_NOISE_REASONS:
        if history_matches:
            normalized_reason = "prior_loss_context_detected"
            confidence = 0.88
        elif positive_signals:
            normalized_reason = "access_impact_plausible"
            confidence = 0.7
        else:
            prefilter_decision = "discard"
            normalized_reason = exclusion_reason or "no_access_impact"
            confidence = 0.12
    elif not positive_signals and not history_matches and exclusion_reason:
        prefilter_decision = "discard"
        normalized_reason = exclusion_reason
        confidence = 0.2
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "raw_item_id": _text(raw_item, "raw_item_id"),
        "lead_id": _text(lead, "lead_id"),
        "source_id": _text(raw_item, "source_id"),
        "source_name": _text(raw_item, "source_name"),
        "item_url": _text(raw_item, "item_url"),
        "title": _text(raw_item, "title"),
        "source_publication_date": _text(raw_item, "source_publication_date"),
        "prefilter_decision": prefilter_decision,
        "normalized_reason": normalized_reason,
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
        "confidence": round(confidence, 3),
        "escalated_to_full_review": prefilter_decision == "escalate_to_full_review",
        "history_match_count": len(history_matches),
        "history_matches": history_matches[:10],
    }


def _extract_subject(title: str, passage: str, *, service_line: str) -> tuple[str, str]:
    patterns = [
        re.compile(r"^(?P<subject>.+?)\s+(?:will\s+)?(?:close|closing|closes|shut(?:ting)? down|suspend(?:s|ed|ing)?|halt(?:s|ed|ing)?|end(?:s|ed|ing)?|reduce(?:s|d|ing)? hours?|reopen(?:s|ed|ing)?|restore(?:s|d|ing)?)\b", re.I),
        re.compile(r"^(?P<subject>.+?)\s+(?:announced?|plans?|planned)\s+to\s+(?:close|suspend|end|reduce|reopen|restore)\b", re.I),
    ]
    for pattern in patterns:
        match = pattern.search(title.strip())
        if match:
            subject = match.group("subject").strip(" -:")
            if re.search(r"\b(judge|court|request|vote|lawsuit|appeal)\b", subject, re.I):
                continue
            return subject, subject
    facility_match = re.search(
        r"\b([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,6}\s+"
        r"(?:Hospital|Clinic|Medical Center|Health Center|Health System|Healthcare System|Children's Hospital|Center))\b",
        title + " " + passage,
        re.I,
    )
    if facility_match:
        subject = facility_match.group(1).strip()
        return subject, subject
    provider_match = re.search(
        r"\b([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,6}\s+(?:System|Network|Services))\b",
        title + " " + passage,
        re.I,
    )
    if provider_match:
        provider = provider_match.group(1).strip()
        return "", provider
    service_subject_match = re.search(
        r"\b((?:[A-Z][A-Za-z0-9&'.-]+\s+){0,4}(?:labor and delivery|labor & delivery|maternity ward|maternity unit|birthing center|birth center|maternity|labor and delivery unit|labor and delivery services))\b",
        title + " " + passage,
        re.I,
    )
    if service_subject_match:
        subject = service_subject_match.group(1).strip(" -:")
        return subject, subject
    if service_line:
        return "", ""
    return "", ""


def _extract_geography(raw_item: Mapping[str, Any], text: str) -> dict[str, str]:
    state = _text(raw_item, "source_state")
    jurisdiction_display = JURISDICTIONS_BY_CODE.get(state, {}).get("display", state)
    match_city_state = re.search(r"\b([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}),\s*([A-Z]{2})\b", text)
    if match_city_state:
        city = match_city_state.group(1).strip()
        explicit_state = match_city_state.group(2).strip().upper()
        if explicit_state in JURISDICTIONS_BY_CODE:
            return {
                "state": explicit_state,
                "city": city,
                "geographic_scope": "city",
                "jurisdiction_display": JURISDICTIONS_BY_CODE[explicit_state]["display"],
                "service_region": "",
            }
    lowered = text.casefold()
    for alias, entry in JURISDICTION_ALIASES.items():
        if len(alias) < 3:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", lowered, re.I):
            state = entry["code"]
            jurisdiction_display = entry["display"]
            break
    service_region = ""
    region_match = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+(?:region|area|service area|service region))\b", text)
    if region_match:
        service_region = region_match.group(1).strip()
    geographic_scope = "service_region" if service_region else "statewide" if state else ""
    return {
        "state": state,
        "city": "",
        "geographic_scope": geographic_scope,
        "jurisdiction_display": jurisdiction_display or state,
        "service_region": service_region,
    }


def _permanence_from_text(text: str, event_type: str) -> str:
    lowered = text.lower()
    if event_type in {"temporary_facility_suspension", "service_suspension"} or "temporary" in lowered:
        return "temporary"
    if event_type in {"facility_reopening", "service_restoration"}:
        return "restoration"
    if event_type:
        return "permanent" if re.search(r"\b(permanent|permanently)\b", lowered) or event_type not in {"hours_reduction", "capacity_reduction", "service_reduction"} else "temporary_or_unknown"
    return ""


def _access_consequences_from_text(text: str, event_type: str) -> tuple[list[str], str]:
    consequences = []
    for value, pattern in ACCESS_CONSEQUENCE_PATTERNS:
        if pattern.search(text) and value not in consequences:
            consequences.append(value)
    if consequences:
        return consequences, ""
    if event_type in {"facility_closure", "planned_facility_closure", "service_closure"}:
        return ["LOSS_OF_LOCAL_ACCESS"], "direct_service_loss_event"
    if event_type in {"service_suspension", "temporary_facility_suspension"}:
        return ["REDUCED_SERVICE_AVAILABILITY"], "direct_service_loss_event"
    if event_type == "hours_reduction":
        return ["REDUCED_OPERATING_HOURS"], "direct_service_loss_event"
    if event_type in {"capacity_reduction", "service_reduction", "bankruptcy_service_impact"}:
        return ["REDUCED_SERVICE_AVAILABILITY"], "direct_service_loss_event"
    if re.search(r"\b(shutting down inpatient beds|closed inpatient beds|ending inpatient beds)\b", text, re.I):
        return ["REDUCED_BED_OR_APPOINTMENT_CAPACITY"], "inherent_capacity_loss"
    if re.search(r"\b(stop admissions|stopped admissions)\b", text, re.I):
        return ["REDUCED_SERVICE_AVAILABILITY"], "inherent_service_loss"
    if event_type in {"facility_reopening", "service_restoration"}:
        return ["SUBSTITUTE_SERVICE_OFFERED"], "restoration_event"
    return [], ""


def _supporting_passage(text: str, event_type: str, service_line: str) -> str:
    if not text:
        return ""
    sentences = _sentence_candidates(text)
    scoring_terms = [
        pattern
        for _, pattern in SERVICE_LINE_PATTERNS
    ] + [
        pattern
        for _, _, pattern in POSITIVE_EVENT_PATTERNS
    ]
    best = ""
    best_score = -1
    for index, sentence in enumerate(sentences):
        score = 0
        for pattern in scoring_terms:
            if pattern.search(sentence):
                score += 2
        for _, pattern in ACCESS_CONSEQUENCE_PATTERNS:
            if pattern.search(sentence):
                score += 3
        if re.search(r"\b(because|due to|after|until|patients|staff|beds|appointments?)\b", sentence, re.I):
            score += 2
        if event_type and event_type.replace("_", " ")[:6].lower() in sentence.lower():
            score += 1
        if service_line and service_line.replace("_", " ")[:5].lower() in sentence.lower():
            score += 1
        if len(sentence.split()) <= 8:
            score -= 1
        if score > best_score or (score == best_score and len(sentence) > len(best)):
            best = sentence
            best_score = score
            if index + 1 < len(sentences) and best_score >= 2:
                follow = sentences[index + 1]
                if any(pattern.search(follow) for _, pattern in ACCESS_CONSEQUENCE_PATTERNS):
                    best = f"{sentence} {follow}".strip()
    if best_score <= 0:
        return ""
    return best[:500].strip()


def _supports_review_without_full_article(
    *,
    supporting_passage: str,
    event_type: str,
    service_line: str,
    subject: str,
    provider: str,
    access_consequences: list[str],
) -> bool:
    if not supporting_passage.strip():
        return False
    if event_type not in {"facility_closure", "planned_facility_closure", "service_closure", "service_suspension", "hours_reduction", "capacity_reduction", "service_reduction", "facility_reopening", "service_restoration"}:
        return False
    if not (subject or provider or service_line):
        return False
    if not access_consequences:
        return False
    lowered = supporting_passage.casefold()
    return bool(
        re.search(r"\b(close|closing|closed|end|ending|suspend|suspended|halt|halted|cut|reducing|reduce|reopen|reopened|restore|restored|transfer|move)\b", lowered)
        and (re.search(r"\b(hospital|clinic|center|ward|unit|department|service|services)\b", lowered) or service_line)
    )


def _sentence_currentness(
    sentence: str,
    *,
    source_date: date | None,
    event_type: str,
    service_line: str,
) -> dict[str, Any]:
    lowered = sentence.casefold()
    positive_hits = _keyword_hits(sentence, [(label, pattern) for label, _, pattern in POSITIVE_EVENT_PATTERNS])
    access_hits = _keyword_hits(sentence, ACCESS_CONSEQUENCE_PATTERNS)
    context_hits = _keyword_hits(sentence, HEALTHCARE_CONTEXT_PATTERNS)
    date_refs = _all_date_references(sentence, source_date=source_date)
    has_historical_marker = any(marker in lowered for marker in HISTORICAL_MARKERS) or bool(re.search(r"\bthe hospital closed in\b|\bclosed in 20\d{2}\b|\bpreviously\b", lowered))
    has_recent_background_marker = any(marker in lowered for marker in RECENT_BACKGROUND_MARKERS)
    has_current_announcement = any(marker in lowered for marker in CURRENT_ANNOUNCEMENT_MARKERS) or bool(re.search(r"\bannounced\b|\bsaid\b|\bwill\b|\bplans?\b", lowered))
    has_ongoing_update = any(marker in lowered for marker in ONGOING_UPDATE_MARKERS)
    has_retro_marker = any(marker in lowered for marker in RETROSPECTIVE_MARKERS)
    actionable_event = bool(ACTIONABLE_EVENT_PATTERN.search(sentence))
    explicit_historical = any(ref.get("relation") == "historical" for ref in date_refs)
    explicit_future = any(ref.get("relation") == "future" for ref in date_refs)
    recent_past = any(ref.get("relation") == "recent_past" for ref in date_refs)
    score = 0
    score += len(positive_hits) * 3
    score += len(access_hits) * 2
    score += len(context_hits)
    if service_line and _service_line_from_text(sentence) == service_line:
        score += 1
    if has_current_announcement:
        score += 3
    if has_ongoing_update:
        score += 3
    if explicit_future:
        score += 4
    if event_type in {"facility_reopening", "service_restoration"}:
        score += 2
    if has_historical_marker:
        score -= 5
    if explicit_historical:
        score -= 6
    if has_retro_marker:
        score -= 4
    if has_recent_background_marker:
        score -= 2
    currentness_class = "DATE_UNRESOLVED"
    freshness_role = "UNKNOWN"
    operative_date = ""
    prior_event_date = ""
    reasoning_parts: list[str] = []
    if date_refs:
        operative_date = next((ref["date"] for ref in date_refs if ref.get("relation") in {"future", "current", "recent_past", "current_or_future"} and ref.get("date")), "")
        prior_event_date = next((ref["date"] for ref in date_refs if ref.get("relation") == "historical" and ref.get("date")), "")
    if event_type in {"facility_reopening", "service_restoration"} and (has_current_announcement or explicit_future or recent_past):
        currentness_class = "CURRENT_RESTORATION"
        freshness_role = "RESTORATION_UPDATE"
        reasoning_parts.append("restoration language with current update")
    elif explicit_future and explicit_historical:
        currentness_class = "DATE_UNRESOLVED"
        freshness_role = "UNKNOWN"
        reasoning_parts.append("conflicting future and historical date signals")
    elif actionable_event and (explicit_future or bool(re.search(rf"\b(?:on|effective|beginning|starting)\s+(?:{MONTH_NAME_PATTERN})\b", sentence, re.I))):
        currentness_class = "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE"
        freshness_role = "FUTURE_EFFECTIVE"
        reasoning_parts.append("future effective date in operative sentence")
    elif has_ongoing_update:
        currentness_class = "CURRENT_UPDATE_TO_PRIOR_EVENT"
        freshness_role = "ONGOING_EVENT_UPDATE"
        reasoning_parts.append("current article confirms continuing interruption")
    elif has_historical_marker or explicit_historical:
        currentness_class = "HISTORICAL_BACKGROUND"
        freshness_role = "HISTORICAL_CONTEXT"
        reasoning_parts.append("historical closure/reference markers")
    elif has_recent_background_marker or recent_past:
        currentness_class = "RECENT_BACKGROUND"
        freshness_role = "RECENT_CONTEXT"
        reasoning_parts.append("recent prior event used as context")
    elif has_retro_marker and not has_current_announcement:
        currentness_class = "RETROSPECTIVE_ANALYSIS"
        freshness_role = "HISTORICAL_CONTEXT"
        reasoning_parts.append("retrospective or policy analysis framing")
    elif actionable_event and positive_hits and context_hits and (has_current_announcement or source_date is not None):
        currentness_class = "CURRENT_EVENT"
        freshness_role = "BREAKING" if source_date and any(token in lowered for token in ("today", "tonight", "this week")) else "CURRENT"
        operative_date = operative_date or (source_date.isoformat() if source_date else "")
        reasoning_parts.append("current announcement or current operational event")
    elif positive_hits and not actionable_event:
        currentness_class = "RETROSPECTIVE_ANALYSIS" if has_retro_marker or has_historical_marker else "DATE_UNRESOLVED"
        freshness_role = "HISTORICAL_CONTEXT" if currentness_class == "RETROSPECTIVE_ANALYSIS" else "UNKNOWN"
        reasoning_parts.append("event noun appears without operative event action")
    confidence = 0.35 + min(0.55, max(score, 0) * 0.05)
    if currentness_class == "DATE_UNRESOLVED":
        confidence = 0.4 if positive_hits else 0.2
        reasoning_parts.append("date relationship unresolved")
    return {
        "sentence": sentence,
        "score": score,
        "currentness_class": currentness_class,
        "freshness_role": freshness_role,
        "operative_event_date": operative_date,
        "prior_event_date": prior_event_date,
        "date_references": date_refs,
        "reasoning": "; ".join(reasoning_parts),
        "confidence": round(min(confidence, 0.95), 3),
        "positive_hits": positive_hits,
        "access_hits": access_hits,
    }


def _currentness_analysis(
    *,
    title: str,
    lead_text: str,
    text: str,
    source_publication_date: str,
    event_type: str,
    service_line: str,
) -> dict[str, Any]:
    source_date = _parse_iso_date(source_publication_date)
    sentences = _sentence_candidates(text)
    sentence_rows = [
        _sentence_currentness(sentence, source_date=source_date, event_type=event_type, service_line=service_line)
        for sentence in sentences
        if _keyword_hits(sentence, [(label, pattern) for label, _, pattern in POSITIVE_EVENT_PATTERNS])
        or _keyword_hits(sentence, ACCESS_CONSEQUENCE_PATTERNS)
    ]
    title_agrees = _title_body_event_agreement(title, text, service_line=service_line)
    reference_text = _normalize_text(f"{title} {lead_text}")
    class_rank = {
        "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE": 4,
        "CURRENT_RESTORATION": 4,
        "CURRENT_UPDATE_TO_PRIOR_EVENT": 3,
        "CURRENT_EVENT": 3,
        "DATE_UNRESOLVED": 2,
        "RECENT_BACKGROUND": 1,
        "HISTORICAL_BACKGROUND": 0,
        "RETROSPECTIVE_ANALYSIS": 0,
    }
    operative = max(sentence_rows, key=lambda row: (class_rank.get(row["currentness_class"], -1), row["score"], len(row["sentence"]))) if sentence_rows else None
    background_rows = [
        row for row in sentence_rows
        if row is not operative and row["currentness_class"] in {"RECENT_BACKGROUND", "HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}
    ]
    title_lowered = title.casefold()
    body_lowered = text.casefold()
    article_retro = any(marker in title_lowered or marker in body_lowered for marker in RETROSPECTIVE_MARKERS)
    failed_gates: list[str] = []
    currentness_class = operative["currentness_class"] if operative else "DATE_UNRESOLVED"
    freshness_role = operative["freshness_role"] if operative else "UNKNOWN"
    if operative and currentness_class in {"HISTORICAL_BACKGROUND", "RECENT_BACKGROUND"} and article_retro:
        currentness_class = "RETROSPECTIVE_ANALYSIS"
        freshness_role = "HISTORICAL_CONTEXT"
    if operative and currentness_class == "CURRENT_EVENT" and not title_agrees and operative["currentness_class"] not in {"CURRENT_RESTORATION", "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE"}:
        currentness_class = "DATE_UNRESOLVED"
        freshness_role = "UNKNOWN"
        failed_gates.append("title_body_conflict")
    if operative and currentness_class in {"CURRENT_EVENT", "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE", "CURRENT_UPDATE_TO_PRIOR_EVENT", "CURRENT_RESTORATION"}:
        if _alignment_score(reference_text, operative["sentence"]) == 0 and len(background_rows) >= 2:
            currentness_class = "RETROSPECTIVE_ANALYSIS"
            freshness_role = "HISTORICAL_CONTEXT"
            failed_gates.append("lead_alignment_missing")
    elif operative and currentness_class in {"RECENT_BACKGROUND", "HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}:
        failed_gates.append("historical_context_only")
    else:
        failed_gates.append("currentness_unresolved")
    if operative and currentness_class == "CURRENT_EVENT":
        past_refs = [ref for ref in operative["date_references"] if ref.get("relation") == "historical"]
        current_refs = [ref for ref in operative["date_references"] if ref.get("relation") in {"future", "current", "recent_past", "current_or_future"}]
        if past_refs and not current_refs and "announced" not in operative["sentence"].casefold():
            currentness_class = "HISTORICAL_BACKGROUND"
            freshness_role = "HISTORICAL_CONTEXT"
            failed_gates.append("historical_date_only")
    background_dates = []
    for row in background_rows:
        for ref in row["date_references"]:
            if ref.get("date") or ref.get("label"):
                background_dates.append(ref.get("date") or ref.get("label"))
    return {
        "source_publication_date": source_publication_date,
        "event_announcement_date": source_publication_date if currentness_class in {"CURRENT_EVENT", "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE", "CURRENT_RESTORATION"} else "",
        "event_effective_date": operative["operative_event_date"] if operative and currentness_class == "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE" else "",
        "observed_date": source_publication_date if currentness_class == "CURRENT_UPDATE_TO_PRIOR_EVENT" else "",
        "retrieval_date": utc_now().split("T", 1)[0],
        "prior_event_date": operative["prior_event_date"] if operative else "",
        "currentness_class": currentness_class,
        "freshness_role": freshness_role,
        "operative_event_passage": operative["sentence"] if operative else "",
        "operative_event_date": operative["operative_event_date"] if operative else "",
        "background_event_passages": [row["sentence"] for row in background_rows],
        "background_date_references": background_dates,
        "title_body_agree": title_agrees,
        "currentness_confidence": operative["confidence"] if operative else 0.0,
        "currentness_reasoning": operative["reasoning"] if operative else "no operative healthcare-access event sentence resolved",
        "currentness_failed_gates": failed_gates,
        "sentence_analyses": sentence_rows,
    }


def _can_fetch_item_url(source: CareLineSource, url: str) -> bool:
    if not url or not source.item_permalink_available:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    allowed = {value.lower() for value in source.allowed_hosts if value}
    if allowed and host not in allowed:
        return False
    if not allowed:
        source_host = (urlparse(source.feed_url).hostname or "").lower()
        if source_host and host != source_host:
            return False
    return True


def _extract_article_content(
    source: CareLineSource,
    html_payload: bytes,
    *,
    source_url: str,
    response_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    content_type = _text(response_meta or {}, "content_type").casefold()
    if "pdf" in content_type or source_url.lower().endswith(".pdf"):
        return {
            "title": "",
            "description": "",
            "text": "",
            "extraction_method": "pdf_required",
            "extraction_outcome": "PDF_REQUIRED",
            "content_hash": stable_json_hash([source_url, "pdf_required"]),
            "source_format_family": SOURCE_FORMAT_FAMILIES.get(source.adapter_type, source.adapter_type.upper()),
        }

    decoded = html_payload.decode("utf-8", errors="ignore")
    if not decoded.strip():
        return {
            "title": "",
            "description": "",
            "text": "",
            "extraction_method": "empty_response",
            "extraction_outcome": "EMPTY_RESPONSE",
            "content_hash": stable_json_hash([source_url, "empty_response"]),
            "source_format_family": SOURCE_FORMAT_FAMILIES.get(source.adapter_type, source.adapter_type.upper()),
        }

    extractor = _ScopedArticleExtractor(_source_selector_hints(source))
    extractor.feed(decoded)
    json_ld = _json_ld_article_fields(extractor.json_ld_text())
    selector_text = _clean_extracted_text(extractor.scoped_text())
    article_text = _clean_extracted_text(extractor.article_text())
    full_text = _clean_extracted_text(extractor.full_text())
    body_text = _first_nonempty(json_ld["text"], selector_text, article_text, full_text)
    description = _first_nonempty(json_ld["description"], extractor.og_description, extractor.meta_description)
    title = _first_nonempty(json_ld["title"], extractor.og_title, extractor.meta_title, extractor.title)
    lowered_full = full_text.casefold()
    if any(hint in lowered_full for hint in PAYWALL_HINTS):
        outcome = "PAYWALLED"
        method = "paywall_detection"
        text = ""
    elif body_text and len(body_text) >= 80:
        outcome = "BODY_EXTRACTED"
        method = "json_ld" if json_ld["text"] else "selector" if selector_text else "semantic_html"
        text = body_text
    elif article_text and len(article_text) >= 80:
        outcome = "PARTIAL_BODY"
        method = "semantic_html_partial"
        text = article_text
    elif description:
        outcome = "PARTIAL_BODY"
        method = "metadata_summary"
        text = _bounded_text(description, max_chars=1200)
    elif title:
        outcome = "HEADLINE_ONLY"
        method = "headline_only"
        text = ""
    else:
        outcome = "PARSE_FAILED" if full_text else "EMPTY_RESPONSE"
        method = "generic_parse_failure" if full_text else "empty_response"
        text = ""
    return {
        "title": _bounded_text(title, max_chars=500),
        "description": _bounded_text(description, max_chars=1200),
        "text": text,
        "full_text": _bounded_text(full_text),
        "selector_text": _bounded_text(selector_text),
        "article_text": _bounded_text(article_text),
        "json_ld_text": _bounded_text(json_ld["text"]),
        "extraction_method": method,
        "extraction_outcome": outcome,
        "content_hash": sha256(decoded.encode("utf-8", errors="ignore")).hexdigest(),
        "source_format_family": SOURCE_FORMAT_FAMILIES.get(source.adapter_type, source.adapter_type.upper()),
    }


def _evidence_blob(raw_item: Mapping[str, Any], article_content: Mapping[str, Any] | None) -> str:
    parts = [
        _text(raw_item, "title"),
        _text(raw_item, "description"),
        _text(article_content or {}, "description"),
        _text(article_content or {}, "text"),
        _text(article_content or {}, "article_text"),
        _text(article_content or {}, "selector_text"),
    ]
    return "\n".join(part for part in parts if part)


def _negative_classification(text: str, *, positive_hits: list[str], context_hits: list[str]) -> str:
    negative_hits = _keyword_hits(text, NEGATIVE_PATTERNS)
    if not positive_hits:
        if "award_or_fundraising" in negative_hits:
            return "award_or_fundraising"
        if "marketing_announcement" in negative_hits:
            return "marketing_announcement"
        if "hiring_or_biography" in negative_hits:
            return "hiring_or_biography"
        if "resource_listing" in negative_hits:
            return "resource_listing"
        if "research_or_health_advice" in negative_hits:
            return "general_healthcare_news"
        if "general_healthcare_news" in negative_hits:
            return "general_healthcare_news"
        return "non_care_line"
    if "award_or_fundraising" in negative_hits:
        return "award_or_fundraising"
    if "marketing_announcement" in negative_hits:
        return "marketing_announcement"
    if "hiring_or_biography" in negative_hits:
        return "hiring_or_biography"
    if "policy_commentary_only" in negative_hits and not context_hits:
        return "policy_commentary_only"
    if "construction_without_access_consequence" in negative_hits and not re.search(r"\b(close|loss|shutdown|suspend)\b", text, re.I):
        return "construction_without_access_consequence"
    if "financial_distress_without_access_consequence" in negative_hits and not re.search(r"\b(close|service|patient|access|admissions?)\b", text, re.I):
        return "financial_distress_without_access_consequence"
    return ""


def event_lead_from_raw_item(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    title = _text(raw_item, "title")
    description = _text(raw_item, "description")
    combined = _normalize_text(f"{title} {description[:350]}")
    positive_hits = _keyword_hits(combined, [(label, pattern) for label, _, pattern in POSITIVE_EVENT_PATTERNS])
    context_hits = _keyword_hits(combined, HEALTHCARE_CONTEXT_PATTERNS)
    negative_reason = _negative_classification(combined, positive_hits=positive_hits, context_hits=context_hits)
    lead_score = len(positive_hits) * 3 + len(context_hits) * 2 - (2 if negative_reason else 0)
    qualifies = bool(positive_hits and context_hits and not negative_reason and lead_score >= 5)
    service_line = _service_line_from_text(combined)
    event_type = _event_type_from_text(combined, service_line=service_line)
    if _is_service_expansion_only(combined) and event_type not in {"facility_reopening", "service_restoration"}:
        negative_reason = "service_expansion_without_prior_loss_context"
        qualifies = False
    if _is_restoration_without_prior_loss_context(combined, event_type):
        negative_reason = "service_expansion_without_prior_loss_context"
        qualifies = False
    if event_type == "bankruptcy_service_impact" and not re.search(r"\b(close|suspend|service|patient|access)\b", combined, re.I):
        negative_reason = "financial_distress_without_access_consequence"
        qualifies = False
    return {
        "schema_version": EVENT_LEAD_SCHEMA_VERSION,
        "lead_id": _stable_id("care-line-event-lead", raw_item.get("raw_item_id", ""), title, event_type),
        "raw_item_id": raw_item.get("raw_item_id", ""),
        "source_id": raw_item.get("source_id", ""),
        "source_name": raw_item.get("source_name", ""),
        "item_url": raw_item.get("item_url", ""),
        "title": title,
        "description": description,
        "positive_hits": positive_hits,
        "context_hits": context_hits,
        "lead_score": lead_score,
        "event_type_hint": event_type,
        "service_line_hint": service_line,
        "qualification_status": "event_lead" if qualifies else "excluded",
        "exclusion_reason": negative_reason or ("non_care_line" if not positive_hits or not context_hits else ""),
        "full_article_required": bool(raw_item.get("requires_html_followup")) or not description,
        "public_eligibility_precheck": False,
        "created_at": utc_now(),
    }


def _make_provenance(value: Any, *, source_field: str, supporting_text: str, provenance_type: str = "structured_input", review_status: str = "confirmed", confidence: float = 1.0) -> FieldProvenance:
    return FieldProvenance(
        value=value,
        provenance_type=provenance_type,
        source_field=source_field,
        supporting_text=supporting_text,
        confidence=confidence,
        review_status=review_status,  # type: ignore[arg-type]
    )


def review_priority(reviewed: CareLineReviewedRecord) -> tuple[str, str]:
    text = " ".join([
        reviewed.supporting_passage,
        reviewed.claim_summary,
        reviewed.review_notes,
        reviewed.source_title,
    ])
    isolated = bool(re.search(r"\b(island|only hospital|sole hospital|only emergency department)\b", text, re.I))
    if reviewed.event_type in {"facility_closure", "planned_facility_closure"}:
        if reviewed.event_type == "facility_closure":
            return "CRITICAL", "facility closure"
        return "HIGH", "announced facility closure"
    if reviewed.event_type in {"service_closure", "service_suspension"} and reviewed.service_line in {"emergency_care", "labor_and_delivery", "maternity", "ambulance_ems"}:
        return "CRITICAL", "major current service loss"
    if reviewed.event_type in {"temporary_facility_suspension", "bankruptcy_service_impact"}:
        return "CRITICAL" if isolated else "HIGH", "current operational interruption"
    if reviewed.event_type in {"service_closure", "service_suspension"}:
        return "HIGH", "major service-line access loss"
    if reviewed.event_type in {"capacity_reduction", "service_reduction"}:
        return "HIGH", "material access reduction"
    if reviewed.event_type in {"hours_reduction", "facility_relocation", "facility_conversion", "facility_reopening", "service_restoration"}:
        return "STANDARD", "qualifying but lower urgency operational change"
    return "LOW", "qualifying low-impact item"


def _evidence_level(article_content: Mapping[str, Any] | None, raw_item: Mapping[str, Any]) -> str:
    if article_content and _text(article_content, "text"):
        return "article_excerpt"
    if _text(raw_item, "description"):
        return "feed_summary"
    return "metadata_only"


def _subject_or_provider(subject: str, text: str) -> tuple[str, str]:
    if not subject:
        return "", ""
    if re.search(r"\b(hospital|clinic|center)\b", subject, re.I):
        return subject, subject
    if re.search(r"\b(system|health)\b", subject, re.I):
        return "", subject
    return subject, subject


def stable_candidate_id(raw_item: Mapping[str, Any], reviewed: CareLineReviewedRecord) -> str:
    return _stable_id(
        "care_line_candidate",
        _text(raw_item, "source_id"),
        reviewed.source_url,
        reviewed.event_type,
        reviewed.service_line,
        reviewed.facility_name,
        reviewed.provider_name,
        reviewed.state,
        reviewed.city,
        reviewed.announcement_date,
        reviewed.effective_date,
    )


def _normalized_location_key(reviewed: CareLineReviewedRecord) -> str:
    return "|".join(
        value
        for value in (
            reviewed.state,
            reviewed.city or reviewed.locality_name,
            reviewed.county or reviewed.county_equivalent_name,
            reviewed.service_region,
            reviewed.tribal_service_area,
            reviewed.island_name,
        )
        if value
    )


def cluster_hints(reviewed: CareLineReviewedRecord, *, source_row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source_url = reviewed.source_url or _text(source_row or {}, "url", "canonical_url")
    source_host = ""
    if "://" in source_url:
        source_host = source_url.split("://", 1)[1].split("/", 1)[0].lower()
    subject = reviewed.facility_name or reviewed.provider_name or reviewed.parent_organization or reviewed.operator_name
    cluster_id = _stable_id(
        "care_line_cluster",
        subject.casefold(),
        reviewed.state,
        _normalized_location_key(reviewed).casefold(),
        reviewed.service_line,
        reviewed.event_type,
        reviewed.announcement_date,
        reviewed.effective_date,
    )
    payload = {
        "subject": subject.casefold(),
        "jurisdiction": reviewed.state,
        "location_key": _normalized_location_key(reviewed),
        "service_line": reviewed.service_line,
        "event_type": reviewed.event_type,
        "announcement_date": reviewed.announcement_date,
        "effective_date": reviewed.effective_date,
        "source_host": source_host,
        "source_url": source_url,
    }
    return {
        "schema_version": CLUSTERING_SCHEMA_VERSION,
        "cluster_id": cluster_id,
        "dedupe_key": stable_json_hash(payload),
        "same_system_cross_state_guard": f"{subject.casefold()}|{reviewed.state}",
        "announcement_effective_link_key": stable_json_hash(
            {
                "subject": subject.casefold(),
                "state": reviewed.state,
                "service_line": reviewed.service_line,
                "event_type": reviewed.event_type,
                "announcement_date": reviewed.announcement_date,
                "effective_date": reviewed.effective_date,
            }
        ),
    }


def normalize_candidate_record(
    raw_item: Mapping[str, Any],
    *,
    article_content: Mapping[str, Any] | None,
    supporting_passage: str,
    geography: Mapping[str, str],
    subject: str,
    provider: str,
    event_type: str,
    service_line: str,
    access_consequences: list[str],
    access_exception: str,
    artifact_path: str,
    run_id: str,
    qualification_status: str,
    failed_gates: list[str],
    exclusion_reason: str,
    extraction_confidence: float,
    full_article_required: bool,
    currentness: Mapping[str, Any],
) -> dict[str, Any]:
    facility_name, provider_name = _subject_or_provider(subject or provider, supporting_passage)
    evidence_blob = _evidence_blob(raw_item, article_content)
    source_title = _text(article_content or {}, "title") or _text(raw_item, "title")
    source_url = _text(raw_item, "item_url")
    source_date = _text(raw_item, "source_publication_date")
    currentness_date = _text(currentness, "operative_event_date") or _text(currentness, "event_announcement_date") or _text(currentness, "event_effective_date") or _text(currentness, "observed_date")
    announcement_date = source_date or currentness_date
    effective_date = _text(currentness, "event_effective_date") or (_text(currentness, "operative_event_date") if _text(currentness, "currentness_class") == "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE" else "")
    text_for_summary = supporting_passage or _text(raw_item, "description") or source_title
    authority_level = _normalize_authority_level(_text(raw_item, "authority_level"))
    reviewed = CareLineReviewedRecord.model_validate(
        {
            "producer_record_id": _text(raw_item, "raw_item_id"),
            "source_url": source_url,
            "source_title": source_title,
            "source_publisher": _text(raw_item, "source_publisher"),
            "source_publication_date": source_date,
            "source_type": _text(raw_item, "source_type"),
            "source_role": _text(raw_item, "source_role") or "national_intake_candidate",
            "supporting_passage": supporting_passage,
            "effective_evidence_text": supporting_passage,
            "raw_payload_hash": stable_json_hash(
                {
                    "raw_item": _text(raw_item, "record_fingerprint"),
                    "article_text": _text(article_content or {}, "text"),
                    "passage": supporting_passage,
                }
            ),
            "event_type": event_type,
            "event_type_raw": event_type,
            "announcement_date": announcement_date,
            "effective_date": effective_date,
            "service_line": service_line,
            "service_line_raw": service_line,
            "facility_name": facility_name,
            "provider_name": provider_name or facility_name or subject,
            "facility_type": _facility_type_from_text(evidence_blob),
            "city": _text(geography, "city"),
            "state": _text(geography, "state"),
            "jurisdiction_display": _text(geography, "jurisdiction_display"),
            "service_region": _text(geography, "service_region"),
            "geographic_scope": _text(geography, "geographic_scope") or ("service_region" if _text(geography, "service_region") else "statewide"),
            "country_code": "US",
            "permanence": _permanence_from_text(evidence_blob, event_type),
            "claim_summary": text_for_summary[:400],
            "access_consequences": access_consequences,
            "authority_level": authority_level,
            "review_status": "not_reviewed",
            "record_status": "needs_normalization_review",
            "public_status": "not_public",
            "universal_event_status": "needs_evidence_review" if qualification_status != "qualified" else "needs_normalization_review",
            "evidence_level": _evidence_level(article_content, raw_item),
            "evidence_provenance_type": "source_explicit",
            "evidence_valid_for_universal_event": qualification_status == "qualified",
            "verification_notes": access_exception,
            "field_provenance": {
                "producer_record_id": _make_provenance(_text(raw_item, "raw_item_id"), source_field="raw_item_id", supporting_text=_text(raw_item, "raw_item_id")),
                "source_url": _make_provenance(source_url, source_field="item_url", supporting_text=source_url),
                "source_title": _make_provenance(source_title, source_field="title", supporting_text=source_title),
                "announcement_date": _make_provenance(source_date, source_field="source_publication_date", supporting_text=source_date),
                "supporting_passage": _make_provenance(supporting_passage, source_field="article_text", supporting_text=supporting_passage),
                "state": _make_provenance(_text(geography, "state"), source_field="geography", supporting_text=_text(geography, "jurisdiction_display") or _text(geography, "state"), confidence=1.0 if _text(geography, "state") else 0.0, review_status="confirmed" if _text(geography, "state") else "unresolved"),
            },
            "metadata": {
                "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
                "collection_run_id": run_id,
                "source_artifact_path": artifact_path,
                "qualification_status": qualification_status,
                "failed_gates": list(failed_gates),
                "exclusion_reason": exclusion_reason,
                "full_article_required": full_article_required,
                "extraction_confidence": extraction_confidence,
                "source_record_id": _text(raw_item, "raw_item_id"),
                "currentness": dict(currentness),
            },
        }
    )
    candidate_id = stable_candidate_id(raw_item, reviewed)
    priority, priority_reason = review_priority(reviewed)
    qualification_result = {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "qualification_status": qualification_status,
        "failed_gates": list(failed_gates),
        "exclusion_reason": exclusion_reason,
        "extraction_confidence": extraction_confidence,
        "full_article_required": full_article_required,
        "public_eligibility_precheck": qualification_status == "qualified" and not reviewed.validation_issues(),
        "review_priority_recommendation": priority,
        "priority_reason": priority_reason,
        "review_warnings": [
            warning
            for warning in (
                "derived_review_date_from_currentness" if not source_date and announcement_date else "",
                "missing_source_publication_date" if not source_date else "",
            )
            if warning
        ],
        "currentness_class": _text(currentness, "currentness_class"),
        "freshness_role": _text(currentness, "freshness_role"),
        "operative_event_date": _text(currentness, "operative_event_date"),
        "background_date_references": list(currentness.get("background_date_references", [])) if isinstance(currentness.get("background_date_references"), list) else [],
        "currentness_confidence": currentness.get("currentness_confidence", 0.0),
        "currentness_reasoning": _text(currentness, "currentness_reasoning"),
        "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])) if isinstance(currentness.get("currentness_failed_gates"), list) else [],
        "operative_event_passage": _text(currentness, "operative_event_passage"),
        "background_event_passages": list(currentness.get("background_event_passages", [])) if isinstance(currentness.get("background_event_passages"), list) else [],
        "title_body_agree": bool(currentness.get("title_body_agree")),
    }
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "source_id": _text(raw_item, "source_id"),
        "source_name": _text(raw_item, "source_name"),
        "source_artifact_path": artifact_path,
        "collection_run_id": run_id,
        "normalization_status": "normalized" if not reviewed.validation_issues() else "needs_review",
        "validation_errors": [issue.model_dump() for issue in reviewed.validation_issues()],
        "duplicate_cluster_hints": cluster_hints(reviewed, source_row=raw_item),
        "normalized_record": reviewed.model_dump(mode="json"),
        "qualification_result": qualification_result,
        "first_seen": utc_now(),
        "last_seen": utc_now(),
    }


def _qualified_gate_failures(
    raw_item: Mapping[str, Any],
    *,
    geography: Mapping[str, str],
    subject: str,
    event_type: str,
    service_line: str,
    supporting_passage: str,
    access_consequences: list[str],
) -> list[str]:
    failures = []
    if not _text(raw_item, "source_id"):
        failures.append("missing_source_id")
    if not _text(raw_item, "item_url"):
        failures.append("missing_source_url")
    if _text(raw_item, "source_date_state") != "source_dated":
        failures.append("missing_source_date")
    if not _text(geography, "state"):
        failures.append("missing_geography")
    if not subject:
        failures.append("missing_subject")
    if not event_type:
        failures.append("missing_event_type")
    facility_wide = bool(re.search(r"\b(hospital|clinic|center|health center|medical center)\b", subject, re.I))
    healthcare_passage = bool(_keyword_hits(supporting_passage, HEALTHCARE_CONTEXT_PATTERNS)) or bool(service_line)
    subject_invalid = bool(re.search(r"\b(court|ruling|order|law|bill|governor|congress|judge|approvals?)\b", subject, re.I))
    if event_type in {"service_closure", "service_suspension", "service_reduction", "hours_reduction"} and not service_line and not facility_wide:
        failures.append("missing_service_line_or_facility_scope")
    if subject_invalid:
        failures.append("missing_subject")
    if not supporting_passage:
        failures.append("insufficient_bounded_evidence")
    if supporting_passage and not healthcare_passage and not facility_wide:
        failures.append("non_healthcare_passage")
    if not access_consequences:
        failures.append("needs_access_consequence")
    return failures


def qualify_event_lead(
    source: CareLineSource,
    raw_item: Mapping[str, Any],
    lead: Mapping[str, Any],
    *,
    artifact_path: str,
    run_id: str,
    fetch_timeout: int,
    allow_insecure_tls: bool,
    prefilter_decision: str = "",
) -> tuple[str, dict[str, Any]]:
    if prefilter_decision == "discard":
        return "prefilter_discarded", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "prefilter_decision": "discard",
            "normalized_reason": _text(lead, "exclusion_reason") or "no_access_impact",
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "confidence": 0.0,
            "positive_signals": list(lead.get("positive_hits") or []) + list(lead.get("context_hits") or []),
            "negative_signals": [str(_text(lead, "exclusion_reason") or "")] if _text(lead, "exclusion_reason") else [],
            "escalated_to_full_review": False,
            "history_match_count": 0,
            "history_matches": [],
        }
    if _text(lead, "qualification_status") != "event_lead" and prefilter_decision != "escalate_to_full_review":
        exclusion_reason = _text(lead, "exclusion_reason") or "non_care_line"
        return "excluded", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-exclusion", raw_item.get("raw_item_id", ""), exclusion_reason),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": exclusion_reason.upper(),
            "exclusion_reason": exclusion_reason,
            "editorial_outcome": "EXCLUDED",
            "extraction_outcome": "INDEX_ONLY",
            "extraction_method": "feed_only_exclusion",
            "failed_gates": [],
            "supporting_text": _text(raw_item, "description"),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    article_content: dict[str, Any] | None = None
    extraction_outcome = "INDEX_ONLY"
    extraction_method = "feed_summary"
    extraction_hash = stable_json_hash([raw_item.get("raw_item_id", ""), "feed_summary"])
    if _can_fetch_item_url(source, _text(raw_item, "item_url")):
        try:
            article_payload, response_meta = fetch_url(_text(raw_item, "item_url"), timeout=fetch_timeout, allow_insecure_tls=allow_insecure_tls)
            article_content = _extract_article_content(source, article_payload, source_url=_text(raw_item, "item_url"), response_meta=response_meta)
            extraction_outcome = _text(article_content, "extraction_outcome") or extraction_outcome
            extraction_method = _text(article_content, "extraction_method") or extraction_method
            extraction_hash = _text(article_content, "content_hash") or extraction_hash
        except Exception:  # noqa: BLE001
            article_content = None
            extraction_outcome = "ACCESS_BLOCKED"
            extraction_method = "fetch_failure"

    evidence_blob = _evidence_blob(raw_item, article_content)
    if extraction_outcome in {"PAYWALLED", "PDF_REQUIRED", "SCRIPT_RENDERED", "ACCESS_BLOCKED"}:
        passage_source = _text(article_content or {}, "text", "description")
    else:
        passage_source = _text(article_content or {}, "text") or evidence_blob
    service_line = _text(lead, "service_line_hint") or _service_line_from_text(_text(article_content or {}, "text")) or _service_line_from_text(_text(raw_item, "description")) or _service_line_from_text(evidence_blob)
    event_type = _text(lead, "event_type_hint") or _event_type_from_text(_text(article_content or {}, "text") or evidence_blob, service_line=service_line)
    if _text(lead, "event_type_hint") in {"facility_closure", "planned_facility_closure", "service_closure", "service_suspension"} and event_type in {"facility_reopening", "service_restoration"}:
        event_type = _text(lead, "event_type_hint")
    currentness = _currentness_analysis(
        title=_text(raw_item, "title"),
        lead_text=_text(raw_item, "description"),
        text=_text(article_content or {}, "text") or evidence_blob,
        source_publication_date=_text(raw_item, "source_publication_date"),
        event_type=event_type,
        service_line=service_line,
    )
    if _is_service_expansion_only(evidence_blob) and event_type not in {"facility_reopening", "service_restoration"}:
        return "excluded", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-exclusion", raw_item.get("raw_item_id", ""), "service_expansion_without_prior_loss_context"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": "RESOURCE_LISTING",
            "exclusion_reason": "service_expansion_without_prior_loss_context",
            "editorial_outcome": "EXCLUDED",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": [],
            "supporting_text": _supporting_passage(evidence_blob, event_type, service_line),
            "currentness_class": _text(currentness, "currentness_class"),
            "freshness_role": _text(currentness, "freshness_role"),
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    source_date_state = _text(raw_item, "source_date_state")
    has_reviewable_currentness_date = bool(
        _text(currentness, "operative_event_date")
        or _text(currentness, "event_announcement_date")
        or _text(currentness, "event_effective_date")
        or _text(currentness, "observed_date")
    )
    if source_date_state != "source_dated" and not has_reviewable_currentness_date:
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), "needs_date"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": "NEEDS_DATE",
            "exclusion_reason": "needs_date",
            "editorial_outcome": "NEEDS_DATE",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": ["missing_source_date"],
            "supporting_text": _text(raw_item, "description"),
            "currentness_class": _text(currentness, "currentness_class"),
            "freshness_role": _text(currentness, "freshness_role"),
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }
    summary_text = f"{_text(raw_item, 'title')} {_text(raw_item, 'description')}"
    summary_explicit_event = bool(
        re.search(r"\b(close|closing|closed|end|ending|suspend|suspended|halt|halted|cut|reducing|reduce|reopen|reopened|restore|restored|transfer|move)\b", summary_text, re.I)
        and re.search(r"\b(hospital|clinic|center|ward|unit|department|labor and delivery|maternity|service|services)\b", summary_text, re.I)
        and re.search(r"\b(effective|according to|announced|news release|transfer|move|patients|board)\b", summary_text, re.I)
    )
    if extraction_outcome in {"PAYWALLED", "PDF_REQUIRED", "SCRIPT_RENDERED", "ACCESS_BLOCKED"} and not summary_explicit_event:
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), extraction_outcome.casefold()),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": "NEEDS_HUMAN_REVIEW",
            "exclusion_reason": "needs_full_article",
            "editorial_outcome": "NEEDS_HUMAN_REVIEW",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": ["insufficient_bounded_evidence"],
            "supporting_text": _text(raw_item, "description"),
            "currentness_class": _text(currentness, "currentness_class"),
            "freshness_role": _text(currentness, "freshness_role"),
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    supporting_passage = _supporting_passage(passage_source, event_type, service_line)
    if not supporting_passage and not article_content:
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), "needs_full_article"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": "NEEDS_FULL_ARTICLE",
            "exclusion_reason": "needs_full_article",
            "editorial_outcome": "NEEDS_FULL_ARTICLE",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": ["insufficient_bounded_evidence"],
            "supporting_text": _text(raw_item, "description"),
            "currentness_class": _text(currentness, "currentness_class"),
            "freshness_role": _text(currentness, "freshness_role"),
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }
    if not supporting_passage:
        editorial_outcome = "NEEDS_HUMAN_REVIEW" if extraction_outcome in {"PARTIAL_BODY", "PAYWALLED", "SCRIPT_RENDERED", "PDF_REQUIRED"} else "NEEDS_FULL_ARTICLE"
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), "insufficient_bounded_evidence"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": editorial_outcome,
            "exclusion_reason": "insufficient_bounded_evidence",
            "editorial_outcome": editorial_outcome,
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": ["insufficient_bounded_evidence"],
            "supporting_text": _text(article_content or {}, "description") or _text(raw_item, "description"),
            "currentness_class": _text(currentness, "currentness_class"),
            "freshness_role": _text(currentness, "freshness_role"),
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": list(currentness.get("currentness_failed_gates", [])),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    supporting_service_line = _service_line_from_text(supporting_passage)
    if supporting_service_line:
        service_line = supporting_service_line
    elif event_type in {"facility_closure", "planned_facility_closure", "temporary_facility_suspension", "facility_conversion", "facility_relocation", "facility_reopening"}:
        service_line = ""
    geography = _extract_geography(raw_item, supporting_passage or evidence_blob)
    subject, provider = _extract_subject(_text(raw_item, "title"), supporting_passage, service_line=service_line)
    access_consequences, access_exception = _access_consequences_from_text(supporting_passage, event_type)
    failed_gates = _qualified_gate_failures(
        raw_item,
        geography=geography,
        subject=subject or provider,
        event_type=event_type,
        service_line=service_line,
        supporting_passage=supporting_passage,
        access_consequences=access_consequences,
    )
    if source_date_state != "source_dated" and has_reviewable_currentness_date:
        failed_gates = [gate for gate in failed_gates if gate != "missing_source_date"]
    extraction_confidence = min(1.0, 0.55 + 0.07 * len(_keyword_hits(supporting_passage, [(label, pattern) for label, _, pattern in POSITIVE_EVENT_PATTERNS])) + (0.1 if article_content else 0.0))
    full_article_required = bool(_text(lead, "full_article_required")) and not article_content
    currentness_passage = _text(currentness, "operative_event_passage")
    if currentness_passage:
        supporting_passage = currentness_passage
    freshness_role = _text(currentness, "freshness_role")
    currentness_class = _text(currentness, "currentness_class")
    currentness_failed_gates = list(currentness.get("currentness_failed_gates", [])) if isinstance(currentness.get("currentness_failed_gates"), list) else []
    if currentness_class in {"RECENT_BACKGROUND", "HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}:
        return "excluded", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-exclusion", raw_item.get("raw_item_id", ""), "historical_context_only"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": currentness_class,
            "exclusion_reason": "background_only",
            "editorial_outcome": "EXCLUDED",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": currentness_failed_gates or ["historical_context_only"],
            "supporting_text": supporting_passage,
            "currentness_class": currentness_class,
            "freshness_role": freshness_role,
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": currentness_failed_gates or ["historical_context_only"],
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }
    if currentness_class == "DATE_UNRESOLVED":
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), "currentness_unresolved"),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": "NEEDS_HUMAN_REVIEW",
            "exclusion_reason": "needs_date",
            "editorial_outcome": "NEEDS_HUMAN_REVIEW",
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": currentness_failed_gates or ["currentness_unresolved"],
            "supporting_text": supporting_passage,
            "currentness_class": currentness_class,
            "freshness_role": freshness_role,
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": currentness_failed_gates or ["currentness_unresolved"],
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    if failed_gates:
        primary = failed_gates[0]
        reason_map = {
            "missing_geography": "needs_geography",
            "missing_subject": "missing_subject",
            "missing_event_type": "missing_event_type",
            "missing_service_line_or_facility_scope": "missing_service_line_or_facility_scope",
            "insufficient_bounded_evidence": "insufficient_bounded_evidence",
            "non_healthcare_passage": "non_care_line",
            "needs_access_consequence": "needs_access_consequence",
            "missing_source_date": "needs_date",
        }
        reason = reason_map.get(primary, "needs_full_article")
        classification = "NEEDS_HUMAN_REVIEW" if extraction_outcome in {"PARTIAL_BODY", "PAYWALLED", "SCRIPT_RENDERED", "PDF_REQUIRED"} and reason in {"needs_full_article", "missing_subject", "insufficient_bounded_evidence"} else reason.upper()
        return "failed_extraction", {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "exclusion_id": _stable_id("care-line-failed-extraction", raw_item.get("raw_item_id", ""), reason),
            "raw_item_id": raw_item.get("raw_item_id", ""),
            "lead_id": lead.get("lead_id", ""),
            "source_id": raw_item.get("source_id", ""),
            "source_name": raw_item.get("source_name", ""),
            "item_url": raw_item.get("item_url", ""),
            "title": raw_item.get("title", ""),
            "source_publication_date": raw_item.get("source_publication_date", ""),
            "classification": classification,
            "exclusion_reason": reason,
            "editorial_outcome": classification,
            "extraction_outcome": extraction_outcome,
            "extraction_method": extraction_method,
            "failed_gates": failed_gates,
            "supporting_text": supporting_passage,
            "currentness_class": currentness_class,
            "freshness_role": freshness_role,
            "operative_event_date": _text(currentness, "operative_event_date"),
            "background_date_references": list(currentness.get("background_date_references", [])),
            "currentness_confidence": currentness.get("currentness_confidence", 0.0),
            "currentness_reasoning": _text(currentness, "currentness_reasoning"),
            "currentness_failed_gates": currentness_failed_gates,
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    candidate = normalize_candidate_record(
        raw_item,
        article_content=article_content,
        supporting_passage=supporting_passage,
        geography=geography,
        subject=subject,
        provider=provider,
        event_type=event_type,
        service_line=service_line,
        access_consequences=access_consequences,
        access_exception=access_exception,
        artifact_path=artifact_path,
        run_id=run_id,
        qualification_status="qualified",
        failed_gates=[],
        exclusion_reason="",
        extraction_confidence=round(extraction_confidence, 3),
        full_article_required=full_article_required,
        currentness=currentness,
    )
    candidate["qualification_result"]["editorial_outcome"] = "QUALIFIED"
    candidate["qualification_result"]["extraction_outcome"] = extraction_outcome
    candidate["qualification_result"]["extraction_method"] = extraction_method
    candidate["qualification_result"]["content_hash"] = extraction_hash
    if full_article_required:
        candidate["qualification_result"]["review_warnings"] = list(dict.fromkeys(candidate["qualification_result"].get("review_warnings", []) + ["full_article_recommended"]))
    return "qualified", candidate


def _candidate_quality_score(candidate: Mapping[str, Any]) -> tuple[int, int, int, str]:
    normalized = candidate.get("normalized_record") if isinstance(candidate.get("normalized_record"), Mapping) else {}
    reviewed = CareLineReviewedRecord.model_validate(normalized)
    evidence_score = {
        "article_excerpt": 4,
        "official_notice": 4,
        "feed_summary": 2,
        "metadata_only": 1,
    }.get(reviewed.evidence_level, 1)
    authority_score = {
        "primary": 4,
        "official": 4,
        "sector": 3,
        "reviewed": 3,
        "secondary": 2,
        "unknown": 1,
    }.get(reviewed.authority_level or "unknown", 1)
    source_date = reviewed.source_publication_date or ""
    return (-evidence_score, -authority_score, -len(reviewed.supporting_passage), source_date)


def cluster_candidates(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        hint = row.get("duplicate_cluster_hints") if isinstance(row.get("duplicate_cluster_hints"), Mapping) else {}
        cluster_id = str(hint.get("cluster_id") or "")
        if not cluster_id:
            continue
        groups.setdefault(cluster_id, []).append(dict(row))
    clusters = []
    for cluster_id, rows in sorted(groups.items()):
        sorted_rows = sorted(rows, key=_candidate_quality_score)
        canonical = sorted_rows[0]
        candidate_ids = [str(item.get("candidate_id") or "") for item in sorted_rows]
        normalized = canonical.get("normalized_record") if isinstance(canonical.get("normalized_record"), Mapping) else {}
        reviewed = CareLineReviewedRecord.model_validate(normalized)
        reasoning = "strongest bounded evidence and authority chosen as canonical"
        confidence = "high" if len(rows) > 1 else "single_record"
        clusters.append(
            {
                "cluster_id": cluster_id,
                "canonical_candidate_id": canonical.get("candidate_id", ""),
                "candidate_ids": candidate_ids,
                "candidate_count": len(candidate_ids),
                "duplicate_confidence": confidence,
                "cluster_confidence": confidence,
                "canonical_selection_reasoning": reasoning,
                "event_type": reviewed.event_type,
                "service_line": reviewed.service_line,
                "jurisdiction": reviewed.state,
            }
        )
    return {
        "schema_version": CLUSTERING_SCHEMA_VERSION,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }


def _queue_row_from_candidate(item: Mapping[str, Any], *, cluster_id: str, duplicate_status: str) -> dict[str, Any]:
    normalized = item.get("normalized_record") if isinstance(item.get("normalized_record"), Mapping) else {}
    reviewed = CareLineReviewedRecord.model_validate(normalized)
    qualification = item.get("qualification_result") if isinstance(item.get("qualification_result"), Mapping) else {}
    priority = str(qualification.get("review_priority_recommendation") or "LOW")
    priority_reason = str(qualification.get("priority_reason") or "")
    return {
        "candidate_id": item.get("candidate_id", ""),
        "event_cluster_id": cluster_id,
        "facility_or_system": reviewed.facility_name or reviewed.provider_name or reviewed.parent_organization,
        "public_location_label": reviewed.public_location_label,
        "jurisdiction": reviewed.state,
        "event_type": reviewed.event_type,
        "service_line": reviewed.service_line,
        "access_consequence": reviewed.access_consequences,
        "source_url": reviewed.source_url,
        "source_publisher": reviewed.source_publisher,
        "source_date": reviewed.source_publication_date,
        "supporting_passage": reviewed.supporting_passage,
        "verification_state": reviewed.verification_state,
        "workflow_state": reviewed.workflow_state,
        "evidence_level": reviewed.evidence_level,
        "authority_level": reviewed.authority_level,
        "duplicate_status": duplicate_status,
        "review_priority": priority,
        "priority_reason": priority_reason,
        "currentness_class": str(qualification.get("currentness_class") or ""),
        "freshness_role": str(qualification.get("freshness_role") or "UNKNOWN"),
        "operative_event_date": str(qualification.get("operative_event_date") or ""),
        "background_date_references": qualification.get("background_date_references") or [],
        "currentness_confidence": qualification.get("currentness_confidence", 0.0),
        "currentness_reasoning": str(qualification.get("currentness_reasoning") or ""),
        "currentness_failed_gates": qualification.get("currentness_failed_gates") or [],
        "review_reason": "corroborating_evidence" if duplicate_status == "duplicate" else "needs_editorial_review",
        "originating_run_id": item.get("collection_run_id", ""),
        "source_artifact_path": item.get("source_artifact_path", ""),
        "reviewer_decision": "",
        "reviewer_note": "",
        "exclusion_reason": "",
        "first_seen": item.get("first_seen", ""),
        "last_seen": item.get("last_seen", ""),
        "qualification_result": qualification,
    }


def _queue_sort_key(row: Mapping[str, Any]) -> tuple[int, str, str, str]:
    return (
        PRIORITY_ORDER.get(str(row.get("review_priority") or "LOW"), 9),
        str(row.get("source_date") or ""),
        str(row.get("facility_or_system") or ""),
        str(row.get("candidate_id") or ""),
    )


def build_review_queue(
    candidates: Iterable[Mapping[str, Any]],
    *,
    edition_date: str,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
) -> dict[str, Any]:
    active_freshness_roles = {"BREAKING", "CURRENT", "FUTURE_EFFECTIVE", "ONGOING_EVENT_UPDATE", "RESTORATION_UPDATE"}
    all_candidates = [
        dict(candidate)
        for candidate in candidates
        if str(candidate.get("candidate_status") or "active") == "active"
    ]
    clusters = cluster_candidates(all_candidates)
    canonical_by_cluster = {row["cluster_id"]: row["canonical_candidate_id"] for row in clusters["clusters"]}
    canonical_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    for item in all_candidates:
        hints = item.get("duplicate_cluster_hints") if isinstance(item.get("duplicate_cluster_hints"), Mapping) else {}
        cluster_id = str(hints.get("cluster_id") or "")
        duplicate_status = "canonical" if canonical_by_cluster.get(cluster_id) == item.get("candidate_id") else "duplicate" if cluster_id else "uncertain"
        row = _queue_row_from_candidate(item, cluster_id=cluster_id, duplicate_status=duplicate_status)
        if duplicate_status == "duplicate":
            duplicate_rows.append(row)
        else:
            canonical_rows.append(row)
    canonical_rows.sort(key=_queue_sort_key)
    duplicate_rows.sort(key=_queue_sort_key)
    queueable = [row for row in canonical_rows if row["freshness_role"] in active_freshness_roles]
    contextual = [row for row in canonical_rows if row["freshness_role"] not in active_freshness_roles]
    critical_high = [row for row in queueable if row["review_priority"] in {"CRITICAL", "HIGH"}]
    standard = [row for row in queueable if row["review_priority"] == "STANDARD"]
    low = [row for row in queueable if row["review_priority"] == "LOW"]
    standard_cap = max(active_queue_limit - len(critical_high), 0)
    active_standard = standard[:standard_cap]
    remaining_after_standard = max(active_queue_limit - len(critical_high) - len(active_standard), 0)
    active_low = low[: min(low_priority_cap, remaining_after_standard)]
    backlog = contextual + standard[standard_cap:] + low[min(low_priority_cap, remaining_after_standard) :]
    active_rows = critical_high + active_standard + active_low
    return {
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "edition_date": edition_date,
        "queue_item_count": len(active_rows),
        "backlog_item_count": len(backlog),
        "duplicate_item_count": len(duplicate_rows),
        "clusters": clusters,
        "items": active_rows,
        "backlog": backlog,
        "duplicates": duplicate_rows,
    }


def update_candidate_registry(root: Path, *, edition_date: str, candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    path = root / CANDIDATE_REGISTRY_PATH
    return update_candidate_registry_at_path(path, edition_date=edition_date, candidates=candidates)


def update_candidate_registry_at_path(path: Path, *, edition_date: str, candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    existing = _load_json(path, {"schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION, "candidates": []})
    existing_by_id = {
        str(row.get("candidate_id") or ""): dict(row)
        for row in existing.get("candidates", [])
        if isinstance(row, Mapping) and str(row.get("candidate_id") or "")
    }
    seen_this_run: set[str] = set()
    created = 0
    updated = 0
    merged_by_id = dict(existing_by_id)
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        seen_this_run.add(candidate_id)
        current = existing_by_id.get(candidate_id)
        merged = dict(row)
        if current:
            merged["first_seen"] = current.get("first_seen") or row.get("first_seen") or edition_date
            updated += 1
        else:
            merged["first_seen"] = row.get("first_seen") or edition_date
            created += 1
        merged["last_seen"] = row.get("last_seen") or edition_date
        merged["candidate_status"] = "active"
        merged_by_id[candidate_id] = merged
    persistent_prior = 0
    stale_count = 0
    superseded_count = 0
    for candidate_id, row in existing_by_id.items():
        if candidate_id in seen_this_run:
            continue
        persistent_prior += 1
        persisted = dict(row)
        persisted["candidate_status"] = "stale"
        merged_by_id[candidate_id] = persisted
        stale_count += 1
    for row in merged_by_id.values():
        normalized = row.get("normalized_record") if isinstance(row.get("normalized_record"), Mapping) else {}
        if str(normalized.get("workflow_state") or "") == "SUPERSEDED":
            superseded_count += 1
    payload = {
        "schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION,
        "candidate_count": len(merged_by_id),
        "created_this_run": created,
        "updated_this_run": updated,
        "persistent_candidates_from_prior_runs": persistent_prior,
        "stale_candidate_count": stale_count,
        "superseded_candidate_count": superseded_count,
        "candidates": [merged_by_id[key] for key in sorted(merged_by_id)],
    }
    _atomic_write(path, payload)
    return payload


def write_review_snapshot(
    root: Path,
    *,
    edition_date: str,
    queue_payload: Mapping[str, Any],
    legacy_fallback_path: str = "",
    review_snapshot_root: Path = REVIEW_SNAPSHOT_ROOT,
) -> dict[str, Any]:
    snapshot_path = root / review_snapshot_root / f"{edition_date}.json"
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "edition_date": edition_date,
        "queue_payload": queue_payload,
        "legacy_fallback_path": legacy_fallback_path,
    }
    _atomic_write(snapshot_path, payload)
    return {
        "snapshot_path": snapshot_path.as_posix(),
        "snapshot_sha256": sha256(snapshot_path.read_bytes()).hexdigest(),
    }


def validate_review_snapshot(
    root: Path,
    *,
    edition_date: str,
    snapshot_path: str,
    snapshot_sha256: str,
    allow_legacy_fallback: bool = False,
    fallback_queue_path: str = "",
    review_snapshot_root: Path = REVIEW_SNAPSHOT_ROOT,
) -> dict[str, Any]:
    path = root / snapshot_path
    if path.exists():
        actual = sha256(path.read_bytes()).hexdigest()
        if actual != snapshot_sha256:
            raise ValueError("review snapshot SHA-256 is stale")
        return {"mode": "snapshot", "path": path.as_posix(), "sha256": actual}
    if allow_legacy_fallback and fallback_queue_path:
        fallback = root / fallback_queue_path
        if not fallback.exists():
            raise ValueError("unable to read review snapshot or legacy fallback queue")
        return {"mode": "legacy_fallback", "path": fallback.as_posix(), "sha256": sha256(fallback.read_bytes()).hexdigest()}
    raise ValueError("unable to read review snapshot")


@dataclass(frozen=True)
class CollectionAttempt:
    source_id: str
    source_name: str
    readiness: str
    readiness_reason: str
    collection_status: str
    item_count: int
    raw_item_count: int
    event_lead_count: int
    qualified_candidate_count: int
    excluded_item_count: int
    failed_extraction_count: int
    started_at: str
    completed_at: str
    source_urls: tuple[str, ...]
    failure_reason: str = ""
    parser_version: str = PARSER_VERSION
    content_hash: str = ""
    retry_state: str = "not_retried"

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "readiness": self.readiness,
            "readiness_reason": self.readiness_reason,
            "collection_status": self.collection_status,
            "item_count": self.item_count,
            "raw_item_count": self.raw_item_count,
            "event_lead_count": self.event_lead_count,
            "qualified_candidate_count": self.qualified_candidate_count,
            "excluded_item_count": self.excluded_item_count,
            "failed_extraction_count": self.failed_extraction_count,
            "collection_started_at": self.started_at,
            "collection_completed_at": self.completed_at,
            "source_urls_discovered": list(self.source_urls),
            "failure_reason": self.failure_reason,
            "parser_version": self.parser_version,
            "content_hash": self.content_hash,
            "retry_state": self.retry_state,
        }


def run_collection_attempt(
    root: Path,
    *,
    run_date: str,
    run_id: str,
    source_row: Mapping[str, Any],
    historical_reviewed_records: Iterable[CareLineReviewedRecord] | None = None,
    allow_insecure_tls: bool = False,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
    collection_runs_root: Path = COLLECTION_RUNS_ROOT,
) -> dict[str, Any]:
    run_dir = root / collection_runs_root / run_date / run_id
    source = source_row["source"]
    started_at = utc_now()
    if not isinstance(source, CareLineSource):
        raise TypeError("source_row['source'] must be CareLineSource")
    if source_readiness_status(source) == "DISABLED":
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="skipped",
            item_count=0,
            raw_item_count=0,
            event_lead_count=0,
            qualified_candidate_count=0,
            excluded_item_count=0,
            failed_extraction_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason="disabled_source",
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "manual_review": [], "failure": attempt.failure_reason}
    if source_readiness_status(source) == "MANUAL_REVIEW_ONLY":
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="skipped",
            item_count=0,
            raw_item_count=0,
            event_lead_count=0,
            qualified_candidate_count=0,
            excluded_item_count=0,
            failed_extraction_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason="manual_source_not_automated",
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "manual_review": [], "failure": attempt.failure_reason}
    try:
        payload, fetch_meta = fetch_source(source, timeout=fetch_timeout, allow_insecure_tls=allow_insecure_tls)
        items = parse_source_items(
            source,
            payload,
            source_url=str(fetch_meta.get("final_url") or source.feed_url),
            fetch_timeout=fetch_timeout,
            allow_insecure_tls=allow_insecure_tls,
            max_items_per_source=max_items_per_source,
        )
    except ET.ParseError as exc:
        failure = f"ParseError: {exc}"
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="failed",
            item_count=0,
            raw_item_count=0,
            event_lead_count=0,
            qualified_candidate_count=0,
            excluded_item_count=0,
            failed_extraction_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason=failure,
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        _atomic_write(run_dir / _source_failure_filename(source.source_id), {"source_id": source.source_id, "failure_reason": failure})
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "manual_review": [], "failure": failure}
    except Exception as exc:  # noqa: BLE001
        failure = f"{type(exc).__name__}: {exc}"
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="failed",
            item_count=0,
            raw_item_count=0,
            event_lead_count=0,
            qualified_candidate_count=0,
            excluded_item_count=0,
            failed_extraction_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason=failure,
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        _atomic_write(run_dir / _source_failure_filename(source.source_id), {"source_id": source.source_id, "failure_reason": failure})
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "manual_review": [], "failure": failure}
    raw_artifact_path = run_dir / _source_raw_items_filename(source.source_id)
    raw_items = [
        discovery_record_from_direct_item(
            item,
            source,
            discovery_date=run_date,
            rank=index,
            collected_at=started_at,
            collection_run_id=run_id,
            source_artifact_path=raw_artifact_path.as_posix(),
        )
        for index, item in enumerate(items, start=1)
    ]
    event_leads = [event_lead_from_raw_item(item) for item in raw_items]
    leads_by_id = {str(lead.get("lead_id") or ""): lead for lead in event_leads}
    qualified_candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    failed_extractions: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    prefilter_diagnostics: list[dict[str, Any]] = []
    prefilter_discarded: list[dict[str, Any]] = []
    for raw_item, lead in zip(raw_items, event_leads, strict=False):
        prefilter = _care_line_access_prefilter(raw_item, lead, reviewed_records=historical_reviewed_records or ())
        prefilter_diagnostics.append(prefilter)
        if prefilter["prefilter_decision"] == "discard":
            prefilter_discarded.append(prefilter)
            continue
        status, payload_row = qualify_event_lead(
            source,
            raw_item,
            lead,
            artifact_path=raw_artifact_path.as_posix(),
            run_id=run_id,
            fetch_timeout=fetch_timeout,
            allow_insecure_tls=allow_insecure_tls,
            prefilter_decision=str(prefilter.get("prefilter_decision") or ""),
        )
        if status == "qualified":
            qualified_candidates.append(payload_row)
        elif status == "excluded":
            exclusions.append(payload_row)
        else:
            failed_extractions.append(payload_row)
            if _text(payload_row, "classification") == "NEEDS_HUMAN_REVIEW":
                manual_review.append(_manual_review_row(payload_row, lead=lead))
    content_hash = sha256(payload).hexdigest() if payload else ""
    attempt = CollectionAttempt(
        source_id=source.source_id,
        source_name=source.name,
        readiness=source_readiness_status(source),
        readiness_reason=source_readiness_reason(source),
        collection_status="partial" if source_readiness_status(source) == "AUTOMATED_PARTIAL" else "ok",
        item_count=len(items),
        raw_item_count=len(raw_items),
        event_lead_count=sum(1 for lead in event_leads if _text(lead, "qualification_status") == "event_lead"),
        qualified_candidate_count=len(qualified_candidates),
        excluded_item_count=len(exclusions),
        failed_extraction_count=len(failed_extractions),
        started_at=started_at,
        completed_at=utc_now(),
        source_urls=tuple(sorted({_text(row, "item_url") for row in raw_items if _text(row, "item_url")})),
        failure_reason=source.limitations if source_readiness_status(source) == "AUTOMATED_PARTIAL" else "",
        content_hash=content_hash,
    )
    _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
    _atomic_write(
        run_dir / _source_prefilter_filename(source.source_id),
        {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "prefilter_count": len(prefilter_diagnostics),
            "discarded_count": len(prefilter_discarded),
            "items": prefilter_diagnostics,
        },
    )
    _atomic_write(raw_artifact_path, {"schema_version": PIPELINE_SCHEMA_VERSION, "raw_items": raw_items})
    _atomic_write(run_dir / _source_event_leads_filename(source.source_id), {"schema_version": PIPELINE_SCHEMA_VERSION, "event_leads": event_leads})
    _atomic_write(run_dir / _source_candidates_filename(source.source_id), {"schema_version": PIPELINE_SCHEMA_VERSION, "qualified_candidates": qualified_candidates})
    _atomic_write(run_dir / _source_exclusions_filename(source.source_id), {"schema_version": PIPELINE_SCHEMA_VERSION, "exclusions": exclusions})
    _atomic_write(run_dir / _source_failed_extractions_filename(source.source_id), {"schema_version": PIPELINE_SCHEMA_VERSION, "failed_extractions": failed_extractions})
    return {
        "attempt": attempt.to_payload(),
        "raw_items": raw_items,
        "event_leads": event_leads,
        "candidates": qualified_candidates,
        "exclusions": exclusions,
        "failed_extractions": failed_extractions,
        "manual_review": manual_review,
        "prefilter_diagnostics": prefilter_diagnostics,
        "prefilter_discarded": prefilter_discarded,
        "failure": "",
    }


def _write_review_private_outputs(
    root: Path,
    *,
    queue_payload: Mapping[str, Any],
    exclusions: list[Mapping[str, Any]],
    failed_extractions: list[Mapping[str, Any]],
    manual_review: list[Mapping[str, Any]],
    follow_up_state: Mapping[str, Any] | None = None,
    review_root: Path = REVIEW_ROOT,
) -> None:
    review_paths = _review_state_paths(review_root)
    _atomic_write(root / review_paths["review_queue"], queue_payload)
    _atomic_write(
        root / review_paths["backlog"],
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue_payload.get("edition_date", ""),
            "backlog_item_count": len(queue_payload.get("backlog", [])),
            "items": list(queue_payload.get("backlog", [])),
        },
    )
    _atomic_write(
        root / review_paths["duplicates"],
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue_payload.get("edition_date", ""),
            "duplicate_item_count": len(queue_payload.get("duplicates", [])),
            "items": list(queue_payload.get("duplicates", [])),
        },
    )
    _atomic_write(
        root / review_paths["exclusions"],
        {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "excluded_item_count": len(exclusions),
            "items": exclusions,
        },
    )
    _atomic_write(
        root / review_paths["failed_extractions"],
        {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "failed_extraction_count": len(failed_extractions),
            "items": failed_extractions,
        },
    )
    _atomic_write(
        root / review_paths["manual_review"],
        {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "manual_review_count": len(manual_review),
            "items": manual_review,
        },
    )
    if follow_up_state is not None:
        _atomic_write(root / review_paths["follow_up_state"], follow_up_state)


def _manual_review_row(row: Mapping[str, Any], *, lead: Mapping[str, Any] | None = None) -> dict[str, Any]:
    missing_fields = list(row.get("failed_gates", [])) if isinstance(row.get("failed_gates"), list) else []
    return {
        "source_url": _text(row, "item_url"),
        "source": _text(row, "source_name"),
        "lead_reason": ", ".join(lead.get("positive_hits", [])) if isinstance(lead, Mapping) else "",
        "extraction_outcome": _text(row, "extraction_outcome"),
        "missing_fields": missing_fields,
        "suggested_reviewer_action": "Retrieve full article or confirm exclusion from bounded source text.",
        "priority_estimate": "HIGH" if isinstance(lead, Mapping) and int(lead.get("lead_score", 0) or 0) >= 6 else "STANDARD",
        "originating_run_id": _text(row.get("lineage", {}) if isinstance(row.get("lineage"), Mapping) else {}, "collection_run_id"),
        "raw_item_id": _text(row, "raw_item_id"),
        "lead_id": _text(row, "lead_id"),
        "title": _text(row, "title"),
        "classification": _text(row, "classification"),
        "supporting_text": _text(row, "supporting_text"),
    }


def run_national_pipeline(
    root: Path,
    *,
    run_date: str,
    run_id: str | None = None,
    include_partial: bool = True,
    include_manual_review: bool = False,
    allow_insecure_tls: bool = False,
    source_limit: int | None = None,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
    smoke_test: bool = False,
    collection_runs_root: Path = COLLECTION_RUNS_ROOT,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    registry = load_canonical_registry(root, include_disabled=True)
    source_rows = collectable_sources(registry, include_partial=include_partial, include_manual_review=include_manual_review)
    if smoke_test:
        source_rows = _sorted_smoke_source_rows(source_rows)
    if source_limit is not None:
        source_rows = source_rows[:source_limit]
    review_paths = _review_state_paths(review_root)
    historical_reviewed_records = load_reviewed_records(root)
    settings = {
        "include_partial": include_partial,
        "include_manual_review": include_manual_review,
        "allow_insecure_tls": allow_insecure_tls,
        "source_limit": source_limit,
        "fetch_timeout": fetch_timeout,
        "max_items_per_source": max_items_per_source,
        "active_queue_limit": active_queue_limit,
        "low_priority_cap": low_priority_cap,
        "smoke_test": smoke_test,
        "collection_runs_root": collection_runs_root.as_posix(),
        "review_root": review_root.as_posix(),
    }
    manifest = begin_collection_run(
        root,
        run_date=run_date,
        source_rows=source_rows,
        settings=settings,
        run_id=run_id,
        collection_runs_root=collection_runs_root,
    )
    run_id = manifest["run_id"]
    attempts = []
    raw_items: list[dict[str, Any]] = []
    event_leads: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    failed_extractions: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    prefilter_diagnostics: list[dict[str, Any]] = []
    prefilter_discarded: list[dict[str, Any]] = []
    for source_row in source_rows:
        result = run_collection_attempt(
            root,
            run_date=run_date,
            run_id=run_id,
            source_row=source_row,
            historical_reviewed_records=historical_reviewed_records,
            allow_insecure_tls=allow_insecure_tls,
            fetch_timeout=fetch_timeout,
            max_items_per_source=max_items_per_source,
            collection_runs_root=collection_runs_root,
        )
        attempts.append(result["attempt"])
        raw_items.extend(result["raw_items"])
        event_leads.extend(result["event_leads"])
        candidates.extend(result["candidates"])
        exclusions.extend(result["exclusions"])
        failed_extractions.extend(result["failed_extractions"])
        manual_review.extend(result.get("manual_review", []))
        prefilter_diagnostics.extend(result.get("prefilter_diagnostics", []))
        prefilter_discarded.extend(result.get("prefilter_discarded", []))
    candidate_registry = update_candidate_registry_at_path(root / review_paths["candidate_registry"], edition_date=run_date, candidates=candidates)
    queue_payload = build_review_queue(candidate_registry["candidates"], edition_date=run_date, active_queue_limit=active_queue_limit, low_priority_cap=low_priority_cap)
    _write_review_private_outputs(
        root,
        queue_payload=queue_payload,
        exclusions=exclusions,
        failed_extractions=failed_extractions,
        manual_review=manual_review,
        review_root=review_root,
    )
    existing_follow_up_state = load_follow_up_state(root, state_root=review_root)
    follow_up_records = historical_reviewed_records
    follow_up_queries = build_follow_up_queries(
        root,
        run_date,
        reviewed_records=follow_up_records,
        state=existing_follow_up_state,
    )
    follow_up_discovery_report: dict[str, Any] = {"query_rows": []}
    if follow_up_queries:
        follow_up_discovery_report = discover_care_line_sources(
            root,
            run_date,
            follow_up_queries=follow_up_queries,
            max_queries=len(follow_up_queries),
            write=False,
            dry_run=True,
        )
    follow_up_state_result = update_follow_up_state(
        root,
        run_date=run_date,
        follow_up_queries=follow_up_queries,
        discovery_query_rows=follow_up_discovery_report.get("query_rows", []),
        state_root=review_root,
    )
    collection_status_counts = Counter(str(attempt.get("collection_status") or "unknown") for attempt in attempts)
    successful_attempt_count = sum(collection_status_counts.get(key, 0) for key in ("ok", "partial"))
    failed_source_count = collection_status_counts.get("failed", 0)
    skipped_source_count = collection_status_counts.get("skipped", 0)
    if attempts and successful_attempt_count == 0 and failed_source_count > 0:
        run_status = RUN_STATUS_FAILURE
    elif failed_source_count > 0:
        run_status = RUN_STATUS_PARTIAL_SUCCESS
    else:
        run_status = RUN_STATUS_SUCCESS
    qualified_priorities = Counter(
        str((row.get("qualification_result") or {}).get("review_priority_recommendation") or "LOW")
        for row in candidates
    )
    final_manifest = {
        **manifest,
        "completed_at": utc_now(),
        "status": run_status,
        "collection_only": True,
        "attempts": attempts,
        "source_attempt_count": len(attempts),
        "successful_attempt_count": successful_attempt_count,
        "failed_source_count": failed_source_count,
        "skipped_source_count": skipped_source_count,
        "collection_status_counts": dict(sorted(collection_status_counts.items())),
        "smoke_test": smoke_test,
        "selected_source_ids": list(manifest["source_ids"]),
        "production_review_queue_mutation_disabled": smoke_test,
        "review_state_mode": _review_state_mode(smoke_test=smoke_test, review_root=review_root),
        "collection_runs_root": collection_runs_root.as_posix(),
        "review_root": review_root.as_posix(),
        "review_queue_path": (review_paths["review_queue"]).as_posix(),
        "candidate_registry_path": (review_paths["candidate_registry"]).as_posix(),
        "raw_items_retrieved_this_run": len(raw_items),
        "event_leads_created_this_run": sum(1 for lead in event_leads if _text(lead, "qualification_status") == "event_lead"),
        "follow_up_query_count": len(follow_up_queries),
        "follow_up_query_row_count": len(follow_up_discovery_report.get("query_rows", [])),
        "follow_up_material_update_count": sum(1 for item in follow_up_state_result["items"] if item.get("status") == "MATERIAL_UPDATE_FOUND"),
        "follow_up_state_path": str(follow_up_state_result.get("state_path") or ""),
        "qualified_candidates_created_this_run": candidate_registry["created_this_run"],
        "candidates_updated_this_run": candidate_registry["updated_this_run"],
        "persistent_candidates_from_prior_runs": candidate_registry["persistent_candidates_from_prior_runs"],
        "excluded_item_count": len(exclusions),
        "failed_extraction_count": len(failed_extractions),
        "manual_review_count": len(manual_review),
        "prefilter_discarded_count": len(prefilter_discarded),
        "prefilter_decision_count": len(prefilter_diagnostics),
        "active_review_queue_count": queue_payload["queue_item_count"],
        "backlog_item_count": queue_payload["backlog_item_count"],
        "duplicate_item_count": queue_payload["duplicate_item_count"],
        "cluster_count": queue_payload["clusters"]["cluster_count"],
        "priority_counts": dict(sorted(qualified_priorities.items())),
        "stale_candidate_count": candidate_registry["stale_candidate_count"],
        "superseded_candidate_count": candidate_registry["superseded_candidate_count"],
    }
    final_manifest["run_manifest_path"] = (collection_runs_root / run_date / run_id / "run-manifest.json").as_posix()
    _atomic_write(root / collection_runs_root / run_date / run_id / "run-manifest.json", final_manifest)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_manifest": final_manifest,
        "candidate_registry": candidate_registry,
        "review_queue": queue_payload,
        "compatibility_pressure_registry": adapt_pressure_registry(root),
        "exclusions": {"excluded_item_count": len(exclusions), "items": exclusions},
        "failed_extractions": {"failed_extraction_count": len(failed_extractions), "items": failed_extractions},
        "manual_review": {"manual_review_count": len(manual_review), "items": manual_review},
        "follow_up_state": follow_up_state_result,
    }
