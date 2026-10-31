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


def _candidate_payload_key(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if candidate_id:
        return f"candidate:{candidate_id}"
    return f"payload:{hashlib.sha256(_canonical(candidate)).hexdigest()}"


def _merge_candidate_payloads(state: dict[str, Any], outcomes: dict[str, Any]) -> None:
    payloads = state.setdefault("candidate_payloads", {})
    if not isinstance(payloads, dict):
        raise ContinuationLedgerError("candidate payload store is not an object")
    for outcome in outcomes.values():
        if not isinstance(outcome, dict):
            continue
        for candidate in outcome.get("candidates") or []:
            if not isinstance(candidate, dict):
                raise ContinuationLedgerError("candidate payload is not an object")
            payloads[_candidate_payload_key(candidate)] = dict(candidate)


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
        "checkpointed_query_ids": [],
        "query_results": {},
        "candidate_payloads": {},
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


def record_source_watch_batch(
    directory: Path,
    *,
    binding: dict[str, str],
    child_payload: dict[str, Any],
) -> dict[str, Any]:
    """Bind the bounded source-watch result to the pre-created ledger.

    Source-watch still owns the provider process, but its durable ledger is
    created before that process starts.  This function records only the
    explicitly returned query rows and candidate payloads; unobserved rows
    remain pending and therefore cannot be mistaken for successful coverage.
    """
    observed_rows = child_payload.get("query_rows")
    if not isinstance(observed_rows, list):
        raise ContinuationLedgerError("source-watch result lacks durable query rows")
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        if state.get("unknown_query_ids"):
            raise ContinuationLedgerError("source-watch ledger already contains uncertain IDs")
        plan = _read_json(directory / PLAN_NAME)
        planned_rows = list(plan.get("query_rows") or [])
        planned_ids = list(plan.get("query_ids") or [])
        if len(planned_rows) != len(planned_ids):
            raise ContinuationLedgerError("source-watch plan has mismatched rows and IDs")
        planned_id_set = set(planned_ids)
        claim = state.get("source_watch_claim")
        claimed = list(claim.get("query_ids") or []) if isinstance(claim, dict) else []
        in_flight = list(state.get("in_flight_query_ids") or [])
        if (
            not claimed
            or len(set(claimed)) != len(claimed)
            or set(claimed) != set(in_flight)
            or not set(claimed) <= planned_id_set
        ):
            raise ContinuationLedgerError("source-watch claim and in-flight IDs are not an exact match")
        pending = list(state.get("pending_query_ids") or [])
        if set(claimed) & set(pending):
            raise ContinuationLedgerError("source-watch claim overlaps pending IDs")
        planned_by_id = dict(zip(planned_ids, planned_rows, strict=True))
        results = state.get("query_results")
        if not isinstance(results, dict):
            raise ContinuationLedgerError("source-watch ledger query results are not an object")
        result_ids = set(results)
        if not result_ids <= planned_id_set:
            raise ContinuationLedgerError("source-watch ledger contains foreign query result IDs")
        if result_ids & set(pending):
            raise ContinuationLedgerError("source-watch ledger contains results for pending IDs")
        checkpointed = state.get("checkpointed_query_ids")
        if not isinstance(checkpointed, list):
            raise ContinuationLedgerError("source-watch ledger checkpoint IDs are not a list")
        checkpointed_ids = set(checkpointed)
        if not checkpointed_ids <= planned_id_set:
            raise ContinuationLedgerError("source-watch ledger contains foreign checkpoint IDs")
        if checkpointed_ids & set(pending):
            raise ContinuationLedgerError("source-watch ledger contains checkpoints for pending IDs")
        observed_by_id: dict[str, dict[str, Any]] = {}
        for observed in observed_rows:
            if not isinstance(observed, dict):
                raise ContinuationLedgerError("source-watch query row is not an object")
            matches = [
                query_id
                for query_id in claimed
                if isinstance(planned_by_id.get(query_id), dict)
                and all(observed.get(key) == value for key, value in planned_by_id[query_id].items())
            ]
            if len(matches) != 1:
                raise ContinuationLedgerError("source-watch query row did not bind to exactly one claimed query ID")
            query_id = matches[0]
            if query_id in observed_by_id:
                raise ContinuationLedgerError(f"source-watch returned duplicate query row: {query_id}")
            checkpoint = results.get(query_id)
            if not isinstance(checkpoint, dict):
                raise ContinuationLedgerError(f"source-watch query lacks a durable checkpoint: {query_id}")
            checkpoint_rows = checkpoint.get("query_rows")
            if not isinstance(checkpoint_rows, list) or checkpoint_rows != [observed]:
                raise ContinuationLedgerError(f"source-watch row contradicts durable checkpoint: {query_id}")
            observed_by_id[query_id] = dict(observed)

        child_status = str(child_payload.get("status") or "")
        terminal_child_statuses = HANDOFF_ALLOWED_STATUSES | {"incomplete_capped"}
        if child_status in terminal_child_statuses:
            for query_id in claimed:
                checkpoint = results.get(query_id)
                if not isinstance(checkpoint, dict):
                    raise ContinuationLedgerError(f"source-watch terminal result lacks durable checkpoint: {query_id}")
                checkpoint_rows = checkpoint.get("query_rows")
                if not isinstance(checkpoint_rows, list):
                    raise ContinuationLedgerError(f"source-watch checkpoint rows are invalid: {query_id}")
                if checkpoint_rows and query_id not in observed_by_id:
                    raise ContinuationLedgerError(f"source-watch durable checkpoint row is missing from child result: {query_id}")

        finalized: list[str] = []
        for query_id in claimed:
            checkpoint = results.get(query_id)
            if not isinstance(checkpoint, dict):
                continue
            if not isinstance(checkpoint.get("terminal"), bool) or not isinstance(checkpoint.get("uncertain"), bool):
                raise ContinuationLedgerError(f"source-watch checkpoint state flags are invalid: {query_id}")
            if checkpoint.get("terminal") and checkpoint.get("uncertain"):
                raise ContinuationLedgerError(f"source-watch checkpoint is contradictory: {query_id}")
            if checkpoint.get("terminal") is True and checkpoint.get("uncertain") is False:
                finalized.append(query_id)

        state["in_flight_query_ids"] = [query_id for query_id in in_flight if query_id not in set(finalized)]
        completed = list(state.get("completed_query_ids") or [])
        for query_id in finalized:
            if query_id not in completed:
                completed.append(query_id)
        state["completed_query_ids"] = completed
        remaining = list(state["in_flight_query_ids"])
        state["source_watch_capture"] = {
            "status": "captured" if not remaining else "quarantined",
            "observed_query_count": len(observed_by_id),
            "finalized_query_count": len(finalized),
            "remaining_in_flight_query_ids": remaining,
            "source_status": child_status,
            "source_run_id": str(child_payload.get("run_id") or ""),
        }
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


def claim_source_watch_batch(
    directory: Path,
    *,
    binding: dict[str, str],
    batch_size: int,
) -> dict[str, Any]:
    """Durably claim the first source-watch batch before any provider call.

    The legacy source-watch child still returns one aggregate payload, but the
    ledger must not remain all-pending while that child is executing.  A crash
    therefore leaves an in-flight quarantine that continuation cannot replay
    until an explicit verified reconciliation.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        if state.get("unknown_query_ids") or state.get("in_flight_query_ids"):
            raise ContinuationLedgerError("source-watch ledger already contains uncertain or in-flight IDs")
        claimed = list(state.get("pending_query_ids") or [])[:batch_size]
        if not claimed:
            return state
        state["pending_query_ids"] = [query_id for query_id in state["pending_query_ids"] if query_id not in set(claimed)]
        state["in_flight_query_ids"] = claimed
        state["source_watch_claim"] = {
            "status": "claimed",
            "query_ids": list(claimed),
            "claimed_at": _utc_now(),
        }
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


def checkpoint_in_flight_outcome(
    directory: Path,
    *,
    binding: dict[str, str],
    query_id: str,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Persist one outcome/payload while its query remains in-flight.

    This is the durable hook for a per-query source executor.  The ID is not
    marked completed until the executor's final capture succeeds, so an
    interrupted process cannot turn a partial result into false completion.
    """
    if not isinstance(outcome, dict):
        raise ContinuationLedgerError("checkpoint outcome must be an object")
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        if query_id not in set(state.get("in_flight_query_ids") or []):
            raise ContinuationLedgerError(f"query is not in-flight: {query_id}")
        state["query_results"][query_id] = dict(outcome)
        _merge_candidate_payloads(state, {query_id: outcome})
        checkpointed = list(state.get("checkpointed_query_ids") or [])
        if query_id not in checkpointed:
            checkpointed.append(query_id)
        state["checkpointed_query_ids"] = checkpointed
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


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
    checkpoint_outcomes: bool = False,
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

        def checkpoint(query_id: str, outcome: dict[str, Any]) -> None:
            if query_id not in claimed:
                raise ContinuationLedgerError(f"checkpoint contains a foreign query ID: {query_id}")
            if not isinstance(outcome, dict):
                raise ContinuationLedgerError(f"checkpoint outcome is not an object: {query_id}")
            state["query_results"][query_id] = dict(outcome)
            _merge_candidate_payloads(state, {query_id: outcome})
            checkpointed = list(state.get("checkpointed_query_ids") or [])
            if query_id not in checkpointed:
                checkpointed.append(query_id)
            state["checkpointed_query_ids"] = checkpointed
            state["updated_at"] = _utc_now()
            _write_json_atomic(directory / LEDGER_NAME, state)

        try:
            outcomes = batch_executor(rows, checkpoint) if checkpoint_outcomes else batch_executor(rows)
            if not isinstance(outcomes, dict) or set(outcomes) != set(claimed):
                raise ContinuationLedgerError("batch executor returned an incomplete or foreign ID set")
        except BaseException as exc:
            state["unknown_query_ids"] = list(claimed)
            state["in_flight_query_ids"] = []
            state["updated_at"] = _utc_now()
            state["last_error"] = {"type": type(exc).__name__, "message": str(exc)}
            _write_json_atomic(directory / LEDGER_NAME, state)
            raise
        completed: list[str] = []
        uncertain: list[str] = []
        for query_id in claimed:
            outcome = outcomes[query_id]
            if not isinstance(outcome, dict):
                raise ContinuationLedgerError(f"batch executor outcome is not an object: {query_id}")
            terminal = outcome.get("terminal")
            is_uncertain = outcome.get("uncertain")
            if terminal is True and is_uncertain is False:
                completed.append(query_id)
            elif terminal is False and is_uncertain is True:
                uncertain.append(query_id)
            else:
                raise ContinuationLedgerError(f"batch executor outcome has invalid terminal state: {query_id}")
        state["query_results"].update({key: outcomes[key] for key in claimed})
        _merge_candidate_payloads(state, {key: outcomes[key] for key in claimed})
        state["completed_query_ids"].extend(query_id for query_id in completed if query_id not in state["completed_query_ids"])
        state["unknown_query_ids"].extend(query_id for query_id in uncertain if query_id not in state["unknown_query_ids"])
        state["in_flight_query_ids"] = []
        state["updated_at"] = _utc_now()
        _write_json_atomic(directory / LEDGER_NAME, state)
        return state


def reconcile_unknown(directory: Path, *, binding: dict[str, str], verified_results: dict[str, Any]) -> dict[str, Any]:
    with _exclusive_lock(directory):
        state = load_ledger(directory, binding=binding)
        uncertain = set(state.get("unknown_query_ids") or []) | set(state.get("in_flight_query_ids") or [])
        if set(verified_results) != uncertain:
            raise ContinuationLedgerError("reconciliation must cover exactly all unknown or in-flight query IDs")
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
        # Reconciliation is a terminal batch just like an ordinary continuation
        # batch.  Merge its candidate payloads while the same exclusive lock is
        # held, before clearing unknown IDs or writing the new ledger snapshot.
        # Otherwise the coverage gate can become eligible while the intake
        # export silently loses candidates recovered from an unknown outcome.
        _merge_candidate_payloads(state, verified_results)
        state["completed_query_ids"].extend(sorted(uncertain))
        state["unknown_query_ids"] = []
        state["in_flight_query_ids"] = []
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
