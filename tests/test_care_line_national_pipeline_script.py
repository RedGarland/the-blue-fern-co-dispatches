from __future__ import annotations

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
    assert "Care Line national pipeline" in result.stdout
