from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_wide_source_audit import main as wide_audit_main


def main(argv: list[str] | None = None) -> int:
    return wide_audit_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
