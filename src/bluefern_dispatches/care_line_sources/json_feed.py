from __future__ import annotations

import json
from typing import Any

from .base import _text, normalize_publication_date, strip_html


def parse(payload: bytes) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8"))
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "title": _text(item, "title"),
                "url": _text(item, "url", "external_url", "id"),
                "published_at": normalize_publication_date(_text(item, "date_published", "date_modified")),
                "description": strip_html(_text(item, "summary", "content_text", "content_html")),
                "source": _text(data, "title"),
                "id": _text(item, "id"),
            }
        )
    return items

