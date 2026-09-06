from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from bluefern_dispatches.source_based_retrospective_approval import validate_approval


REQUEST_SCHEMA = "bluefern.source_based_retrospective_release_request.v1"
FOOD_RELEASE_SCHEMA = "food_line_source_based_retrospective_release_v1"
CARE_RELEASE_SCHEMA = "care_line_source_based_retrospective_release_v1"
RELEASE_TYPE = "source_based_retrospective_release_authorization"
RELEASE_STATE = "release_authorized"
READINESS_SCHEMA = "bluefern.source_based_retrospective_release_readiness_review.v1"
SUPPORTED_DATE_BINDINGS = {
    "august_event",
    "august_announcement_future_effect",
    "august_restoration",
    "august_reporting_on_continuing_prior_loss",
    "september_effective_event_with_august_source",
}
RELEASE_ROOTS = {
    "food-line": Path("releases") / "food-line" / "source-based-retrospectives",
    "care-line": Path("releases") / "care-line" / "source-based-retrospectives",
}
RELEASE_SCHEMAS = {
    "food-line": FOOD_RELEASE_SCHEMA,
    "care-line": CARE_RELEASE_SCHEMA,
}


class SourceBasedRetrospectiveReleaseError(ValueError):
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
        raise SourceBasedRetrospectiveReleaseError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _assert_clean_source(root: Path) -> None:
    dirty = _git(root, "status", "--porcelain")
    if dirty:
        raise SourceBasedRetrospectiveReleaseError("source working tree must be clean before release authorization")


def _safe_slug(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise SourceBasedRetrospectiveReleaseError(f"{field} must be a lowercase slug")
    return text


def _require_dispatch(value: Any) -> str:
    dispatch = str(value or "").strip()
    if dispatch not in RELEASE_ROOTS:
        raise SourceBasedRetrospectiveReleaseError("dispatch must be food-line or care-line")
    return dispatch


def _require_nonempty(row: dict[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise SourceBasedRetrospectiveReleaseError(f"{field} is required")
    return value


def release_path_for(dispatch: str, release_batch_id: str) -> str:
    dispatch = _require_dispatch(dispatch)
    return (RELEASE_ROOTS[dispatch] / f"{_safe_slug(release_batch_id, 'release_batch_id')}-release-v1.json").as_posix()


def _assert_no_public_authority(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    for field in ("publication_authorized", "pages_authorized", "social_authorized", "audio_authorized"):
        if payload.get(field) is not False:
            errors.append(f"{label} {field} must be false")
    if payload.get("scheduled_task_change_authorized", payload.get("schedule_authorized", False)) is not False:
        errors.append(f"{label} schedule authority must be false")


def _approval_hash(path: Path) -> str:
    return "sha256:" + _sha256_bytes(path.read_bytes())


def _load_approval(root: Path, binding: dict[str, Any], dispatch: str) -> tuple[dict[str, Any], str]:
    path_text = _require_nonempty(binding, "approval_path")
    approval_path = Path(path_text)
    if approval_path.is_absolute():
        raise SourceBasedRetrospectiveReleaseError("approval_path must be repository-relative")
    path = (root / approval_path).resolve(strict=True)
    if root.resolve() not in path.parents:
        raise SourceBasedRetrospectiveReleaseError("approval_path resolves outside repository")
    expected_hash = str(binding.get("approval_sha256") or "").strip()
    actual_hash = _approval_hash(path)
    if expected_hash != actual_hash:
        raise SourceBasedRetrospectiveReleaseError("approval hash mismatch")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectiveReleaseError("approval must be a JSON object")
    errors = validate_approval(payload, expected_dispatch=dispatch)
    if errors:
        raise SourceBasedRetrospectiveReleaseError("approval validation failed: " + "; ".join(errors))
    if payload.get("approved_for_retrospective_editorial_use") is not True:
        raise SourceBasedRetrospectiveReleaseError("approval did not grant retrospective editorial use")
    _assert_no_public_authority(payload, "approval", errors := [])
    if errors:
        raise SourceBasedRetrospectiveReleaseError("; ".join(errors))
    return payload, actual_hash


def _readiness_items(readiness: dict[str, Any], dispatch: str) -> dict[str, dict[str, Any]]:
    schema_version = readiness.get("schema_version") or readiness.get("summary", {}).get("schema_version")
    if schema_version != READINESS_SCHEMA:
        raise SourceBasedRetrospectiveReleaseError("release-readiness schema_version is invalid")
    items = readiness.get("items")
    if not isinstance(items, list):
        raise SourceBasedRetrospectiveReleaseError("release-readiness items are missing")
    selected: dict[str, dict[str, Any]] = {}
    for row in items:
        if not isinstance(row, dict) or row.get("dispatch") != dispatch:
            continue
        item_id = str(row.get("approval_item_id") or "").strip()
        if not item_id:
            raise SourceBasedRetrospectiveReleaseError("release-readiness item_id missing")
        if item_id in selected:
            raise SourceBasedRetrospectiveReleaseError("duplicate release-readiness item ID")
        selected[item_id] = row
    return selected


def _release_item(approval_item: dict[str, Any], readiness_item: dict[str, Any], *, coverage_month: str) -> dict[str, Any]:
    if readiness_item.get("release_readiness_state") != "ready_for_release_authorization":
        raise SourceBasedRetrospectiveReleaseError("release-readiness item is not ready")
    date_binding = str(readiness_item.get("date_binding_classification") or "").strip()
    if date_binding not in SUPPORTED_DATE_BINDINGS:
        raise SourceBasedRetrospectiveReleaseError("date-binding classification is unsupported")
    recommended_date = str(readiness_item.get("recommended_public_edition_event_date") or "").strip()
    if not recommended_date:
        raise SourceBasedRetrospectiveReleaseError("recommended public date binding is missing")
    wording = str(readiness_item.get("wording_constraints") or approval_item.get("uncertainty_or_wording_constraint") or "").strip()
    if not wording:
        raise SourceBasedRetrospectiveReleaseError("wording constraints are required")
    duplicate_status = "distinct"
    if str(readiness_item.get("duplicate_relationship") or approval_item.get("duplicate_lineage") or "").strip():
        duplicate_status = "duplicate_lineage_recorded"
    return {
        "approval_item_id": approval_item["approval_item_id"],
        "dispatch": approval_item["dispatch"],
        "source_identifier": approval_item["source_identifier"],
        "retrospective_finding_id": approval_item["retrospective_finding_id"],
        "release_decision": RELEASE_STATE,
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "date_binding_classification": date_binding,
        "recommended_public_date_binding": recommended_date,
        "source_or_publication_date": approval_item.get("bounded_item_snapshot", {}).get("date_or_event_date", ""),
        "event_or_effective_date": approval_item.get("event_or_effective_date", ""),
        "retrospective_coverage_month": coverage_month,
        "wording_constraints": wording,
        "duplicate_status": duplicate_status,
        "duplicate_relationship": readiness_item.get("duplicate_relationship") or approval_item.get("duplicate_lineage", ""),
        "lineage_validation_status": readiness_item.get("lineage_validation_status"),
        "readiness_item_snapshot": readiness_item,
        "approval_item_sha256": _fingerprint(approval_item),
        "readiness_item_sha256": _fingerprint(readiness_item),
    }


def validate_release(payload: dict[str, Any], *, expected_dispatch: str | None = None) -> list[str]:
    errors: list[str] = []
    dispatch = str(payload.get("dispatch") or "")
    if expected_dispatch is not None and dispatch != expected_dispatch:
        errors.append("release dispatch does not match expected dispatch")
    if dispatch not in RELEASE_ROOTS:
        errors.append("release dispatch must be food-line or care-line")
        return errors
    if payload.get("schema_version") != RELEASE_SCHEMAS[dispatch]:
        errors.append("release schema_version does not match dispatch")
    if payload.get("release_type") != RELEASE_TYPE:
        errors.append("release_type is invalid")
    if payload.get("release_authorized") is not True:
        errors.append("release_authorized must be true")
    _assert_no_public_authority(payload, "release", errors)
    items = payload.get("release_items")
    if not isinstance(items, list) or not items:
        errors.append("release_items are required")
        return errors
    expected_count = payload.get("item_count")
    if expected_count != len(items):
        errors.append("item_count does not match release_items")
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"release item {index} must be an object")
            continue
        item_id = str(item.get("approval_item_id") or "")
        if not item_id:
            errors.append(f"release item {index} approval_item_id missing")
        elif item_id in seen:
            errors.append("duplicate release item IDs")
        seen.add(item_id)
        if item.get("release_decision") != RELEASE_STATE or item.get("release_authorized") is not True:
            errors.append(f"release item {index} release decision is invalid")
        if item.get("dispatch") != dispatch:
            errors.append(f"release item {index} dispatch does not match release dispatch")
        _assert_no_public_authority(item, f"release item {index}", errors)
        if item.get("date_binding_classification") not in SUPPORTED_DATE_BINDINGS:
            errors.append(f"release item {index} date-binding classification is unsupported")
        for field in (
            "source_identifier",
            "retrospective_finding_id",
            "recommended_public_date_binding",
            "wording_constraints",
            "lineage_validation_status",
            "approval_item_sha256",
            "readiness_item_sha256",
        ):
            if not str(item.get(field) or "").strip():
                errors.append(f"release item {index} {field} missing")
    return errors


def create_release(root: Path, request_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    request_absolute = request_path.resolve(strict=True)
    if root == request_absolute or root in request_absolute.parents:
        raise SourceBasedRetrospectiveReleaseError("release request must remain private and outside the repository")
    request = _read_json(request_absolute)
    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise SourceBasedRetrospectiveReleaseError("release request schema_version is invalid")
    dispatch = _require_dispatch(request.get("dispatch"))
    release_batch_id = _safe_slug(request.get("release_batch_id"), "release_batch_id")
    coverage_month = str(request.get("retrospective_coverage_month") or "2026-08")
    authorized_by = _require_nonempty(request, "authorized_by")
    authorized_at = _require_nonempty(request, "authorized_at")
    datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    source_base_commit = _require_nonempty(request, "source_base_commit")
    if _git(root, "rev-parse", "HEAD") != source_base_commit:
        raise SourceBasedRetrospectiveReleaseError("source_base_commit must match current HEAD")
    _assert_clean_source(root)
    readiness_path = Path(_require_nonempty(request, "release_readiness_review_path"))
    if not readiness_path.is_absolute():
        readiness_path = root / readiness_path
    readiness_raw = readiness_path.resolve(strict=True).read_bytes()
    readiness_sha = "sha256:" + _sha256_bytes(readiness_raw)
    if readiness_sha != str(request.get("release_readiness_review_sha256") or "").strip():
        raise SourceBasedRetrospectiveReleaseError("release-readiness hash mismatch")
    readiness = json.loads(readiness_raw.decode("utf-8"))
    if not isinstance(readiness, dict):
        raise SourceBasedRetrospectiveReleaseError("release-readiness review must be a JSON object")
    readiness_by_id = _readiness_items(readiness, dispatch)
    approval_bindings = request.get("approval_bindings")
    if not isinstance(approval_bindings, list) or not approval_bindings:
        raise SourceBasedRetrospectiveReleaseError("approval_bindings are required")
    release_items: list[dict[str, Any]] = []
    approval_records: list[dict[str, Any]] = []
    for binding in approval_bindings:
        if not isinstance(binding, dict):
            raise SourceBasedRetrospectiveReleaseError("approval binding must be an object")
        approval, approval_sha = _load_approval(root, binding, dispatch)
        approval_records.append(
            {
                "approval_batch_id": approval["batch_id"],
                "approval_path": binding["approval_path"],
                "approval_sha256": approval_sha,
                "approval_item_count": len(approval["approved_items"]),
            }
        )
        for item in approval["approved_items"]:
            item_id = item["approval_item_id"]
            if item_id not in readiness_by_id:
                raise SourceBasedRetrospectiveReleaseError(f"release-readiness review missing approval item: {item_id}")
            release_items.append(_release_item(item, readiness_by_id[item_id], coverage_month=coverage_month))
    if len({item["approval_item_id"] for item in release_items}) != len(release_items):
        raise SourceBasedRetrospectiveReleaseError("duplicate approval item IDs in release batch")
    requested_count = request.get("expected_item_count")
    if requested_count != len(release_items):
        raise SourceBasedRetrospectiveReleaseError("expected_item_count does not match release items")
    release = {
        "schema_version": RELEASE_SCHEMAS[dispatch],
        "release_type": RELEASE_TYPE,
        "dispatch": dispatch,
        "release_batch_id": release_batch_id,
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "source_base_commit": source_base_commit,
        "retrospective_coverage_month": coverage_month,
        "source_approval_records": approval_records,
        "source_approval_batch_ids": [row["approval_batch_id"] for row in approval_records],
        "release_readiness_review": {
            "path": str(request.get("release_readiness_review_path")),
            "sha256": readiness_sha,
            "schema_version": readiness.get("summary", {}).get("schema_version") or readiness.get("schema_version"),
        },
        "item_count": len(release_items),
        "release_items": release_items,
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "release_fingerprint": _fingerprint(release_items),
    }
    errors = validate_release(release, expected_dispatch=dispatch)
    if errors:
        raise SourceBasedRetrospectiveReleaseError("; ".join(errors))
    path = root / release_path_for(dispatch, release_batch_id)
    if path.exists():
        existing = _read_json(path)
        if existing == release:
            return {"ok": True, "status": "idempotent_noop", "release_path": path.as_posix(), "release": release}
        raise SourceBasedRetrospectiveReleaseError(f"release already exists with different content: {path}")
    _write_json(path, release)
    return {"ok": True, "status": "release_written", "release_path": path.as_posix(), "release": release}


def validate_release_path(root: Path, release_path: Path, *, dispatch: str | None = None) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = release_path if release_path.is_absolute() else root / release_path
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectiveReleaseError("release must be a JSON object")
    errors = validate_release(payload, expected_dispatch=dispatch)
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "release_path": path.as_posix()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage source-based retrospective release authorization records.")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--request", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--release-path", type=Path, required=True)
    validate.add_argument("--dispatch", choices=sorted(RELEASE_ROOTS))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "create":
            result = create_release(args.repo_root, args.request)
        else:
            result = validate_release_path(args.repo_root, args.release_path, dispatch=args.dispatch)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
