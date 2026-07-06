from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.generator import pages_sync_repair_message
from scripts.validation_profiles import (
    PROFILE_GAZA_DAILY,
    apply_env_profile,
    get_profile,
    pytest_command,
)


DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
DEFAULT_REMOTE_URL = "https://github.com/RedGarland/the-blue-fern-co-dispatches.git"
DEFAULT_PAGES_BRANCH = "gh-pages"
BASE_PUBLIC_URL = "https://dispatches.thebluefernco.com"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_SOURCE_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
}


class LinkTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_by_name = {name.lower(): value for name, value in attrs}
        href = attrs_by_name.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, indent=2))


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def source_file_for(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def load_source_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manual_sources.json must be a list or an object with a sources list")
    return [record for record in records if isinstance(record, dict)]


def valid_source_errors(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
        url = str(record.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
    return errors


def run_command(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    output = (result.stdout or "").strip()
    if not output:
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})\s*$", output, flags=re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


def command_text(args: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in args)


def generation_command(edition_date: str, dry_run: bool) -> list[str]:
    args = [
        sys.executable,
        "scripts\\run_gaza_dispatch.py",
        "--date",
        edition_date,
        "--historical",
        "--from-manual-sources",
        "--all",
    ]
    if dry_run:
        args.append("--dry-run")
    return args


def pages_publish_command(
    pages_repo: Path,
    remote_url: str,
    pages_branch: str,
    edition_date: str,
    dry_run: bool,
    only_dispatches: tuple[str, ...] = ("gaza",),
    allow_listing_shrink: bool = False,
) -> list[str]:
    args = [sys.executable, "scripts\\publish_github_pages.py"]
    if dry_run:
        args.append("--dry-run")
    args.extend([
        "--pages-repo",
        str(pages_repo),
        "--pages-branch",
        pages_branch,
        "--expect-date",
        edition_date,
        "--expect-dispatch",
        "gaza",
    ])
    for dispatch in only_dispatches:
        args.extend(["--only-dispatch", dispatch])
    if not dry_run:
        args.extend(["--remote-url", remote_url, "--commit", "--no-push"])
    if allow_listing_shrink:
        args.append("--allow-listing-shrink")
    return args


def manual_push_command(pages_repo: Path, pages_branch: str) -> str:
    return f'cd "{pages_repo}"\ngit status\ngit push origin {pages_branch}'


def edition_dir(edition_date: str) -> Path:
    return ROOT / "output" / "site" / "gaza" / "editions" / edition_date


def required_output_paths(edition_date: str) -> list[Path]:
    base = edition_dir(edition_date)
    return [
        base / "index.html",
        base / "edition_manifest.json",
        base / "sources_manifest.json",
        base / "curation_manifest.json",
    ]


def visible_source_links(html_text: str) -> list[tuple[str, str]]:
    parser = LinkTextParser()
    parser.feed(html_text)
    return [
        (href, text)
        for href, text in parser.links
        if href.startswith(("http://", "https://")) and text and re.search(r"source|reuters|agency|news|times|un|publisher", html_text, re.I)
    ]


def validate_generated_output(edition_date: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = [str(path) for path in required_output_paths(edition_date) if not path.exists()]
    if missing:
        errors.extend(f"missing generated output: {path}" for path in missing)
        return {
            "archive_updated": False,
            "rss_updated": False,
            "source_count": 0,
            "public_story_count": 0,
            "warnings": warnings,
            "errors": errors,
        }

    archive = ROOT / "output" / "site" / "gaza" / "archive.html"
    rss = ROOT / "output" / "site" / "gaza" / "rss.xml"
    archive_updated = archive.exists() and edition_date in archive.read_text(encoding="utf-8")
    rss_updated = rss.exists() and edition_date in rss.read_text(encoding="utf-8")
    if not archive_updated:
        errors.append(f"output/site/gaza/archive.html does not contain {edition_date}")
    if not rss_updated:
        errors.append(f"output/site/gaza/rss.xml does not contain {edition_date}")

    sources = read_json(edition_dir(edition_date) / "sources_manifest.json")
    curation = read_json(edition_dir(edition_date) / "curation_manifest.json")
    if not isinstance(sources, list):
        errors.append("sources_manifest.json must contain a list")
        sources = []
    if not isinstance(curation, list):
        errors.append("curation_manifest.json must contain a list")
        curation = []

    source_ids = {
        str(source.get("source_record_id") or source.get("source_id"))
        for source in sources
        if isinstance(source, dict) and (source.get("source_record_id") or source.get("source_id"))
    }
    if not source_ids:
        errors.append("sources_manifest.json has no source records")

    public_story_count = 0
    for story in curation:
        if not isinstance(story, dict):
            errors.append("curation_manifest.json contains a non-object story record")
            continue
        if story.get("included_in_public_summary") is False:
            continue
        public_story_count += 1
        story_source_ids = story.get("source_ids") or story.get("source_record_ids") or []
        if not story_source_ids:
            errors.append(f"{story.get('story_id', 'unknown story')} is public but has no source IDs")
            continue
        missing_ids = [str(source_id) for source_id in story_source_ids if str(source_id) not in source_ids]
        if missing_ids:
            errors.append(f"{story.get('story_id', 'unknown story')} references missing source IDs: {', '.join(missing_ids)}")
    if public_story_count == 0:
        errors.append("curation_manifest.json has no public stories")

    html_text = (edition_dir(edition_date) / "index.html").read_text(encoding="utf-8")
    if "Sources" not in html_text and "Source" not in html_text:
        errors.append("rendered HTML does not include visible source labeling")
    if not visible_source_links(html_text):
        errors.append("rendered HTML contains no visible public source links")

    return {
        "archive_updated": archive_updated,
        "rss_updated": rss_updated,
        "source_count": len(source_ids),
        "public_story_count": public_story_count,
        "warnings": warnings,
        "errors": errors,
    }


def validate_public_site_has_no_detail_files() -> list[str]:
    site_root = ROOT / "output" / "site"
    if not site_root.exists():
        return []
    errors: list[str] = []
    for path in site_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(site_root)
        if relative.parts and relative.parts[0] in {"detail", "paid"}:
            errors.append(f"paid/detail file is in public output: {path}")
    return errors


def validate_pages_outputs(pages_repo: Path, edition_date: str) -> list[str]:
    required = [
        pages_repo / "gaza" / "archive.html",
        pages_repo / "gaza" / "rss.xml",
        pages_repo / "gaza" / "editions" / edition_date / "index.html",
    ]
    return [f"missing Pages repo output: {path}" for path in required if not path.exists()]


def make_local_pytest_basetemp(prefix: str = ".pytest-temp-gaza-publish") -> Path:
    path = ROOT / f"{prefix}-{os.getpid()}-{int(time.time() * 1000)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_tests(validation_profile: str, pytest_basetemp: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    cmd = pytest_command(validation_profile, pytest_basetemp)
    return run_command(cmd), cmd


def push_pages_repo(pages_repo: Path, pages_branch: str) -> tuple[bool, list[str], str]:
    messages: list[str] = []
    status = run_command(["git", "status"], cwd=pages_repo)
    messages.append(status.stdout.strip() or status.stderr.strip())
    if status.returncode != 0:
        return False, messages, ""
    push = run_command(["git", "push", "origin", pages_branch], cwd=pages_repo)
    messages.append(push.stdout.strip() or push.stderr.strip())
    if push.returncode != 0:
        detail = push.stdout.strip() or push.stderr.strip() or f"git push origin {pages_branch} failed"
        lower = detail.lower()
        if any(token in lower for token in ("non-fast-forward", "fetch first", "rejected", "update your local branch")):
            detail = f"{detail}\n{pages_sync_repair_message(pages_repo, pages_branch)}"
        return False, messages, detail
    return True, messages, push.stdout.strip() or push.stderr.strip()


def open_local_edition(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        run_command(["open", str(path)])


def initial_summary(args: argparse.Namespace, source_file: Path) -> dict[str, Any]:
    public_urls = {
        "archive": f"{BASE_PUBLIC_URL}/gaza/archive.html",
        "edition": f"{BASE_PUBLIC_URL}/gaza/editions/{args.date}/",
    }
    local_paths = {
        "source_file": str(source_file),
        "edition": str(edition_dir(args.date) / "index.html"),
        "archive": str(ROOT / "output" / "site" / "gaza" / "archive.html"),
        "rss": str(ROOT / "output" / "site" / "gaza" / "rss.xml"),
        "pages_repo": str(Path(args.pages_repo)),
    }
    return {
        "ok": False,
        "date": args.date,
        "source_file": str(source_file),
        "generated": False,
        "tests_run": False,
        "tests_ok": None,
        "archive_updated": False,
        "rss_updated": False,
        "source_count": 0,
        "public_story_count": 0,
        "pages_repo_updated": False,
        "pages_commit_sha": None,
        "pushed": False,
        "public_urls": public_urls,
        "local_paths": local_paths,
        "warnings": [],
        "errors": [],
        "target_pages_branch": args.pages_branch,
        "checked_out_branch": None,
        "committed_branch": None,
        "would_push": bool(args.push),
        "manual_push_command": manual_push_command(Path(args.pages_repo), args.pages_branch),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and publish a historical Gaza edition from project-local source records.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repo path.")
    parser.add_argument("--remote-url", default=DEFAULT_REMOTE_URL, help="Pages repo remote URL for local commit metadata.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Git branch GitHub Pages deploys from.")
    parser.add_argument(
        "--validation-profile",
        default=PROFILE_GAZA_DAILY,
        help="Validation profile to run before Pages publishing.",
    )
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest before Pages publishing.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions without updating, committing, or pushing the Pages repo.")
    parser.add_argument("--push", action="store_true", help="Push the Pages repo to origin gh-pages after local publishing succeeds.")
    parser.add_argument("--open-local", action="store_true", help="Open the rendered local edition after successful validation.")
    parser.add_argument("--allow-empty-sources", action="store_true", help="Allow an empty manual source file. Publishing still requires traceable rendered stories.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.validation_profile = apply_env_profile(args.validation_profile)
    try:
        profile = get_profile(args.validation_profile)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)], "validation_profile": args.validation_profile}, indent=2))
        return 1
    args.date = validate_date(args.date)
    args.pages_repo = str(Path(args.pages_repo))
    pages_repo = Path(args.pages_repo)
    source_file = source_file_for(args.date)
    summary = initial_summary(args, source_file)
    summary["validation_profile"] = profile.name
    summary["skipped_unrelated_tests"] = profile.skipped_unrelated_tests
    pytest_basetemp = make_local_pytest_basetemp()

    if not source_file.exists():
        summary["errors"].append(f"Create the source file first at {source_file}.")
        write_summary(summary)
        return 1

    try:
        manual_records = load_source_records(source_file)
    except Exception as exc:
        summary["errors"].append(str(exc))
        write_summary(summary)
        return 1

    source_errors = valid_source_errors(manual_records)
    if source_errors:
        summary["errors"].extend(source_errors)
        write_summary(summary)
        return 1
    if not manual_records and not args.allow_empty_sources:
        summary["errors"].append("manual_sources.json contains no valid source records")
        write_summary(summary)
        return 1
    summary["source_count"] = len(manual_records)

    planned = [
        command_text(generation_command(args.date, dry_run=args.dry_run)),
        command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=True)),
    ]
    if not args.dry_run:
        planned.append(command_text(pytest_command(args.validation_profile, pytest_basetemp)))
        planned.append(command_text(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=False)))
    if args.push:
        planned.append("git status")
        planned.append(f"git push origin {args.pages_branch}")
    summary["planned_actions"] = planned

    generation = run_command(generation_command(args.date, dry_run=args.dry_run))
    if generation.returncode != 0:
        summary["errors"].append(generation.stderr.strip() or generation.stdout.strip() or "historical Gaza generation failed")
        write_summary(summary)
        return 1
    summary["generated"] = not args.dry_run

    if not args.dry_run or all(path.exists() for path in required_output_paths(args.date)):
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
    else:
        summary["warnings"].append("dry run did not write generated output; skipped rendered-output validation")

    summary["errors"].extend(validate_public_site_has_no_detail_files())
    if summary["errors"]:
        write_summary(summary)
        return 1

    if not args.skip_tests and not args.dry_run:
        summary["tests_run"] = True
        tests, tests_cmd = run_tests(args.validation_profile, pytest_basetemp)
        summary["tests_command"] = subprocess.list2cmdline(tests_cmd)
        summary["tests_ok"] = tests.returncode == 0
        if tests.returncode != 0:
            summary["errors"].append(tests.stdout.strip() or tests.stderr.strip() or "tests failed")
            write_summary(summary)
            return 1
    elif args.skip_tests:
        summary["warnings"].append("tests skipped by --skip-tests")
        summary["tests_ok"] = None

    pages_dry_run = run_command(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=True))
    if pages_dry_run.returncode != 0:
        summary["errors"].append(pages_dry_run.stderr.strip() or pages_dry_run.stdout.strip() or "Pages publish dry-run failed")
        write_summary(summary)
        return 1
    try:
        pages_dry_run_payload = parse_json_stdout(pages_dry_run)
    except Exception as exc:
        summary["errors"].append(f"could not parse Pages dry-run JSON: {exc}")
        write_summary(summary)
        return 1
    if pages_dry_run_payload.get("ok") is not True:
        summary["errors"].append("Pages publish dry-run did not report ok: true")
    if pages_dry_run_payload.get("target_pages_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages publish dry-run targeted {pages_dry_run_payload.get('target_pages_branch')}, expected {args.pages_branch}")
    if pages_dry_run_payload.get("errors") not in ([], None):
        summary["errors"].append(f"Pages publish dry-run reported errors: {pages_dry_run_payload.get('errors')}")
    if pages_dry_run_payload.get("paid_detail_excluded_from_public") is not True:
        summary["errors"].append("Pages publish dry-run did not confirm paid/detail exclusion")
    if summary["errors"]:
        write_summary(summary)
        return 1

    if args.dry_run:
        summary["ok"] = True
        write_summary(summary)
        return 0

    pages_publish = run_command(pages_publish_command(pages_repo, args.remote_url, args.pages_branch, args.date, dry_run=False))
    if pages_publish.returncode != 0:
        summary["errors"].append(pages_publish.stderr.strip() or pages_publish.stdout.strip() or "Pages publish failed")
        write_summary(summary)
        return 1
    try:
        pages_payload = parse_json_stdout(pages_publish)
    except Exception as exc:
        summary["errors"].append(f"could not parse Pages publish JSON: {exc}")
        write_summary(summary)
        return 1
    if pages_payload.get("ok") is not True:
        summary["errors"].append("Pages publish did not report ok: true")
    if pages_payload.get("target_pages_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages publish targeted {pages_payload.get('target_pages_branch')}, expected {args.pages_branch}")
    if pages_payload.get("committed_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages commit targeted {pages_payload.get('committed_branch')}, expected {args.pages_branch}")
    summary["pages_repo_updated"] = bool(pages_payload.get("copied"))
    summary["pages_commit_sha"] = pages_payload.get("commit_sha")
    summary["target_pages_branch"] = pages_payload.get("target_pages_branch", args.pages_branch)
    summary["checked_out_branch"] = pages_payload.get("checked_out_branch")
    summary["committed_branch"] = pages_payload.get("committed_branch")
    summary["errors"].extend(validate_pages_outputs(pages_repo, args.date))
    if summary["errors"]:
        write_summary(summary)
        return 1

    if args.push:
        pushed, messages, push_result = push_pages_repo(pages_repo, args.pages_branch)
        summary["push_output"] = messages
        summary["pushed"] = pushed
        if not pushed:
            summary["errors"].append(push_result or f"git push origin {args.pages_branch} failed")
            write_summary(summary)
            return 1

    if args.open_local:
        open_local_edition(edition_dir(args.date) / "index.html")

    summary["ok"] = True
    write_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
