from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.care_line_national_pipeline import REVIEW_ROOT, build_review_queue

SYNTHETIC_PREFIX = "synthetic-"
DISPATCHES = {"food-line", "care-line"}
CLEANUP_SCHEMA = "bluefern.external_agent_handoff_synthetic_cleanup.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _quarantine(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(destination)
    except OSError:
        shutil.copy2(source, destination)
        source.unlink()


def _retired_destination(retired_root: Path, category: str, source: Path, root: Path) -> Path:
    source_ref = source.relative_to(root).as_posix()
    short_name = f"{category[:1]}-{root.name[:1]}-{source.stem}-{hashlib.sha256(source_ref.encode('utf-8')).hexdigest()[:8]}.json"
    return retired_root / category / short_name


def _validate_id(run_id: str) -> None:
    if not run_id or run_id != run_id.strip() or not run_id.startswith(SYNTHETIC_PREFIX) or "/" in run_id or "\\" in run_id or ".." in run_id or "*" in run_id or "?" in run_id:
        raise ValueError("--agent-run-id must be one exact sanctioned synthetic ID")


def _matching_json(root: Path, pattern: str, run_id: str) -> list[Path]:
    matches: list[Path] = []
    for path in root.glob(pattern):
        payload = _load(path, {})
        declared = str(payload.get("agent_run_id") or payload.get("collection_run_id") or "") if isinstance(payload, dict) else ""
        if declared == run_id or path.stem == run_id or path.name.startswith(run_id + "-"):
            matches.append(path)
    return matches


def _plan(root: Path, dispatch: str, run_id: str) -> dict[str, Any]:
    if dispatch not in DISPATCHES:
        raise ValueError("unsupported dispatch")
    active: list[Path] = []
    audit: list[Path] = []
    if dispatch == "food-line":
        active.extend(_matching_json(root / "data/dispatches/food-line/agent-inbox", f"{run_id}.json", run_id))
        active.extend(_matching_json(root / "data/dispatches/food-line/agent-intake", f"*/{run_id}.json", run_id))
        active.extend(_matching_json(root / "data/dispatches/food-line/agent-intake/reports", f"*/{run_id}.json", run_id))
    else:
        registry_path = root / REVIEW_ROOT / "candidate-registry.json"
        registry = _load(registry_path, {"candidates": []})
        ids = [str(row.get("candidate_id")) for row in registry.get("candidates", []) if isinstance(row, dict) and str(row.get("collection_run_id") or "") == run_id]
        active.extend([registry_path] if ids else [])
        queue_path = root / REVIEW_ROOT / "current-review-queue.json"
        queue = _load(queue_path, {})
        if any(isinstance(row, dict) and row.get("candidate_id") in ids for row in queue.get("items", [])):
            active.append(queue_path)
    private = root / "data/private-agent-handoff"
    audit.extend(_matching_json(private / "inbox" / dispatch, f"{run_id}.json", run_id))
    audit.extend(_matching_json(private / "archive" / dispatch, f"*/{run_id}.json", run_id))
    audit.extend(_matching_json(private / "receipts" / dispatch, f"*/*{run_id}*.json", run_id))
    active_set = set(active)
    return {"active": sorted(active_set), "audit": sorted(set(audit) - active_set)}


def cleanup(root: Path, *, dispatch: str, agent_run_id: str, apply: bool) -> dict[str, Any]:
    _validate_id(agent_run_id)
    plan = _plan(root, dispatch, agent_run_id)
    active_refs = [path.relative_to(root).as_posix() for path in plan["active"]]
    audit_refs = [path.relative_to(root).as_posix() for path in plan["audit"]]
    if not apply:
        return {"schema_version": CLEANUP_SCHEMA, "dispatch": dispatch, "agent_run_id": agent_run_id, "synthetic": True, "dry_run": True, "active_artifacts_to_quarantine": active_refs, "audit_artifacts_to_preserve": audit_refs, "result": "DRY_RUN_NO_CHANGES"}
    retired_root = root / "data/private-agent-handoff/retired" / dispatch / agent_run_id
    moved: list[dict[str, str]] = []
    registry_path = root / REVIEW_ROOT / "candidate-registry.json"
    queue_path = root / REVIEW_ROOT / "current-review-queue.json"
    if dispatch == "care-line":
        for preserved in (registry_path, queue_path):
            if preserved.exists():
                destination = _retired_destination(retired_root, "active-before-cleanup", preserved, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = _sha256(preserved)
                shutil.copy2(str(preserved), str(destination))
                moved.append({"category": "audit", "source_ref": preserved.relative_to(root).as_posix(), "retired_ref": destination.relative_to(root).as_posix(), "sha256": digest})
    for category, paths in (("active", plan["active"]), ("audit", plan["audit"])):
        for source in paths:
            if dispatch == "care-line" and source in {registry_path, queue_path}:
                continue
            if not source.exists():
                continue
            destination = _retired_destination(retired_root, category, source, root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(destination.stem + "-" + _sha256(source)[:12] + destination.suffix)
            digest = _sha256(source)
            _quarantine(source, destination)
            moved.append({"category": category, "source_ref": source.relative_to(root).as_posix(), "retired_ref": destination.relative_to(root).as_posix(), "sha256": digest})
    if dispatch == "care-line":
        registry = _load(registry_path, {"schema_version": "bluefern.care_line.candidate_registry.v2", "candidates": []})
        registry["candidates"] = [row for row in registry.get("candidates", []) if not (isinstance(row, dict) and str(row.get("collection_run_id") or "") == agent_run_id)]
        registry["candidate_count"] = len(registry["candidates"])
        _write(registry_path, registry)
        _write(queue_path, build_review_queue(registry["candidates"], edition_date="synthetic-cleanup"))
    cleanup_record = {"schema_version": CLEANUP_SCHEMA, "dispatch": dispatch, "agent_run_id": agent_run_id, "cleanup_timestamp": _now(), "synthetic": True, "dry_run": False, "active_artifacts_removed": [row for row in moved if row["category"] == "active"], "audit_artifacts_preserved": [row for row in moved if row["category"] == "audit"], "result": "SYNTHETIC_RUN_RETIRED", "operator_tool": "cleanup_external_agent_synthetic_run.v1"}
    cleanup_path = root / "data/private-agent-handoff/cleanup" / dispatch / f"{agent_run_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}.json"
    _write(cleanup_path, cleanup_record)
    cleanup_record["cleanup_receipt_ref"] = cleanup_path.relative_to(root).as_posix()
    return cleanup_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or quarantine one exact synthetic external-agent run")
    parser.add_argument("--dispatch", required=True, choices=tuple(sorted(DISPATCHES)))
    parser.add_argument("--agent-run-id", required=True)
    parser.add_argument("--runner-root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = cleanup(args.runner_root.resolve(), dispatch=args.dispatch, agent_run_id=args.agent_run_id, apply=args.apply)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
