from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_workflow_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "run_cascadia_detention_watch_workflow.py"
    spec = importlib.util.spec_from_file_location("cascadia_detention_watch_workflow", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refresh_mode_writes_review_output_and_dashboard_path(tmp_path: Path, monkeypatch):
    mod = _load_workflow_module()
    review_dir = tmp_path / "output" / "review" / "cascadia" / "detention_watch"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / "source_refresh_2026-05-26.json"
    dashboard_path = review_dir / "review_dashboard_2026-05-26.html"

    def fake_run_refresh(root: Path, as_of: str | None = None):
        review_path.write_text(json.dumps({"as_of_date": as_of, "sources": [], "candidate_claims": []}), encoding="utf-8")
        return {"ok": True, "output_path": str(review_path), "source_count": 0, "candidate_count": 0}

    def fake_render_dashboard(path: Path):
        dashboard_path.write_text("<html>Local editorial review only — not for publication</html>", encoding="utf-8")
        return dashboard_path

    monkeypatch.setattr(mod, "run_refresh", fake_run_refresh)
    monkeypatch.setattr(mod, "render_review_dashboard", fake_render_dashboard)
    result = mod.run_refresh_mode(tmp_path, "2026-05-26")
    assert result["ok"] is True
    assert Path(result["output_path"]).exists()
    assert Path(result["dashboard_path"]).exists()
    assert "output/site" not in str(result["dashboard_path"]).replace("\\", "/")


def test_promote_mode_refuses_invalid_candidate_input(tmp_path: Path):
    mod = _load_workflow_module()
    review_path = tmp_path / "source_refresh_2026-05-26.json"
    review_path.write_text(
        json.dumps(
            {
                "sources": [{"source_id": "reg-one"}],
                "candidate_claims": [
                    {
                        "source_id": "reg-one",
                        "source_url": "https://example.org",
                        "source_title": "Example",
                        "retrieved_at": "2026-05-26T10:00:00+00:00",
                        "source_family": "local_media",
                        "proposed_claim_class": "reported",
                        "proposed_claim_text": "",
                        "review_status": "approved",
                        "confidence": "low",
                        "notes": "n/a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        mod.run_promote_mode(tmp_path, "2026-05-26", review_path)
        assert False, "expected promote validation failure"
    except ValueError as exc:
        assert "proposed_claim_text is empty" in str(exc)


def test_render_mode_refuses_non_approved_update(tmp_path: Path):
    mod = _load_workflow_module()
    update_path = tmp_path / "update_2026-05-26.json"
    update_path.write_text(json.dumps({"review_status": "candidate"}), encoding="utf-8")
    result = mod.run_render_mode(tmp_path, "2026-05-26", update_path)
    assert result["ok"] is False
    assert "must be approved" in result["errors"][0]


def test_status_mode_identifies_latest_baseline_review_and_update(tmp_path: Path):
    mod = _load_workflow_module()
    data_root = tmp_path / "data" / "dispatches" / "cascadia" / "detention_watch"
    review_root = tmp_path / "output" / "review" / "cascadia" / "detention_watch"
    data_root.mkdir(parents=True, exist_ok=True)
    review_root.mkdir(parents=True, exist_ok=True)
    (data_root / "baseline_2026-05-26.json").write_text("{}", encoding="utf-8")
    (data_root / "update_2026-06-01.json").write_text(json.dumps({"review_status": "approved"}), encoding="utf-8")
    (review_root / "source_refresh_2026-06-01.json").write_text(json.dumps({"as_of_date": "2026-06-01"}), encoding="utf-8")
    (review_root / "review_dashboard_2026-06-01.html").write_text("<html></html>", encoding="utf-8")
    status = mod.run_status_mode(tmp_path)
    assert status["ok"] is True
    assert status["latest_baseline_file"].endswith("baseline_2026-05-26.json")
    assert status["latest_update_file"].endswith("update_2026-06-01.json")
    assert status["latest_review_json"].endswith("source_refresh_2026-06-01.json")
    assert status["latest_review_dashboard"].endswith("review_dashboard_2026-06-01.html")
    assert status["latest_update_is_approved"] is True
