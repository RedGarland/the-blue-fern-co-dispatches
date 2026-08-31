from __future__ import annotations

import hashlib
import html
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
    _EditionAudioOwnership,
    fingerprint_payload,
    plan_correction,
    prepare_correction_proposal,
    create_package_approval,
    _correction_notice_html,
    _load_json_document,
    _public_correction_record,
    _reader_correction_copy,
    _read_text_artifact,
    _render_curation_manifest_json,
    _render_dedupe_report_json,
    _render_edition_manifest_json,
    _render_flash_briefing_json,
    _render_original_audio_metadata_json,
    _render_story_scoped_edition_html,
    _validate_edition_audio_ownership,
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

JSON_PRESERVATION_FIXTURES = (
    Path(__file__).parent
    / "fixtures"
    / "gaza"
    / "correction_json_byte_preservation"
)


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_exact_text(
    path: Path,
    text: str,
    *,
    newline: str,
    final_newline: bool,
    bom: bool,
) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    formatted = normalized.replace("\n", newline) + (newline if final_newline else "")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + formatted.encode("utf-8"))


def _fixture_document(
    tmp_path: Path,
    name: str,
    *,
    newline: str = "\n",
    final_newline: bool = True,
    bom: bool = False,
):
    source = JSON_PRESERVATION_FIXTURES / name
    path = tmp_path / name
    _write_exact_text(
        path,
        source.read_text(encoding="utf-8"),
        newline=newline,
        final_newline=final_newline,
        bom=bom,
    )
    return path, _load_json_document(path, name)


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


def _synthetic_public_state() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    story = _prior_story()
    source = story.pop("_source")
    other_stories = [
        {
            "story_id": f"gaza-story-2026-08-29-00{index}",
            "title": f"Unrelated synthetic story {index}",
            "summary": f"Unrelated synthetic summary {index}.",
            "category": "humanitarian",
            "event_date": f"{DATE}T10:0{index}:00+00:00",
            "source_record_ids": [f"unrelated-source-{index}"],
            "source_urls": [f"https://unrelated.example/article-{index}"],
            "public_rendered": True,
            "included_in_public_summary": True,
        }
        for index in (1, 2)
    ]
    other_sources = [
        {
            "source_record_id": f"unrelated-source-{index}",
            "title": f"Unrelated source {index}",
            "publisher": "Unrelated",
            "url": f"https://unrelated.example/article-{index}",
            "published_at": f"{DATE}T10:0{index}:00+00:00",
        }
        for index in (1, 2)
    ]
    return [story, *other_stories], [source, *other_sources]


def _synthetic_edition_html() -> str:
    return (
        "<!doctype html><html><body><main>\n"
        f"<h1>Gaza Dispatch \u2014 {DATE}</h1>\n"
        "<h2>Today\u2019s Read</h2>\n"
        "<p>Synthetic lead paragraph.</p>\n"
        f"<p>{PRIOR}</p>\n"
        "<p>Unrelated synthetic summary 1.</p>\n"
        "<p>Unrelated synthetic summary 2.</p>\n"
        "<h2>At A Glance</h2>\n<ul><li>Three developments.</li></ul>\n"
        "<h2>Core Gaza Developments</h2>\n"
        f"<article><h3>{TITLE}</h3>"
        "<p><em>Source A \u00b7 conflict \u00b7 Gaza \u00b7 August 29, 2026</em></p>"
        f"<p>{PRIOR}</p>"
        '<p><strong>Sources:</strong></p><ul><li><a href="https://source-a.example/wrapper">'
        "Synthetic source title</a> - Source A</li></ul></article>\n"
        "<article><h3>Unrelated synthetic story 1</h3>"
        "<p><em>Unrelated \u00b7 humanitarian \u00b7 Gaza \u00b7 August 29, 2026</em></p>"
        "<p>Unrelated synthetic summary 1.</p>"
        '<a href="https://unrelated.example/article-1">Source</a></article>\n'
        "<article><h3>Unrelated synthetic story 2</h3>"
        "<p><em>Unrelated \u00b7 humanitarian \u00b7 Gaza \u00b7 August 29, 2026</em></p>"
        "<p>Unrelated synthetic summary 2.</p>"
        '<a href="https://unrelated.example/article-2">Source</a></article>\n'
        "</main></body></html>"
    )


def _synthetic_correction(*, corrected_claim: str = CORRECTED) -> dict[str, object]:
    return {
        "correction_id": "gaza-correction-v1-synthetic",
        "story_id": STORY_ID,
        "owning_edition_date": DATE,
        "correction_date": CORRECTION_DATE,
        "stable_event_fingerprint": "sha256:" + "1" * 64,
        "prior_claim_fingerprint": "topic_fingerprint_v1:" + "2" * 16,
        "corrected_claim_fingerprint": "sha256:" + "3" * 64,
        "prior_claim": PRIOR,
        "corrected_claim": corrected_claim,
        "change_reason": "Synthetic correction.",
        "source_attribution": "Source B and Source C",
        "evidence_references": EVIDENCE,
        "casualty_change": {
            "field": "casualty_counts.new_deaths",
            "previous_value": 1,
            "corrected_value": 2,
            "operation": "replace",
        },
        "injury_disagreement": {"unresolved": True, "reports": DISPUTE},
    }


def _render_synthetic_html(
    source: str | None = None, *, corrected_claim: str = CORRECTED
) -> str:
    curation, sources = _synthetic_public_state()
    return _render_story_scoped_edition_html(
        source if source is not None else _synthetic_edition_html(),
        correction=_synthetic_correction(corrected_claim=corrected_claim),
        curation=curation,
        sources=sources,
    )


def _make_pages(
    pages: Path,
    *,
    text_format: tuple[str, bool, bool] | None = None,
) -> tuple[str, dict[str, object]]:
    _init_repo(pages, "gh-pages")
    _run(
        pages,
        "remote",
        "add",
        "origin",
        "https://github.com/RedGarland/the-blue-fern-co-dispatches.git",
    )
    curation, sources = _synthetic_public_state()
    story = curation[0]
    edition = pages / "gaza" / "editions" / DATE
    _write_json(edition / "curation_manifest.json", curation)
    _write_json(edition / "sources_manifest.json", sources)
    _write_json(
        edition / "dedupe_report.json",
        {
            "edition_date": DATE,
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
    _write_json(edition / "edition_manifest.json", {"edition_date": DATE, "story_count": 3})
    (edition / "index.html").write_text(_synthetic_edition_html(), encoding="utf-8")
    (edition / "source_quality_report.md").write_text("# Synthetic source quality\n", encoding="utf-8")
    prior_audio = b"ID3-prior-synthetic-audio"
    prior_audio_url = f"/gaza/audio/{DATE}.mp3"
    prior_audio_public_url = f"https://dispatches.thebluefernco.com{prior_audio_url}"
    prior_transcript_url = (
        f"https://dispatches.thebluefernco.com/gaza/audio/{DATE}-transcript.html"
    )
    prior_podcast_item = (
        f"<item><title>Gaza Briefing for {DATE}</title>"
        f"<link>{prior_transcript_url}</link><guid>{prior_transcript_url}</guid>"
        f'<enclosure url="{prior_audio_public_url}" length="{len(prior_audio)}" '
        'type="audio/mpeg" /></item>'
    )
    existing_text = {
        "gaza/rss.xml": "<rss><channel><item><guid>edition</guid></item></channel></rss>",
        f"gaza/audio/{DATE}-transcript.html": (
            f'<html><body><main><audio src="{prior_audio_url}"></audio>'
            f"<p>{PRIOR}</p></main></body></html>"
        ),
        "gaza/podcast.xml": f"<rss><channel>{prior_podcast_item}</channel></rss>",
        "gaza/audio/podcast.xml": f"<rss><channel>{prior_podcast_item}</channel></rss>",
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
        {
            "dispatch_slug": "gaza",
            "edition_date": DATE,
            "audio_status": "generated",
            "script_text": PRIOR,
            "audio_file": f"{DATE}.mp3",
            "audio_url": prior_audio_url,
            "audio_mime_type": "audio/mpeg",
            "audio_file_size_bytes": len(prior_audio),
            "transcript_url": prior_transcript_url,
            "edition_url": f"https://dispatches.thebluefernco.com/gaza/editions/{DATE}/",
        },
    )
    (pages / "gaza" / "audio" / f"{DATE}.mp3").write_bytes(prior_audio)
    _write_json(
        pages / "gaza" / "flash-briefing.json",
        [
            {
                "uid": f"gaza-{DATE}",
                "mainText": PRIOR,
                "redirectionUrl": prior_audio_url,
            }
        ],
    )
    if text_format is not None:
        newline, final_newline, bom = text_format
        existing_text_roles = {
            "edition_html", "rss", "original_transcript", "podcast",
            "audio_podcast", "audio_index", "gaza_index", "gaza_archive",
            "root_index",
        }
        for role in existing_text_roles:
            path = pages / REPLACEMENT_PATHS[role].format(
                date=DATE, correction_id="unused"
            )
            text = path.read_text(encoding="utf-8")
            _write_exact_text(
                path,
                text,
                newline=newline,
                final_newline=final_newline,
                bom=bom,
            )
    return _commit(pages, "Synthetic prior public state"), story


def _build_case(
    tmp_path: Path,
    *,
    with_approval: bool = True,
    text_format: tuple[str, bool, bool] | None = None,
) -> dict[str, object]:
    pages = tmp_path / "pages"
    source = tmp_path / "source"
    inputs = tmp_path / "inputs"
    proposal_path = tmp_path / "proposal.json"
    pages_head, story = _make_pages(pages, text_format=text_format)
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
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script), *[str(value) for value in args]],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
        env=environment,
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


def test_story_scoped_html_updates_today_and_body_only() -> None:
    source = _synthetic_edition_html()
    rendered = _render_synthetic_html(source)
    correction = _synthetic_correction()
    reader = _reader_correction_copy(correction)
    notice = _correction_notice_html(correction)
    expected = source.replace(
        f"<p>{PRIOR}</p>",
        f'<p><a href="#{STORY_ID}">{reader.today_update}</a></p>',
        1,
    )
    expected = expected.replace(
        f"<p>{PRIOR}</p>", f"<p>{reader.story_update}</p>", 1
    )
    expected = expected.replace(
        f"<article><h3>{TITLE}</h3>",
        f'<article id="{STORY_ID}"><h3>{TITLE}</h3>',
        1,
    )
    target_end = "Synthetic source title</a> - Source A</li></ul></article>"
    expected = expected.replace(
        target_end,
        "Synthetic source title</a> - Source A</li></ul>" + notice + "</article>",
        1,
    )
    assert rendered == expected
    assert rendered.count(f'href="#{STORY_ID}"') == 1
    assert rendered.count(f'<article id="{STORY_ID}">') == 1


@pytest.mark.parametrize("missing", ["today", "body"])
def test_story_scoped_html_requires_both_claim_occurrences(missing: str) -> None:
    source = _synthetic_edition_html()
    token = f"<p>{PRIOR}</p>"
    if missing == "today":
        source = source.replace(token, "<p>Missing Today claim.</p>", 1)
    else:
        position = source.rfind(token)
        source = source[:position] + "<p>Missing body claim.</p>" + source[position + len(token):]
    with pytest.raises(CorrectionValidationError, match="prior claim"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_duplicate_today_node() -> None:
    source = _synthetic_edition_html().replace(
        "<h2>At A Glance</h2>", f"<p>{PRIOR}</p>\n<h2>At A Glance</h2>", 1
    )
    with pytest.raises(CorrectionValidationError, match="unrelated or ambiguous"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_duplicate_story_body_node() -> None:
    source = _synthetic_edition_html().replace(
        "<p><strong>Sources:</strong></p>",
        f"<p>{PRIOR}</p><p><strong>Sources:</strong></p>",
        1,
    )
    with pytest.raises(CorrectionValidationError, match="target article body"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_today_link_to_wrong_story() -> None:
    source = _synthetic_edition_html().replace(
        f"<p>{PRIOR}</p>", f'<p><a href="#wrong-story">{PRIOR}</a></p>', 1
    )
    with pytest.raises(CorrectionValidationError, match="links to another story"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_matching_claim_in_unrelated_story() -> None:
    source = _synthetic_edition_html().replace(
        "<p>Unrelated synthetic summary 1.</p>",
        f"<p>Unrelated synthetic summary 1.</p><p>{PRIOR}</p>",
        1,
    )
    with pytest.raises(CorrectionValidationError, match="unrelated or ambiguous"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_third_unowned_occurrence() -> None:
    source = _synthetic_edition_html().replace(
        "</main>", f"<aside><p>{PRIOR}</p></aside></main>", 1
    )
    with pytest.raises(CorrectionValidationError, match="unrelated or ambiguous"):
        _render_synthetic_html(source)


def test_story_scoped_html_tolerates_reordered_unrelated_stories() -> None:
    source = _synthetic_edition_html()
    first_start = source.index("<article><h3>Unrelated synthetic story 1")
    second_start = source.index("<article><h3>Unrelated synthetic story 2")
    end = source.index("</main>", second_start)
    first_article = source[first_start:second_start]
    second_article = source[second_start:end]
    reordered = source[:first_start] + second_article + first_article + source[end:]
    rendered = _render_synthetic_html(reordered)
    assert rendered.index("Unrelated synthetic story 2") < rendered.index(
        "Unrelated synthetic story 1"
    )


def test_story_scoped_html_ignores_similar_title_with_different_source_identity() -> None:
    source = _synthetic_edition_html().replace(
        "</main>",
        (
            f"<article><h3>{TITLE}</h3>"
            "<p><em>Other · conflict · Gaza · August 29, 2026</em></p>"
            "<p>Different claim.</p>"
            '<a href="https://different.example/article">Source</a></article></main>'
        ),
        1,
    )
    rendered = _render_synthetic_html(source)
    assert rendered.count(f"<h3>{TITLE}</h3>") == 2
    assert rendered.count(f'id="{STORY_ID}"') == 1


def test_story_scoped_html_rejects_changed_article_anchor() -> None:
    source = _synthetic_edition_html().replace(
        f"<article><h3>{TITLE}</h3>",
        f'<article id="different-story"><h3>{TITLE}</h3>',
        1,
    )
    with pytest.raises(CorrectionValidationError, match="anchor changed"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_changed_article_source_identity() -> None:
    source = _synthetic_edition_html().replace(
        "https://source-a.example/wrapper",
        "https://different.example/article",
        1,
    )
    with pytest.raises(CorrectionValidationError, match="manifest source URL"):
        _render_synthetic_html(source)


def test_story_scoped_html_rejects_changed_owning_date() -> None:
    source = _synthetic_edition_html().replace("August 29, 2026", "August 28, 2026", 1)
    with pytest.raises(CorrectionValidationError, match="owning date changed"):
        _render_synthetic_html(source)


def test_story_scoped_html_does_not_expose_review_machine_wording() -> None:
    corrected = 'Corrected & attributed <two> "deaths".'
    rendered = _render_synthetic_html(corrected_claim=corrected)
    assert "Corrected & attributed <two>" not in rendered
    assert "Corrected &amp; attributed &lt;two&gt;" not in rendered
    assert _reader_correction_copy(_synthetic_correction()).story_update in rendered


def test_story_scoped_html_rejects_malformed_markup() -> None:
    source = _synthetic_edition_html().replace("</article>", "", 1)
    with pytest.raises(CorrectionValidationError, match="mismatched|unclosed|malformed"):
        _render_synthetic_html(source)


@pytest.mark.parametrize(
    ("newline", "final_newline", "bom"),
    [
        ("\n", False, False),
        ("\n", True, False),
        ("\r\n", False, False),
        ("\r\n", True, True),
    ],
)
def test_text_artifact_preserves_utf8_byte_conventions(
    tmp_path: Path,
    newline: str,
    final_newline: bool,
    bom: bool,
) -> None:
    path = tmp_path / "artifact.html"
    _write_exact_text(
        path,
        "<p>Gaza \u2014 non-ASCII</p>\n<p>unchanged</p>",
        newline=newline,
        final_newline=final_newline,
        bom=bom,
    )
    artifact = _read_text_artifact(path, "synthetic artifact")
    output = artifact.encode(
        artifact.text.replace("Gaza", "Corrected Gaza", 1), "synthetic artifact"
    )
    assert output.startswith(b"\xef\xbb\xbf") is bom
    body = output[3:] if bom else output
    assert (b"\r\n" in body) is (newline == "\r\n")
    assert b"\n" not in body.replace(b"\r\n", b"") if newline == "\r\n" else b"\r" not in body
    assert body.endswith(newline.encode("ascii")) is final_newline
    assert "non-ASCII" in body.decode("utf-8")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"one\r\ntwo\n", "mixed newlines"),
        (b"one\rtwo", "bare-CR"),
        (b"\xff\xfe", "non-UTF-8"),
    ],
)
def test_text_artifact_rejects_ambiguous_or_unsupported_input(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(payload)
    with pytest.raises(CorrectionValidationError, match=message):
        _read_text_artifact(path, "synthetic artifact")


def _synthetic_audio_ownership() -> _EditionAudioOwnership:
    return _EditionAudioOwnership(
        edition_date=DATE,
        audio_path=f"/gaza/audio/{DATE}.mp3",
        transcript_path=f"/gaza/audio/{DATE}-transcript.html",
        edition_path=f"/gaza/editions/{DATE}/",
        audio_size_bytes=len(b"ID3-prior-synthetic-audio"),
    )


def _render_production_shaped_json(tmp_path: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    correction = _synthetic_correction()
    record = _public_correction_record(correction)
    reader = _reader_correction_copy(correction)
    link = (
        "https://dispatches.thebluefernco.com/gaza/corrections/"
        f"{correction['correction_id']}/"
    )
    names = {
        "curation_manifest": "curation_manifest.json",
        "dedupe_report": "dedupe_report.json",
        "edition_manifest": "edition_manifest.json",
        "original_audio_metadata": "audio_metadata.json",
        "flash_briefing": "flash_briefing.json",
    }
    documents = {
        role: _fixture_document(tmp_path, name)[1]
        for role, name in names.items()
    }
    assert all(
        document.render(()) == document.original_bytes
        for document in documents.values()
    )
    originals = {role: document.original_bytes for role, document in documents.items()}
    outputs = {
        "curation_manifest": _render_curation_manifest_json(
            documents["curation_manifest"], correction, record, reader
        ),
        "dedupe_report": _render_dedupe_report_json(
            documents["dedupe_report"], correction, record
        ),
        "edition_manifest": _render_edition_manifest_json(
            documents["edition_manifest"], correction, record
        ),
        "original_audio_metadata": _render_original_audio_metadata_json(
            documents["original_audio_metadata"], correction
        ),
        "flash_briefing": _render_flash_briefing_json(
            documents["flash_briefing"],
            correction,
            {"script_text": reader.audio_script},
            reader,
            link,
            _synthetic_audio_ownership(),
        ),
    }
    return originals, outputs


def _render_flash_redirect(tmp_path: Path, redirect: str, *, uid: str | None = None) -> bytes:
    payload = json.loads((JSON_PRESERVATION_FIXTURES / "flash_briefing.json").read_text(encoding="utf-8"))
    if uid is None:
        uid = f"gaza-{DATE}"
    payload[0]["uid"] = uid
    payload[0]["redirectionUrl"] = redirect
    path = tmp_path / "flash-briefing.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    correction = _synthetic_correction()
    reader = _reader_correction_copy(correction)
    return _render_flash_briefing_json(
        _load_json_document(path, "flash briefing"),
        correction,
        {"script_text": reader.audio_script},
        reader,
        "https://dispatches.thebluefernco.com/gaza/corrections/synthetic/",
        _synthetic_audio_ownership(),
    )


@pytest.mark.parametrize(
    "redirect",
    [
        f"/gaza/audio/{DATE}.mp3",
        f"https://dispatches.thebluefernco.com/gaza/audio/{DATE}.mp3",
    ],
)
def test_flash_briefing_accepts_canonical_relative_or_sanctioned_absolute_audio_url(
    tmp_path: Path, redirect: str
) -> None:
    output = json.loads(_render_flash_redirect(tmp_path, redirect))
    assert output[0]["uid"] == _synthetic_correction()["correction_id"]


@pytest.mark.parametrize(
    "redirect",
    [
        "/gaza/audio/2026-08-28.mp3",
        f"/care-line/audio/{DATE}.mp3",
        f"/Gaza/audio/{DATE}.mp3",
        f"/gaza/audio/{DATE}.wav",
        f"/gaza/audio/{DATE}.mp30",
        f"/gaza/audio/{DATE}-extra.mp3",
        f"/gaza/audio/{DATE}.mp3?download=1",
        f"/gaza/audio/{DATE}.mp3#fragment",
        f"/gaza/audio/%32%30%32%36-08-29.mp3",
        f"/gaza/audio/../audio/{DATE}.mp3",
        f"/gaza/audio/%2e%2e/audio/{DATE}.mp3",
        f"https://example.test/gaza/audio/{DATE}.mp3",
        f"https://dispatches.thebluefernco.com.evil.test/gaza/audio/{DATE}.mp3",
        f"https://user@dispatches.thebluefernco.com/gaza/audio/{DATE}.mp3",
        f"https://dispatches.thebluefernco.com:443/gaza/audio/{DATE}.mp3",
        f"https://dispatches.thebluefernco.com/gaza/editions/{DATE}/",
    ],
)
def test_flash_briefing_rejects_noncanonical_or_wrong_audio_url(
    tmp_path: Path, redirect: str
) -> None:
    with pytest.raises(CorrectionValidationError):
        _render_flash_redirect(tmp_path, redirect)


def test_flash_briefing_requires_exact_uid_and_redirection_field(tmp_path: Path) -> None:
    with pytest.raises(CorrectionValidationError, match="another owning edition"):
        _render_flash_redirect(tmp_path, f"/gaza/audio/{DATE}.mp3", uid=DATE)

    correction = _synthetic_correction()
    reader = _reader_correction_copy(correction)
    for missing in ("uid", "redirectionUrl"):
        payload = json.loads(
            (JSON_PRESERVATION_FIXTURES / "flash_briefing.json").read_text(
                encoding="utf-8"
            )
        )
        del payload[0][missing]
        path = tmp_path / f"missing-{missing}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(CorrectionValidationError, match="missing required field"):
            _render_flash_briefing_json(
                _load_json_document(path, "flash briefing"),
                correction,
                {"script_text": reader.audio_script},
                reader,
                "https://dispatches.thebluefernco.com/gaza/corrections/synthetic/",
                _synthetic_audio_ownership(),
            )


@pytest.mark.parametrize(
    ("newline", "final_newline", "bom"),
    [
        ("\n", True, False),
        ("\n", False, True),
        ("\r\n", True, True),
        ("\r\n", False, False),
    ],
)
def test_flash_briefing_patch_preserves_concrete_syntax_and_is_byte_reversible(
    tmp_path: Path, newline: str, final_newline: bool, bom: bool
) -> None:
    path, document = _fixture_document(
        tmp_path,
        "flash_briefing.json",
        newline=newline,
        final_newline=final_newline,
        bom=bom,
    )
    original = path.read_bytes()
    correction = _synthetic_correction()
    reader = _reader_correction_copy(correction)
    output = _render_flash_briefing_json(
        document,
        correction,
        {"script_text": reader.audio_script},
        reader,
        "https://dispatches.thebluefernco.com/gaza/corrections/synthetic/",
        _synthetic_audio_ownership(),
    )
    assert document.render(()) == original
    assert output.startswith(b"\xef\xbb\xbf") is bom
    body = output[3:] if bom else output
    assert (b"\r\n" in body) is (newline == "\r\n")
    assert body.endswith(newline.encode()) is final_newline
    assert json.loads(body)[0]["uid"] == correction["correction_id"]


def test_edition_audio_ownership_matches_metadata_mp3_transcript_and_both_feeds(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    ownership = _validate_edition_audio_ownership(pages, _synthetic_correction())
    assert ownership == _synthetic_audio_ownership()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dispatch_slug", "care-line", "dispatch identity"),
        ("edition_date", "2026-08-28", "edition identity"),
        ("audio_file", "2026-08-29-copy.mp3", "audio filename"),
        ("audio_mime_type", "audio/wav", "MIME type"),
        ("audio_file_size_bytes", 999, "file size"),
        ("audio_url", "/gaza/audio/2026-08-28.mp3", "metadata URL"),
        (
            "transcript_url",
            "https://dispatches.thebluefernco.com/gaza/audio/2026-08-28-transcript.html",
            "metadata transcript URL",
        ),
        (
            "edition_url",
            "https://dispatches.thebluefernco.com/gaza/editions/2026-08-28/",
            "metadata edition URL",
        ),
    ],
)
def test_edition_audio_ownership_rejects_mismatched_metadata(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    path = pages / "gaza" / "audio" / f"{DATE}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _write_json(path, payload)
    with pytest.raises(CorrectionValidationError, match=message):
        _validate_edition_audio_ownership(pages, _synthetic_correction())


def test_edition_audio_ownership_rejects_missing_metadata(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    (pages / "gaza" / "audio" / f"{DATE}.json").unlink()
    with pytest.raises(CorrectionValidationError, match="audio metadata"):
        _validate_edition_audio_ownership(pages, _synthetic_correction())


def test_edition_audio_ownership_rejects_mismatched_transcript_audio(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    path = pages / "gaza" / "audio" / f"{DATE}-transcript.html"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"/gaza/audio/{DATE}.mp3", "/gaza/audio/2026-08-28.mp3"
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorrectionValidationError, match="transcript audio URL"):
        _validate_edition_audio_ownership(pages, _synthetic_correction())


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            f"https://dispatches.thebluefernco.com/gaza/audio/{DATE}.mp3",
            "https://dispatches.thebluefernco.com/gaza/audio/2026-08-28.mp3",
            "enclosure URL",
        ),
        (
            f"https://dispatches.thebluefernco.com/gaza/audio/{DATE}-transcript.html",
            "https://dispatches.thebluefernco.com/gaza/audio/2026-08-28-transcript.html",
            "transcript link",
        ),
    ],
)
def test_edition_audio_ownership_rejects_mismatched_feed_identity(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    path = pages / "gaza" / "podcast.xml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    with pytest.raises(CorrectionValidationError, match=message):
        _validate_edition_audio_ownership(pages, _synthetic_correction())


def test_edition_audio_ownership_rejects_duplicate_feed_item(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    _make_pages(pages)
    path = pages / "gaza" / "podcast.xml"
    body = path.read_text(encoding="utf-8")
    item = body[body.index("<item>") : body.index("</item>") + len("</item>")]
    path.write_text(body.replace("</channel>", item + "</channel>"), encoding="utf-8")
    with pytest.raises(CorrectionValidationError, match="missing or ambiguous"):
        _validate_edition_audio_ownership(pages, _synthetic_correction())


def test_production_shaped_existing_json_preserves_lexemes_and_is_byte_reversible(
    tmp_path: Path,
) -> None:
    originals, outputs = _render_production_shaped_json(tmp_path)
    correction = _synthetic_correction()

    curation_before = json.loads(originals["curation_manifest"].decode("utf-8"))
    curation_after = json.loads(outputs["curation_manifest"].decode("utf-8"))
    prior_story = next(row for row in curation_before if row["story_id"] == STORY_ID)
    corrected_story = next(row for row in curation_after if row["story_id"] == STORY_ID)
    assert list(corrected_story)[: len(prior_story)] == list(prior_story)
    assert list(corrected_story)[-3:] == [
        "event_fingerprint",
        "injury_disagreement",
        "correction_history",
    ]
    assert corrected_story["summary"] == _reader_correction_copy(correction).story_update
    assert corrected_story["casualty_counts"] == {"new_deaths": 2}
    assert corrected_story["correction_history"][0]["story_id"] == STORY_ID
    curation_text = outputs["curation_manifest"].decode("utf-8")
    for lexical in (
        "1.2300e+02",
        r"slash:\/",
        r'quote:\"',
        "control:\\n",
        "unicode:\\u263a",
    ):
        assert lexical in curation_text

    for role in ("dedupe_report", "edition_manifest", "original_audio_metadata"):
        before = json.loads(originals[role].decode("utf-8"))
        after = json.loads(outputs[role].decode("utf-8"))
        assert list(after)[: len(before)] == list(before)
    assert list(json.loads(outputs["dedupe_report"])["corrections"][0]) == list(
        _public_correction_record(correction)
    )
    assert json.loads(outputs["edition_manifest"])["corrections"][0][
        "aggregates_recomputed_from_corrected_story_versions"
    ] is True
    audio = json.loads(outputs["original_audio_metadata"])
    assert audio["audio_status"] == "superseded_by_formal_correction"
    assert list(audio)[-2:] == [
        "superseded_by_correction_id",
        "replacement_audio_url",
    ]
    assert "1.024e+03" in outputs["original_audio_metadata"].decode("utf-8")
    assert "123.4500" in outputs["original_audio_metadata"].decode("utf-8")

    flash = json.loads(outputs["flash_briefing"])
    assert list(flash[0]) == ["uid", "updateDate", "titleText", "mainText", "redirectionUrl"]
    assert flash[0]["uid"] == correction["correction_id"]

    # Re-rendering the same owned spans is deterministic without rewriting the
    # input fixtures or depending on dictionary sorting.
    assert _render_curation_manifest_json(
        _load_json_document(tmp_path / "curation_manifest.json", "curation manifest"),
        correction,
        _public_correction_record(correction),
        _reader_correction_copy(correction),
    ) == outputs["curation_manifest"]

    # Every renderer call above performs an internal inverse-span reconstruction
    # and raises unless the reconstructed bytes equal this original input exactly.
    for role, original in originals.items():
        assert hashlib.sha256(original).digest() != hashlib.sha256(outputs[role]).digest()


@pytest.mark.parametrize(
    ("newline", "final_newline", "bom"),
    [
        ("\n", True, False),
        ("\n", False, True),
        ("\r\n", True, True),
        ("\r\n", False, False),
    ],
)
def test_existing_json_patch_preserves_bom_newlines_and_final_newline(
    tmp_path: Path, newline: str, final_newline: bool, bom: bool
) -> None:
    path, document = _fixture_document(
        tmp_path,
        "curation_manifest.json",
        newline=newline,
        final_newline=final_newline,
        bom=bom,
    )
    original = path.read_bytes()
    assert document.render(()) == original
    correction = _synthetic_correction()
    output = _render_curation_manifest_json(
        document,
        correction,
        _public_correction_record(correction),
        _reader_correction_copy(correction),
    )
    assert output.startswith(b"\xef\xbb\xbf") is bom
    body = output[3:] if bom else output
    if newline == "\r\n":
        assert b"\n" not in body.replace(b"\r\n", b"")
    else:
        assert b"\r" not in body
    assert body.endswith(newline.encode("ascii")) is final_newline
    assert b"1.2300e+02" in body
    assert b"6.250E-1" not in body


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'{"value": 1,}', "malformed JSON"),
        (b'{"value": 1}\r\n{"other": 2}\n', "mixed newlines"),
        (b'{"value": 1}\r{"other": 2}', "bare-CR"),
        (b"\xff\xfe{\x00}\x00", "non-UTF-8"),
        (b'{"value": 1, "value": 2}', "duplicate object key"),
    ],
)
def test_json_preservation_rejects_unrepresentable_input(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    with pytest.raises(CorrectionValidationError, match=message):
        _load_json_document(path, "synthetic JSON")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda text: text.replace(
                '"gaza-story-2026-08-29-006"', f'"{STORY_ID}"', 1
            ),
            "exactly once",
        ),
        (
            lambda text: text.replace(
                '"event_date": "2026-08-29T12:00:00+00:00"',
                '"event_date": "2026-08-28T12:00:00+00:00"',
                1,
            ),
            "another edition date",
        ),
        (
            lambda text: text.replace(
                f'"summary": "{PRIOR}"', '"summary": 42', 1
            ),
            "summary has type drift",
        ),
        (
            lambda text: text.replace('"new_deaths": 1', '"new_deaths": "1"', 1),
            "prior casualty value drifted",
        ),
    ],
)
def test_curation_json_targeting_fails_closed(
    tmp_path: Path, mutation, message: str
) -> None:
    source = (JSON_PRESERVATION_FIXTURES / "curation_manifest.json").read_text(
        encoding="utf-8"
    )
    path = tmp_path / "curation.json"
    path.write_text(mutation(source), encoding="utf-8")
    document = _load_json_document(path, "curation manifest")
    correction = _synthetic_correction()
    with pytest.raises(CorrectionValidationError, match=message):
        _render_curation_manifest_json(
            document,
            correction,
            _public_correction_record(correction),
            _reader_correction_copy(correction),
        )


def test_curation_json_wrong_story_id_fails_closed(tmp_path: Path) -> None:
    _, document = _fixture_document(tmp_path, "curation_manifest.json")
    correction = _synthetic_correction()
    correction["story_id"] = "gaza-story-2026-08-29-999"
    with pytest.raises(CorrectionValidationError, match="exactly once"):
        _render_curation_manifest_json(
            document,
            correction,
            _public_correction_record(correction),
            _reader_correction_copy(correction),
        )


@pytest.mark.parametrize(
    ("fixture", "mutation", "renderer", "message"),
    [
        (
            "dedupe_report.json",
            lambda text: text.replace('"edition_date": "2026-08-29"', '"edition_date": "2026-08-28"'),
            "dedupe",
            "another owning edition",
        ),
        (
            "dedupe_report.json",
            lambda text: json.dumps(
                {**json.loads(text), "included_stories": {}}, indent=2
            )
            + "\n",
            "dedupe",
            "drifted from JSON array",
        ),
        (
            "edition_manifest.json",
            lambda text: text.replace('"edition_date": "2026-08-29"', '"edition_date": 20260829'),
            "edition",
            "another owning edition",
        ),
        (
            "audio_metadata.json",
            lambda text: text.replace('"audio_status": "audio_file_ready"', '"audio_status": null'),
            "audio",
            "type drift",
        ),
        (
            "flash_briefing.json",
            lambda text: text.replace('"uid": "gaza-2026-08-29"', '"uid": "gaza-2026-08-28"'),
            "flash",
            "another owning edition",
        ),
        (
            "flash_briefing.json",
            lambda text: text[:-2] + ",\n  {}\n]\n",
            "flash",
            "missing or ambiguous",
        ),
    ],
)
def test_role_specific_json_ownership_and_type_drift_fail_closed(
    tmp_path: Path, fixture: str, mutation, renderer: str, message: str
) -> None:
    source = (JSON_PRESERVATION_FIXTURES / fixture).read_text(encoding="utf-8")
    path = tmp_path / fixture
    path.write_text(mutation(source), encoding="utf-8")
    document = _load_json_document(path, fixture)
    correction = _synthetic_correction()
    record = _public_correction_record(correction)
    reader = _reader_correction_copy(correction)
    calls = {
        "dedupe": lambda: _render_dedupe_report_json(document, correction, record),
        "edition": lambda: _render_edition_manifest_json(document, correction, record),
        "audio": lambda: _render_original_audio_metadata_json(document, correction),
        "flash": lambda: _render_flash_briefing_json(
            document,
            correction,
            {"script_text": reader.audio_script},
            reader,
            "https://dispatches.thebluefernco.com/gaza/corrections/synthetic/",
            _synthetic_audio_ownership(),
        ),
    }
    with pytest.raises(CorrectionValidationError, match=message):
        calls[renderer]()


@pytest.mark.parametrize(
    ("newline", "final_newline", "bom"),
    [("\n", False, False), ("\r\n", True, True)],
)
def test_proposal_preserves_existing_text_bytes_and_edition_is_exactly_reversible(
    tmp_path: Path,
    newline: str,
    final_newline: bool,
    bom: bool,
) -> None:
    case = _build_case(
        tmp_path,
        with_approval=False,
        text_format=(newline, final_newline, bom),
    )
    correction = case["correction"]
    reader = _reader_correction_copy(correction)
    preview_root = case["inputs"] / "preview"

    original_path = case["pages"] / REPLACEMENT_PATHS["edition_html"].format(
        date=DATE, correction_id=correction["correction_id"]
    )
    preview_path = preview_root / REPLACEMENT_PATHS["edition_html"].format(
        date=DATE, correction_id=correction["correction_id"]
    )
    original = original_path.read_bytes()
    preview = preview_path.read_bytes()
    prefix = b"\xef\xbb\xbf" if bom else b""
    assert preview.startswith(prefix)
    preview_text = preview[len(prefix):].decode("utf-8")
    reversed_text = preview_text.replace(
        f'<p><a href="#{STORY_ID}">{html.escape(reader.today_update, quote=False)}</a></p>',
        f"<p>{PRIOR}</p>",
        1,
    ).replace(
        f"<p>{html.escape(reader.story_update, quote=False)}</p>",
        f"<p>{PRIOR}</p>",
        1,
    ).replace(
        f'<article id="{STORY_ID}">',
        "<article>",
        1,
    ).replace(
        _correction_notice_html(correction),
        "",
        1,
    )
    assert prefix + reversed_text.encode("utf-8") == original

    for role in {
        "rss", "original_transcript", "podcast", "audio_podcast",
        "audio_index", "gaza_index", "gaza_archive", "root_index",
    }:
        relative = REPLACEMENT_PATHS[role].format(
            date=DATE, correction_id=correction["correction_id"]
        )
        before = (case["pages"] / relative).read_bytes()
        after = (preview_root / relative).read_bytes()
        common_prefix = os.path.commonprefix((before, after))
        prefix_length = len(common_prefix)
        common_suffix_length = 0
        while (
            common_suffix_length < len(before) - prefix_length
            and before[-1 - common_suffix_length] == after[-1 - common_suffix_length]
        ):
            common_suffix_length += 1
        assert before == (
            after[:prefix_length]
            + (after[len(after) - common_suffix_length:] if common_suffix_length else b"")
        )


def test_public_copy_is_reader_facing_while_machine_identity_remains_structured(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path, with_approval=False)
    correction = case["correction"]
    reader = _reader_correction_copy(correction)
    inputs = case["inputs"]
    preview_root = inputs / "preview"
    audio_request = json.loads((inputs / "audio_request.json").read_text(encoding="utf-8"))
    script = audio_request["script_text"]
    assert script == reader.audio_script
    assert "August 29" in script
    assert correction["correction_id"] not in script
    assert STORY_ID not in script
    assert DATE not in script and CORRECTION_DATE not in script
    assert "affiliation" not in script.casefold()

    for role in ("rss", "podcast", "audio_podcast"):
        relative = REPLACEMENT_PATHS[role].format(
            date=DATE, correction_id=correction["correction_id"]
        )
        root = ElementTree.fromstring((preview_root / relative).read_bytes())
        item = next(
            node for node in root.findall(".//item")
            if node.findtext("guid") == correction["correction_id"]
        )
        description = item.findtext("description", default="")
        assert description == reader.feed_description
        assert " | " not in description
        assert correction["correction_id"] not in description
        assert STORY_ID not in description

    edition_relative = REPLACEMENT_PATHS["edition_html"].format(
        date=DATE, correction_id=correction["correction_id"]
    )
    edition = (preview_root / edition_relative).read_text(encoding="utf-8-sig")
    assert f'href="#{STORY_ID}">{reader.today_update}</a>' in edition
    assert reader.notice in edition
    assert "originally reported, citing Source A" in edition
    curation = json.loads(
        (preview_root / REPLACEMENT_PATHS["curation_manifest"].format(
            date=DATE, correction_id=correction["correction_id"]
        )).read_text(encoding="utf-8-sig")
    )
    story = next(row for row in curation if row["story_id"] == STORY_ID)
    assert story["casualty_counts"] == {"new_deaths": 2}
    assert "new_injuries" not in story["casualty_counts"]
    assert story["injury_disagreement"]["unresolved"] is True
    assert story["correction_history"][0]["correction_id"] == correction["correction_id"]


def test_cli_help_works_without_pythonpath_from_repo_root() -> None:
    root = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "scripts/gaza_historical_correction.py", "--help"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--mode" in result.stdout


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


def test_approval_commit_cannot_smuggle_other_source_changes(tmp_path: Path) -> None:
    case = _build_case(tmp_path, with_approval=False)
    create_package_approval(
        source_root=case["source"],
        pages_root=case["pages"],
        proposal_path=case["proposal_path"],
        input_root=case["inputs"],
        approval_request_path=case["inputs"] / "approval_request.json",
        output_path=case["source"] / case["approval_path"],
        approval_id="mixed-commit-approval",
        approver="Synthetic Reviewer",
        approved_at="2026-09-02T12:00:00+00:00",
    )
    (case["source"] / "unrelated.txt").write_text("unsanctioned", encoding="utf-8")
    _commit(case["source"], "Mixed approval and source change")
    with pytest.raises(CorrectionValidationError, match="only the approval artifact"):
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


def test_source_history_drift_is_rejected_before_planning(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    (case["source"] / "unrelated.txt").write_text("drift", encoding="utf-8")
    _commit(case["source"], "Source history drift")
    with pytest.raises(CorrectionValidationError, match="committed package approval"):
        _plan(case)


@pytest.mark.parametrize("target", ["source", "pages"])
def test_stale_validated_plan_cannot_stage_after_drift(
    tmp_path: Path, target: str
) -> None:
    case = _build_case(tmp_path)
    plan = _plan(case)
    if target == "source":
        (case["source"] / "unrelated.txt").write_text("drift", encoding="utf-8")
        _commit(case["source"], "Source history drift")
        message = "source history drifted"
    else:
        (case["pages"] / "unrelated.txt").write_text("drift", encoding="utf-8")
        _commit(case["pages"], "Pages history drift")
        message = "Pages history drifted"
    with pytest.raises(CorrectionValidationError, match=message):
        stage_correction_package(
            plan=plan,
            input_root=case["inputs"],
            rendered_audio_path=case["rendered_audio"],
            staging_root=tmp_path / "staging",
            source_root=case["source"],
            pages_root=case["pages"],
        )


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
    assert [story["story_id"] for story in curation] == [
        STORY_ID,
        "gaza-story-2026-08-29-001",
        "gaza-story-2026-08-29-002",
    ]
    assert curation[0]["correction_history"][0]["story_id"] == STORY_ID
    assert "correction_history" not in curation[1]
    assert "correction_history" not in curation[2]
    assert plan["pages_mutation"] is False
    assert plan["publication_authorized"] is False
    assert not any("scheduler" in row["public_path"] for row in plan["representations"])
