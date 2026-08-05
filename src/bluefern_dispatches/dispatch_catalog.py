from __future__ import annotations

from typing import Any

SUPPORTED_LIFECYCLE_STATES = ("active", "future", "paused", "archived", "deprecated")

DISPATCH_CATALOG: dict[str, dict[str, Any]] = {
    "gaza": {
        "label": "Gaza",
        "public_visible": True,
        "lifecycle_state": "active",
    },
    "cascadia": {
        "label": "Cascadia",
        "public_visible": True,
        "lifecycle_state": "future",
    },
    "american-pressure": {
        "label": "American Pressure",
        "public_visible": True,
        "lifecycle_state": "future",
    },
    "food-line": {
        "label": "Food Line Dispatch",
        "public_visible": True,
        "lifecycle_state": "active",
    },
    "care-line": {
        "label": "The Care Line Dispatch",
        "public_visible": True,
        "lifecycle_state": "active",
    },
}

DISPATCH_LABELS = {slug: str(meta.get("label") or slug) for slug, meta in DISPATCH_CATALOG.items()}


def dispatch_public_visible(slug: str) -> bool:
    meta = DISPATCH_CATALOG.get(slug, {})
    return bool(meta.get("public_visible", True))


def dispatch_lifecycle_state(slug: str) -> str:
    meta = DISPATCH_CATALOG.get(slug, {})
    value = str(meta.get("lifecycle_state") or "active").strip().lower()
    if value not in SUPPORTED_LIFECYCLE_STATES:
        return "active"
    return value


def dispatch_is_active(slug: str) -> bool:
    return dispatch_lifecycle_state(slug) == "active"


def active_dispatch_slugs() -> tuple[str, ...]:
    return tuple(slug for slug in DISPATCH_CATALOG if dispatch_is_active(slug))
