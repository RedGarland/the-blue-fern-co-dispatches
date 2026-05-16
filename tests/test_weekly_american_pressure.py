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

