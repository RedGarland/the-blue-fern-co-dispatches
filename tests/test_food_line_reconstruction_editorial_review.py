import json
import subprocess
import sys
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_date_reconciliation import (
    DateReconciliationError,
    DateState,
    build_reconstruction_editorial_decision,
    record_reconstruction_editorial_decision,
    reconcile_food_line_date,
    reconstruction_candidate_fingerprint,
    validate_reconstruction_editorial_decision,
)

DATE = "2026-09-09"
EVALUATED = "2026-09-18T16:00:00Z"
DECIDED = "2026-09-18T17:00:00Z"


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _research_packet(root: Path, findings: list[dict], *, coverage_sufficient: bool = True) -> Path:
    return _write_json(
        root / "data/dispatches/food-line/historical-reconstruction-inputs" / f"{DATE}.json",
        {
            "schema_version": "food_line_historical_reconstruction_input_v1",
            "target_date": DATE,
            "researched_at": "2026-09-18T15:00:00Z",
            "research_mode": "codex_bounded_historical_web_research",
            "network_access": True,
            "queries_attempted": ["bounded target-date query"],
            "sources_attempted": ["example.test"],
            "sources_succeeded": ["example.test"],
            "sources_failed": [],
            "coverage_by_source_family": {"local_news": "attempted"},
            "coverage_by_pressure_type": {"food_pantry_demand_capacity": "attempted"},
            "coverage_by_geography": {"target": "attempted"},
            "coverage_sufficient": coverage_sufficient,
            "findings": findings,
            "limitations": [],
        },
    )


def _finding(candidate_id: str = "food-recon-1", disposition: str = "RETAINED_FOR_REVIEW") -> dict:
    return {
        "candidate_id": candidate_id,
        "disposition": disposition,
        "canonical_url": f"https://example.test/{candidate_id}",
        "source_published_at": DATE,
        "event_date": DATE,
        "pressure_type": "food_pantry_demand_capacity",
        "geography": "Test County",
        "supporting_passage": "A dated food-access pressure signal.",
        "title": f"Food pressure {candidate_id}",
        "confidence": "high",
    }


def _apply_reconstruction(root: Path, findings: list[dict] | None = None) -> tuple[Path, Path]:
    _research_packet(root, findings or [_finding()])
    result = reconcile_food_line_date(root, DATE, apply=True, evaluated_at=EVALUATED)
    assert result["network_reconstruction_performed"] is True
    recon = root / "data/dispatches/food-line/historical-reconstruction" / DATE / "reconstruction.json"
    research = recon.parent / "research-input.json"
    assert recon.is_file()
    assert research.is_file()
    return recon, research


def _decision(root: Path, candidate_id: str = "food-recon-1", decision: str = "APPROVE", **kwargs) -> dict:
    defaults = {"reviewer": "human-editor", "reason": "Reviewed source-backed reconstruction candidate.", "decided_at": DECIDED}
    defaults.update(kwargs)
    return build_reconstruction_editorial_decision(root, DATE, candidate_id, decision=decision, **defaults)


def test_retained_reconstruction_without_decision_requires_review(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)

    result = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["state"] == DateState.REVIEW_REQUIRED.value
    assert result["candidate_count"] == 1
    assert result["stages"]["historical_recovery_state"]["details"]["retained_candidate_count"] == 1


@pytest.mark.parametrize(
    ("decision", "extra"),
    [
        ("APPROVE", {}),
        ("APPROVE_WITH_EDIT", {"edited_headline": "Edited pressure headline"}),
        ("REJECT", {}),
        ("DUPLICATE", {"duplicate_of": "historical-event:existing-food-pressure"}),
        ("ALREADY_PUBLISHED", {"duplicate_of": "published:https://dispatches.example/food-line/edition"}),
    ],
)
def test_terminal_editorial_decisions_resolve_reconstructed_candidate(tmp_path: Path, decision: str, extra: dict) -> None:
    _apply_reconstruction(tmp_path)
    payload = _decision(tmp_path, decision=decision, **extra)
    result = record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", payload)

    reconciled = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["status"] == "recorded"
    assert reconciled["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert reconciled["candidate_count"] == 0
    assert payload["publication_approval"] is False
    assert payload["publication_eligible"] is False
    assert payload["pages_authorized"] is False


def test_hold_decision_remains_unresolved(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)
    record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", _decision(tmp_path, decision="HOLD"))

    result = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["state"] == DateState.REVIEW_REQUIRED.value
    assert result["candidate_count"] == 1


def test_seven_terminal_decisions_close_candidate_accounting_and_recover_gap(tmp_path: Path) -> None:
    findings = [_finding(f"food-recon-{index}") for index in range(1, 8)]
    _apply_reconstruction(tmp_path, findings)
    for item in findings:
        cid = item["candidate_id"]
        record_reconstruction_editorial_decision(tmp_path, DATE, cid, _decision(tmp_path, candidate_id=cid, decision="APPROVE"))

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = json.loads((tmp_path / "data/dispatches/food-line/coverage-gaps" / f"{DATE}.json").read_text(encoding="utf-8"))

    assert result["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert result["candidate_count"] == 0
    assert result["date_complete"] is True
    assert gap["backfill_status"] == "RECOVERED"
    assert result["stages"]["historical_recovery_state"]["details"]["publication_approval"] is False


def test_review_does_not_mutate_reconstruction_or_research_input(tmp_path: Path) -> None:
    recon, research = _apply_reconstruction(tmp_path)
    before_recon = recon.read_bytes()
    before_research = research.read_bytes()

    record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", _decision(tmp_path, decision="APPROVE_WITH_EDIT", edited_summary="Edited private summary."))

    assert recon.read_bytes() == before_recon
    assert research.read_bytes() == before_research


def test_exact_repeat_is_idempotent_and_conflicting_second_decision_fails(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)
    payload = _decision(tmp_path, decision="REJECT")

    first = record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", payload)
    second = record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", payload)
    conflicting = dict(payload, reason="A different substantive decision record.")

    assert first["status"] == "recorded"
    assert second["status"] == "idempotent_noop"
    with pytest.raises(DateReconciliationError):
        record_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", conflicting)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("research_input_sha256", "0" * 64, "research_input_sha256"),
        ("reconstruction_sha256", "0" * 64, "reconstruction_sha256"),
        ("candidate_fingerprint", "0" * 64, "candidate_fingerprint"),
    ],
)
def test_decision_hash_and_fingerprint_mismatches_fail_closed(tmp_path: Path, field: str, value: str, message: str) -> None:
    _apply_reconstruction(tmp_path)
    payload = _decision(tmp_path)
    payload[field] = value

    with pytest.raises(DateReconciliationError, match=message):
        validate_reconstruction_editorial_decision(tmp_path, DATE, "food-recon-1", payload)


def test_nonexistent_candidate_fails_closed(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)

    with pytest.raises(DateReconciliationError, match="exists 0 times"):
        build_reconstruction_editorial_decision(tmp_path, DATE, "missing", decision="APPROVE", reviewer="human", reason="No such candidate.", decided_at=DECIDED)


def test_decision_against_non_retained_finding_fails_closed(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path, [_finding("already", "ALREADY_PUBLISHED")])

    with pytest.raises(DateReconciliationError, match="not RETAINED_FOR_REVIEW"):
        build_reconstruction_editorial_decision(tmp_path, DATE, "already", decision="APPROVE", reviewer="human", reason="Invalid target.", decided_at=DECIDED)


def test_decision_specific_required_fields_fail_closed(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)

    with pytest.raises(DateReconciliationError, match="APPROVE_WITH_EDIT"):
        _decision(tmp_path, decision="APPROVE_WITH_EDIT")
    with pytest.raises(DateReconciliationError, match="DUPLICATE"):
        _decision(tmp_path, decision="DUPLICATE")
    with pytest.raises(DateReconciliationError, match="ALREADY_PUBLISHED"):
        _decision(tmp_path, decision="ALREADY_PUBLISHED")


def test_candidate_fingerprint_is_exact_immutable_payload_hash(tmp_path: Path) -> None:
    _apply_reconstruction(tmp_path)
    recon = json.loads((tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "reconstruction.json").read_text(encoding="utf-8"))
    finding = recon["findings"][0]
    payload = _decision(tmp_path)

    assert payload["candidate_fingerprint"] == reconstruction_candidate_fingerprint(finding)


def test_review_command_records_private_decision_without_public_or_queue_writes(tmp_path: Path) -> None:
    recon, research = _apply_reconstruction(tmp_path)
    before_recon = recon.read_bytes()
    before_research = research.read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/review_food_line_reconstruction.py",
            "record",
            "--repo-root",
            str(tmp_path),
            "--date",
            DATE,
            "--candidate-id",
            "food-recon-1",
            "--decision",
            "APPROVE_WITH_EDIT",
            "--reviewer",
            "human-editor",
            "--reason",
            "Private item-level approval with edit.",
            "--decided-at",
            DECIDED,
            "--edited-summary",
            "Edited private summary.",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "recorded"
    assert recon.read_bytes() == before_recon
    assert research.read_bytes() == before_research
    assert not (tmp_path / "output/site").exists()
    assert not (tmp_path / "pages").exists()
    assert not (tmp_path / "data/dispatches/food-line/current-review-queue.json").exists()
