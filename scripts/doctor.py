from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bluefern_dispatches.public_prose import html_contains_public_prose_violations, summarize_violations

EXPECTED_CNAME = "dispatches.thebluefernco.com"
EXPECTED_PAGES_BRANCH = "gh-pages"
KNOWN_CASCADIA_TRANSITIONAL_DAILY_DATES = {
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-05-07",
    "2026-05-08",
    "2026-05-09",
}
OLD_PROJECT_NEEDLES = (
    "fda_media_pipeline",
    "FDA media pipeline",
    "FDA/Cascadia media pipeline",
    "old Gaza project",
)
RUNTIME_PATTERNS = ("*.py", "*.ps1")
LINKED_EDITION_RE = re.compile(r"editions/(\d{4}-\d{2}-\d{2})/")
AP_CANDIDATE_FILE_RE = re.compile(
    r"^data/dispatches/american-pressure/candidates/\d{4}-\d{2}-\d{2}/candidate_sources\.json$"
)
AP_FEED_BACKFILL_FILE_RE = re.compile(
    r"^data/dispatches/american-pressure/sources/\d{4}-\d{2}-\d{2}/feed_backfill_sources\.json$"
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class SchedulerTemplateSpec:
    classification: str
    expected_working_directory: str | None = None
    reference_reason: str = ""
    require_existing_working_directory: bool = False


SCHEDULER_REFERENCE_MARKERS = (
    "reference only",
    "reference-only",
    "historical/reference only",
    "intentionally disabled",
)
SCHEDULED_TASK_TEMPLATE_REGISTRY: dict[str, SchedulerTemplateSpec] = {
    "ops/generate_and_notify_task.xml": SchedulerTemplateSpec(
        classification="reference",
        reference_reason=(
            "legacy Gaza monorepo template; active Gaza scheduling runs from "
            r"C:\BlueFernRunner\GazaDispatchesCurrent6"
        ),
    ),
    "ops/run_american_pressure_weekly_task.xml": SchedulerTemplateSpec(
        classification="reference",
        reference_reason="American Pressure weekly publish is intentionally disabled while its split runner is absent",
    ),
    "ops/run_cascadia_weekly_task.xml": SchedulerTemplateSpec(
        classification="reference",
        reference_reason="historical/reference-only Cascadia scheduler template",
    ),
    "ops/blue_fern_operator_task.xml": SchedulerTemplateSpec(
        classification="reference",
        reference_reason="Blue Fern Operator scheduled supervisor template; install only after explicit production rollout verification",
    ),
}


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


def _repo_relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _task_xml_values(path: Path) -> dict[str, str | list[str]]:
    text = _read_text(path)
    try:
        xml_root = ET.fromstring(text)
    except ET.ParseError:
        return {
            "description": "",
            "arguments": text,
            "working_directory": "",
            "settings_enabled": "",
            "trigger_enabled_values": [],
        }

    def local_name(value: str) -> str:
        return value.rsplit("}", 1)[-1] if "}" in value else value

    def children_named(parent: ET.Element, name: str) -> list[ET.Element]:
        return [child for child in parent if local_name(child.tag) == name]

    def first_text(name: str) -> str:
        for child in xml_root.iter():
            if local_name(child.tag) == name:
                return (child.text or "").strip()
        return ""

    settings_enabled = ""
    settings = children_named(xml_root, "Settings")
    if settings:
        enabled = children_named(settings[0], "Enabled")
        if enabled:
            settings_enabled = (enabled[0].text or "").strip().lower()

    trigger_enabled_values: list[str] = []
    triggers = children_named(xml_root, "Triggers")
    if triggers:
        for trigger in triggers[0]:
            for child in trigger.iter():
                if local_name(child.tag) == "Enabled":
                    trigger_enabled_values.append((child.text or "").strip().lower())

    return {
        "description": first_text("Description"),
        "arguments": first_text("Arguments"),
        "working_directory": first_text("WorkingDirectory"),
        "settings_enabled": settings_enabled,
        "trigger_enabled_values": trigger_enabled_values,
    }


def _scheduler_spec_for(path: Path, root: Path) -> SchedulerTemplateSpec:
    rel = _repo_relative(path, root)
    return SCHEDULED_TASK_TEMPLATE_REGISTRY.get(
        rel,
        SchedulerTemplateSpec(classification="active", expected_working_directory=str(root)),
    )


def _path_equal(left: str, right: str) -> bool:
    return left.rstrip("\\/").lower() == right.rstrip("\\/").lower()


def _enabled_value_is(value: object, expected: bool) -> bool:
    return isinstance(value, str) and value.lower() == ("true" if expected else "false")


def check_project_venv(root: Path) -> CheckResult:
    venv = root / ".venv"
    return _result("project .venv", venv.is_dir(), f"{venv} exists" if venv.is_dir() else f"{venv} is missing")


def check_required_folders(root: Path) -> CheckResult:
    required = [
        "assets",
        "data/dispatches/gaza",
        "data/dispatches/american-pressure",
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
    reference_notes: list[str] = []
    absolute_python_re = re.compile(r"[A-Za-z]:\\[^\"'<>]*?\\(?:\.venv|venv)\\Scripts\\python\.exe", re.IGNORECASE)
    for path in task_files:
        text = _read_text(path)
        lowered = text.lower()
        rel = path.relative_to(root)
        rel_posix = _repo_relative(path, root)
        values = _task_xml_values(path)
        spec = _scheduler_spec_for(path, root)
        settings_enabled = values["settings_enabled"]
        trigger_enabled_values = values["trigger_enabled_values"]
        if spec.classification == "reference":
            if not any(marker in lowered for marker in SCHEDULER_REFERENCE_MARKERS):
                problems.append(f"{rel} is classified reference-only but is not marked reference-only in the XML")
                continue
            if not _enabled_value_is(settings_enabled, False):
                problems.append(f"{rel} is classified reference-only but Settings/Enabled is not false")
            if isinstance(trigger_enabled_values, list):
                enabled_triggers = [value for value in trigger_enabled_values if value != "false"]
                if enabled_triggers:
                    problems.append(f"{rel} is classified reference-only but a trigger Enabled value is not false")
            reason = f": {spec.reference_reason}" if spec.reference_reason else ""
            reference_notes.append(f"{rel_posix} reference-only{reason}")
            continue

        if spec.classification != "active":
            problems.append(f"{rel} has unknown scheduler template classification {spec.classification!r}")
            continue

        if not _enabled_value_is(settings_enabled, True):
            problems.append(f"{rel} active scheduler template Settings/Enabled is not true")
        if isinstance(trigger_enabled_values, list):
            disabled_triggers = [value for value in trigger_enabled_values if value != "true"]
            if disabled_triggers:
                problems.append(f"{rel} active scheduler template has a trigger Enabled value that is not true")
        expected_root = spec.expected_working_directory or str(root)
        expected_venv = str(Path(expected_root) / ".venv" / "Scripts" / "python.exe")
        working_directory = str(values["working_directory"])
        if not _path_equal(working_directory, expected_root):
            problems.append(f"{rel} does not set expected working directory {expected_root}")
        if spec.require_existing_working_directory and not Path(expected_root).exists():
            problems.append(f"{rel} expected working directory does not exist: {expected_root}")
        uses_relative_project_venv = ".\\.venv\\Scripts\\python.exe" in text or "./.venv/Scripts/python.exe" in text
        uses_absolute_project_venv = expected_venv in text
        if not uses_relative_project_venv and not uses_absolute_project_venv:
            problems.append(f"{rel} does not use expected .venv Python for {expected_root}")
        for match in absolute_python_re.findall(text):
            if not _path_equal(match, expected_venv):
                problems.append(f"{rel} contains non-project Python path {match}")
        if "full_project" in lowered and ("run_daily_gaza.py" in lowered or "run_cascadia_and_notify.py" in lowered or "run_cascadia_dispatch.py" in lowered):
            problems.append(f"{rel} uses full_project validation profile for scheduled Gaza/Cascadia workflow")
    if not task_files:
        return _result("scheduled task .venv", True, "no scheduled task XML files found; scheduled task check skipped")
    if problems:
        return _result("scheduled task .venv", False, "; ".join(problems))
    if reference_notes:
        return _result(
            "scheduled task .venv",
            True,
            "active scheduler templates use expected runner roots; reference templates skipped: "
            + "; ".join(reference_notes),
        )
    return _result("scheduled task .venv", True, "active scheduler templates use expected runner roots")


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
    repo = repo.resolve()
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", str(repo), "branch", "--show-current"],
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


def check_public_html_prose_quality(root: Path) -> CheckResult:
    site_root = root / "output" / "site"
    html_files = sorted(site_root.rglob("*.html")) if site_root.exists() else []
    if not html_files:
        return _result("public HTML prose quality", True, "no public HTML files found; prose check skipped")
    offenders: list[str] = []
    for path in html_files:
        violations = html_contains_public_prose_violations(_read_text(path))
        if violations:
            offenders.append(f"{path.relative_to(root)}: {summarize_violations(violations)}")
    return _result(
        "public HTML prose quality",
        not offenders,
        "public HTML contains no banned rationale or incomplete modal prose"
        if not offenders
        else "; ".join(offenders),
    )


def classify_ap_artifact_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if normalized == "data/source_registry/american_pressure_sources.json":
        return "durable_source_registry"
    if normalized.startswith("data/dispatches/american-pressure/sources/") and normalized.endswith("/manual_sources.json"):
        return "durable_manual_sources"
    if AP_CANDIDATE_FILE_RE.match(normalized):
        return "deferred_intake_candidates"
    if AP_FEED_BACKFILL_FILE_RE.match(normalized):
        return "deferred_feed_backfill"
    return None


def _git_tracked_files(root: Path) -> list[str]:
    root = root.resolve()
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def check_american_pressure_artifact_retention(root: Path) -> CheckResult:
    tracked = _git_tracked_files(root)
    if not tracked:
        return _result(
            "American Pressure artifact retention",
            True,
            "git tracked-file inventory unavailable; retention check skipped",
        )
    violations = [
        path
        for path in tracked
        if classify_ap_artifact_path(path) in {"deferred_intake_candidates", "deferred_feed_backfill"}
    ]
    if violations:
        return _result(
            "American Pressure artifact retention",
            False,
            "tracked deferred artifacts must be local-only: " + ", ".join(sorted(violations)),
        )
    return _result(
        "American Pressure artifact retention",
        True,
        "deferred intake/backfill artifacts are not tracked; durable manual/registry sources remain commit-eligible",
    )


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
        check_american_pressure_artifact_retention(root),
        check_json_files_parse(root, "data/dispatches/**/manual_sources.json", "manual source JSON"),
        check_json_files_parse(root, "data/dispatches/cascadia/sources/**/historical_search_report.json", "historical search reports"),
        check_public_html_prose_quality(root),
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
