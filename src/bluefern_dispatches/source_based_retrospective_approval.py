from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


REQUEST_SCHEMA = "bluefern.source_based_retrospective_approval_request.v1"
FOOD_APPROVAL_SCHEMA = "food_line_source_based_retrospective_approval_v1"
CARE_APPROVAL_SCHEMA = "care_line_source_based_retrospective_approval_v1"
APPROVAL_TYPE = "source_based_retrospective_editorial_approval"
MAX_ITEMS = 6

APPROVAL_ROOTS = {
    "food-line": Path("approvals") / "food-line" / "source-based-retrospectives",
    "care-line": Path("approvals") / "care-line" / "source-based-retrospectives",
}
APPROVAL_SCHEMAS = {
    "food-line": FOOD_APPROVAL_SCHEMA,
    "care-line": CARE_APPROVAL_SCHEMA,
}
SUPPORTED_APPROVAL_STATES = {"approved_for_retrospective_editorial_use"}


class SourceBasedRetrospectiveApprovalError(ValueError):
    pass


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + _sha256_bytes(raw)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBasedRetrospectiveApprovalError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repo_root(root: Path) -> Path:
    return root.resolve(strict=True)


def _safe_batch_id(batch_id: Any) -> str:
    value = str(batch_id or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,96}", value):
        raise SourceBasedRetrospectiveApprovalError("batch_id must be a lowercase slug")
    return value


def approval_path_for(dispatch: str, batch_id: str) -> str:
    dispatch = _require_dispatch(dispatch)
    return (APPROVAL_ROOTS[dispatch] / f"{_safe_batch_id(batch_id)}-approval-v1.json").as_posix()


def _require_dispatch(dispatch: Any) -> str:
    value = str(dispatch or "").strip()
    if value not in APPROVAL_ROOTS:
        raise SourceBasedRetrospectiveApprovalError("dispatch must be food-line or care-line")
    return value


def _require_nonempty(row: dict[str, Any], field: str, *, label: str | None = None) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise SourceBasedRetrospectiveApprovalError(f"{label or field} is required")
    return value


def _extract_items(prep: Any) -> list[dict[str, Any]]:
    if isinstance(prep, dict) and isinstance(prep.get("items"), list):
        items = prep["items"]
    elif isinstance(prep, dict) and isinstance(prep.get("findings"), list):
        items = prep["findings"]
    elif isinstance(prep, list):
        items = prep
    else:
        raise SourceBasedRetrospectiveApprovalError("approval-prep artifact must contain an items array")
    if not all(isinstance(item, dict) for item in items):
        raise SourceBasedRetrospectiveApprovalError("approval-prep items must be objects")
    return list(items)


def _source_identifier(item: dict[str, Any]) -> str:
    for field in (
        "original_retrospective_finding_id",
        "retrospective_finding_id",
        "row_id",
        "finding_id",
        "source_record_id",
        "candidate_id",
    ):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    public = item.get("proposed_public_record")
    if isinstance(public, dict):
        value = str(public.get("traceability_source_identifier") or "").strip()
        if value:
            return value
    return ""


def _lineage_identifier(item: dict[str, Any]) -> str:
    for field in ("original_retrospective_finding_id", "retrospective_finding_id", "row_id", "finding_id"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return ""


def _event_date(item: dict[str, Any]) -> str:
    public = item.get("proposed_public_record")
    if isinstance(public, dict):
        value = str(public.get("event_date_or_effective_date") or "").strip()
        if value:
            return value
    for field in ("date_or_event_date", "event_date", "effective_date", "date"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return ""


def _field(item: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = str(item.get(field) or "").strip()
        if value:
            return value
    public = item.get("proposed_public_record")
    if isinstance(public, dict):
        for field in fields:
            value = str(public.get(field) or "").strip()
            if value:
                return value
    return ""


def _approval_item(dispatch: str, item: dict[str, Any], prep_sha256: str) -> dict[str, Any]:
    item_dispatch = str(item.get("dispatch") or dispatch).strip()
    if item_dispatch != dispatch:
        raise SourceBasedRetrospectiveApprovalError("approval-prep item dispatch mismatch")
    source_id = _source_identifier(item)
    if not source_id:
        raise SourceBasedRetrospectiveApprovalError("source identifier missing")
    lineage = _lineage_identifier(item)
    if not lineage:
        raise SourceBasedRetrospectiveApprovalError("retrospective finding lineage missing")
    source_url = _field(item, "source_url", "url", "canonical_source_url")
    if not source_url:
        raise SourceBasedRetrospectiveApprovalError("source URL missing")
    event_date = _event_date(item)
    if not event_date:
        raise SourceBasedRetrospectiveApprovalError("event/effective date missing")
    prep_decision = str(item.get("approval_preparation_outcome") or item.get("triage_decision") or "").strip()
    if not prep_decision:
        raise SourceBasedRetrospectiveApprovalError("approval-prep decision lineage missing")
    snapshot = {
        key: item.get(key)
        for key in sorted(item)
        if key
        in {
            "approval_preparation_outcome",
            "approval_preparation_reason",
            "currentness_freshness_basis",
            "date_or_event_date",
            "dispatch",
            "duplicate_cluster_or_linkage",
            "location",
            "original_retrospective_finding_id",
            "pressure_type",
            "primary_pre_pr_311_failure_cause",
            "prior_retrospective_disposition",
            "prior_triage_decision",
            "publisher",
            "recommended_action",
            "significance",
            "source_evidence_basis",
            "source_strength",
            "source_url",
            "state",
            "title",
            "uncertainty",
        }
    }
    public = item.get("proposed_public_record")
    if isinstance(public, dict):
        snapshot["proposed_public_record"] = public
    item_hash = _fingerprint(snapshot)
    return {
        "approval_item_id": f"{dispatch}-source-retrospective-{hashlib.sha256((source_id + prep_sha256).encode('utf-8')).hexdigest()[:16]}",
        "approval_state": "approved_for_retrospective_editorial_use",
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": False,
        "approved_for_publication": False,
        "dispatch": dispatch,
        "source_identifier": source_id,
        "source_url": source_url,
        "publisher": _field(item, "publisher", "source_publisher", "source_name"),
        "retrospective_finding_id": lineage,
        "retrospective_lineage_identifier": lineage,
        "approval_prep_decision_id": f"{prep_decision}:{lineage}",
        "approval_prep_item_sha256": item_hash,
        "event_or_effective_date": event_date,
        "location": _field(item, "location"),
        "state_or_territory": _field(item, "state"),
        "pressure_or_service_type": _field(item, "pressure_type", "service_line", "event_type"),
        "approval_rationale": _field(item, "approval_preparation_reason", "decision_reason", "source_evidence_basis"),
        "uncertainty_or_wording_constraint": _field(item, "uncertainty"),
        "duplicate_lineage": _field(item, "duplicate_cluster_or_linkage", "duplicate_relationship"),
        "source_evidence_sha256": _fingerprint(
            {
                "source_identifier": source_id,
                "source_url": source_url,
                "publisher": _field(item, "publisher", "source_publisher", "source_name"),
                "evidence": _field(item, "source_evidence_basis", "evidence_excerpt", "supporting_passage"),
            }
        ),
        "bounded_item_snapshot": snapshot,
    }


def validate_approval(payload: dict[str, Any], *, expected_dispatch: str | None = None) -> list[str]:
    errors: list[str] = []
    dispatch = str(payload.get("dispatch") or "")
    if expected_dispatch is not None and dispatch != expected_dispatch:
        errors.append("approval dispatch does not match expected dispatch")
    if dispatch not in APPROVAL_ROOTS:
        errors.append("approval dispatch must be food-line or care-line")
        return errors
    if payload.get("schema_version") != APPROVAL_SCHEMAS[dispatch]:
        errors.append("approval schema_version does not match dispatch")
    if payload.get("approval_type") != APPROVAL_TYPE:
        errors.append("approval_type is invalid")
    if payload.get("approved_for_retrospective_editorial_use") is not True:
        errors.append("retrospective editorial approval flag must be true")
    for flag in ("approved_for_release", "approved_for_publication", "release_authorized", "publication_authorized", "pages_authorized"):
        if payload.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if payload.get("social_authorized") is not False or payload.get("audio_authorized") is not False:
        errors.append("social and audio authority must be false")
    if payload.get("scheduled_task_change_authorized") is not False:
        errors.append("scheduled task authority must be false")
    items = payload.get("approved_items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        errors.append(f"approved_items must contain one through {MAX_ITEMS} items")
        return errors
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"approved item {index} must be an object")
            continue
        if item.get("dispatch") != dispatch:
            errors.append(f"approved item {index} dispatch mismatch")
        item_id = str(item.get("approval_item_id") or "")
        if not item_id:
            errors.append(f"approved item {index} approval_item_id missing")
        elif item_id in seen:
            errors.append("duplicate approval item IDs")
        seen.add(item_id)
        if item.get("approval_state") not in SUPPORTED_APPROVAL_STATES:
            errors.append(f"approved item {index} has unsupported approval_state")
        if item.get("approved_for_retrospective_editorial_use") is not True:
            errors.append(f"approved item {index} retrospective approval flag must be true")
        if item.get("approved_for_release") is not False or item.get("approved_for_publication") is not False:
            errors.append(f"approved item {index} release/publication flags must be false")
        for field in (
            "source_identifier",
            "source_url",
            "retrospective_finding_id",
            "retrospective_lineage_identifier",
            "approval_prep_decision_id",
            "event_or_effective_date",
            "approval_rationale",
            "approval_prep_item_sha256",
            "source_evidence_sha256",
        ):
            if not str(item.get(field) or "").strip():
                errors.append(f"approved item {index} {field} missing")
        snapshot = item.get("bounded_item_snapshot")
        if isinstance(snapshot, dict):
            expected_item_hash = _fingerprint(snapshot)
            if item.get("approval_prep_item_sha256") != expected_item_hash:
                errors.append(f"approved item {index} approval-prep item hash mismatch")
            expected_evidence_hash = _fingerprint(
                {
                    "source_identifier": item.get("source_identifier"),
                    "source_url": item.get("source_url"),
                    "publisher": item.get("publisher"),
                    "evidence": str(
                        snapshot.get("source_evidence_basis")
                        or snapshot.get("evidence_excerpt")
                        or snapshot.get("supporting_passage")
                        or ""
                    ).strip(),
                }
            )
            if item.get("source_evidence_sha256") != expected_evidence_hash:
                errors.append(f"approved item {index} source evidence hash mismatch")
    return errors


def create_approval(root: Path, request_path: Path) -> dict[str, Any]:
    root = _repo_root(root)
    request_absolute = request_path.resolve(strict=True)
    if root == request_absolute or root in request_absolute.parents:
        raise SourceBasedRetrospectiveApprovalError("approval request must remain private and outside the repository")
    request = _read_json(request_absolute)
    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise SourceBasedRetrospectiveApprovalError("approval request schema_version is invalid")
    dispatch = _require_dispatch(request.get("dispatch"))
    batch_id = _safe_batch_id(request.get("batch_id"))
    approved_by = _require_nonempty(request, "approved_by")
    approved_at = _require_nonempty(request, "approved_at")
    datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    source_base_commit = _require_nonempty(request, "source_base_commit")
    current_head = _git(root, "rev-parse", "HEAD")
    if current_head != source_base_commit:
        raise SourceBasedRetrospectiveApprovalError("source_base_commit must match current HEAD")
    prep_path = Path(_require_nonempty(request, "approval_prep_artifact_path"))
    if not prep_path.is_absolute():
        prep_path = root / prep_path
    prep_raw = prep_path.resolve(strict=True).read_bytes()
    prep_sha = _sha256_bytes(prep_raw)
    if prep_sha != str(request.get("approval_prep_artifact_sha256") or "").removeprefix("sha256:"):
        raise SourceBasedRetrospectiveApprovalError("approval-prep artifact hash mismatch")
    prep = json.loads(prep_raw.decode("utf-8"))
    prep_items = _extract_items(prep)
    selected_ids = request.get("approved_item_source_identifiers")
    if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= MAX_ITEMS:
        raise SourceBasedRetrospectiveApprovalError(f"approved_item_source_identifiers must contain one through {MAX_ITEMS} IDs")
    selected_text = [str(value or "").strip() for value in selected_ids]
    if len(set(selected_text)) != len(selected_text) or any(not value for value in selected_text):
        raise SourceBasedRetrospectiveApprovalError("approved item source identifiers must be unique and non-empty")
    by_id = {_source_identifier(item): item for item in prep_items if _source_identifier(item)}
    missing = [value for value in selected_text if value not in by_id]
    if missing:
        raise SourceBasedRetrospectiveApprovalError("approved item source identifier missing from prep artifact: " + ", ".join(missing))
    approved_items = [_approval_item(dispatch, by_id[value], prep_sha) for value in selected_text]
    approval = {
        "schema_version": APPROVAL_SCHEMAS[dispatch],
        "approval_type": APPROVAL_TYPE,
        "dispatch": dispatch,
        "batch_id": batch_id,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "source_base_commit": source_base_commit,
        "approval_prep_artifact": {
            "path": str(request.get("approval_prep_artifact_path")),
            "sha256": "sha256:" + prep_sha,
            "item_count": len(prep_items),
            "approved_item_count": len(approved_items),
        },
        "approved_items": approved_items,
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": False,
        "approved_for_publication": False,
        "release_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_output_authorized": False,
        "approval_fingerprint": _fingerprint(approved_items),
    }
    errors = validate_approval(approval, expected_dispatch=dispatch)
    if errors:
        raise SourceBasedRetrospectiveApprovalError("; ".join(errors))
    path = root / approval_path_for(dispatch, batch_id)
    if path.exists():
        existing = _read_json(path)
        if existing == approval:
            return {"ok": True, "status": "idempotent_noop", "approval_path": path.as_posix(), "approval": approval}
        raise SourceBasedRetrospectiveApprovalError(f"approval already exists with different content: {path}")
    _write_json(path, approval)
    return {"ok": True, "status": "approval_written", "approval_path": path.as_posix(), "approval": approval}


def validate_approval_path(root: Path, approval_path: Path, *, dispatch: str | None = None) -> dict[str, Any]:
    root = _repo_root(root)
    path = approval_path if approval_path.is_absolute() else root / approval_path
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectiveApprovalError("approval must be a JSON object")
    errors = validate_approval(payload, expected_dispatch=dispatch)
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "approval_path": path.as_posix()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage source-based retrospective editorial approval records.")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--request", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--approval-path", type=Path, required=True)
    validate.add_argument("--dispatch", choices=sorted(APPROVAL_ROOTS))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "create":
            result = create_approval(args.repo_root, args.request)
        else:
            result = validate_approval_path(args.repo_root, args.approval_path, dispatch=args.dispatch)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
