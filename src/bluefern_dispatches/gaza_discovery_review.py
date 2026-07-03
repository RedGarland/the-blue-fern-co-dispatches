from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bluefern_dispatches.gaza_sources import canonicalize_url, load_sources_config, normalize_publisher, normalize_title


DISPATCH_SLUG = "gaza"


PUBLISHER_RECOMMENDATIONS = {
    "el pais english": "add targeted EL PAIS English Gaza humanitarian query",
    "bbc": "add targeted BBC Gaza health and evacuation query supplement",
    "bbc news": "add targeted BBC Gaza health and evacuation query supplement",
    "jerusalem post": "add targeted Jerusalem Post Gaza accountability query",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a JSON list or a sources list")
    return [row for row in rows if isinstance(row, dict)]


def _manual_records(root: Path, edition_date: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.json"
    if not path.exists():
        return [], [f"manual source file missing: {path}"]
    rows = _load_json_list(path)
    manual = [
        row
        for row in rows
        if str(row.get("source_type") or "").strip().lower() == "manual"
        or str(row.get("provider_id") or "").strip().lower() == "manual-supplement"
    ]
    return manual, []


def _auto_discovery_rows(root: Path, edition_date: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_path = root / "data" / "dispatches" / DISPATCH_SLUG / "raw" / edition_date / "raw_sources.json"
    context_path = root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "source_collection_context.json"

    if raw_path.exists():
        for row in _load_json_list(raw_path):
            if str(row.get("source_type") or "").strip().lower() == "manual":
                continue
            if str(row.get("provider_id") or "").strip().lower() == "manual-supplement":
                continue
            item = dict(row)
            item["candidate_origin"] = "accepted_auto_source"
            rows.append(item)
    else:
        warnings.append(f"optional auto source artifact missing: {raw_path}")

    if context_path.exists():
        payload = _read_json(context_path)
        if isinstance(payload, dict):
            for key in ("review_candidates", "top_rejected_examples"):
                for row in payload.get(key) or []:
                    if not isinstance(row, dict):
                        continue
                    item = dict(row)
                    item["candidate_origin"] = key
                    rows.append(item)
    else:
        warnings.append(f"optional discovery context missing: {context_path}")

    return rows, warnings


def _generated_rows(root: Path, edition_date: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "sources_manifest.json"
    if not path.exists():
        return [], [f"optional generated sources artifact missing: {path}"]
    return _load_json_list(path), []


def _source_config_rows(root: Path) -> list[dict[str, str]]:
    config_path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources.yml"
    rows: list[dict[str, str]] = []
    for definition in load_sources_config(config_path):
        rows.append(
            {
                "source_id": definition.source_id,
                "publisher": definition.publisher,
                "source_state": definition.source_state,
                "source_tier": definition.source_tier,
                "source_group": definition.source_group,
                "discovery_role": definition.discovery_role,
            }
        )
    return rows


def _row_urls(row: dict[str, Any]) -> set[str]:
    urls = {
        canonicalize_url(str(row.get("url") or "")),
        canonicalize_url(str(row.get("canonical_url") or "")),
        canonicalize_url(str(row.get("normalized_url") or "")),
    }
    return {url for url in urls if url}


def _publisher_key(value: Any) -> str:
    return normalize_publisher(str(value or ""))


def _title_key(value: Any) -> str:
    return normalize_title(str(value or ""))


def _matched_auto_rows(manual_row: dict[str, Any], auto_rows: list[dict[str, Any]]) -> dict[str, Any]:
    manual_urls = _row_urls(manual_row)
    manual_publisher = _publisher_key(manual_row.get("publisher"))
    manual_title = _title_key(manual_row.get("title"))
    exact_url_rows: list[dict[str, Any]] = []
    publisher_rows: list[dict[str, Any]] = []
    publisher_title_rows: list[dict[str, Any]] = []

    for row in auto_rows:
        row_urls = _row_urls(row)
        if manual_urls and row_urls.intersection(manual_urls):
            exact_url_rows.append(row)
        row_publisher = _publisher_key(row.get("publisher"))
        if manual_publisher and row_publisher == manual_publisher:
            publisher_rows.append(row)
            if manual_title and _title_key(row.get("title")) == manual_title:
                publisher_title_rows.append(row)

    return {
        "exact_url_rows": exact_url_rows,
        "publisher_rows": publisher_rows,
        "publisher_title_rows": publisher_title_rows,
    }


def _generated_match(manual_row: dict[str, Any], generated_rows: list[dict[str, Any]]) -> bool:
    manual_urls = _row_urls(manual_row)
    manual_id = str(manual_row.get("source_record_id") or "").strip()
    for row in generated_rows:
        if manual_id and manual_id == str(row.get("source_record_id") or "").strip():
            return True
        if manual_urls.intersection(_row_urls(row)):
            return True
    return False


def _publisher_config(publisher: str, config_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    key = _publisher_key(publisher)
    return [row for row in config_rows if key and _publisher_key(row.get("publisher")) == key]


def _likely_miss_reason(
    manual_row: dict[str, Any],
    matches: dict[str, Any],
    config_matches: list[dict[str, str]],
    auto_rows_present: bool,
) -> str:
    if not auto_rows_present:
        return "auto discovery artifacts unavailable for comparison"
    if matches["publisher_title_rows"]:
        return "same publisher and headline surfaced under a different URL variant"
    if matches["publisher_rows"]:
        return "publisher was discovered, but this specific Gaza item did not surface as the same URL"
    if not config_matches:
        return "publisher not present in automated Gaza discovery config"
    enabled = [row for row in config_matches if str(row.get("source_state") or "") == "enabled"]
    if not enabled:
        return "publisher has no enabled automated Gaza discovery source"
    if any(str(row.get("source_group") or "").startswith("accountability") for row in config_matches):
        return "secondary accountability source was configured but did not surface this item"
    return "configured discovery sources did not surface this manual item"


def _recommended_action(manual_row: dict[str, Any], matches: dict[str, Any], config_matches: list[dict[str, str]]) -> str:
    publisher_key = _publisher_key(manual_row.get("publisher"))
    explicit = PUBLISHER_RECOMMENDATIONS.get(publisher_key)
    if matches["publisher_title_rows"]:
        return "review canonical URL handling for same-story alternate URLs"
    if explicit:
        return explicit
    if not config_matches:
        category = str(manual_row.get("category_hint") or "").strip().lower()
        if "accountability" in category or "conduct" in category:
            return "add a narrow Gaza accountability discovery query or keep manual-only"
        return "add a narrow Gaza-specific discovery query for this publisher"
    return "tighten Gaza query targeting for this publisher and category"


def build_gaza_discovery_miss_report(root: Path, edition_date: str) -> dict[str, Any]:
    warnings: list[str] = []
    manual_rows, manual_warnings = _manual_records(root, edition_date)
    auto_rows, auto_warnings = _auto_discovery_rows(root, edition_date)
    generated_rows, generated_warnings = _generated_rows(root, edition_date)
    warnings.extend(manual_warnings)
    warnings.extend(auto_warnings)
    warnings.extend(generated_warnings)
    config_rows = _source_config_rows(root)

    missed_rows: list[dict[str, Any]] = []
    auto_rows_present = bool(auto_rows)
    for manual_row in manual_rows:
        matches = _matched_auto_rows(manual_row, auto_rows)
        found_same_url = bool(matches["exact_url_rows"])
        if found_same_url:
            continue
        publisher = str(manual_row.get("publisher") or "").strip()
        config_matches = _publisher_config(publisher, config_rows)
        missed_rows.append(
            {
                "date": edition_date,
                "source_record_id": str(manual_row.get("source_record_id") or "").strip(),
                "publisher": publisher,
                "url": str(manual_row.get("url") or "").strip(),
                "title": str(manual_row.get("title") or "").strip(),
                "category_hint": str(manual_row.get("category_hint") or "").strip(),
                "auto_discovery_found_same_url": False,
                "auto_discovery_found_same_publisher": bool(matches["publisher_rows"]),
                "appears_in_generated_sources": _generated_match(manual_row, generated_rows),
                "same_publisher_same_title_variant": bool(matches["publisher_title_rows"]),
                "likely_miss_reason": _likely_miss_reason(manual_row, matches, config_matches, auto_rows_present),
                "recommended_discovery_action": _recommended_action(manual_row, matches, config_matches),
            }
        )

    summary = {
        "manual_record_count": len(manual_rows),
        "auto_candidate_count": len(auto_rows),
        "generated_source_count": len(generated_rows),
        "missed_manual_record_count": len(missed_rows),
        "same_publisher_miss_count": sum(1 for row in missed_rows if row["auto_discovery_found_same_publisher"]),
        "same_story_variant_count": sum(1 for row in missed_rows if row["same_publisher_same_title_variant"]),
    }
    return {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "summary": summary,
        "missed_manual_records": missed_rows,
        "warnings": warnings,
    }


def review_report_path(root: Path, edition_date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / "discovery_misses" / f"{edition_date}.json"


def write_gaza_discovery_miss_report(root: Path, edition_date: str) -> dict[str, Any]:
    report = build_gaza_discovery_miss_report(root, edition_date)
    path = review_report_path(root, edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(path)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def summarize_gaza_discovery_miss_report(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    lines = [
        (
            f"{report.get('edition_date')} Gaza discovery misses: "
            f"{summary.get('missed_manual_record_count', 0)} missed manual records "
            f"out of {summary.get('manual_record_count', 0)} manual records."
        )
    ]
    for row in list(report.get("missed_manual_records") or []):
        lines.append(
            "- "
            + f"{row.get('publisher')}: {row.get('title')} "
            + f"[same publisher: {'yes' if row.get('auto_discovery_found_same_publisher') else 'no'}] "
            + f"-> {row.get('recommended_discovery_action')}"
        )
    for warning in list(report.get("warnings") or []):
        lines.append(f"warning: {warning}")
    return "\n".join(lines)
