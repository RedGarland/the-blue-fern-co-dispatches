from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.food_line_operator_recovery_release_authorization import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
