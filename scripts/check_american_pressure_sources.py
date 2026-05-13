from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bluefern_dispatches.american_pressure_sources import (
    build_source_health_report,
    load_source_registry,
    validate_registry_sources,
    write_source_health_report,
)


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate American Pressure source registry and optionally write source health report.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root.")
    parser.add_argument("--fetch-check", action="store_true", help="Attempt lightweight HEAD/GET fetch checks for enabled sources.")
    parser.add_argument("--write-report", action="store_true", help="Write source health report to output/dispatches/american-pressure/source_health/YYYY-MM-DD.json")
    parser.add_argument("--date", default=_today_utc(), help="Date for report filename (YYYY-MM-DD).")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    sources = load_source_registry(root)
    validation_errors = validate_registry_sources(sources)
    if validation_errors:
        print(json.dumps({"ok": False, "validation_errors": validation_errors}, indent=2))
        return 1

    report = build_source_health_report(sources, fetch_check=args.fetch_check)
    output_path = None
    if args.write_report:
        output_path = write_source_health_report(root, report, args.date)

    print(
        json.dumps(
            {
                "ok": True,
                "source_count": len(sources),
                "enabled_count": sum(1 for source in sources if source.get("enabled") is True),
                "fetch_check": args.fetch_check,
                "report_written": bool(output_path),
                "report_path": str(output_path) if output_path else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
