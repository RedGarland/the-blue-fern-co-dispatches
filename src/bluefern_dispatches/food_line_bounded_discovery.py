"""Durable, bounded orchestration for Food Line discovery expansion."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .food_line_agent_export import export_food_line_agent_run
from .food_line_discovery_expansion import (
    _dedupe_candidates,
    build_food_line_discovery_query_plan,
    validate_date,
)

PLAN_SCHEMA = "food_line_bounded_query_plan_v1"
RUN_STATE_SCHEMA = "food_line_bounded_run_state_v1"
PARTITION_SCHEMA = "food_line_bounded_partition_v1"
PLAN_REPORT_SCHEMA = "food_line_query_plan_report_v1"
PLAN_VERSION = "2026-08-01.1"
RUN_ROOT = Path("data/dispatches/food-line/discovery-runs")
QUALIFYING_STATUSES = {"completed", "completed_with_exclusions"}
TERMINAL_QUERY_STATUSES = {"completed", "failed", "timed_out", "cancelled"}

PROFILES: dict[str, dict[str, Any]] = {
    "daily-current": {
        "max_run_minutes": 30.0,
        "max_partition_minutes": 5.0,
        "max_query_seconds": 90.0,
        "per_request_timeout_seconds": 15,
        "max_retries": 1,
        "partition_size": 25,
        "max_workers": 2,
        "progress_interval_seconds": 30,
        "required_tiers": ["tier1"],
        "requested_tiers": ["tier1"],
        "required_coverage_threshold": 0.90,
        "direct_source_coverage_threshold": 0.75,
        "max_results_per_query": 3,
    },
    "supplemental": {
        "max_run_minutes": 30.0,
        "max_partition_minutes": 5.0,
        "max_query_seconds": 90.0,
        "per_request_timeout_seconds": 15,
        "max_retries": 1,
        "partition_size": 25,
        "max_workers": 2,
        "progress_interval_seconds": 30,
        "required_tiers": ["tier1"],
        "requested_tiers": ["tier2", "tier3"],
        "required_coverage_threshold": 0.90,
        "direct_source_coverage_threshold": 0.75,
        "max_results_per_query": 3,
    },
    "smoke": {
        "max_run_minutes": 5.0,
        "max_partition_minutes": 2.0,
        "max_query_seconds": 45.0,
        "per_request_timeout_seconds": 10,
        "max_retries": 0,
        "partition_size": 2,
        "max_workers": 1,
        "progress_interval_seconds": 10,
        "required_tiers": ["tier1"],
        "requested_tiers": ["tier1"],
        "required_coverage_threshold": 0.90,
        "direct_source_coverage_threshold": 0.50,
        "max_results_per_query": 2,
    },
}


class QueryExecutor(Protocol):
    def execute(self, query: dict[str, Any], *, run_dir: Path, options: dict[str, Any]) -> dict[str, Any]: ...
    def cancel_all(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 16) -> str:
    return f"{prefix}-{_sha256(payload)[:length]}"


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _normalized_query_text(value: str) -> str:
    text = re.sub(r"\b(after|before):\d{4}-\d{2}-\d{2}\b", "", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _query_domain(row: dict[str, Any]) -> str:
    domains = [str(value).strip().lower() for value in row.get("allowed_domains") or [] if str(value).strip()]
    return ",".join(sorted(domains)) or str(row.get("direct_source_url") or row.get("direct_source_feed_url") or "")


def _priority_tier(row: dict[str, Any]) -> str:
    channel = str(row.get("discovery_channel") or "")
    family = str(row.get("query_family") or "")
    scope = str(row.get("geographic_scope") or "")
    text = _normalized_query_text(str(row.get("query_text") or ""))
    if channel not in {"", "google_news_rss"}:
        return "tier1"
    if scope == "national" and (
        family in {"core_hunger", "pressure", "policy_program", "snap_state_notice", "institutional_update"}
        or any(token in text for token in ("snap", "ebt", "closure", "disaster", "benefit", "food assistance"))
    ):
        return "tier1"
    if family == "state_territory" or family in {
        "food_bank_provider",
        "feeding_america_affiliate",
        "school_meals_child_nutrition",
        "county_city_agenda",
        "united_way_211",
        "public_radio",
        "nonprofit_report",
    }:
        return "tier2"
    return "tier3"


def _geography(row: dict[str, Any]) -> str:
    return str(
        row.get("state_or_territory")
        or row.get("metro")
        or row.get("geographic_scope")
        or "national"
    ).strip()


def _query_identity(row: dict[str, Any], edition_date: str) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "edition_date": edition_date,
        "query_text": _normalized_query_text(str(row.get("query_text") or "")),
        "query_family": str(row.get("query_family") or ""),
        "geography": _geography(row),
        "domain": _query_domain(row),
        "channel": str(row.get("discovery_channel") or "google_news_rss"),
    }


def _semantic_signature(row: dict[str, Any]) -> str:
    text = _normalized_query_text(str(row.get("query_text") or ""))
    geography = _geography(row).lower()
    text = text.replace(geography, " {geo} ") if geography not in {"", "national"} else text
    replacements = {
        "food pantries": "food pantry",
        "food banks": "food bank",
        "benefits delayed": "benefit disruption",
        "benefits disrupted": "benefit disruption",
        "increased need": "rising demand",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def _immutable_plan_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "query_plan_version": manifest["query_plan_version"],
        "edition_date": manifest["edition_date"],
        "configuration_sha256": manifest["configuration_sha256"],
        "queries": [
            {key: value for key, value in query.items() if key != "execution"}
            for query in manifest["queries"]
        ],
        "partitions": [
            {key: value for key, value in partition.items() if key != "execution"}
            for partition in manifest["partitions"]
        ],
    }


def plan_checksum(manifest: dict[str, Any]) -> str:
    return _sha256(_immutable_plan_payload(manifest))


def build_bounded_query_plan(
    root: Path,
    edition_date: str,
    *,
    run_id: str,
    partition_size: int,
    required_tiers: list[str],
    requested_tiers: list[str],
    created_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    date_text = validate_date(edition_date)
    raw_rows = build_food_line_discovery_query_plan(root, date_text)
    unique: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    exact_duplicates: list[dict[str, str]] = []
    for row in raw_rows:
        identity = _query_identity(row, date_text)
        identity_key = _sha256(identity)
        if identity_key in seen:
            exact_duplicates.append({"duplicate_of": seen[identity_key], "query_text": str(row.get("query_text") or "")})
            continue
        query_id = _stable_id("q", identity)
        seen[identity_key] = query_id
        tier = _priority_tier(row)
        execution_status = "pending" if tier in requested_tiers else "deferred"
        unique.append(
            {
                "query_id": query_id,
                "query_family": str(row.get("query_family") or ""),
                "geography": _geography(row),
                "source_or_domain": _query_domain(row),
                "priority_tier": tier,
                "required": tier in required_tiers,
                "query": dict(row),
                "execution": {
                    "status": execution_status,
                    "attempt_count": 0,
                    "result_count": 0,
                    "failure_reason": "",
                },
            }
        )
    unique.sort(
        key=lambda item: (
            item["priority_tier"],
            0 if str(item["query"].get("discovery_channel") or "") not in {"", "google_news_rss"} else 1,
            item["query_family"],
            item["geography"],
            item["query_id"],
        )
    )
    partitions: list[dict[str, Any]] = []
    size = max(1, int(partition_size))
    for tier in ("tier1", "tier2", "tier3"):
        tier_rows = [row for row in unique if row["priority_tier"] == tier]
        for offset in range(0, len(tier_rows), size):
            query_ids = [row["query_id"] for row in tier_rows[offset : offset + size]]
            definition = {"tier": tier, "query_ids": query_ids, "plan_version": PLAN_VERSION}
            requested = tier in requested_tiers
            partitions.append(
                {
                    "partition_id": _stable_id("p", definition, 12),
                    "priority_tier": tier,
                    "required": tier in required_tiers,
                    "query_ids": query_ids,
                    "execution": {"status": "pending" if requested else "deferred", "attempt_count": 0},
                }
            )
    config_path = root / "data/dispatches/food-line/discovery_expansion_config.json"
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    semantic_groups: dict[str, list[str]] = defaultdict(list)
    for row in unique:
        semantic_groups[_semantic_signature(row["query"])].append(row["query_id"])
    potential_semantic_overlap = [ids for ids in semantic_groups.values() if len(ids) > 1]
    manifest = {
        "schema_version": PLAN_SCHEMA,
        "run_id": run_id,
        "edition_date": date_text,
        "created_at": created_at or _utc_now(),
        "query_plan_version": PLAN_VERSION,
        "configuration_sha256": config_sha,
        "original_query_count": len(raw_rows),
        "total_query_count": len(unique),
        "partition_count": len(partitions),
        "required_tiers": list(required_tiers),
        "requested_tiers": list(requested_tiers),
        "queries": unique,
        "partitions": partitions,
    }
    manifest["query_plan_sha256"] = plan_checksum(manifest)
    report = {
        "schema_version": PLAN_REPORT_SCHEMA,
        "run_id": run_id,
        "edition_date": date_text,
        "original_query_count": len(raw_rows),
        "exact_duplicates_removed": len(exact_duplicates),
        "exact_duplicate_details": exact_duplicates,
        "semantically_redundant_queries_consolidated": 0,
        "semantic_consolidation_note": "Potential overlaps are reported but retained until yield evidence supports consolidation.",
        "potential_semantic_overlap_group_count": len(potential_semantic_overlap),
        "potential_semantic_overlap_groups": potential_semantic_overlap[:50],
        "retained_unique_queries": len(unique),
        "tier_counts": dict(sorted(Counter(row["priority_tier"] for row in unique).items())),
        "query_family_counts": dict(sorted(Counter(row["query_family"] for row in unique).items())),
        "geography_counts": dict(sorted(Counter(row["geography"] for row in unique).items())),
        "geographic_scope_counts": dict(
            sorted(Counter(str(row["query"].get("geographic_scope") or "") for row in unique).items())
        ),
        "discovery_channel_counts": dict(
            sorted(Counter(str(row["query"].get("discovery_channel") or "google_news_rss") for row in unique).items())
        ),
        "source_type_counts": dict(
            sorted(Counter(str(row["query"].get("source_family") or "") for row in unique).items())
        ),
        "production_requested_count": sum(1 for row in unique if row["priority_tier"] in requested_tiers),
        "optional_deferred_count": sum(1 for row in unique if row["priority_tier"] not in requested_tiers),
        "expected_cost": {
            "request_timeout_seconds": "profile controlled",
            "worst_case_seconds_per_initial_request": "per-request timeout multiplied by bounded retry attempts",
        },
    }
    return manifest, report


def _terminate_process(process: subprocess.Popen[str], grace_seconds: float = 2.0) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(1.0, grace_seconds),
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        process.terminate()
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=max(0.1, grace_seconds))


class SubprocessQueryExecutor:
    """Runs each query in a killable child process with no unbounded queue."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._active: dict[int, subprocess.Popen[str]] = {}

    def execute(self, query: dict[str, Any], *, run_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
        query_id = str(query["query_id"])
        worker_dir = run_dir / "workers"
        input_path = worker_dir / f"{query_id}.input.json"
        output_path = worker_dir / f"{query_id}.output.json"
        atomic_write_json(
            input_path,
            {
                "root": str(self.root),
                "edition_date": options["edition_date"],
                "query_id": query_id,
                "query": query["query"],
                "options": options,
            },
        )
        command = [
            sys.executable,
            "-m",
            "bluefern_dispatches.food_line_bounded_worker",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
        environment = os.environ.copy()
        source_path = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
        popen_options: dict[str, Any] = {
            "cwd": self.root,
            "env": environment,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = subprocess.Popen(command, **popen_options)
        with self._lock:
            self._active[process.pid] = process
        try:
            try:
                stdout, stderr = process.communicate(timeout=max(1.0, float(options["max_query_seconds"])))
            except subprocess.TimeoutExpired:
                _terminate_process(process)
                return {
                    "query_id": query_id,
                    "status": "timed_out",
                    "error": f"query exceeded {options['max_query_seconds']} seconds",
                    "candidates": [],
                    "query_rows": [],
                    "summary": {},
                    "worker_pid": process.pid,
                }
            if output_path.exists():
                result = _read_json(output_path)
            else:
                result = {
                    "query_id": query_id,
                    "status": "failed",
                    "error": (stderr or stdout or f"worker exited {process.returncode}").strip()[:2000],
                    "candidates": [],
                    "query_rows": [],
                    "summary": {},
                }
            result["worker_pid"] = process.pid
            result["worker_returncode"] = process.returncode
            return result
        except BaseException:
            _terminate_process(process)
            raise
        finally:
            with self._lock:
                self._active.pop(process.pid, None)
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def cancel_all(self) -> None:
        with self._lock:
            active = list(self._active.values())
        for process in active:
            _terminate_process(process)

    def active_pids(self) -> list[int]:
        with self._lock:
            return sorted(pid for pid, process in self._active.items() if process.poll() is None)


def _run_dir(root: Path, edition_date: str, run_id: str) -> Path:
    return root / RUN_ROOT / validate_date(edition_date) / run_id


def find_run_dir(root: Path, run_id: str, edition_date: str | None = None) -> Path:
    if edition_date:
        candidate = _run_dir(root, edition_date, run_id)
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"run not found: {candidate}")
    matches = list((root / RUN_ROOT).glob(f"*/{run_id}")) if (root / RUN_ROOT).exists() else []
    if len(matches) != 1:
        raise FileNotFoundError(f"expected exactly one run named {run_id}; found {len(matches)}")
    return matches[0]


def _new_run_id(edition_date: str, now: str | None = None) -> str:
    stamp = (now or _utc_now()).replace("-", "").replace(":", "").replace("+00:00", "Z")[:15]
    return f"food-line-{edition_date.replace('-', '')}-{stamp}-{uuid.uuid4().hex[:8]}"


def _query_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["query_id"]): row for row in manifest["queries"]}


def _partition_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["partition_id"]): row for row in manifest["partitions"]}


def _initial_run_state(
    manifest: dict[str, Any],
    *,
    run_dir: Path,
    options: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    deadline_epoch = time.time() + float(options["max_run_minutes"]) * 60.0
    requested_queries = [
        row for row in manifest["queries"] if row["priority_tier"] in manifest["requested_tiers"]
    ]
    requested_partitions = [
        row for row in manifest["partitions"] if row["priority_tier"] in manifest["requested_tiers"]
    ]
    return {
        "schema_version": RUN_STATE_SCHEMA,
        "run_id": manifest["run_id"],
        "edition_date": manifest["edition_date"],
        "query_plan_version": manifest["query_plan_version"],
        "query_plan_sha256": manifest["query_plan_sha256"],
        "query_plan_path": str(run_dir / "query-plan.json"),
        "plan_report_path": str(run_dir / "query-plan-report.json"),
        "started_at": now,
        "last_checkpoint_at": now,
        "completed_at": None,
        "status": "planned",
        "process_id": os.getpid(),
        "deadline": datetime.fromtimestamp(deadline_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
        "deadline_epoch": deadline_epoch,
        "options": options,
        "partitions_total": len(requested_partitions),
        "partitions_completed": 0,
        "queries_total": len(requested_queries),
        "queries_attempted": 0,
        "queries_completed": 0,
        "queries_failed": 0,
        "queries_timed_out": 0,
        "candidates_discovered": 0,
        "candidates_accepted": 0,
        "exclusions_by_reason": {},
        "last_completed_partition": "",
        "resumable": True,
        "resume_count": 0,
        "resume_timestamps": [],
        "final_error": "",
        "timeout_reason": "",
        "coverage": {},
        "agent_export": {"status": "not_attempted", "reason": "collection not complete"},
        "progress_path": str(run_dir / "progress.json"),
    }


def _checkpoint(run_dir: Path, manifest: dict[str, Any], state: dict[str, Any], now: str | None = None) -> None:
    state["last_checkpoint_at"] = now or _utc_now()
    atomic_write_json(run_dir / "query-plan.json", manifest)
    atomic_write_json(run_dir / "run-state.json", state)
    atomic_write_json(
        run_dir / "progress.json",
        {
            "schema_version": "food_line_bounded_progress_v1",
            "run_id": state["run_id"],
            "status": state["status"],
            "last_checkpoint_at": state["last_checkpoint_at"],
            "partitions_completed": state["partitions_completed"],
            "partitions_total": state["partitions_total"],
            "queries_completed": state["queries_completed"],
            "queries_total": state["queries_total"],
            "queries_failed": state["queries_failed"],
            "queries_timed_out": state["queries_timed_out"],
            "candidates_discovered": state["candidates_discovered"],
            "remaining_run_seconds": max(0, int(float(state["deadline_epoch"]) - time.time())),
            "last_completed_partition": state["last_completed_partition"],
        },
    )


def _coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    required = [row for row in manifest["queries"] if bool(row["required"])]
    required_terminal = [row for row in required if row["execution"]["status"] in TERMINAL_QUERY_STATUSES]
    required_completed = [row for row in required if row["execution"]["status"] == "completed"]
    direct = [
        row for row in required
        if str(row["query"].get("discovery_channel") or "") not in {"", "google_news_rss"}
    ]
    direct_completed = [row for row in direct if row["execution"]["status"] == "completed"]
    return {
        "required_query_count": len(required),
        "required_terminal_count": len(required_terminal),
        "required_completed_count": len(required_completed),
        "required_terminal_ratio": len(required_terminal) / len(required) if required else 1.0,
        "required_success_ratio": len(required_completed) / len(required) if required else 1.0,
        "direct_required_count": len(direct),
        "direct_completed_count": len(direct_completed),
        "direct_success_ratio": len(direct_completed) / len(direct) if direct else 1.0,
        "deferred_optional_query_count": sum(
            1 for row in manifest["queries"] if row["execution"]["status"] == "deferred"
        ),
    }


def _refresh_counts(manifest: dict[str, Any], state: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    requested_tiers = set(manifest["requested_tiers"])
    queries = [row for row in manifest["queries"] if row["priority_tier"] in requested_tiers]
    partitions = [row for row in manifest["partitions"] if row["priority_tier"] in requested_tiers]
    state["queries_total"] = len(queries)
    state["queries_attempted"] = sum(1 for row in queries if int(row["execution"]["attempt_count"]) > 0)
    state["queries_completed"] = sum(1 for row in queries if row["execution"]["status"] == "completed")
    state["queries_failed"] = sum(1 for row in queries if row["execution"]["status"] == "failed")
    state["queries_timed_out"] = sum(1 for row in queries if row["execution"]["status"] == "timed_out")
    state["partitions_total"] = len(partitions)
    state["partitions_completed"] = sum(
        1 for row in partitions if row["execution"]["status"] in {"completed", "completed_with_exclusions"}
    )
    state["candidates_discovered"] = len(candidates)
    state["candidates_accepted"] = sum(1 for row in candidates if bool(row.get("public_claim_eligible")))
    exclusions = Counter()
    for row in candidates:
        if bool(row.get("public_claim_eligible")):
            continue
        blockers = [str(value) for value in row.get("public_claim_blockers") or [] if str(value)]
        exclusions[blockers[0] if blockers else str(row.get("exclusion_reason") or "not_exportable")] += 1
    state["exclusions_by_reason"] = dict(sorted(exclusions.items()))


def _partition_artifact_path(run_dir: Path, partition_id: str, attempt: int) -> Path:
    suffix = "" if attempt == 1 else f"-attempt-{attempt}"
    return run_dir / "partitions" / f"{partition_id}{suffix}.json"


def _execute_partition(
    partition: dict[str, Any],
    *,
    manifest: dict[str, Any],
    run_dir: Path,
    state: dict[str, Any],
    executor: QueryExecutor,
    options: dict[str, Any],
    cancel_event: threading.Event,
) -> dict[str, Any]:
    query_by_id = _query_index(manifest)
    partition["execution"]["attempt_count"] = int(partition["execution"].get("attempt_count") or 0) + 1
    partition_attempt = int(partition["execution"]["attempt_count"])
    started_epoch = time.time()
    partition_deadline = min(
        float(state["deadline_epoch"]),
        started_epoch + float(options["max_partition_minutes"]) * 60.0,
    )
    planned = [str(value) for value in partition["query_ids"]]
    eligible = [
        query_by_id[query_id]
        for query_id in planned
        if query_by_id[query_id]["execution"]["status"] != "completed"
    ]
    attempted: list[str] = []
    completed: list[str] = []
    failures: list[dict[str, str]] = []
    query_result_metadata: list[dict[str, Any]] = []
    retries = 0
    candidates: list[dict[str, Any]] = []
    progress_interval = max(1.0, float(options["progress_interval_seconds"]))
    last_progress = 0.0
    max_workers = max(1, int(options["max_workers"]))
    for offset in range(0, len(eligible), max_workers):
        if cancel_event.is_set() or time.time() >= partition_deadline:
            break
        wave = eligible[offset : offset + max_workers]
        futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="food-line-query")
        try:
            for query in wave:
                query["execution"]["attempt_count"] = int(query["execution"].get("attempt_count") or 0) + 1
                if int(query["execution"]["attempt_count"]) > 1:
                    retries += 1
                query["execution"]["status"] = "running"
                attempted.append(str(query["query_id"]))
                futures[pool.submit(executor.execute, query, run_dir=run_dir, options=options)] = query
            remaining = max(0.01, partition_deadline - time.time())
            try:
                for future in as_completed(futures, timeout=remaining):
                    query = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "query_id": query["query_id"],
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "candidates": [],
                            "summary": {},
                        }
                    status = str(result.get("status") or "failed")
                    if status not in TERMINAL_QUERY_STATUSES:
                        status = "failed"
                    query["execution"]["status"] = status
                    query["execution"]["result_count"] = len(list(result.get("candidates") or []))
                    query["execution"]["failure_reason"] = str(result.get("error") or "")
                    query["execution"]["last_attempt_at"] = _utc_now()
                    if status == "completed":
                        completed.append(str(query["query_id"]))
                    else:
                        failures.append({"query_id": str(query["query_id"]), "reason": query["execution"]["failure_reason"] or status})
                    candidates.extend(list(result.get("candidates") or []))
                    query_result_metadata.append(
                        {
                            "query_id": str(query["query_id"]),
                            "status": status,
                            "error": str(result.get("error") or ""),
                            "summary": dict(result.get("summary") or {}),
                            "query_rows": list(result.get("query_rows") or []),
                            "worker_pid": result.get("worker_pid"),
                            "worker_returncode": result.get("worker_returncode"),
                        }
                    )
                    now_epoch = time.time()
                    if now_epoch - last_progress >= progress_interval:
                        print(
                            f"run={state['run_id']} partition={partition['partition_id']} "
                            f"queries={state['queries_completed'] + len(completed)}/{state['queries_total']} "
                            f"failures={state['queries_failed'] + len(failures)} candidates={state['candidates_discovered'] + len(candidates)} "
                            f"remaining_seconds={max(0, int(float(state['deadline_epoch']) - now_epoch))}",
                            flush=True,
                        )
                        atomic_write_json(
                            run_dir / "progress.json",
                            {
                                "schema_version": "food_line_bounded_progress_v1",
                                "run_id": state["run_id"],
                                "status": "running",
                                "last_checkpoint_at": _utc_now(),
                                "current_partition": partition["partition_id"],
                                "partition_queries_completed": len(completed),
                                "partition_queries_failed": len(failures),
                                "partition_candidates_discovered": len(candidates),
                                "queries_total": state["queries_total"],
                                "remaining_run_seconds": max(
                                    0, int(float(state["deadline_epoch"]) - now_epoch)
                                ),
                                "checkpoint_path": str(run_dir / "run-state.json"),
                            },
                        )
                        last_progress = now_epoch
            except TimeoutError:
                cancel_event.set()
                executor.cancel_all()
        finally:
            for future, query in futures.items():
                if not future.done():
                    future.cancel()
                    query["execution"]["status"] = "timed_out"
                    query["execution"]["failure_reason"] = "partition deadline reached"
                    failures.append({"query_id": str(query["query_id"]), "reason": "partition deadline reached"})
            pool.shutdown(wait=True, cancel_futures=True)
        if cancel_event.is_set():
            break
    remaining_ids = [
        query_id for query_id in planned
        if query_by_id[query_id]["execution"]["status"] not in TERMINAL_QUERY_STATUSES
    ]
    deadline_hit = time.time() >= partition_deadline
    if deadline_hit:
        for query_id in remaining_ids:
            if query_by_id[query_id]["execution"]["status"] == "running":
                query_by_id[query_id]["execution"]["status"] = "timed_out"
                query_by_id[query_id]["execution"]["failure_reason"] = "partition deadline reached"
    if cancel_event.is_set() or deadline_hit:
        status = "timed_out"
    elif remaining_ids:
        status = "partial"
    elif failures:
        status = "completed_with_exclusions"
    else:
        status = "completed"
    partition["execution"]["status"] = status
    partition["execution"]["last_completed_at"] = _utc_now()
    artifact = {
        "schema_version": PARTITION_SCHEMA,
        "run_id": state["run_id"],
        "partition_id": partition["partition_id"],
        "partition_attempt": partition_attempt,
        "priority_tier": partition["priority_tier"],
        "required": bool(partition["required"]),
        "planned_query_ids": planned,
        "attempted_query_ids": attempted,
        "completed_query_ids": completed,
        "failures": failures,
        "query_result_metadata": query_result_metadata,
        "retry_count": retries,
        "candidate_ids": sorted({str(row.get("candidate_id") or "") for row in candidates if row.get("candidate_id")}),
        "candidates": candidates,
        "exclusion_counts": dict(
            sorted(
                Counter(
                    str((row.get("public_claim_blockers") or [row.get("exclusion_reason") or "not_exportable"])[0])
                    for row in candidates
                    if not bool(row.get("public_claim_eligible"))
                ).items()
            )
        ),
        "started_at": datetime.fromtimestamp(started_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
        "completed_at": _utc_now(),
        "duration_seconds": round(time.time() - started_epoch, 3),
        "status": status,
    }
    artifact["checksum"] = _sha256(artifact)
    artifact_path = _partition_artifact_path(run_dir, str(partition["partition_id"]), partition_attempt)
    atomic_write_json(artifact_path, artifact)
    partition["execution"].setdefault("artifacts", []).append(str(artifact_path))
    return artifact


def _collect_candidates(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((run_dir / "partitions").glob("*.json")) if (run_dir / "partitions").exists() else []:
        artifact = _read_json(path)
        expected = str(artifact.pop("checksum", ""))
        if expected and _sha256(artifact) != expected:
            raise ValueError(f"partition checksum mismatch: {path}")
        rows.extend([dict(row) for row in artifact.get("candidates") or [] if isinstance(row, dict)])
    by_candidate: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("candidate_id") or _sha256(row))
        current = by_candidate.get(key)
        if current is None or bool(row.get("public_claim_eligible")) > bool(current.get("public_claim_eligible")):
            by_candidate[key] = row
    candidates = list(by_candidate.values())
    _dedupe_candidates(candidates)
    for row in candidates:
        if row.get("duplicate_of"):
            row["classification_status"] = "duplicate"
            row["public_claim_eligible"] = False
            row["public_claim_blockers"] = sorted(
                set([*list(row.get("public_claim_blockers") or []), "duplicate"])
            )
    candidates.sort(key=lambda row: str(row.get("candidate_id") or ""))
    return candidates


def _finalize_status(manifest: dict[str, Any], state: dict[str, Any]) -> str:
    coverage = _coverage(manifest)
    state["coverage"] = coverage
    options = state["options"]
    if coverage["required_terminal_ratio"] < 1.0:
        return "partial"
    if coverage["required_success_ratio"] < float(options["required_coverage_threshold"]):
        return "partial"
    if coverage["direct_success_ratio"] < float(options["direct_source_coverage_threshold"]):
        return "partial"
    if state["queries_failed"] or state["queries_timed_out"]:
        return "completed_with_exclusions"
    return "completed"


def _activate_tiers(manifest: dict[str, Any], tiers: list[str]) -> None:
    requested = set(manifest["requested_tiers"])
    requested.update(tiers)
    manifest["requested_tiers"] = sorted(requested)
    for query in manifest["queries"]:
        if query["priority_tier"] in requested and query["execution"]["status"] == "deferred":
            query["execution"]["status"] = "pending"
    for partition in manifest["partitions"]:
        if partition["priority_tier"] in requested and partition["execution"]["status"] == "deferred":
            partition["execution"]["status"] = "pending"


def _next_action(state: dict[str, Any]) -> str:
    run_id = state["run_id"]
    date_text = state["edition_date"]
    if state["status"] in {"partial", "timed_out", "failed", "cancelled"}:
        return (
            f"python scripts/run_food_line_discovery_expansion.py --date {date_text} "
            f"--resume-run {run_id} --export-agent-inbox"
        )
    if state["status"] in QUALIFYING_STATUSES and int(state["coverage"].get("deferred_optional_query_count") or 0):
        return (
            f"python scripts/run_food_line_discovery_expansion.py --date {date_text} "
            f"--resume-run {run_id} --profile supplemental"
        )
    return "No collection action required."


def profile_options(profile: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown bounded profile: {profile}")
    options = dict(PROFILES[profile])
    for key, value in (overrides or {}).items():
        if value is not None:
            options[key] = value
    options["profile"] = profile
    for key in ("max_run_minutes", "max_partition_minutes", "max_query_seconds"):
        if float(options[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("per_request_timeout_seconds", "partition_size", "max_workers", "progress_interval_seconds"):
        if int(options[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    if int(options["max_retries"]) < 0:
        raise ValueError("max_retries cannot be negative")
    return options


def run_bounded_food_line_discovery(
    root: Path,
    edition_date: str | None,
    *,
    profile: str = "daily-current",
    run_id: str | None = None,
    resume_run: str | None = None,
    priority_tiers: list[str] | None = None,
    export_agent_inbox: bool = False,
    agent_inbox_dir: Path | None = None,
    max_partitions: int | None = None,
    overrides: dict[str, Any] | None = None,
    executor: QueryExecutor | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    options = profile_options(profile, overrides)
    options["max_partitions"] = max_partitions
    options["edition_date"] = validate_date(edition_date) if edition_date else ""
    now = _utc_now()
    if resume_run:
        run_dir = find_run_dir(root, resume_run, edition_date)
        manifest = _read_json(run_dir / "query-plan.json")
        state = _read_json(run_dir / "run-state.json")
        date_text = str(manifest["edition_date"])
        options["edition_date"] = date_text
        if edition_date and validate_date(edition_date) != date_text:
            raise ValueError("resume edition date does not match immutable plan")
        if manifest.get("query_plan_version") != PLAN_VERSION:
            raise ValueError("resume query-plan version is incompatible")
        if plan_checksum(manifest) != manifest.get("query_plan_sha256"):
            raise ValueError("resume query-plan checksum mismatch")
        config_path = root / "data/dispatches/food-line/discovery_expansion_config.json"
        if hashlib.sha256(config_path.read_bytes()).hexdigest() != manifest.get("configuration_sha256"):
            raise ValueError("resume configuration is incompatible with the immutable plan")
        tiers = priority_tiers or list(PROFILES[profile]["requested_tiers"])
        _activate_tiers(manifest, tiers)
        state["resume_count"] = int(state.get("resume_count") or 0) + 1
        state.setdefault("resume_timestamps", []).append(now)
        state["status"] = "resumed"
        state["resumable"] = True
        state["final_error"] = ""
        state["timeout_reason"] = ""
        state["options"] = options
        state["deadline_epoch"] = time.time() + float(options["max_run_minutes"]) * 60.0
        state["deadline"] = datetime.fromtimestamp(
            float(state["deadline_epoch"]), timezone.utc
        ).isoformat().replace("+00:00", "Z")
        _checkpoint(run_dir, manifest, state, now)
    else:
        if not edition_date:
            raise ValueError("--date is required for a new bounded run")
        date_text = validate_date(edition_date)
        actual_run_id = run_id or _new_run_id(date_text, now)
        run_dir = _run_dir(root, date_text, actual_run_id)
        if run_dir.exists():
            raise FileExistsError(f"run already exists; use --resume-run: {actual_run_id}")
        tiers = priority_tiers or list(options["requested_tiers"])
        manifest, plan_report = build_bounded_query_plan(
            root,
            date_text,
            run_id=actual_run_id,
            partition_size=int(options["partition_size"]),
            required_tiers=list(options["required_tiers"]),
            requested_tiers=tiers,
            created_at=now,
        )
        run_dir.mkdir(parents=True, exist_ok=False)
        atomic_write_json(run_dir / "query-plan.json", manifest)
        atomic_write_json(run_dir / "query-plan-report.json", plan_report)
        state = _initial_run_state(manifest, run_dir=run_dir, options=options, now=now)
        atomic_write_json(run_dir / "run-state.json", state)
        _checkpoint(run_dir, manifest, state, now)
    query_executor = executor or SubprocessQueryExecutor(root)
    cancel_event = threading.Event()
    old_handlers: dict[int, Any] = {}

    def _cancel(signum: int, frame: Any) -> None:
        del signum, frame
        cancel_event.set()
        query_executor.cancel_all()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _cancel)
    state["status"] = "running"
    _checkpoint(run_dir, manifest, state)
    selected_query_index = _query_index(manifest)
    selected_partitions = [
        row
        for row in manifest["partitions"]
        if row["priority_tier"] in set(manifest["requested_tiers"])
        and any(
            selected_query_index[query_id]["execution"]["status"] != "completed"
            for query_id in row["query_ids"]
        )
    ]
    if max_partitions is not None:
        selected_partitions = selected_partitions[: max(0, int(max_partitions))]
    try:
        for position, partition in enumerate(selected_partitions, start=1):
            if cancel_event.is_set():
                break
            if time.time() >= float(state["deadline_epoch"]):
                state["status"] = "timed_out"
                state["timeout_reason"] = "whole-run deadline reached before next partition"
                cancel_event.set()
                break
            print(
                f"run={state['run_id']} partition={position}/{len(selected_partitions)} "
                f"id={partition['partition_id']} tier={partition['priority_tier']} "
                f"checkpoint={run_dir / 'run-state.json'}",
                flush=True,
            )
            artifact = _execute_partition(
                partition,
                manifest=manifest,
                run_dir=run_dir,
                state=state,
                executor=query_executor,
                options=options,
                cancel_event=cancel_event,
            )
            state["last_completed_partition"] = str(partition["partition_id"])
            candidates = _collect_candidates(run_dir)
            _refresh_counts(manifest, state, candidates)
            if artifact["status"] == "timed_out":
                if time.time() >= float(state["deadline_epoch"]):
                    state["status"] = "timed_out"
                    state["timeout_reason"] = "whole-run deadline reached"
                else:
                    state["status"] = "partial"
                    state["timeout_reason"] = "partition deadline reached"
                _checkpoint(run_dir, manifest, state)
                break
            _checkpoint(run_dir, manifest, state)
        candidates = _collect_candidates(run_dir)
        _refresh_counts(manifest, state, candidates)
        if cancel_event.is_set() and state["status"] not in {"timed_out", "partial"}:
            state["status"] = "cancelled"
            state["final_error"] = "cancellation requested"
        elif state["status"] == "running":
            state["status"] = _finalize_status(manifest, state)
        state["coverage"] = _coverage(manifest)
        atomic_write_json(run_dir / "final-candidates.json", candidates)
        final_audit = {
            "schema_version": "food_line_bounded_discovery_audit_v1",
            "run_id": state["run_id"],
            "edition_date": state["edition_date"],
            "status": state["status"],
            "query_plan_sha256": state["query_plan_sha256"],
            "coverage": state["coverage"],
            "counts": {
                "queries_total": state["queries_total"],
                "queries_completed": state["queries_completed"],
                "queries_failed": state["queries_failed"],
                "queries_timed_out": state["queries_timed_out"],
                "candidate_count": len(candidates),
                "accepted_candidate_count": state["candidates_accepted"],
                "exclusions_by_reason": state["exclusions_by_reason"],
            },
            "deferred_tiers": sorted(
                {
                    str(row["priority_tier"])
                    for row in manifest["queries"]
                    if row["execution"]["status"] == "deferred"
                }
            ),
        }
        atomic_write_json(run_dir / "final-audit.json", final_audit)
        if export_agent_inbox and state["status"] in QUALIFYING_STATUSES:
            coverage_notes = (
                f"Bounded discovery run {state['run_id']} status={state['status']}; "
                f"required success={state['coverage']['required_success_ratio']:.1%}; "
                f"direct-source success={state['coverage']['direct_success_ratio']:.1%}; "
                f"deferred optional queries={state['coverage']['deferred_optional_query_count']}."
            )
            state["agent_export"] = export_food_line_agent_run(
                candidates,
                edition_date=state["edition_date"],
                destination=agent_inbox_dir or root / "data/dispatches/food-line/agent-inbox",
                started_at=state["started_at"],
                completed_at=_utc_now(),
                agent_run_id=state["run_id"],
                run_slug=state["run_id"],
                coverage_notes=coverage_notes,
            )
        elif export_agent_inbox:
            state["agent_export"] = {
                "status": "blocked_incomplete_collection",
                "reason": f"run status {state['status']} is not export-qualified",
                "resumable": bool(state["resumable"]),
                "resume_command": _next_action(state),
            }
        else:
            state["agent_export"] = {
                "status": "not_requested",
                "reason": "export flag not supplied",
            }
        state["completed_at"] = _utc_now()
        state["resumable"] = state["status"] in {"partial", "timed_out", "cancelled", "failed"} or bool(
            state["coverage"]["deferred_optional_query_count"]
        )
        state["next_action"] = _next_action(state)
        _checkpoint(run_dir, manifest, state)
        return state
    except BaseException as exc:
        query_executor.cancel_all()
        state["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        state["final_error"] = f"{type(exc).__name__}: {exc}"
        state["completed_at"] = _utc_now()
        state["resumable"] = True
        state["agent_export"] = {
            "status": "blocked_incomplete_collection",
            "reason": state["final_error"],
            "resumable": True,
        }
        state["next_action"] = _next_action(state)
        _checkpoint(run_dir, manifest, state)
        if isinstance(exc, KeyboardInterrupt):
            return state
        raise
    finally:
        query_executor.cancel_all()
        if old_handlers:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


def inspect_bounded_run(root: Path, run_id: str, edition_date: str | None = None) -> dict[str, Any]:
    run_dir = find_run_dir(root.resolve(), run_id, edition_date)
    state = _read_json(run_dir / "run-state.json")
    manifest = _read_json(run_dir / "query-plan.json")
    if plan_checksum(manifest) != manifest.get("query_plan_sha256"):
        raise ValueError("query-plan checksum mismatch")
    remaining = [
        row["partition_id"]
        for row in manifest["partitions"]
        if row["priority_tier"] in set(manifest["requested_tiers"])
        and row["execution"]["status"] not in {"completed", "completed_with_exclusions"}
    ]
    return {
        "run_id": state["run_id"],
        "edition_date": state["edition_date"],
        "status": state["status"],
        "elapsed_seconds": max(
            0,
            int(
                (
                    datetime.fromisoformat(str(state.get("completed_at") or _utc_now()).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(state["started_at"]).replace("Z", "+00:00"))
                ).total_seconds()
            ),
        ),
        "coverage": state.get("coverage") or _coverage(manifest),
        "remaining_partition_count": len(remaining),
        "remaining_partitions": remaining,
        "failures": {
            "queries_failed": state["queries_failed"],
            "queries_timed_out": state["queries_timed_out"],
            "final_error": state.get("final_error") or "",
            "timeout_reason": state.get("timeout_reason") or "",
        },
        "candidates_discovered": state["candidates_discovered"],
        "exclusions_by_reason": state["exclusions_by_reason"],
        "agent_export": state["agent_export"],
        "resumable": bool(state["resumable"]),
        "next_action": state.get("next_action") or _next_action(state),
        "run_state_path": str(run_dir / "run-state.json"),
    }
