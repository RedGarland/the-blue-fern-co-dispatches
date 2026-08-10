"""Preservation-first archive and normalization for historical agent exports."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from .ice_historical import (
    extract_detection_date,
    ice_aggregate_metrics,
    ice_historical_identity,
    ice_match_targets,
    ice_report as _ice_report,
    normalize_detection_date,
    normalize_ice_record,
)

DOMAINS = ("food-line", "care-line", "gaza", "ice")
SCHEMA_VERSION = "historical_agent_raw_v1"


class HistoricalEnvelopeError(ValueError):
    """Raised when a preserved text envelope contains an invalid structured payload."""


def parse_historical_input(raw: bytes) -> tuple[Any, dict[str, Any]]:
    """Parse JSON or one embedded JSON fence without changing the preserved bytes."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), {"normalization_method": "structured_json"}
    except json.JSONDecodeError:
        pass

    fence_pattern = re.compile(r"(?ms)^```([^\r\n`]*)\r?\n(.*?)^```[ \t]*(?:\r?\n|$)")
    fences = list(fence_pattern.finditer(text))
    if not fences:
        return {"raw_text": text}, {"normalization_method": "text_envelope"}
    if len(fences) != 1:
        raise HistoricalEnvelopeError("text envelope must contain exactly one fenced JSON object")
    fence = fences[0]
    label = fence.group(1).strip().lower()
    if label not in {"", "json"}:
        raise HistoricalEnvelopeError("text envelope fence must be unlabeled or labeled json")
    try:
        payload = json.loads(fence.group(2))
    except json.JSONDecodeError as exc:
        raise HistoricalEnvelopeError("embedded JSON fence is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise HistoricalEnvelopeError("embedded JSON fence is not a valid agent-run envelope")
    return payload, {
        "normalization_method": "embedded_json_envelope",
        "private_text_provenance": {
            "before_fence": text[: fence.start()],
            "after_fence": text[fence.end() :],
        },
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(value))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_root(root: Path, domain: str) -> Path:
    if domain not in DOMAINS: raise ValueError(f"unsupported domain: {domain}")
    return root / "data" / "agent-history" / domain


def _date_values(value: Any) -> list[str]:
    text = str(value or "")
    return re.findall(r"20\d{2}-\d{2}-\d{2}", text)


def _load_source(path: Path) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    payload, _ = parse_historical_input(raw)
    return raw, payload


def validate_input(path: Path, *, domain: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload, parse_metadata = parse_historical_input(raw)
    except HistoricalEnvelopeError as exc:
        return {"valid": False, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "text", "finding_count": 0, "invalid_records": [], "invalid_detection_dates": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False, "error": str(exc)}
    result: dict[str, Any] = {"valid": True, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "json" if parse_metadata["normalization_method"] == "structured_json" else "text", "finding_count": 0, "invalid_records": [], "invalid_detection_dates": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False}
    result["normalization_method"] = parse_metadata["normalization_method"]
    if parse_metadata["normalization_method"] == "text_envelope":
        result["finding_count"] = 1
        if domain == "ice":
            try:
                extract_detection_date(str(payload.get("raw_text") or ""))
            except ValueError:
                result["invalid_detection_dates"].append(0)
                result["valid"] = False
        return result
    if domain not in DOMAINS: result.update(valid=False, error="unsupported_domain"); return result
    if payload is None:
        if not raw.strip(): result.update(valid=False, error="empty_input")
        result["finding_count"] = 1
        return result
    if isinstance(payload, dict) and "findings" in payload: rows = payload.get("findings")
    elif isinstance(payload, list): rows = payload
    elif isinstance(payload, dict): rows = [payload]
    else: rows = []
    if not isinstance(rows, list): result.update(valid=False, error="findings_must_be_list"); return result
    if isinstance(payload, dict) and payload.get("raw_bytes_base64") is not None:
        try: base64.b64decode(str(payload["raw_bytes_base64"]), validate=True)
        except (ValueError, TypeError): result["malformed_base64"] = True
    result["finding_count"] = len(rows)
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict): result["invalid_records"].append(index); continue
        if domain == "ice" and "detection_date" in row:
            try:
                normalize_detection_date(row.get("detection_date"))
            except ValueError:
                result["invalid_detection_dates"].append(index)
        identity = json.dumps({"url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "").lower().split("?")[0].rstrip("/"), "title": str(row.get("title") or row.get("headline") or "").lower().strip(), "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10]}, sort_keys=True)
        if identity in seen: result["duplicates"].append(index)
        seen.add(identity)
        if not _date_values(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or row.get("discovered_at")): result["missing_dates"].append(index)
        if not str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("summary") or row.get("summary_or_snippet") or "").strip(): result["missing_evidence"].append(index)
    result["valid"] = not (result["invalid_records"] or result["invalid_detection_dates"] or result["missing_dates"] or result["missing_evidence"] or result["malformed_base64"])
    return result


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "findings" in payload: payload = payload["findings"]
    if isinstance(payload, dict): payload = [payload]
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _existing_text(root: Path, domain: str) -> str:
    pieces: list[str] = []
    roots = [root / "data" / "dispatches" / domain, root / "data" / "universal_events"]
    if domain == "gaza": roots.append(root / "data" / "dispatches" / "gaza")
    for base in roots:
        if not base.exists(): continue
        for path in base.rglob("*.json"):
            try: pieces.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError: pass
    return "\n".join(pieces).lower()


def _care_published_ids(root: Path) -> set[str]:
    text = _existing_text(root, "care-line")
    ids = set(re.findall(r"[\"']?(?:event_id|id)[\"']?\s*[:=]\s*[\"']([^\"']+)", text, flags=re.I))
    if "published" not in text: return set()
    return ids


def _care_json_objects(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read only private Care Line JSON artifacts used for historical matching."""
    bases = [
        root / "data" / "universal_events" / "publication-state",
        root / "data" / "universal_events" / "shadow" / "care-line",
        root / "data" / "dispatches" / "care-line" / "reviewed",
        root / "data" / "dispatches" / "care-line" / "evidence-reviews",
        root / "data" / "dispatches" / "care-line" / "sources",
        root / "data" / "dispatches" / "care-line" / "queue-runs",
        root / "data" / "agent-history" / "care-line" / "normalized",
    ]
    objects: list[tuple[str, dict[str, Any]]] = []
    for base in bases:
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

            def visit(item: Any) -> None:
                if isinstance(item, dict):
                    objects.append((str(path.relative_to(root)), item))
                    for child in item.values():
                        visit(child)
                elif isinstance(item, list):
                    for child in item:
                        visit(child)

            visit(value)
    return objects


def care_line_match_targets(root: Path) -> dict[str, Any]:
    """Build the private Care Line identity index; public output is never consulted."""
    objects = _care_json_objects(root)
    published: dict[str, str] = {}
    reviewed: dict[str, str] = {}
    sources: dict[str, list[dict[str, str]]] = {}
    queue: dict[str, str] = {}
    historical: set[str] = set()
    ledger = root / "data" / "universal_events" / "publication-state" / "care-line-signal-wire.json"
    try:
        ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger_value = {}
    for event_id in (ledger_value.get("events", {}) if isinstance(ledger_value, dict) else {}):
        published[str(event_id)] = str(ledger)

    def clean_url(value: Any) -> str:
        return str(value or "").strip().lower().split("?")[0].rstrip("/")

    for path, item in objects:
        event_id = str(item.get("event_id") or item.get("proposed_event_id") or "").strip()
        source_url = clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url"))
        source_id = str(item.get("source_record_id") or item.get("producer_record_id") or item.get("source_item_id") or item.get("record_id") or "")
        if source_url:
            sources.setdefault(source_url, []).append({"path": path, "source_record_id": source_id, "event_id": event_id})
        if event_id and event_id not in published:
            status = str(item.get("review_status") or item.get("revision_status") or item.get("state") or item.get("status") or "").lower()
            if ("queue" in path and status not in {"published", "failed", "rejected"}) or status in {"reviewed", "approved", "corrected", "review_ready", "approved_for_release", "queued", "publishing"}:
                reviewed.setdefault(event_id, path)
            if "queue" in path:
                queue.setdefault(event_id, path)
        if "agent-history" in path and (item.get("domain") == "care-line" or path.replace("\\", "/").startswith("data/agent-history/care-line/")):
            identity = json.dumps({"url": clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url")), "title": str(item.get("title") or item.get("headline") or "").lower().strip(), "date": str(item.get("source_published_at") or item.get("published_at") or item.get("event_date") or "")[:10]}, sort_keys=True)
            historical.add(identity)
    return {"published_events": published, "reviewed_events": reviewed, "sources": sources, "queue": queue, "historical_identities": historical}


def _care_identity(row: dict[str, Any]) -> str:
    return json.dumps({
        "url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "").lower().split("?")[0].rstrip("/"),
        "title": str(row.get("title") or row.get("headline") or "").lower().strip(),
        "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10],
    }, sort_keys=True)


def _care_report(record: dict[str, Any]) -> dict[str, Any]:
    """Return the stable per-finding operational report contract."""
    return {field: record.get(field) for field in (
        "raw_sha256", "agent_name", "agent_run_id", "source_url", "canonical_source_url",
        "source_published_at", "source_published_date", "event_date", "announcement_date", "effective_date",
        "facility_name", "facility", "organization", "location_name", "location", "city", "county", "state",
        "service_affected", "service_line", "event_type", "access_direction", "historical_outcome",
        "matched_event_id", "match_basis", "queue_action", "candidate_created", "review_status",
        "publication_eligible", "publication_approval", "exclusion_reason", "provenance_links",
    )}


def _clean_url(value: Any) -> str:
    return str(value or "").strip().lower().split("?")[0].rstrip("/")


def _normalized_headline(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _json_dicts(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            objects.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return objects


def food_line_match_targets(root: Path) -> dict[str, Any]:
    """Build exact Food Line public, intake, source, and historical indexes."""

    categorized_paths: dict[str, list[Path]] = {
        "editions": [
            root / "output" / "dispatches" / "food-line" / "editions",
            root / "output" / "site" / "food-line" / "editions",
        ],
        "intake": [
            root / "data" / "dispatches" / "food-line" / "agent-intake",
        ],
        "inbox": [
            root / "data" / "dispatches" / "food-line" / "agent-inbox",
        ],
        "source_ledgers": [
            root / "data" / "dispatches" / "food-line" / "sources",
            root / "data" / "dispatches" / "food-line" / "normalized",
            root / "data" / "dispatches" / "food-line" / "curated",
            root / "data" / "dispatches" / "food-line" / "editions",
            root / "data" / "dispatches" / "food-line" / "source_registry.json",
            root / "data" / "dispatches" / "food-line" / "pressure_source_registry.json",
            root / "data" / "records" / "sources.json",
        ],
    }

    def files_for(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if not path.exists():
            return []
        return [
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file()
            and candidate.suffix.lower() in {".csv", ".html", ".json", ".jsonl", ".md", ".txt"}
        ]

    categorized_urls: dict[str, dict[str, list[str]]] = {
        category: {} for category in categorized_paths
    }
    url_pattern = re.compile(r"https?://[^\s\"'<>]+", flags=re.I)
    url_fields = ("canonical_url", "canonical_source_url", "source_url", "url")
    for category, configured_paths in categorized_paths.items():
        for path in sorted(
            {candidate for configured in configured_paths for candidate in files_for(configured)}
        ):
            relative = str(path.relative_to(root))
            urls: set[str] = set()
            if path.suffix.lower() in {".json", ".jsonl"}:
                for item in _json_dicts(_json_value(path)):
                    for field in url_fields:
                        url = _clean_url(item.get(field))
                        if url:
                            urls.add(url)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            urls.update(_clean_url(match) for match in url_pattern.findall(text))
            for url in sorted(url for url in urls if url):
                categorized_urls[category].setdefault(url, []).append(relative)

    historical: list[dict[str, str]] = []
    historical_root = root / "data" / "agent-history" / "food-line" / "normalized"
    if historical_root.exists():
        for path in sorted(historical_root.rglob("*.json")):
            relative = str(path.relative_to(root))
            for item in _json_dicts(_json_value(path)):
                finding_id = str(
                    item.get("finding_id")
                    or item.get("agent_finding_id")
                    or item.get("candidate_id")
                    or ""
                )
                duplicate_key = str(item.get("agent_duplicate_key") or "")
                url = _clean_url(
                    item.get("canonical_url")
                    or item.get("canonical_source_url")
                    or item.get("source_url")
                    or item.get("url")
                )
                published_date = str(
                    item.get("source_published_date")
                    or item.get("source_published_at")
                    or item.get("published_at")
                    or ""
                )[:10]
                if finding_id or duplicate_key or url:
                    historical.append(
                        {
                            "agent_duplicate_key": duplicate_key,
                            "finding_id": finding_id,
                            "path": relative,
                            "source_published_date": published_date,
                            "source_url": url,
                        }
                    )

    return {**categorized_urls, "historical": historical}


def gaza_match_targets(root: Path) -> dict[str, Any]:
    """Build Gaza's private edition, source, cluster, and historical identity indexes."""
    editions: dict[str, dict[str, str]] = {}
    publication_records: list[dict[str, str]] = []

    edition_records = root / "data" / "records" / "editions.json"
    for item in _json_dicts(_json_value(edition_records)):
        if str(item.get("dispatch_id") or "") != "dispatch-gaza" and str(item.get("dispatch_slug") or item.get("slug") or "") != "gaza":
            continue
        edition_date = str(item.get("edition_date") or "")[:10]
        status = str(item.get("status") or "").lower()
        if edition_date and (status == "public" or item.get("public_exposed") is True):
            editions[edition_date] = {
                "edition_id": str(item.get("edition_id") or f"gaza-{edition_date}"),
                "path": str(edition_records.relative_to(root)),
            }

    manifest_root = root / "output" / "dispatches" / "gaza" / "editions"
    if manifest_root.exists():
        for path in manifest_root.glob("*/edition_manifest.json"):
            value = _json_value(path)
            if not isinstance(value, dict) or str(value.get("dispatch_slug") or "") != "gaza":
                continue
            edition_date = str(value.get("edition_date") or path.parent.name)[:10]
            if edition_date and (value.get("public_exposed") is True or value.get("is_free_public") is True):
                editions.setdefault(edition_date, {
                    "edition_id": str(value.get("edition_id") or f"gaza-{edition_date}"),
                    "path": str(path.relative_to(root)),
                })

    run_root = root / "data" / "dispatches" / "gaza" / "editions"
    if run_root.exists():
        for path in run_root.glob("*/run_manifest.json"):
            value = _json_value(path)
            if isinstance(value, dict):
                publication_records.append({
                    "edition_date": str(value.get("edition_date") or path.parent.name)[:10],
                    "path": str(path.relative_to(root)),
                    "public_url": str(value.get("public_url") or ""),
                })

    source_paths: list[Path] = []
    source_patterns = (
        ("data/dispatches/gaza/sources", "**/*.json"),
        ("data/dispatches/gaza/raw", "**/raw_sources.json"),
        ("data/dispatches/gaza/normalized", "**/normalized_sources.json"),
        ("data/dispatches/gaza/curated", "**/curation_manifest.json"),
        ("output/dispatches/gaza/editions", "*/sources_manifest.json"),
    )
    for base_name, pattern in source_patterns:
        base = root / base_name
        if base.exists():
            source_paths.extend(path for path in base.glob(pattern) if ".template." not in path.name)
    shared_sources = root / "data" / "records" / "sources.json"
    if shared_sources.exists():
        source_paths.append(shared_sources)

    sources_by_url: dict[str, list[dict[str, Any]]] = {}
    sources_by_id: dict[str, list[dict[str, Any]]] = {}
    sources_by_composite: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(set(source_paths)):
        relative = str(path.relative_to(root))
        for item in _json_dicts(_json_value(path)):
            if path == shared_sources and str(item.get("dispatch_id") or "") != "dispatch-gaza" and not str(item.get("edition_id") or "").startswith("gaza-"):
                continue
            url = _clean_url(item.get("canonical_url") or item.get("canonical_source_url") or item.get("url") or item.get("source_url"))
            source_id = str(item.get("source_record_id") or item.get("source_id") or "")
            if not url and not source_id:
                continue
            edition_date = str(item.get("edition_date") or "")[:10]
            if not edition_date and str(item.get("edition_id") or "").startswith("gaza-"):
                edition_date = str(item.get("edition_id"))[5:15]
            if not edition_date and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", path.parent.name):
                edition_date = path.parent.name
            story_ids = [str(value) for value in (item.get("used_in_story_ids") or item.get("source_record_ids") or [])]
            published = edition_date in editions and (
                bool(story_ids)
                or "output/dispatches/gaza/editions/" in relative.replace("\\", "/")
                or str(item.get("edition_id") or "").startswith("gaza-")
            )
            source = {
                "path": relative,
                "source_record_id": source_id,
                "url": url,
                "title": str(item.get("title") or ""),
                "publisher": str(item.get("publisher") or ""),
                "source_date": str(item.get("source_published_at") or item.get("published_at") or "")[:10],
                "edition_date": edition_date,
                "story_ids": story_ids,
                "published": published,
                "gaza_role": str(item.get("gaza_role") or item.get("story_scope") or item.get("region_scope") or ""),
                "source_role": str(item.get("source_role") or item.get("attribution_mode") or item.get("claim_status") or item.get("source_type") or ""),
            }
            if url:
                sources_by_url.setdefault(url, []).append(source)
            if source_id:
                sources_by_id.setdefault(source_id, []).append(source)
            composite = json.dumps({
                "title": _normalized_headline(source["title"]),
                "date": source["source_date"],
                "publisher": str(source["publisher"]).lower().strip(),
            }, sort_keys=True)
            if source["title"] and source["source_date"] and source["publisher"]:
                sources_by_composite.setdefault(composite, []).append(source)

    cluster_paths = [
        root / "data" / "records" / "story_memory.json",
        *sorted((root / "data" / "dispatches" / "gaza" / "editions").glob("*/dedupe_report.json")),
        *sorted((root / "output" / "dispatches" / "gaza" / "editions").glob("*/dedupe_report.json")),
        *sorted((root / "output" / "dispatches" / "gaza" / "editions").glob("*/curation_manifest.json")),
    ]
    clusters_by_id: dict[str, list[dict[str, Any]]] = {}
    clusters_by_url: dict[str, list[dict[str, Any]]] = {}
    clusters_by_composite: dict[str, list[dict[str, Any]]] = {}
    for path in cluster_paths:
        if not path.exists():
            continue
        relative = str(path.relative_to(root))
        for item in _json_dicts(_json_value(path)):
            if path.name == "story_memory.json" and str(item.get("dispatch_slug") or "") != "gaza":
                continue
            identifiers = {
                str(item.get(key) or "") for key in
                ("story_id", "event_cluster_id", "cluster_id", "topic_fingerprint", "normalized_event_key", "prior_story_matched")
                if item.get(key)
            }
            urls = {
                _clean_url(value) for value in
                [item.get("source_url"), item.get("canonical_url"), *(item.get("source_urls") or []), *(item.get("canonical_urls") or [])]
                if value
            }
            if not identifiers and not urls:
                continue
            cluster_id = str(
                item.get("event_cluster_id")
                or item.get("cluster_id")
                or item.get("story_id")
                or item.get("topic_fingerprint")
                or item.get("normalized_event_key")
                or item.get("prior_story_matched")
                or ""
            )
            cluster = {
                "path": relative,
                "cluster_id": cluster_id,
                "identifiers": sorted(identifiers),
                "edition_date": str(item.get("edition_date") or item.get("first_seen_date") or "")[:10],
                "title": str(item.get("title") or ""),
                "publisher": str((item.get("publisher_names") or [""])[0] if isinstance(item.get("publisher_names"), list) else item.get("publisher") or ""),
                "urls": sorted(urls),
            }
            for identifier in identifiers:
                clusters_by_id.setdefault(identifier, []).append(cluster)
            for url in urls:
                clusters_by_url.setdefault(url, []).append(cluster)
            composite = json.dumps({
                "title": _normalized_headline(cluster["title"]),
                "date": cluster["edition_date"],
                "publisher": str(cluster["publisher"]).lower().strip(),
            }, sort_keys=True)
            if cluster["title"] and cluster["edition_date"] and cluster["publisher"]:
                clusters_by_composite.setdefault(composite, []).append(cluster)

    historical_identities: set[str] = set()
    historical_root = root / "data" / "agent-history" / "gaza" / "normalized"
    if historical_root.exists():
        for path in historical_root.rglob("*.json"):
            for item in _json_dicts(_json_value(path)):
                if item.get("domain") not in (None, "", "gaza"):
                    continue
                identity = {
                    "url": _clean_url(item.get("canonical_source_url") or item.get("source_url") or item.get("url")),
                    "title": _normalized_headline(item.get("title") or item.get("headline")),
                    "date": str(item.get("source_published_at") or item.get("published_at") or item.get("event_date") or "")[:10],
                }
                if any(identity.values()):
                    historical_identities.add(json.dumps(identity, sort_keys=True))
    return {
        "editions": editions,
        "publication_records": publication_records,
        "sources_by_url": sources_by_url,
        "sources_by_id": sources_by_id,
        "sources_by_composite": sources_by_composite,
        "clusters_by_id": clusters_by_id,
        "clusters_by_url": clusters_by_url,
        "clusters_by_composite": clusters_by_composite,
        "historical_identities": historical_identities,
    }


def _gaza_identity(row: dict[str, Any]) -> str:
    return json.dumps({
        "url": _clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url")),
        "title": _normalized_headline(row.get("title") or row.get("headline")),
        "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10],
    }, sort_keys=True)


def _gaza_report(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in (
        "raw_sha256", "agent_name", "agent_run_id", "source_url", "canonical_source_url",
        "publisher", "source_published_at", "source_date", "event_date", "title",
        "gaza_role", "source_role", "matched_edition_date", "matched_source_or_cluster_id",
        "match_basis", "historical_outcome", "candidate_created", "provenance_only",
        "review_status", "publication_eligible", "publication_approval", "exclusion_reason",
        "ambiguity_reason", "provenance_links",
    )}


def _normalize_gaza_record(
    row: dict[str, Any],
    *,
    payload: Any,
    raw_sha256: str,
    targets: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    record = dict(row)
    source_url = _clean_url(row.get("canonical_source_url") or row.get("source_url") or row.get("url"))
    source_id = str(row.get("manual_source_identifier") or row.get("source_record_id") or row.get("source_id") or "")
    source_date = str(row.get("source_published_at") or row.get("published_at") or "")[:10]
    event_date = str(row.get("event_date") or "")[:10]
    title = str(row.get("title") or row.get("headline") or "")
    publisher = str(row.get("publisher") or "")
    edition_date = str(row.get("edition_date") or "")[:10]
    agent_name = str(payload.get("agent_name") or "") if isinstance(payload, dict) else ""
    agent_run_id = str(payload.get("agent_run_id") or "") if isinstance(payload, dict) else ""
    source_matches = list(targets["sources_by_url"].get(source_url, [])) if source_url else []
    if source_id:
        source_matches.extend(targets["sources_by_id"].get(source_id, []))
    if not source_matches and not source_url and not source_id and title and source_date and publisher:
        composite = json.dumps({"title": _normalized_headline(title), "date": source_date, "publisher": publisher.lower().strip()}, sort_keys=True)
        source_matches.extend(targets["sources_by_composite"].get(composite, []))
    source_matches = list({(item["path"], item["source_record_id"], item["url"]): item for item in source_matches}.values())

    cluster_identifiers = [
        str(row.get(key) or "") for key in
        ("event_cluster_id", "cluster_id", "story_id", "topic_fingerprint", "normalized_event_key")
        if row.get(key)
    ]
    cluster_matches: list[dict[str, Any]] = []
    for identifier in cluster_identifiers:
        cluster_matches.extend(targets["clusters_by_id"].get(identifier, []))
    if not cluster_matches and source_url:
        cluster_matches.extend(targets["clusters_by_url"].get(source_url, []))
    if not cluster_matches and not source_url and not cluster_identifiers and title and (source_date or event_date) and publisher:
        composite = json.dumps({"title": _normalized_headline(title), "date": source_date or event_date, "publisher": publisher.lower().strip()}, sort_keys=True)
        cluster_matches.extend(targets["clusters_by_composite"].get(composite, []))
    cluster_matches = list({(item["path"], item["cluster_id"]): item for item in cluster_matches}.values())

    historical_outcome = "new_historical_candidate"
    match_basis = "unmatched_traceable_finding"
    matched_edition_date = ""
    matched_id = ""
    candidate_created = True
    provenance_only = False
    review_status = "pending_review"
    exclusion_reason = ""
    ambiguity_reason = ""
    provenance_links: list[dict[str, str]] = []

    if _gaza_identity(row) in targets["historical_identities"]:
        historical_outcome, match_basis = "duplicate_historical", "historical_identity"
        candidate_created, review_status = False, "excluded"
    else:
        published_sources = [item for item in source_matches if item.get("published") and item.get("edition_date") in targets["editions"]]
        if published_sources:
            selected = sorted(published_sources, key=lambda item: (item["edition_date"], item["source_record_id"], item["path"]))[0]
            historical_outcome, match_basis = "matched_published_edition", "canonical_source_url_and_published_source"
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("source_record_id") or (selected.get("story_ids") or [""])[0])
            candidate_created, provenance_only, review_status = False, True, "excluded"
        elif source_matches:
            selected = sorted(source_matches, key=lambda item: (item["source_record_id"], item["path"]))[0]
            historical_outcome = "matched_existing_source"
            match_basis = "canonical_source_url" if source_url and selected.get("url") == source_url else ("manual_source_identifier" if source_id else "title_date_publisher")
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("source_record_id") or "")
            candidate_created, provenance_only, review_status = False, True, "excluded"
        elif cluster_matches:
            selected = sorted(cluster_matches, key=lambda item: (item["cluster_id"], item["path"]))[0]
            historical_outcome = "matched_existing_cluster"
            match_basis = "cluster_identifier" if cluster_identifiers else ("canonical_source_url" if source_url else "title_date_publisher")
            matched_edition_date = str(selected.get("edition_date") or "")
            matched_id = str(selected.get("cluster_id") or "")
            candidate_created, provenance_only, review_status = False, True, "excluded"
        else:
            context = " ".join(str(row.get(key) or "") for key in ("gaza_role", "story_scope", "region_scope", "location", "location_name")).lower()
            evidence = str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("evidence_text") or "").strip()
            role_context = any(value in context for value in ("gaza_adjacent_context", "context_only", "archived_context"))
            west_bank_only = "west bank" in context and "gaza" not in context
            explicit_non_gaza = any(value in context for value in ("lebanon", "israel-only", "non-gaza")) and "gaza" not in context
            explicit_context = role_context or west_bank_only or explicit_non_gaza or row.get("is_gaza_relevant") is False
            if explicit_context:
                historical_outcome, match_basis = "archived_context", "non_gaza_or_west_bank_context"
                candidate_created, review_status = False, "historical_context"
                exclusion_reason = "traceable non-Gaza or West Bank-only material retained as historical context"
            elif not evidence:
                historical_outcome, match_basis = "archived_invalid", "missing_exact_evidence"
                candidate_created, review_status = False, "excluded"
                exclusion_reason = "missing exact supporting evidence"
            elif not source_url and not cluster_identifiers:
                historical_outcome, match_basis = "needs_manual_review", "missing_source_or_cluster_identity"
                candidate_created, review_status = False, "pending_review"
                ambiguity_reason = "finding lacks a canonical source URL or explicit cluster identity"

    for item in source_matches:
        provenance_links.append({
            "path": str(item.get("path") or ""),
            "source_record_id": str(item.get("source_record_id") or ""),
            "story_id": str((item.get("story_ids") or [""])[0]),
            "edition_date": str(item.get("edition_date") or ""),
        })
    for item in cluster_matches:
        provenance_links.append({
            "path": str(item.get("path") or ""),
            "source_record_id": "",
            "story_id": str(item.get("cluster_id") or ""),
            "edition_date": str(item.get("edition_date") or ""),
        })
    matched_source = source_matches[0] if source_matches else {}
    record.update({
        "domain": "gaza",
        "historical_backfill": True,
        "raw_sha256": raw_sha256,
        "agent_name": agent_name,
        "agent_run_id": agent_run_id,
        "source_url": str(row.get("source_url") or row.get("url") or ""),
        "canonical_source_url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or ""),
        "publisher": publisher or str(matched_source.get("publisher") or ""),
        "source_published_at": str(row.get("source_published_at") or row.get("published_at") or ""),
        "source_date": source_date,
        "event_date": str(row.get("event_date") or ""),
        "title": title,
        "gaza_role": str(row.get("gaza_role") or row.get("story_scope") or row.get("region_scope") or matched_source.get("gaza_role") or ""),
        "source_role": str(row.get("source_role") or row.get("attribution_mode") or row.get("claim_status") or row.get("source_type") or matched_source.get("source_role") or ""),
        "matched_edition_date": matched_edition_date,
        "matched_source_or_cluster_id": matched_id,
        "match_basis": match_basis,
        "historical_outcome": historical_outcome,
        "deduplication_outcome": historical_outcome,
        "candidate_created": candidate_created,
        "provenance_only": provenance_only,
        "review_status": review_status,
        "publication_eligible": False,
        "publication_approval": False,
        "exclusion_reason": exclusion_reason or None,
        "ambiguity_reason": ambiguity_reason or None,
        "provenance_links": provenance_links,
    })
    if historical_outcome in {"archived_context", "archived_invalid"}:
        record["archive_status"] = "archived"
    return record, historical_outcome


def normalize_records(root: Path, domain: str, payload: Any, *, raw_sha256: str, captured_at: str, correction: dict[str, Any] | None = None, normalization_metadata: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = _rows(payload)
    if domain in {"care-line", "gaza", "ice"} and correction is not None:
        label = {"care-line": "Care Line", "gaza": "Gaza", "ice": "ICE"}[domain]
        if correction.get("raw_sha256") != raw_sha256:
            raise ValueError(f"{label} normalization sidecar raw_sha256 does not match the preserved alert")
        if correction.get("domain") != domain:
            raise ValueError(f"{label} normalization sidecar domain mismatch")
        if correction.get("normalization_type") != "prose_envelope_to_structured_findings":
            raise ValueError(f"unsupported {label} normalization sidecar type")
        if correction.get("approved") is not True or correction.get("approval_scope") != "historical_normalization_only":
            raise ValueError(f"{label} sidecar approval is not limited to historical normalization")
        if correction.get("publication_approval") is not False:
            raise ValueError(f"{label} normalization sidecar cannot grant publication approval")
        rows = [dict(row) for row in correction.get("findings", []) if isinstance(row, dict)]
        if not rows or len(rows) != len(correction.get("findings", [])):
            raise ValueError(f"{label} normalization sidecar findings must be a non-empty list of objects")
        if domain == "ice":
            finding_ids = [str(row.get("finding_id") or "").strip() for row in rows]
            identities = [ice_historical_identity(row) for row in rows]
            if any(not finding_id for finding_id in finding_ids):
                raise ValueError("ICE normalization sidecar findings require stable finding_id values")
            if len(set(finding_ids)) != len(finding_ids) or len(set(identities)) != len(identities):
                raise ValueError("ICE normalization sidecar contains conflicting findings")
    existing = _existing_text(root, domain)
    published_care = _care_published_ids(root) if domain == "care-line" else set()
    normalized: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    if domain == "food-line":
        if isinstance(payload, dict) and "raw_text" in payload and "findings" not in payload:
            record = dict(payload); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": "needs_manual_review"})
            if normalization_metadata: record.update(normalization_metadata)
            return [record], {"needs_manual_review": 1}
        working_payload = payload
        if correction:
            target_url = str(correction.get("source_url") or "").rstrip("/").lower()
            replacement = correction.get("replacement_exact_supporting_passage") or correction.get("supplemental_exact_supporting_passage")
            if not target_url or not isinstance(replacement, str) or not replacement.strip(): raise ValueError("correction requires source_url and exact supporting passage")
            working_payload = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(working_payload, dict):
                working_payload["findings"] = [dict(row, exact_supporting_passage=replacement) if str(row.get("canonical_source_url") or row.get("source_url") or "").rstrip("/").lower() == target_url else row for row in _rows(payload)]
        findings = adapt_food_line_agent_output(working_payload, agent_name=str(payload.get("agent_name") if isinstance(payload, dict) else "historical-agent"), agent_run_id=str(payload.get("agent_run_id") if isinstance(payload, dict) else ""), discovered_at=captured_at)
        for finding in findings:
            candidate = map_finding_to_food_line_candidate(finding, edition_date=(finding.source_published_at[:10] if finding.source_published_at[:10] else captured_at[:10]))
            candidate.update({"historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256})
            if normalization_metadata: candidate.update(normalization_metadata)
            if correction:
                candidate["evidence_correction_provenance"] = {
                    "schema_version": correction.get("schema_version", ""),
                    "raw_record_sha256": correction.get("raw_record_sha256", ""),
                    "source_url": correction.get("source_url", ""),
                    "reviewer": correction.get("reviewer", ""),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approval_scope": correction.get("approval_scope", ""),
                    "publication_approval": correction.get("publication_approval", False),
                }
            key = finding.duplicate_key
            outcome = "duplicate_historical" if key and key in existing else ("invalid" if candidate.get("exclusion_reason") else "new_historical_candidate")
            candidate["deduplication_outcome"] = outcome
            candidate["historical_outcome"] = "archived_invalid" if outcome == "invalid" else outcome
            candidate["candidate_created"] = outcome in {"new_historical_candidate", "matched_existing"}
            candidate["publication_eligible"] = False if outcome == "invalid" else bool(candidate.get("eligible_for_review"))
            candidate["publication_approval"] = False
            if outcome == "invalid":
                candidate.update({"archive_status": "archived", "normalization_status": "completed_with_invalid_findings", "review_status": "excluded"})
            else:
                candidate.update({"archive_status": "archived", "normalization_status": "completed"})
            outcomes[outcome] += 1; normalized.append(candidate)
    elif domain == "gaza":
        targets = gaza_match_targets(root)
        for row in rows:
            record, outcome = _normalize_gaza_record(row, payload=payload, raw_sha256=raw_sha256, targets=targets)
            if normalization_metadata:
                record.update(normalization_metadata)
            if correction is not None:
                record["normalization_sidecar"] = {
                    "raw_sha256": correction.get("raw_sha256"),
                    "raw_file": correction.get("raw_file"),
                    "normalization_type": correction.get("normalization_type"),
                    "reviewer": correction.get("reviewer"),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approved": correction.get("approved"),
                    "approval_scope": correction.get("approval_scope"),
                    "publication_approval": correction.get("publication_approval"),
                }
            outcomes[outcome] += 1
            normalized.append(record)
    elif domain == "ice":
        targets = ice_match_targets(root)
        explicit_raw_detection = (
            extract_detection_date(str(payload.get("raw_text") or ""))
            if isinstance(payload, dict) and isinstance(payload.get("raw_text"), str)
            else None
        )
        for row in rows:
            row = dict(row)
            if row.get("detection_date") in (None, "") and explicit_raw_detection:
                row["detection_date"] = explicit_raw_detection
            record, outcome = normalize_ice_record(row, payload=payload, raw_sha256=raw_sha256, targets=targets)
            record["captured_at"] = captured_at
            record.setdefault("imported_at", None)
            record.setdefault("last_normalized_at", None)
            if normalization_metadata:
                record.update(normalization_metadata)
            if correction is not None:
                record["normalization_sidecar"] = {
                    "raw_sha256": correction.get("raw_sha256"),
                    "raw_file": correction.get("raw_file"),
                    "normalization_type": correction.get("normalization_type"),
                    "reviewer": correction.get("reviewer"),
                    "reviewed_at": correction.get("reviewed_at"),
                    "approved": correction.get("approved"),
                    "approval_scope": correction.get("approval_scope"),
                    "publication_approval": correction.get("publication_approval"),
                }
            outcomes[outcome] += 1
            normalized.append(record)
    else:
        care_targets = care_line_match_targets(root) if domain == "care-line" else None
        for row in rows:
            source = str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "")
            event_id = str(row.get("event_id") or row.get("id") or "")
            outcome = "matched_existing" if source and source.lower().split("?")[0].rstrip("/") in existing else "new_historical_candidate"
            if domain == "care-line" and event_id in published_care: outcome = "matched_existing"
            if not source and not event_id: outcome = "needs_manual_review"
            record = dict(row); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": outcome})
            if domain == "care-line":
                for field in ("source_snapshot_refs", "evidence_review_refs", "reviewed_record_refs", "universal_event_ids"):
                    record.setdefault(field, [])
                assert care_targets is not None
                normalized_source = source.lower().split("?")[0].rstrip("/")
                source_matches = care_targets["sources"].get(normalized_source, [])
                matched_event_id = event_id if event_id in care_targets["published_events"] or event_id in care_targets["reviewed_events"] else ""
                match_basis = ""
                if event_id in care_targets["published_events"]:
                    historical_outcome, queue_action, match_basis = "matched_published_event", "provenance_only", "event_id"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = event_id
                elif event_id in care_targets["reviewed_events"] or event_id in care_targets["queue"]:
                    historical_outcome, queue_action, match_basis = "matched_reviewed_event", "none", "event_id"
                    record.update({"review_status": "pending_review" if event_id in care_targets["queue"] and event_id not in care_targets["reviewed_events"] else "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = event_id
                elif source_matches:
                    matched_source_event = next((str(item.get("event_id")) for item in source_matches if item.get("event_id")), "")
                    if _care_identity(row) in care_targets["historical_identities"]:
                        historical_outcome, queue_action = "duplicate_historical", "none"
                    elif matched_source_event in care_targets["published_events"]:
                        historical_outcome, queue_action = "matched_published_event", "provenance_only"
                    else:
                        historical_outcome, queue_action = "matched_existing_source", "provenance_only"
                    match_basis = "canonical_source_url"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                    matched_event_id = matched_source_event
                elif _care_identity(row) in care_targets["historical_identities"]:
                    historical_outcome, queue_action, match_basis = "duplicate_historical", "none", "historical_identity"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False})
                elif str(row.get("access_direction") or "").lower() == "access_expansion" or str(row.get("event_type") or "").lower() in {"planned_access_expansion", "service_expansion"}:
                    historical_outcome, queue_action, match_basis = "archived_context", "none", "access_expansion_not_loss_event"
                    record.update({"review_status": "historical_context", "candidate_created": False, "publication_eligible": False, "exclusion_reason": "access expansion retained as historical context; not a loss-event candidate"})
                elif not source and not event_id:
                    historical_outcome, queue_action, match_basis = "needs_manual_review", "none", "missing_identity"
                    record.update({"review_status": "pending_review", "candidate_created": False, "publication_eligible": False})
                elif not str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("evidence_text") or "").strip():
                    historical_outcome, queue_action, match_basis = "archived_invalid", "none", "missing_exact_evidence"
                    record.update({"review_status": "excluded", "candidate_created": False, "publication_eligible": False, "archive_status": "archived", "normalization_status": "completed_with_invalid_findings", "exclusion_reason": "missing exact supporting evidence"})
                else:
                    historical_outcome, queue_action, match_basis = "new_historical_candidate", "review_pending", "unmatched_valid_finding"
                    record.update({"review_status": "pending_review", "candidate_created": True, "publication_eligible": False})
                record.update({
                    "historical_outcome": historical_outcome,
                    "matched_event_id": matched_event_id,
                    "match_basis": match_basis,
                    "queue_action": queue_action,
                    "provenance_links": [{"path": item["path"], "source_record_id": item.get("source_record_id", ""), "event_id": item.get("event_id", "")} for item in source_matches],
                    "agent_name": str(payload.get("agent_name") if isinstance(payload, dict) else "historical-agent"),
                    "agent_run_id": str(payload.get("agent_run_id") if isinstance(payload, dict) else ""),
                })
                if correction is not None:
                    record["normalization_sidecar"] = {
                        "raw_sha256": correction.get("raw_sha256"),
                        "normalization_type": correction.get("normalization_type"),
                        "reviewer": correction.get("reviewer"),
                        "reviewed_at": correction.get("reviewed_at"),
                        "approved": correction.get("approved"),
                        "approval_scope": correction.get("approval_scope"),
                        "publication_approval": correction.get("publication_approval"),
                    }
                outcome = historical_outcome
            outcomes[outcome] += 1; normalized.append(record)
    return normalized, dict(outcomes)


def build_inventory(root: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {"schema_version": "agent_history_index_v1", "generated_at": datetime.now(timezone.utc).isoformat(), "domains": {}}
    for domain in DOMAINS:
        base = archive_root(root, domain); raw_files = list((base / "raw").glob("*.json")) if (base / "raw").exists() else []; normalized_files = list((base / "normalized").rglob("*.json")) if (base / "normalized").exists() else []
        records = []
        for path in normalized_files:
            try: records.extend(json.loads(path.read_text(encoding="utf-8")).get("findings", []))
            except (OSError, ValueError, AttributeError): pass
        dates = [d for record in records for d in _date_values(record.get("source_published_at") or record.get("published_at") or record.get("event_date") or record.get("discovered_at"))]
        urls = {str(record.get("canonical_source_url") or record.get("source_url") or record.get("url")) for record in records if record.get("canonical_source_url") or record.get("source_url") or record.get("url")}
        outcomes = Counter(str(record.get("deduplication_outcome") or "needs_manual_review") for record in records)
        historical_outcomes = Counter(str(record.get("historical_outcome") or record.get("deduplication_outcome") or "needs_manual_review") for record in records)
        domain_inventory = {"raw_run_count": len(raw_files), "normalized_finding_count": len(records), "date_range": [min(dates), max(dates)] if dates else [], "unique_urls": len(urls), "duplicates": historical_outcomes.get("duplicate_historical", 0), "matched_existing_records": outcomes.get("matched_existing", 0), "unmatched_records": historical_outcomes.get("new_historical_candidate", 0), "invalid_records": outcomes.get("invalid", 0) + historical_outcomes.get("archived_invalid", 0), "historical_candidate_count": sum(1 for r in records if r.get("candidate_created") is True), "invalid_archived_count": historical_outcomes.get("archived_invalid", 0), "archived_context_count": historical_outcomes.get("archived_context", 0), "matched_published_event_count": historical_outcomes.get("matched_published_event", 0), "matched_published_edition_count": historical_outcomes.get("matched_published_edition", 0), "matched_reviewed_event_count": historical_outcomes.get("matched_reviewed_event", 0), "matched_existing_source_count": historical_outcomes.get("matched_existing_source", 0), "matched_existing_cluster_count": historical_outcomes.get("matched_existing_cluster", 0), "duplicate_historical_count": historical_outcomes.get("duplicate_historical", 0), "new_historical_candidate_count": historical_outcomes.get("new_historical_candidate", 0), "needs_manual_review_count": historical_outcomes.get("needs_manual_review", 0), "excluded_count": sum(1 for r in records if r.get("review_status") == "excluded"), "candidate_creation_count": sum(1 for r in records if r.get("candidate_created") is True), "publication_ready_count": sum(1 for r in records if r.get("publication_eligible") is True), "missing_dates": sum(1 for r in records if not _date_values(r.get("source_published_at") or r.get("published_at") or r.get("event_date"))), "missing_evidence": sum(1 for r in records if not str(r.get("exact_supporting_passage") or r.get("evidence") or r.get("evidence_text") or r.get("summary") or "").strip()), "pending_review_count": sum(1 for r in records if r.get("review_status") == "pending_review")}
        domain_inventory.update({
            "pending_substantive_review": sum(
                1
                for record in records
                if (
                    record.get("historical_outcome")
                    or record.get("deduplication_outcome")
                )
                == "new_historical_candidate"
                and record.get("review_status") == "pending_review"
            ),
            "queue_entries": sum(
                1
                for record in records
                if record.get("queue_action")
                not in {
                    None,
                    "",
                    "none",
                    "provenance_only",
                }
            ),
            "substantively_reviewed": sum(
                1
                for record in records
                if record.get("review_status") == "substantively_reviewed"
            ),
        })
        if domain == "ice":
            domain_inventory.update(ice_aggregate_metrics(records, raw_runs=len(raw_files)))
        inventory["domains"][domain] = domain_inventory
    return inventory
