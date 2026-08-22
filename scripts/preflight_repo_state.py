from __future__ import annotations

import argparse
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.care_line_runtime_paths import CARE_LINE_ALLOWED_DIRTY_CATEGORIES, classify_care_line_runtime_path
from scripts.food_line_runtime_paths import FOOD_LINE_ALLOWED_DIRTY_CATEGORIES, classify_food_line_runtime_path

ALLOWED_DIRTY_CATEGORIES = FOOD_LINE_ALLOWED_DIRTY_CATEGORIES | CARE_LINE_ALLOWED_DIRTY_CATEGORIES


def _run_git_status(repo: Path) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["git", "status", "--short", "--branch", "--untracked-files=all"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    lines = [line.rstrip("\n") for line in (result.stdout or "").splitlines()]
    return result.returncode, lines


def _normalize_path(path_text: str) -> str:
    text = path_text.strip().replace("\\", "/")
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    if text.startswith("./"):
        return text[2:]
    return text


def classify_path(path_text: str) -> str:
    path = _normalize_path(path_text)
    lower = path.lower()
    parts = lower.split("/")
    root_name = parts[0] if parts else lower

    if not path:
        return "unknown"
    if lower.startswith(".venv/") or root_name in {"venv", "env", ".venv"}:
        return "virtualenv"
    if lower.startswith("logs/") or lower.endswith(".log") or "/logs/" in lower:
        return "logs"
    if lower.startswith(".pytest_cache/") or lower.startswith(".pytest-temp") or lower.startswith(".pytest_tmp") or "/cache/" in lower or lower.startswith("cache/") or lower.startswith("tmp/") or lower.startswith(".tmp"):
        return "cache"
    if lower.startswith("tests/") or "/tests/" in lower:
        return "tests"
    if lower.startswith("docs/") or root_name in {"readme.md", "project_summary.md", "agents.md"} or lower.startswith(".github/"):
        return "docs"
    if lower.startswith("src/") or lower.startswith("scripts/") or root_name in {"pyproject.toml", "requirements.txt", ".gitignore"}:
        return "source"
    food_line_category = classify_food_line_runtime_path(path)
    if food_line_category:
        return food_line_category
    care_line_category = classify_care_line_runtime_path(path)
    if care_line_category:
        return care_line_category
    if lower.startswith("output/review/") or "/review/" in lower or lower.startswith("output/dispatches/") and "/review/" in lower:
        return "review_output"
    if lower.startswith("output/site/") or lower.startswith("bluefern-dispatches-pages/"):
        return "generated_public_output"
    if lower.startswith("data/dispatches/") and ("/raw/" in lower or "/normalized/" in lower or "/curated/" in lower or "/editions/" in lower):
        return "generated_public_output"
    return "unknown"


def classify_status_line(line: str) -> dict[str, Any] | None:
    text = line.rstrip()
    if not text or text.startswith("## "):
        return None
    if len(text) < 3:
        return None
    status = text[:2]
    if status == "??":
        path_text = text[3:]
    else:
        path_text = text[3:]
    path = _normalize_path(path_text)
    if not path:
        return None
    category = classify_path(path)
    return {
        "status": status,
        "path": path,
        "category": category,
        "is_untracked": status == "??",
        "is_risky": status != "??" or category not in ALLOWED_DIRTY_CATEGORIES,
    }


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    category_counts = Counter(entry["category"] for entry in entries)
    risky_entries = [entry for entry in entries if entry["is_risky"]]
    allowed_entries = [entry for entry in entries if not entry["is_risky"]]
    ignored_recommendations: list[str] = []
    if any(entry["category"] == "logs" for entry in entries) and "logs/*.log" not in ignored_recommendations:
        ignored_recommendations.append("logs/*.log")
    if any(entry["category"] == "review_output" for entry in entries):
        ignored_recommendations.append("output/review/")
        ignored_recommendations.append("output/dispatches/*/review/")
    if any(entry["category"] == "cache" for entry in entries):
        ignored_recommendations.append(".pytest-temp*/")
    return {
        "entry_count": len(entries),
        "category_counts": dict(sorted(category_counts.items())),
        "risky_entries": risky_entries,
        "allowed_entries": allowed_entries,
        "recommended_ignore_patterns": list(dict.fromkeys(ignored_recommendations)),
        "clean": not risky_entries,
    }


def _detect_pages_repo(source_repo: Path) -> Path | None:
    candidates = [
        source_repo / "bluefern-dispatches-pages",
        source_repo.parent / "bluefern-dispatches-pages",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    return None


def _load_repo_report(repo: Path) -> dict[str, Any]:
    rc, lines = _run_git_status(repo)
    entries = [entry for entry in (classify_status_line(line) for line in lines) if entry is not None]
    summary = summarize_entries(entries)
    return {
        "path": str(repo),
        "git_returncode": rc,
        "status_lines": lines,
        "entries": entries,
        "summary": summary,
    }


def build_preflight_report(source_repo: Path | None = None, pages_repo: Path | None = None) -> dict[str, Any]:
    source_repo = (source_repo or ROOT).resolve()
    resolved_pages_repo = pages_repo or _detect_pages_repo(source_repo)
    pages_repo = resolved_pages_repo.resolve() if resolved_pages_repo else None

    source_report = _load_repo_report(source_repo)
    pages_report = _load_repo_report(pages_repo) if pages_repo else None
    risky_source = list(source_report["summary"]["risky_entries"])
    risky_pages = list(pages_report["summary"]["risky_entries"]) if pages_report else []
    ok = not risky_source and not risky_pages
    if pages_report is not None and pages_report["summary"]["entry_count"] == 0:
        pages_status = "clean"
    elif pages_report is not None and pages_report["summary"]["clean"]:
        pages_status = "allowed-only"
    elif pages_report is not None:
        pages_status = "dirty"
    else:
        pages_status = "missing"
    return {
        "ok": ok,
        "source_repo": source_report,
        "pages_repo": pages_report,
        "pages_repo_status": pages_status,
        "allowlisted_categories": sorted(ALLOWED_DIRTY_CATEGORIES),
    }


def render_report(report: dict[str, Any]) -> str:
    source_repo = report["source_repo"]
    pages_repo = report.get("pages_repo")
    lines = [
        "PRE-FLIGHT REPO STATE",
        f"source_repo: {source_repo['path']}",
        f"pages_repo: {pages_repo['path'] if pages_repo else 'not found'}",
        f"allowed_categories: {', '.join(report['allowlisted_categories'])}",
        "",
        "SOURCE REPO STATUS",
        *source_repo["status_lines"],
        "",
        "SOURCE REPO SUMMARY",
    ]
    for key, value in source_repo["summary"]["category_counts"].items():
        lines.append(f"- {key}: {value}")
    if source_repo["summary"]["recommended_ignore_patterns"]:
        lines.append("- recommended ignore patterns:")
        for pattern in source_repo["summary"]["recommended_ignore_patterns"]:
            lines.append(f"  - {pattern}")
    if source_repo["summary"]["allowed_entries"]:
        lines.append("- allowed local/generated entries:")
        for entry in source_repo["summary"]["allowed_entries"]:
            lines.append(f"  - {entry['category']}: {entry['path']}")
    if source_repo["summary"]["risky_entries"]:
        lines.append("- risky entries:")
        for entry in source_repo["summary"]["risky_entries"]:
            lines.append(f"  - {entry['category']}: {entry['path']}")
    else:
        lines.append("- risky entries: none")

    if pages_repo:
        lines.extend(["", "PAGES REPO STATUS", *pages_repo["status_lines"], "", "PAGES REPO SUMMARY"])
        for key, value in pages_repo["summary"]["category_counts"].items():
            lines.append(f"- {key}: {value}")
        if pages_repo["summary"]["risky_entries"]:
            lines.append("- risky entries:")
            for entry in pages_repo["summary"]["risky_entries"]:
                lines.append(f"  - {entry['category']}: {entry['path']}")
        else:
            lines.append("- risky entries: none")
    lines.append("")
    lines.append("RESULT")
    lines.append("ok" if report["ok"] else "dirty")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight git state for source and Pages repos.")
    parser.add_argument("--source-repo", default=str(ROOT))
    parser.add_argument("--pages-repo", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_repo = Path(args.source_repo)
    pages_repo = Path(args.pages_repo) if args.pages_repo else None
    report = build_preflight_report(source_repo, pages_repo)
    print(render_report(report), end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
