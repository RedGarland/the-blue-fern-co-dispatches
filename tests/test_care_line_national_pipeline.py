from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_national_pipeline import (
    WORKING_REVIEW_QUEUE_PATH,
    adapt_pressure_registry,
    build_review_queue,
    cluster_candidates,
    collectable_sources,
    load_canonical_registry,
    run_collection_attempt,
    run_national_pipeline,
    validate_review_snapshot,
    write_review_snapshot,
)
from bluefern_dispatches.care_line_record import CareLineReviewedRecord


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    return root


def test_collectable_sources_use_canonical_registry_and_skip_disabled(repo_copy: Path) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    rows = collectable_sources(registry)

    assert rows
    assert all(row["source_id"] for row in rows)
    assert all(row["readiness"] != "DISABLED" for row in rows)
    national_ids = [row["source_id"] for row in rows if row["national_single_execution"]]
    assert len(national_ids) == len(set(national_ids))


def test_pressure_registry_is_compatibility_input_only(repo_copy: Path) -> None:
    adapted = adapt_pressure_registry(repo_copy)

    assert adapted["compatibility_role"] == "compatibility_input_only"
    assert adapted["source_count"] >= 25
    assert all(row["legacy_registry_path"].endswith("pressure_source_registry.json") for row in adapted["sources"])


def test_disabled_sources_are_not_selected_even_when_registry_is_loaded_with_disabled(repo_copy: Path) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected_ids = {row["source_id"] for row in collectable_sources(registry)}
    disabled_ids = {source.source_id for source in registry.sources if not source.enabled}

    assert disabled_ids
    assert not (selected_ids & disabled_ids)


def test_manual_only_sources_are_not_treated_as_automated(repo_copy: Path) -> None:
    registry_path = repo_copy / "data" / "dispatches" / "care-line" / "source_registry.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["sources"].append(
        {
            "source_id": "manual-test-source",
            "name": "Manual Test Source",
            "publisher": "Manual Test Source",
            "source_type": "local_publisher",
            "feed_url": "https://example.org/manual",
            "homepage_url": "https://example.org/manual",
            "state": "WA",
            "geographic_scope": "state",
            "organization_type": "test",
            "care_line_topics": ["hospital"],
            "authority_level": "secondary",
            "expected_update_frequency": "weekly",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "test",
            "historical_depth": "current",
            "notes": "",
            "created_at": "2026-08-04T00:00:00Z",
            "updated_at": "2026-08-04T00:00:00Z",
            "allowed_hosts": ["example.org"],
            "source_category": "local_health_reporting",
            "collection_method": "manual_review",
            "searchability": "manual_only",
            "what_changes_it_reports": ["hospital"],
            "limitations": "",
            "page_active_status": "active",
            "explicit_source_dates": True,
            "item_permalink_available": True,
            "archives_distinguishable_from_current": True,
            "last_verified_date": "2026-08-04",
            "jurisdiction_scope": "WA",
        }
    )
    registry_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected_ids = {row["source_id"] for row in collectable_sources(registry)}

    assert "manual-test-source" not in selected_ids


def test_collection_attempt_writes_immutable_raw_artifacts_and_source_id(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    source_row = collectable_sources(registry, include_partial=False)[0]
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (
            b"<rss><channel><item><title>Clinic to close</title><link>https://example.org/story</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>The clinic will close and patients will be redirected.</description></item></channel></rss>",
            {"http_status": 200},
        ),
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.parse_source_items",
        lambda source, payload: [
            {
                "title": "Clinic to close",
                "url": "https://example.org/story",
                "published_at": "Tue, 04 Aug 2026 12:00:00 GMT",
                "description": "The clinic will close and patients will be redirected.",
                "source": source.publisher,
                "id": "item-1",
            }
        ],
    )

    result = run_collection_attempt(repo_copy, run_date="2026-08-04", run_id="20260804-test-01", source_row=source_row)

    assert result["attempt"]["source_id"] == source_row["source_id"]
    assert result["candidates"][0]["source_id"] == source_row["source_id"]
    attempt_path = repo_copy / "data" / "dispatches" / "care-line" / "collection-runs" / "2026-08-04" / "20260804-test-01" / f"{source_row['source_id']}.attempt.json"
    assert any(path.name.endswith(".attempt.json") for path in attempt_path.parent.iterdir())


def test_partial_source_failure_isolated_and_recorded(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    partial_row = next(row for row in collectable_sources(registry, include_partial=True) if row["readiness"] == "AUTOMATED_PARTIAL")
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = run_collection_attempt(repo_copy, run_date="2026-08-04", run_id="20260804-test-02", source_row=partial_row)

    assert result["failure"].startswith("RuntimeError:")
    assert result["records"] == []
    assert result["candidates"] == []


def test_cluster_candidates_isolates_same_system_names_across_states() -> None:
    rows = []
    for state in ("TX", "OK"):
        record = CareLineReviewedRecord.model_validate(
            {
                "producer_record_id": f"rec-{state}",
                "source_url": f"https://example.org/{state.lower()}",
                "source_title": f"Mercy closes service in {state}",
                "source_publisher": "Example",
                "source_publication_date": "2026-08-04",
                "supporting_passage": "Mercy says the service will close.",
                "raw_payload_hash": f"hash-{state}",
                "event_type": "service_closure",
                "announcement_date": "2026-08-04",
                "service_line": "labor_and_delivery",
                "facility_name": "Mercy",
                "provider_name": "Mercy",
                "city": "Example City",
                "state": state,
                "country_code": "US",
                "geographic_scope": "city",
                "permanence": "permanent",
                "review_status": "reviewed",
                "record_status": "active",
                "public_status": "not_public",
                "universal_event_status": "needs_normalization_review",
                "field_provenance": {},
            }
        )
        rows.append(
            {
                "candidate_id": f"cand-{state}",
                "source_id": f"source-{state}",
                "duplicate_cluster_hints": {
                    "cluster_id": f"cluster-{state}",
                },
                "normalized_record": record.model_dump(mode="json"),
            }
        )

    clustered = cluster_candidates(rows)

    assert clustered["cluster_count"] == 2


def test_review_queue_prioritizes_ed_and_maternity_losses() -> None:
    reviewed = CareLineReviewedRecord.model_validate(
        {
            "producer_record_id": "rec-1",
            "source_url": "https://example.org/story",
            "source_title": "Hospital ends labor and delivery",
            "source_publisher": "Example",
            "source_publication_date": "2026-08-04",
            "supporting_passage": "The hospital will end labor and delivery services immediately.",
            "raw_payload_hash": "hash",
            "event_type": "service_closure",
            "announcement_date": "2026-08-04",
            "service_line": "labor_and_delivery",
            "facility_name": "County Hospital",
            "provider_name": "County Hospital",
            "city": "Town",
            "state": "IA",
            "country_code": "US",
            "geographic_scope": "city",
            "permanence": "permanent",
            "review_status": "reviewed",
            "record_status": "active",
            "public_status": "not_public",
            "universal_event_status": "needs_normalization_review",
            "field_provenance": {},
        }
    )
    queue = build_review_queue(
        [
            {
                "candidate_id": "cand-1",
                "collection_run_id": "run-1",
                "source_artifact_path": "artifact.json",
                "first_seen": "2026-08-04T00:00:00Z",
                "last_seen": "2026-08-04T00:00:00Z",
                "duplicate_cluster_hints": {"cluster_id": "cluster-1"},
                "normalized_record": reviewed.model_dump(mode="json"),
            }
        ],
        edition_date="2026-08-04",
    )

    assert queue["items"][0]["review_priority"] == "CRITICAL"


def test_review_snapshot_hashing_and_legacy_fallback(repo_copy: Path) -> None:
    queue_payload = {"schema_version": "test", "items": []}
    snapshot = write_review_snapshot(repo_copy, edition_date="2026-08-04", queue_payload=queue_payload)

    validated = validate_review_snapshot(
        repo_copy,
        edition_date="2026-08-04",
        snapshot_path=Path(snapshot["snapshot_path"]).relative_to(repo_copy).as_posix(),
        snapshot_sha256=snapshot["snapshot_sha256"],
    )
    assert validated["mode"] == "snapshot"

    snapshot_file = repo_copy / Path(snapshot["snapshot_path"]).relative_to(repo_copy)
    snapshot_file.unlink()
    fallback = repo_copy / WORKING_REVIEW_QUEUE_PATH
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text(json.dumps({"schema_version": "legacy", "items": []}), encoding="utf-8")

    validated_fallback = validate_review_snapshot(
        repo_copy,
        edition_date="2026-08-04",
        snapshot_path=Path(snapshot["snapshot_path"]).relative_to(repo_copy).as_posix(),
        snapshot_sha256=snapshot["snapshot_sha256"],
        allow_legacy_fallback=True,
        fallback_queue_path=WORKING_REVIEW_QUEUE_PATH.as_posix(),
    )
    assert validated_fallback["mode"] == "legacy_fallback"


def test_national_pipeline_rerun_preserves_first_seen_and_updates_last_seen(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected = collectable_sources(registry, include_partial=False)[:1]
    source_row = selected[0]
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.collectable_sources",
        lambda registry, include_partial=True, include_manual_review=False: selected,
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (b"<payload/>", {"http_status": 200}),
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.parse_source_items",
        lambda source, payload: [
            {
                "title": "Clinic to close",
                "url": "https://example.org/story",
                "published_at": "Tue, 04 Aug 2026 12:00:00 GMT",
                "description": "The clinic will close and patients will be redirected.",
                "source": source.publisher,
                "id": "item-1",
            }
        ],
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.discovery_record_from_direct_item",
        lambda item, source, discovery_date, rank, collected_at: {
            "discovery_record_id": "record-1",
            "article_title": item["title"],
            "article_url": item["url"],
            "article_publisher": source.publisher,
            "article_publication_date": "2026-08-04",
            "article_description": item["description"],
            "discovery_collected_at": collected_at,
            "source_id": source.source_id,
            "source_name": source.name,
            "source_registry_publisher": source.publisher,
            "state": source.state,
            "geographic_scope": source.geographic_scope,
        },
    )
    first = run_national_pipeline(repo_copy, run_date="2026-08-04", include_partial=False, source_limit=1)
    first_candidate = first["candidate_registry"]["candidates"][0]
    second = run_national_pipeline(repo_copy, run_date="2026-08-04", include_partial=False, source_limit=1)
    second_candidate = second["candidate_registry"]["candidates"][0]

    assert first_candidate["candidate_id"] == second_candidate["candidate_id"]
    assert first_candidate["first_seen"] == second_candidate["first_seen"]
    assert second_candidate["last_seen"] >= first_candidate["last_seen"]
