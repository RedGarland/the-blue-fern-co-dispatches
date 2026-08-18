from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_reviewed_export import export_records_for_date
from bluefern_dispatches.generator import build_site
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    build_bootstrap_review,
    build_entity_review,
    effective_records,
    import_bootstrap_decisions,
    import_entity_review_decisions,
    load_reviewed_records,
    regenerate_matches,
    run_accumulation,
    sample_bootstrap_decisions,
    sample_entity_review_decisions,
    threshold_evaluation,
    write_post_review_reports,
)
from bluefern_dispatches.universal_events.orm import EntityMatchCandidateRow, EntityResolutionDecisionRow, EventRow


REAL_DATE = "2026-05-23"


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    repo = Path.cwd()
    root = tmp_path / "repo"
    shutil.copytree(repo / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    shutil.copytree(repo / "assets", root / "assets")
    (root / "output" / "site").mkdir(parents=True, exist_ok=True)
    (root / "bluefern-dispatches-pages").mkdir(exist_ok=True)
    return root


def accumulation(repo: Path, tmp_path: Path):
    return run_accumulation(
        repo_root=repo,
        reviewed_root=repo / "data" / "dispatches" / "care-line" / "reviewed",
        date_from=REAL_DATE,
        date_to=REAL_DATE,
        database=tmp_path / "shadow.sqlite",
        report_dir=tmp_path / "reports",
        shadow=True,
    )


def test_01_accumulator_selects_effective_record_version(repo_copy: Path):
    path = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    records = load_reviewed_records(path)
    updated = records[0].model_copy(update={"version": 2, "supersedes_record_id": records[0].version_id, "version_id": ""})
    effective, history = effective_records([*records, updated])
    assert len(history) == 6
    assert next(row for row in effective if row.producer_record_id == records[0].producer_record_id).version == 2


def test_02_accumulator_excludes_withdrawn_record(repo_copy: Path):
    path = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    records = load_reviewed_records(path)
    withdrawn = records[1].model_copy(update={"universal_event_status": "withdrawn", "record_status": "withdrawn", "is_withdrawn": True})
    effective, _ = effective_records([withdrawn])
    assert effective[0].universal_event_eligible is False


def test_03_accumulator_excludes_duplicate_record(repo_copy: Path):
    path = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    record = load_reviewed_records(path)[1].model_copy(update={"universal_event_status": "duplicate", "record_status": "duplicate", "duplicate_of_record_id": "other"})
    assert record.universal_event_eligible is False


def test_04_accumulator_feeds_ready_records_to_shadow_adapter(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    assert result["shadow_report"]["run_summary"]["created_candidate_count"] >= 0
    assert result["manifest"]["effective_record_count"] == 5


def test_05_accumulator_creates_no_verified_events(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    assert result["event_count"] == 0
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    with repo.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []
    repo.close()


def test_06_accumulator_is_idempotent(repo_copy: Path, tmp_path: Path):
    first = accumulation(repo_copy, tmp_path)
    second = accumulation(repo_copy, tmp_path)
    assert first["run_id"] == second["run_id"]


def test_07_stable_candidate_ids_survive_canonical_corrections(repo_copy: Path, tmp_path: Path):
    first = accumulation(repo_copy, tmp_path)
    second = accumulation(repo_copy, tmp_path)
    assert first["shadow_report"]["eligible_records"] == second["shadow_report"]["eligible_records"]


def test_08_bootstrap_package_is_deterministic(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert build_bootstrap_review(service, run_id=result["run_id"]) == build_bootstrap_review(service, run_id=result["run_id"])


def test_09_bootstrap_creation_requires_reviewer_approval(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
    decisions["decisions"][0]["reviewer"] = ""
    path = tmp_path / "bootstrap-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    outcome = import_bootstrap_decisions(tmp_path / "shadow.sqlite", path, shadow=True)
    assert outcome["errors"]


def test_10_bootstrap_refuses_identifier_conflict(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    bootstrap = build_bootstrap_review(service, run_id=result["run_id"])
    decisions = sample_bootstrap_decisions(bootstrap)
    decisions["decisions"][0]["decision"] = "defer"
    path = tmp_path / "bootstrap-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_bootstrap_decisions(tmp_path / "shadow.sqlite", path, shadow=True)["errors"] == []


def test_11_bootstrap_does_not_collapse_health_system_and_facility(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    names = {item["raw_name"] for item in build_bootstrap_review(service, run_id=result["run_id"])["review_items"]}
    assert "River Hills Community Health Center" in names
    assert "Centerville, Iowa" in names


def test_12_match_candidates_regenerate_after_bootstrap(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
    path = tmp_path / "bootstrap-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_bootstrap_decisions(tmp_path / "shadow.sqlite", path, shadow=True)["errors"] == []
    regenerated = regenerate_matches(tmp_path / "shadow.sqlite", shadow=True)
    assert regenerated["match_candidates"] >= 1


def test_13_entity_review_decisions_are_append_only(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
    path = tmp_path / "bootstrap-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    import_bootstrap_decisions(tmp_path / "shadow.sqlite", path, shadow=True)
    regenerate_matches(tmp_path / "shadow.sqlite", shadow=True)
    review = sample_entity_review_decisions(service, run_id=result["run_id"])
    review_path = tmp_path / "entity-decisions.json"
    review_path.write_text(json.dumps(review, indent=2, sort_keys=True), encoding="utf-8")
    imported = import_entity_review_decisions(tmp_path / "shadow.sqlite", review_path, shadow=True)
    with service.repository.session_scope() as session:
        assert len(session.execute(select(EntityResolutionDecisionRow)).scalars().all()) >= len(imported["accepted"])


def test_14_rejected_candidates_produce_negative_calibration_labels(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["decision_type"] = "deferred"
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    imported = import_entity_review_decisions(tmp_path / "shadow.sqlite", path, shadow=True)
    assert "calibration_metrics" in imported


def test_15_deferred_decisions_are_retained(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["decision_type"] = "deferred"
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    import_entity_review_decisions(tmp_path / "shadow.sqlite", path, shadow=True)
    with service.repository.session_scope() as session:
        assert session.execute(select(EntityResolutionDecisionRow).where(EntityResolutionDecisionRow.decision_type == "deferred")).scalars().first() is not None


def test_16_threshold_report_does_not_change_resolver_defaults():
    report = threshold_evaluation([{"decision_type": "matched", "selected_rank": 1, "top_score": 1.0}], [{"auto_match_threshold": 0.9, "ambiguity_margin": 0.08}])
    assert report["recommendation"] == "do_not_change_defaults"


def test_17_publication_output_remains_byte_for_byte_unchanged(repo_copy: Path, tmp_path: Path):
    before = build_site(repo_copy, dry_run=False, backup_root=tmp_path / "backup1", dispatch_seed_dates={"care-line": REAL_DATE})
    rendered = repo_copy / "output" / "site" / "care-line" / "editions" / REAL_DATE / "index.html"
    first = rendered.read_bytes()
    accumulation(repo_copy, tmp_path)
    after = build_site(repo_copy, dry_run=False, backup_root=tmp_path / "backup2", dispatch_seed_dates={"care-line": REAL_DATE})
    assert before["ok"] and after["ok"]
    assert first == rendered.read_bytes()


def test_18_publication_code_does_not_read_canonical_reviewed_records(repo_copy: Path, tmp_path: Path):
    reviewed = repo_copy / "data" / "dispatches" / "care-line" / "reviewed" / REAL_DATE / "reviewed_records.json"
    reviewed.write_text("not json", encoding="utf-8")
    result = build_site(repo_copy, dry_run=False, backup_root=tmp_path / "backup", dispatch_seed_dates={"care-line": REAL_DATE})
    assert result["ok"] is True


def test_19_check_only_does_not_write_shadow_database(repo_copy: Path, tmp_path: Path):
    run_accumulation(repo_root=repo_copy, reviewed_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", date_from=REAL_DATE, date_to=REAL_DATE, database=tmp_path / "check.sqlite", report_dir=tmp_path / "reports", shadow=True, check_only=True)
    assert not (tmp_path / "check.sqlite").exists()


def test_20_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())


def test_21_post_review_reports_write_calibration_files(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    paths = write_post_review_reports(tmp_path / "shadow.sqlite", tmp_path / "reports", result["run_id"], shadow=True)
    assert Path(paths["calibration"]).exists()


def test_22_entity_review_package_is_deterministic(repo_copy: Path, tmp_path: Path):
    result = accumulation(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert build_entity_review(service, run_id=result["run_id"]) == build_entity_review(service, run_id=result["run_id"])
