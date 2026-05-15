from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_sources import collect_gaza_sources, validate_source_records
from scripts.run_gaza_dispatch import discover_edition_dates, read_json, render_archive_index_rss, run_gaza_dispatch, validate_date


DISPATCH_SLUG = "gaza"
SOURCE_MODES = ("manual", "auto", "both")
COLLECTION_CONTEXT_NAME = "source_collection_context.json"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _report_path(root: Path) -> Path:
    return root / "output" / "dispatches" / DISPATCH_SLUG / "backfill" / f"gaza_backfill_report_{_utc_stamp()}.json"


def _manual_source_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.json"


def _load_manual_sources(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or object containing a sources list")
    rows = [record for record in records if isinstance(record, dict)]
    errors = validate_source_records(rows, min_sources=0)
    if errors:
        raise ValueError("; ".join(errors))
    return rows


def _dedupe_source_rows(rows: list[dict[str, Any]], max_sources: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("url") or "").strip().lower(), str(row.get("title") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= max_sources:
            break
    return out


def _context_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / COLLECTION_CONTEXT_NAME


def _expand_dates(single_date: str | None, dates: list[str], start_date: str | None, end_date: str | None) -> list[str]:
    mode_count = int(bool(single_date)) + int(bool(dates)) + int(bool(start_date or end_date))
    if mode_count != 1:
        raise ValueError("provide exactly one mode: --date, --dates, or --start-date/--end-date")
    if single_date:
        return [validate_date(single_date)]
    if dates:
        return sorted({validate_date(value) for value in dates})
    if not start_date or not end_date:
        raise ValueError("--start-date and --end-date are required together")
    start = date.fromisoformat(validate_date(start_date))
    end = date.fromisoformat(validate_date(end_date))
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    out: list[str] = []
    cursor = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _resolve_sources(root: Path, edition_date: str, source_mode: str, max_sources: int) -> tuple[str, Path | None, list[dict[str, Any]], dict[str, Any], list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    diagnostics: dict[str, Any] = {}

    manual_path = _manual_source_path(root, edition_date)
    manual_rows: list[dict[str, Any]] = []
    manual_valid = False
    if source_mode in {"manual", "both"} and manual_path.exists():
        try:
            rows = _load_manual_sources(manual_path)
        except Exception as exc:  # noqa: BLE001
            if source_mode == "manual":
                errors.append(str(exc))
                return "manual", manual_path, [], diagnostics, warnings, errors
            warnings.append(f"manual source file invalid: {exc}")
        else:
            manual_rows = rows[:max_sources]
            manual_valid = True
            if source_mode == "manual":
                _context_path(root, edition_date).parent.mkdir(parents=True, exist_ok=True)
                _context_path(root, edition_date).write_text(
                    json.dumps(
                        {
                            "source_mode": "manual",
                            "providers_configured": ["manual_sources_json"],
                            "providers_attempted": ["manual_sources_json"],
                            "providers_successful": ["manual_sources_json"] if manual_rows else [],
                            "provider_failures": [] if manual_rows else [{"source_id": "manual_sources_json", "reason": "zero_candidates", "status": "no_candidates"}],
                            "provider_diagnostics": [{"source_id": "manual_sources_json", "status": "ok" if manual_rows else "no_candidates", "raw_candidates": len(manual_rows)}],
                            "stage_counts": {"registry_sources": 1, "enabled_providers_configured": 1, "providers_attempted": 1, "providers_successful": 1 if manual_rows else 0, "raw_candidates": len(manual_rows)},
                            "raw_candidate_count": len(manual_rows),
                            "accepted_candidate_count_before_dedupe": len(manual_rows),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return "manual", manual_path, manual_rows, diagnostics, warnings, errors

    if source_mode == "manual":
        errors.append(f"manual source file is required: {manual_path}")
        return "manual", manual_path, [], diagnostics, warnings, errors

    try:
        auto = collect_gaza_sources(
            root,
            edition_date,
            max_sources=max_sources,
            min_sources=0,
            output_filename="manual_sources.json",
            prefer_manual=False,
        )
    except Exception as exc:  # noqa: BLE001
        if source_mode == "both" and manual_valid and manual_rows:
            warnings.append(f"auto source collection unavailable; using manual sources only: {exc}")
            _context_path(root, edition_date).parent.mkdir(parents=True, exist_ok=True)
            _context_path(root, edition_date).write_text(
                json.dumps(
                    {
                        "source_mode": "both",
                        "providers_configured": ["manual_sources_json"],
                        "providers_attempted": ["manual_sources_json"],
                        "providers_successful": ["manual_sources_json"],
                        "provider_failures": [],
                        "provider_diagnostics": [{"source_id": "manual_sources_json", "status": "ok", "raw_candidates": len(manual_rows)}],
                        "stage_counts": {"registry_sources": 1, "enabled_providers_configured": 1, "providers_attempted": 1, "providers_successful": 1, "raw_candidates": len(manual_rows)},
                        "raw_candidate_count": len(manual_rows),
                        "accepted_candidate_count_before_dedupe": len(manual_rows),
                        "enabled_auto_provider_count": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return "both", manual_path, manual_rows, diagnostics, warnings, errors
        errors.append(str(exc))
        return "auto", None, [], diagnostics, warnings, errors
    warnings.extend(list(auto.get("warnings") or []))
    errors.extend(list(auto.get("errors") or []))
    diagnostics = auto
    auto_rows = list(auto.get("sources") or [])
    rows = list(auto_rows)
    source_mode_used = "auto"
    source_path = Path(str(auto["source_file"])) if auto.get("source_file") else _manual_source_path(root, edition_date)
    if source_mode == "both":
        source_mode_used = "both"
        rows = _dedupe_source_rows([*auto_rows, *manual_rows], max_sources)
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        source_path = manual_path
    providers_configured = list(auto.get("providers_configured") or [])
    providers_attempted = list(auto.get("providers_attempted") or [])
    providers_successful = list(auto.get("providers_successful") or [])
    provider_diagnostics = list(auto.get("provider_diagnostics") or [])
    if source_mode == "both":
        providers_configured = [*providers_configured, "manual_sources_json"]
        providers_attempted = [*providers_attempted, "manual_sources_json"]
        if manual_valid and manual_rows:
            providers_successful = sorted(set([*providers_successful, "manual_sources_json"]))
        provider_diagnostics.append({"source_id": "manual_sources_json", "status": "ok" if manual_valid and manual_rows else "no_candidates", "raw_candidates": len(manual_rows)})
    _context_path(root, edition_date).parent.mkdir(parents=True, exist_ok=True)
    _context_path(root, edition_date).write_text(
        json.dumps(
            {
                "source_mode": source_mode_used,
                "providers_configured": providers_configured,
                "providers_attempted": providers_attempted,
                "providers_successful": providers_successful,
                "provider_failures": list(auto.get("failed_source_ids") or []),
                "provider_diagnostics": provider_diagnostics,
                "skipped_providers": list(auto.get("skipped_providers") or []),
                "working_providers": list(auto.get("working_providers") or []),
                "stage_counts": dict(auto.get("stage_counts") or {}),
                "rejected_by_reason": dict(auto.get("rejected_by_reason") or {}),
                "raw_candidate_count": int((auto.get("stage_counts") or {}).get("raw_candidates") or 0) + (len(manual_rows) if source_mode == "both" else 0),
                "accepted_candidate_count_before_dedupe": int((auto.get("stage_counts") or {}).get("accepted_before_rank") or 0) + (len(manual_rows) if source_mode == "both" else 0),
                "enabled_auto_provider_count": int((auto.get("stage_counts") or {}).get("enabled_providers_configured") or 0),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return source_mode_used, source_path, rows, diagnostics, warnings, errors


def _collection_report_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json"


def _dedupe_report_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "dedupe_report.json"


def _public_url(edition_date: str) -> str:
    return f"https://dispatches.thebluefernco.com/gaza/editions/{edition_date}/"


def run_backfill(
    root: Path,
    requested_dates: list[str],
    *,
    source_mode: str,
    from_manual_sources: bool,
    publish_local: bool,
    allow_partial: bool,
    max_sources: int,
) -> dict[str, Any]:
    dates = sorted([validate_date(value) for value in requested_dates])
    per_date: list[dict[str, Any]] = []
    completed_dates: list[str] = []
    failed_dates: list[str] = []

    for edition_date in dates:
        source_mode_used, source_path, source_rows, source_diag, source_warnings, source_errors = _resolve_sources(
            root, edition_date, source_mode, max_sources
        )
        command = f"{sys.executable} scripts/run_gaza_dispatch.py --date {edition_date} --historical --from-manual-sources --all"
        result: dict[str, Any]
        if source_rows and not source_errors:
            result = run_gaza_dispatch(
                root,
                edition_date,
                from_manual_sources=True,
                dry_run=False,
                render=bool(publish_local),
                all_steps=True,
            )
        else:
            result = {
                "ok": False,
                "source_count": 0,
                "story_count": 0,
                "public_exposed": False,
                "warnings": source_warnings,
                "errors": source_errors or ["No valid traceable Gaza sources were collected or loaded; refusing to publish."],
            }
            # refresh list pages to ensure failed/unlinked editions remain excluded
            render_archive_index_rss(root, edition_date, dry_run=False, wrote=[], include_current=False)

        collection_path = _collection_report_path(root, edition_date)
        dedupe_path = _dedupe_report_path(root, edition_date)
        collection = read_json(collection_path) if collection_path.exists() else {}
        dedupe = read_json(dedupe_path) if dedupe_path.exists() else {}

        archive_dates = discover_edition_dates(root / "output" / "site")
        archive_linked = edition_date in archive_dates

        row = {
            "date": edition_date,
            "command_run": command,
            "source_mode": source_mode_used,
            "providers_attempted": int((collection or {}).get("providers_attempted_count") or ((source_diag.get("stage_counts") or {}).get("providers_attempted") or 0)),
            "raw_candidates": int((collection or {}).get("raw_candidate_count") or ((source_diag.get("stage_counts") or {}).get("raw_candidates") or 0)),
            "normalized_candidates": int((collection or {}).get("normalized_candidate_count") or ((source_diag.get("stage_counts") or {}).get("normalized_candidates") or 0)),
            "accepted_before_dedupe": int((collection or {}).get("accepted_candidate_count_before_dedupe") or 0),
            "kept_after_dedupe": int((collection or {}).get("kept_after_dedupe") or 0),
            "suppressed_duplicates": int((collection or {}).get("suppressed_after_dedupe") or (dedupe.get("suppressed_candidate_count") or 0)),
            "rejected_counts_by_reason": dict((collection or {}).get("rejection_counts_by_reason") or (source_diag.get("rejected_by_reason") or {})),
            "source_count": int(result.get("source_count") or 0),
            "story_count": int(result.get("story_count") or 0),
            "generated": bool(result.get("ok")),
            "public_exposed": bool(result.get("public_exposed")),
            "archive_linked": bool(archive_linked),
            "errors": list(result.get("errors") or []),
            "warnings": [*source_warnings, *list(result.get("warnings") or [])],
            "collection_report_path": str(collection_path),
            "dedupe_report_path": str(dedupe_path),
            "local_output_path": str(root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date / "index.html"),
            "public_url": _public_url(edition_date),
            "source_file": str(source_path) if source_path else None,
        }
        if row["generated"] and row["public_exposed"]:
            completed_dates.append(edition_date)
        else:
            failed_dates.append(edition_date)
            if not allow_partial:
                per_date.append(row)
                break
        per_date.append(row)

    # rebuild list pages from listable editions only
    if dates:
        render_archive_index_rss(root, dates[-1], dry_run=False, wrote=[], include_current=False)

    ok = not failed_dates or allow_partial
    report = {
        "ok": ok,
        "requested_dates": dates,
        "completed_dates": completed_dates,
        "failed_dates": failed_dates,
        "allow_partial": allow_partial,
        "publish_local": publish_local,
        "source_mode": source_mode,
        "from_manual_sources": from_manual_sources,
        "per_date": per_date,
    }
    path = _report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(path)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Gaza editions with staged source collection and safe listability checks.")
    parser.add_argument("--date")
    parser.add_argument("--dates", nargs="+", default=[])
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--source-mode", choices=SOURCE_MODES, default="both")
    parser.add_argument("--from-manual-sources", action="store_true")
    parser.add_argument("--auto-sources", action="store_true")
    parser.add_argument("--publish-local", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--max-sources", type=int, default=12)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        dates = _expand_dates(args.date, list(args.dates), args.start_date, args.end_date)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 1

    source_mode = args.source_mode
    if args.from_manual_sources:
        source_mode = "manual"
    if args.auto_sources:
        source_mode = "auto"

    report = run_backfill(
        ROOT,
        dates,
        source_mode=source_mode,
        from_manual_sources=bool(args.from_manual_sources),
        publish_local=bool(args.publish_local),
        allow_partial=bool(args.allow_partial),
        max_sources=int(args.max_sources),
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
