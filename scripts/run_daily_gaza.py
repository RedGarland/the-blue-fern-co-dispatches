from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_sources import collect_gaza_sources
from bluefern_dispatches.gaza_sources import validate_source_records as validate_collected_source_records
from scripts.run_and_notify import _smtp_error_message, send_email
from scripts.publish_gaza_historical import (
    BASE_PUBLIC_URL,
    DEFAULT_PAGES_BRANCH,
    DEFAULT_REMOTE_URL,
    LinkTextParser,
    command_text,
    manual_push_command,
    parse_json_stdout,
    pages_publish_command,
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
    "generated",
    "archive_updated",
    "rss_updated",
    "tests_run",
    "tests_ok",
    "validation_profile",
    "tests_command",
    "skipped_unrelated_tests",
    "publish_blocked",
    "publish_blocked_reason",
    "pages_repo_updated",
    "pages_branch",
    "pages_commit_sha",
    "pushed",
    "public_urls",
    "local_paths",
    "warnings",
    "errors",
    "manual_push_command",
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
    return json.loads(path.read_text(encoding="utf-8"))


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
    records = load_source_records(path)
    errors = valid_source_errors(records)
    errors.extend(error for error in validate_collected_source_records(records, min_sources=0) if error not in errors)
    if len(records) < min_sources:
        errors.append(f"{path.name} contains {len(records)} valid source records; minimum is {min_sources}")
    return records, errors


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
            summary["warnings"].append(f"manual_sources.json was present but invalid: {exc}")
            log_line(log_path, f"Manual source file invalid; falling back to auto collection: {exc}")
        else:
            if errors:
                if args.source_mode == "manual":
                    summary["errors"].extend(errors)
                    return manual_path, []
                tried_invalid_manual = True
                summary["warnings"].append(f"manual_sources.json was present but invalid: {'; '.join(errors)}")
                log_line(log_path, f"Manual source file invalid; falling back to auto collection: {errors}")
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
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    source_path = Path(str(collected["source_file"])) if collected.get("source_file") else manual_path
    if args.source_mode == "both":
        source_path = manual_path
    summary["source_file"] = str(source_path)
    summary["source_count"] = len(records)
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
        },
    )
    log_line(log_path, f"Source collection resolved {len(records)} records to {source_path}")
    return source_path, records


def generation_command(edition_date: str) -> list[str]:
    return [
        sys.executable,
        "scripts\\run_gaza_dispatch.py",
        "--date",
        edition_date,
        "--historical",
        "--from-manual-sources",
        "--all",
    ]


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
    return push.returncode == 0, messages, push.stdout.strip() or push.stderr.strip()


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
        "generated": False,
        "archive_updated": False,
        "rss_updated": False,
        "tests_run": False,
        "tests_ok": None,
        "validation_profile": args.validation_profile,
        "tests_command": None,
        "skipped_unrelated_tests": bool(get_profile(args.validation_profile).skipped_unrelated_tests),
        "publish_blocked": False,
        "publish_blocked_reason": None,
        "pages_repo_updated": False,
        "pages_branch": args.pages_branch,
        "pages_commit_sha": None,
        "pushed": False,
        "public_urls": public_urls_for(args.date),
        "local_paths": local_paths_for(args),
        "warnings": [],
        "errors": [],
        "failed_source_ids": [],
        "manual_push_command": manual_push_command(Path(args.pages_repo), args.pages_branch),
        "planned_actions": [],
        "public_story_count": 0,
        "pages_dry_run_ok": False,
    }


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
        f"generated: {str(summary.get('generated')).lower()}",
        f"archive_updated: {str(summary.get('archive_updated')).lower()}",
        f"rss_updated: {str(summary.get('rss_updated')).lower()}",
        f"tests_run: {summary.get('tests_run')}",
        f"tests_ok: {summary.get('tests_ok')}",
        f"validation_profile: {summary.get('validation_profile')}",
        f"tests_command: {summary.get('tests_command')}",
        f"skipped_unrelated_tests: {str(summary.get('skipped_unrelated_tests')).lower()}",
        f"publish_blocked: {str(summary.get('publish_blocked')).lower()}",
        f"publish_blocked_reason: {summary.get('publish_blocked_reason')}",
        f"pages_repo_updated: {summary.get('pages_repo_updated')}",
        f"pages_branch: {summary.get('pages_branch')}",
        f"pages_commit_sha: {summary.get('pages_commit_sha')}",
        f"pushed: {str(summary.get('pushed')).lower()}",
        f"public archive URL: {public_urls.get('archive')}",
        f"public edition URL: {public_urls.get('edition')}",
        f"local edition path: {local_paths.get('edition')}",
        f"log path: {local_paths.get('log')}",
        f"run manifest path: {local_paths.get('run_manifest')}",
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
    args.date = validate_date(args.date)
    args.pages_repo = str(Path(args.pages_repo))
    pages_repo = Path(args.pages_repo)
    log_path = log_path_for(args.date)
    summary = initial_summary(args)
    log_line(log_path, f"Starting daily Gaza run for {args.date} source_mode={args.source_mode} dry_run={args.dry_run}")

    def finish(pipeline_code: int) -> int:
        if not args.email_report:
            write_summary(summary)
            return pipeline_code
        write_run_manifest(summary)
        log_line(log_path, "Email notification attempted.")
        try:
            send_email_report(summary, log_path, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            message = f"Email report failed: {_smtp_error_message(exc)}"
            summary["errors"].append(message)
            log_line(log_path, message)
            write_summary(summary)
            return 2
        summary["email_report_sent"] = True
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
    if summary["errors"]:
        log_line(log_path, f"Source validation failed: {summary['errors']}")
        return finish(1)
    if source_path is None or not records:
        summary["errors"].append("No valid traceable Gaza sources were collected or loaded; refusing to publish.")
        log_line(log_path, "No valid source records; stopping before generation.")
        return finish(1)

    summary["planned_actions"] = [
        command_text(generation_command(args.date)),
        command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=True)),
    ]
    pytest_basetemp = make_pytest_basetemp("bluefern-pytest-gaza")
    if not args.skip_tests:
        summary["planned_actions"].append(command_text(pytest_command(args.validation_profile, pytest_basetemp)))
    if not args.dry_run:
        summary["planned_actions"].append(command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=False)))
    if args.push:
        summary["planned_actions"].extend(["git status", f"git push origin {args.pages_branch}"])

    log_line(log_path, f"Pipeline command started: {command_text(generation_command(args.date))}")
    generation = run_command(generation_command(args.date))
    log_line(log_path, f"Generation return code: {generation.returncode}")
    if generation.returncode != 0:
        summary["errors"].append(generation.stderr.strip() or generation.stdout.strip() or "Gaza generation failed")
        return finish(1)
    summary["generated"] = True

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

    if not args.skip_tests:
        summary["tests_run"] = True
        tests, tests_cmd = run_tests(args.validation_profile, pytest_basetemp)
        summary["tests_command"] = subprocess.list2cmdline(tests_cmd)
        summary["tests_ok"] = tests.returncode == 0
        log_line(log_path, f"Tests return code: {tests.returncode}")
        if tests.returncode != 0:
            summary["errors"].append(tests.stdout.strip() or tests.stderr.strip() or "tests failed")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "dispatch_validation_failed"
            return finish(1)
    else:
        summary["warnings"].append("tests skipped by --skip-tests")

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
        summary["ok"] = True
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
        log_line(log_path, f"Push attempted result={pushed}")
        if not pushed:
            summary["errors"].append(push_result or f"git push origin {args.pages_branch} failed")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "pages-push-failed"
            return finish(1)

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
