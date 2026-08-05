"""Batch the private Food Line current-signal intake boundary.

This module deliberately stops at private editorial review.  It never renders
public output, approves publication, or calls any external publisher.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from .food_line_current_review import (
    ALLOWED_DECISIONS,
    CURRENT_PRODUCTION_SCOPE,
    HISTORICAL_ROOTS,
    PRIVATE_AGENT_INBOX_ROOT,
    PRIVATE_PROPOSED_EDITION_ROOT,
    PRIVATE_QUEUE_PATH,
    QUEUE_SCHEMA_VERSION,
    build_proposed_edition,
    load_queue,
    payload_sha256,
    validate_queue,
    write_json_atomic,
    write_proposed_edition,
)


REPORT_SCHEMA = "food_line_current_intake_report_v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(url: str) -> str:
    return url.split("?", 1)[0].lower().rstrip("/")


def _content_fingerprint(item: dict[str, Any]) -> str:
    material = {
        key: item.get(key)
        for key in (
            "canonical_source_url", "source_published_at", "title",
            "exact_supporting_passage", "proposed_public_headline",
            "proposed_public_summary", "why_it_matters", "uncertainty_note",
        )
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _review_item(candidate: dict[str, Any], *, finding_id: str, artifact_path: str, edition_date: str) -> dict[str, Any]:
    published = str(candidate.get("published_at") or "")
    source_date = date.fromisoformat(published[:10])
    edition = date.fromisoformat(edition_date)
    age_days = (edition - source_date).days
    pressure = str(candidate.get("pressure_summary") or candidate.get("summary_or_snippet") or "").strip()
    title = str(candidate.get("title") or "").strip()
    location = str(candidate.get("location_name") or "United States").strip()
    state = str(candidate.get("state") or "US").strip()
    item = {
        "review_item_id": "food-line-current-" + hashlib.sha256(str(candidate["agent_duplicate_key"]).encode()).hexdigest()[:24],
        "source_finding_or_intake_id": finding_id,
        "source_artifact_path": artifact_path.replace("\\", "/"),
        "source_url": candidate["source_url"],
        "canonical_source_url": candidate["canonical_url"],
        "publisher": candidate["publisher"],
        "source_published_at": published,
        "title": title,
        "exact_supporting_passage": candidate["evidence_text"],
        "proposed_public_headline": title,
        "proposed_public_summary": pressure or title,
        "location_name": location,
        "state": state,
        "location_scope": candidate.get("location_scope") or "national",
        "pressure_type": candidate.get("pressure_type") or "current food-access pressure",
        "affected_groups": list(candidate.get("affected_groups") or []),
        "why_it_matters": pressure or "This report documents a current food-access pressure signal.",
        "evidence_level": candidate.get("evidence_level") or "direct_reporting",
        "confidence": candidate.get("confidence") or "medium",
        "uncertainty_note": "The available source record may not establish the full duration or scale of the pressure.",
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "freshness_check": {"status": "current", "age_days": age_days, "edition_date": edition_date},
        "proposed_section": "Core Food Pressure Signals" if candidate.get("pressure_type") else "Other Food Line Signals",
        "proposed_rank": 1,
        "editorial_status": "pending_editorial_review",
        "editorial_note": "Pending operator decision.",
        "publication_eligible": False,
    }
    item["source_content_sha256"] = _content_fingerprint(item)
    return item


def _discover(inbox: Path) -> list[Path]:
    return sorted(path for path in inbox.rglob("*.json") if "processed" not in path.parts and path.is_file())


def _queue_skeleton(edition_date: str) -> dict[str, Any]:
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": f"food-line-current-review-{edition_date}",
        "edition_date": edition_date,
        "production_scope": CURRENT_PRODUCTION_SCOPE,
        "historical_roots_excluded": list(HISTORICAL_ROOTS),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "items": [],
    }


def process_batch(root: Path, *, edition_date: str, inbox: Path | None = None, build_review_queue: bool = True,
                  build_proposed: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Validate, dry-run, and optionally import every JSON envelope in *inbox*."""
    root = root.resolve()
    inbox = (root / PRIVATE_AGENT_INBOX_ROOT) if inbox is None else inbox.resolve()
    files = _discover(inbox) if inbox.exists() else []
    checks: list[tuple[Path, dict[str, Any], dict[str, Any] | None]] = []
    run_ids: dict[str, Path] = {}
    urls: dict[str, Path] = {}
    errors: list[dict[str, Any]] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            validation = _validate_envelope(payload, path)
            run_id = str(payload.get("agent_run_id") or "")
            if run_id in run_ids:
                raise ValueError(f"duplicate agent_run_id: {run_id}")
            run_ids[run_id] = path
            for finding in payload["findings"]:
                url = _identity(str(finding.get("canonical_source_url") or finding.get("source_url") or finding.get("url") or ""))
                if url and url in urls:
                    raise ValueError(f"duplicate source URL also present in {urls[url].name}: {url}")
                if url:
                    urls[url] = path
            checks.append((path, validation, payload))
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            errors.append({"file": str(path), "status": "rejected", "error": str(exc)})

    dry_runs = []
    for path, _, payload in checks:
        try:
            from scripts.import_food_line_agent_findings import process as import_process
            result = import_process(root, path, edition_date=edition_date, agent_name=str(payload.get("agent_name") or ""), agent_run_id=str(payload.get("agent_run_id") or ""), dry_run=True)
            dry_runs.append({"file": str(path), "status": "dry_run_ok", "result": result})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"file": str(path), "status": "rejected", "error": f"dry_run: {exc}"})

    imports = []
    if not dry_run:
        for path, _, payload in checks:
            if any(row["file"] == str(path) and row["status"] == "rejected" for row in errors):
                continue
            from scripts.import_food_line_agent_findings import process as import_process
            try:
                imports.append(import_process(root, path, edition_date=edition_date, agent_name=str(payload.get("agent_name") or ""), agent_run_id=str(payload.get("agent_run_id") or ""), dry_run=False))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append({"file": str(path), "status": "failed", "error": str(exc)})

    queue_result: dict[str, Any] = {"status": "not_requested"}
    proposal_result: dict[str, Any] = {"status": "not_requested"}
    if not dry_run and build_review_queue:
        queue_result = _refresh_queue(root, edition_date)
        if build_proposed:
            queue_path, md_path, proposal = write_proposed_edition(root, queue_result["queue"])
            proposal_result = {"status": "written", "json_path": str(queue_path), "markdown_path": str(md_path), "draft_status": proposal["draft_status"], "selected_item_count": proposal["selected_item_count"]}
    elif build_proposed:
        queue_result["status"] = "skipped_until_import"

    rejected = any(row.get("status") == "rejected" for row in errors)
    failed = any(row.get("status") == "failed" for row in errors)
    status = "success" if not errors else "success_with_exclusions" if (rejected and (imports or dry_runs)) else "partial_failure" if failed and (imports or dry_runs) else "failed"
    idempotent_noop_count = sum(1 for result in imports if result.get("status") == "idempotent_noop")
    report = {
        "schema_version": REPORT_SCHEMA, "status": status, "edition_date": edition_date,
        "inbox": str(inbox), "discovered_file_count": len(files),
        "accepted_file_count": len(checks), "dry_run_count": len(dry_runs),
        "import_count": len(imports) - idempotent_noop_count,
        "import_attempt_count": len(imports), "idempotent_noop_count": idempotent_noop_count,
        "errors": errors,
        "queue": {key: value for key, value in queue_result.items() if key != "queue"},
        "proposal": proposal_result,
        "publication_side_effects": {"public_output": False, "pages": False, "bluesky": False, "audio": False, "maps": False, "schedule": False},
    }
    if not dry_run:
        report_path = root / "data/dispatches/food-line/review/reports" / edition_date / "current-intake.json"
        write_json_atomic(report_path, report)
        report["report_path"] = str(report_path)
    return report


def _validate_envelope(payload: Any, path: Path) -> dict[str, Any]:
    from scripts.import_food_line_agent_findings import validate_input
    # The importer is the canonical envelope validator; use a temporary-free
    # equivalent here so the batch can reject before any import mutation.
    required = {"schema_version", "agent_name", "agent_run_id", "started_at", "completed_at", "search_window", "findings", "coverage_notes"}
    if not isinstance(payload, dict) or not required.issubset(payload) or not isinstance(payload["findings"], list):
        raise ValueError("invalid envelope: required fields/findings list missing")
    validation = validate_input(path)
    if not validation["valid"]:
        raise ValueError("invalid envelope findings: " + json.dumps(validation, sort_keys=True))
    return validation


def _refresh_queue(root: Path, edition_date: str) -> dict[str, Any]:
    queue_path = root / PRIVATE_QUEUE_PATH
    try:
        old = load_queue(queue_path)
    except ValueError:
        old = _queue_skeleton(edition_date)
    if old.get("edition_date") != edition_date:
        old = _queue_skeleton(edition_date)
    existing = {str(item["review_item_id"]): item for item in old["items"]}
    intake_root = root / "data/dispatches/food-line/agent-intake" / edition_date
    candidates: list[dict[str, Any]] = []
    for artifact in sorted(intake_root.glob("*.json")) if intake_root.exists() else []:
        if artifact.parent.name == "reports":
            continue
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        for candidate in payload.get("candidate_rows", []):
            if not candidate.get("eligible_for_review"):
                continue
            try:
                finding_id = str(candidate.get("candidate_id") or candidate.get("agent_finding_id") or "")
                item = _review_item(candidate, finding_id=finding_id, artifact_path=artifact.relative_to(root).as_posix(), edition_date=edition_date)
                if item["freshness_check"]["age_days"] < 0 or item["freshness_check"]["age_days"] > 3:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            previous = existing.get(item["review_item_id"])
            if previous:
                previous_fingerprint = str(previous.get("source_content_sha256") or _content_fingerprint(previous))
                if previous_fingerprint == item["source_content_sha256"]:
                    item.update(previous)
                else:
                    item["editorial_status"] = "pending_editorial_review"
                    item["editorial_note"] = "Source evidence or proposed wording changed; operator rereview required."
                    item["rereview_required"] = True
            candidates.append(item)
    unique = {item["review_item_id"]: item for item in candidates}
    ordered = sorted(unique.values(), key=lambda row: (0 if row.get("editorial_status") in {"approve", "approve_with_edit"} else 1, str(row["canonical_source_url"])))
    for rank, item in enumerate(ordered, 1):
        item["proposed_rank"] = rank
    queue = _queue_skeleton(edition_date)
    queue["items"] = ordered
    validate_queue(queue)
    write_json_atomic(queue_path, queue)
    return {"status": "written", "queue_path": str(queue_path), "queue_sha256": payload_sha256(queue), "item_count": len(ordered), "queue": queue}
