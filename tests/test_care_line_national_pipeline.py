from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_national_pipeline import (
    SMOKE_COLLECTION_RUNS_ROOT,
    SMOKE_REVIEW_ROOT,
    WORKING_BACKLOG_PATH,
    WORKING_DUPLICATES_PATH,
    WORKING_EXCLUSIONS_PATH,
    WORKING_FAILED_EXTRACTIONS_PATH,
    WORKING_MANUAL_REVIEW_PATH,
    WORKING_REVIEW_QUEUE_PATH,
    build_review_queue,
    cluster_candidates,
    collectable_sources,
    event_lead_from_raw_item,
    load_canonical_registry,
    parse_source_items,
    qualify_event_lead,
    review_priority,
    run_collection_attempt,
    run_national_pipeline,
    validate_review_snapshot,
    write_review_snapshot,
    _extract_article_content,
)
from bluefern_dispatches.care_line_record import CareLineReviewedRecord


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    return root


def _source(repo_copy: Path):
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    return next(row["source"] for row in collectable_sources(registry, include_partial=False) if row["source"].state)


def _raw_item(*, title: str, description: str, source_id: str = "test-source", state: str = "IA", url: str = "https://example.org/story", requires_html_followup: bool = False, source_date: str = "2026-08-04") -> dict[str, object]:
    return {
        "raw_item_id": "raw-1",
        "source_id": source_id,
        "source_name": "Test Source",
        "source_publisher": "Test Publisher",
        "source_type": "local_publisher",
        "source_role": "test",
        "authority_level": "secondary",
        "source_state": state,
        "item_url": url,
        "title": title,
        "description": description,
        "source_publication_date": source_date,
        "source_date_state": "source_dated" if source_date else "missing",
        "requires_html_followup": requires_html_followup,
        "item_permalink_available": True,
        "record_fingerprint": "fingerprint",
    }


def test_collectable_sources_use_canonical_registry_and_skip_disabled(repo_copy: Path) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    rows = collectable_sources(registry)
    assert rows
    assert all(row["source_id"] for row in rows)
    assert all(row["readiness"] != "DISABLED" for row in rows)


def test_generic_healthcare_news_is_excluded_before_candidate_stage() -> None:
    raw_item = _raw_item(
        title="CDC data suggest STI rates may be slowing",
        description="Researchers say the epidemic may be slowing across the United States.",
        state="US",
    )
    lead = event_lead_from_raw_item(raw_item)
    assert lead["qualification_status"] == "excluded"
    assert lead["exclusion_reason"] in {"general_healthcare_news", "non_care_line"}


def test_marketing_and_award_items_are_excluded() -> None:
    raw_item = _raw_item(
        title="County Hospital celebrates award for patient experience",
        description="The hospital received an award and launched a community campaign.",
    )
    lead = event_lead_from_raw_item(raw_item)
    assert lead["qualification_status"] == "excluded"
    assert lead["exclusion_reason"] in {"award_or_fundraising", "marketing_announcement"}


def test_financial_distress_without_access_consequence_is_excluded() -> None:
    raw_item = _raw_item(
        title="Mercy Health reports quarterly losses amid margin pressure",
        description="The system cited balance-sheet pressure but did not announce any service changes.",
    )
    lead = event_lead_from_raw_item(raw_item)
    assert lead["qualification_status"] == "excluded"


def test_raw_item_becomes_event_lead_then_qualified_candidate_when_evidence_is_bounded(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Mercy Hospital will close its labor and delivery unit",
        description="Mercy Hospital said it will close its labor and delivery unit on August 10. Patients will be transferred to another hospital.",
    )
    lead = event_lead_from_raw_item(raw_item)
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert lead["qualification_status"] == "event_lead"
    assert status == "qualified"
    assert payload["candidate_id"].startswith("care_line_candidate_")
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.event_type in {"service_closure", "facility_closure"}
    assert reviewed.supporting_passage


def test_temporary_suspension_qualifies_when_article_text_is_fetched(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="County Hospital temporarily suspends emergency department",
        description="",
        requires_html_followup=True,
    )
    raw_item["item_url"] = f"https://{source.allowed_hosts[0] if source.allowed_hosts else source.feed_url.split('://', 1)[1].split('/', 1)[0]}/story"
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><head><title>County Hospital temporarily suspends emergency department</title></head><body><p>County Hospital temporarily suspended emergency department services Tuesday because of staffing shortages.</p><p>Patients will be transferred to nearby hospitals until service resumes.</p></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.event_type in {"service_suspension", "temporary_facility_suspension"}
    assert "transfer" in reviewed.supporting_passage.lower() or "suspended" in reviewed.supporting_passage.lower()


def test_reduced_hours_qualifies_as_standard_priority(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Rural clinic reduces hours in western Iowa",
        description="The clinic reduced hours because of staffing shortages, limiting appointments for patients.",
    )
    lead = event_lead_from_raw_item(raw_item)
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    priority, _ = review_priority(reviewed)
    assert reviewed.event_type == "hours_reduction"
    assert priority == "STANDARD"


def test_restoration_qualifies_only_with_prior_loss_context(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Hospital reopens labor and delivery after last month's closure",
        description="The hospital said labor and delivery services reopened after last month's temporary closure.",
    )
    lead = event_lead_from_raw_item(raw_item)
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.event_type in {"facility_reopening", "service_restoration"}


def test_full_article_requirement_is_preserved_when_summary_is_insufficient(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Hospital announcement",
        description="",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    lead["qualification_status"] = "event_lead"
    lead["event_type_hint"] = "facility_closure"
    monkeypatch_payload = (
        b"<html><body><p>Hospital announcement.</p></body></html>",
        {"http_status": 200, "content_type": "text/html", "final_url": "https://example.org/story"},
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("bluefern_dispatches.care_line_national_pipeline.fetch_url", lambda *args, **kwargs: monkeypatch_payload)
        status, payload = qualify_event_lead(
            source,
            raw_item,
            lead,
            artifact_path="artifact.json",
            run_id="run-1",
            fetch_timeout=5,
            allow_insecure_tls=False,
        )
    assert status == "failed_extraction"
    assert payload["classification"] in {"NEEDS_FULL_ARTICLE", "INSUFFICIENT_BOUNDED_EVIDENCE"}


def test_explicit_lincoln_closure_excerpt_can_reach_review_without_full_article(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["beckershospitalreview.com"]})
    raw_item = _raw_item(
        title="MaineHealth to end labor and delivery at Lincoln Hospital",
        description="Portland-based MaineHealth will end inpatient labor and delivery services at MaineHealth Lincoln Hospital in Damariscotta, Maine, effective Dec. 18, according to an Aug. 6 health system news release shared with Becker's.",
        source_date="2026-08-06",
        url="https://www.beckershospitalreview.com/finance/mainehealth-to-end-labor-and-delivery-at-lincoln-hospital/",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><p>Subscribe to continue reading</p></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.event_type in {"service_closure", "facility_closure"}
    assert reviewed.validation_issues() == []
    assert "full_article_recommended" in payload["qualification_result"]["review_warnings"]


def test_missing_source_date_is_failed_extraction(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Community Hospital closes clinic",
        description="Community Hospital will close the clinic and redirect patients.",
        source_date="",
    )
    raw_item["source_date_state"] = "missing"
    lead = event_lead_from_raw_item(raw_item)
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "failed_extraction"
    assert payload["classification"] == "NEEDS_DATE"


def test_missing_source_date_can_still_reach_review_when_currentness_is_resolved(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="MaineHealth hospital announces closure of Damariscotta Birthing Center",
        description="MaineHealth said the Damariscotta Birthing Center at the hospital will close on Aug. 6 and patients will be transferred to other hospitals.",
        source_date="",
        url="https://example.org/damariscotta",
    )
    raw_item["source_date_state"] = "missing"
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>MaineHealth said the Damariscotta Birthing Center at the hospital will close on Aug. 6 because of staffing changes, and patients will be transferred to other hospitals.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.announcement_date == "2026-08-06"
    assert reviewed.validation_issues() == []
    assert "missing_source_publication_date" in payload["qualification_result"]["review_warnings"]


def test_bangor_maternity_ward_vote_story_resolves_subject_and_reaches_review(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["bdn-data.s3.amazonaws.com", "bangordailynews.com"]})
    raw_item = _raw_item(
        title="Judge denies request to stop vote on closing midcoast maternity ward",
        description="Justice Daniel Billings said the plaintiff, a pregnant Lincoln County woman, failed to demonstrate how she would be irreparably injured simply by a vote to close the center.",
        source_date="2026-08-06",
        url="https://www.bangordailynews.com/2026/08/06/midcoast/midcoast-police-courts/judge-denies-request-to-stop-vote-closing-lincoln-hospital-maternity-ward/",
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>Justice Daniel Billings said the plaintiff, a pregnant Lincoln County woman, failed to demonstrate how she would be irreparably injured simply by a vote to close the center.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.service_line in {"maternity", "labor_and_delivery"}
    assert reviewed.validation_issues() == []


def test_no_inferred_vulnerability_priority_bump(repo_copy: Path) -> None:
    source = _source(repo_copy)
    raw_item = _raw_item(
        title="Rural clinic reduces hours",
        description="The rural clinic reduced hours because of staffing shortages, limiting appointments.",
    )
    lead = event_lead_from_raw_item(raw_item)
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    priority, _ = review_priority(reviewed)
    assert priority == "STANDARD"


def test_cluster_candidates_reduce_duplicate_rows() -> None:
    reviewed_payload = {
        "producer_record_id": "rec-1",
        "source_url": "https://example.org/story",
        "source_title": "Mercy Hospital will close its labor and delivery unit",
        "source_publisher": "Example",
        "source_publication_date": "2026-08-04",
        "supporting_passage": "Mercy Hospital will close its labor and delivery unit. Patients will be transferred.",
        "raw_payload_hash": "hash-1",
        "event_type": "service_closure",
        "announcement_date": "2026-08-04",
        "service_line": "labor_and_delivery",
        "facility_name": "Mercy Hospital",
        "provider_name": "Mercy Hospital",
        "city": "Example City",
        "state": "IA",
        "country_code": "US",
        "geographic_scope": "city",
        "permanence": "permanent",
        "review_status": "reviewed",
        "record_status": "active",
        "public_status": "not_public",
        "universal_event_status": "needs_normalization_review",
        "field_provenance": {},
    }
    rows = []
    for idx in ("a", "b"):
        reviewed = CareLineReviewedRecord.model_validate(reviewed_payload | {"producer_record_id": f"rec-{idx}", "source_url": f"https://example.org/story-{idx}"})
        rows.append(
                {
                    "candidate_id": f"cand-{idx}",
                    "duplicate_cluster_hints": {"cluster_id": "cluster-1"},
                    "normalized_record": reviewed.model_dump(mode="json"),
                    "qualification_result": {
                        "review_priority_recommendation": "CRITICAL",
                        "priority_reason": "major current service loss",
                        "currentness_class": "CURRENT_EVENT",
                        "freshness_role": "CURRENT",
                    },
                }
            )
    clusters = cluster_candidates(rows)
    queue = build_review_queue(rows, edition_date="2026-08-04", active_queue_limit=10, low_priority_cap=1)
    assert clusters["cluster_count"] == 1
    assert queue["queue_item_count"] == 1
    assert queue["duplicate_item_count"] == 1


def test_sitemap_and_structured_index_parsing_are_supported(repo_copy: Path) -> None:
    source = _source(repo_copy)
    sitemap_source = source.model_copy(update={"adapter_type": "sitemap", "collection_method": "sitemap_polling", "feed_url": "https://example.org/sitemap.xml"})
    sitemap_items = parse_source_items(
        sitemap_source,
        b"""<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>https://example.org/story-one</loc><lastmod>2026-08-04</lastmod></url></urlset>""",
        source_url=sitemap_source.feed_url,
        fetch_timeout=5,
        allow_insecure_tls=False,
        max_items_per_source=10,
    )
    structured_source = source.model_copy(update={"adapter_type": "structured_index", "collection_method": "structured_index_polling", "feed_url": "https://example.org/news"})
    structured_items = parse_source_items(
        structured_source,
        b"<html><body><article><a href='https://example.org/story-two'>County Hospital to close clinic</a><time datetime='2026-08-04'></time></article></body></html>",
        source_url=structured_source.feed_url,
        fetch_timeout=5,
        allow_insecure_tls=False,
        max_items_per_source=10,
    )
    assert sitemap_items[0]["url"] == "https://example.org/story-one"
    assert structured_items[0]["url"] == "https://example.org/story-two"


def test_parser_limits_bound_items(repo_copy: Path) -> None:
    source = _source(repo_copy).model_copy(update={"adapter_type": "structured_index", "collection_method": "structured_index_polling", "feed_url": "https://example.org/news"})
    payload = "<html><body>" + "".join(
        f"<article><a href='https://example.org/story-{idx}'>Hospital {idx} to close clinic</a></article>"
        for idx in range(5)
    ) + "</body></html>"
    items = parse_source_items(
        source,
        payload.encode("utf-8"),
        source_url=source.feed_url,
        fetch_timeout=5,
        allow_insecure_tls=False,
        max_items_per_source=2,
    )
    assert len(items) == 2


def test_extract_article_content_prefers_json_ld_and_filters_boilerplate(repo_copy: Path) -> None:
    source = _source(repo_copy)
    html = b"""
    <html>
      <head>
        <title>Ignored title</title>
        <meta property="og:description" content="Short fallback" />
        <script type="application/ld+json">
          {"@type":"NewsArticle","headline":"County Hospital closes clinic","description":"Clinic closure notice","articleBody":"County Hospital will close its primary care clinic on Aug. 10. Patients will be redirected to another location."}
        </script>
      </head>
      <body>
        <div>Skip to main content</div>
      </body>
    </html>
    """
    extracted = _extract_article_content(source, html, source_url="https://example.org/story", response_meta={"content_type": "text/html"})
    assert extracted["extraction_outcome"] == "BODY_EXTRACTED"
    assert extracted["extraction_method"] == "json_ld"
    assert "Skip to main content" not in extracted["text"]
    assert "County Hospital will close" in extracted["text"]


def test_partial_extraction_routes_strong_lead_to_manual_review(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County Hospital closes clinic",
        description="County Hospital will close the clinic.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><head><meta property='og:description' content='County Hospital will close the clinic.'/></head><body><div>Subscribe to continue reading</div></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "failed_extraction"
    assert payload["classification"] == "NEEDS_HUMAN_REVIEW"
    assert payload["extraction_outcome"] == "PAYWALLED"


def test_kff_style_historical_background_is_excluded(repo_copy: Path) -> None:
    source = _source(repo_copy).model_copy(update={"source_id": "kff-health-news", "allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        source_id="kff-health-news",
        state="",
        title="Earlier Lifeline for Rural Hospitals Faces Test Under Big Beautiful Law",
        description="A century-old rural Michigan hospital converted to a stripped-down model Congress created to help keep small facilities afloat, then it closed.",
        url="https://example.org/kff-story",
    )
    lead = event_lead_from_raw_item(raw_item)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "bluefern_dispatches.care_line_national_pipeline.fetch_url",
            lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
                b"<html><body><article><p>In Holly Springs, Mississippi, Alliance HealthCare System was one of the first to convert to the emergency hospital designation, laying off staff and shutting down inpatient beds.</p></article></body></html>",
                {"http_status": 200, "content_type": "text/html", "final_url": url},
            ),
        )
        status, payload = qualify_event_lead(
            source,
            raw_item,
            lead,
            artifact_path="artifact.json",
            run_id="run-1",
            fetch_timeout=5,
            allow_insecure_tls=False,
        )
    assert status == "excluded"
    assert payload["exclusion_reason"] == "background_only"
    assert payload["currentness_class"] in {"HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}


def test_future_effective_current_announcement_qualifies(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County Hospital will close its emergency department",
        description="County Hospital announced it will close its emergency department on Aug. 15.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>County Hospital announced Tuesday that it will close its emergency department on Aug. 15 because it cannot staff overnight coverage.</p><p>Patients will be transferred to a nearby hospital after that date.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "qualified"
    assert payload["qualification_result"]["currentness_class"] == "CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE"
    assert payload["qualification_result"]["freshness_role"] == "FUTURE_EFFECTIVE"


def test_current_article_about_historical_closure_is_excluded(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County Hospital closure cited in current financing debate",
        description="County Hospital's closure is being cited in a current policy debate.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>The hospital closed in 2024, forcing patients to drive farther for care.</p><p>Lawmakers this week debated whether to expand the emergency hospital model.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "excluded"
    if "currentness_class" in payload:
        assert payload["currentness_class"] in {"HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}


def test_retrospective_trend_story_is_excluded(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="Trend story reviews rural hospital closures",
        description="The article reviews multiple prior closures.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>Over the years, rural hospitals in three states closed labor and delivery units.</p><p>One hospital closed in 2023 and another closed in 2024.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "excluded"
    if "currentness_class" in payload:
        assert payload["currentness_class"] in {"HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}


def test_mixed_historical_and_current_article_selects_current_operative_event(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="Mercy Hospital to reopen labor and delivery next week",
        description="The hospital previously closed the unit but now plans to reopen it.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>Mercy Hospital closed its labor and delivery unit in 2024.</p><p>Mercy Hospital announced it will reopen labor and delivery on Aug. 12.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "qualified"
    assert payload["qualification_result"]["operative_event_passage"].startswith("Mercy Hospital announced it will reopen")
    assert payload["qualification_result"]["currentness_class"] in {"CURRENT_ANNOUNCEMENT_FUTURE_EFFECTIVE", "CURRENT_RESTORATION"}


def test_ongoing_current_interruption_qualifies(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County Hospital remains closed after flood",
        description="County Hospital remains closed and patients are still being transferred.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>County Hospital remains closed this week after flood damage, and patients are still being transferred to nearby hospitals.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "qualified"
    assert payload["qualification_result"]["currentness_class"] == "CURRENT_UPDATE_TO_PRIOR_EVENT"
    assert payload["qualification_result"]["freshness_role"] == "ONGOING_EVENT_UPDATE"


def test_unresolved_conflicting_dates_route_to_manual_review(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County clinic closure timeline remains disputed",
        description="The clinic closure timeline is unclear.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>County clinic announced it would close on Aug. 15, but another sentence says the clinic closed in 2024.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "failed_extraction"
    assert payload["classification"] == "NEEDS_HUMAN_REVIEW"
    assert payload["currentness_class"] == "DATE_UNRESOLVED"


def test_source_publication_date_does_not_override_old_event_date(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        title="County Hospital closure cited in policy debate",
        description="The article cites a previous shutdown.",
        requires_html_followup=True,
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>The hospital closed in 2024 after losing its obstetrics staff.</p><p>Officials cited that shutdown while debating new policy changes today.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(source, raw_item, lead, artifact_path="artifact.json", run_id="run-1", fetch_timeout=5, allow_insecure_tls=False)
    assert status == "excluded"
    assert payload["currentness_class"] in {"HISTORICAL_BACKGROUND", "RETROSPECTIVE_ANALYSIS"}


def test_npr_closure_hint_is_not_downgraded_to_restoration(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(repo_copy).model_copy(update={"source_id": "npr-health", "allowed_hosts": ["example.org"]})
    raw_item = _raw_item(
        source_id="npr-health",
        state="",
        title="One Maine community's fight to save a birthing center",
        description="In mid-coast Maine a grassroots coalition is fighting to prevent the proposed closure of Miles Hospital's labor and delivery center.",
        url="https://example.org/npr-story",
    )
    lead = event_lead_from_raw_item(raw_item)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_url",
        lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
            b"<html><body><article><p>Residents in Maine are fighting to prevent the proposed closure of Miles Hospital's labor and delivery center.</p><p>The center reopened years ago after an earlier review.</p></article></body></html>",
            {"http_status": 200, "content_type": "text/html", "final_url": url},
        ),
    )
    status, payload = qualify_event_lead(
        source,
        raw_item,
        lead,
        artifact_path="artifact.json",
        run_id="run-1",
        fetch_timeout=5,
        allow_insecure_tls=False,
    )
    assert status == "qualified"
    reviewed = CareLineReviewedRecord.model_validate(payload["normalized_record"])
    assert reviewed.event_type in {"facility_closure", "planned_facility_closure"}


def test_run_collection_attempt_preserves_exclusions_and_failed_extractions(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    source_row = next(row for row in collectable_sources(registry, include_partial=False) if row["source"].state)
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (
                b"<rss><channel>"
                b"<item><title>Clinic closes labor and delivery</title><link>https://example.org/qualify</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>The clinic will close labor and delivery and transfer patients.</description></item>"
                b"<item><title>Hospital wins award</title><link>https://example.org/exclude</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>The hospital won an award.</description></item>"
                b"<item><title>Emergency department temporarily suspended</title><link>https://example.org/fail</link><description></description></item>"
                b"</channel></rss>",
                {"http_status": 200, "final_url": source.feed_url},
            ),
        )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "bluefern_dispatches.care_line_national_pipeline.fetch_url",
            lambda url, timeout=20, allow_insecure_tls=False, user_agent="": (
                b"<html><body><p>Hospital announcement.</p></body></html>",
                {"http_status": 200, "content_type": "text/html", "final_url": url},
            ),
        )
        result = run_collection_attempt(repo_copy, run_date="2026-08-04", run_id="20260804-test-01", source_row=source_row, max_items_per_source=10)
        assert len(result["candidates"]) == 1
        assert len(result["exclusions"]) == 1
        assert len(result["failed_extractions"]) == 1


def test_national_pipeline_reports_explicit_counts_and_queue_files(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected = [next(row for row in collectable_sources(registry, include_partial=False) if row["source"].state)]
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.collectable_sources",
        lambda registry, include_partial=True, include_manual_review=False: selected,
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (
            b"<rss><channel>"
            b"<item><title>Mercy Hospital closes clinic</title><link>https://example.org/story-one</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>Mercy Hospital will close the clinic and redirect patients.</description></item>"
            b"<item><title>County Hospital reduces hours</title><link>https://example.org/story-two</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>County Hospital reduced hours, limiting appointments.</description></item>"
            b"</channel></rss>",
            {"http_status": 200, "final_url": source.feed_url},
        ),
    )
    result = run_national_pipeline(repo_copy, run_date="2026-08-04", include_partial=False, source_limit=1, active_queue_limit=1, low_priority_cap=0)
    manifest = result["run_manifest"]
    assert manifest["raw_items_retrieved_this_run"] == 2
    assert manifest["event_leads_created_this_run"] >= 2
    assert manifest["active_review_queue_count"] >= 1
    assert (repo_copy / WORKING_REVIEW_QUEUE_PATH).exists()
    assert (repo_copy / WORKING_BACKLOG_PATH).exists()
    assert (repo_copy / WORKING_EXCLUSIONS_PATH).exists()
    assert (repo_copy / WORKING_DUPLICATES_PATH).exists()
    assert (repo_copy / WORKING_FAILED_EXTRACTIONS_PATH).exists()
    assert (repo_copy / WORKING_MANUAL_REVIEW_PATH).exists()


def test_national_pipeline_smoke_mode_isolates_review_state_and_selects_sources_deterministically(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected = [row for row in collectable_sources(registry, include_partial=True) if row["source"].source_id in {"cdc-newsroom", "acp-news", "beckers-hospital-review"}]
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.collectable_sources",
        lambda registry, include_partial=True, include_manual_review=False: list(reversed(selected)),
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (
            f"<rss><channel><item><title>{source.source_id} closes clinic</title><link>https://example.org/{source.source_id}</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>{source.source_id} will close the clinic and redirect patients.</description></item></channel></rss>".encode("utf-8"),
            {"http_status": 200, "final_url": source.feed_url},
        ),
    )
    result = run_national_pipeline(
        repo_copy,
        run_date="2026-08-04",
        include_partial=True,
        source_limit=2,
        max_items_per_source=1,
        smoke_test=True,
        collection_runs_root=SMOKE_COLLECTION_RUNS_ROOT,
        review_root=SMOKE_REVIEW_ROOT,
    )
    manifest = result["run_manifest"]
    assert manifest["smoke_test"] is True
    assert manifest["selected_source_ids"] == ["acp-news", "beckers-hospital-review"]
    assert manifest["production_review_queue_mutation_disabled"] is True
    assert manifest["review_state_mode"] == "isolated_smoke"
    assert (repo_copy / SMOKE_REVIEW_ROOT / "current-review-queue.json").exists()
    assert (repo_copy / SMOKE_REVIEW_ROOT / "candidate-registry.json").exists()
    assert (repo_copy / SMOKE_COLLECTION_RUNS_ROOT / "2026-08-04" / manifest["run_id"] / "run-manifest.json").exists()
    assert not (repo_copy / WORKING_REVIEW_QUEUE_PATH).exists()


def test_pipeline_rerun_preserves_first_seen_and_reports_prior_candidates(repo_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_canonical_registry(repo_copy, include_disabled=True)
    selected = [next(row for row in collectable_sources(registry, include_partial=False) if row["source"].state)]
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.collectable_sources",
        lambda registry, include_partial=True, include_manual_review=False: selected,
    )
    monkeypatch.setattr(
        "bluefern_dispatches.care_line_national_pipeline.fetch_source",
        lambda source, timeout=20, allow_insecure_tls=False: (
            b"<rss><channel><item><title>Mercy Hospital closes clinic</title><link>https://example.org/story</link><pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate><description>Mercy Hospital will close the clinic and redirect patients.</description></item></channel></rss>",
            {"http_status": 200, "final_url": source.feed_url},
        ),
    )
    first = run_national_pipeline(repo_copy, run_date="2026-08-04", include_partial=False, source_limit=1)
    first_candidate = first["candidate_registry"]["candidates"][0]
    second = run_national_pipeline(repo_copy, run_date="2026-08-04", include_partial=False, source_limit=1)
    second_candidate = second["candidate_registry"]["candidates"][0]
    assert first_candidate["candidate_id"] == second_candidate["candidate_id"]
    assert first_candidate["first_seen"] == second_candidate["first_seen"]
    assert second_candidate["last_seen"] >= first_candidate["last_seen"]


def test_review_snapshot_hashing_and_legacy_fallback(repo_copy: Path) -> None:
    queue_payload = {"schema_version": "test", "items": []}
    snapshot = write_review_snapshot(repo_copy, edition_date="2026-08-04", queue_payload=queue_payload)
    validated = validate_review_snapshot(
        repo_copy,
        edition_date="2026-08-04",
        snapshot_path=Path(snapshot["snapshot_path"]).relative_to(repo_copy).as_posix(),
        snapshot_sha256=snapshot["snapshot_sha256"],
    )
    assert validated["mode"] == "snapshot"
    snapshot_file = repo_copy / Path(snapshot["snapshot_path"]).relative_to(repo_copy)
    snapshot_file.unlink()
    fallback = repo_copy / WORKING_REVIEW_QUEUE_PATH
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text(json.dumps({"schema_version": "legacy", "items": []}), encoding="utf-8")
    validated_fallback = validate_review_snapshot(
        repo_copy,
        edition_date="2026-08-04",
        snapshot_path=Path(snapshot["snapshot_path"]).relative_to(repo_copy).as_posix(),
        snapshot_sha256=snapshot["snapshot_sha256"],
        allow_legacy_fallback=True,
        fallback_queue_path=WORKING_REVIEW_QUEUE_PATH.as_posix(),
    )
    assert validated_fallback["mode"] == "legacy_fallback"
