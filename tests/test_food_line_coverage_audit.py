from __future__ import annotations

import builtins
import csv
import hashlib
import json
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_coverage_audit import (
    build_food_line_coverage_audit,
    render_food_line_coverage_markdown,
    write_food_line_coverage_audit,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _source_record(
    *,
    source_record_id: str,
    title: str,
    url: str,
    publisher: str,
    source_family: str,
    included: bool,
    exclusion_reason: str = "",
    published_at: str = "2026-07-09T12:00:00Z",
    location_name: str = "Omaha, NE",
    state: str = "NE",
    pressure_verification_status: str = "source_text_verified",
    source_role: str = "local_signal",
    source_purpose: str = "current_news",
    pressure_type: str = "demand strain",
) -> dict[str, object]:
    return {
        "source_record_id": source_record_id,
        "title": title,
        "url": url,
        "publisher": publisher,
        "source_name": publisher,
        "published_at": published_at,
        "page_metadata_date": "",
        "retrieved_at": "2026-07-10T00:00:00Z",
        "source_type": "page",
        "collector_source_type": "page",
        "source_origin": "registry",
        "registry_status": "registry_source",
        "extraction_quality": "high",
        "source_family": source_family,
        "location_name": location_name,
        "state": state,
        "source_role": source_role,
        "source_purpose": source_purpose,
        "pressure_type": pressure_type,
        "pressure_signal": included,
        "pressure_verification_status": pressure_verification_status,
        "source_public_story_eligible": included,
        "included": included,
        "exclusion_reason": exclusion_reason,
    }


def _build_fixture_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    review_dir = root / "output" / "review" / "food-line" / "2026-07-09"
    source_dir = data_dir / "sources" / "2026-07-09"
    edition_dir = data_dir / "editions" / "2026-07-09"
    discovery_dir = data_dir / "discovery" / "2026-07-09"
    gap_dir = data_dir / "discovery_gap" / "2026-07-09"

    _write_json(
        source_dir / "auto_sources.json",
        [
            _source_record(
                source_record_id="included-1",
                title="Food bank demand surges in Omaha as SNAP cuts strain families",
                url="https://example.com/news/food-bank-demand-rises",
                publisher="Example News",
                source_family="local_news",
                included=True,
            ),
            _source_record(
                source_record_id="excluded-1",
                title="Community pantry donation drive",
                url="https://example.com/news/community-pantry-drive",
                publisher="Example News",
                source_family="local_news",
                included=False,
                exclusion_reason="resource-only / no pressure signal",
                source_role="resource_context",
                source_purpose="resource_page",
                pressure_verification_status="demoted_context",
            ),
        ],
    )
    _write_json(
        source_dir / "manual_sources.json",
        [
            _source_record(
                source_record_id="unresolved-1",
                title="Community food update needs review",
                url="https://example.org/review-story",
                publisher="Review News",
                source_family="public_radio",
                included=False,
                exclusion_reason="",
                pressure_verification_status="needs_review",
            )
        ],
    )
    _write_json(
        source_dir / "discovery_sources.json",
        [
            _source_record(
                source_record_id="duplicate-source",
                title="Food bank demand surges in Omaha as SNAP cuts strain families",
                url="https://example.com/news/food-bank-demand-rises/",
                publisher="Example News",
                source_family="local_news",
                included=True,
            )
        ],
    )
    _write_json(
        source_dir / "collector_audit.json",
        [
            {
                "source_id": "collector-audit-1",
                "source_name": "Example News",
                "source_family": "local_news",
                "url": "https://example.com/news/food-bank-demand-rises",
                "fetched": True,
                "item_count": 1,
                "accepted_pressure_count": 1,
                "demoted_count": 0,
                "rejected_count": 0,
                "top_rejection_reasons": [],
                "fetch_failure_type": "",
                "fetch_failure_action": "",
                "fetch_failure_transient": False,
                "extraction_basis_used": ["page_text_excerpt"],
            }
        ],
    )
    _write_text(
        review_dir / "pressure_review.csv",
        "\n".join(
            [
                "source_record_id,pressure_signal,pressure_verification_status,pressure_type,source_published_date,source_freshness_status,source_freshness_date_basis,source_public_story_eligible,collected_date,freshness_status,freshness_disqualification_reason,primary_eligible,primary_disqualification_reason,affected_groups,location_name,state,pressure_summary,evidence_text,pressure_match_terms,source_title,source_url,primary_source_url,secondary_source_url,source_traceability_role,source_family,source_id",
                'included-1,true,source_text_verified,demand strain,2026-07-09,fresh_daily_signal,published_at,true,2026-07-09,fresh_daily_signal,,true,,"families","Omaha, NE",NE,"Demand surged","Families are seeking more food","demand surged","Food bank demand surges in Omaha as SNAP cuts strain families","https://example.com/news/food-bank-demand-rises","https://example.com/news/food-bank-demand-rises",,article_url,local_news,example',
            ]
        )
        + "\n",
    )
    _write_json(
        discovery_dir / "discovery_candidates.json",
        [
            {
                "candidate_id": "candidate-need-review",
                "title": "Potential food bank story needing review",
                "discovered_title": "Potential food bank story needing review",
                "url": "https://search.example.com/story",
                "canonical_url": "https://search.example.com/story",
                "discovered_url": "https://search.example.com/story",
                "publisher": "Search News",
                "source_name": "Search News",
                "published_at": "2026-07-09T15:00:00Z",
                "classification_status": "needs_review",
                "review_status": "approved",
                "exclusion_reason": "",
                "discovery_channel": "search",
                "source_family": "local_news",
                "location_name": "Omaha, NE",
                "state": "NE",
            }
        ],
    )
    _write_json(
        gap_dir / "discovery_gap_report.json",
        {
            "candidates": [
                {
                    "candidate_id": "gap-candidate-1",
                    "title": "Potential food bank story needing review",
                    "url": "https://search.example.com/story",
                    "publisher": "Search News",
                    "source_family": "local_news",
                    "classification": "needs_review",
                }
            ]
        },
    )
    _write_json(
        edition_dir / "run_manifest.json",
        {
            "edition_date": "2026-07-09",
            "public_rendered": False,
            "excluded_count": 3,
            "exclusion_reason_summary": "Exclusion breakdown: resource-only / no pressure signal 1; weak pressure signal 1; other 1.",
            "primary_signal_status": "none",
            "public_url": None,
        },
    )
    _write_json(
        data_dir / "source_performance_history.json",
        {
            "example-feed": {
                "runs_seen": 12,
                "runs_fetched": 4,
                "fetch_failures": 8,
                "items_seen": 12,
                "verified_pressure_records": 0,
                "demoted_records": 12,
                "rejected_records": 0,
                "last_verified_pressure_at": "",
                "last_fetch_error": "HTTPError: HTTP Error 403: Forbidden",
                "rolling_quality_score": 0,
            }
        },
    )
    _write_json(
        data_dir / "source_registry.json",
        [
            {
                "source_id": "example-feed",
                "source_name": "Example Feed",
                "publisher": "Example News",
                "url": "https://example.com/news/",
                "source_family": "local_news",
                "source_type": "page",
                "state": "NE",
            }
        ],
    )
    _write_json(
        data_dir / "discovery_gap_queries.json",
        {
            "queries": ["food bank demand", "snap food bank", "pantry demand"],
            "exclude_domains": ["facebook.com", "youtube.com"],
        },
    )

    benchmark_file = data_dir / "coverage_benchmarks" / "2026-07-09_2026-07-10.json"
    _write_json(
        benchmark_file,
        [
            {
                "title": "Food bank demand surges in Omaha as SNAP cuts strain families",
                "url": "https://example.com/news/food-bank-demand-rises/?utm_source=rss",
                "publisher": "Example News",
                "published_at": "2026-07-09T12:00:00Z",
                "reason_expected_to_qualify": "pressure report on food-bank demand",
                "review_status": "approved",
                "location": "Omaha, NE",
                "pressure_type": "demand strain",
            },
            {
                "title": "Community pantry donation drive",
                "url": "https://example.com/news/community-pantry-drive",
                "publisher": "Example News",
                "published_at": "2026-07-09T12:30:00Z",
                "reason_expected_to_qualify": "should be reviewed then excluded as resource only",
                "review_status": "approved",
                "location": "Omaha, NE",
                "pressure_type": "resource-only",
            },
            {
                "title": "Benchmark never discovered",
                "url": "https://example.com/news/never-discovered",
                "publisher": "Example News",
                "published_at": "2026-07-09T13:00:00Z",
                "reason_expected_to_qualify": "food pressure candidate that should have been discovered",
                "review_status": "approved",
                "location": "Omaha, NE",
                "pressure_type": "demand strain",
            },
            {
                "title": "Benchmark missing artifacts",
                "url": "https://example.com/news/missing-artifacts",
                "publisher": "Example News",
                "published_at": "2026-07-10T13:00:00Z",
                "reason_expected_to_qualify": "benchmark on a date with missing artifacts",
                "review_status": "approved",
                "location": "Omaha, NE",
                "pressure_type": "demand strain",
            },
            {
                "title": "Food bank demand surges in Omaha as SNAP cuts strain families",
                "url": "https://example.com/news/food-bank-demand-rises/",
                "publisher": "Example News",
                "published_at": "2026-07-09T12:00:00Z",
                "reason_expected_to_qualify": "duplicate benchmark copy",
                "review_status": "approved",
                "location": "Omaha, NE",
                "pressure_type": "demand strain",
            },
            {
                "title": "Draft benchmark should be skipped",
                "url": "https://example.com/news/draft-benchmark",
                "publisher": "Example News",
                "published_at": "2026-07-09T14:00:00Z",
                "reason_expected_to_qualify": "not yet reviewed",
                "review_status": "draft",
                "location": "Omaha, NE",
                "pressure_type": "demand strain",
            },
        ],
    )

    return root, benchmark_file, source_dir / "auto_sources.json"


def _build_report(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root, benchmark_file, source_file = _build_fixture_root(tmp_path)
    report = build_food_line_coverage_audit(root, "2026-07-09", "2026-07-10", benchmark_file=benchmark_file)
    return root, source_file, report


def test_food_line_coverage_audit_classifies_discovered_and_included_benchmark(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)
    row = next(item for item in report["benchmarks"]["results"] if item["title"] == "Food bank demand surges in Omaha as SNAP cuts strain families" and item["review_status"] == "approved")

    assert row["classification"] == "discovered_and_included"
    assert row["matched_record"]["matched_by"] == "canonical_url"
    assert row["matched_record"]["status"] == "included"
    assert report["recall_metrics"]["discovery_recall"] == pytest.approx(0.5, rel=1e-6)


def test_food_line_coverage_audit_classifies_discovered_and_excluded_benchmark(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)
    row = next(item for item in report["benchmarks"]["results"] if item["title"] == "Community pantry donation drive")

    assert row["classification"] == "discovered_and_excluded"
    assert row["matched_record"]["exclusion_reason"] == "resource-only / no pressure signal"
    assert row["matched_record"]["status"] == "excluded"


def test_food_line_coverage_audit_marks_missing_benchmark_as_not_discovered(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)
    row = next(item for item in report["benchmarks"]["results"] if item["title"] == "Benchmark never discovered")

    assert row["classification"] == "not_discovered"


def test_food_line_coverage_audit_marks_missing_artifacts_as_indeterminate(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)
    row = next(item for item in report["benchmarks"]["results"] if item["title"] == "Benchmark missing artifacts")

    assert row["classification"] == "indeterminate because artifacts are missing"


def test_food_line_coverage_audit_uses_canonical_url_matching(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)
    row = next(item for item in report["benchmarks"]["results"] if item["title"] == "Food bank demand surges in Omaha as SNAP cuts strain families" and item["review_status"] == "approved")

    assert row["matched_record"]["matched_by"] == "canonical_url"
    assert row["matched_record"]["url"] == "https://example.com/news/food-bank-demand-rises"
    assert row["matched_record"]["match_score"] == 1.0


def test_food_line_coverage_audit_dedupes_duplicate_benchmarks(tmp_path: Path) -> None:
    _, _, report = _build_report(tmp_path)

    assert report["benchmarks"]["active_benchmark_count"] == 4
    assert report["benchmarks"]["duplicate_benchmark_count"] == 1
    assert report["benchmarks"]["skipped_benchmark_count"] == 1


def test_food_line_coverage_audit_no_writes_without_flag(tmp_path: Path) -> None:
    root, _, report = _build_report(tmp_path)
    markdown = render_food_line_coverage_markdown(report)

    assert "Food Line coverage audit" in markdown
    assert not (root / "output" / "review" / "food-line" / "coverage-audits").exists()


def test_food_line_coverage_audit_write_stays_outside_output_site(tmp_path: Path) -> None:
    root, _, report = _build_report(tmp_path)
    json_path, markdown_path = write_food_line_coverage_audit(root, report, "2026-07-09", "2026-07-10")

    assert json_path.is_file()
    assert markdown_path.is_file()
    assert str(json_path).replace("\\", "/").startswith(str(root / "output" / "review" / "food-line" / "coverage-audits").replace("\\", "/"))
    assert str(markdown_path).replace("\\", "/").startswith(str(root / "output" / "review" / "food-line" / "coverage-audits").replace("\\", "/"))
    assert not str(json_path).replace("\\", "/").startswith(str(root / "output" / "site").replace("\\", "/"))
    assert not str(markdown_path).replace("\\", "/").startswith(str(root / "output" / "site").replace("\\", "/"))


def test_food_line_coverage_audit_does_not_import_or_invoke_gaza_runner_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, benchmark_file, _ = _build_report(tmp_path)
    forbidden_imports: list[str] = []
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        module_name = str(name or "")
        if "gaza" in module_name.lower():
            forbidden_imports.append(module_name)
            raise AssertionError(f"unexpected Gaza import: {module_name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    report = build_food_line_coverage_audit(root, "2026-07-09", "2026-07-10", benchmark_file=benchmark_file)
    assert report["audit_type"] == "food_line_coverage_audit"
    assert forbidden_imports == []
    assert not (root / "output" / "site").exists()


def test_food_line_coverage_audit_does_not_modify_source_records_or_manifests(tmp_path: Path) -> None:
    root, benchmark_file, source_file = _build_fixture_root(tmp_path)
    manifest_path = root / "data" / "dispatches" / "food-line" / "editions" / "2026-07-09" / "run_manifest.json"
    source_hash_before = hashlib.sha256(source_file.read_bytes()).hexdigest()
    manifest_hash_before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    report = build_food_line_coverage_audit(root, "2026-07-09", "2026-07-10", benchmark_file=benchmark_file)
    _ = render_food_line_coverage_markdown(report)

    source_hash_after = hashlib.sha256(source_file.read_bytes()).hexdigest()
    manifest_hash_after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert source_hash_before == source_hash_after
    assert manifest_hash_before == manifest_hash_after
