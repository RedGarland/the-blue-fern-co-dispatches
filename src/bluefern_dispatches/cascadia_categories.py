from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CascadiaCategory:
    category_id: str
    category_label: str
    category_family: str


CATEGORY_REGISTRY: dict[str, CascadiaCategory] = {
    "energy_utilities": CascadiaCategory("energy_utilities", "Energy and utilities", "infrastructure"),
    "environment_climate": CascadiaCategory("environment_climate", "Environment and climate", "environment"),
    "public_safety": CascadiaCategory("public_safety", "Public safety", "safety"),
    "government_public_services": CascadiaCategory("government_public_services", "Government and public services", "governance"),
    "housing_homelessness": CascadiaCategory("housing_homelessness", "Housing and homelessness", "community_stability"),
    "transportation": CascadiaCategory("transportation", "Transportation", "infrastructure"),
    "healthcare": CascadiaCategory("healthcare", "Healthcare", "community_stability"),
    "infrastructure": CascadiaCategory("infrastructure", "Infrastructure", "infrastructure"),
    "economy_labor": CascadiaCategory("economy_labor", "Economy and labor", "community_stability"),
    "food_agriculture": CascadiaCategory("food_agriculture", "Food and agriculture", "community_stability"),
    "corrections_detention": CascadiaCategory("corrections_detention", "Corrections and detention", "governance"),
}

LEGACY_LABEL_TO_ID: dict[str, str] = {
    "energy and utilities": "energy_utilities",
    "environment and climate": "environment_climate",
    "public safety": "public_safety",
    "government and public services": "government_public_services",
    "housing and homelessness": "housing_homelessness",
    "transportation": "transportation",
    "healthcare": "healthcare",
    "infrastructure": "infrastructure",
    "economy and labor": "economy_labor",
    "food and agriculture": "food_agriculture",
    "corrections and detention": "corrections_detention",
}


def canonical_category_id(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("_", " ")
    if not text:
        return None
    if text in LEGACY_LABEL_TO_ID:
        return LEGACY_LABEL_TO_ID[text]
    compact = text.replace(" ", "_")
    if compact in CATEGORY_REGISTRY:
        return compact
    return None


def category_label_for(category_id: str | None, fallback: str | None = None) -> str:
    if category_id and category_id in CATEGORY_REGISTRY:
        return CATEGORY_REGISTRY[category_id].category_label
    return str(fallback or "").strip()

