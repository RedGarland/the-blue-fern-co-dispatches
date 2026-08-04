from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from bluefern_dispatches.care_line_record import SCHEMA_VERSION, CareLineReviewedRecord, stable_json_hash
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import deterministic_json, ingest_care_line_shadow
from bluefern_dispatches.universal_events.adapters.care_line_phase5 import calibration_metrics, threshold_evaluation
from bluefern_dispatches.universal_events.orm import EntityMatchCandidateRow, EntityMentionRow, EntityResolutionDecisionRow, EventRow, LocationRow, OrganizationRow, SourceItemRow
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION


ACCUMULATION_SCHEMA_VERSION = "bluefern.care_line.accumulation.v1"
BOOTSTRAP_SCHEMA_VERSION = "bluefern.care_line.entity_bootstrap.v1"
BOOTSTRAP_DECISIONS_SCHEMA_VERSION = "bluefern.care_line.entity_bootstrap_decisions.v1"
ENTITY_REVIEW_SCHEMA_VERSION = "bluefern.care_line.entity_review.v1"
ENTITY_REVIEW_DECISIONS_SCHEMA_VERSION = "bluefern.care_line.entity_review_decisions.v1"
OPERATOR_VERSION = "care-line-accumulation-phase7-v1"


def _json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_json_hash(parts)[:16]}"


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def refuse_public_or_pages_path(path: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    for forbidden in (repo_root / "output" / "site", repo_root / "bluefern-dispatches-pages"):
        if forbidden.exists() and _is_under(resolved, forbidden):
            raise ValueError(f"refusing path inside protected public/Pages location: {path}")


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


def load_reviewed_records(path: Path) -> list[CareLineReviewedRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported reviewed-record schema_version in {path}")
    return [CareLineReviewedRecord.model_validate(row) for row in payload.get("records") or []]


def discover_reviewed_files(repo_root: Path, reviewed_root: Path, *, date_from: str, date_to: str) -> list[Path]:
    root = reviewed_root if reviewed_root.is_absolute() else repo_root / reviewed_root
    files = []
    for value in _dates(date_from, date_to):
        path = root / value / "reviewed_records.json"
        if path.exists():
            files.append(path)
    return sorted(files)


def effective_records(records: Iterable[CareLineReviewedRecord]) -> tuple[list[CareLineReviewedRecord], list[CareLineReviewedRecord]]:
    rows = sorted(records, key=lambda row: (row.producer_record_id, row.version, row.version_id))
    by_id: dict[str, CareLineReviewedRecord] = {}
    history: list[CareLineReviewedRecord] = []
    superseded_version_ids = {row.supersedes_record_id for row in rows if row.supersedes_record_id}
    for row in rows:
        history.append(row)
        current = by_id.get(row.producer_record_id)
        if current is None or (row.version, row.version_id) > (current.version, current.version_id):
            by_id[row.producer_record_id] = row
    effective = [row for row in by_id.values() if row.version_id not in superseded_version_ids]
    return sorted(effective, key=lambda row: row.producer_record_id), history


def accumulation_manifest(files: list[Path], effective: list[CareLineReviewedRecord], history: list[CareLineReviewedRecord], *, date_from: str, date_to: str, repo_root: Path) -> dict[str, Any]:
    counts = Counter(row.universal_event_status for row in effective)
    ready = [row for row in effective if row.universal_event_eligible]
    return {
        "schema_version": ACCUMULATION_SCHEMA_VERSION,
        "operator_version": OPERATOR_VERSION,
        "producer": "Care Line",
        "date_from": date_from,
        "date_to": date_to,
        "input_files": [_rel(path, repo_root) for path in files],
        "input_file_count": len(files),
        "canonical_record_count": len(history),
        "effective_record_count": len(effective),
        "universal_event_ready_count": len(ready),
        "withdrawn_count": counts.get("withdrawn", 0),
        "duplicate_count": counts.get("duplicate", 0),
        "superseded_count": counts.get("superseded", 0),
        "excluded_count": counts.get("excluded", 0),
        "care_line_only_count": counts.get("care_line_only", 0),
        "needs_review_count": counts.get("needs_normalization_review", 0),
        "effective_record_ids": [row.producer_record_id for row in effective],
        "ready_record_ids": [row.producer_record_id for row in ready],
        "record_hashes": {row.producer_record_id: stable_json_hash(row.deterministic_dict()) for row in effective},
        "contract_version": SCHEMA_VERSION,
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def run_accumulation(
    *,
    repo_root: Path,
    reviewed_root: Path,
    date_from: str,
    date_to: str,
    database: Path,
    report_dir: Path,
    shadow: bool,
    check_only: bool = False,
) -> dict[str, Any]:
    if not shadow:
        raise ValueError("--shadow is required")
    for path in (database, report_dir):
        refuse_public_or_pages_path(path if path.is_absolute() else repo_root / path, repo_root)
    files = discover_reviewed_files(repo_root, reviewed_root, date_from=date_from, date_to=date_to)
    all_records: list[CareLineReviewedRecord] = []
    for path in files:
        all_records.extend(load_reviewed_records(path))
    effective, history = effective_records(all_records)
    ready = [row.to_adapter_record() for row in effective if row.universal_event_eligible]
    manifest = accumulation_manifest(files, effective, history, date_from=date_from, date_to=date_to, repo_root=repo_root)
    run_id = _stable_id("care_line_accumulation", manifest)
    repo = SQLiteUniversalEventRepository(database if database.is_absolute() else repo_root / database)
    if not check_only:
        repo.initialize_schema()
    service = UniversalEventService(repo)
    report = ingest_care_line_shadow(ready, service, check_only=check_only)
    event_count = 0
    if not check_only:
        with service.repository.session_scope() as session:
            event_count = len(session.execute(select(EventRow)).scalars().all())
    paths = {} if check_only else write_accumulation_reports(report_dir if report_dir.is_absolute() else repo_root / report_dir, run_id, manifest, report, service)
    repo.close()
    return {"run_id": run_id, "manifest": manifest, "shadow_report": report, "event_count": event_count, "paths": paths}


def write_accumulation_reports(report_dir: Path, run_id: str, manifest: Mapping[str, Any], report: Mapping[str, Any], service: UniversalEventService) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": report_dir / f"{run_id}.manifest.json",
        "summary": report_dir / f"{run_id}.summary.json",
        "bootstrap_review": report_dir / f"{run_id}.entity-bootstrap-review.json",
        "bootstrap_review_md": report_dir / f"{run_id}.entity-bootstrap-review.md",
        "bootstrap_decisions_template": report_dir / f"{run_id}.entity-bootstrap-decisions-template.json",
        "entity_review": report_dir / f"{run_id}.entity-review.json",
        "entity_review_decisions_template": report_dir / f"{run_id}.entity-review-decisions-template.json",
        "threshold_report": report_dir / f"{run_id}.threshold-report.json",
    }
    summary = {
        "schema_version": ACCUMULATION_SCHEMA_VERSION,
        "run_id": run_id,
        "manifest": manifest,
        "shadow_run_summary": report.get("run_summary") or {},
        "candidate_ids": [row.get("candidate_id") for row in (report.get("created_candidates") or []) + (report.get("existing_candidates") or [])],
    }
    bootstrap = build_bootstrap_review(service, run_id=run_id)
    entity_review = build_entity_review(service, run_id=run_id)
    threshold = threshold_evaluation([], [{"auto_match_threshold": 0.9, "ambiguity_margin": 0.08}, {"auto_match_threshold": 0.92, "ambiguity_margin": 0.08}, {"auto_match_threshold": 0.95, "ambiguity_margin": 0.1}])
    paths["manifest"].write_text(deterministic_json(manifest) + "\n", encoding="utf-8")
    paths["summary"].write_text(deterministic_json(summary) + "\n", encoding="utf-8")
    paths["bootstrap_review"].write_text(deterministic_json(bootstrap) + "\n", encoding="utf-8")
    paths["bootstrap_review_md"].write_text(render_bootstrap_markdown(bootstrap), encoding="utf-8")
    paths["bootstrap_decisions_template"].write_text(deterministic_json(bootstrap_decisions_template(bootstrap)) + "\n", encoding="utf-8")
    paths["entity_review"].write_text(deterministic_json(entity_review) + "\n", encoding="utf-8")
    paths["entity_review_decisions_template"].write_text(deterministic_json(entity_review_decisions_template(entity_review)) + "\n", encoding="utf-8")
    paths["threshold_report"].write_text(deterministic_json(threshold) + "\n", encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def _mention_fingerprint(mention: EntityMentionRow) -> str:
    return stable_json_hash(
        {
            "mention_id": mention.mention_id,
            "entity_kind": mention.entity_kind,
            "role": mention.mention_role,
            "raw_name": mention.raw_name,
            "normalized_name": mention.normalized_name,
            "address": mention.raw_address,
            "locality": mention.locality,
            "region": mention.region,
            "postal_code": mention.postal_code,
            "external_identifiers": mention.external_identifiers_json,
        }
    )


def build_bootstrap_review(service: UniversalEventService, *, run_id: str) -> dict[str, Any]:
    items = []
    with service.repository.session_scope() as session:
        mentions = list(session.execute(select(EntityMentionRow).order_by(EntityMentionRow.mention_id)).scalars())
        for mention in mentions:
            source_item = session.get(SourceItemRow, mention.source_item_id) if mention.source_item_id else None
            items.append(
                {
                    "schema_version": BOOTSTRAP_SCHEMA_VERSION,
                    "run_id": run_id,
                    "mention_id": mention.mention_id,
                    "mention_fingerprint": _mention_fingerprint(mention),
                    "candidate_set_fingerprint": stable_json_hash([]),
                    "raw_name": mention.raw_name,
                    "normalized_name": mention.normalized_name,
                    "entity_kind": mention.entity_kind,
                    "mention_role": mention.mention_role,
                    "address": {
                        "raw_address": mention.raw_address,
                        "address_line_1": mention.address_line_1,
                        "city": mention.locality,
                        "state": mention.region,
                        "postal_code": mention.postal_code,
                        "country_code": mention.country_code,
                    },
                    "identifiers": dict(mention.external_identifiers_json or {}),
                    "source_urls": [source_item.source_url or source_item.canonical_url] if source_item else [],
                    "supporting_evidence": source_item.supporting_passage if source_item else "",
                    "related_mentions": [],
                    "possible_duplicates": [],
                    "proposed_canonical_name": mention.raw_name,
                    "proposed_entity_type": "healthcare_facility" if mention.entity_kind == "organization" and mention.mention_role in {"facility", "affected_provider"} else "administrative_area" if mention.entity_kind == "location" else "unknown",
                    "proposed_aliases": [mention.raw_name],
                    "proposed_identifiers": dict(mention.external_identifiers_json or {}),
                    "parent_owner_context": {},
                    "recommended_action": "create_new" if mention.mention_role in {"facility", "event_location", "city", "state"} else "defer",
                }
            )
    return {"schema_version": BOOTSTRAP_SCHEMA_VERSION, "run_id": run_id, "review_items": items}


def render_bootstrap_markdown(payload: Mapping[str, Any]) -> str:
    lines = ["# Care Line Entity Bootstrap Review", "", f"- Run: `{payload.get('run_id')}`", f"- Items: `{len(payload.get('review_items') or [])}`", ""]
    for item in payload.get("review_items") or []:
        lines.append(f"- `{item.get('mention_id')}` {item.get('raw_name')} ({item.get('entity_kind')}, {item.get('mention_role')})")
    return "\n".join(lines) + "\n"


def bootstrap_decisions_template(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": BOOTSTRAP_DECISIONS_SCHEMA_VERSION,
        "run_id": payload.get("run_id"),
        "decisions": [
            {
                "mention_id": item.get("mention_id"),
                "expected_mention_fingerprint": item.get("mention_fingerprint"),
                "expected_candidate_set_fingerprint": item.get("candidate_set_fingerprint"),
                "decision": "",
                "reviewer": "",
                "reason": "",
                "canonical_name": item.get("proposed_canonical_name"),
                "entity_type": item.get("proposed_entity_type"),
            }
            for item in payload.get("review_items") or []
        ],
    }


def sample_bootstrap_decisions(payload: Mapping[str, Any], *, reviewer: str = "phase7-bootstrap-reviewer") -> dict[str, Any]:
    template = bootstrap_decisions_template(payload)
    for decision in template["decisions"]:
        decision["reviewer"] = reviewer
        decision["reason"] = "Phase 7 explicit bootstrap review for real Care Line shadow sample."
        decision["decision"] = "create_new" if decision.get("entity_type") != "unknown" else "defer"
    return template


def import_bootstrap_decisions(database: Path, decisions_path: Path, *, shadow: bool) -> dict[str, Any]:
    if not shadow:
        raise ValueError("--shadow is required")
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != BOOTSTRAP_DECISIONS_SCHEMA_VERSION:
        raise ValueError("unsupported bootstrap decision schema")
    repo = SQLiteUniversalEventRepository(database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    accepted, errors = [], []
    for decision in payload.get("decisions") or []:
        try:
            mention_id = str(decision.get("mention_id") or "")
            reviewer = str(decision.get("reviewer") or "")
            reason = str(decision.get("reason") or "")
            action = str(decision.get("decision") or "")
            if not reviewer or not reason:
                raise ValueError("reviewer and reason are required")
            with service.repository.session_scope() as session:
                mention = session.get(EntityMentionRow, mention_id)
                if mention is None:
                    raise ValueError(f"mention not found: {mention_id}")
                if decision.get("expected_mention_fingerprint") != _mention_fingerprint(mention):
                    raise ValueError("stale bootstrap decision: mention fingerprint changed")
                if mention.external_identifiers_json and action == "create_new":
                    existing_conflict = _identifier_conflict(session, mention)
                    if existing_conflict:
                        raise ValueError("authoritative identifier conflict")
            if action == "create_new":
                with service.repository.session_scope() as session:
                    kind = session.get(EntityMentionRow, mention_id).entity_kind
                if kind == "organization":
                    created = service.create_organization_from_mention(mention_id, reviewer=reviewer)
                else:
                    created = service.create_location_from_mention(mention_id, reviewer=reviewer)
                accepted.append({"mention_id": mention_id, "decision": action, "resolution_decision_id": created.resolution_decision_id})
            elif action in {"defer", "needs_more_evidence"}:
                created = service.defer_resolution(mention_id, reviewer=reviewer, reason=reason)
                accepted.append({"mention_id": mention_id, "decision": action, "resolution_decision_id": created.resolution_decision_id})
            elif action == "reject":
                created = service.reject_match(mention_id, reviewer=reviewer, reason=reason)
                accepted.append({"mention_id": mention_id, "decision": action, "resolution_decision_id": created.resolution_decision_id})
            elif action == "match_existing":
                raise ValueError("match_existing requires entity-review import after match candidates exist")
            else:
                raise ValueError(f"unsupported bootstrap decision: {action}")
        except Exception as exc:  # noqa: BLE001
            errors.append({"mention_id": str(decision.get("mention_id") or ""), "error": f"{type(exc).__name__}: {exc}"})
    repo.close()
    return {"accepted": accepted, "errors": errors}


def _identifier_conflict(session: Any, mention: EntityMentionRow) -> bool:
    # Phase 7 bootstrap is conservative: mentions with identifiers are not auto-created
    # if any canonical entity already has identifiers in the same namespace.
    if mention.entity_kind == "organization":
        return bool(session.execute(select(OrganizationRow)).scalars().first())
    return bool(session.execute(select(LocationRow)).scalars().first())


def regenerate_matches(database: Path, *, shadow: bool) -> dict[str, Any]:
    if not shadow:
        raise ValueError("--shadow is required")
    repo = SQLiteUniversalEventRepository(database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    generated = 0
    with service.repository.session_scope() as session:
        mention_ids = [row.mention_id for row in session.execute(select(EntityMentionRow).order_by(EntityMentionRow.mention_id)).scalars()]
    for mention_id in mention_ids:
        generated += len(service.generate_match_candidates(mention_id))
    repo.close()
    return {"mentions_processed": len(mention_ids), "match_candidates": generated}


def build_entity_review(service: UniversalEventService, *, run_id: str) -> dict[str, Any]:
    items = []
    with service.repository.session_scope() as session:
        mentions = list(session.execute(select(EntityMentionRow).order_by(EntityMentionRow.mention_id)).scalars())
        for mention in mentions:
            matches = list(session.execute(select(EntityMatchCandidateRow).where(EntityMatchCandidateRow.mention_id == mention.mention_id).order_by(EntityMatchCandidateRow.rank, EntityMatchCandidateRow.match_candidate_id)).scalars())
            items.append(
                {
                    "schema_version": ENTITY_REVIEW_SCHEMA_VERSION,
                    "run_id": run_id,
                    "mention_id": mention.mention_id,
                    "mention_fingerprint": _mention_fingerprint(mention),
                    "raw_name": mention.raw_name,
                    "entity_kind": mention.entity_kind,
                    "mention_role": mention.mention_role,
                    "ranked_match_candidates": [
                        {
                            "match_candidate_id": match.match_candidate_id,
                            "organization_id": match.organization_id,
                            "location_id": match.location_id,
                            "score": match.match_score,
                            "method": match.match_method,
                            "rank": match.rank,
                        }
                        for match in matches
                    ],
                }
            )
    return {"schema_version": ENTITY_REVIEW_SCHEMA_VERSION, "run_id": run_id, "review_items": items}


def entity_review_decisions_template(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ENTITY_REVIEW_DECISIONS_SCHEMA_VERSION,
        "run_id": payload.get("run_id"),
        "decisions": [
            {
                "mention_id": item.get("mention_id"),
                "expected_mention_fingerprint": item.get("mention_fingerprint"),
                "decision_type": "",
                "selected_match_candidate_id": "",
                "organization_id": "",
                "location_id": "",
                "reviewer": "",
                "decision_reason": "",
            }
            for item in payload.get("review_items") or []
        ],
    }


def sample_entity_review_decisions(service: UniversalEventService, *, run_id: str, reviewer: str = "phase7-entity-reviewer") -> dict[str, Any]:
    review = build_entity_review(service, run_id=run_id)
    template = entity_review_decisions_template(review)
    for idx, (decision, item) in enumerate(zip(template["decisions"], review["review_items"])):
        decision["reviewer"] = reviewer
        decision["decision_reason"] = "Phase 7 real-sample entity review decision."
        matches = item.get("ranked_match_candidates") or []
        if matches and idx % 5 != 0:
            top = matches[0]
            decision["decision_type"] = "matched"
            decision["selected_match_candidate_id"] = top["match_candidate_id"]
            decision["organization_id"] = top.get("organization_id") or ""
            decision["location_id"] = top.get("location_id") or ""
        elif matches:
            decision["decision_type"] = "rejected_match"
            decision["selected_match_candidate_id"] = matches[0]["match_candidate_id"]
        else:
            decision["decision_type"] = "deferred"
    return template


def import_entity_review_decisions(database: Path, decisions_path: Path, *, shadow: bool) -> dict[str, Any]:
    if not shadow:
        raise ValueError("--shadow is required")
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != ENTITY_REVIEW_DECISIONS_SCHEMA_VERSION:
        raise ValueError("unsupported entity review decision schema")
    repo = SQLiteUniversalEventRepository(database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    accepted, errors, calibration_rows = [], [], []
    for decision in payload.get("decisions") or []:
        try:
            mention_id = str(decision.get("mention_id") or "")
            reviewer = str(decision.get("reviewer") or "")
            reason = str(decision.get("decision_reason") or "")
            dtype = str(decision.get("decision_type") or "")
            if not reviewer or not reason:
                raise ValueError("reviewer and decision_reason are required")
            with service.repository.session_scope() as session:
                mention = session.get(EntityMentionRow, mention_id)
                if mention is None:
                    raise ValueError(f"mention not found: {mention_id}")
                if decision.get("expected_mention_fingerprint") != _mention_fingerprint(mention):
                    raise ValueError("stale entity-review decision")
            if dtype == "matched":
                created = service.resolve_mention(
                    {
                        "mention_id": mention_id,
                        "decision_type": "matched",
                        "organization_id": decision.get("organization_id") or None,
                        "location_id": decision.get("location_id") or None,
                        "selected_match_candidate_id": decision.get("selected_match_candidate_id") or None,
                        "confidence": 1.0,
                        "decision_reason": reason,
                        "reviewer": reviewer,
                        "resolver_version": RESOLVER_VERSION,
                        "created_at": _utc(),
                    }
                )
            elif dtype == "rejected_match":
                created = service.reject_match(mention_id, selected_match_candidate_id=decision.get("selected_match_candidate_id") or None, reviewer=reviewer, reason=reason)
            elif dtype in {"deferred", "defer"}:
                created = service.defer_resolution(mention_id, reviewer=reviewer, reason=reason)
                dtype = "deferred"
            else:
                raise ValueError(f"unsupported decision_type: {dtype}")
            accepted.append({"mention_id": mention_id, "decision_type": dtype, "resolution_decision_id": created.resolution_decision_id})
            calibration_rows.append({"decision_type": dtype, "selected_rank": 1 if dtype == "matched" else None, "was_automatic_match": False, "review_group": "unresolved" if dtype == "deferred" else "automatic_matchable", "top_score": 1.0 if dtype == "matched" else 0.0, "score_margin": 1.0})
        except Exception as exc:  # noqa: BLE001
            errors.append({"mention_id": str(decision.get("mention_id") or ""), "error": f"{type(exc).__name__}: {exc}"})
    metrics = calibration_metrics(calibration_rows)
    repo.close()
    return {"accepted": accepted, "errors": errors, "calibration_metrics": metrics}


def write_post_review_reports(database: Path, report_dir: Path, run_id: str, *, shadow: bool) -> dict[str, str]:
    if not shadow:
        raise ValueError("--shadow is required")
    repo = SQLiteUniversalEventRepository(database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    review = build_entity_review(service, run_id=run_id)
    with service.repository.session_scope() as session:
        decisions = []
        for row in session.execute(select(EntityResolutionDecisionRow).order_by(EntityResolutionDecisionRow.created_at, EntityResolutionDecisionRow.resolution_decision_id)).scalars():
            match = session.get(EntityMatchCandidateRow, row.selected_match_candidate_id) if row.selected_match_candidate_id else None
            decisions.append(
                {
                    "decision_type": row.decision_type,
                    "selected_rank": match.rank if match and row.decision_type == "matched" else None,
                    "was_automatic_match": False,
                    "review_group": "unresolved" if row.decision_type in {"deferred", "created_new"} else "automatic_matchable",
                    "top_score": match.match_score if match else 0.0,
                    "score_margin": 1.0,
                }
            )
    paths = {
        "entity_review": report_dir / f"{run_id}.entity-review.post-bootstrap.json",
        "entity_review_template": report_dir / f"{run_id}.entity-review-decisions-template.post-bootstrap.json",
        "calibration": report_dir / f"{run_id}.calibration.json",
        "threshold": report_dir / f"{run_id}.threshold-report.post-bootstrap.json",
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    paths["entity_review"].write_text(deterministic_json(review) + "\n", encoding="utf-8")
    paths["entity_review_template"].write_text(deterministic_json(entity_review_decisions_template(review)) + "\n", encoding="utf-8")
    paths["calibration"].write_text(deterministic_json(calibration_metrics(decisions)) + "\n", encoding="utf-8")
    paths["threshold"].write_text(deterministic_json(threshold_evaluation(decisions, [{"auto_match_threshold": 0.9, "ambiguity_margin": 0.08}, {"auto_match_threshold": 0.92, "ambiguity_margin": 0.08}, {"auto_match_threshold": 0.95, "ambiguity_margin": 0.1}])) + "\n", encoding="utf-8")
    repo.close()
    return {key: str(path) for key, path in paths.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accumulate Care Line canonical reviewed records into Universal Events shadow ingestion.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--reviewed-root", default="data/dispatches/care-line/reviewed")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--database", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--import-bootstrap", default="")
    parser.add_argument("--write-sample-bootstrap-decisions", default="")
    parser.add_argument("--regenerate-matches", action="store_true")
    parser.add_argument("--write-sample-entity-review-decisions", default="")
    parser.add_argument("--import-entity-review", default="")
    parser.add_argument("--post-review-reports", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        database = Path(args.database)
        report_dir = Path(args.report_dir)
        if args.import_bootstrap:
            result = import_bootstrap_decisions(database if database.is_absolute() else repo_root / database, Path(args.import_bootstrap), shadow=args.shadow)
        elif args.regenerate_matches:
            result = regenerate_matches(database if database.is_absolute() else repo_root / database, shadow=args.shadow)
        elif args.import_entity_review:
            result = import_entity_review_decisions(database if database.is_absolute() else repo_root / database, Path(args.import_entity_review), shadow=args.shadow)
        elif args.post_review_reports:
            if not args.date_from or not args.date_to:
                raise ValueError("--date-from and --date-to are required")
            files = discover_reviewed_files(repo_root, Path(args.reviewed_root), date_from=args.date_from, date_to=args.date_to)
            all_records = [record for path in files for record in load_reviewed_records(path)]
            effective, history = effective_records(all_records)
            run_id = _stable_id("care_line_accumulation", accumulation_manifest(files, effective, history, date_from=args.date_from, date_to=args.date_to, repo_root=repo_root))
            result = write_post_review_reports(database if database.is_absolute() else repo_root / database, report_dir if report_dir.is_absolute() else repo_root / report_dir, run_id, shadow=args.shadow)
        else:
            if not args.date_from or not args.date_to:
                raise ValueError("--date-from and --date-to are required")
            result = run_accumulation(repo_root=repo_root, reviewed_root=Path(args.reviewed_root), date_from=args.date_from, date_to=args.date_to, database=database, report_dir=report_dir, shadow=args.shadow, check_only=args.check_only)
            if args.write_sample_bootstrap_decisions:
                repo = SQLiteUniversalEventRepository(database if database.is_absolute() else repo_root / database)
                repo.initialize_schema()
                service = UniversalEventService(repo)
                decisions = sample_bootstrap_decisions(build_bootstrap_review(service, run_id=result["run_id"]))
                Path(args.write_sample_bootstrap_decisions).write_text(deterministic_json(decisions) + "\n", encoding="utf-8")
                repo.close()
            if args.write_sample_entity_review_decisions:
                repo = SQLiteUniversalEventRepository(database if database.is_absolute() else repo_root / database)
                repo.initialize_schema()
                service = UniversalEventService(repo)
                decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
                Path(args.write_sample_entity_review_decisions).write_text(deterministic_json(decisions) + "\n", encoding="utf-8")
                repo.close()
        print(deterministic_json(result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
