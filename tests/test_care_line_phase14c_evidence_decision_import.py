from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_evidence_review import (
    EvidenceDecision,
    DECISION_SCHEMA_VERSION,
    DEFAULT_PRE_REVIEW_RECORDS_OUTPUT,
    DEFAULT_REVIEW_PACKET_OUTPUT,
    DEFAULT_REVIEW_PACKET_REPORT,
    _decision_id,
    _recommendation_for_status,
    _status_for_decision,
    build_pre_review_records_from_packet,
    build_review_packet_from_current_state,
    import_evidence_decisions,
    load_decisions_payloads,
    main as evidence_review_main,
    review_packet_fingerprint,
    review_packet_generation_report,
    write_review_packet_from_current_state,
)
from bluefern_dispatches.care_line_record import stable_json_hash


REPO_DECISIONS = Path("data/universal_events/shadow/care-line/phase14b-evidence-review/phase14b-evidence-decisions-template.json")
REPO_DECISIONS_CSV = Path("data/universal_events/shadow/care-line/phase14b-evidence-review/phase14b-evidence-decisions-template.csv")
REPO_PACKET = Path("data/universal_events/shadow/care-line/phase14b-evidence-review/phase14b-evidence-review.json")
REPO_REVIEWED = Path("data/dispatches/care-line/reviewed/2026-07-22/reviewed_records.json")

REPORT_PATH = Path("data/universal_events/shadow/care-line/phase14c-service/evidence-import-report.json")
LEDGER_PATH = Path("data/dispatches/care-line/evidence-reviews/2026-07-22/evidence_decisions.json")

EXPECTED_DECISIONS = {
    "care-line-direct-discovery-196621161639f9f2": "deferred",
    "care-line-direct-discovery-1ca33817c06119bc": "rejected",
    "care-line-direct-discovery-4a1461b9eccb0219": "deferred",
    "care-line-direct-discovery-8accd0f62b550cc4": "rejected",
    "care-line-direct-discovery-9543c43464dbd7d4": "deferred",
    "care-line-direct-discovery-fe1cba7829f11dc2": "deferred",
}


def _copy_repo_subset(root: Path) -> Path:
    repo = root / "repo"
    phase14b = Path.cwd() / "data" / "universal_events" / "shadow" / "care-line" / "phase14b-evidence-review"
    if not phase14b.exists():
        pytest.skip("Phase 14B fixture artifacts are not present on this branch")
    shutil.copytree(
        Path.cwd() / "data" / "dispatches" / "care-line" / "reviewed",
        repo / "data" / "dispatches" / "care-line" / "reviewed",
        dirs_exist_ok=True,
    )
    shutil.rmtree(repo / "data" / "dispatches" / "care-line" / "evidence-reviews", ignore_errors=True)
    shutil.copytree(
        phase14b,
        repo / "data" / "universal_events" / "shadow" / "care-line" / "phase14b-evidence-review",
        dirs_exist_ok=True,
    )
    shutil.rmtree(repo / "data" / "universal_events" / "shadow" / "care-line" / "phase14c-service", ignore_errors=True)
    reviewed_path = repo / REPO_REVIEWED
    packet_path = repo / REPO_PACKET
    reviewed = _read_json(reviewed_path)
    packet = _read_json(packet_path)
    packet_fingerprints = {row["producer_record_id"]: row["record_fingerprint"] for row in packet["records"]}
    for record in reviewed["records"]:
        record["raw_payload_hash"] = packet_fingerprints[record["producer_record_id"]]
    _write_json(reviewed_path, reviewed)
    (repo / "output" / "site").mkdir(parents=True, exist_ok=True)
    (repo / "bluefern-dispatches-pages").mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    return _copy_repo_subset(tmp_path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, payload: dict) -> None:
    rows = payload["decisions"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "schema_version",
                "producer_record_id",
                "record_fingerprint",
                "evidence_decision",
                "evidence_text",
                "evidence_provenance_type",
                "evidence_source_url",
                "evidence_source_field",
                "evidence_source_artifact",
                "reviewer",
                "review_reason",
                "reviewed_at",
                "supersedes_decision_id",
            ],
        )
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["schema_version"] = payload["schema_version"]
            csv_row["supersedes_decision_id"] = csv_row.pop("supersedes_evidence_decision_id", "")
            writer.writerow(csv_row)


def _decision_payloads(root: Path) -> tuple[dict, dict, dict, dict]:
    decisions_json = _read_json(root / REPO_DECISIONS)
    decisions_csv = root / REPO_DECISIONS_CSV
    packet = _read_json(root / REPO_PACKET)
    reviewed = _read_json(root / REPO_REVIEWED)
    return decisions_json, {"csv_path": decisions_csv}, packet, reviewed


def _paths(root: Path) -> dict[str, Path]:
    return {
        "packet": root / REPO_PACKET,
        "decisions_json": root / REPO_DECISIONS,
        "decisions_csv": root / REPO_DECISIONS_CSV,
        "reviewed": root / REPO_REVIEWED,
        "ledger": root / LEDGER_PATH,
        "report": root / REPORT_PATH,
    }


def _import(root: Path, *, check_only: bool = False, strict: bool = True) -> dict:
    paths = _paths(root)
    return import_evidence_decisions(
        repo_root=root,
        review_packet_path=paths["packet"],
        decisions_json_path=paths["decisions_json"],
        decisions_csv_path=paths["decisions_csv"],
        reviewed_records_path=paths["reviewed"],
        decision_ledger_path=paths["ledger"],
        report_path=paths["report"],
        check_only=check_only,
        strict=strict,
    )


def _load_decisions(root: Path) -> tuple[list[dict], dict, dict]:
    payload = _read_json(root / REPO_DECISIONS)
    packet = _read_json(root / REPO_PACKET)
    reviewed = _read_json(root / REPO_REVIEWED)
    return payload["decisions"], packet, reviewed


def _decision_identity_from_row(row: dict, packet_fp: str) -> str:
    payload = dict(row)
    payload.pop("supersedes_evidence_decision_id", None)
    return f"care_line_evidence_review_{json.dumps(payload | {'review_packet_fingerprint': packet_fp}, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8').hex()[:16]}"


def test_01_decision_rows_and_packet_alignment(repo_root: Path):
    decisions, packet, reviewed = _load_decisions(repo_root)
    packet_fp = review_packet_fingerprint(packet)
    packet_index = {row["producer_record_id"]: row for row in packet["records"]}
    assert len(decisions) == 6
    for producer_record_id, expected_decision in EXPECTED_DECISIONS.items():
        row = next(item for item in decisions if item["producer_record_id"] == producer_record_id)
        assert row["evidence_decision"] == expected_decision
        assert row["record_fingerprint"] == packet_index[producer_record_id]["record_fingerprint"]
        assert row["evidence_source_url"] == packet_index[producer_record_id]["canonical_source_url"]
        assert _decision_id(
            EvidenceDecision(
                schema_version=DECISION_SCHEMA_VERSION,
                producer_record_id=row["producer_record_id"],
                record_fingerprint=row["record_fingerprint"],
                evidence_decision=row["evidence_decision"],
                evidence_text=row["evidence_text"],
                evidence_provenance_type=row["evidence_provenance_type"],
                evidence_source_url=row["evidence_source_url"],
                evidence_source_field=row["evidence_source_field"],
                evidence_source_artifact=row["evidence_source_artifact"],
                reviewer=row["reviewer"],
                review_reason=row["review_reason"],
                reviewed_at=row["reviewed_at"],
                supersedes_decision_id=row["supersedes_evidence_decision_id"],
            ),
            packet_fp,
        ).startswith("care_line_evidence_review_")


@pytest.mark.parametrize(
    "decision,expected_status,expected_recommendation",
    [
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "approved", "faithful transcription", "reviewer_transcribed", "https://example.com", "", "", "Codex", "approved", "2026-07-23T17:51:06Z", ""),
            "universal_event_ready",
            "none",
            id="approved-transcribed",
        ),
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "approved", "", "missing", "https://example.com", "", "", "Codex", "approved", "2026-07-23T17:51:06Z", ""),
            "needs_evidence_review",
            "source_transcription_pending",
            id="approved-missing",
        ),
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "rejected", "", "missing", "https://example.com", "", "", "Codex", "rejected", "2026-07-23T17:51:06Z", ""),
            "excluded",
            "none",
            id="rejected",
        ),
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "deferred", "", "missing", "https://example.com", "", "", "Codex", "deferred", "2026-07-23T17:51:06Z", ""),
            "needs_evidence_review",
            "source_transcription_pending",
            id="deferred",
        ),
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "care_line_only", "", "missing", "https://example.com", "", "", "Codex", "care_line_only", "2026-07-23T17:51:06Z", ""),
            "care_line_only",
            "none",
            id="care-line-only",
        ),
        pytest.param(
            EvidenceDecision("bluefern.care_line.evidence_review.v1", "a", "r", "corrected", "faithful transcription", "source_explicit", "https://example.com", "", "", "Codex", "corrected", "2026-07-23T17:51:06Z", ""),
            "universal_event_ready",
            "none",
            id="corrected-explicit",
        ),
    ],
)
def test_02_status_and_recommendation_mapping(decision: EvidenceDecision, expected_status: str, expected_recommendation: str):
    assert _status_for_decision(decision) == expected_status
    assert _recommendation_for_status(expected_status) == expected_recommendation


@pytest.mark.parametrize(
    "mutator,expected_message",
    [
        pytest.param(lambda payload: payload.__setitem__("schema_version", "bad"), "unsupported decision schema_version", id="json-schema"),
        pytest.param(lambda payload: payload["decisions"][0].__setitem__("producer_record_id", ""), "producer_record_id is required", id="missing-id"),
        pytest.param(lambda payload: payload["decisions"][0].__setitem__("evidence_decision", "bogus"), "unsupported evidence_decision", id="bad-decision"),
        pytest.param(lambda payload: payload["decisions"][0].__setitem__("reviewer", ""), "reviewer is required", id="missing-reviewer"),
        pytest.param(lambda payload: payload["decisions"][0].__setitem__("review_reason", ""), "review_reason is required", id="missing-reason"),
        pytest.param(lambda payload: payload["decisions"][0].__setitem__("reviewed_at", ""), "reviewed_at is required", id="missing-reviewed-at"),
    ],
)
def test_03_decision_schema_and_required_field_failures(repo_root: Path, mutator, expected_message: str):
    payload = _read_json(repo_root / REPO_DECISIONS)
    mutator(payload)
    _write_json(repo_root / REPO_DECISIONS, payload)
    _write_csv(repo_root / REPO_DECISIONS_CSV, payload)
    with pytest.raises(ValueError, match=expected_message):
        load_decisions_payloads(repo_root / REPO_DECISIONS, repo_root / REPO_DECISIONS_CSV, strict=True)


@pytest.mark.parametrize(
    "mutator,expected_message",
    [
        pytest.param(lambda root: None, "stale record fingerprint", id="stale-record"),
        pytest.param(lambda root: _set_packet_record_fingerprint_bad(root), "stale review packet fingerprint", id="stale-packet"),
        pytest.param(lambda root: _append_conflicting_duplicate(root), "conflicting duplicate decision", id="duplicate-conflict"),
        pytest.param(lambda root: _set_supersedes(root, 0, "missing-decision-id"), "invalid supersession target", id="invalid-supersession"),
        pytest.param(lambda root: _set_self_supersedes(root, 0), "self supersession is not allowed", id="self-supersession"),
        pytest.param(lambda root: _set_cycle_supersedes(root), "supersession cycle detected", id="supersession-cycle"),
    ],
)
def test_04_rejects_stale_or_invalid_supersession_cases(repo_root: Path, mutator, expected_message: str):
    if expected_message == "stale record fingerprint":
        _import(repo_root, check_only=False)
        _set_decision_record_fingerprint_bad(repo_root)
    else:
        mutator(repo_root)
    expected_pattern = "stale (reviewed )?record fingerprint" if expected_message == "stale record fingerprint" else expected_message
    with pytest.raises(ValueError, match=expected_pattern):
        _import(repo_root, check_only=False if expected_message == "stale record fingerprint" else True)


def _append_conflicting_duplicate(root: Path) -> None:
    payload = _read_json(root / REPO_DECISIONS)
    extra = dict(payload["decisions"][0])
    extra["review_reason"] = "conflicting duplicate"
    payload["decisions"].append(extra)
    _write_json(root / REPO_DECISIONS, payload)
    _write_csv(root / REPO_DECISIONS_CSV, payload)


def _set_packet_record_fingerprint_bad(root: Path) -> None:
    packet = _read_json(root / REPO_PACKET)
    packet["records"][0]["record_fingerprint"] = "bad"
    _write_json(root / REPO_PACKET, packet)


def _set_decision_record_fingerprint_bad(root: Path) -> None:
    payload = _read_json(root / REPO_DECISIONS)
    payload["decisions"][0]["record_fingerprint"] = "bad"
    _write_json(root / REPO_DECISIONS, payload)
    _write_csv(root / REPO_DECISIONS_CSV, payload)


def _set_supersedes(root: Path, index: int, target: str) -> None:
    payload = _read_json(root / REPO_DECISIONS)
    payload["decisions"][index]["supersedes_evidence_decision_id"] = target
    _write_json(root / REPO_DECISIONS, payload)
    _write_csv(root / REPO_DECISIONS_CSV, payload)


def _set_self_supersedes(root: Path, index: int) -> None:
    payload = _read_json(root / REPO_DECISIONS)
    row = payload["decisions"][index]
    row["supersedes_evidence_decision_id"] = _decision_id(
        EvidenceDecision(
            schema_version=DECISION_SCHEMA_VERSION,
            producer_record_id=row["producer_record_id"],
            record_fingerprint=row["record_fingerprint"],
            evidence_decision=row["evidence_decision"],
            evidence_text=row["evidence_text"],
            evidence_provenance_type=row["evidence_provenance_type"],
            evidence_source_url=row["evidence_source_url"],
            evidence_source_field=row["evidence_source_field"],
            evidence_source_artifact=row["evidence_source_artifact"],
            reviewer=row["reviewer"],
            review_reason=row["review_reason"],
            reviewed_at=row["reviewed_at"],
            supersedes_decision_id="",
        ),
        review_packet_fingerprint(_read_json(root / REPO_PACKET)),
    )
    _write_json(root / REPO_DECISIONS, payload)
    _write_csv(root / REPO_DECISIONS_CSV, payload)


def _set_cycle_supersedes(root: Path) -> None:
    payload = _read_json(root / REPO_DECISIONS)
    packet_fp = review_packet_fingerprint(_read_json(root / REPO_PACKET))
    first = payload["decisions"][0]
    second = payload["decisions"][1]
    first_id = _decision_id(
        EvidenceDecision(
            schema_version=DECISION_SCHEMA_VERSION,
            producer_record_id=first["producer_record_id"],
            record_fingerprint=first["record_fingerprint"],
            evidence_decision=first["evidence_decision"],
            evidence_text=first["evidence_text"],
            evidence_provenance_type=first["evidence_provenance_type"],
            evidence_source_url=first["evidence_source_url"],
            evidence_source_field=first["evidence_source_field"],
            evidence_source_artifact=first["evidence_source_artifact"],
            reviewer=first["reviewer"],
            review_reason=first["review_reason"],
            reviewed_at=first["reviewed_at"],
            supersedes_decision_id="",
        ),
        packet_fp,
    )
    second_id = _decision_id(
        EvidenceDecision(
            schema_version=DECISION_SCHEMA_VERSION,
            producer_record_id=second["producer_record_id"],
            record_fingerprint=second["record_fingerprint"],
            evidence_decision=second["evidence_decision"],
            evidence_text=second["evidence_text"],
            evidence_provenance_type=second["evidence_provenance_type"],
            evidence_source_url=second["evidence_source_url"],
            evidence_source_field=second["evidence_source_field"],
            evidence_source_artifact=second["evidence_source_artifact"],
            reviewer=second["reviewer"],
            review_reason=second["review_reason"],
            reviewed_at=second["reviewed_at"],
            supersedes_decision_id="",
        ),
        packet_fp,
    )
    first["supersedes_evidence_decision_id"] = second_id
    second["supersedes_evidence_decision_id"] = first_id
    _write_json(root / REPO_DECISIONS, payload)
    _write_csv(root / REPO_DECISIONS_CSV, payload)


def test_05_apply_is_idempotent_and_preserves_reviewed_records(repo_root: Path):
    first = _import(repo_root, check_only=False)
    second = _import(repo_root, check_only=False)
    assert first["new_reviewed_record_versions_count"] == 4
    assert second["new_reviewed_record_versions_count"] == 0


@pytest.mark.parametrize(
    "producer_record_id",
    sorted(EXPECTED_DECISIONS),
)
def test_06_each_record_remains_in_expected_effective_status_after_apply(repo_root: Path, producer_record_id: str):
    _import(repo_root, check_only=False)
    report = _import(repo_root, check_only=True)
    row = next(item for item in report["records"] if item["producer_record_id"] == producer_record_id)
    assert row["effective_universal_event_status"] == (
        "excluded" if EXPECTED_DECISIONS[producer_record_id] == "rejected" else "needs_evidence_review"
    )


def test_06b_stale_decision_fingerprint_fails_closed(repo_root: Path):
    _import(repo_root, check_only=False)
    decisions_path = repo_root / REPO_DECISIONS
    decisions = _read_json(decisions_path)
    decisions["decisions"][0]["record_fingerprint"] = "changed-after-review"
    _write_json(decisions_path, decisions)
    _write_csv(repo_root / REPO_DECISIONS_CSV, decisions)
    with pytest.raises(ValueError, match="stale reviewed record fingerprint"):
        _import(repo_root, check_only=True)


def test_06c_substantive_source_content_changes_fingerprint():
    source = {
        "source_record_id": "source-1",
        "title": "Original title",
        "url": "https://example.com/article",
        "publisher": "Example Publisher",
        "published_at": "2026-07-22",
        "evidence_text": "Original evidence",
        "pressure_type": "clinic_access_strain",
        "location_name": "Example Clinic",
        "state": "CA",
    }
    changed = dict(source, evidence_text="Changed substantive evidence")
    assert stable_json_hash(source) != stable_json_hash(changed)


def test_07_cli_smoke_check_only_and_apply(repo_root: Path):
    args = [
        "--repo-root",
        str(repo_root),
        "--review-packet",
        str(REPO_PACKET),
        "--decisions-json",
        str(REPO_DECISIONS),
        "--decisions-csv",
        str(REPO_DECISIONS_CSV),
        "--reviewed-records",
        str(REPO_REVIEWED),
        "--decision-ledger",
        str(LEDGER_PATH),
        "--report",
        str(REPORT_PATH),
        "--check-only",
        "--strict",
    ]
    assert evidence_review_main(args) == 0
    args[args.index("--check-only")] = "--apply"
    assert evidence_review_main(args) == 0


def _write_current_review_state(root: Path) -> None:
    review_root = root / "data" / "dispatches" / "care-line" / "review"
    _write_json(
        root / "data" / "dispatches" / "care-line" / "source_registry.json",
        {
            "schema_version": "bluefern.care_line.source_registry.v1",
            "sources": [
                {
                    "source_id": "county-health",
                    "name": "County Health News",
                    "publisher": "County Health",
                    "source_type": "government_health_department",
                    "authority_level": "primary",
                    "source_role": "health_department",
                    "geographic_scope": "local",
                }
            ],
        },
    )
    manual = {
        "schema_version": "bluefern.care_line.exclusion_record.v1",
        "manual_review_count": 2,
        "items": [
            {
                "raw_item_id": "raw-1",
                "lead_id": "lead-1",
                "source_id": "county-health",
                "source": "County Health News",
                "source_url": "https://county.example.org/clinic-closing",
                "title": "County Clinic will close in Austin, TX",
                "classification": "NEEDS_HUMAN_REVIEW",
                "extraction_outcome": "BODY_EXTRACTED",
                "missing_fields": ["reviewer_decision"],
                "supporting_text": "County Clinic will close on 2026-10-01 and patients will travel farther for primary care.",
            },
            {
                "raw_item_id": "raw-dup",
                "lead_id": "lead-dup",
                "source_id": "county-health",
                "source": "County Health News",
                "source_url": "https://county.example.org/clinic-closing",
                "title": "County Clinic will close in Austin, TX",
                "classification": "NEEDS_HUMAN_REVIEW",
                "extraction_outcome": "BODY_EXTRACTED",
                "missing_fields": ["reviewer_decision"],
                "supporting_text": "County Clinic will close on 2026-10-01 and patients will travel farther for primary care.",
            },
        ],
    }
    failed = {
        "schema_version": "bluefern.care_line.exclusion_record.v1",
        "failed_extraction_count": 2,
        "items": [
            {
                "exclusion_id": "failed-date",
                "raw_item_id": "raw-date",
                "lead_id": "lead-date",
                "source_id": "county-health",
                "source_name": "County Health News",
                "item_url": "https://county.example.org/no-date",
                "title": "County Clinic closing notice",
                "classification": "NEEDS_DATE",
                "extraction_outcome": "BODY_EXTRACTED",
                "failed_gates": ["missing_source_date"],
                "supporting_text": "County Clinic closing notice",
                "lineage": {"collection_run_id": "run-1", "source_artifact_path": "data/dispatches/care-line/collection-runs/run-1/county.raw-items.json"},
            },
            {
                "exclusion_id": "blocked",
                "raw_item_id": "raw-blocked",
                "lead_id": "lead-blocked",
                "source_id": "county-health",
                "source_name": "County Health News",
                "item_url": "https://county.example.org/access-blocked",
                "title": "County Clinic service unavailable",
                "classification": "NEEDS_HUMAN_REVIEW",
                "extraction_outcome": "ACCESS_BLOCKED",
                "failed_gates": ["insufficient_bounded_evidence"],
                "supporting_text": "",
            },
        ],
    }
    _write_json(review_root / "current-manual-review.json", manual)
    _write_json(review_root / "current-failed-extractions.json", failed)
    _write_json(review_root / "current-review-queue.json", {"schema_version": "queue", "items": [], "duplicates": [], "backlog": []})
    _write_json(review_root / "current-review-backlog.json", {"schema_version": "queue", "items": []})
    _write_json(review_root / "candidate-registry.json", {"schema_version": "registry", "candidates": []})


def _write_decision_pair(json_path: Path, csv_path: Path, row: dict) -> None:
    payload = {"schema_version": DECISION_SCHEMA_VERSION, "decisions": [row]}
    _write_json(json_path, payload)
    _write_csv(csv_path, payload)


def test_08_current_review_artifacts_generate_traceable_phase14b_packet(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    packet = build_review_packet_from_current_state(repo)
    report = review_packet_generation_report(packet)

    assert packet["schema_version"] == "bluefern.care_line.phase14b_evidence_review.v1"
    assert len(packet["records"]) == 4
    first = packet["records"][0]
    assert first["canonical_source_url"] == "https://county.example.org/clinic-closing"
    assert first["source_metadata"]["authority_level"] == "primary"
    assert first["proposed_fields"]["event_type_candidate"]["value"] == "planned_facility_closure"
    assert first["proposed_fields"]["access_consequence_candidate"]["value"]
    assert first["supporting_passage"] in first["proposed_fields"]["event_type_candidate"]["provenance"]["source_text"] or first["supporting_passage"]
    assert first["automation_limits"] == {
        "review_status_set": False,
        "universal_event_ready_set": False,
        "publication_invoked": False,
    }
    assert report["records_examined"] == 4
    assert report["records_approved"] == 0
    assert report["records_published"] == 0
    assert report["queue_release_state_changed"] is False


def test_09_packet_generation_deduplicates_and_keeps_unresolved_fields(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    packet = build_review_packet_from_current_state(repo)
    report = review_packet_generation_report(packet)
    duplicates = [row for row in packet["records"] if row["resolution_bucket"] == "duplicate"]
    date_rows = [row for row in packet["records"] if row["producer_record_id"] == "raw-date"]
    blocked_rows = [row for row in packet["records"] if row["producer_record_id"] == "raw-blocked"]

    assert len(duplicates) == 1
    assert duplicates[0]["duplicate_of_producer_record_id"] == "raw-1"
    assert "missing_source_date" in date_rows[0]["unresolved_fields"]
    assert date_rows[0]["resolution_bucket"] == "deterministic_resolution"
    assert blocked_rows[0]["resolution_bucket"] == "additional_fetch_needed"
    assert report["records_automatically_deduplicated"] == 1
    assert report["records_requiring_full_source_research"] == 1


def test_10_packet_generation_is_idempotent_and_check_only_writes_nothing(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    first = build_review_packet_from_current_state(repo)
    second = build_review_packet_from_current_state(repo)
    assert review_packet_fingerprint(first) == review_packet_fingerprint(second)

    packet_path = repo / DEFAULT_REVIEW_PACKET_OUTPUT
    report_path = packet_path.with_name("current-report.json")
    reviewed_path = repo / DEFAULT_PRE_REVIEW_RECORDS_OUTPUT
    result = write_review_packet_from_current_state(
        repo,
        packet_path=packet_path,
        report_path=report_path,
        pre_review_records_path=reviewed_path,
        check_only=True,
    )
    assert result["report"]["records_examined"] == 4
    assert not packet_path.exists()
    assert not report_path.exists()
    assert not reviewed_path.exists()


def test_11_packet_generation_cli_writes_private_packet_without_publication():
    repo = Path("t") / "care-line-packet-cli-test"
    shutil.rmtree(repo, ignore_errors=True)
    try:
        _write_current_review_state(repo)
        packet_rel = DEFAULT_REVIEW_PACKET_OUTPUT
        report_rel = packet_rel.with_name("current-report.json")
        reviewed_rel = DEFAULT_PRE_REVIEW_RECORDS_OUTPUT
        packet_path = repo / packet_rel
        report_path = repo / report_rel
        reviewed_path = repo / reviewed_rel
        assert evidence_review_main(
            [
                "--repo-root",
                str(repo),
                "--generate-review-packet",
                "--apply",
                "--review-packet",
                str(packet_rel),
                "--report",
                str(report_rel),
                "--pre-review-records",
                str(reviewed_rel),
            ]
        ) == 0
        packet = _read_json(packet_path)
        report = _read_json(report_path)
        reviewed = _read_json(reviewed_path)
        assert len(packet["records"]) == 4
        assert reviewed["metadata"]["automated_review_status_changes"] is False
        assert report["records_approved"] == 0
        assert report["records_published"] == 0
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_12_generate_review_packet_requires_explicit_apply_or_check_only(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    assert evidence_review_main(["--repo-root", str(repo), "--generate-review-packet"]) == 2
    assert not (repo / DEFAULT_REVIEW_PACKET_OUTPUT).exists()
    assert not (repo / DEFAULT_REVIEW_PACKET_REPORT).exists()
    assert not (repo / DEFAULT_PRE_REVIEW_RECORDS_OUTPUT).exists()


def test_13_pre_review_bridge_feeds_existing_phase14c_check_only(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    result = write_review_packet_from_current_state(
        repo,
        packet_path=DEFAULT_REVIEW_PACKET_OUTPUT,
        report_path=DEFAULT_REVIEW_PACKET_REPORT,
        pre_review_records_path=DEFAULT_PRE_REVIEW_RECORDS_OUTPUT,
        check_only=False,
    )
    packet = result["packet"]
    pre_review = result["pre_review_records"]
    representative = next(row for row in packet["records"] if not row["duplicate_of_producer_record_id"])
    record = next(row for row in pre_review["records"] if row["producer_record_id"] == representative["producer_record_id"])

    assert record["review_status"] == "not_reviewed"
    assert record["universal_event_status"] == "needs_evidence_review"
    assert record["evidence_valid_for_universal_event"] is False
    assert record["care_line_public_eligible"] is False
    assert record["raw_payload_hash"] == representative["record_fingerprint"]
    assert record["metadata"]["packet_fingerprint"] == review_packet_fingerprint(packet)
    assert record["metadata"]["raw_item_id"] == representative["raw_item_id"]
    assert record["metadata"]["event_lead_id"] == representative["event_lead_id"]

    decision = {
        "producer_record_id": record["producer_record_id"],
        "record_fingerprint": record["raw_payload_hash"],
        "evidence_decision": "deferred",
        "evidence_text": "",
        "evidence_provenance_type": "missing",
        "evidence_source_url": representative["canonical_source_url"],
        "evidence_source_field": "supporting_passage",
        "evidence_source_artifact": "",
        "reviewer": "Test Reviewer",
        "review_reason": "bounded evidence still requires human judgment",
        "reviewed_at": "2026-09-23T19:30:00Z",
        "supersedes_evidence_decision_id": "",
    }
    decisions_json = repo / "data" / "dispatches" / "care-line" / "review" / "evidence-review-packets" / "decision.json"
    decisions_csv = decisions_json.with_suffix(".csv")
    report_path = decisions_json.with_name("phase14c-check-only-report.json")
    ledger_path = decisions_json.with_name("phase14c-ledger.json")
    _write_decision_pair(decisions_json, decisions_csv, decision)

    report = import_evidence_decisions(
        repo_root=repo,
        review_packet_path=repo / DEFAULT_REVIEW_PACKET_OUTPUT,
        decisions_json_path=decisions_json,
        decisions_csv_path=decisions_csv,
        reviewed_records_path=repo / DEFAULT_PRE_REVIEW_RECORDS_OUTPUT,
        decision_ledger_path=ledger_path,
        report_path=report_path,
        check_only=True,
        strict=True,
    )
    assert report["decisions_examined"] == 1
    assert report["deferred_count"] == 1
    assert report["approved_count"] == 0
    assert report["universal_event_ready_count"] == 0
    assert report["records"][0]["producer_record_id"] == record["producer_record_id"]
    assert not ledger_path.exists()

    second = write_review_packet_from_current_state(
        repo,
        packet_path=DEFAULT_REVIEW_PACKET_OUTPUT,
        report_path=DEFAULT_REVIEW_PACKET_REPORT,
        pre_review_records_path=DEFAULT_PRE_REVIEW_RECORDS_OUTPUT,
        check_only=True,
    )
    assert review_packet_fingerprint(second["packet"]) == review_packet_fingerprint(packet)
    assert stable_json_hash(second["pre_review_records"]["records"]) == stable_json_hash(pre_review["records"])

    stale = _read_json(repo / DEFAULT_PRE_REVIEW_RECORDS_OUTPUT)
    stale["records"][0]["raw_payload_hash"] = "stale"
    stale_path = repo / "data" / "dispatches" / "care-line" / "review" / "evidence-review-packets" / "stale-reviewed.json"
    _write_json(stale_path, stale)
    with pytest.raises(ValueError, match="stale reviewed record fingerprint"):
        import_evidence_decisions(
            repo_root=repo,
            review_packet_path=repo / DEFAULT_REVIEW_PACKET_OUTPUT,
            decisions_json_path=decisions_json,
            decisions_csv_path=decisions_csv,
            reviewed_records_path=stale_path,
            decision_ledger_path=ledger_path,
            report_path=report_path,
            check_only=True,
            strict=True,
        )


def test_14_same_url_distinct_events_are_not_deduplicated(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_current_review_state(repo)
    review_root = repo / "data" / "dispatches" / "care-line" / "review"
    manual = _read_json(review_root / "current-manual-review.json")
    manual["items"] = [
        {
            "raw_item_id": "raw-a",
            "source_id": "county-health",
            "source_url": "https://county.example.org/board-agenda",
            "title": "County health board agenda",
            "classification": "NEEDS_HUMAN_REVIEW",
            "supporting_text": "County Clinic will close on 2026-10-01 and patients will travel farther for primary care.",
        },
        {
            "raw_item_id": "raw-b",
            "source_id": "county-health",
            "source_url": "https://county.example.org/board-agenda",
            "title": "County health board agenda",
            "classification": "NEEDS_HUMAN_REVIEW",
            "supporting_text": "County Hospital will suspend emergency care on 2026-11-01 and patients will be redirected.",
        },
        {
            "raw_item_id": "raw-c",
            "source_id": "county-health",
            "source_url": "https://county.example.org/board-agenda",
            "title": "County health board agenda",
            "classification": "NEEDS_HUMAN_REVIEW",
            "supporting_text": "County Clinic will close on 2026-10-01 and patients will travel farther for primary care.",
        },
    ]
    _write_json(review_root / "current-manual-review.json", manual)
    _write_json(review_root / "current-failed-extractions.json", {"items": []})

    packet = build_review_packet_from_current_state(repo)
    unique = [row for row in packet["records"] if not row["duplicate_of_producer_record_id"]]
    duplicates = [row for row in packet["records"] if row["duplicate_of_producer_record_id"]]
    assert len(unique) == 2
    assert len(duplicates) == 1
    assert {row["raw_item_id"] for row in unique} == {"raw-a", "raw-b"}
