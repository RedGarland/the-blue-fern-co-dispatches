from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from bluefern_dispatches.gaza_historical_correction import (
    PREVIEW_PATHS,
    REPLACEMENT_PATHS,
    UNCHANGED_PATHS,
    CorrectionValidationError,
    fingerprint_payload,
    plan_correction,
    prepare_correction_proposal,
    create_package_approval,
    sha256_file,
    stage_correction_package,
    verify_staged_package,
)
from bluefern_dispatches.gaza_sources import story_claim_fingerprint
from bluefern_dispatches.historical_agent_archive import build_gaza_published_story_lineage


DATE = "2026-08-29"
CORRECTION_DATE = "2026-09-02"
STORY_ID = "gaza-story-2026-08-29-005"
TITLE = "Synthetic report on Tal al-Hawa motorcycle strike"
PRIOR = "Source A reported 1 killed and 2 injured in a motorcycle strike in Tal al-Hawa."
CORRECTED = (
    "Source B and Source C reported two people killed in the Tal al-Hawa motorcycle strike; "
    "Source B reported two wounded while Source C reported one, leaving the injury count unresolved."
)
DISPUTE = [
    {"value": 2, "unit": "people wounded", "source_url": "https://source-b.example/article"},
    {"value": 1, "unit": "person wounded", "source_url": "https://source-c.example/article"},
]
EVIDENCE = [
    {
        "role": "principal",
        "url": "https://source-b.example/article",
        "supporting_passage": "Source B reported two killed and two wounded in the same strike.",
    },
    {
        "role": "corroborating",
        "url": "https://source-c.example/article",
        "supporting_passage": "Source C reported two killed and one wounded in the same strike.",
    },
]


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _run(repo, "add", "--all")
    _run(repo, "commit", "-m", message)
    return _run(repo, "rev-parse", "HEAD")


def _init_repo(path: Path, branch: str) -> None:
    path.mkdir(parents=True)
    _run(path, "init", "-b", branch)
    _run(path, "config", "user.email", "synthetic@example.test")
    _run(path, "config", "user.name", "Synthetic Reviewer")


def _prior_story(event_date: str = DATE) -> dict[str, object]:
    source = {
        "source_record_id": "synthetic-source-a",
        "title": "Synthetic source title",
        "publisher": "Source A",
        "url": "https://source-a.example/wrapper",
        "canonical_url": "https://source-a.example/article",
        "published_at": f"{DATE}T12:00:00+00:00",
        "dispatch_slug": "gaza",
        "category_hint": "conflict",
        "used_in_story_ids": [STORY_ID],
    }
    source["claim_fingerprint"] = story_claim_fingerprint(source)
    return {
        "story_id": STORY_ID,
        "title": TITLE,
        "summary": PRIOR,
        "category": "conflict",
        "event_date": f"{event_date}T12:00:00+00:00",
        "location": "Tal al-Hawa, Gaza City",
        "development_type": "casualty_event",
        "casualty_counts": {"new_deaths": 1, "new_injuries": 2},
        "attribution": "Source A",
        "publisher_names": ["Source A"],
        "source_record_ids": ["synthetic-source-a"],
        "source_urls": ["https://source-a.example/wrapper"],
        "public_rendered": True,
        "included_in_public_summary": True,
        "_source": source,
    }


def _make_pages(pages: Path) -> tuple[str, dict[str, object]]:
    _init_repo(pages, "gh-pages")
    _run(
        pages,
        "remote",
        "add",
        "origin",
        "https://github.com/RedGarland/the-blue-fern-co-dispatches.git",
    )
    story = _prior_story()
    source = story.pop("_source")
    edition = pages / "gaza" / "editions" / DATE
    _write_json(edition / "curation_manifest.json", [story])
    _write_json(edition / "sources_manifest.json", [source])
    _write_json(
        edition / "dedupe_report.json",
        {
            "included_stories": [
                {
                    "story_id": STORY_ID,
                    "title": TITLE,
                    "classification": "new",
                    "include_decision": "include",
                    "public_rendered": True,
                }
            ]
        },
    )
    _write_json(edition / "edition_manifest.json", {"edition_date": DATE, "story_count": 1})
    (edition / "index.html").write_text(
        f"<html><body><main><h1>{TITLE}</h1><p>{PRIOR}</p></main></body></html>",
        encoding="utf-8",
    )
    (edition / "source_quality_report.md").write_text("# Synthetic source quality\n", encoding="utf-8")
    existing_text = {
        "gaza/rss.xml": "<rss><channel><item><guid>edition</guid></item></channel></rss>",
        f"gaza/audio/{DATE}-transcript.html": f"<html><body><main><p>{PRIOR}</p></main></body></html>",
        "gaza/podcast.xml": "<rss><channel><item><guid>episode</guid></item></channel></rss>",
        "gaza/audio/podcast.xml": "<rss><channel><item><guid>episode</guid></item></channel></rss>",
        "gaza/audio/index.html": f"<html><body><main><p>{TITLE}</p></main></body></html>",
        "gaza/index.html": f"<html><body><main><p>{TITLE}</p></main></body></html>",
        "gaza/archive.html": f"<html><body><main><p>{TITLE}</p></main></body></html>",
        "index.html": f"<html><body><main><p>{TITLE}</p></main></body></html>",
    }
    for relative, text in existing_text.items():
        path = pages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _write_json(
        pages / "gaza" / "audio" / f"{DATE}.json",
        {"audio_status": "generated", "script_text": PRIOR, "audio_file": f"{DATE}.mp3"},
    )
    (pages / "gaza" / "audio" / f"{DATE}.mp3").write_bytes(b"ID3-prior-synthetic-audio")
    _write_json(
        pages / "gaza" / "flash-briefing.json",
        [{"uid": DATE, "mainText": PRIOR}],
    )
    return _commit(pages, "Synthetic prior public state"), story


def _build_case(tmp_path: Path, *, with_approval: bool = True) -> dict[str, object]:
    pages = tmp_path / "pages"
    source = tmp_path / "source"
    inputs = tmp_path / "inputs"
    proposal_path = tmp_path / "proposal.json"
    pages_head, story = _make_pages(pages)
    _init_repo(source, "feature")

    lineage = build_gaza_published_story_lineage(
        pages,
        pages_commit=pages_head,
        story_id=STORY_ID,
        edition_date=DATE,
        expected_title=TITLE,
        expected_prior_claim=PRIOR,
        backfill_reason="Synthetic correction-lineage test fixture.",
        created_at="2026-09-01T00:00:00+00:00",
    )
    event_fingerprint = lineage["stable_event_identity"]["fingerprint"]
    prior_fingerprint = lineage["prior_claim_identity"]["fingerprint"]
    corrected_fingerprint = "sha256:" + hashlib.sha256(CORRECTED.encode()).hexdigest()
    lineage_path = source / "data" / "agent-history" / "gaza" / "lineage" / "published-stories" / f"{STORY_ID}.json"
    _write_json(lineage_path, lineage)
    raw_path = source / "data" / "agent-history" / "gaza" / "raw" / "raw.json"
    normalized_path = source / "data" / "agent-history" / "gaza" / "normalized" / "normalized.json"
    report_path = source / "data" / "agent-history" / "gaza" / "reports" / "report.json"
    _write_json(raw_path, {"synthetic": "raw"})
    _write_json(normalized_path, {"synthetic": "normalized"})
    _write_json(report_path, {"synthetic": "report"})
    raw_identity = hashlib.sha256(b"synthetic-candidate-identity").hexdigest()
    review = {
        "schema_version": "gaza_historical_editorial_review_v2",
        "raw_sha256": raw_identity,
        "decision": "corrected",
        "resulting_review_state": "substantively_reviewed",
        "current_publication_eligible": False,
        "current_publication_approval": False,
        "candidate_event_fingerprint": corrected_fingerprint,
        "decision_reason": "Two independent sources update the death count while disagreeing on injuries.",
        "normalized_artifact_sha256": sha256_file(normalized_path),
        "report_artifact_sha256": sha256_file(report_path),
        "attribution_assessment": {
            "safe_future_wording": CORRECTED,
            "attributed_to": "Source B and Source C",
            "disputed_values": DISPUTE,
            "dispute_unresolved": True,
        },
        "evidence_references": EVIDENCE,
        "correction_lineage": {
            "prior_reference": {"type": "published_story", "id": STORY_ID, "edition_date": DATE},
            "prior_event_fingerprint": {
                "event_identity": event_fingerprint,
                "fingerprint": prior_fingerprint,
            },
            "corrected_event_fingerprint": {
                "event_identity": event_fingerprint,
                "fingerprint": corrected_fingerprint,
            },
            "previous_value": 1,
            "corrected_value": 2,
            "field_or_claim": "casualty_counts.new_deaths",
            "prior_public_artifact_overwritten": False,
        },
    }
    review_path = source / "data" / "agent-history" / "gaza" / "reviews" / "synthetic-corrected.json"
    _write_json(review_path, review)
    audit = {
        "review_artifact_sha256": sha256_file(review_path),
        "raw_sha256": raw_identity,
        "normalized_artifact_sha256": sha256_file(normalized_path),
        "report_artifact_sha256": sha256_file(report_path),
        "candidate_event_fingerprint": corrected_fingerprint,
        "decision": "corrected",
        "resulting_review_state": "substantively_reviewed",
        "raw_archive_artifact_path": raw_path.relative_to(source).as_posix(),
        "raw_archive_artifact_sha256": sha256_file(raw_path),
        "normalized_artifact_path": normalized_path.relative_to(source).as_posix(),
        "report_artifact_path": report_path.relative_to(source).as_posix(),
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "archive_content_change_authorized": False,
        "edition_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
    }
    audit_path = source / "data" / "agent-history" / "gaza" / "reviews" / "decisions" / "synthetic.json"
    _write_json(audit_path, audit)
    source_commit = _commit(source, "Synthetic private reviewed state")

    proposal_root = tmp_path / "proposals"
    prepared = prepare_correction_proposal(
        source_root=source,
        pages_root=pages,
        story_id=STORY_ID,
        review_path=review_path.relative_to(source).as_posix(),
        decision_audit_path=audit_path.relative_to(source).as_posix(),
        correction_date=CORRECTION_DATE,
        output_root=proposal_root,
        tts_provider="synthetic-tts",
        tts_model="synthetic-model-v1",
        tts_voice="synthetic-voice",
    )
    inputs = Path(prepared["proposal_path"]).parent
    proposal_path = Path(prepared["proposal_path"])
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    correction = proposal["correction"]
    approval_path = "approvals/synthetic-correction.json"
    if with_approval:
        create_package_approval(
            source_root=source,
            pages_root=pages,
            proposal_path=proposal_path,
            input_root=inputs,
            approval_request_path=inputs / "approval_request.json",
            output_path=source / approval_path,
            approval_id="synthetic-independent-approval",
            approver="Independent Synthetic Reviewer",
            approved_at="2026-09-02T12:00:00+00:00",
        )
        _commit(source, "Independent synthetic approval")
    rendered_audio = tmp_path / "rendered-correction.mp3"
    rendered_audio.write_bytes(b"ID3-synthetic-approved-render")
    return {
        "source": source,
        "pages": pages,
        "inputs": inputs,
        "proposal_path": proposal_path,
        "proposal": proposal,
        "correction": correction,
        "approval_path": approval_path,
        "rendered_audio": rendered_audio,
        "proposal_result": prepared,
        "proposal_root": proposal_root,
        "review_relative": review_path.relative_to(source).as_posix(),
        "audit_relative": audit_path.relative_to(source).as_posix(),
    }


def _plan(case: dict[str, object]) -> dict[str, object]:
    return plan_correction(
        source_root=case["source"],
        pages_root=case["pages"],
        proposal_path=case["proposal_path"],
        input_root=case["inputs"],
        approval_ref="HEAD",
        approval_path=case["approval_path"],
    )


def _rewrite_proposal(case: dict[str, object]) -> None:
    proposal = case["proposal"]
    proposal["proposal_sha256"] = fingerprint_payload(proposal, "proposal_sha256")
    _write_json(case["proposal_path"], proposal)


def _prepare_again(case: dict[str, object], output_root: Path) -> dict[str, object]:
    return prepare_correction_proposal(
        source_root=case["source"],
        pages_root=case["pages"],
        story_id=STORY_ID,
        review_path=case["review_relative"],
        decision_audit_path=case["audit_relative"],
        correction_date=CORRECTION_DATE,
        output_root=output_root,
        tts_provider="synthetic-tts",
        tts_model="synthetic-model-v1",
        tts_voice="synthetic-voice",
    )


def _cli(*args: object) -> dict[str, object]:
    script = Path(__file__).parents[1] / "scripts" / "gaza_historical_correction.py"
    result = subprocess.run(
        [sys.executable, str(script), *[str(value) for value in args]],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    return json.loads(result.stdout)


def test_cli_executes_complete_nonpublication_workflow(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    cli_root = tmp_path / "cli-proposals"
    proposed = _cli(
        "--mode", "propose",
        "--source-root", case["source"],
        "--pages-root", case["pages"],
        "--story-id", STORY_ID,
        "--review-path", case["review_relative"],
        "--decision-audit-path", case["audit_relative"],
        "--correction-date", CORRECTION_DATE,
        "--proposal-root", cli_root,
        "--tts-provider", "synthetic-tts",
        "--tts-model", "synthetic-model-v1",
        "--tts-voice", "synthetic-voice",
    )
    assert proposed["status"] == "proposal_created"
    proposal_dir = Path(proposed["proposal_path"]).parent
    approval_relative = "approvals/cli-synthetic-correction.json"
    approved = _cli(
        "--mode", "approve-package",
        "--source-root", case["source"],
        "--pages-root", case["pages"],
        "--proposal", proposal_dir / "proposal.json",
        "--input-root", proposal_dir,
        "--approval-request", proposal_dir / "approval_request.json",
        "--approval-output", case["source"] / approval_relative,
        "--approval-id", "cli-independent-approval",
        "--approver", "CLI Independent Reviewer",
        "--approved-at", "2026-09-02T12:00:00+00:00",
    )
    assert approved["status"] == "package_approval_created"
    _commit(case["source"], "Commit CLI synthetic approval")
    common = (
        "--source-root", case["source"],
        "--pages-root", case["pages"],
        "--proposal", proposal_dir / "proposal.json",
        "--input-root", proposal_dir,
        "--approval-ref", "HEAD",
        "--approval-path", approval_relative,
    )
    planned = _cli("--mode", "plan", *common)
    assert planned["status"] == "validated_plan"
    staging = tmp_path / "cli-staging"
    staged = _cli(
        "--mode", "stage",
        *common,
        "--rendered-audio", case["rendered_audio"],
        "--staging-root", staging,
    )
    assert staged["status"] == "staged_correction_package"
    verified = _cli(
        "--mode", "verify-staged",
        *common,
        "--package-root", staged["package_path"],
    )
    assert verified["status"] == "staged_package_verified"
    assert verified["publication_authorized"] is False
    assert _run(case["pages"], "status", "--porcelain") == ""


def test_proposal_is_non_authorizing_deterministic_and_replay_safe(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    source_head = _run(case["source"], "rev-parse", "HEAD")
    pages_head = _run(case["pages"], "rev-parse", "HEAD")
    first = case["proposal_result"]
    replay = _prepare_again(case, case["proposal_root"])
    second_root = tmp_path / "second-proposal-root"
    independent = _prepare_again(case, second_root)
    assert first["status"] == "proposal_created"
    assert replay["status"] == "idempotent_noop"
    assert independent["proposal_sha256"] == first["proposal_sha256"]
    assert independent["artifact_set_sha256"] == first["artifact_set_sha256"]
    assert _run(case["source"], "rev-parse", "HEAD") == source_head
    assert _run(case["pages"], "rev-parse", "HEAD") == pages_head
    assert _run(case["pages"], "status", "--porcelain") == ""
    request = json.loads((case["inputs"] / "approval_request.json").read_text(encoding="utf-8"))
    assert request["package_authorized"] is False
    assert request["audio_authorized"] is False
    assert request["publication_authorized"] is False
    assert not list(case["inputs"].rglob("*.mp3"))

    preview = next((case["inputs"] / "preview").rglob("*.html"))
    preview.write_text("conflict", encoding="utf-8")
    with pytest.raises(CorrectionValidationError, match="conflicts with deterministic output"):
        _prepare_again(case, case["proposal_root"])


def test_approval_is_created_from_request_and_replay_is_safe(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    output = case["source"] / "approvals" / "reviewer-approval.json"
    kwargs = {
        "source_root": case["source"],
        "pages_root": case["pages"],
        "proposal_path": case["proposal_path"],
        "input_root": case["inputs"],
        "approval_request_path": case["inputs"] / "approval_request.json",
        "output_path": output,
        "approval_id": "reviewer-approval-1",
        "approver": "Independent Reviewer",
        "approved_at": "2026-09-02T12:00:00+00:00",
    }
    first = create_package_approval(**kwargs)
    second = create_package_approval(**kwargs)
    assert first["status"] == "package_approval_created"
    assert second["status"] == "idempotent_noop"
    approval = json.loads(output.read_text(encoding="utf-8"))
    request = json.loads((case["inputs"] / "approval_request.json").read_text(encoding="utf-8"))
    for key in (
        "proposal_sha256",
        "correction_id",
        "source_commit",
        "pages_head",
        "artifact_set_sha256",
        "audio_request_sha256",
    ):
        assert approval[key] == request[key]
    assert approval["package_authorized"] is True
    assert approval["audio_authorized"] is True
    assert approval["publication_authorized"] is False
    with pytest.raises(CorrectionValidationError, match="conflicting"):
        create_package_approval(**{**kwargs, "approver": "Conflicting Reviewer"})


def test_approval_rejects_self_consistent_but_nonvalidator_request(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    request_path = tmp_path / "invented-approval-request.json"
    request = json.loads(
        (case["inputs"] / "approval_request.json").read_text(encoding="utf-8")
    )
    request["artifact_set_sha256"] = "sha256:" + "1" * 64
    request["request_sha256"] = fingerprint_payload(request, "request_sha256")
    _write_json(request_path, request)
    with pytest.raises(CorrectionValidationError, match="not validator-produced"):
        create_package_approval(
            source_root=case["source"],
            pages_root=case["pages"],
            proposal_path=case["proposal_path"],
            input_root=case["inputs"],
            approval_request_path=request_path,
            output_path=case["source"] / "approvals" / "invented.json",
            approval_id="invented-approval",
            approver="Synthetic Reviewer",
            approved_at="2026-09-02T12:00:00+00:00",
        )


def test_approval_output_must_be_commit_ready_source_artifact(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    with pytest.raises(CorrectionValidationError, match="source approvals directory"):
        create_package_approval(
            source_root=case["source"],
            pages_root=case["pages"],
            proposal_path=case["proposal_path"],
            input_root=case["inputs"],
            approval_request_path=case["inputs"] / "approval_request.json",
            output_path=tmp_path / "detached-approval.json",
            approval_id="detached-approval",
            approver="Synthetic Reviewer",
            approved_at="2026-09-02T12:00:00+00:00",
        )


def test_working_tree_approval_cannot_supply_authority(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    create_package_approval(
        source_root=case["source"],
        pages_root=case["pages"],
        proposal_path=case["proposal_path"],
        input_root=case["inputs"],
        approval_request_path=case["inputs"] / "approval_request.json",
        output_path=case["source"] / case["approval_path"],
        approval_id="uncommitted-approval",
        approver="Synthetic Reviewer",
        approved_at="2026-09-02T12:00:00+00:00",
    )
    with pytest.raises(CorrectionValidationError, match="committed approval artifact"):
        _plan(case)


def test_successful_full_package_plan_is_read_only(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    before = {
        "source": _run(case["source"], "status", "--porcelain"),
        "pages": _run(case["pages"], "status", "--porcelain"),
        "files": sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()),
    }
    plan = _plan(case)
    after = {
        "source": _run(case["source"], "status", "--porcelain"),
        "pages": _run(case["pages"], "status", "--porcelain"),
        "files": sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()),
    }
    assert plan["status"] == "validated_plan"
    assert len(plan["representations"]) == len(PREVIEW_PATHS)
    assert len(plan["unchanged_dependencies"]) == len(UNCHANGED_PATHS)
    assert plan["publication_authorized"] is False
    assert before == after


def test_missing_approval_fails_closed_for_gz01_shaped_state(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    with pytest.raises(CorrectionValidationError, match="committed approval artifact"):
        _plan(case)


def test_cross_domain_proposal_is_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    case["proposal"]["domain"] = "food-line"
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError, match="cross-domain"):
        _plan(case)


def test_documented_json_schemas_parse() -> None:
    schema_root = Path(__file__).parents[1] / "docs" / "schemas"
    for name in (
        "gaza-formal-historical-correction-proposal-v1.schema.json",
        "gaza-formal-historical-correction-audio-request-v1.schema.json",
        "gaza-formal-historical-correction-approval-request-v1.schema.json",
        "gaza-formal-historical-correction-approval-v1.schema.json",
    ):
        payload = json.loads((schema_root / name).read_text(encoding="utf-8"))
        assert payload["additionalProperties"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["correction"].update(story_id="gaza-story-2026-08-29-999"), "correction identity"),
        (lambda p: p["correction"].update(owning_edition_date="2026-08-28"), "owning edition"),
        (lambda p: p["correction"].update(stable_event_fingerprint="sha256:" + "1" * 64), "correction identity"),
        (lambda p: p["correction"].update(prior_claim_fingerprint="topic_fingerprint_v1:" + "1" * 16), "correction identity"),
        (lambda p: p["correction"]["casualty_change"].update(operation="add"), "must replace one death"),
    ],
)
def test_identity_and_double_counting_mismatches_fail_closed(
    tmp_path: Path, mutation, message: str
) -> None:
    case = _build_case(tmp_path)
    mutation(case["proposal"])
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError, match=message):
        _plan(case)


def test_ambiguous_lineage_fails_closed(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    lineage = next((case["source"] / "data/agent-history/gaza/lineage/published-stories").glob("*.json"))
    duplicate = lineage.with_name("duplicate.json")
    duplicate.write_bytes(lineage.read_bytes())
    with pytest.raises(CorrectionValidationError, match="ambiguous"):
        _plan(case)


@pytest.mark.parametrize("target", ["review", "decision_audit", "artifact", "pages"])
def test_tampered_evidence_or_pages_hash_fails_closed(tmp_path: Path, target: str) -> None:
    case = _build_case(tmp_path)
    proposal = case["proposal"]
    if target == "review":
        path = case["source"] / proposal["private_evidence"]["review_path"]
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif target == "decision_audit":
        path = case["source"] / proposal["private_evidence"]["decision_audit_path"]
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif target == "artifact":
        path = case["source"] / "data/agent-history/gaza/normalized/normalized.json"
        path.write_text("tampered", encoding="utf-8")
    else:
        path = case["pages"] / f"gaza/editions/{DATE}/index.html"
        path.write_text("drift", encoding="utf-8")
    with pytest.raises(CorrectionValidationError):
        _plan(case)


def test_unrelated_tal_al_hawa_incident_is_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    case["proposal"]["correction"]["corrected_claim"] = "A separate Tal al-Hawa incident."
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError, match="reviewed wording"):
        _plan(case)


def test_unresolved_injury_count_cannot_be_collapsed(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    case["proposal"]["correction"]["injury_disagreement"] = {
        "unresolved": False,
        "reports": DISPUTE,
    }
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError, match="must remain unresolved"):
        _plan(case)


def test_partial_html_only_package_is_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    case["proposal"]["pages_state"]["representations"] = [
        row for row in case["proposal"]["pages_state"]["representations"] if row["role"] == "edition_html"
    ]
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError, match="partial"):
        _plan(case)


@pytest.mark.parametrize("role", ["original_audio_metadata", "correction_transcript", "podcast", "flash_briefing"])
def test_stale_audio_transcript_and_feed_representation_is_rejected(tmp_path: Path, role: str) -> None:
    case = _build_case(tmp_path)
    row = next(row for row in case["proposal"]["pages_state"]["representations"] if row["role"] == role)
    path = case["inputs"] / row["input_path"]
    path.write_text("stale representation", encoding="utf-8")
    row["corrected_sha256"] = sha256_file(path)
    set_payload = [
        {"role": item["role"], "public_path": item["public_path"], "corrected_sha256": item["corrected_sha256"]}
        for item in sorted(case["proposal"]["pages_state"]["representations"], key=lambda value: value["role"])
    ]
    case["proposal"]["pages_state"]["artifact_set_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(set_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _rewrite_proposal(case)
    with pytest.raises(CorrectionValidationError):
        _plan(case)


def test_pages_history_drift_is_rejected(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    (case["pages"] / "unrelated.txt").write_text("drift", encoding="utf-8")
    _commit(case["pages"], "Pages history drift")
    with pytest.raises(CorrectionValidationError, match="history drifted"):
        _plan(case)


def test_stage_is_atomic_idempotent_and_rejects_conflict(tmp_path: Path, monkeypatch) -> None:
    case = _build_case(tmp_path)
    plan = _plan(case)
    staging = tmp_path / "staging"
    original_copy = __import__("shutil").copyfile
    calls = 0

    def fail_second_copy(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic atomic failure")
        return original_copy(source, destination)

    monkeypatch.setattr("bluefern_dispatches.gaza_historical_correction.shutil.copyfile", fail_second_copy)
    with pytest.raises(OSError, match="synthetic atomic failure"):
        stage_correction_package(
            plan=plan,
            input_root=case["inputs"],
            rendered_audio_path=case["rendered_audio"],
            staging_root=staging,
            source_root=case["source"],
            pages_root=case["pages"],
        )
    assert not (staging / plan["correction_id"]).exists()

    monkeypatch.setattr("bluefern_dispatches.gaza_historical_correction.shutil.copyfile", original_copy)
    first = stage_correction_package(
        plan=plan,
        input_root=case["inputs"],
        rendered_audio_path=case["rendered_audio"],
        staging_root=staging,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    second = stage_correction_package(
        plan=plan,
        input_root=case["inputs"],
        rendered_audio_path=case["rendered_audio"],
        staging_root=staging,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    assert first["status"] == "staged_correction_package"
    assert second["status"] == "idempotent_noop"
    package_root = staging / plan["correction_id"]
    verified = verify_staged_package(
        plan=plan,
        package_root=package_root,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    assert verified["status"] == "staged_package_verified"
    assert verified["publication_authorized"] is False
    staged_manifest = json.loads((package_root / "package_manifest.json").read_text(encoding="utf-8"))
    assert len(staged_manifest["representations"]) == len(REPLACEMENT_PATHS)
    assert staged_manifest["rendered_audio_sha256"] == sha256_file(case["rendered_audio"])
    assert staged_manifest["audio_request"]["sha256"] == case["proposal"]["pages_state"]["audio_request"]["sha256"]
    for role in ("podcast", "audio_podcast"):
        row = next(item for item in staged_manifest["representations"] if item["role"] == role)
        feed = ElementTree.fromstring((package_root / row["public_path"]).read_bytes())
        correction_item = next(
            item
            for item in feed.findall(".//item")
            if item.findtext("guid") == plan["correction_id"]
        )
        assert correction_item.find("enclosure").attrib["length"] == str(
            case["rendered_audio"].stat().st_size
        )
    manifest = staging / plan["correction_id"] / "package_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["approval_id"] = "conflict"
    _write_json(manifest, payload)
    with pytest.raises(CorrectionValidationError, match="conflicting"):
        stage_correction_package(
            plan=plan,
            input_root=case["inputs"],
            rendered_audio_path=case["rendered_audio"],
            staging_root=staging,
            source_root=case["source"],
            pages_root=case["pages"],
        )


def test_staged_verification_rejects_source_and_pages_drift(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    plan = _plan(case)
    staging = tmp_path / "staging"
    result = stage_correction_package(
        plan=plan,
        input_root=case["inputs"],
        rendered_audio_path=case["rendered_audio"],
        staging_root=staging,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    package_root = Path(result["package_path"])
    (case["pages"] / "working-tree-drift").write_text("drift", encoding="utf-8")
    with pytest.raises(CorrectionValidationError, match="Pages repository is dirty"):
        verify_staged_package(
            plan=plan,
            package_root=package_root,
            source_root=case["source"],
            pages_root=case["pages"],
        )
    (case["pages"] / "working-tree-drift").unlink()
    (case["source"] / "source-drift").write_text("drift", encoding="utf-8")
    _commit(case["source"], "Synthetic source drift")
    with pytest.raises(CorrectionValidationError, match="source history drifted"):
        verify_staged_package(
            plan=plan,
            package_root=package_root,
            source_root=case["source"],
            pages_root=case["pages"],
        )


def test_no_new_edition_second_story_scheduler_or_publication_side_effect(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    plan = _plan(case)
    assert plan["owning_edition_date"] == DATE
    assert plan["correction_date"] != DATE
    assert all(f"gaza/editions/{CORRECTION_DATE}/" not in row["public_path"] for row in plan["representations"])
    curation_row = next(row for row in plan["representations"] if row["role"] == "curation_manifest")
    curation = json.loads((case["inputs"] / curation_row["input_path"]).read_text(encoding="utf-8"))
    assert [story["story_id"] for story in curation] == [STORY_ID]
    assert plan["pages_mutation"] is False
    assert plan["publication_authorized"] is False
    assert not any("scheduler" in row["public_path"] for row in plan["representations"])
