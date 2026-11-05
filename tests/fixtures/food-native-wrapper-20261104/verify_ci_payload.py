"""Read-only source checks or strict path-free synthetic result export.

This does not execute PowerShell or import any application code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

BASE = "3f58326266abc49546731b791b9cfad3d0d44393"
ROOT = Path(__file__).resolve().parent
PINS = {
    "candidate.ps1": (22152, "59422d8995cd68b33b41d7c51f6541b01c85f67ebc2f603b379c8513d55dc057"),
    "instrumented-candidate.ps1": (23254, "45e600cac8ae4b1497fab417fcda39a4173ee4f8cedb539d2495866f13300194"),
    "fixture-gate.ps1": (1043, "960856ffbf395633e3bf494ea6113eb0f3d9cfd8673e61380f433ec43fc5ae58"),
    "prepare_fixture.py": (2110, "10483145ded954345104a8e3021d34049c1afce8175ee4224acb40852176161d"),
    "preparation-manifest.json": (820, "45ee5a10915dc2ab914ddbff727aa66aba383d1358bd70ef75e053e693c2341a"),
    "instrumentation.patch": (2830, "97b6ddc026e0437071123c2f22234056e129d2c0c382bcc86bd086463c5e2802"),
    "companion-static-test-correction.patch": (2499, "c54c7272cbd52504e1e7381191951396f8262be54c4d893e5f585e64917bb36a"),
    "test-real-wrapper.ps1": (19481, "5734ef07670e71d0f36231156a5ec48367478ceaada7acc256feb59b5a6b93b7"),
}
SCENARIOS = [
    "missing-head", "invalid-head", "capped-complete", "capped-pending",
    "explicit-complete", "explicit-pending", "explicit-missing-runtime",
    "wrong-source", "mixed-source", "missing-plan", "wrong-resume",
    "mixed-resume", "source-exit-one", "sync-failure",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify() -> None:
    require(sys.flags.optimize == 0, "Optimized Python is not admitted")
    for name, (length, digest) in PINS.items():
        value = (ROOT / name).read_bytes()
        require(len(value) == length, f"Length mismatch: {name}")
        require(hashlib.sha256(value).hexdigest() == digest, f"Hash mismatch: {name}")
    original = (ROOT / "candidate.ps1").read_bytes().decode("utf-8-sig")
    instrumented = (ROOT / "instrumented-candidate.ps1").read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    gate = (ROOT / "fixture-gate.ps1").read_text().replace("\n", newline)
    anchor = gate + newline + "Set-StrictMode -Version 3.0"
    require(instrumented.count(anchor) == 1, "Unique fixture gate missing")
    reverse = instrumented.replace(anchor, "Set-StrictMode -Version 3.0", 1)
    for before, after in [
        ("$FixtureState.ExitCode = [int]$ExitCode; return", "exit $ExitCode"),
        ("$remote = Invoke-FixtureGit ls-remote origin", "$remote = & git ls-remote origin"),
        ("$process = Invoke-FixtureChildProcess -FilePath", "$process = Start-Process -FilePath"),
    ]:
        require(reverse.count(before) == 1, "Unique instrumentation boundary missing")
        reverse = reverse.replace(before, after, 1)
    require(reverse == original, "Reverse instrumentation differs from exact source")
    print("Pinned fixture and companion patch verified; no native test executed by this check.")


def summarize(parent: Path, host: str, destination: Path) -> None:
    results = list(parent.glob("food-wrapper-control-flow-*/native-result.json"))
    require(len(results) == 1, "Expected exactly one native-result.json")
    raw = json.loads(results[0].read_text(encoding="utf-8-sig"))
    require(raw.get("status") == "passed", "Native fixture did not report pass")
    require(raw.get("candidate_sha256") == PINS["candidate.ps1"][1], "Result candidate pin mismatch")
    require(raw.get("instrumented_sha256") == PINS["instrumented-candidate.ps1"][1], "Result instrumentation pin mismatch")
    scenarios = raw.get("scenarios", [])
    require([s.get("name") for s in scenarios] == SCENARIOS, "Scenario list/count differs")
    require(all(s.get("status") == "passed" for s in scenarios), "A scenario failed")
    for key in ("external_child_processes_invoked", "git_executable_invoked", "providers_invoked", "production_paths_touched"):
        require(raw.get(key) is False, f"Unexpected effect marker: {key}")
    mutations = raw.get("mutation_evidence", [])
    require(raw.get("mutation_tests_killed") == 2, "Mutation count differs")
    require([m.get("name") for m in mutations] == ["removed-head-guard", "downstream-call"], "Mutation identities differ")
    require(all(m.get("reason") == "actual_boundary_call_order" and m.get("message", "").startswith("Assertion failed: Actual boundary call order;") for m in mutations), "Mutation failure was not attributable")
    # Deliberate allowlist: do not export raw paths, command arguments or receipts.
    safe = {
        "host": host,
        "status": "passed",
        "protected_base": BASE,
        "candidate_sha256": raw["candidate_sha256"],
        "instrumented_sha256": raw["instrumented_sha256"],
        "scenario_names": SCENARIOS,
        "scenario_count": len(SCENARIOS),
        "mutation_names": [m["name"] for m in mutations],
        "mutation_reason": "actual_boundary_call_order",
        "native_child_processes_exercised": False,
        "providers_exercised": False,
        "production_exercised": False,
        "scope": "Instrumented wrapper control flow only; original OS exit and child implementations not exercised.",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
    print(f"{host}: 14 scenarios and two attributable mutations passed; path-free summary saved.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--host", choices=("pwsh", "powershell51"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verify()
    if args.summarize:
        require(args.host is not None and args.output is not None, "Summary host/output required")
        summarize(args.summarize, args.host, args.output)


if __name__ == "__main__":
    main()
