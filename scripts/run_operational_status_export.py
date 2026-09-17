from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import date as date_cls, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.operational_status_exporter import (  # noqa: E402
    ExportError,
    commit_and_push_status,
    export_status,
    load_recovery_context,
    prepare_status_checkout,
)

DEFAULT_SOURCE_ROOT = Path(r"C:\BlueFernRunner\FoodLineCurrent6")
DEFAULT_STATUS_CHECKOUT = Path(r"C:\BlueFernRunner\OperationalStatusCurrent")
DEFAULT_BRANCH = "ops/status/food-line-2026-09-10"
LOG_ROOT = Path("logs/operational-status-exporter")
CARE_SCHEDULED_TASKS: tuple[tuple[str, str], ...] = (
    ("care_line_collection", r"\Blue Fern Co.\Blue Fern Care Line National Collection"),
    ("care_line_reviewed_event_queue", r"\Blue Fern Co.\Blue Fern Care Line Reviewed Event Queue"),
    ("care_line_approved_release_publication", r"\Blue Fern Co.\Blue Fern Care Line Approved Release Publication"),
)
CARE_SCHEDULER_TIMEZONE = ZoneInfo("America/Los_Angeles")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_head(root: Path) -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _default_date(source_root: Path) -> str:
    root = source_root / "status" / "operational-health" / "food-line"
    dates = sorted(path.name for path in root.iterdir() if path.is_dir() and len(path.name) == 10) if root.exists() else []
    if not dates:
        raise ExportError("no Food Line operational-health date directories found")
    return dates[-1]


def _write_local_receipt(source_root: Path, run_id: str, payload: dict[str, Any]) -> Path:
    path = source_root / LOG_ROOT / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_xml(task_name: str) -> ET.Element:
    if os.name != "nt":
        raise ExportError("Care scheduler metadata is available only from Windows Task Scheduler")
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise ExportError(completed.stderr.strip() or f"cannot read scheduled task: {task_name}")
    try:
        return ET.fromstring(completed.stdout)
    except ET.ParseError as exc:
        raise ExportError(f"cannot parse scheduled task XML for {task_name}: {exc}") from exc


def _calendar_trigger_instances(root: ET.Element, *, task_key: str, task_name: str, run_date: date_cls) -> list[dict[str, str]]:
    namespace = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    instances: list[dict[str, str]] = []
    for trigger in root.findall(".//task:CalendarTrigger", namespace):
        enabled = trigger.findtext("task:Enabled", default="true", namespaces=namespace)
        if str(enabled).lower() == "false":
            continue
        start_text = trigger.findtext("task:StartBoundary", namespaces=namespace)
        if not start_text:
            raise ExportError(f"scheduled task trigger missing StartBoundary: {task_name}")
        try:
            start = datetime.fromisoformat(start_text)
        except ValueError as exc:
            raise ExportError(f"scheduled task trigger has invalid StartBoundary: {task_name}") from exc
        days_text = trigger.findtext("task:ScheduleByDay/task:DaysInterval", default="1", namespaces=namespace)
        try:
            days_interval = max(1, int(days_text))
        except ValueError as exc:
            raise ExportError(f"scheduled task trigger has invalid DaysInterval: {task_name}") from exc
        start_local = start.astimezone(CARE_SCHEDULER_TIMEZONE) if start.tzinfo else start.replace(tzinfo=CARE_SCHEDULER_TIMEZONE)
        if (run_date - start_local.date()).days < 0 or (run_date - start_local.date()).days % days_interval:
            continue
        local_instance = datetime(
            run_date.year,
            run_date.month,
            run_date.day,
            start_local.hour,
            start_local.minute,
            start_local.second,
            tzinfo=CARE_SCHEDULER_TIMEZONE,
        )
        instances.append(
            {
                "task_key": task_key,
                "task_name": task_name,
                "scheduled_for": _iso_utc(local_instance),
                "schedule_source": "windows_task_scheduler",
            }
        )
    if not instances:
        raise ExportError(f"scheduled task has no enabled calendar trigger for {run_date.isoformat()}: {task_name}")
    return instances


def care_expected_instances_from_task_scheduler(run_date: str) -> list[dict[str, str]]:
    try:
        parsed_date = date_cls.fromisoformat(run_date)
    except ValueError as exc:
        raise ExportError(f"invalid Care schedule date: {run_date}") from exc
    instances: list[dict[str, str]] = []
    for task_key, task_name in CARE_SCHEDULED_TASKS:
        root = _task_xml(task_name)
        instances.extend(_calendar_trigger_instances(root, task_key=task_key, task_name=task_name, run_date=parsed_date))
    return sorted(instances, key=lambda item: (item["scheduled_for"], item["task_key"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the serialized operational-status exporter only.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--status-checkout", type=Path, default=DEFAULT_STATUS_CHECKOUT)
    parser.add_argument("--date")
    parser.add_argument("--evaluated-at")
    parser.add_argument("--exported-at")
    parser.add_argument("--recovery-context", type=Path)
    parser.add_argument("--care-source-root", type=Path)
    parser.add_argument("--ice-source-root", type=Path)
    parser.add_argument("--prepare-branch", default=DEFAULT_BRANCH)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--commit-message", default="Export operational status")
    parser.add_argument("--no-push", action="store_true", help="Write status locally without committing or pushing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    record: dict[str, Any] = {
        "schema_version": "bluefern_operational_status_exporter_receipt_v1",
        "run_id": run_id,
        "started_at": utc_now(),
        "completed_at": None,
        "exit_code": 1,
        "status_changed": False,
        "commit_created": False,
        "push_succeeded": False,
        "care_source_configured": args.care_source_root is not None,
        "ice_source_configured": args.ice_source_root is not None,
        "source_head": _git_head(args.source_root),
        "status_checkout_head_before": _git_head(args.status_checkout),
        "status_checkout_head_after": None,
        "classification": "exporter_failure",
        "paths": [],
    }
    try:
        date = args.date or _default_date(args.source_root)
        prepare_status_checkout(args.status_checkout, branch=args.prepare_branch, remote=args.remote)
        result = export_status(
            source_root=args.source_root,
            status_checkout=args.status_checkout,
            date=date,
            evaluated_at=args.evaluated_at or utc_now(),
            exported_at=args.exported_at,
            care_source_root=args.care_source_root,
            care_expected_instances=care_expected_instances_from_task_scheduler(date) if args.care_source_root is not None else None,
            ice_source_root=args.ice_source_root,
            recovery=load_recovery_context(args.recovery_context),
        )
        record["paths"] = result["paths"]
        record["status_changed"] = True
        if not args.no_push:
            commit = commit_and_push_status(
                args.status_checkout,
                paths=result["paths"],
                message=args.commit_message,
                remote=args.remote,
                branch=args.prepare_branch,
            )
            record["commit_created"] = commit is not None
            record["push_succeeded"] = True
        record["classification"] = "exported"
        record["exit_code"] = 0
        return_code = 0
    except (ExportError, OSError, ValueError) as exc:
        record["error"] = str(exc)
        return_code = 1
    finally:
        record["completed_at"] = utc_now()
        record["status_checkout_head_after"] = _git_head(args.status_checkout)
        _write_local_receipt(args.source_root, run_id, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
