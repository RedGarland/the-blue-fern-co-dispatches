from __future__ import annotations

from bluefern_dispatches.gaza_wide_source_audit import main as wide_audit_main


def main(argv: list[str] | None = None) -> int:
    return wide_audit_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
