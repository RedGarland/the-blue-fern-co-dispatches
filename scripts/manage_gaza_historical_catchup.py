from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_historical_catchup import (
    GazaHistoricalCatchupError,
    create_approval,
    create_private_preview,
    create_stage,
    load_plan,
    plan_result,
    published_replay_result,
    publish_stage,
    verify_stage,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage approval-gated Gaza historical TRUE-MISS catch-up publication."
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    approve = commands.add_parser("approve", help="Create one deterministic approval from a private request.")
    approve.add_argument("--repo-root", type=Path, required=True)
    approve.add_argument("--pages-root", type=Path, required=True)
    approve.add_argument("--request", type=Path, required=True)

    for name, help_text in (
        ("plan", "Validate a committed approval without persistent mutation."),
        ("preview", "Create deterministic public-shaped material in a private external directory."),
        ("stage", "Create a deterministic external release package and manifest."),
        ("verify-stage", "Rebuild and verify an external release package byte for byte."),
        ("publish", "Publish only an exact verified stage to the bound Pages checkout."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--repo-root", type=Path, required=True)
        command.add_argument("--pages-root", type=Path, required=True)
        command.add_argument("--approval-commit", required=True)
        command.add_argument("--approval-path", required=True)
        command.add_argument("--publication-timestamp", required=True)
        if name == "preview":
            command.add_argument("--preview-root", type=Path, required=True)
        if name in {"stage", "verify-stage", "publish"}:
            command.add_argument("--stage-root", type=Path, required=True)
        if name == "publish":
            command.add_argument("--push", action="store_true")
            command.add_argument("--live-base-url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "approve":
            result = create_approval(args.repo_root, args.pages_root, args.request)
        else:
            if args.operation == "publish":
                replay = published_replay_result(
                    args.repo_root,
                    args.pages_root,
                    approval_commit=args.approval_commit,
                    approval_path=args.approval_path,
                )
                if replay is not None:
                    print(json.dumps(replay, ensure_ascii=False, sort_keys=True, indent=2))
                    return 0
            bundle = load_plan(
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
            elif args.operation == "stage":
                result = create_stage(bundle, args.repo_root, args.stage_root)
            elif args.operation == "verify-stage":
                result = verify_stage(bundle, args.repo_root, args.stage_root)
            else:
                result = publish_stage(
                    args.repo_root,
                    bundle,
                    args.stage_root,
                    push=args.push,
                    live_base_url=args.live_base_url,
                )
    except (GazaHistoricalCatchupError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "status": f"{args.operation}_failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") is True and not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
