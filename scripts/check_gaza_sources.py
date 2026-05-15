from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.gaza_sources import (
    TLS_FAILURE_REASON,
    fetch_feed_payload,
    load_sources_config,
    parse_rss_items,
)


ROOT = Path(__file__).resolve().parents[1]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _report_path() -> Path:
    return ROOT / "output" / "dispatches" / "gaza" / "source_health" / f"gaza_source_health_{_stamp()}.json"


def _recommendation_for_failure(status_code: int | None, reason: str) -> str:
    lowered = reason.lower()
    if status_code == 404 or "404" in lowered:
        return "disabled_dead_source"
    if status_code in {401, 403} or "401" in lowered or "403" in lowered:
        return "manual_only_or_diagnostics_only"
    if "certificate_verify_failed" in lowered or "tls" in lowered or "ssl" in lowered:
        return "diagnostics_only_tls_blocked"
    return "investigate_endpoint"


def _probe_source(source: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_id": source.source_id,
        "tier": source.source_tier,
        "state": source.source_state,
        "url": source.url,
        "status": "skipped",
        "status_code": None,
        "failure_reason": None,
        "raw_candidates": 0,
        "recommendation": None,
        "backend_used": None,
        "tls_error": False,
        "exception_type": None,
    }
    if source.source_state != "enabled":
        row["failure_reason"] = f"state:{source.source_state}"
        return row
    if source.type != "rss":
        row["failure_reason"] = f"unsupported_type:{source.type}"
        return row
    fetch = fetch_feed_payload(source.source_id, source.url)
    row["backend_used"] = fetch.get("backend_used")
    row["tls_error"] = bool(fetch.get("tls_error"))
    row["exception_type"] = fetch.get("exception_type")
    if not fetch.get("ok"):
        row["status"] = "failed"
        row["status_code"] = fetch.get("status_code")
        reason = str(fetch.get("failure_reason") or "feed_fetch_failed")
        row["failure_reason"] = (
            "tls_certificate_verification_failed (environment-sensitive)"
            if reason == TLS_FAILURE_REASON
            else reason
        )
        row["recommendation"] = _recommendation_for_failure(row["status_code"], row["failure_reason"])
        return row
    try:
        items = parse_rss_items(
            fetch.get("content_bytes") or b"",
            content_type=str(fetch.get("content_type") or ""),
            content_encoding=str(fetch.get("content_encoding") or ""),
        )
        row["status"] = "ok"
        row["raw_candidates"] = len(items)
        if len(items) == 0:
            row["status"] = "no_candidates"
            row["failure_reason"] = "feed_returned_zero_items"
    except Exception as exc:  # noqa: BLE001
        row["status"] = "failed"
        row["failure_reason"] = f"{type(exc).__name__}: {exc}"
        row["exception_type"] = type(exc).__name__
        row["recommendation"] = _recommendation_for_failure(fetch.get("status_code"), row["failure_reason"])
    return row


def build_report() -> dict[str, Any]:
    definitions = load_sources_config(ROOT / "data" / "dispatches" / "gaza" / "sources.yml")
    rows = [_probe_source(source) for source in definitions]
    enabled = [row for row in rows if row["state"] == "enabled"]
    attempted = [row for row in enabled if row["status"] in {"ok", "no_candidates", "failed"}]
    successes = [row for row in attempted if row["status"] == "ok"]
    failures = [row for row in attempted if row["status"] == "failed"]
    skipped = [row for row in rows if row["status"] == "skipped"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers_total": len(rows),
        "providers_enabled": len(enabled),
        "providers_attempted": len(attempted),
        "providers_successful": len(successes),
        "providers_failed": len(failures),
        "providers_skipped_by_state_or_type": len(skipped),
        "providers": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gaza source health checker.")
    parser.add_argument("--write-report", action="store_true", help="Write report under output/dispatches/gaza/source_health")
    args = parser.parse_args()
    report = build_report()
    if args.write_report:
        out = _report_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
