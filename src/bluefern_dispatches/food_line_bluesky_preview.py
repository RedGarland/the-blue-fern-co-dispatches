from __future__ import annotations

import hashlib
import json
import os
from html import escape
from pathlib import Path
import textwrap
from typing import Any
from urllib.parse import urlparse

from bluefern_dispatches.food_line_bluesky_approval import public_url_for_edition
from bluefern_dispatches.food_line_social_card import ensure_food_line_social_card, food_line_social_card_relative_path


PREVIEW_DIR_NAME = "bluesky-preview"
PREVIEW_FILENAME = "food-line-bluesky-preview.json"
PREVIEW_HTML_FILENAME = "food-line-bluesky-preview.html"
IN_FEED_PREVIEW_HTML_FILENAME = "food-line-bluesky-in-feed-preview.html"
IN_FEED_PREVIEW_PNG_FILENAME = "food-line-bluesky-in-feed-preview.png"
BLUESKY_ACCOUNT_NAME = "The Blue Fern Co."
BLUESKY_ACCOUNT_HANDLE = "@thebluefernco.com"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def deterministic_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _human_date(edition_date: str) -> str:
    year, month, day = edition_date.split("-")
    month_name = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }[month]
    return f"{month_name} {int(day)}, {year}"


def _card_title(edition_date: str) -> str:
    return f"Food Line — {_human_date(edition_date)}"


def _card_description() -> str:
    return "Read the source-backed U.S. food pressure update from The Blue Fern Co."


def _domain(public_url: str) -> str:
    parsed = urlparse(public_url)
    return parsed.netloc or "dispatches.thebluefernco.com"


def _post_text(manifest: dict[str, Any], review: dict[str, Any]) -> str:
    title = "Food Line Dispatch"
    edition_date = str(manifest.get("edition_date") or "").strip()
    lead_summary = str(manifest.get("public_summary") or "").strip()
    approved_items: list[dict[str, Any]] = []
    layout = review.get("layout") if isinstance(review, dict) else {}
    if isinstance(layout, dict):
        for key in ("todays_read", "core_food_pressure_signals", "at_a_glance"):
            value = layout.get(key)
            if isinstance(value, list):
                approved_items.extend(item for item in value if isinstance(item, dict))
    secondary_summary = ""
    for item in approved_items:
        summary = str(item.get("summary") or "").strip()
        if summary and summary != lead_summary:
            secondary_summary = summary
            break
    body_parts = [lead_summary]
    if secondary_summary:
        body_parts.append(secondary_summary)
    body = " ".join(part.rstrip(".") + "." for part in body_parts if part)
    return f"{title} — {_human_date(edition_date)}\n\n{body}"


def build_food_line_bluesky_preview(project_root: Path, edition_date: str, *, refresh_card: bool = False) -> dict[str, Any]:
    manifest_path = project_root / "output" / "site" / "food-line" / "editions" / edition_date / "edition_manifest.json"
    review_path = project_root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{edition_date}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = _load_json(manifest_path)
    review = _load_json(review_path) if review_path.exists() else {"layout": {}}
    public_url = str(manifest.get("public_url") or public_url_for_edition(edition_date)).strip()
    post_text = _post_text(manifest, review)
    card_title = _card_title(edition_date)
    card_description = _card_description()
    image_path = ensure_food_line_social_card(project_root, edition_date, refresh_existing=refresh_card)
    image_rel = food_line_social_card_relative_path(edition_date)
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
    payload = {
        "schema_version": 2,
        "dispatch_slug": "food-line",
        "edition_date": edition_date,
        "public_url": public_url,
        "account": {
            "display_name": BLUESKY_ACCOUNT_NAME,
            "handle": BLUESKY_ACCOUNT_HANDLE,
            "avatar_path": "assets/bluefern.png",
        },
        "post_text": post_text,
        "card_title": card_title,
        "card_description": card_description,
        "card_domain": _domain(public_url),
        "card_image_path": image_rel.as_posix(),
        "card_image_sha256": image_hash,
        "embed": {
            "uri": public_url,
            "title": card_title,
            "description": card_description,
        },
        "source_provenance": {
            "proposal_sha256": manifest.get("approved_proposal_sha256"),
            "review_sha256": manifest.get("review_snapshot_sha256"),
            "review_item_ids": [
                item.get("review_item_id")
                for item in (review.get("items") or [])
                if isinstance(item, dict) and item.get("review_item_id")
            ],
        },
    }
    payload["content_sha256"] = hashlib.sha256(deterministic_json(payload).encode("utf-8")).hexdigest()
    return payload


def _relative_asset_path(from_path: Path, to_path: Path) -> str:
    try:
        return to_path.resolve().relative_to(from_path.parent.resolve()).as_posix()
    except ValueError:
        import os

        return os.path.relpath(to_path, from_path.parent).replace("\\", "/")


def _render_feed_preview_html(preview: dict[str, Any], *, image_src: str, avatar_src: str) -> str:
    post_text = escape(str(preview["post_text"])).replace("\n", "<br>")
    title = escape(str(preview["card_title"]))
    description = escape(str(preview["card_description"]))
    domain = escape(str(preview["card_domain"]))
    account = preview.get("account") if isinstance(preview.get("account"), dict) else {}
    display_name = escape(str(account.get("display_name") or BLUESKY_ACCOUNT_NAME))
    handle = escape(str(account.get("handle") or BLUESKY_ACCOUNT_HANDLE))
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>Food Line Bluesky In-Feed Preview - {escape(str(preview['edition_date']))}</title>",
            f'  <meta property="og:title" content="{title}">',
            f'  <meta property="og:description" content="{description}">',
            "  <style>",
            "    body { margin: 0; background: #eaf1f7; color: #0f1419; font-family: Arial, Helvetica, sans-serif; }",
            "    main { max-width: 660px; margin: 34px auto; padding: 0 18px; }",
            "    .boundary { margin: 0 0 18px; padding: 12px 14px; background: #071d33; color: #d7e8fb; border: 1px solid #6d91b8; border-radius: 10px; font-size: 14px; }",
            "    .post { background: white; border: 1px solid #b9cce0; border-top: 4px solid #0b3152; border-radius: 16px; padding: 18px; box-shadow: 0 10px 32px rgba(10, 31, 54, 0.16); }",
            "    .author { display: grid; grid-template-columns: 52px 1fr; gap: 12px; align-items: center; margin-bottom: 12px; }",
            "    .avatar { width: 52px; height: 52px; border-radius: 999px; border: 1px solid #b9cce0; object-fit: cover; background: #f7f2eb; }",
            "    .name { font-weight: 800; font-size: 16px; }",
            "    .handle { color: #536471; font-size: 14px; margin-top: 2px; }",
            "    .text { font-size: 17px; line-height: 1.42; margin: 10px 0 14px 64px; white-space: normal; }",
            "    .embed { margin-left: 64px; border: 1px solid #cfd9e4; border-radius: 14px; overflow: hidden; background: #fff; }",
            "    .embed img { display: block; width: 100%; aspect-ratio: 1.904 / 1; object-fit: cover; background: #eee; }",
            "    .meta { padding: 12px 14px 14px; }",
            "    .domain { color: #536471; font-size: 13px; margin-bottom: 4px; text-transform: lowercase; }",
            "    .title { font-weight: 800; font-size: 16px; line-height: 1.25; margin-bottom: 5px; }",
            "    .desc { color: #536471; font-size: 14px; line-height: 1.32; }",
            "    .trace { color: #536471; font-size: 12px; margin: 16px 0 0 64px; word-break: break-word; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            '    <div class="boundary">Private in-feed preview only. Not posted until explicit approval.</div>',
            '    <article class="post" aria-label="Bluesky in-feed preview">',
            '      <header class="author">',
            f'        <img class="avatar" src="{escape(avatar_src)}" alt="The Blue Fern Co. avatar">',
            "        <div>",
            f'          <div class="name">{display_name}</div>',
            f'          <div class="handle">{handle}</div>',
            "        </div>",
            "      </header>",
            f'      <div class="text">{post_text}</div>',
            '      <section class="embed" aria-label="External link card preview">',
            f'        <img src="{escape(image_src)}" alt="Food Line social card preview">',
            '        <div class="meta">',
            f'          <div class="domain">{domain}</div>',
            f'          <div class="title">{title}</div>',
            f'          <div class="desc">{description}</div>',
            "        </div>",
            "      </section>",
            f'      <div class="trace">Image: {escape(str(preview["card_image_path"]))}<br>Image SHA256: {escape(str(preview["card_image_sha256"]))}</div>',
            "    </article>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )


def _draw_wrapped(draw: Any, xy: tuple[int, int], text: str, *, font: Any, fill: str, width: int, line_spacing: int = 8, max_lines: int | None = None) -> int:
    words = text.replace("\n", " \n ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        if word == "\n":
            if current:
                lines.append(current)
                current = ""
            continue
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = textwrap.shorten(lines[-1], width=max(10, len(lines[-1]) - 3), placeholder="...")
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += (bbox[3] - bbox[1]) + line_spacing
    return y


def _render_feed_preview_png(project_root: Path, preview: dict[str, Any], output_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    def font(size: int, *, bold: bool = False) -> Any:
        candidates = (
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        )
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    canvas = Image.new("RGB", (900, 1120), "#eaf1f7")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((70, 84, 830, 1038), radius=24, fill="#ffffff", outline="#b9cce0", width=2)
    draw.rounded_rectangle((70, 84, 830, 92), radius=4, fill="#0b3152")
    avatar_box = (92, 110, 146, 164)
    avatar_path = project_root / "assets" / "bluefern.png"
    if avatar_path.exists():
        avatar = Image.open(avatar_path).convert("RGB").resize((54, 54))
        mask = Image.new("L", (54, 54), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 53, 53), fill=255)
        canvas.paste(avatar, (92, 110), mask)
        draw.ellipse(avatar_box, outline="#b9cce0", width=1)
    else:
        draw.rounded_rectangle(avatar_box, radius=27, fill="#0b3152")
        draw.text((109, 124), "BF", font=font(20, bold=True), fill="#ffffff")
    draw.text((160, 112), BLUESKY_ACCOUNT_NAME, font=font(24, bold=True), fill="#0f1419")
    draw.text((160, 142), BLUESKY_ACCOUNT_HANDLE, font=font(19), fill="#536471")
    y = _draw_wrapped(draw, (160, 198), str(preview["post_text"]), font=font(24), fill="#0f1419", width=610, line_spacing=10, max_lines=7)
    embed_top = max(390, y + 24)
    embed_left, embed_right = 160, 780
    draw.rounded_rectangle((embed_left, embed_top, embed_right, embed_top + 505), radius=18, fill="#ffffff", outline="#b9cce0", width=2)
    card_path = project_root / str(preview["card_image_path"])
    card = Image.open(card_path).convert("RGB")
    card = card.resize((620, 326))
    canvas.paste(card, (embed_left, embed_top))
    meta_y = embed_top + 344
    draw.text((embed_left + 16, meta_y), str(preview["card_domain"]).lower(), font=font(17), fill="#536471")
    y2 = _draw_wrapped(draw, (embed_left + 16, meta_y + 32), str(preview["card_title"]), font=font(23, bold=True), fill="#0f1419", width=570, max_lines=2)
    _draw_wrapped(draw, (embed_left + 16, y2 + 6), str(preview["card_description"]), font=font(19), fill="#536471", width=570, max_lines=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.parent / ".preview-tmp.png"
    canvas.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, output_path)


def write_food_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
    preview = build_food_line_bluesky_preview(project_root, edition_date, refresh_card=True)
    preview_dir = project_root / "data" / "dispatches" / "food-line" / "review" / PREVIEW_DIR_NAME / edition_date
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / PREVIEW_FILENAME
    html_path = preview_dir / PREVIEW_HTML_FILENAME
    in_feed_html_path = preview_dir / IN_FEED_PREVIEW_HTML_FILENAME
    in_feed_png_path = preview_dir / IN_FEED_PREVIEW_PNG_FILENAME
    image_src = _relative_asset_path(in_feed_html_path, project_root / str(preview["card_image_path"]))
    avatar_src = _relative_asset_path(in_feed_html_path, project_root / "assets" / "bluefern.png")
    json_path.write_text(deterministic_json(preview) + "\n", encoding="utf-8")
    html = _render_feed_preview_html(preview, image_src=image_src, avatar_src=avatar_src)
    html_path.write_text(html, encoding="utf-8")
    in_feed_html_path.write_text(html, encoding="utf-8")
    _render_feed_preview_png(project_root, preview, in_feed_png_path)
    return {
        "preview": preview,
        "json_path": json_path,
        "html_path": html_path,
        "in_feed_html_path": in_feed_html_path,
        "in_feed_png_path": in_feed_png_path,
    }
