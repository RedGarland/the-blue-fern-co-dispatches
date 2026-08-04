from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_national_pipeline import (
    WORKING_BACKLOG_PATH,
    WORKING_DUPLICATES_PATH,
    WORKING_EXCLUSIONS_PATH,
    WORKING_FAILED_EXTRACTIONS_PATH,
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
                "qualification_result": {"review_priority_recommendation": "CRITICAL", "priority_reason": "major current service loss"},
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
