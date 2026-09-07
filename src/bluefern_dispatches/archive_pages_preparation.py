from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PREPARATION_SCHEMA = "bluefern.dispatch_archive_pages_preparation.v1"
PREPARATION_MODE = "dispatch_archive_pages_preparation"
EXPECTED_PAGES_BRANCH = "gh-pages"
EXPECTED_CNAME = "dispatches.thebluefernco.com"
EXPECTED_REMOTE_SLUG = "RedGarland/the-blue-fern-co-dispatches"
SUPPORTED_DISPATCHES = ("food-line", "care-line")
RECEIPT_ROOT = Path("data") / "dispatches" / "archive-pages-preparations"


class ArchivePagesPreparationError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveCopyTarget:
    dispatch: str
    source_rel: str
    pages_rel: str
    source_path: Path
    pages_path: Path
    source_sha256: str


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _normalize_remote_url(value: str) -> str:
    text = value.strip().removesuffix(".git").replace("\\", "/").rstrip("/")
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text.removeprefix("git@github.com:")
    return text.lower()


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ArchivePagesPreparationError(detail)
    return result


def _git_stdout(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _git_status_paths(repo: Path) -> list[str]:
    raw = _git_stdout(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not raw:
        return []
    records = [part for part in raw.split("\0") if part]
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        status = record[:2]
        payload = record[3:] if len(record) > 3 and record[2] == " " else record[2:]
        paths.append(payload.replace("\\", "/").lstrip("./"))
        if status and status[0] in {"R", "C"} and index + 1 < len(records):
            paths.append(records[index + 1].replace("\\", "/").lstrip("./"))
            index += 2
        else:
            index += 1
    return paths


def _staged_paths(repo: Path) -> list[str]:
    raw = _git_stdout(repo, "diff", "--cached", "--name-only", "-z")
    return sorted(part.replace("\\", "/").lstrip("./") for part in raw.split("\0") if part)


def _assert_no_git_operation_in_progress(repo: Path) -> None:
    git_dir = Path(_git_stdout(repo, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    blockers = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "BISECT_LOG")
    present = [name for name in blockers if (git_dir / name).exists()]
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        present.append("rebase")
    if present:
        raise ArchivePagesPreparationError("Pages repo has unresolved Git operation: " + ", ".join(sorted(set(present))))


def _assert_expected_origin(pages_repo: Path, expected_remote_slug: str) -> str:
    remote = _git_stdout(pages_repo, "remote", "get-url", "origin")
    normalized = _normalize_remote_url(remote)
    expected = expected_remote_slug.lower().removesuffix(".git").strip("/")
    if expected not in normalized:
        raise ArchivePagesPreparationError("Pages origin remote is not the expected Pages repository")
    return remote


def _assert_pages_origin_synced(pages_repo: Path, pages_branch: str) -> str:
    _git(pages_repo, "fetch", "origin", pages_branch)
    remote_ref = f"origin/{pages_branch}"
    remote_head = _git_stdout(pages_repo, "rev-parse", remote_ref)
    local_head = _git_stdout(pages_repo, "rev-parse", "HEAD")
    if local_head != remote_head:
        counts = _git_stdout(pages_repo, "rev-list", "--left-right", "--count", f"{remote_ref}...HEAD")
        raise ArchivePagesPreparationError(f"Pages local {pages_branch} must match {remote_ref} before preparation: {counts}")
    return remote_head


def _assert_pages_repo(pages_repo: Path, *, expected_remote_slug: str, pages_branch: str) -> dict[str, Any]:
    if not pages_repo.exists() or not pages_repo.is_dir():
        raise ArchivePagesPreparationError("Pages repo does not exist")
    try:
        inside = _git_stdout(pages_repo, "rev-parse", "--is-inside-work-tree")
    except ArchivePagesPreparationError as exc:
        raise ArchivePagesPreparationError("Pages repo is not a Git repository") from exc
    if inside != "true":
        raise ArchivePagesPreparationError("Pages repo is not a Git repository")
    branch = _git_stdout(pages_repo, "branch", "--show-current")
    if branch != pages_branch:
        raise ArchivePagesPreparationError(f"Pages repo must be on {pages_branch}")
    remote = _assert_expected_origin(pages_repo, expected_remote_slug)
    _assert_no_git_operation_in_progress(pages_repo)
    dirty = _git_status_paths(pages_repo)
    if dirty:
        raise ArchivePagesPreparationError("Pages repo has unexpected dirty paths: " + ", ".join(dirty))
    remote_head = _assert_pages_origin_synced(pages_repo, pages_branch)
    cname = pages_repo / "CNAME"
    if not cname.is_file():
        raise ArchivePagesPreparationError("Pages CNAME is missing")
    cname_value = cname.read_text(encoding="utf-8").strip()
    if cname_value != EXPECTED_CNAME:
        raise ArchivePagesPreparationError("Pages CNAME has an unexpected value")
    return {
        "branch": branch,
        "origin": remote,
        "head": _git_stdout(pages_repo, "rev-parse", "HEAD"),
        "remote_head": remote_head,
        "cname_sha256": _sha256_file(cname),
    }


def _repo_relative(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if root == resolved or root not in resolved.parents:
        raise ArchivePagesPreparationError(f"{label} resolves outside repository")
    return resolved.relative_to(root)


def _dispatches_from_arg(dispatch: str) -> tuple[str, ...]:
    if dispatch == "both":
        return SUPPORTED_DISPATCHES
    if dispatch not in SUPPORTED_DISPATCHES:
        raise ArchivePagesPreparationError("dispatch must be food-line, care-line, or both")
    return (dispatch,)


def _assert_valid_archive_identity(source_path: Path, dispatch: str) -> None:
    if not source_path.is_file():
        raise ArchivePagesPreparationError(f"source archive is missing: {source_path}")
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ArchivePagesPreparationError(f"source archive is not readable HTML: {source_path}") from exc
    if "<html" not in text.lower() or "</html>" not in text.lower():
        raise ArchivePagesPreparationError(f"source archive is not valid readable HTML: {source_path}")
    canonical_match = re.search(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', text, re.I)
    expected_url = f"https://dispatches.thebluefernco.com/{dispatch}/archive.html"
    if canonical_match and canonical_match.group(1).strip() != expected_url:
        raise ArchivePagesPreparationError("source archive canonical identity does not match requested dispatch")
    if expected_url not in text and f"/{dispatch}/archive.html" not in text and f"/{dispatch}/" not in text:
        raise ArchivePagesPreparationError("source archive dispatch identity does not match requested dispatch")


def _assert_publish_scope(dispatch: str, source_rel: str, pages_rel: str) -> None:
    from scripts.validate_publish_scope import validate_publish_scope

    errors = validate_publish_scope(
        dispatch=dispatch,
        source_artifact_family="dispatch-archive",
        source_changed_paths=[source_rel],
        pages_changed_paths=[pages_rel],
        strict=True,
    )
    if errors:
        raise ArchivePagesPreparationError("publish scope validation failed: " + "; ".join(errors))


def _target_for(root: Path, pages_repo: Path, dispatch: str) -> ArchiveCopyTarget:
    source_rel = f"output/site/{dispatch}/archive.html"
    pages_rel = f"{dispatch}/archive.html"
    source_path = (root / source_rel).resolve()
    pages_path = (pages_repo / pages_rel).resolve()
    _repo_relative(root, source_path, "source archive path")
    try:
        pages_path.relative_to(pages_repo.resolve())
    except ValueError as exc:
        raise ArchivePagesPreparationError("Pages destination resolves outside repository") from exc
    _assert_valid_archive_identity(source_path, dispatch)
    _assert_publish_scope(dispatch, source_rel, pages_rel)
    return ArchiveCopyTarget(
        dispatch=dispatch,
        source_rel=source_rel,
        pages_rel=pages_rel,
        source_path=source_path,
        pages_path=pages_path,
        source_sha256=_sha256_file(source_path),
    )


def _assert_pages_diff_exact(pages_repo: Path, expected_pages_paths: Sequence[str]) -> list[str]:
    changed = sorted(_git_status_paths(pages_repo))
    expected = sorted(path.replace("\\", "/").lstrip("./") for path in expected_pages_paths)
    extra = sorted(set(changed) - set(expected))
    if extra:
        raise ArchivePagesPreparationError(
            "Pages diff is outside the exact archive scope: "
            f"expected only {', '.join(expected) or '<none>'}; actual extra {', '.join(extra)}"
        )
    if "CNAME" in changed:
        raise ArchivePagesPreparationError("Pages archive preparation must not modify CNAME")
    return changed


def _restore_pages(targets: Sequence[ArchiveCopyTarget], originals: dict[str, bytes | None]) -> None:
    for target in targets:
        original = originals.get(target.pages_rel)
        if original is None:
            if target.pages_path.exists():
                target.pages_path.unlink()
            continue
        target.pages_path.parent.mkdir(parents=True, exist_ok=True)
        target.pages_path.write_bytes(original)


def _commit_message(dispatches: Sequence[str]) -> str:
    if tuple(dispatches) == ("food-line", "care-line"):
        return "Publish Food and Care archive updates"
    if dispatches == ("food-line",):
        return "Publish Food Line archive update"
    if dispatches == ("care-line",):
        return "Publish Care Line archive update"
    return "Publish dispatch archive updates"


def _write_preparation_receipt(
    root: Path,
    *,
    dispatches: Sequence[str],
    targets: Sequence[ArchiveCopyTarget],
    pages_before: dict[str, Any],
    pages_commit_sha: str | None,
    destination_hashes: dict[str, str],
    cname_sha_after: str,
    status: str,
    committed: bool,
) -> tuple[Path, dict[str, Any]]:
    slug = "food-care" if tuple(dispatches) == ("food-line", "care-line") else dispatches[0]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_rel = RECEIPT_ROOT / f"{slug}-{timestamp}.json"
    payload: dict[str, Any] = {
        "schema_version": PREPARATION_SCHEMA,
        "preparation_mode": PREPARATION_MODE,
        "dispatches": list(dispatches),
        "source_head": _git_stdout(root, "rev-parse", "HEAD"),
        "source_archive_paths": [target.source_rel for target in targets],
        "source_archive_sha256": {target.source_rel: target.source_sha256 for target in targets},
        "destination_archive_paths": [target.pages_rel for target in targets],
        "destination_archive_sha256": destination_hashes,
        "pages_head_before": pages_before["head"],
        "pages_origin_head_before": pages_before["remote_head"],
        "pages_commit_sha": pages_commit_sha,
        "pages_local_commit_created": committed,
        "cname_sha256_before": pages_before["cname_sha256"],
        "cname_sha256_after": cname_sha_after,
        "pages_push_performed": False,
        "deployment_status": status,
    }
    _write_json(root / receipt_rel, payload)
    return receipt_rel, payload


def prepare_dispatch_archive_pages(
    *,
    root: Path,
    dispatch: str,
    pages_repo: Path,
    commit: bool = False,
    pages_branch: str = EXPECTED_PAGES_BRANCH,
    expected_remote_slug: str = EXPECTED_REMOTE_SLUG,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    pages_repo = pages_repo.resolve(strict=True)
    dispatches = _dispatches_from_arg(dispatch)
    pages_before = _assert_pages_repo(pages_repo, expected_remote_slug=expected_remote_slug, pages_branch=pages_branch)
    targets = [_target_for(root, pages_repo, item) for item in dispatches]
    expected_pages_paths = [target.pages_rel for target in targets]
    originals = {target.pages_rel: target.pages_path.read_bytes() if target.pages_path.exists() else None for target in targets}
    pre_hashes = {target.pages_rel: _sha256_file(target.pages_path) if target.pages_path.exists() else None for target in targets}
    no_op = all(pre_hashes[target.pages_rel] == target.source_sha256 for target in targets)
    changed: list[str] = []
    destination_hashes: dict[str, str] = {}
    pages_commit_sha: str | None = None
    committed = False
    status = "prepared_already_current" if no_op else ("prepared_not_pushed" if commit else "prepared_not_committed")
    try:
        if not no_op:
            for target in targets:
                target.pages_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target.source_path, target.pages_path)
                dest_hash = _sha256_file(target.pages_path)
                if dest_hash != target.source_sha256:
                    raise ArchivePagesPreparationError(f"source/destination hash mismatch after copy: {target.pages_rel}")
                destination_hashes[target.pages_rel] = dest_hash
            changed = _assert_pages_diff_exact(pages_repo, expected_pages_paths)
        else:
            destination_hashes = {target.pages_rel: target.source_sha256 for target in targets}
            changed = []

        cname_after = _sha256_file(pages_repo / "CNAME")
        if cname_after != pages_before["cname_sha256"]:
            raise ArchivePagesPreparationError("Pages CNAME changed during archive preparation")

        if commit and changed:
            _git(pages_repo, "add", "--", *expected_pages_paths)
            staged = _staged_paths(pages_repo)
            if staged != sorted(changed):
                raise ArchivePagesPreparationError(
                    "Pages staged diff is outside the exact archive scope: "
                    f"expected {', '.join(sorted(changed))}; staged {', '.join(staged) or '<none>'}"
                )
            _git(pages_repo, "commit", "-m", _commit_message(dispatches))
            pages_commit_sha = _git_stdout(pages_repo, "rev-parse", "HEAD")
            if pages_commit_sha == pages_before["head"]:
                raise ArchivePagesPreparationError("Pages commit did not advance HEAD")
            if _git_status_paths(pages_repo):
                raise ArchivePagesPreparationError("Pages repo is dirty after local archive commit")
            committed = True
        elif commit:
            pages_commit_sha = pages_before["head"]

        receipt_rel, _ = _write_preparation_receipt(
            root,
            dispatches=dispatches,
            targets=targets,
            pages_before=pages_before,
            pages_commit_sha=pages_commit_sha,
            destination_hashes=destination_hashes,
            cname_sha_after=cname_after,
            status=status,
            committed=committed,
        )
        return {
            "ok": True,
            "schema_version": PREPARATION_SCHEMA,
            "status": status,
            "dispatches": list(dispatches),
            "source_archive_paths": [target.source_rel for target in targets],
            "destination_archive_paths": expected_pages_paths,
            "source_archive_sha256": {target.source_rel: target.source_sha256 for target in targets},
            "destination_archive_sha256": destination_hashes,
            "pages_head_before": pages_before["head"],
            "pages_origin_head_before": pages_before["remote_head"],
            "pages_commit_sha": pages_commit_sha,
            "pages_local_commit_created": committed,
            "pages_changed_paths": changed,
            "cname_sha256_before": pages_before["cname_sha256"],
            "cname_sha256_after": cname_after,
            "pages_push_performed": False,
            "deployment_status": status,
            "preparation_receipt_path": receipt_rel.as_posix(),
        }
    except Exception:
        _restore_pages(targets, originals)
        if _staged_paths(pages_repo):
            _git(pages_repo, "restore", "--staged", "--", *expected_pages_paths, check=False)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare exact-scope Food/Care archive.html updates in a local Pages repo.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dispatch", choices=("food-line", "care-line", "both"), required=True)
    parser.add_argument("--pages-repo", type=Path, required=True)
    parser.add_argument("--commit", action="store_true", help="Create a local gh-pages commit. This owner never pushes.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = prepare_dispatch_archive_pages(
            root=args.repo_root,
            dispatch=args.dispatch,
            pages_repo=args.pages_repo,
            commit=bool(args.commit),
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports fail-closed reason
        print(json.dumps({"ok": False, "errors": [str(exc)], "pages_push_performed": False}, indent=2), flush=True)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
