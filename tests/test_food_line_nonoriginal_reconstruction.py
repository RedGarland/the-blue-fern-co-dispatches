import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECONSTRUCTION = (
    ROOT
    / "data"
    / "dispatches"
    / "food-line"
    / "nonoriginal-reconstruction"
    / "2026-09-01_2026-09-03"
)


def _load(name):
    return json.loads((RECONSTRUCTION / name).read_text(encoding="utf-8"))


def test_reconstruction_is_not_original_replay_and_preserves_failure_lineage():
    candidates = _load("reconstruction-candidates.json")
    receipt = _load("reconstruction-receipt.json")

    assert candidates["provenance_classification"] == "NONORIGINAL_HISTORICAL_RECONSTRUCTION"
    assert receipt["faithful_replay_status"] == "UNAVAILABLE"
    assert all(value == "FAILED / UPSTREAM_BLOCKED" for value in receipt["original_runtime_status"].values())


def test_reconstruction_has_separate_namespace_and_no_publication_approval():
    receipt = _load("reconstruction-receipt.json")

    assert "nonoriginal-reconstruction" in str(RECONSTRUCTION)
    assert receipt["normal_review_queue_modified"] is False
    assert receipt["publication_approval"] is False
    assert receipt["public_output_written"] is False


def test_historical_dates_are_bounded_and_retrieval_is_distinct():
    candidates = _load("reconstruction-candidates.json")

    assert {item["historical_date"] for item in candidates["candidates"]} <= {
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
    }
    assert all(item["retrieved_at"].startswith("2026-09-10") for item in candidates["candidates"])
    assert all(item["source_published_at"][:10] in {"2026-09-01", "2026-09-02", "2026-09-03"} for item in candidates["candidates"])


def test_foreign_geography_and_duplicates_are_excluded():
    candidates = _load("reconstruction-candidates.json")

    assert all("United States" in item["geography"] or any(state in item["geography"] for state in ("Illinois", "North Carolina", "Hawaii", "Texas")) for item in candidates["candidates"])
    assert all(item["duplicate_status"] == "NEW_RECONSTRUCTED_EVENT" for item in candidates["candidates"])
    assert all(item["recovery_archive_match"] == "NO_MATCHING_RETAINED_LEAD" for item in candidates["candidates"])


def test_archive_linkage_does_not_change_provenance_and_operations_remain_untouched():
    receipt = _load("reconstruction-receipt.json")

    assert receipt["recovery_archive_contribution"]["context_only"] is True
    assert receipt["recovery_archive_contribution"]["archive_is_original_sep1_3_source_watch_output"] is False
    assert receipt["recovery_archive_contribution"]["exact_matches"] == 0
    assert receipt["pages_modified"] is False
    assert receipt["scheduler_modified"] is False
    assert receipt["production_runner_modified"] is False
