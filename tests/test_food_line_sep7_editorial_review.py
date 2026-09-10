from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_DATE = "2026-09-07"
HISTORICAL_ROOT = ROOT / "data/dispatches/food-line/historical-intake/2026-09-07"
DISPOSITIONS = HISTORICAL_ROOT / "editorial-dispositions.json"
QUEUE = HISTORICAL_ROOT / "current-signal-review.json"
PROPOSAL = ROOT / "data/dispatches/food-line/review/proposed-editions/2026-09-07.json"
REPORT = ROOT / "data/dispatches/food-line/review/reports/2026-09-07/current-intake.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_sep7_review_dispositions_account_for_all_twenty_items() -> None:
    receipt = _load(DISPOSITIONS)
    queue = _load(QUEUE)
    decisions = receipt["decisions"]

    assert receipt["historical_date"] == HISTORICAL_DATE
    assert receipt["decision_owner"] == "human_editorial_review"
    assert len(decisions) == 20
    assert Counter(item["final_editorial_decision"] for item in decisions) == {
        "approve_with_edit": 10,
        "reject": 10,
    }
    assert receipt["counts"]["pending"] == 0
    assert queue["review_lifecycle"]["terminal_or_handoff"] == 20
    assert queue["review_lifecycle"]["unaccounted"] == 0
    assert len(queue["items"]) == 20


def test_sep7_selected_six_are_reviewed_but_selection_is_not_approval() -> None:
    decisions = _load(DISPOSITIONS)["decisions"]
    selected = [item for item in decisions if item["selected_in_original_proposal"]]

    assert len(selected) == 6
    assert Counter(item["final_editorial_decision"] for item in selected) == {
        "approve_with_edit": 4,
        "reject": 2,
    }
    assert {item["rank"] for item in selected if item["final_editorial_decision"] == "reject"} == {
        101,
        146,
    }


def test_sep7_item_approval_does_not_set_publication_approval() -> None:
    receipt = _load(DISPOSITIONS)
    proposal = _load(PROPOSAL)
    report = _load(REPORT)

    assert receipt["counts"]["selected"] == 10
    assert receipt["counts"]["publication_eligible"] is False
    assert receipt["counts"]["publication_approval"] is False
    assert not any(receipt["publication_side_effects"].values())

    assert proposal["approved_item_count"] == 10
    assert proposal["rejected_item_count"] == 10
    assert proposal["pending_item_count"] == 0
    assert proposal["selected_item_count"] == 10
    assert proposal["publication_eligible"] is False
    assert proposal["publication_approval"] is False
    assert proposal["published"] is False

    assert report["historical_editorial_disposition"]["publication_eligible"] is False
    assert report["historical_editorial_disposition"]["publication_approval"] is False
    assert report["publication_side_effects"] == {
        "audio": False,
        "bluesky": False,
        "maps": False,
        "pages": False,
        "public_output": False,
        "schedule": False,
    }


def test_sep7_foreign_and_duplicate_items_are_rejected() -> None:
    decisions = {item["rank"]: item for item in _load(DISPOSITIONS)["decisions"]}

    assert decisions[146]["decision_reason"] == "OUT_OF_SCOPE_GEOGRAPHY"
    assert decisions[146]["geography"]["location_scope"] == "international"
    assert decisions[178]["decision_reason"] == "OUT_OF_SCOPE_GEOGRAPHY"
    assert decisions[310]["decision_reason"] == "OUT_OF_SCOPE_GEOGRAPHY"
    assert decisions[194]["decision_reason"] == "DUPLICATE_OR_SAME_EVENT"
    assert decisions[208]["decision_reason"] == "DUPLICATE_OR_SAME_EVENT"


def test_sep7_review_preserves_runtime_lineage_without_public_output() -> None:
    receipt = _load(DISPOSITIONS)

    assert receipt["source_watch_run_id"] == (
        "food-line-scheduled-20260907-20260907T123002Z-3e5890bd"
    )
    assert (ROOT / receipt["source_artifact_path"]).exists()
    assert (ROOT / receipt["source_watch_run_state_path"]).exists()
    assert (ROOT / receipt["source_watch_query_plan_path"]).exists()

    assert not (ROOT / "output/site/food-line/editions/2026-09-07").exists()
    assert not (ROOT / "bluefern-dispatches-pages").exists()
