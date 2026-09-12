from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from recover_food_line_operator_evidence import recover

from bluefern_dispatches.external_agent_handoff import OPERATOR_RECOVERY_CLASS, import_envelope


def _operator_envelope(run_id: str = "food-line-source-watch-20260910T000000Z-fixture") -> dict:
    return {
        "schema_version": 1,
        "agent_name": "Food Line Source Watch",
        "agent_run_id": run_id,
        "started_at": "2026-09-10T00:00:00Z",
        "completed_at": "2026-09-10T00:01:00Z",
        "search_window": {"date_from": "2026-09-10", "date_to": "2026-09-10", "edition_date": "2026-09-10"},
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "recovery_provenance": {
            "provenance_class": OPERATOR_RECOVERY_CLASS,
            "original_production_artifact_present": False,
            "production_collection_failed_before_discovery": True,
            "recovery_reason": "original production Source Watch artifact absent because scheduled collection failed closed before discovery",
            "operator_preserved_at": None,
            "recovery_timestamp": "2026-09-11T00:00:00Z",
            "recovery_method": "complete externally preserved Source Watch payload",
            "eligible_for_review": True,
            "eligible_for_automatic_publication": False,
        },
        "findings": [
            {
                "canonical_source_url": "https://example.org/food-access",
                "publisher": "Example Local News",
                "source_published_at": "2026-09-10T01:00:00Z",
                "title": "Food pantry reports shortage",
                "exact_supporting_passage": "The pantry said it cannot meet rising demand for food bags this week.",
                "summary": "The pantry reports rising demand and not enough food bags.",
                "location_name": "Example City",
                "state": "CA",
                "location_scope": "local",
                "affected_groups": ["pantry clients"],
                "pressure_type": "pantry_shortage",
                "confidence": "high",
                "source_role": "local_news",
                "evidence_level": "source_backed_exact_passage",
                "review_status": "pending_review",
                "exclusion_reason": None,
                "source_verification_status": "syntactic_url_verified",
                "uncertainty_note": "operator recovery fixture",
            }
        ],
        "coverage_notes": "Operator-preserved complete Source Watch payload; not original production runner output.",
    }


def _bundle(*envelopes: dict) -> dict:
    return {
        "schema_version": "bluefern.operator_recovered_source_watch_bundle.v1",
        "dispatch": "food-line",
        "edition_date": "2026-09-10",
        "provenance_class": OPERATOR_RECOVERY_CLASS,
        "production_collection_failed_before_discovery": True,
        "original_production_artifacts_present": False,
        "payloads": list(envelopes),
    }


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_operator_recovered_evidence_enters_review_without_original_lineage(tmp_path: Path) -> None:
    source = tmp_path / "bundle.json"
    _write(source, _bundle(_operator_envelope()))

    result = recover(tmp_path, source, Path("reconciliation.json"))

    assert result["unaccounted"] == 0
    assert result["accepted_as_valid_recovery_evidence"] == 1
    assert result["retained_for_review"] == 1
    row = result["rows"][0]
    assert row["provenance_class"] == OPERATOR_RECOVERY_CLASS
    assert row["provenance_class"] != "original_production_source_watch_artifact"
    assert row["eligible_for_automatic_publication"] is False
    artifact = json.loads(
        (tmp_path / "data/dispatches/food-line/agent-intake/2026-09-10/food-line-source-watch-20260910T000000Z-fixture.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = artifact["candidate_rows"][0]
    assert candidate["review_status"] == "pending_review"
    assert candidate["recovery_provenance"]["recovery_reason"]
    assert candidate["recovery_provenance"]["original_production_artifact_present"] is False


def test_prose_only_summary_cannot_enter_operator_recovery(tmp_path: Path) -> None:
    source = tmp_path / "prose.json"
    payload = _operator_envelope("food-line-source-watch-20260910T000000Z-prose")
    payload["findings"] = [{"title": "Pantry strain summary only", "summary": "A prose-only retrospective summary."}]
    _write(source, payload)

    code, receipt = import_envelope(tmp_path, source, dispatch="food-line")

    assert code != 0
    assert receipt["classification"] == "MALFORMED"
    assert "canonical source URL" in receipt["error"]


def test_synthetic_or_test_evidence_cannot_enter_operator_recovery(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.json"
    payload = _operator_envelope("synthetic-food-line-source-watch-20260910T000000Z")
    _write(source, payload)

    code, receipt = import_envelope(tmp_path, source, dispatch="food-line")

    assert code != 0
    assert receipt["classification"] == "MALFORMED"
    assert "synthetic/test evidence" in receipt["error"]


def test_original_run_id_source_uncertainty_and_zero_unaccounted_are_durable(tmp_path: Path) -> None:
    run_id = "food-line-source-watch-20260910T000000Z-uncertainty"
    envelope = _operator_envelope(run_id)
    envelope["findings"][0]["uncertainty_note"] = "current page may have changed"
    source = tmp_path / "bundle.json"
    _write(source, _bundle(envelope))

    result = recover(tmp_path, source, Path("reconciliation.json"))

    assert result["unaccounted"] == 0
    assert result["rows"][0]["agent_run_id"] == run_id
    artifact = json.loads((tmp_path / "data/dispatches/food-line/agent-intake/2026-09-10" / f"{run_id}.json").read_text(encoding="utf-8"))
    candidate = artifact["candidate_rows"][0]
    assert candidate["raw_agent_payload"]["uncertainty_note"] == "current page may have changed"
    assert candidate["recovery_provenance"]["original_agent_run_id"] == run_id


def test_operator_recovery_rerun_is_idempotent_and_does_not_duplicate_review_rows(tmp_path: Path) -> None:
    source = tmp_path / "bundle.json"
    _write(source, _bundle(_operator_envelope()))

    first = recover(tmp_path, source, Path("reconciliation.json"))
    second = recover(tmp_path, source, Path("reconciliation.json"))

    assert first["unaccounted"] == 0
    assert second["unaccounted"] == 0
    artifact_path = tmp_path / "data/dispatches/food-line/agent-intake/2026-09-10/food-line-source-watch-20260910T000000Z-fixture.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert len(artifact["candidate_rows"]) == 1
    receipts = list((tmp_path / "data/private-agent-handoff/receipts/food-line/2026-09-10").glob("*.json"))
    assert len(receipts) == 2
