from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.source_based_retrospective_pages_preparation import (
    SourceBasedRetrospectivePagesPreparationError,
    prepare_pages_from_generation,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _init_repo(root: Path, branch: str = "main") -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-b", branch)
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Tests")
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


def _init_pages(root: Path, branch: str = "gh-pages") -> Path:
    _init_repo(root, branch)
    _git(root, "remote", "add", "origin", "https://github.com/RedGarland/the-blue-fern-co-dispatches.git")
    (root / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    _git(root, "add", "CNAME")
    _git(root, "commit", "-m", "add CNAME")
    return root


def _receipt(repo: Path, dispatch: str, batch: str, *, count: int = 1) -> Path:
    public_root = repo / "output" / "site" / dispatch / "source-based-retrospectives" / batch
    public_root.mkdir(parents=True)
    html = public_root / "index.html"
    items = public_root / "items.json"
    item_rows = [
        {
            "event": f"{dispatch} event {index}",
            "location": f"Location {index}",
            "publisher": "Example Publisher",
            "source_url": f"https://example.test/{dispatch}/{index}",
        }
        for index in range(count)
    ]
    html.write_text(
        "<!doctype html>\n"
        f"<title>{dispatch} retrospective</title>\n"
        + "\n".join(row["source_url"] for row in item_rows)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_json(items, item_rows)
    rel_html = html.relative_to(repo).as_posix()
    rel_items = items.relative_to(repo).as_posix()
    publication_rel = Path("publication-authorizations") / dispatch / "source-based-retrospectives" / f"{batch}-publication-v1.json"
    _write_json(
        repo / publication_rel,
        {
            "schema_version": f"{dispatch.replace('-', '_')}_source_based_retrospective_publication_authorization_v1",
            "publication_type": "source_based_retrospective_publication_authorization",
            "dispatch": dispatch,
            "publication_batch_id": batch,
            "publication_authorized": True,
            "pages_authorized": False,
            "pages_push_authorized": False,
            "social_authorized": False,
            "audio_authorized": False,
            "schedule_authorized": False,
            "scheduled_task_change_authorized": False,
            "public_generation_authorized": False,
            "public_artifacts_generated": False,
            "item_count": count,
            "publication_items": [],
        },
    )
    receipt_rel = Path("data") / "dispatches" / dispatch / "review" / "source-based-retrospective-generations" / f"{batch}.json"
    payload = {
        "schema_version": "bluefern.source_based_retrospective_public_generation_manifest.v1",
        "generation_mode": "source_based_retrospective_public_generation",
        "dispatch": dispatch,
        "publication_authorization_path": publication_rel.as_posix(),
        "publication_authorization_sha256": _sha(repo / publication_rel),
        "publication_batch_id": batch,
        "source_head": _git(repo, "rev-parse", "HEAD"),
        "generated_at": "2026-09-06T20:00:00Z",
        "public_artifacts_generated": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_path": f"/{dispatch}/source-based-retrospectives/{batch}/",
        "authorized_item_count": count,
        "rendered_item_count": count,
        "skipped_item_count": 0,
        "skipped_items": [],
        "unauthorized_item_count": 0,
        "generated_item_ids": [f"{dispatch}-item-{index}" for index in range(count)],
        "chronology_bindings": [],
        "source_urls": [
            {"publication_item_id": f"{dispatch}-item-{index}", "publisher": "Example Publisher", "source_url": f"https://example.test/{dispatch}/{index}"}
            for index in range(count)
        ],
        "generated_public_paths": [rel_html, rel_items],
        "artifact_hashes": {rel_html: _sha(html), rel_items: _sha(items)},
    }
    _write_json(repo / receipt_rel, payload)
    return receipt_rel


def test_valid_receipt_prepares_exact_pages_files_and_commit_receipt(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august", count=16)
    pages = _init_pages(tmp_path / "pages")
    before = _git(pages, "rev-parse", "HEAD")

    result = prepare_pages_from_generation(
        source,
        dispatch="food-line",
        generation_receipt_path=receipt,
        publication_batch_id="food-august",
        pages_repo=pages,
        commit=True,
    )

    assert result["status"] == "prepared_not_pushed"
    assert result["pages_push_performed"] is False
    assert result["pages_head_before"] == before
    assert result["pages_commit_sha_after"] == _git(pages, "rev-parse", "HEAD")
    assert result["pages_commit_sha_after"] != before
    assert sorted(result["pages_changed_paths"]) == [
        "food-line/source-based-retrospectives/food-august/index.html",
        "food-line/source-based-retrospectives/food-august/items.json",
    ]
    receipt_payload = json.loads((source / result["preparation_receipt_path"]).read_text(encoding="utf-8"))
    assert receipt_payload["pages_push_performed"] is False
    assert receipt_payload["deployment_status"] == "prepared_not_pushed"
    assert receipt_payload["destination_hashes"]["food-line/source-based-retrospectives/food-august/index.html"] == _sha(pages / "food-line/source-based-retrospectives/food-august/index.html")
    assert "Alabama" not in (pages / "food-line/source-based-retrospectives/food-august/index.html").read_text(encoding="utf-8")


def test_care_four_item_receipt_uses_exact_care_destination_without_queue_mutation(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "care-line", "care-august", count=4)
    pages = _init_pages(tmp_path / "pages")

    result = prepare_pages_from_generation(
        source,
        dispatch="care-line",
        generation_receipt_path=receipt,
        publication_batch_id="care-august",
        pages_repo=pages,
        commit=False,
    )

    assert result["status"] == "prepared_not_committed"
    assert (pages / "care-line/source-based-retrospectives/care-august/index.html").is_file()
    assert (pages / "care-line/source-based-retrospectives/care-august/items.json").is_file()
    assert not (source / "data/dispatches/care-line/queue").exists()
    assert not (pages / "care-line/index.html").exists()


def test_invalid_receipt_and_hash_mismatch_fail_before_pages_mutation(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august")
    pages = _init_pages(tmp_path / "pages")
    bad = json.loads((source / receipt).read_text(encoding="utf-8"))
    bad["rendered_item_count"] = 2
    _write_json(source / receipt, bad)

    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="generation receipt validation failed"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages)
    assert not (pages / "food-line").exists()

    bad["rendered_item_count"] = 1
    _write_json(source / receipt, bad)
    (source / "output/site/food-line/source-based-retrospectives/food-august/index.html").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="hash mismatch"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages)
    assert not (pages / "food-line").exists()


def test_wrong_dispatch_and_destination_escape_fail(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august")
    pages = _init_pages(tmp_path / "pages")

    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="dispatch mismatch"):
        prepare_pages_from_generation(source, dispatch="care-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages)

    payload = json.loads((source / receipt).read_text(encoding="utf-8"))
    old_path = payload["generated_public_paths"][0]
    payload["generated_public_paths"][0] = "../escape/index.html"
    payload["artifact_hashes"]["../escape/index.html"] = payload["artifact_hashes"].pop(old_path)
    _write_json(source / receipt, payload)
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="artifact missing|public path/hash set mismatch|outside owned public root"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages)


def test_wrong_pages_branch_dirty_pages_and_cname_fail(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august")
    wrong_branch_pages = _init_pages(tmp_path / "wrong", branch="main")
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="must be on gh-pages"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=wrong_branch_pages)

    dirty_pages = _init_pages(tmp_path / "dirty")
    (dirty_pages / "unrelated.html").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="unexpected dirty paths"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=dirty_pages)

    bad_cname = _init_pages(tmp_path / "bad-cname")
    (bad_cname / "CNAME").write_text("wrong.example\n", encoding="utf-8")
    _git(bad_cname, "add", "CNAME")
    _git(bad_cname, "commit", "-m", "bad cname")
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="CNAME"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=bad_cname)


def test_unrelated_generated_file_and_unrelated_pages_file_rejected(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august")
    extra = source / "output/site/food-line/source-based-retrospectives/food-august/extra.html"
    extra.write_text("extra\n", encoding="utf-8")
    payload = json.loads((source / receipt).read_text(encoding="utf-8"))
    payload["generated_public_paths"].append(extra.relative_to(source).as_posix())
    payload["artifact_hashes"][extra.relative_to(source).as_posix()] = _sha(extra)
    _write_json(source / receipt, payload)
    pages = _init_pages(tmp_path / "pages")

    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="exactly index.html and items.json|not an allowed"):
        prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages)

    receipt = _receipt(source, "care-line", "care-august")
    pages = _init_pages(tmp_path / "pages2")
    (pages / "care-line/index.html").parent.mkdir(parents=True)
    (pages / "care-line/index.html").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SourceBasedRetrospectivePagesPreparationError, match="unexpected dirty paths"):
        prepare_pages_from_generation(source, dispatch="care-line", generation_receipt_path=receipt, publication_batch_id="care-august", pages_repo=pages)


def test_already_prepared_release_is_safe_and_does_not_create_empty_commit(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    receipt = _receipt(source, "food-line", "food-august")
    pages = _init_pages(tmp_path / "pages")
    first = prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages, commit=True)
    second = prepare_pages_from_generation(source, dispatch="food-line", generation_receipt_path=receipt, publication_batch_id="food-august", pages_repo=pages, commit=True)

    assert second["status"] == "prepared_already_current"
    assert second["pages_commit_sha_after"] == first["pages_commit_sha_after"]
    assert second["pages_changed_paths"] == []


def test_publish_scope_new_artifact_family_rejects_unrelated_paths() -> None:
    from scripts.validate_publish_scope import validate_publish_scope

    assert validate_publish_scope(
        dispatch="food-line",
        source_artifact_family="source-based-retrospective",
        publication_batch_id="food-august",
        source_changed_paths=[
            "output/site/food-line/source-based-retrospectives/food-august/index.html",
            "output/site/food-line/source-based-retrospectives/food-august/items.json",
        ],
        pages_changed_paths=[
            "food-line/source-based-retrospectives/food-august/index.html",
            "food-line/source-based-retrospectives/food-august/items.json",
        ],
        strict=True,
    ) == []
    errors = validate_publish_scope(
        dispatch="food-line",
        source_artifact_family="source-based-retrospective",
        publication_batch_id="food-august",
        source_changed_paths=["output/site/food-line/index.html"],
        pages_changed_paths=["gaza/index.html"],
        strict=True,
    )
    assert any("outside the source-based retrospective food-line batch scope" in error for error in errors)
