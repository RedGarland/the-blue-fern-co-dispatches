from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_dispatch_ops_phase2b.ps1"
README = ROOT / "README.md"


EXPECTED_CASES = [
    {
        "Label": "Food",
        "RunnerRoot": r"C:\BlueFernRunner\FoodLineCurrent6",
        "Dispatch": "food-line",
        "Date": "2026-09-09",
        "ExpectedOutcome": "NO_ACTION",
        "ExpectedExitCode": "0",
    },
    {
        "Label": "Care",
        "RunnerRoot": r"C:\BlueFernRunner\CareLineNationalCurrent8",
        "Dispatch": "care-line",
        "Date": "2026-09-18",
        "ExpectedOutcome": "REFUSED",
        "ExpectedExitCode": "3",
    },
    {
        "Label": "Gaza",
        "RunnerRoot": r"C:\BlueFernRunner\GazaDispatchesCurrent6",
        "Dispatch": "gaza",
        "Date": "2026-09-19",
        "ExpectedOutcome": "NO_ACTION",
        "ExpectedExitCode": "0",
    },
    {
        "Label": "ICE",
        "RunnerRoot": r"C:\BlueFernRunner\ICEMonitorCurrent",
        "Dispatch": "ice",
        "Date": "2026-09-17",
        "ExpectedOutcome": "NO_ACTION",
        "ExpectedExitCode": "0",
    },
]


def _read_script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _case_block(text: str, label: str) -> str:
    match = re.search(
        rf"\[ordered\]@\{{\s+Label = \"{re.escape(label)}\"(?P<body>.*?)(?=\n    \[ordered\]@\{{|\n\))",
        text,
        flags=re.DOTALL,
    )
    assert match, f"missing case block for {label}"
    return match.group(0)


def test_phase2b_helper_has_exact_approved_cases() -> None:
    text = _read_script()

    assert text.count("Label = ") == 4
    for case in EXPECTED_CASES:
        block = _case_block(text, case["Label"])
        for key, value in case.items():
            if key == "ExpectedExitCode":
                assert f"{key} = {value}" in block
            else:
                assert f'{key} = "{value}"' in block


def test_phase2b_helper_builds_only_approved_apply_command() -> None:
    text = _read_script()

    for token in [
        '"recover"',
        '"--date"',
        '"--apply"',
        '"--confirm"',
        '"VERIFY_PUBLIC_STATE"',
        '"--json"',
    ]:
        assert token in text
    forbidden_commands = [" fetch ", " merge ", " stash ", " reset ", " clean ", " push ", " publish "]
    lowered = text.lower()
    for command in forbidden_commands:
        assert command not in lowered


def test_phase2b_helper_has_no_angle_placeholder_commands() -> None:
    text = _read_script()

    assert "<dispatch>" not in text
    assert "<date>" not in text
    assert "recover <" not in text
    assert "--date <" not in text


def test_phase2b_docs_warn_against_powershell_angle_placeholders() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "PowerShell interprets `<` and `>` as shell syntax" in readme
    assert "DISPATCH_NAME" in readme
    assert "YYYY-MM-DD" in readme
    assert "recover <dispatch>" not in readme
    assert "--date <date>" not in readme
