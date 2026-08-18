from __future__ import annotations

import argparse
import binascii
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


EVENT_ONE = "events/event_3b4ad4e528e48744/index.html"
EVENT_TWO = "events/event_a12dae614b86cfa9/index.html"
EVENT_ONE_CARD = "events/event_3b4ad4e528e48744/social-card.png"
EVENT_TWO_CARD = "events/event_a12dae614b86cfa9/social-card.png"
SIGNALS_INDEX = "signals/index.html"
SIGNALS_FEED = "signals/feed.xml"
CARE_LINE_INDEX = "care-line/signals/index.html"
CARE_LINE_FEED = "care-line/signals/feed.xml"

PUBLIC_PATHS = (EVENT_ONE, EVENT_TWO, EVENT_ONE_CARD, EVENT_TWO_CARD, SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED)
EXPECTED_PNG_URLS = {
    "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/social-card.png",
    "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/social-card.png",
}
FORBIDDEN_STRINGS = (
    "Children?s",
    "service_restoration",
    "service_expansion",
    "Care Line Universal Event",
    "Source candidate",
    "Producer record",
    "Evidence decision",
    "Record fingerprint",
    "Review packet fingerprint",
    "Deterministic provenance",
)


def _read_text(root: Path, relative_path: str) -> str:
    return (root / relative_path).read_text(encoding="utf-8")


def _read_bytes(root: Path, relative_path: str) -> bytes:
    return (root / relative_path).read_bytes()


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
        "https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/",
        "https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/",
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
    if require_children and not ("Childrenâ€™s" in text or "Children&#x27;s" in text):
        errors.append(f"{path}: missing corrected Childrenâ€™s text")
    if require_temporary_extension and "Temporary network-access extension" not in text:
        errors.append(f"{path}: missing temporary network-access extension wording")

    for forbidden in FORBIDDEN_STRINGS:
        if forbidden in text:
            errors.append(f"{path}: contains forbidden string {forbidden!r}")

    return errors


def _validate_event_page(path: str, text: str, *, title: str, image_url: str, image_alt: str) -> list[str]:
    errors = _validate_public_text(path, text)
    required_snippets = (
        '<meta property="og:type" content="article">',
        '<meta property="og:site_name" content="The Blue Fern Co.">',
        f'<meta property="og:title" content="{title}">',
        f'<meta name="twitter:title" content="{title}">',
        f'<meta property="og:image" content="{image_url}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{image_alt}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:image" content="{image_url}">',
        f'<meta name="twitter:image:alt" content="{image_alt}">',
    )
    for snippet in required_snippets:
        if snippet not in text:
            errors.append(f"{path}: missing required metadata snippet {snippet!r}")
    if ".svg" in text and "social-card.svg" in text:
        errors.append(f"{path}: public social metadata still references SVG")
    return errors


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png(path: str, data: bytes, *, expected_width: int = 1200, expected_height: int = 630) -> list[str]:
    errors: list[str] = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return [f"{path}: missing PNG signature"]

    offset = 8
    width = height = None
    bit_depth = color_type = compression = filter_method = interlace = None
    idat = bytearray()
    saw_iend = False

    while offset < len(data):
        if offset + 8 > len(data):
            errors.append(f"{path}: truncated PNG chunk header")
            break
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        chunk_type = data[offset : offset + 4]
        offset += 4
        if offset + length + 4 > len(data):
            errors.append(f"{path}: truncated PNG chunk {chunk_type!r}")
            break
        chunk = data[offset : offset + length]
        offset += length
        crc_expected = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        crc_actual = binascii.crc32(chunk_type)
        crc_actual = binascii.crc32(chunk, crc_actual) & 0xFFFFFFFF
        if crc_actual != crc_expected:
            errors.append(f"{path}: CRC mismatch in {chunk_type.decode('ascii', errors='replace')} chunk")
            break
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack_from(">IIBBBBB", chunk, 0)
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            saw_iend = True
            break

    if not saw_iend:
        errors.append(f"{path}: missing IEND chunk")
    if width != expected_width or height != expected_height:
        errors.append(f"{path}: unexpected dimensions {(width, height)}")
    if bit_depth != 8 or color_type not in {2, 6} or compression != 0 or filter_method != 0 or interlace != 0:
        errors.append(f"{path}: unsupported PNG encoding mode")
        return errors

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        return errors + [f"{path}: could not decompress IDAT stream: {exc}"]

    channels = 4 if color_type == 6 else 3
    row_bytes = width * channels + 1
    expected_raw_length = height * row_bytes
    if len(raw) != expected_raw_length:
        errors.append(f"{path}: unexpected decompressed size {len(raw)} != {expected_raw_length}")
        return errors

    decoded = bytearray(height * width * channels)
    prev_row = bytearray(width * channels)
    src = 0
    dst = 0
    for _row in range(height):
        filter_type = raw[src]
        src += 1
        row = bytearray(raw[src : src + width * channels])
        src += width * channels
        recon = bytearray(width * channels)
        if filter_type == 0:
            recon[:] = row
        elif filter_type == 1:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                recon[i] = (value + left) & 0xFF
        elif filter_type == 2:
            for i, value in enumerate(row):
                recon[i] = (value + prev_row[i]) & 0xFF
        elif filter_type == 3:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                up = prev_row[i]
                recon[i] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for i, value in enumerate(row):
                left = recon[i - channels] if i >= channels else 0
                up = prev_row[i]
                up_left = prev_row[i - channels] if i >= channels else 0
                recon[i] = (value + _paeth(left, up, up_left)) & 0xFF
        else:
            errors.append(f"{path}: unsupported PNG filter {filter_type}")
            return errors
        decoded[dst : dst + len(recon)] = recon
        dst += len(recon)
        prev_row = recon

    if not decoded:
        errors.append(f"{path}: decoded image is empty")
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
    event_one_png = _read_bytes(root, EVENT_ONE_CARD)
    event_two_png = _read_bytes(root, EVENT_TWO_CARD)

    errors.extend(
        _validate_event_page(
            EVENT_ONE,
            event_one,
            title="UCSF debuts new unit for neurosurgical patients",
            image_url="https://dispatches.thebluefernco.com/events/event_3b4ad4e528e48744/social-card.png",
            image_alt="The Blue Fern Co. Care Line social card for UCSF opens 8-bed pediatric neuroscience unit",
        )
    )
    errors.extend(
        _validate_event_page(
            EVENT_TWO,
            event_two,
            title="UnitedHealthcare, ECU Health extend agreement until August",
            image_url="https://dispatches.thebluefernco.com/events/event_a12dae614b86cfa9/social-card.png",
            image_alt="The Blue Fern Co. Care Line social card for ECU Health extends in-network access",
        )
    )
    errors.extend(_decode_png(EVENT_ONE_CARD, event_one_png))
    errors.extend(_decode_png(EVENT_TWO_CARD, event_two_png))

    for path, text in (
        (SIGNALS_INDEX, signals_index),
        (SIGNALS_FEED, signals_feed),
        (CARE_LINE_INDEX, care_line_index),
        (CARE_LINE_FEED, care_line_feed),
    ):
        errors.extend(
            _validate_public_text(
                path,
                text,
                require_children=path in {SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED},
                require_temporary_extension=path in {SIGNALS_INDEX, SIGNALS_FEED, CARE_LINE_INDEX, CARE_LINE_FEED},
            )
        )

    errors.extend(_validate_feed(SIGNALS_FEED, signals_feed, "https://dispatches.thebluefernco.com/signals/"))
    errors.extend(_validate_feed(CARE_LINE_FEED, care_line_feed, "https://dispatches.thebluefernco.com/care-line/signals/"))

    if "event_3b4ad4e528e48744" not in event_one or "event_a12dae614b86cfa9" not in event_two:
        errors.append("event pages do not preserve the expected event IDs")
    if "Source candidate" in event_one or "Source candidate" in event_two:
        errors.append("public event pages expose source candidate lineage")
    if "social-card.svg" in event_one or "social-card.svg" in event_two:
        errors.append("public event pages expose stale SVG metadata")
    if len([path for path in PUBLIC_PATHS if path.endswith(".png")]) != 2:
        errors.append("unexpected public social-card count")

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
