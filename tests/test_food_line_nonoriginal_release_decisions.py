import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "data" / "dispatches" / "food-line" / "nonoriginal-reconstruction" / "2026-09-01_2026-09-03" / "release-decisions"


def load(date):
    return json.loads((DECISIONS / f"{date}.json").read_text(encoding="utf-8"))


def test_release_decisions_are_governance_blocked_and_not_publication_approval():
    decisions = [load(date) for date in ("2026-09-01", "2026-09-02", "2026-09-03")]

    assert [item["decision"] for item in decisions] == ["BLOCKED_BY_GOVERNANCE"] * 3
    assert sum(len(item["approved_item_ids"]) for item in decisions) == 4
    assert all(item["release_ready"] is False for item in decisions)
    assert all(item["publication_approval"] is False for item in decisions)
    assert all(item["publication_performed"] is False for item in decisions)


def test_release_preserves_nonoriginal_provenance_and_runtime_lineage():
    decisions = [load(date) for date in ("2026-09-01", "2026-09-02", "2026-09-03")]

    assert all(item["provenance"] == "NONORIGINAL_HISTORICAL_RECONSTRUCTION" for item in decisions)
    assert all(item["faithful_replay_status"] == "UNAVAILABLE" for item in decisions)
    assert all(item["original_runtime_status"] == "FAILED / UPSTREAM_BLOCKED" for item in decisions)
    assert all(item["historical_gap_status"] == "OPEN / NONORIGINAL_RECONSTRUCTION_AVAILABLE" for item in decisions)
    assert all(item["reconstruction_disclosure_required"] is True for item in decisions)


def test_release_authority_and_daily_scheduler_isolation_are_false():
    decisions = [load(date) for date in ("2026-09-01", "2026-09-02", "2026-09-03")]

    assert all(item["generation_authorized"] is False for item in decisions)
    assert all(item["publication_authorized"] is False for item in decisions)
    assert all(item["pages_authorized"] is False for item in decisions)
    assert all(item["current_daily_publish_consumable"] is False for item in decisions)


def test_one_item_release_is_not_automatic():
    sep1 = load("2026-09-01")
    sep3 = load("2026-09-03")

    assert len(sep1["approved_item_ids"]) == 1
    assert len(sep3["approved_item_ids"]) == 1
    assert sep1["decision"] == "BLOCKED_BY_GOVERNANCE"
    assert sep3["decision"] == "BLOCKED_BY_GOVERNANCE"
