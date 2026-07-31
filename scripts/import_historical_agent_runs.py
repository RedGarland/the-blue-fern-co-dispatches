from __future__ import annotations
import argparse, base64, hashlib, io, json, re
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.historical_agent_archive import DOMAINS, SCHEMA_VERSION, HistoricalEnvelopeError, _care_report, _gaza_report, _ice_report, archive_root, atomic_json, build_inventory, canonical_json, normalize_records, parse_historical_input, sha256_bytes, validate_input
from bluefern_dispatches.ice_historical import explicit_detection_date_text, extract_detection_date, ice_aggregate_metrics, normalize_detection_date

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


def _raw_supports_date(raw_text: str, value: object) -> bool:
    token = str(value or "").strip()[:10]
    if not token:
        return True
    if token in raw_text:
        return True
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return False
    variants = {
        f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}",
        f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}",
        f"{parsed.month}/{parsed.day}/{parsed.year}",
        f"{parsed.month:02d}/{parsed.day:02d}/{parsed.year}",
    }
    lower_raw = raw_text.lower()
    return any(variant.lower() in lower_raw for variant in variants)


def _raw_supports_agency(raw_text: str, value: object) -> bool:
    token = str(value or "").strip()
    if not token:
        return True
    lower_raw = raw_text.lower()
    if token.lower() in lower_raw:
        return True
    lower_token = token.lower()
    if "immigration and customs enforcement" in lower_token and re.search(r"\bice\b", raw_text, flags=re.I):
        return True
    if "department of homeland security" in lower_token and re.search(r"\bdhs\b", raw_text, flags=re.I):
        return True
    return False


def _raw_supports_count(evidence: str, field: str, value: object) -> bool:
    if value in (None, ""):
        return True
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    words = {
        0: ("zero", "no"),
        1: ("one",),
        2: ("two",),
        3: ("three",),
        4: ("four",),
        5: ("five",),
        6: ("six",),
        7: ("seven",),
        8: ("eight",),
        9: ("nine",),
        10: ("ten",),
    }
    count_present = bool(re.search(rf"\b{number}\b", evidence)) or any(re.search(rf"\b{word}\b", evidence, flags=re.I) for word in words.get(number, ()))
    concepts = {
        "fatalities": ("fatal", "death", "died", "killed"),
        "serious_injuries": ("serious injur", "injured"),
        "hospitalizations": ("hospital"),
    }
    return count_present and any(concept in evidence.lower() for concept in concepts[field])


def _validate_ice_sidecar_against_raw(raw_text: str, correction: dict) -> None:
    raw_detection_date = extract_detection_date(raw_text)
    for finding in correction.get("findings", []):
        if not isinstance(finding, dict):
            continue
        passage = str(finding.get("exact_supporting_passage") or "")
        if not passage or passage not in raw_text:
            raise ValueError("ICE normalization sidecar exact supporting passage is not present in the preserved alert")
        for field in ("event_date", "source_published_at"):
            if not _raw_supports_date(raw_text, finding.get(field)):
                raise ValueError(f"ICE normalization sidecar {field} is not supported by the preserved alert")
        if finding.get("detection_date") not in (None, ""):
            sidecar_detection_date = normalize_detection_date(finding.get("detection_date"))
            if raw_detection_date is None:
                raise ValueError("ICE normalization sidecar detection_date lacks an explicit raw-alert Detection Date")
            if sidecar_detection_date != raw_detection_date:
                raise ValueError("ICE normalization sidecar detection_date conflicts with the preserved alert")
        for field in ("location_name", "city", "county", "facility_name"):
            token = str(finding.get(field) or "").strip()
            if token and token.lower() not in raw_text.lower():
                raise ValueError(f"ICE normalization sidecar {field} is not supported by the preserved alert")
        if not _raw_supports_agency(raw_text, finding.get("agency")):
            raise ValueError("ICE normalization sidecar agency is not supported by the preserved alert")
        for field in ("fatalities", "serious_injuries", "hospitalizations"):
            if not _raw_supports_count(passage, field, finding.get(field)):
                raise ValueError(f"ICE normalization sidecar {field} is not supported by its exact passage")


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


def _batch_aggregates(files: list[dict], *, domain: str = "") -> dict:
    outcomes: dict[str, int] = {}
    for item in files:
        for key, count in (item.get("outcomes") or {}).items():
            outcomes[key] = outcomes.get(key, 0) + int(count)
    imported_statuses = {"imported", "archived", "revised"}
    result = {
        "total_files": len(files),
        "valid_files": sum(1 for item in files if item.get("validation_status") == "valid"),
        "imported_files": sum(1 for item in files if item.get("import_status") in imported_statuses),
        "idempotent_files": sum(1 for item in files if item.get("import_status") == "idempotent_noop"),
        "invalid_files": sum(1 for item in files if item.get("validation_status") == "invalid"),
        "failed_files": sum(1 for item in files if item.get("status") == "failed"),
        "candidates_created": sum(int(item.get("candidate_count") or 0) for item in files),
        "archived_invalid_findings": outcomes.get("archived_invalid", 0) + outcomes.get("invalid", 0),
        "archived_context_findings": outcomes.get("archived_context", 0),
        "matched_published_records": outcomes.get("matched_published_event", 0) + outcomes.get("matched_published_edition", 0),
        "matched_reviewed_records": outcomes.get("matched_reviewed_event", 0),
        "duplicate_historical_records": outcomes.get("duplicate_historical", 0),
        "needs_manual_review_records": outcomes.get("needs_manual_review", 0),
        "publication_ready_count": 0,
    }
    if domain == "ice":
        findings = [finding for item in files for finding in item.get("ice_findings", []) if isinstance(finding, dict)]
        result.update(ice_aggregate_metrics(findings, raw_runs=len(files)))
    return result


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
                if args.domain == "ice":
                    entry["ice_findings"] = proposed.get("ice_findings") or []
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
            if args.domain == "ice":
                entry["ice_findings"] = imported.get("ice_findings") or entry.get("ice_findings") or []
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
    result.update(_batch_aggregates(entries, domain=args.domain))
    if operation == "import":
        result["status"] = "blocked_validation" if blocked_validation else ("partial_failed" if any(entry["status"] == "failed" for entry in entries) else ("partial_completed" if invalid else "completed"))
        report_path = archive_root(args.repo_root, args.domain) / "reports" / "batches" / f"{batch_id}.json"
        result["report_path"] = str(report_path)
        atomic_json(report_path, result)
        history_index_path = archive_root(args.repo_root, args.domain) / "reports" / "history-index.json"
        atomic_json(history_index_path, build_inventory(args.repo_root)["domains"][args.domain])
        result["history_index_path"] = str(history_index_path)
    else:
        result["status"] = "invalid" if invalid else "valid"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if invalid else 0


def _renormalize_ice(args: argparse.Namespace) -> int:
    if args.domain != "ice":
        raise ValueError("renormalize currently supports only the ice domain")
    repo_root = args.repo_root.resolve()
    input_path = args.input.resolve()
    raw = input_path.read_bytes()
    digest = sha256_bytes(raw)
    raw_text = raw.decode("utf-8", errors="replace")
    base = archive_root(repo_root, "ice")
    raw_path = base / "raw" / f"{digest}.json"
    normalized_path = base / "normalized" / f"{digest}.json"
    report_path = base / "reports" / f"{digest}.json"
    if not raw_path.is_file() or not normalized_path.is_file() or not report_path.is_file():
        raise ValueError("renormalize requires an existing raw, normalized, and per-record private archive")

    correction_path = args.correction.resolve() if args.correction else discover_sidecar(
        input_path,
        input_dir=input_path.parent,
        repo_root=repo_root,
        domain="ice",
        raw_sha256=digest,
    )
    if correction_path is None:
        raise ValueError("renormalize requires an approved ICE normalization sidecar")
    correction = json.loads(correction_path.read_text(encoding="utf-8"))
    expected_raw = (repo_root / str(correction.get("raw_file") or "")).resolve()
    if expected_raw != input_path:
        raise ValueError("ICE normalization sidecar raw_file does not match the supplied alert")
    _validate_ice_sidecar_against_raw(raw_text, correction)
    payload, normalization_metadata = parse_historical_input(raw)
    normalize_records(
        repo_root,
        "ice",
        payload,
        raw_sha256=digest,
        captured_at="",
        correction=correction,
        normalization_metadata=normalization_metadata,
    )

    raw_record = json.loads(raw_path.read_text(encoding="utf-8"))
    if raw_record.get("raw_sha256") != digest:
        raise ValueError("archived raw SHA-256 does not match the supplied alert")
    try:
        archived_bytes = base64.b64decode(str(raw_record.get("raw_bytes_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("archived raw bytes are invalid") from exc
    if archived_bytes != raw or sha256_bytes(archived_bytes) != digest:
        raise ValueError("archived raw bytes differ from the supplied immutable alert")

    prior_bytes = normalized_path.read_bytes()
    prior = json.loads(prior_bytes.decode("utf-8"))
    if prior.get("raw_sha256") != digest:
        raise ValueError("normalized record identity does not match the supplied raw SHA-256")
    findings = prior.get("findings")
    if not isinstance(findings, list):
        raise ValueError("normalized ICE record has no findings list")
    existing_by_id = {
        str(item.get("finding_id") or ""): item
        for item in findings
        if isinstance(item, dict) and item.get("finding_id")
    }
    changed_fields: list[dict] = []
    for sidecar_finding in correction.get("findings", []):
        if not isinstance(sidecar_finding, dict) or sidecar_finding.get("detection_date") in (None, ""):
            continue
        finding_id = str(sidecar_finding.get("finding_id") or "")
        existing = existing_by_id.get(finding_id)
        if existing is None:
            raise ValueError(f"approved sidecar finding is absent from normalized record: {finding_id}")
        new_value = normalize_detection_date(sidecar_finding.get("detection_date"))
        old_value = normalize_detection_date(existing.get("detection_date"))
        if old_value not in (None, new_value):
            raise ValueError("renormalize refuses to replace a conflicting existing detection_date")
        if old_value != new_value:
            changed_fields.append(
                {
                    "field": "detection_date",
                    "finding_id": finding_id,
                    "old_value": old_value,
                    "new_value": new_value,
                }
            )

    sidecar_digest = sha256_bytes(correction_path.read_bytes())
    audit_path = base / "reports" / "maintenance" / f"{digest[:16]}-{sidecar_digest[:16]}.json"
    inventory_before = build_inventory(repo_root)["domains"]["ice"]
    if not changed_fields:
        result = {
            "status": "idempotent_noop",
            "domain": "ice",
            "raw_sha256": digest,
            "normalized_path": str(normalized_path),
            "report_path": str(report_path),
            "maintenance_audit_path": str(audit_path),
            "detection_date": next(
                (
                    normalize_detection_date(item.get("detection_date"))
                    for item in findings
                    if isinstance(item, dict) and item.get("detection_date") not in (None, "")
                ),
                None,
            ),
            "inventory": inventory_before,
            "publication_approval": False,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    maintenance_at = datetime.now(timezone.utc).isoformat()
    updated = json.loads(json.dumps(prior, ensure_ascii=False))
    updated_by_id = {
        str(item.get("finding_id") or ""): item
        for item in updated["findings"]
        if isinstance(item, dict) and item.get("finding_id")
    }
    for change in changed_fields:
        updated_by_id[change["finding_id"]]["detection_date"] = change["new_value"]
    updated["last_normalized_at"] = maintenance_at
    previous_digest = sha256_bytes(prior_bytes)
    new_digest = sha256_bytes(canonical_json(updated).encode("utf-8"))
    raw_detection_text = explicit_detection_date_text(raw_text)
    audit = {
        "schema_version": "historical_normalization_maintenance_v1",
        "domain": "ice",
        "raw_sha256": digest,
        "normalized_record_identity": f"{digest}.json",
        "normalized_record_path": str(normalized_path),
        "original_import_report_path": str(report_path),
        "previous_normalized_record_digest": previous_digest,
        "new_normalized_record_digest": new_digest,
        "changed_fields": changed_fields,
        "reason": args.maintenance_reason,
        "source_evidence": {
            "location": "raw alert field: Detection Date",
            "raw_value": raw_detection_text,
            "normalized_value": extract_detection_date(raw_text),
        },
        "sidecar_path": str(correction_path),
        "sidecar_sha256": sidecar_digest,
        "reviewer": correction.get("reviewer"),
        "approval_scope": correction.get("approval_scope"),
        "maintenance_timestamp": maintenance_at,
        "captured_at": raw_record.get("captured_at"),
        "imported_at": raw_record.get("imported_at"),
        "last_normalized_at": maintenance_at,
        "publication_approval": False,
    }
    atomic_json(audit_path, audit)
    atomic_json(normalized_path, updated)
    inventory_after = build_inventory(repo_root)["domains"]["ice"]
    history_index_path = base / "reports" / "history-index.json"
    atomic_json(history_index_path, inventory_after)
    result = {
        "status": "renormalized",
        "domain": "ice",
        "raw_sha256": digest,
        "normalized_path": str(normalized_path),
        "report_path": str(report_path),
        "maintenance_audit_path": str(audit_path),
        "history_index_path": str(history_index_path),
        "previous_normalized_record_digest": previous_digest,
        "new_normalized_record_digest": new_digest,
        "changed_fields": changed_fields,
        "detection_date": changed_fields[0]["new_value"],
        "inventory_before": inventory_before,
        "inventory_after": inventory_after,
        "publication_approval": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _review_historical_candidate(args: argparse.Namespace) -> int:
    supported_decisions = {
        ("ice", "substantively-valid"): {
            "audit_decision": "accept_substantively_valid_historical_candidate",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "new_status": "substantively_reviewed",
        },
    }
    decision_spec = supported_decisions.get((args.domain, args.decision))
    if decision_spec is None:
        supported = ", ".join(
            f"{domain}:{decision}"
            for domain, decision in sorted(supported_decisions)
        )
        raise ValueError(
            "unsupported historical substantive review decision "
            f"{args.domain!r}:{args.decision!r}; supported decisions: {supported}"
        )

    raw_sha = str(args.raw_sha or "")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_sha):
        raise ValueError("--raw-sha must be an exact lowercase SHA-256 digest")
    expected_review_sha = str(args.review_artifact_sha256 or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_review_sha):
        raise ValueError(
            "--review-artifact-sha256 must be an exact lowercase SHA-256 digest"
        )

    repo_root = args.repo_root.resolve()
    base = archive_root(repo_root, args.domain)
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    required_paths = (raw_path, normalized_path, report_path)
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise ValueError(
            "historical review target is incomplete; missing: " + ", ".join(missing)
        )

    review_path = args.review_artifact.resolve()
    reviews_root = (base / "reviews").resolve()
    try:
        review_relative = review_path.relative_to(reviews_root)
    except ValueError as exc:
        raise ValueError(
            f"review artifact must be under the private review root {reviews_root}"
        ) from exc
    if not review_relative.parts or review_relative.parts[0].lower() == "decisions":
        raise ValueError("review artifact must be an independent substantive review")
    if not review_path.is_file():
        raise ValueError(f"review artifact does not exist: {review_path}")

    actual_review_sha = sha256_bytes(review_path.read_bytes())
    if actual_review_sha != expected_review_sha:
        raise ValueError(
            "review artifact SHA-256 mismatch: "
            f"expected {expected_review_sha}, found {actual_review_sha}"
        )

    raw_record = json.loads(raw_path.read_text(encoding="utf-8"))
    normalized_record = json.loads(normalized_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    for label, payload in (
        ("raw archive", raw_record),
        ("normalized record", normalized_record),
        ("import report", report),
        ("substantive review", review),
    ):
        if not isinstance(payload, dict):
            raise ValueError(f"{label} must be a JSON object")
        if payload.get("domain") != args.domain:
            raise ValueError(f"{label} domain does not match {args.domain!r}")
    if raw_record.get("raw_sha256") != raw_sha:
        raise ValueError("raw archive SHA-256 identity does not match")
    if normalized_record.get("raw_sha256") != raw_sha:
        raise ValueError("normalized record SHA-256 identity does not match")
    if report.get("input_sha256") != raw_sha:
        raise ValueError("import report SHA-256 identity does not match")
    if review.get("raw_sha256") != raw_sha:
        raise ValueError("substantive review SHA-256 identity does not match")

    encoded_raw = raw_record.get("raw_bytes_base64")
    if not isinstance(encoded_raw, str):
        raise ValueError("historical raw archive raw_bytes_base64 is missing")
    try:
        decoded_raw = base64.b64decode(encoded_raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("historical raw archive raw_bytes_base64 is malformed") from exc
    if sha256_bytes(decoded_raw) != raw_sha:
        raise ValueError("historical raw archive bytes do not match the requested SHA-256")

    findings = normalized_record.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("historical normalized record contains no findings")
    finding_id = review.get("normalized_finding_id")
    if not isinstance(finding_id, str) or not finding_id:
        raise ValueError("substantive review normalized_finding_id is missing")
    matching_indexes = [
        index
        for index, finding in enumerate(findings)
        if isinstance(finding, dict) and finding.get("finding_id") == finding_id
    ]
    if len(matching_indexes) != 1:
        raise ValueError(
            "substantive review must identify exactly one normalized finding"
        )
    finding_index = matching_indexes[0]
    finding = findings[finding_index]

    required_review_values = {
        "recommended_disposition": decision_spec["recommended_disposition"],
        "archive_mutation_authorized": False,
        "publication_authorized": False,
        "current_review_status": "pending_review",
        "current_queue_action": "none",
        "current_publication_eligible": False,
        "current_publication_approval": False,
    }
    for key, expected in required_review_values.items():
        if review.get(key) != expected:
            raise ValueError(
                f"substantive review {key} must be exactly {expected!r}"
            )
    severity_review = review.get("severity_review")
    if not isinstance(severity_review, dict) or severity_review.get("current_severity") != "high":
        raise ValueError("substantive review must preserve severity high")
    if finding.get("historical_outcome") != "new_historical_candidate":
        raise ValueError(
            "only a new_historical_candidate may receive this substantive decision"
        )
    if finding.get("severity") != "high":
        raise ValueError("ICE substantive-valid decision requires severity high")
    if finding.get("queue_action") != "none":
        raise ValueError("historical candidate queue_action must be none")
    if finding.get("publication_eligible") is not False:
        raise ValueError("historical candidate publication_eligible must be false")
    if finding.get("publication_approval") is not False:
        raise ValueError("historical candidate publication_approval must be false")

    decisions_dir = base / "reviews" / "decisions"
    audit_path = decisions_dir / (
        f"{raw_sha[:24]}-accept-substantively-valid.json"
    )
    immutable_audit_values = {
        "schema_version": "historical_substantive_review_decision_v1",
        "domain": args.domain,
        "raw_sha256": raw_sha,
        "normalized_finding_id": finding_id,
        "review_artifact_sha256": actual_review_sha,
        "operator": args.operator,
        "decision": decision_spec["audit_decision"],
        "previous_review_status": "pending_review",
        "new_review_status": decision_spec["new_status"],
        "historical_outcome": finding.get("historical_outcome"),
        "severity": finding.get("severity"),
        "queue_action": "none",
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "archive_content_change_authorized": False,
    }
    inventory_before = build_inventory(repo_root)["domains"][args.domain]
    previous_status = finding.get("review_status")

    if previous_status == decision_spec["new_status"]:
        if not audit_path.is_file():
            raise ValueError(
                "finding is substantively reviewed but its decision audit is missing"
            )
        existing_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(existing_audit, dict):
            raise ValueError("existing substantive decision audit must be a JSON object")
        for key, expected in immutable_audit_values.items():
            if existing_audit.get(key) != expected:
                raise ValueError(
                    f"existing substantive decision audit conflicts at {key}"
                )
        result = {
            "status": "idempotent_noop",
            "domain": args.domain,
            "raw_sha256": raw_sha,
            "normalized_finding_id": finding_id,
            "review_status": previous_status,
            "review_artifact_sha256": actual_review_sha,
            "decision_audit_path": str(audit_path),
            "inventory": inventory_before,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    if previous_status != "pending_review":
        raise ValueError(
            "historical candidate review status must be pending_review or "
            f"{decision_spec['new_status']}"
        )
    if audit_path.exists():
        raise ValueError(
            "a substantive decision audit already exists for a pending candidate"
        )

    updated_record = json.loads(json.dumps(normalized_record, ensure_ascii=False))
    updated_finding = updated_record["findings"][finding_index]
    updated_finding["review_status"] = decision_spec["new_status"]
    before_without_status = {
        key: value for key, value in finding.items() if key != "review_status"
    }
    after_without_status = {
        key: value for key, value in updated_finding.items() if key != "review_status"
    }
    if before_without_status != after_without_status:
        raise RuntimeError(
            "substantive review attempted to change immutable finding content"
        )

    decided_at = datetime.now(timezone.utc).isoformat()
    try:
        review_artifact_display = review_path.relative_to(repo_root).as_posix()
    except ValueError:
        review_artifact_display = review_path.as_posix()
    audit = {
        **immutable_audit_values,
        "review_artifact_path": review_artifact_display,
        "decided_at": decided_at,
        "normalized_record_sha256_before": sha256_bytes(normalized_path.read_bytes()),
        "normalized_record_sha256_after": sha256_bytes(
            canonical_json(updated_record).encode("utf-8")
        ),
        "changed_fields": ["findings[].review_status"],
        "notes": [
            "Attribute aggregate figures to Reuters' review of internal federal data and state that DHS confirmed the information-sharing relationship.",
            "Do not describe the complete dataset as public, all arrested people as children, or 460,000 leads as 460,000 unique people.",
            "Do not add unsupported criminal-history, geographic, or case-outcome detail.",
            "Treat the gunpoint arrest as one documented case, not a national force pattern.",
            "Do not claim data sharing alone caused longer ORR custody.",
            "Keep government positions, allegations, and unknowns separate.",
        ],
    }
    decisions_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(audit_path, audit)
    atomic_json(normalized_path, updated_record)
    inventory_after = build_inventory(repo_root)["domains"][args.domain]
    index_path = base / "reports" / "history-index.json"
    atomic_json(index_path, inventory_after)
    result = {
        "status": "review_status_updated",
        "domain": args.domain,
        "raw_sha256": raw_sha,
        "normalized_finding_id": finding_id,
        "previous_review_status": previous_status,
        "new_review_status": decision_spec["new_status"],
        "review_artifact_sha256": actual_review_sha,
        "decision_audit_path": str(audit_path),
        "inventory_before": inventory_before,
        "inventory_after": inventory_after,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preserve and normalize historical agent exports privately")
    parser.add_argument("operation", choices=["validate", "dry-run", "import", "inventory", "normalize", "report", "renormalize", "review", "batch-validate", "batch-dry-run", "batch-import"])
    parser.add_argument("--domain", choices=DOMAINS); parser.add_argument("--input", type=Path); parser.add_argument("--input-dir", type=Path); parser.add_argument("--correction", type=Path); parser.add_argument("--repo-root", type=Path, default=Path.cwd()); parser.add_argument("--captured-at", default="")
    parser.add_argument("--recursive", action="store_true"); parser.add_argument("--allow-partial-import", action="store_true")
    parser.add_argument("--maintenance-reason", default="add first-class ICE detection_date from an explicit raw-alert field")
    parser.add_argument("--raw-sha")
    parser.add_argument("--decision")
    parser.add_argument("--review-artifact", type=Path)
    parser.add_argument("--review-artifact-sha256")
    parser.add_argument("--operator", default="William Patton")
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
    if args.operation == "review":
        missing = [
            name
            for name, value in (
                ("--domain", args.domain),
                ("--raw-sha", args.raw_sha),
                ("--decision", args.decision),
                ("--review-artifact", args.review_artifact),
                ("--review-artifact-sha256", args.review_artifact_sha256),
            )
            if not value
        ]
        if missing:
            parser.error("review requires " + ", ".join(missing))
        return _review_historical_candidate(args)
    if not args.domain or not args.input: parser.error("--domain and --input are required")
    if args.operation == "renormalize":
        return _renormalize_ice(args)
    validation = validate_input(args.input, domain=args.domain)
    if args.operation == "validate": print(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2)); return 0 if validation["valid"] else 1
    raw = args.input.read_bytes(); digest = sha256_bytes(raw); captured = args.captured_at or datetime.now(timezone.utc).isoformat()
    try: payload, normalization_metadata = parse_historical_input(raw)
    except HistoricalEnvelopeError as exc:
        print(json.dumps({"valid": False, "domain": args.domain, "input_sha256": digest, "error": str(exc), "dry_run": args.operation in {"dry-run", "report"}}, ensure_ascii=False, sort_keys=True, indent=2)); return 1
    correction = json.loads(args.correction.read_text(encoding="utf-8")) if args.correction else None
    if args.domain in {"care-line", "gaza", "ice"} and correction is not None:
        label = {"care-line": "Care Line", "gaza": "Gaza", "ice": "ICE"}[args.domain]
        expected_raw = (args.repo_root / str(correction.get("raw_file") or "")).resolve()
        if expected_raw != args.input.resolve():
            raise ValueError(f"{label} normalization sidecar raw_file does not match the supplied alert")
        raw_text = raw.decode("utf-8", errors="replace")
        for finding in correction.get("findings", []):
            source_url = str(finding.get("source_url") or "") if isinstance(finding, dict) else ""
            if not source_url or source_url not in raw_text:
                raise ValueError(f"{label} normalization sidecar source URL is not present in the preserved alert")
        if args.domain == "ice":
            _validate_ice_sidecar_against_raw(raw_text, correction)
        if args.domain in {"gaza", "ice"}:
            normalization_metadata = dict(normalization_metadata)
            normalization_metadata["normalization_method"] = correction.get("normalization_type")
    normalized, outcomes = normalize_records(args.repo_root, args.domain, payload, raw_sha256=digest, captured_at=captured, correction=correction, normalization_metadata=normalization_metadata)
    candidate_count = sum(1 for record in normalized if record.get("candidate_created") is True); invalid_count = outcomes.get("invalid", 0) + outcomes.get("archived_invalid", 0)
    result = {"valid": validation["valid"], "domain": args.domain, "input_sha256": digest, "raw_record_count": 1, "normalized_finding_count": len(normalized), "outcomes": outcomes, "candidate_count": candidate_count, "invalid_finding_count": invalid_count, "outcome": "archived_invalid" if invalid_count and not candidate_count else ("candidate_ready" if candidate_count else "needs_manual_review"), "correction_path": str(args.correction) if args.correction else "", "normalization_method": normalization_metadata.get("normalization_method"), "dry_run": args.operation in {"dry-run", "report"}}
    if args.domain == "care-line":
        result["care_line_findings"] = [_care_report(record) for record in normalized]
    if args.domain == "gaza":
        result["gaza_findings"] = [_gaza_report(record) for record in normalized]
    if args.domain == "ice":
        result["ice_findings"] = [_ice_report(record) for record in normalized]
    if args.operation in {"import", "normalize"}:
        base = archive_root(args.repo_root, args.domain); raw_path = base / "raw" / f"{digest}.json"; normalized_path = base / "normalized" / f"{digest}.json"; report_path = base / "reports" / f"{digest}.json"
        correction_digest = sha256_bytes(args.correction.read_bytes())[:16] if args.correction else ""
        if raw_path.exists() and correction_digest:
            base_normalized_path, base_report_path = normalized_path, report_path
            if args.domain in {"care-line", "gaza", "ice"} and base_normalized_path.exists():
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
            imported_at = datetime.now(timezone.utc).isoformat()
            if args.domain == "ice":
                for item in normalized:
                    item["captured_at"] = captured
                    item["imported_at"] = imported_at
                    item["last_normalized_at"] = None
                result["ice_findings"] = [_ice_report(item) for item in normalized]
                result["captured_at"] = captured
                result["imported_at"] = imported_at
                result["last_normalized_at"] = None
            record = {"schema_version": SCHEMA_VERSION, "domain": args.domain, "agent_name": payload.get("agent_name", "historical-agent") if isinstance(payload, dict) else "historical-agent", "agent_run_id": payload.get("agent_run_id", "") if isinstance(payload, dict) else "", "captured_at": captured, "original_run_at": payload.get("started_at", "") if isinstance(payload, dict) else "", "search_window": payload.get("search_window", {}) if isinstance(payload, dict) else {}, "source_format": validation["source_format"], "raw_text": raw.decode("utf-8", errors="replace"), "raw_bytes_base64": base64.b64encode(raw).decode("ascii"), "raw_sha256": digest, "source_chat_or_export_reference": "", "normalization_status": "pending_review", "imported_at": imported_at}
            record.update(normalization_metadata)
            normalized_envelope = {"schema_version": "historical_agent_normalized_v1", "domain": args.domain, "raw_sha256": digest, "normalization_method": normalization_metadata.get("normalization_method"), "private_text_provenance": normalization_metadata.get("private_text_provenance"), "agent_run_id": payload.get("agent_run_id", "") if isinstance(payload, dict) else "", "started_at": payload.get("started_at", "") if isinstance(payload, dict) else "", "completed_at": payload.get("completed_at", "") if isinstance(payload, dict) else "", "search_window": payload.get("search_window", {}) if isinstance(payload, dict) else {}, "findings": normalized}
            if args.domain == "ice":
                normalized_envelope.update({"captured_at": captured, "imported_at": imported_at, "last_normalized_at": None})
            atomic_json(raw_path, record); atomic_json(normalized_path, normalized_envelope); result["status"] = "imported"; atomic_json(report_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0 if validation["valid"] else 1

if __name__ == "__main__": raise SystemExit(main())
