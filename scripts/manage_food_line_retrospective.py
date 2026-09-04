from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_retrospective import (
    FoodLineRetrospectiveError,
    create_public_copy_correction,
    create_private_preview,
    create_retrospective_approval,
    load_retrospective_plan,
    load_retrospective_verification_bundle,
    plan_result,
    verify_private_preview,
    verify_generated_retrospective_set,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage committed-authority Food Line migrated-event retrospective releases."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    correction = subparsers.add_parser("correct-public-copy", help="Create one deterministic non-authorizing correction overlay.")
    correction.add_argument("--repo-root", type=Path, required=True)
    correction.add_argument("--decision-commit", required=True)
    correction.add_argument("--decision-path", required=True)
    correction.add_argument("--decision-blob-sha1", required=True)
    correction.add_argument("--decision-sha256", required=True)
    correction.add_argument("--event-id", required=True)
    correction.add_argument("--field", required=True, choices=("publication_copy.summary",))
    correction.add_argument("--prior-text", required=True)
    correction.add_argument("--replacement-text", required=True)
    correction.add_argument("--reason", required=True)
    correction.add_argument("--corrected-by", required=True)
    correction.add_argument("--corrected-at", required=True)

    approve = subparsers.add_parser("approve", help="Create one approval artifact from a private request.")
    approve.add_argument("--repo-root", type=Path, required=True)
    approve.add_argument("--request", type=Path, required=True)

    plan = subparsers.add_parser("plan", help="Validate a committed approval without mutation.")
    plan.add_argument("--repo-root", type=Path, required=True)
    plan.add_argument("--pages-root", type=Path, required=True)
    plan.add_argument("--approval-commit", required=True)
    plan.add_argument("--approval-path", required=True)
    plan.add_argument("--publication-timestamp", required=True)

    verify = subparsers.add_parser("verify-output", help="Verify exact generated retrospective public surfaces.")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--pages-root", type=Path, required=True)
    verify.add_argument("--approval-commit", action="append", required=True)
    verify.add_argument("--approval-path", action="append", required=True)
    verify.add_argument("--publication-timestamp", required=True)

    for operation, help_text in (
        ("preview", "Create a deterministic private preview outside the repository."),
        ("verify-preview", "Verify exact private preview bytes outside the repository."),
    ):
        preview = subparsers.add_parser(operation, help=help_text)
        preview.add_argument("--repo-root", type=Path, required=True)
        preview.add_argument("--pages-root", type=Path, required=True)
        preview.add_argument("--approval-commit", required=True)
        preview.add_argument("--approval-path", required=True)
        preview.add_argument("--publication-timestamp", required=True)
        preview.add_argument("--preview-root", type=Path, required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "correct-public-copy":
            result = create_public_copy_correction(
                args.repo_root,
                decision_commit=args.decision_commit,
                decision_path=args.decision_path,
                decision_blob_sha1=args.decision_blob_sha1,
                decision_sha256=args.decision_sha256,
                event_id=args.event_id,
                field=args.field,
                prior_text=args.prior_text,
                replacement_text=args.replacement_text,
                reason=args.reason,
                corrected_by=args.corrected_by,
                corrected_at=args.corrected_at,
            )
        elif args.operation == "approve":
            result = create_retrospective_approval(args.repo_root, args.request)
        elif args.operation == "verify-output":
            if len(args.approval_commit) != len(args.approval_path):
                raise FoodLineRetrospectiveError("verify-output requires matching approval commits and paths")
            bundles = [
                load_retrospective_verification_bundle(
                    args.repo_root,
                    args.pages_root,
                    approval_commit=commit,
                    approval_path=path,
                    publication_timestamp=args.publication_timestamp,
                )
                for commit, path in zip(args.approval_commit, args.approval_path)
            ]
            result = verify_generated_retrospective_set(args.repo_root, args.pages_root, bundles)
        else:
            bundle = load_retrospective_plan(
                args.repo_root,
                args.pages_root,
                approval_commit=args.approval_commit,
                approval_path=args.approval_path,
                publication_timestamp=args.publication_timestamp,
            )
            if args.operation == "plan":
                result = plan_result(bundle)
            elif args.operation == "preview":
                result = create_private_preview(bundle, args.repo_root, args.preview_root)
            elif args.operation == "verify-preview":
                result = verify_private_preview(bundle, args.repo_root, args.preview_root)
            else:
                raise FoodLineRetrospectiveError(f"unsupported operation: {args.operation}")
    except (FoodLineRetrospectiveError, OSError, ValueError) as exc:
        result = {"ok": False, "status": f"{args.operation}_failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok", True) and not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
