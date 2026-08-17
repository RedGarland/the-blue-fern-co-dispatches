from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

from bluefern_dispatches.food_line_bluesky_preview import build_food_line_bluesky_preview, write_food_line_bluesky_preview


def _fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "output" / "site" / "food-line" / "editions" / "2026-08-14").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "review" / "proposed-editions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "site" / "food-line" / "editions" / "2026-08-14" / "edition_manifest.json").write_text(
        json.dumps(
            {
                "edition_date": "2026-08-14",
                "headline": "OPB's First Look: Soaring food prices, shrinking SNAP benefits",
                "public_url": "https://dispatches.thebluefernco.com/food-line/editions/2026-08-14/",
                "public_summary": "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households.",
                "approved_proposal_sha256": "449478f3c41bc96792708ea5892038a91097bbbb28031b6adf37a01f68ed172c",
                "review_snapshot_sha256": "6408643c0c22ab49434b4ab169f138569d101dfe675663433b12e0a659579c92",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / "2026-08-14.json").write_text(
        json.dumps(
            {
                "layout": {
                    "todays_read": [
                        {
                            "summary": "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households.",
                        }
                    ],
                    "core_food_pressure_signals": [
                        {
                            "summary": "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households.",
                        },
                        {
                            "summary": "SummitDaily.com reported household food hardship in Colorado, affecting children.",
                        },
                    ],
                },
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "assets" / "food-line-dispatch-social.png").write_bytes(
        Path("assets/food-line-dispatch-social.png").read_bytes()
    )
    return tmp_path


def test_preview_builds_deterministic_editorial_payload(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    preview_1 = build_food_line_bluesky_preview(root, "2026-08-14")
    preview_2 = build_food_line_bluesky_preview(root, "2026-08-14")

    assert preview_1 == preview_2
    assert preview_1["card_title"].startswith("Food Line")
    assert preview_1["card_description"] == "Read the source-backed U.S. food pressure update from The Blue Fern Co."
    assert preview_1["embed"] == {
        "uri": "https://dispatches.thebluefernco.com/food-line/editions/2026-08-14/",
        "title": preview_1["card_title"],
        "description": "Read the source-backed U.S. food pressure update from The Blue Fern Co.",
    }
    assert "2 approved stories" not in preview_1["post_text"]
    assert "#" not in preview_1["post_text"]
    assert "source count" not in preview_1["post_text"].lower()
    assert "record food-bank demand" in preview_1["post_text"]
    assert "household food hardship in Colorado" in preview_1["post_text"]
    assert preview_1["post_text"].startswith("Food Line Dispatch")
    assert "Also covered:" in preview_1["post_text"]
    assert preview_1["card_image_path"] == "assets/food-line-dispatch-social.png"
    assert preview_1["card_image_sha256"]


def test_preview_written_artifacts_are_stable(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    result = write_food_line_bluesky_preview(root, "2026-08-14")
    json_path = result["json_path"]
    html_path = result["html_path"]

    assert json_path.exists()
    assert html_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["edition_date"] == "2026-08-14"
    assert payload["embed"]["uri"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-08-14/"
    assert payload["embed"]["title"].startswith("Food Line")
    assert payload["embed"]["description"] == "Read the source-backed U.S. food pressure update from The Blue Fern Co."
    assert payload["card_image_path"] == "assets/food-line-dispatch-social.png"
    assert payload["content_sha256"]
    html_text = html_path.read_text(encoding="utf-8")
    assert "Food Line Bluesky Preview" in html_text
    assert "no network" not in html_text.lower()


def test_preview_card_asset_matches_approved_food_line_social_card() -> None:
    png = Path("assets/food-line-dispatch-social.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert hashlib.sha256(png).hexdigest().upper() == "6A3D748926407691C99B8FB2869E74CEDD770A07D592350AE0C4B0721DB3AF24"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (1731, 909)


def test_preview_uses_review_artifact_and_public_summary_without_boilerplate(tmp_path: Path) -> None:
    preview = build_food_line_bluesky_preview(_fixture_root(tmp_path), "2026-08-14")
    assert "approved stories" not in preview["post_text"]
    assert "source count" not in preview["post_text"].lower()
    assert preview["post_text"].startswith("Food Line Dispatch")
    assert "Oregon Public Broadcasting reported record food-bank demand" in preview["post_text"]
    assert "SummitDaily.com reported household food hardship in Colorado" in preview["post_text"]


def test_preview_formats_single_and_multi_item_editions_consistently(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)

    multi_item_preview = build_food_line_bluesky_preview(root, "2026-08-14")
    assert multi_item_preview["post_text"].startswith("Food Line Dispatch")
    assert "Also covered:" in multi_item_preview["post_text"]
    assert "SummitDaily.com reported household food hardship in Colorado, affecting children." in multi_item_preview["post_text"]

    (root / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / "2026-08-14.json").write_text(
        json.dumps(
            {
                "layout": {
                    "todays_read": [
                        {
                            "summary": "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households.",
                        }
                    ],
                    "core_food_pressure_signals": [
                        {
                            "summary": "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households.",
                        }
                    ],
                },
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    single_item_preview = build_food_line_bluesky_preview(root, "2026-08-14")
    assert single_item_preview["post_text"].startswith("Food Line Dispatch")
    assert "Also covered:" not in single_item_preview["post_text"]
    assert "Oregon Public Broadcasting reported record food-bank demand and constrained food-bank supply in Oregon, amid rising food prices and pressure on SNAP households." in single_item_preview["post_text"]
