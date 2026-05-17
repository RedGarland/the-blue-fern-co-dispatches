import json
from pathlib import Path

import pytest

import scripts.run_weekly_american_pressure as weekly
import scripts.check_american_pressure_weekly_readiness as readiness


def test_push_requires_publish():
    rc = weekly.main(["--week-ending", "2026-05-09", "--push"])
    assert rc == 1


def test_init_candidates_requires_date_range():
    rc = weekly.main(["--init-candidates"])
    assert rc == 1


def test_init_candidates_creates_daily_files(monkeypatch, tmp_path):
    created: list[str] = []
    monkeypatch.setattr(weekly, "ROOT", tmp_path)

    def fake_run(_root, edition_date, **_kwargs):
        path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / edition_date / "candidate_sources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sources": []}), encoding="utf-8")
        created.append(str(path))
        return {"ok": True, "daily_candidate_path": str(path)}

    monkeypatch.setattr(weekly, "run_american_pressure_dispatch", fake_run)
    rc = weekly.main(["--init-candidates", "--start-date", "2026-05-03", "--end-date", "2026-05-05"])
    assert rc == 0
    assert len(created) == 3
    for path in created:
        assert Path(path).exists()


def test_include_approved_candidates_flag_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, "ROOT", tmp_path)
    dispatch_dir = tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-09"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    (dispatch_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "week_start_date": "2026-05-03",
                "display_date_range": "May 3-May 9, 2026",
                "source_count": 1,
                "story_count": 1,
                "story_plus_data_count": 1,
                "baseline_only_count": 0,
                "missing_required_current_development_pillars": [],
                "collection_gap_pillars": [],
                "public_url": "https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-09/",
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_run(_root, _edition_date, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "source_count": 1, "story_count": 1, "warnings": [], "errors": []}

    monkeypatch.setattr(weekly, "run_american_pressure_dispatch", fake_run)
    rc = weekly.main(["--week-ending", "2026-05-09", "--include-approved-candidates"])
    assert rc == 0
    assert calls
    assert calls[-1]["include_approved_candidates"] is True
    assert calls[-1]["force_regenerate"] is False


def test_force_regenerate_flag_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, "ROOT", tmp_path)
    dispatch_dir = tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-09"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    (dispatch_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "week_start_date": "2026-05-03",
                "display_date_range": "May 3-May 9, 2026",
                "source_count": 1,
                "story_count": 1,
                "story_plus_data_count": 1,
                "baseline_only_count": 0,
                "missing_required_current_development_pillars": [],
                "collection_gap_pillars": [],
                "public_url": "https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-09/",
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_run(_root, _edition_date, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "source_count": 1, "story_count": 1, "warnings": [], "errors": []}

    monkeypatch.setattr(weekly, "run_american_pressure_dispatch", fake_run)
    rc = weekly.main(["--week-ending", "2026-05-09", "--force-regenerate"])
    assert rc == 0
    assert calls
    assert calls[-1]["force_regenerate"] is True


def test_weekly_readiness_reports_missing_pillars(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    day = readiness.CANDIDATES_ROOT / "2026-05-09" / "candidate_sources.json"
    day.parent.mkdir(parents=True, exist_ok=True)
    day.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_record_id": "ap-1",
                        "pillar": "food_pressure",
                        "review_status": "approved",
                        "linked_data_anchor_ids": ["anchor-1"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = readiness.build_readiness_report("2026-05-09")
    assert report["approved_candidate_count"] == 1
    assert "food_pressure" not in report["missing_required_current_development_pillars"]
    assert "labor_income_pressure" in report["missing_required_current_development_pillars"]
    assert report["weekly_publish_recommended"] is False


def test_weekly_readiness_recommended_only_with_approved_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    approved_day = readiness.CANDIDATES_ROOT / "2026-05-09" / "candidate_sources.json"
    approved_day.parent.mkdir(parents=True, exist_ok=True)
    approved_rows = []
    for pillar in readiness.REQUIRED_CURRENT_DEVELOPMENT_PILLARS:
        approved_rows.append(
            {
                "source_record_id": f"ap-{pillar}",
                "pillar": pillar,
                "review_status": "approved",
                "linked_data_anchor_ids": ["anchor-1"],
            }
        )
    approved_day.write_text(json.dumps({"sources": approved_rows}), encoding="utf-8")
    report_ok = readiness.build_readiness_report("2026-05-09")
    assert report_ok["weekly_publish_recommended"] is True
    assert report_ok["reasons_if_not_recommended"] == []
    pending_day = readiness.CANDIDATES_ROOT / "2026-05-08" / "candidate_sources.json"
    pending_day.parent.mkdir(parents=True, exist_ok=True)
    pending_day.write_text(json.dumps({"sources": [{"source_record_id": "x", "pillar": "food_pressure", "review_status": "needs_review"}]}), encoding="utf-8")
    report_still_ok = readiness.build_readiness_report("2026-05-09")
    assert report_still_ok["weekly_publish_recommended"] is True


def test_weekly_run_populates_week_dates_even_when_dispatch_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(weekly, "ROOT", tmp_path)

    def fake_run(_root, _edition_date, **_kwargs):
        return {"ok": False, "warnings": [], "errors": ["quality gate fail"], "source_count": 0, "story_count": 0}

    monkeypatch.setattr(weekly, "run_american_pressure_dispatch", fake_run)
    rc = weekly.main(["--date", "2026-05-16", "--source-mode", "both", "--include-approved-candidates", "--publish"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["week_start_date"] == "2026-05-10"
    assert payload["week_end_date"] == "2026-05-16"
    assert payload["display_date_range"] == "May 10–May 16, 2026"


def test_validate_pages_view_for_date_passes_with_latest_and_prior(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "PAGES_REPO", tmp_path / "bluefern-dispatches-pages")
    ap = weekly.PAGES_REPO / "american-pressure"
    ap.mkdir(parents=True, exist_ok=True)
    (ap / "index.html").write_text("latest 2026-05-16 older 2026-05-09", encoding="utf-8")
    (ap / "archive.html").write_text("2026-05-16 2026-05-09", encoding="utf-8")
    (ap / "rss.xml").write_text("<item>2026-05-16</item><item>2026-05-09</item>", encoding="utf-8")
    assert weekly._validate_pages_view_for_date("2026-05-16") == []


def test_publish_quality_gate_blocks_without_allow_thin(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, "ROOT", tmp_path)
    dispatch_dir = tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-16"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    (dispatch_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "week_start_date": "2026-05-10",
                "display_date_range": "May 10–May 16, 2026",
                "source_count": 2,
                "story_count": 1,
                "story_plus_data_count": 1,
                "baseline_only_count": 0,
                "missing_required_current_development_pillars": ["food_pressure"],
                "collection_gap_pillars": ["food_pressure"],
                "public_url": "https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-16/",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(_root, _edition_date, **_kwargs):
        return {"ok": True, "source_count": 2, "story_count": 1, "warnings": [], "errors": []}

    monkeypatch.setattr(weekly, "run_american_pressure_dispatch", fake_run)
    monkeypatch.setattr(weekly, "build_readiness_report", lambda _d: {"weekly_publish_recommended": False, "reasons_if_not_recommended": ["thin"]})
    rc = weekly.main(["--date", "2026-05-16", "--source-mode", "both", "--publish"])
    assert rc == 1
