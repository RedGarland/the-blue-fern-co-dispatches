from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .external_agent_handoff import OPERATOR_RECOVERY_CLASS
from .food_line_operator_recovery_release_authorization import (
    RELEASE_AUTHORIZATION_STATE,
    SCHEMA_VERSION as RELEASE_SCHEMA_VERSION,
    validate_release_authorization,
)


REQUEST_SCHEMA_VERSION = "bluefern.food_line.operator_recovery_publication_authorization_request.v1"
SCHEMA_VERSION = "bluefern.food_line.operator_recovery_publication_authorization.v1"
PUBLICATION_TYPE = "food_line_operator_recovery_publication_authorization"
PUBLICATION_STATE = "OPERATOR_RECOVERY_PUBLICATION_AUTHORIZED"
PUBLICATION_ROOT = Path("publication-authorizations") / "food-line" / "operator-recovery"
SUPPORTED_PLACEMENT_STRATEGIES = {"recovery_disclosed_current_publication"}
FALSE_DEPLOYMENT_FIELDS = (
    "public_generation_authorized",
    "pages_authorized",
    "pages_push_authorized",
    "social_authorized",
    "audio_authorized",
    "schedule_authorized",
    "scheduled_task_change_authorized",
    "public_artifacts_generated",
    "publication_performed",
    "eligible_for_automatic_publication",
)


class FoodLineOperatorRecoveryPublicationAuthorizationError(ValueError):
    pass


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload), encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _assert_clean_source(root: Path) -> None:
    dirty = _git(root, "status", "--porcelain")
    if dirty:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(
            "source working tree must be clean before operator-recovery publication authorization"
        )


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"{field} is required")
    return text


def _safe_slug(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"{field} must be a lowercase slug")
    return text


def _repo_relative(value: Any, field: str) -> str:
    text = _nonempty(value, field).replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"{field} must be repository-relative")
    return text


def _assert_committed_at_head(root: Path, rel_path: str, label: str) -> None:
    try:
        _git(root, "cat-file", "-e", f"HEAD:{rel_path}")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"{label} must be committed at HEAD") from exc


def publication_path_for(publication_id: str) -> str:
    return (PUBLICATION_ROOT / f"{_safe_slug(publication_id, 'publication_id')}-publication-v1.json").as_posix()


def _assert_false(payload: Mapping[str, Any], field: str, label: str) -> None:
    if payload.get(field) is not False:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"{label} {field} must be false")


def _load_release(root: Path, request: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    rel = _repo_relative(request.get("release_path"), "release_path")
    if Path(rel).parts[:3] != ("releases", "food-line", "operator-recovery"):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release_path must use the operator-recovery release owner")
    _assert_committed_at_head(root, rel, "release artifact")
    path = root / rel
    actual_sha = sha256_file(path)
    if actual_sha != _nonempty(request.get("release_sha256"), "release_sha256"):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release hash mismatch")
    release = _read_json(path)
    if not isinstance(release, dict):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release authorization must be a JSON object")
    errors = validate_release_authorization(release)
    if errors:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release validation failed: " + "; ".join(errors))
    if release.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release schema_version is invalid")
    if release.get("release_authorization_state") != RELEASE_AUTHORIZATION_STATE:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release authorization state is invalid")
    if release.get("release_authorized") is not True:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release_authorized must be true")
    for field in ("publication_authorized", "public_generation_authorized", "pages_authorized", "publication_performed", "eligible_for_automatic_publication"):
        _assert_false(release, field, "release")
    release_fingerprint = _nonempty(release.get("release_fingerprint"), "release_fingerprint")
    if release_fingerprint != _nonempty(request.get("release_fingerprint"), "release_fingerprint"):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release fingerprint mismatch")
    return release, rel, actual_sha


def _request_items(request: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = request.get("publication_items")
    if not isinstance(rows, list) or not rows:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication_items are required")
    selected: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication item request must be an object")
        item_id = _nonempty(row.get("item_id"), "item_id")
        if item_id in selected:
            raise FoodLineOperatorRecoveryPublicationAuthorizationError("duplicate publication item IDs")
        selected[item_id] = row
    return selected


def _publication_item(release: Mapping[str, Any], release_item: Mapping[str, Any], decision: Mapping[str, Any], release_sha: str) -> dict[str, Any]:
    item_id = _nonempty(release_item.get("item_id"), "release item_id")
    if decision.get("item_id") != item_id:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication item does not match release item")
    if decision.get("human_publication_authorization_state") != PUBLICATION_STATE:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("human publication authorization state is invalid")
    placement = _nonempty(decision.get("publication_placement_strategy"), "publication_placement_strategy")
    if placement not in SUPPORTED_PLACEMENT_STRATEGIES:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication placement is ambiguous or unsupported")
    if not str(decision.get("public_edition_date_or_placement") or "").strip():
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("public_edition_date_or_placement is required")
    if decision.get("failed_production_disclosure") != "preserve_failed_production_operator_recovery_distinction":
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("failed production disclosure rule is invalid")
    if decision.get("source_traceability_status") != release_item.get("source_verification"):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("source traceability must match release lineage")
    for field in FALSE_DEPLOYMENT_FIELDS:
        if field in decision and decision[field] is not False:
            raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"publication request item {field} must be false")
    expected = {
        "reviewed_headline": "approved_headline",
        "reviewed_summary": "approved_summary",
        "source_url": "source_url",
        "publisher": "publisher",
        "event_date": "event_date",
    }
    for release_field, decision_field in expected.items():
        if decision.get(decision_field) != release_item.get(release_field):
            raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"publication request changed {decision_field}")
    if release_item.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("provenance_class must be operator_recovered_source_watch_evidence")
    for field in (
        "original_production_artifact_present",
        "original_source_watch_lineage_fabricated",
        "publication_authorized",
        "public_generation_authorized",
        "pages_authorized",
        "publication_performed",
        "eligible_for_automatic_publication",
    ):
        _assert_false(release_item, field, "release item")
    if release_item.get("production_collection_failed_before_discovery") is not True:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("failed production distinction must be preserved")
    if release_item.get("failed_production_lineage_preserved") is not True:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("failed production lineage must be preserved")
    if not str(release_item.get("duplicate_disposition") or "").startswith(("NO_DUPLICATE", "nonblocking")):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("duplicate state must be resolved and nonblocking")
    return {
        "item_id": item_id,
        "release_id": release["release_id"],
        "release_item_sha256": _fingerprint(release_item),
        "release_record_sha256": release_sha,
        "publication_authorization_state": PUBLICATION_STATE,
        "publication_authorized": True,
        "release_authorized": True,
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "original_agent_run_id": release_item["original_agent_run_id"],
        "source_url": release_item["source_url"],
        "publisher": release_item["publisher"],
        "event_date": release_item["event_date"],
        "geography": release_item["geography"],
        "reviewed_headline": release_item["reviewed_headline"],
        "reviewed_summary": release_item["reviewed_summary"],
        "publication_placement_strategy": placement,
        "public_edition_date_or_placement": decision["public_edition_date_or_placement"],
        "public_wording_constraints": _nonempty(decision.get("public_wording_constraints"), "public_wording_constraints"),
        "source_traceability_status": decision["source_traceability_status"],
        "failed_production_disclosure": decision["failed_production_disclosure"],
        "release_item_snapshot": release_item,
        **{field: False for field in FALSE_DEPLOYMENT_FIELDS},
    }


def _existing_publication_item_ids(root: Path, target_path: Path) -> set[str]:
    seen: set[str] = set()
    base = root / PUBLICATION_ROOT
    if not base.exists():
        return seen
    for path in base.glob("*-publication-v1.json"):
        if path.resolve() == target_path.resolve():
            continue
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            continue
        for item in payload.get("publication_items", []):
            if isinstance(item, Mapping) and str(item.get("item_id") or "").strip():
                seen.add(str(item["item_id"]))
    return seen


def validate_publication_authorization(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version is invalid")
    if payload.get("publication_type") != PUBLICATION_TYPE:
        errors.append("publication_type is invalid")
    if payload.get("publication_authorization_state") != PUBLICATION_STATE:
        errors.append("publication_authorization_state is invalid")
    if payload.get("dispatch") != "food-line":
        errors.append("dispatch must be food-line")
    if payload.get("publication_authorized") is not True:
        errors.append("publication_authorized must be true")
    for field in FALSE_DEPLOYMENT_FIELDS:
        if payload.get(field) is not False:
            errors.append(f"{field} must be false")
    items = payload.get("publication_items")
    if not isinstance(items, list) or not items:
        errors.append("publication_items are required")
        return errors
    if payload.get("item_count") != len(items):
        errors.append("item_count must match publication_items")
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"publication item {index} must be an object")
            continue
        item_id = str(item.get("item_id") or "")
        if not item_id:
            errors.append(f"publication item {index} item_id missing")
        elif item_id in seen:
            errors.append("duplicate publication item IDs")
        seen.add(item_id)
        if item.get("publication_authorized") is not True:
            errors.append(f"publication item {index} publication_authorized must be true")
        for field in FALSE_DEPLOYMENT_FIELDS:
            if item.get(field) is not False:
                errors.append(f"publication item {index} {field} must be false")
    return errors


def create_publication_authorization(root: Path, request_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    request_absolute = request_path.resolve(strict=True)
    if root == request_absolute or root in request_absolute.parents:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication request must remain private and outside the repository")
    request = _read_json(request_absolute)
    if not isinstance(request, Mapping) or request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication request schema_version is invalid")
    publication_id = _safe_slug(request.get("publication_id"), "publication_id")
    authorized_by = _nonempty(request.get("authorized_by"), "authorized_by")
    authorized_at = _nonempty(request.get("authorized_at"), "authorized_at")
    datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    source_base_commit = _nonempty(request.get("source_base_commit"), "source_base_commit")
    if _git(root, "rev-parse", "HEAD") != source_base_commit:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("source_base_commit must match current HEAD")
    for field in FALSE_DEPLOYMENT_FIELDS:
        if field in request and request[field] is not False:
            raise FoodLineOperatorRecoveryPublicationAuthorizationError(f"publication request {field} must be false")
    release, release_path, release_sha = _load_release(root, request)
    release_items = release.get("release_items")
    if not isinstance(release_items, list):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release_items are required")
    decisions = _request_items(request)
    expected_ids = request.get("expected_item_ids")
    if not isinstance(expected_ids, list) or set(expected_ids) != set(decisions):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("expected_item_ids must match publication items")
    release_ids = [str(item.get("item_id") or "") for item in release_items if isinstance(item, Mapping)]
    if set(decisions) != set(release_ids):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication request item membership must match release")
    hold = str(release.get("hold_item_exclusion", {}).get("excluded_item_ids", [""])[0])
    if hold in decisions:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("HOLD item cannot receive publication authorization")
    if request.get("expected_item_count") != len(release_items):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("expected_item_count does not match release items")
    path = root / publication_path_for(publication_id)
    publication_items = [_publication_item(release, item, decisions[str(item["item_id"])], release_sha) for item in release_items]
    duplicated = sorted(_existing_publication_item_ids(root, path).intersection(item["item_id"] for item in publication_items))
    if duplicated:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("release item already has publication authorization: " + ", ".join(duplicated))
    publication = {
        "schema_version": SCHEMA_VERSION,
        "publication_type": PUBLICATION_TYPE,
        "publication_authorization_state": PUBLICATION_STATE,
        "publication_id": publication_id,
        "dispatch": "food-line",
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "source_base_commit": source_base_commit,
        "release_path": release_path,
        "release_sha256": release_sha,
        "release_fingerprint": release["release_fingerprint"],
        "item_count": len(publication_items),
        "publication_items": publication_items,
        "hold_item_exclusion": release["hold_item_exclusion"],
        "september_10_original_production_runtime_remains_failed": True,
        "original_source_watch_lineage_fabricated": False,
        "publication_authorized": True,
        "release_authorized": True,
        **{field: False for field in FALSE_DEPLOYMENT_FIELDS},
        "publication_fingerprint": _fingerprint(publication_items),
    }
    errors = validate_publication_authorization(publication)
    if errors:
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("; ".join(errors))
    if path.exists():
        existing = _read_json(path)
        if existing == publication:
            return {"ok": True, "status": "idempotent_noop", "publication_path": path.relative_to(root).as_posix(), "publication": publication}
        raise FoodLineOperatorRecoveryPublicationAuthorizationError(
            f"publication authorization already exists with different content: {path.relative_to(root).as_posix()}"
        )
    _assert_clean_source(root)
    _write_json(path, publication)
    return {"ok": True, "status": "publication_authorization_written", "publication_path": path.relative_to(root).as_posix(), "publication": publication}


def validate_publication_authorization_path(root: Path, publication_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = publication_path if publication_path.is_absolute() else root / publication_path
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise FoodLineOperatorRecoveryPublicationAuthorizationError("publication authorization must be a JSON object")
    errors = validate_publication_authorization(payload)
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "publication_path": path.relative_to(root).as_posix()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Food Line operator-recovery publication authorization records.")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--request", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--publication-path", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = create_publication_authorization(args.repo_root, args.request) if args.operation == "create" else validate_publication_authorization_path(args.repo_root, args.publication_path)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
