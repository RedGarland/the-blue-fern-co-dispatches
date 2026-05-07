from __future__ import annotations

import json


def main() -> int:
    result = {
        "ok": False,
        "deprecated": True,
        "errors": [
            "This legacy Gaza importer is deprecated. Use scripts/run_gaza_dispatch.py with project-local source records."
        ],
    }
    print(json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
