"""Build the private Phase 1 homepage prototype from public site output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.phase1_site import render_site


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path("output/site"))
    parser.add_argument("--output-root", type=Path, default=Path("output/preview/site-phase1"))
    args = parser.parse_args()
    if not (args.site_root / "assets" / "site.css").exists():
        parser.error(f"public site root is missing shared stylesheet: {args.site_root}")
    print(render_site(args.site_root.resolve(), args.output_root.resolve()))
    print(f"Preview written to {args.output_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
