from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from bluefern_dispatches.care_line_national_pipeline import (
    COLLECTION_RUNS_ROOT,
    PIPELINE_SCHEMA_VERSION,
    REVIEW_QUEUE_SCHEMA_VERSION,
    REVIEW_ROOT,
    RUN_STATUS_FAILURE,
    RUN_STATUS_PARTIAL_SUCCESS,
    RUN_STATUS_SUCCESS,
    _atomic_write,
    _load_json,
    _review_state_paths,
    _slug,
    _source_attempt_filename,
    _source_failure_class,
    _source_failure_filename,
    _source_failure_transient,
    build_review_queue,
    collectable_sources,
    load_canonical_registry,
    load_reviewed_records,
    run_collection_attempt,
    update_candidate_registry_at_path,
    utc_now,
)
from bluefern_dispatches.care_line_source_registry import CareLineSource


SOURCE_REPLAY_SCHEMA_VERSION = "bluefern.source_replay.v1"
SOURCE_REPLAY_ROOT = Path("data/dispatches/care-line/source-replays")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SUPPORTED_DISPATCHES = {"care-line", "food-line", "ice", "gaza"}
DISABLED_DISPATCH_REASONS = {
    "food-line": "Food Line has durable task-level recovery receipts but no canonical registry-bound one-source replay contract.",
    "ice": "ICE monitor does not expose a source-isolated replay handler with per-source reconciliation.",
    "gaza": "Gaza source collection remains excluded because source replay is coupled to public/editorial workflows.",
}


class SourceReplayError(RuntimeError):
    """Raised when a source replay request cannot be safely evaluated."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_id(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(text):
        raise SourceReplayError(f"{field} must match {SAFE_ID_RE.pattern}")
    return text


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _file_ref(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": _relative_path(root, path), "exists": False}
    return {
        "path": _relative_path(root, path),
        "exists": True,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _git_head(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _retry_run_id(parent_run_id: str, source_id: str, retry_id: str) -> str:
    return f"{_slug(parent_run_id) or 'parent'}--source-replay--{_slug(source_id) or 'source'}--{_slug(retry_id) or 'retry'}"


def _source_replay_receipt_path(root: Path, *, logical_date: str, parent_run_id: str, source_id: str, retry_id: str) -> Path:
    return (
        root
        / SOURCE_REPLAY_ROOT
        / logical_date
        / (_slug(parent_run_id) or "parent")
        / (_slug(source_id) or "source")
        / f"{_slug(retry_id) or 'retry'}.json"
    )


def _iter_care_replay_receipt_paths(source_root: Path, dates: Iterable[str] | None = None) -> Iterable[Path]:
    root = source_root / SOURCE_REPLAY_ROOT
    date_dirs = [root / date for date in dates] if dates is not None else sorted(root.glob("*"))
    for date_dir in date_dirs:
        if date_dir.is_dir():
            yield from sorted(date_dir.glob("*/*/*.json"))


def load_care_line_source_replay_receipts(source_root: Path, dates: Iterable[str] | None = None) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in _iter_care_replay_receipt_paths(source_root, dates):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == SOURCE_REPLAY_SCHEMA_VERSION
            and payload.get("dispatch") == "care-line"
            and payload.get("public_side_effects") is False
            and payload.get("publication_attempted") is False
        ):
            payload.setdefault("receipt_path", _relative_path(source_root, path))
            receipts.append(payload)
    receipts.sort(key=lambda item: str(item.get("completed_at") or item.get("started_at") or ""))
    return receipts


def successful_replayed_sources_for_parent(
    source_root: Path,
    *,
    logical_date: str,
    parent_run_id: str,
) -> dict[str, dict[str, Any]]:
    recovered: dict[str, dict[str, Any]] = {}
    for receipt in load_care_line_source_replay_receipts(source_root, [logical_date]):
        if receipt.get("parent_run_id") != parent_run_id:
            continue
        if receipt.get("effective_terminal_source_state") not in {"ok", "partial"}:
            continue
        source_id = str(receipt.get("source_id") or "")
        if source_id:
            recovered[source_id] = receipt
    return recovered


def _disabled_payload(dispatch: str) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_REPLAY_SCHEMA_VERSION,
        "dispatch": dispatch,
        "eligible": False,
        "outcome": "disabled",
        "reason": DISABLED_DISPATCH_REASONS.get(dispatch, "dispatch has no registered source replay contract"),
        "publication_attempted": False,
        "public_side_effects": False,
        "production_state_mutated": False,
        "next_eligible_action": "wait_for_next_scheduled_run",
    }


def _source_row_by_id(root: Path, source_id: str) -> dict[str, Any] | None:
    registry = load_canonical_registry(root, include_disabled=True)
    for row in collectable_sources(registry, include_partial=True, include_manual_review=False):
        source = row.get("source")
        if isinstance(source, CareLineSource) and source.source_id == source_id:
            return row
    return None


def _failure_is_transient(failure: Mapping[str, Any], attempt: Mapping[str, Any]) -> bool:
    if failure.get("transient") is True:
        return True
    reason = str(failure.get("failure_reason") or attempt.get("failure_reason") or "")
    failure_class = str(failure.get("failure_class") or _source_failure_class(reason))
    return _source_failure_transient(reason, failure_class)


def _load_parent_context(root: Path, *, logical_date: str, parent_run_id: str, source_id: str) -> dict[str, Any]:
    run_dir = root / COLLECTION_RUNS_ROOT / logical_date / parent_run_id
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file():
        raise SourceReplayError("parent run manifest not found")
    manifest = _load_json(manifest_path, {})
    if not isinstance(manifest, Mapping):
        raise SourceReplayError("parent run manifest is not an object")
    source_ids = set(str(value) for value in manifest.get("selected_source_ids") or manifest.get("source_ids") or [])
    if source_id not in source_ids:
        raise SourceReplayError("source_id was not selected in the parent collection run")
    attempt_path = run_dir / _source_attempt_filename(source_id)
    attempt = _load_json(attempt_path, {})
    if not isinstance(attempt, Mapping) or not attempt:
        raise SourceReplayError("parent source attempt artifact not found")
    failure_path = run_dir / _source_failure_filename(source_id)
    failure = _load_json(failure_path, {}) if failure_path.is_file() else {}
    if str(attempt.get("collection_status") or "") != "failed":
        raise SourceReplayError("parent source attempt is already terminal-successful")
    if not isinstance(failure, Mapping) or not _failure_is_transient(failure, attempt):
        raise SourceReplayError("parent source failure is not an eligible transient failure")
    return {
        "run_dir": run_dir,
        "manifest_path": manifest_path,
        "manifest": dict(manifest),
        "attempt_path": attempt_path,
        "attempt": dict(attempt),
        "failure_path": failure_path,
        "failure": dict(failure),
    }


def _effective_attempts(
    root: Path,
    *,
    manifest: Mapping[str, Any],
    logical_date: str,
    parent_run_id: str,
    replay_attempt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    attempts = [dict(row) for row in manifest.get("attempts", []) if isinstance(row, Mapping)]
    if replay_attempt:
        source_id = str(replay_attempt.get("source_id") or "")
        attempts = [dict(row, retry_state="superseded_by_source_replay") if str(row.get("source_id") or "") == source_id else row for row in attempts]
        attempts.append({**dict(replay_attempt), "retry_state": "source_replay_recovered"})
    recovered = successful_replayed_sources_for_parent(root, logical_date=logical_date, parent_run_id=parent_run_id)
    for source_id, receipt in recovered.items():
        if replay_attempt and source_id == replay_attempt.get("source_id"):
            continue
        attempts = [dict(row, retry_state="superseded_by_source_replay") if str(row.get("source_id") or "") == source_id else row for row in attempts]
        attempts.append(
            {
                "source_id": source_id,
                "source_name": receipt.get("source_name") or source_id,
                "collection_status": receipt.get("effective_terminal_source_state") or "ok",
                "qualified_candidate_count": receipt.get("recovered_candidate_count") or 0,
                "raw_item_count": receipt.get("recovered_item_count") or 0,
                "retry_state": "source_replay_recovered",
            }
        )
    counts = Counter(str(row.get("collection_status") or "unknown") for row in attempts if row.get("retry_state") != "superseded_by_source_replay")
    successful = sum(counts.get(key, 0) for key in ("ok", "partial"))
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    if attempts and successful == 0 and failed > 0:
        status = RUN_STATUS_FAILURE
    elif failed > 0:
        status = RUN_STATUS_PARTIAL_SUCCESS
    else:
        status = RUN_STATUS_SUCCESS
    return {
        "schema_version": f"{PIPELINE_SCHEMA_VERSION}.effective_source_state",
        "parent_run_id": parent_run_id,
        "run_date": logical_date,
        "updated_at": utc_now(),
        "effective_run_status": status,
        "successful_attempt_count": successful,
        "failed_source_count": failed,
        "skipped_source_count": skipped,
        "collection_status_counts": dict(sorted(counts.items())),
        "recovered_source_ids": sorted(recovered),
    }


def _reconcile_replay_result(
    root: Path,
    *,
    logical_date: str,
    result: Mapping[str, Any],
    active_queue_limit: int,
    low_priority_cap: int,
) -> dict[str, Any]:
    candidates = [dict(row) for row in result.get("candidates", []) if isinstance(row, Mapping)]
    if not candidates:
        return {
            "status": "no_candidate_review_state_mutation",
            "recovered_candidate_count": 0,
            "duplicate_or_existing_candidate_count": 0,
            "changed_paths": [],
        }
    review_paths = _review_state_paths(REVIEW_ROOT)
    registry = update_candidate_registry_at_path(
        root / review_paths["candidate_registry"],
        edition_date=logical_date,
        candidates=candidates,
        mark_absent_stale=False,
    )
    queue = build_review_queue(
        registry["candidates"],
        edition_date=logical_date,
        active_queue_limit=active_queue_limit,
        low_priority_cap=low_priority_cap,
    )
    changed_paths = [
        review_paths["candidate_registry"],
        review_paths["review_queue"],
        review_paths["backlog"],
        review_paths["duplicates"],
    ]
    _atomic_write(root / review_paths["review_queue"], queue)
    _atomic_write(
        root / review_paths["backlog"],
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": logical_date,
            "backlog_item_count": len(queue.get("backlog", [])),
            "items": list(queue.get("backlog", [])),
        },
    )
    _atomic_write(
        root / review_paths["duplicates"],
        {
            "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
            "edition_date": logical_date,
            "duplicate_item_count": len(queue.get("duplicates", [])),
            "items": list(queue.get("duplicates", [])),
        },
    )
    return {
        "status": "candidate_registry_merged",
        "recovered_candidate_count": len(candidates),
        "candidate_registry_created_count": registry["created_this_run"],
        "candidate_registry_updated_count": registry["updated_this_run"],
        "duplicate_or_existing_candidate_count": registry["updated_this_run"] + queue["duplicate_item_count"],
        "review_queue_count": queue["queue_item_count"],
        "backlog_item_count": queue["backlog_item_count"],
        "duplicate_item_count": queue["duplicate_item_count"],
        "changed_paths": [path.as_posix() for path in changed_paths],
    }


def replay_source(
    *,
    dispatch: str,
    source_id: str,
    logical_date: str,
    parent_run_id: str,
    retry_id: str,
    repo_root: Path,
    check: bool = False,
    expected_head: str | None = None,
    fetch_timeout: int = 20,
    max_items_per_source: int = 25,
    allow_insecure_tls: bool = False,
    active_queue_limit: int = 150,
    low_priority_cap: int = 25,
) -> dict[str, Any]:
    if dispatch not in SUPPORTED_DISPATCHES:
        raise SourceReplayError(f"unsupported dispatch: {dispatch}")
    if dispatch != "care-line":
        return _disabled_payload(dispatch)
    root = repo_root.resolve()
    source_id = _safe_id(source_id, field="source_id")
    parent_run_id = _safe_id(parent_run_id, field="parent_run_id")
    retry_id = _safe_id(retry_id, field="retry_id")
    try:
        datetime.strptime(logical_date, "%Y-%m-%d")
    except ValueError as exc:
        raise SourceReplayError(f"invalid logical_date: {logical_date}") from exc

    head = _git_head(root)
    if expected_head and head != expected_head:
        raise SourceReplayError("runner HEAD does not match expected protected head")
    source_row = _source_row_by_id(root, source_id)
    if source_row is None:
        raise SourceReplayError("source_id is not an automated collectable Care Line source")
    source = source_row["source"]
    parent = _load_parent_context(root, logical_date=logical_date, parent_run_id=parent_run_id, source_id=source_id)
    existing_recovered = successful_replayed_sources_for_parent(root, logical_date=logical_date, parent_run_id=parent_run_id)
    if source_id in existing_recovered:
        return {
            **existing_recovered[source_id],
            "eligible": False,
            "outcome": "already_recovered",
            "production_state_mutated": False,
            "next_eligible_action": "refresh_status_export",
        }

    receipt_path = _source_replay_receipt_path(
        root,
        logical_date=logical_date,
        parent_run_id=parent_run_id,
        source_id=source_id,
        retry_id=retry_id,
    )
    if receipt_path.is_file():
        payload = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload.setdefault("eligible", False)
            payload.setdefault("outcome", "already_completed")
            payload["production_state_mutated"] = False
            return payload

    failure = parent["failure"]
    attempt = parent["attempt"]
    base_payload: dict[str, Any] = {
        "schema_version": SOURCE_REPLAY_SCHEMA_VERSION,
        "dispatch": "care-line",
        "source_id": source_id,
        "source_name": getattr(source, "name", source_id),
        "logical_date": logical_date,
        "parent_run_id": parent_run_id,
        "retry_id": retry_id,
        "started_at": _now(),
        "protected_head": head,
        "expected_head": expected_head,
        "eligible": True,
        "check_only": check,
        "attempt_number": len(list((receipt_path.parent).glob("*.json"))) + 1,
        "original_failure_classification": {
            "failure_class": failure.get("failure_class") or _source_failure_class(str(attempt.get("failure_reason") or "")),
            "failure_reason": failure.get("failure_reason") or attempt.get("failure_reason") or "",
            "transient": True,
        },
        "original_failure_receipt_reference": {
            "parent_manifest": _file_ref(root, parent["manifest_path"]),
            "parent_attempt": _file_ref(root, parent["attempt_path"]),
            "parent_failure": _file_ref(root, parent["failure_path"]),
        },
        "eligibility_decision": "eligible_transient_source_failure",
        "publication_attempted": False,
        "public_side_effects": False,
        "production_state_mutated": False,
    }
    if check:
        return {
            **base_payload,
            "completed_at": _now(),
            "outcome": "eligible_check_passed",
            "fetch_result": {"attempted": False},
            "processing_result": {"attempted": False},
            "reconciliation_result": {"attempted": False},
            "effective_terminal_source_state": "pending_replay",
            "next_eligible_action": "execute_source_replay",
        }

    retry_run_id = _retry_run_id(parent_run_id, source_id, retry_id)
    retry_run_dir = root / COLLECTION_RUNS_ROOT / logical_date / retry_run_id
    retry_run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        retry_run_dir / "run-manifest.json",
        {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "run_id": retry_run_id,
            "run_date": logical_date,
            "parent_run_id": parent_run_id,
            "source_ids": [source_id],
            "status": "running",
            "source_replay": True,
            "started_at": base_payload["started_at"],
        },
    )
    result = run_collection_attempt(
        root,
        run_date=logical_date,
        run_id=retry_run_id,
        source_row=source_row,
        historical_reviewed_records=load_reviewed_records(root),
        allow_insecure_tls=allow_insecure_tls,
        fetch_timeout=fetch_timeout,
        max_items_per_source=max_items_per_source,
        collection_runs_root=COLLECTION_RUNS_ROOT,
    )
    replay_attempt = dict(result.get("attempt") or {})
    terminal_state = str(replay_attempt.get("collection_status") or "unknown")
    successful = terminal_state in {"ok", "partial"}
    reconciliation = {"status": "not_attempted_after_failed_replay", "changed_paths": []}
    if successful:
        reconciliation = _reconcile_replay_result(
            root,
            logical_date=logical_date,
            result=result,
            active_queue_limit=active_queue_limit,
            low_priority_cap=low_priority_cap,
        )
    effective_state = _effective_attempts(
        root,
        manifest=parent["manifest"],
        logical_date=logical_date,
        parent_run_id=parent_run_id,
        replay_attempt=replay_attempt if successful else None,
    )
    effective_state_path = parent["run_dir"] / "effective-source-state.json"
    _atomic_write(effective_state_path, effective_state)
    retry_manifest = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": retry_run_id,
        "run_date": logical_date,
        "parent_run_id": parent_run_id,
        "source_ids": [source_id],
        "status": RUN_STATUS_SUCCESS if successful else RUN_STATUS_FAILURE,
        "source_replay": True,
        "started_at": base_payload["started_at"],
        "completed_at": utc_now(),
        "attempts": [replay_attempt],
        "source_attempt_count": 1,
        "successful_attempt_count": 1 if successful else 0,
        "failed_source_count": 0 if successful else 1,
        "collection_status_counts": {terminal_state: 1},
        "run_manifest_path": (COLLECTION_RUNS_ROOT / logical_date / retry_run_id / "run-manifest.json").as_posix(),
    }
    _atomic_write(retry_run_dir / "run-manifest.json", retry_manifest)
    changed_paths = [
        (COLLECTION_RUNS_ROOT / logical_date / retry_run_id / "run-manifest.json").as_posix(),
        _relative_path(root, effective_state_path),
        *list(reconciliation.get("changed_paths", [])),
    ]
    receipt = {
        **base_payload,
        "completed_at": _now(),
        "outcome": "recovered" if successful else "replay_failed",
        "retry_run_id": retry_run_id,
        "fetch_result": {
            "attempted": True,
            "collection_status": terminal_state,
            "raw_item_count": replay_attempt.get("raw_item_count", 0),
            "content_hash": replay_attempt.get("content_hash", ""),
        },
        "processing_result": {
            "attempted": True,
            "qualified_candidate_count": replay_attempt.get("qualified_candidate_count", 0),
            "excluded_item_count": replay_attempt.get("excluded_item_count", 0),
            "failed_extraction_count": replay_attempt.get("failed_extraction_count", 0),
        },
        "recovered_item_count": replay_attempt.get("raw_item_count", 0),
        "recovered_candidate_count": reconciliation.get("recovered_candidate_count", 0),
        "duplicate_or_suppressed_count": reconciliation.get("duplicate_or_existing_candidate_count", 0),
        "reconciliation_result": reconciliation,
        "effective_terminal_source_state": terminal_state,
        "effective_source_state_path": _relative_path(root, effective_state_path),
        "retry_run_manifest_path": retry_manifest["run_manifest_path"],
        "changed_paths": sorted(dict.fromkeys(changed_paths)),
        "production_state_mutated": True,
        "next_eligible_action": "refresh_status_export" if successful else "wait_for_next_scheduled_run",
    }
    _atomic_write(receipt_path, receipt)
    receipt["receipt_path"] = _relative_path(root, receipt_path)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay one canonical source-id collection attempt without public side effects.")
    parser.add_argument("--dispatch", required=True, choices=sorted(SUPPORTED_DISPATCHES))
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--logical-date", required=True)
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--retry-id", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument("--max-items-per-source", type=int, default=25)
    parser.add_argument("--active-queue-limit", type=int, default=150)
    parser.add_argument("--low-priority-cap", type=int, default=25)
    parser.add_argument("--allow-insecure-tls", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = replay_source(
            dispatch=args.dispatch,
            source_id=args.source_id,
            logical_date=args.logical_date,
            parent_run_id=args.parent_run_id,
            retry_id=args.retry_id,
            repo_root=Path(args.repo_root),
            check=args.check,
            expected_head=args.expected_head or None,
            fetch_timeout=args.fetch_timeout,
            max_items_per_source=args.max_items_per_source,
            active_queue_limit=args.active_queue_limit,
            low_priority_cap=args.low_priority_cap,
            allow_insecure_tls=args.allow_insecure_tls,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except SourceReplayError as exc:
        print(
            json.dumps(
                {
                    "schema_version": SOURCE_REPLAY_SCHEMA_VERSION,
                    "eligible": False,
                    "outcome": "refused",
                    "reason": str(exc),
                    "publication_attempted": False,
                    "public_side_effects": False,
                    "production_state_mutated": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
