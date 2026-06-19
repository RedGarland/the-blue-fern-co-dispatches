from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    Path("AGENTS.md"),
    Path("docs/agent_workflow.md"),
    Path(".github/ISSUE_TEMPLATE/dispatch_task.yml"),
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/pull_request_template.md"),
]

AGENTS_PHRASES = [
    "prime directive",
    "source-traceable",
    "date-safe",
    "publication-safe",
    "dirty worktree rules",
    "do not publish or push",
    "files changed",
    "commands run",
    "test results",
    "intentionally not touched",
]

WORKFLOW_PHRASES = [
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Human-error risks this workflow is designed to reduce",
    "stale source leakage",
    "future edition",
    "accidental Pages sync",
    "unrelated dirty files",
    "generated artifact drift",
]

PR_TEMPLATE_PHRASES = [
    "Summary",
    "Dispatch Family Affected",
    "Edition Date Affected",
    "Files Changed",
    "Commands Run",
    "Tests Passed",
    "Source Traceability Checked",
    "Stale-source leakage",
    "Future-edition leakage",
    "Deprecated labels",
    "Pages repo / Publish Impact",
    "Publish/deploy impact",
    "Unrelated dirty files",
    "Risks / Follow-up",
    "This PR does not publish unless explicitly stated.",
    "This PR does not include unrelated dirty files.",
    "Generated artifacts were only changed when required by the task.",
]

TEMPLATE_EXPECTATIONS = {
    Path(".github/ISSUE_TEMPLATE/dispatch_task.yml"): {
        "required_text": [
            "dispatch family",
            "edition date",
            "source safety",
            "expected artifacts",
            "tests",
            "publish/deploy expectations",
            "acceptance criteria",
        ],
    },
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"): {
        "required_text": [
            "dispatch family",
            "edition date",
            "acceptance criteria",
            "should publishing be blocked?",
        ],
    },
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _contains_all(text: str, phrases: list[str]) -> list[str]:
    lowered = text.lower()
    missing = []
    for phrase in phrases:
        if phrase.lower() not in lowered:
            missing.append(phrase)
    return missing


def _yaml_load(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    return yaml.safe_load(text)


def _validate_template_structure_from_yaml(path: Path, data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{path}: top-level YAML document must be a mapping."]

    for key in ("name", "description", "title", "labels", "body"):
        if key not in data:
            errors.append(f"{path}: missing required top-level key '{key}'.")

    body = data.get("body")
    if not isinstance(body, list) or not body:
        errors.append(f"{path}: 'body' must be a non-empty list.")
        return errors

    if not isinstance(data.get("labels"), list) or not data["labels"]:
        errors.append(f"{path}: 'labels' must be a non-empty list.")

    for index, item in enumerate(body, start=1):
        if not isinstance(item, dict):
            errors.append(f"{path}: body item {index} must be a mapping.")
            continue

        item_type = item.get("type")
        if not item_type:
            errors.append(f"{path}: body item {index} is missing 'type'.")

        if "attributes" not in item or not isinstance(item["attributes"], dict):
            errors.append(f"{path}: body item {index} is missing 'attributes'.")

        if "id" not in item:
            errors.append(f"{path}: body item {index} is missing 'id'.")

        validations = item.get("validations")
        if not isinstance(validations, dict) or "required" not in validations:
            errors.append(f"{path}: body item {index} is missing validations.required.")

        if item_type == "dropdown":
            options = item.get("attributes", {}).get("options")
            if not isinstance(options, list) or not options:
                errors.append(f"{path}: dropdown body item {index} must define non-empty options.")

    return errors


def _validate_template_structure_from_text(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for key in ("name", "description", "title", "labels", "body"):
        if re.search(rf"^{re.escape(key)}:\s*", text, re.M) is None:
            errors.append(f"{path}: missing required top-level key '{key}'.")

    labels_match = re.search(r"^labels:\s*\n(?P<body>(?:\s*-\s+.+\n?)+)", text, re.M)
    if labels_match is None:
        errors.append(f"{path}: 'labels' must contain at least one item.")

    body_start = text.find("body:")
    if body_start < 0:
        errors.append(f"{path}: missing body section.")
        return errors

    body_text = text[body_start + len("body:") :]
    body_items = list(re.finditer(r"(?m)^\s*-\s+type:\s*(?P<type>[^\n]+)\s*$", body_text))
    if not body_items:
        errors.append(f"{path}: 'body' must contain at least one item.")
        return errors

    for index, match in enumerate(body_items, start=1):
        start = match.start()
        end = body_items[index].start() if index < len(body_items) else len(body_text)
        block = body_text[start:end]
        item_type = match.group("type").strip()

        if re.search(r"(?m)^\s+id:\s*\S+", block) is None:
            errors.append(f"{path}: body item {index} is missing 'id'.")
        if re.search(r"(?m)^\s+attributes:\s*$", block) is None:
            errors.append(f"{path}: body item {index} is missing 'attributes'.")
        if re.search(r"(?m)^\s+validations:\s*$", block) is None:
            errors.append(f"{path}: body item {index} is missing validations.required.")
        elif re.search(r"(?ms)^\s+validations:\s*\n(?:\s+.*\n)*?\s+required:\s*(true|false)\s*$", block) is None:
            errors.append(f"{path}: body item {index} is missing validations.required.")
        if item_type == "dropdown":
            if re.search(r"(?ms)^\s+options:\s*\n(?:\s+-\s+.*\n?)+", block) is None:
                errors.append(f"{path}: dropdown body item {index} must define non-empty options.")

    return errors


def _validate_issue_template(path: Path, required_text: list[str]) -> list[str]:
    errors: list[str] = []
    text = _read_text(path)
    missing = _contains_all(text, required_text)
    if missing:
        errors.extend(f"{path}: missing expected text '{phrase}'." for phrase in missing)

    parsed = _yaml_load(text)
    if parsed is not None:
        errors.extend(_validate_template_structure_from_yaml(path, parsed))
    else:
        errors.extend(_validate_template_structure_from_text(path, text))

    return errors


def validate_repo_governance(repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        if not (root / relative).exists():
            errors.append(f"Missing required file: {relative}")

    agents_path = root / "AGENTS.md"
    if agents_path.exists():
        agents_text = _read_text(agents_path)
        errors.extend(f"AGENTS.md: missing expected text '{phrase}'." for phrase in _contains_all(agents_text, AGENTS_PHRASES))

    workflow_path = root / "docs/agent_workflow.md"
    if workflow_path.exists():
        workflow_text = _read_text(workflow_path)
        errors.extend(
            f"docs/agent_workflow.md: missing expected text '{phrase}'."
            for phrase in _contains_all(workflow_text, WORKFLOW_PHRASES)
        )

    pr_template_path = root / ".github/pull_request_template.md"
    if pr_template_path.exists():
        pr_text = _read_text(pr_template_path)
        errors.extend(
            f".github/pull_request_template.md: missing expected text '{phrase}'."
            for phrase in _contains_all(pr_text, PR_TEMPLATE_PHRASES)
        )

    for relative, spec in TEMPLATE_EXPECTATIONS.items():
        path = root / relative
        if path.exists():
            errors.extend(_validate_issue_template(path, spec["required_text"]))

    return errors


def main() -> int:
    errors = validate_repo_governance()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("Repository governance validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
