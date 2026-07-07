from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts import preflight_repo_state as preflight
from scripts.run_gaza_daily_operator import validate_manual_source_records


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOG_FIELD_RE = re.compile(r"^(?:summary\.)?(?P<key>[a-z_]+):\s*(?P<value>.*)$", re.IGNORECASE)
LIVE_TIMEOUT_SECONDS = 6
MANUAL_TRACEABILITY_FIELDS = ("traceability_note", "attribution_mode", "claim_status")


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_text(repo: Path, *args: str) -> str | None:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def _git_status_lines(repo: Path) -> list[str]:
    result = _run_git(repo, "status", "--short", "--branch", "--untracked-files=all")
    if result.returncode != 0:
        return []
    return [line.rstrip("\n") for line in (result.stdout or "").splitlines()]


def _git_tracking_relation(repo: Path) -> dict[str, Any]:
    upstream = _git_text(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    relation: dict[str, Any] = {
        "upstream": upstream,
        "ahead": None,
        "behind": None,
    }
    if not upstream:
        return relation
    counts = _git_text(repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if not counts:
        return relation
    parts = counts.split()
    if len(parts) != 2:
        return relation
    try:
        relation["behind"] = int(parts[0])
        relation["ahead"] = int(parts[1])
    except ValueError:
        return relation
    return relation


def summarize_git_repo(repo: Path) -> dict[str, Any]:
    status_lines = _git_status_lines(repo)
    entries = [entry for entry in (preflight.classify_status_line(line) for line in status_lines) if entry is not None]
    summary = preflight.summarize_entries(entries)
    state = "clean" if not entries else ("risky" if summary["risky_entries"] else "dirty")
    relation = _git_tracking_relation(repo)
    branch = _git_text(repo, "branch", "--show-current")
    head = _git_text(repo, "rev-parse", "--short", "HEAD")
    origin_ref = _git_text(repo, "rev-parse", "origin/HEAD")
    origin_branch = None
    origin_head = None
    if branch:
        origin_branch = f"origin/{branch}"
        origin_head = _git_text(repo, "rev-parse", origin_branch)
    if origin_head is None and origin_ref:
        origin_branch = origin_ref
        origin_head = _git_text(repo, "rev-parse", origin_ref)
    return {
        "path": str(repo),
        "exists": repo.exists(),
        "branch": branch,
        "upstream": relation["upstream"],
        "ahead": relation["ahead"],
        "behind": relation["behind"],
        "head_sha": head,
        "origin_head_sha": origin_head,
        "state": state,
        "clean": state == "clean",
        "dirty": state == "dirty",
        "risky": state == "risky",
        "status_lines": status_lines,
        "entries": entries,
        "summary": summary,
    }


def manual_sources_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def _load_manual_sources(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path.name} must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def summarize_manual_sources(root: Path, edition_date: str) -> dict[str, Any]:
    path = manual_sources_path(root, edition_date)
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "not_present",
            "record_count": 0,
            "field_counts": {field: {"present": 0, "missing": 0} for field in MANUAL_TRACEABILITY_FIELDS},
            "missing_fields": {field: [] for field in MANUAL_TRACEABILITY_FIELDS},
            "errors": [],
        }

    try:
        records = _load_manual_sources(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "path": str(path),
            "exists": True,
            "status": "invalid",
            "record_count": 0,
            "field_counts": {field: {"present": 0, "missing": 0} for field in MANUAL_TRACEABILITY_FIELDS},
            "missing_fields": {field: [] for field in MANUAL_TRACEABILITY_FIELDS},
            "errors": [str(exc)],
        }

    field_counts = {field: {"present": 0, "missing": 0} for field in MANUAL_TRACEABILITY_FIELDS}
    missing_fields: dict[str, list[int]] = {field: [] for field in MANUAL_TRACEABILITY_FIELDS}
    errors = validate_manual_source_records(records)
    for index, record in enumerate(records, start=1):
        for field in MANUAL_TRACEABILITY_FIELDS:
            value = str(record.get(field) or "").strip()
            if value:
                field_counts[field]["present"] += 1
            else:
                field_counts[field]["missing"] += 1
                missing_fields[field].append(index)
        if not str(record.get("traceability_note") or "").strip():
            errors.append(f"record {index} missing traceability_note")
        if not str(record.get("attribution_mode") or "").strip():
            errors.append(f"record {index} missing attribution_mode")
        if not str(record.get("claim_status") or "").strip():
            errors.append(f"record {index} missing claim_status")

    status = "valid" if not errors else "invalid"
    next_action = "No action needed."
    if errors:
        if any("placeholder/example source" in error for error in errors):
            next_action = "Remove or replace the placeholder/example source record and rerun."
        elif any("missing traceability_note" in error or "missing attribution_mode" in error or "missing claim_status" in error for error in errors):
            next_action = "Fill the missing manual source traceability fields and rerun."
        else:
            next_action = "Fix the invalid manual source record(s) and rerun."
    return {
        "path": str(path),
        "exists": True,
        "status": status,
        "record_count": len(records),
        "field_counts": field_counts,
        "missing_fields": missing_fields,
        "errors": errors,
        "next_action": next_action,
    }


def _artifact_report(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def _text_contains(path: Path, needle: str) -> dict[str, Any]:
    report = _artifact_report(path)
    if not report["exists"]:
        report["contains"] = False
        return report
    text = path.read_text(encoding="utf-8", errors="replace")
    report["contains"] = needle in text
    return report


def summarize_source_artifacts(root: Path, edition_date: str) -> dict[str, Any]:
    base = root / "data" / "dispatches" / "gaza" / "editions" / edition_date
    return {
        "run_manifest": _artifact_report(base / "run_manifest.json"),
        "dedupe_report": _artifact_report(base / "dedupe_report.json"),
        "source_diversity_report": _artifact_report(base / "source_diversity_report.json"),
    }


def summarize_pages_artifacts(root: Path, edition_date: str) -> dict[str, Any]:
    pages_root = root / "bluefern-dispatches-pages"
    edition_path = pages_root / "gaza" / "editions" / edition_date / "index.html"
    audio_transcript = pages_root / "gaza" / "audio" / f"{edition_date}-transcript.html"
    audio_mp3 = pages_root / "gaza" / "audio" / f"{edition_date}.mp3"
    audio_index = pages_root / "gaza" / "audio" / "index.html"
    audio_podcast = pages_root / "gaza" / "audio" / "podcast.xml"
    site_podcast = pages_root / "gaza" / "podcast.xml"
    archive = pages_root / "gaza" / "archive.html"
    rss = pages_root / "gaza" / "rss.xml"
    index = pages_root / "gaza" / "index.html"

    audio_index_text = audio_index.read_text(encoding="utf-8", errors="replace") if audio_index.exists() else ""
    audio_podcast_text = audio_podcast.read_text(encoding="utf-8", errors="replace") if audio_podcast.exists() else ""
    site_podcast_text = site_podcast.read_text(encoding="utf-8", errors="replace") if site_podcast.exists() else ""
    archive_text = archive.read_text(encoding="utf-8", errors="replace") if archive.exists() else ""
    rss_text = rss.read_text(encoding="utf-8", errors="replace") if rss.exists() else ""
    index_text = index.read_text(encoding="utf-8", errors="replace") if index.exists() else ""

    date_token = edition_date
    mp3_name = f"{edition_date}.mp3"

    return {
        "edition_page": _artifact_report(edition_path),
        "audio_transcript": _artifact_report(audio_transcript),
        "audio_mp3": _artifact_report(audio_mp3),
        "audio_index": {
            "path": str(audio_index),
            "exists": audio_index.exists(),
            "links_date": date_token in audio_index_text,
            "links_mp3": mp3_name in audio_index_text,
        },
        "podcast_feed": {
            "audio_podcast": {
                "path": str(audio_podcast),
                "exists": audio_podcast.exists(),
                "includes_mp3": mp3_name in audio_podcast_text,
            },
            "site_podcast": {
                "path": str(site_podcast),
                "exists": site_podcast.exists(),
                "includes_mp3": mp3_name in site_podcast_text,
            },
        },
        "archive": {
            "path": str(archive),
            "exists": archive.exists(),
            "includes_date": date_token in archive_text,
        },
        "rss": {
            "path": str(rss),
            "exists": rss.exists(),
            "includes_date": date_token in rss_text,
        },
        "index": {
            "path": str(index),
            "exists": index.exists(),
            "latest_link_includes_date": f"editions/{edition_date}/" in index_text or date_token in index_text,
        },
    }


def _fetch_url(url: str, *, method: str = "GET") -> tuple[int | None, str, str | None]:
    req = request.Request(url, headers={"Cache-Control": "no-cache"}, method=method)
    try:
        context = ssl.create_default_context()
        with request.urlopen(req, timeout=LIVE_TIMEOUT_SECONDS, context=context) as response:
            status = getattr(response, "status", response.getcode())
            body = response.read().decode("utf-8", errors="replace") if method != "HEAD" else ""
        return int(status), body, None
    except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, "", str(exc)


def _live_page_status(url: str, edition_date: str, *, expect_mp3: str | None = None) -> dict[str, Any]:
    status, body, err = _fetch_url(url, method="GET")
    result = {"url": url, "status_code": status, "error": err, "body": body}
    if status == 200 and body:
        result["contains_date"] = edition_date in body
        if expect_mp3:
            result["contains_mp3"] = expect_mp3 in body
    return result


def _live_mp3_status(url: str) -> dict[str, Any]:
    status, _body, err = _fetch_url(url, method="HEAD")
    return {"url": url, "status_code": status, "error": err}


def summarize_live(root: Path, edition_date: str, *, enabled: bool = True) -> dict[str, Any]:
    base = "https://dispatches.thebluefernco.com/gaza"
    urls = {
        "edition": f"{base}/editions/{edition_date}/",
        "audio_mp3": f"{base}/audio/{edition_date}.mp3",
        "audio_index": f"{base}/audio/index.html",
        "archive": f"{base}/archive.html",
        "rss": f"{base}/rss.xml",
    }
    if not enabled:
        return {
            "enabled": False,
            "ok": True,
            "status": "skipped",
            "edition": {"url": urls["edition"], "status_code": None, "error": None},
            "audio_mp3": {"url": urls["audio_mp3"], "status_code": None, "error": None},
            "audio_index": {"url": urls["audio_index"], "status_code": None, "error": None},
            "archive": {"url": urls["archive"], "status_code": None, "error": None},
            "rss": {"url": urls["rss"], "status_code": None, "error": None},
        }

    edition = _live_page_status(urls["edition"], edition_date)
    audio_mp3 = _live_mp3_status(urls["audio_mp3"])
    audio_index = _live_page_status(urls["audio_index"], edition_date, expect_mp3=f"{edition_date}.mp3")
    archive = _live_page_status(urls["archive"], edition_date)
    rss = _live_page_status(urls["rss"], edition_date)

    checks = [edition, audio_mp3, audio_index, archive, rss]
    actionable = any(
        check.get("status_code") not in {200, None}
        for check in checks
    ) or any(
        check.get("status_code") == 200 and check.get("contains_date") is False
        for check in (edition, audio_index, archive, rss)
    ) or (audio_index.get("status_code") == 200 and audio_index.get("contains_mp3") is False)

    unknown_only = all(check.get("status_code") is None for check in checks)
    return {
        "enabled": True,
        "ok": not actionable,
        "status": "healthy" if not actionable else "action_needed",
        "unknown_only": unknown_only,
        "edition": edition,
        "audio_mp3": audio_mp3,
        "audio_index": audio_index,
        "archive": archive,
        "rss": rss,
    }


def _normalize_log_value(value: str) -> Any:
    text = value.strip()
    if text in {"<none>", "none", "null", "None", ""}:
        return None
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return text


def extract_log_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(path), "exists": path.exists(), "fields": {}}
    if not path.exists():
        return summary
    text = path.read_text(encoding="utf-8", errors="replace")
    fields = {
        "ok",
        "operator_status",
        "generation_ok",
        "tests_ok",
        "validation_ok",
        "audio_status",
        "email_status",
        "bluesky_status",
        "bluesky_post_uri",
        "pages_commit_sha",
        "pages_push_ok",
        "live_http_ok",
        "live_archive_ok",
        "next_action",
    }
    extracted: dict[str, Any] = {}
    for line in text.splitlines():
        match = LOG_FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").lower()
        if key not in fields:
            continue
        extracted[key] = _normalize_log_value(match.group("value"))
    summary["fields"] = extracted
    summary["latest_status"] = extracted.get("operator_status") or extracted.get("ok")
    summary["latest_next_action"] = extracted.get("next_action")
    return summary


def summarize_recent_logs(root: Path) -> dict[str, Any]:
    logs = root / "logs"
    runner_logs = sorted(logs.glob("runner-gaza-*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True) if logs.exists() else []
    daily_logs = sorted(logs.glob("gaza-daily-*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True) if logs.exists() else []
    runner = extract_log_summary(runner_logs[0]) if runner_logs else {"exists": False, "fields": {}}
    daily = extract_log_summary(daily_logs[0]) if daily_logs else {"exists": False, "fields": {}}
    merged_fields: dict[str, Any] = {}
    for source in (runner.get("fields") or {}, daily.get("fields") or {}):
        for key, value in source.items():
            merged_fields.setdefault(key, value)
    latest_status = merged_fields.get("operator_status") or merged_fields.get("ok")
    latest_next_action = merged_fields.get("next_action")
    return {
        "runner_gaza": runner if runner_logs else {"path": None, "exists": False, "fields": {}},
        "gaza_daily": daily if daily_logs else {"path": None, "exists": False, "fields": {}},
        "merged_fields": merged_fields,
        "latest_status": latest_status,
        "latest_next_action": latest_next_action,
    }


def _status_from_manual(manual: dict[str, Any]) -> tuple[str, bool]:
    if not manual["exists"]:
        return "not_present", False
    if manual["status"] != "valid":
        return "invalid", True
    return "valid", False


def _has_actionable_missing_artifact(artifact: dict[str, Any]) -> bool:
    return not bool(artifact.get("exists"))


def summarize_overall(
    *,
    source_repo: dict[str, Any],
    manual_sources: dict[str, Any],
    source_artifacts: dict[str, Any],
    pages_repo: dict[str, Any],
    pages_artifacts: dict[str, Any],
    live: dict[str, Any],
    recent_logs: dict[str, Any],
    edition_date: str,
) -> dict[str, Any]:
    issues: list[str] = []
    if source_repo["risky"]:
        issues.append("Clean or commit risky source repo changes before continuing.")
    if pages_repo["risky"] or pages_repo["dirty"]:
        issues.append("Resolve dirty Pages repo changes before continuing.")
    manual_status, manual_blocking = _status_from_manual(manual_sources)
    if manual_blocking:
        if any("placeholder/example source" in error for error in manual_sources.get("errors") or []):
            issues.append("Remove or replace the placeholder/example manual source record and rerun.")
        else:
            issues.append(manual_sources.get("next_action") or f"Fix {Path(manual_sources['path']).name} validation for {edition_date}.")
    if _has_actionable_missing_artifact(source_artifacts["run_manifest"]):
        issues.append("Run the Gaza pipeline to create the run manifest.")
    if _has_actionable_missing_artifact(source_artifacts["dedupe_report"]):
        issues.append("Run the Gaza pipeline to create the dedupe report.")
    if _has_actionable_missing_artifact(source_artifacts["source_diversity_report"]):
        issues.append("Run the Gaza pipeline to create the source diversity report.")
    if _has_actionable_missing_artifact(pages_artifacts["edition_page"]):
        issues.append("Publish the Gaza edition page into the Pages repo.")
    if _has_actionable_missing_artifact(pages_artifacts["audio_transcript"]):
        issues.append("Publish the Gaza audio transcript into the Pages repo.")
    if _has_actionable_missing_artifact(pages_artifacts["audio_mp3"]):
        issues.append("Publish the Gaza MP3 into the Pages repo.")
    if live["enabled"] and not live["ok"] and not live.get("unknown_only"):
        issues.append("Recheck live Gaza URLs or republish the edition.")

    latest_log_fields = recent_logs.get("merged_fields") or {}
    if not issues and latest_log_fields.get("ok") is False:
        issues.append("Recent runner logs report a failed Gaza run.")
    if not issues and str(latest_log_fields.get("operator_status") or "").upper() in {"FAILED", "AUDIO_FAILED", "BLUESKY_FAILED", "REPO_DIRTY_BLOCKED", "MANUAL_SOURCE_INVALID", "PAGES_REPO_AHEAD_BLOCKED"}:
        issues.append(f"Recent runner logs report {latest_log_fields.get('operator_status')}.")

    overall_status = "healthy" if not issues else "action_needed"
    next_action = "No action needed. Gaza {date} appears published and verified.".format(date=edition_date)
    if issues:
        next_action = issues[0]
    elif live.get("enabled") and live.get("unknown_only"):
        next_action = "Live checks were unreachable, but no local blockers were found."
    return {
        "overall_status": overall_status,
        "next_action": next_action,
        "issues": issues,
        "manual_status": manual_status,
    }


def build_report(root: Path, edition_date: str, *, pages_repo: Path, pages_branch: str, live_checks: bool) -> dict[str, Any]:
    source_repo = summarize_git_repo(root)
    pages_repo_report = summarize_git_repo(pages_repo)
    manual_sources = summarize_manual_sources(root, edition_date)
    source_artifacts = summarize_source_artifacts(root, edition_date)
    pages_artifacts = summarize_pages_artifacts(root, edition_date)
    live = summarize_live(root, edition_date, enabled=live_checks)
    recent_logs = summarize_recent_logs(root)
    overall = summarize_overall(
        source_repo=source_repo,
        manual_sources=manual_sources,
        source_artifacts=source_artifacts,
        pages_repo=pages_repo_report,
        pages_artifacts=pages_artifacts,
        live=live,
        recent_logs=recent_logs,
        edition_date=edition_date,
    )
    return {
        "date": edition_date,
        "source_repo": source_repo,
        "pages_repo": pages_repo_report,
        "manual_sources": manual_sources,
        "source_artifacts": source_artifacts,
        "pages_artifacts": pages_artifacts,
        "live": live,
        "recent_logs": recent_logs,
        "overall_status": overall["overall_status"],
        "next_action": overall["next_action"],
        "issues": overall["issues"],
        "pages_branch": pages_branch,
    }


def render_text_report(report: dict[str, Any]) -> str:
    source_repo = report["source_repo"]
    pages_repo = report["pages_repo"]
    manual = report["manual_sources"]
    source_artifacts = report["source_artifacts"]
    pages_artifacts = report["pages_artifacts"]
    live = report["live"]
    logs = report["recent_logs"]
    lines = [f"GAZA OPERATOR STATUS - {report['date']}", ""]
    lines.append("Repos")
    lines.append(
        f"- Source: {source_repo['state']} on {source_repo['branch'] or '<unknown>'}"
        + (f", upstream {source_repo['upstream']}" if source_repo["upstream"] else "")
        + (f", ahead {source_repo['ahead']}, behind {source_repo['behind']}" if source_repo["upstream"] else "")
    )
    if source_repo["summary"]["risky_entries"]:
        lines.append(f"- Source risky entries: {len(source_repo['summary']['risky_entries'])}")
    if source_repo["summary"]["allowed_entries"]:
        lines.append(f"- Source allowed local/generated entries: {len(source_repo['summary']['allowed_entries'])}")
    lines.append(
        f"- Pages: {pages_repo['state']} on {pages_repo['branch'] or '<unknown>'}"
        + (f", upstream {pages_repo['upstream']}" if pages_repo["upstream"] else "")
        + (f", ahead {pages_repo['ahead']}, behind {pages_repo['behind']}" if pages_repo["upstream"] else "")
        + (f", local {pages_repo['head_sha'] or '<none>'}" if pages_repo["head_sha"] else "")
        + (f", origin {pages_repo['origin_head_sha'] or '<none>'}" if pages_repo["origin_head_sha"] else "")
    )
    lines.append("")
    lines.append("Manual sources")
    lines.append(f"- Status: {manual['status']}")
    lines.append(f"- Records: {manual['record_count']}")
    for field, label in (
        ("traceability_note", "Traceability notes"),
        ("attribution_mode", "Attribution mode"),
        ("claim_status", "Claim status"),
    ):
        counts = manual["field_counts"][field]
        lines.append(f"- {label}: {counts['present']}/{manual['record_count']}")
    if manual["errors"]:
        lines.append(f"- Errors: {', '.join(sorted(set(manual['errors'])))}")
    if manual.get("next_action"):
        lines.append(f"- Next action: {manual['next_action']}")
    lines.append("")
    lines.append("Source artifacts")
    for label, key in (
        ("Run manifest", "run_manifest"),
        ("Dedupe report", "dedupe_report"),
        ("Source diversity report", "source_diversity_report"),
    ):
        lines.append(f"- {label}: {'present' if source_artifacts[key]['exists'] else 'missing'}")
    lines.append("")
    lines.append("Pages artifacts")
    lines.append(f"- Edition page: {'present' if pages_artifacts['edition_page']['exists'] else 'missing'}")
    lines.append(f"- Audio transcript: {'present' if pages_artifacts['audio_transcript']['exists'] else 'missing'}")
    lines.append(f"- MP3: {'present' if pages_artifacts['audio_mp3']['exists'] else 'missing'}")
    lines.append(f"- Audio index links date: {'yes' if pages_artifacts['audio_index']['links_date'] else 'no'}")
    lines.append(f"- Audio index links MP3: {'yes' if pages_artifacts['audio_index']['links_mp3'] else 'no'}")
    lines.append(f"- Podcast feeds include MP3: {'yes' if pages_artifacts['podcast_feed']['audio_podcast']['includes_mp3'] and pages_artifacts['podcast_feed']['site_podcast']['includes_mp3'] else 'no'}")
    lines.append(f"- Archive includes date: {'yes' if pages_artifacts['archive']['includes_date'] else 'no'}")
    lines.append(f"- RSS includes date: {'yes' if pages_artifacts['rss']['includes_date'] else 'no'}")
    lines.append(f"- Latest/read link includes date: {'yes' if pages_artifacts['index']['latest_link_includes_date'] else 'no'}")
    lines.append("")
    lines.append("Live")
    if not live["enabled"]:
        lines.append("- Live checks: skipped")
    else:
        lines.append(f"- Edition: {live['edition']['status_code'] or 'unknown'}")
        lines.append(f"- MP3: {live['audio_mp3']['status_code'] or 'unknown'}")
        lines.append(f"- Audio index links MP3: {'yes' if live['audio_index'].get('contains_mp3') else ('unknown' if live['audio_index']['status_code'] is None else 'no')}")
    lines.append("")
    lines.append("Recent runner")
    if logs["runner_gaza"].get("exists") or logs["gaza_daily"].get("exists"):
        merged = logs["merged_fields"]
        status = merged.get("operator_status") or merged.get("ok") or "<none>"
        lines.append(f"- Latest status: {status}")
        lines.append(f"- Email: {merged.get('email_status') or '<none>'}")
        lines.append(f"- Bluesky: {merged.get('bluesky_status') or '<none>'}")
        lines.append(f"- Pages commit: {merged.get('pages_commit_sha') or '<none>'}")
        lines.append(f"- Next action: {merged.get('next_action') or '<none>'}")
    else:
        lines.append("- Latest status: <none>")
        lines.append("- Email: <none>")
        lines.append("- Bluesky: <none>")
        lines.append("- Pages commit: <none>")
        lines.append("- Next action: <none>")
    lines.append("")
    lines.append("Next safe action")
    lines.append(f"- {report['next_action']}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Gaza operator status summary.")
    parser.add_argument("--date", required=True, help="Gaza edition date in YYYY-MM-DD format.")
    parser.add_argument("--pages-repo", default=str(ROOT / "bluefern-dispatches-pages"), help="Path to the local Pages repo.")
    parser.add_argument("--pages-branch", default="gh-pages", help="Pages branch name.")
    parser.add_argument("--no-live", action="store_true", help="Skip live public URL checks.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)
    args.date = validate_date(args.date)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = ROOT
    pages_repo = Path(args.pages_repo)
    report = build_report(root, args.date, pages_repo=pages_repo, pages_branch=args.pages_branch, live_checks=not args.no_live)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text_report(report))
    return 0 if report["overall_status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
