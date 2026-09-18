from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_date_reconciliation import (
    build_reconstruction_editorial_decision,
    record_reconstruction_editorial_decision,
    validate_reconstruction_editorial_decision,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_artifact(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review artifact must be a JSON object")
    return payload


def _payload(args: argparse.Namespace) -> dict[str, Any]:
    artifact = _read_artifact(args.review_artifact)
    fields = {
        "decision": args.decision,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "decided_at": args.decided_at,
        "edited_headline": args.edited_headline,
        "edited_summary": args.edited_summary,
        "duplicate_of": args.duplicate_of,
        "editorial_note": args.editorial_note,
    }
    for key, value in fields.items():
        if value is not None:
            artifact[key] = value
    if "decided_at" not in artifact:
        artifact["decided_at"] = _utc_now()
    return artifact


def _materialize(args: argparse.Namespace) -> dict[str, Any]:
    payload = _payload(args)
    required = {"schema_version", "target_date", "candidate_id", "candidate_fingerprint", "research_input_sha256", "reconstruction_sha256", "publication_eligible", "publication_approval", "pages_authorized"}
    if required <= set(payload):
        return payload
    return build_reconstruction_editorial_decision(
        args.repo_root,
        args.date,
        args.candidate_id,
        decision=str(payload.get("decision") or ""),
        reviewer=str(payload.get("reviewer") or ""),
        reason=str(payload.get("reason") or ""),
        decided_at=str(payload.get("decided_at") or ""),
        edited_headline=payload.get("edited_headline"),
        edited_summary=payload.get("edited_summary"),
        duplicate_of=payload.get("duplicate_of"),
        editorial_note=payload.get("editorial_note"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or record private Food Line historical reconstruction editorial decisions.")
    parser.add_argument("action", choices=("validate", "record"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--date", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--decision")
    parser.add_argument("--reviewer")
    parser.add_argument("--reason")
    parser.add_argument("--decided-at")
    parser.add_argument("--edited-headline")
    parser.add_argument("--edited-summary")
    parser.add_argument("--duplicate-of")
    parser.add_argument("--editorial-note")
    parser.add_argument("--review-artifact")
    args = parser.parse_args(argv)
    args.repo_root = args.repo_root.resolve()
    payload = _materialize(args)
    if args.action == "validate":
        result = validate_reconstruction_editorial_decision(args.repo_root, args.date, args.candidate_id, payload)
        result = {"status": "valid", **{key: value for key, value in result.items() if key != "finding"}}
    else:
        result = record_reconstruction_editorial_decision(args.repo_root, args.date, args.candidate_id, payload)
        result = {key: value for key, value in result.items() if key != "finding"}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
