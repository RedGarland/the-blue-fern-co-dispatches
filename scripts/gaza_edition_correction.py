#!/usr/bin/env python3
"""Prepare/verify private edition corrections; never approve, render or publish."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.gaza_edition_correction import Error, prepare_edition_correction, verify_edition_correction_packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "propose", "verify"), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pages-root", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--review-packet", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--packet", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "verify":
            if args.packet is None:
                raise Error("verify requires --packet")
            result = verify_edition_correction_packet(packet_path=args.packet,
                source_root=args.source_root, pages_root=args.pages_root)
        else:
            if any(x is None for x in (args.request, args.review_packet, args.output_root)):
                raise Error("prepare/propose require --request, --review-packet and --output-root")
            result = prepare_edition_correction(source_root=args.source_root,
                pages_root=args.pages_root, request_path=args.request,
                review_packet_path=args.review_packet, output_root=args.output_root,
                formal=args.mode == "propose")
    except (Error, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
