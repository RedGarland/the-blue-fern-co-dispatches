from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date as date_type, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.bluesky_post import maybe_post_food_line_dispatch_to_bluesky
from bluefern_dispatches.generator import BASE_URL, discover_public_edition_dates, footer, header as site_header, page
from bluefern_dispatches.food_line_sources import (
    FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK,
    clean_food_line_public_evidence_excerpt,
    collect_food_line_auto_sources,
    canonical_url,
    classify_food_line_source_purpose,
    evaluate_food_line_pressure,
    food_line_is_supported_geography,
    normalize_title,
    refresh_food_line_pressure_registry_source_purpose,
    _url_path_date,
    validate_food_line_source_freshness,
)
from bluefern_dispatches.food_line_discovery_bridge import run_food_line_discovery_intake_bridge
from bluefern_dispatches.food_line_discovery_expansion import read_food_line_discovery_expansion_audit
from bluefern_dispatches.food_line_discovery_expansion import run_food_line_discovery_expansion
from bluefern_dispatches.podcast_feed import write_food_line_podcast_feed
from bluefern_dispatches.tts_provider import synthesize_speech_with_diagnostics

PAGES_REPO = ROOT / "bluefern-dispatches-pages"
PAGES_BRANCH = "gh-pages"
DISPATCH_SLUG = "food-line"
DISPATCH_NAME = "Food Line Dispatch"
FOOD_LINE_SOCIAL_IMAGE_ASSET = "food-line-dispatch-social.png"
FOOD_LINE_SOCIAL_IMAGE_URL = f"{BASE_URL}/food-line/assets/{FOOD_LINE_SOCIAL_IMAGE_ASSET}"
FOOD_LINE_SOCIAL_IMAGE_ALT = "The Food Line Dispatch social card from The Blue Fern Co., with wheat, a U.S. map outline, and the subtitle Source-backed daily food-pressure briefing."
FOOD_LINE_PAGE_DESCRIPTION = "Source-backed daily Food Line dispatch covering pantry demand, benefit disruption, and food-access pressure across the United States."
MAP_RENDERED_COUNT_RE = re.compile(r'data-rendered-marker-count="(\d+)"')
_FOOD_LINE_STATE_NAMES = {
    "AZ": "Arizona",
    "OH": "Ohio",
    "LA": "Louisiana",
    "OK": "Oklahoma",
    "SC": "South Carolina",
    "TN": "Tennessee",
    "TX": "Texas",
    "VA": "Virginia",
}


def _food_line_discovery_expansion_audit(root: Path, date: str) -> dict[str, Any]:
    try:
        audit = read_food_line_discovery_expansion_audit(root, date)
    except Exception:
        return {}
    return audit if isinstance(audit, dict) else {}


def _food_line_discovery_no_current_update_metadata(
    edition_mode: str,
    discovery_bridge_result: dict[str, Any],
) -> tuple[bool, str]:
    discovery_expansion_used = bool(discovery_bridge_result.get("discovery_expansion_used"))
    if edition_mode != "no_current_update" or not discovery_expansion_used:
        return False, ""
    return True, str(discovery_bridge_result.get("discovery_no_current_update_reason") or "").strip() or "No discovery candidates were retained."


def _food_line_no_current_update_public_label() -> str:
    return "No qualifying update"


def _food_line_no_current_update_blocked_freshness_status(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    if not normalized:
        return False
    if normalized in FOOD_LINE_NO_CURRENT_UPDATE_BLOCKED_FRESHNESS_STATUSES:
        return True
    return normalized.startswith("blocked_insufficient")


def _food_line_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def evaluate_food_line_no_current_update_publication_policy(
    *,
    edition_mode: str,
    collector_result: dict[str, Any] | None,
    discovery_gap_check: dict[str, Any] | None,
    discovery_expansion_used: bool,
    source_freshness_status: str,
    news_item_count: int,
    local_signal_count: int,
    state_signal_count: int,
    discovery_gap_unreviewed_likely_qualifying_count: int | None,
) -> dict[str, Any]:
    metrics = {
        "collector_ok": bool((collector_result or {}).get("ok")),
        "collector_source_count": _food_line_int((collector_result or {}).get("source_count")),
        "news_item_count": int(news_item_count),
        "local_signal_count": int(local_signal_count),
        "state_signal_count": int(state_signal_count),
        "local_state_signal_count": int(local_signal_count) + int(state_signal_count),
        "discovery_gap_run": bool((discovery_gap_check or {}).get("run")),
        "discovery_expansion_used": bool(discovery_expansion_used),
        "discovery_gap_unreviewed_likely_qualifying_count": discovery_gap_unreviewed_likely_qualifying_count,
        "source_freshness_status": str(source_freshness_status or "").strip(),
        "min_collector_source_count": FOOD_LINE_NO_CURRENT_UPDATE_MIN_COLLECTOR_SOURCE_COUNT,
        "min_news_item_count": FOOD_LINE_NO_CURRENT_UPDATE_MIN_NEWS_ITEM_COUNT,
        "min_local_state_signal_count": FOOD_LINE_NO_CURRENT_UPDATE_MIN_LOCAL_STATE_SIGNAL_COUNT,
    }
    result = {
        "allowed": False,
        "status": "not_applicable",
        "reasons": [],
        "metrics": metrics,
    }
    if edition_mode != "no_current_update":
        return result

    reasons: list[str] = []
    collector_source_count = metrics["collector_source_count"]
    if not metrics["collector_ok"]:
        reasons.append("source collection did not run successfully")
    if collector_source_count is None:
        reasons.append("collector source_count is missing")
    elif collector_source_count < FOOD_LINE_NO_CURRENT_UPDATE_MIN_COLLECTOR_SOURCE_COUNT:
        reasons.append(
            f"collector source_count {collector_source_count} is below the minimum "
            f"{FOOD_LINE_NO_CURRENT_UPDATE_MIN_COLLECTOR_SOURCE_COUNT}"
        )

    coverage_ok = (
        collector_source_count is not None
        and collector_source_count >= FOOD_LINE_NO_CURRENT_UPDATE_MIN_COLLECTOR_SOURCE_COUNT
        and (
            metrics["news_item_count"] >= FOOD_LINE_NO_CURRENT_UPDATE_MIN_NEWS_ITEM_COUNT
            or metrics["local_state_signal_count"] >= FOOD_LINE_NO_CURRENT_UPDATE_MIN_LOCAL_STATE_SIGNAL_COUNT
        )
    )
    if not coverage_ok:
        reasons.append("monitoring coverage was insufficient for a public no-qualifying-update edition")

    if not (metrics["discovery_gap_run"] or metrics["discovery_expansion_used"]):
        reasons.append("discovery-gap or equivalent expanded discovery did not run")

    if discovery_gap_unreviewed_likely_qualifying_count is None:
        reasons.append("unreviewed likely qualifying discovery candidate count is missing")
    elif discovery_gap_unreviewed_likely_qualifying_count > 0:
        reasons.append(
            f"{discovery_gap_unreviewed_likely_qualifying_count} unreviewed likely qualifying discovery candidate"
            f"{'s remain' if discovery_gap_unreviewed_likely_qualifying_count != 1 else ' remains'}"
        )

    if _food_line_no_current_update_blocked_freshness_status(metrics["source_freshness_status"]):
        reasons.append(
            f"source freshness status {metrics['source_freshness_status']} blocks public no-qualifying-update publication"
        )

    result["allowed"] = not reasons
    result["status"] = "allowed" if not reasons else "blocked"
    result["reasons"] = reasons
    return result


def _food_line_no_current_update_policy_freshness_status(
    *,
    future_date_blocked: bool,
    no_current_update_candidate: bool,
    stale_public_story_count: int,
    public_rendered: bool,
    discovery_gap_check: dict[str, Any] | None,
    discovery_bridge_result: dict[str, Any] | None,
) -> str:
    if future_date_blocked:
        return "future_date_blocked"
    if no_current_update_candidate:
        gap_validated = bool((discovery_gap_check or {}).get("public_no_qualifying_update_validated"))
        expansion_validated = bool((discovery_bridge_result or {}).get("public_no_qualifying_update_validated"))
        if gap_validated or expansion_validated:
            return "passed_no_qualifying_update"
        if stale_public_story_count > 0:
            return "blocked_insufficient_fresh_current_stories"
        return "blocked_insufficient_current_story_sources"
    if public_rendered:
        return "passed" if not stale_public_story_count else "passed_with_stale_exclusions"
    return "blocked_insufficient_current_story_sources"


def header(
    brand: str,
    root_prefix: str,
    archive_href: str | None = None,
    section_href: str | None = None,
    *,
    nav_slugs: tuple[str, ...] | None = None,
) -> str:
    nav = nav_slugs or ("gaza", "cascadia", "food-line")
    return site_header(brand, root_prefix, archive_href, section_href, nav_slugs=nav)


def _food_line_page(title: str, canonical: str, css_href: str, body: str) -> str:
    return page(
        title,
        canonical,
        css_href,
        body,
        DISPATCH_NAME,
        description=FOOD_LINE_PAGE_DESCRIPTION,
        og_image=FOOD_LINE_SOCIAL_IMAGE_URL,
        og_image_alt=FOOD_LINE_SOCIAL_IMAGE_ALT,
    )


DISPATCH_DISPLAY_NAME = "The Food Line Dispatch"
FOOD_LINE_LOGO_ASSET = "food-line-logo.png"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_ITEMS = 5
MIN_FAMILIES = 2
MIN_LOCAL = 3
MIN_USABLE = 3
FOOD_LINE_FRESHNESS_WINDOW_DAYS = 3
FOOD_LINE_NO_CURRENT_UPDATE_MIN_COLLECTOR_SOURCE_COUNT = 10
FOOD_LINE_NO_CURRENT_UPDATE_MIN_NEWS_ITEM_COUNT = 5
FOOD_LINE_NO_CURRENT_UPDATE_MIN_LOCAL_STATE_SIGNAL_COUNT = 5
FOOD_LINE_NO_CURRENT_UPDATE_BLOCKED_FRESHNESS_STATUSES = {
    "blocked_insufficient_fresh_current_stories",
    "blocked_insufficient_current_story_sources",
}
FOOD_LINE_CATEGORY_COLORS: dict[str, str] = {
    "acute strain / service disruption": "#9a4b4b",
    "elevated demand": "#b6784f",
    "summer meal / child nutrition": "#5d7f62",
    "senior hunger": "#6d6287",
    "rural access": "#3f5878",
    "benefit disruption": "#5b6f8a",
    "context / monitoring only": "#61717c",
}
US_STATE_CENTROIDS: dict[str, tuple[float, float]] = {
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
    "IA": (41.878003, -93.097702),
    "ID": (44.068202, -114.742041),
    "IL": (40.633125, -89.398528),
    "IN": (40.551217, -85.602364),
    "KS": (39.011902, -98.484246),
    "KY": (37.839333, -84.270018),
    "LA": (30.984298, -91.962333),
    "MA": (42.407211, -71.382437),
    "MD": (39.045755, -76.641271),
    "ME": (45.253783, -69.445469),
    "MI": (44.314844, -85.602364),
    "MN": (46.729553, -94.6859),
    "MO": (37.964253, -91.831833),
    "MS": (32.354668, -89.398528),
    "MT": (46.879682, -110.362566),
    "NC": (35.759573, -79.0193),
    "ND": (47.551493, -101.002012),
    "NE": (41.492537, -99.901813),
    "NH": (43.193852, -71.572395),
    "NJ": (40.058324, -74.405661),
    "NM": (34.51994, -105.87009),
    "NV": (38.80261, -116.419389),
    "NY": (43.299428, -74.217933),
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
    "VA": (37.431573, -78.656894),
    "VT": (44.558803, -72.577841),
    "WA": (47.751074, -120.740139),
    "WI": (43.78444, -88.787868),
    "WV": (38.597626, -80.454903),
    "WY": (43.075968, -107.290284),
    "DC": (38.907192, -77.036871),
}
US_NATIONAL_CENTER = (39.8283, -98.5795)
CITY_CENTROIDS: dict[str, tuple[float, float]] = {
    "seattle, wa": (47.6062, -122.3321),
    "dallas, tx": (32.7767, -96.797),
    "new york city, ny": (40.7128, -74.006),
}
COUNTY_CENTROIDS: dict[str, tuple[float, float]] = {}
DAILY_PRIORITY_CATEGORIES = {
    "acute strain / service disruption": 50,
    "benefit disruption": 45,
    "elevated demand": 40,
    "summer meal / child nutrition": 35,
    "senior hunger": 30,
    "rural access": 25,
    "context / monitoring only": 10,
}
DAILY_PRIORITY_FAMILIES = {
    "local_reporting": 20,
    "state_official": 18,
    "provider_signal": 16,
    "food_bank_provider": 15,
    "school_meals_child_nutrition": 14,
    "senior_meals": 12,
    "federal_official": 10,
    "policy_research": 8,
    "economic_data": 6,
}
LOCAL_SIGNAL_FAMILIES = {"local_reporting", "state_official", "food_bank_provider", "school_meals_child_nutrition", "disaster_emergency", "rural_access", "senior_meals"}
BASELINE_FAMILIES = {"economic_data"}
POLICY_FAMILIES = {"policy_research", "federal_official", "state_official"}
RESOURCE_FAMILIES = {"food_bank_provider", "school_meals_child_nutrition", "senior_meals"}
NON_MAP_SIGNAL_ROLES = {"data_anchor_signal", "research_signal", "institutional_context_signal"}
PUBLIC_INCLUSION_PRESSURE_TYPES = {
    "demand_strain",
    "pantry_demand",
    "food_bank_inventory_shortage",
    "operating_cost_strain",
    "fuel_cost_strain",
    "volunteer_capacity_strain",
    "benefit_access_decline",
    "snap_enrollment_decline",
    "benefit_disruption",
    "distribution_cancellation",
    "school_meal_access_pressure",
    "disaster_food_access_pressure",
    "household_food_insecurity_data_signal",
}
PUBLIC_POLICY_ACCESS_PRESSURE_TYPES = {
    "benefit_disruption",
    "benefit_access_decline",
    "snap_enrollment_decline",
    "access_gap",
    "household_hardship",
    "household_food_insecurity_data_signal",
}
PUBLIC_PROVIDER_OPERATIONS_PRESSURE_TYPES = {
    "food_bank_inventory_shortage",
    "operating_cost_strain",
    "fuel_cost_strain",
    "volunteer_capacity_strain",
    "service_reduction",
    "distribution_cancellation",
}
PRESSURE_KEYWORDS = {
    "demand strain": ("demand increase", "increased demand", "demand surge", "long lines", "higher demand"),
    "benefit disruption": ("benefit delay", "benefit disruption", "ebt outage", "snap delay", "wic delay"),
    "service reduction": ("reduced hours", "capacity cut", "closed site", "service reduction", "short staffed"),
    "access gap": ("access gap", "no nearby", "transport barrier", "unserved"),
    "funding risk": ("funding cut", "budget cut", "grant loss"),
    "price pressure": ("food price", "grocery prices", "price increase", "inflation"),
    "child meal gap": ("summer meal gap", "child meal", "school meal gap"),
    "senior meal strain": ("senior waitlist", "meals on wheels waitlist"),
    "rural grocery access": ("rural grocery", "store closure", "food desert"),
    "disaster disruption": ("wildfire", "hurricane", "flood", "disaster"),
    "household hardship": ("skipping meals", "food hardship", "cannot afford food"),
}
GROUP_KEYWORDS = {
    "children": ("child", "children", "students"),
    "seniors": ("senior", "older adult"),
    "SNAP households": ("snap household", "snap recipient"),
    "WIC households": ("wic"),
    "low-income households": ("low-income household", "poverty"),
    "rural residents": ("rural"),
    "disaster-affected households": ("disaster", "hurricane", "flood", "wildfire"),
}


def _food_line_is_nonlocal_data_signal(row: dict[str, Any]) -> bool:
    role = str(row.get("source_role") or "").strip()
    if role in NON_MAP_SIGNAL_ROLES:
        return True
    family = str(row.get("source_family") or "").strip().lower()
    return bool(row.get("pressure_signal")) and family in {"economic_data", "policy_research"} and str(row.get("state") or "").strip().upper() in {"", "US"}


def _food_line_map_eligible(row: dict[str, Any]) -> bool:
    if not bool(row.get("pressure_signal")):
        return False
    return not _food_line_is_nonlocal_data_signal(row)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_asset(src: Path, dst: Path, warnings: list[str], wrote: list[str]) -> None:
    if not src.exists():
        warnings.append(f"Missing asset: {src}")
        return
    wrote.append(str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _food_line_theme_styles() -> str:
    return """
<style>
:root {
  --ink: #1E3F4F;
  --navy: #1E3F4F;
  --blue-fern: #4E6B79;
  --soft-blue: #EFE7DA;
  --fern-green: #4E6B79;
  --paper: #FBF7EF;
  --border: #D2C5B4;
  --panel: #FFFDF8;
  --muted: #4E6B79;
}
.food-line-shell {
  display: grid;
  gap: 1.25rem;
}
.food-line-hero {
  display: grid;
  justify-items: center;
  gap: 0.65rem;
  text-align: center;
  padding-bottom: 1.1rem;
  border-bottom: 1px solid var(--border);
}
.food-line-hero .eyebrow,
.food-line-hero p {
  margin: 0;
}
.food-line-logo {
  display: block;
  height: auto;
  max-width: 90vw;
  object-fit: contain;
  margin: 0 auto;
}
.food-line-logo--home {
  width: min(360px, 90vw);
}
.food-line-logo--edition {
  width: min(460px, 90vw);
}
.food-line-logo--map {
  width: min(520px, 90vw);
}
.food-line-logo--audio {
  width: min(360px, 90vw);
}
.food-line-panel {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--panel);
  padding: clamp(18px, 3vw, 28px);
  box-shadow: 0 18px 42px rgba(30, 63, 79, 0.08);
}
.food-line-panel h2:first-child,
.food-line-panel h3:first-child {
  margin-top: 0;
}
.food-line-source-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}
.food-line-source-table th,
.food-line-source-table td {
  border-top: 1px solid var(--border);
  vertical-align: top;
  padding: 0.65rem 0.55rem;
}
.food-line-source-table th {
  text-align: left;
  color: var(--navy);
  background: #F3EBDD;
  position: sticky;
  top: 0;
}
.food-line-source-table td:nth-child(9),
.food-line-source-table td:nth-child(14) {
  min-width: 16rem;
}
.food-line-source-table td:nth-child(10),
.food-line-source-table td:nth-child(17) {
  min-width: 10rem;
}
.food-line-map-shell {
  display: grid;
  gap: 1rem;
}
.food-line-map {
  height: min(70vh, 640px);
  border: 1px solid var(--border);
  border-radius: 18px;
  overflow: hidden;
}
.food-line-map-panel {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--panel);
  padding: 1rem 1.1rem;
}
.food-line-map-panel ul {
  margin-top: 0.8rem;
}
.food-line-map-panel .fl-legend {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.5rem 1rem;
  list-style: none;
  margin: 0;
  padding: 0;
}
.food-line-map-panel .fl-legend li {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}
.food-line-dot {
  width: 0.8rem;
  height: 0.8rem;
  border-radius: 50%;
  border: 1px solid #1B2F39;
  display: inline-block;
}
.food-line-map-popup {
  display: grid;
  gap: 0.3rem;
  max-width: 24rem;
}
.food-line-map-popup a {
  word-break: break-word;
}
.food-line-audio-shell {
  display: grid;
  gap: 1rem;
}
.food-line-audio-list {
  display: grid;
  gap: 0.8rem;
  list-style: none;
  padding: 0;
  margin: 0;
}
.food-line-audio-list li {
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
.food-line-audio-list li:first-child {
  border-top: 0;
  padding-top: 0;
}
.food-line-source-card {
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 1rem 1rem 0.9rem;
  background: #FFFDF9;
  box-shadow: 0 10px 24px rgba(30, 63, 79, 0.06);
}
.food-line-source-card h3 {
  margin-top: 0;
}
.food-line-source-card p {
  margin: 0.35rem 0;
}
@media (max-width: 640px) {
  .food-line-logo--edition,
  .food-line-logo--map,
  .food-line-logo--audio,
  .food-line-logo--home {
    width: 90vw;
  }
  .food-line-source-table {
    font-size: 0.9rem;
  }
}
</style>
""".strip()


def _food_line_assets(root: Path, warnings: list[str], wrote: list[str]) -> None:
    source_root = root / "assets"
    site_assets = root / "output" / "site" / "assets"
    food_assets = root / "output" / "site" / DISPATCH_SLUG / "assets"
    for asset in ("site.css", "bluefern.png", "favicon.ico", "favicon-32x32.png", "favicon-16x16.png", "apple-touch-icon.png"):
        _copy_asset(source_root / asset, site_assets / asset, warnings, wrote)
    for asset in ("site.css", "bluefern.png", FOOD_LINE_LOGO_ASSET):
        _copy_asset(source_root / asset, food_assets / asset, warnings, wrote)


def _food_line_assets_to_output_root(root: Path, output_root: Path, warnings: list[str], wrote: list[str]) -> None:
    source_root = root / "assets"
    output_assets = output_root / "assets"
    for asset in ("site.css", "bluefern.png", FOOD_LINE_LOGO_ASSET):
        _copy_asset(source_root / asset, output_assets / asset, warnings, wrote)


def _food_line_logo_html(size_class: str, asset_prefix: str) -> str:
    return f'<img class="hero-logo food-line-logo {size_class}" src="{asset_prefix}{FOOD_LINE_LOGO_ASSET}" alt="{html.escape(DISPATCH_DISPLAY_NAME)}">'


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd or ROOT), check=False, capture_output=True, text=True, encoding="utf-8")


def _parse_json_stdout(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    return json.loads(raw)


def _manual_source_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / date / "manual_sources.json"


def _auto_source_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / date / "auto_sources.json"


def _collector_audit_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / date / "collector_audit.json"


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _source_evidence_text(row: dict[str, Any]) -> str:
    evidence = _as_text(row.get("evidence_text"))
    if evidence:
        return evidence
    return _as_text(row.get("summary_or_snippet") or row.get("title"))


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _affected_groups_display(value: Any) -> str:
    groups = [str(item).strip() for item in (value or []) if str(item).strip()]
    return ", ".join(groups) if groups else ""


def _public_review_summary(total_count: int, verified_count: int, publisher_count: int | None = None) -> str:
    excluded_count = max(0, total_count - verified_count)
    source_label = "public source" if verified_count == 1 else "public sources"
    excluded_label = "record" if excluded_count == 1 else "records"
    if publisher_count is None:
        source_clause = f"{verified_count} {source_label}"
    else:
        publisher_label = "publisher" if publisher_count == 1 else "publishers"
        source_clause = f"{verified_count} {source_label} from {publisher_count} {publisher_label}"
    return (
        f"Sources behind this briefing: {source_clause}. "
        f"The run reviewed {total_count} records and excluded {excluded_count} {excluded_label} that were duplicate, stale, unrelated, or not strong enough for public use."
    )


def _food_line_pressure_type_key(row: dict[str, Any]) -> str:
    raw = str(row.get("pressure_type") or "").strip().lower()
    raw = raw.replace("-", "_").replace("/", "_")
    raw = re.sub(r"\s+", "_", raw)
    aliases = {
        "food_insecurity": "household_food_insecurity_data_signal",
        "food_bank_inventory": "food_bank_inventory_shortage",
        "food_bank_inventory_shortage": "food_bank_inventory_shortage",
        "operating_costs": "operating_cost_strain",
        "operating_cost_pressure": "operating_cost_strain",
        "fuel_costs": "fuel_cost_strain",
        "fuel_cost_pressure": "fuel_cost_strain",
        "volunteer_shortage": "volunteer_capacity_strain",
        "volunteer_shortages": "volunteer_capacity_strain",
        "snap_enrollment": "snap_enrollment_decline",
        "snap_access": "benefit_access_decline",
        "benefit_access": "benefit_access_decline",
        "benefit_disruption": "benefit_disruption",
        "distribution_cancellation": "distribution_cancellation",
        "school_meal_access": "school_meal_access_pressure",
        "disaster_food_access": "disaster_food_access_pressure",
        "household_hardship": "household_food_insecurity_data_signal",
    }
    return aliases.get(raw, raw)


def _food_line_public_inclusion_reason(row: dict[str, Any]) -> str:
    if not bool(row.get("source_public_story_eligible", True)):
        return "not fresh enough for public inclusion"
    if not bool(row.get("supported_product_geography", True)) or str(row.get("location_scope") or "").strip() == "outside_product_geography":
        return "outside product geography"
    source_role = str(row.get("source_role") or "").strip()
    source_family = str(row.get("source_family") or "").strip().lower()
    if bool(row.get("donation_wrapper")) or source_role == "discovery_lead" or bool(row.get("public_eligible") is False):
        return "discovery lead only / not public eligible"
    if _food_line_source_background_reference(row):
        return "background/context only"
    if not str(row.get("url") or "").strip():
        return "missing source URL"
    if str(row.get("pressure_verification_status") or "").strip().lower() == "demoted_context":
        return "resource-only / no pressure signal"
    if not bool(row.get("pressure_signal")):
        if source_role in {"policy_context", "research_signal", "institutional_context_signal", "data_anchor_signal"} and (
            source_family in POLICY_FAMILIES or _food_line_pressure_type_key(row) in PUBLIC_INCLUSION_PRESSURE_TYPES
        ):
            return ""
        return "resource-only / no pressure signal"
    pressure_key = _food_line_pressure_type_key(row)
    if pressure_key in PUBLIC_INCLUSION_PRESSURE_TYPES:
        return ""
    if pressure_key in {"demand_strain", "service_reduction", "price_pressure", "child_meal_gap", "senior_meal_strain", "rural_grocery_access"}:
        return ""
    if pressure_key.startswith("snap") or "benefit" in pressure_key or "access" in pressure_key:
        return ""
    if any(token in pressure_key for token in ("inventory", "cost", "fuel", "volunteer", "distribution", "disaster", "hardship")):
        return ""
    return "not a current public food-pressure signal"


def _food_line_qualifies_for_public_inclusion(row: dict[str, Any]) -> bool:
    return not bool(_food_line_public_inclusion_reason(row))


def _food_line_candidate_traceability_status(row: dict[str, Any]) -> str:
    primary_url = canonical_url(str(row.get("primary_source_url") or ""))
    url = canonical_url(str(row.get("url") or ""))
    final_trace_url = canonical_url(str(row.get("final_trace_url") or ""))
    discovered_url = canonical_url(str(row.get("discovered_url") or ""))
    traceability_role = str(row.get("source_traceability_role") or "").strip().lower()
    if not any((primary_url, url, final_trace_url, discovered_url)):
        return "missing_url"
    if "wrapper" in traceability_role or "syndicated" in traceability_role:
        return "source_wrapper_only" if not primary_url else "traceable"
    wrapper_hosts = ("news.google.com", "google.com", "t.co", "x.com", "twitter.com", "facebook.com", "instagram.com")
    parsed_url = urlsplit(url or discovered_url or final_trace_url)
    if parsed_url.netloc.lower().endswith(wrapper_hosts) and not primary_url and not final_trace_url:
        return "source_wrapper_only"
    if primary_url or final_trace_url:
        return "traceable"
    if url:
        return "weak_traceability"
    return "missing_url"


def _food_line_candidate_signal_strength(row: dict[str, Any]) -> str:
    if not bool(row.get("pressure_signal")):
        if str(row.get("pressure_verification_status") or "").strip().lower() in {"demoted_context", "registry_summary_only"}:
            return "weak"
        return "none"
    match_terms = [str(term).strip() for term in (row.get("pressure_match_terms") or []) if str(term).strip()]
    verification_status = str(row.get("pressure_verification_status") or "").strip().lower()
    evidence_basis = str(row.get("evidence_text_basis") or "").strip().lower()
    if verification_status == "source_text_verified" and evidence_basis in {"manual_review", "manual_source_text", "page_text_excerpt"} and len(match_terms) >= 2:
        return "strong"
    if verification_status == "source_text_verified":
        return "moderate"
    return "weak"


def _food_line_candidate_pressure_type(row: dict[str, Any]) -> str:
    pressure_key = _food_line_pressure_type_key(row)
    mapping = {
        "demand_strain": "pantry_demand",
        "service_reduction": "distribution_capacity",
        "benefit_disruption": "benefit_gap",
        "benefit_access_decline": "benefit_gap",
        "snap_enrollment_decline": "benefit_gap",
        "school_meal_access_pressure": "school_meal_gap",
        "child_meal_gap": "summer_hunger",
        "summer_meal_gap": "summer_hunger",
        "food_bank_inventory_shortage": "food_bank_inventory",
        "operating_cost_strain": "cost_pressure",
        "fuel_cost_strain": "cost_pressure",
        "volunteer_capacity_strain": "distribution_capacity",
        "distribution_cancellation": "distribution_capacity",
        "disaster_food_access_pressure": "distribution_capacity",
        "household_food_insecurity_data_signal": "household_food_insecurity",
        "access_gap": "household_food_insecurity",
        "price_pressure": "cost_pressure",
        "rural_grocery_access": "household_food_insecurity",
        "senior_meal_strain": "distribution_capacity",
    }
    return mapping.get(pressure_key, "other")


def _food_line_candidate_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    traceability_status = _food_line_candidate_traceability_status(row)
    pressure_strength = _food_line_candidate_signal_strength(row)
    source_role = str(row.get("source_role") or "").strip().lower()
    public_reason = str(row.get("public_inclusion_reason") or "").strip() or _food_line_public_inclusion_reason(row)
    freshness_status = str(row.get("source_freshness_status") or row.get("freshness_status") or "").strip().lower()
    if str(row.get("duplicate_of") or "").strip():
        blockers.append("duplicate")
    if traceability_status == "missing_url":
        blockers.append("missing_original_source_url")
    elif traceability_status == "source_wrapper_only":
        blockers.append("source_wrapper_only")
    if not bool(row.get("supported_product_geography", True)) or str(row.get("location_scope") or "").strip() == "outside_product_geography":
        blockers.append("non_us_scope")
    if not bool(row.get("source_public_story_eligible", True)) or freshness_status in {"stale_outside_daily_window", "missing_source_published_date", "unparsed_source_published_date", "url_path_only"}:
        blockers.append("stale_or_no_usable_date")
    if public_reason == "resource-only / no pressure signal":
        blockers.append("resource_only_no_pressure")
    if pressure_strength == "weak":
        blockers.append("weak_pressure_signal")
    if source_role in {"policy_context", "research_signal", "institutional_context_signal", "data_anchor_signal", "background_context", "baseline_condition"}:
        blockers.append("institutional_context_only")
    if (
        str(row.get("discovery_channel") or "").strip().lower() in {"social", "social_post"}
        or str(row.get("fetch_status") or "").strip().lower().startswith("blocked")
        or str(row.get("pressure_verification_status") or "").strip().lower() == "registry_summary_only"
    ):
        blockers.append("social_only_unverified")
    if public_reason == "not a current public food-pressure signal":
        blockers.append("no_public_impact")
    deduped: list[str] = []
    for blocker in blockers:
        if blocker not in deduped:
            deduped.append(blocker)
    return deduped


def _food_line_candidate_review_status(row: dict[str, Any], blockers: list[str]) -> str:
    if bool(row.get("public_claim_eligible")):
        return "approved"
    hard_reject = {"missing_original_source_url", "source_wrapper_only", "duplicate", "non_us_scope"}
    if any(blocker in hard_reject for blocker in blockers):
        return "rejected"
    if blockers == ["resource_only_no_pressure"] or blockers == ["no_public_impact"]:
        return "rejected"
    if any(blocker in {"weak_pressure_signal", "institutional_context_only", "social_only_unverified"} for blocker in blockers):
        return "watchlist"
    return "needs_review"


def _food_line_candidate_source_role(row: dict[str, Any], review_status: str) -> str:
    source_role = str(row.get("source_role") or "").strip().lower()
    source_family = str(row.get("source_family") or "").strip().lower()
    if review_status == "watchlist":
        return "watchlist_signal"
    if source_role in {"baseline_condition", "data_anchor_signal", "research_signal", "institutional_context_signal", "policy_context"}:
        return "data_anchor"
    if source_role in {"provider_signal", "resource_context"} or source_family in {"food_bank_provider", "school_meals_child_nutrition", "senior_meals", "state_official", "federal_official"}:
        return "operating_signal"
    if bool(row.get("pressure_signal")):
        return "human_story"
    return "watchlist_signal"


def _food_line_candidate_review_note(row: dict[str, Any], blockers: list[str], review_status: str) -> str:
    if bool(row.get("public_claim_eligible")):
        return "Eligible for public source-backed Food Line claims."
    if blockers:
        return "; ".join(blockers[:3]).replace("_", " ")
    if review_status == "needs_review":
        return "Traceable candidate retained for operator review."
    return "Retained outside public claims."


def _annotate_food_line_candidate_review_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocker_counts: Counter[str] = Counter()
    review_status_counts: Counter[str] = Counter()
    traceability_counts: Counter[str] = Counter()
    for row in rows:
        public_claim_eligible = bool(row.get("qualifies_for_public_inclusion")) if "qualifies_for_public_inclusion" in row else _food_line_qualifies_for_public_inclusion(row)
        traceability_status = _food_line_candidate_traceability_status(row)
        blockers = _food_line_candidate_blockers({**row, "public_claim_eligible": public_claim_eligible})
        review_status = _food_line_candidate_review_status({**row, "public_claim_eligible": public_claim_eligible}, blockers)
        candidate_source_role = _food_line_candidate_source_role(row, review_status)
        review_note = _food_line_candidate_review_note(row, blockers, review_status)
        pressure_signal_strength = _food_line_candidate_signal_strength(row)
        pressure_signal_type = _food_line_candidate_pressure_type(row)
        row["review_status"] = review_status
        row["candidate_source_role"] = candidate_source_role
        row["pressure_signal_strength"] = pressure_signal_strength
        row["pressure_signal_type"] = pressure_signal_type
        row["public_claim_eligible"] = bool(public_claim_eligible)
        row["public_claim_blockers"] = blockers
        row["traceability_status"] = traceability_status
        row["review_note"] = review_note
        row["candidate_classification"] = {
            "review_status": review_status,
            "source_role": candidate_source_role,
            "pressure_signal_strength": pressure_signal_strength,
            "pressure_signal_type": pressure_signal_type,
            "public_claim_eligible": bool(public_claim_eligible),
            "public_claim_blockers": blockers,
            "traceability_status": traceability_status,
            "review_note": review_note,
        }
        review_status_counts[review_status] += 1
        traceability_counts[traceability_status] += 1
        for blocker in blockers:
            blocker_counts[blocker] += 1
    return {
        "review_status_counts": dict(sorted(review_status_counts.items())),
        "traceability_status_counts": dict(sorted(traceability_counts.items())),
        "public_claim_blocker_counts": dict(sorted(blocker_counts.items())),
        "public_claim_eligible_count": sum(1 for row in rows if bool(row.get("public_claim_eligible"))),
    }


def _food_line_public_inclusion_bucket(row: dict[str, Any], *, is_lead: bool = False) -> str:
    if not _food_line_qualifies_for_public_inclusion(row):
        if _food_line_source_background_reference(row):
            return "context_only"
        return "excluded"
    if _food_line_is_research_context_signal(row):
        return "included_as_context_signal"
    if is_lead:
        return "included_as_lead"
    pressure_key = _food_line_pressure_type_key(row)
    role = _source_role(row)
    if pressure_key in PUBLIC_POLICY_ACCESS_PRESSURE_TYPES or role in {"policy_context"}:
        return "included_as_policy_access_signal"
    if pressure_key in PUBLIC_PROVIDER_OPERATIONS_PRESSURE_TYPES or role in {"provider_signal", "resource_context"}:
        return "included_as_provider_operations_signal"
    return "included_as_additional_signal"


def _food_line_is_research_context_signal(row: dict[str, Any]) -> bool:
    role = str(row.get("source_role") or "").strip().lower()
    family = str(row.get("source_family") or "").strip().lower()
    source_purpose = str(row.get("source_purpose") or "").strip().lower()
    evidence_level = str(row.get("evidence_level") or "").strip().lower()
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    map_category = str(row.get("map_category") or "").strip().lower()
    return bool(
        role == "research_signal"
        or family == "policy_research"
        or source_purpose in {"research_report", "data_release"}
        or evidence_level in {"research context", "research report", "official data/statistic"}
        or "research" in pressure_type
        or "data" in pressure_type
        or "context" in pressure_type
        or "context" in map_category
    )


def _public_source_rows(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in sources if _food_line_qualifies_for_public_inclusion(row)]


def _food_line_public_rendered_rows(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rendered_rows: list[dict[str, Any]] = []
    rendered_ids: set[str] = set()

    def add_row(row: dict[str, Any] | None) -> None:
        if not row:
            return
        row_id = str(row.get("source_record_id") or "").strip()
        if row_id and row_id in rendered_ids:
            return
        if row_id:
            rendered_ids.add(row_id)
        rendered_rows.append(row)

    add_row(primary_row)
    for row in continuing_rows:
        add_row(row)
    for row in _food_line_public_story_rows(sources, primary_row, continuing_rows):
        add_row(row)
    for row in _food_line_traceability_rows(sources, primary_row, continuing_rows):
        add_row(row)
    return rendered_rows


def _food_line_public_story_rows(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    *,
    edition_mode: str = "current_update",
) -> list[dict[str, Any]]:
    if edition_mode == "no_current_update":
        return []
    rows: list[dict[str, Any]] = []
    rendered_ids: set[str] = set()

    def add_row(row: dict[str, Any] | None) -> None:
        if not row:
            return
        row_id = str(row.get("source_record_id") or "").strip()
        if row_id and row_id in rendered_ids:
            return
        if row_id:
            rendered_ids.add(row_id)
        rows.append(row)

    add_row(primary_row)
    for row in continuing_rows:
        add_row(row)
    for row in _food_line_public_inclusion_rows(sources, primary_row, continuing_rows):
        add_row(row)
    return rows


def _food_line_public_inclusion_rows(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocked_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in ([primary_row] if primary_row else []) + list(continuing_rows)
        if row
    }
    return [
        row
        for row in sources
        if str(row.get("source_record_id") or "").strip() not in blocked_ids
        and _food_line_qualifies_for_public_inclusion(row)
    ]


def _food_line_public_section_rows(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    *,
    edition_mode: str = "current_update",
) -> dict[str, list[dict[str, Any]]]:
    lead_id = str((primary_row or {}).get("source_record_id") or "").strip()
    continuing_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in continuing_rows
        if str(row.get("source_record_id") or "").strip()
    }
    if edition_mode == "no_current_update":
        return {
            "core": [],
            "other": [],
            "context": [],
            "policy": [],
            "provider": [],
            "traceability": _food_line_traceability_rows(sources, primary_row, continuing_rows),
        }
    core_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    provider_rows: list[dict[str, Any]] = []
    for row in _food_line_public_inclusion_rows(sources, primary_row, continuing_rows):
        row_id = str(row.get("source_record_id") or "").strip()
        if row_id and (row_id == lead_id or row_id in continuing_ids):
            continue
        bucket = _food_line_public_inclusion_bucket(row)
        if bucket == "included_as_context_signal":
            context_rows.append(row)
            policy_rows.append(row)
        if bucket == "included_as_policy_access_signal":
            policy_rows.append(row)
        elif bucket == "included_as_provider_operations_signal":
            provider_rows.append(row)
        elif bucket != "included_as_context_signal":
            other_rows.append(row)
    if primary_row:
        core_rows.append(primary_row)
    core_rows.extend(continuing_rows)
    return {
        "core": core_rows,
        "other": other_rows,
        "context": context_rows,
        "policy": policy_rows,
        "provider": provider_rows,
        "traceability": _food_line_traceability_rows(sources, primary_row, continuing_rows),
    }


def _food_line_public_source_family_label(row: dict[str, Any]) -> str:
    family = str(row.get("source_family") or "").strip().lower()
    mapping = {
        "local_news": "Local report",
        "local_reporting": "Local report",
        "state_official": "State official",
        "federal_official": "Federal official",
        "policy_research": "Research",
        "economic_data": "Official data",
        "food_bank_provider": "Food bank/provider",
        "school_meals_child_nutrition": "School meals / child nutrition",
        "senior_meals": "Senior meals",
        "rural_access": "Rural access",
        "nonprofit_news": "Local report",
        "public_radio": "Local report",
    }
    if family in mapping:
        return mapping[family]
    return family.replace("_", " ").title() if family else ""


def _food_line_public_usage_label(
    row: dict[str, Any],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    *,
    edition_mode: str = "current_update",
) -> str:
    row_id = str(row.get("source_record_id") or "").strip()
    freshness_status = str(row.get("source_freshness_status") or row.get("freshness_status") or "").strip()
    if freshness_status == "stale_outside_daily_window" and not _food_line_source_background_reference(row):
        return "Stale excluded"
    if edition_mode == "no_current_update":
        if _food_line_source_background_reference(row):
            return "Background reference"
        disqualification_reason = str(
            row.get("primary_disqualification_reason")
            or row.get("freshness_disqualification_reason")
            or row.get("source_freshness_disqualification_reason")
            or row.get("pressure_reason")
            or ""
        ).strip()
        if disqualification_reason:
            return f"Source audit: {disqualification_reason.rstrip('.')}"
        return "Source audit"
    if primary_row and row_id == str(primary_row.get("source_record_id") or "").strip():
        return "Main story"
    for continuing_row in continuing_rows:
        if row_id == str(continuing_row.get("source_record_id") or "").strip():
            return "Earlier lead"
    if _food_line_qualifies_for_public_inclusion(row):
        return "Current secondary item"
    return "Background reference"


def _food_line_public_page_usage_visible(label: str) -> bool:
    return label in {
        "Main story",
        "Earlier lead",
        "Current secondary item",
        "Policy / Benefits signal",
        "Provider / Operations signal",
    }


def _food_line_public_issue_label(row: dict[str, Any], usage_label: str) -> str:
    if usage_label in {"Background reference", "Stale excluded"} or usage_label.startswith("Source audit"):
        if usage_label.startswith("Source audit"):
            return "Source audit"
        return "Background reference"
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    mapping = {
        "demand strain": "Pantry demand / food-assistance demand",
        "benefit disruption": "Benefit disruption",
        "service reduction": "Service reduction",
        "access gap": "Access gap",
        "funding risk": "Funding risk",
        "price pressure": "Price pressure",
        "child meal gap": "Child meal gap",
        "senior meal strain": "Senior meal strain",
        "hospital-linked caregiver food insecurity": "Hospital-linked caregiver food insecurity",
        "rural grocery access": "Rural grocery access",
        "disaster disruption": "Disaster disruption",
        "household hardship": "Household financial stress" if _food_line_is_nonlocal_data_signal(row) else "Household hardship",
    }
    if pressure_type in mapping:
        return mapping[pressure_type]
    if pressure_type == "context only":
        return "Background reference"
    return pressure_type.replace("_", " ").title() if pressure_type else ""


def _food_line_public_verification_status_label(row: dict[str, Any], usage_label: str = "") -> str:
    freshness_status = str(row.get("source_freshness_status") or row.get("freshness_status") or "").strip()
    if usage_label.startswith("Source audit"):
        return "Source audit"
    if freshness_status == "stale_outside_daily_window" and not _food_line_source_background_reference(row):
        return "Stale excluded"
    if not bool(row.get("pressure_signal")):
        return "Background reference"
    status = str(row.get("pressure_verification_status") or "").strip().lower()
    mapping = {
        "source_text_verified": "Source text reviewed",
        "demoted_context": "Background",
        "context only": "Background",
        "registry_summary_only": "Registry summary only",
    }
    if status in mapping:
        return mapping[status]
    return status.replace("_", " ").title() if status else ""


def _food_line_public_what_happened_label(row: dict[str, Any], usage_label: str) -> str:
    if usage_label in {"Background reference", "Stale excluded"}:
        return "Background reference"
    if usage_label.startswith("Source audit"):
        return "Recorded for audit only."
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    if pressure_summary:
        return pressure_summary
    story_sentence = _food_line_public_story_sentence(row)
    return story_sentence if story_sentence else ""


def _public_audit_summary(sources: list[dict[str, Any]]) -> str:
    return _public_review_summary(len(sources), len(_public_source_rows(sources)))


def _food_line_reported_signal_limitation() -> str:
    return (
        "Food Line tracks source-backed reported signals of food pressure available at publish time. "
        "It should not be read as a complete national measure of food insecurity."
    )


def _public_evidence_excerpt(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "")
    candidates = [
        str(row.get("evidence_text") or "").strip(),
        str(row.get("claim_supported") or "").strip(),
        str(row.get("pressure_summary") or "").strip(),
        str(row.get("summary_or_snippet") or "").strip(),
        title,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        excerpt = clean_food_line_public_evidence_excerpt(candidate, title=title, limit=420)
        if excerpt and excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
            return excerpt
    return FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK


def _food_line_public_summary_is_generic(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .:;,-")
    if not normalized:
        return True
    if normalized in {
        "source-backed food insecurity context signal",
        "food insecurity context signal",
        "source-backed pressure signal",
        "elevated demand signal",
        "context signal",
    }:
        return True
    generic_patterns = (
        r"\breported (?:rising|increasing|increased) food-assistance demand\b",
        r"\breported food-assistance pressure\b",
        r"\breported food-access pressure\b",
        r"\breported reduced distribution hours\b",
        r"\breported a snap benefit delay\b",
        r"\breported a child meal gap\b",
        r"\breported senior meal strain\b",
        r"\breported a food-access gap\b",
    )
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in generic_patterns)


def _food_line_public_summary_sentence(row: dict[str, Any] | None, *, max_words: int = 60) -> str:
    row = row or {}
    title = str(row.get("title") or "").strip()
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    if pressure_summary and not _food_line_public_summary_is_generic(pressure_summary):
        return _food_line_compact_public_sentence(pressure_summary, max_words=max_words)

    evidence_candidates = [
        str(row.get("evidence_text") or "").strip(),
        str(row.get("summary_or_snippet") or "").strip(),
    ]
    if title:
        evidence_candidates.append(title)
    scored_candidates: list[tuple[int, str]] = []
    for candidate in evidence_candidates:
        if not candidate:
            continue
        cleaned = clean_food_line_public_evidence_excerpt(candidate, title=title, limit=480)
        if not cleaned or cleaned == FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
            continue
        if _food_line_public_summary_is_generic(cleaned):
            continue
        scored_candidates.append((_food_line_public_summary_score(cleaned), cleaned))

    if scored_candidates:
        scored_candidates.sort(key=lambda item: item[0], reverse=True)
        summary = _food_line_compact_public_sentence(scored_candidates[0][1], max_words=max_words)
        source_context = " ".join(
            part for part in (
                str(row.get("evidence_text") or ""),
                str(row.get("summary_or_snippet") or ""),
            )
            if part
        ).lower()
        location = str(row.get("location_name") or row.get("state") or "").strip()
        if location and location.lower() not in summary.lower():
            summary = f"{summary} in {location}"
        if "families with children" in source_context and "families with children" not in summary.lower():
            summary = f"{summary}, with summer demand rising for families with children"
        elif any(phrase in source_context for phrase in ("school meals", "summer meal", "summer school-meal")) and not re.search(r"\bschool[- ]meal", summary, flags=re.IGNORECASE):
            summary = f"{summary}, as school-meal gaps add strain"
        elif "summer" in source_context and "summer" not in summary.lower():
            summary = f"{summary}, with summer strain"
        return summary

    publisher = str(row.get("publisher") or row.get("source_name") or "the source").strip()
    location = str(row.get("location_name") or row.get("state") or "").strip()
    pressure_type = str(row.get("pressure_type") or "").strip()
    affected_groups = _affected_groups_display(list(row.get("affected_groups") or []))
    sentence = f"{publisher} reported"
    if pressure_type:
        sentence += f" {pressure_type}"
    else:
        sentence += " food-pressure conditions"
    if location:
        sentence += f" in {location}"
    if affected_groups:
        sentence += f", affecting {affected_groups}"
    return _food_line_compact_public_sentence(sentence, max_words=max_words)


def _food_line_compact_public_sentence(text: str, *, max_words: int = 60) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    if not sentence:
        return ""
    sentence = sentence.rstrip(".")
    words = sentence.split()
    if len(words) <= max_words:
        return sentence
    shortened = " ".join(words[:max_words]).rstrip(" ,;:-")
    if not shortened:
        return ""
    return shortened + "..."


def _food_line_public_summary_score(text: str) -> int:
    lowered = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not lowered:
        return -100
    score = 0
    if "and to their" in lowered:
        score -= 2
    markers = (
        "empty shelves",
        "running out",
        "supplies",
        "dwindling",
        "shipment",
        "first-time",
        "visitors",
        "families with children",
        "school meals",
        "usda",
        "grocery",
        "fuel",
        "less food",
        "smaller than",
        "last of what it had",
        "more regularly",
        "pantry",
        "food bank",
        "lines",
        "summer",
    )
    score += sum(2 for marker in markers if marker in lowered)
    if 20 <= len(lowered.split()) <= 45:
        score += 2
    elif len(lowered.split()) > 45:
        score -= 1
    if re.search(r"\b(rising|increasing|increased) demand\b", lowered):
        score += 1
    if re.search(r"\breported\b", lowered):
        score += 1
    return score


def _food_line_public_story_sentence(row: dict[str, Any] | None) -> str:
    row = row or {}
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    if _food_line_is_nonlocal_data_signal(row) and pressure_summary:
        return pressure_summary.rstrip(".")
    title = str(row.get("title") or "").strip()
    if title:
        return title.rstrip(".")
    if pressure_summary:
        return pressure_summary.rstrip(".")
    evidence_excerpt = _public_evidence_excerpt(row)
    if evidence_excerpt and evidence_excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
        return evidence_excerpt.rstrip(".")
    publisher = str(row.get("publisher") or row.get("source_name") or "the source").strip()
    location = str(row.get("location_name") or row.get("state") or "").strip()
    if publisher and location:
        return f"{publisher} reported food-access pressure in {location}"
    if publisher:
        return f"{publisher} reported food-access pressure"
    return "A public source reported food-access pressure"


def _food_line_public_reported_clause(row: dict[str, Any] | None) -> str:
    row = row or {}
    title = str(row.get("title") or "").strip()
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip().lower()
    location = str(row.get("location_name") or row.get("state") or "").strip()
    if (
        publisher == "13abc"
        and title.lower().startswith("local food pantries are preparing for increased demand as snap benefits face shutdown pause")
    ):
        return f"{location or 'Toledo'} food pantries were preparing for increased demand as SNAP benefits faced a shutdown pause"
    clause = _food_line_public_story_sentence(row)
    clause = clause.strip().rstrip(".")
    clause = re.sub(r"^Local\s+", "", clause, flags=re.IGNORECASE)
    clause = re.sub(r"\bare preparing\b", "were preparing", clause, flags=re.IGNORECASE)
    clause = re.sub(r"\bface\b", "faced", clause, count=1, flags=re.IGNORECASE)
    clause = re.sub(r"\bfaces\b", "faced", clause, count=1, flags=re.IGNORECASE)
    clause = re.sub(r"\bare\b", "were", clause, count=1, flags=re.IGNORECASE)
    clause = re.sub(r"\bis\b", "was", clause, count=1, flags=re.IGNORECASE)
    if clause:
        clause = clause[0].lower() + clause[1:]
    return clause


def _food_line_spoken_secondary_clause(row: dict[str, Any]) -> str:
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip()
    publisher_key = publisher.lower()
    location = str(row.get("location_name") or row.get("state") or "").strip()
    affected_groups = _affected_groups_display(list(row.get("affected_groups") or []))
    if publisher_key == "cascade pbs":
        return "Washington food banks were worried about federal food-program cuts"
    if publisher_key == "kltv":
        return "East Texas food banks were working to keep up with rising demand during the government shutdown"
    clause = ""
    if bool(row.get("pressure_signal")) or str(row.get("evidence_text") or "").strip() or str(row.get("summary_or_snippet") or "").strip():
        clause = _food_line_audio_other_signal_summary(row)
    if not clause:
        clause = str(row.get("pressure_summary") or "").strip()
    if not clause:
        clause = _food_line_public_reported_clause(row)
    clause = clause.strip().rstrip(".")
    if _food_line_is_nonlocal_data_signal(row):
        if publisher and clause.lower().startswith(publisher.lower()):
            clause = clause[len(publisher) :].lstrip(" ,:-")
        clause = re.sub(r"^reported on research linking\s+", "research linked ", clause, flags=re.IGNORECASE)
        clause = re.sub(r"^reported on\s+", "", clause, flags=re.IGNORECASE)
    if publisher and clause.lower().startswith(publisher.lower()):
        clause = clause[len(publisher) :].lstrip(" ,:-")
    clause = re.sub(r"^reported that\s+", "", clause, flags=re.IGNORECASE)
    clause = re.sub(r"^reported\s+", "", clause, flags=re.IGNORECASE)
    if re.match(r"^(rising|increasing|increased)\s+food-assistance demand\b", clause, flags=re.IGNORECASE):
        clause = "food-assistance pressure"
        if location:
            clause += f" in {location}"
        if affected_groups:
            clause += f", affecting {affected_groups}"
    if location and clause and not clause.lower().startswith(location.lower()) and not re.search(rf"\bin\s+{re.escape(location)}\b", clause, flags=re.IGNORECASE):
        clause = f"{location} {clause}".strip()
    return clause


def _public_source_table_rows_html(
    rows: list[dict[str, Any]],
    *,
    primary_row: dict[str, Any] | None = None,
    continuing_rows: list[dict[str, Any]] | None = None,
    edition_mode: str = "current_update",
) -> str:
    continuing_rows = list(continuing_rows or [])
    if not rows:
        return "<tr><td colspan='16'>No verified pressure sources were published today.</td></tr>"
    return "".join(
        "<tr>"
        f"<td>{html.escape(str(s.get('source_record_id') or ''))}</td>"
        f"<td>{html.escape(str(s.get('title') or ''))}</td>"
        f"<td>{html.escape(str(s.get('publisher') or ''))}</td>"
        f"<td>{html.escape(str(s.get('location_name') or s.get('state') or ''))}</td>"
        f"<td><a href=\"{html.escape(str(s.get('url') or ''))}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(str(s.get('url') or ''))}</a></td>"
        f"<td>{html.escape(_food_line_public_source_family_label(s))}</td>"
        f"<td>{html.escape(_food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode))}</td>"
        f"<td>{html.escape(_food_line_public_issue_label(s, _food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode)))}</td>"
        f"<td>{html.escape(_food_line_public_what_happened_label(s, _food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode)))}</td>"
        f"<td>{html.escape(_public_evidence_excerpt(s) if _public_evidence_excerpt(s) != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK else '')}</td>"
        f"<td>{html.escape(_food_line_public_verification_status_label(s, _food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode)))}</td>"
        f"<td>{html.escape(_affected_groups_display(s.get('affected_groups')))}</td>"
        f"<td>{html.escape('No' if (_food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode) in {'Background reference', 'Stale excluded'} or _food_line_public_usage_label(s, primary_row, continuing_rows, edition_mode=edition_mode).startswith('Source audit')) else 'Yes')}</td>"
        f"<td>{html.escape(str(s.get('source_freshness_status') or s.get('freshness_status') or ''))}</td>"
        f"<td>{html.escape(str(s.get('source_freshness_date_basis') or ''))}</td>"
        f"<td>{html.escape(str(s.get('source_public_story_eligible')).lower() if 'source_public_story_eligible' in s else '')}</td>"
        "</tr>"
        for s in rows
    )


def _public_source_cards_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<li>No verified pressure sources were published today.</li>"
    cards: list[str] = []
    for s in rows:
        evidence_excerpt = _public_evidence_excerpt(s)
        groups = _affected_groups_display(s.get("affected_groups"))
        parts = [
            "<li class='food-line-source-card'>",
            f"<h3>{html.escape(str(s.get('title') or ''))}</h3>",
            f"<p><strong>Source:</strong> {html.escape(str(s.get('publisher') or ''))}</p>",
            f"<p><strong>Where:</strong> {html.escape(str(s.get('location_name') or s.get('state') or ''))}</p>",
            f"<p><strong>What happened:</strong> {html.escape(str(s.get('pressure_summary') or ''))}</p>",
        ]
        if evidence_excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
            parts.append(f"<p><strong>What the source says:</strong> {html.escape(evidence_excerpt)}</p>")
        parts.append(f"<p><strong>Verification status:</strong> {html.escape(str(s.get('pressure_verification_status') or ''))}</p>")
        if groups:
            parts.append(f"<p><strong>Who may be affected:</strong> {html.escape(groups)}</p>")
        parts.append(
            f"<p><strong>Read the source:</strong> <a href=\"{html.escape(str(s.get('url') or ''))}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(str(s.get('url') or ''))}</a></p>"
        )
        parts.append("</li>")
        cards.append("".join(parts))
    return "".join(cards)


def _source_role(row: dict[str, Any]) -> str:
    explicit = str(row.get("source_role") or "").strip()
    if explicit:
        return explicit
    family = str(row.get("source_family") or "").strip().lower()
    category = str(row.get("map_category") or "").strip().lower()
    state = str(row.get("state") or "").strip().upper()
    tags = " ".join(str(tag).lower() for tag in (row.get("issue_tags") or []))
    text = _source_evidence_text(row).lower()
    combined = f"{tags} {text}"
    pressure_signal = bool(row.get("pressure_signal"))
    has_concrete_signal = any(token in combined for token in ("benefit", "snap", "food bank", "pantry", "meal", "service", "access", "disruption", "hunger", "demand", "waitlist", "delay"))
    if pressure_signal and state not in {"", "US"} and family in LOCAL_SIGNAL_FAMILIES and has_concrete_signal:
        if str(row.get("published_date_basis") or "") == "retrieved_at_fallback" and str(row.get("collector_source_type") or "").lower() not in {"rss", "feed", "live_update"}:
            return "provider_signal"
        return "local_signal"
    if "context / monitoring only" in category or family in {"economic_data"}:
        return "background_context"
    if family in {"policy_research", "federal_official"} or "policy" in tags or "snap" in tags or "benefits" in tags:
        return "policy_context"
    if family in {"food_bank_provider", "school_meals_child_nutrition", "senior_meals", "rural_access"}:
        if pressure_signal:
            return "provider_signal"
        return "resource_context"
    if category in {"acute strain / service disruption", "benefit disruption", "elevated demand"}:
        if pressure_signal:
            return "daily_signal"
        return "map_signal"
    return "map_signal"


def _contains_pressure_evidence(row: dict[str, Any]) -> tuple[bool, str, str]:
    text = _source_evidence_text(row).lower()
    for pressure_type, needles in PRESSURE_KEYWORDS.items():
        if any(needle in text for needle in needles):
            return True, pressure_type, f"matched pressure terms for {pressure_type}"
    return False, "context only", "no concrete pressure evidence terms matched"


def _affected_groups(row: dict[str, Any], *, pressure_signal: bool) -> list[str]:
    if not pressure_signal:
        return []
    text = _source_evidence_text(row).lower()
    return [group for group, tokens in GROUP_KEYWORDS.items() if any(token in text for token in tokens)]


def _evidence_level(row: dict[str, Any], pressure_signal: bool) -> str:
    if not pressure_signal:
        return "background context"
    family = str(row.get("source_family") or "").lower()
    if family == "food_bank_provider":
        return "provider reported strain"
    if family in {"state_official", "federal_official"}:
        return "policy/benefit change"
    if family == "local_reporting":
        return "local reporting"
    if family == "economic_data":
        return "official data/statistic"
    return "direct reported hardship"


def _freshness_role(row: dict[str, Any]) -> str:
    basis = str(row.get("published_date_basis") or row.get("date_basis") or "source_published")
    if basis == "source_published":
        return "fresh_daily_signal"
    if basis == "retrieved_at_fallback":
        return "current_monitoring"
    return "stable_context"


def _food_line_source_background_reference(row: dict[str, Any]) -> bool:
    source_role = str(row.get("source_role") or "").strip()
    if source_role in {"background_context", "baseline_condition"}:
        return True
    return False


def _food_line_apply_freshness_guard(row: dict[str, Any], edition_date: str) -> dict[str, Any]:
    freshness = validate_food_line_source_freshness(
        edition_date,
        str(row.get("published_at") or row.get("published_date") or ""),
        str(row.get("url") or ""),
        str(row.get("source_role") or "current_public_story"),
        page_metadata_date=str(row.get("page_metadata_date") or ""),
        background=_food_line_source_background_reference(row),
        freshness_window_days=FOOD_LINE_FRESHNESS_WINDOW_DAYS,
    )
    row["source_freshness_status"] = freshness["source_freshness_status"]
    row["source_freshness_disqualification_reason"] = freshness["source_freshness_disqualification_reason"]
    row["source_freshness_window_days"] = freshness["freshness_window_days"]
    row["source_published_date"] = freshness["source_published_date"]
    row["source_published_date_basis"] = freshness["source_published_date_basis"]
    row["source_url_date"] = freshness["source_url_date"]
    row["source_url_date_basis"] = freshness["source_url_date_basis"]
    row["source_freshness_date_basis"] = freshness["source_freshness_date_basis"]
    row["source_public_story_eligible"] = freshness["public_story_eligible"]
    if freshness["source_freshness_status"] == "stale_outside_daily_window":
        row["freshness_status"] = freshness["source_freshness_status"]
        row["freshness_disqualification_reason"] = freshness["source_freshness_disqualification_reason"]
    elif freshness["source_freshness_status"] == "missing_source_published_date":
        row["freshness_status"] = freshness["source_freshness_status"]
        row["freshness_disqualification_reason"] = freshness["source_freshness_disqualification_reason"]
    elif freshness["source_freshness_status"] == "unparsed_source_published_date":
        row["freshness_status"] = freshness["source_freshness_status"]
        row["freshness_disqualification_reason"] = freshness["source_freshness_disqualification_reason"]
    elif freshness["source_freshness_status"] == "url_path_only":
        row["freshness_status"] = freshness["source_freshness_status"]
        row["freshness_disqualification_reason"] = freshness["source_freshness_disqualification_reason"]
    if freshness["source_freshness_status"] in {"stale_outside_daily_window", "missing_source_published_date", "unparsed_source_published_date", "url_path_only"} and not bool(freshness.get("background_reference")):
        row["pressure_signal"] = False
        row["pressure_reason"] = freshness["source_freshness_disqualification_reason"] or "stale/outside daily window"
        row["pressure_summary"] = ""
        row["pressure_verification_status"] = "demoted_context"
        row["map_eligible"] = False
        if freshness["source_freshness_status"] == "url_path_only":
            row["source_role"] = "background_context"
    return freshness


def _source_role_refined(row: dict[str, Any], pressure_signal: bool) -> str:
    family = str(row.get("source_family") or "").lower()
    if pressure_signal and _food_line_is_nonlocal_data_signal(row):
        if family == "economic_data":
            return "data_anchor_signal"
        if family in {"state_official", "federal_official", "state_policy_news"}:
            return "institutional_context_signal"
        return "research_signal"
    if family in BASELINE_FAMILIES:
        return "baseline_condition"
    if family in POLICY_FAMILIES:
        return "pressure_evidence" if pressure_signal else "policy_context"
    if family in RESOURCE_FAMILIES and not pressure_signal:
        return "resource_context"
    if pressure_signal:
        return "pressure_evidence"
    return "resource_context"


def _lead_score(row: dict[str, Any], edition_date: str) -> int:
    role = _source_role(row)
    score = 0
    score += DAILY_PRIORITY_CATEGORIES.get(str(row.get("map_category") or "").strip().lower(), 0)
    score += DAILY_PRIORITY_FAMILIES.get(str(row.get("source_family") or "").strip().lower(), 0)
    score += {
        "daily_signal": 24,
        "local_signal": 36,
        "provider_signal": 16,
        "research_signal": 12,
        "institutional_context_signal": 11,
        "data_anchor_signal": 9,
        "policy_context": 10,
        "map_signal": 6,
        "background_context": 0,
    }.get(role, 0)
    if str(row.get("state") or "").strip().upper() not in {"", "US"}:
        score += 6
    text = _source_evidence_text(row).lower()
    if any(token in text for token in ("delay", "closure", "closed", "wait", "suspension", "disruption", "reduced", "cut")):
        score += 8
    if "first-time visitors" in text or "first time visitors" in text:
        score += 6
    if "running out" in text:
        score += 4
    if "context" in text and role == "background_context":
        score -= 4
    if str(row.get("published_date_basis") or "") == "retrieved_at_fallback" and str(row.get("collector_source_type") or "").lower() not in {"rss", "feed", "live_update"}:
        score -= 6
    if bool(row.get("pressure_signal")):
        score += 30
    if str(row.get("source_role") or "") == "pressure_evidence":
        score += 20
    if str(row.get("source_role") or "") in {"resource_context", "baseline_condition"}:
        score -= 20
    score += int(edition_date.replace("-", "")) % 3
    return score


def _role_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "daily_signal": 0,
        "background_context": 0,
        "policy_context": 0,
        "provider_signal": 0,
        "local_signal": 0,
        "map_signal": 0,
        "resource_context": 0,
        "research_signal": 0,
        "institutional_context_signal": 0,
        "data_anchor_signal": 0,
    }
    for row in rows:
        out[_source_role(row)] = int(out.get(_source_role(row), 0)) + 1
    return out


def _choose_lead(rows: list[dict[str, Any]], edition_date: str) -> tuple[dict[str, Any] | None, str]:
    if not rows:
        return None, "no_sources_available"
    best = sorted(rows, key=lambda r: (_lead_score(r, edition_date), str(r.get("source_record_id") or "")), reverse=True)[0]
    role = _source_role(best)
    why = f"selected for highest priority role/category mix ({role}, {best.get('map_category')})"
    return best, why


def _edition_history_root(root: Path) -> Path:
    return root / "output" / "site" / DISPATCH_SLUG / "editions"


def _previous_edition_date(root: Path, edition_date: str) -> str | None:
    editions_root = _edition_history_root(root)
    if not editions_root.exists():
        return None
    prior_dates = sorted(
        path.name
        for path in editions_root.iterdir()
        if path.is_dir() and DATE_RE.match(path.name) and path.name < edition_date
    )
    return prior_dates[-1] if prior_dates else None


def _load_previous_edition_context(root: Path, edition_date: str) -> dict[str, Any]:
    previous_date = _previous_edition_date(root, edition_date)
    if not previous_date:
        return {}
    edition_dir = _edition_history_root(root) / previous_date
    manifest_path = edition_dir / "edition_manifest.json"
    sources_path = edition_dir / "sources_manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    sources = _read_json(sources_path) if sources_path.exists() else []
    if not isinstance(manifest, dict):
        manifest = {}
    if not isinstance(sources, list):
        sources = []
    lead_source_record_id = str(manifest.get("lead_source_record_id") or "").strip()
    lead_row = next((row for row in sources if str(row.get("source_record_id") or "").strip() == lead_source_record_id), None)
    if lead_row is None:
        lead_row = next((row for row in sources if bool(row.get("pressure_signal"))), None)
    if not lead_row:
        return {"previous_edition_date": previous_date}
    return {
        "previous_edition_date": previous_date,
        "lead_source_record_id": str(lead_row.get("source_record_id") or "").strip(),
        "lead_canonical_url": canonical_url(str(lead_row.get("url") or "")),
        "lead_title": str(lead_row.get("title") or "").strip(),
        "lead_publisher": str(lead_row.get("publisher") or "").strip(),
        "lead_location_name": str(lead_row.get("location_name") or "").strip(),
        "lead_pressure_type": str(lead_row.get("pressure_type") or "").strip(),
        "lead_summary_or_snippet": str(lead_row.get("summary_or_snippet") or "").strip(),
    }


def _food_line_primary_disqualification_reason(row: dict[str, Any], previous_context: dict[str, Any]) -> str:
    if not bool(row.get("pressure_signal")):
        return str(row.get("pressure_reason") or "not a pressure signal").strip() or "not a pressure signal"
    freshness_status = str(row.get("freshness_status") or "").strip()
    freshness_reason = str(row.get("freshness_disqualification_reason") or "").strip()
    if freshness_status == "stale_outside_daily_window":
        return freshness_reason or "stale/outside daily window"
    if freshness_status in {"missing_source_published_date", "unparsed_source_published_date"}:
        return freshness_reason or "missing or unparsed source published date"
    if freshness_status == "url_path_only":
        return freshness_reason or "url path date alone is not a verified publication date"
    if _is_reused_previous_lead(row, previous_context):
        previous_date = str(previous_context.get("previous_edition_date") or "").strip()
        if previous_date:
            return f"reused prior lead from {previous_date}"
        return "reused prior lead"
    return ""


def _annotate_food_line_primary_eligibility(rows: list[dict[str, Any]], previous_context: dict[str, Any]) -> None:
    for row in rows:
        disqualification_reason = _food_line_primary_disqualification_reason(row, previous_context)
        row["primary_eligible"] = not bool(disqualification_reason)
        row["primary_disqualification_reason"] = disqualification_reason


def _is_reused_previous_lead(row: dict[str, Any], previous_context: dict[str, Any]) -> bool:
    if not previous_context:
        return False
    previous_source_record_id = str(previous_context.get("lead_source_record_id") or "").strip()
    previous_canonical_url = str(previous_context.get("lead_canonical_url") or "").strip()
    current_source_record_id = str(row.get("source_record_id") or "").strip()
    current_canonical_url = canonical_url(str(row.get("url") or ""))
    if previous_source_record_id and current_source_record_id == previous_source_record_id:
        return True
    if previous_canonical_url and current_canonical_url and current_canonical_url == previous_canonical_url:
        return True
    return False


def _select_primary_pressure_signal(
    rows: list[dict[str, Any]],
    edition_date: str,
    previous_context: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, str]:
    pressure_rows = [row for row in rows if bool(row.get("pressure_signal"))]
    reused_rows = [row for row in pressure_rows if _is_reused_previous_lead(row, previous_context)]
    current_rows = [row for row in pressure_rows if row not in reused_rows and bool(row.get("primary_eligible", True))]
    if current_rows:
        primary = sorted(current_rows, key=lambda r: (_lead_score(r, edition_date), str(r.get("source_record_id") or "")), reverse=True)[0]
        role = _source_role(primary)
        why = f"selected new primary pressure signal ({role}, {primary.get('map_category')})"
        return primary, reused_rows, why, "new_primary"
    if reused_rows:
        reused_rows = sorted(reused_rows, key=lambda r: (_lead_score(r, edition_date), str(r.get("source_record_id") or "")), reverse=True)
        if previous_context.get("previous_edition_date"):
            why = f"no new primary pressure signal qualified today; prior lead from {previous_context['previous_edition_date']} remains under continuing pressure"
        else:
            why = "no new primary pressure signal qualified today; continuing pressure remains under review"
        return None, reused_rows, why, "continuing_only"
    if pressure_rows:
        top = sorted(pressure_rows, key=lambda r: (_lead_score(r, edition_date), str(r.get("source_record_id") or "")), reverse=True)[0]
        reason = str(top.get("primary_disqualification_reason") or _food_line_primary_disqualification_reason(top, previous_context) or "no new primary pressure signal qualified today").strip()
        return None, [], f"no new primary pressure signal qualified today; {reason}", "none"
    return None, [], "no pressure signal qualified today", "none"


def _editorial_status(rows: list[dict[str, Any]]) -> str:
    if len(rows) < MIN_USABLE:
        return "sparse"
    if sum(1 for row in rows if bool(row.get("pressure_signal"))) == 0:
        return "monitoring/context"
    return "daily_signal"


def _scope_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    local_signal_count = sum(1 for row in rows if _source_role(row) == "local_signal")
    state_signal_count = sum(1 for row in rows if str(row.get("state") or "").strip().upper() not in {"", "US"})
    national_context_count = sum(1 for row in rows if str(row.get("state") or "").strip().upper() == "US")
    return {
        "local_signal_count": local_signal_count,
        "state_signal_count": state_signal_count,
        "national_context_count": national_context_count,
    }


def _pressure_status(rows: list[dict[str, Any]]) -> str:
    if len(rows) < MIN_USABLE:
        return "sparse"
    if any(bool(row.get("pressure_signal")) for row in rows):
        return "pressure_detected"
    return "monitoring_context_only"


def _normalize_source_row(row: dict[str, Any], index: int) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    if not isinstance(row, dict):
        return None, ["invalid field type: record is not an object"]
    source_url = _as_text(row.get("url") or row.get("source_url"))
    primary_source_url = _as_text(row.get("primary_source_url") or row.get("study_url") or row.get("report_url"))
    secondary_source_url = _as_text(row.get("secondary_source_url") or row.get("article_url"))
    preferred_source_url = primary_source_url or source_url
    if primary_source_url and not secondary_source_url and source_url and canonical_url(source_url) != canonical_url(primary_source_url):
        secondary_source_url = source_url
    normalized = {
        "source_record_id": _as_text(row.get("source_record_id") or row.get("id")),
        "title": _as_text(row.get("title")),
        "url": preferred_source_url,
        "publisher": _as_text(row.get("publisher") or "Unknown publisher"),
        "published_at": _as_text(row.get("published_at") or row.get("published_date")),
        "published_date_basis": _as_text(row.get("published_date_basis") or row.get("date_basis") or row.get("source_published_date_basis")),
        "page_metadata_date": _as_text(
            row.get("page_metadata_date")
            or row.get("page_metadata_published_at")
            or row.get("source_page_metadata_date")
            or row.get("metadata_date")
            or row.get("published_metadata_date")
        ),
        "date_provenance_warning": _as_text(row.get("date_provenance_warning") or row.get("published_at_warning")),
        "retrieved_at": _as_text(row.get("retrieved_at") or row.get("published_at") or row.get("published_date")),
        "summary_or_snippet": _as_text(row.get("summary_or_snippet") or row.get("summary") or row.get("text") or row.get("note")),
        "evidence_text": _as_text(row.get("evidence_text")),
        "evidence_text_basis": _as_text(row.get("evidence_text_basis")),
        "pressure_match_terms": [str(item).strip() for item in (row.get("pressure_match_terms") or []) if str(item).strip()],
        "pressure_verification_status": _as_text(row.get("pressure_verification_status")),
        "pressure_signal": row.get("pressure_signal"),
        "pressure_type": _as_text(row.get("pressure_type")),
        "pressure_reason": _as_text(row.get("pressure_reason")),
        "pressure_summary": _as_text(row.get("pressure_summary")),
        "source_type": _as_text(row.get("source_type") or "manual"),
        "source_family": _as_text(row.get("source_family") or row.get("family")),
        "source_role": _as_text(row.get("source_role")),
        "state": _as_text(row.get("state")),
        "issue_tags": _as_tags(row.get("issue_tags") or row.get("tags")),
        "map_category": _as_text(row.get("map_category") or row.get("category") or row.get("signal_category")),
        "location_name": _as_text(row.get("location_name") or row.get("location")),
        "country": _as_text(row.get("country")),
        "source_purpose": _as_text(row.get("source_purpose")),
        "supported_product_geography": row.get("supported_product_geography", True),
        "primary_source_url": primary_source_url or preferred_source_url,
        "secondary_source_url": secondary_source_url,
        "source_traceability_role": _as_text(row.get("source_traceability_role") or row.get("traceability_role")),
    }
    for key in ("source_record_id", "title", "url", "published_at", "summary_or_snippet", "source_family", "state", "map_category", "location_name"):
        if key == "published_at":
            has_verified_or_audit_date = bool(normalized["published_at"]) or bool(normalized["page_metadata_date"]) or bool(_url_path_date(normalized["url"])[0])
            if not has_verified_or_audit_date:
                reasons.append("missing required field: published_at")
            continue
        if not _as_text(normalized.get(key)):
            reasons.append(f"missing required field: {key}")
    if not isinstance(normalized["issue_tags"], list):
        reasons.append("invalid field type: issue_tags/tags must be list or comma-separated string")
    if normalized["url"] and not normalized["url"].startswith(("http://", "https://")):
        reasons.append("missing required field: url")
    if reasons:
        return None, reasons
    if not normalized["retrieved_at"]:
        normalized["retrieved_at"] = utc_now()
    return normalized, []


def _load_source_file(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [], []
    payload = _read_json(path)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("sources")
        if not isinstance(rows, list):
            raise ValueError("unsupported wrapper shape: object must contain a 'sources' list")
    else:
        raise ValueError("unsupported wrapper shape: root must be a list or object with a 'sources' list")
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        normalized, reasons = _normalize_source_row(row, index)
        if normalized is None:
            rejected.append({"record_index": index, "record_id": _as_text(row.get("source_record_id") or row.get("id")) if isinstance(row, dict) else "", "reasons": reasons})
            continue
        valid.append(normalized)
    return valid, rejected, []


def _source_merge_url(row: dict[str, Any]) -> str:
    primary = canonical_url(str(row.get("primary_source_url") or ""))
    url = canonical_url(str(row.get("url") or ""))
    publisher = str(row.get("publisher") or "").strip().lower()
    traceability_role = str(row.get("source_traceability_role") or "").strip().lower()
    if primary and (publisher == "msn" or "msn" in url or "wrapper" in traceability_role or "syndicated" in traceability_role):
        return primary
    return primary or url


def _merged_sources(root: Path, date: str, *, include_discovery_candidates: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    manual_path = _manual_source_path(root, date)
    auto_path = _auto_source_path(root, date)
    discovery_path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / date / "discovery_sources.json"
    manual_rows, manual_rejected, diagnostics = _load_source_file(manual_path) if manual_path.exists() else ([], [], [])
    discovery_rows, discovery_rejected, discovery_diagnostics = ([], [], [])
    if include_discovery_candidates and discovery_path.exists():
        discovery_rows, discovery_rejected, discovery_diagnostics = _load_source_file(discovery_path)
    auto_rows, auto_rejected, _ = _load_source_file(auto_path) if auto_path.exists() else ([], [], [])
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in manual_rows:
        url_key = _source_merge_url(row)
        title_key = normalize_title(str(row.get("title") or ""))
        if url_key in seen_urls or title_key in seen_titles:
            diagnostics.append(f"manual record skipped due to duplicate override: {row.get('source_record_id') or row.get('title')}")
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        merged.append(row)
    for row in discovery_rows:
        url_key = _source_merge_url(row)
        title_key = normalize_title(str(row.get("title") or ""))
        if url_key in seen_urls or title_key in seen_titles:
            diagnostics.append(f"discovery record skipped due to manual duplicate override: {row.get('source_record_id') or row.get('title')}")
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        merged.append(row)
    for row in auto_rows:
        url_key = _source_merge_url(row)
        title_key = normalize_title(str(row.get("title") or ""))
        if url_key in seen_urls or title_key in seen_titles:
            diagnostics.append(f"auto record skipped due to manual duplicate override: {row.get('source_record_id') or row.get('title')}")
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        merged.append(row)
    return merged, [*manual_rejected, *discovery_rejected, *auto_rejected], [*diagnostics, *discovery_diagnostics]


def source_adequacy(sources: list[dict[str, Any]]) -> dict[str, Any]:
    families = sorted({str(r.get("source_family") or "").strip() for r in sources if str(r.get("source_family") or "").strip()})
    local = [r for r in sources if str(r.get("state") or "").strip()]
    status = "daily"
    label = "Daily edition"
    editorial = _editorial_status(sources)
    if editorial == "sparse":
        status = "limited"
        label = "Limited / sparse source day"
    elif editorial == "monitoring/context":
        status = "monitoring_context"
        label = "Monitoring/context edition"
    elif len(sources) < MIN_ITEMS or len(families) < MIN_FAMILIES or len(local) < MIN_LOCAL:
        status = "limited"
        label = "Limited / sparse source day"
    scopes = _scope_counts(sources)
    return {
        "status": status,
        "label": label,
        "source_count": len(sources),
        "source_families": families,
        "local_signal_count": scopes["local_signal_count"],
        "state_signal_count": scopes["state_signal_count"],
        "national_context_count": scopes["national_context_count"],
    }


def _human_date(date: str) -> str:
    dt = datetime.strptime(date, "%Y-%m-%d")
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _episode_title(date: str) -> str:
    return f"Food Line Briefing — {_human_date(date)}"


def _audio_episode_title(date: str) -> str:
    return _episode_title(date)


def _audio_review_summary(sources: list[dict[str, Any]]) -> str:
    return _public_audit_summary(sources)


def _audio_lead_summary(lead: dict[str, Any] | None) -> str:
    summary = _food_line_public_summary_sentence(lead, max_words=60).strip()
    if not summary:
        return ""
    return summary.rstrip(".") + "."


def _food_line_bluesky_summary_clause(lead: dict[str, Any] | None) -> str:
    lead = lead or {}
    candidates = [
        _food_line_public_summary_sentence(lead, max_words=60),
        _audio_lead_summary(lead),
        _food_line_public_pressure_point(lead),
    ]
    publisher = str(lead.get("publisher") or lead.get("source_name") or "").strip()
    location = str(lead.get("location_name") or lead.get("state") or "").strip()
    for candidate in candidates:
        clause = re.sub(r"\s+", " ", str(candidate or "").strip()).rstrip(".")
        if not clause or _food_line_public_summary_is_generic(clause):
            continue
        clause = re.sub(r"^reported that\s+", "", clause, flags=re.IGNORECASE)
        clause = re.sub(r"^reported on\s+", "", clause, flags=re.IGNORECASE)
        clause = re.sub(r"^why\s+", "", clause, flags=re.IGNORECASE)
        clause = re.sub(r"^how\s+", "", clause, flags=re.IGNORECASE)
        clause = clause.strip(" ,;:-")
        if not clause:
            continue
        if publisher and clause.lower().startswith(publisher.lower()):
            clause = clause[len(publisher) :].lstrip(" ,:-")
        if location and clause.lower().startswith(f"in {location.lower()}"):
            clause = clause[3 + len(location) :].lstrip(" ,:-")
        if clause and not clause.endswith("."):
            clause += "."
        if clause and clause != ".":
            return clause.rstrip(".")
    if location:
        return f"{publisher} shared a source-backed update on {location}".strip()
    return f"{publisher} shared a source-backed update".strip()


def _food_line_audio_topic_label(row: dict[str, Any] | None) -> str:
    pressure_type = str((row or {}).get("pressure_type") or "").strip().lower()
    if pressure_type == "demand strain":
        return "food-bank demand"
    if pressure_type == "service reduction":
        return "pantry strain"
    if pressure_type == "benefit disruption":
        return "SNAP disruption"
    if pressure_type == "child meal gap":
        return "summer meal gaps"
    if pressure_type == "senior meal strain":
        return "senior meal strain"
    if pressure_type == "access gap":
        return "food access gaps"
    if pressure_type == "household hardship":
        return "household food pressure"
    return "food pressure"


def _food_line_audio_index_teaser(lead: dict[str, Any] | None, continuing_rows: list[dict[str, Any]] | None = None) -> str:
    lead = lead or {}
    continuing_rows = list(continuing_rows or [])
    parts: list[str] = []

    lead_location = str(lead.get("location_name") or lead.get("state") or "").strip()
    lead_topic = _food_line_audio_topic_label(lead)
    if lead_location:
        parts.append(f"{lead_topic} in {lead_location}")
    elif lead:
        parts.append(lead_topic)

    if continuing_rows:
        continuing = continuing_rows[0]
        continuing_location = str(continuing.get("location_name") or continuing.get("state") or "").strip()
        continuing_topic = _food_line_audio_topic_label(continuing)
        if continuing_location:
            parts.append(f"continuing {continuing_topic} in {continuing_location}")
        else:
            parts.append(f"continuing {continuing_topic}")

    if not parts:
        return "Today's Food Line briefing tracks current food pressure signals."
    return "Today's Food Line briefing tracks " + " and ".join(parts) + "."


def _food_line_audio_core_recap(row: dict[str, Any] | None) -> str:
    row = row or {}
    location = str(row.get("location_name") or row.get("state") or "").strip()
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    subject = location or "the lead"
    if pressure_type == "demand strain":
        return f"The {subject} signal points to pantry demand and supply strain."
    if pressure_type == "service reduction":
        return f"The {subject} signal points to tighter pantry supply and fewer food options."
    if pressure_type == "benefit disruption":
        return f"The {subject} signal points to benefit disruption that can push households toward food pantries."
    if pressure_type == "child meal gap":
        return f"The {subject} signal points to summer meal gaps for children and families."
    if pressure_type == "senior meal strain":
        return f"The {subject} signal points to strain on senior meal programs."
    if pressure_type == "access gap":
        return f"The {subject} signal points to food access gaps."
    if pressure_type == "household hardship":
        return f"The {subject} signal points to household food pressure."
    return f"The {subject} signal points to food-pressure strain."


def _food_line_audio_other_signal_summary(row: dict[str, Any] | None) -> str:
    row = row or {}
    nonlocal_data = _food_line_is_nonlocal_data_signal(row)
    evidence_excerpt = _public_evidence_excerpt(row)
    location = _food_line_natural_location_label(row)
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip()
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    candidates = (
        [evidence_excerpt, pressure_summary, _food_line_public_summary_sentence(row, max_words=80)]
        if nonlocal_data
        else [pressure_summary, _food_line_public_summary_sentence(row, max_words=80), evidence_excerpt]
    )
    for candidate in candidates:
        if not candidate or candidate == FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
            continue
        if _food_line_public_summary_is_generic(candidate):
            continue
        body = re.sub(r"\s+", " ", str(candidate).strip()).rstrip(".")
        prefix = f"In {location}, {publisher} reported that" if location and publisher else f"{publisher} reported that" if publisher else f"In {location},"
        return _food_line_ensure_final_punctuation(f"{prefix} {body}".strip())
    title = str(row.get("title") or "").strip()
    if title:
        return title.rstrip(".")
    return "Another current Food Line signal remains in view."


def _food_line_public_pressure_point(lead: dict[str, Any] | None) -> str:
    lead = lead or {}
    pressure_summary = _audio_lead_summary(lead)
    if not pressure_summary:
        return "Source-backed pressure was limited."
    sentences = [pressure_summary.rstrip(".") + "."]
    title = str(lead.get("title") or "")
    evidence_text = str(lead.get("evidence_text") or "")
    evidence_excerpt = clean_food_line_public_evidence_excerpt(
        evidence_text,
        title=title,
        limit=360,
    )
    if evidence_excerpt and evidence_excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
        if evidence_excerpt.lower() not in pressure_summary.lower():
            sentences.append(evidence_excerpt.rstrip(".") + ".")
    else:
        figure_match = re.search(r"(\d+)\s*(?:percent|%)\s+increase", evidence_text, flags=re.IGNORECASE)
        if figure_match:
            percent = figure_match.group(1)
            sentences.append(
                f"One East Texas pantry operator described a {percent} percent increase over three weeks in people asking for food assistance."
            )
            return " ".join(sentences[:2])
        publisher = str(lead.get("publisher") or lead.get("source_name") or "the source").strip()
        location = str(lead.get("location_name") or "").strip()
        pressure_type = str(lead.get("pressure_type") or "").strip() or "pressure evidence"
        affected_groups = _affected_groups_display(list(lead.get("affected_groups") or []))
        fallback = f"{publisher} reported rising demand"
        if location:
            fallback += f" in {location}"
        fallback += f", with {pressure_type}"
        if affected_groups:
            fallback += f" affecting {affected_groups}"
        sentences.append(fallback.rstrip(".") + ".")
    return " ".join(sentences[:2])


def _food_line_why_it_matters(lead: dict[str, Any] | None, scope_label: str | None = None) -> str:
    lead = lead or {}
    location = str(lead.get("location_name") or "").strip()
    evidence_text = " ".join(
        part
        for part in (
            str(lead.get("evidence_text") or "").strip(),
            str(lead.get("summary_or_snippet") or "").strip(),
            str(lead.get("pressure_summary") or "").strip(),
        )
        if part
    ).lower()
    delay_or_pause_context = any(
        phrase in evidence_text
        for phrase in (
            "benefit delay",
            "benefits delayed",
            "benefits were delayed",
            "benefits paused",
            "benefit disruption",
            "benefits face shutdown pause",
            "snap benefits face shutdown pause",
        )
    )
    snap_cut_context = any(
        phrase in evidence_text
        for phrase in (
            "snap cuts",
            "snap reductions",
            "loss of snap support",
            "cuts to snap",
            "snap support",
        )
    )
    if not delay_or_pause_context and not snap_cut_context:
        return ""
    if delay_or_pause_context:
        sentence = "Benefits were delayed or paused, so households that rely on SNAP may turn to nearby food pantries."
    else:
        sentence = "Cuts to SNAP and other USDA programs are leaving more families with fewer options."
    if location:
        return f"In {location}, {sentence}"
    return sentence


def _food_line_audio_why_it_matters(lead: dict[str, Any] | None) -> str:
    lead = lead or {}
    location = str(lead.get("location_name") or "").strip()
    evidence_text = " ".join(
        part
        for part in (
            str(lead.get("evidence_text") or "").strip(),
            str(lead.get("summary_or_snippet") or "").strip(),
            str(lead.get("pressure_summary") or "").strip(),
        )
        if part
    ).lower()
    delay_or_pause_context = any(
        phrase in evidence_text
        for phrase in (
            "benefit delay",
            "benefits delayed",
            "benefits were delayed",
            "benefits paused",
            "benefit disruption",
        )
    )
    snap_cut_context = any(
        phrase in evidence_text
        for phrase in (
            "snap cuts",
            "snap reductions",
            "loss of snap support",
            "cuts to snap",
            "snap support",
            "snap benefit",
            "snap benefits face",
        )
    )
    if not delay_or_pause_context and not snap_cut_context:
        return ""
    if delay_or_pause_context:
        sentence = "Benefits were delayed or paused, so households that rely on SNAP may turn to nearby food pantries."
    else:
        sentence = "Cuts to SNAP and other USDA programs are leaving more families with fewer options."
    if location:
        return f"In {location}, {sentence}"
    return sentence


def _count_word(count: int) -> str:
    return "one" if count == 1 else str(count)


def _food_line_publishing_note(
    sources: list[dict[str, Any]],
    lead: dict[str, Any] | None = None,
    *,
    public_rows: list[dict[str, Any]] | None = None,
) -> str:
    total_count = len(sources)
    verified_count = len(public_rows) if public_rows is not None else len(_public_source_rows(sources))
    excluded_count = max(0, total_count - verified_count)
    publisher_count = len({str(row.get("publisher") or "").strip() for row in (public_rows or []) if str(row.get("publisher") or "").strip()})
    public_phrase = f"{_count_word(verified_count)} public source"
    if verified_count != 1:
        public_phrase += "s"
    publisher_phrase = f"{_count_word(publisher_count)} publisher"
    if publisher_count != 1:
        publisher_phrase += "s"
    reviewed_phrase = "one record" if total_count == 1 else f"{total_count} records"
    if excluded_count == 0:
        second_sentence = "Food Line reviewed the full source set and excluded nothing."
    elif excluded_count == 1:
        second_sentence = "Food Line reviewed the full source set and excluded one record."
    else:
        second_sentence = f"Food Line reviewed {reviewed_phrase} and excluded {excluded_count} that were duplicate, stale, unrelated, or not strong enough for public use."
    return (
        f"This briefing is based on {public_phrase} from {publisher_phrase}. "
        f"{second_sentence} {_food_line_reported_signal_limitation()} "
        "Source details and review records are preserved for traceability."
    )


def _food_line_source_note() -> str:
    return (
        f"{_food_line_reported_signal_limitation()} "
        "Open the public source table for source links, traceability, and cleaned excerpts."
    )


def _food_line_accountability_note(
    sources: list[dict[str, Any]],
    lead: dict[str, Any] | None = None,
    *,
    public_rows: list[dict[str, Any]] | None = None,
) -> str:
    return _food_line_publishing_note(sources, lead, public_rows=public_rows)


def _food_line_lead_pressure_lane(row: dict[str, Any] | None) -> str:
    row = row or {}
    family = str(row.get("source_family") or "").strip().lower()
    state = str(row.get("state") or "").strip().upper()
    if family in BASELINE_FAMILIES:
        return "baseline_context"
    if family == "school_meals_child_nutrition":
        return "local_child_nutrition" if state not in {"", "US"} else "national_systemic"
    if family in {"food_bank_provider", "local_news", "local_reporting", "public_radio", "nonprofit_news", "rural_access", "senior_meals"}:
        return "local_operational"
    if family in {"state_official", "state_policy_news", "federal_official"}:
        return "state_disruption"
    if family in {"national_news", "economic_data"}:
        return "national_systemic"
    if state in {"", "US"}:
        return "national_systemic"
    return "local_operational"


def _food_line_lead_pressure_scope_label(row: dict[str, Any] | None) -> str:
    lane = _food_line_lead_pressure_lane(row)
    if lane == "national_systemic":
        return "National / systemic"
    return "Local / operational"


def _food_line_lead_pressure_scope_text(row: dict[str, Any] | None) -> str:
    lane = _food_line_lead_pressure_lane(row)
    if lane == "national_systemic":
        return "national/systemic"
    if lane == "state_disruption":
        return "state-level disruption"
    if lane == "local_child_nutrition":
        return "local summer meal / child nutrition"
    if lane == "local_operational":
        return "local/operational"
    return "local/operational"


def _food_line_bluesky_post_text(
    date: str,
    lead: dict[str, Any] | None,
    public_url: str | None = None,
    *,
    context_rows: list[dict[str, Any]] | None = None,
) -> str | None:
    if not lead:
        return None
    context_rows = list(context_rows or [])
    def _normalize_sentence(text: str) -> str:
        sentence = re.sub(r"\s+", " ", str(text or "").strip())
        if not sentence:
            return ""
        if sentence.endswith(":"):
            return sentence
        return sentence.rstrip(".") + "."

    def _shorten_at_word_boundary(text: str, max_len: int) -> str:
        sentence = re.sub(r"\s+", " ", str(text or "").strip())
        if len(sentence) <= max_len:
            return sentence
        if max_len <= 3:
            return "..."[:max_len]
        trimmed = sentence[: max_len - 3].rstrip(" ,;:-.")
        if " " in trimmed:
            trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-.")
        if not trimmed:
            return "..."
        return trimmed + "..."

    def _compose_ranked_post(sentences: list[str], suffix: str, *, max_len: int = 300) -> str:
        clean_sentences = [_normalize_sentence(sentence) for sentence in sentences if str(sentence or "").strip()]
        if not suffix:
            body = " ".join(clean_sentences).strip()
            return _shorten_at_word_boundary(body, max_len)
        suffix = suffix.strip()
        if len(suffix) >= max_len:
            return suffix[:max_len].rstrip()
        for count in range(len(clean_sentences), 0, -1):
            body = " ".join(clean_sentences[:count]).strip()
            text = f"{body} {suffix}".strip()
            if len(text) <= max_len:
                return text
        if len(clean_sentences) >= 2:
            tail = " ".join(clean_sentences[1:]).strip()
            if tail:
                available = max_len - len(suffix) - len(tail) - 2
                if available > 0:
                    shortened_head = _shorten_at_word_boundary(clean_sentences[0], available)
                    text = f"{shortened_head} {tail} {suffix}".strip()
                    if len(text) <= max_len:
                        return text
        available = max_len - len(suffix) - 1
        if available <= 0:
            return suffix
        shortened = _shorten_at_word_boundary(clean_sentences[0], available)
        return f"{shortened} {suffix}".strip()

    date_text = _human_date(date)
    publisher = str(lead.get("publisher") or lead.get("source_name") or "the source").strip()
    summary_clause = _food_line_bluesky_summary_clause(lead).strip().rstrip(".")
    suffix = public_url or ""
    if _food_line_is_nonlocal_data_signal(lead):
        summary = str(lead.get("pressure_summary") or "").strip()
        if summary:
            first_sentence = summary.split(" The signal points", 1)[0].strip()
            first_sentence = re.sub(r", especially among .*$", "", first_sentence, flags=re.IGNORECASE)
            first_sentence = re.sub(r"\breported on research that linked\b", "reported on research linking", first_sentence, flags=re.IGNORECASE)
            lead_sentence = first_sentence.rstrip(".") + "."
        else:
            lead_sentence = f"{publisher} reported on research linked to food-pressure conditions among some U.S. households."
        second_sentence = ""
        if "household financial stress" not in lead_sentence.lower():
            second_sentence = "The signal points to household financial stress as a food-pressure pathway."
        return _compose_ranked_post(
            [f"Food Line Dispatch, {date_text}: {lead_sentence}", second_sentence],
            suffix,
        )
    lead_sentence = _food_line_public_summary_sentence(lead, max_words=28).strip().rstrip(".")
    if not lead_sentence or _food_line_public_summary_is_generic(lead_sentence):
        lead_sentence = summary_clause
    lead_sentence = re.sub(r"^National\s+", "", lead_sentence, flags=re.IGNORECASE).strip()
    lead_sentence = re.sub(r"^reported that\s+", "", lead_sentence, flags=re.IGNORECASE).strip()
    lead_sentence = re.sub(r"^reports?\s+", "", lead_sentence, flags=re.IGNORECASE).strip()
    lead_sentence = re.sub(
        r"^food insecurity pressure is rising\b",
        "rising food insecurity pressure",
        lead_sentence,
        flags=re.IGNORECASE,
    )
    if context_rows and "rising food insecurity pressure" in lead_sentence.lower():
        lead_sentence = re.sub(r"\s+as\s+.*$", "", lead_sentence, flags=re.IGNORECASE).strip()
    if not lead_sentence:
        lead_sentence = summary_clause or "source-backed food pressure remains under review"
    lead_text = f"{publisher} reports {lead_sentence}".strip()
    lead_text = _normalize_sentence(lead_text)
    context_sentence = ""
    if context_rows:
        context_row = context_rows[0]
        context_publisher = str(context_row.get("publisher") or context_row.get("source_name") or "").strip()
        context_title = str(context_row.get("title") or "").strip()
        context_summary = str(context_row.get("pressure_summary") or context_row.get("claim_supported") or "").strip()
        if _food_line_is_research_context_signal(context_row):
            if context_publisher:
                context_sentence = f"A {context_publisher} research release adds national context on food-security disparities."
            else:
                context_sentence = "A research release adds national context on food-security disparities."
        elif context_summary:
            context_sentence = _food_line_compact_public_sentence(context_summary, max_words=20)
        elif context_title:
            context_sentence = f"{context_title} adds national context."
    if context_sentence:
        return _compose_ranked_post(
            [f"Food Line Dispatch, {date_text}: {lead_text}", context_sentence],
            suffix,
        )
    return _compose_ranked_post(
        [
            f"Food Line Dispatch, {date_text}: {lead_text}",
            "Source-backed public briefing:",
        ],
        suffix,
    )


def _food_line_primary_signal_card_html(row: dict[str, Any], *, scope_label: str) -> str:
    title = html.escape(str(row.get("title") or ""))
    publisher = html.escape(str(row.get("publisher") or ""))
    location = html.escape(str(row.get("location_name") or row.get("state") or ""))
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    evidence_excerpt = _public_evidence_excerpt(row)
    affected_groups = _affected_groups_display(row.get("affected_groups"))
    source_url = html.escape(str(row.get("url") or ""))
    why_it_matters = html.escape(_food_line_why_it_matters(row, scope_label))
    parts = ["<article class='food-line-source-card'>", f"<h3>{title}</h3>"]
    parts.extend(
        [
            f"<p><strong>Source:</strong> {publisher}</p>",
            f"<p><strong>Where:</strong> {location}</p>",
        ]
    )
    what_happened = pressure_summary or (evidence_excerpt if evidence_excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK else "")
    if what_happened:
        parts.append(f"<p><strong>What happened:</strong> {html.escape(what_happened)}</p>")
    if affected_groups:
        parts.append(f"<p><strong>Who may be affected:</strong> {html.escape(affected_groups)}</p>")
    if why_it_matters:
        parts.append(f"<p><strong>Why it matters:</strong> {why_it_matters}</p>")
    parts.append(
        f"<p><strong>Read the source:</strong> <a href=\"{source_url}\" target=\"_blank\" rel=\"noopener noreferrer\">{source_url}</a></p>"
    )
    parts.append("</article>")
    return "".join(parts)


def _food_line_transcript_source_links_html(date: str) -> str:
    return f"""
    <p><a href="/food-line/audio/{date}.mp3">Listen or download the MP3</a></p>
    <p><a href="{date}-transcript.html">Open the transcript</a></p>
    <p><a href="../editions/{date}/source_table.html">Open the public source table for {html.escape(date)}</a></p>
    <p><a href="../editions/{date}/">Open the public edition</a></p>
    <p><a href="podcast.xml">Open the podcast feed</a></p>
    """


def _food_line_audio_links_html(date: str, *, include_transcript_link: bool, audio_mp3_url: str | None = None) -> str:
    audio_link = audio_mp3_url or f"/food-line/audio/{date}.mp3"
    parts = [f'    <p><a href="{html.escape(audio_link)}">Listen or download the MP3</a></p>']
    if include_transcript_link:
        parts.append(f'    <p><a href="/food-line/audio/{date}-transcript.html">Read the transcript</a></p>')
    parts.extend(
        [
            f'    <p><a href="/food-line/editions/{date}/source_table.html">Open the source table</a></p>',
            f'    <p><a href="/food-line/editions/{date}/">Open the public edition</a></p>',
            '    <p><a href="podcast.xml">Open the podcast feed</a></p>',
        ]
    )
    return "\n".join(parts)


def _food_line_source_card_html(
    row: dict[str, Any],
    *,
    label: str | None = None,
    heading_prefix: str | None = None,
    background: bool = False,
) -> str:
    title = html.escape(str(row.get("title") or ""))
    publisher = html.escape(str(row.get("publisher") or ""))
    location = html.escape(str(row.get("location_name") or row.get("state") or ""))
    source_url = html.escape(str(row.get("url") or ""))
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    evidence_excerpt_raw = _public_evidence_excerpt(row)
    evidence_excerpt = html.escape(evidence_excerpt_raw)
    affected_groups = _affected_groups_display(row.get("affected_groups"))
    if heading_prefix:
        heading = f"{html.escape(heading_prefix)} {title}".strip()
    else:
        heading = title
    parts = ["<li class='food-line-source-card'>", f"<h3>{heading}</h3>"]
    if label and not background:
        parts.append(f"<p><strong>{html.escape(label)}</strong></p>")
    if publisher:
        location_part = f" - {location}" if location else ""
        parts.append(f"<p><strong>Source:</strong> {publisher}{location_part}</p>")
    if background:
        parts.append(f"<p><strong>Why it is included:</strong> {html.escape(_food_line_background_reason(row))}</p>")
    if pressure_summary and not background:
        parts.append(f"<p><strong>What happened:</strong> {html.escape(pressure_summary)}</p>")
    if evidence_excerpt_raw and evidence_excerpt_raw != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK and not background:
        parts.append(f"<p><strong>What the source says:</strong> {evidence_excerpt}</p>")
    if affected_groups and not background:
        parts.append(f"<p><strong>Who may be affected:</strong> {html.escape(affected_groups)}</p>")
    if not background:
        why_it_matters = html.escape(_food_line_why_it_matters(row, None))
        if why_it_matters:
            parts.append(f"<p><strong>Why it matters:</strong> {why_it_matters}</p>")
    elif evidence_excerpt_raw and evidence_excerpt_raw != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
        parts.append(f"<p><strong>What the source says:</strong> {evidence_excerpt}</p>")
    parts.append(
        f"<p><strong>Read the source:</strong> <a href=\"{source_url}\" target=\"_blank\" rel=\"noopener noreferrer\">{source_url}</a></p>"
    )
    parts.append("</li>")
    return "".join(parts)


def _food_line_public_pressure_type_label(row: dict[str, Any]) -> str:
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    mapping = {
        "demand strain": "Demand strain",
        "benefit disruption": "Benefit disruption",
        "service reduction": "Service reduction",
        "access gap": "Access gap",
        "funding risk": "Funding risk",
        "price pressure": "Price pressure",
        "child meal gap": "Child meal gap",
        "senior meal strain": "Senior meal strain",
        "rural grocery access": "Rural access",
        "disaster disruption": "Disaster disruption",
        "household hardship": "Household hardship",
        "context only": "Context",
    }
    if pressure_type in mapping:
        return mapping[pressure_type]
    return pressure_type.replace("_", " ").title() if pressure_type else "Context"


def _food_line_public_location_label(row: dict[str, Any]) -> str:
    location = str(row.get("location_name") or "").strip()
    if location:
        return location
    county = str(row.get("county_name") or "").strip()
    if county:
        return county
    state = str(row.get("state") or "").strip().upper()
    if state == "US":
        return "United States"
    if state:
        return state
    scope = str(row.get("location_scope") or "").strip()
    if scope:
        return scope.replace("_", " ")
    return "the reported area"


def _food_line_state_display_name(state: str) -> str:
    state = str(state or "").strip().upper()
    if not state:
        return ""
    if state == "US":
        return "United States"
    return _FOOD_LINE_STATE_NAMES.get(state, state.title())


def _food_line_natural_location_label(row: dict[str, Any]) -> str:
    location = str(row.get("location_name") or "").strip()
    state = str(row.get("state") or "").strip()
    state_name = _food_line_state_display_name(state)
    if not location:
        return state_name or _food_line_public_location_label(row)
    if state_name and state_name.lower() not in location.lower():
        if re.search(r",\s*[A-Z]{2}$", location):
            return re.sub(r",\s*[A-Z]{2}$", f", {state_name}", location)
        return f"{location}, {state_name}"
    return location


def _food_line_is_national_location(row: dict[str, Any], location: str | None = None) -> bool:
    location_label = str(location if location is not None else _food_line_natural_location_label(row)).strip().lower()
    scope = str(row.get("location_scope") or "").strip().lower()
    state = str(row.get("state") or "").strip().upper()
    return scope in {"national", "us"} or state == "US" or location_label in {"united states", "the united states"}


def _food_line_public_signal_reader_label(row: dict[str, Any]) -> str:
    row = row or {}
    title = str(row.get("title") or "").strip()
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip().lower()
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    location = _food_line_natural_location_label(row)
    summary = " ".join(
        part
        for part in (
            str(row.get("pressure_summary") or "").strip(),
            str(row.get("summary_or_snippet") or "").strip(),
            str(row.get("evidence_text") or "").strip(),
        )
        if part
    ).lower()
    if pressure_type == "demand strain":
        if "horry county" in location.lower() or "wpde" in publisher:
            return "Horry County food providers report rising pantry demand"
        if location:
            return f"{location} food providers report rising pantry demand"
    if pressure_type == "fuel cost strain":
        if "eastern oklahoma" in summary or "tulsa flyer" in publisher:
            return "Eastern Oklahoma food bank says diesel costs are reducing meal capacity"
        if location:
            return f"{location} food bank says diesel costs are reducing meal capacity"
    if pressure_type == "benefit access decline":
        if "wkrn" in publisher or location:
            return "Tennessee SNAP enrollment dropped by more than 100,000"
    if pressure_type == "benefit disruption":
        if location:
            return f"{location} reports benefit disruption that can push households toward food pantries"
    if pressure_type == "child meal gap":
        if location:
            return f"{location} reports summer meal gaps for children"
    if pressure_type == "service reduction":
        if location:
            return f"{location} reports tighter pantry supply and fewer food options"
    if pressure_type == "senior meal strain":
        if location:
            return f"{location} reports strain on senior meal programs"
    if pressure_type == "access gap":
        if location:
            return f"{location} reports food access gaps"
    if title and not _food_line_public_summary_is_generic(title):
        cleaned_title = re.sub(r"\s+", " ", title).strip().rstrip(".")
        return cleaned_title
    summary_sentence = _food_line_public_summary_sentence(row, max_words=14)
    if summary_sentence:
        return summary_sentence.rstrip(".")
    if location:
        return f"{location} food-pressure signal"
    return "Food-pressure signal"


def _food_line_public_signal_reader_sentence(row: dict[str, Any]) -> str:
    row = row or {}
    location = _food_line_natural_location_label(row)
    national_location = _food_line_is_national_location(row, location)
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip()
    summary = " ".join(
        part
        for part in (
            str(row.get("pressure_summary") or "").strip(),
            str(row.get("summary_or_snippet") or "").strip(),
            str(row.get("evidence_text") or "").strip(),
        )
        if part
    ).lower()
    if pressure_type == "demand strain":
        if location:
            return f"In {location}, food providers reported rising pantry demand and child food insecurity."
        return "Food providers reported rising pantry demand and child food insecurity."
    if pressure_type == "fuel cost strain":
        if location:
            return f"In {location}, higher diesel costs are reducing food-bank meal capacity."
        return "Higher diesel costs are reducing food-bank meal capacity."
    if pressure_type == "benefit access decline":
        if location and publisher:
            return f"In {location}, {publisher} reported that SNAP enrollment fell by more than 100,000 people, though the source does not prove why people left the program."
        if location:
            return f"In {location}, SNAP enrollment fell by more than 100,000 people, though the source does not prove why people left the program."
        if publisher:
            return f"{publisher} reported that SNAP enrollment fell by more than 100,000 people, though the source does not prove why people left the program."
        return "SNAP enrollment fell by more than 100,000 people, though the source does not prove why people left the program."
    if pressure_type == "benefit disruption":
        if location:
            return f"In {location}, the source reported benefit disruption that can push households toward food pantries."
        return "The source reported benefit disruption that can push households toward food pantries."
    if pressure_type == "child meal gap":
        if location:
            return f"In {location}, summer meal gaps are adding strain for children and families."
        return "Summer meal gaps are adding strain for children and families."
    if pressure_type == "service reduction":
        if location:
            return f"In {location}, pantry supply is tightening and fewer food options are available."
        return "Pantry supply is tightening and fewer food options are available."
    if pressure_type == "senior meal strain":
        if location:
            return f"In {location}, senior meal programs are under strain."
        return "Senior meal programs are under strain."
    if pressure_type == "access gap":
        if location:
            return f"In {location}, food-access gaps are adding strain."
        return "Food-access gaps are adding strain."
    if summary:
        sentence = _food_line_public_summary_sentence(row, max_words=40).strip().rstrip(".")
        if sentence:
            if national_location:
                sentence = f"Nationally, {sentence}"
            elif location and location.lower() not in sentence.lower():
                sentence = f"In {location}, {sentence}"
            return sentence + "."
    if national_location:
        return "Nationally, food-pressure conditions were reported."
    if location:
        return f"In {location}, food-pressure conditions were reported."
    return "Food-pressure conditions were reported."


def _food_line_public_date_label(row: dict[str, Any]) -> str:
    raw = _as_text(row.get("source_published_date") or row.get("published_at") or "")
    if not raw:
        return "date not listed"
    for candidate in (raw[:10], raw):
        try:
            return _human_date(datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat())
        except ValueError:
            continue
    return raw[:10] if len(raw) >= 10 else raw


def _food_line_public_signal_metadata_line(row: dict[str, Any]) -> str:
    publisher = str(row.get("publisher") or row.get("source_name") or "Unknown publisher").strip()
    pressure_label = _food_line_public_pressure_type_label(row)
    location_label = _food_line_public_location_label(row)
    date_label = _food_line_public_date_label(row)
    return f"{publisher} · {pressure_label} · {location_label} · {date_label}"


def _food_line_public_signal_context_line(row: dict[str, Any]) -> str:
    scope = _food_line_public_location_label(row)
    date_label = _food_line_public_date_label(row)
    return f"This source record is scoped to {scope} and dated {date_label}."


def _food_line_public_signal_item_html(row: dict[str, Any]) -> str:
    title = html.escape(str(row.get("title") or ""))
    summary = _food_line_public_summary_sentence(row, max_words=45)
    source_title = html.escape(str(row.get("title") or "Source"))
    source_url = html.escape(str(row.get("url") or ""))
    publisher = html.escape(str(row.get("publisher") or row.get("source_name") or ""))
    metadata_line = html.escape(_food_line_public_signal_metadata_line(row))
    context_line = html.escape(_food_line_public_signal_context_line(row))
    parts = [
        "<article class='food-line-source-card'>",
        f"<h3>{title}</h3>",
        f"<p>{metadata_line}</p>",
    ]
    if summary:
        parts.append(f"<p>{html.escape(summary)}</p>")
    parts.append(f"<p><strong>Context:</strong> {context_line}</p>")
    parts.append(f"<p><strong>Limits:</strong> {html.escape(_food_line_public_limits_note(row))}</p>")
    parts.append("<p><strong>Sources</strong></p>")
    parts.append("<ul>")
    parts.append(f'<li><a href="{source_url}" target="_blank" rel="noopener noreferrer">{source_title}</a> - {publisher}</li>')
    parts.append("</ul>")
    parts.append("</article>")
    return "".join(parts)


def _food_line_claim_supported_text(row: dict[str, Any]) -> str:
    summary = _food_line_public_summary_sentence(row, max_words=80).strip()
    if summary and not _food_line_public_summary_is_generic(summary):
        location = _food_line_public_location_label(row)
        cleaned_summary, stripped_location = _food_line_clean_claim_location_tail(summary, location)
        if stripped_location and location and location.lower() not in cleaned_summary.lower():
            cleaned_summary = f"In {location}, {cleaned_summary}"
        return _food_line_ensure_final_punctuation(cleaned_summary)
    evidence_excerpt = _public_evidence_excerpt(row)
    if evidence_excerpt and evidence_excerpt != FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK:
        return _food_line_ensure_final_punctuation(evidence_excerpt)
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    if pressure_summary:
        return _food_line_ensure_final_punctuation(pressure_summary)
    title = str(row.get("title") or "").strip()
    if title:
        return _food_line_ensure_final_punctuation(title)
    return "Source-backed food-pressure claim."


def _food_line_ensure_final_punctuation(text: str) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    if not sentence:
        return ""
    if sentence[-1] in ".!?":
        return sentence
    return f"{sentence}."


def _food_line_clean_claim_location_tail(text: str, location: str) -> tuple[str, bool]:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    location = re.sub(r"\s+", " ", str(location or "").strip()).strip(" ,.;:-")
    if not sentence or not location:
        return sentence, False
    escaped_location = re.escape(location)
    patterns = (
        rf"^(?P<body>.*?)(?:\s*,?\s+in\s+{escaped_location})$",
        rf"^(?P<body>.*?)(?:\s*,?\s+{escaped_location})$",
    )
    for pattern in patterns:
        match = re.match(pattern, sentence, flags=re.IGNORECASE)
        if not match:
            continue
        body = match.group("body").rstrip(" ,;:-")
        if body:
            return body, True
    return sentence, False


def _food_line_claim_interpretation(row: dict[str, Any]) -> str:
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    location = _food_line_public_location_label(row)
    source_role = str(row.get("source_role") or "").strip().lower()
    if source_role == "policy_analysis" or _food_line_is_national_location(row, location):
        if "snap" in pressure_type or "benefit" in pressure_type:
            return "This indicates national policy pressure around SNAP eligibility and food assistance access."
        return "This is a national policy-pressure signal related to food assistance access."
    if pressure_type == "demand strain":
        return f"This points to pantry supply strain in {location}."
    if pressure_type == "service reduction":
        return f"This points to pantry capacity strain in {location}."
    if pressure_type == "benefit disruption":
        return f"This points to benefit disruption that can push households toward local help in {location}."
    if pressure_type == "benefit access decline":
        return f"This points to SNAP access pressure in {location}."
    if pressure_type == "child meal gap":
        return f"This points to summer meal gaps adding strain in {location}."
    if pressure_type == "senior meal strain":
        return f"This points to senior meal strain in {location}."
    if pressure_type == "hospital-linked caregiver food insecurity":
        return f"This points to caregiver food insecurity linked to hospitalization in {location}."
    if pressure_type == "access gap":
        return f"This points to local food-access gaps in {location}."
    if pressure_type == "household hardship":
        return f"This points to household food pressure in {location}."
    if location and location != "the reported area":
        return f"This points to local food-access strain in {location}."
    return "This points to local food-access strain."


def _food_line_claim_confidence(row: dict[str, Any]) -> str:
    role = str(row.get("source_role") or "").strip().lower()
    evidence_level = str(row.get("evidence_level") or "").strip().lower()
    if role in {"research_signal"} or evidence_level in {"research report", "official data/statistic"}:
        return "moderate"
    if role in {"local_signal", "daily_signal"} and evidence_level in {"direct reported hardship", "local reporting", "provider reported strain", "news report"}:
        return "moderate"
    if role in {"provider_signal", "policy_context", "resource_context"} or evidence_level in {"provider reported strain", "official notice", "policy/benefit change", "official data/statistic"}:
        return "moderate"
    return "low"


def _food_line_claim_limitation(row: dict[str, Any]) -> str:
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    location = _food_line_public_location_label(row)
    scope = str(row.get("location_scope") or "").strip().lower()
    role = str(row.get("source_role") or "").strip().lower()
    family = str(row.get("source_family") or "").strip().lower()
    evidence_level = str(row.get("evidence_level") or "").strip().lower()
    source_purpose = str(row.get("source_purpose") or "").strip().lower()
    map_category = str(row.get("map_category") or "").strip().lower()
    research_context = bool(
        role == "research_signal"
        or family == "policy_research"
        or source_purpose in {"research_report", "data_release"}
        or evidence_level in {"research context", "research report", "official data/statistic"}
        or "research" in pressure_type
        or "data" in pressure_type
        or "context" in pressure_type
        or "context" in map_category
    )
    if pressure_type == "demand strain":
        base = "The source documents pantry demand and supply strain"
        if location and location != "the reported area":
            base += f" in {location}"
        return base + ", but it does not isolate all causes of the shortage."
    if pressure_type == "service reduction":
        base = "The source documents service strain"
        if location and location != "the reported area":
            base += f" in {location}"
        return base + ", but it does not measure total unmet need across the full service area."
    if pressure_type == "benefit disruption":
        return "The source documents benefit-related pressure, but it does not independently measure total unmet need across all households."
    if pressure_type == "benefit access decline":
        return (
            "SNAP enrollment decline does not by itself prove reduced food need; it may reflect eligibility changes, "
            "recertification churn, administrative barriers, employment or income changes, or policy effects unless "
            "the source isolates causes."
        )
    if pressure_type == "child meal gap":
        return "The source documents summer meal strain, but it does not prove a statewide trend."
    if pressure_type == "senior meal strain":
        return "The source documents senior meal strain, but it does not measure every local access barrier."
    if pressure_type == "hospital-linked caregiver food insecurity":
        return "The source documents caregiver food insecurity linked to hospitalization, but it does not prove a current service disruption or local access failure."
    if research_context:
        if scope in {"national", "us"} or str(row.get("state") or "").strip().upper() == "US":
            return "The source supports a national research/context signal, but it does not prove a current service disruption or local access failure."
        if scope in {"state_local", "local"} or str(row.get("state") or "").strip():
            return "The source supports a local/state research/context signal, but it does not prove a current service disruption or local access failure."
        return "The source supports a research/context signal, but it does not prove a current service disruption or local access failure."
    if scope in {"state_local", "local"} and location and location != "the reported area":
        return f"The source describes conditions in {location}, but it does not prove a broader regional trend."
    if scope in {"national", "us"} or str(row.get("state") or "").strip().upper() == "US":
        return "The source supports a national food-pressure signal, but it does not measure total unmet need."
    return "The source supports a local/state food-pressure claim, but it does not measure total unmet need."


def _food_line_public_limits_note(row: dict[str, Any]) -> str:
    return _food_line_claim_limitation(row)


def _food_line_claim_ledger_row(row: dict[str, Any]) -> dict[str, str]:
    source_title = str(row.get("title") or "").strip()
    publisher = str(row.get("publisher") or row.get("source_name") or "").strip()
    source_url = str(row.get("url") or "").strip()
    published_date = str(row.get("source_published_date") or row.get("published_at") or row.get("page_metadata_date") or "").strip()
    retrieved_date = str(row.get("retrieved_at") or "").strip()
    evidence_level = str(row.get("evidence_level") or "").strip() or ("background context" if not bool(row.get("pressure_signal")) else "direct reported hardship")
    freshness_role = str(row.get("freshness_role") or "").strip()
    location_scope = str(row.get("location_scope") or "").strip()
    return {
        "claim": _food_line_claim_supported_text(row),
        "interpretation": _food_line_claim_interpretation(row),
        "supporting_source": source_title,
        "publisher": publisher,
        "source_url": source_url,
        "published_date": published_date,
        "retrieved_date": retrieved_date,
        "evidence_level": evidence_level,
        "confidence": _food_line_claim_confidence(row),
        "freshness_role": freshness_role,
        "location_scope": location_scope,
        "limitation": _food_line_claim_limitation(row),
    }


def _food_line_claim_ledger_rows(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    *,
    edition_mode: str = "current_update",
) -> list[dict[str, str]]:
    rows = _food_line_public_story_rows(sources, primary_row, continuing_rows, edition_mode=edition_mode)
    return [_food_line_claim_ledger_row(row) for row in rows if str(row.get("url") or "").strip()]


def _food_line_public_edition_status_label(edition_mode: str, reviewed_count: int, public_signal_count: int, excluded_count: int) -> str:
    if edition_mode == "no_current_update":
        return "no-current-update"
    if public_signal_count <= 0:
        return "limited"
    if excluded_count <= 0:
        return "full"
    if public_signal_count <= 2:
        return "limited"
    return "partial"


def _food_line_edition_summary_html(
    reviewed_count: int,
    publisher_count: int,
    status_label: str,
    public_signal_count: int,
    excluded_count: int,
) -> str:
    source_label = "saved source record" if reviewed_count == 1 else "saved source records"
    publisher_label = "publisher" if publisher_count == 1 else "publishers"
    signal_label = "signal" if public_signal_count == 1 else "signals"
    if status_label == "no-current-update":
        return (
            f"<p>This edition is no-qualifying-update: {reviewed_count} {source_label} from {publisher_count} {publisher_label} were available at publish time, "
            f"but no fresh source-backed current food-pressure signal qualified.</p>"
            f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>"
        )
    if status_label == "full":
        return (
            f"<p>This edition is full: {reviewed_count} {source_label} from {publisher_count} {publisher_label} were available at publish time, "
            f"and {public_signal_count} {signal_label} qualified for public presentation.</p>"
            f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>"
        )
    if status_label == "partial":
        return (
            f"<p>This edition is partial: {reviewed_count} {source_label} from {publisher_count} {publisher_label} were available at publish time, "
            f"{public_signal_count} {signal_label} qualified for public presentation, and {excluded_count} record{'s' if excluded_count != 1 else ''} stayed out of the public current-signal sections.</p>"
            f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>"
        )
    return (
        f"<p>This edition is limited: {reviewed_count} {source_label} from {publisher_count} {publisher_label} were available at publish time, "
        f"{public_signal_count} {signal_label} qualified for public presentation, and source coverage may be uneven.</p>"
        f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>"
    )


def _food_line_background_reason(row: dict[str, Any]) -> str:
    family = str(row.get("source_family") or "").strip().lower()
    source_purpose = str(row.get("source_purpose") or "").strip().lower()
    title = str(row.get("title") or "").strip()
    if family == "school_meals_child_nutrition":
        return "USDA FNS provides background on federal summer nutrition programs for children when school is out."
    if family == "economic_data":
        return "USDA ERS defines food security and tracks national food-security trends."
    if family in {"food_bank_provider", "nonprofit_news", "public_radio", "local_news", "local_reporting"}:
        return "This report provides background on local food-assistance conditions that help explain today’s lead story."
    if source_purpose in {"program_description", "resource_page", "official_notice", "research_report", "data_release"}:
        return "This source provides background on the program or topic that frames today’s lead story."
    cleaned_title = title.split(" | ", 1)[0].strip()
    if cleaned_title:
        return f"This source provides background on {cleaned_title.lower()}."
    return "This source provides background for today’s food-access story."


def _food_line_at_a_glance_items(
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    primary_signal_status: str,
) -> str:
    items: list[str] = []
    if primary_row:
        public_rows = _food_line_public_story_rows(sources, primary_row, continuing_rows)
        for row in public_rows[:3]:
            items.append(f"<li>{html.escape(_food_line_public_signal_reader_label(row))}</li>")
    else:
        items.append(f"<li>{html.escape(_food_line_no_current_secondary_note())}</li>")
    return "".join(items)


def _food_line_today_read_html(
    sources: list[dict[str, Any]],
    date: str,
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    primary_signal_status: str,
    previous_context: dict[str, Any],
    public_signal_count: int,
    review_counts: tuple[int, int],
    lead_scope_label: str | None = None,
) -> str:
    reviewed_count, excluded_count = review_counts
    if primary_signal_status == "no_current_update":
        return (
            "<p>No fresh source-backed current food-pressure signal qualified today.</p>"
            "<p>Background records remain in the source audit and public source table only.</p>"
            f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>"
        )
    if primary_row:
        public_rows = _food_line_public_story_rows(sources, primary_row, continuing_rows)
        signal_label = "signal" if public_signal_count == 1 else "signals"
        paragraphs = [f"Today’s Food Line found {public_signal_count} reported pressure {signal_label}."]
        paragraphs.extend(
            sentence
            for sentence in (_food_line_public_signal_reader_sentence(row) for row in public_rows)
            if sentence
        )
        lead_summary_html = "".join(f"<p>{html.escape(sentence)}</p>" for sentence in paragraphs)
        return (
            f"{lead_summary_html}"
            f"<p>The run reviewed {reviewed_count} records and excluded {excluded_count} records that were duplicate, stale, unrelated, or not strong enough for public use.</p>"
        )
    if primary_signal_status == "continuing_only" and continuing_rows:
        return (
            f"<p>Today’s saved source records point to {public_signal_count} reported food-pressure signals.</p>"
            "<p>The current edition keeps the prior qualifying signal in view while new source records are reviewed.</p>"
        )
    return "<p>No fresh source-backed current food-pressure signal qualified today.</p>"


def _food_line_traceability_rows(sources: list[dict[str, Any]], primary_row: dict[str, Any] | None, continuing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in ([primary_row] if primary_row else []) + list(continuing_rows)
        if row
    }
    traceability_rows = [
        row
        for row in sources
        if str(row.get("source_record_id") or "").strip() not in blocked_ids
        and not bool(row.get("pressure_signal"))
        and str(row.get("source_role") or "") in {"background_context", "policy_context", "resource_context", "baseline_condition"}
    ]
    return traceability_rows[:2]


def _food_line_current_secondary_rows(sources: list[dict[str, Any]], primary_row: dict[str, Any] | None, continuing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _food_line_public_inclusion_rows(sources, primary_row, continuing_rows)


def _food_line_no_current_secondary_note() -> str:
    return "No fresh source-backed current food-pressure signal qualified today."


def _food_line_source_mix_html(
    sources: list[dict[str, Any]],
    public_rows: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    primary_signal_status: str,
) -> str:
    _ = primary_row
    _ = continuing_rows
    _ = primary_signal_status
    publisher_names: list[str] = []
    for row in public_rows:
        publisher = str(row.get("publisher") or row.get("source_name") or "").strip()
        if publisher and publisher not in publisher_names:
            publisher_names.append(publisher)
    publisher_text = ", ".join(html.escape(name) for name in publisher_names) if publisher_names else "none"
    return (
        f"<p>Source mix: {len(public_rows)} signals from {len(publisher_names)} publishers. Source coverage may be uneven.</p>"
        f"<p>Publishers: {publisher_text}.</p>"
        "<p><a href=\"./source_table.html\">Open the public source table for source links, traceability, and cleaned excerpts.</a></p>"
    )


def _food_line_source_note_html(*, source_table_url: str | None = None, claim_ledger_url: str | None = None) -> str:
    parts = [
        "<p>This edition is generated only from saved source records available at publish time. "
        "Source coverage may be uneven; signals are included only when a traceable source record exists.</p>",
        f"<p>{html.escape(_food_line_reported_signal_limitation())}</p>",
    ]
    if source_table_url:
        parts.append(f'<p><a href="{html.escape(source_table_url)}">Open the public source table for source links, traceability, and cleaned excerpts.</a></p>')
    if claim_ledger_url:
        parts.append(f'<p><a href="{html.escape(claim_ledger_url)}">Open the claim ledger</a></p>')
    return "".join(parts)


def _food_line_claim_ledger_html(
    date: str,
    sources: list[dict[str, Any]],
    primary_row: dict[str, Any] | None,
    continuing_rows: list[dict[str, Any]],
    *,
    edition_mode: str = "current_update",
    review_counts: tuple[int, int] = (0, 0),
    exclusion_reason_counts: dict[str, int] | None = None,
) -> str:
    reviewed_count, excluded_count = review_counts
    claim_rows = _food_line_claim_ledger_rows(sources, primary_row, continuing_rows, edition_mode=edition_mode)
    claim_count = len(claim_rows)
    exclusion_reason_counts = exclusion_reason_counts or {}
    no_update = edition_mode == "no_current_update" or claim_count == 0
    diagnostic_lines = [
        f"<li>Records reviewed: {reviewed_count}</li>",
        f"<li>Qualified current records: {claim_count}</li>",
        f"<li>Excluded stale: {int(exclusion_reason_counts.get('stale', 0))}</li>",
        f"<li>Excluded duplicate: {int(exclusion_reason_counts.get('duplicate', 0))}</li>",
        f"<li>Excluded resource-only / no pressure signal: {int(exclusion_reason_counts.get('resource-only / no pressure signal', 0))}</li>",
        f"<li>Excluded weak pressure signal: {int(exclusion_reason_counts.get('weak pressure signal', 0))}</li>",
        f"<li>Excluded insufficient source traceability: {int(exclusion_reason_counts.get('insufficient source traceability', 0))}</li>",
    ]
    if claim_rows:
        rows_html = "".join(
            "<tr>"
            f"<td>{html.escape(row['claim'])}</td>"
            f"<td>{html.escape(row['interpretation'])}</td>"
            f"<td>{html.escape(row['supporting_source'])}</td>"
            f"<td>{html.escape(row['publisher'])}</td>"
            f"<td><a href=\"{html.escape(row['source_url'])}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(row['source_url'])}</a></td>"
            f"<td>{html.escape(row['published_date'])}</td>"
            f"<td>{html.escape(row['retrieved_date'])}</td>"
            f"<td>{html.escape(row['evidence_level'])}</td>"
            f"<td>{html.escape(row['confidence'])}</td>"
            f"<td>{html.escape(row['freshness_role'])}</td>"
            f"<td>{html.escape(row['location_scope'])}</td>"
            f"<td>{html.escape(row['limitation'])}</td>"
            "</tr>"
            for row in claim_rows
        )
        summary_html = (
            f"<p>This ledger records {claim_count} public claim{'s' if claim_count != 1 else ''} supported by source-backed Food Line signals for {_human_date(date)}.</p>"
            f"<p>Records reviewed: {reviewed_count}. Public claims: {claim_count}. Excluded records: {excluded_count}.</p>"
        )
    else:
        rows_html = ""
        summary_html = (
            "<p>No current public Food Line claims were made for this edition because no source-backed food-pressure signal met the project’s freshness and evidence standards.</p>"
            f"<p>Records reviewed: {reviewed_count}. Qualified current records: {claim_count}. Excluded records: {excluded_count}.</p>"
        )
    page_footer = footer("../../")
    return f"""{_food_line_theme_styles()}
{header(DISPATCH_NAME, "../../", "../../archive.html", "/food-line/")}
<main class="container briefing food-line-shell">
  <section class="hero food-line-hero">
    {_food_line_logo_html("food-line-logo--edition", "../../assets/")}
    <p class="eyebrow">{_human_date(date)}</p>
    <h1>Food Line Claim Ledger</h1>
    <p>What exactly did each public Food Line source support?</p>
  </section>
  <section class="food-line-panel">
    {summary_html}
    <table class="food-line-source-table">
      <tr>
        <th>Claim</th><th>Interpretation / why it matters</th><th>Supporting source</th><th>Publisher</th><th>Source URL</th><th>Published date</th><th>Retrieved date</th><th>Evidence level</th><th>Confidence</th><th>Freshness role</th><th>Location scope</th><th>Limitation</th>
      </tr>
      {rows_html}
    </table>
    <h2>Diagnostics</h2>
    <ul>
      {''.join(diagnostic_lines)}
    </ul>
    <p><a href="./source_table.html">Open the public source table</a></p>
    <p><a href="./">Return to the edition</a></p>
  </section>
</main>
{page_footer}"""


def _food_line_skip_reason() -> str:
    return "No new primary food-access signal qualified for public Food Line publication."


def _food_line_future_date_reason() -> str:
    return "Same-day and future-dated Food Line public editions are blocked unless explicitly allowed."


def _food_line_local_today() -> date_type:
    override = str(os.getenv("BLUEFERN_FOOD_LINE_CURRENT_DATE") or "").strip()
    if override:
        try:
            return datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date_type.today()


def _food_line_future_date_blocked(edition_date: str, *, allow_future_date: bool) -> tuple[bool, bool]:
    edition_day = datetime.strptime(validate_date(edition_date), "%Y-%m-%d").date()
    local_today = _food_line_local_today()
    is_future = edition_day >= local_today
    return is_future and not allow_future_date, is_future and allow_future_date


def _food_line_diagnostics_paths(root: Path, date: str) -> list[Path]:
    return [
        root / "output" / "review" / DISPATCH_SLUG / date / "run_manifest.json",
        root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / date / "run_manifest.json",
        root / "output" / "review" / DISPATCH_SLUG / date / "map_data.json",
    ]


def _write_food_line_diagnostics_manifest(root: Path, date: str, manifest: dict[str, Any], map_data: dict[str, Any] | None = None) -> None:
    for path in _food_line_diagnostics_paths(root, date):
        if path.name == "map_data.json":
            if map_data is not None:
                _write_json(path, map_data)
            continue
        _write_json(path, manifest)


def _remove_food_line_public_edition(root: Path, date: str) -> None:
    for path in (
        root / "output" / "site" / DISPATCH_SLUG / "editions" / date,
        root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / date,
    ):
        if path.exists():
            shutil.rmtree(path)


def _remove_food_line_audio_artifacts(root: Path, date: str) -> None:
    audio_root = root / "output" / "site" / DISPATCH_SLUG / "audio"
    if not audio_root.exists():
        return
    for path in audio_root.glob(f"{date}*"):
        if path.is_file():
            path.unlink()


def _food_line_public_edition_manifest(root: Path, date: str) -> dict[str, Any] | None:
    manifest_path = root / "output" / "site" / DISPATCH_SLUG / "editions" / date / "edition_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = _read_json(manifest_path)
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _food_line_public_edition_source_rows(root: Path, date: str) -> list[dict[str, Any]]:
    edition_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / date
    manifest = _food_line_public_edition_manifest(root, date) or {}
    sources_path = edition_dir / "sources_manifest.json"
    if not sources_path.exists():
        return []
    try:
        payload = _read_json(sources_path)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(payload, list):
        return []
    lead_id = str(manifest.get("lead_source_record_id") or "").strip()
    continuing_ids = {
        str(item).strip()
        for item in (manifest.get("continuing_pressure_source_record_ids") or [])
        if str(item).strip()
    }
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    by_id = {
        str(row.get("source_record_id") or "").strip(): row
        for row in payload
        if isinstance(row, dict) and str(row.get("source_record_id") or "").strip()
    }
    lead_row = by_id.get(lead_id)
    if lead_row is not None:
        rows.append(lead_row)
        seen_ids.add(lead_id)
    for source_id in manifest.get("continuing_pressure_source_record_ids") or []:
        row_id = str(source_id).strip()
        if not row_id or row_id in seen_ids:
            continue
        row = by_id.get(row_id)
        if row is None:
            continue
        rows.append(row)
        seen_ids.add(row_id)
    for row in payload:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("source_record_id") or "").strip()
        if not row_id:
            continue
        if row_id in seen_ids:
            continue
        if row_id == lead_id or row_id in continuing_ids or str(row.get("public_inclusion_bucket") or "").startswith("included"):
            rows.append(row)
            seen_ids.add(row_id)
    return rows


def _food_line_archive_location_fragment(row: dict[str, Any]) -> str:
    location = _food_line_public_location_label(row)
    location = re.sub(r",\s*[A-Z]{2}$", "", location).strip()
    return location or "the reported area"


def _food_line_archive_signal_fragment(row: dict[str, Any]) -> str:
    location = _food_line_archive_location_fragment(row)
    text = " ".join(
        part.strip().lower()
        for part in (
            str(row.get("title") or ""),
            str(row.get("pressure_summary") or ""),
            str(row.get("claim_supported") or ""),
            str(row.get("summary_or_snippet") or ""),
            str(row.get("evidence_text") or ""),
        )
        if part and str(part).strip()
    )
    pressure_key = _food_line_pressure_type_key(row)
    if pressure_key == "benefit_access_decline" or "snap enrollment" in text:
        return f"{location} SNAP enrollment"
    if "fuel cost" in text or "diesel" in text or pressure_key == "fuel cost strain":
        return f"{location} fuel costs"
    if pressure_key == "service_reduction" and "inventory" in text:
        return f"{location} food-bank inventory"
    if "st. francis house" in text:
        return f"{location} St. Francis House shortage"
    if "shortage" in text or "empty shelves" in text:
        return f"{location} shortage"
    if "pantr" in text:
        return f"{location} pantry demand"
    if "food bank" in text or "food-bank" in text:
        if "surge" in text or "visitors" in text or "demand" in text or "need" in text:
            return f"{location} food-bank strain"
        if "inventory" in text:
            return f"{location} food-bank inventory"
        return f"{location} food-bank pressure"
    if "summer meal" in text or "school meal" in text:
        return f"{location} summer meal strain"
    if "food insecurity" in text:
        return f"{location} food insecurity"
    if pressure_key in {"demand_strain", "service_reduction"}:
        return f"{location} food pressure"
    return f"{location} food-pressure"


def _food_line_join_archive_phrases(phrases: list[str]) -> str:
    cleaned: list[str] = []
    for item in phrases:
        phrase = str(item).strip()
        if phrase:
            cleaned.append(phrase)
    deduped: list[str] = []
    seen: set[str] = set()
    for phrase in cleaned:
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(phrase)
    if not deduped:
        return ""
    if len(deduped) == 1:
        return deduped[0]
    if len(deduped) == 2:
        return f"{deduped[0]} and {deduped[1]}"
    return f"{', '.join(deduped[:-1])}, and {deduped[-1]}"


def _food_line_public_edition_title(root: Path, date: str) -> str:
    manifest = _food_line_public_edition_manifest(root, date) or {}
    try:
        public_signal_count = int(manifest.get("public_signal_count") or 0)
    except (TypeError, ValueError):
        public_signal_count = 0
    if public_signal_count <= 0:
        if str(manifest.get("edition_mode") or "").strip() == "no_current_update":
            return f"{date} — {_food_line_no_current_update_public_label()}"
        return date
    rows = _food_line_public_edition_source_rows(root, date)
    phrases = [_food_line_archive_signal_fragment(row) for row in rows]
    title_body = _food_line_join_archive_phrases(phrases)
    if not title_body:
        title_body = "Pantry demand and summer food-bank strain"
    return f"{date} — {title_body}"


def _food_line_review_only_manifest_path(root: Path, date: str) -> Path:
    return root / "output" / "site" / DISPATCH_SLUG / "editions" / date / "review_render_manifest.json"


def _food_line_review_only_manifest(root: Path, date: str) -> dict[str, Any]:
    path = _food_line_review_only_manifest_path(root, date)
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _food_line_clean_archive_label_fragment(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return ""
    cleaned = cleaned.rstrip(".")
    return cleaned


def _food_line_review_only_summary_fragment(row: dict[str, Any]) -> str:
    summary = _food_line_clean_archive_label_fragment(str(row.get("pressure_summary") or ""))
    if not summary:
        return ""
    lowered = summary.lower()
    prefixes = (
        "nationally, ",
        "in a national policy signal, ",
        "in ",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            summary = summary[len(prefix):].strip()
            lowered = summary.lower()
            break
    if lowered.startswith("frac warned that "):
        summary = "FRAC warns " + summary[len("FRAC warned that "):]
    elif lowered.startswith("frac warned "):
        summary = "FRAC warns " + summary[len("FRAC warned "):]
    summary = summary.rstrip(".")
    replacements = (
        ("a usda proposal to end broad-based categorical eligibility for snap", "SNAP eligibility proposal"),
        ("broad-based categorical eligibility for snap", "SNAP eligibility"),
        ("would increase hunger for families and children", "could increase hunger"),
        ("would increase hunger", "could increase hunger"),
    )
    lowered = summary.lower()
    for old, new in replacements:
        if old in lowered:
            idx = lowered.index(old)
            summary = summary[:idx] + new + summary[idx + len(old):]
            lowered = summary.lower()
    summary = _food_line_clean_archive_label_fragment(summary)
    if not summary:
        return ""
    if len(summary) > 88:
        summary = summary[:85].rstrip(" ,;:-") + "..."
    return summary


def _food_line_review_only_title_fragment(row: dict[str, Any]) -> str:
    title = _food_line_clean_archive_label_fragment(str(row.get("title") or ""))
    if not title:
        return ""
    source_role = str(row.get("source_role") or "").strip().lower()
    for separator in (" - Food Research & Action Center", " | Food Research & Action Center", " - FRAC", " | FRAC"):
        if title.endswith(separator):
            title = title[: -len(separator)].rstrip()
            break
    lowered = title.lower()
    if source_role == "policy_analysis" and "broad-based categorical eligibility for snap" in lowered and "increase hunger" in lowered:
        return "FRAC warns SNAP eligibility proposal could increase hunger"
    title = _food_line_clean_archive_label_fragment(title)
    return title


def _food_line_review_only_archive_label(root: Path, date: str) -> str:
    manifest = _food_line_review_only_manifest(root, date)
    if not manifest:
        return ""
    if str(manifest.get("render_mode") or "").strip() != "review_only":
        return ""
    try:
        rendered_public_claim_count = int(manifest.get("rendered_public_claim_count") or 0)
    except (TypeError, ValueError):
        rendered_public_claim_count = 0
    if rendered_public_claim_count <= 0:
        return ""
    lead_row = {
        "pressure_summary": str(manifest.get("lead_pressure_summary") or ""),
        "title": str(manifest.get("lead_title") or ""),
        "pressure_type": str(manifest.get("lead_pressure_type") or ""),
        "source_role": str(manifest.get("lead_source_role") or ""),
    }
    fragments = [
        _food_line_review_only_summary_fragment(lead_row),
        _food_line_review_only_title_fragment(lead_row),
        _food_line_clean_archive_label_fragment(str(lead_row.get("pressure_type") or "")),
    ]
    for fragment in fragments:
        if not fragment:
            continue
        if fragment.lower() == f"food line dispatch - {date}".lower():
            continue
        return f"{date} — {fragment}"
    return ""


def _food_line_public_edition_label(root: Path, date: str) -> str:
    title = _food_line_public_edition_title(root, date)
    if title and title != date:
        generic = f"{date} — Food Line Dispatch - {date}"
        if title != generic:
            return title
    review_only_title = _food_line_review_only_archive_label(root, date)
    if review_only_title:
        return review_only_title
    return title


def _food_line_home_archive_dates(root: Path, *, max_edition_date: str | None = None) -> list[str]:
    site_root = root / "output" / "site"
    public_dates = set(discover_public_edition_dates(site_root, DISPATCH_SLUG, max_edition_date=max_edition_date))
    editions_root = site_root / DISPATCH_SLUG / "editions"
    if editions_root.exists():
        for path in editions_root.iterdir():
            if not path.is_dir() or len(path.name) != 10:
                continue
            edition_date = path.name
            if max_edition_date and edition_date > max_edition_date:
                continue
            manifest = _food_line_review_only_manifest(root, edition_date)
            if not manifest:
                continue
            if str(manifest.get("render_mode") or "").strip() != "review_only":
                continue
            try:
                rendered_public_claim_count = int(manifest.get("rendered_public_claim_count") or 0)
            except (TypeError, ValueError):
                rendered_public_claim_count = 0
            if rendered_public_claim_count <= 0:
                continue
            public_dates.add(edition_date)
    return sorted(public_dates, reverse=True)


def _food_line_discovery_gap_report_paths(root: Path, date: str) -> tuple[Path, Path]:
    report_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "discovery_gap" / date
    return report_dir / "discovery_gap_report.json", report_dir / "discovery_gap_report.md"


def _food_line_discovery_gap_summary(
    root: Path,
    date: str,
    public_story_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    report_path, report_markdown_path = _food_line_discovery_gap_report_paths(root, date)
    summary = {
        "run": False,
        "report_found": False,
        "report_path": str(report_path),
        "report_markdown_path": str(report_markdown_path),
        "likely_qualifying_count": 0,
        "unreviewed_likely_qualifying_count": 0,
        "warning": "",
    }
    if not report_path.exists():
        return summary
    summary["report_found"] = True
    try:
        report = _read_json(report_path)
    except Exception as exc:  # noqa: BLE001
        summary["warning"] = f"Food Line discovery gap report could not be read: {exc}"
        return summary
    if not isinstance(report, dict):
        summary["warning"] = "Food Line discovery gap report was not a JSON object."
        return summary
    summary["run"] = True
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        candidates = []
    likely_rows = [
        row
        for row in candidates
        if isinstance(row, dict) and str(row.get("classification") or "").strip() == "likely_qualifying"
    ]
    summary["likely_qualifying_count"] = len(likely_rows)
    included_urls = {
        canonical_url(str(row.get("url") or row.get("source_url") or row.get("normalized_url") or ""))
        for row in public_story_rows
        if canonical_url(str(row.get("url") or row.get("source_url") or row.get("normalized_url") or ""))
    }
    unreviewed_rows = []
    for row in likely_rows:
        candidate_url = canonical_url(str(row.get("url") or row.get("source_url") or row.get("normalized_url") or ""))
        if not candidate_url:
            continue
        if candidate_url in included_urls:
            continue
        unreviewed_rows.append(row)
    summary["unreviewed_likely_qualifying_count"] = len(unreviewed_rows)
    if unreviewed_rows:
        count = len(unreviewed_rows)
        summary["warning"] = (
            f"Food Line discovery gap check found {count} likely qualifying candidate"
            f"{'s' if count != 1 else ''} not included in this edition."
            f" See {report_markdown_path}."
        )
    return summary


def _food_line_map_rendered_marker_count_from_data(map_data: dict[str, Any]) -> int:
    for key in ("rendered_marker_count", "pressure_marker_count"):
        value = map_data.get(key)
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count
    for key in ("mapped_markers", "markers", "plotted_markers"):
        payload = map_data.get(key)
        if isinstance(payload, list):
            return len(payload)
    diagnostics = map_data.get("diagnostics")
    if isinstance(diagnostics, dict):
        for key in ("rendered_marker_count", "pressure_marker_count"):
            value = diagnostics.get(key)
            try:
                count = int(value or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                return count
        for key in ("mapped_markers", "markers", "plotted_markers"):
            payload = diagnostics.get(key)
            if isinstance(payload, list):
                return len(payload)
    return 0


def _food_line_map_is_available(root: Path) -> bool:
    map_root = root / "output" / "site" / DISPATCH_SLUG / "map"
    map_index_path = map_root / "index.html"
    map_data_path = map_root / "map_data.json"
    if not map_index_path.exists():
        return False
    try:
        map_html = map_index_path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = MAP_RENDERED_COUNT_RE.search(map_html)
    if match and int(match.group(1)) > 0:
        return True
    if map_data_path.exists():
        try:
            map_data = _read_json(map_data_path)
        except Exception:  # noqa: BLE001
            return False
        if isinstance(map_data, dict) and _food_line_map_rendered_marker_count_from_data(map_data) > 0:
            return True
    return False


def _food_line_public_edition_is_valid(root: Path, date: str, *, allow_future_date: bool) -> bool:
    if not DATE_RE.match(date):
        return False
    try:
        edition_day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return False
    if not allow_future_date and edition_day > _food_line_local_today():
        return False
    manifest = _food_line_public_edition_manifest(root, date)
    if not manifest:
        return False
    if str(manifest.get("dispatch_slug") or "").strip() != DISPATCH_SLUG:
        return False
    if str(manifest.get("edition_date") or "").strip() not in {"", date}:
        return False
    if manifest.get("public_rendered") is not True:
        return False
    if manifest.get("future_date_blocked") is True:
        return False
    edition_mode = str(manifest.get("edition_mode") or "").strip()
    qualified_primary_count = int(manifest.get("qualified_primary_count") or 0)
    if edition_mode == "no_current_update":
        if qualified_primary_count != 0:
            return False
    elif qualified_primary_count <= 0:
        return False
    if str(manifest.get("skip_reason") or "").strip():
        return False
    return True


def _prune_food_line_public_artifacts(root: Path, *, allow_future_date: bool) -> list[str]:
    site_root = root / "output" / "site" / DISPATCH_SLUG
    editions_root = site_root / "editions"
    audio_root = site_root / "audio"
    removed: list[str] = []
    if not editions_root.exists():
        return removed
    for edition_dir in sorted(path for path in editions_root.iterdir() if path.is_dir() and DATE_RE.match(path.name)):
        date = edition_dir.name
        if _food_line_public_edition_is_valid(root, date, allow_future_date=allow_future_date):
            continue
        removed.extend([str(edition_dir), str(root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / date)])
        if edition_dir.exists():
            shutil.rmtree(edition_dir)
        dispatch_edition_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / date
        if dispatch_edition_dir.exists():
            shutil.rmtree(dispatch_edition_dir)
        for path in (
            audio_root / f"{date}.json",
            audio_root / f"{date}-transcript.html",
            audio_root / f"{date}.mp3",
            audio_root / f"{date}.tmp.mp3",
        ):
            if path.exists():
                path.unlink()
    return removed


def _food_line_latest_public_audio_metadata(root: Path, *, max_edition_date: str | None = None) -> dict[str, Any] | None:
    site_root = root / "output" / "site"
    audio_root = site_root / DISPATCH_SLUG / "audio"
    candidates = sorted(
        (path for path in audio_root.glob("*.json") if not path.name.endswith("flash-briefing.json")),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in candidates:
        edition_date = path.stem
        if not DATE_RE.match(edition_date):
            continue
        try:
            payload = _read_json(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        audio_file = str(payload.get("audio_file") or "").strip()
        audio_mp3_path = audio_root / audio_file if audio_file else None
        if not audio_mp3_path or not audio_mp3_path.exists() or audio_mp3_path.stat().st_size <= 0:
            continue
        if bool(payload.get("audio_available")) or bool(payload.get("podcast_enclosure_present")):
            return payload
    return None


def _write_food_line_audio_status_page(root: Path, date: str, skip_reason: str, *, include_date: bool = True) -> None:
    audio_root = root / "output" / "site" / DISPATCH_SLUG / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    latest_audio = _food_line_latest_public_audio_metadata(root)
    if latest_audio:
        edition_date = str(latest_audio.get("edition_date") or "").strip()
        episode_title = str(latest_audio.get("episode_title") or f"Food Line Briefing — {_human_date(edition_date)}").strip()
        transcript_url = f"/food-line/audio/{edition_date}-transcript.html"
        audio_mp3_url = str(latest_audio.get("audio_mp3_url") or latest_audio.get("audio_url") or f"/food-line/audio/{edition_date}.mp3").strip()
        source_table_url = f"/food-line/editions/{edition_date}/source_table.html"
        podcast_enclosure_text = "present"
        page_footer = footer("../")
        body = f"""{_food_line_theme_styles()}
{header(DISPATCH_NAME, "../", "../archive.html", "/food-line/")}
<main class="home food-line-shell">
  <section class="food-line-hero">
    {_food_line_logo_html("food-line-logo--audio", "../assets/")}
    <p class="eyebrow">The Blue Fern Co.</p>
    <h1>Food Line Audio</h1>
    <p>{html.escape(episode_title)}</p>
  </section>
  <section class="food-line-panel">
    <h2>Latest episode</h2>
    <p><strong>{html.escape(episode_title)}</strong></p>
    <p><audio controls preload="none" src="{html.escape(audio_mp3_url)}"></audio></p>
    <p><a href="{html.escape(audio_mp3_url)}">Listen or download the MP3</a></p>
    <p><a href="{html.escape(transcript_url)}">Read the transcript</a></p>
    <p><a href="{html.escape(source_table_url)}">Open the source table</a></p>
    <p><a href="podcast.xml">Open the podcast feed</a></p>
    <p><strong>Podcast enclosure:</strong> {podcast_enclosure_text}</p>
    <p><a href="../archive.html">Back to the Food Line archive</a></p>
  </section>
</main>
{page_footer}"""
        _write_text(audio_root / "index.html", _food_line_page("Food Line Audio", f"{BASE_URL}/food-line/audio/index.html", "../assets/site.css", body))
        return
    episode_line = (
        f"No public audio episode was published for {_human_date(date)}."
        if include_date
        else "No public audio episode was published for this run."
    )
    page_footer = footer("../")
    body = f"""{_food_line_theme_styles()}
{header(DISPATCH_NAME, "../", "../archive.html", "/food-line/")}
<main class="home food-line-shell">
  <section class="food-line-hero">
    {_food_line_logo_html("food-line-logo--audio", "../assets/")}
    <p class="eyebrow">The Blue Fern Co.</p>
    <h1>Food Line Audio</h1>
    <p>{episode_line}</p>
  </section>
  <section class="food-line-panel">
    <h2>Status</h2>
    <p>{html.escape(skip_reason)}</p>
    <p>{html.escape(_food_line_reported_signal_limitation())}</p>
    <p>The podcast feed remains available for previously published public episodes.</p>
    <p><a href="podcast.xml">Open the podcast feed</a></p>
    <p><a href="../archive.html">Back to the Food Line archive</a></p>
  </section>
</main>
{page_footer}"""
    _write_text(audio_root / "index.html", _food_line_page("Food Line Audio", f"{BASE_URL}/food-line/audio/index.html", "../assets/site.css", body))


def _food_line_edition_navigation_html(previous_date: str | None) -> str:
    previous_link = ""
    if previous_date:
        previous_link = f'<a href="../{previous_date}/">Previous edition</a>'
    else:
        previous_link = "<span>Previous edition unavailable</span>"
    return (
        "<nav class='food-line-edition-nav'>"
        '<a href="../../archive.html">Archive</a>'
        '<a href="../../index.html">Dispatches home</a>'
        f"{previous_link}"
        "</nav>"
    )


def _food_line_audio_story_sections(
    date: str,
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    lead: dict[str, Any] | None,
    editorial_status: str,
    primary_signal_status: str,
    continuing_rows: list[dict[str, Any]],
    previous_context: dict[str, Any],
    *,
    edition_mode: str = "current_update",
) -> dict[str, list[str]]:
    _ = sources
    _ = adequacy
    _ = previous_context
    section_rows = _food_line_public_section_rows(sources, lead, continuing_rows, edition_mode=edition_mode)
    context_rows = section_rows["traceability"]
    opening = [f"This is the Food Line briefing for {_human_date(date)}."]
    today_read: list[str] = []
    main_story: list[str] = []
    what_else: list[str] = []
    policy_benefits: list[str] = []
    provider_operations: list[str] = []
    sources_behind: list[str] = []
    closing = ["This has been the Food Line briefing from The Blue Fern Co."]
    current_public_signal_count = 0 if edition_mode == "no_current_update" else sum(
        1
        for row in _food_line_public_rendered_rows(sources, lead, continuing_rows)
        if _food_line_public_usage_label(row, lead, continuing_rows, edition_mode=edition_mode) != "Background reference"
    )
    if current_public_signal_count > 0 and len(sources) > 0:
        signal_label = "signal" if current_public_signal_count == 1 else "signals"
        record_label = "record" if len(sources) == 1 else "records"
        opening.append(
            f"Today's briefing is based on {current_public_signal_count} current public {signal_label} selected from {len(sources)} reviewed {record_label}."
        )
    if edition_mode == "no_current_update":
        today_read.append("No current Food Line update was published because no fresh source-backed current-story records were available.")
        today_read.append("The source audit below keeps the run traceable without presenting stale sources as current stories.")
    elif lead:
        lead_summary = _audio_lead_summary(lead).strip().rstrip(".")
        if lead_summary:
            today_read.append(lead_summary + ".")
        audio_why_it_matters = _food_line_audio_why_it_matters(lead)
        if audio_why_it_matters:
            today_read.append(audio_why_it_matters)
        main_story.append(_food_line_audio_core_recap(lead))
    elif primary_signal_status == "continuing_only" and continuing_rows:
        continuing = continuing_rows[0]
        publisher = str(continuing.get("publisher") or continuing.get("source_name") or "the source").strip()
        location = str(continuing.get("location_name") or continuing.get("state") or "").strip()
        today_read.extend(
            [
                "No new primary story qualified today.",
                f"The Food Line review keeps the prior {publisher} story in view" + (f" in {location}" if location else "") + " while new records are reviewed.",
            ]
        )
        main_story.append(f"In {location or 'the reported area'}, {publisher} remains the prior lead while new records are reviewed.")
        if previous_context.get("previous_edition_date"):
            today_read.append(f"It was the lead in the {previous_context['previous_edition_date']} edition.")
    else:
        today_read.extend(
            [
                "No new primary story qualified today.",
                "The edition remains a source-backed check on food-access conditions while new records are reviewed.",
            ]
        )
    if editorial_status == "monitoring/context":
        today_read.append("This edition is monitoring and context only, with no verified daily pressure record.")
    elif editorial_status == "sparse":
        today_read.append("This edition is limited because the source set is sparse.")
    if edition_mode == "no_current_update":
        what_else.append("No current secondary items were published in this edition.")
    else:
        secondary_rows: list[dict[str, Any]] = []
        seen_secondary_ids: set[str] = set()

        def add_secondary_row(row: dict[str, Any] | None) -> None:
            if not row:
                return
            row_id = str(row.get("source_record_id") or "").strip()
            if row_id and row_id in seen_secondary_ids:
                return
            if row_id:
                seen_secondary_ids.add(row_id)
            secondary_rows.append(row)

        for row in (section_rows["core"][1:] if len(section_rows["core"]) > 1 else []):
            add_secondary_row(row)
        for row in section_rows["other"]:
            add_secondary_row(row)
        for row in section_rows["policy"]:
            add_secondary_row(row)
        for row in section_rows["provider"]:
            add_secondary_row(row)

        if secondary_rows:
            summaries = [_food_line_audio_other_signal_summary(row) for row in secondary_rows]
            summaries = [summary for summary in summaries if summary]
            if summaries:
                what_else.extend(summaries)
            else:
                transition = _food_line_audio_secondary_transition(lead, secondary_rows)
                if transition:
                    what_else.append(transition)
        else:
            what_else.append("No additional current Food Line signal qualified today.")
    public_rows = _food_line_public_rendered_rows(sources, lead, continuing_rows)
    page_rows = [
        row
        for row in public_rows
        if _food_line_public_usage_label(row, lead, continuing_rows) != "Background reference"
    ]
    background_rows = [
        row
        for row in public_rows
        if _food_line_public_usage_label(row, lead, continuing_rows) == "Background reference"
    ]
    excluded_count = max(0, len(sources) - len(public_rows))
    if edition_mode == "no_current_update":
        sources_behind.append("Source links, excerpts, and background references are available in the public source table.")
    elif background_rows:
        sources_behind.append("Source links, excerpts, and background references are available in the public source table.")
    else:
        sources_behind.append("Source links and excerpts are available in the public source table.")
    return {
        "opening": opening,
        "today_read": today_read,
        "main_story": main_story,
        "what_else": what_else,
        "policy_benefits": policy_benefits,
        "provider_operations": provider_operations,
        "sources_behind": sources_behind,
        "closing": closing,
    }


def _audio_script(
    date: str,
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    lead: dict[str, Any] | None,
    editorial_status: str,
    primary_signal_status: str,
    continuing_rows: list[dict[str, Any]],
    previous_context: dict[str, Any],
    *,
    edition_mode: str = "current_update",
) -> str:
    sections = _food_line_audio_story_sections(
        date,
        sources,
        adequacy,
        lead,
        editorial_status,
        primary_signal_status,
        continuing_rows,
        previous_context,
        edition_mode=edition_mode,
    )
    lines = (
        sections["opening"]
        + sections["today_read"]
        + sections["main_story"]
        + sections["what_else"]
        + sections["sources_behind"]
        + sections["closing"]
    )
    return "\n\n".join(line for line in lines if line)


def _food_line_audio_transcript_sections_html(sections: dict[str, list[str]]) -> list[str]:
    section_labels = [
        ("Opening", "opening"),
        ("Today&apos;s Read", "today_read"),
        ("Core Food Pressure Signals", "main_story"),
        ("Other Food Line Signals", "what_else"),
        ("Policy / Benefits Signals", "policy_benefits"),
        ("Provider / Operations Signals", "provider_operations"),
        ("Source Note", "sources_behind"),
        ("Closing", "closing"),
    ]
    html_parts: list[str] = []
    for heading, key in section_labels:
        entries = sections.get(key) or []
        if not entries:
            continue
        html_parts.append(f"    <h2>{heading}</h2>")
        html_parts.extend(f"    <p>{html.escape(entry)}</p>" for entry in entries)
    return html_parts


def _food_line_audio_secondary_transition(lead: dict[str, Any] | None, rows: list[dict[str, Any]]) -> str:
    _ = lead
    if not rows:
        return ""
    first_type = str(rows[0].get("pressure_type") or "").strip().lower()
    if first_type in {"demand strain", "service reduction"}:
        return "Another report points to related pressure on pantry capacity."
    if first_type in {"benefit disruption", "access gap", "child meal gap", "household hardship"}:
        return "A second signal adds regional context."
    return "Another report adds related food-pressure context."


def _food_line_audio_secondary_group_summary(lead: dict[str, Any] | None, rows: list[dict[str, Any]]) -> str:
    if len(rows) < 2:
        return ""
    pressure_types = {str((lead or {}).get("pressure_type") or "").strip().lower()}
    pressure_types.update(str(row.get("pressure_type") or "").strip().lower() for row in rows)
    if "benefit disruption" in pressure_types and "demand strain" in pressure_types:
        return "Together, these reports show pressure around benefit access and pantry demand."
    if pressure_types.intersection({"demand strain", "service reduction"}):
        return "Together, these reports show pressure on pantry demand and local supply."
    return "Together, these reports show related food-pressure signals across more than one source."


def _food_line_audio_index_sections_html(sections: dict[str, list[str]]) -> list[str]:
    section_labels = [
        ("Opening", "opening"),
        ("Today&apos;s Read", "today_read"),
        ("Core Food Pressure Signals", "main_story"),
        ("Other Food Line Signals", "what_else"),
        ("Policy / Benefits Signals", "policy_benefits"),
        ("Provider / Operations Signals", "provider_operations"),
        ("Source Note", "sources_behind"),
        ("Closing", "closing"),
    ]
    html_parts: list[str] = []
    for heading, key in section_labels:
        entries = sections.get(key) or []
        if not entries:
            continue
        html_parts.append(f"    <h2>{heading}</h2>")
        html_parts.extend(f"    <p>{html.escape(entry)}</p>" for entry in entries)
    return html_parts


def _what_changed_text(status: str, lead: dict[str, Any] | None, role_counts: dict[str, int]) -> str:
    if status == "monitoring/context":
        return "What changed today: this edition is a monitoring/context check-in, with no new local daily-signal records in today’s source set."
    if status == "sparse":
        return "What changed today: source coverage is limited, so this update only reports partial source-backed signals."
    if not lead:
        return "What changed today: source-backed signal selection was limited."
    summary = str(lead.get("pressure_summary") or "").strip()
    if summary:
        return f"What changed today: {summary}"
    return f"What changed today: source-backed signal selection was limited. (traceable to {lead.get('source_record_id')})."


def render_edition(
    date: str,
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    primary_row: dict[str, Any] | None,
    editorial_status: str,
    role_counts: dict[str, int],
    scope_counts: dict[str, int],
    previous_context: dict[str, Any],
    primary_signal_status: str,
    continuing_rows: list[dict[str, Any]],
    edition_mode: str = "current_update",
    ) -> str:
    return render_food_line_edition(
        date,
        sources,
        adequacy,
        primary_row,
        editorial_status,
        role_counts,
        scope_counts,
        previous_context,
        primary_signal_status,
        continuing_rows,
        edition_mode=edition_mode,
    )


def render_food_line_edition(
    date: str,
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    primary_row: dict[str, Any] | None,
    editorial_status: str,
    role_counts: dict[str, int],
    scope_counts: dict[str, int],
    previous_context: dict[str, Any],
    primary_signal_status: str,
    continuing_rows: list[dict[str, Any]],
    edition_mode: str = "current_update",
    display_public_rows: list[dict[str, Any]] | None = None,
) -> str:
    pressure_rows = _public_source_rows(sources)
    reviewed_count = len(sources)
    public_signal_rows = [] if edition_mode == "no_current_update" else list(display_public_rows if display_public_rows is not None else pressure_rows)
    excluded_count = max(0, reviewed_count - len(public_signal_rows))
    status_signal_count = 0 if edition_mode == "no_current_update" else len(pressure_rows)
    status_excluded_count = max(0, reviewed_count - status_signal_count)
    publisher_count = len(
        {
            str(row.get("publisher") or row.get("source_name") or "").strip()
            for row in public_signal_rows
            if str(row.get("publisher") or row.get("source_name") or "").strip()
        }
    )
    public_signal_count = len(public_signal_rows)
    status_label = _food_line_public_edition_status_label(edition_mode, reviewed_count, status_signal_count, status_excluded_count)
    eyebrow_label = {
        "no-current-update": _food_line_no_current_update_public_label(),
        "full": "Full-source update",
        "partial": "Partial-source update",
        "limited": "Limited-source update",
    }.get(status_label, "Limited-source update")
    lead_scope_label = _food_line_lead_pressure_scope_label(primary_row) if primary_row else None
    glance_html = _food_line_at_a_glance_items(sources, primary_row, continuing_rows, primary_signal_status)
    today_read_html = _food_line_today_read_html(
        sources,
        date,
        primary_row,
        continuing_rows,
        primary_signal_status,
        previous_context,
        public_signal_count,
        (reviewed_count, excluded_count),
        lead_scope_label,
    )
    summary_html = _food_line_edition_summary_html(reviewed_count, publisher_count, status_label, public_signal_count, excluded_count)
    source_mix_html = _food_line_source_mix_html(sources, public_signal_rows, primary_row, continuing_rows, primary_signal_status)
    source_note_html = _food_line_source_note_html(source_table_url="./source_table.html", claim_ledger_url="./claim_ledger.html")
    edition_nav_html = _food_line_edition_navigation_html(str(previous_context.get("previous_edition_date") or "").strip() or None)
    section_rows = _food_line_public_section_rows(sources, primary_row, continuing_rows, edition_mode=edition_mode)
    core_rows = list(section_rows["core"])
    other_rows = list(section_rows["other"])
    context_rows = list(section_rows.get("context") or [])
    policy_rows = [row for row in section_rows["policy"] if str(row.get("source_record_id") or "").strip() not in {str(item.get("source_record_id") or "").strip() for item in context_rows}]
    provider_rows = list(section_rows["provider"])
    if edition_mode == "no_current_update":
        core_section_html = "<p>No fresh source-backed current food-pressure signal qualified today.</p>"
        other_section_html = "<p>Background records remain in the source audit and public source table only.</p>"
        context_section_html = ""
        policy_section_html = ""
        provider_section_html = ""
    else:
        core_items = "".join(_food_line_public_signal_item_html(row) for row in core_rows if row)
        other_items = "".join(_food_line_public_signal_item_html(row) for row in other_rows if row)
        context_items = "".join(_food_line_public_signal_item_html(row) for row in context_rows if row)
        policy_items = "".join(_food_line_public_signal_item_html(row) for row in policy_rows if row)
        provider_items = "".join(_food_line_public_signal_item_html(row) for row in provider_rows if row)
        core_section_html = f"<div>{core_items}</div>" if core_items else "<p>No qualifying core Food Line signals were published.</p>"
        if other_items:
            other_section_html = f"<div>{other_items}</div>"
        elif policy_items or provider_items:
            other_section_html = "<p>Additional qualifying signals are grouped below by type.</p>"
        else:
            other_section_html = "<p>No additional Food Line signals qualified today.</p>"
        if context_items:
            context_section_html = f"<h2>Research / Context Signals</h2><div>{context_items}</div>"
        else:
            context_section_html = "<h2>Research / Context Signals</h2><p>No research / context signals qualified today.</p>"
        policy_section_html = f"<h2>Policy / Benefits Signals</h2>{f'<div>{policy_items}</div>' if policy_items else '<p>No policy / benefits signals qualified today.</p>'}"
        provider_section_html = f"<h2>Provider / Operations Signals</h2>{f'<div>{provider_items}</div>' if provider_items else '<p>No provider / operations signals qualified today.</p>'}"
    page_footer = footer("../../")
    body = f"""{_food_line_theme_styles()}
{header(DISPATCH_NAME, "../../", "../../archive.html", "/food-line/")}
<main class="container briefing food-line-shell">
  <section class="hero food-line-hero">
    {_food_line_logo_html("food-line-logo--edition", "../../assets/")}
    <p class="eyebrow">{eyebrow_label} / {_human_date(date)}</p>
    <p>Generated from saved source records available for {_human_date(date)}.</p>
    <h1>{DISPATCH_NAME}</h1>
    {summary_html}
  </section>
  <section class="food-line-panel">
    <h2>Today’s Read</h2>
    {today_read_html}
    <h2>At A Glance</h2>
    <ul>{glance_html}</ul>
    <h2>Core Food Pressure Signals</h2>
    {core_section_html}
    <h2>Other Food Line Signals</h2>
    {other_section_html}
    {context_section_html}
    {policy_section_html}
    {provider_section_html}
    <h2>Source Mix</h2>
    {source_mix_html}
    <h2>Source Note</h2>
    {source_note_html}
    {edition_nav_html}
  </section>
</main>
{page_footer}"""
    return _food_line_page(f"{DISPATCH_NAME} - {date}", f"{BASE_URL}/food-line/editions/{date}/", "../../assets/site.css", body)


def _source_table_html(
    date: str,
    sources: list[dict[str, Any]],
    public_rows: list[dict[str, Any]],
    *,
    primary_row: dict[str, Any] | None = None,
    continuing_rows: list[dict[str, Any]] | None = None,
    edition_mode: str = "current_update",
) -> str:
    effective_rows = public_rows or list(sources)
    rows = _public_source_table_rows_html(
        effective_rows,
        primary_row=primary_row,
        continuing_rows=continuing_rows,
        edition_mode=edition_mode,
    )
    page_rows = []
    audit_rows = []
    background_rows = [
        row
        for row in effective_rows
        if _food_line_public_usage_label(row, primary_row, continuing_rows, edition_mode=edition_mode) == "Background reference"
    ]
    for row in effective_rows:
        usage_label = _food_line_public_usage_label(row, primary_row, continuing_rows, edition_mode=edition_mode)
        if _food_line_public_page_usage_visible(usage_label):
            page_rows.append(row)
        elif usage_label.startswith("Source audit"):
            audit_rows.append(row)
    page_source_count = len(page_rows)
    audit_count = len(audit_rows)
    background_count = len(background_rows)
    stale_count = sum(1 for row in sources if str(row.get("source_freshness_status") or row.get("freshness_status") or "") == "stale_outside_daily_window" and not _food_line_source_background_reference(row))
    if audit_count and background_count:
        audit_summary = (
            f"Sources behind this briefing: {page_source_count} sources were used on the public page, with {audit_count} source audit record{'s' if audit_count != 1 else ''} and {background_count} additional background reference source{'s' if background_count != 1 else ''} listed in the source table. "
            f"The run reviewed {len(sources)} records and excluded {max(0, len(sources) - len(effective_rows))} that were duplicate, stale, unrelated, or not strong enough for public use."
        )
    elif audit_count:
        audit_summary = (
            f"Sources behind this briefing: {page_source_count} sources were used on the public page, with {audit_count} source audit record{'s' if audit_count != 1 else ''} listed in the source table. "
            f"The run reviewed {len(sources)} records and excluded {max(0, len(sources) - len(effective_rows))} that were duplicate, stale, unrelated, or not strong enough for public use."
        )
    elif background_count:
        audit_summary = (
            f"Sources behind this briefing: {page_source_count} sources were used on the public page, with {background_count} additional background reference sources listed in the source table. "
            f"The run reviewed {len(sources)} records and excluded {max(0, len(sources) - len(effective_rows))} that were duplicate, stale, unrelated, or not strong enough for public use."
        )
    else:
        audit_summary = (
            f"Sources behind this briefing: {page_source_count} sources were used on the public page. "
            f"The run reviewed {len(sources)} records and excluded {max(0, len(sources) - len(effective_rows))} that were duplicate, stale, unrelated, or not strong enough for public use."
        )
    if stale_count:
        audit_summary += f" {stale_count} stale current-story candidate source{'s' if stale_count != 1 else ''} were excluded by the freshness window."
    exclusion_summary = _food_line_exclusion_reason_summary(
        _food_line_exclusion_reason_counts(
            sources,
            [],
            [],
            [],
            page_rows,
        )
    )
    page_footer = footer("../../")
    body = (
        f"{_food_line_theme_styles()}"
        f"{header(DISPATCH_NAME, '../../', '../../archive.html', '/food-line/')}"
        f"<main class='container food-line-shell'>"
        f"<section class='food-line-panel'>"
        f"<div class='food-line-hero'>"
        f"{_food_line_logo_html('food-line-logo--edition', '../../assets/')}"
        f"<p class='eyebrow'>The Blue Fern Co.</p>"
        f"<h1>Food Line Source Table {date}</h1>"
        f"</div>"
        f"<p>{html.escape(audit_summary)}</p>"
        f"<p>{html.escape(exclusion_summary)}</p>"
        f"<p><a href=\"./claim_ledger.html\">Open the claim ledger</a></p>"
        f"<table class='food-line-source-table'>"
        "<tr>"
        "<th>Record ID</th><th>Title</th><th>Publisher</th><th>Location</th><th>Source link</th><th>Source family</th><th>How it was used</th><th>Issue</th><th>What happened</th><th>What the source says</th><th>Verification status</th><th>Who may be affected</th><th>Used on public page</th><th>source_freshness_status</th><th>source_freshness_date_basis</th><th>source_public_story_eligible</th>"
        "</tr>"
        f"{rows}</table></section></main>{page_footer}"
    )
    return _food_line_page(f"Food Line Source Table {date}", f"{BASE_URL}/food-line/editions/{date}/source_table.html", "../../assets/site.css", body)


def _build_map_data(date: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    markers = []
    pressure_markers = []
    context_records = []
    baseline_records = []
    excluded_records = []
    exclusion_reasons: dict[str, int] = {}
    for s in sources:
        cleaned_excerpt = _public_evidence_excerpt(s)
        row = {
            "source_record_id": s.get("source_record_id"),
            "location_name": s["location_name"],
            "state": s["state"],
            "county_name": s.get("county_name") or "",
            "category": s["map_category"],
            "publisher": s.get("publisher") or "",
            "source_family": s.get("source_family") or "",
            "source_purpose": s.get("source_purpose") or classify_food_line_source_purpose(s).get("source_purpose") or "",
            "source_role": s.get("source_role") or "",
            "pressure_signal": bool(s.get("pressure_signal")),
            "pressure_summary": s.get("pressure_summary") or "",
            "extraction_quality": s.get("extraction_quality") or "",
            "expected_text_basis": s.get("expected_text_basis") or "",
            "pressure_verification_required": bool(s.get("pressure_verification_required")),
            "evidence_text": cleaned_excerpt,
            "evidence_excerpt": cleaned_excerpt,
            "evidence_text_basis": s.get("evidence_text_basis") or "",
            "pressure_match_terms": s.get("pressure_match_terms") or [],
            "pressure_verification_status": s.get("pressure_verification_status") or "",
            "issue_tags": s.get("issue_tags") or [],
            "affected_groups": s.get("affected_groups") or [],
            "pressure_type": s.get("pressure_type") or "context only",
            "evidence_level": s.get("evidence_level") or "background context",
            "freshness_role": s.get("freshness_role") or "stable_context",
            "source_role_allowed": s.get("source_role_allowed") or "",
            "pressure_required": bool(s.get("pressure_required")),
            "pressure_reason": s.get("pressure_reason") or "",
            "source_title": s["title"],
            "source_url": s["url"],
            "note": s.get("pressure_summary") or "",
            "dispatch_date": date,
            "latitude": s.get("latitude"),
            "longitude": s.get("longitude"),
        }
        markers.append(row)
        role = str(s.get("source_role") or "")
        if role == "baseline_condition":
            baseline_records.append(row)
        if role in {"resource_context", "policy_context", "baseline_condition"}:
            context_records.append(row)
        if bool(s.get("pressure_signal")) and bool(s.get("map_eligible")):
            pressure_markers.append(row)
        else:
            reason = "baseline_condition" if role == "baseline_condition" else ("context_only_or_no_pressure_evidence" if not bool(s.get("pressure_signal")) else "not_map_eligible")
            exclusion_reasons[reason] = int(exclusion_reasons.get(reason, 0)) + 1
            excluded_records.append({**row, "reason": reason})
    pressure_count = sum(1 for s in sources if bool(s.get("pressure_signal")))
    return {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": date,
        "markers": markers,
        "pressure_markers": pressure_markers,
        "context_records": context_records,
        "baseline_records": baseline_records,
        "excluded_records": excluded_records,
        "diagnostics": {
            "total_source_count": len(sources),
            "pressure_signal_count": pressure_count,
            "pressure_marker_count": len(pressure_markers),
            "baseline_record_count": len(baseline_records),
            "context_record_count": len(context_records),
            "excluded_context_count": len(context_records),
            "excluded_record_count": len(excluded_records),
            "unmapped_pressure_count": max(0, pressure_count - len(pressure_markers)),
            "exclusion_reasons": exclusion_reasons,
            "pressure_verified_count": sum(1 for s in sources if str(s.get("pressure_verification_status") or "") == "source_text_verified"),
            "pressure_demoted_unverified_count": sum(1 for s in sources if str(s.get("pressure_verification_status") or "") == "demoted_context"),
            "pressure_registry_only_count": sum(1 for s in sources if str(s.get("pressure_verification_status") or "") == "registry_summary_only"),
            "pressure_evidence_basis_counts": dict(sorted(Counter(str(s.get("evidence_text_basis") or "insufficient_evidence") for s in sources).items())),
            "collected_count_by_extraction_quality": dict(sorted(Counter(str(s.get("extraction_quality") or "unknown") for s in sources).items())),
            "verified_pressure_count_by_extraction_quality": dict(sorted(Counter(str(s.get("extraction_quality") or "unknown") for s in sources if str(s.get("pressure_verification_status") or "") == "source_text_verified").items())),
            "demoted_count_by_extraction_quality": dict(sorted(Counter(str(s.get("extraction_quality") or "unknown") for s in sources if str(s.get("pressure_verification_status") or "") == "demoted_context").items())),
        },
    }


EXCLUSION_BUCKET_ORDER = (
    "stale",
    "duplicate",
    "unrelated",
    "outside product geography",
    "resource-only / no pressure signal",
    "weak pressure signal",
    "missing usable date",
    "missing usable location",
    "insufficient source traceability",
    "background/context only",
    "other",
)


def _empty_food_line_exclusion_counts() -> dict[str, int]:
    return {bucket: 0 for bucket in EXCLUSION_BUCKET_ORDER}


def _increment_food_line_exclusion_count(counts: dict[str, int], bucket: str) -> None:
    counts[bucket] = int(counts.get(bucket, 0)) + 1


def _food_line_exclusion_bucket_for_row(row: dict[str, Any]) -> str:
    freshness_status = str(row.get("source_freshness_status") or row.get("freshness_status") or "").strip()
    pressure_reason = str(row.get("pressure_reason") or row.get("rejection_reason") or "").strip().lower()
    verification_status = str(row.get("pressure_verification_status") or "").strip().lower()
    source_role = str(row.get("source_role") or "").strip().lower()
    source_purpose = str(row.get("source_purpose") or "").strip().lower()
    location_name = str(row.get("location_name") or "").strip()
    state = str(row.get("state") or "").strip()

    if freshness_status == "stale_outside_daily_window":
        return "stale"
    if freshness_status in {"missing_source_published_date", "unparsed_source_published_date", "url_path_only"}:
        return "missing usable date"
    if pressure_reason == "outside product geography" or str(row.get("location_scope") or "").strip() == "outside_product_geography":
        return "outside product geography"
    if not location_name and not state:
        return "missing usable location"
    if source_purpose in {"resource_page", "program_description", "donation_page"}:
        return "resource-only / no pressure signal"
    if source_role in {"background_context", "policy_context", "baseline_condition"}:
        return "background/context only"
    if source_role == "resource_context":
        return "resource-only / no pressure signal"
    if "negative filter" in pressure_reason or "not current pressure evidence" in pressure_reason:
        return "unrelated"
    if verification_status in {"registry_summary_only", "insufficient_evidence"} or "insufficient specific pressure evidence" in pressure_reason:
        return "weak pressure signal"
    if not bool(row.get("pressure_signal")):
        return "background/context only"
    return "other"


def _food_line_exclusion_reason_counts(
    sources: list[dict[str, Any]],
    rejected_records: list[dict[str, Any]],
    source_diagnostics: list[str],
    rejected_news_reasons: list[str],
    current_public_rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts = _empty_food_line_exclusion_counts()
    current_public_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in current_public_rows
        if str(row.get("source_record_id") or "").strip()
    }
    for row in sources:
        row_id = str(row.get("source_record_id") or "").strip()
        if row_id and row_id in current_public_ids:
            continue
        _increment_food_line_exclusion_count(counts, _food_line_exclusion_bucket_for_row(row))
    for reason in source_diagnostics:
        lowered = str(reason or "").lower()
        if "duplicate" in lowered:
            _increment_food_line_exclusion_count(counts, "duplicate")
        else:
            _increment_food_line_exclusion_count(counts, "other")
    for reason in rejected_news_reasons:
        lowered = str(reason or "").lower()
        if "duplicate" in lowered:
            _increment_food_line_exclusion_count(counts, "duplicate")
        elif "negative filter" in lowered or "unrelated" in lowered:
            _increment_food_line_exclusion_count(counts, "unrelated")
        else:
            _increment_food_line_exclusion_count(counts, "other")
    for record in rejected_records:
        reasons = [str(item or "").lower() for item in (record.get("reasons") or [])]
        if any("published_at" in reason for reason in reasons):
            _increment_food_line_exclusion_count(counts, "missing usable date")
        elif any("location_name" in reason or "state" in reason for reason in reasons):
            _increment_food_line_exclusion_count(counts, "missing usable location")
        else:
            _increment_food_line_exclusion_count(counts, "insufficient source traceability")
    return counts


def _food_line_exclusion_reason_summary(counts: dict[str, int]) -> str:
    display_labels = {
        "stale": "stale",
        "duplicate": "duplicate",
        "unrelated": "unrelated",
        "outside product geography": "outside product geography",
        "resource-only / no pressure signal": "resource-only or no pressure signal",
        "weak pressure signal": "weak pressure signal",
        "missing usable date": "missing usable date",
        "missing usable location": "missing usable location",
        "insufficient source traceability": "insufficient source traceability",
        "background/context only": "background or context material",
        "other": "other",
    }
    parts = [
        f"{display_labels.get(bucket, bucket)} {counts[bucket]}"
        for bucket in EXCLUSION_BUCKET_ORDER
        if int(counts.get(bucket, 0)) > 0
    ]
    if not parts:
        return "Exclusion breakdown: none."
    return "Exclusion breakdown: " + "; ".join(parts) + "."


def _write_food_line_review_csv(root: Path, date: str, sources: list[dict[str, Any]]) -> Path:
    review_path = root / "output" / "review" / DISPATCH_SLUG / date / "pressure_review.csv"
    fieldnames = [
        "source_record_id",
        "pressure_signal",
        "pressure_verification_status",
        "pressure_type",
        "source_published_date",
        "source_freshness_status",
        "source_freshness_date_basis",
        "source_public_story_eligible",
        "collected_date",
        "freshness_status",
        "freshness_disqualification_reason",
        "primary_eligible",
        "primary_disqualification_reason",
        "affected_groups",
        "location_name",
        "state",
        "pressure_summary",
        "evidence_text",
        "pressure_match_terms",
        "source_title",
        "source_url",
        "primary_source_url",
        "secondary_source_url",
        "source_traceability_role",
        "source_family",
        "source_id",
    ]
    rows = []
    for s in sources:
        rows.append(
            {
                "source_record_id": s.get("source_record_id") or "",
                "pressure_signal": str(bool(s.get("pressure_signal"))).lower(),
                "pressure_verification_status": s.get("pressure_verification_status") or "",
                "pressure_type": s.get("pressure_type") or "",
                "source_published_date": s.get("source_published_date") or "",
                "source_freshness_status": s.get("source_freshness_status") or "",
                "source_freshness_date_basis": s.get("source_freshness_date_basis") or "",
                "source_public_story_eligible": str(bool(s.get("source_public_story_eligible"))).lower() if "source_public_story_eligible" in s else "",
                "collected_date": s.get("collected_date") or "",
                "freshness_status": s.get("freshness_status") or "",
                "freshness_disqualification_reason": s.get("freshness_disqualification_reason") or "",
                "primary_eligible": str(bool(s.get("primary_eligible"))).lower() if "primary_eligible" in s else "",
                "primary_disqualification_reason": s.get("primary_disqualification_reason") or "",
                "affected_groups": ", ".join(str(item).strip() for item in (s.get("affected_groups") or []) if str(item).strip()),
                "location_name": s.get("location_name") or "",
                "state": s.get("state") or "",
                "pressure_summary": s.get("pressure_summary") or "",
                "evidence_text": s.get("evidence_text") or "",
                "pressure_match_terms": ", ".join(str(term) for term in (s.get("pressure_match_terms") or [])),
                "source_title": s.get("title") or "",
                "source_url": s.get("url") or "",
                "primary_source_url": s.get("primary_source_url") or "",
                "secondary_source_url": s.get("secondary_source_url") or "",
                "source_traceability_role": s.get("source_traceability_role") or "",
                "source_family": s.get("source_family") or "",
                "source_id": s.get("source_id") or "",
            }
        )
    _write_csv(review_path, fieldnames, rows)
    return review_path


def _write_food_line_candidate_review_artifacts(root: Path, date: str, sources: list[dict[str, Any]], classification_summary: dict[str, Any]) -> tuple[Path, Path]:
    review_dir = root / "output" / "review" / DISPATCH_SLUG / date
    json_path = review_dir / "candidate_review.json"
    html_path = review_dir / "candidate_review.html"
    blocker_counts = dict(classification_summary.get("public_claim_blocker_counts") or {})
    payload = {
        "generated_at": utc_now(),
        "edition_date": date,
        "candidate_count_total": len(sources),
        "candidate_count_traceable": sum(1 for row in sources if str(row.get("traceability_status") or "") == "traceable"),
        "candidate_count_approved": sum(1 for row in sources if str(row.get("review_status") or "") == "approved"),
        "candidate_count_needs_review": sum(1 for row in sources if str(row.get("review_status") or "") == "needs_review"),
        "candidate_count_watchlist": sum(1 for row in sources if str(row.get("review_status") or "") == "watchlist"),
        "candidate_count_rejected": sum(1 for row in sources if str(row.get("review_status") or "") == "rejected"),
        "public_claim_eligible_count": int(classification_summary.get("public_claim_eligible_count") or 0),
        "public_claim_blocker_counts": blocker_counts,
        "candidates": [
            {
                "title": str(row.get("title") or ""),
                "publisher": str(row.get("publisher") or ""),
                "date": str(row.get("source_published_date") or row.get("published_at") or ""),
                "url": str(row.get("url") or ""),
                "pressure_type": str(row.get("pressure_signal_type") or ""),
                "pressure_strength": str(row.get("pressure_signal_strength") or ""),
                "review_status": str(row.get("review_status") or ""),
                "public_claim_eligible": bool(row.get("public_claim_eligible")),
                "public_claim_blockers": list(row.get("public_claim_blockers") or []),
                "review_note": str(row.get("review_note") or ""),
            }
            for row in sources
        ],
    }
    _write_json(json_path, payload)
    summary_items = [
        ("Total candidates found", payload["candidate_count_total"]),
        ("Approved", payload["candidate_count_approved"]),
        ("Needs review", payload["candidate_count_needs_review"]),
        ("Watchlist", payload["candidate_count_watchlist"]),
        ("Rejected", payload["candidate_count_rejected"]),
        ("Public-claim eligible", payload["public_claim_eligible_count"]),
    ]
    blocker_list = "".join(
        f"<li>{html.escape(reason)}: {count}</li>"
        for reason, count in sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))
    ) or "<li>none</li>"
    rows_html = "".join(
        (
            "<tr>"
            f"<td>{html.escape(str(row['title']))}</td>"
            f"<td>{html.escape(str(row['publisher']))}</td>"
            f"<td>{html.escape(str(row['date']))}</td>"
            f"<td><a href=\"{html.escape(str(row['url']))}\">source</a></td>"
            f"<td>{html.escape(str(row['pressure_type']))}</td>"
            f"<td>{html.escape(str(row['pressure_strength']))}</td>"
            f"<td>{html.escape(str(row['review_status']))}</td>"
            f"<td>{'true' if row['public_claim_eligible'] else 'false'}</td>"
            f"<td>{html.escape(', '.join(str(item) for item in row['public_claim_blockers']))}</td>"
            f"<td>{html.escape(str(row['review_note']))}</td>"
            "</tr>"
        )
        for row in payload["candidates"]
    )
    summary_html = "".join(f"<li>{html.escape(label)}: {value}</li>" for label, value in summary_items)
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Food Line candidate review</title>"
        "<style>body{font-family:Georgia,serif;margin:2rem;color:#1f2a30}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #c8c8c8;padding:.5rem;vertical-align:top;text-align:left}th{background:#f4efe7}"
        "ul{margin-top:.25rem}</style></head><body>"
        f"<h1>Food Line candidate review - {html.escape(date)}</h1>"
        f"<h2>Summary</h2><ul>{summary_html}</ul>"
        f"<h2>Blocker counts</h2><ul>{blocker_list}</ul>"
        "<h2>Candidates</h2><table><thead><tr>"
        "<th>Title</th><th>Publisher</th><th>Date</th><th>URL</th><th>Pressure type</th><th>Strength</th>"
        "<th>Review status</th><th>Public-claim eligible</th><th>Blockers</th><th>Review note</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table></body></html>"
    )
    _write_text(html_path, document)
    return json_path, html_path


def _read_food_line_review_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_food_line_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _read_json(path)
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _read_food_line_source_discovery_review_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_food_line_discovery_intake_review(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _food_line_review_candidate_rows(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"candidate review file must be an object: {path}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"candidate review file must contain a 'candidates' list: {path}")
    rows = [row for row in candidates if isinstance(row, dict)]
    if len(rows) != len(candidates):
        raise ValueError(f"candidate review file contains non-object candidate rows: {path}")
    return rows


def _food_line_review_candidate_source_family(row: dict[str, Any]) -> str:
    explicit = str(row.get("source_family") or row.get("candidate_source_family") or "").strip().lower()
    if explicit:
        return explicit
    source_role = str(row.get("source_role") or "").strip().lower()
    publisher = str(row.get("publisher") or "").strip().lower()
    if source_role == "policy_analysis":
        return "policy_research"
    if source_role == "institutional_report":
        return "policy_research"
    if source_role == "local_news_report":
        return "local_news"
    if source_role == "public_radio_report":
        return "public_radio"
    if source_role == "food_bank_update":
        return "food_bank_provider"
    if source_role == "resource_context":
        return "food_bank_provider"
    if "radio" in publisher or "npr" in publisher or "pbs" in publisher:
        return "public_radio"
    if any(token in publisher for token in ("news", "times", "tribune", "post", "herald", "journal", "flyer", "wkrn", "wpde", "abc", "nbc", "cbs", "fox")):
        return "local_news"
    return "policy_research"


def _food_line_review_candidate_map_category(row: dict[str, Any]) -> str:
    explicit = str(row.get("map_category") or "").strip()
    if explicit:
        return explicit
    pressure_type = str(row.get("pressure_type") or "").strip().lower()
    if "snap" in pressure_type or "benefit" in pressure_type:
        return "benefit disruption"
    if "school meal" in pressure_type or "child" in pressure_type:
        return "summer meal / child nutrition"
    if "food bank" in pressure_type or "emergency food" in pressure_type:
        return "acute strain / service disruption"
    if "afford" in pressure_type or "demand" in pressure_type:
        return "elevated demand"
    return "context / monitoring only"


def _food_line_review_candidate_row(
    row: dict[str, Any],
    *,
    edition_date: str,
    index: int,
) -> dict[str, Any]:
    source_url = str(row.get("source_url") or row.get("url") or row.get("original_source_url") or "").strip()
    title = str(row.get("selected_title") or row.get("title") or "").strip()
    publisher = str(row.get("publisher") or "").strip()
    source_role = str(row.get("source_role") or "resource_context").strip()
    location_scope = str(row.get("location_scope") or "national").strip()
    source_published_date = str(row.get("source_published_date") or row.get("date") or edition_date).strip()
    pressure_summary = str(row.get("pressure_summary") or "").strip()
    pressure_type = str(row.get("pressure_type") or "").strip()
    pressure_signal_hint = str(row.get("pressure_signal_hint") or "").strip()
    affected_groups = [str(item).strip() for item in list(row.get("affected_groups") or []) if str(item).strip()]
    evidence_level = str(row.get("evidence_level") or "").strip() or "background context"
    freshness_role = str(row.get("freshness_role") or "").strip() or "fresh_daily_signal"
    public_claim_eligible = bool(row.get("public_claim_eligible"))
    blockers = [str(item).strip() for item in list(row.get("public_claim_blockers") or []) if str(item).strip()]
    pressure_signal = bool(public_claim_eligible or pressure_summary or pressure_type or pressure_signal_hint)
    summary_text = pressure_summary or pressure_signal_hint or title
    traceability_status = str(row.get("traceability_status") or "").strip() or ("traceable" if source_url else "missing_url")
    return {
        "source_record_id": str(row.get("source_record_id") or f"food-line-review-{edition_date}-{index:03d}"),
        "title": title,
        "publisher": publisher,
        "url": source_url,
        "primary_source_url": source_url,
        "source_traceability_role": "article_url" if source_url else "",
        "source_family": _food_line_review_candidate_source_family(row),
        "location_scope": location_scope,
        "location_name": str(row.get("location_name") or row.get("state_hint") or ("United States" if location_scope == "national" else "")).strip(),
        "state": str(row.get("state") or row.get("state_hint") or "").strip(),
        "map_category": _food_line_review_candidate_map_category(row),
        "summary_or_snippet": summary_text,
        "evidence_text": summary_text,
        "evidence_text_basis": "candidate_review",
        "source_type": "review_candidate",
        "source_purpose": "review_candidate",
        "source_published_date": source_published_date,
        "published_at": source_published_date,
        "retrieved_at": str(row.get("generated_at") or row.get("retrieved_at") or "").strip(),
        "pressure_signal": pressure_signal,
        "pressure_type": pressure_type,
        "pressure_summary": pressure_summary,
        "pressure_signal_hint": pressure_signal_hint,
        "pressure_verification_status": "source_text_verified" if pressure_summary or pressure_type else "review_candidate_only",
        "affected_groups": affected_groups,
        "evidence_level": evidence_level,
        "freshness_role": freshness_role,
        "freshness_status": freshness_role,
        "source_freshness_status": freshness_role,
        "source_freshness_date_basis": str(row.get("date_basis") or "source_published_date"),
        "source_public_story_eligible": public_claim_eligible,
        "primary_eligible": public_claim_eligible,
        "primary_disqualification_reason": "" if public_claim_eligible else ", ".join(blockers[:3]),
        "public_claim_eligible": public_claim_eligible,
        "public_claim_blockers": blockers,
        "review_status": str(row.get("candidate_review_status") or row.get("review_status") or "").strip(),
        "traceability_status": traceability_status,
        "source_role": source_role,
        "supported_product_geography": True,
        "map_eligible": public_claim_eligible,
        "classification_status": str(row.get("classification_status") or "").strip(),
        "claim_supported": pressure_summary,
        "limitations": str(row.get("limitations") or "").strip(),
    }


def _food_line_review_candidate_source_url(row: dict[str, Any]) -> str:
    return str(row.get("source_url") or row.get("url") or row.get("original_source_url") or "").strip()


def _normalize_food_line_review_selector_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    normalized = _normalize_food_line_source_collection_url(raw)
    return normalized or raw


def _select_food_line_review_candidate_rows(
    candidate_rows: list[dict[str, Any]],
    *,
    review_path: Path,
    public_eligible_only: bool,
    source_url: str | None,
) -> dict[str, Any]:
    public_eligible_count = sum(1 for row in candidate_rows if bool(row.get("public_claim_eligible")))
    if public_eligible_count <= 0:
        raise ValueError(f"candidate review file contains zero public-eligible candidates: {review_path}")

    pre_selector_rows = [row for row in candidate_rows if bool(row.get("public_claim_eligible"))] if public_eligible_only else list(candidate_rows)
    if not pre_selector_rows:
        raise ValueError(f"candidate review selection is empty after filters: {review_path}")

    selector_type = ""
    selector_value = ""
    selector_match_count = 0
    selector_deduplicated = False
    selected_source_url = ""
    selected_rows = list(pre_selector_rows)

    if source_url is not None:
        selector_type = "source_url"
        selector_value = str(source_url).strip()
        normalized_selector = _normalize_food_line_review_selector_url(selector_value)
        if not normalized_selector:
            raise ValueError("review-only selector source URL is empty")

        raw_matches = [
            row for row in candidate_rows
            if _normalize_food_line_review_selector_url(_food_line_review_candidate_source_url(row)) == normalized_selector
        ]
        eligible_matches = [
            row for row in pre_selector_rows
            if _normalize_food_line_review_selector_url(_food_line_review_candidate_source_url(row)) == normalized_selector
        ]
        if not raw_matches:
            raise ValueError(f"review-only selector matched zero candidates for source URL: {selector_value}")
        if public_eligible_only and not eligible_matches:
            raise ValueError(
                f"review-only selector matched zero public-eligible candidates for source URL: {selector_value}"
            )
        selector_match_count = len(eligible_matches if public_eligible_only else raw_matches)
        selected_rows = eligible_matches if public_eligible_only else raw_matches

        if len(selected_rows) > 1:
            candidate_payloads = {
                json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for row in selected_rows
            }
            matched_urls = {
                _normalize_food_line_review_selector_url(_food_line_review_candidate_source_url(row)) for row in selected_rows
            }
            if len(candidate_payloads) != 1 or len(matched_urls) != 1:
                raise ValueError(f"review-only selector is ambiguous for source URL: {selector_value}")
            selector_deduplicated = True
            selected_rows = [selected_rows[0]]

        selected_source_url = _food_line_review_candidate_source_url(selected_rows[0])

    if not selected_rows:
        raise ValueError(f"candidate review selection is empty after filters: {review_path}")

    return {
        "selected_rows": selected_rows,
        "public_eligible_count": public_eligible_count,
        "selector_type": selector_type,
        "selector_value": selector_value,
        "selector_match_count": selector_match_count,
        "selector_deduplicated": selector_deduplicated,
        "selected_source_url": selected_source_url,
        "pre_selector_candidate_count": len(pre_selector_rows),
    }


def render_food_line_review_only(
    root: Path,
    *,
    date: str,
    candidate_review_path: Path,
    public_eligible_only: bool = False,
    source_url: str | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    edition_date = validate_date(date)
    review_path = candidate_review_path.resolve()
    if not review_path.exists():
        raise ValueError(f"candidate review file not found: {review_path}")
    candidate_rows = _food_line_review_candidate_rows(review_path)
    if not candidate_rows:
        raise ValueError(f"candidate review file contains no candidate rows: {review_path}")
    selection = _select_food_line_review_candidate_rows(
        candidate_rows,
        review_path=review_path,
        public_eligible_only=bool(public_eligible_only),
        source_url=source_url,
    )
    public_eligible_count = int(selection["public_eligible_count"])
    selected_rows = list(selection["selected_rows"])

    sources = [
        _food_line_review_candidate_row(row, edition_date=edition_date, index=index)
        for index, row in enumerate(selected_rows, start=1)
    ]
    previous_context: dict[str, Any] = {}
    adequacy = source_adequacy(sources)
    lead_row, continuing_rows, _why_lead, primary_signal_status = _select_primary_pressure_signal(sources, edition_date, previous_context)
    if not lead_row:
        raise ValueError(f"review-only render requires at least one primary-eligible source row: {review_path}")
    role_counts = _role_counts(sources)
    scope_counts = _scope_counts(sources)
    editorial_status = _editorial_status(sources)
    edition_mode = "current_update"
    rendered_story_rows = _food_line_public_story_rows(
        sources,
        lead_row,
        continuing_rows,
        edition_mode=edition_mode,
    )
    rendered_root = (output_root or (root / "output" / "site-review-only" / DISPATCH_SLUG)).resolve()
    edition_dir = rendered_root / "editions" / edition_date
    warnings: list[str] = []
    wrote: list[str] = []
    _food_line_assets_to_output_root(root, rendered_root, warnings, wrote)
    html_page = render_food_line_edition(
        edition_date,
        sources,
        adequacy,
        lead_row,
        editorial_status,
        role_counts,
        scope_counts,
        previous_context,
        primary_signal_status,
        continuing_rows,
        edition_mode=edition_mode,
        display_public_rows=rendered_story_rows,
    )
    public_rows = list(rendered_story_rows)
    source_table_html = _source_table_html(
        edition_date,
        sources,
        public_rows,
        primary_row=lead_row,
        continuing_rows=continuing_rows,
        edition_mode=edition_mode,
    )
    claim_ledger_html = _food_line_claim_ledger_html(
        edition_date,
        sources,
        lead_row,
        continuing_rows,
        edition_mode=edition_mode,
        review_counts=(len(sources), max(0, len(sources) - len(rendered_story_rows))),
        exclusion_reason_counts={},
    )
    rendered_claim_rows = _food_line_claim_ledger_rows(
        sources,
        lead_row,
        continuing_rows,
        edition_mode=edition_mode,
    )
    _write_text(edition_dir / "index.html", html_page)
    _write_text(edition_dir / "source_table.html", source_table_html)
    _write_text(edition_dir / "claim_ledger.html", claim_ledger_html)
    wrote.extend(
        [
            str(edition_dir / "index.html"),
            str(edition_dir / "source_table.html"),
            str(edition_dir / "claim_ledger.html"),
        ]
    )
    manifest = {
        "ok": True,
        "render_mode": "review_only",
        "edition_date": edition_date,
        "candidate_review_path": str(review_path),
        "output_root": str(rendered_root),
        "edition_dir": str(edition_dir),
        "public_eligible_only": bool(public_eligible_only),
        "candidate_count_total": len(candidate_rows),
        "pre_selector_candidate_count": int(selection["pre_selector_candidate_count"]),
        "selected_candidate_count": len(selected_rows),
        "source_count": len(sources),
        "public_eligible_candidate_count": public_eligible_count,
        "public_eligible_candidate_count_before_selector": public_eligible_count,
        "rendered_public_claim_count": len(rendered_claim_rows),
        "selector_type": str(selection["selector_type"]),
        "selector_value": str(selection["selector_value"]),
        "selector_match_count": int(selection["selector_match_count"]),
        "selector_deduplicated": bool(selection["selector_deduplicated"]),
        "selected_source_url": str(selection["selected_source_url"]),
        "source_urls": [str(row.get("url") or "") for row in sources if str(row.get("url") or "").strip()],
        "lead_source_record_id": str(lead_row.get("source_record_id") or ""),
        "lead_title": str(lead_row.get("title") or ""),
        "lead_source_url": str(lead_row.get("url") or ""),
        "lead_source_role": str(lead_row.get("source_role") or ""),
        "lead_pressure_type": str(lead_row.get("pressure_type") or ""),
        "lead_source_published_date": str(lead_row.get("source_published_date") or ""),
        "production_output_mutated": False,
        "pages_repo_mutated": False,
        "source_table_path": str(edition_dir / "source_table.html"),
        "claim_ledger_path": str(edition_dir / "claim_ledger.html"),
        "index_path": str(edition_dir / "index.html"),
        "source_table_exists": True,
        "claim_ledger_exists": True,
        "warnings": warnings,
    }
    _write_json(edition_dir / "review_render_manifest.json", manifest)
    wrote.append(str(edition_dir / "review_render_manifest.json"))
    manifest["written_paths"] = wrote
    return manifest


_FOOD_LINE_SOURCE_COLLECTION_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "spm",
}
_AP_NEWS_ARTICLE_ID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.IGNORECASE)


def _normalize_food_line_source_collection_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return canonical_url(raw)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key
        and key.lower() not in _FOOD_LINE_SOURCE_COLLECTION_TRACKING_PARAMS
        and not key.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/")
    normalized = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )
    return canonical_url(normalized)


def _food_line_source_collection_url_aliases(url: str) -> set[str]:
    normalized = _normalize_food_line_source_collection_url(url)
    if not normalized:
        return set()
    aliases = {normalized}
    parts = urlsplit(normalized)
    host = parts.netloc.lower()
    if host.endswith("apnews.com"):
        article_id_match = _AP_NEWS_ARTICLE_ID_RE.search(parts.path or "")
        if article_id_match:
            article_id = article_id_match.group(1).lower()
            aliases.add(canonical_url(urlunsplit((parts.scheme, parts.netloc, f"/article/{article_id}", "", ""))))
    return aliases


def _food_line_ap_menu_false_positive_reason(candidate: dict[str, Any] | None, reason_text: str) -> str:
    if str(reason_text or "").strip().lower() != "excluded by negative filter: menu":
        return ""
    candidate = candidate or {}
    combined = normalize_title(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("source_name") or ""),
                str(candidate.get("url") or ""),
            )
            if str(part).strip()
        )
    )
    if not combined:
        return ""
    strong_terms = ("food bank", "food banks", "snap", "hunger", "pantry", "funding cuts", "families", "rising costs")
    navigation_terms = ("restaurant", "recipe", "cooking", "chef", "brunch", "dinner", "lunch")
    strong_hits = sum(1 for term in strong_terms if term in combined)
    navigation_hits = sum(1 for term in navigation_terms if term in combined)
    url = str(candidate.get("url") or "").strip().lower()
    article_like = "/article/" in url or bool(re.search(r"/\d{4}/\d{2}/\d{2}/", url))
    if article_like and strong_hits >= 3 and navigation_hits == 0:
        return "collector artifact reflects a pre-fix menu false positive on an article-like food-pressure candidate"
    return ""


def _food_line_source_collection_gold_set_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "source_collection_gold_sets" / f"{date}.json"


def _food_line_source_collection_audit_paths(root: Path, date: str) -> tuple[Path, Path]:
    review_dir = root / "output" / "review" / DISPATCH_SLUG / date
    return review_dir / "source_collection_audit.json", review_dir / "source_collection_audit.md"


def _food_line_discovery_candidates_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "discovery" / date / "discovery_candidates.json"


def _food_line_source_discovery_review_path(root: Path, date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / date / "source_discovery_review.csv"


def _food_line_source_discovery_audit_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / date / "source_discovery_audit.json"


def _food_line_discovery_intake_review_path(root: Path, date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / date / "discovery_intake.json"


def _food_line_can_reuse_collect_artifacts(root: Path, date: str) -> bool:
    return _auto_source_path(root, date).exists() and _collector_audit_path(root, date).exists()


def _food_line_reused_collect_result(root: Path, date: str) -> dict[str, Any]:
    auto_rows = _read_food_line_json_list(_auto_source_path(root, date))
    audit_rows = _read_food_line_json_list(_collector_audit_path(root, date))
    rejected_news_reasons: list[str] = []
    rejected_news_by_source: dict[str, int] = {}
    collected_source_count_by_source_id: dict[str, int] = {}
    pressure_evidence_basis_counts: Counter[str] = Counter()
    fetch_failure_count_by_source_id: dict[str, int] = {}
    fetch_failure_count_by_type: Counter[str] = Counter()
    fetch_failure_type_by_source_id: dict[str, str] = {}
    fetch_failure_action_by_source_id: dict[str, str] = {}
    no_evidence_count_by_source_id: dict[str, int] = {}
    verified_pressure_count = 0
    pressure_demoted_unverified_count = 0
    rejected_news_count = 0
    pressure_verified_count_by_extraction_quality: Counter[str] = Counter()
    demoted_count_by_extraction_quality: Counter[str] = Counter()
    collected_count_by_extraction_quality: Counter[str] = Counter()
    extraction_quality_by_source_id: dict[str, str] = {}

    for row in auto_rows:
        source_id = str(row.get("source_id") or "").strip()
        extraction_quality = str(row.get("extraction_quality") or "unknown").strip() or "unknown"
        if source_id and source_id not in extraction_quality_by_source_id:
            extraction_quality_by_source_id[source_id] = extraction_quality
        collected_count_by_extraction_quality[extraction_quality] += 1
        evidence_basis = str(row.get("evidence_text_basis") or "insufficient_evidence").strip() or "insufficient_evidence"
        pressure_evidence_basis_counts[evidence_basis] += 1
        verification_status = str(row.get("pressure_verification_status") or "").strip()
        if verification_status == "source_text_verified":
            verified_pressure_count += 1
            pressure_verified_count_by_extraction_quality[extraction_quality] += 1
        elif verification_status == "demoted_context":
            pressure_demoted_unverified_count += 1
            demoted_count_by_extraction_quality[extraction_quality] += 1

    for row in audit_rows:
        source_id = str(row.get("source_id") or "").strip()
        item_count = int(row.get("item_count") or 0)
        collected_source_count_by_source_id[source_id] = item_count
        rejected_count = int(row.get("rejected_count") or 0)
        rejected_news_count += rejected_count
        accepted_pressure_count = int(row.get("accepted_pressure_count") or 0)
        verified_pressure_count = max(verified_pressure_count, 0)
        quality = extraction_quality_by_source_id.get(source_id, "unknown")
        if accepted_pressure_count:
            pressure_verified_count_by_extraction_quality[quality] += 0
        top_reasons = [str(item).strip() for item in (row.get("top_rejection_reasons") or []) if str(item).strip()]
        if top_reasons:
            rejected_news_reasons.extend(top_reasons)
            rejected_news_by_source[source_id] = len(top_reasons)
        if not bool(row.get("fetched")):
            fetch_failure_count_by_source_id[source_id] = max(1, len(top_reasons))
            failure_type = str(row.get("fetch_failure_type") or "").strip()
            failure_action = str(row.get("fetch_failure_action") or "").strip()
            if failure_type:
                fetch_failure_count_by_type[failure_type] += 1
                fetch_failure_type_by_source_id[source_id] = failure_type
            if failure_action:
                fetch_failure_action_by_source_id[source_id] = failure_action
            if not item_count:
                no_evidence_count_by_source_id[source_id] = 1
        for basis in row.get("extraction_basis_used") or []:
            basis_text = str(basis).strip()
            if basis_text:
                pressure_evidence_basis_counts[basis_text] += 0

    return {
        "ok": True,
        "source_count": len(auto_rows),
        "collector_audit_path": str(_collector_audit_path(root, date)),
        "reused_existing_artifacts": True,
        "rejected_news_count": rejected_news_count,
        "rejected_news_reasons": rejected_news_reasons,
        "rejected_news_by_source": rejected_news_by_source,
        "collected_source_count_by_source_id": collected_source_count_by_source_id,
        "pressure_verified_count": verified_pressure_count,
        "pressure_demoted_unverified_count": pressure_demoted_unverified_count,
        "pressure_registry_only_count": 0,
        "pressure_evidence_basis_counts": dict(sorted(pressure_evidence_basis_counts.items())),
        "collected_count_by_extraction_quality": dict(sorted(collected_count_by_extraction_quality.items())),
        "verified_pressure_count_by_extraction_quality": dict(sorted(pressure_verified_count_by_extraction_quality.items())),
        "demoted_count_by_extraction_quality": dict(sorted(demoted_count_by_extraction_quality.items())),
        "fetch_failure_count_by_source_id": fetch_failure_count_by_source_id,
        "fetch_failure_count_by_type": dict(sorted(fetch_failure_count_by_type.items())),
        "fetch_failure_type_by_source_id": dict(sorted(fetch_failure_type_by_source_id.items())),
        "fetch_failure_action_by_source_id": dict(sorted(fetch_failure_action_by_source_id.items())),
        "no_evidence_count_by_source_id": no_evidence_count_by_source_id,
        "failed_sources": [
            {"source_id": source_id, "reason": "reused collector artifact recorded a fetch failure"}
            for source_id in sorted(fetch_failure_count_by_source_id)
        ],
        "rejected_news": [],
    }


def _load_food_line_source_collection_gold_set(path: Path, date: str) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"gold set must be a list: {path}")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"gold set row {index} must be an object: {path}")
        normalized_url = _normalize_food_line_source_collection_url(str(row.get("url") or ""))
        if not normalized_url:
            raise ValueError(f"gold set row {index} is missing a valid url: {path}")
        rows.append(
            {
                "date": str(row.get("date") or date).strip() or date,
                "query": str(row.get("query") or "").strip(),
                "url": str(row.get("url") or "").strip(),
                "normalized_url": normalized_url,
                "title": str(row.get("title") or "").strip(),
                "expected_status": str(row.get("expected_status") or "").strip(),
                "expected_reason": str(row.get("expected_reason") or "").strip(),
                "priority": str(row.get("priority") or "medium").strip().lower() or "medium",
                "source_family": str(row.get("source_family") or "").strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
        )
    return rows


def _food_line_source_collection_rejection_reason(
    *reason_sources: Any,
) -> tuple[str, str]:
    reason_text_parts: list[str] = []
    seen_reason_keys: set[str] = set()

    def _append_reason(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = normalize_title(text)
        if not key or key in seen_reason_keys:
            return
        seen_reason_keys.add(key)
        reason_text_parts.append(text)

    def _collect(value: Any) -> None:
        if isinstance(value, dict):
            for key in (
                "reason",
                "exclusion_reason",
                "public_inclusion_reason",
                "primary_disqualification_reason",
                "freshness_disqualification_reason",
                "source_freshness_disqualification_reason",
                "pressure_reason",
            ):
                _append_reason(value.get(key))
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _collect(item)
            return
        _append_reason(value)

    for source in reason_sources:
        _collect(source)

    for source in reason_sources:
        if isinstance(source, dict):
            corrected_reason = _food_line_ap_menu_false_positive_reason(
                source,
                str(source.get("reason") or ((source.get("top_rejection_reasons") or [""])[0] if isinstance(source.get("top_rejection_reasons"), list) else "")),
            )
            if corrected_reason:
                reason_text_parts = [reason for reason in reason_text_parts if normalize_title(reason) != normalize_title("excluded by negative filter: menu")]
                _append_reason(corrected_reason)

    reason_text = "; ".join(reason_text_parts)
    lowered = reason_text.lower()
    merged_row: dict[str, Any] = {}
    for source in reason_sources:
        if isinstance(source, dict):
            merged_row.update(source)
    pressure_verification_status = str(merged_row.get("pressure_verification_status") or "").strip().lower()
    source_purpose = str(merged_row.get("source_purpose") or "").strip().lower()
    source_role = str(merged_row.get("source_role") or "").strip().lower()
    classification_status = str(merged_row.get("classification_status") or "").strip().lower()
    if not reason_text:
        if pressure_verification_status == "demoted_context" or source_purpose in {
            "donation_page",
            "evergreen_context",
            "resource_page",
            "program_description",
        } or source_role == "resource_context" or classification_status == "context_only":
            return "resource-only / no pressure signal", "rejected_resource_only"
        return "", "unknown"
    if "stale" in lowered or "outside daily window" in lowered:
        return reason_text, "rejected_stale"
    if "resource-only" in lowered or "resource only" in lowered or "no pressure signal" in lowered:
        return reason_text, "rejected_resource_only"
    if "background reference" in lowered:
        return "resource-only / no pressure signal", "rejected_resource_only"
    if "weak pressure" in lowered or "not a current public food-pressure signal" in lowered:
        return reason_text, "rejected_weak_pressure"
    if "published_at" in lowered or "missing usable date" in lowered or "missing required field: published_at" in lowered:
        return reason_text, "rejected_missing_date"
    if "traceability" in lowered or "missing source url" in lowered or "missing required field: url" in lowered:
        return reason_text, "rejected_insufficient_traceability"
    if "fetch blocked" in lowered or "blocked fetch" in lowered or "forbidden" in lowered or "timeout" in lowered:
        return reason_text, "fetch_failed"
    return reason_text, "unknown"


def _food_line_source_collection_stage_rank(stage: str) -> int:
    order = {
        "not_discovered": 0,
        "discovered_raw_candidate": 1,
        "fetched_or_attempted": 2,
        "parsed_or_source_record_created": 3,
        "written_to_auto_sources_or_manual_sources": 4,
        "appears_in_pressure_review": 5,
        "rejected_with_reason": 6,
        "qualified_public_candidate": 7,
    }
    return order.get(stage, -1)


def _food_line_source_collection_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"# Food Line source-collection audit - {report.get('edition_date')}",
        "",
        f"- Gold candidates: {summary.get('gold_count', 0)}",
        f"- Found by collector: {summary.get('found_count', 0)}",
        f"- Reached pressure review: {summary.get('reached_review_count', 0)}",
        f"- Qualified public candidates: {summary.get('qualified_count', 0)}",
        f"- Rejected with reason: {summary.get('rejected_with_reason_count', 0)}",
        f"- Missed entirely: {summary.get('missed_count', 0)}",
        f"- High-priority missed: {summary.get('high_priority_missed_count', 0)}",
        f"- Recall: {summary.get('recall', 0.0):.3f}",
        f"- High-priority recall: {summary.get('high_priority_recall', 0.0):.3f}",
        f"- Likely failure category: {summary.get('likely_failure_category', 'no_major_gap_detected')}",
        "",
        "| Priority | Found | Stage | Miss / reject reason | Title | URL |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.get("items") or []:
        reason_cell = str(item.get("miss_reason") or "")
        if str(item.get("highest_stage_reached") or "") == "rejected_with_reason" and str(item.get("rejection_reason") or "").strip():
            reason_cell = str(item.get("rejection_reason") or "")
        elif not reason_cell:
            reason_cell = str(item.get("rejection_reason") or "")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("priority") or ""),
                    "yes" if item.get("found") else "no",
                    str(item.get("highest_stage_reached") or ""),
                    reason_cell,
                    str(item.get("matched_title") or item.get("title") or "").replace("|", "\\|"),
                    str(item.get("url") or "").replace("|", "%7C"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def run_food_line_source_collection_audit(
    root: Path,
    date: str,
    *,
    gold_set_path: Path,
    sources: list[dict[str, Any]],
    rejected_records: list[dict[str, Any]],
    pressure_review_path: Path,
    collect_result: dict[str, Any] | None,
) -> dict[str, Any]:
    gold_rows = _load_food_line_source_collection_gold_set(gold_set_path, date)
    auto_rows = _read_json(_auto_source_path(root, date)) if _auto_source_path(root, date).exists() else []
    manual_rows = _read_json(_manual_source_path(root, date)) if _manual_source_path(root, date).exists() else []
    review_rows = _read_food_line_review_csv(pressure_review_path)
    collector_audit_rows = _read_food_line_json_list(_collector_audit_path(root, date))
    discovery_candidate_rows = _read_food_line_json_list(_food_line_discovery_candidates_path(root, date))
    discovery_review_rows = _read_food_line_source_discovery_review_csv(_food_line_source_discovery_review_path(root, date))
    discovery_audit_rows = _read_food_line_json_list(_food_line_source_discovery_audit_path(root, date))
    discovery_intake_review = _read_food_line_discovery_intake_review(_food_line_discovery_intake_review_path(root, date))
    discovery_intake_rows = [
        row for row in (discovery_intake_review.get("discovery_source_rows") or []) if isinstance(row, dict)
    ]
    rejected_news = list((collect_result or {}).get("rejected_news") or [])
    json_path, markdown_path = _food_line_source_collection_audit_paths(root, date)

    source_rows = list(sources or [])
    combined_written_rows = []
    manual_row_list = manual_rows if isinstance(manual_rows, list) else []
    auto_row_list = auto_rows if isinstance(auto_rows, list) else []
    for row in [*manual_row_list, *auto_row_list, *source_rows]:
        if isinstance(row, dict):
            combined_written_rows.append(row)

    high_priority_total = sum(1 for row in gold_rows if row["priority"] == "high")
    items: list[dict[str, Any]] = []

    for gold in gold_rows:
        normalized_url = gold["normalized_url"]
        gold_aliases = _food_line_source_collection_url_aliases(gold["url"])
        gold_title = str(gold.get("title") or "").strip()
        gold_host = urlsplit(normalized_url).netloc.lower() if normalized_url else ""
        best_fuzzy: dict[str, Any] | None = None
        best_fuzzy_score = 0.0

        def _is_exact_match(candidate_url: str) -> bool:
            return bool(candidate_url) and bool(gold_aliases & _food_line_source_collection_url_aliases(candidate_url))

        def _consider_fuzzy(candidate: dict[str, Any], candidate_title: str, candidate_url: str) -> None:
            nonlocal best_fuzzy, best_fuzzy_score
            if not gold_title or not candidate_title:
                return
            candidate_host = urlsplit(_normalize_food_line_source_collection_url(candidate_url)).netloc.lower()
            if not candidate_host or candidate_host != gold_host:
                return
            score = SequenceMatcher(None, normalize_title(gold_title), normalize_title(candidate_title)).ratio()
            if score >= 0.72 and score > best_fuzzy_score:
                best_fuzzy = candidate
                best_fuzzy_score = score

        exact_source = next((row for row in source_rows if _is_exact_match(str(row.get("url") or row.get("primary_source_url") or ""))), None)
        if exact_source is None:
            for row in source_rows:
                _consider_fuzzy(row, str(row.get("title") or ""), str(row.get("url") or row.get("primary_source_url") or ""))
            exact_source = best_fuzzy
        fuzzy_match = exact_source is best_fuzzy and best_fuzzy is not None

        exact_written = next(
            (row for row in combined_written_rows if _is_exact_match(str(row.get("url") or row.get("primary_source_url") or ""))),
            None,
        )
        if exact_written is None and exact_source is not None:
            exact_written = exact_source

        exact_review = next(
            (row for row in review_rows if _is_exact_match(str(row.get("source_url") or row.get("primary_source_url") or ""))),
            None,
        )
        if exact_review is None and exact_source is not None:
            exact_review = next(
                (
                    row
                    for row in review_rows
                    if str(row.get("source_record_id") or "").strip()
                    and str(row.get("source_record_id") or "").strip() == str(exact_source.get("source_record_id") or "").strip()
                ),
                None,
            )

        exact_rejected_news = next((row for row in rejected_news if _is_exact_match(str(row.get("url") or ""))), None)
        exact_rejected_record = next(
            (
                row
                for row in rejected_records
                if _is_exact_match(str(row.get("url") or "")) or _is_exact_match(str(row.get("primary_source_url") or ""))
            ),
            None,
        )
        exact_collector_audit = next((row for row in collector_audit_rows if _is_exact_match(str(row.get("url") or ""))), None)
        exact_discovery_candidate = next(
            (
                row
                for row in discovery_candidate_rows
                if _is_exact_match(str(row.get("final_trace_url") or row.get("canonical_url") or row.get("discovered_url") or row.get("google_news_url") or ""))
            ),
            None,
        )
        exact_discovery_review = next(
            (row for row in discovery_review_rows if _is_exact_match(str(row.get("candidate_url") or ""))),
            None,
        )
        exact_discovery_audit = next(
            (row for row in discovery_audit_rows if _is_exact_match(str(row.get("candidate_url") or ""))),
            None,
        )
        exact_discovery_intake = next(
            (
                row
                for row in discovery_intake_rows
                if _is_exact_match(
                    str(
                        row.get("url")
                        or row.get("final_trace_url")
                        or row.get("canonical_url")
                        or row.get("discovered_url")
                        or row.get("google_news_url")
                        or ""
                    )
                )
            ),
            None,
        )

        found = any(
            (
                exact_source,
                exact_written,
                exact_review,
                exact_rejected_news,
                exact_rejected_record,
                exact_collector_audit,
                exact_discovery_candidate,
                exact_discovery_review,
                exact_discovery_audit,
                exact_discovery_intake,
            )
        )
        stage = "not_discovered"
        matched_artifact = ""
        matched_row = None

        if exact_rejected_news is not None:
            stage = "discovered_raw_candidate"
            matched_artifact = "collector_rejected_news"
            matched_row = exact_rejected_news
        elif exact_collector_audit is not None:
            stage = "fetched_or_attempted"
            matched_artifact = "collector_audit"
            matched_row = exact_collector_audit
        if exact_source is not None:
            stage = "parsed_or_source_record_created"
            matched_artifact = "merged_sources"
            matched_row = exact_source
        if exact_discovery_candidate is not None:
            fetch_status = str(exact_discovery_candidate.get("fetch_status") or "").strip().lower()
            if fetch_status and fetch_status not in {"ok", "manual_fallback"}:
                if _food_line_source_collection_stage_rank("fetched_or_attempted") > _food_line_source_collection_stage_rank(stage):
                    stage = "fetched_or_attempted"
                    matched_artifact = "discovery_candidates"
                    matched_row = exact_discovery_candidate
            elif _food_line_source_collection_stage_rank("parsed_or_source_record_created") > _food_line_source_collection_stage_rank(stage):
                stage = "parsed_or_source_record_created"
                matched_artifact = "discovery_candidates"
                matched_row = exact_discovery_candidate
        if exact_written is not None and _food_line_source_collection_stage_rank("written_to_auto_sources_or_manual_sources") > _food_line_source_collection_stage_rank(stage):
            stage = "written_to_auto_sources_or_manual_sources"
            matched_artifact = "manual_or_auto_sources"
            matched_row = exact_written
        if exact_discovery_review is not None and _food_line_source_collection_stage_rank("written_to_auto_sources_or_manual_sources") > _food_line_source_collection_stage_rank(stage):
            stage = "written_to_auto_sources_or_manual_sources"
            matched_artifact = "source_discovery_review"
            matched_row = exact_discovery_review
        if exact_discovery_intake is not None and _food_line_source_collection_stage_rank("written_to_auto_sources_or_manual_sources") > _food_line_source_collection_stage_rank(stage):
            stage = "written_to_auto_sources_or_manual_sources"
            matched_artifact = "discovery_intake_review"
            matched_row = exact_discovery_intake
        if exact_review is not None and _food_line_source_collection_stage_rank("appears_in_pressure_review") > _food_line_source_collection_stage_rank(stage):
            stage = "appears_in_pressure_review"
            matched_artifact = "pressure_review"
            matched_row = exact_review

        rejection_text, rejection_miss_reason = _food_line_source_collection_rejection_reason(
            exact_review,
            exact_source,
            exact_written if isinstance(exact_written, dict) else None,
            exact_collector_audit,
            exact_discovery_candidate,
            exact_discovery_audit,
            exact_discovery_review,
            exact_discovery_intake,
            (exact_rejected_news or {}).get("reason"),
            (exact_rejected_record or {}).get("reasons") or [],
            (exact_collector_audit or {}).get("top_rejection_reasons") or [],
        )

        qualifies = (
            bool((exact_source or {}).get("qualifies_for_public_inclusion"))
            or (
                isinstance(exact_review, dict)
                and str(exact_review.get("primary_eligible") or "").strip().lower() == "true"
                and str(exact_review.get("source_public_story_eligible") or "").strip().lower() == "true"
            )
            or str((exact_discovery_candidate or {}).get("classification_status") or "").strip() in {"qualified_pressure_signal", "manual_fallback"}
            or str((exact_discovery_intake or {}).get("classification_status") or "").strip() in {"qualified_pressure_signal", "manual_fallback"}
        )
        if not qualifies and str((exact_discovery_review or {}).get("action") or "").strip() == "rejected_discovery":
            stage = "rejected_with_reason"
            matched_artifact = "source_discovery_review"
            matched_row = exact_discovery_review
        elif (
            not qualifies
            and exact_discovery_candidate is not None
            and str((exact_discovery_candidate or {}).get("classification_status") or "").strip() in {"context_only", "duplicate"}
            and str((exact_discovery_candidate or {}).get("exclusion_reason") or "").strip()
        ):
            stage = "rejected_with_reason"
            matched_artifact = "discovery_candidates"
            matched_row = exact_discovery_candidate
        if qualifies:
            stage = "qualified_public_candidate"
            matched_artifact = matched_artifact or "pressure_review"
        elif (
            exact_collector_audit is not None
            and int((exact_collector_audit or {}).get("rejected_count") or 0) > 0
            and rejection_text
            and _food_line_source_collection_stage_rank("rejected_with_reason") > _food_line_source_collection_stage_rank(stage)
        ):
            stage = "rejected_with_reason"
            matched_artifact = "collector_audit"
            matched_row = exact_collector_audit
        elif found and rejection_text and _food_line_source_collection_stage_rank("rejected_with_reason") > _food_line_source_collection_stage_rank(stage):
            stage = "rejected_with_reason"
            matched_artifact = matched_artifact or ("collector_rejected_news" if exact_rejected_news else "pressure_review")

        miss_reason = ""
        if stage == "not_discovered":
            miss_reason = "not_discovered"
        elif stage == "fetched_or_attempted":
            miss_reason = "fetch_failed"
        elif stage == "parsed_or_source_record_created":
            miss_reason = "not_written_to_sources"
        elif stage == "written_to_auto_sources_or_manual_sources":
            miss_reason = "not_reached_review"
        elif stage == "rejected_with_reason":
            miss_reason = rejection_miss_reason

        if stage == "appears_in_pressure_review" and not qualifies:
            miss_reason = "not_reached_review"

        item = {
            "url": gold["url"],
            "normalized_url": normalized_url,
            "title": gold["title"],
            "query": gold["query"],
            "priority": gold["priority"],
            "source_family": gold["source_family"],
            "expected_status": gold["expected_status"],
            "expected_reason": gold["expected_reason"],
            "found": found,
            "highest_stage_reached": stage,
            "matched_artifact": matched_artifact,
            "matched_source_record_id": str((matched_row or {}).get("source_record_id") or (matched_row or {}).get("candidate_id") or (matched_row or {}).get("source_id") or ""),
            "matched_title": str((matched_row or {}).get("title") or (matched_row or {}).get("source_title") or (matched_row or {}).get("discovered_title") or (matched_row or {}).get("source_name") or ""),
            "fuzzy_match": fuzzy_match,
            "pressure_signal": (
                bool((exact_source or {}).get("pressure_signal"))
                if exact_source is not None
                else (
                    str((exact_discovery_candidate or {}).get("classification_status") or "").strip() == "qualified_pressure_signal"
                    if exact_discovery_candidate is not None
                    else (
                        str((exact_discovery_intake or {}).get("classification_status") or "").strip() in {"qualified_pressure_signal", "manual_fallback"}
                        if exact_discovery_intake is not None
                        else None
                    )
                )
            ),
            "freshness_status": str(
                (exact_source or {}).get("source_freshness_status")
                or (exact_review or {}).get("source_freshness_status")
                or (exact_discovery_intake or {}).get("fetch_status")
                or (exact_discovery_candidate or {}).get("fetch_status")
                or ""
            ),
            "source_public_story_eligible": (
                bool((exact_source or {}).get("source_public_story_eligible"))
                if exact_source is not None and "source_public_story_eligible" in exact_source
                else (
                    str((exact_review or {}).get("source_public_story_eligible") or "").strip().lower() == "true"
                    if exact_review is not None
                    else (
                        str((exact_discovery_intake or {}).get("classification_status") or "").strip() in {"qualified_pressure_signal", "manual_fallback"}
                        if exact_discovery_intake is not None
                        else (
                            str((exact_discovery_candidate or {}).get("classification_status") or "").strip() in {"qualified_pressure_signal", "manual_fallback"}
                            if exact_discovery_candidate is not None
                            else None
                        )
                    )
                )
            ),
            "rejection_reason": rejection_text,
            "miss_reason": miss_reason,
        }
        items.append(item)

    found_count = sum(1 for item in items if item["found"])
    reached_review_count = sum(
        1
        for item in items
        if item["matched_artifact"] in {"pressure_review", "source_discovery_review", "discovery_intake_review"}
        or _food_line_source_collection_stage_rank(str(item["highest_stage_reached"])) >= _food_line_source_collection_stage_rank("appears_in_pressure_review")
    )
    qualified_count = sum(1 for item in items if item["highest_stage_reached"] == "qualified_public_candidate")
    rejected_with_reason_count = sum(1 for item in items if item["highest_stage_reached"] == "rejected_with_reason")
    missed_count = sum(1 for item in items if item["highest_stage_reached"] == "not_discovered")
    high_priority_missed_count = sum(1 for item in items if item["priority"] == "high" and item["highest_stage_reached"] == "not_discovered")

    likely_failure_category = "no_major_gap_detected"
    if high_priority_missed_count > 0 or missed_count > 0:
        likely_failure_category = "discovery_query_gap"
    elif any(item["miss_reason"] == "fetch_failed" for item in items):
        likely_failure_category = "fetch_parse_gap"
    elif any(item["miss_reason"] in {"rejected_resource_only", "rejected_weak_pressure"} for item in items):
        likely_failure_category = "classifier_gap"
    elif any(item["miss_reason"] == "rejected_stale" for item in items):
        likely_failure_category = "freshness_gate"
    elif any(item["miss_reason"] == "rejected_insufficient_traceability" for item in items):
        likely_failure_category = "traceability_gap"

    summary = {
        "gold_count": len(items),
        "found_count": found_count,
        "reached_review_count": reached_review_count,
        "qualified_count": qualified_count,
        "rejected_with_reason_count": rejected_with_reason_count,
        "missed_count": missed_count,
        "high_priority_missed_count": high_priority_missed_count,
        "recall": found_count / len(items) if items else 0.0,
        "high_priority_recall": (
            (high_priority_total - high_priority_missed_count) / high_priority_total if high_priority_total else 1.0
        ),
        "likely_failure_category": likely_failure_category,
    }
    report = {
        "edition_date": date,
        "generated_at": utc_now(),
        "gold_set_path": str(gold_set_path),
        "summary": summary,
        "items": items,
    }
    _write_json(json_path, report)
    _write_text(markdown_path, _food_line_source_collection_markdown(report))
    return {
        "source_collection_audit_run": True,
        "source_collection_gold_count": summary["gold_count"],
        "source_collection_found_count": summary["found_count"],
        "source_collection_reached_review_count": summary["reached_review_count"],
        "source_collection_qualified_count": summary["qualified_count"],
        "source_collection_rejected_with_reason_count": summary["rejected_with_reason_count"],
        "source_collection_missed_count": summary["missed_count"],
        "source_collection_high_priority_missed_count": summary["high_priority_missed_count"],
        "source_collection_recall": summary["recall"],
        "source_collection_high_priority_recall": summary["high_priority_recall"],
        "source_collection_likely_failure_category": summary["likely_failure_category"],
        "source_collection_audit_path": str(json_path),
        "source_collection_audit_markdown_path": str(markdown_path),
    }


def _resolve_marker_coordinates(marker: dict[str, Any]) -> tuple[float, float, str] | None:
    lat_raw = marker.get("latitude")
    lon_raw = marker.get("longitude")
    if lat_raw is not None and lon_raw is not None:
        try:
            return float(lat_raw), float(lon_raw), "exact/source-provided"
        except (TypeError, ValueError):
            return None
    location_key = str(marker.get("location_name") or "").strip().lower()
    if location_key in CITY_CENTROIDS:
        lat, lon = CITY_CENTROIDS[location_key]
        return lat, lon, "city fallback"
    county_name = str(marker.get("county_name") or "").strip().lower()
    if county_name and county_name in COUNTY_CENTROIDS:
        lat, lon = COUNTY_CENTROIDS[county_name]
        return lat, lon, "county fallback"
    state = str(marker.get("state") or "").strip().upper()
    if state == "US":
        return US_NATIONAL_CENTER[0], US_NATIONAL_CENTER[1], "national centroid"
    if state in US_STATE_CENTROIDS:
        lat, lon = US_STATE_CENTROIDS[state]
        return lat, lon, "state centroid"
    return None


def _render_map_index(date: str, map_data: dict[str, Any]) -> str:
    markers = list(map_data.get("pressure_markers") or [])
    plotted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for marker in markers:
        coords = _resolve_marker_coordinates(marker)
        if not coords:
            skipped.append(
                {
                    "source_title": marker.get("source_title"),
                    "location_name": marker.get("location_name"),
                    "state": marker.get("state"),
                    "reason": "missing_coordinates_and_no_supported_state_fallback",
                }
            )
            continue
        lat, lon, basis = coords
        row = dict(marker)
        row["latitude"] = lat
        row["longitude"] = lon
        row["coordinate_basis"] = basis
        plotted.append(row)
    map_data.setdefault("diagnostics", {})
    map_data["diagnostics"]["marker_count"] = len(markers)
    map_data["diagnostics"]["plotted_marker_count"] = len(plotted)
    map_data["diagnostics"]["skipped_marker_count"] = len(skipped)
    map_data["diagnostics"]["skipped_markers"] = skipped
    map_data["mapped_markers"] = plotted
    legend_items = "".join(
        f'<li><span class="food-line-dot" style="background:{html.escape(color)};"></span>{html.escape(cat)}</li>'
        for cat, color in FOOD_LINE_CATEGORY_COLORS.items()
    )
    category_colors_json = json.dumps(FOOD_LINE_CATEGORY_COLORS)
    plotted_json = json.dumps(plotted)
    latest_edition_url = f"/food-line/editions/{date}/"
    page_footer = footer("../")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Food Line Pressure Map</title>
  <link rel="stylesheet" href="../assets/site.css">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  {_food_line_theme_styles()}
</head>
<body>
{header(DISPATCH_NAME, "../", "../archive.html", "/food-line/")}
<main class="home food-line-shell">
  <section class="food-line-hero">
    {_food_line_logo_html("food-line-logo--map", "../assets/")}
    <p class="eyebrow">The Blue Fern Co.</p>
    <h1>Food Line Pressure Map</h1>
    <p>Latest dispatch date: {html.escape(date)}</p>
    <p><a href="{latest_edition_url}">Open latest Food Line edition</a></p>
  </section>
  <section class="food-line-map-shell">
    <div id="foodLineMap" class="food-line-map" data-rendered-marker-count="{len(plotted)}" data-skipped-marker-count="{len(skipped)}"></div>
    <div class="food-line-map-panel">
      <h2>Legend</h2>
      <ul class="fl-legend">{legend_items}</ul>
      <p><strong>Plotted markers:</strong> {len(plotted)} | <strong>Skipped markers:</strong> {len(skipped)}</p>
      <p>Locations are source-backed pressure signals, not a complete census of food insecurity.</p>
    </div>
</section>
</main>
{page_footer}
<script>
const CATEGORY_COLORS = {category_colors_json};
const FALLBACK_MARKERS = {plotted_json};
const MAP_DATA_URL = "map_data.json";
function esc(value) {{
  return String(value || "").replace(/[&<>"]/g, function(c) {{ return ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c] || c; }});
}}
const map = L.map("foodLineMap").setView([39.8283, -98.5795], 4);
L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
  maxZoom: 18,
  attribution: "&copy; OpenStreetMap contributors"
}}).addTo(map);
const layers = [];
function draw(rows) {{
rows.forEach((item) => {{
  const color = CATEGORY_COLORS[item.category] || "#61717c";
  const icon = L.divIcon({{
    className: "",
    html: `<div style="width:14px;height:14px;border-radius:50%;background:${{color}};border:1px solid #1b2f39;"></div>`,
    iconSize: [14,14],
    iconAnchor: [7,7]
  }});
  const popup = `<div class="food-line-map-popup">
    <div><strong>Location:</strong> ${{esc(item.location_name)}} (${{esc(item.state)}})</div>
    <div><strong>Included in briefing:</strong> ${{esc(String(Boolean(item.pressure_signal)))}}</div>
    <div><strong>What happened:</strong> ${{esc(item.pressure_summary)}}</div>
    <div><strong>Record ID:</strong> ${{esc(item.source_record_id)}}</div>
    <div><strong>Publisher:</strong> ${{esc(item.publisher)}}</div>
    <div><strong>Source family:</strong> ${{esc(item.source_family)}}</div>
    <div><strong>How it was used:</strong> ${{esc(item.source_purpose || "")}}</div>
    ${{(item.evidence_excerpt || item.evidence_text) ? `<div><strong>What the source says:</strong> ${{esc(item.evidence_excerpt || item.evidence_text || "")}}</div>` : ""}}
    <div><strong>Issue:</strong> ${{esc(item.pressure_type)}}</div>
    <div><strong>Evidence level:</strong> ${{esc(item.evidence_level)}}</div>
    <div><strong>Freshness role:</strong> ${{esc(item.freshness_role)}}</div>
    ${{(Array.isArray(item.affected_groups) && item.affected_groups.length) ? `<div><strong>Who may be affected:</strong> ${{esc(item.affected_groups.join(", "))}}</div>` : ""}}
    <div><strong>Source:</strong> ${{esc(item.source_title)}}</div>
    <div><strong>Title:</strong> ${{esc(item.source_title)}}</div>
    <div><strong>Source URL:</strong> <a href="${{esc(item.source_url)}}" target="_blank" rel="noopener noreferrer">${{esc(item.source_url)}}</a></div>
    <div><strong>Verification status:</strong> ${{esc(item.pressure_verification_status)}}</div>
    <div><strong>Dispatch date:</strong> ${{esc(item.dispatch_date)}}</div>
    <div><strong>Coordinate basis:</strong> ${{esc(item.coordinate_basis)}}</div>
  </div>`;
  const marker = L.marker([Number(item.latitude), Number(item.longitude)], {{icon}}).addTo(map).bindPopup(popup);
  layers.push(marker);
}});
if (layers.length) {{
  map.fitBounds(L.featureGroup(layers).getBounds(), {{padding:[24,24], maxZoom:7}});
}}
}}
fetch(MAP_DATA_URL)
  .then((resp) => resp.ok ? resp.json() : null)
  .then((payload) => {{
    const mapped = payload && Array.isArray(payload.mapped_markers) ? payload.mapped_markers : FALLBACK_MARKERS;
    draw(mapped);
  }})
  .catch(() => draw(FALLBACK_MARKERS));
</script>
</body>
</html>"""


def _podcast_description(
    lead: dict[str, Any] | None,
    sources: list[dict[str, Any]],
    editorial_status: str,
    *,
    public_rows: list[dict[str, Any]] | None = None,
    continuing_rows: list[dict[str, Any]] | None = None,
    edition_mode: str = "current_update",
) -> str:
    if edition_mode == "no_current_update":
        pressure_point = "No qualifying Food Line update was published because no fresh source-backed current-story records qualified for public release."
        why_it_matters = "Stale sources remain in the source audit, but they are not presented as current stories."
        source_note = "Background and source links are available in the public source table."
        mode_text = "This edition is a public no-qualifying-update fallback."
    else:
        pressure_point = _food_line_audio_index_teaser(lead, continuing_rows)
        why_it_matters = _food_line_audio_why_it_matters(lead)
        source_note = "Background and source links are available in the public source table."
        if editorial_status == "monitoring/context":
            mode_text = "This edition is monitoring and context only."
        elif editorial_status == "sparse":
            mode_text = "This edition is limited because the source set is sparse."
        else:
            mode_text = ""
    parts = [pressure_point, why_it_matters, source_note]
    if mode_text:
        parts.append(mode_text)
    return " ".join(part for part in parts if part)


def write_food_line_audio(
    root: Path,
    date: str,
    sources: list[dict[str, Any]],
    adequacy: dict[str, Any],
    lead: dict[str, Any] | None,
    editorial_status: str,
    previous_context: dict[str, Any] | None = None,
    continuing_rows: list[dict[str, Any]] | None = None,
    *,
    generate_audio: bool = False,
    require_audio: bool = False,
    force_audio_regenerate: bool = False,
    tts_provider: str = "none",
    audio_model: str = "gpt-4o-mini-tts",
    audio_voice: str = "alloy",
    audio_format: str = "mp3",
    audio_timeout_seconds: float = 90.0,
    max_edition_date: str | None = None,
    edition_mode: str = "current_update",
) -> dict[str, Any]:
    audio_root = root / "output" / "site" / "food-line" / "audio"
    previous_context = previous_context or {}
    continuing_rows = list(continuing_rows or [])
    lead_scope_label = _food_line_lead_pressure_scope_label(lead)
    public_rows = _food_line_public_rendered_rows(sources, lead, continuing_rows)
    sections = _food_line_audio_story_sections(
        date,
        sources,
        adequacy,
        lead,
        editorial_status,
        "new_primary" if lead else ("continuing_only" if continuing_rows else "none"),
        continuing_rows,
        previous_context,
        edition_mode=edition_mode,
    )
    script = _audio_script(
        date,
        sources,
        adequacy,
        lead,
        editorial_status,
        "new_primary" if lead else ("continuing_only" if continuing_rows else "none"),
        continuing_rows,
        previous_context,
        edition_mode=edition_mode,
    )
    episode_title = _audio_episode_title(date)
    description = _podcast_description(lead, sources, editorial_status, public_rows=public_rows, continuing_rows=continuing_rows, edition_mode=edition_mode)
    chosen_provider = "none"
    if generate_audio:
        chosen_provider = str(tts_provider or "openai").strip().lower() or "openai"
        if chosen_provider == "none":
            chosen_provider = "openai"
    audio_filename = f"{date}.{audio_format}"
    if generate_audio and force_audio_regenerate:
        preferred_audio_paths = [audio_root / f"{date}-v2.{audio_format}", audio_root / f"{date}.{audio_format}"]
        selected_existing_audio_path = next((path for path in preferred_audio_paths if path.exists()), None)
        audio_filename = selected_existing_audio_path.name if selected_existing_audio_path else f"{date}.{audio_format}"
    else:
        preferred_audio_paths = [audio_root / f"{date}-v2.{audio_format}", audio_root / f"{date}.{audio_format}"]
        selected_existing_audio_path = next((path for path in preferred_audio_paths if path.exists()), None)
        audio_filename = selected_existing_audio_path.name if selected_existing_audio_path else f"{date}.{audio_format}"
    audio_path = audio_root / audio_filename
    audio_temp_path = audio_root / f"{date}.tmp.{audio_format}"
    existing_audio_mp3_size = audio_path.stat().st_size if audio_path.exists() else None
    existing_audio_mp3_path = str(audio_path) if audio_path.exists() else None
    audio_available = bool(existing_audio_mp3_size and existing_audio_mp3_size > 0)
    audio_reused_existing = bool(audio_available)
    audio_replacement_performed = False
    audio_generated = False
    audio_status = "transcript_only"
    audio_mp3_path: str | None = existing_audio_mp3_path
    audio_mp3_url: str | None = f"/food-line/audio/{audio_filename}" if existing_audio_mp3_path else None
    warnings: list[str] = []
    errors: list[str] = []
    generation_attempted = False
    tts_diagnostics: dict[str, Any] = {
        "provider": chosen_provider if generate_audio else "none",
        "model_requested": audio_model if generate_audio else None,
        "voice_requested": audio_voice if generate_audio else None,
        "narration_char_count": len(script),
        "output_path_attempted": str(audio_temp_path if generate_audio and (force_audio_regenerate or not audio_available) else audio_path),
        "api_key_present": bool(str(os.getenv("OPENAI_API_KEY", "")).strip()),
        "output_dir_exists": audio_root.exists(),
        "partial_mp3_exists": audio_temp_path.exists() or audio_path.exists(),
        "elapsed_seconds": 0.0,
        "exception_type": None,
        "exception_message_sanitized": None,
        "timeout_seconds": audio_timeout_seconds,
        "audio_format": audio_format,
    }
    if generate_audio:
        if audio_available and not force_audio_regenerate:
            audio_status = "audio_file_reused_existing"
            audio_mp3_path = existing_audio_mp3_path
            audio_mp3_url = f"/food-line/audio/{audio_filename}"
            audio_generated = False
            audio_reused_existing = True
        else:
            generation_attempted = True
            tts_result, tts_diag = synthesize_speech_with_diagnostics(
                text=script,
                provider=chosen_provider,
                model=audio_model,
                voice=audio_voice,
                audio_format=audio_format,
                timeout=audio_timeout_seconds,
                output_path=audio_temp_path,
            )
            tts_diagnostics.update(vars(tts_diag))
            if tts_result.ok and tts_result.audio_bytes:
                try:
                    audio_root.mkdir(parents=True, exist_ok=True)
                    audio_temp_path.write_bytes(tts_result.audio_bytes)
                    temp_size = audio_temp_path.stat().st_size
                    if temp_size <= 0:
                        raise IOError("temporary audio file was empty")
                    audio_temp_path.replace(audio_path)
                    audio_generated = True
                    audio_available = True
                    audio_reused_existing = False
                    audio_replacement_performed = bool(existing_audio_mp3_path)
                    audio_status = "audio_file_ready"
                    audio_mp3_path = str(audio_path)
                    audio_mp3_url = f"/food-line/audio/{audio_filename}"
                except Exception as exc:  # noqa: BLE001
                    if audio_temp_path.exists():
                        try:
                            audio_temp_path.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                    tts_diagnostics["file_write_exception_type"] = exc.__class__.__name__
                    tts_diagnostics["file_write_exception_message_sanitized"] = re.sub(r"\s+", " ", str(exc)).strip()[:500]
                    warning = f"Food Line audio narration file write failed: {exc.__class__.__name__}"
                    if existing_audio_mp3_path:
                        audio_available = True
                        audio_reused_existing = True
                        audio_mp3_path = existing_audio_mp3_path
                        audio_mp3_url = f"/food-line/audio/{audio_filename}"
                        audio_status = "audio_file_reused_existing_after_failed_regeneration"
                        warnings.append("Food Line audio narration regeneration failed; existing MP3 was preserved.")
                    elif require_audio:
                        audio_status = "audio_file_write_failed"
                        warnings.append(warning)
                        errors.append(warning)
                    else:
                        audio_status = "audio_file_write_failed"
                        warnings.append(warning)
            else:
                failure_reason = tts_result.error_reason or "audio_generation_failed"
                warning = f"Food Line audio narration was not generated: {failure_reason}"
                if existing_audio_mp3_path:
                    audio_available = True
                    audio_reused_existing = True
                    audio_mp3_path = existing_audio_mp3_path
                    audio_mp3_url = f"/food-line/audio/{audio_filename}"
                    audio_status = "audio_file_reused_existing_after_failed_regeneration"
                    warnings.append("Food Line audio narration regeneration failed; existing MP3 was preserved.")
                elif require_audio:
                    audio_status = failure_reason
                    warnings.append(warning)
                    errors.append(warning)
                else:
                    audio_status = failure_reason
                    warnings.append(warning)
    elif require_audio:
        warning = "--require-audio requires --generate-audio"
        warnings.append(warning)
        errors.append(warning)
    elif existing_audio_mp3_path:
        audio_available = True
        audio_reused_existing = True
        audio_mp3_path = existing_audio_mp3_path
        audio_mp3_url = f"/food-line/audio/{audio_filename}"
        audio_status = "audio_file_reused_existing"
    metadata = {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": date,
        "episode_title": episode_title,
        "script_text": script,
        "episode_summary": description,
        "audio_generated": audio_generated,
        "audio_available": audio_available,
        "audio_reused_existing": audio_reused_existing,
        "audio_required": bool(require_audio),
        "force_audio_regenerate": bool(force_audio_regenerate),
        "audio_status": audio_status,
        "audio_mp3_path": audio_mp3_path,
        "audio_mp3_url": audio_mp3_url,
        "podcast_enclosure_present": audio_available,
        "transcript_url": f"{BASE_URL}/food-line/audio/{date}-transcript.html",
        "audio_file": audio_filename if audio_available else None,
        "audio_url": audio_mp3_url,
        "audio_mime_type": "audio/mpeg" if audio_available else None,
        "audio_model": audio_model if generate_audio else None,
        "audio_voice": audio_voice if generate_audio else None,
        "audio_provider": chosen_provider if generate_audio else "none",
        "audio_timeout_seconds": audio_timeout_seconds if generate_audio else None,
        "audio_temp_path": str(audio_temp_path) if generate_audio and (force_audio_regenerate or not audio_available) else None,
        "audio_replacement_performed": audio_replacement_performed,
        "audio_story_section_count": sum(1 for entries in sections.values() if entries),
        "audio_story_sections": [name for name, entries in sections.items() if entries],
        "existing_audio_mp3_path": existing_audio_mp3_path,
        "existing_audio_mp3_size": existing_audio_mp3_size,
        "tts_diagnostics": tts_diagnostics,
        "tts_provider": tts_diagnostics.get("provider"),
        "tts_model_requested": tts_diagnostics.get("model_requested"),
        "tts_voice_requested": tts_diagnostics.get("voice_requested"),
        "tts_narration_char_count": tts_diagnostics.get("narration_char_count"),
        "tts_output_path_attempted": tts_diagnostics.get("output_path_attempted"),
        "tts_api_key_present": tts_diagnostics.get("api_key_present"),
        "tts_output_dir_exists": tts_diagnostics.get("output_dir_exists"),
        "tts_partial_mp3_exists": tts_diagnostics.get("partial_mp3_exists"),
        "tts_elapsed_seconds": tts_diagnostics.get("elapsed_seconds"),
        "tts_exception_type": tts_diagnostics.get("exception_type"),
        "tts_exception_message_sanitized": tts_diagnostics.get("exception_message_sanitized"),
        "tts_error_type": tts_diagnostics.get("exception_type"),
        "tts_error_message_sanitized": tts_diagnostics.get("exception_message_sanitized"),
        "tts_timeout_seconds": tts_diagnostics.get("timeout_seconds"),
        "tts_audio_format": tts_diagnostics.get("audio_format"),
        "tls_verify": tts_diagnostics.get("tls_verify"),
        "ca_file_used": tts_diagnostics.get("ca_file_used"),
        "ca_source": tts_diagnostics.get("ca_source"),
        "truststore_requested": tts_diagnostics.get("truststore_requested"),
        "truststore_available": tts_diagnostics.get("truststore_available"),
        "ssl_cert_file_env": tts_diagnostics.get("ssl_cert_file_env"),
        "requests_ca_bundle_env": tts_diagnostics.get("requests_ca_bundle_env"),
        "bluefern_tts_ca_file_env": tts_diagnostics.get("bluefern_tts_ca_file_env"),
        "tls_workaround_warning": tts_diagnostics.get("tls_workaround_warning"),
        "tts_file_write_exception_type": tts_diagnostics.get("file_write_exception_type"),
        "tts_file_write_exception_message_sanitized": tts_diagnostics.get("file_write_exception_message_sanitized"),
        "warnings": warnings,
        "errors": errors,
    }
    podcast_enclosure_text = "present" if audio_available else "not generated"
    transcript_parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
        f"  <title>Food Line Briefing &mdash; {html.escape(_human_date(date))}</title>",
        '  <link rel="stylesheet" href="../assets/site.css" />',
        "</head>",
        "<body>",
        '  <main class="container food-line-audio-shell">',
        f"    <h1>Food Line Briefing &mdash; {html.escape(_human_date(date))}</h1>",
    ]
    transcript_parts.extend(_food_line_audio_transcript_sections_html(sections))
    if audio_available and audio_mp3_url:
        transcript_parts.append(f'    <p><audio controls preload="none" src="{html.escape(audio_mp3_url)}"></audio></p>')
    transcript_parts.append("    <h2>Source links</h2>")
    transcript_parts.append(_food_line_audio_links_html(date, include_transcript_link=False, audio_mp3_url=audio_mp3_url))
    transcript_parts.append(f"    <p><strong>Podcast enclosure:</strong> {html.escape(podcast_enclosure_text)}</p>")
    transcript_parts.append("  </main>")
    transcript_parts.append("</body>")
    transcript_parts.append("</html>")
    transcript = "".join(transcript_parts)
    _write_text(audio_root / f"{date}-transcript.html", transcript)
    _write_json(audio_root / f"{date}.json", metadata)
    page_footer = footer("../")
    audio_index = "".join(
        [
            _food_line_theme_styles(),
            header(DISPATCH_NAME, "../", "../archive.html", "/food-line/"),
            '<main class="home food-line-audio-shell">\n',
            '  <section class="food-line-hero">\n',
            _food_line_logo_html("food-line-logo--audio", "../assets/"),
            '    <p class="eyebrow">The Blue Fern Co.</p>\n',
            f"    <h1>Food Line Audio &mdash; {_human_date(date)}</h1>\n",
            f"    <p>{html.escape((str(lead.get('publisher') or lead.get('source_name') or 'the source').strip() + ' reported that ' + _audio_lead_summary(lead)) if lead else _food_line_public_story_sentence(lead))}</p>\n",
            '    <p><a href="podcast.xml">Open the podcast feed</a></p>\n',
            '  </section>\n',
            '  <section class="food-line-panel">\n',
            "".join(_food_line_audio_index_sections_html(sections)),
            '    <h2>Source links</h2>\n',
            _food_line_audio_links_html(date, include_transcript_link=True, audio_mp3_url=audio_mp3_url),
            '    <h2>Podcast enclosure status</h2>\n',
            f"    <p><strong>Podcast enclosure:</strong> {html.escape(podcast_enclosure_text)}</p>\n",
            (
                f'    <p><audio controls preload="none" src="{html.escape(audio_mp3_url)}"></audio></p>\n'
                if audio_available and audio_mp3_url
                else ""
            ),
            '    <h2>Artwork note</h2>\n',
            '    <p>Artwork uses the official Food Line logo when it is available.</p>\n',
            '  </section>\n',
            '</main>\n',
            page_footer,
        ]
    )
    _write_text(audio_root / "index.html", _food_line_page("Food Line Audio", f"{BASE_URL}/food-line/audio/index.html", "../assets/site.css", audio_index))
    write_food_line_podcast_feed(project_root=root, dry_run=False, max_edition_date=max_edition_date)
    return {
        "audio_generated": audio_generated,
        "audio_available": audio_available,
        "audio_reused_existing": audio_reused_existing,
        "audio_required": bool(require_audio),
        "force_audio_regenerate": bool(force_audio_regenerate),
        "audio_mp3_path": audio_mp3_path,
        "audio_mp3_url": audio_mp3_url,
        "podcast_enclosure_present": audio_available,
        "audio_status": audio_status,
        "audio_story_section_count": sum(1 for entries in sections.values() if entries),
        "audio_story_sections": [name for name, entries in sections.items() if entries],
        "audio_timeout_seconds": audio_timeout_seconds if generate_audio else None,
        "audio_temp_path": str(audio_temp_path) if generate_audio and (force_audio_regenerate or not audio_available) else None,
        "audio_replacement_performed": audio_replacement_performed,
        "existing_audio_mp3_path": existing_audio_mp3_path,
        "existing_audio_mp3_size": existing_audio_mp3_size,
        "tts_diagnostics": tts_diagnostics,
        "tts_provider": tts_diagnostics.get("provider"),
        "tts_model_requested": tts_diagnostics.get("model_requested"),
        "tts_voice_requested": tts_diagnostics.get("voice_requested"),
        "tts_narration_char_count": tts_diagnostics.get("narration_char_count"),
        "tts_output_path_attempted": tts_diagnostics.get("output_path_attempted"),
        "tts_api_key_present": tts_diagnostics.get("api_key_present"),
        "tts_output_dir_exists": tts_diagnostics.get("output_dir_exists"),
        "tts_partial_mp3_exists": tts_diagnostics.get("partial_mp3_exists"),
        "tts_elapsed_seconds": tts_diagnostics.get("elapsed_seconds"),
        "tts_exception_type": tts_diagnostics.get("exception_type"),
        "tts_exception_message_sanitized": tts_diagnostics.get("exception_message_sanitized"),
        "tts_error_type": tts_diagnostics.get("exception_type"),
        "tts_error_message_sanitized": tts_diagnostics.get("exception_message_sanitized"),
        "tts_timeout_seconds": tts_diagnostics.get("timeout_seconds"),
        "tts_audio_format": tts_diagnostics.get("audio_format"),
        "tts_file_write_exception_type": tts_diagnostics.get("file_write_exception_type"),
        "tts_file_write_exception_message_sanitized": tts_diagnostics.get("file_write_exception_message_sanitized"),
        "warnings": warnings,
        "errors": errors,
        "episode_title": episode_title,
        "episode_summary": description,
        "transcript_path": str(audio_root / f"{date}-transcript.html"),
        "metadata_path": str(audio_root / f"{date}.json"),
        "podcast_path": str(audio_root / "podcast.xml"),
    }


def _update_index_archive(root: Path, date: str, mission: str, *, max_edition_date: str | None = None) -> None:
    dispatch_root = root / "output" / "site" / DISPATCH_SLUG
    public_dates = _food_line_home_archive_dates(root, max_edition_date=max_edition_date)
    latest_public_date = public_dates[0] if public_dates else ""
    latest_public_label = _food_line_public_edition_label(root, latest_public_date) if latest_public_date else ""
    recent_public_dates = public_dates[: min(len(public_dates), 11)]
    archive_entries_html = "".join(
        f'<li><a href="editions/{html.escape(public_date)}/">{html.escape(_food_line_public_edition_label(root, public_date))}</a></li>'
        for public_date in public_dates
    )
    recent_entries_html = "".join(
        f'<li><a href="editions/{html.escape(public_date)}/">{html.escape(_food_line_public_edition_label(root, public_date))}</a></li>'
        for public_date in recent_public_dates
    )
    page_footer = footer("")
    idx_body = "".join(
        [
            _food_line_theme_styles(),
            header(DISPATCH_NAME, "", None, None),
            '<main class="home food-line-shell">\n',
            '  <section class="food-line-hero">\n',
            _food_line_logo_html("food-line-logo--home", "assets/"),
            '    <p class="eyebrow">The Blue Fern Co.</p>\n',
            f"    <h1>{DISPATCH_NAME}</h1>\n",
            f"    <p>{html.escape(mission)}</p>\n",
            '  </section>\n',
            '  <section class="food-line-panel">\n',
            '    <h2>Current coverage</h2>\n',
            f"{'<p><a href=\"editions/{0}/\">{1}</a></p>'.format(latest_public_date, html.escape(latest_public_label)) if latest_public_date else '<p>No public editions have been published yet.</p>'}\n",
            '    <p><a href="audio/index.html">Audio and podcast feed</a></p>\n',
            '    <p><a href="archive.html">Browse the Food Line archive</a></p>\n',
            f"{'<p><a href=\"map/\">Pressure map</a></p>' if _food_line_map_is_available(root) else ''}\n",
            '    <p>This dispatch is source-backed and uses verified pressure signals only.</p>\n',
            f"    <p>{html.escape(_food_line_reported_signal_limitation())}</p>\n",
            '  </section>\n',
            '  <section class="food-line-panel">\n',
            '    <h2>Recent Editions</h2>\n',
            '    <p>Recent source-backed editions, newest first.</p>\n',
            f"    <ul>{recent_entries_html}</ul>\n" if recent_entries_html else '    <p>No public editions have been published yet.</p>\n',
            '    <p><a href="archive.html">Open the full archive</a></p>\n',
            '  </section>\n',
            '</main>\n',
            page_footer,
        ]
    )
    archive_body = "".join(
        [
            _food_line_theme_styles(),
            header(DISPATCH_NAME, "", "archive.html", "/food-line/"),
            '<main class="home food-line-shell">\n',
            '  <section class="food-line-hero">\n',
            _food_line_logo_html("food-line-logo--home", "assets/"),
            '    <p class="eyebrow">The Blue Fern Co.</p>\n',
            '    <h1>Food Line Archive</h1>\n',
            '    <p>Chronological archive of source-backed Food Line editions.</p>\n',
            '  </section>\n',
            '  <section class="food-line-panel">\n',
            '    <h2>Latest edition</h2>\n',
            f"{'<p><a href=\"editions/{0}/\">{1}</a></p>'.format(latest_public_date, html.escape(latest_public_label)) if latest_public_date else '<p>No public editions have been published yet.</p>'}\n",
            '    <h2>Archive</h2>\n',
            f"    <ul>{archive_entries_html}</ul>\n",
            '    <p><a href="index.html">Back to the Food Line home page</a></p>\n',
            '  </section>\n',
            '</main>\n',
            page_footer,
        ]
    )
    _write_text(dispatch_root / "index.html", _food_line_page(f"{DISPATCH_NAME}", f"{BASE_URL}/food-line/", "assets/site.css", idx_body))
    _write_text(dispatch_root / "archive.html", _food_line_page(f"{DISPATCH_NAME} Archive", f"{BASE_URL}/food-line/archive.html", "assets/site.css", archive_body))


def _refresh_food_line_source_tables(root: Path) -> None:
    site_editions_root = root / "output" / "site" / DISPATCH_SLUG / "editions"
    dispatch_editions_root = root / "output" / "dispatches" / DISPATCH_SLUG / "editions"
    if not site_editions_root.exists():
        return
    for edition_dir in sorted(path for path in site_editions_root.iterdir() if path.is_dir()):
        sources_path = edition_dir / "sources_manifest.json"
        manifest_path = edition_dir / "edition_manifest.json"
        if not sources_path.exists():
            continue
        manifest = _read_json(manifest_path) if manifest_path.exists() else {}
        if not isinstance(manifest, dict) or not bool(manifest.get("public_rendered")):
            continue
        payload = _read_json(sources_path)
        sources = payload if isinstance(payload, list) else []
        lead_source_record_id = str(manifest.get("lead_source_record_id") or "").strip()
        continuing_source_record_ids = [str(item).strip() for item in (manifest.get("continuing_pressure_source_record_ids") or []) if str(item).strip()]
        lead_row = next((row for row in sources if str(row.get("source_record_id") or "").strip() == lead_source_record_id), None)
        continuing_rows = [row for row in sources if str(row.get("source_record_id") or "").strip() in continuing_source_record_ids]
        public_rows = _food_line_public_rendered_rows(sources, lead_row, continuing_rows)
        source_table_html = _source_table_html(
            edition_dir.name,
            sources,
            public_rows,
            primary_row=lead_row,
            continuing_rows=continuing_rows,
            edition_mode=str(manifest.get("edition_mode") or ""),
        )
        claim_ledger_html = _food_line_claim_ledger_html(
            edition_dir.name,
            sources,
            lead_row,
            continuing_rows,
            edition_mode=str(manifest.get("edition_mode") or ""),
            review_counts=(len(sources), max(0, len(sources) - len(_food_line_public_story_rows(sources, lead_row, continuing_rows, edition_mode=str(manifest.get("edition_mode") or ""))))),
            exclusion_reason_counts=dict((manifest.get("exclusion_reason_counts") or {})),
        )
        _write_text(edition_dir / "source_table.html", source_table_html)
        _write_text(edition_dir / "claim_ledger.html", claim_ledger_html)
        dispatch_edition_dir = dispatch_editions_root / edition_dir.name
        if dispatch_edition_dir.exists():
            _write_text(dispatch_edition_dir / "source_table.html", source_table_html)
            _write_text(dispatch_edition_dir / "claim_ledger.html", claim_ledger_html)


def run_food_line_dispatch(
    root: Path,
    date: str,
    *,
    collect: bool = False,
    collect_fetcher: Any | None = None,
    use_discovery_candidates: bool = False,
    include_discovery_gap_summary: bool = False,
    generate_audio: bool = False,
    require_audio: bool = False,
    force_audio_regenerate: bool = False,
    tts_provider: str = "none",
    audio_model: str = "gpt-4o-mini-tts",
    audio_voice: str = "alloy",
    audio_format: str = "mp3",
    audio_timeout_seconds: float = 90.0,
    allow_future_date: bool = False,
    audit_source_collection: bool = False,
    gold_set_path: Path | None = None,
    dry_run_requested: bool = False,
    audit_allow_live_discovery: bool = False,
) -> dict[str, Any]:
    date = validate_date(date)
    public_max_date = None if allow_future_date else _food_line_local_today().isoformat()
    collect_result: dict[str, Any] | None = None
    collect_reused_existing = False
    discovery_bootstrap_ran = False
    discovery_bootstrap_skipped_reason = ""
    audit_runtime_bounded = False
    audit_runtime_bound_reason = ""
    registry_purpose_refresh = refresh_food_line_pressure_registry_source_purpose(root)
    discovery_bridge_result: dict[str, Any] = {
        "ok": True,
        "discovery_expansion_used": False,
        "discovery_candidate_count": 0,
        "discovery_qualified_candidate_count": 0,
        "discovery_context_candidate_count": 0,
        "discovery_blocked_candidate_count": 0,
        "discovery_duplicate_count": 0,
        "discovery_confidence": "",
        "discovery_confidence_reason": "",
        "discovery_audit_path": "",
        "discovery_candidates_path": "",
        "discovery_candidates_intaked": 0,
        "discovery_candidates_excluded": 0,
        "discovery_candidates_manual_review_required": 0,
        "discovery_no_current_update_state": "no_candidates_found",
        "discovery_no_current_update_reason": "",
        "discovery_source_input_path": "",
        "discovery_review_path": "",
        "discovery_source_rows": [],
    }
    if use_discovery_candidates:
        discovery_bridge_result = run_food_line_discovery_intake_bridge(root, date)
    if collect:
        if audit_source_collection and dry_run_requested and _food_line_can_reuse_collect_artifacts(root, date):
            collect_result = _food_line_reused_collect_result(root, date)
            collect_reused_existing = True
            audit_runtime_bounded = True
            audit_runtime_bound_reason = "reused_existing_collection_artifacts"
        else:
            try:
                collect_result = collect_food_line_auto_sources(root, date, fetcher=collect_fetcher)
            except Exception as exc:  # noqa: BLE001
                collect_result = {"ok": False, "source_count": 0, "failed_sources": [{"source_id": "collector", "reason": str(exc)}]}
    sources, rejected_records, source_diagnostics = _merged_sources(root, date, include_discovery_candidates=use_discovery_candidates)
    for row in sources:
        row_url_date, _ = _url_path_date(str(row.get("url") or ""))
        preclassified = any(
            str(row.get(key) or "").strip()
            for key in (
                "pressure_signal",
                "pressure_verification_status",
                "pressure_type",
                "pressure_reason",
                "pressure_summary",
                "source_role",
            )
        ) and bool(row_url_date)
        if not preclassified:
            pressure_eval = evaluate_food_line_pressure(
                row,
                edition_date=date,
                pressure_required=bool(row.get("pressure_required")),
                max_age_days=int(row.get("max_age_days") or 14),
                positive_keywords=row.get("positive_keywords") or [],
                negative_keywords=row.get("negative_keywords") or [],
            )
            row.update(pressure_eval)
        source_kind = str(row.get("collector_source_type") or row.get("source_type") or "").strip().lower()
        row["extraction_quality"] = str(row.get("extraction_quality") or ("high" if source_kind in {"rss", "feed", "api"} else "medium")).strip().lower()
        row["expected_text_basis"] = str(row.get("expected_text_basis") or ("rss_summary" if source_kind in {"rss", "feed"} else "page_text")).strip().lower()
        row["pressure_verification_required"] = bool(row.get("pressure_verification_required", True))
        if not preclassified:
            row["location_scope"] = pressure_eval.get("location_scope") or ("state_local" if str(row.get("state") or "").strip().upper() not in {"", "US"} else "national")
        else:
            row["location_scope"] = str(row.get("location_scope") or ("state_local" if str(row.get("state") or "").strip().upper() not in {"", "US"} else "national"))
        row["date_basis"] = str(row.get("published_date_basis") or "source_published")
        row["map_eligible"] = _food_line_map_eligible(row)
        row["coordinate_basis"] = ""
        if preclassified:
            published_at_for_freshness = "" if row_url_date else str(row.get("published_at") or row.get("published_date") or "")
            freshness_probe = validate_food_line_source_freshness(
                date,
                published_at_for_freshness,
                str(row.get("url") or ""),
                str(row.get("source_role") or "current_public_story"),
                page_metadata_date=str(row.get("page_metadata_date") or ""),
                audit_url_path_date=True,
                background=_food_line_source_background_reference(row),
                freshness_window_days=FOOD_LINE_FRESHNESS_WINDOW_DAYS,
            )
            row["source_freshness_status"] = freshness_probe["source_freshness_status"]
            row["source_freshness_disqualification_reason"] = freshness_probe["source_freshness_disqualification_reason"]
            row["source_freshness_window_days"] = freshness_probe["freshness_window_days"]
            row["source_published_date"] = freshness_probe["source_published_date"]
            row["source_published_date_basis"] = freshness_probe["source_published_date_basis"]
            row["source_url_date"] = freshness_probe["source_url_date"]
            row["source_url_date_basis"] = freshness_probe["source_url_date_basis"]
            row["source_freshness_date_basis"] = freshness_probe["source_freshness_date_basis"]
            row["source_public_story_eligible"] = bool(freshness_probe["public_story_eligible"]) or bool(row.get("pressure_signal"))
            if freshness_probe["source_freshness_status"] in {"stale_outside_daily_window", "missing_source_published_date", "unparsed_source_published_date", "url_path_only"} and not bool(freshness_probe.get("background_reference")):
                row["pressure_signal"] = False
                row["pressure_reason"] = freshness_probe["source_freshness_disqualification_reason"] or "stale/outside daily window"
                row["pressure_summary"] = ""
                row["pressure_verification_status"] = "demoted_context"
                row["map_eligible"] = False
                if freshness_probe["source_freshness_status"] == "url_path_only":
                    row["source_role"] = "background_context"
            row["freshness_status"] = freshness_probe["source_freshness_status"]
            row["freshness_disqualification_reason"] = freshness_probe["source_freshness_disqualification_reason"]
        else:
            _food_line_apply_freshness_guard(row, date)
        if not bool(row.get("supported_product_geography", True)) or not food_line_is_supported_geography(row):
            row["supported_product_geography"] = False
            row["pressure_signal"] = False
            row["pressure_reason"] = "outside product geography"
            row["pressure_summary"] = ""
            row["pressure_verification_status"] = "demoted_context"
            row["map_eligible"] = False
            row["source_role"] = "resource_context"
            row["location_scope"] = "outside_product_geography"
        else:
            row["map_eligible"] = _food_line_map_eligible(row)
    adequacy = source_adequacy(sources)
    previous_context = _load_previous_edition_context(root, date)
    _annotate_food_line_primary_eligibility(sources, previous_context)
    lead_row, continuing_rows, why_lead, primary_signal_status = _select_primary_pressure_signal(sources, date, previous_context)
    lead_tags = list((lead_row or {}).get("issue_tags") or [])
    primary_disqualification_reason = ""
    if not lead_row:
        primary_disqualification_reason = next(
            (
                str(row.get("primary_disqualification_reason") or "").strip()
                for row in sources
                if bool(row.get("pressure_signal"))
                and not bool(row.get("primary_eligible"))
                and str(row.get("primary_disqualification_reason") or "").strip()
                and not _is_reused_previous_lead(row, previous_context)
            ),
            "",
        )
        if not primary_disqualification_reason and primary_signal_status == "continuing_only":
            primary_disqualification_reason = why_lead
        elif not primary_disqualification_reason:
            primary_disqualification_reason = why_lead
    future_date_blocked, future_date_override_used = _food_line_future_date_blocked(date, allow_future_date=allow_future_date)
    if future_date_blocked:
        primary_signal_status = "future_date_blocked"
        primary_disqualification_reason = _food_line_future_date_reason()
        why_lead = _food_line_future_date_reason()
    role_counts = _role_counts(sources)
    scope_counts = _scope_counts(sources)
    editorial_status = _editorial_status(sources)
    pressure_status = _pressure_status(sources)
    edition_dir_site = root / "output" / "site" / DISPATCH_SLUG / "editions" / date
    edition_dir_dispatch = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / date
    review_dir = root / "output" / "review" / DISPATCH_SLUG / date
    diagnostics_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / date
    mission = "The Food Line Dispatch tracks daily signs of food insecurity across the United States - benefit disruptions, pantry strain, school-meal gaps, price pressure, and local access failures - using source-backed public records and reporting."
    _food_line_assets(root, source_diagnostics, [])
    map_data = _build_map_data(date, sources)
    for marker in map_data.get("pressure_markers") or []:
        if isinstance(marker, dict):
            coords = _resolve_marker_coordinates(marker)
            marker["coordinate_basis"] = coords[2] if coords else ""
    pressure_source_count_by_family = {
        family: sum(1 for row in sources if str(row.get("source_family") or "") == family and bool(row.get("pressure_signal")))
        for family in sorted({str(row.get("source_family") or "") for row in sources if str(row.get("source_family") or "")})
    }
    pressure_source_count_by_state = {
        state: sum(1 for row in sources if str(row.get("state") or "") == state and bool(row.get("pressure_signal")))
        for state in sorted({str(row.get("state") or "") for row in sources if str(row.get("state") or "")})
    }
    news_item_count = sum(1 for row in sources if str(row.get("collector_source_type") or row.get("source_type") or "").lower() == "rss")
    provider_pressure_count = sum(1 for row in sources if str(row.get("source_family") or "") == "food_bank_provider" and bool(row.get("pressure_signal")))
    official_pressure_count = sum(1 for row in sources if str(row.get("source_family") or "") in {"state_official", "federal_official", "state_policy_news"} and bool(row.get("pressure_signal")))
    baseline_source_count = sum(1 for row in sources if str(row.get("source_role") or "") == "baseline_condition")
    rejected_news_count = int((collect_result or {}).get("rejected_news_count") or 0)
    rejected_news_reasons = list((collect_result or {}).get("rejected_news_reasons") or [])
    rejected_news_by_source = dict((collect_result or {}).get("rejected_news_by_source") or {})
    collected_source_count_by_source_id = dict((collect_result or {}).get("collected_source_count_by_source_id") or {})
    pressure_verified_count = int((collect_result or {}).get("pressure_verified_count") or sum(1 for row in sources if str(row.get("pressure_verification_status") or "") == "source_text_verified"))
    pressure_demoted_unverified_count = int((collect_result or {}).get("pressure_demoted_unverified_count") or sum(1 for row in sources if str(row.get("pressure_verification_status") or "") == "demoted_context"))
    pressure_registry_only_count = int((collect_result or {}).get("pressure_registry_only_count") or sum(1 for row in sources if str(row.get("pressure_verification_status") or "") == "registry_summary_only"))
    pressure_evidence_basis_counts = dict((collect_result or {}).get("pressure_evidence_basis_counts") or Counter(str(row.get("evidence_text_basis") or "insufficient_evidence") for row in sources))
    collected_count_by_extraction_quality = dict((collect_result or {}).get("collected_count_by_extraction_quality") or Counter(str(row.get("extraction_quality") or "unknown") for row in sources))
    verified_pressure_count_by_extraction_quality = dict((collect_result or {}).get("verified_pressure_count_by_extraction_quality") or Counter(str(row.get("extraction_quality") or "unknown") for row in sources if str(row.get("pressure_verification_status") or "") == "source_text_verified"))
    demoted_count_by_extraction_quality = dict((collect_result or {}).get("demoted_count_by_extraction_quality") or Counter(str(row.get("extraction_quality") or "unknown") for row in sources if str(row.get("pressure_verification_status") or "") == "demoted_context"))
    fetch_failure_count_by_source_id = dict((collect_result or {}).get("fetch_failure_count_by_source_id") or {})
    fetch_failure_count_by_type = dict((collect_result or {}).get("fetch_failure_count_by_type") or {})
    fetch_failure_type_by_source_id = dict((collect_result or {}).get("fetch_failure_type_by_source_id") or {})
    fetch_failure_action_by_source_id = dict((collect_result or {}).get("fetch_failure_action_by_source_id") or {})
    no_evidence_count_by_source_id = dict((collect_result or {}).get("no_evidence_count_by_source_id") or {})
    rejected_by_source_purpose_count = int((collect_result or {}).get("rejected_by_source_purpose_count") or sum(1 for row in sources if str(row.get("source_purpose") or "") in {"donation_page", "evergreen_context", "resource_page", "program_description"} and str(row.get("pressure_verification_status") or "") == "demoted_context"))
    demoted_by_source_purpose_count = int((collect_result or {}).get("demoted_by_source_purpose_count") or sum(1 for row in sources if str(row.get("source_purpose") or "") in {"donation_page", "evergreen_context", "resource_page", "program_description"} and str(row.get("pressure_verification_status") or "") == "demoted_context"))
    collector_audit_path = str((collect_result or {}).get("collector_audit_path") or "")
    context_rows = _food_line_traceability_rows(sources, lead_row, continuing_rows)
    continuing_source_record_ids = [str(row.get("source_record_id") or "").strip() for row in continuing_rows if str(row.get("source_record_id") or "").strip()]
    continuing_source_urls = [str(row.get("url") or "").strip() for row in continuing_rows if str(row.get("url") or "").strip()]
    no_current_update = False
    no_current_update_candidate = False
    edition_mode = "blocked_future_date"
    public_rendered = False
    qualified_primary_count = 1 if lead_row and not future_date_blocked else 0
    continuing_pressure_count = len(continuing_rows)
    continuing_context_count = continuing_pressure_count + len(context_rows)
    public_rows = []
    source_table = ""
    excluded_count = max(0, len(sources) - len(map_data.get("pressure_markers") or []))
    stale_source_rows = [
        row
        for row in sources
        if str(row.get("source_freshness_status") or row.get("freshness_status") or "") == "stale_outside_daily_window"
    ]
    stale_public_story_count = sum(1 for row in stale_source_rows if not _food_line_source_background_reference(row))
    excluded_stale_source_count = len(stale_source_rows)
    stale_source_ids = [str(row.get("source_record_id") or "").strip() for row in stale_source_rows if str(row.get("source_record_id") or "").strip()]
    no_current_update_candidate = bool(stale_public_story_count) and not future_date_blocked and not lead_row
    no_current_update = no_current_update_candidate
    edition_mode = "blocked_future_date" if future_date_blocked else ("current_update" if lead_row else ("no_current_update" if no_current_update_candidate else "no_public_edition"))
    public_rendered = edition_mode == "current_update"
    public_section_rows = _food_line_public_section_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    public_rows = _food_line_public_rendered_rows(sources, lead_row, continuing_rows) if public_rendered else []
    current_public_rows = [
        row
        for row in public_rows
        if _food_line_public_usage_label(row, lead_row, continuing_rows, edition_mode=edition_mode) != "Background reference"
    ]
    public_signal_count = 0 if edition_mode == "no_current_update" else len(current_public_rows)
    public_story_rows = _food_line_public_story_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    qualifying_public_rows = [row for row in sources if _food_line_qualifies_for_public_inclusion(row)]
    public_story_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in public_story_rows
        if str(row.get("source_record_id") or "").strip()
    }
    qualified_but_not_public_count = sum(
        1
        for row in qualifying_public_rows
        if str(row.get("source_record_id") or "").strip() not in public_story_ids
    )
    qualified_but_not_public_warning = ""
    if qualified_but_not_public_count > 0:
        qualified_but_not_public_warning = (
            f"Food Line found {qualified_but_not_public_count} source"
            f"{'s' if qualified_but_not_public_count != 1 else ''} that qualified for public inclusion but were not published."
        )
    claim_rows = _food_line_claim_ledger_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    discovery_gap_summary = (
        _food_line_discovery_gap_summary(root, date, public_story_rows)
        if include_discovery_gap_summary
        else {
            "run": False,
            "report_found": False,
            "report_path": str(_food_line_discovery_gap_report_paths(root, date)[0]),
            "report_markdown_path": str(_food_line_discovery_gap_report_paths(root, date)[1]),
            "likely_qualifying_count": 0,
            "unreviewed_likely_qualifying_count": 0,
            "warning": "",
        }
    )
    provisional_source_freshness_status = _food_line_no_current_update_policy_freshness_status(
        future_date_blocked=future_date_blocked,
        no_current_update_candidate=no_current_update_candidate,
        stale_public_story_count=stale_public_story_count,
        public_rendered=public_rendered,
        discovery_gap_check=discovery_gap_summary,
        discovery_bridge_result=discovery_bridge_result,
    )
    no_current_update_policy = evaluate_food_line_no_current_update_publication_policy(
        edition_mode=edition_mode,
        collector_result=collect_result,
        discovery_gap_check=discovery_gap_summary,
        discovery_expansion_used=bool(discovery_bridge_result.get("discovery_expansion_used")),
        source_freshness_status=provisional_source_freshness_status,
        news_item_count=news_item_count,
        local_signal_count=scope_counts["local_signal_count"],
        state_signal_count=scope_counts["state_signal_count"],
        discovery_gap_unreviewed_likely_qualifying_count=_food_line_int(discovery_gap_summary.get("unreviewed_likely_qualifying_count")),
    )
    if no_current_update_candidate and not no_current_update_policy["allowed"]:
        no_current_update = False
        edition_mode = "internal_no_qualifying_update"
        public_rendered = False
    elif no_current_update_candidate:
        no_current_update = True
        edition_mode = "no_current_update"
        public_rendered = True
    public_section_rows = _food_line_public_section_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    public_rows = _food_line_public_rendered_rows(sources, lead_row, continuing_rows) if public_rendered else []
    current_public_rows = [
        row
        for row in public_rows
        if _food_line_public_usage_label(row, lead_row, continuing_rows, edition_mode=edition_mode) != "Background reference"
    ]
    public_signal_count = 0 if edition_mode == "no_current_update" else len(current_public_rows)
    public_story_rows = _food_line_public_story_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    public_story_ids = {
        str(row.get("source_record_id") or "").strip()
        for row in public_story_rows
        if str(row.get("source_record_id") or "").strip()
    }
    qualified_but_not_public_count = sum(
        1
        for row in qualifying_public_rows
        if str(row.get("source_record_id") or "").strip() not in public_story_ids
    )
    qualified_but_not_public_warning = ""
    if qualified_but_not_public_count > 0:
        qualified_but_not_public_warning = (
            f"Food Line found {qualified_but_not_public_count} source"
            f"{'s' if qualified_but_not_public_count != 1 else ''} that qualified for public inclusion but were not published."
        )
    claim_rows = _food_line_claim_ledger_rows(sources, lead_row, continuing_rows, edition_mode=edition_mode)
    for row in sources:
        row_id = str(row.get("source_record_id") or "").strip()
        row["claim_supported"] = _food_line_claim_supported_text(row)
        row["limitations"] = _food_line_claim_limitation(row)
        row["included"] = bool(row_id and row_id in public_story_ids)
        if row["included"]:
            row["exclusion_reason"] = ""
        else:
            usage_label = _food_line_public_usage_label(row, lead_row, continuing_rows, edition_mode=edition_mode)
            if usage_label == "Background reference":
                row["exclusion_reason"] = "background reference"
            elif usage_label.startswith("Source audit"):
                row["exclusion_reason"] = "source audit"
            else:
                row["exclusion_reason"] = str(row.get("exclusion_reason") or row.get("pressure_reason") or row.get("freshness_disqualification_reason") or row.get("source_freshness_disqualification_reason") or "")
        row["source_role"] = str(row.get("source_role") or _source_role(row) or "")
        row["freshness_role"] = str(row.get("freshness_role") or _freshness_role(row) or "")
        row["evidence_level"] = str(row.get("evidence_level") or _evidence_level(row, bool(row.get("pressure_signal"))) or "")
        row["location_scope"] = str(row.get("location_scope") or ("state_local" if str(row.get("state") or "").strip().upper() not in {"", "US"} else "national") or "")
        research_context = _food_line_is_research_context_signal(row)
        if research_context:
            row["primary_eligible"] = False
            row["primary_disqualification_reason"] = row.get("primary_disqualification_reason") or "research/context signal"
        row["qualifies_for_public_inclusion"] = _food_line_qualifies_for_public_inclusion(row)
        row["public_inclusion_reason"] = _food_line_public_inclusion_reason(row)
        row["public_inclusion_bucket"] = _food_line_public_inclusion_bucket(row, is_lead=bool(lead_row and row_id == str(lead_row.get("source_record_id") or "").strip()))
        row["eligible_for_lead"] = bool(row.get("primary_eligible")) and bool(row.get("qualifies_for_public_inclusion"))
        row["included_as_lead"] = bool(lead_row and row_id == str(lead_row.get("source_record_id") or "").strip())
        row["included_as_context_signal"] = row["public_inclusion_bucket"] == "included_as_context_signal"
        row["included_as_policy_access_signal"] = row["public_inclusion_bucket"] == "included_as_policy_access_signal"
        row["included_as_provider_operations_signal"] = row["public_inclusion_bucket"] == "included_as_provider_operations_signal"
        row["included_as_additional_signal"] = row["public_inclusion_bucket"] == "included_as_additional_signal"
        row["context_only"] = row["public_inclusion_bucket"] in {"context_only", "included_as_context_signal"}
        row["excluded"] = row["public_inclusion_bucket"] == "excluded"
    classification_summary = _annotate_food_line_candidate_review_fields(sources)
    pressure_review_path = _write_food_line_review_csv(root, date, sources)
    candidate_review_json_path, candidate_review_html_path = _write_food_line_candidate_review_artifacts(
        root,
        date,
        sources,
        classification_summary,
    )
    source_collection_audit_summary = {
        "source_collection_audit_run": False,
        "source_collection_gold_count": 0,
        "source_collection_found_count": 0,
        "source_collection_reached_review_count": 0,
        "source_collection_qualified_count": 0,
        "source_collection_rejected_with_reason_count": 0,
        "source_collection_missed_count": 0,
        "source_collection_high_priority_missed_count": 0,
        "source_collection_recall": 0.0,
        "source_collection_high_priority_recall": 0.0,
        "source_collection_likely_failure_category": "no_major_gap_detected",
        "source_collection_audit_path": "",
        "source_collection_audit_markdown_path": "",
        "source_collection_audit_elapsed_seconds": 0.0,
        "source_collection_live_discovery_ran": False,
        "source_collection_live_discovery_skipped_reason": "",
        "source_collection_collect_reused_existing": False,
        "source_collection_collect_live_ran": False,
        "source_collection_candidate_count_evaluated": 0,
        "source_collection_query_families_used": [],
        "source_collection_runtime_bounded": False,
        "source_collection_runtime_bound_reason": "",
    }
    if audit_source_collection:
        audit_started_at = time.monotonic()
        should_bootstrap_discovery_audit = bool(collect or include_discovery_gap_summary)
        if should_bootstrap_discovery_audit and not _food_line_discovery_candidates_path(root, date).exists():
            if dry_run_requested and not audit_allow_live_discovery:
                discovery_bootstrap_skipped_reason = "live_discovery_skipped_for_bounded_audit_dry_run"
                audit_runtime_bounded = True
                if not audit_runtime_bound_reason:
                    audit_runtime_bound_reason = discovery_bootstrap_skipped_reason
            else:
                run_food_line_discovery_expansion(
                    root,
                    date,
                    edition_mode="no_current_update" if no_current_update_candidate else "current_update",
                    dry_run=False,
                )
                discovery_bootstrap_ran = True
        if should_bootstrap_discovery_audit and not _food_line_discovery_intake_review_path(root, date).exists():
            if _food_line_discovery_candidates_path(root, date).exists():
                run_food_line_discovery_intake_bridge(root, date, dry_run=False)
                discovery_bootstrap_ran = True
        source_collection_audit_summary = run_food_line_source_collection_audit(
            root,
            date,
            gold_set_path=gold_set_path or _food_line_source_collection_gold_set_path(root, date),
            sources=sources,
            rejected_records=rejected_records,
            pressure_review_path=pressure_review_path,
            collect_result=collect_result,
        )
        discovery_expansion_audit_for_summary = _food_line_discovery_expansion_audit(root, date)
        source_collection_audit_summary.update(
            {
                "source_collection_audit_elapsed_seconds": round(time.monotonic() - audit_started_at, 3),
                "source_collection_live_discovery_ran": discovery_bootstrap_ran,
                "source_collection_live_discovery_skipped_reason": discovery_bootstrap_skipped_reason,
                "source_collection_collect_reused_existing": collect_reused_existing,
                "source_collection_collect_live_ran": bool(collect and not collect_reused_existing),
                "source_collection_candidate_count_evaluated": int(
                    discovery_expansion_audit_for_summary.get("candidate_count")
                    or len(discovery_bridge_result.get("discovery_source_rows") or [])
                    or 0
                ),
                "source_collection_query_families_used": sorted(
                    str(key)
                    for key in (discovery_expansion_audit_for_summary.get("candidates_by_query_family") or {}).keys()
                    if str(key).strip()
                ),
                "source_collection_runtime_bounded": audit_runtime_bounded,
                "source_collection_runtime_bound_reason": audit_runtime_bound_reason,
            }
        )
    exclusion_reason_counts = _food_line_exclusion_reason_counts(
        sources,
        rejected_records,
        source_diagnostics,
        rejected_news_reasons,
        current_public_rows,
    )
    exclusion_reason_summary = _food_line_exclusion_reason_summary(exclusion_reason_counts)
    map_data.setdefault("diagnostics", {})
    map_data["diagnostics"]["exclusion_reason_counts"] = exclusion_reason_counts
    map_data["diagnostics"]["exclusion_reason_summary"] = exclusion_reason_summary
    source_table = _source_table_html(
        date,
        sources,
        sources,
        primary_row=lead_row,
        continuing_rows=continuing_rows,
        edition_mode=edition_mode,
    )
    claim_ledger_html = _food_line_claim_ledger_html(
        date,
        sources,
        lead_row,
        continuing_rows,
        edition_mode=edition_mode,
        review_counts=(len(sources), max(0, len(sources) - len(public_story_rows))),
        exclusion_reason_counts=exclusion_reason_counts,
    )
    no_current_update_policy_reasons = list(no_current_update_policy.get("reasons") or [])
    no_current_update_policy_reason_text = "; ".join(no_current_update_policy_reasons)
    if future_date_blocked:
        skip_reason = _food_line_future_date_reason()
    elif edition_mode == "internal_no_qualifying_update":
        skip_reason = no_current_update_policy_reason_text or "Food Line public no-qualifying-update policy blocked publication."
    elif public_rendered:
        skip_reason = ""
    else:
        skip_reason = _food_line_skip_reason()
    if future_date_blocked:
        source_freshness_status = "future_date_blocked"
        food_line_publish_blocked_reason = _food_line_future_date_reason()
    elif edition_mode == "no_current_update":
        source_freshness_status = "passed_no_qualifying_update"
        food_line_publish_blocked_reason = ""
    elif edition_mode == "internal_no_qualifying_update":
        source_freshness_status = (
            provisional_source_freshness_status
            if _food_line_no_current_update_blocked_freshness_status(provisional_source_freshness_status)
            else "blocked_no_current_update_policy"
        )
        food_line_publish_blocked_reason = (
            "Food Line public no-qualifying-update policy blocked publication: "
            f"{no_current_update_policy_reason_text or 'public no-qualifying-update requirements were not met.'}"
        )
    elif public_rendered:
        source_freshness_status = "passed" if not stale_public_story_count else "passed_with_stale_exclusions"
        food_line_publish_blocked_reason = ""
    else:
        source_freshness_status = "blocked_insufficient_current_story_sources"
        food_line_publish_blocked_reason = skip_reason
    discovery_expansion_audit = _food_line_discovery_expansion_audit(root, date)
    manifest = {
        "dispatch_slug": DISPATCH_SLUG,
        "dispatch_name": DISPATCH_NAME,
        "edition_date": date,
        "generated_at": utc_now(),
        "source_count": len(sources),
        "story_count": qualified_primary_count,
        "source_adequacy": adequacy,
        "lead_source_record_id": (lead_row or {}).get("source_record_id"),
        "lead_source_canonical_url": canonical_url(str((lead_row or {}).get("url") or "")) if lead_row else "",
        "previous_edition_date": previous_context.get("previous_edition_date"),
        "primary_signal_status": primary_signal_status,
        "primary_disqualification_reason": primary_disqualification_reason,
        "public_rendered": public_rendered,
        "public_signal_count": public_signal_count,
        "qualified_primary_count": qualified_primary_count,
        "claim_count": len(claim_rows),
        "claim_ledger_path": f"/food-line/editions/{date}/claim_ledger.html",
        "source_table_path": f"/food-line/editions/{date}/source_table.html",
        "qualified_source_count": len(claim_rows),
        "excluded_source_count": max(0, len(sources) - len(claim_rows)),
        "correction_status": "none",
        "validation_status": "pending",
        "future_date_blocked": future_date_blocked,
        "future_date_override_used": future_date_override_used,
        "edition_mode": edition_mode,
        "continuing_context_count": continuing_context_count,
        "continuing_pressure_count": continuing_pressure_count,
        "context_count": len(context_rows),
        "excluded_count": excluded_count,
        "exclusion_reason_counts": exclusion_reason_counts,
        "exclusion_reason_summary": exclusion_reason_summary,
        "skip_reason": skip_reason,
        "continuing_pressure_source_record_ids": continuing_source_record_ids,
        "continuing_pressure_source_urls": continuing_source_urls,
        "lead_issue_tags": lead_tags,
        "source_roles_count": role_counts,
        "local_signal_count": scope_counts["local_signal_count"],
        "state_signal_count": scope_counts["state_signal_count"],
        "national_context_count": scope_counts["national_context_count"],
        "editorial_status": editorial_status,
        "pressure_status": pressure_status,
        "why_this_lead": why_lead,
        "primary_disqualification_reason": primary_disqualification_reason,
        "selected_lead_source_role": (lead_row or {}).get("source_role"),
        "selected_lead_pressure_type": (lead_row or {}).get("pressure_type"),
        "selected_lead_affected_groups": (lead_row or {}).get("affected_groups") or [],
        "pressure_source_count_by_family": pressure_source_count_by_family,
        "pressure_source_count_by_state": pressure_source_count_by_state,
        "news_item_count": news_item_count,
        "provider_pressure_count": provider_pressure_count,
        "official_pressure_count": official_pressure_count,
        "baseline_source_count": baseline_source_count,
        "rejected_news_count": rejected_news_count,
        "rejected_news_reasons": rejected_news_reasons,
        "rejected_news_by_source": rejected_news_by_source,
        "collected_source_count_by_source_id": collected_source_count_by_source_id,
        "pressure_verified_count": pressure_verified_count,
        "pressure_demoted_unverified_count": pressure_demoted_unverified_count,
        "pressure_registry_only_count": pressure_registry_only_count,
        "pressure_evidence_basis_counts": dict(sorted(pressure_evidence_basis_counts.items())),
        "collected_count_by_extraction_quality": dict(sorted(collected_count_by_extraction_quality.items())),
        "verified_pressure_count_by_extraction_quality": dict(sorted(verified_pressure_count_by_extraction_quality.items())),
        "demoted_count_by_extraction_quality": dict(sorted(demoted_count_by_extraction_quality.items())),
        "fetch_failure_count_by_source_id": dict(sorted(fetch_failure_count_by_source_id.items())),
        "fetch_failure_count_by_type": dict(sorted(fetch_failure_count_by_type.items())),
        "fetch_failure_type_by_source_id": dict(sorted(fetch_failure_type_by_source_id.items())),
        "fetch_failure_action_by_source_id": dict(sorted(fetch_failure_action_by_source_id.items())),
        "no_evidence_count_by_source_id": dict(sorted(no_evidence_count_by_source_id.items())),
        "rejected_by_source_purpose_count": rejected_by_source_purpose_count,
        "demoted_by_source_purpose_count": demoted_by_source_purpose_count,
        "registry_source_purpose_refresh": registry_purpose_refresh,
        "source_freshness_status": source_freshness_status,
        "stale_public_story_count": stale_public_story_count,
        "excluded_stale_source_count": excluded_stale_source_count,
        "freshness_window_days": FOOD_LINE_FRESHNESS_WINDOW_DAYS,
        "stale_source_ids": stale_source_ids,
        "food_line_publish_blocked_reason": food_line_publish_blocked_reason,
        "food_line_no_current_update_policy_status": no_current_update_policy.get("status"),
        "food_line_no_current_update_policy_allowed": bool(no_current_update_policy.get("allowed")),
        "food_line_no_current_update_policy_reasons": no_current_update_policy_reasons,
        "food_line_no_current_update_policy_metrics": no_current_update_policy.get("metrics") or {},
        "discovery_gap_check": discovery_gap_summary,
        "discovery_gap_likely_qualifying_count": int(discovery_gap_summary.get("likely_qualifying_count") or 0),
        "discovery_gap_unreviewed_likely_qualifying_count": int(discovery_gap_summary.get("unreviewed_likely_qualifying_count") or 0),
        "discovery_gap_warning": discovery_gap_summary.get("warning") or "",
        "discovery_gap_report_path": discovery_gap_summary.get("report_path"),
        "discovery_gap_report_markdown_path": discovery_gap_summary.get("report_markdown_path"),
        "discovery_expansion_audit_path": discovery_expansion_audit.get("discovery_audit_json_path"),
        "discovery_expansion_audit_markdown_path": discovery_expansion_audit.get("discovery_audit_md_path"),
        "discovery_expansion_candidate_path": discovery_expansion_audit.get("discovery_candidates_path"),
        "discovery_confidence": discovery_expansion_audit.get("discovery_confidence"),
        "discovery_confidence_reason": discovery_expansion_audit.get("discovery_confidence_reason"),
        "discovery_confidence_summary": discovery_expansion_audit.get("discovery_confidence_summary"),
        "discovery_expansion_used": bool(discovery_bridge_result.get("discovery_expansion_used")),
        "discovery_candidate_count": int(discovery_bridge_result.get("discovery_candidate_count") or 0),
        "discovery_qualified_candidate_count": int(discovery_bridge_result.get("discovery_qualified_candidate_count") or 0),
        "discovery_context_candidate_count": int(discovery_bridge_result.get("discovery_context_candidate_count") or 0),
        "discovery_blocked_candidate_count": int(discovery_bridge_result.get("discovery_blocked_candidate_count") or 0),
        "discovery_duplicate_count": int(discovery_bridge_result.get("discovery_duplicate_count") or 0),
        "discovery_candidates_intaked": int(discovery_bridge_result.get("discovery_candidates_intaked") or 0),
        "discovery_candidates_excluded": int(discovery_bridge_result.get("discovery_candidates_excluded") or 0),
        "discovery_candidates_manual_review_required": int(discovery_bridge_result.get("discovery_candidates_manual_review_required") or 0),
        "discovery_source_input_path": discovery_bridge_result.get("discovery_source_input_path"),
        "discovery_review_path": discovery_bridge_result.get("discovery_review_path"),
        "discovery_no_current_update": _food_line_discovery_no_current_update_metadata(edition_mode, discovery_bridge_result)[0],
        "discovery_no_current_update_state": discovery_bridge_result.get("discovery_no_current_update_state"),
        "discovery_no_current_update_reason": _food_line_discovery_no_current_update_metadata(edition_mode, discovery_bridge_result)[1],
        "public_url": f"{BASE_URL}/food-line/editions/{date}/" if public_rendered else None,
        "public_signal_count": public_signal_count,
        "candidate_count_total": len(sources),
        "candidate_count_traceable": sum(1 for row in sources if str(row.get("traceability_status") or "") == "traceable"),
        "candidate_count_approved": sum(1 for row in sources if str(row.get("review_status") or "") == "approved"),
        "candidate_count_needs_review": sum(1 for row in sources if str(row.get("review_status") or "") == "needs_review"),
        "candidate_count_watchlist": sum(1 for row in sources if str(row.get("review_status") or "") == "watchlist"),
        "candidate_count_rejected": sum(1 for row in sources if str(row.get("review_status") or "") == "rejected"),
        "public_claim_eligible_count": int(classification_summary.get("public_claim_eligible_count") or 0),
        "public_claim_blocker_counts": dict(classification_summary.get("public_claim_blocker_counts") or {}),
        "intake_broadened": True,
        "candidate_review_json_path": str(candidate_review_json_path),
        "candidate_review_html_path": str(candidate_review_html_path),
        "qualified_but_not_public_count": qualified_but_not_public_count,
        "qualified_but_not_public_warning": qualified_but_not_public_warning,
        "bluesky_post_text": None,
        "bluesky_post_ready": False,
        **source_collection_audit_summary,
    }
    bluesky_post_text = _food_line_bluesky_post_text(
        date,
        lead_row,
        public_url=manifest["public_url"],
        context_rows=[row for row in public_section_rows.get("context") or [] if row],
    )
    if future_date_blocked:
        bluesky_post_text = None
    manifest["bluesky_post_text"] = bluesky_post_text
    manifest["bluesky_post_ready"] = bool(bluesky_post_text)

    audio_result: dict[str, Any] = {
        "audio_generated": False,
        "audio_available": False,
        "audio_reused_existing": False,
        "audio_required": require_audio,
        "force_audio_regenerate": bool(force_audio_regenerate),
        "audio_mp3_path": None,
        "audio_mp3_url": None,
        "podcast_enclosure_present": False,
        "existing_audio_mp3_path": None,
        "existing_audio_mp3_size": None,
        "audio_temp_path": None,
        "audio_replacement_performed": False,
        "audio_status": "skipped",
        "audio_story_section_count": 0,
        "audio_story_sections": [],
        "warnings": [],
        "errors": [],
    }
    if public_rendered:
        html_page = render_food_line_edition(
            date,
            sources,
            adequacy,
            lead_row,
            editorial_status,
            role_counts,
            scope_counts,
            previous_context,
            primary_signal_status,
            continuing_rows,
            edition_mode=edition_mode,
        )
        for d in (edition_dir_site, edition_dir_dispatch):
            _write_text(d / "index.html", html_page)
            _write_text(d / "source_table.html", source_table)
            _write_text(d / "claim_ledger.html", claim_ledger_html)
            manifest["validation_status"] = "ok" if all((d / name).exists() for name in ("index.html", "source_table.html", "claim_ledger.html")) else "error"
            _write_json(d / "sources_manifest.json", sources)
            _write_json(d / "curation_manifest.json", {"stories": sources[:6]})
            _write_json(d / "edition_manifest.json", manifest)
        map_html = _render_map_index(date, map_data)
        _write_json(root / "output" / "site" / DISPATCH_SLUG / "map" / "map_data.json", map_data)
        _write_text(root / "output" / "site" / DISPATCH_SLUG / "map" / "index.html", map_html)
        _write_food_line_diagnostics_manifest(root, date, manifest, map_data)
        _update_index_archive(root, date, mission, max_edition_date=public_max_date)
        _refresh_food_line_source_tables(root)
        audio_result = write_food_line_audio(
            root,
            date,
            sources,
            adequacy,
            lead_row,
            editorial_status,
            previous_context,
            continuing_rows,
            generate_audio=generate_audio,
            require_audio=require_audio,
            force_audio_regenerate=force_audio_regenerate,
            tts_provider=tts_provider,
            audio_model=audio_model,
            audio_voice=audio_voice,
            audio_format=audio_format,
            audio_timeout_seconds=audio_timeout_seconds,
            max_edition_date=public_max_date or "",
            edition_mode=edition_mode,
        )
        _prune_food_line_public_artifacts(root, allow_future_date=allow_future_date)
        write_food_line_podcast_feed(project_root=root, dry_run=False, max_edition_date=public_max_date or "")
        _update_index_archive(root, date, mission, max_edition_date=public_max_date)
    else:
        _remove_food_line_public_edition(root, date)
        _remove_food_line_audio_artifacts(root, date)
        _write_food_line_audio_status_page(root, date, skip_reason, include_date=not future_date_blocked)
        _write_food_line_diagnostics_manifest(root, date, manifest, map_data)
        _update_index_archive(root, date, mission, max_edition_date=public_max_date)
        _prune_food_line_public_artifacts(root, allow_future_date=allow_future_date)
        write_food_line_podcast_feed(project_root=root, dry_run=False, max_edition_date=public_max_date or "")
        _update_index_archive(root, date, mission, max_edition_date=public_max_date)
    audio_generated = bool(audio_result.get("audio_generated"))
    audio_available = bool(audio_result.get("audio_available"))
    audio_reused_existing = bool(audio_result.get("audio_reused_existing"))
    audio_required = bool(audio_result.get("audio_required"))
    force_audio_regenerate = bool(audio_result.get("force_audio_regenerate"))
    audio_mp3_path = audio_result.get("audio_mp3_path")
    audio_mp3_url = audio_result.get("audio_mp3_url")
    podcast_enclosure_present = bool(audio_result.get("podcast_enclosure_present"))
    existing_audio_mp3_path = audio_result.get("existing_audio_mp3_path")
    existing_audio_mp3_size = audio_result.get("existing_audio_mp3_size")
    audio_temp_path = audio_result.get("audio_temp_path")
    audio_replacement_performed = bool(audio_result.get("audio_replacement_performed"))
    audio_warnings = list(audio_result.get("warnings") or [])
    audio_errors = list(audio_result.get("errors") or [])
    ok = not audio_errors
    return {
        "ok": ok,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": date,
        "source_count": len(sources),
        "source_adequacy": adequacy,
        "lead_source_record_id": (lead_row or {}).get("source_record_id"),
        "lead_source_canonical_url": canonical_url(str((lead_row or {}).get("url") or "")) if lead_row else "",
        "previous_edition_date": previous_context.get("previous_edition_date"),
        "primary_signal_status": primary_signal_status,
        "primary_disqualification_reason": primary_disqualification_reason,
        "continuing_pressure_source_record_ids": continuing_source_record_ids,
        "continuing_pressure_source_urls": continuing_source_urls,
        "lead_issue_tags": lead_tags,
        "source_roles_count": role_counts,
        "local_signal_count": scope_counts["local_signal_count"],
        "state_signal_count": scope_counts["state_signal_count"],
        "national_context_count": scope_counts["national_context_count"],
        "editorial_status": editorial_status,
        "pressure_status": pressure_status,
        "why_this_lead": why_lead,
        "selected_lead_source_role": (lead_row or {}).get("source_role"),
        "selected_lead_pressure_type": (lead_row or {}).get("pressure_type"),
        "selected_lead_pressure_scope_label": _food_line_lead_pressure_scope_label(lead_row) if lead_row else None,
        "selected_lead_pressure_scope_text": _food_line_lead_pressure_scope_text(lead_row) if lead_row else None,
        "selected_lead_affected_groups": (lead_row or {}).get("affected_groups") or [],
        "pressure_source_count_by_family": pressure_source_count_by_family,
        "pressure_source_count_by_state": pressure_source_count_by_state,
        "news_item_count": news_item_count,
        "provider_pressure_count": provider_pressure_count,
        "official_pressure_count": official_pressure_count,
        "baseline_source_count": baseline_source_count,
        "rejected_news_count": rejected_news_count,
        "rejected_news_reasons": rejected_news_reasons,
        "rejected_news_by_source": rejected_news_by_source,
        "collected_source_count_by_source_id": collected_source_count_by_source_id,
        "pressure_verified_count": pressure_verified_count,
        "pressure_demoted_unverified_count": pressure_demoted_unverified_count,
        "pressure_registry_only_count": pressure_registry_only_count,
        "pressure_evidence_basis_counts": dict(sorted(pressure_evidence_basis_counts.items())),
        "collected_count_by_extraction_quality": dict(sorted(collected_count_by_extraction_quality.items())),
        "verified_pressure_count_by_extraction_quality": dict(sorted(verified_pressure_count_by_extraction_quality.items())),
        "demoted_count_by_extraction_quality": dict(sorted(demoted_count_by_extraction_quality.items())),
        "fetch_failure_count_by_source_id": dict(sorted(fetch_failure_count_by_source_id.items())),
        "no_evidence_count_by_source_id": dict(sorted(no_evidence_count_by_source_id.items())),
        "rejected_by_source_purpose_count": rejected_by_source_purpose_count,
        "demoted_by_source_purpose_count": demoted_by_source_purpose_count,
        "registry_source_purpose_refresh": registry_purpose_refresh,
        "collector_audit_path": collector_audit_path,
        "source_freshness_status": source_freshness_status,
        "stale_public_story_count": stale_public_story_count,
        "excluded_stale_source_count": excluded_stale_source_count,
        "freshness_window_days": FOOD_LINE_FRESHNESS_WINDOW_DAYS,
        "stale_source_ids": stale_source_ids,
        "food_line_publish_blocked_reason": food_line_publish_blocked_reason,
        "food_line_no_current_update_policy_status": no_current_update_policy.get("status"),
        "food_line_no_current_update_policy_allowed": bool(no_current_update_policy.get("allowed")),
        "food_line_no_current_update_policy_reasons": no_current_update_policy_reasons,
        "food_line_no_current_update_policy_metrics": no_current_update_policy.get("metrics") or {},
        "discovery_gap_check": discovery_gap_summary,
        "discovery_gap_likely_qualifying_count": int(discovery_gap_summary.get("likely_qualifying_count") or 0),
        "discovery_gap_unreviewed_likely_qualifying_count": int(discovery_gap_summary.get("unreviewed_likely_qualifying_count") or 0),
        "discovery_gap_warning": discovery_gap_summary.get("warning") or "",
        "discovery_gap_report_path": discovery_gap_summary.get("report_path"),
        "discovery_gap_report_markdown_path": discovery_gap_summary.get("report_markdown_path"),
        "discovery_expansion_audit_path": discovery_expansion_audit.get("discovery_audit_json_path"),
        "discovery_expansion_audit_markdown_path": discovery_expansion_audit.get("discovery_audit_md_path"),
        "discovery_expansion_candidate_path": discovery_expansion_audit.get("discovery_candidates_path"),
        "discovery_confidence": discovery_expansion_audit.get("discovery_confidence"),
        "discovery_confidence_reason": discovery_expansion_audit.get("discovery_confidence_reason"),
        "discovery_confidence_summary": discovery_expansion_audit.get("discovery_confidence_summary"),
        "discovery_expansion_used": bool(discovery_bridge_result.get("discovery_expansion_used")),
        "discovery_candidate_count": int(discovery_bridge_result.get("discovery_candidate_count") or 0),
        "discovery_qualified_candidate_count": int(discovery_bridge_result.get("discovery_qualified_candidate_count") or 0),
        "discovery_context_candidate_count": int(discovery_bridge_result.get("discovery_context_candidate_count") or 0),
        "discovery_blocked_candidate_count": int(discovery_bridge_result.get("discovery_blocked_candidate_count") or 0),
        "discovery_duplicate_count": int(discovery_bridge_result.get("discovery_duplicate_count") or 0),
        "discovery_candidates_intaked": int(discovery_bridge_result.get("discovery_candidates_intaked") or 0),
        "discovery_candidates_excluded": int(discovery_bridge_result.get("discovery_candidates_excluded") or 0),
        "discovery_candidates_manual_review_required": int(discovery_bridge_result.get("discovery_candidates_manual_review_required") or 0),
        "discovery_source_input_path": discovery_bridge_result.get("discovery_source_input_path"),
        "discovery_review_path": discovery_bridge_result.get("discovery_review_path"),
        "discovery_no_current_update": _food_line_discovery_no_current_update_metadata(edition_mode, discovery_bridge_result)[0],
        "discovery_no_current_update_state": discovery_bridge_result.get("discovery_no_current_update_state"),
        "discovery_no_current_update_reason": _food_line_discovery_no_current_update_metadata(edition_mode, discovery_bridge_result)[1],
        "pressure_review_path": str(pressure_review_path),
        "candidate_review_json_path": str(candidate_review_json_path),
        "candidate_review_html_path": str(candidate_review_html_path),
        "public_signal_count": public_signal_count,
        "pressure_signal_count": sum(1 for row in sources if bool(row.get("pressure_signal"))),
        "pressure_marker_count": sum(1 for row in sources if bool(row.get("pressure_signal")) and bool(row.get("map_eligible"))),
        "affected_group_counts": {group: sum(1 for row in sources if group in (row.get("affected_groups") or [])) for group in sorted({g for row in sources for g in (row.get("affected_groups") or [])})},
        "location_pressure_counts": {
            str(row.get("location_name")): int(
                sum(1 for s in sources if bool(s.get("pressure_signal")) and str(s.get("location_name")) == str(row.get("location_name")))
            )
            for row in sources
            if bool(row.get("pressure_signal"))
        },
        "baseline_record_count": sum(1 for row in sources if str(row.get("source_role")) == "baseline_condition"),
        "context_record_count": sum(1 for row in sources if str(row.get("source_role")) in {"resource_context", "policy_context", "baseline_condition"}),
        "excluded_context_count": sum(1 for row in sources if str(row.get("source_role")) in {"resource_context", "policy_context", "baseline_condition"}),
        "candidate_count_total": len(sources),
        "candidate_count_traceable": sum(1 for row in sources if str(row.get("traceability_status") or "") == "traceable"),
        "candidate_count_approved": sum(1 for row in sources if str(row.get("review_status") or "") == "approved"),
        "candidate_count_needs_review": sum(1 for row in sources if str(row.get("review_status") or "") == "needs_review"),
        "candidate_count_watchlist": sum(1 for row in sources if str(row.get("review_status") or "") == "watchlist"),
        "candidate_count_rejected": sum(1 for row in sources if str(row.get("review_status") or "") == "rejected"),
        "public_claim_eligible_count": int(classification_summary.get("public_claim_eligible_count") or 0),
        "public_claim_blocker_counts": dict(classification_summary.get("public_claim_blocker_counts") or {}),
        "intake_broadened": True,
        "public_rendered": public_rendered,
        "public_url": f"{BASE_URL}/food-line/editions/{date}/" if public_rendered else None,
        "qualified_but_not_public_count": qualified_but_not_public_count,
        "qualified_but_not_public_warning": qualified_but_not_public_warning,
        "future_date_blocked": future_date_blocked,
        "future_date_override_used": future_date_override_used,
        "edition_mode": edition_mode,
        **source_collection_audit_summary,
        "bluesky_post_text": manifest["bluesky_post_text"],
        "bluesky_post_ready": manifest["bluesky_post_ready"],
        "qualified_primary_count": qualified_primary_count,
        "continuing_pressure_count": continuing_pressure_count,
        "continuing_context_count": continuing_context_count,
        "excluded_count": excluded_count,
        "exclusion_reason_counts": exclusion_reason_counts,
        "exclusion_reason_summary": exclusion_reason_summary,
        "skip_reason": skip_reason,
        "rejected_source_records": rejected_records,
        "source_diagnostics": source_diagnostics,
        "collector_result": collect_result,
        "generated_output_path": str(root / "output" / "site" / DISPATCH_SLUG if public_rendered else review_dir),
        "diagnostics_output_path": str(diagnostics_dir),
        "diagnostics_manifest_path": str(diagnostics_dir / "run_manifest.json"),
        "audio_generated": audio_generated,
        "audio_available": audio_available,
        "audio_reused_existing": audio_reused_existing,
        "audio_required": audio_required,
        "force_audio_regenerate": bool(force_audio_regenerate),
        "audio_mp3_path": audio_mp3_path,
        "audio_mp3_url": audio_mp3_url,
        "podcast_enclosure_present": podcast_enclosure_present,
        "existing_audio_mp3_path": existing_audio_mp3_path,
        "existing_audio_mp3_size": existing_audio_mp3_size,
        "audio_temp_path": audio_temp_path,
        "audio_replacement_performed": audio_replacement_performed,
        "audio_status": audio_result.get("audio_status"),
        "audio_story_section_count": audio_result.get("audio_story_section_count"),
        "audio_story_sections": audio_result.get("audio_story_sections"),
        "audio_timeout_seconds": audio_timeout_seconds,
        "tts_provider": audio_result.get("tts_provider"),
        "tts_model_requested": audio_result.get("tts_model_requested"),
        "tts_voice_requested": audio_result.get("tts_voice_requested"),
        "tts_narration_char_count": audio_result.get("tts_narration_char_count"),
        "tts_output_path_attempted": audio_result.get("tts_output_path_attempted"),
        "tts_api_key_present": audio_result.get("tts_api_key_present"),
        "tts_output_dir_exists": audio_result.get("tts_output_dir_exists"),
        "tts_partial_mp3_exists": audio_result.get("tts_partial_mp3_exists"),
        "tts_elapsed_seconds": audio_result.get("tts_elapsed_seconds"),
        "tts_exception_type": audio_result.get("tts_exception_type"),
        "tts_exception_message_sanitized": audio_result.get("tts_exception_message_sanitized"),
        "tts_error_type": audio_result.get("tts_error_type"),
        "tts_error_message_sanitized": audio_result.get("tts_error_message_sanitized"),
        "tts_timeout_seconds": audio_result.get("tts_timeout_seconds"),
        "tts_audio_format": audio_result.get("tts_audio_format"),
        "tls_verify": audio_result.get("tls_verify"),
        "ca_file_used": audio_result.get("ca_file_used"),
        "ca_source": audio_result.get("ca_source"),
        "truststore_requested": audio_result.get("truststore_requested"),
        "truststore_available": audio_result.get("truststore_available"),
        "ssl_cert_file_env": audio_result.get("ssl_cert_file_env"),
        "requests_ca_bundle_env": audio_result.get("requests_ca_bundle_env"),
        "bluefern_tts_ca_file_env": audio_result.get("bluefern_tts_ca_file_env"),
        "tls_workaround_warning": audio_result.get("tls_workaround_warning"),
        "tts_file_write_exception_type": audio_result.get("tts_file_write_exception_type"),
        "tts_file_write_exception_message_sanitized": audio_result.get("tts_file_write_exception_message_sanitized"),
        "warnings": audio_warnings,
        "audio_warnings": audio_warnings,
        "audio_errors": audio_errors,
    }


def publish_food_line_pages(root: Path, date: str) -> tuple[bool, list[str], dict[str, Any]]:
    cmd = [
        sys.executable,
        "scripts\\publish_github_pages.py",
        "--pages-repo",
        str(PAGES_REPO),
        "--pages-branch",
        PAGES_BRANCH,
        "--expect-date",
        date,
        "--expect-dispatch",
        "food-line",
        "--only-dispatch",
        "food-line",
        "--commit",
        "--no-push",
    ]
    done = _run_cmd(cmd, cwd=root)
    if done.returncode != 0:
        return False, [done.stderr.strip() or done.stdout.strip() or "pages publish failed"], {}
    payload = _parse_json_stdout(done.stdout)
    errors = [str(item) for item in (payload.get("errors") or [])]
    return payload.get("ok") is True and not errors, errors, payload


def push_pages_repo() -> tuple[bool, str]:
    if not PAGES_REPO.exists():
        return False, f"pages repo does not exist: {PAGES_REPO}"
    pushed = _run_cmd(["git", "push", "origin", PAGES_BRANCH], cwd=PAGES_REPO)
    if pushed.returncode != 0:
        return False, pushed.stderr.strip() or pushed.stdout.strip() or "git push failed"
    return True, "live pages push completed"


def run_range(
    root: Path,
    start_date: str,
    end_date: str,
    *,
    collect: bool = False,
    use_discovery_candidates: bool = False,
    include_discovery_gap_summary: bool = False,
    allow_future_date: bool = False,
    generate_audio: bool = True,
    require_audio: bool = False,
    force_audio_regenerate: bool = False,
    tts_provider: str = "none",
    audio_model: str = "gpt-4o-mini-tts",
    audio_voice: str = "alloy",
    audio_format: str = "mp3",
    audio_timeout_seconds: float = 90.0,
    audit_source_collection: bool = False,
    gold_set_path: Path | None = None,
    dry_run_requested: bool = False,
    audit_allow_live_discovery: bool = False,
) -> list[dict[str, Any]]:
    start = datetime.strptime(validate_date(start_date), "%Y-%m-%d").date()
    end = datetime.strptime(validate_date(end_date), "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end date must be on or after start date")
    out: list[dict[str, Any]] = []
    day = start
    while day <= end:
        out.append(
            run_food_line_dispatch(
                root,
                day.isoformat(),
                collect=collect,
                use_discovery_candidates=use_discovery_candidates,
                include_discovery_gap_summary=include_discovery_gap_summary,
                generate_audio=generate_audio,
                require_audio=require_audio,
                force_audio_regenerate=force_audio_regenerate,
                tts_provider=tts_provider,
                audio_model=audio_model,
                audio_voice=audio_voice,
                audio_format=audio_format,
                audio_timeout_seconds=audio_timeout_seconds,
                allow_future_date=allow_future_date,
                audit_source_collection=audit_source_collection,
                gold_set_path=gold_set_path,
                dry_run_requested=dry_run_requested,
                audit_allow_live_discovery=audit_allow_live_discovery,
            )
        )
        day += timedelta(days=1)
    return out


def _summarize_range_results(runs: list[dict[str, Any]]) -> dict[str, Any]:
    failed_dates: list[str] = []
    errors: list[str] = []
    for run in runs:
        edition_date = str(run.get("edition_date") or "unknown-date")
        if run.get("ok"):
            continue
        failed_dates.append(edition_date)
        run_errors = run.get("errors")
        if isinstance(run_errors, list) and run_errors:
            errors.extend(f"{edition_date}: {str(error)}" for error in run_errors)
        else:
            errors.append(f"{edition_date}: run returned ok=false")
    result: dict[str, Any] = {
        "ok": not failed_dates,
        "runs": runs,
        "start_date": runs[0].get("edition_date") if runs else None,
        "end_date": runs[-1].get("edition_date") if runs else None,
        "run_count": len(runs),
        "failed_dates": failed_dates,
    }
    if errors:
        result["errors"] = errors
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Food Line Dispatch daily editions.")
    p.add_argument("--date", help="Edition date YYYY-MM-DD")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--publish", action="store_true", help="Copy Food Line output into local Pages repo and commit locally.")
    p.add_argument("--push", action="store_true", help="Push local Pages repo gh-pages after --publish succeeds.")
    p.add_argument("--collect", action="store_true", help="Collect auto sources into auto_sources.json before generation.")
    p.add_argument("--use-discovery-candidates", action="store_true", help="Bridge discovery_candidates.json into the daily Food Line intake path before generation.")
    p.add_argument(
        "--include-discovery-gap-summary",
        action="store_true",
        help="Include a Food Line discovery gap warning in the run summary when a same-date report exists.",
    )
    p.add_argument("--dry-run", action="store_true", help="Generate local Food Line output without copying to the Pages repo or pushing.")
    p.add_argument("--post-bluesky", action="store_true", help="Post the published Food Line dispatch to Bluesky after a successful publish.")
    p.add_argument("--skip-bluesky", action="store_true", help="Disable Bluesky posting for this run.")
    p.add_argument("--dry-run-bluesky", action="store_true", help="Write planned Food Line Bluesky post metadata without posting.")
    p.add_argument("--force-bluesky", action="store_true", help="Repost to Bluesky even when a receipt already exists for this edition.")
    p.add_argument("--allow-bluesky-text-only", action="store_true", help="Allow a Food Line Bluesky post to proceed without the social image if upload fails.")
    audio_group = p.add_mutually_exclusive_group()
    audio_group.add_argument(
        "--generate-audio",
        dest="generate_audio",
        action="store_true",
        help="Generate Food Line audio narration and podcast MP3 artifacts.",
    )
    audio_group.add_argument(
        "--no-generate-audio",
        dest="generate_audio",
        action="store_false",
        help="Skip Food Line audio narration and leave transcript-only output.",
    )
    p.set_defaults(generate_audio=True)
    p.add_argument("--require-audio", action="store_true", help="Require the Food Line audio MP3 to be generated before the run can succeed.")
    p.add_argument("--force-audio-regenerate", action="store_true", help="Regenerate Food Line audio even when an MP3 already exists; preserve the existing MP3 if regeneration fails.")
    p.add_argument("--allow-future-date", action="store_true", help="Allow public Food Line output for a future-dated edition.")
    p.add_argument("--audit-source-collection", action="store_true", help="Write a gold-set audit of Food Line source collection and review stages.")
    p.add_argument("--gold-set", help="Optional path to a Food Line source-collection gold-set JSON file.")
    p.add_argument(
        "--audit-live-discovery",
        action="store_true",
        help="Allow audit mode to run live discovery expansion when cached discovery artifacts are missing.",
    )
    p.add_argument("--tts-provider", choices=("none", "openai"), default="none", help="Optional TTS provider when --generate-audio is used.")
    p.add_argument("--audio-model", default="gpt-4o-mini-tts", help="TTS model for Food Line audio generation.")
    p.add_argument("--audio-voice", default="alloy", help="TTS voice for Food Line audio generation.")
    p.add_argument("--audio-format", choices=("mp3",), default="mp3", help="Audio format for Food Line audio generation.")
    p.add_argument("--audio-timeout-seconds", type=float, default=90.0, help="Timeout for Food Line TTS requests.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.push and not args.publish:
            raise ValueError("--push requires --publish")
        gold_set_path = Path(args.gold_set).resolve() if args.gold_set else None
        if args.audit_source_collection and gold_set_path is not None and not gold_set_path.exists():
            raise ValueError(f"gold set file not found: {gold_set_path}")
        if args.start_date or args.end_date:
            if not args.start_date or not args.end_date:
                raise ValueError("--start-date and --end-date are required together")
            runs = run_range(
                Path.cwd(),
                args.start_date,
                args.end_date,
                collect=bool(args.collect),
                use_discovery_candidates=bool(args.use_discovery_candidates),
                include_discovery_gap_summary=bool(args.include_discovery_gap_summary),
                allow_future_date=bool(args.allow_future_date),
                generate_audio=bool(args.generate_audio),
                require_audio=bool(args.require_audio),
                force_audio_regenerate=bool(args.force_audio_regenerate),
                tts_provider=str(args.tts_provider or "none"),
                audio_model=str(args.audio_model or "gpt-4o-mini-tts"),
                audio_voice=str(args.audio_voice or "alloy"),
                audio_format=str(args.audio_format or "mp3"),
                audio_timeout_seconds=float(args.audio_timeout_seconds or 90.0),
                audit_source_collection=bool(args.audit_source_collection),
                gold_set_path=gold_set_path,
                dry_run_requested=bool(args.dry_run),
                audit_allow_live_discovery=bool(args.audit_live_discovery),
            )
            result = _summarize_range_results(runs)
        else:
            if not args.date:
                raise ValueError("--date is required")
            result = run_food_line_dispatch(
                Path.cwd(),
                args.date,
                collect=bool(args.collect),
                use_discovery_candidates=bool(args.use_discovery_candidates),
                include_discovery_gap_summary=bool(args.include_discovery_gap_summary),
                generate_audio=bool(args.generate_audio),
                require_audio=bool(args.require_audio),
                force_audio_regenerate=bool(args.force_audio_regenerate),
                tts_provider=str(args.tts_provider or "none"),
                audio_model=str(args.audio_model or "gpt-4o-mini-tts"),
                audio_voice=str(args.audio_voice or "alloy"),
                audio_format=str(args.audio_format or "mp3"),
                audio_timeout_seconds=float(args.audio_timeout_seconds or 90.0),
                allow_future_date=bool(args.allow_future_date),
                audit_source_collection=bool(args.audit_source_collection),
                gold_set_path=gold_set_path,
                dry_run_requested=bool(args.dry_run),
                audit_allow_live_discovery=bool(args.audit_live_discovery),
            )
            result["pages_publish_path"] = str(PAGES_REPO)
            result["pages_publish_copied"] = False
            result["pushed"] = False
            if args.publish and result.get("ok") and not args.dry_run:
                if not bool(result.get("public_rendered")):
                    publish_skip_reason = str(result.get("edition_mode") or "not_public_rendered")
                    result["publish_status"] = publish_skip_reason
                    result["publish_skipped_reason"] = publish_skip_reason
                    result["pages_publish_skipped_reason"] = publish_skip_reason
                else:
                    ok, errors, publish_payload = publish_food_line_pages(Path.cwd(), args.date)
                    result["pages_publish_copied"] = ok
                    result["pages_publish_result"] = publish_payload
                    if not ok:
                        result["ok"] = False
                        result["errors"] = errors
                    elif args.push:
                        pushed, message = push_pages_repo()
                        result["pushed"] = pushed
                        if not pushed:
                            result["ok"] = False
                            result["errors"] = [message]
                        else:
                            result["push_message"] = message
            elif args.publish and args.dry_run:
                result["publish_skipped_reason"] = "dry_run"
            elif args.publish and not result.get("ok"):
                result["publish_skipped_reason"] = "generation failed"
            bluesky_requested = bool(args.post_bluesky or args.dry_run_bluesky)
            if args.post_bluesky and args.skip_bluesky:
                raise ValueError("--post-bluesky and --skip-bluesky cannot be used together")
            bluesky_result: dict[str, Any] = {
                "status": "skipped",
                "reason": "not_requested",
                "post_uri": None,
                "post_cid": None,
                "post_text": None,
                "card_title": None,
                "card_description": None,
                "image_path": None,
                "image_alt": None,
                "state_path": None,
                "embed_type": None,
                "thumb_status": "not_attempted",
            }
            if bluesky_requested and not args.skip_bluesky:
                if args.dry_run and args.dry_run_bluesky:
                    bluesky_result = maybe_post_food_line_dispatch_to_bluesky(
                        edition_date=args.date,
                        public_url=(result.get("public_url") or result.get("public_urls", {}).get("edition")),
                        post_text=result.get("bluesky_post_text"),
                        run_succeeded=bool(result.get("ok")),
                        public_rendered=bool(result.get("public_rendered")),
                        public_signal_count=int(result.get("public_signal_count") or 0),
                        post_requested=True,
                        project_root=Path.cwd(),
                        force_post=bool(args.force_bluesky),
                        allow_publish=False,
                        dry_run=True,
                        allow_text_only=bool(args.allow_bluesky_text_only),
                    )
                elif args.publish and bool(result.get("pages_publish_copied")) and bool(result.get("ok")):
                    bluesky_result = maybe_post_food_line_dispatch_to_bluesky(
                        edition_date=args.date,
                        public_url=(result.get("public_url") or result.get("public_urls", {}).get("edition")),
                        post_text=result.get("bluesky_post_text"),
                        run_succeeded=bool(result.get("ok") and result.get("pages_publish_copied")),
                        public_rendered=bool(result.get("public_rendered")),
                        public_signal_count=int(result.get("public_signal_count") or 0),
                        post_requested=True,
                        project_root=Path.cwd(),
                        force_post=bool(args.force_bluesky),
                        allow_publish=True,
                        dry_run=False,
                        allow_text_only=bool(args.allow_bluesky_text_only),
                    )
                elif args.dry_run:
                    bluesky_result = {
                        "status": "skipped",
                        "reason": "dry_run",
                        "post_uri": None,
                        "post_cid": None,
                        "post_text": result.get("bluesky_post_text"),
                        "card_title": None,
                        "card_description": None,
                        "image_path": None,
                        "image_alt": None,
                        "state_path": None,
                        "embed_type": None,
                        "thumb_status": "not_attempted",
                    }
                elif args.publish:
                    bluesky_result = {
                        "status": "skipped",
                        "reason": "publish_not_ready",
                        "post_uri": None,
                        "post_cid": None,
                        "post_text": result.get("bluesky_post_text"),
                        "card_title": None,
                        "card_description": None,
                        "image_path": None,
                        "image_alt": None,
                        "state_path": None,
                        "embed_type": None,
                        "thumb_status": "not_attempted",
                    }
                else:
                    bluesky_result = {
                        "status": "skipped",
                        "reason": "not_published",
                        "post_uri": None,
                        "post_cid": None,
                        "post_text": result.get("bluesky_post_text"),
                        "card_title": None,
                        "card_description": None,
                        "image_path": None,
                        "image_alt": None,
                        "state_path": None,
                        "embed_type": None,
                        "thumb_status": "not_attempted",
                    }
            result["bluesky_status"] = bluesky_result.get("status")
            result["bluesky_reason"] = bluesky_result.get("reason")
            result["bluesky_post_uri"] = bluesky_result.get("post_uri")
            result["bluesky_post_cid"] = bluesky_result.get("post_cid")
            result["bluesky_post_text"] = bluesky_result.get("post_text")
            result["bluesky_embed_type"] = bluesky_result.get("embed_type")
            result["bluesky_card_title"] = bluesky_result.get("card_title")
            result["bluesky_card_description"] = bluesky_result.get("card_description")
            result["bluesky_image_path"] = bluesky_result.get("image_path")
            result["bluesky_image_alt"] = bluesky_result.get("image_alt")
            result["bluesky_state_path"] = bluesky_result.get("state_path")
            result["bluesky_thumb_status"] = bluesky_result.get("thumb_status")
            result["bluesky_dry_run"] = bool(args.dry_run or args.dry_run_bluesky)
        result["push_requested"] = bool(args.push)
        result["publish_requested"] = bool(args.publish)
        result["dry_run_requested"] = bool(args.dry_run)
        result["bluesky_requested"] = bool(args.post_bluesky or args.dry_run_bluesky)
        result["bluesky_force_requested"] = bool(args.force_bluesky)
        result["bluesky_skip_requested"] = bool(args.skip_bluesky)
        result["bluesky_allow_text_only"] = bool(args.allow_bluesky_text_only)
        result["recommended_schedule_command"] = (
            "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "
            "\"Set-Location 'C:\\PythonProjects\\Dispatches From The Blue Fern Co'; & '.\\.venv\\Scripts\\python.exe' "
            "'scripts\\run_food_line_dispatch.py' --date (Get-Date -Format 'yyyy-MM-dd') --publish --push --post-bluesky\""
        )
        result["git_push_occurred"] = bool(result.get("pushed"))
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
