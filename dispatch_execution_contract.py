"""Durable, desk-neutral execution contract for Dispatches.

This is a private integration layer.  It deliberately does not contain a
timer, provider client, scheduler mutation, publication implementation, or
production default.  Existing Gaza (and later Food/Care/ICE) code is supplied
as a desk adapter.  The contract owns one idempotent run record, phase
transitions, approval blocking, and crash-safe reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


SCHEMA_VERSION = "dispatch_run_receipt_v1"
CONTRACT_VERSION = "dispatch_execution_contract_v1"
MAX_SUMMARY_BYTES = 64 * 1024
MAX_ERROR_BYTES = 2048
CLAIM_TIMEOUT_SECONDS = 30.0
CLAIM_RETRY_SECONDS = 0.25
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class ContractError(RuntimeError):
    """The run contract rejected an invalid or contradictory transition."""


class ReceiptConflictError(ContractError):
    """A run id was reused for a different logical run."""


class ReceiptBusyError(ContractError):
    """Another process currently owns the run claim and did not release it."""


class UnknownOutcomeError(ContractError):
    """A callback may have caused an external effect whose result is unknown."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def deterministic_run_id(
    *,
    desk: str,
    scheduled_for: str,
    config_identity: str,
    input_fingerprint: str,
) -> str:
    """Derive a stable run identity so duplicate scheduler wakeups are no-ops."""
    parts = (desk, scheduled_for, config_identity, input_fingerprint)
    if any(not isinstance(value, str) or not value for value in parts):
        raise ContractError("run identity inputs must be non-empty strings")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{_safe_id(desk)}-{digest}"


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ContractError(f"unsafe identifier: {value!r}")
    return value


def _json_safe(value: Any, *, limit: int = MAX_SUMMARY_BYTES) -> Any:
    """Bound adapter-owned metadata before it becomes durable receipt data."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"adapter metadata is not JSON serializable: {type(exc).__name__}") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise ContractError("adapter metadata exceeds the bounded receipt limit")
    return json.loads(encoded)


def _safe_error(exc: BaseException) -> dict[str, str]:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)=([^&\s]+)", r"\1=<redacted>", text)
    text = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?<redacted>", text)
    text = text[:MAX_ERROR_BYTES]
    return {"type": type(exc).__name__, "message": text}


@dataclass(frozen=True)
class RunRequest:
    desk: str
    run_id: str
    scheduled_for: str
    config_identity: str
    input_fingerprint: str
    mode: str = "scheduled"
    source_identity: str | None = None

    def __post_init__(self) -> None:
        _safe_id(self.desk)
        _safe_id(self.run_id)
        for name in ("scheduled_for", "config_identity", "input_fingerprint", "mode"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractError(f"{name} must be a non-empty string")

    @property
    def idempotency_key(self) -> str:
        return ":".join(
            (self.desk, self.scheduled_for, self.config_identity, self.input_fingerprint)
        )


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    sha256: str
    size_bytes: int
    path: str | None = None
    version: str | None = None

    def to_record(self) -> dict[str, Any]:
        _safe_id(self.artifact_id)
        _safe_id(self.kind)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
            raise ContractError("artifact sha256 must be hexadecimal")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ContractError("artifact size_bytes must be a non-negative integer")
        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "sha256": self.sha256.lower(),
            "size_bytes": self.size_bytes,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.version is not None:
            result["version"] = self.version
        return result


@dataclass(frozen=True)
class PhaseResult:
    """A callback result; no truth is inferred from process exit alone."""

    status: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Sequence[Artifact] = field(default_factory=tuple)
    error: Mapping[str, Any] | None = None
    next_action: str | None = None

    def to_record(self) -> dict[str, Any]:
        allowed = {"succeeded", "partial", "failed", "unknown", "blocked"}
        if self.status not in allowed:
            raise ContractError(f"invalid phase status: {self.status!r}")
        record: dict[str, Any] = {
            "status": self.status,
            "summary": _json_safe(dict(self.summary)),
            "artifacts": [artifact.to_record() for artifact in self.artifacts],
        }
        if self.error is not None:
            record["error"] = _json_safe(dict(self.error), limit=MAX_ERROR_BYTES)
        if self.next_action is not None:
            record["next_action"] = self.next_action
        return record


@dataclass(frozen=True)
class EditorialDecision:
    """Decision from the existing editorial gate."""

    status: str
    reason: str
    approval_id: str | None = None
    next_action: str | None = None

    def to_record(self) -> dict[str, Any]:
        if self.status not in {"approved", "awaiting_approval", "rejected", "no_publication_needed"}:
            raise ContractError(f"invalid editorial status: {self.status!r}")
        if not isinstance(self.reason, str) or not self.reason:
            raise ContractError("editorial decision needs a reason")
        record: dict[str, Any] = {"status": self.status, "reason": self.reason}
        if self.approval_id is not None:
            _safe_id(self.approval_id)
            record["approval_id"] = self.approval_id
        if self.next_action is not None:
            record["next_action"] = self.next_action
        return record


@dataclass(frozen=True)
class ReconcileResult:
    """Read-only reconciliation of an interrupted or uncertain phase."""

    status: str
    phase: str
    result: PhaseResult | None = None
    next_action: str | None = None

    def to_record(self) -> dict[str, Any]:
        if self.status not in {"resolved", "still_unknown", "awaiting_approval"}:
            raise ContractError(f"invalid reconciliation status: {self.status!r}")
        record: dict[str, Any] = {"status": self.status, "phase": self.phase}
        if self.result is not None:
            record["result"] = self.result.to_record()
        if self.next_action is not None:
            record["next_action"] = self.next_action
        return record


class DeskAdapter(Protocol):
    desk: str

    def collect(self, request: RunRequest) -> PhaseResult: ...

    def validate(self, request: RunRequest, collection: Mapping[str, Any]) -> PhaseResult: ...

    def editorial_gate(
        self,
        request: RunRequest,
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        approval: Mapping[str, Any] | None = None,
    ) -> EditorialDecision: ...

    def publish(
        self,
        request: RunRequest,
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> PhaseResult: ...

    def reconcile(self, request: RunRequest, receipt: Mapping[str, Any]) -> ReconcileResult: ...


@dataclass
class CallableDeskAdapter:
    """Adapter bridge for existing Gaza/Food/Care/ICE functions.

    The callbacks are the current desk implementations; this class only gives
    them the shared signature.  It is intentionally dependency-injected so
    private tests can use production-shaped fakes without network or writes.
    """

    desk: str
    collect_fn: Callable[[RunRequest], PhaseResult]
    validate_fn: Callable[[RunRequest, Mapping[str, Any]], PhaseResult]
    editorial_fn: Callable[
        [RunRequest, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None],
        EditorialDecision,
    ]
    publish_fn: Callable[[RunRequest, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], PhaseResult]
    reconcile_fn: Callable[[RunRequest, Mapping[str, Any]], ReconcileResult]

    def __post_init__(self) -> None:
        _safe_id(self.desk)

    def collect(self, request: RunRequest) -> PhaseResult:
        return self.collect_fn(request)

    def validate(self, request: RunRequest, collection: Mapping[str, Any]) -> PhaseResult:
        return self.validate_fn(request, collection)

    def editorial_gate(
        self,
        request: RunRequest,
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        approval: Mapping[str, Any] | None = None,
    ) -> EditorialDecision:
        return self.editorial_fn(request, collection, validation, approval)

    def publish(
        self,
        request: RunRequest,
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> PhaseResult:
        return self.publish_fn(request, collection, validation, decision)

    def reconcile(self, request: RunRequest, receipt: Mapping[str, Any]) -> ReconcileResult:
        return self.reconcile_fn(request, receipt)


class ReceiptStore:
    """One collision-resistant JSON receipt per logical run."""

    def __init__(self, root: Path, *, clock: Callable[[], str] = utc_now) -> None:
        self.root = Path(root)
        self.clock = clock
        self._claim_local = threading.local()

    def _claim_entries(self) -> dict[str, tuple[Any, int]]:
        entries = getattr(self._claim_local, "entries", None)
        if entries is None:
            entries = {}
            self._claim_local.entries = entries
        return entries

    @contextmanager
    def claim(self, request: RunRequest):
        """Hold a per-run cross-process claim across the full execution."""
        path = self.path_for(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = os.path.normcase(os.path.abspath(str(path)))
        entries = self._claim_entries()
        existing = entries.get(key)
        if existing is not None:
            handle, depth = existing
            entries[key] = (handle, depth + 1)
            try:
                yield
            finally:
                current_handle, current_depth = entries[key]
                if current_handle is not handle or current_depth != depth + 1:
                    raise ContractError("run claim reentrancy state was corrupted")
                entries[key] = (handle, depth)
            return

        handle = None
        lock_path = path.with_suffix(path.suffix + ".lock")
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            deadline = time.monotonic() + CLAIM_TIMEOUT_SECONDS
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise ReceiptBusyError(f"receipt claim busy: {path}") from exc
                        time.sleep(CLAIM_RETRY_SECONDS)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as exc:
                        if time.monotonic() >= deadline:
                            raise ReceiptBusyError(f"receipt claim busy: {path}") from exc
                        time.sleep(CLAIM_RETRY_SECONDS)
            entries[key] = (handle, 0)
            yield
        finally:
            entries.pop(key, None)
            if handle is not None:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()

    def path_for(self, request: RunRequest) -> Path:
        return self.root / _safe_id(request.desk) / request.scheduled_for[:10] / f"{_safe_id(request.run_id)}.json"

    def load(self, request: RunRequest) -> dict[str, Any] | None:
        path = self.path_for(request)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"receipt cannot be read: {path}: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ContractError("receipt schema mismatch")
        self._assert_request_binding(payload, request)
        return payload

    def create(self, request: RunRequest) -> dict[str, Any]:
        with self.claim(request):
            return self._create_locked(request)

    def _create_locked(self, request: RunRequest) -> dict[str, Any]:
        path = self.path_for(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "run_id": request.run_id,
            "desk": request.desk,
            "idempotency_key": request.idempotency_key,
            "scheduled_for": request.scheduled_for,
            "mode": request.mode,
            "config_identity": request.config_identity,
            "input_fingerprint": request.input_fingerprint,
            "source_identity": request.source_identity,
            "phase": "planned",
            "terminal": False,
            "success": False,
            "outcome": "planned",
            "next_action": "start",
            "events": [],
            "phases": {},
            "artifacts": [],
            "created_at": self.clock(),
            "updated_at": self.clock(),
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if path.exists():
            existing = self.load(request)
            if existing is None:
                raise ReceiptConflictError("receipt appeared but could not be loaded")
            return existing
        fd, temp_name = tempfile.mkstemp(prefix=f".{request.run_id}.", suffix=".create.tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                existing = self.load(request)
                if existing is None:
                    raise ReceiptConflictError("receipt appeared but could not be loaded")
                return existing
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return payload

    def open_or_create(self, request: RunRequest) -> dict[str, Any]:
        with self.claim(request):
            return self.load(request) or self._create_locked(request)

    def save(self, request: RunRequest, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self.claim(request):
            return self._save_locked(request, payload)

    def _save_locked(self, request: RunRequest, payload: Mapping[str, Any]) -> dict[str, Any]:
        path = self.path_for(request)
        if not path.exists():
            raise ContractError("cannot update a missing receipt")
        data = _json_safe(dict(payload), limit=4 * MAX_SUMMARY_BYTES)
        data["updated_at"] = self.clock()
        fd, temp_name = tempfile.mkstemp(prefix=f".{request.run_id}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return dict(data)

    @staticmethod
    def _assert_request_binding(payload: Mapping[str, Any], request: RunRequest) -> None:
        expected = {
            "run_id": request.run_id,
            "desk": request.desk,
            "idempotency_key": request.idempotency_key,
            "config_identity": request.config_identity,
            "input_fingerprint": request.input_fingerprint,
            "source_identity": request.source_identity,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ReceiptConflictError(f"receipt binding mismatch for {key}")


class DispatchEngine:
    """The single tested execution path; it never schedules a run."""

    def __init__(self, store: ReceiptStore, adapter: DeskAdapter) -> None:
        if adapter.desk != adapter.desk.strip():
            raise ContractError("adapter desk name is not normalized")
        self.store = store
        self.adapter = adapter

    def execute(self, request: RunRequest, *, approval: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.store.claim(request):
            return self._execute_claimed(request, approval=approval)

    def _execute_claimed(self, request: RunRequest, *, approval: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if request.desk != self.adapter.desk:
            raise ContractError("request and adapter desk mismatch")
        receipt = self.store.open_or_create(request)
        if receipt["terminal"]:
            return receipt
        if receipt["phase"] != "planned":
            return self._resume_claimed(request, approval=approval)

        receipt = self._event(request, receipt, phase="run", status="started", outcome="started", next_action="collect")
        receipt = self._set_state(request, receipt, "collecting", False, False, "collect", "collecting")
        try:
            collection = self.adapter.collect(request)
            collection_record = collection.to_record()
        except UnknownOutcomeError as exc:
            return self._unknown(request, receipt, "collection", exc)
        except Exception as exc:  # callback failures become durable failures
            return self._failed(request, receipt, "collection", exc)

        receipt = self._phase(request, receipt, "collection", collection_record)
        if collection.status == "unknown":
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "collection outcome unknown")
        if collection.status in {"failed", "blocked"}:
            return self._set_state(request, receipt, "failed", True, False, "inspect collection failure", "collection_failed")

        receipt = self._set_state(request, receipt, "validating", False, False, "validate", "validating")
        try:
            validation = self.adapter.validate(request, collection_record)
            validation_record = validation.to_record()
        except UnknownOutcomeError as exc:
            return self._unknown(request, receipt, "validation", exc)
        except Exception as exc:
            return self._failed(request, receipt, "validation", exc)
        receipt = self._phase(request, receipt, "validation", validation_record)
        if validation.status == "unknown":
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "validation outcome unknown")
        if validation.status in {"failed", "blocked"}:
            return self._set_state(request, receipt, "failed", True, False, "inspect validation failure", "validation_failed")

        try:
            decision = self.adapter.editorial_gate(request, collection_record, validation_record, approval)
            decision_record = decision.to_record()
        except Exception as exc:
            return self._failed(request, receipt, "editorial", exc)
        receipt = self._event(request, receipt, phase="editorial", status=decision.status, decision=decision_record)
        return self._continue_after_decision(request, receipt, collection_record, validation_record, decision_record)

    def resume(self, request: RunRequest, *, approval: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.store.claim(request):
            return self._resume_claimed(request, approval=approval)

    def _resume_claimed(self, request: RunRequest, *, approval: Mapping[str, Any] | None = None) -> dict[str, Any]:
        receipt = self.store.load(request)
        if receipt is None:
            raise ContractError("cannot resume a run without a receipt")
        if receipt["terminal"]:
            return receipt

        if receipt["phase"] == "awaiting_approval":
            if approval is None:
                return receipt
            collection = receipt.get("phases", {}).get("collection")
            validation = receipt.get("phases", {}).get("validation")
            blocked = self._prepublication_gate(request, receipt, collection, validation)
            if blocked is not None:
                return blocked
            try:
                decision = self.adapter.editorial_gate(request, collection, validation, approval)
                decision_record = decision.to_record()
            except UnknownOutcomeError as exc:
                return self._unknown(request, receipt, "editorial", exc)
            except Exception as exc:
                return self._failed(request, receipt, "editorial", exc)
            receipt = self._event(request, receipt, phase="editorial", status=decision.status, decision=decision_record)
            return self._continue_after_decision(request, receipt, collection, validation, decision_record)

        if receipt["phase"] in {"collecting", "validating", "publishing", "unknown"}:
            phase_map = {
                "collecting": "collection",
                "validating": "validation",
                "publishing": "publish",
                "unknown": receipt.get("unknown_phase", "unknown"),
            }
            return self._reconcile_interrupted(request, receipt, str(phase_map[receipt["phase"]]))

        if receipt["phase"] == "collection":
            return self._resume_after_resolved_prepublication(request, receipt, approval)

        if receipt["phase"] in {"validation", "editorial"}:
            collection = receipt.get("phases", {}).get("collection")
            validation = receipt.get("phases", {}).get("validation")
            decision = receipt.get("editorial_decision")
            blocked = self._prepublication_gate(request, receipt, collection, validation)
            if blocked is not None:
                return blocked
            if receipt["phase"] == "editorial" and isinstance(decision, dict):
                return self._continue_after_decision(request, receipt, collection, validation, decision)
            if isinstance(decision, dict) and decision.get("status") == "approved" and approval is None:
                return self._continue_after_decision(request, receipt, collection, validation, decision)
            try:
                decision_obj = self.adapter.editorial_gate(request, collection, validation, approval)
                decision = decision_obj.to_record()
            except Exception as exc:
                return self._failed(request, receipt, "editorial", exc)
            receipt = self._event(request, receipt, phase="editorial", status=decision["status"], decision=decision)
            return self._continue_after_decision(request, receipt, collection, validation, decision)

        if receipt["phase"] not in {"planned"}:
            return receipt
        return self.execute(request, approval=approval)

    def _prepublication_gate(
        self,
        request: RunRequest,
        receipt: dict[str, Any],
        collection: Any,
        validation: Any,
    ) -> dict[str, Any] | None:
        for phase_name, phase_record in (("collection", collection), ("validation", validation)):
            if not isinstance(phase_record, dict):
                return self._set_state(request, receipt, "unknown", False, False, "reconcile", f"missing {phase_name} prerequisite")
            status = phase_record.get("status")
            if status == "unknown":
                return self._set_state(request, receipt, "unknown", False, False, "reconcile", f"{phase_name} outcome unknown")
            if status in {"failed", "blocked"}:
                return self._set_state(request, receipt, "failed", True, False, f"inspect {phase_name} failure", f"{phase_name}_failed")
            if status not in {"succeeded", "partial"}:
                return self._set_state(request, receipt, "unknown", False, False, "reconcile", f"{phase_name} prerequisite is not resolved")
        return None

    def _reconcile_interrupted(self, request: RunRequest, receipt: dict[str, Any], phase: str) -> dict[str, Any]:
        try:
            reconciled = self.adapter.reconcile(request, receipt)
            reconciliation_record = reconciled.to_record()
        except UnknownOutcomeError as exc:
            return self._unknown(request, receipt, "reconcile", exc)
        except Exception as exc:
            return self._failed(request, receipt, "reconcile", exc)
        receipt = self._event(
            request,
            receipt,
            phase="reconcile",
            status=reconciled.status,
            reconciliation=reconciliation_record,
            interrupted_phase=phase,
            next_action=reconciled.next_action or "reconcile",
        )
        if reconciled.status == "still_unknown":
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "external outcome remains unknown")
        if reconciled.status == "awaiting_approval":
            return self._set_state(request, receipt, "awaiting_approval", False, False, "approval_required", "approval remains required")
        if reconciled.result is None:
            raise ContractError("resolved reconciliation must include a phase result")
        resolved = reconciled.result.to_record()
        receipt = self._phase(request, receipt, reconciled.phase, resolved)
        if resolved["status"] == "failed":
            return self._set_state(request, receipt, "failed", True, False, "inspect reconciled failure", "reconciled_failure")
        if reconciled.phase == "publish" and resolved["status"] == "succeeded":
            return self._set_state(request, receipt, "published", True, True, "complete", "published outcome reconciled")
        if reconciled.phase in {"collection", "validation"} and resolved["status"] in {"succeeded", "partial"}:
            return self._resume_after_resolved_prepublication(request, receipt, None)
        return self._set_state(request, receipt, "unknown", False, False, "reconcile", "resolved phase cannot advance safely")

    def _resume_after_resolved_prepublication(
        self, request: RunRequest, receipt: dict[str, Any], approval: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        collection = receipt.get("phases", {}).get("collection")
        validation = receipt.get("phases", {}).get("validation")
        if not isinstance(collection, dict):
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "missing prerequisite phase")
        collection_status = collection.get("status")
        if collection_status == "unknown":
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "collection outcome unknown")
        if collection_status not in {"succeeded", "partial"}:
            return self._set_state(request, receipt, "failed", True, False, "inspect collection failure", "collection_failed")
        if "validation" not in receipt.get("phases", {}):
            receipt = self._set_state(request, receipt, "validating", False, False, "validate", "validating")
            try:
                result = self.adapter.validate(request, collection)
                validation = result.to_record()
            except UnknownOutcomeError as exc:
                return self._unknown(request, receipt, "validation", exc)
            except Exception as exc:
                return self._failed(request, receipt, "validation", exc)
            receipt = self._phase(request, receipt, "validation", validation)
        blocked = self._prepublication_gate(request, receipt, collection, validation)
        if blocked is not None:
            return blocked
        try:
            decision = self.adapter.editorial_gate(request, collection, validation, approval)
            decision_record = decision.to_record()
        except UnknownOutcomeError as exc:
            return self._unknown(request, receipt, "editorial", exc)
        except Exception as exc:
            return self._failed(request, receipt, "editorial", exc)
        receipt = self._event(request, receipt, phase="editorial", status=decision.status, decision=decision_record)
        return self._continue_after_decision(request, receipt, collection, validation, decision_record)

    def _continue_after_decision(
        self,
        request: RunRequest,
        receipt: dict[str, Any],
        collection: Mapping[str, Any],
        validation: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        for phase_name, phase_record in (("collection", collection), ("validation", validation)):
            phase_status = phase_record.get("status")
            if phase_status == "unknown":
                return self._set_state(request, receipt, "unknown", False, False, "reconcile", f"{phase_name} outcome unknown")
            if phase_status not in {"succeeded", "partial"}:
                return self._set_state(request, receipt, "failed", True, False, f"inspect {phase_name} failure", f"{phase_name}_failed")
        status = decision["status"]
        if status == "awaiting_approval":
            return self._set_state(request, receipt, "awaiting_approval", False, False, "approval_required", decision.get("reason", "approval required"))
        if status == "rejected":
            return self._set_state(request, receipt, "rejected", True, False, "inspect editorial rejection", "rejected")
        if status == "no_publication_needed":
            return self._set_state(request, receipt, "completed", True, True, "complete", "no_publication_needed")

        receipt = self._set_state(request, receipt, "publishing", False, False, "publish", "publishing")
        try:
            published = self.adapter.publish(request, collection, validation, decision)
            published_record = published.to_record()
        except UnknownOutcomeError as exc:
            return self._unknown(request, receipt, "publish", exc)
        except Exception as exc:
            return self._failed(request, receipt, "publish", exc)
        receipt = self._phase(request, receipt, "publish", published_record)
        if published.status == "unknown":
            return self._set_state(request, receipt, "unknown", False, False, "reconcile", "publication outcome unknown")
        if published.status == "blocked":
            return self._set_state(
                request,
                receipt,
                "blocked",
                True,
                False,
                "publication_not_attempted",
                "publication_blocked_not_attempted",
            )
        if published.status != "succeeded":
            return self._set_state(request, receipt, "failed", True, False, "inspect publication failure", "publication_failed")
        return self._set_state(request, receipt, "published", True, True, "complete", "run completed")

    def _phase(self, request: RunRequest, receipt: dict[str, Any], phase: str, record: dict[str, Any]) -> dict[str, Any]:
        phases = dict(receipt.get("phases", {}))
        phases[phase] = record
        artifacts = list(receipt.get("artifacts", []))
        for artifact in record.get("artifacts", []):
            if artifact not in artifacts:
                artifacts.append(artifact)
        receipt["phases"] = phases
        receipt["artifacts"] = artifacts
        return self.store.save(request, receipt)

    def _event(self, request: RunRequest, receipt: dict[str, Any], *, phase: str, status: str, **extra: Any) -> dict[str, Any]:
        event = {"event_id": uuid.uuid4().hex, "at": self.store.clock(), "phase": phase, "status": status}
        event.update(_json_safe(extra, limit=MAX_SUMMARY_BYTES))
        receipt["events"] = list(receipt.get("events", [])) + [event]
        if phase == "editorial" and isinstance(extra.get("decision"), dict):
            receipt["editorial_decision"] = dict(extra["decision"])
        return self.store.save(request, receipt)

    def _set_state(
        self,
        request: RunRequest,
        receipt: dict[str, Any],
        phase: str,
        terminal: bool,
        success: bool,
        next_action: str,
        outcome: str,
    ) -> dict[str, Any]:
        receipt["phase"] = phase
        receipt["terminal"] = terminal
        receipt["success"] = success
        receipt["outcome"] = outcome
        receipt["next_action"] = next_action
        return self.store.save(request, receipt)

    def _failed(self, request: RunRequest, receipt: dict[str, Any], phase: str, exc: BaseException) -> dict[str, Any]:
        receipt = self._event(request, receipt, phase=phase, status="failed", error=_safe_error(exc))
        receipt["last_error"] = _safe_error(exc)
        return self._set_state(request, receipt, "failed", True, False, f"inspect_{phase}_failure", f"{phase}_failed")

    def _unknown(self, request: RunRequest, receipt: dict[str, Any], phase: str, exc: BaseException) -> dict[str, Any]:
        receipt = self._event(request, receipt, phase=phase, status="unknown", error=_safe_error(exc))
        receipt["last_error"] = _safe_error(exc)
        receipt["unknown_phase"] = phase
        return self._set_state(request, receipt, "unknown", False, False, "reconcile", f"{phase}_outcome_unknown")


__all__ = [
    "Artifact",
    "CallableDeskAdapter",
    "CONTRACT_VERSION",
    "ContractError",
    "DeskAdapter",
    "DispatchEngine",
    "EditorialDecision",
    "MAX_SUMMARY_BYTES",
    "PhaseResult",
    "ReceiptBusyError",
    "ReceiptConflictError",
    "ReceiptStore",
    "ReconcileResult",
    "RunRequest",
    "SCHEMA_VERSION",
    "UnknownOutcomeError",
    "deterministic_run_id",
    "utc_now",
]
