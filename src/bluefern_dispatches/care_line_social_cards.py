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
    draw.ellipse((cx - 37, cy - 37, cx + 37, cy + 37), outline="#d8c086", width=1)
    stem = "#d8c086"
    leaf = "#b7d8ce"
    vein = "#edf7f2"
    draw.line((cx - 11, cy + 29, cx + 9, cy - 30), fill=stem, width=2)
    leaflets = [
        (-7, 21, -22, 16),
        (-4, 13, 13, 7),
        (-2, 5, -20, 1),
        (1, -3, 20, -9),
        (4, -11, -14, -17),
        (6, -19, 15, -26),
        (-9, 26, -18, 25),
        (8, -25, 12, -34),
    ]
    for base_dx, base_dy, tip_dx, tip_dy in leaflets:
        base = (cx + base_dx, cy + base_dy)
        tip = (cx + tip_dx, cy + tip_dy)
        draw.line((base, tip), fill=vein, width=1)
        radius_x = 5 if abs(tip_dx - base_dx) > 8 else 3
        draw.ellipse((tip[0] - radius_x, tip[1] - 3, tip[0] + radius_x, tip[1] + 3), fill=leaf)


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
    outline = "#426f76"
    deep = "#102d35"
    soft = "#163943"
    window = "#2b5962"
    accent = "#8bb4ad"

    left, top, right = 82, base_y - 122, 318
    draw.rectangle((left, top + 24, right, base_y), fill=soft, outline=outline, width=2)
    draw.polygon(((left + 20, top + 24), ((left + right) // 2, top - 26), (right - 20, top + 24)), fill=deep, outline=outline)
    draw.line((left + 46, top + 24, right - 46, top + 24), fill="#6f9c96", width=1)
    for x in (118, 162, 236, 280):
        draw.rounded_rectangle((x, top + 48, x + 18, top + 66), radius=2, fill=window)
        draw.rounded_rectangle((x, top + 84, x + 18, top + 102), radius=2, fill=window)
    draw.rounded_rectangle((192, top + 62, 226, base_y), radius=16, fill="#0a242b", outline="#6f9c96", width=1)
    draw.line((209, top + 62, 209, base_y), fill="#1d4850", width=1)
    for x in (146, 252):
        draw.line((x, top + 38, x, base_y - 8), fill="#6f9c96", width=2)
        draw.line((x - 10, base_y - 8, x + 10, base_y - 8), fill="#6f9c96", width=1)
    draw.line((left - 14, base_y, right + 26, base_y), fill=outline, width=2)

    path = [(336, base_y - 24), (368, base_y - 34), (400, base_y - 50), (432, base_y - 72)]
    draw.line(path, fill="#5f8f8c", width=2, joint="curve")
    for x, y, r in ((336, base_y - 24, 5), (400, base_y - 50, 6), (432, base_y - 72, 5)):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=accent)


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
