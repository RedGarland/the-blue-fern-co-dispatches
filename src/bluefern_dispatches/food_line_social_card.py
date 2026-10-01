from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


CARD_WIDTH = 1200
CARD_HEIGHT = 630
CARD_RELATIVE_PATH_TEMPLATE = "output/site/food-line/editions/{edition_date}/social-card.png"
TEMPLATE_RELATIVE_PATH = Path("assets/food-line-dispatch-social.png")


def food_line_social_card_relative_path(edition_date: str) -> Path:
    date.fromisoformat(edition_date)
    return Path(CARD_RELATIVE_PATH_TEMPLATE.format(edition_date=edition_date))


def food_line_social_card_path(project_root: Path, edition_date: str) -> Path:
    return project_root / food_line_social_card_relative_path(edition_date)


def _display_date(edition_date: str) -> str:
    parsed = date.fromisoformat(edition_date)
    return f"{parsed.strftime('%B').upper()} {parsed.day}, {parsed.year}"


def _font(size: int, *, bold: bool = False) -> Any:
    from PIL import ImageFont  # type: ignore

    names = (
        "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover_template_date(draw: Any) -> None:
    # These coordinates are for the resized 1200x630 locked Food Line template.
    # The old date lives in this open background band; the mask avoids touching
    # the gold separator above it or the publisher footer below it.
    left, top, right, bottom = 445, 405, 755, 454
    for y in range(top, bottom):
        blend = (y - top) / max(1, bottom - top)
        r = int(10 + 4 * blend)
        g = int(28 + 7 * blend)
        b = int(18 + 4 * blend)
        draw.line((left, y, right, y), fill=(r, g, b))
    # Add a little deterministic texture so the patch does not read as a flat box.
    for x in range(left, right, 5):
        y = top + ((x * 37) % (bottom - top))
        shade = 23 + ((x * 11) % 14)
        draw.point((x, y), fill=(shade, shade + 22, shade + 8))


def render_food_line_social_card_png_bytes(edition_date: str, *, template_path: Path | None = None) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw  # type: ignore

    date.fromisoformat(edition_date)
    if template_path is None or not template_path.exists():
        raise FileNotFoundError(template_path or TEMPLATE_RELATIVE_PATH)

    image = Image.open(template_path).convert("RGB").resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    _cover_template_date(draw)

    display_date = _display_date(edition_date)
    date_font = _font(34, bold=True)
    bbox = draw.textbbox((0, 0), display_date, font=date_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (CARD_WIDTH - text_width) / 2
    y = 421 - text_height / 2
    draw.text((x + 2, y + 2), display_date, font=date_font, fill="#5e461c")
    draw.text((x, y), display_date, font=date_font, fill="#d4a53f")

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def ensure_food_line_social_card(project_root: Path, edition_date: str, *, refresh_existing: bool = False) -> Path:
    path = food_line_social_card_path(project_root, edition_date)
    if path.exists() and not refresh_existing:
        return path
    rendered = render_food_line_social_card_png_bytes(edition_date, template_path=project_root / TEMPLATE_RELATIVE_PATH)
    if path.exists() and path.read_bytes() == rendered:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rendered)
    return path
