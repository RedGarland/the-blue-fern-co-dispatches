from __future__ import annotations

import json
import ssl
from pathlib import Path

from bluefern_dispatches import gaza_sources
from bluefern_dispatches import gaza_wide_source_audit


SEEDS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gaza" / "wide_source_audit" / "manual_seed_urls.json"
MANIFEST_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gaza" / "wide_source_audit" / "sources_manifest.json"


def _write_seed_file(tmp_path: Path, extra_rows: list[dict[str, object]] | None = None) -> Path:
    rows = json.loads(SEEDS_FIXTURE.read_text(encoding="utf-8"))
    if extra_rows:
        rows.extend(extra_rows)
    path = tmp_path / "manual_seed_urls.json"
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _noop_fetch_rss_items(_url: str) -> list[dict[str, str]]:
    return []


def _build_report(tmp_path: Path, *, extra_rows: list[dict[str, object]] | None = None) -> dict[str, object]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    manual_urls_file = _write_seed_file(tmp_path, extra_rows)
    return gaza_wide_source_audit.write_gaza_wide_source_audit_report(
        root,
        "2026-06-22",
        manifest_path=MANIFEST_FIXTURE,
        manual_urls_file=manual_urls_file,
        fetch_rss_items_fn=_noop_fetch_rss_items,
    )


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {"Content-Type": "application/rss+xml; charset=utf-8"}
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _write_query_file(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def test_aggregator_url_is_kept_as_discovery_metadata_not_evidence(tmp_path):
    report = _build_report(tmp_path)
    rows = {row["publisher"]: row for row in report["candidates"] if row.get("publisher")}

    trt = rows["TRT World"]
    assert trt["google_news_url"].startswith("https://news.google.com/rss/articles/")
    assert trt["url"].startswith("https://www.trtworld.com/middle-east/")
    assert trt["canonical_url"] == trt["url"]
    assert trt["source_registry_status"] == "known_provider"
    assert trt["source_tier"] != "aggregator"


def test_manifest_matching_known_provider_miss_and_new_provider_candidate(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "https://newsource.example/gaza-water-shortage",
                "title": "Gaza water shortages deepen",
                "publisher": "New Source Co",
                "published_at": "2026-06-22T11:15:00+00:00",
                "summary_or_snippet": "Water access pressure in Gaza.",
                "discovery_source": "manual_seed_url",
            }
        ],
    )
    rows = {row["publisher"]: row for row in report["candidates"] if row.get("publisher")}

    who = rows["WHO"]
    trt = rows["TRT World"]
    new_source = rows["New Source Co"]

    assert who["already_in_manifest"] is True
    assert "already_in_manifest" in who["comparison_flags"]
    assert trt["already_in_manifest"] is False
    assert "known_provider_missed" in trt["comparison_flags"]
    assert new_source["already_in_manifest"] is False
    assert "new_provider_candidate" in new_source["comparison_flags"]
    assert report["summary"]["known_provider_missed_count"] >= 1
    assert report["summary"]["new_provider_candidate_count"] >= 1


def test_google_news_urls_are_never_recommended_as_registry_changes(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVk?oc=5",
                "title": "Emergency care collapse in Gaza - imemc.org",
                "publisher": "",
                "published_at": "2026-06-22T11:15:00+00:00",
                "summary_or_snippet": "Google News discovery only.",
                "discovery_source": "manual_seed_url",
            }
        ],
    )

    registry_targets = {str(row["publisher_or_domain"]).lower() for row in report["recommended_registry_changes"]}
    assert "news.google.com" not in registry_targets
    assert "google news" not in registry_targets


def test_unresolved_google_news_candidate_is_blocked_and_uses_discovery_metadata_only(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVk?oc=5",
                "title": "Hospital attack leaves medics wounded - imemc.org",
                "publisher": "",
                "published_at": "2026-06-22T11:15:00+00:00",
                "summary_or_snippet": "Google News discovery only.",
                "discovery_source": "manual_seed_url",
            }
        ],
    )

    row = next(item for item in report["candidates"] if item["title"] == "Hospital attack leaves medics wounded - imemc.org")
    assert row["aggregator_url"].startswith("https://news.google.com/rss/articles/")
    assert row["google_news_url"] == row["aggregator_url"]
    assert row["url"] == ""
    assert row["canonical_url"] == ""
    assert "manual_review_needed" in row["comparison_flags"]
    assert "blocked_or_unresolved" in row["comparison_flags"]
    assert row["source_registry_status"] == "canonical_resolution_needed"
    assert row["recommended_action"] == "resolve canonical publisher URL before source review"


def test_visible_domain_in_google_news_title_is_inferred_for_classification(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVkLWltZW1j?oc=5",
                "title": "Israeli attack kills child in al-Mawasi - imemc.org",
                "publisher": "",
                "published_at": "2026-06-22T11:15:00+00:00",
                "summary_or_snippet": "Same Google News item, visible source suffix only.",
                "discovery_source": "manual_seed_url",
            }
        ],
    )

    row = next(item for item in report["candidates"] if item["title"] == "Israeli attack kills child in al-Mawasi - imemc.org")
    assert row["publisher"] == "IMEMC"
    assert row["inferred_publisher_domain"] == "imemc.org"
    assert row["source_tier"] == "region_specialist"
    assert row["source_registry_status"] == "canonical_resolution_needed"


def test_google_news_wrapper_payload_can_resolve_real_publisher_url(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiT2h0dHBzOi8vd3d3LnRydHdvcmxkLmNvbS9taWRkbGUtZWFzdC9nYXphLWhlYWx0aC1zeXN0ZW0tc3RyYWluLW9mZmljaWFscy13YXJuLW9mLWNvbGxhcHNpbmctY2FyZS0xODI3MzY0NQ?oc=5",
                "title": "Gaza health system strain, officials warn of collapsing care - TRT World",
                "publisher": "",
                "published_at": "2026-06-22T07:15:00+00:00",
                "summary_or_snippet": "Health care access and hospital strain in Gaza.",
                "discovery_source": "manual_seed_url",
            }
        ],
    )

    row = next(item for item in report["candidates"] if item["title"] == "Gaza health system strain, officials warn of collapsing care - TRT World")
    assert row["google_news_url"].startswith("https://news.google.com/rss/articles/")
    assert row["url"] == "https://www.trtworld.com/middle-east/gaza-health-system-strain-officials-warn-of-collapsing-care-18273645"
    assert row["canonical_url"] == row["url"]
    assert row["publisher"] == "TRT World"
    assert row["source_registry_status"] == "known_provider"


def test_audit_fetch_abstraction_succeeds_with_verified_tls_context(monkeypatch):
    seen: dict[str, object] = {}
    feed = b"""<?xml version='1.0' encoding='UTF-8'?><rss><channel><item><title>Gaza hospital warning</title><link>https://www.who.int/news/item/2026-06-22-gaza-hospital-access-warning</link><pubDate>2026-06-22</pubDate><description>Official humanitarian warning.</description></item></channel></rss>"""

    def _fake_open(request, *, timeout, context):
        seen["timeout"] = timeout
        seen["context"] = context
        return _FakeResponse(feed)

    monkeypatch.setattr(gaza_wide_source_audit, "_open_url_with_context", _fake_open)
    monkeypatch.setattr(ssl, "_create_unverified_context", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected unverified TLS context")))

    fetch = gaza_wide_source_audit._default_audit_fetch_rss_items(gaza_wide_source_audit.AuditFetchOptions(timeout=7))
    items = fetch("https://example.test/feed.xml")

    assert len(items) == 1
    assert items[0]["title"] == "Gaza hospital warning"
    assert seen["timeout"] == 7
    context = seen["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_syndicated_duplicate_prefers_original_and_official_source(tmp_path):
    report = _build_report(tmp_path)
    rows = {row["publisher"]: row for row in report["candidates"] if row.get("publisher")}

    repub = rows["Local News Syndication"]
    who = rows["WHO"]

    assert "syndicated_duplicate" in repub["comparison_flags"]
    assert "official_source_preferred" in repub["comparison_flags"]
    assert repub["recommended_action"] == "skip syndicated duplicate; keep original/source-of-record URL"
    assert who["already_in_manifest"] is True
    assert "official_source_preferred" in who["comparison_flags"]


def test_stale_outside_scope_weak_signal_and_blocked_urls_are_explicit(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "https://example.com/live-blog",
                "title": "Sports live blog mentions Gaza once",
                "publisher": "Example Sports Desk",
                "published_at": "2026-06-22T12:00:00+00:00",
                "summary_or_snippet": "Incidental Gaza mention in unrelated live coverage.",
                "discovery_source": "manual_seed_url",
            },
            {
                "url": "https://example.com/election-live",
                "title": "Election live blog",
                "publisher": "Example Politics Desk",
                "published_at": "2026-06-22T12:00:00+00:00",
                "summary_or_snippet": "No relevance here.",
                "discovery_source": "manual_seed_url",
            },
            {
                "url": "",
                "title": "Gaza access warning with no URL",
                "publisher": "Blocked Source",
                "published_at": "",
                "summary_or_snippet": "Could not fetch the source.",
                "discovery_source": "manual_seed_url",
            },
        ],
    )
    rows = {row["title"]: row for row in report["candidates"] if row.get("title")}

    stale = rows["Gaza medic killed in strike near hospital"]
    weak = rows["Sports live blog mentions Gaza once"]
    outside = rows["Election live blog"]
    blocked = rows["Gaza access warning with no URL"]

    assert "stale" in stale["comparison_flags"]
    assert stale["skip_or_accept_reason"] == "stale item"
    assert "weak_signal" in weak["comparison_flags"]
    assert weak["skip_or_accept_reason"] == "weak context-only item"
    assert "outside_scope" in outside["comparison_flags"]
    assert outside["skip_or_accept_reason"] == "outside-scope item"
    assert "blocked_or_unresolved" in blocked["comparison_flags"]
    assert blocked["skip_or_accept_reason"] == "blocked fetch or unresolved URL"


def test_report_writes_only_under_output_review_and_does_not_touch_public_paths(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    manual_urls_file = _write_seed_file(tmp_path)
    monkeypatch.setattr(gaza_sources, "fetch_rss_items", lambda *_args, **_kwargs: [])

    report = gaza_wide_source_audit.write_gaza_wide_source_audit_report(
        root,
        "2026-06-22",
        manifest_path=MANIFEST_FIXTURE,
        manual_urls_file=manual_urls_file,
    )

    json_path = root / "output" / "review" / "gaza_wide_discovery_2026-06-22.json"
    md_path = root / "output" / "review" / "gaza_wide_discovery_2026-06-22.md"
    assert json_path.exists()
    assert md_path.exists()
    assert report["json_report_path"] == str(json_path)
    assert report["markdown_report_path"] == str(md_path)
    assert not (root / "output" / "site").exists()
    assert not (root / "output" / "site" / "gaza" / "rss.xml").exists()
    assert not (root / "output" / "site" / "gaza" / "archive.html").exists()
    assert not (root / "output" / "site" / "gaza" / "audio" / "podcast.xml").exists()
    assert not (root / "bluefern-dispatches-pages").exists()


def test_tls_fetch_failure_is_recorded_with_context_and_does_not_crash(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    queries_file = _write_query_file(
        tmp_path,
        [
            {
                "query_url": "https://example.test/feed.xml",
                "publisher": "Example Feed",
                "discovery_source": "query_surface",
                "discovery_query": "\"Gaza\" example feed",
                "surface_type": "query_surface",
            }
        ],
    )

    monkeypatch.setattr(gaza_wide_source_audit, "_default_query_surfaces", lambda *_args, **_kwargs: [])

    def _tls_fail(_request, *, timeout, context):
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(gaza_wide_source_audit, "_open_url_with_context", _tls_fail)
    monkeypatch.setattr(gaza_wide_source_audit, "_certifi_cafile", lambda: "")

    report = gaza_wide_source_audit.write_gaza_wide_source_audit_report(
        root,
        "2026-06-22",
        manifest_path=MANIFEST_FIXTURE,
        queries_file=queries_file,
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["candidates"] == []
    warning = report["warnings"][0]
    assert "feed_fetch_failed" in warning
    assert "url=https://example.test/feed.xml" in warning
    assert 'query="Gaza" example feed' in warning
    assert "tls_certificate_verification_failed" in warning


def test_curl_no_revoke_workaround_is_opt_in_only(monkeypatch):
    def _tls_fail(_request, *, timeout, context):
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(gaza_wide_source_audit, "_open_url_with_context", _tls_fail)
    monkeypatch.setattr(gaza_wide_source_audit, "_certifi_cafile", lambda: "")

    called = {"count": 0}

    def _fake_curl(url, *, timeout, allow_no_revoke):
        called["count"] += 1
        assert allow_no_revoke is True
        return (
            b"<?xml version='1.0' encoding='UTF-8'?><rss><channel><item><title>Gaza aid convoy reaches hospital</title><link>https://www.aljazeera.com/news/2026/6/22/gaza-aid-convoy-reaches-hospital</link></item></channel></rss>",
            "curl_no_revoke",
        )

    monkeypatch.setattr(gaza_wide_source_audit, "_run_curl_fetch", _fake_curl)

    blocked_payload = gaza_wide_source_audit._audit_fetch_feed_payload(
        "https://example.test/feed.xml",
        options=gaza_wide_source_audit.AuditFetchOptions(timeout=5, allow_curl_no_revoke=False),
    )
    assert blocked_payload["ok"] is False
    assert called["count"] == 0

    allowed_fetch = gaza_wide_source_audit._default_audit_fetch_rss_items(
        gaza_wide_source_audit.AuditFetchOptions(timeout=5, allow_curl_no_revoke=True)
    )
    items = allowed_fetch("https://example.test/feed.xml")
    assert called["count"] == 1
    assert items[0]["title"] == "Gaza aid convoy reaches hospital"


def test_dry_run_does_not_write_review_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    manual_urls_file = _write_seed_file(tmp_path)

    report = gaza_wide_source_audit.write_gaza_wide_source_audit_report(
        root,
        "2026-06-22",
        manifest_path=MANIFEST_FIXTURE,
        manual_urls_file=manual_urls_file,
        fetch_rss_items_fn=_noop_fetch_rss_items,
        dry_run=True,
    )

    assert report["json_report_path"].endswith("output\\review\\gaza_wide_discovery_2026-06-22.json")
    assert report["markdown_report_path"].endswith("output\\review\\gaza_wide_discovery_2026-06-22.md")
    assert not (root / "output" / "review" / "gaza_wide_discovery_2026-06-22.json").exists()
    assert not (root / "output" / "review" / "gaza_wide_discovery_2026-06-22.md").exists()


def test_report_separates_current_and_stale_blocked_rows_and_prioritizes_current_resolved_items(tmp_path):
    report = _build_report(
        tmp_path,
        extra_rows=[
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVkLWN1cnJlbnQ?oc=5",
                "title": "Current unresolved child casualty report - Reuters",
                "publisher": "",
                "published_at": "2026-06-22T12:05:00+00:00",
                "summary_or_snippet": "Current Gaza casualty report.",
                "discovery_source": "manual_seed_url",
            },
            {
                "url": "",
                "google_news_url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVkLXN0YWxl?oc=5",
                "title": "Older unresolved hospital report - Reuters",
                "publisher": "",
                "published_at": "2026-06-10T12:05:00+00:00",
                "summary_or_snippet": "Older Gaza hospital report.",
                "discovery_source": "manual_seed_url",
            },
            {
                "url": "https://www.aljazeera.com/news/2026/6/22/gaza-child-killed-in-strike",
                "title": "Child killed in Gaza strike",
                "publisher": "Al Jazeera",
                "published_at": "2026-06-22T13:00:00+00:00",
                "summary_or_snippet": "Current Gaza ground development with casualties and child death.",
                "discovery_source": "manual_seed_url",
            },
            {
                "url": "https://www.who.int/news/item/2026-06-15-gaza-shelter-warning",
                "title": "Older Gaza shelter warning",
                "publisher": "WHO",
                "published_at": "2026-06-15T10:00:00+00:00",
                "summary_or_snippet": "Older Gaza warning.",
                "discovery_source": "manual_seed_url",
            },
        ],
    )

    current_titles = [row["title"] for row in report["blocked_or_unresolved_current"]]
    stale_titles = [row["title"] for row in report["blocked_or_unresolved_stale"]]
    assert "Current unresolved child casualty report - Reuters" in current_titles
    assert "Older unresolved hospital report - Reuters" in stale_titles

    markdown = gaza_wide_source_audit.render_gaza_wide_source_audit_markdown(report)
    assert "## Current Blocked Or Unresolved" in markdown
    assert "## Stale Blocked Or Unresolved" in markdown
    assert markdown.index("Child killed in Gaza strike") < markdown.index("Older unresolved hospital report - Reuters")


def test_script_parse_args_supports_requested_flags():
    args = gaza_wide_source_audit.parse_args(
        [
            "--date",
            "2026-06-22",
            "--manifest",
            "data/dispatches/gaza/editions/2026-06-22/sources_manifest.json",
            "--queries-file",
            "queries.json",
            "--manual-urls-file",
            "manual_seed_urls.json",
            "--output-dir",
            "output/review",
            "--feed-timeout",
            "45",
            "--allow-curl-no-revoke",
            "--dry-run",
        ]
    )

    assert args.date == "2026-06-22"
    assert args.dry_run is True
    assert args.output_dir == "output/review"
    assert args.feed_timeout == 45
    assert args.allow_curl_no_revoke is True
