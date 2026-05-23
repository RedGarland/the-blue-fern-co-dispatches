from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.american_pressure_sources import (
    load_national_source_registry,
    validate_national_source_registry,
)
from bluefern_dispatches.american_pressure_sources import _fetch_status as fetch_status  # intentional shared logic


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate known American Pressure feed URLs and emit report only.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root")
    parser.add_argument("--timeout-seconds", type=int, default=8, help="Per-request timeout")
    parser.add_argument("--max-checks", type=int, default=200, help="Max feed URL checks in this run")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sources = load_national_source_registry(root)
    registry_errors = validate_national_source_registry(sources)
    if registry_errors:
        print(json.dumps({"ok": False, "registry_errors": registry_errors}, indent=2))
        return 1

    report_rows: list[dict[str, Any]] = []
    flat_results: list[dict[str, Any]] = []
    checks = 0
    for row in sources:
        feed_urls: list[tuple[str, str]] = []
        for field in ("rss_url", "atom_url", "json_feed_url", "sitemap_url"):
            value = str(row.get(field) or "").strip()
            if value:
                feed_urls.append((field, value))

        source_result: dict[str, Any] = {
            "source_id": row.get("source_id"),
            "state": row.get("state"),
            "feed_url_known": bool(row.get("feed_url_known")),
            "validation_status": row.get("validation_status"),
            "feed_validated_live": bool(row.get("feed_validated_live")),
            "ingest_ready": bool(row.get("ingest_ready")),
            "checks": [],
        }
        for field, url in feed_urls:
            if checks >= args.max_checks:
                skipped = {"field": field, "url": url, "ok": False, "status_code": None, "reason": "max_checks_exhausted"}
                source_result["checks"].append(skipped)
                continue
            ok, status_code, reason = fetch_status(url, timeout_seconds=max(1, int(args.timeout_seconds)))
            check_row = {"field": field, "url": url, "ok": ok, "status_code": status_code, "reason": reason}
            source_result["checks"].append(check_row)
            flat_results.append(
                {
                    "source_id": row.get("source_id"),
                    "source_name": row.get("source_name"),
                    "state": row.get("state"),
                    "feed_url": url,
                    "feed_type": field.replace("_url", ""),
                    "status": "live_validated" if ok else "live_failed",
                    "validation_status": "live_validated" if ok else "live_failed",
                    "http_status": status_code,
                    "error": None if ok else reason,
                    "checked_at_utc": utc_now(),
                }
            )
            checks += 1
        report_rows.append(source_result)

    live_validated_count = sum(1 for row in flat_results if row.get("validation_status") == "live_validated")
    live_failed_count = sum(1 for row in flat_results if row.get("validation_status") == "live_failed")
    pending_count = sum(1 for row in sources if str(row.get("validation_status") or "") == "pending_live_validation")

    output_path = root / "output" / "site" / "american-pressure" / "source_feed_validation_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_payload = {
        "generated_at": utc_now(),
        "max_checks": int(args.max_checks),
        "checks_attempted": checks,
        "live_validated_count": live_validated_count,
        "live_failed_count": live_failed_count,
        "pending_count": pending_count,
        "results_count": len(flat_results),
        "results": flat_results,
        "sources": report_rows,
    }
    output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report_path": str(output_path),
                "checks_attempted": checks,
                "live_validated_count": live_validated_count,
                "live_failed_count": live_failed_count,
                "pending_count": pending_count,
                "results_count": len(flat_results),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
