import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_DATE = "2026-09-08"
QUEUE_PATH = (
    ROOT
    / "data/dispatches/food-line/historical-intake/2026-09-08/current-signal-review.json"
)
PROPOSAL_PATH = (
    ROOT / "data/dispatches/food-line/review/proposed-editions/2026-09-08.json"
)
REPORT_PATH = (
    ROOT / "data/dispatches/food-line/review/reports/2026-09-08/current-intake.json"
)
DISPOSITION_PATH = (
    ROOT
    / "data/dispatches/food-line/historical-intake/2026-09-08/editorial-dispositions.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_items_by_id() -> dict[str, dict]:
    queue = _load(QUEUE_PATH)
    return {item["candidate_id"]: item for item in queue["items"]}


def test_sep8_historical_dispositions_account_for_all_three_items() -> None:
    queue = _load(QUEUE_PATH)
    dispositions = _load(DISPOSITION_PATH)

    assert queue["edition_date"] == HISTORICAL_DATE
    assert len(queue["items"]) == 3
    assert queue["review_lifecycle"]["terminal_or_handoff"] == 3
    assert queue["review_lifecycle"]["unaccounted"] == 0
    assert queue["review_lifecycle"]["editorial_dispositions"] == {
        "approve_with_edit": 1,
        "reject": 2,
    }

    assert dispositions["historical_date"] == HISTORICAL_DATE
    assert dispositions["decision_owner"] == "human_editorial_review"
    assert dispositions["publication_side_effects"]
    assert not any(dispositions["publication_side_effects"].values())
    assert dispositions["counts"]["approved"] == 1
    assert dispositions["counts"]["rejected"] == 2
    assert dispositions["counts"]["pending"] == 0
    assert dispositions["counts"]["selected"] == 1
    assert dispositions["counts"]["publication_eligible"] is False
    assert dispositions["counts"]["publication_approval"] is False
    assert len(dispositions["decisions"]) == 3


def test_sep8_foreign_items_are_rejected_and_not_publication_eligible() -> None:
    items = _queue_items_by_id()

    telesur = items["finding_dcfd8c4c3725a9cb069ec574"]
    assert telesur["editorial_status"] == "reject"
    assert telesur["editorial_decision_reason"] == "OUT_OF_SCOPE_GEOGRAPHY"
    assert telesur["location_name"] == "Latin America and the Caribbean"
    assert telesur["location_scope"] == "international"
    assert telesur["state"] == ""
    assert telesur["publication_eligible"] is False

    unb = items["finding_020d46faa53691a9b37ecd79"]
    assert unb["editorial_status"] == "reject"
    assert unb["editorial_decision_reason"] == "OUT_OF_SCOPE_GEOGRAPHY"
    assert unb["location_name"] == "Bangladesh"
    assert unb["location_scope"] == "international"
    assert unb["state"] == ""
    assert unb["publication_eligible"] is False


def test_sep8_daily_tar_heel_is_item_approved_with_restrained_edit_only() -> None:
    daily = _queue_items_by_id()["finding_1c3cb30a0ce9abe71d9097bf"]

    assert daily["editorial_status"] == "approve_with_edit"
    assert daily["editorial_decision_reason"] == "IN_SCOPE_FOOD_ACCESS_PRESSURE"
    assert daily["decision_owner"] == "human_editorial_review"
    assert daily["state"] == "NC"
    assert daily["location_name"] == "North Carolina"
    assert daily["location_scope"] == "state"
    assert daily["publication_eligible"] is False
    assert daily["selected_for_publication_consideration"] is True
    assert (
        daily["approved_public_summary"]
        == "Recent SNAP eligibility restrictions and cuts could cause thousands of North Carolina students to lose automatic eligibility for free school meals, according to The Daily Tar Heel."
    )
    assert "benefit delay" not in daily["approved_public_summary"].lower()


def test_sep8_proposed_edition_is_not_release_approved_or_publication_ready() -> None:
    proposal = _load(PROPOSAL_PATH)
    report = _load(REPORT_PATH)

    assert proposal["edition_date"] == HISTORICAL_DATE
    assert proposal["publication_eligible"] is False
    assert proposal["publication_approval"] is False
    assert proposal["selected_item_count"] == 1
    assert proposal["approved_item_count"] == 1
    assert proposal["rejected_item_count"] == 2
    assert proposal["pending_item_count"] == 0
    assert len(proposal["items"]) == 1
    assert proposal["items"][0]["state"] == "NC"
    assert proposal["items"][0]["summary"] == (
        "Recent SNAP eligibility restrictions and cuts could cause thousands of North Carolina students to lose automatic eligibility for free school meals, according to The Daily Tar Heel."
    )

    assert report["publication_side_effects"]
    assert not any(report["publication_side_effects"].values())
    assert report["historical_editorial_disposition"]["publication_eligible"] is False
    assert report["historical_editorial_disposition"]["publication_approval"] is False
