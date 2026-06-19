from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_validator_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_repo_governance.py"
    spec = importlib.util.spec_from_file_location("validate_repo_governance", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_issue_template(path: Path, title_label: str) -> None:
    path.write_text(
        f"""name: {title_label}
description: Minimal validation fixture
title: "[Test] "
labels:
  - codex-task
body:
  - type: input
    id: example_field
    attributes:
      label: Example field
      description: Example description
    validations:
      required: true
""",
        encoding="utf-8",
    )


def test_validate_repo_governance_passes_against_repo_root() -> None:
    module = _load_validator_module()
    errors = module.validate_repo_governance(Path(__file__).resolve().parents[1])
    assert errors == []


def test_validate_repo_governance_reports_missing_required_file(tmp_path) -> None:
    module = _load_validator_module()

    root = tmp_path / "repo"
    root.mkdir()
    (root / "docs").mkdir()
    (root / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)

    (root / "AGENTS.md").write_text("prime directive source-traceable date-safe publication-safe dirty worktrees do not publish do not push files changed commands run test results intentionally not touched", encoding="utf-8")
    (root / "docs" / "agent_workflow.md").write_text(
        "Phase 1 Phase 2 Phase 3 Phase 4 Human-error risks this workflow is designed to reduce stale source leakage future edition accidental Pages sync unrelated dirty files generated artifact drift",
        encoding="utf-8",
    )
    _write_minimal_issue_template(root / ".github" / "ISSUE_TEMPLATE" / "dispatch_task.yml", "Dispatch task")
    _write_minimal_issue_template(root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml", "Dispatch bug report")
    (root / ".github" / "pull_request_template.md").write_text(
        "Summary Dispatch Family Affected Edition Date Affected Files Changed Commands Run Tests Passed Source Traceability Checked Stale-source leakage Future-edition leakage Deprecated labels Pages Repo / Publish Impact Publish/deploy impact Unrelated dirty files Risks / Follow-up This PR does not publish unless explicitly stated. This PR does not include unrelated dirty files. Generated artifacts were only changed when required by the task.",
        encoding="utf-8",
    )

    (root / "AGENTS.md").unlink()
    errors = module.validate_repo_governance(root)
    assert any("Missing required file: AGENTS.md" in error for error in errors)
