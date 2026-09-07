from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.source_based_retrospective_public_generation import (
    SourceBasedRetrospectivePublicGenerationError,
    generate_public_artifacts,
    validate_generation_manifest,
)
from bluefern_dispatches.source_based_retrospective_publication import CARE_PUBLICATION_SCHEMA, FOOD_PUBLICATION_SCHEMA


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


def _item(
    dispatch: str,
    suffix: str,
    *,
    source_identifier: str | None = None,
    location: str | None = None,
    event: str | None = None,
    publisher: str = "Example Publisher",
    source_url: str | None = None,
    chronology: str = "august_event",
    source_date: str = "2026-08-17",
    binding: str = "2026-08-17",
    placement: str | None = None,
    wording: str = "Keep wording bounded to the source.",
    pressure: str = "service interruption",
) -> dict:
    item_id = f"{dispatch}-source-retrospective-{suffix}"
    ready = {
        "publisher": publisher,
        "source_url": source_url if source_url is not None else f"https://example.test/{dispatch}/{suffix}",
        "location": location or f"{dispatch} location {suffix}",
        "event": event or f"{dispatch} access strain {suffix}",
        "state_or_territory": "IA",
        "pressure_type": pressure,
    }
    release_snapshot = {
        "approval_item_id": item_id,
        "dispatch": dispatch,
        "source_identifier": source_identifier or item_id,
        "retrospective_finding_id": source_identifier or item_id,
        "release_authorized": True,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "date_binding_classification": chronology,
        "recommended_public_date_binding": binding,
        "source_or_publication_date": source_date,
        "event_or_effective_date": binding,
        "retrospective_coverage_month": "2026-08",
        "wording_constraints": wording,
        "duplicate_status": "distinct",
        "duplicate_relationship": "",
        "lineage_validation_status": "valid",
        "readiness_item_snapshot": ready,
        "approval_item_sha256": "sha256:" + "1" * 64,
        "readiness_item_sha256": "sha256:" + "2" * 64,
    }
    return {
        "publication_item_id": item_id,
        "release_batch_id": f"{dispatch}-august-2026-source-based-release",
        "release_item_id": item_id,
        "release_item_sha256": _fingerprint(release_snapshot),
        "release_record_sha256": "sha256:" + "3" * 64,
        "approval_item_id": item_id,
        "source_identifier": source_identifier or item_id,
        "retrospective_finding_id": source_identifier or item_id,
        "approval_item_sha256": "sha256:" + "1" * 64,
        "readiness_item_sha256": "sha256:" + "2" * 64,
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
        "chronology_classification": chronology,
        "source_publication_date": source_date,
        "event_or_effective_date_or_range": binding,
        "retrospective_coverage_period": "2026-08",
        "public_edition_date_or_placement": placement or binding,
        "public_wording_constraints": wording,
        "duplicate_status": "distinct",
        "duplicate_relationship": "",
        "source_traceability_status": "traceable",
        "release_item_snapshot": release_snapshot,
    }


def _publication(dispatch: str, batch_id: str, items: list[dict]) -> dict:
    return {
        "schema_version": FOOD_PUBLICATION_SCHEMA if dispatch == "food-line" else CARE_PUBLICATION_SCHEMA,
        "publication_type": "source_based_retrospective_publication_authorization",
        "dispatch": dispatch,
        "publication_batch_id": batch_id,
        "authorized_by": "William Patton",
        "authorized_at": "2026-09-06T20:00:00Z",
        "source_base_commit": "0" * 40,
        "release_records": [],
        "release_batch_ids": [],
        "item_count": len(items),
        "publication_items": items,
        "publication_authorized": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_generation_authorized": False,
        "public_artifacts_generated": False,
        "publication_fingerprint": _fingerprint(items),
    }


def _commit_publication(repo: Path, dispatch: str, batch_id: str, items: list[dict]) -> Path:
    rel = Path("publication-authorizations") / dispatch / "source-based-retrospectives" / f"{batch_id}-publication-v1.json"
    _write_json(repo / rel, _publication(dispatch, batch_id, items))
    _git(repo, "add", str(rel))
    _git(repo, "commit", "-m", f"add {dispatch} publication authorization")
    return rel


def test_valid_authorization_generates_deterministic_local_artifacts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    rel = _commit_publication(repo, "food-line", "august-publication", [_item("food-line", "one")])
    expected_sha = _sha(repo / rel)

    result = generate_public_artifacts(repo, rel, dispatch="food-line", expected_sha256=expected_sha)

    generation = result["generation"]
    assert generation["authorized_item_count"] == 1
    assert generation["rendered_item_count"] == 1
    assert generation["skipped_item_count"] == 0
    assert generation["unauthorized_item_count"] == 0
    assert generation["pages_authorized"] is False
    assert generation["social_authorized"] is False
    assert generation["audio_authorized"] is False
    assert generation["scheduled_task_change_authorized"] is False
    html_path = repo / "output/site/food-line/source-based-retrospectives/august-publication/index.html"
    first_html = html_path.read_text(encoding="utf-8")
    assert "https://example.test/food-line/one" in first_html
    assert "release authorization" not in first_html
    assert validate_generation_manifest(repo, generation["generation_receipt_path"])["ok"] is True
    _git(repo, "add", "output", "data")
    _git(repo, "commit", "-m", "record generated artifacts")

    second = generate_public_artifacts(repo, rel, dispatch="food-line", expected_sha256=expected_sha)

    assert html_path.read_text(encoding="utf-8") == first_html
    assert second["generation"]["artifact_hashes"]["output/site/food-line/source-based-retrospectives/august-publication/index.html"] == generation["artifact_hashes"]["output/site/food-line/source-based-retrospectives/august-publication/index.html"]


def test_missing_invalid_hash_unauthorized_and_count_mismatch_fail(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    missing = Path("publication-authorizations/food-line/source-based-retrospectives/missing-publication-v1.json")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="unable to read valid JSON"):
        generate_public_artifacts(repo, missing, dispatch="food-line")

    rel = _commit_publication(repo, "food-line", "august-publication", [_item("food-line", "one")])
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="hash mismatch"):
        generate_public_artifacts(repo, rel, dispatch="food-line", expected_sha256="sha256:" + "0" * 64)

    payload = json.loads((repo / rel).read_text(encoding="utf-8"))
    payload["publication_items"][0]["publication_authorized"] = False
    _write_json(repo / rel, payload)
    _git(repo, "add", str(rel))
    _git(repo, "commit", "-m", "make item unauthorized")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="publication authorization validation failed"):
        generate_public_artifacts(repo, rel, dispatch="food-line")

    payload["publication_items"][0]["publication_authorized"] = True
    payload["item_count"] = 2
    _write_json(repo / rel, payload)
    _git(repo, "add", str(rel))
    _git(repo, "commit", "-m", "mismatch item count")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="item_count"):
        generate_public_artifacts(repo, rel, dispatch="food-line")


def test_rejects_wrong_input_dispatch_missing_source_and_dirty_source(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    approval = Path("approvals/food-line/source-based-retrospectives/not-a-publication.json")
    _write_json(repo / approval, {"ok": True})
    _git(repo, "add", str(approval))
    _git(repo, "commit", "-m", "add non-publication input")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="durable publication authorization"):
        generate_public_artifacts(repo, approval, dispatch="food-line")

    rel = _commit_publication(repo, "care-line", "august-publication", [_item("care-line", "one")])
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="dispatch mismatch"):
        generate_public_artifacts(repo, rel, dispatch="food-line")

    bad_rel = _commit_publication(repo, "food-line", "august-publication", [_item("food-line", "bad", publisher="")])
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="publisher missing"):
        generate_public_artifacts(repo, bad_rel, dispatch="food-line")

    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SourceBasedRetrospectivePublicGenerationError, match="working tree must be clean"):
        generate_public_artifacts(repo, rel, dispatch="care-line")


def test_no_pages_schedule_social_or_audio_mutation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    pages_marker = repo / "bluefern-dispatches-pages"
    pages_marker.write_text("unchanged\n", encoding="utf-8")
    task = repo / "ops" / "care-line-task.xml"
    task.parent.mkdir(parents=True)
    task.write_text("<Task>unchanged</Task>\n", encoding="utf-8")
    _git(repo, "add", "bluefern-dispatches-pages", "ops/care-line-task.xml")
    _git(repo, "commit", "-m", "add untouched operational files")
    rel = _commit_publication(repo, "food-line", "august-publication", [_item("food-line", "one")])

    generate_public_artifacts(repo, rel, dispatch="food-line")

    assert pages_marker.read_text(encoding="utf-8") == "unchanged\n"
    assert task.read_text(encoding="utf-8") == "<Task>unchanged</Task>\n"
    assert not (repo / "output/site/food-line/audio").exists()
    assert not (repo / "output/site/food-line/social").exists()


def test_food_sixteen_item_population_and_chronology_protections(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    items = [
        _item("food-line", "lucky", source_identifier="Lucky supermarket", location="Lucky", event="Store closure announced", chronology="august_announcement_future_effect", source_date="2026-08-04", binding="2026-09-11", wording="Lucky is effective Sept. 11, not closed in August."),
        _item("food-line", "south-amboy", location="South Amboy", chronology="august_announcement_future_effect", source_date="2026-08-10", binding="2026-08-21/2026-09-07", wording="South Amboy runs Aug. 21 through Sept. 7."),
        _item("food-line", "ut-dallas", location="UT Dallas Comet Cupboard", source_date="2026-08-17", binding="2026-08-17/2026-09-04", wording="UT Dallas runs Aug. 17 through Sept. 4."),
        _item("food-line", "abiding-love", location="Abiding Love", source_date="2026-08-31", binding="2026-08-31/2026-09-07", wording="Abiding Love runs Aug. 31 through Sept. 7."),
    ]
    items.extend(_item("food-line", f"extra-{index}") for index in range(12))
    rel = _commit_publication(repo, "food-line", "august-publication", items)

    result = generate_public_artifacts(repo, rel, dispatch="food-line")

    html_text = (repo / "output/site/food-line/source-based-retrospectives/august-publication/index.html").read_text(encoding="utf-8")
    assert result["generation"]["authorized_item_count"] == 16
    assert "Alabama SNAP" not in html_text
    assert "2026-09-11" in html_text
    assert "does not describe a completed August closure" in html_text
    assert "2026-08-21/2026-09-07" in html_text
    assert "2026-08-17/2026-09-04" in html_text
    assert "2026-08-31/2026-09-07" in html_text
    assert html_text.count('href="https://example.test/food-line/') == 16


def test_care_four_item_population_and_mandatory_framing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    items = [
        _item("care-line", "brattleboro", location="Brattleboro VA", event="Clinic reopening after repair closure", chronology="august_restoration", source_date="closed February 2026; reopening September 1, 2026", binding="2026-09-01", wording="Render as restoration/reopening after repair closure."),
        _item("care-line", "council-bluffs", location="Council Bluffs", event="Labor and delivery and Level II NICU services end", chronology="september_effective_event_with_august_source", source_date="after August 31, 2026 / effective September 1, 2026", binding="2026-08-31/2026-09-01", wording="Preserve labor-delivery and Level II NICU loss."),
        _item("care-line", "newark-wayne", location="Newark-Wayne", event="Maternity service shutdown", chronology="september_effective_event_with_august_source", source_date="after August 31, 2026; formal suspension September 3, 2026", binding="2026-08-31/2026-09-03", wording="Preserve the maternity shutdown transition."),
        _item("care-line", "heights", location="Heights University Hospital", event="Continuing hospital and emergency department access loss", chronology="august_reporting_on_continuing_prior_loss", source_date="final ED suspension March 14, 2026; August 27, 2026 public record", binding="2026-03-14 continuing; August 27 public record", wording="March 14 remains the original ED suspension date; August 27 is reporting context."),
    ]
    rel = _commit_publication(repo, "care-line", "august-publication", items)

    result = generate_public_artifacts(repo, rel, dispatch="care-line")

    html_text = (repo / "output/site/care-line/source-based-retrospectives/august-publication/index.html").read_text(encoding="utf-8")
    assert result["generation"]["authorized_item_count"] == 4
    assert "restoration or reopening" in html_text
    assert "2026-08-31/2026-09-01" in html_text
    assert "2026-08-31/2026-09-03" in html_text
    assert "2026-03-14 continuing; August 27 public record" in html_text
    assert "not rendered as a newly effective August closure" in html_text
    assert html_text.count('href="https://example.test/care-line/') == 4
