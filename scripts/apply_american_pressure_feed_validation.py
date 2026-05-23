from __future__ import annotations

import argparse
import json
from pathlib import Path

from bluefern_dispatches.american_pressure_sources import (
    NATIONAL_REGISTRY_PATH,
    NATIONAL_VALIDATION_REPORT_PATH,
    apply_feed_validation_report,
    build_feed_health_summary,
    build_national_coverage_summary,
    load_national_source_registry,
    validate_national_source_registry,
    write_feed_health_summary,
    write_national_coverage_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely apply feed validation results to American Pressure source registry.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root")
    parser.add_argument("--registry-path", type=Path, default=NATIONAL_REGISTRY_PATH, help="Registry JSON path")
    parser.add_argument("--report-path", type=Path, default=NATIONAL_VALIDATION_REPORT_PATH, help="Validation report JSON path")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sources = load_national_source_registry(root, args.registry_path)
    report_path = root / args.report_path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    updated_sources, apply_summary = apply_feed_validation_report(sources, report)
    registry_errors = validate_national_source_registry(updated_sources)
    if registry_errors:
        print(json.dumps({"ok": False, "registry_errors": registry_errors}, indent=2))
        return 1

    registry_path = root / args.registry_path
    registry_path.write_text(json.dumps(updated_sources, indent=2), encoding="utf-8")
    coverage_summary = build_national_coverage_summary(updated_sources)
    feed_health_summary = build_feed_health_summary(updated_sources)
    write_national_coverage_summary(root, coverage_summary)
    write_feed_health_summary(root, feed_health_summary)

    print(
        json.dumps(
            {
                "ok": True,
                "registry_path": str(registry_path),
                "report_path": str(report_path),
                "applied_validated": int(apply_summary.get("applied_validated") or 0),
                "applied_failed": int(apply_summary.get("applied_failed") or 0),
                "unmatched_results": int(apply_summary.get("unmatched_results") or 0),
                "total_ingest_ready_sources": int(feed_health_summary.get("total_ingest_ready_sources") or 0),
                "failed_validation_count": int(feed_health_summary.get("failed_validation_count") or 0),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
