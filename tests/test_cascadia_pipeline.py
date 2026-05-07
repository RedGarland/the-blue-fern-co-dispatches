import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.cascadia_curate import curate_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources, load_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import render_cascadia_edition, refresh_cascadia_archive_pages
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, containing_week, explicit_week, previous_completed_week
from bluefern_dispatches.generator import CASCADIA_LOGO_ASSET, build_site, publish_pages
from bluefern_dispatches.shared_records import update_shared_records

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from run_cascadia_dispatch import completed_week_windows, run_pipeline


@pytest.fixture()
def cascadia_work_root():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    (root / "data" / "dispatches" / "cascadia").mkdir(parents=True)
    shutil.copytree(repo / "assets", root / "assets")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "sources.yml", root / "data" / "dispatches" / "cascadia" / "sources.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "manual_sources.json", root / "data" / "dispatches" / "cascadia" / "manual_sources.json")
    return root


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_cascadia_sources_yml_loads(cascadia_work_root):
    sources = load_sources(cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources.yml")

    assert sources
    assert any(source["source_id"] == "cascadia-manual" for source in sources)
    assert all("reliability_tier" in source for source in sources)


def test_weekly_window_logic():
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-11")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-12")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-18")) == ("2026-05-11", "2026-05-17")
    assert tuple(day.isoformat() for day in containing_week("2026-05-06")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in explicit_week("2026-05-04", "2026-05-10")) == ("2026-05-04", "2026-05-10")
    with pytest.raises(ValueError):
        explicit_week("2026-05-05", "2026-05-10")
    assert completed_week_windows("2026-05-11", 4) == [
        ("2026-05-04", "2026-05-10", "2026-05-10"),
        ("2026-04-27", "2026-05-03", "2026-05-03"),
        ("2026-04-20", "2026-04-26", "2026-04-26"),
        ("2026-04-13", "2026-04-19", "2026-04-19"),
    ]


def test_ingestion_runs_with_manual_fixture(cascadia_work_root):
    result = ingest_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    assert result["raw_count"] == 3
    raw = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "raw" / "2026-05-03" / "raw_sources.json")
    assert raw[0]["source_record_id"]
    assert raw[0]["url"]


def test_normalization_dedupes_records(cascadia_work_root):
    raw_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "raw" / "2026-05-03"
    raw_dir.mkdir(parents=True)
    duplicate = {
        "source_record_id": "raw-1",
        "source_id": "cascadia-manual",
        "source_name": "Manual",
        "title": "Oregon emergency management preparedness notice",
        "url": "https://www.oregon.gov/oem/",
        "published_at": "2026-05-03T00:00:00Z",
        "retrieved_at": "2026-05-03T01:00:00Z",
        "summary_or_snippet": "Emergency management notice.",
        "raw_payload": {},
        "region_scope": "OR",
        "category_hint": "Public safety",
    }
    (raw_dir / "raw_sources.json").write_text(json.dumps([duplicate, dict(duplicate, source_record_id="raw-2")]), encoding="utf-8")

    result = normalize_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    assert result["normalized_count"] == 1
    assert "deduped duplicate record" in result["warnings"][0]


def test_curation_excludes_sports_and_keeps_public_source_urls(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    result = curate_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    excluded = [story for story in curated if story["excluded_reason"]]
    public = [story for story in curated if story["included_in_public_summary"]]
    assert any(story["excluded_reason"] == "sports" for story in excluded)
    assert public
    assert all(story["source_urls"] for story in public)
    assert all(story["source_record_ids"] for story in public)


def test_render_writes_manifests_links_and_detail_only_outside_public(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = render_cascadia_edition(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    public_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    detail_dir = cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-03"
    assert (public_dir / "index.html").exists()
    assert (public_dir / "edition_manifest.json").exists()
    assert (public_dir / "sources_manifest.json").exists()
    assert (public_dir / "curation_manifest.json").exists()
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    assert "The Cascadia Briefing" in html
    assert "Cascadia Signal Pack" in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    curation = read_json(public_dir / "curation_manifest.json")
    assert all("source_record_ids" in story for story in curation)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in (cascadia_work_root / "output" / "site").rglob("*") if path.suffix in {".html", ".json", ".xml", ".css"})
    assert "output/detail" not in public_text
    assert "cascadia_signal_records" not in public_text
    assert (detail_dir / "cascadia_signal_records.json").exists()
    assert (detail_dir / "cascadia_signal_records.csv").exists()
    assert (detail_dir / "cascadia_source_manifest.json").exists()
    assert (detail_dir / "cascadia_category_summary.json").exists()
    assert (detail_dir / "cascadia_category_summary.csv").exists()
    assert (detail_dir / "cascadia_run_manifest.json").exists()
    assert (detail_dir / "cascadian_detail_records.json").exists()
    assert (detail_dir / "cascadian_detail_records.csv").exists()
    edition_manifest = read_json(public_dir / "edition_manifest.json")
    assert edition_manifest["briefing_type"] == "weekly"
    assert edition_manifest["dispatch_slug"] == "cascadia"
    assert edition_manifest["public_name"] == "The Cascadia Briefing"
    public_paths = [path.relative_to(cascadia_work_root / "output" / "site").as_posix() for path in (cascadia_work_root / "output" / "site").rglob("*") if path.is_file()]
    assert not any(path.startswith("detail/") or path.startswith("paid/") for path in public_paths)


def test_weekly_aggregation_filters_dedupes_and_renders(cascadia_work_root):
    start, end = containing_week("2026-05-06")
    records = [
        {
            "source_record_id": "src-in",
            "canonical_url": "https://example.com/weekly",
            "title": "Washington bridge inspection program",
            "publisher": "Source",
            "published_at": "2026-05-04T08:00:00Z",
            "retrieved_at": "2026-05-04T09:00:00Z",
            "text": "Bridge inspection update.",
            "source_id": "cascadia-manual",
            "region_scope": "WA",
            "category_hint": "Transportation",
        },
        {
            "source_record_id": "src-out",
            "canonical_url": "https://example.com/outside",
            "title": "Outside record",
            "publisher": "Source",
            "published_at": "2026-05-11T08:00:00Z",
            "retrieved_at": "2026-05-11T09:00:00Z",
            "text": "Outside the week.",
            "source_id": "cascadia-manual",
            "region_scope": "WA",
            "category_hint": "Transportation",
        },
    ]
    story = {
        "story_id": "story-in",
        "title": "Washington bridge inspection program",
        "summary": "Bridge inspection update.",
        "category": "Transportation",
        "score": 80,
        "source_record_ids": ["src-in"],
        "source_urls": ["https://example.com/weekly"],
        "included_in_public_summary": True,
        "included_in_detail_dataset": True,
        "excluded_reason": None,
        "source_records": [records[0]],
    }
    duplicate = dict(story, story_id="story-dup", score=70)
    outside = dict(story, story_id="story-out", title="Outside record", source_record_ids=["src-out"], source_urls=["https://example.com/outside"], source_records=[records[1]])
    for day, payload in {"2026-05-04": [story, outside], "2026-05-05": [duplicate]}.items():
        normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / day
        curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / day
        normalized_dir.mkdir(parents=True)
        curated_dir.mkdir(parents=True)
        (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        (curated_dir / "curation_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    aggregate = aggregate_weekly_curation(cascadia_work_root, "2026-05-11", start, end)
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-10",
        run_date="2026-05-11",
        coverage_start="2026-05-04",
        coverage_end="2026-05-10",
        briefing_type="weekly",
    )

    assert aggregate["curated_count"] == 1
    assert result["ok"] is True
    public_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-10"
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    assert "Weekly briefing / Coverage: 2026-05-04 through 2026-05-10" in html
    assert "https://example.com/weekly" in html
    assert "https://example.com/outside" not in html
    manifest = read_json(public_dir / "edition_manifest.json")
    assert manifest["briefing_type"] == "weekly"
    assert manifest["run_date"] == "2026-05-11"
    assert manifest["coverage_start"] == "2026-05-04"
    assert manifest["coverage_end"] == "2026-05-10"
    assert manifest["week_label"] == "2026-W19"
    assert manifest["source_record_ids"] == ["src-in"]
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-10" in archive
    assert "Weekly source-backed regional briefings for Washington, Oregon, and Idaho." not in archive
    assert "Weekly source-backed regional briefings" in (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")


def test_cascadia_archive_recent_and_rss_list_weekly_editions_only(cascadia_work_root):
    editions_root = cascadia_work_root / "output" / "site" / "cascadia" / "editions"
    for day in ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]:
        edition_dir = editions_root / day
        edition_dir.mkdir(parents=True)
        (edition_dir / "index.html").write_text(f"<p>Daily {day}</p>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps({"dispatch_slug": "cascadia", "edition_date": day, "briefing_type": "daily"}, indent=2),
            encoding="utf-8",
        )
    weekly_dates = {
        "2026-05-10": ("2026-05-04", "2026-05-10", "2026-W19"),
        "2026-05-03": ("2026-04-27", "2026-05-03", "2026-W18"),
        "2026-04-26": ("2026-04-20", "2026-04-26", "2026-W17"),
        "2026-04-19": ("2026-04-13", "2026-04-19", "2026-W16"),
    }
    for edition_date, (coverage_start, coverage_end, week) in weekly_dates.items():
        edition_dir = editions_root / edition_date
        edition_dir.mkdir(parents=True)
        (edition_dir / "index.html").write_text(f"<p>Weekly {edition_date}</p>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "cascadia",
                    "edition_date": edition_date,
                    "briefing_type": "weekly",
                    "coverage_start": coverage_start,
                    "coverage_end": coverage_end,
                    "week_label": week,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    written = []
    refresh_cascadia_archive_pages(cascadia_work_root, dry_run=False, written=written)

    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    index = (cascadia_work_root / "output" / "site" / "cascadia" / "index.html").read_text(encoding="utf-8")
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")
    for text in [archive, index, rss]:
        for day in ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]:
            assert day not in text
        for weekly_date in weekly_dates:
            assert weekly_date in text
    assert "Weekly source-backed regional briefings for Washington, Oregon, and Idaho." not in archive


def test_shared_dispatch_records_include_gaza_cascadia_and_signal_package(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    detail = write_cascadia_signal_package(cascadia_work_root, "2026-05-03")
    result = update_shared_records(cascadia_work_root, "2026-05-03", detail["output_paths"], public_rendered=False)

    assert result["ok"] is True
    records_root = cascadia_work_root / "data" / "records"
    dispatches = read_json(records_root / "dispatches.json")
    editions = read_json(records_root / "editions.json")
    sources = read_json(records_root / "sources.json")
    records = read_json(records_root / "records.json")
    packages = read_json(records_root / "detail_packages.json")
    assert {row["slug"] for row in dispatches} >= {"gaza", "cascadia"}
    assert any(row["internal_name"] == "Cascadia Signal" for row in dispatches)
    assert all("dispatch_id" in row for row in editions)
    assert all("dispatch_id" in row for row in sources)
    assert all("source_ids" in row for row in records)
    assert packages and all(row["public_exposed"] is False for row in packages)


def test_signal_package_movement_fields_first_and_second_run(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    first = write_cascadia_signal_package(cascadia_work_root, "2026-05-03")
    assert first["ok"] is True
    first_records = read_json(cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-03" / "cascadia_signal_records.json")
    assert all(record["movement_label"] == "New" for record in first_records)
    assert all(record["trend_direction"] == "new" for record in first_records)

    ingest_sources(cascadia_work_root, "2026-05-04")
    normalize_sources(cascadia_work_root, "2026-05-04")
    curate_sources(cascadia_work_root, "2026-05-04")
    second = write_cascadia_signal_package(cascadia_work_root, "2026-05-04")
    second_records = read_json(cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-04" / "cascadia_signal_records.json")
    assert second["ok"] is True
    assert all("first_seen" in record and "last_seen" in record for record in second_records)
    assert {record["movement_label"] for record in second_records} <= {"New", "Rising", "Falling", "Stable"}
    assert {record["trend_direction"] for record in second_records} <= {"new", "up", "down", "flat"}


def test_generic_build_preserves_real_cascadia_public_edition(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(cascadia_work_root, "2026-05-03")

    result = build_site(cascadia_work_root, backup_root=cascadia_work_root / "backup")

    assert result["ok"] is True
    public_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "Washington bridge inspection program" in public_html
    assert "Oregon emergency management preparedness notice" in public_html
    assert "Launch placeholder" not in public_html
    assert "Placeholder source" not in public_html


def test_pages_publish_copies_real_cascadia_public_edition(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(cascadia_work_root, "2026-05-03")
    old_daily_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-04"
    old_daily_dir.mkdir(parents=True)
    (old_daily_dir / "index.html").write_text("<p>Old daily Cascadia edition</p>", encoding="utf-8")
    (old_daily_dir / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "cascadia", "edition_date": "2026-05-04", "briefing_type": "daily"}, indent=2),
        encoding="utf-8",
    )
    (old_daily_dir / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (old_daily_dir / "curation_manifest.json").write_text("[]", encoding="utf-8")
    pages_repo = cascadia_work_root / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / ".keep").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "add", ".keep"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "Initial Pages repo"], cwd=pages_repo, check=True, capture_output=True, text=True)
    stale_pages_daily_dir = pages_repo / "cascadia" / "editions" / "2026-05-04"
    stale_pages_daily_dir.mkdir(parents=True)
    (stale_pages_daily_dir / "index.html").write_text("<p>Stale daily page</p>", encoding="utf-8")

    result = publish_pages(cascadia_work_root, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=cascadia_work_root / "backup")

    assert result["ok"] is True
    pages_html = (pages_repo / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "Washington bridge inspection program" in pages_html
    assert "Oregon emergency management preparedness notice" in pages_html
    assert "Launch placeholder" not in pages_html
    assert "Placeholder source" not in pages_html
    assert 'target="_blank" rel="noopener noreferrer"' in pages_html
    assert (pages_repo / "assets" / CASCADIA_LOGO_ASSET).read_bytes() == (cascadia_work_root / "assets" / CASCADIA_LOGO_ASSET).read_bytes()
    assert (pages_repo / "cascadia" / "assets" / CASCADIA_LOGO_ASSET).read_bytes() == (cascadia_work_root / "assets" / CASCADIA_LOGO_ASSET).read_bytes()
    assert not (pages_repo / "cascadia" / "editions" / "2026-05-04").exists()
    assert result["non_publishable_pages_editions_removed"]
    assert not (pages_repo / "detail").exists()
    assert not (pages_repo / "paid").exists()


def test_daily_and_weekly_public_modes_write_expected_artifacts(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    daily = run_pipeline("2026-05-03", ingest=True, normalize=True, curate=True, render=False, dry_run=False, mode="daily")
    assert daily["ok"] is True
    assert daily["mode"] == "daily"
    assert daily["public_rendered"] is False
    assert daily["raw_count"] == 3
    assert daily["normalized_count"] == 3
    assert daily["curated_count"] == 3
    assert daily["detail_count"] == 3
    assert daily["shared_record_paths"]
    assert not (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()

    weekly = run_pipeline("2026-05-03", ingest=False, normalize=False, curate=False, render=True, dry_run=False, mode="weekly-public")
    assert weekly["ok"] is True
    assert weekly["mode"] == "weekly-public"
    assert weekly["public_rendered"] is True
    assert (cascadia_work_root / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()


def test_archive_week_cli_uses_sunday_edition_date(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    ingest_sources(cascadia_work_root, "2026-05-04")
    normalize_sources(cascadia_work_root, "2026-05-04")
    normalized_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-04" / "normalized_sources.json"
    normalized = read_json(normalized_path)
    for record in normalized:
        record["published_at"] = "2026-05-04T08:00:00Z"
    normalized_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    curate_sources(cascadia_work_root, "2026-05-04")

    code = run_cascadia_dispatch.main(["--archive-week", "2026-05-06", "--weekly-public"])

    assert code == 0
    edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-10"
    assert (edition_dir / "index.html").exists()
    manifest = read_json(edition_dir / "edition_manifest.json")
    assert manifest["edition_date"] == "2026-05-10"
    assert manifest["coverage_start"] == "2026-05-04"
    assert manifest["coverage_end"] == "2026-05-10"


def test_backfill_weeks_cli_generates_completed_weekly_editions_without_sources(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)

    code = run_cascadia_dispatch.main(["--weekly-public", "--backfill-weeks", "4", "--date", "2026-05-11"])

    assert code == 0
    expected = {
        "2026-05-10": ("2026-05-04", "2026-05-10"),
        "2026-05-03": ("2026-04-27", "2026-05-03"),
        "2026-04-26": ("2026-04-20", "2026-04-26"),
        "2026-04-19": ("2026-04-13", "2026-04-19"),
    }
    for edition_date, (coverage_start, coverage_end) in expected.items():
        edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / edition_date
        html = (edition_dir / "index.html").read_text(encoding="utf-8")
        manifest = read_json(edition_dir / "edition_manifest.json")
        assert "Weekly briefing" in html
        assert coverage_start in html
        assert coverage_end in html
        assert "No qualifying source-backed Cascadia signals were identified" in html
        assert manifest["briefing_type"] == "weekly"
        assert manifest["edition_date"] == edition_date
        assert manifest["coverage_start"] == coverage_start
        assert manifest["coverage_end"] == coverage_end
        assert manifest["run_date"] == "2026-05-11"
        assert "source_record_ids" in manifest
        assert "source_urls" in manifest
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert all(edition_date in archive for edition_date in expected)
