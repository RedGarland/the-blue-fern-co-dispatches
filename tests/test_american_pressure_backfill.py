import json
import shutil
import uuid
from pathlib import Path

import scripts.backfill_american_pressure as backfill


def _valid_record(date_str: str, suffix: str, pillar: str = "food_pressure") -> dict:
    return {
        "source_record_id": f"ap-{date_str}-{suffix}",
        "source_id": f"source-{suffix}",
        "title": f"Title {suffix}",
        "url": f"https://example.org/{date_str}/{suffix}",
        "publisher": "Publisher",
        "published_at": f"{date_str}T00:00:00Z",
        "retrieved_at": f"{date_str}T12:00:00Z",
        "summary_or_snippet": f"Summary {suffix}",
        "source_type": "official_report_page",
        "region_scope": "United States",
        "category_hint": pillar,
        "pillar": pillar,
        "reliability_tier": "official_primary",
    }


def _write_manual(root: Path, date_str: str, rows: list[dict]) -> None:
    path = root / "data" / "dispatches" / "american-pressure" / "sources" / date_str / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _make_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "american-pressure-backfill"
    shutil.copytree(repo / "assets", root / "assets")
    shutil.copytree(repo / "data" / "dispatches" / "american-pressure", root / "data" / "dispatches" / "american-pressure")
    return root


def test_backfill_runs_chronological_range():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-11", [_valid_record("2026-05-11", "a")])
        _write_manual(root, "2026-05-12", [_valid_record("2026-05-12", "b", "labor_income_pressure")])
        report = backfill.run_backfill(root, ["2026-05-12", "2026-05-11"], publish=False, dry_run=False, from_manual_sources=False, source_mode="manual", allow_partial=False)
        assert report["requested_dates"] == ["2026-05-11", "2026-05-12"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_per_date_diagnostics_present():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-12", [_valid_record("2026-05-12", "a")])
        report = backfill.run_backfill(root, ["2026-05-12"], publish=False, dry_run=False, from_manual_sources=False, source_mode="both", allow_partial=False)
        row = report["per_date"]["2026-05-12"]
        assert "pillars_present" in row
        assert "pillars_missing" in row
        assert "source_count_by_pillar" in row
        assert "story_count_by_pillar" in row
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_report_outside_output_site():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-12", [_valid_record("2026-05-12", "a")])
        report = backfill.run_backfill(root, ["2026-05-12"], publish=False, dry_run=False, from_manual_sources=False, source_mode="manual", allow_partial=False)
        assert "output\\site\\" not in str(report["report_path"]).lower()
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
