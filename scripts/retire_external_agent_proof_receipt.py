"""Retire one known malformed synthetic-proof receipt from live handoff status."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DISPATCHES = {"food-line", "care-line"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
ATTEMPT_RE = re.compile(r"^unknown-run-attempt-[A-Za-z0-9.-]+$")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def retire_proof_receipt(
    runner_root: Path,
    *,
    dispatch: str,
    receipt: str,
    receipt_sha256: str,
    proof_run_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    if dispatch not in DISPATCHES:
        raise ValueError("unsupported dispatch")
    if not proof_run_id.startswith("synthetic-") or "/" in proof_run_id or "\\" in proof_run_id:
        raise ValueError("proof_run_id must be a single synthetic run identifier")
    expected_sha = receipt_sha256.lower()
    if not SHA_RE.fullmatch(expected_sha):
        raise ValueError("receipt_sha256 must be a 64-character SHA256")
    receipt_ref = receipt.replace("\\", "/")
    parts = Path(receipt_ref).parts
    expected_prefix = ("data", "private-agent-handoff", "receipts", dispatch, "unknown-date")
    if parts[:5] != expected_prefix or len(parts) != 6 or not ATTEMPT_RE.fullmatch(parts[-1][:-5]):
        raise ValueError("receipt must be one exact unknown-date attempt receipt for the selected dispatch")
    target = runner_root / Path(*parts)
    root = runner_root.resolve()
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("receipt path escapes runner root")
    if not target.is_file():
        raise ValueError("receipt does not exist")
    actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("receipt SHA256 does not match")
    value = json.loads(target.read_text(encoding="utf-8"))
    if (
        value.get("dispatch") != dispatch
        or value.get("status") != "FAILED"
        or value.get("classification") != "MALFORMED"
        or value.get("agent_run_id")
        or value.get("input_filename") != "external-agent-envelope.json"
        or "Unexpected UTF-8 BOM" not in str(value.get("error") or "")
    ):
        raise ValueError("receipt is not the bounded malformed proof-receipt shape")
    attempt_id = target.stem
    tombstone = {
        "schema_version": "bluefern.external_agent_handoff_proof_retirement.v1",
        "dispatch": dispatch,
        "receipt_attempt_id": attempt_id,
        "receipt_ref": receipt_ref,
        "receipt_sha256": actual_sha,
        "proof_run_id": proof_run_id,
        "synthetic_proof": True,
        "audit_preserved": True,
        "retirement_status": "PRODUCTION_PROOF_RETIRED",
        "retired_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "operator_tool": "retire_external_agent_proof_receipt.v1",
    }
    tombstone_ref = f"data/private-agent-handoff/cleanup/{dispatch}/proof-receipt-retirement-{attempt_id}.json"
    result = {"result": "DRY_RUN_NO_CHANGES" if not apply else "PROOF_RECEIPT_RETIRED", "tombstone_ref": tombstone_ref, "receipt_ref": receipt_ref, "receipt_sha256": actual_sha, "audit_preserved": True}
    if apply:
        tombstone_path = runner_root / Path(*tombstone_ref.split("/"))
        if tombstone_path.exists():
            existing = json.loads(tombstone_path.read_text(encoding="utf-8"))
            expected = {key: existing.get(key) for key in ("dispatch", "receipt_attempt_id", "receipt_ref", "receipt_sha256", "synthetic_proof", "audit_preserved")}
            actual = {key: tombstone.get(key) for key in expected}
            if expected != actual:
                raise ValueError("retirement tombstone already exists with different identity")
        else:
            _write_json(tombstone_path, tombstone)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-root", type=Path, required=True)
    parser.add_argument("--dispatch", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--receipt-sha256", required=True)
    parser.add_argument("--proof-run-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = retire_proof_receipt(args.runner_root.resolve(), dispatch=args.dispatch, receipt=args.receipt, receipt_sha256=args.receipt_sha256, proof_run_id=args.proof_run_id, apply=args.apply)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
