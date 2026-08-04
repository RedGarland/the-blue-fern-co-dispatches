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
from datetime import datetime, timezone
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
WORKING_REVIEW_QUEUE_PATH = REVIEW_ROOT / "current-review-queue.json"
WORKING_BACKLOG_PATH = REVIEW_ROOT / "current-review-backlog.json"
WORKING_EXCLUSIONS_PATH = REVIEW_ROOT / "current-exclusions.json"
WORKING_DUPLICATES_PATH = REVIEW_ROOT / "current-duplicates.json"
WORKING_FAILED_EXTRACTIONS_PATH = REVIEW_ROOT / "current-failed-extractions.json"
REVIEW_SNAPSHOT_ROOT = REVIEW_ROOT / "signal-reviews"
LEGACY_PRESSURE_REGISTRY_PATH = Path("data/dispatches/care-line/pressure_source_registry.json")
CANONICAL_REGISTRY_PATH = Path("data/dispatches/care-line/source_registry.json")
CANDIDATE_REGISTRY_PATH = REVIEW_ROOT / "candidate-registry.json"

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

SOURCE_FAILURE_CLASSES = {"HTTPError", "ValueError", "ParseError", "TimeoutError", "URLError"}

POSITIVE_EVENT_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("facility_closure", "closure", re.compile(r"\b(close|closing|closure|shut(?:ting)? down|cease(?:s|d)? operations?)\b", re.I)),
    ("planned_facility_closure", "closure", re.compile(r"\b(will close|plans? to close|set to close|scheduled to close)\b", re.I)),
    ("temporary_facility_suspension", "suspension", re.compile(r"\b(temp(?:orary|orarily)? (?:close|closure|shut(?:down)?|suspend)|temporarily halt)\b", re.I)),
    ("service_closure", "service", re.compile(r"\b(end(?:ing)?|stop(?:ping)?|discontinu(?:e|ing)|eliminat(?:e|ing))\b", re.I)),
    ("service_suspension", "service", re.compile(r"\b(suspend(?:ed|ing|s)?|halt(?:ed|ing|s)?|pause(?:d|s|ing)? services?|stop admissions|divert(?:ed|ing|s)?)\b", re.I)),
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


def build_run_key(*, run_date: str, source_ids: Iterable[str]) -> str:
    normalized = sorted({str(value).strip() for value in source_ids if str(value).strip()})
    return stable_json_hash({"run_date": run_date, "source_ids": normalized})[:12]


def build_run_id(root: Path, *, run_date: str, source_ids: Iterable[str]) -> str:
    run_key = build_run_key(run_date=run_date, source_ids=source_ids)
    run_root = root / COLLECTION_RUNS_ROOT / run_date
    existing = sorted(path.name for path in run_root.glob(f"{run_date.replace('-', '')}-{run_key}-*") if path.is_dir())
    suffix = len(existing) + 1
    return f"{run_date.replace('-', '')}-{run_key}-{suffix:02d}"


def begin_collection_run(root: Path, *, run_date: str, source_rows: Iterable[dict[str, Any]], settings: Mapping[str, Any]) -> dict[str, Any]:
    source_ids = [_text(row, "source_id") for row in source_rows]
    run_id = build_run_id(root, run_date=run_date, source_ids=source_ids)
    run_dir = root / COLLECTION_RUNS_ROOT / run_date / run_id
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


def _keyword_hits(text: str, patterns: Iterable[tuple[str, re.Pattern[str]]]) -> list[str]:
    hits = []
    for label, pattern in patterns:
        if pattern.search(text):
            hits.append(label)
    return hits


def _sentence_candidates(text: str) -> list[str]:
    prepared = re.sub(r"[\r\n]+", " ", text)
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
    return not re.search(r"\b(after|following|resume|restore|reopen)\b", text, re.I) or not re.search(r"\b(close|closure|shut|halt|suspend|loss)\b", text, re.I)


def _is_service_expansion_only(text: str) -> bool:
    return bool(re.search(r"\b(expand(?:ed|ing|s)?|open(?:ed|ing|s)? new|launch(?:ed|es|ing)? new)\b", text, re.I))


def _extract_subject(title: str, passage: str, *, service_line: str) -> tuple[str, str]:
    patterns = [
        re.compile(r"^(?P<subject>.+?)\s+(?:will\s+)?(?:close|closing|closes|shut(?:ting)? down|suspend(?:s|ed|ing)?|halt(?:s|ed|ing)?|end(?:s|ed|ing)?|reduce(?:s|d|ing)? hours?|reopen(?:s|ed|ing)?|restore(?:s|d|ing)?)\b", re.I),
        re.compile(r"^(?P<subject>.+?)\s+(?:announced?|plans?|planned)\s+to\s+(?:close|suspend|end|reduce|reopen|restore)\b", re.I),
    ]
    for pattern in patterns:
        match = pattern.search(title.strip())
        if match:
            subject = match.group("subject").strip(" -:")
            return subject, subject
    facility_match = re.search(r"\b([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,5}\s+(?:Hospital|Clinic|Medical Center|Health Center|Health System|Center))\b", title + " " + passage)
    if facility_match:
        subject = facility_match.group(1).strip()
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
                score += 2
        if event_type and event_type.replace("_", " ")[:6].lower() in sentence.lower():
            score += 1
        if service_line and service_line.replace("_", " ")[:5].lower() in sentence.lower():
            score += 1
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


def _extract_article_content(html_payload: bytes) -> dict[str, str]:
    decoded = html_payload.decode("utf-8", errors="ignore")
    extractor = _TextExtractor()
    extractor.feed(decoded)
    text = extractor.text()
    return {
        "title": _normalize_text(extractor.title),
        "description": _normalize_text(extractor.meta_description),
        "text": _normalize_text(text),
    }


def _evidence_blob(raw_item: Mapping[str, Any], article_content: Mapping[str, Any] | None) -> str:
    parts = [
        _text(raw_item, "title"),
        _text(raw_item, "description"),
        _text(article_content or {}, "description"),
        _text(article_content or {}, "text"),
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
) -> dict[str, Any]:
    facility_name, provider_name = _subject_or_provider(subject or provider, supporting_passage)
    evidence_blob = _evidence_blob(raw_item, article_content)
    source_title = _text(article_content or {}, "title") or _text(raw_item, "title")
    source_url = _text(raw_item, "item_url")
    source_date = _text(raw_item, "source_publication_date")
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
            "announcement_date": source_date,
            "effective_date": "",
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
) -> tuple[str, dict[str, Any]]:
    if _text(lead, "qualification_status") != "event_lead":
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
            "failed_gates": [],
            "supporting_text": _text(raw_item, "description"),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    article_content: dict[str, Any] | None = None
    if _can_fetch_item_url(source, _text(raw_item, "item_url")):
        try:
            article_payload, _ = fetch_url(_text(raw_item, "item_url"), timeout=fetch_timeout, allow_insecure_tls=allow_insecure_tls)
            article_content = _extract_article_content(article_payload)
        except Exception:  # noqa: BLE001
            article_content = None

    evidence_blob = _evidence_blob(raw_item, article_content)
    passage_source = _text(article_content or {}, "text") or evidence_blob
    service_line = _text(lead, "service_line_hint") or _service_line_from_text(evidence_blob)
    event_type = _text(lead, "event_type_hint") or _event_type_from_text(evidence_blob, service_line=service_line)
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
            "failed_gates": [],
            "supporting_text": _supporting_passage(evidence_blob, event_type, service_line),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    if _text(raw_item, "source_date_state") != "source_dated":
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
            "failed_gates": ["missing_source_date"],
            "supporting_text": _text(raw_item, "description"),
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
            "failed_gates": ["insufficient_bounded_evidence"],
            "supporting_text": _text(raw_item, "description"),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }
    if not supporting_passage:
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
            "classification": "NEEDS_FULL_ARTICLE",
            "exclusion_reason": "insufficient_bounded_evidence",
            "failed_gates": ["insufficient_bounded_evidence"],
            "supporting_text": _text(article_content or {}, "description") or _text(raw_item, "description"),
            "lineage": {"collection_run_id": run_id, "source_artifact_path": artifact_path},
        }

    geography = _extract_geography(raw_item, evidence_blob)
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
    extraction_confidence = min(1.0, 0.55 + 0.07 * len(_keyword_hits(supporting_passage, [(label, pattern) for label, _, pattern in POSITIVE_EVENT_PATTERNS])) + (0.1 if article_content else 0.0))
    full_article_required = bool(_text(lead, "full_article_required")) and not article_content

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
        classification = reason.upper()
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
            "failed_gates": failed_gates,
            "supporting_text": supporting_passage,
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
    )
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
    all_candidates = list(candidates)
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
    critical_high = [row for row in canonical_rows if row["review_priority"] in {"CRITICAL", "HIGH"}]
    standard = [row for row in canonical_rows if row["review_priority"] == "STANDARD"]
    low = [row for row in canonical_rows if row["review_priority"] == "LOW"]
    standard_cap = max(active_queue_limit - len(critical_high), 0)
    active_standard = standard[:standard_cap]
    remaining_after_standard = max(active_queue_limit - len(critical_high) - len(active_standard), 0)
    active_low = low[: min(low_priority_cap, remaining_after_standard)]
    backlog = standard[standard_cap:] + low[min(low_priority_cap, remaining_after_standard) :]
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
) -> dict[str, Any]:
    snapshot_path = root / REVIEW_SNAPSHOT_ROOT / f"{edition_date}.json"
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
    allow_insecure_tls: bool = False,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
) -> dict[str, Any]:
    run_dir = root / COLLECTION_RUNS_ROOT / run_date / run_id
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
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "failure": attempt.failure_reason}
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
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "failure": attempt.failure_reason}
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
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "failure": failure}
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
        return {"attempt": attempt.to_payload(), "raw_items": [], "event_leads": [], "candidates": [], "exclusions": [], "failed_extractions": [], "failure": failure}
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
    qualified_candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    failed_extractions: list[dict[str, Any]] = []
    for raw_item, lead in zip(raw_items, event_leads, strict=False):
        status, payload_row = qualify_event_lead(
            source,
            raw_item,
            lead,
            artifact_path=raw_artifact_path.as_posix(),
            run_id=run_id,
            fetch_timeout=fetch_timeout,
            allow_insecure_tls=allow_insecure_tls,
        )
        if status == "qualified":
            qualified_candidates.append(payload_row)
        elif status == "excluded":
            exclusions.append(payload_row)
        else:
            failed_extractions.append(payload_row)
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
        "failure": "",
    }


def _write_review_private_outputs(
    root: Path,
    *,
    queue_payload: Mapping[str, Any],
    exclusions: list[Mapping[str, Any]],
    failed_extractions: list[Mapping[str, Any]],
) -> None:
    _atomic_write(root / WORKING_REVIEW_QUEUE_PATH, queue_payload)
    _atomic_write(
        root / WORKING_BACKLOG_PATH,
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue_payload.get("edition_date", ""),
            "backlog_item_count": len(queue_payload.get("backlog", [])),
            "items": list(queue_payload.get("backlog", [])),
        },
    )
    _atomic_write(
        root / WORKING_DUPLICATES_PATH,
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": queue_payload.get("edition_date", ""),
            "duplicate_item_count": len(queue_payload.get("duplicates", [])),
            "items": list(queue_payload.get("duplicates", [])),
        },
    )
    _atomic_write(
        root / WORKING_EXCLUSIONS_PATH,
        {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "excluded_item_count": len(exclusions),
            "items": exclusions,
        },
    )
    _atomic_write(
        root / WORKING_FAILED_EXTRACTIONS_PATH,
        {
            "schema_version": EXCLUSION_SCHEMA_VERSION,
            "failed_extraction_count": len(failed_extractions),
            "items": failed_extractions,
        },
    )


def run_national_pipeline(
    root: Path,
    *,
    run_date: str,
    include_partial: bool = True,
    include_manual_review: bool = False,
    allow_insecure_tls: bool = False,
    source_limit: int | None = None,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
) -> dict[str, Any]:
    registry = load_canonical_registry(root, include_disabled=True)
    source_rows = collectable_sources(registry, include_partial=include_partial, include_manual_review=include_manual_review)
    if source_limit is not None:
        source_rows = source_rows[:source_limit]
    settings = {
        "include_partial": include_partial,
        "include_manual_review": include_manual_review,
        "allow_insecure_tls": allow_insecure_tls,
        "source_limit": source_limit,
        "fetch_timeout": fetch_timeout,
        "max_items_per_source": max_items_per_source,
        "active_queue_limit": active_queue_limit,
        "low_priority_cap": low_priority_cap,
    }
    manifest = begin_collection_run(root, run_date=run_date, source_rows=source_rows, settings=settings)
    run_id = manifest["run_id"]
    attempts = []
    raw_items: list[dict[str, Any]] = []
    event_leads: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    failed_extractions: list[dict[str, Any]] = []
    for source_row in source_rows:
        result = run_collection_attempt(
            root,
            run_date=run_date,
            run_id=run_id,
            source_row=source_row,
            allow_insecure_tls=allow_insecure_tls,
            fetch_timeout=fetch_timeout,
            max_items_per_source=max_items_per_source,
        )
        attempts.append(result["attempt"])
        raw_items.extend(result["raw_items"])
        event_leads.extend(result["event_leads"])
        candidates.extend(result["candidates"])
        exclusions.extend(result["exclusions"])
        failed_extractions.extend(result["failed_extractions"])
    candidate_registry = update_candidate_registry(root, edition_date=run_date, candidates=candidates)
    queue_payload = build_review_queue(candidate_registry["candidates"], edition_date=run_date, active_queue_limit=active_queue_limit, low_priority_cap=low_priority_cap)
    _write_review_private_outputs(root, queue_payload=queue_payload, exclusions=exclusions, failed_extractions=failed_extractions)
    qualified_priorities = Counter(
        str((row.get("qualification_result") or {}).get("review_priority_recommendation") or "LOW")
        for row in candidates
    )
    final_manifest = {
        **manifest,
        "completed_at": utc_now(),
        "status": "complete",
        "attempts": attempts,
        "raw_items_retrieved_this_run": len(raw_items),
        "event_leads_created_this_run": sum(1 for lead in event_leads if _text(lead, "qualification_status") == "event_lead"),
        "qualified_candidates_created_this_run": candidate_registry["created_this_run"],
        "candidates_updated_this_run": candidate_registry["updated_this_run"],
        "persistent_candidates_from_prior_runs": candidate_registry["persistent_candidates_from_prior_runs"],
        "excluded_item_count": len(exclusions),
        "failed_extraction_count": len(failed_extractions),
        "active_review_queue_count": queue_payload["queue_item_count"],
        "backlog_item_count": queue_payload["backlog_item_count"],
        "duplicate_item_count": queue_payload["duplicate_item_count"],
        "cluster_count": queue_payload["clusters"]["cluster_count"],
        "priority_counts": dict(sorted(qualified_priorities.items())),
        "stale_candidate_count": candidate_registry["stale_candidate_count"],
        "superseded_candidate_count": candidate_registry["superseded_candidate_count"],
    }
    _atomic_write(root / COLLECTION_RUNS_ROOT / run_date / run_id / "run-manifest.json", final_manifest)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_manifest": final_manifest,
        "candidate_registry": candidate_registry,
        "review_queue": queue_payload,
        "compatibility_pressure_registry": adapt_pressure_registry(root),
        "exclusions": {"excluded_item_count": len(exclusions), "items": exclusions},
        "failed_extractions": {"failed_extraction_count": len(failed_extractions), "items": failed_extractions},
    }
