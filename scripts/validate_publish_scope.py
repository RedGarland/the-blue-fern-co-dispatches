from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DISPATCH_CHOICES = (
    "gaza",
    "food-line",
    "care-line",
    "cascadia",
    "american-pressure",
    "sitewide",
)


@dataclass(frozen=True)
class ScopePaths:
    source_prefixes: tuple[str, ...]
    pages_prefixes: tuple[str, ...]


DISPATCH_SCOPES: dict[str, ScopePaths] = {
    "gaza": ScopePaths(
        source_prefixes=(
            "data/dispatches/gaza/",
            "output/site/gaza/",
            "output/review/gaza/",
            "logs/gaza/",
            "src/bluefern_dispatches/gaza",
            "scripts/run_gaza",
            "scripts/publish_gaza",
            "tests/test_gaza",
            "tests/test_bluesky_post.py",
            "tests/test_gaza_audio.py",
        ),
        pages_prefixes=(
            "gaza/",
            "assets/",
        ),
    ),
    "food-line": ScopePaths(
        source_prefixes=(
            "data/dispatches/food-line/",
            "output/site/food-line/",
            "output/review/food-line/",
            "logs/food-line/",
            "src/bluefern_dispatches/food_line",
            "scripts/run_food_line",
            "scripts/check_food_line",
            "scripts/discover_food_line",
            "tests/test_food_line",
            "tests/test_food_line_",
        ),
        pages_prefixes=(
            "food-line/",
            "assets/",
        ),
    ),
    "care-line": ScopePaths(
        source_prefixes=(
            "data/dispatches/care-line/",
            "output/site/care-line/",
            "output/review/care-line/",
            "logs/care-line/",
            "src/bluefern_dispatches/care_line",
            "scripts/run_care_line",
            "tests/test_care_line",
        ),
        pages_prefixes=(
            "care-line/",
            "assets/",
        ),
    ),
    "cascadia": ScopePaths(
        source_prefixes=(
            "data/dispatches/cascadia/",
            "output/site/cascadia/",
            "output/review/cascadia/",
            "output/detail/cascadia/",
            "logs/cascadia/",
            "src/bluefern_dispatches/cascadia",
            "scripts/run_cascadia",
            "scripts/run_weekly_cascadia.ps1",
            "tests/test_cascadia",
        ),
        pages_prefixes=(
            "cascadia/",
            "assets/",
        ),
    ),
    "american-pressure": ScopePaths(
        source_prefixes=(
            "data/dispatches/american-pressure/",
            "output/site/american-pressure/",
            "output/review/american-pressure/",
            "logs/american-pressure/",
            "src/bluefern_dispatches/american_pressure",
            "scripts/run_american_pressure",
            "scripts/scout_american_pressure",
            "scripts/review_american_pressure",
            "tests/test_american_pressure",
        ),
        pages_prefixes=(
            "american-pressure/",
            "assets/",
        ),
    ),
}

SITEWIDE_SOURCE_PREFIXES = (
    "AGENTS.md",
    "README.md",
    "PROJECT_SUMMARY.md",
    "docs/",
    ".github/",
    "scripts/",
    "src/bluefern_dispatches/",
    "assets/",
    "tests/",
    "data/records/",
    "output/site/",
)

SITEWIDE_PAGES_PREFIXES = (
    "",
)

DATE_DIR_RE = r"(?:editions|review|sources)/(?P<date>\d{4}-\d{2}-\d{2})(?:/|$)"
AUDIO_DATE_RE = r"audio/(?P<date>\d{4}-\d{2}-\d{2})(?:-v\d+)?(?:-transcript)?\.(?:html|json|mp3)$"


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _parse_date_text(date_text: str | None) -> dt.date | None:
    if date_text is None or not str(date_text).strip():
        return None
    try:
        return dt.date.fromisoformat(str(date_text))
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_text}': expected YYYY-MM-DD.") from exc


def _git_status_porcelain(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Unable to inspect git status in {repo_root}: {exc.stderr or exc.stdout or exc}") from exc
    records = [item for item in result.stdout.split("\0") if item]
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        status = record[:2]
        if status and status[0] in {"R", "C"}:
            payload = record[3:] if len(record) > 3 and record[2] == " " else record[2:]
            paths.append(_normalize_path(payload))
            if index + 1 < len(records):
                paths.append(_normalize_path(records[index + 1]))
            index += 2
            continue
        payload = record[3:] if len(record) > 3 and record[2] == " " else record[2:]
        paths.append(_normalize_path(payload))
        index += 1
    return paths


def _scope_for_dispatch(dispatch: str) -> ScopePaths:
    if dispatch == "sitewide":
        return ScopePaths(source_prefixes=SITEWIDE_SOURCE_PREFIXES, pages_prefixes=SITEWIDE_PAGES_PREFIXES)
    return DISPATCH_SCOPES[dispatch]


def _path_matches_any(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _classify_path(path: str) -> tuple[bool, bool, bool]:
    normalized = _normalize_path(path)
    is_audio = "/audio/" in normalized or normalized.endswith("podcast.xml")
    is_map = "/map/" in normalized or normalized.endswith("map.html") or normalized.endswith("map_data.json")
    is_bluesky = "bluesky" in normalized.lower()
    return is_audio, is_map, is_bluesky


def _date_from_path(path: str) -> dt.date | None:
    normalized = _normalize_path(path)
    for pattern in (DATE_DIR_RE, AUDIO_DATE_RE):
        match = re.search(pattern, normalized)
        if match:
            return dt.date.fromisoformat(match.group("date"))
    return None


def _validate_paths(
    *,
    paths: Sequence[str],
    dispatch: str,
    declared_date: dt.date | None,
    context: str,
    allow_audio: bool,
    allow_map: bool,
    allow_bluesky: bool,
) -> list[str]:
    errors: list[str] = []
    scope = _scope_for_dispatch(dispatch)
    prefixes = scope.source_prefixes if context.startswith("source") else scope.pages_prefixes

    for raw_path in paths:
        path = _normalize_path(raw_path)
        audio, map_artifact, bluesky = _classify_path(path)
        date_in_path = _date_from_path(path)

        if dispatch != "sitewide" and declared_date is not None and date_in_path is not None:
            if date_in_path > declared_date:
                errors.append(f"{context} path has future edition date {date_in_path.isoformat()} beyond declared {declared_date.isoformat()}: {path}")
            elif date_in_path != declared_date:
                errors.append(f"{context} path uses edition date {date_in_path.isoformat()} outside declared {declared_date.isoformat()}: {path}")

        if audio and not allow_audio:
            errors.append(f"{context} path uses audio/transcript/podcast artifacts without --allow-audio: {path}")
        if map_artifact and not allow_map:
            errors.append(f"{context} path uses map artifacts without --allow-map: {path}")
        if bluesky and not allow_bluesky:
            errors.append(f"{context} path uses Bluesky artifacts without --allow-bluesky: {path}")

        if not _path_matches_any(path, prefixes):
            errors.append(f"{context} path is outside the declared {dispatch} publish scope: {path}")

    return errors


def validate_publish_scope(
    *,
    dispatch: str,
    date_text: str | None = None,
    source_repo_root: Path | str = Path("."),
    pages_repo_root: Path | str | None = None,
    allow_pages: bool = False,
    allow_audio: bool = False,
    allow_map: bool = False,
    allow_bluesky: bool = False,
    strict: bool = False,
    source_changed_paths: Sequence[str] | None = None,
    pages_changed_paths: Sequence[str] | None = None,
) -> list[str]:
    errors: list[str] = []

    if dispatch not in DISPATCH_CHOICES:
        errors.append(f"Unknown dispatch '{dispatch}'. Expected one of: {', '.join(DISPATCH_CHOICES)}.")
        return errors

    try:
        declared_date = _parse_date_text(date_text)
    except ValueError as exc:
        return [str(exc)]

    if dispatch != "sitewide" and declared_date is None:
        errors.append("--date is required for dated dispatch scopes unless --dispatch sitewide is used.")

    if pages_repo_root is not None and not allow_pages:
        errors.append("--pages-repo-root was provided without --allow-pages; refusing to inspect Pages repo scope.")
    if allow_pages and pages_repo_root is None:
        errors.append("--allow-pages requires --pages-repo-root so the Pages repo can be inspected.")

    if source_changed_paths is None:
        try:
            source_changed_paths = _git_status_porcelain(Path(source_repo_root))
        except RuntimeError as exc:
            errors.append(str(exc))
            source_changed_paths = ()
    if pages_repo_root is not None and allow_pages and pages_changed_paths is None:
        try:
            pages_changed_paths = _git_status_porcelain(Path(pages_repo_root))
        except RuntimeError as exc:
            errors.append(str(exc))
            pages_changed_paths = ()

    errors.extend(
        _validate_paths(
            paths=source_changed_paths,
            dispatch=dispatch,
            declared_date=declared_date,
            context="source repo",
            allow_audio=allow_audio,
            allow_map=allow_map,
            allow_bluesky=allow_bluesky,
        )
    )

    if pages_repo_root is not None and allow_pages:
        errors.extend(
            _validate_paths(
                paths=pages_changed_paths or (),
                dispatch=dispatch,
                declared_date=declared_date,
                context="Pages repo",
                allow_audio=allow_audio,
                allow_map=allow_map,
                allow_bluesky=allow_bluesky,
            )
        )

    if strict:
        # Strict mode currently keeps the same conservative checks but records
        # that the caller intentionally requested release-grade validation.
        pass

    return errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate publish scope before Pages sync or public release.")
    parser.add_argument("--dispatch", required=True, choices=DISPATCH_CHOICES, help="Declared dispatch family or sitewide scope.")
    parser.add_argument("--date", help="Declared edition date in YYYY-MM-DD format. Required unless --dispatch sitewide.")
    parser.add_argument("--source-repo-root", default=".", help="Source repository root to inspect with git status.")
    parser.add_argument("--pages-repo-root", help="Optional local Pages repo root to inspect with git status.")
    parser.add_argument("--allow-pages", action="store_true", help="Explicitly allow Pages repo scope inspection.")
    parser.add_argument("--allow-audio", action="store_true", help="Explicitly allow audio/transcript/podcast artifacts.")
    parser.add_argument("--allow-map", action="store_true", help="Explicitly allow map artifacts.")
    parser.add_argument("--allow-bluesky", action="store_true", help="Explicitly allow Bluesky state/post artifacts.")
    parser.add_argument("--strict", action="store_true", help="Fail closed on publish-sensitive files outside the declared scope.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        source_repo_root = Path(args.source_repo_root).resolve()
        pages_repo_root = Path(args.pages_repo_root).resolve() if args.pages_repo_root else None
    except Exception as exc:
        print(f"Failed to resolve repository roots: {exc}", file=sys.stderr)
        return 2

    errors = validate_publish_scope(
        dispatch=args.dispatch,
        date_text=args.date,
        source_repo_root=source_repo_root,
        pages_repo_root=pages_repo_root,
        allow_pages=args.allow_pages,
        allow_audio=args.allow_audio,
        allow_map=args.allow_map,
        allow_bluesky=args.allow_bluesky,
        strict=args.strict,
    )

    if errors:
        print("Publish scope validation failed.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("Publish scope validation passed.")
    print(f"- Dispatch: {args.dispatch}")
    if args.date:
        print(f"- Date: {args.date}")
    print(f"- Source repo root: {source_repo_root}")
    if pages_repo_root is not None:
        print(f"- Pages repo root: {pages_repo_root}")
    print(f"- Strict mode: {bool(args.strict)}")
    if args.allow_pages:
        print("- Pages repo inspection enabled.")
    if args.allow_audio:
        print("- Audio/transcript/podcast artifacts allowed.")
    if args.allow_map:
        print("- Map artifacts allowed.")
    if args.allow_bluesky:
        print("- Bluesky artifacts allowed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
