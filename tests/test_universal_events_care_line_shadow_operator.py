from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_shadow import (
    build_input_manifest,
    import_review_decisions,
    load_manifest_records,
    run_operator,
    shadow_run_id,
    configuration_hash,
)
from bluefern_dispatches.universal_events.orm import (
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventRow,
    OrganizationIdentifierRow,
    OrganizationRow,
    ShadowIngestionExecutionRow,
    ShadowIngestionRecordResultRow,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "care_line_shadow_records.json"


def _records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write_day(root: Path, day: str, rows: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "care-line" / "sources" / day / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    rows = _records()
    _write_day(root, "2026-06-01", rows[:5])
    _write_day(root, "2026-06-02", rows[5:10])
    _write_day(root, "2026-06-04", rows[10:16])
    (root / "bluefern-dispatches-pages").mkdir()
    (root / "output" / "site").mkdir(parents=True)
    return root


def _args(root: Path, db: Path, **overrides) -> Namespace:
    values = {
        "repo_root": str(root),
        "database": str(db),
        "date": None,
        "date_from": "2026-06-01",
        "date_to": "2026-06-04",
        "input": None,
        "report_dir": str(root / "data" / "universal_events" / "shadow" / "care-line" / "reports"),
        "review_dir": "",
        "calibration_dir": str(root / "data" / "universal_events" / "shadow" / "care-line" / "calibration"),
        "check_only": False,
        "shadow": True,
        "resume": False,
        "rerun": False,
        "strict": False,
        "max_records": None,
        "adapter_version": "care-line-shadow-v1",
        "resolver_version": "entity-resolver-v1",
        "fail_on_error": False,
        "fail_on_warning": False,
        "include_nonpublic_reviewed": False,
        "allow_future_date": False,
        "import_review": None,
    }
    values.update(overrides)
    return Namespace(**values)


def _count(db: Path, row_cls) -> int:
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    with service.repository.session_scope() as session:
        total = len(session.execute(select(row_cls)).scalars().all())
    repo.close()
    return total


def test_explicit_date_range_discovery_manifest_and_missing_dates(sample_repo: Path):
    manifest = build_input_manifest(sample_repo, date_from="2026-06-01", date_to="2026-06-04")
    again = build_input_manifest(sample_repo, date_from="2026-06-01", date_to="2026-06-04")

    assert manifest == again
    assert [row["date"] for row in manifest["input_files"]] == ["2026-06-01", "2026-06-02", "2026-06-04"]
    assert manifest["missing_dates"] == ["2026-06-03"]
    assert sum(manifest["record_counts"].values()) == 16


def test_refuses_without_explicit_date_or_input(sample_repo: Path):
    with pytest.raises(ValueError, match="explicit"):
        build_input_manifest(sample_repo)


def test_operator_refuses_without_shadow_or_public_paths(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    with pytest.raises(ValueError, match="--shadow"):
        run_operator(_args(sample_repo, db, shadow=False))
    with pytest.raises(ValueError, match="protected"):
        run_operator(_args(sample_repo, sample_repo / "output" / "site" / "bad.sqlite"))
    with pytest.raises(ValueError, match="protected"):
        run_operator(_args(sample_repo, sample_repo / "bluefern-dispatches-pages" / "bad.sqlite"))


def test_check_only_performs_no_row_writes(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    result = run_operator(_args(sample_repo, db, check_only=True))

    assert result["report"]["run_summary"]["eligible_count"] == 12
    assert _count(db, ShadowIngestionExecutionRow) == 0
    assert _count(db, EntityMentionRow) == 0


def test_stable_shadow_run_id_and_new_execution_id(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    first = run_operator(_args(sample_repo, db))
    second = run_operator(_args(sample_repo, db))

    assert first["shadow_run_id"] == second["shadow_run_id"]
    assert first["execution_id"] != second["execution_id"]
    assert _count(db, ShadowIngestionExecutionRow) == 2


def test_repeated_run_does_not_duplicate_candidates_or_record_results(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    first = run_operator(_args(sample_repo, db))
    second = run_operator(_args(sample_repo, db, resume=True))

    assert second["report"]["run_summary"]["created_candidate_count"] == 0
    assert second["report"]["run_summary"]["existing_candidate_count"] == 12
    assert _count(db, ShadowIngestionRecordResultRow) == 16


def test_rerun_preserves_candidate_ids_and_writes_diff(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    first = run_operator(_args(sample_repo, db))
    rows = _records()
    changed = dict(rows[0])
    changed["summary_or_snippet"] = "Changed source-observed summary."
    changed["withdrawn"] = True
    _write_day(sample_repo, "2026-06-01", [changed, *rows[1:5]])
    second = run_operator(_args(sample_repo, db, rerun=True))

    assert first["report"]["created_candidates"][0]["candidate_id"] in {row["candidate_id"] for row in second["report"]["existing_candidates"]}
    assert Path(second["paths"]["diff"]).exists()
    diff = json.loads(Path(second["paths"]["diff"]).read_text(encoding="utf-8"))
    assert "care-shadow-001-ld-closure" in diff["changed_payloads"]


def test_review_package_ordering_and_context(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    org = service.create_organization({"organization_id": "org-silver-lake", "canonical_name": "Silver Lake Hospital", "organization_type": "hospital"})
    service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "cms_ccn", "identifier_value": "123456", "is_authoritative": True})
    first = service.create_organization({"organization_id": "org-river-a", "canonical_name": "North River Clinic", "organization_type": "clinic"})
    second = service.create_organization({"organization_id": "org-river-b", "canonical_name": "South River Clinic", "organization_type": "clinic"})
    service.add_organization_alias({"organization_id": first.organization_id, "alias_name": "River Clinic"})
    service.add_organization_alias({"organization_id": second.organization_id, "alias_name": "River Clinic"})
    repo.close()

    result = run_operator(_args(sample_repo, db))
    review = json.loads(Path(result["paths"]["review"]).read_text(encoding="utf-8"))
    again = json.loads(Path(result["paths"]["review"]).read_text(encoding="utf-8"))

    assert review == again
    assert any(row["review_group"] == "exact_authoritative" for row in review["review_items"])
    assert any(row["review_group"] == "ambiguous" for row in review["review_items"])
    assert any(row["review_group"] == "unresolved" for row in review["review_items"])
    assert all("source_url" in row and "supporting_evidence_text" in row for row in review["review_items"])


def test_review_import_matched_created_new_rejections_corrections_and_calibration(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    calibration = tmp_path / "calibration"
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    org = service.create_organization({"organization_id": "org-silver-lake", "canonical_name": "Silver Lake Hospital", "organization_type": "hospital"})
    service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "cms_ccn", "identifier_value": "123456", "is_authoritative": True})
    repo.close()
    result = run_operator(_args(sample_repo, db))
    review = json.loads(Path(result["paths"]["review"]).read_text(encoding="utf-8"))
    exact = next(row for row in review["review_items"] if row["review_group"] == "exact_authoritative")
    unresolved = next(row for row in review["review_items"] if row["review_group"] == "unresolved" and row["entity_kind"] == "organization")
    selected = exact["ranked_match_candidates"][0]
    decisions = {
        "decisions": [
            {
                "mention_id": exact["mention_id"],
                "decision_type": "matched",
                "selected_match_candidate_id": selected["match_candidate_id"],
                "organization_id": selected["organization_id"],
                "reviewer": "reviewer",
                "decision_reason": "Exact CMS identifier",
                "confidence": 1.0,
                "expected_resolver_version": "entity-resolver-v1",
                "expected_mention_fingerprint": exact["mention_fingerprint"],
            },
            {
                "mention_id": unresolved["mention_id"],
                "decision_type": "created_new",
                "organization_type": "healthcare_facility",
                "reviewer": "reviewer",
                "decision_reason": "New facility",
                "confidence": 1.0,
                "expected_resolver_version": "entity-resolver-v1",
                "expected_mention_fingerprint": unresolved["mention_fingerprint"],
            },
        ]
    }
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    imported = import_review_decisions(db, path, shadow=True, calibration_dir=calibration)

    assert imported["errors"] == []
    assert _count(db, EntityResolutionDecisionRow) == 2
    assert (calibration / "care_line_shadow_calibration.jsonl").exists()

    stale = dict(decisions)
    stale["decisions"] = [dict(decisions["decisions"][0], expected_mention_fingerprint="bad")]
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    assert import_review_decisions(db, stale_path, shadow=True)["errors"]

    wrong = dict(decisions)
    wrong["decisions"] = [dict(decisions["decisions"][0], location_id="loc-nope", organization_id=None)]
    wrong_path = tmp_path / "wrong.json"
    wrong_path.write_text(json.dumps(wrong), encoding="utf-8")
    assert import_review_decisions(db, wrong_path, shadow=True)["errors"]

    invalid = dict(decisions)
    invalid["decisions"] = [dict(decisions["decisions"][0], selected_match_candidate_id="missing")]
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    assert import_review_decisions(db, invalid_path, shadow=True)["errors"]

    first_decision_id = None
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    with service.repository.session_scope() as session:
        first_decision_id = session.execute(select(EntityResolutionDecisionRow).where(EntityResolutionDecisionRow.decision_type == "matched")).scalars().first().resolution_decision_id
    repo.close()
    correction = {"decisions": [dict(decisions["decisions"][0], decision_type="corrected", superseded_decision_id=first_decision_id)]}
    correction_path = tmp_path / "correction.json"
    correction_path.write_text(json.dumps(correction), encoding="utf-8")
    assert import_review_decisions(db, correction_path, shadow=True)["errors"] == []
    assert _count(db, EntityResolutionDecisionRow) == 3


def test_no_verified_events_no_care_line_input_changes_no_public_output(sample_repo: Path, tmp_path: Path):
    before = {path.as_posix(): path.read_text(encoding="utf-8") for path in sample_repo.rglob("manual_sources.json")}
    public_before = sorted((sample_repo / "output" / "site").rglob("*"))

    result = run_operator(_args(sample_repo, tmp_path / "shadow.sqlite"))

    assert _count(tmp_path / "shadow.sqlite", EventRow) == 0
    after = {path.as_posix(): path.read_text(encoding="utf-8") for path in sample_repo.rglob("manual_sources.json")}
    assert after == before
    assert sorted((sample_repo / "output" / "site").rglob("*")) == public_before
    assert result["status"] == "ok"


def test_quality_metrics_are_deterministic_and_provisional(sample_repo: Path, tmp_path: Path):
    result = run_operator(_args(sample_repo, tmp_path / "shadow.sqlite"))
    summary = json.loads(Path(result["paths"]["summary"]).read_text(encoding="utf-8"))

    assert summary["quality_metrics"]["ingestion"]["eligibility_rate"] == 0.75
    assert summary["quality_metrics"]["review"]["sample_label"] == "provisional_small_sample"


def test_phase4_migration_upgrade_downgrade_reupgrade(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "shadow_ingestion_runs" in tables
    assert "shadow_ingestion_executions" in tables
    with engine.connect() as connection:
        triggers = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert "trg_shadow_ingestion_executions_append_only_update" in triggers
    engine.dispose()

    command.downgrade(cfg, "0002_universal_entity_resolution")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    tables = set(inspect(engine).get_table_names())
    assert "shadow_ingestion_runs" not in tables
    assert "entity_mentions" in tables
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    assert "shadow_ingestion_record_results" in set(inspect(engine).get_table_names())
    engine.dispose()


def test_execution_history_is_append_only(sample_repo: Path, tmp_path: Path):
    db = tmp_path / "shadow.sqlite"
    run_operator(_args(sample_repo, db))
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    with pytest.raises(IntegrityError, match="append-only"):
        with service.repository.session_scope() as session:
            row = session.execute(select(ShadowIngestionExecutionRow)).scalars().first()
            row.status = "mutated"
    repo.close()
