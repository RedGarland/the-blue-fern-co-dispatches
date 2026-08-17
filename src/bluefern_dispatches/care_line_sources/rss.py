from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .base import _text, normalize_publication_date, strip_html
from .structured_index import parse as parse_structured_index


def parse(payload: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return parse_structured_index(payload)
    items = []
    for node in root.findall(".//item"):
        guid = node.find("guid")
        items.append(
            {
                "title": node.findtext("title") or "",
                "url": node.findtext("link") or "",
                "published_at": normalize_publication_date(node.findtext("pubDate") or node.findtext("published") or ""),
                "description": strip_html(node.findtext("description") or ""),
                "source": node.findtext("source") or "",
                "id": (guid.text or "") if guid is not None else "",
            }
        )
    return items
