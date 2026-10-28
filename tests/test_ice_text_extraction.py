"""Offline regressions: transport receipts are not supporting evidence."""
from __future__ import annotations

import json
import zlib
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from bluefern_dispatches import ice_dispatch as ice

TITLE = "ICE detention audit report identifies oversight deficiencies"
URL = "https://example.test/reports/OIG-synthetic.pdf"
OBSERVED = "2026-10-28T00:00:00Z"


def synthetic_pdf(*, compressed: bool = True, visible_text: bool = True) -> bytes:
    """Build a one-page PDF with correct offsets; no parser/network dependency."""
    prose = b"BT /F1 12 Tf 72 720 Td (ICE audit found detention deficiencies.) Tj ET" if visible_text else b""
    stream = zlib.compress(prose) if compressed else prose
    filter_entry = b" /Filter /FlateDecode" if compressed else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + filter_entry + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Title (ICE detention audit report) /CreationDate (D:20261026000000Z) >>",
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


@pytest.fixture(params=[(True, True), (False, True), (True, False)], ids=["flate-compressed", "uncompressed", "no-text"])
def pdf_bytes(request, tmp_path):
    body = synthetic_pdf(compressed=request.param[0], visible_text=request.param[1])
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(body)
    return path.read_bytes()


class Response:
    status = 200

    def __init__(self, body: bytes, content_type: str, final_url: str):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.body[:size]

    def geturl(self):
        return self.final_url


def source():
    return {
        "source_id": "dhs-oig-reports", "publisher": "Synthetic oversight agency",
        "source_type": "official_oversight_reports", "url": "https://example.test/index",
        "enabled": True, "tier": 1, "collection_mechanism": "html",
        "geography": "US_and_territories", "categories": ["legal_oversight_accountability"],
        "expected_cadence": "weekly_monitor",
    }


def listing(url=URL):
    return f'<tr><td><a href="{url}">{TITLE}</a></td><td class="views-field-field-issue-date"><time datetime="2026-10-26T12:00:00Z">10/26/2026</time></td></tr>'


def collect(monkeypatch, content, *, url=URL, **result_fields):
    def fake_fetch(requested, **kwargs):
        if requested == source()["url"]:
            return ice.FetchResult(requested, True, 200, listing(url), None, "offline-fixture")
        return ice.FetchResult(requested, True, 200, content, None, "offline-fixture", **result_fields)
    monkeypatch.setattr(ice, "fetch_url_secure", fake_fetch)
    return ice.collect_live_candidates([source()], max_per_source=1, observed_at=OBSERVED)


def test_pdf_http_success_never_decodes_binary_as_prose(monkeypatch, pdf_bytes):
    monkeypatch.setattr(ice.urllib.request, "urlopen", lambda *a, **kw: Response(pdf_bytes, "application/pdf", URL))
    monkeypatch.setattr(ice, "_decode_bytes", lambda data: pytest.fail("PDF bytes must not be decoded as prose"))
    monkeypatch.setattr(ice.subprocess, "run", lambda *a, **kw: pytest.fail("HTTP success must not retry"))
    result = ice.fetch_url_secure(URL)
    assert result.ok and result.status == 200 and result.error is None
    assert result.content == ""
    assert result.extraction_error == "pdf_text_extraction_unsupported"
    assert result.content_type == "application/pdf"


@pytest.mark.parametrize("requested,final,mime,body", [
    ("https://example.test/download", "https://example.test/download", "application/octet-stream", synthetic_pdf()),
    ("https://example.test/download", "https://example.test/download", "Application/PDF; charset=binary", b"truncated"),
    ("https://example.test/report.PDF?download=1", "https://example.test/report.PDF", "text/plain", b"truncated"),
    ("https://example.test/report%2Epdf", "https://example.test/report%2Epdf", "text/plain", b"truncated"),
    ("https://example.test/download", URL, "application/octet-stream", b"truncated"),
])
def test_pdf_detection_uses_magic_media_type_requested_or_redirect_url(monkeypatch, requested, final, mime, body):
    monkeypatch.setattr(ice.urllib.request, "urlopen", lambda *a, **kw: Response(body, mime, final))
    result = ice.fetch_url_secure(requested)
    assert result.ok and result.content == ""
    assert result.extraction_error == "pdf_text_extraction_unsupported"


@pytest.mark.parametrize("payload", [
    {"content_type": "application/pdf", "content": "37 80 68 70"},
    {"content_type": "application/octet-stream", "content": "%PDF-1.4 binary stream"},
    {"content_type": "application/octet-stream", "content": "", "extraction_error": "pdf_text_extraction_unsupported"},
    {"content_type": "text/plain", "content": "truncated", "url": URL},
])
def test_windows_fallback_keeps_http_success_and_pdf_gap_separate(monkeypatch, payload):
    def failed(*args, **kwargs):
        raise OSError("offline synthetic urllib failure")
    monkeypatch.setattr(ice.urllib.request, "urlopen", failed)
    calls = []
    data = {"ok": True, "status": 200, "url": "https://example.test/download", "error": None, **payload}
    monkeypatch.setattr(ice.subprocess, "run", lambda args, **kwargs: calls.append(args) or SimpleNamespace(stdout=json.dumps(data)))
    result = ice.fetch_url_secure("https://example.test/download")
    assert result.ok and result.status == 200 and result.content == ""
    assert result.extraction_error == "pdf_text_extraction_unsupported"
    script = calls[0][-1]
    assert "$resp.Content -is [byte[]]" in script
    assert "content_type=$contentType" in script
    assert "extraction_error=$extractionError" in script
    assert "SkipCertificateCheck" not in script


def test_binary_pdf_is_retained_as_discovery_not_qualified_event(monkeypatch, pdf_bytes):
    raw, gaps, health, receipts = collect(monkeypatch, ice._decode_bytes(pdf_bytes))
    assert raw == []
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["reason"] == "pdf_text_extraction_unsupported"
    assert gap["source_url"] == URL and gap["title"] == TITLE
    assert gap["review_status"] == "NEEDS_REVIEW"
    assert gap["verification_status"] == "supporting_evidence_missing"
    assert gap["published_at"] == "2026-10-26T12:00:00Z"
    assert gap["date_source"] == "oig_listing_issue_date"
    assert gap["http_ok"] is True and gap["status"] == 200
    assert health[0].success is True and health[0].accepted_records == 0 and health[0].failed_records == 1
    assert receipts[1]["ok"] is True and receipts[1]["error"] is None
    assert receipts[1]["text_extraction_status"] == "unsupported"
    assert receipts[1]["qualification_reason"] == gap["reason"]


@pytest.mark.parametrize("body", [
    "", "<html></html>", f"<title>{TITLE}</title><h1>{TITLE}</h1>",
    f"<p>{TITLE}.</p>",
    "<nav><p>ICE detained people in Texas.</p></nav>",
    '<div role="navigation"><div>ICE detention reports</div>ICE arrests</div>',
    '<div><a href="/a">ICE detention</a> <a href="/b">ICE arrests</a></div>',
    '<div><a href="/a">ICE detention</a> | <a href="/b">ICE arrests</a></div>',
    '<p hidden>ICE detained 12 people in Texas.</p>',
    '<div aria-hidden="true">ICE detained 12 people.</div>',
    '<div aria-hidden="TRUE">ICE detained 12 people.</div>',
    '<nav>ICE detained 12 people.</nav><main><h1>ICE detention report</h1><p>Please sign in.</p></main>',
])
def test_title_only_navigation_or_empty_html_cannot_supply_passage(monkeypatch, body):
    raw, gaps, health, receipts = collect(monkeypatch, body, url="https://example.test/report.html")
    assert raw == []
    assert gaps[0]["reason"] == "no_supporting_passage"
    assert gaps[0]["review_status"] == "NEEDS_REVIEW"
    assert gaps[0]["source_url"] == "https://example.test/report.html"
    assert health[0].success is True and health[0].failed_records == 1
    assert receipts[1]["text_extraction_status"] == "no_supporting_passage"


def test_title_event_signal_cannot_qualify_generic_body(monkeypatch):
    raw, gaps, _, _ = collect(monkeypatch, "<p>ICE is a federal agency.</p>", url="https://example.test/report.html")
    assert raw == []
    assert gaps[0]["reason"] == "no_event_level_ice_signal"
    assert gaps[0]["review_status"] == "NEEDS_REVIEW"


def test_event_bearing_prose_is_preferred_over_earlier_agency_background(monkeypatch):
    body = "<p>Immigration and Customs Enforcement is a federal agency. ICE said 12 people were arrested in Texas.</p>"
    raw, gaps, _, _ = collect(monkeypatch, body, url="https://example.test/report.html")
    assert gaps == [] and len(raw) == 1
    assert raw[0]["sources"][0]["exact_supporting_passage"] == "ICE said 12 people were arrested in Texas."
    assert raw[0]["impact"]["arrests_count"] == 12


def test_html_prose_links_dates_and_transport_receipts_are_preserved(monkeypatch):
    body = '<nav>ICE arrested 999 people.</nav><main><h1>Heading</h1><p>ICE said <a href="/evidence">12 people were arrested</a> in Texas during an enforcement operation.</p></main>'
    raw, gaps, health, receipts = collect(monkeypatch, body, url="https://example.test/report.html")
    assert gaps == [] and len(raw) == 1
    assert raw[0]["impact"]["arrests_count"] == 12
    assert raw[0]["location"]["state_or_territory"] == "TX"
    assert raw[0]["sources"][0]["exact_supporting_passage"] == "ICE said 12 people were arrested in Texas during an enforcement operation."
    assert raw[0]["sources"][0]["date_source"] == "oig_listing_issue_date"
    assert raw[0]["currentness_status"] == "current_publication"
    assert health[0].success and health[0].accepted_records == 1 and health[0].failed_records == 0
    assert receipts[1]["ok"] and receipts[1]["status"] == 200 and receipts[1]["error"] is None
    assert receipts[1]["text_extraction_status"] == "supporting_passage_extracted"


def test_diagnostic_serializes_gaps_without_normalized_findings(monkeypatch, tmp_path):
    collect(monkeypatch, ice._decode_bytes(synthetic_pdf()))
    monkeypatch.setattr(ice, "utc_now", lambda: OBSERVED)
    registry = tmp_path / "sources.json"
    registry.write_text(json.dumps({"sources": [source()]}))
    output = tmp_path / "diagnostic"
    result = ice.run_diagnostic(registry, live=True, output_dir=output)
    assert result["raw_candidates"] == result["normalized_events"] == result["canonical_events"] == []
    assert result["run_manifest"]["successful_providers"] == 1
    assert result["run_manifest"]["accepted_records"] == 0
    assert result["public_side_effects"] is False
    assert json.loads((output / "exclusions.json").read_text())[0]["review_status"] == "NEEDS_REVIEW"
    receipts = json.loads((output / "fetch_results.json").read_text())
    assert receipts[1]["ok"] and receipts[1]["text_extraction_status"] == "unsupported"
