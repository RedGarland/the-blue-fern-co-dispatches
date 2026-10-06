from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import preflight_repo_state


def _preflight_report(lines: list[str], monkeypatch, tmp_path: Path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(preflight_repo_state, "_run_git_status", lambda _repo: (0, lines))
    return preflight_repo_state.build_preflight_report(source_repo)


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _load_scheduler_module(repo: Path):
    path = repo / "scripts" / "care_line_collection_scheduler.py"
    spec = importlib.util.spec_from_file_location("care_line_collection_scheduler", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_publication_scheduler_module(repo: Path):
    path = repo / "scripts" / "care_line_publication_scheduler.py"
    spec = importlib.util.spec_from_file_location("care_line_publication_scheduler_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_care_line_runtime_paths_are_allowed_but_nearby_paths_stay_risky(monkeypatch, tmp_path: Path) -> None:
    lines = [
        "## add/care-line-runtime",
        "?? data/dispatches/care-line/review/candidate-registry.json",
        "?? data/dispatches/care-line/review/current-review-queue.json",
        "?? data/dispatches/care-line/review/evidence-review-packets/current-care-line-evidence-review.json",
        "?? data/dispatches/care-line/review/effective-date-follow-up-state.json",
        "?? data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json",
        "?? data/dispatches/care-line/queue-runs/2026-08-22/20260822T191105Z-89152084.json",
        "?? data/dispatches/care-line/sources/2026-08-23/discovered_sources.json",
        "?? data/dispatches/care-line/sources/2026-08-23/discovery_report.json",
        "?? data/dispatches/incidents/incident_seeds.json",
        "?? data/dispatches/incidents/incident_seed_discovery_report.json",
        "?? logs/care-line/collection-scheduler/2026-08-22.log",
        "?? status/care-line/locks/national-collection.lock",
        "?? status/care-line/scheduler-runs/2026-08-22/receipt.json",
        "?? status/care-line/effective-date-follow-up-state.json",
        "?? data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json",
        "?? data/dispatches/care-line/source_registry.json",
        " M src/bluefern_dispatches/care_line_national_pipeline.py",
    ]

    report = _preflight_report(lines, monkeypatch, tmp_path)

    assert report["ok"] is False
    assert {entry["category"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "review_state",
        "local_run_state",
        "logs",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json",
        "data/dispatches/care-line/source_registry.json",
        "src/bluefern_dispatches/care_line_national_pipeline.py",
    }
    assert report["allowlisted_categories"] == [
        "cache",
        "local_run_state",
        "logs",
        "review_output",
        "review_state",
        "virtualenv",
    ]


def test_care_line_runtime_path_classification_is_narrow_and_strict() -> None:
    assert preflight_repo_state.classify_path("data/dispatches/care-line/review/current-review-queue.json") == "review_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/review/evidence-review-packets/current-care-line-evidence-review.json") == "review_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/queue-runs/2026-08-22/20260822T191105Z-89152084.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/sources/2026-08-23/discovered_sources.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/sources/2026-08-23/discovery_report.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/incidents/incident_seeds.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/incidents/incident_seed_discovery_report.json") == "local_run_state"
    assert preflight_repo_state.classify_path("logs/care-line/collection-scheduler/2026-08-22.log") == "logs"
    assert preflight_repo_state.classify_path("status/care-line/effective-date-follow-up-state.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json") == "unknown"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/review/proposed-editions/file.json") == "review_output"


def test_care_line_scheduler_verify_checkout_allows_runtime_state_but_blocks_source_drift(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scheduler = _load_scheduler_module(Path(__file__).resolve().parents[1])

    def fake_run(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        "## add/pages-repo-default",
                        "?? data/dispatches/care-line/review/current-review-queue.json",
                        "?? data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json",
                        "?? logs/care-line/collection-scheduler/2026-08-22.log",
                        "?? data/dispatches/care-line/sources/2026-08-23/discovered_sources.json",
                        "?? data/dispatches/care-line/sources/2026-08-23/discovery_report.json",
                        "?? data/dispatches/incidents/incident_seeds.json",
                        "?? data/dispatches/incidents/incident_seed_discovery_report.json",
                        "?? status/care-line/effective-date-follow-up-state.json",
                        "?? status/care-line/locks/national-collection.lock",
                        "?? status/care-line/scheduler-runs/2026-08-22/receipt.json",
                        "?? ops/operator/runs/2026-09-26/runner-sync-apply-20260927T050430Z-1b01d7504368/guarded-runner-sync-receipt.json",
                    ]
                )
                + "\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run)
    assert scheduler.verify_checkout(repo, "add/pages-repo-default") == "abc123"

    def fake_run_dirty(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="## add/pages-repo-default\n?? data/dispatches/care-line/review/current-review-queue.json\n M src/bluefern_dispatches/care_line_national_pipeline.py\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run_dirty)
    try:
        scheduler.verify_checkout(repo, "add/pages-repo-default")
    except scheduler.SchedulerError as exc:  # type: ignore[attr-defined]
        assert "src/bluefern_dispatches/care_line_national_pipeline.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected dirty checkout to fail closed")


def test_care_line_scheduler_allows_sanctioned_operator_run_evidence_but_not_nearby_paths(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scheduler = _load_scheduler_module(Path(__file__).resolve().parents[1])

    def fake_run_operator_evidence(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        "## add/pages-repo-default",
                        "?? ops/operator/runs/2026-09-26/runner-sync-apply-20260927T050430Z-1b01d7504368/guarded-runner-sync-report.json",
                    ]
                )
                + "\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run_operator_evidence)
    assert scheduler.verify_checkout(repo, "add/pages-repo-default") == "abc123"

    def fake_run_nearby_operator_path(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="## add/pages-repo-default\n?? ops/operator/runs/not-a-date/runner-sync.json\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run_nearby_operator_path)
    with pytest.raises(scheduler.SchedulerError, match="ops/operator/runs/not-a-date/runner-sync.json"):  # type: ignore[attr-defined]
        scheduler.verify_checkout(repo, "add/pages-repo-default")


def test_care_line_publication_proof_only_allows_generated_output_residue_but_not_source_drift() -> None:
    scheduler = _load_publication_scheduler_module(Path(__file__).resolve().parents[1])
    status_output = "\n".join(
        [
            "## add/pages-repo-default",
            " M output/site/assets/site.css",
            " M output/site/care-line/index.html",
            " D output/site/care-line/old.html",
            "?? output/dispatches/care-line/editions/2026-10-06/index.html",
            "?? output/site/care-line/editions/2026-10-06/sources_manifest.json",
            "?? logs/care-line/publication-scheduler/2026-10-06/proof.log",
            "?? status/care-line/publication-scheduler-runs/2026-10-06/proof.json",
            " M src/bluefern_dispatches/care_line_national_pipeline.py",
        ]
    )

    assert scheduler.unexpected_dirty_paths(
        status_output,
        allow_care_line_runtime=True,
        allow_generated_public_output_residue=True,
    ) == ["src/bluefern_dispatches/care_line_national_pipeline.py"]

    assert scheduler.unexpected_dirty_paths(
        status_output,
        allow_care_line_runtime=True,
        allow_generated_public_output_residue=False,
    ) == [
        "output/dispatches/care-line/editions/2026-10-06/index.html",
        "output/site/assets/site.css",
        "output/site/care-line/editions/2026-10-06/sources_manifest.json",
        "output/site/care-line/index.html",
        "output/site/care-line/old.html",
        "src/bluefern_dispatches/care_line_national_pipeline.py",
    ]


@pytest.mark.parametrize("status_code", ["M ", "D ", "A ", "MM"])
def test_care_line_publication_generated_output_staged_changes_remain_risky(status_code: str) -> None:
    scheduler = _load_publication_scheduler_module(Path(__file__).resolve().parents[1])

    assert scheduler.unexpected_dirty_paths(
        f"{status_code} output/site/care-line/index.html\n",
        allow_care_line_runtime=True,
        allow_generated_public_output_residue=True,
    ) == ["output/site/care-line/index.html"]


def test_care_line_publication_scheduler_limits_generated_output_residue_to_proof_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    scheduler = _load_publication_scheduler_module(Path(__file__).resolve().parents[1])
    calls: list[dict[str, object]] = []

    class DummyLock:
        stale_recovered = False

        def __init__(self, path: Path) -> None:
            self.path = path

        def acquire(self) -> str:
            return "acquired"

        def release(self) -> None:
            return None

    def fake_verify_repo(root: Path, **kwargs):  # noqa: ANN001
        calls.append(dict(kwargs))
        return {"head": "abc123", "remote_head": "abc123", "unexpected_dirty_paths": []}

    monkeypatch.setattr(scheduler, "SchedulerLock", DummyLock)
    monkeypatch.setattr(scheduler, "verify_repo", fake_verify_repo)
    monkeypatch.setattr(scheduler, "run_preflight", lambda _root, _pages_root: None)
    monkeypatch.setattr(scheduler, "discover_release_candidates", lambda *args: [])
    monkeypatch.setattr(scheduler, "_write_operational_health_receipt", lambda _root, _record: None)

    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()

    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        run_date="2026-10-06",
        run_id="proof",
        proof_only=True,
    )

    assert exit_code == 0
    assert receipt["status"] == "safe_no_op"
    assert [call["label"] for call in calls] == ["source repo", "Pages repo"]
    assert calls[0]["allow_generated_public_output_residue"] is True
    assert calls[1]["allow_generated_public_output_residue"] is False

    calls.clear()
    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        run_date="2026-10-06",
        run_id="publication",
        proof_only=False,
    )

    assert exit_code == 0
    assert receipt["status"] == "safe_no_op"
    assert [call["label"] for call in calls] == ["source repo", "Pages repo"]
    assert calls[0]["allow_generated_public_output_residue"] is False
    assert calls[1]["allow_generated_public_output_residue"] is False


def test_care_line_collection_scheduler_help_executes_from_other_cwd(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "care_line_collection_scheduler.py"), "--help"],
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
