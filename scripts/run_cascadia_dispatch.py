from pathlib import Path
import argparse
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_curate import curate_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import render_cascadia_edition
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.shared_records import update_shared_records


def run_pipeline(date: str, ingest: bool, normalize: bool, curate: bool, render: bool, dry_run: bool, mode: str = "custom") -> dict[str, object]:
    warnings: list[str] = []
    errors: list[str] = []
    output_paths: dict[str, str] = {}
    manifest_paths: dict[str, str] = {}
    detail_output_paths: dict[str, str] = {}
    shared_record_paths: dict[str, str] = {}
    raw_count = normalized_count = curated_count = public_story_count = 0
    detail_count = 0

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
        result = render_cascadia_edition(ROOT, date, dry_run=dry_run)
        public_story_count = int(result.get("public_story_count", public_story_count))
        detail_count = int(result.get("detail_count", detail_count))
        output_paths.update(result.get("output_paths", {}))
        detail_output_paths.update(result.get("detail_output_paths", {}))
        manifest_paths.update(result.get("manifest_paths", {}))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
    if (curate or render) and not render and not errors:
        result = write_cascadia_signal_package(ROOT, date, dry_run=dry_run)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the standalone Cascadia Signal pipeline and Cascadia Briefing renderer.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--curate", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--weekly-public", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_all = args.all or not any([args.ingest, args.normalize, args.curate, args.render, args.daily, args.weekly_public])
    if args.daily:
        result = run_pipeline(args.date, ingest=True, normalize=True, curate=True, render=False, dry_run=args.dry_run, mode="daily")
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.weekly_public:
        result = run_pipeline(args.date, ingest=False, normalize=False, curate=False, render=True, dry_run=args.dry_run, mode="weekly-public")
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
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
