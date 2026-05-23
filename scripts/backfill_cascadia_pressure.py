from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as local_date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections import Counter


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_historical_search import retrieve_historical_sources  # noqa: E402


def _parse_date(value: str) -> local_date:
    return local_date.fromisoformat(value)


def _completed_week_windows(start: local_date, end: local_date) -> list[tuple[str, str, str]]:
    windows: list[tuple[str, str, str]] = []
    cursor = start
    while cursor.weekday() != 5:
        cursor += timedelta(days=1)
    while cursor <= end:
        week_start = cursor - timedelta(days=6)
        if week_start >= start and cursor <= end:
            windows.append((week_start.isoformat(), cursor.isoformat(), cursor.isoformat()))
        cursor += timedelta(days=7)
    return windows


def run_backfill(
    root: Path,
    start_date: str,
    end_date: str,
    *,
    max_per_source: int = 10,
    weekly: bool = False,
    write: bool = False,
    dry_run: bool = False,
    allow_insecure_ssl: bool = False,
) -> dict[str, Any]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    windows = _completed_week_windows(start, end) if weekly else [(start.isoformat(), end.isoformat(), end.isoformat())]
    effective_dry_run = dry_run or not write
    if allow_insecure_ssl and write and not dry_run:
        raise ValueError("Refusing to write publishable output with --allow-insecure-ssl. Use --dry-run for diagnostics only.")
    os.environ["CASCADIA_SSL_MODE"] = "certifi"
    os.environ["CASCADIA_ALLOW_INSECURE_SSL"] = "1" if allow_insecure_ssl else "0"
    runs: list[dict[str, Any]] = []
    fetch_diagnostics: list[dict[str, Any]] = []
    all_states: set[str] = set()
    all_providers: set[str] = set()
    total_saved = 0
    total_excluded = 0
    total_entries_scanned = 0
    ssl_modes_seen: set[str] = set()
    insecure_ssl_used = bool(allow_insecure_ssl)
    accepted_by_state: Counter[str] = Counter()
    accepted_by_source: Counter[str] = Counter()
    accepted_by_pressure_area: Counter[str] = Counter()
    rejected_reasons: Counter[str] = Counter()
    mapped_count = 0
    unmapped_count = 0
    for coverage_start, coverage_end, edition_date in windows:
        result = retrieve_historical_sources(
            root,
            _parse_date(coverage_start),
            _parse_date(coverage_end),
            edition_date=edition_date,
            run_date=end.isoformat(),
            dry_run=effective_dry_run,
            refresh_cache=False,
            historical_provider="all",
            max_historical_queries=max_per_source,
            disable_registry_sources=False,
        )
        report = dict(result.get("report") or {})
        query_rows = [item for item in report.get("queries_run", []) if isinstance(item, dict)]
        registry_rows = [item for item in report.get("registry_source_diagnostics", []) if isinstance(item, dict)]
        total_entries_scanned += sum(int(item.get("result_count") or 0) for item in query_rows)
        total_entries_scanned += int(report.get("registry_records_raw") or 0)
        for item in query_rows:
            ssl_mode = str(item.get("ssl_mode") or "unknown")
            ssl_modes_seen.add(ssl_mode)
            fetch_diagnostics.append(
                {
                    "provider": item.get("provider_id"),
                    "source_id": None,
                    "source_name": None,
                    "url_or_query": item.get("request_url") or item.get("query"),
                    "ssl_mode": ssl_mode,
                    "insecure_ssl_used": bool(item.get("insecure_ssl_used", allow_insecure_ssl)),
                    "fetch_status": "ok" if not item.get("error") else "failed",
                    "http_status": item.get("status_code"),
                    "bytes_read": item.get("bytes_read"),
                    "entries_found": int(item.get("result_count") or 0),
                    "error": item.get("error"),
                }
            )
        for item in registry_rows:
            ssl_mode = str(item.get("ssl_mode") or "unknown")
            ssl_modes_seen.add(ssl_mode)
            fetch_diagnostics.append(
                {
                    "provider": "registry",
                    "source_id": item.get("source_id"),
                    "source_name": item.get("source_name"),
                    "url_or_query": item.get("url"),
                    "ssl_mode": ssl_mode,
                    "insecure_ssl_used": bool(item.get("insecure_ssl_used", allow_insecure_ssl)),
                    "fetch_status": "ok" if item.get("fetch_successful") else "failed",
                    "http_status": item.get("status_code"),
                    "bytes_read": item.get("bytes_read"),
                    "entries_found": int(item.get("records_raw") or item.get("raw_count") or 0),
                    "error": " | ".join(str(err) for err in (item.get("errors") or []) if err) or None,
                }
            )
        states = sorted((report.get("records_by_state_hint") or {}).keys())
        all_states.update(states)
        all_providers.update(str(item) for item in (report.get("providers_used") or []))
        report_saved = int(report.get("records_saved") or 0)
        report_excluded = int(report.get("records_excluded") or 0)
        registry_excluded = int(report.get("registry_records_excluded") or 0)
        accepted_by_state.update({str(k): int(v or 0) for k, v in (report.get("records_by_state_hint") or {}).items()})
        accepted_by_source.update({str(k): int(v or 0) for k, v in (report.get("records_by_source_id") or {}).items()})
        accepted_by_pressure_area.update({str(k): int(v or 0) for k, v in (report.get("records_by_category_hint") or {}).items()})
        rejected_reasons.update({str(k): int(v or 0) for k, v in (report.get("exclusion_reasons") or {}).items()})
        rejected_reasons.update({str(k): int(v or 0) for k, v in (report.get("registry_exclusion_reasons") or {}).items()})
        mapped_count += report_saved
        unmapped_count += (report_excluded + registry_excluded)
        total_saved += int(report_saved or result.get("source_count") or 0)
        total_excluded += int((report_excluded + registry_excluded) or result.get("excluded_source_count") or 0)
        runs.append(
            {
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "edition_date": edition_date,
                "ok": bool(result.get("ok")),
                "records_saved": int(report.get("records_saved") or 0),
                "records_excluded": int(report_excluded + registry_excluded),
                "providers_used": list(report.get("providers_used") or []),
                "records_by_state_hint": dict(report.get("records_by_state_hint") or {}),
                "exclusion_reasons": dict(report.get("exclusion_reasons") or {}),
                "recommendation": report.get("recommendation"),
                "historical_sources_path": result.get("historical_sources_path"),
                "historical_search_report_path": result.get("historical_search_report_path"),
                "warnings": list(result.get("warnings") or []),
                "errors": list(result.get("errors") or []),
            }
        )
    payload = {
        "ok": all(item["ok"] for item in runs),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_range": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "windows_run": runs,
        "total_records_saved": total_saved,
        "total_records_excluded": total_excluded,
        "entries_scanned": total_entries_scanned,
        "states_seen": sorted(all_states),
        "providers_seen": sorted(all_providers),
        "accepted_by_state": dict(sorted(accepted_by_state.items())),
        "accepted_by_source": dict(sorted(accepted_by_source.items())),
        "accepted_by_pressure_area": dict(sorted(accepted_by_pressure_area.items())),
        "rejected_reasons": dict(sorted(rejected_reasons.items())),
        "mapped_count": mapped_count,
        "unmapped_count": unmapped_count,
        "ssl_mode": "certifi" if "certifi" in ssl_modes_seen else "default",
        "insecure_ssl_used": insecure_ssl_used,
    }
    summary_path = root / "output" / "dispatches" / "cascadia" / "backfill" / f"backfill_summary_{end.isoformat()}.json"
    diagnostics_path = root / "output" / "dispatches" / "cascadia" / "backfill" / f"backfill_fetch_diagnostics_{end.isoformat()}.json"
    diagnostics_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ssl_mode": payload["ssl_mode"],
        "insecure_ssl_used": insecure_ssl_used,
        "rows": fetch_diagnostics,
    }
    if write and not effective_dry_run:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps(diagnostics_payload, indent=2), encoding="utf-8")
    payload["summary_path"] = str(summary_path)
    payload["diagnostics_path"] = str(diagnostics_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Cascadia pressure records across a date range.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-per-source", type=int, default=10)
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-insecure-ssl", action="store_true")
    args = parser.parse_args(argv)
    result = run_backfill(
        ROOT,
        args.start_date,
        args.end_date,
        max_per_source=args.max_per_source,
        weekly=bool(args.weekly),
        write=bool(args.write),
        dry_run=bool(args.dry_run),
        allow_insecure_ssl=bool(args.allow_insecure_ssl),
    )
    if result.get("insecure_ssl_used"):
        print("WARNING: Insecure SSL mode was used for diagnostics only. Do not publish insecure-generated output.", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
