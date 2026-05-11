from pathlib import Path
import argparse
import json
import sys
from datetime import date as local_date, timedelta


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_curate import curate_sources
from bluefern_dispatches.cascadia_historical_search import create_manual_source_template, load_historical_config, manual_sources_path, retrieve_historical_sources, source_folder, validate_manual_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import refresh_cascadia_archive_pages, render_cascadia_edition
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_source_registry import load_source_registry
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, backfill_weekly_from_existing_editions, containing_week, explicit_week, previous_completed_week
from bluefern_dispatches.cascadia_weekly import format_coverage_label
from bluefern_dispatches.shared_records import update_shared_records


ZERO_WEEK_SOURCE_GROUPS = [
    "state governor press releases",
    "state emergency management",
    "state transportation agencies",
    "state health agencies",
    "state environmental/wildfire/climate agencies",
    "housing and homelessness agencies",
    "labor/economic/workforce agencies",
    "public utility / energy reliability sources",
    "major city/county public safety or emergency pages",
    "reputable regional media sources",
]

MIN_SUCCESSFUL_SOURCE_CHECKS_FOR_CREDIBLE_ZERO = 20
MIN_SUCCESSFUL_FETCH_RATE_FOR_CREDIBLE_ZERO = 0.40
REQUIRED_SUCCESSFUL_SOURCE_GROUPS_FOR_CREDIBLE_ZERO = [
    "official_state_regional_source",
    "local_regional_source",
    "historical_search_provider",
]


def run_pipeline(
    date: str,
    ingest: bool,
    normalize: bool,
    curate: bool,
    render: bool,
    dry_run: bool,
    mode: str = "custom",
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str | None = None,
) -> dict[str, object]:
    warnings: list[str] = []
    errors: list[str] = []
    output_paths: dict[str, str] = {}
    manifest_paths: dict[str, str] = {}
    detail_output_paths: dict[str, str] = {}
    shared_record_paths: dict[str, str] = {}
    raw_count = normalized_count = curated_count = public_story_count = 0
    detail_count = 0
    run_date = run_date or date

    if ingest:
        result = ingest_sources(ROOT, date, dry_run=dry_run)
        raw_count = int(result.get("raw_count", 0))
        output_paths["raw"] = str(result.get("raw_path"))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if normalize and not errors:
        result = normalize_sources(ROOT, date, dry_run=dry_run)
        normalized_count = int(result.get("normalized_count", 0))
        output_paths["normalized"] = str(result.get("normalized_path"))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if curate and not errors:
        result = curate_sources(ROOT, date, dry_run=dry_run)
        curated_count = int(result.get("curated_count", 0))
        public_story_count = int(result.get("public_story_count", 0))
        output_paths["curated"] = str(result.get("curation_path"))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if render and not errors:
        result = render_cascadia_edition(
            ROOT,
            date,
            dry_run=dry_run,
            run_date=run_date,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            briefing_type=briefing_type or ("weekly" if mode in {"weekly", "weekly-public"} else "daily"),
        )
        public_story_count = int(result.get("public_story_count", public_story_count))
        detail_count = int(result.get("detail_count", detail_count))
        output_paths.update(result.get("output_paths", {}))
        detail_output_paths.update(result.get("detail_output_paths", {}))
        manifest_paths.update(result.get("manifest_paths", {}))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if (curate or render) and not render and not errors:
        result = write_cascadia_signal_package(
            ROOT,
            date,
            dry_run=dry_run,
            run_date=run_date,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            briefing_type=briefing_type or ("weekly" if mode in {"weekly", "weekly-public"} else "daily"),
        )
        detail_count = int(result.get("detail_count", 0))
        detail_output_paths.update(result.get("output_paths", {}))
        if "records_json" in detail_output_paths:
            output_paths["detail_output"] = str(Path(detail_output_paths["records_json"]).parent)
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if (curate or render) and not errors:
        result = update_shared_records(ROOT, date, detail_paths=detail_output_paths, public_rendered=render, dry_run=dry_run)
        shared_record_paths.update(result.get("shared_record_paths", {}))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))

    return {
        "ok": not errors,
        "date": date,
        "run_date": run_date,
        "edition_date": date,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "mode": mode,
        "raw_count": raw_count,
        "normalized_count": normalized_count,
        "curated_count": curated_count,
        "detail_count": detail_count,
        "public_story_count": public_story_count,
        "public_rendered": render and not errors,
        "output_paths": output_paths,
        "manifest_paths": manifest_paths,
        "detail_output_paths": detail_output_paths,
        "shared_record_paths": shared_record_paths,
        "warnings": warnings,
        "errors": errors,
    }


def resolve_week_args(args: argparse.Namespace) -> tuple[str, str, str]:
    if bool(args.week_start) != bool(args.week_end):
        raise ValueError("--week-start and --week-end must be provided together")
    if args.archive_week and (args.week_start or args.week_end):
        raise ValueError("--archive-week cannot be combined with --week-start/--week-end")
    if args.week_start and args.week_end:
        start, end = explicit_week(args.week_start, args.week_end)
    elif args.archive_week:
        start, end = containing_week(args.archive_week)
    else:
        start, end = previous_completed_week(args.date)
    return start.isoformat(), end.isoformat(), end.isoformat()


def completed_week_windows(run_date: str, weeks: int) -> list[tuple[str, str, str]]:
    if weeks < 1:
        raise ValueError("--backfill-weeks must be at least 1")
    start, end = previous_completed_week(run_date)
    windows = []
    for offset in range(weeks):
        window_start = start - timedelta(days=7 * offset)
        window_end = end - timedelta(days=7 * offset)
        windows.append((window_start.isoformat(), window_end.isoformat(), window_end.isoformat()))
    return windows


def unique_messages(messages: list[str]) -> list[str]:
    seen = set()
    unique = []
    for message in messages:
        if message in seen:
            continue
        seen.add(message)
        unique.append(message)
    return unique


def read_json_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def manifest_public_story_count(edition_date: str) -> int:
    manifest_path = ROOT / "output" / "site" / "cascadia" / "editions" / edition_date / "edition_manifest.json"
    manifest = read_json_object(manifest_path)
    try:
        return int(manifest.get("public_story_count") or 0)
    except (TypeError, ValueError):
        return 0


def quality_targets() -> dict[str, int]:
    config = load_historical_config(ROOT)
    targets = config.get("quality_targets") if isinstance(config.get("quality_targets"), dict) else {}
    return {
        "min_public_stories": int(targets.get("min_public_stories", 3)),
        "target_public_stories": int(targets.get("target_public_stories", 5)),
        "max_public_stories": int(targets.get("max_public_stories", 7)),
    }


def weekly_manual_commands(start: str, end: str) -> dict[str, str]:
    base = ".\\.venv\\Scripts\\python.exe scripts\\run_cascadia_dispatch.py"
    rerun = (
        f"{base} --archive-week {start} --weekly-public --historical-search "
        "--historical-provider all --max-historical-queries 8 --historical-delay-seconds 10"
    )
    return {
        "create_template": f"{base} --archive-week {start} --create-manual-template",
        "validate": f"{base} --archive-week {start} --validate-manual-sources",
        "rerun": rerun,
    }


def write_weekly_quality_report(start: str, end: str, edition_date: str, aggregate: dict[str, object], render_result: dict[str, object], dry_run: bool) -> dict[str, object]:
    targets = quality_targets()
    public_dir = ROOT / "output" / "site" / "cascadia" / "editions" / edition_date
    curation = read_json_list(ROOT / "data" / "dispatches" / "cascadia" / "curated" / edition_date / "curation_manifest.json")
    sources = read_json_list(public_dir / "sources_manifest.json")
    historical_report = dict(aggregate.get("report") or {})
    public_stories = [story for story in curation if story.get("included_in_public_summary")]
    public_story_count = int(render_result.get("public_story_count", len(public_stories)) or 0)
    target_public_stories = targets["target_public_stories"]
    below_target = public_story_count < targets["min_public_stories"]
    manual_path = manual_sources_path(ROOT, local_date.fromisoformat(start), local_date.fromisoformat(end))
    recommendations: list[str] = []
    warnings = unique_messages(list(aggregate.get("warnings", [])) + list(render_result.get("warnings", [])))
    errors = unique_messages(list(aggregate.get("errors", [])) + list(render_result.get("errors", [])))
    if below_target:
        recommendations.append("Below target story count; add manual supplement or rerun with additional providers.")
        warnings.append("Below target story count; add manual supplement or rerun with additional providers.")
    commands = weekly_manual_commands(start, end)
    story_categories: dict[str, int] = {}
    story_states: dict[str, int] = {}
    for story in public_stories:
        category = str(story.get("category") or "unknown")
        story_categories[category] = story_categories.get(category, 0) + 1
        states = {
            str(record.get("state_hint") or record.get("region_scope") or "unknown")
            for record in story.get("source_records", [])
            if isinstance(record, dict)
        } or {"unknown"}
        for state in states:
            story_states[state] = story_states.get(state, 0) + 1
    source_count_by_provider: dict[str, int] = {}
    source_count_by_type: dict[str, int] = {}
    for source in sources:
        provider = str(source.get("provider_id") or "unknown")
        source_type = str(source.get("source_type") or "unknown")
        source_count_by_provider[provider] = source_count_by_provider.get(provider, 0) + 1
        source_count_by_type[source_type] = source_count_by_type.get(source_type, 0) + 1
    report = {
        "coverage_start": start,
        "coverage_end": end,
        "coverage_label": format_coverage_label(start, end),
        "query_groups_run": [
            {
                "provider_id": item.get("provider_id"),
                "query_group": item.get("query_group"),
                "result_count": item.get("result_count", 0),
                "cache_hit": item.get("cache_hit", False),
            }
            for item in historical_report.get("queries_run", [])
            if isinstance(item, dict)
        ],
        "raw_results_count": int(historical_report.get("raw_results_count") or 0),
        "records_saved": int(historical_report.get("records_saved") or aggregate.get("source_count") or 0),
        "records_excluded": int(historical_report.get("records_excluded") or aggregate.get("excluded_source_count") or 0),
        "public_story_count": public_story_count,
        "min_public_stories": targets["min_public_stories"],
        "target_public_stories": target_public_stories,
        "max_public_stories": targets["max_public_stories"],
        "below_target": below_target,
        "missing_story_count": max(0, target_public_stories - public_story_count),
        "stories_by_category": dict(sorted(story_categories.items())),
        "stories_by_state_hint": dict(sorted(story_states.items())),
        "source_count_by_provider": dict(sorted(source_count_by_provider.items())),
        "source_count_by_type": dict(sorted(source_count_by_type.items())),
        "manual_supplement_path": str(manual_path),
        "manual_supplement_commands": commands,
        "recommendations": recommendations,
        "warnings": unique_messages(warnings),
        "errors": errors,
    }
    report_path = source_folder(ROOT, local_date.fromisoformat(start), local_date.fromisoformat(end)) / "weekly_quality_report.json"
    if not dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["weekly_quality_report_path"] = str(report_path)
    return report


def _warning_list(*values: object) -> list[str]:
    warnings: list[str] = []
    for value in values:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, list):
                    warnings.extend(str(nested) for nested in item if nested)
                elif item:
                    warnings.append(str(item))
        elif value:
            warnings.append(str(value))
    return unique_messages(warnings)


def _registry_diagnostics(report: dict[str, object]) -> list[dict[str, object]]:
    diagnostics = report.get("registry_source_diagnostics")
    return [item for item in diagnostics if isinstance(item, dict)] if isinstance(diagnostics, list) else []


def _failure_reason(item: dict[str, object]) -> str | None:
    errors = item.get("errors")
    if isinstance(errors, list) and errors:
        text = " ".join(str(error) for error in errors)
    else:
        text = str(item.get("error") or "")
    warnings = " ".join(str(warning) for warning in item.get("warnings", []) if warning) if isinstance(item.get("warnings"), list) else ""
    combined = f"{text} {warnings}".strip().lower()
    if not combined and item.get("fetch_successful") is False:
        return "fetch_failed"
    if "certificate" in combined or "ssl" in combined or "tls" in combined or "schannel" in combined or "revocation" in combined:
        return "tls_or_certificate"
    if "timed out" in combined or "timeout" in combined or "handshake" in combined:
        return "timeout"
    if "getaddrinfo" in combined or "dns" in combined:
        return "dns"
    if "empty response" in combined:
        return "empty_response"
    if "parse" in combined or "invalid json" in combined or "non-json" in combined:
        return "parse_failure"
    if combined:
        return "fetch_or_provider_error"
    return None


def _diagnostic_fetch_successful(item: dict[str, object]) -> bool:
    if _failure_reason(item):
        return False
    if item.get("cache_hit"):
        return True
    try:
        if int(item.get("bytes_read") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if item.get("fetch_backend") == "cache":
        return True
    return False


def _query_fetch_successful(item: dict[str, object]) -> bool:
    if item.get("error") or item.get("rate_limited"):
        return False
    return True


def _source_group_for_diagnostic(item: dict[str, object]) -> str:
    source_type = str(item.get("source_type") or "")
    geographic_scope = str(item.get("geographic_scope") or item.get("source_name") or "").lower()
    source_id = str(item.get("source_id") or "").lower()
    if source_type in {"official_page", "press_release_page", "alert_feed"}:
        if any(term in geographic_scope or term in source_id for term in ["seattle", "king", "tacoma", "pierce", "spokane", "portland", "multnomah", "salem", "marion", "eugene", "lane", "boise", "ada"]):
            return "local_regional_source"
        return "official_state_regional_source"
    if source_type in {"rss", "media_feed"}:
        return "local_regional_source"
    return "registry_source"


def _successful_fetch_metrics(historical_report: dict[str, object]) -> dict[str, object]:
    registry_diagnostics = _registry_diagnostics(historical_report)
    attempted = 0
    successful = 0
    successful_by_group: dict[str, int] = {}
    failed_by_group: dict[str, int] = {}
    failures_by_reason: dict[str, int] = {}

    for item in registry_diagnostics:
        if item.get("skip_reason") == "unsupported_source_type":
            continue
        attempted += 1
        group = _source_group_for_diagnostic(item)
        if _diagnostic_fetch_successful(item):
            successful += 1
            successful_by_group[group] = successful_by_group.get(group, 0) + 1
        else:
            failed_by_group[group] = failed_by_group.get(group, 0) + 1
            reason = _failure_reason(item) or "not_fetchable_or_empty"
            failures_by_reason[reason] = failures_by_reason.get(reason, 0) + 1

    for item in historical_report.get("queries_run", []):
        if not isinstance(item, dict):
            continue
        attempted += 1
        group = "historical_search_provider"
        if _query_fetch_successful(item):
            successful += 1
            successful_by_group[group] = successful_by_group.get(group, 0) + 1
        else:
            failed_by_group[group] = failed_by_group.get(group, 0) + 1
            reason = _failure_reason(item) or "provider_query_failed"
            failures_by_reason[reason] = failures_by_reason.get(reason, 0) + 1

    failed = max(0, attempted - successful)
    rate = round(successful / attempted, 4) if attempted else 0.0
    minimum_review_threshold_met = (
        successful >= MIN_SUCCESSFUL_SOURCE_CHECKS_FOR_CREDIBLE_ZERO
        and rate >= MIN_SUCCESSFUL_FETCH_RATE_FOR_CREDIBLE_ZERO
        and all(successful_by_group.get(group, 0) > 0 for group in REQUIRED_SUCCESSFUL_SOURCE_GROUPS_FOR_CREDIBLE_ZERO)
    )
    return {
        "source_checks_attempted": attempted,
        "source_checks_successful": successful,
        "source_checks_failed": failed,
        "successful_fetch_rate": rate,
        "successful_sources_by_group": dict(sorted(successful_by_group.items())),
        "failed_sources_by_group": dict(sorted(failed_by_group.items())),
        "fetch_failures_by_reason": dict(sorted(failures_by_reason.items())),
        "minimum_review_threshold_met": minimum_review_threshold_met,
        "minimum_review_threshold": {
            "min_successful_source_checks": MIN_SUCCESSFUL_SOURCE_CHECKS_FOR_CREDIBLE_ZERO,
            "min_successful_fetch_rate": MIN_SUCCESSFUL_FETCH_RATE_FOR_CREDIBLE_ZERO,
            "required_successful_source_groups": REQUIRED_SUCCESSFUL_SOURCE_GROUPS_FOR_CREDIBLE_ZERO,
        },
    }


def zero_story_public_wording(minimum_review_threshold_met: bool) -> str:
    if minimum_review_threshold_met:
        return "Reviewed week | No qualifying source-backed regional signals identified"
    return "Reviewed week | No qualifying source-backed regional signals surfaced"


def _rejected_candidate_summary(report: dict[str, object]) -> list[dict[str, object]]:
    rejected: list[dict[str, object]] = []
    for group_name, key in [
        ("historical_search", "exclusion_reasons"),
        ("registry", "registry_exclusion_reasons"),
        ("official_pages", "official_exclusion_reasons"),
    ]:
        reasons = report.get(key)
        if isinstance(reasons, dict):
            for reason, count in sorted(reasons.items()):
                rejected.append({"source_group": group_name, "reason": reason, "count": count})
    for diagnostic in _registry_diagnostics(report):
        for excluded in diagnostic.get("excluded_links", []) if isinstance(diagnostic.get("excluded_links"), list) else []:
            if isinstance(excluded, dict):
                rejected.append(
                    {
                        "source_group": "official_pages",
                        "source_id": diagnostic.get("source_id"),
                        "title": excluded.get("title"),
                        "url": excluded.get("url"),
                        "publisher": diagnostic.get("source_name"),
                        "published_date": excluded.get("published_at"),
                        "state_or_region_inferred": None,
                        "category_inferred": None,
                        "score": None,
                        "accepted": False,
                        "rejection_stage": "official_page_parse",
                        "reason": excluded.get("reason") or "excluded",
                        "rejection_reason": excluded.get("reason") or "excluded",
                        "url_fetchable": _diagnostic_fetch_successful(diagnostic),
                        "sufficient_source_support": False,
                    }
                )
    return rejected


def _candidate_rejection_counts(rejected_candidates: list[dict[str, object]], report: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in rejected_candidates:
        reason = str(candidate.get("rejection_reason") or candidate.get("reason") or "rejected")
        counts[reason] = counts.get(reason, 0) + 1
    for key in ["exclusion_reasons", "registry_exclusion_reasons", "official_exclusion_reasons"]:
        reasons = report.get(key)
        if isinstance(reasons, dict):
            for reason, count in reasons.items():
                counts[str(reason)] = counts.get(str(reason), 0) + int(count or 0)
    return dict(sorted(counts.items()))


def _candidate_rejections_by_stage(rejected_candidates: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in rejected_candidates:
        stage = str(candidate.get("rejection_stage") or candidate.get("source_group") or "validation")
        counts[stage] = counts.get(stage, 0) + 1
    return dict(sorted(counts.items()))


def _update_zero_story_public_manifests(edition_date: str, wording: str, minimum_review_threshold_met: bool, dry_run: bool) -> None:
    if dry_run:
        return
    for base in [
        ROOT / "output" / "site" / "cascadia" / "editions" / edition_date,
        ROOT / "output" / "dispatches" / "cascadia" / "editions" / edition_date,
    ]:
        manifest_path = base / "edition_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(manifest, dict) or int(manifest.get("public_story_count") or 0) != 0:
            continue
        manifest["public_archive_subtitle"] = wording
        manifest["minimum_review_threshold_met"] = minimum_review_threshold_met
        manifest["zero_story_review_status"] = "credible" if minimum_review_threshold_met else "incomplete_successful_fetch_coverage"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written: list[str] = []
    refresh_cascadia_archive_pages(ROOT, dry_run=False, written=written)


def write_zero_week_gap_report(
    start: str,
    end: str,
    edition_date: str,
    initial_public_story_count: int,
    aggregate: dict[str, object],
    render_result: dict[str, object],
    dry_run: bool,
) -> dict[str, object]:
    historical_report = dict(aggregate.get("report") or {})
    registry_diagnostics = _registry_diagnostics(historical_report)
    gdelt_queries = [
        item
        for item in historical_report.get("queries_run", [])
        if isinstance(item, dict) and item.get("provider_id") == "gdelt"
    ]
    official_pages = [
        item
        for item in registry_diagnostics
        if item.get("source_type") in {"official_page", "press_release_page"}
    ]
    registry_sources_checked = [
        {
            "source_id": item.get("source_id"),
            "source_name": item.get("source_name"),
            "source_type": item.get("source_type"),
            "url": item.get("url"),
            "cache_hit": item.get("cache_hit"),
            "raw_count": item.get("raw_count"),
            "errors": item.get("errors", []),
            "warnings": item.get("warnings", []),
        }
        for item in registry_diagnostics
    ]
    provider_queries = [
        {
            "provider_id": item.get("provider_id"),
            "query_group": item.get("query_group"),
            "query": item.get("query"),
            "result_count": item.get("result_count", 0),
            "error": item.get("error"),
            "cache_hit": item.get("cache_hit", False),
            "fallback_used": item.get("fallback_used", False),
        }
        for item in historical_report.get("queries_run", [])
        if isinstance(item, dict)
    ]
    fetch_warnings = _warning_list(
        historical_report.get("warnings", []),
        [item.get("warnings", []) for item in registry_diagnostics],
    )
    provider_warnings = _warning_list(aggregate.get("warnings", []), render_result.get("warnings", []))
    tls_or_network_warnings = [
        warning
        for warning in _warning_list(
            historical_report.get("tls_or_revocation_hint"),
            historical_report.get("python_fetch_error"),
            historical_report.get("curl_stderr_tail"),
            [item.get("errors", []) for item in registry_diagnostics],
            [item.get("recommendation") for item in registry_diagnostics],
        )
        if any(term in warning.lower() for term in ["tls", "ssl", "certificate", "timeout", "network", "dns", "getaddrinfo", "curl", "revocation"])
    ]
    fetch_metrics = _successful_fetch_metrics(historical_report)
    accepted_candidate_count = int(historical_report.get("records_saved") or aggregate.get("source_count") or 0)
    rejected_candidate_count = int(historical_report.get("records_excluded") or 0)
    rejected_candidate_count += int(historical_report.get("registry_records_excluded") or 0)
    rejected_candidate_count += int(historical_report.get("official_links_excluded") or 0)
    candidate_count = max(
        accepted_candidate_count + rejected_candidate_count,
        int(historical_report.get("raw_results_count") or 0)
        + int(historical_report.get("registry_records_raw") or 0)
        + int(historical_report.get("manual_sources_loaded") or 0),
    )
    final_public_story_count = int(render_result.get("public_story_count") or 0)
    manual_valid = bool(historical_report.get("manual_sources_valid", True))
    minimum_review_threshold_met = bool(fetch_metrics["minimum_review_threshold_met"])
    final_zero_story_result_is_credible: bool | None
    public_zero_story_wording: str | None
    if final_public_story_count == 0:
        final_zero_story_result_is_credible = (
            accepted_candidate_count == 0
            and manual_valid
            and minimum_review_threshold_met
            and not aggregate.get("errors")
            and not render_result.get("errors")
        )
        public_zero_story_wording = zero_story_public_wording(minimum_review_threshold_met)
    else:
        final_zero_story_result_is_credible = None
        public_zero_story_wording = None
    if final_public_story_count:
        final_reason = "One or more source-backed public stories survived validation."
    elif final_zero_story_result_is_credible:
        final_reason = "No source-backed public story survived validation after manual, registry, official-page, and GDELT checks; provider/TLS/network warnings are documented."
    else:
        final_reason = "Zero-story result needs additional review because successful fetched/evaluated source coverage did not meet the minimum review threshold or validation errors were recorded."
    registry = load_source_registry(ROOT)
    rejected_candidates = _rejected_candidate_summary(historical_report)
    candidate_rejection_counts = _candidate_rejection_counts(rejected_candidates, historical_report)
    report = {
        "edition_date": edition_date,
        "coverage_start": start,
        "coverage_end": end,
        "coverage_label": format_coverage_label(start, end),
        "initial_public_story_count": initial_public_story_count,
        "final_public_story_count": final_public_story_count,
        "source_groups_checked": ZERO_WEEK_SOURCE_GROUPS,
        "configured_registry_source_count": len([item for item in registry if isinstance(item, dict) and item.get("enabled", False)]),
        "provider_queries_attempted": provider_queries,
        "official_pages_checked": [
            {
                "source_id": item.get("source_id"),
                "source_name": item.get("source_name"),
                "url": item.get("url"),
                "raw_count": item.get("raw_count"),
                "errors": item.get("errors", []),
                "warnings": item.get("warnings", []),
            }
            for item in official_pages
        ],
        "registry_sources_checked": registry_sources_checked,
        "gdelt_queries_attempted": provider_queries if not gdelt_queries else [
            {
                "query_group": item.get("query_group"),
                "query": item.get("query"),
                "result_count": item.get("result_count", 0),
                "error": item.get("error"),
                "cache_hit": item.get("cache_hit", False),
                "fallback_used": item.get("fallback_used", False),
            }
            for item in gdelt_queries
        ],
        "candidate_count": candidate_count,
        "accepted_candidate_count": accepted_candidate_count,
        "rejected_candidate_count": rejected_candidate_count,
        "rejected_candidates": rejected_candidates,
        "candidate_review_diagnostics": rejected_candidates,
        "candidate_rejection_counts": candidate_rejection_counts,
        "candidate_rejections_by_stage": _candidate_rejections_by_stage(rejected_candidates),
        "fetch_warnings": fetch_warnings,
        "provider_warnings": provider_warnings,
        "tls_or_network_warnings": unique_messages(tls_or_network_warnings),
        "manual_source_records_added": int(historical_report.get("manual_sources_loaded") or 0) > 0,
        "manual_sources_path": historical_report.get("manual_sources_path"),
        "manual_sources_valid": manual_valid,
        **fetch_metrics,
        "final_zero_story_result_is_credible": final_zero_story_result_is_credible,
        "final_reason": final_reason,
        "public_zero_story_wording": public_zero_story_wording,
    }
    report_path = ROOT / "output" / "dispatches" / "cascadia" / "weekly_gap_reports" / f"{edition_date}.json"
    if not dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if final_public_story_count == 0:
            _update_zero_story_public_manifests(edition_date, public_zero_story_wording, minimum_review_threshold_met, dry_run=False)
    report["weekly_gap_report_path"] = str(report_path)
    return report


def write_source_health_report(start: str, end: str, edition_date: str, aggregate: dict[str, object], dry_run: bool) -> dict[str, object]:
    historical_report = dict(aggregate.get("report") or {})
    registry_diagnostics = _registry_diagnostics(historical_report)
    edition_dir = ROOT / "output" / "site" / "cascadia" / "editions" / edition_date
    sources_manifest = read_json_list(edition_dir / "sources_manifest.json")
    curation_manifest = read_json_list(edition_dir / "curation_manifest.json")
    accepted_urls = {
        str(url)
        for story in curation_manifest
        if story.get("included_in_public_summary")
        for url in story.get("source_urls", [])
        if isinstance(url, str)
    }
    by_source_url_counts: dict[str, int] = {}
    for source in sources_manifest:
        url = str(source.get("source_url") or source.get("url") or "")
        if url:
            by_source_url_counts[url] = by_source_url_counts.get(url, 0) + 1

    entries: list[dict[str, object]] = []
    for item in registry_diagnostics:
        source_url = str(item.get("url") or "")
        candidate_count = by_source_url_counts.get(source_url, 0)
        accepted_count = 1 if source_url and source_url in accepted_urls else 0
        rejected_count = max(0, candidate_count - accepted_count)
        entries.append(
            {
                "source_id": item.get("source_id"),
                "url": source_url,
                "group": _source_group_for_diagnostic(item),
                "state_region": item.get("geographic_scope"),
                "source_type": item.get("source_type"),
                "fetch_success": _diagnostic_fetch_successful(item),
                "backend_used": item.get("selected_backend") or item.get("fetch_backend"),
                "status_code": item.get("status_code"),
                "failure_reason": _failure_reason(item) or item.get("failure_reason"),
                "last_successful_fetch": None,
                "candidate_count": candidate_count,
                "accepted_candidate_count": accepted_count,
                "rejected_candidate_count": rejected_count,
            }
        )
    payload = {
        "edition_date": edition_date,
        "coverage_start": start,
        "coverage_end": end,
        "sources": entries,
    }
    path = ROOT / "output" / "dispatches" / "cascadia" / "source_health" / f"{edition_date}.json"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["source_health_report_path"] = str(path)
    return payload


def create_manual_template_command(args: argparse.Namespace) -> dict[str, object]:
    start, end, _ = resolve_week_args(args)
    return create_manual_source_template(ROOT, local_date.fromisoformat(start), local_date.fromisoformat(end), force=args.force)


def validate_manual_sources_command(args: argparse.Namespace) -> dict[str, object]:
    start, end, _ = resolve_week_args(args)
    return validate_manual_sources(ROOT, local_date.fromisoformat(start), local_date.fromisoformat(end))


def source_gap_item(start: str, end: str, edition_date: str) -> dict[str, object]:
    week_start = local_date.fromisoformat(start)
    week_end = local_date.fromisoformat(end)
    folder = source_folder(ROOT, week_start, week_end)
    manual_path = manual_sources_path(ROOT, week_start, week_end)
    historical_path = folder / "historical_sources.json"
    report_path = folder / "historical_search_report.json"
    edition_manifest_path = ROOT / "output" / "site" / "cascadia" / "editions" / edition_date / "edition_manifest.json"
    manual_sources = read_json_list(manual_path)
    historical_sources = read_json_list(historical_path)
    registry_sources = [item for item in historical_sources if item.get("provider_id") == "registry"]
    gdelt_sources = [item for item in historical_sources if item.get("provider_id") == "gdelt"]
    historical_only = [item for item in historical_sources if item.get("provider_id") != "manual" and item.get("source_type") != "manual"]
    urls = {str(item.get("url") or item.get("source_url") or item.get("canonical_url") or "") for item in manual_sources + historical_sources}
    urls.discard("")
    public_story_count = 0
    if edition_manifest_path.exists():
        try:
            public_story_count = int((json.loads(edition_manifest_path.read_text(encoding="utf-8")) or {}).get("public_story_count") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            public_story_count = 0
    last_report: dict[str, object] | None = None
    if report_path.exists():
        try:
            last_report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            last_report = {"errors": ["historical_search_report.json is invalid JSON"]}
    total_source_count = len(urls) if urls else len(manual_sources) + len(historical_sources)
    weak_provider_count = 0
    if last_report:
        source_counts = last_report.get("source_count_by_provider") if isinstance(last_report.get("source_count_by_provider"), dict) else {}
        registry_count = int(source_counts.get("registry", len(registry_sources)) or 0)
        gdelt_count = int(source_counts.get("gdelt", len(gdelt_sources)) or 0)
        if "registry" in source_counts and registry_count == 0:
            weak_provider_count += 1
        if "gdelt" in source_counts and gdelt_count == 0:
            weak_provider_count += 1
    else:
        registry_count = len(registry_sources)
        gdelt_count = len(gdelt_sources)
    if total_source_count >= 4:
        action = "enough source records available"
    elif not manual_path.exists():
        action = "add manual_sources.json"
    elif last_report and int(last_report.get("registry_records_raw") or 0) > 0 and int(last_report.get("registry_records_saved") or 0) == 0:
        action = "registry found records but curation excluded them; inspect diagnostics"
    elif last_report and int(last_report.get("registry_sources_run") or 0) == 0 and int(last_report.get("registry_sources_planned") or 0) > 0:
        action = "check disabled registry sources"
    elif historical_path.exists() and len(historical_only) == 0 and len(manual_sources) == 0:
        action = "rerun historical search with refresh-cache"
    else:
        action = "provider sparse; manual supplement recommended"
    return {
        "coverage_start": start,
        "coverage_end": end,
        "coverage_label": format_coverage_label(start, end),
        "source_folder": str(folder),
        "manual_sources_exists": manual_path.exists(),
        "manual_source_count": len(manual_sources),
        "registry_source_count": registry_count,
        "gdelt_source_count": gdelt_count,
        "historical_source_count": len(historical_only),
        "total_source_count": total_source_count,
        "public_story_count": public_story_count,
        "weak_provider_count": weak_provider_count,
        "last_historical_search_report": str(report_path) if report_path.exists() else None,
        "recommended_action": action,
        "report_recommendation": last_report.get("recommendation") if isinstance(last_report, dict) else None,
    }


def run_source_gap_report(args: argparse.Namespace) -> dict[str, object]:
    if args.backfill_weeks:
        windows = completed_week_windows(args.date, args.backfill_weeks)
    else:
        windows = [resolve_week_args(args)]
    items = [source_gap_item(start, end, edition_date) for start, end, edition_date in windows]
    return {
        "ok": True,
        "date": args.date,
        "mode": "source-gap-report",
        "backfill_weeks": args.backfill_weeks,
        "weeks": items,
        "warnings": [],
        "errors": [],
    }


def run_weekly_public(args: argparse.Namespace, mode: str) -> dict[str, object]:
    start, end, edition_date = resolve_week_args(args)
    initial_public_story_count = manifest_public_story_count(edition_date)
    historical_search = args.historical_search or args.quality_weekly
    historical_provider = args.historical_provider
    max_historical_queries = args.max_historical_queries
    historical_delay_seconds = args.historical_delay_seconds
    if args.quality_weekly:
        historical_provider = historical_provider or "all"
        max_historical_queries = max_historical_queries or 8
        historical_delay_seconds = historical_delay_seconds if historical_delay_seconds is not None else 10
    if historical_search:
        aggregate = retrieve_historical_sources(
            ROOT,
            local_date.fromisoformat(start),
            local_date.fromisoformat(end),
            edition_date=edition_date,
            run_date=args.date,
            dry_run=args.dry_run,
            refresh_cache=args.refresh_cache,
            historical_provider=historical_provider,
            max_historical_queries=max_historical_queries,
            historical_delay_seconds=historical_delay_seconds,
        )
    else:
        aggregate = aggregate_weekly_curation(ROOT, args.date, local_date.fromisoformat(start), local_date.fromisoformat(end), edition_date=edition_date, dry_run=args.dry_run)
    result = run_pipeline(
        edition_date,
        ingest=False,
        normalize=False,
        curate=False,
        render=True,
        dry_run=args.dry_run,
        mode=mode,
        run_date=args.date,
        coverage_start=start,
        coverage_end=end,
        briefing_type="weekly",
    )
    result["weekly_aggregation"] = aggregate
    result["historical_search"] = bool(historical_search)
    if args.quality_weekly:
        quality_report = write_weekly_quality_report(start, end, edition_date, aggregate, result, args.dry_run)
        gap_report = write_zero_week_gap_report(start, end, edition_date, initial_public_story_count, aggregate, result, args.dry_run)
        source_health = write_source_health_report(start, end, edition_date, aggregate, args.dry_run)
        result["quality_weekly"] = True
        result["quality_weekly_report"] = quality_report
        result["weekly_quality_report_path"] = quality_report.get("weekly_quality_report_path")
        result["weekly_gap_report"] = gap_report
        result["weekly_gap_report_path"] = gap_report.get("weekly_gap_report_path")
        result["source_health_report"] = source_health
        result["source_health_report_path"] = source_health.get("source_health_report_path")
        result["warnings"] = unique_messages(list(result.get("warnings", [])) + list(quality_report.get("warnings", [])))
        result["errors"] = unique_messages(list(result.get("errors", [])) + list(quality_report.get("errors", [])))
    result["normalized_count"] = aggregate.get("normalized_count", result.get("normalized_count", 0))
    result["curated_count"] = aggregate.get("curated_count", result.get("curated_count", 0))
    result["warnings"] = unique_messages(list(aggregate.get("warnings", [])) + list(result.get("warnings", [])))
    result["errors"] = unique_messages(list(aggregate.get("errors", [])) + list(result.get("errors", [])))
    result["ok"] = bool(aggregate.get("ok")) and bool(result.get("ok")) and not result["errors"]
    return result


def run_weekly_backfill(args: argparse.Namespace, mode: str) -> dict[str, object]:
    results = []
    warnings: list[str] = []
    errors: list[str] = []
    for start, end, edition_date in completed_week_windows(args.date, args.backfill_weeks):
        initial_public_story_count = manifest_public_story_count(edition_date)
        historical_search = args.historical_search or args.quality_weekly
        if historical_search:
            aggregate = retrieve_historical_sources(
                ROOT,
                local_date.fromisoformat(start),
                local_date.fromisoformat(end),
                edition_date=edition_date,
                run_date=args.date,
                dry_run=args.dry_run,
                refresh_cache=args.refresh_cache,
                historical_provider=args.historical_provider or "all",
                max_historical_queries=args.max_historical_queries or (8 if args.quality_weekly else None),
                historical_delay_seconds=args.historical_delay_seconds if args.historical_delay_seconds is not None else (10 if args.quality_weekly else None),
            )
        elif args.from_existing_editions:
            aggregate = backfill_weekly_from_existing_editions(
                ROOT,
                args.date,
                local_date.fromisoformat(start),
                local_date.fromisoformat(end),
                edition_date=edition_date,
                dry_run=args.dry_run,
            )
        else:
            aggregate = aggregate_weekly_curation(
                ROOT,
                args.date,
                local_date.fromisoformat(start),
                local_date.fromisoformat(end),
                edition_date=edition_date,
                dry_run=args.dry_run,
            )
        result = run_pipeline(
            edition_date,
            ingest=False,
            normalize=False,
            curate=False,
            render=True,
            dry_run=args.dry_run,
            mode=mode,
            run_date=args.date,
            coverage_start=start,
            coverage_end=end,
            briefing_type="weekly",
        )
        result["weekly_aggregation"] = aggregate
        result["historical_search"] = bool(historical_search)
        if args.quality_weekly:
            quality_report = write_weekly_quality_report(start, end, edition_date, aggregate, result, args.dry_run)
            gap_report = write_zero_week_gap_report(start, end, edition_date, initial_public_story_count, aggregate, result, args.dry_run)
            source_health = write_source_health_report(start, end, edition_date, aggregate, args.dry_run)
            result["quality_weekly"] = True
            result["quality_weekly_report"] = quality_report
            result["weekly_quality_report_path"] = quality_report.get("weekly_quality_report_path")
            result["weekly_gap_report"] = gap_report
            result["weekly_gap_report_path"] = gap_report.get("weekly_gap_report_path")
            result["source_health_report"] = source_health
            result["source_health_report_path"] = source_health.get("source_health_report_path")
            result["warnings"] = unique_messages(list(result.get("warnings", [])) + list(quality_report.get("warnings", [])))
            result["errors"] = unique_messages(list(result.get("errors", [])) + list(quality_report.get("errors", [])))
        result["normalized_count"] = aggregate.get("normalized_count", result.get("normalized_count", 0))
        result["curated_count"] = aggregate.get("curated_count", result.get("curated_count", 0))
        result["warnings"] = unique_messages(list(aggregate.get("warnings", [])) + list(result.get("warnings", [])))
        result["errors"] = unique_messages(list(aggregate.get("errors", [])) + list(result.get("errors", [])))
        result["ok"] = bool(aggregate.get("ok")) and bool(result.get("ok")) and not result["errors"]
        warnings.extend(result["warnings"])
        errors.extend(result["errors"])
        results.append(result)
    return {
        "ok": not errors and all(bool(result.get("ok")) for result in results),
        "date": args.date,
        "run_date": args.date,
        "mode": f"{mode}-backfill",
        "backfill_weeks": args.backfill_weeks,
        "from_existing_editions": bool(args.from_existing_editions),
        "historical_search": bool(args.historical_search),
        "edition_dates": [result.get("edition_date") for result in results],
        "weekly_editions": results,
        "warnings": warnings,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone Cascadia Signal pipeline and Cascadia Briefing renderer.")
    parser.add_argument("--date", default=local_date.today().isoformat())
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--curate", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--weekly-public", action="store_true")
    parser.add_argument("--week-start")
    parser.add_argument("--week-end")
    parser.add_argument("--archive-week")
    parser.add_argument("--backfill-weeks", type=int)
    parser.add_argument("--from-existing-editions", action="store_true")
    parser.add_argument("--historical-search", action="store_true")
    parser.add_argument("--quality-weekly", action="store_true", help="Run the recommended quality weekly free-source build preset.")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--historical-provider", default="all")
    parser.add_argument("--max-historical-queries", type=int)
    parser.add_argument("--historical-delay-seconds", type=float)
    parser.add_argument("--create-manual-template", action="store_true")
    parser.add_argument("--validate-manual-sources", action="store_true")
    parser.add_argument("--source-gap-report", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_all = args.all or not any([args.ingest, args.normalize, args.curate, args.render, args.daily, args.weekly, args.weekly_public])
    try:
        if args.daily:
            result = run_pipeline(args.date, ingest=True, normalize=True, curate=True, render=False, dry_run=args.dry_run, mode="daily", briefing_type="daily")
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.create_manual_template:
            result = create_manual_template_command(args)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.validate_manual_sources:
            result = validate_manual_sources_command(args)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.source_gap_report:
            result = run_source_gap_report(args)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.backfill_weeks and not args.weekly_public:
            raise ValueError("--backfill-weeks requires --weekly-public")
        if args.backfill_weeks and (args.week_start or args.week_end or args.archive_week):
            raise ValueError("--backfill-weeks cannot be combined with --week-start/--week-end or --archive-week")
        if args.from_existing_editions and not args.backfill_weeks:
            raise ValueError("--from-existing-editions requires --backfill-weeks")
        if args.from_existing_editions and args.historical_search:
            raise ValueError("--from-existing-editions cannot be combined with --historical-search")
        if args.quality_weekly and not args.weekly_public:
            raise ValueError("--quality-weekly requires --weekly-public")
        if args.weekly or args.weekly_public:
            if args.backfill_weeks:
                result = run_weekly_backfill(args, "weekly-public")
                print(json.dumps(result, indent=2))
                return 0 if result["ok"] else 1
            result = run_weekly_public(args, "weekly-public" if args.weekly_public else "weekly")
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 1
    result = run_pipeline(
        args.date,
        ingest=args.ingest or run_all,
        normalize=args.normalize or run_all,
        curate=args.curate or run_all,
        render=args.render or run_all,
        dry_run=args.dry_run,
        mode="all" if run_all else "custom",
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
