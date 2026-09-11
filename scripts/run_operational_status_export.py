from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the serialized operational-status exporter only.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--status-checkout", type=Path, default=DEFAULT_STATUS_CHECKOUT)
    parser.add_argument("--date")
    parser.add_argument("--evaluated-at")
    parser.add_argument("--exported-at")
    parser.add_argument("--recovery-context", type=Path)
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
