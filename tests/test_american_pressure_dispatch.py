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


def _required_source_record() -> dict:
    return {
        "source_record_id": "ap-test-001",
        "title": "AP Test Source",
        "url": "https://example.com/ap-test-source",
        "publisher": "Example Publisher",
        "published_at": "2026-05-03T00:00:00Z",
        "retrieved_at": "2026-05-03T12:00:00Z",
        "summary_or_snippet": "Source-backed test snippet for generator shape handling.",
        "source_type": "report",
        "region_scope": "United States",
        "category_hint": "food",
        "reliability_tier": "official-public-source",
    }


def test_american_pressure_fixture_schema_matches_required_fields():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert records
    for record in records:
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(record.keys()))
        assert not missing


def test_american_pressure_fixture_schema_matches_required_fields_when_wrapped():
    record = _required_source_record()
    missing = sorted(REQUIRED_SOURCE_FIELDS - set(record.keys()))
    assert not missing


def test_american_pressure_build_accepts_sources_wrapped_shape(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    fixture_dst = work / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    fixture_dst.parent.mkdir(parents=True, exist_ok=True)
    fixture_dst.write_text(json.dumps({"sources": [_required_source_record()]}, indent=2), encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-03")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    assert result["ok"] is True
    assert not result["errors"]
    edition = (work / "output" / "site" / "american-pressure" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "AP Test Source" in edition


def test_american_pressure_build_accepts_legacy_list_shape(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    fixture_dst = work / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    fixture_dst.parent.mkdir(parents=True, exist_ok=True)
    fixture_dst.write_text(json.dumps([_required_source_record()], indent=2), encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-03")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    assert result["ok"] is True
    assert not result["errors"]


def test_american_pressure_invalid_shape_reports_clear_error(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    fixture_dst = work / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-03" / "manual_sources.json"
    fixture_dst.parent.mkdir(parents=True, exist_ok=True)
    fixture_dst.write_text(json.dumps({"bad_root": []}, indent=2), encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-03")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    assert result["ok"] is False
    assert any("american-pressure manual sources file has invalid shape" in err for err in result["errors"])
    assert any(str(fixture_dst) in err for err in result["errors"])


def test_american_pressure_topic_builds_and_links(monkeypatch):
    work, result, _ = _build_with_fixture(monkeypatch)
    assert result["ok"] is True
    root_index = (work / "output" / "site" / "index.html").read_text(encoding="utf-8")
    topic_index = (work / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    edition = (work / "output" / "site" / "american-pressure" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "The American Pressure Dispatch" in root_index
    assert 'href="/american-pressure/"' in root_index
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
