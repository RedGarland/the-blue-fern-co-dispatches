from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_discovery import discover_care_line_sources
from bluefern_dispatches.care_line_effective_date_follow_up import (
    build_follow_up_queries,
    load_follow_up_state,
    load_reviewed_records,
    update_follow_up_state,
)


COLLECTION_RUNS_ROOT = Path("data/dispatches/care-line/collection-runs")
REVIEW_ROOT = Path("data/dispatches/care-line/review")
SMOKE_COLLECTION_RUNS_ROOT = COLLECTION_RUNS_ROOT / "smoke"
SMOKE_REVIEW_ROOT = REVIEW_ROOT / "smoke"

PIPELINE_SCHEMA_VERSION = "bluefern.care_line.national_pipeline.v3"
REVIEW_QUEUE_SCHEMA_VERSION = "bluefern.care_line.national_review_queue.v3"
CANDIDATE_REGISTRY_SCHEMA_VERSION = "bluefern.care_line.candidate_registry.v3"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        rows = payload["records"]
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        rows = payload["sources"]
    else:
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _discovered_rows_path(report: Mapping[str, Any], root: Path) -> Path:
    value = str(report.get("discovered_sources_path") or "")
    if not value:
        raise ValueError("discovery report missing discovered_sources_path")
    path = Path(value)
    return path if path.is_absolute() else root / path


def _follow_up_state_path(review_root: Path) -> Path:
    return review_root / "effective-date-follow-up-state.json"


def _queue_sort_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("published_at") or row.get("source_publication_date") or row.get("source_published_date") or ""),
        str(row.get("source_title") or row.get("title") or ""),
        str(row.get("source_record_id") or row.get("source_id") or row.get("source_url") or ""),
    )


def _dedupe_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = _text(row, "source_record_id", "source_id", "source_url", "url")
        if not key:
            key = json.dumps({"title": _text(row, "title", "source_title"), "publisher": _text(row, "publisher", "source_publisher")}, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def _review_disposition(row: Mapping[str, Any]) -> str:
    if _text(row, "duplicate_of", "duplicate_of_record_id", "duplicate_of_producer_record_id"):
        return "duplicate"
    if bool(row.get("primary_eligible")):
        return "included"
    if bool(row.get("manual_review_required")):
        return "manual_review"
    if _text(row, "exclusion_reason", "primary_disqualification_reason"):
        return "excluded"
    return "excluded"


def _review_watchlist_status(row: Mapping[str, Any]) -> str:
    if bool(row.get("primary_eligible")):
        return "publication_candidate"
    if bool(row.get("manual_review_required")):
        return "watchlist_candidate"
    if _text(row, "duplicate_of", "duplicate_of_record_id", "duplicate_of_producer_record_id"):
        return "duplicate"
    return "excluded"


def _review_source_traceability(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_record_id": _text(row, "source_record_id", "source_id", "care_line_record_id"),
        "source_url": _text(row, "source_url", "url", "canonical_url"),
        "canonical_url": _text(row, "canonical_url", "source_url", "url"),
        "source_origin": _text(row, "source_origin", "source_record_origin", "discovery_channel"),
        "source_record_origin": _text(row, "source_record_origin"),
        "discovery_channel": _text(row, "discovery_channel"),
        "source_family": _text(row, "source_family"),
        "source_traceability_role": _text(row, "source_traceability_role"),
        "query_text": _text(row, "query_text", "query"),
        "query_url": _text(row, "query_url"),
        "retrieved_at": _text(row, "retrieved_at"),
        "published_at": _text(row, "published_at", "source_published_date"),
        "source_published_date": _text(row, "source_published_date"),
        "evidence_text_basis": _text(row, "evidence_text_basis"),
    }


def _build_review_audit(
    *,
    run_date: str,
    discovered_rows: Iterable[Mapping[str, Any]],
    review_queue: Mapping[str, Any],
    candidate_registry: Mapping[str, Any],
    manual_review: Iterable[Mapping[str, Any]],
    exclusions: Iterable[Mapping[str, Any]],
    paths: Mapping[str, str],
    follow_up_state: Mapping[str, Any],
) -> dict[str, Any]:
    manual_review_rows = [dict(row) for row in manual_review]
    exclusion_rows = [dict(row) for row in exclusions]
    discovered_list = [dict(row) for row in discovered_rows]
    audit_rows: list[dict[str, Any]] = []
    for row in discovered_list:
        disposition = _review_disposition(row)
        audit_rows.append(
            {
                "candidate_id": _text(row, "source_record_id", "source_id", "care_line_record_id"),
                "source_record_id": _text(row, "source_record_id", "source_id", "care_line_record_id"),
                "title": _text(row, "title"),
                "disposition": disposition,
                "qualification_status": "qualifying" if bool(row.get("primary_eligible")) else "not_qualifying",
                "publication_eligible": bool(row.get("primary_eligible")),
                "watchlist_status": _review_watchlist_status(row),
                "dedupe_target": _text(row, "duplicate_of", "duplicate_of_record_id", "duplicate_of_producer_record_id"),
                "novelty_status": _text(row, "freshness_role", "classification_status"),
                "confidence": _text(row, "confidence"),
                "exclusion_reason": _text(row, "exclusion_reason"),
                "primary_disqualification_reason": _text(row, "primary_disqualification_reason"),
                "manual_review_required": bool(row.get("manual_review_required")),
                "source_traceability": _review_source_traceability(row),
            }
        )
    disposition_counts = Counter(entry["disposition"] for entry in audit_rows)
    exclusion_reason_counts = Counter(entry["exclusion_reason"] or entry["primary_disqualification_reason"] or "included" for entry in audit_rows)
    confidence_counts = Counter(entry["confidence"] or "unknown" for entry in audit_rows)
    source_origin_counts = Counter(entry["source_traceability"].get("source_origin") or "unknown" for entry in audit_rows)
    audit = {
        "schema_version": "bluefern.care_line.review_audit.v1",
        "edition_date": run_date,
        "generated_at": utc_now(),
        "review_queue_path": paths["review_queue_path"],
        "candidate_registry_path": paths["candidate_registry_path"],
        "manual_review_path": paths["manual_review_path"],
        "exclusions_path": paths["exclusions_path"],
        "follow_up_state_path": paths["follow_up_state_path"],
        "run_manifest_paths": {
            "review_queue": paths["review_queue_path"],
            "candidate_registry": paths["candidate_registry_path"],
            "manual_review": paths["manual_review_path"],
            "exclusions": paths["exclusions_path"],
            "follow_up_state": paths["follow_up_state_path"],
        },
        "counts": {
            "discovered": len(discovered_list),
            "included": sum(1 for row in discovered_list if bool(row.get("primary_eligible"))),
            "manual_review": len(manual_review_rows),
            "excluded": len(exclusion_rows),
            "duplicate": sum(1 for row in discovered_list if _review_disposition(row) == "duplicate"),
            "watchlist": sum(1 for row in discovered_list if _review_watchlist_status(row) == "watchlist_candidate"),
            "queue_items": int(review_queue.get("queue_item_count") or 0),
            "backlog_items": int(review_queue.get("backlog_item_count") or 0),
            "registry_items": int(candidate_registry.get("candidate_count") or 0),
        },
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "exclusion_reason_counts": dict(sorted(exclusion_reason_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "source_origin_counts": dict(sorted(source_origin_counts.items())),
        "rows": audit_rows,
        "follow_up_state": follow_up_state,
    }
    return audit


def _write_review_audit(root: Path, review_root: Path, audit: Mapping[str, Any]) -> dict[str, str]:
    review_root_abs = root / review_root
    audit_json_path = review_root_abs / "current-review-audit.json"
    audit_md_path = review_root_abs / "current-review-audit.md"
    _atomic_write(audit_json_path, audit)
    md_lines = [
        f"# Care Line review audit {audit.get('edition_date')}",
        "",
        f"- discovered: {audit.get('counts', {}).get('discovered', 0)}",
        f"- included: {audit.get('counts', {}).get('included', 0)}",
        f"- manual review: {audit.get('counts', {}).get('manual_review', 0)}",
        f"- excluded: {audit.get('counts', {}).get('excluded', 0)}",
        f"- duplicates: {audit.get('counts', {}).get('duplicate', 0)}",
        f"- watchlist candidates: {audit.get('counts', {}).get('watchlist', 0)}",
        "",
        "## Disposition counts",
    ]
    for key, value in sorted(dict(audit.get("disposition_counts") or {}).items()):
        md_lines.append(f"- {key}: {value}")
    md_lines.extend(["", "## Exclusion reasons"])
    for key, value in sorted(dict(audit.get("exclusion_reason_counts") or {}).items()):
        md_lines.append(f"- {key}: {value}")
    md_lines.extend(["", "## Source origin counts"])
    for key, value in sorted(dict(audit.get("source_origin_counts") or {}).items()):
        md_lines.append(f"- {key}: {value}")
    audit_md_path.parent.mkdir(parents=True, exist_ok=True)
    audit_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return {
        "review_audit_path": audit_json_path.as_posix(),
        "review_audit_markdown_path": audit_md_path.as_posix(),
    }


def build_review_queue(
    rows: Iterable[Mapping[str, Any]],
    *,
    edition_date: str,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
) -> dict[str, Any]:
    rows = _dedupe_rows(rows)
    rows.sort(key=_queue_sort_key, reverse=True)
    active_rows = [row for row in rows if bool(row.get("primary_eligible"))]
    manual_rows = [row for row in rows if not bool(row.get("primary_eligible"))]
    active = active_rows[: max(0, active_queue_limit)]
    backlog = active_rows[len(active) :] + manual_rows
    return {
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "edition_date": edition_date,
        "queue_item_count": len(active),
        "backlog_item_count": len(backlog),
        "duplicate_item_count": 0,
        "items": active,
        "backlog": backlog,
        "duplicates": [],
        "low_priority_cap": low_priority_cap,
    }


def update_candidate_registry(rows: Iterable[Mapping[str, Any]], *, edition_date: str) -> dict[str, Any]:
    candidates = _dedupe_rows(rows)
    return {
        "schema_version": CANDIDATE_REGISTRY_SCHEMA_VERSION,
        "edition_date": edition_date,
        "candidate_count": len(candidates),
        "created_this_run": len(candidates),
        "updated_this_run": 0,
        "persistent_candidates_from_prior_runs": 0,
        "stale_candidate_count": 0,
        "superseded_candidate_count": 0,
        "candidates": candidates,
    }


def _write_review_outputs(
    root: Path,
    *,
    review_root: Path,
    review_queue: Mapping[str, Any],
    candidate_registry: Mapping[str, Any],
    follow_up_state: Mapping[str, Any],
    manual_review: Iterable[Mapping[str, Any]],
    exclusions: Iterable[Mapping[str, Any]],
    review_audit: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    review_root_abs = root / review_root
    queue_path = review_root_abs / "current-review-queue.json"
    candidate_registry_path = review_root_abs / "candidate-registry.json"
    manual_review_path = review_root_abs / "current-manual-review.json"
    exclusions_path = review_root_abs / "current-exclusions.json"
    state_path = root / _follow_up_state_path(review_root)
    manual_review_rows = list(manual_review)
    exclusion_rows = list(exclusions)
    _atomic_write(queue_path, review_queue)
    _atomic_write(candidate_registry_path, candidate_registry)
    _atomic_write(manual_review_path, {"schema_version": REVIEW_QUEUE_SCHEMA_VERSION, "items": manual_review_rows, "manual_review_count": len(manual_review_rows)})
    _atomic_write(exclusions_path, {"schema_version": REVIEW_QUEUE_SCHEMA_VERSION, "items": exclusion_rows, "excluded_count": len(exclusion_rows)})
    _atomic_write(state_path, follow_up_state)
    paths = {
        "review_queue_path": queue_path.as_posix(),
        "candidate_registry_path": candidate_registry_path.as_posix(),
        "manual_review_path": manual_review_path.as_posix(),
        "exclusions_path": exclusions_path.as_posix(),
        "follow_up_state_path": state_path.as_posix(),
    }
    if review_audit is not None:
        paths.update(_write_review_audit(root, review_root, review_audit))
    return paths


def run_national_pipeline(
    root: Path,
    *,
    run_date: str,
    include_partial: bool = True,
    include_manual_review: bool = False,
    allow_insecure_tls: bool = False,
    source_limit: int | None = None,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
    smoke_test: bool = False,
    collection_runs_root: Path = COLLECTION_RUNS_ROOT,
    review_root: Path = REVIEW_ROOT,
) -> dict[str, Any]:
    root = Path(root).resolve()
    reviewed_records = load_reviewed_records(root)
    follow_up_state = load_follow_up_state(root)
    follow_up_queries = build_follow_up_queries(root, run_date, reviewed_records=reviewed_records, state=follow_up_state)
    discovery = discover_care_line_sources(
        root,
        run_date,
        max_results_per_query=max_items_per_source,
        max_queries=source_limit,
        max_candidates=None,
        follow_up_queries=follow_up_queries,
        write=True,
        dry_run=False,
    )
    discovered_rows = _load_rows(_discovered_rows_path(discovery, root))
    query_rows = list(discovery.get("query_rows") or [])
    follow_up_state_result = update_follow_up_state(
        root,
        run_date=run_date,
        follow_up_queries=follow_up_queries,
        discovery_query_rows=query_rows,
    )
    review_queue = build_review_queue(discovered_rows, edition_date=run_date, active_queue_limit=active_queue_limit, low_priority_cap=low_priority_cap)
    candidate_registry = update_candidate_registry(discovered_rows, edition_date=run_date)
    manual_review = [row for row in discovered_rows if not bool(row.get("primary_eligible"))]
    exclusions = [row for row in discovered_rows if not bool(row.get("primary_eligible"))]
    review_audit = _build_review_audit(
        run_date=run_date,
        discovered_rows=discovered_rows,
        review_queue=review_queue,
        candidate_registry=candidate_registry,
        manual_review=manual_review,
        exclusions=exclusions,
        paths={
            "review_queue_path": str(root / review_root / "current-review-queue.json"),
            "candidate_registry_path": str(root / review_root / "candidate-registry.json"),
            "manual_review_path": str(root / review_root / "current-manual-review.json"),
            "exclusions_path": str(root / review_root / "current-exclusions.json"),
            "follow_up_state_path": str(root / _follow_up_state_path(review_root)),
        },
        follow_up_state=follow_up_state_result,
    )
    paths = _write_review_outputs(
        root,
        review_root=review_root,
        review_queue=review_queue,
        candidate_registry=candidate_registry,
        follow_up_state=follow_up_state_result,
        manual_review=manual_review,
        exclusions=exclusions,
        review_audit=review_audit,
    )
    successful_attempt_count = sum(1 for row in query_rows if not str(row.get("error") or "").strip())
    failed_source_count = sum(1 for row in query_rows if str(row.get("error") or "").strip())
    skipped_source_count = 0
    status = "failure" if successful_attempt_count == 0 and failed_source_count > 0 else "partial_success" if failed_source_count else "success"
    follow_up_material_update_count = sum(1 for item in follow_up_state_result["items"] if item.get("status") == "MATERIAL_UPDATE_FOUND")
    run_id = f"{run_date.replace('-', '')}-{os.getpid()}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    run_manifest = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "run_date": run_date,
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "status": status,
        "collection_only": True,
        "smoke_test": smoke_test,
        "settings": {
            "include_partial": include_partial,
            "include_manual_review": include_manual_review,
            "allow_insecure_tls": allow_insecure_tls,
            "source_limit": source_limit,
            "fetch_timeout": fetch_timeout,
            "max_items_per_source": max_items_per_source,
            "active_queue_limit": active_queue_limit,
            "low_priority_cap": low_priority_cap,
            "smoke_test": smoke_test,
        },
        "selected_source_ids": [str(row.get("source_id") or row.get("source_record_id") or "") for row in candidate_registry["candidates"]],
        "source_attempt_count": len(query_rows),
        "successful_attempt_count": successful_attempt_count,
        "failed_source_count": failed_source_count,
        "skipped_source_count": skipped_source_count,
        "raw_items_retrieved_this_run": discovery.get("source_count", len(discovered_rows)),
        "event_leads_created_this_run": discovery.get("public_signal_count", 0),
        "qualified_candidates_created_this_run": candidate_registry["candidate_count"],
        "candidates_updated_this_run": candidate_registry["updated_this_run"],
        "persistent_candidates_from_prior_runs": candidate_registry["persistent_candidates_from_prior_runs"],
        "excluded_item_count": len(exclusions),
        "failed_extraction_count": 0,
        "manual_review_count": len(manual_review),
        "active_review_queue_count": review_queue["queue_item_count"],
        "backlog_item_count": review_queue["backlog_item_count"],
        "duplicate_item_count": review_queue["duplicate_item_count"],
        "priority_counts": dict(sorted(Counter(str(row.get("confidence") or "low") for row in candidate_registry["candidates"]).items())),
        "stale_candidate_count": candidate_registry["stale_candidate_count"],
        "superseded_candidate_count": candidate_registry["superseded_candidate_count"],
        "follow_up_query_count": len(follow_up_queries),
        "follow_up_material_update_count": follow_up_material_update_count,
        "follow_up_state_path": paths["follow_up_state_path"],
        "review_queue_path": paths["review_queue_path"],
        "candidate_registry_path": paths["candidate_registry_path"],
        "review_audit_path": paths.get("review_audit_path"),
        "review_audit_markdown_path": paths.get("review_audit_markdown_path"),
        "review_audit_disposition_counts": dict(sorted(review_audit["disposition_counts"].items())),
        "production_review_queue_mutation_disabled": smoke_test,
    }
    run_manifest_path = root / collection_runs_root / run_date / run_id / "run-manifest.json"
    _atomic_write(run_manifest_path, run_manifest)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_manifest": {**run_manifest, "run_manifest_path": run_manifest_path.as_posix()},
        "discovery": discovery,
        "candidate_registry": candidate_registry,
        "review_queue": review_queue,
        "follow_up_state": follow_up_state_result,
        "paths": paths,
        "manual_review": {"schema_version": REVIEW_QUEUE_SCHEMA_VERSION, "items": list(manual_review), "manual_review_count": len(list(manual_review))},
        "exclusions": {"schema_version": REVIEW_QUEUE_SCHEMA_VERSION, "items": list(exclusions), "excluded_count": len(list(exclusions))},
        "review_audit": review_audit,
    }
