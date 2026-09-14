from __future__ import annotations

import json
from pathlib import Path


def test_september_14_ice_recovery_artifact_is_review_only() -> None:
    path = (
        Path("data")
        / "private-agent-handoff"
        / "discovery-recovery"
        / "ice"
        / "2026-09-14"
        / "recovery-review-intake.json"
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "bluefern.ice.discovery_recovery_review_intake.v1"
    assert payload["provenance_class"] == "operator_recovered_public_source_recall_evidence"
    assert payload["recovery_scope"] == "bounded_non_public_human_review_intake"
    assert payload["original_production_discovery_lineage_fabricated"] is False
    assert payload["publication_eligible"] is False
    assert payload["publication_approval"] is False
    assert payload["publication_performed"] is False
    assert payload["public_generation_authorized"] is False
    assert payload["pages_authorized"] is False
    assert payload["summary"] == {
        "item_count": 4,
        "public_side_effects": False,
        "publication_approval": 0,
        "publication_eligible": 0,
        "retained_for_review": 4,
    }

    items = payload["items"]
    assert len(items) == 4
    assert {item["review_retention_disposition"] for item in items} == {"retained_for_review"}
    assert {item["production_artifact_present"] for item in items} == {False}
    assert {item["publication_eligible"] for item in items} == {False}
    assert {item["publication_approval"] for item in items} == {False}
    assert {item["publication_performed"] for item in items} == {False}
    assert all(item["source_url"].startswith("https://") for item in items)
