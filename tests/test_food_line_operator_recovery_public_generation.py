from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_operator_recovery_public_generation import (
    EXPECTED_ITEM_IDS,
    HOLD_ITEM_ID,
    RECOVERY_DISCLOSURE,
    FoodLineOperatorRecoveryPublicGenerationError,
    generate_private_preview,
)
from bluefern_dispatches.food_line_operator_recovery_publication_authorization import (
    PUBLICATION_STATE,
    REQUEST_SCHEMA_VERSION,
    create_publication_authorization,
    sha256_file,
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


PUBLICATION_REL = Path(
    "publication-authorizations/food-line/operator-recovery/"
    "september-10-food-line-operator-recovery-publication-v1.json"
)


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


def _request_for(root: Path, release_path: Path, release: dict) -> dict:
    placement = "2026-09-12 recovery-disclosed current Food Line publication placement; not a September 10 normal-production edition"
    items = []
    for release_item in release["release_items"]:
        items.append(
            {
                "item_id": release_item["item_id"],
                "human_publication_authorization_state": PUBLICATION_STATE,
                "publication_placement_strategy": "recovery_disclosed_current_publication",
                "public_edition_date_or_placement": placement,
                "public_wording_constraints": RECOVERY_DISCLOSURE.removeprefix("Recovery note: "),
                "source_traceability_status": release_item["source_verification"],
                "failed_production_disclosure": "preserve_failed_production_operator_recovery_distinction",
                "approved_headline": release_item["reviewed_headline"],
                "approved_summary": release_item["reviewed_summary"],
                "source_url": release_item["source_url"],
                "publisher": release_item["publisher"],
                "event_date": release_item["event_date"],
            }
        )
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "publication_id": "september-10-food-line-operator-recovery",
        "authorized_by": "Publication owner",
        "authorized_at": "2026-09-12T20:00:00Z",
        "source_base_commit": _git(root, "rev-parse", "HEAD"),
        "release_path": release_path.relative_to(root).as_posix(),
        "release_sha256": sha256_file(release_path),
        "release_fingerprint": release["release_fingerprint"],
        "expected_item_count": 4,
        "expected_item_ids": list(EXPECTED_ITEM_IDS),
        "publication_items": items,
    }


def _repo_with_publication(tmp_path: Path, *, commit_publication: bool = True) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    release_items = [_release_item(item_id, index) for index, item_id in enumerate(EXPECTED_ITEM_IDS)]
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
    release_path = root / "releases/food-line/operator-recovery/september-10-food-line-operator-recovery-release-v1.json"
    _write_json(release_path, release)
    _commit_all(root, "release authorization")
    request_path = tmp_path / "request.json"
    _write_json(request_path, _request_for(root, release_path, release))
    create_publication_authorization(root, request_path)
    if commit_publication:
        _commit_all(root, "publication authorization")
    return root, root / PUBLICATION_REL


def _mutate_publication(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    _write_json(path, payload)


def test_valid_publication_authorization_generates_private_preview(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    output = tmp_path / "preview"
    result = generate_private_preview(root, publication, output, expected_sha256=sha256_file(publication))
    assert result["status"] == "private_preview_generated"
    assert result["preview_files"] == ["index.html", "preview-manifest.json"]
    assert result["rendered_headlines"] == [f"Reviewed headline {index}" for index in range(4)]
    html = (output / "index.html").read_text(encoding="utf-8")
    assert html.count('<article class="story">') == 4
    assert RECOVERY_DISCLOSURE in html
    assert HOLD_ITEM_ID not in html
    assert "operator_recovered_source_watch_evidence" not in html
    assert "PUBLICATION_AUTHORIZED" not in html
    assert "sha256" not in html
    assert "finding_" not in html
    assert "C:\\" not in html
    assert "normal-production edition" in html
    manifest = (output / "preview-manifest.json").read_text(encoding="utf-8")
    assert "sha256" not in manifest
    assert "finding_" not in manifest
    assert "publication-authorizations" not in manifest
    assert "public_generation_authorized" not in manifest
    assert "C:\\" not in manifest
    assert not (root / "output").exists()
    assert not (root / "data/dispatches").exists()
    assert not (root / "status").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update({"public_generation_authorized": True}), "public_generation_authorized"),
        (lambda payload: payload.update({"pages_authorized": True}), "pages_authorized"),
        (lambda payload: payload.update({"pages_push_authorized": True}), "pages_push_authorized"),
        (lambda payload: payload.update({"public_artifacts_generated": True}), "public_artifacts_generated"),
        (lambda payload: payload.update({"publication_performed": True}), "publication_performed"),
        (lambda payload: payload.update({"eligible_for_automatic_publication": True}), "eligible_for_automatic_publication"),
        (lambda payload: payload["publication_items"][0].update({"item_id": HOLD_ITEM_ID}), "item membership"),
        (lambda payload: payload["publication_items"][0].update({"reviewed_headline": "Changed"}), "changed reviewed_headline"),
        (lambda payload: payload["publication_items"][0].update({"reviewed_summary": "Changed"}), "changed reviewed_summary"),
        (lambda payload: payload["publication_items"][0].update({"source_url": "https://example.org/changed"}), "changed source_url"),
        (lambda payload: payload["publication_items"][0].update({"publisher": "Changed"}), "changed publisher"),
        (lambda payload: payload["publication_items"][0].update({"event_date": "2026-09-30"}), "changed event_date"),
        (lambda payload: payload["publication_items"][0].update({"provenance_class": "source_based_retrospective"}), "provenance"),
        (lambda payload: payload["publication_items"][0].update({"failed_production_disclosure": "hidden"}), "failed-production disclosure"),
        (lambda payload: payload["publication_items"][0].update({"publication_placement_strategy": "normal_september_10"}), "placement strategy"),
        (lambda payload: payload.update({"september_10_original_production_runtime_remains_failed": False}), "failed-production distinction"),
        (lambda payload: payload.update({"original_source_watch_lineage_fabricated": True}), "lineage"),
    ],
)
def test_authorization_drift_fails_closed(tmp_path: Path, mutate, message: str) -> None:
    root, publication = _repo_with_publication(tmp_path)
    _mutate_publication(publication, mutate)
    with pytest.raises(FoodLineOperatorRecoveryPublicGenerationError, match=message):
        generate_private_preview(root, publication, tmp_path / "preview")


def test_changed_authorization_sha_fails(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    with pytest.raises(FoodLineOperatorRecoveryPublicGenerationError, match="hash mismatch"):
        generate_private_preview(root, publication, tmp_path / "preview", expected_sha256="sha256:" + ("0" * 64))


def test_uncommitted_publication_authorization_fails(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path, commit_publication=False)
    with pytest.raises(FoodLineOperatorRecoveryPublicGenerationError, match="must be committed at HEAD"):
        generate_private_preview(root, publication, tmp_path / "preview")


@pytest.mark.parametrize(
    "relative",
    [
        Path("output/site/food-line/operator-recovery-preview"),
        Path("public/food-line"),
        Path("data/dispatches/food-line/review"),
        Path("status/food-line"),
    ],
)
def test_public_or_operational_output_paths_are_refused(tmp_path: Path, relative: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    with pytest.raises(FoodLineOperatorRecoveryPublicGenerationError, match="outside the repository"):
        generate_private_preview(root, publication, root / relative)


def test_pages_like_external_output_path_is_refused(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    with pytest.raises(FoodLineOperatorRecoveryPublicGenerationError, match="public or Pages destination"):
        generate_private_preview(root, publication, tmp_path / "gh-pages")


def test_private_preview_replay_is_deterministic(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    output = tmp_path / "preview"
    first = generate_private_preview(root, publication, output)
    index_one = (output / "index.html").read_text(encoding="utf-8")
    manifest_one = (output / "preview-manifest.json").read_text(encoding="utf-8")
    second = generate_private_preview(root, publication, output)
    assert second["rendered_headlines"] == first["rendered_headlines"]
    assert (output / "index.html").read_text(encoding="utf-8") == index_one
    assert (output / "preview-manifest.json").read_text(encoding="utf-8") == manifest_one


def test_source_based_generation_behavior_remains_unaffected(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="outside the dispatch publication-authorization owner"):
        generate_public_artifacts(root, publication, dispatch="food-line")


def test_generation_does_not_write_public_or_operational_surfaces(tmp_path: Path) -> None:
    root, publication = _repo_with_publication(tmp_path)
    before = set(_git(root, "status", "--porcelain", "--untracked-files=all").splitlines())
    generate_private_preview(root, publication, tmp_path / "preview")
    after = set(_git(root, "status", "--porcelain", "--untracked-files=all").splitlines())
    assert after == before
    for disallowed in (
        "output",
        "public",
        "bluefern-dispatches-pages",
        "archive",
        "data/dispatches",
        "status",
    ):
        assert not (root / disallowed).exists()
