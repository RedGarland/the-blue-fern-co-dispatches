from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_care_has_cms_public_notices_primary_source() -> None:
    payload = json.loads((ROOT / "data/dispatches/care-line/source_registry.json").read_text(encoding="utf-8"))
    row = next(item for item in payload["sources"] if item["source_id"] == "cms-public-notices")
    assert row["enabled"] is True
    assert row["authority_level"] == "primary"
    assert "certification-compliance/public-notices" in row["feed_url"]
    assert "Termination" not in row.get("notes", "") or "auto-approved" in row["notes"]


def test_gaza_has_targeted_ocha_and_who_health_access_queries() -> None:
    payload = yaml.safe_load((ROOT / "data/dispatches/gaza/sources.yml").read_text(encoding="utf-8"))
    rows = [row for tier in payload["tiers"].values() for row in tier]
    by_id = {row["source_id"]: row for row in rows}
    for source_id in ("ocha-gaza-health-access-query", "who-gaza-health-capacity-query"):
        assert by_id[source_id]["enabled"] is True
        assert by_id[source_id]["discovery_role"] == "strong_ground_development"
        assert "health" in by_id[source_id]["query"].lower() or "hospital" in by_id[source_id]["query"].lower()


def test_food_has_essential_access_disruption_query_family() -> None:
    payload = json.loads((ROOT / "data/dispatches/food-line/discovery_expansion_config.json").read_text(encoding="utf-8"))
    row = next(item for item in payload["query_families"] if item["query_family"] == "essential_food_access_disruption")
    joined = " ".join(row["templates"]).lower()
    assert "only grocery store" in joined
    assert "food distribution" in joined
    assert "storm" in joined
