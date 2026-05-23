from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bluefern_dispatches.cascadia_curate import why_it_matters
from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import containing_week, format_coverage_label, week_label
from bluefern_dispatches.generator import (
    BASE_URL,
    CASCADIA_LOGO_ASSET,
    CASCADIA_PUBLIC_DESCRIPTION,
    CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE,
    TEMPLATE_VERSION,
    DispatchConfig,
    discover_public_edition_dates,
    footer,
    header,
    page,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
    write_text as generator_write_text,
)
from bluefern_dispatches.story_dedupe import dedupe_public_stories


DISPATCH_NAME = "The Cascadia Briefing"
INTERNAL_PRODUCT_NAME = "Cascadia Signal"
DISPATCH_SLUG = "cascadia"
SHORT_PUBLIC_DESCRIPTION = "Weekly source-backed regional briefings for Washington, Oregon, and Idaho."
MAP_NOTE = "Regional systems weather map built from source-backed public reporting across Washington, Oregon, and Idaho."
PRESSURE_LABELS = {
    "food": "Food and household support",
    "food_pressure": "Food and household support",
    "health": "Health care access",
    "health_access_pressure": "Health care access",
    "labor": "Jobs and local economy",
    "labor_income_pressure": "Jobs and local economy",
    "housing": "Housing and utility pressure",
    "housing_household_cost_pressure": "Housing and utility pressure",
    "transportation": "Transportation and access",
    "policy": "Schools and local government services",
    "government": "Schools and local government services",
    "safety": "Public safety and emergency services",
    "public safety": "Public safety and emergency services",
    "emergency": "Public safety and emergency services",
    "wildfire": "Wildfire, drought, flood, and recovery",
    "drought": "Wildfire, drought, flood, and recovery",
    "flood": "Wildfire, drought, flood, and recovery",
    "recovery": "Wildfire, drought, flood, and recovery",
}
REGIONAL_AREAS = {
    "WA": "Puget Sound",
    "OR": "Portland metro",
    "ID": "Idaho rural communities",
}
LOCATION_PRECISION_NOTES = {
    "address": "Mapped to reported address/facility.",
    "facility": "Mapped to reported address/facility.",
    "city": "Mapped to city level.",
    "county": "Mapped to county level.",
    "statewide": "Statewide report.",
    "regional": "Regional report.",
}
PRESSURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Wildfire, drought, flood, and recovery": ("wildfire", "smoke", "drought", "flood", "water shortage", "burn ban", "disaster recovery", "landslide", "fire"),
    "Housing and utility pressure": ("housing", "rent", "homeless", "shelter", "eviction", "utility bill", "cooling bills", "power shutoff", "water bill", "utilities"),
    "Health care access": ("hospital", "clinic", "patients", "insurance", "medicaid", "provider", "behavioral health", "opioid treatment", "health care"),
    "Public safety and emergency services": ("public safety", "emergency response", "fire department", "police staffing", "911", "ems", "ambulance"),
    "Schools and local government services": ("school district", "public schools", "local government", "city services", "county services", "budget cuts", "service reduction"),
    "Jobs and local economy": ("layoff", "layoffs", "wages", "unemployment", "business closure", "local economy", "workforce", "job cuts"),
    "Food and household support": ("food bank", "snap", "meal", "pantry", "grocery affordability", "food insecurity"),
    "Transportation and access": ("ferry", "transit", "bus", "road closure", "highway", "bridge", "airport", "mobility", "rail"),
}
PLACE_STOPWORDS = {"washington", "oregon", "idaho", "pacific northwest", "cascadia", "statewide", "regional"}
LOCAL_CITY_HINTS: tuple[str, ...] = (
    "Seattle", "Tacoma", "Spokane", "Everett", "Bellevue", "Olympia", "Vancouver", "Yakima", "Bellingham", "Tri-Cities", "Wenatchee", "Woodinville",
    "Portland", "Salem", "Eugene", "Bend", "Medford", "Ashland", "Corvallis", "Grants Pass",
    "Boise", "Meridian", "Nampa", "Caldwell", "Idaho Falls", "Pocatello", "Coeur d'Alene", "Twin Falls",
)
LOCAL_COUNTY_HINTS: tuple[str, ...] = (
    "King County", "Pierce County", "Snohomish County", "Spokane County", "Yakima County", "Chelan County",
    "Multnomah County", "Marion County", "Lane County", "Deschutes County", "Jackson County",
    "Ada County", "Canyon County", "Bonneville County", "Bannock County", "Kootenai County",
)
LOCAL_ENTITY_HINTS: tuple[str, ...] = (
    "MultiCare", "UW Medicine", "Providence", "Swedish", "Virginia Mason", "Seattle Children's",
    "Washington State Ferries", "WSDOT", "Sound Transit", "King County Metro", "Spokane Transit",
    "TriMet", "ODOT", "Portland Public Schools", "Salem-Keizer", "OHSU", "Providence Oregon", "Legacy Health",
    "St. Luke's", "Saint Alphonsus", "Idaho State University", "Valley Regional Transit",
)
LOCAL_SERVICE_AREA_HINTS: tuple[str, ...] = (
    "Puget Sound", "Willamette Valley", "Treasure Valley", "Tri-Cities", "I-5 corridor", "I-84 corridor",
)
LOCAL_PLACE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+County\b", "county"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Public Schools\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+School District\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Transit\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Ferries\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Terminal\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+General\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Hospital\b", "facility"),
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Clinic\b", "facility"),
)
ENTITY_PLACE_ALIASES: dict[str, tuple[str, str]] = {
    "multicare": ("Tacoma", "facility"),
    "uw medicine": ("Seattle", "facility"),
    "providence oregon": ("Portland", "facility"),
    "providence": ("Seattle", "facility"),
    "swedish": ("Seattle", "facility"),
    "virginia mason": ("Seattle", "facility"),
    "seattle children's": ("Seattle", "facility"),
    "washington state ferries": ("Seattle", "facility"),
    "wsdot": ("Olympia", "facility"),
    "sound transit": ("Seattle", "facility"),
    "king county metro": ("Seattle", "facility"),
    "spokane transit": ("Spokane", "facility"),
    "trimet": ("Portland", "facility"),
    "odot": ("Salem", "facility"),
    "portland public schools": ("Portland", "facility"),
    "salem-keizer": ("Salem", "facility"),
    "ohsu": ("Portland", "facility"),
    "legacy health": ("Portland", "facility"),
    "st. luke's": ("Boise", "facility"),
    "saint alphonsus": ("Boise", "facility"),
    "idaho state university": ("Pocatello", "facility"),
    "valley regional transit": ("Boise", "facility"),
}
GENERIC_LANDING_TERMS = ("alerts & emergencies currently selected", "category", "public safety", "landing", "home page")
MAP_LOCATIONS_PATH = Path("data") / "dispatches" / "cascadia" / "map_locations.yml"
DEFAULT_MAP_LOCATIONS: dict[str, Any] = {
    "state_centroids": {
        "WA": {"lat": 47.4009, "lon": -120.4508},
        "OR": {"lat": 43.8041, "lon": -120.5542},
        "ID": {"lat": 44.2405, "lon": -114.4788},
    },
    "source_defaults": {
        "Washington State Standard Feed": {"lat": 47.0379, "lon": -122.9007},
        "Idaho Capital Sun Feed": {"lat": 43.6150, "lon": -116.2023},
        "Portland Public Alerts": {"lat": 45.5152, "lon": -122.6784},
    },
    "place_defaults": {
        "Seattle": {"lat": 47.6062, "lon": -122.3321},
        "Tacoma": {"lat": 47.2529, "lon": -122.4443},
        "Spokane": {"lat": 47.6588, "lon": -117.4260},
        "Everett": {"lat": 47.97898, "lon": -122.20208},
        "Bellevue": {"lat": 47.6101, "lon": -122.2015},
        "Olympia": {"lat": 47.0379, "lon": -122.9007},
        "Vancouver": {"lat": 45.6387, "lon": -122.6615},
        "Yakima": {"lat": 46.6021, "lon": -120.5059},
        "Bellingham": {"lat": 48.7519, "lon": -122.4787},
        "Tri-Cities": {"lat": 46.2112, "lon": -119.1372},
        "Wenatchee": {"lat": 47.4235, "lon": -120.3103},
        "Woodinville": {"lat": 47.7543, "lon": -122.1635},
        "Portland": {"lat": 45.5152, "lon": -122.6784},
        "Salem": {"lat": 44.9429, "lon": -123.0351},
        "Eugene": {"lat": 44.0521, "lon": -123.0868},
        "Bend": {"lat": 44.0582, "lon": -121.3153},
        "Medford": {"lat": 42.3265, "lon": -122.8756},
        "Ashland": {"lat": 42.1946, "lon": -122.7095},
        "Corvallis": {"lat": 44.5646, "lon": -123.2620},
        "Grants Pass": {"lat": 42.4390, "lon": -123.3284},
        "Boise": {"lat": 43.6150, "lon": -116.2023},
        "Meridian": {"lat": 43.6121, "lon": -116.3915},
        "Nampa": {"lat": 43.5407, "lon": -116.5635},
        "Caldwell": {"lat": 43.6629, "lon": -116.6874},
        "Idaho Falls": {"lat": 43.4917, "lon": -112.0339},
        "Pocatello": {"lat": 42.8713, "lon": -112.4455},
        "Coeur d'Alene": {"lat": 47.6777, "lon": -116.7805},
        "Twin Falls": {"lat": 42.5629, "lon": -114.4609},
        "King County": {"lat": 47.5480, "lon": -121.9836},
        "Pierce County": {"lat": 47.0379, "lon": -122.1295},
        "Snohomish County": {"lat": 47.9893, "lon": -122.2021},
        "Spokane County": {"lat": 47.6572, "lon": -117.4294},
        "Yakima County": {"lat": 46.6021, "lon": -120.5059},
        "Chelan County": {"lat": 47.7511, "lon": -120.7401},
        "Multnomah County": {"lat": 45.5148, "lon": -122.6749},
        "Marion County": {"lat": 44.9429, "lon": -123.0351},
        "Lane County": {"lat": 44.0521, "lon": -123.0868},
        "Deschutes County": {"lat": 44.0582, "lon": -121.3153},
        "Jackson County": {"lat": 42.3265, "lon": -122.8756},
        "Ada County": {"lat": 43.6150, "lon": -116.2023},
        "Canyon County": {"lat": 43.6221, "lon": -116.7093},
        "Bonneville County": {"lat": 43.4917, "lon": -112.0339},
        "Bannock County": {"lat": 42.8713, "lon": -112.4455},
        "Kootenai County": {"lat": 47.6777, "lon": -116.7805},
    },
}


def public_stories(curated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [story for story in curated if story.get("included_in_public_summary")]


def validate_public_stories(stories: list[dict[str, Any]]) -> list[str]:
    errors = []
    for story in stories:
        if not story.get("source_record_ids") or not story.get("source_urls"):
            errors.append(f"public story lacks source trace: {story.get('story_id')}")
        if not story.get("why_it_matters") and not story.get("source_records"):
            errors.append(f"public story lacks why-it-matters trace: {story.get('story_id')}")
    return errors


def sources_manifest_from_curated(
    curated: list[dict[str, Any]],
    edition_date: str,
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str | None = None,
    coverage_label: str | None = None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for story in curated:
        for record in story.get("source_records", []):
            item = {
                "source_record_id": record["source_record_id"],
                "source_id": record.get("source_id"),
                "title": record.get("title"),
                "url": record.get("source_url") or record.get("url") or record.get("canonical_url"),
                "publisher": record.get("publisher"),
                "published_at": record.get("published_at"),
                "retrieved_at": record.get("retrieved_at"),
                "archive_path": None,
                "used_in_story_ids": [story["story_id"]],
                "claim_ids": [story["story_id"]],
                "dispatch_slug": DISPATCH_SLUG,
                "public_name": DISPATCH_NAME,
                "briefing_type": briefing_type,
                "run_date": run_date,
                "edition_date": edition_date,
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "coverage_label": coverage_label,
                "region_scope": record.get("region_scope"),
                "category_hint": record.get("category_hint"),
            }
            for field in [
                "source_type",
                "provider_id",
                "provider_name",
                "query_used",
                "search_start_date",
                "search_end_date",
                "region_terms_matched",
                "state_hint",
                "reliability_tier",
                "derived_from_edition_date",
                "derived_from_edition_path",
                "derived_from_manifest_path",
                "original_source_record_id",
                "source_url",
                "source_title",
                "weekly_date_basis",
                "traceability_note",
                "address",
                "address_line",
                "facility_name",
                "location_name",
                "location_precision",
                "precision",
                "place",
                "geography",
                "lat",
                "lon",
                "latitude",
                "longitude",
            ]:
                if field in record:
                    item[field] = record.get(field)
            by_id[record["source_record_id"]] = item
    return sorted(by_id.values(), key=lambda item: item["source_record_id"])


def public_curation_manifest(
    curated: list[dict[str, Any]],
    run_date: str | None = None,
    edition_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str | None = None,
    coverage_label: str | None = None,
) -> list[dict[str, Any]]:
    public = []
    for story in curated:
        item = dict(story)
        item.pop("source_records", None)
        item["dispatch_slug"] = DISPATCH_SLUG
        item["public_name"] = DISPATCH_NAME
        item["briefing_type"] = briefing_type
        item["run_date"] = run_date
        item["edition_date"] = edition_date
        item["coverage_start"] = coverage_start
        item["coverage_end"] = coverage_end
        item["coverage_label"] = coverage_label
        public.append(item)
    return public


def render_story_group(category: str, stories: list[dict[str, Any]]) -> str:
    items = []
    for story in sorted(stories, key=lambda item: item["score"], reverse=True):
        source_records = [record for record in story.get("source_records", []) if isinstance(record, dict)]
        source_blocks = "\n".join(render_source_metadata(url, source_records, story.get("category")) for url in story.get("source_urls", []))
        why = story_why_it_matters(story)
        items.append(
            f"""<article class="dispatch-story">
<h3>{html.escape(story["title"])}</h3>
<p>{html.escape(story["summary"])}</p>
<p><strong>Why it matters:</strong> {html.escape(why)}</p>
{source_blocks}
</article>"""
        )
    return f"<h2>{html.escape(category)}</h2>\n" + "\n".join(items)


def story_why_it_matters(story: dict[str, Any]) -> str:
    value = " ".join(str(story.get("why_it_matters") or "").split())
    if value:
        return value
    records = [record for record in story.get("source_records", []) if isinstance(record, dict)]
    return why_it_matters(records[0] if records else {}, story.get("category"))


def matching_source_record(url: str, source_records: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            record
            for record in source_records
            if (record.get("canonical_url") or record.get("source_url") or record.get("url")) == url
        ),
        source_records[0] if source_records else {},
    )


def render_source_metadata(url: str, source_records: list[dict[str, Any]], category: str | None = None) -> str:
    record = matching_source_record(url, source_records)
    label = source_link_label(url, source_records)
    published = format_public_date(record.get("published_at"))
    category_text = " ".join(str(category or record.get("category_hint") or "").split())
    lines = [
        f'<p>Source: <a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a></p>'
    ]
    if published:
        lines.append(f"<p>Published: {html.escape(published)}</p>")
    if category_text:
        lines.append(f"<p>Category: {html.escape(category_text)}</p>")
    return '<div class="source-meta">' + "\n".join(lines) + "</div>"


def render_source_link(url: str, source_records: list[dict[str, Any]]) -> str:
    label = source_link_label(url, source_records)
    return f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a></li>'


def source_link_label(url: str, source_records: list[dict[str, Any]]) -> str:
    matching = matching_source_record(url, source_records)
    publisher = display_publisher(" ".join(str(matching.get("publisher") or matching.get("source_name") or "").split()))
    title = " ".join(str(matching.get("title") or matching.get("source_title") or "").split())
    if publisher and title:
        return f"{publisher} - {title}"
    if publisher:
        return publisher
    domain = domain_from_url(url)
    return domain or url


def format_public_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
        if not match:
            return ""
        parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def display_publisher(value: str) -> str:
    domain = value.lower().removeprefix("www.")
    known = {
        "seattletimes.com": "Seattle Times",
        "opb.org": "OPB",
        "crosscut.com": "Cascade PBS Crosscut",
        "kuow.org": "KUOW",
        "boisestatepublicradio.org": "Boise State Public Radio",
        "idahostatesman.com": "Idaho Statesman",
        "oregonlive.com": "OregonLive",
        "spokesman.com": "The Spokesman-Review",
    }
    return known.get(domain, value)


def domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlsplit

        return urlsplit(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def public_story_states(stories: list[dict[str, Any]]) -> list[str]:
    states: list[str] = []
    for story in stories:
        for record in story.get("source_records", []) or []:
            if not isinstance(record, dict):
                continue
            state = str(record.get("state_hint") or record.get("region_scope") or "").strip()
            if state in {"WA", "OR", "ID"} and state not in states:
                states.append(state)
    return states


def public_story_publishers(stories: list[dict[str, Any]]) -> list[str]:
    publishers: list[str] = []
    for story in stories:
        for record in story.get("source_records", []) or []:
            if not isinstance(record, dict):
                continue
            publisher = display_publisher(" ".join(str(record.get("publisher") or record.get("source_name") or "").split()))
            if publisher and publisher not in publishers:
                publishers.append(publisher)
    return publishers


def public_story_categories(stories: list[dict[str, Any]]) -> list[str]:
    categories: list[str] = []
    for story in stories:
        category = " ".join(str(story.get("category") or "").split())
        if category and category not in categories:
            categories.append(category)
    return categories


def sentence_join(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def build_weekly_summary_bullets(stories: list[dict[str, Any]]) -> list[str]:
    if not stories:
        return []
    categories = public_story_categories(stories)
    states = public_story_states(stories)
    publishers = public_story_publishers(stories)
    bullets: list[str] = []
    if categories and states:
        bullets.append(f"{sentence_join(categories[:3])} appeared in {sentence_join(states)} source records.")
    elif categories:
        bullets.append(f"{sentence_join(categories[:3])} appeared in this week's public source records.")
    if publishers:
        bullets.append(f"This edition includes source-backed items from {sentence_join(publishers[:3])}.")
    bullets.append(f"{len(stories)} public source-backed {'story' if len(stories) == 1 else 'stories'} met the current public-systems criteria.")
    return bullets[:4]


def render_weekly_summary(bullets: list[str]) -> str:
    if not bullets:
        return ""
    items = "\n".join(f"<li>{html.escape(bullet)}</li>" for bullet in bullets)
    return f"<h2>This week's signals</h2>\n<ul>{items}</ul>"


def parse_simple_yaml_map(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    section: str | None = None
    child: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        if not raw_line.startswith(" "):
            if ":" in raw_line:
                key = raw_line.split(":", 1)[0].strip()
                section = key
                result.setdefault(section, {})
                child = None
            continue
        stripped = raw_line.strip()
        if raw_line.startswith("  ") and stripped.endswith(":") and not raw_line.startswith("    "):
            child = stripped[:-1].strip()
            if section:
                result.setdefault(section, {}).setdefault(child, {})
            continue
        if raw_line.startswith("    ") and ":" in stripped and section and child:
            key, value = stripped.split(":", 1)
            value_text = value.strip().strip("\"'")
            lowered = value_text.lower()
            parsed: Any = value_text
            if lowered in {"true", "false"}:
                parsed = lowered == "true"
            else:
                try:
                    parsed = float(value_text)
                except ValueError:
                    parsed = value_text
            result[section][child][key.strip()] = parsed
    return result


def load_map_locations(root: Path) -> dict[str, Any]:
    path = root / MAP_LOCATIONS_PATH
    payload: dict[str, Any] = {
        "state_centroids": dict(DEFAULT_MAP_LOCATIONS["state_centroids"]),
        "source_defaults": dict(DEFAULT_MAP_LOCATIONS["source_defaults"]),
        "place_defaults": dict(DEFAULT_MAP_LOCATIONS["place_defaults"]),
    }
    if not path.exists():
        return payload
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text) or {}
        if isinstance(loaded, dict):
            payload["state_centroids"].update(loaded.get("state_centroids") or {})
            payload["source_defaults"].update(loaded.get("source_defaults") or {})
            payload["place_defaults"].update(loaded.get("place_defaults") or {})
            return payload
    except Exception:
        pass
    loaded = parse_simple_yaml_map(text)
    payload["state_centroids"].update(loaded.get("state_centroids") or {})
    payload["source_defaults"].update(loaded.get("source_defaults") or {})
    payload["place_defaults"].update(loaded.get("place_defaults") or {})
    return payload


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_source_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
        if not match:
            return None
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)


def is_within_coverage_window(published_at: Any, coverage_start: str | None, coverage_end: str | None) -> bool:
    if not (coverage_start and coverage_end):
        return True
    published = parse_source_datetime(published_at)
    if not published:
        return False
    start_dt = parse_source_datetime(coverage_start)
    end_dt = parse_source_datetime(coverage_end)
    if not start_dt or not end_dt:
        return True
    end_inclusive = end_dt.replace(hour=23, minute=59, second=59)
    return start_dt <= published <= end_inclusive


def normalize_public_pressure(
    category_hint: Any,
    title: Any,
    summary: Any,
) -> tuple[str | None, bool]:
    category_text = str(category_hint or "").strip().lower()
    if category_text in PRESSURE_LABELS:
        return PRESSURE_LABELS[category_text], False
    haystack = f"{str(title or '').lower()} {str(summary or '').lower()} {category_text}"
    for label, terms in PRESSURE_KEYWORDS.items():
        if any(term in haystack for term in terms):
            return label, True
    return None, False


def infer_state_code(source: dict[str, Any]) -> str:
    state = str(source.get("state_hint") or source.get("region_scope") or source.get("geography") or "").strip().upper()
    if state in {"WASHINGTON", "WA"}:
        return "WA"
    if state in {"OREGON", "OR"}:
        return "OR"
    if state in {"IDAHO", "ID"}:
        return "ID"
    return ""


def extract_local_location(source: dict[str, Any], story: dict[str, Any] | None = None) -> dict[str, str]:
    address = str(source.get("address") or source.get("address_line") or "").strip()
    facility = str(source.get("facility_name") or source.get("location_name") or "").strip()
    place = str(source.get("geography") or source.get("place") or "").strip()
    title = str((story or {}).get("title") or source.get("title") or source.get("source_title") or "").strip()
    summary = str((story or {}).get("summary") or source.get("summary_or_snippet") or source.get("text") or "").strip()
    body = str(source.get("article_text") or source.get("body_text") or source.get("content") or "").strip()
    source_meta = str(source.get("publisher") or source.get("source_name") or source.get("source_id") or "").strip()
    source_url = str(source.get("url") or source.get("source_url") or "").strip()
    source_path = source_url.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    search_text = " ".join([title, summary, body, source_meta, source_path])
    if address:
        return {"place": place or address, "facility": facility, "address": address, "location_precision": "address", "field": "address"}
    if facility:
        return {"place": place or facility, "facility": facility, "address": "", "location_precision": "facility", "field": "facility"}
    if place and place.lower() not in PLACE_STOPWORDS:
        return {"place": place, "facility": "", "address": "", "location_precision": ("county" if "county" in place.lower() else "city"), "field": "geography"}
    lowered = search_text.lower()
    for entity in LOCAL_ENTITY_HINTS:
        if entity.lower() in lowered:
            alias = ENTITY_PLACE_ALIASES.get(entity.lower())
            if alias:
                return {"place": alias[0], "facility": entity, "address": "", "location_precision": alias[1], "field": "entity_hint"}
            return {"place": entity, "facility": entity, "address": "", "location_precision": "facility", "field": "entity_hint"}
    for pattern, precision in LOCAL_PLACE_PATTERNS:
        match = re.search(pattern, search_text)
        if match:
            term = re.sub(r"\s+", " ", search_text[match.start():match.end()].strip())
            return {"place": term, "facility": (term if precision == "facility" else ""), "address": "", "location_precision": precision, "field": "pattern"}
    for city in LOCAL_CITY_HINTS:
        if re.search(rf"\b{re.escape(city)}\b", search_text, flags=re.IGNORECASE):
            return {"place": city, "facility": "", "address": "", "location_precision": "city", "field": "city_hint"}
    for county in LOCAL_COUNTY_HINTS:
        if re.search(rf"\b{re.escape(county)}\b", search_text, flags=re.IGNORECASE):
            return {"place": county, "facility": "", "address": "", "location_precision": "county", "field": "county_hint"}
    for area in LOCAL_SERVICE_AREA_HINTS:
        if re.search(rf"\b{re.escape(area)}\b", search_text, flags=re.IGNORECASE):
            return {"place": area, "facility": "", "address": "", "location_precision": "regional", "field": "service_area_hint"}
    state = infer_state_code(source)
    return {"place": state or "regional", "facility": "", "address": "", "location_precision": "statewide", "field": "none"}


def map_exclusion_reason_for_record(
    source_url: str,
    title: str,
    place: str,
    published_at: Any,
    coverage_start: str | None,
    coverage_end: str | None,
) -> str | None:
    lower_url = source_url.lower()
    lower_title = title.lower()
    if not parse_source_datetime(published_at):
        return "no_source_date"
    if not is_within_coverage_window(published_at, coverage_start, coverage_end):
        return "outside_report_window"
    if any(term in lower_title for term in GENERIC_LANDING_TERMS) or "/category/" in lower_url:
        return "generic_landing_page"
    if any(term in lower_title for term in ("currently selected", "alerts & emergencies")):
        return "stale_reference_page"
    return None


def resolve_marker_coordinates(
    source: dict[str, Any],
    map_locations: dict[str, Any],
    extracted_place: str | None = None,
    extracted_facility: str | None = None,
) -> tuple[float | None, float | None, str]:
    explicit_lat = _float_or_none(source.get("lat"))
    explicit_lon = _float_or_none(source.get("lon"))
    if explicit_lat is None or explicit_lon is None:
        explicit_lat = _float_or_none(source.get("latitude"))
        explicit_lon = _float_or_none(source.get("longitude"))
    if explicit_lat is not None and explicit_lon is not None:
        return explicit_lat, explicit_lon, "explicit"

    source_defaults = map_locations.get("source_defaults") if isinstance(map_locations.get("source_defaults"), dict) else {}
    source_keys = [
        str(source.get("source_id") or "").strip(),
        str(source.get("provider_name") or "").strip(),
        str(source.get("publisher") or "").strip(),
    ]
    for key in source_keys:
        if not key:
            continue
        candidate = source_defaults.get(key)
        if isinstance(candidate, dict):
            lat = _float_or_none(candidate.get("lat"))
            lon = _float_or_none(candidate.get("lon"))
            if lat is not None and lon is not None:
                return lat, lon, "source_default"
    place_defaults = map_locations.get("place_defaults") if isinstance(map_locations.get("place_defaults"), dict) else {}
    place_keys = [
        str(extracted_place or "").strip(),
        str(extracted_facility or "").strip(),
        str(source.get("place") or source.get("geography") or source.get("location_name") or "").strip(),
    ]
    normalized_place_defaults = {str(key).strip().lower(): value for key, value in place_defaults.items()}
    for key in place_keys:
        if not key:
            continue
        candidate = normalized_place_defaults.get(key.lower())
        if isinstance(candidate, dict):
            lat = _float_or_none(candidate.get("lat"))
            lon = _float_or_none(candidate.get("lon"))
            if lat is not None and lon is not None:
                return lat, lon, "place_lookup"

    state_centroids = map_locations.get("state_centroids") if isinstance(map_locations.get("state_centroids"), dict) else {}
    state = str(source.get("state_hint") or source.get("region_scope") or source.get("geography") or "").strip().upper()
    if state in {"WASHINGTON", "WA"}:
        state = "WA"
    elif state in {"OREGON", "OR"}:
        state = "OR"
    elif state in {"IDAHO", "ID"}:
        state = "ID"
    centroid = state_centroids.get(state) if state else None
    if isinstance(centroid, dict):
        lat = _float_or_none(centroid.get("lat"))
        lon = _float_or_none(centroid.get("lon"))
        if lat is not None and lon is not None:
            return lat, lon, "state_centroid"
    return None, None, "missing"


def build_cascadia_map_markers(
    public_curation: list[dict[str, Any]],
    sources_manifest: list[dict[str, Any]],
    map_locations: dict[str, Any],
    coverage_start: str | None = None,
    coverage_end: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "raw_candidate_count": 0,
        "accepted_local_marker_count": 0,
        "regional_statewide_report_count": 0,
        "excluded_count": 0,
        "excluded_reasons": {},
        "category_corrections_applied": 0,
        "stale_records_removed": 0,
        "state_only_place_excluded": 0,
        "local_extraction_success_count": 0,
        "local_extraction_failure_count": 0,
        "local_extraction_success_rate": 0.0,
        "unresolved_category_count": 0,
        "category_resolution_success_count": 0,
        "extraction_fields_success": {},
        "extraction_fields_failed": {},
        "place_extraction_attempted": 0,
        "place_extraction_succeeded": 0,
        "place_match_by_type": {},
        "candidate_diagnostics_rows": [],
    }
    by_source_record_id = {
        str(source.get("source_record_id")): source
        for source in sources_manifest
        if isinstance(source, dict) and source.get("source_record_id")
    }
    markers: list[dict[str, Any]] = []
    regional_reports: list[dict[str, Any]] = []
    excluded_reasons: Counter[str] = Counter()
    extraction_success: Counter[str] = Counter()
    extraction_failed: Counter[str] = Counter()
    candidate_rows: list[dict[str, Any]] = []
    place_match_by_type: Counter[str] = Counter()
    place_extraction_attempted = 0
    place_extraction_succeeded = 0
    for story in public_curation:
        if not story.get("included_in_public_summary"):
            continue
        if not story.get("title") or not story.get("category"):
            warnings.append(f"map skipped story missing title/category: {story.get('story_id')}")
            continue
        source_ids = [str(item) for item in story.get("source_record_ids", []) if item]
        linked_sources = [by_source_record_id[item] for item in source_ids if item in by_source_record_id]
        if not linked_sources:
            warnings.append(f"map skipped story with no linked source records: {story.get('story_id')}")
            continue
        source = linked_sources[0]
        diagnostics["raw_candidate_count"] += 1
        source_url = str(source.get("url") or source.get("source_url") or "").strip()
        if not source_url or not source_url.startswith(("http://", "https://")):
            warnings.append(f"map skipped story missing source URL: {story.get('story_id')}")
            continue
        state = infer_state_code(source)
        region = state
        if not region:
            warnings.append(f"map skipped story missing state/region: {story.get('story_id')}")
            continue
        extracted = extract_local_location(source, story)
        diagnostics["place_extraction_attempted"] += 1
        extraction_field = str(extracted.get("field") or "none")
        if extraction_field == "none":
            extraction_failed[extraction_field] += 1
            diagnostics["local_extraction_failure_count"] += 1
        else:
            extraction_success[extraction_field] += 1
            diagnostics["local_extraction_success_count"] += 1
            diagnostics["place_extraction_succeeded"] += 1
        place_match_by_type: Counter[str] = Counter(diagnostics.get("place_match_by_type") or {})
        place_match_by_type[extraction_field] += 1
        diagnostics["place_match_by_type"] = dict(place_match_by_type)
        lat, lon, coordinate_basis = resolve_marker_coordinates(
            source,
            map_locations,
            extracted_place=str(extracted.get("place") or ""),
            extracted_facility=str(extracted.get("facility") or ""),
        )
        if lat is None or lon is None:
            warnings.append(f"map skipped story missing coordinate fallback: {story.get('story_id')}")
            diagnostics["candidate_diagnostics_rows"].append(
                {
                    "title": str(story.get("title") or source.get("title") or ""),
                    "source_url": source_url,
                    "publisher": str(source.get("publisher") or ""),
                    "detected_pressure_category": str(story.get("category") or source.get("category_hint") or ""),
                    "detected_place_candidates": [str(extracted.get("place") or "")],
                    "selected_place": "",
                    "selected_precision": "",
                    "coordinate_basis": "missing",
                    "inclusion_decision": "excluded",
                    "excluded_reason": "missing_coordinate_fallback",
                }
            )
            continue
        category_text = str(story.get("category") or source.get("category_hint") or "").strip()
        public_pressure_label, corrected = normalize_public_pressure(category_text, story.get("title"), story.get("summary"))
        if corrected:
            diagnostics["category_corrections_applied"] += 1
        if not public_pressure_label:
            excluded_reasons["unresolved_pressure_category"] += 1
            diagnostics["unresolved_category_count"] += 1
            continue
        diagnostics["category_resolution_success_count"] += 1
        what_found = str(story.get("summary") or "").strip() or "Source-backed local development."
        marker_scope = "statewide" if coordinate_basis == "state_centroid" else "local"
        if coordinate_basis == "source_default":
            marker_scope = "county"
        place = str(extracted.get("place") or source.get("geography") or source.get("place") or source.get("region_scope") or state).strip() or state
        regional_area = REGIONAL_AREAS.get(state, state)
        source_title = str(source.get("title") or source.get("source_title") or "").strip()
        address = str(extracted.get("address") or source.get("address") or source.get("address_line") or "").strip()
        facility = str(extracted.get("facility") or source.get("facility_name") or source.get("location_name") or "").strip()
        location_label = address or facility or place
        precision_raw = str(source.get("location_precision") or source.get("precision") or "").strip().lower()
        if precision_raw in {"address", "address_level"}:
            location_precision = "address"
        elif precision_raw in {"facility", "facility_level"}:
            location_precision = "facility"
        elif precision_raw in {"city", "city_level"}:
            location_precision = "city"
        elif precision_raw in {"county", "county_level", "service_area"}:
            location_precision = "county"
        elif precision_raw in {"state", "state_level", "statewide"}:
            location_precision = "statewide"
        elif precision_raw == "regional":
            location_precision = "regional"
        elif address:
            location_precision = "address"
        elif facility:
            location_precision = "facility"
        elif extracted.get("location_precision"):
            location_precision = str(extracted.get("location_precision"))
        elif marker_scope == "statewide":
            location_precision = "statewide"
        elif "county" in place.lower():
            location_precision = "county"
        elif any(term in place.lower() for term in ("region", "metro", "cascadia", "pacific northwest")):
            location_precision = "regional"
        elif marker_scope == "county":
            location_precision = "county"
        else:
            location_precision = "city"
        exclusion = map_exclusion_reason_for_record(
            source_url=source_url,
            title=str(source_title or story.get("title") or ""),
            place=place,
            published_at=source.get("published_at"),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        if exclusion:
            excluded_reasons[exclusion] += 1
            if exclusion == "outside_report_window":
                diagnostics["stale_records_removed"] += 1
            if exclusion == "no_specific_place":
                diagnostics["state_only_place_excluded"] += 1
            diagnostics["candidate_diagnostics_rows"].append(
                {
                    "title": str(story.get("title") or source_title or ""),
                    "source_url": source_url,
                    "publisher": str(source.get("publisher") or ""),
                    "detected_pressure_category": public_pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(source.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": location_precision,
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": exclusion,
                }
            )
            continue
        if coordinate_basis == "state_centroid" and location_precision not in {"statewide", "regional"}:
            location_precision = "statewide"
        if place.strip().upper() in {"WA", "OR", "ID"} and location_precision in {"city", "county"}:
            excluded_reasons["no_specific_place"] += 1
            diagnostics["state_only_place_excluded"] += 1
            diagnostics["candidate_diagnostics_rows"].append(
                {
                    "title": str(story.get("title") or source_title or ""),
                    "source_url": source_url,
                    "publisher": str(source.get("publisher") or ""),
                    "detected_pressure_category": public_pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(source.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": location_precision,
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": "no_specific_place",
                }
            )
            continue
        if location_precision in {"statewide", "regional"}:
            excluded_reasons["statewide_not_default_layer"] += 1
            regional_reports.append(
                {
                    "story_id": story.get("story_id"),
                    "title": source_title or story.get("title"),
                    "category": story.get("category") or public_pressure_label,
                    "pressure_type": public_pressure_label,
                    "place": place,
                    "state": state,
                    "state_or_region": state,
                    "region_label": regional_area,
                    "regional_area": regional_area,
                    "publisher": source.get("publisher"),
                    "published_at": source.get("published_at"),
                    "source_url": source_url,
                    "what_we_found": what_found,
                    "why_it_matters": story_why_it_matters(story),
                    "read_more": f"/cascadia/editions/{story.get('edition_date') or ''}/" if story.get("edition_date") else "/cascadia/",
                    "read_more_label": str(story.get("title") or "Related Cascadia section"),
                    "location_label": location_label,
                    "address": address or None,
                    "location_precision": location_precision,
                    "precision_note": LOCATION_PRECISION_NOTES.get(location_precision, "Statewide report."),
                    "lat": lat,
                    "lon": lon,
                    "coordinate_basis": coordinate_basis,
                }
            )
            diagnostics["candidate_diagnostics_rows"].append(
                {
                    "title": str(story.get("title") or source_title or ""),
                    "source_url": source_url,
                    "publisher": str(source.get("publisher") or ""),
                    "detected_pressure_category": public_pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(source.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": location_precision,
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "regional_report",
                    "excluded_reason": "",
                }
            )
            continue
        dispatch_link = str(story.get("edition_date") or "").strip()
        if dispatch_link:
            dispatch_link = f"/cascadia/editions/{dispatch_link}/"
        else:
            dispatch_link = "/cascadia/"
        markers.append(
            {
                "story_id": story.get("story_id"),
                "title": source_title or story.get("title"),
                "category": story.get("category"),
                "pressure_type": public_pressure_label,
                "place": place,
                "state": state,
                "state_or_region": state,
                "region_label": regional_area,
                "regional_area": regional_area,
                "publisher": source.get("publisher"),
                "published_at": source.get("published_at"),
                "source_url": source_url,
                "source_record_id": source.get("source_record_id"),
                "what_we_found": what_found,
                "why_it_matters": story_why_it_matters(story),
                "read_more": dispatch_link,
                "read_more_label": str(story.get("title") or "Related Cascadia section"),
                "location_label": location_label,
                "address": address or None,
                "location_precision": location_precision,
                "precision_note": LOCATION_PRECISION_NOTES.get(location_precision, "Mapped to city level."),
                "marker_scope": marker_scope,
                "lat": lat,
                "lon": lon,
                "coordinate_basis": coordinate_basis,
            }
        )
        diagnostics["candidate_diagnostics_rows"].append(
            {
                "title": str(story.get("title") or source_title or ""),
                "source_url": source_url,
                "publisher": str(source.get("publisher") or ""),
                "detected_pressure_category": public_pressure_label or category_text,
                "detected_place_candidates": [str(extracted.get("place") or ""), str(source.get("geography") or "")],
                "selected_place": place,
                "selected_precision": location_precision,
                "coordinate_basis": coordinate_basis,
                "inclusion_decision": "local_marker",
                "excluded_reason": "",
            }
        )
    diagnostics["accepted_local_marker_count"] = len(markers)
    diagnostics["regional_statewide_report_count"] = len(regional_reports)
    diagnostics["excluded_count"] = int(sum(excluded_reasons.values()))
    diagnostics["excluded_reasons"] = dict(excluded_reasons)
    total_extraction = diagnostics["local_extraction_success_count"] + diagnostics["local_extraction_failure_count"]
    diagnostics["local_extraction_success_rate"] = round(
        diagnostics["local_extraction_success_count"] / total_extraction,
        4,
    ) if total_extraction else 0.0
    diagnostics["extraction_fields_success"] = dict(extraction_success)
    diagnostics["extraction_fields_failed"] = dict(extraction_failed)
    return markers, regional_reports, warnings, diagnostics


def build_backfill_map_markers(
    records: list[dict[str, Any]],
    map_locations: dict[str, Any],
    coverage_start: str | None,
    coverage_end: str | None,
    edition_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, int]]:
    warnings: list[str] = []
    markers: list[dict[str, Any]] = []
    regional_reports: list[dict[str, Any]] = []
    excluded_reasons: Counter[str] = Counter()
    category_corrections_applied = 0
    extraction_success: Counter[str] = Counter()
    extraction_failed: Counter[str] = Counter()
    candidate_rows: list[dict[str, Any]] = []
    place_match_by_type: Counter[str] = Counter()
    place_extraction_attempted = 0
    place_extraction_succeeded = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        source_url = str(record.get("url") or record.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            continue
        state = infer_state_code(record)
        if state not in {"WA", "OR", "ID"}:
            warnings.append(f"map excluded backfill record with unknown state: {record.get('source_record_id')}")
            continue
        extracted = extract_local_location(record)
        place_extraction_attempted += 1
        extraction_field = str(extracted.get("field") or "none")
        place_match_by_type[extraction_field] += 1
        if extraction_field == "none":
            extraction_failed[extraction_field] += 1
        else:
            extraction_success[extraction_field] += 1
            place_extraction_succeeded += 1
        lat, lon, coordinate_basis = resolve_marker_coordinates(
            record,
            map_locations,
            extracted_place=str(extracted.get("place") or ""),
            extracted_facility=str(extracted.get("facility") or ""),
        )
        if lat is None or lon is None:
            warnings.append(f"map excluded backfill record missing coordinates: {record.get('source_record_id')}")
            candidate_rows.append(
                {
                    "title": str(record.get("title") or ""),
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": str(record.get("category_hint") or ""),
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": "",
                    "selected_precision": "",
                    "coordinate_basis": "missing",
                    "inclusion_decision": "excluded",
                    "excluded_reason": "missing_coordinate_fallback",
                }
            )
            continue
        marker_scope = "statewide" if coordinate_basis == "state_centroid" else ("county" if coordinate_basis == "source_default" else "local")
        category_text = str(record.get("category_hint") or "").strip()
        pressure_label, corrected = normalize_public_pressure(category_text, record.get("title"), record.get("summary_or_snippet"))
        if corrected:
            category_corrections_applied += 1
        if not pressure_label:
            excluded_reasons["unresolved_pressure_category"] += 1
            candidate_rows.append(
                {
                    "title": str(record.get("title") or ""),
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": "",
                    "selected_precision": "",
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": "unresolved_pressure_category",
                }
            )
            continue
        place = str(extracted.get("place") or record.get("geography") or record.get("place") or state).strip() or state
        title = str(record.get("title") or record.get("source_title") or "Source-backed pressure record").strip()
        summary = str(record.get("summary_or_snippet") or title).strip()
        exclusion = map_exclusion_reason_for_record(
            source_url=source_url,
            title=title,
            place=place,
            published_at=record.get("published_at"),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        if exclusion:
            excluded_reasons[exclusion] += 1
            candidate_rows.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": "",
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": exclusion,
                }
            )
            continue
        if any(token in source_url.lower() for token in ("/category/", "/alerts", "/public-safety")) and "2026" not in source_url:
            excluded_reasons["stale_reference_page"] += 1
            candidate_rows.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": "",
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": "stale_reference_page",
                }
            )
            continue
        address = str(extracted.get("address") or record.get("address") or record.get("address_line") or "").strip()
        facility = str(extracted.get("facility") or record.get("facility_name") or record.get("location_name") or "").strip()
        location_label = address or facility or place
        precision_raw = str(record.get("location_precision") or record.get("precision") or "").strip().lower()
        if precision_raw in {"address", "address_level"}:
            location_precision = "address"
        elif precision_raw in {"facility", "facility_level"}:
            location_precision = "facility"
        elif precision_raw in {"city", "city_level"}:
            location_precision = "city"
        elif precision_raw in {"county", "county_level", "service_area"}:
            location_precision = "county"
        elif precision_raw in {"state", "state_level", "statewide"}:
            location_precision = "statewide"
        elif precision_raw == "regional":
            location_precision = "regional"
        elif address:
            location_precision = "address"
        elif facility:
            location_precision = "facility"
        elif extracted.get("location_precision"):
            location_precision = str(extracted.get("location_precision"))
        elif marker_scope == "statewide":
            location_precision = "statewide"
        elif "county" in place.lower():
            location_precision = "county"
        elif any(term in place.lower() for term in ("region", "metro", "cascadia", "pacific northwest")):
            location_precision = "regional"
        elif marker_scope == "county":
            location_precision = "county"
        else:
            location_precision = "city"
        if coordinate_basis == "state_centroid" and location_precision not in {"statewide", "regional"}:
            location_precision = "statewide"
        if place.strip().upper() in {"WA", "OR", "ID"} and location_precision in {"city", "county"}:
            excluded_reasons["no_specific_place"] += 1
            candidate_rows.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": location_precision,
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "excluded",
                    "excluded_reason": "no_specific_place",
                }
            )
            continue
        if location_precision in {"statewide", "regional"}:
            excluded_reasons["statewide_not_default_layer"] += 1
            regional_reports.append(
                {
                    "story_id": f"map-{record.get('source_record_id') or (source_url + title).encode('utf-8').hex()[:16]}",
                    "title": title,
                    "category": record.get("category_hint") or pressure_label,
                    "pressure_type": pressure_label,
                    "place": place,
                    "state": state,
                    "state_or_region": state,
                    "region_label": REGIONAL_AREAS.get(state, state),
                    "regional_area": REGIONAL_AREAS.get(state, state),
                    "publisher": record.get("publisher") or record.get("source_name") or "Source",
                    "published_at": record.get("published_at"),
                    "source_url": source_url,
                    "source_record_id": record.get("source_record_id"),
                    "what_we_found": summary,
                    "why_it_matters": f"In {state}, this report signals pressure on local services, household stability, or public infrastructure.",
                    "read_more": f"/cascadia/editions/{edition_date}/",
                    "read_more_label": title,
                    "location_label": location_label,
                    "address": address or None,
                    "location_precision": location_precision,
                    "precision_note": LOCATION_PRECISION_NOTES.get(location_precision, "Statewide report."),
                    "marker_scope": marker_scope,
                    "lat": lat,
                    "lon": lon,
                    "coordinate_basis": coordinate_basis,
                    "map_source": "backfill_record",
                    "coverage_start": coverage_start,
                    "coverage_end": coverage_end,
                }
            )
            candidate_rows.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "publisher": str(record.get("publisher") or ""),
                    "detected_pressure_category": pressure_label or category_text,
                    "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                    "selected_place": place,
                    "selected_precision": location_precision,
                    "coordinate_basis": coordinate_basis,
                    "inclusion_decision": "regional_report",
                    "excluded_reason": "",
                }
            )
            continue
        markers.append(
            {
                "story_id": f"map-{record.get('source_record_id') or (source_url + title).encode('utf-8').hex()[:16]}",
                "title": title,
                "category": record.get("category_hint") or pressure_label,
                "pressure_type": pressure_label,
                "place": place,
                "state": state,
                "state_or_region": state,
                "region_label": REGIONAL_AREAS.get(state, state),
                "regional_area": REGIONAL_AREAS.get(state, state),
                "publisher": record.get("publisher") or record.get("source_name") or "Source",
                "published_at": record.get("published_at"),
                "source_url": source_url,
                "source_record_id": record.get("source_record_id"),
                "what_we_found": summary,
                "why_it_matters": f"In {state}, this report signals pressure on local services, household stability, or public infrastructure.",
                "read_more": f"/cascadia/editions/{edition_date}/",
                "read_more_label": title,
                "location_label": location_label,
                "address": address or None,
                "location_precision": location_precision,
                "precision_note": LOCATION_PRECISION_NOTES.get(location_precision, "Mapped to city level."),
                "marker_scope": marker_scope,
                "lat": lat,
                "lon": lon,
                "coordinate_basis": coordinate_basis,
                "map_source": "backfill_record",
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
            }
        )
        candidate_rows.append(
            {
                "title": title,
                "source_url": source_url,
                "publisher": str(record.get("publisher") or ""),
                "detected_pressure_category": pressure_label or category_text,
                "detected_place_candidates": [str(extracted.get("place") or ""), str(record.get("geography") or "")],
                "selected_place": place,
                "selected_precision": location_precision,
                "coordinate_basis": coordinate_basis,
                "inclusion_decision": "local_marker",
                "excluded_reason": "",
            }
        )
    payload = dict(excluded_reasons)
    payload["_category_corrections_applied"] = int(category_corrections_applied)
    payload["_extraction_fields_success"] = dict(extraction_success)
    payload["_extraction_fields_failed"] = dict(extraction_failed)
    payload["_candidate_rows"] = candidate_rows
    payload["_place_match_by_type"] = dict(place_match_by_type)
    payload["_place_extraction_attempted"] = int(place_extraction_attempted)
    payload["_place_extraction_succeeded"] = int(place_extraction_succeeded)
    return markers, regional_reports, warnings, payload


def dedupe_map_markers(markers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    duplicates = 0
    for marker in markers:
        key = (
            str(marker.get("source_url") or "").strip().lower(),
            str(marker.get("place") or marker.get("regional_area") or "").strip().lower(),
            str(marker.get("pressure_type") or "").strip().lower(),
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(marker)
    return deduped, duplicates


def build_source_density_diagnostics(
    local_markers: list[dict[str, Any]],
    regional_reports: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    excluded_reasons: dict[str, Any],
) -> dict[str, Any]:
    def source_key(row: dict[str, Any]) -> str:
        publisher = str(row.get("publisher") or "").strip()
        if publisher:
            return publisher
        source_url = str(row.get("source_url") or "").strip()
        if source_url:
            parsed = urlparse(source_url)
            if parsed.netloc:
                return parsed.netloc
        return "Unknown source"

    local_counts: Counter[str] = Counter()
    regional_counts: Counter[str] = Counter()
    local_report_source_urls: set[str] = set()

    for marker in local_markers:
        local_counts[source_key(marker)] += 1
        source_url = str(marker.get("source_url") or "").strip()
        if source_url:
            local_report_source_urls.add(source_url)
    for report in regional_reports:
        regional_counts[source_key(report)] += 1

    no_local_source_counts: Counter[str] = Counter()
    for row in candidate_rows:
        if str(row.get("inclusion_decision") or "") == "local_marker":
            continue
        source_url = str(row.get("source_url") or "").strip()
        if source_url and source_url in local_report_source_urls:
            continue
        no_local_source_counts[source_key(row)] += 1

    sources_only_regional: list[dict[str, Any]] = []
    for key, count in regional_counts.items():
        if local_counts.get(key, 0) == 0:
            sources_only_regional.append({"source": key, "regional_reports": int(count)})
    sources_only_regional.sort(key=lambda row: row["regional_reports"], reverse=True)

    missing_place_reason_keys = (
        "no_specific_place",
        "missing_coordinate_fallback",
        "statewide_not_default_layer",
        "missing_coordinate",
    )
    missing_place_reasons = [
        {"reason": reason, "count": int(excluded_reasons.get(reason, 0))}
        for reason in missing_place_reason_keys
        if int(excluded_reasons.get(reason, 0)) > 0
    ]
    missing_place_reasons.sort(key=lambda row: row["count"], reverse=True)

    recommended_source_additions = [
        "County emergency-management pages with dated updates",
        "Transit and ferry alert/news feeds with service-area detail",
        "Local public radio and metro newsroom operations updates",
        "Utility outage and public health update pages with dated posts",
    ]

    top_local_sources = [{"source": key, "local_markers": int(count)} for key, count in local_counts.most_common(8)]
    sources_no_local_mappable = [{"source": key, "non_local_reports": int(count)} for key, count in no_local_source_counts.most_common(8)]

    return {
        "top_sources_local_markers": top_local_sources,
        "sources_only_regional_reports": sources_only_regional[:8],
        "sources_with_no_local_mappable_reports": sources_no_local_mappable,
        "top_missing_place_reasons": missing_place_reasons,
        "recommended_source_additions": recommended_source_additions,
    }


def render_map_html(edition_date: str, note: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cascadia Pressure Map - {html.escape(edition_date)}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" crossorigin="anonymous" referrerpolicy="no-referrer">
  <style>
    :root {{ --deep-blue:#15384a; --panel:#f8fcfc; --card:#ffffff; --card-edge:#d9e6ea; --header-bg:#1E3F4F; --header-primary:#EFE7DA; --header-secondary:#9BAEB5; }}
    html, body {{ height: 100%; margin: 0; font: 15px/1.45 "Segoe UI", Tahoma, sans-serif; background: #e8eff1; color:#13252f; }}
    .shell {{ height: 100%; display: flex; flex-direction: column; }}
    .resource-header {{ background:var(--header-bg); color:var(--header-secondary); padding:8px 12px 10px; border-bottom:1px solid #355564; text-align:center; }}
    .resource-branding {{ margin-top:2px; display:flex; flex-direction:column; align-items:center; gap:4px; }}
    .bluefern-icon-link img {{ width:30px; height:30px; border-radius:6px; display:block; }}
    .resource-header h1 {{ margin:2px 0 0; font-size:1rem; }}
    .map-title-accent {{ color:var(--header-primary); }}
    .resource-header p {{ margin:.16rem 0 0; color:var(--header-secondary); font-size:.8rem; }}
    .resource-header .date-range {{ margin-top:.2rem; font-size:.82rem; font-weight:700; color:var(--header-primary); }}
    .resource-header .source-note {{ color:var(--header-secondary); }}
    .header-actions {{ margin-top:6px; display:flex; justify-content:center; }}
    #resetMap {{ border:1px solid #7ea2b3; background:#f4fbff; color:#12384b; border-radius:8px; padding:6px 10px; cursor:pointer; font-size:13px; font-weight:700; }}
    #mapWrap {{ position: relative; flex: 1; min-height: 0; }}
    #map {{ height: 100%; width: 100%; }}
    .controls {{ position:absolute; right:14px; top:14px; z-index:930; display:grid; gap:6px; background:rgba(248,252,252,.96); padding:9px; border-radius:10px; border:1px solid #c8d7db; min-width:230px; box-shadow:0 6px 24px rgba(16,36,44,.08); }}
    .controls label {{ font-size:.72rem; color:#2a4c59; font-weight:700; }}
    .controls select {{ width:100%; border:1px solid #b6c9cf; background:#fff; color:#12364a; border-radius:8px; padding:6px 8px; cursor:pointer; font-size:13px; }}
    details.legend {{ position:absolute; right:14px; bottom:14px; z-index:900; background:var(--panel); border:1px solid #c8d7db; border-radius:10px; padding:8px 10px; width:260px; box-shadow:0 6px 24px rgba(16,36,44,.08); }}
    details.howto {{ position:absolute; left:14px; top:14px; z-index:900; background:var(--panel); border:1px solid #c8d7db; border-radius:10px; padding:8px 10px; width:300px; box-shadow:0 6px 24px rgba(16,36,44,.08); }}
    details.legend summary {{ cursor:pointer; font-weight:600; color:var(--deep-blue); }}
    details.howto summary {{ cursor:pointer; font-weight:600; color:var(--deep-blue); }}
    .tip {{ font-size:12px; color:#35515f; }}
    .legend-list {{ list-style:none; padding:0; margin:8px 0 0; display:grid; gap:6px; }}
    .legend-item {{ display:flex; align-items:center; gap:8px; font-size:12px; color:#24424f; }}
    .legend-dot {{ width:11px; height:11px; border-radius:999px; display:inline-block; border:1px solid rgba(20,38,49,.15); }}
    .leaflet-tooltip.region-tooltip {{ background:rgba(255,255,255,.94); color:#18323d; border:1px solid var(--card-edge); border-radius:12px; box-shadow:0 8px 20px rgba(15,40,52,.17); max-width:280px; white-space:normal; line-height:1.35; text-align:center; padding:7px 9px; font-size:12px; }}
    .leaflet-tooltip.region-tooltip .tt-place {{ display:block; font-size:13px; font-weight:700; margin-bottom:2px; color:#113243; }}
    .map-popup {{ font-size:14px; line-height:1.5; max-width:346px; color:#1b323b; background:var(--card); border:1px solid var(--card-edge); border-radius:14px; box-shadow:0 10px 24px rgba(15,40,52,.14); padding:2px; }}
    .leaflet-popup-content-wrapper {{ border-radius:14px; box-shadow:0 10px 24px rgba(15,40,52,.14); }}
    .leaflet-popup-content {{ margin:10px 11px; min-width:280px; max-width:346px; }}
    .map-popup .place {{ font-size:1.02rem; font-weight:800; line-height:1.2; margin:0 0 .45rem; color:#15384a; }}
    .map-popup .field {{ margin-top:.45rem; padding-top:.45rem; border-top:1px solid #e3ecef; word-break:break-word; overflow-wrap:anywhere; }}
    .map-popup .field:first-of-type {{ border-top:0; margin-top:0; padding-top:0; }}
    .map-popup strong {{ color:#15384a; display:block; margin-bottom:.08rem; letter-spacing:.01em; }}
    .map-popup ul {{ margin:.2rem 0 0 1rem; padding:0; }}
    .map-popup .toggle-row {{ margin-top:.58rem; }}
    .map-popup .toggle-button {{ border:1px solid #a9c3cd; background:#f7fcff; color:#1a4357; border-radius:8px; padding:5px 8px; font-size:12px; font-weight:600; cursor:pointer; }}
    .map-popup .reports-panel {{ margin-top:.58rem; border-top:1px solid #deeaee; padding-top:.56rem; display:none; }}
    .map-popup .reports-panel.open {{ display:block; }}
    .map-popup .reports-panel.scrollable {{ max-height:220px; overflow:auto; padding-right:4px; }}
    .map-popup .report-card {{ background:#fbfefe; border:1px solid #deeaee; border-radius:10px; padding:8px; margin-bottom:7px; box-shadow:0 1px 4px rgba(18,56,74,.06); }}
    .map-popup .report-headline {{ margin:0; font-size:.9rem; line-height:1.25; color:#12394a; font-weight:700; }}
    .map-popup .report-summary {{ margin:.26rem 0 .4rem; font-size:.81rem; color:#284754; line-height:1.35; }}
    .map-popup .report-meta {{ margin:.32rem 0 0; font-size:.8rem; color:#365563; }}
    .map-popup .report-meta strong {{ display:inline; margin:0; color:#1e4658; font-weight:700; }}
    .map-popup .report-link {{ margin-top:.34rem; display:inline-block; font-size:.8rem; color:#1d5a73; text-decoration:none; font-weight:600; }}
    .group-count {{ display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; border-radius:999px; color:#f4fbfd; font-weight:800; background:#4f829b; border:2px solid #ecf5f8; }}
    .regional-pill {{ display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; border-radius:999px; color:#1b2f3a; font-weight:800; background:#f7efe3; border:2px solid #6f4f2c; box-shadow:0 2px 8px rgba(25,32,40,.2); font-size:11px; letter-spacing:.4px; }}
    .local-pill {{ display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; border-radius:999px; color:#f4fbfd; font-weight:800; border:2px solid #ecf5f8; box-shadow:0 2px 8px rgba(25,32,40,.2); font-size:15px; }}
    .empty-state {{ position:absolute; left:14px; bottom:14px; z-index:905; background:rgba(248,252,252,.96); color:#2a4c59; border:1px solid #c8d7db; border-radius:10px; padding:10px 12px; font-size:12px; max-width:330px; box-shadow:0 6px 24px rgba(16,36,44,.08); display:none; }}
    @media (max-width: 900px) {{ .controls {{ left:14px; right:14px; top:auto; bottom:14px; min-width:0; grid-template-columns:1fr 1fr; }} details.legend {{ right:14px; bottom:210px; }} details.howto {{ left:14px; top:92px; right:14px; width:auto; }} .empty-state {{ bottom:210px; right:14px; max-width:none; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="resource-header">
      <div class="resource-branding">
        <a class="bluefern-icon-link" href="https://thebluefernco.com/" target="_blank" rel="noopener noreferrer"><img src="/assets/bluefern.ico" alt="Blue Fern icon"></a>
      </div>
      <h1 class="map-title-accent">Cascadia Public Pressure Map</h1>
      <p>{html.escape(note)}</p>
      <p class="date-range" id="dateRange">Reports shown: {html.escape(edition_date)}</p>
      <p class="source-note">Sources: public regional reporting and official/public sources</p>
      <div class="header-actions"><button id="resetMap">Reset Map</button></div>
    </header>
    <div id="mapWrap">
      <div class="controls">
        <label for="pressureFilter">Pressure area</label><select id="pressureFilter"><option value="">All pressure areas</option></select>
        <label for="stateFilter">State</label><select id="stateFilter"><option value="">All states</option></select>
        <label for="regionFilter">Region</label><select id="regionFilter"><option value="">All regions</option></select>
        <label for="timeFilter">Report window</label><select id="timeFilter"><option value="all">All shown dates</option><option value="7">Current completed week</option><option value="14">Current + previous week</option><option value="30">Last 30 days</option></select>
        <label for="viewMode">Map view</label><select id="viewMode"><option value="grouped">Grouped places</option><option value="individual">Individual reports</option></select>
        <label for="showRegional"><input type="checkbox" id="showRegional"> Show regional/statewide reports</label>
      </div>
      <details class="howto"><summary>How to read this map</summary><p class="tip">This is a regional systems weather map. Markers are source-backed public reports, not predictions.</p><p class="tip">Color and letter show pressure category. Grouped circles show multiple reports in one place.</p><p class="tip">Local markers are place-specific reports. Regional/statewide reports are a separate context layer.</p><p class="tip">This map is not a complete census or disaster map. It shows traceable weekly signals.</p></details>
      <details class="legend"><summary>Legend</summary><p class="tip">Icon markers show pressure categories. Grouped circles show multiple reports in one place.</p><ul class="legend-list"><li class="legend-item"><span class="legend-dot" style="background:#B08A57;"></span>Housing and utility pressure</li><li class="legend-item"><span class="legend-dot" style="background:#5D8793;"></span>Health care access</li><li class="legend-item"><span class="legend-dot" style="background:#3F5878;"></span>Jobs and local economy</li><li class="legend-item"><span class="legend-dot" style="background:#5D7F62;"></span>Food and household support</li><li class="legend-item"><span class="legend-dot" style="background:#5B6F8A;"></span>Transportation and access</li><li class="legend-item"><span class="legend-dot" style="background:#9A5A4A;"></span>Public safety and emergency services</li><li class="legend-item"><span class="legend-dot" style="background:#B6784F;"></span>Wildfire, drought, flood, and recovery</li><li class="legend-item"><span class="legend-dot" style="background:#6D6287;"></span>Schools and local government services</li></ul></details>
      <div id="emptyState" class="empty-state">No reports match the current map filters. Reset Map to restore the default view.</div>
      <div id="renderWarning" class="empty-state">Some map markers could not be displayed.</div>
      <div id="map"></div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const DEFAULT_CENTER = [45.8, -120.5];
    const DEFAULT_ZOOM = 5;
    const map = L.map('map').setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }}).addTo(map);
    fetch('./map_data.json').then((r) => r.json()).then((payload) => {{
      const markers = payload.markers || [];
      const grouped = payload.grouped_markers || [];
      const groupedRegional = payload.grouped_regional_reports || [];
      const regional = payload.regional_reports || [];
      const fallbackNote = "No local reports met the mapping rules for this week. Showing regional/statewide reports instead.";
      const sparseNote = "Only a small number of local reports met the mapping rules this week. Regional/statewide reports are shown for context.";
      const noResultsNote = "No reports match the current map filters. Reset Map to restore the default view.";
      const showRegionalDefault = Boolean(payload.show_regional_default);
      const defaultViewMode = String(payload.default_view_mode || 'local');
      const localMarkerCount = Number((payload.diagnostics && payload.diagnostics.local_marker_count) || (markers || []).length || 0);
      let active = [];
      function clear() {{ active.forEach((m) => map.removeLayer(m)); active = []; }}
      function categoryIcon(category) {{ const c = String(category || '').toLowerCase(); if (c.includes('housing') || c.includes('utility')) return 'H'; if (c.includes('health')) return '+'; if (c.includes('jobs') || c.includes('economy') || c.includes('labor')) return 'J'; if (c.includes('food')) return 'F'; if (c.includes('transport')) return 'T'; if (c.includes('safety') || c.includes('emergency')) return '!'; if (c.includes('wildfire') || c.includes('flood') || c.includes('recovery')) return 'W'; if (c.includes('school') || c.includes('government') || c.includes('policy')) return 'S'; return 'P'; }}
      function categoryColor(category) {{ const c = String(category || '').toLowerCase(); if (c.includes('housing') || c.includes('utility')) return '#B08A57'; if (c.includes('health')) return '#5D8793'; if (c.includes('jobs') || c.includes('economy') || c.includes('labor')) return '#3F5878'; if (c.includes('food')) return '#5D7F62'; if (c.includes('transport')) return '#5B6F8A'; if (c.includes('safety') || c.includes('emergency')) return '#9A5A4A'; if (c.includes('wildfire') || c.includes('flood') || c.includes('recovery')) return '#B6784F'; if (c.includes('school') || c.includes('government') || c.includes('policy')) return '#6D6287'; return '#5D8793'; }}
      function localMarkerHtml(category) {{ const label = categoryIcon(category); const color = categoryColor(category); return `<div class="local-pill" style="background:${{color}};">${{label}}</div>`; }}
      function markerHtml(scope, count) {{ const size = Math.min(52, 30 + (count * 2)); return `<div class="group-count" style="width:${{size}}px;height:${{size}}px;">${{count}}</div>`; }}
      function regionalMarkerHtml() {{ return '<div class="regional-pill">REG</div>'; }}
      function truncateSummary(text) {{ const value = String(text || '').trim(); if (!value) return 'Source-backed local systems pressure signal.'; return value.length > 140 ? `${{value.slice(0, 137)}}...` : value; }}
      function groupedPopup(item) {{
        const reports = Array.isArray(item.reports) ? item.reports : [];
        const groupId = String(item.group_id || `${{item.place || 'group'}}-${{item.lat}}-${{item.lon}}`).replace(/[^a-zA-Z0-9_-]/g, '-');
        const pressureItems = (item.pressure_areas || []).slice(0, 5).map((value) => `<li>${{value}}</li>`).join('');
        const topItems = reports.slice(0, 3).map((report) => `<li>${{report.headline || 'Source-backed report'}}</li>`).join('');
        const reportCards = reports.map((report) => {{ const readLabel = report.read_more_label || report.headline || 'Read more'; return `<article class="report-card"><h4 class="report-headline">${{report.headline || 'Source-backed report'}}</h4><p class="report-summary">${{truncateSummary(report.summary)}}</p><p class="report-meta"><strong>Pressure type:</strong> ${{report.pressure_type || 'Local pressure signal'}}</p><p class="report-meta"><strong>Source:</strong> ${{report.publisher || 'Source'}}</p><a class="report-link" href="${{report.source_url || report.read_more || '/cascadia/'}}" target="_blank" rel="noopener noreferrer">${{readLabel}}</a></article>`; }}).join('');
        const panelClass = reports.length > 3 ? 'reports-panel scrollable' : 'reports-panel';
        return `<div class="map-popup"><div class="place">${{item.place || item.regional_area || item.state || 'Place'}}</div><div class="field"><strong>Number of reports:</strong>${{item.group_count || reports.length || 1}}</div><div class="field"><strong>Pressure areas:</strong><ul>${{pressureItems || '<li>Local pressure signal</li>'}}</ul></div><div class="field"><strong>Top reports:</strong><ul>${{topItems || '<li>Source-backed local development</li>'}}</ul></div><div class="toggle-row"><button type="button" class="toggle-button js-toggle-reports" data-target="${{groupId}}">View individual reports</button></div><div class="${{panelClass}}" id="reports-${{groupId}}">${{reportCards}}</div></div>`;
      }}
      function popup(item) {{
        if ((item.group_count || 0) > 1 && Array.isArray(item.reports) && item.reports.length) return groupedPopup(item);
        const readMoreLabel = item.read_more_label || 'Dispatch section';
        const dateLabel = item.published_at ? new Date(item.published_at).toLocaleDateString('en-US', {{month:'long', day:'numeric', year:'numeric'}}) : 'Date not listed';
        return `<div class="map-popup"><div class="place">${{item.title || item.place || item.regional_area || item.state || 'Place'}}</div><div class="field"><strong>Pressure type:</strong>${{item.pressure_type || 'Local pressure signal'}}</div><div class="field"><strong>Location:</strong>${{item.location_label || item.place || item.regional_area || item.state || 'Place'}}</div><div class="field"><strong>Source:</strong><a href="${{item.source_url}}" target="_blank" rel="noopener noreferrer">${{item.publisher || 'Source'}}</a></div><div class="field"><strong>Date:</strong>${{dateLabel}}</div><div class="field"><strong>Summary:</strong>${{item.what_we_found || 'Source-backed report.'}}</div><div class="field"><strong>Why it matters:</strong>${{item.why_it_matters || 'This affects local households and services.'}}</div><div class="field"><strong>Read more:</strong><a href="${{item.read_more || '/cascadia/'}}">${{readMoreLabel}}</a></div>${{item.address ? `<div class="field"><strong>Address:</strong>${{item.address}}</div>` : ''}}<div class="field"><strong>View on map:</strong>${{item.precision_note || 'Mapped to city level.'}}</div></div>`;
      }}
      function validCoordinate(item) {{ const lat = Number(item.lat); const lon = Number(item.lon); return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180; }}
      function markerLatLon(item) {{ return [Number(item.lat), Number(item.lon)]; }}
      function draw(rows) {{ clear(); const bounds = []; const attemptedCount = rows.length; let renderErrors = 0; let skippedInvalidCoordinates = 0; for (const item of rows) {{ if (!validCoordinate(item)) {{ skippedInvalidCoordinates += 1; continue; }} try {{ const isRegional = String(item.marker_scope || '').toLowerCase() === 'regional'; const icon = (item.group_count || 0) > 1 ? L.divIcon({{className:'', html: markerHtml(item.marker_scope || 'local', item.group_count || 0), iconSize:[24,24]}}) : (isRegional ? L.divIcon({{className:'', html: regionalMarkerHtml(), iconSize:[34,34], iconAnchor:[17,17]}}) : L.divIcon({{className:'', html: localMarkerHtml(item.pressure_type), iconSize:[34,34], iconAnchor:[17,17]}})); const latLon = markerLatLon(item); const marker = L.marker(latLon, {{icon}}).addTo(map).bindPopup(popup(item), {{maxWidth: 360}}).bindTooltip(`<span class="tt-place">${{item.place || item.regional_area || item.state || 'Place'}}</span>${{item.pressure_type || 'Local pressure signal'}}`, {{className: 'region-tooltip', direction: 'top', offset:[0,-8], sticky:true}}); active.push(marker); bounds.push(latLon); }} catch (err) {{ renderErrors += 1; console.warn('map marker render failed', err); }} }} const wrap = getEl('mapWrap'); if (wrap) {{ wrap.setAttribute('data-invalid-coordinate-count', String(skippedInvalidCoordinates)); wrap.setAttribute('data-render-attempted-count', String(attemptedCount)); wrap.setAttribute('data-rendered-marker-count', String(active.length)); wrap.setAttribute('data-render-error-count', String(renderErrors)); }} const empty = document.getElementById('emptyState'); if (empty) empty.style.display = active.length ? 'none' : 'block'; const renderWarning = document.getElementById('renderWarning'); if (renderWarning) renderWarning.style.display = renderErrors > 0 ? 'block' : 'none'; if (bounds.length) map.fitBounds(bounds, {{padding:[36,36], maxZoom:7}}); else map.setView(DEFAULT_CENTER, DEFAULT_ZOOM); }}
      const publishedDate = (item) => {{ const raw = String(item.published_at || ''); const value = new Date(raw); return Number.isNaN(value.getTime()) ? null : value; }};
      const getEl = (id) => document.getElementById(id);
      const controlsReady = () => ['pressureFilter','stateFilter','regionFilter','timeFilter','viewMode','showRegional','resetMap'].every((id) => Boolean(getEl(id)));
      function optionize(id, values) {{ const el = document.getElementById(id); for (const value of values) {{ const option = document.createElement('option'); option.value = value; option.textContent = value; el.appendChild(option); }} }}
      optionize('pressureFilter', [...new Set(markers.map((m) => m.pressure_type).filter(Boolean))].sort());
      optionize('stateFilter', [...new Set(markers.map((m) => m.state).filter(Boolean))].sort());
      optionize('regionFilter', [...new Set(markers.map((m) => m.region_label).filter(Boolean))].sort());
      const dateRange = document.getElementById('dateRange');
      const formatDate = (value) => {{ const dt = new Date(String(value || '')); if (Number.isNaN(dt.getTime())) return null; return dt.toLocaleDateString('en-US', {{month:'long', day:'numeric', year:'numeric'}}); }};
      if (payload.coverage_start && payload.coverage_end) {{ const start = formatDate(payload.coverage_start); const end = formatDate(payload.coverage_end); if (start && end) dateRange.textContent = `Reports shown: ${{start}}-${{end}}`; }}
      function defaultNoteText() {{ if (defaultViewMode === 'regional_fallback') return fallbackNote; if (defaultViewMode === 'sparse_local_plus_regional') return sparseNote; return noResultsNote; }}
      function defaultRows() {{ const modeEl = getEl('viewMode'); const regionalEl = getEl('showRegional'); const mode = modeEl ? modeEl.value : 'grouped'; const includeRegional = regionalEl ? regionalEl.checked : showRegionalDefault; const baseLocal = mode === 'grouped' && grouped.length ? grouped : markers; const baseRegional = mode === 'grouped' && groupedRegional.length ? groupedRegional : regional; return includeRegional ? baseLocal.concat(baseRegional) : baseLocal; }}
      function applyFilters() {{ const pressureEl = getEl('pressureFilter'); const stateEl = getEl('stateFilter'); const regionEl = getEl('regionFilter'); const timeEl = getEl('timeFilter'); const note = getEl('emptyState'); if (note) note.textContent = defaultNoteText(); const pressure = pressureEl ? pressureEl.value : ''; const state = stateEl ? stateEl.value : ''; const region = regionEl ? regionEl.value : ''; const time = timeEl ? timeEl.value : 'all'; const base = defaultRows(); const now = new Date(); const filtered = base.filter((item) => {{ if (pressure && item.pressure_type !== pressure) return false; if (state && item.state !== state) return false; if (region && item.region_label !== region) return false; if (time !== 'all') {{ const days = Number(time); const dt = publishedDate(item); if (!dt) return false; const diff = (now.getTime() - dt.getTime()) / (1000 * 60 * 60 * 24); if (diff > days) return false; }} return true; }}); const wrap = getEl('mapWrap'); if (wrap) wrap.setAttribute('data-post-filter-count', String(filtered.length)); draw(filtered); }}
      function resetToDefaultView() {{ if (!controlsReady()) return; getEl('pressureFilter').value = ''; getEl('stateFilter').value = ''; getEl('regionFilter').value = ''; getEl('timeFilter').value = 'all'; getEl('viewMode').value = 'grouped'; getEl('showRegional').checked = showRegionalDefault; applyFilters(); }}
      if (controlsReady()) {{ getEl('showRegional').checked = showRegionalDefault; }}
      const initialRows = defaultRows();
      const wrap = getEl('mapWrap');
      if (wrap) {{ wrap.setAttribute('data-initial-row-count', String(initialRows.length)); wrap.setAttribute('data-post-filter-count', String(initialRows.length)); wrap.setAttribute('data-initial-visible-count', String((payload.diagnostics && payload.diagnostics.initial_visible_count) || initialRows.length)); }}
      draw(defaultRows());
      resetToDefaultView();
      map.on('popupopen', (event) => {{ const root = event.popup && event.popup.getElement ? event.popup.getElement() : null; if (!root) return; root.querySelectorAll('.js-toggle-reports').forEach((button) => {{ button.addEventListener('click', () => {{ const target = String(button.getAttribute('data-target') || ''); if (!target) return; const panel = root.querySelector(`#reports-${{target}}`); if (!panel) return; panel.classList.toggle('open'); button.textContent = panel.classList.contains('open') ? 'Hide reports' : 'View individual reports'; }}); }}); }});
      if (controlsReady()) {{ ['pressureFilter','stateFilter','regionFilter','timeFilter','viewMode','showRegional'].forEach((id) => {{ const el = getEl(id); if (el) el.addEventListener('change', applyFilters); }}); const resetBtn = getEl('resetMap'); if (resetBtn) resetBtn.addEventListener('click', resetToDefaultView); }}
    }});
  </script>
</body>
</html>
"""

def render_map_embed_html(
    *,
    include_edition_map_link: bool,
    include_source_table_link: bool,
) -> str:
    links: list[str] = []
    if include_edition_map_link:
        links.append('<a href="map.html">Open this week\'s interactive map</a>')
    if include_source_table_link:
        links.append('<a href="source_table.html">Open this week\'s source table</a>')
    links.append('<a href="/cascadia/map/" target="_blank" rel="noopener noreferrer">Open latest Cascadia map</a>')
    links_html = " | ".join(links)
    return f"<section class=\"cascadia-map-link\"><p>{links_html}</p></section>"


def build_grouped_markers(markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, int], dict[str, Any]] = {}
    for marker in markers:
        key = (
            str(marker.get("regional_area") or marker.get("state_or_region") or "Place"),
            int(round(float(marker.get("lat", 0.0)) * 10)),
            int(round(float(marker.get("lon", 0.0)) * 10)),
        )
        if key not in grouped:
            grouped[key] = dict(marker)
            grouped[key]["group_count"] = 0
            grouped[key]["group_id"] = f"{key[0]}-{key[1]}-{key[2]}".lower().replace(" ", "-")
            grouped[key]["reports"] = []
            grouped[key]["pressure_areas"] = []
        grouped[key]["group_count"] += 1
        report = {
            "headline": str(marker.get("title") or "").strip() or "Source-backed report",
            "summary": str(marker.get("what_we_found") or "").strip() or "Source-backed local development.",
            "pressure_type": str(marker.get("pressure_type") or "").strip() or "Local pressure signal",
            "publisher": str(marker.get("publisher") or "").strip() or "Source",
            "source_url": str(marker.get("source_url") or "").strip(),
            "read_more": str(marker.get("read_more") or "").strip() or "/cascadia/",
            "read_more_label": str(marker.get("read_more_label") or marker.get("title") or "").strip() or "Read more",
        }
        grouped[key]["reports"].append(report)
        pressure_area = report["pressure_type"]
        if pressure_area and pressure_area not in grouped[key]["pressure_areas"]:
            grouped[key]["pressure_areas"].append(pressure_area)
    return list(grouped.values())


def render_dashboard_html(edition_date: str, coverage_label: str, map_data: dict[str, Any]) -> str:
    markers = list(map_data.get("markers") or [])
    grouped = list(map_data.get("grouped_markers") or [])
    areas = sorted({str(row.get("regional_area") or row.get("state_or_region") or "") for row in grouped if row.get("regional_area") or row.get("state_or_region")})
    categories = sorted({str(row.get("pressure_type") or "") for row in markers if row.get("pressure_type")})
    changed = "".join(f"<li>{html.escape(row.get('regional_area') or row.get('state_or_region') or 'Place')}: {html.escape(str(row.get('what_we_found') or 'Source-backed development.'))}</li>" for row in markers[:6])
    watch = "".join(f"<li>{html.escape(area)}</li>" for area in areas[:8]) or "<li>Coverage concentrated in a small set of places this week.</li>"
    category_list = "".join(f"<li>{html.escape(cat)}</li>" for cat in categories)
    body = f"""{header(DISPATCH_NAME, "../", "../archive.html", "/cascadia/")}
  <main class="briefing">
    <section class="hero"><img class="hero-logo" src="../assets/{CASCADIA_LOGO_ASSET}" alt="{DISPATCH_NAME}"></section>
    <p class="eyebrow">Weekly systems and pressure signals across Washington, Oregon, and Idaho</p>
    <h1>The Cascadia Briefing Dashboard</h1>
    <p>Weekly framing: {html.escape(coverage_label)}.</p>
    <section><h2>Cascadia pressure map preview</h2><iframe title="Cascadia regional pressure map preview" src="/cascadia/map/" loading="lazy" style="width:100%;height:420px;border:1px solid #cfd8de;"></iframe><p><a href="/cascadia/map/">Open interactive Cascadia map</a></p></section>
    <section><h2>What changed this week</h2><ul>{changed}</ul></section>
    <section><h2>Regional watch areas</h2><ul>{watch}</ul></section>
    <section><h2>What this may mean for daily life</h2><p>These reports point to real pressures on housing costs, access to services, travel, and recovery. Impacts can differ across Puget Sound, Portland metro, Central Oregon, Inland Northwest, Idaho rural communities, and coastal communities.</p></section>
    <section><h2>Pressure categories</h2><ul>{category_list}</ul></section>
    <section><h2>Areas where visibility is weaker</h2><p>Some rural and coastal communities have fewer regularly published local reports, so weekly visibility can be thinner even when pressure is real.</p></section>
    <section><h2>Sources and methods</h2><p>Every public claim links back to a traceable source. Weekly reporting blends local news, public media, and official agencies from WA, OR, and ID.</p></section>
  </main>
{footer("../")}"""
    return page("Cascadia Regional Dashboard", f"{BASE_URL}/cascadia/dashboard/", "../assets/site.css", body, DISPATCH_NAME)


def archive_subtitle(stories: list[dict[str, Any]]) -> str:
    if not stories:
        return CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE
    parts = [f"{len(stories)} {'story' if len(stories) == 1 else 'stories'}"]
    states = public_story_states(stories)
    categories = public_story_categories(stories)
    if states:
        parts.append(", ".join(states))
    if categories:
        parts.append(", ".join(categories[:4]))
    return " | ".join(parts)


def render_cascadia_html(
    edition_date: str,
    stories: list[dict[str, Any]],
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str = "weekly",
    map_embed_html: str = "",
) -> str:
    coverage_label = format_coverage_label(coverage_start, coverage_end) if coverage_start and coverage_end else edition_date
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for story in stories:
        grouped[story["category"]].append(story)
    groups = "\n".join(render_story_group(category, items) for category, items in sorted(grouped.items()))
    weekly_bullets = build_weekly_summary_bullets(stories)
    weekly_summary = render_weekly_summary(weekly_bullets)
    if not groups:
        if coverage_start and coverage_end and briefing_type == "weekly":
            groups = (
                "<p>No qualifying source-backed Cascadia signals were identified "
                f"for this coverage window under the current public-systems criteria. "
                "Source collection diagnostics are retained locally for review.</p>"
            )
        else:
            groups = "<p>No public Cascadia stories met the source and relevance threshold for this edition.</p>"
    coverage_line = ""
    if coverage_start and coverage_end:
        coverage_line = f"Weekly briefing / {html.escape(coverage_label)} / Coverage: {html.escape(coverage_start)} through {html.escape(coverage_end)}"
    else:
        coverage_line = f"Regional systems briefing / {html.escape(edition_date)}"
    run_line = f"\n    <p class=\"edition-date\">Run date: {html.escape(run_date)}</p>" if run_date else ""
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/cascadia/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/{CASCADIA_LOGO_ASSET}" alt="{DISPATCH_NAME}">
    </section>
    <p class="eyebrow">{coverage_line}</p>{run_line}
    <p><strong>{DISPATCH_NAME}</strong></p>
    <p>{html.escape(CASCADIA_PUBLIC_DESCRIPTION)}</p>
    <p><strong>Cascadia Signal Pack</strong><br>Detailed downloadable records are being prepared for future release.</p>
    {weekly_summary}
    {map_embed_html}
    {groups}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {coverage_label}", f"{BASE_URL}/cascadia/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def write_json(path: Path, payload: Any, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text(path: Path, content: str, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_detail_csv(path: Path, records: list[dict[str, Any]], dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "story_id",
        "title",
        "category",
        "score",
        "source_record_ids",
        "source_urls",
        "included_in_public_summary",
        "included_in_detail_dataset",
        "excluded_reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: json.dumps(record.get(field)) if isinstance(record.get(field), list) else record.get(field) for field in fieldnames})


def editorial_checklist(
    coverage_label: str | None,
    stories: list[dict[str, Any]],
    html_text: str,
    weekly_summary_bullets: list[str],
    archive_weekly_only: bool = True,
) -> str:
    def pass_fail(condition: bool) -> str:
        return "pass" if condition else "fail"

    warnings: list[str] = []
    if "Score:" in html_text:
        warnings.append("Public HTML contains a numeric score label.")
    if any(not story.get("source_urls") for story in stories):
        warnings.append("At least one public story is missing a source URL.")
    if any(not story_why_it_matters(story) for story in stories):
        warnings.append("At least one public story is missing a why-it-matters line.")
    if any(str(story.get("summary") or "").strip().lower() == str(story.get("title") or "").strip().lower() for story in stories):
        warnings.append("At least one public story repeats the title as its summary.")
    if not warnings:
        warnings.append("No local editorial warnings.")
    source_labels_present = all(
        source_link_label(url, [record for record in story.get("source_records", []) if isinstance(record, dict)])
        for story in stories
        for url in story.get("source_urls", [])
    )
    lines = [
        "# Cascadia Editorial Review",
        "",
        f"- Coverage label: {coverage_label or 'not set'}",
        f"- Public story count: {len(stories)}",
        f"- Minimum story target met: {'yes' if len(stories) >= 5 else 'no'}",
        f"- No public numeric scores: {pass_fail('Score:' not in html_text)}",
        f"- No title-as-summary repeats: {pass_fail(all(str(story.get('summary') or '').strip().lower() != str(story.get('title') or '').strip().lower() for story in stories))}",
        f"- Every public story has source URL: {pass_fail(all(bool(story.get('source_urls')) for story in stories))}",
        f"- Every public story has source label: {pass_fail(source_labels_present)}",
        f"- Every public story has why-it-matters line: {pass_fail(all(bool(story_why_it_matters(story)) for story in stories))}",
        f"- Weekly summary present when stories exist: {pass_fail((not stories) or bool(weekly_summary_bullets))}",
        f"- Cascadia archive/recent/RSS weekly-only: {pass_fail(archive_weekly_only)}",
        f"- No output/detail or output/paid exposed publicly: {pass_fail('output/detail' not in html_text and 'output/paid' not in html_text)}",
        "",
        "## Warnings/recommendations",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def refresh_cascadia_archive_pages(root: Path, dry_run: bool, written: list[str]) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(
        slug=DISPATCH_SLUG,
        name=DISPATCH_NAME,
        edition_date="",
        tagline=SHORT_PUBLIC_DESCRIPTION,
        logo=CASCADIA_LOGO_ASSET,
        sources=[],
        stories=[],
    )
    dates = discover_public_edition_dates(site_root, DISPATCH_SLUG)
    if dates:
        dispatch = DispatchConfig(
            slug=DISPATCH_SLUG,
            name=DISPATCH_NAME,
            edition_date=dates[0],
            tagline=SHORT_PUBLIC_DESCRIPTION,
            logo=CASCADIA_LOGO_ASSET,
            sources=[],
            stories=[],
        )
    public_root = site_root / DISPATCH_SLUG
    generator_write_text(public_root / "index.html", render_dispatch_index_for_dates(dispatch, dates, site_root), dry_run, written)
    generator_write_text(public_root / "archive.html", render_archive_for_dates(dispatch, dates, site_root), dry_run, written)
    generator_write_text(public_root / "rss.xml", render_rss_for_dates(dispatch, dates, site_root), dry_run, written)


def render_cascadia_edition(
    root: Path,
    edition_date: str,
    dry_run: bool = False,
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str = "weekly",
) -> dict[str, Any]:
    root = root.resolve()
    if briefing_type == "weekly" and (not coverage_start or not coverage_end):
        week_start, week_end = containing_week(edition_date)
        coverage_start = coverage_start or week_start.isoformat()
        coverage_end = coverage_end or week_end.isoformat()
    curated_path = root / CASCADE_DATA_ROOT / "curated" / edition_date / "curation_manifest.json"
    output_dispatch_dir = root / "output" / "dispatches" / "cascadia" / "editions" / edition_date
    public_dir = root / "output" / "site" / "cascadia" / "editions" / edition_date
    detail_dir = root / "output" / "detail" / "cascadia" / edition_date
    warnings: list[str] = []
    errors: list[str] = []
    written: list[str] = []
    if not curated_path.exists():
        errors.append(f"curation manifest not found: {curated_path}")
        return {"ok": False, "written": written, "warnings": warnings, "errors": errors}
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    dedupe_result = dedupe_public_stories(root, DISPATCH_SLUG, edition_date, public_stories(curated), dry_run=dry_run, written=written)
    stories = dedupe_result.stories
    included_ids = {story.get("story_id") for story in stories}
    curated_for_public = [
        {**story, "included_in_public_summary": story.get("story_id") in included_ids}
        for story in curated
    ]
    errors.extend(validate_public_stories(stories))
    coverage_label = format_coverage_label(coverage_start, coverage_end) if coverage_start and coverage_end else None
    sources_manifest = sources_manifest_from_curated(stories, edition_date, run_date, coverage_start, coverage_end, briefing_type, coverage_label)
    curation_manifest = public_curation_manifest(curated_for_public, run_date, edition_date, coverage_start, coverage_end, briefing_type, coverage_label)
    public_curation = [item for item in curation_manifest if item.get("included_in_public_summary")]
    map_locations = load_map_locations(root)
    curated_map_markers, curated_regional_reports, map_warnings, curated_map_diagnostics = build_cascadia_map_markers(
        public_curation, sources_manifest, map_locations, coverage_start=coverage_start, coverage_end=coverage_end
    )
    warnings.extend(map_warnings)
    backfill_records: list[dict[str, Any]] = []
    if coverage_start and coverage_end:
        historical_sources_path = root / CASCADE_DATA_ROOT / "sources" / f"{coverage_start}_{coverage_end}" / "historical_sources.json"
        if historical_sources_path.exists():
            try:
                loaded = json.loads(historical_sources_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    backfill_records = [item for item in loaded if isinstance(item, dict)]
            except json.JSONDecodeError:
                warnings.append(f"map backfill source file is invalid JSON: {historical_sources_path}")
    backfill_map_markers, backfill_regional_reports, backfill_map_warnings, backfill_excluded_reasons = build_backfill_map_markers(
        backfill_records, map_locations, coverage_start, coverage_end, edition_date
    )
    warnings.extend(backfill_map_warnings)
    all_local_markers = curated_map_markers + backfill_map_markers
    merged_markers, map_duplicates_removed = dedupe_map_markers(all_local_markers)
    grouped_markers = build_grouped_markers(merged_markers)
    regional_reports = curated_regional_reports + backfill_regional_reports
    regional_reports_deduped, regional_duplicates_removed = dedupe_map_markers(regional_reports)
    grouped_regional_reports = build_grouped_markers(regional_reports_deduped)
    backfill_category_corrections = int((backfill_excluded_reasons or {}).pop("_category_corrections_applied", 0))
    backfill_extraction_success = dict((backfill_excluded_reasons or {}).pop("_extraction_fields_success", {}) or {})
    backfill_extraction_failed = dict((backfill_excluded_reasons or {}).pop("_extraction_fields_failed", {}) or {})
    backfill_candidate_rows = list((backfill_excluded_reasons or {}).pop("_candidate_rows", []) or [])
    backfill_place_match_by_type = dict((backfill_excluded_reasons or {}).pop("_place_match_by_type", {}) or {})
    backfill_place_extraction_attempted = int((backfill_excluded_reasons or {}).pop("_place_extraction_attempted", 0))
    backfill_place_extraction_succeeded = int((backfill_excluded_reasons or {}).pop("_place_extraction_succeeded", 0))
    excluded_reasons: Counter[str] = Counter(curated_map_diagnostics.get("excluded_reasons") or {})
    excluded_reasons.update(backfill_excluded_reasons or {})
    combined_extraction_success: Counter[str] = Counter(curated_map_diagnostics.get("extraction_fields_success") or {})
    combined_extraction_success.update(backfill_extraction_success)
    combined_extraction_failed: Counter[str] = Counter(curated_map_diagnostics.get("extraction_fields_failed") or {})
    combined_extraction_failed.update(backfill_extraction_failed)
    combined_place_match_by_type: Counter[str] = Counter(curated_map_diagnostics.get("place_match_by_type") or {})
    combined_place_match_by_type.update(backfill_place_match_by_type)
    local_extraction_success_count = int(curated_map_diagnostics.get("local_extraction_success_count", 0)) + int(sum(backfill_extraction_success.values()))
    local_extraction_failure_count = int(curated_map_diagnostics.get("local_extraction_failure_count", 0)) + int(sum(backfill_extraction_failed.values()))
    local_extraction_total = local_extraction_success_count + local_extraction_failure_count
    place_extraction_attempted = int(curated_map_diagnostics.get("place_extraction_attempted", 0)) + backfill_place_extraction_attempted
    place_extraction_succeeded = int(curated_map_diagnostics.get("place_extraction_succeeded", 0)) + backfill_place_extraction_succeeded
    candidate_diagnostics_rows = list(curated_map_diagnostics.get("candidate_diagnostics_rows") or []) + backfill_candidate_rows
    map_diagnostics = {
        "raw_candidate_count": int(curated_map_diagnostics.get("raw_candidate_count", 0)) + len(backfill_records),
        "accepted_local_marker_count": len(merged_markers),
        "regional_statewide_report_count": len(regional_reports_deduped),
        "excluded_count": int(curated_map_diagnostics.get("excluded_count", 0)) + int(sum((backfill_excluded_reasons or {}).values())),
        "excluded_reasons": dict(excluded_reasons),
        "category_corrections_applied": int(curated_map_diagnostics.get("category_corrections_applied", 0)) + backfill_category_corrections,
        "stale_records_removed": int(curated_map_diagnostics.get("stale_records_removed", 0))
        + int((backfill_excluded_reasons or {}).get("outside_report_window", 0))
        + int((backfill_excluded_reasons or {}).get("stale_reference_page", 0)),
        "state_only_place_excluded": int(curated_map_diagnostics.get("state_only_place_excluded", 0)) + int((backfill_excluded_reasons or {}).get("no_specific_place", 0)),
        "local_extraction_success_count": local_extraction_success_count,
        "local_extraction_failure_count": local_extraction_failure_count,
        "local_extraction_success_rate": round(local_extraction_success_count / local_extraction_total, 4) if local_extraction_total else 0.0,
        "unresolved_category_count": int(curated_map_diagnostics.get("unresolved_category_count", 0)) + int((backfill_excluded_reasons or {}).get("unresolved_pressure_category", 0)),
        "category_resolution_success_count": int(curated_map_diagnostics.get("category_resolution_success_count", 0)),
        "extraction_fields_success": dict(combined_extraction_success),
        "extraction_fields_failed": dict(combined_extraction_failed),
        "place_extraction_attempted": place_extraction_attempted,
        "place_extraction_succeeded": place_extraction_succeeded,
        "place_match_by_type": dict(combined_place_match_by_type),
        "candidate_diagnostics_rows": candidate_diagnostics_rows,
        "curated_story_marker_candidates": len(curated_map_markers),
        "backfill_record_candidates": len(backfill_records),
        "backfill_marker_candidates": len(backfill_map_markers),
        "duplicates_removed": map_duplicates_removed,
        "included_markers": len(merged_markers),
        "regional_duplicates_removed": regional_duplicates_removed,
        "excluded_backfill_records": max(0, len(backfill_records) - len(backfill_map_markers)),
    }
    local_marker_count = len(merged_markers)
    regional_report_count = len(regional_reports_deduped)
    if local_marker_count == 0 and regional_report_count > 0:
        default_view_mode = "regional_fallback"
        show_regional_default = True
    elif local_marker_count < 3 and regional_report_count > 0:
        default_view_mode = "sparse_local_plus_regional"
        show_regional_default = True
    else:
        default_view_mode = "local"
        show_regional_default = False
    map_diagnostics["default_view_mode"] = default_view_mode
    map_diagnostics["local_marker_count"] = local_marker_count
    map_diagnostics["regional_report_count"] = regional_report_count
    map_diagnostics["default_show_regional"] = bool(show_regional_default)
    map_diagnostics["initial_render_layer"] = (
        "regional_only"
        if default_view_mode == "regional_fallback"
        else ("local_plus_regional" if default_view_mode == "sparse_local_plus_regional" else "local_only")
    )
    local_grouped_count = len(grouped_markers) if grouped_markers else local_marker_count
    regional_grouped_count = len(grouped_regional_reports) if grouped_regional_reports else regional_report_count
    map_diagnostics["initial_visible_count"] = local_grouped_count + (regional_grouped_count if show_regional_default else 0)
    map_diagnostics.update(
        build_source_density_diagnostics(
            local_markers=merged_markers,
            regional_reports=regional_reports_deduped,
            candidate_rows=candidate_diagnostics_rows,
            excluded_reasons=dict(excluded_reasons),
        )
    )
    map_data = {
        "edition_date": edition_date,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "coverage_label": coverage_label,
        "note": MAP_NOTE,
        "markers": merged_markers,
        "regional_reports": regional_reports_deduped,
        "grouped_regional_reports": grouped_regional_reports,
        "show_regional_default": show_regional_default,
        "default_view_mode": default_view_mode,
        "grouped_markers": grouped_markers,
        "diagnostics": map_diagnostics,
    }
    map_html = render_map_html(edition_date, MAP_NOTE)
    map_embed_html = render_map_embed_html(
        include_edition_map_link=True,
        include_source_table_link=True,
    )
    html_text = render_cascadia_html(edition_date, stories, run_date, coverage_start, coverage_end, briefing_type, map_embed_html=map_embed_html)
    weekly_summary_bullets = build_weekly_summary_bullets(stories)
    public_categories = public_story_categories(stories)
    public_state_hints = public_story_states(stories)
    public_source_publishers = public_story_publishers(stories)
    public_archive_subtitle = archive_subtitle(stories)
    generated_at = datetime.now(timezone.utc).isoformat()
    source_record_ids = sorted({source["source_record_id"] for source in sources_manifest})
    source_urls = sorted({source["url"] for source in sources_manifest if source.get("url")})
    providers_used = sorted({source.get("provider_id") for source in sources_manifest if source.get("provider_id")})
    query_count = len({(source.get("provider_id"), source.get("query_used")) for source in sources_manifest if source.get("query_used")})
    historical_search = any(source.get("source_type") == "historical_search" for source in sources_manifest)
    included_source_count = len({source["source_record_id"] for source in sources_manifest})
    excluded_source_count = sum(1 for story in curated if story.get("excluded_reason"))
    historical_report_path = None
    historical_report: dict[str, Any] = {}
    if coverage_start and coverage_end:
        candidate = root / CASCADE_DATA_ROOT / "sources" / f"{coverage_start}_{coverage_end}" / "historical_search_report.json"
        if candidate.exists():
            historical_report_path = str(candidate)
            historical_report = json.loads(candidate.read_text(encoding="utf-8"))
            historical_search = True
            providers_used = sorted(set(providers_used) | set(historical_report.get("providers_used") or []))
            query_count = len(historical_report.get("queries_run") or []) or query_count
            excluded_source_count = int(historical_report.get("records_excluded", excluded_source_count))
            warnings.extend(historical_report.get("warnings") or [])
            errors.extend(historical_report.get("errors") or [])
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "public_name": DISPATCH_NAME,
        "briefing_type": briefing_type,
        "cadence": briefing_type,
        "edition_type": briefing_type,
        "run_date": run_date or edition_date,
        "edition_date": edition_date,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "coverage_label": coverage_label,
        "public_coverage_label": coverage_label,
        "public_coverage_range": {
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
        } if coverage_start and coverage_end else None,
        "week_label": week_label(datetime.fromisoformat(coverage_start).date()) if coverage_start else None,
        "source_record_ids": source_record_ids,
        "source_urls": source_urls,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/cascadia/editions/{edition_date}/",
        "local_output_path": str(public_dir),
        "local_backup_path": None,
        "template_version": TEMPLATE_VERSION,
        "source_count": len(sources_manifest),
        "included_source_count": included_source_count,
        "excluded_source_count": excluded_source_count,
        "story_count": len(curated),
        "public_story_count": len(stories),
        "public_categories": public_categories,
        "public_state_hints": public_state_hints,
        "public_source_publishers": public_source_publishers,
        "weekly_summary_bullets": weekly_summary_bullets,
        "public_archive_subtitle": public_archive_subtitle,
        "historical_search": historical_search,
        "providers_used": providers_used,
        "query_count": query_count,
        "historical_search_report_path": historical_report_path,
        "source_manifest_path": str(public_dir / "sources_manifest.json"),
        "curation_manifest_path": str(public_dir / "curation_manifest.json"),
        "free_public_artifacts": [
            str(public_dir / "index.html"),
            str(public_dir / "sources_manifest.json"),
            str(public_dir / "curation_manifest.json"),
        ],
        "paid_or_detail_artifacts": [],
        "detail_artifacts_publicly_exposed": False,
        "warnings": warnings,
        "errors": errors,
    }
    if errors:
        return {"ok": False, "written": written, "warnings": warnings, "errors": errors}
    for out_dir in [output_dispatch_dir, public_dir]:
        write_text(out_dir / "index.html", html_text, dry_run, written)
        write_json(out_dir / "edition_manifest.json", edition_manifest, dry_run, written)
        write_json(out_dir / "sources_manifest.json", sources_manifest, dry_run, written)
        write_json(out_dir / "curation_manifest.json", curation_manifest, dry_run, written)
        write_json(out_dir / "map_data.json", map_data, dry_run, written)
        write_text(out_dir / "map.html", map_html, dry_run, written)
    dashboard_html = render_dashboard_html(edition_date, coverage_label or edition_date, map_data)
    write_json(root / "output" / "site" / "cascadia" / "map" / "map_data.json", map_data, dry_run, written)
    write_text(root / "output" / "site" / "cascadia" / "map" / "index.html", map_html, dry_run, written)
    write_text(root / "output" / "site" / "cascadia" / "dashboard" / "index.html", dashboard_html, dry_run, written)
    write_text(
        output_dispatch_dir / "editorial_review.md",
        editorial_checklist(coverage_label, stories, html_text, weekly_summary_bullets),
        dry_run,
        written,
    )
    refresh_cascadia_archive_pages(root, dry_run, written)
    detail_result = write_cascadia_signal_package(
        root,
        edition_date,
        dry_run=dry_run,
        run_date=run_date,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        briefing_type=briefing_type,
    )
    written.extend(detail_result.get("written", []))
    warnings.extend(detail_result.get("warnings", []))
    errors.extend(detail_result.get("errors", []))
    return {
        "ok": not errors,
        "public_story_count": len(stories),
        "detail_count": int(detail_result.get("detail_count", 0)),
        "output_paths": {
            "dispatch_output": str(output_dispatch_dir),
            "public_site_output": str(public_dir),
            "detail_output": str(detail_dir),
        },
        "detail_output_paths": detail_result.get("output_paths", {}),
        "manifest_paths": {
            "edition_manifest": str(public_dir / "edition_manifest.json"),
            "sources_manifest": str(public_dir / "sources_manifest.json"),
            "curation_manifest": str(public_dir / "curation_manifest.json"),
        },
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }


