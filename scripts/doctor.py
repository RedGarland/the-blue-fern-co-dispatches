from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


EXPECTED_CNAME = "dispatches.thebluefernco.com"
EXPECTED_PAGES_BRANCH = "gh-pages"
KNOWN_CASCADIA_TRANSITIONAL_DAILY_DATES = {
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-05-07",
    "2026-05-08",
}
OLD_PROJECT_NEEDLES = (
    "fda_media_pipeline",
    "FDA media pipeline",
    "FDA/Cascadia media pipeline",
    "old Gaza project",
)
RUNTIME_PATTERNS = ("*.py", "*.ps1")
LINKED_EDITION_RE = re.compile(r"editions/(\d{4}-\d{2}-\d{2})/")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-16")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")


def _load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _result(name: str, ok: bool, message: str) -> CheckResult:
    return CheckResult(name=name, ok=ok, message=message)


def check_project_venv(root: Path) -> CheckResult:
    venv = root / ".venv"
    return _result("project .venv", venv.is_dir(), f"{venv} exists" if venv.is_dir() else f"{venv} is missing")


def check_required_folders(root: Path) -> CheckResult:
    required = [
        "assets",
        "data/dispatches/gaza",
        "data/dispatches/cascadia",
        "docs",
        "logs",
        "output/site",
        "scripts",
        "src/bluefern_dispatches",
        "tests",
    ]
    missing = [folder for folder in required if not (root / folder).is_dir()]
    return _result("required folders", not missing, "all required folders exist" if not missing else f"missing: {', '.join(missing)}")


def check_old_project_runtime_strings(root: Path) -> CheckResult:
    roots = [root / "scripts", root / "src"]
    matches: list[str] = []
    for search_root in roots:
        if not search_root.exists():
            continue
        for pattern in RUNTIME_PATTERNS:
            for path in search_root.rglob(pattern):
                if "__pycache__" in path.parts:
                    continue
                if path == Path(__file__).resolve():
                    continue
                text = _read_text(path)
                for needle in OLD_PROJECT_NEEDLES:
                    if needle in text:
                        matches.append(f"{path.relative_to(root)} contains {needle!r}")
    return _result("old project runtime strings", not matches, "no old project path/dependency strings found" if not matches else "; ".join(matches))


def check_scheduled_tasks_use_project_venv(root: Path) -> CheckResult:
    ops = root / "ops"
    if not ops.exists():
        return _result("scheduled task .venv", True, "ops folder is absent; scheduled task check skipped")
    task_files = sorted(ops.glob("*.xml"))
    problems: list[str] = []
    for path in task_files:
        text = _read_text(path)
        if "run_and_notify.py" in text and ".venv\\Scripts\\python.exe" not in text and ".venv/Scripts/python.exe" not in text:
            problems.append(f"{path.relative_to(root)} does not use project .venv Python")
    if not task_files:
        return _result("scheduled task .venv", True, "no scheduled task XML files found; scheduled task check skipped")
    return _result("scheduled task .venv", not problems, "scheduled task templates use project .venv" if not problems else "; ".join(problems))


def check_no_public_detail_or_paid(root: Path) -> CheckResult:
    site = root / "output" / "site"
    blocked = [child for child in (site / "detail", site / "paid") if child.exists()]
    return _result("public detail/paid exclusion", not blocked, "output/site has no detail or paid folders" if not blocked else f"blocked folders: {', '.join(str(p) for p in blocked)}")


def _linked_cascadia_dates(root: Path) -> dict[str, set[str]]:
    cascadia = root / "output" / "site" / "cascadia"
    files = {
        "archive": cascadia / "archive.html",
        "recent": cascadia / "index.html",
        "rss": cascadia / "rss.xml",
    }
    linked: dict[str, set[str]] = {}
    for label, path in files.items():
        if path.exists():
            linked[label] = set(LINKED_EDITION_RE.findall(_read_text(path)))
        else:
            linked[label] = set()
    return linked


def _manifest_for(root: Path, edition_date: str) -> dict[str, object] | None:
    path = root / "output" / "site" / "cascadia" / "editions" / edition_date / "edition_manifest.json"
    if not path.exists():
        return None
    loaded = _load_json(path)
    return loaded if isinstance(loaded, dict) else None


def check_cascadia_transitional_dates_excluded(root: Path) -> CheckResult:
    linked = _linked_cascadia_dates(root)
    offenders = sorted(
        f"{section}:{edition_date}"
        for section, dates in linked.items()
        for edition_date in dates & KNOWN_CASCADIA_TRANSITIONAL_DAILY_DATES
    )
    return _result("Cascadia transitional dates excluded", not offenders, "known transitional daily dates are not linked publicly" if not offenders else f"linked transitional dates: {', '.join(offenders)}")


def check_cascadia_weekly_links(root: Path) -> CheckResult:
    linked = _linked_cascadia_dates(root)
    problems: list[str] = []
    for section, dates in linked.items():
        if not dates:
            problems.append(f"{section} has no Cascadia edition links")
            continue
        for edition_date in sorted(dates):
            manifest = _manifest_for(root, edition_date)
            if manifest is None:
                problems.append(f"{section}:{edition_date} missing edition manifest")
                continue
            coverage_start = manifest.get("coverage_start")
            coverage_end = manifest.get("coverage_end")
            coverage_label = manifest.get("coverage_label")
            if manifest.get("briefing_type") != "weekly":
                problems.append(f"{section}:{edition_date} is not weekly")
            if coverage_end != edition_date:
                problems.append(f"{section}:{edition_date} coverage_end is not edition date")
            try:
                if date.fromisoformat(edition_date).weekday() != 6:
                    problems.append(f"{section}:{edition_date} is not a Sunday coverage_end")
            except ValueError:
                problems.append(f"{section}:{edition_date} is not a valid date")
            if not coverage_start or not coverage_end:
                problems.append(f"{section}:{edition_date} missing coverage range")
            if not coverage_label:
                problems.append(f"{section}:{edition_date} missing coverage label")
                continue
            section_path = root / "output" / "site" / "cascadia" / ("archive.html" if section == "archive" else "index.html" if section == "recent" else "rss.xml")
            if section_path.exists() and str(coverage_label) not in _read_text(section_path):
                problems.append(f"{section}:{edition_date} coverage label not shown")
    return _result("Cascadia weekly public links", not problems, "archive, recent editions, and RSS link weekly coverage labels only" if not problems else "; ".join(problems))


def check_gaza_archive(root: Path) -> CheckResult:
    path = root / "output" / "site" / "gaza" / "archive.html"
    return _result("Gaza archive", path.exists(), f"{path} exists" if path.exists() else f"{path} is missing")


def _git_branch(repo: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def check_pages_repo(root: Path) -> CheckResult:
    pages = root / "bluefern-dispatches-pages"
    if not pages.exists():
        return _result("Pages repo branch", True, "Pages repo is not present; branch check skipped")
    if not (pages / ".git").exists():
        return _result("Pages repo branch", False, f"{pages} exists but is not a git repo")
    branch = _git_branch(pages)
    return _result("Pages repo branch", branch == EXPECTED_PAGES_BRANCH, f"Pages repo is on {EXPECTED_PAGES_BRANCH}" if branch == EXPECTED_PAGES_BRANCH else f"Pages repo branch is {branch or 'unknown'}, expected {EXPECTED_PAGES_BRANCH}")


def check_cname(root: Path) -> CheckResult:
    pages = root / "bluefern-dispatches-pages"
    if not pages.exists():
        return _result("Pages CNAME", True, "Pages repo is not present; CNAME check skipped")
    cname = pages / "CNAME"
    if not cname.exists():
        return _result("Pages CNAME", False, f"{cname} is missing")
    value = _read_text(cname).strip()
    return _result("Pages CNAME", value == EXPECTED_CNAME, f"CNAME is {EXPECTED_CNAME}" if value == EXPECTED_CNAME else f"CNAME is {value!r}, expected {EXPECTED_CNAME!r}")


def check_smtp_password_not_logged(root: Path) -> CheckResult:
    logs = root / "logs"
    if not logs.exists():
        return _result("SMTP_PASSWORD logs", True, "logs folder is absent; log scan skipped")
    offenders: list[str] = []
    for path in logs.rglob("*.log"):
        if "SMTP_PASSWORD" in _read_text(path):
            offenders.append(str(path.relative_to(root)))
    return _result("SMTP_PASSWORD logs", not offenders, "SMTP_PASSWORD does not appear in logs" if not offenders else f"SMTP_PASSWORD appears in: {', '.join(offenders)}")


def check_json_files_parse(root: Path, glob_pattern: str, name: str) -> CheckResult:
    files = sorted(root.glob(glob_pattern))
    problems: list[str] = []
    for path in files:
        try:
            _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.relative_to(root)}: {exc}")
    if not files:
        return _result(name, True, f"no files matched {glob_pattern}; parse check skipped")
    return _result(name, not problems, f"{len(files)} file(s) parse" if not problems else "; ".join(problems))


def run_checks(root: Path) -> list[CheckResult]:
    root = root.resolve()
    return [
        check_project_venv(root),
        check_required_folders(root),
        check_old_project_runtime_strings(root),
        check_scheduled_tasks_use_project_venv(root),
        check_no_public_detail_or_paid(root),
        check_cascadia_transitional_dates_excluded(root),
        check_cascadia_weekly_links(root),
        check_gaza_archive(root),
        check_pages_repo(root),
        check_cname(root),
        check_smtp_password_not_logged(root),
        check_json_files_parse(root, "data/dispatches/**/manual_sources.json", "manual source JSON"),
        check_json_files_parse(root, "data/dispatches/cascadia/sources/**/historical_search_report.json", "historical search reports"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Dispatches From The Blue Fern Co. project contract.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root to check.")
    args = parser.parse_args(argv)

    results = run_checks(args.root)
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"{status}  {result.name}: {result.message}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
