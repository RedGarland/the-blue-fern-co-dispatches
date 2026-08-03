"""Build the Phase 1A root-site foundation without touching dispatch output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.phase1_site import render_phase1a_site


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site-root",
        type=Path,
        required=True,
        help="Current released public tree used for edition discovery, normally the freshly fetched Pages worktree.",
    )
    parser.add_argument(
        "--shell-asset-root",
        type=Path,
        default=ROOT / "output" / "site",
        help="Root containing the established shell stylesheet and brand assets; this root is not used for edition discovery.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("output/preview/site-phase1a"))
    args = parser.parse_args(argv)
    site_root = args.site_root.resolve()
    stale_source_output = (ROOT / "output" / "site").resolve()
    if site_root == stale_source_output:
        parser.error("output/site is not an authoritative current-public discovery root; pass the freshly fetched Pages worktree")
    result = render_phase1a_site(
        site_root,
        args.output_root.resolve(),
        shell_asset_root=args.shell_asset_root.resolve(),
    )
    print(json.dumps(result, indent=2))
    print(f"Phase 1A output written to {args.output_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
