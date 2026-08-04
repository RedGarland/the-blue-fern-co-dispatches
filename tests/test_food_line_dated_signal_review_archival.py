from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_approved_proposal import load_approved_proposal, sha256_file


ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = ROOT / "data/dispatches/food-line/review"
JULY28_SNAPSHOT = "data/dispatches/food-line/review/signal-reviews/2026-07-28.json"
JULY31_SNAPSHOT = "data/dispatches/food-line/review/signal-reviews/2026-07-31.json"
EXPECTED_JULY28_PUBLIC_HASHES = {
    "output/site/food-line/editions/2026-07-28/index.html": "e97a4434c0fe30a9b4ae8e5ef6b233a620ab7c32065b319f2b2572b112de2a01",
    "output/site/food-line/editions/2026-07-28/claim_ledger.html": "965ef518a001a0ef9220f176d4019ca6ff976bce537a9f0306b0ec5c6a2ff2a4",
    "output/site/food-line/editions/2026-07-28/source_table.html": "4128ae5d8f6cc7afe3a72ee749cb848f41cf2d0a5123c27b400b59f8f82d86c4",
    "output/site/food-line/editions/2026-07-28/sources_manifest.json": "720354d8f34db469e1be5beed52f9b0d979b03b3bd285a45ebd64ad6ef79a222",
    "output/site/food-line/editions/2026-07-28/curation_manifest.json": "a06c0233eaa26ab4c8b466435348c8fc336fa1463ca2ca754553821613c6eaf9",
    "output/site/food-line/editions/2026-07-28/edition_manifest.json": "b69798a41620099e976653c4456c25548bacc1c9fd3c6611005277ede57bf907",
    "output/dispatches/food-line/editions/2026-07-28/index.html": "e97a4434c0fe30a9b4ae8e5ef6b233a620ab7c32065b319f2b2572b112de2a01",
    "output/dispatches/food-line/editions/2026-07-28/claim_ledger.html": "965ef518a001a0ef9220f176d4019ca6ff976bce537a9f0306b0ec5c6a2ff2a4",
    "output/dispatches/food-line/editions/2026-07-28/source_table.html": "4128ae5d8f6cc7afe3a72ee749cb848f41cf2d0a5123c27b400b59f8f82d86c4",
    "output/dispatches/food-line/editions/2026-07-28/sources_manifest.json": "720354d8f34db469e1be5beed52f9b0d979b03b3bd285a45ebd64ad6ef79a222",
    "output/dispatches/food-line/editions/2026-07-28/curation_manifest.json": "a06c0233eaa26ab4c8b466435348c8fc336fa1463ca2ca754553821613c6eaf9",
    "output/dispatches/food-line/editions/2026-07-28/edition_manifest.json": "b69798a41620099e976653c4456c25548bacc1c9fd3c6611005277ede57bf907",
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _review_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "data/dispatches/food-line/review"
    shutil.copytree(REVIEW_ROOT, target)
    return tmp_path


def test_july28_and_july31_proposals_validate_simultaneously_from_dated_snapshots(tmp_path: Path) -> None:
    root = _review_fixture(tmp_path)

    july28 = load_approved_proposal(root, root / "data/dispatches/food-line/review/proposed-editions/2026-07-28.json", "2026-07-28")
    july31 = load_approved_proposal(root, root / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json", "2026-07-31")

    assert july28.queue_path.relative_to(root).as_posix() == JULY28_SNAPSHOT
    assert july31.queue_path.relative_to(root).as_posix() == JULY31_SNAPSHOT
    assert july28.legacy_current_review_fallback_used is False
    assert july31.legacy_current_review_fallback_used is False


def test_dated_proposals_ignore_singleton_changes_and_missing_snapshot_fails_closed(tmp_path: Path) -> None:
    root = _review_fixture(tmp_path)
    current_queue = root / "data/dispatches/food-line/review/current-signal-review.json"
    july28_snapshot = root / JULY28_SNAPSHOT
    july31_snapshot = root / JULY31_SNAPSHOT

    current_queue.write_bytes(july31_snapshot.read_bytes())
    july28 = load_approved_proposal(root, root / "data/dispatches/food-line/review/proposed-editions/2026-07-28.json", "2026-07-28")
    assert july28.queue_path.relative_to(root).as_posix() == JULY28_SNAPSHOT

    current_queue.write_bytes(july28_snapshot.read_bytes())
    july31 = load_approved_proposal(root, root / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json", "2026-07-31")
    assert july31.queue_path.relative_to(root).as_posix() == JULY31_SNAPSHOT

    july31_snapshot.unlink()
    with pytest.raises(ValueError, match="unable to read review snapshot"):
        load_approved_proposal(root, root / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json", "2026-07-31")


def test_july28_and_july31_release_records_preserve_snapshot_provenance() -> None:
    july28_proposal = _json(ROOT / "data/dispatches/food-line/review/proposed-editions/2026-07-28.json")
    july31_proposal = _json(ROOT / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json")
    july28_release = _json(ROOT / "data/dispatches/food-line/review/releases/2026-07-28.json")
    july31_release = _json(ROOT / "data/dispatches/food-line/review/releases/2026-07-31.json")

    assert july28_proposal["review_snapshot_path"] == JULY28_SNAPSHOT
    assert july28_proposal["review_snapshot_sha256"] == "112cc2b51d4753a9ba3657cddcd95609f6193fdf5f32d8e65180026311bea01b"
    assert july31_proposal["review_snapshot_path"] == JULY31_SNAPSHOT
    assert july31_proposal["review_snapshot_sha256"] == "67372beb029c0ca7b5e993b19c58e0410630d107dec70897e7965329c96d0c4e"

    assert july28_release["review_snapshot_path"] == july28_proposal["review_snapshot_path"]
    assert july28_release["review_snapshot_sha256"] == july28_proposal["review_snapshot_sha256"]
    assert july28_release["approved_proposal_sha256"] == sha256_file(ROOT / "data/dispatches/food-line/review/proposed-editions/2026-07-28.json")
    assert july31_release["review_snapshot_path"] == july31_proposal["review_snapshot_path"]
    assert july31_release["review_snapshot_sha256"] == july31_proposal["review_snapshot_sha256"]
    assert july31_release["approved_proposal_sha256"] == sha256_file(ROOT / "data/dispatches/food-line/review/proposed-editions/2026-07-31.json")


def test_existing_july28_public_artifacts_remain_byte_for_byte_unchanged() -> None:
    for relpath, expected_hash in EXPECTED_JULY28_PUBLIC_HASHES.items():
        assert sha256_file(ROOT / relpath) == expected_hash
