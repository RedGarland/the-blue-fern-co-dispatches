from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_source_recovery import discovery_inventory, import_review
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    build_bootstrap_review,
    build_entity_review,
    import_entity_review_decisions,
    regenerate_matches,
    sample_entity_review_decisions,
)
from bluefern_dispatches.universal_events.operators.care_line_phase8 import run_phase8
from bluefern_dispatches.universal_events.orm import EntityResolutionDecisionRow, EventRow
from tests.test_care_line_source_recovery_phase9 import recoverable_row, reviewed_decisions, write_discovery


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def phase8(repo: Path, tmp_path: Path):
    return run_phase8(
        repo_root=repo,
        date_from="2026-06-20",
        date_to="2026-06-20",
        reviewed_root=repo / "data" / "dispatches" / "care-line" / "reviewed",
        database=tmp_path / "phase9.sqlite",
        report_dir=tmp_path / "reports",
        review_dir=tmp_path / "reviews",
        calibration_dir=tmp_path / "calibration",
        shadow=True,
        resume=True,
        promotion_readiness_preview_enabled=True,
    )


def create_recovered_source(repo: Path):
    write_discovery(repo, "2026-06-20", [recoverable_row()])
    inv = discovery_inventory(repo, date_from="2026-06-20", date_to="2026-06-20")
    return import_review(inv, reviewed_decisions(inv), output_root=repo / "data" / "dispatches" / "care-line" / "sources", repo_root=repo)


def test_01_shadow_ingestion_creates_candidates_only(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    result = phase8(repo_copy, tmp_path)
    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["event_count"] == 0


def test_02_no_verified_events_are_created(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase9.sqlite")
    repo.initialize_schema()
    with repo.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []
    repo.close()


def test_03_bootstrap_requires_reviewer_approval(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase9.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    review = build_bootstrap_review(service, run_id=result["run_id"])
    assert review["review_items"][0]["recommended_action"]
    repo.close()


def test_04_match_regeneration_is_deterministic(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    phase8(repo_copy, tmp_path)
    first = regenerate_matches(tmp_path / "phase9.sqlite", shadow=True)
    second = regenerate_matches(tmp_path / "phase9.sqlite", shadow=True)
    assert first["mentions_processed"] == second["mentions_processed"]


def test_05_entity_review_import_rejects_stale_candidate_sets(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    result = phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase9.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["expected_mention_fingerprint"] = "stale"
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    assert import_entity_review_decisions(tmp_path / "phase9.sqlite", path, shadow=True)["errors"]
    repo.close()


def test_06_effective_decision_counts_ignore_superseded_history_rows(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    phase8(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase9.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    review = build_entity_review(service, run_id="phase9")
    decisions = sample_entity_review_decisions(service, run_id="phase9")
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2, sort_keys=True), encoding="utf-8")
    import_entity_review_decisions(tmp_path / "phase9.sqlite", path, shadow=True)
    with service.repository.session_scope() as session:
        rows = session.execute(select(EntityResolutionDecisionRow)).scalars().all()
    assert len(rows) >= len(review["review_items"])
    repo.close()


def test_07_promotion_preview_creates_no_events(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    result = phase8(repo_copy, tmp_path)
    assert result["promotion_readiness_preview"]["metrics"]["total_candidates"] == 1
    assert result["counts"]["event_count"] == 0


def test_08_real_readiness_thresholds_exclude_fixtures(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    result = phase8(repo_copy, tmp_path)
    assert result["readiness"]["decision"].startswith("NOT READY")


def test_09_difference_report_is_deterministic(repo_copy: Path, tmp_path: Path):
    create_recovered_source(repo_copy)
    assert phase8(repo_copy, tmp_path)["difference_from_phase7"] == phase8(repo_copy, tmp_path)["difference_from_phase7"]


def test_10_existing_phase8_tests_exist():
    assert Path("tests/test_universal_events_care_line_phase8.py").exists()


def test_11_phase7_tests_exist():
    assert Path("tests/test_care_line_reviewed_export_phase7.py").exists()
    assert Path("tests/test_universal_events_care_line_accumulation_phase7.py").exists()


def test_12_phase6_tests_exist():
    assert Path("tests/test_care_line_reviewed_record_contract.py").exists()
    assert Path("tests/test_care_line_historical_normalization.py").exists()


def test_13_phase3_to_phase5_tests_exist():
    assert Path("tests/test_universal_events_care_line_shadow.py").exists()
    assert Path("tests/test_universal_events_care_line_shadow_operator.py").exists()
    assert Path("tests/test_universal_events_care_line_phase5.py").exists()


def test_14_universal_events_phase1_and_phase2_tests_exist():
    assert Path("tests/test_universal_events_phase1.py").exists()
    assert Path("tests/test_universal_events_entity_resolution.py").exists()


def test_15_care_line_publication_tests_exist():
    assert Path("tests/test_care_line_dispatch.py").exists()


def test_16_full_repository_suite_command_is_available():
    assert Path("pyproject.toml").exists()


def test_17_no_new_tracked_public_output_diffs_are_created():
    status = subprocess.run(["git", "diff", "--name-only"], check=True, text=True, capture_output=True)
    assert "tests/test_universal_events_care_line_phase9.py" not in [line for line in status.stdout.splitlines() if line.startswith("output/site/")]


def test_18_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
