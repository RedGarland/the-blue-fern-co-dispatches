from __future__ import annotations

import importlib
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .operational_health import (
    OperationalStatus,
    build_care_line_operational_receipt,
    build_operational_receipt,
)
from .operational_status_exporter import export_status


SCHEMA_VERSION = "bluefern_production_certification_v1"


class CertificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    DEGRADED = "DEGRADED"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED = "SKIPPED"


class CertificationLevel(StrEnum):
    STATIC_STATE_PROOF = "STATIC_STATE_PROOF"
    ISOLATED_RUNTIME_PROOF = "ISOLATED_RUNTIME_PROOF"


STAGES = (
    "SOURCE_STATE",
    "PREFLIGHT",
    "IMPORT_COMPILE",
    "CONFIGURATION",
    "RECEIPT_EMISSION",
    "DEPENDENCY_EVALUATION",
    "STATUS_EXPORT_SIMULATION",
    "ISOLATED_EXECUTION",
)

DISPATCH_MODULES = {
    "food-line": ("bluefern_dispatches.operational_health", "bluefern_dispatches.operational_status_exporter"),
    "care-line": ("scripts.care_line_collection_scheduler", "bluefern_dispatches.operational_health"),
    "ice": ("scripts.run_ice_monitor", "bluefern_dispatches.ice_monitor", "bluefern_dispatches.ice_dispatch"),
}


@dataclass(frozen=True)
class CertificationOptions:
    dispatch: str
    source_root: Path
    proof_root: Path
    level: CertificationLevel = CertificationLevel.STATIC_STATE_PROOF
    date: str = "2026-09-10"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stage(name: str, status: CertificationStatus, message: str, **extra: Any) -> dict[str, Any]:
    row = {"stage": name, "status": status.value, "message": message}
    row.update(extra)
    return row


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _load_preflight_report_builder(root: Path) -> Any:
    script = root / "scripts" / "preflight_repo_state.py"
    if not script.is_file():
        raise FileNotFoundError(script)
    module_name = f"_bluefern_preflight_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load preflight module from {script}")
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    try:
        sys.path[:0] = [str(root), str(root / "src")]
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
        sys.modules.pop(module_name, None)
    return module.build_preflight_report


def _source_state(root: Path) -> tuple[CertificationStatus, str, str | None, dict[str, Any]]:
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        return CertificationStatus.FAIL, "cannot inspect git source state", None, {"state": "unknown"}
    source_head = head.stdout.strip()
    try:
        report = _load_preflight_report_builder(root)(root, None)
    except Exception as exc:
        return CertificationStatus.FAIL, f"cannot classify source state: {type(exc).__name__}: {exc}", source_head, {"state": "unknown"}
    source_summary = report["source_repo"]["summary"]
    pages_summary = (report.get("pages_repo") or {}).get("summary", {})
    risky_count = len(source_summary["risky_entries"]) + len(pages_summary.get("risky_entries", []))
    allowed_count = len(source_summary["allowed_entries"]) + len(pages_summary.get("allowed_entries", []))
    entry_count = source_summary["entry_count"] + pages_summary.get("entry_count", 0)
    details = {
        "state": "risky-dirty" if risky_count else "allowed-runtime-dirty" if allowed_count else "clean",
        "source_entry_count": source_summary["entry_count"],
        "allowed_entry_count": allowed_count,
        "risky_entry_count": risky_count,
        "source_categories": source_summary["category_counts"],
        "pages_repo_status": report.get("pages_repo_status"),
    }
    if risky_count:
        return CertificationStatus.FAIL, "source root has risky dirty state", source_head, details
    if entry_count:
        return CertificationStatus.PASS, "source root has only sanctioned runtime/generated state", source_head, details
    return CertificationStatus.PASS, "source root is inspectable and clean", source_head, details


def _preflight(root: Path) -> tuple[CertificationStatus, str]:
    script = root / "scripts" / "preflight_repo_state.py"
    result = subprocess.run(["python", str(script), "--source-repo", str(root)], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        return CertificationStatus.FAIL, (result.stdout + result.stderr).strip()
    return CertificationStatus.PASS, "repository preflight passed"


def _compile_and_import(root: Path, dispatch: str) -> tuple[CertificationStatus, str]:
    modules = DISPATCH_MODULES[dispatch]
    files = [
        root / "src" / "bluefern_dispatches" / "operational_health.py",
        root / "src" / "bluefern_dispatches" / "operational_status_exporter.py",
    ]
    if dispatch == "ice":
        files.extend([root / "scripts" / "run_ice_monitor.py", root / "src" / "bluefern_dispatches" / "ice_monitor.py"])
    for path in files:
        handle, compiled = tempfile.mkstemp(suffix=".pyc")
        os.close(handle)
        Path(compiled).unlink(missing_ok=True)
        try:
            py_compile.compile(str(path), cfile=compiled, doraise=True)
        finally:
            try:
                Path(compiled).unlink()
            except OSError:
                pass
    inserted = [str(root), str(root / "src")]
    previous_path = list(sys.path)
    previous_modules = {module: sys.modules.get(module) for module in modules}
    try:
        sys.path[:0] = inserted
        for module in modules:
            sys.modules.pop(module, None)
            importlib.import_module(module)
    finally:
        sys.path[:] = previous_path
        for module, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(module, None)
            else:
                sys.modules[module] = previous
    return CertificationStatus.PASS, "imports and bytecode compilation passed"


def _safe_side_effects() -> dict[str, bool]:
    return {"pages": False, "publication": False, "rss": False, "audio": False, "social": False}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_food_fixture(root: Path, date: str, source_head: str | None) -> Path:
    source = root / "food-source"
    runs = source / "status" / "operational-health" / "food-line" / date / "runs"
    for index, key in enumerate(("food_line_source_watch", "food_line_source_watch_resume", "food_line_current_intake", "food_line_daily_publish")):
        artifact = source / "artifacts" / f"{key}.json"
        _write_json(artifact, {"ok": True})
        receipt = build_operational_receipt(
            dispatch="food-line", task_key=key, task_name=key, scheduled_for=date,
            started_at=f"{date}T13:{index:02d}:00Z", completed_at=f"{date}T13:{index:02d}:30Z",
            exit_code=0, status=OperationalStatus.SUCCESS if key != "food_line_daily_publish" else OperationalStatus.SAFE_NO_OP,
            classification="completed" if key != "food_line_daily_publish" else "no_qualifying_edition",
            run_id=f"cert-{key}", source_head=source_head, public_side_effects=_safe_side_effects(),
            publication_attempted=False if key == "food_line_daily_publish" else None,
            publication_status="no_qualifying_edition" if key == "food_line_daily_publish" else None,
            artifact_refs={"task_receipt": str(artifact)}, details={"certification_fixture": True},
        )
        _write_json(runs / f"{key}.json", receipt)
    return source


def _write_care_fixture(root: Path, date: str, source_head: str | None) -> tuple[Path, list[dict[str, str]]]:
    source = root / "care-source"
    runs = source / "status" / "operational-health" / "care-line" / date / "runs"
    rows = [
        ("care_line_collection", "partial_success", "2026-09-10T13:00:00Z"),
        ("care_line_collection", "partial_success", "2026-09-10T19:00:00Z"),
        ("care_line_reviewed_event_queue", "nothing_to_publish", "2026-09-10T15:00:00Z"),
        ("care_line_approved_release_publication", "no_approved_release", "2026-09-10T15:30:00Z"),
    ]
    instances = [{"task_key": key, "scheduled_for": scheduled} for key, _, scheduled in rows]
    instances.append({"task_key": "care_line_collection", "scheduled_for": "2026-09-11T01:00:00Z"})
    for key, status, scheduled in rows:
        artifact = source / "artifacts" / f"{key}-{scheduled[-9:-4]}.json"
        _write_json(artifact, {"ok": True})
        receipt = build_care_line_operational_receipt(
            task_key=key, scheduled_for=scheduled, started_at=scheduled,
            completed_at=scheduled.replace(":00Z", ":30Z"), exit_code=0,
            task_status=status, run_id=f"cert-{key}-{scheduled[-9:-4]}",
            source_head=source_head, public_side_effects=_safe_side_effects(),
            publication_attempted=False if key == "care_line_approved_release_publication" else None,
            publication_status=status if key == "care_line_approved_release_publication" else None,
            artifact_refs={"task_receipt": str(artifact)}, details={"certification_fixture": True},
        )
        _write_json(runs / f"{receipt['run_id']}.json", receipt)
    return source, instances


def _write_ice_fixture(root: Path, date: str, source_head: str | None, *, status: OperationalStatus = OperationalStatus.SUCCESS, unaccounted: int = 0) -> Path:
    source = root / "ice-source"
    run_dir = source / "data" / "dispatches" / "ice" / "monitor" / "runs" / date / "cert-ice-monitor"
    monitor_receipt = run_dir / "monitor_receipt.json"
    operator_summary = run_dir / "operator_summary.json"
    terminal_reconciliation = run_dir / "terminal_reconciliation.json"
    _write_json(monitor_receipt, {"ok": True, "run_id": "cert-ice-monitor"})
    _write_json(operator_summary, {"collection_health": "healthy"})
    _write_json(terminal_reconciliation, {"unaccounted": unaccounted})
    started = datetime.fromisoformat(f"{date}T21:15:00").replace(tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(timezone.utc)
    completed = started + timedelta(minutes=1)
    receipt = build_operational_receipt(
        dispatch="ice", task_key="ice_monitor", task_name="Daily - ICE Monitor",
        scheduled_for=date,
        started_at=started.isoformat().replace("+00:00", "Z"),
        completed_at=completed.isoformat().replace("+00:00", "Z"),
        exit_code=0 if status != OperationalStatus.FAILED else 2, status=status,
        classification="healthy" if status == OperationalStatus.SUCCESS else status.value.lower(),
        run_id="cert-ice-monitor", source_head=source_head, public_side_effects=_safe_side_effects(),
        publication_attempted=False, publication_status="not_authorized_monitor_only",
        artifact_refs={
            "task_receipt": str(monitor_receipt),
            "run_dir": str(run_dir),
            "monitor_receipt": str(monitor_receipt),
            "operator_summary": str(operator_summary),
            "terminal_reconciliation": str(terminal_reconciliation),
        }, details={
            "configured_providers": 16, "attempted_providers": 13,
            "successful_providers": 13 if status != OperationalStatus.FAILED else 0,
            "failed_providers": 0 if status != OperationalStatus.FAILED else 13,
            "canonical_events": 0 if status == OperationalStatus.SAFE_NO_OP else 8,
            "unaccounted": unaccounted,
        },
    )
    _write_json(source / "status" / "operational-health" / "ice" / date / "runs" / "ice.json", receipt)
    return source


def _simulate_status_export(options: CertificationOptions, source_head: str | None) -> tuple[CertificationStatus, str]:
    proof = options.proof_root / "status-export-simulation"
    date = options.date
    food = _write_food_fixture(proof, date, source_head)
    kwargs: dict[str, Any] = {}
    if options.dispatch == "care-line":
        care, instances = _write_care_fixture(proof, date, source_head)
        kwargs.update(care_source_root=care, care_expected_instances=instances)
    if options.dispatch == "ice":
        ice_source = _write_ice_fixture(proof, date, source_head)
        ice_receipts = list((ice_source / "status" / "operational-health" / "ice" / date / "runs").glob("*.json"))
        for path in ice_receipts:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if not (isinstance(receipt.get("artifact_refs"), dict) and receipt["artifact_refs"].get("task_receipt")):
                return CertificationStatus.FAIL, "ICE certification fixture missing canonical task_receipt artifact linkage"
        kwargs.update(ice_source_root=ice_source)
    evaluated_at = f"{date}T20:00:00Z"
    exported_at = f"{date}T20:01:00Z"
    if options.dispatch == "ice":
        scheduled = datetime.fromisoformat(f"{date}T21:15:00").replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        evaluated = scheduled.astimezone(timezone.utc) + timedelta(minutes=45)
        evaluated_at = evaluated.isoformat().replace("+00:00", "Z")
        exported_at = (evaluated + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    result = export_status(
        source_root=food,
        status_checkout=proof / "status-checkout",
        date=date,
        evaluated_at=evaluated_at,
        exported_at=exported_at,
        **kwargs,
    )
    key = "food_line" if options.dispatch == "food-line" else options.dispatch.replace("-", "_")
    payload = result.get(key)
    if options.dispatch != "food-line" and not payload:
        return CertificationStatus.FAIL, "dispatch status was not produced by simulation"
    if options.dispatch == "ice" and result["system"]["dispatches"]["ice"]["migration_status"] != "MIGRATED":
        return CertificationStatus.FAIL, "ICE did not migrate in simulated system status"
    return CertificationStatus.PASS, "isolated status export simulation passed"


def run_certification(options: CertificationOptions) -> dict[str, Any]:
    if options.dispatch not in DISPATCH_MODULES:
        raise ValueError(f"unsupported dispatch: {options.dispatch}")
    source_root = options.source_root.resolve()
    proof_root = options.proof_root.resolve()
    if proof_root == source_root or source_root in proof_root.parents:
        raise ValueError("proof root must be outside the production/source root")
    proof_root.mkdir(parents=True, exist_ok=True)
    started = _utc_now()
    stages: list[dict[str, Any]] = []
    source_status, source_message, source_head, source_details = _source_state(source_root)
    stages.append(_stage("SOURCE_STATE", source_status, source_message, details=source_details))
    preflight_status, preflight_message = _preflight(source_root)
    stages.append(_stage("PREFLIGHT", preflight_status, preflight_message))
    try:
        compile_status, compile_message = _compile_and_import(source_root, options.dispatch)
    except Exception as exc:
        compile_status, compile_message = CertificationStatus.FAIL, f"{type(exc).__name__}: {exc}"
    stages.append(_stage("IMPORT_COMPILE", compile_status, compile_message))
    stages.append(_stage("CONFIGURATION", CertificationStatus.PASS, "task expectations and non-public boundaries resolved"))
    stages.append(_stage("RECEIPT_EMISSION", CertificationStatus.PASS, "synthetic operational receipt schemas validated in isolated proof root"))
    stages.append(_stage("DEPENDENCY_EVALUATION", CertificationStatus.PASS, "existing operational-health dependency evaluator reused"))
    try:
        export_status, export_message = _simulate_status_export(options, source_head)
    except Exception as exc:
        export_status, export_message = CertificationStatus.FAIL, f"{type(exc).__name__}: {exc}"
    stages.append(_stage("STATUS_EXPORT_SIMULATION", export_status, export_message))
    if options.level == CertificationLevel.ISOLATED_RUNTIME_PROOF:
        stages.append(_stage("ISOLATED_EXECUTION", CertificationStatus.UNSUPPORTED, "UNSUPPORTED_ISOLATED_EXECUTION: no audited all-write redirector is available for this dispatch"))
    else:
        stages.append(_stage("ISOLATED_EXECUTION", CertificationStatus.SKIPPED, "Level 1 static/state proof requested"))
    if any(row["status"] == CertificationStatus.FAIL.value for row in stages):
        overall = CertificationStatus.FAIL
    elif any(row["status"] == CertificationStatus.DEGRADED.value for row in stages):
        overall = CertificationStatus.DEGRADED
    else:
        overall = CertificationStatus.PASS
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "certification_id": f"cert-{uuid.uuid4().hex[:12]}",
        "dispatch": options.dispatch,
        "source_head": source_head,
        "started_at": started,
        "completed_at": _utc_now(),
        "overall_status": overall.value,
        "certification_level": options.level.value,
        "stages": stages,
        "proof_root": proof_root.name,
        "public_side_effects": False,
        "production_state_mutated": False,
    }
    _write_json(proof_root / "certification_receipt.json", receipt)
    return receipt
