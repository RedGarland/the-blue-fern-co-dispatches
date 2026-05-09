from __future__ import annotations

from typing import Any


CATEGORIES = [
    "Infrastructure",
    "Public safety",
    "Healthcare",
    "Housing and homelessness",
    "Economy and labor",
    "Food and agriculture",
    "Environment and climate",
    "Government and public services",
    "Transportation",
    "Energy and utilities",
]

EXCLUSION_TERMS = {
    "sports": ["sports", "team", "game", "coach", "postseason", "tournament"],
    "entertainment": ["celebrity", "movie", "concert", "festival", "album"],
    "opinion": ["opinion", "editorial", "letter to the editor"],
    "lifestyle": ["recipe", "restaurant review", "fashion", "travel tips"],
    "historical trivia": ["on this day", "history quiz", "trivia"],
}

CATEGORY_KEYWORDS = {
    "Infrastructure": ["bridge", "road", "water", "sewer", "dam", "maintenance"],
    "Public safety": ["emergency", "police", "fire", "evacuation", "preparedness", "public safety"],
    "Healthcare": ["hospital", "health", "clinic", "medicaid"],
    "Housing and homelessness": ["housing", "homeless", "shelter", "rent"],
    "Economy and labor": ["labor", "strike", "jobs", "wages", "economy"],
    "Food and agriculture": ["farm", "agriculture", "food", "crop"],
    "Environment and climate": ["climate", "wildfire", "flood", "air quality", "environment"],
    "Government and public services": ["agency", "public services", "budget", "council", "government"],
    "Transportation": ["transportation", "transit", "bridge", "highway", "road", "rail"],
    "Energy and utilities": ["power", "energy", "utility", "electric", "grid"],
}

RELIABILITY_SCORE = {
    "official": 20,
    "editorial-record": 16,
    "reputable": 14,
    "unknown": 8,
}


def exclusion_reason(record: dict[str, Any]) -> str | None:
    text = f"{record.get('title', '')} {record.get('text', '')} {record.get('category_hint', '')}".lower()
    if not record.get("title") or not record.get("canonical_url"):
        return "missing usable source title or URL"
    for reason, terms in EXCLUSION_TERMS.items():
        if any(term in text for term in terms):
            return reason
    return None


def assign_category(record: dict[str, Any]) -> str:
    hint = str(record.get("category_hint") or "").strip()
    if hint in CATEGORIES:
        return hint
    text = f"{record.get('title', '')} {record.get('text', '')}".lower()
    best_category = "Government and public services"
    best_hits = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in text)
        if hits > best_hits:
            best_category = category
            best_hits = hits
    return best_category


def score_record(record: dict[str, Any], reliability_tier: str = "unknown", duplicate_count: int = 1) -> dict[str, Any]:
    text = f"{record.get('title', '')} {record.get('text', '')}".lower()
    category = assign_category(record)
    regional_relevance_score = 15 if record.get("region_scope") in {"WA", "OR", "ID", "regional"} else 5
    systems_impact_score = 20 if any(keyword in text for words in CATEGORY_KEYWORDS.values() for keyword in words) else 5
    public_consequence_score = 15 if category in CATEGORIES else 5
    recency_score = 10 if record.get("published_at") else 5
    source_reliability_score = RELIABILITY_SCORE.get(reliability_tier, RELIABILITY_SCORE["unknown"])
    multi_source_score = 5 if duplicate_count > 1 else 0
    duplicate_penalty = 0
    low_signal_penalty = 15 if systems_impact_score <= 5 else 0
    total_score = regional_relevance_score + systems_impact_score + public_consequence_score + recency_score + source_reliability_score + multi_source_score - duplicate_penalty - low_signal_penalty
    reasons = [
        f"category={category}",
        f"region_scope={record.get('region_scope')}",
        f"source_reliability={reliability_tier}",
    ]
    return {
        "category": category,
        "regional_relevance_score": regional_relevance_score,
        "systems_impact_score": systems_impact_score,
        "public_consequence_score": public_consequence_score,
        "recency_score": recency_score,
        "source_reliability_score": source_reliability_score,
        "multi_source_score": multi_source_score,
        "duplicate_penalty": duplicate_penalty,
        "low_signal_penalty": low_signal_penalty,
        "total_score": total_score,
        "scoring_reasons": reasons,
    }
