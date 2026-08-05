from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_COMMIT = "55da006c6f45d21a411472cebcc1c36f581f2edc"
ARTIFACT_COMMIT = "0068ebeaaf4daecd06df69a00e248da28255846f"
PHASE_E_BASE = "56e134f084e7b0106d86cba97bfd5256bc5ec990"


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_aug05_release_manifest_uses_artifact_commit_and_linear_ancestry() -> None:
    release_manifest = json.loads(
        (ROOT / "data/dispatches/care-line/review/releases/2026-08-05.json").read_text(encoding="utf-8")
    )

    assert release_manifest["schema_version"] == "care_line_release_manifest_v1"
    assert release_manifest["dispatch"] == "care-line"
    assert release_manifest["edition_date"] == "2026-08-05"
    assert release_manifest["source_commit"] == ARTIFACT_COMMIT
    assert _git_output("rev-parse", f"{RELEASE_COMMIT}^") == ARTIFACT_COMMIT
    assert _git_output("rev-parse", f"{ARTIFACT_COMMIT}^") == PHASE_E_BASE

    source_commit = release_manifest["source_commit"]
    for entry in release_manifest["entries"]:
        file_bytes = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "--filters", f"{source_commit}:{entry['source_path']}"],
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(file_bytes).hexdigest() == entry["source_sha256"]
        assert _sha256(ROOT / entry["source_path"]) == entry["source_sha256"]

    approved_proposal = ROOT / release_manifest["approved_proposal_path"]
    review_snapshot = ROOT / release_manifest["review_snapshot_path"]
    assert _sha256(approved_proposal) == release_manifest["approved_proposal_sha256"]
    assert _sha256(review_snapshot) == release_manifest["review_snapshot_sha256"]
