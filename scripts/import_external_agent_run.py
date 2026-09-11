from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.external_agent_handoff import import_envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import one private external Food/Care agent envelope")
    parser.add_argument("--dispatch", required=True, help="food-line or care-line; unknown values produce a failure receipt")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--runner-root", required=True, type=Path)
    args = parser.parse_args(argv)
    code, result = import_envelope(args.runner_root.resolve(), args.input.resolve(), dispatch=args.dispatch)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
