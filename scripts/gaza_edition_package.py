#!/usr/bin/env python3
"""Approve, plan, privately stage, or verify a Gaza edition correction package.

No mode publishes Pages, edits the public site, or grants publication authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.gaza_edition_package import (
    Error,
    create_edition_approval_request,
    create_edition_package_approval,
    plan_edition_correction,
    stage_edition_correction,
    verify_staged_edition_package,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("approval-request", "approve-package", "plan", "stage", "verify-staged"), required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--pages-root", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approval-request", type=Path)
    parser.add_argument("--approval-id")
    parser.add_argument("--approver")
    parser.add_argument("--approved-at")
    parser.add_argument("--approval-ref")
    parser.add_argument("--approval-path")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--rendered-audio", type=Path)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--package-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "approval-request":
            if args.proposal is None or args.output is None:
                raise Error("approval-request requires --proposal and --output")
            result = create_edition_approval_request(proposal_path=args.proposal, output_path=args.output)
        elif args.mode == "approve-package":
            required = (args.source_root, args.proposal, args.approval_request, args.output,
                        args.approval_id, args.approver, args.approved_at)
            if any(value is None for value in required):
                raise Error("approve-package requires source/proposal/request/output and authority fields")
            result = create_edition_package_approval(
                source_root=args.source_root, proposal_path=args.proposal,
                approval_request_path=args.approval_request, output_path=args.output,
                approval_id=args.approval_id, approver=args.approver, approved_at=args.approved_at,
            )
        elif args.mode == "plan":
            required = (args.source_root, args.pages_root, args.proposal, args.approval_ref, args.approval_path)
            if any(value is None for value in required):
                raise Error("plan requires source/pages/proposal/approval-ref/approval-path")
            result = plan_edition_correction(
                source_root=args.source_root, pages_root=args.pages_root,
                proposal_path=args.proposal, approval_ref=args.approval_ref,
                approval_path=args.approval_path,
            )
        else:
            if args.plan is None:
                raise Error(f"{args.mode} requires --plan")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            if args.mode == "stage":
                required = (args.input_root, args.rendered_audio, args.staging_root, args.source_root, args.pages_root)
                if any(value is None for value in required):
                    raise Error("stage requires input/audio/staging/source/pages paths")
                result = stage_edition_correction(
                    plan=plan, input_root=args.input_root, rendered_audio_path=args.rendered_audio,
                    staging_root=args.staging_root, source_root=args.source_root, pages_root=args.pages_root,
                )
            else:
                required = (args.package_root, args.source_root, args.pages_root)
                if any(value is None for value in required):
                    raise Error("verify-staged requires package/source/pages paths")
                result = verify_staged_edition_package(
                    plan=plan, package_root=args.package_root,
                    source_root=args.source_root, pages_root=args.pages_root,
                )
    except (Error, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
