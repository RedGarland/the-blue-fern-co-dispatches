from __future__ import annotations

import html
import json
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "msclkid",
}


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_publication_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    return match.group(0) if match else raw[:10]


def normalize_article_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_QUERY_PARAMS],
        doseq=True,
    )
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def hostname(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def json_subset(item: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "title",
        "link",
        "url",
        "source",
        "publisher",
        "published",
        "published_at",
        "pubDate",
        "summary",
        "description",
        "id",
        "guid",
        "query",
        "feed_url",
        "article_url",
        "canonical_url",
        "source_url",
    )
    subset: dict[str, Any] = {}
    for key in allowed:
        if key in item:
            value = item[key]
            if isinstance(value, str):
                subset[key] = value[:2000]
            elif isinstance(value, Mapping):
                subset[key] = {
                    str(k): (str(v)[:1000] if not isinstance(v, (dict, list)) else str(v)[:1000])
                    for k, v in value.items()
                    if str(k).lower() not in {"token", "secret", "password", "authorization"}
                }
            else:
                subset[key] = value
    return subset

