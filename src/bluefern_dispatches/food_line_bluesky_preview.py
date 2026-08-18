from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import Path
from typing import Any

from bluefern_dispatches.food_line_bluesky_approval import public_url_for_edition


PREVIEW_DIR_NAME = "bluesky-preview"
PREVIEW_FILENAME = "food-line-bluesky-preview.json"
PREVIEW_HTML_FILENAME = "food-line-bluesky-preview.html"
PREVIEW_IMAGE_PATH = Path("assets/food-line-dispatch-social.png")


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


def build_food_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
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
    image_path = project_root / PREVIEW_IMAGE_PATH
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
    payload = {
        "schema_version": 1,
        "dispatch_slug": "food-line",
        "edition_date": edition_date,
        "public_url": public_url,
        "post_text": post_text,
        "card_title": card_title,
        "card_description": card_description,
        "card_image_path": PREVIEW_IMAGE_PATH.as_posix(),
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


def write_food_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
    preview = build_food_line_bluesky_preview(project_root, edition_date)
    preview_dir = project_root / "data" / "dispatches" / "food-line" / "review" / PREVIEW_DIR_NAME / edition_date
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / PREVIEW_FILENAME
    html_path = preview_dir / PREVIEW_HTML_FILENAME
    json_path.write_text(deterministic_json(preview) + "\n", encoding="utf-8")
    html_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '  <meta charset="utf-8">',
                f"  <title>Food Line Bluesky Preview - {edition_date}</title>",
                f'  <meta property="og:title" content="{escape(str(preview["card_title"]))}">',
                f'  <meta property="og:description" content="{escape(str(preview["card_description"]))}">',
                "</head>",
                "<body>",
                "  <h1>Food Line Bluesky Preview</h1>",
                f"  <p><strong>Edition:</strong> {escape(edition_date)}</p>",
                f"  <p><strong>Post text:</strong> {escape(str(preview['post_text'])).replace(chr(10), '<br>')}</p>",
                f"  <p><strong>Embed URI:</strong> {escape(str(preview['embed']['uri']))}</p>",
                f"  <p><strong>Card image:</strong> {escape(str(preview['card_image_path']))}</p>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "preview": preview,
        "json_path": json_path,
        "html_path": html_path,
    }
