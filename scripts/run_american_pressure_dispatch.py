from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from scripts.american_pressure_anchor_ids import canonical_valid_anchor_ids_by_pillar
from scripts.american_pressure_text_cleaning import (
    clean_candidate_text,
    clean_google_rss_title,
    contains_forbidden_public_markup,
)

from bluefern_dispatches.american_pressure_sources import load_source_registry  # noqa: E402
from bluefern_dispatches.public_prose import find_public_prose_violations, sanitize_public_prose  # noqa: E402
from bluefern_dispatches.generator import (  # noqa: E402
    BASE_URL,
    DispatchConfig,
    footer,
    header,
    page,
    public_edition_is_listable,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
)


DISPATCH_SLUG = "american-pressure"
DISPATCH_NAME = "The American Pressure Dispatch"
DISPATCH_TAGLINE = "Weekly source-backed household pressure briefing"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE_MODES = {"manual", "auto", "both"}
IMPORTANT_CURRENT_DEVELOPMENT_PILLARS = [
    "housing_household_cost_pressure",
    "financial_distress_pressure",
    "food_pressure",
    "health_access_pressure",
    "labor_income_pressure",
    "local_system_strain",
]
REQUIRED_CURRENT_DEVELOPMENT_SEARCH_TARGETS = {
    "food_pressure": "food bank demand / SNAP / grocery pressure",
    "financial_distress_pressure": "bankruptcy / Chapter 11 / Chapter 7 / business closure / employer bankruptcy / hospital bankruptcy / nursing home bankruptcy / retail closure / debt distress / foreclosure",
    "housing_household_cost_pressure": "eviction filings / rent increases / utility shutoffs / utility rate hikes / energy burden / homelessness shelter demand / housing authority waitlist / insurance premium increases / mobile home park rent / property tax pressure",
    "health_access_pressure": "clinic / hospital / pharmacy / health access",
    "labor_income_pressure": "layoffs / WARN / employer cuts",
    "local_system_strain": "disaster / drought / flood / heat / local service strain",
    "policy_implementation": "benefit or policy implementation problems",
}
PILLAR_ORDER = [
    "food_pressure",
    "financial_distress_pressure",
    "housing_household_cost_pressure",
    "health_access_pressure",
    "labor_income_pressure",
    "environmental_pressure",
    "local_system_strain",
    "policy_implementation",
]
PILLAR_HEADINGS = {
    "food_pressure": "Food and Grocery Pressure",
    "financial_distress_pressure": "Debt and Bankruptcy Pressure",
    "housing_household_cost_pressure": "Housing and Monthly Bills",
    "health_access_pressure": "Health Care Access",
    "labor_income_pressure": "Jobs and Paychecks",
    "environmental_pressure": "Weather, Drought, and Disaster Strain",
    "local_system_strain": "Local Services Under Strain",
    "policy_implementation": "Benefit and Policy Delivery",
}
PILLAR_GUIDANCE = {
    "food_pressure": {
        "why_it_matters": "Food pressure is often an early sign of household squeeze.",
        "who_may_feel_it": "Families with low or fixed incomes, households with children, and older adults.",
        "watch_next": "Watch whether food assistance reliance and grocery cost pressure are rising or easing.",
    },
    "financial_distress_pressure": {
        "why_it_matters": "Debt and bankruptcy pressure can confirm that easier options are running out.",
        "who_may_feel_it": "Heavily indebted households, small businesses, and workers tied to stressed employers.",
        "watch_next": "Watch for spillover into layoffs, service disruptions, and local business closures.",
    },
    "housing_household_cost_pressure": {
        "why_it_matters": "Housing and utility costs can crowd out spending on food, care, and savings.",
        "who_may_feel_it": "Renters, first-time buyers, and households already cost-burdened on monthly bills.",
        "watch_next": "Watch whether shelter and utility pressure is broadening or stabilizing.",
    },
    "health_access_pressure": {
        "why_it_matters": "Health access pressure can turn routine care gaps into emergencies.",
        "who_may_feel_it": "People with chronic conditions, caregivers, and uninsured or underinsured households.",
        "watch_next": "Watch for coverage changes, closures, and access bottlenecks.",
    },
    "labor_income_pressure": {
        "why_it_matters": "Job and income pressure can quickly raise missed-bill and debt risk.",
        "who_may_feel_it": "Hourly workers, workers in cyclical sectors, and households with small savings buffers.",
        "watch_next": "Watch layoffs, unemployment direction, and paycheck resilience.",
    },
    "environmental_pressure": {
        "why_it_matters": "Weather and disaster strain can increase costs and disrupt routines quickly.",
        "who_may_feel_it": "Rural communities, outdoor workers, and households in climate-vulnerable regions.",
        "watch_next": "Watch whether drought and weather pressures spill into food, housing, and health stress.",
    },
    "local_system_strain": {
        "why_it_matters": "Local service strain makes broader economic pressure harder to absorb.",
        "who_may_feel_it": "Commuters, caregivers, students, and households relying on local services.",
        "watch_next": "Watch for cuts, local budget strain, and recurring infrastructure disruptions.",
    },
    "policy_implementation": {
        "why_it_matters": "Policy delivery pressure affects whether support reaches households in time.",
        "who_may_feel_it": "Benefit-dependent households, providers, and local agencies.",
        "watch_next": "Watch for enrollment delays, eligibility changes, and access friction.",
    },
}
PILLAR_DASHBOARD_SUMMARIES = {
    "food_pressure": "More households may be stretching grocery budgets or turning to food support.",
    "financial_distress_pressure": "Bankruptcy data can show where financial stress has moved from warning signs to hard outcomes.",
    "housing_household_cost_pressure": "Monthly bills may be taking a larger bite out of household income.",
    "health_access_pressure": "People may face longer delays, fewer nearby options, or harder choices about care.",
    "labor_income_pressure": "Paycheck disruption can quickly turn into missed bills for households with little cushion.",
    "environmental_pressure": "Storms, drought, or recovery delays can raise costs and strain local services.",
    "local_system_strain": "When local systems are stretched, residents may feel it through schools, transit, emergency response, or public services.",
    "policy_implementation": "This area tracks whether public support is reaching households when it is needed.",
}
US_STATE_CENTROIDS = {
    "AL": (32.806671, -86.79113),
    "AK": (64.200841, -149.493673),
    "AZ": (34.048928, -111.093731),
    "AR": (34.969704, -92.373123),
    "CA": (36.778259, -119.417931),
    "CO": (39.550051, -105.782067),
    "CT": (41.603221, -73.087749),
    "DE": (38.910832, -75.52767),
    "FL": (27.664827, -81.515754),
    "GA": (32.157435, -82.907123),
    "HI": (19.898682, -155.665857),
    "ID": (44.068202, -114.742041),
    "IL": (40.633125, -89.398528),
    "IN": (40.551217, -85.602364),
    "IA": (41.878003, -93.097702),
    "KS": (39.011902, -98.484246),
    "KY": (37.839333, -84.270018),
    "LA": (30.984298, -91.962333),
    "ME": (45.253783, -69.445469),
    "MD": (39.045755, -76.641271),
    "MA": (42.407211, -71.382437),
    "MI": (44.314844, -85.602364),
    "MN": (46.729553, -94.6859),
    "MS": (32.354668, -89.398528),
    "MO": (37.964253, -91.831833),
    "MT": (46.879682, -110.362566),
    "NE": (41.492537, -99.901813),
    "NV": (38.80261, -116.419389),
    "NH": (43.193852, -71.572395),
    "NJ": (40.058324, -74.405661),
    "NM": (34.51994, -105.87009),
    "NY": (43.299428, -74.217933),
    "NC": (35.759573, -79.0193),
    "ND": (47.551493, -101.002012),
    "OH": (40.417287, -82.907123),
    "OK": (35.46756, -97.516428),
    "OR": (43.804133, -120.554201),
    "PA": (41.203322, -77.194525),
    "RI": (41.580095, -71.477429),
    "SC": (33.836081, -81.163725),
    "SD": (43.969515, -99.901813),
    "TN": (35.517491, -86.580447),
    "TX": (31.968599, -99.901813),
    "UT": (39.32098, -111.093731),
    "VT": (44.558803, -72.577841),
    "VA": (37.431573, -78.656894),
    "WA": (47.751074, -120.740139),
    "WV": (38.597626, -80.454903),
    "WI": (43.78444, -88.787868),
    "WY": (43.075968, -107.290284),
    "DC": (38.907192, -77.036871),
}
US_STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
US_CITY_STATE_LOOKUP = {
    ("sacramento", "CA"): (38.5816, -121.4944),
    ("los angeles", "CA"): (34.0522, -118.2437),
    ("portland", "OR"): (45.5152, -122.6784),
    ("new york", "NY"): (40.7128, -74.0060),
    ("denver", "CO"): (39.7392, -104.9903),
    ("jackson", "MS"): (32.2988, -90.1848),
    ("detroit", "MI"): (42.3314, -83.0458),
    ("centerville", "IA"): (40.7342, -92.8744),
    ("marshall", "MO"): (39.1233, -93.1960),
}
US_COUNTY_STATE_LOOKUP = {
    ("osceola county", "FL"): (28.1014, -81.4167),
    ("los angeles county", "CA"): (34.0522, -118.2437),
    ("san luis obispo county", "CA"): (35.2828, -120.6596),
    ("saline county", "MO"): (39.1361, -93.2020),
    ("appanoose county", "IA"): (40.7430, -92.8700),
}
CATEGORY_BY_PILLAR = {
    "food_pressure": "food-pressure",
    "financial_distress_pressure": "financial-distress-pressure",
    "housing_household_cost_pressure": "housing-household-cost-pressure",
    "health_access_pressure": "health-access-pressure",
    "labor_income_pressure": "labor-income-pressure",
    "environmental_pressure": "environmental-pressure",
    "local_system_strain": "local-system-strain",
    "policy_implementation": "policy-implementation",
}
REQUIRED_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "source_type",
    "summary_or_snippet",
    "reliability_tier",
}
PUBLIC_PRESSURE_KEYWORDS = {
    "job", "jobs", "layoff", "layoffs", "worker", "workers", "hospital", "healthcare", "clinic",
    "food", "supplier", "grocery", "housing", "rent", "mortgage", "utility", "utilities", "rural",
    "household", "consumer", "county", "district", "service", "services", "employer", "employment",
}
INVESTOR_ONLY_KEYWORDS = {
    "shareholder", "bondholder", "equity holder", "equityholders", "investor presentation", "capital structure optimization", "eps guidance",
}
LOW_QUALITY_PUBLIC_ITEM_PHRASES = (
    "official baseline",
    "data table",
    "research page",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def validate_not_future_date(edition_date: str, *, allow_future: bool) -> None:
    if allow_future:
        return
    today = datetime.now().date()
    requested = datetime.strptime(edition_date, "%Y-%m-%d").date()
    if requested > today:
        raise ValueError(
            f"future edition date refused without --allow-future: {edition_date} (today: {today.isoformat()})"
        )


def _week_start_date(edition_date: str) -> str:
    end = datetime.strptime(edition_date, "%Y-%m-%d").date()
    return (end - timedelta(days=6)).isoformat()


def _display_date_range(edition_date: str) -> str:
    end = datetime.strptime(edition_date, "%Y-%m-%d").date()
    start = end - timedelta(days=6)
    month_start = start.strftime("%B")
    month_end = end.strftime("%B")
    if month_start == month_end:
        return f"{month_start} {start.day}–{month_end} {end.day}, {end.year}"
    return f"{month_start} {start.day}–{month_end} {end.day}, {end.year}"


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


def copy_file(source: Path, target: Path, dry_run: bool, wrote: list[str], warnings: list[str]) -> None:
    if not source.exists():
        warnings.append(f"missing file: {source}")
        return
    wrote.append(str(target))
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def manual_source_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.json"


def daily_candidate_source_path(root: Path, day: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "candidates" / day / "candidate_sources.json"


def init_manual_sources_file(root: Path, edition_date: str, *, dry_run: bool, wrote: list[str]) -> Path:
    path = manual_source_path(root, edition_date)
    if path.exists():
        return path
    payload = {"sources": [], "_guidance": "Add source-backed records to sources[]."}
    write_json(path, payload, dry_run, wrote)
    return path


def init_daily_candidates_file(root: Path, day: str, *, dry_run: bool, wrote: list[str]) -> Path:
    path = daily_candidate_source_path(root, day)
    if path.exists():
        return path
    payload = {"sources": [], "_guidance": "Add daily candidate current-development records to sources[]."}
    write_json(path, payload, dry_run, wrote)
    return path


def _normalize_pillar(value: str) -> str:
    norm = (value or "").strip().lower().replace("-", "_")
    mapping = {
        "household_cost_pressure": "housing_household_cost_pressure",
        "housing_household_cost_pressure": "housing_household_cost_pressure",
        "housing_cost_pressure": "housing_household_cost_pressure",
    }
    return mapping.get(norm, norm)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_list_of_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_safe_text(item) for item in value if _safe_text(item)]
    if isinstance(value, str) and value.strip():
        return [_safe_text(part) for part in value.split(",") if _safe_text(part)]
    return []


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def _in_us_bounds(latitude: float, longitude: float) -> bool:
    return 18.0 <= latitude <= 72.0 and -179.9 <= longitude <= -66.0


def _split_city_state(location_label: str) -> tuple[str, str]:
    parts = [part.strip() for part in location_label.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _split_county_state(location_label: str) -> tuple[str, str]:
    city_or_county, state = _split_city_state(location_label)
    if city_or_county.lower().endswith("county") and state:
        return city_or_county, state
    return "", ""


def _normalize_state_token(value: str) -> str:
    token = _safe_text(value).strip().lower().strip(".")
    if not token:
        return ""
    if token in US_STATE_NAME_TO_ABBR:
        return US_STATE_NAME_TO_ABBR[token]
    candidate = token.upper()
    if candidate in US_STATE_CENTROIDS:
        return candidate
    return ""


_CITY_STATE_PATTERNS = [
    re.compile(r"\b([A-Z][A-Za-z .'-]+?),\s*([A-Z]{2})\b"),
    re.compile(r"\b([A-Z][A-Za-z .'-]+?),\s*(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b", re.IGNORECASE),
]
_COUNTY_STATE_PATTERNS = [
    re.compile(r"\b([A-Z][A-Za-z .'-]+? County),\s*([A-Z]{2})\b"),
    re.compile(r"\b([A-Z][A-Za-z .'-]+? County),\s*(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b", re.IGNORECASE),
]


def _extract_state_only(text: str) -> str | None:
    token = _safe_text(text)
    if not token:
        return None
    lower = token.lower()
    # Only accept state-only parsing when exactly one unambiguous full state name is present.
    matched: list[str] = []
    for name, abbr in US_STATE_NAME_TO_ABBR.items():
        if re.search(rf"\b{re.escape(name)}\b", lower, flags=re.IGNORECASE):
            matched.append(abbr)
    unique = sorted(set(matched))
    if len(unique) != 1:
        return None
    # "Washington" alone is ambiguous unless explicitly "Washington state" or "state of Washington".
    if unique[0] == "WA":
        if not (
            re.search(r"\bwashington state\b", lower, flags=re.IGNORECASE)
            or re.search(r"\bstate of washington\b", lower, flags=re.IGNORECASE)
        ):
            return None
    return unique[0]


def _has_ambiguous_location_text(title: str, summary: str) -> bool:
    text = f"{_safe_text(title)} {_safe_text(summary)}".lower()
    if "washington" in text and "washington state" not in text and "state of washington" not in text:
        return True
    return False


def _extract_location_candidates_from_text(text: str, *, method_prefix: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for pattern in _COUNTY_STATE_PATTERNS:
        for m in pattern.finditer(text):
            county = _safe_text(m.group(1)).lower()
            state = _normalize_state_token(m.group(2))
            if not county or not state:
                continue
            candidates.append(
                {
                    "kind": "county_state",
                    "county": county,
                    "state": state,
                    "evidence": m.group(0),
                    "confidence": "high",
                    "method": f"{method_prefix}_county_state",
                }
            )
    for pattern in _CITY_STATE_PATTERNS:
        for m in pattern.finditer(text):
            city = _safe_text(m.group(1)).lower()
            state = _normalize_state_token(m.group(2))
            if not city or not state:
                continue
            candidates.append(
                {
                    "kind": "city_state",
                    "city": city,
                    "state": state,
                    "evidence": m.group(0),
                    "confidence": "medium",
                    "method": f"{method_prefix}_city_state",
                }
            )
    state = _extract_state_only(text)
    if state:
        candidates.append(
            {
                "kind": "state_only",
                "state": state,
                "evidence": state,
                "confidence": "low",
                "method": f"{method_prefix}_state_only",
            }
        )
    return candidates


def _is_national_scope(source: dict[str, Any]) -> bool:
    # Do not classify by generic "geography=US"; only explicit scope fields should trigger national-only handling.
    scope = _safe_text(source.get("region_scope") or source.get("location_scope") or source.get("region")).lower()
    return scope in {"us", "u.s.", "usa", "united states", "national", "nationwide"}


def _build_map_data(
    edition_date: str,
    manifest: dict[str, Any],
    sources: list[dict[str, Any]],
    stories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    mapped: list[dict[str, Any]] = []
    national_records: list[dict[str, Any]] = []
    unmapped_records: list[dict[str, Any]] = []
    week_range = _safe_text(manifest.get("display_date_range")) or _display_date_range(edition_date)
    precision_counts = {
        "exact_or_source_provided": 0,
        "city_state": 0,
        "county_state": 0,
        "state_level": 0,
    }

    diagnostics = {"extracted_candidates": [], "accepted_candidates": [], "rejected_candidates": []}
    source_to_stories: dict[str, list[dict[str, Any]]] = {}
    for story in stories or []:
        for sid in _safe_list_of_text(story.get("source_record_ids")):
            source_to_stories.setdefault(sid, []).append(story)

    def reject_candidate(candidate: dict[str, Any], reason: str) -> None:
        diagnostics["rejected_candidates"].append({**candidate, "rejection_reason": reason})

    for source in sorted(sources, key=lambda row: _safe_text(row.get("source_record_id"))):
        source_record_id = _safe_text(source.get("source_record_id"))
        source_url = _safe_text(source.get("url"))
        if not source_record_id or not source_url.startswith(("http://", "https://")):
            continue
        raw_fields = [
            "city", "state", "state_code", "location", "manual_location", "location_scope",
            "place_name", "county", "region", "state_hint",
        ]
        location_label = _safe_text(source.get("manual_location") or source.get("location") or source.get("location_scope") or source.get("place_name"))
        city, state = _split_city_state(location_label)
        county_from_label, county_state_from_label = _split_county_state(location_label)
        category = _safe_text(source.get("pillar") or source.get("category_hint"))
        title = _safe_text(source.get("reader_headline") or source.get("title"))
        summary = _safe_text(source.get("summary_or_snippet"))
        linked_stories = source_to_stories.get(source_record_id, [])
        story_ids = [str(s.get("story_id") or "").strip() for s in linked_stories if str(s.get("story_id") or "").strip()]
        latitude = _safe_float(source.get("latitude"))
        if latitude is None:
            latitude = _safe_float(source.get("lat"))
        longitude = _safe_float(source.get("longitude"))
        if longitude is None:
            longitude = _safe_float(source.get("lon"))
        if longitude is None:
            longitude = _safe_float(source.get("lng"))
        edition_url = f"/american-pressure/editions/{edition_date}/"
        entry = {
            "title": title,
            "date": _safe_text(source.get("published_at")) or edition_date,
            "location_label": location_label,
            "city": city,
            "state": state,
            "category": category,
            "source_urls": [source_url],
            "edition_url": edition_url,
            "source_record_ids": [source_record_id],
            "story_ids": story_ids,
            "dispatch_url": "/american-pressure/",
            "source_title_date": f"{title} ({_safe_text(source.get('published_at')) or edition_date})",
        }
        if latitude is not None and longitude is not None and _in_us_bounds(latitude, longitude):
            mapped.append({**entry, "latitude": latitude, "longitude": longitude, "state": state, "location_precision": "exact_or_source_provided", "location_precision_warning": "", "location_extraction_method": "source_coordinates", "location_extraction_text": "latitude/longitude", "extraction_evidence_text": "latitude/longitude", "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
            diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": location_label, "method": "source_coordinates", "evidence": "latitude/longitude", "precision": "exact_or_source_provided"})
            precision_counts["exact_or_source_provided"] += 1
            continue
        if latitude is not None and longitude is not None and not _in_us_bounds(latitude, longitude):
            unmapped_records.append({**entry, "unmapped_reason": "invalid_coordinates"})
            continue

        city_value = _safe_text(source.get("city") or city).lower()
        state_value = _normalize_state_token(_safe_text(source.get("state_code") or source.get("state") or source.get("state_hint") or state or county_state_from_label))
        county_value = _safe_text(source.get("county") or county_from_label).lower()
        if city_value.endswith("county") and not county_value:
            county_value = city_value
            city_value = ""
        if city_value and state_value:
            coords = US_CITY_STATE_LOOKUP.get((city_value, state_value))
            if coords:
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": f"{city_value.title()}, {state_value}", "state": state_value, "location_precision": "city_state", "location_precision_warning": "", "location_extraction_method": "source_metadata_city_state", "location_extraction_text": f"{city_value.title()}, {state_value}", "extraction_evidence_text": f"{city_value.title()}, {state_value}", "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": f"{city_value.title()}, {state_value}", "method": "source_metadata_city_state", "evidence": f"{city_value.title()}, {state_value}", "precision": "city_state"})
                precision_counts["city_state"] += 1
                continue
        if county_value and state_value:
            key = (county_value if county_value.endswith("county") else f"{county_value} county", state_value)
            coords = US_COUNTY_STATE_LOOKUP.get(key)
            if coords:
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": f"{key[0].title()}, {state_value}", "state": state_value, "location_precision": "county_state", "location_precision_warning": "county-level location, not exact address.", "location_extraction_method": "source_metadata_county_state", "location_extraction_text": f"{key[0].title()}, {state_value}", "extraction_evidence_text": f"{key[0].title()}, {state_value}", "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": f"{key[0].title()}, {state_value}", "method": "source_metadata_county_state", "evidence": f"{key[0].title()}, {state_value}", "precision": "county_state"})
                precision_counts["county_state"] += 1
                continue
        state_only = _normalize_state_token(
            _safe_text(source.get("state") or source.get("state_code") or source.get("state_hint") or state or location_label)
        )
        if state_only:
            coords = US_STATE_CENTROIDS.get(state_only)
            if coords:
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": f"{state_only}", "state": state_only, "location_precision": "state_level", "location_precision_warning": "state-level location, not exact address.", "location_extraction_method": "source_metadata_state", "location_extraction_text": state_only, "extraction_evidence_text": state_only, "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": state_only, "method": "source_metadata_state", "evidence": state_only, "precision": "state_level"})
                precision_counts["state_level"] += 1
                continue

        candidate_texts: list[tuple[str, str]] = [("source_title", _safe_text(source.get("title"))), ("source_snippet", summary), ("reader_headline", title)]
        for story in linked_stories:
            for field in ("title", "summary", "human_story_summary", "what_happened", "who_may_feel_it", "location_scope"):
                candidate_texts.append((f"story_{field}", _safe_text(story.get(field))))
        extracted: list[dict[str, Any]] = []
        for field_name, text_value in candidate_texts:
            if text_value:
                extracted.extend(_extract_location_candidates_from_text(text_value, method_prefix=f"text_{field_name}"))
        seen_locations: set[tuple[str, str]] = set()
        if _is_national_scope(source):
            for candidate in extracted:
                diagnostics["extracted_candidates"].append({**candidate, "source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids})
                reject_candidate({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "candidate": candidate}, "national_scope")
            national_records.append({**entry, "unmapped_reason": "national_scope"})
            continue
        for candidate in extracted:
            diagnostics["extracted_candidates"].append({**candidate, "source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids})
            if candidate["kind"] == "city_state":
                key = (candidate["city"], candidate["state"])
                coords = US_CITY_STATE_LOOKUP.get(key)
                if not coords:
                    reject_candidate({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "candidate": candidate}, "city_lookup_missing")
                    continue
                if key in seen_locations:
                    continue
                seen_locations.add(key)
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": f"{candidate['city'].title()}, {candidate['state']}", "state": candidate["state"], "location_precision": "city_state", "location_precision_warning": "", "location_extraction_method": candidate["method"], "location_extraction_text": candidate["evidence"], "extraction_evidence_text": candidate["evidence"], "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": f"{candidate['city'].title()}, {candidate['state']}", "method": candidate["method"], "evidence": candidate["evidence"], "precision": "city_state"})
                precision_counts["city_state"] += 1
            elif candidate["kind"] == "county_state":
                key = (candidate["county"], candidate["state"])
                coords = US_COUNTY_STATE_LOOKUP.get(key)
                if not coords:
                    reject_candidate({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "candidate": candidate}, "county_lookup_missing")
                    continue
                if key in seen_locations:
                    continue
                seen_locations.add(key)
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": f"{candidate['county'].title()}, {candidate['state']}", "state": candidate["state"], "location_precision": "county_state", "location_precision_warning": "county-level location, not exact address.", "location_extraction_method": candidate["method"], "location_extraction_text": candidate["evidence"], "extraction_evidence_text": candidate["evidence"], "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": f"{candidate['county'].title()}, {candidate['state']}", "method": candidate["method"], "evidence": candidate["evidence"], "precision": "county_state"})
                precision_counts["county_state"] += 1
            elif candidate["kind"] == "state_only":
                coords = US_STATE_CENTROIDS.get(candidate["state"])
                if not coords:
                    reject_candidate({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "candidate": candidate}, "state_lookup_missing")
                    continue
                key = (candidate["state"], "state")
                if key in seen_locations:
                    continue
                seen_locations.add(key)
                mapped.append({**entry, "latitude": coords[0], "longitude": coords[1], "location_label": candidate["state"], "state": candidate["state"], "location_precision": "state_level", "location_precision_warning": "state-level location, not exact address.", "location_extraction_method": candidate["method"], "location_extraction_text": candidate["evidence"], "extraction_evidence_text": candidate["evidence"], "source_record_id": source_record_id, "source_url": source_url, "story_id": story_ids[0] if story_ids else ""})
                diagnostics["accepted_candidates"].append({"source_record_id": source_record_id, "source_url": source_url, "story_ids": story_ids, "location_label": candidate["state"], "method": candidate["method"], "evidence": candidate["evidence"], "precision": "state_level"})
                precision_counts["state_level"] += 1
        if seen_locations:
            continue
        any_location_fields = any(_safe_text(source.get(field)) for field in raw_fields)
        ambiguous = _has_ambiguous_location_text(title, summary)
        if ambiguous:
            unmapped_reason = "ambiguous_location"
        elif any_location_fields:
            unmapped_reason = "no_lookup_match"
        else:
            unmapped_reason = "missing_location"
        unmapped_records.append({**entry, "unmapped_reason": unmapped_reason})

    categories = sorted({row.get("category") for row in mapped if row.get("category")})
    states = sorted({row.get("state") for row in mapped if row.get("state")})
    aggregates: dict[tuple[float, float, str], dict[str, Any]] = {}
    for pin in mapped:
        key = (float(pin["latitude"]), float(pin["longitude"]), str(pin.get("location_precision") or ""))
        grouped = aggregates.setdefault(
            key,
            {
                "latitude": pin["latitude"],
                "longitude": pin["longitude"],
                "location_label": pin.get("location_label"),
                "state": pin.get("state"),
                "location_precision": pin.get("location_precision"),
                "record_count": 0,
                "source_record_ids": [],
                "source_urls": [],
                "story_ids": [],
                "categories": [],
                "pins": [],
            },
        )
        grouped["record_count"] += 1
        grouped["source_record_ids"].extend(pin.get("source_record_ids") or [])
        grouped["source_urls"].extend(pin.get("source_urls") or [])
        grouped["story_ids"].extend(pin.get("story_ids") or [])
        if pin.get("category"):
            grouped["categories"].append(pin["category"])
        grouped["pins"].append(pin)
    aggregated_pins = []
    for item in aggregates.values():
        item["source_record_ids"] = sorted(set(item["source_record_ids"]))
        item["source_urls"] = sorted(set(item["source_urls"]))
        item["story_ids"] = sorted(set(item["story_ids"]))
        item["categories"] = sorted(set(item["categories"]))
        aggregated_pins.append(item)

    return {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "display_date_range": week_range,
        "total_source_backed_records": len(mapped) + len(national_records) + len(unmapped_records),
        "pin_count": len(mapped),
        "needs_location_count": len(unmapped_records),
        "mapped_records_count": len(mapped),
        "exact_or_source_provided_count": precision_counts["exact_or_source_provided"],
        "city_state_count": precision_counts["city_state"],
        "county_state_count": precision_counts["county_state"],
        "state_level_count": precision_counts["state_level"],
        "national_records_count": len(national_records),
        "unmapped_records_count": len(unmapped_records),
        "categories": categories,
        "states": states,
        "location_precisions": ["exact_or_source_provided", "city_state", "county_state", "state_level"],
        "pins": mapped,
        "aggregated_pins": sorted(aggregated_pins, key=lambda row: (-int(row.get("record_count", 0)), _safe_text(row.get("location_label")))),
        "national_records": national_records,
        "unmapped_records": unmapped_records,
        "needs_location": unmapped_records,
        "location_extraction_diagnostics": diagnostics,
    }


def render_map_html(edition_date: str, map_data: dict[str, Any]) -> str:
    week_range = _safe_text(map_data.get("display_date_range")) or _display_date_range(edition_date)
    return page(
        "American Pressure Map",
        f"{BASE_URL}/american-pressure/map/",
        "../assets/site.css",
        f"""{header(DISPATCH_NAME, "../", "../archive.html", "/american-pressure/")}
  <main class="home ap-map-home">
    <section class="hero">
      <img class="hero-logo" src="../assets/american-pressure-logo.png" alt="{html.escape(DISPATCH_NAME)}">
    </section>
    <p class="eyebrow">American Pressure Map</p>
    <h1>American Pressure Map</h1>
    <p class="lede">Weekly briefing range: {html.escape(week_range)}</p>
    <p><a href="/american-pressure/">Back to American Pressure Dispatch</a> | <a href="/">Dispatches Home</a></p>
    <div class="ap-map-controls">
      <label for="ap-map-filter">Pressure area</label>
      <select id="ap-map-filter"><option value="all">All</option></select>
      <label for="ap-map-precision-filter">Location precision</label>
      <select id="ap-map-precision-filter"><option value="all">All</option></select>
      <label for="ap-map-state-filter">State</label>
      <select id="ap-map-state-filter"><option value="all">All</option></select>
      <span id="ap-map-count" class="ap-map-note"></span>
    </div>
    <section class="ap-map-legend">
      <h2>Counters</h2>
      <ul id="ap-map-counters"></ul>
    </section>
    <div id="ap-map" class="ap-map-canvas" role="region" aria-label="United States map of source-backed American Pressure locations"></div>
    <section class="ap-map-legend">
      <h2>Legend</h2>
      <p>Green: exact/source-provided. Blue: city/state. Orange: county/state. Gray: state-level.</p>
    </section>
    <section class="ap-map-needs">
      <h2>National records</h2>
      <ul id="ap-map-national-list"></ul>
    </section>
    <section class="ap-map-needs">
      <h2>Unmapped source-backed records</h2>
      <ul id="ap-map-needs-list"></ul>
    </section>
  </main>
{footer("../")}
<style>
.ap-map-home {{ width: min(1120px, calc(100% - 24px)); }}
.ap-map-controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0 12px; }}
.ap-map-note {{ color: var(--muted); font-size: .9rem; }}
.ap-map-canvas {{ height: 520px; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: #e9eff4; }}
.ap-map-legend, .ap-map-needs {{ margin-top: 18px; border: 1px solid var(--border); border-radius: 12px; background: #f8fbfd; padding: 12px 14px; }}
.ap-map-needs ul {{ margin: 0; padding-left: 18px; }}
.ap-map-needs li {{ margin: 8px 0; }}
@media (max-width: 760px) {{
  .ap-map-home {{ width: calc(100% - 16px); }}
  .ap-map-canvas {{ height: 430px; }}
}}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
(function () {{
  var mapEl = document.getElementById("ap-map");
  var filter = document.getElementById("ap-map-filter");
  var countEl = document.getElementById("ap-map-count");
  var precisionFilter = document.getElementById("ap-map-precision-filter");
  var stateFilter = document.getElementById("ap-map-state-filter");
  var needsList = document.getElementById("ap-map-needs-list");
  var nationalList = document.getElementById("ap-map-national-list");
  var counters = document.getElementById("ap-map-counters");
  if (!mapEl || !filter || !precisionFilter || !stateFilter || !countEl || !needsList || !nationalList || !counters || !window.L) return;
  var map = L.map(mapEl).setView([39.5, -98.35], 4);
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 9,
    attribution: "&copy; OpenStreetMap contributors"
  }}).addTo(map);
  fetch("map_data.json")
    .then(function (res) {{ return res.json(); }})
    .then(function (payload) {{
      var pins = Array.isArray(payload.pins) ? payload.pins : [];
      var aggregated = Array.isArray(payload.aggregated_pins) ? payload.aggregated_pins : [];
      var needs = Array.isArray(payload.unmapped_records) ? payload.unmapped_records : [];
      var national = Array.isArray(payload.national_records) ? payload.national_records : [];
      var categories = Array.isArray(payload.categories) ? payload.categories : [];
      var states = Array.isArray(payload.states) ? payload.states : [];
      var markers = [];
      categories.forEach(function (cat) {{
        var opt = document.createElement("option");
        opt.value = cat;
        opt.textContent = cat;
        filter.appendChild(opt);
      }});
      ["exact_or_source_provided", "city_state", "county_state", "state_level"].forEach(function (p) {{
        var opt = document.createElement("option");
        opt.value = p;
        opt.textContent = p;
        precisionFilter.appendChild(opt);
      }});
      states.forEach(function (st) {{
        var opt = document.createElement("option");
        opt.value = st;
        opt.textContent = st;
        stateFilter.appendChild(opt);
      }});
      counters.innerHTML =
        "<li>Total source-backed records: " + (payload.total_source_backed_records || 0) + "</li>" +
        "<li>Mapped records: " + (payload.mapped_records_count || 0) + "</li>" +
        "<li>Exact/source-provided pins: " + (payload.exact_or_source_provided_count || 0) + "</li>" +
        "<li>City/state pins: " + (payload.city_state_count || 0) + "</li>" +
        "<li>County/state pins: " + (payload.county_state_count || 0) + "</li>" +
        "<li>State-level pins: " + (payload.state_level_count || 0) + "</li>" +
        "<li>National records: " + (payload.national_records_count || 0) + "</li>" +
        "<li>Unmapped records: " + (payload.unmapped_records_count || 0) + "</li>";
      function popupHtml(pin) {{
        var sources = (pin.source_urls || []).map(function (url) {{
          return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">Source</a>';
        }}).join(" | ");
        return '<strong>' + (pin.title || "Untitled") + '</strong><br>' +
          'Date: ' + (pin.date || '') + '<br>' +
          (pin.location_label || "Location not labeled") + '<br>' +
          'Category: ' + (pin.category || "uncategorized") + '<br>' +
          'Precision: ' + (pin.location_precision || "unknown") + '<br>' +
          (pin.location_precision_warning ? ('Warning: ' + pin.location_precision_warning + '<br>') : '') +
          'Record IDs: ' + (pin.source_record_ids || []).join(", ") + '<br>' +
          '<a href="' + (pin.edition_url || "/american-pressure/") + '">Edition</a> | ' + sources;
      }}
      function markerStyle(pin) {{
        if (pin.location_precision === "exact_or_source_provided") return {{ radius: 7, fillColor: "#1f8a3b", color: "#0d5d25", weight: 1, fillOpacity: 0.9 }};
        if (pin.location_precision === "city_state") return {{ radius: 7, fillColor: "#2b6cb0", color: "#1a436e", weight: 1, fillOpacity: 0.9 }};
        if (pin.location_precision === "county_state") return {{ radius: 7, fillColor: "#c06c00", color: "#8f5100", weight: 1, fillOpacity: 0.9 }};
        return {{ radius: 7, fillColor: "#616161", color: "#3b3b3b", weight: 1, fillOpacity: 0.9 }};
      }}
      function renderPins(category, precision, state) {{
        markers.forEach(function (m) {{ map.removeLayer(m); }});
        markers = [];
        var active = aggregated.filter(function (pin) {{
          var catOk = category === "all" || (Array.isArray(pin.categories) && pin.categories.indexOf(category) >= 0);
          var okPrecision = precision === "all" || pin.location_precision === precision;
          var okState = state === "all" || pin.state === state;
          return catOk && okPrecision && okState;
        }});
        active.forEach(function (group) {{
          var pin = (Array.isArray(group.pins) && group.pins[0]) ? group.pins[0] : group;
          var style = markerStyle(pin);
          if (group.record_count > 1) style.radius = 10;
          var marker = L.circleMarker([group.latitude, group.longitude], style).addTo(map).bindPopup(
            '<strong>' + (group.location_label || "Location") + '</strong><br>' +
            'Grouped records: ' + (group.record_count || 0) + '<br>' +
            popupHtml(pin)
          );
          markers.push(marker);
        }});
        var recordCount = active.reduce(function (sum, group) {{
          return sum + (group.record_count || 0);
        }}, 0);
        countEl.textContent = recordCount + " mapped record(s), " + active.length + " location group(s)";
      }}
      if (pins.length === 0) {{
        countEl.textContent = "No mapped records for this edition. See National and Unmapped panels.";
      }}
      national.forEach(function (row) {{
        var li = document.createElement("li");
        var source = (row.source_urls && row.source_urls[0]) || "";
        li.innerHTML = '<strong>' + (row.title || "Untitled") + '</strong> (' + (row.category || "uncategorized") + ') ' +
          (source ? '<a href="' + source + '" target="_blank" rel="noopener noreferrer">source</a>' : "");
        nationalList.appendChild(li);
      }});
      needs.forEach(function (row) {{
        var li = document.createElement("li");
        var source = (row.source_urls && row.source_urls[0]) || "";
        var reason = row.unmapped_reason ? (' - reason: ' + row.unmapped_reason) : "";
        li.innerHTML = '<strong>' + (row.title || "Untitled") + '</strong> (' + (row.category || "uncategorized") + ') ' +
          (source ? '<a href="' + source + '" target="_blank" rel="noopener noreferrer">source</a>' : "") + reason;
        needsList.appendChild(li);
      }});
      function rerender() {{ renderPins(filter.value, precisionFilter.value, stateFilter.value); }}
      filter.addEventListener("change", rerender);
      precisionFilter.addEventListener("change", rerender);
      stateFilter.addEventListener("change", rerender);
      renderPins("all", "all", "all");
    }})
    .catch(function () {{
      countEl.textContent = "Map data unavailable.";
    }});
}})();
</script>
""",
        DISPATCH_NAME,
    )


def load_manual_sources(root: Path, edition_date: str) -> tuple[Path, list[dict[str, Any]]]:
    path = manual_source_path(root, edition_date)
    if not path.exists():
        raise FileNotFoundError(
            "manual source file is required for source-mode manual/both: "
            f"{path}\n"
            f"Create it with:\n"
            f"  .\\.venv\\Scripts\\python.exe scripts\\run_american_pressure_dispatch.py --date {edition_date} --init-manual-sources"
        )
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manual_sources.json must be a list or an object with a sources list")
    return path, [record for record in records if isinstance(record, dict)]


def load_daily_candidate_sources(
    root: Path,
    edition_date: str,
    *,
    lookback_days: int = 6,
    include_approved_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    end_date = datetime.strptime(edition_date, "%Y-%m-%d").date()
    for offset in range(lookback_days + 1):
        day = (end_date - timedelta(days=offset)).isoformat()
        path = daily_candidate_source_path(root, day)
        if not path.exists():
            continue
        payload = read_json(path)
        records = payload.get("sources") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            warnings.append(f"daily candidate file has invalid shape (expected list/sources list): {path}")
            continue
        for record in records:
            if isinstance(record, dict):
                if include_approved_only and _safe_text(record.get("review_status")).lower() != "approved":
                    continue
                enriched = dict(record)
                enriched.setdefault("candidate_collected_on", day)
                out.append(enriched)
    return out, warnings


def load_auto_sources(root: Path, edition_date: str) -> list[dict[str, Any]]:
    rows = load_source_registry(root)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("enabled") is not True:
            continue
        if str(row.get("source_state") or "enabled") != "enabled":
            continue
        source_id = str(row.get("source_id") or "").strip()
        if not source_id:
            continue
        pillar = _normalize_pillar(str(row.get("pillar") or ""))
        if pillar not in PILLAR_ORDER:
            continue
        published = f"{edition_date}T00:00:00Z"
        out.append(
            {
                "source_record_id": f"auto-{edition_date}-{source_id}",
                "source_id": source_id,
                "title": str(row.get("name") or source_id),
                "url": str(row.get("url") or ""),
                "publisher": str(row.get("publisher") or ""),
                "published_at": published,
                "retrieved_at": utc_now(),
                "summary_or_snippet": str(row.get("notes") or "Official baseline indicator source."),
                "source_type": str(row.get("source_type") or "official_source"),
                "region_scope": str(row.get("geography") or "US"),
                "category_hint": pillar,
                "pillar": pillar,
                "reliability_tier": str(row.get("reliability_tier") or "official_primary"),
                "source_state": "enabled",
                "is_baseline_auto": True,
            }
        )
    return out


def normalize_sources(records: list[dict[str, Any]], edition_date: str) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, int]]:
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    diagnostics = {
        "sources_attempted": len(records),
        "candidates_found": 0,
        "candidates_accepted": 0,
        "rejected_investor_only": 0,
        "rejected_no_public_pressure_angle": 0,
        "rejected_duplicate_or_stale": 0,
        "rejected_missing_required_fields": 0,
    }
    for index, record in enumerate(records, start=1):
        missing = sorted(field for field in REQUIRED_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            diagnostics["rejected_missing_required_fields"] += 1
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
            continue
        diagnostics["candidates_found"] += 1
        source_record_id = str(record.get("source_record_id") or "").strip()
        source_id = str(record.get("source_id") or source_record_id).strip()
        if not source_id:
            diagnostics["rejected_missing_required_fields"] += 1
            errors.append(f"source record {index} missing required source_id/source_record_id")
            continue
        if source_id in seen_ids:
            diagnostics["rejected_duplicate_or_stale"] += 1
            continue
        seen_ids.add(source_id)
        url = str(record.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            diagnostics["rejected_missing_required_fields"] += 1
            errors.append(f"source record {index} has invalid URL: {url}")
            continue
        region_scope = str(record.get("region_scope") or record.get("geography") or "").strip()
        category_hint = str(record.get("category_hint") or record.get("pillar") or "").strip()
        if not region_scope or not category_hint:
            diagnostics["rejected_missing_required_fields"] += 1
            errors.append(f"source record {index} missing region_scope/geography or category_hint/pillar")
            continue
        title = clean_google_rss_title(record.get("title"), record.get("publisher"))
        summary = clean_candidate_text(record.get("summary_or_snippet"))
        publisher = clean_candidate_text(record.get("publisher"))
        combined_text = f"{title} {summary}".lower()
        host = (urlsplit(url).netloc or "").lower()
        dedupe_key = (host, title.lower())
        if dedupe_key in seen_keys:
            diagnostics["rejected_duplicate_or_stale"] += 1
            continue
        seen_keys.add(dedupe_key)
        if any(term in combined_text for term in INVESTOR_ONLY_KEYWORDS):
            diagnostics["rejected_investor_only"] += 1
            continue
        is_bankruptcy_story = any(term in combined_text for term in ("bankrupt", "bankruptcy", "chapter 11", "chapter 7", "chapter 13", "insolvency"))
        has_public_pressure_angle = any(term in combined_text for term in PUBLIC_PRESSURE_KEYWORDS) or (
            "debt" in combined_text and any(term in combined_text for term in ("household", "consumer"))
        )
        if is_bankruptcy_story and not has_public_pressure_angle and "uscourts.gov" not in host:
            diagnostics["rejected_no_public_pressure_angle"] += 1
            continue
        pillar = _normalize_pillar(str(record.get("pillar") or category_hint))
        if pillar not in PILLAR_ORDER:
            pillar = "local_system_strain"
        signal_family = str(record.get("signal_family") or "").strip()
        bankruptcy_subtype = str(record.get("bankruptcy_subtype") or "").strip()
        if is_bankruptcy_story:
            pillar = "financial_distress_pressure"
            signal_family, bankruptcy_subtype = classify_bankruptcy_signal(
                title=title,
                summary=summary,
                category_hint=category_hint,
                source_type=str(record["source_type"]).strip(),
            )
        diagnostics["candidates_accepted"] += 1
        normalized.append(
            {
                "source_record_id": source_record_id,
                "source_id": source_id,
                "title": title,
                "url": url,
                "publisher": publisher,
                "published_at": str(record["published_at"]).strip(),
                "retrieved_at": str(record["retrieved_at"]).strip(),
                "summary_or_snippet": summary,
                "source_type": str(record["source_type"]).strip(),
                "region_scope": region_scope,
                "category_hint": category_hint,
                "pillar": pillar,
                "signal_family": signal_family,
                "bankruptcy_subtype": bankruptcy_subtype,
                "is_official_filings_data": bool("uscourts.gov" in host and any(t in combined_text for t in ("bankruptcy", "filings"))),
                "reliability_tier": str(record["reliability_tier"]).strip(),
                "edition_date": edition_date,
                "dispatch_slug": DISPATCH_SLUG,
                "is_baseline_auto": bool(record.get("is_baseline_auto")),
                "reader_headline": clean_google_rss_title(record.get("reader_headline"), publisher),
                "manual_what_happened": clean_candidate_text(record.get("what_happened")),
                "manual_potential_relevance": clean_candidate_text(record.get("potential_relevance")),
                "manual_who_may_feel_it": clean_candidate_text(record.get("who_may_feel_it")),
                "manual_what_to_watch_next": clean_candidate_text(record.get("what_to_watch_next")),
                "location_scope": _safe_text(record.get("location_scope")),
                "city": _safe_text(record.get("city")),
                "state": _safe_text(record.get("state")),
                "state_code": _safe_text(record.get("state_code")),
                "county": _safe_text(record.get("county")),
                "place_name": _safe_text(record.get("place_name")),
                "state_hint": _safe_text(record.get("state_hint")),
                "affected_people": _safe_text(record.get("affected_people")),
                "pressure_direction": _safe_text(record.get("pressure_direction")),
                "public_pressure_angle": clean_candidate_text(record.get("public_pressure_angle")),
                "manual_human_story_summary": clean_candidate_text(record.get("human_story_summary")),
                "manual_location": clean_candidate_text(record.get("location")),
                "manual_pressure_area": _safe_text(record.get("pressure_area")),
                "manual_source_role": _safe_text(record.get("source_role")).lower(),
                "latitude": _safe_float(record.get("latitude") if record.get("latitude") is not None else record.get("lat")),
                "longitude": _safe_float(
                    record.get("longitude")
                    if record.get("longitude") is not None
                    else record.get("lon")
                    if record.get("lon") is not None
                    else record.get("lng")
                ),
                "linked_data_anchor_ids": _safe_list_of_text(record.get("linked_data_anchor_ids")),
                "key_stat_label": _safe_text(record.get("key_stat_label")),
                "key_stat_value": _safe_text(record.get("key_stat_value")),
                "key_stat_unit": _safe_text(record.get("key_stat_unit")),
                "key_stat_context": _safe_text(record.get("key_stat_context")),
                "key_stat_source_id": _safe_text(record.get("key_stat_source_id")),
                "candidate_collected_on": _safe_text(record.get("candidate_collected_on")),
            }
        )
    warnings.append(f"curation_diagnostics={json.dumps(diagnostics, sort_keys=True)}")
    return normalized, warnings, errors, diagnostics


def classify_bankruptcy_signal(*, title: str, summary: str, category_hint: str, source_type: str) -> tuple[str, str]:
    text = f"{title} {summary} {category_hint} {source_type}".lower()
    if any(t in text for t in ("hospital", "healthcare", "clinic")):
        return "local_service_disruption_bankruptcy", "healthcare"
    if any(t in text for t in ("food", "grocery", "supplier")):
        return "local_service_disruption_bankruptcy", "food_system"
    if any(t in text for t in ("housing", "real estate", "rent")):
        return "local_service_disruption_bankruptcy", "housing"
    if any(t in text for t in ("job", "jobs", "layoff", "employer", "worker")):
        return "employer_bankruptcy_job_risk", "employer_jobs"
    return "bankruptcy_filings", "consumer"


def _reader_facing_summary(source: dict[str, Any]) -> str:
    title = str(source.get("title") or "")
    summary = str(source.get("summary_or_snippet") or "")
    combined = f"{title} {summary}".lower()
    url = str(source.get("url") or "").lower()

    if "snap" in combined or "fns" in combined:
        return (
            "SNAP data helps show whether food assistance remains a major support for households under grocery pressure."
        )
    if "bankrupt" in combined or "chapter 11" in combined or "chapter 7" in combined or "chapter 13" in combined:
        if "uscourts.gov" in url:
            return (
                "Bankruptcy filings are a delayed but concrete sign that households or businesses have run out of easier options."
            )
        return (
            "Bankruptcy-related reporting can indicate rising financial distress for households or businesses, "
            "with spillover risk for jobs and local services."
        )
    cleaned_summary = clean_candidate_text(summary)
    if not cleaned_summary:
        cleaned_summary = clean_candidate_text(title)
    summary_words = [w for w in re.findall(r"[A-Za-z]+", cleaned_summary)]
    summary_title_case_words = sum(1 for w in summary_words if len(w) > 2 and w[:1].isupper() and w[1:].islower())
    summary_looks_source_style = len(summary_words) >= 8 and summary_title_case_words >= max(5, int(len(summary_words) * 0.6))
    if (
        len(cleaned_summary) > 140
        or " - " in cleaned_summary
        or "(" in cleaned_summary
        or " | " in cleaned_summary
        or summary_looks_source_style
    ):
        pillar = _safe_text(source.get("pillar"))
        if pillar == "food_pressure":
            return "Food banks and assistance providers are reporting tighter conditions for households."
        if pillar == "housing_household_cost_pressure":
            return "Housing and monthly bill pressures are increasing strain on household budgets."
        if pillar == "health_access_pressure":
            return "Local developments suggest growing pressure on care access and service continuity."
        if pillar == "labor_income_pressure":
            return "Local job and paycheck developments suggest increasing household pressure."
    return cleaned_summary


def _location_phrase(source: dict[str, Any]) -> str:
    explicit = _safe_text(source.get("manual_location") or source.get("location"))
    if explicit:
        return explicit
    scope = _safe_text(source.get("location_scope"))
    if scope and scope not in {"local", "regional", "national"}:
        return scope
    region_scope = _safe_text(source.get("region_scope"))
    if region_scope.lower() in {"us", "u.s.", "united states"}:
        return "Nationally"
    return region_scope


def _normalize_location_intro(place: str) -> tuple[str, str]:
    cleaned = place.strip()
    if cleaned.lower() == "nationally":
        return ("Nationally", "nationally")
    match = re.match(r"(?i)^multiple counties in ([a-z][a-z .'-]+)$", cleaned)
    if match:
        state = match.group(1).strip()
        return (f"Across multiple {state} counties", "across")
    if cleaned.lower().startswith("multiple counties"):
        return (f"Across {cleaned.lower()}", "across")
    return (cleaned, "in")


def _extract_named_actor(source: dict[str, Any]) -> str:
    title = _safe_text(source.get("title"))
    summary = _safe_text(source.get("summary_or_snippet"))
    for text in (title, summary):
        for pattern in (
            r"\b(SLO Food Bank)\b",
            r"\b(River Hills Community Health Center)\b",
            r"\b(Sacramento City Unified)\b",
            r"\b(state and federal teams)\b",
            r"\b(Wisconsin officials)\b",
        ):
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                return m.group(1)
    publisher = _safe_text(source.get("publisher")).lower()
    if "wisconsin emergency management" in publisher:
        text = f"{title} {summary}".lower()
        if "state and federal teams" in text:
            return "state and federal teams"
        return "state officials"
    return ""


def _remove_location_repetition(base: str, place: str) -> str:
    trimmed = base.strip()
    if not trimmed:
        return trimmed
    lowered = trimmed.lower()
    place_words = [w for w in re.split(r"[\s,]+", place.lower()) if w and len(w) > 2]
    for word in place_words:
        if lowered.startswith(f"a {word} "):
            trimmed = re.sub(rf"(?i)^a\s+{re.escape(word)}\s+", "a ", trimmed).strip()
            lowered = trimmed.lower()
            break
    return trimmed


def _trim_generic_lead_when_actor_present(text: str) -> str:
    trimmed = text.strip()
    patterns = (
        r"(?i)^a\s+[a-z\s-]{1,60}\s+(reported|announced|approved|began|said|warned)\b",
        r"(?i)^an\s+[a-z\s-]{1,60}\s+(reported|announced|approved|began|said|warned)\b",
    )
    for pat in patterns:
        m = re.match(pat, trimmed)
        if m:
            verb = m.group(1)
            return re.sub(pat, verb, trimmed, count=1).strip()
    return trimmed


def _locationized_current_development(source: dict[str, Any]) -> str:
    base = _safe_text(source.get("manual_human_story_summary")) or _safe_text(source.get("manual_what_happened")) or _reader_facing_summary(source)
    if not base:
        return ""
    words = [w for w in re.findall(r"[A-Za-z]+", base)]
    title_case_words = sum(1 for w in words if len(w) > 2 and w[:1].isupper() and w[1:].islower())
    looks_source_style = len(words) >= 8 and title_case_words >= max(5, int(len(words) * 0.6))
    if looks_source_style or " - " in base or "(" in base:
        base = _reader_facing_summary(source)
    base = re.sub(r"\s+", " ", base).strip()
    place = _location_phrase(source)
    affected = _safe_text(source.get("affected_people"))
    affected = re.sub(r"\s+", " ", affected).strip().rstrip(".")
    if affected and affected[:1].isupper() and not affected[:2].isupper():
        affected = affected[:1].lower() + affected[1:]

    def _with_affected(sentence: str) -> str:
        cleaned = sentence.strip().rstrip(".")
        if affected:
            return f"{cleaned}. This may affect {affected}."
        return f"{cleaned}."

    if not place:
        return _with_affected(base)
    if re.match(r"(?i)^(in|across)\s+", base):
        return _with_affected(base)
    intro_place, intro_mode = _normalize_location_intro(place)
    normalized = _remove_location_repetition(base, intro_place)
    if normalized.lower().startswith("in "):
        return _with_affected(base)
    actor = _extract_named_actor(source)
    if "state and federal teams" in normalized.lower():
        actor = "state and federal teams"
    if actor:
        normalized = _trim_generic_lead_when_actor_present(normalized)
        if normalized.lower().startswith("the "):
            normalized = re.sub(r"(?i)^the\s+", "", normalized, count=1)
        # Normalize redundant actor fragments before deciding whether to prepend actor.
        if "state and federal teams" in actor.lower():
            normalized = re.sub(r"(?i)^wisconsin officials\s+", "", normalized, count=1).strip()
            normalized = re.sub(r"(?i)^state and federal teams\s+wisconsin officials\s+", "state and federal teams ", normalized, count=1).strip()
            normalized = re.sub(r"(?i)^state and federal teams\s+state officials\s+", "state and federal teams ", normalized, count=1).strip()
        if "wisconsin officials" in actor.lower():
            normalized = re.sub(r"(?i)^state officials\s+", "wisconsin officials ", normalized, count=1).strip()

        if normalized.lower().startswith(actor.lower()) or (
            "officials" in actor.lower() and normalized.lower().startswith("wisconsin officials")
        ):
            normalized = normalized
        else:
            normalized = f"{actor} {normalized}"
        # Mid-sentence actor capitalization cleanup.
        normalized = re.sub(r"^State and federal teams\b", "state and federal teams", normalized)
    if intro_mode == "nationally":
        sentence = f"Nationally, {normalized}"
    elif intro_mode == "across":
        sentence = f"{'In' if intro_mode == 'in' else 'Across'} {intro_place.removeprefix('Across ').removeprefix('In ')}, {normalized}"
    else:
        sentence = f"In {intro_place}, {normalized}"
    return _with_affected(sentence)


def _reader_facing_headline(source: dict[str, Any]) -> str:
    manual = clean_google_rss_title(source.get("reader_headline"), source.get("publisher"))
    if manual:
        words = [w for w in re.findall(r"[A-Za-z]+", manual)]
        title_case_words = sum(1 for w in words if len(w) > 2 and w[:1].isupper() and w[1:].islower())
        looks_source_style = len(words) >= 8 and title_case_words >= max(5, int(len(words) * 0.6))
        if len(manual) <= 95 and " - " not in manual and "(" not in manual and not looks_source_style:
            return manual
    text = f"{source.get('title', '')} {source.get('summary_or_snippet', '')}".lower()
    if "snap" in text or "fns" in text:
        return "Food help remains one of the clearest signs of household strain"
    if "bankrupt" in text or "chapter 11" in text or "chapter 7" in text or "chapter 13" in text:
        return "Bankruptcy filings help show where debt stress is breaking through"
    if "shelter" in text or "housing" in text or "rent" in text:
        return "Housing costs are still the budget pressure that can crowd out everything else"
    if "medicaid" in text or "chip" in text:
        return "Health coverage data helps show who may be exposed if access changes"
    if "employment situation" in text or "payroll" in text or "unemployment" in text or "jobs" in text:
        return "Jobs and paychecks remain the first line of defense against household pressure"
    if "noaa" in text or "ncei" in text or "climate" in text or "drought" in text:
        return "Weather and climate conditions can turn into cost pressure"
    if "fema" in text or "disaster declaration" in text:
        return "Disaster declarations show where local systems may be stretched"
    pillar = _safe_text(source.get("pillar"))
    location = _safe_text(source.get("manual_location") or source.get("location_scope"))
    if location.lower() in {"us", "u.s.", "united states"}:
        location = "nationally"
    location_phrase = f" in {location}" if location and location.lower() != "nationally" else (" nationally" if location else "")
    if pillar == "food_pressure":
        return f"Food support networks{location_phrase} report rising demand".strip()
    if pillar == "labor_income_pressure":
        return f"Local job and paycheck strain{location_phrase} is affecting households".strip()
    if pillar == "housing_household_cost_pressure":
        return f"Housing and bill pressure{location_phrase} is squeezing household budgets".strip()
    if pillar == "health_access_pressure":
        return f"Health access pressure{location_phrase} is disrupting care".strip()
    if pillar == "financial_distress_pressure":
        return f"Financial distress signals{location_phrase} suggest higher household risk".strip()
    if pillar == "environmental_pressure":
        return f"Weather and environmental stress{location_phrase} is adding household pressure".strip()
    if pillar == "local_system_strain":
        return f"Local systems{location_phrase} are showing strain".strip()
    return "Source-backed pressure signal"


def _potential_relevance(source: dict[str, Any], pillar: str) -> str:
    manual = _safe_text(source.get("manual_potential_relevance"))
    if manual:
        return manual
    if pillar == "food_pressure":
        return "This signal may show up later as tighter grocery tradeoffs, higher pantry demand, and less room in monthly budgets."
    if pillar == "financial_distress_pressure":
        return "This signal can show where debt stress could break into missed payments, service disruption, or local job risk."
    if pillar == "housing_household_cost_pressure":
        return "This is a baseline for watching whether housing and utility costs keep crowding out other essentials."
    if pillar == "health_access_pressure":
        return "This signal may matter if households could face coverage gaps, delayed care, or higher out-of-pocket pressure."
    if pillar == "labor_income_pressure":
        return "This is a signal of whether paycheck stability can still absorb rising costs."
    if pillar == "environmental_pressure":
        return "This can show up later as food, energy, insurance, and repair cost pressure."
    if pillar == "policy_implementation":
        return "This is a baseline for watching whether support can reach people quickly when pressure rises."
    return "This signal may indicate where local systems could have less buffer if conditions worsen."


def _is_low_quality_public_item(story: dict[str, Any]) -> bool:
    combined = " ".join(
        _safe_text(story.get(field)).lower()
        for field in ("what_happened", "potential_relevance", "who_may_feel_it", "what_to_watch_next")
    )
    if not combined:
        return True
    for phrase in LOW_QUALITY_PUBLIC_ITEM_PHRASES:
        if phrase in combined and all(token not in combined for token in ("may", "could", "can", "signal", "watch")):
            return True
    return False


def _extract_layoff_stat_from_text(source: dict[str, Any]) -> tuple[str, str, str] | None:
    text = f"{_safe_text(source.get('title'))} {_safe_text(source.get('summary_or_snippet'))}".lower()
    if "layoff" not in text and "laid off" not in text:
        return None
    for pattern in (r"\b(\d{2,6})\s+employees?\s+laid off\b", r"\b(\d{2,6})\s+laid off\b", r"\b(\d{2,6})\s+layoffs?\b"):
        match = re.search(pattern, text)
        if match:
            return ("Reported layoffs", match.group(1), "workers")
    return None


def _choose_key_stat_for_story(story_sources: list[dict[str, Any]], story: dict[str, Any]) -> dict[str, str] | None:
    source_by_record_id = {str(source.get("source_record_id") or ""): source for source in story_sources}
    source_by_source_id = {str(source.get("source_id") or ""): source for source in story_sources}
    for source in story_sources:
        label = _safe_text(source.get("key_stat_label"))
        value = _safe_text(source.get("key_stat_value"))
        source_ref = _safe_text(source.get("key_stat_source_id"))
        if label and value and source_ref:
            stat_source = source_by_record_id.get(source_ref) or source_by_source_id.get(source_ref)
            if stat_source:
                unit = _safe_text(source.get("key_stat_unit"))
                context = _safe_text(source.get("key_stat_context"))
                return {
                    "label": label,
                    "value": value,
                    "unit": unit,
                    "context": context,
                    "source_record_id": _safe_text(stat_source.get("source_record_id")),
                }
    for source in story_sources:
        source_id = _safe_text(source.get("source_id"))
        if not source_id:
            continue
        extracted = _extract_layoff_stat_from_text(source)
        if extracted is None:
            continue
        label, value, unit = extracted
        return {
            "label": label,
            "value": value,
            "unit": unit,
            "context": "",
            "source_record_id": _safe_text(source.get("source_record_id")),
        }
    return None


def _classify_item_type(source: dict[str, Any]) -> str:
    source_type = str(source.get("source_type") or "").lower()
    text = f"{source.get('title', '')} {source.get('summary_or_snippet', '')}".lower()
    current_week_markers = (
        "layoff", "warn", "closure", "cuts", "disruption", "strike", "shutoff", "flood", "fire", "storm",
        "heat wave", "bankruptcy filing", "declaration", "emergency", "service reduction", "food bank", "spike",
    )
    if any(token in source_type for token in ("news", "press", "filing", "bulletin", "alert")):
        return "current_week_development"
    if any(token in text for token in current_week_markers):
        return "current_week_development"
    return "baseline_gauge"


def _classify_source_role(source: dict[str, Any]) -> str:
    manual = _safe_text(source.get("manual_source_role")).lower()
    if manual in {"human_story", "data_anchor", "watchlist_signal"}:
        return manual
    item_type = _classify_item_type(source)
    source_type = _safe_text(source.get("source_type")).lower()
    text = f"{source.get('title', '')} {source.get('summary_or_snippet', '')}".lower()
    if item_type == "baseline_gauge":
        return "data_anchor"
    if "dataset" in source_type or "statistics" in source_type:
        return "data_anchor"
    if any(t in text for t in ("watch", "monitor", "outlook", "forecast")):
        return "watchlist_signal"
    return "human_story"


def _item_sort_key(story: dict[str, Any]) -> tuple[int, int]:
    quality = _safe_text(story.get("brief_quality"))
    rank = {
        "story_plus_data": 0,
        "official_release_only": 1,
        "baseline_only": 2,
        "watchlist_only": 3,
    }.get(quality, 4)
    return (rank, int(story.get("score") or 0) * -1)


def _dedupe_headline(headline: str, seen: dict[str, int]) -> str:
    key = headline.strip().lower()
    seen[key] = seen.get(key, 0) + 1
    if seen[key] == 1:
        return headline
    return f"{headline} ({seen[key]})"


def _build_data_context_summary(data_anchors: list[dict[str, Any]], pillar: str) -> str:
    if not data_anchors:
        return "No data anchor was available for this item this week."
    labels = ", ".join(anchor["title"] for anchor in data_anchors[:3])
    if pillar == "food_pressure":
        return f"Data context from {labels} is a baseline for tracking whether food pressure is broadening."
    if pillar == "financial_distress_pressure":
        return f"Data context from {labels} helps anchor whether debt stress may be moving from warning signs to hard outcomes."
    if pillar == "housing_household_cost_pressure":
        return f"Data context from {labels} is a baseline for whether housing and bill pressure remains persistent."
    if pillar == "health_access_pressure":
        return f"Data context from {labels} helps track whether coverage and access pressure could widen."
    if pillar == "labor_income_pressure":
        return f"Data context from {labels} helps show whether job and paycheck stability is holding."
    return f"Data context from {labels} provides baseline evidence for this pressure area."


def _validate_manual_human_story_records(sources: list[dict[str, Any]], root: Path) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    known_record_ids = {str(source.get("source_record_id") or "").strip() for source in sources}
    known_source_ids = {str(source.get("source_id") or "").strip() for source in sources}
    canonical_by_pillar = canonical_valid_anchor_ids_by_pillar(root, tuple(PILLAR_ORDER))
    canonical_all = {source_id for values in canonical_by_pillar.values() for source_id in values}
    for source in sources:
        role = _classify_source_role(source)
        if role != "human_story":
            continue
        source_id = _safe_text(source.get("source_id") or source.get("source_record_id"))
        required_fields = {
            "url": _safe_text(source.get("url")),
            "publisher": _safe_text(source.get("publisher")),
            "title": _safe_text(source.get("title")),
            "published_at": _safe_text(source.get("published_at")),
            "summary_or_snippet": _safe_text(source.get("summary_or_snippet")),
            "public_pressure_angle": _safe_text(source.get("public_pressure_angle")),
        }
        missing = sorted([field for field, value in required_fields.items() if not value])
        if missing:
            errors.append(f"human_story record {source_id} missing required fields: {', '.join(missing)}")
        resolved_ids: list[str] = []
        story_pillar = _normalize_pillar(_safe_text(source.get("pillar") or source.get("category_hint")))
        for linked_id in source.get("linked_data_anchor_ids", []):
            candidate = _safe_text(linked_id)
            if not candidate:
                continue
            if candidate in known_record_ids:
                resolved_ids.append(candidate)
                continue
            if candidate in canonical_all:
                resolved_ids.append(candidate)
                continue
            if candidate in known_source_ids:
                linked_source = next((item for item in sources if _safe_text(item.get('source_id')) == candidate), None)
                if linked_source:
                    resolved_ids.append(_safe_text(linked_source.get("source_record_id")))
                    continue
            available_hint_pool = list(canonical_by_pillar.get(story_pillar, []))
            if not available_hint_pool:
                available_hint_pool = sorted(canonical_all)
            hint = ", ".join(available_hint_pool[:6]) if available_hint_pool else "none"
            errors.append(
                f"human_story record {source_id} has unknown linked_data_anchor_id: {candidate} "
                f"(pillar={story_pillar or 'unknown'}; available anchors: {hint})"
            )
        source["linked_data_anchor_ids"] = resolved_ids
        if not resolved_ids:
            warnings.append(f"human_story record {source_id} has no linked data anchors")
    return warnings, errors


def curate_stories(sources: list[dict[str, Any]], edition_date: str, generated_at: str) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    source_by_id = {str(source["source_record_id"]): source for source in sources}
    grouped: dict[str, list[dict[str, Any]]] = {pillar: [] for pillar in PILLAR_ORDER}
    for source in sources:
        grouped.setdefault(str(source.get("pillar") or "local_system_strain"), []).append(source)

    headline_seen: dict[str, int] = {}
    for index, pillar in enumerate(PILLAR_ORDER, start=1):
        bucket = grouped.get(pillar, [])
        if not bucket:
            continue
        human_story_sources = [s for s in bucket if _classify_source_role(s) == "human_story"]
        human_story_sources = sorted(
            human_story_sources,
            key=lambda s: (
                _safe_text(s.get("reliability_tier")).startswith("official"),
                bool(_safe_text(s.get("manual_location"))),
                bool(_safe_text(s.get("affected_people"))),
                len(_safe_text(s.get("public_pressure_angle"))),
            ),
            reverse=True,
        )[:3]
        data_anchor_sources = [s for s in bucket if _classify_source_role(s) == "data_anchor"]
        watchlist_sources = [s for s in bucket if _classify_source_role(s) == "watchlist_signal"]

        linked_anchors: list[dict[str, Any]] = []
        for src in human_story_sources:
            for linked_id in src.get("linked_data_anchor_ids", []):
                linked = source_by_id.get(str(linked_id))
                if linked and linked not in linked_anchors:
                    linked_anchors.append(linked)
        if linked_anchors:
            data_anchor_sources = [*linked_anchors, *[s for s in data_anchor_sources if s not in linked_anchors]]

        primary_source = human_story_sources[0] if human_story_sources else (data_anchor_sources[0] if data_anchor_sources else watchlist_sources[0])
        curation_reason = {
            "food_pressure": "food assistance dependency",
            "financial_distress_pressure": "bankruptcy/financial distress baseline",
            "housing_household_cost_pressure": "shelter cost pressure",
            "health_access_pressure": "health coverage/access pressure",
            "labor_income_pressure": "job market pressure",
            "environmental_pressure": "drought/disaster/local-system strain",
            "local_system_strain": "drought/disaster/local-system strain",
            "policy_implementation": "policy implementation pressure",
        }.get(pillar, "local-system strain baseline")
        has_human = bool(human_story_sources)
        has_data = bool(data_anchor_sources)
        if has_human and has_data:
            brief_quality = "story_plus_data"
        elif has_data and any(_classify_item_type(s) == "current_week_development" for s in data_anchor_sources):
            brief_quality = "official_release_only"
        elif has_data:
            brief_quality = "baseline_only"
        else:
            brief_quality = "watchlist_only"

        human_summary = ""
        if human_story_sources:
            human_summary = _locationized_current_development(human_story_sources[0])

        headline = _dedupe_headline(_reader_facing_headline(primary_source), headline_seen) if primary_source else _dedupe_headline(PILLAR_HEADINGS[pillar], headline_seen)
        combined_sources = [*human_story_sources, *data_anchor_sources, *watchlist_sources][:6]
        combined_ids = [str(s["source_record_id"]) for s in combined_sources]
        combined_urls = [str(s["url"]) for s in combined_sources]
        combined_publishers = [str(s["publisher"]) for s in combined_sources]
        location_scope = _safe_text(primary_source.get("manual_location") or primary_source.get("location_scope")) if primary_source else ""
        key_stat = _choose_key_stat_for_story(combined_sources, primary_source or {})
        stories.append(
            {
                "story_id": f"american-pressure-story-{edition_date}-{index:03d}",
                "title": headline,
                "summary": _safe_text(primary_source.get("summary_or_snippet")) if primary_source else "",
                "category": CATEGORY_BY_PILLAR.get(str(pillar), "local-system-strain"),
                "pillar": pillar,
                "curation_reason": curation_reason,
                "item_type": "current_week_development" if has_human else "baseline_gauge",
                "source_role_counts": {
                    "human_story": len(human_story_sources),
                    "data_anchor": len(data_anchor_sources),
                    "watchlist_signal": len(watchlist_sources),
                },
                "brief_quality": brief_quality,
                "score": 100 - index,
                "included_in_public_summary": True,
                "source_record_ids": combined_ids,
                "source_urls": combined_urls,
                "publisher_names": combined_publishers,
                "human_story_source_ids": [str(s["source_record_id"]) for s in human_story_sources],
                "data_anchor_source_ids": [str(s["source_record_id"]) for s in data_anchor_sources],
                "watchlist_source_ids": [str(s["source_record_id"]) for s in watchlist_sources],
                "reader_headline": headline,
                "human_story_summary": human_summary,
                "data_context_summary": _build_data_context_summary(data_anchor_sources, pillar),
                "what_happened": _safe_text(primary_source.get("manual_what_happened")) or _reader_facing_summary(primary_source),
                "potential_relevance": _potential_relevance(primary_source, pillar),
                "who_may_feel_it": _safe_text(primary_source.get("manual_who_may_feel_it")) or PILLAR_GUIDANCE[pillar]["who_may_feel_it"],
                "what_to_watch_next": _safe_text(primary_source.get("manual_what_to_watch_next")) or PILLAR_GUIDANCE[pillar]["watch_next"],
                "location_scope": location_scope,
                "affected_people": _safe_text(primary_source.get("affected_people")),
                "pressure_area": _safe_text(primary_source.get("manual_pressure_area")),
                "pressure_direction": _safe_text(primary_source.get("pressure_direction")),
                "public_pressure_angle": _safe_text(primary_source.get("public_pressure_angle")),
                "key_stat": key_stat,
                "generated_at": generated_at,
            }
        )
    return stories


def _pillar_counts(items: list[dict[str, Any]], field: str = "pillar") -> dict[str, int]:
    out = {pillar: 0 for pillar in PILLAR_ORDER}
    for item in items:
        p = _normalize_pillar(str(item.get(field) or ""))
        if p in out:
            out[p] += 1
    return out


def _coverage(stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, int], dict[str, int]]:
    source_counts = _pillar_counts(sources)
    story_counts = _pillar_counts(stories)
    present = [pillar for pillar in PILLAR_ORDER if source_counts[pillar] > 0 or story_counts[pillar] > 0]
    missing = [pillar for pillar in PILLAR_ORDER if pillar not in present]
    return present, missing, source_counts, story_counts


def _item_type_counts(stories: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"baseline_gauge": 0, "current_week_development": 0, "watchlist_item": 0}
    for story in stories:
        key = str(story.get("item_type") or "baseline_gauge")
        if key not in counts:
            counts[key] = 0
        counts[key] += 1
    return counts


def _source_role_counts(sources: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"human_story": 0, "data_anchor": 0, "watchlist_signal": 0}
    for source in sources:
        role = _classify_source_role(source)
        counts[role] = counts.get(role, 0) + 1
    return counts


def _brief_quality_counts(stories: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"story_plus_data": 0, "official_release_only": 0, "baseline_only": 0, "watchlist_only": 0}
    for story in stories:
        quality = _safe_text(story.get("brief_quality")) or "watchlist_only"
        counts[quality] = counts.get(quality, 0) + 1
    return counts


def render_edition_html(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]], source_mode: str, *, display_date_range: str | None = None) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    pillars_present, pillars_missing, _, _ = _coverage(stories, sources)
    display = display_date_range or edition_date
    chunks: list[str] = ["<h1>The American Pressure Dispatch</h1>", f"<p class=\"eyebrow\">Weekly briefing / {display}</p>"]
    item_counts = _item_type_counts(stories)
    current_developments = item_counts.get("current_week_development", 0)
    data_context_briefs = item_counts.get("baseline_gauge", 0)
    baseline_only_briefs = sum(1 for story in stories if _safe_text(story.get("brief_quality")) == "baseline_only")
    chunks.append("<h2>This Week at a Glance</h2>")
    chunks.append(
        "<p>This week’s sources point to pressure around groceries, debt, housing costs, health coverage, jobs, and local disruptions. "
        "Some items are real-world developments; others are official data points that help show where pressure may be building.</p>"
    )
    chunks.append(
        f"<p>In this edition, {current_developments} items are current developments and {data_context_briefs} items are data context. "
        "Watch for whether local disruptions spread, whether job and benefit access weakens, and whether cost pressure broadens.</p>"
    )
    collection_gap_pillars = [str(p) for p in stories[0].get("collection_gap_pillars", [])] if stories else []
    if collection_gap_pillars:
        readable = ", ".join(PILLAR_HEADINGS.get(p, p) for p in collection_gap_pillars)
        chunks.append(f"<p><strong>Collection gaps this week:</strong> {html.escape(readable)}. No current-development source was captured for this pillar.</p>")
    if baseline_only_briefs > 0:
        chunks.append("<p><strong>Some items are baseline data points. They help track pressure, but they do not by themselves prove what changed this week.</strong></p>")

    stories_by_pillar: dict[str, list[dict[str, Any]]] = {pillar: [] for pillar in PILLAR_ORDER}
    for story in stories:
        stories_by_pillar.setdefault(story.get("pillar", "local_system_strain"), []).append(story)

    for pillar in PILLAR_ORDER:
        chunks.append(f"<h2>{html.escape(PILLAR_HEADINGS[pillar])}</h2>")
        items = sorted(stories_by_pillar.get(pillar, []), key=_item_sort_key)
        if not items:
            chunks.append("<p>No source-backed signal in this edition.</p>")
            continue
        guide = PILLAR_GUIDANCE[pillar]
        for story in items:
            chunks.append(f"<article><h3>{html.escape(story['title'])}</h3>")
            human_story_summary = _safe_text(story.get("human_story_summary"))
            if human_story_summary:
                chunks.append(f"<p><strong>Current Development:</strong> {html.escape(human_story_summary)}</p>")
            elif _safe_text(story.get("item_type")) == "baseline_gauge":
                chunks.append("<p><strong>Current Development:</strong> No current-development source was captured for this pillar.</p>")
            chunks.append(f"<p><strong>Data Context:</strong> {html.escape(str(story.get('data_context_summary') or guide['why_it_matters']))}</p>")
            key_stat = story.get("key_stat") if isinstance(story.get("key_stat"), dict) else None
            if key_stat:
                text = f"{_safe_text(key_stat.get('label'))}: {_safe_text(key_stat.get('value'))}"
                unit = _safe_text(key_stat.get("unit"))
                if unit:
                    text += f" {unit}"
                context = _safe_text(key_stat.get("context"))
                if context:
                    text += f" ({context})"
                chunks.append(f"<p><strong>Key number:</strong> {html.escape(text)}</p>")
            chunks.append(f"<p><strong>Potential Relevance:</strong> {html.escape(str(story.get('potential_relevance') or guide['why_it_matters']))}</p>")
            chunks.append(f"<p><strong>Who May Feel It:</strong> {html.escape(str(story.get('who_may_feel_it') or guide['who_may_feel_it']))}</p>")
            chunks.append(f"<p><strong>What to Watch Next:</strong> {html.escape(str(story.get('what_to_watch_next') or guide['watch_next']))}</p>")
            human_ids = [str(x) for x in story.get("human_story_source_ids", [])]
            data_ids = [str(x) for x in story.get("data_anchor_source_ids", [])]
            watch_ids = [str(x) for x in story.get("watchlist_source_ids", [])]
            chunks.append("<p><strong>Sources:</strong></p>")
            if human_ids:
                links = []
                for source_id in human_ids:
                    source = source_by_id[source_id]
                    links.append(f'<a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a> ({html.escape(source["publisher"])})')
                chunks.append(f"<p><em>Real-life story sources: {'; '.join(links)}</em></p>")
            if data_ids:
                links = []
                for source_id in data_ids:
                    source = source_by_id[source_id]
                    links.append(f'<a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a> ({html.escape(source["publisher"])})')
                chunks.append(f"<p><em>Data/context sources: {'; '.join(links)}</em></p>")
            if watch_ids:
                links = []
                for source_id in watch_ids:
                    source = source_by_id[source_id]
                    links.append(f'<a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a> ({html.escape(source["publisher"])})')
                chunks.append(f"<p><em>Watchlist sources: {'; '.join(links)}</em></p>")
            chunks.append("</article>")

    chunks.append("<h2>What We’re Watching Next</h2>")
    chunks.append("<p>Watch for developments in layoffs/WARN notices, local closures, food-bank demand, utility burden, benefit access changes, and local budget stress.</p>")
    chunks.append("<h2>What We Still Do Not Know</h2>")
    if pillars_missing:
        chunks.append("<ul>" + "".join(f"<li>{html.escape(PILLAR_HEADINGS[p])}</li>" for p in pillars_missing) + "</ul>")
    else:
        chunks.append("<p>All pillar families had at least one source-backed signal this week.</p>")

    chunks.append("<h2>Sources</h2>")
    chunks.append(f"<p>Source mode: {html.escape(source_mode)}. Public items are generated from traceable source links only; no unsupported claims are added.</p>")
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/american-pressure/")}
  <main class=\"briefing\">
    <section class=\"hero\">
      <img class=\"hero-logo\" src=\"../../assets/american-pressure-logo.png\" alt=\"The American Pressure Dispatch\">
    </section>
    {' '.join(chunks)}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {edition_date}", f"{BASE_URL}/american-pressure/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def render_edition_markdown(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]], *, display_date_range: str | None = None) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    display = display_date_range or edition_date
    lines = [f"# {DISPATCH_NAME}", "", f"Weekly briefing / {display}", ""]
    for story in stories:
        lines.append(f"## {story['title']}")
        lines.append(story.get("human_story_summary") or story.get("data_context_summary") or story["summary"])
        for source_id in story.get("source_record_ids", []):
            source = source_by_id[source_id]
            lines.append(f"Source: [{source['title']}]({source['url']}) ({source['publisher']}, {source['published_at']})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _public_prose_guardrail(stories: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    fields = (
        "title",
        "summary",
        "reader_headline",
        "human_story_summary",
        "data_context_summary",
        "what_happened",
        "potential_relevance",
        "who_may_feel_it",
        "what_to_watch_next",
    )
    for story in stories:
        story_id = _safe_text(story.get("story_id")) or "unknown-story"
        item_type = _safe_text(story.get("item_type"))
        requires_human_prose = item_type == "current_week_development"
        if requires_human_prose:
            for req in ("reader_headline", "human_story_summary", "potential_relevance", "who_may_feel_it"):
                if not _safe_text(story.get(req)):
                    errors.append(f"public prose missing required field in {story_id}.{req}")
        for field in fields:
            value = _safe_text(story.get(field))
            sanitized = sanitize_public_prose(value)
            if value and sanitized != value:
                errors.append(f"public prose quality failed in {story_id}.{field}: requires source-backed completion or removal")
            if value and contains_forbidden_public_markup(value):
                errors.append(f"public prose contains forbidden markup/token in {story_id}.{field}")
            if value and any(bad in value.lower() for bad in ("structure, rejecting", "news.google.com/rss/articles")):
                errors.append(f"public prose quality failed in {story_id}.{field}")
            for violation in find_public_prose_violations(value):
                errors.append(f"public prose quality failed in {story_id}.{field}: {violation}")
    return errors


def _sanitize_story_public_prose(stories: list[dict[str, Any]]) -> None:
    fields = (
        "summary",
        "reader_headline",
        "human_story_summary",
        "data_context_summary",
        "what_happened",
        "potential_relevance",
        "who_may_feel_it",
        "what_to_watch_next",
    )
    for story in stories:
        for field in fields:
            value = _safe_text(story.get(field))
            if not value:
                continue
            story[field] = sanitize_public_prose(value)


def discover_edition_dates(site_root: Path) -> list[str]:
    editions_root = site_root / DISPATCH_SLUG / "editions"
    if not editions_root.exists():
        return []
    return sorted(
        (
            path.name
            for path in editions_root.iterdir()
            if (
                path.is_dir()
                and DATE_RE.match(path.name)
                and (path / "index.html").exists()
                and public_edition_is_listable(site_root, DISPATCH_SLUG, path.name)
            )
        ),
        reverse=True,
    )


def render_archive_index_rss(root: Path, edition_date: str, dry_run: bool, wrote: list[str]) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(slug=DISPATCH_SLUG, name=DISPATCH_NAME, edition_date=edition_date, tagline=DISPATCH_TAGLINE, logo="american-pressure-logo.png", sources=[], stories=[], detail_artifacts=[])
    dates = discover_edition_dates(site_root)
    # Keep public listing bounded to the requested weekly build point.
    dates = [d for d in dates if d <= edition_date]
    if edition_date not in dates:
        dates = sorted([*dates, edition_date], reverse=True)
    dispatch_root = site_root / DISPATCH_SLUG
    write_text(dispatch_root / "index.html", render_dispatch_index_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "archive.html", render_archive_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "rss.xml", render_rss_for_dates(dispatch, dates), dry_run, wrote)
    if dates:
        latest = dates[0]
        latest_edition = site_root / DISPATCH_SLUG / "editions" / latest
        latest_manifest = read_json(latest_edition / "edition_manifest.json") if (latest_edition / "edition_manifest.json").exists() else {}
        latest_sources = read_json(latest_edition / "sources_manifest.json") if (latest_edition / "sources_manifest.json").exists() else []
        latest_curation = read_json(latest_edition / "curation_manifest.json") if (latest_edition / "curation_manifest.json").exists() else {}
        stories = latest_curation.get("stories") if isinstance(latest_curation, dict) else []
        if not isinstance(stories, list):
            stories = []
        map_data = _build_map_data(latest, latest_manifest, latest_sources, stories)
        write_json(dispatch_root / "map" / "map_data.json", map_data, dry_run, wrote)
        diagnostics_payload = map_data.get("location_extraction_diagnostics")
        if isinstance(diagnostics_payload, dict):
            write_json(dispatch_root / "map" / "location_extraction_diagnostics.json", diagnostics_payload, dry_run, wrote)
        write_text(dispatch_root / "map" / "index.html", render_map_html(latest, map_data), dry_run, wrote)
        write_text(dispatch_root / "dashboard" / "index.html", render_dashboard_html(root, latest), dry_run, wrote)
        # Re-render listing pages after dashboard creation so index can include the dashboard link.
        write_text(dispatch_root / "index.html", render_dispatch_index_for_dates(dispatch, dates, site_root), dry_run, wrote)
        write_text(dispatch_root / "archive.html", render_archive_for_dates(dispatch, dates, site_root), dry_run, wrote)
        write_text(dispatch_root / "rss.xml", render_rss_for_dates(dispatch, dates), dry_run, wrote)


def _slugify_heading(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return cleaned or "section"


def _dashboard_status_for_pillar(
    pillar: str,
    *,
    source_count: int,
    story_count: int,
    current_development_count: int,
    human_story_count: int,
    collection_gap_pillars: list[str],
) -> str:
    if pillar in collection_gap_pillars:
        return "Collection gap"
    if source_count <= 0 and story_count <= 0:
        return "No signal captured"
    if current_development_count > 0 and story_count > 0:
        return "Active"
    if story_count > 0 and current_development_count <= 0 and human_story_count <= 0:
        return "Data only"
    return "Data only"


def _dashboard_quality_label(status: str, brief_quality: str) -> str:
    if status == "Active":
        return "Story plus data"
    if status == "Collection gap":
        return "Needs human current development"
    if status == "No signal captured":
        return "No public signal captured"
    if brief_quality == "official_release_only":
        return "Official baseline only"
    return "Data context only"


def _safe_manifest_counts(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in payload.items():
        try:
            out[str(key)] = int(value or 0)
        except Exception:  # noqa: BLE001
            out[str(key)] = 0
    return out


def render_dashboard_html(root: Path, edition_date: str) -> str:
    site_edition = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    manifest_path = site_edition / "edition_manifest.json"
    curation_path = site_edition / "curation_manifest.json"
    sources_path = site_edition / "sources_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    curation = read_json(curation_path) if curation_path.exists() else {}
    sources = read_json(sources_path) if sources_path.exists() else []
    stories = curation.get("stories") if isinstance(curation, dict) else []
    if not isinstance(stories, list):
        stories = []
    if not isinstance(sources, list):
        sources = []

    week_range = _safe_text(manifest.get("display_date_range")) or _display_date_range(edition_date)
    source_count = int(manifest.get("source_count") or len(sources) or 0)
    story_count = int(manifest.get("story_count") or len(stories) or 0)
    story_plus_data_count = int(manifest.get("story_plus_data_count") or 0)
    collection_gap_pillars = [str(item) for item in (manifest.get("collection_gap_pillars") or [])]
    current_counts = _safe_manifest_counts(manifest.get("current_development_count_by_pillar"))
    human_counts = _safe_manifest_counts(manifest.get("human_story_count_by_pillar"))
    source_counts = _safe_manifest_counts(manifest.get("source_count_by_pillar"))
    story_counts = _safe_manifest_counts(manifest.get("story_count_by_pillar"))
    data_anchor_counts: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        pillar = _normalize_pillar(_safe_text(source.get("pillar")))
        if not pillar:
            continue
        if _classify_source_role(source) == "data_anchor":
            data_anchor_counts[pillar] = data_anchor_counts.get(pillar, 0) + 1

    story_by_pillar: dict[str, dict[str, Any]] = {}
    for story in stories:
        if isinstance(story, dict):
            pillar = _normalize_pillar(_safe_text(story.get("pillar")))
            if pillar and pillar not in story_by_pillar:
                story_by_pillar[pillar] = story

    metric_current = sum(current_counts.get(p, 0) for p in PILLAR_ORDER)
    metric_data_only = sum(1 for p in PILLAR_ORDER if story_counts.get(p, 0) > 0 and human_counts.get(p, 0) <= 0 and current_counts.get(p, 0) <= 0)
    written_dispatch_href = f"/american-pressure/editions/{edition_date}/"
    source_ledger_href = f"/american-pressure/editions/{edition_date}/sources_manifest.json"
    map_href = "/american-pressure/map/"
    archive_href = "/american-pressure/archive.html"
    map_payload = _build_map_data(edition_date, manifest, sources, stories)
    map_summary = (
        f"Map currently shows {int(map_payload.get('pin_count') or 0)} pinned source-backed locations, "
        f"{int(map_payload.get('national_records_count') or 0)} national records, and "
        f"{int(map_payload.get('unmapped_records_count') or 0)} unmapped records."
    )

    metric_cards = f"""
      <div class="apd-metrics">
        <article class="apd-metric"><h3>Sources Reviewed</h3><p>{source_count}</p></article>
        <article class="apd-metric"><h3>Public Signals</h3><p>{story_count}</p></article>
        <article class="apd-metric"><h3>Story+Data Signals</h3><p>{story_plus_data_count}</p></article>
        <article class="apd-metric"><h3>Collection Gaps</h3><p>{len(collection_gap_pillars)}</p></article>
        <article class="apd-metric"><h3>Current Developments</h3><p>{metric_current}</p></article>
        <article class="apd-metric"><h3>Data-Only Signals</h3><p>{metric_data_only}</p></article>
      </div>
    """

    cards: list[str] = []
    card_payloads: list[dict[str, str]] = []
    for pillar in PILLAR_ORDER:
        heading = PILLAR_HEADINGS.get(pillar, pillar)
        source_ct = int(source_counts.get(pillar, 0) or 0)
        story_ct = int(story_counts.get(pillar, 0) or 0)
        current_ct = int(current_counts.get(pillar, 0) or 0)
        human_ct = int(human_counts.get(pillar, 0) or 0)
        data_ct = int(data_anchor_counts.get(pillar, 0) or 0)
        status = _dashboard_status_for_pillar(
            pillar,
            source_count=source_ct,
            story_count=story_ct,
            current_development_count=current_ct,
            human_story_count=human_ct,
            collection_gap_pillars=collection_gap_pillars,
        )
        story = story_by_pillar.get(pillar, {})
        brief_quality = _safe_text(story.get("brief_quality"))
        quality = _dashboard_quality_label(status, brief_quality)
        what_this_means = _safe_text(PILLAR_DASHBOARD_SUMMARIES.get(pillar)) or _safe_text(PILLAR_GUIDANCE.get(pillar, {}).get("why_it_matters")) or "No reader-facing takeaway is available yet."
        section_anchor = _slugify_heading(heading)
        badge_class = {
            "Active": "apd-status-active",
            "Data only": "apd-status-data",
            "Collection gap": "apd-status-gap",
            "No signal captured": "apd-status-none",
        }.get(status, "apd-status-none")
        written_section_href = f"{written_dispatch_href}#{section_anchor}"
        card_payload = {
            "pillar": pillar,
            "heading": heading,
            "status": status,
            "status_class": badge_class,
            "counts": f"Sources: {source_ct} | Stories: {story_ct} | Current developments: {current_ct}",
            "supporting_counts": f"Human stories: {human_ct} | Data anchors: {data_ct}",
            "quality": f"Quality: {quality}",
            "what_this_means": what_this_means,
            "written_href": written_section_href,
            "ledger_href": source_ledger_href,
            "gap_note": "Collection gap: no current-development source was captured for this pillar this week." if status == "Collection gap" else "",
        }
        card_payloads.append(card_payload)
        cards.append(
            f"""        <article class="apd-card {badge_class}" id="{html.escape(pillar)}" data-pillar="{html.escape(pillar)}" data-heading="{html.escape(heading)}" data-status="{html.escape(status)}" data-status-class="{html.escape(badge_class)}" data-counts="{html.escape(card_payload['counts'])}" data-supporting-counts="{html.escape(card_payload['supporting_counts'])}" data-quality="{html.escape(card_payload['quality'])}" data-what="{html.escape(what_this_means)}" data-written-href="{html.escape(written_section_href)}" data-ledger-href="{html.escape(source_ledger_href)}" data-gap-note="{html.escape(card_payload['gap_note'])}">
          <h3>{html.escape(heading)} <span class="apd-status {badge_class}">{html.escape(status)}</span></h3>
          <p class="apd-big">{current_ct}</p>
          <p class="apd-small">Current developments</p>
          <p class="apd-small">{html.escape(card_payload['counts'])}</p>
          <p class="apd-small">{html.escape(card_payload['supporting_counts'])}</p>
          <p class="apd-small">{html.escape(card_payload['quality'])}</p>
          <p>{html.escape(what_this_means)}</p>
          <p class="apd-links"><a href="#{html.escape(pillar)}" data-focus-pillar="{html.escape(pillar)}">Focus this pressure area</a> | <a href="{written_section_href}">Read written section</a> | <a href="{source_ledger_href}">View sources</a></p>
        </article>"""
        )

    default_pillar = card_payloads[0] if card_payloads else None
    detail_panel = ""
    if default_pillar:
        detail_panel = f"""
    <section class="apd-detail" id="apd-selected-panel" aria-live="polite">
      <p class="eyebrow">Selected Pressure Area</p>
      <h2 id="apd-selected-heading">{html.escape(default_pillar['heading'])}</h2>
      <p><span id="apd-selected-status" class="apd-status {html.escape(default_pillar['status_class'])}">{html.escape(default_pillar['status'])}</span></p>
      <p id="apd-selected-counts" class="apd-small">{html.escape(default_pillar['counts'])}</p>
      <p id="apd-selected-supporting-counts" class="apd-small">{html.escape(default_pillar['supporting_counts'])}</p>
      <p id="apd-selected-what">{html.escape(default_pillar['what_this_means'])}</p>
      <p id="apd-selected-gap" class="apd-small">{html.escape(default_pillar['gap_note'])}</p>
      <p class="apd-links"><a id="apd-selected-written" href="{html.escape(default_pillar['written_href'])}">Read written section</a> | <a id="apd-selected-ledger" href="{html.escape(default_pillar['ledger_href'])}">View sources</a></p>
      <p><a href="#all" id="apd-show-all">Show all pressure areas</a></p>
    </section>
"""

    body = f"""{header(DISPATCH_NAME, "../", "../archive.html", "/american-pressure/")}
  <main class="home apd-home">
    <section class="hero">
      <img class="hero-logo" src="../assets/american-pressure-logo.png" alt="{html.escape(DISPATCH_NAME)}">
    </section>
    <p class="eyebrow">American Pressure Dashboard</p>
    <h1>American Pressure Dashboard</h1>
    <p class="lede">Weekly briefing range: {html.escape(week_range)}</p>
    <p><a href="{written_dispatch_href}">Read written dispatch</a> | <a href="{source_ledger_href}">View source ledger</a> | <a href="{map_href}">Open map</a> | <a href="{archive_href}">Open archive</a></p>
    <p class="apd-small">Built from {source_count} source records and {story_count} public signals. Trend unavailable until prior-week comparison is added.</p>
    {metric_cards}
    {detail_panel}
    <h2>Pressure by Pillar</h2>
    <section class="apd-grid" id="apd-grid">
{''.join(cards)}
    </section>
    <p class="apd-small">{html.escape(map_summary)} <a href="{map_href}">View American Pressure map.</a></p>
  </main>
{footer("../")}"""
    return page(
        "American Pressure Dashboard",
        f"{BASE_URL}/american-pressure/dashboard/",
        "../assets/site.css",
        body + """
<style>
.apd-home { width: min(1120px, calc(100% - 24px)); }
.apd-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0 20px; }
.apd-metric { border: 1px solid var(--border); border-radius: 12px; background: #f4f8fb; padding: 14px; box-shadow: 0 6px 16px rgba(14, 30, 37, .06); }
.apd-metric h3 { margin: 0 0 6px; border-top: 0; padding-top: 0; font-size: .85rem; letter-spacing: .04em; text-transform: uppercase; color: var(--navy); }
.apd-metric p { margin: 0; font-size: 2rem; line-height: 1; font-weight: 800; color: var(--ink); }
.apd-detail { border: 1px solid var(--border); border-left: 6px solid var(--navy); border-radius: 12px; background: #f8fbfd; padding: 14px 16px; margin-bottom: 18px; }
.apd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.apd-card { border: 1px solid var(--border); border-left: 6px solid #4b5f64; border-radius: 12px; background: var(--panel); padding: 14px; transition: transform .16s ease, box-shadow .16s ease, opacity .16s ease; }
.apd-card:hover, .apd-card:focus-within { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(14, 30, 37, .1); }
.apd-card h3 { margin: 0 0 8px; border-top: 0; padding-top: 0; font-size: 1.05rem; }
.apd-big { margin: 0; font-size: 2.1rem; line-height: 1; font-weight: 800; color: #102a43; }
.apd-status { display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }
.apd-status-active { background: #d8efe6; color: #1f5d49; border-left-color: #1f5d49; }
.apd-status-data { background: #ddecf1; color: #0f2a33; border-left-color: #0f2a33; }
.apd-status-gap { background: #f9e5d3; color: #7a3c00; border-left-color: #7a3c00; }
.apd-status-none { background: #edf1f2; color: #4b5f64; }
.apd-card.apd-status-active { background: #f4fbf7; }
.apd-card.apd-status-data { background: #f2f8fb; }
.apd-card.apd-status-gap { background: #fff7f1; }
.apd-small { color: var(--muted); font-size: .88rem; margin: 0 0 8px; }
.apd-links { margin: 12px 0 0; }
.apd-grid.is-filtered .apd-card { opacity: .28; }
.apd-grid.is-filtered .apd-card.is-selected { opacity: 1; box-shadow: 0 12px 26px rgba(14, 30, 37, .16); transform: translateY(-3px); }
@media (max-width: 760px) {
  .apd-home { width: calc(100% - 16px); }
  .apd-grid { grid-template-columns: 1fr; }
}
</style>
<script>
(function () {
  var grid = document.getElementById("apd-grid");
  var panel = document.getElementById("apd-selected-panel");
  if (!grid || !panel) return;
  var cards = Array.prototype.slice.call(grid.querySelectorAll(".apd-card[data-pillar]"));
  var heading = document.getElementById("apd-selected-heading");
  var status = document.getElementById("apd-selected-status");
  var counts = document.getElementById("apd-selected-counts");
  var supportingCounts = document.getElementById("apd-selected-supporting-counts");
  var what = document.getElementById("apd-selected-what");
  var gap = document.getElementById("apd-selected-gap");
  var written = document.getElementById("apd-selected-written");
  var ledger = document.getElementById("apd-selected-ledger");
  var showAll = document.getElementById("apd-show-all");

  function getFromHash() {
    var value = window.location.hash.replace(/^#/, "");
    return value === "all" ? "" : value;
  }
  function updateHash(pillar) {
    var target = pillar ? "#" + pillar : "#all";
    if (window.location.hash !== target) window.location.hash = target;
  }
  function selectCard(pillar) {
    var selected = null;
    cards.forEach(function (card) {
      var match = pillar && card.getAttribute("data-pillar") === pillar;
      card.classList.toggle("is-selected", !!match);
      if (match) selected = card;
    });
    if (!selected) {
      grid.classList.remove("is-filtered");
      return;
    }
    grid.classList.add("is-filtered");
    heading.textContent = selected.getAttribute("data-heading") || "";
    status.className = "apd-status " + (selected.getAttribute("data-status-class") || "apd-status-none");
    status.textContent = selected.getAttribute("data-status") || "";
    counts.textContent = selected.getAttribute("data-counts") || "";
    supportingCounts.textContent = selected.getAttribute("data-supporting-counts") || "";
    what.textContent = selected.getAttribute("data-what") || "";
    gap.textContent = selected.getAttribute("data-gap-note") || "";
    written.setAttribute("href", selected.getAttribute("data-written-href") || "#");
    ledger.setAttribute("href", selected.getAttribute("data-ledger-href") || "#");
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  grid.addEventListener("click", function (event) {
    var focusLink = event.target.closest("[data-focus-pillar]");
    if (!focusLink) return;
    event.preventDefault();
    updateHash(focusLink.getAttribute("data-focus-pillar") || "");
  });
  if (showAll) {
    showAll.addEventListener("click", function (event) {
      event.preventDefault();
      updateHash("");
    });
  }
  window.addEventListener("hashchange", function () {
    selectCard(getFromHash());
  });
  selectCard(getFromHash());
})();
</script>
""",
        DISPATCH_NAME,
    )


def run_american_pressure_dispatch(
    root: Path,
    edition_date: str,
    *,
    publish: bool,
    dry_run: bool,
    from_manual_sources: bool,
    source_mode: str = "both",
    init_manual_sources: bool = False,
    init_daily_candidates: bool = False,
    allow_future: bool = False,
    include_approved_candidates: bool = False,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    validate_not_future_date(edition_date, allow_future=allow_future)
    mode = source_mode.strip().lower()
    if from_manual_sources and mode == "both":
        mode = "manual"
    if mode not in SOURCE_MODES:
        raise ValueError(f"unsupported --source-mode: {source_mode}")

    wrote: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    if init_manual_sources:
        path = init_manual_sources_file(root, edition_date, dry_run=dry_run, wrote=wrote)
        return {"ok": True, "dispatch_slug": DISPATCH_SLUG, "edition_date": edition_date, "manual_source_path": str(path), "source_count": 0, "story_count": 0, "generated": False, "initialized_manual_sources": True, "archive_updated": False, "rss_updated": False, "pages_repo_updated": False, "pushed": False, "wrote": wrote, "warnings": warnings, "errors": errors}
    if init_daily_candidates:
        path = init_daily_candidates_file(root, edition_date, dry_run=dry_run, wrote=wrote)
        return {"ok": True, "dispatch_slug": DISPATCH_SLUG, "edition_date": edition_date, "daily_candidate_path": str(path), "source_count": 0, "story_count": 0, "generated": False, "initialized_daily_candidates": True, "archive_updated": False, "rss_updated": False, "pages_repo_updated": False, "pushed": False, "wrote": wrote, "warnings": warnings, "errors": errors}

    manual_path = manual_source_path(root, edition_date)
    raw_records: list[dict[str, Any]] = []
    if mode in {"manual", "both"}:
        if manual_path.exists():
            _, manual_records = load_manual_sources(root, edition_date)
            raw_records.extend(manual_records)
        elif mode == "manual":
            _ = load_manual_sources(root, edition_date)
        else:
            warnings.append(f"manual sources not found for {edition_date}; continuing with auto baseline sources only")
        if mode == "both":
            daily_candidate_records, daily_candidate_warnings = load_daily_candidate_sources(
                root,
                edition_date,
                include_approved_only=include_approved_candidates,
            )
            raw_records.extend(daily_candidate_records)
            warnings.extend(daily_candidate_warnings)
    if mode in {"auto", "both"}:
        raw_records.extend(load_auto_sources(root, edition_date))
    if from_manual_sources and mode == "auto":
        warnings.append("--from-manual-sources ignored when --source-mode auto")

    generated_at = utc_now()
    week_start_date = _week_start_date(edition_date)
    display_date_range = _display_date_range(edition_date)
    sources, source_warnings, source_errors, diagnostics = normalize_sources(raw_records, edition_date)
    warnings.extend(source_warnings)
    errors.extend(source_errors)
    human_story_warnings, human_story_errors = _validate_manual_human_story_records(sources, root)
    warnings.extend(human_story_warnings)
    errors.extend(human_story_errors)

    stories = curate_stories(sources, edition_date, generated_at)
    pillars_present, pillars_missing, source_count_by_pillar, story_count_by_pillar = _coverage(stories, sources)
    item_type_counts = _item_type_counts(stories)
    source_role_counts = _source_role_counts(sources)
    brief_quality_counts = _brief_quality_counts(stories)
    briefs_with_human_story = sum(1 for story in stories if story.get("source_role_counts", {}).get("human_story", 0) > 0)
    briefs_with_data_anchor = sum(1 for story in stories if story.get("source_role_counts", {}).get("data_anchor", 0) > 0)
    baseline_only_briefs = sum(1 for story in stories if _safe_text(story.get("brief_quality")) == "baseline_only")
    missing_human_story_pillars = sorted(
        [
            pillar for pillar in PILLAR_ORDER
            if source_count_by_pillar.get(pillar, 0) > 0
            and not any(
                story.get("pillar") == pillar and story.get("source_role_counts", {}).get("human_story", 0) > 0
                for story in stories
            )
        ]
    )
    current_development_count_by_pillar = _pillar_counts([s for s in sources if _classify_item_type(s) == "current_week_development"])
    human_story_count_by_pillar = _pillar_counts([s for s in sources if _classify_source_role(s) == "human_story"])
    missing_required_current_development_pillars = sorted([p for p in IMPORTANT_CURRENT_DEVELOPMENT_PILLARS if human_story_count_by_pillar.get(p, 0) == 0])
    collection_gap_pillars = list(missing_required_current_development_pillars)
    searched_pillars = sorted(REQUIRED_CURRENT_DEVELOPMENT_SEARCH_TARGETS.keys())
    story_plus_data_count = int(brief_quality_counts.get("story_plus_data", 0) or 0)
    baseline_only_count = int(brief_quality_counts.get("baseline_only", 0) or 0)
    for story in stories:
        story["collection_gap_pillars"] = collection_gap_pillars

    _sanitize_story_public_prose(stories)
    prose_errors = _public_prose_guardrail(stories)
    if prose_errors:
        errors.extend(prose_errors)

    if not sources:
        errors.append(f"No valid source-backed American Pressure records found for {edition_date}; refusing zero-source edition.")
    if len(pillars_present) < 4:
        warnings.append("coverage_weak: fewer than 4 represented pillars")
    if len(stories) < 4:
        warnings.append("coverage_weak: fewer than 4 public stories")
    if set(pillars_present).issubset({"food_pressure", "environmental_pressure"}):
        warnings.append("coverage_weak: SNAP/weather-only pattern")
    if item_type_counts.get("current_week_development", 0) == 0:
        warnings.append("coverage_watchlist: no current_week_development records; add manual weekly developments")
    if missing_required_current_development_pillars:
        warnings.append(
            "coverage_watchlist: missing important current-development pillars: "
            + ",".join(missing_required_current_development_pillars)
        )
        warnings.append("coverage_watchlist: No current-development source was captured for this pillar.")
    low_quality_story_ids = [str(story.get("story_id")) for story in stories if _is_low_quality_public_item(story)]
    if low_quality_story_ids:
        warnings.append(f"curation_validation: low-quality public item prose for story_ids={','.join(low_quality_story_ids)}")

    html_content = render_edition_html(edition_date, stories, sources, mode, display_date_range=display_date_range)
    markdown_content = render_edition_markdown(edition_date, stories, sources, display_date_range=display_date_range)
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "week_start_date": week_start_date,
        "week_end_date": edition_date,
        "display_date_range": display_date_range,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/american-pressure/editions/{edition_date}/",
        "source_count": len(sources),
        "story_count": len(stories),
        "pillars_present": pillars_present,
        "pillars_missing": pillars_missing,
        "source_count_by_pillar": source_count_by_pillar,
        "story_count_by_pillar": story_count_by_pillar,
        "rejected_no_public_pressure_angle": diagnostics["rejected_no_public_pressure_angle"],
        "rejected_investor_only": diagnostics["rejected_investor_only"],
        "rejected_duplicate_or_stale": diagnostics["rejected_duplicate_or_stale"],
        "rejected_missing_required_fields": diagnostics["rejected_missing_required_fields"],
        "is_free_public": True,
        "public_exposed": True,
        "has_detail_tier": False,
        "source_mode": mode,
        "item_type_counts": item_type_counts,
        "source_role_counts": source_role_counts,
        "brief_quality_counts": brief_quality_counts,
        "searched_pillars": searched_pillars,
        "required_current_development_search_targets": REQUIRED_CURRENT_DEVELOPMENT_SEARCH_TARGETS,
        "current_development_count_by_pillar": current_development_count_by_pillar,
        "human_story_count_by_pillar": human_story_count_by_pillar,
        "missing_required_current_development_pillars": missing_required_current_development_pillars,
        "collection_gap_pillars": collection_gap_pillars,
        "story_plus_data_count": story_plus_data_count,
        "baseline_only_count": baseline_only_count,
        "briefs_with_human_story": briefs_with_human_story,
        "briefs_with_data_anchor": briefs_with_data_anchor,
        "baseline_only_briefs": baseline_only_briefs,
        "missing_human_story_pillars": missing_human_story_pillars,
        "baseline_only_edition": item_type_counts.get("baseline_gauge", 0) > 0 and item_type_counts.get("current_week_development", 0) == 0,
        "low_quality_story_ids": low_quality_story_ids,
        "future_enhancements": {
            "american_pressure_map": "Show current-week developments with location data filtered by pressure area; do not map national baseline gauges unless they include state/county geography."
        },
        "warnings": warnings,
        "errors": errors,
    }

    curation_manifest = {
        "stories": stories,
        "edition_date": edition_date,
        "week_start_date": week_start_date,
        "week_end_date": edition_date,
        "display_date_range": display_date_range,
        "pillars_present": pillars_present,
        "pillars_missing": pillars_missing,
        "source_count_by_pillar": source_count_by_pillar,
        "story_count_by_pillar": story_count_by_pillar,
        "rejected_no_public_pressure_angle": diagnostics["rejected_no_public_pressure_angle"],
        "rejected_investor_only": diagnostics["rejected_investor_only"],
        "rejected_duplicate_or_stale": diagnostics["rejected_duplicate_or_stale"],
        "rejected_missing_required_fields": diagnostics["rejected_missing_required_fields"],
        "item_type_counts": item_type_counts,
        "source_role_counts": source_role_counts,
        "brief_quality_counts": brief_quality_counts,
        "searched_pillars": searched_pillars,
        "required_current_development_search_targets": REQUIRED_CURRENT_DEVELOPMENT_SEARCH_TARGETS,
        "current_development_count_by_pillar": current_development_count_by_pillar,
        "human_story_count_by_pillar": human_story_count_by_pillar,
        "missing_required_current_development_pillars": missing_required_current_development_pillars,
        "collection_gap_pillars": collection_gap_pillars,
        "story_plus_data_count": story_plus_data_count,
        "baseline_only_count": baseline_only_count,
        "briefs_with_human_story": briefs_with_human_story,
        "briefs_with_data_anchor": briefs_with_data_anchor,
        "baseline_only_briefs": baseline_only_briefs,
        "missing_human_story_pillars": missing_human_story_pillars,
        "low_quality_story_ids": low_quality_story_ids,
        "future_enhancements": {
            "american_pressure_map": "Show current-week developments with location data filtered by pressure area; do not map national baseline gauges unless they include state/county geography."
        },
    }

    if errors:
        return {"ok": False, "dispatch_slug": DISPATCH_SLUG, "edition_date": edition_date, "manual_source_path": str(manual_path), "source_count": len(sources), "story_count": len(stories), "generated": False, "archive_updated": False, "rss_updated": False, "pages_repo_updated": False, "pushed": False, "wrote": wrote, "warnings": warnings, "errors": errors, "pillars_present": pillars_present, "pillars_missing": pillars_missing, "source_count_by_pillar": source_count_by_pillar, "story_count_by_pillar": story_count_by_pillar}

    archive_updated = False
    rss_updated = False
    dispatch_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date
    site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    write_text(dispatch_dir / "index.html", html_content, dry_run, wrote)
    write_text(dispatch_dir / "edition.html", html_content, dry_run, wrote)
    write_text(dispatch_dir / "edition.md", markdown_content, dry_run, wrote)
    write_json(dispatch_dir / "edition_manifest.json", edition_manifest, dry_run, wrote)
    write_json(dispatch_dir / "sources_manifest.json", sources, dry_run, wrote)
    write_json(dispatch_dir / "curation_manifest.json", curation_manifest, dry_run, wrote)
    write_text(site_dir / "index.html", html_content, dry_run, wrote)
    write_json(site_dir / "edition_manifest.json", edition_manifest, dry_run, wrote)
    write_json(site_dir / "sources_manifest.json", sources, dry_run, wrote)
    write_json(site_dir / "curation_manifest.json", curation_manifest, dry_run, wrote)
    dashboard_content = render_dashboard_html(root, edition_date)
    write_text(site_dir / "dashboard.html", dashboard_content, dry_run, wrote)
    if force_regenerate and not dry_run:
        regen_targets = [
            dispatch_dir / "index.html",
            dispatch_dir / "edition.html",
            dispatch_dir / "edition.md",
            dispatch_dir / "edition_manifest.json",
            dispatch_dir / "sources_manifest.json",
            dispatch_dir / "curation_manifest.json",
            site_dir / "index.html",
            site_dir / "dashboard.html",
            site_dir / "edition_manifest.json",
            site_dir / "sources_manifest.json",
            site_dir / "curation_manifest.json",
        ]
        for target in regen_targets:
            if target.exists():
                target.touch()

    for asset in ("site.css", "american-pressure-logo.png", "bluefern.png"):
        copy_file(root / "assets" / asset, root / "output" / "site" / DISPATCH_SLUG / "assets" / asset, dry_run, wrote, warnings)
    if publish:
        render_archive_index_rss(root, edition_date, dry_run, wrote)
        archive_updated = True
        rss_updated = True

    return {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "manual_source_path": str(manual_path),
        "source_count": len(sources),
        "story_count": len(stories),
        "generated": True,
        "archive_updated": archive_updated,
        "rss_updated": rss_updated,
        "pages_repo_updated": False,
        "pushed": False,
        "wrote": wrote,
        "warnings": warnings,
        "errors": errors,
        "pillars_present": pillars_present,
        "pillars_missing": pillars_missing,
        "source_count_by_pillar": source_count_by_pillar,
        "story_count_by_pillar": story_count_by_pillar,
        "source_mode": mode,
        "include_approved_candidates": include_approved_candidates,
        "force_regenerate": force_regenerate,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly American Pressure editions.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--publish", action="store_true", help="Update public index/archive/rss after edition generation.")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without changing files.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Legacy flag; manual mode now controlled by --source-mode.")
    parser.add_argument("--source-mode", choices=sorted(SOURCE_MODES), default="both", help="Source input mode: manual, auto, or both.")
    parser.add_argument("--init-manual-sources", action="store_true", help="Create starter manual source file for --date when missing.")
    parser.add_argument("--init-daily-candidates", action="store_true", help="Create starter daily candidate file for --date when missing.")
    parser.add_argument("--allow-future", action="store_true", help="Allow future --date values (disabled by default).")
    parser.add_argument("--include-approved-candidates", action="store_true", help="Include only approved daily candidates from the 7-day window when --source-mode both.")
    parser.add_argument("--force-regenerate", action="store_true", help="Force rewrite/timestamp refresh for edition output files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_american_pressure_dispatch(
            ROOT,
            args.date,
            publish=bool(args.publish),
            dry_run=bool(args.dry_run),
            from_manual_sources=bool(args.from_manual_sources),
            source_mode=str(args.source_mode),
            init_manual_sources=bool(args.init_manual_sources),
            init_daily_candidates=bool(args.init_daily_candidates),
            allow_future=bool(args.allow_future),
            include_approved_candidates=bool(args.include_approved_candidates),
            force_regenerate=bool(args.force_regenerate),
        )
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())


