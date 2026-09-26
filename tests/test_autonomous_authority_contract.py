from __future__ import annotations

from pathlib import Path

from scripts import blue_fern_operator as operator


ROOT = Path(__file__).resolve().parents[1]


def _agents_text() -> str:
    return (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def _production_policy() -> operator.RemediationPolicy:
    return operator.load_remediation_policy(ROOT / "ops" / "operator" / "remediation-policy.yaml")


def test_root_agents_declares_autonomous_working_authority() -> None:
    text = _agents_text()

    assert "## Autonomous Working Authority" in text
    assert "absence of additional operator input is permission to continue" in text
    assert "Do not stop merely because an intermediate step is complete" in text
    assert "bounded routine protected-branch merge when the merge rules below permit it" in text
    assert "non-public production proof" in text
    assert "operational-status refresh" in text


def test_root_agents_documents_genuine_stop_boundaries() -> None:
    text = _agents_text()

    assert "### Stop Only At Genuine Authority Boundaries" in text
    for phrase in (
        "destructive or irreversible actions",
        "force push, destructive reset/clean, or evidence loss",
        "credentials, secrets, or security changes",
        "persistent host/network configuration changes",
        "public-content publication when explicit approval is required",
        "editorial-policy changes",
        "source removal or substitution that materially changes coverage",
    ):
        assert phrase in text
    assert "Do not request approval for routine code changes" in text


def test_root_agents_completion_rule_requires_deployment_or_proof_when_applicable() -> None:
    text = _agents_text()

    assert "### Completion Rule" in text
    assert "implemented, deployed where applicable, and proven at the strongest safe level available" in text
    assert "A locally working change or merged PR alone is not completion" in text
    assert "complete every independent safe step before escalating" in text


def test_authority_sources_distinguish_codex_from_operator_merge_authority() -> None:
    text = _agents_text()

    assert "`AGENTS.md` governs Codex and implementation-agent behavior" in text
    assert "`ops/operator/remediation-policy.yaml` governs the autonomous Blue Fern Operator" in text
    assert "Codex routine PR merge authority is not Operator merge authority" in text
    assert "The Operator must keep `MERGE_PR` approval-gated" in text
    assert "Codex must not use routine merge permission to expand its own authority" in text


def test_no_nested_agent_overrides_currently_redefine_root_authority() -> None:
    agent_files = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("AGENTS.md"))
    override_files = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("AGENTS.override.md"))

    assert agent_files == ["AGENTS.md"]
    assert override_files == []


def test_operator_policy_keeps_low_risk_actions_automatic() -> None:
    policy = _production_policy()

    assert policy.mode_for("REBUILD_STATUS") == "automatic"
    assert policy.mode_for("REFRESH_STATUS_EXPORT") == "automatic"
    assert policy.mode_for(operator.TRANSIENT_NETWORK_RETRY_ACTION) == "automatic"
    assert policy.mode_for(operator.SOURCE_TRANSIENT_RETRY_ACTION) == "automatic"
    assert policy.mode_for("ENGINEER_PREPARE_FIX") == "automatic_prepare_pr"


def test_operator_policy_keeps_publication_and_collection_replay_forbidden() -> None:
    policy = _production_policy()

    for action in ("PUBLISH_NO_UPDATE", "PUBLISH_APPROVED_RELEASE", "REPLAY_COLLECTION"):
        assert policy.mode_for(action) == "forbidden"
        assert action in operator.FORBIDDEN_REMEDIATION_ACTIONS


def test_unknown_security_or_destructive_actions_default_nonautomatic() -> None:
    policy = _production_policy()

    for action in (
        "ROTATE_CREDENTIALS",
        "DELETE_RUNTIME_STATE",
        "MUTATE_NETWORK_CONFIGURATION",
        "FORCE_PUSH_BRANCH",
        "UNKNOWN_REQUIRES_OPERATOR",
    ):
        assert policy.mode_for(action) != "automatic"


def test_operator_merge_pr_stays_approval_required_not_executable_or_automatic() -> None:
    policy = _production_policy()

    assert policy.mode_for("MERGE_PR") == "approval_required"
    assert "MERGE_PR" in operator.APPROVAL_REQUIRED_ACTIONS
    assert "MERGE_PR" not in operator.EXECUTABLE_REMEDIATION_ACTIONS
    assert "MERGE_PR" not in operator.AUTOMATIC_REMEDIATION_ACTIONS


def test_engineer_prepare_pr_does_not_grant_operator_merge_authority() -> None:
    policy = _production_policy()

    assert policy.mode_for("ENGINEER_PREPARE_FIX") == "automatic_prepare_pr"
    assert policy.mode_for("MERGE_PR") == "approval_required"
    assert "merge_pr" in (ROOT / "scripts" / "blue_fern_operator.py").read_text(encoding="utf-8")


def test_intermediate_success_never_creates_publication_permission() -> None:
    text = _agents_text()
    policy = _production_policy()

    assert "A source PR merge does not authorize Pages sync, publication" in text
    assert "Never infer publish permission from PR approval, test success, or dry-run success" in text
    assert policy.mode_for("PUBLISH_APPROVED_RELEASE") == "forbidden"
    assert policy.mode_for("PUBLISH_NO_UPDATE") == "forbidden"
