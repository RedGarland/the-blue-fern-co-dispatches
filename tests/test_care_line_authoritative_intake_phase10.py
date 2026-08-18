from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_authoritative_intake import (
    INTAKE_BATCH_SCHEMA_VERSION,
    INTAKE_SCHEMA_VERSION,
    difference_from_phase9,
    import_intake,
    is_disallowed_url,
    load_intake,
    template_from_inventory,
    validate_batch,
    write_templates,
)
from bluefern_dispatches.care_line_reviewed_export import export_records_for_date
from bluefern_dispatches.care_line_source_recovery import discovery_inventory


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def proposal_inventory(root: Path) -> dict:
    return discovery_inventory(root, date_from="2026-06-18", date_to="2026-06-18", max_records=3)


def valid_intake(inventory: dict, idx: int = 0, **updates) -> dict:
    proposal = inventory["proposals"][idx]
    row = {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "intake_record_id": f"phase10-intake-{idx}",
        "discovery_record_id": proposal["discovery_record_id"],
        "discovery_date": proposal["discovery_date"],
        "expected_source_payload_fingerprint": proposal["source_payload_fingerprint"],
        "expected_proposal_fingerprint": proposal["proposal_fingerprint"],
        "reviewer": "phase10-reviewer",
        "review_reason": "Reviewer checked the canonical publisher source and supporting passage.",
        "reviewed_at": "2026-07-21T12:00:00Z",
        "canonical_source_url": f"https://publisher.example.org/care-line/{idx}",
        "source_title": "County hospital announces labor unit suspension",
        "publisher": "Publisher Example",
        "publication_date": "2026-06-18",
        "source_type": "publisher_article",
        "source_role": "clinic_operations_signal",
        "supporting_passage": "The publisher article says the hospital will suspend labor and delivery services and redirect patients to nearby facilities.",
        "event_type": "service_suspension",
        "service_line": "labor_and_delivery",
        "facility_name": "County Hospital",
        "provider_name": "County Hospital",
        "parent_organization": "",
        "operator_name": "",
        "former_owner": "",
        "new_owner": "",
        "facility_type": "hospital",
        "address_line_1": "100 Main St",
        "address_line_2": "",
        "city": "Example City",
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
    return {"schema_version": INTAKE_BATCH_SCHEMA_VERSION, "batch_id": "phase10-test-batch", "reviewer": "phase10-reviewer", "records": records}


def test_01_google_news_url_is_rejected(repo_copy: Path):
    assert is_disallowed_url("https://news.google.com/rss/articles/example")[0]


def test_02_search_engine_result_url_is_rejected(repo_copy: Path):
    assert is_disallowed_url("https://www.google.com/search?q=hospital")[0]


def test_03_canonical_publisher_url_is_accepted(repo_copy: Path):
    assert is_disallowed_url("https://publisher.example.org/story")[0] is False


def test_04_publisher_is_required(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, publisher="")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_publisher" in result["results"][0]["rejection_reasons"]


def test_05_supporting_passage_is_required(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, supporting_passage="")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_evidence" in result["results"][0]["rejection_reasons"]


def test_06_reviewer_is_required(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, reviewer="")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "reviewer_missing" in result["results"][0]["rejection_reasons"]


def test_07_review_reason_is_required(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, review_reason="")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "review_reason_missing" in result["results"][0]["rejection_reasons"]


def test_08_discovery_fingerprint_mismatch_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, expected_source_payload_fingerprint="stale")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "stale_discovery_fingerprint" in result["results"][0]["rejection_reasons"]


def test_09_source_fingerprint_mismatch_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, expected_proposal_fingerprint="stale")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "stale_source_fingerprint" in result["results"][0]["rejection_reasons"]


def test_10_unsupported_event_type_is_rejected(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, event_type="unsupported")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "unsupported_event_type" in result["results"][0]["rejection_reasons"]


def test_11_missing_facility_fails_facility_profile(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    row = valid_intake(inv, event_type="facility_closure", service_line="", facility_name="", provider_name="")
    result = validate_batch(inv, batch([row]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_facility_or_provider" in result["results"][0]["rejection_reasons"]


def test_12_missing_service_line_fails_service_profile(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, service_line="")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert "missing_service_line" in result["results"][0]["rejection_reasons"]


def test_13_non_operational_record_may_remain_care_line_only(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    row = valid_intake(inv, event_type="resource_context", service_line="", facility_name="", provider_name="", city="", announcement_date="", universal_event_eligible=False)
    result = validate_batch(inv, batch([row]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert result["results"][0]["final_decision"] == "accepted"


def test_14_reviewer_supplied_provenance_is_preserved(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["intake_record_id"] == "phase10-intake-0")
    assert row["field_provenance"]["canonical_source_url"]["provenance_type"] == "reviewer_supplied"


def test_15_discovery_wrapper_provenance_is_preserved(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["intake_record_id"] == "phase10-intake-0")
    assert row["discovery_provenance"]["wrapper_url"].startswith("https://news.google.com")


def test_16_canonical_url_never_replaces_wrapper_provenance(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    rows = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["intake_record_id"] == "phase10-intake-0")
    assert row["source_url"] != row["discovery_provenance"]["wrapper_url"]


def test_17_check_only_writes_nothing(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    target = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json"
    before = target.exists()
    result = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, check_only=True)
    assert result["accepted"]
    assert target.exists() is before


def test_18_apply_writes_accepted_source_record(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert result["write_manifests"]


def test_19_existing_manual_source_records_remain_intact(repo_copy: Path):
    manual = repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-05-23" / "manual_sources.json"
    before = manual.read_text(encoding="utf-8")
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert manual.read_text(encoding="utf-8") == before


def test_20_source_pack_ordering_is_deterministic(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    rows = [valid_intake(inv, 1, intake_record_id="b"), valid_intake(inv, 0, intake_record_id="a")]
    import_intake(inv, batch(rows), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    payload = json.loads((repo_copy / "data" / "dispatches" / "care-line" / "sources" / "2026-06-18" / "manual_sources.json").read_text(encoding="utf-8"))
    assert [row["source_record_id"] for row in payload] == sorted(row["source_record_id"] for row in payload)


def test_21_duplicate_submission_is_idempotent(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    one = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    two = import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert one["write_manifests"][0]["after_hash"] == two["write_manifests"][0]["after_hash"]


def test_22_corrected_intake_supersedes_prior_intake(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, supersedes_intake_record_id="phase10-intake-old")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert result["results"][0]["final_decision"] == "superseded"


def test_23_withdrawn_intake_preserves_history(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    result = validate_batch(inv, batch([valid_intake(inv, withdrawal_status="withdrawn")]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources")
    assert result["results"][0]["final_decision"] == "withdrawn"


def test_24_partial_mode_writes_only_valid_rows(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    rows = [valid_intake(inv, 0), valid_intake(inv, 1, canonical_source_url="https://news.google.com/rss/articles/bad")]
    result = import_intake(inv, batch(rows), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True, allow_partial=True)
    assert len(result["accepted"]) == 1
    assert result["write_manifests"]


def test_25_default_mode_is_all_or_nothing(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    rows = [valid_intake(inv, 0), valid_intake(inv, 1, canonical_source_url="https://news.google.com/rss/articles/bad")]
    result = import_intake(inv, batch(rows), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    assert result["all_or_nothing_blocked"]
    assert not result["write_manifests"]


def test_26_public_output_paths_are_refused(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    with pytest.raises(ValueError):
        import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "output" / "site", repo_root=repo_copy, apply=True)


def test_27_pages_paths_are_refused(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    with pytest.raises(ValueError):
        import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "bluefern-dispatches-pages", repo_root=repo_copy, apply=True)


def test_28_csv_and_json_intake_normalize_identically(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    row = valid_intake(inv)
    json_path = tmp_path / "intake.json"
    csv_path = tmp_path / "intake.csv"
    json_path.write_text(json.dumps(batch([row]), indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    assert load_intake(json_path)["records"][0]["canonical_source_url"] == load_intake(csv_path)["records"][0]["canonical_source_url"]


def test_29_canonical_export_accepts_reviewed_source(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, apply=True)
    manifest = export_records_for_date(repo_copy, "2026-06-18", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", reviewer="phase10")
    assert manifest["reviewed_record_count"] >= 1
    assert "news-google-com-b8d228c64c59" in manifest["record_ids"]
    assert manifest["universal_event_ready_count"] >= 1


def test_30_canonical_export_rejects_unapproved_intake(repo_copy: Path):
    manifest = export_records_for_date(repo_copy, "2026-06-18", output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", reviewer="phase10")
    assert manifest["reviewed_record_count"] == 0


def test_31_template_generation_includes_json_csv_and_guide(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    paths = write_templates(inv, sample_id="phase10-template", output_dir=tmp_path, max_records=2)
    assert Path(paths["json_template"]).exists()
    assert Path(paths["csv_template"]).exists()
    assert Path(paths["guide"]).exists()


def test_32_template_separates_read_only_and_editable_fields(repo_copy: Path):
    inv = proposal_inventory(repo_copy)
    template = template_from_inventory(inv, sample_id="phase10-template", max_records=1)
    assert "read_only" in template["rows"][0]
    assert "reviewer_editable" in template["rows"][0]


def test_33_difference_report_is_deterministic():
    result = {"validation": {"record_count": 1}, "accepted": [{"x": 1}], "rejected": [], "deferred": [], "write_manifests": []}
    assert difference_from_phase9(result) == difference_from_phase9(result)


def test_34_validation_report_files_are_written(repo_copy: Path, tmp_path: Path):
    inv = proposal_inventory(repo_copy)
    report = tmp_path / "validation.json"
    import_intake(inv, batch([valid_intake(inv)]), source_root=repo_copy / "data" / "dispatches" / "care-line" / "sources", repo_root=repo_copy, report=report, check_only=True)
    assert report.exists()
    assert (tmp_path / "phase10-test-batch.validation.md").exists()


def test_35_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
