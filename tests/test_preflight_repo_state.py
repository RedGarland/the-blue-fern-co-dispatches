from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import preflight_repo_state

ROOT = Path(__file__).resolve().parents[1]


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def test_classify_path_covers_expected_categories():
    assert preflight_repo_state.classify_path("scripts/preflight_repo_state.py") == "source"
    assert preflight_repo_state.classify_path("tests/test_preflight_repo_state.py") == "tests"
    assert preflight_repo_state.classify_path("docs/project-contract.md") == "docs"
    assert preflight_repo_state.classify_path("output/site/gaza/index.html") == "generated_public_output"
    assert preflight_repo_state.classify_path("output/dispatches/american-pressure/review/report.md") == "review_output"
    assert preflight_repo_state.classify_path("output/tmp-backups-pages/gaza/2026-10-02/index.html") == "review_output"
    assert preflight_repo_state.classify_path("logs/gaza-daily-2026-06-22.log") == "logs"
    assert preflight_repo_state.classify_path(".pytest-temp-gaza-wide/") == "cache"
    assert preflight_repo_state.classify_path(".pytest-tmp-bluefern-layout-deploy/pages/index.html") == "cache"
    assert preflight_repo_state.classify_path("scripts/__pycache__/food_line_daily_scheduler.cpython-313.pyc") == "cache"
    assert preflight_repo_state.classify_path(".venv/Scripts/python.exe") == "virtualenv"
    assert preflight_repo_state.classify_path("status/food-line/runtime/source_performance_history.json") == "local_run_state"
    assert preflight_repo_state.classify_path("status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/latest.json") == "local_run_state"
    assert preflight_repo_state.classify_path("status/operational-health/food-line/2026-09-10/runs/food_line_current_intake-run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/records/story_memory.json") == "shared_dispatch_records"
    assert preflight_repo_state.classify_path("some/unknown/path.txt") == "unknown"


@pytest.mark.parametrize(
    "path",
    [
        "data/private-agent-handoff/inbox/food-line/synthetic-food-handoff-20260910-001.json",
        "data/private-agent-handoff/archive/care-line/2026-09-10/synthetic-care-handoff-20260910-001.json",
        "data/private-agent-handoff/receipts/food-line/2026-09-10/synthetic-food-handoff-20260910-001-attempt-20260911T021459.547492Z-06e00afbe286.json",
        "data/private-agent-handoff/receipts/care-line/unknown-date/unknown-run-attempt-20260911T021437.536828Z-6f4a05d22e47.json",
        "data/private-agent-handoff/retired/food-line/synthetic-food-handoff-20260910-001/active/a-F-synthetic-food-handoff-20260910-001-9c33adea.json",
        "data/private-agent-handoff/retired/care-line/synthetic-care-handoff-20260910-001/active-before-cleanup/a-C-current-review-queue-ab8d48a5.json",
        "data/private-agent-handoff/cleanup/food-line/synthetic-food-handoff-20260910-001-20260911T021713.130201Z.json",
        "data/private-agent-handoff/cleanup/food-line/proof-receipt-retirement-unknown-run-attempt-20260911T021435.418564Z-ded1b18eb6c0.json",
        "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json",
        "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/source-payloads/food-line-source-watch-20260910T110306Z-hawaii-island-pantry-shortages.json",
    ],
)
def test_external_handoff_evidence_is_allowed(path):
    assert preflight_repo_state.classify_path(path) == "local_run_state"


@pytest.mark.parametrize(
    "path",
    [
        "data/private-agent-handoff/receipts/gaza/2026-09-10/receipt.json",
        "data/private-agent-handoff/retired/food-line/real-run/active/evidence.json",
        "data/private-agent-handoff/cleanup/care-line/synthetic-run.json",
        "data/private-agent-handoff/archive/food-line/2026-09-10/../secret.json",
        "data/private-agent-handoff/receipts/food-line/2026-09-10/receipt.txt",
        "data/private-agent-handoff/unrelated.json",
        "data/other-runtime/evidence.json",
    ],
)
def test_unrelated_or_unsafe_handoff_paths_remain_unknown(path):
    assert preflight_repo_state.classify_path(path) == "unknown"


def test_production_shaped_external_handoff_evidence_is_clean_but_nearby_dirt_is_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                "## add/pages-repo-default",
                "?? data/private-agent-handoff/cleanup/food-line/synthetic-food-handoff-20260910-001-20260911T021713.130201Z.json",
                "?? data/private-agent-handoff/cleanup/food-line/proof-receipt-retirement-unknown-run-attempt-20260911T021435.418564Z-ded1b18eb6c0.json",
                "?? data/private-agent-handoff/retired/care-line/synthetic-care-handoff-20260910-001/audit/a-C-receipt.json",
                "?? data/private-agent-handoff/receipts/food-line/unknown-date/unknown-run-attempt-20260911T021437.536828Z-6f4a05d22e47.json",
                "?? data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json",
                "?? data/private-agent-handoff/cleanup/food-line/unrelated.json",
            ],
        ),
    )
    report = preflight_repo_state.build_preflight_report(source_repo)
    assert report["ok"] is False
    assert {
        entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]
    } == {
        "data/private-agent-handoff/cleanup/food-line/synthetic-food-handoff-20260910-001-20260911T021713.130201Z.json",
        "data/private-agent-handoff/cleanup/food-line/proof-receipt-retirement-unknown-run-attempt-20260911T021435.418564Z-ded1b18eb6c0.json",
        "data/private-agent-handoff/retired/care-line/synthetic-care-handoff-20260910-001/audit/a-C-receipt.json",
        "data/private-agent-handoff/receipts/food-line/unknown-date/unknown-run-attempt-20260911T021437.536828Z-6f4a05d22e47.json",
        "data/private-agent-handoff/operator-recovery/food-line/2026-09-10/reconciliation.json",
    }
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/private-agent-handoff/cleanup/food-line/unrelated.json"
    ]



def test_operator_tracked_runtime_state_is_allowed_but_source_changes_remain_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                " M ops/operator/latest.json",
                " M ops/operator/notification-latest.json",
                " M ops/operator/history.jsonl",
                " M ops/operator/incidents/bfo-example.json",
                " M ops/operator/runs/2026-09-26/pr489-production-handoff/scheduler-attempt.json",
                " M ops/operator/remediation/receipts/2026-09-26/proof.json",
                " M ops/operator/engineering/work-item.json",
                "?? ops/operator/runs/2026-09-26/pr489-production-handoff/new-proof.json",
                " M scripts/blue_fern_operator.py",
            ],
        ),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "ops/operator/latest.json",
        "ops/operator/notification-latest.json",
        "ops/operator/history.jsonl",
        "ops/operator/incidents/bfo-example.json",
        "ops/operator/runs/2026-09-26/pr489-production-handoff/scheduler-attempt.json",
        "ops/operator/remediation/receipts/2026-09-26/proof.json",
        "ops/operator/engineering/work-item.json",
        "ops/operator/runs/2026-09-26/pr489-production-handoff/new-proof.json",
    }
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "scripts/blue_fern_operator.py"
    ]


def test_shared_dispatch_records_modified_state_is_allowed_but_unsafe_states_remain_risky(
    monkeypatch, tmp_path
):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                "## add/pages-repo-default",
                " M data/records/curation_decisions.json",
                " M data/records/dispatches.json",
                " M data/records/editions.json",
                " M data/records/records.json",
                " M data/records/sources.json",
                " M data/records/story_memory.json",
                "M  data/records/detail_packages.json",
                " D data/records/records.json",
                " M data/records/unexpected.json",
                "?? data/records/local-export.json",
            ],
        ),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/records/curation_decisions.json",
        "data/records/dispatches.json",
        "data/records/editions.json",
        "data/records/records.json",
        "data/records/sources.json",
        "data/records/story_memory.json",
    }
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/records/detail_packages.json",
        "data/records/records.json",
        "data/records/unexpected.json",
        "data/records/local-export.json",
    ]


@pytest.mark.parametrize("status", ["M ", "MM", " D", "D "])
def test_operator_runtime_staged_or_deleted_state_remains_risky(monkeypatch, tmp_path, status):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [f"{status} ops/operator/notification-latest.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "ops/operator/notification-latest.json"
    ]

def test_food_line_tracked_runtime_state_is_risky_but_sanctioned_runtime_path_is_allowed(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-fix",
            " M data/dispatches/food-line/source_performance_history.json",
            "?? status/food-line/runtime/source_performance_history.json",
            " M data/dispatches/food-line/source_registry.json",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "status/food-line/runtime/source_performance_history.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/source_performance_history.json",
        "data/dispatches/food-line/source_registry.json",
    }


def test_recovery_execution_ledger_path_is_allowed_but_unknown_sibling_is_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                "?? status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/latest.json",
                "?? status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/attempts/01-recovery-abc.json",
                "?? status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/raw-provider-dump.json",
            ],
        ),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/latest.json",
        "status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/attempts/01-recovery-abc.json",
    }
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_source_watch/raw-provider-dump.json"
    ]


def test_food_line_current_review_tracked_state_is_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [" M data/dispatches/food-line/review/current-signal-review.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/dispatches/food-line/review/current-signal-review.json"
    ]


@pytest.mark.parametrize("status", ["M ", "MM", " D", "D "])
def test_food_line_source_performance_history_staged_or_deleted_state_remains_risky(
    monkeypatch, tmp_path, status
):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [f"{status} data/dispatches/food-line/source_performance_history.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/dispatches/food-line/source_performance_history.json"
    ]


@pytest.mark.parametrize("status", ["M ", "MM", " D", "D "])
def test_food_line_current_review_staged_or_deleted_state_remains_risky(monkeypatch, tmp_path, status):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [f"{status} data/dispatches/food-line/review/current-signal-review.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/dispatches/food-line/review/current-signal-review.json"
    ]


def test_food_line_discovery_candidates_path_is_allowed_but_nearby_paths_stay_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-runner-hygiene",
            "?? data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json",
            "?? data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
            "?? data/dispatches/food-line/discovery/foo/discovery_candidates.json",
            "?? data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json",
        "data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
        "data/dispatches/food-line/discovery/foo/discovery_candidates.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
    }


def test_food_line_historical_recovery_paths_are_allowed_but_siblings_stay_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-recovery",
            " M data/dispatches/food-line/coverage-gaps/2026-09-09.json",
            "?? data/dispatches/food-line/date-reconciliation/2026-09-09.json",
            "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/reconstruction.json",
            "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/research-input.json",
            "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/candidates.json",
            "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/decisions/food-recon-20260909-002-lansingburgh-pantry.json",
            "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/notes.json",
            "?? data/dispatches/food-line/historical-reconstruction/not-a-date/reconstruction.json",
            "?? data/dispatches/food-line/date-reconciliation/latest.json",
            "?? data/dispatches/food-line/coverage-gaps/readme.json",
            "?? data/dispatches/food-line/random/file.json",
            " M scripts/doctor.py",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/dispatches/food-line/coverage-gaps/2026-09-09.json",
        "data/dispatches/food-line/date-reconciliation/2026-09-09.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/reconstruction.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/research-input.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/candidates.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/decisions/food-recon-20260909-002-lansingburgh-pantry.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/notes.json",
        "data/dispatches/food-line/historical-reconstruction/not-a-date/reconstruction.json",
        "data/dispatches/food-line/date-reconciliation/latest.json",
        "data/dispatches/food-line/coverage-gaps/readme.json",
        "data/dispatches/food-line/random/file.json",
        "scripts/doctor.py",
    }




def test_operator_runtime_ledger_paths_are_sanctioned_but_operator_config_remains_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                " M ops/operator/latest.json",
                " M ops/operator/history.jsonl",
                " M ops/operator/incidents/bfo-123.json",
                "?? ops/operator/notification-latest.json",
                "?? ops/operator/runs/2026-09-25/operator-run.json",
                " M ops/operator/config.json",
                " M ops/operator/remediation-policy.yaml",
                " M scripts/blue_fern_operator.py",
            ],
        ),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "ops/operator/latest.json",
        "ops/operator/history.jsonl",
        "ops/operator/incidents/bfo-123.json",
        "ops/operator/notification-latest.json",
        "ops/operator/runs/2026-09-25/operator-run.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "ops/operator/config.json",
        "ops/operator/remediation-policy.yaml",
        "scripts/blue_fern_operator.py",
    }


def test_operator_preserved_runtime_evidence_paths_are_sanctioned(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (
            0,
            [
                "?? ops/operator/remediation/receipts/2026-09-26/pr489-incident-preservation.json",
                "?? ops/operator/remediation/receipts/2026-09-26/nested/evidence.json",
                "?? ops/operator/engineering/active/work-item/work-item.json",
                "?? ops/operator/.lock/operator.lock",
                "?? ops/operator/runs/2026-09-26/pr489-production-handoff.json",
                "?? ops/operator/runs/2026-09-26/pr489-production-handoff/scheduler-diagnostics/scheduler-attempt.json",
                "?? ops/operator/runs/2026-09-26/pr489-production-handoff/proofs/operator-sync-plan.json",
                "?? ops/operator/remediation-policy-drafts/policy.json",
                "?? ops/operator/runs/2026-09-26/pr489-production-handoff/../secret.json",
                "?? ops/operator/runs/not-a-date/pr489-production-handoff/evidence.json",
            ],
        ),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "ops/operator/remediation/receipts/2026-09-26/pr489-incident-preservation.json",
        "ops/operator/remediation/receipts/2026-09-26/nested/evidence.json",
        "ops/operator/engineering/active/work-item/work-item.json",
        "ops/operator/.lock/operator.lock",
        "ops/operator/runs/2026-09-26/pr489-production-handoff.json",
        "ops/operator/runs/2026-09-26/pr489-production-handoff/scheduler-diagnostics/scheduler-attempt.json",
        "ops/operator/runs/2026-09-26/pr489-production-handoff/proofs/operator-sync-plan.json",
    }
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "ops/operator/remediation-policy-drafts/policy.json",
        "ops/operator/runs/2026-09-26/pr489-production-handoff/../secret.json",
        "ops/operator/runs/not-a-date/pr489-production-handoff/evidence.json",
    ]


def test_allowed_local_generated_entries_do_not_fail_preflight(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: pages_repo)

    def fake_run_git_status(repo: Path):
        if repo == source_repo:
            return 0, [
                "## add/gaza-wide-source-discovery-audit",
                "?? logs/gaza-daily-2026-06-22.log",
                "?? output/dispatches/american-pressure/review/report.md",
                "?? .pytest-temp-gaza-wide/",
                "?? .venv/Scripts/python.exe",
            ]
        return 0, ["## gh-pages...origin/gh-pages"]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is True
    assert report["source_repo"]["summary"]["risky_entries"] == []
    assert {entry["category"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "logs",
        "review_output",
        "cache",
        "virtualenv",
    }
    assert report["pages_repo_status"] == "clean"


def test_generated_public_output_residue_is_allowed_in_source_checkout_but_not_pages(
    monkeypatch, tmp_path
):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: pages_repo)

    def fake_run_git_status(repo: Path):
        if repo.resolve() == source_repo.resolve():
            return 0, [
                "## add/pages-repo-default",
                " M output/site/assets/site.css",
                " M output/site/gaza/index.html",
                " D output/site/food-line/editions/2026-09-12/index.html",
                "?? output/tmp-backups-pages/gaza/2026-10-02/index.html",
                "?? .pytest-tmp-bluefern-layout-deploy/test_pages_publish/repo/output/site/index.html",
                "?? output/site/gaza/editions/2026-09-20/index.html",
                "?? output/dispatches/gaza/editions/2026-09-20/index.html",
            ]
        return 0, [
            "## gh-pages...origin/gh-pages",
            " M output/site/gaza/index.html",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {
        entry["path"]
        for entry in report["source_repo"]["summary"]["allowed_entries"]
    } == {
        "output/site/assets/site.css",
        "output/site/gaza/index.html",
        "output/site/food-line/editions/2026-09-12/index.html",
        "output/tmp-backups-pages/gaza/2026-10-02/index.html",
        ".pytest-tmp-bluefern-layout-deploy/test_pages_publish/repo/output/site/index.html",
        "output/site/gaza/editions/2026-09-20/index.html",
        "output/dispatches/gaza/editions/2026-09-20/index.html",
    }
    assert report["source_repo"]["summary"]["risky_entries"] == []
    assert [entry["path"] for entry in report["pages_repo"]["summary"]["risky_entries"]] == [
        "output/site/gaza/index.html",
    ]
    assert report["pages_repo_status"] == "dirty"


def test_generated_public_output_residue_only_does_not_fail_source_preflight(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/pages-repo-default",
            " M output/site/assets/site.css",
            " M output/site/gaza/index.html",
            " D output/site/food-line/editions/2026-09-12/index.html",
            " D output/dispatches/food-line/editions/2026-09-12/edition_manifest.json",
            "?? output/tmp-backups-pages/gaza/2026-10-02/index.html",
            "?? .pytest-tmp-bluefern-layout-deploy/test_pages_publish/repo/output/site/index.html",
            "?? output/site/food-line/editions/2026-10-01/index.html",
            "?? output/dispatches/food-line/editions/2026-10-01/index.html",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is True
    assert {
        entry["path"]
        for entry in report["source_repo"]["summary"]["allowed_entries"]
    } == {
        "output/site/assets/site.css",
        "output/site/gaza/index.html",
        "output/site/food-line/editions/2026-09-12/index.html",
        "output/dispatches/food-line/editions/2026-09-12/edition_manifest.json",
        "output/tmp-backups-pages/gaza/2026-10-02/index.html",
        ".pytest-tmp-bluefern-layout-deploy/test_pages_publish/repo/output/site/index.html",
        "output/site/food-line/editions/2026-10-01/index.html",
        "output/dispatches/food-line/editions/2026-10-01/index.html",
    }
    assert report["source_repo"]["summary"]["risky_entries"] == []


def test_staged_generated_public_output_remains_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/pages-repo-default",
            "M  output/site/gaza/index.html",
            "D  output/site/gaza/old.html",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "output/site/gaza/index.html",
        "output/site/gaza/old.html",
    ]


def test_risky_dirty_files_fail_preflight_and_report_pages_repo(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: pages_repo)

    def fake_run_git_status(repo: Path):
        if repo == source_repo:
            return 0, [
                "## add/gaza-wide-source-discovery-audit",
                " M docs/dispatches-project.md",
                "?? src/bluefern_dispatches/gaza_wide_source_audit.py",
                "?? output/site/gaza/index.html",
                "?? tests/test_preflight_repo_state.py",
            ]
        return 0, [
            "## gh-pages...origin/gh-pages",
            "?? output/site/index.html",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)
    rendered = preflight_repo_state.render_report(report)

    assert report["ok"] is False
    assert report["source_repo"]["summary"]["risky_entries"]
    assert report["pages_repo_status"] == "dirty"
    assert "generated_public_output" in rendered
    assert "risky entries" in rendered


def test_main_returns_nonzero_when_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(preflight_repo_state, "_run_git_status", lambda _repo: (0, ["## add/branch", " M docs/project-contract.md"]))

    rc = preflight_repo_state.main(["--source-repo", str(source_repo)])

    assert rc == 1


def test_preflight_script_imports_without_pythonpath_injection(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight_repo_state.py"), "--source-repo", str(tmp_path)],
        cwd=tmp_path,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode in {0, 1}, completed.stdout + completed.stderr
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr
