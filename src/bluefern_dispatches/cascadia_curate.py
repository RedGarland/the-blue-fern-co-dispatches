from __future__ import annotations

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any
from collections import Counter

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT, DEFAULT_SOURCES_PATH, load_sources
from bluefern_dispatches.cascadia_score import exclusion_reason, score_record


def story_id_for(record: dict[str, Any]) -> str:
    raw = "|".join([record.get("source_record_id", ""), record.get("canonical_url", ""), record.get("title", "")])
    return f"story-{sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def deterministic_summary(record: dict[str, Any]) -> str:
    title = clean_sentence(record.get("title") or "")
    snippet = clean_sentence(record.get("text") or record.get("summary_or_snippet") or "")
    publisher = clean_sentence(record.get("publisher") or record.get("source_name") or "")
    category = clean_sentence(record.get("category_hint") or "")
    region = region_label(record)
    if snippet and snippet.lower() != title.lower():
        summary = trim_to_sentences(snippet)
    elif title:
        category_text = category.replace("_", " ") if category else "public-systems"
        region_text = f" for {region}" if region else ""
        summary = f"This source was flagged as a {category_text} signal{region_text} based on its title and source metadata."
    else:
        source_text = f" from {publisher}" if publisher else ""
        summary = f"This source{source_text} was flagged as a public-systems signal based on source metadata."
    rationale = supported_rationale(category, region)
    if rationale and rationale.lower() not in summary.lower():
        return f"{summary} {rationale}"
    return summary


def why_it_matters(record: dict[str, Any], category: str | None = None) -> str:
    category_text = clean_sentence(category or record.get("category_hint") or "public systems").replace("_", " ").lower()
    region = region_label(record)
    category_label, rationale = category_rationale(category_text)
    prefix = f"In {region}, " if region and region in {"Washington", "Oregon", "Idaho"} else ""
    return f"{prefix}{category_label} signals can affect {rationale}."


def category_rationale(category_text: str) -> tuple[str, str]:
    if any(term in category_text for term in ("environment", "climate", "wildfire", "water")):
        return "Environmental and climate", "public safety, infrastructure planning, and regional resilience"
    if any(term in category_text for term in ("transportation", "infrastructure", "freight", "bridge", "road")):
        return "Transportation", "mobility, emergency access, freight movement, and infrastructure maintenance"
    if any(term in category_text for term in ("public safety", "emergency", "disaster", "fire")):
        return "Public safety", "emergency response, household stability, and local service coordination"
    if any(term in category_text for term in ("housing", "homeless")):
        return "Housing", "household stability, local services, and public-sector capacity"
    if any(term in category_text for term in ("health", "hospital", "healthcare", "public health")):
        return "Health system", "access to care, public services, and community resilience"
    return "Public systems", "public services, local planning, and regional resilience"


def clean_sentence(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return re.sub(r"\s+([?.!,;:])", r"\1", text).strip()


def trim_to_sentences(value: str, max_sentences: int = 2, max_chars: int = 320) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]
    summary = " ".join(parts[:max_sentences]) if parts else value
    if len(summary) <= max_chars:
        return summary
    cut = summary[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}."


def region_label(record: dict[str, Any]) -> str:
    value = str(record.get("state_hint") or record.get("region_scope") or "").strip()
    return {"WA": "Washington", "OR": "Oregon", "ID": "Idaho", "PNW": "the Pacific Northwest", "regional": "the Pacific Northwest"}.get(value, value)


def supported_rationale(category: str, region: str) -> str:
    category_text = category.replace("_", " ").lower()
    if not category_text:
        return ""
    public_system_terms = {
        "infrastructure",
        "transportation",
        "energy",
        "utilities",
        "healthcare",
        "public safety",
        "housing",
        "homelessness",
        "environment",
        "climate",
        "government",
        "public services",
        "economy",
        "labor",
        "food",
        "agriculture",
    }
    if not any(term in category_text for term in public_system_terms):
        return ""
    region_text = f" in {region}" if region else ""
    return f"It is included because the source metadata ties it to {category_text}{region_text}."


def story_state(story: dict[str, Any]) -> str:
    records = story.get("source_records") or []
    for record in records:
        state = str(record.get("state_hint") or record.get("region_scope") or "").strip()
        if state:
            return state
    return ""


def balance_public_stories(curated: list[dict[str, Any]], max_public_stories: int = 7, max_per_category: int = 2) -> None:
    candidates = [story for story in curated if story.get("included_in_public_summary")]
    if len(candidates) <= max_public_stories and len({story.get("category") for story in candidates}) <= 1:
        return
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    category_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    categories = {story.get("category") for story in candidates}
    enforce_category_cap = len(categories) > 1
    ordered = sorted(candidates, key=lambda item: item.get("score") or 0, reverse=True)
    while ordered and len(selected) < min(max_public_stories, len(candidates)):
        best_index = 0
        best_rank: tuple[int, int, int] | None = None
        for index, story in enumerate(ordered):
            category = str(story.get("category") or "")
            state = story_state(story)
            over_category = enforce_category_cap and category_counts[category] >= max_per_category
            rank = (
                0 if not over_category else 1,
                state_counts[state] if state else 0,
                -(int(story.get("score") or 0)),
            )
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_index = index
        story = ordered.pop(best_index)
        category = str(story.get("category") or "")
        if enforce_category_cap and category_counts[category] >= max_per_category:
            if any(category_counts[str(other.get("category") or "")] < max_per_category for other in ordered):
                ordered.append(story)
                continue
            break
        selected.append(story)
        selected_ids.add(story["story_id"])
        category_counts[category] += 1
        state = story_state(story)
        if state:
            state_counts[state] += 1
    for story in candidates:
        if story["story_id"] not in selected_ids:
            story["included_in_public_summary"] = False
            if not story.get("excluded_reason"):
                story["excluded_reason"] = "quality_weekly_balance_limit"
            reasons = list(story.get("scoring_reasons") or [])
            reasons.append("public_balance=held_for_detail")
            story["scoring_reasons"] = reasons


def curate_sources(root: Path, edition_date: str, dry_run: bool = False) -> dict[str, Any]:
    root = root.resolve()
    in_path = root / CASCADE_DATA_ROOT / "normalized" / edition_date / "normalized_sources.json"
    out_dir = root / CASCADE_DATA_ROOT / "curated" / edition_date
    out_path = out_dir / "curation_manifest.json"
    warnings: list[str] = []
    errors: list[str] = []
    if not in_path.exists():
        errors.append(f"normalized source file not found: {in_path}")
        return {"ok": False, "curated_count": 0, "public_story_count": 0, "curation_path": str(out_path), "warnings": warnings, "errors": errors}
    records = json.loads(in_path.read_text(encoding="utf-8"))
    sources = {source["source_id"]: source for source in load_sources(root / DEFAULT_SOURCES_PATH)}
    curated = []
    for record in records:
        source_config = sources.get(record.get("source_id"), {})
        excluded_reason = exclusion_reason(record)
        reliability_tier = str(source_config.get("reliability_tier") or record.get("reliability_tier") or "unknown")
        score = score_record(record, reliability_tier)
        included_public = excluded_reason is None and score["total_score"] >= 35 and bool(record.get("canonical_url"))
        curated.append(
            {
                "story_id": story_id_for(record),
                "title": record.get("title", ""),
                "summary": deterministic_summary(record),
                "category": score["category"],
                "why_it_matters": why_it_matters(record, score["category"]),
                "score": score["total_score"],
                "regional_relevance_score": score["regional_relevance_score"],
                "systems_impact_score": score["systems_impact_score"],
                "public_consequence_score": score["public_consequence_score"],
                "recency_score": score["recency_score"],
                "source_reliability_score": score["source_reliability_score"],
                "multi_source_score": score["multi_source_score"],
                "duplicate_penalty": score["duplicate_penalty"],
                "low_signal_penalty": score["low_signal_penalty"],
                "scoring_reasons": score["scoring_reasons"],
                "source_record_ids": [record["source_record_id"]],
                "source_urls": [record["canonical_url"]] if record.get("canonical_url") else [],
                "included_in_public_summary": included_public,
                "included_in_detail_dataset": excluded_reason is None,
                "excluded_reason": excluded_reason,
                "source_records": [record],
                "traceability_note": record.get("traceability_note"),
            }
        )
    balance_public_stories(curated)
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(curated, indent=2), encoding="utf-8")
    public_story_count = sum(1 for story in curated if story["included_in_public_summary"])
    return {
        "ok": not errors,
        "curated_count": len(curated),
        "public_story_count": public_story_count,
        "curation_path": str(out_path),
        "warnings": warnings,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = curate_sources(Path.cwd(), args.date, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
