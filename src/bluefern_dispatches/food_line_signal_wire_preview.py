from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from html import escape

from bluefern_dispatches.bluesky_post import BLUESKY_MAX_POST_LENGTH
from bluefern_dispatches.food_line_bluesky_preview import deterministic_json
from bluefern_dispatches.food_line_signal_wire import (
    CARD_DIR_NAME,
    CARD_SIZE,
    CURRENT_AS_OF,
    PREVIEW_HTML_NAME,
    PREVIEW_JSON_NAME,
    PREVIEW_ROOT,
    _compose_post,
    _event_from_record,
    _hash_text,
    _load_json,
    _render_card,
    build_current_event_eligibility_fixture,
)


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
        "public_permalink_contract": f"{preview_base_url()}/food-line/wire/<signal-id>/",
        "card_dimensions": {"width": CARD_SIZE[0], "height": CARD_SIZE[1]},
        "bluesky_post_limit": BLUESKY_MAX_POST_LENGTH,
        "examples": examples,
    }
    for event in examples:
        event["card_image_path"] = (PREVIEW_ROOT / CARD_DIR_NAME / f"{event['signal_id']}.png").as_posix()
    preview["content_sha256"] = _hash_text(preview)
    return preview


def preview_base_url() -> str:
    return "https://dispatches.thebluefernco.com"


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
