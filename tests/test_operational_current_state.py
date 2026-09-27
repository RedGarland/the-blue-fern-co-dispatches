import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_STATE = ROOT / "ops" / "current-state.json"


def _load_current_state() -> dict[str, object]:
    return json.loads(CURRENT_STATE.read_text(encoding="utf-8"))


def test_current_state_has_resume_policy_instead_of_authoritative_head() -> None:
    state = _load_current_state()
    protected_source = state["protected_source"]
    next_action = state["next_action"]

    assert protected_source["branch"] == "add/pages-repo-default"
    assert "head" not in protected_source
    assert "observed_head" in protected_source
    assert "re-read" in protected_source["resume_policy"]
    assert "re-read" in next_action["target_head_policy"]
    assert "current protected head" in next_action["summary"]


def test_current_state_preserves_operator_blocker_and_no_go_boundaries() -> None:
    state = _load_current_state()
    runner_state = state["runner_state"]
    next_action = state["next_action"]
    known_blockers = state["known_blockers"]

    assert runner_state["operator"]["status"] == "blocked_unsynced_after_pr498"
    assert any(blocker["id"] == "operator-runner-unsynced-after-pr498" for blocker in known_blockers)
    assert next_action["status"] == "awaiting_windows_guarded_sync_rerun_after_pr498"
    assert runner_state["food"]["status"] == "scheduler_proven_at_pr491_behind_protected_source"
    assert runner_state["care"]["status"] == "runner_synced_at_pr491_behind_protected_source"
    assert runner_state["gaza"]["status"] == "runner_synced_at_pr491_behind_protected_source"
    assert runner_state["ice"]["status"] == "runner_synced_at_pr491_behind_protected_source"
    assert set(next_action["must_not_trigger"]) >= {
        "collection",
        "publication",
        "Pages sync or push",
        "Cascadia activation",
        "scheduled task definition mutation",
        "evidence deletion",
    }


def test_current_state_records_status_export_as_non_public_proof() -> None:
    state = _load_current_state()
    proof = state["proofs"]["operational_status_export"]

    assert proof["highest_proven_layer"] == "SCHEDULER_PROVEN_AND_STATUS_HANDOFF_PROVEN"
    assert proof["last_task_result"] == 0
    assert proof["public_side_effects"] is False
    assert proof["wrapper_receipt"].endswith(".json")
    assert proof["exporter_receipt"].endswith(".json")
