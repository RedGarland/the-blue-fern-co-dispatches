from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import tempfile
import subprocess
import sys
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_expansion import (
    build_food_line_discovery_query_plan,
    main as discovery_main,
    run_food_line_discovery_expansion,
)


RUN_STATE_SCHEMA = "food_line_bounded_run_state_v1"
QUERY_PLAN_SCHEMA = "food_line_bounded_query_plan_v1"
NONFATAL_COMPLETED_WITH_EXCLUSIONS = "completed_with_exclusions"
INCOMPLETE_CAPPED_STATUS = "incomplete_capped"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _terminal_status(result: dict[str, object]) -> str:
    raw_status = str(result.get("status") or "").strip()
    if bool(result.get("timed_out")) or raw_status == "timed_out":
        return "timeout"
    # A bounded run that intentionally left plan rows deferred is not a
    # completed zero-result run.  Preserve the durable state so the scheduler
    # can keep the manual continuation gate closed.
    if raw_status == INCOMPLETE_CAPPED_STATUS:
        return INCOMPLETE_CAPPED_STATUS
    if bool(result.get("ok")) and raw_status == NONFATAL_COMPLETED_WITH_EXCLUSIONS:
        return NONFATAL_COMPLETED_WITH_EXCLUSIONS
    if not bool(result.get("ok")):
        if raw_status in {"child_process_failure", "malformed_child_output", "collection_failure"}:
            return raw_status
        return "collection_failure"
    candidate_count = int(result.get("candidate_count") or result.get("public_eligible_candidate_count") or 0)
    return "zero_result_completion" if candidate_count <= 0 else "success"


def _terminal_contract_result(
    result: dict[str, object],
    *,
    edition_date: str,
    run_id: str,
    timed_out: bool | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    payload = dict(result)
    candidate_count = int(payload.get("candidate_count") or payload.get("public_eligible_candidate_count") or 0)
    source_count = int(
        payload.get("source_count")
        or payload.get("direct_source_count")
        or payload.get("provider_pressure_count")
        or payload.get("official_pressure_count")
        or 0
    )
    if timed_out is None:
        timed_out = bool(payload.get("timed_out")) or str(payload.get("status") or "") == "timed_out"
    status = _terminal_status({**payload, "timed_out": timed_out})
    incomplete_capped = status == INCOMPLETE_CAPPED_STATUS
    if error_type is None:
        error_type = str(payload.get("error_type") or "").strip() or None
    if error_message is None:
        error_message = str(payload.get("error_message") or payload.get("error") or "").strip() or None
    contract_ok = bool(payload.get("ok")) and not incomplete_capped
    if not contract_ok and not error_type:
        error_type = status
    if incomplete_capped and not error_message:
        error_message = "plan-bound continuation required before intake"
    elif not contract_ok and not error_message:
        error_message = status.replace("_", " ")
    if status == NONFATAL_COMPLETED_WITH_EXCLUSIONS and bool(payload.get("ok")):
        payload.setdefault("child_outcome_classification", NONFATAL_COMPLETED_WITH_EXCLUSIONS)
        payload.setdefault("fatal_error", "")
    return {
        **payload,
        "ok": contract_ok,
        "status": status,
        "edition_date": edition_date,
        "run_id": run_id,
        "source_count": source_count,
        "candidate_count": candidate_count,
        "timed_out": bool(timed_out),
        "error_type": error_type,
        "error_message": error_message,
        "warnings": list(warnings if warnings is not None else payload.get("warnings") or []),
    }


def _export_allowed(root: Path, edition_date: str, run_id: str, result: dict[str, object]) -> bool:
    if not bool(result.get("ok")):
        return False
    query_plan = build_food_line_discovery_query_plan(root, edition_date)
    state, _ = _bounded_state_from_result(
        root=root,
        edition_date=edition_date,
        run_id=run_id,
        query_plan=query_plan,
        result=result,
    )
    if state["status"] == INCOMPLETE_CAPPED_STATUS or state["counter_integrity_status"] != "valid":
        return False
    if state["coverage"]["required_success_ratio"] < state["options"]["required_coverage_threshold"]:
        return False
    if state["coverage"]["direct_success_ratio"] < state["options"]["direct_source_coverage_threshold"]:
        return False
    return True


def _result_counters_valid(result: dict[str, object]) -> bool:
    required = ("queries_attempted", "queries_succeeded", "queries_failed", "queries_timed_out")
    if any(result.get(key) is None for key in required):
        return False
    attempted = int(result["queries_attempted"])
    if attempted != int(result["queries_succeeded"]) + int(result["queries_failed"]) + int(result["queries_timed_out"]):
        return False
    direct_attempted = result.get("direct_source_fetch_attempt_count")
    direct_succeeded = result.get("direct_source_fetch_success_count")
    if direct_attempted is None or direct_succeeded is None:
        return False
    return 0 <= int(direct_succeeded) <= int(direct_attempted)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _short_run_tag(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return digest[:10]


def _export_agent_inbox(
    root: Path,
    *,
    edition_date: str,
    run_id: str,
    result: dict[str, object],
    agent_inbox_dir: str | None,
) -> dict[str, object]:
    inbox_dir = Path(agent_inbox_dir).resolve() if agent_inbox_dir else root / "status" / "food-line" / "runtime" / "agent-inbox"
    findings = list(result.get("candidates") or [])
    export_payload = {
        "schema_version": "food_line_source_watch_agent_export_v1",
        "agent_name": "Food Line Source Watch",
        "agent_run_id": run_id,
        "edition_date": edition_date,
        "started_at": result.get("task_started_at") or result.get("started_at") or "",
        "completed_at": result.get("task_completed_at") or result.get("completed_at") or "",
        "search_window": {"edition_date": edition_date},
        "findings": findings,
        "coverage_notes": "Exported source-watch findings for current Food Line intake.",
    }
    export_path = inbox_dir / f"food-line-source-watch-{edition_date}-{_short_run_tag(run_id)}.json"
    _atomic_write_json(export_path, export_payload)
    export_sha256 = hashlib.sha256(export_path.read_bytes()).hexdigest()
    return {"status": "success", "path": str(export_path), "sha256": export_sha256}


def _legacy_args_supplied(args: argparse.Namespace) -> bool:
    return any(
        (
            args.run_id,
            args.resume_run,
            args.status_run,
            args.profile is not None,
            args.max_run_minutes is not None,
            args.export_agent_inbox,
            args.agent_inbox_dir is not None,
        )
    )


def _terminate_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        process_query_limited_information = 0x1000
        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information | process_terminate, False, pid)
        if handle:
            try:
                if ctypes.windll.kernel32.TerminateProcess(handle, 1):
                    return
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _run_command_with_timeout(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(0.1, float(timeout_seconds)))
        return subprocess.CompletedProcess(command, proc.returncode or 0, stdout, stderr), False
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc.pid)
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(command, proc.returncode or 124, stdout, stderr), True


def _query_plan_payload(root: Path, edition_date: str, run_id: str, query_plan: list[dict[str, object]]) -> dict[str, object]:
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.exists() else ""
    payload = {
        "schema_version": QUERY_PLAN_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "configuration_sha256": config_sha256,
        "query_count": len(query_plan),
        "queries": query_plan,
    }
    payload["query_plan_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def _prepare_legacy_run_dir(
    root: Path,
    edition_date: str,
    run_id: str,
    query_plan: list[dict[str, object]],
) -> tuple[Path, dict[str, object]]:
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_payload = _query_plan_payload(root, edition_date, run_id, query_plan)
    (run_dir / "query-plan.json").write_text(
        json.dumps(plan_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return run_dir, plan_payload


def _bounded_state_from_result(
    *,
    root: Path,
    edition_date: str,
    run_id: str,
    query_plan: list[dict[str, object]],
    result: dict[str, object],
    resume_count: int = 0,
) -> tuple[dict[str, object], dict[str, object]]:
    plan_payload = _query_plan_payload(root, edition_date, run_id, query_plan)
    discovery_ok = bool(result.get("ok"))
    timed_out = bool(result.get("timed_out")) or str(result.get("status") or "") == "timed_out"
    has_exclusions = bool(result.get("rejected_news_count")) or bool(result.get("fetch_failure_count_by_type"))
    status = str(result.get("status") or "").strip()
    if not status:
        status = "completed_with_exclusions" if discovery_ok and has_exclusions else "completed" if discovery_ok else "failed"
    candidate_count = int(result.get("candidate_count") or result.get("public_eligible_candidate_count") or 0)
    direct_source_count = int(result.get("direct_source_count") or result.get("source_count") or 0)
    queries_completed = int(result.get("queries_completed") if result.get("queries_completed") is not None else (0 if timed_out else len(query_plan)))
    queries_succeeded = int(
        result.get("queries_succeeded")
        if result.get("queries_succeeded") is not None
        else max(0, queries_completed - int(result.get("queries_failed") or 0) - int(result.get("queries_timed_out") or 0))
    )
    counter_fields_present = all(result.get(key) is not None for key in ("queries_attempted", "queries_succeeded", "queries_failed", "queries_timed_out"))
    counter_total = queries_succeeded + int(result.get("queries_failed") or 0) + int(result.get("queries_timed_out") or 0)
    counters_consistent = counter_fields_present and int(result.get("queries_attempted") or 0) == counter_total and int(result.get("queries_attempted") or 0) == queries_completed
    counter_integrity_status = "valid" if counters_consistent else "missing_or_inconsistent"
    direct_attempted = int(result.get("direct_source_fetch_attempt_count") or 0)
    direct_succeeded = int(result.get("direct_source_fetch_success_count") or 0)
    direct_fields_present = result.get("direct_source_fetch_attempt_count") is not None and result.get("direct_source_fetch_success_count") is not None
    direct_consistent = direct_fields_present and 0 <= direct_succeeded <= direct_attempted
    direct_ratio = 1.0 if direct_attempted == 0 and direct_consistent else (direct_succeeded / direct_attempted if direct_consistent and direct_attempted else 0.0)
    if not direct_consistent:
        counter_integrity_status = "missing_or_inconsistent"
    queries_timed_out = int(result.get("queries_timed_out") if result.get("queries_timed_out") is not None else (1 if timed_out else 0))
    queries_failed = int(result.get("queries_failed") if result.get("queries_failed") is not None else 0)
    query_plan_available_count = int(
        result.get("query_plan_available_count")
        if result.get("query_plan_available_count") is not None
        else len(query_plan)
    )
    query_plan_bounded_count = int(
        result.get("query_plan_bounded_count")
        if result.get("query_plan_bounded_count") is not None
        else len(query_plan)
    )
    queries_deferred = int(
        result.get("queries_deferred")
        if result.get("queries_deferred") is not None
        else max(0, query_plan_available_count - query_plan_bounded_count)
    )
    queries_attempted = int(
        result.get("queries_processed_this_invocation")
        if result.get("queries_processed_this_invocation") is not None
        else queries_completed + queries_failed + queries_timed_out
    )
    incomplete_capped = discovery_ok and queries_deferred > 0
    if not counters_consistent and not incomplete_capped:
        status = "collection_failure"
    if incomplete_capped:
        status = "incomplete_capped"
    coverage_ratio = (
        1.0
        if query_plan_available_count == 0
        else min(1.0, queries_completed / query_plan_available_count)
    )
    state = {
        "schema_version": RUN_STATE_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "started_at": _utc_now(),
        "completed_at": _utc_now(),
        "status": status,
        "resumable": (bool(result.get("resumable")) or timed_out) and not incomplete_capped,
        "resume_count": int(resume_count),
        "partitions_total": 1,
        "partitions_completed": int(result.get("partitions_completed") if result.get("partitions_completed") is not None else (0 if timed_out or incomplete_capped else 1)),
        "queries_total": query_plan_available_count,
        "queries_bounded": query_plan_bounded_count,
        "queries_completed": queries_completed,
        "queries_succeeded": queries_succeeded,
        "counter_integrity_status": counter_integrity_status,
        "queries_deferred": queries_deferred,
        "queries_remaining": max(0, query_plan_available_count - queries_attempted),
        "queries_failed": queries_failed,
        "queries_timed_out": queries_timed_out,
        "candidates_discovered": candidate_count or direct_source_count,
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "final_error": (
            "query disposition counters missing or inconsistent"
            if counter_integrity_status != "valid"
            else "" if discovery_ok else str(result.get("errors") or result.get("error") or "discovery expansion failed")
        ),
        "options": {
            "required_coverage_threshold": 0.90,
            "direct_source_coverage_threshold": 0.75,
        },
        "coverage": {
            "required_success_ratio": 0.0 if timed_out or not counters_consistent else min(1.0, queries_succeeded / max(1, query_plan_available_count)),
            "direct_success_ratio": 0.0 if timed_out or not counters_consistent or not direct_consistent else min(1.0, direct_ratio),
        },
        "agent_export": {
            "status": "success_with_exclusions"
            if status == "completed_with_exclusions" and counter_integrity_status == "valid"
            else "blocked_incomplete_collection"
            if incomplete_capped
            else "success"
            if discovery_ok and not timed_out and counter_integrity_status == "valid"
            else "blocked_incomplete_collection",
            "path": str(Path(result.get("agent_export_path") or (root / "status" / "food-line" / "runtime" / "agent-inbox"))),
            "sha256": str(result.get("agent_export_sha256") or hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()),
        },
        "next_action": "Plan-bound continuation required before intake." if status == INCOMPLETE_CAPPED_STATUS else "Repair collection counters before intake." if counter_integrity_status != "valid" else "No collection action required.",
    }
    return state, plan_payload


def _write_state_files(root: Path, edition_date: str, run_id: str, result: dict[str, object], *, resume_count: int = 0) -> dict[str, object]:
    query_plan = build_food_line_discovery_query_plan(root, edition_date)
    state, plan_payload = _bounded_state_from_result(
        root=root,
        edition_date=edition_date,
        run_id=run_id,
        query_plan=query_plan,
        result=result,
        resume_count=resume_count,
    )
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "query-plan.json").write_text(json.dumps(plan_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "run-state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    state_ok = state["status"] in {"success", "completed", "completed_with_exclusions", "zero_result_completion"} and state["counter_integrity_status"] == "valid"
    return {
        **result,
        "ok": bool(result.get("ok")) and state_ok,
        "status": state["status"],
        "run_state_path": str(run_dir / "run-state.json"),
        "query_plan_path": str(run_dir / "query-plan.json"),
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "run_id": run_id,
        "edition_date": edition_date,
        "status": state["status"],
        "coverage": state["coverage"],
        "agent_export": state["agent_export"],
        "queries_total": state["queries_total"],
        "queries_completed": state["queries_completed"],
        "queries_failed": state["queries_failed"],
        "queries_timed_out": state["queries_timed_out"],
        "counter_integrity_status": state["counter_integrity_status"],
        "final_error": state["final_error"],
        "candidates_discovered": state["candidates_discovered"],
    }


def _write_timed_out_state_files(
    root: Path,
    edition_date: str,
    run_id: str,
    query_plan: list[dict[str, object]],
    *,
    timeout_seconds: float,
    resume_count: int = 0,
    result: dict[str, object] | None = None,
) -> dict[str, object]:
    plan_payload = _query_plan_payload(root, edition_date, run_id, query_plan)
    state = {
        "schema_version": RUN_STATE_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "started_at": _utc_now(),
        "completed_at": _utc_now(),
        "status": "timed_out",
        "resumable": True,
        "resume_count": int(resume_count),
        "partitions_total": 1,
        "partitions_completed": 0,
        "queries_total": len(query_plan),
        "queries_completed": 0,
        "queries_failed": 0,
        "queries_timed_out": 1,
        "candidates_discovered": 0,
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "final_error": f"Food Line discovery exceeded bounded runtime of {timeout_seconds:.0f} seconds",
        "options": {
            "required_coverage_threshold": 0.90,
            "direct_source_coverage_threshold": 0.75,
        },
        "coverage": {
            "required_success_ratio": 0.0,
            "direct_success_ratio": 0.0,
        },
        "agent_export": {
            "status": "blocked_incomplete_collection",
            "path": str(root / "status" / "food-line" / "runtime" / "agent-inbox"),
            "sha256": "",
        },
        "next_action": "Plan-bound continuation required before intake.",
    }
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "query-plan.json").write_text(
        json.dumps(plan_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "run-state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    merged = {
        "ok": False,
        "status": state["status"],
        "error": state["final_error"],
        "run_state_path": str(run_dir / "run-state.json"),
        "query_plan_path": str(run_dir / "query-plan.json"),
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "run_id": run_id,
        "edition_date": edition_date,
        "coverage": state["coverage"],
        "agent_export": state["agent_export"],
        "queries_total": state["queries_total"],
        "queries_completed": state["queries_completed"],
        "queries_failed": state["queries_failed"],
        "queries_timed_out": state["queries_timed_out"],
        "candidates_discovered": state["candidates_discovered"],
    }
    if result:
        merged.update(result)
    return merged


def _find_run_dir(root: Path, run_id: str, edition_date: str | None = None) -> Path:
    if edition_date:
        candidate = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"run not found: {candidate}")
    matches = list((root / "data" / "dispatches" / "food-line" / "discovery-runs").glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected exactly one run named {run_id}; found {len(matches)}")
    return matches[0]


def _read_state(root: Path, run_id: str, edition_date: str | None = None) -> dict[str, object]:
    run_dir = _find_run_dir(root, run_id, edition_date)
    state_path = run_dir / "run-state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"run-state.json not found: {state_path}")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run-state.json must contain an object: {state_path}")
    return payload


def _run_legacy_bounded_contract(args: argparse.Namespace) -> dict[str, object]:
    root = Path.cwd()
    run_id = str(args.resume_run or args.run_id or "")
    if args.status_run:
        if not run_id:
            raise ValueError("--status-run requires a run identifier")
        run_dir = _find_run_dir(root, run_id, args.date)
        state = _read_state(root, run_id, args.date)
        return {
            "ok": True,
            **state,
            "run_id": str(state.get("run_id") or run_id),
            "edition_date": str(state.get("edition_date") or args.date or run_dir.parent.name),
            "run_state_path": str(run_dir / "run-state.json"),
        }
    if not run_id:
        raise ValueError("legacy bounded discovery requires --run-id or --resume-run")
    if not args.date:
        raise ValueError("--date is required for a bounded discovery run")
    edition_date = args.date
    resume_count = 1 if args.resume_run else 0
    resume_from_query_index = 0
    if args.resume_run:
        run_state = _read_state(root, run_id, args.date)
        resume_from_query_index = max(0, int(run_state.get("queries_completed") or 0))
    if args.max_run_minutes is None:
        result = run_food_line_discovery_expansion(
            root,
            edition_date,
            edition_mode=args.edition_mode,
            max_results_per_query=args.max_results_per_query,
            max_queries=args.max_queries,
            query_lookback_days=args.query_lookback_days,
            query_lookahead_days=args.query_lookahead_days,
            public_claim_lookback_days=args.public_claim_lookback_days,
            public_claim_lookahead_days=args.public_claim_lookahead_days,
            dry_run=bool(args.dry_run),
            resume_from_query_index=resume_from_query_index,
        )
        if args.export_agent_inbox and _export_allowed(root, edition_date, run_id, result):
            result = dict(result)
            export = _export_agent_inbox(
                root,
                edition_date=edition_date,
                run_id=run_id,
                result=result,
                agent_inbox_dir=args.agent_inbox_dir,
            )
            result["agent_export"] = export
            result["agent_export_path"] = export["path"]
            result["agent_export_sha256"] = export["sha256"]
        wrapped = _write_state_files(root, edition_date, run_id, result, resume_count=resume_count)
        return _terminal_contract_result(wrapped, edition_date=edition_date, run_id=run_id)

    runtime_deadline = datetime.now(timezone.utc) + timedelta(minutes=max(1.0, float(args.max_run_minutes)))
    result = run_food_line_discovery_expansion(
        root,
        edition_date,
        edition_mode=args.edition_mode,
        max_results_per_query=args.max_results_per_query,
        max_queries=args.max_queries,
        query_lookback_days=args.query_lookback_days,
        query_lookahead_days=args.query_lookahead_days,
        public_claim_lookback_days=args.public_claim_lookback_days,
        public_claim_lookahead_days=args.public_claim_lookahead_days,
        dry_run=bool(args.dry_run),
        resume_from_query_index=resume_from_query_index,
        runtime_deadline=runtime_deadline,
    )
    if args.export_agent_inbox and _export_allowed(root, edition_date, run_id, result):
        result = dict(result)
        export = _export_agent_inbox(
            root,
            edition_date=edition_date,
            run_id=run_id,
            result=result,
            agent_inbox_dir=args.agent_inbox_dir,
        )
        result["agent_export"] = export
        result["agent_export_path"] = export["path"]
        result["agent_export_sha256"] = export["sha256"]
    wrapped = _write_state_files(root, edition_date, run_id, result, resume_count=resume_count)
    if not wrapped["ok"] and not bool(result.get("timed_out")) and wrapped.get("status") != INCOMPLETE_CAPPED_STATUS:
        wrapped["status"] = "collection_failure"
    return _terminal_contract_result(
        wrapped,
        edition_date=edition_date,
        run_id=run_id,
        timed_out=bool(result.get("timed_out")),
        error_type=str(result.get("error_type") or "").strip() or ("timeout" if bool(result.get("timed_out")) else None),
        error_message=str(result.get("error_message") or result.get("error") or "").strip() or None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Food Line discovery expansion layer.")
    parser.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--run-id", default="", help="Optional run identifier used for structured result envelopes.")
    parser.add_argument("--manual-fallback-file", help="Optional JSON list of manual fallback records.")
    parser.add_argument("--edition-mode", default="current_update", choices=("current_update", "no_current_update"))
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--query-lookback-days", type=int, default=1)
    parser.add_argument("--query-lookahead-days", type=int, default=1)
    parser.add_argument("--public-claim-lookback-days", type=int, default=0)
    parser.add_argument("--public-claim-lookahead-days", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--resume-run")
    parser.add_argument("--status-run")
    parser.add_argument("--max-run-minutes", type=float)
    parser.add_argument("--export-agent-inbox", action="store_true")
    parser.add_argument("--agent-inbox-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if _legacy_args_supplied(args):
            result = _run_legacy_bounded_contract(args)
        else:
            root = Path.cwd()
            manual_path = Path(args.manual_fallback_file).resolve() if args.manual_fallback_file else None
            result = run_food_line_discovery_expansion(
                root,
                args.date,
                manual_fallback_path=manual_path,
                edition_mode=args.edition_mode,
                max_results_per_query=args.max_results_per_query,
                max_queries=args.max_queries,
                query_lookback_days=args.query_lookback_days,
                query_lookahead_days=args.query_lookahead_days,
                public_claim_lookback_days=args.public_claim_lookback_days,
                public_claim_lookahead_days=args.public_claim_lookahead_days,
                dry_run=bool(args.dry_run),
            )
            if int(result.get("queries_deferred") or 0) > 0:
                result = {**result, "ok": False, "status": INCOMPLETE_CAPPED_STATUS}
            elif not _result_counters_valid(result):
                result = {
                    **result,
                    "ok": False,
                    "status": "collection_failure",
                    "error_type": "collection_failure",
                    "error_message": "query disposition counters missing or inconsistent",
                    "final_error": "query disposition counters missing or inconsistent",
                    "agent_export": {"status": "blocked_incomplete_collection", "path": None, "sha256": None},
                }
        result = _terminal_contract_result(
            result,
            edition_date=str(result.get("edition_date") or args.date or ""),
            run_id=str(result.get("run_id") or args.run_id or ""),
        )
    except Exception as exc:  # noqa: BLE001
        result = _terminal_contract_result(
            {
                "ok": False,
                "status": "child_process_failure",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "error": str(exc),
                "warnings": [],
            },
            edition_date=args.date,
            run_id=str(args.run_id or ""),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())




