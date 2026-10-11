from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.ice_public_pilot_gate import (  # noqa: E402
    build_ice_public_pilot_readiness_report,
)


def _source_head(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return ""


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare an ICE public pilot readiness report without granting publication authority."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--source-head")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    report = build_ice_public_pilot_readiness_report(
        repo_root=repo_root,
        bundle_manifest_path=args.bundle_manifest,
        staging_root=args.staging_root,
        source_head=args.source_head or _source_head(repo_root),
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        _write_json(args.output, report)
    print(text)
    return 0 if report["ready_for_human_approval"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
