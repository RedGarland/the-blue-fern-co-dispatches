import json
import shutil
import uuid
from pathlib import Path

from bluefern_dispatches.gaza_sources import REQUIRED_SOURCE_FIELDS
from bluefern_dispatches.generator import build_site


def _build_with_fixture(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    fixture_src = repo / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    fixture_dst = work / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    fixture_dst.parent.mkdir(parents=True, exist_ok=True)
    fixture_dst.write_text(fixture_src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-03")
    result = build_site(work, dry_run=False, backup_root=work / "backup")
    return work, result, fixture_dst


def test_american_pressure_fixture_schema_matches_required_fields():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert records
    for record in records:
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(record.keys()))
        assert not missing


def test_american_pressure_topic_builds_and_links(monkeypatch):
    work, result, _ = _build_with_fixture(monkeypatch)
    assert result["ok"] is True
    root_index = (work / "output" / "site" / "index.html").read_text(encoding="utf-8")
    topic_index = (work / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    edition = (work / "output" / "site" / "american-pressure" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "The American Pressure Dispatch" not in root_index
    assert 'href="/american-pressure/"' not in root_index
    assert 'href="/">Dispatches Home</a>' in topic_index
    assert 'href="/">Dispatches Home</a>' in edition
    assert "The American Pressure Dispatch" in topic_index


def test_american_pressure_edition_uses_fixture_backed_claims_only(monkeypatch):
    work, _, fixture_path = _build_with_fixture(monkeypatch)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    edition = (work / "output" / "site" / "american-pressure" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    sources_manifest = json.loads((work / "output" / "site" / "american-pressure" / "editions" / "2026-05-03" / "sources_manifest.json").read_text(encoding="utf-8"))

    for row in fixture:
        assert row["title"] in edition
        assert row["url"] in edition
        assert row["source_record_id"] in {item["source_id"] for item in sources_manifest}

    assert "No source-backed signal in this edition." in edition
    assert "Medicaid enrollment" not in edition
