#!/usr/bin/env python3
"""Plan or stage an approval-gated formal Gaza historical correction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bluefern_dispatches.gaza_historical_correction import (
    CorrectionValidationError,
    plan_correction,
    stage_correction_package,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Validate a complete formal historical-correction proposal. "
            "This command cannot publish or mutate Pages."
        )
    )
    result.add_argument("--source-root", type=Path, required=True)
    result.add_argument("--pages-root", type=Path, required=True)
    result.add_argument("--proposal", type=Path, required=True)
    result.add_argument("--input-root", type=Path, required=True)
    result.add_argument("--approval-ref", required=True)
    result.add_argument("--approval-path", required=True)
    result.add_argument("--mode", choices=("plan", "stage"), default="plan")
    result.add_argument("--staging-root", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        plan = plan_correction(
            source_root=args.source_root,
            pages_root=args.pages_root,
            proposal_path=args.proposal,
            input_root=args.input_root,
            approval_ref=args.approval_ref,
            approval_path=args.approval_path,
        )
        if args.mode == "stage":
            if args.staging_root is None:
                raise CorrectionValidationError("--staging-root is required in stage mode")
            result = stage_correction_package(
                plan=plan,
                input_root=args.input_root,
                staging_root=args.staging_root,
                source_root=args.source_root,
                pages_root=args.pages_root,
            )
        else:
            result = plan
    except CorrectionValidationError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
