from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit

from .external_agent_handoff import OPERATOR_RECOVERY_CLASS

SCHEMA_VERSION = "bluefern.food_line.operator_recovery_release_prep.v1"
PRIVATE_RELEASE_PREP_STATE = "OPERATOR_RECOVERY_RELEASE_PREP_ELIGIBLE"
DEFAULT_PREP_ROOT = Path("data/private-agent-handoff/operator-recovery/food-line")
REJECTED_EDITORIAL_DISPOSITIONS = {"HOLD", "REJECT", "hold", "reject"}
APPROVED_EDITORIAL_DISPOSITIONS = {"APPROVE", "APPROVE_WITH_EDIT", "approve", "approve_with_edit"}
NONBLOCKING_DUPLICATE_STATES = {
    "NO_DUPLICATE_FOUND",
    "not_published",
    "nonblocking",
    "resolved_nonblocking",
}
VALID_SOURCE_VERIFICATION_STATES = {
    "current_url_verified",
    "syntactic_url_verified",
    "traceable",
    "verified",
}
FORBIDDEN_PUBLIC_ROOTS = (
    "output/",
    "output/site/",
    "bluefern-dispatches-pages/",
)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def payload_sha256(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json(payload)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _repo_relative_path(value: Path | str) -> str:
    text = str(value).replace("\\", "/").strip()
    if not text:
        raise ValueError("artifact path must not be empty")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("artifact paths must be repository-relative")
    lowered = pure.as_posix().lower()
    if any(lowered == root.rstrip("/") or lowered.startswith(root) for root in FORBIDDEN_PUBLIC_ROOTS):
        raise ValueError("operator recovery release prep cannot read public output or Pages paths")
    return pure.as_posix()


def _load_json(root: Path, relative_path: Path | str) -> tuple[dict[str, Any], Path, str]:
    text = _repo_relative_path(relative_path)
    path = root / text
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"required artifact is missing: {text}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"required artifact is invalid JSON: {text}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"required artifact must be a JSON object: {text}")
    return payload, path, sha256_file(path)


def _require_bool(payload: Mapping[str, Any], field: str, expected: bool) -> None:
    if payload.get(field) is not expected:
        raise ValueError(f"{field} must be {str(expected).lower()}")


def _require_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"{field} must contain an ISO date") from exc
    if not text.startswith(parsed.isoformat()):
        raise ValueError(f"{field} must begin with an ISO date")
    return parsed.isoformat()


def _require_https(value: Any, field: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{field} must be a traceable HTTPS URL")
    return text


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be present")
    return text


def _safe_item_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    if not text:
        raise ValueError("item ID must be present")
    return text[:160]


def _rows(payload: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return [row for row in value if isinstance(row, dict)]


def _matches(row: Mapping[str, Any], item_id: str) -> bool:
    return item_id in {
        str(row.get("item") or ""),
        str(row.get("item_id") or ""),
        str(row.get("candidate_id") or ""),
        str(row.get("agent_run_id") or ""),
        str(row.get("review_item_id") or ""),
    }


def _one_matching(rows: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    matches = [row for row in rows if _matches(row, item_id)]
    if len(matches) != 1:
        raise ValueError(f"{label} must contain exactly one row for {item_id}")
    return matches[0]


def _duplicate_state(row: Mapping[str, Any]) -> str:
    duplicate = row.get("duplicate_status") or row.get("duplicate_disposition")
    if isinstance(duplicate, Mapping):
        duplicate = duplicate.get("status") or duplicate.get("disposition")
    if not duplicate and isinstance(row.get("duplicate_check"), Mapping):
        duplicate = row["duplicate_check"].get("status")
    if not duplicate and isinstance(row.get("assessments"), Mapping):
        duplicate = row["assessments"].get("duplicate_status")
    return str(duplicate or "").strip()


def _duplicate_is_nonblocking(row: Mapping[str, Any]) -> bool:
    state = _duplicate_state(row)
    if state in NONBLOCKING_DUPLICATE_STATES:
        return True
    return state.startswith("NO_DUPLICATE") or state.startswith("nonblocking")


def _approved_text(editorial: Mapping[str, Any], release_decision: Mapping[str, Any]) -> tuple[str, str]:
    headline = (
        editorial.get("corrected_headline")
        or editorial.get("approved_headline")
        or release_decision.get("approved_headline_verified")
    )
    summary = (
        editorial.get("bounded_source_backed_summary")
        or editorial.get("approved_summary")
        or release_decision.get("approved_summary_verified")
    )
    return _nonempty(headline, "approved headline"), _nonempty(summary, "approved summary")


def _source_verification(reconciliation: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    run_id = str(row.get("agent_run_id") or "")
    source_url = str(row.get("canonical_source_url") or row.get("source_url") or "")
    verifications = _rows(reconciliation, "source_verification")
    matches = [
        item
        for item in verifications
        if str(item.get("agent_run_id") or "") == run_id
        or str(item.get("canonical_source_url") or "") == source_url
    ]
    if len(matches) != 1:
        raise ValueError("source verification must resolve exactly once")
    verification = matches[0]
    if verification.get("supporting_evidence_present") is not True:
        raise ValueError("source-backed supporting evidence must be present")
    state = _nonempty(verification.get("source_verification_status"), "source verification status")
    if state not in VALID_SOURCE_VERIFICATION_STATES:
        raise ValueError("source verification status is not valid for release preparation")
    return state


def _validate_reconciliation(payload: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    if payload.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise ValueError("reconciliation provenance_class must be operator_recovered_source_watch_evidence")
    _require_bool(payload, "original_production_artifact_present", False)
    _require_bool(payload, "production_collection_failed_before_discovery", True)
    _require_bool(payload, "publication_performed", False)
    _require_bool(payload, "synthetic_evidence_used", False)
    row = _one_matching(_rows(payload, "rows"), item_id, "reconciliation")
    if row.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise ValueError("reconciliation row must preserve operator recovery provenance")
    if row.get("corrected_disposition") != "retained_for_review":
        raise ValueError("reconciliation disposition must be retained_for_review")
    if row.get("eligible_for_automatic_publication") is not False:
        raise ValueError("eligible_for_automatic_publication must remain false")
    _nonempty(row.get("agent_run_id"), "original agent run ID")
    _require_https(row.get("canonical_source_url") or row.get("source_url"), "source URL")
    _nonempty(row.get("publisher"), "publisher/source identity")
    source_state = _source_verification(payload, row)
    return {**row, "source_verification_status": source_state}


def _assessment_confirms(editorial: Mapping[str, Any], key: str, field: str) -> None:
    value = editorial.get(key)
    if value is True:
        return
    assessments = editorial.get("assessments") if isinstance(editorial.get("assessments"), Mapping) else {}
    text = str(assessments.get(key) or editorial.get(field) or "")
    if not (text.startswith("APPROVED") or text.startswith("SUFFICIENT")):
        raise ValueError(f"{field} must be confirmed")


def _validate_editorial(payload: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    if payload.get("recovery_provenance") != OPERATOR_RECOVERY_CLASS:
        raise ValueError("editorial review must preserve operator recovery provenance")
    boundaries = payload.get("scope_boundaries") if isinstance(payload.get("scope_boundaries"), Mapping) else {}
    if boundaries.get("publication_authority_granted") is not False:
        raise ValueError("editorial review cannot grant publication authority")
    if boundaries.get("original_production_lineage_rewritten_or_upgraded") is not False:
        raise ValueError("editorial review cannot rewrite original production lineage")
    row = _one_matching(_rows(payload, "items"), item_id, "editorial review")
    disposition = _nonempty(row.get("editorial_disposition"), "editorial disposition")
    if disposition in REJECTED_EDITORIAL_DISPOSITIONS:
        raise ValueError(f"{disposition} cannot enter private release preparation")
    if disposition not in APPROVED_EDITORIAL_DISPOSITIONS:
        raise ValueError("editorial disposition must be APPROVE or APPROVE_WITH_EDIT")
    if row.get("eligible_for_automatic_publication") is not False:
        raise ValueError("eligible_for_automatic_publication must remain false")
    _require_date(row.get("event_date_or_source_date"), "event/currentness date")
    if not _duplicate_is_nonblocking(row):
        raise ValueError("duplicate state must be resolved and nonblocking")
    _assessment_confirms(row, "in_scope_geography", "geography")
    _assessment_confirms(row, "materiality", "material Food Line relevance")
    headline, summary = _approved_text(row, {})
    return {**row, "approved_headline": headline, "approved_summary": summary}


def _validate_release_decision(payload: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    if payload.get("recovery_provenance") != OPERATOR_RECOVERY_CLASS:
        raise ValueError("release decision must preserve operator recovery provenance")
    boundaries = payload.get("scope_boundaries") if isinstance(payload.get("scope_boundaries"), Mapping) else {}
    for field in (
        "publication_performed",
        "pages_modified",
        "archive_rss_homepage_modified",
        "schedules_modified",
        "operational_health_state_modified",
        "food_line_operationally_recovered",
        "eligible_for_automatic_publication_changed",
        "publication_eligible_changed",
        "publication_approval_changed",
    ):
        if boundaries.get(field) is not False:
            raise ValueError(f"release decision boundary {field} must be false")
    row = _one_matching(_rows(payload, "items"), item_id, "release decision")
    state = str(row.get("release_preparation_state") or row.get("release_decision_state") or "").strip()
    if state != PRIVATE_RELEASE_PREP_STATE:
        raise ValueError("release decision state must be OPERATOR_RECOVERY_RELEASE_PREP_ELIGIBLE")
    if row.get("publication_eligible") is not False:
        raise ValueError("publication_eligible must remain false")
    if row.get("publication_approval") is not False:
        raise ValueError("publication_approval must remain false")
    if row.get("operator_recovery_lineage_verified") is not True:
        raise ValueError("operator recovery lineage must be verified")
    if row.get("original_failed_runtime_distinction_preserved") is not True:
        raise ValueError("original failed production runtime distinction must be preserved")
    if row.get("source_traceability_verified") is not True:
        raise ValueError("source traceability must be verified")
    if row.get("material_food_line_relevance_verified") is not True:
        raise ValueError("material Food Line relevance must be verified")
    _require_date(row.get("event_currentness_date"), "event/currentness date")
    if not _duplicate_is_nonblocking(row):
        raise ValueError("duplicate state must be resolved and nonblocking")
    _nonempty(row.get("geography_verified"), "geography")
    return row


def build_release_prep_artifact(
    *,
    root: Path,
    item_id: str,
    reconciliation_ref: Path | str,
    editorial_review_ref: Path | str,
    release_decision_ref: Path | str,
) -> dict[str, Any]:
    reconciliation, reconciliation_path, reconciliation_hash = _load_json(root, reconciliation_ref)
    editorial, editorial_path, editorial_hash = _load_json(root, editorial_review_ref)
    release_decision, release_path, release_hash = _load_json(root, release_decision_ref)

    recovery_row = _validate_reconciliation(reconciliation, item_id)
    editorial_row = _validate_editorial(editorial, item_id)
    decision_row = _validate_release_decision(release_decision, item_id)
    headline, summary = _approved_text(editorial_row, decision_row)
    event_date = _require_date(decision_row.get("event_currentness_date") or editorial_row.get("event_date_or_source_date"), "event/currentness date")

    source_url = _require_https(
        recovery_row.get("canonical_source_url") or decision_row.get("source_url") or editorial_row.get("source_url"),
        "source URL",
    )
    publisher = _nonempty(recovery_row.get("publisher") or decision_row.get("source_publisher") or editorial_row.get("source_publisher"), "publisher")
    agent_run_id = _nonempty(recovery_row.get("agent_run_id") or decision_row.get("agent_run_id") or editorial_row.get("agent_run_id"), "original agent run ID")
    return {
        "schema_version": SCHEMA_VERSION,
        "item_id": item_id,
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "release_preparation_state": PRIVATE_RELEASE_PREP_STATE,
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "original_agent_run_id": agent_run_id,
        "recovery_reconciliation_ref": reconciliation_path.relative_to(root).as_posix(),
        "recovery_reconciliation_sha256": reconciliation_hash,
        "editorial_review_ref": editorial_path.relative_to(root).as_posix(),
        "editorial_review_sha256": editorial_hash,
        "release_decision_ref": release_path.relative_to(root).as_posix(),
        "release_decision_sha256": release_hash,
        "source_url": source_url,
        "publisher": publisher,
        "source_verification": recovery_row["source_verification_status"],
        "event_date": event_date,
        "geography": _nonempty(decision_row.get("geography_verified") or editorial_row.get("location_name"), "geography"),
        "duplicate_disposition": _duplicate_state(decision_row) or _duplicate_state(editorial_row),
        "approved_headline": headline,
        "approved_summary": summary,
        "reviewer_metadata": {
            "editorial_disposition": editorial_row.get("editorial_disposition"),
            "editorial_reviewed_at": editorial.get("reviewed_at"),
            "release_decided_at": release_decision.get("decided_at"),
        },
        "release_preparation_eligible": True,
        "eligible_for_automatic_publication": False,
        "publication_eligible": False,
        "publication_approval": False,
        "publication_performed": False,
        "failed_production_lineage_preserved": True,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "archive_rss_homepage_authorized": False,
        "scheduler_authorized": False,
        "operational_health_state_authorized": False,
    }


def default_release_prep_path(root: Path, edition_date: str, item_id: str) -> Path:
    safe_id = _safe_item_id(item_id)
    return root / DEFAULT_PREP_ROOT / edition_date / "release-prep" / f"{safe_id}.json"


def write_release_prep_artifact(
    *,
    root: Path,
    item_id: str,
    edition_date: str,
    reconciliation_ref: Path | str,
    editorial_review_ref: Path | str,
    release_decision_ref: Path | str,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    artifact = build_release_prep_artifact(
        root=root,
        item_id=item_id,
        reconciliation_ref=reconciliation_ref,
        editorial_review_ref=editorial_review_ref,
        release_decision_ref=release_decision_ref,
    )
    target = root / _repo_relative_path(output_path) if output_path else default_release_prep_path(root, edition_date, item_id)
    rendered = canonical_json(artifact).encode("utf-8")
    if target.exists():
        existing = target.read_bytes()
        if existing == rendered:
            return {
                "ok": True,
                "status": "idempotent_noop",
                "artifact_path": target.relative_to(root).as_posix(),
                "artifact_sha256": "sha256:" + hashlib.sha256(existing).hexdigest(),
                "artifact": artifact,
            }
        raise ValueError(f"release-prep artifact already exists with different content: {target.relative_to(root).as_posix()}")
    _write_json_atomic(target, artifact)
    return {
        "ok": True,
        "status": "created",
        "artifact_path": target.relative_to(root).as_posix(),
        "artifact_sha256": payload_sha256(artifact),
        "artifact": artifact,
    }
