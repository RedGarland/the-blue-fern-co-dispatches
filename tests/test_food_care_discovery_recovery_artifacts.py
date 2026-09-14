from __future__ import annotations

import json
from pathlib import Path


def test_september_14_food_care_recovery_artifact_is_review_only() -> None:
    path = (
        Path("data")
        / "private-agent-handoff"
        / "discovery-recovery"
        / "food-care"
        / "2026-09-14"
        / "recovery-review-intake.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["items"]

    assert payload["recovery_scope"] == "bounded_non_public_human_review_intake"
    assert payload["original_production_discovery_lineage_fabricated"] is False
    assert payload["publication_eligible"] is False
    assert payload["publication_approval"] is False
    assert payload["publication_performed"] is False
    assert payload["pages_authorized"] is False
    assert len(items) == 4
    assert {item["dispatch"] for item in items} == {"food-line", "care-line"}
    assert all(item["review_retention_disposition"] == "retained_for_review" for item in items)
    assert all(item["eligible_for_automatic_publication"] is False for item in items)
    assert all(item["publication_eligible"] is False for item in items)
    assert all(item["publication_approval"] is False for item in items)
    assert all(item["publication_performed"] is False for item in items)
    assert all(item["production_artifact_present"] is False for item in items)
