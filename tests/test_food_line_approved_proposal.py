from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_approved_proposal import load_approved_proposal
from bluefern_dispatches.food_line_current_review import (
    apply_editorial_decision,
    build_proposed_edition,
    validate_queue,
    write_json_atomic,
)
from bluefern_dispatches.source_based_qualification import assess_review_retention
from scripts.process_food_line_current_intake import _build_review_queue
from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from scripts import run_food_line_dispatch as runner


DATE = "2026-07-31"
SUMMARY = (
    "Faith Food Pantry in Superior closed after its final distribution on July 28. "
    "The pantry had recently served about 960 people and distributed approximately 34,000 pounds of food per month across Douglas County. "
    "People seeking assistance were directed to Second Harvest Northland in Duluth, but available reporting does not establish whether that alternative has sufficient capacity or equivalent accessibility."
)
SOURCE_URL = "https://www.northernnewsnow.com/2026/07/28/superior-food-pantry-closing-after-more-than-30-years/"
CANONICAL_URL = SOURCE_URL.rstrip("/")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, dict, dict]:
    item = {
        "affected_groups": ["approximately 960 recent monthly pantry users"],
        "canonical_source_url": CANONICAL_URL,
        "confidence": "high",
        "decision_audit": {
            "decided_at": "2026-08-01T05:57:31.156360+00:00",
            "decided_by": "Operator",
            "decision": "approve",
        },
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "editorial_status": "approve",
        "evidence_level": "documented_service_closure_with_reported_service_volume",
        "exact_supporting_passage": "The pantry served 960 people and distributed approximately 34,000 pounds each month before closing.",
        "freshness_check": {"status": "current", "edition_date": DATE, "age_days": 3},
        "location_scope": "Superior and Douglas County, Wisconsin",
        "pressure_type": "food_pantry_closure",
        "proposed_public_headline": "Superior food pantry closes after more than 30 years",
        "proposed_public_summary": SUMMARY,
        "proposed_rank": 1,
        "publication_eligible": False,
        "publisher": "Northern News Now",
        "review_item_id": "food-line-current-test",
        "source_artifact_path": "data/dispatches/food-line/agent-intake/2026-07-31/alert.json",
        "source_finding_or_intake_id": "finding_test",
        "source_published_at": "2026-07-28T16:26:00-05:00",
        "source_url": SOURCE_URL,
        "state": "Wisconsin",
        "uncertainty_note": "Available reporting does not establish equivalent replacement capacity.",
        "why_it_matters": "The closure removes a substantial local food-distribution site.",
    }
    queue = {
        "schema_version": "food_line_current_signal_review_v1",
        "edition_date": DATE,
        "production_scope": "current_nonhistorical_only",
        "items": [item],
    }
    queue_path = root / "status/food-line/runtime/current-signal-review.json"
    _write_json(queue_path, queue)
    public_item = {
        "rank": 1,
        "headline": item["proposed_public_headline"],
        "summary": SUMMARY,
        "why_it_matters": item["why_it_matters"],
        "exact_supporting_passage": item["exact_supporting_passage"],
        "uncertainty_note": item["uncertainty_note"],
        "source": "Northern News Now",
        "source_url": CANONICAL_URL,
        "source_published_at": item["source_published_at"],
        "location_name": "Superior",
        "state": "Wisconsin",
        "section": "Core Food Pressure Signals",
    }
    proposal = {
        "schema_version": "food_line_proposed_edition_v1",
        "edition_date": DATE,
        "draft": True,
        "draft_status": "draft_approved_pending_publication",
        "published": False,
        "publication_eligible": False,
        "publication_approval": False,
        "selected_item_count": 1,
        "approved_item_count": 1,
        "pending_item_count": 0,
        "rejected_item_count": 0,
        "source_queue_path": "status/food-line/runtime/current-signal-review.json",
        "source_queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
        "items": [public_item],
    }
    proposal_path = root / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json"
    _write_json(proposal_path, proposal)
    return proposal_path, proposal, queue


def _rewrite(root: Path, proposal_path: Path, proposal: dict, queue: dict) -> None:
    queue_path = root / "status/food-line/runtime/current-signal-review.json"
    _write_json(queue_path, queue)
    proposal["source_queue_sha256"] = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    _write_json(proposal_path, proposal)


def test_approved_proposal_loads_one_source_backed_current_signal(tmp_path: Path) -> None:
    proposal_path, _, _ = _fixture(tmp_path)
    bundle = load_approved_proposal(tmp_path, proposal_path, DATE)
    assert len(bundle.source_rows) == 1
    row = bundle.source_rows[0]
    assert row["url"] == SOURCE_URL
    assert row["approved_public_summary"] == SUMMARY
    assert row["location_name"] == "Superior"
    assert row["country"] == "United States"
    assert row["event_country"] == "United States"
    assert row["state"] == "WI"
    assert row["event_state"] == "WI"
    assert row["supported_product_geography"] is True
    assert row["map_eligible"] is False


def test_approved_loader_rejects_unreviewed_proposal_location_override(tmp_path: Path) -> None:
    proposal_path, proposal, _ = _fixture(tmp_path)
    proposal["items"][0]["location_name"] = "France"
    _write_json(proposal_path, proposal)
    with pytest.raises(ValueError, match="location_name does not match reviewed queue geography"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_approved_loader_rejects_contiguous_place_fragment_override(tmp_path: Path) -> None:
    """Synthetic contract fixture: a reviewed New Mexico row cannot authorize Mexico."""
    proposal_path, proposal, queue = _fixture(tmp_path)
    queue["items"][0].update(
        {
            "event_location": "New Mexico",
            "event_state": "NM",
            "location_name": "New Mexico",
            "location_scope": "New Mexico",
            "state": "NM",
        }
    )
    proposal["items"][0]["location_name"] = "Mexico"
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="location_name does not match reviewed queue geography"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


@pytest.mark.parametrize(
    "geography_fields",
    [
        {"event_country": "Netherlands", "state": ""},
        {"event_state": "WV", "event_location": "Virginia", "state": ""},
        {"event_state": "ZZ", "state": ""},
    ],
)
def test_approved_loader_rejects_foreign_conflicting_or_unknown_queue_geography(
    tmp_path: Path, geography_fields: dict[str, str]
) -> None:
    proposal_path, proposal, queue = _fixture(tmp_path)
    queue["items"][0].update(geography_fields)
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="event geography"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_current_review_producer_output_loads_and_keeps_queue_evidence(tmp_path: Path) -> None:
    proposal_path, _, queue = _fixture(tmp_path)
    queue["historical_roots_excluded"] = ["data/agent-history", "data/agent-history-staging"]
    queue["allowed_decisions"] = ["approve", "approve_with_edit", "hold", "reject"]
    queue["items"][0]["title"] = queue["items"][0]["proposed_public_headline"]
    queue["items"][0]["location_name"] = "Superior and Douglas County, Wisconsin"
    queue["items"][0]["editorial_note"] = "Approved for the producer-to-loader regression."
    queue["items"][0]["proposed_section"] = "Core Food Pressure Signals"
    queue_path = tmp_path / "status/food-line/runtime/current-signal-review.json"
    _write_json(queue_path, queue)
    proposed = build_proposed_edition(queue)
    assert "exact_supporting_passage" not in proposed["items"][0]
    proposed["source_queue_sha256"] = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    _write_json(proposal_path, proposed)
    bundle = load_approved_proposal(tmp_path, proposal_path, DATE)
    assert bundle.source_rows[0]["exact_supporting_passage"] == queue["items"][0]["exact_supporting_passage"]
    assert proposed["items"][0]["location_name"] == "Superior and Douglas County, Wisconsin"
    assert bundle.source_rows[0]["location_name"] == "Superior and Douglas County, Wisconsin"


def test_arizona_and_original_nc_body_only_signals_retain_validate_and_load_as_approved_sources(tmp_path: Path) -> None:
    inbox = tmp_path / "data/dispatches/food-line/agent-inbox"
    inbox.mkdir(parents=True)
    findings = [
        {
            "finding_id": "arizona-body-only",
            "title": "Arizona pantry closes after losing its remaining food supply",
            "publisher": "Arizona Example News",
            "source_url": "https://example.org/arizona-pantry",
            "source_published_at": f"{DATE}T08:00:00Z",
            "exact_supporting_passage": "The Arizona pantry closed permanently after losing its remaining food supply.",
            "source_body_text": "The Arizona pantry closed permanently after losing its remaining food supply.",
            "geography_evidence_basis": "source_body",
            "pressure_type": "service closure",
            "pressure_summary": "An Arizona pantry closed permanently after losing its remaining supply.",
        },
        {
            "finding_id": "north-carolina-body-only",
            "title": "North Carolina pantry cannot supply everyone seeking assistance",
            "publisher": "North Carolina Example News",
            "source_url": "https://example.org/north-carolina-pantry",
            "source_published_at": f"{DATE}T09:00:00Z",
            "exact_supporting_passage": "The North Carolina pantry cannot supply food to everyone seeking assistance after an inventory shortage.",
            "source_body_text": "The North Carolina pantry cannot supply food to everyone seeking assistance after an inventory shortage.",
            "geography_evidence_basis": "source_body",
            "pressure_type": "supply shortage",
            "pressure_summary": "A North Carolina pantry cannot supply everyone seeking assistance.",
        },
    ]
    payload = {
        "agent_name": "Food Line Source Watch",
        "agent_run_id": "body-only-az-nc",
        "edition_date": DATE,
        "findings": findings,
    }
    inbox_path = inbox / "body-only-az-nc.json"
    _write_json(inbox_path, payload)

    adapted = adapt_food_line_agent_output(payload, agent_name="Food Line Source Watch", agent_run_id="body-only-az-nc")
    candidates = [map_finding_to_food_line_candidate(finding, edition_date=DATE) for finding in adapted]
    for candidate, state in zip(candidates, ("AZ", "NC")):
        assert candidate["eligible_for_review"] is True
        assert candidate["state"] == state
        assert candidate["location_name"] in {"Arizona", "North Carolina"}
        assert assess_review_retention(candidate, dispatch="food-line", edition_date=DATE)["eligible_for_review"] is True

    queue = _build_review_queue(tmp_path, DATE, inbox)
    assert len(queue["items"]) == 2
    assert [item["state"] for item in queue["items"]] == ["AZ", "NC"]
    assert queue["review_lifecycle"]["dispositions"] == {"retained_for_review": 2}
    assert validate_queue(queue) == queue

    for item in queue["items"]:
        apply_editorial_decision(
            queue,
            review_item_id=item["review_item_id"],
            decision="approve",
            decided_by="Independent reviewer",
            decided_at=f"{DATE}T12:00:00Z",
            editorial_note="Approved for the private body-only geography integration test.",
        )
    queue_path = tmp_path / "status/food-line/runtime/current-signal-review.json"
    write_json_atomic(queue_path, queue)
    proposal = build_proposed_edition(queue)
    proposal_path = tmp_path / "data/dispatches/food-line/review/proposed-editions" / f"{DATE}.json"
    proposal["source_queue_sha256"] = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    _write_json(proposal_path, proposal)
    bundle = load_approved_proposal(tmp_path, proposal_path, DATE)

    assert {row["state"] for row in bundle.source_rows} == {"AZ", "NC"}
    assert all(row["supported_product_geography"] is True for row in bundle.source_rows)
    assert all(row["source_public_story_eligible"] is True for row in bundle.source_rows)


def test_proposal_exact_supporting_passage_mismatch_fails_closed(tmp_path: Path) -> None:
    proposal_path, proposal, _ = _fixture(tmp_path)
    proposal["items"][0]["exact_supporting_passage"] = "A passage that was not reviewed."
    _write_json(proposal_path, proposal)
    with pytest.raises(ValueError, match="exact supporting passage"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_homepage_binds_previous_title_and_count_to_pages_when_source_history_is_incomplete(tmp_path: Path) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    current_edition = source / "output/site/food-line/editions/2026-10-15"
    current_edition.mkdir(parents=True)
    (current_edition / "edition_manifest.json").write_text(
        json.dumps({"edition_date": "2026-10-15", "public_signal_count": 3}), encoding="utf-8"
    )
    pages_edition = pages / "food-line/editions/2026-10-13"
    pages_edition.mkdir(parents=True)
    (pages_edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-10-13",
                "public_archive_title": "October 13 verified Food Line signals",
                "public_signal_count": 4,
            }
        ),
        encoding="utf-8",
    )
    pages_archive = pages / "food-line/archive.html"
    pages_archive.parent.mkdir(parents=True, exist_ok=True)
    pages_archive.write_text(
        "<ul>"
        '<li><a href="editions/2026-10-15/">2026-10-15 - Current Food Line</a></li>'
        '<li><a href="editions/2026-10-13/">2026-10-13 - October 13 verified Food Line signals</a></li>'
        "</ul>",
        encoding="utf-8",
    )

    runner._update_index_archive(
        source,
        "2026-10-15",
        "A bounded Food Line mission.",
        max_edition_date="2026-10-15",
        retrospective_pages_root=pages,
    )

    homepage = (source / "output/site/food-line/index.html").read_text(encoding="utf-8")
    assert "October 13 verified Food Line signals" in homepage
    assert "4 source-backed developments" in homepage
    assert "Food Line briefing" not in homepage
    assert "0 source-backed developments" not in homepage


@pytest.mark.parametrize("status", ["pending_editorial_review", "draft_pending_review"])
def test_pending_proposal_fails_closed(tmp_path: Path, status: str) -> None:
    proposal_path, proposal, _ = _fixture(tmp_path)
    proposal["draft_status"] = status
    _write_json(proposal_path, proposal)
    with pytest.raises(ValueError, match="draft_status"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


@pytest.mark.parametrize("decision", ["hold", "reject"])
def test_held_or_rejected_queue_item_fails_closed(tmp_path: Path, decision: str) -> None:
    proposal_path, proposal, queue = _fixture(tmp_path)
    queue["items"][0]["editorial_status"] = decision
    queue["items"][0]["decision_audit"]["decision"] = decision
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="editorial_status"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_proposal_date_and_queue_identity_mismatches_fail(tmp_path: Path) -> None:
    proposal_path, proposal, queue = _fixture(tmp_path)
    proposal["edition_date"] = "2026-07-30"
    _write_json(proposal_path, proposal)
    with pytest.raises(ValueError, match="date"):
        load_approved_proposal(tmp_path, proposal_path, DATE)
    proposal["edition_date"] = DATE
    queue["items"][0]["proposed_rank"] = 2
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="review-queue identity"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_historical_input_and_missing_decision_audit_fail(tmp_path: Path) -> None:
    proposal_path, proposal, queue = _fixture(tmp_path)
    queue["items"][0]["source_artifact_path"] = "data/agent-history/food-line/normalized/finding.json"
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="current nonhistorical"):
        load_approved_proposal(tmp_path, proposal_path, DATE)
    queue["items"][0]["source_artifact_path"] = "data/dispatches/food-line/agent-intake/2026-07-31/alert.json"
    queue["items"][0]["decision_audit"]["decided_by"] = ""
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="operator"):
        load_approved_proposal(tmp_path, proposal_path, DATE)
    queue["items"][0]["decision_audit"]["decided_by"] = "Operator"
    queue["items"][0]["decision_audit"]["decided_at"] = ""
    _rewrite(tmp_path, proposal_path, proposal, queue)
    with pytest.raises(ValueError, match="timestamp"):
        load_approved_proposal(tmp_path, proposal_path, DATE)


def test_canonical_generation_records_hashes_and_keeps_private_ids_out_of_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_path, _, _ = _fixture(tmp_path)
    pages = tmp_path / "pages"
    pages.mkdir()
    monkeypatch.setattr(runner, "PAGES_REPO", pages)
    result = runner.run_food_line_dispatch(
        tmp_path,
        DATE,
        generate_audio=False,
        approved_proposal_path=proposal_path,
    )
    assert result["ok"] is True
    edition = tmp_path / "output/site/food-line/editions" / DATE
    manifest = json.loads((edition / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved_proposal_sha256"] == hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    assert manifest["publication_status"] == "unpublished"
    assert manifest["pages_status"] == "not_synced"
    assert manifest["audio_status"] == "not_generated"
    html_text = "\n".join(path.read_text(encoding="utf-8") for path in edition.glob("*.html"))
    assert SUMMARY in html_text
    assert "about 960 people" in html_text
    assert "approximately 34,000 pounds" in html_text
    assert "960 households" not in html_text
    assert "documented turnaways" not in html_text
    assert "food-line-current-test" not in html_text
    assert "finding_test" not in html_text
    assert "data/dispatches/food-line" not in html_text
    assert "1 saved source record from 1 publisher was available" in html_text
    assert "Source mix: 1 signal from 1 publisher" in html_text
    assert "1 source was used on the public page" in html_text
    assert not (tmp_path / "output/site/food-line/audio").exists()
    assert not (tmp_path / "output/site/food-line/map").exists()
    assert not (tmp_path / "output/site/food-line/podcast.xml").exists()
    assert not (tmp_path / "data/bluesky").exists()
    assert not (tmp_path / "schedules").exists()


def test_approved_generation_preserves_full_pages_history_for_sparse_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_date = "2026-10-15"
    proposal_path, proposal, queue = _fixture(tmp_path)
    proposal["edition_date"] = target_date
    queue["edition_date"] = target_date
    queue["items"][0]["freshness_check"]["edition_date"] = target_date
    _rewrite(tmp_path, proposal_path, proposal, queue)
    pages = tmp_path / "pages"
    history_dates = [
        "2026-06-06", "2026-06-07", "2026-06-09", "2026-06-13", "2026-06-14",
        "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-20",
        "2026-06-21", "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25",
        "2026-06-26", "2026-06-27", "2026-06-28", "2026-06-29", "2026-06-30",
        "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05",
        "2026-07-27", "2026-07-28", "2026-07-31", "2026-08-05", "2026-08-09",
        "2026-08-14", "2026-08-15", "2026-08-16", "2026-08-30", "2026-08-31",
        "2026-09-12", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
        "2026-10-03", "2026-10-04", "2026-10-07", "2026-10-09", "2026-10-11",
        "2026-10-12", "2026-10-13",
    ]
    pages_editions = pages / "food-line" / "editions"
    archive_items = []
    for history_date in history_dates:
        edition = pages_editions / history_date
        edition.mkdir(parents=True, exist_ok=True)
        source_id = f"historical-{history_date}"
        summary = f"Preserved public Food Line history for {history_date}."
        _write_json(
            edition / "edition_manifest.json",
            {
                "dispatch_slug": "food-line",
                "edition_date": history_date,
                "public_rendered": True,
                "edition_mode": "current_update",
                "source_freshness_status": "current",
                "freshness_window_days": 3,
                "qualified_primary_count": 1,
                "public_signal_count": 1,
                "public_archive_title": f"Preserved Food Line history {history_date}",
                "public_archive_subtitle": summary,
                "lead_source_record_id": source_id,
            },
        )
        _write_json(
            edition / "sources_manifest.json",
            [{
                "source_record_id": source_id,
                "pressure_signal": True,
                "title": f"Historical source {history_date}",
                "publisher": "Historical source",
                "url": f"https://example.com/food/{history_date}",
                "summary_or_snippet": summary,
            }],
        )
        _write_json(edition / "curation_manifest.json", {"stories": []})
        (edition / "index.html").write_text(f"<html><body><h1>{history_date}</h1></body></html>", encoding="utf-8")
        archive_items.append(
            f'<li><a href="editions/{history_date}/">{history_date} - Preserved Food Line history {history_date}</a>'
            f"<br><small>{summary}</small></li>"
        )
    archive = pages / "food-line" / "archive.html"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("<ul>" + "".join(reversed(archive_items)) + "</ul>", encoding="utf-8")

    monkeypatch.setattr(runner, "PAGES_REPO", pages)
    result = runner.run_food_line_dispatch(
        tmp_path,
        target_date,
        generate_audio=False,
        approved_proposal_path=proposal_path,
    )

    assert result["ok"] is True
    output_archive = (tmp_path / "output/site/food-line/archive.html").read_text(encoding="utf-8")
    output_dates = sorted(set(re.findall(r"editions/(\d{4}-\d{2}-\d{2})/", output_archive)))
    assert output_dates == sorted(set([*history_dates, target_date]))
    current_manifest = json.loads(
        (tmp_path / "output/site/food-line/editions" / target_date / "edition_manifest.json").read_text(encoding="utf-8")
    )
    assert current_manifest["previous_edition_date"] == "2026-10-13"
    homepage = (tmp_path / "output/site/food-line/index.html").read_text(encoding="utf-8")
    assert 'href="editions/2026-10-13/"' in homepage


def test_current_release_manifest_includes_existing_food_line_social_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_path, _, _ = _fixture(tmp_path)
    pages = tmp_path / "pages"
    pages.mkdir()
    edition = tmp_path / "output/site/food-line/editions" / DATE
    edition.mkdir(parents=True)
    (edition / "social-card.png").write_bytes(b"private-preview-card")
    monkeypatch.setattr(runner, "PAGES_REPO", pages)

    result = runner.run_food_line_dispatch(
        tmp_path,
        DATE,
        generate_audio=False,
        approved_proposal_path=proposal_path,
    )

    assert result["ok"] is True
    manifest = json.loads(
        (tmp_path / "data/dispatches/food-line/review/releases" / f"{DATE}.json").read_text(
            encoding="utf-8"
        )
    )
    entry = next(item for item in manifest["entries"] if item["source_path"].endswith("/social-card.png"))
    assert entry["pages_path"] == f"food-line/editions/{DATE}/social-card.png"
    assert entry["source_sha256"] == hashlib.sha256((edition / "social-card.png").read_bytes()).hexdigest()
