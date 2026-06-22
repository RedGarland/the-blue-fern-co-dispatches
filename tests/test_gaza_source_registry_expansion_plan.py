from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import plan_gaza_source_registry_expansion


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gaza" / "source_registry_expansion_plan" / "source_coverage_audit.json"
COLLECTION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gaza" / "source_coverage_audit" / "collection_report.json"


def _prepare_root_from_audit_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    audit_path = root / "output" / "review" / "gaza" / "source_coverage_audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, audit_path)
    return root


def _prepare_root_from_collection_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    report_path = root / "data" / "dispatches" / "gaza" / "editions" / "2026-06-22" / "collection_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(COLLECTION_FIXTURE, report_path)
    return root


def test_plan_uses_audit_fixture_and_writes_outputs(tmp_path, monkeypatch):
    root = _prepare_root_from_audit_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    report = plan_gaza_source_registry_expansion.write_plan_report(root, "2026-06-22")

    json_path = root / "output" / "review" / "gaza" / "source_registry_expansion_plan.json"
    md_path = root / "output" / "review" / "gaza" / "source_registry_expansion_plan.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert json_path.exists()
    assert md_path.exists()
    assert len(report["plan_rows"]) == 7
    assert len(payload["plan_rows"]) == 7
    assert {row["source_id"] for row in payload["plan_rows"]} == {
        "who-news",
        "wfp-newsroom",
        "reuters-middle-east-rss",
        "unrwa-updates",
        "gaza-health-ministry",
        "times-of-israel-rss",
        "un-human-rights-office",
    }


def test_markdown_reports_required_sections(tmp_path, monkeypatch):
    root = _prepare_root_from_audit_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    plan_gaza_source_registry_expansion.write_plan_report(root, "2026-06-22")
    markdown = (root / "output" / "review" / "gaza" / "source_registry_expansion_plan.md").read_text(encoding="utf-8")

    assert "## Executive Summary" in markdown
    assert "## Critical Sources To Fix First" in markdown
    assert "## Sources That Need Endpoint Research" in markdown
    assert "## Sources That Should Remain Manual-Only" in markdown
    assert "## Official-Claim Sources Requiring Attribution Safeguards" in markdown
    assert "## Already Active Sources" in markdown
    assert "## Sources Recommended For Exclusion" in markdown
    assert "## Concrete Next Implementation Steps" in markdown
    assert "does not enable any sources automatically" in markdown


def test_deterministic_action_classification(tmp_path, monkeypatch):
    root = _prepare_root_from_audit_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    report = plan_gaza_source_registry_expansion.build_plan(root, "2026-06-22")
    rows = {row["source_id"]: row for row in report["plan_rows"]}

    assert rows["who-news"]["recommended_action"] == "already_active"
    assert rows["wfp-newsroom"]["recommended_action"] == "needs_endpoint_research"
    assert rows["reuters-middle-east-rss"]["recommended_action"] == "replace_blocked_endpoint"
    assert rows["unrwa-updates"]["recommended_action"] == "manual_only"
    assert rows["gaza-health-ministry"]["recommended_action"] == "official_claim_source"
    assert rows["times-of-israel-rss"]["recommended_action"] == "manual_only"
    assert rows["un-human-rights-office"]["recommended_action"] == "manual_only"


def test_missing_sources_do_not_make_ok_false(tmp_path, monkeypatch):
    root = _prepare_root_from_audit_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    report = plan_gaza_source_registry_expansion.build_plan(root, "2026-06-22")

    assert report["ok"] is True
    assert any(row["recommended_action"] == "official_claim_source" for row in report["plan_rows"])
    assert any(row["recommended_action"] == "manual_only" for row in report["plan_rows"])


def test_plan_falls_back_to_collection_report_and_supports_date(tmp_path, monkeypatch):
    root = _prepare_root_from_collection_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    rc = plan_gaza_source_registry_expansion.main(["--date", "2026-06-22"])

    assert rc == 0
    assert (root / "output" / "review" / "gaza" / "source_coverage_audit.json").exists()
    assert (root / "output" / "review" / "gaza" / "source_registry_expansion_plan.json").exists()


def test_disabled_and_blocked_sources_are_not_auto_enabled(tmp_path, monkeypatch):
    root = _prepare_root_from_audit_fixture(tmp_path)
    monkeypatch.setattr(plan_gaza_source_registry_expansion, "ROOT", root)

    report = plan_gaza_source_registry_expansion.build_plan(root, "2026-06-22")
    rows = {row["source_id"]: row for row in report["plan_rows"]}

    assert rows["wfp-newsroom"]["do_not_auto_enable"] is True
    assert rows["reuters-middle-east-rss"]["do_not_auto_enable"] is True
    assert rows["wfp-newsroom"]["recommended_action"] != "already_active"
    assert rows["reuters-middle-east-rss"]["recommended_action"] != "already_active"
