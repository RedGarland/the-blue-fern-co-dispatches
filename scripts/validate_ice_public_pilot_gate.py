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
    IcePublicPilotGateError,
    validate_ice_public_pilot_gate,
)


def _source_head(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate ICE public pilot readiness gate; performs no publication.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--source-head")
    parser.add_argument("--social-requested", action="store_true")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    try:
        result = validate_ice_public_pilot_gate(
            repo_root=repo_root,
            bundle_manifest_path=args.bundle_manifest,
            staging_root=args.staging_root,
            approval_path=args.approval,
            source_head=args.source_head or _source_head(repo_root),
            social_requested=args.social_requested,
        )
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, IcePublicPilotGateError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

