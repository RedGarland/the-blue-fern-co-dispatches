from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bluefern_dispatches.ice_staging_publication import (
    _validate_staging_links,
    stage_ice_publication,
    tree_hashes,
)

NOW = "2026-09-09T16:30:00Z"


def source(slug: str = "primary", publisher: str = "Official Source", url: str | None = None) -> dict:
    return {
        "source_url": url or f"https://official.example/ice/{slug}",
        "publisher": publisher,
        "source_type": "official",
        "tier": 1,
        "published_at": "2026-09-09T12:00:00Z",
        "modified_at": None,
        "date_source": "official_publication_date",
        "date_confidence": "high",
        "retrieved_at": NOW,
        "exact_supporting_passage": f"Official traceable evidence for ICE event {slug}.",
    }


def event(**overrides) -> dict:
    record = {
        "review_status": "QUALIFIES",
        "event_id": "ice-fixture-1",
        "event_date": "2026-09-09",
        "event_type": "ICE detention facility oversight failure",
        "primary_category": "detention",
        "severity": "high",
        "status": "reported",
        "location": {
            "state_or_territory": "CA",
            "county_or_equivalent": "Los Angeles County",
            "city": "Los Angeles",
            "facility_name": "Los Angeles Processing Center",
            "latitude": 34.0522,
            "longitude": -118.2437,
            "location_precision": "exact_facility",
            "geography_source": "official facility address",
            "geography_provenance": "reviewed fixture",
        },
        "impact": {"detained_count": 42},
        "agencies": {"ice": True},
        "sources": [source()],
        "verification_status": "source_traceable",
        "corroboration_count": 1,
        "currentness_status": "current",
        "currentness_confidence": "high",
    }
    record.update(overrides)
    return record


def seed_ice(staging_root: Path) -> dict[str, bytes]:
    ice = staging_root / "ice"
    (ice / "editions" / "2026-09-01").mkdir(parents=True)
    (ice / "editions" / "2026-09-01" / "index.html").write_text("old edition 2026-09-01\n", encoding="utf-8")
    entries = [{
        "edition_date": "2026-09-01",
        "title": "ICE Dispatch - 2026-09-01",
        "href": "editions/2026-09-01/",
        "canonical_path": "/ice/editions/2026-09-01/",
        "event_count": 1,
        "source_count": 1,
        "highest_severity": "medium",
        "editorial_decision": "ELIGIBLE_FOR_EDITION",
        "collection_state": "healthy",
        "guid": "bluefern-ice-2026-09-01",
        "non_public": True,
    }]
    (ice / "archive_entries.json").write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (ice / "index.html").write_text('<a href="editions/2026-09-01/">old</a>\n', encoding="utf-8")
    (ice / "archive.html").write_text('<a href="editions/2026-09-01/">old</a>\n', encoding="utf-8")
    (ice / "rss.xml").write_text("<rss><channel><item><guid>bluefern-ice-2026-09-01</guid></item></channel></rss>\n", encoding="utf-8")
    return {rel: (ice / rel).read_bytes() for rel in ("editions/2026-09-01/index.html",)}


def test_eligible_staging_publication_writes_complete_ice_tree_and_receipt(tmp_path: Path) -> None:
    result = stage_ice_publication(
        [event()],
        edition_date="2026-09-09",
        collection_state="healthy",
        staging_root=tmp_path,
        generated_at=NOW,
        source_commit="abc123",
    )

    assert result.status == "staged"
    assert result.public_side_effects is False
    assert (tmp_path / "ice" / "index.html").exists()
    assert (tmp_path / "ice" / "archive.html").exists()
    assert (tmp_path / "ice" / "rss.xml").exists()
    assert (tmp_path / "ice" / "editions" / "2026-09-09" / "index.html").exists()
    assert (tmp_path / "ice" / "editions" / "2026-09-09" / "map_payload.json").exists()
    assert (tmp_path / "ice" / "editions" / "2026-09-09" / "manifest.json").exists()
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["archive_changed"] is True
    assert receipt["rss_changed"] is True
    assert receipt["index_changed"] is True
    assert receipt["map_payload_changed"] is True
    assert receipt["source_commit"] == "abc123"
    assert receipt["public_side_effects"] is False


def test_no_publication_needed_is_exact_noop_for_ice_tree(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before = tree_hashes(tmp_path / "ice")

    result = stage_ice_publication(
        [],
        edition_date="2026-09-09",
        collection_state="healthy",
        staging_root=tmp_path,
        generated_at=NOW,
    )

    assert result.status == "no_op"
    assert tree_hashes(tmp_path / "ice") == before
    assert not (tmp_path / "ice" / "editions" / "2026-09-09").exists()
    assert json.loads(result.receipt_path.read_text(encoding="utf-8"))["reason"] == "NO_PUBLICATION_NEEDED"


def test_insufficient_verified_evidence_is_exact_noop(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before = tree_hashes(tmp_path / "ice")

    result = stage_ice_publication(
        [event(review_status="INSUFFICIENT_EVIDENCE")],
        edition_date="2026-09-09",
        collection_state="healthy",
        staging_root=tmp_path,
        generated_at=NOW,
        decision_override="INSUFFICIENT_VERIFIED_EVIDENCE",
    )

    assert result.status == "no_op"
    assert tree_hashes(tmp_path / "ice") == before
    assert json.loads(result.receipt_path.read_text(encoding="utf-8"))["reason"] == "INSUFFICIENT_VERIFIED_EVIDENCE"


def test_degraded_collection_stages_with_explicit_warning(tmp_path: Path) -> None:
    result = stage_ice_publication([event()], edition_date="2026-09-09", collection_state="degraded", staging_root=tmp_path, generated_at=NOW)

    manifest = json.loads((tmp_path / "ice" / "editions" / "2026-09-09" / "manifest.json").read_text(encoding="utf-8"))
    assert result.status == "staged"
    assert manifest["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert manifest["collection_state"] == "degraded"
    assert manifest["collection_warning"]


def test_collection_failed_is_exact_noop(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before = tree_hashes(tmp_path / "ice")

    result = stage_ice_publication([event()], edition_date="2026-09-09", collection_state="failed", staging_root=tmp_path, generated_at=NOW)

    assert result.status == "no_op"
    assert tree_hashes(tmp_path / "ice") == before
    assert json.loads(result.receipt_path.read_text(encoding="utf-8"))["reason"] == "COLLECTION_FAILED"


def test_archive_rss_and_index_are_monotonic_and_same_date_rerun_idempotent(tmp_path: Path) -> None:
    old_files = seed_ice(tmp_path)

    first = stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    after_first = tree_hashes(tmp_path / "ice")
    second = stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    after_second = tree_hashes(tmp_path / "ice")

    entries = json.loads((tmp_path / "ice" / "archive_entries.json").read_text(encoding="utf-8"))
    rss = (tmp_path / "ice" / "rss.xml").read_text(encoding="utf-8")
    index = (tmp_path / "ice" / "index.html").read_text(encoding="utf-8")

    assert [entry["edition_date"] for entry in entries] == ["2026-09-09", "2026-09-01"]
    assert rss.count("bluefern-ice-2026-09-09") == 1
    assert rss.count("bluefern-ice-2026-09-01") == 1
    assert "Latest staging edition" in index and "2026-09-09" in index and "2026-09-01" in index
    assert (tmp_path / "ice" / "editions" / "2026-09-01" / "index.html").read_bytes() == old_files["editions/2026-09-01/index.html"]
    assert after_first == after_second
    assert first.status == second.status == "staged"


def test_archive_only_gains_intended_new_entry(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before_entries = json.loads((tmp_path / "ice" / "archive_entries.json").read_text(encoding="utf-8"))

    result = stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    after_entries = json.loads((tmp_path / "ice" / "archive_entries.json").read_text(encoding="utf-8"))

    assert result.status == "staged"
    assert len(after_entries) == len(before_entries) + 1
    assert {entry["edition_date"] for entry in after_entries} == {"2026-09-09", "2026-09-01"}
    assert after_entries[1] == before_entries[0]


def test_rss_preserves_old_guid_and_adds_new_guid_once(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before_rss = (tmp_path / "ice" / "rss.xml").read_text(encoding="utf-8")

    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    after_rss = (tmp_path / "ice" / "rss.xml").read_text(encoding="utf-8")

    assert "bluefern-ice-2026-09-01" in before_rss
    assert after_rss.count("bluefern-ice-2026-09-01") == 1
    assert after_rss.count("bluefern-ice-2026-09-09") == 1
    assert after_rss.index("bluefern-ice-2026-09-09") < after_rss.index("bluefern-ice-2026-09-01")


def test_map_payload_integrates_locations_without_duplicate_entries(tmp_path: Path) -> None:
    exact = event(event_id="same-1", sources=[source("a"), source("b", "Second Official", "https://official.example/ice/b")])
    city = event(
        event_id="city-1",
        event_type="ICE operational development in city",
        event_date="2026-09-08",
        primary_category="policy_operations",
        severity="medium",
        location={"state_or_territory": "NY", "city": "New York", "location_precision": "city", "geography_source": "official notice", "geography_provenance": "reviewed fixture"},
        sources=[source("city")],
    )
    territory = event(
        event_id="territory-1",
        event_type="ICE legal oversight development in Puerto Rico",
        event_date="2026-09-07",
        primary_category="legal_oversight_accountability",
        severity="medium",
        location={"state_or_territory": "PR", "location_precision": "state_or_territory", "geography_source": "official notice", "geography_provenance": "reviewed fixture"},
        sources=[source("pr")],
    )
    unmapped = event(
        event_id="unmapped-1",
        event_type="ICE legal oversight development with unresolved geography",
        event_date="2026-09-06",
        primary_category="legal_oversight_accountability",
        severity="medium",
        location={},
        sources=[source("unmapped")],
    )

    stage_ice_publication([exact, city, territory, unmapped], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    payload = json.loads((tmp_path / "ice" / "editions" / "2026-09-09" / "map_payload.json").read_text(encoding="utf-8"))
    by_id = {item["canonical_event_id"]: item for item in payload["events"]}

    assert len(payload["events"]) == 4
    assert by_id["same-1"]["map_readiness"] == "MAPPABLE_EXACT"
    assert by_id["city-1"]["map_readiness"] == "MAPPABLE_CITY"
    assert by_id["territory-1"]["state_or_territory"] == "PR"
    assert by_id["territory-1"]["latitude"] is None
    assert by_id["unmapped-1"]["map_readiness"] == "NOT_YET_MAPPABLE"


def test_update_correction_labels_and_source_lineage_survive_staging(tmp_path: Path) -> None:
    update = event(review_status="UPDATE_TO_EXISTING_EVENT", event_id="update-1", event_type="ICE detention update", sources=[source("update")])
    correction = event(
        review_status="CORRECTION",
        event_id="correction-1",
        event_date="2026-09-08",
        event_type="ICE correction to prior record",
        supersedes=["old-event"],
        location={"state_or_territory": "TX", "city": "El Paso", "location_precision": "city", "geography_source": "official notice", "geography_provenance": "reviewed fixture"},
        sources=[source("correction")],
    )

    stage_ice_publication([update, correction], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)
    html = (tmp_path / "ice" / "editions" / "2026-09-09" / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "ice" / "editions" / "2026-09-09" / "manifest.json").read_text(encoding="utf-8"))

    assert "UPDATE" in html
    assert "CORRECTION" in html
    assert manifest["accepted_event_count"] == 2


def test_link_integrity_rejects_live_style_internal_links(tmp_path: Path) -> None:
    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)

    assert _validate_staging_links(tmp_path / "ice") == []
    (tmp_path / "ice" / "bad.html").write_text('<a href="/ice/editions/2026-09-09/">bad</a>', encoding="utf-8")
    assert _validate_staging_links(tmp_path / "ice")


def test_unrelated_dispatches_are_not_touched(tmp_path: Path) -> None:
    for dispatch in ("gaza", "food-line", "care-line", "cascadia", "american-pressure"):
        root = tmp_path / dispatch
        root.mkdir()
        (root / "index.html").write_text(f"{dispatch} sentinel\n", encoding="utf-8")
        (root / "archive.html").write_text(f"{dispatch} archive\n", encoding="utf-8")
        (root / "rss.xml").write_text(f"{dispatch} rss\n", encoding="utf-8")
    before = {dispatch: tree_hashes(tmp_path / dispatch) for dispatch in ("gaza", "food-line", "care-line", "cascadia", "american-pressure")}

    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)

    assert {dispatch: tree_hashes(tmp_path / dispatch) for dispatch in before} == before


def test_failure_after_candidate_generation_leaves_staging_tree_coherent(tmp_path: Path) -> None:
    seed_ice(tmp_path)
    before = tree_hashes(tmp_path / "ice")

    result = stage_ice_publication(
        [event()],
        edition_date="2026-09-09",
        collection_state="healthy",
        staging_root=tmp_path,
        generated_at=NOW,
        simulate_failure_after_candidate=True,
    )

    assert result.status == "failed_before_promote"
    assert tree_hashes(tmp_path / "ice") == before
    assert not (tmp_path / "ice" / "editions" / "2026-09-09").exists()


def test_deterministic_rerun_from_scratch(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=first_root, generated_at=NOW)
    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=second_root, generated_at=NOW)

    assert tree_hashes(first_root / "ice") == tree_hashes(second_root / "ice")


def test_staging_storage_hygiene(tmp_path: Path) -> None:
    stage_ice_publication([event()], edition_date="2026-09-09", collection_state="healthy", staging_root=tmp_path, generated_at=NOW)

    total_bytes = sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file())
    assert total_bytes < 250_000
    assert not any(part == ".git" for path in tmp_path.rglob("*") for part in path.parts)


def test_staging_cli_writes_no_public_side_effects(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps([event()], indent=2), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_ice_staging_publication.py",
            "--reviewed-events",
            str(reviewed),
            "--edition-date",
            "2026-09-09",
            "--collection-state",
            "healthy",
            "--staging-root",
            str(tmp_path / "stage"),
            "--generated-at",
            NOW,
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "staged"
    assert payload["public_side_effects"] is False
