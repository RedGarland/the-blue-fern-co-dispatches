from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_current_review import (
    ALLOWED_DECISIONS,
    CURRENT_PRODUCTION_SCOPE,
    HISTORICAL_ROOTS,
    QUEUE_SCHEMA_VERSION,
    apply_editorial_decision,
    build_proposed_edition,
    load_queue,
    payload_sha256,
    render_operator_markdown,
    validate_queue,
    write_json_atomic,
    write_proposed_edition,
)
from scripts import manage_food_line_current_review as review_cli


DATE = "2026-07-31"


def _item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "review_item_id": "food-line-current-001",
        "source_finding_or_intake_id": "finding-current-001",
        "source_artifact_path": "data/dispatches/food-line/agent-intake/2026-07-31/run.json",
        "source_url": "https://example.org/current-food-pressure",
        "canonical_source_url": "https://example.org/current-food-pressure",
        "publisher": "Example News",
        "source_published_at": "2026-07-31T08:00:00-07:00",
        "title": "Pantry closes after supply loss",
        "exact_supporting_passage": "The pantry closed Friday after losing its remaining food supply.",
        "proposed_public_headline": "Local pantry closes after supply loss",
        "proposed_public_summary": "Example News reports that a local pantry closed after losing its food supply.",
        "location_name": "Example City",
        "state": "CA",
        "location_scope": "city",
        "pressure_type": "service_closure",
        "affected_groups": ["pantry clients"],
        "why_it_matters": "A food-access point is no longer operating.",
        "evidence_level": "direct_reporting",
        "confidence": "high",
        "uncertainty_note": "The duration of the closure is not yet known.",
        "duplicate_check": {"status": "not_published", "matched_edition": None},
        "freshness_check": {"status": "current", "age_days": 0, "edition_date": "2026-07-31"},
        "proposed_section": "Core Food Pressure Signals",
        "proposed_rank": 1,
        "editorial_status": "pending_editorial_review",
        "editorial_note": "Pending operator decision.",
        "publication_eligible": False,
    }
    item.update(overrides)
    return item


def _queue(items: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": "food-line-current-review-2026-07-31",
        "edition_date": "2026-07-31",
        "production_scope": CURRENT_PRODUCTION_SCOPE,
        "historical_roots_excluded": list(HISTORICAL_ROOTS),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "items": list(items or []),
    }


def test_queue_accepts_current_item_and_deterministic_atomic_write(tmp_path: Path) -> None:
    payload = _queue([_item(source_url="https://example.org/current-food-pressure?utm_source=agent")])
    path = tmp_path / "queue.json"
    write_json_atomic(path, payload)
    first = path.read_bytes()
    write_json_atomic(path, payload)
    assert path.read_bytes() == first
    assert load_queue(path) == payload
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "source_path",
    (
        "data/agent-history/food-line/normalized/record.json",
        "data/agent-history-staging/food-line/alert.txt",
    ),
)
def test_historical_paths_fail_closed(source_path: str) -> None:
    with pytest.raises(ValueError, match="historical source artifacts cannot enter"):
        validate_queue(_queue([_item(source_artifact_path=source_path)]))


def test_private_payload_and_publication_eligibility_fail_closed() -> None:
    with pytest.raises(ValueError, match="private payload fields"):
        validate_queue(_queue([_item(duplicate_check={"status": "not_published", "raw_agent_payload": {"hidden": True}})]))
    with pytest.raises(ValueError, match="publication_eligible must remain false"):
        validate_queue(_queue([_item(publication_eligible=True)]))


def test_current_queue_enforces_three_day_freshness() -> None:
    validate_queue(
        _queue(
            [
                _item(
                    source_published_at="2026-07-28T08:00:00-07:00",
                    freshness_check={"status": "current", "age_days": 3, "edition_date": "2026-07-31"},
                )
            ]
        )
    )
    with pytest.raises(ValueError, match="outside the 3-day current review window"):
        validate_queue(
            _queue(
                [
                    _item(
                        source_published_at="2026-07-27T08:00:00-07:00",
                        freshness_check={"status": "current", "age_days": 4, "edition_date": "2026-07-31"},
                    )
                ]
            )
        )


def test_editorial_decision_never_grants_publication_authority() -> None:
    payload = _queue([_item()])
    apply_editorial_decision(
        payload,
        review_item_id="food-line-current-001",
        decision="approve",
        decided_by="operator",
        decided_at="2026-07-31T17:00:00Z",
        editorial_note="Approved for draft assembly only.",
    )
    assert payload["items"][0]["editorial_status"] == "approve"
    assert payload["items"][0]["publication_eligible"] is False
    proposed = build_proposed_edition(payload)
    assert proposed["draft_status"] == "draft_approved_pending_publication"
    assert proposed["selected_item_count"] == 1
    assert proposed["approved_item_count"] == 1
    assert proposed["pending_item_count"] == 0
    assert proposed["rejected_item_count"] == 0
    assert proposed["publication_eligible"] is False
    assert proposed["publication_approval"] is False


def test_pending_item_can_enter_private_preview_without_publication_authority() -> None:
    proposed = build_proposed_edition(_queue([_item()]))
    assert proposed["draft_status"] == "draft_pending_editorial_review"
    assert proposed["selected_item_count"] == 1
    assert proposed["approved_item_count"] == 0
    assert proposed["pending_item_count"] == 1
    assert proposed["publication_eligible"] is False
    assert proposed["publication_approval"] is False


def test_identical_editorial_decision_preserves_original_audit_timestamp() -> None:
    payload = _queue([_item()])
    kwargs = {
        "review_item_id": "food-line-current-001",
        "decision": "approve",
        "decided_by": "operator",
        "editorial_note": "Approved for draft assembly only.",
    }
    apply_editorial_decision(payload, decided_at="2026-07-31T17:00:00Z", **kwargs)
    first_hash = payload_sha256(payload)

    apply_editorial_decision(payload, decided_at="2026-07-31T18:00:00Z", **kwargs)

    assert payload_sha256(payload) == first_hash
    assert payload["items"][0]["decision_audit"]["decided_at"] == "2026-07-31T17:00:00Z"


def test_empty_queue_writes_only_private_blocked_preview(tmp_path: Path) -> None:
    payload = _queue()
    json_path, markdown_path, proposed = write_proposed_edition(tmp_path, payload)
    assert proposed["draft_status"] == "blocked_no_reviewable_current_signals"
    assert proposed["selected_item_count"] == 0
    assert json_path == tmp_path / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json"
    assert markdown_path.exists()
    assert not (tmp_path / "output/site").exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["published"] is False


def test_operator_preview_uses_established_food_line_structure() -> None:
    queue = _queue([_item()])
    proposed = build_proposed_edition(queue)
    preview = render_operator_markdown(queue, proposed)

    for heading in (
        "## Today’s Read",
        "## At a Glance",
        "## Core Food Pressure Signals",
        "## Other Food Line Signals",
        "## Source Mix",
        "## Source Note",
    ):
        assert heading in preview
    assert "review_item_id" not in preview
    assert "This edition tracks 1 current, source-traceable food-pressure signal." in preview
    assert "editorial approval" not in proposed["layout"]["source_note"]


def test_release_readiness_writer_requires_approved_current_review_and_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    queue = _queue(
        [
            _item(editorial_status="approve", editorial_note="Approved."),
            _item(
                review_item_id="food-line-current-002",
                source_finding_or_intake_id="finding-current-002",
                source_artifact_path="data/dispatches/food-line/agent-intake/2026-07-31/run-2.json",
                source_url="https://example.org/current-food-pressure-2",
                canonical_source_url="https://example.org/current-food-pressure-2",
                publisher="Example News 2",
                source_published_at="2026-07-31T09:00:00-07:00",
                title="Second pantry signal",
                exact_supporting_passage="Another pantry signal is present.",
                proposed_public_headline="Second pantry signal",
                proposed_public_summary="Second pantry signal summary",
                location_name="Example County",
                state="CA",
                location_scope="county",
                pressure_type="service_closure",
                affected_groups=["pantry clients"],
                why_it_matters="Another access issue.",
                evidence_level="direct_reporting",
                confidence="high",
                uncertainty_note="None.",
                duplicate_check={"status": "not_published", "matched_edition": None},
                freshness_check={"status": "current", "age_days": 0, "edition_date": "2026-07-31"},
                proposed_section="Core Food Pressure Signals",
                proposed_rank=2,
                editorial_status="approve",
                editorial_note="Approved.",
                publication_eligible=False,
            ),
        ]
    )
    review_path = root / "status" / "food-line" / "runtime" / "current-signal-review.json"
    write_json_atomic(review_path, queue)
    proposal_path, _, proposal = write_proposed_edition(root, queue)
    readiness_path = root / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json"

    rc = review_cli.main(["release-ready"])
    assert rc == 0
    first = readiness_path.read_bytes()
    rc = review_cli.main(["release-ready"])
    assert rc == 0
    assert readiness_path.read_bytes() == first

    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert readiness["schema_version"] == "food_line_release_readiness_v1"
    assert readiness["edition_date"] == DATE
    assert readiness["status"] == "approved_current_review_ready_for_source_generation"
    assert readiness["approved_signal_ids"] == ["food-line-current-001", "food-line-current-002"]
    assert readiness["selected_item_count"] == 2
    assert readiness["publication_approval"] is True
    assert readiness["approved_proposal_path"] == "data/dispatches/food-line/review/proposed-editions/2026-07-31.json"
    assert readiness["review_snapshot_path"] == "data/dispatches/food-line/review/signal-reviews/2026-07-31.json"


def test_release_readiness_blocks_on_pending_items(tmp_path: Path) -> None:
    root = tmp_path
    queue = _queue([_item(), _item(review_item_id="food-line-current-002", source_finding_or_intake_id="finding-current-002", proposed_rank=2, editorial_status="pending_editorial_review")])
    review_path = root / "status" / "food-line" / "runtime" / "current-signal-review.json"
    write_json_atomic(review_path, queue)
    proposal_path, _, proposal = write_proposed_edition(root, queue)
    with pytest.raises(ValueError, match="draft_approved_pending_publication"):
        review_cli.build_release_readiness_record(
            proposal=proposal,
            queue=queue,
            proposal_path=proposal_path.relative_to(root),
            proposal_sha256=payload_sha256(proposal),
            snapshot_path=(root / "data" / "dispatches" / "food-line" / "review" / "signal-reviews" / f"{DATE}.json").relative_to(root),
            snapshot_sha256=payload_sha256(queue),
        )


def test_release_ready_command_fails_when_proposal_hash_mismatches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    queue = _queue([_item(editorial_status="approve")])
    write_json_atomic(root / "status" / "food-line" / "runtime" / "current-signal-review.json", queue)
    proposal_path, _, proposal = write_proposed_edition(root, queue)
    proposal["approved_item_count"] = 999
    write_json_atomic(proposal_path, proposal)
    readiness_path = root / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json"

    rc = review_cli.main(["release-ready"])
    assert rc != 0
    assert not readiness_path.exists()


def test_release_readiness_blocks_on_snapshot_mismatch(tmp_path: Path) -> None:
    root = tmp_path
    queue = _queue([_item(editorial_status="approve")])
    write_json_atomic(root / "status" / "food-line" / "runtime" / "current-signal-review.json", queue)
    proposal_path, _, proposal = write_proposed_edition(root, queue)
    proposal["approved_signal_ids"] = ["wrong-id"]
    write_json_atomic(proposal_path, proposal)
    with pytest.raises(ValueError, match="current approved review queue"):
        review_cli.build_release_readiness_record(
            proposal=proposal,
            queue=queue,
            proposal_path=proposal_path.relative_to(root),
            proposal_sha256=payload_sha256(proposal),
            snapshot_path=(root / "data" / "dispatches" / "food-line" / "review" / "signal-reviews" / f"{DATE}.json").relative_to(root),
            snapshot_sha256=payload_sha256(queue),
        )


def test_release_readiness_blocks_on_unapproved_selected_item(tmp_path: Path) -> None:
    root = tmp_path
    queue = _queue([_item(editorial_status="hold", editorial_note="Hold for verification.")])
    write_json_atomic(root / "status" / "food-line" / "runtime" / "current-signal-review.json", queue)
    proposal_path, _, proposal = write_proposed_edition(root, queue)
    with pytest.raises(ValueError, match="draft_approved_pending_publication"):
        review_cli.build_release_readiness_record(
            proposal=proposal,
            queue=queue,
            proposal_path=proposal_path.relative_to(root),
            proposal_sha256=payload_sha256(proposal),
            snapshot_path=(root / "data" / "dispatches" / "food-line" / "review" / "signal-reviews" / f"{DATE}.json").relative_to(root),
            snapshot_sha256=payload_sha256(queue),
        )


def test_release_ready_command_blocks_on_malformed_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    queue = _queue([_item(editorial_status="approve")])
    write_json_atomic(root / "status" / "food-line" / "runtime" / "current-signal-review.json", queue)
    proposal_path, _, _ = write_proposed_edition(root, queue)
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal.pop("edition_date")
    write_json_atomic(proposal_path, proposal)

    rc = review_cli.main(["release-ready"])
    assert rc != 0
