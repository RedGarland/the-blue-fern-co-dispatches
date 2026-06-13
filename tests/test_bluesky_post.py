import io
import json
from pathlib import Path
from urllib import error

from bluefern_dispatches import bluesky_post


def test_builds_expected_gaza_post_text_without_url():
    text = bluesky_post.build_gaza_bluesky_post_text("2026-05-07", "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/")
    assert "Today's Gaza Dispatch is live." not in text
    assert "Today's edition follows several threads at once:" not in text
    assert "Read the source-backed" not in text
    assert "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/" in text
    assert len(text) <= bluesky_post.BLUESKY_MAX_POST_LENGTH


def test_builds_reader_friendly_prose_from_fixture_story_fields(tmp_path: Path):
    curated = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-05-29" / "curation_manifest.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(
        json.dumps(
            [
                {
                    "title": "Mediators report renewed strain in ceasefire talks",
                    "summary": "Aid convoy access remains constrained after checkpoint delays.",
                    "category": "diplomacy",
                },
                {
                    "title": "Reports describe expanded Israeli military control positions inside Gaza",
                    "summary": "Displacement pressure increased in several areas.",
                    "category_hint": "conflict",
                },
                {
                    "title": "Legal filing seeks independent review of strike evidence and records",
                    "summary": "Palestinians were held without charge in reported detention cases.",
                    "category": "legal",
                },
            ]
        ),
        encoding="utf-8",
    )
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-05-29",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-05-29/",
        project_root=tmp_path,
    )
    assert text.startswith("In the May 29 Gaza briefing:")
    assert "Displacement pressure increased in several areas." in text or "Palestinians were held without charge in reported detention cases." in text
    assert "https://dispatches.thebluefernco.com/gaza/editions/2026-05-29/" in text
    assert "satellite imagery" not in text
    assert "1967" not in text


def write_current_edition_artifacts(root: Path, edition_date: str = "2026-05-07", summary: str = "Specific verified Gaza dispatch summary.") -> None:
    current = root / "output" / "dispatches" / "gaza" / "editions" / edition_date
    current.mkdir(parents=True, exist_ok=True)
    (current / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "title": "Current edition summary",
                    "summary": "Palestinians inspect the aftermath of an Israeli strike in Khan Younis.",
                    "included_in_public_summary": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    (current / "edition_manifest.json").write_text(
        json.dumps({"edition_date": edition_date, "source_count": 1, "publisher_count": 1, "publishers": ["Example News"], "social_summary": summary}),
        encoding="utf-8",
    )
    site = root / "output" / "site" / "gaza" / "editions" / edition_date
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html><body><p>June 7, 2026</p></body></html>", encoding="utf-8")
    run_manifest = root / "data" / "dispatches" / "gaza" / "editions" / edition_date / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(json.dumps({"social_summary": summary}), encoding="utf-8")


def test_focus_fallback_when_no_topics_derived(tmp_path: Path):
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-05-29",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-05-29/",
        project_root=tmp_path,
    )
    assert text == bluesky_post.BLUESKY_GAZA_POST_FALLBACK


def test_focus_derivation_sanitizes_internal_or_incomplete_public_prose(tmp_path: Path):
    curated = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-05-29" / "curation_manifest.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(
        json.dumps(
            [
                {
                    "title": "Aid corridor update",
                    "summary": "Aid corridor pressure remains in focus. It is included because source metadata ties it to Gaza.",
                },
                {
                    "title": "Ceasefire talks update",
                    "summary": "Mediators said the talks would allow.",
                },
            ]
        ),
        encoding="utf-8",
    )
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-05-29",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-05-29/",
        project_root=tmp_path,
    )
    assert text.startswith("In the May 29 Gaza briefing:")
    assert "Aid corridor pressure remains in focus." in text
    assert "included because" not in text.lower()
    assert "would allow." not in text.lower()


def test_reader_prose_respects_military_and_gaza_adjacent_attribution(tmp_path: Path):
    curated = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-05-31" / "curation_manifest.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(
        json.dumps(
            [
                {
                    "title": "IDF says it destroyed Hamas weapons storage facilities in Gaza",
                    "summary": "i24NEWS reported an IDF statement saying Israeli forces destroyed weapons storage facilities in Gaza.",
                    "category": "military_operations",
                    "attribution_mode": "military_claim_reported",
                    "claim_status": "military_claim_reported",
                },
                {
                    "title": "Gaza-bound aid convoy dissolved in Libya after arrests",
                    "summary": "Jerusalem Post reported a Gaza-bound convoy in Libya was dissolved after arrests.",
                    "category": "humanitarian_access_context",
                    "attribution_mode": "gaza_adjacent_context",
                    "claim_status": "gaza_adjacent_context",
                    "region_scope": "Libya / Gaza-bound convoy context",
                },
            ]
        ),
        encoding="utf-8",
    )
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-05-31",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-05-31/",
        project_root=tmp_path,
    )
    assert text.startswith("In the May 31 Gaza briefing:")
    assert "destroyed weapons storage facilities in Gaza" in text
    assert "Gaza-bound convoy in Libya was dissolved after arrests." in text
    assert "inside Gaza" not in text.lower()


def test_june_5_post_uses_filtered_gaza_topics(tmp_path: Path):
    curated = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-06-05" / "curation_manifest.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(
        json.dumps(
            [
                {
                    "title": "Friday briefing: How Gaza, Lebanon and Iran have found themselves caught in an escalation without end",
                    "summary": "UK politics | social care system. Environment | climate. Ukraine | talks. England news | planning laws.",
                    "included_in_public_summary": True,
                },
                {
                    "title": "UN agency says displacement in Lebanon rises despite ceasefire",
                    "summary": "More than 2,100 people sheltering in UNRWA facilities as hostilities continue despite truce.",
                    "included_in_public_summary": True,
                },
                {
                    "title": "Israeli strikes kill 11 people in Gaza City, medics say",
                    "summary": "Medics reported casualties after strikes in Gaza City.",
                    "included_in_public_summary": True,
                },
                {
                    "title": "Israel Supreme Court strikes down ban on Red Cross prison visits",
                    "summary": "The ICRC said it was ready to resume visits to Palestinian detainees.",
                    "included_in_public_summary": True,
                },
                {
                    "title": "Newly disclosed Israeli testimonies detail expulsions, killings during 1967 war: Report",
                    "summary": "Archival material documents expulsions and killings of Palestinians in 1967.",
                    "included_in_public_summary": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-06-05",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-06-05/",
        project_root=tmp_path,
    )
    assert text.startswith("In the June 5 Gaza briefing:")
    assert "Gaza City" in text or "Red Cross" in text or "1967" in text
    assert "satellite imagery" not in text


def test_june_7_post_uses_current_edition_artifacts_without_stale_phrases():
    root = Path(r"C:\PythonProjects\Dispatches From The Blue Fern Co")
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-06-07",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-06-07/",
        project_root=root,
    )
    assert text.startswith("In the June 7 Gaza briefing:")
    assert "https://dispatches.thebluefernco.com/gaza/editions/2026-06-07/" in text
    assert "Israeli strikes killed 10 people in Gaza on Saturday" in text or "Khan Younis strikes" in text
    assert "satellite imagery" not in text
    assert "1967" not in text


def test_june_13_post_uses_public_summary_and_public_url():
    root = Path(r"C:\PythonProjects\Dispatches From The Blue Fern Co")
    public_url = "https://dispatches.thebluefernco.com/gaza/editions/2026-06-13/"
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-06-13",
        public_url,
        project_root=root,
    )
    assert text.startswith("In the June 13 Gaza briefing:")
    assert public_url in text
    assert "Israeli attack kills one person in central Gaza's Bureij camp" in text
    assert bluesky_post.BLUESKY_GAZA_POST_FALLBACK not in text
    assert "The latest Gaza briefing is live." not in text


def test_build_uses_current_date_artifacts_and_ignores_prior_edition_artifacts(tmp_path: Path):
    prior = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-06-06"
    prior.mkdir(parents=True, exist_ok=True)
    (prior / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "title": "Prior edition stale summary",
                    "summary": "Satellite imagery showing changes on the ground and 1967 expulsions and killings.",
                    "included_in_public_summary": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    current = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-06-07"
    current.mkdir(parents=True, exist_ok=True)
    (current / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "title": "Current edition summary",
                    "summary": "Palestinians inspect the aftermath of an Israeli strike in Khan Younis.",
                    "included_in_public_summary": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    (current / "edition_manifest.json").write_text(
        json.dumps({"edition_date": "2026-06-07", "source_count": 1, "publisher_count": 1, "publishers": ["Example News"]}),
        encoding="utf-8",
    )
    site = tmp_path / "output" / "site" / "gaza" / "editions" / "2026-06-07"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html><body><p>June 7, 2026</p></body></html>", encoding="utf-8")
    text = bluesky_post.build_gaza_bluesky_post_text(
        "2026-06-07",
        "https://dispatches.thebluefernco.com/gaza/editions/2026-06-07/",
        project_root=tmp_path,
    )
    assert "Satellite imagery showing changes on the ground" not in text
    assert "1967 expulsions and killings" not in text
    assert "Khan Younis strikes" in text


def test_force_bluesky_post_cannot_bypass_stale_content_guard(monkeypatch):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")

    monkeypatch.setattr(
        bluesky_post,
        "build_gaza_bluesky_post_text",
        lambda *_args, **_kwargs: "In the June 7 Gaza briefing: satellite imagery showing changes on the ground; and newly surfaced documentation tied to 1967 expulsions and killings.",
    )
    monkeypatch.setattr(bluesky_post, "build_gaza_card_description", lambda *_args, **_kwargs: "Safe summary.")
    called = {"count": 0}

    def fake_post_json(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("network call should not happen when stale content is blocked")

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call should not happen when stale content is blocked")))
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-06-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-06-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=Path(r"C:\PythonProjects\Dispatches From The Blue Fern Co"),
        force_post=True,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "stale-content-guard-failed"
    assert called["count"] == 0


def test_gaza_post_blocks_fallback_text_with_clear_reason(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    write_current_edition_artifacts(tmp_path)

    monkeypatch.setattr(bluesky_post, "build_gaza_bluesky_post_text", lambda *_args, **_kwargs: bluesky_post.BLUESKY_GAZA_POST_FALLBACK)
    monkeypatch.setattr(bluesky_post, "build_gaza_card_description", lambda *_args, **_kwargs: "Specific verified Gaza dispatch summary.")

    def fake_post_json(*_args, **_kwargs):
        raise AssertionError("network call should not happen when fallback text is blocked")

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call should not happen when fallback text is blocked")))
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
        force_post=True,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "current-edition-public-summary-unavailable"
    assert result["stale_content_guard_status"] == "blocked"


def test_bluesky_blocks_when_artifact_date_mismatches_requested_date(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    current = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-06-07"
    current.mkdir(parents=True, exist_ok=True)
    (current / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "title": "Current edition summary",
                    "summary": "Palestinians inspect the aftermath of an Israeli strike in Khan Younis.",
                    "included_in_public_summary": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    (current / "edition_manifest.json").write_text(
        json.dumps({"edition_date": "2026-06-06", "source_count": 1, "publisher_count": 1, "publishers": ["Example News"]}),
        encoding="utf-8",
    )
    site = tmp_path / "output" / "site" / "gaza" / "editions" / "2026-06-07"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html><body><p>June 7, 2026</p></body></html>", encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "build_gaza_bluesky_post_text", lambda *_args, **_kwargs: "Safe current-edition text.")
    monkeypatch.setattr(bluesky_post, "build_gaza_card_description", lambda *_args, **_kwargs: "Safe summary.")
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-06-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-06-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "current-edition-date-mismatch"
    assert result["edition_date_verified"] is False


def test_card_description_uses_daily_specific_summary(tmp_path: Path):
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Aid convoy access shifted today after overnight negotiations."}), encoding="utf-8")
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path)
    assert description == "Aid convoy access shifted today after overnight negotiations."


def test_description_prefers_dispatch_level_social_summary(tmp_path: Path):
    run_manifest = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(
        json.dumps(
            {
                "summary": "Top story paragraph that should not be used first.",
                "dispatch_summary": {"social_summary": "Dispatch-level Gaza summary should be used."},
            }
        ),
        encoding="utf-8",
    )
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path)
    assert description == "Dispatch-level Gaza summary should be used."


def test_description_prefers_gaza_story_summary_over_first_non_gaza_item(tmp_path: Path):
    curated = tmp_path / "output" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "curation_manifest.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text(
        json.dumps(
            [
                {"summary": "Regional briefing item from Lebanon."},
                {"summary": "Gaza aid distribution resumed in multiple areas.", "region_scope": "Gaza"},
            ]
        ),
        encoding="utf-8",
    )
    edition_html = tmp_path / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "index.html"
    edition_html.parent.mkdir(parents=True, exist_ok=True)
    edition_html.write_text("<html><body><p>First paragraph from page should not win.</p></body></html>", encoding="utf-8")
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path)
    assert description == "Gaza aid distribution resumed in multiple areas."


def test_card_description_fallback_when_no_summary(tmp_path: Path):
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path)
    assert description == "Source-backed daily Gaza briefing from The Blue Fern Co."


def test_card_description_truncates_cleanly(tmp_path: Path):
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "social_summary": (
                    "A very long dispatch summary describing verified developments in Gaza and "
                    "humanitarian access constraints that continues far beyond the configured limit."
                )
            }
        ),
        encoding="utf-8",
    )
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path, max_length=80)
    assert len(description) <= 80
    assert not description.endswith(" ")
    assert description[-1].isalnum() or description[-1] in {".", "!", "?"}


def test_card_description_has_no_url_html_or_source_ids(tmp_path: Path):
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"social_summary": "<p>Update from gaza-src-2026-05-07-001 https://dispatches.thebluefernco.com/</p>"}),
        encoding="utf-8",
    )
    description = bluesky_post.build_gaza_card_description("2026-05-07", tmp_path)
    assert "http" not in description.lower()
    assert "<" not in description and ">" not in description
    assert "gaza-src-2026-05-07-001" not in description


def test_skips_when_env_disabled(monkeypatch):
    monkeypatch.delenv("BLUESKY_ENABLED", raising=False)
    monkeypatch.delenv("BLUESKY_POST_AFTER_GAZA", raising=False)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "disabled_by_env"


def test_skips_when_run_failed(monkeypatch):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=False,
        post_requested=True,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "run_failed"


def test_redacts_app_password_in_errors(monkeypatch, tmp_path: Path):
    secret = "bluefern-app-pass"
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", secret)
    write_current_edition_artifacts(tmp_path)

    def fake_post_json(_url, _payload, timeout=20.0):
        _ = timeout
        raise RuntimeError(f"authentication failed for {secret}")

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "failure"
    assert secret not in str(result.get("reason"))
    assert "<redacted>" in str(result.get("reason"))


def test_posts_success_with_external_embed_and_public_url(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    asset = tmp_path / "assets" / "gaza-social-card.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x89PNG\r\n")

    def fake_post_json(url, payload, timeout=20.0):
        _ = timeout
        assert url.endswith("/com.atproto.server.createSession")
        assert payload["identifier"] == "bluefern.test"
        return {"accessJwt": "token-123", "did": "did:plc:abc123"}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        if req.full_url.endswith("/com.atproto.repo.uploadBlob"):
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "cid"}, "mimeType": "image/png", "size": 123}})
        assert req.full_url.endswith("/com.atproto.repo.createRecord")
        assert req.headers.get("Authorization", "").startswith("Bearer ")
        body = json.loads(req.data.decode("utf-8"))
        record = body["record"]
        assert "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/" not in record["text"]
        assert "Public edition:" not in record["text"]
        embed = record["embed"]
        assert embed["$type"] == "app.bsky.embed.external"
        assert embed["external"]["uri"] == "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/"
        assert embed["external"]["description"] == "Specific verified Gaza dispatch summary."
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["post_uri"] == "at://did:plc:abc123/app.bsky.feed.post/xyz"
    assert result["embed_type"] == "app.bsky.embed.external"
    assert "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/" not in result["post_text"]
    assert "Public edition:" not in result["post_text"]
    assert "Specific verified Gaza dispatch summary." in result["post_text"]
    assert result["stale_content_guard_status"] == "passed"
    assert result["thumb_status"] == "uploaded"
    assert result["compressed_thumb"] is False
    assert isinstance(result["original_thumb_bytes"], int)
    assert isinstance(result["uploaded_thumb_bytes"], int)
    assert result["error_type"] is None
    assert result["error_message"] is None


def test_posts_text_only_when_no_embed_is_available(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")

    def fake_post_json(url, payload, timeout=20.0):
        _ = timeout
        assert url.endswith("/com.atproto.server.createSession")
        assert payload["identifier"] == "bluefern.test"
        return {"accessJwt": "token-123", "did": "did:plc:abc123"}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        assert req.full_url.endswith("/com.atproto.repo.createRecord")
        body = json.loads(req.data.decode("utf-8"))
        record = body["record"]
        assert "embed" not in record
        assert "Public edition: https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/" in record["text"]
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/text-only"})

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bluesky_post, "build_gaza_card_description", lambda *_args, **_kwargs: bluesky_post.BLUESKY_CARD_FALLBACK_DESCRIPTION)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["embed_type"] is None
    assert "Public edition: https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/" in result["post_text"]
    assert "Specific verified Gaza dispatch summary." in result["post_text"]
    assert result["stale_content_guard_status"] == "passed"


def test_thumbnail_candidate_selection_order(tmp_path: Path):
    candidates = bluesky_post._thumbnail_candidates(tmp_path)
    assert candidates[0] == tmp_path / "assets" / "gaza-social-card.jpg"
    assert candidates[1] == tmp_path / "assets" / "gaza-social-card.jpeg"
    assert candidates[2] == tmp_path / "assets" / "gaza-social-card.png"
    assert candidates[3] == tmp_path / "assets" / "dispatches-from-blue-fern-co.png"
    assert candidates[4] == tmp_path / "assets" / "bluefern.png"


def test_jpg_social_card_preferred_over_png(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    jpg = b"jpg-bytes"
    png = b"png-bytes"
    (assets / "gaza-social-card.jpg").write_bytes(jpg)
    (assets / "gaza-social-card.png").write_bytes(png)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    seen = {"upload_payload": None}

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        if req.full_url.endswith("/com.atproto.repo.uploadBlob"):
            seen["upload_payload"] = req.data
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "cid"}, "mimeType": "image/jpeg", "size": len(jpg)}})
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert seen["upload_payload"] == jpg


def test_jpeg_extension_supported(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    assets = tmp_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    jpeg = b"jpeg-bytes"
    (assets / "gaza-social-card.jpeg").write_bytes(jpeg)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    seen = {"upload_payload": None}

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        if req.full_url.endswith("/com.atproto.repo.uploadBlob"):
            seen["upload_payload"] = req.data
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "cid"}, "mimeType": "image/jpeg", "size": len(jpeg)}})
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert seen["upload_payload"] == jpeg


def test_no_thumbnail_reports_status_but_post_succeeds(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")

    def fake_post_json(_url, _payload, timeout=20.0):
        _ = timeout
        return {"accessJwt": "token-123", "did": "did:plc:abc123"}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"}).encode("utf-8")

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        assert req.full_url.endswith("/com.atproto.repo.createRecord")
        payload = json.loads(req.data.decode("utf-8"))
        assert "thumb" not in payload["record"]["embed"]["external"]
        return FakeResponse()

    monkeypatch.setattr(bluesky_post, "_post_json", fake_post_json)
    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["thumb_status"] == "no_thumbnail"


def test_under_limit_image_uploads_directly(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    raw = b"\x89PNG\r\nsmall-image"
    (tmp_path / "assets" / "gaza-social-card.png").write_bytes(raw)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    seen = {"upload_payload": None}

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        if req.full_url.endswith("/com.atproto.repo.uploadBlob"):
            seen["upload_payload"] = req.data
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "cid"}, "mimeType": "image/png", "size": len(raw)}})
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["thumb_status"] == "uploaded"
    assert result["compressed_thumb"] is False
    assert seen["upload_payload"] == raw


def test_over_limit_image_is_compressed_before_upload(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    big = b"a" * (bluesky_post.BLUESKY_BLOB_MAX_BYTES + 1000)
    compressed = b"j" * 200000
    (tmp_path / "assets" / "gaza-social-card.png").write_bytes(big)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})
    monkeypatch.setattr(bluesky_post, "_compress_thumb_to_jpeg", lambda _bytes: compressed)

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    seen = {"upload_payload": None}

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        if req.full_url.endswith("/com.atproto.repo.uploadBlob"):
            seen["upload_payload"] = req.data
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "cid"}, "mimeType": "image/jpeg", "size": len(compressed)}})
        return FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["thumb_status"] == "uploaded_compressed"
    assert result["compressed_thumb"] is True
    assert result["original_thumb_bytes"] == len(big)
    assert result["uploaded_thumb_bytes"] == len(compressed)
    assert seen["upload_payload"] == compressed


def test_compression_failure_posts_without_thumbnail(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    big = b"a" * (bluesky_post.BLUESKY_BLOB_MAX_BYTES + 1000)
    (tmp_path / "assets" / "gaza-social-card.png").write_bytes(big)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})
    monkeypatch.setattr(bluesky_post, "_compress_thumb_to_jpeg", lambda _bytes: None)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"}).encode("utf-8")

    def fake_urlopen(req, timeout=20.0):
        _ = timeout
        assert req.full_url.endswith("/com.atproto.repo.createRecord")
        payload = json.loads(req.data.decode("utf-8"))
        assert "thumb" not in payload["record"]["embed"]["external"]
        return FakeResponse()

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["thumb_status"] == "skipped_too_large"


def test_over_limit_never_causes_full_post_failure(monkeypatch, tmp_path: Path):
    secret = "super-secret"
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", secret)
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    big = b"a" * (bluesky_post.BLUESKY_BLOB_MAX_BYTES + 1000)
    (tmp_path / "assets" / "gaza-social-card.png").write_bytes(big)
    manifest_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})
    monkeypatch.setattr(bluesky_post, "_compress_thumb_to_jpeg", lambda _bytes: None)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"}).encode("utf-8")

    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda _req, timeout=20.0: FakeResponse())
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    assert result["thumb_status"] == "skipped_too_large"
    assert secret not in json.dumps(result)


def test_http_error_returns_safe_reason(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pass123")
    write_current_edition_artifacts(tmp_path)

    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    def fake_urlopen(_req, timeout=20.0):
        _ = timeout
        raise error.HTTPError("https://bsky.social", 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b""))

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "failure"
    assert result["reason"] == "http_error_401"
    assert result["error_type"] is None
    assert result["error_message"] is None


def test_http_400_json_body_is_included_safely(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pass123")
    write_current_edition_artifacts(tmp_path)
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    def fake_urlopen(_req, timeout=20.0):
        _ = timeout
        body = json.dumps({"error": "InvalidRequest", "message": "invalid embed shape"})
        raise error.HTTPError("https://bsky.social", 400, "Bad Request", hdrs=None, fp=io.BytesIO(body.encode("utf-8")))

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "failure"
    assert result["reason"] == "http_error_400: InvalidRequest: invalid embed shape"
    assert result["error_type"] == "InvalidRequest"
    assert result["error_message"] == "invalid embed shape"


def test_http_400_plain_text_body_is_included_safely_and_truncated(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pass123")
    write_current_edition_artifacts(tmp_path)
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    plain = "invalid field length " * 40

    def fake_urlopen(_req, timeout=20.0):
        _ = timeout
        raise error.HTTPError("https://bsky.social", 400, "Bad Request", hdrs=None, fp=io.BytesIO(plain.encode("utf-8")))

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "failure"
    assert result["reason"].startswith("http_error_400: ")
    assert len(result["reason"]) <= len("http_error_400: ") + bluesky_post.MAX_HTTP_ERROR_DETAIL_LENGTH


def test_http_400_redacts_bearer_tokens_and_password(monkeypatch, tmp_path: Path):
    secret = "super-secret-pass"
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", secret)
    write_current_edition_artifacts(tmp_path)
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    body = {
        "error": "InvalidRequest",
        "message": f"Authorization Bearer abc.def.ghi failed for {secret}",
        "accessJwt": "abc123",
        "refreshJwt": "xyz789",
    }

    def fake_urlopen(_req, timeout=20.0):
        _ = timeout
        raise error.HTTPError("https://bsky.social", 400, "Bad Request", hdrs=None, fp=io.BytesIO(json.dumps(body).encode("utf-8")))

    monkeypatch.setattr(bluesky_post.request, "urlopen", fake_urlopen)
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    reason = str(result["reason"])
    assert secret not in reason
    assert "abc.def.ghi" not in reason
    assert "Bearer <redacted>" in reason
    assert "<redacted>" in reason


def test_first_successful_post_writes_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    run_manifest = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"})
        if req.full_url.endswith("/com.atproto.repo.createRecord")
        else (_ for _ in ()).throw(AssertionError("unexpected blob upload")),
    )
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json"
    assert receipt.exists()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["post_uri"] == "at://did:plc:abc123/app.bsky.feed.post/xyz"
    assert payload["dispatch_slug"] == "gaza"


def test_second_run_skips_using_existing_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    write_current_edition_artifacts(tmp_path)
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "dispatch_slug": "gaza",
                "edition_date": "2026-05-07",
                "public_url": "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
                "post_uri": "at://did:plc:abc123/app.bsky.feed.post/existing",
                "status": "success",
                "embed_type": "app.bsky.embed.external",
                "card_title": "Dispatches from Gaza - May 7, 2026",
                "card_description": "Existing desc",
                "thumb_status": "uploaded",
            }
        ),
        encoding="utf-8",
    )
    called = {"count": 0}
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: called.__setitem__("count", called["count"] + 1))
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "skipped_existing_receipt"
    assert result["post_uri"] == "at://did:plc:abc123/app.bsky.feed.post/existing"
    assert called["count"] == 0


def test_force_bluesky_post_ignores_existing_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "status": "success",
                "public_url": "https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
                "post_uri": "at://old",
            }
        ),
        encoding="utf-8",
    )
    run_manifest = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda req, timeout=20.0: FakeResponse({"uri": "at://did:plc:abc123/app.bsky.feed.post/new"})
        if req.full_url.endswith("/com.atproto.repo.createRecord")
        else (_ for _ in ()).throw(AssertionError("unexpected blob upload")),
    )
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
        force_post=True,
    )
    assert result["status"] == "success"
    assert result["post_uri"] == "at://did:plc:abc123/app.bsky.feed.post/new"


def test_receipt_mismatch_public_url_does_not_skip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({"status": "success", "public_url": "https://dispatches.thebluefernco.com/gaza/editions/DIFFERENT/", "post_uri": "at://old"}),
        encoding="utf-8",
    )
    run_manifest = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps({"uri": "at://did:plc:abc123/app.bsky.feed.post/new"}).encode("utf-8")

    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda _req, timeout=20.0: FakeResponse())
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"


def test_failed_post_does_not_write_success_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    write_current_edition_artifacts(tmp_path)
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})
    monkeypatch.setattr(
        bluesky_post.request,
        "urlopen",
        lambda _req, timeout=20.0: (_ for _ in ()).throw(error.HTTPError("https://bsky.social", 400, "Bad Request", hdrs=None, fp=io.BytesIO(b"bad"))),
    )
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "failure"
    receipt = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json"
    assert not receipt.exists()


def test_receipt_never_contains_secrets(monkeypatch, tmp_path: Path):
    secret = "super-secret-password"
    monkeypatch.setenv("BLUESKY_ENABLED", "1")
    monkeypatch.setenv("BLUESKY_POST_AFTER_GAZA", "1")
    monkeypatch.setenv("BLUESKY_HANDLE", "bluefern.test")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", secret)
    run_manifest = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_manifest.write_text(json.dumps({"social_summary": "Specific verified Gaza dispatch summary."}), encoding="utf-8")
    monkeypatch.setattr(bluesky_post, "_post_json", lambda *_args, **_kwargs: {"accessJwt": "token-123", "did": "did:plc:abc123"})

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def read(self):
            return json.dumps({"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz"}).encode("utf-8")

    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda _req, timeout=20.0: FakeResponse())
    result = bluesky_post.maybe_post_gaza_dispatch_to_bluesky(
        edition_date="2026-05-07",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-05-07/",
        run_succeeded=True,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["status"] == "success"
    receipt_text = (tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "bluesky_post_receipt.json").read_text(encoding="utf-8")
    assert secret not in receipt_text
    assert "accessJwt" not in receipt_text
    assert "refreshJwt" not in receipt_text
    assert "Authorization" not in receipt_text
    assert "Bearer " not in receipt_text
