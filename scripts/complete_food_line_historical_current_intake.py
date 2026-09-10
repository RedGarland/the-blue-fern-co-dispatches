from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts import process_food_line_current_intake as current_intake
from bluefern_dispatches.food_line_current_review import build_proposed_edition, write_json_atomic

SCHEMA_VERSION = "food_line_historical_current_intake_replay_v1"
REASON = "missing_current_intake"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _payload_date(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    search_window = payload.get("search_window") if isinstance(payload.get("search_window"), dict) else {}
    return str(payload.get("edition_date") or search_window.get("edition_date") or "").strip()


def _find_source_watch_run(root: Path, historical_date: str, run_id: str | None) -> tuple[Path, Path]:
    runs_root = root / "data" / "dispatches" / "food-line" / "discovery-runs" / historical_date
    if not runs_root.exists():
        raise FileNotFoundError(f"missing retained discovery run directory: {runs_root}")
    candidates = [path for path in sorted(runs_root.iterdir()) if path.is_dir()]
    if run_id:
        candidates = [path for path in candidates if path.name == run_id]
    valid = [path for path in candidates if (path / "run-state.json").is_file() and (path / "query-plan.json").is_file()]
    if len(valid) != 1:
        raise ValueError(f"expected exactly one retained source-watch run for {historical_date}; found {len(valid)}")
    return valid[0] / "run-state.json", valid[0] / "query-plan.json"


def _selected_input_paths(root: Path, historical_date: str, inbox: Path) -> list[Path]:
    """Return only retained dated handoffs from the inbox, never live/fallback discovery output."""
    if not inbox.exists():
        return []
    paths: list[Path] = []
    for path in sorted(inbox.rglob("*.json")):
        if not path.is_file() or "processed" in path.parts:
            continue
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if _payload_date(payload) == historical_date:
            paths.append(path)
    return paths


def _copy_selected_inputs_to_boundary(root: Path, historical_date: str, selected: list[Path]) -> list[Path]:
    boundary_dir = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "retained-inputs"
    boundary_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in selected:
        target = boundary_dir / source.name
        if target.exists() and _sha256(target) != _sha256(source):
            raise ValueError(f"historical retained-input collision: {target}")
        if not target.exists():
            shutil.copyfile(source, target)
        copied.append(target)
    return copied


def run_replay(
    root: Path,
    *,
    historical_date: str,
    inbox: Path,
    source_watch_run_id: str | None = None,
    replayed_at: str | None = None,
) -> dict[str, Any]:
    replayed_at = replayed_at or _utc_now()
    run_state_path, query_plan_path = _find_source_watch_run(root, historical_date, source_watch_run_id)
    discovery_candidates_path = root / "data" / "dispatches" / "food-line" / "discovery" / historical_date / "discovery_candidates.json"
    if not discovery_candidates_path.is_file():
        raise FileNotFoundError(f"missing retained discovery candidates: {discovery_candidates_path}")

    selected_inputs = _selected_input_paths(root, historical_date, inbox)
    if not selected_inputs:
        raise ValueError(f"no retained source-watch handoff matched historical date {historical_date}")
    for path in selected_inputs:
        payload = _load_json(path)
        if _payload_date(payload) != historical_date:
            raise ValueError(f"input escaped historical boundary: {path}")

    boundary_inputs = _copy_selected_inputs_to_boundary(root, historical_date, selected_inputs)
    queue_path = root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    original_queue_bytes = queue_path.read_bytes() if queue_path.exists() else None
    try:
        queue = current_intake._build_review_queue(root, historical_date, inbox)
        proposed = build_proposed_edition(queue)
        historical_queue_path = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "current-signal-review.json"
        write_json_atomic(historical_queue_path, queue)
        report = current_intake._current_intake_report(root, historical_date, inbox)
    finally:
        if original_queue_bytes is None:
            try:
                queue_path.unlink()
            except FileNotFoundError:
                pass
        else:
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_bytes(original_queue_bytes)

    proposed_json = root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{historical_date}.json"
    proposed_md = root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{historical_date}.md"
    proposed = dict(proposed)
    proposed["historical_replay"] = {
        "schema_version": SCHEMA_VERSION,
        "historical_date": historical_date,
        "replayed_at": replayed_at,
        "replay_reason": REASON,
        "source_watch_run_id": run_state_path.parent.name,
        "publication_side_effects": False,
        "auto_approval": False,
    }
    write_json_atomic(proposed_json, proposed)
    proposed_md.parent.mkdir(parents=True, exist_ok=True)
    proposed_md.write_text(
        "\n".join(
            [
                f"# Food Line historical intake replay — {historical_date}",
                "",
                f"- historical_date: {historical_date}",
                f"- replayed_at: {replayed_at}",
                f"- replay_reason: {REASON}",
                f"- source_watch_run_id: {run_state_path.parent.name}",
                f"- pending_item_count: {proposed.get('pending_item_count', 0)}",
                f"- selected_item_count: {proposed.get('selected_item_count', 0)}",
                "- approved_item_count: 0",
                "- publication_side_effects: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    operator_attention_dir = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "operator-attention"
    daily_publish_dir = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "daily-publish-receipt"
    operator_attention_refs = [
        {"path": _rel(root, path), "sha256": _sha256(path)}
        for path in sorted(operator_attention_dir.glob("*.json"))
    ] if operator_attention_dir.exists() else []
    daily_publish_refs = [
        {"path": _rel(root, path), "sha256": _sha256(path)}
        for path in sorted(daily_publish_dir.glob("*.json"))
    ] if daily_publish_dir.exists() else []

    report_path = root / "data" / "dispatches" / "food-line" / "review" / "reports" / historical_date / "current-intake.json"
    if isinstance(report.get("queue"), dict):
        report["queue"]["path"] = _rel(root, historical_queue_path)
    report.update(
        {
            "historical_replay": True,
            "historical_date": historical_date,
            "replayed_at": replayed_at,
            "replay_reason": REASON,
            "source_watch_run_id": run_state_path.parent.name,
            "source_watch_run_state_path": _rel(root, run_state_path),
            "source_watch_query_plan_path": _rel(root, query_plan_path),
            "discovery_candidates_path": _rel(root, discovery_candidates_path),
            "historical_evidence_boundary": {
                "selected_inputs": [{"path": _rel(root, p), "sha256": _sha256(p)} for p in selected_inputs],
                "retained_input_copies": [{"path": _rel(root, p), "sha256": _sha256(p)} for p in boundary_inputs],
                "source_watch_run_state": {"path": _rel(root, run_state_path), "sha256": _sha256(run_state_path)},
                "source_watch_query_plan": {"path": _rel(root, query_plan_path), "sha256": _sha256(query_plan_path)},
                "discovery_candidates": {"path": _rel(root, discovery_candidates_path), "sha256": _sha256(discovery_candidates_path)},
                "operator_attention": operator_attention_refs,
                "daily_publish_receipts": daily_publish_refs,
            },
        }
    )
    write_json_atomic(report_path, report)

    operator_attention_dir = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "operator-attention"
    daily_publish_dir = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "daily-publish-receipt"
    operator_attention_refs = [
        {"path": _rel(root, path), "sha256": _sha256(path)}
        for path in sorted(operator_attention_dir.glob("*.json"))
    ] if operator_attention_dir.exists() else []
    daily_publish_refs = [
        {"path": _rel(root, path), "sha256": _sha256(path)}
        for path in sorted(daily_publish_dir.glob("*.json"))
    ] if daily_publish_dir.exists() else []

    receipt_path = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "replay-receipt.json"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "historical_date": historical_date,
        "replayed_at": replayed_at,
        "replay_reason": REASON,
        "source_watch_run_id": run_state_path.parent.name,
        "selected_input_count": len(selected_inputs),
        "considered": int(report.get("import_attempt_count") or report.get("accepted_file_count") or 0),
        "imported": int(report.get("import_count") or 0),
        "pending": int(report.get("queue", {}).get("pending_item_count") or 0),
        "selected": int((proposed.get("selected_item_count") or 0)),
        "approved": 0,
        "excluded": int(report.get("queue", {}).get("rejected_item_count") or 0),
        "unresolved": 0,
        "network_access": False,
        "publication_side_effects": report.get("publication_side_effects"),
        "artifacts": {
            "current_intake_report": _rel(root, report_path),
            "historical_queue_snapshot": _rel(root, historical_queue_path),
            "proposed_edition_json": _rel(root, proposed_json),
            "proposed_edition_markdown": _rel(root, proposed_md),
        },
        "historical_evidence_boundary": {
            **report["historical_evidence_boundary"],
            "operator_attention": operator_attention_refs,
            "daily_publish_receipts": daily_publish_refs,
        },
    }
    write_json_atomic(receipt_path, receipt)
    return receipt


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete historical Food Line Current Intake from retained artifacts only.")
    parser.add_argument("--historical-date", required=True)
    parser.add_argument("--inbox", default="data/dispatches/food-line/agent-inbox")
    parser.add_argument("--source-watch-run-id")
    parser.add_argument("--replayed-at")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        receipt = run_replay(
            Path.cwd(),
            historical_date=args.historical_date,
            inbox=Path(args.inbox),
            source_watch_run_id=args.source_watch_run_id,
            replayed_at=args.replayed_at,
        )
    except Exception as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "failed", "error": str(exc)}, indent=2, ensure_ascii=True))
        return 1
    print(json.dumps(receipt, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





