from __future__ import annotations

from pathlib import Path

import importlib
import pytest

runner = importlib.import_module("scripts.run_food_line_signal_wire")


def test_publish_live_maps_arguments_and_requires_explicit_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_live(repo_root: Path, *, pages_repo: Path, source_branch: str, pages_branch: str, dry_run: bool, post_bluesky: bool):
        captured["repo_root"] = repo_root
        captured["pages_repo"] = pages_repo
        captured["source_branch"] = source_branch
        captured["pages_branch"] = pages_branch
        captured["dry_run"] = dry_run
        captured["post_bluesky"] = post_bluesky
        return {"ok": True}

    monkeypatch.setattr(runner, "run_signal_wire_live_publication", fake_live)
    result = runner.main(
        [
            "--repo-root",
            str(tmp_path),
            "--pages-repo",
            str(tmp_path / "pages"),
            "--source-branch",
            "agent/refine-care-line-signal-wire-public-rendering",
            "--pages-branch",
            "gh-pages",
            "--publish-live",
        ]
    )

    assert result == 0
    assert captured["repo_root"] == tmp_path
    assert captured["pages_repo"] == tmp_path / "pages"
    assert captured["source_branch"] == "agent/refine-care-line-signal-wire-public-rendering"
    assert captured["pages_branch"] == "gh-pages"
    assert captured["dry_run"] is False
    assert captured["post_bluesky"] is True


def test_wrapper_script_exposes_python_executable_contract() -> None:
    script = Path("scripts/windows/run_food_line_signal_wire.ps1").read_text(encoding="utf-8")
    assert "[string]$PythonExecutable" in script
    assert "Test-Path -LiteralPath $python -PathType Leaf" in script
    assert "Join-Path $RepoRoot \".venv\\Scripts\\python.exe\"" in script
