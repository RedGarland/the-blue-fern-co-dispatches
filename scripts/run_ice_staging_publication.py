from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.ice_staging_publication import stage_ice_publication


def _source_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run staging-only ICE publication integration.")
    parser.add_argument("--reviewed-events", type=Path, required=True)
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--collection-state", default="healthy")
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--decision-override")
    args = parser.parse_args(argv)

    records = json.loads(args.reviewed_events.read_text(encoding="utf-8"))
    result = stage_ice_publication(
        records,
        edition_date=args.edition_date,
        collection_state=args.collection_state,
        staging_root=args.staging_root,
        generated_at=args.generated_at,
        source_commit=_source_commit(),
        decision_override=args.decision_override,
    )
    print(json.dumps({
        "status": result.status,
        "receipt_path": str(result.receipt_path),
        "changed_paths": list(result.changed_paths),
        "public_side_effects": result.public_side_effects,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
