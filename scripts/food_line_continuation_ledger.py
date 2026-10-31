"""Durable stable-ID continuation state for Food collection-only runs.

This module is intentionally JSON/file based: it has no provider, SQLite,
publication, intake, scheduler, or subprocess side effects.  The caller owns
the batch executor; this module only binds plans, records intent/outcomes, and
fails closed on uncertainty or drift.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import socket
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA = "food_line_stable_continuation_v1"
LEDGER_NAME = "continuation-ledger.json"
PLAN_NAME = "continuation-query-plan.json"
LOCK_NAME = "continuation-ledger.lock"
STALE_LOCK_AFTER_SECONDS = 6 * 60 * 60
LEGACY_NAMES = {"run-state.json", "query-plan.json", "query-plan.jsonl"}
HANDOFF_ALLOWED_STATUSES = {
    "success",
    "zero_result_completion",
    "completed",
    "completed_with_exclusions",
}


class ContinuationLedgerError(RuntimeError):
    """Fail-closed continuation error."""


class ContinuationLockError(ContinuationLedgerError):
    """Another owner holds the continuation ledger lock."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def stable_query_id(row: dict[str, Any]) -> str:
    """Return an ID derived from the complete canonical query row."""
    return hashlib.sha256(_canonical(row)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuationLedgerError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuationLedgerError(f"JSON object required: {path}")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (_canonical(value) + b"\n")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


@contextmanager
def _exclusive_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / LOCK_NAME
    metadata = _canonical({"pid": os.getpid(), "host": socket.gethostname(), "started_at": _utc_now()})
    recovered_lock: Path | None = None
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            existing = lock.read_bytes()
            owner = json.loads(existing.decode("utf-8"))
            started_at = datetime.fromisoformat(str(owner["started_at"].replace("Z", "+00:00")))
            owner_pid = int(owner["pid"])
            owner_host = str(owner["host"])
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as parse_exc:
            raise ContinuationLockError(f"continuation lock owner is unknown; recovery refused: {lock}") from parse_exc
        age = time.time() - started_at.timestamp()
        if owner_host != socket.gethostname() or age < STALE_LOCK_AFTER_SECONDS:
            raise ContinuationLockError(f"continuation lock already held: {lock}") from exc
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise ContinuationLockError(f"continuation lock owner is still alive or uninspectable: {lock}") from exc
        except OSError as alive_exc:
            # Windows reports an impossible PID as ERROR_INVALID_PARAMETER;
            # that is the dead-owner result for this bounded probe.  Other
            # errors remain unknown and fail closed.
            if getattr(alive_exc, "winerror", None) == 87:
                pass
            else:
                raise ContinuationLockError(f"continuation lock owner state is unknown: {lock}") from alive_exc
        else:
            raise ContinuationLockError(f"continuation lock already held: {lock}") from exc
        recovered_lock = lock.with_name(f"{LOCK_NAME}.stale-{owner_pid}-{time.time_ns()}")
        try:
            os.replace(lock, recovered_lock)
        except FileNotFoundError as replace_exc:
            raise ContinuationLockError(f"continuation lock changed during stale recovery: {lock}") from replace_exc
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as reacquire_exc:
            try:
                recovered_lock.unlink()
            except OSError:
                pass
            raise ContinuationLockError(f"continuation lock was acquired by another owner: {lock}") from reacquire_exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(metadata + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ContinuationLedgerError(f"cannot release continuation lock: {exc}") from exc
        finally:
            if recovered_lock is not None:
                try:
                    recovered_lock.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ContinuationLedgerError(f"cannot remove recovered stale lock marker: {exc}") from exc


def _reject_legacy(directory: Path) -> None:
    present = sorted(name for name in LEGACY_NAMES if (directory / name).exists())
    if present:
        raise ContinuationLedgerError(f"legacy continuation artifacts refused: {present}")


def _binding(
    edition_date: str,
    run_id: str,
    source_identity: str,
    source_module_sha256: str,
    config_sha256: str,
    plan_sha256: str,
) -> dict[str, str]:
    return {
        "edition_date": edition_date,
        "run_id": run_id,
        "source_identity": source_identity,
        "source_module_sha256": source_module_sha256,
        "config_sha256": config_sha256,
        "plan_sha256": plan_sha256,
    }


def _assert_binding(state: dict[str, Any], binding: dict[str, str]) -> None:
    actual = state.get("binding")
    if actual != binding:
        raise ContinuationLedgerError(f"continuation binding drift: expected {binding}, found {actual}")


def _validate_query_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(row) for row in rows]
    ids = [stable_query_id(row) for row in result]
    if len(ids) != len(set(ids)):
        raise ContinuationLedgerError("query plan contains duplicate stable IDs")
    return result


def build_plan(
    *,
    edition_date: str,
    run_id: str,
    source_identity: str,
    source_module_sha256: str,
    config_sha256: str,
    query_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    rows = _validate_query_rows(query_rows)
    plan = {
        "schema": SCHEMA,
        "edition_date": edition_date,
        "run_id": run_id,
        "source_identity": source_identity,
        "source_module_sha256": source_module_sha256,
        "config_sha256": config_sha256,
        "query_rows": rows,
        "query_ids": [stable_query_id(row) for row in rows],
    }
    plan["plan_sha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    return plan


def prepare_ledger(
    directory: Path,
    *,
    edition_date: str,
    run_id: str,
    source_identity: str,
    source_module_sha256: str,
    config_sha256: str,
    query_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    _reject_legacy(directory)
    plan = build_plan(
        edition_date=edition_date,
        run_id=run_id,
        source_identity=source_identity,
        source_module_sha256=source_module_sha256,
        config_sha256=config_sha256,
        query_rows=query_rows,
    )
    plan_sha = str(plan["plan_sha256"])
    binding = _binding(edition_date, run_id, source_identity, source_module_sha256, config_sha256, plan_sha)
    ledger = {
        "schema": SCHEMA,
        "binding": binding,
        "planned_query_ids": list(plan["query_ids"]),
        "pending_query_ids": list(plan["query_ids"]),
        "in_flight_query_ids": [],
        "completed_query_ids": [],
        "unknown_query_ids": [],
        "query_results": {},
        "publication_attempted": False,
        "intake_eligible": False,
        "release_ready": False,
        "publication_side_effects": {},
        "export_status": "not_requested",
        "execution_mode": "collection_only",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    with _exclusive_lock(directory):
        if (directory / PLAN_NAME).exists() or (directory / LEDGER_NAME).exists():
            raise ContinuationLedgerError("continuation ledger already exists; refusing replacement")
        _write_json_atomic(directory / PLAN_NAME, plan)
        _write_json_atomic(directory / LEDGER_NAME, ledger)
    return ledger


def load_ledger(directory: Path, *, binding: dict[str, str]) -> dict[str, Any]:
    _reject_legacy(directory)
    plan = _read_json(directory / PLAN_NAME)
    ledger = _read_json(directory / LEDGER_NAME)
    expected_plan_sha = hashlib.sha256(_canonical({key: plan[key] for key in plan if key != "plan_sha256"})).hexdigest()
    if plan.get("plan_sha256") != expected_plan_sha:
        raise ContinuationLedgerError("query plan hash mismatch")
    if ledger.get("schema") != SCHEMA or plan.get("schema") != SCHEMA:
        raise ContinuationLedgerError("unsupported continuation schema")
    if ledger.get("binding", {}).get("plan_sha256") != expected_plan_sha:
        raise ContinuationLedgerError("ledger does not bind the query plan")
    _assert_binding(ledger, binding)
    query_ids = list(plan.get("query_ids") or [])
    if query_ids != list(ledger.get("planned_query_ids") or []):
        raise ContinuationLedgerError("ledger/query-plan ID mismatch")
    return ledger


def continue_ledger(
    directory: Path,
    *,
    binding: dict[str, str],
    batch_size: int,
    batch_executor: Any,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        if state["unknown_query_ids"] or state["in_flight_query_ids"]:
            raise ContinuationLedgerError("uncertain or in-flight query outcomes require verified reconciliation")
        plan = _read_json(directory / PLAN_NAME)
        by_id = dict(zip(plan["query_ids"], plan["query_rows"], strict=True))
        claimed = list(state["pending_query_ids"][:batch_size])
        if not claimed:
            state["updated_at"] = _utc_now()
            _write_json_atomic(directory / LEDGER_NAME, state)
            return state
        state["pending_query_ids"] = [item for item in state["pending_query_ids"] if item not in claimed]
        state["in_flight_query_ids"] = claimed
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        rows = [by_id[item] for item in claimed]
        try:
            outcomes = batch_executor(rows)
            if not isinstance(outcomes, dict) or set(outcomes) != set(claimed):
                raise ContinuationLedgerError("batch executor returned an incomplete or foreign ID set")
        except BaseException as exc:
            state["unknown_query_ids"] = list(claimed)
            state["in_flight_query_ids"] = []
            state["updated_at"] = _utc_now()
            state["last_error"] = {"type": type(exc).__name__, "message": str(exc)}
            _write_json_atomic(directory / LEDGER_NAME, state)
            raise
        state["completed_query_ids"].extend(claimed)
        state["query_results"].update({key: outcomes[key] for key in claimed})
        state["in_flight_query_ids"] = []
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


def reconcile_unknown(directory: Path, *, binding: dict[str, str], verified_results: dict[str, Any]) -> dict[str, Any]:
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        unknown = set(state["unknown_query_ids"])
        if set(verified_results) != unknown:
            raise ContinuationLedgerError("reconciliation must cover exactly all unknown query IDs")
        for query_id, result in verified_results.items():
            if not isinstance(result, dict):
                raise ContinuationLedgerError(f"reconciliation result is not an object: {query_id}")
            if (
                result.get("status") not in HANDOFF_ALLOWED_STATUSES
                or result.get("ok") is not True
                or result.get("terminal") is not True
                or result.get("uncertain") is not False
                or result.get("required_coverage_success") is not True
                or result.get("collection_only") is not True
            ):
                raise ContinuationLedgerError(f"reconciliation result is not an allowed verified terminal outcome: {query_id}")
        state["query_results"].update(verified_results)
        state["completed_query_ids"].extend(sorted(unknown))
        state["unknown_query_ids"] = []
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


def source_stage_handoff_assessment(
    state: dict[str, Any],
    *,
    required_threshold: float = 0.90,
    direct_threshold: float = 0.75,
) -> dict[str, Any]:
    """Assess whether stable-ID results can hand off to the existing intake gate.

    This is deliberately stricter than the collection-only ledger gate.  Every
    planned ID must have an explicit terminal, allowed outcome with explicit
    coverage markers.  Missing markers, failed outcomes, pending IDs, and
    uncertain IDs cannot be interpreted as zero-news or completion.
    """
    planned = list(state.get("planned_query_ids") or [])
    planned_set = set(planned)
    completed = set(state.get("completed_query_ids") or [])
    pending = list(state.get("pending_query_ids") or [])
    in_flight = list(state.get("in_flight_query_ids") or [])
    unknown = list(state.get("unknown_query_ids") or [])
    results = state.get("query_results") if isinstance(state.get("query_results"), dict) else {}
    reasons: list[str] = []
    if pending:
        reasons.append("pending query IDs remain")
    if in_flight:
        reasons.append("in-flight query IDs remain")
    if unknown:
        reasons.append("unknown query IDs require verified reconciliation")
    if completed != planned_set:
        reasons.append("completed IDs do not exactly cover the bound plan")
    if len(completed) != len(planned):
        reasons.append("duplicate or missing completed IDs")

    required_successes = 0
    direct_attempted = 0
    direct_successes = 0
    candidate_count = 0
    exclusions = 0
    normalized_results: list[dict[str, Any]] = []
    for query_id in planned:
        outcome = results.get(query_id)
        if not isinstance(outcome, dict):
            reasons.append(f"missing outcome for {query_id}")
            continue
        status = str(outcome.get("status") or "")
        if status not in HANDOFF_ALLOWED_STATUSES:
            reasons.append(f"outcome {query_id} has disallowed status {status!r}")
        if outcome.get("ok") is not True:
            reasons.append(f"outcome {query_id} is not explicitly successful")
        if outcome.get("terminal") is not True or outcome.get("uncertain") is True:
            reasons.append(f"outcome {query_id} is not a verified terminal result")
        if not isinstance(outcome.get("required_coverage_success"), bool):
            reasons.append(f"outcome {query_id} lacks explicit required coverage")
        else:
            required_successes += int(outcome["required_coverage_success"])
        attempted = outcome.get("direct_source_attempted")
        direct_ok = outcome.get("direct_source_success")
        if not isinstance(attempted, bool) or not isinstance(direct_ok, bool):
            reasons.append(f"outcome {query_id} lacks explicit direct-source coverage")
        elif attempted:
            direct_attempted += 1
            direct_successes += int(direct_ok)
        candidate_count += int(outcome.get("candidate_count") or 0)
        exclusions += int(outcome.get("exclusion_count") or 0)
        normalized_results.append(outcome)

    total = len(planned)
    required_ratio = required_successes / max(1, total)
    direct_ratio = direct_successes / direct_attempted if direct_attempted else 1.0
    if required_ratio < required_threshold:
        reasons.append("required coverage is below the existing intake threshold")
    if direct_ratio < direct_threshold:
        reasons.append("direct-source coverage is below the existing intake threshold")
    eligible = not reasons and len(normalized_results) == total
    collection_status = "completed_with_exclusions" if exclusions else "completed"
    return {
        "status": "eligible" if eligible else "blocked",
        "intake_eligible": eligible,
        "collection_status": collection_status if eligible else "incomplete_or_blocked",
        "reason": "" if eligible else "; ".join(dict.fromkeys(reasons)),
        "planned_count": total,
        "completed_count": len(completed),
        "required_success_count": required_successes,
        "direct_source_attempted_count": direct_attempted,
        "direct_source_success_count": direct_successes,
        "required_success_ratio": required_ratio,
        "direct_success_ratio": direct_ratio,
        "candidate_count": candidate_count,
        "exclusion_count": exclusions,
        "no_news": bool(eligible and candidate_count == 0),
        "publication_attempted": False,
        "public_export_status": "not_requested",
        "publication_side_effects": {},
    }


def gate_summary(state: dict[str, Any]) -> dict[str, Any]:
    handoff = source_stage_handoff_assessment(state)
    return {
        "publication_attempted": False,
        "intake_eligible": handoff["intake_eligible"],
        "release_ready": False,
        "publication_side_effects": {},
        "export_status": "not_requested",
        "public_export_status": "not_requested",
        "source_stage_handoff": handoff,
        "pending_count": len(state.get("pending_query_ids") or []),
        "in_flight_count": len(state.get("in_flight_query_ids") or []),
        "unknown_count": len(state.get("unknown_query_ids") or []),
        "completed_count": len(state.get("completed_query_ids") or []),
    }

