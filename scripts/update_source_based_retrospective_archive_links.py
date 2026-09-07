from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_sources import (  # noqa: E402
    DISPATCH_NAME as CARE_LINE_DISPATCH_NAME,
    DISPATCH_SLUG as CARE_LINE_DISPATCH_SLUG,
    DISPATCH_TAGLINE as CARE_LINE_DISPATCH_TAGLINE,
)
from bluefern_dispatches.generator import (  # noqa: E402
    CARE_LINE_PUBLIC_DESCRIPTION,
    DispatchConfig,
    discover_public_edition_dates,
    render_archive_for_dates,
)
from scripts.run_food_line_dispatch import refresh_food_line_archive_from_public_state  # noqa: E402


SUPPORTED_DISPATCHES = ("food-line", "care-line", "both")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _refresh_care_line_archive(root: Path, pages_root: Path) -> dict[str, Any]:
    site_root = root / "output" / "site"
    dates = discover_public_edition_dates(site_root, CARE_LINE_DISPATCH_SLUG, pages_repo=pages_root)
    if not dates:
        dates = discover_public_edition_dates(pages_root, CARE_LINE_DISPATCH_SLUG)
    latest = dates[0] if dates else ""
    dispatch = DispatchConfig(
        slug=CARE_LINE_DISPATCH_SLUG,
        name=CARE_LINE_DISPATCH_NAME,
        edition_date=latest,
        tagline=CARE_LINE_DISPATCH_TAGLINE,
        logo="care-line-logo.png",
        sources=[],
        stories=[],
        body_html=CARE_LINE_PUBLIC_DESCRIPTION,
        detail_artifacts=[],
    )
    archive_path = site_root / CARE_LINE_DISPATCH_SLUG / "archive.html"
    _write_text(
        archive_path,
        render_archive_for_dates(
            dispatch,
            dates,
            site_root,
            retrospective_pages_root=pages_root,
        ),
    )
    return {
        "archive_path": str(archive_path),
        "edition_dates": dates,
        "entry_count": len(dates),
    }


def refresh_archives(root: Path, pages_root: Path, dispatch: str) -> dict[str, Any]:
    if dispatch not in SUPPORTED_DISPATCHES:
        raise ValueError(f"unsupported dispatch: {dispatch}")
    result: dict[str, Any] = {}
    if dispatch in {"food-line", "both"}:
        result["food-line"] = refresh_food_line_archive_from_public_state(root, pages_root)
    if dispatch in {"care-line", "both"}:
        result["care-line"] = _refresh_care_line_archive(root, pages_root)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh Food/Care archive links for deployed retrospective recovery pages."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--pages-root", type=Path, required=True)
    parser.add_argument("--dispatch", choices=SUPPORTED_DISPATCHES, default="both")
    args = parser.parse_args(argv)
    result = refresh_archives(args.repo_root.resolve(), args.pages_root.resolve(), args.dispatch)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
