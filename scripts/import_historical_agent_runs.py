from __future__ import annotations
import argparse, base64, hashlib, io, json, re
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.historical_agent_archive import DOMAINS, SCHEMA_VERSION, HistoricalEnvelopeError, _care_report, _gaza_report, _ice_report, archive_root, atomic_json, build_inventory, canonical_json, care_line_match_targets, food_line_match_targets, gaza_match_targets, normalize_records, parse_historical_input, sha256_bytes, validate_input
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


GAZA_EDITORIAL_DECISIONS = {
    "confirmed": "substantively_reviewed",
    "corrected": "substantively_reviewed",
    "deferred": "pending_review",
    "rejected": "excluded",
    "duplicate": "excluded",
}

GAZA_TAXONOMY_ALLOWLISTS = {
    "category": {
        "aid_access",
        "ceasefire_diplomacy",
        "civilian_harm",
        "detention_disappearance",
        "displacement",
        "healthcare_access",
        "humanitarian_access",
        "humanitarian_operations",
        "infrastructure_damage",
        "legal_diplomatic",
    },
    "event_type": {
        "aid_access_change",
        "casualty_event",
        "ceasefire_or_diplomatic_development",
        "detention_or_disappearance_analysis",
        "displacement_event",
        "healthcare_access_deterioration",
        "humanitarian_worker_injury",
        "infrastructure_damage",
        "legal_filing",
    },
    "gaza_role": {
        "civilian_harm",
        "core_gaza",
        "detention_disappearance",
        "healthcare_access",
        "humanitarian_access",
        "humanitarian_operations_and_safety",
        "legal_diplomatic",
    },
    "source_role": {
        "official_government_statement",
        "official_humanitarian_report",
        "primary_source",
        "primary_un_humanitarian_report",
        "reported_public_source",
        "secondary_news_report",
    },
}

GAZA_ATTRIBUTION_MODES = {
    "direct_official_record",
    "official_claim",
    "organizational_estimate",
    "allegation",
    "single_source_report",
    "multi_source_disputed_quantity",
}


def _clean_review_url(value: object) -> str:
    return str(value or "").strip().lower().split("?")[0].rstrip("/")


def _require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _require_bounded_string(value: object, label: str, *, maximum: int = 4000) -> str:
    text = _require_nonempty_string(value, label)
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds the {maximum}-character limit")
    return text


def _parse_review_date(value: object, label: str) -> str:
    text = _require_nonempty_string(value, label)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{label} must be an ISO calendar date")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid calendar date") from exc
    return text


def _review_candidate_identifier(review: dict) -> tuple[str, str]:
    identifiers = [
        ("finding_id", review.get("normalized_finding_id")),
        ("audit_candidate_id", review.get("audit_candidate_id")),
    ]
    present = [
        (field, str(value).strip())
        for field, value in identifiers
        if isinstance(value, str) and value.strip()
    ]
    if len(present) != 1:
        raise ValueError(
            "Gaza editorial review must declare exactly one of "
            "normalized_finding_id or audit_candidate_id"
        )
    return present[0]


def _gaza_candidate_fingerprint(finding: dict) -> str:
    excluded = {
        "audit_candidate_id",
        "candidate_created",
        "deduplication_outcome",
        "finding_id",
        "historical_outcome",
        "matched_edition_date",
        "matched_source_or_cluster_id",
        "publication_approval",
        "publication_eligible",
        "queue_action",
        "review_status",
    }
    immutable_claim = {
        key: value
        for key, value in finding.items()
        if key not in excluded
    }
    return "sha256:" + hashlib.sha256(
        canonical_json(immutable_claim).encode("utf-8")
    ).hexdigest()


def _matching_report_candidate(report: dict, finding: dict, id_field: str, identifier: str) -> dict:
    report_findings = report.get("gaza_findings")
    if not isinstance(report_findings, list) or not report_findings:
        raise ValueError("Gaza import report contains no gaza_findings")
    explicit_matches = [
        row for row in report_findings
        if isinstance(row, dict) and str(row.get(id_field) or "") == identifier
    ]
    if len(explicit_matches) > 1:
        raise ValueError("Gaza candidate identifier is duplicated in the import report")
    if explicit_matches:
        return explicit_matches[0]

    # Older import reports predate audit_candidate_id.  Their corresponding row is
    # still bound by exact run, URL, title, and source-date lineage.
    finding_url = _clean_review_url(
        finding.get("canonical_source_url") or finding.get("source_url")
    )
    finding_title = str(finding.get("title") or "").strip()
    finding_date = str(
        finding.get("source_published_at") or finding.get("source_date") or ""
    )[:10]
    finding_run = str(finding.get("agent_run_id") or "")
    lineage_matches = []
    for row in report_findings:
        if not isinstance(row, dict):
            continue
        row_url = _clean_review_url(
            row.get("canonical_source_url") or row.get("source_url")
        )
        row_title = str(row.get("title") or "").strip()
        row_date = str(
            row.get("source_published_at") or row.get("source_date") or ""
        )[:10]
        row_run = str(row.get("agent_run_id") or "")
        if (
            finding_url
            and row_url == finding_url
            and row_title == finding_title
            and row_date == finding_date
            and row_run == finding_run
        ):
            lineage_matches.append(row)
    if len(lineage_matches) != 1:
        raise ValueError(
            "Gaza candidate must resolve exactly once in the normalized and import "
            "report lineage"
        )
    return lineage_matches[0]


def _validate_gaza_review_dates(review: dict, finding: dict) -> dict:
    assessment = review.get("date_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("Gaza editorial review date_assessment is missing")
    publication = _parse_review_date(
        finding.get("source_published_at") or finding.get("source_date"),
        "candidate source publication date",
    )
    if assessment.get("source_published_at") != publication:
        raise ValueError("Gaza review source publication date does not match candidate")

    event_date = str(finding.get("event_date") or "").strip()
    period_start = str(finding.get("event_period_start") or "").strip()
    period_end = str(finding.get("event_period_end") or "").strip()
    if event_date and (period_start or period_end):
        raise ValueError("Gaza candidate cannot declare both event_date and event period")
    if event_date:
        event_date = _parse_review_date(event_date, "candidate event_date")
        if assessment.get("event_date") != event_date:
            raise ValueError("Gaza review event_date does not match candidate")
        if assessment.get("event_period") not in (None, {}):
            raise ValueError("Gaza point-date candidate cannot acquire an event period")
        event_boundary = event_date
        result = {"event_date": event_date, "source_published_at": publication}
    else:
        if not period_start and not period_end and (
            finding.get("event_date_status") == "unknown"
            or finding.get("event_onset_unknown") is True
        ):
            if assessment.get("event_date_status") != "unknown":
                raise ValueError("Gaza review must retain the candidate's unknown event date")
            explanation = _require_bounded_string(
                assessment.get("unknown_event_date_explanation"),
                "unknown event date explanation",
            )
            result = {
                "event_date_status": "unknown",
                "unknown_event_date_explanation": explanation,
                "source_published_at": publication,
            }
            event_boundary = ""
        elif not period_start or not period_end:
            raise ValueError("Gaza candidate must contain an event date or bounded event period")
        else:
            period_start = _parse_review_date(period_start, "candidate event_period_start")
            period_end = _parse_review_date(period_end, "candidate event_period_end")
            if period_start > period_end:
                raise ValueError("Gaza candidate event period start must not follow its end")
            if assessment.get("event_date") not in (None, ""):
                raise ValueError("Gaza period candidate cannot acquire a point event_date")
            if assessment.get("event_period") != {
                "start": period_start,
                "end": period_end,
            }:
                raise ValueError("Gaza review event period does not match candidate")
            event_boundary = period_start
            result = {
                "event_period_start": period_start,
                "event_period_end": period_end,
                "source_published_at": publication,
            }

    if event_boundary and publication < event_boundary:
        if assessment.get("publication_precedes_event_supported") is not True:
            raise ValueError("Gaza publication date precedes the event boundary")
        _require_nonempty_string(
            assessment.get("publication_precedes_event_explanation"),
            "publication-before-event explanation",
        )
    for candidate_key, review_key in (
        ("discovered_at", "discovered_at"),
        ("imported_at", "imported_at"),
    ):
        candidate_value = str(finding.get(candidate_key) or "").strip()
        if candidate_value and assessment.get(review_key) != candidate_value:
            raise ValueError(f"Gaza review {review_key} does not match candidate")
    return result


def _validate_gaza_review_taxonomy(review: dict, finding: dict) -> dict:
    taxonomy = review.get("taxonomy_review")
    if not isinstance(taxonomy, dict):
        raise ValueError("Gaza editorial review taxonomy_review is missing")
    unexpected = set(taxonomy) - {"domain", *GAZA_TAXONOMY_ALLOWLISTS}
    if unexpected:
        raise ValueError(
            "Gaza review taxonomy contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    validated: dict[str, str] = {}
    for field, allowlist in GAZA_TAXONOMY_ALLOWLISTS.items():
        candidate_value = str(finding.get(field) or "").strip()
        review_value = str(taxonomy.get(field) or "").strip()
        if candidate_value:
            if candidate_value not in allowlist:
                raise ValueError(f"Gaza candidate {field} is outside the allowlist")
            if review_value != candidate_value:
                raise ValueError(f"Gaza review {field} does not match candidate")
            validated[field] = candidate_value
        elif review_value:
            raise ValueError(f"Gaza review cannot substitute a missing {field}")
    if not validated.get("category") and not validated.get("event_type"):
        raise ValueError("Gaza candidate must have an allowed category or event_type")
    if taxonomy.get("domain") not in (None, "gaza"):
        raise ValueError("Gaza review taxonomy domain must remain gaza")
    return validated


def _validate_gaza_evidence(review: dict, finding: dict) -> list[dict]:
    references = review.get("evidence_references")
    if not isinstance(references, list) or not references:
        raise ValueError("Gaza editorial review requires evidence references")
    candidate_url = _clean_review_url(
        finding.get("canonical_source_url") or finding.get("source_url")
    )
    if not candidate_url:
        raise ValueError("Gaza candidate source URL is missing")
    validated: list[dict] = []
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("Gaza evidence reference must be an object")
        role = reference.get("role")
        if role not in {"principal", "corroborating"}:
            raise ValueError("Gaza evidence role must be principal or corroborating")
        url = _require_nonempty_string(reference.get("url"), "evidence URL")
        if not re.match(r"^https://[^\s]+$", url, flags=re.I):
            raise ValueError("Gaza evidence URL must be HTTPS")
        passage = _require_nonempty_string(
            reference.get("supporting_passage"), "evidence supporting passage"
        )
        validated.append({"role": role, "url": url, "supporting_passage": passage})
    principal_urls = {
        _clean_review_url(item["url"])
        for item in validated
        if item["role"] == "principal"
    }
    if candidate_url not in principal_urls:
        raise ValueError("Gaza principal evidence source does not match candidate")
    return validated


def _validate_gaza_attribution(review: dict) -> dict:
    attribution = review.get("attribution_assessment")
    if not isinstance(attribution, dict):
        raise ValueError("Gaza editorial review attribution_assessment is missing")
    mode = attribution.get("mode")
    if mode not in GAZA_ATTRIBUTION_MODES:
        raise ValueError("Gaza attribution mode is not allowed")
    _require_nonempty_string(attribution.get("attributed_to"), "attribution authority")
    _require_nonempty_string(
        attribution.get("safe_future_wording"), "safe future wording"
    )
    if attribution.get("attribution_preserved") is not True:
        raise ValueError("Gaza attribution must remain explicit")
    if attribution.get("unsupported_certainty_escalation") is not False:
        raise ValueError("Gaza review must reject unsupported certainty escalation")
    if attribution.get("uncertainty_preserved") is not True:
        raise ValueError("Gaza attribution must preserve uncertainty")
    if mode == "organizational_estimate":
        if attribution.get("estimate_not_independently_verified") is not True:
            raise ValueError("Gaza organizational estimate must retain verification limits")
        if attribution.get("methodology_preserved") is not True:
            raise ValueError("Gaza organizational estimate must preserve methodology")
    if mode == "allegation" and attribution.get("allegation_not_adjudicated") is not True:
        raise ValueError("Gaza allegation must remain distinct from an adjudicated fact")
    if mode == "single_source_report" and attribution.get(
        "single_source_uncertainty_preserved"
    ) is not True:
        raise ValueError("Gaza single-source report must retain source uncertainty")
    if mode == "multi_source_disputed_quantity":
        values = attribution.get("disputed_values")
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError("Gaza disputed quantity requires at least two values")
        normalized_values = {
            canonical_json(item.get("value"))
            for item in values
            if isinstance(item, dict)
            and isinstance(item.get("source_url"), str)
            and re.match(r"^https://[^\s]+$", item["source_url"], flags=re.I)
            and "value" in item
        }
        if len(normalized_values) < 2 or len(normalized_values) != len(values):
            raise ValueError("Gaza disputed values must be distinct and source-backed")
        if attribution.get("dispute_unresolved") is not True:
            raise ValueError("Gaza disputed quantity must remain unresolved")
    return attribution


def _validate_gaza_prior_reference(repo_root: Path, reference: dict, label: str) -> None:
    reference_id = _require_nonempty_string(reference.get("id"), f"{label} id")
    if reference.get("type") == "historical_candidate":
        normalized_root = archive_root(repo_root, "gaza") / "normalized"
        matches = []
        for path in normalized_root.rglob("*.json") if normalized_root.exists() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for candidate in record.get("findings", []):
                if isinstance(candidate, dict) and reference_id in {
                    str(candidate.get("finding_id") or ""),
                    str(candidate.get("audit_candidate_id") or ""),
                }:
                    matches.append((path, candidate))
        if len(matches) != 1:
            raise ValueError(f"{label} must resolve exactly once as a historical candidate")
        return
    targets = gaza_match_targets(repo_root)
    matches = list(targets["clusters_by_id"].get(reference_id, []))
    matches.extend(
        source
        for sources in targets["sources_by_url"].values()
        for source in sources
        if reference_id in source.get("story_ids", [])
    )
    matches.extend(
        edition
        for edition in targets["editions"].values()
        if edition.get("edition_id") == reference_id
    )
    if not matches:
        raise ValueError(f"{label} does not resolve as a published story")


def _validate_gaza_decision_details(
    review: dict,
    decision: str,
    evidence: list[dict],
    repo_root: Path,
) -> dict:
    details: dict[str, object] = {}
    if decision in {"confirmed", "corrected"}:
        duplicate_check = review.get("duplicate_and_authoritative_match_check")
        if (
            not isinstance(duplicate_check, dict)
            or duplicate_check.get("candidate_remains_distinct") is not True
            or duplicate_check.get("existing_edition_match") is not None
            or duplicate_check.get("existing_source_match") is not None
            or duplicate_check.get("existing_story_cluster_match") is not None
            or duplicate_check.get("existing_historical_match") is not None
        ):
            raise ValueError(
                "confirmed or corrected Gaza review requires a clear bounded "
                "duplicate and authoritative-match check"
            )
        details["duplicate_and_authoritative_match_check"] = duplicate_check
    if decision == "corrected":
        lineage = review.get("correction_lineage")
        if not isinstance(lineage, dict):
            raise ValueError("corrected decision requires correction_lineage")
        prior = lineage.get("prior_reference")
        if not isinstance(prior, dict) or prior.get("type") not in {
            "historical_candidate",
            "published_story",
        }:
            raise ValueError("correction lineage requires a prior candidate or story reference")
        if str(prior.get("id") or "") in {
            str(review.get("normalized_finding_id") or ""),
            str(review.get("audit_candidate_id") or ""),
        }:
            raise ValueError("correction prior reference cannot be the selected candidate")
        _validate_gaza_prior_reference(repo_root, prior, "correction prior reference")
        if prior.get("type") == "published_story":
            _parse_review_date(prior.get("edition_date"), "prior story edition_date")
        prior_fp = lineage.get("prior_event_fingerprint")
        corrected_fp = lineage.get("corrected_event_fingerprint")
        if not isinstance(prior_fp, dict) or not isinstance(corrected_fp, dict):
            raise ValueError("correction lineage requires prior and corrected fingerprints")
        prior_identity = _require_nonempty_string(
            prior_fp.get("event_identity"), "prior event identity"
        )
        corrected_identity = _require_nonempty_string(
            corrected_fp.get("event_identity"), "corrected event identity"
        )
        if prior_identity != corrected_identity:
            raise ValueError("correction fingerprints refer to unrelated events")
        old_fp = _require_nonempty_string(prior_fp.get("fingerprint"), "prior fingerprint")
        new_fp = _require_nonempty_string(
            corrected_fp.get("fingerprint"), "corrected fingerprint"
        )
        if old_fp == new_fp:
            raise ValueError("correction fingerprints must differ")
        if new_fp != review.get("candidate_event_fingerprint"):
            raise ValueError("corrected fingerprint must match the selected candidate")
        _require_nonempty_string(lineage.get("field_or_claim"), "corrected field or claim")
        if "previous_value" not in lineage or "corrected_value" not in lineage:
            raise ValueError("correction lineage requires previous and corrected values")
        if canonical_json(lineage["previous_value"]) == canonical_json(
            lineage["corrected_value"]
        ):
            raise ValueError("correction previous and corrected values must differ")
        indexes = lineage.get("evidence_reference_indexes")
        if (
            not isinstance(indexes, list)
            or not indexes
            or not all(isinstance(value, int) and 0 <= value < len(evidence) for value in indexes)
        ):
            raise ValueError("correction lineage requires traceable evidence indexes")
        if lineage.get("corroboration_required") is True and len(set(indexes)) < 2:
            raise ValueError("correction requiring corroboration needs multiple references")
        _require_bounded_string(
            lineage.get("materiality_explanation"), "correction materiality explanation"
        )
        uncertainty = lineage.get("remaining_uncertainty")
        if not isinstance(uncertainty, dict) or not isinstance(
            uncertainty.get("persists"), bool
        ):
            raise ValueError("correction lineage must state remaining uncertainty")
        if uncertainty["persists"]:
            _require_bounded_string(
                uncertainty.get("description"), "remaining uncertainty description"
            )
        if lineage.get("prior_public_artifact_overwritten") is not False:
            raise ValueError("review cannot overwrite a prior public artifact")
        details["correction_lineage"] = lineage
    elif decision == "deferred":
        details["unresolved_requirement"] = _require_bounded_string(
            review.get("unresolved_requirement"), "deferred unresolved requirement"
        )
    elif decision == "rejected":
        details["rejection_basis"] = _require_bounded_string(
            review.get("rejection_basis"), "rejection basis"
        )
    elif decision == "duplicate":
        matched = review.get("matched_reference")
        if not isinstance(matched, dict) or matched.get("type") not in {
            "historical_candidate",
            "published_story",
        }:
            raise ValueError("duplicate decision requires a matched reference")
        if str(matched.get("id") or "") in {
            str(review.get("normalized_finding_id") or ""),
            str(review.get("audit_candidate_id") or ""),
        }:
            raise ValueError("duplicate reference cannot be the selected candidate")
        _validate_gaza_prior_reference(repo_root, matched, "duplicate reference")
        _require_nonempty_string(
            matched.get("event_fingerprint"), "duplicate event fingerprint"
        )
        details["matched_reference"] = matched
    return details


def _review_gaza_editorial_candidate(args: argparse.Namespace) -> int:
    if args.decision not in GAZA_EDITORIAL_DECISIONS:
        raise ValueError(
            "unsupported Gaza editorial decision; expected one of: "
            + ", ".join(sorted(GAZA_EDITORIAL_DECISIONS))
        )
    raw_sha = str(args.raw_sha or "")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_sha):
        raise ValueError("--raw-sha must be an exact lowercase SHA-256 digest")
    expected_review_sha = str(args.review_artifact_sha256 or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_review_sha):
        raise ValueError("--review-artifact-sha256 must be an exact lowercase SHA-256 digest")

    repo_root = args.repo_root.resolve()
    base = archive_root(repo_root, "gaza")
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    missing = [str(path) for path in (raw_path, normalized_path, report_path) if not path.is_file()]
    if missing:
        raise ValueError("historical review target is incomplete; missing: " + ", ".join(missing))

    review_path = args.review_artifact.resolve()
    reviews_root = (base / "reviews").resolve()
    try:
        relative_review = review_path.relative_to(reviews_root)
    except ValueError as exc:
        raise ValueError(f"review artifact must be under {reviews_root}") from exc
    if not relative_review.parts or relative_review.parts[0].lower() == "decisions":
        raise ValueError("review artifact must be independent of decision records")
    if not review_path.is_file():
        raise ValueError(f"review artifact does not exist: {review_path}")
    review_bytes = review_path.read_bytes()
    actual_review_sha = sha256_bytes(review_bytes)
    if actual_review_sha != expected_review_sha:
        raise ValueError("review artifact SHA-256 mismatch")

    raw_bytes = raw_path.read_bytes()
    normalized_bytes = normalized_path.read_bytes()
    report_bytes = report_path.read_bytes()
    raw_record = json.loads(raw_bytes.decode("utf-8"))
    normalized_record = json.loads(normalized_bytes.decode("utf-8"))
    report = json.loads(report_bytes.decode("utf-8"))
    review = json.loads(review_bytes.decode("utf-8"))
    for label, payload in (
        ("raw archive", raw_record),
        ("normalized record", normalized_record),
        ("import report", report),
        ("editorial review", review),
    ):
        if not isinstance(payload, dict) or payload.get("domain") != "gaza":
            raise ValueError(f"{label} must be a Gaza JSON object")
    if raw_record.get("raw_sha256") != raw_sha:
        raise ValueError("raw archive SHA-256 identity does not match")
    if normalized_record.get("raw_sha256") != raw_sha:
        raise ValueError("normalized record SHA-256 identity does not match")
    if report.get("input_sha256") != raw_sha:
        raise ValueError("import report SHA-256 identity does not match")
    if review.get("raw_sha256") != raw_sha:
        raise ValueError("editorial review SHA-256 identity does not match")
    encoded = raw_record.get("raw_bytes_base64")
    try:
        preserved_raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("historical raw archive bytes are malformed") from exc
    if sha256_bytes(preserved_raw) != raw_sha:
        raise ValueError("historical raw archive bytes do not match content address")
    normalized_sha = sha256_bytes(normalized_bytes)
    report_sha = sha256_bytes(report_bytes)
    if review.get("normalized_artifact_sha256") != normalized_sha:
        raise ValueError("normalized artifact digest does not match review lineage")
    if review.get("report_artifact_sha256") != report_sha:
        raise ValueError("report artifact digest does not match review lineage")
    if review.get("schema_version") != "gaza_historical_editorial_review_v2":
        raise ValueError("Gaza editorial review schema_version is invalid")
    if review.get("review_type") != "historical_editorial_review":
        raise ValueError("Gaza editorial review review_type is invalid")
    if review.get("decision") != args.decision:
        raise ValueError("review artifact decision does not match CLI decision")

    id_field, identifier = _review_candidate_identifier(review)
    findings = normalized_record.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("historical normalized record contains no findings")
    matches = [
        item for item in findings
        if isinstance(item, dict) and str(item.get(id_field) or "") == identifier
    ]
    if len(matches) != 1:
        raise ValueError("Gaza candidate identifier must resolve exactly once")
    finding = matches[0]
    if finding.get("domain") not in (None, "", "gaza"):
        raise ValueError("Gaza candidate domain does not match")
    report_candidate = _matching_report_candidate(report, finding, id_field, identifier)
    if report_candidate.get("domain") not in (None, "", "gaza"):
        raise ValueError("Gaza report candidate domain does not match")
    run_values = [
        str(value or "").strip()
        for value in (
            raw_record.get("agent_run_id"),
            normalized_record.get("agent_run_id"),
            finding.get("agent_run_id"),
            report_candidate.get("agent_run_id"),
            review.get("agent_run_id"),
        )
    ]
    if any(not value for value in run_values) or len(set(run_values)) != 1:
        raise ValueError("Gaza candidate agent_run_id lineage does not match")
    finding_url = _clean_review_url(
        finding.get("canonical_source_url") or finding.get("source_url")
    )
    report_url = _clean_review_url(
        report_candidate.get("canonical_source_url")
        or report_candidate.get("source_url")
    )
    finding_date = str(
        finding.get("source_published_at") or finding.get("source_date") or ""
    )[:10]
    report_date = str(
        report_candidate.get("source_published_at")
        or report_candidate.get("source_date")
        or ""
    )[:10]
    if (
        not finding_url
        or report_url != finding_url
        or report_date != finding_date
        or str(report_candidate.get("title") or "").strip()
        != str(finding.get("title") or "").strip()
    ):
        raise ValueError("Gaza candidate source lineage does not match import report")
    if finding.get("historical_outcome") != "new_historical_candidate":
        raise ValueError("Gaza editorial review requires a new_historical_candidate")
    if finding.get("review_status") != "pending_review":
        raise ValueError("Gaza candidate must remain pending_review before decision recording")
    if finding.get("publication_eligible") is not False or finding.get(
        "publication_approval"
    ) is not False:
        raise ValueError("Gaza candidate must remain nonpublishable and nonapproved")
    if finding.get("queue_action") not in (None, "", "none"):
        raise ValueError("Gaza candidate must not be queued")
    forbidden_active_states = {
        "approval": True,
        "approved": True,
        "publication_ready": True,
        "release_authorized": True,
        "release_ready": True,
        "queued": True,
        "publishing": True,
        "publishing_authorized": True,
        "published": True,
    }
    for key, forbidden in forbidden_active_states.items():
        if finding.get(key) == forbidden or review.get(key) == forbidden:
            raise ValueError(f"Gaza editorial review cannot set {key}")

    required_review_values = {
        "decision_reason": str,
        "current_review_status": "pending_review",
        "current_publication_eligible": False,
        "current_publication_approval": False,
        "current_queue_action": "none",
        "resulting_review_state": GAZA_EDITORIAL_DECISIONS[args.decision],
        "archive_mutation_authorized": False,
        "edition_authorized": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
    }
    for key, expected in required_review_values.items():
        if expected is str:
            _require_bounded_string(
                review.get(key), f"Gaza review {key}", maximum=2000
            )
        elif review.get(key) != expected:
            raise ValueError(f"Gaza review {key} must be exactly {expected!r}")

    evidence = _validate_gaza_evidence(review, finding)
    candidate_fingerprint = _gaza_candidate_fingerprint(finding)
    if review.get("candidate_event_fingerprint") != candidate_fingerprint:
        raise ValueError("Gaza candidate event fingerprint does not match")
    dates = _validate_gaza_review_dates(review, finding)
    taxonomy = _validate_gaza_review_taxonomy(review, finding)
    attribution = _validate_gaza_attribution(review)
    decision_details = _validate_gaza_decision_details(
        review, args.decision, evidence, repo_root
    )

    identifier_key = f"{id_field}:{identifier}"
    identity_digest = hashlib.sha256(identifier_key.encode("utf-8")).hexdigest()[:20]
    decision_path = base / "reviews" / "decisions" / (
        f"{raw_sha[:24]}-{identity_digest}.json"
    )
    try:
        review_display = review_path.relative_to(repo_root).as_posix()
    except ValueError:
        review_display = review_path.as_posix()
    immutable = {
        "schema_version": "gaza_historical_editorial_decision_v2",
        "domain": "gaza",
        "raw_sha256": raw_sha,
        "raw_archive_artifact_path": raw_path.relative_to(repo_root).as_posix(),
        "raw_archive_artifact_sha256": sha256_bytes(raw_bytes),
        id_field: identifier,
        "agent_run_id": review["agent_run_id"],
        "normalized_artifact_path": normalized_path.relative_to(repo_root).as_posix(),
        "normalized_artifact_sha256": normalized_sha,
        "report_artifact_path": report_path.relative_to(repo_root).as_posix(),
        "report_artifact_sha256": report_sha,
        "review_artifact_path": review_display,
        "review_artifact_sha256": actual_review_sha,
        "operator": args.operator,
        "decision": args.decision,
        "decision_reason": review["decision_reason"],
        "candidate_event_fingerprint": candidate_fingerprint,
        "previous_review_status": "pending_review",
        "resulting_review_state": GAZA_EDITORIAL_DECISIONS[args.decision],
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "queue_action": "none",
        "archive_content_change_authorized": False,
        "edition_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
        "date_assessment": dates,
        "taxonomy_review": taxonomy,
        "attribution_assessment": attribution,
        "evidence_references": evidence,
        **decision_details,
    }
    if decision_path.exists():
        existing = json.loads(decision_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("existing Gaza editorial decision must be a JSON object")
        for key, expected in immutable.items():
            if existing.get(key) != expected:
                raise ValueError(f"existing Gaza editorial decision conflicts at {key}")
        result = {
            "status": "idempotent_noop",
            "domain": "gaza",
            id_field: identifier,
            "decision": args.decision,
            "resulting_review_state": GAZA_EDITORIAL_DECISIONS[args.decision],
            "decision_audit_path": str(decision_path),
            "publication_authorized": False,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.dry_run:
        result = {
            "status": "dry_run_validated",
            "domain": "gaza",
            id_field: identifier,
            "decision": args.decision,
            "resulting_review_state": GAZA_EDITORIAL_DECISIONS[args.decision],
            "decision_audit_path": str(decision_path),
            "publication_authorized": False,
            "persistent_mutation": False,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    audit = {**immutable, "decided_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(decision_path, audit)
    result = {
        "status": "decision_recorded",
        "domain": "gaza",
        id_field: identifier,
        "decision": args.decision,
        "previous_review_status": "pending_review",
        "resulting_review_state": GAZA_EDITORIAL_DECISIONS[args.decision],
        "decision_audit_path": str(decision_path),
        "publication_authorized": False,
        "normalized_artifact_unchanged": sha256_bytes(normalized_path.read_bytes()) == normalized_sha,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _review_historical_candidate(args: argparse.Namespace) -> int:
    if args.domain == "gaza" and args.decision in GAZA_EDITORIAL_DECISIONS:
        return _review_gaza_editorial_candidate(args)
    supported_decisions = {
        ("ice", "substantively-valid"): {
            "audit_decision": "accept_substantively_valid_historical_candidate",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "new_status": "substantively_reviewed",
            "queue_action": "none",
        },
        ("care-line", "substantively-valid"): {
            "audit_decision": "accept_substantively_valid_historical_candidate",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "new_status": "substantively_reviewed",
            "queue_action": "historical_review_candidate",
        },
        ("gaza", "substantively-valid"): {
            "audit_decision": "accept_substantively_valid_historical_candidate",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "new_status": "substantively_reviewed",
            "queue_action": "none",
            "finding_queue_actions": (None, "", "none"),
        },
        ("food-line", "substantively-valid"): {
            "audit_decision": "accept_substantively_valid_historical_candidate",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "new_status": "substantively_reviewed",
            "queue_action": "none",
            "finding_queue_actions": (None, "", "none"),
            "review_queue_action_required": False,
            "allow_legacy_publication_flags": True,
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
    finding_id_fields = (
        ("finding_id", "agent_finding_id", "candidate_id")
        if args.domain == "food-line"
        else ("finding_id",)
    )
    matching_indexes = [
        index
        for index, finding in enumerate(findings)
        if isinstance(finding, dict)
        and finding_id
        in {
            str(finding.get(field) or "")
            for field in finding_id_fields
            if finding.get(field)
        }
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
        "current_publication_eligible": False,
        "current_publication_approval": False,
    }
    if decision_spec.get("review_queue_action_required", True):
        required_review_values["current_queue_action"] = decision_spec["queue_action"]
    for key, expected in required_review_values.items():
        if review.get(key) != expected:
            raise ValueError(
                f"substantive review {key} must be exactly {expected!r}"
            )
    historical_outcome = finding.get("historical_outcome")
    if args.domain == "food-line" and not historical_outcome:
        historical_outcome = finding.get("deduplication_outcome")
    if historical_outcome != "new_historical_candidate":
        raise ValueError(
            "only a new_historical_candidate may receive this substantive decision"
        )
    allowed_finding_queue_actions = decision_spec.get(
        "finding_queue_actions",
        (decision_spec["queue_action"],),
    )
    if finding.get("queue_action") not in allowed_finding_queue_actions:
        raise ValueError(
            "historical candidate queue_action must be one of "
            f"{allowed_finding_queue_actions!r}"
        )
    if decision_spec.get("allow_legacy_publication_flags"):
        if finding.get("publication_eligible") not in (None, False):
            raise ValueError("historical candidate publication_eligible must be false")
        if finding.get("publication_approval") not in (None, False):
            raise ValueError("historical candidate publication_approval must be false")
    else:
        if finding.get("publication_eligible") is not False:
            raise ValueError("historical candidate publication_eligible must be false")
        if finding.get("publication_approval") is not False:
            raise ValueError("historical candidate publication_approval must be false")

    domain_audit_values: dict[str, object]
    if args.domain == "ice":
        severity_review = review.get("severity_review")
        if not isinstance(severity_review, dict) or severity_review.get("current_severity") != "high":
            raise ValueError("substantive review must preserve severity high")
        if finding.get("severity") != "high":
            raise ValueError("ICE substantive-valid decision requires severity high")
        domain_audit_values = {
            "severity": "high",
        }
    elif args.domain == "care-line":
        if review.get("schema_version") != "care_line_substantive_historical_review_v1":
            raise ValueError(
                "Care Line substantive review schema_version must be exactly "
                "'care_line_substantive_historical_review_v1'"
            )
        if review.get("review_type") != "substantive_historical_review":
            raise ValueError(
                "Care Line substantive review review_type must be exactly "
                "'substantive_historical_review'"
            )
        if review.get("queue_authorized") is not False:
            raise ValueError("Care Line substantive review queue_authorized must be false")
        duplicate_check = review.get("duplicate_and_live_record_check")
        if (
            not isinstance(duplicate_check, dict)
            or duplicate_check.get("historical_candidate_remains_distinct") is not True
            or duplicate_check.get("existing_published_event") is not None
            or duplicate_check.get("existing_reviewed_live_candidate") is not None
            or duplicate_check.get("live_reviewed_event_queue_entry") is not None
        ):
            raise ValueError(
                "Care Line substantive review must preserve a distinct private candidate"
            )
        materiality = review.get("materiality_assessment")
        materiality_value = (
            materiality.get("assessment") if isinstance(materiality, dict) else None
        )
        if materiality_value not in {
            "high_access_impact",
            "moderate_access_impact",
            "limited_access_impact",
            "access_impact_unclear",
            "context_only",
        }:
            raise ValueError(
                "Care Line substantive review materiality_assessment is invalid"
            )
        taxonomy = review.get("taxonomy_review")
        if not isinstance(taxonomy, dict):
            raise ValueError("Care Line substantive review taxonomy_review is missing")
        event_type_review = taxonomy.get("event_type")
        service_line_review = taxonomy.get("service_line")
        event_status_review = taxonomy.get("event_status")
        effective_date_review = taxonomy.get("effective_date")
        if not all(
            isinstance(value, dict)
            for value in (
                event_type_review,
                service_line_review,
                event_status_review,
                effective_date_review,
            )
        ):
            raise ValueError(
                "Care Line substantive review taxonomy fields are incomplete"
            )
        if event_type_review.get("current_value") != finding.get("event_type"):
            raise ValueError(
                "Care Line substantive review event_type does not match normalized finding"
            )
        if effective_date_review.get("value") != finding.get("effective_date"):
            raise ValueError(
                "Care Line substantive review effective_date does not match normalized finding"
            )
        service_line = service_line_review.get("value")
        event_status = event_status_review.get("value")
        if not isinstance(service_line, str) or not service_line:
            raise ValueError("Care Line substantive review service_line is missing")
        if event_status != "scheduled":
            raise ValueError(
                "Care Line substantive review event_status must be exactly 'scheduled'"
            )
        editorial_restrictions = review.get("editorial_restrictions")
        if (
            not isinstance(editorial_restrictions, list)
            or not editorial_restrictions
            or not all(
                isinstance(item, str) and item.strip()
                for item in editorial_restrictions
            )
        ):
            raise ValueError(
                "Care Line substantive review editorial_restrictions are missing"
            )

        care_targets = care_line_match_targets(repo_root)
        event_id = str(finding.get("event_id") or finding.get("matched_event_id") or "")
        if (
            event_id
            and (
                event_id in care_targets["published_events"]
                or event_id in care_targets["reviewed_events"]
                or event_id in care_targets["queue"]
            )
        ):
            raise ValueError(
                "Care Line historical candidate now matches a live or published event"
            )
        source_url = str(
            finding.get("canonical_source_url") or finding.get("source_url") or ""
        ).strip().lower().split("?")[0].rstrip("/")
        live_source_matches = [
            item
            for item in care_targets["sources"].get(source_url, [])
            if not str(item.get("path") or "").replace("\\", "/").startswith(
                "data/agent-history/care-line/"
            )
        ]
        if live_source_matches:
            raise ValueError(
                "Care Line historical candidate now matches a live source record"
            )
        domain_audit_values = {
            "effective_date": finding.get("effective_date"),
            "editorial_restrictions": list(editorial_restrictions),
            "event_status": event_status,
            "event_type": finding.get("event_type"),
            "materiality_assessment": materiality_value,
            "service_line": service_line,
        }
    elif args.domain == "food-line":
        if review.get("schema_version") != "food_line_substantive_historical_review_v1":
            raise ValueError(
                "Food Line substantive review schema_version must be exactly "
                "'food_line_substantive_historical_review_v1'"
            )
        if review.get("review_type") != "substantive_historical_review":
            raise ValueError(
                "Food Line substantive review review_type must be exactly "
                "'substantive_historical_review'"
            )
        if review.get("edition_authorized") is not False:
            raise ValueError("Food Line substantive review edition_authorized must be false")

        finding_identities = {
            str(finding.get(field) or "")
            for field in finding_id_fields
            if finding.get(field)
        }
        if finding_identities != {finding_id}:
            raise ValueError(
                "Food Line historical candidate finding identity fields do not match"
            )

        provenance = review.get("provenance_verification")
        if not isinstance(provenance, dict):
            raise ValueError("Food Line substantive review provenance_verification is missing")
        review_run_id = str(
            review.get("agent_run_id") or provenance.get("agent_run_id") or ""
        )
        run_ids = {
            str(value)
            for value in (
                raw_record.get("agent_run_id"),
                normalized_record.get("agent_run_id"),
                finding.get("agent_run_id"),
                review_run_id,
            )
            if value
        }
        if len(run_ids) != 1 or not review_run_id:
            raise ValueError("Food Line substantive review agent_run_id does not match")
        if provenance.get("original_historical_outcome") != historical_outcome:
            raise ValueError(
                "Food Line substantive review historical outcome does not match"
            )

        taxonomy = review.get("taxonomy_review")
        if not isinstance(taxonomy, dict):
            raise ValueError("Food Line substantive review taxonomy_review is missing")
        pressure_signal_review = taxonomy.get("pressure_signal")
        pressure_type_review = taxonomy.get("pressure_type")
        location_scope_review = taxonomy.get("location_scope")
        if not all(
            isinstance(value, dict)
            for value in (
                pressure_signal_review,
                pressure_type_review,
                location_scope_review,
            )
        ):
            raise ValueError("Food Line substantive review taxonomy fields are incomplete")
        if finding.get("pressure_signal") is not True or pressure_signal_review.get(
            "current_value"
        ) is not True:
            raise ValueError("Food Line substantive review pressure_signal must be true")
        pressure_type = str(finding.get("pressure_type") or "")
        if not pressure_type or pressure_type_review.get("current_value") != pressure_type:
            raise ValueError("Food Line substantive review pressure_type does not match")
        location_name = str(finding.get("location_name") or "").strip()
        location_scope = str(finding.get("location_scope") or "").strip()
        if (
            not location_name
            or not location_scope
            or location_scope_review.get("current_value") != location_scope
        ):
            raise ValueError("Food Line substantive review location identity does not match")
        development = review.get("development_assessment")
        review_geography = (
            str(development.get("geographic_scope") or "")
            if isinstance(development, dict)
            else ""
        )
        if location_name.casefold() not in review_geography.casefold():
            raise ValueError("Food Line substantive review geographic scope does not match")

        materiality = review.get("materiality_assessment")
        materiality_value = (
            materiality.get("assessment") if isinstance(materiality, dict) else None
        )
        if materiality_value not in {
            "high_food_access_impact",
            "moderate_food_access_impact",
            "limited_food_access_impact",
            "food_access_impact_unclear",
        }:
            raise ValueError(
                "Food Line substantive review materiality_assessment is invalid"
            )

        duplicate_check = review.get("duplicate_and_public_record_check")
        if not isinstance(duplicate_check, dict) or duplicate_check.get(
            "candidate_remains_distinct"
        ) is not True:
            raise ValueError(
                "Food Line substantive review must preserve a distinct private candidate"
            )
        for field in (
            "canonical_source_url_match",
            "edition_match",
            "historical_duplicate",
            "normalized_event_fingerprint_match",
            "prior_intake_match",
            "public_claim_or_source_ledger_match",
            "source_match",
        ):
            if duplicate_check.get(field) is not None:
                raise ValueError(
                    "Food Line substantive review duplicate check is not clear at "
                    f"{field}"
                )

        editorial_restrictions = review.get("editorial_restrictions")
        if (
            not isinstance(editorial_restrictions, list)
            or not editorial_restrictions
            or not all(
                isinstance(item, str) and item.strip()
                for item in editorial_restrictions
            )
        ):
            raise ValueError(
                "Food Line substantive review editorial_restrictions are missing"
            )

        targets = food_line_match_targets(repo_root)
        source_url = str(
            finding.get("canonical_url")
            or finding.get("canonical_source_url")
            or finding.get("source_url")
            or finding.get("url")
            or ""
        ).strip().lower().split("?")[0].rstrip("/")
        if not source_url:
            raise ValueError("Food Line historical candidate canonical source URL is missing")
        public_or_intake_matches = {
            category: targets[category].get(source_url, [])
            for category in ("editions", "intake", "inbox", "source_ledgers")
            if targets[category].get(source_url)
        }
        if public_or_intake_matches:
            raise ValueError(
                "Food Line historical candidate now matches an exact public, intake, "
                "inbox, or source-ledger record"
            )

        current_relative = str(normalized_path.relative_to(repo_root))
        duplicate_key = str(finding.get("agent_duplicate_key") or "")
        source_published_date = str(
            finding.get("source_published_date")
            or finding.get("source_published_at")
            or finding.get("published_at")
            or ""
        )[:10]
        historical_matches = []
        for candidate in targets["historical"]:
            if candidate.get("path") == current_relative:
                continue
            same_finding = bool(finding_id) and candidate.get("finding_id") == finding_id
            same_duplicate_key = bool(duplicate_key) and candidate.get(
                "agent_duplicate_key"
            ) == duplicate_key
            same_source_event = (
                bool(source_url)
                and candidate.get("source_url") == source_url
                and candidate.get("source_published_date") == source_published_date
            )
            if same_finding or same_duplicate_key or same_source_event:
                historical_matches.append(candidate)
        if historical_matches:
            raise ValueError(
                "Food Line historical candidate now matches a prior historical record"
            )

        principal_source = review.get("source_assessment", {}).get(
            "principal_source", {}
        )
        review_source_url = str(
            principal_source.get("url") if isinstance(principal_source, dict) else ""
        ).strip().lower().split("?")[0].rstrip("/")
        if review_source_url != source_url:
            raise ValueError("Food Line substantive review source URL does not match")

        domain_audit_values = {
            "agent_run_id": review_run_id,
            "editorial_restrictions": list(editorial_restrictions),
            "edition_authorized": False,
            "intake_authorized": False,
            "location_name": location_name,
            "location_scope": location_scope,
            "map_authorized": False,
            "materiality_assessment": materiality_value,
            "pressure_type": pressure_type,
        }
    elif args.domain == "gaza":
        if review.get("schema_version") != "gaza_substantive_historical_review_v1":
            raise ValueError(
                "Gaza substantive review schema_version must be exactly "
                "'gaza_substantive_historical_review_v1'"
            )
        if review.get("review_type") != "substantive_historical_review":
            raise ValueError(
                "Gaza substantive review review_type must be exactly "
                "'substantive_historical_review'"
            )
        if review.get("edition_authorized") is not False:
            raise ValueError("Gaza substantive review edition_authorized must be false")
        if review.get("current_historical_outcome") != finding.get(
            "historical_outcome"
        ):
            raise ValueError(
                "Gaza substantive review historical outcome does not match "
                "normalized finding"
            )

        expected_finding_values = {
            "event_type": "humanitarian_worker_injury",
            "gaza_role": "humanitarian_operations_and_safety",
            "source_role": "primary_un_humanitarian_report",
            "evidence_level": "primary_report_qualified_incident",
            "confidence": "moderate_high",
            "operational_impact": "unknown",
            "event_date": "2026-07-25",
            "source_published_at": "2026-07-30",
        }
        for key, expected in expected_finding_values.items():
            if finding.get(key) != expected:
                raise ValueError(
                    f"Gaza historical candidate {key} must be exactly {expected!r}"
                )

        date_assessment = review.get("date_assessment")
        if not isinstance(date_assessment, dict):
            raise ValueError("Gaza substantive review date_assessment is missing")
        if date_assessment.get("event_date") != finding.get("event_date"):
            raise ValueError(
                "Gaza substantive review event_date does not match normalized finding"
            )
        if date_assessment.get("report_publication_date") != finding.get(
            "source_published_at"
        ):
            raise ValueError(
                "Gaza substantive review source publication date does not match "
                "normalized finding"
            )

        materiality = review.get("materiality_assessment")
        materiality_value = (
            materiality.get("assessment") if isinstance(materiality, dict) else None
        )
        if materiality_value != "operating_impact_unclear":
            raise ValueError(
                "Gaza substantive review materiality_assessment must be exactly "
                "'operating_impact_unclear'"
            )
        operational_impact = review.get("operational_impact_assessment")
        if (
            not isinstance(operational_impact, dict)
            or operational_impact.get("assessment") != finding.get("operational_impact")
        ):
            raise ValueError(
                "Gaza substantive review operational_impact does not match "
                "normalized finding"
            )

        taxonomy = review.get("taxonomy_review")
        if not isinstance(taxonomy, dict):
            raise ValueError("Gaza substantive review taxonomy_review is missing")
        taxonomy_fields = {
            "event_type": "event_type",
            "gaza_role": "gaza_role",
            "source_role": "source_role",
            "evidence_level": "evidence_level",
            "confidence": "confidence",
            "operational_impact": "operational_impact",
        }
        for review_key, finding_key in taxonomy_fields.items():
            value = taxonomy.get(review_key)
            if (
                not isinstance(value, dict)
                or value.get("current_value") != finding.get(finding_key)
            ):
                raise ValueError(
                    f"Gaza substantive review {review_key} does not match "
                    "normalized finding"
                )

        attribution = review.get("attribution_assessment")
        safe_wording = (
            str(attribution.get("safe_future_wording") or "")
            if isinstance(attribution, dict)
            else ""
        )
        if "reportedly" not in safe_wording.lower():
            raise ValueError(
                "Gaza substantive review must preserve qualified attribution"
            )
        editorial_restrictions = review.get("editorial_restrictions")
        if (
            not isinstance(editorial_restrictions, list)
            or not editorial_restrictions
            or not all(
                isinstance(item, str) and item.strip()
                for item in editorial_restrictions
            )
        ):
            raise ValueError(
                "Gaza substantive review editorial_restrictions are missing"
            )

        duplicate_check = review.get("duplicate_and_authoritative_match_check")
        if (
            not isinstance(duplicate_check, dict)
            or duplicate_check.get("historical_candidate_remains_distinct") is not True
            or duplicate_check.get("existing_edition_match") is not None
            or duplicate_check.get("existing_source_match") is not None
            or duplicate_check.get("existing_story_cluster_match") is not None
            or duplicate_check.get("existing_historical_match") is not None
        ):
            raise ValueError(
                "Gaza substantive review must preserve a distinct private candidate"
            )

        targets = gaza_match_targets(repo_root)
        source_url = str(
            finding.get("canonical_source_url") or finding.get("source_url") or ""
        ).strip().lower().split("?")[0].rstrip("/")
        source_matches = list(targets["sources_by_url"].get(source_url, []))
        source_identifier = str(
            finding.get("manual_source_identifier")
            or finding.get("source_record_id")
            or finding.get("source_id")
            or ""
        )
        if source_identifier:
            source_matches.extend(
                targets["sources_by_id"].get(source_identifier, [])
            )
        cluster_matches = list(targets["clusters_by_url"].get(source_url, []))
        for key in (
            "event_cluster_id",
            "cluster_id",
            "story_id",
            "topic_fingerprint",
            "normalized_event_key",
        ):
            identifier = str(finding.get(key) or "")
            if identifier:
                cluster_matches.extend(targets["clusters_by_id"].get(identifier, []))
        if source_matches or cluster_matches:
            raise ValueError(
                "Gaza historical candidate now matches an authoritative source or "
                "cluster record"
            )
        if finding.get("matched_edition_date") or finding.get(
            "matched_source_or_cluster_id"
        ):
            raise ValueError(
                "Gaza historical candidate now carries an authoritative match"
            )

        duplicate_findings = []
        normalized_root = base / "normalized"
        for candidate_path in normalized_root.rglob("*.json"):
            if candidate_path.resolve() == normalized_path.resolve():
                continue
            try:
                candidate_record = json.loads(
                    candidate_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            for candidate in candidate_record.get("findings", []):
                if not isinstance(candidate, dict):
                    continue
                candidate_url = str(
                    candidate.get("canonical_source_url")
                    or candidate.get("source_url")
                    or ""
                ).strip().lower().split("?")[0].rstrip("/")
                same_identity = candidate.get("finding_id") == finding_id
                same_incident = bool(source_url) and candidate_url == source_url and (
                    candidate.get("event_date") == finding.get("event_date")
                    or candidate.get("source_published_at")
                    == finding.get("source_published_at")
                )
                if same_identity or same_incident:
                    duplicate_findings.append(str(candidate_path))
        if duplicate_findings:
            raise ValueError(
                "Gaza historical candidate now matches a prior historical record"
            )

        domain_audit_values = {
            "audio_authorized": False,
            "cluster_authorized": False,
            "editorial_restrictions": list(editorial_restrictions),
            "edition_authorized": False,
            "event_date": finding.get("event_date"),
            "event_type": finding.get("event_type"),
            "gaza_role": finding.get("gaza_role"),
            "materiality_assessment": materiality_value,
            "operational_impact": finding.get("operational_impact"),
            "source_published_at": finding.get("source_published_at"),
            "source_record_authorized": False,
        }

    decisions_dir = base / "reviews" / "decisions"
    audit_path = decisions_dir / (
        f"{raw_sha[:24]}-accept-substantively-valid.json"
    )
    try:
        review_artifact_display = review_path.relative_to(repo_root).as_posix()
    except ValueError:
        review_artifact_display = review_path.as_posix()
    immutable_audit_values = {
        "schema_version": "historical_substantive_review_decision_v1",
        "domain": args.domain,
        "raw_sha256": raw_sha,
        "normalized_finding_id": finding_id,
        "review_artifact_path": review_artifact_display,
        "review_artifact_sha256": actual_review_sha,
        "operator": args.operator,
        "decision": decision_spec["audit_decision"],
        "previous_review_status": "pending_review",
        "new_review_status": decision_spec["new_status"],
        "historical_outcome": historical_outcome,
        "queue_action": decision_spec["queue_action"],
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "archive_content_change_authorized": False,
        **domain_audit_values,
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
    if args.dry_run:
        result = {
            "status": "dry_run_validated",
            "domain": args.domain,
            "raw_sha256": raw_sha,
            "normalized_finding_id": finding_id,
            "previous_review_status": previous_status,
            "new_review_status": decision_spec["new_status"],
            "review_artifact_sha256": actual_review_sha,
            "decision_audit_path": str(audit_path),
            "publication_authorized": False,
            "persistent_mutation": False,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

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
    audit = {
        **immutable_audit_values,
        "decided_at": decided_at,
        "normalized_record_sha256_before": sha256_bytes(normalized_path.read_bytes()),
        "normalized_record_sha256_after": sha256_bytes(
            canonical_json(updated_record).encode("utf-8")
        ),
        "changed_fields": ["findings[].review_status"],
    }
    if args.domain == "ice":
        audit["notes"] = [
            "Attribute aggregate figures to Reuters' review of internal federal data and state that DHS confirmed the information-sharing relationship.",
            "Do not describe the complete dataset as public, all arrested people as children, or 460,000 leads as 460,000 unique people.",
            "Do not add unsupported criminal-history, geographic, or case-outcome detail.",
            "Treat the gunpoint arrest as one documented case, not a national force pattern.",
            "Do not claim data sharing alone caused longer ORR custody.",
            "Keep government positions, allegations, and unknowns separate.",
        ]
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
    parser.add_argument("--dry-run", action="store_true")
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
