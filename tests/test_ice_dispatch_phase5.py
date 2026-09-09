from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bluefern_dispatches.ice_dispatch import (
    generate_ice_edition_candidate,
    ice_edition_items,
    normalize_ice_candidate,
    reviewed_ice_events,
)


FIXED_NOW = "2026-09-09T16:00:00Z"


def source(
    slug: str = "primary",
    *,
    publisher: str = "Official Source",
    tier: int = 1,
    url: str | None = None,
    published_at: str = "2026-09-09T12:00:00Z",
) -> dict:
    return {
        "source_url": url or f"https://example.gov/ice/{slug}",
        "publisher": publisher,
        "source_type": "official",
        "tier": tier,
        "published_at": published_at,
        "date_source": "official_publication_date",
        "date_confidence": "high",
        "retrieved_at": FIXED_NOW,
        "exact_supporting_passage": f"Traceable evidence for {slug} involving ICE operations.",
    }


def reviewed_event(**overrides) -> dict:
    record = {
        "review_status": "QUALIFIES",
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


def test_high_reviewed_event_generates_non_public_edition_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "candidate"
    result = generate_ice_edition_candidate(
        [reviewed_event()],
        edition_date="2026-09-09",
        collection_state="healthy",
        output_dir=output_dir,
        generated_at=FIXED_NOW,
    )

    assert result["manifest"]["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert result["manifest"]["input_contract"] == "reviewed_canonical_events_only"
    assert result["manifest"]["public_side_effects"] is False
    assert result["items"][0]["source_count"] == 1
    assert result["items"][0]["map_readiness"] == "MAPPABLE_EXACT"
    assert (output_dir / "edition_manifest.json").exists()
    assert (output_dir / "index.html").exists()
    assert (output_dir / "archive_candidate.json").exists()
    assert (output_dir / "rss_candidate.json").exists()
    assert (output_dir / "map_payload.json").exists()
    assert not (tmp_path / "ice").exists()


def test_multiple_medium_events_can_cross_publication_threshold() -> None:
    records = [
        reviewed_event(
            event_type=f"ICE operational policy change {idx}",
            primary_category="policy_operations",
            severity="medium",
            event_date=f"2026-09-0{idx}",
            sources=[source(f"medium-{idx}")],
        )
        for idx in (7, 8, 9)
    ]

    result = generate_ice_edition_candidate(records, edition_date="2026-09-09", collection_state="healthy", generated_at=FIXED_NOW)

    assert result["manifest"]["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert result["manifest"]["editorial_decision_reason"] == "multiple coherent medium ICE events"
    assert [item["event_date"] for item in result["items"]] == ["2026-09-09", "2026-09-08", "2026-09-07"]


def test_low_only_reviewed_events_no_publication_candidate_without_fake_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "candidate"
    result = generate_ice_edition_candidate(
        [reviewed_event(severity="low", primary_category="verified_community_impact", event_type="Limited ICE community impact report")],
        edition_date="2026-09-09",
        collection_state="healthy",
        output_dir=output_dir,
        generated_at=FIXED_NOW,
    )

    assert result["manifest"]["editorial_decision"] == "NO_PUBLICATION_NEEDED"
    assert result["manifest"]["accepted_event_count"] == 1
    assert result["manifest"]["rendered_event_count"] == 0
    assert (output_dir / "edition_manifest.json").exists()
    assert not (output_dir / "index.html").exists()
    assert "archive_candidate" not in result


def test_review_status_contract_excludes_blocked_and_tier3_uncorroborated() -> None:
    records = [
        reviewed_event(review_status="NEEDS_CORROBORATION"),
        reviewed_event(review_status="DUPLICATE"),
        reviewed_event(review_status="OUT_OF_SCOPE"),
        reviewed_event(review_status="INSUFFICIENT_CURRENTNESS"),
        reviewed_event(review_status="INSUFFICIENT_EVIDENCE"),
        reviewed_event(review_status="DOES_NOT_QUALIFY"),
        reviewed_event(sources=[source("advocacy", publisher="Community Source", tier=3)], corroboration_count=1),
    ]

    result = generate_ice_edition_candidate(records, edition_date="2026-09-09", collection_state="healthy", generated_at=FIXED_NOW)

    assert result["manifest"]["editorial_decision"] == "NO_PUBLICATION_NEEDED"
    assert result["manifest"]["accepted_event_count"] == 0
    reasons = {row["reason"] for row in result["manifest"]["review_exclusions"]}
    assert "review_status_not_edition_eligible" in reasons
    assert "tier3_requires_corroboration" in reasons


def test_duplicate_observations_collapse_to_one_card_and_preserve_unique_sources() -> None:
    first = reviewed_event(event_id="obs-1", sources=[source("a", publisher="Official A")])
    second = reviewed_event(event_id="obs-2", sources=[source("b", publisher="Official B", url="https://example.gov/ice/second")])
    duplicate = reviewed_event(event_id="obs-3", sources=[source("a", publisher="Official A")])

    canonical, exclusions = reviewed_ice_events([first, second, duplicate])
    items = ice_edition_items(canonical)

    assert len(items) == 1
    assert {row["publisher"] for row in items[0]["sources"]} == {"Official A", "Official B"}
    assert len(items[0]["sources"]) == 2
    assert any(row["reason"] == "collapsed_into_canonical_event" for row in exclusions)


def test_update_and_correction_relationships_are_rendered() -> None:
    update = reviewed_event(review_status="UPDATE_TO_EXISTING_EVENT", event_id="update-1")
    correction = reviewed_event(
        review_status="CORRECTION",
        event_id="correction-1",
        event_type="ICE correction to prior detention facility report",
        event_date="2026-09-08",
        supersedes=["ice-old-fact"],
        location={
            "state_or_territory": "TX",
            "county_or_equivalent": "El Paso County",
            "city": "El Paso",
            "facility_name": "El Paso Processing Center",
            "location_precision": "city",
            "geography_source": "official notice",
            "geography_provenance": "reviewed fixture",
        },
        sources=[source("correction")],
    )

    result = generate_ice_edition_candidate([update, correction], edition_date="2026-09-09", collection_state="healthy", generated_at=FIXED_NOW)

    relationships = {item["event_id"]: item["edition_relationship"] for item in result["items"]}
    assert relationships["update-1"] == "UPDATE"
    assert relationships["correction-1"] == "CORRECTION"
    assert "UPDATE" in result["html"]
    assert "CORRECTION" in result["html"]


def test_degraded_collection_state_is_preserved_separately_from_editorial_decision() -> None:
    result = generate_ice_edition_candidate(
        [reviewed_event()],
        edition_date="2026-09-09",
        collection_state="degraded",
        generated_at=FIXED_NOW,
    )

    assert result["manifest"]["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert result["manifest"]["collection_state"] == "degraded"
    assert result["manifest"]["collection_warning"]


def test_map_payload_preserves_territories_unmappable_events_and_source_lineage() -> None:
    puerto_rico = reviewed_event(
        event_id="pr-event",
        event_type="ICE policy operation in Puerto Rico",
        primary_category="policy_operations",
        severity="medium",
        location={"state_or_territory": "PR", "location_precision": "state_or_territory", "geography_source": "official notice", "geography_provenance": "reviewed fixture"},
        sources=[source("pr")],
    )
    unmappable = reviewed_event(
        event_id="unmapped-event",
        event_type="ICE legal oversight development with unresolved geography",
        primary_category="legal_oversight_accountability",
        severity="medium",
        location={},
        sources=[source("unmapped")],
    )

    result = generate_ice_edition_candidate([puerto_rico, unmappable], edition_date="2026-09-09", collection_state="healthy", generated_at=FIXED_NOW)
    payload = result["map_payload"]
    by_id = {event["canonical_event_id"]: event for event in payload["events"]}

    assert by_id["pr-event"]["state_or_territory"] == "PR"
    assert by_id["pr-event"]["latitude"] is None
    assert by_id["pr-event"]["geography_provenance"] == "reviewed fixture"
    assert by_id["unmapped-event"]["map_readiness"] == "NOT_YET_MAPPABLE"


def test_archive_rss_and_file_outputs_are_deterministic(tmp_path: Path) -> None:
    records = [reviewed_event()]
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = generate_ice_edition_candidate(records, edition_date="2026-09-09", collection_state="healthy", output_dir=first_dir, generated_at=FIXED_NOW)
    second = generate_ice_edition_candidate(records, edition_date="2026-09-09", collection_state="healthy", output_dir=second_dir, generated_at=FIXED_NOW)

    assert first["archive_candidate"] == second["archive_candidate"]
    assert first["rss_candidate"] == second["rss_candidate"]
    assert first["map_payload"] == second["map_payload"]
    assert (first_dir / "index.html").read_text(encoding="utf-8") == (second_dir / "index.html").read_text(encoding="utf-8")
    assert sum(path.stat().st_size for path in first_dir.iterdir() if path.is_file()) < 100_000


def test_non_public_cli_reads_reviewed_events_and_writes_candidate(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.json"
    output_dir = tmp_path / "out"
    reviewed.write_text(json.dumps([reviewed_event()], indent=2), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_ice_edition_candidate.py",
            "--reviewed-events",
            str(reviewed),
            "--edition-date",
            "2026-09-09",
            "--collection-state",
            "healthy",
            "--output-dir",
            str(output_dir),
            "--generated-at",
            FIXED_NOW,
        ],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert summary["public_side_effects"] is False
    assert (output_dir / "edition_manifest.json").exists()
