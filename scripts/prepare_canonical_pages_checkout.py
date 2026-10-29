"""Explicit private fresh-checkout CLI; no runner integration or publication."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.canonical_pages_checkout import (  # noqa: E402
    CheckoutRefused, create_canonical_pages_clone,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--approved-parent", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--reviewed-template", required=True, type=Path,
                        help="Explicit reviewed Git template; not an implicit/default-policy claim")
    parser.add_argument("--create-private-clone", required=True, action="store_true")
    args = parser.parse_args(argv)
    try:
        result = create_canonical_pages_clone(args.source_repo, args.destination,
            approved_parent=args.approved_parent, expected_commit=args.expected_commit,
            reviewed_template=args.reviewed_template)
    except (CheckoutRefused, OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc), "destination": str(args.destination),
                  "failure_residue_retained": True, "publication_authorized": False}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
