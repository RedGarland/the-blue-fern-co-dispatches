from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_source_registry import CareLineSourceRegistry, load_registry, validate_registry_file


def source(**updates):
    row = {
        "source_id": "example-health-feed",
        "name": "Example Health Feed",
        "publisher": "Example Health",
        "source_type": "regional_publisher",
        "feed_url": "https://example.org/health/feed/",
        "homepage_url": "https://example.org/health/",
        "state": "IA",
        "geographic_scope": "state",
        "organization_type": "nonprofit_newsroom",
        "care_line_topics": ["hospital", "clinic", "access"],
        "authority_level": "secondary",
        "expected_update_frequency": "daily",
        "enabled": True,
        "adapter_type": "rss",
        "requires_html_followup": False,
        "source_role": "state_health_access_reporting",
        "historical_depth": "current feed",
        "notes": "Fixture source.",
        "created_at": "2026-07-22T00:00:00Z",
        "updated_at": "2026-07-22T00:00:00Z",
        "allowed_hosts": ["example.org"],
    }
    row.update(updates)
    return row


def registry(*sources):
    return {"schema_version": "bluefern.care_line.source_registry.v1", "sources": list(sources)}


def test_01_registry_schema_validates_a_direct_rss_source():
    parsed = CareLineSourceRegistry.model_validate(registry(source()))
    assert parsed.sources[0].adapter_type == "rss"


def test_02_blank_publisher_is_rejected():
    with pytest.raises(ValueError, match="publisher is required"):
        CareLineSourceRegistry.model_validate(registry(source(publisher="")))


def test_03_unsupported_adapter_is_rejected():
    with pytest.raises(ValueError):
        CareLineSourceRegistry.model_validate(registry(source(adapter_type="browser")))


def test_04_duplicate_source_id_is_rejected():
    with pytest.raises(ValueError, match="duplicate source_id"):
        CareLineSourceRegistry.model_validate(registry(source(), source(feed_url="https://other.example.org/feed/")))


def test_05_duplicate_feed_url_requires_explicit_reason():
    with pytest.raises(ValueError, match="duplicate feed_url"):
        CareLineSourceRegistry.model_validate(registry(source(), source(source_id="other-source")))


def test_06_duplicate_feed_url_with_reason_is_allowed():
    parsed = CareLineSourceRegistry.model_validate(
        registry(source(duplicate_feed_reason="shared government feed"), source(source_id="other-source", duplicate_feed_reason="shared government feed"))
    )
    assert len(parsed.sources) == 2


def test_07_disabled_source_is_skipped_by_default(tmp_path: Path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry(source(enabled=False))), encoding="utf-8")
    assert load_registry(path).sources == []
    assert len(load_registry(path, include_disabled=True).sources) == 1


def test_08_invalid_scope_is_rejected():
    with pytest.raises(ValueError, match="unsupported geographic_scope"):
        CareLineSourceRegistry.model_validate(registry(source(geographic_scope="planet")))


def test_09_missing_state_for_state_scope_is_rejected():
    with pytest.raises(ValueError, match="state is required"):
        CareLineSourceRegistry.model_validate(registry(source(state="")))


def test_10_registry_file_summary_counts_enabled_sources(tmp_path: Path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry(source(), source(source_id="disabled", feed_url="https://disabled.example.org/feed/", enabled=False))), encoding="utf-8")
    summary = validate_registry_file(path)
    assert summary["source_count"] == 2
    assert summary["enabled_source_count"] == 1


def test_11_registry_loader_normalizes_known_source_urls(tmp_path: Path):
    path = tmp_path / "registry.json"
    payload = registry(
        source(
            source_id="calmatters-health",
            feed_url="https://calmatters.org/category/health/feed/",
            homepage_url="https://calmatters.org/category/health/",
        ),
        source(
            source_id="rural-health-info-hub",
            feed_url="https://www.ruralhealthinfo.org/rss/news",
            homepage_url="https://www.ruralhealthinfo.org/news",
        ),
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_registry(path, include_disabled=True)
    cal = next(row for row in loaded.sources if row.source_id == "calmatters-health")
    rural = next(row for row in loaded.sources if row.source_id == "rural-health-info-hub")

    assert cal.feed_url == "https://calmatters.org/category/health/"
    assert cal.homepage_url == "https://calmatters.org/category/health/"
    assert rural.feed_url == "https://www.ruralhealthinfo.org/rss/news.xml"
    assert rural.homepage_url == "https://www.ruralhealthinfo.org/news"


def test_12_project_registry_meets_phase13_size_targets():
    summary = validate_registry_file(Path("data/dispatches/care-line/source_registry.json"))
    assert 20 <= summary["enabled_source_count"] <= 75
    assert summary["state_count"] >= 5
    assert sum(count for key, count in summary["source_type_counts"].items() if key in {"federal_source", "government_regulator", "government_health_department"}) >= 5
    assert summary["source_type_counts"].get("healthcare_organization", 0) >= 5
    regional = sum(summary["source_type_counts"].get(key, 0) for key in {"regional_publisher", "local_publisher", "public_radio"})
    assert regional >= 10
