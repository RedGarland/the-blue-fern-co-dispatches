from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from scripts.american_pressure_anchor_ids import canonical_valid_anchor_ids_by_pillar
from scripts.american_pressure_text_cleaning import clean_candidate_text, clean_google_rss_title, safe_text
TARGETS_PATH = ROOT / "data" / "dispatches" / "american-pressure" / "search_targets.yml"
CANDIDATES_ROOT = ROOT / "data" / "dispatches" / "american-pressure" / "candidates"
RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PILLARS = (
    "food_pressure",
    "financial_distress_pressure",
    "housing_household_cost_pressure",
    "health_access_pressure",
    "labor_income_pressure",
    "local_system_strain",
    "environmental_pressure",
    "policy_implementation",
)
INVESTOR_ONLY_TERMS = (
    "shareholder",
    "bondholder",
    "eps",
    "guidance",
    "capital structure",
    "equity offering",
    "investor call",
)
OPINION_ONLY_TERMS = ("opinion", "op-ed", "editorial", "commentary")
PUBLIC_IMPACT_TERMS = (
    "jobs",
    "layoffs",
    "rent",
    "eviction",
    "debt",
    "bankruptcy",
    "clinic",
    "hospital",
    "food",
    "grocery",
    "utility",
    "disaster",
    "flood",
    "fire",
    "storm",
    "benefit",
    "aid",
)
US_RELEVANCE_TERMS = ("u.s.", "united states", "us ", " county", "california", "texas", "new york", "florida", "wisconsin")
NON_US_TERMS = ("canada", "uk", "europe", "australia", "china", "india", "germany", "france", "japan")
US_STATE_HINTS = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
)


def _safe_text(value: Any) -> str:
    return safe_text(value)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _load_targets() -> dict[str, Any]:
    if not TARGETS_PATH.exists():
        raise FileNotFoundError(f"missing search target config: {TARGETS_PATH}")
    raw = TARGETS_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def _load_registry_anchors() -> dict[str, list[str]]:
    return canonical_valid_anchor_ids_by_pillar(ROOT, PILLARS)


def _candidate_path(day: str) -> Path:
    return CANDIDATES_ROOT / day / "candidate_sources.json"


def _read_existing_candidate_urls(window_days: int = 28) -> set[str]:
    urls: set[str] = set()
    if not CANDIDATES_ROOT.exists():
        return urls
    today = date.today()
    for offset in range(window_days + 1):
        day = (today - timedelta(days=offset)).isoformat()
        path = _candidate_path(day)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("sources", []) if isinstance(payload, dict) else []
        for row in rows:
            if isinstance(row, dict):
                url = _safe_text(row.get("url"))
                if url:
                    urls.add(url)
    return urls


def _fetch_rss_items(query: str, *, timeout: int = 15) -> list[dict[str, str]]:
    url = RSS_TEMPLATE.format(query=urllib.parse.quote_plus(query))
    req = urllib.request.Request(url, headers={"User-Agent": "BlueFernDispatches/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        xml_text = response.read().decode("utf-8", errors="replace")
    root = ET.fromstring(xml_text)
    out: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        out.append(
            {
                "title": clean_google_rss_title(item.findtext("title"), item.findtext("source")),
                "url": _safe_text(item.findtext("link")),
                "publisher": clean_candidate_text(item.findtext("source")),
                "published_at": _safe_text(item.findtext("pubDate")),
                "summary_or_snippet": clean_candidate_text(item.findtext("description")),
            }
        )
    return out


def _looks_non_us(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in NON_US_TERMS) and not any(term in lowered for term in US_RELEVANCE_TERMS)


def _publisher_quality(raw: dict[str, Any]) -> str:
    publisher = _safe_text(raw.get("publisher")).lower()
    url = _safe_text(raw.get("url")).lower()
    # Google News RSS URLs are wrappers around the underlying publisher's story.
    # Do not auto-classify wrapper links as low-quality aggregators.
    if "news.google.com" in url and not publisher:
        return "aggregator_or_repost"
    if any(term in publisher for term in ("opinion", "editorial", "column", "advice")):
        return "opinion_or_advice"
    if any(term in publisher for term in ("gov", "department", "agency", "bureau", "fema", "cdc", "usda", "bls", "cms", "hrsa", "hhs", "treasury")):
        return "official_primary"
    if any(term in publisher for term in ("npr", "pbs", "public radio", "public media")):
        return "public_media"
    if any(term in publisher for term in ("reuters", "ap ", "associated press", "new york times", "washington post", "wall street journal", "usa today", "cnn", "abc", "cbs", "nbc")):
        return "reputable_national_news"
    if any(term in publisher for term in ("tribune", "times", "journal", "gazette", "herald", "post", "news")):
        return "reputable_local_news"
    if any(term in publisher for term in ("foundation", "institute", "nonprofit", "university", "research")):
        return "nonprofit_or_research"
    if any(term in publisher for term in ("dailyhunt", "eastafrican", "tribal news network", "bc spca")):
        return "foreign_or_non_us"
    return "low_confidence"


def _us_relevance(raw: dict[str, Any], *, title: str, summary: str, location: str) -> tuple[bool, str]:
    text = f"{title} {summary} {_safe_text(raw.get('publisher'))}".lower()
    if location and any(state in location.lower() for state in US_STATE_HINTS):
        return True, "us_location_resolved"
    if any(term in text for term in US_RELEVANCE_TERMS) or any(state in text for state in US_STATE_HINTS):
        return True, "explicit_us_relevance"
    if _looks_non_us(text):
        return False, "foreign_or_non_us_without_clear_us_relevance"
    return False, "us_relevance_unclear"


def _extract_location(text: str) -> str:
    bad_second_tokens = {"rejecting", "announces", "announce", "reports", "report", "warning", "budget", "demand", "closure"}
    patterns = (
        r"\b([A-Z][a-z]+ County,\s*[A-Z][a-z]+)\b",
        r"\b([A-Z][a-z]+,\s*[A-Z][a-z]+)\b",
        r"\b([A-Z][a-z]+\s+[A-Z][a-z]+,\s*[A-Z][a-z]+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            parts = [p.strip().lower() for p in candidate.split(",")]
            if len(parts) == 2 and parts[1] in bad_second_tokens:
                continue
            return candidate
    return ""


def _has_key_number(text: str) -> bool:
    return bool(re.search(r"\b\d{2,3}(?:,\d{3})*(?:\.\d+)?\b", text))


def score_candidate(raw: dict[str, Any], *, pillar: str, anchor_ids: list[str], seen_urls: set[str]) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    rejections: list[str] = []
    title = _safe_text(raw.get("title"))
    summary = _safe_text(raw.get("summary_or_snippet"))
    url = _safe_text(raw.get("url"))
    publisher = _safe_text(raw.get("publisher"))
    combined = f"{title} {summary} {publisher}".lower()
    if not url:
        rejections.append("no_url")
        return -100, reasons, rejections
    if url in seen_urls:
        score -= 60
        rejections.append("duplicate_or_stale")
    publisher_quality = _publisher_quality(raw)
    if publisher_quality == "official_primary":
        score += 15
        reasons.append("official_primary_source")
    elif publisher_quality in {"reputable_local_news", "reputable_national_news", "public_media"}:
        score += 10
        reasons.append("reputable_reporting")
    elif publisher_quality in {"aggregator_or_repost", "foreign_or_non_us", "opinion_or_advice", "low_confidence"}:
        score -= 25
        rejections.append(f"publisher_quality:{publisher_quality}")
    if any(term in combined for term in INVESTOR_ONLY_TERMS):
        score -= 80
        rejections.append("investor_only")
    if any(term in combined for term in OPINION_ONLY_TERMS):
        score -= 40
        rejections.append("opinion_only")
    if _looks_non_us(combined):
        score -= 40
        rejections.append("non_us_not_relevant")
    if any(term in combined for term in PUBLIC_IMPACT_TERMS):
        score += 20
        reasons.append("clear_public_pressure_angle")
    else:
        score -= 40
        rejections.append("no_public_impact")
    if publisher:
        score += 20
        reasons.append("reliable_source_attribution")
    location = _extract_location(f"{title} {summary}")
    if location:
        score += 10
        reasons.append("clear_location")
    us_ok, us_reason = _us_relevance(raw, title=title, summary=summary, location=location)
    if us_ok:
        score += 15
        reasons.append(us_reason)
    else:
        score -= 30
        rejections.append(us_reason)
    if any(term in combined for term in ("families", "workers", "residents", "patients", "customers", "households")):
        score += 15
        reasons.append("affected_people_or_services")
    if _has_key_number(combined):
        score += 10
        reasons.append("source_backed_key_number")
    if anchor_ids:
        score += 15
        reasons.append("linkable_data_anchor")
    return score, reasons, rejections


def _pillar_guidance(pillar: str) -> tuple[str, str, str]:
    guidance = {
        "food_pressure": (
            "This may signal tighter grocery tradeoffs and higher pantry demand.",
            "Households with low or fixed incomes and local food support networks.",
            "Watch pantry demand, benefit access, and grocery affordability shifts.",
        ),
        "financial_distress_pressure": (
            "This may signal rising debt stress or instability in local employers/services.",
            "Workers tied to stressed employers, borrowers, and local small businesses.",
            "Watch layoffs, service disruptions, and distress spillover into bills or housing.",
        ),
        "housing_household_cost_pressure": (
            "This may signal heavier monthly housing and utility burden.",
            "Renters, cost-burdened households, and residents facing utility pressure.",
            "Watch eviction risk, rent trends, and utility burden changes.",
        ),
        "health_access_pressure": (
            "This may signal pressure in coverage, clinic access, or care continuity.",
            "Patients with chronic needs, caregivers, and underinsured households.",
            "Watch clinic capacity, coverage shifts, and care delays.",
        ),
        "labor_income_pressure": (
            "This may signal weakening paycheck stability for local households.",
            "Hourly workers, newly displaced workers, and households with low savings buffers.",
            "Watch layoffs, hours cuts, and unemployment direction.",
        ),
        "local_system_strain": (
            "This may signal strain in local service delivery and resilience.",
            "Residents relying on local agencies, schools, transit, or emergency services.",
            "Watch recurring disruptions and emergency management capacity.",
        ),
        "environmental_pressure": (
            "This may signal weather/disaster stress feeding into household cost pressure.",
            "Climate-vulnerable households, rural communities, and outdoor workers.",
            "Watch recovery timelines, infrastructure stress, and secondary cost impacts.",
        ),
        "policy_implementation": (
            "This may signal friction in delivering policy support to households.",
            "Benefit-dependent households and frontline service providers.",
            "Watch enrollment delays, eligibility barriers, and implementation backlog.",
        ),
    }
    return guidance.get(pillar, guidance["local_system_strain"])


def _build_candidate(*, day: str, pillar: str, raw: dict[str, Any], score: int, score_reasons: list[str], anchor_ids: list[str]) -> dict[str, Any]:
    title = clean_google_rss_title(raw.get("title"), raw.get("publisher"))
    summary = clean_candidate_text(raw.get("summary_or_snippet"))
    url = _safe_text(raw.get("url"))
    publisher = clean_candidate_text(raw.get("publisher")) or "Unknown publisher"
    location = _extract_location(f"{title} {summary}")
    publisher_quality = _publisher_quality(raw)
    us_relevant, us_reason = _us_relevance(raw, title=title, summary=summary, location=location)
    potential, who, watch = _pillar_guidance(pillar)
    source_id = re.sub(r"[^a-z0-9]+", "-", f"{pillar}-{title}".lower()).strip("-")[:80] or f"{pillar}-candidate"
    source_record_id = f"ap-{day}-{source_id}"
    angle = "Candidate signal; review against source text before approval."
    reader_headline = title
    if len(reader_headline) > 95 or " - " in reader_headline or "(" in reader_headline:
        reader_headline = _candidate_reader_headline(pillar, summary, location)
    candidate = {
        "source_record_id": source_record_id,
        "source_id": source_id,
        "title": title,
        "url": url,
        "publisher": publisher,
        "published_at": _safe_text(raw.get("published_at")) or _iso_utc_now(),
        "retrieved_at": _iso_utc_now(),
        "summary_or_snippet": summary,
        "source_type": "news_report",
        "region_scope": "US",
        "category_hint": pillar,
        "pillar": pillar,
        "reliability_tier": "reputable_reporting",
        "source_role": "human_story",
        "item_type": "current_week_development",
        "reader_headline": reader_headline,
        "human_story_summary": summary,
        "what_happened": summary,
        "potential_relevance": potential,
        "who_may_feel_it": who,
        "what_to_watch_next": watch,
        "location": location,
        "affected_people": "",
        "pressure_direction": "rising",
        "public_pressure_angle": angle,
        "linked_data_anchor_ids": anchor_ids,
        "candidate_score": score,
        "candidate_score_reasons": score_reasons,
        "review_status": "needs_review",
        "publisher_quality": publisher_quality,
        "us_relevance_ok": us_relevant,
        "us_relevance_reason": us_reason,
        "location_confidence": "high" if location else "low",
        "editorial_rejection_reason": "",
    }
    prose_bad = (
        (" - " in title)
        or ("(" in title and ")" in title)
        or ("structure, rejecting" in title.lower())
        or ("nationally," in summary.lower() and not us_relevant)
    )
    if prose_bad:
        candidate["review_status"] = "quarantine"
        candidate["editorial_rejection_reason"] = "prose_quality_failed"
    if not us_relevant or publisher_quality in {"foreign_or_non_us", "aggregator_or_repost", "opinion_or_advice", "low_confidence"}:
        candidate["review_status"] = "quarantine"
        if not candidate["editorial_rejection_reason"]:
            candidate["editorial_rejection_reason"] = "us_relevance_or_source_quality_failed"
    return candidate


def _candidate_reader_headline(pillar: str, summary: str, location: str) -> str:
    location_clause = ""
    if location and location.lower() not in {"us", "u.s.", "united states"}:
        location_clause = f" in {location}"
    if pillar == "food_pressure":
        return f"Food support networks{location_clause} report rising demand".strip()
    if pillar == "labor_income_pressure":
        return f"Paycheck pressure signals{location_clause} point to job instability".strip()
    if pillar == "housing_household_cost_pressure":
        return f"Housing and bill pressure{location_clause} is tightening household budgets".strip()
    if pillar == "health_access_pressure":
        return f"Health access strain{location_clause} is affecting care continuity".strip()
    if pillar == "financial_distress_pressure":
        return f"Financial distress indicators{location_clause} suggest deeper household risk".strip()
    if pillar == "environmental_pressure":
        return f"Weather stress{location_clause} is feeding household pressure".strip()
    if pillar == "policy_implementation":
        return f"Policy rollout friction{location_clause} is slowing support delivery".strip()
    if summary:
        return summary[:100].rstrip(".")
    return "Source-backed household pressure signal"


def scout_day(day: str, *, max_per_pillar: int, fetcher: Any | None = None) -> dict[str, Any]:
    config = _load_targets()
    target_groups: dict[str, Any] = config.get("target_groups", {})
    anchor_map = _load_registry_anchors()
    suggested_unavailable_anchor_ids: set[str] = set()
    seen_urls = _read_existing_candidate_urls()
    effective_fetcher = fetcher or _fetch_rss_items
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    attempted_queries = 0
    fetch_error_count = 0
    for pillar in PILLARS:
        group = target_groups.get(pillar, {})
        phrases = group.get("search_phrases", [])
        hint_ids = [_safe_text(item) for item in group.get("data_anchor_hints", []) if _safe_text(item)]
        available_anchor_ids = set(anchor_map.get(pillar, []))
        selected_anchor_ids: list[str] = [anchor_id for anchor_id in hint_ids if anchor_id in available_anchor_ids]
        for hint in hint_ids:
            if hint not in available_anchor_ids:
                suggested_unavailable_anchor_ids.add(hint)
        if not selected_anchor_ids:
            selected_anchor_ids = list(anchor_map.get(pillar, []))
        bucket: list[dict[str, Any]] = []
        for phrase in phrases:
            query = f"{phrase} when:2d"
            attempted_queries += 1
            try:
                items = effective_fetcher(query)
            except Exception as exc:  # noqa: BLE001
                fetch_error_count += 1
                rejected.append({"pillar": pillar, "title": phrase, "reason": f"fetch_error:{exc}"})
                continue
            for raw in items:
                score, reasons, rejection_reasons = score_candidate(raw, pillar=pillar, anchor_ids=selected_anchor_ids, seen_urls=seen_urls)
                if not _safe_text(raw.get("url")):
                    rejected.append({"pillar": pillar, "title": _safe_text(raw.get("title")), "reason": "no_url"})
                    continue
                candidate = _build_candidate(day=day, pillar=pillar, raw=raw, score=score, score_reasons=reasons, anchor_ids=selected_anchor_ids)
                candidate["candidate_bucket"] = "recommended" if score >= 45 and not rejection_reasons else ("maybe" if score >= 5 else "rejected")
                if candidate.get("review_status") == "quarantine":
                    candidate["candidate_bucket"] = "rejected"
                if rejection_reasons:
                    candidate["rejection_reasons"] = rejection_reasons
                bucket.append(candidate)
                seen_urls.add(candidate["url"])
        bucket.sort(key=lambda row: int(row.get("candidate_score") or 0), reverse=True)
        accepted.extend(bucket[:max_per_pillar])
    no_live_backend = attempted_queries > 0 and fetch_error_count >= attempted_queries and len(accepted) == 0
    diagnostics: dict[str, Any] = {
        "collection_backend": "google_news_rss",
        "attempted_queries": attempted_queries,
        "fetch_error_count": fetch_error_count,
        "no_live_collection_backend_configured": no_live_backend,
        "suggested_unavailable_anchor_ids": sorted(suggested_unavailable_anchor_ids),
    }
    if no_live_backend:
        diagnostics["no_live_collection_backend_message"] = (
            "No live candidate collection backend is configured; add manual candidate records or configure source collectors."
        )
    return {
        "date": day,
        "sources": accepted,
        "rejected_candidates": rejected,
        "source_count": len(accepted),
        "generated_at": _iso_utc_now(),
        "target_config": str(TARGETS_PATH),
        "intake_only": True,
        "diagnostics": diagnostics,
    }


def _write_candidate_file(day: str, payload: dict[str, Any]) -> Path:
    path = _candidate_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _date_span(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(_validate_date(start), "%Y-%m-%d").date()
    end_dt = datetime.strptime(_validate_date(end), "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise ValueError("--end-date must be on or after --start-date")
    return [(start_dt + timedelta(days=offset)).isoformat() for offset in range((end_dt - start_dt).days + 1)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scout daily American Pressure candidate stories.")
    parser.add_argument("--date", help="Single intake date YYYY-MM-DD.")
    parser.add_argument("--start-date", help="Range start date YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Range end date YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true", help="Collect and score without writing files.")
    parser.add_argument("--write", action="store_true", help="Write candidate output files.")
    parser.add_argument("--max-per-pillar", type=int, default=4, help="Max accepted candidates per pillar.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.date and not (args.start_date and args.end_date):
            raise ValueError("provide --date or --start-date/--end-date")
        days = [_validate_date(args.date)] if args.date else _date_span(args.start_date, args.end_date)
        results: list[dict[str, Any]] = []
        for day in days:
            payload = scout_day(day, max_per_pillar=max(1, int(args.max_per_pillar)))
            if args.write and not args.dry_run:
                path = _write_candidate_file(day, payload)
                payload["candidate_file"] = str(path)
            results.append(payload)
        output = {"ok": True, "days": results, "write": bool(args.write and not args.dry_run), "published": False}
    except Exception as exc:  # noqa: BLE001
        output = {"ok": False, "errors": [str(exc)], "published": False}
    print(json.dumps(output, indent=2))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
