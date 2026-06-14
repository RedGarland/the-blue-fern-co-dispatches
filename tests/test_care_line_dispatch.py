from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from bluefern_dispatches.care_line_sources import (
    build_public_edition_report,
    load_manual_source_records,
    load_pressure_source_registry,
    public_claim_rows,
    record_is_public,
    source_table_rows,
    summary_for_records,
    validate_manual_source_records,
    validate_pressure_source_registry,
)
from bluefern_dispatches.generator import build_site


def _copy_care_line_data(repo: Path, work: Path) -> None:
    source_root = repo / "data" / "dispatches" / "care-line"
    target_root = work / "data" / "dispatches" / "care-line"
    shutil.copytree(source_root, target_root)


def _copy_assets(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "assets", work / "assets")


def _work_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "care-line"
    work.mkdir(parents=True, exist_ok=True)
    _copy_assets(repo, work)
    _copy_care_line_data(repo, work)
    return work


def test_care_line_registry_and_manual_sources_validate():
    repo = Path(__file__).resolve().parents[1]
    registry = load_pressure_source_registry(repo)
    registry_errors = validate_pressure_source_registry(registry)
    assert 25 <= len(registry) <= 40
    assert not registry_errors
    assert any("health_care_access_pressure" in (row.get("pressure_pillars") or []) for row in registry)

    records = load_manual_source_records(repo, "2026-05-23")
    manual_errors = validate_manual_source_records(records)
    assert len(records) == 5
    assert not manual_errors

    public_rows = [row for row in records if record_is_public(row)]
    assert len(public_rows) == 3
    assert any(row.get("included_as_lead") is True for row in public_rows)
    assert any(row.get("freshness_role") == "stale_current_signal" for row in records)
    assert any(row.get("exclusion_reason") == "resource_only_baseline" for row in records)

    table_rows = source_table_rows(records)
    claim_rows = public_claim_rows(records)
    assert len(table_rows) == 5
    assert len(claim_rows) == 3
    assert claim_rows[0]["supporting_source"] == "Medicaid cuts threaten hundreds of hospitals, new report finds"
    assert summary_for_records(records).startswith("Medicaid cuts could threaten hospital access and stability.")

    stale_row = next(row for row in table_rows if row["record_id"].endswith("stale"))
    resource_row = next(row for row in table_rows if row["record_id"].endswith("map"))
    assert stale_row["used_on_public_page"] == "No"
    assert stale_row["verification_status"] == "stale_current_signal"
    assert resource_row["used_on_public_page"] == "No"
    assert resource_row["verification_status"] == "resource_only_baseline"


def test_care_line_build_renders_public_edition_and_excludes_stale_signals(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = _work_root()
    backup_root = work / "backup"
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    result = build_site(
        work,
        dry_run=False,
        backup_root=backup_root,
        dispatch_seed_dates={"care-line": "2026-05-23"},
    )

    assert result["ok"] is True
    site_root = work / "output" / "site" / "care-line"
    edition_dir = site_root / "editions" / "2026-05-23"
    manifest = json.loads((edition_dir / "edition_manifest.json").read_text(encoding="utf-8"))
    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    archive_html = (site_root / "archive.html").read_text(encoding="utf-8")
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")

    assert manifest["dispatch_slug"] == "care-line"
    assert manifest["source_count"] == 5
    assert manifest["story_count"] == 3
    assert manifest["claim_count"] == 3
    assert manifest["qualified_public_claim_count"] == 3
    assert manifest["lead_signal_count"] == 1
    assert manifest["stale_current_signal_count"] == 0
    assert manifest["resource_only_count"] == 0
    assert manifest["public_rendered"] is True
    assert manifest["source_table_path"].endswith("source_table.html")
    assert manifest["claim_ledger_path"].endswith("claim_ledger.html")

    assert "The Care Line Dispatch" in index_html
    assert "Source-backed signals of where American healthcare access is under strain." in index_html
    assert 'href="editions/2026-05-23/"' in index_html
    assert 'href="source_table.html"' in edition_html
    assert 'href="claim_ledger.html"' in edition_html

    assert "2026-05-23" in archive_html
    assert 'href="editions/2026-05-23/"' in archive_html

    assert "At A Glance" in edition_html
    assert "Hospital / Clinic Operations Signals" in edition_html
    assert "Maternity / Family Care Signals" in edition_html
    assert "Source Note" in edition_html
    assert "stale current signal" not in claim_ledger_html
    assert "resource_only_baseline" not in claim_ledger_html
    assert "Medicaid cuts" in claim_ledger_html
    assert "hospital access" in claim_ledger_html
    assert "duplicate" not in claim_ledger_html.lower()
    assert "resource-only baseline" not in claim_ledger_html.lower()
    assert "stale" not in claim_ledger_html.lower()
    assert "care-line-2026-05-23-kcrg-centerville-clinic-closure-stale" in source_table_html
    assert "care-line-2026-05-23-medicaidgov-enrollment-map" in source_table_html
    assert "No" in source_table_html

    report = build_public_edition_report(work / "output" / "site", "2026-05-23")
    assert report["listable"] is True
    assert report["source_table_exists"] is True
    assert report["claim_ledger_exists"] is True
    assert report["qualified_public_claim_count"] == 3
