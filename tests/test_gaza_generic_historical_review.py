import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts.import_historical_agent_runs import main


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_fingerprint(finding: dict) -> str:
    excluded = {
        "audit_candidate_id",
        "candidate_created",
        "deduplication_outcome",
        "finding_id",
        "historical_outcome",
        "matched_edition_date",
        "matched_source_or_cluster_id",
        "publication_approval",
        "publication_eligible",
        "queue_action",
        "review_status",
    }
    payload = {key: value for key, value in finding.items() if key not in excluded}
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def generic_review_fixture(
    root: Path,
    *,
    identifier_kind: str = "audit_candidate_id",
    decision: str = "confirmed",
    attribution_mode: str = "official_claim",
    period: bool = False,
    unknown_date: bool = False,
) -> tuple[list[str], dict[str, Path], dict]:
    raw_payload = b"synthetic Gaza historical candidate fixture\n"
    raw_sha = hashlib.sha256(raw_payload).hexdigest()
    base = root / "data/agent-history/gaza"
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    review_path = base / "reviews" / f"synthetic-{identifier_kind}-{decision}.json"
    source_url = "https://example.org/gaza/synthetic-candidate"
    identifier = "synthetic-audit-candidate" if identifier_kind == "audit_candidate_id" else "synthetic-finding"
    run_id = "synthetic-gaza-generic-review-run"
    finding = {
        identifier_kind: identifier,
        "agent_run_id": run_id,
        "historical_outcome": "new_historical_candidate",
        "review_status": "pending_review",
        "publication_eligible": False,
        "publication_approval": False,
        "queue_action": None,
        "canonical_source_url": source_url,
        "source_url": source_url,
        "source_published_at": "2026-04-16",
        "title": "Synthetic historical Gaza access finding",
        "category": "humanitarian_access",
    }
    date_assessment = {"source_published_at": "2026-04-16"}
    if unknown_date:
        finding["event_date_status"] = "unknown"
        date_assessment.update(
            event_date_status="unknown",
            unknown_event_date_explanation="The source does not identify the onset date.",
        )
    elif period:
        finding.update(event_period_start="2026-04-10", event_period_end="2026-04-15")
        date_assessment["event_period"] = {"start": "2026-04-10", "end": "2026-04-15"}
    else:
        finding["event_date"] = "2026-04-15"
        date_assessment["event_date"] = "2026-04-15"
    report_finding = {
        **finding,
        "source_date": "2026-04-16",
    }
    write_json(
        raw_path,
        {
            "domain": "gaza",
            "raw_sha256": raw_sha,
            "raw_bytes_base64": base64.b64encode(raw_payload).decode("ascii"),
            "agent_run_id": run_id,
        },
    )
    write_json(
        normalized_path,
        {
            "schema_version": "historical_agent_normalized_v1",
            "domain": "gaza",
            "raw_sha256": raw_sha,
            "agent_run_id": run_id,
            "findings": [finding],
        },
    )
    write_json(
        report_path,
        {
            "domain": "gaza",
            "input_sha256": raw_sha,
            "agent_run_id": run_id,
            "gaza_findings": [report_finding],
        },
    )
    attribution = {
        "mode": attribution_mode,
        "attributed_to": "Synthetic Gaza authority",
        "safe_future_wording": "The authority said access had changed; independent verification remained unavailable.",
        "attribution_preserved": True,
        "uncertainty_preserved": True,
        "unsupported_certainty_escalation": False,
    }
    if attribution_mode == "organizational_estimate":
        attribution.update(
            estimate_not_independently_verified=True,
            methodology_preserved=True,
        )
    if attribution_mode == "allegation":
        attribution["allegation_not_adjudicated"] = True
    if attribution_mode == "single_source_report":
        attribution["single_source_uncertainty_preserved"] = True
    if attribution_mode == "multi_source_disputed_quantity":
        attribution.update(
            disputed_values=[
                {"value": 7, "source_url": source_url},
                {"value": 9, "source_url": "https://corroborator.example/gaza"},
            ],
            dispute_unresolved=True,
        )
    review = {
        "schema_version": "gaza_historical_editorial_review_v2",
        "review_type": "historical_editorial_review",
        "domain": "gaza",
        "raw_sha256": raw_sha,
        identifier_kind if identifier_kind == "audit_candidate_id" else "normalized_finding_id": identifier,
        "agent_run_id": run_id,
        "normalized_artifact_sha256": file_digest(normalized_path),
        "report_artifact_sha256": file_digest(report_path),
        "decision": decision,
        "decision_reason": "Synthetic bounded editorial rationale.",
        "candidate_event_fingerprint": candidate_fingerprint(finding),
        "current_review_status": "pending_review",
        "current_publication_eligible": False,
        "current_publication_approval": False,
        "current_queue_action": "none",
        "resulting_review_state": {
            "confirmed": "substantively_reviewed",
            "corrected": "substantively_reviewed",
            "deferred": "pending_review",
            "rejected": "excluded",
            "duplicate": "excluded",
        }[decision],
        "archive_mutation_authorized": False,
        "edition_authorized": False,
        "publication_authorized": False,
        "queue_authorized": False,
        "source_record_authorized": False,
        "cluster_authorized": False,
        "audio_authorized": False,
        "date_assessment": date_assessment,
        "taxonomy_review": {
            "domain": "gaza",
            "category": "humanitarian_access",
        },
        "attribution_assessment": attribution,
        "evidence_references": [
            {
                "role": "principal",
                "url": source_url,
                "supporting_passage": "The synthetic authority described the access change.",
            }
        ],
    }
    if decision in {"confirmed", "corrected"}:
        review["duplicate_and_authoritative_match_check"] = {
            "candidate_remains_distinct": True,
            "existing_edition_match": None,
            "existing_source_match": None,
            "existing_story_cluster_match": None,
            "existing_historical_match": None,
        }
    if decision == "corrected":
        review["evidence_references"].append(
            {
                "role": "corroborating",
                "url": "https://corroborator.example/gaza",
                "supporting_passage": "A second source supported the corrected quantity.",
            }
        )
        review["correction_lineage"] = {
            "prior_reference": {
                "type": "published_story",
                "id": "synthetic-prior-story",
                "edition_date": "2026-04-15",
            },
            "prior_event_fingerprint": {
                "event_identity": "synthetic-access-event",
                "fingerprint": "sha256:old",
            },
            "corrected_event_fingerprint": {
                "event_identity": "synthetic-access-event",
                "fingerprint": review["candidate_event_fingerprint"],
            },
            "field_or_claim": "affected_facilities",
            "previous_value": 2,
            "corrected_value": 3,
            "evidence_reference_indexes": [0, 1],
            "corroboration_required": True,
            "materiality_explanation": "The corrected number materially changes the access assessment.",
            "remaining_uncertainty": {
                "persists": True,
                "description": "The sources do not establish the duration of closure.",
            },
            "prior_public_artifact_overwritten": False,
        }
    elif decision == "deferred":
        review["unresolved_requirement"] = "Obtain an independently attributable second source."
    elif decision == "rejected":
        review["rejection_basis"] = "The supplied evidence does not support the candidate claim."
    elif decision == "duplicate":
        review["matched_reference"] = {
            "type": "published_story",
            "id": "synthetic-prior-story",
            "event_fingerprint": "sha256:existing",
        }
    if decision in {"corrected", "duplicate"}:
        write_json(
            root / "data/records/story_memory.json",
            [
                {
                    "dispatch_slug": "gaza",
                    "story_id": "synthetic-prior-story",
                    "source_url": "https://prior.example/gaza/story",
                    "edition_date": "2026-04-15",
                    "title": "Synthetic prior public Gaza story",
                }
            ],
        )
    write_json(review_path, review)
    args = [
        "review",
        "--domain",
        "gaza",
        "--raw-sha",
        raw_sha,
        "--decision",
        decision,
        "--review-artifact",
        str(review_path),
        "--review-artifact-sha256",
        file_digest(review_path),
        "--repo-root",
        str(root),
    ]
    return args, {
        "raw": raw_path,
        "normalized": normalized_path,
        "report": report_path,
        "review": review_path,
    }, review


def refresh_review_digest(args: list[str], review_path: Path) -> None:
    args[args.index("--review-artifact-sha256") + 1] = file_digest(review_path)


def run_json(capsys, args: list[str]) -> dict:
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("identifier_kind", ["finding_id", "audit_candidate_id"])
def test_generic_review_supports_both_candidate_identifier_types(
    tmp_path: Path, capsys, identifier_kind: str
):
    args, paths, _ = generic_review_fixture(tmp_path, identifier_kind=identifier_kind)
    immutable = {name: file_digest(path) for name, path in paths.items()}
    result = run_json(capsys, args)
    assert result["status"] == "decision_recorded"
    output_key = identifier_kind
    assert result[output_key].startswith("synthetic-")
    assert result["resulting_review_state"] == "substantively_reviewed"
    assert result["publication_authorized"] is False
    assert {name: file_digest(path) for name, path in paths.items()} == immutable


@pytest.mark.parametrize(
    ("decision", "state"),
    [
        ("confirmed", "substantively_reviewed"),
        ("corrected", "substantively_reviewed"),
        ("deferred", "pending_review"),
        ("rejected", "excluded"),
        ("duplicate", "excluded"),
    ],
)
def test_generic_decision_vocabulary_is_closed_and_nonpublishing(
    tmp_path: Path, capsys, decision: str, state: str
):
    args, _, _ = generic_review_fixture(tmp_path, decision=decision)
    result = run_json(capsys, args)
    audit = json.loads(Path(result["decision_audit_path"]).read_text(encoding="utf-8"))
    assert audit["decision"] == decision
    assert audit["resulting_review_state"] == state
    assert audit["publication_eligible"] is False
    assert audit["publication_approval"] is False
    assert audit["publication_authorized"] is False
    assert audit["queue_authorized"] is False
    assert audit["queue_action"] == "none"


def test_unknown_decision_fails_closed(tmp_path: Path):
    args, _, _ = generic_review_fixture(tmp_path)
    args[args.index("confirmed")] = "approved"
    with pytest.raises(ValueError, match="unsupported"):
        main(args)


@pytest.mark.parametrize(
    "state_field",
    [
        "approved",
        "release_ready",
        "queued",
        "publishing",
        "published",
    ],
)
def test_generic_review_rejects_publication_capable_states(
    tmp_path: Path, state_field: str
):
    args, paths, review = generic_review_fixture(tmp_path)
    review[state_field] = True
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match=state_field):
        main(args)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown_identifier", "resolve exactly once"),
        ("conflicting_identifiers", "exactly one"),
        ("duplicate_identifier", "resolve exactly once"),
        ("cross_run", "agent_run_id lineage"),
        ("cross_domain", "candidate domain"),
        ("digest_mismatch", "normalized artifact digest"),
        ("fingerprint_mismatch", "event fingerprint"),
        ("candidate_source_mismatch", "principal evidence source"),
        ("report_source_mismatch", "source lineage"),
        ("invalid_transition", "pending_review"),
    ],
)
def test_generic_identity_and_state_checks_fail_closed(
    tmp_path: Path, mutation: str, message: str
):
    args, paths, review = generic_review_fixture(tmp_path)
    normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    if mutation == "unknown_identifier":
        review["audit_candidate_id"] = "unknown"
    elif mutation == "conflicting_identifiers":
        review["normalized_finding_id"] = "synthetic-finding"
    elif mutation == "duplicate_identifier":
        normalized["findings"].append(json.loads(json.dumps(normalized["findings"][0])))
        write_json(paths["normalized"], normalized)
        review["normalized_artifact_sha256"] = file_digest(paths["normalized"])
    elif mutation == "cross_run":
        review["agent_run_id"] = "another-run"
    elif mutation == "cross_domain":
        normalized["findings"][0]["domain"] = "food-line"
        write_json(paths["normalized"], normalized)
        review["normalized_artifact_sha256"] = file_digest(paths["normalized"])
    elif mutation == "digest_mismatch":
        review["normalized_artifact_sha256"] = "0" * 64
    elif mutation == "fingerprint_mismatch":
        review["candidate_event_fingerprint"] = "sha256:" + "0" * 64
    elif mutation == "candidate_source_mismatch":
        review["evidence_references"][0]["url"] = "https://other.example/gaza"
    elif mutation == "report_source_mismatch":
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        report["gaza_findings"][0]["canonical_source_url"] = "https://other.example/gaza"
        report["gaza_findings"][0]["source_url"] = "https://other.example/gaza"
        write_json(paths["report"], report)
        review["report_artifact_sha256"] = file_digest(paths["report"])
    elif mutation == "invalid_transition":
        normalized["findings"][0]["review_status"] = "substantively_reviewed"
        write_json(paths["normalized"], normalized)
        review["normalized_artifact_sha256"] = file_digest(paths["normalized"])
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match=message):
        main(args)
    assert not (tmp_path / "data/agent-history/gaza/reviews/decisions").exists()


def test_generic_review_requires_evidence_for_every_decision(tmp_path: Path):
    args, paths, review = generic_review_fixture(tmp_path)
    review["evidence_references"] = []
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match="requires evidence"):
        main(args)


def test_report_duplicate_identifier_fails_closed(tmp_path: Path):
    args, paths, review = generic_review_fixture(tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["gaza_findings"].append(json.loads(json.dumps(report["gaza_findings"][0])))
    write_json(paths["report"], report)
    review["report_artifact_sha256"] = file_digest(paths["report"])
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match="duplicated in the import report"):
        main(args)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_prior", "prior candidate or story"),
        ("missing_fingerprint", "prior and corrected fingerprints"),
        ("unrelated_event", "unrelated events"),
        ("same_value", "values must differ"),
        ("missing_evidence", "traceable evidence"),
        ("uncertainty_missing", "remaining uncertainty"),
        ("overwrite", "cannot overwrite"),
        ("self_reference", "cannot be the selected candidate"),
    ],
)
def test_correction_lineage_fails_closed(
    tmp_path: Path, mutation: str, message: str
):
    args, paths, review = generic_review_fixture(tmp_path, decision="corrected")
    lineage = review["correction_lineage"]
    if mutation == "missing_prior":
        lineage.pop("prior_reference")
    elif mutation == "missing_fingerprint":
        lineage.pop("prior_event_fingerprint")
    elif mutation == "unrelated_event":
        lineage["corrected_event_fingerprint"]["event_identity"] = "another-event"
    elif mutation == "same_value":
        lineage["corrected_value"] = lineage["previous_value"]
    elif mutation == "missing_evidence":
        lineage["evidence_reference_indexes"] = []
    elif mutation == "uncertainty_missing":
        lineage.pop("remaining_uncertainty")
    elif mutation == "overwrite":
        lineage["prior_public_artifact_overwritten"] = True
    elif mutation == "self_reference":
        lineage["prior_reference"] = {
            "type": "historical_candidate",
            "id": review["audit_candidate_id"],
        }
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match=message):
        main(args)


def test_valid_correction_preserves_prior_public_artifact(tmp_path: Path, capsys):
    prior = tmp_path / "output/site/gaza/editions/2026-04-15/index.html"
    prior.parent.mkdir(parents=True)
    prior.write_text("immutable prior public story", encoding="utf-8")
    before = prior.read_bytes()
    args, paths, _ = generic_review_fixture(tmp_path, decision="corrected")
    normalized_before = paths["normalized"].read_bytes()
    result = run_json(capsys, args)
    assert result["resulting_review_state"] == "substantively_reviewed"
    assert prior.read_bytes() == before
    assert paths["normalized"].read_bytes() == normalized_before


def test_candidate_derived_period_passes(tmp_path: Path, capsys):
    args, _, _ = generic_review_fixture(tmp_path, period=True)
    result = run_json(capsys, args)
    audit = json.loads(Path(result["decision_audit_path"]).read_text(encoding="utf-8"))
    assert audit["date_assessment"]["event_period_start"] == "2026-04-10"
    assert audit["date_assessment"]["event_period_end"] == "2026-04-15"


def test_unknown_event_date_is_retained_explicitly(tmp_path: Path, capsys):
    args, _, _ = generic_review_fixture(tmp_path, unknown_date=True)
    result = run_json(capsys, args)
    audit = json.loads(Path(result["decision_audit_path"]).read_text(encoding="utf-8"))
    assert audit["date_assessment"]["event_date_status"] == "unknown"
    assert audit["date_assessment"]["unknown_event_date_explanation"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("rewritten_date", "event_date does not match"),
        ("invalid_range", "start must not follow"),
        ("unknown_taxonomy", "outside the allowlist"),
        ("substituted_taxonomy", "cannot substitute"),
        ("cross_domain_taxonomy", "must remain gaza"),
        ("unknown_taxonomy_field", "unsupported fields"),
    ],
)
def test_candidate_dates_and_taxonomy_fail_closed(
    tmp_path: Path, mutation: str, message: str
):
    args, paths, review = generic_review_fixture(tmp_path, period=mutation == "invalid_range")
    normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    if mutation == "rewritten_date":
        review["date_assessment"]["event_date"] = "2026-08-30"
    elif mutation == "invalid_range":
        normalized["findings"][0]["event_period_start"] = "2026-04-20"
        write_json(paths["normalized"], normalized)
        review["normalized_artifact_sha256"] = file_digest(paths["normalized"])
        review["candidate_event_fingerprint"] = candidate_fingerprint(
            normalized["findings"][0]
        )
        review["date_assessment"]["event_period"]["start"] = "2026-04-20"
    elif mutation == "unknown_taxonomy":
        normalized["findings"][0]["category"] = "food_pressure"
        write_json(paths["normalized"], normalized)
        review["normalized_artifact_sha256"] = file_digest(paths["normalized"])
        review["candidate_event_fingerprint"] = candidate_fingerprint(
            normalized["findings"][0]
        )
        review["taxonomy_review"]["category"] = "food_pressure"
    elif mutation == "substituted_taxonomy":
        review["taxonomy_review"]["event_type"] = "casualty_event"
    elif mutation == "cross_domain_taxonomy":
        review["taxonomy_review"]["domain"] = "food-line"
    elif mutation == "unknown_taxonomy_field":
        review["taxonomy_review"]["food_pressure"] = "household_cost"
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match=message):
        main(args)


@pytest.mark.parametrize(
    "mode",
    [
        "direct_official_record",
        "official_claim",
        "organizational_estimate",
        "allegation",
        "single_source_report",
        "multi_source_disputed_quantity",
    ],
)
def test_supported_attribution_modes_pass(tmp_path: Path, capsys, mode: str):
    args, _, _ = generic_review_fixture(tmp_path, attribution_mode=mode)
    result = run_json(capsys, args)
    assert result["status"] == "decision_recorded"


@pytest.mark.parametrize(
    ("mode", "mutation", "message"),
    [
        ("organizational_estimate", "unattributed", "authority"),
        ("allegation", "adjudicated", "adjudicated fact"),
        ("single_source_report", "single_source", "source uncertainty"),
        ("official_claim", "certainty", "certainty escalation"),
        ("multi_source_disputed_quantity", "collapse_dispute", "at least two values"),
    ],
)
def test_attribution_safeguards_fail_closed(
    tmp_path: Path, mode: str, mutation: str, message: str
):
    args, paths, review = generic_review_fixture(tmp_path, attribution_mode=mode)
    attribution = review["attribution_assessment"]
    if mutation == "unattributed":
        attribution["attributed_to"] = ""
    elif mutation == "adjudicated":
        attribution["allegation_not_adjudicated"] = False
    elif mutation == "single_source":
        attribution["single_source_uncertainty_preserved"] = False
    elif mutation == "certainty":
        attribution["unsupported_certainty_escalation"] = True
    elif mutation == "collapse_dispute":
        attribution["disputed_values"] = attribution["disputed_values"][:1]
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match=message):
        main(args)


def test_review_dry_run_writes_nothing(tmp_path: Path, capsys):
    args, paths, _ = generic_review_fixture(tmp_path)
    args.append("--dry-run")
    before = {path: file_digest(path) for path in tmp_path.rglob("*") if path.is_file()}
    result = run_json(capsys, args)
    after = {path: file_digest(path) for path in tmp_path.rglob("*") if path.is_file()}
    assert result["status"] == "dry_run_validated"
    assert result["persistent_mutation"] is False
    assert before == after
    assert not Path(result["decision_audit_path"]).exists()


def test_exact_replay_is_noop_and_conflicting_replay_fails(tmp_path: Path, capsys):
    args, paths, review = generic_review_fixture(tmp_path)
    first = run_json(capsys, args)
    decision_path = Path(first["decision_audit_path"])
    first_digest = file_digest(decision_path)
    replay = run_json(capsys, args)
    assert replay["status"] == "idempotent_noop"
    assert file_digest(decision_path) == first_digest
    review["decision_reason"] = "A conflicting editorial rationale."
    write_json(paths["review"], review)
    refresh_review_digest(args, paths["review"])
    with pytest.raises(ValueError, match="conflicts"):
        main(args)
    assert file_digest(decision_path) == first_digest


def test_apply_mutates_only_private_decision_artifact(tmp_path: Path, capsys):
    protected = [
        tmp_path / "output/site/gaza/index.html",
        tmp_path / "output/dispatches/gaza/marker.txt",
        tmp_path / "bluefern-dispatches-pages/gaza/index.html",
        tmp_path / "schedules/gaza.txt",
        tmp_path / "data/dispatches/gaza/review_queue.json",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")
    args, paths, _ = generic_review_fixture(tmp_path)
    before = {path: path.read_bytes() for path in [*protected, *paths.values()]}
    result = run_json(capsys, args)
    assert all(path.read_bytes() == value for path, value in before.items())
    changed = [path for path in tmp_path.rglob("*") if path.is_file() and path not in before]
    assert changed == [Path(result["decision_audit_path"])]
