from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import traceback
from datetime import date as date_class, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.discover_food_line_sources import discover_food_line_sources  # noqa: E402
from scripts.check_food_line_blue_fern_compliance import run_food_line_blue_fern_compliance  # noqa: E402
from scripts.run_food_line_dispatch import publish_food_line_pages, push_pages_repo, run_food_line_dispatch  # noqa: E402
from scripts.test_food_line_candidate_sources import cleanup_food_line_candidates, test_food_line_candidate_sources  # noqa: E402

LOGGER_NAME = "food_line_daily_ops"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _today_local_date() -> str:
    return date_class.today().isoformat()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_families(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_max_runtime_minutes(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _report_dir(root: Path, date: str) -> Path:
    return root / "output" / "review" / "food-line" / date


def _report_paths(root: Path, date: str) -> dict[str, str]:
    base = _report_dir(root, date)
    return {
        "daily_ops_report_json": str(base / "daily_ops_report.json"),
        "daily_ops_report_md": str(base / "daily_ops_report.md"),
    }


def _log_path(root: Path, date: str) -> Path:
    return root / "logs" / "food-line" / "daily_ops" / f"{date}.log"


def _status_path(root: Path) -> Path:
    return root / "status" / "food-line-daily-ops-status.json"


def _lock_path(root: Path) -> Path:
    return root / "runtime" / "food-line-daily-ops.lock"


def _process_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _acquire_lock(path: Path) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "created_at": datetime.now().astimezone().isoformat(),
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = _read_json_file(path)
        existing_pid = int(existing.get("pid") or 0) if existing else 0
        if existing_pid and _process_is_active(existing_pid):
            return False, f"Food Line daily ops is already running (pid {existing_pid})."
        return True, None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return True, None


def _release_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _configure_file_logger(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _close_file_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _append_warning(warnings: list[str], message: str, logger: logging.Logger | None = None) -> None:
    warnings.append(message)
    if logger is not None:
        logger.warning(message)


def _runtime_limit_reached(start_monotonic: float, max_runtime_minutes: float | None) -> bool:
    if max_runtime_minutes is None:
        return False
    elapsed_minutes = (time.monotonic() - start_monotonic) / 60.0
    return elapsed_minutes >= max_runtime_minutes


def _runtime_limit_warning(step_name: str, max_runtime_minutes: float | None) -> str:
    if max_runtime_minutes is None:
        return ""
    return f"Stopped before starting {step_name} because max runtime of {max_runtime_minutes} minutes was reached."


def _top_verified_pressure_records(pressure_review_path: str, limit: int = 3) -> list[dict[str, Any]]:
    rows = _read_csv_rows(Path(pressure_review_path))
    verified = [
        row
        for row in rows
        if str(row.get("pressure_signal") or "").lower() == "true"
        and str(row.get("pressure_verification_status") or "") == "source_text_verified"
    ]
    verified.sort(key=lambda row: (str(row.get("pressure_type") or ""), str(row.get("source_record_id") or "")))
    selected: list[dict[str, Any]] = []
    for row in verified[:limit]:
        selected.append(
            {
                "source_record_id": row.get("source_record_id") or "",
                "source_title": row.get("source_title") or "",
                "location_name": row.get("location_name") or "",
                "pressure_type": row.get("pressure_type") or "",
                "pressure_summary": row.get("pressure_summary") or "",
                "evidence_text": row.get("evidence_text") or "",
            }
        )
    return selected


def _default_blue_fern_compliance_result(root: Path, date: str) -> dict[str, Any]:
    report_dir = _report_dir(root, date)
    return {
        "ok": True,
        "date": date,
        "dispatch_slug": "food-line",
        "dispatch_name": "Food Line Dispatch",
        "logo_checks": {},
        "visual_checks": {},
        "product_checks": {},
        "pressure_marker_checks": {},
        "source_table_checks": {},
        "mobile_basic_html_checks": {},
        "warnings": [],
        "failures": [],
        "checked_files": [],
        "report_json": str(report_dir / "blue_fern_compliance_report.json"),
        "report_md": str(report_dir / "blue_fern_compliance_report.md"),
    }


def _render_markdown_report(report: dict[str, Any]) -> str:
    cleanup = report.get("cleanup") or {}
    discovery = report.get("discovery") or {}
    candidate = report.get("candidate_testing") or {}
    promotion = report.get("promotion") or {}
    production = report.get("production") or {}
    lines = [
        f"# Food Line Daily Ops {report.get('date')}",
        "",
        f"- Cleanup mode: `{report.get('cleanup_mode')}`",
        f"- Cleanup archived: `{report.get('cleanup_archived_count')}`",
        f"- Cleanup quarantined: `{report.get('cleanup_quarantined_count')}`",
        f"- Discovery inserted: `{report.get('discovery_inserted_count')}`",
        f"- Discovery updated: `{report.get('discovery_updated_count')}`",
        f"- Candidate count: `{report.get('candidate_count')}`",
        f"- Promoted candidates: `{report.get('promoted_candidate_count')}`",
        f"- Production ok: `{report.get('production_ok')}`",
        f"- Pressure verified: `{report.get('pressure_verified_count')}`",
        f"- Pressure markers: `{report.get('pressure_marker_count')}`",
        f"- Published: `{report.get('published')}`",
        f"- Pushed: `{report.get('pushed')}`",
    ]
    lines.extend(
        [
            "",
            "## Audio Diagnostics",
            f"- audio_generated: `{report.get('audio_generated')}`",
            f"- audio_required: `{report.get('audio_required')}`",
            f"- audio_status: `{report.get('audio_status')}`",
            f"- audio_mp3_path: `{report.get('audio_mp3_path')}`",
            f"- audio_mp3_url: `{report.get('audio_mp3_url')}`",
            f"- podcast_enclosure_present: `{report.get('podcast_enclosure_present')}`",
            f"- tts_provider: `{report.get('tts_provider')}`",
            f"- tts_model_requested: `{report.get('tts_model_requested')}`",
            f"- tts_voice_requested: `{report.get('tts_voice_requested')}`",
            f"- tts_narration_char_count: `{report.get('tts_narration_char_count')}`",
            f"- tts_output_path_attempted: `{report.get('tts_output_path_attempted')}`",
            f"- tts_api_key_present: `{report.get('tts_api_key_present')}`",
            f"- tts_output_dir_exists: `{report.get('tts_output_dir_exists')}`",
            f"- tts_partial_mp3_exists: `{report.get('tts_partial_mp3_exists')}`",
            f"- tts_elapsed_seconds: `{report.get('tts_elapsed_seconds')}`",
            f"- tts_exception_type: `{report.get('tts_exception_type')}`",
            f"- tts_exception_message_sanitized: `{report.get('tts_exception_message_sanitized')}`",
            f"- tts_error_type: `{report.get('tts_error_type')}`",
            f"- tts_error_message_sanitized: `{report.get('tts_error_message_sanitized')}`",
            f"- tts_timeout_seconds: `{report.get('tts_timeout_seconds')}`",
            f"- tts_audio_format: `{report.get('tts_audio_format')}`",
            f"- tls_verify: `{report.get('tls_verify')}`",
            f"- ca_file_used: `{report.get('ca_file_used')}`",
            f"- ca_source: `{report.get('ca_source')}`",
            f"- truststore_requested: `{report.get('truststore_requested')}`",
            f"- truststore_available: `{report.get('truststore_available')}`",
            f"- ssl_cert_file_env: `{report.get('ssl_cert_file_env')}`",
            f"- requests_ca_bundle_env: `{report.get('requests_ca_bundle_env')}`",
            f"- bluefern_tts_ca_file_env: `{report.get('bluefern_tts_ca_file_env')}`",
            f"- tls_workaround_warning: `{report.get('tls_workaround_warning')}`",
            f"- tts_file_write_exception_type: `{report.get('tts_file_write_exception_type')}`",
            f"- tts_file_write_exception_message_sanitized: `{report.get('tts_file_write_exception_message_sanitized')}`",
        ]
    )
    if report.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {warning}" for warning in report.get("warnings") or []])
    if report.get("top_verified_pressure_records"):
        lines.extend(["", "## Top Verified Pressure Records"])
        for row in report.get("top_verified_pressure_records") or []:
            lines.append(
                f"- {row.get('source_title') or row.get('source_record_id')}: {row.get('pressure_type') or ''} "
                f"({row.get('location_name') or ''})"
            )
    compliance = report.get("blue_fern_compliance") or {}
    lines.extend(
        [
            "",
            "## Blue Fern Compliance",
            f"- ok: `{report.get('blue_fern_compliance_ok')}`",
            f"- report json: `{report.get('blue_fern_compliance_report_json') or compliance.get('report_json') or ''}`",
            f"- report md: `{report.get('blue_fern_compliance_report_md') or compliance.get('report_md') or ''}`",
        ]
    )
    if compliance.get("warnings"):
        lines.extend(["", "### Compliance Warnings"])
        lines.extend([f"- {warning}" for warning in compliance.get("warnings") or []])
    if compliance.get("failures"):
        lines.extend(["", "### Compliance Failures"])
        lines.extend([f"- {failure}" for failure in compliance.get("failures") or []])
    lines.extend(
        [
            "",
            "## Artifacts",
            f"- Cleanup report: `{cleanup.get('cleanup_report_path') or ''}`",
            f"- Discovery review: `{discovery.get('review_path') or ''}`",
            f"- Candidate review: `{candidate.get('candidate_review_path') or ''}`",
            f"- Promotion report: `{promotion.get('candidate_promotion_report_path') or ''}`",
            f"- Production pressure review: `{production.get('pressure_review_path') or ''}`",
            f"- Production collector audit: `{production.get('collector_audit_path') or ''}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def run_food_line_daily_ops(
    root: Path,
    date: str,
    *,
    publish: bool = False,
    push: bool = False,
    skip_discovery: bool = False,
    skip_cleanup: bool = False,
    cleanup_mode: str = "conservative",
    dry_run: bool = False,
    max_insertions: int = 100,
    min_source_quality_score: float = 0.45,
    families: list[str] | None = None,
    skip_known_bad: bool = True,
    skip_quarantined: bool = True,
    skip_archived: bool = True,
    no_discovery_safe_mode: bool = False,
    max_runtime_minutes: float | None = None,
    check_blue_fern_compliance: bool = False,
    generate_audio: bool = False,
    require_audio: bool = False,
    force_audio_regenerate: bool = False,
    tts_provider: str = "none",
    audio_model: str = "gpt-4o-mini-tts",
    audio_voice: str = "alloy",
    audio_format: str = "mp3",
    audio_timeout_seconds: float = 90.0,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    if push and not publish:
        raise ValueError("--push requires --publish")

    date = str(date)
    root = Path(root)
    families = families or ["local_news", "public_radio", "food_bank_provider", "state_official"]
    report_paths = _report_paths(root, date)
    warnings: list[str] = []
    start_monotonic = time.monotonic()
    cleanup_summary: dict[str, Any] = {"ok": True, "skipped": skip_cleanup, "dry_run": dry_run}
    discovery_summary: dict[str, Any] = {"ok": True, "skipped": skip_discovery or no_discovery_safe_mode, "dry_run": dry_run}
    candidate_summary: dict[str, Any] = {"ok": True, "skipped": False, "dry_run": dry_run}
    production_summary: dict[str, Any] = {"ok": True, "skipped": False}
    blue_fern_compliance = _default_blue_fern_compliance_result(root, date)
    published = False
    pushed = False
    stopped_due_runtime_limit = False
    runtime_stop_step = ""

    if logger is not None:
        logger.info("Food Line daily ops start for date=%s", date)
        logger.info(
            "Command context: %s",
            json.dumps(
                {
                    "publish": publish,
                    "push": push,
                    "skip_discovery": skip_discovery,
                    "skip_cleanup": skip_cleanup,
                    "cleanup_mode": cleanup_mode,
                    "dry_run": dry_run,
                    "max_insertions": max_insertions,
                    "min_source_quality_score": min_source_quality_score,
                    "families": families,
                    "skip_known_bad": skip_known_bad,
                    "skip_quarantined": skip_quarantined,
                    "skip_archived": skip_archived,
                    "no_discovery_safe_mode": no_discovery_safe_mode,
                "max_runtime_minutes": max_runtime_minutes,
                "generate_audio": generate_audio,
                "require_audio": require_audio,
                "force_audio_regenerate": force_audio_regenerate,
            },
            ensure_ascii=False,
        ),
        )

    if not skip_cleanup:
        if _runtime_limit_reached(start_monotonic, max_runtime_minutes):
            stopped_due_runtime_limit = True
            runtime_stop_step = "cleanup"
            _append_warning(warnings, _runtime_limit_warning("cleanup", max_runtime_minutes), logger)
            cleanup_summary = {"ok": False, "skipped": True, "dry_run": dry_run}
        else:
            if logger is not None:
                logger.info("Substep start: cleanup")
                cleanup_started = time.monotonic()
            cleanup_summary = cleanup_food_line_candidates(root, mode=cleanup_mode, dry_run=dry_run)
            if logger is not None:
                logger.info("Substep end: cleanup duration_seconds=%.2f", time.monotonic() - cleanup_started)

    if not skip_discovery and not no_discovery_safe_mode:
        if _runtime_limit_reached(start_monotonic, max_runtime_minutes):
            stopped_due_runtime_limit = True
            runtime_stop_step = runtime_stop_step or "discovery"
            _append_warning(warnings, _runtime_limit_warning("discovery", max_runtime_minutes), logger)
            discovery_summary = {"ok": False, "skipped": True, "dry_run": dry_run}
        else:
            if logger is not None:
                logger.info("Substep start: discovery")
                discovery_started = time.monotonic()
            discovery_summary = discover_food_line_sources(
                root,
                date,
                max_insertions=max_insertions,
                families=families,
                min_source_quality_score=min_source_quality_score,
                skip_known_bad=skip_known_bad,
                skip_quarantined=skip_quarantined,
                skip_archived=skip_archived,
                write_candidates=not dry_run,
                dry_run=dry_run,
            )
            if logger is not None:
                logger.info("Substep end: discovery duration_seconds=%.2f", time.monotonic() - discovery_started)
            if int(discovery_summary.get("inserted_count") or 0) > max_insertions:
                _append_warning(warnings, "Discovery inserted more candidates than the requested max-insertions cap.", logger)
                discovery_summary["ok"] = False
    elif no_discovery_safe_mode and logger is not None:
        logger.info("Substep skipped: discovery (no-discovery-safe-mode)")

    if _runtime_limit_reached(start_monotonic, max_runtime_minutes):
        stopped_due_runtime_limit = True
        runtime_stop_step = runtime_stop_step or "candidate_testing"
        _append_warning(warnings, _runtime_limit_warning("candidate_testing", max_runtime_minutes), logger)
        candidate_summary = {"ok": False, "skipped": True, "dry_run": dry_run}
    else:
        if logger is not None:
            logger.info("Substep start: candidate_testing")
            candidate_started = time.monotonic()
        candidate_summary = test_food_line_candidate_sources(
            root,
            date,
            promote_enabled=not dry_run,
        )
        if logger is not None:
            logger.info("Substep end: candidate_testing duration_seconds=%.2f", time.monotonic() - candidate_started)

    if _runtime_limit_reached(start_monotonic, max_runtime_minutes):
        stopped_due_runtime_limit = True
        runtime_stop_step = runtime_stop_step or "production"
        _append_warning(warnings, _runtime_limit_warning("production", max_runtime_minutes), logger)
        production_summary = {
            "ok": False,
            "skipped": True,
            "pressure_verified_count": 0,
            "pressure_marker_count": 0,
            "pressure_review_path": str(_report_dir(root, date) / "pressure_review.csv"),
            "collector_audit_path": str(root / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"),
        }
    else:
        if logger is not None:
            logger.info("Substep start: production")
            production_started = time.monotonic()
        production_summary = run_food_line_dispatch(
            root,
            date,
            collect=not dry_run,
            generate_audio=generate_audio,
            require_audio=require_audio,
            force_audio_regenerate=force_audio_regenerate,
            tts_provider=tts_provider,
            audio_model=audio_model,
            audio_voice=audio_voice,
            audio_format=audio_format,
            audio_timeout_seconds=audio_timeout_seconds,
        )
        if logger is not None:
            logger.info("Substep end: production duration_seconds=%.2f", time.monotonic() - production_started)

    audio_ready = bool(production_summary.get("audio_available")) and bool(production_summary.get("podcast_enclosure_present"))
    if require_audio and not audio_ready:
        production_summary["ok"] = False
        production_summary["audio_required"] = True
        _append_warning(warnings, "Required Food Line audio narration was not generated; publish and push were skipped.", logger)

    production_ok = bool(production_summary.get("ok"))
    if not production_ok and not production_summary.get("skipped"):
        _append_warning(warnings, "Production run failed; publish and push were skipped.", logger)
    for warning in list(production_summary.get("warnings") or []):
        _append_warning(warnings, str(warning), logger)

    if check_blue_fern_compliance:
        try:
            blue_fern_compliance = run_food_line_blue_fern_compliance(root, date)
        except Exception as exc:  # noqa: BLE001
            blue_fern_compliance = _default_blue_fern_compliance_result(root, date) | {
                "ok": False,
                "warnings": [str(exc)],
                "failures": [str(exc)],
            }
        if not blue_fern_compliance.get("ok"):
            _append_warning(warnings, "Blue Fern compliance failed; push will be skipped.", logger)

    if production_ok and publish and not _runtime_limit_reached(start_monotonic, max_runtime_minutes):
        if logger is not None:
            logger.info("Substep start: publish")
            publish_started = time.monotonic()
        published, publish_errors, publish_payload = publish_food_line_pages(root, date)
        production_summary["pages_publish_result"] = publish_payload
        production_summary["pages_publish_copied"] = published
        if logger is not None:
            logger.info("Substep end: publish duration_seconds=%.2f", time.monotonic() - publish_started)
        if not published:
            for error in publish_errors or ["Publish failed."]:
                _append_warning(warnings, error, logger)
        elif push and bool(blue_fern_compliance.get("ok", True)):
            if logger is not None:
                logger.info("Substep start: push")
                push_started = time.monotonic()
            pushed, push_message = push_pages_repo()
            production_summary["pushed"] = pushed
            if logger is not None:
                logger.info("Substep end: push duration_seconds=%.2f", time.monotonic() - push_started)
            if not pushed:
                _append_warning(warnings, push_message, logger)
        elif push and not bool(blue_fern_compliance.get("ok", True)):
            production_summary["pushed"] = False
    elif publish and not production_ok:
        production_summary["pages_publish_copied"] = False
        production_summary["pushed"] = False
    elif publish and _runtime_limit_reached(start_monotonic, max_runtime_minutes):
        _append_warning(warnings, _runtime_limit_warning("publish", max_runtime_minutes), logger)
        production_summary["pages_publish_copied"] = False
        production_summary["pushed"] = False
    else:
        production_summary["pages_publish_copied"] = False
        production_summary["pushed"] = False

    pressure_verified_count = int(production_summary.get("pressure_verified_count") or 0)
    pressure_marker_count = int(production_summary.get("pressure_marker_count") or 0)
    if pressure_verified_count == 0:
        _append_warning(warnings, "No verified pressure records found; edition is monitoring/context only.", logger)

    cleanup_before = int(cleanup_summary.get("candidate_count_before") or cleanup_summary.get("candidate_count") or 0)
    cleanup_archived = int(cleanup_summary.get("archived_count") or 0)
    cleanup_quarantined = int(cleanup_summary.get("quarantined_count") or 0)
    if cleanup_before and cleanup_archived / max(cleanup_before, 1) > 0.5:
        _append_warning(warnings, "Cleanup archived more than 50% of candidate sources.", logger)

    top_verified_records = _top_verified_pressure_records(str(production_summary.get("pressure_review_path") or ""), limit=3)
    report = {
        "date": date,
        "cleanup_mode": cleanup_mode,
        "cleanup": cleanup_summary,
        "discovery": discovery_summary,
        "candidate_testing": candidate_summary,
        "promotion": {
            "ok": True,
            "candidate_promotion_report_path": candidate_summary.get("candidate_promotion_report_path") or "",
            "promoted_candidate_count": int(candidate_summary.get("promoted_candidate_count") or 0),
            "promoted_source_ids": list(candidate_summary.get("promoted_source_ids") or []),
        },
        "production": production_summary,
        "pressure_verified_count": pressure_verified_count,
        "pressure_marker_count": pressure_marker_count,
        "selected_lead": {
            "source_record_id": production_summary.get("lead_source_record_id") or production_summary.get("selected_lead_source_record_id") or "",
            "pressure_type": production_summary.get("selected_lead_pressure_type") or "",
            "affected_groups": production_summary.get("selected_lead_affected_groups") or [],
        },
        "top_verified_pressure_records": top_verified_records,
        "warnings": warnings,
        "artifact_paths": {
            "cleanup_report_path": cleanup_summary.get("cleanup_report_path") or "",
            "cleanup_health_report_path": cleanup_summary.get("source_registry_health_report_path") or "",
            "discovery_review_path": discovery_summary.get("review_path") or "",
            "discovery_audit_path": discovery_summary.get("audit_path") or "",
            "discovery_query_performance_report_path": discovery_summary.get("query_performance_report_path") or "",
            "candidate_review_path": candidate_summary.get("candidate_review_path") or "",
            "candidate_audit_path": candidate_summary.get("candidate_audit_path") or "",
            "candidate_promotion_report_path": candidate_summary.get("candidate_promotion_report_path") or "",
            "production_collector_audit_path": production_summary.get("collector_audit_path") or "",
            "production_pressure_review_path": production_summary.get("pressure_review_path") or "",
            "daily_ops_report_json": report_paths["daily_ops_report_json"],
            "daily_ops_report_md": report_paths["daily_ops_report_md"],
        },
        "published": published,
        "pushed": pushed,
        "production_ok": production_ok,
        "cleanup_quarantined_count": cleanup_quarantined,
        "cleanup_archived_count": cleanup_archived,
        "discovery_inserted_count": int(discovery_summary.get("inserted_count") or 0),
        "discovery_updated_count": int(discovery_summary.get("updated_count") or 0),
        "candidate_count": int(candidate_summary.get("candidate_count") or 0),
        "promoted_candidate_count": int(candidate_summary.get("promoted_candidate_count") or 0),
        "daily_ops_report_json": report_paths["daily_ops_report_json"],
        "daily_ops_report_md": report_paths["daily_ops_report_md"],
        "blue_fern_compliance": blue_fern_compliance,
        "blue_fern_compliance_ok": bool(blue_fern_compliance.get("ok")),
        "blue_fern_compliance_report_json": str(blue_fern_compliance.get("report_json") or report_paths["daily_ops_report_json"]),
        "blue_fern_compliance_report_md": str(blue_fern_compliance.get("report_md") or report_paths["daily_ops_report_md"]),
        "audio_generated": bool(production_summary.get("audio_generated")),
        "audio_available": bool(production_summary.get("audio_available")),
        "audio_reused_existing": bool(production_summary.get("audio_reused_existing")),
        "audio_required": bool(production_summary.get("audio_required")),
        "audio_mp3_path": production_summary.get("audio_mp3_path"),
        "audio_mp3_url": production_summary.get("audio_mp3_url"),
        "podcast_enclosure_present": bool(production_summary.get("podcast_enclosure_present")),
        "existing_audio_mp3_path": production_summary.get("existing_audio_mp3_path"),
        "existing_audio_mp3_size": production_summary.get("existing_audio_mp3_size"),
        "force_audio_regenerate": bool(production_summary.get("force_audio_regenerate")),
        "audio_temp_path": production_summary.get("audio_temp_path"),
        "audio_replacement_performed": bool(production_summary.get("audio_replacement_performed")),
        "audio_status": production_summary.get("audio_status"),
        "audio_timeout_seconds": production_summary.get("audio_timeout_seconds"),
        "tts_provider": production_summary.get("tts_provider"),
        "tts_model_requested": production_summary.get("tts_model_requested"),
        "tts_voice_requested": production_summary.get("tts_voice_requested"),
        "tts_narration_char_count": production_summary.get("tts_narration_char_count"),
        "tts_output_path_attempted": production_summary.get("tts_output_path_attempted"),
        "tts_api_key_present": production_summary.get("tts_api_key_present"),
        "tts_output_dir_exists": production_summary.get("tts_output_dir_exists"),
        "tts_partial_mp3_exists": production_summary.get("tts_partial_mp3_exists"),
        "tts_elapsed_seconds": production_summary.get("tts_elapsed_seconds"),
        "tts_exception_type": production_summary.get("tts_exception_type"),
        "tts_exception_message_sanitized": production_summary.get("tts_exception_message_sanitized"),
        "tts_error_type": production_summary.get("tts_error_type"),
        "tts_error_message_sanitized": production_summary.get("tts_error_message_sanitized"),
        "tts_timeout_seconds": production_summary.get("tts_timeout_seconds"),
        "tts_audio_format": production_summary.get("tts_audio_format"),
        "tls_verify": production_summary.get("tls_verify"),
        "ca_file_used": production_summary.get("ca_file_used"),
        "ca_source": production_summary.get("ca_source"),
        "truststore_requested": production_summary.get("truststore_requested"),
        "truststore_available": production_summary.get("truststore_available"),
        "ssl_cert_file_env": production_summary.get("ssl_cert_file_env"),
        "requests_ca_bundle_env": production_summary.get("requests_ca_bundle_env"),
        "bluefern_tts_ca_file_env": production_summary.get("bluefern_tts_ca_file_env"),
        "tls_workaround_warning": production_summary.get("tls_workaround_warning"),
        "tts_file_write_exception_type": production_summary.get("tts_file_write_exception_type"),
        "tts_file_write_exception_message_sanitized": production_summary.get("tts_file_write_exception_message_sanitized"),
        "stopped_due_runtime_limit": stopped_due_runtime_limit,
        "runtime_stop_step": runtime_stop_step,
        "no_discovery_safe_mode": no_discovery_safe_mode,
        "max_runtime_minutes": max_runtime_minutes,
    }
    report["ok"] = production_ok and (not publish or published) and (not push or pushed) and not stopped_due_runtime_limit and (not check_blue_fern_compliance or bool(blue_fern_compliance.get("ok")))
    if logger is not None:
        logger.info("Food Line daily ops end for date=%s", date)
        logger.info(
            "Final JSON summary: %s",
            json.dumps(
                {
                    "ok": bool(report.get("ok")),
                    "date": report.get("date"),
                    "cleanup_mode": report.get("cleanup_mode"),
                    "cleanup_quarantined_count": report.get("cleanup_quarantined_count"),
                    "cleanup_archived_count": report.get("cleanup_archived_count"),
                    "discovery_inserted_count": report.get("discovery_inserted_count"),
                    "discovery_updated_count": report.get("discovery_updated_count"),
                    "candidate_count": report.get("candidate_count"),
                    "promoted_candidate_count": report.get("promoted_candidate_count"),
                    "production_ok": report.get("production_ok"),
                    "pressure_verified_count": report.get("pressure_verified_count"),
                    "pressure_marker_count": report.get("pressure_marker_count"),
                    "published": report.get("published"),
                    "pushed": report.get("pushed"),
                    "blue_fern_compliance_ok": report.get("blue_fern_compliance_ok"),
                    "blue_fern_compliance_report_json": report.get("blue_fern_compliance_report_json"),
                    "blue_fern_compliance_report_md": report.get("blue_fern_compliance_report_md"),
                    "audio_generated": report.get("audio_generated"),
                    "audio_required": report.get("audio_required"),
                    "audio_mp3_path": report.get("audio_mp3_path"),
                    "audio_mp3_url": report.get("audio_mp3_url"),
                    "podcast_enclosure_present": report.get("podcast_enclosure_present"),
                    "audio_status": report.get("audio_status"),
                    "audio_timeout_seconds": report.get("audio_timeout_seconds"),
                    "tts_provider": report.get("tts_provider"),
                    "tts_model_requested": report.get("tts_model_requested"),
                    "tts_voice_requested": report.get("tts_voice_requested"),
                    "tts_narration_char_count": report.get("tts_narration_char_count"),
                    "tts_output_path_attempted": report.get("tts_output_path_attempted"),
                    "tts_api_key_present": report.get("tts_api_key_present"),
                    "tts_output_dir_exists": report.get("tts_output_dir_exists"),
                    "tts_partial_mp3_exists": report.get("tts_partial_mp3_exists"),
                    "tts_elapsed_seconds": report.get("tts_elapsed_seconds"),
                    "tts_exception_type": report.get("tts_exception_type"),
                    "tts_exception_message_sanitized": report.get("tts_exception_message_sanitized"),
                    "tts_timeout_seconds": report.get("tts_timeout_seconds"),
                    "tts_audio_format": report.get("tts_audio_format"),
                    "tls_verify": report.get("tls_verify"),
                    "ca_file_used": report.get("ca_file_used"),
                    "ca_source": report.get("ca_source"),
                    "truststore_requested": report.get("truststore_requested"),
                    "truststore_available": report.get("truststore_available"),
                    "ssl_cert_file_env": report.get("ssl_cert_file_env"),
                    "requests_ca_bundle_env": report.get("requests_ca_bundle_env"),
                    "bluefern_tts_ca_file_env": report.get("bluefern_tts_ca_file_env"),
                    "tls_workaround_warning": report.get("tls_workaround_warning"),
                    "tts_file_write_exception_type": report.get("tts_file_write_exception_type"),
                    "tts_file_write_exception_message_sanitized": report.get("tts_file_write_exception_message_sanitized"),
                    "daily_ops_report_json": report.get("daily_ops_report_json"),
                    "daily_ops_report_md": report.get("daily_ops_report_md"),
                    "warnings": report.get("warnings") or [],
                },
                ensure_ascii=False,
            ),
        )
    _write_json(Path(report_paths["daily_ops_report_json"]), report)
    _write_text(Path(report_paths["daily_ops_report_md"]), _render_markdown_report(report))
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Food Line daily ops workflow.")
    parser.add_argument("--date", help="Edition date YYYY-MM-DD. Defaults to today's local date.")
    parser.add_argument("--publish", action="store_true", help="Publish the generated site to the local Pages repo.")
    parser.add_argument("--push", action="store_true", help="Push the Pages repo after successful publish.")
    parser.add_argument("--skip-discovery", action="store_true", help="Skip source discovery.")
    parser.add_argument("--no-discovery-safe-mode", action="store_true", help="Skip discovery while still running cleanup, candidate testing, and production.")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip candidate cleanup.")
    parser.add_argument("--cleanup-mode", default="conservative", choices=["conservative", "normal", "aggressive"], help="Cleanup mode.")
    parser.add_argument("--dry-run", action="store_true", help="Run cleanup/discovery in dry-run mode and skip mutating promotion/publish/push.")
    parser.add_argument("--max-insertions", type=int, default=100, help="Maximum discovery insertions.")
    parser.add_argument("--min-source-quality-score", type=float, default=0.45, help="Minimum discovery quality score.")
    parser.add_argument("--families", default="local_news,public_radio,food_bank_provider,state_official", help="Comma-separated discovery families.")
    parser.add_argument("--max-runtime-minutes", type=float, help="Stop before starting a new substep once this runtime limit is reached.")
    parser.add_argument("--check-blue-fern-compliance", action="store_true", help="Run the Food Line Blue Fern compliance audit before push.")
    parser.add_argument("--generate-audio", action="store_true", help="Generate Food Line audio narration and podcast MP3 artifacts.")
    parser.add_argument("--require-audio", action="store_true", help="Require Food Line audio MP3 generation before the run can succeed.")
    parser.add_argument("--force-audio-regenerate", action="store_true", help="Regenerate Food Line audio even when an MP3 already exists; preserve the existing MP3 if regeneration fails.")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default="none", help="Optional TTS provider when --generate-audio is used.")
    parser.add_argument("--audio-model", default="gpt-4o-mini-tts", help="TTS model for Food Line audio generation.")
    parser.add_argument("--audio-voice", default="alloy", help="TTS voice for Food Line audio generation.")
    parser.add_argument("--audio-format", choices=("mp3",), default="mp3", help="Audio format for Food Line audio generation.")
    parser.add_argument("--audio-timeout-seconds", type=float, default=90.0, help="Timeout for Food Line TTS requests.")
    parser.add_argument("--skip-known-bad", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-quarantined", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-archived", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(argv)


def _final_console_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "date": result.get("date"),
        "cleanup_mode": result.get("cleanup_mode"),
        "cleanup_quarantined_count": result.get("cleanup_quarantined_count"),
        "cleanup_archived_count": result.get("cleanup_archived_count"),
        "discovery_inserted_count": result.get("discovery_inserted_count"),
        "discovery_updated_count": result.get("discovery_updated_count"),
        "candidate_count": result.get("candidate_count"),
        "promoted_candidate_count": result.get("promoted_candidate_count"),
        "production_ok": result.get("production_ok"),
        "pressure_verified_count": result.get("pressure_verified_count"),
        "pressure_marker_count": result.get("pressure_marker_count"),
        "published": result.get("published"),
        "pushed": result.get("pushed"),
        "blue_fern_compliance_ok": result.get("blue_fern_compliance_ok"),
        "blue_fern_compliance_report_json": result.get("blue_fern_compliance_report_json"),
        "blue_fern_compliance_report_md": result.get("blue_fern_compliance_report_md"),
        "audio_generated": result.get("audio_generated"),
        "audio_available": result.get("audio_available"),
        "audio_reused_existing": result.get("audio_reused_existing"),
        "audio_required": result.get("audio_required"),
        "audio_mp3_path": result.get("audio_mp3_path"),
        "audio_mp3_url": result.get("audio_mp3_url"),
        "podcast_enclosure_present": result.get("podcast_enclosure_present"),
        "existing_audio_mp3_path": result.get("existing_audio_mp3_path"),
        "existing_audio_mp3_size": result.get("existing_audio_mp3_size"),
        "force_audio_regenerate": result.get("force_audio_regenerate"),
        "audio_temp_path": result.get("audio_temp_path"),
        "audio_replacement_performed": result.get("audio_replacement_performed"),
        "audio_status": result.get("audio_status"),
        "audio_timeout_seconds": result.get("audio_timeout_seconds"),
        "tts_provider": result.get("tts_provider"),
        "tts_model_requested": result.get("tts_model_requested"),
        "tts_voice_requested": result.get("tts_voice_requested"),
        "tts_narration_char_count": result.get("tts_narration_char_count"),
        "tts_output_path_attempted": result.get("tts_output_path_attempted"),
        "tts_api_key_present": result.get("tts_api_key_present"),
        "tts_output_dir_exists": result.get("tts_output_dir_exists"),
        "tts_partial_mp3_exists": result.get("tts_partial_mp3_exists"),
        "tts_elapsed_seconds": result.get("tts_elapsed_seconds"),
        "tts_exception_type": result.get("tts_exception_type"),
        "tts_exception_message_sanitized": result.get("tts_exception_message_sanitized"),
        "tts_error_type": result.get("tts_error_type"),
        "tts_error_message_sanitized": result.get("tts_error_message_sanitized"),
        "tts_timeout_seconds": result.get("tts_timeout_seconds"),
        "tts_audio_format": result.get("tts_audio_format"),
        "tls_verify": result.get("tls_verify"),
        "ca_file_used": result.get("ca_file_used"),
        "ca_source": result.get("ca_source"),
        "truststore_requested": result.get("truststore_requested"),
        "truststore_available": result.get("truststore_available"),
        "ssl_cert_file_env": result.get("ssl_cert_file_env"),
        "requests_ca_bundle_env": result.get("requests_ca_bundle_env"),
        "bluefern_tts_ca_file_env": result.get("bluefern_tts_ca_file_env"),
        "tls_workaround_warning": result.get("tls_workaround_warning"),
        "tts_file_write_exception_type": result.get("tts_file_write_exception_type"),
        "tts_file_write_exception_message_sanitized": result.get("tts_file_write_exception_message_sanitized"),
        "daily_ops_report_json": result.get("daily_ops_report_json"),
        "daily_ops_report_md": result.get("daily_ops_report_md"),
        "warnings": result.get("warnings") or [],
        "reason": result.get("reason"),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    date = args.date or _today_local_date()
    log_path = _log_path(ROOT, date)
    status_path = _status_path(ROOT)
    report_paths = _report_paths(ROOT, date)
    status_payload: dict[str, Any] = {
        "last_run_at": None,
        "last_run_date": date,
        "ok": False,
        "production_ok": False,
        "published": False,
        "pushed": False,
        "blue_fern_compliance_ok": False,
        "blue_fern_compliance_report_json": str(_report_dir(ROOT, date) / "blue_fern_compliance_report.json"),
        "blue_fern_compliance_report_md": str(_report_dir(ROOT, date) / "blue_fern_compliance_report.md"),
        "audio_generated": False,
        "audio_available": False,
        "audio_reused_existing": False,
        "audio_required": False,
        "audio_mp3_path": None,
        "audio_mp3_url": None,
        "podcast_enclosure_present": False,
        "existing_audio_mp3_path": None,
        "existing_audio_mp3_size": None,
        "force_audio_regenerate": False,
        "audio_temp_path": None,
        "audio_replacement_performed": False,
        "audio_status": None,
        "audio_timeout_seconds": None,
        "tts_provider": None,
        "tts_model_requested": None,
        "tts_voice_requested": None,
        "tts_narration_char_count": None,
        "tts_output_path_attempted": None,
        "tts_api_key_present": None,
        "tts_output_dir_exists": None,
        "tts_partial_mp3_exists": None,
        "tts_elapsed_seconds": None,
        "tts_exception_type": None,
        "tts_exception_message_sanitized": None,
        "tts_error_type": None,
        "tts_error_message_sanitized": None,
        "tts_timeout_seconds": None,
        "tts_audio_format": None,
        "tls_verify": None,
        "ca_file_used": None,
        "ca_source": None,
        "truststore_requested": None,
        "truststore_available": None,
        "ssl_cert_file_env": None,
        "requests_ca_bundle_env": None,
        "bluefern_tts_ca_file_env": None,
        "tls_workaround_warning": None,
        "tts_file_write_exception_type": None,
        "tts_file_write_exception_message_sanitized": None,
        "pressure_verified_count": 0,
        "pressure_marker_count": 0,
        "warnings": [],
        "latest_report_json": report_paths["daily_ops_report_json"],
        "latest_report_md": report_paths["daily_ops_report_md"],
        "latest_log_path": str(log_path),
    }
    result: dict[str, Any] = {
        "ok": False,
        "date": date,
        "daily_ops_report_json": report_paths["daily_ops_report_json"],
        "daily_ops_report_md": report_paths["daily_ops_report_md"],
        "blue_fern_compliance_ok": False,
        "blue_fern_compliance_report_json": str(_report_dir(ROOT, date) / "blue_fern_compliance_report.json"),
        "blue_fern_compliance_report_md": str(_report_dir(ROOT, date) / "blue_fern_compliance_report.md"),
        "audio_status": None,
        "audio_timeout_seconds": None,
        "audio_available": None,
        "audio_reused_existing": None,
        "existing_audio_mp3_path": None,
        "existing_audio_mp3_size": None,
        "force_audio_regenerate": None,
        "audio_temp_path": None,
        "audio_replacement_performed": None,
        "tts_provider": None,
        "tts_model_requested": None,
        "tts_voice_requested": None,
        "tts_narration_char_count": None,
        "tts_output_path_attempted": None,
        "tts_api_key_present": None,
        "tts_output_dir_exists": None,
        "tts_partial_mp3_exists": None,
        "tts_elapsed_seconds": None,
        "tts_exception_type": None,
        "tts_exception_message_sanitized": None,
        "tts_error_type": None,
        "tts_error_message_sanitized": None,
        "tts_timeout_seconds": None,
        "tts_audio_format": None,
        "tls_verify": None,
        "ca_file_used": None,
        "ca_source": None,
        "truststore_requested": None,
        "truststore_available": None,
        "ssl_cert_file_env": None,
        "requests_ca_bundle_env": None,
        "bluefern_tts_ca_file_env": None,
        "tls_workaround_warning": None,
        "tts_file_write_exception_type": None,
        "tts_file_write_exception_message_sanitized": None,
        "warnings": [],
    }
    exit_code = 1
    lock_acquired = False
    start_at = datetime.now().astimezone()
    logger = _configure_file_logger(log_path)

    if args.push and not args.publish:
        result.update({"reason": "--push requires --publish", "warnings": ["--push requires --publish"]})
        logger.info("Daily ops start_at=%s", start_at.isoformat())
        logger.info("Daily ops log path: %s", log_path)
        logger.info("Daily ops lock path: %s", _lock_path(ROOT))
        logger.info("Command arguments: %s", json.dumps(vars(args), ensure_ascii=False))
        logger.warning("--push requires --publish")
        _write_json(status_path, status_payload | {"last_run_at": datetime.now().astimezone().isoformat(), "warnings": result["warnings"]})
        print(json.dumps(_final_console_payload(result), indent=2))
        _close_file_logger(logger)
        return 3

    lock_path = _lock_path(ROOT)
    try:
        logger.info("Daily ops start_at=%s", start_at.isoformat())
        logger.info("Daily ops log path: %s", log_path)
        logger.info("Daily ops lock path: %s", lock_path)
        logger.info("Command arguments: %s", json.dumps(vars(args), ensure_ascii=False))
        lock_acquired, lock_reason = _acquire_lock(lock_path)
        if not lock_acquired:
            result.update(
                {
                    "reason": lock_reason or "Food Line daily ops is already running.",
                    "warnings": [lock_reason or "Food Line daily ops is already running."],
                }
            )
            logger.warning(result["reason"])
            exit_code = 2
        else:
            result = run_food_line_daily_ops(
                ROOT,
                date,
                publish=bool(args.publish),
                push=bool(args.push),
                skip_discovery=bool(args.skip_discovery),
                skip_cleanup=bool(args.skip_cleanup),
                cleanup_mode=str(args.cleanup_mode),
                dry_run=bool(args.dry_run),
                max_insertions=int(args.max_insertions),
                min_source_quality_score=float(args.min_source_quality_score),
                families=_parse_families(args.families),
                skip_known_bad=bool(args.skip_known_bad),
                skip_quarantined=bool(args.skip_quarantined),
                skip_archived=bool(args.skip_archived),
                no_discovery_safe_mode=bool(args.no_discovery_safe_mode),
                max_runtime_minutes=_parse_max_runtime_minutes(str(args.max_runtime_minutes) if args.max_runtime_minutes is not None else None),
                check_blue_fern_compliance=bool(args.check_blue_fern_compliance),
                generate_audio=bool(args.generate_audio),
                require_audio=bool(args.require_audio),
                force_audio_regenerate=bool(args.force_audio_regenerate),
                tts_provider=str(args.tts_provider or "none"),
                audio_model=str(args.audio_model or "gpt-4o-mini-tts"),
                audio_voice=str(args.audio_voice or "alloy"),
                audio_format=str(args.audio_format or "mp3"),
                audio_timeout_seconds=float(args.audio_timeout_seconds or 90.0),
                logger=logger,
            )
            exit_code = 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled exception during Food Line daily ops")
        traceback_text = traceback.format_exc()
        logger.error(traceback_text)
        result.update({"reason": str(exc), "warnings": [str(exc)]})
        exit_code = 1
    finally:
        if lock_acquired:
            _release_lock(lock_path)
        end_at = datetime.now().astimezone()
        status_payload.update(
            {
                "last_run_at": end_at.isoformat(),
                "last_run_date": date,
                "ok": bool(result.get("ok")),
                "production_ok": bool(result.get("production_ok")),
                "published": bool(result.get("published")),
                "pushed": bool(result.get("pushed")),
                "blue_fern_compliance_ok": bool(result.get("blue_fern_compliance_ok")),
                "blue_fern_compliance_report_json": str(result.get("blue_fern_compliance_report_json") or report_paths["daily_ops_report_json"]),
                "blue_fern_compliance_report_md": str(result.get("blue_fern_compliance_report_md") or report_paths["daily_ops_report_md"]),
                "audio_generated": bool(result.get("audio_generated")),
                "audio_available": bool(result.get("audio_available")),
                "audio_reused_existing": bool(result.get("audio_reused_existing")),
                "audio_required": bool(result.get("audio_required")),
                "audio_mp3_path": result.get("audio_mp3_path"),
                "audio_mp3_url": result.get("audio_mp3_url"),
                "podcast_enclosure_present": bool(result.get("podcast_enclosure_present")),
                "existing_audio_mp3_path": result.get("existing_audio_mp3_path"),
                "existing_audio_mp3_size": result.get("existing_audio_mp3_size"),
                "force_audio_regenerate": bool(result.get("force_audio_regenerate")),
                "audio_temp_path": result.get("audio_temp_path"),
                "audio_replacement_performed": bool(result.get("audio_replacement_performed")),
                "audio_status": result.get("audio_status"),
                "audio_timeout_seconds": result.get("audio_timeout_seconds"),
                "tts_provider": result.get("tts_provider"),
                "tts_model_requested": result.get("tts_model_requested"),
                "tts_voice_requested": result.get("tts_voice_requested"),
                "tts_narration_char_count": result.get("tts_narration_char_count"),
                "tts_output_path_attempted": result.get("tts_output_path_attempted"),
                "tts_api_key_present": result.get("tts_api_key_present"),
                "tts_output_dir_exists": result.get("tts_output_dir_exists"),
                "tts_partial_mp3_exists": result.get("tts_partial_mp3_exists"),
                "tts_elapsed_seconds": result.get("tts_elapsed_seconds"),
                "tts_exception_type": result.get("tts_exception_type"),
                "tts_exception_message_sanitized": result.get("tts_exception_message_sanitized"),
                "tts_error_type": result.get("tts_error_type"),
                "tts_error_message_sanitized": result.get("tts_error_message_sanitized"),
                "tts_timeout_seconds": result.get("tts_timeout_seconds"),
                "tts_audio_format": result.get("tts_audio_format"),
                "tls_verify": result.get("tls_verify"),
                "ca_file_used": result.get("ca_file_used"),
                "ca_source": result.get("ca_source"),
                "truststore_requested": result.get("truststore_requested"),
                "truststore_available": result.get("truststore_available"),
                "ssl_cert_file_env": result.get("ssl_cert_file_env"),
                "requests_ca_bundle_env": result.get("requests_ca_bundle_env"),
                "bluefern_tts_ca_file_env": result.get("bluefern_tts_ca_file_env"),
                "tls_workaround_warning": result.get("tls_workaround_warning"),
                "tts_file_write_exception_type": result.get("tts_file_write_exception_type"),
                "tts_file_write_exception_message_sanitized": result.get("tts_file_write_exception_message_sanitized"),
                "pressure_verified_count": int(result.get("pressure_verified_count") or 0),
                "pressure_marker_count": int(result.get("pressure_marker_count") or 0),
                "warnings": list(result.get("warnings") or []),
                "latest_report_json": str(result.get("daily_ops_report_json") or report_paths["daily_ops_report_json"]),
                "latest_report_md": str(result.get("daily_ops_report_md") or report_paths["daily_ops_report_md"]),
                "latest_log_path": str(log_path),
            }
        )
        _write_json(status_path, status_payload)
        logger.info("Status artifact path: %s", status_path)
        logger.info("Status payload: %s", json.dumps(status_payload, ensure_ascii=False))
        logger.info("Daily ops end_at=%s", end_at.isoformat())

    print(json.dumps(_final_console_payload(result), indent=2))
    _close_file_logger(logger)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
