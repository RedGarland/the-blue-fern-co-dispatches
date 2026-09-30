from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


CARD_WIDTH = 1200
CARD_HEIGHT = 630
CARD_RELATIVE_PATH_TEMPLATE = "output/site/food-line/editions/{edition_date}/social-card.png"


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
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_food_line_social_card_png_bytes(edition_date: str) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw  # type: ignore

    date.fromisoformat(edition_date)
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#102018")
    draw = ImageDraw.Draw(image)

    # Layer simple vector-like shapes so the generated card is deterministic and
    # independent of the old static social image.
    draw.rectangle((42, 42, CARD_WIDTH - 42, CARD_HEIGHT - 42), outline="#b7c0a7", width=3)
    draw.rectangle((64, 64, CARD_WIDTH - 64, CARD_HEIGHT - 64), outline="#435846", width=2)
    draw.ellipse((-150, 390, 260, 800), fill="#233528")
    draw.polygon([(780, 375), (1110, 315), (1115, 475), (800, 500)], fill="#26372c")

    title_font = _font(78, bold=True)
    subtitle_font = _font(34)
    date_font = _font(34, bold=True)
    brand_font = _font(26)

    title = "Food Line Dispatch"
    subtitle = "Source-backed daily briefing\non U.S. food pressure"
    display_date = _display_date(edition_date)

    draw.text((180, 182), title, font=title_font, fill="#f0eadc")
    draw.multiline_text((252, 285), subtitle, font=subtitle_font, fill="#d9d1bd", spacing=8, align="center")
    draw.text((454, 386), display_date, font=date_font, fill="#caa45f")
    draw.text((454, 455), "The Blue Fern Co.", font=brand_font, fill="#d9d1bd")
    draw.line((188, 152, 430, 152), fill="#556b58", width=2)
    draw.line((770, 152, 1012, 152), fill="#556b58", width=2)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def ensure_food_line_social_card(project_root: Path, edition_date: str) -> Path:
    path = food_line_social_card_path(project_root, edition_date)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_food_line_social_card_png_bytes(edition_date))
    return path
