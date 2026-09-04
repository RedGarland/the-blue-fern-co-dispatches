from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for value in (str(ROOT), str(SRC)):
    if value not in sys.path:
        sys.path.insert(0, value)

from bluefern_dispatches.food_line_retrospective import (
    FoodLineRetrospectiveError,
    RetrospectiveBundle,
    load_retrospective_plan,
    record_retrospective_publication,
    verify_generated_retrospective_set,
)
from bluefern_dispatches.pages_release_safety import sync_pages_from_source
from scripts.run_food_line_dispatch import (
    generate_prevalidated_food_line_retrospective,
    refresh_food_line_retrospective_release_manifest,
)


def _load_batch_set(
    source_root: Path,
    pages_root: Path,
    *,
    approval_commits: Sequence[str],
    approval_paths: Sequence[str],
    publication_timestamp: str,
) -> list[RetrospectiveBundle]:
    if len(approval_commits) != len(approval_paths) or len(approval_paths) < 2:
        raise FoodLineRetrospectiveError("atomic retrospective publication requires matching bindings for at least two batches")
    bundles = [
        load_retrospective_plan(
            source_root,
            pages_root,
            approval_commit=commit,
            approval_path=path,
            publication_timestamp=publication_timestamp,
        )
        for commit, path in zip(approval_commits, approval_paths)
    ]
    bundles.sort(key=lambda bundle: bundle.edition_date)
    if len({bundle.edition_date for bundle in bundles}) != len(bundles):
        raise FoodLineRetrospectiveError("atomic retrospective batches must use distinct edition dates")
    if len({bundle.batch_id for bundle in bundles}) != len(bundles):
        raise FoodLineRetrospectiveError("atomic retrospective batch identities must be distinct")
    if len({bundle.pages_head for bundle in bundles}) != 1 or len({bundle.source_head for bundle in bundles}) != 1:
        raise FoodLineRetrospectiveError("atomic retrospective batches must bind one source head and one pre-publish Pages head")
    event_fingerprints = [
        str(binding.get("event_fingerprint") or "")
        for bundle in bundles
        for binding in bundle.decision_bindings
    ]
    if len(event_fingerprints) != len(set(event_fingerprints)):
        raise FoodLineRetrospectiveError("atomic retrospective batches contain an overlapping event")
    return bundles


def run_atomic_retrospective_batches(
    *,
    source_root: Path,
    pages_root: Path,
    source_branch: str,
    pages_branch: str,
    approval_commits: Sequence[str],
    approval_paths: Sequence[str],
    publication_timestamp: str,
    commit_pages: bool,
    push_pages: bool,
    live_check: bool,
    record_publication: bool,
) -> dict[str, object]:
    source_root = source_root.resolve()
    pages_root = pages_root.resolve()
    if push_pages and not commit_pages:
        raise FoodLineRetrospectiveError("Pages push requires one atomic Pages commit")
    if live_check and not push_pages:
        raise FoodLineRetrospectiveError("live verification requires a pushed Pages commit")
    if record_publication and not (push_pages and live_check):
        raise FoodLineRetrospectiveError("durable recording requires pushed and live-verified Pages output")
    bundles = _load_batch_set(
        source_root,
        pages_root,
        approval_commits=approval_commits,
        approval_paths=approval_paths,
        publication_timestamp=publication_timestamp,
    )
    generated = [generate_prevalidated_food_line_retrospective(source_root, pages_root, bundle) for bundle in bundles]
    manifests = [refresh_food_line_retrospective_release_manifest(source_root, pages_root, bundle) for bundle in bundles]
    verified_set = verify_generated_retrospective_set(source_root, pages_root, bundles)
    verified = verified_set["verification_results"]
    pages_result = sync_pages_from_source(
        dispatch="food-line",
        dates=[bundle.edition_date for bundle in bundles],
        require_source_branch=source_branch,
        pages_branch=pages_branch,
        source_repo=source_root,
        pages_repo=pages_root,
        dry_run=not commit_pages,
        commit=commit_pages,
        push=push_pages,
        live_check=live_check,
        release_manifests=manifests,
        include_rss=True,
    )
    if not pages_result.get("ok"):
        raise FoodLineRetrospectiveError("atomic retrospective Pages publication failed: " + "; ".join(pages_result.get("errors") or []))
    recordings: list[dict[str, object]] = []
    if record_publication:
        live_result = pages_result.get("live_check") or {}
        pages_commit = str(pages_result.get("commit_hash") or "")
        recordings = [
            record_retrospective_publication(
                source_root,
                pages_root,
                bundle,
                pages_commit=pages_commit,
                live_check_ok=bool(live_result.get("ok")),
            )
            for bundle in bundles
        ]
    return {
        "ok": True,
        "status": "atomic_retrospective_publication_complete" if record_publication else "atomic_retrospective_batch_prepared",
        "edition_dates": [bundle.edition_date for bundle in bundles],
        "batch_ids": [bundle.batch_id for bundle in bundles],
        "story_counts": [len(bundle.source_rows) for bundle in bundles],
        "publication_timestamp": publication_timestamp,
        "pages_pre_publish_commit": bundles[0].pages_head,
        "release_manifests": [str(path) for path in manifests],
        "generation_results": [{key: value for key, value in row.items() if key != "_retrospective_bundle"} for row in generated],
        "verification_results": verified,
        "post_generation_verification": verified_set,
        "pages_result": pages_result,
        "publication_recordings": recordings,
        "social_authorized": False,
        "audio_authorized": False,
        "scheduled_task_change_authorized": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and atomically publish committed Food Line retrospective batches.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pages-root", type=Path, required=True)
    parser.add_argument("--source-branch", default="add/pages-repo-default")
    parser.add_argument("--pages-branch", default="gh-pages")
    parser.add_argument("--approval-commit", action="append", required=True)
    parser.add_argument("--approval-path", action="append", required=True)
    parser.add_argument("--publication-timestamp", required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--push", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if not (args.publish and args.push):
            raise FoodLineRetrospectiveError("the atomic production command requires both --publish and --push")
        result = run_atomic_retrospective_batches(
            source_root=args.source_root,
            pages_root=args.pages_root,
            source_branch=args.source_branch,
            pages_branch=args.pages_branch,
            approval_commits=args.approval_commit,
            approval_paths=args.approval_path,
            publication_timestamp=args.publication_timestamp,
            commit_pages=True,
            push_pages=True,
            live_check=True,
            record_publication=True,
        )
    except (FoodLineRetrospectiveError, OSError, ValueError) as exc:
        result = {"ok": False, "status": "atomic_retrospective_publication_failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
