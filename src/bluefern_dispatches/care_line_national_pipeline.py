from __future__ import annotations

import json
import os
import ssl
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_record import CareLineReviewedRecord, stable_json_hash
from bluefern_dispatches.care_line_reviewed_export import reviewed_record_from_source
from bluefern_dispatches.care_line_source_registry import (
    CareLineSource,
    CareLineSourceRegistry,
    load_registry,
    source_readiness_reason,
    source_readiness_status,
)
from bluefern_dispatches.care_line_sources import load_pressure_source_registry


COLLECTION_RUNS_ROOT = Path("data/dispatches/care-line/collection-runs")
WORKING_REVIEW_QUEUE_PATH = Path("data/dispatches/care-line/review/current-review-queue.json")
REVIEW_SNAPSHOT_ROOT = Path("data/dispatches/care-line/review/signal-reviews")
LEGACY_PRESSURE_REGISTRY_PATH = Path("data/dispatches/care-line/pressure_source_registry.json")
CANONICAL_REGISTRY_PATH = Path("data/dispatches/care-line/source_registry.json")

PIPELINE_SCHEMA_VERSION = "bluefern.care_line.national_pipeline.v1"
REVIEW_QUEUE_SCHEMA_VERSION = "bluefern.care_line.national_review_queue.v1"
SNAPSHOT_SCHEMA_VERSION = "bluefern.care_line.review_snapshot.v1"
CANDIDATE_REGISTRY_SCHEMA_VERSION = "bluefern.care_line.candidate_registry.v1"
CLUSTERING_SCHEMA_VERSION = "bluefern.care_line.cluster_summary.v1"

REVIEW_PRIORITIES = {"CRITICAL", "HIGH", "STANDARD", "LOW"}
EXCLUSION_REASONS = {
    "unreachable_source",
    "parser_failure",
    "unsupported_source_format",
    "missing_source_date",
    "missing_event_date",
    "missing_geography",
    "unknown_jurisdiction",
    "insufficient_access_consequence",
    "general_financial_distress_only",
    "ownership_change_without_access_effect",
    "duplicate",
    "superseded",
    "stale",
    "background_context_only",
    "resource_listing_only",
    "restoration_without_prior_loss_link",
    "unsupported_taxonomy",
    "private_or_inaccessible_evidence",
    "insufficient_traceability",
    "disabled_source",
    "manual_source_not_automated",
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


def fetch_source(source: CareLineSource, *, timeout: int = 20, allow_insecure_tls: bool = False) -> tuple[bytes, dict[str, Any]]:
    request = urllib.request.Request(source.feed_url, headers={"User-Agent": "BlueFernCareLineNationalPipeline/1.0"})
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        return response.read(), {"http_status": getattr(response, "status", 0) or 0}


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


def parse_source_items(source: CareLineSource, payload: bytes) -> list[dict[str, Any]]:
    if source.adapter_type == "rss":
        return _rss_items(payload)
    if source.adapter_type == "atom":
        return _atom_items(payload)
    if source.adapter_type == "json_feed":
        return _json_feed_items(payload)
    raise ValueError(f"unsupported adapter: {source.adapter_type}")


def discovery_record_from_direct_item(
    item: Mapping[str, Any],
    source: CareLineSource,
    *,
    discovery_date: str,
    rank: int,
    collected_at: str,
) -> dict[str, Any]:
    raw_url = _text(item, "url", "link", "id")
    title = _text(item, "title")
    publication_date = _text(item, "published_at", "published", "updated")
    return {
        "schema_version": "bluefern.care_line.discovery_record.v2",
        "discovery_record_id": _stable_id("care-line-direct-discovery", source.source_id, raw_url, title, publication_date),
        "discovery_provider": f"canonical_{source.adapter_type}",
        "discovery_date": discovery_date,
        "article_url": raw_url,
        "article_title": title,
        "article_publisher": source.publisher,
        "article_publication_date": publication_date[:10],
        "article_description": _text(item, "description", "summary", "content"),
        "created_at": collected_at,
        "discovery_collected_at": collected_at,
        "discovery_rank": rank,
        "discovery_raw_id": _text(item, "id") or _stable_id("feed-item", raw_url, title),
        "discovery_status": "reviewer_usable" if raw_url and title else "malformed",
        "source_id": source.source_id,
        "source_type": source.source_type,
        "source_name": source.name,
        "source_registry_publisher": source.publisher,
        "geographic_scope": source.geographic_scope,
        "state": source.state,
        "raw_article_url": raw_url,
        "normalized_article_url": raw_url,
        "record_fingerprint": stable_json_hash({"source_id": source.source_id, "url": raw_url, "title": title, "published_at": publication_date[:10]}),
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


def _source_candidates_filename(source_id: str) -> str:
    return f"{_slug(source_id) or 'source'}.candidates.json"


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


def begin_collection_run(root: Path, *, run_date: str, source_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
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
        "attempts": [],
    }
    _atomic_write(run_dir / "run-manifest.json", manifest)
    return manifest


def _candidate_source_row(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_record_id": _text(record, "discovery_record_id"),
        "title": _text(record, "article_title"),
        "url": _text(record, "article_url"),
        "canonical_url": _text(record, "article_url"),
        "publisher": _text(record, "article_publisher"),
        "published_at": _text(record, "article_publication_date"),
        "source_published_date": _text(record, "article_publication_date"),
        "retrieved_at": _text(record, "discovery_collected_at", "created_at"),
        "summary_or_snippet": _text(record, "article_description"),
        "evidence_text": _text(record, "article_description"),
        "evidence_text_basis": "feed_description" if _text(record, "article_description") else "missing",
        "pressure_signal": False,
        "pressure_type": "",
        "pressure_reason": "",
        "pressure_summary": _text(record, "article_description", "article_title"),
        "source_family": _text(record, "source_type") or "other",
        "source_role": "discovery_lead",
        "state": _text(record, "state"),
        "location_name": _text(record, "state"),
        "location_scope": "statewide" if _text(record, "state") else "national" if _text(record, "geographic_scope") == "national" else "",
        "affected_groups": [],
        "evidence_level": "feed_metadata",
        "freshness_status": "current" if _text(record, "article_publication_date") else "unknown",
        "freshness_role": "current_signal" if _text(record, "article_publication_date") else "background_context",
        "date_basis": "source_publication_date" if _text(record, "article_publication_date") else "missing",
        "source_freshness_date_basis": "source_publication_date" if _text(record, "article_publication_date") else "missing",
        "source_public_story_eligible": False,
        "primary_eligible": False,
        "primary_disqualification_reason": "",
        "claim_supported": _text(record, "article_description", "article_title"),
        "limitations": "",
        "included": False,
        "excluded": False,
        "exclusion_reason": "",
        "qualifies_for_public_inclusion": False,
        "public_inclusion_bucket": "Other Care Line Signals",
        "included_as_lead": False,
        "included_as_hospital_operations_signal": False,
        "included_as_insurance_affordability_signal": False,
        "included_as_rural_access_signal": False,
        "included_as_maternity_family_signal": False,
        "included_as_emergency_ems_signal": False,
        "included_as_public_health_signal": False,
        "included_as_additional_signal": True,
        "context_only": False,
        "confidence": "medium",
        "source_id": _text(record, "source_id"),
        "source_name": _text(record, "source_name"),
        "source_registry_publisher": _text(record, "source_registry_publisher"),
        "geographic_scope": _text(record, "geographic_scope"),
    }


def normalize_candidate_record(record: Mapping[str, Any], *, artifact_path: str, run_id: str) -> dict[str, Any]:
    source_row = _candidate_source_row(record)
    reviewed = reviewed_record_from_source(source_row, input_path=Path(artifact_path), index=1, reviewer="care-line-phase-d", review_reason="canonical national intake dry run")
    candidate_id = stable_candidate_id(source_row, reviewed)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "source_id": _text(record, "source_id"),
        "source_artifact_path": artifact_path,
        "collection_run_id": run_id,
        "normalization_status": "normalized" if not reviewed.validation_issues() else "needs_review",
        "validation_errors": [issue.model_dump() for issue in reviewed.validation_issues()],
        "duplicate_cluster_hints": cluster_hints(reviewed, source_row=source_row),
        "normalized_record": reviewed.model_dump(mode="json"),
        "first_seen": utc_now(),
        "last_seen": utc_now(),
    }


def stable_candidate_id(source_row: Mapping[str, Any], reviewed: CareLineReviewedRecord) -> str:
    return _stable_id(
        "care_line_candidate",
        _text(source_row, "source_id"),
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
    payload = {
        "subject": subject,
        "jurisdiction": reviewed.state,
        "location_key": _normalized_location_key(reviewed),
        "service_line": reviewed.service_line,
        "event_type": reviewed.event_type,
        "announcement_date": reviewed.announcement_date,
        "effective_date": reviewed.effective_date,
        "source_host": source_host,
        "source_url": source_url,
        "title": reviewed.source_title.casefold(),
        "prior_access_loss_event_id": reviewed.prior_access_loss_event_id,
    }
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
        canonical = sorted(rows, key=lambda item: (str(item.get("candidate_id") or ""), str(item.get("source_id") or "")))[0]
        candidate_ids = sorted(str(item.get("candidate_id") or "") for item in rows)
        reasoning = "same subject, jurisdiction, location, event type, service line, and date spine"
        confidence = "high" if len(rows) > 1 else "single_record"
        clusters.append(
            {
                "cluster_id": cluster_id,
                "canonical_candidate_id": canonical.get("candidate_id", ""),
                "candidate_ids": candidate_ids,
                "candidate_count": len(candidate_ids),
                "duplicate_confidence": confidence,
                "reasoning": reasoning,
            }
        )
    return {
        "schema_version": CLUSTERING_SCHEMA_VERSION,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }


def review_priority(reviewed: CareLineReviewedRecord) -> str:
    if reviewed.event_type == "facility_closure":
        return "CRITICAL"
    if reviewed.event_type == "service_closure" and reviewed.service_line in {"emergency_care", "labor_and_delivery", "maternity", "ambulance_ems"}:
        return "CRITICAL"
    if reviewed.event_type in {"service_suspension", "temporary_facility_suspension", "bankruptcy_service_impact"}:
        return "HIGH"
    if reviewed.event_type in {"service_reduction", "capacity_reduction", "hours_reduction"}:
        return "STANDARD"
    return "LOW"


def build_review_queue(
    candidates: Iterable[Mapping[str, Any]],
    *,
    edition_date: str,
) -> dict[str, Any]:
    rows = []
    clusters = cluster_candidates(candidates)
    canonical_by_cluster = {row["cluster_id"]: row["canonical_candidate_id"] for row in clusters["clusters"]}
    for item in candidates:
        normalized = item.get("normalized_record") if isinstance(item.get("normalized_record"), Mapping) else {}
        reviewed = CareLineReviewedRecord.model_validate(normalized)
        hints = item.get("duplicate_cluster_hints") if isinstance(item.get("duplicate_cluster_hints"), Mapping) else {}
        cluster_id = str(hints.get("cluster_id") or "")
        duplicate_status = "canonical" if canonical_by_cluster.get(cluster_id) == item.get("candidate_id") else "duplicate" if cluster_id else "uncertain"
        rows.append(
            {
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
                "freshness_status": "current" if reviewed.source_publication_date else "unknown",
                "review_priority": review_priority(reviewed),
                "review_reason": "duplicate_review" if duplicate_status == "duplicate" else "needs_editorial_review",
                "originating_run_id": item.get("collection_run_id", ""),
                "source_artifact_path": item.get("source_artifact_path", ""),
                "reviewer_decision": "",
                "reviewer_note": "",
                "exclusion_reason": "",
                "first_seen": item.get("first_seen", ""),
                "last_seen": item.get("last_seen", ""),
            }
        )
    return {
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "edition_date": edition_date,
        "queue_item_count": len(rows),
        "clusters": clusters,
        "items": sorted(rows, key=lambda row: (row["review_priority"], row["candidate_id"])),
    }


def update_candidate_registry(root: Path, *, edition_date: str, candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    path = root / "data/dispatches/care-line/review/candidate-registry.json"
    existing = _load_json(path, {"schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION, "candidates": []})
    by_id = {
        str(row.get("candidate_id") or ""): dict(row)
        for row in existing.get("candidates", [])
        if isinstance(row, Mapping) and str(row.get("candidate_id") or "")
    }
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        current = by_id.get(candidate_id)
        merged = dict(row)
        if current:
            merged["first_seen"] = current.get("first_seen") or row.get("first_seen") or edition_date
        else:
            merged["first_seen"] = row.get("first_seen") or edition_date
        merged["last_seen"] = row.get("last_seen") or edition_date
        by_id[candidate_id] = merged
    payload = {
        "schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION,
        "candidate_count": len(by_id),
        "candidates": [by_id[key] for key in sorted(by_id)],
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
    started_at: str
    completed_at: str
    source_urls: tuple[str, ...]
    failure_reason: str = ""
    parser_version: str = PIPELINE_SCHEMA_VERSION
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
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason="disabled_source",
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        return {"attempt": attempt.to_payload(), "records": [], "candidates": [], "failure": attempt.failure_reason}
    if source_readiness_status(source) == "MANUAL_REVIEW_ONLY":
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="skipped",
            item_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason="manual_source_not_automated",
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        return {"attempt": attempt.to_payload(), "records": [], "candidates": [], "failure": attempt.failure_reason}
    try:
        payload, fetch_meta = fetch_source(source, timeout=fetch_timeout, allow_insecure_tls=allow_insecure_tls)
        items = parse_source_items(source, payload)
    except Exception as exc:  # noqa: BLE001
        failure = f"{type(exc).__name__}: {exc}"
        attempt = CollectionAttempt(
            source_id=source.source_id,
            source_name=source.name,
            readiness=source_readiness_status(source),
            readiness_reason=source_readiness_reason(source),
            collection_status="failed",
            item_count=0,
            started_at=started_at,
            completed_at=utc_now(),
            source_urls=(),
            failure_reason=failure,
        )
        _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
        _atomic_write(run_dir / _source_failure_filename(source.source_id), {"source_id": source.source_id, "failure_reason": failure})
        return {"attempt": attempt.to_payload(), "records": [], "candidates": [], "failure": failure}
    records = [
        discovery_record_from_direct_item(
            item,
            source,
            discovery_date=run_date,
            rank=index,
            collected_at=started_at,
        )
        for index, item in enumerate(items, start=1)
    ]
    content_hash = sha256(payload).hexdigest() if payload else ""
    attempt = CollectionAttempt(
        source_id=source.source_id,
        source_name=source.name,
        readiness=source_readiness_status(source),
        readiness_reason=source_readiness_reason(source),
        collection_status="partial" if source_readiness_status(source) == "AUTOMATED_PARTIAL" else "ok",
        item_count=len(records),
        started_at=started_at,
        completed_at=utc_now(),
        source_urls=tuple(sorted({_text(row, "article_url") for row in records if _text(row, "article_url")})),
        failure_reason=source.limitations if source_readiness_status(source) == "AUTOMATED_PARTIAL" else "",
        content_hash=content_hash,
    )
    artifact_path = run_dir / _source_candidates_filename(source.source_id)
    _atomic_write(run_dir / _source_attempt_filename(source.source_id), attempt.to_payload())
    _atomic_write(artifact_path, {"schema_version": PIPELINE_SCHEMA_VERSION, "records": records})
    candidates = [
        normalize_candidate_record(record, artifact_path=artifact_path.as_posix(), run_id=run_id)
        for record in records
    ]
    return {"attempt": attempt.to_payload(), "records": records, "candidates": candidates, "failure": ""}


def run_national_pipeline(
    root: Path,
    *,
    run_date: str,
    include_partial: bool = True,
    include_manual_review: bool = False,
    allow_insecure_tls: bool = False,
    source_limit: int | None = None,
    fetch_timeout: int = 20,
) -> dict[str, Any]:
    registry = load_canonical_registry(root, include_disabled=True)
    source_rows = collectable_sources(registry, include_partial=include_partial, include_manual_review=include_manual_review)
    if source_limit is not None:
        source_rows = source_rows[:source_limit]
    manifest = begin_collection_run(root, run_date=run_date, source_rows=source_rows)
    run_id = manifest["run_id"]
    attempts = []
    candidates = []
    for source_row in source_rows:
        result = run_collection_attempt(
            root,
            run_date=run_date,
            run_id=run_id,
            source_row=source_row,
            allow_insecure_tls=allow_insecure_tls,
            fetch_timeout=fetch_timeout,
        )
        attempts.append(result["attempt"])
        candidates.extend(result["candidates"])
    candidate_registry = update_candidate_registry(root, edition_date=run_date, candidates=candidates)
    queue_payload = build_review_queue(candidate_registry["candidates"], edition_date=run_date)
    _atomic_write(root / WORKING_REVIEW_QUEUE_PATH, queue_payload)
    final_manifest = {
        **manifest,
        "completed_at": utc_now(),
        "status": "complete",
        "attempts": attempts,
        "candidate_count": len(candidate_registry["candidates"]),
        "queue_item_count": queue_payload["queue_item_count"],
        "cluster_count": queue_payload["clusters"]["cluster_count"],
        "priority_counts": dict(sorted(Counter(row["review_priority"] for row in queue_payload["items"]).items())),
    }
    _atomic_write(root / COLLECTION_RUNS_ROOT / run_date / run_id / "run-manifest.json", final_manifest)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_manifest": final_manifest,
        "candidate_registry": candidate_registry,
        "review_queue": queue_payload,
        "compatibility_pressure_registry": adapt_pressure_registry(root),
    }
