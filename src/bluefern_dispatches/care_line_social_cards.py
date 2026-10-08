from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from textwrap import wrap
from typing import Any


CARD_WIDTH = 1200
CARD_HEIGHT = 630
PRIVATE_CARD_FILENAME = "care-line-social-card.png"
BASE_URL = "https://dispatches.thebluefernco.com"


def care_line_private_card_relative_path(edition_date: str) -> Path:
    date.fromisoformat(edition_date)
    return Path("data") / "dispatches" / "care-line" / "review" / "bluesky-preview" / edition_date / PRIVATE_CARD_FILENAME


def care_line_private_card_path(project_root: Path, edition_date: str) -> Path:
    return project_root / care_line_private_card_relative_path(edition_date)


def _display_date(edition_date: str) -> str:
    parsed = date.fromisoformat(edition_date)
    return f"{parsed.strftime('%B').upper()} {parsed.day}, {parsed.year}"


def _font(size: int, *, serif: bool = False, bold: bool = False) -> Any:
    from PIL import ImageFont  # type: ignore

    if serif:
        names = (
            "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        )
    else:
        names = (
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered_text(draw: Any, y: int, text: str, *, font: Any, fill: str, shadow: str | None = None) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (CARD_WIDTH - (bbox[2] - bbox[0])) / 2
    if shadow:
        draw.text((x + 2, y + 2), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_leaf_medallion(draw: Any) -> None:
    cx, cy, r = CARD_WIDTH // 2, 116, 48
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#102a31", outline="#8bb4ad", width=2)
    draw.ellipse((cx - 38, cy - 38, cx + 38, cy + 38), outline="#d8c086", width=1)
    leaf_fill = "#9ec8bb"
    stem = "#d8c086"
    draw.line((cx, cy + 26, cx, cy - 24), fill=stem, width=3)
    for offset, width, height, side in ((-18, 42, 20, -1), (-3, 50, 24, 1), (15, 38, 18, -1)):
        y = cy + offset
        if side < 0:
            box = (cx - width, y - height // 2, cx + 4, y + height // 2)
            start, end = 205, 25
        else:
            box = (cx - 4, y - height // 2, cx + width, y + height // 2)
            start, end = 155, 335
        draw.pieslice(box, start=start, end=end, fill=leaf_fill, outline="#d7eee8")


def _draw_background(draw: Any) -> None:
    for y in range(CARD_HEIGHT):
        blend = y / CARD_HEIGHT
        red = int(8 + 10 * blend)
        green = int(26 + 24 * blend)
        blue = int(34 + 22 * blend)
        draw.line((0, y, CARD_WIDTH, y), fill=(red, green, blue))
    for x in range(0, CARD_WIDTH, 7):
        y = (x * 37) % CARD_HEIGHT
        shade = 22 + ((x * 17) % 18)
        draw.point((x, y), fill=(shade, shade + 22, shade + 18))
    for y in range(0, CARD_HEIGHT, 11):
        x = (y * 29) % CARD_WIDTH
        draw.point((x, y), fill=(30, 54, 61))


def _draw_access_motif(draw: Any) -> None:
    base_y = 505
    fill = "#12333b"
    outline = "#315963"
    draw.rectangle((92, base_y - 122, 320, base_y), fill=fill, outline=outline, width=2)
    draw.rectangle((128, base_y - 174, 284, base_y - 122), fill="#102c35", outline=outline, width=2)
    for x in range(122, 292, 38):
        for y in range(base_y - 96, base_y - 18, 34):
            draw.rectangle((x, y, x + 14, y + 17), fill="#234b53")
    draw.rectangle((194, base_y - 46, 222, base_y), fill="#0b222a")
    draw.line((360, base_y - 94, 496, base_y - 126), fill="#294f58", width=3)
    draw.line((360, base_y - 58, 510, base_y - 82), fill="#294f58", width=2)
    draw.line((380, base_y - 22, 472, base_y - 38), fill="#294f58", width=2)
    for x, y, r in ((402, base_y - 100, 9), (468, base_y - 82, 7), (438, base_y - 37, 6)):
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#8bb4ad")


def social_card_spec_for_edition(
    *,
    edition_date: str,
    public_url: str,
    label: str = "Care Line",
) -> dict[str, Any]:
    date.fromisoformat(edition_date)
    return {
        "edition_date": edition_date,
        "display_date": _display_date(edition_date),
        "title": "The Care Line Dispatch",
        "subtitle": "Source-backed briefing on U.S. health-care access",
        "footer": "The Blue Fern Co.",
        "label": label,
        "public_url": public_url,
        "brand": "The Blue Fern Co.",
        "domain": "dispatches.thebluefernco.com",
        "alt_text": f"The Blue Fern Co. Care Line Dispatch social card for {_display_date(edition_date)}",
        "image_url": f"{BASE_URL}/care-line/editions/{edition_date}/",
    }


def render_social_card_png_bytes(spec: dict[str, Any]) -> bytes:
    from PIL import Image, ImageDraw  # type: ignore

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#071c24")
    draw = ImageDraw.Draw(image)
    _draw_background(draw)
    _draw_access_motif(draw)
    draw.rectangle((38, 38, CARD_WIDTH - 38, CARD_HEIGHT - 38), outline="#d1b66f", width=2)
    draw.rectangle((52, 52, CARD_WIDTH - 52, CARD_HEIGHT - 52), outline="#3f7176", width=1)
    _draw_leaf_medallion(draw)

    _centered_text(draw, 184, str(spec.get("label") or "Care Line").upper(), font=_font(20, bold=True), fill="#8bb4ad")
    _centered_text(
        draw,
        232,
        str(spec.get("title") or "The Care Line Dispatch"),
        font=_font(66, serif=True, bold=True),
        fill="#f7efe2",
        shadow="#06151b",
    )
    subtitle = str(spec.get("subtitle") or "Source-backed briefing on U.S. health-care access")
    for index, line in enumerate(wrap(subtitle, width=54)[:2]):
        _centered_text(draw, 324 + (index * 34), line, font=_font(26), fill="#d7e6e2")
    _centered_text(draw, 414, str(spec.get("display_date") or ""), font=_font(28, bold=True), fill="#d8b86a")
    _centered_text(draw, 538, str(spec.get("footer") or "The Blue Fern Co."), font=_font(24, serif=True), fill="#d7e6e2")

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def ensure_care_line_social_card(
    project_root: Path,
    edition_date: str,
    *,
    public_url: str,
    refresh_existing: bool = False,
) -> Path:
    path = care_line_private_card_path(project_root, edition_date)
    if path.exists() and not refresh_existing:
        return path
    rendered = render_social_card_png_bytes(social_card_spec_for_edition(edition_date=edition_date, public_url=public_url))
    if path.exists() and path.read_bytes() == rendered:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rendered)
    return path
