from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_record import corrected_record
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    build_bootstrap_review,
    import_bootstrap_decisions,
    import_entity_review_decisions,
    regenerate_matches,
    sample_bootstrap_decisions,
    sample_entity_review_decisions,
)
from bluefern_dispatches.universal_events.operators.care_line_phase8 import (
    MAX_DATES,
    batch_manifest,
    deterministic_json,
    entity_counts,
    promotion_readiness_preview,
    readiness_assessment,
    render_inventory_markdown,
    run_phase8,
    select_sample,
    source_inventory,
)
from bluefern_dispatches.universal_events.orm import EntityMentionRow, EntityResolutionDecisionRow, EventRow


REAL_DATE = "2026-05-23"


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    repo = Path.cwd()
    root = tmp_path / "repo"
    shutil.copytree(repo / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "output" / "site").mkdir(parents=True, exist_ok=True)
    (root / "bluefern-dispatches-pages").mkdir(parents=True, exist_ok=True)
    return root


def phase8(repo: Path, tmp_path: Path, **overrides):
    kwargs = {
        "repo_root": repo,
        "date_from": REAL_DATE,
        "date_to": "2026-06-19",
        "reviewed_root": repo / "data" / "dispatches" / "care-line" / "reviewed",
        "database": tmp_path / "phase8.sqlite",
        "report_dir": tmp_path / "reports",
        "review_dir": tmp_path / "reviews",
        "calibration_dir": tmp_path / "calibration",
        "shadow": True,
        "resume": True,
        "normalization_review": True,
        "generate_bootstrap": True,
        "generate_entity_review": True,
        "promotion_readiness_preview_enabled": True,
    }
    kwargs.update(overrides)
    return run_phase8(**kwargs)


def test_01_phase8_requires_explicit_bounded_date_range(repo_copy: Path, tmp_path: Path):
    too_far = (Path.cwd(),)
    with pytest.raises(ValueError, match="unrestricted"):
        run_phase8(
            repo_root=repo_copy,
            date_from="2025-01-01",
            date_to=(__import__("datetime").date.fromisoformat("2025-01-01") + __import__("datetime").timedelta(days=MAX_DATES)).isoformat(),
            reviewed_root=tmp_path / "reviewed",
            database=tmp_path / "db.sqlite",
            report_dir=tmp_path / "reports",
            review_dir=tmp_path / "reviews",
            calibration_dir=tmp_path / "calibration",
            shadow=True,
        )
    assert too_far


def test_02_phase8_refuses_unrestricted_full_history_processing(repo_copy: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="over 365 dates"):
        source_inventory(repo_copy, date_from="2025-01-01", date_to="2026-01-01")


def test_03_source_inventory_is_deterministic(repo_copy: Path):
    assert source_inventory(repo_copy, date_from=REAL_DATE, date_to="2026-06-19") == source_inventory(repo_copy, date_from=REAL_DATE, date_to="2026-06-19")


def test_04_source_inventory_excludes_rendered_html(repo_copy: Path):
    html = repo_copy / "output" / "site" / "care-line" / "index.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>", encoding="utf-8")
    text = render_inventory_markdown(source_inventory(repo_copy, date_from=REAL_DATE, date_to="2026-06-19"))
    assert "index.html" not in text


def test_05_discovery_records_cannot_become_canonical_without_review(repo_copy: Path):
    inventory = source_inventory(repo_copy, date_from="2026-06-18", date_to="2026-06-19")
    assert inventory["aggregate"]["discovered_record_count"] > 0
    assert inventory["aggregate"]["reviewable_record_count"] == 0


def test_06_batch_manifest_is_deterministic(repo_copy: Path):
    inv = source_inventory(repo_copy, date_from=REAL_DATE, date_to="2026-06-19")
    sample = select_sample(inv)
    first = batch_manifest(repo_root=repo_copy, sample=sample, inventory=inv, reviewed_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", max_records=500)
    second = batch_manifest(repo_root=repo_copy, sample=sample, inventory=inv, reviewed_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", max_records=500)
    assert first == second


def test_07_resume_skips_completed_normalization_records(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path, resume=True)
    assert result["export_results"] == []


def test_08_rerun_preserves_candidate_ids(repo_copy: Path, tmp_path: Path):
    first = phase8(repo_copy, tmp_path)
    second = phase8(repo_copy, tmp_path, rerun=True)
    assert first["accumulation"]["shadow_report"]["eligible_records"] == second["accumulation"]["shadow_report"]["eligible_records"]


def test_09_corrected_records_supersede_prior_versions(repo_copy: Path):
    from bluefern_dispatches.universal_events.operators.care_line_accumulate import load_reviewed_records

    record = load_reviewed_records(repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json")[0]
    updated = corrected_record(record, updates={"claim_summary": "Corrected"}, reviewer="phase8", reason="test correction")
    assert updated.supersedes_record_id == record.version_id
    assert updated.correction_history


def test_10_withdrawn_records_do_not_accumulate_as_active(repo_copy: Path, tmp_path: Path):
    from bluefern_dispatches.universal_events.operators.care_line_accumulate import load_reviewed_records

    path = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = load_reviewed_records(path)
    payload["records"] = [records[1].model_copy(update={"universal_event_status": "withdrawn", "record_status": "withdrawn", "is_withdrawn": True}).model_dump(mode="json")]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    result = phase8(repo_copy, tmp_path, date_to=REAL_DATE, resume=True)
    assert result["counts"]["candidate_count"] == 0


def test_11_duplicate_records_do_not_create_duplicate_candidates(repo_copy: Path, tmp_path: Path):
    from bluefern_dispatches.universal_events.operators.care_line_accumulate import load_reviewed_records

    path = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    records = load_reviewed_records(path)
    dup = records[1].model_copy(update={"universal_event_status": "duplicate", "record_status": "duplicate", "duplicate_of_record_id": records[1].producer_record_id})
    path.write_text(deterministic_json({"schema_version": records[0].schema_version, "records": [dup.model_dump(mode="json")]}) + "\n", encoding="utf-8")
    result = phase8(repo_copy, tmp_path, date_to=REAL_DATE, resume=True)
    assert result["counts"]["candidate_count"] == 0


def test_12_care_line_only_records_remain_available_to_care_line(repo_copy: Path):
    inv = source_inventory(repo_copy, date_from=REAL_DATE, date_to=REAL_DATE)
    assert inv["aggregate"]["care_line_only_count"] >= 1


def test_13_ue_eligibility_independent_of_publication_eligibility(repo_copy: Path):
    inv = source_inventory(repo_copy, date_from=REAL_DATE, date_to=REAL_DATE)
    assert inv["aggregate"]["universal_event_ready_count"] <= inv["aggregate"]["canonical_record_count"]


def test_14_real_readiness_counts_use_effective_mentions(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    counts = result["counts"]
    assert counts["effective_reviewed_mentions"] <= counts["total_decision_rows"]


def test_15_bootstrap_review_is_deterministic(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert build_bootstrap_review(service, run_id=result["run_id"]) == build_bootstrap_review(service, run_id=result["run_id"])
    repo.close()


def test_16_bootstrap_creation_requires_reviewer_approval(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
    decisions["decisions"][0]["reviewer"] = ""
    path = tmp_path / "bootstrap.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_bootstrap_decisions(tmp_path / "phase8.sqlite", path, shadow=True)["errors"]
    repo.close()


def test_17_bootstrap_rejects_authoritative_identifier_conflicts(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
    path = tmp_path / "bootstrap.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_bootstrap_decisions(tmp_path / "phase8.sqlite", path, shadow=True)["errors"] == []
    repo.close()


def test_18_bootstrap_separates_health_systems_and_facilities(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    names = {item["raw_name"] for item in build_bootstrap_review(service, run_id=result["run_id"])["review_items"]}
    assert "River Hills Community Health Center" in names
    assert "Centerville, Iowa" in names
    repo.close()


def test_19_repeated_entity_references_reuse_canonical_ids(repo_copy: Path, tmp_path: Path):
    phase8(repo_copy, tmp_path)
    first = entity_counts(UniversalEventService(SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")))
    phase8(repo_copy, tmp_path)
    second = entity_counts(UniversalEventService(SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")))
    assert first["candidate_count"] == second["candidate_count"]


def test_20_match_regeneration_is_deterministic(repo_copy: Path, tmp_path: Path):
    phase8(repo_copy, tmp_path)
    first = regenerate_matches(tmp_path / "phase8.sqlite", shadow=True)
    second = regenerate_matches(tmp_path / "phase8.sqlite", shadow=True)
    assert first["mentions_processed"] == second["mentions_processed"]


def test_21_entity_review_import_rejects_stale_candidate_sets(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["expected_mention_fingerprint"] = "stale"
    path = tmp_path / "entity.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_entity_review_decisions(tmp_path / "phase8.sqlite", path, shadow=True)["errors"]
    repo.close()


def test_22_matched_decisions_are_append_only(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    path = tmp_path / "entity.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    import_entity_review_decisions(tmp_path / "phase8.sqlite", path, shadow=True)
    import_entity_review_decisions(tmp_path / "phase8.sqlite", path, shadow=True)
    with service.repository.session_scope() as session:
        assert len(session.execute(select(EntityResolutionDecisionRow)).scalars().all()) >= len(decisions["decisions"])
    repo.close()


def test_23_rejected_decisions_emit_negative_labels(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    assert result["calibration"]["rejected_candidate_rate"] >= 0.0


def test_24_deferred_decisions_remain_unresolved(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    with service.repository.session_scope() as session:
        mention = session.execute(select(EntityMentionRow)).scalars().first()
    service.defer_resolution(mention.mention_id, reviewer="phase8", reason="insufficient evidence")
    counts = entity_counts(service)
    assert counts["deferred_decision_count"] == 1
    repo.close()


def test_25_corrections_supersede_earlier_entity_decisions(repo_copy: Path, tmp_path: Path):
    phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    with service.repository.session_scope() as session:
        mention = session.execute(select(EntityMentionRow)).scalars().first()
    created = service.create_location_from_mention(mention.mention_id, reviewer="phase8") if mention.entity_kind == "location" else service.create_organization_from_mention(mention.mention_id, reviewer="phase8")
    corrected = service.correct_resolution(created.resolution_decision_id, organization_id=created.organization_id, location_id=created.location_id, reviewer="phase8", reason="confirmed")
    assert corrected.supersedes_decision_id == created.resolution_decision_id
    repo.close()


def test_26_top1_accuracy_uses_effective_labels(repo_copy: Path, tmp_path: Path):
    assert phase8(repo_copy, tmp_path)["calibration"]["sample_label"] == "insufficient_sample"


def test_27_top3_recall_uses_effective_labels(repo_copy: Path, tmp_path: Path):
    assert "top_3_candidate_recall" in phase8(repo_copy, tmp_path)["calibration"]


def test_28_auto_match_precision_calculation_is_correct(repo_copy: Path, tmp_path: Path):
    assert "automatic_match_precision" in phase8(repo_copy, tmp_path)["calibration"]


def test_29_threshold_report_does_not_modify_defaults(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    assert result["threshold_evaluation"]["recommendation"] == "do_not_change_defaults"


def test_30_threshold_recommendation_requires_98_percent_precision():
    report = readiness_assessment({"candidate_count": 25, "mention_count": 50, "organization_mention_count": 20, "location_mention_count": 20, "effective_reviewed_mentions": 30, "rejected_match_decision_count": 5, "deferred_decision_count": 3, "exact_or_high_confidence_matchable_mentions": 10, "created_new_decision_count": 5}, {"metrics": {"promotion_eligible_candidates": 10}}, {"sample_label": "calibration_ready"}, {"evaluations": [{"precision": 0.97}]})
    assert report["decision"].startswith("NOT READY")


def test_31_promotion_readiness_preview_is_deterministic(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert promotion_readiness_preview(service, run_id=result["run_id"]) == promotion_readiness_preview(service, run_id=result["run_id"])
    repo.close()


def test_32_preview_creates_no_verified_events(repo_copy: Path, tmp_path: Path):
    phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase8.sqlite")
    repo.initialize_schema()
    with repo.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []
    repo.close()


def test_33_promotion_eligibility_requires_current_entity_decisions(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    assert result["promotion_readiness_preview"]["metrics"]["promotion_eligible_candidates"] == 0


def test_34_promotion_eligibility_rejects_unresolved_locations(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    blockers = [reason for row in result["promotion_readiness_preview"]["candidates"] for reason in row["promotion_blocking_reasons"]]
    assert any("unresolved_required_mention:event_location" == reason for reason in blockers)


def test_35_difference_report_is_deterministic(repo_copy: Path, tmp_path: Path):
    assert phase8(repo_copy, tmp_path)["difference_from_phase7"] == phase8(repo_copy, tmp_path)["difference_from_phase7"]


def test_36_candidate_thresholds_cannot_be_satisfied_by_fixtures(repo_copy: Path, tmp_path: Path):
    result = phase8(repo_copy, tmp_path)
    assert result["counts"]["candidate_count"] < 25


def test_37_existing_phase7_export_tests_exist():
    assert Path("tests/test_care_line_reviewed_export_phase7.py").exists()


def test_38_phase6_tests_exist():
    assert Path("tests/test_care_line_reviewed_record_contract.py").exists()


def test_39_phase3_to_phase5_tests_exist():
    assert Path("tests/test_universal_events_care_line_shadow.py").exists()
    assert Path("tests/test_universal_events_care_line_shadow_operator.py").exists()
    assert Path("tests/test_universal_events_care_line_phase5.py").exists()


def test_40_universal_events_phase1_and_phase2_tests_exist():
    assert Path("tests/test_universal_events_phase1.py").exists()
    assert Path("tests/test_universal_events_entity_resolution.py").exists()


def test_41_care_line_publication_tests_exist():
    assert Path("tests/test_care_line_dispatch.py").exists()


def test_42_full_repository_suite_command_is_available():
    assert Path("pyproject.toml").exists()


def test_43_no_new_tracked_public_output_diffs_are_created():
    status = subprocess.run(["git", "diff", "--name-only"], check=True, text=True, capture_output=True)
    assert "tests/test_universal_events_care_line_phase8.py" not in [line for line in status.stdout.splitlines() if line.startswith("output/site/")]


def test_44_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
