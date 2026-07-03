from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import ssl
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_sources import build_gaza_collection_timing_metadata
from bluefern_dispatches.gaza_sources import collect_gaza_sources
from bluefern_dispatches.gaza_sources import validate_source_records as validate_collected_source_records
from bluefern_dispatches.gaza_sources import write_source_records
from bluefern_dispatches.bluesky_post import maybe_post_gaza_dispatch_to_bluesky
from scripts.run_and_notify import notification_error_message, send_email
from scripts.publish_gaza_historical import (
    BASE_PUBLIC_URL,
    DEFAULT_PAGES_BRANCH,
    DEFAULT_REMOTE_URL,
    LinkTextParser,
    command_text,
    manual_push_command,
    parse_json_stdout,
    pages_publish_command,
    pages_sync_repair_message,
    required_output_paths,
    run_command,
    validate_generated_output,
    validate_pages_outputs,
    validate_public_site_has_no_detail_files,
    valid_source_errors,
)
from scripts.validation_profiles import (
    PROFILE_GAZA_DAILY,
    apply_env_profile,
    get_profile,
    make_pytest_basetemp,
    profile_names,
    pytest_command,
)


DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE_MODES = {"auto", "manual", "both"}
REQUIRED_PUBLIC_SUMMARY_FIELDS = (
    "ok",
    "date",
    "mode",
    "source_mode",
    "source_file",
    "source_count",
    "source_adequacy_status",
    "publisher_count",
    "publishers",
    "generation_ok",
    "generated",
    "archive_updated",
    "rss_updated",
    "tests_run",
    "tests_ok",
    "validation_ok",
    "validation_profile",
    "tests_command",
    "skipped_unrelated_tests",
    "pipeline_ok",
    "email_requested",
    "email_ok",
    "notification_error",
    "overall_ok",
    "publish_ok",
    "publish_blocked",
    "publish_blocked_reason",
    "pages_repo_updated",
    "local_pages_copy_ok",
    "pages_commit_ok",
    "pages_push_ok",
    "remote_tree_verify_ok",
    "live_http_ok",
    "live_archive_ok",
    "pages_branch",
    "pages_commit_sha",
    "pushed",
    "public_urls",
    "local_paths",
    "warnings",
    "errors",
    "manual_push_command",
    "bluesky_status",
    "bluesky_post_uri",
    "bluesky_reason",
    "bluesky_post_text",
    "bluesky_embed_type",
    "bluesky_card_title",
    "bluesky_card_description",
    "bluesky_source_artifact_paths",
    "bluesky_edition_date_verified",
    "bluesky_stale_content_guard_status",
    "bluesky_thumb_status",
    "scheduled_run_local_time",
    "actual_run_local_time",
    "source_window_start_utc",
    "source_window_end_utc",
    "first_source_retrieved_at",
    "last_source_retrieved_at",
    "contains_later_same_day_update",
    "later_same_day_update_count",
    "later_same_day_update_batch_count",
    "later_same_day_update_source_count",
    "contains_post_edition_date_update",
    "post_edition_date_update_count",
    "post_edition_date_update_batch_count",
    "post_edition_date_update_source_count",
    "post_edition_date_retrieval_batches",
    "retrieval_batches",
)
COLLECTION_CONTEXT_NAME = "source_collection_context.json"


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def source_dir_for(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date


def source_file_for(edition_date: str) -> Path:
    return source_dir_for(edition_date) / "manual_sources.json"


def run_manifest_path(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "editions" / edition_date / "run_manifest.json"


def log_path_for(edition_date: str) -> Path:
    return ROOT / "logs" / f"gaza-daily-{edition_date}.log"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_source_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def ensure_source_folder(edition_date: str) -> None:
    source_dir_for(edition_date).mkdir(parents=True, exist_ok=True)


def log_line(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8") if not log_path.exists() else None
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def validate_source_file(path: Path, min_sources: int) -> tuple[list[dict[str, Any]], list[str]]:
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    dict_records: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"source record {index} is not a JSON object")
            continue
        dict_records.append(record)
    errors.extend(valid_source_errors(dict_records))
    errors.extend(error for error in validate_collected_source_records(dict_records, min_sources=0) if error not in errors)
    if len(dict_records) < min_sources:
        errors.append(f"{path.name} contains {len(dict_records)} valid source records; minimum is {min_sources}")
    return dict_records, errors


def manual_source_validation_help(edition_date: str) -> str:
    return f"python scripts/add_gaza_manual_source.py --date {edition_date} --validate-only"


def manual_source_invalid_message(path: Path, reason: str, edition_date: str, *, skipped: bool) -> str:
    suffix = " and was skipped" if skipped else ""
    return (
        f"manual_sources.json at {path} was present but invalid{suffix}: {reason}. "
        f"Run: {manual_source_validation_help(edition_date)}"
    )


def _dedupe_source_rows(rows: list[dict[str, Any]], max_sources: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "").strip().lower()
        title = str(row.get("title") or "").strip().lower()
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= max_sources:
            break
    return out


def _context_path(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "editions" / edition_date / COLLECTION_CONTEXT_NAME


def _write_collection_context(edition_date: str, payload: dict[str, Any]) -> None:
    path = _context_path(edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def collect_or_load_sources(args: argparse.Namespace, summary: dict[str, Any], log_path: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    ensure_source_folder(args.date)
    manual_path = source_file_for(args.date)
    tried_invalid_manual = False
    manual_records: list[dict[str, Any]] = []
    manual_valid = False
    if args.source_mode in {"manual", "both"} and manual_path.exists():
        log_line(log_path, f"Loading manual source file: {manual_path}")
        try:
            records, errors = validate_source_file(manual_path, args.min_sources)
        except Exception as exc:
            if args.source_mode == "manual":
                summary["errors"].append(str(exc))
                return manual_path, []
            tried_invalid_manual = True
            warning = manual_source_invalid_message(manual_path, str(exc), args.date, skipped=True)
            summary["warnings"].append(warning)
            log_line(log_path, warning)
        else:
            if errors:
                if args.source_mode == "manual":
                    summary["errors"].extend(errors)
                    return manual_path, []
                tried_invalid_manual = True
                reason = "; ".join(errors)
                warning = manual_source_invalid_message(manual_path, reason, args.date, skipped=True)
                summary["warnings"].append(warning)
                log_line(log_path, warning)
            else:
                manual_records = records[: args.max_sources]
                manual_valid = True
                if args.source_mode == "manual":
                    summary["source_file"] = str(manual_path)
                    summary["source_count"] = len(manual_records)
                    _write_collection_context(
                        args.date,
                        {
                            "source_mode": "manual",
                            "providers_configured": ["manual_sources_json"],
                            "providers_attempted": ["manual_sources_json"],
                            "providers_successful": ["manual_sources_json"] if manual_records else [],
                            "provider_failures": [] if manual_records else [{"source_id": "manual_sources_json", "reason": "zero_candidates", "status": "no_candidates"}],
                            "provider_diagnostics": [{"source_id": "manual_sources_json", "status": "ok" if manual_records else "no_candidates", "raw_candidates": len(manual_records)}],
                            "stage_counts": {"registry_sources": 1, "enabled_providers_configured": 1, "providers_attempted": 1, "providers_successful": 1 if manual_records else 0, "raw_candidates": len(manual_records)},
                            "raw_candidate_count": len(manual_records),
                            "accepted_candidate_count_before_dedupe": len(manual_records),
                            **build_gaza_collection_timing_metadata(manual_records, args.date),
                        },
                    )
                    return manual_path, manual_records

    if args.source_mode == "manual":
        summary["errors"].append(f"manual source file is required: {manual_path}")
        return manual_path, []

    log_line(log_path, "Running auto source collection.")
    try:
        collected = collect_gaza_sources(
            ROOT,
            args.date,
            max_sources=args.max_sources,
            min_sources=0 if args.source_mode == "both" else args.min_sources,
            output_filename="manual_sources.json",
            prefer_manual=False,
            write_output=args.source_mode != "both",
        )
    except Exception as exc:  # noqa: BLE001
        if args.source_mode == "both" and manual_valid and manual_records:
            summary["warnings"].append(f"auto source collection unavailable; using manual sources only: {exc}")
            summary["source_file"] = str(manual_path)
            summary["source_count"] = len(manual_records)
            _write_collection_context(
                args.date,
                {
                    "source_mode": "both",
                    "providers_configured": ["manual_sources_json"],
                    "providers_attempted": ["manual_sources_json"],
                    "providers_successful": ["manual_sources_json"],
                    "provider_failures": [],
                    "provider_diagnostics": [{"source_id": "manual_sources_json", "status": "ok", "raw_candidates": len(manual_records)}],
                    "stage_counts": {"registry_sources": 1, "enabled_providers_configured": 1, "providers_attempted": 1, "providers_successful": 1, "raw_candidates": len(manual_records)},
                    "raw_candidate_count": len(manual_records),
                    "accepted_candidate_count_before_dedupe": len(manual_records),
                    "enabled_auto_provider_count": 0,
                    **build_gaza_collection_timing_metadata(manual_records, args.date),
                },
            )
            return manual_path, manual_records
        summary["errors"].append(str(exc))
        return None, []
    summary["warnings"].extend(collected.get("warnings", []))
    summary["errors"].extend(collected.get("errors", []))
    summary["failed_source_ids"].extend(collected.get("failed_source_ids", []))
    summary["source_count"] = int(collected.get("source_count") or 0)
    if not collected.get("ok") and args.source_mode == "auto":
        return None, []
    auto_records = list(collected.get("sources") or [])
    records = list(auto_records)
    source_mode_used = "auto"
    if args.source_mode == "both":
        source_mode_used = "both"
        records = _dedupe_source_rows([*auto_records, *manual_records], args.max_sources)
    source_path = Path(str(collected["source_file"])) if collected.get("source_file") else manual_path
    if args.source_mode == "both":
        source_path = manual_path
    summary["source_file"] = str(source_path)
    providers_configured = list(collected.get("providers_configured") or [])
    providers_attempted = list(collected.get("providers_attempted") or [])
    providers_successful = list(collected.get("providers_successful") or [])
    provider_failures = list(collected.get("failed_source_ids") or [])
    provider_diagnostics = list(collected.get("provider_diagnostics") or [])
    if args.source_mode == "both":
        providers_configured = [*providers_configured, "manual_sources_json"]
        providers_attempted = [*providers_attempted, "manual_sources_json"]
        if manual_valid and manual_records:
            providers_successful = sorted(set([*providers_successful, "manual_sources_json"]))
        provider_diagnostics.append(
            {"source_id": "manual_sources_json", "status": "ok" if manual_valid and manual_records else "no_candidates", "raw_candidates": len(manual_records)}
        )
    if len(records) > 0:
        if args.source_mode == "both":
            source_path = write_source_records(ROOT, args.date, records, "manual_sources.json")
        if source_path is None or not source_path.exists():
            raise RuntimeError(f"source collection produced {len(records)} records but failed to persist manual_sources.json")
        persisted_records, persisted_errors = validate_source_file(source_path, args.min_sources)
        if persisted_errors:
            raise RuntimeError(f"source collection persisted invalid manual_sources.json: {'; '.join(persisted_errors)}")
        if len(persisted_records) != len(records):
            raise RuntimeError(
                f"source collection produced {len(records)} records but manual_sources.json contains {len(persisted_records)} records"
            )
        records = persisted_records
    summary["source_count"] = len(records)
    _write_collection_context(
        args.date,
        {
            "source_mode": source_mode_used,
            "providers_configured": providers_configured,
            "providers_attempted": providers_attempted,
            "providers_successful": providers_successful,
            "provider_failures": provider_failures,
            "provider_diagnostics": provider_diagnostics,
            "skipped_providers": list(collected.get("skipped_providers") or []),
            "working_providers": list(collected.get("working_providers") or []),
            "stage_counts": dict(collected.get("stage_counts") or {}),
            "rejected_by_reason": dict(collected.get("rejected_by_reason") or {}),
            "top_rejected_examples": list(collected.get("top_rejected_examples") or []),
            "review_candidates": list(collected.get("review_candidates") or []),
            "raw_candidate_count": int((collected.get("stage_counts") or {}).get("raw_candidates") or 0) + (len(manual_records) if args.source_mode == "both" else 0),
            "accepted_candidate_count_before_dedupe": int((collected.get("stage_counts") or {}).get("accepted_before_rank") or 0) + (len(manual_records) if args.source_mode == "both" else 0),
            "enabled_auto_provider_count": int((collected.get("stage_counts") or {}).get("enabled_providers_configured") or 0),
            **build_gaza_collection_timing_metadata(records, args.date),
        },
    )
    summary["source_file"] = str(source_path) if source_path else None
    summary["source_count"] = len(records)
    log_line(log_path, f"Source collection resolved {len(records)} records to {source_path}")
    return source_path, records


def generation_command(
    edition_date: str,
    allow_thin_edition: bool = False,
    allow_post_edition_date_sources: bool = False,
) -> list[str]:
    cmd = [
        sys.executable,
        "scripts\\run_gaza_dispatch.py",
        "--date",
        edition_date,
        "--historical",
        "--from-manual-sources",
        "--all",
    ]
    if allow_thin_edition:
        cmd.append("--allow-thin-edition")
    if allow_post_edition_date_sources:
        cmd.append("--allow-post-edition-date-sources")
    return cmd


def run_tests(validation_profile: str, pytest_basetemp: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    smtp_env_names = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "EMAIL_TO",
        "EMAIL_FROM",
        "SMTP_FROM",
        "SMTP_USE_SSL",
        "SMTP_SKIP_VERIFY",
        "SMTP_RELAX_X509_STRICT",
        "SMTP_TLS_VERIFY",
        "SMTP_CA_BUNDLE",
        "SMTP_CA_FILE",
        "SMTP_DEBUG_FILE",
    )
    saved = {name: os.environ.pop(name) for name in smtp_env_names if name in os.environ}
    try:
        cmd = pytest_command(validation_profile, pytest_basetemp)
        return run_command(cmd), cmd
    finally:
        os.environ.update(saved)


def push_pages_repo(pages_repo: Path, pages_branch: str) -> tuple[bool, list[str], str]:
    messages: list[str] = []
    status = run_command(["git", "status"], cwd=pages_repo)
    messages.append(status.stdout.strip() or status.stderr.strip())
    if status.returncode != 0:
        return False, messages, status.stderr.strip() or status.stdout.strip()
    push = run_command(["git", "push", "origin", pages_branch], cwd=pages_repo)
    messages.append(push.stdout.strip() or push.stderr.strip())
    if push.returncode != 0:
        detail = push.stdout.strip() or push.stderr.strip() or f"git push origin {pages_branch} failed"
        lower = detail.lower()
        if any(token in lower for token in ("non-fast-forward", "fetch first", "rejected", "update your local branch")):
            detail = (
                f"{detail}\n"
                f"{pages_sync_repair_message(pages_repo, pages_branch)}"
            )
        return False, messages, detail
    return True, messages, push.stdout.strip() or push.stderr.strip()


def verify_remote_pages_tree(pages_repo: Path, pages_branch: str, edition_date: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "fetched": False,
        "remote_commit_sha": None,
        "edition_file_ok": False,
        "archive_ok": False,
        "rss_ok": False,
        "errors": [],
    }
    fetch = run_command(["git", "fetch", "origin", pages_branch], cwd=pages_repo)
    result["fetched"] = fetch.returncode == 0
    if fetch.returncode != 0:
        result["errors"].append(fetch.stderr.strip() or fetch.stdout.strip() or f"git fetch origin {pages_branch} failed")
        return result

    remote_ref = f"origin/{pages_branch}"
    remote_sha = run_command(["git", "rev-parse", remote_ref], cwd=pages_repo)
    if remote_sha.returncode != 0:
        result["errors"].append(remote_sha.stderr.strip() or remote_sha.stdout.strip() or f"could not resolve {remote_ref}")
        return result
    result["remote_commit_sha"] = remote_sha.stdout.strip()

    edition_path = f"gaza/editions/{edition_date}/index.html"
    archive_path = "gaza/archive.html"
    rss_path = "gaza/rss.xml"
    edition_tree = run_command(["git", "ls-tree", "--name-only", remote_ref, "--", edition_path], cwd=pages_repo)
    archive_tree = run_command(["git", "ls-tree", "--name-only", remote_ref, "--", archive_path], cwd=pages_repo)
    rss_tree = run_command(["git", "ls-tree", "--name-only", remote_ref, "--", rss_path], cwd=pages_repo)
    result["edition_file_ok"] = edition_tree.returncode == 0 and edition_path in edition_tree.stdout.split()
    result["archive_ok"] = archive_tree.returncode == 0 and archive_path in archive_tree.stdout.split()
    result["rss_ok"] = rss_tree.returncode == 0 and rss_path in rss_tree.stdout.split()
    if not result["edition_file_ok"]:
        result["errors"].append(f"remote tree is missing {edition_path}")
    if not result["archive_ok"]:
        result["errors"].append(f"remote tree is missing {archive_path}")
    if not result["rss_ok"]:
        result["errors"].append(f"remote tree is missing {rss_path}")
    if result["errors"]:
        return result
    result["ok"] = True
    return result


def verify_live_public_urls(edition_date: str, public_urls: dict[str, str], cache_token: str | None = None) -> dict[str, Any]:
    token = str(cache_token or edition_date).strip() or edition_date
    edition_url = f"{public_urls.get('edition')}?v={token}"
    archive_url = f"{public_urls.get('archive')}?v={token}"
    edition_result: dict[str, Any] = {"ok": False, "status": None, "marker_found": False, "error": None}
    archive_result: dict[str, Any] = {"ok": False, "status": None, "marker_found": False, "error": None}

    def _fetch(url: str) -> tuple[int | None, str, str | None]:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        try:
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                status = getattr(response, "status", 200)
                body = response.read().decode("utf-8", errors="replace")
            return status, body, None
        except Exception as exc:  # noqa: BLE001
            return None, "", str(exc)

    status, body, error = _fetch(edition_url)
    edition_result["status"] = status
    edition_result["error"] = error
    if error is None and status == 200:
        edition_result["marker_found"] = (
            "Dispatches From Gaza" in body
            and edition_date in body
            and (
                "Limited-source update" in body
                or "Today’s Read" in body
                or "Today's Read" in body
                or "Source Mix" in body
            )
        )
        edition_result["ok"] = bool(edition_result["marker_found"])
        if not edition_result["ok"]:
            edition_result["diagnostic_excerpt"] = " ".join(body.split())[:200]

    status, body, error = _fetch(archive_url)
    archive_result["status"] = status
    archive_result["error"] = error
    if error is None and status == 200:
        archive_result["marker_found"] = edition_date in body
        archive_result["ok"] = bool(archive_result["marker_found"])

    return {
        "edition_url": edition_url,
        "archive_url": archive_url,
        "edition": edition_result,
        "archive": archive_result,
        "live_http_ok": edition_result["ok"],
        "live_archive_ok": archive_result["ok"],
        "ok": bool(edition_result["ok"] and archive_result["ok"]),
        "cache_token": token,
    }


def open_local_edition(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        run_command(["open", str(path)])


def public_urls_for(edition_date: str) -> dict[str, str]:
    return {
        "archive": f"{BASE_PUBLIC_URL}/gaza/archive.html",
        "rss": f"{BASE_PUBLIC_URL}/gaza/rss.xml",
        "edition": f"{BASE_PUBLIC_URL}/gaza/editions/{edition_date}/",
    }


def local_paths_for(args: argparse.Namespace) -> dict[str, str]:
    return {
        "source_folder": str(source_dir_for(args.date)),
        "source_file": str(source_file_for(args.date)),
        "edition": str(ROOT / "output" / "site" / "gaza" / "editions" / args.date / "index.html"),
        "archive": str(ROOT / "output" / "site" / "gaza" / "archive.html"),
        "rss": str(ROOT / "output" / "site" / "gaza" / "rss.xml"),
        "pages_repo": str(Path(args.pages_repo)),
        "log": str(log_path_for(args.date)),
        "run_manifest": str(run_manifest_path(args.date)),
    }


def initial_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "date": args.date,
        "mode": "dry-run" if args.dry_run else "publish-local",
        "source_mode": args.source_mode,
        "source_file": str(source_file_for(args.date)),
        "source_count": 0,
        "source_adequacy_status": None,
        "publisher_count": 0,
        "publishers": [],
        "generation_ok": False,
        "generated": False,
        "archive_updated": False,
        "rss_updated": False,
        "tests_run": False,
        "tests_ok": None,
        "validation_ok": None,
        "validation_profile": args.validation_profile,
        "tests_command": None,
        "skipped_unrelated_tests": bool(get_profile(args.validation_profile).skipped_unrelated_tests),
        "pipeline_ok": False,
        "email_requested": bool(args.email_report),
        "email_ok": None,
        "notification_error": None,
        "overall_ok": False,
        "publish_ok": False,
        "publish_blocked": False,
        "publish_blocked_reason": None,
        "pages_repo_updated": False,
        "local_pages_copy_ok": False,
        "pages_commit_ok": False,
        "pages_push_ok": None,
        "remote_tree_verify_ok": None,
        "live_http_ok": None,
        "live_archive_ok": None,
        "pages_branch": args.pages_branch,
        "pages_commit_sha": None,
        "pushed": False,
        "public_urls": public_urls_for(args.date),
        "local_paths": local_paths_for(args),
        "warnings": [],
        "errors": [],
        "failed_source_ids": [],
        "manual_push_command": manual_push_command(Path(args.pages_repo), args.pages_branch),
        "bluesky_status": "skipped",
        "bluesky_post_uri": None,
        "bluesky_reason": "not_attempted",
        "bluesky_post_text": None,
        "bluesky_embed_type": None,
        "bluesky_card_title": None,
        "bluesky_card_description": None,
        "bluesky_source_artifact_paths": [],
        "bluesky_edition_date_verified": False,
        "bluesky_stale_content_guard_status": "not_evaluated",
        "bluesky_thumb_status": "not_attempted",
        "scheduled_run_local_time": None,
        "actual_run_local_time": None,
        "source_window_start_utc": None,
        "source_window_end_utc": None,
        "first_source_retrieved_at": None,
        "last_source_retrieved_at": None,
        "contains_later_same_day_update": False,
        "later_same_day_update_count": 0,
        "later_same_day_update_batch_count": 0,
        "later_same_day_update_source_count": 0,
        "contains_post_edition_date_update": False,
        "post_edition_date_update_count": 0,
        "post_edition_date_update_batch_count": 0,
        "post_edition_date_update_source_count": 0,
        "post_edition_date_retrieval_batches": [],
        "retrieval_batches": [],
        "planned_actions": [],
        "public_story_count": 0,
        "pages_dry_run_ok": False,
    }


def compute_overall_ok(summary: dict[str, Any], *, push_requested: bool, dry_run: bool) -> bool:
    if not summary.get("pipeline_ok"):
        return False
    if summary.get("generation_ok") is not True:
        return False
    if summary.get("validation_ok") is False:
        return False
    if dry_run:
        return bool(summary.get("publish_ok"))
    if not push_requested:
        return bool(summary.get("publish_ok"))
    required_fields = (
        "local_pages_copy_ok",
        "pages_commit_ok",
        "pages_push_ok",
        "remote_tree_verify_ok",
        "live_http_ok",
        "live_archive_ok",
    )
    return all(summary.get(field) is True for field in required_fields)


def write_run_manifest(summary: dict[str, Any]) -> None:
    path = run_manifest_path(summary["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_summary(summary: dict[str, Any]) -> None:
    write_run_manifest(summary)
    print(json.dumps(summary, indent=2))


def tail_log(path: Path, line_count: int = 80) -> str:
    if not path.exists():
        return "<log file not found>"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:]) if lines else "<log file is empty>"


def email_subject(summary: dict[str, Any]) -> str:
    edition_date = str(summary["date"])
    if summary.get("ok") is True:
        return f"[Blue Fern Dispatches] Gaza daily succeeded - {edition_date}"
    source_issue_terms = (
        "source count 0",
        "No valid traceable Gaza sources",
        "manual_sources.json contains",
        "manual source file is required",
        "No valid traceable Gaza sources were collected or loaded",
    )
    has_source_issue = summary.get("source_count", 0) < 1 or any(any(term in str(error) for term in source_issue_terms) for error in summary.get("errors", []))
    if has_source_issue:
        return f"[Blue Fern Dispatches] Gaza daily failed: source issue - {edition_date}"
    return f"[Blue Fern Dispatches] Gaza daily failed - {edition_date}"


def format_lines(values: list[Any]) -> str:
    if not values:
        return "- <none>"
    return "\n".join(f"- {value}" for value in values)


def build_email_body(summary: dict[str, Any], log_path: Path) -> str:
    public_urls = summary.get("public_urls") or {}
    local_paths = summary.get("local_paths") or {}
    lines = [
        f"date: {summary.get('date')}",
        f"ok: {str(summary.get('ok')).lower()}",
        f"source_count: {summary.get('source_count')}",
        f"source_adequacy_status: {summary.get('source_adequacy_status')}",
        f"publisher_count: {summary.get('publisher_count')}",
        f"publishers: {', '.join(summary.get('publishers') or []) if summary.get('publishers') else '<none>'}",
        f"generation_ok: {str(summary.get('generation_ok')).lower()}",
        f"generated: {str(summary.get('generated')).lower()}",
        f"archive_updated: {str(summary.get('archive_updated')).lower()}",
        f"rss_updated: {str(summary.get('rss_updated')).lower()}",
        f"tests_run: {summary.get('tests_run')}",
        f"tests_ok: {summary.get('tests_ok')}",
        f"validation_ok: {summary.get('validation_ok')}",
        f"validation_profile: {summary.get('validation_profile')}",
        f"tests_command: {summary.get('tests_command')}",
        f"skipped_unrelated_tests: {str(summary.get('skipped_unrelated_tests')).lower()}",
        f"pipeline_ok: {str(summary.get('pipeline_ok')).lower()}",
        f"email_requested: {str(summary.get('email_requested')).lower()}",
        f"email_ok: {str(summary.get('email_ok')).lower()}",
        f"notification_error: {summary.get('notification_error')}",
        f"overall_ok: {str(summary.get('overall_ok')).lower()}",
        f"publish_ok: {str(summary.get('publish_ok')).lower()}",
        f"publish_blocked: {str(summary.get('publish_blocked')).lower()}",
        f"publish_blocked_reason: {summary.get('publish_blocked_reason')}",
        f"pages_repo_updated: {summary.get('pages_repo_updated')}",
        f"local_pages_copy_ok: {summary.get('local_pages_copy_ok')}",
        f"pages_commit_ok: {summary.get('pages_commit_ok')}",
        f"pages_push_ok: {summary.get('pages_push_ok')}",
        f"remote_tree_verify_ok: {summary.get('remote_tree_verify_ok')}",
        f"live_http_ok: {summary.get('live_http_ok')}",
        f"live_archive_ok: {summary.get('live_archive_ok')}",
        f"pages_branch: {summary.get('pages_branch')}",
        f"pages_commit_sha: {summary.get('pages_commit_sha')}",
        f"pushed: {str(summary.get('pushed')).lower()}",
        f"public archive URL: {public_urls.get('archive')}",
        f"public edition URL: {public_urls.get('edition')}",
        f"local edition path: {local_paths.get('edition')}",
        f"log path: {local_paths.get('log')}",
        f"run manifest path: {local_paths.get('run_manifest')}",
        f"bluesky_status: {summary.get('bluesky_status')}",
        f"bluesky_post_uri: {summary.get('bluesky_post_uri')}",
        f"bluesky_reason: {summary.get('bluesky_reason')}",
        f"bluesky_post_text: {summary.get('bluesky_post_text')}",
        f"bluesky_embed_type: {summary.get('bluesky_embed_type')}",
        f"bluesky_card_title: {summary.get('bluesky_card_title')}",
        f"bluesky_card_description: {summary.get('bluesky_card_description')}",
        f"bluesky_source_artifact_paths: {summary.get('bluesky_source_artifact_paths')}",
        f"bluesky_edition_date_verified: {summary.get('bluesky_edition_date_verified')}",
        f"bluesky_stale_content_guard_status: {summary.get('bluesky_stale_content_guard_status')}",
        f"bluesky_thumb_status: {summary.get('bluesky_thumb_status')}",
        "",
        "warnings:",
        format_lines(list(summary.get("warnings") or [])),
        "",
        "errors:",
        format_lines(list(summary.get("errors") or [])),
    ]
    if not summary.get("pushed"):
        lines.extend(["", "manual push command:", str(summary.get("manual_push_command") or "<none>")])
    failed_source_ids = summary.get("failed_source_ids") or []
    if failed_source_ids:
        lines.extend(["", "failed source IDs:", format_lines(f"{item.get('source_id')}: {item.get('reason')}" for item in failed_source_ids)])
    lines.extend(["", "last 80 log lines:", tail_log(log_path, 80)])
    return "\n".join(lines)


def send_email_report(summary: dict[str, Any], log_path: Path, smtp_debug: bool = False) -> None:
    if smtp_debug:
        send_email(email_subject(summary), build_email_body(summary, log_path), str(summary["date"]), smtp_debug=True)
    else:
        send_email(email_subject(summary), build_email_body(summary, log_path), str(summary["date"]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the one-command daily Gaza workflow.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Edition date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate, but do not update, commit, or push the Pages repo.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest validation.")
    parser.add_argument("--push", action="store_true", help="Push the Pages repo to origin after local publish succeeds.")
    parser.add_argument("--email-report", action="store_true", help="Email a plain-text run report after success or failure.")
    parser.add_argument("--smtp-debug", action="store_true", help="Enable smtplib debug output when --email-report sends mail.")
    parser.add_argument("--open-local", action="store_true", help="Open the rendered local edition after success.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repo path.")
    parser.add_argument("--remote-url", default=DEFAULT_REMOTE_URL, help="Pages repo remote URL.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="GitHub Pages deploy branch.")
    parser.add_argument("--max-sources", type=int, default=12, help="Maximum collected source records.")
    parser.add_argument("--min-sources", type=int, default=1, help="Minimum valid source records required.")
    parser.add_argument("--source-mode", choices=sorted(SOURCE_MODES), default="both", help="Source collection mode.")
    parser.add_argument("--allow-thin-edition", action="store_true", help="Allow publish when only thin Gaza coverage survives relevance gates.")
    parser.add_argument(
        "--allow-post-edition-date-sources",
        action="store_true",
        help="Allow Gaza reruns/backfills to use sources retrieved after the local edition date.",
    )
    parser.add_argument("--post-bluesky", action="store_true", help="Post a Gaza dispatch announcement to Bluesky after successful publish.")
    parser.add_argument("--no-post-bluesky", action="store_true", help="Disable Bluesky posting for this run.")
    parser.add_argument("--force-bluesky-post", action="store_true", help="Post to Bluesky even when a successful receipt already exists for this edition.")
    parser.add_argument("--generate-audio", action="store_true", help="Generate Gaza audio artifacts after dispatch generation.")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default="none", help="Optional TTS provider when --generate-audio is used.")
    parser.add_argument("--audio-voice", default="alloy", help="TTS voice for --generate-audio.")
    parser.add_argument("--audio-voices", default="", help="Comma-separated voices for alternating mode (example: alloy,verse).")
    parser.add_argument("--audio-alternate-voices", action="store_true", help="Alternate voices across segmented Gaza audio stories.")
    parser.add_argument("--audio-segue-chime", choices=("none", "gentle"), default="none", help="Optional segue chime between segmented stories.")
    parser.add_argument("--audio-model", default="gpt-4o-mini-tts", help="TTS model for --generate-audio.")
    parser.add_argument("--audio-format", choices=("mp3", "wav"), default="mp3", help="Audio format for generated speech.")
    parser.add_argument("--tts-price-per-1m-chars", type=float, default=None, help="Optional pricing basis used for estimated TTS cost logging.")
    parser.add_argument(
        "--validation-profile",
        choices=list(profile_names()),
        default=PROFILE_GAZA_DAILY,
        help="Validation profile for scheduled/run gating.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.validation_profile = apply_env_profile(args.validation_profile)
    try:
        _ = get_profile(args.validation_profile)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)], "validation_profile": args.validation_profile}, indent=2))
        return 1
    if args.post_bluesky and args.no_post_bluesky:
        print(json.dumps({"ok": False, "errors": ["--post-bluesky and --no-post-bluesky cannot be used together"]}, indent=2))
        return 1
    args.date = validate_date(args.date)
    args.pages_repo = str(Path(args.pages_repo))
    pages_repo = Path(args.pages_repo)
    log_path = log_path_for(args.date)
    summary = initial_summary(args)
    log_line(log_path, f"Starting daily Gaza run for {args.date} source_mode={args.source_mode} dry_run={args.dry_run}")

    def finish(pipeline_code: int) -> int:
        summary["pipeline_ok"] = pipeline_code == 0
        summary["overall_ok"] = compute_overall_ok(summary, push_requested=bool(args.push), dry_run=bool(args.dry_run))
        if summary.get("email_requested"):
            summary["email_ok"] = False
        if not args.email_report:
            summary["email_ok"] = None
            summary["ok"] = summary["overall_ok"]
            write_summary(summary)
            return pipeline_code
        write_run_manifest(summary)
        log_line(log_path, "Email notification attempted.")
        try:
            send_email_report(summary, log_path, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            message = notification_error_message(exc)
            summary["email_ok"] = False
            summary["notification_error"] = message
            if summary["overall_ok"] and not args.dry_run:
                summary["warnings"].append(f"Email report failed: {message}")
                summary["ok"] = True
                write_summary(summary)
                return 0
            summary["overall_ok"] = False
            summary["errors"].append(f"Email report failed: {message}")
            log_line(log_path, f"Email report failed: {message}")
            summary["ok"] = summary["overall_ok"]
            write_summary(summary)
            return 2
        summary["email_report_sent"] = True
        summary["email_ok"] = True
        summary["overall_ok"] = compute_overall_ok(summary, push_requested=bool(args.push), dry_run=bool(args.dry_run))
        summary["ok"] = summary["overall_ok"]
        log_line(log_path, "Email notification succeeded.")
        write_summary(summary)
        return pipeline_code

    try:
        source_path, records = collect_or_load_sources(args, summary, log_path)
    except Exception as exc:  # noqa: BLE001
        message = str(exc) or exc.__class__.__name__
        summary["errors"].append(message)
        log_line(log_path, f"Source collection failed: {message}")
        return finish(1)
    summary.update(build_gaza_collection_timing_metadata(records, args.date))
    if summary["errors"]:
        log_line(log_path, f"Source validation failed: {summary['errors']}")
        return finish(1)
    if source_path is None or not records:
        summary["errors"].append("No valid traceable Gaza sources were collected or loaded; refusing to publish.")
        log_line(log_path, "No valid source records; stopping before generation.")
        return finish(1)

    summary["planned_actions"] = [
        command_text(
            generation_command(
                args.date,
                allow_thin_edition=bool(args.allow_thin_edition),
                allow_post_edition_date_sources=bool(args.allow_post_edition_date_sources),
            )
        ),
        command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=True)),
    ]
    pytest_basetemp = make_pytest_basetemp("bluefern-pytest-gaza")
    if not args.skip_tests:
        summary["planned_actions"].append(command_text(pytest_command(args.validation_profile, pytest_basetemp)))
    if not args.dry_run:
        summary["planned_actions"].append(command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=False)))
    if args.push:
        summary["planned_actions"].extend(["git status", f"git push origin {args.pages_branch}"])

    log_line(
        log_path,
        f"Pipeline command started: {command_text(generation_command(args.date, allow_thin_edition=bool(args.allow_thin_edition), allow_post_edition_date_sources=bool(args.allow_post_edition_date_sources)))}",
    )
    generation = run_command(
        generation_command(
            args.date,
            allow_thin_edition=bool(args.allow_thin_edition),
            allow_post_edition_date_sources=bool(args.allow_post_edition_date_sources),
        )
    )
    log_line(log_path, f"Generation return code: {generation.returncode}")
    generation_payload: dict[str, Any] = {}
    try:
        generation_payload = parse_json_stdout(generation)
    except Exception:
        generation_payload = {}
    if generation.returncode != 0:
        summary["errors"].append(generation.stderr.strip() or generation.stdout.strip() or "Gaza generation failed")
        return finish(1)
    for warning in generation_payload.get("warnings") or []:
        text = str(warning).strip()
        if text and text not in summary["warnings"]:
            summary["warnings"].append(text)
    summary["generated"] = True
    summary["generation_ok"] = True
    summary["source_adequacy_status"] = generation_payload.get("source_adequacy_status")
    summary["publisher_count"] = int(generation_payload.get("publisher_count") or 0)
    summary["publishers"] = list(generation_payload.get("publishers") or [])
    for warning in generation_payload.get("source_adequacy_warnings") or []:
        text = str(warning).strip()
        if text and text not in summary["warnings"]:
            summary["warnings"].append(text)

    generated_validation = validate_generated_output(args.date)
    summary.update(
        {
            "archive_updated": generated_validation["archive_updated"],
            "rss_updated": generated_validation["rss_updated"],
            "source_count": generated_validation["source_count"],
            "public_story_count": generated_validation["public_story_count"],
        }
    )
    summary["warnings"].extend(generated_validation["warnings"])
    summary["errors"].extend(generated_validation["errors"])
    summary["errors"].extend(validate_public_site_has_no_detail_files())
    log_line(log_path, f"Validation source_count={summary['source_count']} errors={summary['errors']}")
    if summary["errors"]:
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "post-generation-validation-errors"
        return finish(1)

    if args.generate_audio:
        try:
            from bluefern_dispatches.gaza_audio import write_gaza_audio_outputs

            audio_result = write_gaza_audio_outputs(
                ROOT,
                args.date,
                dry_run=bool(args.dry_run),
                tts_provider=str(args.tts_provider or "none"),
                tts_model=str(args.audio_model or "gpt-4o-mini-tts"),
                tts_voice=str(args.audio_voice or "alloy"),
                audio_format=str(args.audio_format or "mp3"),
                alternate_voices=bool(args.audio_alternate_voices),
                voices=str(args.audio_voices or ""),
                segue_chime=str(args.audio_segue_chime or "none"),
                tts_price_per_1m_chars=args.tts_price_per_1m_chars,
            )
            if not args.dry_run:
                summary["warnings"].append(f"audio artifacts updated: {audio_result.transcript_path}")
            if str(args.tts_provider or "none") != "none" and str(audio_result.audio_status) != "audio_file_ready":
                summary["errors"].append(f"audio generation failed: {audio_result.audio_status}")
                summary["publish_blocked"] = True
                summary["publish_blocked_reason"] = "audio-generation-failed"
                return finish(1)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"audio generation failed: {exc}")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "audio-generation-failed"
            return finish(1)
    else:
        summary["warnings"].append(
            f"audio not generated in daily run; follow-up: python scripts/run_gaza_audio.py --date {args.date} --tts-provider none"
        )

    if not args.skip_tests:
        summary["tests_run"] = True
        tests, tests_cmd = run_tests(args.validation_profile, pytest_basetemp)
        summary["tests_command"] = subprocess.list2cmdline(tests_cmd)
        summary["tests_ok"] = tests.returncode == 0
        summary["validation_ok"] = summary["tests_ok"]
        log_line(log_path, f"Tests return code: {tests.returncode}")
        if tests.returncode != 0:
            summary["errors"].append(tests.stdout.strip() or tests.stderr.strip() or "tests failed")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "dispatch_validation_failed"
            return finish(1)
    else:
        summary["warnings"].append("tests skipped by --skip-tests")
        summary["validation_ok"] = True

    pages_dry_run = run_command(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=True))
    log_line(log_path, f"Pages dry-run return code: {pages_dry_run.returncode}")
    if pages_dry_run.returncode != 0:
        summary["errors"].append(pages_dry_run.stderr.strip() or pages_dry_run.stdout.strip() or "Pages publish dry-run failed")
        return finish(1)
    try:
        pages_dry_run_payload = parse_json_stdout(pages_dry_run)
    except Exception as exc:
        summary["errors"].append(f"could not parse Pages dry-run JSON: {exc}")
        return finish(1)
    if pages_dry_run_payload.get("ok") is not True:
        summary["errors"].append("Pages publish dry-run did not report ok: true")
    if pages_dry_run_payload.get("target_pages_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages dry-run targeted {pages_dry_run_payload.get('target_pages_branch')}, expected {args.pages_branch}")
    if pages_dry_run_payload.get("errors") not in ([], None):
        summary["errors"].append(f"Pages publish dry-run reported errors: {pages_dry_run_payload.get('errors')}")
    if pages_dry_run_payload.get("paid_detail_excluded_from_public") is not True:
        summary["errors"].append("Pages dry-run did not confirm paid/detail exclusion")
    summary["pages_dry_run_ok"] = not summary["errors"]
    if summary["errors"]:
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-dry-run-safety-failed"
        return finish(1)

    if args.dry_run:
        if bool(args.post_bluesky and not args.no_post_bluesky):
            dry_run_bluesky = maybe_post_gaza_dispatch_to_bluesky(
                edition_date=args.date,
                public_url=(summary.get("public_urls") or {}).get("edition"),
                run_succeeded=True,
                post_requested=True,
                project_root=ROOT,
                force_post=bool(args.force_bluesky_post),
                allow_publish=False,
            )
            summary["bluesky_status"] = str(dry_run_bluesky.get("status") or "skipped")
            summary["bluesky_post_uri"] = dry_run_bluesky.get("post_uri")
            summary["bluesky_reason"] = dry_run_bluesky.get("reason")
            summary["bluesky_post_text"] = dry_run_bluesky.get("post_text")
            summary["bluesky_embed_type"] = dry_run_bluesky.get("embed_type")
            summary["bluesky_card_title"] = dry_run_bluesky.get("card_title")
            summary["bluesky_card_description"] = dry_run_bluesky.get("card_description")
            summary["bluesky_source_artifact_paths"] = list(dry_run_bluesky.get("source_artifact_paths") or [])
            summary["bluesky_edition_date_verified"] = bool(dry_run_bluesky.get("edition_date_verified"))
            summary["bluesky_stale_content_guard_status"] = dry_run_bluesky.get("stale_content_guard_status")
            summary["bluesky_thumb_status"] = dry_run_bluesky.get("thumb_status") or "not_attempted"
        summary["ok"] = True
        summary["publish_ok"] = True
        log_line(log_path, "Dry run complete; Pages repo was not updated.")
        return finish(0)

    pages_publish = run_command(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=False))
    log_line(log_path, f"Pages publish return code: {pages_publish.returncode}")
    if pages_publish.returncode != 0:
        try:
            pages_failure_payload = parse_json_stdout(pages_publish)
        except Exception:
            summary["errors"].append(pages_publish.stderr.strip() or pages_publish.stdout.strip() or "Pages publish failed")
        else:
            for warning in pages_failure_payload.get("warnings") or []:
                summary["warnings"].append(f"Pages publish warning: {warning}")
            payload_errors = pages_failure_payload.get("errors") or []
            if payload_errors:
                summary["errors"].extend(f"Pages publish failed: {error}" for error in payload_errors)
            else:
                summary["errors"].append("Pages publish failed")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-publish-failed"
        return finish(1)
    try:
        pages_payload = parse_json_stdout(pages_publish)
    except Exception as exc:
        summary["errors"].append(f"could not parse Pages publish JSON: {exc}")
        return finish(1)
    if pages_payload.get("ok") is not True:
        summary["errors"].append("Pages publish did not report ok: true")
    if pages_payload.get("target_pages_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages publish targeted {pages_payload.get('target_pages_branch')}, expected {args.pages_branch}")
    if pages_payload.get("committed_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages commit targeted {pages_payload.get('committed_branch')}, expected {args.pages_branch}")
    summary["pages_repo_updated"] = bool(pages_payload.get("copied"))
    summary["local_pages_copy_ok"] = bool(pages_payload.get("copied"))
    pages_commit_message = str(pages_payload.get("message") or "").strip().lower()
    summary["pages_commit_ok"] = bool(pages_payload.get("committed")) or pages_commit_message in {"no changes to commit", "dry run; no commit created"}
    summary["pages_push_ok"] = None
    summary["remote_tree_verify_ok"] = None
    summary["live_http_ok"] = None
    summary["live_archive_ok"] = None
    summary["publish_ok"] = True
    summary["pages_commit_sha"] = pages_payload.get("commit_sha")
    summary["pages_branch"] = pages_payload.get("target_pages_branch", args.pages_branch)
    summary["errors"].extend(validate_pages_outputs(pages_repo, args.date))
    if summary["errors"]:
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-output-validation-failed"
        return finish(1)

    if args.push:
        pushed, messages, push_result = push_pages_repo(pages_repo, args.pages_branch)
        summary["push_output"] = messages
        summary["pushed"] = pushed
        summary["pages_push_ok"] = pushed
        log_line(log_path, f"Push attempted result={pushed}")
        if not pushed:
            log_line(log_path, push_result)
            summary["errors"].append(push_result or f"git push origin {args.pages_branch} failed")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "pages-push-failed"
            return finish(1)
        remote_verify = verify_remote_pages_tree(pages_repo, args.pages_branch, args.date)
        summary["remote_tree_verify_ok"] = bool(remote_verify.get("ok"))
        summary["errors"].extend(remote_verify.get("errors") or [])
        log_line(log_path, f"Remote tree verification ok={summary['remote_tree_verify_ok']} remote_commit={remote_verify.get('remote_commit_sha')}")
        if not remote_verify.get("ok"):
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "pages-remote-tree-verify-failed"
            return finish(1)
        live_verify = verify_live_public_urls(args.date, summary.get("public_urls") or {})
        summary["live_http_ok"] = bool(live_verify.get("live_http_ok"))
        summary["live_archive_ok"] = bool(live_verify.get("live_archive_ok"))
        log_line(log_path, f"Live verification ok={live_verify.get('ok')} edition_ok={summary['live_http_ok']} archive_ok={summary['live_archive_ok']}")
        if not live_verify.get("ok"):
            summary["errors"].append(
                f"live verification failed: edition={live_verify['edition_url']} archive={live_verify['archive_url']}"
            )
            if not live_verify.get("live_http_ok"):
                summary["errors"].append(f"live edition verification failed: {live_verify['edition']}")
            if not live_verify.get("live_archive_ok"):
                summary["errors"].append(f"live archive verification failed: {live_verify['archive']}")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "pages-live-verification-failed"
            return finish(1)

    bluesky_result = maybe_post_gaza_dispatch_to_bluesky(
        edition_date=args.date,
        public_url=(summary.get("public_urls") or {}).get("edition"),
        run_succeeded=bool(summary.get("generation_ok") and summary.get("publish_ok") and summary.get("pages_repo_updated")),
        post_requested=bool(args.post_bluesky and not args.no_post_bluesky),
        project_root=ROOT,
        force_post=bool(args.force_bluesky_post),
        allow_publish=not bool(args.dry_run),
    )
    summary["bluesky_status"] = str(bluesky_result.get("status") or "skipped")
    summary["bluesky_post_uri"] = bluesky_result.get("post_uri")
    summary["bluesky_reason"] = bluesky_result.get("reason")
    summary["bluesky_post_text"] = bluesky_result.get("post_text")
    summary["bluesky_embed_type"] = bluesky_result.get("embed_type")
    summary["bluesky_card_title"] = bluesky_result.get("card_title")
    summary["bluesky_card_description"] = bluesky_result.get("card_description")
    summary["bluesky_source_artifact_paths"] = list(bluesky_result.get("source_artifact_paths") or [])
    summary["bluesky_edition_date_verified"] = bool(bluesky_result.get("edition_date_verified"))
    summary["bluesky_stale_content_guard_status"] = bluesky_result.get("stale_content_guard_status")
    summary["bluesky_thumb_status"] = bluesky_result.get("thumb_status") or "not_attempted"
    summary["bluesky_requested_date"] = bluesky_result.get("requested_date")
    summary["bluesky_manifest_edition_date"] = bluesky_result.get("manifest_edition_date")
    summary["bluesky_public_url"] = bluesky_result.get("public_url")
    summary["bluesky_canonical_url"] = bluesky_result.get("canonical_url")
    summary["bluesky_page_title"] = bluesky_result.get("page_title")
    summary["bluesky_page_heading"] = bluesky_result.get("page_heading")
    summary["bluesky_mismatched_field"] = bluesky_result.get("mismatched_field")
    summary["bluesky_date_issues"] = list(bluesky_result.get("date_issues") or [])
    if summary["bluesky_status"] == "failure":
        summary["warnings"].append(f"Bluesky post failed: {summary['bluesky_reason']}")
    elif summary["bluesky_status"] == "success":
        log_line(log_path, f"Bluesky post succeeded: {summary['bluesky_post_uri']}")

    if args.open_local:
        open_local_edition(ROOT / "output" / "site" / "gaza" / "editions" / args.date / "index.html")

    missing_summary_fields = [field for field in REQUIRED_PUBLIC_SUMMARY_FIELDS if field not in summary]
    if missing_summary_fields:
        summary["errors"].append(f"summary missing required fields: {', '.join(missing_summary_fields)}")
        return finish(1)
    summary["ok"] = True
    log_line(log_path, f"Pipeline finished with exit code 0. pages_repo_updated={summary['pages_repo_updated']} pushed={summary['pushed']}")
    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
