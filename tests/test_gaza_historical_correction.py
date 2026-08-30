from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.gaza_historical_correction import (
    APPROVAL_SCHEMA,
    NEW_ROLES,
    PROPOSAL_SCHEMA,
    REPLACEMENT_PATHS,
    UNCHANGED_PATHS,
    CorrectionValidationError,
    correction_identity,
    fingerprint_payload,
    plan_correction,
    sha256_file,
    stage_correction_package,
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
    (edition / "index.html").write_text(f"<h1>{TITLE}</h1><p>{PRIOR}</p>", encoding="utf-8")
    (edition / "source_quality_report.md").write_text("# Synthetic source quality\n", encoding="utf-8")
    existing_text = {
        "gaza/rss.xml": "<rss><channel><item><guid>edition</guid></item></channel></rss>",
        f"gaza/audio/{DATE}-transcript.html": f"<p>{PRIOR}</p>",
        "gaza/podcast.xml": "<rss><channel><item><guid>episode</guid></item></channel></rss>",
        "gaza/audio/podcast.xml": "<rss><channel><item><guid>episode</guid></item></channel></rss>",
        "gaza/audio/index.html": f"<p>{TITLE}</p>",
        "gaza/index.html": f"<p>{TITLE}</p>",
        "gaza/archive.html": f"<p>{TITLE}</p>",
        "index.html": f"<p>{TITLE}</p>",
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


def _correction_record(correction: dict[str, object]) -> dict[str, object]:
    return {
        "correction_id": correction["correction_id"],
        "story_id": correction["story_id"],
        "owning_edition_date": correction["owning_edition_date"],
        "correction_date": correction["correction_date"],
        "stable_event_fingerprint": correction["stable_event_fingerprint"],
        "prior_claim": correction["prior_claim"],
        "corrected_claim": correction["corrected_claim"],
        "change_reason": correction["change_reason"],
        "source_attribution": correction["source_attribution"],
        "evidence_references": correction["evidence_references"],
        "casualty_change": correction["casualty_change"],
        "injury_disagreement": correction["injury_disagreement"],
    }


def _semantic_text(correction: dict[str, object], wrapper: str = "p") -> str:
    body = " | ".join(
        str(correction[field])
        for field in ("correction_id", "story_id", "correction_date", "prior_claim", "corrected_claim")
    )
    return f"<{wrapper}>{body}</{wrapper}>"


def _feed(correction: dict[str, object], podcast: bool) -> str:
    description = " | ".join(
        str(correction[field])
        for field in ("correction_id", "story_id", "correction_date", "prior_claim", "corrected_claim")
    )
    enclosure = (
        f'<enclosure url="https://example.test/gaza/audio/corrections/{correction["correction_id"]}.mp3" '
        'type="audio/mpeg" />'
        if podcast
        else ""
    )
    return (
        "<rss><channel><item>"
        f"<guid>{correction['correction_id']}</guid>"
        f"<link>https://example.test/gaza/corrections/{correction['correction_id']}/</link>"
        f"<description>{description}</description>{enclosure}"
        "</item><item><guid>original-edition-guid</guid></item></channel></rss>"
    )


def _make_inputs(input_root: Path, correction: dict[str, object]) -> dict[str, Path]:
    record = _correction_record(correction)
    paths: dict[str, Path] = {}
    for role in REPLACEMENT_PATHS:
        path = input_root / f"{role}.artifact"
        path.parent.mkdir(parents=True, exist_ok=True)
        if role == "curation_manifest":
            payload = [
                {
                    "story_id": STORY_ID,
                    "title": TITLE,
                    "summary": CORRECTED,
                    "event_fingerprint": correction["stable_event_fingerprint"],
                    "casualty_counts": {"new_deaths": 2},
                    "injury_disagreement": {"unresolved": True, "reports": DISPUTE},
                    "correction_history": [record],
                }
            ]
            _write_json(path, payload)
        elif role == "dedupe_report":
            _write_json(path, {"included_stories": [{"story_id": STORY_ID}], "corrections": [record]})
        elif role == "edition_manifest":
            edition_record = {**record, "aggregates_recomputed_from_corrected_story_versions": True}
            _write_json(path, {"edition_date": DATE, "story_count": 1, "corrections": [edition_record]})
        elif role == "correction_manifest":
            _write_json(path, record)
        elif role == "original_audio_metadata":
            _write_json(
                path,
                {
                    "audio_status": "superseded_by_formal_correction",
                    "superseded_by_correction_id": correction["correction_id"],
                    "original_script_text": PRIOR,
                    "replacement_audio_url": f"/gaza/audio/corrections/{correction['correction_id']}.mp3",
                },
            )
        elif role == "correction_audio_metadata":
            _write_json(
                path,
                {
                    "audio_status": "formal_correction",
                    "correction_id": correction["correction_id"],
                    "story_id": STORY_ID,
                    "owning_edition_date": DATE,
                    "correction_date": CORRECTION_DATE,
                    "script_text": _semantic_text(correction),
                    "injury_disagreement": {"unresolved": True, "reports": DISPUTE},
                },
            )
        elif role == "flash_briefing":
            _write_json(path, [{"uid": correction["correction_id"], "mainText": _semantic_text(correction)}])
        elif role == "rss":
            path.write_text(_feed(correction, False), encoding="utf-8")
        elif role in {"podcast", "audio_podcast"}:
            path.write_text(_feed(correction, True), encoding="utf-8")
        elif role == "correction_audio":
            path.write_bytes(b"ID3-synthetic-corrected-audio")
        else:
            path.write_text(_semantic_text(correction), encoding="utf-8")
        paths[role] = path
    return paths


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
    correction = {
        "correction_id": correction_identity(
            STORY_ID, event_fingerprint, prior_fingerprint, corrected_fingerprint
        ),
        "story_id": STORY_ID,
        "owning_edition_date": DATE,
        "correction_date": CORRECTION_DATE,
        "stable_event_fingerprint": event_fingerprint,
        "prior_claim_fingerprint": prior_fingerprint,
        "corrected_claim_fingerprint": corrected_fingerprint,
        "prior_claim": PRIOR,
        "corrected_claim": CORRECTED,
        "change_reason": "Two independent sources update the death count while disagreeing on injuries.",
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
        "normalized_artifact_sha256": sha256_file(normalized_path),
        "report_artifact_sha256": sha256_file(report_path),
        "attribution_assessment": {
            "safe_future_wording": CORRECTED,
            "attributed_to": "Source B and Source C",
            "disputed_values": DISPUTE,
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

    input_paths = _make_inputs(inputs, correction)
    representations = []
    for role in REPLACEMENT_PATHS:
        public_path = REPLACEMENT_PATHS[role].format(date=DATE, correction_id=correction["correction_id"])
        representations.append(
            {
                "role": role,
                "public_path": public_path,
                "input_path": input_paths[role].relative_to(inputs).as_posix(),
                "prior_sha256": None if role in NEW_ROLES else sha256_file(pages / public_path),
                "corrected_sha256": sha256_file(input_paths[role]),
            }
        )
    set_payload = [
        {"role": row["role"], "public_path": row["public_path"], "corrected_sha256": row["corrected_sha256"]}
        for row in sorted(representations, key=lambda item: item["role"])
    ]
    artifact_set_sha = "sha256:" + hashlib.sha256(
        json.dumps(set_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    unchanged = [
        {
            "role": role,
            "public_path": template.format(date=DATE),
            "sha256": sha256_file(pages / template.format(date=DATE)),
        }
        for role, template in UNCHANGED_PATHS.items()
    ]
    proposal = {
        "schema_version": PROPOSAL_SCHEMA,
        "operation": "formal_historical_correction",
        "domain": "gaza",
        "source_commit": source_commit,
        "correction": correction,
        "private_evidence": {
            "lineage_path": lineage_path.relative_to(source).as_posix(),
            "lineage_sha256": sha256_file(lineage_path),
            "review_path": review_path.relative_to(source).as_posix(),
            "review_sha256": sha256_file(review_path),
            "decision_audit_path": audit_path.relative_to(source).as_posix(),
            "decision_audit_sha256": sha256_file(audit_path),
            "raw_sha256": raw_identity,
            "normalized_artifact_sha256": sha256_file(normalized_path),
            "report_artifact_sha256": sha256_file(report_path),
        },
        "pages_state": {
            "branch": "gh-pages",
            "expected_head": pages_head,
            "representations": representations,
            "unchanged_dependencies": unchanged,
            "artifact_set_sha256": artifact_set_sha,
        },
        "proposal_sha256": "",
    }
    proposal["proposal_sha256"] = fingerprint_payload(proposal, "proposal_sha256")
    _write_json(proposal_path, proposal)
    approval_path = "approvals/synthetic-correction.json"
    if with_approval:
        approval = {
            "schema_version": APPROVAL_SCHEMA,
            "scope": "formal_historical_correction",
            "approval_id": "synthetic-independent-approval",
            "proposal_sha256": proposal["proposal_sha256"],
            "correction_id": correction["correction_id"],
            "source_commit": source_commit,
            "pages_head": pages_head,
            "artifact_set_sha256": artifact_set_sha,
            "approved_at": "2026-09-02T12:00:00+00:00",
            "approver": "Independent Synthetic Reviewer",
            "package_authorized": True,
            "audio_authorized": True,
            "publication_authorized": False,
            "approval_fingerprint": "",
        }
        approval["approval_fingerprint"] = fingerprint_payload(approval, "approval_fingerprint")
        _write_json(source / approval_path, approval)
        _commit(source, "Independent synthetic approval")
    return {
        "source": source,
        "pages": pages,
        "inputs": inputs,
        "proposal_path": proposal_path,
        "proposal": proposal,
        "correction": correction,
        "approval_path": approval_path,
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
    assert len(plan["representations"]) == len(REPLACEMENT_PATHS)
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
            staging_root=staging,
            source_root=case["source"],
            pages_root=case["pages"],
        )
    assert not (staging / plan["correction_id"]).exists()

    monkeypatch.setattr("bluefern_dispatches.gaza_historical_correction.shutil.copyfile", original_copy)
    first = stage_correction_package(
        plan=plan,
        input_root=case["inputs"],
        staging_root=staging,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    second = stage_correction_package(
        plan=plan,
        input_root=case["inputs"],
        staging_root=staging,
        source_root=case["source"],
        pages_root=case["pages"],
    )
    assert first["status"] == "staged_correction_package"
    assert second["status"] == "idempotent_noop"
    manifest = staging / plan["correction_id"] / "package_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["approval_id"] = "conflict"
    _write_json(manifest, payload)
    with pytest.raises(CorrectionValidationError, match="conflicting"):
        stage_correction_package(
            plan=plan,
            input_root=case["inputs"],
            staging_root=staging,
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
