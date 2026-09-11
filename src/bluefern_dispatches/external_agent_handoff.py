"""Private, durable import bridge for manually supplied Food/Care envelopes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bluefern_dispatches.care_line_national_pipeline import (
    REVIEW_ROOT,
    build_review_queue,
    normalize_candidate_record,
    update_candidate_registry_at_path,
)
from bluefern_dispatches.source_based_qualification import assess_review_retention

SCHEMA_VERSION = 1
DISPATCHES = {"food-line", "care-line"}
REQUIRED_FIELDS = (
    "schema_version", "agent_name", "agent_run_id", "started_at",
    "completed_at", "search_window", "findings", "coverage_notes",
)
PRIVATE_ROOT = Path("data/private-agent-handoff")
RECEIPT_SCHEMA = "bluefern.external_agent_handoff_receipt.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Mapping[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _date_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def envelope_date(payload: Mapping[str, Any]) -> str:
    window = payload.get("search_window") if isinstance(payload.get("search_window"), Mapping) else {}
    for value in (window.get("edition_date"), window.get("date_from"), payload.get("started_at")):
        parsed = _date_value(value)
        if parsed:
            return parsed
    return ""


def validate_envelope(payload: Any, *, dispatch: str) -> list[str]:
    errors: list[str] = []
    if dispatch not in DISPATCHES:
        errors.append("unsupported dispatch")
    if not isinstance(payload, dict):
        return ["envelope must be a JSON object"]
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    if not str(payload.get("agent_name") or "").strip():
        errors.append("agent_name must be non-empty")
    if not str(payload.get("agent_run_id") or "").strip():
        errors.append("agent_run_id must be non-empty")
    if not isinstance(payload.get("search_window"), dict):
        errors.append("search_window must be an object")
    if not isinstance(payload.get("findings"), list):
        errors.append("findings must be a list")
    elif any(not isinstance(row, dict) for row in payload["findings"]):
        errors.append("every finding must be an object")
    if not envelope_date(payload):
        errors.append("search_window must contain an ISO date")
    return errors


def _food_importer():
    path = Path(__file__).resolve().parents[2] / "scripts" / "import_food_line_agent_findings.py"
    spec = importlib.util.spec_from_file_location("external_food_importer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Food Line importer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _food_dispatch(root: Path, archive: Path, payload: dict[str, Any], edition_date: str) -> dict[str, Any]:
    result = _food_importer().process(
        root, archive, edition_date=edition_date,
        agent_name=str(payload["agent_name"]), agent_run_id=str(payload["agent_run_id"]), dry_run=False,
    )
    artifact = result.get("artifact") or {}
    rows = artifact.get("candidate_rows") if isinstance(artifact, dict) else []
    reconciliation: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        item = dict(row) if isinstance(row, dict) else {}
        item["review_status"] = "pending_review"
        if item.get("review_retention_disposition") == "duplicate":
            item["review_retention_disposition"] = "duplicate_with_reason"
            item["duplicate_linkage"] = {
                "reason": "duplicate_article_within_run",
                "agent_duplicate_key": item.get("agent_duplicate_key", ""),
            }
        reconciliation.append({
            "finding_id": item.get("finding_id", ""),
            "candidate_id": item.get("candidate_id", ""),
            "terminal_disposition": item.get("review_retention_disposition") or "rejected_with_reason",
            "duplicate_linkage": item.get("duplicate_linkage") or {},
            "reason": item.get("exclusion_reason") or item.get("uncertainty_note") or "",
            "unaccounted": False,
        })
    if isinstance(artifact, dict):
        artifact["candidate_rows"] = [
            {**row, "review_status": "pending_review", "review_retention_disposition": ("duplicate_with_reason" if row.get("review_retention_disposition") == "duplicate" else row.get("review_retention_disposition"))}
            for row in rows if isinstance(row, dict)
        ]
        artifact["counts"] = dict(__import__("collections").Counter(str(row.get("review_retention_disposition") or "rejected_with_reason") for row in artifact["candidate_rows"]))
        artifact_path = root / "data/dispatches/food-line/agent-intake" / edition_date / f"{payload['agent_run_id']}.json"
        _write_json(artifact_path, artifact)
    return {"reconciliation": reconciliation, "imported": len(reconciliation), "artifact_ref": f"data/dispatches/food-line/agent-intake/{edition_date}/{payload['agent_run_id']}.json"}


def _care_dispatch(root: Path, archive_ref: str, payload: dict[str, Any], edition_date: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    for index, finding in enumerate(payload["findings"]):
        row = dict(finding)
        source_url = str(row.get("canonical_source_url") or row.get("source_url") or row.get("url") or "")
        evidence = str(row.get("exact_supporting_passage") or row.get("evidence_text") or row.get("supporting_passage") or row.get("description") or "").strip()
        retention = assess_review_retention({**row, "item_url": source_url, "source_publisher": row.get("publisher") or row.get("source_publisher"), "access_consequences": row.get("access_consequences") or ["REDUCED_SERVICE_AVAILABILITY"]}, dispatch="care-line", edition_date=edition_date)
        finding_id = str(row.get("finding_id") or f"{payload['agent_run_id']}-{index + 1}")
        if not retention["eligible_for_review"]:
            reconciliation.append({"finding_id": finding_id, "candidate_id": "", "terminal_disposition": retention["disposition"], "duplicate_linkage": {}, "reason": "; ".join(retention["failure_reasons"]), "unaccounted": False})
            continue
        state = str(row.get("state") or row.get("state_abbrev") or row.get("jurisdiction") or "").upper()
        city = str(row.get("city") or row.get("locality") or row.get("location_name") or "")
        event_type = str(row.get("event_type") or "service_reduction")
        service_line = str(row.get("service_line") or "unknown")
        raw = {
            "raw_item_id": finding_id, "source_id": str(row.get("source_id") or row.get("publisher") or "external-agent"),
            "source_name": str(row.get("publisher") or row.get("source_publisher") or "External agent"),
            "source_publisher": str(row.get("publisher") or row.get("source_publisher") or "External agent"),
            "source_type": str(row.get("source_type") or "external_agent_handoff"), "source_role": str(row.get("source_role") or "external_agent_source"),
            "item_url": source_url, "title": str(row.get("title") or row.get("headline") or "External agent finding"),
            "source_publication_date": str(row.get("source_published_at") or row.get("source_published_date") or row.get("published_at") or edition_date),
            "description": evidence, "record_fingerprint": finding_id,
        }
        currentness = {"currentness_class": "CURRENT_EVENT", "freshness_role": "CURRENT", "operative_event_date": edition_date, "currentness_confidence": 0.8, "currentness_reasoning": "explicit external-agent handoff currentness; human review required", "operative_event_passage": evidence, "title_body_agree": True, "currentness_failed_gates": []}
        candidate = normalize_candidate_record(
            raw, article_content=None, supporting_passage=evidence,
            geography={"state": state, "city": city, "jurisdiction_display": state, "service_region": "", "geographic_scope": "statewide"},
            geography_provenance=None, subject=str(row.get("subject") or row.get("facility_name") or row.get("provider") or row.get("publisher") or ""), provider=str(row.get("provider") or row.get("publisher") or ""), subject_provenance=None,
            event_type=event_type, service_line=service_line, access_consequences=list(row.get("access_consequences") or ["REDUCED_SERVICE_AVAILABILITY"]), access_exception=str(row.get("uncertainty_note") or ""), artifact_path=archive_ref, run_id=str(payload["agent_run_id"]), qualification_status="qualified", failed_gates=[], exclusion_reason="", extraction_confidence=0.8, full_article_required=True, currentness=currentness,
        )
        candidate["metadata"] = {**candidate.get("metadata", {}), "external_agent_handoff": True, "review_status": "pending_review", "finding_id": finding_id, "retention": retention}
        candidates.append(candidate)
        reconciliation.append({"finding_id": finding_id, "candidate_id": candidate["candidate_id"], "terminal_disposition": "retained_for_review", "duplicate_linkage": {}, "reason": "", "unaccounted": False})
    registry_path = root / REVIEW_ROOT / "candidate-registry.json"
    registry = update_candidate_registry_at_path(registry_path, edition_date=edition_date, candidates=candidates)
    queue = build_review_queue(registry["candidates"], edition_date=edition_date)
    queue_path = root / REVIEW_ROOT / "current-review-queue.json"
    _write_json(queue_path, queue)
    return {"reconciliation": reconciliation, "imported": len(candidates), "artifact_ref": "data/dispatches/care-line/review/candidate-registry.json", "queue_ref": "data/dispatches/care-line/review/current-review-queue.json"}


def import_envelope(root: Path, input_path: Path, *, dispatch: str) -> tuple[int, dict[str, Any]]:
    raw = input_path.read_bytes()
    digest = sha256_bytes(raw)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return 2, {"status": "REJECTED", "classification": "MALFORMED", "error": str(exc), "input_sha256": digest}
    errors = validate_envelope(payload, dispatch=dispatch)
    if errors:
        return 2, {"status": "REJECTED", "classification": "MALFORMED", "errors": errors, "input_sha256": digest}
    run_id = str(payload["agent_run_id"])
    edition_date = envelope_date(payload)
    archive = root / PRIVATE_ROOT / "archive" / dispatch / edition_date / f"{run_id}.json"
    archive_ref = archive.relative_to(root).as_posix()
    if archive.exists():
        previous = archive.read_bytes()
        if sha256_bytes(previous) == digest:
            return 0, {"status": "SAFE_NO_OP", "classification": "IDEMPOTENT_RETRY", "input_sha256": digest, "archive_ref": archive_ref, "agent_run_id": run_id}
        return 2, {"status": "CONFLICT", "classification": "IDEMPOTENCY_CONFLICT", "input_sha256": digest, "archive_ref": archive_ref, "agent_run_id": run_id}
    inbox = root / PRIVATE_ROOT / "inbox" / dispatch / f"{run_id}.json"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    try:
        with inbox.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if sha256_bytes(inbox.read_bytes()) != digest:
            return 2, {"status": "CONFLICT", "classification": "IDEMPOTENCY_CONFLICT", "input_sha256": digest, "archive_ref": archive_ref, "agent_run_id": run_id}
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if sha256_bytes(archive.read_bytes()) == digest:
            return 0, {"status": "SAFE_NO_OP", "classification": "IDEMPOTENT_RETRY", "input_sha256": digest, "archive_ref": archive_ref, "agent_run_id": run_id}
        return 2, {"status": "CONFLICT", "classification": "IDEMPOTENCY_CONFLICT", "input_sha256": digest, "archive_ref": archive_ref, "agent_run_id": run_id}
    try:
        dispatch_result = _food_dispatch(root, archive, payload, edition_date) if dispatch == "food-line" else _care_dispatch(root, archive_ref, payload, edition_date)
        reconciliation = dispatch_result["reconciliation"]
        terminal_counts = {key: sum(1 for row in reconciliation if row["terminal_disposition"] == key) for key in ("retained_for_review", "duplicate_with_reason", "rejected_with_reason", "deferred_with_reason", "invalid_source_with_reason")}
        receipt = {
            "schema_version": RECEIPT_SCHEMA, "dispatch": dispatch, "agent_run_id": run_id, "input_filename": "external-agent-envelope.json", "input_sha256": digest, "validation_result": "passed", "import_result": "accepted", "imported_count": dispatch_result["imported"], "rejected_count": terminal_counts["rejected_with_reason"], "duplicate_count": terminal_counts["duplicate_with_reason"], "deferred_count": terminal_counts["deferred_with_reason"], "invalid_count": terminal_counts["invalid_source_with_reason"], "unaccounted": sum(1 for row in reconciliation if row.get("unaccounted")), "archive_ref": archive_ref, "imported_artifact_ref": dispatch_result["artifact_ref"], "started_at": payload["started_at"], "completed_at": payload["completed_at"], "receipt_created_at": _utc_now(), "exit_code": 0, "status": "SUCCESS", "classification": "ACCEPTED", "reconciliation": reconciliation,
        }
        receipt_path = root / PRIVATE_ROOT / "receipts" / dispatch / edition_date / f"{run_id}.json"
        _write_json(receipt_path, receipt)
        return 0, receipt
    except Exception as exc:  # noqa: BLE001
        receipt = {"schema_version": RECEIPT_SCHEMA, "dispatch": dispatch, "agent_run_id": run_id, "input_filename": "external-agent-envelope.json", "input_sha256": digest, "validation_result": "passed", "import_result": "failed", "imported_count": 0, "rejected_count": 0, "duplicate_count": 0, "deferred_count": 0, "invalid_count": 0, "unaccounted": len(payload["findings"]), "archive_ref": archive_ref, "started_at": payload["started_at"], "completed_at": payload["completed_at"], "receipt_created_at": _utc_now(), "exit_code": 1, "status": "FAILURE", "classification": "IMPORT_ERROR", "error_type": type(exc).__name__, "error": str(exc)}
        _write_json(root / PRIVATE_ROOT / "receipts" / dispatch / edition_date / f"{run_id}.json", receipt)
        return 1, receipt
