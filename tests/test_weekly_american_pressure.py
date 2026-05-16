import json
from pathlib import Path

import pytest

import scripts.run_weekly_american_pressure as weekly


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
