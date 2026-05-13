from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_american_pressure_dispatch import (  # noqa: E402
    DISPATCH_SLUG,
    manual_source_path,
    run_american_pressure_dispatch,
    validate_date,
)


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _report_path(root: Path) -> Path:
    return root / "output" / "dispatches" / DISPATCH_SLUG / "backfill" / f"backfill_report_{_utc_stamp()}.json"


def _build_template_record(date_str: str) -> dict[str, str]:
    return {
        "source_record_id": f"replace-with-unique-source-record-id-{date_str}",
        "source_id": "replace-with-registry-or-editorial-source-id",
        "title": "REPLACE WITH SOURCE TITLE",
        "url": "https://replace-with-source-url.example",
        "publisher": "REPLACE WITH PUBLISHER",
        "published_at": f"{date_str}T00:00:00Z",
        "retrieved_at": f"{date_str}T00:00:00Z",
        "summary_or_snippet": "REPLACE WITH SOURCE-BACKED SUMMARY SNIPPET",
        "source_type": "official_report_page",
        "region_scope": "United States",
        "category_hint": "food_pressure",
        "reliability_tier": "official_primary",
    }


def write_template(root: Path, date_str: str) -> Path:
    validate_date(date_str)
    target = manual_source_path(root, date_str).with_name("manual_sources.example.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps([_build_template_record(date_str)], indent=2), encoding="utf-8")
    return target


def run_backfill(
    root: Path,
    dates: list[str],
    *,
    publish: bool,
    dry_run: bool,
    from_manual_sources: bool,
    allow_partial: bool,
) -> dict[str, Any]:
    requested_dates = [validate_date(value) for value in dates]
    completed_dates: list[str] = []
    failed_dates: list[str] = []
    per_date: dict[str, dict[str, Any]] = {}
    for edition_date in requested_dates:
        source_path = manual_source_path(root, edition_date)
        try:
            result = run_american_pressure_dispatch(
                root,
                edition_date,
                publish=publish,
                dry_run=dry_run,
                from_manual_sources=from_manual_sources,
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "source_count": 0,
                "story_count": 0,
                "generated": False,
                "errors": [str(exc)],
                "warnings": [],
            }
        ok = bool(result.get("ok"))
        if ok:
            completed_dates.append(edition_date)
        else:
            failed_dates.append(edition_date)
        per_date[edition_date] = {
            "date": edition_date,
            "source_file": str(source_path),
            "source_count": int(result.get("source_count") or 0),
            "story_count": int(result.get("story_count") or 0),
            "generated": bool(result.get("generated")),
            "errors": list(result.get("errors") or []),
            "warnings": list(result.get("warnings") or []),
            "local_edition_path": str(root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date / "index.html"),
            "public_url": f"https://dispatches.thebluefernco.com/{DISPATCH_SLUG}/editions/{edition_date}/",
        }
    ok = not failed_dates or allow_partial
    report = {
        "ok": ok,
        "requested_dates": requested_dates,
        "completed_dates": completed_dates,
        "failed_dates": failed_dates,
        "allow_partial": allow_partial,
        "publish": publish,
        "dry_run": dry_run,
        "from_manual_sources": from_manual_sources,
        "per_date": per_date,
    }
    report_path = _report_path(root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill American Pressure editions using manual source records only.")
    parser.add_argument("--date", action="append", default=[], help="Single edition date; repeat for multiple.")
    parser.add_argument("--dates", nargs="+", default=[], help="Multiple edition dates.")
    parser.add_argument("--publish", action="store_true", help="Update public archive/index/rss while running each date.")
    parser.add_argument("--dry-run", action="store_true", help="Run each date in dry-run mode.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Require manual source files per date.")
    parser.add_argument("--allow-partial", action="store_true", help="Return success status even when some dates fail.")
    parser.add_argument("--write-template", help="Write manual_sources.example.json for a date and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_template:
        path = write_template(ROOT, args.write_template)
        print(json.dumps({"ok": True, "template_path": str(path)}, indent=2))
        return 0
    requested = [*args.date, *args.dates]
    if not requested:
        print(json.dumps({"ok": False, "errors": ["at least one --date or --dates value is required"]}, indent=2))
        return 1
    report = run_backfill(
        ROOT,
        requested,
        publish=bool(args.publish),
        dry_run=bool(args.dry_run),
        from_manual_sources=bool(args.from_manual_sources),
        allow_partial=bool(args.allow_partial),
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
