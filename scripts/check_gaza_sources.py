from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.gaza_sources import collect_gaza_sources, filter_recent_duplicate_sources


ROOT = Path(__file__).resolve().parents[1]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _audit_path() -> Path:
    return ROOT / "output" / "dispatches" / "gaza" / "source_collection" / f"gaza_collection_audit_{_stamp()}.json"


def build_report(edition_date: str) -> dict[str, Any]:
    collected = collect_gaza_sources(ROOT, edition_date, max_sources=50, min_sources=0, output_filename="manual_sources.json", prefer_manual=False)
    candidates = list(collected.get("sources") or [])
    kept, dedupe_report = filter_recent_duplicate_sources(ROOT, edition_date, candidates, lookback_days=7)
    providers = collected.get("provider_diagnostics") or []
    rejected = collected.get("rejected_by_reason") or {}
    missing_published = sum(1 for item in candidates if not str(item.get("published_at") or "").strip())
    wrapper_count = sum(1 for item in candidates if str(item.get("wrapper_url") or "").strip())
    direct_count = len(candidates) - wrapper_count
    publishers: dict[str, int] = {}
    for item in candidates:
        name = str(item.get("publisher") or "unknown").strip() or "unknown"
        publishers[name] = int(publishers.get(name, 0)) + 1
    top_publishers = sorted(({"publisher": k, "count": v} for k, v in publishers.items()), key=lambda row: row["count"], reverse=True)[:10]
    recommendations: list[str] = []
    if wrapper_count > direct_count:
        recommendations.append("Increase direct publisher or official feeds; Google wrapper URLs dominate.")
    if missing_published:
        recommendations.append("Prioritize providers that emit parseable published_at values.")
    if int(dedupe_report.get("suppressed_candidate_count", 0)) >= int(dedupe_report.get("input_candidate_count", 0)) and int(dedupe_report.get("input_candidate_count", 0)) > 0:
        recommendations.append("Collection found candidates but all were deduped; expand provider diversity.")
    if not recommendations:
        recommendations.append("Collection balance looks acceptable; continue monitoring.")
    return {
        "edition_date": edition_date,
        "source_feeds_checked": [row.get("url") for row in providers if isinstance(row, dict) and row.get("url")],
        "source_providers_attempted": providers,
        "candidates_found": len(candidates),
        "candidates_accepted": len(kept),
        "candidates_rejected_by_reason": rejected,
        "candidates_suppressed_by_dedupe": dedupe_report.get("suppressed_candidate_count", 0),
        "candidates_missing_published_at": missing_published,
        "google_news_wrapper_url_count": wrapper_count,
        "direct_canonical_url_count": direct_count,
        "top_publishers_found": top_publishers,
        "recommended_fixes": recommendations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Gaza source diagnostics.")
    parser.add_argument("--date", help="Edition date YYYY-MM-DD (default: UTC today).")
    parser.add_argument("--write-report", action="store_true", help="Write JSON report under output/dispatches/gaza/source_collection.")
    args = parser.parse_args()
    edition_date = args.date or datetime.now(timezone.utc).date().isoformat()
    report = build_report(edition_date)
    if args.write_report:
        out = _audit_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
