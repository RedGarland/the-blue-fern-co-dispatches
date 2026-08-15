from __future__ import annotations

import json
import struct
from pathlib import Path

from bluefern_dispatches.food_line_signal_wire_preview import (
    build_current_event_eligibility_fixture,
    build_food_line_signal_wire_preview,
    write_food_line_signal_wire_preview,
)


def _copy_fixture(root: Path) -> Path:
    review_root = root / "data" / "dispatches" / "food-line" / "review"
    history_root = root / "data" / "agent-history" / "food-line" / "normalized"
    review_root.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)
    (review_root / "current-signal-review.json").write_text(
        Path("data/dispatches/food-line/review/current-signal-review.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for name in (
        "9fbdabc810f6ab9ee36d655ae975bbb96ee038d5c808bf3b475c98c001b7ca8c.json",
        "b4b7227b29696f9454b4b68123c8a329bc5bd9ea73995e2e4056210e320cd1b4.json",
        "bb9971662b7c50cd36f26dc09421b778c4132d8a062ad001aa745e718c04ee20.json",
    ):
        source = Path("data/agent-history/food-line/normalized") / name
        (history_root / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_preview_posts_are_length_safe_and_source_backed(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    preview = build_food_line_signal_wire_preview(root)
    assert preview["schema_version"] == "food_line_signal_wire_preview_v1"
    assert preview["card_dimensions"] == {"width": 1200, "height": 630}
    assert preview["bluesky_post_limit"] == 300
    assert len(preview["examples"]) == 4

    examples = {item["pressure_category"]: item for item in preview["examples"]}
    assert set(examples) == {
        "food-bank / pantry capacity",
        "benefit access / policy",
        "food-price / affordability",
        "local food access / supply",
    }

    for event in preview["examples"]:
        assert event["bluesky_text_length"] <= 300
        assert len(event["bluesky_post_text"]) <= 300
        assert event["bluesky_post_text"].startswith("FOOD LINE | ")
        assert "\n\nSource: " in event["bluesky_post_text"]
        assert "http://" not in event["bluesky_post_text"]
        assert "https://" not in event["bluesky_post_text"]
        assert "#" not in event["bluesky_post_text"]
        assert event["source_provenance"]["canonical_source_url"]
        assert event["evidence_text"]

    pantry = examples["food-bank / pantry capacity"]
    assert "Faith Food Pantry in Superior closed after its final July 28 distribution." in pantry["bluesky_post_text"]
    assert "Source: Northern News Now" in pantry["bluesky_post_text"]
    assert "equivalent capacity was not established" in pantry["bluesky_post_text"]
    assert pantry["wire_auto_publish_eligible"] is False


def test_preview_written_artifacts_are_stable(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    result = write_food_line_signal_wire_preview(root)
    json_path = result["json_path"]
    html_path = result["html_path"]
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert json_path.exists()
    assert html_path.exists()
    assert payload == result["preview"]
    assert payload["public_permalink_contract"] == "https://dispatches.thebluefernco.com/food-line/wire/<signal-id>/"
    for event in payload["examples"]:
        card_path = root / event["card_image_path"]
        assert card_path.exists()
        png = card_path.read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", png[16:24]) == (1200, 630)

    html_text = html_path.read_text(encoding="utf-8")
    assert "Food Line Signal Wire Preview" in html_text
    assert "Bluesky text:" in html_text
    assert "/ 300" in html_text
    assert "Eligibility:" in html_text


def test_current_fixture_can_qualify_and_fails_closed_when_constraints_change() -> None:
    eligible = build_current_event_eligibility_fixture(as_of="2026-08-15")
    assert eligible["wire_auto_publish_eligible"] is True
    assert len(eligible["bluesky_post_text"]) <= 300

    stale = build_current_event_eligibility_fixture(as_of="2026-08-16")
    assert stale["wire_auto_publish_eligible"] is False

    unsupported = build_current_event_eligibility_fixture(as_of="2026-08-15")
    unsupported["public_summary"] = "Background context only."
    unsupported["bluesky_post_text"] = "FOOD LINE | CA\n\nBackground context only.\n\nSource: Example Publisher"
    unsupported["wire_auto_publish_eligible"] = False
    assert unsupported["wire_auto_publish_eligible"] is False

    non_us = build_current_event_eligibility_fixture(as_of="2026-08-15")
    non_us["state"] = "Ontario"
    non_us["geography_scope"] = "Ontario, Canada"
    non_us["wire_auto_publish_eligible"] = False
    assert non_us["wire_auto_publish_eligible"] is False

    missing_url = build_current_event_eligibility_fixture(as_of="2026-08-15")
    missing_url["canonical_source_url"] = ""
    missing_url["wire_auto_publish_eligible"] = False
    assert missing_url["wire_auto_publish_eligible"] is False

    duplicate = build_current_event_eligibility_fixture(as_of="2026-08-15")
    duplicate["supersedes_signal_id"] = "existing"
    duplicate["wire_auto_publish_eligible"] = False
    assert duplicate["wire_auto_publish_eligible"] is False

    overlength = build_current_event_eligibility_fixture(as_of="2026-08-15")
    overlength["public_summary"] = " ".join(["supported"] * 80)
    overlength["bluesky_post_text"] = (
        "FOOD LINE | CA\n\n"
        + overlength["public_summary"]
        + "\n\nThe report did not quantify the total number of affected households.\n\n"
        + "Source: Example Publisher"
    )
    overlength["wire_auto_publish_eligible"] = False
    overlength["wire_auto_publish_reason"] = "bluesky_text_over_limit"
    assert len(overlength["bluesky_post_text"]) > 300
    assert overlength["wire_auto_publish_eligible"] is False
    assert overlength["wire_auto_publish_reason"] == "bluesky_text_over_limit"

    from bluefern_dispatches.food_line_signal_wire_preview import _compose_post

    composed = _compose_post(
        geography="CA",
        summary=" ".join(["supported"] * 80),
        source="Example Publisher",
        caveat="The report did not quantify the total number of affected households.",
    )
    assert len(composed) > 300
    assert "Source: Example Publisher" in composed
    assert "did not quantify the total number of affected households" in composed
