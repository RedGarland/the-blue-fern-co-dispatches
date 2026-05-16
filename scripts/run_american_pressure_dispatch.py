from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.american_pressure_sources import load_source_registry  # noqa: E402
from bluefern_dispatches.generator import (  # noqa: E402
    BASE_URL,
    DispatchConfig,
    footer,
    header,
    page,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
)


DISPATCH_SLUG = "american-pressure"
DISPATCH_NAME = "The American Pressure Dispatch"
DISPATCH_TAGLINE = "Weekly source-backed household pressure briefing"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE_MODES = {"manual", "auto", "both"}
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


def init_manual_sources_file(root: Path, edition_date: str, *, dry_run: bool, wrote: list[str]) -> Path:
    path = manual_source_path(root, edition_date)
    if path.exists():
        return path
    payload = {"sources": [], "_guidance": "Add source-backed records to sources[]."}
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
        title = str(record["title"]).strip()
        summary = str(record["summary_or_snippet"]).strip()
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
                "publisher": str(record["publisher"]).strip(),
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
    return summary


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


def _item_sort_key(story: dict[str, Any]) -> tuple[int, int]:
    item_type = str(story.get("item_type") or "baseline_gauge")
    rank = {"current_week_development": 0, "baseline_gauge": 1, "watchlist_item": 2}.get(item_type, 3)
    return (rank, int(story.get("score") or 0) * -1)


def curate_stories(sources: list[dict[str, Any]], edition_date: str, generated_at: str) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        pillar = str(source.get("pillar") or "local_system_strain")
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
        stories.append(
            {
                "story_id": f"american-pressure-story-{edition_date}-{index:03d}",
                "title": source["title"],
                "summary": source["summary_or_snippet"],
                "category": CATEGORY_BY_PILLAR.get(str(source.get("pillar") or ""), "local-system-strain"),
                "pillar": pillar,
                "curation_reason": curation_reason,
                "item_type": _classify_item_type(source),
                "score": 100 - index,
                "included_in_public_summary": True,
                "source_record_ids": [source["source_record_id"]],
                "source_urls": [source["url"]],
                "publisher_names": [source["publisher"]],
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


def render_edition_html(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]], source_mode: str) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    pillars_present, pillars_missing, _, _ = _coverage(stories, sources)
    chunks: list[str] = ["<h1>The American Pressure Dispatch</h1>", f"<p class=\"eyebrow\">Weekly briefing / {edition_date}</p>"]
    chunks.append("<h2>This Week’s Read</h2>")
    chunks.append(
        "<p>American Pressure tracks how everyday life pressure is moving for households across groceries, bills, jobs, health care, debt, and local services. "
        "Official baselines are the evidence layer, not the headline layer.</p>"
    )
    chunks.append("<h2>What This Means</h2>")
    chunks.append(
        "<p>This edition does not point to one single national emergency. It shows several pressure gauges moving across food, debt, housing, health coverage, work, and local disruption. "
        "The purpose is to track whether ordinary households are getting more room to breathe or less.</p>"
    )
    chunks.append("<h2>What Feels Tight</h2>")
    chunks.append(
        f"<p>{len(stories)} source-backed signals are active across {len(pillars_present)} pillars. "
        "Read these as gauges of where pressure is showing up, who may be getting squeezed, and what to watch next.</p>"
    )
    chunks.append("<h2>What Changed</h2>")
    chunks.append("<p>Current-week developments are listed first in each section, followed by baseline gauges for context.</p>")

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
            source = source_by_id[story["source_record_ids"][0]]
            item_type = str(story.get("item_type") or "baseline_gauge")
            chunks.append(f"<article><h3>{html.escape(story['title'])}</h3>")
            chunks.append(f"<p><strong>Type:</strong> {html.escape(item_type)}</p>")
            chunks.append(f"<p><strong>What happened:</strong> {html.escape(_reader_facing_summary(source))}</p>")
            chunks.append(f"<p><strong>Why it matters:</strong> {html.escape(guide['why_it_matters'])}</p>")
            chunks.append(f"<p><strong>Who may feel it:</strong> {html.escape(guide['who_may_feel_it'])}</p>")
            chunks.append(f"<p><strong>What to watch next:</strong> {html.escape(guide['watch_next'])}</p>")
            chunks.append(f'<p><em>Source: <a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a> ({html.escape(source["publisher"])})</em></p></article>')

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


def render_edition_markdown(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    lines = [f"# {DISPATCH_NAME}", "", f"Weekly briefing / {edition_date}", ""]
    for story in stories:
        source = source_by_id[story["source_record_ids"][0]]
        lines.append(f"## {story['title']}")
        lines.append(story["summary"])
        lines.append(f"Source: [{source['title']}]({source['url']}) ({source['publisher']}, {source['published_at']})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def discover_edition_dates(site_root: Path) -> list[str]:
    editions_root = site_root / DISPATCH_SLUG / "editions"
    if not editions_root.exists():
        return []
    return sorted((path.name for path in editions_root.iterdir() if path.is_dir() and DATE_RE.match(path.name) and (path / "index.html").exists()), reverse=True)


def render_archive_index_rss(root: Path, edition_date: str, dry_run: bool, wrote: list[str]) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(slug=DISPATCH_SLUG, name=DISPATCH_NAME, edition_date=edition_date, tagline=DISPATCH_TAGLINE, logo="american-pressure-logo.png", sources=[], stories=[], detail_artifacts=[])
    dates = discover_edition_dates(site_root)
    if edition_date not in dates:
        dates = sorted([*dates, edition_date], reverse=True)
    dispatch_root = site_root / DISPATCH_SLUG
    write_text(dispatch_root / "index.html", render_dispatch_index_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "archive.html", render_archive_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "rss.xml", render_rss_for_dates(dispatch, dates), dry_run, wrote)


def run_american_pressure_dispatch(root: Path, edition_date: str, *, publish: bool, dry_run: bool, from_manual_sources: bool, source_mode: str = "both", init_manual_sources: bool = False, allow_future: bool = False) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    validate_not_future_date(edition_date, allow_future=allow_future)
    mode = source_mode.strip().lower()
    if mode not in SOURCE_MODES:
        raise ValueError(f"unsupported --source-mode: {source_mode}")

    wrote: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    if init_manual_sources:
        path = init_manual_sources_file(root, edition_date, dry_run=dry_run, wrote=wrote)
        return {"ok": True, "dispatch_slug": DISPATCH_SLUG, "edition_date": edition_date, "manual_source_path": str(path), "source_count": 0, "story_count": 0, "generated": False, "initialized_manual_sources": True, "archive_updated": False, "rss_updated": False, "pages_repo_updated": False, "pushed": False, "wrote": wrote, "warnings": warnings, "errors": errors}

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
    if mode in {"auto", "both"}:
        raw_records.extend(load_auto_sources(root, edition_date))
    if from_manual_sources and mode == "auto":
        warnings.append("--from-manual-sources ignored when --source-mode auto")

    generated_at = utc_now()
    sources, source_warnings, source_errors, diagnostics = normalize_sources(raw_records, edition_date)
    warnings.extend(source_warnings)
    errors.extend(source_errors)

    stories = curate_stories(sources, edition_date, generated_at)
    pillars_present, pillars_missing, source_count_by_pillar, story_count_by_pillar = _coverage(stories, sources)
    item_type_counts = _item_type_counts(stories)

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

    html_content = render_edition_html(edition_date, stories, sources, mode)
    markdown_content = render_edition_markdown(edition_date, stories, sources)
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
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
        "warnings": warnings,
        "errors": errors,
    }

    curation_manifest = {
        "stories": stories,
        "pillars_present": pillars_present,
        "pillars_missing": pillars_missing,
        "source_count_by_pillar": source_count_by_pillar,
        "story_count_by_pillar": story_count_by_pillar,
        "rejected_no_public_pressure_angle": diagnostics["rejected_no_public_pressure_angle"],
        "rejected_investor_only": diagnostics["rejected_investor_only"],
        "rejected_duplicate_or_stale": diagnostics["rejected_duplicate_or_stale"],
        "rejected_missing_required_fields": diagnostics["rejected_missing_required_fields"],
        "item_type_counts": item_type_counts,
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
    write_json(site_dir / "sources_manifest.json", sources, dry_run, wrote)
    write_json(site_dir / "curation_manifest.json", curation_manifest, dry_run, wrote)

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
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly American Pressure editions.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--publish", action="store_true", help="Update public index/archive/rss after edition generation.")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without changing files.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Legacy flag; manual mode now controlled by --source-mode.")
    parser.add_argument("--source-mode", choices=sorted(SOURCE_MODES), default="both", help="Source input mode: manual, auto, or both.")
    parser.add_argument("--init-manual-sources", action="store_true", help="Create starter manual source file for --date when missing.")
    parser.add_argument("--allow-future", action="store_true", help="Allow future --date values (disabled by default).")
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
            allow_future=bool(args.allow_future),
        )
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
