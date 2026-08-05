from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


RELEASE_SCHEMA = "care_line_release_manifest_v1"
PUBLIC_RELEASE_STATUS_FIELD = "public_release_status"
PAGES_RELEASE_STATUS_FIELD = "pages_release_status"
PENDING_PUBLIC_RELEASE_STATUS = "not_published"
PUBLISHED_PUBLIC_RELEASE_STATUS = "published"
PENDING_PAGES_RELEASE_STATUS = "not_synced"
SYNCED_PAGES_RELEASE_STATUS = "synced"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_deterministic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def initialize_public_release_status(manifest: dict[str, Any]) -> None:
    manifest[PUBLIC_RELEASE_STATUS_FIELD] = PENDING_PUBLIC_RELEASE_STATUS
    manifest[PAGES_RELEASE_STATUS_FIELD] = PENDING_PAGES_RELEASE_STATUS


def finalize_public_release_status(manifest: dict[str, Any]) -> bool:
    if str(manifest.get("generation_mode") or "").strip() != "approved_current_review_proposal":
        return False
    manifest[PUBLIC_RELEASE_STATUS_FIELD] = PUBLISHED_PUBLIC_RELEASE_STATUS
    manifest[PAGES_RELEASE_STATUS_FIELD] = SYNCED_PAGES_RELEASE_STATUS
    return True


def build_release_manifest(
    *,
    root: Path,
    pages_root: Path,
    edition_date: str,
    source_commit: str,
    source_paths: Sequence[Path],
    approved_proposal_path: str | None = None,
    approved_proposal_sha256: str | None = None,
    review_snapshot_path: str | None = None,
    review_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    pages_root = pages_root.resolve()
    entries: list[dict[str, Any]] = []
    for source_path in sorted({path.resolve() for path in source_paths}, key=lambda path: path.as_posix()):
        source_rel = source_path.relative_to(root).as_posix()
        if not source_rel.startswith("output/site/care-line/"):
            raise ValueError(f"release source path is outside Care Line public output: {source_rel}")
        pages_rel = source_rel.removeprefix("output/site/")
        target = pages_root / pages_rel
        source_sha = sha256_file(source_path)
        if not target.exists():
            action = "add"
            target_sha = None
        else:
            target_sha = sha256_file(target)
            action = "unchanged" if target_sha == source_sha else "modify"
        entries.append(
            {
                "source_path": source_rel,
                "pages_path": pages_rel,
                "action": action,
                "source_sha256": source_sha,
                "pages_sha256_before": target_sha,
            }
        )
    manifest = {
        "schema_version": RELEASE_SCHEMA,
        "dispatch": "care-line",
        "edition_date": edition_date,
        "source_commit": source_commit,
        "entries": entries,
        "deletions": [],
        "shared_files": [],
    }
    if approved_proposal_path:
        manifest["approved_proposal_path"] = str(approved_proposal_path)
    if approved_proposal_sha256:
        manifest["approved_proposal_sha256"] = str(approved_proposal_sha256)
    if review_snapshot_path:
        manifest["review_snapshot_path"] = str(review_snapshot_path)
    if review_snapshot_sha256:
        manifest["review_snapshot_sha256"] = str(review_snapshot_sha256)
    return manifest
