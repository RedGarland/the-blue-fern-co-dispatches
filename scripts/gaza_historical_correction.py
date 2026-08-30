#!/usr/bin/env python3
"""Plan or stage an approval-gated formal Gaza historical correction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bluefern_dispatches.gaza_historical_correction import (
    CorrectionValidationError,
    create_package_approval,
    plan_correction,
    prepare_correction_proposal,
    stage_correction_package,
    verify_staged_package,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Validate a complete formal historical-correction proposal. "
            "This command cannot publish or mutate Pages."
        )
    )
    result.add_argument("--mode", choices=("propose", "approve-package", "plan", "stage", "verify-staged"), required=True)
    result.add_argument("--source-root", type=Path)
    result.add_argument("--pages-root", type=Path)
    result.add_argument("--story-id")
    result.add_argument("--review-path")
    result.add_argument("--decision-audit-path")
    result.add_argument("--correction-date")
    result.add_argument("--proposal-root", type=Path)
    result.add_argument("--tts-provider")
    result.add_argument("--tts-model")
    result.add_argument("--tts-voice")
    result.add_argument("--approval-request", type=Path)
    result.add_argument("--approval-output", type=Path)
    result.add_argument("--approval-id")
    result.add_argument("--approver")
    result.add_argument("--approved-at")
    result.add_argument("--proposal", type=Path)
    result.add_argument("--input-root", type=Path)
    result.add_argument("--approval-ref")
    result.add_argument("--approval-path")
    result.add_argument("--staging-root", type=Path)
    result.add_argument("--rendered-audio", type=Path)
    result.add_argument("--package-root", type=Path)
    return result


def _required(args: argparse.Namespace, *names: str) -> None:
    missing = [f"--{name.replace('_', '-')}" for name in names if getattr(args, name) in (None, "")]
    if missing:
        raise CorrectionValidationError(f"{args.mode} requires: {', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.mode == "propose":
            _required(
                args,
                "source_root",
                "pages_root",
                "story_id",
                "review_path",
                "decision_audit_path",
                "correction_date",
                "proposal_root",
                "tts_provider",
                "tts_model",
                "tts_voice",
            )
            result = prepare_correction_proposal(
                source_root=args.source_root,
                pages_root=args.pages_root,
                story_id=args.story_id,
                review_path=args.review_path,
                decision_audit_path=args.decision_audit_path,
                correction_date=args.correction_date,
                output_root=args.proposal_root,
                tts_provider=args.tts_provider,
                tts_model=args.tts_model,
                tts_voice=args.tts_voice,
            )
        elif args.mode == "approve-package":
            _required(
                args,
                "source_root",
                "pages_root",
                "proposal",
                "input_root",
                "approval_request",
                "approval_output",
                "approval_id",
                "approver",
                "approved_at",
            )
            result = create_package_approval(
                source_root=args.source_root,
                pages_root=args.pages_root,
                proposal_path=args.proposal,
                input_root=args.input_root,
                approval_request_path=args.approval_request,
                output_path=args.approval_output,
                approval_id=args.approval_id,
                approver=args.approver,
                approved_at=args.approved_at,
            )
        else:
            _required(
                args,
                "source_root",
                "pages_root",
                "proposal",
                "input_root",
                "approval_ref",
                "approval_path",
            )
            plan = plan_correction(
                source_root=args.source_root,
                pages_root=args.pages_root,
                proposal_path=args.proposal,
                input_root=args.input_root,
                approval_ref=args.approval_ref,
                approval_path=args.approval_path,
            )
            if args.mode == "plan":
                result = plan
            elif args.mode == "stage":
                _required(args, "staging_root", "rendered_audio")
                result = stage_correction_package(
                    plan=plan,
                    input_root=args.input_root,
                    rendered_audio_path=args.rendered_audio,
                    staging_root=args.staging_root,
                    source_root=args.source_root,
                    pages_root=args.pages_root,
                )
            else:
                _required(args, "package_root")
                result = verify_staged_package(
                    plan=plan,
                    package_root=args.package_root,
                    source_root=args.source_root,
                    pages_root=args.pages_root,
                )
    except CorrectionValidationError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
