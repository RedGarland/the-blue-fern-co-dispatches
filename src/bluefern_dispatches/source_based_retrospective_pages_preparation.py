from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from bluefern_dispatches.source_based_retrospective_public_generation import (
    MANIFEST_SCHEMA as GENERATION_MANIFEST_SCHEMA,
    validate_generation_manifest,
)


PREPARATION_SCHEMA = "bluefern.source_based_retrospective_pages_preparation.v1"
PREPARATION_MODE = "source_based_retrospective_pages_preparation"
EXPECTED_PAGES_BRANCH = "gh-pages"
EXPECTED_CNAME = "dispatches.thebluefernco.com"
EXPECTED_REMOTE_SLUG = "RedGarland/the-blue-fern-co-dispatches"
SUPPORTED_DISPATCHES = {"food-line", "care-line"}
RECEIPT_ROOT = Path("data") / "dispatches"


class SourceBasedRetrospectivePagesPreparationError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBasedRetrospectivePagesPreparationError(f"unable to read valid JSON: {path}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise SourceBasedRetrospectivePagesPreparationError(detail)
    return result


def _git_stdout(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _repo_relative(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if root == resolved or root not in resolved.parents:
        raise SourceBasedRetrospectivePagesPreparationError(f"{label} resolves outside repository")
    return resolved.relative_to(root)


def _safe_slug(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise SourceBasedRetrospectivePagesPreparationError(f"{label} must be a lowercase slug")
    return text


def _normalize_remote_url(value: str) -> str:
    text = value.strip().removesuffix(".git").replace("\\", "/").rstrip("/")
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text.removeprefix("git@github.com:")
    return text.lower()


def _assert_expected_origin(pages_repo: Path, expected_remote_slug: str) -> str:
    remote = _git_stdout(pages_repo, "remote", "get-url", "origin")
    normalized = _normalize_remote_url(remote)
    expected = expected_remote_slug.lower().removesuffix(".git").strip("/")
    if expected not in normalized:
        raise SourceBasedRetrospectivePagesPreparationError("Pages origin remote is not the expected Pages repository")
    return remote


def _assert_no_git_operation_in_progress(repo: Path) -> None:
    git_dir = Path(_git_stdout(repo, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    blockers = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "BISECT_LOG")
    present = [name for name in blockers if (git_dir / name).exists()]
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        present.append("rebase")
    if present:
        raise SourceBasedRetrospectivePagesPreparationError("Pages repo has unresolved Git operation: " + ", ".join(sorted(set(present))))


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


def _assert_clean_pages_repo(pages_repo: Path) -> None:
    dirty = _git_status_paths(pages_repo)
    if dirty:
        raise SourceBasedRetrospectivePagesPreparationError("Pages repo has unexpected dirty paths: " + ", ".join(dirty))


def _assert_pages_repo(pages_repo: Path, *, expected_remote_slug: str, pages_branch: str) -> dict[str, Any]:
    if not pages_repo.exists() or not pages_repo.is_dir():
        raise SourceBasedRetrospectivePagesPreparationError("Pages repo does not exist")
    try:
        inside = _git_stdout(pages_repo, "rev-parse", "--is-inside-work-tree")
    except SourceBasedRetrospectivePagesPreparationError as exc:
        raise SourceBasedRetrospectivePagesPreparationError("Pages repo is not a Git repository") from exc
    if inside != "true":
        raise SourceBasedRetrospectivePagesPreparationError("Pages repo is not a Git repository")
    branch = _git_stdout(pages_repo, "branch", "--show-current")
    if branch != pages_branch:
        raise SourceBasedRetrospectivePagesPreparationError(f"Pages repo must be on {pages_branch}")
    remote = _assert_expected_origin(pages_repo, expected_remote_slug)
    _assert_no_git_operation_in_progress(pages_repo)
    _assert_clean_pages_repo(pages_repo)
    cname = pages_repo / "CNAME"
    if not cname.is_file():
        raise SourceBasedRetrospectivePagesPreparationError("Pages CNAME is missing")
    cname_value = cname.read_text(encoding="utf-8").strip()
    if cname_value != EXPECTED_CNAME:
        raise SourceBasedRetrospectivePagesPreparationError("Pages CNAME has an unexpected value")
    return {
        "branch": branch,
        "origin": remote,
        "head": _git_stdout(pages_repo, "rev-parse", "HEAD"),
        "cname_sha256": _sha256_file(cname),
    }


def _destination_for(dispatch: str, batch_id: str, source_rel: str) -> str:
    prefix = f"output/site/{dispatch}/source-based-retrospectives/{batch_id}/"
    if not source_rel.startswith(prefix):
        raise SourceBasedRetrospectivePagesPreparationError(f"generated artifact is outside owned public root: {source_rel}")
    leaf = source_rel.removeprefix(prefix)
    if leaf not in {"index.html", "items.json"}:
        raise SourceBasedRetrospectivePagesPreparationError(f"generated artifact is not an allowed public file: {source_rel}")
    return f"{dispatch}/source-based-retrospectives/{batch_id}/{leaf}"


def _validate_generation_receipt(root: Path, receipt_path: Path, *, dispatch: str, publication_batch_id: str) -> tuple[dict[str, Any], Path, str]:
    path = receipt_path if receipt_path.is_absolute() else root / receipt_path
    rel = _repo_relative(root, path, "generation receipt path")
    validation = validate_generation_manifest(root, rel)
    if not validation.get("ok"):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt validation failed: " + "; ".join(validation.get("errors") or []))
    receipt = _read_json(path)
    if not isinstance(receipt, dict):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt must be a JSON object")
    if receipt.get("schema_version") != GENERATION_MANIFEST_SCHEMA:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt schema is invalid")
    if receipt.get("dispatch") != dispatch:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt dispatch mismatch")
    if receipt.get("publication_batch_id") != publication_batch_id:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt publication batch mismatch")
    source_head = str(receipt.get("source_head") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source_head):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt source_head is missing or invalid")
    current_head = _git_stdout(root, "rev-parse", "HEAD")
    if current_head != source_head:
        raise SourceBasedRetrospectivePagesPreparationError("source HEAD does not match generation receipt binding")
    publication_rel_text = str(receipt.get("publication_authorization_path") or "").strip()
    publication_sha = str(receipt.get("publication_authorization_sha256") or "").strip()
    if not publication_rel_text or not publication_sha:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt lacks publication authorization lineage")
    publication_rel = Path(publication_rel_text)
    if publication_rel.is_absolute():
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage path must be repository-relative")
    if publication_rel.parts[:3] != ("publication-authorizations", dispatch, "source-based-retrospectives"):
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage is outside the dispatch owner")
    publication_path = (root / publication_rel).resolve()
    try:
        publication_path.relative_to(root)
    except ValueError as exc:
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage escapes repository") from exc
    if not publication_path.is_file():
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage file is missing")
    if _sha256_file(publication_path) != publication_sha:
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage hash mismatch")
    publication_payload = _read_json(publication_path)
    if not isinstance(publication_payload, dict) or publication_payload.get("dispatch") != dispatch:
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage dispatch mismatch")
    if publication_payload.get("publication_batch_id") != publication_batch_id:
        raise SourceBasedRetrospectivePagesPreparationError("publication authorization lineage batch mismatch")
    if receipt.get("authorized_item_count") != receipt.get("rendered_item_count"):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt authorized/rendered count mismatch")
    if receipt.get("skipped_item_count") != 0 or receipt.get("unauthorized_item_count") != 0:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt records skipped or unauthorized items")
    if not receipt.get("source_urls") or len(receipt["source_urls"]) != receipt.get("rendered_item_count"):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt source traceability count is incomplete")
    for field in ("pages_authorized", "pages_push_authorized", "social_authorized", "audio_authorized", "schedule_authorized", "scheduled_task_change_authorized"):
        if receipt.get(field) is not False:
            raise SourceBasedRetrospectivePagesPreparationError(f"{field} must remain false before Pages preparation")
    artifact_hashes = receipt.get("artifact_hashes")
    public_paths = receipt.get("generated_public_paths")
    if not isinstance(artifact_hashes, dict) or not isinstance(public_paths, list):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt must declare generated paths and hashes")
    public_artifacts = [str(path) for path in public_paths if str(path).startswith("output/site/")]
    if sorted(public_artifacts) != sorted(path for path in artifact_hashes if str(path).startswith("output/site/")):
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt public path/hash set mismatch")
    if set(Path(path).name for path in public_artifacts) != {"index.html", "items.json"} or len(public_artifacts) != 2:
        raise SourceBasedRetrospectivePagesPreparationError("generation receipt must declare exactly index.html and items.json public artifacts")
    for rel_text in public_artifacts:
        expected = str(artifact_hashes.get(rel_text) or "")
        actual = _sha256_file(root / rel_text)
        if actual != expected:
            raise SourceBasedRetrospectivePagesPreparationError(f"generated artifact hash mismatch: {rel_text}")
        _destination_for(dispatch, publication_batch_id, rel_text)
    return receipt, rel, _sha256_file(path)


def _assert_publish_scope(
    *,
    dispatch: str,
    publication_batch_id: str,
    source_paths: Sequence[str],
    pages_paths: Sequence[str],
) -> None:
    from scripts.validate_publish_scope import validate_publish_scope

    errors = validate_publish_scope(
        dispatch=dispatch,
        source_artifact_family="source-based-retrospective",
        publication_batch_id=publication_batch_id,
        source_changed_paths=source_paths,
        pages_changed_paths=pages_paths,
        strict=True,
    )
    if errors:
        raise SourceBasedRetrospectivePagesPreparationError("publish scope validation failed: " + "; ".join(errors))


def _copy_artifacts(root: Path, pages_repo: Path, *, dispatch: str, batch_id: str, public_paths: Sequence[str], hashes: dict[str, str]) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for source_rel in sorted(public_paths):
        dest_rel = _destination_for(dispatch, batch_id, source_rel)
        source = (root / source_rel).resolve()
        dest = (pages_repo / dest_rel).resolve()
        try:
            dest.relative_to(pages_repo.resolve())
        except ValueError as exc:
            raise SourceBasedRetrospectivePagesPreparationError(f"Pages destination escapes repository: {dest_rel}") from exc
        if _sha256_file(source) != hashes[source_rel]:
            raise SourceBasedRetrospectivePagesPreparationError(f"generated artifact hash mismatch before copy: {source_rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest_hash = _sha256_file(dest)
        if dest_hash != hashes[source_rel]:
            raise SourceBasedRetrospectivePagesPreparationError(f"Pages destination hash mismatch after copy: {dest_rel}")
        copied.append({"source_path": source_rel, "pages_path": dest_rel, "sha256": dest_hash})
    return copied


def _assert_pages_diff_exact(pages_repo: Path, expected_pages_paths: Sequence[str]) -> list[str]:
    changed = sorted(_git_status_paths(pages_repo))
    expected = sorted(path.replace("\\", "/").lstrip("./") for path in expected_pages_paths)
    extra = sorted(set(changed) - set(expected))
    if extra:
        raise SourceBasedRetrospectivePagesPreparationError(
            "Pages diff is outside the exact prepared scope: "
            f"expected only {', '.join(expected) or '<none>'}; actual extra {', '.join(extra)}"
        )
    if "CNAME" in changed:
        raise SourceBasedRetrospectivePagesPreparationError("Pages preparation must not modify CNAME")
    return changed


def _write_preparation_receipt(
    root: Path,
    *,
    dispatch: str,
    batch_id: str,
    generation_receipt_rel: Path,
    generation_receipt_sha: str,
    pages_before: dict[str, Any],
    pages_commit_sha: str | None,
    copied: list[dict[str, str]],
    cname_sha_after: str,
    committed: bool,
    deployment_status: str,
) -> tuple[Path, dict[str, Any]]:
    receipt_rel = RECEIPT_ROOT / dispatch / "review" / "source-based-retrospective-pages-preparations" / f"{batch_id}.json"
    payload: dict[str, Any] = {
        "schema_version": PREPARATION_SCHEMA,
        "preparation_mode": PREPARATION_MODE,
        "dispatch": dispatch,
        "publication_batch_id": batch_id,
        "generation_receipt_path": generation_receipt_rel.as_posix(),
        "generation_receipt_sha256": generation_receipt_sha,
        "generated_artifact_hashes": {row["source_path"]: row["sha256"] for row in copied},
        "pages_repo_head_before": pages_before["head"],
        "pages_commit_sha_after": pages_commit_sha,
        "pages_local_commit_created": committed,
        "destination_paths": [row["pages_path"] for row in copied],
        "destination_hashes": {row["pages_path"]: row["sha256"] for row in copied},
        "cname_sha256_before": pages_before["cname_sha256"],
        "cname_sha256_after": cname_sha_after,
        "pages_push_performed": False,
        "deployment_status": deployment_status,
    }
    _write_json(root / receipt_rel, payload)
    return receipt_rel, payload


def prepare_pages_from_generation(
    root: Path,
    *,
    dispatch: str,
    generation_receipt_path: Path,
    publication_batch_id: str,
    pages_repo: Path,
    commit: bool = False,
    pages_branch: str = EXPECTED_PAGES_BRANCH,
    expected_remote_slug: str = EXPECTED_REMOTE_SLUG,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    pages_repo = pages_repo.resolve(strict=True)
    if pages_repo == root or root in pages_repo.parents:
        # A nested sibling named bluefern-dispatches-pages is expected; the guard
        # specifically rejects the source repository itself, not a child Pages repo.
        if pages_repo == root:
            raise SourceBasedRetrospectivePagesPreparationError("Pages repo cannot be the source repository")
    if dispatch not in SUPPORTED_DISPATCHES:
        raise SourceBasedRetrospectivePagesPreparationError("dispatch must be food-line or care-line")
    batch_id = _safe_slug(publication_batch_id, "publication_batch_id")
    receipt, receipt_rel, receipt_sha = _validate_generation_receipt(root, generation_receipt_path, dispatch=dispatch, publication_batch_id=batch_id)
    public_paths = sorted(path for path in receipt["generated_public_paths"] if str(path).startswith("output/site/"))
    pages_paths = [_destination_for(dispatch, batch_id, path) for path in public_paths]
    _assert_publish_scope(dispatch=dispatch, publication_batch_id=batch_id, source_paths=public_paths, pages_paths=pages_paths)
    pages_before = _assert_pages_repo(pages_repo, expected_remote_slug=expected_remote_slug, pages_branch=pages_branch)
    copied = _copy_artifacts(root, pages_repo, dispatch=dispatch, batch_id=batch_id, public_paths=public_paths, hashes=receipt["artifact_hashes"])
    changed = _assert_pages_diff_exact(pages_repo, pages_paths)
    cname_after = _sha256_file(pages_repo / "CNAME")
    if cname_after != pages_before["cname_sha256"]:
        raise SourceBasedRetrospectivePagesPreparationError("Pages CNAME changed during preparation")
    pages_commit_sha: str | None = None
    committed = False
    if commit and changed:
        for row in copied:
            _git(pages_repo, "add", "--", row["pages_path"])
        message = f"Publish {dispatch} source-based retrospective {batch_id}"
        _git(pages_repo, "commit", "-m", message)
        pages_commit_sha = _git_stdout(pages_repo, "rev-parse", "HEAD")
        if pages_commit_sha == pages_before["head"]:
            raise SourceBasedRetrospectivePagesPreparationError("Pages commit did not advance HEAD")
        if _git_status_paths(pages_repo):
            raise SourceBasedRetrospectivePagesPreparationError("Pages repo is dirty after local commit")
        committed = True
    elif commit:
        pages_commit_sha = pages_before["head"]
    deployment_status = "prepared_not_pushed" if committed else ("prepared_already_current" if commit else "prepared_not_committed")
    receipt_out_rel, prep_receipt = _write_preparation_receipt(
        root,
        dispatch=dispatch,
        batch_id=batch_id,
        generation_receipt_rel=receipt_rel,
        generation_receipt_sha=receipt_sha,
        pages_before=pages_before,
        pages_commit_sha=pages_commit_sha,
        copied=copied,
        cname_sha_after=cname_after,
        committed=committed,
        deployment_status=deployment_status,
    )
    return {
        "ok": True,
        "status": prep_receipt["deployment_status"],
        "dispatch": dispatch,
        "publication_batch_id": batch_id,
        "pages_head_before": pages_before["head"],
        "pages_commit_sha_after": pages_commit_sha,
        "pages_changed_paths": changed,
        "pages_push_performed": False,
        "preparation_receipt_path": receipt_out_rel.as_posix(),
        "preparation": prep_receipt,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare local Pages files from source-based retrospective generation receipts.")
    parser.add_argument("--repo-root", type=Path, required=True, help="Source repository root.")
    parser.add_argument("--dispatch", choices=sorted(SUPPORTED_DISPATCHES), required=True)
    parser.add_argument("--generation-receipt", type=Path, required=True)
    parser.add_argument("--publication-batch-id", required=True)
    parser.add_argument("--pages-repo", type=Path, required=True, help="Local Pages repository on gh-pages.")
    parser.add_argument("--pages-branch", default=EXPECTED_PAGES_BRANCH)
    parser.add_argument("--expected-remote-slug", default=EXPECTED_REMOTE_SLUG)
    parser.add_argument("--commit", action="store_true", help="Create the local Pages commit. No push capability is provided.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = prepare_pages_from_generation(
            args.repo_root,
            dispatch=args.dispatch,
            generation_receipt_path=args.generation_receipt,
            publication_batch_id=args.publication_batch_id,
            pages_repo=args.pages_repo,
            commit=args.commit,
            pages_branch=args.pages_branch,
            expected_remote_slug=args.expected_remote_slug,
        )
    except SourceBasedRetrospectivePagesPreparationError as exc:
        print(json.dumps({"ok": False, "status": "failed_closed", "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
