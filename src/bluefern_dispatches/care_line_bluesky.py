from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import textwrap
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from bluefern_dispatches.bluesky_post import (
    BLUESKY_API_BASE,
    BLUESKY_BLOB_MAX_BYTES,
    BLUESKY_COMPRESS_TARGET_BYTES,
    BLUESKY_MAX_POST_LENGTH,
    _build_auth_request,
    _compress_thumb_to_jpeg,
    _guess_image_mime,
    _post_json,
    _safe_error,
    _safe_http_error,
    _upload_blob,
)
from bluefern_dispatches.care_line_release_render import load_approved_release
from bluefern_dispatches.care_line_social_cards import care_line_private_card_relative_path, ensure_care_line_social_card
from bluefern_dispatches.generator import BASE_URL


CARE_LINE_DISPATCH_SLUG = "care-line"
CARE_LINE_SOCIAL_IMAGE_PATH = "assets/care-line-dispatch-social.png"
CARE_LINE_SOCIAL_IMAGE_URL = f"{BASE_URL}/care-line/assets/care-line-dispatch-social.png"
CARE_LINE_SOCIAL_IMAGE_ALT = (
    "The Care Line Dispatch social card from The Blue Fern Co., with a healthcare-access motif "
    "and the subtitle Healthcare access dispatch."
)
CARE_LINE_BLUESKY_POST_STATE_FILENAME = "bluesky_post.json"
PREVIEW_DIR_NAME = "bluesky-preview"
PREVIEW_FILENAME = "care-line-bluesky-preview.json"
PREVIEW_HTML_FILENAME = "care-line-bluesky-preview.html"
IN_FEED_PREVIEW_HTML_FILENAME = "care-line-bluesky-in-feed-preview.html"
IN_FEED_PREVIEW_PNG_FILENAME = "care-line-bluesky-in-feed-preview.png"
BLUESKY_ACCOUNT_NAME = "The Blue Fern Co."
BLUESKY_ACCOUNT_HANDLE = "@thebluefernco.com"


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


def public_url_for_edition(edition_date: str) -> str:
    return f"{BASE_URL}/care-line/editions/{edition_date}/"


def social_image_path(project_root: Path) -> Path:
    return project_root / CARE_LINE_SOCIAL_IMAGE_PATH


def social_image_sha256(project_root: Path) -> str:
    return hashlib.sha256(social_image_path(project_root).read_bytes()).hexdigest()


def _card_title(edition_date: str) -> str:
    return f"Care Line - {_human_date(edition_date)}"


def _card_description() -> str:
    return "Read the source-backed U.S. healthcare access dispatch from The Blue Fern Co."


def deterministic_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _domain(public_url: str) -> str:
    parsed = urlparse(public_url)
    return parsed.netloc or "dispatches.thebluefernco.com"


def _limit_post_text(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("\n", " \n ").split())
    cleaned = cleaned.replace(" \n ", "\n\n")
    if len(cleaned) <= BLUESKY_MAX_POST_LENGTH:
        return cleaned
    return textwrap.shorten(cleaned, width=BLUESKY_MAX_POST_LENGTH, placeholder="...")


def _post_text(
    manifest: dict[str, Any],
    bundle_items: tuple[dict[str, Any], ...],
    proposal_summary: str = "",
) -> str:
    title = "Care Line Dispatch"
    edition_date = str(manifest.get("edition_date") or "").strip()
    lead_summary = str(
        proposal_summary
        or manifest.get("edition_summary")
        or manifest.get("public_summary")
        or manifest.get("public_archive_subtitle")
        or manifest.get("source_adequacy_label")
        or ""
    ).strip()
    if not lead_summary and bundle_items:
        lead_summary = str(bundle_items[0].get("bounded_public_summary") or bundle_items[0].get("approved_public_claim") or "").strip()
    body = lead_summary.rstrip(".") + "." if lead_summary else ""
    secondary_summary = ""
    for item in bundle_items[1:]:
        summary = str(item.get("bounded_public_summary") or item.get("approved_public_claim") or "").strip()
        if summary and summary != lead_summary:
            secondary_summary = summary
            break
    if secondary_summary:
        body = f"{body} Also covered: {secondary_summary.rstrip('.')}.".strip()
    if body:
        return f"{title} - {_human_date(edition_date)}\n\n{body}"
    return f"{title} - {_human_date(edition_date)}"


def _preview_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / "care-line" / "review" / PREVIEW_DIR_NAME / edition_date


def _load_manifest(project_root: Path, edition_date: str) -> dict[str, Any]:
    manifest_path = project_root / "output" / "site" / "care-line" / "editions" / edition_date / "edition_manifest.json"
    if not manifest_path.exists():
        return {
            "edition_date": edition_date,
            "public_url": public_url_for_edition(edition_date),
            "public_rendered": False,
            "public_signal_count": 0,
            "edition_mode": "private_preview",
            "validation_status": "private_preview",
        }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {manifest_path}")
    return payload


def build_care_line_bluesky_preview(project_root: Path, edition_date: str, *, refresh_card: bool = False) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest = _load_manifest(project_root, edition_date)
    bundle = load_approved_release(project_root, edition_date)
    if bundle is None:
        raise ValueError("Care Line edition is not release-ready")
    public_url = str(manifest.get("public_url") or public_url_for_edition(edition_date)).strip()
    raw_post_text = _post_text(manifest, bundle.approved_items, str(bundle.proposal.get("edition_summary") or "").strip())
    post_text = _limit_post_text(raw_post_text.encode("ascii", "replace").decode("ascii").replace("?", "-"))
    card_title = _card_title(edition_date)
    card_description = _card_description()
    image_path = ensure_care_line_social_card(project_root, edition_date, public_url=public_url, refresh_existing=refresh_card)
    image_rel = care_line_private_card_relative_path(edition_date)
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
    payload = {
        "schema_version": 2,
        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": public_url,
        "account": {
            "display_name": BLUESKY_ACCOUNT_NAME,
            "handle": BLUESKY_ACCOUNT_HANDLE,
            "avatar_path": "assets/bluefern.png",
        },
        "post_text": post_text,
        "post_text_length": len(post_text),
        "post_text_within_limit": len(post_text) <= BLUESKY_MAX_POST_LENGTH,
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
            "proposal_sha256": bundle.proposal_sha256,
            "review_sha256": bundle.review_snapshot_sha256,
            "review_item_ids": [
                item.get("review_item_id")
                for item in bundle.approved_items
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
            f"  <title>Care Line Bluesky In-Feed Preview - {escape(str(preview['edition_date']))}</title>",
            f'  <meta property="og:title" content="{title}">',
            f'  <meta property="og:description" content="{description}">',
            "  <style>",
            "    body { margin: 0; background: #eaf1f7; color: #0f1419; font-family: Arial, Helvetica, sans-serif; }",
            "    main { max-width: 660px; margin: 34px auto; padding: 0 18px; }",
            "    .boundary { margin: 0 0 18px; padding: 12px 14px; background: #071d33; color: #d7e8fb; border: 1px solid #6d91b8; border-radius: 10px; font-size: 14px; }",
            "    .post { background: white; border: 1px solid #b9cce0; border-top: 4px solid #1c4a51; border-radius: 16px; padding: 18px; box-shadow: 0 10px 32px rgba(10, 31, 54, 0.16); }",
            "    .author { display: grid; grid-template-columns: 52px 1fr; gap: 12px; align-items: center; margin-bottom: 12px; }",
            "    .avatar { width: 52px; height: 52px; border-radius: 999px; border: 1px solid #b9cce0; object-fit: cover; background: #f7f2eb; }",
            "    .name { font-weight: 800; font-size: 16px; }",
            "    .handle { color: #536471; font-size: 14px; margin-top: 2px; }",
            "    .text { font-size: 17px; line-height: 1.42; margin: 10px 0 14px 64px; white-space: normal; }",
            "    .embed { margin-left: 64px; border: 1px solid #cfd9e4; border-radius: 14px; overflow: hidden; background: #fff; }",
            "    .embed img { display: block; width: 100%; aspect-ratio: 1.904 / 1; object-fit: cover; background: #071d33; }",
            "    .meta { padding: 12px 14px 14px; }",
            "    .domain { color: #536471; font-size: 13px; margin-bottom: 4px; text-transform: lowercase; }",
            "    .title { font-weight: 800; font-size: 16px; line-height: 1.25; margin-bottom: 5px; }",
            "    .desc { color: #536471; font-size: 14px; line-height: 1.32; }",
            "    .trace { color: #536471; font-size: 12px; margin: 16px 0 0 64px; word-break: break-word; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            '    <div class="boundary">Private Care Line in-feed preview only. Not posted until explicit social approval.</div>',
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
            f'        <img src="{escape(image_src)}" alt="Care Line social card preview">',
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


def _draw_wrapped(
    draw: Any,
    xy: tuple[int, int],
    text: str,
    *,
    font: Any,
    fill: str,
    width: int,
    line_spacing: int = 8,
    max_lines: int | None = None,
) -> int:
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
    draw.rounded_rectangle((70, 84, 830, 92), radius=4, fill="#1c4a51")
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
    card = Image.open(card_path).convert("RGB").resize((620, 326))
    canvas.paste(card, (embed_left, embed_top))
    meta_y = embed_top + 344
    draw.text((embed_left + 16, meta_y), str(preview["card_domain"]).lower(), font=font(17), fill="#536471")
    y2 = _draw_wrapped(draw, (embed_left + 16, meta_y + 32), str(preview["card_title"]), font=font(23, bold=True), fill="#0f1419", width=570, max_lines=2)
    _draw_wrapped(draw, (embed_left + 16, y2 + 6), str(preview["card_description"]), font=font(19), fill="#536471", width=570, max_lines=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.parent / ".preview-tmp.png"
    canvas.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, output_path)


def write_care_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
    project_root = Path(project_root)
    preview = build_care_line_bluesky_preview(project_root, edition_date, refresh_card=True)
    preview_dir = _preview_path(project_root, edition_date)
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / PREVIEW_FILENAME
    html_path = preview_dir / PREVIEW_HTML_FILENAME
    in_feed_html_path = preview_dir / IN_FEED_PREVIEW_HTML_FILENAME
    in_feed_png_path = preview_dir / IN_FEED_PREVIEW_PNG_FILENAME
    card_path = project_root / str(preview["card_image_path"])
    image_src = _relative_asset_path(in_feed_html_path, card_path)
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
        "card_path": card_path,
    }


def _receipt_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / CARE_LINE_DISPATCH_SLUG / "editions" / edition_date / CARE_LINE_BLUESKY_POST_STATE_FILENAME


def _load_post_state_for_same_public_url(project_root: Path, edition_date: str, public_url: str) -> dict[str, Any] | None:
    path = _receipt_path(project_root, edition_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "") != "success":
        return None
    if not str(payload.get("post_uri") or "").strip():
        return None
    if str(payload.get("public_url") or "").strip() != str(public_url).strip():
        return None
    return payload


def _write_post_state(project_root: Path, edition_date: str, payload: dict[str, Any]) -> Path:
    path = _receipt_path(project_root, edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _thumbnail_candidates(project_root: Path, edition_date: str | None = None, card_image_path: str | None = None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if card_image_path:
        candidates.append(project_root / card_image_path)
    if edition_date:
        candidates.append(project_root / care_line_private_card_relative_path(edition_date))
    candidates.extend(
        (
            project_root / "assets" / "care-line-dispatch-social.png",
        project_root / "assets" / "care-line-logo.png",
        project_root / "assets" / "care-line-mark.png",
        project_root / "assets" / "bluefern.png",
        )
    )
    return tuple(dict.fromkeys(candidates))


def _upload_care_line_card_thumb(
    access_jwt: str,
    project_root: Path,
    *,
    edition_date: str | None = None,
    card_image_path: str | None = None,
) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None, Path | None]:
    for path in _thumbnail_candidates(project_root, edition_date, card_image_path):
        if not path.exists():
            continue
        mime = _guess_image_mime(path)
        if not mime:
            continue
        try:
            data = path.read_bytes()
            original_bytes = len(data)
            if original_bytes <= BLUESKY_COMPRESS_TARGET_BYTES:
                blob = _upload_blob(access_jwt, data, mime)
                if blob:
                    return blob, "uploaded", False, original_bytes, original_bytes, path
                return None, "upload_failed", False, original_bytes, None, path
            compressed = _compress_thumb_to_jpeg(data)
            if not compressed:
                return None, "skipped_too_large", False, original_bytes, None, path
            if len(compressed) >= BLUESKY_BLOB_MAX_BYTES:
                return None, "skipped_too_large", False, original_bytes, None, path
            blob = _upload_blob(access_jwt, compressed, "image/jpeg")
            if blob:
                return blob, "uploaded_compressed", True, original_bytes, len(compressed), path
            return None, "upload_failed", True, original_bytes, len(compressed), path
        except Exception:  # noqa: BLE001
            return None, "upload_failed", False, None, None, path
    return None, "no_thumbnail", False, None, None, None


def maybe_post_care_line_dispatch_to_bluesky(
    *,
    edition_date: str,
    public_url: str | None,
    post_text: str | None,
    run_succeeded: bool,
    public_rendered: bool,
    public_signal_count: int,
    post_requested: bool,
    project_root: Path | None = None,
    force_post: bool = False,
    allow_publish: bool = True,
    dry_run: bool = False,
    allow_text_only: bool = False,
    allow_archival_bluesky_post: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "skipped",
        "post_uri": None,
        "post_cid": None,
        "reason": None,
        "embed_type": None,
        "card_title": None,
        "card_description": None,
        "post_text": None,
        "image_path": None,
        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
        "thumb_status": "not_attempted",
        "compressed_thumb": False,
        "original_thumb_bytes": None,
        "uploaded_thumb_bytes": None,
        "error_type": None,
        "error_message": None,
        "state_path": None,
        "edition_date_verified": False,
        "public_rendered": bool(public_rendered),
        "public_signal_count": int(public_signal_count or 0),
        "dry_run": bool(dry_run),
        "forced_post": bool(force_post),
        "archival_override": bool(allow_archival_bluesky_post),
    }
    root = project_root or Path.cwd()
    state_path = _receipt_path(root, edition_date)
    result["state_path"] = str(state_path)
    if not run_succeeded:
        result["reason"] = "run_failed"
    elif not post_requested:
        result["reason"] = "disabled_by_config"
    elif not public_rendered:
        result["reason"] = "not_public_rendered"
    elif int(public_signal_count or 0) <= 0:
        result["reason"] = "no_public_signals"
    elif not public_url or not str(public_url).strip():
        result["reason"] = "missing_public_url"
    else:
        try:
            preview = build_care_line_bluesky_preview(root, edition_date)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "blocked"
            result["reason"] = _safe_error(str(exc), None)
        else:
            preview_post_text = str(preview["post_text"]).strip()
            if not preview_post_text:
                result["reason"] = "post_text_unavailable"
            else:
                result["post_text"] = preview_post_text
                if allow_archival_bluesky_post:
                    result["post_text"] = f"[ARCHIVAL / RETROSPECTIVE] {preview_post_text}"
                result["card_title"] = str(preview["card_title"])
                result["card_description"] = str(preview["card_description"])
                result["image_path"] = str(preview["card_image_path"])
                result["edition_date_verified"] = True
                receipt = _load_post_state_for_same_public_url(root, edition_date, str(public_url))
                if receipt and not force_post:
                    return {
                        **result,
                        "status": "skipped",
                        "reason": "skipped_existing_receipt",
                        "post_uri": receipt.get("post_uri"),
                        "post_cid": receipt.get("post_cid"),
                        "card_title": receipt.get("card_title") or result["card_title"],
                        "card_description": receipt.get("card_description") or result["card_description"],
                        "image_path": receipt.get("image_path"),
                        "image_alt": receipt.get("image_alt") or CARE_LINE_SOCIAL_IMAGE_ALT,
                        "thumb_status": receipt.get("thumb_status") or "not_attempted",
                        "compressed_thumb": False,
                        "original_thumb_bytes": receipt.get("original_thumb_bytes"),
                        "uploaded_thumb_bytes": receipt.get("uploaded_thumb_bytes"),
                        "state_path": str(state_path),
                    }
                if dry_run or not allow_publish:
                    return {
                        **result,
                        "status": "skipped",
                        "reason": "dry_run",
                        "thumb_status": "not_attempted",
                        "state_path": str(state_path),
                    }
                from bluefern_dispatches.care_line_bluesky_approval import verify_approval

                approval = verify_approval(root, edition_date)
                if not approval.get("ok"):
                    return {
                        **result,
                        "status": "blocked",
                        "reason": approval.get("reason") or "social_approval_not_granted",
                        "approval_path": approval.get("approval_path"),
                        "thumb_status": "not_attempted",
                        "state_path": str(state_path),
                    }
    if result["reason"] is not None:
        state_payload = {
            "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
            "edition_date": edition_date,
            "public_url": str(public_url or ""),
            "post_text": result["post_text"],
            "card_title": result["card_title"],
            "card_description": result["card_description"],
            "image_path": None,
            "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
            "status": "skipped",
            "skip_reason": result["reason"],
            "dry_run": bool(dry_run),
            "forced_post": bool(force_post),
            "post_uri": None,
            "post_cid": None,
            "embed_type": None,
            "thumb_status": "not_attempted",
            "posted_at": None,
        }
        if allow_publish and not dry_run:
            _write_post_state(root, edition_date, state_payload)
        return result

    handle = str(os.getenv("BLUESKY_HANDLE", "")).strip()
    app_password = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle:
        result["reason"] = "missing_handle"
    elif not app_password:
        result["reason"] = "missing_app_password"
    else:
        try:
            session = _post_json(
                f"{BLUESKY_API_BASE}/com.atproto.server.createSession",
                {"identifier": handle, "password": app_password},
            )
            access_jwt = str(session.get("accessJwt") or "")
            did = str(session.get("did") or "")
            if not access_jwt or not did:
                result["status"] = "failure"
                result["reason"] = "invalid_session_response"
            else:
                thumb_blob = None
                thumb_status = "no_thumbnail"
                compressed_thumb = False
                original_thumb_bytes = None
                uploaded_thumb_bytes = None
                image_path = None
                try:
                    thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes, image_path = _upload_care_line_card_thumb(
                        access_jwt,
                        root,
                        edition_date=edition_date,
                        card_image_path=str(result.get("image_path") or ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    thumb_blob = None
                    thumb_status = "upload_failed"
                    result["error_type"] = exc.__class__.__name__
                    result["error_message"] = str(exc)
                if not thumb_blob and not allow_text_only:
                    result["status"] = "blocked"
                    result["reason"] = "card_image_unavailable"
                    state_payload = {
                        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else None,
                        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
                        "status": "blocked",
                        "skip_reason": "card_image_unavailable",
                        "dry_run": False,
                        "forced_post": bool(force_post),
                        "post_uri": None,
                        "post_cid": None,
                        "embed_type": None,
                        "thumb_status": thumb_status,
                        "posted_at": None,
                    }
                    _write_post_state(root, edition_date, state_payload)
                    return result
                external: dict[str, Any] = {
                    "$type": "app.bsky.embed.external",
                    "external": {"uri": str(public_url), "title": result["card_title"], "description": result["card_description"]},
                }
                if thumb_blob:
                    external["external"]["thumb"] = thumb_blob
                record_payload = {
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": result["post_text"],
                        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "embed": external,
                    },
                }
                req = _build_auth_request(f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord", record_payload, access_jwt)
                with request.urlopen(req, timeout=20.0) as resp:
                    body = resp.read().decode("utf-8")
                payload = json.loads(body) if body else {}
                post_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
                post_cid = str(payload.get("cid") or "").strip() if isinstance(payload, dict) else ""
                if not post_uri:
                    result["status"] = "failure"
                    result["reason"] = "missing_post_uri"
                else:
                    result.update(
                        {
                            "status": "success",
                            "reason": None,
                            "post_uri": post_uri,
                            "post_cid": post_cid or None,
                            "embed_type": "app.bsky.embed.external",
                            "thumb_status": thumb_status,
                            "compressed_thumb": compressed_thumb,
                            "original_thumb_bytes": original_thumb_bytes,
                            "uploaded_thumb_bytes": uploaded_thumb_bytes,
                            "image_path": str(image_path) if image_path else (CARE_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        }
                    )
                    state_payload = {
                        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else (CARE_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
                        "status": "success",
                        "skip_reason": None,
                        "dry_run": False,
                        "forced_post": bool(force_post),
                        "post_uri": post_uri,
                        "post_cid": post_cid or None,
                        "embed_type": "app.bsky.embed.external",
                        "thumb_status": thumb_status,
                        "compressed_thumb": compressed_thumb,
                        "original_thumb_bytes": original_thumb_bytes,
                        "uploaded_thumb_bytes": uploaded_thumb_bytes,
                        "posted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
                    _write_post_state(root, edition_date, state_payload)
                    return result
        except error.HTTPError as exc:
            result["status"] = "failure"
            result["reason"], result["error_type"], result["error_message"] = _safe_http_error(exc, app_password)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failure"
            result["reason"] = _safe_error(str(exc), app_password)

    state_payload = {
        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": str(public_url or ""),
        "post_text": result["post_text"],
        "card_title": result["card_title"],
        "card_description": result["card_description"],
        "image_path": result["image_path"],
        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
        "status": result["status"],
        "skip_reason": result["reason"],
        "dry_run": bool(dry_run),
        "forced_post": bool(force_post),
        "post_uri": result["post_uri"],
        "post_cid": result["post_cid"],
        "embed_type": result["embed_type"],
        "thumb_status": result["thumb_status"],
        "compressed_thumb": result["compressed_thumb"],
        "original_thumb_bytes": result["original_thumb_bytes"],
        "uploaded_thumb_bytes": result["uploaded_thumb_bytes"],
        "posted_at": None,
    }
    if allow_publish and not dry_run:
        _write_post_state(root, edition_date, state_payload)
    return result
