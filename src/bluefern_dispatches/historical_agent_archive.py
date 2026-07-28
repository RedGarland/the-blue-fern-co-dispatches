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

DOMAINS = ("food-line", "care-line", "gaza", "ice")
SCHEMA_VERSION = "historical_agent_raw_v1"


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
    try: payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError): payload = None
    return raw, payload


def validate_input(path: Path, *, domain: str) -> dict[str, Any]:
    raw, payload = _load_source(path)
    result: dict[str, Any] = {"valid": True, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "json" if payload is not None else "text", "finding_count": 0, "invalid_records": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False}
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
        identity = json.dumps({"url": str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "").lower().split("?")[0].rstrip("/"), "title": str(row.get("title") or row.get("headline") or "").lower().strip(), "date": str(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or "")[:10]}, sort_keys=True)
        if identity in seen: result["duplicates"].append(index)
        seen.add(identity)
        if not _date_values(row.get("source_published_at") or row.get("published_at") or row.get("event_date") or row.get("discovered_at")): result["missing_dates"].append(index)
        if not str(row.get("exact_supporting_passage") or row.get("evidence") or row.get("summary") or row.get("summary_or_snippet") or "").strip(): result["missing_evidence"].append(index)
    result["valid"] = not (result["invalid_records"] or result["missing_dates"] or result["missing_evidence"] or result["malformed_base64"])
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


def normalize_records(root: Path, domain: str, payload: Any, *, raw_sha256: str, captured_at: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = _rows(payload)
    existing = _existing_text(root, domain)
    published_care = _care_published_ids(root) if domain == "care-line" else set()
    normalized: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    if domain == "food-line":
        if isinstance(payload, dict) and "raw_text" in payload and "findings" not in payload:
            record = dict(payload); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": "needs_manual_review"})
            return [record], {"needs_manual_review": 1}
        findings = adapt_food_line_agent_output(payload, agent_name=str(payload.get("agent_name") if isinstance(payload, dict) else "historical-agent"), agent_run_id=str(payload.get("agent_run_id") if isinstance(payload, dict) else ""), discovered_at=captured_at)
        for finding in findings:
            candidate = map_finding_to_food_line_candidate(finding, edition_date=(finding.source_published_at[:10] if finding.source_published_at[:10] else captured_at[:10]))
            candidate.update({"historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256})
            key = finding.duplicate_key
            outcome = "duplicate_historical" if key and key in existing else ("invalid" if candidate.get("exclusion_reason") else "new_historical_candidate")
            candidate["deduplication_outcome"] = outcome; outcomes[outcome] += 1; normalized.append(candidate)
    else:
        for row in rows:
            source = str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "")
            event_id = str(row.get("event_id") or row.get("id") or "")
            outcome = "matched_existing" if source and source.lower().split("?")[0].rstrip("/") in existing else "new_historical_candidate"
            if domain == "care-line" and event_id in published_care: outcome = "matched_existing"
            if not source and not event_id: outcome = "needs_manual_review"
            record = dict(row); record.update({"domain": domain, "historical_backfill": True, "review_status": "pending_review", "raw_sha256": raw_sha256, "deduplication_outcome": outcome})
            if domain == "ice":
                for field in ("event_category", "event_date", "location", "agency_facility", "injuries", "fatalities", "detention", "removal", "legal", "policy", "evidence", "sources", "severity"):
                    record.setdefault(field, None)
                record.setdefault("verification_status", "pending_review")
            if domain == "care-line":
                for field in ("source_snapshot_refs", "evidence_review_refs", "reviewed_record_refs", "universal_event_ids"):
                    record.setdefault(field, [])
            if domain == "gaza": record.setdefault("provenance_links", [])
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
        inventory["domains"][domain] = {"raw_run_count": len(raw_files), "normalized_finding_count": len(records), "date_range": [min(dates), max(dates)] if dates else [], "unique_urls": len(urls), "duplicates": outcomes.get("duplicate_historical", 0), "matched_existing_records": outcomes.get("matched_existing", 0), "unmatched_records": outcomes.get("new_historical_candidate", 0), "invalid_records": outcomes.get("invalid", 0), "missing_dates": sum(1 for r in records if not _date_values(r.get("source_published_at") or r.get("published_at") or r.get("event_date"))), "missing_evidence": sum(1 for r in records if not str(r.get("exact_supporting_passage") or r.get("evidence") or r.get("summary") or "").strip()), "pending_review_count": sum(1 for r in records if r.get("review_status") == "pending_review")}
    return inventory
