"""Private Food Line source-watch export to the existing agent-run contract."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.food_line_agent import adapt_food_line_agent_output
from .agent_findings import normalize_source_url


SCHEMA_VERSION = "food_line_agent_run_v1"
AGENT_NAME = "Food Line Source Watch"
ALLOWED_EVIDENCE_BASES = {"page_text_excerpt", "rss_item_text", "manual_source_text", "operator_reviewed_exact_passage"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc_timestamp(value: str | None = None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:24] or "run"


def _canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _candidate_url(row: dict[str, Any]) -> str:
    return _text(row.get("source_url") or row.get("final_trace_url") or row.get("canonical_url"))


def _exclusion_reason(row: dict[str, Any]) -> str:
    source_url = _candidate_url(row)
    canonical = normalize_source_url(_text(row.get("canonical_url") or source_url))
    evidence = _text(row.get("evidence_text"))
    basis = _text(row.get("evidence_text_basis"))
    published = _text(row.get("source_published_date") or row.get("source_published_at"))
    location = _text(row.get("metro") or row.get("location_name") or row.get("state_or_territory") or row.get("state_abbrev"))
    classification = _text(row.get("classification_status"))
    if _text(row.get("duplicate_of")) or classification == "duplicate":
        return "duplicate"
    if not source_url.startswith("https://") or not canonical:
        return "invalid_or_missing_https_url"
    if not published:
        return "missing_source_publication_timestamp"
    try:
        datetime.fromisoformat(published[:10])
    except ValueError:
        return "invalid_source_publication_timestamp"
    if not evidence or basis not in ALLOWED_EVIDENCE_BASES:
        return "missing_exact_supporting_passage"
    if not location:
        return "missing_supported_location"
    if classification not in {"qualified_pressure_signal", "manual_fallback"} or not bool(row.get("pressure_signal")):
        return _text(row.get("exclusion_reason")) or "not_a_documented_food_access_pressure_signal"
    if not _text(row.get("pressure_type")):
        return "missing_pressure_type"
    if not bool(row.get("public_claim_eligible")):
        blockers = [_text(value) for value in row.get("public_claim_blockers") or [] if _text(value)]
        return blockers[0] if blockers else "not_currently_reviewable"
    return ""


def _finding(row: dict[str, Any], *, agent_run_id: str) -> dict[str, Any]:
    source_url = _candidate_url(row)
    canonical = normalize_source_url(_text(row.get("canonical_url") or source_url))
    location = _text(row.get("metro") or row.get("location_name") or row.get("state_or_territory") or "United States")
    state = _text(row.get("state_abbrev") or row.get("state") or "US").upper()
    context = {
        "discovery_candidate_id": _text(row.get("candidate_id")),
        "discovery_query": _text(row.get("discovery_query") or row.get("query_text")),
        "query_family": _text(row.get("query_family")),
        "discovery_channel": _text(row.get("discovery_channel")),
        "discovery_date": _text(row.get("discovery_date") or row.get("target_date")),
    }
    return {
        "agent_run_id": agent_run_id,
        "source_url": source_url,
        "canonical_source_url": canonical,
        "publisher": _text(row.get("discovered_publisher") or row.get("publisher") or row.get("direct_source_name")),
        "source_published_at": _text(row.get("source_published_date") or row.get("source_published_at")),
        "title": _text(row.get("selected_title") or row.get("discovered_title") or row.get("title")),
        "exact_supporting_passage": _text(row.get("evidence_text")),
        "summary": _text(row.get("summary_or_snippet") or row.get("pressure_summary")),
        "location_name": location,
        "state": state,
        "location_scope": _text(row.get("location_scope") or row.get("geographic_scope") or ("national" if state == "US" else "state_local")),
        "affected_groups": list(row.get("affected_groups") or []),
        "pressure_type": _text(row.get("pressure_type")),
        "confidence": _text(row.get("confidence") or ("high" if row.get("public_claim_eligible") else "medium")),
        "source_role": _text(row.get("source_role")),
        "evidence_level": _text(row.get("evidence_level")),
        "agent_query_context": context,
        "review_status": "pending_review",
        "exclusion_reason": "",
        "raw_agent_payload": {
            "discovery_candidate_id": context["discovery_candidate_id"],
            "classification_status": _text(row.get("classification_status")),
            "traceability_status": _text(row.get("traceability_status")),
            "evidence_text_basis": _text(row.get("evidence_text_basis")),
            "source_watch_run_id": agent_run_id,
        },
    }


def build_food_line_agent_envelope(
    candidates: list[dict[str, Any]], *, edition_date: str, started_at: str | None = None,
    completed_at: str | None = None, agent_run_id: str | None = None,
    coverage_notes: str = "", agent_name: str = AGENT_NAME,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    started = _utc_timestamp(started_at)
    completed = _utc_timestamp(completed_at or started)
    link_material = "|".join(sorted(_text(row.get("candidate_id") or _candidate_url(row)) for row in candidates))
    content_key = hashlib.sha256(f"{edition_date}|{link_material}".encode("utf-8")).hexdigest()[:12]
    run_id = agent_run_id or f"food-line-source-watch-{started.replace('-', '').replace(':', '')[:15]}Z-{content_key}"
    exclusions: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    for row in candidates:
        reason = _exclusion_reason(row)
        if reason:
            exclusions.append({"candidate_id": _text(row.get("candidate_id")), "reason": reason})
            continue
        findings.append(_finding(row, agent_run_id=run_id))
    counts = Counter(item["reason"] for item in exclusions)
    note = coverage_notes.strip() or f"Source watch for {edition_date}: {len(findings)} exportable findings; {len(exclusions)} exclusions."
    if counts:
        note += " Exclusions: " + ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items())) + "."
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "agent_name": agent_name,
        "agent_run_id": run_id,
        "started_at": started,
        "completed_at": completed,
        "search_window": {"date_from": edition_date, "date_to": edition_date, "edition_date": edition_date},
        "findings": findings,
        "coverage_notes": note,
    }
    if findings:
        adapted = adapt_food_line_agent_output(envelope, agent_name=agent_name, agent_run_id=run_id, discovered_at=completed)
        if len(adapted) != len(findings):
            raise ValueError("agent envelope validation changed the finding count")
    return envelope, exclusions


def _write_collision_safe(destination: Path, filename: str, body: bytes) -> tuple[Path, str]:
    destination.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    target = destination / filename
    if target.exists():
        if target.read_bytes() == body:
            return target, "idempotent_existing"
        target = target.with_name(f"{target.stem}-{digest[:12]}{target.suffix}")
        if target.exists():
            if target.read_bytes() == body:
                return target, "idempotent_existing"
            raise ValueError(f"refusing filename collision with different bytes: {target}")
    descriptor, temp_name = tempfile.mkstemp(prefix=".food-line-agent.", suffix=".tmp", dir=destination)
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, "written"


def export_food_line_agent_run(
    candidates: list[dict[str, Any]], *, edition_date: str, destination: Path,
    started_at: str | None = None, completed_at: str | None = None,
    agent_run_id: str | None = None, run_slug: str | None = None,
    coverage_notes: str = "",
) -> dict[str, Any]:
    normalized_parts = {part.lower() for part in destination.resolve().parts}
    if {"agent-history", "agent-history-staging"} & normalized_parts:
        raise ValueError("historical paths cannot be used as the current Food Line agent inbox")
    envelope, exclusions = build_food_line_agent_envelope(
        candidates, edition_date=edition_date, started_at=started_at, completed_at=completed_at,
        agent_run_id=agent_run_id, coverage_notes=coverage_notes,
    )
    if not envelope["findings"]:
        body = _canonical_bytes(envelope)
        return {
            "status": "no_exportable_findings", "mutation": "none", "path": None,
            "sha256": hashlib.sha256(body).hexdigest(), "agent_run_id": envelope["agent_run_id"],
            "finding_count": 0, "excluded_count": len(exclusions), "exclusions": exclusions,
        }
    stamp = envelope["started_at"].replace("-", "").replace(":", "")[:15] + "Z"
    filename = f"food-line-source-watch-{stamp}-{_slug(run_slug or envelope['agent_run_id'])}.json"
    body = _canonical_bytes(envelope)
    path, mutation = _write_collision_safe(destination, filename, body)
    if exclusions:
        status = "success_with_exclusions" if mutation == "written" else mutation
    else:
        status = "success" if mutation == "written" else mutation
    return {
        "status": status, "mutation": mutation, "path": str(path),
        "sha256": hashlib.sha256(body).hexdigest(), "agent_run_id": envelope["agent_run_id"],
        "finding_count": len(envelope["findings"]), "excluded_count": len(exclusions),
        "exclusions": exclusions,
    }
