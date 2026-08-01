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
    validate_queue,
    write_json_atomic,
    write_proposed_edition,
)


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
    assert proposed["draft_status"] == "draft_pending_editorial_review"
    assert proposed["selected_item_count"] == 1
    assert proposed["publication_eligible"] is False
    assert proposed["publication_approval"] is False


def test_pending_item_can_enter_private_preview_without_publication_authority() -> None:
    proposed = build_proposed_edition(_queue([_item()]))
    assert proposed["draft_status"] == "draft_pending_editorial_review"
    assert proposed["selected_item_count"] == 1
    assert proposed["publication_eligible"] is False
    assert proposed["publication_approval"] is False


def test_empty_queue_writes_only_private_blocked_preview(tmp_path: Path) -> None:
    payload = _queue()
    json_path, markdown_path, proposed = write_proposed_edition(tmp_path, payload)
    assert proposed["draft_status"] == "blocked_no_reviewable_current_signals"
    assert proposed["selected_item_count"] == 0
    assert json_path == tmp_path / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json"
    assert markdown_path.exists()
    assert not (tmp_path / "output/site").exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["published"] is False
