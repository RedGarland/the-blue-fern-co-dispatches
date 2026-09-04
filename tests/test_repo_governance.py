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


def test_merge_governance_requires_bounded_exact_head_policy() -> None:
    module = _load_validator_module()
    policy = {
        Path("policy.md"): """
Codex may merge a bounded routine source PR after synchronizing with the current protected base.
The exact PR head and every required check must be verified immediately before merge.
Human merge is required for authority-bearing or governance changes.
A source merge does not authorize publication or Pages activity.
Codex must never expand its own authority through routine merge permission.
""",
    }

    assert module._validate_merge_governance(policy) == []
    incomplete = {Path("policy.md"): policy[Path("policy.md")].replace("The exact PR head", "The reviewed revision")}
    errors = module._validate_merge_governance(incomplete)
    assert "Merge governance: missing required concept 'exact PR head'." in errors

    workflow = (
        Path(__file__).resolve().parents[1] / "docs" / "workflows" / "codex_pr_workflow.md"
    ).read_text(encoding="utf-8")
    assert "reviewed_head == head_immediately_before_merge" in workflow
    assert "--match-head-commit <EXACT_PR_HEAD>" in workflow
    assert "This governance PR is therefore `HUMAN_MERGE_REQUIRED`" in workflow


def test_merge_governance_rejects_obsolete_absolute_prohibitions() -> None:
    module = _load_validator_module()
    valid = """
Codex may merge a bounded routine source PR after synchronizing with the current protected base.
The exact PR head and every required check must be verified immediately before merge.
Human merge is required for authority-bearing or governance changes.
A source merge does not authorize publication or Pages activity.
Codex must never expand its own authority through routine merge permission.
"""
    obsolete = [
        "Codex does not merge PRs.",
        "Codex must not merge a PR.",
        "No AI agent may merge without explicit instruction.",
        "- merge a PR",
        "A human reviews the PR in GitHub and clicks Merge.",
    ]

    for statement in obsolete:
        errors = module._validate_merge_governance({Path("policy.md"): valid + statement})
        assert any("obsolete" in error for error in errors), statement


def test_production_readiness_governance_files_are_present_and_referenced() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = root / "docs" / "production-readiness-contract.md"
    template = root / "docs" / "templates" / "production-readiness-proof.md"
    agents_text = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert contract.exists()
    assert template.exists()
    assert "docs/production-readiness-contract.md" in agents_text


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


def test_dispatch_validation_workflow_bridges_validate_to_head_sha() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "dispatch-validation.yml").read_text(encoding="utf-8")

    assert "jobs:" in workflow
    assert "  validate:" in workflow
    assert "pull_request:" in workflow
    assert "types:" in workflow
    assert "- opened" in workflow
    assert "- synchronize" in workflow
    assert "- reopened" in workflow
    assert "- ready_for_review" in workflow
    assert "statuses: write" in workflow
    assert "Publish validate commit status for head SHA" in workflow
    assert "if: always() && github.event_name == 'pull_request'" in workflow
    assert "actions/github-script@v7" in workflow
    assert "pulls.get" in workflow
    assert "pull.head?.sha" in workflow
    assert "merge_commit_sha" not in workflow
