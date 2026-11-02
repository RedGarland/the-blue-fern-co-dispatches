from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_gaza_production_adapter import (  # noqa: E402
    EXPLICIT_SCRIPT_FILES,
    GazaAdapterError,
    ProductionGazaConfig,
    build_source_manifest,
)


def _minimal_source(root: Path) -> tuple[Path, Path]:
    source = root / "source"
    pages = root / "pages"
    pages.mkdir()
    for relative in EXPLICIT_SCRIPT_FILES:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")
    return source, pages


def _config(tmp_path: Path, source: Path, pages: Path, artifact_root: Path) -> ProductionGazaConfig:
    manifest = build_source_manifest(source)
    return ProductionGazaConfig(
        source_root=source,
        pages_repo=pages,
        receipt_root=tmp_path / "receipts",
        artifact_root=artifact_root,
        scheduled_for="2026-11-02",
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        python_executable=Path(sys.executable),
        expected_source_head="0" * 40,
        expected_source_manifest_identity=manifest["identity"],
    )


def test_artifact_root_inside_source_checkout_is_rejected(tmp_path: Path) -> None:
    source, pages = _minimal_source(tmp_path)
    with pytest.raises(GazaAdapterError, match="artifact_root must be outside source_root"):
        _config(tmp_path, source, pages, source / "logs" / "gaza-contract").validate()


def test_artifact_root_equal_to_source_checkout_is_rejected(tmp_path: Path) -> None:
    source, pages = _minimal_source(tmp_path)
    with pytest.raises(GazaAdapterError, match="artifact_root must be outside source_root"):
        _config(tmp_path, source, pages, source.resolve()).validate()


def test_wrapper_routes_contract_artifacts_under_task_root() -> None:
    wrapper = (REPO_ROOT / "scripts" / "run_runner_dispatch.ps1").read_text(encoding="utf-8")
    old = '"--artifact-root", (Join-Path (Join-Path $RepoRoot "logs") "gaza-contract")'
    new = '"--artifact-root", (Join-Path $taskTestRoot "gaza-contract-artifacts")'
    assert old not in wrapper
    assert new in wrapper
    assert wrapper.index("$taskTestRoot = Set-GazaTaskScopedTestRoot") < wrapper.index(new)
