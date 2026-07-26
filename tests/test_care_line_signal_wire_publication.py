from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
import xml.etree.ElementTree as ET
import struct
import zlib
from pathlib import Path

import pytest

from bluefern_dispatches.generator import build_site
import bluefern_dispatches.universal_events.care_line_signal_wire as care_line_signal_wire
from bluefern_dispatches.universal_events.care_line_signal_wire import (
    EXPECTED_EVENT_IDS,
    READY_RECORD_IDS,
    SignalWireEvent,
    _repair_public_text,
    _render_event_page,
    _render_feed,
    _render_index,
    _publication_content_hash,
    _resolve_publication_state,
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


def _png_info(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        chunk_type = data[offset : offset + 4]
        offset += 4
        chunk = data[offset : offset + length]
        offset += length
        offset += 4  # crc
        if chunk_type == b"IHDR":
            width, height = struct.unpack_from(">II", chunk, 0)
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break
    assert width is not None and height is not None
    raw = zlib.decompress(bytes(idat))
    bytes_per_pixel = 4
    row_bytes = 1 + width * bytes_per_pixel
    assert len(raw) == height * row_bytes
    return width, height


def _png_pixels(data: bytes) -> tuple[int, int, bytes]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = None
    color_type = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        chunk_type = data[offset : offset + 4]
        offset += 4
        chunk = data[offset : offset + length]
        offset += length
        offset += 4
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack_from(">IIBBBBB", chunk, 0)
            assert bit_depth == 8
            assert color_type in {2, 6}
            assert compression == 0
            assert filter_method == 0
            assert interlace == 0
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break
    assert width is not None and height is not None and color_type is not None
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(bytes(idat))
    row_bytes = 1 + width * channels
    assert len(raw) == height * row_bytes
    decoded = bytearray(height * width * channels)
    prev_row = bytearray(width * channels)
    src = 0
    dst = 0
    for _row in range(height):
        filter_type = raw[src]
        src += 1
        row = bytearray(raw[src : src + width * channels])
        src += width * channels
        recon = bytearray(width * channels)
        if filter_type == 0:
            recon[:] = row
        elif filter_type == 1:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                recon[i] = (value + left) & 0xFF
        elif filter_type == 2:
            for i, value in enumerate(row):
                recon[i] = (value + prev_row[i]) & 0xFF
        elif filter_type == 3:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                up = prev_row[i]
                recon[i] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                up = prev_row[i]
                up_left = prev_row[i - channels] if i >= channels else 0
                recon[i] = (value + _paeth(left, up, up_left)) & 0xFF
        else:
            raise AssertionError(f"unsupported PNG filter {filter_type}")
        decoded[dst : dst + len(recon)] = recon
        dst += len(recon)
        prev_row = recon
    return width, height, bytes(decoded)


def _rgba_at(decoded: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * width + x) * 4
    return decoded[offset], decoded[offset + 1], decoded[offset + 2], decoded[offset + 3]


def _brightness(pixel: tuple[int, int, int, int]) -> int:
    return (pixel[0] + pixel[1] + pixel[2]) // 3


def _rgb_distance(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1]) + abs(left[2] - right[2])


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
    template_path = REPO / "assets" / "care-line-social-card-template.png"
    assert template_path.exists()
    assert _png_info(template_path.read_bytes()) == (1200, 630)

    def _fail_if_procedural_renderer_is_used(*_: object, **__: object) -> bytes:
        raise AssertionError("approved Care Line assets must be copied, not procedurally rendered")

    monkeypatch.setattr(care_line_signal_wire, "render_social_card_png_bytes", _fail_if_procedural_renderer_is_used)

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
    first_card = (site_root / "events" / "event_3b4ad4e528e48744" / "social-card.png").read_bytes()
    second_card = (site_root / "events" / "event_a12dae614b86cfa9" / "social-card.png").read_bytes()
    first_source_card = (work / "assets" / "care-line" / "event_3b4ad4e528e48744.png").read_bytes()
    second_source_card = (work / "assets" / "care-line" / "event_a12dae614b86cfa9.png").read_bytes()

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
        assert "social-card.svg" not in page

    for page, title, image_url, alt_text in (
        (
            first_event,
            "UCSF debuts new unit for neurosurgical patients",
            "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/social-card.png",
            "The Blue Fern Co. Care Line social card for UCSF opens 8-bed pediatric neuroscience unit",
        ),
        (
            second_event,
            "UnitedHealthcare, ECU Health extend agreement until August",
            "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/social-card.png",
            "The Blue Fern Co. Care Line social card for ECU Health extends in-network access",
        ),
    ):
        assert '<meta property="og:type" content="article">' in page
        assert '<meta property="og:site_name" content="The Blue Fern Co.">' in page
        assert f'<meta property="og:title" content="{title}">' in page
        assert f'<meta name="twitter:title" content="{title}">' in page
        assert f'<meta property="og:image" content="{image_url}">' in page
        assert '<meta property="og:image:width" content="1200">' in page
        assert '<meta property="og:image:height" content="630">' in page
        assert f'<meta property="og:image:alt" content="{alt_text}">' in page
        assert '<meta name="twitter:card" content="summary_large_image">' in page
        assert f'<meta name="twitter:image" content="{image_url}">' in page
        assert f'<meta name="twitter:image:alt" content="{alt_text}">' in page

    assert first_card == first_source_card
    assert second_card == second_source_card
    assert hashlib.sha256(first_card).hexdigest() == hashlib.sha256(first_source_card).hexdigest()
    assert hashlib.sha256(second_card).hexdigest() == hashlib.sha256(second_source_card).hexdigest()
    assert first_card != second_card
    assert hashlib.sha256(first_card).hexdigest() != hashlib.sha256(second_card).hexdigest()
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
    assert set(manifest["public_urls"]) == {
        "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/",
        "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/",
        "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/social-card.png",
        "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/social-card.png",
    }
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

def test_phase14h_utf8_round_trip_rendering(tmp_path: Path) -> None:
    event = _sample_utf8_event()
    html_path = tmp_path / "event.html"
    feed_path = tmp_path / "feed.xml"
    index_path = tmp_path / "index.html"
    expected_title = event.title
    expected_description = event.public_summary
    expected_alt = f"The Blue Fern Co. Care Line social card for {event.title}"
    expected_image_url = "https://dispatches.thebluefernco.com/events/event_roundtrip/social-card.png"

    html_path.write_text(_render_event_page(event), encoding="utf-8")
    html_text = html_path.read_text(encoding="utf-8")
    assert '<meta property="og:type" content="article">' in html_text
    assert '<meta property="og:site_name" content="The Blue Fern Co.">' in html_text
    assert f'<meta property="og:title" content="{expected_title}">' in html_text
    assert f'<meta property="og:description" content="{expected_description}">' in html_text
    assert f'<meta property="og:image" content="{expected_image_url}">' in html_text
    assert '<meta property="og:image:width" content="1200">' in html_text
    assert '<meta property="og:image:height" content="630">' in html_text
    assert f'<meta property="og:image:alt" content="{expected_alt}">' in html_text
    assert f'<meta name="twitter:title" content="{expected_title}">' in html_text
    assert f'<meta name="twitter:image" content="{expected_image_url}">' in html_text
    assert f'<meta name="twitter:image:alt" content="{expected_alt}">' in html_text
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

    feed_text = feed_path.read_text(encoding="utf-8")
    index_text = index_path.read_text(encoding="utf-8")

    assert event.title in html_text
    assert "UnitedHealthcare" in html_text
    assert event.public_summary in html_text
    assert event.title in html_text
    assert "Children?s" not in html_text
    assert "social-card.svg" not in html_text
    assert "Temporary network-access extension" in index_text
    assert event.title in index_text
    assert event.title in feed_text
    assert "UnitedHealthcare" in feed_text
    assert event.title in feed_text
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
    monkeypatch.setattr(care_line_signal_wire, "render_social_card_png_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("approved Care Line assets must be copied, not procedurally rendered")))

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
    state_path = work / "data" / "universal_events" / "publication-state" / "care-line-signal-wire.json"
    assert state_path.exists()
    first_state = state_path.read_text(encoding="utf-8")
    first_pngs = {
        path.name: path.read_bytes()
        for path in [
            site_root / "events" / "event_3b4ad4e528e48744" / "social-card.png",
            site_root / "events" / "event_a12dae614b86cfa9" / "social-card.png",
        ]
    }
    (work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json").unlink()
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
    second_state = state_path.read_text(encoding="utf-8")
    second_pngs = {
        path.name: path.read_bytes()
        for path in [
            site_root / "events" / "event_3b4ad4e528e48744" / "social-card.png",
            site_root / "events" / "event_a12dae614b86cfa9" / "social-card.png",
        ]
    }

    assert first["ok"] is True
    assert second["ok"] is True

    assert first_index == second_index
    assert first_feed == second_feed
    assert first_manifest == second_manifest
    assert first_state == second_state
    assert first_pngs == second_pngs
    assert hashlib.sha256(first_pngs["social-card.png"]).hexdigest() == hashlib.sha256(second_pngs["social-card.png"]).hexdigest()


def test_phase14h_publication_uses_publication_state_without_shadow_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare_work_root(tmp_path)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    first = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    assert first["ok"] is True

    state_path = work / "data" / "universal_events" / "publication-state" / "care-line-signal-wire.json"
    manifest_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json"
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first_manifest["public_published_at"] == first_state["events"]["event_3b4ad4e528e48744"]["public_published_at"]
    assert first_state["events"]["event_3b4ad4e528e48744"]["public_content_hash"]

    manifest_path.unlink()
    second = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    assert second["ok"] is True

    second_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first_manifest == second_manifest
    assert first_state == second_state


def test_phase14h_publication_state_hash_is_public_only_and_revision_safe(tmp_path: Path) -> None:
    event = _sample_utf8_event()
    first_events, first_state = _resolve_publication_state(
        tmp_path,
        [event],
        generated_at="2026-07-24T05:00:00Z",
        fallback_public_published_at="2026-07-24T05:00:00Z",
    )
    state_path = tmp_path / "data" / "universal_events" / "publication-state" / "care-line-signal-wire.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(first_state), encoding="utf-8")

    metadata_only = replace(event, review_decision_id="new-internal-decision", review_packet_fingerprint="new-packet")
    public_revision = replace(event, public_summary=event.public_summary + " Revised.")
    assert _publication_content_hash(metadata_only) == _publication_content_hash(event)
    assert _publication_content_hash(public_revision) != _publication_content_hash(event)

    unchanged_events, unchanged_state = _resolve_publication_state(
        tmp_path,
        [event],
        generated_at="2026-07-25T05:00:00Z",
        fallback_public_published_at="2026-07-25T05:00:00Z",
    )
    assert unchanged_events[0].public_published_at == first_events[0].public_published_at
    assert unchanged_events[0].last_updated_at == first_events[0].last_updated_at
    assert unchanged_state == first_state

    revised_events, _ = _resolve_publication_state(
        tmp_path,
        [public_revision],
        generated_at="2026-07-25T05:00:00Z",
        fallback_public_published_at="2026-07-25T05:00:00Z",
    )
    assert revised_events[0].public_published_at == first_events[0].public_published_at
    assert revised_events[0].last_updated_at == "2026-07-25T05:00:00Z"
