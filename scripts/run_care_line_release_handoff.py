from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_approved_release import build_approved_release_artifacts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Care Line approved release artifacts from reviewed records.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    result = build_approved_release_artifacts(repo_root, args.edition_date)
    if args.check_only:
        result = dict(result)
        result["status"] = "approved_release_preview" if result.get("release_ready") else "no_approved_release"
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
