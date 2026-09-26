from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bluefern_dispatches.care_line_source_registry import load_registry


ROOT = Path(__file__).resolve().parents[1]


def _tracked_paths(*prefixes: str) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--", *prefixes],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def test_current_project_docs_name_active_families_and_inactive_cascadia() -> None:
    summary = (ROOT / "PROJECT_SUMMARY.md").read_text(encoding="utf-8")
    notes = (ROOT / "docs" / "dispatches-project.md").read_text(encoding="utf-8")

    for slug in ("gaza", "food-line", "care-line", "ice", "american-pressure"):
        assert f"`{slug}`" in summary
        assert f"`{slug}`" in notes

    assert "cascadia" in summary.casefold()
    assert "intentionally inactive" in summary.casefold()
    assert "intentionally inactive" in notes.casefold()
    assert "Current generated edition dates:" not in notes


def test_legacy_agent_and_bootstrap_files_cannot_compete_with_current_authority() -> None:
    legacy_agent = (ROOT / ".agent.md").read_text(encoding="utf-8")
    bootstrap = (ROOT / "bluefern_dispatches_codex_prompt.txt").read_text(encoding="utf-8")

    assert "Deprecated Agent Reference" in legacy_agent
    assert "Do not use this file as the project authority" in legacy_agent
    assert "AGENTS.md" in legacy_agent

    assert bootstrap.startswith("DEPRECATED PROJECT BOOTSTRAP PROMPT")
    assert "AGENTS.md" in bootstrap
    assert "must not be treated as current instructions" in bootstrap


def test_care_registry_is_the_persisted_runtime_source_of_truth() -> None:
    registry_path = ROOT / "data" / "dispatches" / "care-line" / "source_registry.json"
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    raw_by_id = {row["source_id"]: row for row in raw["sources"]}
    loaded = load_registry(registry_path, include_disabled=True)
    loaded_by_id = {row.source_id: row for row in loaded.sources}

    expected = {
        "hhs-news": ("https://www.hhs.gov/press-room/index.html?page=0", "structured_index"),
        "hrsa-news": ("https://www.hrsa.gov/about/news/press-releases?page=1", "structured_index"),
        "calmatters-health": ("https://calmatters.org/category/health/", "structured_index"),
        "ct-mirror-health": ("https://ctmirror.org/health/", "structured_index"),
        "ohio-capital-journal-health": ("https://ohiocapitaljournal.com/category/health-care/", "structured_index"),
        "missouri-independent-health": ("https://missouriindependent.com/category/health-care/", "structured_index"),
        "michigan-advance-health": ("https://michiganadvance.com/category/health-care/", "structured_index"),
        "kaiser-permanente-news": ("https://about.kaiserpermanente.org/rss-feeds/main-rss", "rss"),
        "mayo-clinic-news": ("https://newsnetwork.mayoclinic.org/category/news-cycle/?pg=1", "rss"),
        "cleveland-clinic-newsroom": ("https://newsroom.clevelandclinic.org/news-releases", "structured_index"),
        "aha-news": ("https://www.aha.org/news", "structured_index"),
        "rural-health-info-hub": ("https://www.ruralhealthinfo.org/rss/news.xml", "rss"),
    }

    for source_id, (feed_url, adapter_type) in expected.items():
        assert raw_by_id[source_id]["feed_url"] == feed_url
        assert raw_by_id[source_id]["adapter_type"] == adapter_type
        assert loaded_by_id[source_id].feed_url == feed_url
        assert loaded_by_id[source_id].adapter_type == adapter_type

    source_code = (ROOT / "src" / "bluefern_dispatches" / "care_line_source_registry.py").read_text(encoding="utf-8")
    assert "_CARE_LINE_SOURCE_URL_OVERRIDES" not in source_code
    assert '"gu-dphss"' not in source_code


def test_operational_status_branch_has_dispatch_neutral_name_everywhere() -> None:
    config = json.loads((ROOT / "ops" / "operator" / "config.json").read_text(encoding="utf-8"))
    py = (ROOT / "scripts" / "run_operational_status_export.py").read_text(encoding="utf-8")
    ps1 = (ROOT / "scripts" / "run_operational_status_export.ps1").read_text(encoding="utf-8")

    assert config["operator"]["status_branch"] == "ops/status/current"
    assert 'DEFAULT_BRANCH = "ops/status/current"' in py
    assert "'ops/status/current'" in ps1
    assert "ops/status/food-line-2026-09-10" not in py
    assert "ops/status/food-line-2026-09-10" not in ps1


def test_generated_runtime_artifacts_are_not_tracked() -> None:
    assert _tracked_paths("output/test-runs") == []
    assert _tracked_paths("logs") == []
    for name in ("publish.log", "publish-dryrun.log", "run-output.json", "run-stderr.log"):
        assert not (ROOT / name).exists()

    assert not (ROOT / "ops" / "operator" / "known-failures.yaml").exists()


def test_pr_template_distinguishes_pages_sync_from_guarded_runner_sync() -> None:
    text = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

    assert "Pages/publication sync" in text
    assert "publish/sync action" not in text
    assert "Human release/publication approval remains required" in text


def test_operator_runner_roots_match_status_export_wrapper_defaults() -> None:
    config = json.loads((ROOT / "ops" / "operator" / "config.json").read_text(encoding="utf-8"))
    ps1 = (ROOT / "scripts" / "run_operational_status_export.ps1").read_text(encoding="utf-8")

    expected = {
        "food-line": "$SourceRoot",
        "care-line": "$CareSourceRoot",
        "gaza": "$GazaSourceRoot",
        "ice": "$IceSourceRoot",
    }
    for dispatch, parameter in expected.items():
        runner_root = config["dispatches"][dispatch]["runner_root"]
        assert runner_root in ps1, f"{dispatch} runner root drifted from {parameter}"

    assert config["operator"]["status_root"] in ps1


def test_current_docs_do_not_embed_personal_workstation_paths() -> None:
    files = [
        ROOT / "README.md",
        ROOT / "docs" / "dispatches-project.md",
    ]
    forbidden = (
        r"C:\\Users\\Admin\\Desktop\\Python",
        r"C:\\Users\\willb\\OneDrive\\Desktop\\Python",
        r"C:\\PythonProjects\\Dispatches From The Blue Fern Co",
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{path.relative_to(ROOT)} still contains {marker}"


def test_readme_lists_current_public_products_and_marks_cascadia_historical() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "https://dispatches.thebluefernco.com/gaza/" in text
    assert "https://dispatches.thebluefernco.com/food-line/" in text
    assert "https://dispatches.thebluefernco.com/care-line/" in text
    assert "https://dispatches.thebluefernco.com/cascadia/" in text
    assert "historical/inactive archive" in text


def test_required_runtime_dependencies_are_declared() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()

    for package in ("pyyaml", "pydantic", "pillow", "certifi", "beautifulsoup4", "tzdata"):
        assert package in requirements


def test_validation_workflow_targets_protected_source_branch() -> None:
    text = (ROOT / ".github" / "workflows" / "dispatch-validation.yml").read_text(encoding="utf-8")

    assert "      - add/pages-repo-default" in text
    assert "      - main" not in text


def test_readme_does_not_require_undefined_editable_package_install() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install -e ." not in text
    assert "pip install -r requirements.txt" in text


def test_validation_status_is_bound_to_tested_event_head() -> None:
    text = (ROOT / ".github" / "workflows" / "dispatch-validation.yml").read_text(encoding="utf-8")

    assert "context.payload.pull_request?.head?.sha" in text
    assert "github.rest.pulls.get" not in text
    assert "event pull_request.head.sha is unavailable" in text


def test_validation_cancels_superseded_pr_runs() -> None:
    text = (ROOT / ".github" / "workflows" / "dispatch-validation.yml").read_text(encoding="utf-8")

    assert "concurrency:" in text
    assert "github.event.pull_request.number || github.ref" in text
    assert "cancel-in-progress: true" in text


def test_readme_names_current_protected_source_branch() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "The protected source branch is `add/pages-repo-default`" in text
    assert "source project branch (`master` or `main`)" not in text


def test_live_operational_docs_and_helpers_do_not_depend_on_legacy_workstation_root() -> None:
    legacy_root = r"C:\\PythonProjects\\Dispatches From The Blue Fern Co"
    current_files = [
        ROOT / "docs" / "workflows" / "codex_pr_workflow.md",
        ROOT / "docs" / "runner-operations.md",
        ROOT / "docs" / "pages-publish-safety.md",
        ROOT / "docs" / "dispatch-archive-pages-preparation.md",
        ROOT / "docs" / "care-line-reviewed-event-queue-scheduler.md",
        ROOT / "scripts" / "status_pages_repo.py",
        ROOT / "run_food_line_daily.ps1",
        ROOT / "ops" / "generate_and_notify_task.xml",
        ROOT / "ops" / "run_american_pressure_weekly_task.xml",
        ROOT / "ops" / "run_cascadia_weekly_task.xml",
    ]

    for path in current_files:
        text = path.read_text(encoding="utf-8-sig")
        assert legacy_root not in text, f"{path.relative_to(ROOT)} still depends on the legacy workstation root"


def test_pages_status_helper_is_repo_relative_and_read_only() -> None:
    text = (ROOT / "scripts" / "status_pages_repo.py").read_text(encoding="utf-8")

    assert 'ROOT = Path(__file__).resolve().parents[1]' in text
    assert 'default=str(ROOT)' in text
    assert 'default=str(ROOT / "bluefern-dispatches-pages")' in text
    assert "git add american-pressure/" not in text
    assert "git commit -m" not in text
    assert "git push origin" not in text
    assert "does not stage, commit, or push any files" in text


def test_food_line_legacy_wrapper_derives_default_root_from_its_location() -> None:
    text = (ROOT / "run_food_line_daily.ps1").read_text(encoding="utf-8")

    assert "else { $PSScriptRoot }" in text
    assert "BLUEFERN_PROJECT_ROOT" in text
    assert r"C:\\PythonProjects\\Dispatches From The Blue Fern Co" not in text


def test_runner_operations_uses_current_topology_and_declared_dependencies() -> None:
    text = (ROOT / "docs" / "runner-operations.md").read_text(encoding="utf-8")

    for runner in (
        "BlueFernOperatorCurrent",
        "FoodLineCurrent6",
        "CareLineNationalCurrent8",
        "GazaDispatchesCurrent6",
        "ICEMonitorCurrent",
        "OperationalStatusCurrent",
    ):
        assert runner in text

    assert "pip install -r requirements.txt" in text
    assert "pip install -e ." not in text
    assert "Cascadia is intentionally inactive" in text
