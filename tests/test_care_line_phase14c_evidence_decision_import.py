from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_evidence_review import (
    EvidenceDecision,
    DECISION_SCHEMA_VERSION,
    _decision_id,
    _recommendation_for_status,
    _status_for_decision,
    import_evidence_decisions,
    load_decisions_payloads,
    main as evidence_review_main,
    review_packet_fingerprint,
)
from bluefern_dispatches.care_line_normalize import source_payload_fingerprint


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
    shutil.copytree(
        Path.cwd() / "data" / "dispatches" / "care-line",
        repo / "data" / "dispatches" / "care-line",
        dirs_exist_ok=True,
    )
    shutil.rmtree(repo / "data" / "dispatches" / "care-line" / "evidence-reviews", ignore_errors=True)
    shutil.copytree(
        Path.cwd() / "data" / "universal_events" / "shadow" / "care-line" / "phase14b-evidence-review",
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
    assert source_payload_fingerprint(source) != source_payload_fingerprint(changed)


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
