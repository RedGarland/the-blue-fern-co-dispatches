import pytest

from scripts.care_line_runtime_paths import classify_care_line_runtime_path
from scripts.preflight_repo_state import classify_status_line

RECEIPT = "data/dispatches/care-line/source-replays/2026-10-14/20261014-fb5626113b2f-01/nc-health-news/oct14-recovery-a41bf5a6.json"


def test_canonical_untracked_care_replay_receipt_is_runtime_state():
    assert classify_care_line_runtime_path(RECEIPT) == "local_run_state"
    assert classify_status_line(f"?? {RECEIPT}")["is_risky"] is False


@pytest.mark.parametrize("status", [" M", "M ", "MM", " D", "D ", "A ", "AM", "R "])
def test_tracked_care_replay_receipt_changes_stay_blocked(status):
    assert classify_status_line(f"{status} {RECEIPT}")["is_risky"] is True


@pytest.mark.parametrize("path", [
    "data/dispatches/care-line/source-replays/notes.json",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/retry.json",
    "data/dispatches/care-line/source-replays/not-a-date/parent/source/retry.json",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/source/retry.py",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/source/extra/retry.json",
    "data/dispatches/care-line/source-replays/2026-10-14/.hidden/source/retry.json",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/source/.hidden.json",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/source/../retry.json",
    "data/dispatches/care-line/source-replays/2026-10-14/parent/source/retry.json.bak",
    "data/dispatches/care-line/source-replays-old/2026-10-14/parent/source/retry.json",
    "data/dispatches/food-line/source-replays/2026-10-14/parent/source/retry.json",
])
def test_malformed_and_unrelated_replay_paths_stay_blocked(path):
    assert classify_care_line_runtime_path(path) is None
    assert classify_status_line(f"?? {path}")["is_risky"] is True


def test_renamed_care_replay_receipt_stays_blocked():
    assert classify_status_line(f"R  unrelated.json -> {RECEIPT}")["is_risky"] is True
