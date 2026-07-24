from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


EVENT_ONE = "events/event_3b4ad4e528e48744/index.html"
EVENT_TWO = "events/event_a12dae614b86cfa9/index.html"
SIGNALS_INDEX = "signals/index.html"
SIGNALS_FEED = "signals/feed.xml"
CARE_LINE_INDEX = "care-line/signals/index.html"
CARE_LINE_FEED = "care-line/signals/feed.xml"

PUBLIC_PATHS = (EVENT_ONE, EVENT_TWO, SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED)
FORBIDDEN_STRINGS = (
    "Children?s",
    "service_restoration",
    "service_expansion",
    "Source candidate",
    "Producer record",
    "Evidence decision",
    "Record fingerprint",
    "Review packet fingerprint",
    "Deterministic provenance",
)


def _read_text(root: Path, relative_path: str) -> str:
    return (root / relative_path).read_text(encoding="utf-8")


def _validate_feed(path: str, text: str, expected_link: str) -> list[str]:
    errors: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"{path}: invalid RSS XML: {exc}"]

    items = root.findall("./channel/item")
    if len(items) != 2:
        errors.append(f"{path}: expected exactly 2 RSS items, found {len(items)}")
    guids = [item.findtext("guid") for item in items]
    expected_guids = [
        f"https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/",
        f"https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/",
    ]
    if guids != expected_guids:
        errors.append(f"{path}: unexpected RSS GUIDs: {guids}")
    if root.findtext("./channel/link") != expected_link:
        errors.append(f"{path}: unexpected channel link: {root.findtext('./channel/link')}")
    return errors


def _validate_public_text(path: str, text: str, *, require_children: bool = False, require_temporary_extension: bool = False) -> list[str]:
    errors: list[str] = []

    if "Children?s" in text:
        errors.append(f"{path}: contains mojibake Children?s")
    if require_children and not ("Children’s" in text or "Children&#x27;s" in text):
        errors.append(f"{path}: missing corrected Children’s text")
    if require_temporary_extension and "Temporary network-access extension" not in text:
        errors.append(f"{path}: missing temporary network-access extension wording")

    for forbidden in FORBIDDEN_STRINGS:
        if forbidden in text:
            errors.append(f"{path}: contains forbidden string {forbidden!r}")

    return errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []

    missing = [path for path in PUBLIC_PATHS if not (root / path).exists()]
    if missing:
        errors.extend(f"missing file: {path}" for path in missing)
        return errors

    event_one = _read_text(root, EVENT_ONE)
    event_two = _read_text(root, EVENT_TWO)
    signals_index = _read_text(root, SIGNALS_INDEX)
    signals_feed = _read_text(root, SIGNALS_FEED)
    care_line_index = _read_text(root, CARE_LINE_INDEX)
    care_line_feed = _read_text(root, CARE_LINE_FEED)

    for path, text in (
        (EVENT_ONE, event_one),
        (EVENT_TWO, event_two),
        (SIGNALS_INDEX, signals_index),
        (SIGNALS_FEED, signals_feed),
        (CARE_LINE_INDEX, care_line_index),
        (CARE_LINE_FEED, care_line_feed),
    ):
        errors.extend(
            _validate_public_text(
                path,
                text,
                require_children=path in {EVENT_ONE, SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED},
                require_temporary_extension=path in {EVENT_TWO, SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED},
            )
        )

    errors.extend(_validate_feed(SIGNALS_FEED, signals_feed, "https://dispatches.thebluefernco.com/signals/"))
    errors.extend(_validate_feed(CARE_LINE_FEED, care_line_feed, "https://dispatches.thebluefernco.com/care-line/signals/"))

    if "event_3b4ad4e528e48744" not in event_one or "event_a12dae614b86cfa9" not in event_two:
        errors.append("event pages do not preserve the expected event IDs")
    if "Source candidate" in event_one or "Source candidate" in event_two:
        errors.append("public event pages expose source candidate lineage")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Care Line Signal Wire public publication artifacts.")
    parser.add_argument("--root", type=Path, default=Path("output") / "site", help="Site root containing the six public Signal Wire files.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    errors = validate(root)
    if errors:
        print("Care Line Signal Wire validation failed.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("Care Line Signal Wire validation passed.")
    print(f"- Root: {root}")
    for path in PUBLIC_PATHS:
        print(f"- Checked: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
