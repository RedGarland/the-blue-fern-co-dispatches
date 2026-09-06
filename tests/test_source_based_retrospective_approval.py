from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.source_based_retrospective_approval import (
    CARE_APPROVAL_SCHEMA,
    FOOD_APPROVAL_SCHEMA,
    MAX_ITEMS,
    REQUEST_SCHEMA,
    SourceBasedRetrospectiveApprovalError,
    approval_path_for,
    create_approval,
    validate_approval,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _food_item(identifier: str = "food-line-history-finding-abc123") -> dict:
    return {
        "approval_preparation_outcome": "recommended_for_approval",
        "approval_preparation_reason": "The source documents a multi-day pantry closure with no replacement capacity identified.",
        "currentness_freshness_basis": "newly_effective",
        "date_or_event_date": "2026-08-13",
        "dispatch": "food-line",
        "duplicate_cluster_or_linkage": "",
        "location": "Example Food Pantry",
        "original_retrospective_finding_id": identifier,
        "pressure_type": "food_pantry_multi_day_service_closure",
        "prior_retrospective_disposition": "retained_for_review",
        "prior_triage_decision": "approve_for_retrospective_editorial_review",
        "publisher": "Example Pantry",
        "significance": "medium",
        "source_evidence_basis": "The pantry will be closed August 13 through August 24.",
        "source_strength": "strong_primary",
        "source_url": "https://example.test/food-pantry",
        "state": "IOWA",
        "title": "Example pantry closure",
        "uncertainty": "The source does not quantify missed visits.",
    }


def _care_item(identifier: str = "care-line-retrospective-finding-abc123") -> dict:
    return {
        "approval_preparation_outcome": "recommended_for_approval",
        "approval_preparation_reason": "The source documents a maternity service shutdown with access consequences.",
        "date_or_event_date": "2026-08-22",
        "dispatch": "care-line",
        "duplicate_cluster_or_linkage": "",
        "location": "Example Hospital",
        "original_retrospective_finding_id": identifier,
        "pressure_type": "labor_and_delivery_service_shutdown",
        "publisher": "Example News",
        "source_evidence_basis": "The hospital will end labor and delivery services.",
        "source_strength": "strong_secondary",
        "source_url": "https://example.test/hospital",
        "state": "IA",
        "title": "Hospital ends maternity care",
        "uncertainty": "Do not characterize this as an emergency department closure.",
    }


def _request(repo: Path, prep: Path, *, dispatch: str, ids: list[str], batch_id: str = "august-2026-batch-01") -> Path:
    request = repo.parent / f"{dispatch}-request.json"
    _write_json(
        request,
        {
            "schema_version": REQUEST_SCHEMA,
            "dispatch": dispatch,
            "batch_id": batch_id,
            "approved_by": "William Patton",
            "approved_at": "2026-09-06T12:00:00Z",
            "source_base_commit": _git(repo, "rev-parse", "HEAD"),
            "approval_prep_artifact_path": str(prep),
            "approval_prep_artifact_sha256": "sha256:" + _sha256(prep),
            "approved_item_source_identifiers": ids,
        },
    )
    return request


def test_food_approval_prep_item_can_be_bound_without_fake_recovery_decision(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})

    result = create_approval(repo, _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"]))

    assert result["status"] == "approval_written"
    approval_path = repo / approval_path_for("food-line", "august-2026-batch-01")
    assert approval_path.exists()
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["schema_version"] == FOOD_APPROVAL_SCHEMA
    assert approval["approved_for_retrospective_editorial_use"] is True
    assert approval["approved_for_release"] is False
    assert approval["approved_for_publication"] is False
    assert approval["pages_authorized"] is False
    assert not (repo / "output" / "site").exists()


def test_care_valid_retrospective_approval_item_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "care-prep.json"
    _write_json(prep, {"items": [_care_item()]})

    result = create_approval(repo, _request(repo, prep, dispatch="care-line", ids=["care-line-retrospective-finding-abc123"]))

    approval = result["approval"]
    assert approval["schema_version"] == CARE_APPROVAL_SCHEMA
    assert approval["dispatch"] == "care-line"
    assert validate_approval(approval, expected_dispatch="care-line") == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("source_url", "source URL missing"),
        ("original_retrospective_finding_id", "source identifier missing"),
    ],
)
def test_food_requires_source_and_retrospective_lineage(tmp_path: Path, field: str, message: str) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _food_item()
    item[field] = ""
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [item]})

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match=message):
        create_approval(repo, _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"]))


def test_six_story_limit_preserved_and_seven_records_fail(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    ids = [f"food-line-history-finding-{index:02d}" for index in range(MAX_ITEMS + 1)]
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item(identifier) for identifier in ids]})

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match="one through 6"):
        create_approval(repo, _request(repo, prep, dispatch="food-line", ids=ids))


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match="unique"):
        create_approval(
            repo,
            _request(
                repo,
                prep,
                dispatch="food-line",
                ids=["food-line-history-finding-abc123", "food-line-history-finding-abc123"],
            ),
        )


def test_publication_release_flags_cannot_be_implied() -> None:
    approval = {
        "schema_version": CARE_APPROVAL_SCHEMA,
        "approval_type": "source_based_retrospective_editorial_approval",
        "dispatch": "care-line",
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": True,
        "approved_for_publication": False,
        "release_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "scheduled_task_change_authorized": False,
        "approved_items": [_minimal_approval_item("care-line")],
    }

    assert "approved_for_release must be false" in validate_approval(approval, expected_dispatch="care-line")


def test_care_missing_source_url_and_lineage_fail() -> None:
    item = _minimal_approval_item("care-line")
    item["source_url"] = ""
    item["retrospective_finding_id"] = ""
    approval = _minimal_approval("care-line", [item])

    errors = validate_approval(approval, expected_dispatch="care-line")

    assert any("source_url missing" in error for error in errors)
    assert any("retrospective_finding_id missing" in error for error in errors)


def test_mixed_dispatch_batch_fails() -> None:
    approval = _minimal_approval("care-line", [_minimal_approval_item("food-line")])

    assert "approved item 1 dispatch mismatch" in validate_approval(approval, expected_dispatch="care-line")


def test_durable_lineage_remains_after_original_prep_file_disappears(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})
    result = create_approval(repo, _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"]))
    prep.unlink()

    approval = result["approval"]

    assert validate_approval(approval, expected_dispatch="food-line") == []
    assert approval["approved_items"][0]["bounded_item_snapshot"]["source_url"] == "https://example.test/food-pantry"


def test_input_hash_mismatch_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})
    request = _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"])
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["approval_prep_artifact_sha256"] = "sha256:" + "0" * 64
    _write_json(request, payload)

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match="hash mismatch"):
        create_approval(repo, request)


def test_source_evidence_hash_mismatch_fails_validation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})
    result = create_approval(repo, _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"]))
    approval = result["approval"]
    approval["approved_items"][0]["source_url"] = "https://example.test/changed"

    errors = validate_approval(approval, expected_dispatch="food-line")

    assert any("source evidence hash mismatch" in error for error in errors)


def test_malformed_edited_prep_input_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"not_items": []})

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match="items array"):
        create_approval(repo, _request(repo, prep, dispatch="food-line", ids=["food-line-history-finding-abc123"]))


def test_exact_dispatch_binding_enforced(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    prep = tmp_path / "food-prep.json"
    _write_json(prep, {"items": [_food_item()]})

    with pytest.raises(SourceBasedRetrospectiveApprovalError, match="dispatch mismatch"):
        create_approval(repo, _request(repo, prep, dispatch="care-line", ids=["food-line-history-finding-abc123"]))


def _minimal_approval(dispatch: str, items: list[dict]) -> dict:
    return {
        "schema_version": FOOD_APPROVAL_SCHEMA if dispatch == "food-line" else CARE_APPROVAL_SCHEMA,
        "approval_type": "source_based_retrospective_editorial_approval",
        "dispatch": dispatch,
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": False,
        "approved_for_publication": False,
        "release_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "scheduled_task_change_authorized": False,
        "approved_items": items,
    }


def _minimal_approval_item(dispatch: str) -> dict:
    return {
        "approval_item_id": f"{dispatch}-item-1",
        "approval_state": "approved_for_retrospective_editorial_use",
        "approved_for_retrospective_editorial_use": True,
        "approved_for_release": False,
        "approved_for_publication": False,
        "dispatch": dispatch,
        "source_identifier": "source-1",
        "source_url": "https://example.test/source",
        "retrospective_finding_id": "finding-1",
        "retrospective_lineage_identifier": "finding-1",
        "approval_prep_decision_id": "decision-1",
        "event_or_effective_date": "2026-08-01",
        "approval_rationale": "Source-backed access strain.",
        "approval_prep_item_sha256": "sha256:" + "1" * 64,
        "source_evidence_sha256": "sha256:" + "2" * 64,
    }
