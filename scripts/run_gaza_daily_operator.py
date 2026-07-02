from __future__ import annotations

import argparse
import io
import json
import re
import ssl
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.bluesky_post import maybe_post_gaza_dispatch_to_bluesky
from bluefern_dispatches.gaza_sources import validate_source_records as validate_collected_source_records
from scripts.run_and_notify import notification_error_message, send_email
from scripts.run_daily_gaza import DEFAULT_PAGES_REPO, DEFAULT_PAGES_BRANCH, DEFAULT_REMOTE_URL
import scripts.run_daily_gaza as daily


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SOURCE_BRANCH = "add/pages-repo-default"
SAFE_SOURCE_CLEAN_PREFIXES = (
    "data/records/",
    "output/site/",
    "output/dispatches/",
    "output/review/",
    "output/tmp-backups-pages/",
    "logs/",
)
MANUAL_REQUIRED_FIELDS = (
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "provider_id",
    "region_scope",
    "category_hint",
    "reliability_tier",
    "attribution_mode",
    "claim_status",
    "traceability_note",
)
NORMALIZABLE_FIELDS = {
    "source_record_id",
    "retrieved_at",
    "provider_id",
    "attribution_mode",
    "claim_status",
}
LIVE_RETRY_DELAYS = (0, 15, 30, 60, 60, 60, 60, 60, 60, 60, 60, 60)
LIVE_MARKERS = ("Dispatches From Gaza", "Today's Read", "Todayâ€™s Read", "Limited-source update", "Source Mix")


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def manual_source_path(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def edition_artifact_dir(edition_date: str) -> Path:
    return ROOT / "output" / "site" / "gaza" / "editions" / edition_date


def audio_file_path(edition_date: str, audio_format: str) -> Path:
    return ROOT / "output" / "site" / "gaza" / "audio" / f"{edition_date}.{audio_format}"


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/")


def _run_command(args: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_output(repo: Path, *args: str) -> str:
    result = _run_command(["git", *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_status_lines(repo: Path) -> list[str]:
    return [line for line in _git_output(repo, "status", "--short").splitlines() if line.strip()]


def _git_status_branch(repo: Path) -> str:
    return _git_output(repo, "status", "--short", "--branch")


def _git_branch(repo: Path) -> str:
    return _git_output(repo, "branch", "--show-current")


def _git_dirty_path(line: str) -> str:
    text = line[3:] if len(line) > 3 else line
    if " -> " in text:
        text = text.split(" -> ", 1)[1]
    return _normalize_repo_path(text.strip())


def _split_source_dirty_state(edition_date: str, status_lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    manual_path = _normalize_repo_path(str(manual_source_path(edition_date).relative_to(ROOT)))
    allowed_manual = {manual_path}
    keep: list[str] = []
    cleanable: list[str] = []
    risky: list[str] = []
    for line in status_lines:
        path = _git_dirty_path(line)
        if path in allowed_manual:
            keep.append(line)
            continue
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in SAFE_SOURCE_CLEAN_PREFIXES):
            cleanable.append(line)
            continue
        risky.append(line)
    return keep, cleanable, risky


def _pages_git_action_in_progress(pages_repo: Path) -> str | None:
    git_dir = pages_repo / ".git"
    markers = {
        "rebase-merge": "rebase",
        "rebase-apply": "rebase",
        "MERGE_HEAD": "merge",
        "CHERRY_PICK_HEAD": "cherry-pick",
    }
    for marker, label in markers.items():
        if (git_dir / marker).exists():
            return label
    return None


def _clean_source_generated_artifacts() -> dict[str, Any]:
    restore = _run_command(["git", "restore", "--source=HEAD", "--", "data/records", "output/site"], cwd=ROOT)
    clean = _run_command(["git", "clean", "-fd", "--", "output", "logs"], cwd=ROOT)
    commands = [
        "git restore --source=HEAD -- data/records output/site",
        "git clean -fd -- output logs",
    ]
    ok = restore.returncode == 0 and clean.returncode == 0
    return {
        "ok": ok,
        "status": "cleaned" if ok else "cleanup_failed",
        "commands": commands,
        "errors": [
            text
            for text in (
                restore.stderr.strip() or restore.stdout.strip(),
                clean.stderr.strip() or clean.stdout.strip(),
            )
            if text
        ],
    }


def _validate_pages_repo(pages_repo: Path, pages_branch: str) -> tuple[bool, str | None]:
    if not pages_repo.exists():
        return False, f"Pages repo does not exist: {pages_repo}"
    action = _pages_git_action_in_progress(pages_repo)
    if action:
        return False, f"Pages repo has an active {action} in progress. Resolve it before running the operator."
    branch = _git_branch(pages_repo)
    if branch != pages_branch:
        return False, f"Pages repo must be on {pages_branch}; found {branch or '<detached>'}."
    dirty = _git_status_lines(pages_repo)
    if dirty:
        return False, "Pages repo has uncommitted changes and cannot be reset safely:\n" + "\n".join(dirty)
    return True, None


def _sync_pages_repo(pages_repo: Path, pages_branch: str) -> dict[str, Any]:
    commands: list[str] = []
    fetch = _run_command(["git", "-C", str(pages_repo), "fetch", "origin"], cwd=ROOT)
    commands.append(f'git -C "{pages_repo}" fetch origin')
    if fetch.returncode != 0:
        return {"ok": False, "commands": commands, "error": fetch.stderr.strip() or fetch.stdout.strip()}
    reset = _run_command(["git", "-C", str(pages_repo), "reset", "--hard", f"origin/{pages_branch}"], cwd=ROOT)
    commands.append(f'git -C "{pages_repo}" reset --hard origin/{pages_branch}')
    if reset.returncode != 0:
        return {"ok": False, "commands": commands, "error": reset.stderr.strip() or reset.stdout.strip()}
    return {"ok": True, "commands": commands}


def _load_manual_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def _write_manual_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def normalize_manual_source_records(records: list[dict[str, Any]], edition_date: str) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    changes: list[str] = []
    for index, record in enumerate(records, start=1):
        row = dict(record)
        if not str(row.get("source_record_id") or "").strip():
            row["source_record_id"] = f"gaza-src-{edition_date}-{index:03d}"
            changes.append(f"source record {index}: added source_record_id")
        if not str(row.get("retrieved_at") or "").strip():
            row["retrieved_at"] = str(row.get("published_at") or f"{edition_date}T23:59:59Z").strip()
            changes.append(f"source record {index}: added retrieved_at")
        if not str(row.get("provider_id") or "").strip():
            row["provider_id"] = "manual-supplement"
            changes.append(f"source record {index}: added provider_id=manual-supplement")
        if not str(row.get("attribution_mode") or "").strip():
            row["attribution_mode"] = "reported_public_source"
            changes.append(f"source record {index}: added attribution_mode=reported_public_source")
        if not str(row.get("claim_status") or "").strip():
            row["claim_status"] = "reported_public_source"
            changes.append(f"source record {index}: added claim_status=reported_public_source")
        normalized.append(row)
    return normalized, changes


def validate_manual_source_records(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    collected_errors = validate_collected_source_records(records, min_sources=0)
    for index, record in enumerate(records, start=1):
        missing = [field for field in MANUAL_REQUIRED_FIELDS if not str(record.get(field) or "").strip()]
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
        url = str(record.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
    for error in collected_errors:
        if error not in errors:
            errors.append(error)
    return errors


def validate_or_repair_manual_sources(edition_date: str) -> dict[str, Any]:
    path = manual_source_path(edition_date)
    if not path.exists():
        return {"ok": True, "status": "not_present", "path": str(path), "changes": [], "errors": []}
    records = _load_manual_records(path)
    normalized, changes = normalize_manual_source_records(records, edition_date)
    errors = validate_manual_source_records(normalized)
    if errors:
        missing_only = True
        for error in errors:
            match = re.search(r"missing required fields: (.+)$", error)
            if not match:
                missing_only = False
                break
            missing = {part.strip() for part in match.group(1).split(",")}
            if not missing.issubset(NORMALIZABLE_FIELDS):
                missing_only = False
                break
        if missing_only:
            errors = validate_manual_source_records(normalized)
    if changes and not errors:
        _write_manual_records(path, normalized)
    return {
        "ok": not errors,
        "status": "normalized" if changes and not errors else ("valid" if not errors else "invalid"),
        "path": str(path),
        "changes": changes,
        "errors": errors,
        "record_count": len(normalized),
    }


def _capture_daily_run(args: list[str]) -> tuple[int, dict[str, Any], str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = daily.main(args)
    output = buffer.getvalue()
    summary: dict[str, Any] = {}
    match = re.search(r"(\{[\s\S]*\})\s*$", output)
    if match:
        summary = json.loads(match.group(1))
    return code, summary, output


def _daily_args(
    *,
    edition_date: str,
    pages_repo: Path,
    pages_branch: str,
    remote_url: str,
    dry_run: bool,
    generate_audio: bool,
    tts_provider: str,
    audio_model: str,
    audio_voice: str,
    audio_format: str,
    skip_tests: bool = False,
) -> list[str]:
    args = [
        "--date",
        edition_date,
        "--pages-repo",
        str(pages_repo),
        "--pages-branch",
        pages_branch,
        "--remote-url",
        remote_url,
    ]
    if dry_run:
        args.append("--dry-run")
    if skip_tests:
        args.append("--skip-tests")
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
    return args


def _remote_tree_verify(pages_repo: Path, pages_branch: str, edition_date: str) -> dict[str, Any]:
    errors: list[str] = []
    fetch = _run_command(["git", "fetch", "origin", pages_branch], cwd=pages_repo)
    if fetch.returncode != 0:
        return {"ok": False, "errors": [fetch.stderr.strip() or fetch.stdout.strip()], "remote_commit_sha": None}
    sha = _git_output(pages_repo, "rev-parse", f"origin/{pages_branch}")
    expected = (
        f"gaza/editions/{edition_date}/index.html",
        "gaza/index.html",
        "gaza/archive.html",
        "gaza/rss.xml",
    )
    for path in expected:
        result = _run_command(["git", "ls-tree", "--name-only", f"origin/{pages_branch}", "--", path], cwd=pages_repo)
        if path not in result.stdout.split():
            errors.append(f"remote tree is missing {path}")
    return {"ok": not errors, "errors": errors, "remote_commit_sha": sha}


def _cache_busted_url(url: str, token: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={token}"


def _fetch_url(url: str) -> tuple[int | None, str, str | None]:
    req = request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        context = ssl._create_unverified_context()
        with request.urlopen(req, timeout=30, context=context) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
        return status, body, None
    except Exception as exc:  # noqa: BLE001
        return None, "", str(exc)


def _excerpt(text: str, max_chars: int = 200) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:max_chars]


def verify_live_publication(
    *,
    edition_date: str,
    public_urls: dict[str, str],
    cache_token: str,
    remote_tree_ok: bool,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    edition_base = str(public_urls.get("edition") or "")
    archive_base = str(public_urls.get("archive") or "")
    homepage_base = edition_base.rsplit("/editions/", 1)[0] + "/"
    attempts: list[dict[str, Any]] = []
    for delay in LIVE_RETRY_DELAYS:
        if delay:
            sleep_fn(delay)
        edition_url = _cache_busted_url(edition_base, cache_token)
        archive_url = _cache_busted_url(archive_base, cache_token)
        homepage_url = _cache_busted_url(homepage_base, cache_token)
        edition_status, edition_body, edition_error = _fetch_url(edition_url)
        archive_status, archive_body, archive_error = _fetch_url(archive_url)
        homepage_status, homepage_body, homepage_error = _fetch_url(homepage_url)
        edition_marker_found = (
            edition_status == 200
            and edition_date in edition_body
            and "Dispatches From Gaza" in edition_body
            and any(marker in edition_body for marker in LIVE_MARKERS)
        )
        archive_ok = archive_status == 200 and edition_date in archive_body
        homepage_ok = homepage_status == 200 and edition_date in homepage_body
        attempt = {
            "edition_status": edition_status,
            "archive_status": archive_status,
            "homepage_status": homepage_status,
            "edition_error": edition_error,
            "archive_error": archive_error,
            "homepage_error": homepage_error,
            "edition_marker_found": edition_marker_found,
            "archive_ok": archive_ok,
            "homepage_ok": homepage_ok,
            "edition_excerpt": _excerpt(edition_body),
        }
        attempts.append(attempt)
        if edition_marker_found and archive_ok and homepage_ok:
            return {
                "ok": True,
                "status": "LIVE_OK",
                "live_http_ok": True,
                "live_archive_ok": True,
                "live_homepage_ok": True,
                "edition_url": edition_url,
                "archive_url": archive_url,
                "homepage_url": homepage_url,
                "attempts": attempts,
                "diagnostic_excerpt": attempt["edition_excerpt"],
            }
    last = attempts[-1] if attempts else {}
    pending = bool(remote_tree_ok and (last.get("edition_status") in {200, 404, None}))
    return {
        "ok": False,
        "status": "PAGES_PROPAGATION_PENDING" if pending else "LIVE_VERIFICATION_FAILED",
        "live_http_ok": bool(last.get("edition_marker_found")),
        "live_archive_ok": bool(last.get("archive_ok")),
        "live_homepage_ok": bool(last.get("homepage_ok")),
        "edition_url": _cache_busted_url(edition_base, cache_token),
        "archive_url": _cache_busted_url(archive_base, cache_token),
        "homepage_url": _cache_busted_url(homepage_base, cache_token),
        "attempts": attempts,
        "diagnostic_excerpt": str(last.get("edition_excerpt") or ""),
        "expected_marker": "Dispatches From Gaza + edition date + current Gaza label",
    }


def _build_email_subject(result: dict[str, Any]) -> str:
    return f"[Blue Fern Dispatches] Gaza operator {result['operator_status']} - {result['date']}"


def _build_email_body(result: dict[str, Any]) -> str:
    lines = [
        f"operator_status: {result.get('operator_status')}",
        f"ok: {str(bool(result.get('ok'))).lower()}",
        f"date: {result.get('date')}",
        f"source_count: {result.get('source_count')}",
        f"publisher_count: {result.get('publisher_count')}",
        f"public_story_count: {result.get('public_story_count')}",
        f"pages_commit_sha: {result.get('pages_commit_sha')}",
        f"bluesky_status: {result.get('bluesky_status')}",
        f"audio_status: {result.get('audio_status')}",
        f"email_status: {result.get('email_status')}",
        f"cleanup_status: {result.get('cleanup_status')}",
        f"public_url: {result.get('public_url') or '<none>'}",
        f"next_action: {result.get('next_action') or '<none>'}",
    ]
    return "\n".join(lines)


def _maybe_send_email(result: dict[str, Any], smtp_debug: bool) -> str:
    send_email(_build_email_subject(result), _build_email_body(result), str(result["date"]), smtp_debug=smtp_debug)
    return "sent"


def _operator_result_base(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "operator_status": "FAILED",
        "date": args.date,
        "source_count": 0,
        "publisher_count": 0,
        "public_story_count": 0,
        "generation_ok": False,
        "tests_ok": None,
        "validation_ok": None,
        "pages_synced_before_publish": False,
        "pages_commit_sha": None,
        "pages_push_ok": None,
        "remote_tree_verify_ok": None,
        "live_verify_status": None,
        "live_http_ok": None,
        "live_archive_ok": None,
        "bluesky_status": "skipped",
        "bluesky_post_uri": None,
        "audio_status": "audio_skipped",
        "email_status": "not_requested",
        "cleanup_status": "not_run",
        "source_repo_status_after": None,
        "pages_repo_status_after": None,
        "next_action": None,
        "public_url": f"https://dispatches.thebluefernco.com/gaza/editions/{args.date}/",
        "commands_run": [],
        "manual_source_validation": None,
        "pages_repo": str(Path(args.pages_repo)),
    }


def run_operator(args: argparse.Namespace) -> dict[str, Any]:
    result = _operator_result_base(args)
    pages_repo = Path(args.pages_repo).resolve()
    source_status_before = _git_status_lines(ROOT)
    kept, cleanable, risky = _split_source_dirty_state(args.date, source_status_before)
    if risky:
        result["operator_status"] = "REPO_DIRTY_BLOCKED"
        result["next_action"] = "Clean or commit unexpected source repo changes before rerunning."
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo) if pages_repo.exists() else None
        return result
    if cleanable:
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        if not cleanup["ok"]:
            result["cleanup_status"] = cleanup["status"]
            result["operator_status"] = "REPO_DIRTY_BLOCKED"
            result["next_action"] = "Run the printed cleanup commands manually and rerun."
            result["source_repo_status_after"] = _git_status_branch(ROOT)
            result["pages_repo_status_after"] = _git_status_branch(pages_repo) if pages_repo.exists() else None
            return result
    current_branch = _git_branch(ROOT)
    if current_branch != args.expected_source_branch:
        result["operator_status"] = "REPO_DIRTY_BLOCKED"
        result["next_action"] = f"Switch source repo to {args.expected_source_branch} or override --expected-source-branch."
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo) if pages_repo.exists() else None
        return result
    pages_ok, pages_error = _validate_pages_repo(pages_repo, args.pages_branch)
    if not pages_ok:
        result["operator_status"] = "REPO_DIRTY_BLOCKED"
        result["next_action"] = pages_error
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo) if pages_repo.exists() else None
        return result

    manual_validation = validate_or_repair_manual_sources(args.date)
    result["manual_source_validation"] = manual_validation
    if not manual_validation["ok"]:
        result["operator_status"] = "MANUAL_SOURCE_INVALID"
        result["next_action"] = "; ".join(manual_validation["errors"])
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result
    if args.manual_source_check_only:
        result["ok"] = True
        result["operator_status"] = "MANUAL_SOURCE_VALID"
        result["next_action"] = "Manual source validation completed."
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    if args.post_bluesky_only:
        live = verify_live_publication(
            edition_date=args.date,
            public_urls={
                "edition": result["public_url"],
                "archive": "https://dispatches.thebluefernco.com/gaza/archive.html",
            },
            cache_token=args.date,
            remote_tree_ok=True,
            sleep_fn=lambda _seconds: None,
        )
        result["live_verify_status"] = live["status"]
        result["live_http_ok"] = live["live_http_ok"]
        result["live_archive_ok"] = live["live_archive_ok"]
        if live["status"] != "LIVE_OK":
            result["operator_status"] = live["status"]
            result["next_action"] = "Wait for the live site to update, then rerun with --post-bluesky-only."
        else:
            bluesky = maybe_post_gaza_dispatch_to_bluesky(
                edition_date=args.date,
                public_url=str(result["public_url"]),
                run_succeeded=True,
                post_requested=not args.skip_bluesky,
                project_root=ROOT,
                force_post=bool(args.force_bluesky_post),
                allow_publish=not args.dry_run,
            )
            result["bluesky_status"] = str(bluesky.get("status") or "skipped")
            result["bluesky_post_uri"] = bluesky.get("post_uri")
            if result["bluesky_status"] == "success":
                result["ok"] = True
                result["operator_status"] = "PUBLISHED_AND_POSTED"
            elif str(bluesky.get("reason")) == "skipped_existing_receipt":
                result["ok"] = True
                result["operator_status"] = "ALREADY_POSTED"
            elif str(bluesky.get("reason")) == "dry_run":
                result["ok"] = True
                result["operator_status"] = "BLUESKY_PREVIEW_READY"
            else:
                result["operator_status"] = "BLUESKY_FAILED"
                result["next_action"] = str(bluesky.get("reason") or "Bluesky post-only run failed.")
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    sync = _sync_pages_repo(pages_repo, args.pages_branch)
    result["commands_run"].extend(sync["commands"])
    if not sync["ok"]:
        result["operator_status"] = "REPO_DIRTY_BLOCKED"
        result["next_action"] = str(sync.get("error") or "Pages repo sync failed.")
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result
    result["pages_synced_before_publish"] = True

    generate_audio = bool(args.generate_audio and not args.skip_audio)
    if generate_audio and audio_file_path(args.date, args.audio_format).exists() and not args.force_audio:
        generate_audio = False
        result["audio_status"] = "audio_reused_existing"

    daily_args = _daily_args(
        edition_date=args.date,
        pages_repo=pages_repo,
        pages_branch=args.pages_branch,
        remote_url=args.remote_url,
        dry_run=bool(args.dry_run),
        generate_audio=generate_audio,
        tts_provider=args.tts_provider,
        audio_model=args.audio_model,
        audio_voice=args.audio_voice,
        audio_format=args.audio_format,
    )
    code, summary, _stdout = _capture_daily_run(daily_args)
    result["commands_run"].append("scripts/run_daily_gaza.py " + " ".join(daily_args))
    result["source_count"] = int(summary.get("source_count") or 0)
    result["publisher_count"] = int(summary.get("publisher_count") or 0)
    result["public_story_count"] = int(summary.get("public_story_count") or 0)
    result["generation_ok"] = bool(summary.get("generation_ok"))
    result["tests_ok"] = summary.get("tests_ok")
    result["validation_ok"] = summary.get("validation_ok")
    result["pages_commit_sha"] = summary.get("pages_commit_sha")
    if args.generate_audio and result["audio_status"] == "audio_skipped":
        result["audio_status"] = "audio_generated" if generate_audio else "audio_reused_existing"
    if code != 0:
        errors = [str(item) for item in summary.get("errors") or []]
        no_publication = any(
            "No valid traceable Gaza sources survived normalization and dedupe" in item or "all candidates were suppressed as repeated or stale-risk" in item
            for item in errors
        )
        if no_publication:
            result["ok"] = True
            result["operator_status"] = "NO_PUBLICATION_NEEDED"
            result["generation_ok"] = True
            result["validation_ok"] = True
            result["next_action"] = "No dispatch was published because no new source-backed Gaza update qualified."
        else:
            result["operator_status"] = "AUDIO_FAILED" if any("audio generation failed" in item for item in errors) else "FAILED"
            result["next_action"] = errors[0] if errors else "Daily Gaza run failed."
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    if args.dry_run:
        if args.post_bluesky and not args.skip_bluesky:
            bluesky = maybe_post_gaza_dispatch_to_bluesky(
                edition_date=args.date,
                public_url=str(result["public_url"]),
                run_succeeded=True,
                post_requested=True,
                project_root=ROOT,
                force_post=bool(args.force_bluesky_post),
                allow_publish=False,
            )
            result["bluesky_status"] = str(bluesky.get("status") or "skipped")
            result["bluesky_post_uri"] = bluesky.get("post_uri")
        result["ok"] = True
        result["operator_status"] = "DRY_RUN_READY"
        result["next_action"] = "Review the dry-run summary; rerun with --push for live publication."
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    if not args.push:
        result["ok"] = True
        result["operator_status"] = "LOCAL_PUBLISH_READY"
        result["next_action"] = f'Push from the Pages repo or rerun with --push. Pages repo: "{pages_repo}"'
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    push = _run_command(["git", "-C", str(pages_repo), "push", "origin", args.pages_branch], cwd=ROOT)
    result["commands_run"].append(f'git -C "{pages_repo}" push origin {args.pages_branch}')
    if push.returncode != 0:
        result["operator_status"] = "FAILED"
        result["pages_push_ok"] = False
        result["next_action"] = push.stderr.strip() or push.stdout.strip() or "Pages push failed."
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result
    result["pages_push_ok"] = True

    remote_verify = _remote_tree_verify(pages_repo, args.pages_branch, args.date)
    result["remote_tree_verify_ok"] = bool(remote_verify["ok"])
    if remote_verify.get("remote_commit_sha"):
        result["pages_commit_sha"] = remote_verify["remote_commit_sha"]
    live = verify_live_publication(
        edition_date=args.date,
        public_urls={"edition": str(result["public_url"]), "archive": "https://dispatches.thebluefernco.com/gaza/archive.html"},
        cache_token=str(result["pages_commit_sha"] or args.date),
        remote_tree_ok=bool(remote_verify["ok"]),
    )
    result["live_verify_status"] = live["status"]
    result["live_http_ok"] = live["live_http_ok"]
    result["live_archive_ok"] = live["live_archive_ok"]
    if live["status"] != "LIVE_OK":
        result["ok"] = live["status"] == "PAGES_PROPAGATION_PENDING"
        result["operator_status"] = live["status"]
        result["next_action"] = (
            f"Wait for Pages propagation, then rerun: python scripts/run_gaza_daily_operator.py --date {args.date} --post-bluesky-only"
            if live["status"] == "PAGES_PROPAGATION_PENDING"
            else "Inspect live verification diagnostics and marker expectations."
        )
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    if args.skip_bluesky or not args.post_bluesky:
        result["ok"] = True
        result["operator_status"] = "PUBLISHED_NOT_POSTED"
        result["next_action"] = (
            f"Run post-only if needed: python scripts/run_gaza_daily_operator.py --date {args.date} --post-bluesky-only"
            if args.skip_bluesky
            else "Publication completed."
        )
        cleanup = _clean_source_generated_artifacts()
        result["commands_run"].extend(cleanup["commands"])
        result["cleanup_status"] = cleanup["status"]
        result["source_repo_status_after"] = _git_status_branch(ROOT)
        result["pages_repo_status_after"] = _git_status_branch(pages_repo)
        return result

    bluesky = maybe_post_gaza_dispatch_to_bluesky(
        edition_date=args.date,
        public_url=str(result["public_url"]),
        run_succeeded=True,
        post_requested=True,
        project_root=ROOT,
        force_post=bool(args.force_bluesky_post),
        allow_publish=True,
    )
    result["bluesky_status"] = str(bluesky.get("status") or "skipped")
    result["bluesky_post_uri"] = bluesky.get("post_uri")
    if result["bluesky_status"] == "success":
        result["ok"] = True
        result["operator_status"] = "PUBLISHED_AND_POSTED"
        result["next_action"] = "Publication and Bluesky posting completed."
    elif str(bluesky.get("reason")) == "skipped_existing_receipt":
        result["ok"] = True
        result["operator_status"] = "ALREADY_POSTED"
        result["next_action"] = "Publication was already posted to Bluesky."
    else:
        result["operator_status"] = "BLUESKY_FAILED"
        result["next_action"] = str(bluesky.get("reason") or "Bluesky posting failed.")

    cleanup = _clean_source_generated_artifacts()
    result["commands_run"].extend(cleanup["commands"])
    result["cleanup_status"] = cleanup["status"]
    result["source_repo_status_after"] = _git_status_branch(ROOT)
    result["pages_repo_status_after"] = _git_status_branch(pages_repo)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the safe Gaza daily operator.")
    parser.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--dry-run", action="store_true", help="Run generation and validation without pushing Pages or posting live.")
    parser.add_argument("--post-bluesky", action="store_true", help="Preview or post the Gaza Bluesky card as part of the operator run.")
    parser.add_argument("--generate-audio", action="store_true", help="Generate Gaza audio artifacts during the run.")
    parser.add_argument("--email-report", action="store_true", help="Send the operator summary email.")
    parser.add_argument("--smtp-debug", action="store_true", help="Enable SMTP debug output when sending the operator email.")
    parser.add_argument("--push", action="store_true", help="Push the Pages repo after local publish succeeds.")
    parser.add_argument("--skip-audio", action="store_true", help="Skip audio generation even when audio artifacts exist or generation is requested.")
    parser.add_argument("--skip-bluesky", action="store_true", help="Disable Bluesky posting for this run.")
    parser.add_argument("--force-pages-rebuild", action="store_true", help="Reserved compatibility flag; the operator already fetches and hard-resets Pages safely.")
    parser.add_argument("--manual-source-check-only", action="store_true", help="Validate and safely normalize Gaza manual_sources.json, then exit.")
    parser.add_argument("--post-bluesky-only", action="store_true", help="Skip generation/publish and only finish the Bluesky post after live verification.")
    parser.add_argument("--force-bluesky-post", action="store_true", help="Ignore an existing Bluesky receipt and post again.")
    parser.add_argument("--force-audio", action="store_true", help="Regenerate dated Gaza audio even when an existing audio file is already present.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repo path.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Pages branch.")
    parser.add_argument("--remote-url", default=DEFAULT_REMOTE_URL, help="Pages repo remote URL.")
    parser.add_argument("--expected-source-branch", default=DEFAULT_SOURCE_BRANCH, help="Required source repo branch for safety checks.")
    parser.add_argument("--tts-provider", choices=("none", "openai"), default="none", help="TTS provider used with --generate-audio.")
    parser.add_argument("--audio-model", default="gpt-4o-mini-tts", help="TTS model used with --generate-audio.")
    parser.add_argument("--audio-voice", default="alloy", help="TTS voice used with --generate-audio.")
    parser.add_argument("--audio-format", choices=("mp3", "wav"), default="mp3", help="Audio format.")
    args = parser.parse_args(argv)
    args.date = validate_date(args.date)
    return args


def _print_human_summary(result: dict[str, Any]) -> None:
    print(f"Gaza operator status: {result['operator_status']}")
    print(f"Date: {result['date']}")
    print(f"Next action: {result.get('next_action') or '<none>'}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_operator(args)
    if args.email_report:
        try:
            result["email_status"] = _maybe_send_email(result, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            result["email_status"] = f"failed: {notification_error_message(exc)}"
            if result.get("ok") and not args.dry_run:
                result.setdefault("warnings", []).append(result["email_status"])
                result["source_repo_status_after"] = _git_status_branch(ROOT)
                result["pages_repo_status_after"] = _git_status_branch(Path(args.pages_repo))
                print(f"Email warning: {result['email_status']}")
                _print_human_summary(result)
                print(json.dumps(result, indent=2))
                return 0
            if result.get("ok"):
                result["ok"] = False
                result["next_action"] = result["email_status"]
    _print_human_summary(result)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
