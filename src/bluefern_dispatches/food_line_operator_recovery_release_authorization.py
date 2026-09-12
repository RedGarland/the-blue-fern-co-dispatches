from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .external_agent_handoff import OPERATOR_RECOVERY_CLASS
from .food_line_operator_recovery_release_prep import (
    PRIVATE_RELEASE_PREP_STATE,
    SCHEMA_VERSION as RELEASE_PREP_SCHEMA_VERSION,
    NONBLOCKING_DUPLICATE_STATES,
    VALID_SOURCE_VERIFICATION_STATES,
)


REQUEST_SCHEMA_VERSION = "bluefern.food_line.operator_recovery_release_authorization_request.v1"
SCHEMA_VERSION = "bluefern.food_line.operator_recovery_release_authorization.v1"
CORRECTION_SCHEMA_VERSION = "bluefern.food_line.operator_recovery_release_prep_correction.v1"
RELEASE_TYPE = "food_line_operator_recovery_release_authorization"
RELEASE_AUTHORIZATION_STATE = "OPERATOR_RECOVERY_RELEASE_AUTHORIZED"
CORRECTION_TYPE = "stale_reference_hash_binding"
RELEASE_ROOT = Path("releases") / "food-line" / "operator-recovery"
RELEASE_PREP_ROOT_PARTS = (
    "data",
    "private-agent-handoff",
    "operator-recovery",
    "food-line",
)
FALSE_AUTHORITY_FIELDS = (
    "publication_authorized",
    "public_generation_authorized",
    "pages_authorized",
    "pages_push_authorized",
    "social_authorized",
    "audio_authorized",
    "schedule_authorized",
    "scheduled_task_change_authorized",
    "archive_rss_homepage_authorized",
    "operational_health_state_authorized",
    "publication_performed",
    "eligible_for_automatic_publication",
    "publication_eligible",
    "publication_approval",
)
REQUIRED_FALSE_PREP_FIELDS = (
    "eligible_for_automatic_publication",
    "publication_eligible",
    "publication_approval",
    "publication_performed",
    "public_generation_authorized",
    "pages_authorized",
    "archive_rss_homepage_authorized",
    "scheduler_authorized",
    "operational_health_state_authorized",
)
OPTIONAL_FALSE_PREP_FIELDS = (
    "publication_authorized",
    "pages_push_authorized",
    "social_authorized",
    "audio_authorized",
    "schedule_authorized",
    "scheduled_task_change_authorized",
)
REQUIRED_PREP_REFS = (
    ("recovery_reconciliation_ref", "recovery_reconciliation_sha256"),
    ("editorial_review_ref", "editorial_review_sha256"),
    ("release_decision_ref", "release_decision_sha256"),
)
CORRECTABLE_PREP_REFS = {
    "editorial_review_ref": (
        "original_stored_editorial_review_sha256",
        "authoritative_editorial_review_ref",
        "authoritative_editorial_review_sha256",
    ),
    "release_decision_ref": (
        "original_stored_release_decision_sha256",
        "authoritative_release_decision_ref",
        "authoritative_release_decision_sha256",
    ),
}


class FoodLineOperatorRecoveryReleaseAuthorizationError(ValueError):
    pass


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def payload_sha256(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload), encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _assert_clean_source(root: Path) -> None:
    dirty = _git(root, "status", "--porcelain")
    if dirty:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "source working tree must be clean before operator-recovery release authorization"
        )


def _safe_slug(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{field} must be a lowercase slug")
    return text


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{field} is required")
    return text


def _repo_relative_path(value: Any, field: str) -> str:
    text = _nonempty(value, field).replace("\\", "/")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{field} must be repository-relative")
    return pure.as_posix()


def _assert_under_repo(root: Path, rel_path: str, field: str) -> Path:
    path = (root / rel_path).resolve()
    if root.resolve() == path or root.resolve() not in path.parents:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{field} resolves outside repository")
    return path


def _assert_committed_at_head(root: Path, rel_path: str) -> None:
    try:
        _git(root, "cat-file", "-e", f"HEAD:{rel_path}")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            f"release-prep artifact must be committed at HEAD: {rel_path}"
        ) from exc


def _assert_present_false(payload: Mapping[str, Any], field: str, label: str) -> None:
    if field not in payload:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{label} {field} must be present and false")
    _assert_false(payload, field, label)


def _assert_false(payload: Mapping[str, Any], field: str, label: str) -> None:
    if payload.get(field) is not False:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{label} {field} must be false")


def _assert_sha(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{field} must be a sha256 digest")
    return text


def _duplicate_is_nonblocking(value: Any) -> bool:
    text = str(value or "").strip()
    return text in NONBLOCKING_DUPLICATE_STATES or text.startswith("NO_DUPLICATE") or text.startswith("nonblocking")


def release_authorization_path_for(release_id: str) -> str:
    return (RELEASE_ROOT / f"{_safe_slug(release_id, 'release_id')}-release-v1.json").as_posix()


def release_prep_correction_path_for(edition_date: str, item_id: str) -> str:
    date_text = _nonempty(edition_date, "edition_date")
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", _nonempty(item_id, "item_id")).strip("-")
    if not safe_id:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("item_id is required")
    return (
        Path(*RELEASE_PREP_ROOT_PARTS)
        / date_text
        / "release-prep-corrections"
        / f"{safe_id}-correction-v1.json"
    ).as_posix()


def _load_correction(root: Path, binding: Mapping[str, Any], prep: Mapping[str, Any], prep_rel: str, prep_sha: str) -> tuple[dict[str, Any], str, str] | None:
    correction_path_value = binding.get("release_prep_correction_path")
    correction_sha_value = binding.get("release_prep_correction_sha256")
    if correction_path_value is None and correction_sha_value is None:
        return None
    if correction_path_value is None or correction_sha_value is None:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("correction path and sha256 must both be supplied")
    rel = _repo_relative_path(correction_path_value, "release_prep_correction_path")
    pure = PurePosixPath(rel)
    if pure.parts[:4] != RELEASE_PREP_ROOT_PARTS or "release-prep-corrections" not in pure.parts:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "release_prep_correction_path must be under data/private-agent-handoff/operator-recovery/food-line"
        )
    _assert_committed_at_head(root, rel)
    path = _assert_under_repo(root, rel, "release_prep_correction_path")
    expected_sha = _assert_sha(correction_sha_value, "release_prep_correction_sha256")
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction hash mismatch")
    correction = _read_json(path)
    if not isinstance(correction, dict):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction must be a JSON object")
    _validate_correction_payload(root, correction, prep, prep_rel, prep_sha)
    return correction, rel, actual_sha


def _validate_correction_payload(root: Path, correction: Mapping[str, Any], prep: Mapping[str, Any], prep_rel: str, prep_sha: str) -> None:
    if correction.get("schema_version") != CORRECTION_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction schema_version is invalid")
    if correction.get("correction_type") != CORRECTION_TYPE:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction_type is invalid")
    if correction.get("item_id") != prep.get("item_id"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction item_id mismatch")
    if correction.get("provenance_class") != OPERATOR_RECOVERY_CLASS or correction.get("provenance_class") != prep.get("provenance_class"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction provenance_class mismatch")
    if correction.get("original_release_prep_path") != prep_rel:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction original path mismatch")
    if correction.get("original_release_prep_sha256") != prep_sha:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction original prep SHA mismatch")
    protected_source_commit = _nonempty(correction.get("protected_source_commit"), "protected_source_commit")
    for rel in (
        prep_rel,
        str(prep.get("editorial_review_ref") or ""),
        str(prep.get("release_decision_ref") or ""),
    ):
        try:
            _git(root, "cat-file", "-e", f"{protected_source_commit}:{rel}")
        except (OSError, subprocess.CalledProcessError) as exc:
            raise FoodLineOperatorRecoveryReleaseAuthorizationError(
                "release-prep correction protected_source_commit does not contain referenced artifacts"
            ) from exc
    _nonempty(correction.get("correction_reason"), "correction_reason")
    _nonempty(correction.get("corrected_at"), "corrected_at")
    datetime.fromisoformat(str(correction["corrected_at"]).replace("Z", "+00:00"))
    for field in (
        "original_production_artifact_present",
        "eligible_for_automatic_publication",
        "publication_eligible",
        "publication_approval",
        "publication_performed",
    ):
        _assert_false(correction, field, "release-prep correction")
    if correction.get("production_collection_failed_before_discovery") is not True:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "release-prep correction production_collection_failed_before_discovery must be true"
        )
    if correction.get("release_preparation_eligible") is not True:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction release_preparation_eligible must be true")
    for field in (
        "publication_authorized",
        "public_generation_authorized",
        "pages_authorized",
        "pages_push_authorized",
        "social_authorized",
        "audio_authorized",
        "schedule_authorized",
        "scheduled_task_change_authorized",
    ):
        if field in correction:
            _assert_false(correction, field, "release-prep correction")
    reviewer = prep.get("reviewer_metadata") if isinstance(prep.get("reviewer_metadata"), Mapping) else {}
    immutable_fields = (
        "source_url",
        "publisher",
        "event_date",
        "geography",
        "duplicate_disposition",
        "approved_headline",
        "approved_summary",
        "release_preparation_eligible",
    )
    for field in immutable_fields:
        if correction.get(field) != prep.get(field):
            raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"release-prep correction changed {field}")
    if correction.get("editorial_disposition") != reviewer.get("editorial_disposition"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction changed editorial_disposition")
    if correction.get("original_stored_editorial_review_sha256") != prep.get("editorial_review_sha256"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction prior editorial hash mismatch")
    if correction.get("original_stored_release_decision_sha256") != prep.get("release_decision_sha256"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction prior release-decision hash mismatch")
    if correction.get("authoritative_editorial_review_ref") != prep.get("editorial_review_ref"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction editorial ref mismatch")
    if correction.get("authoritative_release_decision_ref") != prep.get("release_decision_ref"):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep correction release-decision ref mismatch")
    for _, ref_field, current_hash_field in CORRECTABLE_PREP_REFS.values():
        rel = _repo_relative_path(correction.get(ref_field), ref_field)
        actual = sha256_file(_assert_under_repo(root, rel, ref_field))
        if correction.get(current_hash_field) != actual:
            raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"release-prep correction {current_hash_field} mismatch")


def _validate_bound_ref(
    root: Path,
    prep: Mapping[str, Any],
    ref_field: str,
    hash_field: str,
    correction: Mapping[str, Any] | None,
) -> tuple[str, str | None]:
    rel = _repo_relative_path(prep.get(ref_field), ref_field)
    expected = _assert_sha(prep.get(hash_field), hash_field)
    path = _assert_under_repo(root, rel, ref_field)
    if not path.exists():
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{ref_field} is missing: {rel}")
    actual = sha256_file(path)
    if actual == expected:
        return expected, None
    if ref_field not in CORRECTABLE_PREP_REFS or correction is None:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{ref_field} hash mismatch")
    prior_field, correction_ref_field, correction_hash_field = CORRECTABLE_PREP_REFS[ref_field]
    if correction.get(prior_field) != expected:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{ref_field} correction prior hash mismatch")
    if correction.get(correction_ref_field) != rel:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{ref_field} correction ref mismatch")
    if correction.get(correction_hash_field) != actual:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(f"{ref_field} correction current hash mismatch")
    return actual, correction_hash_field


def _validate_release_prep(
    root: Path,
    prep: Mapping[str, Any],
    *,
    expected_item_id: str,
    correction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if prep.get("schema_version") != RELEASE_PREP_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep schema_version is invalid")
    item_id = _nonempty(prep.get("item_id"), "item_id")
    if item_id != expected_item_id:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep item_id does not match binding")
    if prep.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "provenance_class must be operator_recovered_source_watch_evidence"
        )
    if prep.get("release_preparation_state") != PRIVATE_RELEASE_PREP_STATE:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "release-prep artifact is not OPERATOR_RECOVERY_RELEASE_PREP_ELIGIBLE"
        )
    if prep.get("release_preparation_eligible") is not True:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release_preparation_eligible must be true")
    if prep.get("original_production_artifact_present") is not False:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("original_production_artifact_present must be false")
    if prep.get("production_collection_failed_before_discovery") is not True:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "production_collection_failed_before_discovery must be true"
        )
    if prep.get("synthetic_evidence_used") is True:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("synthetic evidence cannot be release-authorized")
    reviewer = prep.get("reviewer_metadata") if isinstance(prep.get("reviewer_metadata"), Mapping) else {}
    if reviewer.get("editorial_disposition") in {"HOLD", "REJECT", "hold", "reject"}:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("HOLD and REJECT items cannot be release-authorized")
    if prep.get("source_verification") not in VALID_SOURCE_VERIFICATION_STATES:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("source verification is not valid")
    if not _duplicate_is_nonblocking(prep.get("duplicate_disposition")):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("duplicate state must be resolved and nonblocking")
    for field in REQUIRED_FALSE_PREP_FIELDS:
        _assert_present_false(prep, field, "release-prep")
    for field in OPTIONAL_FALSE_PREP_FIELDS:
        if field in prep:
            _assert_false(prep, field, "release-prep")
    corrected_hashes: dict[str, str] = {}
    corrected_by: dict[str, str] = {}
    for ref_field, hash_field in REQUIRED_PREP_REFS:
        valid_hash, correction_hash_field = _validate_bound_ref(root, prep, ref_field, hash_field, correction)
        corrected_hashes[hash_field] = valid_hash
        if correction_hash_field:
            corrected_by[hash_field] = correction_hash_field
    if correction is not None and not corrected_by:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("correction overlay supplied without stale reference hash mismatch")
    required_fields = (
        "original_agent_run_id",
        "source_url",
        "publisher",
        "event_date",
        "geography",
        "approved_headline",
        "approved_summary",
    )
    return {field: _nonempty(prep.get(field), field) for field in required_fields} | {
        "item_id": item_id,
        "reviewer_metadata": reviewer,
        "validated_reference_hashes": corrected_hashes,
        "corrected_reference_hashes": corrected_by,
    }


def _load_release_prep(root: Path, binding: Mapping[str, Any]) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    item_id = _nonempty(binding.get("item_id"), "binding item_id")
    rel = _repo_relative_path(binding.get("release_prep_path"), "release_prep_path")
    pure = PurePosixPath(rel)
    if pure.parts[:4] != RELEASE_PREP_ROOT_PARTS or "release-prep" not in pure.parts:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "release_prep_path must be under data/private-agent-handoff/operator-recovery/food-line"
        )
    _assert_committed_at_head(root, rel)
    path = _assert_under_repo(root, rel, "release_prep_path")
    expected_sha = _assert_sha(binding.get("release_prep_sha256"), "release_prep_sha256")
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep hash mismatch")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep artifact must be a JSON object")
    correction_record = _load_correction(root, binding, payload, rel, actual_sha)
    correction = correction_record[0] if correction_record is not None else None
    normalized = _validate_release_prep(root, payload, expected_item_id=item_id, correction=correction)
    if correction_record is not None:
        normalized["release_prep_correction_path"] = correction_record[1]
        normalized["release_prep_correction_sha256"] = correction_record[2]
    return payload, rel, actual_sha, normalized


def _validate_hold_exclusion(root: Path, request: Mapping[str, Any], selected_ids: set[str]) -> dict[str, Any]:
    hold = request.get("hold_item_exclusion")
    if not isinstance(hold, Mapping):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("hold_item_exclusion is required")
    excluded_ids = hold.get("excluded_item_ids")
    if not isinstance(excluded_ids, list) or not excluded_ids:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("hold_item_exclusion.excluded_item_ids are required")
    overlap = selected_ids.intersection(str(item).strip() for item in excluded_ids)
    if overlap:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("HOLD item is included in release authorization")
    if hold.get("release_prep_artifact_present") is not False:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("HOLD item must have no release-prep artifact")
    for item_id in excluded_ids:
        default_path = root / RELEASE_PREP_ROOT_PARTS[0] / RELEASE_PREP_ROOT_PARTS[1] / RELEASE_PREP_ROOT_PARTS[2]
        default_path = default_path / RELEASE_PREP_ROOT_PARTS[3] / "2026-09-10" / "release-prep" / f"{item_id}.json"
        if default_path.exists():
            raise FoodLineOperatorRecoveryReleaseAuthorizationError(
                f"HOLD item release-prep artifact exists: {default_path.relative_to(root).as_posix()}"
            )
    return {
        "excluded_item_ids": [str(item).strip() for item in excluded_ids],
        "reason": _nonempty(hold.get("reason"), "hold exclusion reason"),
        "release_prep_artifact_present": False,
    }


def _release_item(prep: Mapping[str, Any], rel: str, prep_sha: str, normalized: Mapping[str, Any]) -> dict[str, Any]:
    item = {
        "item_id": normalized["item_id"],
        "release_decision": RELEASE_AUTHORIZATION_STATE,
        "release_authorized": True,
        "release_prep_path": rel,
        "release_prep_sha256": prep_sha,
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "original_agent_run_id": normalized["original_agent_run_id"],
        "source_url": normalized["source_url"],
        "publisher": normalized["publisher"],
        "source_verification": prep["source_verification"],
        "event_date": normalized["event_date"],
        "geography": normalized["geography"],
        "duplicate_disposition": prep["duplicate_disposition"],
        "reviewed_headline": normalized["approved_headline"],
        "reviewed_summary": normalized["approved_summary"],
        "recovery_reconciliation_ref": prep["recovery_reconciliation_ref"],
        "recovery_reconciliation_sha256": normalized["validated_reference_hashes"]["recovery_reconciliation_sha256"],
        "editorial_review_ref": prep["editorial_review_ref"],
        "editorial_review_sha256": normalized["validated_reference_hashes"]["editorial_review_sha256"],
        "release_decision_ref": prep["release_decision_ref"],
        "release_decision_sha256": normalized["validated_reference_hashes"]["release_decision_sha256"],
        "original_release_prep_stored_editorial_review_sha256": prep["editorial_review_sha256"],
        "original_release_prep_stored_release_decision_sha256": prep["release_decision_sha256"],
        "corrected_reference_hashes": normalized["corrected_reference_hashes"],
        "failed_production_lineage_preserved": True,
        "original_source_watch_lineage_fabricated": False,
        **{field: False for field in FALSE_AUTHORITY_FIELDS},
    }
    if "release_prep_correction_path" in normalized:
        item["release_prep_correction_path"] = normalized["release_prep_correction_path"]
        item["release_prep_correction_sha256"] = normalized["release_prep_correction_sha256"]
    return item


def validate_release_authorization(payload: Mapping[str, Any], *, expected_release_id: str | None = None) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version is invalid")
    if payload.get("release_type") != RELEASE_TYPE:
        errors.append("release_type is invalid")
    if payload.get("dispatch") != "food-line":
        errors.append("dispatch must be food-line")
    if expected_release_id is not None and payload.get("release_id") != expected_release_id:
        errors.append("release_id does not match expected release")
    if payload.get("release_authorization_state") != RELEASE_AUTHORIZATION_STATE:
        errors.append("release_authorization_state is invalid")
    if payload.get("release_authorized") is not True:
        errors.append("release_authorized must be true")
    for field in FALSE_AUTHORITY_FIELDS:
        if payload.get(field) is not False:
            errors.append(f"{field} must be false")
    items = payload.get("release_items")
    if not isinstance(items, list) or not items:
        errors.append("release_items are required")
        return errors
    if payload.get("item_count") != len(items):
        errors.append("item_count must match release_items")
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            errors.append(f"release item {index} must be an object")
            continue
        item_id = str(item.get("item_id") or "")
        if not item_id:
            errors.append(f"release item {index} item_id missing")
        elif item_id in seen:
            errors.append("duplicate release item IDs")
        seen.add(item_id)
        if item.get("release_decision") != RELEASE_AUTHORIZATION_STATE or item.get("release_authorized") is not True:
            errors.append(f"release item {index} release decision is invalid")
        if item.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
            errors.append(f"release item {index} provenance_class is invalid")
        if item.get("original_production_artifact_present") is not False:
            errors.append(f"release item {index} original production artifact flag must be false")
        if item.get("production_collection_failed_before_discovery") is not True:
            errors.append(f"release item {index} failed-before-discovery flag must be true")
        for field in FALSE_AUTHORITY_FIELDS:
            if item.get(field) is not False:
                errors.append(f"release item {index} {field} must be false")
        for field in (
            "release_prep_path",
            "release_prep_sha256",
            "original_agent_run_id",
            "source_url",
            "publisher",
            "source_verification",
            "event_date",
            "geography",
            "duplicate_disposition",
            "reviewed_headline",
            "reviewed_summary",
            "recovery_reconciliation_ref",
            "recovery_reconciliation_sha256",
            "editorial_review_ref",
            "editorial_review_sha256",
            "release_decision_ref",
            "release_decision_sha256",
        ):
            if not str(item.get(field) or "").strip():
                errors.append(f"release item {index} {field} missing")
    hold = payload.get("hold_item_exclusion")
    if not isinstance(hold, Mapping) or hold.get("release_prep_artifact_present") is not False:
        errors.append("hold_item_exclusion must preserve absent release-prep artifact")
    return errors


def create_release_authorization(root: Path, request_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    request_absolute = request_path.resolve(strict=True)
    if root == request_absolute or root in request_absolute.parents:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            "release-authorization request must remain private and outside the repository"
        )
    request = _read_json(request_absolute)
    if not isinstance(request, Mapping) or request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-authorization request schema_version is invalid")
    release_id = _safe_slug(request.get("release_id"), "release_id")
    authorized_by = _nonempty(request.get("authorized_by"), "authorized_by")
    authorized_at = _nonempty(request.get("authorized_at"), "authorized_at")
    datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    source_base_commit = _nonempty(request.get("source_base_commit"), "source_base_commit")
    if _git(root, "rev-parse", "HEAD") != source_base_commit:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("source_base_commit must match current HEAD")
    bindings = request.get("release_prep_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release_prep_bindings are required")
    release_items: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise FoodLineOperatorRecoveryReleaseAuthorizationError("release-prep binding must be an object")
        prep, rel, prep_sha, normalized = _load_release_prep(root, binding)
        item_id = normalized["item_id"]
        if item_id in selected_ids:
            raise FoodLineOperatorRecoveryReleaseAuthorizationError("duplicate release-prep item IDs")
        selected_ids.add(item_id)
        release_items.append(_release_item(prep, rel, prep_sha, normalized))
    if request.get("expected_item_count") != len(release_items):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("expected_item_count does not match release items")
    hold_exclusion = _validate_hold_exclusion(root, request, selected_ids)
    release = {
        "schema_version": SCHEMA_VERSION,
        "release_type": RELEASE_TYPE,
        "dispatch": "food-line",
        "release_id": release_id,
        "release_authorization_state": RELEASE_AUTHORIZATION_STATE,
        "authorized_by": authorized_by,
        "authorized_at": authorized_at,
        "source_base_commit": source_base_commit,
        "item_count": len(release_items),
        "release_items": release_items,
        "hold_item_exclusion": hold_exclusion,
        "september_10_original_production_runtime_remains_failed": True,
        "original_source_watch_lineage_fabricated": False,
        "release_authorized": True,
        **{field: False for field in FALSE_AUTHORITY_FIELDS},
        "release_fingerprint": payload_sha256(release_items),
    }
    errors = validate_release_authorization(release, expected_release_id=release_id)
    if errors:
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("; ".join(errors))
    path = root / release_authorization_path_for(release_id)
    if path.exists():
        existing = _read_json(path)
        if existing == release:
            return {
                "ok": True,
                "status": "idempotent_noop",
                "release_authorization_path": path.relative_to(root).as_posix(),
                "release_authorization_sha256": sha256_file(path),
                "release_authorization": release,
            }
        raise FoodLineOperatorRecoveryReleaseAuthorizationError(
            f"release authorization already exists with different content: {path.relative_to(root).as_posix()}"
        )
    _assert_clean_source(root)
    _write_json(path, release)
    return {
        "ok": True,
        "status": "release_authorization_written",
        "release_authorization_path": path.relative_to(root).as_posix(),
        "release_authorization_sha256": payload_sha256(release),
        "release_authorization": release,
    }


def validate_release_authorization_path(
    root: Path,
    release_authorization_path: Path,
    *,
    expected_release_id: str | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    path = release_authorization_path if release_authorization_path.is_absolute() else root / release_authorization_path
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise FoodLineOperatorRecoveryReleaseAuthorizationError("release authorization must be a JSON object")
    errors = validate_release_authorization(payload, expected_release_id=expected_release_id)
    return {
        "ok": not errors,
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "release_authorization_path": path.relative_to(root).as_posix(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Food Line operator-recovery release authorization records.")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--request", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--release-authorization-path", type=Path, required=True)
    validate.add_argument("--release-id")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "create":
            result = create_release_authorization(args.repo_root, args.request)
        else:
            result = validate_release_authorization_path(
                args.repo_root,
                args.release_authorization_path,
                expected_release_id=args.release_id,
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
