from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from bluefern_dispatches.care_line_record import SCHEMA_VERSION as CARE_LINE_REVIEWED_RECORD_SCHEMA_VERSION
from bluefern_dispatches.care_line_record import CareLineReviewedRecord
from bluefern_dispatches.universal_events import CandidateStatus, EventDomain, EventStatus
from bluefern_dispatches.universal_events.enums import EvidenceRole
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventAttributeRow,
    EventEntityLinkRow,
    SourceItemRow,
)
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION, ResolverThresholds
from bluefern_dispatches.universal_events.service import UniversalEventService

from .care_line import ADAPTER_VERSION, SUPPORTED_EVENT_TYPES, _event_type, _exclusion_reason, _facility_name, _service_line, _text


PHASE5_SCHEMA_VERSION = "bluefern.care_line.phase5.v1"
SUPPORTED_INPUT_TYPES = {"manual-sources", "discovered-sources", "reviewed-records", "canonical-reviewed-records", "claim-ledger", "source-manifest", "curation-manifest"}
ADMITTED_INPUT_TYPES = {"manual-sources", "reviewed-records", "canonical-reviewed-records", "claim-ledger"}
RENDERED_EXTENSIONS = {".html", ".htm"}


@dataclass(frozen=True)
class CanonicalCareLineRecord:
    producer: str
    producer_record_id: str
    producer_input_type: str
    producer_input_path: str
    review_status: str = ""
    public_status: str = ""
    record_status: str = ""
    original_publisher: str = ""
    source_url: str = ""
    source_title: str = ""
    source_publication_date: str = ""
    supporting_passage: str = ""
    facility_name: str = ""
    provider_name: str = ""
    parent_organization: str = ""
    operator_name: str = ""
    former_owner: str = ""
    new_owner: str = ""
    address: str = ""
    city: str = ""
    county: str = ""
    state: str = ""
    postal_code: str = ""
    country_code: str = "US"
    event_type_raw: str = ""
    service_line_raw: str = ""
    announcement_date: str = ""
    effective_date: str = ""
    date_precision: str = ""
    permanence: str = ""
    summary: str = ""
    reason: str = ""
    evidence_level: str = ""
    source_role: str = ""
    raw_payload_hash: str = ""
    field_provenance: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def as_ingestion_record(self) -> dict[str, Any]:
        row = dict(self.raw_payload)
        if self.producer_input_type not in ADMITTED_INPUT_TYPES:
            row["care_line_review_status"] = "not_reviewed"
            row["qualifies_for_public_inclusion"] = False
            row["source_public_story_eligible"] = False
            row["pressure_signal"] = False
        row.setdefault("source_record_id", self.producer_record_id)
        row.setdefault("care_line_review_status", self.review_status)
        row.setdefault("url", self.source_url)
        row.setdefault("publisher", self.original_publisher)
        row.setdefault("title", self.source_title)
        row.setdefault("published_at", self.source_publication_date)
        row.setdefault("source_published_date", self.source_publication_date[:10])
        row.setdefault("evidence_text", self.supporting_passage)
        row.setdefault("facility_name", self.facility_name or self.provider_name)
        row.setdefault("provider_name", self.provider_name or self.facility_name)
        row.setdefault("parent_organization", self.parent_organization)
        row.setdefault("operator", self.operator_name)
        row.setdefault("former_owner", self.former_owner)
        row.setdefault("new_owner", self.new_owner)
        row.setdefault("address", self.address)
        row.setdefault("city", self.city)
        row.setdefault("county", self.county)
        row.setdefault("state", self.state)
        row.setdefault("postal_code", self.postal_code)
        row.setdefault("country_code", self.country_code or "US")
        row.setdefault("event_type", self.event_type_raw)
        row.setdefault("service_line", self.service_line_raw)
        row.setdefault("announcement_date", self.announcement_date)
        row.setdefault("effective_date", self.effective_date)
        row.setdefault("event_date_precision", self.date_precision)
        row.setdefault("permanence", self.permanence)
        row.setdefault("pressure_summary", self.summary)
        row.setdefault("pressure_reason", self.reason)
        row.setdefault("evidence_level", self.evidence_level)
        row.setdefault("source_role", self.source_role)
        row["_care_line_phase5"] = {
            "schema_version": PHASE5_SCHEMA_VERSION,
            "producer_input_type": self.producer_input_type,
            "producer_input_path": self.producer_input_path,
            "field_provenance": self.field_provenance,
            "raw_payload_hash": self.raw_payload_hash,
        }
        if self.producer_input_type == "canonical-reviewed-records":
            row["_care_line_reviewed_record_contract"] = dict(row.get("_care_line_reviewed_record_contract") or {})
        return row


def stable_json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def reject_rendered_source(path: Path) -> None:
    if path.suffix.lower() in RENDERED_EXTENSIONS:
        raise ValueError("rendered HTML is not a Care Line structured input")


def detect_input_type(path: Path) -> str:
    reject_rendered_source(path)
    name = path.name
    if name == "manual_sources.json":
        return "manual-sources"
    if name == "discovered_sources.json":
        return "discovered-sources"
    if name == "sources_manifest.json":
        return "source-manifest"
    if name == "curation_manifest.json":
        return "curation-manifest"
    if name in {"claim_ledger.json", "claims.json"}:
        return "claim-ledger"
    if name == "reviewed_records.json":
        return "canonical-reviewed-records"
    if name == "review.json":
        return "reviewed-records"
    raise ValueError(f"unsupported or ambiguous Care Line input type for {path}")


def _rows_from_payload(payload: Any, input_type: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sources", "records", "claims", "reviewed_records"):
            if isinstance(payload.get(key), list):
                return [dict(row) for row in payload[key] if isinstance(row, dict)]
    raise ValueError(f"{input_type} payload does not contain structured records")


def load_canonical_records(path: Path, input_type: str | None = None) -> list[CanonicalCareLineRecord]:
    reject_rendered_source(path)
    resolved_type = input_type or detect_input_type(path)
    if resolved_type not in SUPPORTED_INPUT_TYPES:
        raise ValueError(f"unsupported Care Line input type: {resolved_type}")
    payload = _load_json(path)
    rows = _rows_from_payload(payload, resolved_type)
    return [normalize_record(row, input_type=resolved_type, input_path=path, index=index) for index, row in enumerate(rows, start=1)]


def normalize_record(row: Mapping[str, Any], *, input_type: str, input_path: Path, index: int = 1) -> CanonicalCareLineRecord:
    raw = dict(row)
    if input_type == "canonical-reviewed-records" or raw.get("schema_version") == CARE_LINE_REVIEWED_RECORD_SCHEMA_VERSION:
        return normalize_canonical_reviewed_record(raw, input_type=input_type, input_path=input_path, index=index)
    raw_hash = stable_json_hash(raw)
    record_id = _text(raw, "source_record_id", "producer_record_id", "care_line_record_id", "source_id", "claim_id") or f"{input_type}:{raw_hash[:16]}"
    event_type, _ = _event_type(raw)
    service_raw, _ = _service_line(raw)
    public_status = "public_approved" if raw.get("qualifies_for_public_inclusion") is True else "not_public"
    review_status = _text(raw, "care_line_review_status", "review_status", "editorial_review_status")
    if input_type == "manual-sources" and raw.get("included") is True and raw.get("excluded") is False:
        review_status = review_status or "reviewed"
    if input_type in {"discovered-sources", "source-manifest", "curation-manifest"}:
        review_status = review_status or "not_reviewed"
    provenance = {
        "producer_record_id": f"{input_path.as_posix()}#{index}:source_record_id",
        "source_url": f"{input_path.as_posix()}#{index}:url",
        "supporting_passage": f"{input_path.as_posix()}#{index}:evidence_text",
        "event_type_raw": f"{input_path.as_posix()}#{index}:event_type/pressure_type",
    }
    return CanonicalCareLineRecord(
        producer="Care Line",
        producer_record_id=record_id,
        producer_input_type=input_type,
        producer_input_path=input_path.as_posix(),
        review_status=review_status,
        public_status=public_status,
        record_status="excluded" if raw.get("excluded") is True else "active",
        original_publisher=_text(raw, "publisher", "source_name"),
        source_url=_text(raw, "url", "canonical_url", "source_url"),
        source_title=_text(raw, "title"),
        source_publication_date=_text(raw, "published_at", "source_published_date"),
        supporting_passage=_text(raw, "evidence_text", "claim_supported", "summary_or_snippet"),
        facility_name=_facility_name(raw),
        provider_name=_text(raw, "provider_name", "affected_provider", "organization_name"),
        parent_organization=_text(raw, "parent_organization"),
        operator_name=_text(raw, "operator"),
        former_owner=_text(raw, "former_owner", "ownership_change_from"),
        new_owner=_text(raw, "new_owner", "ownership_change_to"),
        address=_text(raw, "address", "address_line_1"),
        city=_text(raw, "city"),
        county=_text(raw, "county"),
        state=_text(raw, "state"),
        postal_code=_text(raw, "postal_code", "zip"),
        country_code=_text(raw, "country_code") or "US",
        event_type_raw=event_type or _text(raw, "event_type", "healthcare_event_type", "pressure_type"),
        service_line_raw=service_raw,
        announcement_date=_text(raw, "announcement_date", "published_at", "source_published_date"),
        effective_date=_text(raw, "effective_date", "effective_date_text"),
        date_precision=_text(raw, "event_date_precision", "date_precision") or "day",
        permanence=_text(raw, "permanence", "temporary_or_permanent"),
        summary=_text(raw, "pressure_summary", "summary_or_snippet"),
        reason=_text(raw, "pressure_reason", "reason"),
        evidence_level=_text(raw, "evidence_level"),
        source_role=_text(raw, "source_role"),
        raw_payload_hash=raw_hash,
        field_provenance=provenance,
        raw_payload=raw,
    )


def normalize_canonical_reviewed_record(row: Mapping[str, Any], *, input_type: str, input_path: Path, index: int = 1) -> CanonicalCareLineRecord:
    reviewed = CareLineReviewedRecord.model_validate(dict(row))
    adapter_record = reviewed.to_adapter_record()
    return CanonicalCareLineRecord(
        producer=reviewed.producer,
        producer_record_id=reviewed.producer_record_id,
        producer_input_type="canonical-reviewed-records",
        producer_input_path=input_path.as_posix(),
        review_status=reviewed.review_status,
        public_status=reviewed.public_status,
        record_status=reviewed.record_status,
        original_publisher=reviewed.source_publisher,
        source_url=reviewed.source_url,
        source_title=reviewed.source_title,
        source_publication_date=reviewed.source_publication_date,
        supporting_passage=reviewed.supporting_passage,
        facility_name=reviewed.facility_name,
        provider_name=reviewed.provider_name,
        parent_organization=reviewed.parent_organization,
        operator_name=reviewed.operator_name,
        former_owner=reviewed.former_owner,
        new_owner=reviewed.new_owner,
        address=reviewed.address_line_1,
        city=reviewed.city,
        county=reviewed.county,
        state=reviewed.state,
        postal_code=reviewed.postal_code,
        country_code=reviewed.country_code,
        event_type_raw=reviewed.event_type,
        service_line_raw=reviewed.service_line,
        announcement_date=reviewed.announcement_date,
        effective_date=reviewed.effective_date,
        date_precision=reviewed.date_precision,
        permanence=reviewed.permanence,
        summary=reviewed.claim_summary,
        reason=reviewed.verification_notes,
        evidence_level=reviewed.evidence_level,
        source_role=reviewed.source_role,
        raw_payload_hash=reviewed.raw_payload_hash,
        field_provenance={key: value.model_dump(mode="json") for key, value in sorted(reviewed.field_provenance.items())},
        raw_payload=adapter_record,
    )


def find_structured_sources(repo_root: Path) -> list[dict[str, Any]]:
    roots = [
        repo_root / "data" / "dispatches" / "care-line" / "sources",
        repo_root / "data" / "dispatches" / "care-line" / "review",
        repo_root / "data" / "dispatches" / "care-line" / "candidates",
        repo_root / "data" / "dispatches" / "care-line" / "manifests",
        repo_root / "data" / "dispatches" / "care-line" / "claims",
        repo_root / "output" / "dispatches" / "care-line" / "review",
        repo_root / "output" / "dispatches" / "care-line" / "editions",
    ]
    out: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            out.append({"path_pattern": root.as_posix(), "exists": False, "safe_for_shadow_ingestion": False, "reason": "missing"})
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in RENDERED_EXTENSIONS:
                out.append({"path_pattern": path.as_posix(), "exists": True, "safe_for_shadow_ingestion": False, "reason": "rendered_html_excluded"})
                continue
            if path.suffix.lower() != ".json":
                continue
            try:
                input_type = detect_input_type(path)
                rows = load_canonical_records(path, input_type)
                safe = input_type in ADMITTED_INPUT_TYPES
                reason = "admitted_input_contract" if safe else "structured_but_not_authoritative_for_ingestion"
                out.append(
                    {
                        "path_pattern": path.as_posix(),
                        "exists": True,
                        "input_type": input_type,
                        "producer_stage": _producer_stage(input_type),
                        "record_count": len(rows),
                        "reviewed": any(row.review_status in {"approved", "reviewed", "corrected", "public_approved", "shadow_approved"} for row in rows),
                        "public_approved": any(row.public_status == "public_approved" for row in rows),
                        "preserves_original_source_provenance": any(bool(row.source_url) for row in rows),
                        "contains_event_classification": any(bool(row.event_type_raw) for row in rows),
                        "contains_provider_or_facility": any(bool(row.facility_name or row.provider_name) for row in rows),
                        "contains_geography": any(bool(row.city or row.county or row.state) for row in rows),
                        "contains_dates": any(bool(row.announcement_date or row.effective_date) for row in rows),
                        "contains_evidence_text": any(bool(row.supporting_passage) for row in rows),
                        "may_contain_duplicate_or_rejected_records": True,
                        "safe_for_shadow_ingestion": safe,
                        "authoritative_or_derived": "authoritative" if safe else "derived_or_diagnostic",
                        "reason": reason,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                out.append({"path_pattern": path.as_posix(), "exists": True, "safe_for_shadow_ingestion": False, "reason": f"unsupported_shape: {type(exc).__name__}: {exc}"})
    return sorted(out, key=lambda item: item["path_pattern"])


def _producer_stage(input_type: str) -> str:
    return {
        "manual-sources": "reviewed manual source pack",
        "discovered-sources": "raw discovery output",
        "reviewed-records": "reviewed structured records",
        "canonical-reviewed-records": "canonical reviewed structured records",
        "claim-ledger": "claim ledger",
        "source-manifest": "generated source manifest",
        "curation-manifest": "generated curation manifest",
    }.get(input_type, "unknown")


def analyze_exclusions(canonical_records: Iterable[CanonicalCareLineRecord]) -> dict[str, Any]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for canonical in canonical_records:
        ingestion_row = canonical.as_ingestion_record()
        reason = _exclusion_reason(ingestion_row, seen)
        if canonical.producer_record_id:
            seen.add(canonical.producer_record_id)
        if not reason:
            continue
        missing = []
        if not canonical.source_url:
            missing.append("source_url")
        if not (canonical.facility_name or canonical.provider_name):
            missing.append("facility_or_provider")
        if not (canonical.city or canonical.county or canonical.state):
            missing.append("geography")
        if not (canonical.announcement_date or canonical.effective_date):
            missing.append("event_date")
        if not canonical.supporting_passage:
            missing.append("supporting_passage")
        classification = _classify_exclusion(canonical, reason, missing)
        rows.append(
            {
                "producer_record_id": canonical.producer_record_id,
                "input_type": canonical.producer_input_type,
                "source_file": canonical.producer_input_path,
                "original_publisher": canonical.original_publisher,
                "source_url": canonical.source_url,
                "exclusion_reason": reason,
                "missing_or_invalid_fields": missing,
                "another_artifact_contains_missing_information": False,
                "exclusion_classification": classification,
                "adapter_enhancement_recommended": classification in {"taxonomy_mapping_gap", "care_line_input_contract_too_narrow"},
                "manual_review_could_resolve": reason in {"missing_provider_or_facility", "missing_geography", "ambiguous_service_change"},
            }
        )
    return {
        "schema_version": PHASE5_SCHEMA_VERSION,
        "excluded_records": sorted(rows, key=lambda item: (item["exclusion_reason"], item["producer_record_id"])),
        "aggregates": _exclusion_aggregates(rows),
    }


def _classify_exclusion(record: CanonicalCareLineRecord, reason: str, missing: list[str]) -> str:
    if record.producer_input_type == "discovered-sources":
        return "source_file_discovery_gap" if reason == "not_review_approved" else "expected_unsupported_record"
    if reason == "unsupported_event_type":
        return "taxonomy_mapping_gap"
    if reason == "missing_provider_or_facility" and "facility_or_provider" in missing:
        return "care_line_input_contract_too_narrow"
    if reason == "not_review_approved":
        return "review_status_mismatch"
    if reason in {"stale_current_signal", "resource_only_baseline", "no_operational_access_change", "financial_context_only", "policy_only"}:
        return "correct_exclusion"
    return "record_quality_problem"


def _exclusion_aggregates(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    def counts(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "")
            if value:
                out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items()))

    recoverable = [row for row in rows if row.get("adapter_enhancement_recommended") or row.get("manual_review_could_resolve")]
    return {
        "exclusions_by_reason": counts("exclusion_reason"),
        "exclusions_by_input_type": counts("input_type"),
        "exclusions_by_event_classification": {},
        "exclusions_by_publisher": counts("original_publisher"),
        "exclusions_by_state": {},
        "recoverable_from_another_structured_source": 0,
        "recoverable_exclusion_count": len(recoverable),
        "irrecoverable_exclusion_count": len(rows) - len(recoverable),
        "potential_taxonomy_gaps": [row["producer_record_id"] for row in rows if row.get("exclusion_classification") == "taxonomy_mapping_gap"],
        "potential_data_quality_defects": [row["producer_record_id"] for row in rows if row.get("exclusion_classification") == "record_quality_problem"],
    }


def select_bounded_real_sample(repo_root: Path, *, max_dates: int = 365, max_records: int = 500) -> dict[str, Any]:
    files = []
    for path in sorted((repo_root / "data" / "dispatches" / "care-line" / "sources").glob("*/*.json")):
        try:
            input_type = detect_input_type(path)
        except ValueError:
            continue
        if input_type not in {"manual-sources", "discovered-sources"}:
            continue
        rows = load_canonical_records(path, input_type)
        files.append({"path": path.as_posix(), "input_type": input_type, "date": path.parent.name, "record_count": len(rows)})
    selected = []
    total = 0
    for item in files:
        if len({row["date"] for row in selected}) >= max_dates or total >= max_records:
            break
        selected.append(item)
        total += int(item["record_count"])
    return {
        "schema_version": PHASE5_SCHEMA_VERSION,
        "selection_rationale": "All available structured Care Line source files were selected because repository data is below Phase 5 maximums.",
        "target": "at least 90 dates or 100 reviewed records",
        "maximums": {"dates": max_dates, "records": max_records},
        "selected_files": selected,
        "selected_dates": sorted({row["date"] for row in selected}),
        "selected_record_count": total,
        "bounded": len({row["date"] for row in selected}) <= max_dates and total <= max_records,
    }


def build_bootstrap_review_artifact(service: UniversalEventService, *, shadow_run_id: str, limit: int = 50) -> dict[str, Any]:
    items = []
    with service.repository.session_scope() as session:
        mentions = list(session.execute(select(EntityMentionRow).order_by(EntityMentionRow.mention_id)).scalars())[:limit]
        for mention in mentions:
            matches = list(session.execute(select(EntityMatchCandidateRow).where(EntityMatchCandidateRow.mention_id == mention.mention_id).order_by(EntityMatchCandidateRow.rank)).scalars())
            items.append(
                {
                    "shadow_run_id": shadow_run_id,
                    "mention_id": mention.mention_id,
                    "raw_mention": mention.raw_name,
                    "entity_kind": mention.entity_kind,
                    "proposed_canonical_name": mention.raw_name,
                    "organization_or_location_type": "healthcare_facility" if mention.entity_kind == "organization" else "service_area",
                    "address": {
                        "raw_address": mention.raw_address,
                        "locality": mention.locality,
                        "region": mention.region,
                        "postal_code": mention.postal_code,
                        "country_code": mention.country_code,
                    },
                    "external_identifiers": dict(mention.external_identifiers_json or {}),
                    "supporting_care_line_records": [mention.candidate_id],
                    "duplicate_candidates": [match.organization_id or match.location_id for match in matches],
                    "proposed_aliases": [mention.raw_name],
                    "reviewer_decision": "",
                    "decision_reason": "",
                }
            )
    return {"schema_version": PHASE5_SCHEMA_VERSION, "shadow_run_id": shadow_run_id, "bootstrap_items": items}


def calibration_metrics(review_decisions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = [dict(row) for row in review_decisions]
    reviewed = len(decisions)
    matched = [row for row in decisions if row.get("decision_type") == "matched"]
    correct_top1 = sum(1 for row in matched if row.get("selected_rank") == 1)
    top3 = sum(1 for row in matched if int(row.get("selected_rank") or 99) <= 3)
    automatic = [row for row in decisions if row.get("was_automatic_match")]
    automatic_correct = [row for row in automatic if row.get("decision_type") == "matched" and row.get("selected_rank") == 1]
    label = "insufficient_sample" if reviewed < 30 else "provisional" if reviewed < 100 else "exploratory"
    return {
        "schema_version": PHASE5_SCHEMA_VERSION,
        "sample_label": label,
        "reviewed_mention_count": reviewed,
        "top_1_candidate_accuracy": round(correct_top1 / len(matched), 4) if matched else None,
        "top_3_candidate_recall": round(top3 / len(matched), 4) if matched else None,
        "automatic_match_precision": round(len(automatic_correct) / len(automatic), 4) if automatic else None,
        "automatic_match_false_positive_count": len(automatic) - len(automatic_correct),
        "automatic_match_coverage": round(len(automatic) / reviewed, 4) if reviewed else 0.0,
        "ambiguity_rate": round(sum(1 for row in decisions if row.get("review_group") == "ambiguous") / reviewed, 4) if reviewed else 0.0,
        "unresolved_rate": round(sum(1 for row in decisions if row.get("decision_type") in {"deferred", "unresolved"}) / reviewed, 4) if reviewed else 0.0,
        "created_new_rate": round(sum(1 for row in decisions if row.get("decision_type") == "created_new") / reviewed, 4) if reviewed else 0.0,
        "rejected_candidate_rate": round(sum(1 for row in decisions if row.get("decision_type") == "rejected_match") / reviewed, 4) if reviewed else 0.0,
        "identifier_conflict_count": sum(1 for row in decisions if row.get("identifier_conflict")),
        "administrative_region_conflict_count": sum(1 for row in decisions if row.get("administrative_region_conflict")),
        "health_system_facility_confusion_count": sum(1 for row in decisions if row.get("health_system_facility_confusion")),
        "alias_collision_count": sum(1 for row in decisions if row.get("alias_collision")),
    }


def threshold_evaluation(review_decisions: Iterable[Mapping[str, Any]], thresholds: Iterable[Mapping[str, float]]) -> dict[str, Any]:
    decisions = [dict(row) for row in review_decisions]
    rows = []
    for config in thresholds:
        auto_threshold = float(config.get("auto_match_threshold", ResolverThresholds.auto_match_threshold))
        margin = float(config.get("ambiguity_margin", ResolverThresholds.ambiguity_margin))
        auto = [row for row in decisions if float(row.get("top_score") or 0.0) >= auto_threshold and float(row.get("score_margin") or 1.0) >= margin]
        correct = [row for row in auto if row.get("decision_type") == "matched" and row.get("selected_rank") == 1]
        rows.append(
            {
                "auto_match_threshold": auto_threshold,
                "ambiguity_margin": margin,
                "auto_matched_mention_count": len(auto),
                "correct_auto_matches": len(correct),
                "incorrect_auto_matches": len(auto) - len(correct),
                "precision": round(len(correct) / len(auto), 4) if auto else None,
                "coverage": round(len(auto) / len(decisions), 4) if decisions else 0.0,
                "ambiguous_count": sum(1 for row in decisions if row.get("review_group") == "ambiguous"),
                "unresolved_count": sum(1 for row in decisions if row.get("decision_type") in {"deferred", "unresolved"}),
            }
        )
    recommendation = "do_not_change_defaults"
    if len([row for row in decisions if row.get("decision_type") == "matched"]) >= 30:
        viable = [row for row in rows if row["precision"] is not None and row["precision"] >= 0.98 and row["incorrect_auto_matches"] == 0]
        if viable:
            recommendation = "review_possible_threshold_change"
    return {"schema_version": PHASE5_SCHEMA_VERSION, "evaluations": rows, "recommendation": recommendation}


def promotion_eligibility(service: UniversalEventService, candidate_id: str, promotion_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    with service.repository.session_scope() as session:
        candidate = session.get(CandidateEventRow, candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        metadata = dict(candidate.metadata_json or {})
        source_item = session.get(SourceItemRow, candidate.source_item_id)
        if candidate.candidate_status != CandidateStatus.APPROVED:
            blockers.append("candidate_not_approved")
        if metadata.get("shadow_withdrawn"):
            blockers.append("candidate_withdrawn")
        if metadata.get("duplicate_of_producer_record_id"):
            blockers.append("unresolved_producer_duplicate")
        if source_item is None or not (source_item.source_url or source_item.canonical_url):
            blockers.append("missing_traceable_source")
        if source_item is None or not source_item.supporting_passage:
            blockers.append("missing_evidence")
        if str(metadata.get("event_type") or "") not in SUPPORTED_EVENT_TYPES:
            blockers.append("unsupported_event_type")
        mentions = list(session.execute(select(EntityMentionRow).where(EntityMentionRow.candidate_id == candidate_id)).scalars())
        effective: dict[str, EntityResolutionDecisionRow] = {}
        for mention in mentions:
            decisions = list(
                session.execute(
                    select(EntityResolutionDecisionRow)
                    .where(EntityResolutionDecisionRow.mention_id == mention.mention_id)
                    .order_by(EntityResolutionDecisionRow.created_at.desc(), EntityResolutionDecisionRow.resolution_decision_id.desc())
                ).scalars()
            )
            if decisions:
                effective[mention.mention_id] = decisions[0]
        required_roles = _required_mention_roles(str(metadata.get("event_type") or ""))
        for role in required_roles:
            role_mentions = [mention for mention in mentions if mention.mention_role == role]
            if not role_mentions:
                blockers.append(f"missing_required_mention:{role}")
                continue
            for mention in role_mentions:
                decision = effective.get(mention.mention_id)
                if decision is None or decision.decision_type not in {"matched", "created_new", "corrected"}:
                    blockers.append(f"unresolved_required_mention:{role}")
        if promotion_review is None or promotion_review.get("decision") != "approved":
            blockers.append("promotion_review_not_approved")
        else:
            for key in ("reviewer", "decision_reason", "candidate_fingerprint", "resolution_fingerprint", "evidence_fingerprint"):
                if not promotion_review.get(key):
                    blockers.append(f"promotion_review_missing_{key}")
            if promotion_review.get("candidate_fingerprint") and promotion_review.get("candidate_fingerprint") != candidate_fingerprint(session, candidate_id):
                blockers.append("stale_candidate_review")
            if promotion_review.get("resolution_fingerprint") and promotion_review.get("resolution_fingerprint") != resolution_fingerprint(session, candidate_id):
                blockers.append("stale_resolution_review")
            if promotion_review.get("evidence_fingerprint") and promotion_review.get("evidence_fingerprint") != evidence_fingerprint(session, candidate_id):
                blockers.append("stale_evidence_review")
        return {
            "candidate_id": candidate_id,
            "eligible": not blockers,
            "blocking_conditions": sorted(set(blockers)),
            "warnings": sorted(set(warnings)),
            "candidate_fingerprint": candidate_fingerprint(session, candidate_id),
            "resolution_fingerprint": resolution_fingerprint(session, candidate_id),
            "evidence_fingerprint": evidence_fingerprint(session, candidate_id),
        }


def _required_mention_roles(event_type: str) -> list[str]:
    if event_type in {"ownership_change", "operator_change"}:
        return ["facility", "event_location", "new_owner"]
    if event_type in {"service_closure", "service_suspension", "service_reduction", "service_expansion", "service_restoration"}:
        return ["facility", "event_location"]
    return ["facility", "event_location"]


def candidate_fingerprint(session: Any, candidate_id: str) -> str:
    row = session.get(CandidateEventRow, candidate_id)
    return stable_json_hash(
        {
            "candidate_id": candidate_id,
            "candidate_status": row.candidate_status.value if hasattr(row.candidate_status, "value") else str(row.candidate_status),
            "source_item_id": row.source_item_id,
            "metadata": row.metadata_json,
        }
    )


def resolution_fingerprint(session: Any, candidate_id: str) -> str:
    rows = list(session.execute(select(EntityMentionRow).where(EntityMentionRow.candidate_id == candidate_id).order_by(EntityMentionRow.mention_id)).scalars())
    payload = []
    for mention in rows:
        decisions = list(
            session.execute(
                select(EntityResolutionDecisionRow)
                .where(EntityResolutionDecisionRow.mention_id == mention.mention_id)
                .order_by(EntityResolutionDecisionRow.created_at, EntityResolutionDecisionRow.resolution_decision_id)
            ).scalars()
        )
        payload.append(
            {
                "mention_id": mention.mention_id,
                "role": mention.mention_role,
                "decisions": [
                    {
                        "decision_id": decision.resolution_decision_id,
                        "decision_type": decision.decision_type,
                        "organization_id": decision.organization_id,
                        "location_id": decision.location_id,
                        "supersedes": decision.supersedes_decision_id,
                    }
                    for decision in decisions
                ],
            }
        )
    return stable_json_hash(payload)


def evidence_fingerprint(session: Any, candidate_id: str) -> str:
    candidate = session.get(CandidateEventRow, candidate_id)
    source_item = session.get(SourceItemRow, candidate.source_item_id) if candidate else None
    return stable_json_hash(
        {
            "candidate_id": candidate_id,
            "source_item_id": candidate.source_item_id if candidate else "",
            "source_url": source_item.source_url if source_item else "",
            "content_hash": source_item.content_hash if source_item else "",
            "supporting_passage": source_item.supporting_passage if source_item else "",
        }
    )


def promotion_preview(service: UniversalEventService, *, shadow_run_id: str, promotion_reviews: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    review_by_candidate = {str(row.get("candidate_id") or ""): dict(row) for row in promotion_reviews}
    previews = []
    with service.repository.session_scope() as session:
        candidates = list(session.execute(select(CandidateEventRow).order_by(CandidateEventRow.candidate_id)).scalars())
    for candidate in candidates:
        review = review_by_candidate.get(candidate.candidate_id)
        eligibility = promotion_eligibility(service, candidate.candidate_id, review)
        with service.repository.session_scope() as session:
            current = session.get(CandidateEventRow, candidate.candidate_id)
            source_item = session.get(SourceItemRow, current.source_item_id)
            metadata = dict(current.metadata_json or {})
            event_id = f"event_{stable_json_hash(['Care Line', current.candidate_id])[:16]}"
            previews.append(
                {
                    "shadow_run_id": shadow_run_id,
                    "candidate_id": current.candidate_id,
                    "care_line_record_id": metadata.get("producer_record_id"),
                    "promotion_eligible": eligibility["eligible"],
                    "blocking_conditions": eligibility["blocking_conditions"],
                    "warnings": eligibility["warnings"],
                    "proposed_event_payload": {
                        "event_id": event_id,
                        "title": current.title,
                        "summary": current.summary,
                        "event_type": metadata.get("event_type"),
                        "announcement_date": metadata.get("announcement_date"),
                        "effective_date": metadata.get("effective_date"),
                        "published_date": current.published_at.isoformat() if current.published_at else None,
                        "status": current.event_status.value if hasattr(current.event_status, "value") else str(current.event_status),
                        "domain": current.domain.value if hasattr(current.domain, "value") else str(current.domain),
                    },
                    "evidence_links": [
                        {
                            "source_item_id": current.source_item_id,
                            "source_url": source_item.source_url if source_item else "",
                            "supporting_passage": source_item.supporting_passage if source_item else "",
                        }
                    ],
                    "healthcare_attributes": dict(metadata.get("healthcare_attributes") or {}),
                    "producer_provenance": {
                        "producer": metadata.get("producer"),
                        "producer_record_id": metadata.get("producer_record_id"),
                        "raw_payload_hash": metadata.get("raw_payload_hash"),
                    },
                    "promotion_review": review or {},
                }
            )
    return {"schema_version": PHASE5_SCHEMA_VERSION, "shadow_run_id": shadow_run_id, "promotion_previews": sorted(previews, key=lambda row: row["candidate_id"])}


def promote_candidate_test_only(service: UniversalEventService, candidate_id: str, promotion_review: Mapping[str, Any]) -> dict[str, Any]:
    eligibility = promotion_eligibility(service, candidate_id, promotion_review)
    if not eligibility["eligible"]:
        raise ValueError("candidate is not promotion eligible: " + ", ".join(eligibility["blocking_conditions"]))
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with service.repository.session_scope() as session:
        candidate = session.get(CandidateEventRow, candidate_id)
        metadata = dict(candidate.metadata_json or {})
        event_id = f"event_{stable_json_hash(['Care Line', candidate_id])[:16]}"
        mentions = list(session.execute(select(EntityMentionRow).where(EntityMentionRow.candidate_id == candidate_id)).scalars())
        entity_links = []
        for mention in mentions:
            decision = session.execute(
                select(EntityResolutionDecisionRow)
                .where(EntityResolutionDecisionRow.mention_id == mention.mention_id)
                .order_by(EntityResolutionDecisionRow.created_at.desc(), EntityResolutionDecisionRow.resolution_decision_id.desc())
            ).scalars().first()
            if decision and decision.decision_type in {"matched", "created_new", "corrected"}:
                entity_links.append(
                    {
                        "event_id": event_id,
                        "candidate_id": candidate_id,
                        "mention_id": mention.mention_id,
                        "resolution_decision_id": decision.resolution_decision_id,
                        "entity_kind": mention.entity_kind,
                        "entity_role": mention.mention_role,
                        "organization_id": decision.organization_id,
                        "location_id": decision.location_id,
                        "metadata_json": {"promotion_review": promotion_review.get("decision_reason")},
                    }
                )
    event = service.create_event(
        {
            "event_id": event_id,
            "candidate_id": candidate_id,
            "domain": EventDomain.HEALTHCARE_ACCESS,
            "title": candidate.title,
            "summary": candidate.summary,
            "status": EventStatus(metadata.get("event_status") or EventStatus.ANNOUNCED.value),
            "published_at": now,
            "metadata_json": {"promotion_review": dict(promotion_review), "producer": "Care Line"},
        },
        evidence=[
            {
                "event_id": event_id,
                "source_item_id": candidate.source_item_id,
                "role": EvidenceRole.PRIMARY,
                "evidence_strength": "reviewed_source",
                "is_primary_source": True,
                "supporting_passage": candidate.summary,
                "created_at": now,
                "metadata_json": {"candidate_id": candidate_id},
            }
        ],
        entity_links=entity_links,
    )
    attrs = dict(metadata.get("healthcare_attributes") or {})
    for key, value in sorted(attrs.items()):
        service.add_event_attribute(
            {
                "event_id": event.event_id,
                "domain": EventDomain.HEALTHCARE_ACCESS,
                "attribute_key": key,
                "value_json": value,
                "source_item_id": candidate.source_item_id,
                "created_at": now,
                "metadata_json": {"candidate_id": candidate_id},
            }
        )
    return {"event_id": event.event_id, "candidate_id": candidate_id, "created": True}


def readiness_decision(report: Mapping[str, Any]) -> dict[str, Any]:
    blockers = []
    if int(report.get("eligible_candidates") or 0) < 25:
        blockers.append("fewer_than_25_real_eligible_candidates")
    if int(report.get("mention_count") or 0) < 50:
        blockers.append("fewer_than_50_real_entity_mentions")
    if int(report.get("reviewed_mention_count") or 0) < min(50, int(report.get("mention_count") or 0)):
        blockers.append("required_human_review_sample_not_completed")
    if not report.get("calibration_generated"):
        blockers.append("calibration_metrics_missing")
    if not report.get("promotion_preview_deterministic"):
        blockers.append("promotion_preview_not_verified_deterministic")
    return {
        "decision": "READY FOR REVIEWED CARE LINE CANDIDATE PROMOTION" if not blockers else "NOT READY FOR REVIEWED CARE LINE CANDIDATE PROMOTION",
        "blocking_conditions": blockers,
    }


def write_phase5_quality(path_json: Path, path_md: Path, payload: Mapping[str, Any]) -> None:
    path_json.parent.mkdir(parents=True, exist_ok=True)
    path_json.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Care Line Universal Events Phase 5 Quality",
        "",
        f"- Readiness: `{payload.get('readiness', {}).get('decision')}`",
        f"- Eligible candidates: `{payload.get('eligible_candidates')}`",
        f"- Entity mentions: `{payload.get('mention_count')}`",
        f"- Reviewed mentions: `{payload.get('reviewed_mention_count')}`",
        "",
        "## Blocking Conditions",
    ]
    blockers = payload.get("readiness", {}).get("blocking_conditions") or []
    lines.extend([f"- `{item}`" for item in blockers] or ["- None"])
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def canonical_records_to_json(records: Iterable[CanonicalCareLineRecord]) -> str:
    return json.dumps([asdict(row) for row in records], indent=2, sort_keys=True, ensure_ascii=False)
