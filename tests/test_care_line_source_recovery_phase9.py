from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_reviewed_export import export_records_for_date
from bluefern_dispatches.care_line_source_recovery import (
    DECISIONS_SCHEMA_VERSION,
    REVIEWED_SOURCE_SCHEMA_VERSION,
    comparison_from_phase8,
    decisions_template,
    discovery_inventory,
    import_review,
    is_wrapper_url,
    review_package,
    run_recovery,
    source_quality_metrics,
)


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def write_discovery(root: Path, date: str, rows: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "care-line" / "sources" / date / "discovered_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return path


def recoverable_row(record_id: str = "lead-001") -> dict:
    return {
        "source_record_id": record_id,
        "title": "County clinic announces closure",
        "url": "https://news.google.com/rss/articles/example",
        "canonical_url": "https://local.example.org/county-clinic-closure",
        "canonical_publisher": "Local Example",
        "publisher": "news.google.com",
        "published_at": "2026-06-20",
        "source_published_date": "2026-06-20",
        "summary_or_snippet": "Clinic closure lead.",
        "canonical_evidence_text": "The article reports the county clinic will close and patients will be redirected.",
        "pressure_type": "clinic_access_strain",
        "facility_name": "County Clinic",
        "location_name": "Example City, ST",
        "state": "ST",
    }


def wrapper_row(record_id: str = "lead-wrapper") -> dict:
    row = recoverable_row(record_id)
    row.pop("canonical_url", None)
    row.pop("canonical_publisher", None)
    row.pop("canonical_evidence_text", None)
    return row


def reviewed_decisions(inventory: dict, decision: str = "approve_source") -> dict:
    review = review_package(inventory, sample_id="sample")
    payload = decisions_template(review)
    for row in payload["decisions"]:
        row.update(
            {
                "decision": decision,
                "reviewer": "phase9-reviewer",
                "reason": "Reviewed canonical publisher source and supporting passage.",
                "canonical_url": "https://local.example.org/county-clinic-closure",
                "publisher": "Local Example",
                "source_title": "County clinic announces closure",
                "supporting_passage": "The article reports the county clinic will close and patients will be redirected.",
                "event_type": "facility_closure",
                "facility_name": "County Clinic",
                "provider_name": "County Clinic",
                "location_name": "Example City, ST",
                "state": "ST",
                "source_pack_date": "2026-06-20",
            }
        )
    return payload


def test_01_wrapper_url_is_never_accepted_as_canonical_evidence_url(repo_copy: Path):
    assert is_wrapper_url("https://news.google.com/rss/articles/example")


def test_02_canonical_url_embedded_locally_can_be_proposed(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    assert inv["proposals"][0]["recovery_status"] == "recoverable_from_local_fields"


def test_03_reviewer_approval_required_before_source_pack_creation(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    result = import_review(inv, {"schema_version": DECISIONS_SCHEMA_VERSION, "sample_id": "sample", "decisions": []}, output_root=tmp_path / "sources", repo_root=repo_copy)
    assert result["accepted"] == []


def test_04_reviewer_can_replace_proposed_url(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv, "replace_source")
    decisions["decisions"][0]["canonical_url"] = "https://publisher.example.org/replacement"
    result = import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)
    assert result["accepted"][0]["decision"] == "replace_source"


def test_05_stale_reviewer_decision_is_rejected(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv)
    decisions["decisions"][0]["expected_proposal_fingerprint"] = "stale"
    assert import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)["errors"]


def test_06_missing_evidence_blocks_approval(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv)
    decisions["decisions"][0]["supporting_passage"] = ""
    inv["proposals"][0]["proposed_supporting_passage"] = ""
    assert import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)["errors"]


def test_07_missing_publisher_blocks_approval(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv)
    decisions["decisions"][0]["publisher"] = ""
    inv["proposals"][0]["proposed_canonical_publisher"] = ""
    assert import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)["errors"]


def test_08_unsupported_event_type_blocks_ue_ready_classification(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv)
    decisions["decisions"][0]["event_type"] = "unsupported"
    assert import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)["errors"]


def test_09_non_operational_record_may_remain_care_line_only(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv, "mark_non_operational")
    result = import_review(inv, decisions, output_root=tmp_path / "sources", repo_root=repo_copy)
    assert result["accepted"][0]["decision"] == "mark_non_operational"


def test_10_discovery_file_remains_unchanged(repo_copy: Path, tmp_path: Path):
    path = write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    before = path.read_bytes()
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=tmp_path / "sources", repo_root=repo_copy)
    assert path.read_bytes() == before


def test_11_existing_manual_pack_merges_deterministically(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    first = import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    second = import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    assert first["write_manifests"][0]["after_hash"] == second["write_manifests"][0]["after_hash"]


def test_12_existing_source_records_are_preserved(repo_copy: Path):
    manual = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-05-23" / "manual_sources.json"
    before = len(json.loads(manual.read_text(encoding="utf-8")))
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    assert len(json.loads(manual.read_text(encoding="utf-8"))) == before


def test_13_stable_producer_ids_survive_source_correction(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row("stable-id")])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    result = import_review(inv, reviewed_decisions(inv), output_root=tmp_path / "sources", repo_root=repo_copy)
    assert result["accepted"][0]["source_record_id"] == "stable-id"


def test_14_duplicate_source_record_is_not_written_twice(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row("dup-id"), recoverable_row("dup-id")])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    decisions = reviewed_decisions(inv)
    import_review(inv, decisions, output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-20" / "manual_sources.json").read_text(encoding="utf-8"))
    assert len({row["source_record_id"] for row in rows}) == len(rows)


def test_15_same_event_different_source_records_remain_distinct(repo_copy: Path):
    rows = [recoverable_row("source-a"), recoverable_row("source-b")]
    rows[1]["canonical_url"] = "https://other.example.org/county-clinic-closure"
    write_discovery(repo_copy, "2026-06-20", rows)
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    result = import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    assert len(result["accepted"]) == 2


def test_16_source_pack_ordering_is_deterministic(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row("b"), recoverable_row("a")])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-20" / "manual_sources.json").read_text(encoding="utf-8"))
    assert [row["source_record_id"] for row in rows] == sorted(row["source_record_id"] for row in rows)


def test_17_source_pack_manifest_is_deterministic(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    first = import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)["write_manifests"]
    second = import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)["write_manifests"]
    assert first[0]["after_hash"] == second[0]["after_hash"]


def test_18_check_only_writes_nothing(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=tmp_path / "sources", repo_root=repo_copy, check_only=True)
    assert not (tmp_path / "sources").exists()


def test_19_public_output_paths_are_refused(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    with pytest.raises(ValueError):
        import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "output" / "site", repo_root=repo_copy)


def test_20_pages_paths_are_refused(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    with pytest.raises(ValueError):
        import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "bluefern-dispatches-pages", repo_root=repo_copy)


def test_21_recovery_provenance_is_preserved(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=tmp_path / "sources", repo_root=repo_copy)
    row = json.loads((tmp_path / "sources" / "2026-06-20" / "manual_sources.json").read_text(encoding="utf-8"))[0]
    assert row["schema_version"] == REVIEWED_SOURCE_SCHEMA_VERSION
    assert row["discovery_provenance"]["wrapper_url"].startswith("https://news.google.com")


def test_22_reviewer_identity_and_reason_are_preserved(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=tmp_path / "sources", repo_root=repo_copy)
    row = json.loads((tmp_path / "sources" / "2026-06-20" / "manual_sources.json").read_text(encoding="utf-8"))[0]
    assert row["reviewer"] == "phase9-reviewer"


def test_23_canonical_export_accepts_approved_reviewed_source_packs(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    import_review(inv, reviewed_decisions(inv), output_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy)
    manifest = export_records_for_date(repo_copy, "2026-06-20", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", reviewer="phase9")
    assert manifest["reviewed_record_count"] == 1


def test_24_canonical_export_rejects_unapproved_recovery_proposals(repo_copy: Path):
    write_discovery(repo_copy, "2026-06-20", [recoverable_row()])
    manifest = export_records_for_date(repo_copy, "2026-06-20", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed")
    assert manifest["reviewed_record_count"] == 0


def test_25_source_quality_metrics_are_deterministic(repo_copy: Path, tmp_path: Path):
    write_discovery(repo_copy, "2026-06-20", [wrapper_row()])
    inv = discovery_inventory(repo_copy, date_from="2026-06-20", date_to="2026-06-20")
    result = import_review(inv, reviewed_decisions(inv, "reject_source"), output_root=tmp_path / "sources", repo_root=repo_copy)
    assert source_quality_metrics(inv, result) == source_quality_metrics(inv, result)


def test_26_comparison_report_is_deterministic():
    result = {"accepted": [{"x": 1}], "rejected": [{"decision": "reject_source"}], "write_manifests": []}
    assert comparison_from_phase8(result) == comparison_from_phase8(result)


def test_27_run_recovery_writes_review_package(repo_copy: Path):
    result = run_recovery(repo_copy, date_from="2026-06-18", date_to="2026-06-19", max_records=5, report_dir=repo_copy / "data" / "universal_events" / "phase9", review_dir=repo_copy / "data" / "universal_events" / "phase9-review")
    assert Path(result["paths"]["review_json"]).exists()


def test_28_real_inventory_wrapper_only(repo_copy: Path):
    inv = discovery_inventory(repo_copy, date_from="2026-06-18", date_to="2026-06-19")
    assert inv["lead_count"] == 359
    assert inv["wrapper_url_count"] == 359
    assert inv["canonical_url_count"] == 0


def test_29_check_only_recovery_writes_no_docs(repo_copy: Path):
    run_recovery(repo_copy, date_from="2026-06-18", date_to="2026-06-18", max_records=1, report_dir=repo_copy / "reports", review_dir=repo_copy / "reviews", check_only=True)
    assert not (repo_copy / "docs" / "care-line-phase9-discovery-inventory.md").exists()


def test_30_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
