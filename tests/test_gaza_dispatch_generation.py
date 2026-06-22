import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.generator import build_site
from bluefern_dispatches.gaza_audio import write_gaza_audio_outputs
from scripts.run_gaza_dispatch import build_source_diversity_report, curate_stories, normalize_sources, render_gaza_edition, run_gaza_dispatch


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
    payload = records if records is not None else [
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


def test_four_sources_one_publisher_renders_limited_source_update_with_top_note(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    records = []
    for idx in range(1, 5):
        records.append(
            {
                "source_record_id": f"gaza-src-2026-05-26-{idx:03d}",
                "title": f"Gaza aid update {idx}",
                "url": f"https://example.com/gaza-aid-{idx}",
                "publisher": "Al Jazeera",
                "published_at": "2026-05-26T12:00:00Z",
                "retrieved_at": "2026-05-26T12:10:00Z",
                "summary_or_snippet": "Gaza aid and humanitarian access update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-26", records)
    result = run_gaza_dispatch(work, "2026-05-26", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-26" / "index.html")
    assert result["ok"] is True
    assert result["source_adequacy_status"] == "limited_source_update"
    assert "Limited-source update / May 26, 2026" in html
    assert "This is a limited-source update generated from 4 saved source records from 1 publisher." in html
    assert "All saved source records for this edition came from Al Jazeera." in html
    assert html.index("This is a limited-source update") < html.index("<h2>At A Glance</h2>")
    assert '<strong>Sources</strong>' in html
    assert 'href="https://example.com/gaza-aid-1"' in html
    assert "Lead Development" not in html


def test_limited_source_warning_uses_plural_publishers_for_two_publishers(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    records = []
    for idx in range(1, 5):
        records.append(
            {
                "source_record_id": f"gaza-src-2026-05-29-{idx:03d}",
                "title": f"Gaza aid update {idx}",
                "url": f"https://example.com/gaza-aid-two-pub-{idx}",
                "publisher": "Al Jazeera" if idx <= 2 else "Reuters",
                "published_at": "2026-05-29T12:00:00Z",
                "retrieved_at": "2026-05-29T12:10:00Z",
                "summary_or_snippet": "Gaza aid and humanitarian access update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-29", records)
    result = run_gaza_dispatch(work, "2026-05-29", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-29" / "index.html")
    assert result["ok"] is True
    assert "This is a limited-source update generated from 4 saved source records from 2 publishers." in html
    assert "This is a limited-source update generated from 4 saved source records from 2 publisher." not in html


def test_aljazeera_sources_produce_non_empty_source_family_classification(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = []
    for idx in range(1, 4):
        rows.append(
            {
                "source_record_id": f"gaza-src-2026-05-30-{idx:03d}",
                "title": f"Gaza hospital aid update {idx}",
                "url": f"https://example.com/alj-source-family-{idx}",
                "publisher": "Al Jazeera",
                "published_at": "2026-05-30T12:00:00Z",
                "retrieved_at": "2026-05-30T12:10:00Z",
                "summary_or_snippet": "Hospitals and aid access disruptions were reported in Gaza.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-30", rows)
    result = run_gaza_dispatch(work, "2026-05-30", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-30" / "source_quality_report.json"))
    family_counts = dict(report.get("source_family_counts") or {})
    assert family_counts
    assert int(family_counts.get("news_media", 0)) >= 1


def test_anadolu_manual_source_is_classified_as_regional_wire_and_rendered(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "aa-2026-05-31-gaza-airstrikes-injuries",
            "title": "6 Palestinians injured in Israeli airstrikes across Gaza Strip",
            "url": "https://www.aa.com.tr/en/middle-east/6-palestinians-injured-in-israeli-airstrikes-across-gaza-strip/3952450",
            "publisher": "Anadolu Agency",
            "published_at": "2026-05-31",
            "retrieved_at": "2026-05-31T13:00:35.954473+00:00",
            "summary_or_snippet": "Six Palestinians were reported injured in Israeli airstrikes across Gaza locations.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
            "source_family": "regional_wire",
            "claim_status": "reported",
        },
        {
            "source_record_id": "gaza-src-2026-05-31-001",
            "title": "Gaza aid and displacement update",
            "url": "https://example.com/gaza-aid-001",
            "publisher": "Reuters",
            "published_at": "2026-05-31T10:00:00Z",
            "retrieved_at": "2026-05-31T11:00:00Z",
            "summary_or_snippet": "Aid and displacement pressures were reported in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-05-31-002",
            "title": "Hospital access constraints in Gaza",
            "url": "https://example.com/gaza-hospital-001",
            "publisher": "BBC News",
            "published_at": "2026-05-31T11:00:00Z",
            "retrieved_at": "2026-05-31T11:05:00Z",
            "summary_or_snippet": "Hospitals faced access constraints after airstrikes in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, "2026-05-31", rows)
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-31" / "source_quality_report.json"))
    assert int((report.get("source_family_counts") or {}).get("regional_wire", 0)) >= 1
    sources_manifest = json.loads(read(work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-31" / "sources_manifest.json"))
    assert any(str(row.get("publisher") or "") == "Anadolu Agency" for row in sources_manifest)


def test_duplicate_airstrike_records_are_deduped_in_normalization():
    now = "2026-05-31T13:00:35.954473+00:00"
    records = [
        {
            "source_record_id": "aa-2026-05-31-gaza-airstrikes-injuries-1",
            "title": "6 Palestinians injured in Israeli airstrikes across Gaza Strip",
            "url": "https://www.aa.com.tr/en/middle-east/6-palestinians-injured-in-israeli-airstrikes-across-gaza-strip/3952450",
            "publisher": "Anadolu Agency",
            "published_at": "2026-05-31",
            "retrieved_at": now,
            "summary_or_snippet": "Six Palestinians were reported injured in Israeli airstrikes across Gaza locations.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "aa-2026-05-31-gaza-airstrikes-injuries-2",
            "title": "6 Palestinians injured in Israeli airstrikes across Gaza Strip",
            "url": "https://www.aa.com.tr/en/middle-east/6-palestinians-injured-in-israeli-airstrikes-across-gaza-strip/3952450",
            "publisher": "Anadolu Agency",
            "published_at": "2026-05-31",
            "retrieved_at": now,
            "summary_or_snippet": "Six Palestinians were reported injured in Israeli airstrikes across Gaza locations.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
    ]
    normalized, warnings, errors = normalize_sources(records, "2026-05-31", now)
    assert errors == []
    assert len(normalized) == 1
    assert any("deduped duplicate source record" in item for item in warnings)


def test_aljazeera_satellite_story_counts_as_core_ground_development():
    now = "2026-05-31T18:30:00+00:00"
    records = [
        {
            "source_record_id": "alj-2026-05-31-gaza-satellite-erasure-control",
            "title": "Satellite imagery shows erasure of southern Gaza as Israel expands control",
            "url": "https://www.aljazeera.com/news/2026/5/31/satellite-imagery-shows-erasure-of-southern-gaza-as-israel-expands-control",
            "publisher": "Al Jazeera",
            "published_at": "2026-05-31",
            "retrieved_at": now,
            "summary_or_snippet": "Al Jazeera reported satellite imagery indicating extensive destruction in southern Gaza.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "territorial_control_and_destruction",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "reported_public_source",
        }
    ]
    normalized, _warnings, errors = normalize_sources(records, "2026-05-31", now)
    assert errors == []
    stories, _rejected, _top = curate_stories(normalized, "2026-05-31", now)
    assert len(stories) == 1
    assert stories[0]["core_ground_development"] is True


def test_new_arab_drone_strike_qualifies_as_core_ground_development():
    now = "2026-05-31T18:35:00+00:00"
    records = [
        {
            "source_record_id": "newarab-2026-05-31-al-bureij-drone-strike-injuries",
            "title": "Israeli drone strike injures Palestinians in Al-Bureij camp in Gaza",
            "url": "https://www.newarab.com/news/israeli-drone-strike-injures-palestinians-al-bureij-camp-gaza",
            "publisher": "The New Arab",
            "published_at": "2026-05-31",
            "retrieved_at": now,
            "summary_or_snippet": "The New Arab reported injuries after an Israeli drone strike in Al-Bureij.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "airstrikes_and_civilian_harm",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "reported_public_source",
        }
    ]
    normalized, _warnings, errors = normalize_sources(records, "2026-05-31", now)
    assert errors == []
    stories, _rejected, _top = curate_stories(normalized, "2026-05-31", now)
    assert stories[0]["core_ground_development"] is True


def test_i24news_military_claim_renders_claim_attribution_caveat(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "i24-2026-05-31-idf-weapons-storage-destruction",
            "title": "IDF says it destroyed Hamas weapons storage facilities in Gaza",
            "url": "https://www.i24news.tv/en/news/israel-at-war/artc-idf-says-it-destroyed-hamas-weapons-storage-facilities-in-gaza",
            "publisher": "i24NEWS",
            "published_at": "2026-05-31",
            "retrieved_at": "2026-05-31T18:40:00+00:00",
            "summary_or_snippet": "i24NEWS reported an IDF statement saying Israeli forces destroyed Hamas weapons storage facilities in Gaza.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "military_operations",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "military_claim_reported",
            "claim_status": "military_claim_reported",
        },
        {
            "source_record_id": "support-2026-05-31-001",
            "title": "Gaza hospitals report emergency caseload pressures",
            "url": "https://example.com/gaza-hosp-pressures",
            "publisher": "Reuters",
            "published_at": "2026-05-31T12:00:00Z",
            "retrieved_at": "2026-05-31T12:05:00Z",
            "summary_or_snippet": "Hospitals in Gaza reported rising emergency caseload pressures.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "support-2026-05-31-002",
            "title": "Gaza displacement and aid-access constraints continue",
            "url": "https://example.com/gaza-displacement-aid",
            "publisher": "BBC News",
            "published_at": "2026-05-31T13:00:00Z",
            "retrieved_at": "2026-05-31T13:05:00Z",
            "summary_or_snippet": "Displacement and aid access constraints continued in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, "2026-05-31", rows)
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-31" / "index.html")
    assert "based primarily on an IDF statement" in html


def test_gaza_adjacent_context_source_does_not_satisfy_core_ground_threshold(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "jpost-2026-05-31-libya-gaza-bound-convoy-dissolved",
            "title": "Gaza-bound aid convoy dissolved in Libya after arrests",
            "url": "https://www.jpost.com/middle-east/article-855919",
            "publisher": "Jerusalem Post",
            "published_at": "2026-05-31",
            "retrieved_at": "2026-05-31T18:45:00+00:00",
            "summary_or_snippet": "Jerusalem Post reported a Gaza-bound aid convoy in Libya was dissolved after arrests.",
            "source_type": "manual",
            "region_scope": "Libya / Gaza-bound convoy context",
            "category_hint": "humanitarian_access_context",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "gaza_adjacent_context",
            "claim_status": "gaza_adjacent_context",
        }
    ]
    write_manual_sources(work, "2026-05-31", rows)
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is False
    assert any("No substantive Gaza/Palestinian ground-development story cleared threshold" in err for err in result["errors"])


def test_source_manifest_carries_attribution_mode_and_claim_status(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "aa-2026-05-31-gaza-airstrikes-injuries",
            "title": "6 Palestinians injured in Israeli airstrikes across Gaza Strip",
            "url": "https://www.aa.com.tr/en/middle-east/6-palestinians-injured-in-israeli-airstrikes-across-gaza-strip/3952450",
            "publisher": "Anadolu Agency",
            "published_at": "2026-05-31",
            "retrieved_at": "2026-05-31T18:30:00+00:00",
            "summary_or_snippet": "Anadolu Agency reported six Palestinians were injured in airstrikes across Gaza.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "airstrikes_and_civilian_harm",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "reported_public_source",
            "claim_status": "reported_public_source",
        },
        {
            "source_record_id": "support-2026-05-31-003",
            "title": "Gaza aid and hospital operations update",
            "url": "https://example.com/gaza-aid-hospital-ops",
            "publisher": "NPR",
            "published_at": "2026-05-31T12:00:00Z",
            "retrieved_at": "2026-05-31T12:05:00Z",
            "summary_or_snippet": "Hospital operations and aid access constraints were reported in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "support-2026-05-31-004",
            "title": "Gaza displacement and ceasefire condition update",
            "url": "https://example.com/gaza-displacement-ceasefire",
            "publisher": "UN News",
            "published_at": "2026-05-31T13:00:00Z",
            "retrieved_at": "2026-05-31T13:05:00Z",
            "summary_or_snippet": "Displacement and ceasefire conditions were reported in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "official-humanitarian-source",
        },
    ]
    write_manual_sources(work, "2026-05-31", rows)
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    payload = json.loads(read(work / "output" / "site" / "gaza" / "editions" / "2026-05-31" / "sources_manifest.json"))
    aa_row = next(row for row in payload if row["publisher"] == "Anadolu Agency")
    assert aa_row["attribution_mode"] == "reported_public_source"
    assert aa_row["claim_status"] == "reported_public_source"


def test_provider_no_matches_with_manual_same_publisher_marks_discovery_gap(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "alj-2026-05-31-gap-001",
            "title": "Satellite imagery shows erasure of southern Gaza as Israel expands control",
            "url": "https://www.aljazeera.com/news/2026/5/31/satellite-imagery-shows-erasure-of-southern-gaza-as-israel-expands-control",
            "publisher": "Al Jazeera",
            "published_at": "2026-05-31",
            "retrieved_at": "2026-05-31T18:30:00+00:00",
            "summary_or_snippet": "Al Jazeera reported satellite imagery indicating extensive destruction in southern Gaza.",
            "source_type": "manual",
            "region_scope": "Gaza Strip",
            "category_hint": "territorial_control_and_destruction",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "reported_public_source",
        },
        {
            "source_record_id": "support-2026-05-31-005",
            "title": "Gaza aid constraints continue",
            "url": "https://example.com/gaza-aid-constraints-2",
            "publisher": "NPR",
            "published_at": "2026-05-31T12:00:00Z",
            "retrieved_at": "2026-05-31T12:05:00Z",
            "summary_or_snippet": "Aid constraints continued in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "support-2026-05-31-006",
            "title": "Gaza hospital access pressure update",
            "url": "https://example.com/gaza-hospital-pressure-2",
            "publisher": "UN News",
            "published_at": "2026-05-31T13:00:00Z",
            "retrieved_at": "2026-05-31T13:05:00Z",
            "summary_or_snippet": "Hospital access pressure was reported in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "official-humanitarian-source",
        },
    ]
    write_manual_sources(work, "2026-05-31", rows)
    ctx_path = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-31" / "source_collection_context.json"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(
            {
                "provider_diagnostics": [
                    {
                        "source_id": "aljazeera-middle-east",
                        "publisher": "Al Jazeera",
                        "status": "no_matches",
                        "raw_items": 4,
                        "rejected_counts": {"rejected_low_relevance": 4},
                        "no_match_reason_flags": ["items_rejected_by_relevance_or_anchor"],
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-31" / "collection_report.json"))
    alj_diag = next(item for item in report["provider_diagnostics"] if item.get("source_id") == "aljazeera-middle-east")
    assert alj_diag["likely_discovery_gap"] is True
    assert alj_diag["manual_backfill_source"] == ["alj-2026-05-31-gap-001"]


def test_eight_plus_sources_four_publishers_render_daily_briefing(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    publishers = ["Reuters", "AP", "BBC", "UN News", "Reuters", "AP", "BBC", "UN News"]
    records = []
    for idx, publisher in enumerate(publishers, start=1):
        records.append(
            {
                "source_record_id": f"gaza-src-2026-05-21-{idx:03d}",
                "title": f"Gaza humanitarian update {idx}",
                "url": f"https://example.com/gaza-update-{idx}",
                "publisher": publisher,
                "published_at": "2026-05-21T12:00:00Z",
                "retrieved_at": "2026-05-21T12:10:00Z",
                "summary_or_snippet": "Gaza humanitarian and access update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-21", records)
    result = run_gaza_dispatch(work, "2026-05-21", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-21" / "index.html")
    assert result["ok"] is True
    assert result["source_adequacy_status"] == "daily_briefing"
    assert "Daily briefing / May 21, 2026" in html
    assert "limited-source update generated" not in html.lower()


def test_fewer_than_three_sources_does_not_render_as_normal_daily_briefing(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    records = []
    for idx in range(1, 3):
        records.append(
            {
                "source_record_id": f"gaza-src-2026-05-27-{idx:03d}",
                "title": f"Gaza update {idx}",
                "url": f"https://example.com/gaza-thin-{idx}",
                "publisher": "Reuters",
                "published_at": "2026-05-27T12:00:00Z",
                "retrieved_at": "2026-05-27T12:10:00Z",
                "summary_or_snippet": "Gaza update with traceable source.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-27", records)
    result = run_gaza_dispatch(work, "2026-05-27", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    manifest = json.loads(read(work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-27" / "edition_manifest.json"))
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-27" / "index.html")
    assert result["ok"] is True
    assert result["source_adequacy_status"] == "limited_source_update"
    assert manifest["source_adequacy_status"] == "limited_source_update"
    assert "Limited-source update / May 27, 2026" in html
    assert "No publishable source-backed update / May 27, 2026" not in html
    assert "Daily briefing / May 27, 2026" not in html


def test_june_21_related_wishah_records_render_as_limited_source_update_with_clean_summary(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-06-21",
        [
            {
                "source_record_id": "gaza-2026-06-21-aljazeera-middle-east-1886929b0f50",
                "title": "'Kind, principled': Colleagues remember Gaza journalist killed by Israel",
                "url": "https://www.aljazeera.com/news/2026/6/21/kind-principled-palestinian-journalists-remember-slain-gaza-journalist?traffic_source=rss",
                "publisher": "Al Jazeera",
                "published_at": "2026-06-21T10:30:18+00:00",
                "retrieved_at": "2026-06-21T13:00:35.431407+00:00",
                "summary_or_snippet": "Ahmed Wishah is the 12th Al Jazeera journalist killed by Israel in Gaza since October 2023.",
                "source_type": "rss",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-2026-06-21-aljazeera-middle-east-51b8e7eeb9a2",
                "title": "Mother of Al Jazeera's Ahmed Wishah mourns his killing",
                "url": "https://www.aljazeera.com/video/newsfeed/2026/6/21/mother-of-al-jazeeras-ahmed-wishah-mourns-his-killing?traffic_source=rss",
                "publisher": "Al Jazeera",
                "published_at": "2026-06-21T10:01:27+00:00",
                "retrieved_at": "2026-06-21T13:00:35.431407+00:00",
                "summary_or_snippet": "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after. Israel killed him in Gaza.",
                "source_type": "rss",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
        ],
    )

    result = run_gaza_dispatch(work, "2026-06-21", from_manual_sources=True, dry_run=False, render=True, all_steps=False, allow_thin_edition=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-06-21" / "index.html")
    audio_result = write_gaza_audio_outputs(work, "2026-06-21", dry_run=False, tts_provider="none")
    transcript = audio_result.transcript_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["source_adequacy_status"] == "limited_source_update"
    assert "Limited-source update / June 21, 2026" in html
    assert "No publishable source-backed update / June 21, 2026" not in html
    assert "Today’s saved source records point to 2 reported developments." in html
    assert "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after Israel killed him in Gaza." in html
    assert "after. Israel killed him in Gaza" not in html
    assert html.count("<article><h3>") == 2
    assert html.count("Ahmed Wishah") >= 2
    assert "This is the moment the mother of Al Jazeera cameraman Ahmed Wishah first saw his body after Israel killed him in Gaza." in transcript
    assert "after. Israel killed him in Gaza" not in transcript


def test_source_quality_reports_and_manual_template_written_for_limited_source(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = []
    for idx in range(1, 5):
        rows.append(
            {
                "source_record_id": f"gaza-src-2026-05-28-{idx:03d}",
                "title": f"Gaza update {idx}",
                "url": f"https://example.com/gaza-limited-{idx}",
                "publisher": "Publisher A",
                "published_at": "2026-05-28T12:00:00Z",
                "retrieved_at": "2026-05-28T12:10:00Z",
                "summary_or_snippet": "Source-backed Gaza update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        )
    write_manual_sources(work, "2026-05-28", rows)
    result = run_gaza_dispatch(work, "2026-05-28", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    json_report = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-28" / "source_quality_report.json"
    md_report = work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-28" / "source_quality_report.md"
    template = work / "data" / "dispatches" / "gaza" / "sources" / "2026-05-28" / "manual_sources.template.json"
    assert json_report.exists()
    assert md_report.exists()
    assert template.exists()
    payload = json.loads(read(json_report))
    assert payload["source_quality_status"] == "limited_source_update"


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
    dispatch_manifest = work / "output" / "dispatches" / "gaza" / "editions" / "2026-04-29" / "edition_manifest.json"
    manifest_payload = json.loads(read(dispatch_manifest))
    assert result["ok"] is False
    assert not edition_dir.exists()
    assert manifest_payload["public_exposed"] is False
    assert manifest_payload["source_count"] == 0
    assert manifest_payload["story_count"] == 0


def test_zero_source_run_refuses_public_generation_and_keeps_failure_non_public(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-13", [])

    result = run_gaza_dispatch(work, "2026-05-13", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    site_edition = work / "output" / "site" / "gaza" / "editions" / "2026-05-13"
    dispatch_manifest = work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-13" / "edition_manifest.json"
    collection_report = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-13" / "collection_report.json"
    payload = json.loads(read(dispatch_manifest))
    report = json.loads(read(collection_report))
    assert result["ok"] is False
    assert result["source_adequacy_status"] == "no_publishable_source_backed_update"
    assert not site_edition.exists()
    assert payload["public_exposed"] is False
    assert payload["source_count"] == 0
    assert payload["story_count"] == 0
    assert report["no_story_credibility_decision"] == "no_candidates_found"
    assert report["providers_attempted_count"] == 1
    assert report["providers_successful_count"] == 0
    assert report["google_wrapper_count"] == 0
    assert report["canonical_publisher_url_count"] == 0
    assert report["providers_attempted"] == ["manual_sources_json"]
    assert report["provider_failures"][0]["source_id"] == "manual_sources_json"


def test_collection_report_includes_provider_attempted_success_counts(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-15")
    result = run_gaza_dispatch(work, "2026-05-15", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-15" / "collection_report.json"))
    assert result["ok"] is True
    assert report["providers_configured"] == ["manual_sources_json"]
    assert report["providers_attempted"] == ["manual_sources_json"]
    assert report["providers_successful"] == ["manual_sources_json"]
    assert report["final_story_count"] >= 1


def test_collection_report_carries_provider_rejection_diagnostics(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-15")
    ctx_path = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-15" / "source_collection_context.json"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(
            {
                "providers_configured": ["guardian-world"],
                "providers_attempted": ["guardian-world"],
                "providers_successful": [],
                "provider_failures": [],
                "rejected_by_reason": {"rejected_off_topic": 3, "rejected_missing_published_at": 2},
                "top_rejected_examples": [
                    {
                        "source_id": "guardian-world",
                        "title": "Live politics blog",
                        "url": "https://example.com/live",
                        "published_at": "",
                        "rejection_reason": "rejected_low_relevance",
                        "matched_terms": ["gaza"],
                        "relevance_band": "low",
                        "date_basis": "missing_published_at",
                    }
                ],
                "review_candidates": [
                    {
                        "source_id": "guardian-world",
                        "title": "Gaza update in live blog",
                        "url": "https://example.com/live",
                        "publisher": "Guardian",
                        "published_at": "",
                        "rejection_reason": "rejected_weak_date_basis",
                        "matched_terms": ["gaza"],
                        "relevance_band": "low",
                        "date_basis": "weak_date_basis",
                        "summary_or_snippet": "Possible relevance",
                    }
                ],
                "provider_diagnostics": [
                    {
                        "source_id": "guardian-world",
                        "raw_items": 10,
                        "items_with_gaza_terms": 3,
                        "items_with_palestine_terms": 2,
                        "items_in_date_window": 4,
                        "accepted": 1,
                        "rejected_counts": {"rejected_low_relevance": 3},
                        "most_common_rejection_reasons": [{"reason": "rejected_low_relevance", "count": 3}],
                        "top_rejected_examples": [],
                    }
                ],
                "stage_counts": {"raw_candidates": 10, "providers_attempted": 1, "providers_successful": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_gaza_dispatch(work, "2026-05-15", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-15" / "collection_report.json"))
    assert result["ok"] is True
    assert report["rejected_off_topic"] == 3
    assert report["rejected_weak_date"] == 2
    assert len(report["top_rejected_examples"]) == 1
    assert len(report["review_candidates"]) == 1
    assert report["provider_diagnostics"][0]["items_with_gaza_terms"] == 3


def test_collection_report_tracks_source_window_and_later_same_day_updates(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "gaza-src-2026-06-20-001",
            "title": "Gaza aid access update",
            "url": "https://example.com/gaza-aid-access-1",
            "publisher": "Reuters",
            "published_at": "2026-06-20T08:15:00Z",
            "retrieved_at": "2026-06-20T08:20:00Z",
            "summary_or_snippet": "Aid access conditions were updated in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-06-20-002",
            "title": "Gaza hospital access update",
            "url": "https://example.com/gaza-hospital-access-2",
            "publisher": "BBC News",
            "published_at": "2026-06-20T12:05:00Z",
            "retrieved_at": "2026-06-20T15:40:00Z",
            "summary_or_snippet": "Hospital access conditions in Gaza were updated later the same day.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, "2026-06-20", rows)
    result = run_gaza_dispatch(work, "2026-06-20", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-06-20" / "collection_report.json"))
    manifest = json.loads(read(work / "output" / "dispatches" / "gaza" / "editions" / "2026-06-20" / "edition_manifest.json"))
    run_manifest = json.loads(read(work / "output" / "test-backups" / "gaza" / "2026-06-20" / "run_manifest.json"))
    assert result["ok"] is True
    assert report["scheduled_run_local_time"]
    assert report["source_window_start_utc"] == "2026-06-20T08:15:00Z"
    assert report["source_window_end_utc"] == "2026-06-20T12:05:00Z"
    assert report["first_source_retrieved_at"] == "2026-06-20T08:20:00Z"
    assert report["last_source_retrieved_at"] == "2026-06-20T15:40:00Z"
    assert report["contains_later_same_day_update"] is True
    assert report["later_same_day_update_count"] == 1
    assert len(report["retrieval_batches"]) == 2
    assert manifest["contains_later_same_day_update"] is True
    assert run_manifest["contains_later_same_day_update"] is True


def test_collection_report_does_not_flag_later_same_day_update_for_single_batch(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "gaza-src-2026-06-20-101",
            "title": "Gaza aid access update",
            "url": "https://example.com/gaza-aid-access-101",
            "publisher": "Reuters",
            "published_at": "2026-06-20T08:15:00Z",
            "retrieved_at": "2026-06-20T08:20:00Z",
            "summary_or_snippet": "Aid access conditions were updated in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-06-20-102",
            "title": "Gaza hospital access update",
            "url": "https://example.com/gaza-hospital-access-102",
            "publisher": "BBC News",
            "published_at": "2026-06-20T10:05:00Z",
            "retrieved_at": "2026-06-20T08:20:00Z",
            "summary_or_snippet": "Hospital access conditions in Gaza were updated in the same batch.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, "2026-06-20", rows)
    result = run_gaza_dispatch(work, "2026-06-20", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-06-20" / "collection_report.json"))
    assert result["ok"] is True
    assert report["contains_later_same_day_update"] is False
    assert report["later_same_day_update_count"] == 0
    assert len(report["retrieval_batches"]) == 1


def test_zero_story_run_refuses_public_generation(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    repeated = [
        {
            "source_record_id": "gaza-src-001",
            "title": "Repeat source title",
            "url": "https://example.com/repeat",
            "publisher": "Example Desk",
            "published_at": "2026-05-10T08:00:00+00:00",
            "retrieved_at": "2026-05-10T08:00:00+00:00",
            "summary_or_snippet": "repeat",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        }
    ]
    write_manual_sources(work, "2026-05-10", repeated)
    run_gaza_dispatch(work, "2026-05-10", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    write_manual_sources(work, "2026-05-11", repeated)

    result = run_gaza_dispatch(work, "2026-05-11", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    site_edition = work / "output" / "site" / "gaza" / "editions" / "2026-05-11"
    assert result["ok"] is False
    assert not site_edition.exists()


def test_review_candidates_never_create_public_story(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-16", [])
    ctx_path = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-16" / "source_collection_context.json"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(
            {
                "providers_configured": ["guardian-world"],
                "providers_attempted": ["guardian-world"],
                "providers_successful": [],
                "provider_failures": [],
                "review_candidates": [
                    {
                        "source_id": "guardian-world",
                        "title": "Gaza live update",
                        "url": "https://example.com/live",
                        "publisher": "Guardian",
                        "published_at": "",
                        "rejection_reason": "rejected_low_relevance",
                        "matched_terms": ["gaza"],
                        "relevance_band": "low",
                        "date_basis": "missing_published_at",
                        "summary_or_snippet": "candidate",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_gaza_dispatch(work, "2026-05-16", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is False
    assert not (work / "output" / "site" / "gaza" / "editions" / "2026-05-16" / "index.html").exists()


def test_archive_rss_latest_and_shared_records(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    backup_root = work / "output" / "test-backups" / "gaza"
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", backup_root)
    write_manual_sources(work, "2026-04-30")
    write_manual_sources(
        work,
        "2026-05-01",
        [
            {
                "source_record_id": "gaza-src-2026-05-01-001",
                "title": "UN agency reports new aid convoy access in Gaza",
                "url": "https://valid.test/gaza-2026-05-01-aid",
                "publisher": "UN OCHA",
                "published_at": "2026-05-01T12:00:00Z",
                "retrieved_at": "2026-05-01T18:00:00Z",
                "summary_or_snippet": "Distinct source-backed update for 2026-05-01.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )

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
    first = run_gaza_dispatch(
        work,
        "2026-05-10",
        from_manual_sources=True,
        dry_run=False,
        render=False,
        all_steps=True,
        allow_thin_edition=True,
    )
    assert (work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json").exists()
    write_manual_sources(work, "2026-05-11", repeated)

    result = run_gaza_dispatch(work, "2026-05-11", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    dedupe_report = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-11" / "dedupe_report.json"
    report = json.loads(dedupe_report.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "No new source-backed Gaza developments after cross-edition dedupe" in " ".join(result["errors"])
    assert report["suppressed_candidate_count"] >= 1


def test_dedupe_report_is_rewritten_for_current_run(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-10")
    dedupe_path = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "dedupe_report.json"
    dedupe_path.parent.mkdir(parents=True, exist_ok=True)
    dedupe_path.write_text(json.dumps({"sentinel": "old"}), encoding="utf-8")

    result = run_gaza_dispatch(work, "2026-05-10", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    payload = json.loads(dedupe_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert payload.get("edition_date") == "2026-05-10"
    assert "sentinel" not in payload


def test_preserved_seed_path_runs_cross_edition_dedupe_and_refuses_repeated_publish(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    prior_edition = work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-11"
    prior_edition.mkdir(parents=True, exist_ok=True)
    repeated = [
        {
            "source_record_id": "gaza-src-prior-001",
            "title": "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza",
            "url": "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF",
            "canonical_url": "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF",
            "publisher": "The New York Times",
            "published_at": "",
            "retrieved_at": "2026-05-11T12:00:00+00:00",
            "category_hint": "humanitarian",
        },
        {
            "source_record_id": "gaza-src-prior-002",
            "title": "U.S. to close Israel command center overseeing Gaza truce as Trump plan stalls",
            "url": "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE",
            "canonical_url": "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE",
            "publisher": "Haaretz",
            "published_at": "",
            "retrieved_at": "2026-05-11T12:01:00+00:00",
            "category_hint": "humanitarian",
        },
        {
            "source_record_id": "gaza-src-prior-003",
            "title": "Court extends detention of 2 Gaza flotilla activists accused of Hamas links",
            "url": "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR",
            "canonical_url": "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR",
            "publisher": "The Times of Israel",
            "published_at": "",
            "retrieved_at": "2026-05-11T12:02:00+00:00",
            "category_hint": "humanitarian",
        },
    ]
    (prior_edition / "sources_manifest.json").write_text(json.dumps(repeated, indent=2), encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-12")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    dedupe_report = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-12" / "dedupe_report.json"
    report = json.loads(dedupe_report.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition." in result["errors"]
    assert report["suppressed_candidate_count"] == 3
    assert all(
        item["matched_key_type"] in {"canonical_url", "normalized_url", "publisher_title", "title_fingerprint", "claim_fingerprint"}
        for item in report["suppressed_candidates"]
    )
    assert not (work / "output" / "site" / "gaza" / "editions" / "2026-05-12" / "index.html").exists()


def test_suppressed_gaza_editions_are_not_listed_in_archive_index_or_rss(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    repeated = [
        {
            "source_record_id": "gaza-src-001",
            "title": "Repeat source title",
            "url": "https://example.com/repeat",
            "publisher": "Example Desk",
            "published_at": "2026-05-10T08:00:00+00:00",
            "retrieved_at": "2026-05-10T08:00:00+00:00",
            "summary_or_snippet": "repeat",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        }
    ]
    write_manual_sources(work, "2026-05-10", repeated)
    write_manual_sources(work, "2026-05-11", repeated)
    run_gaza_dispatch(
        work,
        "2026-05-10",
        from_manual_sources=True,
        dry_run=False,
        render=False,
        all_steps=True,
        allow_thin_edition=True,
    )
    run_gaza_dispatch(work, "2026-05-11", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    build_result = build_site(work, dry_run=False, backup_root=work / "backup")
    assert build_result["ok"] is True

    archive = read(work / "output" / "site" / "gaza" / "archive.html")
    index = read(work / "output" / "site" / "gaza" / "index.html")
    rss = read(work / "output" / "site" / "gaza" / "rss.xml")
    assert "2026-05-10" in archive
    assert "2026-05-10" in index
    assert "2026-05-10" in rss
    assert "2026-05-11" not in archive
    assert "2026-05-11" not in index
    assert "2026-05-11" not in rss


def test_valid_source_backed_edition_remains_public(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-09")
    result = run_gaza_dispatch(work, "2026-05-09", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    assert (work / "output" / "site" / "gaza" / "editions" / "2026-05-09" / "index.html").exists()


def test_zero_source_or_zero_story_gaza_editions_are_not_listed(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")

    write_manual_sources(work, "2026-05-09")
    run_gaza_dispatch(work, "2026-05-09", from_manual_sources=True, dry_run=False, render=False, all_steps=True)

    bad_dir = work / "output" / "site" / "gaza" / "editions" / "2026-05-11"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "index.html").write_text("<html>bad</html>", encoding="utf-8")
    (bad_dir / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (bad_dir / "curation_manifest.json").write_text("[]", encoding="utf-8")
    (bad_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "gaza",
                "edition_date": "2026-05-11",
                "source_count": 0,
                "story_count": 0,
                "errors": [
                    "No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition."
                ],
            }
        ),
        encoding="utf-8",
    )

    build_result = build_site(work, dry_run=False, backup_root=work / "backup")
    assert build_result["ok"] is True

    archive = read(work / "output" / "site" / "gaza" / "archive.html")
    index = read(work / "output" / "site" / "gaza" / "index.html")
    rss = read(work / "output" / "site" / "gaza" / "rss.xml")
    assert "2026-05-09" in archive
    assert "2026-05-09" in index
    assert "2026-05-09" in rss
    assert "2026-05-11" not in archive
    assert "2026-05-11" not in index
    assert "2026-05-11" not in rss


def test_rendered_gaza_html_strips_escaped_feed_markup(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-14",
        [
            {
                "source_record_id": "gaza-src-2026-05-14-001",
                "title": "UNRWA archive update for Gaza",
                "url": "https://www.theguardian.com/world/2026/may/14/unrwa-gaza-aid-access",
                "publisher": "The Guardian",
                "published_at": "2026-05-14T12:00:00Z",
                "retrieved_at": "2026-05-14T18:00:00Z",
                "summary_or_snippet": "&lt;p&gt;Aid note&lt;/p&gt; &lt;a href='https://x'&gt;Continue reading...&lt;/a&gt; &#x27;quoted&#x27;",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-14", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-14" / "index.html")
    assert result["ok"] is True
    assert "&lt;p&gt;" not in html
    assert "&lt;/p&gt;" not in html
    assert "&lt;a href=" not in html
    assert "Continue reading..." not in html


def test_manual_sources_apply_topical_relevance_filter(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-14",
        [
            {
                "source_record_id": "gaza-src-2026-05-14-001",
                "title": "Gaza sisters win prize for turning rubble into reusable bricks",
                "url": "https://www.bbc.com/news/articles/ce8p7vngmp3o",
                "publisher": "BBC News",
                "published_at": "2026-05-14T12:00:00Z",
                "retrieved_at": "2026-05-14T18:00:00Z",
                "summary_or_snippet": "Families in Gaza are reusing rubble to rebuild housing.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-14-002",
                "title": "Taylor vows to run coal long and hard and scrap EV concessions in budget reply",
                "url": "https://www.theguardian.com/australia-news/live/2026/may/14/budget-reply-live-blog",
                "publisher": "The Guardian",
                "published_at": "2026-05-14T12:30:00Z",
                "retrieved_at": "2026-05-14T18:01:00Z",
                "summary_or_snippet": "Live blog includes incidental mention of Gaza among unrelated Australia politics updates.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-14-003",
                "title": "The secret mission to rescue the UNâ€™s vital Palestinian refugee archive",
                "url": "https://www.theguardian.com/world/2026/may/14/secret-mission-palestinian-refugee-archive-unrwa-israel",
                "publisher": "The Guardian",
                "published_at": "2026-05-14T13:00:00Z",
                "retrieved_at": "2026-05-14T18:02:00Z",
                "summary_or_snippet": "UNRWA documents from Gaza and East Jerusalem were moved for preservation.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-14", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-14" / "index.html")
    assert result["ok"] is True
    assert "Taylor vows to run coal" not in html
    assert "Gaza sisters win prize for turning rubble into reusable bricks" in html
    assert "secret mission to rescue the UN" in html


def test_palestinian_developments_section_and_gaza_top_story(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-15",
        [
            {
                "source_record_id": "gaza-src-001",
                "title": "Gaza hospitals face acute aid shortages after airstrikes",
                "url": "https://example.com/gaza-hospitals",
                "publisher": "BBC News",
                "published_at": "2026-05-15T10:00:00Z",
                "retrieved_at": "2026-05-15T12:00:00Z",
                "summary_or_snippet": "Core Gaza humanitarian impact update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-002",
                "title": "Settler violence rises in West Bank communities",
                "url": "https://example.com/west-bank-settler-violence",
                "publisher": "The Guardian",
                "published_at": "2026-05-15T10:30:00Z",
                "retrieved_at": "2026-05-15T12:10:00Z",
                "summary_or_snippet": "Palestinian civil rights and security impact.",
                "source_type": "news",
                "region_scope": "Palestine",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
                {
                    "source_record_id": "gaza-src-003",
                    "title": "East Jerusalem hospital access restrictions affect Palestinian patients",
                    "url": "https://example.com/east-jerusalem-rights",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-05-15T11:00:00Z",
                    "retrieved_at": "2026-05-15T12:20:00Z",
                    "summary_or_snippet": "Health and mobility restrictions in East Jerusalem affect Palestinian care.",
                    "source_type": "news",
                    "region_scope": "Palestine",
                    "category_hint": "rights",
                    "reliability_tier": "reported-public-source",
                },
            {
                "source_record_id": "gaza-src-004",
                "title": "UNRWA warns Palestinian refugee services face new cuts",
                "url": "https://example.com/unrwa-refugee-services",
                "publisher": "Reuters",
                "published_at": "2026-05-15T11:30:00Z",
                "retrieved_at": "2026-05-15T12:30:00Z",
                "summary_or_snippet": "Refugee and aid service impacts.",
                "source_type": "news",
                "region_scope": "Palestine",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-005",
                "title": "Nakba memory and right of return debate gains legal attention",
                "url": "https://example.com/nakba-right-of-return",
                "publisher": "AP",
                "published_at": "2026-05-15T12:00:00Z",
                "retrieved_at": "2026-05-15T12:40:00Z",
                "summary_or_snippet": "Legal/accountability discourse affecting Palestinian rights.",
                "source_type": "news",
                "region_scope": "Palestine",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-15", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-15" / "index.html")
    assert "<h2>At A Glance</h2>" in html
    assert "<h2>Today" in html and "Read</h2>" in html
    assert "Limited-source update / May 15, 2026" in html
    assert "Generated from saved source records available for May 15, 2026." in html
    assert "<h2>Civilian Harm and Access</h2>" in html
    assert "<h2>International Law and Diplomacy</h2>" in html
    assert "<h2>Source Mix</h2>" in html
    assert "<h2>Source Note</h2>" in html
    assert "Source mix: 5 stories from 5 publishers. Source coverage may be uneven." in html
    assert '<a href="/gaza/archive.html">Gaza archive</a> | <a href="/">Dispatches home</a>' in html
    assert "Gaza hospitals face acute aid shortages after airstrikes" in html
    assert "Settler violence rises in West Bank communities" in html
    assert "East Jerusalem hospital access restrictions affect Palestinian patients" in html
    assert "UNRWA warns Palestinian refugee services face new cuts" in html
    assert "Nakba memory and right of return debate gains legal attention" in html
    # Verify visible links are present for Palestinian developments.
    assert 'href="https://example.com/west-bank-settler-violence"' in html
    assert 'href="https://example.com/east-jerusalem-rights"' in html
    curation = json.loads(read(work / "output" / "dispatches" / "gaza" / "editions" / "2026-05-15" / "curation_manifest.json"))
    assert any(item.get("category") == "palestinian_development" for item in curation)
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-15" / "collection_report.json"))
    assert report["core_gaza_count"] >= 1
    assert report["palestinian_development_count"] >= 1


def test_todays_read_conservative_with_single_story_and_metadata_omits_missing_fields(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-22",
        [
            {
                "source_record_id": "gaza-src-2026-05-22-001",
                "title": "Single update on aid access corridor timing",
                "url": "https://example.com/gaza-aid-corridor",
                "publisher": "Example News",
                "published_at": "2026-05-22T10:00:00Z",
                "retrieved_at": "2026-05-22T11:00:00Z",
                "summary_or_snippet": "Aid access timing changed after checkpoint delays.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-22", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-22" / "index.html")
    assert "<h2>Today" in html and "Read</h2>" in html
    assert "Today’s saved source records point to 1 reported development." in html
    assert "Aid access timing changed after checkpoint delays." in html
    assert "Example News" in html
    assert "humanitarian" in html
    assert "Gaza" in html
    assert "May 22, 2026" in html
    assert "<strong>Context:</strong>" in html

    write_manual_sources(
        work,
        "2026-05-23",
        [
            {
                "source_record_id": "gaza-src-2026-05-23-001",
                "title": "Malformed date source metadata row",
                "url": "https://example.com/sparse-meta",
                "publisher": "Example News",
                "published_at": "unknown",
                "retrieved_at": "2026-05-23T11:00:00Z",
                "summary_or_snippet": "A minimal source-backed summary with malformed source date text.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result_two = run_gaza_dispatch(
        work,
        "2026-05-23",
        from_manual_sources=True,
        dry_run=False,
        render=False,
        all_steps=True,
        allow_thin_edition=True,
    )
    assert result_two["ok"] is True
    html_two = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-23" / "index.html")
    assert "Example News" in html_two
    assert "humanitarian" in html_two
    assert "Gaza" in html_two
    assert "unknown" not in html_two


def test_manual_equatorial_guinea_asylum_story_rejected_as_no_palestinian_anchor(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-15",
        [
            {
                "source_record_id": "gaza-src-001",
                "title": "Gaza hospitals face acute aid shortages after airstrikes",
                "url": "https://example.com/gaza-hospitals",
                "publisher": "BBC News",
                "published_at": "2026-05-15T10:00:00Z",
                "retrieved_at": "2026-05-15T12:00:00Z",
                "summary_or_snippet": "Core Gaza humanitarian impact update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-002",
                "title": "UN pleads for Equatorial Guinea not to send US asylum seekers to their home countries",
                "url": "https://example.com/equatorial-guinea-asylum",
                "publisher": "Example",
                "published_at": "2026-05-15T10:10:00Z",
                "retrieved_at": "2026-05-15T12:10:00Z",
                "summary_or_snippet": "Generic asylum and refoulement case outside the publication scope.",
                "source_type": "news",
                "region_scope": "Global",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-15", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-15" / "index.html")
    assert "Gaza hospitals face acute aid shortages after airstrikes" in html
    assert "Equatorial Guinea" not in html


def test_2026_05_25_off_topic_liveblog_cannot_be_gaza_top_story_and_thin_blocks_without_override(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-25",
        [
            {
                "source_record_id": "gaza-src-2026-05-25-001",
                "title": "Liberal MP is first to be suspended from lower house in five years - as it happened",
                "url": "https://www.theguardian.com/australia-news/live/2026/may/25/liberal-mp-suspended-live",
                "publisher": "The Guardian",
                "published_at": "2026-05-25T10:00:00Z",
                "retrieved_at": "2026-05-25T12:00:00Z",
                "summary_or_snippet": "Australian politics live coverage with incidental Gaza references.",
                "source_type": "news",
                "region_scope": "Global",
                "category_hint": "politics",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-25-002",
                "title": "Court extends detention of Gaza flotilla activists",
                "url": "https://example.com/gaza-flotilla-detention",
                "publisher": "Example News",
                "published_at": "2026-05-25T11:00:00Z",
                "retrieved_at": "2026-05-25T12:10:00Z",
                "summary_or_snippet": "Gaza flotilla and activist return update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
        ],
    )

    blocked = run_gaza_dispatch(work, "2026-05-25", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert blocked["ok"] is False
    assert any("No substantive Gaza/Palestinian ground-development story cleared threshold" in err for err in blocked["errors"])
    assert not (work / "output" / "site" / "gaza" / "editions" / "2026-05-25" / "index.html").exists()

    allowed = run_gaza_dispatch(
        work,
        "2026-05-25",
        from_manual_sources=True,
        dry_run=False,
        render=False,
        all_steps=True,
        allow_thin_edition=True,
    )
    assert allowed["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-25" / "index.html")
    assert "Liberal MP is first to be suspended from lower house in five years - as it happened" not in html
    assert "Court extends detention of Gaza flotilla activists" in html


def test_source_diversity_report_written_with_stage_counts(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-20",
        [
            {
                "source_record_id": "gaza-src-2026-05-20-001",
                "title": "Aid access shifts after crossing review",
                "url": "https://example.com/aid-access-1",
                "publisher": "Publisher A",
                "published_at": "2026-05-20T09:00:00Z",
                "retrieved_at": "2026-05-20T10:00:00Z",
                "summary_or_snippet": "Aid access and convoy delays were reported in Gaza.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-20-002",
                "title": "Hospital supply pressure reported in Gaza",
                "url": "https://example.com/hospital-supply-2",
                "publisher": "Publisher B",
                "published_at": "2026-05-20T09:10:00Z",
                "retrieved_at": "2026-05-20T10:10:00Z",
                "summary_or_snippet": "Hospitals in Gaza reported supply pressure.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
                {
                    "source_record_id": "gaza-src-2026-05-20-003",
                    "title": "Gaza ceasefire talks continue with mediator statements",
                    "url": "https://example.com/ceasefire-talks-3",
                    "publisher": "Publisher C",
                    "published_at": "2026-05-20T09:20:00Z",
                    "retrieved_at": "2026-05-20T10:20:00Z",
                    "summary_or_snippet": "Mediators discussed ceasefire terms and Gaza aid access.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "diplomacy",
                    "reliability_tier": "reported-public-source",
                },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-20", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(
        read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-20" / "source_diversity_report.json")
    )
    assert report["date"] == "2026-05-20"
    assert report["raw_source_count"] == 3
    assert report["normalized_source_count"] == 2
    assert report["curated_story_count"] == 2
    assert report["rendered_story_count"] == 2
    assert report["unique_raw_publishers"] == 3
    assert report["unique_rendered_publishers"] == 2
    assert report["source_diversity_warning"] is False
    assert report["publisher_dominance_warning"] is False
    assert report["warning_severity"] == "info"


def test_source_diversity_warning_triggers_for_five_stories_two_publishers(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    rows = [
        {
            "source_record_id": "gaza-src-2026-05-21-001",
            "title": "Gaza hospital fuel deliveries delayed",
            "url": "https://example.com/gaza-hospital-fuel",
            "publisher": "Publisher A",
            "published_at": "2026-05-21T01:00:00Z",
            "retrieved_at": "2026-05-21T01:10:00Z",
            "summary_or_snippet": "Hospitals reported delayed fuel deliveries in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-05-21-002",
            "title": "Gaza water pumping interruptions expanded",
            "url": "https://example.com/gaza-water-pumping",
            "publisher": "Publisher A",
            "published_at": "2026-05-21T02:00:00Z",
            "retrieved_at": "2026-05-21T02:10:00Z",
            "summary_or_snippet": "Water pumping interruptions expanded in Gaza districts.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-05-21-003",
            "title": "Gaza aid convoy checkpoint backlog reported",
            "url": "https://example.com/gaza-convoy-backlog",
            "publisher": "Publisher A",
            "published_at": "2026-05-21T03:00:00Z",
            "retrieved_at": "2026-05-21T03:10:00Z",
            "summary_or_snippet": "Aid convoy checkpoint backlogs were reported in Gaza.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-05-21-004",
            "title": "Gaza court filing referenced detention case",
            "url": "https://example.com/gaza-court-detention",
            "publisher": "Publisher B",
            "published_at": "2026-05-21T04:00:00Z",
            "retrieved_at": "2026-05-21T04:10:00Z",
            "summary_or_snippet": "A detention-related court filing referenced Gaza developments.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "legal",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-src-2026-05-21-005",
            "title": "Gaza ceasefire mediator statement updated",
            "url": "https://example.com/gaza-mediator-update",
            "publisher": "Publisher B",
            "published_at": "2026-05-21T05:00:00Z",
            "retrieved_at": "2026-05-21T05:10:00Z",
            "summary_or_snippet": "Mediator statements on Gaza ceasefire terms were updated.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "diplomacy",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, "2026-05-21", rows)
    result = run_gaza_dispatch(work, "2026-05-21", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(
        read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-21" / "source_diversity_report.json")
    )
    assert report["rendered_story_count"] >= 4
    assert report["unique_rendered_publishers"] == 2
    assert report["source_diversity_warning"] is True
    assert report["warning_severity"] == "warning"
    assert any("rendered_story_count>=4" in reason for reason in report["warning_reason"])


def test_dominance_warning_without_low_diversity_for_seven_stories_three_publishers(monkeypatch):
    raw_sources = [{"publisher": "Publisher A"} for _ in range(5)] + [{"publisher": "Publisher B"}, {"publisher": "Publisher C"}]
    normalized_sources = list(raw_sources)
    rendered_stories = [{"publisher_names": ["Publisher A"]} for _ in range(5)] + [{"publisher_names": ["Publisher B"]}, {"publisher_names": ["Publisher C"]}]
    report = build_source_diversity_report(
        "2026-05-27",
        raw_sources=raw_sources,
        normalized_sources=normalized_sources,
        curated_stories=rendered_stories,
        rendered_stories=rendered_stories,
        collection_report={},
        cross_edition_report={},
        stage_drop_diagnostics={},
    )
    assert report["rendered_story_count"] == 7
    assert report["unique_rendered_publishers"] == 3
    assert report["source_diversity_warning"] is False
    assert report["publisher_dominance_warning"] is True
    assert report["warning_severity"] == "info"
    assert "single_publisher_supplies_more_than_60_percent_of_rendered_stories" in report["warning_reason"]


def test_serious_warning_for_five_stories_one_publisher(monkeypatch):
    raw_sources = [{"publisher": "Publisher A"} for _ in range(5)]
    normalized_sources = list(raw_sources)
    rendered_stories = [{"publisher_names": ["Publisher A"]} for _ in range(5)]
    report = build_source_diversity_report(
        "2026-05-28",
        raw_sources=raw_sources,
        normalized_sources=normalized_sources,
        curated_stories=rendered_stories,
        rendered_stories=rendered_stories,
        collection_report={},
        cross_edition_report={},
        stage_drop_diagnostics={},
    )
    assert report["rendered_story_count"] == 5
    assert report["unique_rendered_publishers"] == 1
    assert report["source_diversity_warning"] is True
    assert report["publisher_dominance_warning"] is True
    assert report["warning_severity"] == "serious"


def test_curation_prefers_similarly_strong_story_from_new_publisher():
    now = "2026-05-25T12:00:00Z"
    sources = [
        {
            "source_record_id": "src-1",
            "title": "Gaza hospital strike and aid disruption",
            "summary_or_snippet": "Hospital and aid disruptions in Gaza were reported.",
            "url": "https://example.com/src-1",
            "publisher": "Publisher A",
            "category_hint": "humanitarian",
            "region_scope": "Gaza",
            "candidate_score": 95,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
        {
            "source_record_id": "src-2",
            "title": "Gaza aid corridor delay update",
            "summary_or_snippet": "Aid corridor delays in Gaza were reported.",
            "url": "https://example.com/src-2",
            "publisher": "Publisher A",
            "category_hint": "humanitarian",
            "region_scope": "Gaza",
            "candidate_score": 94,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
        {
            "source_record_id": "src-3",
            "title": "Gaza ceasefire mediator update",
            "summary_or_snippet": "Ceasefire and access negotiations were reported.",
            "url": "https://example.com/src-3",
            "publisher": "Publisher B",
            "category_hint": "diplomacy",
            "region_scope": "Gaza",
            "candidate_score": 93,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
    ]
    stories, _, _ = curate_stories(sources, "2026-05-25", now)
    assert stories[0]["publisher_names"][0] == "Publisher A"
    assert stories[1]["publisher_names"][0] == "Publisher B"


def test_curation_does_not_include_weak_unsupported_story_for_diversity():
    now = "2026-05-25T12:00:00Z"
    sources = [
        {
            "source_record_id": "src-1",
            "title": "Gaza hospital aid update",
            "summary_or_snippet": "Hospital and aid access in Gaza were reported.",
            "url": "https://example.com/src-1",
            "publisher": "Publisher A",
            "category_hint": "humanitarian",
            "region_scope": "Gaza",
            "candidate_score": 95,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
        {
            "source_record_id": "src-2",
            "title": "Domestic football commentary roundup",
            "summary_or_snippet": "No Gaza or Palestinian development context.",
            "url": "https://example.com/src-2",
            "publisher": "Publisher Z",
            "category_hint": "sports",
            "region_scope": "Global",
            "candidate_score": 94,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
    ]
    stories, rejected, _ = curate_stories(sources, "2026-05-25", now)
    titles = [story["title"] for story in stories]
    assert "Gaza hospital aid update" in titles
    assert "Domestic football commentary roundup" not in titles
    assert any(row.get("action") == "rejected" for row in rejected)


def test_opinion_url_sources_are_marked_excluded_from_story_selection():
    now = "2026-05-25T12:00:00Z"
    records = [
        {
            "source_record_id": "src-opinion",
            "title": "Opinion: Gaza aid access debate is still unresolved",
            "summary_or_snippet": "An opinion column on Gaza aid access.",
            "url": "https://example.com/opinion/gaza-aid-access-debate",
            "publisher": "Example News",
            "published_at": "2026-05-25T10:00:00Z",
            "retrieved_at": now,
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "commentary",
            "reliability_tier": "reported-public-source",
        }
    ]
    normalized, warnings, errors = normalize_sources(records, "2026-05-25", now)
    assert errors == []
    assert warnings == []
    assert normalized[0]["story_selection_excluded_reason"] == "opinion/editorial/commentary source excluded from Gaza story selection"
    assert normalized[0]["used_in_story_ids"] == []
    stories, rejected, _ = curate_stories(normalized, "2026-05-25", now)
    assert stories == []
    assert rejected[0]["reason"] == "opinion/editorial/commentary source excluded from Gaza story selection"


def test_labeled_context_opinion_url_is_retained_for_metadata_but_not_story_selection():
    now = "2026-05-25T12:00:00Z"
    records = [
        {
            "source_record_id": "src-context-opinion",
            "title": "Opinion: Gaza aid access debate is still unresolved",
            "summary_or_snippet": "An opinion column on Gaza aid access.",
            "url": "https://example.com/opinion/gaza-aid-access-debate",
            "publisher": "Example News",
            "published_at": "2026-05-25T10:00:00Z",
            "retrieved_at": now,
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "commentary",
            "reliability_tier": "reported-public-source",
            "attribution_mode": "gaza_adjacent_context",
            "claim_status": "gaza_adjacent_context",
        }
    ]
    normalized, warnings, errors = normalize_sources(records, "2026-05-25", now)
    assert errors == []
    assert warnings == []
    assert normalized[0]["story_selection_excluded_reason"] == "opinion/editorial/commentary source retained as labeled context"
    assert normalized[0]["used_in_story_ids"] == []
    stories, rejected, _ = curate_stories(normalized, "2026-05-25", now)
    assert stories == []
    assert rejected[0]["reason"] == "opinion/editorial/commentary source retained as labeled context"


def test_bbc_style_valid_raw_record_survives_normalization():
    now = "2026-05-25T12:00:00Z"
    records = [
        {
            "source_record_id": "bbc-raw-001",
            "title": "Gaza flotilla activists allege abuse by Israeli forces while detained",
            "url": "https://www.bbc.com/news/articles/cglp5z63k9no?at_medium=RSS&at_campaign=rss",
            "publisher": "BBC News",
            "published_at": "2026-05-23T08:10:51+00:00",
            "retrieved_at": now,
            "summary_or_snippet": "Detainees described abuse allegations while in detention after the flotilla interception.",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        }
    ]
    normalized, warnings, errors = normalize_sources(records, "2026-05-25", now)
    assert errors == []
    assert len(normalized) == 1
    assert normalized[0]["publisher"] == "BBC News"
    assert warnings == []


def test_invalid_raw_record_rejected_with_clear_reason():
    now = "2026-05-25T12:00:00Z"
    records = [
        {
            "source_record_id": "bad-001",
            "title": "Gaza source without URL",
            "url": "",
            "publisher": "Example",
            "published_at": "2026-05-25T08:10:51+00:00",
            "retrieved_at": now,
            "summary_or_snippet": "Missing URL should fail normalization.",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        }
    ]
    normalized, _warnings, errors = normalize_sources(records, "2026-05-25", now)
    assert normalized == []
    assert any("missing required fields: url" in err for err in errors)


def test_source_diversity_report_includes_stage_drop_reasons(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    repeated = [
        {
            "source_record_id": "shared-001",
            "title": "Gaza hospital fuel warning",
            "url": "https://example.com/gaza-shared-fuel",
            "publisher": "BBC News",
            "published_at": "2026-05-24T08:00:00+00:00",
            "retrieved_at": "2026-05-24T08:10:00+00:00",
            "summary_or_snippet": "Fuel shortages reported in Gaza hospitals.",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        }
    ]
    write_manual_sources(work, "2026-05-24", repeated)
    first = run_gaza_dispatch(work, "2026-05-24", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert first["ok"] is True
    write_manual_sources(
        work,
        "2026-05-25",
        repeated
        + [
            {
                "source_record_id": "guardian-live-001",
                "title": "Liberal MP is first to be suspended from lower house in five years - as it happened",
                "url": "https://www.theguardian.com/australia-news/live/2026/may/25/teals-new-party-gaza-flotilla-activists",
                "publisher": "The Guardian",
                "published_at": "2026-05-25T08:18:33+00:00",
                "retrieved_at": "2026-05-25T09:00:00+00:00",
                "summary_or_snippet": "Australian politics live blog with incidental Gaza references.",
                "source_type": "rss",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "alj-main-001",
                "title": "Gaza hospital strike and displacement update",
                "url": "https://example.com/alj-main-001",
                "publisher": "Al Jazeera",
                "published_at": "2026-05-25T11:00:00+00:00",
                "retrieved_at": "2026-05-25T11:10:00+00:00",
                "summary_or_snippet": "Displacement and strike impacts were reported in Gaza.",
                "source_type": "rss",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    second = run_gaza_dispatch(
        work,
        "2026-05-25",
        from_manual_sources=True,
        dry_run=False,
        render=False,
        all_steps=True,
        allow_thin_edition=True,
    )
    assert second["ok"] is True
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-25" / "source_diversity_report.json"))
    assert "publisher_breakdown_by_source_stage" in report
    stage = report["publisher_breakdown_by_source_stage"]
    assert "manual_sources_json" in stage
    assert stage["manual_sources_json"]["raw_items"] == 3
    assert "stage_explanation" in stage["manual_sources_json"]
    assert "accepted_records_present" in (stage["manual_sources_json"].get("stage_explanation") or "")
    drops = report["stage_drop_diagnostics"]
    assert any(row["source_record_id"] == "shared-001" and row["reason"] == "cross_edition_duplicate" for row in drops["normalization_drops"])
    assert any(row["source_record_id"] == "guardian-live-001" and "incidental_liveblog_or_domestic_politics_without_ground_development" in str(row["reason"]) for row in drops["curation_exclusions"])


def test_source_diversity_report_includes_skipped_source_explanations(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(work, "2026-05-26")
    ctx_path = work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-26" / "source_collection_context.json"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(
            {
                "provider_diagnostics": [
                    {"source_id": "ocha-opt-updates", "source_state": "disabled", "status": "skipped", "reason": "disabled:dead endpoint (historical 404)"},
                    {"source_id": "unrwa-updates", "source_state": "diagnostics_only", "status": "skipped", "reason": "diagnostics_only:blocked endpoint"},
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_gaza_dispatch(work, "2026-05-26", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert result["ok"] is True
    report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / "2026-05-26" / "source_diversity_report.json"))
    stage = report["publisher_breakdown_by_source_stage"]
    assert "disabled_by_config" in (stage["ocha-opt-updates"]["stage_explanation"] or "")
    assert "diagnostics_only_by_config" in (stage["unrwa-updates"]["stage_explanation"] or "")


def test_story_cleanup_repairs_missing_sentence_boundaries_and_today_read_uses_cleaned_text():
    now = "2026-05-28T12:00:00Z"
    edition_date = "2026-05-28"
    sources = [
        {
            "source_record_id": "gaza-src-clean-001",
            "title": "Aid crossing conditions changed",
            "summary_or_snippet": "accusations of antisemitism For a brief period access improved in Gaza.",
            "url": "https://example.com/clean-1",
            "publisher": "Publisher A",
            "category_hint": "humanitarian",
            "region_scope": "Gaza",
            "candidate_score": 95,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
        {
            "source_record_id": "gaza-src-clean-002",
            "title": "Minister statement drew international criticism",
            "summary_or_snippet": "ethnic cleansing Israel's defence minister said operations would continue.",
            "url": "https://example.com/clean-2",
            "publisher": "Publisher B",
            "category_hint": "conflict",
            "region_scope": "Gaza",
            "candidate_score": 94,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
    ]
    stories, _rejected, _diag = curate_stories(sources, edition_date, now)
    adequacy = {
        "status": "limited_source_update",
        "label": "Limited-source update",
        "warnings": ["test warning"],
        "all_stories_one_publisher": False,
        "publishers": ["Publisher A", "Publisher B"],
    }
    html = render_gaza_edition(edition_date, stories, sources, adequacy)
    assert html.count("accusations of antisemitism. For a brief period access improved in Gaza.") >= 2
    assert html.count("ethnic cleansing. Israel&#x27;s defence minister said operations would continue.") >= 2
    assert "accusations of antisemitism For a brief period" not in html
    assert "ethnic cleansing Israel&#x27;s defence minister" not in html


def test_story_cleanup_drops_paragraph_that_exactly_repeats_headline():
    now = "2026-05-28T12:00:00Z"
    edition_date = "2026-05-28"
    title = "Court extends detention of 2 Gaza flotilla activists"
    sources = [
        {
            "source_record_id": "gaza-src-clean-003",
            "title": title,
            "summary_or_snippet": title,
            "url": "https://example.com/clean-3",
            "publisher": "Publisher A",
            "category_hint": "legal",
            "region_scope": "Gaza",
            "candidate_score": 95,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
        {
            "source_record_id": "gaza-src-clean-004",
            "title": "Aid corridor delays reported",
            "summary_or_snippet": "Aid corridor delays were reported across Gaza.",
            "url": "https://example.com/clean-4",
            "publisher": "Publisher B",
            "category_hint": "humanitarian",
            "region_scope": "Gaza",
            "candidate_score": 93,
            "ranking_reasons": ["test"],
            "candidate_score_breakdown": {},
        },
    ]
    stories, rejected, _diag = curate_stories(sources, edition_date, now)
    repeat_story = next(story for story in stories if story["title"] == title)
    assert repeat_story["summary"] == ""
    assert any(item.get("reason") == "summary_repeats_headline_or_is_malformed" for item in rejected)


def test_gaza_run_merges_named_casualty_same_event_and_keeps_distinct_story(monkeypatch):
    root = make_work_root(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", root / "output" / "backups" / "gaza")
    source_dir = root / "data" / "dispatches" / "gaza" / "sources" / "2026-06-20"
    source_dir.mkdir(parents=True)
    source_dir.joinpath("manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-06-20-guardian-world-554da4348d06",
                    "title": "Al Jazeera cameraman Ahmed Wishah killed in Israeli strike on Gaza",
                    "url": "https://www.theguardian.com/world/2026/jun/20/al-jazeera-cameraman-ahmed-wishah-killed-in-israeli-strike-on-gaza",
                    "publisher": "The Guardian",
                    "published_at": "2026-06-20T19:49:11+00:00",
                    "retrieved_at": "2026-06-20T21:04:51.242154+00:00",
                    "summary_or_snippet": "Ahmed Wishah, a cameraman for Al Jazeera, was killed in a strike targeting a house in the Bureij refugee camp in central Gaza.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-06-20-aljazeera-middle-east-d44617db0c97",
                    "title": "Al Jazeera cameraman Ahmed Wishah killed in Israeli attack in Gaza",
                    "url": "https://www.aljazeera.com/news/2026/6/20/al-jazeera-cameraman-ahmad-wishah-killed-in-israeli-attack-in-gaza?traffic_source=rss",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-06-20T18:07:09+00:00",
                    "retrieved_at": "2026-06-20T21:04:51.242154+00:00",
                    "summary_or_snippet": "Al Jazeera said its cameraman Ahmed Wishah was killed in an Israeli attack in Gaza.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-06-20-bbc-middle-east-aed29a70b613",
                    "title": "Israeli strikes kill six people in Gaza including Al Jazeera cameraman, officials say",
                    "url": "https://www.bbc.com/news/articles/c4gy26p6pwzo?at_medium=RSS&at_campaign=rss",
                    "publisher": "BBC News",
                    "published_at": "2026-06-20T21:57:36+00:00",
                    "retrieved_at": "2026-06-20T22:10:54.052988+00:00",
                    "summary_or_snippet": "The Israeli military accused Ahmed Wishah of being a \"Hamas sniper operative\", without providing evidence.",
                    "source_type": "rss",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-06-20-aljazeera-middle-east-family",
                    "title": "Parents and two daughters killed in Israeli strike in Gaza",
                    "url": "https://www.aljazeera.com/news/2026/6/20/family-including-two-daughters-killed-in-israeli-strikes-on-gaza?traffic_source=rss",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-06-20T17:45:00+00:00",
                    "retrieved_at": "2026-06-20T21:04:51.242154+00:00",
                    "summary_or_snippet": "Israel has repeatedly violated the October ceasefire brokered by the US.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_gaza_dispatch(root, "2026-06-20", from_manual_sources=True, dry_run=False, render=True, all_steps=False)
    html = (root / "output" / "site" / "gaza" / "editions" / "2026-06-20" / "index.html").read_text(encoding="utf-8")
    glance = html.split("<h2>At A Glance</h2>", 1)[1].split("</ul>", 1)[0]
    core = html.split("<h2>Core Gaza Developments</h2>", 1)[1]
    audio_result = write_gaza_audio_outputs(root, "2026-06-20", dry_run=False, tts_provider="none")
    transcript = audio_result.transcript_path.read_text(encoding="utf-8")
    audio_json = json.loads(audio_result.metadata_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert glance.count("Ahmed Wishah") == 1
    assert core.count("<article><h3>") == 2
    assert "Parents and two daughters killed in Israeli strike in Gaza" in html
    assert "https://www.theguardian.com/world/2026/jun/20/al-jazeera-cameraman-ahmed-wishah-killed-in-israeli-strike-on-gaza" in html
    assert "https://www.aljazeera.com/news/2026/6/20/al-jazeera-cameraman-ahmad-wishah-killed-in-israeli-attack-in-gaza?traffic_source=rss" in html
    assert "https://www.bbc.com/news/articles/c4gy26p6pwzo" in html
    assert "Source mix: 2 stories from 3 publishers." in html
    assert "Publishers: Al Jazeera, BBC News, The Guardian." in html
    assert audio_json["source_count"] == 4
    assert audio_json["tts_story_count"] == 2
    assert ".." not in audio_json["script_text"]
    assert "Wishah among at least 260 journalists killed since." not in audio_json["script_text"]
    assert "260. Palestinian journalists" not in audio_json["script_text"]
    assert "Qatar-based news network Al Jazeera has said one of its journalists" not in audio_json["script_text"]
    assert "to have been killed since." not in audio_json["script_text"]
    assert audio_json["script_text"].count("Israel's war on Gaza began in October 2023") <= 1
    assert audio_json["script_text"].count("journalists killed since") <= 1
    assert "Multiple outlets reported that Ahmed Wishah, a cameraman for Al Jazeera, was killed in an Israeli strike on a house in the Bureij refugee camp in central Gaza." in audio_json["script_text"]
    attribution = audio_json["script_text"].split("This was reported by ", 1)[1].split(".", 1)[0]
    assert "The Guardian" in attribution
    assert "Al Jazeera" in attribution
    assert "BBC News" in attribution
    assert transcript.count("This was reported by") == 2
    assert 'href="https://www.bbc.com/news/articles/c4gy26p6pwzo?at_medium=RSS&amp;at_campaign=rss"' in transcript
    assert 'href="https://www.theguardian.com/world/2026/jun/20/al-jazeera-cameraman-ahmed-wishah-killed-in-israeli-strike-on-gaza"' in transcript
    assert 'href="https://www.aljazeera.com/news/2026/6/20/al-jazeera-cameraman-ahmad-wishah-killed-in-israeli-attack-in-gaza?traffic_source=rss"' in transcript
    assert 'href="https://www.aljazeera.com/news/2026/6/20/family-including-two-daughters-killed-in-israeli-strikes-on-gaza?traffic_source=rss"' in transcript


def test_gaza_public_summary_sanitizer_repairs_entity_period_joins_and_drops_trailing_fragments(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-29",
        [
            {
                "source_record_id": "gaza-src-2026-05-29-001",
                "title": "Ceasefire terms and control boundaries update",
                "url": "https://example.com/gaza-join-1",
                "publisher": "Reuters",
                "published_at": "2026-05-29T09:00:00Z",
                "retrieved_at": "2026-05-29T10:00:00Z",
                "summary_or_snippet": "The expansion in control by. Israel would contradict the terms of the ceasefire. Israel and Hamas agreed to in October 2025.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-002",
                "title": "Demarcation line and control update",
                "url": "https://example.com/gaza-join-2",
                "publisher": "AP",
                "published_at": "2026-05-29T09:10:00Z",
                "retrieved_at": "2026-05-29T10:05:00Z",
                "summary_or_snippet": "Under the US-brokered ceasefire in October, the Israeli army withdrew to a demarcation line which gave. Israel direct control of 53% of Gaza territory.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-003",
                "title": "Protests over upcoming matches and Gaza war response",
                "url": "https://example.com/gaza-join-3",
                "publisher": "BBC",
                "published_at": "2026-05-29T09:20:00Z",
                "retrieved_at": "2026-05-29T10:10:00Z",
                "summary_or_snippet": "Ireland's football match against Qatar was stalled by pro-Palestinian protests over upcoming games against. Israel.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-004",
                "title": "West Bank land registry escalation concern",
                "url": "https://example.com/gaza-join-4",
                "publisher": "Guardian",
                "published_at": "2026-05-29T09:30:00Z",
                "retrieved_at": "2026-05-29T10:15:00Z",
                "summary_or_snippet": "A digital register of land ownership in the West Bank is seen as an escalation of. Israel's occupation.",
                "source_type": "news",
                "region_scope": "Palestine",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-005",
                "title": "Talks face risk of collapse amid renewed pressure",
                "url": "https://example.com/gaza-join-5",
                "publisher": "Al Jazeera",
                "published_at": "2026-05-29T09:40:00Z",
                "retrieved_at": "2026-05-29T10:20:00Z",
                "summary_or_snippet": "Negotiators described an escalation in a move that threatens to torpedo an",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "diplomacy",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-006",
                "title": "NPR detention law context summary",
                "url": "https://example.com/gaza-join-6",
                "publisher": "NPR",
                "published_at": "2026-05-29T09:50:00Z",
                "retrieved_at": "2026-05-29T10:25:00Z",
                "summary_or_snippet": "A controversial law allows. Israel to hold Palestinians in prison without charge or trial.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-29", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-29" / "index.html")
    assert "by. Israel" not in html
    assert "gave. Israel" not in html
    assert "against. Israel" not in html
    assert "of. Israel" not in html
    assert "allows. Israel" not in html
    assert "to torpedo an" not in html
    assert "ceasefire. Israel and Hamas agreed to" not in html
    assert "allows Israel to hold Palestinians" in html
    assert "expansion in control by Israel" not in html
    assert "expansion in Israeli control" in html
    assert "ceasefire Israel and Hamas agreed to" in html


def test_npr_detention_summary_repairs_allows_period_in_public_html(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-31",
        [
            {
                "source_record_id": "gaza-src-2026-05-31-001",
                "title": "NPR detention law context summary",
                "url": "https://example.com/gaza-npr-detention",
                "publisher": "NPR",
                "published_at": "2026-05-31T07:00:00Z",
                "retrieved_at": "2026-05-31T07:05:00Z",
                "summary_or_snippet": "A controversial law allows. Israel to hold Palestinians in prison without charge or trial.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "rights",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-31-002",
                "title": "Gaza update filler",
                "url": "https://example.com/gaza-filler",
                "publisher": "Reuters",
                "published_at": "2026-05-31T08:00:00Z",
                "retrieved_at": "2026-05-31T08:05:00Z",
                "summary_or_snippet": "Gaza developments were reported.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-31", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-31" / "index.html")
    assert "allows. Israel" not in html
    assert "allows Israel to hold Palestinians" in html


def test_summary_keeps_complete_sentence_and_drops_bad_trailing_fragment(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-29",
        [
            {
                "source_record_id": "gaza-src-2026-05-29-011",
                "title": "Netanyahu orders army expansion in Gaza",
                "url": "https://example.com/gaza-guardian-like",
                "publisher": "The Guardian",
                "published_at": "2026-05-29T09:00:00Z",
                "retrieved_at": "2026-05-29T10:00:00Z",
                "summary_or_snippet": (
                    "Israeli prime minister Benjamin Netanyahu said he ordered the army to expand control of Gaza. "
                    "The expansion in control by. Israel would contradict the terms of the ceasefire in a move that threatens to torpedo an"
                ),
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-05-29-012",
                "title": "Aid route update",
                "url": "https://example.com/gaza-aid-route",
                "publisher": "Reuters",
                "published_at": "2026-05-29T09:10:00Z",
                "retrieved_at": "2026-05-29T10:10:00Z",
                "summary_or_snippet": "Aid route restrictions were reported.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-29", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-29" / "index.html")
    assert "Israeli prime minister Benjamin Netanyahu said he ordered the army to expand control of Gaza." in html
    assert "to torpedo an" not in html
    assert "by. Israel" not in html
    assert "The Guardian" in html
    guardian_block = html.split("Netanyahu orders army expansion in Gaza", 1)[1]
    assert "<p><strong>Context:</strong>" in guardian_block
    assert guardian_block.split("<p><strong>Context:</strong>", 1)[0].count("<p>") >= 2


def test_summary_with_only_bad_fragment_gets_title_fallback_and_today_read_not_empty(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    write_manual_sources(
        work,
        "2026-05-30",
        [
            {
                "source_record_id": "gaza-src-2026-05-30-001",
                "title": "Netanyahu orders Israeli army to seize '70% of Gaza Strip', violating ceasefire deal",
                "url": "https://example.com/gaza-only-fragment",
                "publisher": "The Guardian",
                "published_at": "2026-05-30T09:00:00Z",
                "retrieved_at": "2026-05-30T10:00:00Z",
                "summary_or_snippet": "in a move that threatens to torpedo an",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result = run_gaza_dispatch(work, "2026-05-30", from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-30" / "index.html")
    assert "to torpedo an" not in html
    assert "This source record concerns: Netanyahu orders Israeli army to seize" in html
    assert "<h2>Today" in html and "Read</h2>" in html
    today_read_block = html
    assert "<p>" in today_read_block


def test_render_gaza_edition_shows_transcript_audio_callout_when_transcript_exists(tmp_path):
    edition_date = "2026-05-31"
    audio_dir = tmp_path / "output" / "site" / "gaza" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{edition_date}-transcript.html").write_text("transcript", encoding="utf-8")
    (tmp_path / "output" / "site" / "gaza" / "audio" / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    stories = [
        {
            "title": "Test story",
            "summary": "Source-backed summary.",
            "source_record_ids": ["s1"],
            "story_scope": "core_gaza",
            "category": "conflict",
        }
    ]
    sources = [{"source_record_id": "s1", "title": "S1", "url": "https://example.com/s1", "publisher": "Reuters"}]
    adequacy = {"label": "Daily briefing", "status": "daily_briefing", "warnings": []}
    html = render_gaza_edition(edition_date, stories, sources, adequacy, root=tmp_path)
    assert "Read audio transcript" in html
    assert "Audio file not generated yet." in html
    assert "/gaza/audio/podcast.xml" in html
    assert "<audio controls" not in html


def test_written_gaza_edition_excludes_newsletter_sidebar_and_lebanon_only_rows(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    edition_date = "2026-06-05"
    write_manual_sources(
        work,
        edition_date,
        [
            {
                "source_record_id": "gaza-2026-06-05-guardian-newsletter",
                "title": "Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end",
                "url": "https://example.com/guardian-newsletter",
                "publisher": "The Guardian",
                "published_at": "2026-06-05T09:00:00Z",
                "retrieved_at": "2026-06-05T09:05:00Z",
                "summary_or_snippet": (
                    "In today's newsletter: Global powers are focused elsewhere. "
                    "UK politics | social care system update. "
                    "Environment | broad climate item. "
                    "Ukraine | negotiation item. "
                    "England news | planning laws debate. "
                    "UK news | Andrew Mountbatten-Windsor and royal property."
                ),
                "source_type": "news",
                "region_scope": "Gaza / Lebanon / Iran",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-2026-06-05-lebanon-only",
                "title": "UN agency says displacement in Lebanon rises despite ceasefire",
                "url": "https://example.com/lebanon-only",
                "publisher": "Anadolu Agency",
                "published_at": "2026-06-05T09:10:00Z",
                "retrieved_at": "2026-06-05T09:15:00Z",
                "summary_or_snippet": "More than 2,100 people sheltering in UNRWA facilities as hostilities continue despite truce.",
                "source_type": "news",
                "region_scope": "Lebanon",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-2026-06-05-detainees",
                "title": "Israel Supreme Court strikes down ban on Red Cross prison visits",
                "url": "https://example.com/detainees",
                "publisher": "The New Arab",
                "published_at": "2026-06-05T09:20:00Z",
                "retrieved_at": "2026-06-05T09:25:00Z",
                "summary_or_snippet": "The ICRC said it was ready to resume visits to Palestinian detainees held in Israeli detention.",
                "source_type": "news",
                "region_scope": "Palestinian detainees / Gaza context",
                "category_hint": "palestinian_development",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-2026-06-05-strikes",
                "title": "Israeli strikes kill 11 people in Gaza City, medics say",
                "url": "https://example.com/strikes",
                "publisher": "BBC News",
                "published_at": "2026-06-05T09:30:00Z",
                "retrieved_at": "2026-06-05T09:35:00Z",
                "summary_or_snippet": "Medics reported casualties after strikes in Gaza City.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-2026-06-05-1967",
                "title": "Newly disclosed Israeli testimonies detail expulsions, killings during 1967 war: Report",
                "url": "https://example.com/1967",
                "publisher": "Anadolu Agency",
                "published_at": "2026-06-05T09:40:00Z",
                "retrieved_at": "2026-06-05T09:45:00Z",
                "summary_or_snippet": "Archival material documents expulsions and killings of Palestinians in 1967.",
                "source_type": "news",
                "region_scope": "Palestinian context",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, edition_date, from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True

    html = read(work / "output" / "site" / "gaza" / "editions" / edition_date / "index.html")
    for blocked in (
        "UK politics",
        "Environment",
        "Ukraine",
        "England news",
        "Andrew Mountbatten-Windsor",
        "Lebanon rises despite ceasefire",
        "social care system",
        "planning laws",
    ):
        assert blocked not in html
    assert "Israel Supreme Court strikes down ban on Red Cross prison visits" in html
    assert "Israeli strikes kill 11 people in Gaza City, medics say" in html
    assert "Newly disclosed Israeli testimonies detail expulsions, killings during 1967 war: Report" in html
    assert 'href="https://example.com/detainees"' in html
    assert 'href="https://example.com/strikes"' in html

    curation = json.loads(read(work / "output" / "site" / "gaza" / "editions" / edition_date / "curation_manifest.json"))
    excluded = {row["title"]: row for row in curation if row.get("public_rendered") is False}
    assert excluded["Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end"]["excluded_reason"] == "excluded marker 'UK politics |'"
    assert excluded["UN agency says displacement in Lebanon rises despite ceasefire"]["excluded_reason"] == "excluded marker 'Lebanon rises despite ceasefire'"

    collection_report = json.loads(read(work / "data" / "dispatches" / "gaza" / "editions" / edition_date / "collection_report.json"))
    reasons = {row["title"]: row["reason"] for row in collection_report.get("written_public_exclusions") or []}
    assert reasons["Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end"] == "excluded marker 'UK politics |'"
    assert reasons["UN agency says displacement in Lebanon rises despite ceasefire"] == "excluded marker 'Lebanon rises despite ceasefire'"
    assert collection_report["final_story_count"] == 3


def test_render_gaza_edition_shows_audio_player_only_when_mp3_exists(tmp_path):
    edition_date = "2026-05-31"
    audio_dir = tmp_path / "output" / "site" / "gaza" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{edition_date}-transcript.html").write_text("transcript", encoding="utf-8")
    (audio_dir / f"{edition_date}.mp3").write_bytes(b"audio")
    stories = [
        {
            "title": "Test story",
            "summary": "Source-backed summary.",
            "source_record_ids": ["s1"],
            "story_scope": "core_gaza",
            "category": "conflict",
        }
    ]
    sources = [{"source_record_id": "s1", "title": "S1", "url": "https://example.com/s1", "publisher": "Reuters"}]
    adequacy = {"label": "Daily briefing", "status": "daily_briefing", "warnings": []}
    html = render_gaza_edition(edition_date, stories, sources, adequacy, root=tmp_path)
    assert "<audio controls" in html
    assert f"/gaza/audio/{edition_date}.mp3" in html


def test_render_gaza_edition_omits_audio_callout_when_no_audio_artifacts(tmp_path):
    edition_date = "2026-05-31"
    stories = [
        {
            "title": "Test story",
            "summary": "Source-backed summary.",
            "source_record_ids": ["s1"],
            "story_scope": "core_gaza",
            "category": "conflict",
        }
    ]
    sources = [{"source_record_id": "s1", "title": "S1", "url": "https://example.com/s1", "publisher": "Reuters"}]
    adequacy = {"label": "Daily briefing", "status": "daily_briefing", "warnings": []}
    html = render_gaza_edition(edition_date, stories, sources, adequacy, root=tmp_path)
    assert "Audio Briefing" not in html
    assert "/gaza/audio/" not in html


def test_run_gaza_dispatch_preserves_audio_callout_when_audio_artifacts_exist(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    edition_date = "2026-05-31"
    audio_dir = work / "output" / "site" / "gaza" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{edition_date}-transcript.html").write_text("transcript", encoding="utf-8")
    (work / "output" / "site" / "gaza" / "audio" / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    write_manual_sources(
        work,
        edition_date,
        [
            {
                "source_record_id": "gaza-src-2026-05-31-001",
                "title": "Aid pressure update",
                "url": "https://example.com/gaza-audio-preserve-1",
                "publisher": "Reuters",
                "published_at": "2026-05-31T09:00:00Z",
                "retrieved_at": "2026-05-31T10:00:00Z",
                "summary_or_snippet": "Aid pressure around Gaza's borders continued.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result = run_gaza_dispatch(work, edition_date, from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / edition_date / "index.html")
    assert "Read audio transcript" in html
    assert "Audio file not generated yet." in html
    assert "/gaza/audio/podcast.xml" in html


def test_gaza_public_prose_cleans_sentence_stitching_in_html(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    edition_date = "2026-06-16"
    write_manual_sources(
        work,
        edition_date,
        [
                {
                    "source_record_id": "gaza-src-2026-06-16-001",
                    "title": "Palestinian detainees face review in Gaza",
                    "url": "https://example.com/gaza-under",
                    "publisher": "Reuters",
                    "published_at": "2026-06-16T09:00:00Z",
                    "retrieved_at": "2026-06-16T09:05:00Z",
                    "summary_or_snippet": "In Gaza, under. Israel's 'unlawful combatant' law remained in force for Palestinian detainees.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "rights",
                    "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-06-16-002",
                "title": "Gaza response while clause",
                "url": "https://example.com/gaza-while",
                "publisher": "The New Arab",
                "published_at": "2026-06-16T10:00:00Z",
                "retrieved_at": "2026-06-16T10:05:00Z",
                "summary_or_snippet": "while. Israel violently lashes out at critics of the ceasefire.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            },
            {
                "source_record_id": "gaza-src-2026-06-16-003",
                "title": "Gaza talks between clause",
                "url": "https://example.com/gaza-between",
                "publisher": "Al Jazeera",
                "published_at": "2026-06-16T11:00:00Z",
                "retrieved_at": "2026-06-16T11:05:00Z",
                "summary_or_snippet": "between. Israel and the militant group Hamas remained divided on access terms.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "diplomacy",
                "reliability_tier": "reported-public-source",
            },
        ],
    )
    result = run_gaza_dispatch(work, edition_date, from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / edition_date / "index.html")
    lowered = html.lower()
    assert "under. israel" not in lowered
    assert "while. israel" not in lowered
    assert "between. israel" not in lowered
    assert "while israel violently lashes out" in lowered
    assert "between israel and the militant group hamas" in lowered


def test_gaza_public_prose_collapses_same_event_ceasefire_reports_and_preserves_traceability(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = make_work_root(repo)
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", work / "output" / "test-backups" / "gaza")
    edition_date = "2026-06-18"
    records = [
        {
            "source_record_id": "gaza-2026-06-18-the-new-arab-ff68b80ca5b4",
            "title": "Israel has killed more than 1,000 people in Gaza since ceasefire",
            "url": "https://www.newarab.com/news/israel-has-killed-more-1000-people-gaza-ceasefire",
            "publisher": "The New Arab",
            "published_at": "2026-06-18T12:00:00Z",
            "retrieved_at": "2026-06-18T12:05:00Z",
            "summary_or_snippet": "The number of Palestinians killed by Israel since an October 2025 so-called 'truce' brokered by US President Donald Trump was 1,008, the health ministry said.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-2026-06-18-aljazeera-middle-east-5898873c55a6",
            "title": "Israel kills at least three Palestinians in Gaza City drone strike",
            "url": "https://www.aljazeera.com/news/2026/6/18/israel-kills-at-least-three-palestinians-in-gaza-city-drone-strike?traffic_source=rss",
            "publisher": "Al Jazeera",
            "published_at": "2026-06-18T12:10:00Z",
            "retrieved_at": "2026-06-18T12:15:00Z",
            "summary_or_snippet": "Gaza's Health Ministry says at least 1,007 Palestinians have been killed by Israel since the so-called 'ceasefire'.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
        {
            "source_record_id": "gaza-2026-06-18-npr-world-da9d8e33a46b",
            "title": "Over 1,000 people killed during Gaza ceasefire, Palestinian authorities say",
            "url": "https://www.npr.org/2026/06/18/g-s1-128734/over-1-000-people-killed-during-gaza-ceasefire-palestinian-authorities-say",
            "publisher": "NPR",
            "published_at": "2026-06-18T12:20:00Z",
            "retrieved_at": "2026-06-18T12:25:00Z",
            "summary_or_snippet": "Israeli operations in the Gaza Strip have killed 1,005 Palestinians since a ceasefire was reached between Israel and the militant group Hamas last October, according to Gaza Health Ministry.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "conflict",
            "reliability_tier": "reported-public-source",
        },
    ]
    write_manual_sources(work, edition_date, records)
    result = run_gaza_dispatch(work, edition_date, from_manual_sources=True, dry_run=False, render=False, all_steps=True, allow_thin_edition=True)
    assert result["ok"] is True
    html = read(work / "output" / "site" / "gaza" / "editions" / edition_date / "index.html")
    today_read = html.split("<h2>Today", 1)[1].split("<h2>At A Glance</h2>", 1)[0]
    at_a_glance = html.split("<h2>At A Glance</h2>", 1)[1].split("</ul>", 1)[0]
    assert today_read.count("<p>") == 2
    assert today_read.count("Multiple outlets reported it") == 1
    assert at_a_glance.count("<li>") == 1
    assert at_a_glance.count("1,000") <= 1
    for url in (
        "https://www.newarab.com/news/israel-has-killed-more-1000-people-gaza-ceasefire",
        "https://www.aljazeera.com/news/2026/6/18/israel-kills-at-least-three-palestinians-in-gaza-city-drone-strike?traffic_source=rss",
        "https://www.npr.org/2026/06/18/g-s1-128734/over-1-000-people-killed-during-gaza-ceasefire-palestinian-authorities-say",
    ):
        assert url in html

