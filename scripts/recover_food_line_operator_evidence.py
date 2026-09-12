from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.external_agent_handoff import OPERATOR_RECOVERY_CLASS, import_envelope

DEFAULT_INPUT = Path(
    "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/"
    "operator-preserved-source-watch-payloads.json"
)
DEFAULT_OUTPUT = Path("data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json")
SPLIT_ROOT = Path("data/private-agent-handoff/operator-recovery/food-line/2026-09-10/source-payloads")
TERMINAL = {
    "retained_for_review",
    "duplicate_with_reason",
    "rejected_with_reason",
    "deferred_with_reason",
    "invalid_source_with_reason",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_bundle(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("operator recovery bundle must be a JSON object")
    if payload.get("schema_version") != "bluefern.operator_recovered_source_watch_bundle.v1":
        raise ValueError("unsupported operator recovery bundle schema")
    if payload.get("dispatch") != "food-line":
        raise ValueError("operator Food recovery bundle must use dispatch=food-line")
    if payload.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise ValueError("operator recovery bundle must declare operator recovery provenance")
    if payload.get("original_production_artifacts_present") is not False:
        raise ValueError("operator recovery bundle must not claim original production artifacts")
    if not isinstance(payload.get("payloads"), list):
        raise ValueError("operator recovery bundle must contain payloads")
    return payload


def _read_intake(root: Path, edition_date: str, run_id: str) -> dict[str, Any]:
    path = root / "data/dispatches/food-line/agent-intake" / edition_date / f"{run_id}.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _source_verification(envelope: dict[str, Any]) -> dict[str, Any]:
    finding = (envelope.get("findings") or [{}])[0]
    if not isinstance(finding, dict):
        finding = {}
    return {
        "agent_run_id": envelope.get("agent_run_id"),
        "canonical_source_url": finding.get("canonical_source_url") or finding.get("source_url") or "",
        "publisher": finding.get("publisher") or finding.get("discovered_publisher") or "",
        "source_role": finding.get("source_role") or "",
        "supporting_evidence_present": bool(
            str(finding.get("exact_supporting_passage") or finding.get("evidence_text") or "").strip()
        ),
        "source_verification_status": finding.get("source_verification_status") or "syntactic_url_verified",
    }


def recover(root: Path, input_path: Path, output_path: Path) -> dict[str, Any]:
    bundle = _load_bundle(input_path)
    edition_date = str(bundle["edition_date"])
    rows: list[dict[str, Any]] = []
    source_verification: list[dict[str, Any]] = []
    accepted = 0
    unavailable = 0

    for envelope in bundle["payloads"]:
        if not isinstance(envelope, dict):
            unavailable += 1
            continue
        run_id = str(envelope.get("agent_run_id") or "")
        split_path = root / SPLIT_ROOT / f"{run_id}.json"
        _write_json(split_path, envelope)
        code, receipt = import_envelope(root, split_path, dispatch="food-line")
        intake = _read_intake(root, edition_date, run_id)
        candidate_rows = [row for row in intake.get("candidate_rows", []) if isinstance(row, dict)]
        if code == 0 and candidate_rows:
            accepted += 1
        else:
            unavailable += 1
        source_verification.append(_source_verification(envelope))
        for row in candidate_rows:
            disposition = str(row.get("review_retention_disposition") or "")
            rows.append(
                {
                    "agent_run_id": run_id,
                    "candidate_id": row.get("candidate_id") or "",
                    "title": row.get("title") or "",
                    "canonical_source_url": row.get("canonical_url") or row.get("canonical_source_url") or row.get("url") or "",
                    "publisher": row.get("publisher") or "",
                    "corrected_disposition": disposition,
                    "duplicate_linkage": row.get("duplicate_linkage") or {},
                    "reason": row.get("exclusion_reason") or "",
                    "provenance_class": row.get("lineage_class") or "",
                    "eligible_for_automatic_publication": row.get("eligible_for_automatic_publication"),
                    "resolution_status": "accounted" if disposition in TERMINAL else "unaccounted",
                    "receipt_status": receipt.get("status"),
                    "receipt_classification": receipt.get("classification"),
                }
            )

    dispositions = Counter(row["corrected_disposition"] for row in rows)
    result = {
        "schema_version": "bluefern.operator_recovered_source_watch_reconciliation.v1",
        "dispatch": "food-line",
        "edition_date": edition_date,
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "publication_performed": False,
        "pages_modified": False,
        "synthetic_evidence_used": False,
        "operator_preserved_payloads_submitted": len(bundle["payloads"]),
        "accepted_as_valid_recovery_evidence": accepted,
        "unavailable_or_incomplete": unavailable,
        "retained_for_review": dispositions["retained_for_review"],
        "duplicate_with_reason": dispositions["duplicate_with_reason"],
        "rejected_with_reason": dispositions["rejected_with_reason"],
        "deferred_with_reason": dispositions["deferred_with_reason"],
        "invalid_source_with_reason": dispositions["invalid_source_with_reason"],
        "unaccounted": sum(row["resolution_status"] == "unaccounted" for row in rows) + unavailable,
        "source_verification": source_verification,
        "rows": rows,
    }
    _write_json(root / output_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover Food Line operator-preserved Source Watch evidence")
    parser.add_argument("--root", "--runner-root", dest="root", type=Path, default=Path.cwd())
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    result = recover(root, root / args.input, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["unaccounted"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
