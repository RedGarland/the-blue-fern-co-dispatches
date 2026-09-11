from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.external_agent_handoff import import_envelope
from scripts.cleanup_external_agent_synthetic_run import cleanup
from test_external_agent_handoff import _envelope, _write


def test_conflict_failure_receipts_are_sanitized_and_auditable(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    _write(source, _envelope(run_id="synthetic-conflict"))
    assert import_envelope(tmp_path, source, dispatch="food-line")[0] == 0
    changed = _envelope(run_id="synthetic-conflict")
    changed["coverage_notes"] = "different attempt"
    _write(source, changed)
    code, result = import_envelope(tmp_path, source, dispatch="food-line")
    assert code == 2
    assert result["status"] == "FAILED"
    assert result["classification"] == "IDEMPOTENCY_CONFLICT"
    receipt_text = (tmp_path / result["receipt_ref"]).read_text(encoding="utf-8")
    assert "exact_supporting_passage" not in receipt_text
    assert str(tmp_path) not in receipt_text
    assert len(list((tmp_path / "data/private-agent-handoff/receipts/food-line/2026-09-10").glob("*attempt*.json"))) == 1


@pytest.mark.parametrize("payload,dispatch", [({}, "food-line"), (_envelope(), "gaza")])
def test_malformed_and_unsupported_attempts_emit_failure_receipts(tmp_path: Path, payload: dict, dispatch: str) -> None:
    source = tmp_path / "bad.json"
    _write(source, payload)
    code, result = import_envelope(tmp_path, source, dispatch=dispatch)
    assert code == 2
    assert result["status"] == "FAILED"
    assert (tmp_path / result["receipt_ref"]).exists()


def test_cleanup_dry_run_is_non_mutating_and_apply_quarantines_food_audit(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    run_id = "synthetic-food-handoff-20260910-001"
    _write(source, _envelope(run_id=run_id))
    assert import_envelope(tmp_path, source, dispatch="food-line")[0] == 0
    dry = cleanup(tmp_path, dispatch="food-line", agent_run_id=run_id, apply=False)
    assert dry["result"] == "DRY_RUN_NO_CHANGES"
    assert (tmp_path / "data/private-agent-handoff/archive/food-line/2026-09-10" / f"{run_id}.json").exists()
    applied = cleanup(tmp_path, dispatch="food-line", agent_run_id=run_id, apply=True)
    assert applied["result"] == "SYNTHETIC_RUN_RETIRED"
    assert not (tmp_path / "data/private-agent-handoff/archive/food-line/2026-09-10" / f"{run_id}.json").exists()
    assert list((tmp_path / "data/private-agent-handoff/retired/food-line" / run_id).rglob(f"*{run_id}*.json"))
    assert Path(tmp_path / applied["cleanup_receipt_ref"]).exists()


def test_cleanup_removes_only_matching_care_candidate_and_preserves_audit(tmp_path: Path) -> None:
    run_id = "synthetic-care-handoff-20260910-001"
    source = tmp_path / "care.json"
    _write(source, _envelope("care-line", run_id))
    assert import_envelope(tmp_path, source, dispatch="care-line")[0] == 0
    before = json.loads((tmp_path / "data/dispatches/care-line/review/candidate-registry.json").read_text(encoding="utf-8"))
    assert any(row.get("collection_run_id") == run_id for row in before["candidates"])
    result = cleanup(tmp_path, dispatch="care-line", agent_run_id=run_id, apply=True)
    after = json.loads((tmp_path / "data/dispatches/care-line/review/candidate-registry.json").read_text(encoding="utf-8"))
    assert all(row.get("collection_run_id") != run_id for row in after["candidates"])
    assert result["audit_artifacts_preserved"]
    assert Path(tmp_path / result["cleanup_receipt_ref"]).exists()


@pytest.mark.parametrize("run_id", ["", "normal-run", "synthetic-*", "synthetic-../escape", "synthetic-other/child"])
def test_cleanup_refuses_unsafe_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        cleanup(tmp_path, dispatch="food-line", agent_run_id=run_id, apply=False)
