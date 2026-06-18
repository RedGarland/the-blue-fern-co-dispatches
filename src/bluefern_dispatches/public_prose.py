from __future__ import annotations

import re
from typing import Iterable


BANNED_PUBLIC_PHRASES = (
    "it is included because",
    "source metadata ties it",
    "metadata ties it",
    "included because",
)

INCOMPLETE_MODAL_ENDINGS = (
    "would allow.",
    "that would allow.",
    "would require.",
    "that would require.",
    "would prohibit.",
    "that would prohibit.",
    "would create.",
    "that would create.",
)

SENTENCE_STITCH_JOINERS = (
    "under",
    "while",
    "between",
    "with",
    "by",
    "from",
    "to",
    "of",
    "in",
    "on",
    "for",
    "about",
    "amid",
)

_SENTENCE_STITCH_RE = re.compile(
    rf"\b({'|'.join(SENTENCE_STITCH_JOINERS)})\.\s+(?=([\"'“‘]*[A-Z]))",
)


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def _repair_sentence_stitching(text: str) -> str:
    value = str(text or "")
    while True:
        updated = _SENTENCE_STITCH_RE.sub(lambda match: f"{match.group(1)} ", value)
        if updated == value:
            break
        value = updated
    return value


def _ensure_terminal_punctuation(text: str) -> str:
    value = str(text or "").rstrip()
    if not value:
        return ""
    if value.endswith(("!", "?")):
        return value
    if value.endswith("."):
        return value
    if value.endswith(('"', "'", "”", "’")) and len(value) >= 2 and value[-2] in ".!?":
        return value
    return f"{value}."


def sanitize_public_prose(text: str) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    value = _repair_sentence_stitching(value)
    kept: list[str] = []
    for sentence in _split_sentences(value):
        lowered = sentence.lower()
        if any(phrase in lowered for phrase in BANNED_PUBLIC_PHRASES):
            continue
        if any(lowered.endswith(ending) for ending in INCOMPLETE_MODAL_ENDINGS):
            continue
        kept.append(sentence)
    cleaned = " ".join(kept).strip()
    return _ensure_terminal_punctuation(cleaned)


def find_public_prose_violations(text: str) -> list[str]:
    lowered = " ".join(str(text or "").split()).lower()
    errors: list[str] = []
    for phrase in BANNED_PUBLIC_PHRASES:
        if phrase in lowered:
            errors.append(f"contains banned phrase: {phrase}")
    for ending in INCOMPLETE_MODAL_ENDINGS:
        if ending in lowered:
            errors.append(f"contains incomplete modal ending: {ending}")
    return errors


def html_contains_public_prose_violations(html_text: str) -> list[str]:
    plain = re.sub(r"<[^>]+>", " ", str(html_text or ""))
    return find_public_prose_violations(plain)


def summarize_violations(values: Iterable[str]) -> str:
    unique = sorted(set(values))
    return "; ".join(unique)
