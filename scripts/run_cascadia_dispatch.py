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
from bluefern_dispatches.cascadia_ingest import ingest_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import render_cascadia_edition
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, containing_week, explicit_week, previous_completed_week
from bluefern_dispatches.shared_records import update_shared_records


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


def run_weekly_public(args: argparse.Namespace, mode: str) -> dict[str, object]:
    start, end, edition_date = resolve_week_args(args)
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
    result["normalized_count"] = aggregate.get("normalized_count", result.get("normalized_count", 0))
    result["curated_count"] = aggregate.get("curated_count", result.get("curated_count", 0))
    result["warnings"] = list(aggregate.get("warnings", [])) + list(result.get("warnings", []))
    result["errors"] = list(aggregate.get("errors", [])) + list(result.get("errors", []))
    result["ok"] = bool(aggregate.get("ok")) and bool(result.get("ok")) and not result["errors"]
    return result


def run_weekly_backfill(args: argparse.Namespace, mode: str) -> dict[str, object]:
    results = []
    warnings: list[str] = []
    errors: list[str] = []
    for start, end, edition_date in completed_week_windows(args.date, args.backfill_weeks):
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
        result["normalized_count"] = aggregate.get("normalized_count", result.get("normalized_count", 0))
        result["curated_count"] = aggregate.get("curated_count", result.get("curated_count", 0))
        result["warnings"] = list(aggregate.get("warnings", [])) + list(result.get("warnings", []))
        result["errors"] = list(aggregate.get("errors", [])) + list(result.get("errors", []))
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_all = args.all or not any([args.ingest, args.normalize, args.curate, args.render, args.daily, args.weekly, args.weekly_public])
    try:
        if args.daily:
            result = run_pipeline(args.date, ingest=True, normalize=True, curate=True, render=False, dry_run=args.dry_run, mode="daily", briefing_type="daily")
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.backfill_weeks and not args.weekly_public:
            raise ValueError("--backfill-weeks requires --weekly-public")
        if args.backfill_weeks and (args.week_start or args.week_end or args.archive_week):
            raise ValueError("--backfill-weeks cannot be combined with --week-start/--week-end or --archive-week")
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
