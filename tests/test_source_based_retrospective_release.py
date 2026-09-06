from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.source_based_retrospective_approval import (
    FOOD_APPROVAL_SCHEMA,
)
from bluefern_dispatches.source_based_retrospective_release import (
    CARE_RELEASE_SCHEMA,
    FOOD_RELEASE_SCHEMA,
    REQUEST_SCHEMA,
    SourceBasedRetrospectiveReleaseError,
    create_release,
    release_path_for,
    validate_release,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


def _approval_item(dispatch: str, suffix: str, *, source_identifier: str | None = None) -> dict:
    snapshot = {
        "date_or_event_date": "2026-08-17",
        "source_evidence_basis": "The source documents a service interruption.",
    }
    source_id = source_identifier or f"{dispatch}-finding-{suffix}"
    item = {
        "approval_item_id": f"{dispatch}-approval-item-{suffix}",
        "approval_state": "approved_for_retrospective_editorial_use",
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": False,
        "approved_for_publication": False,
        "dispatch": dispatch,
        "source_identifier": source_id,
        "source_url": f"https://example.test/{dispatch}/{suffix}",
        "publisher": "Example Publisher",
        "retrospective_finding_id": source_id,
        "retrospective_lineage_identifier": source_id,
        "approval_prep_decision_id": f"recommended:{source_id}",
        "approval_prep_item_sha256": _fingerprint(snapshot),
        "event_or_effective_date": "2026-08-17",
        "location": "Example",
        "state_or_territory": "IA",
        "pressure_or_service_type": "service_closure",
        "approval_rationale": "Source-backed access strain.",
        "uncertainty_or_wording_constraint": "Keep wording limited to the source.",
        "duplicate_lineage": "",
        "source_evidence_sha256": _fingerprint(
            {
                "source_identifier": source_id,
                "source_url": f"https://example.test/{dispatch}/{suffix}",
                "publisher": "Example Publisher",
                "evidence": "The source documents a service interruption.",
            }
        ),
        "bounded_item_snapshot": snapshot,
    }
    return item


def _approval(repo: Path, dispatch: str, batch_id: str, count: int, *, alabama: bool = False) -> Path:
    item_prefix = batch_id.removesuffix("-approval-v1").removesuffix("-approval")
    items = [_approval_item(dispatch, f"{item_prefix}-{index}") for index in range(count)]
    if alabama:
        items.append(_approval_item(dispatch, "alabama", source_identifier="food-line-discovery-b97a3e603001d8ba"))
    schema = FOOD_APPROVAL_SCHEMA if dispatch == "food-line" else "care_line_source_based_retrospective_approval_v1"
    payload = {
        "schema_version": schema,
        "approval_type": "source_based_retrospective_editorial_approval",
        "dispatch": dispatch,
        "batch_id": batch_id,
        "approved_by": "William Patton",
        "approved_at": "2026-09-06T12:00:00Z",
        "source_base_commit": _git(repo, "rev-parse", "HEAD"),
        "approval_prep_artifact": {"path": "private.json", "sha256": "sha256:" + "1" * 64, "item_count": count, "approved_item_count": count},
        "approved_items": items,
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
        "approval_fingerprint": _fingerprint(items),
    }
    path = repo / "approvals" / dispatch / "source-based-retrospectives" / f"{batch_id}-approval-v1.json"
    _write_json(path, payload)
    _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-m", f"add {dispatch} approval {batch_id}")
    return path.relative_to(repo)


def _readiness(repo: Path, approval_paths: list[Path], dispatch: str) -> Path:
    rows = []
    for path in approval_paths:
        approval = json.loads((repo / path).read_text(encoding="utf-8"))
        for item in approval["approved_items"]:
            rows.append(
                {
                    "dispatch": dispatch,
                    "approval_batch_id": approval["batch_id"],
                    "approval_item_id": item["approval_item_id"],
                    "source_identifier": item["source_identifier"],
                    "release_readiness_state": "ready_for_release_authorization",
                    "date_binding_classification": "august_event",
                    "recommended_public_edition_event_date": item["event_or_effective_date"],
                    "wording_constraints": item["uncertainty_or_wording_constraint"],
                    "duplicate_relationship": item["duplicate_lineage"],
                    "lineage_validation_status": "valid",
                }
            )
    path = repo.parent / f"{dispatch}-readiness.json"
    _write_json(
        path,
        {
            "summary": {"schema_version": "bluefern.source_based_retrospective_release_readiness_review.v1"},
            "items": rows,
        },
    )
    return path


def _request(repo: Path, dispatch: str, approval_paths: list[Path], readiness: Path, *, count: int, release_id: str = "august-release-01") -> Path:
    path = repo.parent / f"{dispatch}-release-request.json"
    _write_json(
        path,
        {
            "schema_version": REQUEST_SCHEMA,
            "dispatch": dispatch,
            "release_batch_id": release_id,
            "authorized_by": "William Patton",
            "authorized_at": "2026-09-06T13:00:00Z",
            "source_base_commit": _git(repo, "rev-parse", "HEAD"),
            "retrospective_coverage_month": "2026-08",
            "release_readiness_review_path": str(readiness),
            "release_readiness_review_sha256": _sha(readiness),
            "expected_item_count": count,
            "approval_bindings": [
                {"approval_path": path.as_posix(), "approval_sha256": _sha(repo / path)}
                for path in approval_paths
            ],
        },
    )
    return path


def test_valid_approval_to_valid_release_authorization(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = _approval(repo, "food-line", "food-line-august-2026-source-based-01", 2)
    readiness = _readiness(repo, [approval], "food-line")

    result = create_release(repo, _request(repo, "food-line", [approval], readiness, count=2))

    assert result["status"] == "release_written"
    assert result["release"]["schema_version"] == FOOD_RELEASE_SCHEMA
    assert result["release"]["release_authorized"] is True
    assert result["release"]["publication_authorized"] is False
    assert not (repo / "output" / "site").exists()


def test_approval_hash_mismatch_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = _approval(repo, "food-line", "food-line-august-2026-source-based-01", 1)
    readiness = _readiness(repo, [approval], "food-line")
    request = _request(repo, "food-line", [approval], readiness, count=1)
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["approval_bindings"][0]["approval_sha256"] = "sha256:" + "0" * 64
    _write_json(request, payload)

    with pytest.raises(SourceBasedRetrospectiveReleaseError, match="approval hash mismatch"):
        create_release(repo, request)


def test_readiness_hash_mismatch_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = _approval(repo, "food-line", "food-line-august-2026-source-based-01", 1)
    readiness = _readiness(repo, [approval], "food-line")
    request = _request(repo, "food-line", [approval], readiness, count=1)
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["release_readiness_review_sha256"] = "sha256:" + "0" * 64
    _write_json(request, payload)

    with pytest.raises(SourceBasedRetrospectiveReleaseError, match="readiness hash mismatch"):
        create_release(repo, request)


def test_dirty_source_fails_closed_before_release_record(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = _approval(repo, "food-line", "food-line-august-2026-source-based-01", 1)
    readiness = _readiness(repo, [approval], "food-line")
    request = _request(repo, "food-line", [approval], readiness, count=1)
    (repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(SourceBasedRetrospectiveReleaseError, match="working tree must be clean"):
        create_release(repo, request)


def test_missing_approval_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    missing = Path("approvals/food-line/source-based-retrospectives/missing-approval-v1.json")
    readiness = _readiness(repo, [], "food-line")

    with pytest.raises(FileNotFoundError):
        create_release(repo, _request(repo, "food-line", [missing], readiness, count=0))


def test_duplicate_id_and_mixed_dispatch_fail_validation() -> None:
    item = _release_item("food-line-approval-item-1", "food-line")
    duplicate = dict(item)
    payload = _release("food-line", [item, duplicate])
    errors = validate_release(payload, expected_dispatch="food-line")
    assert "duplicate release item IDs" in errors

    mixed = _release("care-line", [_release_item("x", "food-line")])
    assert "release item 1 dispatch does not match release dispatch" in validate_release(mixed, expected_dispatch="care-line")


def test_publication_authority_enabled_fails() -> None:
    payload = _release("care-line", [_release_item("care-line-approval-item-1", "care-line")])
    payload["publication_authorized"] = True

    assert "release publication_authorized must be false" in validate_release(payload, expected_dispatch="care-line")


def test_food_three_approved_august_batches_and_alabama_excluded(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approvals = [
        _approval(repo, "food-line", "food-line-august-2026-source-based-01", 6),
        _approval(repo, "food-line", "food-line-august-2026-source-based-02", 6),
        _approval(repo, "food-line", "food-line-august-2026-source-based-03", 4),
    ]
    readiness = _readiness(repo, approvals, "food-line")

    result = create_release(repo, _request(repo, "food-line", approvals, readiness, count=16, release_id="food-line-august-2026-source-based-release"))

    assert result["release"]["item_count"] == 16
    assert all("b97a3e603001d8ba" not in item["source_identifier"] for item in result["release"]["release_items"])


def test_care_four_records_chronology_and_no_queue_publication_handoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = _approval(repo, "care-line", "care-line-august-2026-source-based-01", 4)
    readiness = _readiness(repo, [approval], "care-line")
    payload = json.loads(readiness.read_text(encoding="utf-8"))
    payload["items"][0]["date_binding_classification"] = "august_restoration"
    payload["items"][0]["recommended_public_edition_event_date"] = "2026-09-01"
    payload["items"][0]["wording_constraints"] = "Frame as restoration, not a new August closure."
    payload["items"][3]["date_binding_classification"] = "august_reporting_on_continuing_prior_loss"
    payload["items"][3]["recommended_public_edition_event_date"] = "2026-03-14 continuing; August 27 public record"
    payload["items"][3]["wording_constraints"] = "Do not portray August 27 as the original closure date."
    _write_json(readiness, payload)

    result = create_release(repo, _request(repo, "care-line", [approval], readiness, count=4, release_id="care-line-august-2026-source-based-release"))

    assert result["release"]["schema_version"] == CARE_RELEASE_SCHEMA
    assert {item["date_binding_classification"] for item in result["release"]["release_items"]} >= {
        "august_restoration",
        "august_reporting_on_continuing_prior_loss",
    }
    assert result["release"]["publication_authorized"] is False
    assert not (repo / "data" / "dispatches" / "care-line" / "queue").exists()


def _release_item(item_id: str, dispatch: str) -> dict:
    return {
        "approval_item_id": item_id,
        "dispatch": dispatch,
        "source_identifier": "source",
        "retrospective_finding_id": "finding",
        "release_decision": "release_authorized",
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "date_binding_classification": "august_event",
        "recommended_public_date_binding": "2026-08-01",
        "wording_constraints": "Use bounded wording.",
        "duplicate_status": "distinct",
        "lineage_validation_status": "valid",
        "approval_item_sha256": "sha256:" + "1" * 64,
        "readiness_item_sha256": "sha256:" + "2" * 64,
    }


def _release(dispatch: str, items: list[dict]) -> dict:
    return {
        "schema_version": FOOD_RELEASE_SCHEMA if dispatch == "food-line" else CARE_RELEASE_SCHEMA,
        "release_type": "source_based_retrospective_release_authorization",
        "dispatch": dispatch,
        "release_batch_id": "release-1",
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "item_count": len(items),
        "release_items": items,
    }
