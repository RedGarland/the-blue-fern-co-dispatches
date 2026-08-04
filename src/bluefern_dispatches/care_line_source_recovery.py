from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bluefern_dispatches.care_line_record import CARE_LINE_EVENT_TYPES, SERVICE_LINES, stable_json_hash
from bluefern_dispatches.care_line_reviewed_export import refuse_public_or_pages_path
from bluefern_dispatches.story_dedupe import normalize_url


RECOVERY_SCHEMA_VERSION = "bluefern.care_line.source_recovery.v1"
REVIEW_SCHEMA_VERSION = "bluefern.care_line.source_recovery_review.v1"
DECISIONS_SCHEMA_VERSION = "bluefern.care_line.source_recovery_decisions.v1"
REVIEWED_SOURCE_SCHEMA_VERSION = "bluefern.care_line.reviewed_source.v1"
MANIFEST_SCHEMA_VERSION = "bluefern.care_line.reviewed_source_manifest.v1"
RECOVERY_VERSION = "care-line-source-recovery-phase9-v1"

RECOVERY_STATUSES = {
    "already_reviewed",
    "recoverable_from_local_fields",
    "requires_manual_source_lookup",
    "wrapper_only",
    "duplicate",
    "stale",
    "non_operational",
    "insufficient_evidence",
    "malformed",
}
PROPOSAL_STATES = {"proposed", "review_required", "approved", "rejected", "deferred", "unrecoverable"}
REVIEW_DECISIONS = {
    "approve_source",
    "replace_source",
    "reject_source",
    "defer",
    "mark_duplicate",
    "mark_stale",
    "mark_non_operational",
    "mark_unrecoverable",
}
PROVENANCE_TYPES = {
    "repository_structured_field",
    "repository_cross_artifact_join",
    "reviewer_supplied",
    "reviewer_corrected",
    "discovery_metadata",
    "unresolved",
}
NON_OPERATIONAL_PRESSURE_TYPES = {
    "coverage_disruption",
    "medicaid_access_pressure",
    "medical_debt_or_affordability",
    "hospital_closure",
}
PRESSURE_EVENT_MAP = {
    "clinic_access_strain": "facility_closure",
    "maternity_care_loss": "service_suspension",
    "service_line_cut": "service_closure",
    "er_crowding_or_diversion": "service_reduction",
    "pharmacy_access_pressure": "facility_closure",
    "public_health_capacity_cut": "capacity_reduction",
    "behavioral_health_access_strain": "service_reduction",
    "ambulance_or_ems_strain": "service_reduction",
    "specialty_care_delay": "service_reduction",
}
SERVICE_LINE_TERMS = {
    "labor": "labor_and_delivery",
    "maternity": "maternity",
    "emergency": "emergency_care",
    "pharmacy": "pharmacy",
    "clinic": "primary_care",
    "primary care": "primary_care",
    "urgent care": "urgent_care",
    "behavioral": "behavioral_health",
}


def deterministic_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_hash(parts)[:16]}"


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _bool(row: Mapping[str, Any], key: str) -> bool:
    return row.get(key) is True or str(row.get(key) or "").strip().lower() == "true"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write_path = _long_path(tmp)
    target_path = _long_path(path)
    write_path.write_text(content, encoding="utf-8")
    write_path.replace(target_path)


def _long_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\") or len(resolved) < 240:
        return path
    return Path("\\\\?\\" + resolved)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


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


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sources", "records", "reviewed_records"):
            if isinstance(payload.get(key), list):
                return [dict(row) for row in payload[key] if isinstance(row, dict)]
    return []


def is_wrapper_url(url: str) -> bool:
    value = normalize_url(url) or url.strip().lower()
    return "news.google.com/" in value or "google.com/rss/articles" in value or "google.com/read/" in value


def _source_fingerprint(row: Mapping[str, Any]) -> str:
    return stable_json_hash(
        {
            "source_record_id": _text(row, "source_record_id", "source_id"),
            "title": _text(row, "title"),
            "url": _text(row, "url", "canonical_url", "source_url"),
            "publisher": _text(row, "publisher", "source_name"),
            "published_at": _text(row, "published_at", "source_published_date"),
            "snippet": _text(row, "summary_or_snippet", "evidence_text"),
        }
    )


def _event_type(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "event_type", "universal_event_type", "healthcare_event_type")
    if explicit:
        return explicit
    return PRESSURE_EVENT_MAP.get(_text(row, "pressure_type"), "")


def _service_line(row: Mapping[str, Any]) -> str:
    explicit = _text(row, "service_line", "affected_service_line")
    if explicit:
        return explicit
    blob = " ".join(_text(row, key).lower() for key in ("title", "summary_or_snippet", "evidence_text", "pressure_type"))
    for term, value in SERVICE_LINE_TERMS.items():
        if term in blob:
            return value
    return ""


def _manual_records(repo_root: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted((repo_root / "data" / "dispatches" / "care-line" / "sources").glob("*/manual_sources.json")):
        for row in _rows(path):
            out.append({"path": path, "row": row})
    return out


def _reviewed_records(repo_root: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted((repo_root / "data" / "dispatches" / "care-line" / "reviewed").glob("*/reviewed_records.json")):
        for row in _rows(path):
            out.append({"path": path, "row": row})
    return out


def _reviewed_duplicate(row: Mapping[str, Any], reviewed: list[dict[str, Any]], manuals: list[dict[str, Any]]) -> tuple[bool, str]:
    record_id = _text(row, "source_record_id", "source_id")
    url = normalize_url(_text(row, "url", "canonical_url", "source_url"))
    title_key = (_text(row, "publisher").casefold(), _text(row, "published_at", "source_published_date")[:10], _text(row, "title").casefold())
    for item in [*reviewed, *manuals]:
        other = item["row"]
        if record_id and record_id == _text(other, "source_record_id", "producer_record_id"):
            return True, _text(other, "source_record_id", "producer_record_id")
        other_url = normalize_url(_text(other, "url", "source_url", "canonical_url"))
        if url and other_url and url == other_url and not is_wrapper_url(url):
            return True, _text(other, "source_record_id", "producer_record_id")
        other_key = (_text(other, "publisher", "source_publisher").casefold(), _text(other, "published_at", "source_publication_date")[:10], _text(other, "title", "source_title").casefold())
        if all(title_key) and title_key == other_key:
            return True, _text(other, "source_record_id", "producer_record_id")
    return False, ""


class RecoveryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_schema_version: str = RECOVERY_SCHEMA_VERSION
    discovery_record_id: str
    discovery_date: str
    discovery_file: str
    wrapper_url: str = ""
    headline: str = ""
    snippet: str = ""
    reported_publisher: str = ""
    reported_publication_date: str = ""
    proposed_canonical_url: str = ""
    proposed_canonical_publisher: str = ""
    proposed_source_title: str = ""
    proposed_source_publication_date: str = ""
    proposed_supporting_passage: str = ""
    proposed_event_type: str = ""
    proposed_service_line: str = ""
    proposed_facility: str = ""
    proposed_provider: str = ""
    proposed_geography: str = ""
    proposal_sources: dict[str, str] = Field(default_factory=dict)
    proposal_rules: list[str] = Field(default_factory=list)
    proposal_confidence: float = 0.0
    source_payload_fingerprint: str
    proposal_fingerprint: str = ""
    recovery_status: str
    review_status: Literal["proposed", "review_required", "approved", "rejected", "deferred", "unrecoverable"] = "review_required"
    reviewer: str = ""
    review_reason: str = ""

    @model_validator(mode="after")
    def validate_proposal(self) -> "RecoveryProposal":
        if self.recovery_schema_version != RECOVERY_SCHEMA_VERSION:
            raise ValueError("unsupported recovery_schema_version")
        if self.recovery_status not in RECOVERY_STATUSES:
            raise ValueError(f"unsupported recovery_status: {self.recovery_status}")
        if self.review_status not in PROPOSAL_STATES:
            raise ValueError(f"unsupported review_status: {self.review_status}")
        if not self.proposal_fingerprint:
            payload = self.model_dump(mode="json", exclude={"proposal_fingerprint", "reviewer", "review_reason", "review_status"})
            object.__setattr__(self, "proposal_fingerprint", stable_json_hash(payload))
        return self


def proposal_from_discovery(row: Mapping[str, Any], *, discovery_file: Path, repo_root: Path, reviewed: list[dict[str, Any]], manuals: list[dict[str, Any]]) -> RecoveryProposal:
    record_id = _text(row, "source_record_id", "source_id") or _stable_id("care-line-discovery", _source_fingerprint(row))
    duplicate, duplicate_of = _reviewed_duplicate(row, reviewed, manuals)
    wrapper_url = _text(row, "url", "canonical_url", "source_url")
    canonical = _text(row, "canonical_url", "source_url")
    canonical_present = bool(canonical and not is_wrapper_url(canonical))
    evidence = _text(row, "publisher_evidence_text", "canonical_evidence_text")
    event_type = _event_type(row)
    status = "duplicate" if duplicate else "stale" if _text(row, "freshness_status").lower() == "stale" else "non_operational" if _bool(row, "context_only") or _text(row, "pressure_type") in NON_OPERATIONAL_PRESSURE_TYPES else "recoverable_from_local_fields" if canonical_present and evidence else "wrapper_only" if is_wrapper_url(wrapper_url) else "requires_manual_source_lookup"
    proposal_sources = {
        "wrapper_url": "discovery_metadata",
        "headline": "discovery_metadata",
        "snippet": "discovery_metadata",
        "reported_publisher": "discovery_metadata",
    }
    if canonical_present:
        proposal_sources["proposed_canonical_url"] = "repository_structured_field"
    if evidence:
        proposal_sources["proposed_supporting_passage"] = "repository_structured_field"
    return RecoveryProposal(
        discovery_record_id=record_id,
        discovery_date=discovery_file.parent.name,
        discovery_file=_rel(discovery_file, repo_root),
        wrapper_url=wrapper_url,
        headline=_text(row, "title"),
        snippet=_text(row, "summary_or_snippet", "evidence_text"),
        reported_publisher=_text(row, "publisher", "source_name"),
        reported_publication_date=_text(row, "published_at", "source_published_date"),
        proposed_canonical_url=canonical if canonical_present else "",
        proposed_canonical_publisher=_text(row, "canonical_publisher", "source_publisher") if canonical_present else "",
        proposed_source_title=_text(row, "source_title", "title") if canonical_present else "",
        proposed_source_publication_date=_text(row, "source_published_date", "published_at"),
        proposed_supporting_passage=evidence,
        proposed_event_type=event_type,
        proposed_service_line=_service_line(row),
        proposed_geography=_text(row, "location_name", "state"),
        proposal_sources=proposal_sources,
        proposal_rules=["local_canonical_url_present"] if canonical_present else ["wrapper_rejected_as_evidence"],
        proposal_confidence=0.9 if canonical_present and evidence else 0.0,
        source_payload_fingerprint=_source_fingerprint(row),
        recovery_status=status,
        review_status="proposed" if status == "recoverable_from_local_fields" else "review_required",
        review_reason=duplicate_of,
    )


def discovery_inventory(repo_root: Path, *, date_from: str | None = None, date_to: str | None = None, max_records: int | None = None) -> dict[str, Any]:
    reviewed = _reviewed_records(repo_root)
    manuals = _manual_records(repo_root)
    allowed = set(_dates(date_from, date_to)) if date_from and date_to else None
    proposals: list[RecoveryProposal] = []
    for path in sorted((repo_root / "data" / "dispatches" / "care-line" / "sources").glob("*/discovered_sources.json")):
        if allowed is not None and path.parent.name not in allowed:
            continue
        for row in _rows(path):
            proposals.append(proposal_from_discovery(row, discovery_file=path, repo_root=repo_root, reviewed=reviewed, manuals=manuals))
            if max_records and len(proposals) >= max_records:
                break
        if max_records and len(proposals) >= max_records:
            break
    rows = [proposal.model_dump(mode="json") for proposal in proposals]
    counts = Counter(row["recovery_status"] for row in rows)
    wrapper_url_count = sum(1 for row in rows if is_wrapper_url(str(row.get("wrapper_url") or "")))
    canonical_url_count = sum(1 for row in rows if row.get("proposed_canonical_url"))
    evidence_text_count = sum(1 for row in rows if row.get("proposed_supporting_passage"))
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "recovery_version": RECOVERY_VERSION,
        "date_from": date_from or "",
        "date_to": date_to or "",
        "lead_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "wrapper_url_count": wrapper_url_count,
        "canonical_url_count": canonical_url_count,
        "evidence_text_count": evidence_text_count,
        "proposals": rows,
    }


def render_inventory_markdown(inventory: Mapping[str, Any]) -> str:
    lines = [
        "# Care Line Phase 9 Discovery Inventory",
        "",
        f"- Leads inventoried: `{inventory.get('lead_count')}`",
        f"- Status counts: `{json.dumps(inventory.get('status_counts') or {}, sort_keys=True)}`",
        "",
        "| discovery_record_id | date | reported_publisher | canonical_url_present | evidence_text_present | recovery_status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in inventory.get("proposals") or []:
        lines.append(
            f"| {row.get('discovery_record_id')} | {row.get('discovery_date')} | {row.get('reported_publisher')} | {bool(row.get('proposed_canonical_url'))} | {bool(row.get('proposed_supporting_passage'))} | {row.get('recovery_status')} |"
        )
    return "\n".join(lines) + "\n"


def review_package(inventory: Mapping[str, Any], *, sample_id: str) -> dict[str, Any]:
    items = []
    for row in inventory.get("proposals") or []:
        items.append(
            {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "sample_id": sample_id,
                "discovery_record_id": row["discovery_record_id"],
                "source_payload_fingerprint": row["source_payload_fingerprint"],
                "proposal_fingerprint": row["proposal_fingerprint"],
                "headline": row["headline"],
                "snippet": row["snippet"],
                "wrapper_url": row["wrapper_url"],
                "reported_publisher": row["reported_publisher"],
                "reported_publication_date": row["reported_publication_date"],
                "proposed_canonical_url": row["proposed_canonical_url"],
                "proposed_canonical_publisher": row["proposed_canonical_publisher"],
                "proposed_source_title": row["proposed_source_title"],
                "proposed_supporting_passage": row["proposed_supporting_passage"],
                "proposed_facility": row["proposed_facility"],
                "proposed_geography": row["proposed_geography"],
                "proposed_event_type": row["proposed_event_type"],
                "proposed_service_line": row["proposed_service_line"],
                "potential_duplicate_reviewed_records": [row["review_reason"]] if row.get("recovery_status") == "duplicate" and row.get("review_reason") else [],
                "recovery_warnings": _warnings(row),
                "required_reviewer_actions": _required_actions(row),
            }
        )
    return {"schema_version": REVIEW_SCHEMA_VERSION, "sample_id": sample_id, "review_items": items}


def decisions_template(review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "sample_id": review.get("sample_id"),
        "decisions": [
            {
                "discovery_record_id": item["discovery_record_id"],
                "expected_source_payload_fingerprint": item["source_payload_fingerprint"],
                "expected_proposal_fingerprint": item["proposal_fingerprint"],
                "decision": "",
                "reviewer": "",
                "reason": "",
                "canonical_url": item["proposed_canonical_url"],
                "publisher": item["proposed_canonical_publisher"],
                "source_title": item["proposed_source_title"],
                "supporting_passage": item["proposed_supporting_passage"],
                "event_type": item["proposed_event_type"],
                "service_line": item["proposed_service_line"],
                "facility_name": item["proposed_facility"],
                "provider_name": item["proposed_facility"],
                "location_name": item["proposed_geography"],
                "state": "",
            }
            for item in review.get("review_items") or []
        ],
    }


def _warnings(row: Mapping[str, Any]) -> list[str]:
    warnings = []
    if is_wrapper_url(str(row.get("wrapper_url") or "")):
        warnings.append("wrapper_url_not_evidence")
    if not row.get("proposed_canonical_url"):
        warnings.append("canonical_url_required")
    if not row.get("proposed_supporting_passage"):
        warnings.append("supporting_passage_required")
    if row.get("recovery_status") == "stale":
        warnings.append("stale_discovery_lead")
    return warnings


def _required_actions(row: Mapping[str, Any]) -> list[str]:
    if row.get("recovery_status") == "recoverable_from_local_fields":
        return ["approve_source_or_correct"]
    if row.get("recovery_status") == "duplicate":
        return ["mark_duplicate_or_review_separate_source"]
    if row.get("recovery_status") == "stale":
        return ["mark_stale_or_supply_current_evidence"]
    return ["supply_canonical_publisher_url_and_evidence_or_reject"]


def write_review_package(inventory: Mapping[str, Any], *, sample_id: str, output_dir: Path) -> dict[str, str]:
    review = review_package(inventory, sample_id=sample_id)
    paths = {
        "review_json": output_dir / f"{sample_id}.source-recovery-review.json",
        "review_md": output_dir / f"{sample_id}.source-recovery-review.md",
        "decisions_template": output_dir / f"{sample_id}.source-recovery-decisions-template.json",
    }
    _write_atomic(paths["review_json"], deterministic_json(review) + "\n")
    _write_atomic(paths["review_md"], render_review_markdown(review))
    _write_atomic(paths["decisions_template"], deterministic_json(decisions_template(review)) + "\n")
    return {key: str(path) for key, path in paths.items()}


def render_review_markdown(review: Mapping[str, Any]) -> str:
    lines = ["# Care Line Source Recovery Review", "", f"- Sample: `{review.get('sample_id')}`", f"- Items: `{len(review.get('review_items') or [])}`", ""]
    for item in review.get("review_items") or []:
        lines.append(f"- `{item['discovery_record_id']}` {item.get('headline')}")
    return "\n".join(lines) + "\n"


def _proposal_by_id(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["discovery_record_id"]): dict(row) for row in inventory.get("proposals") or []}


def validate_approval(proposal: Mapping[str, Any], decision: Mapping[str, Any]) -> None:
    if decision.get("expected_source_payload_fingerprint") != proposal.get("source_payload_fingerprint"):
        raise ValueError("stale recovery decision: source fingerprint changed")
    if decision.get("expected_proposal_fingerprint") != proposal.get("proposal_fingerprint"):
        raise ValueError("stale recovery decision: proposal fingerprint changed")
    reviewer = str(decision.get("reviewer") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    action = str(decision.get("decision") or "").strip()
    if not reviewer or not reason:
        raise ValueError("reviewer and reason are required")
    if action not in REVIEW_DECISIONS:
        raise ValueError(f"unsupported recovery review decision: {action}")
    if action in {"approve_source", "replace_source"}:
        url = str(decision.get("canonical_url") or proposal.get("proposed_canonical_url") or "").strip()
        publisher = str(decision.get("publisher") or proposal.get("proposed_canonical_publisher") or "").strip()
        evidence = str(decision.get("supporting_passage") or proposal.get("proposed_supporting_passage") or "").strip()
        event_type = str(decision.get("event_type") or proposal.get("proposed_event_type") or "").strip()
        service_line = str(decision.get("service_line") or proposal.get("proposed_service_line") or "").strip()
        if not url or is_wrapper_url(url):
            raise ValueError("approved source requires a non-wrapper canonical URL")
        if not publisher:
            raise ValueError("approved source requires canonical publisher")
        if not evidence:
            raise ValueError("approved source requires supporting evidence")
        if event_type and event_type not in CARE_LINE_EVENT_TYPES and event_type not in {"financial_context", "resource_context", "workforce_context", "policy_context", "context_only"}:
            raise ValueError(f"unsupported event_type: {event_type}")
        if service_line and service_line not in SERVICE_LINES:
            raise ValueError(f"unsupported service_line: {service_line}")


def reviewed_source_from_decision(proposal: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    action = str(decision.get("decision") or "")
    reviewer = str(decision.get("reviewer") or "")
    reason = str(decision.get("reason") or "")
    url = str(decision.get("canonical_url") or proposal.get("proposed_canonical_url") or "")
    publisher = str(decision.get("publisher") or proposal.get("proposed_canonical_publisher") or "")
    title = str(decision.get("source_title") or proposal.get("proposed_source_title") or proposal.get("headline") or "")
    event_type = str(decision.get("event_type") or proposal.get("proposed_event_type") or "")
    service_line = str(decision.get("service_line") or proposal.get("proposed_service_line") or "")
    status = "reviewed" if action in {"approve_source", "replace_source"} else "rejected"
    public_status = "not_public" if action != "approve_source" else "public_approved"
    record_id = str(decision.get("producer_record_id") or proposal.get("discovery_record_id") or _stable_id("care-line-recovered", url, proposal.get("reported_publication_date")))
    return {
        "schema_version": REVIEWED_SOURCE_SCHEMA_VERSION,
        "producer": "Care Line",
        "source_record_id": record_id,
        "producer_record_id": record_id,
        "review_status": status,
        "care_line_review_status": status,
        "public_status": public_status,
        "source_url": url,
        "canonical_url": url,
        "url": url,
        "source_title": title,
        "title": title,
        "publisher": publisher,
        "publication_date": str(decision.get("publication_date") or proposal.get("proposed_source_publication_date") or proposal.get("reported_publication_date") or ""),
        "published_at": str(decision.get("publication_date") or proposal.get("proposed_source_publication_date") or proposal.get("reported_publication_date") or ""),
        "source_type": "recovered_source",
        "source_family": "recovered_source",
        "source_role": "clinic_operations_signal",
        "supporting_passage": str(decision.get("supporting_passage") or proposal.get("proposed_supporting_passage") or ""),
        "evidence_text": str(decision.get("supporting_passage") or proposal.get("proposed_supporting_passage") or ""),
        "effective_evidence_text": str(decision.get("supporting_passage") or proposal.get("proposed_supporting_passage") or ""),
        "evidence_provenance_type": "reviewer_transcribed",
        "evidence_valid_for_universal_event": True,
        "event_type": event_type,
        "universal_event_type": event_type,
        "service_line": service_line,
        "facility_name": str(decision.get("facility_name") or proposal.get("proposed_facility") or ""),
        "provider_name": str(decision.get("provider_name") or decision.get("facility_name") or proposal.get("proposed_provider") or proposal.get("proposed_facility") or ""),
        "location_name": str(decision.get("location_name") or proposal.get("proposed_geography") or ""),
        "city": str(decision.get("city") or ""),
        "county": str(decision.get("county") or ""),
        "state": str(decision.get("state") or ""),
        "country_code": str(decision.get("country_code") or "US"),
        "announcement_date": str(decision.get("announcement_date") or proposal.get("proposed_source_publication_date") or proposal.get("reported_publication_date") or ""),
        "effective_date": str(decision.get("effective_date") or ""),
        "permanence": str(decision.get("permanence") or "temporary_or_unknown"),
        "evidence_level": str(decision.get("evidence_level") or "reviewed_source"),
        "evidence_strength": str(decision.get("evidence_strength") or "reviewed"),
        "confidence": str(decision.get("evidence_strength") or "reviewed"),
        "is_primary_source": bool(decision.get("is_primary_source", False)),
        "included": action == "approve_source",
        "excluded": action != "approve_source",
        "qualifies_for_public_inclusion": action == "approve_source",
        "source_public_story_eligible": action == "approve_source",
        "pressure_signal": action == "approve_source",
        "context_only": action in {"mark_non_operational"},
        "duplicate_of_record_id": str(decision.get("duplicate_of_record_id") or ""),
        "supersedes_record_id": str(decision.get("supersedes_record_id") or ""),
        "is_withdrawn": bool(decision.get("is_withdrawn", False)),
        "discovery_provenance": {
            "discovery_record_id": proposal.get("discovery_record_id"),
            "discovery_date": proposal.get("discovery_date"),
            "discovery_file": proposal.get("discovery_file"),
            "wrapper_url": proposal.get("wrapper_url"),
            "headline": proposal.get("headline"),
            "snippet": proposal.get("snippet"),
            "source_payload_fingerprint": proposal.get("source_payload_fingerprint"),
            "proposal_fingerprint": proposal.get("proposal_fingerprint"),
        },
        "field_provenance": {
            "source_url": "reviewer_supplied" if action == "replace_source" else "repository_structured_field",
            "publisher": "reviewer_supplied" if action == "replace_source" else "repository_structured_field",
            "supporting_passage": "reviewer_supplied" if action == "replace_source" else "repository_structured_field",
            "headline": "discovery_metadata",
        },
        "reviewer": reviewer,
        "review_reason": reason,
    }


def merge_manual_pack(path: Path, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    existing = _rows(path)
    before_hash = stable_json_hash(existing)
    by_id = {_text(row, "source_record_id", "producer_record_id"): dict(row) for row in existing}
    for record in records:
        record_id = _text(record, "source_record_id", "producer_record_id")
        if not record_id:
            continue
        if record_id in by_id and stable_json_hash(by_id[record_id]) == stable_json_hash(record):
            continue
        by_id[record_id] = dict(record)
    merged = [by_id[key] for key in sorted(by_id)]
    _write_atomic(path, deterministic_json(merged) + "\n")
    after_hash = stable_json_hash(merged)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "path": str(path),
        "before_count": len(existing),
        "after_count": len(merged),
        "added_count": max(0, len(merged) - len(existing)),
        "before_hash": before_hash,
        "after_hash": after_hash,
        "record_ids": sorted(by_id),
    }


def import_review(inventory: Mapping[str, Any], decisions: Mapping[str, Any], *, output_root: Path, repo_root: Path, check_only: bool = False) -> dict[str, Any]:
    if decisions.get("schema_version") != DECISIONS_SCHEMA_VERSION:
        raise ValueError("unsupported recovery decisions schema")
    by_id = _proposal_by_id(inventory)
    accepted, rejected, errors = [], [], []
    writes: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions.get("decisions") or []:
        record_id = str(decision.get("discovery_record_id") or "")
        proposal = by_id.get(record_id)
        try:
            if proposal is None:
                raise ValueError(f"unknown discovery_record_id: {record_id}")
            validate_approval(proposal, decision)
            action = str(decision.get("decision") or "")
            if action in {"approve_source", "replace_source", "mark_non_operational"}:
                record = reviewed_source_from_decision(proposal, decision)
                date_key = str(decision.get("source_pack_date") or proposal.get("discovery_date") or "")
                writes.setdefault(date_key, []).append(record)
                accepted.append({"discovery_record_id": record_id, "decision": action, "source_record_id": record["source_record_id"]})
            else:
                rejected.append({"discovery_record_id": record_id, "decision": action})
        except Exception as exc:  # noqa: BLE001
            errors.append({"discovery_record_id": record_id, "error": f"{type(exc).__name__}: {exc}"})
    manifests = []
    if not check_only and not errors:
        refuse_public_or_pages_path(output_root if output_root.is_absolute() else repo_root / output_root, repo_root)
        root = output_root if output_root.is_absolute() else repo_root / output_root
        for date_key, records in sorted(writes.items()):
            manifests.append(merge_manual_pack(root / date_key / "manual_sources.json", records))
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "accepted": accepted if not errors else [],
        "rejected": rejected,
        "errors": errors,
        "check_only": check_only,
        "write_manifests": manifests,
    }


def source_quality_metrics(inventory: Mapping[str, Any], import_result: Mapping[str, Any]) -> dict[str, Any]:
    proposals = list(inventory.get("proposals") or [])
    statuses = Counter(row.get("recovery_status") for row in proposals)
    accepted = list(import_result.get("accepted") or [])
    lead_count = len(proposals)
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "leads_inventoried": lead_count,
        "leads_reviewed": len(accepted) + len(import_result.get("rejected") or []) + len(import_result.get("errors") or []),
        "canonical_urls_recovered": len(accepted),
        "canonical_publishers_recovered": len(accepted),
        "evidence_passages_recovered": len(accepted),
        "wrapper_like_discovery_rate": round(sum(1 for row in proposals if is_wrapper_url(str(row.get("wrapper_url") or ""))) / max(1, lead_count), 4),
        "canonical_url_available_rate": round(sum(1 for row in proposals if row.get("proposed_canonical_url")) / max(1, lead_count), 4),
        "evidence_text_available_rate": round(sum(1 for row in proposals if row.get("proposed_supporting_passage")) / max(1, lead_count), 4),
        "wrapper_only_rejection_rate": round(statuses.get("wrapper_only", 0) / max(1, lead_count), 4),
        "duplicate_rate": round(statuses.get("duplicate", 0) / max(1, lead_count), 4),
        "stale_rate": round(statuses.get("stale", 0) / max(1, lead_count), 4),
        "non_operational_rate": round(statuses.get("non_operational", 0) / max(1, lead_count), 4),
        "unrecoverable_rate": round(statuses.get("wrapper_only", 0) / max(1, lead_count), 4),
        "deferred_rate": 0.0,
        "reviewed_source_records_created": len(accepted),
        "repository_local_recovery_rate": round(sum(1 for row in proposals if row.get("recovery_status") == "recoverable_from_local_fields") / max(1, lead_count), 4),
        "reviewer_supplied_url_rate": 0.0,
        "discovery_only_field_rate": 1.0 if lead_count else 0.0,
        "unresolved_field_rate": round((lead_count - len(accepted)) / max(1, lead_count), 4),
    }


def comparison_from_phase8(import_result: Mapping[str, Any], canonical_ready: int = 0, candidates: int = 0, mentions: int = 0) -> dict[str, Any]:
    accepted = list(import_result.get("accepted") or [])
    lost = Counter(str(row.get("decision") or "") for row in import_result.get("rejected") or [])
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "new_reviewed_dates": sorted({str((manifest.get("path") or "")).split("\\")[-2] for manifest in import_result.get("write_manifests") or [] if manifest.get("path")}),
        "new_source_packs": len(import_result.get("write_manifests") or []),
        "new_canonical_source_urls": len(accepted),
        "new_reviewed_source_records": len(accepted),
        "new_canonical_reviewed_records": 0,
        "new_ue_ready_records": canonical_ready,
        "new_candidates": candidates,
        "new_mentions": mentions,
        "new_canonical_entities": 0,
        "new_match_candidates": 0,
        "new_effective_decisions": 0,
        "new_promotion_eligible_candidates": 0,
        "lost_records": dict(sorted(lost.items())),
    }


def run_recovery(repo_root: Path, *, date_from: str | None, date_to: str | None, max_records: int, report_dir: Path, review_dir: Path, check_only: bool = False) -> dict[str, Any]:
    refuse_public_or_pages_path(report_dir if report_dir.is_absolute() else repo_root / report_dir, repo_root)
    refuse_public_or_pages_path(review_dir if review_dir.is_absolute() else repo_root / review_dir, repo_root)
    inventory = discovery_inventory(repo_root, date_from=date_from, date_to=date_to, max_records=max_records)
    sample_id = f"care_line_phase9_{date_from or 'all'}_{date_to or 'all'}_{_hash([inventory.get('lead_count'), inventory.get('status_counts')])[:12]}"
    report_root = report_dir if report_dir.is_absolute() else repo_root / report_dir
    review_root = review_dir if review_dir.is_absolute() else repo_root / review_dir
    report_root.mkdir(parents=True, exist_ok=True)
    review_root.mkdir(parents=True, exist_ok=True)
    docs_path = repo_root / "docs" / "care-line-phase9-discovery-inventory.md"
    if not check_only:
        _write_atomic(docs_path, render_inventory_markdown(inventory))
    paths = write_review_package(inventory, sample_id=sample_id, output_dir=review_root) if not check_only else {}
    inventory_path = report_root / f"{sample_id}.discovery-inventory.json"
    summary_path = report_root / f"{sample_id}.summary.json"
    summary = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "sample_id": sample_id,
        "inventory": inventory,
        "review_paths": paths,
        "readiness": {"decision": "NOT READY FOR CARE LINE ENTITY CALIBRATION", "reason": "source recovery review decisions have not produced enough approved reviewed sources"},
    }
    if not check_only:
        _write_atomic(inventory_path, deterministic_json(inventory) + "\n")
        _write_atomic(summary_path, deterministic_json(summary) + "\n")
    summary["paths"] = {"inventory": str(inventory_path), "summary": str(summary_path), **paths}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover Care Line discovery leads into reviewed source packs.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--max-records", type=int, default=359)
    parser.add_argument("--report-dir", default="data/universal_events/shadow/care-line/phase9-reports")
    parser.add_argument("--review-dir", default="data/universal_events/shadow/care-line/phase9-reviews")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--import-review", default="")
    parser.add_argument("--output-root", default="data/dispatches/care-line/sources")
    parser.add_argument("--report", default="")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        if args.import_review:
            inventory = _load_json(Path(args.inventory))
            decisions = _load_json(Path(args.import_review))
            result = import_review(inventory, decisions, output_root=Path(args.output_root), repo_root=repo_root, check_only=args.check_only)
            if args.report:
                _write_atomic(Path(args.report), deterministic_json(result) + "\n")
        else:
            result = run_recovery(repo_root, date_from=args.date_from or None, date_to=args.date_to or None, max_records=args.max_records, report_dir=Path(args.report_dir), review_dir=Path(args.review_dir), check_only=args.check_only)
        print(deterministic_json(result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
