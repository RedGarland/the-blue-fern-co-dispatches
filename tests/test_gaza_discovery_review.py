import json
from pathlib import Path

from bluefern_dispatches import gaza_discovery_review


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_sources_config(root: Path) -> None:
    path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """tiers:
  wire_and_major_international:
    - source_id: bbc-middle-east
      name: BBC Middle East
      url: https://feeds.bbci.co.uk/news/world/middle_east/rss.xml
      type: rss
      enabled: true
      source_state: enabled
      publisher: BBC News
      reliability_tier: reported-public-source
      category_hint: conflict
      region_scope: Gaza
      source_group: major
  region_specialist:
    - source_id: jpost-gaza-accountability-query
      name: Jerusalem Post Gaza Accountability Query
      query: site:jpost.com Gaza accountability
      type: google_news_rss
      enabled: true
      source_state: enabled
      publisher: The Jerusalem Post
      reliability_tier: reported-public-source
      category_hint: military_conduct_accountability
      region_scope: Gaza
      source_group: accountability_secondary
      discovery_role: secondary_accountability
""",
        encoding="utf-8",
    )


def test_manual_source_not_found_in_auto_candidates_is_reported(tmp_path: Path):
    _write_sources_config(tmp_path)
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json",
        [
            {
                "source_record_id": "manual-elpais",
                "title": "Heatwave in Gaza tents",
                "url": "https://english.elpais.com/international/2026/07/03/gaza-heatwave.html",
                "publisher": "EL PAIS English",
                "published_at": "2026-07-03T04:00:00Z",
                "retrieved_at": "2026-07-03T04:00:00Z",
                "summary_or_snippet": "Heat and water shortages in Gaza.",
                "source_type": "manual",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "humanitarian_conditions",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "raw" / "2026-07-03" / "raw_sources.json",
        [
            {
                "source_record_id": "auto-a",
                "title": "Other Gaza item",
                "url": "https://example.com/other",
                "publisher": "BBC News",
                "published_at": "2026-07-03T01:00:00Z",
                "retrieved_at": "2026-07-03T01:00:00Z",
                "summary_or_snippet": "Other item.",
                "source_type": "rss",
                "provider_id": "bbc-middle-east",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            }
        ],
    )

    report = gaza_discovery_review.build_gaza_discovery_miss_report(tmp_path, "2026-07-03")

    assert report["summary"]["missed_manual_record_count"] == 1
    row = report["missed_manual_records"][0]
    assert row["source_record_id"] == "manual-elpais"
    assert row["auto_discovery_found_same_url"] is False
    assert row["auto_discovery_found_same_publisher"] is False


def test_exact_url_match_is_not_reported_as_missed(tmp_path: Path):
    _write_sources_config(tmp_path)
    manual_row = {
        "source_record_id": "manual-bbc",
        "title": "Gaza patients face evacuation delays",
        "url": "https://www.bbc.com/news/articles/cn75ex1dv61o",
        "publisher": "BBC News",
        "published_at": "2026-07-02T11:53:00Z",
        "retrieved_at": "2026-07-02T11:53:00Z",
        "summary_or_snippet": "Evacuation delays.",
        "source_type": "manual",
        "provider_id": "manual-supplement",
        "region_scope": "Gaza",
        "category_hint": "medical_evacuation_health_system",
        "reliability_tier": "reported-public-source",
    }
    _write_json(tmp_path / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json", [manual_row])
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "raw" / "2026-07-03" / "raw_sources.json",
        [
            {
                **manual_row,
                "source_type": "rss",
                "provider_id": "bbc-middle-east",
            }
        ],
    )

    report = gaza_discovery_review.build_gaza_discovery_miss_report(tmp_path, "2026-07-03")

    assert report["summary"]["missed_manual_record_count"] == 0
    assert report["missed_manual_records"] == []


def test_same_publisher_different_url_is_reported_distinctly(tmp_path: Path):
    _write_sources_config(tmp_path)
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json",
        [
            {
                "source_record_id": "manual-bbc",
                "title": "Gaza patients face evacuation delays",
                "url": "https://www.bbc.com/news/articles/cn75ex1dv61o",
                "publisher": "BBC News",
                "published_at": "2026-07-02T11:53:00Z",
                "retrieved_at": "2026-07-02T11:53:00Z",
                "summary_or_snippet": "Evacuation delays.",
                "source_type": "manual",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "medical_evacuation_health_system",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "raw" / "2026-07-03" / "raw_sources.json",
        [
            {
                "source_record_id": "auto-bbc",
                "title": "Gaza patients face evacuation delays",
                "url": "https://www.bbc.co.uk/news/articles/cn75ex1dv61o?at_medium=RSS&at_campaign=rss",
                "publisher": "BBC News",
                "published_at": "2026-07-02T04:59:55+00:00",
                "retrieved_at": "2026-07-03T17:52:42Z",
                "summary_or_snippet": "Evacuation delays.",
                "source_type": "rss",
                "provider_id": "bbc-middle-east",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
            }
        ],
    )

    report = gaza_discovery_review.build_gaza_discovery_miss_report(tmp_path, "2026-07-03")

    assert report["summary"]["missed_manual_record_count"] == 1
    row = report["missed_manual_records"][0]
    assert row["auto_discovery_found_same_url"] is False
    assert row["auto_discovery_found_same_publisher"] is True
    assert row["same_publisher_same_title_variant"] is True


def test_missing_optional_artifacts_do_not_crash(tmp_path: Path):
    _write_sources_config(tmp_path)
    _write_json(
        tmp_path / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json",
        [
            {
                "source_record_id": "manual-jpost",
                "title": "IDF probes soldiers after Gaza video",
                "url": "https://www.jpost.com/israel-news/article-901263",
                "publisher": "The Jerusalem Post",
                "published_at": "2026-07-02T18:05:00+03:00",
                "retrieved_at": "2026-07-02T18:05:00+03:00",
                "summary_or_snippet": "Accountability coverage.",
                "source_type": "manual",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "military_conduct_accountability",
                "reliability_tier": "reported-public-source",
            }
        ],
    )

    report = gaza_discovery_review.build_gaza_discovery_miss_report(tmp_path, "2026-07-03")

    assert report["ok"] is True
    assert report["summary"]["missed_manual_record_count"] == 1
    assert any("optional auto source artifact missing" in warning for warning in report["warnings"])
