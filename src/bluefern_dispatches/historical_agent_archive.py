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
        return {"valid": False, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "text", "finding_count": 0, "invalid_records": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False, "error": str(exc)}
    result: dict[str, Any] = {"valid": True, "domain": domain, "input_sha256": sha256_bytes(raw), "source_format": "json" if parse_metadata["normalization_method"] == "structured_json" else "text", "finding_count": 0, "invalid_records": [], "missing_dates": [], "missing_evidence": [], "duplicates": [], "malformed_base64": False}
    result["normalization_method"] = parse_metadata["normalization_method"]
    if parse_metadata["normalization_method"] == "text_envelope":
        result["finding_count"] = 1
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


def normalize_records(root: Path, domain: str, payload: Any, *, raw_sha256: str, captured_at: str, correction: dict[str, Any] | None = None, normalization_metadata: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = _rows(payload)
    if domain == "care-line" and correction is not None:
        if correction.get("raw_sha256") != raw_sha256:
            raise ValueError("Care Line normalization sidecar raw_sha256 does not match the preserved alert")
        if correction.get("domain") != "care-line":
            raise ValueError("Care Line normalization sidecar domain mismatch")
        if correction.get("normalization_type") != "prose_envelope_to_structured_findings":
            raise ValueError("unsupported Care Line normalization sidecar type")
        if correction.get("approved") is not True or correction.get("approval_scope") != "historical_normalization_only":
            raise ValueError("Care Line sidecar approval is not limited to historical normalization")
        if correction.get("publication_approval") is not False:
            raise ValueError("Care Line normalization sidecar cannot grant publication approval")
        rows = [dict(row) for row in correction.get("findings", []) if isinstance(row, dict)]
        if not rows or len(rows) != len(correction.get("findings", [])):
            raise ValueError("Care Line normalization sidecar findings must be a non-empty list of objects")
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
    else:
        care_targets = care_line_match_targets(root) if domain == "care-line" else None
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
                    historical_outcome, queue_action, match_basis = "new_historical_candidate", "historical_review_candidate", "unmatched_valid_finding"
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
        historical_outcomes = Counter(str(record.get("historical_outcome") or record.get("deduplication_outcome") or "needs_manual_review") for record in records)
        inventory["domains"][domain] = {"raw_run_count": len(raw_files), "normalized_finding_count": len(records), "date_range": [min(dates), max(dates)] if dates else [], "unique_urls": len(urls), "duplicates": historical_outcomes.get("duplicate_historical", 0), "matched_existing_records": outcomes.get("matched_existing", 0), "unmatched_records": historical_outcomes.get("new_historical_candidate", 0), "invalid_records": outcomes.get("invalid", 0) + historical_outcomes.get("archived_invalid", 0), "historical_candidate_count": sum(1 for r in records if r.get("candidate_created") is True), "invalid_archived_count": historical_outcomes.get("archived_invalid", 0), "archived_context_count": historical_outcomes.get("archived_context", 0), "matched_published_event_count": historical_outcomes.get("matched_published_event", 0), "matched_reviewed_event_count": historical_outcomes.get("matched_reviewed_event", 0), "matched_existing_source_count": historical_outcomes.get("matched_existing_source", 0), "duplicate_historical_count": historical_outcomes.get("duplicate_historical", 0), "new_historical_candidate_count": historical_outcomes.get("new_historical_candidate", 0), "needs_manual_review_count": historical_outcomes.get("needs_manual_review", 0), "excluded_count": sum(1 for r in records if r.get("review_status") == "excluded"), "candidate_creation_count": sum(1 for r in records if r.get("candidate_created") is True), "publication_ready_count": sum(1 for r in records if r.get("publication_eligible") is True), "missing_dates": sum(1 for r in records if not _date_values(r.get("source_published_at") or r.get("published_at") or r.get("event_date"))), "missing_evidence": sum(1 for r in records if not str(r.get("exact_supporting_passage") or r.get("evidence") or r.get("evidence_text") or r.get("summary") or "").strip()), "pending_review_count": sum(1 for r in records if r.get("review_status") == "pending_review")}
    return inventory
