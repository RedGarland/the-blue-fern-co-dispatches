from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any

from .base import normalize_publication_date, strip_html, _text


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "a":
            attr_map = {k.lower(): v for k, v in attrs}
            href = attr_map.get("href", "")
            self._current = {"url": href, "title": "", "description": "", "source": "", "id": href}

    def handle_data(self, data: str):
        if self._current is not None:
            self._current["title"] = f"{self._current['title']} {data}".strip()

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._current is not None:
            self._current["title"] = strip_html(self._current["title"])
            self.items.append(self._current)
            self._current = None


def parse(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("items") or data.get("entries") or []
        items: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "title": _text(item, "title", "name"),
                    "url": _text(item, "url", "link", "canonical_url"),
                    "published_at": normalize_publication_date(_text(item, "published_at", "published", "date_published", "date")),
                    "description": strip_html(_text(item, "description", "summary", "excerpt")),
                    "source": _text(data if isinstance(data, dict) else {}, "title", "name"),
                    "id": _text(item, "id", "guid", "source_id"),
                }
            )
        return items
    parser = _AnchorParser()
    parser.feed(text)
    return parser.items

