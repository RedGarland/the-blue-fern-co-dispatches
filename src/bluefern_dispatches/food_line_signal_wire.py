from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
import ctypes
from ctypes import wintypes
import struct
import zlib

from bluefern_dispatches.bluesky_post import BLUESKY_MAX_POST_LENGTH
from bluefern_dispatches.food_line_bluesky_preview import deterministic_json


BASE_URL = "https://dispatches.thebluefernco.com"
PUBLIC_PATH_PREFIX = "/food-line/wire/"
PREVIEW_ROOT = Path("output/review/food-line/signal-wire")
PREVIEW_JSON_NAME = "preview.json"
PREVIEW_HTML_NAME = "index.html"
CARD_DIR_NAME = "cards"
CARD_SIZE = (1200, 630)
CURRENT_AS_OF = "2026-08-15"


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_uint8), ("rgbGreen", ctypes.c_uint8), ("rgbRed", ctypes.c_uint8), ("rgbReserved", ctypes.c_uint8)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


DT_CENTER = 0x00000001
DT_WORDBREAK = 0x00000010
DT_NOPREFIX = 0x00000800
DT_END_ELLIPSIS = 0x00008000
TRANSPARENT = 1
BI_RGB = 0
SRCCOPY = 0x00CC0020
FW_NORMAL = 400
FW_SEMIBOLD = 600
DEFAULT_CHARSET = 1
OUT_DEFAULT_PRECIS = 0
CLIP_DEFAULT_PRECIS = 0
CLEARTYPE_QUALITY = 5
FF_DONTCARE = 0
DT_LEFT = 0x00000000
DT_TOP = 0x00000000


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _first_mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(key in payload for key in ("summary", "title", "evidence_text", "source_url", "canonical_source_url")):
            return payload
        for value in payload.values():
            found = _first_mapping(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_mapping(item)
            if found:
                return found
    return {}


def _source_identity(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _hash_text(payload: Any) -> str:
    return hashlib.sha256(deterministic_json(payload).encode("utf-8")).hexdigest()


def _hash_signal_id(*, source_url: str, event_date: str, geography_scope: str, pressure_category: str) -> str:
    seed = "|".join(
        [
            _source_identity(source_url),
            str(event_date or "").strip(),
            str(geography_scope or "").strip().lower(),
            str(pressure_category or "").strip().lower(),
        ]
    ).encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:20]


def _trim_sentence(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    for sep in (". ", "; ", ", "):
        idx = cut.rfind(sep)
        if idx > max(32, limit // 3):
            cut = cut[: idx + 1]
            break
    return cut.rstrip(" ,;:-") + "..."


def _compose_post(*, geography: str, summary: str, source: str, caveat: str | None = None) -> str:
    header = f"FOOD LINE | {geography.strip()}"
    body_parts = [summary.strip()]
    if caveat and caveat.strip():
        body_parts.append(caveat.strip())
    source_line = f"Source: {source.strip()}"
    return f"{header}\n\n{' '.join(body_parts)}\n\n{source_line}"


def _eligible_post(*, geography: str, summary: str, source: str, caveat: str | None = None) -> str:
    post = _compose_post(geography=geography, summary=summary, source=source, caveat=caveat)
    if len(post) > BLUESKY_MAX_POST_LENGTH:
        raise ValueError("bluesky_text_over_limit")
    return post


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw = bytearray()
    row_size = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * row_size : (y + 1) * row_size])
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(_png_chunk(b"IEND", b""))
    path.write_bytes(bytes(png))


def _new_canvas(width: int, height: int) -> bytearray:
    pixels = bytearray(width * height * 4)
    for y in range(height):
        ratio = y / max(1, height - 1)
        for x in range(width):
            horiz = x / max(1, width - 1)
            red = int(9 + ratio * 12 + horiz * 10)
            green = int(18 + ratio * 18 + horiz * 16)
            blue = int(29 + ratio * 34 + horiz * 20)
            idx = (y * width + x) * 4
            pixels[idx : idx + 4] = bytes((red, green, blue, 255))
    return pixels


def _alpha_blend(base: bytearray, width: int, x: int, y: int, rgba: tuple[int, int, int, int]) -> None:
    if not (0 <= x < width and 0 <= y < len(base) // (width * 4)):
        return
    idx = (y * width + x) * 4
    src_r, src_g, src_b, src_a = rgba
    if src_a >= 255:
        base[idx : idx + 4] = bytes((src_r, src_g, src_b, 255))
        return
    dst_b, dst_g, dst_r, _ = base[idx : idx + 4]
    alpha = src_a / 255.0
    inv = 1.0 - alpha
    base[idx : idx + 4] = bytes(
        (
            int(dst_b * inv + src_b * alpha),
            int(dst_g * inv + src_g * alpha),
            int(dst_r * inv + src_r * alpha),
            255,
        )
    )


def _draw_soft_ellipse(pixels: bytearray, width: int, height: int, cx: int, cy: int, rx: int, ry: int, color: tuple[int, int, int, int]) -> None:
    for y in range(cy - ry, cy + ry + 1):
        for x in range(cx - rx, cx + rx + 1):
            dx = (x - cx) / max(1, rx)
            dy = (y - cy) / max(1, ry)
            dist = dx * dx + dy * dy
            if dist <= 1.0:
                alpha = int(color[3] * max(0.0, 1.0 - dist))
                _alpha_blend(pixels, width, x, y, (color[0], color[1], color[2], alpha))


def _draw_fine_line(pixels: bytearray, width: int, height: int, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int, int]) -> None:
    steps = max(abs(x2 - x1), abs(y2 - y1), 1)
    for i in range(steps + 1):
        t = i / steps
        x = round(x1 + (x2 - x1) * t)
        y = round(y1 + (y2 - y1) * t)
        _alpha_blend(pixels, width, x, y, color)
        _alpha_blend(pixels, width, x + 1, y, (color[0], color[1], color[2], color[3] // 2))
        _alpha_blend(pixels, width, x, y + 1, (color[0], color[1], color[2], color[3] // 2))


def _wrap_words(text: str, max_chars: int) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= max_chars or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _measure_lines(text: str, max_chars: int) -> list[str]:
    return _wrap_words(text, max_chars)


def _render_card(event: dict[str, Any], path: Path) -> None:
    width, height = CARD_SIZE
    pixels = _new_canvas(width, height)
    # Subtle fern-like backdrop and frame accents.
    _draw_soft_ellipse(pixels, width, height, 166, 160, 124, 82, (34, 54, 45, 120))
    _draw_soft_ellipse(pixels, width, height, 965, 186, 162, 98, (20, 38, 34, 92))
    _draw_soft_ellipse(pixels, width, height, 892, 122, 58, 26, (94, 126, 104, 88))
    _draw_fine_line(pixels, width, height, 58, 54, 1142, 54, (72, 103, 89, 180))
    _draw_fine_line(pixels, width, height, 58, 576, 1142, 576, (72, 103, 89, 180))
    _draw_fine_line(pixels, width, height, 58, 54, 58, 576, (72, 103, 89, 160))
    _draw_fine_line(pixels, width, height, 1142, 54, 1142, 576, (72, 103, 89, 160))
    _draw_fine_line(pixels, width, height, 82, 110, 1118, 110, (50, 79, 68, 120))
    _draw_fine_line(pixels, width, height, 82, 516, 1118, 516, (50, 79, 68, 120))
    _draw_fine_line(pixels, width, height, 808, 336, 1046, 336, (52, 82, 71, 105))
    _draw_fine_line(pixels, width, height, 880, 300, 960, 286, (52, 82, 71, 105))
    _draw_fine_line(pixels, width, height, 960, 286, 998, 254, (52, 82, 71, 105))

    try:
        user32.SetProcessDPIAware()
        hdc = gdi32.CreateCompatibleDC(None)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        hbitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not hbitmap:
            raise OSError("CreateDIBSection failed")
        old_bitmap = gdi32.SelectObject(hdc, hbitmap)
        buf = (ctypes.c_ubyte * len(pixels)).from_address(bits.value)
        buf[:] = pixels

        def rgb(r: int, g: int, b: int) -> int:
            return r | (g << 8) | (b << 16)

        def make_font(height_px: int, face: str, weight: int = FW_NORMAL) -> int:
            return gdi32.CreateFontW(
                -height_px,
                0,
                0,
                0,
                weight,
                0,
                0,
                0,
                DEFAULT_CHARSET,
                OUT_DEFAULT_PRECIS,
                CLIP_DEFAULT_PRECIS,
                CLEARTYPE_QUALITY,
                FF_DONTCARE,
                face,
            )

        def draw_text_box(text: str, left: int, top: int, right: int, bottom: int, *, font: int, color: tuple[int, int, int], flags: int) -> None:
            old_font = gdi32.SelectObject(hdc, font)
            gdi32.SetTextColor(hdc, rgb(*color))
            gdi32.SetBkMode(hdc, TRANSPARENT)
            rect = RECT(left, top, right, bottom)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rect), flags | DT_NOPREFIX)
            gdi32.SelectObject(hdc, old_font)

        def draw_rule(x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int], width_px: int = 1) -> None:
            pen = gdi32.CreatePen(0, width_px, rgb(*color))
            old_pen = gdi32.SelectObject(hdc, pen)
            gdi32.MoveToEx(hdc, x1, y1, None)
            gdi32.LineTo(hdc, x2, y2)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.DeleteObject(pen)

        masthead = make_font(24, "Segoe UI", FW_SEMIBOLD)
        section = make_font(58, "Georgia", FW_NORMAL)
        signal = make_font(20, "Segoe UI", FW_SEMIBOLD)
        geo = make_font(20, "Segoe UI", FW_NORMAL)
        headline = make_font(44, "Georgia", FW_NORMAL)
        footer = make_font(18, "Segoe UI", FW_NORMAL)

        draw_text_box("THE BLUE FERN CO.", 82, 66, 1120, 102, font=masthead, color=(214, 229, 221), flags=DT_LEFT | DT_TOP)
        draw_text_box("FOOD LINE", 82, 118, 520, 176, font=section, color=(245, 247, 242), flags=DT_LEFT | DT_TOP)
        draw_text_box("SIGNAL WIRE", 82, 174, 520, 214, font=signal, color=(172, 201, 180), flags=DT_LEFT | DT_TOP)
        draw_text_box(f"{event['state']}  •  {event['pressure_category']}", 82, 224, 1120, 256, font=geo, color=(193, 211, 200), flags=DT_LEFT | DT_TOP)
        draw_rule(82, 262, 1116, 262, (65, 95, 82), 1)

        headline_text = "\n".join(_measure_lines(str(event["headline"]), 34)[:3])
        draw_text_box(headline_text, 82, 288, 1090, 476, font=headline, color=(250, 251, 248), flags=DT_LEFT | DT_TOP | DT_WORDBREAK)

        draw_rule(82, 520, 1118, 520, (65, 95, 82), 1)
        draw_text_box("SOURCE-BACKED UPDATE", 82, 534, 470, 572, font=footer, color=(171, 194, 172), flags=DT_LEFT | DT_TOP)
        publisher = str(event.get("publisher") or "").strip()
        if publisher:
            draw_text_box(publisher, 900, 534, 1118, 572, font=footer, color=(171, 194, 172), flags=DT_LEFT | DT_TOP)

        for handle in (masthead, section, signal, geo, headline, footer):
            gdi32.DeleteObject(handle)
        ctypes.memmove(ctypes.addressof(ctypes.c_ubyte.from_buffer(pixels)), bits.value, len(pixels))
        gdi32.SelectObject(hdc, old_bitmap)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc)
    except Exception:
        # Fall back to the internal raster composition if GDI is unavailable.
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_png(path, width, height, pixels)


def _base_record(payload: Any) -> dict[str, Any]:
    return _first_mapping(payload)


def _event_from_record(
    payload: Any,
    *,
    pressure_category: str,
    kind: str,
    state_override: str | None = None,
    geography_override: str | None = None,
    summary_override: str | None = None,
    caveat_override: str | None = None,
    as_of: str = CURRENT_AS_OF,
    source_published_at_override: str | None = None,
) -> dict[str, Any]:
    base = _base_record(payload)
    source_url = str(base.get("canonical_url") or base.get("canonical_source_url") or base.get("source_url") or base.get("url") or "").strip()
    publisher = str(base.get("publisher") or base.get("source") or base.get("source_name") or "").strip()
    source_title = str(base.get("title") or base.get("headline") or "").strip()
    source_published_at = str(
        source_published_at_override
        or base.get("source_published_at")
        or base.get("published_at")
        or base.get("source_published_date")
        or ""
    ).strip()
    event_date = source_published_at[:10] if source_published_at else as_of
    geography_scope = str(
        geography_override
        or base.get("geography_scope")
        or base.get("location_scope")
        or base.get("location_name")
        or base.get("state")
        or ""
    ).strip()
    state = str(state_override or base.get("state") or "").strip() or "US"
    locality = str(base.get("location_name") or "").strip()
    evidence_text = str(base.get("exact_supporting_passage") or base.get("evidence_text") or "").strip()
    summary_source = summary_override or base.get("summary") or base.get("summary_or_snippet") or base.get("pressure_summary") or evidence_text
    public_summary = _trim_sentence(str(summary_source or "").strip(), 170)
    caveat = caveat_override or str(base.get("uncertainty_note") or "").strip() or None
    why_it_matters = str(base.get("why_it_matters") or base.get("approved_why_it_matters") or "").strip() or "It documents a current food-access pressure signal."
    qualifying = bool(source_url and publisher and evidence_text and public_summary)
    if as_of and source_published_at and source_published_at[:10] < as_of:
        qualifying = False
    if not (
        state.upper() in {"US", "UNITED STATES", "CA"}
        or "United States" in geography_scope
        or geography_scope.endswith(", USA")
        or geography_scope.endswith(", U.S.")
    ):
        qualifying = False
    if kind != "current_fixture":
        qualifying = False
    signal_id = _hash_signal_id(
        source_url=source_url,
        event_date=event_date,
        geography_scope=geography_scope,
        pressure_category=pressure_category,
    )
    bluesky_post_text = _compose_post(geography=state, summary=public_summary, source=publisher, caveat=caveat)
    eligible = qualifying and len(bluesky_post_text) <= BLUESKY_MAX_POST_LENGTH
    if kind == "current_fixture" and not eligible and len(bluesky_post_text) > BLUESKY_MAX_POST_LENGTH:
        reason = "bluesky_text_over_limit"
    elif eligible:
        reason = "fresh current U.S. pressure event"
    else:
        reason = "stale_or_contextual_preview_event"
    event = {
        "kind": kind,
        "dispatch_slug": "food-line",
        "signal_id": signal_id,
        "event_date": event_date,
        "source_published_at": source_published_at or as_of,
        "discovered_at": str(base.get("retrieved_at") or base.get("completed_at") or source_published_at or as_of),
        "verified_at": str(base.get("decision_audit", {}).get("decided_at") or base.get("verified_at") or base.get("retrieved_at") or as_of),
        "geography_scope": geography_scope,
        "state": state,
        "locality": locality,
        "pressure_category": pressure_category,
        "headline": source_title or public_summary,
        "public_summary": public_summary,
        "why_it_matters": why_it_matters,
        "publisher": publisher,
        "canonical_source_url": source_url,
        "source_title": source_title,
        "evidence_text": evidence_text,
        "evidence_basis": str(base.get("evidence_text_basis") or base.get("evidence_level") or "source_text_verified"),
        "source_artifact_path": str(base.get("source_artifact_path") or base.get("_artifact_path") or "").strip(),
        "qualification_status": "qualified" if qualifying else "unqualified",
        "qualification_reason": "direct source-backed pressure signal" if qualifying else "missing exact evidence",
        "review_status": str(base.get("review_status") or base.get("editorial_status") or "").strip() or None,
        "review_decision_id": str(base.get("review_item_id") or "").strip() or None,
        "record_fingerprint": _hash_text(
            {
                "signal_id": signal_id,
                "canonical_source_url": source_url,
                "event_date": event_date,
                "public_summary": public_summary,
                "why_it_matters": why_it_matters,
                "evidence_text": evidence_text,
            }
        ),
        "content_sha256": "",
        "supersedes_signal_id": None,
        "public_permalink": f"{BASE_URL}{PUBLIC_PATH_PREFIX}{signal_id}/",
        "bluesky_post_text": bluesky_post_text,
        "bluesky_text_length": len(bluesky_post_text),
        "card_title": "Food Line Signal Wire",
        "card_description": f"{state} - {pressure_category}",
        "card_image_path": "",
        "publication_status": "preview",
        "bluesky_status": "not_posted",
        "wire_auto_publish_eligible": bool(eligible),
        "wire_auto_publish_reason": reason,
        "source_provenance": {
            "source_artifact_path": str(base.get("source_artifact_path") or base.get("_artifact_path") or "").strip(),
            "source_url": source_url,
            "canonical_source_url": source_url,
            "publisher": publisher,
            "source_title": source_title,
            "evidence_text": evidence_text,
        },
    }
    event["content_sha256"] = _hash_text({k: v for k, v in event.items() if k not in {"content_sha256"}})
    return event


def _load_examples(project_root: Path) -> list[dict[str, Any]]:
    review = _load_json(project_root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json")
    current_item = (review.get("items") or [{}])[0] if review else {}
    history_root = project_root / "data" / "agent-history" / "food-line" / "normalized"
    examples = [
        _event_from_record(
            current_item,
            pressure_category="food-bank / pantry capacity",
            kind="historical_reference",
            summary_override="Faith Food Pantry in Superior closed after its final July 28 distribution. It had recently served about 960 people.",
            caveat_override="Clients were directed to Second Harvest Northland, but equivalent capacity was not established.",
        ),
        _event_from_record(
            _load_json(history_root / "9fbdabc810f6ab9ee36d655ae975bbb96ee038d5c808bf3b475c98c001b7ca8c.json"),
            pressure_category="benefit access / policy",
            kind="historical_reference",
            state_override="MA",
            geography_override="Massachusetts",
            summary_override="In March, the Massachusetts DTA answered only 19% of calls, and reporting tied access barriers to some SNAP losses.",
            caveat_override="The article did not quantify exactly how many losses were caused by failed contact.",
        ),
        _event_from_record(
            _load_json(history_root / "b4b7227b29696f9454b4b68123c8a329bc5bd9ea73995e2e4056210e320cd1b4.json"),
            pressure_category="food-price / affordability",
            kind="historical_reference",
            state_override="TX",
            geography_override="North Texas",
            summary_override="North Texas food banks reported rising demand as SNAP participation fell and donations dropped.",
            caveat_override="Both food banks also said they had to buy substantially more food to keep serving clients.",
        ),
        _event_from_record(
            _load_json(history_root / "bb9971662b7c50cd36f26dc09421b778c4132d8a062ad001aa745e718c04ee20.json"),
            pressure_category="local food access / supply",
            kind="historical_reference",
            state_override="MI",
            geography_override="Greater Lansing area",
            summary_override="Greater Lansing Food Bank stopped distributing implicated lettuce and is discarding 800 to 1,200 pounds a week.",
            caveat_override="The loss is tied to an unresolved cyclospora outbreak.",
        ),
    ]
    return examples


def _render_preview_html(preview: dict[str, Any]) -> str:
    rows = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        "  <title>Food Line Signal Wire Preview</title>",
        "</head>",
        "<body>",
        "  <main>",
        "    <h1>Food Line Signal Wire Preview</h1>",
        "    <p>Minimum viable, offline, source-backed preview only.</p>",
    ]
    for event in preview["examples"]:
        rows.extend(
            [
                "    <section>",
                f"      <h2>{escape(str(event['headline']))}</h2>",
                f"      <p><strong>Signal ID:</strong> {escape(str(event['signal_id']))}</p>",
                f"      <p><strong>Bluesky text:</strong> {escape(str(event['bluesky_text_length']))} / {BLUESKY_MAX_POST_LENGTH}</p>",
                f"      <pre>{escape(str(event['bluesky_post_text']))}</pre>",
                f"      <p><strong>Eligibility:</strong> {escape('yes' if event['wire_auto_publish_eligible'] else 'no')} - {escape(str(event['wire_auto_publish_reason']))}</p>",
                f"      <p><strong>Summary:</strong> {escape(str(event['public_summary']))}</p>",
                f"      <p><strong>Evidence:</strong> {escape(str(event['evidence_text']))}</p>",
                f"      <p><strong>Source:</strong> <a href=\"{escape(str(event['canonical_source_url']))}\">{escape(str(event['publisher']))}</a></p>",
                f"      <p><strong>Card:</strong> <img src=\"{escape(str(event['card_image_path']))}\" alt=\"{escape(str(event['card_description']))}\" width=\"1200\" height=\"630\"></p>",
                "    </section>",
            ]
        )
    rows.extend(["  </main>", "</body>", "</html>"])
    return "\n".join(rows)


def build_food_line_signal_wire_preview(project_root: Path) -> dict[str, Any]:
    examples = _load_examples(project_root)
    preview = {
        "schema_version": "food_line_signal_wire_preview_v1",
        "dispatch_slug": "food-line",
        "public_permalink_contract": f"{BASE_URL}{PUBLIC_PATH_PREFIX}<signal-id>/",
        "card_dimensions": {"width": CARD_SIZE[0], "height": CARD_SIZE[1]},
        "bluesky_post_limit": BLUESKY_MAX_POST_LENGTH,
        "examples": examples,
    }
    for event in examples:
        event["card_image_path"] = (PREVIEW_ROOT / CARD_DIR_NAME / f"{event['signal_id']}.png").as_posix()
    preview["content_sha256"] = _hash_text(preview)
    return preview


def write_food_line_signal_wire_preview(project_root: Path) -> dict[str, Any]:
    preview = build_food_line_signal_wire_preview(project_root)
    preview_root = project_root / PREVIEW_ROOT
    cards_dir = preview_root / CARD_DIR_NAME
    cards_dir.mkdir(parents=True, exist_ok=True)
    for event in preview["examples"]:
        _render_card(event, project_root / event["card_image_path"])
    json_path = preview_root / PREVIEW_JSON_NAME
    html_path = preview_root / PREVIEW_HTML_NAME
    json_path.write_text(deterministic_json(preview) + "\n", encoding="utf-8")
    html_path.write_text(_render_preview_html(preview), encoding="utf-8")
    return {"json_path": json_path, "html_path": html_path, "preview": preview}


def build_current_event_eligibility_fixture(*, as_of: str = CURRENT_AS_OF) -> dict[str, Any]:
    return _event_from_record(
        {
            "source_url": "https://example.com/current-food-pressure",
            "canonical_source_url": "https://example.com/current-food-pressure",
            "publisher": "Example Publisher",
            "title": "Example pantry demand surge",
            "source_published_at": as_of,
            "location_name": "Example City",
            "state": "CA",
            "location_scope": "Example City, California, United States",
            "summary": "A pantry in Example City reported a current surge in food demand after a benefit interruption.",
            "exact_supporting_passage": "The pantry reported a current surge in food demand after a benefit interruption.",
            "uncertainty_note": "The report did not quantify the total number of affected households.",
            "why_it_matters": "It documents a direct access pressure signal.",
            "evidence_text_basis": "source_text_verified",
            "source_artifact_path": "data/dispatches/food-line/agent-intake/2026-08-15/example.json",
            "review_status": "approve",
            "review_item_id": "example-current-fixture",
        },
        pressure_category="benefit access / policy",
        kind="current_fixture",
        state_override="CA",
        geography_override="California, United States",
        summary_override="A pantry in Example City reported a current surge in food demand after a benefit interruption.",
        caveat_override="The report did not quantify the total number of affected households.",
        as_of=as_of,
        source_published_at_override=CURRENT_AS_OF,
    )
