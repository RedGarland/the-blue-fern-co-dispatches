import base64
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


def care_substantive_review_fixture(root: Path) -> tuple[list[str], dict[str, Path]]:
    raw_bytes = b"private historical Care Line fixture\n"
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    base = root / "data/agent-history/care-line"
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    review_path = base / "reviews" / f"{raw_sha}-substantive-review.json"
    source_url = "https://example.org/hutcheson-pharmacy-closure"
    write_json(
        root,
        str(raw_path.relative_to(root)),
        {
            "domain": "care-line",
            "raw_sha256": raw_sha,
            "raw_bytes_base64": base64.b64encode(raw_bytes).decode("ascii"),
        },
    )
    write_json(
        root,
        str(normalized_path.relative_to(root)),
        {
            "schema_version": "historical_agent_normalized_v1",
            "domain": "care-line",
            "raw_sha256": raw_sha,
            "findings": [
                {
                    "finding_id": "care-line-hutcheson-fixture",
                    "historical_outcome": "new_historical_candidate",
                    "deduplication_outcome": "new_historical_candidate",
                    "candidate_created": True,
                    "review_status": "pending_review",
                    "facility": "Hutcheson Pharmacy",
                    "organization": "Texas County Memorial Hospital",
                    "location_name": "Texas County, Missouri",
                    "city": "Houston",
                    "county": "Texas County",
                    "state": "MO",
                    "event_type": "permanent_service_closure",
                    "service_affected": "hospital-operated retail pharmacy and medication access",
                    "access_direction": "access_loss",
                    "announcement_date": "2026-07-29",
                    "effective_date": "2026-08-21",
                    "queue_action": "review_pending",
                    "publication_eligible": False,
                    "publication_approval": False,
                    "source_published_at": "2026-07-29",
                    "source_url": source_url,
                    "canonical_source_url": source_url,
                    "exact_supporting_passage": "The hospital announced the scheduled retail pharmacy closure and prescription transfer process.",
                },
                {
                    "finding_id": "care-line-delaware-context-fixture",
                    "historical_outcome": "archived_context",
                    "deduplication_outcome": "new_historical_candidate",
                    "candidate_created": False,
                    "review_status": "historical_context",
                    "event_type": "planned_access_expansion",
                    "queue_action": "none",
                    "publication_eligible": False,
                    "publication_approval": False,
                    "source_published_at": "2026-07-29",
                    "source_url": "https://example.org/delaware-context",
                    "exact_supporting_passage": "The state announced a planned access expansion.",
                },
            ],
        },
    )
    write_json(
        root,
        str(report_path.relative_to(root)),
        {
            "domain": "care-line",
            "input_sha256": raw_sha,
            "status": "imported",
        },
    )
    write_json(
        root,
        str(review_path.relative_to(root)),
        {
            "schema_version": "care_line_substantive_historical_review_v1",
            "domain": "care-line",
            "raw_sha256": raw_sha,
            "normalized_finding_id": "care-line-hutcheson-fixture",
            "review_type": "substantive_historical_review",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "archive_mutation_authorized": False,
            "queue_authorized": False,
            "publication_authorized": False,
            "current_review_status": "pending_review",
            "current_queue_action": "review_pending",
            "current_publication_eligible": False,
            "current_publication_approval": False,
            "materiality_assessment": {"assessment": "moderate_access_impact"},
            "duplicate_and_live_record_check": {
                "historical_candidate_remains_distinct": True,
                "existing_published_event": None,
                "existing_reviewed_live_candidate": None,
                "live_reviewed_event_queue_entry": None,
            },
            "taxonomy_review": {
                "event_type": {
                    "current_value": "permanent_service_closure",
                },
                "service_line": {"value": "retail pharmacy"},
                "event_status": {"value": "scheduled"},
                "effective_date": {"value": "2026-08-21"},
            },
            "editorial_restrictions": [
                "Describe the pharmacy as scheduled to close.",
                "Do not call it the only local pharmacy.",
            ],
        },
    )
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    args = [
        "review",
        "--domain",
        "care-line",
        "--raw-sha",
        raw_sha,
        "--decision",
        "substantively-valid",
        "--review-artifact",
        str(review_path),
        "--review-artifact-sha256",
        review_sha,
        "--repo-root",
        str(root),
    ]
    return args, {
        "raw": raw_path,
        "normalized": normalized_path,
        "report": report_path,
        "review": review_path,
    }


def run_json(capsys, args: list[str]) -> tuple[int, dict]:
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


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
    assert candidate["queue_action"] == "review_pending"
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
    assert item["queue_action"] == "review_pending"
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


def test_care_substantive_review_changes_only_status_and_is_idempotent(
    tmp_path: Path,
    capsys,
):
    private_targets(tmp_path)
    protected = [
        tmp_path / "output/site/marker.txt",
        tmp_path / "bluefern-dispatches-pages/marker.txt",
        tmp_path / "data/agent-history/food-line/marker.txt",
        tmp_path / "data/agent-history/gaza/marker.txt",
        tmp_path / "data/agent-history/ice/marker.txt",
        tmp_path / "data/bluesky/marker.txt",
        tmp_path / "schedules/marker.txt",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")

    ledger = (
        tmp_path
        / "data/universal_events/publication-state/care-line-signal-wire.json"
    )
    args, paths = care_substantive_review_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    original_ledger = ledger.read_bytes()
    before_normalized = json.loads(originals["normalized"].decode("utf-8"))

    code, accepted = run_json(capsys, args)
    assert code == 0
    assert accepted["status"] == "review_status_updated"
    assert accepted["previous_review_status"] == "pending_review"
    assert accepted["new_review_status"] == "substantively_reviewed"
    assert accepted["inventory_before"]["raw_run_count"] == 1
    assert accepted["inventory_before"]["normalized_finding_count"] == 2
    assert accepted["inventory_before"]["historical_candidate_count"] == 1
    assert accepted["inventory_before"]["archived_context_count"] == 1
    assert accepted["inventory_before"]["pending_substantive_review"] == 1
    assert accepted["inventory_before"]["substantively_reviewed"] == 0
    assert accepted["inventory_after"]["pending_substantive_review"] == 0
    assert accepted["inventory_after"]["substantively_reviewed"] == 1
    assert accepted["inventory_after"]["queue_entries"] == 1
    assert accepted["inventory_after"]["publication_ready_count"] == 0

    after_normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(before_normalized))
    expected["findings"][0]["review_status"] = "substantively_reviewed"
    assert after_normalized == expected
    assert after_normalized["findings"][1] == before_normalized["findings"][1]
    assert paths["raw"].read_bytes() == originals["raw"]
    assert paths["report"].read_bytes() == originals["report"]
    assert paths["review"].read_bytes() == originals["review"]
    assert ledger.read_bytes() == original_ledger
    assert all(path.read_text(encoding="utf-8") == "unchanged" for path in protected)
    assert not (
        tmp_path
        / "data/universal_events/publication-state/care-line-reviewed-event-queue.json"
    ).exists()

    audit_path = Path(accepted["decision_audit_path"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["domain"] == "care-line"
    assert audit["decision"] == "accept_substantively_valid_historical_candidate"
    assert audit["operator"] == "William Patton"
    assert audit["previous_review_status"] == "pending_review"
    assert audit["new_review_status"] == "substantively_reviewed"
    assert audit["historical_outcome"] == "new_historical_candidate"
    assert audit["event_type"] == "permanent_service_closure"
    assert audit["service_line"] == "retail pharmacy"
    assert audit["event_status"] == "scheduled"
    assert audit["effective_date"] == "2026-08-21"
    assert audit["materiality_assessment"] == "moderate_access_impact"
    assert audit["queue_action"] == "review_pending"
    assert audit["publication_eligible"] is False
    assert audit["publication_approval"] is False
    assert audit["archive_content_change_authorized"] is False
    assert audit["queue_authorized"] is False
    assert audit["publication_authorized"] is False
    assert audit["editorial_restrictions"] == [
        "Describe the pharmacy as scheduled to close.",
        "Do not call it the only local pharmacy.",
    ]
    assert audit["changed_fields"] == ["findings[].review_status"]
    assert len(list(audit_path.parent.glob("*.json"))) == 1

    no_op_hashes = {
        "normalized": hashlib.sha256(paths["normalized"].read_bytes()).hexdigest(),
        "audit": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "index": hashlib.sha256(
            (
                tmp_path
                / "data/agent-history/care-line/reports/history-index.json"
            ).read_bytes()
        ).hexdigest(),
    }
    decided_at = audit["decided_at"]
    code, repeated = run_json(capsys, args)
    assert code == 0
    assert repeated["status"] == "idempotent_noop"
    assert repeated["inventory"]["pending_substantive_review"] == 0
    assert repeated["inventory"]["substantively_reviewed"] == 1
    assert repeated["inventory"]["queue_entries"] == 1
    assert repeated["inventory"]["publication_ready_count"] == 0
    assert (
        hashlib.sha256(paths["normalized"].read_bytes()).hexdigest()
        == no_op_hashes["normalized"]
    )
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == no_op_hashes["audit"]
    assert (
        hashlib.sha256(
            (
                tmp_path
                / "data/agent-history/care-line/reports/history-index.json"
            ).read_bytes()
        ).hexdigest()
        == no_op_hashes["index"]
    )
    assert json.loads(audit_path.read_text(encoding="utf-8"))["decided_at"] == decided_at
    assert len(list(audit_path.parent.glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema_version": "wrong"}, "schema_version"),
        ({"queue_authorized": True}, "queue_authorized"),
        ({"normalized_finding_id": "wrong-finding"}, "exactly one normalized finding"),
        (
            {"materiality_assessment": {"assessment": "urgent"}},
            "materiality_assessment",
        ),
    ],
)
def test_care_substantive_review_fails_closed_on_artifact_mismatch(
    tmp_path: Path,
    mutation: dict,
    message: str,
):
    private_targets(tmp_path)
    args, paths = care_substantive_review_fixture(tmp_path)
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    review.update(mutation)
    write_json(tmp_path, str(paths["review"].relative_to(tmp_path)), review)
    args[args.index("--review-artifact-sha256") + 1] = hashlib.sha256(
        paths["review"].read_bytes()
    ).hexdigest()
    normalized_before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match=message):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (
        tmp_path / "data/agent-history/care-line/reviews/decisions"
    ).exists()


def test_care_substantive_review_rejects_new_live_source_match(tmp_path: Path):
    private_targets(tmp_path)
    args, paths = care_substantive_review_fixture(tmp_path)
    normalized_before = paths["normalized"].read_bytes()
    write_json(
        tmp_path,
        "data/dispatches/care-line/sources/new-live-source.json",
        {
            "source_record_id": "source-new-live",
            "source_url": "https://example.org/hutcheson-pharmacy-closure",
        },
    )

    with pytest.raises(ValueError, match="now matches a live source record"):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (
        tmp_path / "data/agent-history/care-line/reviews/decisions"
    ).exists()
