from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from bluefern_dispatches.source_based_retrospective_release import (
    CARE_RELEASE_SCHEMA,
    FOOD_RELEASE_SCHEMA,
    SUPPORTED_DATE_BINDINGS,
    validate_release,
)


REQUEST_SCHEMA = "bluefern.source_based_retrospective_publication_request.v1"
FOOD_PUBLICATION_SCHEMA = "food_line_source_based_retrospective_publication_authorization_v1"
CARE_PUBLICATION_SCHEMA = "care_line_source_based_retrospective_publication_authorization_v1"
PUBLICATION_TYPE = "source_based_retrospective_publication_authorization"
PUBLICATION_STATE = "publication_authorized"
PUBLICATION_ROOTS = {
    "food-line": Path("publication-authorizations") / "food-line" / "source-based-retrospectives",
    "care-line": Path("publication-authorizations") / "care-line" / "source-based-retrospectives",
}
PUBLICATION_SCHEMAS = {
    "food-line": FOOD_PUBLICATION_SCHEMA,
    "care-line": CARE_PUBLICATION_SCHEMA,
}


class SourceBasedRetrospectivePublicationError(ValueError):
    pass


def _filesystem_path(path: Path) -> str:
    text = str(path)
    if not text.startswith("\\\\?\\") and len(text) >= 240:
        return "\\\\?\\" + str(path.resolve())
    return text


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + _sha256_bytes(raw)


def _read_json(path: Path) -> Any:
    try:
        with open(_filesystem_path(path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBasedRetrospectivePublicationError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(_filesystem_path(path), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _assert_clean_source(root: Path) -> None:
    dirty = _git(root, "status", "--porcelain")
    if dirty:
        raise SourceBasedRetrospectivePublicationError("source working tree must be clean before publication authorization")


def _safe_slug(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise SourceBasedRetrospectivePublicationError(f"{field} must be a lowercase slug")
    return text


def _require_dispatch(value: Any) -> str:
    dispatch = str(value or "").strip()
    if dispatch not in PUBLICATION_ROOTS:
        raise SourceBasedRetrospectivePublicationError("dispatch must be food-line or care-line")
    return dispatch


def _require_nonempty(row: dict[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise SourceBasedRetrospectivePublicationError(f"{field} is required")
    return value


def publication_path_for(dispatch: str, publication_batch_id: str) -> str:
    dispatch = _require_dispatch(dispatch)
    return (PUBLICATION_ROOTS[dispatch] / f"{_safe_slug(publication_batch_id, 'publication_batch_id')}-publication-v1.json").as_posix()


def _assert_no_deployment_authority(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    for field in (
        "pages_authorized",
        "pages_push_authorized",
        "social_authorized",
        "audio_authorized",
        "schedule_authorized",
        "scheduled_task_change_authorized",
        "public_generation_authorized",
        "public_artifacts_generated",
    ):
        if payload.get(field) is not False:
            errors.append(f"{label} {field} must be false")


def _file_sha(path: Path) -> str:
    return "sha256:" + _sha256_bytes(path.read_bytes())


def _load_release(root: Path, binding: dict[str, Any], dispatch: str) -> tuple[dict[str, Any], str]:
    path_text = _require_nonempty(binding, "release_path")
    release_path = Path(path_text)
    if release_path.is_absolute():
        raise SourceBasedRetrospectivePublicationError("release_path must be repository-relative")
    path = (root / release_path).resolve(strict=True)
    if root.resolve() not in path.parents:
        raise SourceBasedRetrospectivePublicationError("release_path resolves outside repository")
    actual_hash = _file_sha(path)
    if actual_hash != str(binding.get("release_sha256") or "").strip():
        raise SourceBasedRetrospectivePublicationError("release hash mismatch")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectivePublicationError("release must be a JSON object")
    errors = validate_release(payload, expected_dispatch=dispatch)
    if errors:
        raise SourceBasedRetrospectivePublicationError("release validation failed: " + "; ".join(errors))
    if payload.get("release_authorized") is not True:
        raise SourceBasedRetrospectivePublicationError("release_authorized must be true")
    if payload.get("publication_authorized") is not False:
        raise SourceBasedRetrospectivePublicationError("release must not already authorize publication")
    return payload, actual_hash


def _request_decisions(request: dict[str, Any], dispatch: str) -> dict[str, dict[str, Any]]:
    rows = request.get("publication_items")
    if not isinstance(rows, list) or not rows:
        raise SourceBasedRetrospectivePublicationError("publication_items are required")
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SourceBasedRetrospectivePublicationError("publication item request must be an object")
        if row.get("dispatch") != dispatch:
            raise SourceBasedRetrospectivePublicationError("publication item request dispatch mismatch")
        item_id = _require_nonempty(row, "release_item_id")
        if item_id in selected:
            raise SourceBasedRetrospectivePublicationError("duplicate publication item IDs")
        selected[item_id] = row
    return selected


def _publication_item(release: dict[str, Any], release_item: dict[str, Any], decision: dict[str, Any], release_hash: str) -> dict[str, Any]:
    release_item_id = _require_nonempty(decision, "release_item_id")
    if release_item_id != release_item.get("approval_item_id"):
        raise SourceBasedRetrospectivePublicationError("publication item does not match release item")
    chronology = _require_nonempty(decision, "chronology_classification")
    if chronology != release_item.get("date_binding_classification"):
        raise SourceBasedRetrospectivePublicationError("chronology classification must match release lineage")
    if chronology not in SUPPORTED_DATE_BINDINGS:
        raise SourceBasedRetrospectivePublicationError("chronology classification is unsupported")
    public_placement = _require_nonempty(decision, "public_edition_date_or_placement")
    event_binding = _require_nonempty(decision, "event_or_effective_date_or_range")
    release_binding = str(release_item.get("recommended_public_date_binding") or "").strip()
    if event_binding != release_binding:
        raise SourceBasedRetrospectivePublicationError("event/effective binding must match release lineage")
    wording = _require_nonempty(decision, "public_wording_constraints")
    release_wording = str(release_item.get("wording_constraints") or "").strip()
    if release_wording and release_wording not in wording:
        raise SourceBasedRetrospectivePublicationError("public wording constraints must preserve release wording constraints")
    traceability = _require_nonempty(decision, "source_traceability_status")
    if traceability != "traceable":
        raise SourceBasedRetrospectivePublicationError("source traceability status must be traceable")
    state = _require_nonempty(decision, "human_publication_authorization_state")
    if state != PUBLICATION_STATE:
        raise SourceBasedRetrospectivePublicationError("human publication authorization state is invalid")
    return {
        "publication_item_id": release_item_id,
        "release_batch_id": release["release_batch_id"],
        "release_item_id": release_item_id,
        "release_item_sha256": _fingerprint(release_item),
        "release_record_sha256": release_hash,
        "approval_item_id": release_item.get("approval_item_id"),
        "source_identifier": release_item.get("source_identifier"),
        "retrospective_finding_id": release_item.get("retrospective_finding_id"),
        "approval_item_sha256": release_item.get("approval_item_sha256"),
        "readiness_item_sha256": release_item.get("readiness_item_sha256"),
        "dispatch": release_item.get("dispatch"),
        "publication_decision": PUBLICATION_STATE,
        "human_publication_authorization_state": state,
        "publication_authorized": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_generation_authorized": False,
        "public_artifacts_generated": False,
        "chronology_classification": chronology,
        "source_publication_date": str(release_item.get("source_or_publication_date") or ""),
        "event_or_effective_date_or_range": event_binding,
        "retrospective_coverage_period": str(decision.get("retrospective_coverage_period") or release_item.get("retrospective_coverage_month") or "").strip(),
        "public_edition_date_or_placement": public_placement,
        "public_wording_constraints": wording,
        "duplicate_status": release_item.get("duplicate_status"),
        "duplicate_relationship": release_item.get("duplicate_relationship", ""),
        "source_traceability_status": traceability,
        "release_item_snapshot": release_item,
    }


def _existing_publication_item_ids(root: Path, target_path: Path) -> set[str]:
    seen: set[str] = set()
    for base in PUBLICATION_ROOTS.values():
        root_base = root / base
        if not root_base.exists():
            continue
        for path in root_base.glob("*-publication-v1.json"):
            if path.resolve() == target_path.resolve():
                continue
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            for item in payload.get("publication_items", []):
                if isinstance(item, dict) and str(item.get("release_item_id") or "").strip():
                    seen.add(str(item["release_item_id"]))
    return seen


def validate_publication(payload: dict[str, Any], *, expected_dispatch: str | None = None) -> list[str]:
    errors: list[str] = []
    dispatch = str(payload.get("dispatch") or "")
    if expected_dispatch is not None and dispatch != expected_dispatch:
        errors.append("publication dispatch does not match expected dispatch")
    if dispatch not in PUBLICATION_ROOTS:
        errors.append("publication dispatch must be food-line or care-line")
        return errors
    if payload.get("schema_version") != PUBLICATION_SCHEMAS[dispatch]:
        errors.append("publication schema_version does not match dispatch")
    if payload.get("publication_type") != PUBLICATION_TYPE:
        errors.append("publication_type is invalid")
    if payload.get("publication_authorized") is not True:
        errors.append("publication_authorized must be true")
    _assert_no_deployment_authority(payload, "publication", errors)
    items = payload.get("publication_items")
    if not isinstance(items, list) or not items:
        errors.append("publication_items are required")
        return errors
    if payload.get("item_count") != len(items):
        errors.append("item_count does not match publication_items")
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"publication item {index} must be an object")
            continue
        item_id = str(item.get("release_item_id") or "")
        if not item_id:
            errors.append(f"publication item {index} release_item_id missing")
        elif item_id in seen:
            errors.append("duplicate publication item IDs")
        seen.add(item_id)
        if item.get("dispatch") != dispatch:
            errors.append(f"publication item {index} dispatch does not match publication dispatch")
        if item.get("publication_decision") != PUBLICATION_STATE or item.get("publication_authorized") is not True:
            errors.append(f"publication item {index} publication decision is invalid")
        _assert_no_deployment_authority(item, f"publication item {index}", errors)
        if item.get("chronology_classification") not in SUPPORTED_DATE_BINDINGS:
            errors.append(f"publication item {index} chronology classification is unsupported")
        for field in (
            "release_batch_id",
            "release_record_sha256",
            "release_item_sha256",
            "approval_item_id",
            "source_identifier",
            "retrospective_finding_id",
            "public_edition_date_or_placement",
            "event_or_effective_date_or_range",
            "retrospective_coverage_period",
            "public_wording_constraints",
            "duplicate_status",
            "source_traceability_status",
            "human_publication_authorization_state",
        ):
            if not str(item.get(field) or "").strip():
                errors.append(f"publication item {index} {field} missing")
    return errors


def create_publication(root: Path, request_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    request_absolute = request_path.resolve(strict=True)
    if root == request_absolute or root in request_absolute.parents:
        raise SourceBasedRetrospectivePublicationError("publication request must remain private and outside the repository")
    request = _read_json(request_absolute)
    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise SourceBasedRetrospectivePublicationError("publication request schema_version is invalid")
    dispatch = _require_dispatch(request.get("dispatch"))
    publication_batch_id = _safe_slug(request.get("publication_batch_id"), "publication_batch_id")
    authorized_by = _require_nonempty(request, "authorized_by")
    authorized_at = _require_nonempty(request, "authorized_at")
    datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    source_base_commit = _require_nonempty(request, "source_base_commit")
    if _git(root, "rev-parse", "HEAD") != source_base_commit:
        raise SourceBasedRetrospectivePublicationError("source_base_commit must match current HEAD")
    _assert_clean_source(root)
    decisions = _request_decisions(request, dispatch)
    release_bindings = request.get("release_bindings")
    if not isinstance(release_bindings, list) or not release_bindings:
        raise SourceBasedRetrospectivePublicationError("release_bindings are required")
    release_records: list[dict[str, Any]] = []
    publication_items: list[dict[str, Any]] = []
    for binding in release_bindings:
        if not isinstance(binding, dict):
            raise SourceBasedRetrospectivePublicationError("release binding must be an object")
        release, release_hash = _load_release(root, binding, dispatch)
        release_records.append(
            {
                "release_batch_id": release["release_batch_id"],
                "release_path": binding["release_path"],
                "release_sha256": release_hash,
                "release_item_count": len(release["release_items"]),
            }
        )
        for release_item in release["release_items"]:
            item_id = str(release_item.get("approval_item_id") or "").strip()
            if item_id not in decisions:
                raise SourceBasedRetrospectivePublicationError(f"publication request missing release item: {item_id}")
            publication_items.append(_publication_item(release, release_item, decisions[item_id], release_hash))
    if len({item["release_item_id"] for item in publication_items}) != len(publication_items):
        raise SourceBasedRetrospectivePublicationError("duplicate publication item IDs")
    if set(decisions) != {item["release_item_id"] for item in publication_items}:
        raise SourceBasedRetrospectivePublicationError("publication request includes items outside release lineage")
    if request.get("expected_item_count") != len(publication_items):
        raise SourceBasedRetrospectivePublicationError("expected_item_count does not match publication items")
    path = root / publication_path_for(dispatch, publication_batch_id)
    collisions = _existing_publication_item_ids(root, path)
    duplicated = sorted(collisions.intersection(item["release_item_id"] for item in publication_items))
    if duplicated:
        raise SourceBasedRetrospectivePublicationError("release item already has publication authorization: " + ", ".join(duplicated))
    publication = {
        "schema_version": PUBLICATION_SCHEMAS[dispatch],
        "publication_type": PUBLICATION_TYPE,
        "dispatch": dispatch,
        "publication_batch_id": publication_batch_id,
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "source_base_commit": source_base_commit,
        "release_records": release_records,
        "release_batch_ids": [row["release_batch_id"] for row in release_records],
        "item_count": len(publication_items),
        "publication_items": publication_items,
        "publication_authorized": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_generation_authorized": False,
        "public_artifacts_generated": False,
        "publication_fingerprint": _fingerprint(publication_items),
    }
    errors = validate_publication(publication, expected_dispatch=dispatch)
    if errors:
        raise SourceBasedRetrospectivePublicationError("; ".join(errors))
    if path.exists():
        existing = _read_json(path)
        if existing == publication:
            return {"ok": True, "status": "idempotent_noop", "publication_path": path.as_posix(), "publication": publication}
        raise SourceBasedRetrospectivePublicationError(f"publication authorization already exists with different content: {path}")
    _write_json(path, publication)
    return {"ok": True, "status": "publication_written", "publication_path": path.as_posix(), "publication": publication}


def validate_publication_path(root: Path, publication_path: Path, *, dispatch: str | None = None) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = publication_path if publication_path.is_absolute() else root / publication_path
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectivePublicationError("publication authorization must be a JSON object")
    errors = validate_publication(payload, expected_dispatch=dispatch)
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "publication_path": path.as_posix()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage source-based retrospective publication authorization records.")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--request", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--publication-path", type=Path, required=True)
    validate.add_argument("--dispatch", choices=sorted(PUBLICATION_ROOTS))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "create":
            result = create_publication(args.repo_root, args.request)
        else:
            result = validate_publication_path(args.repo_root, args.publication_path, dispatch=args.dispatch)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
