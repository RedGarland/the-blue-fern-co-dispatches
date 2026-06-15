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
from bluefern_dispatches.care_line_render import render_care_line_edition_body
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
    assert manifest["public_archive_title"] == "Medicaid cuts and hospital-access pressure"

    assert "The Care Line Dispatch" in index_html
    assert "Source-backed signals of where American healthcare access is under strain." in index_html
    assert "Browse the Care Line archive" in index_html
    assert "Source-backed signals of where American healthcare access is under strain. archive" not in index_html
    assert "No map is published in this pilot phase. Future Care Line maps will show where current source-backed healthcare-access pressure signals were found. Areas without markers should not be read as places without healthcare strain." in index_html
    assert 'href="editions/2026-05-23/"' in index_html
    assert 'href="source_table.html"' in edition_html
    assert 'href="claim_ledger.html"' in edition_html

    assert "2026-05-23" in archive_html
    assert 'href="editions/2026-05-23/"' in archive_html
    assert "Medicaid cuts and hospital-access pressure" in archive_html
    assert "The Care Line Dispatch - 2026-05-23" not in archive_html

    assert "At A Glance" in edition_html
    assert "Core Healthcare Access Signals" in edition_html
    assert "Hospital / Clinic Operations Signals" in edition_html
    assert "Maternity / Family Care Signals" in edition_html
    assert "Insurance / Affordability Signals" not in edition_html
    assert "Rural Access Signals" not in edition_html
    assert "Emergency / EMS Signals" not in edition_html
    assert "Public Health Capacity Signals" not in edition_html
    assert "Other Care Line Signals" not in edition_html
    assert "No qualifying public signals were placed in this bucket for this edition." not in edition_html
    assert "Other monitored categories had no qualifying public signal in this edition: insurance affordability, rural access, emergency and EMS, public health capacity, other Care Line signals." in edition_html
    assert "CLINIC_ACCESS_STRAIN" not in edition_html
    assert "MATERNITY_CARE_LOSS" not in edition_html
    assert "Clinic access strain" in edition_html
    assert "Maternity care loss" in edition_html
    assert "What changed:</strong> A new report warned that Medicaid cuts could threaten hundreds of hospitals." in edition_html
    assert "Who may be affected:</strong> Clinic patients in and around Centerville." in edition_html
    assert "Who may be affected:</strong> Pregnant patients, families, and patients needing local maternity care near Los Alamos." in edition_html
    assert "Why it matters:</strong> A local clinic closure can mean longer travel, fewer appointment options, or delayed routine care." in edition_html
    assert "Why it matters:</strong> Loss of local labor and delivery services can force patients to travel farther for time-sensitive care." in edition_html
    assert "Limit:</strong> The article does not quantify total patient displacement." in edition_html
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


def test_care_line_render_no_current_update_path_preserves_fallback_copy():
    html = render_care_line_edition_body([], "2026-05-23")

    assert "No current public signals were qualified for this pilot edition." in html
    assert "Other monitored categories had no qualifying public signal in this edition:" not in html
    assert "No qualifying public signals were placed in this bucket for this edition." not in html
    assert "source_table.html" in html
    assert "claim_ledger.html" in html
