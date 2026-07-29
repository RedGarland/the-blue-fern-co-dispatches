from __future__ import annotations
import argparse, base64, json
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.historical_agent_archive import DOMAINS, SCHEMA_VERSION, HistoricalEnvelopeError, _care_report, archive_root, atomic_json, build_inventory, normalize_records, parse_historical_input, sha256_bytes, validate_input

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preserve and normalize historical agent exports privately")
    parser.add_argument("operation", choices=["validate", "dry-run", "import", "inventory", "normalize", "report"])
    parser.add_argument("--domain", choices=DOMAINS); parser.add_argument("--input", type=Path); parser.add_argument("--correction", type=Path); parser.add_argument("--repo-root", type=Path, default=Path.cwd()); parser.add_argument("--captured-at", default="")
    args = parser.parse_args(argv)
    if args.operation == "inventory":
        result = build_inventory(args.repo_root)
        full_result = result
        if args.domain: result = {"domain": args.domain, "inventory": full_result["domains"][args.domain]}
        if args.operation == "inventory":
            path = args.repo_root / "data/agent-history/history-index.json"; atomic_json(path, result) if args.input is None else None
            if args.input is None:
                for domain in DOMAINS: atomic_json(archive_root(args.repo_root, domain) / "reports" / "history-index.json", full_result["domains"][domain])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0
    if not args.domain or not args.input: parser.error("--domain and --input are required")
    validation = validate_input(args.input, domain=args.domain)
    if args.operation == "validate": print(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2)); return 0 if validation["valid"] else 1
    raw = args.input.read_bytes(); digest = sha256_bytes(raw); captured = args.captured_at or datetime.now(timezone.utc).isoformat()
    try: payload, normalization_metadata = parse_historical_input(raw)
    except HistoricalEnvelopeError as exc:
        print(json.dumps({"valid": False, "domain": args.domain, "input_sha256": digest, "error": str(exc), "dry_run": args.operation in {"dry-run", "report"}}, ensure_ascii=False, sort_keys=True, indent=2)); return 1
    correction = json.loads(args.correction.read_text(encoding="utf-8")) if args.correction else None
    if args.domain == "care-line" and correction is not None:
        expected_raw = (args.repo_root / str(correction.get("raw_file") or "")).resolve()
        if expected_raw != args.input.resolve():
            raise ValueError("Care Line normalization sidecar raw_file does not match the supplied alert")
        raw_text = raw.decode("utf-8", errors="replace")
        for finding in correction.get("findings", []):
            source_url = str(finding.get("source_url") or "") if isinstance(finding, dict) else ""
            if not source_url or source_url not in raw_text:
                raise ValueError("Care Line normalization sidecar source URL is not present in the preserved alert")
    normalized, outcomes = normalize_records(args.repo_root, args.domain, payload, raw_sha256=digest, captured_at=captured, correction=correction, normalization_metadata=normalization_metadata)
    candidate_count = sum(outcomes.get(key, 0) for key in ("new_historical_candidate", "matched_existing")); invalid_count = outcomes.get("invalid", 0) + outcomes.get("archived_invalid", 0)
    result = {"valid": validation["valid"], "domain": args.domain, "input_sha256": digest, "raw_record_count": 1, "normalized_finding_count": len(normalized), "outcomes": outcomes, "candidate_count": candidate_count, "invalid_finding_count": invalid_count, "outcome": "archived_invalid" if invalid_count and not candidate_count else ("candidate_ready" if candidate_count else "needs_manual_review"), "correction_path": str(args.correction) if args.correction else "", "normalization_method": normalization_metadata.get("normalization_method"), "dry_run": args.operation in {"dry-run", "report"}}
    if args.domain == "care-line":
        result["care_line_findings"] = [_care_report(record) for record in normalized]
    if args.operation in {"import", "normalize"}:
        base = archive_root(args.repo_root, args.domain); raw_path = base / "raw" / f"{digest}.json"; normalized_path = base / "normalized" / f"{digest}.json"; report_path = base / "reports" / f"{digest}.json"
        correction_digest = sha256_bytes(args.correction.read_bytes())[:16] if args.correction else ""
        if raw_path.exists() and correction_digest:
            base_normalized_path, base_report_path = normalized_path, report_path
            if args.domain == "care-line" and base_normalized_path.exists():
                try:
                    prior_normalized = json.loads(base_normalized_path.read_text(encoding="utf-8"))
                    already_applied = any((item.get("normalization_sidecar") or {}).get("raw_sha256") == digest for item in prior_normalized.get("findings", []))
                except (OSError, ValueError, AttributeError):
                    already_applied = False
                if already_applied:
                    normalized_path, report_path = base_normalized_path, base_report_path
                else:
                    normalized_path = base / "normalized" / f"revision-{digest[:16]}-{correction_digest}.json"; report_path = base / "reports" / f"revision-{digest[:16]}-{correction_digest}.json"
            else:
                normalized_path = base / "normalized" / f"revision-{digest[:16]}-{correction_digest}.json"; report_path = base / "reports" / f"revision-{digest[:16]}-{correction_digest}.json"
            normalized_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.exists():
            prior = json.loads(raw_path.read_text(encoding="utf-8"))
            if prior.get("raw_sha256") != digest: raise ValueError("content-addressed archive collision")
            if normalized_path.exists(): result["status"] = "idempotent_noop"
            elif correction_digest:
                atomic_json(normalized_path, {"schema_version": "historical_agent_normalized_v1", "domain": args.domain, "raw_sha256": digest, "revision_of": f"{digest}.json", "correction_sha256": correction_digest, "normalization_method": normalization_metadata.get("normalization_method"), "private_text_provenance": normalization_metadata.get("private_text_provenance"), "agent_run_id": payload.get("agent_run_id", "") if isinstance(payload, dict) else "", "started_at": payload.get("started_at", "") if isinstance(payload, dict) else "", "completed_at": payload.get("completed_at", "") if isinstance(payload, dict) else "", "search_window": payload.get("search_window", {}) if isinstance(payload, dict) else {}, "findings": normalized}); result["status"] = "revised"
                atomic_json(report_path, result)
            else: result["status"] = "idempotent_noop"
        else:
            record = {"schema_version": SCHEMA_VERSION, "domain": args.domain, "agent_name": payload.get("agent_name", "historical-agent") if isinstance(payload, dict) else "historical-agent", "agent_run_id": payload.get("agent_run_id", "") if isinstance(payload, dict) else "", "captured_at": captured, "original_run_at": payload.get("started_at", "") if isinstance(payload, dict) else "", "search_window": payload.get("search_window", {}) if isinstance(payload, dict) else {}, "source_format": validation["source_format"], "raw_text": raw.decode("utf-8", errors="replace"), "raw_bytes_base64": base64.b64encode(raw).decode("ascii"), "raw_sha256": digest, "source_chat_or_export_reference": "", "normalization_status": "pending_review", "imported_at": datetime.now(timezone.utc).isoformat()}
            record.update(normalization_metadata)
            atomic_json(raw_path, record); atomic_json(normalized_path, {"schema_version": "historical_agent_normalized_v1", "domain": args.domain, "raw_sha256": digest, "normalization_method": normalization_metadata.get("normalization_method"), "private_text_provenance": normalization_metadata.get("private_text_provenance"), "agent_run_id": payload.get("agent_run_id", "") if isinstance(payload, dict) else "", "started_at": payload.get("started_at", "") if isinstance(payload, dict) else "", "completed_at": payload.get("completed_at", "") if isinstance(payload, dict) else "", "search_window": payload.get("search_window", {}) if isinstance(payload, dict) else {}, "findings": normalized}); result["status"] = "imported"; atomic_json(report_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0 if validation["valid"] else 1

if __name__ == "__main__": raise SystemExit(main())
