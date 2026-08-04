from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from bluefern_dispatches.care_line_normalize import NORMALIZER_VERSION, load_source_records, normalize_historical_records, write_review_package
from bluefern_dispatches.care_line_record import SCHEMA_VERSION, CareLineReviewedRecord, deterministic_records_json, stable_json_hash
from bluefern_dispatches.care_line_reviewed_export import EXPORTER_VERSION, export_records_for_date, refuse_public_or_pages_path
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import ADAPTER_VERSION
from bluefern_dispatches.universal_events.adapters.care_line_phase5 import calibration_metrics, promotion_eligibility, threshold_evaluation
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    BOOTSTRAP_DECISIONS_SCHEMA_VERSION,
    ENTITY_REVIEW_DECISIONS_SCHEMA_VERSION,
    OPERATOR_VERSION as ACCUMULATION_OPERATOR_VERSION,
    build_bootstrap_review,
    build_entity_review,
    discover_reviewed_files,
    effective_records,
    import_bootstrap_decisions,
    import_entity_review_decisions,
    load_reviewed_records,
    regenerate_matches,
    run_accumulation,
    write_post_review_reports,
)
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventRow,
    LocationRow,
    OrganizationRow,
    SourceItemRow,
)
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION, ResolverThresholds


PHASE8_SCHEMA_VERSION = "bluefern.care_line.phase8.v1"
PHASE8_OPERATOR_VERSION = "care-line-phase8-v1"
MAX_DATES = 365
MAX_RECORDS = 500


def _json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def deterministic_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _dates(date_from: str, date_to: str) -> list[str]:
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date-to cannot be before date-from")
    out = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    if len(out) > MAX_DATES:
        raise ValueError(f"Phase 8 refuses unrestricted historical processing over {MAX_DATES} dates")
    return out


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _load_reviewed_payload(path: Path) -> list[CareLineReviewedRecord]:
    if not path.exists():
        return []
    return load_reviewed_records(path)


def _source_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _read_json(path)
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sources", "records", "claims", "reviewed_records"):
            if isinstance(payload.get(key), list):
                return [dict(row) for row in payload[key] if isinstance(row, dict)]
    return []


def _record_status_counts(records: Iterable[CareLineReviewedRecord]) -> Counter[str]:
    return Counter(row.universal_event_status for row in records)


def _event_types_from_rows(rows: Iterable[Mapping[str, Any]], reviewed: Iterable[CareLineReviewedRecord]) -> list[str]:
    values = {_text(row, "event_type", "universal_event_type", "healthcare_event_type", "pressure_type") for row in rows}
    values.update(row.event_type for row in reviewed)
    return sorted(value for value in values if value)


def _service_lines_from_rows(rows: Iterable[Mapping[str, Any]], reviewed: Iterable[CareLineReviewedRecord]) -> list[str]:
    values = {_text(row, "service_line", "affected_service_line") for row in rows}
    values.update(row.service_line for row in reviewed)
    return sorted(value for value in values if value)


def source_inventory(repo_root: Path, *, date_from: str, date_to: str) -> dict[str, Any]:
    rows = []
    for value in _dates(date_from, date_to):
        manual = repo_root / "data" / "dispatches" / "care-line" / "sources" / value / "manual_sources.json"
        discovered = repo_root / "data" / "dispatches" / "care-line" / "sources" / value / "discovered_sources.json"
        reviewed_path = repo_root / "data" / "dispatches" / "care-line" / "reviewed" / value / "reviewed_records.json"
        manual_rows = _source_rows(manual)
        discovered_rows = _source_rows(discovered)
        reviewed_records = _load_reviewed_payload(reviewed_path)
        reviewable_count = max(len(manual_rows), len(reviewed_records))
        counts = _record_status_counts(reviewed_records)
        source_like = [*manual_rows, *discovered_rows]
        rows.append(
            {
                "date": value,
                "manual_source_file_present": manual.exists(),
                "reviewed_record_file_present": reviewed_path.exists(),
                "discovered_source_file_present": discovered.exists(),
                "manual_record_count": len(manual_rows),
                "canonical_record_count": len(reviewed_records),
                "discovered_record_count": len(discovered_rows),
                "reviewable_record_count": reviewable_count,
                "universal_event_ready_count": counts.get("universal_event_ready", 0),
                "care_line_only_count": counts.get("care_line_only", 0),
                "excluded_count": counts.get("excluded", 0),
                "potentially_recoverable_count": counts.get("needs_normalization_review", 0),
                "publishers": sorted({_text(row, "publisher", "source_name") for row in source_like if _text(row, "publisher", "source_name")}),
                "states": sorted({_text(row, "state") for row in source_like if _text(row, "state")}),
                "event_types": _event_types_from_rows(source_like, reviewed_records),
                "service_lines": _service_lines_from_rows(source_like, reviewed_records),
            }
        )
    aggregate = {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "date_from": date_from,
        "date_to": date_to,
        "dates_examined": len(rows),
        "manual_source_file_count": sum(1 for row in rows if row["manual_source_file_present"]),
        "reviewed_record_file_count": sum(1 for row in rows if row["reviewed_record_file_present"]),
        "discovered_source_file_count": sum(1 for row in rows if row["discovered_source_file_present"]),
        "manual_record_count": sum(row["manual_record_count"] for row in rows),
        "canonical_record_count": sum(row["canonical_record_count"] for row in rows),
        "discovered_record_count": sum(row["discovered_record_count"] for row in rows),
        "reviewable_record_count": sum(row["reviewable_record_count"] for row in rows),
        "universal_event_ready_count": sum(row["universal_event_ready_count"] for row in rows),
        "care_line_only_count": sum(row["care_line_only_count"] for row in rows),
        "excluded_count": sum(row["excluded_count"] for row in rows),
        "potentially_recoverable_count": sum(row["potentially_recoverable_count"] for row in rows),
        "publishers": sorted({value for row in rows for value in row["publishers"]}),
        "states": sorted({value for row in rows for value in row["states"]}),
        "event_types": sorted({value for row in rows for value in row["event_types"]}),
        "service_lines": sorted({value for row in rows for value in row["service_lines"]}),
    }
    return {"schema_version": PHASE8_SCHEMA_VERSION, "aggregate": aggregate, "dates": rows}


def render_inventory_markdown(inventory: Mapping[str, Any]) -> str:
    aggregate = dict(inventory.get("aggregate") or {})
    lines = [
        "# Care Line Phase 8 Source Expansion Inventory",
        "",
        f"- Date range: `{aggregate.get('date_from')}` to `{aggregate.get('date_to')}`",
        f"- Dates examined: `{aggregate.get('dates_examined')}`",
        f"- Manual source files: `{aggregate.get('manual_source_file_count')}`",
        f"- Reviewed record files: `{aggregate.get('reviewed_record_file_count')}`",
        f"- Discovery files: `{aggregate.get('discovered_source_file_count')}`",
        f"- Reviewable records: `{aggregate.get('reviewable_record_count')}`",
        f"- Canonical records: `{aggregate.get('canonical_record_count')}`",
        f"- UE-ready records: `{aggregate.get('universal_event_ready_count')}`",
        "",
        "| date | manual_source_file_present | reviewed_record_file_present | discovered_source_file_present | manual_record_count | canonical_record_count | reviewable_record_count | universal_event_ready_count | care_line_only_count | excluded_count | potentially_recoverable_count | publishers | states | event_types |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in inventory.get("dates") or []:
        if not (row.get("manual_source_file_present") or row.get("reviewed_record_file_present") or row.get("discovered_source_file_present")):
            continue
        lines.append(
            "| {date} | {manual} | {reviewed} | {discovered} | {manual_count} | {canonical_count} | {reviewable_count} | {ready_count} | {care_only} | {excluded} | {recoverable} | {publishers} | {states} | {event_types} |".format(
                date=row["date"],
                manual=row["manual_source_file_present"],
                reviewed=row["reviewed_record_file_present"],
                discovered=row["discovered_source_file_present"],
                manual_count=row["manual_record_count"],
                canonical_count=row["canonical_record_count"],
                reviewable_count=row["reviewable_record_count"],
                ready_count=row["universal_event_ready_count"],
                care_only=row["care_line_only_count"],
                excluded=row["excluded_count"],
                recoverable=row["potentially_recoverable_count"],
                publishers=", ".join(row["publishers"]),
                states=", ".join(row["states"]),
                event_types=", ".join(row["event_types"]),
            )
        )
    return "\n".join(lines) + "\n"


def select_sample(inventory: Mapping[str, Any], *, max_records: int = MAX_RECORDS) -> dict[str, Any]:
    dated = [
        row
        for row in (inventory.get("dates") or [])
        if row.get("manual_source_file_present") or row.get("reviewed_record_file_present")
    ]
    selected = []
    total = 0
    for row in dated:
        record_count = int(row.get("reviewable_record_count") or 0)
        if selected and total + record_count > max_records:
            break
        selected.append(row)
        total += record_count
    dates = [row["date"] for row in selected]
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "sample_id": _sample_id(dates, total),
        "selected_dates": dates,
        "date_from": dates[0] if dates else "",
        "date_to": dates[-1] if dates else "",
        "reviewable_record_count": total,
        "bounded": len(dates) <= MAX_DATES and total <= max_records,
        "selection_rationale": "Selected all locally available authoritative Care Line manual/canonical reviewed dates in the requested bounded range; discovery-only dates are inventoried but not admitted as reviewed evidence.",
        "limits": {"max_dates": MAX_DATES, "max_records": max_records},
    }


def _sample_id(dates: Iterable[str], count: int) -> str:
    values = list(dates)
    if not values:
        return "care_line_phase8_empty_sample"
    return f"care_line_phase8_{values[0]}_{values[-1]}_{_json_hash([values, count])[:12]}"


def batch_manifest(
    *,
    repo_root: Path,
    sample: Mapping[str, Any],
    inventory: Mapping[str, Any],
    reviewed_root: Path,
    max_records: int,
) -> dict[str, Any]:
    selected_dates = list(sample.get("selected_dates") or [])
    source_files = []
    reviewed_files = []
    input_hashes = {}
    record_counts = {}
    for value in selected_dates:
        manual = repo_root / "data" / "dispatches" / "care-line" / "sources" / value / "manual_sources.json"
        if manual.exists():
            source_files.append(manual)
            input_hashes[_rel(manual, repo_root)] = _json_hash(_read_json(manual))
        reviewed_path = (reviewed_root if reviewed_root.is_absolute() else repo_root / reviewed_root) / value / "reviewed_records.json"
        if reviewed_path.exists():
            reviewed_files.append(reviewed_path)
            input_hashes[_rel(reviewed_path, repo_root)] = _json_hash(_read_json(reviewed_path))
        inv = next((row for row in inventory.get("dates") or [] if row.get("date") == value), {})
        record_counts[value] = {
            "manual": int(inv.get("manual_record_count") or 0),
            "canonical": int(inv.get("canonical_record_count") or 0),
            "reviewable": int(inv.get("reviewable_record_count") or 0),
            "universal_event_ready": int(inv.get("universal_event_ready_count") or 0),
        }
    aggregate = dict(inventory.get("aggregate") or {})
    configuration = {
        "max_records": max_records,
        "max_dates": MAX_DATES,
        "shadow_only": True,
        "resolver_defaults": {
            "auto_match_threshold": ResolverThresholds.auto_match_threshold,
            "review_threshold": ResolverThresholds.review_threshold,
            "ambiguity_margin": ResolverThresholds.ambiguity_margin,
            "top_n": ResolverThresholds.top_n,
        },
    }
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "operator_version": PHASE8_OPERATOR_VERSION,
        "sample_id": sample.get("sample_id"),
        "date_from": sample.get("date_from"),
        "date_to": sample.get("date_to"),
        "selected_dates": selected_dates,
        "input_files": [_rel(path, repo_root) for path in source_files],
        "input_file_hashes": dict(sorted(input_hashes.items())),
        "reviewed_record_files": [_rel(path, repo_root) for path in reviewed_files],
        "record_counts_by_date": record_counts,
        "record_counts_by_status": {
            "universal_event_ready": aggregate.get("universal_event_ready_count", 0),
            "care_line_only": aggregate.get("care_line_only_count", 0),
            "excluded": aggregate.get("excluded_count", 0),
            "needs_normalization_review": aggregate.get("potentially_recoverable_count", 0),
        },
        "publishers": aggregate.get("publishers") or [],
        "states": aggregate.get("states") or [],
        "event_types": aggregate.get("event_types") or [],
        "service_lines": aggregate.get("service_lines") or [],
        "exporter_version": EXPORTER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "resolver_version": RESOLVER_VERSION,
        "accumulation_operator_version": ACCUMULATION_OPERATOR_VERSION,
        "configuration_hash": _json_hash(configuration),
    }


def write_normalization_review_packages(
    *,
    repo_root: Path,
    sample: Mapping[str, Any],
    review_dir: Path,
    max_records: int,
) -> dict[str, Any]:
    review_dir.mkdir(parents=True, exist_ok=True)
    paths: list[dict[str, str]] = []
    review_items = 0
    records_processed = 0
    for value in sample.get("selected_dates") or []:
        source_path = repo_root / "data" / "dispatches" / "care-line" / "sources" / value / "manual_sources.json"
        if not source_path.exists():
            continue
        source_rows = load_source_records(source_path)
        remaining = max_records - records_processed
        if remaining <= 0:
            break
        source_rows = source_rows[:remaining]
        records_processed += len(source_rows)
        sample_id = f"{sample.get('sample_id')}_{value}"
        normalized = normalize_historical_records(source_rows, input_path=source_path, sample_id=sample_id)
        written = write_review_package(review_dir / value, sample_id, normalized)
        paths.append({key: _rel(Path(path), repo_root) for key, path in written.items()})
        review_items += len(normalized.get("review_items") or [])
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "records_processed": records_processed,
        "review_item_count": review_items,
        "review_packages": paths,
    }


def _latest_decisions(service: UniversalEventService) -> tuple[list[EntityResolutionDecisionRow], list[EntityResolutionDecisionRow]]:
    with service.repository.session_scope() as session:
        rows = list(session.execute(select(EntityResolutionDecisionRow).order_by(EntityResolutionDecisionRow.created_at, EntityResolutionDecisionRow.resolution_decision_id)).scalars())
        latest: dict[str, EntityResolutionDecisionRow] = {}
        for row in rows:
            latest[row.mention_id] = row
        return rows, list(latest.values())


def _decision_calibration_rows(service: UniversalEventService) -> list[dict[str, Any]]:
    _, effective = _latest_decisions(service)
    rows = []
    with service.repository.session_scope() as session:
        for decision in effective:
            match = session.get(EntityMatchCandidateRow, decision.selected_match_candidate_id) if decision.selected_match_candidate_id else None
            all_matches = list(session.execute(select(EntityMatchCandidateRow).where(EntityMatchCandidateRow.mention_id == decision.mention_id).order_by(EntityMatchCandidateRow.rank)).scalars())
            top = all_matches[0] if all_matches else None
            rows.append(
                {
                    "decision_type": decision.decision_type,
                    "selected_rank": match.rank if match and decision.decision_type in {"matched", "corrected"} else None,
                    "was_automatic_match": bool(top and top.match_score >= ResolverThresholds.auto_match_threshold),
                    "review_group": "ambiguous" if len(all_matches) > 1 else "unresolved" if not all_matches or decision.decision_type == "deferred" else "automatic_matchable",
                    "top_score": top.match_score if top else 0.0,
                    "score_margin": (top.match_score - all_matches[1].match_score) if len(all_matches) > 1 else 1.0,
                    "identifier_conflict": bool(top and top.match_method == "authoritative_identifier_conflict"),
                    "administrative_region_conflict": False,
                    "health_system_facility_confusion": False,
                    "alias_collision": False,
                }
            )
    return rows


def entity_counts(service: UniversalEventService) -> dict[str, Any]:
    total_decisions, effective_decisions = _latest_decisions(service)
    with service.repository.session_scope() as session:
        mentions = list(session.execute(select(EntityMentionRow)).scalars())
        candidates = list(session.execute(select(CandidateEventRow)).scalars())
        events = list(session.execute(select(EventRow)).scalars())
        matches = list(session.execute(select(EntityMatchCandidateRow)).scalars())
        orgs = list(session.execute(select(OrganizationRow)).scalars())
        locations = list(session.execute(select(LocationRow)).scalars())
    decision_counts = Counter(row.decision_type for row in effective_decisions)
    total_decision_counts = Counter(row.decision_type for row in total_decisions)
    matchable = [mention for mention in mentions if any(match.mention_id == mention.mention_id and match.match_score >= ResolverThresholds.auto_match_threshold for match in matches)]
    ambiguous_or_unresolved = [
        mention
        for mention in mentions
        if len([match for match in matches if match.mention_id == mention.mention_id]) != 1
    ]
    return {
        "candidate_count": len(candidates),
        "event_count": len(events),
        "mention_count": len(mentions),
        "organization_mention_count": sum(1 for row in mentions if row.entity_kind == "organization"),
        "location_mention_count": sum(1 for row in mentions if row.entity_kind == "location"),
        "match_candidate_count": len(matches),
        "exact_or_high_confidence_matchable_mentions": len({row.mention_id for row in matchable}),
        "ambiguous_or_unresolved_before_final_review": len(ambiguous_or_unresolved),
        "canonical_organization_count": len(orgs),
        "canonical_location_count": len(locations),
        "canonical_entity_count": len(orgs) + len(locations),
        "total_decision_rows": len(total_decisions),
        "total_decision_counts": dict(sorted(total_decision_counts.items())),
        "effective_reviewed_mentions": len(effective_decisions),
        "effective_decision_counts": dict(sorted(decision_counts.items())),
        "matched_decision_count": decision_counts.get("matched", 0) + decision_counts.get("corrected", 0),
        "created_new_decision_count": decision_counts.get("created_new", 0),
        "rejected_match_decision_count": decision_counts.get("rejected_match", 0),
        "deferred_decision_count": decision_counts.get("deferred", 0),
        "corrected_decision_count": decision_counts.get("corrected", 0),
    }


def promotion_readiness_preview(service: UniversalEventService, *, run_id: str) -> dict[str, Any]:
    rows = []
    with service.repository.session_scope() as session:
        candidates = list(session.execute(select(CandidateEventRow).order_by(CandidateEventRow.candidate_id)).scalars())
    for candidate in candidates:
        eligibility = promotion_eligibility(service, candidate.candidate_id, None)
        with service.repository.session_scope() as session:
            current = session.get(CandidateEventRow, candidate.candidate_id)
            source_item = session.get(SourceItemRow, current.source_item_id) if current else None
            metadata = dict(current.metadata_json or {}) if current else {}
            mentions = list(session.execute(select(EntityMentionRow).where(EntityMentionRow.candidate_id == candidate.candidate_id).order_by(EntityMentionRow.mention_id)).scalars())
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "producer_record_id": metadata.get("producer_record_id"),
                "event_type": metadata.get("event_type"),
                "service_line": (metadata.get("healthcare_attributes") or {}).get("service_line_normalized") or (metadata.get("healthcare_attributes") or {}).get("service_line"),
                "facility": (metadata.get("healthcare_attributes") or {}).get("facility_name_raw"),
                "location": metadata.get("geographic_scope"),
                "announcement_date": metadata.get("announcement_date"),
                "effective_date": metadata.get("effective_date"),
                "evidence": source_item.supporting_passage if source_item else "",
                "source_items": [candidate.source_item_id],
                "required_mentions": [row.mention_id for row in mentions if row.mention_role in {"facility", "event_location", "new_owner"}],
                "effective_entity_decisions": eligibility.get("resolution_fingerprint"),
                "unresolved_blockers": [item for item in eligibility["blocking_conditions"] if item.startswith("unresolved_required_mention")],
                "stale_review_blockers": [item for item in eligibility["blocking_conditions"] if item.startswith("stale_")],
                "duplicate_blockers": [item for item in eligibility["blocking_conditions"] if "duplicate" in item],
                "withdrawal_blockers": [item for item in eligibility["blocking_conditions"] if "withdrawn" in item],
                "promotion_eligible": eligibility["eligible"],
                "promotion_blocking_reasons": eligibility["blocking_conditions"],
            }
        )
    counts = Counter(reason for row in rows for reason in row["promotion_blocking_reasons"])
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "run_id": run_id,
        "candidates": rows,
        "metrics": {
            "total_candidates": len(rows),
            "fully_reviewed_candidates": sum(1 for row in rows if not row["unresolved_blockers"]),
            "promotion_eligible_candidates": sum(1 for row in rows if row["promotion_eligible"]),
            "blocked_by_unresolved_organization": sum(count for reason, count in counts.items() if "facility" in reason or "new_owner" in reason),
            "blocked_by_unresolved_location": sum(count for reason, count in counts.items() if "event_location" in reason),
            "blocked_by_missing_evidence": counts.get("missing_evidence", 0) + counts.get("missing_traceable_source", 0),
            "blocked_by_date_uncertainty": counts.get("missing_date", 0),
            "blocked_by_duplicate_status": sum(count for reason, count in counts.items() if "duplicate" in reason),
            "blocked_by_withdrawal": sum(count for reason, count in counts.items() if "withdrawn" in reason),
            "blocked_by_stale_entity_decision": sum(count for reason, count in counts.items() if reason.startswith("stale_")),
            "blocked_by_unsupported_event_profile": counts.get("unsupported_event_type", 0),
            "blocked_by_missing_promotion_review": counts.get("promotion_review_not_approved", 0),
        },
    }


def readiness_assessment(counts: Mapping[str, Any], preview: Mapping[str, Any], calibration: Mapping[str, Any], threshold: Mapping[str, Any]) -> dict[str, Any]:
    blockers = []
    checks = {
        "at_least_25_real_ue_candidates": (int(counts.get("candidate_count") or 0), 25),
        "at_least_50_real_entity_mentions": (int(counts.get("mention_count") or 0), 50),
        "at_least_20_organization_mentions": (int(counts.get("organization_mention_count") or 0), 20),
        "at_least_20_location_mentions": (int(counts.get("location_mention_count") or 0), 20),
        "at_least_30_effective_reviewed_mentions": (int(counts.get("effective_reviewed_mentions") or 0), 30),
        "at_least_5_rejected_match_decisions": (int(counts.get("rejected_match_decision_count") or 0), 5),
        "at_least_3_deferred_decisions": (int(counts.get("deferred_decision_count") or 0), 3),
        "at_least_10_high_confidence_matchable_mentions": (int(counts.get("exact_or_high_confidence_matchable_mentions") or 0), 10),
        "at_least_5_canonical_entity_creations": (int(counts.get("created_new_decision_count") or 0), 5),
        "at_least_10_promotion_eligible_candidates": (int((preview.get("metrics") or {}).get("promotion_eligible_candidates") or 0), 10),
    }
    for name, (actual, required) in checks.items():
        if actual < required:
            blockers.append({"gate": name, "actual": actual, "required": required})
    viable_threshold = False
    for row in threshold.get("evaluations") or []:
        if row.get("precision") is not None and float(row["precision"]) >= 0.98:
            viable_threshold = True
    if not viable_threshold:
        blockers.append({"gate": "threshold_precision_at_least_0_98", "actual": None, "required": 0.98})
    if calibration.get("sample_label") == "calibration_ready" and blockers:
        blockers.append({"gate": "calibration_label_consistency", "actual": calibration.get("sample_label"), "required": "not calibration_ready unless gates pass"})
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "decision": "READY FOR REVIEWED CARE LINE CANDIDATE PROMOTION" if not blockers else "NOT READY FOR REVIEWED CARE LINE CANDIDATE PROMOTION",
        "blocking_thresholds": blockers,
    }


def difference_from_phase7(counts: Mapping[str, Any], calibration: Mapping[str, Any], threshold: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    phase7 = {
        "candidates": 2,
        "mentions": 12,
        "canonical_entities": 10,
        "match_candidates": 36,
        "effective_reviewed_mentions": 12,
        "rejected_matches": 3,
        "deferred_decisions": 0,
        "top_1_accuracy": 1.0,
        "top_3_recall": 1.0,
        "promotion_eligible_candidates": 0,
    }
    return {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "new_candidates": int(counts.get("candidate_count") or 0) - phase7["candidates"],
        "new_mentions": int(counts.get("mention_count") or 0) - phase7["mentions"],
        "new_canonical_entities": int(counts.get("canonical_entity_count") or 0) - phase7["canonical_entities"],
        "new_match_candidates": int(counts.get("match_candidate_count") or 0) - phase7["match_candidates"],
        "new_effective_reviewed_mentions": int(counts.get("effective_reviewed_mentions") or 0) - phase7["effective_reviewed_mentions"],
        "new_rejected_matches": int(counts.get("rejected_match_decision_count") or 0) - phase7["rejected_matches"],
        "new_deferred_decisions": int(counts.get("deferred_decision_count") or 0) - phase7["deferred_decisions"],
        "top_1_accuracy_change": None if calibration.get("top_1_candidate_accuracy") is None else round(float(calibration["top_1_candidate_accuracy"]) - phase7["top_1_accuracy"], 4),
        "top_3_recall_change": None if calibration.get("top_3_candidate_recall") is None else round(float(calibration["top_3_candidate_recall"]) - phase7["top_3_recall"], 4),
        "newly_promotion_eligible_candidates": int((preview.get("metrics") or {}).get("promotion_eligible_candidates") or 0) - phase7["promotion_eligible_candidates"],
        "threshold_recommendation": threshold.get("recommendation"),
    }


def run_phase8(
    *,
    repo_root: Path,
    date_from: str,
    date_to: str,
    reviewed_root: Path,
    database: Path,
    report_dir: Path,
    review_dir: Path,
    calibration_dir: Path,
    shadow: bool,
    max_records: int = MAX_RECORDS,
    check_only: bool = False,
    resume: bool = False,
    rerun: bool = False,
    normalization_review: bool = False,
    generate_bootstrap: bool = False,
    regenerate: bool = False,
    generate_entity_review: bool = False,
    post_review_reports: bool = False,
    promotion_readiness_preview_enabled: bool = False,
) -> dict[str, Any]:
    if not shadow and not check_only:
        raise ValueError("--shadow is required for Phase 8 database writes")
    if max_records > MAX_RECORDS:
        raise ValueError(f"Phase 8 refuses samples over {MAX_RECORDS} records")
    for path in (reviewed_root, database, report_dir, review_dir, calibration_dir):
        refuse_public_or_pages_path(path if path.is_absolute() else repo_root / path, repo_root)
    inventory = source_inventory(repo_root, date_from=date_from, date_to=date_to)
    sample = select_sample(inventory, max_records=max_records)
    if not sample["selected_dates"]:
        raise ValueError("no authoritative Care Line manual or reviewed records found in requested range")
    report_dir_abs = report_dir if report_dir.is_absolute() else repo_root / report_dir
    review_dir_abs = review_dir if review_dir.is_absolute() else repo_root / review_dir
    calibration_dir_abs = calibration_dir if calibration_dir.is_absolute() else repo_root / calibration_dir
    reviewed_root_abs = reviewed_root if reviewed_root.is_absolute() else repo_root / reviewed_root
    database_abs = database if database.is_absolute() else repo_root / database
    report_dir_abs.mkdir(parents=True, exist_ok=True)
    review_dir_abs.mkdir(parents=True, exist_ok=True)
    calibration_dir_abs.mkdir(parents=True, exist_ok=True)

    inventory_path = repo_root / "docs" / "care-line-phase8-source-expansion-inventory.md"
    inventory_path.write_text(render_inventory_markdown(inventory), encoding="utf-8")

    export_manifests = []
    for value in sample["selected_dates"]:
        reviewed_path = reviewed_root_abs / value / "reviewed_records.json"
        # Phase 8 reruns must preserve reviewed canonical versions. Re-export
        # only fills missing canonical days; it never overwrites an existing
        # reviewed-record file unless a future explicit replacement workflow is
        # added with separate review controls.
        if reviewed_path.exists():
            continue
        export_manifests.append(export_records_for_date(repo_root, value, output_root=reviewed_root_abs, check_only=check_only, reviewer="phase8-exporter", review_reason="Phase 8 bounded canonical export"))

    normalization = {"schema_version": PHASE8_SCHEMA_VERSION, "records_processed": 0, "review_item_count": 0, "review_packages": []}
    if normalization_review:
        normalization = write_normalization_review_packages(repo_root=repo_root, sample=sample, review_dir=review_dir_abs, max_records=max_records)

    manifest = batch_manifest(repo_root=repo_root, sample=sample, inventory=source_inventory(repo_root, date_from=date_from, date_to=date_to), reviewed_root=reviewed_root_abs, max_records=max_records)
    run_id = f"care_line_phase8_{_json_hash(manifest)[:16]}"
    accumulation = run_accumulation(
        repo_root=repo_root,
        reviewed_root=reviewed_root_abs,
        date_from=str(sample["date_from"]),
        date_to=str(sample["date_to"]),
        database=database_abs,
        report_dir=report_dir_abs,
        shadow=shadow,
        check_only=check_only,
    )
    if generate_bootstrap and not check_only:
        repo = SQLiteUniversalEventRepository(database_abs)
        repo.initialize_schema()
        service = UniversalEventService(repo)
        (review_dir_abs / f"{run_id}.entity-bootstrap-review.json").write_text(deterministic_json(build_bootstrap_review(service, run_id=run_id)) + "\n", encoding="utf-8")
        repo.close()
    if regenerate and not check_only:
        regenerate_matches(database_abs, shadow=shadow)
    if generate_entity_review and not check_only:
        repo = SQLiteUniversalEventRepository(database_abs)
        repo.initialize_schema()
        service = UniversalEventService(repo)
        (review_dir_abs / f"{run_id}.entity-review.json").write_text(deterministic_json(build_entity_review(service, run_id=run_id)) + "\n", encoding="utf-8")
        repo.close()
    if post_review_reports and not check_only:
        write_post_review_reports(database_abs, calibration_dir_abs, accumulation["run_id"], shadow=shadow)

    repo = SQLiteUniversalEventRepository(database_abs)
    if not check_only:
        repo.initialize_schema()
    service = UniversalEventService(repo)
    counts = entity_counts(service) if not check_only else {}
    calibration_rows = _decision_calibration_rows(service) if not check_only else []
    calibration = calibration_metrics(calibration_rows)
    if int(counts.get("candidate_count") or 0) >= 25 and int(counts.get("effective_reviewed_mentions") or 0) >= 30:
        calibration["sample_label"] = "calibration_ready"
    threshold = threshold_evaluation(
        calibration_rows,
        [
            {"auto_match_threshold": ResolverThresholds.auto_match_threshold, "ambiguity_margin": ResolverThresholds.ambiguity_margin},
            {"auto_match_threshold": 0.9, "ambiguity_margin": 0.08},
            {"auto_match_threshold": 0.92, "ambiguity_margin": 0.08},
            {"auto_match_threshold": 0.95, "ambiguity_margin": 0.1},
        ],
    )
    preview = promotion_readiness_preview(service, run_id=run_id) if promotion_readiness_preview_enabled and not check_only else {"schema_version": PHASE8_SCHEMA_VERSION, "run_id": run_id, "candidates": [], "metrics": {}}
    readiness = readiness_assessment(counts, preview, calibration, threshold) if not check_only else {"decision": "NOT READY FOR REVIEWED CARE LINE CANDIDATE PROMOTION", "blocking_thresholds": [{"gate": "check_only", "actual": True, "required": False}]}
    difference = difference_from_phase7(counts, calibration, threshold, preview) if not check_only else {}
    repo.close()

    result = {
        "schema_version": PHASE8_SCHEMA_VERSION,
        "operator_version": PHASE8_OPERATOR_VERSION,
        "run_id": run_id,
        "check_only": check_only,
        "inventory": inventory,
        "sample": sample,
        "manifest": manifest,
        "export_results": export_manifests,
        "normalization": normalization,
        "accumulation": accumulation,
        "counts": counts,
        "calibration": calibration,
        "threshold_evaluation": threshold,
        "promotion_readiness_preview": preview,
        "readiness": readiness,
        "difference_from_phase7": difference,
    }
    paths = {
        "manifest": report_dir_abs / f"{run_id}.manifest.json",
        "summary": report_dir_abs / f"{run_id}.summary.json",
        "inventory": report_dir_abs / f"{run_id}.source-inventory.json",
        "calibration": calibration_dir_abs / f"{run_id}.calibration.json",
        "threshold": calibration_dir_abs / f"{run_id}.threshold-evaluation.json",
        "promotion_preview": report_dir_abs / f"{run_id}.promotion-readiness-preview.json",
        "difference": report_dir_abs / f"{run_id}.difference-from-phase7.json",
    }
    for key, path in paths.items():
        payload = {
            "manifest": manifest,
            "summary": result,
            "inventory": inventory,
            "calibration": calibration,
            "threshold": threshold,
            "promotion_preview": preview,
            "difference": difference,
        }[key]
        path.write_text(deterministic_json(payload) + "\n", encoding="utf-8")
    result["paths"] = {key: _rel(path, repo_root) for key, path in paths.items()}
    return result


def import_normalization_review(*, source_path: Path, decisions_path: Path, output_path: Path, sample_id: str) -> dict[str, Any]:
    from bluefern_dispatches.care_line_normalize import import_review_decisions

    rows = load_source_records(source_path)
    return import_review_decisions(rows, input_path=source_path, decisions_path=decisions_path, output_path=output_path, sample_id=sample_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run guarded Phase 8 Care Line Universal Events shadow orchestration.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--date")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS)
    parser.add_argument("--reviewed-root", default="data/dispatches/care-line/reviewed")
    parser.add_argument("--database", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--review-dir", required=True)
    parser.add_argument("--calibration-dir", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--normalization-review", action="store_true")
    parser.add_argument("--import-normalization-review", default="")
    parser.add_argument("--import-normalization-source", default="")
    parser.add_argument("--import-normalization-output", default="")
    parser.add_argument("--import-normalization-sample-id", default="")
    parser.add_argument("--generate-bootstrap", action="store_true")
    parser.add_argument("--import-bootstrap", default="")
    parser.add_argument("--regenerate-matches", action="store_true")
    parser.add_argument("--generate-entity-review", action="store_true")
    parser.add_argument("--import-entity-review", default="")
    parser.add_argument("--post-review-reports", action="store_true")
    parser.add_argument("--promotion-readiness-preview", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        if args.date:
            date_from = args.date
            date_to = args.date
        else:
            if not args.date_from or not args.date_to:
                raise ValueError("explicit --date or --date-from/--date-to is required")
            date_from, date_to = args.date_from, args.date_to
        database = Path(args.database)
        if args.import_normalization_review:
            if not args.import_normalization_source or not args.import_normalization_output or not args.import_normalization_sample_id:
                raise ValueError("normalization import requires source, output, and sample-id")
            result = import_normalization_review(
                source_path=Path(args.import_normalization_source),
                decisions_path=Path(args.import_normalization_review),
                output_path=Path(args.import_normalization_output),
                sample_id=args.import_normalization_sample_id,
            )
        elif args.import_bootstrap:
            result = import_bootstrap_decisions(database if database.is_absolute() else repo_root / database, Path(args.import_bootstrap), shadow=args.shadow)
        elif args.import_entity_review:
            result = import_entity_review_decisions(database if database.is_absolute() else repo_root / database, Path(args.import_entity_review), shadow=args.shadow)
        elif args.regenerate_matches and not any([args.normalization_review, args.generate_bootstrap, args.generate_entity_review, args.post_review_reports, args.promotion_readiness_preview]):
            result = regenerate_matches(database if database.is_absolute() else repo_root / database, shadow=args.shadow)
        else:
            result = run_phase8(
                repo_root=repo_root,
                date_from=date_from,
                date_to=date_to,
                reviewed_root=Path(args.reviewed_root),
                database=database,
                report_dir=Path(args.report_dir),
                review_dir=Path(args.review_dir),
                calibration_dir=Path(args.calibration_dir),
                shadow=args.shadow,
                max_records=args.max_records,
                check_only=args.check_only,
                resume=args.resume,
                rerun=args.rerun,
                normalization_review=args.normalization_review,
                generate_bootstrap=args.generate_bootstrap,
                regenerate=args.regenerate_matches,
                generate_entity_review=args.generate_entity_review,
                post_review_reports=args.post_review_reports,
                promotion_readiness_preview_enabled=args.promotion_readiness_preview,
            )
        print(deterministic_json(result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
