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
    "job",
    "jobs",
    "layoff",
    "layoffs",
    "worker",
    "workers",
    "hospital",
    "healthcare",
    "clinic",
    "food",
    "supplier",
    "grocery",
    "housing",
    "rent",
    "mortgage",
    "utility",
    "utilities",
    "rural",
    "household",
    "consumer",
    "county",
    "district",
    "service",
    "services",
    "employer",
    "employment",
}
INVESTOR_ONLY_KEYWORDS = {
    "shareholder",
    "bondholder",
    "equity holder",
    "equityholders",
    "investor presentation",
    "capital structure optimization",
    "eps guidance",
}


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


def load_manual_sources(root: Path, edition_date: str, from_manual_sources: bool) -> tuple[Path, list[dict[str, Any]]]:
    if not from_manual_sources:
        raise ValueError("American Pressure generation currently requires --from-manual-sources")
    path = manual_source_path(root, edition_date)
    if not path.exists():
        raise FileNotFoundError(f"manual source file is required: {path}")
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manual_sources.json must be a list or an object with a sources list")
    return path, [record for record in records if isinstance(record, dict)]


def normalize_sources(records: list[dict[str, Any]], edition_date: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
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
    }
    for index, record in enumerate(records, start=1):
        missing = sorted(field for field in REQUIRED_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
            continue
        diagnostics["candidates_found"] += 1
        source_record_id = str(record.get("source_record_id") or "").strip()
        source_id = str(record.get("source_id") or source_record_id).strip()
        if not source_id:
            errors.append(f"source record {index} missing required source_id/source_record_id")
            continue
        if source_id in seen_ids:
            diagnostics["rejected_duplicate_or_stale"] += 1
            continue
        seen_ids.add(source_id)
        url = str(record.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
            continue
        region_scope = str(record.get("region_scope") or record.get("geography") or "").strip()
        category_hint = str(record.get("category_hint") or record.get("pillar") or "").strip()
        if not region_scope:
            errors.append(f"source record {index} missing region_scope/geography")
            continue
        if not category_hint:
            errors.append(f"source record {index} missing category_hint/pillar")
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
        is_bankruptcy_story = any(
            term in combined_text
            for term in ("bankrupt", "bankruptcy", "chapter 11", "chapter 7", "chapter 13", "insolvency")
        )
        has_public_pressure_angle = any(term in combined_text for term in PUBLIC_PRESSURE_KEYWORDS) or (
            "debt" in combined_text and any(term in combined_text for term in ("household", "consumer"))
        )
        if is_bankruptcy_story and not has_public_pressure_angle and "uscourts.gov" not in host:
            diagnostics["rejected_no_public_pressure_angle"] += 1
            continue
        pillar = str(record.get("pillar") or "").strip()
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
            }
        )
    warnings.append(f"bankruptcy_diagnostics={json.dumps(diagnostics, sort_keys=True)}")
    return normalized, warnings, errors


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
    if "rural" in text:
        return "local_service_disruption_bankruptcy", "rural_services"
    if any(t in text for t in ("chapter 13", "household repayment", "consumer filing")):
        return "chapter_13_household_repayment", "consumer"
    if any(t in text for t in ("consumer bankruptcy", "household debt")):
        return "consumer_bankruptcy_pressure", "consumer"
    if any(t in text for t in ("small business", "main street")):
        return "small_business_distress", "small_business"
    if "chapter 7" in text:
        return "chapter_7_liquidation", "business"
    if "chapter 11" in text:
        return "chapter_11_restructuring", "business"
    if any(t in text for t in ("county", "district filings", "rate")):
        return "county_bankruptcy_rate", "consumer"
    if "business" in text or "corporate" in text:
        return "business_bankruptcy_pressure", "business"
    return "bankruptcy_filings", "consumer"


def _category_to_section(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if "food" in normalized:
        return "food-pressure"
    if "health" in normalized:
        return "health-access-pressure"
    if "household" in normalized or "cost" in normalized:
        return "household-cost-pressure"
    if "environment" in normalized:
        return "environmental-pressure"
    if "policy" in normalized:
        return "policy-implementation"
    if "financial" in normalized or "distress" in normalized or "bankrupt" in normalized:
        return "financial-distress-pressure"
    return "local-system-strain"


def curate_stories(sources: list[dict[str, Any]], edition_date: str, generated_at: str) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        stories.append(
            {
                "story_id": f"american-pressure-story-{edition_date}-{index:03d}",
                "title": source["title"],
                "summary": source["summary_or_snippet"],
                "category": _category_to_section(str(source.get("pillar") or source["category_hint"])),
                "score": 100 - index,
                "scoring_reasons": ["Included because a complete project-local manual source record was provided."],
                "included_in_public_summary": True,
                "included_in_detail_dataset": False,
                "source_record_ids": [source["source_record_id"]],
                "source_ids": [source["source_record_id"]],
                "source_urls": [source["url"]],
                "publisher_names": [source["publisher"]],
                "generated_at": generated_at,
            }
        )
    return stories


def render_edition_html(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    chunks: list[str] = ["<h1>The American Pressure Dispatch</h1>", f"<p class=\"eyebrow\">Weekly briefing / {edition_date}</p>"]
    chunks.append("<h2>Top Signal</h2>")
    if stories:
        top = stories[0]
        top_source = source_by_id[top["source_record_ids"][0]]
        chunks.append(f"<p>{html.escape(top['summary'])}</p>")
        chunks.append(
            f'<p><em>Source: <a href="{html.escape(top_source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(top_source["title"])}</a> '
            f'({html.escape(top_source["publisher"])}, {html.escape(top_source["published_at"])})</em></p>'
        )
    else:
        chunks.append("<p>No source-backed signal in this edition.</p>")
    section_titles = {
        "food-pressure": "Food Pressure",
        "health-access-pressure": "Health Access Pressure",
        "household-cost-pressure": "Household Cost Pressure",
        "environmental-pressure": "Environmental Pressure",
        "financial-distress-pressure": "Financial Distress",
        "policy-implementation": "Policy Implementation",
        "local-system-strain": "Local System Strain",
    }
    stories_by_category: dict[str, list[dict[str, Any]]] = {}
    for story in stories:
        stories_by_category.setdefault(story["category"], []).append(story)
    chunks.append("<h2>Stories</h2>")
    for category in (
        "food-pressure",
        "health-access-pressure",
        "household-cost-pressure",
        "environmental-pressure",
        "financial-distress-pressure",
        "policy-implementation",
        "local-system-strain",
    ):
        items = stories_by_category.get(category, [])
        chunks.append(f"<h3>{html.escape(section_titles[category])}</h3>")
        if not items:
            chunks.append("<p>No source-backed signal in this edition.</p>")
            continue
        for story in items:
            source = source_by_id[story["source_record_ids"][0]]
            chunks.append(f"<article><h4>{html.escape(story['title'])}</h4>")
            chunks.append(f"<p>{html.escape(story['summary'])}</p>")
            if source.get("is_official_filings_data"):
                chunks.append("<p><strong>Official filings data:</strong></p>")
            chunks.append(
                f'<p><em>Source: <a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(source["title"])}</a> '
                f'({html.escape(source["publisher"])})</em></p></article>'
            )
    chunks.append("<h2>Source Note</h2>")
    chunks.append("<p>This edition is generated only from project-local manual source records for the requested date.</p>")
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/american-pressure/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/american-pressure-logo.png" alt="The American Pressure Dispatch">
    </section>
    {' '.join(chunks)}
  </main>
{footer("../../")}"""
    return page(
        f"{DISPATCH_NAME} - {edition_date}",
        f"{BASE_URL}/american-pressure/editions/{edition_date}/",
        "../../assets/site.css",
        body,
        DISPATCH_NAME,
    )


def render_edition_markdown(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}
    lines = [f"# {DISPATCH_NAME}", f"", f"Weekly briefing / {edition_date}", ""]
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
    return sorted(
        (
            path.name
            for path in editions_root.iterdir()
            if path.is_dir() and DATE_RE.match(path.name) and (path / "index.html").exists()
        ),
        reverse=True,
    )


def render_archive_index_rss(root: Path, edition_date: str, dry_run: bool, wrote: list[str]) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(
        slug=DISPATCH_SLUG,
        name=DISPATCH_NAME,
        edition_date=edition_date,
        tagline=DISPATCH_TAGLINE,
        logo="american-pressure-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )
    dates = discover_edition_dates(site_root)
    if edition_date not in dates:
        dates = sorted([*dates, edition_date], reverse=True)
    dispatch_root = site_root / DISPATCH_SLUG
    write_text(dispatch_root / "index.html", render_dispatch_index_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "archive.html", render_archive_for_dates(dispatch, dates), dry_run, wrote)
    write_text(dispatch_root / "rss.xml", render_rss_for_dates(dispatch, dates), dry_run, wrote)


def run_american_pressure_dispatch(
    root: Path,
    edition_date: str,
    *,
    publish: bool,
    dry_run: bool,
    from_manual_sources: bool,
) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    wrote: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    generated_at = utc_now()
    manual_path, raw_records = load_manual_sources(root, edition_date, from_manual_sources=from_manual_sources)
    sources, source_warnings, source_errors = normalize_sources(raw_records, edition_date)
    warnings.extend(source_warnings)
    errors.extend(source_errors)
    if not sources:
        errors.append(f"No valid source-backed American Pressure records found for {edition_date}; refusing to publish empty or repeated edition.")
        return {
            "ok": False,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_date": edition_date,
            "manual_source_path": str(manual_path),
            "source_count": 0,
            "story_count": 0,
            "generated": False,
            "archive_updated": False,
            "rss_updated": False,
            "pages_repo_updated": False,
            "pushed": False,
            "wrote": wrote,
            "warnings": warnings,
            "errors": errors,
            "live_fetch_enabled": False,
            "registry_used_for_public_claims": False,
            "todo_dedupe_hook": "TODO: add canonical URL/publisher-title/claim-fingerprint dedupe; never treat retrieved_at as freshness.",
        }
    stories = curate_stories(sources, edition_date, generated_at)
    html_content = render_edition_html(edition_date, stories, sources)
    markdown_content = render_edition_markdown(edition_date, stories, sources)
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/american-pressure/editions/{edition_date}/",
        "source_count": len(sources),
        "story_count": len(stories),
        "is_free_public": True,
        "public_exposed": True,
        "has_detail_tier": False,
        "warnings": warnings,
        "errors": errors,
    }
    curation_manifest = stories
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
        "ok": not errors,
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
        "live_fetch_enabled": False,
        "registry_used_for_public_claims": False,
        "todo_dedupe_hook": "TODO: add canonical URL/publisher-title/claim-fingerprint dedupe; never treat retrieved_at as freshness.",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly manual-source American Pressure editions.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--publish", action="store_true", help="Update American Pressure public index/archive/rss after edition generation.")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without changing files.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Require project-local manual source records.")
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
        )
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
