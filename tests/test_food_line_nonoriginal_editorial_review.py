import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "data" / "dispatches" / "food-line" / "nonoriginal-reconstruction" / "2026-09-01_2026-09-03"


def load(name):
    return json.loads((REVIEW / name).read_text(encoding="utf-8"))


def test_exactly_four_reconstructed_candidates_are_reviewed():
    review = load("editorial-review.json")
    dispositions = load("editorial-dispositions.json")

    assert review["reviewed_count"] == 4
    assert len(review["items"]) == 4
    assert dispositions["reviewed_count"] == 4
    assert len(dispositions["items"]) == 4


def test_nonoriginal_provenance_and_replay_boundary_are_preserved():
    review = load("editorial-review.json")

    assert review["provenance"] == "NONORIGINAL_HISTORICAL_RECONSTRUCTION"
    assert review["faithful_replay_status"] == "UNAVAILABLE"
    assert review["original_runtime_failure_lineage"] == "UNCHANGED"
    assert review["reconstructed_at"].startswith("2026-09-10")
    assert review["editorial_decided_at"].startswith("2026-09-10")
    assert all(item["retrieved_at"].startswith("2026-09-10") for item in review["items"])
    assert all(item["historical_date_assignment_basis"] for item in review["items"])
    assert all(item["retrieved_at"] != item["source_published_at"] for item in review["items"])


def test_item_approval_is_distinct_from_publication_approval():
    review = load("editorial-review.json")
    dispositions = load("editorial-dispositions.json")

    assert all(item["decision"] == "APPROVE_WITH_EDIT" for item in review["items"])
    assert review["publication_approval"] is False
    assert dispositions["publication_state"]["publication_approval"] is False
    assert dispositions["publication_state"]["published"] is False


def test_normal_queue_and_original_lineage_are_unchanged():
    review = load("editorial-review.json")
    dispositions = load("editorial-dispositions.json")

    assert review["normal_review_queue_modified"] is False
    assert all(value == "FAILED / UPSTREAM_BLOCKED" for value in dispositions["original_runtime_status"].values())


def test_duplicate_handling_and_historical_dates_are_bounded():
    review = load("editorial-review.json")

    assert {item["historical_date"] for item in review["items"]} == {"2026-09-01", "2026-09-02", "2026-09-03"}
    assert all(item["duplicate_status"] == "NEW_RECONSTRUCTED_EVENT" for item in review["items"])
    assert all(item["recovery_archive_match"] == "NO_MATCHING_RETAINED_LEAD" for item in review["items"])


def test_publication_and_pages_remain_untouched():
    dispositions = load("editorial-dispositions.json")

    assert dispositions["publication_state"]["publication_eligible"] is False
    assert dispositions["pages_unchanged"] is True
    assert dispositions["archive_unchanged"] is True
    assert dispositions["rss_unchanged"] is True
    assert dispositions["homepage_unchanged"] is True
    assert dispositions["audio_social_output_created"] is False
    assert dispositions["release_approval_created"] is False
