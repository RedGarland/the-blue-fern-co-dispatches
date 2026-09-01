from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_historical_recovery import (
    HISTORICAL_DEDUPE_SURFACES,
    FoodLineHistoricalRecoveryError,
    build_recovery,
    import_recovery,
    migrate_recovery_to_four_tiers,
    parse_aggregate_handoff,
    record_historical_event_review,
    sha256_bytes,
)
from scripts.import_food_line_historical_recovery import main


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repository(root: Path, files: list[str]) -> str:
    _git(root, "init")
    _git(root, "add", "--", *files)
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return _git(root, "rev-parse", "HEAD")


def _case(tmp_path: Path) -> dict:
    root = tmp_path / "source"
    pages = tmp_path / "pages"
    root.mkdir()
    pages.mkdir()
    finding = {
        "title": "County pantry suspends new client intake",
        "publisher": "County Pantry",
        "source_url": "https://pantry.example.org/updates/intake",
        "canonical_source_url": "https://pantry.example.org/updates/intake",
        "source_published_at": "2026-08-10",
        "exact_supporting_passage": "The pantry suspended new-client intake for August.",
        "summary": "New-client intake was suspended while existing clients continued service.",
        "location_name": "Example County",
        "state": "AZ",
        "location_scope": "county",
        "affected_groups": ["prospective pantry clients"],
        "pressure_type": "service_reduction",
        "confidence": "high",
        "source_role": "official_provider",
        "evidence_level": "direct_official_statement",
        "agent_query_context": {"query": "pantry intake suspension"},
        "review_status": "pending_review",
        "exclusion_reason": None,
        "raw_agent_payload": {"uncertainty": "No turnaway count was published."},
    }
    envelope = {
        "schema_version": "food_line_agent_finding_v1",
        "agent_name": "fixture-agent",
        "agent_run_id": "fixture-run",
        "started_at": "2026-08-11T01:00:00Z",
        "completed_at": "2026-08-11T01:05:00Z",
        "search_window": {"date_from": "2026-08-01", "date_to": "2026-08-10"},
        "findings": [finding],
        "coverage_notes": "Private historical discovery.",
    }
    raw = ("```json\n" + json.dumps(envelope) + "\n```\n").encode()
    input_path = tmp_path / "aggregate.md"
    input_path.write_bytes(raw)
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    finding_id = parsed["findings"][0]["finding_id"]
    spec = {
        "schema_version": "food_line_historical_event_cluster_spec_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": "2026-08",
        "reviewed_by": "fixture-reviewer",
        "reviewed_at": "2026-09-01T00:00:00Z",
        "publication_approval": False,
        "unassigned_finding_ids": [],
        "clusters": [
            {
                "location": "Example County, Arizona",
                "organization": "County Pantry",
                "event_start_date": "2026-08-01",
                "event_end_date": "2026-08-31",
                "pressure_category": "service reduction",
                "underlying_development": "new-client pantry intake suspended for August",
                "affected_population": ["prospective pantry clients"],
                "finding_ids": [finding_id],
                "primary_finding_id": finding_id,
                "measured_access_consequence": {
                    "type": "disaster_household_food_loss",
                    "description": "New-client pantry intake was suspended for August.",
                    "measurement": "one-month suspension",
                    "supporting_finding_ids": [finding_id],
                },
                "uncertainty": {
                    "condition": {"status": "resolved", "note": "The suspension was directly stated."},
                    "causal": {"status": "unresolved", "note": "The source did not state a cause."},
                    "severity": {"status": "unresolved", "note": "No turnaway count was published."},
                },
                "prior_publication_match": {"status": "none"},
                "proposed_disposition": "confirmed_historical_review_candidate",
                "disposition_reason": "A direct service suspension is source-backed.",
                "unresolved_requirement": None,
                "exclusion_rule": None,
            }
        ],
    }
    spec_path = tmp_path / "clusters.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    predecessor_artifacts = build_recovery(
        root,
        input_path,
        spec_path,
        pages_root=None,
        captured_at="2026-08-31T23:45:00Z",
        run_month="2026-08",
    )
    for artifact_name, row_name in (
        ("event_cluster_manifest.json", "clusters"),
        ("priority_confirmed_candidates.json", "candidates"),
    ):
        predecessor_artifacts[artifact_name][row_name][0]["priority"] = 5
    predecessor = import_recovery(
        root,
        predecessor_artifacts,
        cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()),
    )
    migrated = migrate_recovery_to_four_tiers(
        root,
        input_path,
        spec_path,
        predecessor_artifact_set_sha256=predecessor["artifact_set_sha256"],
        implementation_source_commit="a" * 40,
        captured_at="2026-08-31T23:45:00Z",
        run_month="2026-08",
    )
    migration = Path(migrated["recovery_path"])
    event_manifest = json.loads((migration / "event_cluster_manifest.json").read_text())
    event = event_manifest["clusters"][0]
    source_head = _init_repository(root, ["data"])
    (pages / "food-line").mkdir()
    (pages / "food-line" / "index.html").write_text("Food Line history", encoding="utf-8")
    pages_head = _init_repository(pages, ["food-line/index.html"])
    return {
        "root": root,
        "pages": pages,
        "source_head": source_head,
        "pages_head": pages_head,
        "migration": migration,
        "identity": migrated["successor_identity_sha256"],
        "artifact_set": migrated["artifact_set_sha256"],
        "event": event,
    }


def _review(case: dict, *, decision: str = "confirmed") -> tuple[Path, dict]:
    event = case["event"]
    source = event["sources"][0]
    confirmed = decision == "confirmed"
    dedupe_result = {
        "duplicate_or_corroboration": "corroborating_source",
        "already_published": "already_published",
    }.get(decision, "no_match")
    matched = None
    if dedupe_result != "no_match":
        matched = {
            "repository": "pages",
            "artifact_path": "food-line/index.html",
            "reference_id": "food-line-existing-story",
            "event_fingerprint": "sha256:" + "b" * 64,
            "canonical_source_url": source["canonical_source_url"],
        }
    payload = {
        "schema_version": "food_line_historical_event_editorial_review_v1",
        "review_type": "historical_event_editorial_review",
        "source_head": case["source_head"],
        "pages_head": case["pages_head"],
        "recovery_identity_sha256": case["identity"],
        "recovery_artifact_set_sha256": case["artifact_set"],
        "event_id": event["event_id"],
        "event_fingerprint": event["event_fingerprint"],
        "decision": decision,
        "decision_reason": "The source directly documents the bounded service condition.",
        "reviewed_by": "fixture-editor",
        "reviewed_at": "2026-09-01T02:00:00Z",
        "evidence_references": [
            {
                "canonical_source_url": source["canonical_source_url"],
                "publisher": source["publisher"],
                "source_published_at": source["source_published_at"],
                "role": "principal",
                "exact_supporting_passages": [source["exact_supporting_passage"]],
            }
        ],
        "event_assessment": {
            "location": event["location_display"],
            "organization": event["organization_display"],
            "affected_population": event["affected_population"],
            "event_start_date": event["event_start_date"],
            "event_end_date": event["event_end_date"],
            "service": "Recurring pantry intake for prospective clients.",
            "measurable_change": event["measured_access_consequence"],
            "attribution": "County Pantry directly stated that new-client intake was suspended.",
            "uncertainty": event["uncertainty"],
        },
        "dedupe_assessment": {
            "result": dedupe_result,
            "checked_surfaces": sorted(HISTORICAL_DEDUPE_SURFACES),
            "matched_reference": matched,
            "continued_condition_assessment": "No distinct continued condition or material update was identified.",
        },
        "publication_copy": {
            "headline": "County pantry paused new-client intake during August",
            "summary": "A county pantry said it suspended new-client intake during August while continuing service for existing clients; it did not report a turnaway count.",
            "source_links": [source["canonical_source_url"]],
        } if confirmed else None,
        "recommended_batch": {
            "batch_id": "food-line-august-2026-retrospective-01",
            "order": 1,
            "edition_title": "Food Line: August access losses recovered",
            "edition_introduction": "This retrospective documents food-access changes reported during August and preserves the limits of the available evidence.",
        } if confirmed else None,
        "archive_mutation_authorized": False,
        "intake_authorized": False,
        "queue_authorized": False,
        "generation_authorized": False,
        "approval_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
    }
    review_path = (
        case["root"]
        / "data"
        / "agent-history"
        / "food-line"
        / "reviews"
        / "recovery-submissions"
        / f"{event['event_id']}.json"
    )
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return review_path, payload


def _record(case: dict, review_path: Path, payload: dict, *, dry_run: bool = False) -> dict:
    return record_historical_event_review(
        case["root"],
        case["pages"],
        successor_identity_sha256=case["identity"],
        artifact_set_sha256=case["artifact_set"],
        event_id=case["event"]["event_id"],
        decision=payload["decision"],
        review_artifact_path=review_path,
        review_artifact_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),
        operator="fixture-operator",
        dry_run=dry_run,
    )


def test_confirmed_review_records_deterministically_and_exact_replay_is_noop(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    before = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in case["migration"].iterdir()
    }

    first = _record(case, review_path, payload)
    decision_path = Path(first["decision_path"])
    first_bytes = decision_path.read_bytes()
    first_timestamp = decision_path.stat().st_mtime_ns
    replay = _record(case, review_path, payload)

    assert first["status"] == "decision_recorded"
    assert replay["status"] == "idempotent_noop"
    assert decision_path.read_bytes() == first_bytes
    assert decision_path.stat().st_mtime_ns == first_timestamp
    assert before == {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in case["migration"].iterdir()
    }
    decision = json.loads(first_bytes)
    assert decision["publication_approval"] is False
    assert decision["queue_authorized"] is False
    assert decision["pages_authorized"] is False
    assert first["queue_items_created"] == first["pages_files_written"] == 0
    assert not (case["root"] / "output").exists()


def test_review_dry_run_is_non_mutating(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    result = _record(case, review_path, payload, dry_run=True)
    assert result["status"] == "dry_run_validated"
    assert result["persistent_mutation"] is False
    assert not Path(result["decision_path"]).exists()


def test_altered_decision_bytes_fail_exact_replay(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    first = _record(case, review_path, payload)
    decision_path = Path(first["decision_path"])
    decision_path.write_bytes(decision_path.read_bytes() + b" ")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="conflicting"):
        _record(case, review_path, payload)


@pytest.mark.parametrize("field", ["queue_authorized", "publication_authorized", "pages_authorized"])
def test_review_cannot_grant_authority(tmp_path: Path, field: str):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    payload[field] = True
    review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="cannot grant"):
        _record(case, review_path, payload)


def test_review_rejects_recovery_drift_and_unbound_source(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    payload["evidence_references"][0]["canonical_source_url"] = "https://other.example.org/story"
    review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="not bound"):
        _record(case, review_path, payload)

    review_path, payload = _review(case)
    artifact = case["migration"] / "priority_confirmed_candidates.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="artifact drifted"):
        _record(case, review_path, payload)


def test_nonconfirmed_review_cannot_prepare_copy_and_requires_matching_dedupe_state(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case, decision="already_published")
    payload["publication_copy"] = {"headline": "Not allowed", "summary": "Not allowed", "source_links": []}
    review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="cannot prepare"):
        _record(case, review_path, payload)

    review_path, payload = _review(case, decision="duplicate_or_corroboration")
    result = _record(case, review_path, payload)
    assert result["status"] == "decision_recorded"
    assert json.loads(Path(result["decision_path"]).read_text())["publication_copy"] is None


def test_public_copy_rejects_internal_ids_iso_dates_and_story_cap_overflow(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    payload["publication_copy"]["summary"] = f"Internal {case['event']['event_id']} on 2026-08-01 | hidden"
    review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="exposes internal"):
        _record(case, review_path, payload)

    review_path, payload = _review(case)
    payload["recommended_batch"]["order"] = 7
    review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="six-story"):
        _record(case, review_path, payload)


def test_cli_review_boundary_records_no_public_side_effects(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    rc = main(
        [
            "review",
            "--repo-root", str(case["root"]),
            "--pages-root", str(case["pages"]),
            "--successor-identity", case["identity"],
            "--artifact-set", case["artifact_set"],
            "--event-id", case["event"]["event_id"],
            "--decision", payload["decision"],
            "--review-artifact", str(review_path),
            "--review-artifact-sha256", hashlib.sha256(review_path.read_bytes()).hexdigest(),
            "--operator", "fixture-operator",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert result["status"] == "decision_recorded"
    assert result["publication_approval"] is False
    assert result["queue_items_created"] == 0
    assert result["pages_files_written"] == 0


def test_pages_drift_blocks_review(tmp_path: Path):
    case = _case(tmp_path)
    review_path, payload = _review(case)
    (case["pages"] / "food-line" / "index.html").write_text("changed", encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="clean"):
        _record(case, review_path, payload)
