import json
import hashlib
from pathlib import Path

import pytest
from bluefern_dispatches.historical_agent_archive import normalize_records
from scripts.import_historical_agent_runs import main


PUBLISHED_A = "event_3b4ad4e528e48744"
PUBLISHED_B = "event_a12dae614b86cfa9"


def finding(**overrides):
    value = {
        "event_id": "",
        "source_url": "https://example.org/care-story",
        "source_published_at": "2026-07-20T00:00:00Z",
        "event_date": "2026-07-20",
        "facility_name": "Example Medical Center",
        "location_name": "Example, NC",
        "service_line": "inpatient_care",
        "event_type": "service_reduction",
        "exact_supporting_passage": "The hospital reduced inpatient capacity after the service change.",
    }
    value.update(overrides)
    return value


def write_json(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def private_targets(root: Path) -> None:
    write_json(root, "data/universal_events/publication-state/care-line-signal-wire.json", {"schema_version": 1, "events": {PUBLISHED_A: {}, PUBLISHED_B: {}}})
    write_json(root, "data/universal_events/shadow/care-line/lineage.json", {"events": [{"event_id": "event-reviewed", "source_url": "https://example.org/reviewed", "source_item_id": "source-reviewed", "revision_status": "approved"}]})
    write_json(root, "data/dispatches/care-line/reviewed/records.json", {"records": [{"event_id": "event-reviewed", "source_url": "https://example.org/reviewed", "review_status": "approved"}]})
    write_json(root, "data/dispatches/care-line/sources/snapshots.json", {"records": [{"source_record_id": "source-existing", "source_url": "https://example.org/existing"}]})


def normalize(root: Path, row: dict):
    records, outcomes = normalize_records(root, "care-line", {"agent_name": "fixture", "agent_run_id": "run-1", "findings": [row]}, raw_sha256="raw", captured_at="2026-07-29T00:00:00Z")
    return records[0], outcomes


def test_published_events_are_provenance_only_and_never_requeued(tmp_path: Path):
    private_targets(tmp_path)
    for event_id in (PUBLISHED_A, PUBLISHED_B):
        record, outcomes = normalize(tmp_path, finding(event_id=event_id))
        assert outcomes == {"matched_published_event": 1}
        assert record["historical_outcome"] == "matched_published_event"
        assert record["queue_action"] == "provenance_only"
        assert record["candidate_created"] is False
        assert record["publication_eligible"] is False


def test_reviewed_event_and_existing_source_are_not_duplicated(tmp_path: Path):
    private_targets(tmp_path)
    reviewed, reviewed_outcomes = normalize(tmp_path, finding(event_id="event-reviewed", source_url="https://example.org/reviewed"))
    existing, existing_outcomes = normalize(tmp_path, finding(source_url="https://example.org/existing"))
    assert reviewed_outcomes == {"matched_reviewed_event": 1}
    assert reviewed["queue_action"] == "none"
    assert existing_outcomes == {"matched_existing_source": 1}
    assert existing["queue_action"] == "provenance_only"
    assert existing["provenance_links"][0]["source_record_id"] == "source-existing"


def test_unmatched_and_invalid_care_findings_stay_private(tmp_path: Path):
    private_targets(tmp_path)
    candidate, candidate_outcomes = normalize(tmp_path, finding(source_url="https://example.org/new"))
    invalid, invalid_outcomes = normalize(tmp_path, finding(source_url="https://example.org/no-evidence", exact_supporting_passage=""))
    manual, manual_outcomes = normalize(tmp_path, finding(source_url="", event_id=""))
    assert candidate_outcomes == {"new_historical_candidate": 1}
    assert candidate["queue_action"] == "historical_review_candidate"
    assert candidate["review_status"] == "pending_review"
    assert candidate["publication_eligible"] is False
    assert invalid_outcomes == {"archived_invalid": 1}
    assert invalid["review_status"] == "excluded"
    assert invalid["candidate_created"] is False
    assert manual_outcomes == {"needs_manual_review": 1}
    assert manual["review_status"] == "pending_review"


def test_duplicate_historical_is_identity_based_and_preserves_dates(tmp_path: Path):
    private_targets(tmp_path)
    write_json(tmp_path, "data/agent-history/care-line/normalized/prior.json", {"domain": "care-line", "findings": [finding(source_url="https://example.org/duplicate")]})
    record, outcomes = normalize(tmp_path, finding(source_url="https://example.org/duplicate"))
    assert outcomes == {"duplicate_historical": 1}
    assert record["historical_outcome"] == "duplicate_historical"
    assert record["source_published_at"].startswith("2026-07-20")


def test_historical_normalization_never_writes_queue_or_public_output(tmp_path: Path):
    private_targets(tmp_path)
    normalize(tmp_path, finding(source_url="https://example.org/new"))
    assert not (tmp_path / "data/universal_events/publication-state/care-line-reviewed-event-queue.json").exists()
    assert not (tmp_path / "output/site").exists()


def test_dry_run_report_contains_care_line_operational_fields(tmp_path: Path, capsys):
    private_targets(tmp_path)
    source = tmp_path / "alert.json"
    source.write_text(json.dumps({"agent_name": "fixture", "agent_run_id": "run-report", "findings": [finding(source_url="https://example.org/report")]}), encoding="utf-8")
    assert main(["dry-run", "--domain", "care-line", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    item = report["care_line_findings"][0]
    for field in ("raw_sha256", "agent_run_id", "source_url", "source_published_at", "event_date", "facility_name", "location_name", "service_line", "event_type", "queue_action", "candidate_created", "review_status", "publication_eligible", "provenance_links"):
        assert field in item
    assert item["queue_action"] == "historical_review_candidate"
    assert item["publication_eligible"] is False


def test_structured_care_sidecar_splits_prose_without_mutating_raw(tmp_path: Path, capsys):
    raw_path = tmp_path / "data/agent-history-staging/care-line/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw = "Missouri source https://missouri.example/story\nDelaware source https://delaware.example/story\n"
    raw_path.write_bytes(raw.encode("utf-8"))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    sidecar = {
        "raw_sha256": digest,
        "raw_file": "data/agent-history-staging/care-line/alert.txt",
        "domain": "care-line",
        "normalization_type": "prose_envelope_to_structured_findings",
        "reviewer": "William Patton",
        "reviewed_at": "2026-07-29T00:00:00Z",
        "approved": True,
        "approval_scope": "historical_normalization_only",
        "publication_approval": False,
        "findings": [
            finding(source_url="https://missouri.example/story", event_type="permanent_service_closure", access_direction="access_loss"),
            finding(source_url="https://delaware.example/story", event_type="planned_access_expansion", access_direction="access_expansion"),
        ],
    }
    sidecar_path = tmp_path / "sidecar.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    before = raw_path.read_bytes()
    assert main(["dry-run", "--domain", "care-line", "--input", str(raw_path), "--correction", str(sidecar_path), "--repo-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["normalized_finding_count"] == 2
    assert result["outcomes"] == {"archived_context": 1, "new_historical_candidate": 1}
    assert raw_path.read_bytes() == before
    assert not (tmp_path / "data/agent-history/care-line").exists()


def test_care_sidecar_hash_mismatch_fails_closed(tmp_path: Path):
    raw_path = tmp_path / "data/agent-history-staging/care-line/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("https://example.org/story\n", encoding="utf-8")
    sidecar_path = tmp_path / "sidecar.json"
    sidecar_path.write_text(json.dumps({
        "raw_sha256": "wrong", "raw_file": "data/agent-history-staging/care-line/alert.txt", "domain": "care-line",
        "normalization_type": "prose_envelope_to_structured_findings", "approved": True,
        "approval_scope": "historical_normalization_only", "publication_approval": False,
        "findings": [finding(source_url="https://example.org/story")],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="raw_sha256"):
        main(["dry-run", "--domain", "care-line", "--input", str(raw_path), "--correction", str(sidecar_path), "--repo-root", str(tmp_path)])
