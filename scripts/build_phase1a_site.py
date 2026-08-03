"""Build the Phase 1A root-site foundation without touching dispatch output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.phase1_site import render_phase1a_site


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path("output/site"))
    parser.add_argument("--output-root", type=Path, default=Path("output/preview/site-phase1a"))
    args = parser.parse_args(argv)
    result = render_phase1a_site(args.site_root.resolve(), args.output_root.resolve())
    print(json.dumps(result, indent=2))
    print(f"Phase 1A output written to {args.output_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
