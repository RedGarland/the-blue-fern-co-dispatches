from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.external_agent_handoff import OPERATOR_RECOVERY_CLASS
from bluefern_dispatches.food_line_operator_recovery_release_prep import (
    PRIVATE_RELEASE_PREP_STATE,
    SCHEMA_VERSION,
    write_release_prep_artifact,
)

ITEM_ID = "finding-fixture"
RUN_ID = "food-line-source-watch-20260910T000000Z-fixture"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_reconciliation() -> dict:
    return {
        "schema_version": "bluefern.operator_recovered_source_watch_reconciliation.v1",
        "dispatch": "food-line",
        "edition_date": "2026-09-10",
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "original_production_artifact_present": False,
        "production_collection_failed_before_discovery": True,
        "publication_performed": False,
        "pages_modified": False,
        "synthetic_evidence_used": False,
        "source_verification": [
            {
                "agent_run_id": RUN_ID,
                "canonical_source_url": "https://example.org/food-access",
                "publisher": "Example Local News",
                "source_role": "local_news",
                "supporting_evidence_present": True,
                "source_verification_status": "current_url_verified",
            }
        ],
        "rows": [
            {
                "agent_run_id": RUN_ID,
                "candidate_id": ITEM_ID,
                "title": "Food pantry reports shortage",
                "canonical_source_url": "https://example.org/food-access",
                "publisher": "Example Local News",
                "corrected_disposition": "retained_for_review",
                "duplicate_linkage": {},
                "reason": "",
                "provenance_class": OPERATOR_RECOVERY_CLASS,
                "eligible_for_automatic_publication": False,
                "resolution_status": "accounted",
                "receipt_status": "SUCCESS",
                "receipt_classification": "ACCEPTED",
            }
        ],
    }


def _valid_editorial(disposition: str = "APPROVE_WITH_EDIT") -> dict:
    return {
        "review_id": "fixture-review",
        "review_type": "human_editorial_review",
        "reviewed_at": "2026-09-12T00:00:00Z",
        "repository": "RedGarland/the-blue-fern-co-dispatches",
        "protected_head": "17b9b7256341714316b6e5df75a8dd96ea54f544",
        "recovery_provenance": OPERATOR_RECOVERY_CLASS,
        "scope_boundaries": {
            "original_production_lineage_rewritten_or_upgraded": False,
            "publication_authority_granted": False,
        },
        "items": [
            {
                "item": "Fixture food access item",
                "agent_run_id": RUN_ID,
                "candidate_id": ITEM_ID,
                "source_url": "https://example.org/food-access",
                "source_publisher": "Example Local News",
                "event_date_or_source_date": "2026-09-10",
                "source_lineage_verified": True,
                "source_traceable_at_review": True,
                "provenance_preserved": OPERATOR_RECOVERY_CLASS,
                "eligible_for_automatic_publication": False,
                "retained_for_review": True,
                "assessments": {
                    "in_scope_geography": "APPROVED: Example City is in Food Line scope.",
                    "materiality": "APPROVED: pantry shortage creates material food-access pressure.",
                    "duplicate_status": "NO_DUPLICATE_FOUND: no matching Food Line event was found.",
                    "supporting_evidence": "SUFFICIENT",
                },
                "editorial_disposition": disposition,
                "corrected_headline": "Example pantry reports staple shortages",
                "bounded_source_backed_summary": "Example Local News reported that the pantry could not meet rising demand for food bags this week.",
            }
        ],
    }


def _valid_release_decision(state: str = PRIVATE_RELEASE_PREP_STATE) -> dict:
    return {
        "release_decision_id": "fixture-release-decision",
        "decision_type": "separate_release_decision",
        "decided_at": "2026-09-12T00:00:00Z",
        "repository": "RedGarland/the-blue-fern-co-dispatches",
        "protected_baseline": "17b9b7256341714316b6e5df75a8dd96ea54f544",
        "recovery_provenance": OPERATOR_RECOVERY_CLASS,
        "scope_boundaries": {
            "publication_performed": False,
            "pages_modified": False,
            "archive_rss_homepage_modified": False,
            "schedules_modified": False,
            "operational_health_state_modified": False,
            "food_line_operationally_recovered": False,
            "eligible_for_automatic_publication_changed": False,
            "publication_eligible_changed": False,
            "publication_approval_changed": False,
        },
        "items": [
            {
                "item": "Fixture food access item",
                "agent_run_id": RUN_ID,
                "candidate_id": ITEM_ID,
                "source_url": "https://example.org/food-access",
                "source_publisher": "Example Local News",
                "operator_recovery_lineage_verified": True,
                "recovery_provenance_preserved": OPERATOR_RECOVERY_CLASS,
                "original_failed_runtime_distinction_preserved": True,
                "editorial_review_disposition_verified": "APPROVE_WITH_EDIT",
                "approved_headline_verified": "Example pantry reports staple shortages",
                "approved_summary_verified": "Example Local News reported that the pantry could not meet rising demand for food bags this week.",
                "source_traceability_verified": True,
                "duplicate_status": "NO_DUPLICATE_FOUND",
                "event_currentness_date": "2026-09-10",
                "geography_verified": "Example City",
                "material_food_line_relevance_verified": True,
                "release_preparation_state": state,
                "publication_eligible": False,
                "publication_approval": False,
            }
        ],
    }


def _fixture(root: Path, *, editorial: dict | None = None, release_decision: dict | None = None, reconciliation: dict | None = None) -> dict[str, str]:
    paths = {
        "reconciliation": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json",
        "editorial": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/editorial-review.json",
        "release": "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/release-decision.json",
    }
    _write_json(root / paths["reconciliation"], reconciliation or _valid_reconciliation())
    _write_json(root / paths["editorial"], editorial or _valid_editorial())
    _write_json(root / paths["release"], release_decision or _valid_release_decision())
    return paths


def _prepare(root: Path, paths: dict[str, str]) -> dict:
    return write_release_prep_artifact(
        root=root,
        item_id=ITEM_ID,
        edition_date="2026-09-10",
        reconciliation_ref=paths["reconciliation"],
        editorial_review_ref=paths["editorial"],
        release_decision_ref=paths["release"],
    )


def test_valid_operator_recovered_human_approved_item_enters_private_release_prep(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    result = _prepare(tmp_path, paths)

    artifact = result["artifact"]
    assert result["status"] == "created"
    assert artifact["schema_version"] == SCHEMA_VERSION
    assert artifact["release_preparation_state"] == PRIVATE_RELEASE_PREP_STATE
    assert artifact["release_preparation_eligible"] is True
    assert artifact["provenance_class"] == OPERATOR_RECOVERY_CLASS
    assert artifact["original_production_artifact_present"] is False
    assert artifact["production_collection_failed_before_discovery"] is True
    assert artifact["original_agent_run_id"] == RUN_ID
    assert artifact["eligible_for_automatic_publication"] is False
    assert artifact["publication_eligible"] is False
    assert artifact["publication_approval"] is False
    assert artifact["publication_performed"] is False
    assert artifact["public_generation_authorized"] is False


def test_approve_with_edit_preserves_reviewed_copy(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    artifact = _prepare(tmp_path, paths)["artifact"]

    assert artifact["approved_headline"] == "Example pantry reports staple shortages"
    assert (
        artifact["approved_summary"]
        == "Example Local News reported that the pantry could not meet rising demand for food bags this week."
    )


@pytest.mark.parametrize("disposition", ["HOLD", "REJECT"])
def test_hold_and_reject_are_rejected(tmp_path: Path, disposition: str) -> None:
    paths = _fixture(tmp_path, editorial=_valid_editorial(disposition))

    with pytest.raises(ValueError, match=f"{disposition} cannot enter private release preparation"):
        _prepare(tmp_path, paths)


def test_synthetic_evidence_is_rejected(tmp_path: Path) -> None:
    reconciliation = _valid_reconciliation()
    reconciliation["synthetic_evidence_used"] = True
    paths = _fixture(tmp_path, reconciliation=reconciliation)

    with pytest.raises(ValueError, match="synthetic_evidence_used must be false"):
        _prepare(tmp_path, paths)


def test_missing_run_id_is_rejected(tmp_path: Path) -> None:
    reconciliation = _valid_reconciliation()
    reconciliation["rows"][0]["agent_run_id"] = ""
    paths = _fixture(tmp_path, reconciliation=reconciliation)

    with pytest.raises(ValueError, match="original agent run ID must be present"):
        _prepare(tmp_path, paths)


def test_missing_source_evidence_is_rejected(tmp_path: Path) -> None:
    reconciliation = _valid_reconciliation()
    reconciliation["source_verification"][0]["supporting_evidence_present"] = False
    paths = _fixture(tmp_path, reconciliation=reconciliation)

    with pytest.raises(ValueError, match="source-backed supporting evidence must be present"):
        _prepare(tmp_path, paths)


def test_missing_editorial_review_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    (tmp_path / paths["editorial"]).unlink()

    with pytest.raises(ValueError, match="required artifact is missing"):
        _prepare(tmp_path, paths)


def test_unresolved_duplicate_is_rejected(tmp_path: Path) -> None:
    release_decision = _valid_release_decision()
    release_decision["items"][0]["duplicate_status"] = "UNRESOLVED_DUPLICATE"
    paths = _fixture(tmp_path, release_decision=release_decision)

    with pytest.raises(ValueError, match="duplicate state must be resolved and nonblocking"):
        _prepare(tmp_path, paths)


def test_automatic_publication_eligibility_cannot_become_true(tmp_path: Path) -> None:
    reconciliation = _valid_reconciliation()
    reconciliation["rows"][0]["eligible_for_automatic_publication"] = True
    paths = _fixture(tmp_path, reconciliation=reconciliation)

    with pytest.raises(ValueError, match="eligible_for_automatic_publication must remain false"):
        _prepare(tmp_path, paths)


def test_publication_approval_cannot_become_true(tmp_path: Path) -> None:
    release_decision = _valid_release_decision()
    release_decision["items"][0]["publication_approval"] = True
    paths = _fixture(tmp_path, release_decision=release_decision)

    with pytest.raises(ValueError, match="publication_approval must remain false"):
        _prepare(tmp_path, paths)


def test_no_public_pages_scheduler_or_operational_health_files_are_written(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    failed_runtime = tmp_path / "status/food-line/2026-09-10-production-failure.json"
    operational_health = tmp_path / "status/operational-health/current.json"
    failed_runtime.parent.mkdir(parents=True, exist_ok=True)
    operational_health.parent.mkdir(parents=True, exist_ok=True)
    failed_runtime.write_text("failed\n", encoding="utf-8")
    operational_health.write_text("unchanged\n", encoding="utf-8")

    _prepare(tmp_path, paths)

    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()
    assert not (tmp_path / "scripts/windows").exists()
    assert failed_runtime.read_text(encoding="utf-8") == "failed\n"
    assert operational_health.read_text(encoding="utf-8") == "unchanged\n"


def test_missing_event_date_geography_or_materiality_is_rejected(tmp_path: Path) -> None:
    release_decision = _valid_release_decision()
    release_decision["items"][0]["event_currentness_date"] = ""
    paths = _fixture(tmp_path, release_decision=release_decision)
    with pytest.raises(ValueError, match="event/currentness date must contain an ISO date"):
        _prepare(tmp_path, paths)

    release_decision = _valid_release_decision()
    release_decision["items"][0]["geography_verified"] = ""
    paths = _fixture(tmp_path, release_decision=release_decision)
    with pytest.raises(ValueError, match="geography must be present"):
        _prepare(tmp_path, paths)

    release_decision = _valid_release_decision()
    release_decision["items"][0]["material_food_line_relevance_verified"] = False
    paths = _fixture(tmp_path, release_decision=release_decision)
    with pytest.raises(ValueError, match="material Food Line relevance must be verified"):
        _prepare(tmp_path, paths)


def test_exact_replay_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    first = _prepare(tmp_path, paths)
    second = _prepare(tmp_path, paths)

    assert first["status"] == "created"
    assert second["status"] == "idempotent_noop"
    assert second["artifact_sha256"] == first["artifact_sha256"]
