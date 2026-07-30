from __future__ import annotations
import argparse, base64, hashlib, io, json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.historical_agent_archive import DOMAINS, SCHEMA_VERSION, HistoricalEnvelopeError, _care_report, archive_root, atomic_json, build_inventory, normalize_records, parse_historical_input, sha256_bytes, validate_input

SUPPORTED_BATCH_EXTENSIONS = {".txt", ".md", ".json"}
IGNORED_BATCH_DIRECTORIES = {"corrections", "raw", "normalized", "reports", "batches", "archive"}


def _normalized_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_batch_input(path: Path, input_dir: Path, *, recursive: bool) -> bool:
    relative = path.relative_to(input_dir)
    if path.suffix.lower() not in SUPPORTED_BATCH_EXTENSIONS:
        return False
    if any(part.startswith(".") for part in relative.parts) or path.name.startswith(("~", "~$")):
        return False
    lowered = path.name.lower()
    if lowered.endswith((".tmp", ".temp", ".partial", ".swp")):
        return False
    if recursive and any(part.lower() in IGNORED_BATCH_DIRECTORIES for part in relative.parts[:-1]):
        return False
    return True


def discover_batch_files(input_dir: Path, *, recursive: bool = False) -> list[Path]:
    if not input_dir.is_dir():
        raise ValueError(f"batch input directory does not exist: {input_dir}")
    candidates = input_dir.rglob("*") if recursive else input_dir.glob("*")
    files = [path for path in candidates if path.is_file() and _is_batch_input(path, input_dir, recursive=recursive)]
    return sorted(files, key=lambda path: _normalized_relative(path, input_dir).casefold())


def batch_id_for(domain: str, files: list[Path]) -> str:
    ordered_hashes = [sha256_bytes(path.read_bytes()) for path in files]
    identity = json.dumps({"domain": domain, "input_hashes": ordered_hashes}, sort_keys=True, separators=(",", ":"))
    return f"batch-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _sidecar_payloads(input_dir: Path) -> list[tuple[Path, dict]]:
    correction_dir = input_dir / "corrections"
    if not correction_dir.is_dir():
        return []
    payloads: list[tuple[Path, dict]] = []
    for path in sorted(correction_dir.glob("*.json"), key=lambda item: item.name.casefold()):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            payloads.append((path, value))
    return payloads


def _validate_sidecar_identity(input_path: Path, sidecar_path: Path, sidecar: dict) -> None:
    identity = sidecar.get("finding_identity")
    declared_run_id = sidecar.get("agent_run_id")
    if isinstance(identity, dict):
        declared_run_id = identity.get("agent_run_id") or declared_run_id
    if not declared_run_id and not isinstance(identity, dict):
        return
    payload, _ = parse_historical_input(input_path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError(f"sidecar identity cannot be verified for {input_path.name}: {sidecar_path.name}")
    if declared_run_id and payload.get("agent_run_id") != declared_run_id:
        raise ValueError(f"sidecar agent_run_id mismatch for {input_path.name}: {sidecar_path.name}")
    if not isinstance(identity, dict):
        return
    declared_fields = {key: identity[key] for key in ("source_url", "title") if identity.get(key)}
    findings = payload.get("findings")
    if declared_fields and (
        not isinstance(findings, list)
        or not any(isinstance(row, dict) and all(row.get(key) == value for key, value in declared_fields.items()) for row in findings)
    ):
        raise ValueError(f"sidecar finding identity mismatch for {input_path.name}: {sidecar_path.name}")
    declared_finding_id = identity.get("finding_id")
    raw_finding_ids = {row.get("finding_id") for row in findings or [] if isinstance(row, dict) and row.get("finding_id")}
    if declared_finding_id and raw_finding_ids and declared_finding_id not in raw_finding_ids:
        raise ValueError(f"sidecar finding_id mismatch for {input_path.name}: {sidecar_path.name}")


def discover_sidecar(input_path: Path, *, input_dir: Path, repo_root: Path, domain: str, raw_sha256: str) -> Path | None:
    matches: list[Path] = []
    for path, sidecar in _sidecar_payloads(input_dir):
        declared_hash = str(sidecar.get("raw_sha256") or sidecar.get("raw_record_sha256") or "")
        declared_file = str(sidecar.get("raw_file") or "")
        declared_file_matches = bool(declared_file) and (repo_root / declared_file).resolve() == input_path.resolve()
        if declared_file_matches and declared_hash != raw_sha256:
            raise ValueError(f"sidecar raw SHA-256 mismatch for {input_path.name}: {path.name}")
        if declared_hash != raw_sha256:
            continue
        if sidecar.get("domain") not in (None, "", domain):
            raise ValueError(f"sidecar domain mismatch for {input_path.name}: {path.name}")
        if declared_file and (repo_root / declared_file).resolve() != input_path.resolve():
            raise ValueError(f"sidecar raw_file mismatch for {input_path.name}: {path.name}")
        scope = str(sidecar.get("approval_scope") or "")
        if "normalization" in scope and sidecar.get("publication_approval") is not False:
            raise ValueError(f"normalization-only sidecar contains publication approval: {path.name}")
        if sidecar.get("approved") is False and scope:
            raise ValueError(f"sidecar has conflicting approval state: {path.name}")
        _validate_sidecar_identity(input_path, path, sidecar)
        matches.append(path)
    if len(matches) > 1:
        raise ValueError(f"multiple sidecars match raw SHA-256 for {input_path.name}")
    return matches[0] if matches else None


def _invoke_single(operation: str, *, domain: str, input_path: Path, repo_root: Path, correction: Path | None, captured_at: str) -> tuple[int, dict]:
    argv = [operation, "--domain", domain, "--input", str(input_path), "--repo-root", str(repo_root)]
    if correction:
        argv.extend(["--correction", str(correction)])
    if captured_at:
        argv.extend(["--captured-at", captured_at])
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            code = main(argv)
        value = json.loads(output.getvalue())
        return code, value
    except Exception as exc:
        return 1, {"valid": False, "error": f"{type(exc).__name__}: {exc}"}


def _batch_format(validation: dict) -> str:
    return str(validation.get("normalization_method") or validation.get("source_format") or "unknown")


def _batch_aggregates(files: list[dict]) -> dict:
    outcomes: dict[str, int] = {}
    for item in files:
        for key, count in (item.get("outcomes") or {}).items():
            outcomes[key] = outcomes.get(key, 0) + int(count)
    imported_statuses = {"imported", "archived", "revised"}
    return {
        "total_files": len(files),
        "valid_files": sum(1 for item in files if item.get("validation_status") == "valid"),
        "imported_files": sum(1 for item in files if item.get("import_status") in imported_statuses),
        "idempotent_files": sum(1 for item in files if item.get("import_status") == "idempotent_noop"),
        "invalid_files": sum(1 for item in files if item.get("validation_status") == "invalid"),
        "failed_files": sum(1 for item in files if item.get("status") == "failed"),
        "candidates_created": sum(int(item.get("candidate_count") or 0) for item in files),
        "archived_invalid_findings": outcomes.get("archived_invalid", 0) + outcomes.get("invalid", 0),
        "archived_context_findings": outcomes.get("archived_context", 0),
        "matched_published_records": outcomes.get("matched_published_event", 0),
        "matched_reviewed_records": outcomes.get("matched_reviewed_event", 0),
        "duplicate_historical_records": outcomes.get("duplicate_historical", 0),
        "needs_manual_review_records": outcomes.get("needs_manual_review", 0),
        "publication_ready_count": 0,
    }


def _batch_main(args: argparse.Namespace) -> int:
    input_dir = args.input_dir.resolve()
    files = discover_batch_files(input_dir, recursive=args.recursive)
    batch_id = batch_id_for(args.domain, files)
    started_at = args.captured_at or datetime.now(timezone.utc).isoformat()
    entries: list[dict] = []
    for path in files:
        raw = path.read_bytes()
        digest = sha256_bytes(raw)
        entry = {
            "filename": _normalized_relative(path, input_dir),
            "sha256": digest,
            "detected_format": "unknown",
            "matching_sidecar": "",
            "validation_status": "invalid",
            "proposed_outcome": "",
            "import_status": "not_run",
            "status": "invalid",
            "outcomes": {},
            "candidate_count": 0,
            "artifact_paths": {},
        }
        try:
            correction = discover_sidecar(path, input_dir=input_dir, repo_root=args.repo_root.resolve(), domain=args.domain, raw_sha256=digest)
            entry["matching_sidecar"] = _normalized_relative(correction, input_dir) if correction else ""
            code, validation = _invoke_single("validate", domain=args.domain, input_path=path, repo_root=args.repo_root, correction=None, captured_at=started_at)
            entry["detected_format"] = _batch_format(validation)
            if code or not validation.get("valid"):
                entry["validation"] = validation
                entries.append(entry)
                continue
            entry["validation_status"] = "valid"
            entry["status"] = "valid"
            entry["validation"] = validation
            dry_code, proposed = _invoke_single("dry-run", domain=args.domain, input_path=path, repo_root=args.repo_root, correction=correction, captured_at=started_at)
            if dry_code:
                entry.update(status="failed", proposed_outcome="failed", error=proposed.get("error", "dry-run failed"))
            else:
                entry["proposed_outcome"] = str(proposed.get("outcome") or "")
                entry["outcomes"] = proposed.get("outcomes") or {}
                entry["candidate_count"] = int(proposed.get("candidate_count") or 0)
        except Exception as exc:
            entry.update(status="failed", validation_status="invalid", error=f"{type(exc).__name__}: {exc}")
        entries.append(entry)

    invalid = [entry for entry in entries if entry["validation_status"] != "valid" or entry["status"] == "failed"]
    operation = args.operation.removeprefix("batch-")
    blocked_validation = False
    if operation == "import":
        blocked_validation = bool(invalid) and not args.allow_partial_import
        for entry, path in zip(entries, files):
            if blocked_validation:
                entry["import_status"] = "skipped"
                if entry["status"] == "valid":
                    entry["status"] = "skipped"
                continue
            if entry["validation_status"] != "valid" or entry["status"] == "failed":
                entry["import_status"] = "skipped"
                continue
            correction = input_dir / entry["matching_sidecar"] if entry["matching_sidecar"] else None
            code, imported = _invoke_single("import", domain=args.domain, input_path=path, repo_root=args.repo_root, correction=correction, captured_at=started_at)
            if code:
                entry.update(status="failed", import_status="failed", error=imported.get("error", "import failed"))
                continue
            entry["import_status"] = str(imported.get("status") or "imported")
            entry["status"] = entry["import_status"]
            entry["outcomes"] = imported.get("outcomes") or entry["outcomes"]
            entry["candidate_count"] = int(imported.get("candidate_count") or 0)
            digest = entry["sha256"]
            base = archive_root(args.repo_root, args.domain)
            entry["artifact_paths"] = {
                "raw": str(base / "raw" / f"{digest}.json"),
                "normalized": str(base / "normalized" / f"{digest}.json"),
                "report": str(base / "reports" / f"{digest}.json"),
            }
        invalid = [entry for entry in entries if entry["validation_status"] != "valid" or entry["status"] == "failed"]

    result = {
        "schema_version": "historical_agent_batch_v1",
        "batch_id": batch_id,
        "domain": args.domain,
        "command": args.operation,
        "started_at": started_at,
        "input_directory": str(input_dir),
        "file_count": len(files),
        "ordered_files": [entry["filename"] for entry in entries],
        "files": entries,
    }
    result.update(_batch_aggregates(entries))
    if operation == "import":
        result["status"] = "blocked_validation" if blocked_validation else ("partial_failed" if any(entry["status"] == "failed" for entry in entries) else ("partial_completed" if invalid else "completed"))
        report_path = archive_root(args.repo_root, args.domain) / "reports" / "batches" / f"{batch_id}.json"
        result["report_path"] = str(report_path)
        atomic_json(report_path, result)
    else:
        result["status"] = "invalid" if invalid else "valid"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if invalid else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preserve and normalize historical agent exports privately")
    parser.add_argument("operation", choices=["validate", "dry-run", "import", "inventory", "normalize", "report", "batch-validate", "batch-dry-run", "batch-import"])
    parser.add_argument("--domain", choices=DOMAINS); parser.add_argument("--input", type=Path); parser.add_argument("--input-dir", type=Path); parser.add_argument("--correction", type=Path); parser.add_argument("--repo-root", type=Path, default=Path.cwd()); parser.add_argument("--captured-at", default="")
    parser.add_argument("--recursive", action="store_true"); parser.add_argument("--allow-partial-import", action="store_true")
    args = parser.parse_args(argv)
    if args.operation.startswith("batch-"):
        if not args.domain or not args.input_dir:
            parser.error("--domain and --input-dir are required for batch operations")
        return _batch_main(args)
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
