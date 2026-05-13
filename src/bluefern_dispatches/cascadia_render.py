from __future__ import annotations

import csv
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_curate import why_it_matters
from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import format_coverage_label, week_label
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
MAP_NOTE = (
    "Map shows source-backed public signals by state/source geography; markers may represent source or regional "
    "centroids, not exact incident locations."
)
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
            return payload
    except Exception:
        pass
    loaded = parse_simple_yaml_map(text)
    payload["state_centroids"].update(loaded.get("state_centroids") or {})
    payload["source_defaults"].update(loaded.get("source_defaults") or {})
    return payload


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_marker_coordinates(source: dict[str, Any], map_locations: dict[str, Any]) -> tuple[float | None, float | None, str]:
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
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    by_source_record_id = {
        str(source.get("source_record_id")): source
        for source in sources_manifest
        if isinstance(source, dict) and source.get("source_record_id")
    }
    markers: list[dict[str, Any]] = []
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
        source_url = str(source.get("url") or source.get("source_url") or "").strip()
        if not source_url or not source_url.startswith(("http://", "https://")):
            warnings.append(f"map skipped story missing source URL: {story.get('story_id')}")
            continue
        region = str(source.get("state_hint") or source.get("region_scope") or source.get("geography") or "").strip()
        if not region:
            warnings.append(f"map skipped story missing state/region: {story.get('story_id')}")
            continue
        lat, lon, coordinate_basis = resolve_marker_coordinates(source, map_locations)
        if lat is None or lon is None:
            warnings.append(f"map skipped story missing coordinate fallback: {story.get('story_id')}")
            continue
        markers.append(
            {
                "story_id": story.get("story_id"),
                "title": story.get("title"),
                "category": story.get("category"),
                "state_or_region": region,
                "publisher": source.get("publisher"),
                "published_at": source.get("published_at"),
                "source_url": source_url,
                "source_record_id": source.get("source_record_id"),
                "lat": lat,
                "lon": lon,
                "coordinate_basis": coordinate_basis,
            }
        )
    return markers, warnings


def render_map_html(edition_date: str, note: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cascadia Weekly Signal Map - {html.escape(edition_date)}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .map-note {{ font: 14px/1.4 Arial, sans-serif; margin: 8px; }}
  </style>
</head>
<body>
  <div class="map-note">{html.escape(note)}</div>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const map = L.map('map').setView([45.8, -120.5], 5);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);
    fetch('./map_data.json').then((r) => r.json()).then((payload) => {{
      const markers = payload.markers || [];
      const bounds = [];
      for (const item of markers) {{
        const popup = `<strong>${{item.title}}</strong><br>` +
          `Category: ${{item.category}}<br>` +
          `State/region: ${{item.state_or_region}}<br>` +
          `Publisher: ${{item.publisher || 'Unknown'}}<br>` +
          `Published: ${{item.published_at || 'Unknown'}}<br>` +
          `<a href="${{item.source_url}}" target="_blank" rel="noopener noreferrer">Source link</a>`;
        const marker = L.marker([item.lat, item.lon]).addTo(map).bindPopup(popup);
        bounds.push([item.lat, item.lon]);
      }}
      if (bounds.length) {{
        map.fitBounds(bounds, {{padding: [24, 24]}});
      }}
    }});
  </script>
</body>
</html>
"""


def render_map_embed_html() -> str:
    return (
        "<section class=\"cascadia-map\">"
        "<h2>Weekly Signal Map</h2>"
        f"<p>{html.escape(MAP_NOTE)}</p>"
        "<iframe title=\"Cascadia weekly signal map\" src=\"map.html\" loading=\"lazy\" "
        "referrerpolicy=\"no-referrer\" style=\"width:100%;height:420px;border:1px solid #cfd8de;\"></iframe>"
        "<p><a href=\"map.html\" target=\"_blank\" rel=\"noopener noreferrer\">Open map in a new tab</a></p>"
        "</section>"
    )


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
    map_markers, map_warnings = build_cascadia_map_markers(public_curation, sources_manifest, map_locations)
    warnings.extend(map_warnings)
    map_data = {"edition_date": edition_date, "note": MAP_NOTE, "markers": map_markers}
    map_html = render_map_html(edition_date, MAP_NOTE)
    map_embed_html = render_map_embed_html()
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
        "run_date": run_date or edition_date,
        "edition_date": edition_date,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "coverage_label": coverage_label,
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

