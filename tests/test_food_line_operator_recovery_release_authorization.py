from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.external_agent_handoff import OPERATOR_RECOVERY_CLASS
from bluefern_dispatches.food_line_operator_recovery_release_authorization import (
    FoodLineOperatorRecoveryReleaseAuthorizationError,
    REQUEST_SCHEMA_VERSION,
    create_release_authorization,
    release_authorization_path_for,
    validate_release_authorization,
)
from bluefern_dispatches.food_line_operator_recovery_release_prep import (
    PRIVATE_RELEASE_PREP_STATE,
    SCHEMA_VERSION as PREP_SCHEMA_VERSION,
    sha256_file,
)
from bluefern_dispatches.source_based_retrospective_public_generation import (
    SourceBasedRetrospectivePublicGenerationError,
    generate_public_artifacts,
)


ITEM_IDS = [
    "finding_0ca835b632814684d8f1f2ec",
    "finding_5a56d5dbd54a3ceca06f44d6",
    "finding_4341336d9c9a9cb00b9543ca",
    "finding_ba095f74fbb914bc56aeb120",
]
HOLD_ITEM_ID = "finding_1cae6cc95cacbe13fa5eb7f8"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "Tests")


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)


def _prep_path(root: Path, item_id: str) -> Path:
    return (
        root
        / "data"
        / "private-agent-handoff"
        / "operator-recovery"
        / "food-line"
        / "2026-09-10"
        / "release-prep"
        / f"{item_id}.json"
    )


def _base_prep(root: Path, item_id: str, index: int = 0) -> dict:
    evidence_root = root / "data" / "private-agent-handoff" / "operator-recovery" / "food-line" / "2026-09-10"
    reconciliation = evidence_root / "reconciliation.json"
    editorial = evidence_root / "editorial-review.json"
    decision = evidence_root / "release-decision.json"
    if not reconciliation.exists():
        _write_json(reconciliation, {"kind": "reconciliation", "item_ids": ITEM_IDS})
        _write_json(editorial, {"kind": "editorial", "item_ids": ITEM_IDS})
        _write_json(decision, {"kind": "release-decision", "item_ids": ITEM_IDS})
    return {
        "schema_version": PREP_SCHEMA_VERSION,
        "item_id": item_id,
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "release_preparation_state": PRIVATE_RELEASE_PREP_STATE,
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "original_agent_run_id": f"agent-run-{index}",
        "recovery_reconciliation_ref": reconciliation.relative_to(root).as_posix(),
        "recovery_reconciliation_sha256": sha256_file(reconciliation),
        "editorial_review_ref": editorial.relative_to(root).as_posix(),
        "editorial_review_sha256": sha256_file(editorial),
        "release_decision_ref": decision.relative_to(root).as_posix(),
        "release_decision_sha256": sha256_file(decision),
        "source_url": f"https://example.org/source-{index}",
        "publisher": f"Publisher {index}",
        "source_verification": "current_url_verified",
        "event_date": "2026-09-10",
        "geography": "Food Line geography",
        "duplicate_disposition": "NO_DUPLICATE_FOUND",
        "approved_headline": f"Reviewed headline {index}",
        "approved_summary": f"Reviewed summary {index}",
        "reviewer_metadata": {"editorial_disposition": "APPROVE_WITH_EDIT"},
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
        "publication_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
    }


def _repo_with_prep(tmp_path: Path, *, overrides: dict | None = None, item_ids: list[str] | None = None) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    ids = item_ids or ITEM_IDS
    bindings = []
    for index, item_id in enumerate(ids):
        prep = _base_prep(root, item_id, index)
        if overrides:
            prep.update(copy.deepcopy(overrides))
        path = _prep_path(root, item_id)
        _write_json(path, prep)
        bindings.append(
            {
                "item_id": item_id,
                "release_prep_path": path.relative_to(root).as_posix(),
                "release_prep_sha256": sha256_file(path),
            }
        )
    _commit_all(root, "prep artifacts")
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "release_id": "september-10-food-line-operator-recovery",
        "authorized_by": "Human release owner",
        "authorized_at": "2026-09-12T12:00:00Z",
        "source_base_commit": _git(root, "rev-parse", "HEAD"),
        "expected_item_count": len(bindings),
        "release_prep_bindings": bindings,
        "hold_item_exclusion": {
            "excluded_item_ids": [HOLD_ITEM_ID],
            "reason": "editorial disposition remains HOLD",
            "release_prep_artifact_present": False,
        },
    }
    return root, request


def _write_private_request(tmp_path: Path, request: dict) -> Path:
    path = tmp_path / "request.json"
    _write_json(path, request)
    return path


def test_authorizes_one_hash_bound_private_batch_with_reviewed_wording(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path)
    result = create_release_authorization(root, _write_private_request(tmp_path, request))
    release = result["release_authorization"]
    assert result["status"] == "release_authorization_written"
    assert release["item_count"] == 4
    assert release["release_authorized"] is True
    assert release["publication_authorized"] is False
    assert release["public_generation_authorized"] is False
    assert release["pages_authorized"] is False
    assert release["eligible_for_automatic_publication"] is False
    assert release["publication_eligible"] is False
    assert release["publication_approval"] is False
    assert release["publication_performed"] is False
    assert release["hold_item_exclusion"]["excluded_item_ids"] == [HOLD_ITEM_ID]
    assert [item["item_id"] for item in release["release_items"]] == ITEM_IDS
    assert release["release_items"][0]["reviewed_headline"] == "Reviewed headline 0"
    assert release["release_items"][0]["release_prep_sha256"] == request["release_prep_bindings"][0]["release_prep_sha256"]
    assert validate_release_authorization(release, expected_release_id=request["release_id"]) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"reviewer_metadata": {"editorial_disposition": "HOLD"}}, "HOLD and REJECT"),
        ({"provenance_class": "synthetic_test_evidence"}, "provenance_class"),
        ({"original_production_artifact_present": True}, "original_production_artifact_present"),
        ({"publication_eligible": True}, "publication_eligible"),
        ({"publication_approval": True}, "publication_approval"),
        ({"synthetic_evidence_used": True}, "synthetic evidence"),
        ({"duplicate_disposition": "UNRESOLVED"}, "duplicate state"),
        ({"release_preparation_eligible": False}, "release_preparation_eligible"),
    ],
)
def test_rejects_disallowed_lineage_and_authority_states(tmp_path: Path, overrides: dict, message: str) -> None:
    root, request = _repo_with_prep(tmp_path, overrides=overrides, item_ids=[ITEM_IDS[0]])
    with pytest.raises(FoodLineOperatorRecoveryReleaseAuthorizationError, match=message):
        create_release_authorization(root, _write_private_request(tmp_path, request))


def test_rejects_missing_release_prep_artifact(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    _git(root, "rm", request["release_prep_bindings"][0]["release_prep_path"])
    _git(root, "commit", "-m", "remove prep artifact")
    request["source_base_commit"] = _git(root, "rev-parse", "HEAD")
    with pytest.raises(FoodLineOperatorRecoveryReleaseAuthorizationError, match="release-prep artifact must be committed"):
        create_release_authorization(root, _write_private_request(tmp_path, request))


def test_rejects_release_prep_hash_drift(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    request["release_prep_bindings"][0]["release_prep_sha256"] = "sha256:" + ("0" * 64)
    with pytest.raises(FoodLineOperatorRecoveryReleaseAuthorizationError, match="release-prep hash mismatch"):
        create_release_authorization(root, _write_private_request(tmp_path, request))


def test_rejects_bound_review_hash_drift(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    editorial = root / "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/editorial-review.json"
    _write_json(editorial, {"kind": "changed"})
    _commit_all(root, "change editorial review")
    request["source_base_commit"] = _git(root, "rev-parse", "HEAD")
    with pytest.raises(FoodLineOperatorRecoveryReleaseAuthorizationError, match="editorial_review_ref hash mismatch"):
        create_release_authorization(root, _write_private_request(tmp_path, request))


def test_rejects_hold_item_bound_into_request(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[HOLD_ITEM_ID])
    with pytest.raises(FoodLineOperatorRecoveryReleaseAuthorizationError, match="HOLD item is included"):
        create_release_authorization(root, _write_private_request(tmp_path, request))


def test_replay_is_idempotent_after_release_artifact_is_committed(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    request_path = _write_private_request(tmp_path, request)
    first = create_release_authorization(root, request_path)
    second = create_release_authorization(root, request_path)
    assert second["status"] == "idempotent_noop"
    assert second["release_authorization"] == first["release_authorization"]


def test_owner_does_not_create_public_scheduler_or_operational_outputs(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    create_release_authorization(root, _write_private_request(tmp_path, request))
    status = _git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert status == [f"?? {release_authorization_path_for(request['release_id'])}"]
    for disallowed in (
        "output",
        "bluefern-dispatches-pages",
        "publication-authorizations",
        "data/dispatches/food-line/archive",
        "status/food-line",
    ):
        assert not (root / disallowed).exists()


def test_public_generation_refuses_release_authorization_alone(tmp_path: Path) -> None:
    root, request = _repo_with_prep(tmp_path, item_ids=[ITEM_IDS[0]])
    result = create_release_authorization(root, _write_private_request(tmp_path, request))
    _commit_all(root, "release authorization")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="durable publication authorization"):
        generate_public_artifacts(root, Path(result["release_authorization_path"]), dispatch="food-line")
