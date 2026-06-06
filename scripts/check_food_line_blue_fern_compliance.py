from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_food_line_dispatch import DISPATCH_DISPLAY_NAME, DISPATCH_NAME, DISPATCH_SLUG, FOOD_LINE_LOGO_ASSET  # noqa: E402

BLUE_FERN_COLORS = ("#1E3F4F", "#EFE7DA", "#4E6B79")
RESOURCE_DIRECTORY_PHRASES = (
    "find food",
    "resources near you",
    "food resource directory",
    "food pantry directory",
)
REQUIRED_SOURCE_TABLE_HEADERS = (
    "Record ID",
    "Title",
    "Publisher",
    "Source link",
    "Source family",
    "How it was used",
    "Issue",
    "What happened",
    "What the source says",
    "Verification status",
    "Who may be affected",
    "Used on public page",
)
REQUIRED_POPUP_FIELDS = (
    "Included in briefing:",
    "What happened:",
    "Record ID:",
    "Publisher:",
    "Source family:",
    "How it was used:",
    "What the source says:",
    "Issue:",
    "Evidence level:",
    "Freshness role:",
    "Who may be affected:",
    "Title:",
    "Source URL:",
    "Verification status:",
    "Dispatch date:",
    "Coordinate basis:",
)
PUBLIC_CHROME_PHRASES = (
    "Skip to content",
    "Advertise With Us",
    "Teacher Tribute",
    "Health Update",
    "Aging Untold",
    "Local News Video",
    "Extra Community",
    "We the People",
    "Watch Live",
    "Weather Extra",
    "Sports",
    "Contests",
    "Closings & Delays",
    "Reception Issues",
    "About Us",
    "Election Results",
    "Pet Project",
    "Watch East Texas Now",
    "Watch Newscasts",
    "Big Red Box",
    "See it, Snap it, Send it",
)
PUBLIC_BRIEFING_DEBUG_PHRASES = (
    "matched terms",
    "Review summary:",
    "source_text_verified",
    "verification status",
    "evidence level",
    "source_role",
    "pressure_match_terms",
    "the verified record came from",
)
ARCHIVE_EDITION_LINK_RE = re.compile(r"editions/(\d{4}-\d{2}-\d{2})/?(?:index\.html)?", re.IGNORECASE)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _report_dir(root: Path, date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / date


def _html_header_values(html_text: str) -> list[str]:
    return re.findall(r"<th[^>]*>(.*?)</th>", html_text, flags=re.IGNORECASE | re.DOTALL)


def _count_phrase(text: str, phrase: str) -> int:
    return text.lower().count(phrase.lower())


def _contains_public_chrome(text: str) -> list[str]:
    return [phrase for phrase in PUBLIC_CHROME_PHRASES if phrase.lower() in text.lower()]


def _snippet_for_phrase(text: str, phrase: str, *, window: int = 90) -> str:
    index = text.lower().find(phrase.lower())
    if index == -1:
        return ""
    start = max(0, index - window)
    end = min(len(text), index + len(phrase) + window)
    snippet = " ".join(text[start:end].split())
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _linked_food_line_edition_dates(site_root: Path) -> list[str]:
    archive_html = _read_text(site_root / "archive.html")
    linked_dates: list[str] = []
    for match in ARCHIVE_EDITION_LINK_RE.finditer(archive_html):
        edition_date = match.group(1)
        if edition_date not in linked_dates:
            linked_dates.append(edition_date)
    return linked_dates


def _public_food_line_pages(root: Path, date: str | None = None) -> tuple[list[Path], list[str]]:
    site_root = root / "output" / "site" / DISPATCH_SLUG
    linked_dates = _linked_food_line_edition_dates(site_root)
    if date and date not in linked_dates:
        linked_dates.append(date)
    pages = [
        site_root / "index.html",
        site_root / "archive.html",
        site_root / "map" / "index.html",
        site_root / "audio" / "index.html",
        site_root / "audio" / "podcast.xml",
    ]
    for edition_date in linked_dates:
        pages.append(site_root / "editions" / edition_date / "index.html")
        pages.append(site_root / "editions" / edition_date / "source_table.html")
        pages.append(site_root / "audio" / f"{edition_date}-transcript.html")
    return pages, linked_dates


def _public_food_line_audio_pages(pages: list[Path]) -> list[Path]:
    return [
        path
        for path in pages
        if path.parent.name == "audio" and (path.name == "index.html" or path.name == "podcast.xml" or path.name.endswith("-transcript.html"))
    ]


def _audit_logo_checks(root: Path, date: str) -> tuple[dict[str, Any], list[str], list[str]]:
    site_root = root / "output" / "site" / DISPATCH_SLUG
    assets_root = site_root / "assets"
    edition_dir = site_root / "editions" / date
    index_html = _read_text(site_root / "index.html")
    archive_html = _read_text(site_root / "archive.html")
    edition_html = _read_text(edition_dir / "index.html")
    source_table_html = _read_text(edition_dir / "source_table.html")
    map_html = _read_text(site_root / "map" / "index.html")
    audio_index = _read_text(site_root / "audio" / "index.html")
    podcast_xml = _read_text(site_root / "audio" / "podcast.xml")
    podcast_artwork = site_root / "audio" / "podcast-artwork.png"
    checked = [
        site_root / "index.html",
        site_root / "archive.html",
        site_root / "map" / "index.html",
        site_root / "audio" / "index.html",
        edition_dir / "index.html",
        edition_dir / "source_table.html",
        assets_root / FOOD_LINE_LOGO_ASSET,
    ]
    if (site_root / "audio" / "podcast.xml").exists():
        checked.append(site_root / "audio" / "podcast.xml")
    if (site_root / "audio" / "podcast-artwork.png").exists():
        checked.append(site_root / "audio" / "podcast-artwork.png")
    if edition_html:
        checked.append(edition_dir / "index.html")
    logo_asset_exists = (assets_root / FOOD_LINE_LOGO_ASSET).exists()
    generated_logo_exists = logo_asset_exists
    landing_ref = FOOD_LINE_LOGO_ASSET in index_html
    archive_ref = FOOD_LINE_LOGO_ASSET in archive_html
    edition_ref = FOOD_LINE_LOGO_ASSET in edition_html
    source_table_ref = FOOD_LINE_LOGO_ASSET in source_table_html
    map_ref = FOOD_LINE_LOGO_ASSET in map_html
    audio_ref = FOOD_LINE_LOGO_ASSET in audio_index
    podcast_artwork_exists = podcast_artwork.exists()
    podcast_feed_references_artwork = "podcast-artwork.png" in podcast_xml and "food-line/audio/podcast-artwork.png" in podcast_xml
    alt_text_present = DISPATCH_DISPLAY_NAME in index_html and DISPATCH_DISPLAY_NAME in archive_html and DISPATCH_DISPLAY_NAME in edition_html and DISPATCH_DISPLAY_NAME in source_table_html and DISPATCH_DISPLAY_NAME in map_html and DISPATCH_DISPLAY_NAME in audio_index
    css_present = any(
        "object-fit: contain" in page
        for page in (index_html, archive_html, edition_html, map_html, audio_index)
    )
    responsive_width_present = "max-width: 90vw" in index_html or "max-width: 90vw" in edition_html or "max-width: 90vw" in map_html or "max-width: 90vw" in audio_index
    result = {
        "asset_exists": logo_asset_exists,
        "generated_logo_exists": generated_logo_exists,
        "landing_page_references_logo": landing_ref,
        "archive_page_references_logo": archive_ref,
        "edition_page_references_logo": edition_ref,
        "source_table_page_references_logo": source_table_ref,
        "map_page_references_logo": map_ref,
        "audio_page_references_logo": audio_ref,
        "podcast_artwork_exists": podcast_artwork_exists,
        "podcast_feed_references_artwork": podcast_feed_references_artwork,
        "alt_text_present": alt_text_present,
        "css_prevents_distortion_or_cropping": css_present and responsive_width_present,
    }
    failures: list[str] = []
    if not logo_asset_exists:
        failures.append(f"missing logo asset: {assets_root / FOOD_LINE_LOGO_ASSET}")
    if not landing_ref:
        failures.append("landing page does not reference the Food Line logo")
    if not edition_ref:
        failures.append("edition page does not reference the Food Line logo")
    if not source_table_ref:
        failures.append("source table page does not reference the Food Line logo")
    if not map_ref:
        failures.append("map page does not reference the Food Line logo")
    if not audio_ref:
        failures.append("audio landing page does not reference the Food Line logo")
    if not podcast_artwork_exists:
        failures.append(f"missing podcast artwork: {podcast_artwork}")
    if not podcast_feed_references_artwork:
        failures.append("podcast feed does not reference the generated Food Line artwork")
    if not alt_text_present:
        failures.append(f"logo alt text does not include {DISPATCH_DISPLAY_NAME!r}")
    if not result["css_prevents_distortion_or_cropping"]:
        failures.append("logo CSS does not clearly prevent distortion or cropping")
    return result, failures, [str(path) for path in checked if path.exists()]


def _audit_visual_checks(root: Path, date: str | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    pages, linked_dates = _public_food_line_pages(root, date)
    audio_pages = _public_food_line_audio_pages(pages)
    combined = "\n".join(_read_text(path) for path in audio_pages)
    checked = [str(path) for path in pages if path.exists()]
    required_colors_present = all(color in combined for color in BLUE_FERN_COLORS)
    blue_fern_identification_present = "The Blue Fern Co." in combined
    footer_present = "Published by" in combined and "The Blue Fern Company" in combined
    chrome_hits = _contains_public_chrome(combined)
    signal_mix_present = "Signal mix today" in combined
    briefing_debug_hits = [phrase for phrase in PUBLIC_BRIEFING_DEBUG_PHRASES if phrase.lower() in combined.lower()]
    result = {
        "required_colors_present": required_colors_present,
        "blue_fern_identification_present": blue_fern_identification_present,
        "footer_present": footer_present,
        "public_chrome_hits": chrome_hits,
        "signal_mix_present": signal_mix_present,
        "public_briefing_debug_hits": briefing_debug_hits,
    }
    failures: list[str] = []
    linked_hint = f"linked_dates={','.join(linked_dates) or 'none'}"
    if not required_colors_present:
        failures.append("Food Line output does not include the required Blue Fern palette colors")
    if not blue_fern_identification_present:
        failures.append("Food Line output does not identify The Blue Fern Co.")
    if not footer_present:
        failures.append("Food Line output is missing the Blue Fern footer branding")
    if chrome_hits:
        for path in pages:
            text = _read_text(path)
            hits = [phrase for phrase in chrome_hits if phrase.lower() in text.lower()]
            if hits:
                snippet = next((_snippet_for_phrase(text, phrase) for phrase in hits if _snippet_for_phrase(text, phrase)), "")
                failures.append(
                    f"Food Line public output contains scraped site chrome in {path} ({linked_hint}): {', '.join(hits)}. Snippet: {snippet}"
                )
    if signal_mix_present:
        for path in audio_pages:
            text = _read_text(path)
            if "Signal mix today" in text:
                snippet = _snippet_for_phrase(text, "Signal mix today")
                failures.append(
                    f"Food Line public output still includes the confusing signal mix summary in {path} ({linked_hint}). Snippet: {snippet}"
                )
    if briefing_debug_hits:
        for path in audio_pages:
            text = _read_text(path)
            hits = [phrase for phrase in briefing_debug_hits if phrase.lower() in text.lower()]
            if hits:
                snippet = next((_snippet_for_phrase(text, phrase) for phrase in hits if _snippet_for_phrase(text, phrase)), "")
                failures.append(
                    f"Food Line public briefing text in {path} ({linked_hint}) still includes internal/debug phrasing: {', '.join(hits)}. Snippet: {snippet}"
                )
    return result, failures, checked


def _audit_product_checks(root: Path, date: str | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    pages, linked_dates = _public_food_line_pages(root, date)
    audio_pages = _public_food_line_audio_pages(pages)
    combined = "\n".join(_read_text(path) for path in audio_pages)
    checked = [str(path) for path in pages if path.exists()]
    forbidden = [phrase for phrase in RESOURCE_DIRECTORY_PHRASES if phrase in combined.lower()]
    product_ok = not forbidden
    chrome_hits = _contains_public_chrome(combined)
    signal_mix_present = "Signal mix today" in combined
    briefing_debug_hits = [phrase for phrase in PUBLIC_BRIEFING_DEBUG_PHRASES if phrase.lower() in combined.lower()]
    result = {
        "resource_directory_language_present": bool(forbidden),
        "forbidden_phrases": forbidden,
        "public_chrome_hits": chrome_hits,
        "signal_mix_present": signal_mix_present,
        "public_briefing_debug_hits": briefing_debug_hits,
    }
    failures = []
    linked_hint = f"linked_dates={','.join(linked_dates) or 'none'}"
    if not product_ok:
        failures.append(f"Food Line output contains forbidden resource-directory language: {', '.join(forbidden)}")
    if chrome_hits:
        for path in pages:
            text = _read_text(path)
            hits = [phrase for phrase in chrome_hits if phrase.lower() in text.lower()]
            if hits:
                snippet = next((_snippet_for_phrase(text, phrase) for phrase in hits if _snippet_for_phrase(text, phrase)), "")
                failures.append(
                    f"Food Line public output contains scraped site chrome in {path} ({linked_hint}): {', '.join(hits)}. Snippet: {snippet}"
                )
    if signal_mix_present:
        for path in audio_pages:
            text = _read_text(path)
            if "Signal mix today" in text:
                snippet = _snippet_for_phrase(text, "Signal mix today")
                failures.append(
                    f"Food Line public output still includes the confusing signal mix summary in {path} ({linked_hint}). Snippet: {snippet}"
                )
    if briefing_debug_hits:
        for path in audio_pages:
            text = _read_text(path)
            hits = [phrase for phrase in briefing_debug_hits if phrase.lower() in text.lower()]
            if hits:
                snippet = next((_snippet_for_phrase(text, phrase) for phrase in hits if _snippet_for_phrase(text, phrase)), "")
                failures.append(
                    f"Food Line public briefing text in {path} ({linked_hint}) still includes internal/debug phrasing: {', '.join(hits)}. Snippet: {snippet}"
                )
    return result, failures, checked


def _audit_pressure_marker_checks(root: Path, date: str) -> tuple[dict[str, Any], list[str], list[str]]:
    site_root = root / "output" / "site" / DISPATCH_SLUG
    map_data_path = site_root / "map" / "map_data.json"
    map_html_path = site_root / "map" / "index.html"
    payload = _read_json(map_data_path) or {}
    pressure_markers = payload.get("pressure_markers") if isinstance(payload, dict) else []
    excluded_records = payload.get("excluded_records") if isinstance(payload, dict) else []
    context_records = payload.get("context_records") if isinstance(payload, dict) else []
    baseline_records = payload.get("baseline_records") if isinstance(payload, dict) else []
    failures: list[str] = []
    checked = [str(path) for path in (map_data_path, map_html_path) if path.exists()]
    invalid_markers: list[str] = []
    if not isinstance(pressure_markers, list):
        failures.append("map_data.json pressure_markers is missing or malformed")
        pressure_markers = []
    for marker in pressure_markers:
        if not isinstance(marker, dict):
            invalid_markers.append("non-object marker")
            continue
        marker_issues = []
        if not marker.get("location_name"):
            marker_issues.append("location_name")
        if not marker.get("pressure_summary"):
            marker_issues.append("pressure_summary")
        if not marker.get("evidence_text"):
            marker_issues.append("evidence_text")
        if not marker.get("pressure_type") or str(marker.get("pressure_type")) == "context only":
            marker_issues.append("pressure_type")
        if not marker.get("evidence_level"):
            marker_issues.append("evidence_level")
        if not marker.get("freshness_role"):
            marker_issues.append("freshness_role")
        if not marker.get("source_title"):
            marker_issues.append("source_title")
        if not marker.get("source_url"):
            marker_issues.append("source_url")
        if str(marker.get("pressure_verification_status") or "") != "source_text_verified":
            marker_issues.append("pressure_verification_status")
        if not marker.get("dispatch_date"):
            marker_issues.append("dispatch_date")
        if not marker.get("coordinate_basis"):
            marker_issues.append("coordinate_basis")
        if "affected_groups" not in marker:
            marker_issues.append("affected_groups")
        if marker_issues:
            invalid_markers.append(f"{marker.get('source_record_id') or marker.get('source_title') or 'unknown'}: {', '.join(marker_issues)}")
    context_ids = {str(row.get("source_record_id") or "") for row in context_records or [] if isinstance(row, dict)}
    baseline_ids = {str(row.get("source_record_id") or "") for row in baseline_records or [] if isinstance(row, dict)}
    marker_ids = {str(row.get("source_record_id") or "") for row in pressure_markers if isinstance(row, dict)}
    overlap = sorted((context_ids | baseline_ids) & marker_ids)
    if overlap:
        failures.append(f"context or baseline records are being plotted as pressure markers: {', '.join(overlap)}")
    if invalid_markers:
        failures.append("pressure markers are missing required fields: " + "; ".join(invalid_markers))
    map_popup_template = _read_text(map_html_path)
    missing_popup_fields = [field for field in REQUIRED_POPUP_FIELDS if field not in map_popup_template]
    if missing_popup_fields:
        failures.append("map popup is missing required fields: " + ", ".join(missing_popup_fields))
    result = {
        "pressure_marker_count": len(pressure_markers),
        "context_record_count": len(context_records or []),
        "baseline_record_count": len(baseline_records or []),
        "excluded_record_count": len(excluded_records or []),
        "pressure_markers_are_verified": not invalid_markers and not overlap,
        "missing_popup_fields": missing_popup_fields,
    }
    return result, failures, checked


def _audit_source_table_checks(root: Path, date: str | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    site_root = root / "output" / "site" / DISPATCH_SLUG
    edition_root = site_root / "editions"
    checked: list[str] = []
    failures: list[str] = []
    table_headers_by_edition: dict[str, list[str]] = {}
    linked_dates = _linked_food_line_edition_dates(site_root)
    if date and date not in linked_dates:
        linked_dates.append(date)
    edition_dirs = [edition_root / edition_date for edition_date in linked_dates]
    for edition_dir in edition_dirs:
        source_table_path = edition_dir / "source_table.html"
        if not source_table_path.exists():
            continue
        checked.append(str(source_table_path))
        html_text = _read_text(source_table_path)
        headers = _html_header_values(html_text)
        table_headers_by_edition[edition_dir.name] = headers
        missing = [header for header in REQUIRED_SOURCE_TABLE_HEADERS if header not in headers]
        if missing:
            failures.append(f"{source_table_path} is missing required headers: {', '.join(missing)}")
        if "<td>false</td>" in html_text.lower():
            failures.append(f"{source_table_path} still includes excluded context records in the public source table")
    result = {
        "source_table_headers_by_edition": table_headers_by_edition,
        "required_columns_present": not failures,
    }
    return result, failures, checked


def _audit_mobile_basic_html_checks(root: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    pages, _linked_dates = _public_food_line_pages(root, None)
    html_pages = [path for path in pages if path.suffix.lower() == ".html"]
    combined = "\n".join(_read_text(path) for path in html_pages)
    checked = [str(path) for path in html_pages if path.exists()]
    viewport_ok = all('name="viewport" content="width=device-width, initial-scale=1"' in _read_text(path) for path in html_pages if path.exists())
    responsive_ok = "max-width: 90vw" in combined and "object-fit: contain" in combined
    result = {
        "viewport_meta_present": viewport_ok,
        "responsive_logo_rules_present": responsive_ok,
    }
    failures = []
    if not viewport_ok:
        failures.append("one or more Food Line pages are missing the responsive viewport meta tag")
    if not responsive_ok:
        failures.append("Food Line pages are missing the responsive logo sizing rules")
    return result, failures, checked


def run_food_line_blue_fern_compliance(root: Path, date: str) -> dict[str, Any]:
    root = Path(root).resolve()
    date = date.strip()
    site_root = root / "output" / "site" / DISPATCH_SLUG
    report_dir = _report_dir(root, date)
    report_json = report_dir / "blue_fern_compliance_report.json"
    report_md = report_dir / "blue_fern_compliance_report.md"
    warnings: list[str] = []
    failures: list[str] = []
    checked_files: list[str] = []

    logo_checks, logo_failures, logo_files = _audit_logo_checks(root, date)
    visual_checks, visual_failures, visual_files = _audit_visual_checks(root, date)
    product_checks, product_failures, product_files = _audit_product_checks(root, date)
    pressure_marker_checks, pressure_failures, pressure_files = _audit_pressure_marker_checks(root, date)
    source_table_checks, source_table_failures, source_table_files = _audit_source_table_checks(root, date)
    mobile_checks, mobile_failures, mobile_files = _audit_mobile_basic_html_checks(root)

    checked_files.extend(sorted({*logo_files, *visual_files, *product_files, *pressure_files, *source_table_files, *mobile_files}))
    failures.extend(logo_failures)
    failures.extend(visual_failures)
    failures.extend(product_failures)
    failures.extend(pressure_failures)
    failures.extend(source_table_failures)
    failures.extend(mobile_failures)

    if not (site_root / "audio" / "podcast-artwork.png").exists():
        warnings.append("Food Line podcast artwork was not generated in output/site/food-line/audio/")

    report = {
        "ok": not failures,
        "date": date,
        "dispatch_slug": DISPATCH_SLUG,
        "dispatch_name": DISPATCH_NAME,
        "logo_checks": logo_checks,
        "visual_checks": visual_checks,
        "product_checks": product_checks,
        "pressure_marker_checks": pressure_marker_checks,
        "source_table_checks": source_table_checks,
        "mobile_basic_html_checks": mobile_checks,
        "warnings": warnings,
        "failures": failures,
        "checked_files": checked_files,
        "report_json": str(report_json),
        "report_md": str(report_md),
    }

    _write_json(report_json, report)
    md_lines = [
        f"# Food Line Blue Fern Compliance {date}",
        "",
        f"- ok: `{report['ok']}`",
        f"- report_json: `{report['report_json']}`",
        f"- report_md: `{report['report_md']}`",
        "",
        "## Logo Checks",
    ]
    for key, value in logo_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Visual Checks"])
    for key, value in visual_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Product Checks"])
    for key, value in product_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Pressure Marker Checks"])
    for key, value in pressure_marker_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Source Table Checks"])
    for key, value in source_table_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## Mobile / Basic HTML Checks"])
    for key, value in mobile_checks.items():
        md_lines.append(f"- {key}: `{value}`")
    if warnings:
        md_lines.extend(["", "## Warnings"])
        md_lines.extend([f"- {warning}" for warning in warnings])
    if failures:
        md_lines.extend(["", "## Failures"])
        md_lines.extend([f"- {failure}" for failure in failures])
    md_lines.extend(["", "## Checked Files"])
    md_lines.extend([f"- `{path}`" for path in checked_files])
    _write_text(report_md, "\n".join(md_lines).strip() + "\n")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Food Line Blue Fern compliance audit.")
    parser.add_argument("--date", required=True, help="Edition date YYYY-MM-DD to audit.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root that contains output/site.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_food_line_blue_fern_compliance(Path(args.root), args.date)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
