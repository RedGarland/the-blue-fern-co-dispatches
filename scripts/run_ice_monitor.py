from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for candidate in (REPO_ROOT, SRC):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from bluefern_dispatches.ice_monitor import main

if __name__ == "__main__":
    raise SystemExit(main())
