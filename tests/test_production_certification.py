from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.production_certification import (
    CertificationLevel,
    CertificationOptions,
    run_certification,
)


def _init_clean_source(root: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "cert@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Cert Test"], cwd=root, check=True)
    for relative in ("src", "scripts"):
        shutil.copytree(repo / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for relative in ("pyproject.toml", ".gitignore"):
        if (repo / relative).exists():
            (root / relative).write_text((repo / relative).read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "add", "src", "scripts", "pyproject.toml", ".gitignore"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("dispatch", ["food-line", "care-line", "ice"])
def test_level_one_certification_is_non_public_and_isolated(tmp_path: Path, dispatch: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _init_clean_source(source)
    proof = tmp_path / "proof"

    before = subprocess.run(["git", "status", "--porcelain=v1"], cwd=source, check=True, capture_output=True, text=True).stdout
    receipt = run_certification(CertificationOptions(dispatch=dispatch, source_root=source, proof_root=proof))
    after = subprocess.run(["git", "status", "--porcelain=v1"], cwd=source, check=True, capture_output=True, text=True).stdout

    assert receipt["schema_version"] == "bluefern_production_certification_v1"
    assert receipt["overall_status"] == "PASS"
    assert receipt["public_side_effects"] is False
    assert receipt["production_state_mutated"] is False
    assert before == after == ""
    assert (proof / "certification_receipt.json").is_file()
    serialized = json.dumps(receipt)
    assert str(source) not in serialized
    assert "private" not in serialized.lower()


def test_proof_root_must_not_be_source_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _init_clean_source(source)

    with pytest.raises(ValueError, match="proof root must be outside"):
        run_certification(CertificationOptions(dispatch="ice", source_root=source, proof_root=source))


def test_level_two_reports_unsupported_without_runtime_execution(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _init_clean_source(source)

    receipt = run_certification(
        CertificationOptions(
            dispatch="ice",
            source_root=source,
            proof_root=tmp_path / "proof",
            level=CertificationLevel.ISOLATED_RUNTIME_PROOF,
        )
    )

    stages = {row["stage"]: row for row in receipt["stages"]}
    assert stages["ISOLATED_EXECUTION"]["status"] == "UNSUPPORTED"
    assert "UNSUPPORTED_ISOLATED_EXECUTION" in stages["ISOLATED_EXECUTION"]["message"]
