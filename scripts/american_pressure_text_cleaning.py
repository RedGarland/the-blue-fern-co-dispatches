from __future__ import annotations

import html
import re
from typing import Any


HTML_TAG_RE = re.compile(r"<[^>]+>")
GOOGLE_RSS_URL_RE = re.compile(r"https?://news\.google\.com/rss/articles/[^\s\"'<>]+", re.IGNORECASE)
TRACKING_TOKEN_RE = re.compile(r"\(([A-Za-z0-9_-]{8,})\)")
WHITESPACE_RE = re.compile(r"\s+")


def safe_text(value: Any) -> str:
    return str(value or "").strip()


def clean_candidate_text(value: Any) -> str:
    text = safe_text(value)
    if not text:
        return ""
    text = html.unescape(text)
    text = GOOGLE_RSS_URL_RE.sub("", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = TRACKING_TOKEN_RE.sub("", text)
    text = re.sub(r"\s+\|\s+[^|]{2,80}$", "", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text


def clean_google_rss_title(title: Any, publisher: Any = "") -> str:
    cleaned = clean_candidate_text(title)
    original_cleaned = cleaned
    publisher_text = clean_candidate_text(publisher)
    if publisher_text:
        suffix = f" - {publisher_text}".lower()
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    cleaned = re.sub(r"\s+-\s+[A-Za-z][\w .&'/-]{2,60}$", "", cleaned).strip()
    return cleaned or original_cleaned


def contains_forbidden_public_markup(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("<a ", "</a>", "<font", "href=", "target=", "news.google.com/rss/articles"))
