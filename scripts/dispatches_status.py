from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_PAGES_BRANCH = "gh-pages"
EXPECTED_CNAME = "dispatches.thebluefernco.com"
BAD_FNS_URL = "https://www.fns.usda.gov/research/snap-household-characteristics"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EDITION_LINK_RE = re.compile(r"editions/(\d{4}-\d{2}-\d{2})/")
URL_RE = re.compile(r"https?://[^\s\"'>)]+")

SOURCE_DIR_PREFIXES = (
    "src/",
    "scripts/",
    "tests/",
    "docs/",
    "assets/",
    "data/dispatches/american-pressure/",
)
GENERATED_DIR_PREFIXES = (
    "output/",
    "logs/",
    "data/records/",
    "bluefern-dispatches-pages/",
)


def run_git(repo: Path, *args: str) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={repo.resolve()}", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False, ""
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout).strip()
    return True, completed.stdout.strip()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def list_edition_dates(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted([p.name for p in base.iterdir() if p.is_dir() and DATE_RE.match(p.name)])


def latest_edition(base: Path) -> str | None:
    dates = list_edition_dates(base)
    return dates[-1] if dates else None


def count_sources(sources_manifest: Path) -> int | None:
    payload = read_json(sources_manifest)
    if isinstance(payload, list):
        return len(payload)
    return None


def extract_links(html_path: Path) -> list[str]:
    if not html_path.exists():
        return []
    return URL_RE.findall(read_text(html_path))


def extract_linked_edition_dates(*paths: Path) -> list[str]:
    linked: set[str] = set()
    for path in paths:
        text = read_text(path)
        linked.update(EDITION_LINK_RE.findall(text))
    return sorted(linked)


def git_tracking_status(repo: Path) -> str | None:
    ok, out = run_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if not ok or not out:
        return None
    ok, counts = run_git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    if not ok:
        return None
    parts = counts.split()
    if len(parts) != 2:
        return None
    behind = int(parts[0])
    ahead = int(parts[1])
    if ahead == 0 and behind == 0:
        return "up-to-date"
    return f"ahead {ahead}, behind {behind}"


def parse_git_status(repo: Path) -> dict[str, Any]:
    ok, out = run_git(repo, "status", "--short")
    rows = out.splitlines() if ok and out else []
    source_changes = []
    generated_changes = []
    for row in rows:
        path = row[3:] if len(row) > 3 else row
        normalized = path.replace("\\", "/")
        if normalized.startswith(SOURCE_DIR_PREFIXES):
            source_changes.append(normalized)
        if normalized.startswith(GENERATED_DIR_PREFIXES):
            generated_changes.append(normalized)
    return {
        "raw_rows": rows,
        "source_changes": source_changes,
        "generated_changes": generated_changes,
        "has_source_changes": bool(source_changes),
        "has_generated_changes": bool(generated_changes),
    }


def find_latest_file(base: Path, pattern: str) -> Path | None:
    paths = sorted(base.glob(pattern))
    return paths[-1] if paths else None


def summarize_pages_repo(pages_repo: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(pages_repo),
        "exists": pages_repo.exists(),
        "branch": None,
        "head_short_sha": None,
        "clean": None,
        "tracking": None,
        "cname_ok": False,
        "cname_value": None,
    }
    if not pages_repo.exists() or not (pages_repo / ".git").exists():
        return summary
    ok, branch = run_git(pages_repo, "branch", "--show-current")
    summary["branch"] = branch if ok else None
    ok, sha = run_git(pages_repo, "rev-parse", "--short", "HEAD")
    summary["head_short_sha"] = sha if ok else None
    ok, status = run_git(pages_repo, "status", "--porcelain")
    summary["clean"] = ok and status == ""
    summary["tracking"] = git_tracking_status(pages_repo)

    cname = pages_repo / "CNAME"
    if cname.exists():
        value = read_text(cname).strip()
        summary["cname_value"] = value
        summary["cname_ok"] = value == EXPECTED_CNAME
    return summary


def _check_old_project_strings(root: Path) -> list[str]:
    try:
        from scripts import doctor
    except Exception:
        return []
    result = doctor.check_old_project_runtime_strings(root)
    if result.ok:
        return []
    return [result.message]


def _scan_smtp_password_in_logs(root: Path) -> list[str]:
    logs = root / "logs"
    if not logs.exists():
        return []
    offenders = []
    for path in logs.rglob("*.log"):
        if "SMTP_PASSWORD" in read_text(path):
            offenders.append(str(path.relative_to(root)))
    return offenders


def _scan_bad_fns_in_active_ap_output(root: Path) -> list[str]:
    active_roots = [
        root / "output" / "site" / "american-pressure",
        root / "output" / "dispatches" / "american-pressure",
    ]
    offenders: list[str] = []
    for base in active_roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if BAD_FNS_URL in read_text(path):
                offenders.append(str(path.relative_to(root)))
    return offenders


def check_public_safety(root: Path) -> dict[str, Any]:
    output_site = root / "output" / "site"
    detail_exists = (output_site / "detail").exists()
    paid_exists = (output_site / "paid").exists()
    smtp_hits = _scan_smtp_password_in_logs(root)
    bad_fns_hits = _scan_bad_fns_in_active_ap_output(root)
    old_runtime_hits = _check_old_project_strings(root)
    return {
        "output_site_detail_exists": detail_exists,
        "output_site_paid_exists": paid_exists,
        "smtp_password_in_logs": smtp_hits,
        "bad_fns_link_hits": bad_fns_hits,
        "old_project_runtime_hits": old_runtime_hits,
    }


def summarize_dispatch(root: Path, pages_root: Path, slug: str, public_name: str) -> dict[str, Any]:
    site_editions = root / "output" / "site" / slug / "editions"
    pages_editions = pages_root / slug / "editions"
    dispatch_editions = root / "output" / "dispatches" / slug / "editions"

    site_latest = latest_edition(site_editions)
    pages_latest = latest_edition(pages_editions)
    archive_path = root / "output" / "site" / slug / "archive.html"
    rss_path = root / "output" / "site" / slug / "rss.xml"

    latest_sources = dispatch_editions / site_latest / "sources_manifest.json" if site_latest else None
    if latest_sources and not latest_sources.exists():
        latest_sources = site_editions / site_latest / "sources_manifest.json"

    latest_curation = site_editions / site_latest / "curation_manifest.json" if site_latest else None
    latest_manifest = site_editions / site_latest / "edition_manifest.json" if site_latest else None
    latest_index = site_editions / site_latest / "index.html" if site_latest else None

    source_count = count_sources(latest_sources) if latest_sources else None
    curation_payload = read_json(latest_curation) if latest_curation and latest_curation.exists() else None
    story_count = len(curation_payload) if isinstance(curation_payload, list) else None
    manifest_payload = read_json(latest_manifest) if latest_manifest and latest_manifest.exists() else None

    source_links_visible = bool(extract_links(latest_index)) if latest_index else False

    summary = {
        "slug": slug,
        "name": public_name,
        "latest_public_edition_date": site_latest,
        "latest_pages_edition_date": pages_latest,
        "archive_exists": archive_path.exists(),
        "rss_exists": rss_path.exists(),
        "latest_sources_manifest_path": str(latest_sources) if latest_sources else None,
        "latest_source_count": source_count,
        "latest_story_count": story_count,
        "latest_manifest_errors": manifest_payload.get("errors") if isinstance(manifest_payload, dict) else None,
        "latest_manifest_warnings": manifest_payload.get("warnings") if isinstance(manifest_payload, dict) else None,
        "latest_public_url": f"https://dispatches.thebluefernco.com/{slug}/editions/{site_latest}/" if site_latest else None,
        "latest_has_visible_source_links": source_links_visible,
    }
    return summary


def summarize_gaza(root: Path, pages_root: Path) -> dict[str, Any]:
    result = summarize_dispatch(root, pages_root, "gaza", "Dispatches From Gaza")
    site_editions = root / "output" / "site" / "gaza" / "editions"
    site_dates = list_edition_dates(site_editions)
    archive_path = root / "output" / "site" / "gaza" / "archive.html"
    rss_path = root / "output" / "site" / "gaza" / "rss.xml"
    linked_dates = extract_linked_edition_dates(archive_path, rss_path)
    public_dates = [d for d in linked_dates if d in set(site_dates)]
    latest_public = public_dates[-1] if public_dates else None
    stale_or_unlinked = sorted([d for d in site_dates if d not in set(public_dates)])

    result["public_archive_dates"] = public_dates
    result["latest_public_edition_date"] = latest_public
    result["latest_public_url"] = (
        f"https://dispatches.thebluefernco.com/gaza/editions/{latest_public}/" if latest_public else None
    )
    result["stale_or_unlinked_edition_dates"] = stale_or_unlinked

    latest = latest_public
    if latest:
        latest_sources = root / "output" / "dispatches" / "gaza" / "editions" / latest / "sources_manifest.json"
        if not latest_sources.exists():
            latest_sources = root / "output" / "site" / "gaza" / "editions" / latest / "sources_manifest.json"
        latest_curation = root / "output" / "site" / "gaza" / "editions" / latest / "curation_manifest.json"
        latest_manifest = root / "output" / "site" / "gaza" / "editions" / latest / "edition_manifest.json"
        latest_index = root / "output" / "site" / "gaza" / "editions" / latest / "index.html"
        result["latest_sources_manifest_path"] = str(latest_sources) if latest_sources.exists() else None
        result["latest_source_count"] = count_sources(latest_sources) if latest_sources.exists() else None
        curation_payload = read_json(latest_curation) if latest_curation.exists() else None
        manifest_payload = read_json(latest_manifest) if latest_manifest.exists() else None
        result["latest_story_count"] = len(curation_payload) if isinstance(curation_payload, list) else None
        result["latest_manifest_errors"] = (
            manifest_payload.get("errors") if isinstance(manifest_payload, dict) else None
        )
        result["latest_manifest_warnings"] = (
            manifest_payload.get("warnings") if isinstance(manifest_payload, dict) else None
        )
        result["latest_has_visible_source_links"] = bool(extract_links(latest_index)) if latest_index.exists() else False
    else:
        result["latest_sources_manifest_path"] = None
        result["latest_source_count"] = None
        result["latest_story_count"] = None
        result["latest_manifest_errors"] = None
        result["latest_manifest_warnings"] = None
        result["latest_has_visible_source_links"] = False

    dedupe_summary = None
    suppressed_dates: list[str] = []
    if latest:
        dedupe_path = find_latest_file(root / "output" / "dispatches" / "gaza" / "editions", "*/dedupe_report.json")
        if dedupe_path:
            payload = read_json(dedupe_path)
            if isinstance(payload, dict):
                input_count = payload.get("input_candidate_count", payload.get("candidates_seen"))
                kept = payload.get("kept_candidate_count")
                if kept is None and isinstance(payload.get("included_stories"), list):
                    kept = len(payload["included_stories"])
                suppressed = payload.get("suppressed_candidate_count")
                if suppressed is None and isinstance(payload.get("duplicate_skipped"), list):
                    suppressed = len(payload["duplicate_skipped"])
                dedupe_summary = {
                    "edition_date": payload.get("edition_date", dedupe_path.parent.name),
                    "input_candidate_count": input_count,
                    "kept_candidate_count": kept,
                    "suppressed_candidate_count": suppressed,
                    "warnings": payload.get("warnings", []),
                }
                if suppressed and int(suppressed) > 0:
                    suppressed_dates.append(str(payload.get("edition_date", dedupe_path.parent.name)))
    result["suppressed_duplicate_dates"] = sorted(set(suppressed_dates))
    result["latest_dedupe_report"] = dedupe_summary

    # linked edition health checks
    zero_source_linked: list[str] = []
    zero_story_linked: list[str] = []
    dedupe_refusal_linked: list[str] = []
    for d in public_dates:
        manifest = read_json(site_editions / d / "edition_manifest.json")
        sources_payload = read_json(site_editions / d / "sources_manifest.json")
        curation_payload = read_json(site_editions / d / "curation_manifest.json")
        source_count = len(sources_payload) if isinstance(sources_payload, list) else int((manifest or {}).get("source_count", 0) or 0)
        story_count = len(curation_payload) if isinstance(curation_payload, list) else int((manifest or {}).get("story_count", 0) or 0)
        if source_count == 0:
            zero_source_linked.append(d)
        if story_count == 0:
            zero_story_linked.append(d)
        errors = manifest.get("errors") if isinstance(manifest, dict) else []
        if isinstance(errors, list) and any("No new source-backed Gaza developments after cross-edition dedupe" in str(e) for e in errors):
            dedupe_refusal_linked.append(d)
    result["public_linked_zero_source_dates"] = sorted(zero_source_linked)
    result["public_linked_zero_story_dates"] = sorted(zero_story_linked)
    result["public_linked_dedupe_refusal_dates"] = sorted(dedupe_refusal_linked)

    # repeated URLs across recent editions
    recent = sorted(public_dates)[-5:]
    url_to_dates: dict[str, set[str]] = {}
    for d in recent:
        sources_payload = read_json(root / "output" / "site" / "gaza" / "editions" / d / "sources_manifest.json")
        if isinstance(sources_payload, list):
            for src in sources_payload:
                if isinstance(src, dict):
                    url = str(src.get("url") or "").strip()
                    if url:
                        url_to_dates.setdefault(url, set()).add(d)
    repeats = {url: sorted(list(dates)) for url, dates in url_to_dates.items() if len(dates) > 1}
    result["repeated_source_urls_recent"] = repeats
    return result


def summarize_cascadia(root: Path, pages_root: Path) -> dict[str, Any]:
    result = summarize_dispatch(root, pages_root, "cascadia", "The Cascadia Briefing")
    archive = read_text(root / "output" / "site" / "cascadia" / "archive.html")
    weekly_only = "Daily" not in archive and "daily" not in archive
    transitional_dates = {"2026-05-04", "2026-05-05"}
    linked = set(EDITION_LINK_RE.findall(archive + "\n" + read_text(root / "output" / "site" / "cascadia" / "index.html")))
    transitional_linked = sorted(linked & transitional_dates)
    result["weekly_labels_only"] = weekly_only
    result["transitional_public_links"] = transitional_linked
    result["latest_weekly_edition_date"] = result.get("latest_public_edition_date")

    gap_path = find_latest_file(root / "output" / "dispatches" / "cascadia" / "weekly_gap_reports", "*.json")
    gap_summary = None
    if gap_path:
        payload = read_json(gap_path)
        if isinstance(payload, dict):
            gap_summary = {
                "path": str(gap_path),
                "source_checks_attempted": payload.get("source_checks_attempted"),
                "source_checks_successful": payload.get("source_checks_successful"),
                "successful_fetch_rate": payload.get("successful_fetch_rate"),
                "final_public_story_count": payload.get("final_public_story_count"),
                "final_zero_story_result_is_credible": payload.get("final_zero_story_result_is_credible"),
            }
    result["latest_weekly_gap_report"] = gap_summary
    return result


def _parse_simple_registry_yaml(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                current[k.strip()] = v.strip().strip('"\'')
            continue
        if current is None or ":" not in stripped:
            continue
        k, v = stripped.split(":", 1)
        raw = v.strip().strip('"\'')
        if raw.lower() == "true":
            current[k.strip()] = True
        elif raw.lower() == "false":
            current[k.strip()] = False
        else:
            current[k.strip()] = raw
    if current:
        entries.append(current)
    return entries


def summarize_american_pressure(root: Path, pages_root: Path) -> dict[str, Any]:
    result = summarize_dispatch(root, pages_root, "american-pressure", "The American Pressure Dispatch")
    registry = root / "data" / "dispatches" / "american-pressure" / "source_registry.yml"
    result["source_registry_exists"] = registry.exists()

    registry_entries = _parse_simple_registry_yaml(registry) if registry.exists() else []
    enabled = [entry for entry in registry_entries if entry.get("enabled") is True]
    by_pillar: dict[str, int] = {}
    for entry in enabled:
        pillar = str(entry.get("pillar") or "unknown")
        by_pillar[pillar] = by_pillar.get(pillar, 0) + 1
    result["registry_summary"] = {
        "total_sources": len(registry_entries),
        "enabled_sources": len(enabled),
        "enabled_by_pillar": by_pillar,
    }

    health_path = find_latest_file(root / "output" / "dispatches" / "american-pressure" / "source_health", "*.json")
    result["latest_source_health_report"] = str(health_path) if health_path else None

    manual_root = root / "data" / "dispatches" / "american-pressure" / "sources"
    manual_dates = sorted([p.name for p in manual_root.iterdir() if p.is_dir() and DATE_RE.match(p.name)]) if manual_root.exists() else []
    latest_manual_date = manual_dates[-1] if manual_dates else None
    result["latest_manual_source_date"] = latest_manual_date

    latest_pub = result.get("latest_public_edition_date")
    latest_manual_for_public = bool(latest_pub and (manual_root / latest_pub / "manual_sources.json").exists())
    result["latest_manual_source_exists_for_latest_public_edition"] = latest_manual_for_public

    runner = read_text(root / "scripts" / "run_american_pressure_dispatch.py")
    result["live_fetch_disabled_by_default"] = "live_fetch_enabled\": False" in runner

    result["latest_public_source_count_gt_zero"] = bool((result.get("latest_source_count") or 0) > 0)
    result["bad_fns_hits_in_active_output"] = _scan_bad_fns_in_active_ap_output(root)
    return result


def run_doctor(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "doctor.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": completed.stdout,
    }


def build_status(root: Path, pages_repo: Path, run_doctor_flag: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ok_branch, branch = run_git(root, "branch", "--show-current")
    ok_sha, sha = run_git(root, "rev-parse", "--short", "HEAD")
    project_git = parse_git_status(root)

    project = {
        "root": str(root),
        "branch": branch if ok_branch else None,
        "head_short_sha": sha if ok_sha else None,
        "tracking": git_tracking_status(root),
        "has_source_test_doc_changes": project_git["has_source_changes"],
        "source_change_paths": project_git["source_changes"],
        "has_generated_runtime_dirt": project_git["has_generated_changes"],
        "generated_change_paths": project_git["generated_changes"],
        "python": sys.executable,
        "timestamp": now,
    }

    pages = summarize_pages_repo(pages_repo)
    safety = check_public_safety(root)

    gaza = summarize_gaza(root, pages_repo)
    cascadia = summarize_cascadia(root, pages_repo)
    american = summarize_american_pressure(root, pages_repo)

    critical_errors: list[str] = []
    warnings: list[str] = []

    if safety["output_site_detail_exists"]:
        critical_errors.append("output/site/detail exists")
    if safety["output_site_paid_exists"]:
        critical_errors.append("output/site/paid exists")
    if safety["smtp_password_in_logs"]:
        critical_errors.append("SMTP_PASSWORD appears in logs")
    if not pages.get("exists"):
        critical_errors.append("Pages repo missing")
    elif pages.get("branch") != EXPECTED_PAGES_BRANCH:
        critical_errors.append("Pages repo is not on gh-pages")
    if not pages.get("cname_ok"):
        critical_errors.append("Pages CNAME missing or incorrect")
    if american["bad_fns_hits_in_active_output"]:
        critical_errors.append("bad FNS link appears in active American Pressure output")
    if gaza.get("repeated_source_urls_recent"):
        critical_errors.append("Gaza duplicate public edition detected")
    if gaza.get("public_linked_zero_source_dates"):
        critical_errors.append("Gaza linked public edition has zero sources")
    if gaza.get("public_linked_zero_story_dates"):
        critical_errors.append("Gaza linked public edition has zero stories")
    if gaza.get("public_linked_dedupe_refusal_dates"):
        critical_errors.append("Gaza linked public edition has dedupe-refusal errors")
    if cascadia.get("transitional_public_links"):
        critical_errors.append("Cascadia transitional dates publicly linked")
    if not gaza.get("latest_has_visible_source_links"):
        critical_errors.append("latest public Gaza edition missing source links")

    recommendations = []
    if pages.get("clean") and pages.get("tracking") and str(pages.get("tracking", "")).startswith("ahead"):
        recommendations.append("Review local Pages output and push gh-pages if satisfied.")
    if project["has_source_test_doc_changes"]:
        recommendations.append("Review and commit source/test/doc files only.")
    if not project["has_source_test_doc_changes"] and project["has_generated_runtime_dirt"]:
        recommendations.append("No source commit needed.")
    if gaza.get("repeated_source_urls_recent"):
        recommendations.append("Run Gaza replacement/dedupe workflow before publishing.")
    if not american.get("latest_manual_source_exists_for_latest_public_edition"):
        recommendations.append("Prepare manual_sources.json before weekly run.")

    doctor = None
    if run_doctor_flag:
        doctor = run_doctor(root)
        if not doctor["ok"]:
            recommendations.append("Fix doctor errors before publishing.")

    return {
        "ok": not critical_errors,
        "critical_errors": critical_errors,
        "warnings": warnings,
        "project": project,
        "pages_repo": pages,
        "public_safety": safety,
        "dispatches": {
            "gaza": gaza,
            "cascadia": cascadia,
            "american_pressure": american,
        },
        "doctor": doctor,
        "recommendations": recommendations,
    }


def apply_strict_failures(status: dict[str, Any]) -> list[str]:
    strict_errors: list[str] = []
    project = status["project"]
    pages = status["pages_repo"]
    american = status["dispatches"]["american_pressure"]
    if project["has_source_test_doc_changes"]:
        strict_errors.append("source working tree contains source/test/doc changes")
    tracking = pages.get("tracking")
    if isinstance(tracking, str) and tracking.startswith("ahead"):
        strict_errors.append("Pages repo is ahead of origin/gh-pages")
    if not american.get("latest_manual_source_exists_for_latest_public_edition"):
        strict_errors.append("latest American Pressure manual source file missing")
    return strict_errors


def render_text_status(status: dict[str, Any], strict_errors: list[str]) -> str:
    lines = []
    lines.append("Dispatches Status")
    lines.append(f"OK: {status['ok'] and not strict_errors}")
    if status["critical_errors"]:
        lines.append("Critical:")
        for e in status["critical_errors"]:
            lines.append(f"- {e}")
    if strict_errors:
        lines.append("Strict Failures:")
        for e in strict_errors:
            lines.append(f"- {e}")

    lines.append("Project:")
    p = status["project"]
    lines.append(f"- root: {p['root']}")
    lines.append(f"- branch: {p['branch']} @ {p['head_short_sha']}")
    lines.append(f"- tracking: {p['tracking']}")
    lines.append(f"- source changes: {p['has_source_test_doc_changes']}")
    lines.append(f"- generated/runtime dirt: {p['has_generated_runtime_dirt']}")
    lines.append(f"- python: {p['python']}")
    lines.append(f"- timestamp: {p['timestamp']}")

    pages = status["pages_repo"]
    lines.append("Pages Repo:")
    lines.append(f"- path: {pages['path']}")
    lines.append(f"- exists: {pages['exists']}")
    lines.append(f"- branch/head: {pages['branch']} @ {pages['head_short_sha']}")
    lines.append(f"- clean: {pages['clean']}")
    lines.append(f"- tracking: {pages['tracking']}")
    lines.append(f"- CNAME ok: {pages['cname_ok']} ({pages['cname_value']})")

    for key, label in (("gaza", "Gaza"), ("cascadia", "Cascadia"), ("american_pressure", "American Pressure")):
        d = status["dispatches"][key]
        lines.append(f"{label}:")
        lines.append(f"- latest public/pages: {d.get('latest_public_edition_date')} / {d.get('latest_pages_edition_date')}")
        lines.append(f"- archive/rss: {d.get('archive_exists')} / {d.get('rss_exists')}")
        lines.append(f"- latest source/story count: {d.get('latest_source_count')} / {d.get('latest_story_count')}")
        lines.append(f"- latest public URL: {d.get('latest_public_url')}")
        lines.append(f"- visible source links in latest index: {d.get('latest_has_visible_source_links')}")

    if status.get("doctor") is not None:
        lines.append(f"Doctor: {'ok' if status['doctor']['ok'] else 'fail'}")

    if status["recommendations"]:
        lines.append("Recommendations:")
        for rec in status["recommendations"]:
            lines.append(f"- {rec}")

    return "\n".join(lines)


def make_default_report_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return root / "output" / "dispatches" / "status" / f"dispatches_status_{stamp}.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local operations dashboard/status for Dispatches From The Blue Fern Co.")
    parser.add_argument("--json", action="store_true", dest="json_mode")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--pages-repo", type=Path)
    parser.add_argument("--run-doctor", action="store_true")
    parser.add_argument("--run-tests", action="store_true", help="Opt-in; currently reports unsupported and does not execute tests.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    pages_repo = (args.pages_repo or (root / "bluefern-dispatches-pages")).resolve()

    status = build_status(root, pages_repo, run_doctor_flag=args.run_doctor)
    strict_errors = apply_strict_failures(status) if args.strict else []

    report_path = None
    if args.write_report:
        report_path = (args.report_path or make_default_report_path(root)).resolve()
        if (root / "output" / "site") in report_path.parents:
            print("Refusing to write report under output/site", file=sys.stderr)
            return 1
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    if args.json_mode:
        print(json.dumps(status, indent=2))
    else:
        if args.run_tests:
            print("--run-tests requested but execution is intentionally disabled in this status script.")
        print(render_text_status(status, strict_errors))
        if report_path:
            print(f"Report written: {report_path}")

    failed = bool(status["critical_errors"]) or bool(strict_errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
