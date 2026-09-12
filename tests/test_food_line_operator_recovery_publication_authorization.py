from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_operator_recovery_publication_authorization import (
    FoodLineOperatorRecoveryPublicationAuthorizationError,
    PUBLICATION_STATE,
    REQUEST_SCHEMA_VERSION,
    create_publication_authorization,
    publication_path_for,
    sha256_file,
    validate_publication_authorization,
)
from bluefern_dispatches.food_line_operator_recovery_release_authorization import (
    RELEASE_AUTHORIZATION_STATE,
    SCHEMA_VERSION as RELEASE_SCHEMA_VERSION,
    payload_sha256,
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


def _release_item(item_id: str, index: int) -> dict:
    return {
        "item_id": item_id,
        "release_decision": RELEASE_AUTHORIZATION_STATE,
        "release_authorized": True,
        "release_prep_path": f"data/private-agent-handoff/operator-recovery/food-line/2026-09-10/release-prep/{item_id}.json",
        "release_prep_sha256": "sha256:" + f"{index + 1:064x}",
        "release_prep_correction_path": f"data/private-agent-handoff/operator-recovery/food-line/2026-09-10/release-prep-corrections/{item_id}-correction-v1.json",
        "release_prep_correction_sha256": "sha256:" + f"{index + 11:064x}",
        "provenance_class": "operator_recovered_source_watch_evidence",
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "failed_production_lineage_preserved": True,
        "original_source_watch_lineage_fabricated": False,
        "original_agent_run_id": f"food-line-source-watch-{index}",
        "source_url": f"https://example.org/source-{index}",
        "publisher": f"Publisher {index}",
        "source_verification": "current_url_verified",
        "event_date": f"2026-09-{8 + min(index, 2):02d}",
        "geography": f"Geography {index}",
        "duplicate_disposition": "NO_DUPLICATE_FOUND",
        "reviewed_headline": f"Reviewed headline {index}",
        "reviewed_summary": f"Reviewed summary {index}",
        "editorial_review_ref": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/editorial-review.json",
        "editorial_review_sha256": "sha256:" + ("a" * 64),
        "release_decision_ref": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/release-decision.json",
        "release_decision_sha256": "sha256:" + ("b" * 64),
        "recovery_reconciliation_ref": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json",
        "recovery_reconciliation_sha256": "sha256:" + ("c" * 64),
        "publication_authorized": False,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "archive_rss_homepage_authorized": False,
        "operational_health_state_authorized": False,
        "publication_performed": False,
        "eligible_for_automatic_publication": False,
        "publication_eligible": False,
        "publication_approval": False,
    }


def _repo_with_release(tmp_path: Path, *, release_overrides: dict | None = None) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    release_items = [_release_item(item_id, index) for index, item_id in enumerate(ITEM_IDS)]
    release = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_type": "food_line_operator_recovery_release_authorization",
        "dispatch": "food-line",
        "release_id": "september-10-food-line-operator-recovery",
        "release_authorization_state": RELEASE_AUTHORIZATION_STATE,
        "authorized_by": "Release owner",
        "authorized_at": "2026-09-12T17:22:35Z",
        "source_base_commit": "protected-release-base",
        "item_count": 4,
        "release_items": release_items,
        "hold_item_exclusion": {
            "excluded_item_ids": [HOLD_ITEM_ID],
            "reason": "editorial disposition remains HOLD",
            "release_prep_artifact_present": False,
        },
        "september_10_original_production_runtime_remains_failed": True,
        "original_source_watch_lineage_fabricated": False,
        "release_authorized": True,
        "publication_authorized": False,
        "public_generation_authorized": False,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "archive_rss_homepage_authorized": False,
        "operational_health_state_authorized": False,
        "publication_performed": False,
        "eligible_for_automatic_publication": False,
        "publication_eligible": False,
        "publication_approval": False,
        "release_fingerprint": payload_sha256(release_items),
    }
    if release_overrides:
        release.update(copy.deepcopy(release_overrides))
    release_path = root / "releases/food-line/operator-recovery/september-10-food-line-operator-recovery-release-v1.json"
    _write_json(release_path, release)
    _commit_all(root, "release authorization")
    request = _request_for(root, release_path, release)
    return root, request


def _request_for(root: Path, release_path: Path, release: dict, *, overrides: dict | None = None) -> dict:
    items = []
    for release_item in release["release_items"]:
        items.append(
            {
                "item_id": release_item["item_id"],
                "human_publication_authorization_state": PUBLICATION_STATE,
                "publication_placement_strategy": "recovery_disclosed_current_publication",
                "public_edition_date_or_placement": "operator-recovery publication after September 10 failed production",
                "public_wording_constraints": "Preserve operator-recovery and failed-production distinction.",
                "source_traceability_status": release_item["source_verification"],
                "failed_production_disclosure": "preserve_failed_production_operator_recovery_distinction",
                "approved_headline": release_item["reviewed_headline"],
                "approved_summary": release_item["reviewed_summary"],
                "source_url": release_item["source_url"],
                "publisher": release_item["publisher"],
                "event_date": release_item["event_date"],
            }
        )
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "publication_id": "september-10-food-line-operator-recovery",
        "authorized_by": "Publication owner",
        "authorized_at": "2026-09-12T18:30:00Z",
        "source_base_commit": _git(root, "rev-parse", "HEAD"),
        "release_path": release_path.relative_to(root).as_posix(),
        "release_sha256": sha256_file(release_path),
        "release_fingerprint": release["release_fingerprint"],
        "expected_item_count": 4,
        "expected_item_ids": ITEM_IDS,
        "publication_items": items,
    }
    if overrides:
        request.update(copy.deepcopy(overrides))
    return request


def _write_request(tmp_path: Path, request: dict) -> Path:
    path = tmp_path / "publication-request.json"
    _write_json(path, request)
    return path


def test_exact_protected_release_receives_publication_authorization(tmp_path: Path) -> None:
    root, request = _repo_with_release(tmp_path)
    result = create_publication_authorization(root, _write_request(tmp_path, request))
    publication = result["publication"]
    assert result["status"] == "publication_authorization_written"
    assert publication["publication_authorized"] is True
    assert publication["public_generation_authorized"] is False
    assert publication["pages_authorized"] is False
    assert publication["public_artifacts_generated"] is False
    assert publication["publication_performed"] is False
    assert publication["item_count"] == 4
    assert [item["item_id"] for item in publication["publication_items"]] == ITEM_IDS
    assert publication["release_sha256"] == request["release_sha256"]
    assert publication["release_fingerprint"] == request["release_fingerprint"]
    assert validate_publication_authorization(publication) == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda root, request: request.update({"release_path": "missing.json"}), "release_path must use|must be committed at HEAD|unable to read"),
        (lambda root, request: request.update({"release_sha256": "sha256:" + ("0" * 64)}), "release hash mismatch"),
        (lambda root, request: request.update({"release_fingerprint": "sha256:" + ("1" * 64)}), "release fingerprint mismatch"),
        (lambda root, request: request["publication_items"][0].update({"approved_headline": "Changed"}), "changed approved_headline"),
        (lambda root, request: request["publication_items"][0].update({"approved_summary": "Changed"}), "changed approved_summary"),
        (lambda root, request: request["publication_items"][0].update({"source_url": "https://example.org/changed"}), "changed source_url"),
        (lambda root, request: request["publication_items"][0].update({"event_date": "2026-09-30"}), "changed event_date"),
        (lambda root, request: request["publication_items"].append({**request["publication_items"][0], "item_id": HOLD_ITEM_ID}), "item membership|expected_item_ids"),
        (lambda root, request: request["publication_items"][0].update({"publication_placement_strategy": "ambiguous"}), "placement is ambiguous"),
        (lambda root, request: request.pop("authorized_by"), "authorized_by"),
    ],
)
def test_publication_request_fail_closed_cases(tmp_path: Path, mutate, message: str) -> None:
    root, request = _repo_with_release(tmp_path)
    mutate(root, request)
    with pytest.raises(FoodLineOperatorRecoveryPublicationAuthorizationError, match=message):
        create_publication_authorization(root, _write_request(tmp_path, request))


@pytest.mark.parametrize(
    ("release_overrides", "message"),
    [
        ({"release_authorized": False}, "release_authorized"),
        ({"publication_authorized": True}, "release validation failed|publication_authorized"),
        ({"eligible_for_automatic_publication": True}, "release validation failed|eligible_for_automatic_publication"),
        ({"release_authorization_state": "WRONG"}, "release validation failed|state"),
        ({"schema_version": "wrong"}, "release validation failed|schema"),
    ],
)
def test_release_authority_fail_closed_cases(tmp_path: Path, release_overrides: dict, message: str) -> None:
    root, request = _repo_with_release(tmp_path, release_overrides=release_overrides)
    with pytest.raises(FoodLineOperatorRecoveryPublicationAuthorizationError, match=message):
        create_publication_authorization(root, _write_request(tmp_path, request))


def test_duplicate_or_conflicting_authority_fails_and_exact_replay_is_idempotent(tmp_path: Path) -> None:
    root, request = _repo_with_release(tmp_path)
    request_path = _write_request(tmp_path, request)
    first = create_publication_authorization(root, request_path)
    second = create_publication_authorization(root, request_path)
    assert second["status"] == "idempotent_noop"
    assert second["publication"] == first["publication"]
    _commit_all(root, "publication authorization")
    other = copy.deepcopy(request)
    other["publication_id"] = "september-10-food-line-operator-recovery-copy"
    other["source_base_commit"] = _git(root, "rev-parse", "HEAD")
    with pytest.raises(FoodLineOperatorRecoveryPublicationAuthorizationError, match="already has publication authorization"):
        create_publication_authorization(root, _write_request(tmp_path, other))


def test_public_generation_rejects_operator_recovery_publication_authorization(tmp_path: Path) -> None:
    root, request = _repo_with_release(tmp_path)
    result = create_publication_authorization(root, _write_request(tmp_path, request))
    _commit_all(root, "publication authorization")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="outside the dispatch publication-authorization owner"):
        generate_public_artifacts(root, Path(result["publication_path"]), dispatch="food-line")


def test_no_public_pages_archive_scheduler_or_operational_outputs(tmp_path: Path) -> None:
    root, request = _repo_with_release(tmp_path)
    create_publication_authorization(root, _write_request(tmp_path, request))
    assert _git(root, "status", "--porcelain", "--untracked-files=all").splitlines() == [
        f"?? {publication_path_for(request['publication_id'])}"
    ]
    for disallowed in (
        "output",
        "bluefern-dispatches-pages",
        "data/dispatches/food-line/archive",
        "status/food-line",
    ):
        assert not (root / disallowed).exists()
