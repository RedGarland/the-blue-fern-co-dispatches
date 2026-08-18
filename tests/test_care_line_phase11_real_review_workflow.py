from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_authoritative_intake import (
    INTAKE_BATCH_SCHEMA_VERSION,
    INTAKE_SCHEMA_VERSION,
    difference_from_phase9,
    import_intake,
    research_workbook_from_batch,
    select_research_batch,
    validate_batch,
    workbook_completion_status,
    write_research_packet,
)
from bluefern_dispatches.care_line_reviewed_export import export_records_for_date, export_range
from bluefern_dispatches.care_line_source_recovery import discovery_inventory
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    build_bootstrap_review,
    import_entity_review_decisions,
    sample_entity_review_decisions,
)
from bluefern_dispatches.universal_events.operators.care_line_phase8 import run_phase8
from bluefern_dispatches.universal_events.orm import EventRow


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def inventory(root: Path, max_records: int = 40) -> dict:
    return discovery_inventory(root, date_from="2026-06-18", date_to="2026-06-19", max_records=max_records)


def proposal_inventory(root: Path) -> dict:
    return discovery_inventory(root, date_from="2026-06-18", date_to="2026-06-18", max_records=5)


def valid_intake(inv: dict, idx: int = 0, **updates) -> dict:
    proposal = inv["proposals"][idx]
    row = {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "intake_record_id": f"phase11-intake-{idx}",
        "discovery_record_id": proposal["discovery_record_id"],
        "discovery_date": proposal["discovery_date"],
        "expected_source_payload_fingerprint": proposal["source_payload_fingerprint"],
        "expected_proposal_fingerprint": proposal["proposal_fingerprint"],
        "reviewer": "phase11-reviewer",
        "review_reason": "Reviewer located canonical authoritative source and supporting passage.",
        "reviewed_at": "2026-07-21T12:00:00Z",
        "canonical_source_url": f"https://authoritative.example.org/care-line/{idx}",
        "source_title": "Clinic announces access-impacting service suspension",
        "publisher": "Authoritative Example",
        "publication_date": "2026-06-18",
        "source_type": "publisher_article",
        "source_role": "clinic_operations_signal",
        "supporting_passage": "The source states the clinic will suspend primary care services and direct affected patients to another site.",
        "event_type": "service_suspension",
        "service_line": "primary_care",
        "facility_name": f"Example Clinic {idx}",
        "provider_name": f"Example Clinic {idx}",
        "parent_organization": "",
        "operator_name": "",
        "former_owner": "",
        "new_owner": "",
        "facility_type": "clinic",
        "address_line_1": f"{100 + idx} Main St",
        "address_line_2": "",
        "city": f"Example City {idx}",
        "county": "Example County",
        "state": "IA",
        "postal_code": "50000",
        "country_code": "US",
        "announcement_date": "2026-06-18",
        "effective_date": "2026-07-01",
        "date_precision": "day",
        "permanence": "temporary_or_unknown",
        "evidence_level": "publisher_source",
        "evidence_strength": "reviewed",
        "is_primary_source": False,
        "care_line_public_eligible": False,
        "universal_event_eligible": True,
        "duplicate_of_record_id": "",
        "supersedes_intake_record_id": "",
        "withdrawal_status": "",
        "review_notes": "",
    }
    row.update(updates)
    return row


def batch(records: list[dict]) -> dict:
    return {"schema_version": INTAKE_BATCH_SCHEMA_VERSION, "batch_id": "phase11-reviewed-batch", "records": records}


def phase11_shadow(repo: Path, tmp_path: Path) -> dict:
    return run_phase8(
        repo_root=repo,
        date_from="2026-05-23",
        date_to="2026-06-18",
        reviewed_root=repo / "data" / "dispatches" / "care-line" / "reviewed",
        database=tmp_path / "phase11.sqlite",
        report_dir=tmp_path / "reports",
        review_dir=tmp_path / "reviews",
        calibration_dir=tmp_path / "calibration",
        shadow=True,
        resume=True,
        normalization_review=True,
        generate_bootstrap=True,
        generate_entity_review=True,
        promotion_readiness_preview_enabled=True,
    )


def test_01_research_batch_selects_reviewable_range(repo_copy: Path):
    selected = select_research_batch(inventory(repo_copy), batch_id="phase11-test", min_records=25, max_records=40)
    assert 25 <= selected["record_count"] <= 40


def test_02_research_batch_preserves_discovery_fingerprints(repo_copy: Path):
    selected = select_research_batch(inventory(repo_copy), batch_id="phase11-test", min_records=25, max_records=40)
    assert all(row["source_payload_fingerprint"] for row in selected["records"])
    assert all(row["proposal_fingerprint"] for row in selected["records"])


def test_03_blank_research_workbook_requires_human_review(repo_copy: Path):
    workbook = research_workbook_from_batch(select_research_batch(inventory(repo_copy), batch_id="phase11-test"))
    assert workbook_completion_status(workbook)["decision"] == "HUMAN SOURCE REVIEW REQUIRED"


def test_04_research_packet_writes_json_csv_and_guide(repo_copy: Path, tmp_path: Path):
    paths = write_research_packet(select_research_batch(inventory(repo_copy), batch_id="phase11-test"), output_dir=tmp_path)
    assert Path(paths["batch_selection_report"]).exists()
    assert Path(paths["research_workbook_json"]).exists()
    assert Path(paths["research_workbook_csv"]).exists()
    assert Path(paths["research_guide"]).exists()


def test_05_research_guide_blocks_wrapper_evidence(repo_copy: Path, tmp_path: Path):
    paths = write_research_packet(select_research_batch(inventory(repo_copy), batch_id="phase11-test"), output_dir=tmp_path)
    guide = Path(paths["research_guide"]).read_text(encoding="utf-8")
    assert "Do not use Google News wrappers" in guide


def test_06_empty_workbook_does_not_create_source_packs(repo_copy: Path):
    source_pack = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json"
    source_pack.unlink(missing_ok=True)
    workbook = research_workbook_from_batch(select_research_batch(inventory(repo_copy), batch_id="phase11-test"))
    assert workbook_completion_status(workbook)["completed_record_count"] == 0
    assert not source_pack.exists()


def test_07_completed_authoritative_row_validates(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert result["results"][0]["final_decision"] == "accepted"


def test_08_wrapper_url_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, canonical_source_url="https://news.google.com/rss/articles/bad")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "wrapper_url" in result["results"][0]["rejection_reasons"]


def test_09_google_news_publisher_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, publisher="Google News")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_publisher" in result["results"][0]["rejection_reasons"]


def test_10_headline_only_evidence_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    headline = inv["proposals"][0]["headline"]
    result = validate_batch(inv, batch([valid_intake(inv, supporting_passage=headline)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_evidence" in result["results"][0]["rejection_reasons"]


def test_11_snippet_only_evidence_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    snippet = inv["proposals"][0]["snippet"]
    result = validate_batch(inv, batch([valid_intake(inv, supporting_passage=snippet)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_evidence" in result["results"][0]["rejection_reasons"]


def test_12_stale_discovery_fingerprint_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, expected_source_payload_fingerprint="stale")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "stale_discovery_fingerprint" in result["results"][0]["rejection_reasons"]


def test_13_duplicate_canonical_source_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    first = valid_intake(inv, 0, canonical_source_url="https://authoritative.example.org/duplicate")
    import_intake(inv, batch([first]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    second = valid_intake(inv, 1, canonical_source_url="https://authoritative.example.org/duplicate")
    result = validate_batch(inv, batch([second]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "duplicate_source" in result["results"][0]["rejection_reasons"]


def test_14_accepted_import_writes_reviewed_source_pack(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert result["write_manifests"]


def test_15_existing_manual_source_records_remain_intact(repo_copy: Path):
    existing = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-05-23" / "manual_sources.json"
    before = existing.read_text(encoding="utf-8")
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert existing.read_text(encoding="utf-8") == before


def test_16_discovery_provenance_remains_separate(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["intake_record_id"] == "phase11-intake-0")
    assert row["discovery_provenance"]["wrapper_url"].startswith("https://news.google.com")


def test_17_canonical_url_does_not_replace_wrapper_provenance(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["intake_record_id"] == "phase11-intake-0")
    assert row["source_url"] != row["discovery_provenance"]["wrapper_url"]


def test_18_check_only_writes_nothing(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    target = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json"
    target.unlink(missing_ok=True)
    result = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, check_only=True)
    assert result["accepted"]
    assert not target.exists()


def test_19_canonical_export_excludes_raw_discovery_by_default(repo_copy: Path):
    (repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").unlink(missing_ok=True)
    manifest = export_records_for_date(repo_copy, "2026-06-18", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", check_only=True)
    assert manifest["reviewed_record_count"] == 0
    assert manifest["missing_dates"] == ["2026-06-18"]


def test_20_discovery_diagnostics_are_not_canonical_input(repo_copy: Path):
    manifest = export_records_for_date(
        repo_copy,
        "2026-06-18",
        output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed",
        check_only=True,
        include_discovery_diagnostics=True,
    )
    assert manifest["discovery_diagnostics"]["included_in_canonical_export"] is False


def test_21_canonical_export_accepts_reviewed_source(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    manifest = export_records_for_date(repo_copy, "2026-06-18", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", check_only=True)
    assert "news-google-com-b8d228c64c59" in manifest["record_ids"]


def test_22_export_refuses_public_output_paths(repo_copy: Path):
    with pytest.raises(ValueError):
        export_range(repo_copy, date_from="2026-06-18", date_to="2026-06-18", output_root=repo_copy / "output" / "site", check_only=True)


def test_23_export_refuses_pages_paths(repo_copy: Path):
    with pytest.raises(ValueError):
        export_range(repo_copy, date_from="2026-06-18", date_to="2026-06-18", output_root=repo_copy / "bluefern-dispatches-pages", check_only=True)


def test_24_cli_creates_research_packet(repo_copy: Path, tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bluefern_dispatches.care_line_authoritative_intake",
            "--repo-root",
            str(repo_copy),
            "--date-from",
            "2026-06-18",
            "--date-to",
            "2026-06-19",
            "--max-records",
            "40",
            "--research-packet-dir",
            str(tmp_path / "phase11"),
            "--batch-id",
            "phase11-cli",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    payload = json.loads(result.stdout)
    assert payload["completion"]["decision"] == "HUMAN SOURCE REVIEW REQUIRED"


def test_25_shadow_ingestion_creates_candidates_only(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    result = phase11_shadow(repo_copy, tmp_path)
    assert result["counts"]["candidate_count"] >= 1
    assert result["counts"]["event_count"] == 0


def test_26_shadow_database_has_no_verified_events(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    phase11_shadow(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase11.sqlite")
    repo.initialize_schema()
    with repo.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []
    repo.close()


def test_27_bootstrap_requires_separate_review(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    result = phase11_shadow(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase11.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert build_bootstrap_review(service, run_id=result["run_id"])["review_items"]
    repo.close()


def test_28_entity_review_import_rejects_stale_fingerprints(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    result = phase11_shadow(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase11.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["expected_mention_fingerprint"] = "stale"
    path = tmp_path / "entity-review.json"
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    assert import_entity_review_decisions(tmp_path / "phase11.sqlite", path, shadow=True)["errors"]
    repo.close()


def test_29_calibration_excludes_fixture_labels(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    result = phase11_shadow(repo_copy, tmp_path)
    assert result["calibration"]["sample_label"] == "insufficient_sample"


def test_30_promotion_preview_creates_no_events(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    result = phase11_shadow(repo_copy, tmp_path)
    assert "metrics" in result["promotion_readiness_preview"]
    assert result["counts"]["event_count"] == 0


def test_31_difference_report_is_deterministic(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    intake_result = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    shadow = phase11_shadow(repo_copy, tmp_path)
    assert difference_from_phase9(intake_result, shadow_result=shadow) == difference_from_phase9(intake_result, shadow_result=shadow)


def test_32_public_output_diff_is_not_created_by_phase11_tests():
    status = subprocess.run(["git", "diff", "--name-only"], check=True, text=True, capture_output=True)
    assert "tests/test_care_line_phase11_real_review_workflow.py" not in [line for line in status.stdout.splitlines() if line.startswith("output/site/")]


def test_33_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
