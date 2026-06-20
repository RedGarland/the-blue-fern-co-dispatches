import json
from pathlib import Path

from bluefern_dispatches.cascadia_detention_watch import (
    build_detention_watch,
    render_html,
    unsupported_public_label_hits,
    validate_detention_watch_artifacts,
    validate_payload,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "cascadia" / "detention_watch" / "baseline_2026-05-26.json"


def _build(tmp_path: Path):
    return build_detention_watch(tmp_path, "2026-05-26", input_path=FIXTURE_PATH)


def _approved_update_payload() -> dict:
    return {
        "date": "2026-06-02",
        "title": "Cascadia Detention Watch Update / June 2, 2026",
        "summary": "Weekly update with approved source-backed claims only.",
        "sources": [
            {
                "source_id": "src-update-local-media",
                "title": "Local update source",
                "url": "https://example.org/local-update",
                "source_type": "media_reporting",
                "publisher": "Example Local Media",
                "published_at": "2026-06-02",
            }
        ],
        "changed_this_week": [
            {
                "item": "A source-backed update item for this week.",
                "claim_class": "reported",
                "source_refs": ["src-update-local-media"],
            }
        ],
        "current_indicators_delta": [
            {
                "indicator": "Court docket activity",
                "basis": "Additional tracked filing.",
                "claim_class": "documented",
                "source_refs": ["src-update-local-media"],
            }
        ],
        "timeline_additions": [
            {
                "date": "2026-06-02",
                "event": "New timeline entry from approved update.",
                "claim_class": "documented",
                "source_refs": ["src-update-local-media"],
            }
        ],
        "claims": [
            {
                "text": "New documented claim from approved update.",
                "claim_class": "documented",
                "source_refs": ["src-update-local-media"],
            },
            {
                "text": "New reported claim from approved update.",
                "claim_class": "reported",
                "source_refs": ["src-update-local-media"],
            },
            {
                "text": "New alleged claim from approved update.",
                "claim_class": "alleged",
                "source_refs": ["src-update-local-media"],
            },
        ],
        "open_questions": [
            {
                "text": "What changed in filing cadence after this update?",
                "label": "open_question",
            }
        ],
        "method_note": "Update claims preserve documented/reported/alleged/unknown distinctions.",
        "review_status": "approved",
    }


def test_build_detention_watch_writes_required_paths(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    assert (tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "index.html").exists()
    assert (tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").exists()
    assert (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").exists()
    assert (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "archive.html").exists()
    assert (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").exists()
    assert (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "source_table.html").exists()


def test_claim_validation_fails_when_documented_claim_has_no_source_ref(tmp_path: Path):
    _build(tmp_path)
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["documented_facts"][0]["source_refs"] = []
    errors = validate_payload(payload)
    assert any("documented claim missing source_refs" in error for error in errors)


def test_public_html_labels_documented_reported_and_alleged_distinctly(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "Documented:" in html
    assert "Reported:" in html
    assert "Alleged:" in html
    assert "<h2>Open questions</h2>" in html
    assert "<h2>Detention Watch Summary</h2>" in html
    assert "Last checked:" in html
    assert "Latest source update:" in html
    assert "<h2>Sources</h2>" in html
    assert "<h2>Method note</h2>" in html
    assert 'href="/cascadia/"' in html
    assert 'href="/cascadia/map/"' in html


def test_landing_last_checked_uses_latest_record_timestamp(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    landing = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    edition = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "Last checked: Not listed" not in landing
    marker = "<strong>Last checked:</strong> "
    landing_value = landing.split(marker, 1)[1].split("</p>", 1)[0].strip()
    edition_value = edition.split(marker, 1)[1].split("</p>", 1)[0].strip()
    assert landing_value
    assert landing_value == edition_value
    assert "T17:" not in landing_value


def test_record_page_last_checked_falls_back_to_not_listed_when_no_usable_timestamp():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for key in ("last_checked", "checked_at", "retrieved_at", "generated_at", "record_generated_at"):
        payload.pop(key, None)
    html = render_html(payload, is_update=False)
    assert "<strong>Last checked:</strong> Not listed" in html


def test_expected_next_review_is_written_and_rendered(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    payload_path = tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload.get("expected_next_review") == "2026-06-02"
    landing = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    edition = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "Next review expected:" in landing
    assert "Next review expected:" in edition


def test_unresolved_source_ref_fails_validation(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    payload_path = tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["timeline"][0]["source_refs"] = ["missing-source-id"]
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    assert any("source_ref does not resolve to root source" in error for error in validate_detention_watch_artifacts(tmp_path, "2026-05-26"))


def test_manifest_claim_counts_match_payload_derived_counts(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    manifest_path = tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "edition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("claim_instance_count_by_class") == {
        "documented": 6,
        "unknown": 2,
        "reported": 1,
        "alleged": 1,
    }


def test_raw_iso_timestamps_not_rendered_in_public_html(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    landing = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    edition = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    payload_path = tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    raw_last_checked = str(payload.get("last_checked") or "")
    assert raw_last_checked
    assert raw_last_checked not in landing
    assert raw_last_checked not in edition

def test_current_indicators_render_meaningful_text(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "<h2>Monitoring checklist</h2>" in html
    assert "Official records updates" in html
    assert "Court docket activity" in html
    assert "Conditions claims" in html
    assert " | monitoring | " not in html.lower()
    assert " | unknown | " not in html.lower()


def test_public_html_has_no_empty_claim_bullets_or_double_periods(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "Documented: ." not in html
    assert "Reported: ." not in html
    assert "Alleged: ." not in html
    assert ".. Sources:" not in html


def test_cascadia_nav_label_is_not_detention_watch(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert '<a href="/cascadia/">Cascadia Detention Watch</a>' not in html
    assert 'href="/cascadia/"' in html


def test_geo_source_type_is_not_media_reporting(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    geo = [source for source in payload["sources"] if source.get("source_id") == "src-geo-facility-page"][0]
    assert geo["source_type"] != "media_reporting"


def test_baseline_weak_sentence_replaced_in_rendered_html(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "Public-source reporting and legal-organization updates continue to reference Tacoma detention operations for regional monitoring." not in html
    assert "No new weekly development is asserted in this baseline record." in html


def test_detention_watch_logo_present_on_index_and_edition(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    edition_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    index_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    assert "cascadia-detention-logo.png" in edition_html
    assert 'alt="Cascadia Detention Watch"' in edition_html
    assert "cascadia-detention-logo.png" in index_html
    assert 'alt="Cascadia Detention Watch"' in index_html
    assert 'href="/cascadia/detention-watch/editions/2026-05-26/"' in index_html
    assert "Open latest record" in index_html
    assert "Open the 2026-05-26 starting record" not in index_html
    assert "View source table" in index_html
    assert "//assets/cascadia-detention-logo.png" not in edition_html
    assert "//assets/cascadia-detention-logo.png" not in index_html


def test_claim_validation_fails_when_source_id_reference_is_unknown(tmp_path: Path):
    _build(tmp_path)
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["timeline"][0]["source_refs"] = ["src-does-not-exist"]
    errors = validate_payload(payload)
    assert any("unknown source_id" in error for error in errors)


def test_reported_and_alleged_claims_without_sources_fail_validation(tmp_path: Path):
    _build(tmp_path)
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["what_changed_this_week"][1]["source_refs"] = []
    payload["reported_allegations"][0]["source_refs"] = []
    errors = validate_payload(payload)
    assert any("reported claim missing source_refs" in error for error in errors)
    assert any("alleged claim missing source_refs" in error for error in errors)


def test_unknown_claim_and_open_question_without_sources_are_allowed(tmp_path: Path):
    _build(tmp_path)
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["facility_profile"]["notes"][1]["source_refs"] = []
    payload["open_questions"][0]["source_refs"] = []
    payload["open_questions"][0]["label"] = "open_question"
    errors = validate_payload(payload)
    assert errors == []


def test_open_question_without_label_and_without_sources_fails(tmp_path: Path):
    _build(tmp_path)
    payload_path = tmp_path / "output" / "dispatches" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "detention_watch_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["open_questions"][0] = {"text": "Unsourced question without label"}
    errors = validate_payload(payload)
    assert any("open question without source_refs must be labeled" in error for error in errors)


def test_public_html_has_no_unsupported_inflammatory_labels(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert unsupported_public_label_hits(html) == []


def test_public_wording_is_reader_facing_and_avoids_internal_phrases(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    edition_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    index_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    blocked = [
        "source-backed monitoring baseline",
        "baseline dossier",
        "verified weekly operational change",
        "source categories and monitoring fields",
        "fixed facts",
    ]
    for phrase in blocked:
        assert phrase not in edition_html.lower()
        assert phrase not in index_html.lower()
    assert "starting record" in edition_html.lower()
    assert "sourced facts" in edition_html.lower()
    assert "reported concerns" in edition_html.lower()
    assert "open questions" in edition_html.lower()
    assert "We separate what is documented, what is reported by cited sources, and what remains unknown." in edition_html


def test_default_build_uses_latest_available_baseline_file(tmp_path: Path):
    data_path = tmp_path / "data" / "dispatches" / "cascadia" / "detention_watch" / "baseline_2026-05-26.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    result = build_detention_watch(tmp_path)
    assert result["ok"] is True
    assert result["edition_date"] == "2026-05-26"


def test_update_with_unapproved_review_status_is_refused(tmp_path: Path):
    update = _approved_update_payload()
    update["review_status"] = "candidate"
    update_path = tmp_path / "update_2026-06-02.json"
    update_path.write_text(json.dumps(update), encoding="utf-8")
    result = build_detention_watch(tmp_path, "2026-05-26", input_path=FIXTURE_PATH, update_path=update_path)
    assert result["ok"] is False
    assert any("review_status must be approved" in error for error in result["errors"])


def test_approved_update_renders_dated_edition(tmp_path: Path):
    update_path = tmp_path / "update_2026-06-02.json"
    update_path.write_text(json.dumps(_approved_update_payload()), encoding="utf-8")
    result = build_detention_watch(tmp_path, "2026-05-26", input_path=FIXTURE_PATH, update_path=update_path)
    assert result["ok"] is True
    html_path = tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-06-02" / "index.html"
    assert html_path.exists()


def test_source_merge_conflicting_duplicate_source_ids_fail_validation(tmp_path: Path):
    update = _approved_update_payload()
    update["sources"] = [
        {
            "source_id": "src-ice-facility-page",
            "title": "Conflicting title",
            "url": "https://different.example.org/conflict",
            "source_type": "official_record",
            "publisher": "Conflict Publisher",
            "published_at": "2026-06-02",
        }
    ]
    update["changed_this_week"][0]["source_refs"] = ["src-ice-facility-page"]
    update["current_indicators_delta"][0]["source_refs"] = ["src-ice-facility-page"]
    update["timeline_additions"][0]["source_refs"] = ["src-ice-facility-page"]
    for claim in update["claims"]:
        claim["source_refs"] = ["src-ice-facility-page"]
    update_path = tmp_path / "update_2026-06-02.json"
    update_path.write_text(json.dumps(update), encoding="utf-8")
    result = build_detention_watch(tmp_path, "2026-05-26", input_path=FIXTURE_PATH, update_path=update_path)
    assert result["ok"] is False
    assert any("conflicting duplicate source_id" in error for error in result["errors"])


def test_update_timeline_and_claim_sections_render_as_new_content(tmp_path: Path):
    update_path = tmp_path / "update_2026-06-02.json"
    update_path.write_text(json.dumps(_approved_update_payload()), encoding="utf-8")
    result = build_detention_watch(tmp_path, "2026-05-26", input_path=FIXTURE_PATH, update_path=update_path)
    assert result["ok"] is True
    html = (
        tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-06-02" / "index.html"
    ).read_text(encoding="utf-8")
    assert "<h2>New timeline entries</h2>" in html
    assert "New timeline entry from approved update." in html
    assert "<h2>New documented facts</h2>" in html
    assert "<h2>New reported allegations</h2>" in html
    assert "<h2>New alleged claims</h2>" in html
    assert "The Tacoma detention facility is publicly listed and referenced in official and public source records." not in html


def test_index_points_to_latest_approved_update(tmp_path: Path):
    data_root = tmp_path / "data" / "dispatches" / "cascadia" / "detention_watch"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "baseline_2026-05-26.json").write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    update = _approved_update_payload()
    update["date"] = "2026-06-03"
    (data_root / "update_2026-06-03.json").write_text(json.dumps(update), encoding="utf-8")
    result = build_detention_watch(tmp_path)
    assert result["ok"] is True
    index_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    assert '/cascadia/detention-watch/editions/2026-06-03/' in index_html


def test_detention_watch_source_table_has_required_columns_and_cited_sources(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    source_table = (
        tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "source_table.html"
    ).read_text(encoding="utf-8")
    assert "<thead>" in source_table
    assert "<tbody>" in source_table
    assert '<th scope="col">Source</th>' in source_table
    assert '<th scope="col">Source type</th>' in source_table
    assert '<th scope="col">Publisher / agency</th>' in source_table
    assert '<th scope="col">What this source supports</th>' in source_table
    assert '<th scope="col">Verification status</th>' in source_table
    assert '<th scope="col">Last checked</th>' in source_table
    assert '<th scope="row"><a href="' in source_table
    assert "ICE detention facilities listing" in source_table
    assert "Court records search: Tacoma immigration detention references" in source_table


def test_detention_watch_index_has_no_detention_rss_link(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    index_html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "index.html").read_text(encoding="utf-8")
    assert "detention-watch/rss.xml" not in index_html


def test_detention_watch_artifact_validation_passes_for_baseline(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    assert validate_detention_watch_artifacts(tmp_path, "2026-05-26") == []


def test_facility_profile_table_has_accessibility_scopes(tmp_path: Path):
    result = _build(tmp_path)
    assert result["ok"] is True
    edition = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    assert "<thead>" in edition
    assert "<tbody>" in edition
    assert '<th scope="col">Field</th>' in edition
    assert '<th scope="row">Facility name</th>' in edition

