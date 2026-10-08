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
    leaf = "#c7e4dc"
    vein = "#f1f5e9"

    stem_points = [(cx - 2, cy + 31), (cx - 2, cy + 17), (cx + 1, cy + 3), (cx + 4, cy - 12), (cx + 5, cy - 30)]
    draw.line(stem_points, fill=stem, width=2)

    def leaflet(base: tuple[int, int], tip: tuple[int, int], spread: int = 5) -> None:
        bx, by = base
        tx, ty = tip
        dx, dy = tx - bx, ty - by
        length = max((dx * dx + dy * dy) ** 0.5, 1)
        px, py = -dy / length, dx / length
        points = [
            (bx + px * 1.4, by + py * 1.4),
            (bx + px * spread, by + py * spread),
            (tx, ty),
            (bx - px * spread, by - py * spread),
            (bx - px * 1.4, by - py * 1.4),
        ]
        draw.polygon(points, fill=leaf)
        draw.line((base, tip), fill=vein, width=1)

    for base_dx, base_dy, tip_dx, tip_dy, spread in (
        (-3, 23, -24, 12, 5),
        (-2, 18, 21, 8, 5),
        (-1, 12, -27, 0, 5),
        (0, 6, 24, -5, 5),
        (2, 0, -21, -13, 4),
        (3, -7, 21, -20, 4),
        (4, -15, -14, -27, 4),
        (5, -23, 13, -36, 3),
    ):
        leaflet((cx + base_dx, cy + base_dy), (cx + tip_dx, cy + tip_dy), spread)
    leaflet((cx - 2, cy + 30), (cx - 18, cy + 27), 3)
    leaflet((cx + 5, cy - 30), (cx + 6, cy - 42), 3)


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
    deep = "#0d2830"
    soft = "#143943"
    window = "#2b5962"
    accent = "#8bb4ad"

    left, right = 74, 334
    wing_top = base_y - 84
    lobby_left, lobby_right = 164, 236
    lobby_top = base_y - 128

    draw.rounded_rectangle((left, wing_top, right, base_y), radius=3, fill=soft, outline=outline, width=2)
    draw.rounded_rectangle((lobby_left, lobby_top, lobby_right, base_y), radius=4, fill="#173f49", outline="#6f9c96", width=2)
    draw.rectangle((left + 10, wing_top - 10, lobby_left + 2, wing_top), fill=deep, outline=outline)
    draw.rectangle((lobby_right - 2, wing_top - 10, right - 10, wing_top), fill=deep, outline=outline)
    draw.line((left - 10, base_y, right + 28, base_y), fill=outline, width=2)

    for x in (96, 126, 270, 300):
        draw.rounded_rectangle((x, wing_top + 20, x + 16, wing_top + 38), radius=2, fill=window)
        draw.rounded_rectangle((x, wing_top + 52, x + 16, wing_top + 70), radius=2, fill=window)
    for x in (178, 208):
        draw.rounded_rectangle((x, lobby_top + 20, x + 17, lobby_top + 39), radius=2, fill="#315f67")

    cross_box = (184, lobby_top + 50, 216, lobby_top + 82)
    draw.rounded_rectangle(cross_box, radius=3, fill="#224e57", outline="#7fb0aa", width=1)
    cx = (cross_box[0] + cross_box[2]) // 2
    cy = (cross_box[1] + cross_box[3]) // 2
    draw.line((cx - 9, cy, cx + 9, cy), fill="#bdd9d0", width=3)
    draw.line((cx, cy - 9, cx, cy + 9), fill="#bdd9d0", width=3)

    draw.rounded_rectangle((172, base_y - 44, 228, base_y - 4), radius=13, fill="#0a242b", outline="#6f9c96", width=1)
    draw.line((200, base_y - 44, 200, base_y - 4), fill="#1d4850", width=1)
    draw.rounded_rectangle((158, base_y - 58, 242, base_y - 46), radius=3, fill="#725f3b", outline="#a79055", width=1)
    draw.line((238, base_y - 24, 312, base_y - 10), fill="#7ea59f", width=3)
    draw.line((238, base_y - 34, 312, base_y - 20), fill="#507d7b", width=1)
    draw.line((245, base_y - 28, 303, base_y - 17), fill="#a5c3bd", width=1)

    path = [(326, base_y - 17), (365, base_y - 28), (397, base_y - 47), (430, base_y - 69)]
    draw.line(path, fill="#5f8f8c", width=2, joint="curve")
    for x, y, r in ((326, base_y - 17, 4), (430, base_y - 69, 5)):
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
