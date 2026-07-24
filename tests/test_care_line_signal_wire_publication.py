from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bluefern_dispatches.generator import build_site
from bluefern_dispatches.universal_events.care_line_signal_wire import (
    EXPECTED_EVENT_IDS,
    READY_RECORD_IDS,
    SignalWireEvent,
    _repair_public_text,
    _render_event_page,
    _render_feed,
    _render_index,
    build_care_line_signal_wire_publication,
)


REPO = Path(__file__).resolve().parents[1]


def _copy_assets(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "assets", work / "assets")


def _copy_care_line_data(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "data" / "dispatches" / "care-line", work / "data" / "dispatches" / "care-line")


def _copy_phase14e(repo: Path, work: Path) -> None:
    shutil.copytree(
        repo / "data" / "universal_events" / "shadow" / "care-line" / "phase14e-universal-events",
        work / "data" / "universal_events" / "shadow" / "care-line" / "phase14e-universal-events",
    )


def _prepare_work_root(tmp_path: Path) -> Path:
    work = tmp_path / "repo"
    _copy_assets(REPO, work)
    _copy_care_line_data(REPO, work)
    _copy_phase14e(REPO, work)
    return work


def _sample_utf8_event() -> SignalWireEvent:
    raw_summary = _repair_public_text("Childrenâ€™s Hospital — Renée will remain in network for some UnitedHealthcare plans through August 6.")
    raw_title = _repair_public_text("Childrenâ€™s network-access update — Renée")
    raw_facility = _repair_public_text("Childrenâ€™s Hospital — Renée")
    return SignalWireEvent(
        event_id="event_roundtrip",
        candidate_id="candidate_roundtrip",
        producer_record_id="care-line-direct-discovery-roundtrip",
        source_item_id="source_item_roundtrip",
        source_url="https://example.com/source",
        publisher="Example Health",
        source_title="Childrenâ€™s network-access update — Renée",
        source_publication_date="2026-07-24",
        source_publication_at="2026-07-24T00:00:00Z",
        announcement_date="2026-07-24",
        effective_date="2026-08-06",
        public_published_at="2026-07-24T05:14:30.376748+00:00",
        system_discovered_at="2026-07-24T05:14:30.376748Z",
        verification_at="2026-07-24T05:14:30.376748Z",
        event_type="service_restoration",
        status="completed",
        domain="healthcare_access",
        title=raw_title,
        public_label="Temporary network-access extension",
        public_summary=raw_summary,
        why_it_matters="It keeps some UnitedHealthcare patients in network through August 6, delaying a potential disruption.",
        revision_status="corrected",
        taxonomy_gap_note=(
            "Underlying schema value preserved as service_restoration; public label and summary were corrected because "
            "the evidence supports a temporary in-network extension through a deadline, not a permanent restoration."
        ),
        service_line="other",
        service_line_normalized="",
        facility_name=raw_facility,
        city="Greenville",
        state="NC",
        country_code="US",
        evidence_text=raw_summary,
        evidence_provenance_type="reviewer_transcribed",
        evidence_source_field="article_body",
        evidence_source_artifact="canonical_publisher_page",
        review_decision_id="review-1",
        review_reason="temporary extension",
        supersedes_evidence_decision_id="decision-0",
        record_fingerprint="record-fingerprint",
        review_packet_fingerprint="packet-fingerprint",
        last_updated_at="2026-07-24T05:14:30.376748+00:00",
    )


def test_phase14h_publication_renders_two_events_and_hides_internal_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare_work_root(tmp_path)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    result = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )

    assert result["ok"] is True
    assert all(url.startswith("https://dispatches.thebluefernco.com/") for url in result["public_urls"])
    assert "https://dispatches.thebluefernco.com/signals/" in result["public_urls"]
    assert "https://dispatches.thebluefernco.com/signals/feed.xml" in result["public_urls"]
    assert "https://dispatches.thebluefernco.com/care-line/signals/feed.xml" in result["public_urls"]

    site_root = work / "output" / "site"
    signals_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    signals_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    care_line_feed = (site_root / "care-line" / "signals" / "feed.xml").read_text(encoding="utf-8")
    first_event = (site_root / "events" / "event_3b4ad4e528e48744" / "index.html").read_text(encoding="utf-8")
    second_event = (site_root / "events" / "event_a12dae614b86cfa9" / "index.html").read_text(encoding="utf-8")

    assert "Care Line Signal Wire" in signals_index
    assert "event_3b4ad4e528e48744" in signals_index
    assert "event_a12dae614b86cfa9" in signals_index
    assert "Service expansion" in signals_index
    assert "Temporary network-access extension" in signals_index
    assert "service_restoration" not in signals_index

    for page in (signals_index, signals_feed, care_line_feed, first_event, second_event):
        assert "Source candidate" not in page
        assert "Producer record" not in page
        assert "Record fingerprint" not in page
        assert "Review packet fingerprint" not in page
        assert "children?s" not in page.lower()
        assert "service_restoration" not in page

    assert "Children’s" in first_event
    assert "UnitedHealthcare" in second_event
    assert "Temporary network-access extension" in second_event
    assert "This signal was published from a reviewed source record with preserved source lineage." in second_event

    signals_root = ET.fromstring(signals_feed)
    care_line_root = ET.fromstring(care_line_feed)
    signal_items = signals_root.findall("./channel/item")
    care_line_items = care_line_root.findall("./channel/item")
    assert len(signal_items) == 2
    assert len(care_line_items) == 2
    assert [item.findtext("guid") for item in signal_items] == [
        "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/",
        "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/",
    ]
    assert signals_feed.count("<item>") == 2
    assert care_line_feed.count("<item>") == 2

    manifest_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json"
    lineage_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-source-to-event-lineage-report.json"
    duplicate_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-duplicate-idempotency-report.json"
    drafts_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-bluesky-drafts.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    duplicate = json.loads(duplicate_path.read_text(encoding="utf-8"))
    drafts = json.loads(drafts_path.read_text(encoding="utf-8"))

    assert set(manifest["selected_record_ids"]) == READY_RECORD_IDS
    assert set(manifest["event_ids"]) == EXPECTED_EVENT_IDS
    assert sorted(manifest["deferred_record_ids"]) == [
        "care-line-direct-discovery-196621161639f9f2",
        "care-line-direct-discovery-9543c43464dbd7d4",
    ]
    assert manifest["taxonomy_gap_notes"] == [
        {
            "event_id": "event_a12dae614b86cfa9",
            "public_label": "Temporary network-access extension",
            "event_type": "service_restoration",
            "note": (
                "Underlying schema value preserved as service_restoration; public label and summary were corrected because "
                "the evidence supports a temporary in-network extension through a deadline, not a permanent restoration."
            ),
        }
    ]
    assert len(lineage["events"]) == 2
    assert lineage["events"][0]["public_label"] in {"Service expansion", "Temporary network-access extension"}
    assert duplicate["event_ids_unique"] is True
    assert duplicate["rerun_idempotent"] is True
    assert len(drafts["drafts"]) == 2
    assert all(len(draft["text"]) < 300 for draft in drafts["drafts"])
    assert any("Children’s" in draft["text"] for draft in drafts["drafts"])


def test_phase14h_utf8_round_trip_rendering(tmp_path: Path) -> None:
    event = _sample_utf8_event()
    html_path = tmp_path / "event.html"
    feed_path = tmp_path / "feed.xml"
    index_path = tmp_path / "index.html"

    html_path.write_text(_render_event_page(event), encoding="utf-8")
    feed_path.write_text(
        _render_feed(
            [event],
            title="Care Line Signal Wire",
            link="https://dispatches.thebluefernco.com/signals/",
            description="UTF-8 regression feed",
        ),
        encoding="utf-8",
    )
    index_path.write_text(_render_index([event]), encoding="utf-8")

    html_text = html_path.read_text(encoding="utf-8")
    feed_text = feed_path.read_text(encoding="utf-8")
    index_text = index_path.read_text(encoding="utf-8")

    assert "Children’s" in html_text
    assert "UnitedHealthcare" in html_text
    assert "Renée" in html_text
    assert "—" in html_text
    assert "Children?s" not in html_text
    assert "Temporary network-access extension" in index_text
    assert "Children’s" in index_text
    assert "Children’s" in feed_text
    assert "UnitedHealthcare" in feed_text
    assert "Renée" in feed_text
    assert "—" in feed_text
    assert "Children?s" not in feed_text

    parsed = ET.fromstring(feed_text)
    assert parsed.findtext("./channel/title") == "Care Line Signal Wire"
    assert parsed.findall("./channel/item")[0].findtext("guid") == "https://dispatches.thebluefernco.com/events/event_roundtrip/"


def test_phase14h_publication_fails_closed_when_evidence_is_missing(tmp_path: Path) -> None:
    work = _prepare_work_root(tmp_path)
    reviewed_path = work / "data" / "dispatches" / "care-line" / "reviewed" / "2026-07-22" / "reviewed_records.json"
    payload = json.loads(reviewed_path.read_text(encoding="utf-8"))
    records = payload["records"]
    for row in records:
        if row["producer_record_id"] in READY_RECORD_IDS:
            row["supporting_passage"] = ""
            row["evidence_valid_for_universal_event"] = False
    reviewed_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing faithful evidence passage"):
        build_care_line_signal_wire_publication(work, generated_at="2026-07-24T00:00:00Z")


def test_phase14h_publication_is_deterministic_on_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare_work_root(tmp_path)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    first = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    site_root = work / "output" / "site"
    first_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    first_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    first_manifest = (work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json").read_text(encoding="utf-8")
    second = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    second_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    second_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    second_manifest = (work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json").read_text(encoding="utf-8")

    assert first["ok"] is True
    assert second["ok"] is True

    assert first_index == second_index
    assert first_feed == second_feed
    assert first_manifest == second_manifest
