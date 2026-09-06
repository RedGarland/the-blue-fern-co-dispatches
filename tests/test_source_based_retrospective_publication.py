from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.source_based_retrospective_publication import (
    CARE_PUBLICATION_SCHEMA,
    FOOD_PUBLICATION_SCHEMA,
    REQUEST_SCHEMA,
    SourceBasedRetrospectivePublicationError,
    create_publication,
    validate_publication,
)
from bluefern_dispatches.source_based_retrospective_release import CARE_RELEASE_SCHEMA, FOOD_RELEASE_SCHEMA


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


def _release_item(
    dispatch: str,
    suffix: str,
    *,
    source_identifier: str | None = None,
    chronology: str = "august_event",
    binding: str = "2026-08-17",
    event_date: str = "2026-08-17",
    wording: str = "Use bounded wording.",
) -> dict:
    source_id = source_identifier or f"{dispatch}-finding-{suffix}"
    item = {
        "approval_item_id": f"{dispatch}-release-item-{suffix}",
        "dispatch": dispatch,
        "source_identifier": source_id,
        "retrospective_finding_id": source_id,
        "release_decision": "release_authorized",
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "date_binding_classification": chronology,
        "recommended_public_date_binding": binding,
        "source_or_publication_date": "2026-08-01",
        "event_or_effective_date": event_date,
        "retrospective_coverage_month": "2026-08",
        "wording_constraints": wording,
        "duplicate_status": "distinct",
        "duplicate_relationship": "",
        "lineage_validation_status": "valid",
        "approval_item_sha256": "sha256:" + "1" * 64,
        "readiness_item_sha256": "sha256:" + "2" * 64,
        "readiness_item_snapshot": {
            "source_url": f"https://example.test/{dispatch}/{suffix}",
            "publisher": "Example Publisher",
        },
    }
    return item


def _release(repo: Path, dispatch: str, release_id: str, items: list[dict]) -> Path:
    payload = {
        "schema_version": FOOD_RELEASE_SCHEMA if dispatch == "food-line" else CARE_RELEASE_SCHEMA,
        "release_type": "source_based_retrospective_release_authorization",
        "dispatch": dispatch,
        "release_batch_id": release_id,
        "authorized_by": "William Patton",
        "authorized_at": "2026-09-06T13:00:00Z",
        "source_base_commit": _git(repo, "rev-parse", "HEAD"),
        "retrospective_coverage_month": "2026-08",
        "source_approval_records": [],
        "source_approval_batch_ids": [],
        "release_readiness_review": {"path": "private.json", "sha256": "sha256:" + "3" * 64},
        "item_count": len(items),
        "release_items": items,
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "release_fingerprint": _fingerprint(items),
    }
    path = repo / "releases" / dispatch / "source-based-retrospectives" / f"{release_id}-release-v1.json"
    _write_json(path, payload)
    _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-m", f"add {dispatch} release")
    return path.relative_to(repo)


def _decision(release_item: dict, *, placement: str | None = None, wording: str | None = None) -> dict:
    return {
        "dispatch": release_item["dispatch"],
        "release_item_id": release_item["approval_item_id"],
        "chronology_classification": release_item["date_binding_classification"],
        "event_or_effective_date_or_range": release_item["recommended_public_date_binding"],
        "retrospective_coverage_period": release_item["retrospective_coverage_month"],
        "public_edition_date_or_placement": placement or release_item["recommended_public_date_binding"],
        "public_wording_constraints": wording or release_item["wording_constraints"],
        "source_traceability_status": "traceable",
        "human_publication_authorization_state": "publication_authorized",
    }


def _request(repo: Path, dispatch: str, release_paths: list[Path], decisions: list[dict], *, count: int, publication_id: str = "august-publication-01") -> Path:
    path = repo.parent / f"{dispatch}-publication-request.json"
    _write_json(
        path,
        {
            "schema_version": REQUEST_SCHEMA,
            "dispatch": dispatch,
            "publication_batch_id": publication_id,
            "authorized_by": "William Patton",
            "authorized_at": "2026-09-06T14:00:00Z",
            "source_base_commit": _git(repo, "rev-parse", "HEAD"),
            "expected_item_count": count,
            "release_bindings": [
                {"release_path": release_path.as_posix(), "release_sha256": _sha(repo / release_path)}
                for release_path in release_paths
            ],
            "publication_items": decisions,
        },
    )
    return path


def test_valid_release_to_valid_publication_authorization(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _release_item("food-line", "lucky", chronology="august_announcement_future_effect", binding="2026-09-11")
    release = _release(repo, "food-line", "food-release-1", [item])

    result = create_publication(repo, _request(repo, "food-line", [release], [_decision(item, placement="2026-09-11")], count=1))

    assert result["status"] == "publication_written"
    assert result["publication"]["schema_version"] == FOOD_PUBLICATION_SCHEMA
    assert result["publication"]["publication_authorized"] is True
    assert result["publication"]["pages_authorized"] is False
    assert result["publication"]["public_generation_authorized"] is False
    assert not (repo / "output" / "site").exists()


def test_missing_release_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    missing_item = _release_item("food-line", "missing")
    missing = Path("releases/food-line/source-based-retrospectives/missing-release-v1.json")

    with pytest.raises(FileNotFoundError):
        create_publication(repo, _request(repo, "food-line", [missing], [_decision(missing_item)], count=1))


def test_release_hash_mismatch_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _release_item("food-line", "hash")
    release = _release(repo, "food-line", "food-release-1", [item])
    request = _request(repo, "food-line", [release], [_decision(item)], count=1)
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["release_bindings"][0]["release_sha256"] = "sha256:" + "0" * 64
    _write_json(request, payload)

    with pytest.raises(SourceBasedRetrospectivePublicationError, match="release hash mismatch"):
        create_publication(repo, request)


def test_release_not_authorized_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _release_item("food-line", "not-authorized")
    release = _release(repo, "food-line", "food-release-1", [item])
    path = repo / release
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["release_authorized"] = False
    _write_json(path, payload)
    _git(repo, "add", str(release))
    _git(repo, "commit", "-m", "mark release not authorized")

    with pytest.raises(SourceBasedRetrospectivePublicationError, match="release validation failed"):
        create_publication(repo, _request(repo, "food-line", [release], [_decision(item)], count=1))


def test_duplicate_ids_and_mixed_dispatch_fail(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _release_item("food-line", "dup")
    release = _release(repo, "food-line", "food-release-1", [item])
    duplicate = [_decision(item), _decision(item)]
    with pytest.raises(SourceBasedRetrospectivePublicationError, match="duplicate publication item IDs"):
        create_publication(repo, _request(repo, "food-line", [release], duplicate, count=2))

    mixed = _decision(item)
    mixed["dispatch"] = "care-line"
    with pytest.raises(SourceBasedRetrospectivePublicationError, match="dispatch mismatch"):
        create_publication(repo, _request(repo, "food-line", [release], [mixed], count=1))


def test_pages_authority_enabled_fails_validation() -> None:
    item = _publication_item("care-line-item-1", "care-line")
    payload = _publication("care-line", [item])
    payload["pages_authorized"] = True

    assert "publication pages_authorized must be false" in validate_publication(payload, expected_dispatch="care-line")


def test_food_chronology_exceptions_are_preserved_and_alabama_absent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    items = [
        _release_item("food-line", "lucky", source_identifier="Lucky supermarket", chronology="august_announcement_future_effect", binding="2026-09-11"),
        _release_item("food-line", "south-amboy", source_identifier="South Amboy pantry", chronology="august_announcement_future_effect", binding="2026-08-21/2026-09-07"),
        _release_item("food-line", "comet-cupboard", source_identifier="UT Dallas Comet Cupboard", chronology="august_event", binding="2026-08-17/2026-09-04"),
        _release_item("food-line", "abiding-love", source_identifier="Abiding Love", chronology="august_event", binding="2026-08-31/2026-09-07"),
    ]
    release = _release(repo, "food-line", "food-release-1", items)

    result = create_publication(repo, _request(repo, "food-line", [release], [_decision(item) for item in items], count=4))

    by_source = {item["source_identifier"]: item for item in result["publication"]["publication_items"]}
    assert by_source["Lucky supermarket"]["event_or_effective_date_or_range"] == "2026-09-11"
    assert by_source["South Amboy pantry"]["event_or_effective_date_or_range"] == "2026-08-21/2026-09-07"
    assert by_source["UT Dallas Comet Cupboard"]["event_or_effective_date_or_range"] == "2026-08-17/2026-09-04"
    assert by_source["Abiding Love"]["event_or_effective_date_or_range"] == "2026-08-31/2026-09-07"
    assert all("ee6f2e01f1f3422ce095c6ec" not in json.dumps(item) for item in result["publication"]["publication_items"])


def test_care_chronology_and_no_queue_publication_handoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    items = [
        _release_item("care-line", "brattleboro", source_identifier="Brattleboro VA", chronology="august_restoration", binding="2026-09-01", event_date="closed February 2026; reopening September 1, 2026", wording="restoration must not become closure"),
        _release_item("care-line", "council-bluffs", source_identifier="Council Bluffs", chronology="september_effective_event_with_august_source", binding="2026-08-31/2026-09-01", wording="preserve labor-delivery and Level II NICU transition"),
        _release_item("care-line", "newark-wayne", source_identifier="Newark-Wayne", chronology="september_effective_event_with_august_source", binding="2026-08-31/2026-09-03", wording="preserve maternity shutdown transition"),
        _release_item("care-line", "heights", source_identifier="Heights University Hospital", chronology="august_reporting_on_continuing_prior_loss", binding="2026-03-14 continuing; August 27 public record", event_date="final ED suspension March 14, 2026; August 27 public record", wording="continuing loss must not become newly effective closure"),
    ]
    release = _release(repo, "care-line", "care-release-1", items)

    result = create_publication(repo, _request(repo, "care-line", [release], [_decision(item) for item in items], count=4))

    assert result["publication"]["schema_version"] == CARE_PUBLICATION_SCHEMA
    assert {item["chronology_classification"] for item in result["publication"]["publication_items"]} == {
        "august_restoration",
        "september_effective_event_with_august_source",
        "august_reporting_on_continuing_prior_loss",
    }
    assert result["publication"]["public_generation_authorized"] is False
    assert not (repo / "data" / "dispatches" / "care-line" / "queue").exists()


def test_lost_wording_or_wrong_binding_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    item = _release_item("care-line", "heights", chronology="august_reporting_on_continuing_prior_loss", binding="2026-03-14 continuing; August 27 public record", wording="continuing loss must not become newly effective closure")
    release = _release(repo, "care-line", "care-release-1", [item])
    decision = _decision(item)
    decision["public_wording_constraints"] = "generic wording"
    with pytest.raises(SourceBasedRetrospectivePublicationError, match="wording constraints"):
        create_publication(repo, _request(repo, "care-line", [release], [decision], count=1))

    decision = _decision(item)
    decision["event_or_effective_date_or_range"] = "2026-08-27"
    with pytest.raises(SourceBasedRetrospectivePublicationError, match="event/effective binding"):
        create_publication(repo, _request(repo, "care-line", [release], [decision], count=1))


def _publication_item(item_id: str, dispatch: str) -> dict:
    return {
        "publication_item_id": item_id,
        "release_batch_id": "release-1",
        "release_item_id": item_id,
        "release_item_sha256": "sha256:" + "1" * 64,
        "release_record_sha256": "sha256:" + "2" * 64,
        "approval_item_id": item_id,
        "source_identifier": "source",
        "retrospective_finding_id": "finding",
        "approval_item_sha256": "sha256:" + "3" * 64,
        "readiness_item_sha256": "sha256:" + "4" * 64,
        "dispatch": dispatch,
        "publication_decision": "publication_authorized",
        "human_publication_authorization_state": "publication_authorized",
        "publication_authorized": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_generation_authorized": False,
        "public_artifacts_generated": False,
        "chronology_classification": "august_event",
        "source_publication_date": "2026-08-01",
        "event_or_effective_date_or_range": "2026-08-01",
        "retrospective_coverage_period": "2026-08",
        "public_edition_date_or_placement": "2026-08-01",
        "public_wording_constraints": "Use bounded wording.",
        "duplicate_status": "distinct",
        "duplicate_relationship": "",
        "source_traceability_status": "traceable",
    }


def _publication(dispatch: str, items: list[dict]) -> dict:
    return {
        "schema_version": FOOD_PUBLICATION_SCHEMA if dispatch == "food-line" else CARE_PUBLICATION_SCHEMA,
        "publication_type": "source_based_retrospective_publication_authorization",
        "dispatch": dispatch,
        "publication_batch_id": "publication-1",
        "publication_authorized": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_generation_authorized": False,
        "public_artifacts_generated": False,
        "item_count": len(items),
        "publication_items": items,
    }
