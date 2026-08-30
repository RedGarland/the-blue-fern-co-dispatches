from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.gaza_sources import story_claim_fingerprint
from bluefern_dispatches.historical_agent_archive import (
    GAZA_PAGES_REPOSITORY,
    _canonical_fingerprint,
    _lineage_record_fingerprint,
    build_gaza_published_story_lineage,
    gaza_candidate_matches_published_lineage,
    gaza_match_targets,
    gaza_published_lineage_path,
    gaza_stable_event_fingerprint,
    gaza_stable_event_identity_inputs,
    record_gaza_published_story_lineage,
    validate_gaza_published_story_lineage,
)
from bluefern_dispatches.story_dedupe import topic_fingerprint
from scripts.import_historical_agent_runs import (
    _gaza_candidate_fingerprint,
    _validate_gaza_decision_details,
)


STORY_ID = "gaza-story-2026-08-29-005"
EDITION_DATE = "2026-08-29"
TITLE = "WAFA reports casualties in Tal al-Hawa motorbike strike"
CLAIM = "WAFA reported 1 killed and 2 injured in a strike on a motorbike in Tal al-Hawa, Gaza City."
SOURCE_ID = "gaza-2026-08-29-wafa-gaza-motorbike-query-synthetic"
SOURCE_URL = "https://english.wafa.ps/Pages/Details/174181"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def pages_story() -> dict:
    source_record = {
        "source_record_id": SOURCE_ID,
        "title": "Palestinian killed, two injured in Israeli drone strike in Gaza City - WAFA Agency",
        "publisher": "WAFA",
        "url": "https://news.google.com/rss/articles/synthetic?oc=5",
        "summary_or_snippet": (
            "A Palestinian was killed and two others were injured after an Israeli "
            "drone struck an electric motorcycle in the Tal al-Hawa neighborhood."
        ),
        "event_date": "2026-08-29T14:26:12+00:00",
        "location": "Tal al-Hawa, Gaza City",
        "development_type": "casualty_event",
        "casualty_counts": {"new_deaths": 1, "new_injuries": 2},
    }
    return {
        "story_id": STORY_ID,
        "title": TITLE,
        "summary": CLAIM,
        "category": "conflict",
        "event_date": "2026-08-29T14:26:12+00:00",
        "location": "Tal al-Hawa, Gaza City",
        "development_type": "casualty_event",
        "casualty_counts": {"new_deaths": 1, "new_injuries": 2},
        "attribution": "WAFA",
        "publisher_names": ["WAFA"],
        "source_record_ids": [SOURCE_ID],
        "source_urls": [source_record["url"]],
        "source_records": [source_record],
        "public_rendered": True,
        "included_in_public_summary": True,
    }


def init_pages_repo(root: Path) -> tuple[Path, str, str]:
    pages = root / "pages"
    pages.mkdir()
    subprocess.run(["git", "init", "-b", "gh-pages", str(pages)], check=True, capture_output=True)
    git(pages, "config", "user.email", "tests@example.com")
    git(pages, "config", "user.name", "Tests")
    git(pages, "remote", "add", "origin", GAZA_PAGES_REPOSITORY)
    (pages / "README.md").write_text("synthetic Pages history\n", encoding="utf-8")
    git(pages, "add", "README.md")
    git(pages, "commit", "-m", "Initial Pages state")
    prior_commit = git(pages, "rev-parse", "HEAD")

    story = pages_story()
    base = pages / "gaza" / "editions" / EDITION_DATE
    write_json(base / "curation_manifest.json", [story])
    source = {
        "source_record_id": SOURCE_ID,
        "title": story["source_records"][0]["title"],
        "publisher": "WAFA",
        "url": story["source_records"][0]["url"],
        "canonical_url": SOURCE_URL,
        "published_at": "2026-08-29T14:26:12+00:00",
        "dispatch_slug": "gaza",
        "category_hint": "conflict",
        "used_in_story_ids": [STORY_ID],
    }
    source["claim_fingerprint"] = story_claim_fingerprint(source)
    write_json(base / "sources_manifest.json", [source])
    write_json(
        base / "dedupe_report.json",
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
    (base / "index.html").write_text(
        f"<html><body><h3>{TITLE}</h3><p>{CLAIM}</p></body></html>\n",
        encoding="utf-8",
    )
    git(pages, "add", "gaza")
    git(pages, "commit", "-m", "Publish exact Gaza story")
    return pages, prior_commit, git(pages, "rev-parse", "HEAD")


def build_record(pages: Path, commit: str, **overrides: object) -> dict:
    values = {
        "pages_commit": commit,
        "story_id": STORY_ID,
        "edition_date": EDITION_DATE,
        "expected_title": TITLE,
        "expected_prior_claim": CLAIM,
        "backfill_reason": "Synthetic immutable prior-story lineage.",
        "created_at": "2026-08-30T00:00:00+00:00",
    }
    values.update(overrides)
    return build_gaza_published_story_lineage(pages, **values)


def test_valid_provenance_dry_run_apply_replay_and_resolution(tmp_path: Path):
    pages, _, commit = init_pages_repo(tmp_path)
    repo = tmp_path / "source"
    result = record_gaza_published_story_lineage(
        repo,
        pages,
        pages_commit=commit,
        story_id=STORY_ID,
        edition_date=EDITION_DATE,
        expected_title=TITLE,
        expected_prior_claim=CLAIM,
        backfill_reason="Synthetic immutable prior-story lineage.",
        dry_run=True,
    )
    path = gaza_published_lineage_path(repo, STORY_ID)
    assert result["status"] == "dry_run_validated"
    assert result["persistent_mutation"] is False
    assert not path.exists()

    applied = record_gaza_published_story_lineage(
        repo,
        pages,
        pages_commit=commit,
        story_id=STORY_ID,
        edition_date=EDITION_DATE,
        expected_title=TITLE,
        expected_prior_claim=CLAIM,
        backfill_reason="Synthetic immutable prior-story lineage.",
        dry_run=False,
    )
    before = path.read_bytes()
    replay = record_gaza_published_story_lineage(
        repo,
        pages,
        pages_commit=commit,
        story_id=STORY_ID,
        edition_date=EDITION_DATE,
        expected_title=TITLE,
        expected_prior_claim=CLAIM,
        backfill_reason="Synthetic immutable prior-story lineage.",
        dry_run=False,
    )
    assert applied["status"] == "lineage_recorded"
    assert replay["status"] == "idempotent_noop"
    assert path.read_bytes() == before
    matches = gaza_match_targets(repo)["clusters_by_id"][STORY_ID]
    assert len(matches) == 1
    assert matches[0]["record_type"] == "private_published_story_lineage"
    assert matches[0]["lineage_record"]["prior_claim"]["text"] == CLAIM
    assert not (repo / "data/agent-history/gaza/reviews/decisions").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"story_id": "gaza-story-2026-08-29-999"}, "story ID exactly once"),
        ({"expected_title": "Changed title"}, "title differs"),
        ({"expected_prior_claim": "Changed prior claim"}, "prior claim differs"),
        ({"edition_date": "2026-08-28"}, "Git provenance check failed"),
    ],
)
def test_pages_provenance_mismatches_fail(tmp_path: Path, overrides: dict, message: str):
    pages, _, commit = init_pages_repo(tmp_path)
    with pytest.raises(ValueError, match=message):
        build_record(pages, commit, **overrides)


def test_wrong_pages_commit_fails(tmp_path: Path):
    pages, prior_commit, _ = init_pages_repo(tmp_path)
    with pytest.raises(ValueError, match="Git provenance check failed"):
        build_record(pages, prior_commit)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row["pages_provenance"]["artifacts"][0].update(path="wrong/path.json"), "artifact path"),
        (lambda row: row["pages_provenance"]["artifacts"][0].update(sha256="0" * 64), "provenance fingerprint"),
        (lambda row: row.update(story_title="Changed title"), "title differs"),
        (lambda row: row["prior_claim"].update(text="Changed claim"), "prior claim differs"),
        (lambda row: row.update(edition_date="2026-08-28"), "artifact path"),
        (lambda row: row["evidence"]["story"].update(story_id="gaza-story-2026-08-29-999"), "another story"),
    ],
)
def test_tampered_lineage_fails_closed(tmp_path: Path, mutation, message: str):
    pages, _, commit = init_pages_repo(tmp_path)
    record = build_record(pages, commit)
    mutation(record)
    record["record_fingerprint"] = _lineage_record_fingerprint(record)
    with pytest.raises(ValueError, match=message):
        validate_gaza_published_story_lineage(record)


def test_fingerprint_derivation_is_deterministic_and_not_caller_controlled(tmp_path: Path):
    pages, _, commit = init_pages_repo(tmp_path)
    left = build_record(pages, commit)
    right = build_record(pages, commit)
    assert left == right
    tampered = copy.deepcopy(left)
    tampered["stable_event_identity"]["fingerprint"] = "sha256:" + "0" * 64
    tampered["record_fingerprint"] = _lineage_record_fingerprint(tampered)
    with pytest.raises(ValueError, match="stable event fingerprint"):
        validate_gaza_published_story_lineage(tampered)
    tampered = copy.deepcopy(left)
    tampered["canonicalization_version"] = "unknown-v2"
    tampered["record_fingerprint"] = _lineage_record_fingerprint(tampered)
    with pytest.raises(ValueError, match="canonicalization version"):
        validate_gaza_published_story_lineage(tampered)


def test_stable_event_identity_is_distinct_from_versioned_claim_identity():
    prior = pages_story()
    corrected = copy.deepcopy(prior)
    corrected["summary"] = "Two Palestinians were killed in the Tal al-Hawa motorbike strike."
    corrected["casualty_counts"] = {"new_deaths": 2, "new_injuries": 2}
    assert gaza_stable_event_fingerprint(
        gaza_stable_event_identity_inputs(prior)
    ) == gaza_stable_event_fingerprint(gaza_stable_event_identity_inputs(corrected))
    assert topic_fingerprint(prior) != topic_fingerprint(corrected)


def test_duplicate_and_cross_domain_lineage_fail(tmp_path: Path):
    pages, _, commit = init_pages_repo(tmp_path)
    repo = tmp_path / "source"
    record = build_record(pages, commit)
    path = gaza_published_lineage_path(repo, STORY_ID)
    write_json(path, record)
    write_json(path.with_name("duplicate.json"), record)
    with pytest.raises(ValueError, match="duplicate Gaza published-story lineage"):
        gaza_match_targets(repo)
    path.with_name("duplicate.json").unlink()
    cross_domain = copy.deepcopy(record)
    cross_domain["domain"] = "care-line"
    cross_domain["record_fingerprint"] = _lineage_record_fingerprint(cross_domain)
    write_json(path, cross_domain)
    with pytest.raises(ValueError, match="cross-domain"):
        gaza_match_targets(repo)


def correction_finding() -> dict:
    return {
        "audit_candidate_id": "GZ-01",
        "event_date": EDITION_DATE,
        "title": "Tal al-Hawa strike toll updated from one to two killed",
        "summary": (
            "The Tal al-Hawa strike toll was updated from one Palestinian killed to two; "
            "the injury count remains unresolved."
        ),
        "exact_supporting_passage": "Israeli attacks across Gaza kill 2 Palestinians",
        "material_update_lineage": {
            "initial_report": "one Palestinian killed",
            "updated_report": "two Palestinians killed",
        },
    }


def correction_evidence() -> list[dict]:
    return [
        {
            "role": "principal",
            "url": "https://www.aa.com.tr/example",
            "supporting_passage": (
                "The updated report concerned the Tal al-Hawa electric motorcycle strike."
            ),
        },
        {
            "role": "corroborating",
            "url": "https://apnews.com/example",
            "supporting_passage": "AP also described the Tal al-Hawa motorcycle strike.",
        },
    ]


def test_same_event_compatibility_and_correction_gate(tmp_path: Path):
    pages, _, commit = init_pages_repo(tmp_path)
    repo = tmp_path / "source"
    record = build_record(pages, commit)
    write_json(gaza_published_lineage_path(repo, STORY_ID), record)
    finding = correction_finding()
    evidence = correction_evidence()
    assert gaza_candidate_matches_published_lineage(finding, record, evidence)
    review = {
        "audit_candidate_id": "GZ-01",
        "candidate_event_fingerprint": _gaza_candidate_fingerprint(finding),
        "duplicate_and_authoritative_match_check": {
            "candidate_remains_distinct": True,
            "existing_edition_match": None,
            "existing_source_match": None,
            "existing_story_cluster_match": None,
            "existing_historical_match": None,
        },
        "correction_lineage": {
            "prior_reference": {
                "type": "published_story",
                "id": STORY_ID,
                "edition_date": EDITION_DATE,
            },
            "prior_event_fingerprint": {
                "event_identity": record["stable_event_identity"]["fingerprint"],
                "fingerprint": record["prior_claim_identity"]["fingerprint"],
            },
            "corrected_event_fingerprint": {
                "event_identity": record["stable_event_identity"]["fingerprint"],
                "fingerprint": _gaza_candidate_fingerprint(finding),
            },
            "field_or_claim": "casualty_counts.new_deaths",
            "previous_value": 1,
            "corrected_value": 2,
            "evidence_reference_indexes": [0, 1],
            "corroboration_required": True,
            "materiality_explanation": "The death toll increased from one to two.",
            "remaining_uncertainty": {
                "persists": True,
                "description": "The sources differ on the injury count.",
            },
            "prior_public_artifact_overwritten": False,
        },
    }
    details = _validate_gaza_decision_details(
        review, "corrected", evidence, repo, finding
    )
    assert details["correction_lineage"]["previous_value"] == 1
    assert not (repo / "data/agent-history/gaza/reviews/decisions").exists()

    unrelated = correction_finding()
    unrelated["title"] = "Tal al-Hawa apartment strike toll updated"
    unrelated_evidence = copy.deepcopy(evidence)
    for item in unrelated_evidence:
        item["supporting_passage"] = "The report concerned a Tal al-Hawa apartment strike."
    assert not gaza_candidate_matches_published_lineage(
        unrelated, record, unrelated_evidence
    )


def test_missing_lineage_still_fails_correction_gate(tmp_path: Path):
    finding = correction_finding()
    evidence = correction_evidence()
    review = {
        "audit_candidate_id": "GZ-01",
        "duplicate_and_authoritative_match_check": {
            "candidate_remains_distinct": True,
            "existing_edition_match": None,
            "existing_source_match": None,
            "existing_story_cluster_match": None,
            "existing_historical_match": None,
        },
        "correction_lineage": {
            "prior_reference": {
                "type": "published_story",
                "id": STORY_ID,
                "edition_date": EDITION_DATE,
            }
        },
    }
    with pytest.raises(ValueError, match="does not resolve as a published story"):
        _validate_gaza_decision_details(review, "corrected", evidence, tmp_path, finding)
