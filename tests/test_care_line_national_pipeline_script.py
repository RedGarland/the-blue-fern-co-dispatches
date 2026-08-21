from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_care_line_national_pipeline_script_is_present_and_importable() -> None:
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "run_care_line_national_pipeline.py"
    assert script.is_file()

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--collection-only" in result.stdout
    assert "--run-date" in result.stdout


def test_care_line_national_pipeline_script_handles_utf8_json_on_cp1252_stdout() -> None:
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import scripts.run_care_line_national_pipeline as m; "
        "m.run_national_pipeline = lambda *args, **kwargs: {"
        "'ok': True, "
        "'run_manifest': {'status': 'success'}, "
        "'message': 'unicode narrow no-break space \\u202f works'"
        "}; "
        "raise SystemExit(m.main(['--repo-root', r'"
        + str(repo).replace("\\", "\\\\")
        + "', '--run-date', '2026-08-21', '--collection-only']))"
    )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unicode narrow no-break space" in result.stdout
    assert "\\u202f" not in result.stdout
