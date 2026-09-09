from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.ice_dispatch import generate_ice_edition_candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a non-public ICE Dispatch edition candidate from manually reviewed canonical events."
    )
    parser.add_argument("--reviewed-events", type=Path, required=True)
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--collection-state", default="healthy")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generated-at")
    args = parser.parse_args(argv)

    reviewed_records = json.loads(args.reviewed_events.read_text(encoding="utf-8"))
    result = generate_ice_edition_candidate(
        reviewed_records,
        edition_date=args.edition_date,
        collection_state=args.collection_state,
        output_dir=args.output_dir,
        generated_at=args.generated_at,
    )
    print(json.dumps({
        "editorial_decision": result["manifest"]["editorial_decision"],
        "rendered_event_count": result["manifest"]["rendered_event_count"],
        "accepted_event_count": result["manifest"]["accepted_event_count"],
        "public_side_effects": result["public_side_effects"],
        "output_dir": str(args.output_dir),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
