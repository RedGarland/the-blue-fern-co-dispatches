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
CARE_LINE_FOREGROUND_ASSET = Path("assets") / "care-line" / "care-line-foreground-paperwork-v1.png"
CARE_LINE_FACILITY_ASSET = Path("assets") / "care-line" / "care-line-facility-v1.png"


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


def _draw_fern_frond(
    draw: Any,
    *,
    root: tuple[float, float],
    height: float,
    color: str,
    vein: str,
    width: int = 2,
    lean: float = 0.0,
    alpha_shape: bool = False,
) -> None:
    rx, ry = root
    stem: list[tuple[float, float]] = []
    steps = 16
    for index in range(steps + 1):
        t = index / steps
        x = rx + lean * t + 6 * (t - 0.5) * (t - 0.5) - 2 * t * (1 - t)
        y = ry - height * t
        stem.append((x, y))
    draw.line(stem, fill=vein, width=width, joint="curve")

    for index in range(2, steps):
        t = index / steps
        bx, by = stem[index]
        length = (34 * (1 - abs(t - 0.44) * 1.55) + 4) * (height / 92)
        spread = max(1.8, 4.3 * (1 - t) * (height / 92))
        for side in (-1, 1):
            if index > steps - 3 and side < 0:
                continue
            angle_x = side * length * (0.9 + 0.08 * (index % 2))
            angle_y = -length * (0.22 + t * 0.24)
            tip = (bx + angle_x, by + angle_y + side * 0.8)
            px, py = -angle_y, angle_x
            norm = max((px * px + py * py) ** 0.5, 1)
            px, py = px / norm, py / norm
            base = (bx + side * 2.0, by)
            points = [
                (base[0] + px * 0.9, base[1] + py * 0.9),
                (base[0] + px * spread, base[1] + py * spread),
                (tip[0] - side * 2.2, tip[1] + 1.6),
                tip,
                (tip[0] - side * 1.4, tip[1] - 2.0),
                (base[0] - px * spread, base[1] - py * spread),
                (base[0] - px * 0.9, base[1] - py * 0.9),
            ]
            draw.polygon(points, fill=color)
            if not alpha_shape:
                draw.line((base, tip), fill=vein, width=1)
    draw.polygon(
        [
            (stem[-1][0] - 3, stem[-1][1] + 5),
            (stem[-1][0] + 3, stem[-1][1] + 5),
            (stem[-1][0] + lean * 0.08, stem[-1][1] - 11),
        ],
        fill=color,
    )


def _draw_leaf_medallion(draw: Any) -> None:
    cx, cy, r = CARD_WIDTH // 2, 111, 52
    draw.line((218, cy + 1, cx - 88, cy + 1), fill="#d6d3bd", width=1)
    draw.line((cx + 88, cy + 1, 982, cy + 1), fill="#d6d3bd", width=1)
    draw.line((218, cy + 5, cx - 88, cy + 5), fill="#6f9c96", width=1)
    draw.line((cx + 88, cy + 5, 982, cy + 5), fill="#6f9c96", width=1)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#082129", outline="#d6bd7a", width=2)
    draw.arc((cx - 58, cy - 58, cx + 58, cy + 58), start=104, end=260, fill="#f3e8d0", width=2)
    draw.arc((cx - 58, cy - 58, cx + 58, cy + 58), start=285, end=77, fill="#d6bd7a", width=2)
    _draw_fern_frond(
        draw,
        root=(cx - 12, cy + 37),
        height=74,
        color="#87b3ac",
        vein="#cde1da",
        width=1,
        lean=-12,
        alpha_shape=True,
    )
    _draw_fern_frond(
        draw,
        root=(cx - 1, cy + 38),
        height=88,
        color="#efe8d5",
        vein="#f4ead6",
        width=2,
        lean=7,
    )


def _draw_background(draw: Any) -> None:
    for y in range(CARD_HEIGHT):
        blend = y / CARD_HEIGHT
        red = int(6 + 10 * blend)
        green = int(24 + 21 * blend)
        blue = int(28 + 18 * blend)
        draw.line((0, y, CARD_WIDTH, y), fill=(red, green, blue))
    for x in range(0, CARD_WIDTH, 7):
        y = (x * 37) % CARD_HEIGHT
        shade = 22 + ((x * 17) % 18)
        draw.point((x, y), fill=(shade, shade + 22, shade + 18))
    for y in range(0, CARD_HEIGHT, 11):
        x = (y * 29) % CARD_WIDTH
        draw.point((x, y), fill=(30, 54, 61))


def _foreground_asset_path() -> Path:
    return Path(__file__).resolve().parents[2] / CARE_LINE_FOREGROUND_ASSET


def _facility_asset_path() -> Path:
    return Path(__file__).resolve().parents[2] / CARE_LINE_FACILITY_ASSET


def _fit_asset(asset_path: Path, target_width: int) -> Any:
    from PIL import Image  # type: ignore

    if not asset_path.exists():
        return None
    layer = Image.open(asset_path).convert("RGBA")
    bbox = layer.getchannel("A").getbbox()
    if bbox:
        layer = layer.crop(bbox)
    ratio = target_width / layer.width
    return layer.resize((target_width, int(layer.height * ratio)), Image.Resampling.LANCZOS)


def _paste_care_foreground(image: Any) -> None:
    foreground = _fit_asset(_foreground_asset_path(), 600)
    if foreground is None:
        return
    alpha = foreground.getchannel("A").point(lambda value: int(value * 0.98))
    image.paste(foreground.convert("RGB"), (-72, 74), alpha)


def _paste_facility_background(image: Any) -> None:
    from PIL import ImageEnhance  # type: ignore

    facility = _fit_asset(_facility_asset_path(), 560)
    if facility is None:
        return
    alpha = facility.getchannel("A")
    muted = facility.convert("RGB")
    muted = ImageEnhance.Color(muted).enhance(0.48)
    muted = ImageEnhance.Brightness(muted).enhance(0.58)
    muted = ImageEnhance.Contrast(muted).enhance(0.82)
    alpha = alpha.point(lambda value: int(value * 0.5))
    image.paste(muted, (766, 138), alpha)


def _draw_text_field(image: Any) -> None:
    from PIL import Image, ImageDraw, ImageFilter  # type: ignore

    mask = Image.new("L", (CARD_WIDTH, CARD_HEIGHT), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((208, 152, 1006, 510), radius=24, fill=222)
    mask = mask.filter(ImageFilter.GaussianBlur(34))
    field = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), "#082329")
    field.putalpha(mask)
    image.paste(field.convert("RGB"), (0, 0), field.getchannel("A"))

    inner_mask = Image.new("L", (CARD_WIDTH, CARD_HEIGHT), 0)
    inner_draw = ImageDraw.Draw(inner_mask)
    inner_draw.rounded_rectangle((254, 184, 958, 476), radius=18, fill=110)
    inner_mask = inner_mask.filter(ImageFilter.GaussianBlur(10))
    inner_field = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), "#082329")
    inner_field.putalpha(inner_mask)
    image.paste(inner_field.convert("RGB"), (0, 0), inner_field.getchannel("A"))


def _draw_access_motif(image: Any, draw: Any) -> None:
    _paste_facility_background(image)
    draw.ellipse((-54, 548, 448, 630), fill="#06171d")
    _paste_care_foreground(image)
    _draw_text_field(image)


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


def social_card_spec_for_event(
    *,
    event_id: str,
    title: str,
    facility_name: str,
    city: str,
    state: str,
    public_label: str,
    effective_date: str,
) -> dict[str, Any]:
    date_label = str(effective_date or "").strip()
    try:
        date_label = _display_date(date_label)
    except ValueError:
        date_label = date_label.upper()
    location = ", ".join(part for part in (str(facility_name or "").strip(), str(city or "").strip(), str(state or "").strip()) if part)
    return {
        "event_id": event_id,
        "headline": str(title or "").strip(),
        "location": location,
        "event_type_label": str(public_label or "Care access update").strip(),
        "date_label": date_label,
        "brand_name": "The Blue Fern Co.",
        "section_label": "CARE LINE",
        "title": "The Care Line Dispatch",
        "subtitle": str(title or "Source-backed briefing on U.S. health-care access").strip(),
        "display_date": date_label,
        "footer": "The Blue Fern Co.",
        "label": "Care Line",
        "public_url": f"{BASE_URL}/events/{event_id}/",
        "alt_text": f"The Blue Fern Co. Care Line social card for {str(title or event_id).strip()}",
        "image_url": f"{BASE_URL}/events/{event_id}/social-card.png",
    }


def render_social_card_png_bytes(spec: dict[str, Any]) -> bytes:
    from PIL import Image, ImageDraw  # type: ignore

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#071c24")
    draw = ImageDraw.Draw(image)
    _draw_background(draw)
    _draw_access_motif(image, draw)
    draw.rectangle((38, 38, CARD_WIDTH - 38, CARD_HEIGHT - 38), outline="#d1b66f", width=2)
    draw.rectangle((52, 52, CARD_WIDTH - 52, CARD_HEIGHT - 52), outline="#3f7176", width=1)
    _draw_leaf_medallion(draw)

    _centered_text(draw, 184, str(spec.get("label") or "Care Line").upper(), font=_font(20, bold=True), fill="#8bb4ad")
    _centered_text(
        draw,
        232,
        str(spec.get("title") or "The Care Line Dispatch"),
        font=_font(60, serif=True, bold=True),
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
