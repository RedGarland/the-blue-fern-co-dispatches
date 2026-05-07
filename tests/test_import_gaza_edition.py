import json
import shutil
import uuid
from pathlib import Path

from scripts.import_gaza_edition import import_one, run_imports


class Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_work_root(repo: Path) -> Path:
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    (work / "data" / "records").mkdir(parents=True)
    for name in ("dispatches", "editions", "sources", "records", "curation_decisions", "detail_packages"):
        (work / "data" / "records" / f"{name}.json").write_text("[]", encoding="utf-8")
    for asset in ("site.css", "gaza-logo.png", "bluefern.png"):
        target = work / "output" / "site" / "gaza" / "assets" / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / "assets" / asset, target)
    return work


def make_source(tmp_path: Path, date: str, with_structured_sources: bool = True) -> Path:
    source = tmp_path / "old-gaza" / "output" / "editions" / date
    source.mkdir(parents=True)
    (source / "edition.md").write_text(
        f"""# Dispatches From Gaza

Daily Briefing - {date}

A preserved older public Gaza edition with a source link.

Source: https://example.com/gaza-{date}
""",
        encoding="utf-8",
    )
    (source / "edition.docx").write_text("placeholder", encoding="utf-8")
    (source / "paid").mkdir()
    (source / "paid" / "detail.json").write_text('{"do_not_copy": true}', encoding="utf-8")
    if with_structured_sources:
        (source / "sources_manifest.json").write_text(
            json.dumps(
                [
                    {
                        "source_id": f"gaza-src-{date}",
                        "title": f"Source for {date}",
                        "url": f"https://example.com/gaza-{date}",
                        "publisher": "Example News",
                        "published_at": f"{date}T00:00:00Z",
                    }
                ]
            ),
            encoding="utf-8",
        )
    return source


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_import_single_gaza_edition_from_fixture(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    source = make_source(tmp_path, "2026-04-30")
    monkeypatch.setattr("scripts.import_gaza_edition.ROOT", work)
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", tmp_path / "backups" / "gaza")

    result = import_one(work, "2026-04-30", source, dry_run=False, force=False)

    edition_dir = work / "output" / "site" / "gaza" / "editions" / "2026-04-30"
    assert result["ok"] is True
    assert (edition_dir / "index.html").exists()
    html = read(edition_dir / "index.html")
    assert "Dispatches Home" in html
    assert 'href="/gaza/"' in html
    assert 'src="../../assets/gaza-logo.png"' in html
    assert (work / "output" / "dispatches" / "gaza" / "editions" / "2026-04-30" / "index.html").exists()


def test_import_multiple_gaza_editions_from_source_root(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    root = tmp_path / "old-gaza" / "output" / "editions"
    make_source(tmp_path, "2026-04-29")
    make_source(tmp_path, "2026-04-30")
    make_source(tmp_path, "2026-05-01")
    monkeypatch.setattr("scripts.import_gaza_edition.ROOT", work)
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", tmp_path / "backups" / "gaza")

    args = Args(
        source_root=str(root),
        start_date="2026-04-30",
        end_date="2026-05-01",
        date=None,
        source_edition_dir=None,
        dry_run=False,
        force=False,
    )
    result = run_imports(args)

    assert result["imported_dates"] == ["2026-04-30", "2026-05-01"]
    assert (work / "output" / "site" / "gaza" / "editions" / "2026-04-30" / "index.html").exists()
    assert (work / "output" / "site" / "gaza" / "editions" / "2026-05-01" / "index.html").exists()


def test_archive_and_rss_update_reverse_chronological(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", tmp_path / "backups" / "gaza")
    import_one(work, "2026-04-29", make_source(tmp_path, "2026-04-29"), dry_run=False, force=False)
    import_one(work, "2026-05-01", make_source(tmp_path, "2026-05-01"), dry_run=False, force=False)

    archive = read(work / "output" / "site" / "gaza" / "archive.html")
    rss = read(work / "output" / "site" / "gaza" / "rss.xml")
    index = read(work / "output" / "site" / "gaza" / "index.html")
    assert archive.index("2026-05-01") < archive.index("2026-04-29")
    assert rss.index("2026-05-01") < rss.index("2026-04-29")
    assert 'href="editions/2026-05-01/">Read the latest briefing</a>' in index


def test_manifests_written_and_missing_structured_sources_warn(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    source = make_source(tmp_path, "2026-04-28", with_structured_sources=False)
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", tmp_path / "backups" / "gaza")

    import_one(work, "2026-04-28", source, dry_run=False, force=False)

    edition_dir = work / "output" / "site" / "gaza" / "editions" / "2026-04-28"
    sources = json.loads(read(edition_dir / "sources_manifest.json"))
    import_manifest = json.loads(read(edition_dir / "import_manifest.json"))
    assert (edition_dir / "edition_manifest.json").exists()
    assert (edition_dir / "curation_manifest.json").exists()
    assert sources == []
    assert import_manifest["source_links_detected"] is True
    assert import_manifest["did_not_invent_sources"] is True
    assert any("missing structured source records" in warning for warning in import_manifest["warnings"])


def test_gaza_records_free_public_and_no_detail_artifacts_copied(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    source = make_source(tmp_path, "2026-04-27")
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", tmp_path / "backups" / "gaza")

    import_one(work, "2026-04-27", source, dry_run=False, force=False)

    dispatches = json.loads(read(work / "data" / "records" / "dispatches.json"))
    editions = json.loads(read(work / "data" / "records" / "editions.json"))
    detail_packages = json.loads(read(work / "data" / "records" / "detail_packages.json"))
    gaza = next(row for row in dispatches if row["slug"] == "gaza")
    edition = next(row for row in editions if row["edition_id"] == "gaza-2026-04-27")
    public_files = [path.relative_to(work / "output" / "site").as_posix() for path in (work / "output" / "site").rglob("*") if path.is_file()]
    assert gaza["is_free_public"] is True
    assert gaza["has_detail_tier"] is False
    assert edition["public_exposed"] is True
    assert edition["is_free_public"] is True
    assert not detail_packages
    assert not any("paid" in path or "detail.json" in path for path in public_files)


def test_backups_include_original_and_manifests(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    backup_root = tmp_path / "backups" / "gaza"
    monkeypatch.setattr("scripts.import_gaza_edition.BACKUP_ROOT", backup_root)

    import_one(work, "2026-04-26", make_source(tmp_path, "2026-04-26"), dry_run=False, force=False)

    backup = backup_root / "2026-04-26"
    assert (backup / "index.html").exists()
    assert (backup / "edition_manifest.json").exists()
    assert (backup / "sources_manifest.json").exists()
    assert (backup / "curation_manifest.json").exists()
    assert (backup / "import_manifest.json").exists()
    assert (backup / "original" / "edition.md").exists()
