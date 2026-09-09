from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import food_line_daily_scheduler as scheduler
from scripts import process_food_line_current_intake as current_intake_compat
from scripts import run_food_line_discovery_expansion as discovery_compat


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "scripts" / "food_line_daily_scheduler.py"
SOURCE_WATCH_WRAPPER = ROOT / "scripts" / "windows" / "run_food_line_daily_current.ps1"
SOURCE_WATCH_RESUME_WRAPPER = ROOT / "scripts" / "windows" / "resume_food_line_daily_current.ps1"
CURRENT_INTAKE_WRAPPER = ROOT / "scripts" / "windows" / "run_food_line_current_intake.ps1"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def test_food_line_daily_scheduler_imports_without_pythonpath_injection(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import scripts.food_line_daily_scheduler as m; print('IMPORT_OK')"],
        cwd=ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "IMPORT_OK" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr


def test_food_line_windows_wrappers_force_utf8_child_io() -> None:
    for wrapper in (SOURCE_WATCH_WRAPPER, SOURCE_WATCH_RESUME_WRAPPER, CURRENT_INTAKE_WRAPPER):
        text = wrapper.read_text(encoding="utf-8")
        assert "$env:PYTHONIOENCODING = \"utf-8\"" in text
        assert "[Console]::OutputEncoding = $utf8" in text


def test_food_line_daily_scheduler_help_executes_from_other_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCHEDULER), "--help"],
        cwd=tmp_path,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    combined = completed.stdout + completed.stderr
    assert "usage:" in combined.lower()
    assert "ModuleNotFoundError" not in combined


def test_food_line_daily_scheduler_defaults_to_production_branch() -> None:
    parser = scheduler.build_parser()
    args = parser.parse_args(
        [
            "source-watch",
            "--repo-root",
            ".",
            "--python",
            "python",
            "--edition-date",
            "2026-08-17",
            "--run-id",
            "run-1",
        ]
    )

    assert args.branch == "add/pages-repo-default"


def test_scheduler_accepts_successful_source_watch_runtime_state_for_the_next_run() -> None:
    status = "\n".join(
        [
            " M data/dispatches/food-line/source_performance_history.json",
            "?? status/food-line/runs/2026-09-04.json",
            "?? data/dispatches/food-line/discovery-runs/2026-09-04/run-1/run-state.json",
            "?? logs/food-line/source-watch/2026-09-04/receipt.json",
        ]
    )

    assert scheduler._unexpected_dirty_paths(status) == []


def test_scheduler_accepts_current_intake_review_state_for_the_next_run() -> None:
    status = "\n".join(
        [
            " M data/dispatches/food-line/review/current-signal-review.json",
            "?? data/dispatches/food-line/review/proposed-editions/2026-09-04.json",
            "?? data/dispatches/food-line/review/reports/2026-09-04/current-intake.json",
            "?? logs/food-line/current-intake/2026-09-04/receipt.json",
        ]
    )

    assert scheduler._unexpected_dirty_paths(status) == []


def test_checkout_validation_preserves_durable_runtime_evidence(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", scheduler.PRODUCTION_BRANCH], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    history = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    history.parent.mkdir(parents=True)
    history.write_text('{"runs_seen": 1}\n', encoding="utf-8")
    subprocess.run(["git", "add", "--", str(history.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    history.write_text('{"runs_seen": 2}\n', encoding="utf-8")
    run_state = tmp_path / "status" / "food-line" / "runs" / "2026-09-04.json"
    receipt = tmp_path / "logs" / "food-line" / "source-watch" / "2026-09-04" / "receipt.json"
    run_state.parent.mkdir(parents=True)
    receipt.parent.mkdir(parents=True)
    run_state.write_text('{"status": "completed"}\n', encoding="utf-8")
    receipt.write_text('{"exit_code": 0}\n', encoding="utf-8")

    head = scheduler.verify_checkout(tmp_path, scheduler.PRODUCTION_BRANCH, update=False)

    assert head == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert history.read_text(encoding="utf-8") == '{"runs_seen": 2}\n'
    assert run_state.read_text(encoding="utf-8") == '{"status": "completed"}\n'
    assert receipt.read_text(encoding="utf-8") == '{"exit_code": 0}\n'


@pytest.mark.parametrize(
    "status_line",
    [
        "M  data/dispatches/food-line/source_performance_history.json",
        "MM data/dispatches/food-line/source_performance_history.json",
        " D data/dispatches/food-line/source_performance_history.json",
        " M data/dispatches/food-line/source_registry.json",
        "M  data/dispatches/food-line/review/current-signal-review.json",
        "MM data/dispatches/food-line/review/current-signal-review.json",
        " D data/dispatches/food-line/review/current-signal-review.json",
        "?? data/dispatches/food-line/unrecognized-runtime.json",
    ],
)
def test_scheduler_tracked_or_unrecognized_drift_still_fails_closed(status_line: str) -> None:
    assert scheduler._unexpected_dirty_paths(status_line)


def test_legacy_discovery_wrapper_writes_run_state_and_query_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "dispatches" / "food-line").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_core(root: Path, edition_date: str, **kwargs: object) -> dict[str, object]:
        assert root == tmp_path
        assert edition_date == "2026-08-17"
        return {
            "ok": True,
            "candidate_count": 5,
            "public_eligible_candidate_count": 1,
            "rejected_news_count": 0,
            "fetch_failure_count_by_type": {},
            "direct_source_count": 5,
        }

    def fake_plan(root: Path, edition_date: str) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-17"
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    monkeypatch.setattr(discovery_compat, "run_food_line_discovery_expansion", fake_core)
    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-17",
            "--run-id",
            "food-line-scheduled-test",
            "--profile",
            "daily-current",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 0
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-17" / "food-line-scheduled-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "query-plan.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == scheduler.RUN_STATE_SCHEMA
    assert state["status"] == "completed"
    assert state["queries_total"] == 1
    assert state["candidates_discovered"] == 5
    assert state["agent_export"]["status"] == "success"
    exported = Path(state["agent_export"]["path"])
    assert exported.exists()
    exported_payload = json.loads(exported.read_text(encoding="utf-8"))
    assert exported_payload["agent_name"] == "Food Line Source Watch"
    assert exported_payload["agent_run_id"] == "food-line-scheduled-test"
    assert exported_payload["findings"] == []
    assert plan["schema_version"] == "food_line_bounded_query_plan_v1"
    assert plan["query_count"] == 1


def _valid_current_queue_item(date: str) -> dict[str, object]:
    source_date = "2026-08-16" if date == "2026-08-17" else date
    freshness_age_days = 1 if source_date != date else 0
    return {
        "review_item_id": "food-line-current-001",
        "source_finding_or_intake_id": "finding-current-001",
        "source_artifact_path": f"data/dispatches/food-line/agent-intake/{date}/run.json",
        "source_url": "https://example.org/current-food-pressure",
        "canonical_source_url": "https://example.org/current-food-pressure",
        "publisher": "Example News",
        "source_published_at": f"{source_date}T08:00:00-07:00",
        "title": "Pantry closes after supply loss",
        "exact_supporting_passage": "The pantry closed Friday after losing its remaining food supply.",
        "proposed_public_headline": "Local pantry closes after supply loss",
        "proposed_public_summary": "Example News reports that a local pantry closed after losing its food supply.",
        "location_name": "Example City",
        "state": "CA",
        "location_scope": "city",
        "pressure_type": "service_closure",
        "affected_groups": ["pantry clients"],
        "why_it_matters": "A food-access point is no longer operating.",
        "evidence_level": "direct_reporting",
        "confidence": "high",
        "uncertainty_note": "The duration of the closure is not yet known.",
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "freshness_check": {"status": "current", "age_days": freshness_age_days, "edition_date": date},
        "proposed_section": "Core Food Pressure Signals",
        "proposed_rank": 1,
        "editorial_status": "approve",
        "editorial_note": "Approved for draft assembly only.",
        "publication_eligible": False,
    }


def test_legacy_current_intake_wrapper_writes_report_and_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    review_root = tmp_path / "data" / "dispatches" / "food-line" / "review"
    queue_path = review_root / "current-signal-review.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue = {
        "schema_version": "food_line_current_signal_review_v1",
        "queue_id": "food-line-current-review-2026-08-17",
        "edition_date": "2026-08-17",
        "production_scope": "current_nonhistorical_only",
        "historical_roots_excluded": ["data/agent-history", "data/agent-history-staging"],
        "allowed_decisions": ["approve", "approve_with_edit", "hold", "reject"],
        "items": [_valid_current_queue_item("2026-08-17")],
    }
    queue_path.write_text(json.dumps(queue, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    (inbox / "2026-08-17").mkdir(parents=True, exist_ok=True)
    (inbox / "2026-08-17" / "finding.json").write_text("{}", encoding="utf-8")

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-17",
            "--inbox",
            str(inbox),
        ]
    )

    assert code == 0
    report = json.loads(
        (review_root / "reports" / "2026-08-17" / "current-intake.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == "food_line_current_intake_report_v1"
    assert report["status"] == "success"
    assert report["errors"] == []
    assert report["queue"]["item_count"] == 1
    assert report["proposal"]["draft_status"] == "draft_approved_pending_publication"
    assert Path(report["proposal"]["markdown_path"]).exists()


def test_legacy_current_intake_wrapper_builds_queue_from_inbox_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    export_path = inbox / "food-line-source-watch-2026-08-17-food-line-scheduled-test.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_source_watch_agent_export_v1",
                "agent_name": "Food Line Source Watch",
                "agent_run_id": "food-line-scheduled-test",
                "edition_date": "2026-08-17",
                "findings": [{"title": "placeholder finding"}],
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    discovery_item = _valid_current_queue_item("2026-08-17")
    discovery_item["candidate_id"] = "food-line-current-001"
    discovery_item["agent_finding_id"] = "finding-current-001"
    discovery_item.pop("source_artifact_path", None)
    discovery_item.pop("review_item_id", None)
    discovery_item.pop("source_finding_or_intake_id", None)
    discovery_item.pop("duplicate_check", None)
    discovery_item.pop("freshness_check", None)
    discovery_item.pop("proposed_section", None)
    discovery_item.pop("proposed_rank", None)
    discovery_item.pop("editorial_status", None)
    discovery_item.pop("editorial_note", None)

    monkeypatch.setattr(
        current_intake_compat,
        "adapt_food_line_agent_output",
        lambda payload, *, agent_name, agent_run_id: [object()],
    )
    monkeypatch.setattr(
        current_intake_compat,
        "map_finding_to_food_line_candidate",
        lambda finding, *, edition_date: dict(discovery_item),
    )

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-17",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    queue_path = tmp_path / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    assert queue_path.exists()
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert queue["schema_version"] == "food_line_current_signal_review_v1"
    assert len(queue["items"]) == 1
    item = queue["items"][0]
    assert item["review_item_id"] == "food-line-current-001"
    assert item["source_finding_or_intake_id"] == "finding-current-001"
    assert item["source_artifact_path"].startswith("data/dispatches/food-line/agent-intake/2026-08-17/")
    assert item["duplicate_check"]["status"] == "not_published"
    assert item["freshness_check"]["status"] == "current"
    assert item["editorial_status"] == "pending_editorial_review"
    report = json.loads(
        (tmp_path / "data" / "dispatches" / "food-line" / "review" / "reports" / "2026-08-17" / "current-intake.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == "food_line_current_intake_report_v1"
    assert report["status"] == "success"
    assert report["queue"]["item_count"] == 1
    assert report["proposal"]["draft_status"] == "draft_pending_editorial_review"
    assert report["selected_input_count"] == 1
    assert report["selected_inputs"][0]["path"] == "data/dispatches/food-line/agent-inbox/food-line-source-watch-2026-08-17-food-line-scheduled-test.json"


def test_current_intake_uses_only_current_dated_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for edition_date, title in (
        ("2026-08-16", "older"),
        ("2026-08-17", "current"),
    ):
        (inbox / f"food-line-source-watch-{edition_date}.json").write_text(
            json.dumps(
                {
                    "schema_version": "food_line_source_watch_agent_export_v1",
                    "agent_name": "Food Line Source Watch",
                    "agent_run_id": f"run-{edition_date}",
                    "edition_date": edition_date,
                    "findings": [{"title": title}],
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    consumed_titles: list[str] = []
    discovery_item = _valid_current_queue_item("2026-08-17")
    discovery_item["candidate_id"] = "food-line-current-001"
    discovery_item["agent_finding_id"] = "finding-current-001"

    def fake_adapt(payload: dict[str, object], *, agent_name: str, agent_run_id: str) -> list[dict[str, object]]:
        title = str(payload["findings"][0]["title"])
        consumed_titles.append(title)
        return [{"title": title}]

    monkeypatch.setattr(current_intake_compat, "adapt_food_line_agent_output", fake_adapt)
    monkeypatch.setattr(
        current_intake_compat,
        "map_finding_to_food_line_candidate",
        lambda finding, *, edition_date: dict(discovery_item),
    )

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-17",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    assert consumed_titles == ["current"]
    assert (inbox / "food-line-source-watch-2026-08-16.json").exists()
    report = json.loads(
        (tmp_path / "data" / "dispatches" / "food-line" / "review" / "reports" / "2026-08-17" / "current-intake.json").read_text(encoding="utf-8")
    )
    assert report["selected_input_count"] == 1
    assert report["selected_inputs"][0]["path"].endswith("food-line-source-watch-2026-08-17.json")


def test_current_intake_multiple_current_handoffs_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for suffix in ("b", "a"):
        (inbox / f"food-line-source-watch-2026-08-17-{suffix}.json").write_text(
            json.dumps(
                {
                    "schema_version": "food_line_source_watch_agent_export_v1",
                    "agent_name": "Food Line Source Watch",
                    "agent_run_id": f"run-{suffix}",
                    "edition_date": "2026-08-17",
                    "findings": [{"title": suffix}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    paths = current_intake_compat._queue_source_paths(tmp_path, inbox, "2026-08-17")

    assert [path.name for path in paths] == [
        "food-line-source-watch-2026-08-17-a.json",
        "food-line-source-watch-2026-08-17-b.json",
    ]


def test_current_intake_no_current_handoff_writes_empty_private_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "food-line-source-watch-2026-08-16.json").write_text(
        json.dumps({"edition_date": "2026-08-16", "findings": []}),
        encoding="utf-8",
    )

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-17",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    queue = json.loads((tmp_path / "data/dispatches/food-line/review/current-signal-review.json").read_text(encoding="utf-8"))
    report = json.loads(
        (tmp_path / "data/dispatches/food-line/review/reports/2026-08-17/current-intake.json").read_text(encoding="utf-8")
    )
    assert queue["items"] == []
    assert queue["source_inputs"] == []
    assert report["selected_input_count"] == 0
    assert report["proposal"]["draft_status"] == "blocked_no_reviewable_current_signals"


def test_legacy_current_intake_wrapper_records_duplicate_source_watch_findings_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    export_path = inbox / "food-line-source-watch-2026-08-19-food-line-scheduled-test.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_source_watch_agent_export_v1",
                "agent_name": "Food Line Source Watch",
                "agent_run_id": "food-line-scheduled-test",
                "edition_date": "2026-08-19",
                "completed_at": "2026-08-19T10:00:00Z",
                "findings": [
                    {"title": "first"},
                    {"title": "second"},
                ],
                "coverage_notes": "synthetic test",
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    duplicate_item = _valid_current_queue_item("2026-08-19")
    duplicate_item["candidate_id"] = "food-line-current-001"
    duplicate_item["agent_duplicate_key"] = "duplicate-source-watch-finding"
    duplicate_item["agent_finding_id"] = "finding-current-001"
    duplicate_item.pop("review_item_id", None)
    duplicate_item.pop("source_finding_or_intake_id", None)

    second_duplicate_item = _valid_current_queue_item("2026-08-19")
    second_duplicate_item["candidate_id"] = "food-line-current-002"
    second_duplicate_item["agent_duplicate_key"] = "duplicate-source-watch-finding"
    second_duplicate_item["agent_finding_id"] = "finding-current-002"
    second_duplicate_item.pop("review_item_id", None)
    second_duplicate_item.pop("source_finding_or_intake_id", None)

    findings = [object(), object()]
    mapped_rows = [duplicate_item, second_duplicate_item]

    monkeypatch.setattr(
        current_intake_compat,
        "adapt_food_line_agent_output",
        lambda payload, *, agent_name, agent_run_id: list(findings),
    )
    monkeypatch.setattr(
        current_intake_compat,
        "map_finding_to_food_line_candidate",
        lambda finding, *, edition_date: dict(mapped_rows.pop(0)),
    )

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-19",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    intake_path = tmp_path / "data" / "dispatches" / "food-line" / "agent-intake" / "2026-08-19" / "food-line-scheduled-test.json"
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    candidate_rows = intake["candidate_rows"]
    assert len(candidate_rows) == 2
    assert candidate_rows[0]["candidate_disposition"] == "retained_for_review"
    assert candidate_rows[1]["candidate_disposition"] == "duplicate"
    assert candidate_rows[1]["candidate_disposition_reason"] == "duplicate agent_duplicate_key within intake"
    assert intake["counts"]["eligible_for_review"] == 1
    assert intake["counts"]["duplicate"] == 1
    report = json.loads(
        (tmp_path / "data" / "dispatches" / "food-line" / "review" / "reports" / "2026-08-19" / "current-intake.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "success"
    assert report["queue"]["item_count"] == 1
    assert report["proposal"]["draft_status"] == "draft_approved_pending_publication"


def test_legacy_current_intake_wrapper_accepts_source_published_date_from_inbox_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    export_path = inbox / "food-line-source-watch-2026-08-19-food-line-scheduled-test.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_source_watch_agent_export_v1",
                "agent_name": "Food Line Source Watch",
                "agent_run_id": "food-line-scheduled-test",
                "edition_date": "2026-08-19",
                "completed_at": "2026-08-19T10:00:00Z",
                "findings": [
                    {
                        "title": "Pantry could not continue safely",
                        "publisher": "Example News",
                        "source_url": "https://example.org/current-food-pressure",
                        "canonical_source_url": "https://example.org/current-food-pressure",
                        "exact_supporting_passage": "The pantry provided food to an average of 960 people and distributed approximately 34,000 pounds of food each month. Building conditions and repair costs made it impossible to continue operating the pantry safely and sustainably.",
                        "summary": "A pressure signal.",
                        "location_name": "Example City",
                        "state": "CA",
                        "location_scope": "city",
                        "pressure_type": "service_closure",
                        "source_role": "pressure_reporting",
                        "evidence_level": "direct_reporting",
                        "source_published_date": "2026-08-19",
                    }
                ],
                "coverage_notes": "synthetic test",
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-19",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    report_path = tmp_path / "data" / "dispatches" / "food-line" / "review" / "reports" / "2026-08-19" / "current-intake.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "food_line_current_intake_report_v1"
    assert report["status"] == "success"
    assert report["errors"] == []
    assert report["queue"]["item_count"] == 1
    assert report["proposal"]["draft_status"] == "draft_pending_editorial_review"
    assert Path(report["proposal"]["markdown_path"]).exists()


def test_legacy_discovery_timeout_helper_terminates_process_tree(tmp_path: Path) -> None:
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path)
    try:
        discovery_compat._terminate_process_tree(parent.pid)
        deadline = time.time() + 10
        while time.time() < deadline and scheduler.process_is_running(parent.pid):
            time.sleep(0.05)

        assert not scheduler.process_is_running(parent.pid)
    finally:
        if parent.poll() is None:
            parent.kill()


def test_food_line_discovery_terminal_json_is_ascii_safe_for_windows_cp1252(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        discovery_compat,
        "run_food_line_discovery_expansion",
        lambda *args, **kwargs: {"ok": True, "status": "completed", "marker": "\ufeff"},
    )

    code = discovery_compat.main(["--date", "2026-09-08"])

    output = capsys.readouterr().out
    output.encode("cp1252")
    assert code == 0
    assert json.loads(output)["marker"] == "\ufeff"


def test_legacy_discovery_wrapper_timeout_writes_timed_out_state_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-18"
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    def fake_core(root: Path, edition_date: str, **kwargs: object) -> dict[str, object]:
        assert root == tmp_path
        assert edition_date == "2026-08-18"
        assert kwargs["runtime_deadline"] is not None
        return {
            "ok": False,
            "status": "timed_out",
            "timed_out": True,
            "error_type": "timeout",
            "error_message": "Food Line discovery exceeded bounded runtime of 1 seconds",
            "error": "Food Line discovery exceeded bounded runtime of 1 seconds",
            "candidate_count": 1,
            "public_eligible_candidate_count": 0,
            "rejected_news_count": 0,
            "fetch_failure_count_by_type": {},
            "direct_source_count": 1,
            "queries_completed": 1,
            "queries_timed_out": 1,
            "queries_failed": 0,
            "partitions_completed": 0,
            "resumable": True,
        }

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(discovery_compat, "run_food_line_discovery_expansion", fake_core)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "food-line-scheduled-timeout-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 1
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-18" / "food-line-scheduled-timeout-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "query-plan.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == scheduler.RUN_STATE_SCHEMA
    assert state["status"] == "timed_out"
    assert state["resumable"] is True
    assert state["queries_total"] == 1
    assert state["queries_completed"] == 1
    assert state["queries_timed_out"] == 1
    assert plan["schema_version"] == "food_line_bounded_query_plan_v1"
    assert not (tmp_path / "status" / "food-line" / "locks" / "source-watch.lock").exists()
    assert "timeout" in capsys.readouterr().out


def test_legacy_discovery_wrapper_zero_result_completion_is_structured_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-18"
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    zero_result = {
        "ok": True,
        "candidate_count": 0,
        "public_eligible_candidate_count": 0,
        "rejected_news_count": 0,
        "fetch_failure_count_by_type": {},
        "direct_source_count": 0,
    }

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(
        discovery_compat,
        "run_food_line_discovery_expansion",
        lambda root, edition_date, **kwargs: {
            "ok": True,
            "status": "completed",
            "candidate_count": 0,
            "public_eligible_candidate_count": 0,
            "rejected_news_count": 0,
            "fetch_failure_count_by_type": {},
            "direct_source_count": 0,
            "queries_completed": 1,
            "queries_timed_out": 0,
            "queries_failed": 0,
            "partitions_completed": 1,
            "resumable": False,
        },
    )

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "food-line-scheduled-zero-result-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "zero_result_completion" in stdout
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-18" / "food-line-scheduled-zero-result-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["candidates_discovered"] == 0
    assert not (tmp_path / "status" / "food-line" / "locks" / "source-watch.lock").exists()


def test_food_line_resume_passes_run_id_to_status_and_resume_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = "food-line-scheduled-resume-test"
    edition_date = "2026-08-19"
    layout_root = tmp_path
    run_dir = layout_root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "run-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": scheduler.RUN_STATE_SCHEMA,
                "run_id": run_id,
                "edition_date": edition_date,
                "status": "timed_out",
                "resumable": True,
                "resume_count": 0,
                "partitions_total": 1,
                "partitions_completed": 0,
                "queries_total": 1,
                "queries_completed": 0,
                "queries_failed": 0,
                "queries_timed_out": 1,
                "candidates_discovered": 0,
                "query_plan_sha256": "abc",
                "final_error": "timed out",
                "options": {"required_coverage_threshold": 0.9, "direct_source_coverage_threshold": 0.75},
                "coverage": {"required_success_ratio": 0.0, "direct_success_ratio": 0.0},
                "agent_export": {"status": "blocked_incomplete_collection", "path": str(tmp_path), "sha256": ""},
                "next_action": "No collection action required.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    record = {
        "schema_version": scheduler.RUN_RECORD_SCHEMA,
        "edition_date": edition_date,
        "run_id": run_id,
        "source_commit": "deadbeef",
        "source_branch": scheduler.PRODUCTION_BRANCH,
        "run_state_path": str(state_path),
        "scheduled_start_at": "2026-08-19T18:34:44.502059Z",
        "resume_attempted": False,
    }
    run_record_path = layout_root / "status" / "food-line" / "runs" / f"{edition_date}.json"
    run_record_path.parent.mkdir(parents=True, exist_ok=True)
    run_record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    captured_commands: list[list[str]] = []

    @contextmanager
    def fake_source_lock(*args: object, **kwargs: object):
        yield

    def fake_load_record_and_state(layout: scheduler.Layout, loaded_edition_date: str):
        assert loaded_edition_date == edition_date
        return record, json.loads(state_path.read_text(encoding="utf-8")), state_path

    def fake_invoke_python(python: Path, root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        captured_commands.append([str(item) for item in arguments])
        if arguments[1] != "--status-run":
            assert "--run-id" in arguments
            assert "--max-run-minutes" in arguments
            assert arguments[arguments.index("--max-run-minutes") + 1] == "30"
            assert "--max-queries" in arguments
            assert arguments[arguments.index("--max-queries") + 1] == "200"
        else:
            assert "--run-id" in arguments
            assert arguments[arguments.index("--run-id") + 1] == run_id
        return subprocess.CompletedProcess([str(python), *arguments], 0, stdout="{}", stderr="")

    monkeypatch.setattr(scheduler, "source_lock", fake_source_lock)
    monkeypatch.setattr(scheduler, "_load_record_and_state", fake_load_record_and_state)
    monkeypatch.setattr(scheduler, "_verify_same_source_commit", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke_python)
    monkeypatch.setattr(scheduler, "collection_qualifies", lambda state: False)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda run_dir: [])

    code = scheduler.run_resume(
        scheduler.argparse.Namespace(
            repo_root=str(tmp_path),
            python=str(tmp_path / ".venv" / "Scripts" / "python.exe"),
            edition_date=edition_date,
            branch=scheduler.PRODUCTION_BRANCH,
            test_mode=False,
            stale_lock_minutes=45,
        )
    )

    assert code == 2
    assert captured_commands[0][:2] == ["scripts/run_food_line_discovery_expansion.py", "--status-run"]
    assert "--run-id" in captured_commands[0]
    assert "--run-id" in captured_commands[1]
    assert "--run-id" in captured_commands[2]
    assert "--max-run-minutes" in captured_commands[1]
    assert captured_commands[1][captured_commands[1].index("--max-run-minutes") + 1] == "30"
    assert "--max-queries" in captured_commands[1]
    assert captured_commands[1][captured_commands[1].index("--max-queries") + 1] == "200"


def test_legacy_discovery_wrapper_reports_child_process_failure_with_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(discovery_compat, "run_food_line_discovery_expansion", boom)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "child_process_failure"
    assert payload["ok"] is False
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "boom"


def test_legacy_discovery_wrapper_reports_malformed_child_output_and_stderr_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(
        discovery_compat,
        "run_food_line_discovery_expansion",
        lambda root, edition_date, **kwargs: {
            "ok": False,
            "status": "malformed_child_output",
            "error_type": "malformed_child_output",
            "error_message": "legacy bounded discovery returned invalid JSON: <test>",
            "error": "legacy bounded discovery returned invalid JSON: <test>",
            "candidate_count": 0,
            "public_eligible_candidate_count": 0,
            "rejected_news_count": 0,
            "fetch_failure_count_by_type": {},
            "direct_source_count": 0,
            "queries_completed": 0,
            "queries_timed_out": 0,
            "queries_failed": 0,
            "partitions_completed": 1,
            "resumable": False,
        },
    )

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "malformed-child-output-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "collection_failure"
    assert payload["ok"] is False
    assert payload["error_type"] == "malformed_child_output"
    assert "invalid JSON" in payload["error_message"]


def test_real_child_completed_with_exclusions_contract_is_exit_zero_and_scheduler_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    edition_date = "2026-09-07"
    run_id = "completed-with-exclusions-contract"
    monkeypatch.chdir(tmp_path)

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "query_id": "q-1",
                "query_family": "source_watch",
                "geographic_scope": "national",
                "discovery_channel": "direct_rss",
                "query_text": "food access strain",
            }
        ]

    def fake_discovery(root: Path, edition_date: str, **kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "status": "completed_with_exclusions",
            "candidate_count": 1,
            "public_eligible_candidate_count": 1,
            "direct_source_count": 1,
            "source_count": 1,
            "rejected_news_count": 2,
            "fetch_failure_count_by_type": {"http_503": 1},
            "queries_completed": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "partitions_completed": 1,
            "resumable": False,
            "candidates": [
                {
                    "title": "Pantry documents food access strain",
                    "source_url": "https://example.org/food-access-strain",
                    "publisher": "Example Pantry",
                }
            ],
        }

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(discovery_compat, "run_food_line_discovery_expansion", fake_discovery)

    child_exit = discovery_compat.main(
        [
            "--date",
            edition_date,
            "--run-id",
            run_id,
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )
    child_stdout = capsys.readouterr().out
    child_payload = json.loads(child_stdout)
    state_path = Path(str(child_payload["run_state_path"]))
    durable_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert child_exit == 0
    assert child_payload["ok"] is True
    assert child_payload["status"] == "completed_with_exclusions"
    assert child_payload["child_outcome_classification"] == "completed_with_exclusions"
    assert child_payload["fatal_error"] == ""
    assert durable_state["status"] == "completed_with_exclusions"
    assert durable_state["final_error"] == ""
    assert durable_state["queries_completed"] == durable_state["queries_total"]
    assert durable_state["queries_failed"] == 0
    assert durable_state["queries_timed_out"] == 0
    assert durable_state["agent_export"]["status"] == "success_with_exclusions"
    assert Path(durable_state["agent_export"]["path"]).exists()

    monkeypatch.setattr(scheduler, "verify_checkout", lambda *args, **kwargs: "test-source-commit")
    monkeypatch.setattr(scheduler, "run_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        scheduler,
        "_invoke_python",
        lambda python, root, arguments: subprocess.CompletedProcess(
            [str(python), *arguments], child_exit, stdout=child_stdout, stderr=""
        ),
    )

    scheduler_exit = scheduler.run_source_watch(_source_watch_args(tmp_path, edition_date=edition_date, run_id=run_id))
    receipts = sorted((tmp_path / "logs" / "food-line" / "source-watch" / edition_date).glob("*-source-watch.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))

    assert scheduler_exit == 0
    assert receipt["exit_code"] == 0
    assert receipt["command_exit_code"] == 0
    assert receipt["child_terminal_status"] == "completed_with_exclusions"
    assert receipt["child_terminal_ok"] is True
    assert receipt["child_declared_outcome"] == "completed_with_exclusions"
    assert receipt["child_outcome_classification"] == "allowed_nonfatal_completed_with_exclusions"
    assert receipt["child_validation_error"] == ""
    assert receipt["required_export_present"] is True
    assert not (tmp_path / "output" / "site").exists()


def _source_watch_args(tmp_path: Path, *, edition_date: str = "2026-09-07", run_id: str = "source-watch-test") -> scheduler.argparse.Namespace:
    return scheduler.argparse.Namespace(
        repo_root=str(tmp_path),
        python=str(tmp_path / ".venv" / "Scripts" / "python.exe"),
        edition_date=edition_date,
        run_id=run_id,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
    )


def _write_source_watch_artifacts(
    root: Path,
    *,
    edition_date: str,
    run_id: str,
    status: str,
    export_status: str = "success",
    final_error: str = "",
    export_present: bool = True,
) -> Path:
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    export_path = root / "data" / "dispatches" / "food-line" / "agent-inbox" / f"food-line-source-watch-{edition_date}-test.json"
    if export_present:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text('{"schema_version":"food_line_source_watch_agent_export_v1","findings":[]}\n', encoding="utf-8")
    plan = {
        "schema_version": "food_line_bounded_query_plan_v1",
        "run_id": run_id,
        "edition_date": edition_date,
        "configuration_sha256": "config-sha",
        "query_count": 1,
        "queries": [{"query_id": "q-1"}],
        "query_plan_sha256": "plan-sha",
    }
    state = {
        "schema_version": scheduler.RUN_STATE_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "started_at": "2026-09-07T12:30:00Z",
        "completed_at": "2026-09-07T12:31:00Z",
        "status": status,
        "resumable": False,
        "resume_count": 0,
        "partitions_total": 1,
        "partitions_completed": 1,
        "queries_total": 1,
        "queries_completed": 1,
        "queries_failed": 0,
        "queries_timed_out": 0,
        "candidates_discovered": 1,
        "query_plan_sha256": "plan-sha",
        "final_error": final_error,
        "options": {"required_coverage_threshold": 0.90, "direct_source_coverage_threshold": 0.75},
        "coverage": {"required_success_ratio": 1.0, "direct_success_ratio": 1.0},
        "agent_export": {"status": export_status, "path": str(export_path), "sha256": "export-sha"},
        "next_action": "No collection action required.",
    }
    (run_dir / "query-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "run-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return export_path


def _run_source_watch_with_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    child_exit: int,
    child_stdout: str,
    child_stderr: str = "",
    state_status: str = "completed",
    export_status: str = "success",
    final_error: str = "",
    export_present: bool = True,
    run_id: str = "source-watch-test",
) -> tuple[int, dict[str, object]]:
    edition_date = "2026-09-07"
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *args, **kwargs: "test-source-commit")
    monkeypatch.setattr(scheduler, "run_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda *args, **kwargs: [])

    def fake_invoke_python(python: Path, root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        _write_source_watch_artifacts(
            root,
            edition_date=edition_date,
            run_id=run_id,
            status=state_status,
            export_status=export_status,
            final_error=final_error,
            export_present=export_present,
        )
        return subprocess.CompletedProcess([str(python), *arguments], child_exit, stdout=child_stdout, stderr=child_stderr)

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke_python)
    code = scheduler.run_source_watch(_source_watch_args(tmp_path, edition_date=edition_date, run_id=run_id))
    receipts = sorted((tmp_path / "logs" / "food-line" / "source-watch" / edition_date).glob("*-source-watch.json"))
    assert len(receipts) == 1
    return code, json.loads(receipts[0].read_text(encoding="utf-8"))


def _child_payload(
    *,
    status: str,
    ok: bool,
    run_id: str = "source-watch-test",
    outcome: str | None = None,
    export_status: str = "success",
    final_error: str = "",
    fatal_error: str = "",
) -> str:
    payload: dict[str, object] = {
        "ok": ok,
        "status": status,
        "run_id": run_id,
        "edition_date": "2026-09-07",
        "agent_export": {"status": export_status, "path": "unused", "sha256": "export-sha"},
        "final_error": final_error,
        "fatal_error": fatal_error,
    }
    if outcome is not None:
        payload["child_outcome_classification"] = outcome
    return json.dumps(payload)


def test_source_watch_child_zero_with_structured_success_returns_scheduler_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(status="completed", ok=True),
    )

    assert code == 0
    assert receipt["exit_code"] == 0
    assert receipt["child_exit_code"] == 0
    assert receipt["child_outcome_classification"] == "success"
    assert receipt["child_terminal_output_parsed"] is True


def test_source_watch_child_zero_explicit_nonfatal_with_qualified_state_returns_scheduler_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(
            status="completed_with_exclusions",
            ok=True,
            outcome="completed_with_exclusions",
            export_status="success_with_exclusions",
        ),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
    )

    assert code == 0
    assert receipt["exit_code"] == 0
    assert receipt["command_exit_code"] == 0
    assert receipt["child_terminal_ok"] is True
    assert receipt["child_declared_outcome"] == "completed_with_exclusions"
    assert receipt["child_outcome_classification"] == "allowed_nonfatal_completed_with_exclusions"
    assert receipt["child_validation_error"] == ""


def test_source_watch_child_one_missing_terminal_result_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(tmp_path, monkeypatch, child_exit=1, child_stdout="")

    assert code == 1
    assert receipt["exit_code"] == 1
    assert receipt["child_terminal_output_found"] is False
    assert receipt["child_outcome_classification"] == "missing_terminal_result"


def test_source_watch_child_one_malformed_terminal_result_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(tmp_path, monkeypatch, child_exit=1, child_stdout="{not-json")

    assert code == 1
    assert receipt["exit_code"] == 1
    assert receipt["child_terminal_output_parsed"] is False
    assert receipt["child_outcome_classification"] == "malformed_terminal_result"


def test_source_watch_child_zero_unknown_nonfatal_classification_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(
            status="completed_with_exclusions",
            ok=True,
            export_status="success_with_exclusions",
        ),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
    )

    assert code == 2
    assert receipt["child_outcome_classification"] == "unknown"
    assert "lacks an allowed outcome" in receipt["child_validation_error"]


def test_source_watch_child_one_explicit_fatal_result_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=1,
        child_stdout=_child_payload(
            status="completed_with_exclusions",
            ok=False,
            outcome="completed_with_exclusions",
            export_status="success_with_exclusions",
            fatal_error="boom",
        ),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
    )

    assert code == 1
    assert receipt["child_outcome_classification"] == "fatal"
    assert "fatal/final error" in receipt["child_validation_error"]


def test_source_watch_allowed_nonfatal_outcome_requires_child_ok_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=1,
        child_stdout=_child_payload(
            status="collection_failure",
            ok=False,
            outcome="completed_with_exclusions",
            export_status="success_with_exclusions",
        ),
        state_status="collection_failure",
        export_status="success_with_exclusions",
    )

    assert code == 1
    assert receipt["child_outcome_classification"] == "unknown"
    assert receipt["child_validation_error"] == "allowed nonfatal child outcome requires ok=true"


def test_source_watch_child_zero_contradicting_durable_state_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(status="completed", ok=True, outcome="completed_with_exclusions"),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
    )

    assert code == 2
    assert receipt["child_outcome_classification"] == "contradiction"
    assert "does not match durable status" in receipt["child_validation_error"]


def test_source_watch_child_zero_cannot_override_nonqualifying_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(status="failed", ok=True),
        state_status="failed",
        final_error="collection failed",
    )

    assert code == 2
    assert receipt["exit_code"] == 2
    assert receipt["final_status"] == "failed"


def test_source_watch_missing_required_export_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(
            status="completed_with_exclusions",
            ok=True,
            outcome="completed_with_exclusions",
            export_status="success_with_exclusions",
        ),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
        export_present=False,
    )

    assert code == 2
    assert receipt["required_export_required"] is True
    assert receipt["required_export_present"] is False


def test_source_watch_child_output_audit_is_bounded_and_marks_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_stdout = "x" * (scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT + 25)
    long_stderr = "e" * (scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT + 10)

    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=1,
        child_stdout=long_stdout,
        child_stderr=long_stderr,
    )

    assert code == 1
    assert receipt["child_stdout_truncated"] is True
    assert receipt["child_stderr_truncated"] is True
    assert len(receipt["child_stdout_tail"]) == scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT
    assert len(receipt["child_stderr_tail"]) == scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT
    assert receipt["child_stdout_char_count"] == scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT + 25
    assert receipt["child_stderr_char_count"] == scheduler.CHILD_OUTPUT_AUDIT_CHAR_LIMIT + 10


def test_source_watch_receipt_contains_child_audit_fields_and_no_public_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(
            status="completed_with_exclusions",
            ok=True,
            outcome="completed_with_exclusions",
            export_status="success_with_exclusions",
        ),
        state_status="completed_with_exclusions",
        export_status="success_with_exclusions",
    )

    assert code == 0
    for key in (
        "child_exit_code",
        "child_terminal_output_found",
        "child_terminal_output_parsed",
        "child_outcome_classification",
        "child_stdout_tail",
        "child_stdout_truncated",
        "child_stderr_tail",
        "child_stderr_truncated",
        "child_validation_error",
        "required_export_present",
    ):
        assert key in receipt
    assert not (tmp_path / "output" / "site").exists()


def _resume_args(tmp_path: Path, *, edition_date: str = "2026-09-07") -> scheduler.argparse.Namespace:
    return scheduler.argparse.Namespace(
        repo_root=str(tmp_path),
        python=str(tmp_path / ".venv" / "Scripts" / "python.exe"),
        edition_date=edition_date,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
    )


def _intake_args(tmp_path: Path, *, edition_date: str = "2026-09-07") -> scheduler.argparse.Namespace:
    return scheduler.argparse.Namespace(
        repo_root=str(tmp_path),
        python=str(tmp_path / ".venv" / "Scripts" / "python.exe"),
        edition_date=edition_date,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        lock_wait_seconds=0,
        lock_poll_seconds=0.01,
    )


def _run_record_payload(tmp_path: Path, *, edition_date: str, run_id: str, status: str) -> dict[str, object]:
    return {
        "schema_version": scheduler.RUN_RECORD_SCHEMA,
        "edition_date": edition_date,
        "run_id": run_id,
        "source_commit": "test-source-commit",
        "source_branch": scheduler.PRODUCTION_BRANCH,
        "run_state_path": str(
            tmp_path
            / "data"
            / "dispatches"
            / "food-line"
            / "discovery-runs"
            / edition_date
            / run_id
            / "run-state.json"
        ),
        "scheduled_start_at": "2026-09-07T12:30:00Z",
        "source_watch_status": status,
        "last_status": status,
        "resume_attempted": False,
        "release_ready": False,
    }


def _write_run_record(tmp_path: Path, *, edition_date: str = "2026-09-07", run_id: str = "source-watch-test", status: str) -> Path:
    path = tmp_path / "status" / "food-line" / "runs" / f"{edition_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_run_record_payload(tmp_path, edition_date=edition_date, run_id=run_id, status=status), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def test_resume_before_source_watch_initialization_is_successful_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_if_locked(*args: object, **kwargs: object) -> object:
        raise AssertionError("resume must not acquire the source-watch lock before a run record exists")

    monkeypatch.setattr(scheduler, "source_lock", fail_if_locked)

    code = scheduler.run_resume(_resume_args(tmp_path))

    assert code == 0
    assert not (tmp_path / "status" / "food-line" / "locks" / "source-watch.lock").exists()
    receipt = json.loads(next((tmp_path / "logs" / "food-line" / "source-watch" / "2026-09-07").glob("*-status-resume.json")).read_text(encoding="utf-8"))
    assert receipt["final_status"] == scheduler.UPSTREAM_NOT_INITIALIZED_STATUS
    assert receipt["exit_code"] == 0
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["ok"] is True
    assert terminal["final_status"] == scheduler.UPSTREAM_NOT_INITIALIZED_STATUS
    operational = json.loads(
        next(
            (tmp_path / "status" / "operational-health" / "food-line" / "2026-09-07" / "runs").glob(
                "food_line_source_watch_resume-*.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert operational["schema_version"] == "bluefern_operational_health_receipt_v1"
    assert operational["status"] == "UPSTREAM_BLOCKED"
    assert operational["artifact_refs"]["task_receipt"].endswith("-status-resume.json")
    assert receipt["operational_health_receipt_path"].endswith(".json")


def test_source_watch_after_resume_preinitialization_noop_can_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert scheduler.run_resume(_resume_args(tmp_path)) == 0

    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(status="completed", ok=True),
    )

    assert code == 0
    assert receipt["exit_code"] == 0
    run_record = json.loads((tmp_path / "status" / "food-line" / "runs" / "2026-09-07.json").read_text(encoding="utf-8"))
    assert run_record["source_watch_status"] == "completed"
    assert run_record["source_receipt_path"]


def test_source_watch_active_lock_collision_writes_daily_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "status" / "food-line" / "locks" / "source-watch.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"task": "status-resume", "pid": os.getpid(), "acquired_at": "2026-09-07T13:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "process_is_running", lambda pid: True)

    code = scheduler.run_source_watch(_source_watch_args(tmp_path))

    assert code == 10
    assert lock_dir.exists()
    record = json.loads((tmp_path / "status" / "food-line" / "runs" / "2026-09-07.json").read_text(encoding="utf-8"))
    assert record["source_watch_status"] == scheduler.BLOCKED_OVERLAPPING_STATUS
    assert record["lock_owner"]["task"] == "status-resume"
    receipt = json.loads(next((tmp_path / "logs" / "food-line" / "source-watch" / "2026-09-07").glob("*-source-watch.json")).read_text(encoding="utf-8"))
    assert receipt["final_status"] == scheduler.BLOCKED_OVERLAPPING_STATUS
    assert receipt["exit_code"] == 10
    operational = json.loads(
        next((tmp_path / "status" / "operational-health" / "food-line" / "2026-09-07" / "runs").glob("food_line_source_watch-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert operational["status"] == "FAILED"
    assert operational["classification"] == scheduler.BLOCKED_OVERLAPPING_STATUS


def test_active_lock_is_not_reclaimed_even_after_stale_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "status" / "food-line" / "locks" / "source-watch.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(json.dumps({"pid": os.getpid(), "task": "source-watch"}), encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock_dir, (old, old))
    monkeypatch.setattr(scheduler, "process_is_running", lambda pid: True)

    code = scheduler.run_source_watch(_source_watch_args(tmp_path))

    assert code == 10
    assert lock_dir.exists()
    record = json.loads((tmp_path / "status" / "food-line" / "runs" / "2026-09-07.json").read_text(encoding="utf-8"))
    assert record["source_watch_status"] == scheduler.BLOCKED_OVERLAPPING_STATUS


def test_proven_stale_lock_is_reclaimed_before_source_watch_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "status" / "food-line" / "locks" / "source-watch.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(json.dumps({"pid": 999999, "task": "source-watch"}), encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock_dir, (old, old))
    monkeypatch.setattr(scheduler, "process_is_running", lambda pid: False)

    code, receipt = _run_source_watch_with_child(
        tmp_path,
        monkeypatch,
        child_exit=0,
        child_stdout=_child_payload(status="completed", ok=True),
    )

    assert code == 0
    assert receipt["exit_code"] == 0
    assert not lock_dir.exists()
    attention = sorted((tmp_path / "logs" / "food-line" / "operator-attention" / "2026-09-07").glob("*.json"))
    assert any(json.loads(path.read_text(encoding="utf-8"))["category"] == "stale_lock_reclaimed" for path in attention)


def test_ambiguous_stale_lock_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    lock_dir = tmp_path / "status" / "food-line" / "locks" / "source-watch.lock"
    lock_dir.mkdir(parents=True)
    old = time.time() - 7200
    os.utime(lock_dir, (old, old))

    code = scheduler.run_source_watch(_source_watch_args(tmp_path))

    assert code == 10
    assert lock_dir.exists()
    record = json.loads((tmp_path / "status" / "food-line" / "runs" / "2026-09-07.json").read_text(encoding="utf-8"))
    assert record["source_watch_status"] == scheduler.AMBIGUOUS_STALE_LOCK_STATUS


def test_current_intake_missing_source_watch_record_is_successful_upstream_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = scheduler.run_intake(_intake_args(tmp_path))

    assert code == 0
    receipt = json.loads(next((tmp_path / "logs" / "food-line" / "current-intake" / "2026-09-07").glob("*-current-intake.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == scheduler.UPSTREAM_NOT_INITIALIZED_STATUS
    assert receipt["exit_code"] == 0
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["ok"] is True


def test_current_intake_blocked_source_watch_record_is_successful_upstream_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_run_record(tmp_path, status=scheduler.BLOCKED_OVERLAPPING_STATUS)

    code = scheduler.run_intake(_intake_args(tmp_path))

    assert code == 0
    receipt = json.loads(next((tmp_path / "logs" / "food-line" / "current-intake" / "2026-09-07").glob("*-current-intake.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == "upstream_blocked"
    assert receipt["source_status"] == scheduler.BLOCKED_OVERLAPPING_STATUS
    assert receipt["exit_code"] == 0
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["ok"] is True
    operational = json.loads(
        next((tmp_path / "status" / "operational-health" / "food-line" / "2026-09-07" / "runs").glob("food_line_current_intake-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert operational["status"] == "UPSTREAM_BLOCKED"
    assert operational["upstream_dependency_status"] == scheduler.BLOCKED_OVERLAPPING_STATUS


def test_current_intake_corrupt_source_watch_record_still_fails_closed(tmp_path: Path) -> None:
    record = tmp_path / "status" / "food-line" / "runs" / "2026-09-07.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not-json", encoding="utf-8")

    code = scheduler.run_intake(_intake_args(tmp_path))

    assert code == 10
    receipt = json.loads(
        next((tmp_path / "logs" / "food-line" / "current-intake" / "2026-09-07").glob("*-current-intake.json")).read_text(encoding="utf-8")
    )
    assert receipt["status"] == "current_intake_failed"
    operational = json.loads(
        next((tmp_path / "status" / "operational-health" / "food-line" / "2026-09-07" / "runs").glob("food_line_current_intake-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert operational["status"] == "FAILED"
