from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import bluefern_dispatches.gaza_historical_catchup as owner
from bluefern_dispatches.gaza_historical_catchup import (
    APPROVAL_REQUEST_SCHEMA,
    GazaHistoricalCatchupError,
    create_approval,
    create_private_preview,
    create_stage,
    load_plan,
    plan_result,
    publish_stage,
    published_replay_result,
    verify_stage,
)
from bluefern_dispatches.story_dedupe import dedupe_public_stories


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _commit(root: Path, message: str, *paths: str, allow_empty: bool = False) -> str:
    if paths:
        _git(root, "add", "--", *paths)
    args = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit"]
    if allow_empty:
        args.append("--allow-empty")
    args.extend(["-m", message])
    _git(root, *args)
    return _git(root, "rev-parse", "HEAD")


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _binding(root: Path, commit: str, path: str) -> dict[str, str]:
    raw = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"], check=True, capture_output=True
    ).stdout
    return {
        "commit": commit,
        "path": path,
        "blob_sha1": _git(root, "rev-parse", f"{commit}:{path}"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _make_case(
    tmp_path: Path,
    *,
    missing_date: bool = False,
    decision_value: str = "confirmed",
    audio: bool = False,
    social: bool = False,
    approver: str = "Independent Human Approver",
    already_public: bool = False,
    occupied: bool = False,
    mismatched_decision: bool = False,
    attribution_mode: str = "official_claim",
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "source"
    pages = tmp_path / "pages"
    remote = tmp_path / "pages-remote.git"
    private = tmp_path / "private"
    root.mkdir()
    pages.mkdir()
    private.mkdir()
    remote.mkdir()
    _git(root, "init", "-b", "protected")
    _git(root, "config", "core.autocrlf", "false")
    _git(pages, "init", "-b", "gh-pages")
    _git(pages, "config", "core.autocrlf", "false")
    _git(remote, "init", "--bare")

    for relative in owner.PROTECTED_OWNER_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture {relative}\n", encoding="utf-8")
    _json(root / "data/records/story_memory.json", [])

    candidate = "GZ-SYNTHETIC-TRUE-MISS-233"
    normalized_path = "data/agent-history/gaza/normalized/synthetic.json"
    review_path = "data/agent-history/gaza/reviews/synthetic.json"
    decision_path = "data/agent-history/gaza/reviews/decisions/synthetic.json"
    finding = {
        "audit_candidate_id": candidate,
        "domain": "gaza",
        "historical_backfill": True,
        "title": "Civil Defence reported bodies recovered from destroyed homes",
        "summary": "A protected historical finding.",
        "category": "civilian_harm",
        "event_date": "" if missing_date else "2026-08-27",
        "event_period_start": "",
        "event_period_end": "",
        "source_published_at": "2026-08-27",
        "publisher": "Example News",
        "canonical_source_url": "https://publisher.example/reports/recovery-233",
    }
    _json(root / normalized_path, {"findings": [finding]})
    review = {
        "schema_version": "gaza_historical_editorial_review_v2",
        "review_type": "historical_editorial_review",
        "domain": "gaza",
        "audit_candidate_id": candidate + "-OTHER" if mismatched_decision else candidate,
        "operator": "Historical Human Reviewer",
        "decision": decision_value,
        "resulting_review_state": "substantively_reviewed" if decision_value == "confirmed" else "deferred",
        "candidate_event_fingerprint": "sha256:" + "1" * 64,
        "current_publication_approval": False,
        "archive_mutation_authorized": False,
        "edition_authorized": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
    }
    _json(root / review_path, review)
    decision = {
        "schema_version": "gaza_historical_editorial_decision_v2",
        "domain": "gaza",
        "audit_candidate_id": candidate,
        "operator": "Historical Human Reviewer",
        "decision": decision_value,
        "resulting_review_state": "substantively_reviewed" if decision_value == "confirmed" else "deferred",
        "candidate_event_fingerprint": review["candidate_event_fingerprint"],
        "review_artifact_path": review_path,
        "review_artifact_sha256": hashlib.sha256((root / review_path).read_bytes()).hexdigest(),
        "normalized_artifact_path": normalized_path,
        "normalized_artifact_sha256": hashlib.sha256((root / normalized_path).read_bytes()).hexdigest(),
        "publication_approval": False,
        "archive_content_change_authorized": False,
        "edition_authorized": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
        "date_assessment": {"event_date": finding["event_date"], "source_published_at": "2026-08-27"},
        "taxonomy_review": {"category": "civilian_harm"},
        "attribution_assessment": {
            "mode": attribution_mode,
            "attributed_to": "Gaza Civil Defence, as reported by Example News",
            "safe_future_wording": (
                "The filing alleged a procedural breach; the allegation was not an adjudicated finding."
                if attribution_mode == "allegation"
                else "Gaza Civil Defence said 233 bodies or remains were recovered while 174 people remained missing."
            ),
            "attribution_preserved": True,
            "uncertainty_preserved": True,
            "unsupported_certainty_escalation": False,
        },
        "evidence_references": [
            {"role": "principal", "url": finding["canonical_source_url"], "supporting_passage": "Civil Defence said 233 were recovered."}
        ],
        "duplicate_and_authoritative_match_check": {
            "candidate_remains_distinct": True,
            "existing_edition_match": None,
            "existing_source_match": None,
            "existing_story_cluster_match": None,
            "existing_historical_match": None,
        },
    }
    _json(root / decision_path, decision)
    source_base = _commit(root, "protected reviewed finding", "src", "scripts", "data")

    old = "2026-09-01"
    old_dir = pages / "gaza" / "editions" / old
    old_dir.mkdir(parents=True)
    (old_dir / "index.html").write_text("old edition", encoding="utf-8")
    _json(old_dir / "edition_manifest.json", {"dispatch_slug": "gaza", "edition_date": old, "source_count": 1, "story_count": 1})
    _json(old_dir / "sources_manifest.json", [{"source_record_id": "old"}])
    old_curation = [{"story_id": "old"}]
    if already_public:
        old_curation[0].update({"historical_candidate_id": candidate, "event_fingerprint": review["candidate_event_fingerprint"]})
    _json(old_dir / "curation_manifest.json", old_curation)
    for relative, raw in {
        "index.html": "root home\n",
        "gaza/index.html": f'<ul class="edition-list">\n<li><a href="editions/{old}/">{old}</a></li>\n</ul>\n',
        "gaza/archive.html": f'<ul class="edition-list">\n<li><a href="editions/{old}/">{old}</a></li>\n</ul>\n',
        "gaza/rss.xml": (
            "<rss><channel><description>Daily briefing</description>\n"
            f'<item><guid>https://dispatches.thebluefernco.com/gaza/editions/{old}/</guid></item>\n'
            "</channel></rss>\n"
        ),
        "gaza/podcast.xml": "podcast unchanged\n",
        "gaza/flash-briefing.json": "{}\n",
    }.items():
        path = pages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
    if occupied:
        target = pages / "gaza/catchups/gaza-historical-catchup-synthetic"
        target.mkdir(parents=True)
        (target / "index.html").write_text("occupied", encoding="utf-8")
    pages_head = _commit(pages, "pages baseline", "index.html", "gaza")
    _git(pages, "remote", "add", "origin", str(remote))
    _git(pages, "push", "-u", "origin", "gh-pages")

    request = {
        "schema_version": APPROVAL_REQUEST_SCHEMA,
        "catchup_id": "gaza-historical-catchup-synthetic",
        "publication_date": "2026-09-04",
        "title": "Gaza historical catch-up",
        "introduction": "A bounded collection of reviewed, source-backed developments.",
        "retrospective_disclosure": "Historical review recovered previously missed reporting, which is published later in this catch-up.",
        "approved_by": approver,
        "approved_at": "2026-09-03T18:00:00Z",
        "source_base_commit": source_base,
        "pages_head": pages_head,
        "public_path": "gaza/catchups/gaza-historical-catchup-synthetic/",
        "public_url": "https://dispatches.thebluefernco.com/gaza/catchups/gaza-historical-catchup-synthetic/",
        "review_bindings": [_binding(root, source_base, review_path)],
        "decision_bindings": [_binding(root, source_base, decision_path)],
        "item_order": [candidate],
        "publication_authorized": True,
        "audio_authorized": audio,
        "social_authorized": social,
    }
    request_path = private / "approval.json"
    _json(request_path, request)
    return {
        "root": root, "pages": pages, "remote": remote, "private": private,
        "candidate": candidate, "review_path": review_path, "decision_path": decision_path,
        "source_base": source_base, "pages_head": pages_head, "request": request,
        "request_path": request_path,
    }


def _approve_and_merge(case: dict, *, mutate_approval=None) -> dict:
    first = create_approval(case["root"], case["pages"], case["request_path"])
    assert first["status"] == "approval_created"
    approval_path = first["approval_path"]
    if mutate_approval:
        path = case["root"] / approval_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate_approval(payload)
        identity = dict(payload)
        identity.pop("approval_fingerprint", None)
        payload["approval_fingerprint"] = owner.fingerprint(identity)
        _json(path, payload)
    approval_commit = _commit(case["root"], "approval only", approval_path)
    _commit(case["root"], "protected merge", allow_empty=True)
    case.update({"approval_path": approval_path, "approval_commit": approval_commit})
    return case


def _plan(case: dict):
    return load_plan(
        case["root"], case["pages"], approval_commit=case["approval_commit"],
        approval_path=case["approval_path"], publication_timestamp="2026-09-04T17:30:00Z",
    )


def _add_daily_publication(
    case: dict,
    *,
    day: str = "2026-09-04",
    candidate_id: str | None = None,
    complete: bool = True,
) -> str:
    root = case["pages"] / "gaza/editions" / day
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("normal daily edition\n", encoding="utf-8")
    _json(root / "edition_manifest.json", {"dispatch_slug": "gaza", "edition_date": day, "source_count": 1, "story_count": 1})
    if complete:
        _json(root / "sources_manifest.json", [{"source_record_id": "daily-source"}])
        story = {"story_id": "daily-story"}
        if candidate_id:
            story["historical_candidate_id"] = candidate_id
        _json(root / "curation_manifest.json", [story])
    for relative in ("gaza/index.html", "gaza/archive.html"):
        path = case["pages"] / relative
        path.write_text(path.read_text(encoding="utf-8").replace(
            '<ul class="edition-list">',
            f'<ul class="edition-list">\n<li><a href="editions/{day}/">{day}</a></li>',
            1,
        ), encoding="utf-8")
    rss = case["pages"] / "gaza/rss.xml"
    rss.write_text(rss.read_text(encoding="utf-8").replace(
        "</channel>",
        f'<item><guid>https://dispatches.thebluefernco.com/gaza/editions/{day}/</guid></item>\n</channel>',
        1,
    ), encoding="utf-8")
    return _commit(case["pages"], "publish intervening daily edition", "gaza")


def test_committed_authority_approval_replay_and_public_copy(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    first = create_approval(case["root"], case["pages"], case["request_path"])
    path = case["root"] / first["approval_path"]
    raw, timestamp = path.read_bytes(), path.stat().st_mtime_ns
    second = create_approval(case["root"], case["pages"], case["request_path"])
    assert second["status"] == "idempotent_noop"
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == timestamp
    approval = json.loads(raw)
    copy = approval["approved_items"][0]["public_copy"]
    assert copy["summary"].startswith("Gaza Civil Defence said 233")
    assert "attributed" in copy["uncertainty"]
    assert copy["event_date"] == "2026-08-27"
    assert copy["source_published_at"] == "2026-08-27"
    assert approval["audio_authorized"] is approval["social_authorized"] is False

    allegation = _make_case(tmp_path / "allegation", attribution_mode="allegation")
    result = create_approval(allegation["root"], allegation["pages"], allegation["request_path"])
    legal_copy = json.loads((allegation["root"] / result["approval_path"]).read_text(encoding="utf-8"))["approved_items"][0]["public_copy"]
    assert "not an adjudicated finding" in legal_copy["summary"]
    assert "not an adjudicated finding" in legal_copy["uncertainty"]

    changed = dict(case["request"])
    changed["title"] = "Altered catch-up"
    _json(case["request_path"], changed)
    with pytest.raises(GazaHistoricalCatchupError, match="conflicting"):
        create_approval(case["root"], case["pages"], case["request_path"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("review_sha", "0" * 64, "raw SHA-256"),
        ("review_blob", "0" * 40, "blob SHA-1"),
        ("decision_sha", "0" * 64, "raw SHA-256"),
        ("audio", True, "audio and social"),
        ("social", True, "audio and social"),
        ("approver", "Codex automation", "human"),
        ("approver", "Historical Human Reviewer", "independent"),
    ],
)
def test_authority_bindings_and_independence_fail_closed(tmp_path: Path, field: str, value, message: str) -> None:
    kwargs = {field: value} if field in {"audio", "social", "approver"} else {}
    case = _make_case(tmp_path, **kwargs)
    if field == "review_sha":
        case["request"]["review_bindings"][0]["sha256"] = value
    if field == "review_blob":
        case["request"]["review_bindings"][0]["blob_sha1"] = value
    if field == "decision_sha":
        case["request"]["decision_bindings"][0]["sha256"] = value
    _json(case["request_path"], case["request"])
    with pytest.raises(GazaHistoricalCatchupError, match=message):
        create_approval(case["root"], case["pages"], case["request_path"])


def test_worktree_unreviewed_unknown_date_and_stale_bindings_rejected(tmp_path: Path) -> None:
    dirty = _make_case(tmp_path / "dirty")
    (dirty["root"] / dirty["review_path"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(GazaHistoricalCatchupError, match="clean source"):
        create_approval(dirty["root"], dirty["pages"], dirty["request_path"])

    dirty_decision = _make_case(tmp_path / "dirty-decision")
    (dirty_decision["root"] / dirty_decision["decision_path"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(GazaHistoricalCatchupError, match="clean source"):
        create_approval(dirty_decision["root"], dirty_decision["pages"], dirty_decision["request_path"])

    mismatch = _make_case(tmp_path / "mismatch", mismatched_decision=True)
    with pytest.raises(GazaHistoricalCatchupError, match="do not match"):
        create_approval(mismatch["root"], mismatch["pages"], mismatch["request_path"])

    deferred = _make_case(tmp_path / "deferred", decision_value="deferred")
    with pytest.raises(GazaHistoricalCatchupError, match="confirmed"):
        create_approval(deferred["root"], deferred["pages"], deferred["request_path"])

    correction = _make_case(tmp_path / "correction", decision_value="corrected")
    with pytest.raises(GazaHistoricalCatchupError, match="confirmed"):
        create_approval(correction["root"], correction["pages"], correction["request_path"])

    undated = _make_case(tmp_path / "undated", missing_date=True)
    with pytest.raises(GazaHistoricalCatchupError, match="cannot be invented"):
        create_approval(undated["root"], undated["pages"], undated["request_path"])

    stale_source = _make_case(tmp_path / "stale-source")
    stale_source["request"]["source_base_commit"] = "0" * 40
    _json(stale_source["request_path"], stale_source["request"])
    with pytest.raises(GazaHistoricalCatchupError, match="ancestor"):
        create_approval(stale_source["root"], stale_source["pages"], stale_source["request_path"])

    stale_pages = _make_case(tmp_path / "stale-pages")
    stale_pages["request"]["pages_head"] = "0" * 40
    _json(stale_pages["request_path"], stale_pages["request"])
    with pytest.raises(GazaHistoricalCatchupError, match="drifted"):
        create_approval(stale_pages["root"], stale_pages["pages"], stale_pages["request_path"])


@pytest.mark.parametrize("copy_field", ["title", "summary", "attribution", "uncertainty", "canonical_source_url"])
def test_committed_approval_copy_cannot_be_forged(tmp_path: Path, copy_field: str) -> None:
    def mutate(payload: dict) -> None:
        copy = payload["approved_items"][0]["public_copy"]
        copy[copy_field] = "https://evil.example/changed" if copy_field.endswith("url") else "Changed approved prose"
        identity = dict(copy)
        identity.pop("public_copy_sha256", None)
        copy["public_copy_sha256"] = owner.fingerprint(identity)
        public_set = [{"order": 1, "candidate_id": payload["item_order"][0], "public_copy": copy}]
        payload["ordered_public_copy_sha256"] = owner.fingerprint(public_set)

    case = _approve_and_merge(_make_case(tmp_path), mutate_approval=mutate)
    with pytest.raises(GazaHistoricalCatchupError, match="public copy drifted"):
        _plan(case)


def test_approval_topology_plan_preview_stage_and_no_side_authority(tmp_path: Path) -> None:
    case = _approve_and_merge(_make_case(tmp_path))
    bundle = _plan(case)
    result = plan_result(bundle)
    assert result["status"] == "validated_plan"
    assert result["persistent_mutation"] is result["pages_mutation"] is False
    assert result["audio_authorized"] is result["social_authorized"] is False
    source_head = _git(case["root"], "rev-parse", "HEAD")
    pages_head = _git(case["pages"], "rev-parse", "HEAD")
    podcast = (case["pages"] / "gaza/podcast.xml").read_bytes()
    flash = (case["pages"] / "gaza/flash-briefing.json").read_bytes()
    protected = {relative: (case["root"] / relative).read_bytes() for relative in owner.PROTECTED_OWNER_PATHS}

    preview = create_private_preview(bundle, case["root"], case["private"] / "preview")
    replay = create_private_preview(bundle, case["root"], case["private"] / "preview")
    assert preview["status"] == "preview_created" and replay["status"] == "idempotent_noop"
    preview_html = next(Path(preview["preview_root"]).glob("preview.html")).read_text(encoding="utf-8")
    assert bundle.disclosure in preview_html

    staged = create_stage(bundle, case["root"], case["private"] / "stage")
    assert staged["status"] == "stage_created"
    assert create_stage(bundle, case["root"], case["private"] / "stage")["status"] == "idempotent_noop"
    assert verify_stage(bundle, case["root"], case["private"] / "stage")["status"] == "stage_verified"
    release = json.loads(Path(staged["release_manifest"]).read_text(encoding="utf-8"))
    paths = {row["pages_path"] for row in release["entries"]}
    assert "gaza/index.html" in paths and "gaza/archive.html" in paths and "gaza/rss.xml" in paths
    assert "gaza/catchups/gaza-historical-catchup-synthetic/index.html" in paths
    assert not any(path.startswith("gaza/editions/2026-09-04/") for path in paths)
    assert not any("podcast" in path or "flash" in path or "audio" in path for path in paths)
    assert _git(case["root"], "rev-parse", "HEAD") == source_head
    assert _git(case["pages"], "rev-parse", "HEAD") == pages_head
    assert (case["pages"] / "gaza/podcast.xml").read_bytes() == podcast
    assert (case["pages"] / "gaza/flash-briefing.json").read_bytes() == flash
    assert {relative: (case["root"] / relative).read_bytes() for relative in owner.PROTECTED_OWNER_PATHS} == protected
    assert not (case["root"] / "data/queues").exists()
    assert not (case["root"] / "output").exists()


def test_occupied_already_public_topology_and_owner_drift_rejected(tmp_path: Path) -> None:
    occupied = _approve_and_merge(_make_case(tmp_path / "occupied", occupied=True))
    with pytest.raises(GazaHistoricalCatchupError, match="already occupied"):
        _plan(occupied)

    represented = _approve_and_merge(_make_case(tmp_path / "represented", already_public=True))
    with pytest.raises(GazaHistoricalCatchupError, match="already represented"):
        _plan(represented)

    topology = _make_case(tmp_path / "topology")
    created = create_approval(topology["root"], topology["pages"], topology["request_path"])
    extra = topology["root"] / "unrelated.txt"
    extra.write_text("not approval-only\n", encoding="utf-8")
    topology["approval_path"] = created["approval_path"]
    topology["approval_commit"] = _commit(topology["root"], "mixed commit", created["approval_path"], "unrelated.txt")
    _commit(topology["root"], "protected merge", allow_empty=True)
    with pytest.raises(GazaHistoricalCatchupError, match="approval-only"):
        _plan(topology)

    drift = _approve_and_merge(_make_case(tmp_path / "owner-drift"))
    protected = drift["root"] / owner.PROTECTED_OWNER_PATHS[0]
    protected.write_text("# changed owner\n", encoding="utf-8")
    _commit(drift["root"], "owner changed", owner.PROTECTED_OWNER_PATHS[0])
    with pytest.raises(GazaHistoricalCatchupError, match="source changed"):
        _plan(drift)


def test_stage_tampering_forged_manifest_and_history_shrink_fail_closed(tmp_path: Path) -> None:
    case = _approve_and_merge(_make_case(tmp_path))
    bundle = _plan(case)
    stage = create_stage(bundle, case["root"], case["private"] / "stage")
    target = Path(stage["stage_root"])
    edition = target / "site/gaza/catchups/gaza-historical-catchup-synthetic/index.html"
    edition.write_text(edition.read_text(encoding="utf-8").replace("233", "999"), encoding="utf-8")
    manifest_path = target / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = next(item for item in manifest["entries"] if item["pages_path"].endswith("index.html"))
    row["sha256"] = hashlib.sha256(edition.read_bytes()).hexdigest()
    row["length"] = len(edition.read_bytes())
    identity = dict(manifest)
    identity.pop("release_fingerprint", None)
    manifest["release_fingerprint"] = owner.fingerprint(identity)
    _json(manifest_path, manifest)
    with pytest.raises(GazaHistoricalCatchupError, match="bytes or approved prose drifted"):
        verify_stage(bundle, case["root"], case["private"] / "stage")
    pages_head = _git(case["pages"], "rev-parse", "HEAD")
    with pytest.raises(GazaHistoricalCatchupError, match="bytes or approved prose drifted"):
        publish_stage(case["root"], bundle, case["private"] / "stage", push=True)
    assert _git(case["pages"], "rev-parse", "HEAD") == pages_head
    assert _git(case["pages"], "status", "--porcelain", "--untracked-files=all") == ""
    assert not (case["pages"] / "gaza/catchups/gaza-historical-catchup-synthetic").exists()

    # History shrink is checked independently immediately before Pages mutation.
    files, release = owner._stage_payload(bundle)
    clean_target = case["private"] / "history"
    for relative, raw in files.items():
        path = clean_target / "site" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for relative in owner.PUBLIC_ENTRY_NAMES:
        path = clean_target / "site" / relative
        path.write_text(path.read_text(encoding="utf-8").replace("2026-09-01", "removed-date"), encoding="utf-8")
    with pytest.raises(GazaHistoricalCatchupError, match="history-shrink"):
        owner._assert_history_preserved(bundle, clean_target)


def test_atomic_publish_push_replay_and_daily_dedupe(tmp_path: Path) -> None:
    case = _approve_and_merge(_make_case(tmp_path))
    bundle = _plan(case)
    stage_root = case["private"] / "stage"
    create_stage(bundle, case["root"], stage_root)
    podcast = (case["pages"] / "gaza/podcast.xml").read_bytes()
    flash = (case["pages"] / "gaza/flash-briefing.json").read_bytes()
    with pytest.raises(GazaHistoricalCatchupError, match="explicit push"):
        publish_stage(case["root"], bundle, stage_root, push=False)
    published = publish_stage(case["root"], bundle, stage_root, push=True)
    assert published["status"] == "published"
    assert published["pages_commit"] == _git(case["pages"], "rev-parse", "HEAD")
    assert published["pages_commit"] == _git(case["remote"], "rev-parse", "refs/heads/gh-pages")
    assert (case["pages"] / "gaza/podcast.xml").read_bytes() == podcast
    assert (case["pages"] / "gaza/flash-briefing.json").read_bytes() == flash
    assert publish_stage(case["root"], bundle, stage_root, push=True)["status"] == "idempotent_noop"
    assert published_replay_result(
        case["root"], case["pages"], approval_commit=case["approval_commit"], approval_path=case["approval_path"]
    )["status"] == "idempotent_noop"
    state = json.loads((case["root"] / owner.STATE_PREFIX / f"{bundle.catchup_id}.json").read_text(encoding="utf-8"))
    assert state["catchup_id"] == bundle.catchup_id
    assert state["public_path"] == bundle.public_path
    assert state["public_url"] == bundle.public_url

    copy = bundle.items[0]["public_copy"]
    later = {
        "story_id": "daily-reappearance",
        "title": copy["title"],
        "summary": copy["summary"],
        "source_record_ids": ["daily-source"],
        "source_urls": [copy["canonical_source_url"]],
        "publisher_names": [copy["publisher"]],
        "source_dates": [copy["source_published_at"]],
        "category": copy["category"],
    }
    dedupe = dedupe_public_stories(case["root"], "gaza", "2026-09-05", [later], dry_run=True)
    assert dedupe.stories == []
    assert dedupe.report["duplicate_skipped"]
    assert len(json.loads((case["root"] / "data/records/story_memory.json").read_text(encoding="utf-8"))) == 1


def test_dirty_or_wrong_pages_and_pages_drift_rejected(tmp_path: Path) -> None:
    dirty = _approve_and_merge(_make_case(tmp_path / "dirty"))
    (dirty["pages"] / "stray.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(GazaHistoricalCatchupError, match="clean"):
        _plan(dirty)

    wrong = _approve_and_merge(_make_case(tmp_path / "wrong"))
    _git(wrong["pages"], "checkout", "-b", "wrong")
    with pytest.raises(GazaHistoricalCatchupError, match="gh-pages"):
        _plan(wrong)

    drift = _approve_and_merge(_make_case(tmp_path / "drift"))
    _git(drift["pages"], "commit", "--amend", "--no-edit")
    with pytest.raises(GazaHistoricalCatchupError, match="not a strict descendant"):
        _plan(drift)


def test_daily_edition_and_catchup_coexist_with_distinct_navigation_and_rss(tmp_path: Path) -> None:
    case = _approve_and_merge(_make_case(tmp_path))
    _add_daily_publication(case)
    daily_head = _add_daily_publication(case, day="2026-09-05")
    editions_root = case["pages"] / "gaza/editions"
    daily_bytes = {path.relative_to(editions_root): path.read_bytes() for path in editions_root.rglob("*") if path.is_file()}

    bundle = _plan(case)
    assert bundle.approval_pages_head == case["pages_head"]
    assert bundle.pages_head == daily_head
    assert bundle.public_path == "gaza/catchups/gaza-historical-catchup-synthetic/"
    assert bundle.public_url.endswith("/gaza/catchups/gaza-historical-catchup-synthetic/")
    stage = create_stage(bundle, case["root"], case["private"] / "stage")
    site = Path(stage["stage_root"]) / "site"
    assert (site / bundle.public_path / "index.html").is_file()
    assert not (site / "gaza/editions/2026-09-04/index.html").exists()
    for relative in ("gaza/index.html", "gaza/archive.html", "gaza/rss.xml"):
        text = (site / relative).read_text(encoding="utf-8")
        assert "/gaza/editions/2026-09-04/" in text or "editions/2026-09-04/" in text
        assert "/gaza/editions/2026-09-05/" in text or "editions/2026-09-05/" in text
        assert "catchups/gaza-historical-catchup-synthetic/" in text
    archive = (site / "gaza/archive.html").read_text(encoding="utf-8")
    assert "Historical catch-up / 2026-09-04" in archive
    rss = (site / "gaza/rss.xml").read_text(encoding="utf-8")
    assert rss.count(bundle.public_url) == 2
    assert bundle.disclosure in rss
    assert {path.relative_to(editions_root): path.read_bytes() for path in editions_root.rglob("*") if path.is_file()} == daily_bytes


def test_pages_descendant_safety_rejects_collision_history_loss_and_incomplete_publication(tmp_path: Path) -> None:
    collision = _approve_and_merge(_make_case(tmp_path / "collision"))
    _add_daily_publication(collision, candidate_id=collision["candidate"])
    with pytest.raises(GazaHistoricalCatchupError, match="already represented"):
        _plan(collision)

    modified = _approve_and_merge(_make_case(tmp_path / "modified"))
    manifest = modified["pages"] / "gaza/editions/2026-09-01/curation_manifest.json"
    _json(manifest, [{"story_id": "changed-prior-claim"}])
    _commit(modified["pages"], "modify prior claim", "gaza/editions/2026-09-01/curation_manifest.json")
    with pytest.raises(GazaHistoricalCatchupError, match="modified a relevant prior"):
        _plan(modified)

    shrink = _approve_and_merge(_make_case(tmp_path / "shrink"))
    archive = shrink["pages"] / "gaza/archive.html"
    archive.write_text(archive.read_text(encoding="utf-8").replace("2026-09-01", "removed"), encoding="utf-8")
    _commit(shrink["pages"], "drop archive history", "gaza/archive.html")
    with pytest.raises(GazaHistoricalCatchupError, match="dropped Gaza history"):
        _plan(shrink)

    incomplete = _approve_and_merge(_make_case(tmp_path / "incomplete"))
    _add_daily_publication(incomplete, complete=False)
    with pytest.raises(GazaHistoricalCatchupError, match="incomplete Gaza publication"):
        _plan(incomplete)


def test_obsolete_date_keyed_approval_requires_renewed_human_approval(tmp_path: Path) -> None:
    protected_approval = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "approvals/gaza/gaza-historical-catchup-aug29-sep02-2026-batch-01-approval.json"
        ).read_text(encoding="utf-8")
    )
    assert protected_approval["schema_version"] == "gaza_historical_catchup_approval_v1"
    assert "public_path" not in protected_approval and "public_url" not in protected_approval
    assert protected_approval["schema_version"] != owner.APPROVAL_SCHEMA

    case = _make_case(tmp_path)
    created = create_approval(case["root"], case["pages"], case["request_path"])
    path = case["root"] / created["approval_path"]
    approval = json.loads(path.read_text(encoding="utf-8"))
    approval["schema_version"] = "gaza_historical_catchup_approval_v1"
    identity = dict(approval)
    identity.pop("approval_fingerprint", None)
    approval["approval_fingerprint"] = owner.fingerprint(identity)
    _json(path, approval)
    case["approval_path"] = created["approval_path"]
    case["approval_commit"] = _commit(case["root"], "obsolete approval only", created["approval_path"])
    _commit(case["root"], "protected merge", allow_empty=True)
    with pytest.raises(GazaHistoricalCatchupError, match="renewed human approval"):
        _plan(case)

    forged = _make_case(tmp_path / "forged-path")
    forged["request"]["public_path"] = "gaza/editions/2026-09-04/"
    forged["request"]["public_url"] = "https://dispatches.thebluefernco.com/gaza/editions/2026-09-04/"
    _json(forged["request_path"], forged["request"])
    with pytest.raises(GazaHistoricalCatchupError, match="canonical catch-up path"):
        create_approval(forged["root"], forged["pages"], forged["request_path"])
