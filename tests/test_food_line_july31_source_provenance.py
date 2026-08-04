from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from bluefern_dispatches.food_line_approved_proposal import finalize_public_release_status, sha256_file


ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-07-31"
PROPOSAL_SHA = "777512150a74c705001ab59b3447d8bd7e1a630b3f042eb7713bd6dbbcc77174"
QUEUE_SHA = "67372beb029c0ca7b5e993b19c58e0410630d107dec70897e7965329c96d0c4e"
SOURCE_COMMIT = "af91544bd437970e7d7ab766a8b89038b2bf0274"
EXPECTED_RELEASE_ACTIONS = Counter({"unchanged": 5, "modify": 3})
EXPECTED_SOURCE_HASHES = {
    "output/site/food-line/index.html": "00adfc52425ea68ae285ccc158865443d9523418f678b114c133d518ea558261",
    "output/site/food-line/archive.html": "5c2f2477a166f6e2c4645ddb2d2b637faf0b0b1613e260c2f5e7979302f64f82",
    "output/site/food-line/editions/2026-07-31/index.html": "c8efe183e753652acf88c0e57cf3241b1728ccad8783b7049770e816d0935168",
    "output/site/food-line/editions/2026-07-31/claim_ledger.html": "8e2a5db4b6543e8e3a319cc701b29399cadfcd891ad3c7dcfb09101f18eb173b",
    "output/site/food-line/editions/2026-07-31/source_table.html": "bf487d244161832972590e9c3cfebeffc83fc42b929c5d654c47671227d957f1",
    "output/site/food-line/editions/2026-07-31/sources_manifest.json": "148ad8138f363e52756ca2691ef3a47f28e0369964b97cf96673994639008cd0",
    "output/site/food-line/editions/2026-07-31/curation_manifest.json": "65817913211f248f80efa7a882f5b4b5d35cf4a06c0ec4b0d8980edbaf18671e",
    "output/site/food-line/editions/2026-07-31/edition_manifest.json": "c34cf49750da516ea4d8c7d7a484d27f37d31b63382130993353583ab7a26235",
    "output/dispatches/food-line/editions/2026-07-31/index.html": "c8efe183e753652acf88c0e57cf3241b1728ccad8783b7049770e816d0935168",
    "output/dispatches/food-line/editions/2026-07-31/claim_ledger.html": "8e2a5db4b6543e8e3a319cc701b29399cadfcd891ad3c7dcfb09101f18eb173b",
    "output/dispatches/food-line/editions/2026-07-31/source_table.html": "bf487d244161832972590e9c3cfebeffc83fc42b929c5d654c47671227d957f1",
    "output/dispatches/food-line/editions/2026-07-31/sources_manifest.json": "148ad8138f363e52756ca2691ef3a47f28e0369964b97cf96673994639008cd0",
    "output/dispatches/food-line/editions/2026-07-31/curation_manifest.json": "65817913211f248f80efa7a882f5b4b5d35cf4a06c0ec4b0d8980edbaf18671e",
    "output/dispatches/food-line/editions/2026-07-31/edition_manifest.json": "c34cf49750da516ea4d8c7d7a484d27f37d31b63382130993353583ab7a26235",
}
EXPECTED_PAGES_SHA_BEFORE = {
    "food-line/archive.html": "a2567a0411d96dfa8938833da02ebcbcd44fbef4d639e424bebb19795bce6510",
    "food-line/editions/2026-07-31/claim_ledger.html": "8e2a5db4b6543e8e3a319cc701b29399cadfcd891ad3c7dcfb09101f18eb173b",
    "food-line/editions/2026-07-31/curation_manifest.json": "65817913211f248f80efa7a882f5b4b5d35cf4a06c0ec4b0d8980edbaf18671e",
    "food-line/editions/2026-07-31/edition_manifest.json": "8021b0c2d11bdd32384323ccb3cadb11190ef6abaeb70c4829688d1acecee081",
    "food-line/editions/2026-07-31/index.html": "c8efe183e753652acf88c0e57cf3241b1728ccad8783b7049770e816d0935168",
    "food-line/editions/2026-07-31/source_table.html": "bf487d244161832972590e9c3cfebeffc83fc42b929c5d654c47671227d957f1",
    "food-line/editions/2026-07-31/sources_manifest.json": "148ad8138f363e52756ca2691ef3a47f28e0369964b97cf96673994639008cd0",
    "food-line/index.html": "7a5144a49bf2b7b359ce6c01be5f5046d80491a88c841f31888298734eb73bb4",
}


def _json(relpath: str) -> dict:
    return json.loads((ROOT / relpath).read_text(encoding="utf-8"))


def test_july31_recovered_release_manifest_matches_recovered_source_files() -> None:
    proposal_path = ROOT / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json"
    queue_path = ROOT / "data/dispatches/food-line/review/current-signal-review.json"
    release_path = ROOT / "data/dispatches/food-line/review/releases/2026-07-31.json"

    assert sha256_file(proposal_path) == PROPOSAL_SHA
    assert sha256_file(queue_path) == QUEUE_SHA

    for relpath, expected_hash in EXPECTED_SOURCE_HASHES.items():
        assert sha256_file(ROOT / relpath) == expected_hash

    release = _json("data/dispatches/food-line/review/releases/2026-07-31.json")
    assert release["schema_version"] == "food_line_release_manifest_v1"
    assert release["dispatch"] == "food-line"
    assert release["edition_date"] == DATE
    assert release["source_commit"] == SOURCE_COMMIT
    assert release["deletions"] == []
    assert release["shared_files"] == []
    assert Counter(entry["action"] for entry in release["entries"]) == EXPECTED_RELEASE_ACTIONS

    entries = {entry["pages_path"]: entry for entry in release["entries"]}
    assert set(entries) == set(EXPECTED_PAGES_SHA_BEFORE)
    for pages_path, expected_pages_hash in EXPECTED_PAGES_SHA_BEFORE.items():
        entry = entries[pages_path]
        assert entry["pages_sha256_before"] == expected_pages_hash
        assert sha256_file(ROOT / entry["source_path"]) == entry["source_sha256"]

    assert release_path.exists()


def test_july31_recovered_source_manifest_finalizes_to_live_release_state() -> None:
    proposal = _json("data/dispatches/food-line/review/proposed-editions/2026-07-31.json")
    queue = _json("data/dispatches/food-line/review/current-signal-review.json")
    source_manifest = _json("output/site/food-line/editions/2026-07-31/edition_manifest.json")
    dispatch_manifest = _json("output/dispatches/food-line/editions/2026-07-31/edition_manifest.json")

    assert source_manifest == dispatch_manifest
    assert source_manifest["approved_proposal_sha256"] == PROPOSAL_SHA
    assert source_manifest["review_queue_sha256"] == QUEUE_SHA
    assert source_manifest["generator_source_commit"] == SOURCE_COMMIT
    assert source_manifest["public_release_status"] == "not_published"
    assert source_manifest["pages_release_status"] == "not_synced"
    assert source_manifest["public_signal_count"] == 1
    assert source_manifest["source_count"] == 1
    assert source_manifest["claim_count"] == 1
    assert source_manifest["edition_mode"] == "current_update"
    assert source_manifest["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-07-31/"
    assert source_manifest["approved_item_provenance"][0]["review_item_id"] == "food-line-current-152a4cc873903b41"
    assert source_manifest["approved_item_provenance"][0]["finding_id"] == "finding_48b492255edda92746442df1"

    finalized = json.loads(json.dumps(source_manifest))
    assert finalize_public_release_status(finalized) is True
    assert finalized["public_release_status"] == "published"
    assert finalized["pages_release_status"] == "synced"
    assert finalized["publication_status"] == "unpublished"
    assert finalized["pages_status"] == "not_synced"
    assert finalized["approved_proposal_sha256"] == PROPOSAL_SHA
    assert finalized["review_queue_sha256"] == QUEUE_SHA
    assert finalized["lead_source_canonical_url"] == "https://www.northernnewsnow.com/2026/07/28/superior-food-pantry-closing-after-more-than-30-years"

    assert proposal["source_queue_sha256"] == QUEUE_SHA
    assert queue["queue_id"] == "food-line-current-review-2026-07-31"
