import json
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.run_gaza_dispatch import run_gaza_dispatch


def make_work_root(repo: Path) -> Path:
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    (work / "data" / "records").mkdir(parents=True)
    for name in ("dispatches", "editions", "sources", "records", "curation_decisions", "detail_packages"):
        (work / "data" / "records" / f"{name}.json").write_text("[]", encoding="utf-8")
    return work


def write_manual_sources(work: Path, edition_date: str, records: list[dict] | None = None) -> Path:
    path = work / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = records or [
        {
            "source_record_id": f"gaza-src-{edition_date}-001",
            "title": "UN says durable shelter materials remain blocked from Gaza",
            "url": "https://www.aa.com.tr/en/middle-east/un-says-israel-blocks-durable-shelter-materials-from-entering-gaza/3923572",
            "publisher": "Anadolu Agency",
            "published_at": f"{edition_date}T12:00:00Z",
            "retrieved_at": "2026-05-07T00:00:00Z",
            "summary_or_snippet": "The source reports UN comments that aid agencies could not bring durable shelter materials into Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        }
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_manual_source_generation_writes_public_edition_and_manifests(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    backup_root = work / "output" / "test-backups" / "gaza"
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", backup_root)
    manual_path = write_manual_sources(work, "2026-04-30")

    result = run_gaza_dispatch(work, "2026-04-30", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    edition_dir = work / "output" / "site" / "gaza" / "editions" / "2026-04-30"
    dispatch_dir = work / "output" / "dispatches" / "gaza" / "editions" / "2026-04-30"
    assert result["ok"] is True
    assert result["manual_source_path"] == str(manual_path)
    assert (work / "data" / "dispatches" / "gaza" / "raw" / "2026-04-30" / "raw_sources.json").exists()
    assert (work / "data" / "dispatches" / "gaza" / "normalized" / "2026-04-30" / "normalized_sources.json").exists()
    assert (work / "data" / "dispatches" / "gaza" / "curated" / "2026-04-30" / "curation_manifest.json").exists()
    assert (edition_dir / "index.html").exists()
    assert (dispatch_dir / "index.html").exists()
    html = read(edition_dir / "index.html")
    assert "Dispatches Home" in html
    assert "Sources" in html
    assert "https://www.aa.com.tr/en/middle-east/un-says-israel-blocks-durable-shelter-materials-from-entering-gaza/3923572" in html
    assert "source_record_id" in read(edition_dir / "sources_manifest.json")


def test_missing_manual_sources_fail_safely(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")

    with pytest.raises(FileNotFoundError):
        run_gaza_dispatch(work, "2026-04-30", from_manual_sources=True, dry_run=False, render=False, all_steps=True)


def test_invalid_source_record_does_not_invent_sources(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-04-29",
        [
            {
                "source_record_id": "bad-source",
                "title": "Missing URL source",
                "publisher": "Example",
                "published_at": "2026-04-29T00:00:00Z",
                "retrieved_at": "2026-05-07T00:00:00Z",
                "summary_or_snippet": "This should not become a story.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "test",
            }
        ],
    )

    result = run_gaza_dispatch(work, "2026-04-29", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    edition_dir = work / "output" / "site" / "gaza" / "editions" / "2026-04-29"
    sources = json.loads(read(edition_dir / "sources_manifest.json"))
    curation = json.loads(read(edition_dir / "curation_manifest.json"))
    assert result["ok"] is False
    assert sources == []
    assert curation == []
    assert "No source records were available" in read(edition_dir / "index.html")


def test_archive_rss_latest_and_shared_records(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    backup_root = work / "output" / "test-backups" / "gaza"
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", backup_root)
    write_manual_sources(work, "2026-04-30")
    write_manual_sources(work, "2026-05-01")

    run_gaza_dispatch(work, "2026-04-30", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    run_gaza_dispatch(work, "2026-05-01", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    archive = read(work / "output" / "site" / "gaza" / "archive.html")
    rss = read(work / "output" / "site" / "gaza" / "rss.xml")
    index = read(work / "output" / "site" / "gaza" / "index.html")
    assert archive.index("2026-05-01") < archive.index("2026-04-30")
    assert rss.index("2026-05-01") < rss.index("2026-04-30")
    assert 'href="editions/2026-05-01/">Read the latest briefing</a>' in index

    dispatches = json.loads(read(work / "data" / "records" / "dispatches.json"))
    editions = json.loads(read(work / "data" / "records" / "editions.json"))
    sources = json.loads(read(work / "data" / "records" / "sources.json"))
    records = json.loads(read(work / "data" / "records" / "records.json"))
    detail_packages = json.loads(read(work / "data" / "records" / "detail_packages.json"))
    gaza = next(row for row in dispatches if row["dispatch_slug"] == "gaza")
    edition = next(row for row in editions if row["edition_id"] == "gaza-2026-05-01")
    assert gaza["is_free_public"] is True
    assert gaza["has_detail_tier"] is False
    assert gaza["public_exposed"] is True
    assert edition["public_exposed"] is True
    assert sources[0]["source_id"]
    assert records[0]["source_ids"]
    assert detail_packages == []
    assert (backup_root / "2026-05-01" / "sources_manifest.json").exists()


def test_repeated_cross_edition_sources_fail_cleanly_and_write_dedupe_report(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    repeated = [
        {
            "source_record_id": "gaza-src-001",
            "title": "Dispatches From Gaza - 2026-05-10",
            "url": "https://news.google.com/rss/articles/abc123?utm_source=rss",
            "publisher": "Google News",
            "published_at": "2026-05-10T08:00:00+00:00",
            "retrieved_at": "2026-05-10T08:00:00+00:00",
            "summary_or_snippet": "Structured daily briefing synthesizing key developments from public reporting.",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "general",
            "reliability_tier": "reported-public-source",
        }
    ]
    write_manual_sources(work, "2026-05-10", repeated)
    first = run_gaza_dispatch(work, "2026-05-10", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert first["ok"] is True
    write_manual_sources(work, "2026-05-11", repeated)

    result = run_gaza_dispatch(work, "2026-05-11", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    dedupe_report = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-11" / "dedupe_report.json"
    report = json.loads(dedupe_report.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "No new source-backed Gaza developments after cross-edition dedupe" in " ".join(result["errors"])
    assert report["suppressed_candidate_count"] >= 1
