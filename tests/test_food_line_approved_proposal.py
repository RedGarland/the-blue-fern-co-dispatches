from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_approved_proposal import load_approved_proposal
from bluefern_dispatches.food_line_current_review import build_proposed_edition
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
    assert row["map_eligible"] is False


def test_current_review_producer_output_loads_and_keeps_queue_evidence(tmp_path: Path) -> None:
    proposal_path, _, queue = _fixture(tmp_path)
    queue["historical_roots_excluded"] = ["data/agent-history", "data/agent-history-staging"]
    queue["allowed_decisions"] = ["approve", "approve_with_edit", "hold", "reject"]
    queue["items"][0]["title"] = queue["items"][0]["proposed_public_headline"]
    queue["items"][0]["location_name"] = "Superior"
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
