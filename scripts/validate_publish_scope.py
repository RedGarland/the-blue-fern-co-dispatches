from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.food_line_runtime_paths import FOOD_LINE_ALLOWED_DIRTY_CATEGORIES, classify_food_line_runtime_path


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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_run(repo_root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=text,
        check=False,
    )


def _git_stdout(repo_root: Path, *args: str) -> str | None:
    result = _git_run(repo_root, *args)
    if result.returncode != 0:
        return None
    return str(result.stdout).strip()


def _git_commit_exists(repo_root: Path, commit: str) -> bool:
    if not commit:
        return False
    result = _git_run(repo_root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    return result.returncode == 0


def _git_path_is_tracked(repo_root: Path, relpath: str) -> bool:
    result = _git_run(repo_root, "ls-files", "--error-unmatch", "--", relpath)
    return result.returncode == 0


def _git_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    result = _git_run(repo_root, "merge-base", "--is-ancestor", ancestor, descendant)
    return result.returncode == 0


def _git_file_bytes(repo_root: Path, commit: str, relpath: str) -> bytes | None:
    spec = f"{commit}:{relpath}"
    result = _git_run(repo_root, "cat-file", "--filters", spec, text=False)
    if result.returncode != 0:
        return None
    return bytes(result.stdout)


def _food_line_runtime_editorial_provenance_is_allowed(relpath: str) -> bool:
    normalized = _normalize_path(relpath)
    if classify_food_line_runtime_path(normalized) not in FOOD_LINE_ALLOWED_DIRTY_CATEGORIES:
        return False
    if normalized.startswith("data/dispatches/food-line/review/proposed-editions/") and normalized.endswith(".json"):
        return True
    if normalized.startswith("data/dispatches/food-line/review/signal-reviews/") and normalized.endswith(".json"):
        return True
    return False


def _release_manifest_delta(
    *,
    manifest_path: Path,
    dispatch: str,
    declared_date: dt.date | None,
    source_repo_root: Path,
    pages_repo_root: Path | None,
    required_source_ref: str | None = None,
    release_manifest_commit: str | None = None,
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    source_paths: list[str] = []
    pages_paths: list[str] = []
    source_root = source_repo_root.resolve()
    pages_root = pages_repo_root.resolve() if pages_repo_root is not None else None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [], [f"Unable to read release manifest {manifest_path}: {exc}"]
    if not isinstance(payload, dict):
        return [], [], ["release manifest must be a JSON object"]
    expected_schema = {
        "food-line": "food_line_release_manifest_v2",
        "care-line": "care_line_release_manifest_v1",
    }.get(dispatch)
    if expected_schema is None:
        errors.append("release manifest is only supported for Food Line and Care Line")
    elif payload.get("schema_version") != expected_schema:
        errors.append(f"release manifest schema_version must be {expected_schema}")
    if payload.get("dispatch") != dispatch:
        errors.append("release manifest dispatch does not match --dispatch")
    if declared_date is not None and payload.get("edition_date") != declared_date.isoformat():
        errors.append("release manifest edition_date does not match --date")
    if payload.get("deletions") not in ([], None):
        errors.append(f"release manifest deletions are not authorized for this {dispatch} release")
    if payload.get("shared_files") not in ([], None):
        errors.append("release manifest shared files require a separate explicit authorization")
    source_commit = str(payload.get("source_commit") or "").strip()
    if not source_commit:
        errors.append("release manifest source_commit is required")
    elif not _git_commit_exists(source_root, source_commit):
        errors.append(f"release manifest source_commit does not resolve to a git commit: {source_commit}")
    else:
        expected_ref = required_source_ref or "HEAD"
        resolved_expected_ref = _git_stdout(source_root, "rev-parse", expected_ref)
        if not resolved_expected_ref:
            errors.append(f"unable to resolve expected source ref for release manifest validation: {expected_ref}")
        elif not _git_is_ancestor(source_root, source_commit, expected_ref):
            errors.append(
                f"release manifest source_commit is not reachable from expected source ref {expected_ref}: {source_commit}"
            )
        manifest_ref = release_manifest_commit or "HEAD"
        resolved_manifest_ref = _git_stdout(source_root, "rev-parse", manifest_ref)
        if not resolved_manifest_ref:
            errors.append(f"unable to resolve release-manifest commit for validation: {manifest_ref}")
        elif not _git_is_ancestor(source_root, source_commit, manifest_ref):
            errors.append(
                f"release-manifest commit {manifest_ref} is not a descendant of source_commit {source_commit}"
            )
    provenance_pairs = (
        ("approved_proposal_path", "approved_proposal_sha256"),
        ("review_snapshot_path", "review_snapshot_sha256"),
    )
    for path_key, sha_key in provenance_pairs:
        relpath = _normalize_path(str(payload.get(path_key) or ""))
        recorded_sha = str(payload.get(sha_key) or "").strip().lower()
        if not relpath and not recorded_sha:
            continue
        if not relpath or not recorded_sha:
            errors.append(f"release manifest requires both {path_key} and {sha_key}")
            continue
        tracked = _git_path_is_tracked(source_root, relpath)
        if tracked:
            if source_commit and _git_commit_exists(source_root, source_commit):
                file_bytes = _git_file_bytes(source_root, source_commit, relpath)
                if file_bytes is None:
                    errors.append(
                        f"release manifest source-input file is missing at source_commit: {relpath} "
                        f"(role=source_input, source_commit={source_commit})"
                    )
                else:
                    commit_sha = _sha256_bytes(file_bytes)
                    if commit_sha != recorded_sha:
                        errors.append(
                            f"release manifest source-input hash mismatch for {relpath} "
                            f"(role=source_input, expected={recorded_sha}, actual={commit_sha}, source_commit={source_commit})"
                        )
        file_path = (source_root / relpath).resolve()
        try:
            file_path.relative_to(source_root)
        except ValueError:
            errors.append(f"release manifest referenced path resolves outside the source repo: {relpath}")
            continue
        if not file_path.is_file():
            errors.append(f"release manifest referenced file is missing in the working tree: {relpath}")
            continue
        actual_sha = _sha256_file(file_path)
        if actual_sha != recorded_sha:
            errors.append(
                f"release manifest source-input hash mismatch for {relpath} "
                f"(role=source_input, expected={recorded_sha}, actual={actual_sha}, source_commit={source_commit})"
            )
        if not tracked and dispatch == "food-line" and not _food_line_runtime_editorial_provenance_is_allowed(relpath):
            errors.append(
                f"release manifest untracked source-input path is not an approved Food Line runtime editorial input: {relpath}"
            )
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("release manifest must contain a non-empty entries list")
        return source_paths, pages_paths, errors
    seen_source: set[str] = set()
    seen_pages: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"release manifest entry {index} must be an object")
            continue
        source_rel = _normalize_path(str(entry.get("source_path") or ""))
        pages_rel = _normalize_path(str(entry.get("pages_path") or ""))
        provenance_role = str(entry.get("provenance_role") or "").strip()
        if not source_rel or not pages_rel:
            errors.append(f"release manifest entry {index} requires source_path and pages_path")
            continue
        if expected_schema == "food_line_release_manifest_v2":
            if not provenance_role:
                errors.append(f"release manifest entry {source_rel} requires provenance_role")
                continue
            if provenance_role not in {"generated_output", "source_input"}:
                errors.append(f"release manifest entry {source_rel} has unsupported provenance_role: {provenance_role}")
                continue
        else:
            provenance_role = provenance_role or "generated_output"
        if source_rel in seen_source or pages_rel in seen_pages:
            errors.append(f"release manifest contains a duplicate source or Pages path: {source_rel} -> {pages_rel}")
            continue
        seen_source.add(source_rel)
        seen_pages.add(pages_rel)
        source_paths.append(source_rel)
        pages_paths.append(pages_rel)
        expected_pages = source_rel.removeprefix("output/site/") if source_rel.startswith("output/site/") else ""
        if pages_rel != expected_pages:
            errors.append(f"release manifest source-to-Pages mapping is invalid: {source_rel} -> {pages_rel}")
        source_file = (source_root / source_rel).resolve()
        try:
            source_file.relative_to(source_root)
        except ValueError:
            errors.append(f"release manifest source path resolves outside the source repo: {source_rel}")
            continue
        if not source_file.is_file():
            errors.append(f"release manifest source file is missing: {source_rel}")
            continue
        actual_source_sha = _sha256_file(source_file)
        recorded_sha = str(entry.get("source_sha256") or "").lower()
        if recorded_sha != actual_source_sha:
            errors.append(
                f"release manifest generated-output hash mismatch for {source_rel} "
                f"(role={provenance_role or 'unknown'}, expected={recorded_sha}, actual={actual_source_sha})"
            )
        if provenance_role == "source_input":
            if not source_commit:
                errors.append(
                    f"release manifest entry {source_rel} requires source_commit for source-input validation "
                    f"(role=source_input)"
                )
            elif _git_commit_exists(source_root, source_commit):
                source_bytes_at_commit = _git_file_bytes(source_root, source_commit, source_rel)
                if source_bytes_at_commit is None:
                    errors.append(
                        f"release manifest source-input file is missing at source_commit for {source_rel} "
                        f"(role=source_input, source_commit={source_commit})"
                    )
                else:
                    commit_source_sha = _sha256_bytes(source_bytes_at_commit)
                    if recorded_sha != commit_source_sha:
                        errors.append(
                            f"release manifest source-input hash mismatch for {source_rel} "
                            f"(role=source_input, expected={recorded_sha}, actual={commit_source_sha}, source_commit={source_commit})"
                        )
        elif provenance_role != "generated_output":
            errors.append(f"release manifest entry {source_rel} has unknown provenance_role: {provenance_role or '<missing>'}")
        target = pages_root / pages_rel if pages_root is not None else None
        if target is None or not target.exists():
            expected_action = "add"
            target_sha = None
        elif not target.is_file():
            errors.append(f"release manifest Pages destination is not a file: {pages_rel}")
            continue
        else:
            target_sha = _sha256_file(target)
            expected_action = "unchanged" if target_sha == actual_source_sha else "modify"
        if entry.get("action") != expected_action:
            errors.append(f"release manifest action for {pages_rel} is stale: expected {expected_action}")
        recorded_target_sha = entry.get("pages_sha256_before")
        if recorded_target_sha != target_sha:
            errors.append(f"release manifest pre-sync Pages SHA-256 is stale: {pages_rel}")

    if dispatch in {"food-line", "care-line"} and declared_date is not None:
        edition_dir = source_root / "output" / "site" / dispatch / "editions" / declared_date.isoformat()
        expected_paths = {
            f"output/site/{dispatch}/index.html",
            f"output/site/{dispatch}/archive.html",
        }
        if dispatch in {"food-line", "care-line"}:
            expected_paths.add(f"output/site/{dispatch}/rss.xml")
        if edition_dir.is_dir():
            expected_paths.update(path.relative_to(source_root).as_posix() for path in edition_dir.rglob("*") if path.is_file())
        missing = sorted(expected_paths - set(source_paths))
        extra = sorted(set(source_paths) - expected_paths)
        if missing:
            errors.append(f"release manifest omits generated {dispatch} publication files: " + ", ".join(missing))
        if extra:
            errors.append(f"release manifest contains unexpected {dispatch} publication files: " + ", ".join(extra))
    return source_paths, pages_paths, errors


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
    release_manifest_path: Path | str | None = None,
    required_source_ref: str | None = None,
    release_manifest_commit: str | None = None,
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

    if release_manifest_path is not None:
        if not strict:
            errors.append("--release-manifest requires --strict")
        if pages_repo_root is None or not allow_pages:
            errors.append("--release-manifest requires --allow-pages and --pages-repo-root")
        else:
            source_changed_paths, pages_changed_paths, manifest_errors = _release_manifest_delta(
                manifest_path=Path(release_manifest_path).resolve(),
                dispatch=dispatch,
                declared_date=declared_date,
                source_repo_root=Path(source_repo_root),
                pages_repo_root=Path(pages_repo_root),
                required_source_ref=required_source_ref,
                release_manifest_commit=release_manifest_commit,
            )
            errors.extend(manifest_errors)
            try:
                dirty_pages = _git_status_porcelain(Path(pages_repo_root))
            except RuntimeError as exc:
                errors.append(str(exc))
            else:
                if dirty_pages:
                    errors.append("Pages repo must be clean before release-manifest synchronization: " + ", ".join(dirty_pages))
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
    parser.add_argument("--release-manifest", help="Validate the exact source-to-Pages delta declared by a deterministic release manifest.")
    parser.add_argument("--required-source-ref", help="Expected source branch or commit ref for release-manifest provenance checks.")
    parser.add_argument("--release-manifest-commit", help="Commit ref that should contain the release manifest. Defaults to HEAD.")
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
        release_manifest_path=args.release_manifest,
        required_source_ref=args.required_source_ref,
        release_manifest_commit=args.release_manifest_commit,
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
    if args.release_manifest:
        print(f"- Release manifest: {Path(args.release_manifest).resolve()}")
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
