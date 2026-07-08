from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.publish_gaza_historical import command_text, pages_publish_command, parse_json_stdout
from scripts.run_daily_gaza import DEFAULT_PAGES_BRANCH, DEFAULT_PAGES_REPO, DEFAULT_REMOTE_URL, validate_source_file


DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
REQUIRED_GAZA_ASSETS = (
    ROOT / "assets" / "site.css",
    ROOT / "assets" / "gaza-logo.png",
)


def validate_date(value: str) -> str:
    if not re.match(DATE_RE, value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def _run_command(args: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_output(repo: Path, *args: str) -> str:
    result = _run_command(["git", *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_status_lines(repo: Path) -> list[str]:
    result = _run_command(["git", "status", "--short"], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repo_clean_status(repo: Path) -> dict[str, Any]:
    lines = _git_status_lines(repo)
    return {
        "clean": not lines,
        "dirty_paths": [line[3:].strip() for line in lines],
        "status_lines": lines,
    }


def _pages_repo_sync_status(pages_repo: Path, pages_branch: str) -> dict[str, Any]:
    status = _repo_clean_status(pages_repo)
    branch = ""
    upstream = ""
    ahead = behind = None
    head_sha = ""
    upstream_sha = ""
    try:
        branch = _git_output(pages_repo, "branch", "--show-current") or "<detached>"
    except Exception as exc:  # noqa: BLE001
        status["error"] = str(exc)
    try:
        upstream = _git_output(pages_repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        counts = _git_output(pages_repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        left_right = counts.split()
        if len(left_right) == 2:
            behind = int(left_right[0])
            ahead = int(left_right[1])
        head_sha = _git_output(pages_repo, "rev-parse", "--short", "HEAD")
        upstream_sha = _git_output(pages_repo, "rev-parse", "--short", upstream)
    except Exception as exc:  # noqa: BLE001
        status["error"] = status.get("error") or str(exc)
    clean = bool(status.get("clean"))
    synced = clean and branch == pages_branch and upstream == f"origin/{pages_branch}" and ahead == 0 and behind == 0
    return {
        "clean": clean,
        "synced": synced,
        "branch": branch,
        "expected_branch": pages_branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "head_sha": head_sha,
        "upstream_sha": upstream_sha,
        "dirty_paths": list(status.get("dirty_paths") or []),
        "status_lines": list(status.get("status_lines") or []),
        "error": status.get("error"),
    }


def _manual_sources_status(edition_date: str) -> dict[str, Any]:
    path = ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    if not path.exists():
        return {"ok": True, "status": "not_present", "path": str(path), "record_count": 0, "errors": []}
    try:
        records, errors = validate_source_file(path, 0)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "invalid", "path": str(path), "record_count": 0, "errors": [str(exc)]}
    status = "valid" if not errors else "invalid"
    return {
        "ok": not errors,
        "status": status,
        "path": str(path),
        "record_count": len(records),
        "errors": errors,
    }


def _asset_status() -> dict[str, Any]:
    missing = [str(path) for path in REQUIRED_GAZA_ASSETS if not path.exists()]
    return {"ok": not missing, "missing": missing}


def _credentials_status(*, generate_audio: bool, tts_provider: str) -> dict[str, Any]:
    required = ["SMTP_HOST", "EMAIL_TO"]
    present = [name for name in required if str(os.getenv(name, "")).strip()]
    missing = [name for name in required if name not in present]
    notes: list[str] = []

    smtp_user = str(os.getenv("SMTP_USER", "")).strip() or str(os.getenv("SMTP_USERNAME", "")).strip()
    if smtp_user:
        required.append("SMTP_PASSWORD")
        if str(os.getenv("SMTP_PASSWORD", "")).strip():
            present.append("SMTP_PASSWORD")
        else:
            missing.append("SMTP_PASSWORD")
            notes.append("SMTP_PASSWORD is required when SMTP_USER or SMTP_USERNAME is set.")

    if generate_audio and tts_provider == "openai":
        required.append("OPENAI_API_KEY")
        if str(os.getenv("OPENAI_API_KEY", "")).strip():
            present.append("OPENAI_API_KEY")
        else:
            missing.append("OPENAI_API_KEY")
            notes.append("OPENAI_API_KEY is required when --generate-audio uses the openai provider.")

    return {
        "ok": not missing,
        "required": list(dict.fromkeys(required)),
        "present": list(dict.fromkeys(present)),
        "missing": list(dict.fromkeys(missing)),
        "notes": notes,
    }


def _cleanup_generated_artifacts(edition_date: str) -> None:
    restore_paths = [
        "data/records",
        "output/site",
        "output/dispatches",
        "data/dispatches/gaza/editions",
        "data/dispatches/gaza/sources",
    ]
    restore = _run_command(
        ["git", "restore", "--source=HEAD", "--staged", "--worktree", "--", *restore_paths],
        cwd=ROOT,
    )
    if restore.returncode != 0:
        raise RuntimeError(restore.stderr.strip() or restore.stdout.strip() or "git restore cleanup failed")
    log_path = ROOT / "logs" / f"gaza-daily-{edition_date}.log"
    if log_path.exists():
        try:
            log_path.unlink()
        except Exception:  # noqa: BLE001
            pass
    clean = _run_command(
        [
            "git",
            "clean",
            "-fd",
            "--",
            "data/records",
            "output/site",
            "output/dispatches",
            "data/dispatches/gaza/editions",
            "data/dispatches/gaza/sources",
            "logs",
        ],
        cwd=ROOT,
    )
    if clean.returncode != 0:
        raise RuntimeError(clean.stderr.strip() or clean.stdout.strip() or "git clean cleanup failed")


def _run_gaza_dry_run(
    *,
    edition_date: str,
    pages_repo: Path,
    pages_branch: str,
    remote_url: str,
    generate_audio: bool,
    tts_provider: str,
    audio_model: str,
    audio_voice: str,
    audio_format: str,
    audio_alternate_voices: bool,
    audio_voices: str,
    audio_segue_chime: str,
) -> dict[str, Any]:
    args = [
        sys.executable,
        "scripts\\run_daily_gaza.py",
        "--date",
        edition_date,
        "--dry-run",
        "--pages-repo",
        str(pages_repo),
        "--pages-branch",
        pages_branch,
        "--remote-url",
        remote_url,
    ]
    if generate_audio:
        args.extend(
            [
                "--generate-audio",
                "--tts-provider",
                tts_provider,
                "--audio-model",
                audio_model,
                "--audio-voice",
                audio_voice,
                "--audio-format",
                audio_format,
            ]
        )
        if audio_alternate_voices:
            args.append("--audio-alternate-voices")
        if audio_voices:
            args.extend(["--audio-voices", audio_voices])
        if audio_segue_chime:
            args.extend(["--audio-segue-chime", audio_segue_chime])

    result = _run_command(args, cwd=ROOT)
    payload = parse_json_stdout(result) if result.stdout.strip() else {}
    return {
        "command": command_text(args),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "payload": payload,
    }


def _run_pages_history_guard(
    *,
    edition_date: str,
    pages_repo: Path,
    pages_branch: str,
) -> dict[str, Any]:
    args = pages_publish_command(
        pages_repo,
        DEFAULT_REMOTE_URL,
        pages_branch,
        edition_date,
        dry_run=True,
        only_dispatches=("gaza",),
    )
    result = _run_command(args, cwd=ROOT)
    payload = parse_json_stdout(result) if result.stdout.strip() else {}
    return {
        "command": command_text(args),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "payload": payload,
    }


def _history_guard_status(payload: dict[str, Any]) -> dict[str, Any]:
    surfaces = []
    blockers: list[str] = []
    for row in payload.get("gaza_public_surface_history") or []:
        if not isinstance(row, dict):
            continue
        surface = str(row.get("surface") or "").strip()
        dropped_dates = list(row.get("dropped_dates") or [])
        current_count = int(row.get("current_count") or 0)
        previous_count = int(row.get("previous_count") or 0)
        ok = bool(row.get("ok")) and not dropped_dates
        if not ok:
            blockers.append(
                f"{surface} shrank from {previous_count} to {current_count}: {', '.join(str(item) for item in dropped_dates) or 'no dates preserved'}"
            )
        surfaces.append(
            {
                "surface": surface,
                "previous_count": previous_count,
                "current_count": current_count,
                "preserved_dates": list(row.get("preserved_dates") or []),
                "added_dates": list(row.get("added_dates") or []),
                "dropped_dates": dropped_dates,
                "ok": ok,
            }
        )
    return {"ok": not blockers and bool(surfaces), "surfaces": surfaces, "blockers": blockers}


def build_readiness_report(
    *,
    edition_date: str,
    pages_repo: Path = DEFAULT_PAGES_REPO,
    pages_branch: str = DEFAULT_PAGES_BRANCH,
    remote_url: str = DEFAULT_REMOTE_URL,
    generate_audio: bool = False,
    tts_provider: str = "none",
    audio_model: str = "gpt-4o-mini-tts",
    audio_voice: str = "alloy",
    audio_format: str = "mp3",
    audio_alternate_voices: bool = False,
    audio_voices: str = "",
    audio_segue_chime: str = "none",
) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    pages_repo = Path(pages_repo).resolve()

    source_repo_status = _repo_clean_status(ROOT)
    pages_repo_status = _pages_repo_sync_status(pages_repo, pages_branch)
    manual_sources_status = _manual_sources_status(edition_date)
    credentials_status = _credentials_status(generate_audio=generate_audio, tts_provider=tts_provider)
    assets_status = _asset_status()

    blockers: list[str] = []
    if not source_repo_status["clean"]:
        blockers.append("source repo has uncommitted changes")
    if not pages_repo_status["clean"]:
        blockers.append("pages repo has uncommitted changes")
    if not pages_repo_status["synced"]:
        blockers.append("pages repo is not synced to origin/gh-pages")
    if not manual_sources_status["ok"]:
        blockers.extend(manual_sources_status.get("errors") or [f"manual sources invalid: {manual_sources_status['path']}"])
    if not credentials_status["ok"]:
        blockers.append("missing required credentials/environment variables")
    if not assets_status["ok"]:
        blockers.append("missing required Gaza assets")

    dry_run_status: dict[str, Any] = {
        "ok": False,
        "skipped": bool(blockers),
        "command": None,
        "returncode": None,
        "stdout": None,
        "stderr": None,
        "payload": {},
    }
    history_guard_status: dict[str, Any] = {"ok": False, "skipped": True, "command": None, "returncode": None, "surfaces": [], "blockers": []}

    if not blockers:
        dry_run_result = _run_gaza_dry_run(
            edition_date=edition_date,
            pages_repo=pages_repo,
            pages_branch=pages_branch,
            remote_url=remote_url,
            generate_audio=generate_audio,
            tts_provider=tts_provider,
            audio_model=audio_model,
            audio_voice=audio_voice,
            audio_format=audio_format,
            audio_alternate_voices=audio_alternate_voices,
            audio_voices=audio_voices,
            audio_segue_chime=audio_segue_chime,
        )
        dry_run_status.update(dry_run_result)
        dry_run_payload = dict(dry_run_result.get("payload") or {})
        dry_run_status["ok"] = dry_run_result["returncode"] == 0 and dry_run_payload.get("ok") is not False
        if not dry_run_status["ok"]:
            blockers.extend(str(item) for item in dry_run_payload.get("errors") or [])
            if dry_run_result["returncode"] != 0:
                blockers.append("Gaza dry-run command failed")
        try:
            _cleanup_generated_artifacts(edition_date)
        except Exception as exc:  # noqa: BLE001
            dry_run_status["cleanup_ok"] = False
            dry_run_status["cleanup_error"] = str(exc)
        else:
            dry_run_status["cleanup_ok"] = True
        history_guard_status = _history_guard_status(dry_run_payload)
        history_guard_status.update(
            {
                "command": dry_run_status["command"],
                "returncode": dry_run_status["returncode"],
                "stdout": dry_run_status["stdout"],
                "stderr": dry_run_status["stderr"],
            }
        )
        if not dry_run_payload.get("pages_dry_run_ok"):
            blockers.append("Gaza dry-run pages check failed")
            history_guard_status["ok"] = False
        elif not history_guard_status["ok"]:
            blockers.extend(history_guard_status.get("blockers") or [])
    else:
        dry_run_status["skipped"] = True

    ok = not blockers and source_repo_status["clean"] and pages_repo_status["clean"] and pages_repo_status["synced"] and manual_sources_status["ok"] and credentials_status["ok"] and assets_status["ok"] and dry_run_status["ok"] and history_guard_status["ok"]
    next_action = "Schedule the Gaza daily run."
    if blockers:
        next_action = f"Fix the first blocker, then rerun: {blockers[0]}"

    return {
        "ok": ok,
        "date": edition_date,
        "source_repo_clean": bool(source_repo_status["clean"]),
        "pages_repo_clean": bool(pages_repo_status["clean"]),
        "pages_repo_synced": bool(pages_repo_status["synced"]),
        "manual_sources_status": manual_sources_status,
        "credentials_status": credentials_status,
        "assets_status": assets_status,
        "dry_run_status": dry_run_status,
        "history_guard_status": history_guard_status,
        "blockers": blockers,
        "next_action": next_action,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether the next Gaza daily run is likely to succeed.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repo path.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Pages branch.")
    parser.add_argument("--remote-url", default=DEFAULT_REMOTE_URL, help="Pages repo remote URL.")
    parser.add_argument("--generate-audio", action="store_true", help="Allow the readiness check to exercise Gaza audio generation.")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default="none", help="TTS provider to validate when --generate-audio is set.")
    parser.add_argument("--audio-model", default="gpt-4o-mini-tts", help="TTS model used when --generate-audio is set.")
    parser.add_argument("--audio-voice", default="alloy", help="TTS voice used when --generate-audio is set.")
    parser.add_argument("--audio-format", choices=("mp3", "wav"), default="mp3", help="Audio format used when --generate-audio is set.")
    parser.add_argument("--audio-alternate-voices", action="store_true", help="Validate alternating voice audio generation.")
    parser.add_argument("--audio-voices", default="", help="Comma-separated voices used with --audio-alternate-voices.")
    parser.add_argument("--audio-segue-chime", choices=("none", "gentle"), default="none", help="Optional segue chime used when --generate-audio is set.")
    args = parser.parse_args(argv)
    args.date = validate_date(args.date)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_readiness_report(
        edition_date=args.date,
        pages_repo=Path(args.pages_repo),
        pages_branch=args.pages_branch,
        remote_url=args.remote_url,
        generate_audio=bool(args.generate_audio),
        tts_provider=args.tts_provider,
        audio_model=args.audio_model,
        audio_voice=args.audio_voice,
        audio_format=args.audio_format,
        audio_alternate_voices=bool(args.audio_alternate_voices),
        audio_voices=str(args.audio_voices or ""),
        audio_segue_chime=str(args.audio_segue_chime or "none"),
    )
    print(json.dumps(report, separators=(",", ":"), ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
