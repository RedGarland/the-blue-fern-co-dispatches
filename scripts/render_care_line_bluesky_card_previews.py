from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bluefern_dispatches.care_line_social_cards import (
    CARD_HEIGHT,
    CARD_WIDTH,
    care_line_private_card_path,
    render_social_card_png_bytes,
    social_card_spec_for_edition,
)
from bluefern_dispatches.care_line_bluesky import public_url_for_edition


PRIVATE_CARE_CARD_PREVIEWS: tuple[dict[str, str], ...] = (
    {
        "edition_date": "2026-10-06",
        "sheet_label": "Bradford / The Pavilion",
        "subtitle": "Source-backed briefing on U.S. health-care access",
    },
    {
        "edition_date": "2026-09-18",
        "sheet_label": "UPMC",
        "subtitle": "Source-backed briefing on U.S. health-care access",
    },
    {
        "edition_date": "2026-09-10",
        "sheet_label": "Texas Medicaid",
        "subtitle": "Source-backed briefing on U.S. health-care access",
    },
)


def _font(size: int, *, bold: bool = False) -> Any:
    from PIL import ImageFont  # type: ignore

    for name in (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_private_preview_cards(project_root: Path) -> list[Path]:
    project_root = project_root.resolve()
    rendered_paths: list[Path] = []
    for preview in PRIVATE_CARE_CARD_PREVIEWS:
        edition_date = preview["edition_date"]
        spec = social_card_spec_for_edition(edition_date=edition_date, public_url=public_url_for_edition(edition_date))
        spec["subtitle"] = preview["subtitle"]
        output_path = care_line_private_card_path(project_root, edition_date)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(render_social_card_png_bytes(spec))
        rendered_paths.append(output_path)
    return rendered_paths


def render_contact_sheet(project_root: Path, card_paths: list[Path], output_path: Path) -> Path:
    from PIL import Image, ImageDraw  # type: ignore

    scale = 0.37
    thumb_width = int(CARD_WIDTH * scale)
    thumb_height = int(CARD_HEIGHT * scale)
    gutter = 28
    top = 70
    label_y = 20
    canvas_width = gutter + len(card_paths) * thumb_width + (len(card_paths) - 1) * gutter + gutter
    canvas_height = top + thumb_height + 28
    canvas = Image.new("RGB", (canvas_width, canvas_height), "#e8f0f3")
    draw = ImageDraw.Draw(canvas)
    label_font = _font(18, bold=True)

    for index, (card_path, preview) in enumerate(zip(card_paths, PRIVATE_CARE_CARD_PREVIEWS, strict=True)):
        x = gutter + index * (thumb_width + gutter)
        draw.text((x, label_y), preview["sheet_label"], font=label_font, fill="#0f2c33")
        card = Image.open(card_path).convert("RGB").resize((thumb_width, thumb_height))
        canvas.paste(card, (x, top))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render private Care Line Bluesky card previews and a contact sheet.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--contact-sheet",
        type=Path,
        default=Path("output") / "review" / "care-line-bluesky-card-contact-sheet-20261008-still-life-v1.png",
    )
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    card_paths = render_private_preview_cards(project_root)
    contact_sheet = args.contact_sheet
    if not contact_sheet.is_absolute():
        contact_sheet = project_root / contact_sheet
    render_contact_sheet(project_root, card_paths, contact_sheet)
    print("Rendered private Care Line card previews:")
    for path in card_paths:
        print(f"- {path.relative_to(project_root).as_posix()}")
    print(f"Contact sheet: {contact_sheet.relative_to(project_root).as_posix()}")
    print("Approvals unchanged: false; no public post or Pages push performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
