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
    assert stale_row["verification_status"] == "stale signal"
    assert resource_row["used_on_public_page"] == "No"
    assert resource_row["verification_status"] == "resource-only baseline"


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
    assert manifest["stale_current_signal_count"] == 1
    assert manifest["resource_only_count"] == 1
    assert manifest["public_rendered"] is True
    assert manifest["source_table_path"].endswith("source_table.html")
    assert manifest["claim_ledger_path"].endswith("claim_ledger.html")
    assert manifest["public_archive_title"] == "Medicaid cuts and hospital-access pressure"

    assert "The Care Line Dispatch" in index_html
    assert "Source-backed signals of where American healthcare access is under strain." in index_html
    assert "Browse the Care Line archive" in index_html
    assert "No map is published for Care Line yet. Future maps will show where current source-backed healthcare-access pressure signals were found. Areas without markers should not be read as places without healthcare strain." in index_html
    assert 'href="source_table.html"' in edition_html
    assert 'href="claim_ledger.html"' in edition_html

    assert "The Care Line Dispatch - 2026-05-23" not in archive_html

    assert "Today's Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Core Healthcare Access Signals" in edition_html
    assert "Hospital / Clinic Operations Signals" in edition_html
    assert "Maternity / Family Care Signals" in edition_html
    assert "Insurance / Affordability Signals" not in edition_html
    assert "Rural Access Signals" not in edition_html
    assert "Emergency / EMS Signals" not in edition_html
    assert "Public Health Capacity Signals" not in edition_html
    assert "Other Care Line Signals" not in edition_html
    assert "Pilot Edition" not in edition_html
    assert "No current public signals were qualified for this edition." not in edition_html
    assert "Other monitored categories had no qualifying public signal in this edition: insurance affordability, rural access, emergency and EMS, public health capacity, other Care Line signals." in edition_html
    assert "CLINIC_ACCESS_STRAIN" not in edition_html
    assert "MATERNITY_CARE_LOSS" not in edition_html
    assert "Clinic access strain" in edition_html
    assert "Maternity care loss" in edition_html
    assert "Herald-Standard | Hospital closure | Pennsylvania | 2026-05-07" in edition_html
    assert "What changed:</strong> A new report warned that Medicaid cuts could threaten hundreds of hospitals." in edition_html
    assert "Who may be affected:</strong> Clinic patients in and around Centerville." in edition_html
    assert "Who may be affected:</strong> Pregnant patients, families, and patients needing local maternity care near Los Alamos." in edition_html
    assert "Why it matters:</strong> A local clinic closure can mean longer travel, fewer appointment options, or delayed routine care." in edition_html
    assert "Why it matters:</strong> Loss of local labor and delivery services can force patients to travel farther for time-sensitive care." in edition_html
    assert "Limit:</strong> The article does not quantify total patient displacement." in edition_html
    assert "Source Note" in edition_html
    assert "Care Line does not publish a map in this release." in edition_html
    assert "The source table and claim ledger preserve the traceable record for readers and researchers." in edition_html
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
    assert "This table preserves the public signals, excluded context, and stale records that informed the edition." in source_table_html
    assert "This ledger keeps the public claims, supporting interpretations, and traceability limits in one place." in claim_ledger_html

    report = build_public_edition_report(work / "output" / "site", "2026-05-23")
    assert report["listable"] is True
    assert report["source_table_exists"] is True
    assert report["claim_ledger_exists"] is True
    assert report["qualified_public_claim_count"] == 3


def test_care_line_render_no_current_update_path_preserves_fallback_copy():
    html = render_care_line_edition_body([], "2026-05-23")

    assert "No current Care Line update was published because no source records were reviewed for this edition date." in html
    assert "No public claims qualified for this edition." in html
    assert "No current public signals were qualified for this edition." not in html
    assert "source_table.html" in html
    assert "claim_ledger.html" in html


def test_care_line_build_renders_no_current_update_edition_and_lists_it(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = _work_root()
    backup_root = work / "backup"
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-06-19")

    result = build_site(
        work,
        dry_run=False,
        backup_root=backup_root,
        dispatch_seed_dates={"care-line": "2026-06-19"},
    )

    assert result["ok"] is True
    site_root = work / "output" / "site" / "care-line"
    edition_dir = site_root / "editions" / "2026-06-19"
    manifest = json.loads((edition_dir / "edition_manifest.json").read_text(encoding="utf-8"))
    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    archive_html = (site_root / "archive.html").read_text(encoding="utf-8")
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")

    assert manifest["edition_mode"] == "no_current_update"
    assert manifest["source_count"] == 0
    assert manifest["story_count"] == 0
    assert manifest["claim_count"] == 0
    assert manifest["qualified_public_claim_count"] == 0
    assert manifest["public_rendered"] is True
    assert manifest["public_archive_title"] == "2026-06-19 — No current update"
    assert manifest["public_archive_subtitle"] == "No current Care Line update was published because no source records were reviewed for this edition date."

    assert "The Care Line Dispatch" in index_html
    assert "Browse the Care Line archive" in index_html
    assert "2026-06-19 — No current update" in archive_html
    assert "No current Care Line update was published because no source records were reviewed for this edition date." in archive_html

    assert "No current Care Line update was published because no source records were reviewed for this edition date." in edition_html
    assert "No public claims qualified for this edition." in edition_html
    assert "No current public signals were qualified for this edition." not in edition_html
    assert "No current Care Line update was published for this edition." in source_table_html
    assert "No current Care Line update was published for this edition." in claim_ledger_html

    report = build_public_edition_report(work / "output" / "site", "2026-06-19")
    assert report["listable"] is True
    assert report["edition_mode"] == "no_current_update"
    assert report["qualified_public_claim_count"] == 0


def test_care_line_approved_release_renders_limited_source_public_edition(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = _work_root()
    backup_root = work / "backup"
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-08-09")

    root_index = work / "output" / "site" / "index.html"
    root_index.parent.mkdir(parents=True, exist_ok=True)
    original_root = "<!doctype html>\n<html><body><main>Latest published developments</main></body></html>\n"
    root_index.write_text(original_root, encoding="utf-8")

    proposal_dir = work / "data" / "dispatches" / "care-line" / "review" / "proposed-editions"
    snapshot_dir = work / "data" / "dispatches" / "care-line" / "review" / "signal-reviews"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    proposal = {
        "schema_version": "bluefern.care_line.proposed_edition.v1",
        "edition_date": "2026-08-09",
        "edition_mode": "current_update",
        "headline": "Care Line limited-source update",
        "edition_summary": "This limited-source update reflects the approved Care Line access developments available in the replay evidence.",
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
        "source_adequacy_label": "Limited-source update",
        "source_count": 2,
        "public_developments": 2,
        "publisher_count": 2,
        "approved_signal_ids": [
            "care_line_candidate_20260809_damariscotta",
            "care_line_candidate_20260809_massachusetts_behavioral",
        ],
    }
    snapshot = {
        "schema_version": "bluefern.care_line.review_snapshot.v2",
        "edition_date": "2026-08-09",
        "reviewed_at": "2026-08-10T00:15:00Z",
        "review_payload": {
            "edition_date": "2026-08-09",
            "items": [
                {
                    "candidate_id": "care_line_candidate_20260809_damariscotta",
                    "source_name": "Nonprofit Policy Reporting (Maine)",
                    "source_title": "Despite community pushback, MaineHealth announces closure of Damariscotta birthing center",
                    "source_url": "https://mainemorningstar.com/2026/08/06/despite-community-pushback-mainehealth-announces-closure-of-damariscotta-birthing-center/",
                    "source_date": "2026-08-06",
                    "approved_public_claim": "MaineHealth is closing inpatient labor and delivery services at Lincoln Hospital in Damariscotta.",
                    "bounded_public_summary": "MaineHealth announced it will close the labor and delivery service at Lincoln Hospital in Damariscotta, adding pressure to rural maternity access in mid-coast Maine.",
                    "approved_service_line": "labor_and_delivery",
                    "approved_event_type": "service_line_closure",
                    "approved_access_consequence": "reduced_rural_maternity_access",
                    "approved_geography": "Damariscotta, Maine",
                    "exact_supporting_passage": "The closure of a birthing center at a Damariscotta hospital is moving forward, the MaineHealth Board of Trustees announced Thursday, despite months of community opposition and concern about worsening health access for rural Mainers.",
                    "review_decision": "APPROVE_WITH_CORRECTION",
                    "reviewer_identity": "codex_editorial_review",
                    "reviewer_rationale": "Bounded current access-pressure claim.",
                    "role_in_edition": "core_access_signal",
                    "notes": "Use bounded closure language only.",
                    "evidence_level": "article_excerpt",
                },
                {
                    "candidate_id": "care_line_candidate_20260809_massachusetts_behavioral",
                    "source_name": "Becker's Behavioral Health",
                    "source_title": "Massachusetts behavioral health provider to close after 40+ years",
                    "source_url": "https://www.beckersbehavioralhealth.com/behavioral-health-news/massachusetts-behavioral-health-provider-to-close-after-40-years/",
                    "source_date": "2026-08-07",
                    "approved_public_claim": "Community Healthlink will cease operations after more than 40 years of service.",
                    "bounded_public_summary": "A Massachusetts behavioral health provider says it will cease operations after more than 40 years, raising access concerns for affected patients and programs.",
                    "approved_service_line": "behavioral_health",
                    "approved_event_type": "service_line_closure",
                    "approved_access_consequence": "reduced_behavioral_health_access",
                    "approved_geography": "Worcester, Massachusetts",
                    "exact_supporting_passage": "Worcester, Mass.-based Community Healthlink (CHL) will cease operations after more than 40 years of service once its programs are transitioned to several community organizations.",
                    "review_decision": "APPROVE_WITH_CORRECTION",
                    "reviewer_identity": "codex_editorial_review",
                    "reviewer_rationale": "Bounded closure claim.",
                    "role_in_edition": "core_access_signal",
                    "notes": "Bounded claim remains limited-source.",
                    "evidence_level": "article_excerpt",
                },
            ],
        },
    }
    (proposal_dir / "2026-08-09.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    (snapshot_dir / "2026-08-09.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    result = build_site(
        work,
        dry_run=False,
        backup_root=backup_root,
        only_dispatches=("care-line",),
        dispatch_seed_dates={"care-line": "2026-08-09"},
    )

    assert result["ok"] is True
    assert root_index.read_text(encoding="utf-8") == original_root

    site_root = work / "output" / "site" / "care-line"
    edition_dir = site_root / "editions" / "2026-08-09"
    manifest = json.loads((edition_dir / "edition_manifest.json").read_text(encoding="utf-8"))
    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    archive_html = (site_root / "archive.html").read_text(encoding="utf-8")

    assert manifest["source_adequacy_status"] == "LIMITED_SOURCE_UPDATE"
    assert manifest["source_adequacy_label"] == "Limited-source update"
    assert manifest["public_archive_title"] == "Limited-source update"
    assert manifest["public_archive_subtitle"].startswith("This limited-source update reflects the approved Care Line access developments")
    assert manifest["source_count"] == 2
    assert manifest["story_count"] == 2
    assert manifest["claim_count"] == 2
    assert manifest["qualified_public_claim_count"] == 2
    assert "Limited-source update" in index_html
    assert "August 9, 2026" in index_html or "2026-08-09" in index_html
    assert "Full-source update" not in index_html
    assert "Limited-source update / August 9, 2026" in archive_html or "Limited-source update" in archive_html
    assert "Full-source update" not in archive_html
    assert "MaineHealth is closing inpatient labor and delivery services at Lincoln Hospital in Damariscotta." in edition_html
    assert "Community Healthlink will cease operations after more than 40 years of service." in edition_html
    assert "AnMed" not in edition_html
    assert "Sentara" not in edition_html


def test_care_line_august5_release_candidate_uses_approved_miles_wording(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = _work_root()
    backup_root = work / "backup"
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-08-05")

    result = build_site(
        work,
        dry_run=False,
        backup_root=backup_root,
        dispatch_seed_dates={"care-line": "2026-08-05"},
    )

    assert result["ok"] is True
    site_root = work / "output" / "site" / "care-line"
    edition_dir = site_root / "editions" / "2026-08-05"
    manifest = json.loads((edition_dir / "edition_manifest.json").read_text(encoding="utf-8"))
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    archive_html = (site_root / "archive.html").read_text(encoding="utf-8")
    index_html = (site_root / "index.html").read_text(encoding="utf-8")

    assert manifest["dispatch_slug"] == "care-line"
    assert manifest["edition_mode"] == "current_update"
    assert manifest["source_count"] == 1
    assert manifest["story_count"] == 1
    assert manifest["claim_count"] == 1
    assert manifest["qualified_public_claim_count"] == 1
    assert manifest["public_archive_title"] == "Miles Hospital proposes closing its labor and delivery center"
    assert manifest["public_summary"] == "NPR reports that a coalition in mid-coast Maine is fighting a proposed closure of Miles Hospital's labor and delivery center. The source does not establish that labor and delivery services have already ended."
    assert manifest["generation_mode"] == "approved_current_review_proposal"
    assert manifest["publication_status"] == "unpublished"
    assert manifest["pages_status"] == "not_synced"
    assert manifest["public_release_status"] == "not_published"
    assert manifest["pages_release_status"] == "not_synced"
    assert manifest["approved_proposal_path"] == "data/dispatches/care-line/review/proposed-editions/2026-08-05.json"
    assert manifest["review_snapshot_path"] == "data/dispatches/care-line/review/signal-reviews/2026-08-05.json"

    assert "Miles Hospital proposes closing its labor and delivery center" in edition_html
    assert "proposed closure of Miles Hospital&#x27;s labor and delivery center" in edition_html
    assert "Who may be affected:</strong> Pregnant patients and families needing labor and delivery care in mid-coast Maine." in edition_html
    assert "does not establish that labor and delivery services have already ended" in edition_html
    assert "Texas Tribune" not in edition_html
    assert "Virginia Mercury" not in edition_html
    assert "2026-08-05" in archive_html
    assert "Miles Hospital proposes closing its labor and delivery center" in archive_html
    assert "Miles Hospital proposes closing its labor and delivery center" in index_html
