from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import audit_gaza_source_coverage


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gaza" / "source_coverage_audit" / "collection_report.json"


def _prepare_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    report_path = root / "data" / "dispatches" / "gaza" / "editions" / "2026-06-22" / "collection_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, report_path)
    site_edition = root / "output" / "site" / "gaza" / "editions" / "2026-06-22"
    site_edition.mkdir(parents=True, exist_ok=True)
    (site_edition / "sources_manifest.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "who-news",
                    "title": "WHO reports health-system strain in Gaza",
                    "url": "https://example.com/who",
                    "publisher": "WHO",
                    "reliability_tier": "official-humanitarian-source",
                },
                {
                    "source_record_id": "reuters-middle-east-rss",
                    "title": "Gazans flee scorching tents for a polluted sea",
                    "url": "https://example.com/reuters",
                    "publisher": "Reuters",
                    "reliability_tier": "reported-public-source",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (site_edition / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "gaza-story-2026-06-22-001",
                    "title": "WHO reports health-system strain in Gaza",
                    "public_rendered": True,
                    "included_in_public_summary": True,
                    "source_ids": ["who-news"],
                },
                {
                    "story_id": "gaza-story-2026-06-22-002",
                    "title": "Gazans flee scorching tents for a polluted sea",
                    "public_rendered": False,
                    "included_in_public_summary": True,
                    "source_ids": ["reuters-middle-east-rss"],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def test_audit_includes_all_providers_and_writes_outputs(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(audit_gaza_source_coverage, "ROOT", root)

    report = audit_gaza_source_coverage.write_audit_report(root, "2026-06-22")

    json_path = root / "output" / "review" / "gaza" / "source_coverage_audit.json"
    md_path = root / "output" / "review" / "gaza" / "source_coverage_audit.md"

    assert report["ok"] is True
    assert json_path.exists()
    assert md_path.exists()
    assert len(report["providers"]) == 6
    assert len(report["target_source_coverage"]) >= 10
    assert report["rendered_public_story_source_ids"] == ["who-news"]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload["providers"]) == 6
    assert any(row["source_id"] == "manual_sources_json" for row in payload["providers"])


def test_markdown_reports_required_sections(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(audit_gaza_source_coverage, "ROOT", root)

    report = audit_gaza_source_coverage.write_audit_report(root, "2026-06-22")
    markdown = (root / "output" / "review" / "gaza" / "source_coverage_audit.md").read_text(encoding="utf-8")

    assert "## Enabled Sources Checked This Run" in markdown
    assert "## Manual-Only Sources Requiring Review/Backfill" in markdown
    assert "## Disabled or Blocked Sources With Reasons" in markdown
    assert "## Missing Target Reliable Sources" in markdown
    assert "## Sources Contributing Rendered Public Stories" in markdown
    assert "WHO" in markdown
    assert "missing_from_registry" in markdown
    assert report["missing_target_sources"]


def test_recommended_actions_are_deterministic(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(audit_gaza_source_coverage, "ROOT", root)

    report = audit_gaza_source_coverage.build_audit(root, "2026-06-22")
    actions = {row["source_id"]: row["recommended_action"] for row in report["providers"]}
    targets = {row["target_name"]: row["recommended_action"] for row in report["target_source_coverage"]}

    assert actions["who-news"] == "continue checking"
    assert actions["ap-middle-east-rss"] == "continue checking; no matching items this run"
    assert actions["reuters-middle-east-rss"] == "keep diagnostics-only or replace with manual/API workflow"
    assert actions["wfp-newsroom"] == "find replacement endpoint or keep disabled with documented reason"
    assert actions["unrwa-updates"] == "manual review/backfill required when relevant"
    assert targets["OCHA oPt"] == "add to registry or document exclusion"


def test_missing_targets_do_not_fail_audit(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(audit_gaza_source_coverage, "ROOT", root)

    report = audit_gaza_source_coverage.build_audit(root, "2026-06-22")

    assert report["ok"] is True
    assert report["missing_target_sources"]
    assert any(row["status"] == "missing_from_registry" for row in report["target_source_coverage"])


def test_date_specific_collection_report_path(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(audit_gaza_source_coverage, "ROOT", root)

    rc = audit_gaza_source_coverage.main(["--date", "2026-06-22"])

    assert rc == 0
    audit_json = root / "output" / "review" / "gaza" / "source_coverage_audit.json"
    payload = json.loads(audit_json.read_text(encoding="utf-8"))
    assert payload["collection_report_path"].endswith(r"data\dispatches\gaza\editions\2026-06-22\collection_report.json")
