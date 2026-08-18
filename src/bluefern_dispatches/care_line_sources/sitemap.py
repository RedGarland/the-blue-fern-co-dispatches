from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .base import normalize_publication_date


def parse(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    items = []
    for node in root.findall(".//{*}url"):
        loc = node.findtext("{*}loc") or node.findtext("loc") or ""
        lastmod = node.findtext("{*}lastmod") or node.findtext("lastmod") or ""
        items.append(
            {
                "title": loc.rsplit("/", 1)[-1].replace("-", " ").strip(),
                "url": loc,
                "published_at": normalize_publication_date(lastmod),
                "description": "",
                "source": "",
                "id": loc,
            }
        )
    return items

