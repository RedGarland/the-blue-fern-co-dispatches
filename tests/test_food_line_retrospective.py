from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_retrospective import (
    APPROVAL_REQUEST_SCHEMA,
    CORRECTION_PREFIX,
    DECISION_PREFIX,
    FoodLineRetrospectiveError,
    create_private_preview,
    create_public_copy_correction,
    create_retrospective_approval,
    load_retrospective_plan,
    plan_result,
    record_retrospective_publication,
    verify_private_preview,
    verify_complete_output,
)
from bluefern_dispatches.pages_release_safety import sync_pages_from_source
from scripts.run_food_line_dispatch import run_food_line_dispatch


DEFECTIVE = "Aug. 1Ã¢â‚¬â€œ19"
CORRECTED = "Aug. 1\u201319"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _commit(root: Path, message: str, *paths: str) -> str:
    _git(root, "add", "--", *paths)
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _binding(root: Path, commit: str, path: str) -> dict[str, str]:
    raw = (root / path).read_bytes()
    return {
        "commit": commit,
        "path": path,
        "blob_sha1": _git(root, "rev-parse", f"{commit}:{path}"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _decision(event_number: int, *, batch: int, order: int, mojibake: bool = False) -> tuple[dict, dict]:
    event_id = f"food-line-event-{event_number:024x}"
    source_url = f"https://publisher.example.org/report/{event_number}"
    submission = {"schema_version": "food_line_historical_event_editorial_review_v1", "event_id": event_id}
    summary = (
        f"Temple College listed its pantry as closed during {DEFECTIVE}, followed by registration before reopening."
        if mojibake
        else f"Provider {event_number} documented a demonstrated food-access reduction while the available evidence left its duration or severity bounded."
    )
    decision = {
        "schema_version": "food_line_historical_event_editorial_decision_v1",
        "source_head": "a" * 40,
        "pages_head": "b" * 40,
        "recovery_identity_sha256": "sha256:" + "c" * 64,
        "recovery_artifact_set_sha256": "sha256:" + "d" * 64,
        "review_artifact_path": f"data/agent-history/food-line/reviews/recovery-submissions/{event_id}.json",
        "review_artifact_sha256": "",
        "operator": "Fixture editorial reviewer",
        "reviewed_by": "Fixture editorial reviewer",
        "reviewed_at": "2026-09-01T01:00:00Z",
        "event_id": event_id,
        "event_fingerprint": "sha256:" + f"{event_number:064x}"[-64:],
        "priority": 1,
        "decision": "confirmed",
        "decision_reason": "Direct evidence documents a bounded food-access condition.",
        "evidence_references": [
            {
                "canonical_source_url": source_url,
                "publisher": f"Publisher {event_number}",
                "source_published_at": "2026-08-15",
                "role": "principal",
                "exact_supporting_passages": ["The provider reported the service reduction."],
            }
        ],
        "event_assessment": {
            "location": f"County {event_number}",
            "affected_population": ["households seeking food assistance"],
        },
        "dedupe_assessment": {"result": "no_match"},
        "publication_copy": {
            "headline": f"Provider {event_number} reported a food-access reduction",
            "summary": summary,
            "source_links": [source_url],
        },
        "recommended_batch": {
            "batch_id": f"food-line-august-2026-retrospective-{batch:02d}",
            "order": order,
            "edition_title": f"Food Line August retrospective {batch}",
            "edition_introduction": "A bounded retrospective collection.",
        },
        "publication_eligible": False,
        "publication_approval": False,
        "archive_mutation_authorized": False,
        "intake_authorized": False,
        "queue_authorized": False,
        "generation_authorized": False,
        "approval_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
    }
    return submission, decision


def _fixture(tmp_path: Path) -> dict:
    root = tmp_path / "source"
    pages = tmp_path / "pages"
    requests = tmp_path / "private"
    root.mkdir()
    pages.mkdir()
    requests.mkdir()
    _git(root, "init", "-b", "protected")
    _git(root, "config", "core.autocrlf", "false")
    for relative in (
        "src/bluefern_dispatches/food_line_retrospective.py",
        "scripts/run_food_line_dispatch.py",
        "src/bluefern_dispatches/pages_release_safety.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# protected {relative}\n", encoding="utf-8")
    decision_paths: list[str] = []
    for index in range(1, 10):
        batch = 1 if index <= 6 else 2
        order = index if batch == 1 else index - 6
        submission, decision = _decision(index, batch=batch, order=order, mojibake=index == 6)
        event_id = decision["event_id"]
        submission_path = f"data/agent-history/food-line/reviews/recovery-submissions/{event_id}.json"
        _write_json(root / submission_path, submission)
        decision["review_artifact_sha256"] = hashlib.sha256((root / submission_path).read_bytes()).hexdigest()
        decision_path = f"{DECISION_PREFIX}{'c' * 32}/{event_id}.json"
        _write_json(root / decision_path, decision)
        decision_paths.append(decision_path)
    initial = _commit(root, "protected decisions", "src", "scripts", "data")

    temple_path = decision_paths[5]
    correction = create_public_copy_correction(
        root,
        decision_commit=initial,
        decision_path=temple_path,
        decision_blob_sha1=_git(root, "rev-parse", f"{initial}:{temple_path}"),
        decision_sha256=hashlib.sha256((root / temple_path).read_bytes()).hexdigest(),
        event_id="food-line-event-000000000000000000000006",
        field="publication_copy.summary",
        prior_text=DEFECTIVE,
        replacement_text=CORRECTED,
        reason="Encoding/mojibake correction for public copy.",
        corrected_by="Fixture copy operator",
        corrected_at="2026-09-01T02:00:00Z",
    )
    correction_path = correction["correction_path"]
    correction_commit = _commit(root, "record correction overlay", correction_path)

    _git(pages, "init", "-b", "gh-pages")
    _git(pages, "config", "core.autocrlf", "false")
    for relative, text in (
        ("food-line/index.html", "Food Line"),
        ("food-line/archive.html", "Archive"),
        ("food-line/rss.xml", "<rss></rss>"),
    ):
        path = pages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    pages_head = _commit(pages, "pages fixture", "food-line")

    approval_paths: list[str] = []
    for batch, edition, selected in ((1, "2026-08-30", decision_paths[:6]), (2, "2026-08-31", decision_paths[6:])):
        request = {
            "schema_version": APPROVAL_REQUEST_SCHEMA,
            "batch_id": f"food-line-august-2026-retrospective-{batch:02d}",
            "edition_date": edition,
            "edition_title": f"Food Line August retrospective {batch}",
            "edition_introduction": "These source-backed accounts preserve condition, cause, and severity limits.",
            "retrospective_disclosure": "This retrospective recovers previously missed August 2026 reporting and was published later.",
            "approved_by": "Independent human approver",
            "approved_at": "2026-09-01T03:00:00Z",
            "source_base_commit": correction_commit,
            "pages_head": pages_head,
            "decision_bindings": [_binding(root, initial, path) for path in selected],
            "correction_bindings": [_binding(root, correction_commit, correction_path)] if batch == 1 else [],
            "audio_authorized": False,
            "publication_authorized": True,
        }
        request_path = requests / f"batch-{batch}.json"
        _write_json(request_path, request)
        created = create_retrospective_approval(root, request_path)
        assert created["status"] == "approval_created"
        replay = create_retrospective_approval(root, request_path)
        assert replay["status"] == "idempotent_noop"
        approval_paths.append(created["approval_path"])
    approval_commit = _commit(root, "approve retrospective batches", *approval_paths)
    _git(root, "commit", "--allow-empty", "-m", "merge approval PR")
    merged_head = _git(root, "rev-parse", "HEAD")
    return {
        "root": root,
        "pages": pages,
        "requests": requests,
        "initial": initial,
        "correction_commit": correction_commit,
        "correction_path": correction_path,
        "approval_commit": approval_commit,
        "approval_paths": approval_paths,
        "merged_head": merged_head,
        "decision_paths": decision_paths,
    }


@pytest.fixture(scope="module")
def retrospective_case(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _fixture(tmp_path_factory.mktemp("retrospective-case"))


def _clone(source: Path, destination: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--local", str(source), str(destination)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return destination


def test_exact_temple_correction_preserves_original_and_replays(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-b", "protected")
    _git(root, "config", "core.autocrlf", "false")
    submission, decision = _decision(6, batch=1, order=6, mojibake=True)
    submission_path = f"data/agent-history/food-line/reviews/recovery-submissions/{decision['event_id']}.json"
    _write_json(root / submission_path, submission)
    decision["review_artifact_sha256"] = hashlib.sha256((root / submission_path).read_bytes()).hexdigest()
    decision_path = f"{DECISION_PREFIX}{'c' * 32}/{decision['event_id']}.json"
    _write_json(root / decision_path, decision)
    commit = _commit(root, "decision", "data")
    original = (root / decision_path).read_bytes()
    kwargs = {
        "decision_commit": commit,
        "decision_path": decision_path,
        "decision_blob_sha1": _git(root, "rev-parse", f"{commit}:{decision_path}"),
        "decision_sha256": hashlib.sha256(original).hexdigest(),
        "event_id": decision["event_id"],
        "field": "publication_copy.summary",
        "prior_text": DEFECTIVE,
        "replacement_text": CORRECTED,
        "reason": "Encoding/mojibake correction.",
        "corrected_by": "Copy editor",
        "corrected_at": "2026-09-01T02:00:00Z",
    }
    first = create_public_copy_correction(root, **kwargs)
    overlay = root / first["correction_path"]
    first_bytes = overlay.read_bytes()
    first_time = overlay.stat().st_mtime_ns
    replay = create_public_copy_correction(root, **kwargs)
    assert replay["status"] == "idempotent_noop"
    assert overlay.read_bytes() == first_bytes
    assert overlay.stat().st_mtime_ns == first_time
    assert (root / decision_path).read_bytes() == original
    with pytest.raises(FoodLineRetrospectiveError, match="exactly once"):
        create_public_copy_correction(root, **{**kwargs, "prior_text": "already correct"})
    overlay.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FoodLineRetrospectiveError, match="conflicting public-copy correction replay"):
        create_public_copy_correction(root, **kwargs)


def test_nine_clean_records_two_batches_and_normal_merge_plan(retrospective_case: dict) -> None:
    case = retrospective_case
    plans = []
    for path in case["approval_paths"]:
        bundle = load_retrospective_plan(
            case["root"],
            case["pages"],
            approval_commit=case["approval_commit"],
            approval_path=path,
            publication_timestamp="2026-09-01T12:00:00Z",
        )
        plans.append(plan_result(bundle))
        assert all("Ã" not in row["summary"] and "â€" not in row["summary"] for row in bundle.public_copies)
    assert [row["story_count"] for row in plans] == [6, 3]
    assert sum(row["story_count"] for row in plans) == 9
    assert all(row["status"] == "validated_plan" and row["persistent_mutation"] is False for row in plans)


def test_worktree_drift_date_collision_and_backdated_timestamp_fail_closed(
    tmp_path: Path, retrospective_case: dict
) -> None:
    case = retrospective_case
    path = case["approval_paths"][0]
    dirty = case["root"] / "unexpected.txt"
    dirty.write_text("drift", encoding="utf-8")
    with pytest.raises(FoodLineRetrospectiveError, match="clean source"):
        load_retrospective_plan(
            case["root"], case["pages"], approval_commit=case["approval_commit"], approval_path=path,
            publication_timestamp="2026-09-01T12:00:00Z",
        )
    dirty.unlink()
    with pytest.raises(FoodLineRetrospectiveError, match="later real execution"):
        load_retrospective_plan(
            case["root"], case["pages"], approval_commit=case["approval_commit"], approval_path=path,
            publication_timestamp="2026-08-30T12:00:00Z",
        )
    pages_copy = _clone(case["pages"], tmp_path / "pages-collision")
    occupied = pages_copy / "food-line" / "editions" / "2026-08-30"
    occupied.mkdir(parents=True)
    (occupied / "index.html").write_text("occupied", encoding="utf-8")
    _commit(pages_copy, "occupy date", "food-line/editions/2026-08-30")
    with pytest.raises(FoodLineRetrospectiveError, match="Pages checkout drifted"):
        load_retrospective_plan(
            case["root"], pages_copy, approval_commit=case["approval_commit"], approval_path=path,
            publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_generation_uses_normal_food_line_surfaces_with_disclosure_and_real_pubdate(
    tmp_path: Path, retrospective_case: dict
) -> None:
    case = dict(retrospective_case)
    case["root"] = _clone(retrospective_case["root"], tmp_path / "generation-source")
    case["pages"] = _clone(retrospective_case["pages"], tmp_path / "generation-pages")
    result = run_food_line_dispatch(
        case["root"],
        "2026-08-30",
        generate_audio=False,
        retrospective_approval_commit=case["approval_commit"],
        retrospective_approval_path=case["approval_paths"][0],
        retrospective_publication_timestamp="2026-09-01T12:00:00Z",
        retrospective_pages_root=case["pages"],
    )
    assert result["ok"] is True
    edition = case["root"] / "output" / "site" / "food-line" / "editions" / "2026-08-30"
    html = (edition / "index.html").read_text(encoding="utf-8")
    source_table = (edition / "source_table.html").read_text(encoding="utf-8")
    rss = (case["root"] / "output" / "site" / "food-line" / "rss.xml").read_text(encoding="utf-8")
    assert "retrospective" in html.lower() and "previously missed August 2026 reporting" in html
    assert CORRECTED in html and DEFECTIVE not in html
    assert "food-line-event-" not in html and "Record ID" not in source_table
    assert "Tue, 01 Sep 2026 12:00:00 +0000" in rss
    assert result["output_verification"]["path_count"] == 9
    assert len(json.loads((edition / "sources_manifest.json").read_text(encoding="utf-8"))) == 6
    assert len(json.loads((edition / "curation_manifest.json").read_text(encoding="utf-8"))["stories"]) == 6
    assert not (case["root"] / "output" / "site" / "food-line" / "audio" / "2026-08-30.mp3").exists()


def test_working_tree_or_unmerged_approval_cannot_grant_authority(
    tmp_path: Path, retrospective_case: dict
) -> None:
    case = dict(retrospective_case)
    case["root"] = _clone(retrospective_case["root"], tmp_path / "unmerged-source")
    case["pages"] = _clone(retrospective_case["pages"], tmp_path / "unmerged-pages")
    _git(case["root"], "checkout", case["approval_commit"])
    with pytest.raises(FoodLineRetrospectiveError, match="normal protected merge"):
        load_retrospective_plan(
            case["root"], case["pages"], approval_commit=case["approval_commit"],
            approval_path=case["approval_paths"][0], publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_private_preview_is_external_exact_and_idempotent(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    bundle = load_retrospective_plan(
        case["root"], case["pages"], approval_commit=case["approval_commit"],
        approval_path=case["approval_paths"][0], publication_timestamp="2026-09-01T12:00:00Z",
    )
    preview_root = tmp_path / "private-preview"
    first = create_private_preview(bundle, case["root"], preview_root)
    target = Path(first["preview_root"])
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.iterdir()}
    replay = create_private_preview(bundle, case["root"], preview_root)
    assert replay["status"] == "idempotent_noop"
    assert before == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in target.iterdir()}
    assert verify_private_preview(bundle, case["root"], preview_root)["status"] == "preview_verified"
    (target / "preview.html").write_text("partial", encoding="utf-8")
    with pytest.raises(FoodLineRetrospectiveError, match="preview bytes drifted"):
        verify_private_preview(bundle, case["root"], preview_root)
    with pytest.raises(FoodLineRetrospectiveError, match="outside the source repository"):
        create_private_preview(bundle, case["root"], case["root"] / "private")


def test_audio_unrelated_authority_and_story_cap_fail_closed(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    original = json.loads((case["requests"] / "batch-1.json").read_text(encoding="utf-8"))
    original["source_base_commit"] = case["merged_head"]
    audio = dict(original)
    audio["audio_authorized"] = True
    audio_path = tmp_path / "audio.json"
    _write_json(audio_path, audio)
    with pytest.raises(FoodLineRetrospectiveError, match="audio is optional"):
        create_retrospective_approval(case["root"], audio_path)
    too_many = dict(original)
    too_many["decision_bindings"] = list(original["decision_bindings"]) + [original["decision_bindings"][0]]
    cap_path = tmp_path / "cap.json"
    _write_json(cap_path, too_many)
    with pytest.raises(FoodLineRetrospectiveError, match="one through six"):
        create_retrospective_approval(case["root"], cap_path)
    authored = dict(original)
    authored["approved_by"] = "Fixture editorial reviewer"
    authored_path = tmp_path / "self-authored.json"
    _write_json(authored_path, authored)
    with pytest.raises(FoodLineRetrospectiveError, match="independent"):
        create_retrospective_approval(case["root"], authored_path)


def test_intervening_owner_or_dedupe_drift_blocks_plan(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    source = _clone(case["root"], tmp_path / "source-owner-drift")
    pages = _clone(case["pages"], tmp_path / "pages-owner-drift")
    owner = source / "src/bluefern_dispatches/food_line_retrospective.py"
    owner.write_text(owner.read_text(encoding="utf-8") + "# changed after approval\n", encoding="utf-8")
    _commit(source, "change publication owner", "src/bluefern_dispatches/food_line_retrospective.py")
    with pytest.raises(FoodLineRetrospectiveError, match="changed after approval"):
        load_retrospective_plan(
            source, pages, approval_commit=case["approval_commit"], approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )

    source = _clone(case["root"], tmp_path / "source-dedupe-drift")
    pages = _clone(case["pages"], tmp_path / "pages-dedupe-drift")
    decision = json.loads((source / case["decision_paths"][0]).read_text(encoding="utf-8"))
    memory = source / "data/records/story_memory.json"
    _write_json(memory, [{"dispatch_slug": "food-line", "canonical_urls": [decision["publication_copy"]["source_links"][0]]}])
    _commit(source, "intervening publication memory", "data/records/story_memory.json")
    with pytest.raises(FoodLineRetrospectiveError, match="publication or dedupe state"):
        load_retrospective_plan(
            source, pages, approval_commit=case["approval_commit"], approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_guarded_pages_application_and_durable_recording(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    source = _clone(case["root"], tmp_path / "publish-source")
    pages = _clone(case["pages"], tmp_path / "publish-pages")
    _git(pages, "config", "user.name", "Fixture")
    _git(pages, "config", "user.email", "fixture@example.invalid")
    result = run_food_line_dispatch(
        source,
        "2026-08-30",
        generate_audio=False,
        retrospective_approval_commit=case["approval_commit"],
        retrospective_approval_path=case["approval_paths"][0],
        retrospective_publication_timestamp="2026-09-01T12:00:00Z",
        retrospective_pages_root=pages,
    )
    bundle = result["_retrospective_bundle"]
    release = Path(result["release_manifest_path"])
    dry_run = sync_pages_from_source(
        dispatch="food-line", dates=["2026-08-30"], require_source_branch="protected",
        source_repo=source, pages_repo=pages, dry_run=True, release_manifest=release, include_rss=True,
    )
    assert dry_run["ok"] is True
    assert "food-line/rss.xml" in dry_run["planned_pages_paths"]
    sources_manifest = source / "output/site/food-line/editions/2026-08-30/sources_manifest.json"
    sources_before = sources_manifest.read_bytes()
    release_before = release.read_bytes()
    forged_sources = json.loads(sources_before.decode("utf-8"))
    forged_sources[0]["summary_or_snippet"] = "Different unapproved public copy."
    sources_manifest.write_text(json.dumps(forged_sources, indent=2), encoding="utf-8")
    forged_release = json.loads(release_before.decode("utf-8"))
    for entry in forged_release["entries"]:
        if entry["source_path"].endswith("sources_manifest.json"):
            entry["source_sha256"] = hashlib.sha256(sources_manifest.read_bytes()).hexdigest()
    release.write_text(json.dumps(forged_release, indent=2), encoding="utf-8")
    forged = sync_pages_from_source(
        dispatch="food-line", dates=["2026-08-30"], require_source_branch="protected",
        source_repo=source, pages_repo=pages, dry_run=True, release_manifest=release, include_rss=True,
    )
    assert forged["ok"] is False
    assert any("exact approved public copy" in error for error in forged["errors"])
    sources_manifest.write_bytes(sources_before)
    release.write_bytes(release_before)
    applied = sync_pages_from_source(
        dispatch="food-line", dates=["2026-08-30"], require_source_branch="protected",
        source_repo=source, pages_repo=pages, commit=True, release_manifest=release, include_rss=True,
    )
    assert applied["ok"] is True and applied["commit_status"] == "committed"
    pages_commit = str(applied["commit_hash"])
    recorded = record_retrospective_publication(
        source, pages, bundle, pages_commit=pages_commit, live_check_ok=True,
    )
    assert recorded["status"] == "publication_recorded" and recorded["story_memory_rows"] == 6
    memory = json.loads((source / "data/records/story_memory.json").read_text(encoding="utf-8"))
    assert len([row for row in memory if row.get("approval_sha256") == bundle.approval.sha256]) == 6
    replay = record_retrospective_publication(
        source, pages, bundle, pages_commit=pages_commit, live_check_ok=True,
    )
    assert replay["status"] == "idempotent_noop"
    assert not (source / "output/site/food-line/audio/2026-08-30.mp3").exists()


def test_partial_generated_output_is_rejected(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    source = _clone(case["root"], tmp_path / "partial-source")
    pages = _clone(case["pages"], tmp_path / "partial-pages")
    result = run_food_line_dispatch(
        source, "2026-08-31", generate_audio=False,
        retrospective_approval_commit=case["approval_commit"],
        retrospective_approval_path=case["approval_paths"][1],
        retrospective_publication_timestamp="2026-09-01T12:00:00Z", retrospective_pages_root=pages,
    )
    bundle = result["_retrospective_bundle"]
    (source / "output/site/food-line/editions/2026-08-31/claim_ledger.html").unlink()
    with pytest.raises(FoodLineRetrospectiveError, match="incomplete or unsafe"):
        verify_complete_output(source, bundle)
