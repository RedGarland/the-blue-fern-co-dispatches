from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.ice_dispatch import (
    CollectionHealth,
    EventRelationship,
    canonicalize_url,
    compare_event_observation,
    event_to_dict,
    event_with_accumulated_sources,
    map_ready_event,
    normalize_ice_candidate,
    run_diagnostic,
    utc_now,
)

MONITOR_MODE = "MONITOR_ONLY"
DEFAULT_REGISTRY = Path("data/dispatches/ice/sources.yml")
DEFAULT_OUTPUT_ROOT = Path("data/dispatches/ice/monitor")
DEFAULT_WINDOW_HOURS = 168
DEFAULT_MAX_PER_SOURCE = 5
PRODUCTION_BRANCH = "add/pages-repo-default"
STATE_SCHEMA = "bluefern.ice.monitor.state.v1"
QUEUE_SCHEMA = "bluefern.ice.monitor.review_queue.v1"
RUN_SCHEMA = "bluefern.ice.monitor.run.v1"
TERMINAL_REVIEW_STATES = {"REVIEWED", "REJECTED"}
ACTIVE_REVIEW_STATES = {"NEW", "NEEDS_REVIEW", "NEEDS_CORROBORATION", "DUPLICATE_UPDATED"}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, encoding="utf-8", check=False)


def verify_monitor_checkout(repo_root: Path, *, branch: str = PRODUCTION_BRANCH) -> str:
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo_root)
    if status.returncode != 0:
        raise RuntimeError(f"git status failed: {status.stderr.strip()}")
    unexpected = [line[3:] for line in status.stdout.splitlines() if line and not line[3:].startswith("data/dispatches/ice/monitor/")]
    if unexpected:
        raise RuntimeError(f"ICE monitor checkout contains unexpected dirty paths: {', '.join(unexpected)}")
    current = _run(["git", "branch", "--show-current"], cwd=repo_root)
    if current.returncode != 0:
        raise RuntimeError(f"git branch failed: {current.stderr.strip()}")
    actual_branch = current.stdout.strip() or "<detached>"
    if actual_branch != branch:
        raise RuntimeError(f"ICE monitor branch mismatch: expected {branch}, found {actual_branch}")
    preflight = _run([sys.executable, "scripts/preflight_repo_state.py", "--source-repo", str(repo_root)], cwd=repo_root)
    if preflight.returncode != 0:
        raise RuntimeError(f"repository preflight failed: {(preflight.stdout + preflight.stderr).strip()}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {head.stderr.strip()}")
    return head.stdout.strip()


def _event_key(event: dict[str, Any]) -> str:
    lineage = event.get("lineage") or {}
    return str(lineage.get("fingerprint") or event.get("event_id"))


def _source_urls(event: dict[str, Any]) -> set[str]:
    return {
        canonicalize_url(str(source.get("canonical_source_url") or source.get("source_url")))
        for source in event.get("sources") or []
        if source.get("canonical_source_url") or source.get("source_url")
    }


def _source_tiers(event: dict[str, Any]) -> list[int]:
    return sorted({int(source.get("tier") or 3) for source in event.get("sources") or []})


def _published_or_modified(event: dict[str, Any], field_name: str) -> str | None:
    values = [source.get(field_name) for source in event.get("sources") or [] if source.get(field_name)]
    if not values:
        return None
    return min(values) if field_name == "published_at" else max(values)


def _event_from_dict(event: dict[str, Any]):
    payload = dict(event)
    lineage = payload.get("lineage") or {}
    for key in ("supersedes", "corrected_by", "edition_ids", "related_event_ids"):
        if key not in payload and isinstance(lineage, dict) and key in lineage:
            payload[key] = lineage.get(key) or []
    return normalize_ice_candidate(payload, observed_at=payload.get("last_updated_at") or payload.get("first_observed_at") or utc_now())


def _merged_event(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = event_to_dict(event_with_accumulated_sources(_event_from_dict(previous), _event_from_dict(current)))
    merged["first_seen"] = previous.get("first_seen") or previous.get("first_observed_at") or current.get("first_observed_at")
    return merged


def _relationship(previous: dict[str, Any] | None, current: dict[str, Any]) -> tuple[str, list[str]]:
    if previous is None:
        return EventRelationship.NEW_EVENT.value, ["not previously seen by monitor"]
    rel, reasons = compare_event_observation(_event_from_dict(previous), _event_from_dict(current))
    return rel.value, reasons


def _queue_state(event: dict[str, Any], relationship: str, existing: dict[str, Any] | None) -> str:
    if existing and existing.get("review_status") in TERMINAL_REVIEW_STATES:
        return str(existing["review_status"])
    editorial = event.get("editorial") or {}
    source_tiers = _source_tiers(event)
    if editorial.get("exclusion_reason") == "tier3_requires_corroboration" or (source_tiers and min(source_tiers) >= 3 and len(_source_urls(event)) < 2):
        return "NEEDS_CORROBORATION"
    if relationship in {EventRelationship.DUPLICATE_COVERAGE.value, EventRelationship.FOLLOW_UP_WITH_NEW_FACTS.value, EventRelationship.UPDATE_TO_EXISTING_EVENT.value, EventRelationship.CORRECTION.value}:
        return "DUPLICATE_UPDATED"
    return "NEW" if not existing else "NEEDS_REVIEW"


def _queue_item(event: dict[str, Any], *, relationship: str, reasons: list[str], run_id: str, run_path: Path, existing: dict[str, Any] | None, observed_at: str) -> dict[str, Any]:
    mapped = map_ready_event(_event_from_dict(event))
    state = _queue_state(event, relationship, existing)
    first_seen = (existing or {}).get("first_seen") or event.get("first_seen") or observed_at
    source_urls = sorted(_source_urls(event))
    return {
        "canonical_event_id": event.get("event_id"),
        "event_fingerprint": _event_key(event),
        "first_seen": first_seen,
        "last_seen": observed_at,
        "last_run_id": run_id,
        "event_date": event.get("event_date"),
        "published_at": _published_or_modified(event, "published_at"),
        "modified_at": _published_or_modified(event, "modified_at"),
        "category": event.get("primary_category"),
        "severity": event.get("severity"),
        "geography": event.get("location") or {},
        "source_set": source_urls,
        "source_tiers": _source_tiers(event),
        "corroboration_status": "corroborated" if len(source_urls) >= 2 or (source_urls and min(_source_tiers(event) or [3]) <= 1) else "needs_corroboration",
        "currentness": (event.get("editorial") or {}).get("currentness_status"),
        "map_readiness": mapped["map_readiness"],
        "review_status": state,
        "editorial_eligibility_candidate": bool((event.get("editorial") or {}).get("public_eligibility")),
        "evidence_references": {
            "run_dir": str(run_path.as_posix()),
            "canonical_events": str((run_path / "canonical_events.json").as_posix()),
            "raw_candidates": str((run_path / "raw_candidates.json").as_posix()),
            "provider_health": str((run_path / "provider_health.json").as_posix()),
        },
        "relationship_to_previous": relationship,
        "relationship_reasons": reasons,
    }


def _age_queue(queue: dict[str, Any], *, observed_at: str, stale_after_days: int) -> None:
    cutoff = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) - timedelta(days=stale_after_days)
    for item in queue.get("items", []):
        if item.get("review_status") not in ACTIVE_REVIEW_STATES:
            continue
        last_seen = item.get("last_seen")
        if not last_seen:
            continue
        try:
            seen = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
        except ValueError:
            continue
        if seen < cutoff:
            item["review_status"] = "REJECTED"
            item["stale_aged_out"] = True


def _summary(run_id: str, result: dict[str, Any], relationships: list[dict[str, Any]], queue_items: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = result["run_manifest"]
    relation_counts = Counter(row["relationship"] for row in relationships)
    state_counts = Counter(item["review_status"] for item in queue_items)
    return {
        "schema_version": RUN_SCHEMA,
        "mode": MONITOR_MODE,
        "run_id": run_id,
        "configured_providers": manifest["configured_providers"],
        "attempted_providers": manifest["attempted_providers"],
        "successful_providers": manifest["successful_providers"],
        "failed_providers": manifest["failed_providers"],
        "collection_health": manifest["health"],
        "raw_candidates": len(result["raw_candidates"]),
        "canonical_events": len(result["canonical_events"]),
        "new_events_since_prior_run": relation_counts.get(EventRelationship.NEW_EVENT.value, 0),
        "updates": relation_counts.get(EventRelationship.UPDATE_TO_EXISTING_EVENT.value, 0) + relation_counts.get(EventRelationship.FOLLOW_UP_WITH_NEW_FACTS.value, 0),
        "duplicates": relation_counts.get(EventRelationship.DUPLICATE_COVERAGE.value, 0),
        "corrections": relation_counts.get(EventRelationship.CORRECTION.value, 0),
        "needs_corroboration": state_counts.get("NEEDS_CORROBORATION", 0),
        "review_queue_additions": sum(1 for row in relationships if row["relationship"] == EventRelationship.NEW_EVENT.value),
        "category_summary": Counter(event.get("primary_category") for event in result["canonical_events"]),
        "geography_summary": Counter((event.get("location") or {}).get("state_or_territory") or "unknown" for event in result["canonical_events"]),
        "publication_side_effects": False,
        "shared_service_failures": manifest.get("shared_service_failures") or [],
    }


def run_monitor(
    repo_root: Path,
    *,
    registry: Path = DEFAULT_REGISTRY,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    live: bool = True,
    fixture: Path | None = None,
    run_id: str | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    observed_at: str | None = None,
    stale_after_days: int = 30,
    enforce_production_preflight: bool = False,
    branch: str = PRODUCTION_BRANCH,
) -> tuple[int, dict[str, Any]]:
    repo_root = repo_root.resolve()
    source_commit = verify_monitor_checkout(repo_root, branch=branch) if enforce_production_preflight else None
    observed_at = observed_at or utc_now()
    run_id = run_id or f"ice-monitor-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    output_root = repo_root / output_root
    run_path = output_root / "runs" / observed_at[:10] / run_id
    registry_path = (repo_root / registry).resolve() if not registry.is_absolute() else registry
    fixture_path = (repo_root / fixture).resolve() if fixture and not fixture.is_absolute() else fixture
    result = run_diagnostic(
        registry_path,
        fixture_path=fixture_path,
        output_dir=run_path,
        live=live,
        max_per_source=max_per_source,
        window_hours=window_hours,
    )
    state_path = output_root / "state" / "event_state.json"
    queue_path = output_root / "review_queue.json"
    state = _read_json(state_path, {"schema_version": STATE_SCHEMA, "events": {}})
    queue = _read_json(queue_path, {"schema_version": QUEUE_SCHEMA, "items": []})
    previous_events: dict[str, dict[str, Any]] = dict(state.get("events") or {})
    existing_queue = {item["event_fingerprint"]: item for item in queue.get("items", [])}
    relationships: list[dict[str, Any]] = []
    queue_by_key = dict(existing_queue)
    for event in result["canonical_events"]:
        key = _event_key(event)
        previous = previous_events.get(key)
        relationship, reasons = _relationship(previous, event)
        merged = _merged_event(previous, event) if previous else dict(event)
        merged["first_seen"] = (previous or {}).get("first_seen") or observed_at
        merged["last_seen"] = observed_at
        previous_events[key] = merged
        relationships.append({
            "event_fingerprint": key,
            "canonical_event_id": merged.get("event_id"),
            "relationship": relationship,
            "reasons": reasons,
            "source_urls": sorted(_source_urls(merged)),
        })
        queue_by_key[key] = _queue_item(merged, relationship=relationship, reasons=reasons, run_id=run_id, run_path=run_path, existing=queue_by_key.get(key), observed_at=observed_at)
    queue["items"] = sorted(queue_by_key.values(), key=lambda item: (item["review_status"], item["first_seen"], item["canonical_event_id"]))
    _age_queue(queue, observed_at=observed_at, stale_after_days=stale_after_days)
    state.update({"schema_version": STATE_SCHEMA, "updated_at": observed_at, "events": previous_events})
    summary = _summary(run_id, result, relationships, queue["items"])
    for artifact in ("raw_candidates", "normalized_events", "canonical_events", "event_relationships", "map_readiness", "exclusions", "fetch_results"):
        _write_json(run_path / f"{artifact}.json", result.get(artifact, []))
    _write_json(run_path / "provider_health.json", result["run_manifest"]["provider_health"])
    _write_json(run_path / "collection_report.json", result["run_manifest"])
    _write_json(run_path / "monitor_relationships.json", relationships)
    _write_json(run_path / "operator_summary.json", summary)
    _write_json(state_path, state)
    _write_json(queue_path, queue)
    payload = {
        "ok": result["run_manifest"]["health"] != CollectionHealth.COLLECTION_FAILED.value,
        "status": "success" if result["run_manifest"]["health"] in {CollectionHealth.HEALTHY.value, CollectionHealth.LIMITED_SOURCE_UPDATE.value, CollectionHealth.COLLECTION_DEGRADED.value} else "collection_failed",
        "mode": MONITOR_MODE,
        "run_id": run_id,
        "run_dir": str(run_path),
        "review_queue_path": str(queue_path),
        "state_path": str(state_path),
        "operator_summary": summary,
        "publication_side_effects": False,
        "source_commit": source_commit,
        "public_artifacts_written": False,
        "audio_requested": False,
        "bluesky_requested": False,
    }
    _write_json(run_path / "monitor_receipt.json", payload)
    return (0 if payload["ok"] else 2), payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ICE Dispatch monitor-only collection.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--max-per-source", type=int, default=DEFAULT_MAX_PER_SOURCE)
    parser.add_argument("--observed-at")
    parser.add_argument("--stale-after-days", type=int, default=30)
    parser.add_argument("--enforce-production-preflight", action="store_true")
    parser.add_argument("--branch", default=PRODUCTION_BRANCH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code, payload = run_monitor(
        args.repo_root,
        registry=args.registry,
        output_root=args.output_root,
        live=not args.no_live,
        fixture=args.fixture,
        run_id=args.run_id,
        window_hours=args.window_hours,
        max_per_source=args.max_per_source,
        observed_at=args.observed_at,
        stale_after_days=args.stale_after_days,
        enforce_production_preflight=args.enforce_production_preflight,
        branch=args.branch,
    )
    print(json.dumps({
        "ok": payload["ok"],
        "status": payload["status"],
        "mode": payload["mode"],
        "run_id": payload["run_id"],
        "collection_health": payload["operator_summary"]["collection_health"],
        "raw_candidates": payload["operator_summary"]["raw_candidates"],
        "canonical_events": payload["operator_summary"]["canonical_events"],
        "new_events_since_prior_run": payload["operator_summary"]["new_events_since_prior_run"],
        "updates": payload["operator_summary"]["updates"],
        "duplicates": payload["operator_summary"]["duplicates"],
        "needs_corroboration": payload["operator_summary"]["needs_corroboration"],
        "review_queue_path": payload["review_queue_path"],
        "publication_side_effects": False,
    }, sort_keys=True))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
