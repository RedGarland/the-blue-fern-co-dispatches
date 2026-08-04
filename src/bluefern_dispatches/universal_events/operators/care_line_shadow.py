from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import inspect, select

from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import (
    ADAPTER_VERSION,
    PRODUCER,
    deterministic_json,
    ingest_care_line_shadow,
)
from bluefern_dispatches.universal_events.adapters.care_line_phase5 import (
    ADMITTED_INPUT_TYPES,
    PHASE5_SCHEMA_VERSION,
    analyze_exclusions,
    build_bootstrap_review_artifact,
    calibration_metrics,
    canonical_records_to_json,
    detect_input_type,
    find_structured_sources,
    load_canonical_records,
    promotion_preview,
    readiness_decision,
    select_bounded_real_sample,
    threshold_evaluation,
    write_phase5_quality,
)
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventRow,
    LocationRow,
    OrganizationRow,
    ShadowIngestionExecutionRow,
    ShadowIngestionRecordResultRow,
    ShadowIngestionRunRow,
    SourceItemRow,
    utc_now,
)
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION, ResolverThresholds


MANIFEST_VERSION = "bluefern.care_line_shadow.manifest.v1"
REVIEW_SCHEMA_VERSION = "bluefern.care_line_shadow.review.v1"
CALIBRATION_SCHEMA_VERSION = "bluefern.entity_resolution.calibration.v1"
PHASE5_QUALITY_SCHEMA_VERSION = "bluefern.care_line.phase5.quality.v1"


def _json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_json_hash(parts)[:16]}"


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _dates(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("date-to cannot be before date-from")
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        rows = payload["sources"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        rows = payload["records"]
    else:
        raise ValueError(f"unsupported Care Line source JSON shape: {path}")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_output_path(path: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    forbidden = [
        repo_root / "bluefern-dispatches-pages",
        repo_root / "output" / "site",
        repo_root / "output" / "dispatches",
        repo_root / "assets",
        repo_root / "data" / "dispatches" / "care-line",
    ]
    for root in forbidden:
        if root.exists() and _is_under(resolved, root):
            raise ValueError(f"refusing shadow path inside protected output/input location: {path}")


def discover_inputs(
    repo_root: Path,
    *,
    date_value: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    input_paths: Iterable[Path] = (),
    input_types: Iterable[str] = ("manual-sources",),
    allow_future_date: bool = False,
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    today = date.today()
    files: dict[str, Path] = {}
    missing: list[str] = []
    if input_paths:
        for path in input_paths:
            files[path.resolve().as_posix()] = path
        start_text = date_from or date_value or ""
        end_text = date_to or date_value or start_text
    else:
        if date_value:
            start = end = _date(date_value)
        elif date_from and date_to:
            start, end = _date(date_from), _date(date_to)
        else:
            raise ValueError("explicit --date, --date-from/--date-to, or --input is required")
        if not allow_future_date and any(day > today for day in (start, end)):
            raise ValueError("future dates are refused without --allow-future-date")
        start_text, end_text = start.isoformat(), end.isoformat()
        type_to_filename = {
            "manual-sources": "manual_sources.json",
            "discovered-sources": "discovered_sources.json",
            "canonical-reviewed-records": "reviewed_records.json",
        }
        selected_types = tuple(input_types or ("manual-sources",))
        for input_type in selected_types:
            if input_type not in type_to_filename:
                raise ValueError(f"date discovery does not support input type: {input_type}")
        for day in _dates(start, end):
            found_for_day = False
            for input_type in selected_types:
                if input_type == "canonical-reviewed-records":
                    path = repo_root / "data" / "dispatches" / "care-line" / "reviewed" / day.isoformat() / type_to_filename[input_type]
                else:
                    path = repo_root / "data" / "dispatches" / "care-line" / "sources" / day.isoformat() / type_to_filename[input_type]
                if path.exists():
                    files[path.resolve().as_posix()] = path
                    found_for_day = True
            if not found_for_day:
                missing.append(day.isoformat())
    input_files: list[dict[str, Any]] = []
    for key in sorted(files):
        path = files[key]
        malformed = ""
        count = 0
        digest = ""
        try:
            input_type = detect_input_type(path)
            digest = _file_hash(path)
            count = len(_load_records(path))
        except Exception as exc:  # noqa: BLE001
            input_type = ""
            malformed = f"{type(exc).__name__}: {exc}"
        input_files.append(
            {
                "path": _relative_or_str(path, repo_root),
                "absolute_path": path.resolve().as_posix(),
                "input_type": input_type,
                "date": path.parent.name if path.parent.name[:4].isdigit() else "",
                "file_hash": digest,
                "record_count": count,
                "malformed_error": malformed,
            }
        )
    return input_files, missing, start_text, end_text


def build_input_manifest(
    repo_root: Path,
    *,
    date_value: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    input_paths: Iterable[Path] = (),
    input_types: Iterable[str] = ("manual-sources",),
    adapter_version: str = ADAPTER_VERSION,
    resolver_version: str = RESOLVER_VERSION,
    allow_future_date: bool = False,
) -> dict[str, Any]:
    input_files, missing, start_text, end_text = discover_inputs(
        repo_root,
        date_value=date_value,
        date_from=date_from,
        date_to=date_to,
        input_paths=input_paths,
        input_types=input_types,
        allow_future_date=allow_future_date,
    )
    return {
        "manifest_version": MANIFEST_VERSION,
        "producer": PRODUCER,
        "date_from": start_text,
        "date_to": end_text,
        "input_files": [{k: v for k, v in row.items() if k != "absolute_path"} for row in input_files],
        "input_types": sorted(set(input_types or ("manual-sources",))),
        "missing_dates": missing,
        "file_hashes": {row["path"]: row["file_hash"] for row in input_files},
        "record_counts": {row["path"]: row["record_count"] for row in input_files},
        "malformed_files": [{row["path"]: row["malformed_error"]} for row in input_files if row["malformed_error"]],
        "adapter_version": adapter_version,
        "resolver_version": resolver_version,
    }


def load_manifest_records(manifest: Mapping[str, Any], repo_root: Path, *, include_nonpublic_reviewed: bool = False, max_records: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in manifest.get("input_files") or []:
        if item.get("malformed_error"):
            continue
        path = repo_root / str(item["path"])
        input_type = str(item.get("input_type") or detect_input_type(path))
        for canonical in load_canonical_records(path, input_type):
            enriched = canonical.as_ingestion_record()
            enriched["_shadow_source_file"] = str(item["path"])
            enriched["_shadow_input_type"] = input_type
            if include_nonpublic_reviewed:
                enriched.setdefault("care_line_review_status", "approved")
            rows.append(enriched)
            if max_records is not None and len(rows) >= max_records:
                return rows
    return rows


def configuration_hash(options: Mapping[str, Any]) -> str:
    stable = {
        "strict": bool(options.get("strict")),
        "include_nonpublic_reviewed": bool(options.get("include_nonpublic_reviewed")),
        "adapter_version": options.get("adapter_version") or ADAPTER_VERSION,
        "resolver_version": options.get("resolver_version") or RESOLVER_VERSION,
    }
    return _json_hash(stable)


def shadow_run_id(manifest: Mapping[str, Any], config_hash: str) -> str:
    return _stable_id("shadow_run", PRODUCER, _json_hash(manifest), config_hash)


def execution_id(shadow_id: str, started_at: datetime) -> str:
    return _stable_id("execution", shadow_id, started_at.isoformat())


def _mention_fingerprint(row: EntityMentionRow | Mapping[str, Any]) -> str:
    if isinstance(row, EntityMentionRow):
        payload = {
            "mention_id": row.mention_id,
            "entity_kind": row.entity_kind,
            "raw_name": row.raw_name,
            "normalized_name": row.normalized_name,
            "mention_role": row.mention_role,
            "raw_address": row.raw_address,
            "address_line_1": row.address_line_1,
            "locality": row.locality,
            "region": row.region,
            "postal_code": row.postal_code,
            "external_identifiers": row.external_identifiers_json,
        }
    else:
        payload = dict(row)
    return _json_hash(payload)


def _report_paths(report_dir: Path, run_id: str) -> dict[str, Path]:
    return {
        "manifest": report_dir / f"{run_id}.manifest.json",
        "summary": report_dir / f"{run_id}.summary.json",
        "review": report_dir / f"{run_id}.review.json",
        "review_md": report_dir / f"{run_id}.review.md",
        "excluded": report_dir / f"{run_id}.excluded.json",
        "calibration": report_dir / f"{run_id}.calibration.jsonl",
        "diff": report_dir / f"{run_id}.diff.json",
        "entity_review_template": report_dir / f"{run_id}.entity-review-template.json",
        "entity_review_guide": report_dir / f"{run_id}.entity-review-guide.md",
        "phase5_quality_json": report_dir / f"{run_id}.phase5-quality.json",
        "phase5_quality_md": report_dir / f"{run_id}.phase5-quality.md",
        "promotion_preview": report_dir / f"{run_id}.promotion-preview.json",
        "threshold_report": report_dir / f"{run_id}.threshold-report.json",
        "source_inventory": report_dir / f"{run_id}.source-inventory.json",
        "canonical_records": report_dir / f"{run_id}.canonical-records.json",
    }


def _counts(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if value:
            out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def build_review_package(service: UniversalEventService, report: Mapping[str, Any], manifest: Mapping[str, Any], run_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    thresholds = ResolverThresholds()
    review_items: list[dict[str, Any]] = []
    with service.repository.session_scope() as session:
        mention_ids = {
            row.get("mention_id")
            for section in ("organization_mentions", "location_mentions", "unresolved_mentions")
            for row in (report.get(section) or [])
            if row.get("mention_id")
        }
        for mention_id in sorted(mention_ids):
            mention = session.get(EntityMentionRow, mention_id)
            if mention is None:
                continue
            matches = list(
                session.execute(
                    select(EntityMatchCandidateRow)
                    .where(EntityMatchCandidateRow.mention_id == mention_id)
                    .order_by(EntityMatchCandidateRow.rank, EntityMatchCandidateRow.match_candidate_id)
                ).scalars()
            )
            source_url = ""
            evidence_text = ""
            candidate = session.get(CandidateEventRow, mention.candidate_id)
            if candidate is not None:
                source_item = session.get(SourceItemRow, candidate.source_item_id)
                if source_item is not None:
                    source_url = source_item.source_url or source_item.canonical_url
                    evidence_text = source_item.supporting_passage
            top = matches[0] if matches else None
            second = matches[1] if len(matches) > 1 else None
            margin = round(float(top.match_score) - float(second.match_score), 4) if top and second else None
            group = "unresolved"
            if top and top.match_method == "exact_identifier":
                group = "exact_authoritative"
            elif top and top.match_score >= thresholds.auto_match_threshold and (margin is None or margin >= thresholds.ambiguity_margin):
                group = "automatic_matchable"
            elif len(matches) > 1:
                group = "ambiguous"
            review_items.append(
                {
                    "review_schema_version": REVIEW_SCHEMA_VERSION,
                    "shadow_run_id": run_id,
                    "mention_id": mention.mention_id,
                    "mention_fingerprint": _mention_fingerprint(mention),
                    "producer_record_id": next((r.get("producer_record_id") for r in (report.get("organization_mentions") or []) + (report.get("location_mentions") or []) if r.get("mention_id") == mention.mention_id), ""),
                    "candidate_id": mention.candidate_id,
                    "raw_mention": mention.raw_name,
                    "normalized_mention": mention.normalized_name,
                    "entity_kind": mention.entity_kind,
                    "mention_role": mention.mention_role,
                    "address": {
                        "raw_address": mention.raw_address,
                        "address_line_1": mention.address_line_1,
                        "locality": mention.locality,
                        "region": mention.region,
                        "postal_code": mention.postal_code,
                        "country_code": mention.country_code,
                    },
                    "external_identifiers": dict(mention.external_identifiers_json or {}),
                    "ranked_match_candidates": [
                        {
                            "match_candidate_id": match.match_candidate_id,
                            "organization_id": match.organization_id,
                            "location_id": match.location_id,
                            "score": match.match_score,
                            "match_method": match.match_method,
                            "features": dict(match.match_features_json or {}),
                            "rank": match.rank,
                        }
                        for match in matches
                    ],
                    "thresholds": {
                        "auto_match_threshold": thresholds.auto_match_threshold,
                        "review_threshold": thresholds.review_threshold,
                        "ambiguity_margin": thresholds.ambiguity_margin,
                    },
                    "review_status": "pending",
                    "reviewer_notes": "",
                    "suggested_action": "confirm_match" if group in {"exact_authoritative", "automatic_matchable"} else "review_required" if group == "ambiguous" else "create_or_defer",
                    "score_margin": margin,
                    "blocking_rules": [m.match_features_json.get("blocking_rule") for m in matches if isinstance(m.match_features_json, dict) and m.match_features_json.get("blocking_rule")],
                    "source_url": source_url,
                    "supporting_evidence_text": evidence_text,
                    "source_evidence_excerpt": evidence_text[:500],
                    "canonical_entity_summary": _canonical_entity_summary(matches),
                    "existing_aliases": [],
                    "existing_identifiers": [],
                    "existing_locations": [],
                    "parent_owner_context": {},
                    "related_care_line_records": [mention.candidate_id],
                    "match_score_explanation": _match_score_explanation(matches),
                    "previous_decisions": _previous_decisions(session, mention.mention_id),
                    "correction_history": [],
                    "recommended_reviewer_action": "confirm_match" if group in {"exact_authoritative", "automatic_matchable"} else "review_required" if group == "ambiguous" else "create_or_defer",
                    "review_group": group,
                }
            )
    summary = dict(report.get("run_summary") or {})
    eligible = list(report.get("eligible_records") or [])
    excluded = list(report.get("excluded_records") or [])
    candidates = list(report.get("created_candidates") or []) + list(report.get("existing_candidates") or [])
    summary_report = {
        "shadow_run_id": run_id,
        "run_summary": summary,
        "input_files": manifest.get("input_files") or [],
        "quality_metrics": quality_metrics(report),
        "counts_by_event_type": _counts(eligible, "event_type"),
        "counts_by_service_line": {},
        "counts_by_state": {},
        "counts_by_source_type": {},
        "counts_by_publisher": {},
        "counts_by_exclusion_reason": _counts(excluded, "reason"),
        "candidate_ids": sorted(row.get("candidate_id") for row in candidates if row.get("candidate_id")),
    }
    review_json = {"shadow_run_id": run_id, "review_items": review_items}
    review_md = render_review_markdown(review_json, excluded)
    return summary_report, review_json, review_md


def _canonical_entity_summary(matches: list[EntityMatchCandidateRow]) -> list[dict[str, Any]]:
    return [
        {
            "match_candidate_id": match.match_candidate_id,
            "target_id": match.organization_id or match.location_id,
            "entity_kind": match.entity_kind,
            "score": match.match_score,
            "method": match.match_method,
            "rank": match.rank,
        }
        for match in matches
    ]


def _match_score_explanation(matches: list[EntityMatchCandidateRow]) -> list[str]:
    return [
        f"rank {match.rank}: {match.match_method} score={match.match_score}"
        for match in matches
    ]


def _previous_decisions(session: Any, mention_id: str) -> list[dict[str, Any]]:
    decisions = list(
        session.execute(
            select(EntityResolutionDecisionRow)
            .where(EntityResolutionDecisionRow.mention_id == mention_id)
            .order_by(EntityResolutionDecisionRow.created_at, EntityResolutionDecisionRow.resolution_decision_id)
        ).scalars()
    )
    return [
        {
            "resolution_decision_id": row.resolution_decision_id,
            "decision_type": row.decision_type,
            "organization_id": row.organization_id,
            "location_id": row.location_id,
            "reviewer": row.reviewer,
            "decision_reason": row.decision_reason,
            "supersedes_decision_id": row.supersedes_decision_id,
        }
        for row in decisions
    ]


def quality_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    s = dict(report.get("run_summary") or {})
    total = int(s.get("input_record_count") or 0) or 1
    mentions = int(s.get("mention_count") or 0) or 1
    return {
        "ingestion": {
            "eligibility_rate": round(int(s.get("eligible_count") or 0) / total, 4),
            "exclusion_rate": round(int(s.get("excluded_count") or 0) / total, 4),
            "malformed_record_rate": round(int(s.get("error_count") or 0) / total, 4),
            "candidate_creation_rate": round(int(s.get("created_candidate_count") or 0) / total, 4),
            "idempotent_existing_rate": round(int(s.get("existing_candidate_count") or 0) / total, 4),
            "candidate_update_rate": round(sum(1 for row in report.get("existing_candidates") or [] if row.get("metadata_updated")) / total, 4),
        },
        "resolution": {
            "mentions_per_candidate": round(int(s.get("mention_count") or 0) / max(1, int(s.get("created_candidate_count") or 0) + int(s.get("existing_candidate_count") or 0)), 4),
            "candidate_matches_per_mention": round(int(s.get("match_candidate_count") or 0) / mentions, 4),
            "automatic_matchable_rate": round(int(s.get("automatically_matchable_mention_count") or 0) / mentions, 4),
            "ambiguity_rate": round(int(s.get("ambiguous_mention_count") or 0) / mentions, 4),
            "unresolved_rate": round(int(s.get("unresolved_mention_count") or 0) / mentions, 4),
            "blocking_rule_rate": 0.0,
        },
        "review": {
            "sample_label": "provisional_small_sample",
            "accepted_match_rate": None,
            "rejected_match_rate": None,
            "created_new_rate": None,
            "deferred_rate": None,
            "correction_rate": None,
        },
        "data_quality": {
            "missing_address_rate": None,
            "missing_county_rate": None,
            "missing_identifier_rate": None,
            "missing_effective_date_rate": None,
            "unknown_service_line_rate": None,
            "unknown_event_type_rate": None,
            "duplicate_producer_record_rate": None,
        },
    }


def render_review_markdown(review_json: Mapping[str, Any], excluded: list[Mapping[str, Any]]) -> str:
    groups = [
        ("exact_authoritative", "Exact authoritative matches"),
        ("automatic_matchable", "High-confidence automatic-matchable cases"),
        ("ambiguous", "Ambiguous cases"),
        ("unresolved", "Unresolved mentions"),
    ]
    items = list(review_json.get("review_items") or [])
    lines = ["# Care Line Shadow Review", ""]
    for key, title in groups:
        lines.extend([f"## {title}", ""])
        rows = [row for row in items if row.get("review_group") == key]
        if not rows:
            lines.append("- None")
        for row in rows:
            lines.append(f"- {row.get('raw_mention')} ({row.get('entity_kind')}, {row.get('mention_role')})")
            lines.append(f"  - Mention ID: `{row.get('mention_id')}`")
    lines.extend(["", "## Excluded records", ""])
    if not excluded:
        lines.append("- None")
    for row in excluded:
        lines.append(f"- `{row.get('producer_record_id')}`: `{row.get('reason')}`")
    return "\n".join(lines) + "\n"


def write_review_package(report_dir: Path, run_id: str, manifest: Mapping[str, Any], report: Mapping[str, Any], service: UniversalEventService) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = _report_paths(report_dir, run_id)
    summary_report, review_json, review_md = build_review_package(service, report, manifest, run_id)
    paths["manifest"].write_text(deterministic_json(manifest) + "\n", encoding="utf-8")
    paths["summary"].write_text(deterministic_json(summary_report) + "\n", encoding="utf-8")
    paths["review"].write_text(deterministic_json(review_json) + "\n", encoding="utf-8")
    paths["review_md"].write_text(review_md, encoding="utf-8")
    paths["excluded"].write_text(deterministic_json({"excluded_records": report.get("excluded_records") or []}) + "\n", encoding="utf-8")
    template = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "shadow_run_id": run_id,
        "resolver_version": manifest.get("resolver_version"),
        "instructions": "Fill reviewer, decision_type, decision_reason, expected_mention_fingerprint, expected_candidate_set_fingerprint, and selected entity fields. Do not edit mention fingerprints.",
        "decisions": [
            {
                "shadow_run_id": run_id,
                "resolver_version": manifest.get("resolver_version"),
                "mention_id": item.get("mention_id"),
                "expected_mention_fingerprint": item.get("mention_fingerprint"),
                "expected_candidate_set_fingerprint": _json_hash(item.get("ranked_match_candidates") or []),
                "reviewer": "",
                "decision_type": "",
                "decision_reason": "",
                "selected_match_candidate_id": "",
                "organization_id": "",
                "location_id": "",
            }
            for item in review_json.get("review_items") or []
        ],
    }
    paths["entity_review_template"].write_text(deterministic_json(template) + "\n", encoding="utf-8")
    paths["entity_review_guide"].write_text(_entity_review_guide(run_id), encoding="utf-8")
    if not paths["calibration"].exists():
        paths["calibration"].write_text("", encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _entity_review_guide(run_id: str) -> str:
    return "\n".join(
        [
            "# Care Line Entity Review Guide",
            "",
            f"- Shadow run: `{run_id}`",
            "- Valid decision types: `matched`, `created_new`, `rejected_match`, `deferred`, `corrected`.",
            "- Every decision must include a reviewer and decision reason.",
            "- Do not change mention fingerprints or candidate-set fingerprints.",
            "- Defer when source evidence does not identify a canonical organization or location cleanly.",
            "",
        ]
    )


def write_phase5_artifacts(
    repo_root: Path,
    report_dir: Path,
    run_id: str,
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    service: UniversalEventService,
) -> dict[str, str]:
    paths = _report_paths(report_dir, run_id)
    canonical_records = []
    for item in manifest.get("input_files") or []:
        if item.get("malformed_error"):
            continue
        path = repo_root / str(item["path"])
        canonical_records.extend(load_canonical_records(path, str(item.get("input_type") or detect_input_type(path))))
    exclusion_report = analyze_exclusions(canonical_records)
    inventory = find_structured_sources(repo_root)
    sample = select_bounded_real_sample(repo_root)
    bootstrap = build_bootstrap_review_artifact(service, shadow_run_id=run_id)
    preview = promotion_preview(service, shadow_run_id=run_id)
    threshold_report = threshold_evaluation(
        [],
        [
            {"auto_match_threshold": 0.9, "ambiguity_margin": 0.08},
            {"auto_match_threshold": 0.92, "ambiguity_margin": 0.08},
            {"auto_match_threshold": 0.95, "ambiguity_margin": 0.1},
        ],
    )
    s = dict(report.get("run_summary") or {})
    quality = {
        "schema_version": PHASE5_QUALITY_SCHEMA_VERSION,
        "shadow_run_id": run_id,
        "input_coverage": {
            "dates_examined": sample.get("selected_dates"),
            "structured_files_discovered": len(inventory),
            "records_by_input_type": _counts(canonical_records_dicts(canonical_records), "producer_input_type"),
            "missing_dates": manifest.get("missing_dates") or [],
            "malformed_files": manifest.get("malformed_files") or [],
        },
        "eligibility": {
            "eligible_candidates": int(s.get("eligible_count") or 0),
            "exclusions": int(s.get("excluded_count") or 0),
            "exclusion_analysis": exclusion_report.get("aggregates"),
        },
        "entity_resolution": {
            "mentions": int(s.get("mention_count") or 0),
            "candidate_matches": int(s.get("match_candidate_count") or 0),
            "reviewed_decisions": 0,
            "calibration": calibration_metrics([]),
        },
        "promotion_readiness": {
            "candidates_reviewed": 0,
            "promotion_eligible": sum(1 for row in preview.get("promotion_previews") or [] if row.get("promotion_eligible")),
            "promotion_blocked": sum(1 for row in preview.get("promotion_previews") or [] if not row.get("promotion_eligible")),
            "promotion_previews_generated": len(preview.get("promotion_previews") or []),
        },
        "eligible_candidates": int(s.get("eligible_count") or 0),
        "mention_count": int(s.get("mention_count") or 0),
        "reviewed_mention_count": 0,
        "calibration_generated": True,
        "promotion_preview_deterministic": preview == promotion_preview(service, shadow_run_id=run_id),
        "sample_selection": sample,
        "readiness": {},
    }
    quality["readiness"] = readiness_decision(quality)
    paths["source_inventory"].write_text(deterministic_json({"schema_version": PHASE5_SCHEMA_VERSION, "sources": inventory}) + "\n", encoding="utf-8")
    paths["canonical_records"].write_text(canonical_records_to_json(canonical_records) + "\n", encoding="utf-8")
    paths["excluded"].write_text(deterministic_json(exclusion_report) + "\n", encoding="utf-8")
    paths["promotion_preview"].write_text(deterministic_json(preview) + "\n", encoding="utf-8")
    paths["threshold_report"].write_text(deterministic_json(threshold_report) + "\n", encoding="utf-8")
    paths["entity_review_template"].write_text(deterministic_json(bootstrap) + "\n", encoding="utf-8")
    write_phase5_quality(paths["phase5_quality_json"], paths["phase5_quality_md"], quality)
    return {key: str(value) for key, value in paths.items() if value.exists()}


def canonical_records_dicts(records: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(getattr(row, "__dict__", row)) for row in records]


def persist_run_history(
    service: UniversalEventService,
    *,
    run_id: str,
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    config_hash: str,
    execution: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    options: Mapping[str, Any],
    report_path: str,
) -> None:
    s = dict(report.get("run_summary") or {})
    with service.repository.session_scope() as session:
        row = session.get(ShadowIngestionRunRow, run_id)
        now = utc_now()
        if row is None:
            row = ShadowIngestionRunRow(
                shadow_run_id=run_id,
                producer=PRODUCER,
                input_manifest_hash=_json_hash(manifest),
                date_from=str(manifest.get("date_from") or ""),
                date_to=str(manifest.get("date_to") or ""),
                adapter_version=str(manifest.get("adapter_version") or ADAPTER_VERSION),
                resolver_version=str(manifest.get("resolver_version") or RESOLVER_VERSION),
                configuration_hash=config_hash,
                status=status,
                first_executed_at=started_at,
                last_executed_at=completed_at,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        row.status = status
        row.last_executed_at = completed_at
        row.input_file_count = len(manifest.get("input_files") or [])
        row.input_record_count = int(s.get("input_record_count") or 0)
        row.eligible_record_count = int(s.get("eligible_count") or 0)
        row.excluded_record_count = int(s.get("excluded_count") or 0)
        row.candidate_count = int(s.get("created_candidate_count") or 0) + int(s.get("existing_candidate_count") or 0)
        row.mention_count = int(s.get("mention_count") or 0)
        row.match_candidate_count = int(s.get("match_candidate_count") or 0)
        row.ambiguous_count = int(s.get("ambiguous_mention_count") or 0)
        row.unresolved_count = int(s.get("unresolved_mention_count") or 0)
        row.automatic_matchable_count = int(s.get("automatically_matchable_mention_count") or 0)
        row.error_count = int(s.get("error_count") or 0)
        row.warning_count = len(report.get("mapping_warnings") or []) + len(report.get("normalization_warnings") or [])
        session.flush()
        if session.get(ShadowIngestionExecutionRow, execution) is None:
            session.add(
                ShadowIngestionExecutionRow(
                    execution_id=execution,
                    shadow_run_id=run_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=status,
                    host_label=socket.gethostname()[:120],
                    command_options_json=dict(options),
                    report_path=report_path,
                    error_summary="; ".join(str(item.get("error") or "") for item in report.get("errors") or []),
                    created_at=started_at,
                )
            )
        for section, result_type in (("created_candidates", "created"), ("existing_candidates", "existing")):
            for item in report.get(section) or []:
                producer_id = str(item.get("producer_record_id") or "")
                record_hash = str(item.get("payload_hash") or "")
                prior_state = session.execute(
                    select(ShadowIngestionRecordResultRow)
                    .where(ShadowIngestionRecordResultRow.shadow_run_id == run_id)
                    .where(ShadowIngestionRecordResultRow.producer_record_id == producer_id)
                    .where(ShadowIngestionRecordResultRow.input_record_hash == record_hash)
                ).scalar_one_or_none()
                if prior_state is not None:
                    continue
                row_id = _stable_id("record_result", run_id, producer_id, record_hash, result_type)
                if session.get(ShadowIngestionRecordResultRow, row_id) is None:
                    session.add(
                        ShadowIngestionRecordResultRow(
                            record_result_id=row_id,
                            shadow_run_id=run_id,
                            producer_record_id=producer_id,
                            source_file="",
                            input_record_hash=record_hash,
                            result_type="withdrawn" if item.get("metadata_updated") and item.get("withdrawn") else result_type,
                            candidate_id=item.get("candidate_id"),
                            exclusion_reason=None,
                            warning_codes_json=[],
                            created_at=started_at,
                        )
                    )
        for item in report.get("excluded_records") or []:
            producer_id = str(item.get("producer_record_id") or "")
            record_hash = _json_hash(item)
            row_id = _stable_id("record_result", run_id, producer_id, record_hash, "excluded")
            if session.get(ShadowIngestionRecordResultRow, row_id) is None:
                session.add(
                    ShadowIngestionRecordResultRow(
                        record_result_id=row_id,
                        shadow_run_id=run_id,
                        producer_record_id=producer_id,
                        source_file="",
                        input_record_hash=record_hash,
                        result_type="excluded",
                        candidate_id=None,
                        exclusion_reason=item.get("reason"),
                        warning_codes_json=[],
                        created_at=started_at,
                    )
                )


def import_review_decisions(database: Path, decisions_path: Path, *, shadow: bool, calibration_dir: Path | None = None) -> dict[str, Any]:
    if not shadow:
        raise ValueError("--shadow is required")
    repo = SQLiteUniversalEventRepository(database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions = payload.get("decisions") if isinstance(payload, dict) else payload
    if not isinstance(decisions, list):
        raise ValueError("review decisions must be a list or {'decisions': [...]}")
    accepted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    calibration_rows: list[dict[str, Any]] = []
    for decision in decisions:
        try:
            mention_id = str(decision["mention_id"])
            with service.repository.session_scope() as session:
                mention = session.get(EntityMentionRow, mention_id)
                if mention is None:
                    raise ValueError(f"mention not found: {mention_id}")
                expected = str(decision.get("expected_mention_fingerprint") or "")
                if expected and expected != _mention_fingerprint(mention):
                    raise ValueError("stale review package: mention fingerprint changed")
                selected = str(decision.get("selected_match_candidate_id") or "")
                if selected and session.get(EntityMatchCandidateRow, selected) is None:
                    raise ValueError(f"selected match candidate not found: {selected}")
            decision_type = str(decision["decision_type"])
            if decision_type == "matched":
                service.resolve_mention(
                    {
                        "mention_id": mention_id,
                        "decision_type": "matched",
                        "organization_id": decision.get("organization_id"),
                        "location_id": decision.get("location_id"),
                        "selected_match_candidate_id": decision.get("selected_match_candidate_id"),
                        "confidence": float(decision.get("confidence") or 0),
                        "decision_reason": str(decision.get("decision_reason") or ""),
                        "reviewer": str(decision.get("reviewer") or ""),
                        "resolver_version": str(decision.get("expected_resolver_version") or RESOLVER_VERSION),
                        "created_at": _utc(),
                        "supersedes_decision_id": decision.get("superseded_decision_id"),
                    }
                )
            elif decision_type == "created_new":
                if decision.get("organization_type"):
                    service.create_organization_from_mention(mention_id, reviewer=str(decision.get("reviewer") or ""), created_at=_utc())
                elif decision.get("location_type"):
                    service.create_location_from_mention(mention_id, reviewer=str(decision.get("reviewer") or ""), created_at=_utc())
                else:
                    raise ValueError("created_new requires organization_type or location_type")
            elif decision_type == "corrected":
                prior = str(decision.get("superseded_decision_id") or "")
                if not prior:
                    raise ValueError("corrected requires superseded_decision_id")
                service.correct_resolution(
                    prior,
                    organization_id=decision.get("organization_id"),
                    location_id=decision.get("location_id"),
                    reviewer=str(decision.get("reviewer") or ""),
                    reason=str(decision.get("decision_reason") or ""),
                    created_at=_utc(),
                )
            elif decision_type in {"deferred", "unresolved"}:
                service.defer_resolution(mention_id, reviewer=str(decision.get("reviewer") or ""), reason=str(decision.get("decision_reason") or ""), created_at=_utc())
            elif decision_type == "rejected_match":
                service.reject_match(mention_id, selected_match_candidate_id=decision.get("selected_match_candidate_id"), reviewer=str(decision.get("reviewer") or ""), reason=str(decision.get("decision_reason") or ""), created_at=_utc())
            else:
                raise ValueError(f"unsupported decision_type: {decision_type}")
            accepted.append({"mention_id": mention_id, "decision_type": decision_type})
            calibration_rows.extend(_calibration_rows(service, mention_id, decision))
        except Exception as exc:  # noqa: BLE001
            errors.append({"mention_id": str(decision.get("mention_id") or ""), "error": f"{type(exc).__name__}: {exc}"})
    if calibration_dir:
        calibration_dir.mkdir(parents=True, exist_ok=True)
        path = calibration_dir / "care_line_shadow_calibration.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for row in calibration_rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    repo.close()
    return {"accepted": accepted, "errors": errors, "calibration_rows": len(calibration_rows)}


def _calibration_rows(service: UniversalEventService, mention_id: str, decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with service.repository.session_scope() as session:
        mention = session.get(EntityMentionRow, mention_id)
        matches = list(session.execute(select(EntityMatchCandidateRow).where(EntityMatchCandidateRow.mention_id == mention_id)).scalars())
        for match in matches:
            selected = str(decision.get("selected_match_candidate_id") or "")
            rows.append(
                {
                    "calibration_schema_version": CALIBRATION_SCHEMA_VERSION,
                    "mention_id": mention_id,
                    "entity_kind": mention.entity_kind if mention else "",
                    "raw_name": mention.raw_name if mention else "",
                    "normalized_name": mention.normalized_name if mention else "",
                    "context": {"role": mention.mention_role if mention else ""},
                    "candidate_entity_id": match.organization_id or match.location_id,
                    "match_score": match.match_score,
                    "match_method": match.match_method,
                    "match_features": dict(match.match_features_json or {}),
                    "resolver_version": match.resolver_version,
                    "review_decision": decision.get("decision_type"),
                    "is_correct_match": bool(selected and selected == match.match_candidate_id and decision.get("decision_type") == "matched"),
                    "reviewer": decision.get("reviewer"),
                    "decision_reason": decision.get("decision_reason"),
                    "source_domain": "healthcare",
                    "producer": "care-line",
                }
            )
    return rows


def run_operator(args: argparse.Namespace) -> dict[str, Any]:
    if not args.shadow:
        raise ValueError("--shadow is required")
    repo_root = Path(args.repo_root or ".").resolve()
    database = Path(args.database)
    report_dir = Path(args.report_dir)
    review_dir = Path(args.review_dir or args.report_dir)
    calibration_dir = Path(args.calibration_dir or args.report_dir)
    for path in (database, report_dir, review_dir, calibration_dir):
        validate_output_path(path if path.is_absolute() else repo_root / path, repo_root)
    input_paths = [Path(value) for value in (args.input or [])]
    if input_paths:
        input_paths = [path if path.is_absolute() else repo_root / path for path in input_paths]
    input_types = tuple(getattr(args, "input_type", None) or ("manual-sources",))
    unsupported = sorted(set(input_types) - {"manual-sources", "discovered-sources", "canonical-reviewed-records"})
    if unsupported and not input_paths:
        raise ValueError(f"date discovery does not support input types: {', '.join(unsupported)}")
    phase5_quality = bool(getattr(args, "phase5_quality", False))
    if any(item not in ADMITTED_INPUT_TYPES for item in input_types) and not phase5_quality:
        raise ValueError("non-admitted input types require --phase5-quality so exclusions are reported explicitly")
    manifest = build_input_manifest(
        repo_root,
        date_value=args.date,
        date_from=args.date_from,
        date_to=args.date_to,
        input_paths=input_paths,
        input_types=input_types,
        adapter_version=args.adapter_version or ADAPTER_VERSION,
        resolver_version=args.resolver_version or RESOLVER_VERSION,
        allow_future_date=args.allow_future_date,
    )
    if args.strict and manifest.get("malformed_files"):
        raise ValueError("strict mode refuses malformed input files")
    records = load_manifest_records(manifest, repo_root, include_nonpublic_reviewed=args.include_nonpublic_reviewed, max_records=args.max_records)
    config_hash = configuration_hash(vars(args))
    run_id = shadow_run_id(manifest, config_hash)
    started = _utc()
    exec_id = execution_id(run_id, started)
    repo = SQLiteUniversalEventRepository(database if database.is_absolute() else repo_root / database)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    report = ingest_care_line_shadow(records, service, check_only=args.check_only)
    completed = _utc()
    status = "failed" if report["run_summary"]["error_count"] else "ok"
    paths = write_review_package((report_dir if report_dir.is_absolute() else repo_root / report_dir), run_id, manifest, report, service)
    if not args.check_only:
        persist_run_history(
            service,
            run_id=run_id,
            manifest=manifest,
            report=report,
            config_hash=config_hash,
            execution=exec_id,
            started_at=started,
            completed_at=completed,
            status=status,
            options={key: str(value) for key, value in vars(args).items() if key not in {"import_review"}},
            report_path=paths["summary"],
        )
    if args.rerun:
        paths["diff"] = write_diff_report(service, (report_dir if report_dir.is_absolute() else repo_root / report_dir), run_id, report)
    if phase5_quality or bool(getattr(args, "promotion_preview", False)) or bool(getattr(args, "source_inventory", False)):
        paths.update(
            write_phase5_artifacts(
                repo_root,
                (report_dir if report_dir.is_absolute() else repo_root / report_dir),
                run_id,
                manifest,
                report,
                service,
            )
        )
    repo.close()
    return {"shadow_run_id": run_id, "execution_id": exec_id, "manifest": manifest, "report": report, "paths": paths, "status": status}


def write_diff_report(service: UniversalEventService, report_dir: Path, run_id: str, report: Mapping[str, Any]) -> str:
    diff = {
        "shadow_run_id": run_id,
        "comparison_basis": "deterministic_payloads_only",
        "added_producer_records": [row.get("producer_record_id") for row in report.get("created_candidates") or []],
        "removed_or_withdrawn_producer_records": [row.get("producer_record_id") for row in report.get("existing_candidates") or [] if row.get("metadata_updated")],
        "changed_payloads": [row.get("producer_record_id") for row in report.get("existing_candidates") or [] if row.get("metadata_updated")],
        "changed_eligibility": [],
        "changed_exclusions": [],
        "changed_candidate_metadata": [],
        "changed_match_rankings": [],
        "changed_automatic_matchability": [],
    }
    path = report_dir / f"{run_id}.diff.json"
    path.write_text(deterministic_json(diff) + "\n", encoding="utf-8")
    return str(path)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Guarded Care Line Universal Events shadow operator.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--database")
    p.add_argument("--date")
    p.add_argument("--date-from")
    p.add_argument("--date-to")
    p.add_argument("--input", action="append")
    p.add_argument("--input-type", action="append", choices=sorted({"manual-sources", "discovered-sources", "reviewed-records", "canonical-reviewed-records", "claim-ledger"}))
    p.add_argument("--report-dir", default="data/universal_events/shadow/care-line/reports")
    p.add_argument("--review-dir", default="")
    p.add_argument("--calibration-dir", default="")
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--shadow", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--max-records", type=int)
    p.add_argument("--adapter-version", default=ADAPTER_VERSION)
    p.add_argument("--resolver-version", default=RESOLVER_VERSION)
    p.add_argument("--fail-on-error", action="store_true")
    p.add_argument("--fail-on-warning", action="store_true")
    p.add_argument("--include-nonpublic-reviewed", action="store_true")
    p.add_argument("--allow-future-date", action="store_true")
    p.add_argument("--import-review")
    p.add_argument("--phase5-quality", action="store_true")
    p.add_argument("--source-inventory", action="store_true")
    p.add_argument("--promotion-preview", action="store_true")
    p.add_argument("--shadow-run-id", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.import_review:
            if not args.database:
                raise ValueError("--database is required")
            result = import_review_decisions(Path(args.database), Path(args.import_review), shadow=args.shadow, calibration_dir=Path(args.calibration_dir) if args.calibration_dir else None)
            print(deterministic_json(result))
            return 0 if not result["errors"] else 1
        if not args.database:
            raise ValueError("--database is required")
        result = run_operator(args)
        print(deterministic_json({"shadow_run_id": result["shadow_run_id"], "execution_id": result["execution_id"], "status": result["status"], "paths": result["paths"]}))
        if args.fail_on_error and result["report"]["run_summary"]["error_count"]:
            return 1
        if args.fail_on_warning and (result["report"].get("mapping_warnings") or result["report"].get("normalization_warnings")):
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
