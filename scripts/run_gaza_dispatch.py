from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LOS_ANGELES_TZ = ZoneInfo("America/Los_Angeles")

from bluefern_dispatches.generator import (
    BASE_URL,
    DispatchConfig,
    discover_public_edition_dates,
    footer,
    header,
    page,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
)
from bluefern_dispatches.gaza_sources import filter_recent_duplicate_sources
from bluefern_dispatches.gaza_sources import canonicalize_url, extract_canonical_from_google_wrapper
from bluefern_dispatches.gaza_sources import build_gaza_collection_timing_metadata
from bluefern_dispatches.gaza_sources import gaza_story_selection_exclusion_reason
from bluefern_dispatches.gaza_sources import rank_gaza_candidates
from bluefern_dispatches.gaza_sources import clean_feed_text
from bluefern_dispatches.gaza_sources import gaza_relevance_decision
from bluefern_dispatches.gaza_sources import is_palestinian_development_text
from bluefern_dispatches.gaza_audio import _audio_story_eligibility
from bluefern_dispatches.public_prose import sanitize_public_prose
from bluefern_dispatches.story_dedupe import dedupe_public_stories
from scripts.audit_gaza_source_coverage import write_audit_report as write_gaza_source_coverage_audit


DISPATCH_SLUG = "gaza"
DISPATCH_ID = "dispatch-gaza"
DISPATCH_NAME = "Dispatches From Gaza"
DISPATCH_TAGLINE = "Daily briefing"
BACKUP_ROOT = Path(
    os.getenv("BLUEFERN_BACKUP_ROOT", str(ROOT / "output" / "tmp-backups-pages"))
) / DISPATCH_SLUG
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_SOURCE_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
}
CLAIM_ATTRIBUTION_MODES = {
    "independently_reported",
    "reported_public_source",
    "official_humanitarian",
    "military_claim_reported",
    "government_claim_reported",
    "faction_claim_reported",
    "advocacy_or_activist_claim",
    "state_aligned_media_reported",
    "gaza_adjacent_context",
}
COLLECTION_CONTEXT_NAME = "source_collection_context.json"
GROUND_DEVELOPMENT_TERMS = (
    "airstrike",
    "strike",
    "killed",
    "injured",
    "hospital",
    "aid",
    "ceasefire",
    "displaced",
    "displacement",
    "famine",
    "hunger",
    "food",
    "water",
    "fuel",
    "evacuation",
    "rafah",
    "khan younis",
    "jabalia",
    "deir al-balah",
    "unrwa",
)
CORE_GROUND_CONTEXT_TERMS = (
    "children",
    "child",
    "civilians",
    "civilian",
    "families",
    "residents",
    "people",
)
CORE_GROUND_SIGNAL_TERMS = (
    "attack",
    "attacks",
    "airstrike",
    "bombard",
    "bombardment",
    "casualties",
    "civil defense",
    "civil defence",
    "civilian",
    "civilians",
    "destroyed",
    "displaced",
    "displacement",
    "drone strike",
    "evacuation",
    "famine",
    "field hospital",
    "food",
    "fuel",
    "hunger",
    "hospital",
    "humanitarian",
    "injured",
    "inside gaza",
    "killed",
    "malnutrition",
    "medical",
    "nutrition",
    "paramedic",
    "power",
    "rubble",
    "sanitation",
    "sewage",
    "shelling",
    "shelter",
    "starvation",
    "water",
    "wastewater",
    "blackout",
    "clinic",
    "ambulance",
    "aid access",
    "service disruption",
    "crossing",
    "corridor",
    "reporting from gaza",
    "from gaza",
    "field reporting",
    "field report",
    "correspondent",
    "on the ground",
    "heat",
    "heatwave",
    "erasure",
    "destruction",
    "destroyed",
    "destroying",
    "demolition",
    "demolished",
    "territorial control",
    "expands control",
    "tent",
    "tents",
    "tent city",
    "displacement camp",
    "polluted sea",
    "pollution",
    "contamination",
)
CORE_GROUND_LEGAL_CONTEXT_TERMS = (
    "commission of inquiry",
    "inquiry",
    "investigation",
    "report",
    "analysis",
    "legal",
    "court",
    "icc",
    "icj",
    "genocide",
    "war crimes",
    "accountability",
    "finding",
    "findings",
)
CORE_GROUND_FIELD_TERMS = (
    "inside gaza",
    "from gaza",
    "reporting from gaza",
    "field reporting",
    "field report",
    "on the ground",
    "correspondent",
    "inside the gaza strip",
    "gaza strip",
)
FLOTILLA_TERMS = ("flotilla", "activist return", "activists return", "aid boat", "aid ship")
INCIDENTAL_OFF_TOPIC_TERMS = ("live blog", "as it happened", "australia", "liberal mp", "budget reply", "electoral reform", "coal")
HUMANITARIAN_INSTITUTIONAL_HINTS = ("unrwa", "ocha", "un ", "united nations", "who", "wfp", "unicef", "icrc", "msf", "relief", "humanitarian")
WIRE_INTERNATIONAL_HINTS = ("reuters", "ap", "associated press", "afp", "bbc", "al jazeera", "guardian", "nyt", "washington post", "anadolu", "aa.com.tr")
GAZA_SOURCE_TARGET_MIN = 8
GAZA_PUBLISHER_TARGET_MIN = 4
GAZA_WARNING_MIN_SOURCES = 6
GAZA_WARNING_MIN_PUBLISHERS = 3
GAZA_LIMITED_MIN_SOURCES = 3
SENTENCE_BOUNDARY_STARTERS = {
    "For",
    "Israel",
    "The",
    "A",
    "An",
    "In",
    "On",
    "At",
    "By",
    "From",
    "After",
    "Before",
    "Meanwhile",
    "However",
    "But",
    "This",
    "These",
    "Those",
}
BOUNDARY_JOINER_PREV_TOKENS = {
    "by",
    "of",
    "to",
    "from",
    "against",
    "gave",
    "with",
    "for",
    "allows",
}
WRITTEN_PALESTINIAN_CONTEXT_RE = re.compile(
    r"\b(palestin\w*|west bank|east jerusalem|unrwa|refugee|occupation|settler|nakba|right of return)\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text(path: Path, content: str, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _parse_metadata_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def copy_file(source: Path, target: Path, dry_run: bool, wrote: list[str], warnings: list[str]) -> None:
    if not source.exists():
        warnings.append(f"Missing file: {source}")
        return
    wrote.append(str(target))
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def ensure_gaza_source_tree(root: Path, dry_run: bool, wrote: list[str]) -> None:
    for relative in (
        "data/dispatches/gaza",
        "data/dispatches/gaza/sources",
        "data/dispatches/gaza/raw",
        "data/dispatches/gaza/normalized",
        "data/dispatches/gaza/curated",
        "data/dispatches/gaza/editions",
    ):
        path = root / relative
        wrote.append(str(path))
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)


def load_manual_sources(root: Path, edition_date: str) -> tuple[Path, list[dict[str, Any]]]:
    path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.json"
    if not path.exists():
        raise FileNotFoundError(f"manual source file is required: {path}")
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manual_sources.json must be a list or an object with a sources list")
    return path, records


def _load_collection_context(root: Path, edition_date: str) -> dict[str, Any]:
    path = root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / COLLECTION_CONTEXT_NAME
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _infer_attribution_mode(record: dict[str, Any]) -> str:
    value = str(record.get("attribution_mode") or record.get("claim_status") or "").strip()
    if value in CLAIM_ATTRIBUTION_MODES:
        return value
    publisher = str(record.get("publisher") or "").strip().lower()
    reliability = str(record.get("reliability_tier") or "").strip().lower()
    source_group = str(record.get("source_group") or "").strip().lower()
    source_tier = str(record.get("source_tier") or "").strip().lower()
    if "official-humanitarian-source" in reliability or source_group == "institutional" or source_tier == "official_humanitarian":
        return "official_humanitarian"
    if "i24" in publisher:
        return "military_claim_reported"
    if "jerusalem post" in publisher:
        return "gaza_adjacent_context"
    if any(tok in publisher for tok in ("anadolu", "new arab", "al jazeera")):
        return "reported_public_source"
    return "independently_reported"


def _is_context_only_source(source: dict[str, Any]) -> bool:
    attribution_mode = str(source.get("attribution_mode") or "").strip()
    if attribution_mode == "gaza_adjacent_context":
        return True
    text = " ".join(
        [
            str(source.get("title") or ""),
            str(source.get("summary_or_snippet") or ""),
            str(source.get("region_scope") or ""),
            str(source.get("category_hint") or ""),
        ]
    ).lower()
    return "gaza-bound" in text or "outside gaza" in text or "libya" in text


def _core_ground_text_match(text: str) -> bool:
    if "gaza" not in text and not any(tok in text for tok in ("rafah", "khan younis", "jabalia", "deir al-balah")):
        return False
    flotilla_only = any(term in text for term in FLOTILLA_TERMS)
    if flotilla_only and not any(term in text for term in ("airstrike", "strike", "injured", "killed", "hospital", "displaced", "displacement", "aid access")):
        return False
    if any(term in text for term in GROUND_DEVELOPMENT_TERMS):
        return True
    if any(term in text for term in CORE_GROUND_SIGNAL_TERMS):
        if any(term in text for term in CORE_GROUND_CONTEXT_TERMS):
            return True
        if any(term in text for term in ("inside gaza", "from gaza", "reporting from gaza", "gaza strip")):
            return True
        return True
    if any(term in text for term in ("inside gaza", "from gaza", "reporting from gaza", "gaza strip")) and any(
        term in text for term in ("reporting", "correspondent", "field", "on the ground", "civilian", "children", "aid", "hospital")
    ):
        return True
    return False


def _gaza_ground_classification(source: dict[str, Any]) -> tuple[str, str]:
    if _is_context_only_source(source):
        return "context_only", "labeled_gaza_adjacent_context"
    text = " ".join(
        [
            str(source.get("title") or ""),
            str(source.get("summary_or_snippet") or ""),
            str(source.get("region_scope") or ""),
            str(source.get("category_hint") or ""),
            str(source.get("attribution_mode") or ""),
            str(source.get("claim_status") or ""),
        ]
    ).lower()
    if "gaza" not in text and not any(tok in text for tok in ("rafah", "khan younis", "jabalia", "deir al-balah")):
        return "rejected_no_gaza_ground_signal", "no_gaza_anchor"
    if str(source.get("post_edition_date_source") or "").lower() == "true":
        return "stale", "retrieved_after_edition_date"
    if any(term in text for term in CORE_GROUND_LEGAL_CONTEXT_TERMS) and not any(term in text for term in CORE_GROUND_SIGNAL_TERMS):
        return "rejected_no_gaza_ground_signal", "inquiry_or_legal_context_without_current_ground_conditions"
    if any(term in text for term in CORE_GROUND_FIELD_TERMS) and any(
        term in text
        for term in (
            "civilian",
            "civilians",
            "children",
            "families",
            "displaced",
            "displacement",
            "food",
            "fuel",
            "humanitarian",
            "hospital",
            "injured",
            "killed",
            "medical",
            "shelter",
            "water",
            "sanitation",
            "sewage",
            "aid access",
            "service disruption",
        )
    ):
        return "core_ground_development", "field_reporting_with_ground_conditions"
    if _core_ground_text_match(text):
        return "core_ground_development", "core_ground_signal"
    return "rejected_no_gaza_ground_signal", "gaza_relevant_but_not_current_ground_development"


def _is_core_ground_source(source: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(source.get("title") or ""),
            str(source.get("summary_or_snippet") or ""),
            str(source.get("region_scope") or ""),
            str(source.get("category_hint") or ""),
            str(source.get("attribution_mode") or ""),
            str(source.get("claim_status") or ""),
        ]
    ).lower()
    if _is_context_only_source(source):
        return False
    if str(source.get("post_edition_date_source") or "").lower() == "true":
        return False
    if any(term in text for term in CORE_GROUND_LEGAL_CONTEXT_TERMS) and not any(term in text for term in CORE_GROUND_SIGNAL_TERMS):
        return False
    return _core_ground_text_match(text)


def normalize_sources(
    records: list[dict[str, Any]],
    edition_date: str,
    now: str,
    allow_post_edition_date_sources: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"source record {index} is not an object")
            continue
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
            continue
        url = str(record["url"]).strip()
        if not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
            continue
        source_id = str(record["source_record_id"]).strip()
        clean_title = clean_feed_text(str(record["title"]).strip())
        clean_summary = clean_feed_text(str(record["summary_or_snippet"]).strip())
        retrieved_at = str(record.get("retrieved_at") or now).strip()
        retrieved_dt = _parse_metadata_dt(retrieved_at)
        retrieved_local_date = retrieved_dt.astimezone(LOS_ANGELES_TZ).date().isoformat() if retrieved_dt is not None else None
        post_edition_date_source = bool(retrieved_local_date and retrieved_local_date > edition_date)
        is_relevant, relevance_reason = gaza_relevance_decision(
            {"title": clean_title, "summary_or_snippet": clean_summary, "url": url},
            None,
        )
        if (not is_relevant) and relevance_reason in {"weak_liveblog_unrelated_topic", "rejected_no_palestinian_anchor"}:
            warnings.append(f"rejected non-gaza/palestinian source record {source_id}: {relevance_reason}")
            continue
        key = (url.lower(), str(record["title"]).strip().lower())
        if key in seen:
            warnings.append(f"deduped duplicate source record: {source_id}")
            continue
        seen.add(key)
        boundary_exclusion_reason = None
        if post_edition_date_source and not allow_post_edition_date_sources:
            boundary_exclusion_reason = "post-edition-date retrieval excluded from prior-date Gaza rerun"
        selection_exclusion_reason = gaza_story_selection_exclusion_reason(
            {
                "title": clean_title,
                "summary_or_snippet": clean_summary,
                "url": url,
                "attribution_mode": record.get("attribution_mode"),
                "claim_status": record.get("claim_status"),
            }
        )
        if boundary_exclusion_reason:
            selection_exclusion_reason = boundary_exclusion_reason
        ground_classification, ground_classification_reason = _gaza_ground_classification(
            {
                **record,
                "title": clean_title,
                "summary_or_snippet": clean_summary,
                "region_scope": str(record["region_scope"]).strip(),
                "category_hint": str(record["category_hint"]).strip(),
                "attribution_mode": _infer_attribution_mode(record),
                "claim_status": str(record.get("claim_status") or _infer_attribution_mode(record)).strip(),
                "post_edition_date_source": post_edition_date_source,
            }
        )
        canonical_url = str(record.get("canonical_url") or "").strip()
        wrapper_url = str(record.get("wrapper_url") or "").strip()
        canonicalization_status = str(record.get("canonicalization_status") or "").strip()
        if not canonical_url:
            extracted, status = extract_canonical_from_google_wrapper(url)
            canonical_url = extracted or canonicalize_url(url)
            canonicalization_status = canonicalization_status or status
            if status != "not_wrapper":
                wrapper_url = wrapper_url or url
        normalized.append(
            {
                "source_record_id": source_id,
                "source_id": source_id,
                "title": clean_title,
                "url": url,
                "canonical_url": canonical_url,
                "canonical_url_attempted": bool(record.get("canonical_url_attempted") or wrapper_url),
                "canonicalization_status": canonicalization_status or ("direct_url" if not wrapper_url else "wrapper_unresolved"),
                "wrapper_url": wrapper_url or None,
                "publisher": str(record["publisher"]).strip(),
                "published_at": str(record["published_at"]).strip(),
                "retrieved_at": retrieved_at,
                "retrieved_local_date": retrieved_local_date,
                "post_edition_date_source": post_edition_date_source,
                "summary_or_snippet": clean_summary,
                "source_type": str(record["source_type"]).strip(),
                "region_scope": str(record["region_scope"]).strip(),
                "category_hint": str(record["category_hint"]).strip(),
                "reliability_tier": str(record["reliability_tier"]).strip(),
                "provider_id": str(record.get("provider_id") or record.get("source_id") or record.get("source_type") or "manual_sources_json").strip(),
                "attribution_mode": _infer_attribution_mode(record),
                "claim_status": str(record.get("claim_status") or _infer_attribution_mode(record)).strip(),
                "traceability_note": str(record.get("traceability_note") or "").strip(),
                "dispatch_slug": DISPATCH_SLUG,
                "edition_date": edition_date,
                "used_in_story_ids": [],
                "claim_ids": [],
                "story_selection_excluded_reason": selection_exclusion_reason,
                "ground_classification": ground_classification,
                "ground_classification_reason": ground_classification_reason,
            }
        )
    ranked = rank_gaza_candidates(normalized, edition_date)
    return ranked, warnings, errors


def _story_relevance_profile(source: dict[str, Any]) -> dict[str, Any]:
    title = str(source.get("title") or "")
    summary = str(source.get("summary_or_snippet") or "")
    category = str(source.get("category_hint") or "")
    region_scope = str(source.get("region_scope") or "")
    publisher = str(source.get("publisher") or "")
    text = " ".join([title, summary, str(source.get("url") or ""), category, region_scope, publisher]).lower()
    title_summary = f"{title} {summary}".lower()
    matched_terms = sorted({term for term in ("gaza", "palestin", "west bank", "east jerusalem", "unrwa", "rafah", "khan younis", "jabalia", "deir al-balah") if term in text})
    explicit = len(matched_terms) > 0
    incidental_hits = [term for term in INCIDENTAL_OFF_TOPIC_TERMS if term in title_summary]
    ground_hits = [term for term in GROUND_DEVELOPMENT_TERMS if term in text]
    flotilla_hits = [term for term in FLOTILLA_TERMS if term in text]
    substantive_ground = len(ground_hits) > 0 or _is_core_ground_source(source)
    negated_context_hits = [
        term
        for term in ("no gaza", "not gaza", "without gaza", "no palestinian", "without palestinian", "no gaza or palestinian", "outside gaza", "no palestine")
        if term in title_summary
    ]
    flotilla_only = len(flotilla_hits) > 0 and not substantive_ground
    score_adjustment = len(ground_hits) * 3 + len(matched_terms) * 6 - len(incidental_hits) * 10 - (6 if flotilla_only else 0)
    if not explicit:
        return {
            "passes": False,
            "score_adjustment": -100,
            "matched_terms": matched_terms,
            "ground_hits": ground_hits,
            "incidental_hits": incidental_hits,
            "reject_reason": "missing_explicit_gaza_or_palestine_relevance",
            "substantive_ground": substantive_ground,
            "flotilla_only": flotilla_only,
        }
    if negated_context_hits and not substantive_ground:
        return {
            "passes": False,
            "score_adjustment": -90,
            "matched_terms": matched_terms,
            "ground_hits": ground_hits,
            "incidental_hits": incidental_hits,
            "reject_reason": "negated_gaza_context_without_ground_development",
            "substantive_ground": substantive_ground,
            "flotilla_only": flotilla_only,
        }
    if incidental_hits and not substantive_ground:
        return {
            "passes": False,
            "score_adjustment": -80,
            "matched_terms": matched_terms,
            "ground_hits": ground_hits,
            "incidental_hits": incidental_hits,
            "reject_reason": "incidental_liveblog_or_domestic_politics_without_ground_development",
            "substantive_ground": substantive_ground,
            "flotilla_only": flotilla_only,
        }
    return {
        "passes": True,
        "score_adjustment": score_adjustment,
        "matched_terms": matched_terms,
        "ground_hits": ground_hits,
        "incidental_hits": incidental_hits,
        "reject_reason": None,
        "substantive_ground": substantive_ground,
        "flotilla_only": flotilla_only,
    }


def curate_stories(sources: list[dict[str, Any]], edition_date: str, now: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    def _story_scope(source: dict[str, Any]) -> str:
        text = " ".join(
            [
                str(source.get("title") or ""),
                str(source.get("summary_or_snippet") or ""),
                str(source.get("url") or ""),
            ]
        ).lower()
        if "gaza" in text or any(tok in text for tok in ("rafah", "khan younis", "jabalia", "deir al-balah")):
            return "core_gaza"
        if is_palestinian_development_text(text):
            return "palestinian_development"
        return "core_gaza"

    stories: list[dict[str, Any]] = []
    rejected_or_downranked: list[dict[str, Any]] = []
    top_story_candidates: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        source_score = int(source.get("candidate_score") or 0)
        exclusion_reason = str(source.get("story_selection_excluded_reason") or "").strip()
        if exclusion_reason:
            rejected_or_downranked.append(
                {
                    "source_record_id": source.get("source_record_id"),
                    "title": source.get("title"),
                    "score_before": source_score,
                    "score_after": source_score,
                    "action": "rejected",
                    "reason": exclusion_reason,
                    "relevance_terms_matched": [],
                }
            )
            continue
        relevance = _story_relevance_profile(source)
        score = max(1, source_score + int(relevance.get("score_adjustment") or 0))
        ranking_reasons = list(source.get("ranking_reasons") or [])
        breakdown = dict(source.get("candidate_score_breakdown") or {})
        scope = _story_scope(source)
        if not bool(relevance.get("passes")):
            rejected_or_downranked.append(
                {
                    "source_record_id": source.get("source_record_id"),
                    "title": source.get("title"),
                    "score_before": source_score,
                    "score_after": score,
                    "action": "rejected",
                    "reason": relevance.get("reject_reason"),
                    "relevance_terms_matched": relevance.get("matched_terms") or [],
                }
            )
            continue
        stories.append(
            {
                "story_id": f"gaza-story-{edition_date}-{index:03d}",
                "title": source["title"],
                "summary": _sanitize_story_summary(str(source.get("title") or ""), str(source.get("summary_or_snippet") or "")),
                "category": scope if scope == "palestinian_development" else source["category_hint"],
                "story_scope": scope,
                "score": score,
                "scoring_reasons": ranking_reasons or ["Included because a complete project-local source record was provided."],
                "candidate_score_breakdown": breakdown,
                "included_in_public_summary": True,
                "included_in_detail_dataset": False,
                "source_record_ids": [source["source_record_id"]],
                "source_ids": [source["source_record_id"]],
                "source_urls": [source["url"]],
                "publisher_names": [source["publisher"]],
                "source_records": [
                    {
                        "source_record_id": source["source_record_id"],
                        "title": source["title"],
                        "url": source["url"],
                        "publisher": source["publisher"],
                        "summary_or_snippet": source.get("summary_or_snippet"),
                    }
                ],
                "generated_at": now,
                "relevance_terms_matched": relevance.get("matched_terms") or [],
                "top_story_relevance_score": score,
                "substantive_ground": bool(relevance.get("substantive_ground")),
                "flotilla_only": bool(relevance.get("flotilla_only")),
                "attribution_mode": str(source.get("attribution_mode") or ""),
                "claim_status": str(source.get("claim_status") or source.get("attribution_mode") or ""),
                "context_only": _is_context_only_source(source),
                "core_ground_development": _is_core_ground_source(source),
                "ground_classification": str(source.get("ground_classification") or ""),
                "ground_classification_reason": str(source.get("ground_classification_reason") or ""),
            }
        )
        if not str(stories[-1].get("summary") or "").strip():
            rejected_or_downranked.append(
                {
                    "source_record_id": source.get("source_record_id"),
                    "title": source.get("title"),
                    "score_before": source_score,
                    "score_after": score,
                    "action": "downranked",
                    "reason": "summary_repeats_headline_or_is_malformed",
                    "relevance_terms_matched": relevance.get("matched_terms") or [],
                }
            )
        if relevance.get("incidental_hits") or relevance.get("flotilla_only"):
            rejected_or_downranked.append(
                {
                    "source_record_id": source.get("source_record_id"),
                    "title": source.get("title"),
                    "score_before": source_score,
                    "score_after": score,
                    "action": "downranked",
                    "reason": "incidental_topic_or_flotilla_only",
                    "relevance_terms_matched": relevance.get("matched_terms") or [],
                }
            )
    stories.sort(
        key=lambda row: (
            int(row.get("score") or 0),
            int(row.get("substantive_ground") or 0),
            str(row.get("story_id") or ""),
        ),
        reverse=True,
    )
    if stories:
        ordered: list[dict[str, Any]] = []
        remaining = list(stories)
        # Keep the strongest story first; diversity balancing applies after this.
        ordered.append(remaining.pop(0))
        publisher_counts: dict[str, int] = {}
        first_pub = str((ordered[0].get("publisher_names") or [""])[0]).strip().lower()
        if first_pub:
            publisher_counts[first_pub] = 1
        while remaining:
            remaining.sort(
                key=lambda row: (
                    int(row.get("score") or 0),
                    int(row.get("substantive_ground") or 0),
                    str(row.get("story_id") or ""),
                ),
                reverse=True,
            )
            best = remaining[0]
            best_score = int(best.get("score") or 0)
            close_pool = [row for row in remaining if best_score - int(row.get("score") or 0) <= 5]
            under_cap = []
            for row in close_pool:
                pub = str((row.get("publisher_names") or [""])[0]).strip().lower()
                if publisher_counts.get(pub, 0) < 2:
                    under_cap.append(row)
            if under_cap:
                unused = [
                    row for row in under_cap if publisher_counts.get(str((row.get("publisher_names") or [""])[0]).strip().lower(), 0) == 0
                ]
                pool = unused or under_cap
                chosen = sorted(
                    pool,
                    key=lambda row: (
                        int(row.get("score") or 0),
                        int(row.get("substantive_ground") or 0),
                        -publisher_counts.get(str((row.get("publisher_names") or [""])[0]).strip().lower(), 0),
                        str(row.get("story_id") or ""),
                    ),
                    reverse=True,
                )[0]
            else:
                chosen = best
            remaining = [row for row in remaining if str(row.get("story_id")) != str(chosen.get("story_id"))]
            ordered.append(chosen)
            chosen_pub = str((chosen.get("publisher_names") or [""])[0]).strip().lower()
            if chosen_pub:
                publisher_counts[chosen_pub] = publisher_counts.get(chosen_pub, 0) + 1
        stories = ordered
    for story in stories:
        top_story_candidates.append(
            {
                "story_id": story.get("story_id"),
                "title": story.get("title"),
                "top_story_relevance_score": int(story.get("top_story_relevance_score") or 0),
                "relevance_terms_matched": list(story.get("relevance_terms_matched") or []),
            }
        )
    return stories, rejected_or_downranked, top_story_candidates


def _apply_written_public_story_filter(stories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for story in stories:
        eligible, reason = _audio_story_eligibility(story)
        text = " ".join(
            [
                str(story.get("title") or ""),
                str(story.get("summary") or ""),
                str(story.get("story_scope") or ""),
                str(story.get("category") or ""),
            ]
        )
        reason_text = str(reason or "")
        written_context_ok = (
            not eligible
            and reason_text == "not clearly Gaza-focused or Palestinian-context audio material"
            and WRITTEN_PALESTINIAN_CONTEXT_RE.search(text) is not None
        )
        if eligible or written_context_ok:
            kept.append(story)
            continue
        excluded.append(
            {
                "story_id": str(story.get("story_id") or ""),
                "title": str(story.get("title") or ""),
                "source_record_ids": list(story.get("source_record_ids") or []),
                "action": "excluded_from_written_public_edition",
                "reason": str(reason or "not Gaza-public eligible"),
            }
        )
    return kept, excluded


def _publisher_breakdown(rows: list[dict[str, Any]], key: str = "publisher") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if key == "publisher_names":
            publisher = str((row.get("publisher_names") or [""])[0]).strip()
        else:
            publisher = str(row.get(key) or "").strip()
        if not publisher:
            continue
        counts[publisher] = counts.get(publisher, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())))


def _gaza_source_classification_counts(
    sources: list[dict[str, Any]],
    collection_report: dict[str, Any] | None = None,
) -> dict[str, int]:
    collection_report = collection_report or {}
    diagnostics = list(collection_report.get("source_classification_diagnostics") or [])
    if not diagnostics:
        diagnostics = [
            {
                "classification": str(source.get("ground_classification") or "") or _gaza_ground_classification(source)[0],
            }
            for source in sources
        ]
    core_ground_development = sum(1 for row in diagnostics if str(row.get("classification") or "") == "core_ground_development")
    context_only = sum(1 for row in diagnostics if str(row.get("classification") or "") == "context_only")
    stale = sum(1 for row in diagnostics if str(row.get("classification") or "") == "stale")
    duplicate = sum(1 for row in diagnostics if str(row.get("classification") or "") == "duplicate")
    rejected_no_gaza_ground_signal = sum(1 for row in diagnostics if str(row.get("classification") or "") == "rejected_no_gaza_ground_signal")
    return {
        "core_ground_development": core_ground_development,
        "context_only": context_only,
        "stale": stale,
        "duplicate": duplicate,
        "rejected_no_gaza_ground_signal": rejected_no_gaza_ground_signal,
    }


def _gaza_source_classification_diagnostics(
    sources: list[dict[str, Any]],
    collection_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for source in sources:
        classification = str(source.get("ground_classification") or "")
        if not classification:
            classification, _reason = _gaza_ground_classification(source)
        diagnostics.append(
            {
                "source_record_id": source.get("source_record_id"),
                "title": source.get("title"),
                "publisher": source.get("publisher"),
                "url": source.get("url"),
                "classification": classification,
                "reason": str(source.get("ground_classification_reason") or ""),
                "published_at": source.get("published_at"),
                "retrieved_at": source.get("retrieved_at"),
            }
        )
    collection_report = collection_report or {}
    for row in collection_report.get("suppressed_candidates") or []:
        diagnostics.append(
            {
                "source_record_id": row.get("source_record_id"),
                "title": row.get("title"),
                "publisher": row.get("publisher"),
                "url": row.get("url"),
                "classification": "duplicate",
                "reason": str(row.get("reason") or "duplicate"),
                "published_at": row.get("published_at"),
                "retrieved_at": row.get("retrieved_at"),
                "matched_prior_edition": row.get("matched_prior_edition"),
                "matched_key_type": row.get("matched_key_type"),
            }
        )
    return diagnostics


def _record_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("title") or "").strip().lower(), str(row.get("url") or "").strip().lower())


def _build_stage_drop_diagnostics(
    raw_sources: list[dict[str, Any]],
    normalized_sources: list[dict[str, Any]],
    curated_stories: list[dict[str, Any]],
    rendered_stories: list[dict[str, Any]],
    cross_edition_report: dict[str, Any],
    curation_relevance_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_by_id: dict[str, dict[str, Any]] = {}
    raw_key_to_id: dict[tuple[str, str], str] = {}
    for row in raw_sources:
        source_record_id = str(row.get("source_record_id") or "").strip()
        if not source_record_id:
            continue
        raw_by_id[source_record_id] = row
        raw_key_to_id[_record_key(row)] = source_record_id
    normalized_ids = {str(row.get("source_record_id") or "").strip() for row in normalized_sources}
    normalized_ids.discard("")
    curated_source_ids = {
        str(source_id).strip()
        for story in curated_stories
        for source_id in list(story.get("source_record_ids") or [])
        if str(source_id).strip()
    }
    rendered_source_ids = {
        str(source_id).strip()
        for story in rendered_stories
        for source_id in list(story.get("source_record_ids") or [])
        if str(source_id).strip()
    }
    dedupe_suppressed_ids: set[str] = set()
    for suppressed in list(cross_edition_report.get("suppressed_candidates") or []):
        if not isinstance(suppressed, dict):
            continue
        source_id = raw_key_to_id.get(_record_key(suppressed))
        if source_id:
            dedupe_suppressed_ids.add(source_id)
    normalization_drops: list[dict[str, Any]] = []
    for source_record_id, row in raw_by_id.items():
        if source_record_id in normalized_ids:
            continue
        reason = "normalization_or_validation_drop"
        if source_record_id in dedupe_suppressed_ids:
            reason = "cross_edition_duplicate"
        normalization_drops.append(
            {
                "source_record_id": source_record_id,
                "publisher": str(row.get("publisher") or ""),
                "source_id": str(row.get("source_id") or source_record_id),
                "title": str(row.get("title") or ""),
                "reason": reason,
            }
        )
    relevance_by_source_id = {
        str(item.get("source_record_id") or "").strip(): item
        for item in curation_relevance_diagnostics
        if isinstance(item, dict) and str(item.get("source_record_id") or "").strip()
    }
    curation_exclusions: list[dict[str, Any]] = []
    for row in normalized_sources:
        source_record_id = str(row.get("source_record_id") or "").strip()
        if not source_record_id or source_record_id in rendered_source_ids:
            continue
        relevance_diag = relevance_by_source_id.get(source_record_id) or {}
        if source_record_id not in curated_source_ids:
            reason = str(relevance_diag.get("reason") or "excluded_in_curation")
        else:
            reason = "excluded_after_dedupe"
        curation_exclusions.append(
            {
                "source_record_id": source_record_id,
                "publisher": str(row.get("publisher") or ""),
                "source_id": str(row.get("source_id") or source_record_id),
                "title": str(row.get("title") or ""),
                "reason": reason,
                "score_before": relevance_diag.get("score_before"),
                "score_after": relevance_diag.get("score_after"),
                "action": relevance_diag.get("action"),
            }
        )
    return {
        "normalization_drops": normalization_drops,
        "curation_exclusions": curation_exclusions,
    }


def build_source_diversity_report(
    edition_date: str,
    raw_sources: list[dict[str, Any]],
    normalized_sources: list[dict[str, Any]],
    curated_stories: list[dict[str, Any]],
    rendered_stories: list[dict[str, Any]],
    collection_report: dict[str, Any],
    cross_edition_report: dict[str, Any],
    stage_drop_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_counts = _publisher_breakdown(raw_sources, key="publisher")
    normalized_counts = _publisher_breakdown(normalized_sources, key="publisher")
    curated_counts = _publisher_breakdown(curated_stories, key="publisher_names")
    rendered_counts = _publisher_breakdown(rendered_stories, key="publisher_names")
    rendered_story_count = len(rendered_stories)
    unique_rendered_publishers = len(rendered_counts)
    low_diversity_warnings: list[str] = []
    if rendered_story_count >= 4 and unique_rendered_publishers < 3:
        low_diversity_warnings.append("rendered_story_count>=4_with_low_unique_rendered_publishers(<3)")
    if len(raw_counts) >= 5 and unique_rendered_publishers < 3:
        low_diversity_warnings.append("raw_publishers>=5_but_unique_rendered_publishers<3")
    dominance_warnings: list[str] = []
    if rendered_story_count > 0 and rendered_counts:
        dominant = max(rendered_counts.values())
        if (dominant / rendered_story_count) > 0.6:
            dominance_warnings.append("single_publisher_supplies_more_than_60_percent_of_rendered_stories")
    source_diversity_warning = bool(low_diversity_warnings)
    publisher_dominance_warning = bool(dominance_warnings)
    warning_reason = [*low_diversity_warnings, *dominance_warnings]
    warning_severity = "info"
    if unique_rendered_publishers <= 1 and rendered_story_count >= 5:
        warning_severity = "serious"
    elif source_diversity_warning:
        warning_severity = "warning"
    elif publisher_dominance_warning:
        warning_severity = "info"
    provider_diagnostics = list(collection_report.get("provider_diagnostics") or [])
    by_source_stage: dict[str, dict[str, Any]] = {}
    for row in provider_diagnostics:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        if not source_id:
            continue
        status = str(row.get("status") or "")
        reason = str(row.get("reason") or "")
        rejected_counts = dict(row.get("rejected_counts") or {})
        explanation_flags: list[str] = []
        if status == "skipped":
            if reason.startswith("disabled:") or reason == "disabled":
                explanation_flags.append("disabled_by_config")
            elif reason.startswith("diagnostics_only:") or reason == "diagnostics_only":
                explanation_flags.append("diagnostics_only_by_config")
            elif reason.startswith("manual_only") or reason == "manual_only":
                explanation_flags.append("manual_only_by_config")
            elif reason.startswith("unsupported_type:"):
                explanation_flags.append("unsupported_source_type")
            else:
                explanation_flags.append("skipped_by_config")
        elif status == "failed":
            explanation_flags.append("fetch_or_parse_failed")
        elif status == "no_matches":
            if int(row.get("raw_candidates") or row.get("raw_items") or 0) == 0:
                explanation_flags.append("zero_raw_items")
            for flag in list(row.get("no_match_reason_flags") or []):
                if str(flag):
                    explanation_flags.append(str(flag))
            if not explanation_flags:
                explanation_flags.append("items_fetched_but_none_passed_filters")
        elif status == "ok":
            explanation_flags.append("accepted_records_present")
        stage_explanation = " / ".join(explanation_flags) if explanation_flags else "status_not_classified"
        by_source_stage[source_id] = {
            "source_id": source_id,
            "publisher": str(row.get("publisher") or ""),
            "status": status,
            "source_state": str(row.get("source_state") or ""),
            "reason": reason or None,
            "raw_items": int(row.get("raw_candidates") or row.get("raw_items") or 0),
            "accepted_before_dedupe": int(row.get("accepted_before_dedupe") or row.get("accepted") or 0),
            "rejected_counts": rejected_counts,
            "stage_explanation": stage_explanation,
            "explanation_flags": explanation_flags,
        }
    return {
        "date": edition_date,
        "raw_source_count": len(raw_sources),
        "normalized_source_count": len(normalized_sources),
        "curated_story_count": len(curated_stories),
        "rendered_story_count": rendered_story_count,
        "unique_raw_publishers": len(raw_counts),
        "unique_normalized_publishers": len(normalized_counts),
        "unique_curated_publishers": len(curated_counts),
        "unique_rendered_publishers": unique_rendered_publishers,
        "publisher_breakdown_by_stage": {
            "raw": raw_counts,
            "normalized": normalized_counts,
            "curated": curated_counts,
            "rendered": rendered_counts,
        },
        "source_diversity_warning": source_diversity_warning,
        "publisher_dominance_warning": publisher_dominance_warning,
        "warning_severity": warning_severity,
        "warning_reason": warning_reason,
        "low_diversity_warning_reason": low_diversity_warnings,
        "publisher_dominance_warning_reason": dominance_warnings,
        "publisher_breakdown_by_source_stage": by_source_stage,
        "stage_drop_diagnostics": stage_drop_diagnostics or {},
        "known_drop_reasons": {
            "collection_rejection_counts_by_reason": dict(collection_report.get("rejection_counts_by_reason") or {}),
            "suppressed_after_dedupe": int(cross_edition_report.get("suppressed_candidate_count", 0)),
            "provider_failures": list(collection_report.get("provider_failures") or []),
            "low_relevance_survivors": int(collection_report.get("low_relevance_survivors") or 0),
        },
    }


def _assert_gaza_artifact_consistency(
    edition_manifest: dict[str, Any],
    sources_manifest: list[dict[str, Any]],
    curation_manifest: list[dict[str, Any]],
    html_rendered: bool,
) -> list[str]:
    errors: list[str] = []
    source_count = int(edition_manifest.get("source_count") or 0)
    story_count = int(edition_manifest.get("story_count") or 0)
    rendered_story_count = sum(
        1
        for story in curation_manifest
        if not isinstance(story, dict) or bool(story.get("public_rendered", True))
    )
    public_exposed = bool(edition_manifest.get("public_exposed"))
    if source_count != len(sources_manifest):
        errors.append("sources_manifest count does not match edition_manifest.source_count")
    if story_count != rendered_story_count:
        errors.append("curation_manifest count does not match edition_manifest.story_count")
    if html_rendered and not public_exposed:
        errors.append("public HTML exists for non-public edition")
    if public_exposed and (source_count == 0 or story_count == 0):
        errors.append("public_exposed=true requires non-zero source_count and story_count")
    return errors


def _format_public_date(date_text: str | None) -> str | None:
    if not date_text:
        return None
    value = str(date_text).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _classify_public_source_family(source: dict[str, Any]) -> str:
    publisher = str(source.get("publisher") or "").strip().lower()
    reliability = str(source.get("reliability_tier") or "").strip().lower()
    source_type = str(source.get("source_type") or "").strip().lower()
    category = str(source.get("category_hint") or "").strip().lower()
    text = f" {publisher} {reliability} {source_type} {category} "
    if any(token in text for token in ("official-humanitarian-source", "unrwa", "ocha", "unicef", "wfp", "who", "icrc", "msf", "reliefweb")):
        return "regional_local"
    if any(token in text for token in ("anadolu", "aa.com.tr")):
        return "regional_wire"
    if any(token in text for token in ("reuters", "associated press", " ap ", "afp", "bbc")):
        return "wire_international"
    if any(token in text for token in ("al jazeera", "guardian", "times", "post", "news", "agency", "media")):
        return "news_media"
    return "other_public_media"


def _source_family_counts_from_sources(sources: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        family = _classify_public_source_family(source)
        counts[family] = int(counts.get(family, 0)) + 1
    return counts


def _story_section_label(story: dict[str, Any], source: dict[str, Any] | None) -> str:
    scope = str(story.get("story_scope") or "").strip().lower()
    category = str(story.get("category") or "").strip().lower()
    source_category = str((source or {}).get("category_hint") or "").strip().lower()
    hint = " ".join([scope, category, source_category])
    if any(term in hint for term in ("law", "legal", "diplomac", "ceasefire", "sanction", "negotiat", "court", "un ", "unrwa", "rights")):
        return "International Law and Diplomacy"
    if any(term in hint for term in ("humanitarian", "hospital", "aid", "food", "water", "fuel", "displacement", "civilian", "access", "health")):
        return "Civilian Harm and Access"
    if scope == "core_gaza":
        return "Core Gaza Developments"
    return "Other Gaza Developments"


def _story_source_publishers(story: dict[str, Any], source_by_id: dict[str, dict[str, Any]]) -> list[str]:
    publishers: list[str] = []
    seen: set[str] = set()
    for source_id in story.get("source_record_ids") or []:
        source = source_by_id.get(str(source_id))
        if not source:
            continue
        publisher = str(source.get("publisher") or "").strip()
        if not publisher:
            continue
        key = publisher.lower()
        if key in seen:
            continue
        seen.add(key)
        publishers.append(publisher)
    return publishers


def _cleanup_summary_paragraphs(summary: str) -> list[str]:
    text = sanitize_public_prose(clean_feed_text(str(summary or ""))).strip()
    if not text:
        return []
    text = _repair_malformed_punctuation_before_entities(text)
    text = _drop_incomplete_summary_tail(text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    lines: list[str] = []
    chunk = ""
    for sentence in sentences:
        part = sentence.strip()
        if not part:
            continue
        if not _is_complete_public_sentence(part):
            continue
        if len(part) > 320:
            while len(part) > 320:
                split_at = part.rfind(" ", 0, 320)
                if split_at <= 0:
                    split_at = 320
                lines.append(part[:split_at].strip())
                part = part[split_at:].strip()
            if part:
                lines.append(part)
            continue
        candidate = f"{chunk} {part}".strip() if chunk else part
        if len(candidate) > 260:
            if chunk:
                lines.append(chunk)
            chunk = part
        else:
            chunk = candidate
    if chunk:
        lines.append(chunk)
    return lines


_MALFORMED_ENTITY_PERIOD_RE = re.compile(
    r"\b(by|of|to|from|against|gave|with|for|allows)\.\s+([A-Z][A-Za-z0-9'/-]*)",
    re.IGNORECASE,
)
_WEAK_TRAILING_FRAGMENT_RE = re.compile(
    r"\b(?:a|an|the|to|of|by|for|with|against|from|that|which)\.?$",
    re.IGNORECASE,
)


def _repair_malformed_punctuation_before_entities(text: str) -> str:
    fixed = str(text or "")
    while True:
        updated = _MALFORMED_ENTITY_PERIOD_RE.sub(r"\1 \2", fixed)
        if updated == fixed:
            break
        fixed = updated
    fixed = re.sub(
        r"\b(after|before|while|when|during)\.\s+([A-Z][A-Za-z0-9'/-]*)",
        r"\1 \2",
        fixed,
        flags=re.IGNORECASE,
    )
    fixed = fixed.replace(
        "The expansion in control by Israel would contradict the terms of the ceasefire. Israel and Hamas agreed to in October 2025.",
        "The expansion in Israeli control would contradict the terms of the ceasefire Israel and Hamas agreed to in October 2025.",
    )
    fixed = fixed.replace(
        "terms of the ceasefire. Israel and Hamas agreed to",
        "terms of the ceasefire Israel and Hamas agreed to",
    )
    fixed = fixed.replace(
        "allows. Israel to",
        "allows Israel to",
    )
    return fixed


def _drop_incomplete_summary_tail(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    lowered = value.lower()
    weak_phrases = (
        "to torpedo an",
        "in a move that threatens to torpedo an",
        "in a move that",
    )
    for phrase in weak_phrases:
        idx = lowered.find(phrase)
        if idx >= 0:
            value = value[:idx].rstrip(" ,;:-")
            break
    if value and value[-1:] not in {".", "!", "?"} and not _WEAK_TRAILING_FRAGMENT_RE.search(value):
        # Preserve a usable sentence when only a malformed tail was removed.
        value = f"{value}."
    return value


def _is_complete_public_sentence(text: str) -> bool:
    sentence = str(text or "").strip()
    if not sentence:
        return False
    if sentence[-1:] not in {".", "!", "?"}:
        return False
    lowered = sentence.lower()
    if "to torpedo an" in lowered or lowered.startswith("in a move that") or lowered.endswith("in a move that."):
        return False
    if _WEAK_TRAILING_FRAGMENT_RE.search(sentence[:-1].rstrip()):
        return False
    return True


def _repair_missing_sentence_boundaries(text: str) -> str:
    tokens = str(text or "").split()
    if len(tokens) < 2:
        return str(text or "").strip()
    out: list[str] = [tokens[0]]
    for token in tokens[1:]:
        prev = out[-1]
        prev_base = re.sub(r"[^A-Za-z].*$", "", prev).lower()
        token_base = re.sub(r"[^A-Za-z].*$", "", token)
        if (
            prev
            and prev[-1].isalnum()
            and token_base in SENTENCE_BOUNDARY_STARTERS
            and prev_base not in BOUNDARY_JOINER_PREV_TOKENS
        ):
            out[-1] = f"{prev}."
        out.append(token)
    return " ".join(out).strip()


def _sanitize_story_summary(title: str, summary: str) -> str:
    clean_title = clean_feed_text(str(title or "")).strip()
    clean_summary = sanitize_public_prose(clean_feed_text(str(summary or ""))).strip()
    if not clean_summary:
        return ""
    if clean_title and clean_summary.lower() == clean_title.lower():
        return ""
    if clean_title and clean_summary.lower().startswith(clean_title.lower()):
        clean_summary = clean_summary[len(clean_title) :].lstrip(" -:;,.")
    if not clean_summary:
        return ""
    clean_summary = _repair_malformed_punctuation_before_entities(clean_summary)
    clean_summary = _repair_missing_sentence_boundaries(clean_summary)
    clean_summary = _repair_malformed_punctuation_before_entities(clean_summary)
    clean_summary = _drop_incomplete_summary_tail(clean_summary)
    clean_summary = sanitize_public_prose(clean_summary)
    cleaned_lines = _cleanup_summary_paragraphs(clean_summary)
    clean_summary = " ".join(cleaned_lines).strip()
    if clean_title and clean_summary.lower() == clean_title.lower():
        return ""
    return clean_summary


def _story_summary_fallback(title: str) -> str:
    clean_title = clean_feed_text(str(title or "")).strip()
    if clean_title:
        return f"This source record concerns: {clean_title}."
    return "Source record summary was unavailable or could not be rendered cleanly. See the linked source for details."


def _build_today_read(stories: list[dict[str, Any]], source_by_id: dict[str, dict[str, Any]]) -> list[str]:
    if not stories:
        return []
    if len(stories) < 2:
        story = stories[0]
        lead = "Today’s saved source records point to 1 reported development."
        cleaned = _cleanup_summary_paragraphs(str(story.get("summary") or ""))
        if cleaned:
            line = cleaned[0]
        else:
            line = _story_summary_fallback(str(story.get("title") or ""))
        publishers = _story_source_publishers(story, source_by_id)
        if len(publishers) > 1:
            line = f"{line} Multiple outlets reported it: {', '.join(publishers[:3])}."
        return [lead, line]
    section_labels = []
    for story in stories:
        source = source_by_id.get(str((story.get("source_record_ids") or [""])[0]))
        label = _story_section_label(story, source)
        if label not in section_labels:
            section_labels.append(label)
    generic_labels = {"Core Gaza Developments", "Other Gaza Developments", "Lead Development"}
    public_labels = [label for label in section_labels if label not in generic_labels]
    if public_labels:
        lead = f"Today’s saved source records point to {len(stories)} reported {_pluralize(len(stories), 'development')} across {', '.join(public_labels[:3])}."
    else:
        lead = f"Today’s saved source records point to {len(stories)} reported {_pluralize(len(stories), 'development')}."
    summary_lines: list[str] = []
    for story in stories[:4]:
        cleaned = _cleanup_summary_paragraphs(str(story.get("summary") or ""))
        if cleaned:
            line = cleaned[0]
        else:
            line = _story_summary_fallback(str(story.get("title") or ""))
        publishers = _story_source_publishers(story, source_by_id)
        if len(publishers) > 1:
            line = f"{line} Multiple outlets reported it: {', '.join(publishers[:3])}."
        summary_lines.append(line)
    if not summary_lines:
        return ["Today’s source records support a limited update for Gaza; readers should rely on the story entries below for the verified details."]
    output = [lead, *summary_lines[:3]]
    return output[:5]


def _story_metadata_line(story: dict[str, Any], source: dict[str, Any] | None) -> str | None:
    if source is None:
        return None
    publisher = str(source.get("publisher") or "").strip()
    topic = str(source.get("category_hint") or story.get("category") or "").strip()
    scope = str(source.get("region_scope") or "").strip()
    source_date = _format_public_date(source.get("published_at"))
    parts = [publisher, topic, scope, source_date]
    compact = [part for part in parts if part]
    if not compact:
        return None
    return " \u00b7 ".join(compact)


def _story_context_line(story: dict[str, Any], source: dict[str, Any] | None) -> str | None:
    if source is None:
        return None
    scope = str(source.get("region_scope") or "").strip()
    source_date = _format_public_date(source.get("published_at"))
    if scope and source_date:
        return f"Context: This source record is scoped to {scope} and dated {source_date}."
    if scope:
        return f"Context: This source record is scoped to {scope}."
    if source_date:
        return f"Context: This source record is dated {source_date}."
    return None


def _story_claim_caveat(story: dict[str, Any], source: dict[str, Any] | None) -> str | None:
    if source is None:
        return None
    mode = str(source.get("attribution_mode") or story.get("attribution_mode") or "").strip()
    publisher = str(source.get("publisher") or "the source").strip()
    if mode == "military_claim_reported":
        return f"This item is based primarily on an IDF statement reported by {publisher} and should be read as a military-announced account."
    if mode in {"government_claim_reported", "faction_claim_reported", "state_aligned_media_reported"}:
        return f"This item is included as a claim-attributed account reported by {publisher}."
    if mode == "reported_public_source" and "anadolu" in publisher.lower():
        return "This item is based on reporting by Anadolu Agency and is included as a regional-wire account."
    if mode == "gaza_adjacent_context" or _is_context_only_source(source):
        return "This item occurred outside Gaza and is included as Gaza-bound aid-access context."
    return None


def _find_adjacent_public_editions(root: Path, edition_date: str) -> tuple[str | None, str | None]:
    editions_root = root / "output" / "site" / "gaza" / "editions"
    if not editions_root.exists():
        return None, None
    dates = sorted(
        [
            path.name
            for path in editions_root.iterdir()
            if path.is_dir() and DATE_RE.match(path.name) and (path / "index.html").exists()
        ]
    )
    if not dates:
        return None, None
    prev_date = None
    next_date = None
    for candidate in dates:
        if candidate < edition_date:
            prev_date = candidate
        if candidate > edition_date and next_date is None:
            next_date = candidate
    return prev_date, next_date


def compute_gaza_source_adequacy(sources: list[dict[str, Any]], stories: list[dict[str, Any]]) -> dict[str, Any]:
    publishers = sorted(
        {
            str(source.get("publisher") or "").strip()
            for source in sources
            if str(source.get("publisher") or "").strip()
        }
    )
    source_count = len(sources)
    publisher_count = len(publishers)
    categories = sorted(
        {
            str(source.get("category_hint") or "").strip()
            for source in sources
            if str(source.get("category_hint") or "").strip()
        }
    )
    rendered_publishers = []
    source_by_id = {str(source.get("source_record_id") or ""): source for source in sources}
    for story in stories:
        source_id = str((story.get("source_record_ids") or [""])[0]).strip()
        source = source_by_id.get(source_id) or {}
        publisher = str(source.get("publisher") or "").strip()
        if publisher:
            rendered_publishers.append(publisher)
    rendered_unique_publishers = sorted(set(rendered_publishers))
    one_publisher_only = len(rendered_unique_publishers) == 1 if rendered_publishers else publisher_count == 1
    all_text = " ".join(
        [
            " ".join(str(source.get("publisher") or "") for source in sources),
            " ".join(str(source.get("title") or "") for source in sources),
            " ".join(str(source.get("summary_or_snippet") or "") for source in sources),
            " ".join(str(source.get("source_type") or "") for source in sources),
            " ".join(str(source.get("reliability_tier") or "") for source in sources),
        ]
    ).lower()
    has_humanitarian_institutional = any(hint in all_text for hint in HUMANITARIAN_INSTITUTIONAL_HINTS)
    has_wire_international = any(hint in all_text for hint in WIRE_INTERNATIONAL_HINTS)
    story_count = len(stories)
    core_ground_source_count = sum(1 for source in sources if _is_core_ground_source(source))
    context_only_source_count = sum(1 for source in sources if _is_context_only_source(source))
    claim_attributed_source_count = sum(
        1
        for source in sources
        if str(source.get("attribution_mode") or "") in {
            "military_claim_reported",
            "government_claim_reported",
            "faction_claim_reported",
            "state_aligned_media_reported",
            "advocacy_or_activist_claim",
        }
    )
    if source_count >= GAZA_SOURCE_TARGET_MIN and publisher_count >= GAZA_PUBLISHER_TARGET_MIN:
        status = "daily_briefing"
        label = "Daily briefing"
    elif source_count > 0 and story_count > 0:
        status = "limited_source_update"
        label = "Limited-source update"
    else:
        status = "no_publishable_source_backed_update"
        label = "No publishable source-backed update"
    warnings: list[str] = []
    if status == "limited_source_update" or source_count < GAZA_WARNING_MIN_SOURCES or publisher_count < GAZA_WARNING_MIN_PUBLISHERS:
        warnings.append(
            f"This is a limited-source update generated from {source_count} saved {_pluralize(source_count, 'source record')} from {publisher_count} {_pluralize(publisher_count, 'publisher')}. It should be read as a partial update, not a full daily briefing."
        )
    if one_publisher_only and rendered_unique_publishers:
        warnings.append(f"All saved source records for this edition came from {rendered_unique_publishers[0]}.")
    if core_ground_source_count == 0 and source_count > 0:
        warnings.append("No core in-Gaza ground-development source was identified; context-only coverage cannot carry the edition.")
    return {
        "status": status,
        "label": label,
        "source_count": source_count,
        "publisher_count": publisher_count,
        "publishers": publishers,
        "category_count": len(categories),
        "categories": categories,
        "all_stories_one_publisher": one_publisher_only,
        "has_humanitarian_or_institutional_sources": has_humanitarian_institutional,
        "has_wire_or_international_sources": has_wire_international,
        "core_ground_source_count": core_ground_source_count,
        "context_only_source_count": context_only_source_count,
        "claim_attributed_source_count": claim_attributed_source_count,
        "thresholds": {
            "target_min_usable_sources": GAZA_SOURCE_TARGET_MIN,
            "target_min_publishers": GAZA_PUBLISHER_TARGET_MIN,
            "warning_min_sources": GAZA_WARNING_MIN_SOURCES,
            "warning_min_publishers": GAZA_WARNING_MIN_PUBLISHERS,
            "limited_min_sources": GAZA_LIMITED_MIN_SOURCES,
        },
        "warnings": warnings,
    }


def _source_quality_recommendation(adequacy: dict[str, Any]) -> str:
    status = str(adequacy.get("status") or "")
    if status == "daily_briefing":
        return "publish normally"
    if status == "limited_source_update":
        return "publish limited-source update"
    return "hold for manual supplement"


def _build_source_quality_report(
    edition_date: str,
    adequacy: dict[str, Any],
    collection_report: dict[str, Any],
    diversity_report: dict[str, Any],
) -> dict[str, Any]:
    provider_failures = list(collection_report.get("provider_failures") or [])
    rejected_counts = dict(collection_report.get("rejection_counts_by_reason") or {})
    source_family_counts = dict(collection_report.get("source_family_counts") or {})
    source_classification_counts = dict(collection_report.get("source_classification_counts") or {})
    blocked_candidate_diagnostics = list(collection_report.get("blocked_candidate_diagnostics") or [])
    duplicate_count = int(collection_report.get("suppressed_after_dedupe") or 0)
    warnings: list[str] = list(adequacy.get("warnings") or [])
    if int(adequacy.get("publisher_count") or 0) < GAZA_WARNING_MIN_PUBLISHERS:
        warnings.append("publisher diversity below warning threshold")
    if int(adequacy.get("source_count") or 0) < GAZA_WARNING_MIN_SOURCES:
        warnings.append("source count below warning threshold")
    if diversity_report.get("source_diversity_warning"):
        warnings.append("source diversity warning triggered")
    return {
        "date": edition_date,
        "source_quality_status": str(adequacy.get("status") or ""),
        "source_count": int(adequacy.get("source_count") or 0),
        "publisher_count": int(adequacy.get("publisher_count") or 0),
        "publishers": list(adequacy.get("publishers") or []),
        "category_count": int(adequacy.get("category_count") or 0),
        "all_stories_one_publisher": bool(adequacy.get("all_stories_one_publisher")),
        "core_ground_source_count": int(adequacy.get("core_ground_source_count") or 0),
        "context_only_source_count": int(adequacy.get("context_only_source_count") or 0),
        "claim_attributed_source_count": int(adequacy.get("claim_attributed_source_count") or 0),
        "source_family_counts": source_family_counts,
        "source_classification_counts": source_classification_counts,
        "has_humanitarian_or_institutional_sources": bool(adequacy.get("has_humanitarian_or_institutional_sources")),
        "has_wire_or_international_sources": bool(adequacy.get("has_wire_or_international_sources")),
        "fetch_failures": provider_failures,
        "stale_feeds": [row for row in provider_failures if "stale" in str(row.get("reason") or "").lower()],
        "duplicate_count": duplicate_count,
        "accepted_count": int(collection_report.get("kept_after_dedupe") or 0),
        "rejected_count": int(collection_report.get("rejected_count") or 0),
        "rejection_counts_by_reason": rejected_counts,
        "blocked_candidate_diagnostics": blocked_candidate_diagnostics,
        "adequacy_status": str(adequacy.get("status") or ""),
        "warnings": sorted(set(warnings)),
        "recommendation": _source_quality_recommendation(adequacy),
        "thresholds": dict(adequacy.get("thresholds") or {}),
    }


def _source_quality_report_markdown(report: dict[str, Any]) -> str:
    publishers = ", ".join(report.get("publishers") or []) or "<none>"
    failures = list(report.get("fetch_failures") or [])
    warnings = list(report.get("warnings") or [])
    family_counts = dict(report.get("source_family_counts") or {})
    classification_counts = dict(report.get("source_classification_counts") or {})
    blocked_candidates = list(report.get("blocked_candidate_diagnostics") or [])
    lines = [
        f"# Gaza Source Quality Report - {report.get('date')}",
        "",
        f"- Status: **{report.get('source_quality_status')}**",
        f"- Recommendation: **{report.get('recommendation')}**",
        f"- Sources: {report.get('source_count')} from {report.get('publisher_count')} {_pluralize(int(report.get('publisher_count') or 0), 'publisher')}",
        f"- Publishers: {publishers}",
        f"- Categories: {report.get('category_count')}",
        f"- All stories from one publisher: {str(bool(report.get('all_stories_one_publisher'))).lower()}",
        f"- Core ground-development sources: {int(report.get('core_ground_source_count') or 0)}",
        f"- Context-only sources: {int(report.get('context_only_source_count') or 0)}",
        f"- Claim-attributed sources: {int(report.get('claim_attributed_source_count') or 0)}",
        "",
        "## Source families",
    ]
    if family_counts:
        for key, value in sorted(family_counts.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Source classifications")
    if classification_counts:
        for key, value in sorted(classification_counts.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Blocked candidates")
    if blocked_candidates:
        for row in blocked_candidates:
            title = row.get("title") or "<untitled>"
            reason = row.get("reason") or row.get("classification") or "<unknown>"
            publisher = row.get("publisher") or "<unknown publisher>"
            lines.append(f"- {title} ({publisher}): {reason}")
    else:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Fetch failures")
    if failures:
        for row in failures:
            lines.append(f"- {row.get('source_id')}: {row.get('reason')}")
    else:
        lines.append("- <none>")
    lines.append("")
    lines.append("## Warnings")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- <none>")
    return "\n".join(lines) + "\n"


def _manual_sources_template_payload(edition_date: str) -> list[dict[str, str]]:
    return [
        {
            "source_record_id": f"gaza-{edition_date}-manual-001",
            "title": "Replace with source headline",
            "url": "https://example.com/replace-with-source-url",
            "publisher": "Replace with publisher name",
            "published_at": f"{edition_date}T00:00:00Z",
            "retrieved_at": utc_now(),
            "summary_or_snippet": "Replace with a direct source-backed summary/snippet; do not invent claims.",
            "source_type": "manual",
            "provider_id": "manual-supplement",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "editorial-record",
            "attribution_mode": "reported_public_source",
            "claim_status": "reported_public_source",
            "traceability_note": "Add a concise note about how the source supports a traceable public claim.",
        }
    ]


def _gaza_audio_callout_html(root: Path, edition_date: str) -> str:
    gaza_root = root / "output" / "site" / "gaza"
    transcript_path = gaza_root / "audio" / f"{edition_date}-transcript.html"
    mp3_path = gaza_root / "audio" / f"{edition_date}.mp3"
    podcast_path = gaza_root / "audio" / "podcast.xml"
    has_transcript = transcript_path.exists()
    has_mp3 = mp3_path.exists()
    has_podcast = podcast_path.exists()
    if not has_transcript and not has_mp3:
        return ""
    lines = ['<section class="audio-callout">', "<h2>Audio Briefing</h2>"]
    if has_mp3:
        lines.append(f'<audio controls preload="none" src="/gaza/audio/{html.escape(edition_date)}.mp3"></audio>')
        lines.append(f'<p><a href="/gaza/audio/{html.escape(edition_date)}-transcript.html">Read audio transcript</a></p>')
    elif has_transcript:
        lines.append(f'<p><a href="/gaza/audio/{html.escape(edition_date)}-transcript.html">Read audio transcript</a></p>')
        lines.append("<p>Audio file not generated yet.</p>")
    if has_podcast:
        lines.append('<p><a href="/gaza/audio/podcast.xml">Podcast feed</a></p>')
    lines.append("</section>")
    return "\n".join(lines)


def render_gaza_edition(
    edition_date: str,
    stories: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    root: Path = ROOT,
) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    today_read_lines = _build_today_read(stories, source_by_id)
    rendered_story_count = len(stories)
    unique_publishers = sorted(
        {str(source.get("publisher") or "").strip() for source in sources if str(source.get("publisher") or "").strip()}
    )
    prev_edition, next_edition = _find_adjacent_public_editions(ROOT, edition_date)
    friendly_edition_date = _format_public_date(edition_date) or edition_date

    def render_story(story: dict[str, Any]) -> None:
        first_source_id = str((story.get("source_record_ids") or [""])[0])
        primary_source = source_by_id.get(first_source_id)
        chunks.append(f"<article><h3>{html.escape(story['title'])}</h3>")
        metadata_line = _story_metadata_line(story, primary_source)
        if metadata_line:
            chunks.append(f"<p><em>{html.escape(metadata_line)}</em></p>")
        story_paragraphs = _cleanup_summary_paragraphs(str(story.get("summary") or ""))
        if not story_paragraphs:
            story_paragraphs = [_story_summary_fallback(str(story.get("title") or ""))]
        for paragraph in story_paragraphs:
            chunks.append(f"<p>{html.escape(paragraph)}</p>")
        context_line = _story_context_line(story, primary_source)
        if context_line:
            chunks.append(f"<p><strong>{html.escape(context_line.split(':', 1)[0])}:</strong>{html.escape(context_line.split(':', 1)[1])}</p>")
        caveat = _story_claim_caveat(story, primary_source)
        if caveat:
            chunks.append(f"<p><strong>Claim attribution:</strong> {html.escape(caveat)}</p>")
        chunks.append("<p><strong>Sources</strong></p><ul>")
        for source_id in story.get("source_record_ids") or []:
            source = source_by_id[source_id]
            chunks.append(
                f'<li><a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">'
                f'{html.escape(source["title"])}</a> - {html.escape(source["publisher"])}</li>'
            )
        chunks.append("</ul></article>")

    chunks: list[str] = []
    chunks.append("<h1>Dispatches From Gaza</h1>")
    audio_callout = _gaza_audio_callout_html(root, edition_date)
    if audio_callout:
        chunks.append(audio_callout)
    if adequacy.get("status") == "limited_source_update":
        chunks.append(
            f'<p><strong>{html.escape(str((adequacy.get("warnings") or [""])[0]))}</strong></p>'
        )
        if bool(adequacy.get("all_stories_one_publisher")) and (adequacy.get("publishers") or []):
            chunks.append(
                f"<p><strong>All saved source records for this edition came from {html.escape(str((adequacy.get('publishers') or [''])[0]))}.</strong></p>"
            )
    if stories:
        if today_read_lines:
            chunks.append("<h2>Today’s Read</h2>")
            for line in today_read_lines:
                chunks.append(f"<p>{html.escape(line)}</p>")
        chunks.append("<h2>At A Glance</h2>")
        chunks.append("<ul>")
        for story in stories:
            chunks.append(f"<li>{html.escape(story['title'])}</li>")
        chunks.append("</ul>")
        grouped: dict[str, list[dict[str, Any]]] = {
            "Core Gaza Developments": [],
            "Civilian Harm and Access": [],
            "International Law and Diplomacy": [],
            "Other Gaza Developments": [],
        }
        for story in stories:
            first_source_id = str((story.get("source_record_ids") or [""])[0])
            source = source_by_id.get(first_source_id)
            grouped[_story_section_label(story, source)].append(story)
        for label in ("Core Gaza Developments", "Civilian Harm and Access", "International Law and Diplomacy", "Other Gaza Developments"):
            section_stories = grouped.get(label) or []
            if not section_stories:
                continue
            chunks.append(f"<h2>{html.escape(label)}</h2>")
            for story in section_stories:
                render_story(story)
    else:
        chunks.append("<p>No source-backed Gaza stories were generated for this date. Add project-local source records before publishing factual coverage.</p>")
        chunks.append("<h2>Sources</h2><p>No source records were available.</p>")
    chunks.append("<h2>Source Mix</h2>")
    chunks.append(f"<p>Source mix: {rendered_story_count} stories from {len(unique_publishers)} publishers. Source coverage may be uneven.</p>")
    if unique_publishers and len(unique_publishers) <= 8:
        chunks.append(f"<p>Publishers: {html.escape(', '.join(unique_publishers))}.</p>")
    chunks.append("<h2>Source Note</h2>")
    chunks.append("<p>This edition is generated only from saved source records available at publish time. Source coverage may be uneven; stories are included only when a traceable source record exists.</p>")
    chunks.append('<p><a href="/gaza/archive.html">Gaza archive</a> | <a href="/">Dispatches home</a>')
    if prev_edition:
        chunks.append(f' | <a href="/gaza/editions/{html.escape(prev_edition)}/">Previous edition</a>')
    if next_edition:
        chunks.append(f' | <a href="/gaza/editions/{html.escape(next_edition)}/">Next edition</a>')
    chunks.append("</p>")
    body_chunks = "\n    ".join(chunks)
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/gaza/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/gaza-logo.png" alt="Dispatches From Gaza">
    </section>
    <p class="eyebrow">{html.escape(str(adequacy.get('label') or 'Daily briefing'))} / {friendly_edition_date}</p>
    <p>Generated from saved source records available for {html.escape(friendly_edition_date)}.</p>
    {body_chunks}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {edition_date}", f"{BASE_URL}/gaza/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def discover_edition_dates(site_root: Path) -> list[str]:
    return discover_public_edition_dates(site_root, DISPATCH_SLUG)


def render_archive_index_rss(root: Path, edition_date: str, dry_run: bool, wrote: list[str], include_current: bool = True) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(
        slug=DISPATCH_SLUG,
        name=DISPATCH_NAME,
        edition_date=edition_date,
        tagline=DISPATCH_TAGLINE,
        logo="gaza-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )
    dates = discover_edition_dates(site_root)
    if include_current and edition_date not in dates:
        dates = sorted([*dates, edition_date], reverse=True)
    gaza_root = site_root / DISPATCH_SLUG
    write_text(gaza_root / "index.html", render_dispatch_index_for_dates(dispatch, dates, site_root), dry_run, wrote)
    write_text(gaza_root / "archive.html", render_archive_for_dates(dispatch, dates, site_root), dry_run, wrote)
    write_text(gaza_root / "rss.xml", render_rss_for_dates(dispatch, dates, site_root), dry_run, wrote)


def build_manifests(
    root: Path,
    edition_date: str,
    sources: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    generated_at: str,
    warnings: list[str],
    errors: list[str],
    adequacy: dict[str, Any],
    allow_post_edition_date_sources: bool = False,
    post_edition_date_source_count: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    dispatch_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date
    backup_dir = BACKUP_ROOT / edition_date
    rendered_story_count = sum(
        1 for story in stories if not isinstance(story, dict) or bool(story.get("public_rendered", True))
    )
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
        "local_output_path": str(site_dir),
        "local_dispatch_output_path": str(dispatch_dir),
        "local_backup_path": str(backup_dir),
        "template_version": "gaza-source-record-v1",
        "source_count": len(sources),
        "story_count": rendered_story_count,
        "source_adequacy_status": str(adequacy.get("status") or ""),
        "source_adequacy_label": str(adequacy.get("label") or ""),
        "publisher_count": int(adequacy.get("publisher_count") or 0),
        "publishers": list(adequacy.get("publishers") or []),
        "source_adequacy_warnings": list(adequacy.get("warnings") or []),
        "allow_post_edition_date_sources": bool(allow_post_edition_date_sources),
        "post_edition_date_sources_included": bool(post_edition_date_source_count > 0),
        "post_edition_date_source_count": int(post_edition_date_source_count),
        "source_manifest_path": str(site_dir / "sources_manifest.json"),
        "curation_manifest_path": str(site_dir / "curation_manifest.json"),
        "free_public_artifacts": [
            str(site_dir / "index.html"),
            str(site_dir / "edition_manifest.json"),
            str(site_dir / "sources_manifest.json"),
            str(site_dir / "curation_manifest.json"),
        ],
        "paid_or_detail_artifacts": [],
        "detail_artifacts_publicly_exposed": False,
        "is_free_public": True,
        "has_detail_tier": False,
        "public_exposed": True,
        "warnings": warnings,
        "errors": errors,
    }
    run_manifest = {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "source_workflow": "project-local-manual-source-records",
        "did_not_invent_sources": True,
        "source_adequacy_status": str(adequacy.get("status") or ""),
        "source_count": int(adequacy.get("source_count") or 0),
        "publisher_count": int(adequacy.get("publisher_count") or 0),
        "publishers": list(adequacy.get("publishers") or []),
        "source_adequacy_warnings": list(adequacy.get("warnings") or []),
        "allow_post_edition_date_sources": bool(allow_post_edition_date_sources),
        "post_edition_date_sources_included": bool(post_edition_date_source_count > 0),
        "post_edition_date_source_count": int(post_edition_date_source_count),
        "old_project_dependency": False,
        "warnings": warnings,
        "errors": errors,
    }
    return edition_manifest, sources, stories, run_manifest


def upsert(rows: list[dict[str, Any]], key: str, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(row[key]): row for row in rows if key in row}
    for row in incoming:
        by_key[str(row[key])] = row
    return sorted(by_key.values(), key=lambda row: str(row.get(key, "")))


def read_record_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    return payload if isinstance(payload, list) else []


def update_shared_records(
    root: Path,
    edition_date: str,
    sources: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    generated_at: str,
    dry_run: bool,
    wrote: list[str],
) -> None:
    records_root = root / "data" / "records"
    records_root.mkdir(parents=True, exist_ok=True)
    files = {
        "dispatches": records_root / "dispatches.json",
        "editions": records_root / "editions.json",
        "sources": records_root / "sources.json",
        "records": records_root / "records.json",
        "curation_decisions": records_root / "curation_decisions.json",
        "detail_packages": records_root / "detail_packages.json",
    }
    edition_id = f"gaza-{edition_date}"
    dispatches = upsert(
        read_record_file(files["dispatches"]),
        "dispatch_id",
        [
            {
                "dispatch_id": DISPATCH_ID,
                "slug": DISPATCH_SLUG,
                "dispatch_slug": DISPATCH_SLUG,
                "public_name": DISPATCH_NAME,
                "internal_name": "Gaza Dispatch",
                "description": "Free public Gaza briefing compiled from traceable project-local source records.",
                "is_free_public": True,
                "has_detail_tier": False,
                "public_exposed": True,
                "created_at": "2026-05-03T00:00:00Z",
                "updated_at": generated_at,
            }
        ],
    )
    site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    backup_dir = BACKUP_ROOT / edition_date
    editions = upsert(
        read_record_file(files["editions"]),
        "edition_id",
        [
            {
                "edition_id": edition_id,
                "dispatch_id": DISPATCH_ID,
                "dispatch_slug": DISPATCH_SLUG,
                "slug": DISPATCH_SLUG,
                "edition_date": edition_date,
                "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
                "output_path": str(site_dir),
                "backup_path": str(backup_dir),
                "generated_at": generated_at,
                "status": "public",
                "is_free_public": True,
                "public_exposed": True,
                "has_detail_tier": False,
            }
        ],
    )
    source_rows = [
        {
            "source_id": source["source_record_id"],
            "source_record_id": source["source_record_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "publisher": source["publisher"],
            "title": source["title"],
            "url": source["url"],
            "published_at": source["published_at"],
            "retrieved_at": source["retrieved_at"],
            "retrieved_local_date": source.get("retrieved_local_date"),
            "post_edition_date_source": bool(source.get("post_edition_date_source")),
            "archive_path": None,
            "reliability_tier": source["reliability_tier"],
            "attribution_mode": str(source.get("attribution_mode") or ""),
            "claim_status": str(source.get("claim_status") or ""),
            "story_selection_excluded_reason": str(source.get("story_selection_excluded_reason") or ""),
        }
        for source in sources
    ]
    record_rows = [
        {
            "record_id": story["story_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "category": story["category"],
            "title": story["title"],
            "public_summary": story["summary"],
            "detail_summary": None,
            "score": story["score"],
            "included_public": True,
            "included_detail": False,
            "source_ids": story["source_record_ids"],
            "generated_at": generated_at,
            "is_free_public": True,
            "public_exposed": True,
        }
        for story in stories
    ]
    decisions = [
        {
            "decision_id": f"decision-{story['story_id']}",
            "record_id": story["story_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "included_public": True,
            "included_detail": False,
            "exclusion_reason": None,
            "scoring_reasons": story["scoring_reasons"],
        }
        for story in stories
    ]
    existing_sources = [row for row in read_record_file(files["sources"]) if row.get("edition_id") != edition_id]
    existing_records = [row for row in read_record_file(files["records"]) if row.get("edition_id") != edition_id]
    existing_decisions = [row for row in read_record_file(files["curation_decisions"]) if row.get("edition_id") != edition_id]
    write_json(files["dispatches"], dispatches, dry_run, wrote)
    write_json(files["editions"], upsert(read_record_file(files["editions"]), "edition_id", editions), dry_run, wrote)
    write_json(files["sources"], upsert(existing_sources, "source_id", source_rows), dry_run, wrote)
    write_json(files["records"], upsert(existing_records, "record_id", record_rows), dry_run, wrote)
    write_json(files["curation_decisions"], upsert(existing_decisions, "decision_id", decisions), dry_run, wrote)
    write_json(files["detail_packages"], read_record_file(files["detail_packages"]), dry_run, wrote)


def run_gaza_dispatch(
    root: Path,
    edition_date: str,
    from_manual_sources: bool,
    dry_run: bool,
    render: bool,
    all_steps: bool,
    allow_thin_edition: bool = False,
    allow_post_edition_date_sources: bool = False,
) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    generated_at = utc_now()
    warnings: list[str] = []
    errors: list[str] = []
    wrote: list[str] = []
    ensure_gaza_source_tree(root, dry_run, wrote)
    if not from_manual_sources:
        raise ValueError("Gaza generation currently requires --from-manual-sources")
    manual_path, manual_records = load_manual_sources(root, edition_date)
    raw_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "raw" / edition_date
    normalized_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "normalized" / edition_date
    curated_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "curated" / edition_date
    write_json(raw_dir / "raw_sources.json", manual_records, dry_run, wrote)
    normalized, norm_warnings, norm_errors = normalize_sources(
        manual_records,
        edition_date,
        generated_at,
        allow_post_edition_date_sources=allow_post_edition_date_sources,
    )
    warnings.extend(norm_warnings)
    errors.extend(norm_errors)
    normalized, cross_edition_report = filter_recent_duplicate_sources(root, edition_date, normalized, lookback_days=7)
    write_json(normalized_dir / "normalized_sources.json", normalized, dry_run, wrote)
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "dedupe_report.json", cross_edition_report, dry_run, wrote)
    if cross_edition_report.get("suppressed_candidate_count", 0):
        warnings.append(
            f"suppressed {cross_edition_report['suppressed_candidate_count']} repeated/stale candidate sources via cross-edition dedupe"
        )
    if cross_edition_report.get("input_candidate_count", 0) > 0 and not normalized:
        errors.append("No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition.")
    post_edition_date_source_count = sum(
        1
        for source in normalized
        if bool(source.get("post_edition_date_source")) and not str(source.get("story_selection_excluded_reason") or "").strip()
    )
    timing_metadata = build_gaza_collection_timing_metadata(
        normalized,
        edition_date,
        actual_run_utc=generated_at,
    )
    story_selection_excluded_count = sum(1 for source in normalized if str(source.get("story_selection_excluded_reason") or "").strip())
    story_selection_excluded_reasons: dict[str, int] = {}
    for source in normalized:
        reason = str(source.get("story_selection_excluded_reason") or "").strip()
        if reason:
            story_selection_excluded_reasons[reason] = int(story_selection_excluded_reasons.get(reason) or 0) + 1
    context = _load_collection_context(root, edition_date)
    context_stage = dict(context.get("stage_counts") or {})
    provider_diagnostics = list(context.get("provider_diagnostics") or []) or [
        {
            "source_id": "manual_sources_json",
            "source_tier": "manual_supplements",
            "status": "ok" if manual_records else "no_candidates",
            "reason": "manual records loaded" if manual_records else "no manual records for date",
            "raw_candidates": len(manual_records),
            "accepted_before_dedupe": int(cross_edition_report.get("input_candidate_count", 0)),
            "suppressed_duplicate": int(cross_edition_report.get("suppressed_candidate_count", 0)),
            "kept_after_dedupe": int(cross_edition_report.get("kept_candidate_count", 0)),
        }
    ]
    manual_records_by_publisher: dict[str, list[str]] = {}
    for row in manual_records:
        if not isinstance(row, dict):
            continue
        publisher = str(row.get("publisher") or "").strip().lower()
        source_record_id = str(row.get("source_record_id") or "").strip()
        if not publisher or not source_record_id:
            continue
        manual_records_by_publisher.setdefault(publisher, []).append(source_record_id)
    for diag in provider_diagnostics:
        if not isinstance(diag, dict):
            continue
        status = str(diag.get("status") or "").strip()
        publisher = str(diag.get("publisher") or "").strip().lower()
        likely_gap = bool(status == "no_matches" and publisher and publisher in manual_records_by_publisher)
        diag["provider_checked"] = True
        diag["candidates_seen"] = int(diag.get("raw_items") or diag.get("raw_candidates") or 0)
        diag["candidates_rejected"] = int(sum(int(v or 0) for v in dict(diag.get("rejected_counts") or {}).values()))
        diag["rejection_reason"] = (
            ", ".join(str(item) for item in (diag.get("no_match_reason_flags") or []))
            if status == "no_matches"
            else str(diag.get("error") or diag.get("reason") or "")
        )
        diag["likely_discovery_gap"] = likely_gap
        diag["manual_backfill_source"] = manual_records_by_publisher.get(publisher, []) if likely_gap else []
    for diag in provider_diagnostics:
        if not isinstance(diag, dict):
            continue
        raw_candidates = int(diag.get("raw_candidates") or diag.get("raw_items") or 0)
        accepted_before = int(diag.get("accepted_before_dedupe") or diag.get("accepted") or 0)
        diag["raw_candidates"] = raw_candidates
        diag["accepted_before_dedupe"] = accepted_before
        diag["kept_after_dedupe"] = diag.get("kept_after_dedupe")
        diag["tls_error"] = bool(diag.get("tls_error"))
        diag["backend_used"] = str(diag.get("backend_used") or "python")
    rejected_by_reason = dict(context.get("rejected_by_reason") or {})
    rejected_no_anchor_count = sum(1 for warning in norm_warnings if "rejected_no_palestinian_anchor" in str(warning))
    if rejected_no_anchor_count > 0:
        rejected_by_reason["rejected_no_palestinian_anchor"] = int(rejected_by_reason.get("rejected_no_palestinian_anchor") or 0) + rejected_no_anchor_count
    rejected_by_reason["normalization_errors"] = int(rejected_by_reason.get("normalization_errors") or 0) + len(norm_errors)
    rejected_by_reason["cross_edition_duplicates"] = int(cross_edition_report.get("suppressed_candidate_count", 0))
    stage_counts = {
        "registry_sources": int(context_stage.get("registry_sources") or 1),
        "enabled_providers_configured": int(context_stage.get("enabled_providers_configured") or 1),
        "providers_attempted": int(context_stage.get("providers_attempted") or 1),
        "providers_successful": int(context_stage.get("providers_successful") or (1 if manual_records else 0)),
        "raw_candidates": int(context_stage.get("raw_candidates") or len(manual_records)),
        "normalized_candidates": len(normalized) + int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "accepted_before_dedupe": int(cross_edition_report.get("input_candidate_count", 0)),
    }
    low_relevance_survivors = sum(1 for row in normalized if str(row.get("relevance_band") or "") == "low")
    no_story_explanation = "stories_available"
    if len(manual_records) == 0:
        no_story_explanation = "no_candidates_found_from_attempted_providers"
    elif int(cross_edition_report.get("input_candidate_count", 0)) > 0 and len(normalized) == 0:
        no_story_explanation = "all_candidates_suppressed_as_duplicates_or_stale"
    elif low_relevance_survivors > 0 and low_relevance_survivors == len(normalized):
        no_story_explanation = "only_low_relevance_items_survived"
    providers_configured = list(context.get("providers_configured") or ["manual_sources_json"])
    providers_attempted = list(context.get("providers_attempted") or ["manual_sources_json"])
    providers_successful = list(context.get("providers_successful") or (["manual_sources_json"] if manual_records else []))
    provider_failures = list(context.get("provider_failures") or ([] if manual_records else [{"source_id": "manual_sources_json", "reason": "zero_candidates", "status": "no_candidates"}]))
    collection_report = {
        "edition_date": edition_date,
        "lookback_window_days": 7,
        "providers_configured": providers_configured,
        "providers_attempted": providers_attempted,
        "providers_successful": providers_successful,
        "provider_failures": provider_failures,
        "raw_candidate_count": int(context.get("raw_candidate_count") or len(manual_records)),
        "normalized_candidate_count": len(normalized) + int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "accepted_candidate_count_before_dedupe": int(cross_edition_report.get("input_candidate_count", 0)),
        "kept_after_dedupe": int(cross_edition_report.get("kept_candidate_count", 0)),
        "suppressed_after_dedupe": int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "rejection_counts_by_reason": rejected_by_reason,
        "top_rejected_examples": list(context.get("top_rejected_examples") or [])[:25],
        "review_candidates": list(context.get("review_candidates") or [])[:25],
        "rejected_off_topic": int(rejected_by_reason.get("rejected_off_topic", 0)),
        "rejected_weak_date": int(rejected_by_reason.get("rejected_weak_date_basis", 0)) + int(rejected_by_reason.get("rejected_missing_published_at", 0)),
        "rejected_missing_url_or_title": int(rejected_by_reason.get("rejected_missing_url", 0)) + int(rejected_by_reason.get("rejected_missing_title", 0)),
        "suppressed_duplicate": int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "final_story_count": 0,
        "story_selection_excluded_count": story_selection_excluded_count,
        "story_selection_excluded_reason_counts": story_selection_excluded_reasons,
        "low_relevance_survivors": low_relevance_survivors,
        "no_story_explanation": no_story_explanation,
        "no_story_credibility_decision": "no_candidates_found" if no_story_explanation == "no_candidates_found_from_attempted_providers" else no_story_explanation,
        "providers_attempted_count": len(providers_attempted),
        "providers_successful_count": len(providers_successful),
        "provider_diagnostics": provider_diagnostics,
        "source_providers_attempted": provider_diagnostics,
        "source_family_counts": dict(context.get("source_family_counts") or _source_family_counts_from_sources(normalized)),
        "stage_counts": stage_counts,
        "google_wrapper_count": int(cross_edition_report.get("google_wrapper_count", 0)),
        "canonical_publisher_url_count": int(cross_edition_report.get("canonical_publisher_url_count", 0)),
        "allow_post_edition_date_sources": bool(allow_post_edition_date_sources),
        "post_edition_date_sources_included": bool(post_edition_date_source_count > 0),
        "post_edition_date_source_count": int(post_edition_date_source_count),
    }
    collection_report.update(timing_metadata)
    stories, relevance_decisions, top_story_candidates = curate_stories(normalized, edition_date, generated_at)
    original_story_rows = [dict(story) for story in stories]
    public_stories, written_public_exclusions = _apply_written_public_story_filter(stories)
    for excluded in written_public_exclusions:
        relevance_decisions.append(
            {
                "story_id": excluded.get("story_id"),
                "title": excluded.get("title"),
                "action": excluded.get("action"),
                "reason": excluded.get("reason"),
                "source_record_ids": excluded.get("source_record_ids") or [],
            }
        )
    stories = public_stories
    adequacy = compute_gaza_source_adequacy(normalized, stories)
    collection_report["source_adequacy"] = adequacy
    if adequacy.get("warnings"):
        warnings.extend(str(item) for item in adequacy.get("warnings") or [])
    collection_report["final_story_count"] = len(stories)
    collection_report["public_story_count"] = sum(1 for story in stories if bool(story.get("core_ground_development")))
    collection_report["core_gaza_count"] = sum(1 for story in stories if str(story.get("story_scope") or "") == "core_gaza")
    collection_report["palestinian_development_count"] = sum(
        1 for story in stories if str(story.get("story_scope") or "") == "palestinian_development"
    )
    collection_report["written_public_exclusions"] = written_public_exclusions
    collection_report["rejected_count"] = int(sum(int(v or 0) for v in rejected_by_reason.values()))
    collection_report["story_relevance_diagnostics"] = relevance_decisions
    collection_report["top_story_candidates"] = top_story_candidates
    top_story = stories[0] if stories else None
    collection_report["top_story_relevance_score"] = int(top_story.get("top_story_relevance_score") or 0) if top_story else 0
    collection_report["top_story_relevance_terms_matched"] = list(top_story.get("relevance_terms_matched") or []) if top_story else []
    has_substantive_ground = any(bool(story.get("core_ground_development")) for story in stories)
    collection_report["thin_edition_override_used"] = bool(allow_thin_edition)
    collection_report["blocked_for_thin_or_off_topic"] = False
    collection_report["thin_edition_reason"] = None
    if stories and not has_substantive_ground:
        if allow_thin_edition:
            warnings.append("thin Gaza edition override used: no substantive Gaza/Palestinian ground-development story cleared threshold.")
            collection_report["thin_edition_reason"] = "no_substantive_ground_story_override_used"
        else:
            errors.append("No substantive Gaza/Palestinian ground-development story cleared threshold; publication blocked (use --allow-thin-edition to override).")
            collection_report["blocked_for_thin_or_off_topic"] = True
            collection_report["thin_edition_reason"] = "no_substantive_ground_story"
    if len(normalized) > 0 and len(stories) == 0:
        collection_report["no_story_explanation"] = "all_candidates_rejected_or_deduped_in_curation"
        collection_report["no_story_credibility_decision"] = "candidates_rejected"
    elif collection_report["no_story_explanation"] == "all_candidates_suppressed_as_duplicates_or_stale":
        collection_report["no_story_credibility_decision"] = "all_candidates_deduped"
    enabled_auto = int(context.get("enabled_auto_provider_count") or 0)
    if enabled_auto > 0 and not providers_attempted:
        collection_report.setdefault("warnings", []).append("enabled automatic providers exist but none were attempted")
    if not dict(collection_report.get("source_family_counts") or {}):
        collection_report["source_family_counts"] = _source_family_counts_from_sources(normalized)
    collection_report["source_classification_counts"] = _gaza_source_classification_counts(normalized, collection_report)
    collection_report["source_classification_diagnostics"] = _gaza_source_classification_diagnostics(normalized, collection_report)
    collection_report["blocked_candidate_diagnostics"] = [
        row
        for row in collection_report["source_classification_diagnostics"]
        if str(row.get("classification") or "") != "core_ground_development"
    ]
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json", collection_report, dry_run, wrote)
    dedupe_result = dedupe_public_stories(root, DISPATCH_SLUG, edition_date, stories, dry_run=dry_run, written=wrote)
    stories = dedupe_result.stories
    story_usage: dict[str, list[str]] = {}
    for story in stories:
        story_id = str(story.get("story_id") or "").strip()
        if not story_id:
            continue
        for source_id in story.get("source_record_ids") or []:
            key = str(source_id or "").strip()
            if not key:
                continue
            story_usage.setdefault(key, []).append(story_id)
    for source in normalized:
        source_id = str(source.get("source_record_id") or "").strip()
        source["used_in_story_ids"] = sorted(dict.fromkeys(story_usage.get(source_id) or []))
    merged_story_by_id = {str(story.get("story_id") or ""): story for story in stories if str(story.get("story_id") or "")}
    decision_by_story = {str(item.get("story_id")): item for item in dedupe_result.decisions if isinstance(item, dict)}
    written_exclusion_by_story = {str(item.get("story_id") or ""): item for item in written_public_exclusions if str(item.get("story_id") or "")}
    curation_manifest_full: list[dict[str, Any]] = []
    for story in original_story_rows:
        story_id = str(story.get("story_id") or "")
        written_exclusion = written_exclusion_by_story.get(story_id)
        decision = decision_by_story.get(story_id) or {}
        include_decision = str(decision.get("include_decision") or "include")
        if written_exclusion:
            include_decision = "exclude_written_public_edition"
        row = dict(story)
        if written_exclusion:
            row["included_in_public_summary"] = False
        row["include_decision"] = include_decision
        row["public_rendered"] = include_decision == "include"
        if written_exclusion:
            row["excluded_reason"] = written_exclusion.get("reason")
        row["prior_story_matched"] = decision.get("prior_story_matched")
        row["prior_edition_date"] = decision.get("prior_edition_date")
        row["dedupe_classification"] = decision.get("classification") or row.get("dedupe_classification")
        row["dedupe_reasons"] = decision.get("duplicate_reasons") or row.get("dedupe_reasons") or []
        merged_story = merged_story_by_id.get(story_id)
        if merged_story:
            for key in ("source_record_ids", "source_ids", "source_urls", "publisher_names", "canonical_urls", "source_records"):
                merged_values = list(merged_story.get(key) or [])
                if merged_values:
                    row[key] = merged_values
            if merged_story.get("summary"):
                row["summary"] = merged_story.get("summary")
            if merged_story.get("score") is not None:
                row["score"] = merged_story.get("score")
            if merged_story.get("scoring_reasons"):
                row["scoring_reasons"] = list(merged_story.get("scoring_reasons") or [])
        curation_manifest_full.append(row)
    stage_drop_diagnostics = _build_stage_drop_diagnostics(
        raw_sources=manual_records,
        normalized_sources=normalized,
        curated_stories=original_story_rows,
        rendered_stories=stories,
        cross_edition_report=cross_edition_report,
        curation_relevance_diagnostics=relevance_decisions,
    )
    diversity_report = build_source_diversity_report(
        edition_date,
        raw_sources=manual_records,
        normalized_sources=normalized,
        curated_stories=original_story_rows,
        rendered_stories=stories,
        collection_report=collection_report,
        cross_edition_report=cross_edition_report,
        stage_drop_diagnostics=stage_drop_diagnostics,
    )
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "source_diversity_report.json", diversity_report, dry_run, wrote)
    source_quality_report = _build_source_quality_report(
        edition_date,
        adequacy=adequacy,
        collection_report=collection_report,
        diversity_report=diversity_report,
    )
    write_json(
        root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "source_quality_report.json",
        source_quality_report,
        dry_run,
        wrote,
    )
    write_text(
        root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "source_quality_report.md",
        _source_quality_report_markdown(source_quality_report),
        dry_run,
        wrote,
    )
    if str(adequacy.get("status") or "") != "daily_briefing":
        template_path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.template.json"
        template_payload = _manual_sources_template_payload(edition_date)
        write_json(template_path, template_payload, dry_run, wrote)
        warnings.append(f"manual supplement recommended: add source-backed records at {template_path}")
    collection_report["source_diversity"] = {
        "source_diversity_warning": bool(diversity_report.get("source_diversity_warning")),
        "publisher_dominance_warning": bool(diversity_report.get("publisher_dominance_warning")),
        "warning_severity": str(diversity_report.get("warning_severity") or "info"),
        "warning_reason": list(diversity_report.get("warning_reason") or []),
        "low_diversity_warning_reason": list(diversity_report.get("low_diversity_warning_reason") or []),
        "publisher_dominance_warning_reason": list(diversity_report.get("publisher_dominance_warning_reason") or []),
        "unique_rendered_publishers": int(diversity_report.get("unique_rendered_publishers") or 0),
        "unique_raw_publishers": int(diversity_report.get("unique_raw_publishers") or 0),
    }
    collection_report["source_quality_status"] = str(source_quality_report.get("source_quality_status") or "")
    collection_report["source_quality_recommendation"] = str(source_quality_report.get("recommendation") or "")
    collection_report["source_quality_warnings"] = list(source_quality_report.get("warnings") or [])
    if diversity_report.get("source_diversity_warning"):
        warning_reasons = ", ".join(str(item) for item in (diversity_report.get("warning_reason") or []))
        warnings.append(f"source diversity warning: {warning_reasons}")
    elif diversity_report.get("publisher_dominance_warning"):
        warning_reasons = ", ".join(str(item) for item in (diversity_report.get("publisher_dominance_warning_reason") or []))
        warnings.append(f"source dominance note: {warning_reasons}")
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json", collection_report, dry_run, wrote)
    if len(normalized) == 0:
        errors.append("No valid traceable Gaza sources survived normalization and dedupe; refusing public edition generation.")
    if len(stories) == 0:
        errors.append("No source-backed Gaza stories survived curation/dedupe; refusing public edition generation.")
    write_json(curated_dir / "curation_manifest.json", curation_manifest_full, dry_run, wrote)
    source_coverage_audit = write_gaza_source_coverage_audit(root, edition_date)
    collection_report["source_coverage_audit_path"] = source_coverage_audit.get("json_report_path")
    collection_report["source_coverage_audit_markdown_path"] = source_coverage_audit.get("markdown_report_path")
    collection_report["source_coverage_audit_warning_count"] = len(source_coverage_audit.get("warnings") or [])
    collection_report["source_coverage_audit_rendered_public_story_count"] = len(source_coverage_audit.get("rendered_public_stories") or [])
    collection_report["rendered_public_story_source_ids"] = list(source_coverage_audit.get("rendered_public_story_source_ids") or [])
    collection_report["rendered_public_story_sources"] = list(source_coverage_audit.get("rendered_public_story_sources") or [])
    for warning in source_coverage_audit.get("warnings") or []:
        text = str(warning).strip()
        if text and text not in warnings:
            warnings.append(text)
    should_render = render or all_steps
    if should_render and not errors:
        html_content = render_gaza_edition(edition_date, stories, normalized, adequacy, root=root)
        edition_manifest, sources_manifest, curation_manifest, run_manifest = build_manifests(
            root,
            edition_date,
            normalized,
            curation_manifest_full,
            generated_at,
            warnings,
            errors,
            adequacy,
            allow_post_edition_date_sources=allow_post_edition_date_sources,
            post_edition_date_source_count=post_edition_date_source_count,
        )
        edition_manifest.update(timing_metadata)
        run_manifest.update(timing_metadata)
        consistency_errors = _assert_gaza_artifact_consistency(edition_manifest, sources_manifest, curation_manifest, html_rendered=True)
        if consistency_errors:
            errors.extend(consistency_errors)
            edition_manifest["errors"] = list(edition_manifest.get("errors") or []) + consistency_errors
            edition_manifest["public_exposed"] = False
            should_render = False
    if should_render and not errors:
        html_content = render_gaza_edition(edition_date, stories, normalized, adequacy, root=root)
        edition_manifest, sources_manifest, curation_manifest, run_manifest = build_manifests(
            root,
            edition_date,
            normalized,
            curation_manifest_full,
            generated_at,
            warnings,
            errors,
            adequacy,
            allow_post_edition_date_sources=allow_post_edition_date_sources,
            post_edition_date_source_count=post_edition_date_source_count,
        )
        edition_manifest.update(timing_metadata)
        run_manifest.update(timing_metadata)
        for base in (
            root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date,
            root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date,
        ):
            write_text(base / "index.html", html_content, dry_run, wrote)
            write_json(base / "edition_manifest.json", edition_manifest, dry_run, wrote)
            write_json(base / "sources_manifest.json", sources_manifest, dry_run, wrote)
            write_json(base / "curation_manifest.json", curation_manifest, dry_run, wrote)
        for asset in ("site.css", "gaza-logo.png", "bluefern.png"):
            copy_file(root / "assets" / asset, root / "output" / "site" / DISPATCH_SLUG / "assets" / asset, dry_run, wrote, warnings)
        render_archive_index_rss(root, edition_date, dry_run, wrote, include_current=True)
        backup_dir = BACKUP_ROOT / edition_date
        write_text(backup_dir / "index.html", html_content, dry_run, wrote)
        write_json(backup_dir / "edition_manifest.json", edition_manifest, dry_run, wrote)
        write_json(backup_dir / "sources_manifest.json", sources_manifest, dry_run, wrote)
        write_json(backup_dir / "curation_manifest.json", curation_manifest, dry_run, wrote)
        write_json(backup_dir / "run_manifest.json", run_manifest, dry_run, wrote)
        update_shared_records(root, edition_date, normalized, stories, generated_at, dry_run, wrote)
    elif should_render:
        failed_manifest = {
            "dispatch_name": DISPATCH_NAME,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_date": edition_date,
            "generated_at": generated_at,
            "public_url": None,
            "local_output_path": None,
            "local_dispatch_output_path": str(root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date),
            "local_backup_path": None,
            "template_version": "gaza-source-record-v1",
            "source_count": len(normalized),
            "story_count": len(stories),
            "source_adequacy_status": str(adequacy.get("status") or ""),
            "source_adequacy_label": str(adequacy.get("label") or ""),
            "publisher_count": int(adequacy.get("publisher_count") or 0),
            "publishers": list(adequacy.get("publishers") or []),
            "source_adequacy_warnings": list(adequacy.get("warnings") or []),
            **timing_metadata,
            "free_public_artifacts": [],
            "paid_or_detail_artifacts": [],
            "detail_artifacts_publicly_exposed": False,
            "is_free_public": True,
            "has_detail_tier": False,
            "public_exposed": False,
            "warnings": warnings,
            "errors": errors,
        }
        failed_manifest["errors"] = list(failed_manifest.get("errors") or []) + _assert_gaza_artifact_consistency(
            failed_manifest, normalized, stories, html_rendered=False
        )
        dispatch_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date
        write_json(dispatch_dir / "edition_manifest.json", failed_manifest, dry_run, wrote)
        write_json(dispatch_dir / "sources_manifest.json", normalized, dry_run, wrote)
        write_json(dispatch_dir / "curation_manifest.json", curation_manifest_full, dry_run, wrote)
        site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
        if site_dir.exists():
            wrote.append(str(site_dir))
            if not dry_run:
                shutil.rmtree(site_dir)
        render_archive_index_rss(root, edition_date, dry_run, wrote, include_current=False)
    return {
        "ok": not errors,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "manual_source_path": str(manual_path),
        "source_count": len(normalized),
        "story_count": len(stories),
        "public_story_count": int(collection_report.get("public_story_count") or 0),
        "source_adequacy_status": str(adequacy.get("status") or ""),
        "publisher_count": int(adequacy.get("publisher_count") or 0),
        "publishers": list(adequacy.get("publishers") or []),
        "source_adequacy_warnings": list(adequacy.get("warnings") or []),
        "source_coverage_audit_path": str(collection_report.get("source_coverage_audit_path") or ""),
        "source_coverage_audit_markdown_path": str(collection_report.get("source_coverage_audit_markdown_path") or ""),
        "source_coverage_audit_warning_count": int(collection_report.get("source_coverage_audit_warning_count") or 0),
        "source_coverage_audit_rendered_public_story_count": int(collection_report.get("source_coverage_audit_rendered_public_story_count") or 0),
        "rendered_public_story_count": int(collection_report.get("source_coverage_audit_rendered_public_story_count") or 0),
        "rendered_public_story_source_ids": list(collection_report.get("rendered_public_story_source_ids") or []),
        "rendered_public_story_sources": list(collection_report.get("rendered_public_story_sources") or []),
        "source_classification_counts": dict(collection_report.get("source_classification_counts") or {}),
        "source_classification_diagnostics": list(collection_report.get("source_classification_diagnostics") or []),
        "dry_run": dry_run,
        "wrote": wrote,
        "warnings": warnings,
        "errors": errors,
        "is_free_public": True,
        "has_detail_tier": False,
        "public_exposed": not errors,
        "backup_root": str(BACKUP_ROOT),
        "allow_thin_edition": bool(allow_thin_edition),
        "allow_post_edition_date_sources": bool(allow_post_edition_date_sources),
        "post_edition_date_sources_included": bool(post_edition_date_source_count > 0),
        "post_edition_date_source_count": int(post_edition_date_source_count),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate self-contained source-backed Gaza editions.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--historical", action="store_true", help="Generate a historical Gaza edition.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Use project-local manual source records.")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without changing files.")
    parser.add_argument("--render", action="store_true", help="Render the public edition and manifests.")
    parser.add_argument("--all", action="store_true", help="Run all generation stages.")
    parser.add_argument("--allow-thin-edition", action="store_true", help="Allow publish when only thin Gaza coverage survives relevance gates.")
    parser.add_argument(
        "--allow-post-edition-date-sources",
        action="store_true",
        help="Allow sources retrieved after the edition date to be used in this Gaza rerun/backfill.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_gaza_dispatch(
            ROOT,
            args.date,
            from_manual_sources=args.from_manual_sources,
            dry_run=args.dry_run,
            render=args.render,
            all_steps=args.all,
            allow_thin_edition=args.allow_thin_edition,
            allow_post_edition_date_sources=args.allow_post_edition_date_sources,
        )
    except Exception as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
