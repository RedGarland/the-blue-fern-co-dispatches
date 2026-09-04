from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_retrospective import (
    APPROVAL_PREFIX,
    APPROVAL_REQUEST_SCHEMA,
    APPROVAL_SCHEMA,
    CORRECTION_PREFIX,
    DECISION_PREFIX,
    LEGACY_V1_APPROVAL_SCHEMA,
    LEGACY_V2_APPROVAL_SCHEMA,
    FoodLineRetrospectiveError,
    _apply_overlay,
    approval_path_for,
    assert_retrospective_history_monotonic,
    create_private_preview,
    create_public_copy_correction,
    create_retrospective_approval,
    fingerprint,
    legacy_v1_approval_path_for,
    legacy_v2_approval_path_for,
    load_committed_json,
    load_retrospective_plan,
    load_retrospective_verification_bundle,
    plan_result,
    record_retrospective_publication,
    verify_private_preview,
    verify_complete_output,
    verify_generated_retrospective_set,
)
from bluefern_dispatches.pages_release_safety import sync_pages_from_source
from scripts.run_food_line_dispatch import run_food_line_dispatch
from scripts.run_food_line_retrospective_batches import run_atomic_retrospective_batches
from scripts.manage_food_line_retrospective import main as manage_retrospective_main


DEFECTIVE = "Aug. 1Ã¢â‚¬â€œ19"
CORRECTED = "Aug. 1\u201319"
PUBLISHED_HISTORY_DATES = (
    "2026-08-24",
    "2026-08-16",
    "2026-08-05",
    "2026-07-31",
    "2026-07-28",
    "2026-06-20",
    "2026-06-19",
    "2026-06-18",
    "2026-06-17",
    "2026-06-16",
    "2026-06-14",
    "2026-06-13",
    "2026-06-09",
    "2026-06-07",
    "2026-06-06",
)
SOURCE_HISTORY_DATES = tuple(
    value
    for value in PUBLISHED_HISTORY_DATES
    if value not in {"2026-08-24", "2026-08-16", "2026-08-05", "2026-07-31", "2026-07-28"}
)

REAL_RETROSPECTIVE_BATCHES = {
    "food-line-august-2026-retrospective-01": {
        "edition_date": "2026-08-30",
        "ordered_copy": "sha256:c5df2c9764ba78a0966a8e4d35c3ec74b161fb325683965a63b62558aee50e4f",
    },
    "food-line-august-2026-retrospective-02": {
        "edition_date": "2026-08-31",
        "ordered_copy": "sha256:cc32d446adbac5546e078ed57df5fdb7b5222dcdbdeb7eaf96f1b083518e87c5",
    },
}
REAL_LEGACY_APPROVALS = {
    "v1": {
        "schema": LEGACY_V1_APPROVAL_SCHEMA,
        "path_for": legacy_v1_approval_path_for,
        "expectations": {
            "food-line-august-2026-retrospective-01": {
                "sha256": "8f0602de3dcae6bc92894ab08403bd3436a70487de90357d622dd1de0deb8425",
                "length": 10356,
                "blob": "72469c9c00c799fb83a8a1015fbc4404b1080568",
            },
            "food-line-august-2026-retrospective-02": {
                "sha256": "013007d878b78cb70e89912ecdf349d843dc03914ede2c1c3a16cdbe3cdc4f4e",
                "length": 5953,
                "blob": "4e29b3b6a1979e7821ee6f7c7d840cc6460fffaf",
            },
        },
    },
    "v2": {
        "schema": LEGACY_V2_APPROVAL_SCHEMA,
        "path_for": legacy_v2_approval_path_for,
        "expectations": {
            "food-line-august-2026-retrospective-01": {
                "sha256": "abb9046540797e87e6b605dbe6848cf8fd81e66c5b97d0d1c5f94e646cf1f9c7",
                "length": 10356,
                "blob": "7f14365e67629e7add635480075f4768a613b34e",
            },
            "food-line-august-2026-retrospective-02": {
                "sha256": "64350df3ae3aef67ee6b9d8d19b749d654a0cc65979b6fabfe1d6644eb8e92f3",
                "length": 5953,
                "blob": "eb4ad87daf6c813402d64d0394d6c473d18acea1",
            },
        },
    },
}
REAL_V3_SOURCE_BASE = "62d2fe5852fc51c95d4205236561e0c133e2a2c2"
REAL_V3_PAGES_HEAD = "cd48dd3c3e9060d718897e5b0254328f2b8b3b6b"


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


def _history_dates(text: str) -> set[str]:
    return set(re.findall(r"/food-line/editions/(\d{4}-\d{2}-\d{2})/", text)) | set(
        re.findall(r'(?<!/)editions/(\d{4}-\d{2}-\d{2})/', text)
    )


def _seed_production_shaped_history(root: Path, pages: Path) -> None:
    (pages / "food-line").mkdir(parents=True, exist_ok=True)
    for edition_date in SOURCE_HISTORY_DATES:
        edition = root / "output" / "site" / "food-line" / "editions" / edition_date
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<h1>{edition_date}</h1>\n", encoding="utf-8")
        _write_json(
            edition / "edition_manifest.json",
            {
                "dispatch_slug": "food-line",
                "edition_date": edition_date,
                "public_rendered": True,
                "edition_mode": "current_update",
                "source_freshness_status": "fresh",
                "freshness_window_days": 2,
                "stale_public_story_count": 0,
                "excluded_stale_source_count": 0,
                "stale_source_ids": [],
                "qualified_primary_count": 1,
                "skip_reason": "",
                "public_archive_title": f"Food Line Dispatch - {edition_date}",
            },
        )
    archive_items = "".join(
        f'<li><a href="editions/{edition_date}/">Food Line Dispatch - {edition_date}</a></li>'
        for edition_date in PUBLISHED_HISTORY_DATES
    )
    rss_items = "".join(
        "\n  <item>\n"
        f"    <title>Food Line Dispatch - {edition_date}</title>\n"
        f"    <link>https://dispatches.thebluefernco.com/food-line/editions/{edition_date}/</link>\n"
        f"    <guid>https://dispatches.thebluefernco.com/food-line/editions/{edition_date}/</guid>\n"
        f"    <description>{edition_date}</description>\n"
        "  </item>"
        for edition_date in PUBLISHED_HISTORY_DATES
    )
    (pages / "food-line" / "archive.html").write_text(
        f"<html><body><ul>{archive_items}</ul></body></html>\n",
        encoding="utf-8",
    )
    (pages / "food-line" / "rss.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss><channel>{rss_items}\n</channel></rss>\n',
        encoding="utf-8",
    )
    for edition_date in PUBLISHED_HISTORY_DATES:
        edition = pages / "food-line" / "editions" / edition_date
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<h1>{edition_date}</h1>\n", encoding="utf-8")


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
    _seed_production_shaped_history(root, pages)
    initial = _commit(root, "protected decisions", "src", "scripts", "data", "output")

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
    (pages / "food-line" / "index.html").write_text("Food Line", encoding="utf-8")
    pages_head = _commit(pages, "pages fixture", "food-line")

    legacy_paths: list[str] = []
    legacy_v2_paths: list[str] = []
    for batch in (1, 2):
        batch_id = f"food-line-august-2026-retrospective-{batch:02d}"
        legacy_path = legacy_v1_approval_path_for(batch_id)
        legacy_v2_path = legacy_v2_approval_path_for(batch_id)
        _write_json(
            root / legacy_path,
            {
                "schema_version": LEGACY_V1_APPROVAL_SCHEMA,
                "batch_id": batch_id,
                "historical_fixture": True,
            },
        )
        _write_json(
            root / legacy_v2_path,
            {
                "schema_version": LEGACY_V2_APPROVAL_SCHEMA,
                "batch_id": batch_id,
                "historical_fixture": True,
            },
        )
        legacy_paths.append(legacy_path)
        legacy_v2_paths.append(legacy_v2_path)
    historical_paths = legacy_paths + legacy_v2_paths
    legacy_commit = _commit(root, "preserve legacy retrospective approvals", *historical_paths)
    legacy_bytes = {path: (root / path).read_bytes() for path in historical_paths}
    legacy_times = {path: (root / path).stat().st_mtime_ns for path in historical_paths}

    approval_paths: list[str] = []
    approval_bytes: dict[str, bytes] = {}
    approval_times: dict[str, int] = {}
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
            "source_base_commit": legacy_commit,
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
        approval_target = root / created["approval_path"]
        approval_bytes[created["approval_path"]] = approval_target.read_bytes()
        approval_times[created["approval_path"]] = approval_target.stat().st_mtime_ns
        replay = create_retrospective_approval(root, request_path)
        assert replay["status"] == "idempotent_noop"
        assert approval_target.read_bytes() == approval_bytes[created["approval_path"]]
        assert approval_target.stat().st_mtime_ns == approval_times[created["approval_path"]]
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
        "legacy_commit": legacy_commit,
        "legacy_paths": legacy_paths,
        "legacy_v2_paths": legacy_v2_paths,
        "legacy_bytes": legacy_bytes,
        "legacy_times": legacy_times,
        "approval_commit": approval_commit,
        "approval_paths": approval_paths,
        "approval_bytes": approval_bytes,
        "approval_times": approval_times,
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


def test_v1_v2_and_v3_coexist_without_mutating_legacy_bytes(retrospective_case: dict) -> None:
    case = retrospective_case
    assert case["approval_paths"] == [
        "approvals/food-line/food-line-august-2026-retrospective-01-approval-v3.json",
        "approvals/food-line/food-line-august-2026-retrospective-02-approval-v3.json",
    ]
    for path in case["legacy_paths"] + case["legacy_v2_paths"]:
        target = case["root"] / path
        assert target.read_bytes() == case["legacy_bytes"][path]
        assert target.stat().st_mtime_ns == case["legacy_times"][path]
    changed = _git(case["root"], "diff-tree", "--no-commit-id", "--name-only", "-r", case["approval_commit"]).splitlines()
    assert changed == case["approval_paths"]
    for path in case["approval_paths"]:
        target = case["root"] / path
        approval = json.loads(target.read_text(encoding="utf-8"))
        assert approval["schema_version"] == "food_line_retrospective_approval_v3"
        assert approval["pages_head"] == _git(case["pages"], "rev-parse", "HEAD")
        assert target.read_bytes() == case["approval_bytes"][path]
        assert target.stat().st_mtime_ns == case["approval_times"][path]
    approval_names = sorted(path.as_posix() for path in (case["root"] / APPROVAL_PREFIX).glob("*.json"))
    assert len(approval_names) == 6


def test_v3_schemas_preserve_v2_material_contract_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    for stem in ("food-line-retrospective-approval-request", "food-line-retrospective-approval"):
        v2 = json.loads((root / "docs" / "schemas" / f"{stem}-v2.schema.json").read_text(encoding="utf-8"))
        v3 = json.loads((root / "docs" / "schemas" / f"{stem}-v3.schema.json").read_text(encoding="utf-8"))
        assert v3["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert v3["type"] == "object" and v3["additionalProperties"] is False
        assert v3["required"] == v2["required"]
        assert set(v3["properties"]) == set(v2["properties"])
        assert v3["properties"]["schema_version"]["const"] == stem.replace("-", "_") + "_v3"


def test_v1_approval_is_obsolete_and_cannot_be_planned(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    with pytest.raises(FoodLineRetrospectiveError, match="obsolete Food Line retrospective V1 approval"):
        load_retrospective_plan(
            case["root"], case["pages"], approval_commit=case["legacy_commit"],
            approval_path=case["legacy_paths"][0], publication_timestamp="2026-09-01T12:00:00Z",
        )

    source = _clone(case["root"], tmp_path / "v3-at-v1-path-source")
    (source / case["legacy_paths"][0]).write_bytes((source / case["approval_paths"][0]).read_bytes())
    replacement_commit = _commit(source, "attempt V3 content at V1 path", case["legacy_paths"][0])
    _git(source, "commit", "--allow-empty", "-m", "merge invalid replacement")
    with pytest.raises(FoodLineRetrospectiveError, match="obsolete Food Line retrospective V1 approval"):
        load_retrospective_plan(
            source, case["pages"], approval_commit=replacement_commit,
            approval_path=case["legacy_paths"][0], publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_v2_approval_is_obsolete_and_cannot_be_planned(retrospective_case: dict) -> None:
    case = retrospective_case
    with pytest.raises(FoodLineRetrospectiveError, match="obsolete Food Line retrospective V2 approval"):
        load_retrospective_plan(
            case["root"], case["pages"], approval_commit=case["legacy_commit"],
            approval_path=case["legacy_v2_paths"][0], publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_conflicting_v3_replay_and_alternate_paths_fail_closed(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    conflicting = json.loads((case["requests"] / "batch-1.json").read_text(encoding="utf-8"))
    conflicting["source_base_commit"] = case["merged_head"]
    conflicting["edition_title"] = "A conflicting renewed title"
    conflict_path = tmp_path / "conflict.json"
    _write_json(conflict_path, conflicting)
    with pytest.raises(FoodLineRetrospectiveError, match="conflicting retrospective approval replay"):
        create_retrospective_approval(case["root"], conflict_path)

    source = _clone(case["root"], tmp_path / "alternate-path-source")
    alternate = "approvals/food-line/alternate-approval-v3.json"
    (source / alternate).parent.mkdir(parents=True, exist_ok=True)
    (source / alternate).write_bytes((source / case["approval_paths"][0]).read_bytes())
    alternate_commit = _commit(source, "attempt alternate approval path", alternate)
    _git(source, "commit", "--allow-empty", "-m", "merge alternate approval")
    with pytest.raises(FoodLineRetrospectiveError, match="owner-derived V3 approval path"):
        load_retrospective_plan(
            source, case["pages"], approval_commit=alternate_commit, approval_path=alternate,
            publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_mixed_legacy_v3_or_unrelated_approval_commit_fails_closed(tmp_path: Path, retrospective_case: dict) -> None:
    case = retrospective_case
    mixed = _clone(case["root"], tmp_path / "mixed-approval-source")
    (mixed / case["legacy_paths"][0]).write_text('{"changed":true}\n', encoding="utf-8")
    v3 = json.loads((mixed / case["approval_paths"][0]).read_text(encoding="utf-8"))
    v3["approval_fingerprint"] = "sha256:" + "0" * 64
    _write_json(mixed / case["approval_paths"][0], v3)
    mixed_commit = _commit(mixed, "attempt mixed V1 and V3 mutation", case["legacy_paths"][0], case["approval_paths"][0])
    _git(mixed, "commit", "--allow-empty", "-m", "merge mixed approval")
    with pytest.raises(FoodLineRetrospectiveError, match="V3-approval-only"):
        load_retrospective_plan(
            mixed, case["pages"], approval_commit=mixed_commit, approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )

    mixed_v2 = _clone(case["root"], tmp_path / "mixed-v2-v3-source")
    (mixed_v2 / case["legacy_v2_paths"][0]).write_text('{"changed":true}\n', encoding="utf-8")
    active = json.loads((mixed_v2 / case["approval_paths"][0]).read_text(encoding="utf-8"))
    active["approval_fingerprint"] = "sha256:" + "2" * 64
    _write_json(mixed_v2 / case["approval_paths"][0], active)
    mixed_v2_commit = _commit(
        mixed_v2,
        "attempt mixed V2 and V3 mutation",
        case["legacy_v2_paths"][0],
        case["approval_paths"][0],
    )
    _git(mixed_v2, "commit", "--allow-empty", "-m", "merge mixed V2 V3 approval")
    with pytest.raises(FoodLineRetrospectiveError, match="V3-approval-only"):
        load_retrospective_plan(
            mixed_v2, case["pages"], approval_commit=mixed_v2_commit, approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )

    mixed_all = _clone(case["root"], tmp_path / "mixed-all-versions-source")
    for path in (case["legacy_paths"][0], case["legacy_v2_paths"][0], case["approval_paths"][0]):
        (mixed_all / path).write_bytes((mixed_all / path).read_bytes() + b" ")
    mixed_all_commit = _commit(
        mixed_all,
        "attempt mixed V1 V2 and V3 mutation",
        case["legacy_paths"][0], case["legacy_v2_paths"][0], case["approval_paths"][0],
    )
    _git(mixed_all, "commit", "--allow-empty", "-m", "merge all-version approval")
    with pytest.raises(FoodLineRetrospectiveError, match="V3-approval-only"):
        load_retrospective_plan(
            mixed_all, case["pages"], approval_commit=mixed_all_commit, approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )

    unrelated = _clone(case["root"], tmp_path / "unrelated-approval-source")
    (unrelated / "README.md").write_text("unrelated\n", encoding="utf-8")
    altered = json.loads((unrelated / case["approval_paths"][0]).read_text(encoding="utf-8"))
    altered["approval_fingerprint"] = "sha256:" + "1" * 64
    _write_json(unrelated / case["approval_paths"][0], altered)
    unrelated_commit = _commit(unrelated, "attempt approval plus source", "README.md", case["approval_paths"][0])
    _git(unrelated, "commit", "--allow-empty", "-m", "merge unsafe approval")
    with pytest.raises(FoodLineRetrospectiveError, match="V3-approval-only"):
        load_retrospective_plan(
            unrelated, case["pages"], approval_commit=unrelated_commit, approval_path=case["approval_paths"][0],
            publication_timestamp="2026-09-01T12:00:00Z",
        )


def test_real_v1_approvals_and_public_copy_fingerprints_are_immutable() -> None:
    root = Path(__file__).resolve().parents[1]
    expectations = {
        "food-line-august-2026-retrospective-01": {
            "sha256": "8f0602de3dcae6bc92894ab08403bd3436a70487de90357d622dd1de0deb8425",
            "length": 10356,
            "blob": "72469c9c00c799fb83a8a1015fbc4404b1080568",
            "copy": "sha256:c5df2c9764ba78a0966a8e4d35c3ec74b161fb325683965a63b62558aee50e4f",
        },
        "food-line-august-2026-retrospective-02": {
            "sha256": "013007d878b78cb70e89912ecdf349d843dc03914ede2c1c3a16cdbe3cdc4f4e",
            "length": 5953,
            "blob": "4e29b3b6a1979e7821ee6f7c7d840cc6460fffaf",
            "copy": "sha256:cc32d446adbac5546e078ed57df5fdb7b5222dcdbdeb7eaf96f1b083518e87c5",
        },
    }
    for batch_id, expected in expectations.items():
        path = legacy_v1_approval_path_for(batch_id)
        raw = (root / path).read_bytes()
        approval = json.loads(raw.decode("utf-8"))
        assert len(raw) == expected["length"]
        assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
        assert _git(root, "rev-parse", f"HEAD:{path}") == expected["blob"]
        assert approval["schema_version"] == LEGACY_V1_APPROVAL_SCHEMA
        assert approval["ordered_public_copy_sha256"] == expected["copy"]

        corrections = {}
        for binding in approval["correction_bindings"]:
            overlay = load_committed_json(
                root, commit=binding["commit"], path=binding["path"], prefix=CORRECTION_PREFIX,
                expected_blob_sha1=binding["blob_sha1"], expected_sha256=binding["sha256"],
            )
            corrections[overlay.payload["event_id"]] = overlay
        ordered = []
        for binding in sorted(approval["decision_bindings"], key=lambda item: item["batch_order"]):
            decision = load_committed_json(
                root, commit=binding["decision_commit"], path=binding["decision_path"], prefix=DECISION_PREFIX,
                expected_blob_sha1=binding["decision_blob_sha1"], expected_sha256=binding["decision_sha256"],
            )
            copy, _ = _apply_overlay(decision, corrections.get(binding["event_id"]))
            ordered.append({"event_id": binding["event_id"], "order": binding["batch_order"], "copy": copy})
        assert fingerprint(ordered) == expected["copy"]
        if batch_id.endswith("-01"):
            temple = next(row["copy"]["summary"] for row in ordered if row["event_id"].startswith("food-line-event-d3d39df"))
            assert "Aug. 1–19" in temple and "Ã" not in temple


def _assert_real_approval_lifecycle(root: Path, *, verify_git_blobs: bool = False) -> str:
    for legacy in REAL_LEGACY_APPROVALS.values():
        for batch_id, expected in legacy["expectations"].items():
            path = legacy["path_for"](batch_id)
            raw = (root / path).read_bytes()
            approval = json.loads(raw.decode("utf-8"))
            assert len(raw) == expected["length"]
            assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
            assert approval["schema_version"] == legacy["schema"]
            assert approval["batch_id"] == batch_id
            if verify_git_blobs:
                assert _git(root, "rev-parse", f"HEAD:{path}") == expected["blob"]

    expected_paths = {approval_path_for(batch_id) for batch_id in REAL_RETROSPECTIVE_BATCHES}
    approval_dir = root / APPROVAL_PREFIX
    alternates = {
        path.relative_to(root).as_posix()
        for batch_id in REAL_RETROSPECTIVE_BATCHES
        for path in approval_dir.glob(f"{batch_id}*-approval-v3.json")
    } - expected_paths
    assert not alternates, f"alternate/conflicting V3 approval paths: {sorted(alternates)}"

    present = {path for path in expected_paths if (root / path).exists()}
    assert not present or present == expected_paths, "partial V3 approval authority is invalid"
    if not present:
        return "pre_approval"

    authority = {
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
        "daily_collection_authorized": False,
        "source_configuration_change_authorized": False,
        "executed": False,
        "published": False,
    }
    for batch_id, expected in REAL_RETROSPECTIVE_BATCHES.items():
        path = approval_path_for(batch_id)
        approval = json.loads((root / path).read_bytes().decode("utf-8"))
        assert approval["schema_version"] == APPROVAL_SCHEMA
        assert approval["batch_id"] == batch_id
        assert approval_path_for(approval["batch_id"]) == path
        assert approval["edition_date"] == expected["edition_date"]
        assert approval["ordered_public_copy_sha256"] == expected["ordered_copy"]
        assert approval["approved_by"] == "William Patton"
        assert approval["source_base_commit"] == REAL_V3_SOURCE_BASE
        assert approval["pages_head"] == REAL_V3_PAGES_HEAD
        assert re.fullmatch(r"[0-9a-f]{40}", approval["source_base_commit"])
        assert re.fullmatch(r"[0-9a-f]{40}", approval["pages_head"])
        assert all(approval[key] == value for key, value in authority.items())
        identity = dict(approval)
        stored_fingerprint = identity.pop("approval_fingerprint")
        assert stored_fingerprint == fingerprint(identity)
    return "approved"


def _copy_real_legacy_approvals(source: Path, target: Path) -> None:
    for legacy in REAL_LEGACY_APPROVALS.values():
        for batch_id in REAL_RETROSPECTIVE_BATCHES:
            path = legacy["path_for"](batch_id)
            destination = target / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((source / path).read_bytes())


def _valid_real_v3_approval(batch_id: str) -> dict:
    expected = REAL_RETROSPECTIVE_BATCHES[batch_id]
    approval = {
        "schema_version": APPROVAL_SCHEMA,
        "batch_id": batch_id,
        "edition_date": expected["edition_date"],
        "ordered_public_copy_sha256": expected["ordered_copy"],
        "approved_by": "William Patton",
        "source_base_commit": REAL_V3_SOURCE_BASE,
        "pages_head": REAL_V3_PAGES_HEAD,
        "generation_authorized": True,
        "publication_authorized": True,
        "pages_authorized": True,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
        "daily_collection_authorized": False,
        "source_configuration_change_authorized": False,
        "executed": False,
        "published": False,
    }
    approval["approval_fingerprint"] = fingerprint(approval)
    return approval


def _write_real_v3_approvals(root: Path) -> None:
    for batch_id in REAL_RETROSPECTIVE_BATCHES:
        _write_json(root / approval_path_for(batch_id), _valid_real_v3_approval(batch_id))


def test_real_legacy_approvals_are_immutable_and_v3_lifecycle_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    assert _assert_real_approval_lifecycle(root, verify_git_blobs=True) in {"pre_approval", "approved"}


def test_real_v3_lifecycle_accepts_preapproval_and_complete_authority(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    _copy_real_legacy_approvals(root, tmp_path)
    assert _assert_real_approval_lifecycle(tmp_path) == "pre_approval"
    _write_real_v3_approvals(tmp_path)
    assert _assert_real_approval_lifecycle(tmp_path) == "approved"


@pytest.mark.parametrize("missing_batch", tuple(REAL_RETROSPECTIVE_BATCHES))
def test_real_v3_lifecycle_rejects_partial_authority(tmp_path: Path, missing_batch: str) -> None:
    root = Path(__file__).resolve().parents[1]
    _copy_real_legacy_approvals(root, tmp_path)
    _write_real_v3_approvals(tmp_path)
    (tmp_path / approval_path_for(missing_batch)).unlink()
    with pytest.raises(AssertionError, match="partial V3"):
        _assert_real_approval_lifecycle(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", "food_line_retrospective_approval_v2"),
        ("batch_id", "food-line-august-2026-retrospective-02"),
        ("ordered_public_copy_sha256", "sha256:" + "0" * 64),
        ("audio_authorized", True),
        ("social_authorized", True),
        ("daily_collection_authorized", True),
    ),
)
def test_real_v3_lifecycle_rejects_invalid_sanctioned_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    root = Path(__file__).resolve().parents[1]
    _copy_real_legacy_approvals(root, tmp_path)
    _write_real_v3_approvals(tmp_path)
    path = tmp_path / approval_path_for("food-line-august-2026-retrospective-01")
    approval = json.loads(path.read_text(encoding="utf-8"))
    approval[field] = value
    identity = dict(approval)
    identity.pop("approval_fingerprint")
    approval["approval_fingerprint"] = fingerprint(identity)
    _write_json(path, approval)
    with pytest.raises(AssertionError):
        _assert_real_approval_lifecycle(tmp_path)


@pytest.mark.parametrize("legacy_version", ("v1", "v2"))
def test_real_v3_lifecycle_rejects_modified_legacy_approval(tmp_path: Path, legacy_version: str) -> None:
    root = Path(__file__).resolve().parents[1]
    _copy_real_legacy_approvals(root, tmp_path)
    batch_id = "food-line-august-2026-retrospective-01"
    path = tmp_path / REAL_LEGACY_APPROVALS[legacy_version]["path_for"](batch_id)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(AssertionError):
        _assert_real_approval_lifecycle(tmp_path)


def test_real_v3_lifecycle_rejects_alternate_conflicting_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    _copy_real_legacy_approvals(root, tmp_path)
    alternate = tmp_path / APPROVAL_PREFIX / (
        "food-line-august-2026-retrospective-01-alternate-approval-v3.json"
    )
    _write_json(alternate, _valid_real_v3_approval("food-line-august-2026-retrospective-01"))
    with pytest.raises(AssertionError, match="alternate/conflicting"):
        _assert_real_approval_lifecycle(tmp_path)


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
    release_before = release.read_bytes()
    for version, legacy_path in (("V1", case["legacy_paths"][0]), ("V2", case["legacy_v2_paths"][0])):
        legacy_release = json.loads(release_before.decode("utf-8"))
        legacy_release["approval_commit"] = case["legacy_commit"]
        legacy_release["approval_path"] = legacy_path
        legacy_release["approval_sha256"] = hashlib.sha256((source / legacy_path).read_bytes()).hexdigest()
        release.write_text(json.dumps(legacy_release, indent=2), encoding="utf-8")
        legacy_sync = sync_pages_from_source(
            dispatch="food-line", dates=["2026-08-30"], require_source_branch="protected",
            source_repo=source, pages_repo=pages, dry_run=True, release_manifest=release, include_rss=True,
        )
        assert legacy_sync["ok"] is False
        assert any(f"obsolete Food Line retrospective {version} approval" in error for error in legacy_sync["errors"])
    release.write_bytes(release_before)
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


def test_two_approvals_publish_in_one_pages_commit_and_record_nine_events(
    tmp_path: Path, retrospective_case: dict
) -> None:
    case = retrospective_case
    source = _clone(case["root"], tmp_path / "atomic-source")
    pages = _clone(case["pages"], tmp_path / "atomic-pages")
    _git(pages, "config", "user.name", "Fixture")
    _git(pages, "config", "user.email", "fixture@example.invalid")
    bundles = [
        load_retrospective_plan(
            source, pages, approval_commit=case["approval_commit"], approval_path=path,
            publication_timestamp="2026-09-01T12:00:00Z",
        )
        for path in case["approval_paths"]
    ]
    result = run_atomic_retrospective_batches(
        source_root=source,
        pages_root=pages,
        source_branch="protected",
        pages_branch="gh-pages",
        approval_commits=[case["approval_commit"], case["approval_commit"]],
        approval_paths=case["approval_paths"],
        publication_timestamp="2026-09-01T12:00:00Z",
        commit_pages=True,
        push_pages=False,
        live_check=False,
        record_publication=False,
    )
    assert result["edition_dates"] == ["2026-08-30", "2026-08-31"]
    assert result["story_counts"] == [6, 3]
    pages_commit = str(result["pages_result"]["commit_hash"])
    assert _git(pages, "rev-parse", "HEAD^") == _git(case["pages"], "rev-parse", "HEAD")
    assert (pages / "food-line/editions/2026-08-30/index.html").is_file()
    assert (pages / "food-line/editions/2026-08-31/index.html").is_file()
    rss = (pages / "food-line/rss.xml").read_text(encoding="utf-8")
    assert "/2026-08-30/" in rss and "/2026-08-31/" in rss
    recordings = [
        record_retrospective_publication(source, pages, bundle, pages_commit=pages_commit, live_check_ok=True)
        for bundle in bundles
    ]
    assert [row["story_memory_rows"] for row in recordings] == [6, 3]
    memory = json.loads((source / "data/records/story_memory.json").read_text(encoding="utf-8"))
    assert sum(1 for row in memory if row.get("retrospective") is True) == 9


def test_production_shaped_retrospective_preserves_pages_history(
    tmp_path: Path, retrospective_case: dict
) -> None:
    case = retrospective_case
    source = _clone(case["root"], tmp_path / "history-source")
    pages = _clone(case["pages"], tmp_path / "history-pages")
    before_archive = _history_dates((pages / "food-line/archive.html").read_text(encoding="utf-8"))
    pages_rss_text = (pages / "food-line/rss.xml").read_text(encoding="utf-8")
    before_rss = _history_dates(pages_rss_text)
    prior_rss_blocks = re.findall(r"<item\b[^>]*>.*?</item>", pages_rss_text, re.I | re.S)

    result = run_atomic_retrospective_batches(
        source_root=source,
        pages_root=pages,
        source_branch="protected",
        pages_branch="gh-pages",
        approval_commits=[case["approval_commit"], case["approval_commit"]],
        approval_paths=case["approval_paths"],
        publication_timestamp="2026-09-01T12:00:00Z",
        commit_pages=False,
        push_pages=False,
        live_check=False,
        record_publication=False,
    )

    after_archive = _history_dates((source / "output/site/food-line/archive.html").read_text(encoding="utf-8"))
    prepared_rss_text = (source / "output/site/food-line/rss.xml").read_text(encoding="utf-8")
    after_rss = _history_dates(prepared_rss_text)
    assert before_archive == set(PUBLISHED_HISTORY_DATES)
    assert before_rss == set(PUBLISHED_HISTORY_DATES)
    assert after_archive == before_archive | {"2026-08-30", "2026-08-31"}
    assert after_rss == before_rss | {"2026-08-30", "2026-08-31"}
    assert all(block in prepared_rss_text for block in prior_rss_blocks)
    assert result["pages_result"]["ok"] is True
    assert result["pages_result"]["commit_status"] == "dry-run"

    verification_bundles = [
        load_retrospective_verification_bundle(
            source,
            pages,
            approval_commit=case["approval_commit"],
            approval_path=path,
            publication_timestamp="2026-09-01T12:00:00Z",
        )
        for path in case["approval_paths"]
    ]
    verified = verify_generated_retrospective_set(source, pages, verification_bundles)
    assert verified["status"] == "post_generation_verified"
    assert verified["history_monotonicity"]["prior_archive_count"] == 15
    assert verified["history_monotonicity"]["prepared_archive_count"] == 17
    assert verified["history_monotonicity"]["archive_dropped"] == []
    assert verified["history_monotonicity"]["rss_dropped"] == []
    cli_args = [
        "verify-output",
        "--repo-root", str(source),
        "--pages-root", str(pages),
        "--publication-timestamp", "2026-09-01T12:00:00Z",
    ]
    for path in case["approval_paths"]:
        cli_args.extend(["--approval-commit", case["approval_commit"], "--approval-path", path])
    assert manage_retrospective_main(cli_args) == 0

    unrelated = source / "unexpected.txt"
    unrelated.write_text("not sanctioned", encoding="utf-8")
    with pytest.raises(FoodLineRetrospectiveError, match="unrelated dirty paths"):
        verify_generated_retrospective_set(source, pages, verification_bundles)
    unrelated.unlink()

    rendered = source / "output/site/food-line/editions/2026-08-30/index.html"
    original = rendered.read_bytes()
    rendered.write_bytes(original + b"drift")
    with pytest.raises(FoodLineRetrospectiveError, match="drifted"):
        verify_generated_retrospective_set(source, pages, verification_bundles)
    rendered.write_bytes(original)


def test_retrospective_history_guard_reports_archive_and_rss_drops(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    candidate = tmp_path / "candidate"
    _seed_production_shaped_history(tmp_path / "source-history", pages)
    (candidate / "food-line").mkdir(parents=True)
    kept = PUBLISHED_HISTORY_DATES[5:]
    archive_items = "".join(f'<a href="editions/{value}/">{value}</a>' for value in kept)
    rss_items = "".join(
        f'<item><link>https://dispatches.thebluefernco.com/food-line/editions/{value}/</link></item>'
        for value in kept
    )
    (candidate / "food-line/archive.html").write_text(archive_items, encoding="utf-8")
    (candidate / "food-line/rss.xml").write_text(f"<rss><channel>{rss_items}</channel></rss>", encoding="utf-8")
    with pytest.raises(FoodLineRetrospectiveError) as caught:
        assert_retrospective_history_monotonic(pages, candidate, edition_dates=["2026-08-30"])
    message = str(caught.value)
    for value in PUBLISHED_HISTORY_DATES[:5]:
        assert value in message
