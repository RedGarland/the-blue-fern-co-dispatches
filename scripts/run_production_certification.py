from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.production_certification import (  # noqa: E402
    CertificationLevel,
    CertificationOptions,
    run_certification,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated non-public Blue Fern production certification.")
    parser.add_argument("--dispatch", choices=["food-line", "care-line", "ice"], required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--proof-root", type=Path, required=True)
    parser.add_argument("--date", default="2026-09-10")
    parser.add_argument("--level", choices=[item.value for item in CertificationLevel], default=CertificationLevel.STATIC_STATE_PROOF.value)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt = run_certification(
        CertificationOptions(
            dispatch=args.dispatch,
            source_root=args.source_root,
            proof_root=args.proof_root,
            date=args.date,
            level=CertificationLevel(args.level),
        )
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["overall_status"] in {"PASS", "DEGRADED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
