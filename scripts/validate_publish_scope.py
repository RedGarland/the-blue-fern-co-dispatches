from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _git_run(repo_root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=text,
        check=False,
    )


def _git_commit_exists(repo_root: Path, commit: str) -> bool:
    if not commit:
        return False
    result = _git_run(repo_root, "rev-parse", "--verify", f"{commit}^{{commit}}")
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


def _git_blob_bytes(repo_root: Path, commit: str, relpath: str) -> bytes | None:
    resolved = _git_run(repo_root, "rev-parse", f"{commit}:{relpath}")
    if resolved.returncode != 0:
        return None
    blob = resolved.stdout.strip()
    result = _git_run(repo_root, "cat-file", "blob", blob, text=False)
    if result.returncode != 0:
        return None
    return bytes(result.stdout)


def _validate_retrospective_release_authority(
    *,
    payload: dict[str, Any],
    dispatch: str,
    declared_date: dt.date | None,
    source_repo_root: Path,
    pages_repo_root: Path | None,
) -> list[str]:
    if payload.get("release_mode") != "approved_migrated_event_retrospective":
        return ["approved retrospective generated output requires the exact retrospective release mode"]
    if dispatch != "food-line" or declared_date is None or pages_repo_root is None:
        return ["approved retrospective generated output requires Food Line, one date, and a Pages checkout"]
    approval_commit = str(payload.get("approval_commit") or "").strip().lower()
    approval_path = _normalize_path(str(payload.get("approval_path") or ""))
    approval_sha = str(payload.get("approval_sha256") or "").removeprefix("sha256:").strip().lower()
    source_commit = str(payload.get("source_commit") or "").strip().lower()
    pages_head = str(payload.get("pages_pre_publish_commit") or "").strip().lower()
    errors: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", approval_commit):
        errors.append("retrospective release approval_commit must be a full lowercase commit ID")
    legacy_approval_path = re.fullmatch(
        r"approvals/food-line/food-line-[a-z0-9-]+-retrospective-[0-9]{2}-approval\.json",
        approval_path,
    )
    v2_approval_path = re.fullmatch(
        r"approvals/food-line/food-line-[a-z0-9-]+-retrospective-[0-9]{2}-approval-v2\.json",
        approval_path,
    )
    v3_approval_path = re.fullmatch(
        r"approvals/food-line/food-line-[a-z0-9-]+-retrospective-[0-9]{2}-approval-v3\.json",
        approval_path,
    )
    v4_approval_path = re.fullmatch(
        r"approvals/food-line/(food-line-[a-z0-9-]+-retrospective-[0-9]{2})-approval-v4\.json",
        approval_path,
    )
    if legacy_approval_path:
        errors.append("obsolete Food Line retrospective V1 approval; renewed V4 approval is required")
    elif v2_approval_path:
        errors.append("obsolete Food Line retrospective V2 approval; renewed V4 approval is required")
    elif v3_approval_path:
        errors.append("obsolete Food Line retrospective V3 approval; renewed V4 approval is required")
    elif not v4_approval_path:
        errors.append("retrospective release approval_path is outside the approval owner")
    if not re.fullmatch(r"[0-9a-f]{64}", approval_sha):
        errors.append("retrospective release approval_sha256 is malformed")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or not _git_commit_exists(source_repo_root, source_commit):
        errors.append("retrospective release source_commit is missing or malformed")
    if errors:
        return errors
    if approval_commit == source_commit or not _git_is_ancestor(source_repo_root, approval_commit, source_commit):
        errors.append("retrospective approval must be consumed after its normal protected merge")
    changed = [
        _normalize_path(line)
        for line in _git_run(source_repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", approval_commit).stdout.splitlines()
        if line.strip()
    ]
    if approval_path not in changed or any(
        not re.fullmatch(
            r"approvals/food-line/food-line-[a-z0-9-]+-retrospective-[0-9]{2}-approval-v4\.json",
            path,
        )
        for path in changed
    ):
        errors.append("retrospective approval authority did not originate in a V4-approval-only commit")
    raw = _git_blob_bytes(source_repo_root, approval_commit, approval_path)
    if raw is None:
        errors.append("retrospective approval is missing from committed Git history")
        return errors
    if _sha256_bytes(raw) != approval_sha:
        errors.append("retrospective approval SHA-256 does not match committed Git history")
        return errors
    try:
        approval = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append("retrospective approval is not valid committed UTF-8 JSON")
        return errors
    required_flags = {
        "schema_version": "food_line_retrospective_approval_v4",
        "approval_type": "migrated_event_retrospective_batch",
        "edition_date": declared_date.isoformat(),
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
        "daily_collection_authorized": False,
        "source_configuration_change_authorized": False,
        "audio_authorized": False,
        "audio_policy": "existing_optional_audio_explicitly_not_authorized",
        "executed": False,
        "published": False,
    }
    if not isinstance(approval, dict) or any(approval.get(key) != value for key, value in required_flags.items()):
        errors.append("retrospective approval schema, edition, or authority flags are invalid")
    if isinstance(approval, dict) and v3_approval_path and approval.get("batch_id") != v3_approval_path.group(1):
        errors.append("retrospective approval path does not match its bound batch ID")
    if str(approval.get("pages_head") or "").lower() != pages_head:
        errors.append("retrospective release Pages binding does not match its committed approval")
    actual_pages_head = _git_run(pages_repo_root, "rev-parse", "HEAD").stdout.strip().lower()
    if pages_head != actual_pages_head:
        errors.append("retrospective release Pages checkout drifted from the approved head")
    sources_path = source_repo_root / "output" / "site" / "food-line" / "editions" / declared_date.isoformat() / "sources_manifest.json"
    edition_path = source_repo_root / "output" / "site" / "food-line" / "editions" / declared_date.isoformat() / "index.html"
    edition_manifest_path = source_repo_root / "output" / "site" / "food-line" / "editions" / declared_date.isoformat() / "edition_manifest.json"
    try:
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        edition_manifest = json.loads(edition_manifest_path.read_text(encoding="utf-8"))
        edition_html = edition_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append("retrospective generated public-copy surfaces are missing or invalid")
        return errors
    if not isinstance(sources, list) or len(sources) != approval.get("story_count"):
        errors.append("retrospective generated story inventory does not match the approval")
        return errors
    rendered_copies: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            errors.append("retrospective generated source inventory contains a malformed row")
            return errors
        copy = {
            "headline": str(source.get("title") or ""),
            "summary": str(source.get("summary_or_snippet") or ""),
            "source_links": list(source.get("source_links") or []),
        }
        if not copy["headline"] or not copy["summary"] or not copy["source_links"]:
            errors.append("retrospective generated public copy is incomplete")
            return errors
        rendered_copies.append(copy)
        if html.escape(copy["headline"]) not in edition_html or html.escape(copy["summary"]) not in edition_html:
            errors.append("retrospective edition HTML does not contain its exact approved public copy")
            return errors
    rendered_hash = "sha256:" + _sha256_bytes(
        (json.dumps(rendered_copies, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    expected_rendered_hash = str(approval.get("ordered_rendered_copy_sha256") or "")
    if rendered_hash != expected_rendered_hash:
        errors.append("retrospective generated public copy does not match the committed approval")
    exact_manifest = {
        "approval_commit": approval_commit,
        "approval_path": approval_path,
        "approval_sha256": approval_sha,
        "ordered_rendered_copy_sha256": expected_rendered_hash,
        "edition_date": declared_date.isoformat(),
        "published_at": str(payload.get("publication_timestamp") or ""),
        "retrospective_disclosure": approval.get("retrospective_disclosure"),
    }
    if not isinstance(edition_manifest, dict) or any(edition_manifest.get(key) != value for key, value in exact_manifest.items()):
        errors.append("retrospective edition manifest drifted from the committed approval or release timestamp")
    if html.escape(str(approval.get("retrospective_disclosure") or "")) not in edition_html:
        errors.append("retrospective edition HTML is missing the exact approved disclosure")
    return errors


def _release_manifest_delta(
    *,
    manifest_path: Path,
    dispatch: str,
    declared_date: dt.date | None,
    source_repo_root: Path,
    pages_repo_root: Path | None,
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    source_paths: list[str] = []
    pages_paths: list[str] = []
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
        errors.append("release manifest deletions are not authorized for this Food Line release")
    if payload.get("shared_files") not in ([], None):
        errors.append("release manifest shared files require a separate explicit authorization")
    source_commit = str(payload.get("source_commit") or "").strip()
    if not source_commit:
        errors.append("release manifest source_commit is required")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("release manifest must contain a non-empty entries list")
        return source_paths, pages_paths, errors
    retrospective_role_requested = any(
        isinstance(entry, dict) and entry.get("provenance_role") == "approved_retrospective_generated_output"
        for entry in entries
    )
    retrospective_authority_errors: list[str] = []
    if retrospective_role_requested:
        retrospective_authority_errors = _validate_retrospective_release_authority(
            payload=payload,
            dispatch=dispatch,
            declared_date=declared_date,
            source_repo_root=source_repo_root,
            pages_repo_root=pages_repo_root,
        )
        errors.extend(retrospective_authority_errors)
        if pages_repo_root is not None:
            from bluefern_dispatches.food_line_retrospective import (
                FoodLineRetrospectiveError,
                assert_retrospective_history_monotonic,
            )
            try:
                assert_retrospective_history_monotonic(
                    pages_repo_root,
                    source_repo_root / "output" / "site",
                    edition_dates=[declared_date.isoformat()] if declared_date is not None else [],
                )
            except FoodLineRetrospectiveError as exc:
                errors.append(str(exc))
    seen_source: set[str] = set()
    seen_pages: set[str] = set()
    source_root = source_repo_root.resolve()
    pages_root = pages_repo_root.resolve() if pages_repo_root is not None else None
    runtime_editorial_prefixes = (
        "data/dispatches/food-line/review/proposed-editions/",
        "data/dispatches/food-line/review/signal-reviews/",
    )
    runtime_editorial_inputs = (
        (
            _normalize_path(str(payload.get("approved_proposal_path") or "")),
            str(payload.get("approved_proposal_sha256") or "").lower(),
        ),
        (
            _normalize_path(str(payload.get("review_snapshot_path") or "")),
            str(payload.get("review_snapshot_sha256") or "").lower(),
        ),
    )
    for source_rel, expected_sha in runtime_editorial_inputs:
        if not source_rel:
            continue
        source_file = (source_root / source_rel).resolve()
        try:
            source_file.relative_to(source_root)
        except ValueError:
            errors.append(f"release manifest runtime editorial input resolves outside the source repo: {source_rel}")
            continue
        if not source_file.is_file():
            errors.append(f"release manifest runtime editorial input is missing in the working tree: {source_rel}")
            continue
        actual_sha = _sha256_file(source_file)
        if not expected_sha:
            errors.append(f"release manifest is missing a recorded runtime editorial hash for {source_rel}")
        elif expected_sha != actual_sha:
            errors.append(
                f"release manifest runtime editorial hash mismatch for {source_rel} "
                f"(expected={expected_sha}, actual={actual_sha})"
            )
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
        if str(entry.get("source_sha256") or "").lower() != actual_source_sha:
            errors.append(f"release manifest source SHA-256 mismatch: {source_rel}")
        is_runtime_editorial = dispatch == "food-line" and any(source_rel.startswith(prefix) for prefix in runtime_editorial_prefixes)
        if is_runtime_editorial:
            if provenance_role != "runtime_editorial":
                errors.append(
                    f"release manifest runtime editorial input must declare provenance_role=runtime_editorial: {source_rel}"
                )
            if not source_file.exists():
                errors.append(f"release manifest runtime editorial input is missing in the working tree: {source_rel}")
        else:
            if provenance_role not in {"generated_output", "approved_retrospective_generated_output"}:
                errors.append(f"release manifest entry {source_rel} has unknown provenance_role: {provenance_role or '<missing>'}")
            if provenance_role == "approved_retrospective_generated_output":
                if retrospective_authority_errors:
                    errors.append(f"retrospective generated output lacks valid committed authority: {source_rel}")
            elif source_commit and _git_commit_exists(source_root, source_commit):
                file_bytes = _git_file_bytes(source_root, source_commit, source_rel)
                if file_bytes is None:
                    errors.append(
                        f"release manifest source-input file is missing at source_commit: {source_rel} "
                        f"(role=source_input, source_commit={source_commit})"
                    )
                else:
                    commit_sha = _sha256_bytes(file_bytes)
                    if commit_sha != actual_source_sha:
                        errors.append(
                            f"release manifest source-input hash mismatch for {source_rel} "
                            f"(role=source_input, expected={actual_source_sha}, actual={commit_sha}, source_commit={source_commit})"
                        )
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

    if dispatch == "food-line" and declared_date is not None:
        edition_dir = source_root / "output" / "site" / "food-line" / "editions" / declared_date.isoformat()
        expected_paths = {
            "output/site/food-line/index.html",
            "output/site/food-line/archive.html",
            "output/site/food-line/rss.xml",
        }
        if edition_dir.is_dir():
            expected_paths.update(path.relative_to(source_root).as_posix() for path in edition_dir.rglob("*") if path.is_file())
        missing = sorted(expected_paths - set(source_paths))
        extra = sorted(set(source_paths) - expected_paths)
        if missing:
            errors.append("release manifest omits generated Food Line publication files: " + ", ".join(missing))
        if extra:
            errors.append("release manifest contains unexpected Food Line publication files: " + ", ".join(extra))
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
