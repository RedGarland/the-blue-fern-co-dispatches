from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .base import normalize_publication_date
from .structured_index import parse as parse_structured_index


def parse(payload: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return parse_structured_index(payload)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    nodes = root.findall(".//atom:entry", ns) or root.findall(".//entry")
    items = []
    for node in nodes:
        link = ""
        for link_node in node.findall("atom:link", ns) or node.findall("link"):
            rel = link_node.attrib.get("rel", "alternate")
            if rel == "alternate" and link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        items.append(
            {
                "title": node.findtext("atom:title", default="", namespaces=ns) or node.findtext("title") or "",
                "url": link or node.findtext("atom:id", default="", namespaces=ns) or node.findtext("id") or "",
                "published_at": normalize_publication_date(
                    node.findtext("atom:published", default="", namespaces=ns) or node.findtext("atom:updated", default="", namespaces=ns) or ""
                ),
                "description": node.findtext("atom:summary", default="", namespaces=ns) or node.findtext("atom:content", default="", namespaces=ns) or "",
                "source": "",
                "id": node.findtext("atom:id", default="", namespaces=ns) or "",
            }
        )
    return items
