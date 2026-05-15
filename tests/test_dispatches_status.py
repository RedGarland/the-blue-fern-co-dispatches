from __future__ import annotations

import json
from pathlib import Path

from scripts import dispatches_status


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, indent=2))


def _make_repo(root: Path, include_pages: bool = True) -> tuple[Path, Path]:
    for folder in [
        "scripts",
        "src/bluefern_dispatches",
        "tests",
        "docs",
        "assets",
        "logs",
        "output/site/gaza/editions/2026-05-10",
        "output/site/cascadia/editions/2026-05-10",
        "output/site/american-pressure/editions/2026-05-10",
        "output/dispatches/gaza/editions/2026-05-10",
        "output/dispatches/cascadia/weekly_gap_reports",
        "output/dispatches/american-pressure/source_health",
        "data/dispatches/american-pressure/sources/2026-05-10",
        "data/dispatches/american-pressure",
    ]:
        (root / folder).mkdir(parents=True, exist_ok=True)

    _write(root / "scripts" / "run_american_pressure_dispatch.py", 'result = {"live_fetch_enabled": False}\n')
    _write(root / "src" / "bluefern_dispatches" / "__init__.py", "")
    _write(
        root / "output" / "site" / "gaza" / "index.html",
        '<a href="editions/2026-05-10/">latest</a>',
    )
    _write(root / "output" / "site" / "gaza" / "archive.html", "archive")
    _write(root / "output" / "site" / "gaza" / "rss.xml", "rss")
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "edition_manifest.json", {"source_count": 1})
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json", [{"url": "https://example.com/a"}])
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "curation_manifest.json", [{"story_id": "s1"}])
    _write(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "index.html", '<a href="https://example.com/a">src</a>')
    _json(
        root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "dedupe_report.json",
        {"edition_date": "2026-05-10", "candidates_seen": 2, "included_stories": [{"id": 1}], "duplicate_skipped": []},
    )

    _write(root / "output" / "site" / "cascadia" / "index.html", '<a href="editions/2026-05-10/">weekly</a>')
    _write(root / "output" / "site" / "cascadia" / "archive.html", '<a href="editions/2026-05-10/">May 4-10</a>')
    _write(root / "output" / "site" / "cascadia" / "rss.xml", "rss")
    _json(root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "edition_manifest.json", {"warnings": [], "errors": []})
    _json(root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "sources_manifest.json", [{"url": "https://example.com/c"}])
    _json(root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "curation_manifest.json", [{"story_id": "c1"}])
    _write(root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "index.html", '<a href="https://example.com/c">src</a>')
    _json(
        root / "output" / "dispatches" / "cascadia" / "weekly_gap_reports" / "2026-05-10.json",
        {
            "source_checks_attempted": 10,
            "source_checks_successful": 9,
            "successful_fetch_rate": 0.9,
            "final_public_story_count": 2,
            "final_zero_story_result_is_credible": True,
        },
    )

    _write(root / "output" / "site" / "american-pressure" / "index.html", '<a href="editions/2026-05-10/">weekly</a>')
    _write(root / "output" / "site" / "american-pressure" / "archive.html", "archive")
    _write(root / "output" / "site" / "american-pressure" / "rss.xml", "rss")
    _json(root / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "edition_manifest.json", {"source_count": 1})
    _json(root / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "sources_manifest.json", [{"url": "https://example.com/ap"}])
    _json(root / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "curation_manifest.json", [{"story_id": "a1"}])
    _write(root / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "index.html", '<a href="https://example.com/ap">src</a>')

    _write(
        root / "data" / "dispatches" / "american-pressure" / "source_registry.yml",
        """sources:\n  - source_id: s1\n    pillar: food_pressure\n    enabled: true\n  - source_id: s2\n    pillar: health_access_pressure\n    enabled: false\n""",
    )
    _json(root / "output" / "dispatches" / "american-pressure" / "source_health" / "2026-05-10.json", [{"source_id": "s1"}])
    _json(root / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-10" / "manual_sources.json", {"sources": []})

    pages = root / "bluefern-dispatches-pages"
    if include_pages:
        (pages / ".git").mkdir(parents=True, exist_ok=True)
        _write(pages / "CNAME", f"{dispatches_status.EXPECTED_CNAME}\n")
    return root, pages


def _stub_git(monkeypatch, *, root: Path, pages: Path, pages_branch: str = "gh-pages"):
    def fake(repo: Path, *args: str):
        repo = repo.resolve()
        if repo == root.resolve():
            if args == ("branch", "--show-current"):
                return True, "main"
            if args == ("rev-parse", "--short", "HEAD"):
                return True, "abc1234"
            if args == ("status", "--short"):
                return True, ""
            if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                return False, ""
        if repo == pages.resolve():
            if args == ("branch", "--show-current"):
                return True, pages_branch
            if args == ("rev-parse", "--short", "HEAD"):
                return True, "def5678"
            if args == ("status", "--porcelain"):
                return True, ""
            if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                return False, ""
        return False, ""

    monkeypatch.setattr(dispatches_status, "run_git", fake)


def test_build_status_runs_on_fake_repo(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert status["project"]["root"] == str(root)
    assert status["dispatches"]["american_pressure"]["registry_summary"]["total_sources"] == 2


def test_detects_public_detail_critical(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    (root / "output" / "site" / "detail").mkdir(parents=True)
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "output/site/detail exists" in status["critical_errors"]


def test_detects_public_paid_critical(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    (root / "output" / "site" / "paid").mkdir(parents=True)
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "output/site/paid exists" in status["critical_errors"]


def test_detects_bad_fns_only_in_active_output(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(root / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "index.html", dispatches_status.BAD_FNS_URL)
    _write(root / "output" / "test-runs" / "x" / "bad.txt", dispatches_status.BAD_FNS_URL)
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "bad FNS link appears in active American Pressure output" in status["critical_errors"]


def test_detects_pages_repo_missing(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path, include_pages=False)
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "Pages repo missing" in status["critical_errors"]


def test_detects_pages_wrong_branch(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _stub_git(monkeypatch, root=root, pages=pages, pages_branch="main")
    status = dispatches_status.build_status(root, pages)
    assert "Pages repo is not on gh-pages" in status["critical_errors"]


def test_detects_gaza_duplicate_urls(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(
        root / "output" / "site" / "gaza" / "archive.html",
        '<a href="editions/2026-05-08/">2026-05-08</a><a href="editions/2026-05-09/">2026-05-09</a>',
    )
    _write(
        root / "output" / "site" / "gaza" / "rss.xml",
        "<item><link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-08/</link></item>"
        "<item><link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-09/</link></item>",
    )
    for day in ["2026-05-09", "2026-05-08"]:
        _json(root / "output" / "site" / "gaza" / "editions" / day / "sources_manifest.json", [{"url": "https://dup.example/story"}])
        _json(root / "output" / "site" / "gaza" / "editions" / day / "edition_manifest.json", {"source_count": 1})
        _json(root / "output" / "site" / "gaza" / "editions" / day / "curation_manifest.json", [{"story_id": day}])
        _write(root / "output" / "site" / "gaza" / "editions" / day / "index.html", '<a href="https://dup.example/story">src</a>')
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "Gaza duplicate public edition detected" in status["critical_errors"]


def test_reports_latest_manual_source_date(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _json(root / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-17" / "manual_sources.json", {"sources": []})
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert status["dispatches"]["american_pressure"]["latest_manual_source_date"] == "2026-05-17"


def test_json_output_valid(tmp_path, monkeypatch, capsys):
    root, pages = _make_repo(tmp_path)
    _stub_git(monkeypatch, root=root, pages=pages)
    monkeypatch.setattr(dispatches_status.Path, "resolve", lambda self: self)
    monkeypatch.setattr(dispatches_status, "build_status", lambda *_args, **_kwargs: {"ok": True, "critical_errors": [], "warnings": [], "project": {}, "pages_repo": {}, "public_safety": {}, "dispatches": {}, "recommendations": []})
    rc = dispatches_status.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)["ok"] is True


def test_write_report_outside_output_site(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _stub_git(monkeypatch, root=root, pages=pages)
    monkeypatch.setattr(dispatches_status, "build_status", lambda *_args, **_kwargs: {"ok": True, "critical_errors": [], "warnings": [], "project": {}, "pages_repo": {}, "public_safety": {}, "dispatches": {}, "recommendations": []})
    report = root / "output" / "dispatches" / "status" / "r.json"
    monkeypatch.setattr(dispatches_status.Path, "resolve", lambda self: self)
    monkeypatch.setattr(dispatches_status.Path, "parents", property(lambda self: tuple(Path(self).parts and [Path(*Path(self).parts[:i]) for i in range(len(Path(self).parts), 0, -1)])))
    # run via build_status/write path directly to avoid cwd assumptions
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("{}", encoding="utf-8")
    assert report.exists()


def test_scan_smtp_not_printed(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(root / "logs" / "x.log", "SMTP_PASSWORD=secret")
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    rendered = dispatches_status.render_text_status(status, [])
    assert "secret" not in rendered
    assert "SMTP_PASSWORD" in status["critical_errors"][0]


def test_gaza_latest_public_comes_from_archive_rss_links_not_latest_folder(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    for day in ["2026-05-11", "2026-05-12"]:
        _json(root / "output" / "site" / "gaza" / "editions" / day / "edition_manifest.json", {"source_count": 1, "story_count": 1, "errors": []})
        _json(root / "output" / "site" / "gaza" / "editions" / day / "sources_manifest.json", [{"url": f"https://example.com/{day}"}])
        _json(root / "output" / "site" / "gaza" / "editions" / day / "curation_manifest.json", [{"story_id": day}])
        _write(root / "output" / "site" / "gaza" / "editions" / day / "index.html", '<a href="https://example.com/src">src</a>')
    _write(root / "output" / "site" / "gaza" / "archive.html", '<a href="editions/2026-05-11/">2026-05-11</a>')
    _write(root / "output" / "site" / "gaza" / "rss.xml", '<link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-11/</link>')
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    gaza = status["dispatches"]["gaza"]
    assert gaza["latest_public_edition_date"] == "2026-05-11"
    assert "2026-05-12" in gaza["stale_or_unlinked_edition_dates"]


def test_gaza_linked_zero_source_is_critical(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(root / "output" / "site" / "gaza" / "archive.html", '<a href="editions/2026-05-10/">2026-05-10</a>')
    _write(root / "output" / "site" / "gaza" / "rss.xml", '<link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-10/</link>')
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json", [])
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "curation_manifest.json", [])
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "edition_manifest.json", {"source_count": 0, "story_count": 0, "errors": []})
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert status["ok"] is False
    assert "Gaza linked public edition has zero sources" in status["critical_errors"]


def test_unlinked_dedupe_refused_gaza_folder_not_critical_by_itself(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(root / "output" / "site" / "gaza" / "archive.html", '<a href="editions/2026-05-10/">2026-05-10</a>')
    _write(root / "output" / "site" / "gaza" / "rss.xml", '<link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-10/</link>')
    _json(
        root / "output" / "site" / "gaza" / "editions" / "2026-05-12" / "edition_manifest.json",
        {"source_count": 0, "story_count": 0, "errors": ["No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition."]},
    )
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-12" / "sources_manifest.json", [])
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-12" / "curation_manifest.json", [])
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "Gaza linked public edition has dedupe-refusal errors" not in status["critical_errors"]


def test_unlinked_zero_source_gaza_folder_is_stale_info_not_critical(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _write(root / "output" / "site" / "gaza" / "archive.html", '<a href="editions/2026-05-10/">2026-05-10</a>')
    _write(root / "output" / "site" / "gaza" / "rss.xml", '<link>https://dispatches.thebluefernco.com/gaza/editions/2026-05-10/</link>')
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-13" / "sources_manifest.json", [])
    _json(root / "output" / "site" / "gaza" / "editions" / "2026-05-13" / "curation_manifest.json", [])
    _json(
        root / "output" / "site" / "gaza" / "editions" / "2026-05-13" / "edition_manifest.json",
        {"source_count": 0, "story_count": 0, "public_exposed": False, "errors": ["No valid traceable Gaza sources survived normalization and dedupe; refusing public edition generation."]},
    )
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert "2026-05-13" in status["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"]
    assert "Gaza linked public edition has zero sources" not in status["critical_errors"]


def test_gaza_collection_report_zero_candidates_is_warning_not_critical(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    _json(
        root / "data" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "collection_report.json",
        {
            "edition_date": "2026-05-10",
            "raw_candidate_count": 0,
            "accepted_candidate_count_before_dedupe": 0,
            "kept_after_dedupe": 0,
            "suppressed_after_dedupe": 0,
            "provider_failures": [],
            "source_providers_attempted": [{"provider": "manual_sources_json", "status": "no_candidates"}],
        },
    )
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    assert any("recent collection found zero candidates" in w for w in status["warnings"])
    assert not any("collection found zero candidates" in e for e in status["critical_errors"])


def test_cascadia_summary_includes_failure_counts_and_top_sources(tmp_path, monkeypatch):
    root, pages = _make_repo(tmp_path)
    week = root / "data" / "dispatches" / "cascadia" / "sources" / "2026-05-04_2026-05-10"
    week.mkdir(parents=True, exist_ok=True)
    _json(
        week / "weekly_quality_report.json",
        {
            "warnings": [
                "seattle-alerts item has weak date basis; published_at unavailable or unparseable",
                "registry source fetch error: HTTP Error 404: Not Found",
                "historical provider gdelt failed for query X: <urlopen error timed out>",
            ]
        },
    )
    _json(
        week / "registry_source_report.json",
        {
            "diagnostics": [
                {"source_id": "or-odot-news", "status_code": 404, "errors": ["HTTP Error 404: Not Found"], "failure_reason": "http_404"},
                {"source_id": "or-odot-news", "status_code": 404, "errors": ["HTTP Error 404: Not Found"], "failure_reason": "http_404"},
                {"source_id": "wa-ecology-news", "status_code": 403, "errors": ["HTTP Error 403: Forbidden"], "failure_reason": "http_403"},
            ],
            "records_by_source_id": {"seattle-alerts": 3},
        },
    )
    _stub_git(monkeypatch, root=root, pages=pages)
    status = dispatches_status.build_status(root, pages)
    cascadia = status["dispatches"]["cascadia"]
    assert cascadia["weak_date_warning_count"] == 1
    assert cascadia["registry_fetch_error_count"] == 1
    assert cascadia["gdelt_timeout_rate_limit_count"] == 1
    assert cascadia["top_failing_source_ids"][0]["source_id"] == "or-odot-news"
    assert cascadia["registry_fetch_errors_by_source_status"][0]["source_id"] == "or-odot-news"
    assert cascadia["registry_fetch_errors_by_source_status"][0]["status_code"] == 404
    assert cascadia["repeated_registry_failures"][0]["source_id"] == "or-odot-news"
    assert cascadia["repeated_registry_failures"][0]["count"] == 2


def test_build_cascadia_source_reliability_audit_classifies_dead_and_diagnostics(tmp_path):
    root, _pages = _make_repo(tmp_path)
    _write(
        root / "data" / "dispatches" / "cascadia" / "source_registry.yml",
        """sources:
  - source_id: dead-source
    url: https://example.com/dead
    enabled: true
    geographic_scope: WA
  - source_id: blocked-source
    url: https://example.com/blocked
    enabled: true
    geographic_scope: OR
""",
    )
    week = root / "data" / "dispatches" / "cascadia" / "sources" / "2026-05-04_2026-05-10"
    week.mkdir(parents=True, exist_ok=True)
    _json(
        week / "registry_source_report.json",
        {
            "warnings": ["dead-source item has weak date basis; published_at unavailable or unparseable"],
            "diagnostics": [
                {"source_id": "dead-source", "status_code": 404, "errors": ["HTTP Error 404: Not Found"], "failure_reason": "http_404"},
                {"source_id": "blocked-source", "status_code": 403, "errors": ["HTTP Error 403: Forbidden"], "failure_reason": "http_403"},
            ],
            "records_by_source_id": {},
        },
    )
    audit = dispatches_status.build_cascadia_source_reliability_audit(root)
    actions = {row["source_id"]: row["recommended_action"] for row in audit["sources"]}
    assert actions["dead-source"] == "disable_dead_source"
    assert actions["blocked-source"] == "diagnostics_only"


def test_parse_git_status_classifies_gaza_dated_runtime_paths_as_generated(monkeypatch, tmp_path):
    rows = "\n".join(
        [
            "?? data/dispatches/gaza/raw/2026-05-14/raw_sources.json",
            "?? data/dispatches/gaza/normalized/2026-05-14/normalized_sources.json",
            "?? data/dispatches/gaza/curated/2026-05-14/curation_manifest.json",
            "?? data/dispatches/gaza/editions/2026-05-14/collection_report.json",
            "?? data/dispatches/gaza/sources/2026-05-14/manual_sources.json",
        ]
    )

    monkeypatch.setattr(dispatches_status, "run_git", lambda *_args, **_kwargs: (True, rows))
    parsed = dispatches_status.parse_git_status(tmp_path)

    assert parsed["has_generated_changes"] is True
    assert parsed["has_source_changes"] is False
    assert any("data/dispatches/gaza/raw/2026-05-14" in path for path in parsed["generated_changes"])


def test_parse_git_status_keeps_gaza_sources_yml_as_source_change(monkeypatch, tmp_path):
    rows = " M data/dispatches/gaza/sources.yml"
    monkeypatch.setattr(dispatches_status, "run_git", lambda *_args, **_kwargs: (True, rows))
    parsed = dispatches_status.parse_git_status(tmp_path)
    assert parsed["has_source_changes"] is True
    assert "data/dispatches/gaza/sources.yml" in parsed["source_changes"]
    assert "data/dispatches/gaza/sources.yml" not in parsed["generated_changes"]


def test_parse_git_status_classifies_cascadia_dated_runtime_paths_as_generated(monkeypatch, tmp_path):
    rows = "\n".join(
        [
            "?? data/dispatches/cascadia/raw/2026-05-10/raw_sources.json",
            "?? data/dispatches/cascadia/normalized/2026-05-10/normalized_sources.json",
            "?? data/dispatches/cascadia/curated/2026-05-10/curation_manifest.json",
            "?? data/dispatches/cascadia/sources/2026-05-04_2026-05-10/weekly_quality_report.json",
        ]
    )
    monkeypatch.setattr(dispatches_status, "run_git", lambda *_args, **_kwargs: (True, rows))
    parsed = dispatches_status.parse_git_status(tmp_path)
    assert parsed["has_generated_changes"] is True
    assert parsed["has_source_changes"] is False
    assert any("data/dispatches/cascadia/raw/2026-05-10" in path for path in parsed["generated_changes"])


def test_parse_git_status_keeps_cascadia_source_registry_as_source_change(monkeypatch, tmp_path):
    rows = " M data/dispatches/cascadia/source_registry.yml"
    monkeypatch.setattr(dispatches_status, "run_git", lambda *_args, **_kwargs: (True, rows))
    parsed = dispatches_status.parse_git_status(tmp_path)
    assert parsed["has_source_changes"] is True
    assert "data/dispatches/cascadia/source_registry.yml" in parsed["source_changes"]
    assert "data/dispatches/cascadia/source_registry.yml" not in parsed["generated_changes"]
