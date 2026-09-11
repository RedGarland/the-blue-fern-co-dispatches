from __future__ import annotations

from bluefern_dispatches.ice_dispatch import (
    CollectionHealth,
    FetchResult,
    ProviderHealth,
    best_date_evidence,
    collect_live_candidates,
    currentness_for_dates,
    evaluate_collection_health,
    extract_pdf_text_date_evidence,
)


def oig_source(**overrides):
    data = {
        "source_id": "dhs-oig-reports",
        "publisher": "DHS Office of Inspector General",
        "source_type": "official_oversight_reports",
        "url": "https://www.oig.dhs.gov/reports/audits-inspections-and-evaluations?order=field_issue_date&sort=desc",
        "enabled": True,
        "tier": 1,
        "geography": "US_and_territories",
        "categories": ["legal_oversight_accountability"],
        "collection_mechanism": "html",
        "expected_cadence": "weekly_monitor",
    }
    data.update(overrides)
    return data


def test_oig_listing_issue_date_preferred_for_pdf_link():
    source = oig_source()
    link = {
        "title": "ICE Homeland Security Investigations Hindered in Countering Fentanyl Entering the United States",
        "url": "https://www.oig.dhs.gov/sites/default/files/assets/2026-08/OIG-26-23-Aug26.pdf",
        "canonical_url": "https://oig.dhs.gov/sites/default/files/assets/2026-08/OIG-26-23-Aug26.pdf",
    }
    listing = """
    <tr>
      <td class="views-field-field-report-number">OIG-26-23</td>
      <td class="views-field-title"><a href="/sites/default/files/assets/2026-08/OIG-26-23-Aug26.pdf">ICE Homeland Security Investigations Hindered in Countering Fentanyl Entering the United States</a></td>
      <td class="views-field-field-issue-date"><time datetime="2026-09-08T12:00:00Z">09/08/2026</time></td>
    </tr>
    """
    evidence = best_date_evidence(source, link, listing, "%PDF Report Date: August 20, 2026")
    assert evidence == {
        "published_at": "2026-09-08T12:00:00Z",
        "modified_at": None,
        "date_source": "oig_listing_issue_date",
        "date_confidence": "official_listing_metadata",
    }


def test_pdf_visible_date_used_when_listing_date_absent():
    evidence = extract_pdf_text_date_evidence("%PDF text\nReport Date: August 20, 2026\nbody")
    assert evidence == {
        "published_at": "2026-08-20T00:00:00Z",
        "modified_at": None,
        "date_source": "pdf_visible_report_date",
        "date_confidence": "official_visible_text",
    }


def test_pdf_metadata_fallback_is_bounded_and_not_filesystem_timestamp():
    evidence = extract_pdf_text_date_evidence("%PDF\n/CreationDate(D:20260908123456-07'00')\n")
    assert evidence == {
        "published_at": "2026-09-08T19:34:56Z",
        "modified_at": None,
        "date_source": "pdf_document_metadata",
        "date_confidence": "document_metadata",
    }
    assert extract_pdf_text_date_evidence("%PDF no date") == {
        "published_at": None,
        "modified_at": None,
        "date_source": None,
        "date_confidence": None,
    }


def test_current_oig_pdf_is_current_and_historical_pdf_is_stale(monkeypatch):
    source = oig_source()
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if "reports/audits" in url:
            return FetchResult(url, True, 200, """
            <tr>
              <td class="views-field-title"><a href="/sites/default/files/assets/2026-09/OIG-current.pdf">ICE current audit report</a></td>
              <td class="views-field-field-issue-date"><time datetime="2026-09-08T12:00:00Z">09/08/2026</time></td>
            </tr>
            <tr>
              <td class="views-field-title"><a href="/sites/default/files/assets/2026-07/OIG-old.pdf">ICE historical audit report</a></td>
              <td class="views-field-field-issue-date"><time datetime="2026-07-23T12:00:00Z">07/23/2026</time></td>
            </tr>
            """, None, "test")
        return FetchResult(url, True, 200, "<p>ICE audit report found detention deficiencies.</p>", None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, _ = collect_live_candidates(
        [source], max_per_source=2, window_hours=72, observed_at="2026-09-09T00:00:00Z"
    )
    assert len(raw) == 1
    assert raw[0]["sources"][0]["published_at"] == "2026-09-08T12:00:00Z"
    assert raw[0]["sources"][0]["date_source"] == "oig_listing_issue_date"
    assert raw[0]["currentness_status"] == "current_publication"
    assert exclusions[0]["reason"] == "stale_resurfaced_article"
    assert exclusions[0]["published_at"] == "2026-07-23T12:00:00Z"
    assert health[0].success is True


def test_undated_pdf_stays_unresolved_not_fabricated(monkeypatch):
    source = oig_source()
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if "reports/audits" in url:
            return FetchResult(url, True, 200, '<tr><td><a href="/sites/default/files/assets/2026-09/OIG-undated.pdf">ICE undated audit report</a></td></tr>', None, "test")
        return FetchResult(url, True, 200, "%PDF no visible date but ICE audit report found detention deficiencies.", None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, _, _ = collect_live_candidates([source], max_per_source=1, window_hours=72)
    assert exclusions == []
    assert raw[0]["sources"][0]["published_at"] is None
    assert raw[0]["sources"][0]["date_source"] is None
    assert raw[0]["currentness_status"] == "unresolved_date"
    assert raw[0]["currentness_confidence"] == "undated_current_surface"


def test_currentness_and_collection_health_are_separate():
    currentness = currentness_for_dates(
        published_at="2026-07-23T12:00:00Z",
        modified_at=None,
        event_date=None,
        retrieved_at="2026-09-09T00:00:00Z",
        window_hours=72,
    )
    health = evaluate_collection_health([
        ProviderHealth(source_id="dhs-oig-reports", publisher="DHS OIG", tier=1, attempted=True, success=True, accepted_records=0)
    ])
    assert currentness["status"] == "stale_resurfaced_article"
    assert health == CollectionHealth.HEALTHY
