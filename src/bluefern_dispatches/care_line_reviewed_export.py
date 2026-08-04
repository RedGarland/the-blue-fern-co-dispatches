from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_normalize import extraction_proposals, parse_location, source_payload_fingerprint
from bluefern_dispatches.care_line_record import (
    CARE_LINE_EVENT_TYPES,
    SCHEMA_VERSION,
    SERVICE_EVENT_TYPES,
    CareLineReviewedRecord,
    FieldProvenance,
    deterministic_records_json,
    stable_json_hash,
)
from bluefern_dispatches.care_line_sources import record_is_public


EXPORTER_VERSION = "care-line-reviewed-export-v1"
MANIFEST_SCHEMA_VERSION = "bluefern.care_line.reviewed_export_manifest.v1"

READY_STATUSES = {"approved", "reviewed", "public_approved", "shadow_approved", ""}
REASON_CODES = {
    "missing_facility_or_provider",
    "missing_geography",
    "missing_event_date",
    "missing_service_line",
    "unsupported_event_type",
    "non_operational_context",
    "financial_context_only",
    "workforce_only",
    "stale",
    "duplicate",
    "insufficient_evidence",
    "not_review_approved",
    "withdrawn",
    "superseded",
}
PRESSURE_EVENT_MAP = {
    "clinic_access_strain": "facility_closure",
    "maternity_care_loss": "service_suspension",
    "service_line_cut": "service_closure",
    "er_crowding_or_diversion": "service_reduction",
    "public_health_capacity_cut": "capacity_reduction",
    "behavioral_health_access_strain": "service_reduction",
    "ambulance_or_ems_strain": "service_reduction",
    "specialty_care_delay": "service_reduction",
}
FINANCIAL_CONTEXT_TYPES = {"hospital_closure", "medical_debt_or_affordability", "coverage_disruption", "medicaid_access_pressure"}
WORKFORCE_CONTEXT_TYPES = {"staffing_shortage_access"}


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _bool(row: Mapping[str, Any], key: str) -> bool:
    return row.get(key) is True or str(row.get(key) or "").strip().lower() == "true"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _json(path)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        rows = payload["sources"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        rows = payload["records"]
    else:
        raise ValueError(f"Care Line source file has invalid shape: {path}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def refuse_public_or_pages_path(path: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    forbidden = [repo_root / "output" / "site", repo_root / "bluefern-dispatches-pages"]
    for root in forbidden:
        if root.exists() and _is_under(resolved, root):
            raise ValueError(f"refusing canonical export path inside protected public/Pages location: {path}")


def _dates(date_from: str, date_to: str) -> list[str]:
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date-to cannot be before date-from")
    out = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _provenance(value: Any, source_field: str, *, reviewer: str = "", reason: str = "") -> FieldProvenance:
    return FieldProvenance(
        value=value,
        provenance_type="structured_input",
        source_field=source_field,
        supporting_text=str(value or ""),
        confidence=1.0 if value not in (None, "") else 0.0,
        review_status="confirmed" if value not in (None, "") else "unresolved",
        reviewer=reviewer,
        decision_reason=reason,
    )


def _proposed_provenance(value: Any, source_field: str, supporting_text: str, rule_id: str) -> FieldProvenance:
    return FieldProvenance(value=value, provenance_type="deterministic_extraction", source_field=source_field, supporting_text=supporting_text, confidence=0.8, review_status="proposed", rule_id=rule_id, rule_version="1")


def _review_status(row: Mapping[str, Any]) -> str:
    return _text(row, "care_line_review_status", "review_status", "editorial_review_status") or ("reviewed" if _bool(row, "included") and not _bool(row, "excluded") else "not_reviewed")


def _event_type(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "event_type", "universal_event_type", "healthcare_event_type")
    if explicit:
        return explicit
    pressure_type = _text(row, "pressure_type")
    if pressure_type in FINANCIAL_CONTEXT_TYPES:
        return "financial_context"
    if pressure_type in WORKFORCE_CONTEXT_TYPES:
        return "workforce_context"
    if _bool(row, "context_only") or pressure_type == "context_only":
        return "resource_context"
    return PRESSURE_EVENT_MAP.get(pressure_type, "")


def _service_line(row: Mapping[str, Any]) -> str:
    raw = _text(row, "service_line", "affected_service_line")
    if raw:
        return raw
    for proposal in extraction_proposals(row):
        if proposal.field == "service_line":
            return str(proposal.value or "")
    return ""


def _classification(row: Mapping[str, Any], draft: CareLineReviewedRecord) -> tuple[str, str]:
    review_status = _review_status(row)
    if _bool(row, "withdrawn") or _bool(row, "is_withdrawn"):
        return "withdrawn", "withdrawn"
    if _text(row, "supersedes_record_id") and _text(row, "record_status") == "superseded":
        return "superseded", "superseded"
    if _text(row, "duplicate_of_record_id", "duplicate_of_producer_record_id"):
        return "duplicate", "duplicate"
    if _text(row, "freshness_status").lower() == "stale" or _text(row, "exclusion_reason", "primary_disqualification_reason") == "stale_current_signal":
        return "excluded", "stale"
    if review_status not in READY_STATUSES:
        return "needs_normalization_review", "not_review_approved"
    if draft.event_type in {"financial_context"}:
        return "care_line_only", "financial_context_only"
    if draft.event_type in {"workforce_context"}:
        return "care_line_only", "workforce_only"
    if draft.event_type in {"resource_context", "policy_context", "context_only", ""} or _bool(row, "context_only"):
        return "care_line_only", "non_operational_context"
    issues = draft.validation_issues()
    if issues:
        reason_map = {
            "missing_subject": "missing_facility_or_provider",
            "missing_geography": "missing_geography",
            "missing_event_date": "missing_event_date",
            "missing_service_line": "missing_service_line",
            "unsupported_event_type": "unsupported_event_type",
            "missing_evidence": "insufficient_evidence",
        }
        if issues[0].code == "missing_evidence":
            return "needs_evidence_review", "insufficient_evidence"
        return "needs_normalization_review", reason_map.get(issues[0].code, "unsupported_event_type")
    evidence_provenance = _text(row, "evidence_provenance_type") or ("source_explicit" if _text(row, "effective_evidence_text", "evidence_text") and _text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"} else "missing")
    evidence_valid = _bool(row, "evidence_valid_for_universal_event") or (
        evidence_provenance == "source_explicit"
        and bool(_text(row, "effective_evidence_text", "evidence_text"))
        and _text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"}
    )
    if not evidence_valid or evidence_provenance not in {"source_explicit", "reviewer_transcribed"}:
        return "needs_evidence_review", "insufficient_evidence"
    return "universal_event_ready", ""


def reviewed_record_from_source(row: Mapping[str, Any], *, input_path: Path, index: int = 1, reviewer: str = "", review_reason: str = "") -> CareLineReviewedRecord:
    location = parse_location(row)
    proposals = extraction_proposals(row)
    proposed = {proposal.field: proposal for proposal in proposals}
    facility = _text(row, "facility_name", "provider_name", "affected_provider", "organization_name")
    provider = _text(row, "provider_name", "affected_provider", "organization_name")
    event_type = _event_type(row)
    service_line = _service_line(row)
    raw_evidence = _text(row, "effective_evidence_text", "evidence_text")
    evidence_provenance = _text(row, "evidence_provenance_type") or ("source_explicit" if raw_evidence and _text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"} else "missing")
    effective_evidence = raw_evidence if evidence_provenance in {"source_explicit", "reviewer_transcribed"} else ""
    field_provenance: dict[str, FieldProvenance] = {
        "producer_record_id": _provenance(_text(row, "source_record_id", "producer_record_id", "care_line_record_id"), "source_record_id", reviewer=reviewer, reason=review_reason),
        "source_url": _provenance(_text(row, "url", "canonical_url", "source_url"), "url", reviewer=reviewer, reason=review_reason),
        "source_title": _provenance(_text(row, "title"), "title", reviewer=reviewer, reason=review_reason),
        "supporting_passage": _provenance(effective_evidence, "supporting_passage", reviewer=reviewer, reason=review_reason),
        "state": _provenance(location["state"], "state", reviewer=reviewer, reason=review_reason),
        "announcement_date": _provenance(_text(row, "announcement_date", "published_at", "source_published_date"), "published_at", reviewer=reviewer, reason=review_reason),
    }
    for field, value in (("facility_name", facility), ("provider_name", provider), ("event_type", event_type), ("service_line", service_line), ("city", location["city"])):
        if value:
            source = "structured_input"
            field_provenance[field] = _provenance(value, field, reviewer=reviewer, reason=review_reason)
        elif field in proposed:
            proposal = proposed[field]
            field_provenance[field] = _proposed_provenance(proposal.value, proposal.source_field, proposal.supporting_text, proposal.rule_id)
    record_id = _text(row, "source_record_id", "producer_record_id", "care_line_record_id") or f"care-line-export-{stable_json_hash([input_path.as_posix(), index])[:16]}"
    draft = CareLineReviewedRecord(
        producer_record_id=record_id,
        record_status="active",
        review_status=_review_status(row),  # type: ignore[arg-type]
        public_status="public_approved" if record_is_public(dict(row)) else "not_public",
        universal_event_status="needs_normalization_review",
        care_line_public_eligible=record_is_public(dict(row)),
        source_url=_text(row, "url", "canonical_url", "source_url"),
        source_title=_text(row, "title"),
        source_publisher=_text(row, "publisher", "source_name"),
        source_publication_date=_text(row, "published_at", "source_published_date"),
        source_type=_text(row, "source_family", "source_type"),
        source_role=_text(row, "source_role"),
        supporting_passage=effective_evidence,
        effective_evidence_text=effective_evidence,
        evidence_provenance_type=evidence_provenance,
        evidence_valid_for_universal_event=bool(_bool(row, "evidence_valid_for_universal_event") or (evidence_provenance == "source_explicit" and effective_evidence and _text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"})),
        recommended_status="universal_event_ready" if (_bool(row, "evidence_valid_for_universal_event") or (evidence_provenance == "source_explicit" and effective_evidence and _text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"})) else "needs_evidence_review",
        review_notes=_text(row, "review_notes", "claim_supported"),
        raw_payload_hash=source_payload_fingerprint(row),
        event_type=event_type,
        event_type_raw=_text(row, "event_type", "pressure_type"),
        change_direction=_text(row, "change_direction") or ("reduced" if event_type else ""),
        permanence=_text(row, "permanence", "temporary_or_permanent") or ("temporary_or_unknown" if event_type in CARE_LINE_EVENT_TYPES else ""),
        announcement_date=_text(row, "announcement_date", "published_at", "source_published_date"),
        effective_date=_text(row, "effective_date", "effective_date_text"),
        date_precision=_text(row, "date_precision", "event_date_precision") or "day",
        service_line=service_line,
        service_line_raw=_text(row, "service_line", "affected_service_line"),
        facility_name=facility,
        provider_name=provider,
        parent_organization=_text(row, "parent_organization"),
        operator_name=_text(row, "operator", "operator_name"),
        former_owner=_text(row, "former_owner", "ownership_change_from"),
        new_owner=_text(row, "new_owner", "ownership_change_to"),
        replacement_provider=_text(row, "replacement_provider", "replacement_provider_named"),
        regulator=_text(row, "regulator"),
        facility_type=_text(row, "facility_type"),
        city=location["city"],
        county=location["county"],
        state=location["state"],
        postal_code=location["postal_code"],
        country_code=location["country_code"],
        location_text=location["location_text"],
        geographic_scope=location["geographic_scope"],
        claim_summary=effective_evidence,
        evidence_level=_text(row, "evidence_level"),
        evidence_strength=_text(row, "confidence"),
        is_primary_source=_text(row, "evidence_text_basis") in {"publisher_article", "official_notice", "official_data"},
        verification_notes=_text(row, "limitations"),
        is_withdrawn=_bool(row, "withdrawn") or _bool(row, "is_withdrawn"),
        withdrawal_reason=_text(row, "withdrawal_reason"),
        supersedes_record_id=_text(row, "supersedes_record_id"),
        duplicate_of_record_id=_text(row, "duplicate_of_record_id", "duplicate_of_producer_record_id"),
        correction_reason=_text(row, "correction_reason"),
        field_provenance=field_provenance,
        metadata={
            "exporter_version": EXPORTER_VERSION,
            "input_index": index,
            "reviewer": reviewer,
            "review_reason": review_reason,
            "source_exclusion_reason": _text(row, "exclusion_reason", "primary_disqualification_reason"),
            "evidence_provenance_type": evidence_provenance,
            "evidence_valid_for_universal_event": _bool(row, "evidence_valid_for_universal_event"),
            "review_notes": _text(row, "review_notes", "claim_supported"),
        },
    )
    status, reason = _classification(row, draft)
    payload = draft.model_dump(mode="json")
    payload["record_status"] = status
    payload["universal_event_status"] = status
    payload["metadata"]["canonical_export_reason"] = reason
    if reason == "duplicate":
        payload["metadata"]["shadow_exclusion_reason"] = "duplicate_producer_record"
    elif status in {"care_line_only"}:
        payload["metadata"]["shadow_exclusion_reason"] = "no_operational_access_change"
    elif status in {"withdrawn", "superseded", "excluded"}:
        payload["metadata"]["shadow_exclusion_reason"] = "not_review_approved"
    return CareLineReviewedRecord.model_validate(payload)


def export_records_for_date(repo_root: Path, edition_date: str, *, output_root: Path, check_only: bool = False, reviewer: str = "", review_reason: str = "", include_discovery_diagnostics: bool = False) -> dict[str, Any]:
    source_path = repo_root / "data" / "dispatches" / "care-line" / "sources" / edition_date / "manual_sources.json"
    if not source_path.exists():
        manifest = _manifest(edition_date, edition_date, [], [], missing=[edition_date])
        if include_discovery_diagnostics:
            discovered = repo_root / "data" / "dispatches" / "care-line" / "sources" / edition_date / "discovered_sources.json"
            manifest["discovery_diagnostics"] = {
                "discovered_source_file_present": discovered.exists(),
                "discovered_source_count": len(_source_rows(discovered)) if discovered.exists() else 0,
                "included_in_canonical_export": False,
            }
        return manifest
    rows = _source_rows(source_path)
    records = [reviewed_record_from_source(row, input_path=source_path, index=index, reviewer=reviewer, review_reason=review_reason) for index, row in enumerate(rows, start=1)]
    out_dir = output_root / edition_date
    manifest = _manifest(edition_date, edition_date, rows, records)
    if include_discovery_diagnostics:
        discovered = repo_root / "data" / "dispatches" / "care-line" / "sources" / edition_date / "discovered_sources.json"
        manifest["discovery_diagnostics"] = {
            "discovered_source_file_present": discovered.exists(),
            "discovered_source_count": len(_source_rows(discovered)) if discovered.exists() else 0,
            "included_in_canonical_export": False,
        }
    if not check_only:
        _write_atomic(out_dir / "reviewed_records.json", deterministic_records_json(records))
        _write_atomic(out_dir / "reviewed_records_manifest.json", json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return manifest


def export_range(repo_root: Path, *, date_from: str, date_to: str, output_root: Path, report_path: Path | None = None, check_only: bool = False, reviewer: str = "", review_reason: str = "", include_discovery_diagnostics: bool = False) -> dict[str, Any]:
    refuse_public_or_pages_path(output_root if output_root.is_absolute() else repo_root / output_root, repo_root)
    if report_path:
        refuse_public_or_pages_path(report_path if report_path.is_absolute() else repo_root / report_path, repo_root)
    manifests = []
    for value in _dates(date_from, date_to):
        manifests.append(export_records_for_date(repo_root, value, output_root=output_root if output_root.is_absolute() else repo_root / output_root, check_only=check_only, reviewer=reviewer, review_reason=review_reason, include_discovery_diagnostics=include_discovery_diagnostics))
    aggregate = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "producer": "Care Line",
        "date_from": date_from,
        "date_to": date_to,
        "daily_manifests": manifests,
        "input_record_count": sum(item["input_record_count"] for item in manifests),
        "reviewed_record_count": sum(item["reviewed_record_count"] for item in manifests),
        "universal_event_ready_count": sum(item["universal_event_ready_count"] for item in manifests),
        "care_line_only_count": sum(item["care_line_only_count"] for item in manifests),
        "review_required_count": sum(item["review_required_count"] for item in manifests),
        "needs_evidence_review_count": sum(item.get("needs_evidence_review_count", 0) for item in manifests),
        "excluded_count": sum(item["excluded_count"] for item in manifests),
        "withdrawn_count": sum(item["withdrawn_count"] for item in manifests),
        "duplicate_count": sum(item["duplicate_count"] for item in manifests),
        "exporter_version": EXPORTER_VERSION,
        "contract_version": SCHEMA_VERSION,
        "check_only": check_only,
    }
    if report_path:
        _write_atomic(report_path if report_path.is_absolute() else repo_root / report_path, json.dumps(aggregate, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return aggregate


def _manifest(date_from: str, date_to: str, input_rows: list[Mapping[str, Any]], records: list[CareLineReviewedRecord], *, missing: list[str] | None = None) -> dict[str, Any]:
    counts = Counter(row.universal_event_status for row in records)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "producer": "Care Line",
        "date_from": date_from,
        "date_to": date_to,
        "input_record_count": len(input_rows),
        "reviewed_record_count": len(records),
        "universal_event_ready_count": counts.get("universal_event_ready", 0),
        "care_line_only_count": counts.get("care_line_only", 0),
        "review_required_count": counts.get("needs_normalization_review", 0) + counts.get("needs_evidence_review", 0),
        "needs_evidence_review_count": counts.get("needs_evidence_review", 0),
        "excluded_count": counts.get("excluded", 0),
        "withdrawn_count": counts.get("withdrawn", 0),
        "duplicate_count": counts.get("duplicate", 0),
        "record_ids": sorted(record.producer_record_id for record in records),
        "record_hashes": {record.producer_record_id: stable_json_hash(record.deterministic_dict()) for record in sorted(records, key=lambda item: item.producer_record_id)},
        "missing_dates": missing or [],
        "exporter_version": EXPORTER_VERSION,
        "contract_version": SCHEMA_VERSION,
    }


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Care Line reviewed records to canonical reviewed-record storage.")
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default="data/dispatches/care-line/reviewed")
    parser.add_argument("--report", default="")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--review-reason", default="")
    parser.add_argument("--include-discovery-diagnostics", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        result = export_range(
            repo_root,
            date_from=args.date_from,
            date_to=args.date_to,
            output_root=Path(args.output_root),
            report_path=Path(args.report) if args.report else None,
            check_only=args.check_only,
            reviewer=args.reviewer,
            review_reason=args.review_reason,
            include_discovery_diagnostics=args.include_discovery_diagnostics,
        )
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
