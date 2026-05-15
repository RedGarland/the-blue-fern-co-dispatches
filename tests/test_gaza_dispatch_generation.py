import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.generator import build_site
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
    assert report["provider_diagnostics"][0]["items_with_gaza_terms"] == 3


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
    first = run_gaza_dispatch(work, "2026-05-10", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
    assert first["ok"] is True
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
    run_gaza_dispatch(work, "2026-05-10", from_manual_sources=True, dry_run=False, render=False, all_steps=True)
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
                "title": "The secret mission to rescue the UN’s vital Palestinian refugee archive",
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
