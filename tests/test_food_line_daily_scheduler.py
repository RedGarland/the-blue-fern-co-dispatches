from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "scripts" / "food_line_daily_scheduler.py"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def test_food_line_daily_scheduler_imports_without_pythonpath_injection(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import scripts.food_line_daily_scheduler as m; print('IMPORT_OK')"],
        cwd=ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "IMPORT_OK" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr


def test_food_line_daily_scheduler_help_executes_from_other_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCHEDULER), "--help"],
        cwd=tmp_path,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    combined = completed.stdout + completed.stderr
    assert "usage:" in combined.lower()
    assert "ModuleNotFoundError" not in combined
