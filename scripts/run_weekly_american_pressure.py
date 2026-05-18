from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PAGES_REPO = ROOT / "bluefern-dispatches-pages"
PAGES_BRANCH = "gh-pages"
BASE_PUBLIC_URL = "https://dispatches.thebluefernco.com/american-pressure/"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_american_pressure_dispatch import run_american_pressure_dispatch, validate_date  # noqa: E402
from scripts.check_american_pressure_weekly_readiness import build_readiness_report  # noqa: E402


def _completed_saturday_from(today: date) -> date:
    days_since_saturday = (today.weekday() - 5) % 7
    if days_since_saturday == 0:
        days_since_saturday = 7
    return today - timedelta(days=days_since_saturday)


def _resolve_week_ending(raw_date: str | None, raw_week_ending: str | None) -> str:
    if raw_date and raw_week_ending:
        raise ValueError("use either --date or --week-ending, not both")
    if raw_date:
        return validate_date(raw_date)
    if raw_week_ending:
        if raw_week_ending == "previous-saturday":
            return _completed_saturday_from(date.today()).isoformat()
        return validate_date(raw_week_ending)
    return _completed_saturday_from(date.today()).isoformat()


def _week_start_date(week_end: str) -> str:
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    return (end - timedelta(days=6)).isoformat()


def _display_date_range(week_end: str) -> str:
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    start = end - timedelta(days=6)
    start_month = start.strftime("%B")
    end_month = end.strftime("%B")
    return f"{start_month} {start.day}–{end_month} {end.day}, {end.year}"


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    days = (end - start).days
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


def _run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd or ROOT), check=False, text=True, capture_output=True, encoding="utf-8")


def _parse_json_stdout(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    return json.loads(text)


def _extract_dates(text: str) -> list[str]:
    return re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)


def _run_quality_gate(edition_date: str, manifest: dict[str, Any], readiness: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if readiness.get("weekly_publish_recommended") is not True:
        errors.extend([str(item) for item in (readiness.get("reasons_if_not_recommended") or [])])
    if int(manifest.get("story_count") or 0) < 4:
        errors.append("story_count below minimum quality gate (need >=4).")
    if int(manifest.get("story_plus_data_count") or 0) < 3:
        errors.append("story_plus_data_count below minimum quality gate (need >=3).")
    html_path = ROOT / "output" / "site" / "american-pressure" / "editions" / edition_date / "index.html"
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8", errors="replace")
        for bad in ("Structure, Rejecting", "In US,"):
            if bad in content:
                errors.append(f"public prose contains malformed phrase: {bad}")
    return errors


def _validate_pages_view_for_date(edition_date: str) -> list[str]:
    errors: list[str] = []
    required = {
        "index": PAGES_REPO / "american-pressure" / "index.html",
        "archive": PAGES_REPO / "american-pressure" / "archive.html",
        "rss": PAGES_REPO / "american-pressure" / "rss.xml",
        "dashboard": PAGES_REPO / "american-pressure" / "dashboard" / "index.html",
    }
    for label, path in required.items():
        if not path.exists():
            errors.append(f"missing Pages file: {path}")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if edition_date not in content:
            errors.append(f"american-pressure/{label} does not reference expected week-ending date {edition_date}")
        for found in _extract_dates(content):
            if found > edition_date:
                errors.append(f"stale future edition exposed in {path}: {found}")
    return errors


def _publish_pages(edition_date: str) -> tuple[bool, list[str]]:
    cmd = [
        sys.executable,
        "scripts\\publish_github_pages.py",
        "--pages-repo",
        str(PAGES_REPO),
        "--pages-branch",
        PAGES_BRANCH,
        "--expect-date",
        edition_date,
        "--expect-dispatch",
        "american-pressure",
        "--only-dispatch",
        "american-pressure",
        "--commit",
        "--no-push",
    ]
    result = _run_cmd(cmd)
    if result.returncode != 0:
        return False, [result.stderr.strip() or result.stdout.strip() or "Pages publish failed"]
    payload = _parse_json_stdout(result.stdout)
    errors = [str(err) for err in (payload.get("errors") or [])]
    if payload.get("ok") is not True:
        errors.append("Pages publish did not report ok: true")
    errors.extend(_validate_pages_view_for_date(edition_date))
    return not errors, errors


def _push_pages() -> tuple[bool, str]:
    if PAGES_REPO.resolve() != (ROOT / "bluefern-dispatches-pages").resolve():
        return False, "push must run from bluefern-dispatches-pages"
    push = _run_cmd(["git", "push", "origin", PAGES_BRANCH], cwd=PAGES_REPO)
    if push.returncode != 0:
        return False, push.stderr.strip() or push.stdout.strip() or "git push failed"
    return True, "Live GitHub Pages pushed from bluefern-dispatches-pages."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run weekly American Pressure pipeline.")
    parser.add_argument("--date", help="Alias for explicit weekly edition date (YYYY-MM-DD).")
    parser.add_argument("--week-ending", help="Week-ending date (YYYY-MM-DD) or previous-saturday.")
    parser.add_argument("--source-mode", choices=("manual", "auto", "both"), default="both")
    parser.add_argument("--publish", action="store_true", help="Update local Pages repo copy/commit only.")
    parser.add_argument("--push", action="store_true", help="Push local Pages repo to origin gh-pages (requires --publish).")
    parser.add_argument("--init-candidates", action="store_true", help="Initialize daily candidate source files.")
    parser.add_argument("--start-date", help="Start date for --init-candidates (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End date for --init-candidates (YYYY-MM-DD).")
    parser.add_argument("--include-approved-candidates", action="store_true", help="Merge only approved daily candidates from the weekly window.")
    parser.add_argument("--allow-thin-edition", action="store_true", help="Override weekly quality gate and allow publish.")
    parser.add_argument("--force-regenerate", action="store_true", help="Force rewrite/timestamp refresh for edition output files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        edition_date = _resolve_week_ending(args.date, args.week_ending)
        computed_week_start = _week_start_date(edition_date)
        computed_display_date_range = _display_date_range(edition_date)
        output: dict[str, Any] = {
            "week_start_date": computed_week_start,
            "week_end_date": edition_date,
            "display_date_range": computed_display_date_range,
            "source_count": 0,
            "story_count": 0,
            "story_plus_data_count": 0,
            "baseline_only_count": 0,
            "missing_required_current_development_pillars": [],
            "collection_gap_pillars": [],
            "pages_repo_updated": False,
            "pushed": False,
            "public_url": BASE_PUBLIC_URL,
            "ok": False,
            "warnings": [],
            "errors": [],
        }
        if args.push and not args.publish:
            raise ValueError("--push requires --publish")

        if args.init_candidates:
            if not args.start_date or not args.end_date:
                raise ValueError("--init-candidates requires --start-date and --end-date")
            days = _date_range(validate_date(args.start_date), validate_date(args.end_date))
            initialized: list[str] = []
            for day in days:
                result = run_american_pressure_dispatch(
                    ROOT,
                    day,
                    publish=False,
                    dry_run=False,
                    from_manual_sources=False,
                    source_mode=args.source_mode,
                    init_daily_candidates=True,
                )
                path = str(result.get("daily_candidate_path") or "")
                if path:
                    initialized.append(path)
            output["ok"] = True
            output["initialized_candidate_files"] = initialized
            print(json.dumps(output, indent=2))
            return 0

        run = run_american_pressure_dispatch(
            ROOT,
            edition_date,
            publish=False,
            dry_run=False,
            from_manual_sources=False,
            source_mode=args.source_mode,
            include_approved_candidates=bool(args.include_approved_candidates),
            force_regenerate=bool(args.force_regenerate),
        )
        manifest_path = ROOT / "output" / "dispatches" / "american-pressure" / "editions" / edition_date / "edition_manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        output.update(
            {
                "week_start_date": str(manifest.get("week_start_date") or computed_week_start),
                "display_date_range": str(manifest.get("display_date_range") or computed_display_date_range),
                "source_count": int(manifest.get("source_count") or run.get("source_count") or 0),
                "story_count": int(manifest.get("story_count") or run.get("story_count") or 0),
                "story_plus_data_count": int(manifest.get("story_plus_data_count") or 0),
                "baseline_only_count": int(manifest.get("baseline_only_count") or 0),
                "missing_required_current_development_pillars": list(manifest.get("missing_required_current_development_pillars") or []),
                "collection_gap_pillars": list(manifest.get("collection_gap_pillars") or []),
                "public_url": str(manifest.get("public_url") or f"{BASE_PUBLIC_URL}editions/{edition_date}/"),
                "warnings": list(run.get("warnings") or []),
                "errors": list(run.get("errors") or []),
            }
        )
        if run.get("ok") is not True:
            print(json.dumps(output, indent=2))
            return 1

        if args.publish:
            readiness = build_readiness_report(edition_date)
            gate_errors = _run_quality_gate(edition_date, manifest, readiness)
            if gate_errors and not args.allow_thin_edition:
                output["errors"].extend(gate_errors)
                output["errors"].append("weekly publish blocked by readiness/quality gate (pass --allow-thin-edition to override).")
                print(json.dumps(output, indent=2))
                return 1

        if args.publish:
            rerun = run_american_pressure_dispatch(
                ROOT,
                edition_date,
                publish=True,
                dry_run=False,
                from_manual_sources=False,
                source_mode=args.source_mode,
                include_approved_candidates=bool(args.include_approved_candidates),
                force_regenerate=bool(args.force_regenerate),
            )
            if rerun.get("ok") is not True:
                output["errors"].extend(list(rerun.get("errors") or []))
                print(json.dumps(output, indent=2))
                return 1
            published_ok, publish_errors = _publish_pages(edition_date)
            output["pages_repo_updated"] = published_ok
            if publish_errors:
                output["errors"].extend(publish_errors)
                print(json.dumps(output, indent=2))
                return 1
            print("Local Pages repo updated; live site not pushed. Add --push to publish live.")
            if args.push:
                pushed, push_message = _push_pages()
                output["pushed"] = pushed
                if not pushed:
                    output["errors"].append(push_message)
                    print(json.dumps(output, indent=2))
                    return 1
                print(push_message)

        output["ok"] = True
        print(json.dumps(output, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "errors": [str(exc)], "pages_repo_updated": False, "pushed": False}
        print(json.dumps(result, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

