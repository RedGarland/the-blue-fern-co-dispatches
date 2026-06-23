from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import research_gaza_critical_source_endpoints


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "gaza"
    / "critical_source_endpoint_research"
    / "source_registry_expansion_plan.json"
)


def _prepare_root_from_plan_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    plan_path = root / "output" / "review" / "gaza" / "source_registry_expansion_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, plan_path)
    return root


def test_research_uses_fixture_and_writes_outputs(tmp_path, monkeypatch):
    root = _prepare_root_from_plan_fixture(tmp_path)
    monkeypatch.setattr(research_gaza_critical_source_endpoints, "ROOT", root)

    report = research_gaza_critical_source_endpoints.write_report(root, "2026-06-22")

    json_path = root / "output" / "review" / "gaza" / "critical_source_endpoint_research.json"
    md_path = root / "output" / "review" / "gaza" / "critical_source_endpoint_research.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert json_path.exists()
    assert md_path.exists()
    assert len(payload["critical_sources"]) == 8
    assert {row["target_name"] for row in payload["critical_sources"]} == {
        "Reuters",
        "Associated Press",
        "AFP",
        "OCHA oPt",
        "UNICEF",
        "WFP",
        "PRCS / Palestinian Red Crescent",
        "UNRWA",
    }


def test_markdown_reports_required_sections(tmp_path, monkeypatch):
    root = _prepare_root_from_plan_fixture(tmp_path)
    monkeypatch.setattr(research_gaza_critical_source_endpoints, "ROOT", root)

    research_gaza_critical_source_endpoints.write_report(root, "2026-06-22")
    markdown = (root / "output" / "review" / "gaza" / "critical_source_endpoint_research.md").read_text(encoding="utf-8")

    assert "## Executive Summary" in markdown
    assert "## Critical Sources By Recommended Handling" in markdown
    assert "## Dead Or Blocked Endpoints" in markdown
    assert "## Sources Needing Endpoint Research" in markdown
    assert "## Sources Recommended For Manual/API Workflow" in markdown
    assert "## Candidate Endpoints Table" in markdown
    assert "## Implementation Recommendations" in markdown
    assert "## No Sources Enabled" in markdown
    assert "Endpoint verification was not performed" in markdown


def test_deterministic_endpoint_statuses(tmp_path, monkeypatch):
    root = _prepare_root_from_plan_fixture(tmp_path)
    monkeypatch.setattr(research_gaza_critical_source_endpoints, "ROOT", root)

    report = research_gaza_critical_source_endpoints.build_report(root, "2026-06-22")
    rows = {row["target_name"]: row for row in report["critical_sources"]}

    assert rows["Reuters"]["endpoint_research_status"] == "blocked_or_forbidden"
    assert rows["Associated Press"]["endpoint_research_status"] == "has_known_dead_endpoint"
    assert rows["AFP"]["endpoint_research_status"] == "needs_live_endpoint_research"
    assert rows["OCHA oPt"]["endpoint_research_status"] == "has_known_dead_endpoint"
    assert rows["UNICEF"]["endpoint_research_status"] == "has_known_dead_endpoint"
    assert rows["WFP"]["endpoint_research_status"] == "has_known_dead_endpoint"
    assert rows["PRCS / Palestinian Red Crescent"]["endpoint_research_status"] == "needs_live_endpoint_research"
    assert rows["UNRWA"]["endpoint_research_status"] == "ready_for_manual_review_only"


def test_do_not_auto_enable_is_true_for_every_source(tmp_path, monkeypatch):
    root = _prepare_root_from_plan_fixture(tmp_path)
    monkeypatch.setattr(research_gaza_critical_source_endpoints, "ROOT", root)

    report = research_gaza_critical_source_endpoints.build_report(root, "2026-06-22")

    assert all(row["do_not_auto_enable"] is True for row in report["critical_sources"])


def test_missing_input_plan_fails_clearly(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    monkeypatch.setattr(research_gaza_critical_source_endpoints, "ROOT", root)

    rc = research_gaza_critical_source_endpoints.main(["--date", "2026-06-22"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "Run python scripts/plan_gaza_source_registry_expansion.py --date YYYY-MM-DD first." in captured.err


def test_script_supports_date_argument():
    args = research_gaza_critical_source_endpoints.parse_args(["--date", "2026-06-22"])

    assert args.date == "2026-06-22"
