from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any, Iterable


FORBIDDEN_PUBLIC_FRAGMENTS = (
    "good morning",
    "mamdani",
    "nyc democratic primaries",
    "sign up",
    "newsletter",
)
PUBLIC_PROSE_PUNCTUATION_ERRORS = ("?.", "!.")
NEWSLETTER_SENTENCE_RE = re.compile(
    r"(?:^|(?<=[.!?])\s+)([^.!?]*(?:good morning|mamdani|nyc democratic primaries|sign up|newsletter)[^.!?]*[.!?]?)",
    re.IGNORECASE,
)
FIRST_THING_WRAPPER_RE = re.compile(r"\s*\|\s*First Thing\s*$", re.IGNORECASE)
FIRST_THING_PREFIX_RE = re.compile(r"^\s*First Thing\s*[:|\-]\s*", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def sanitize_gaza_public_text(value: Any) -> tuple[str, list[str]]:
    text = WHITESPACE_RE.sub(" ", html.unescape(str(value or ""))).strip()
    if not text:
        return "", []
    stripped: list[str] = []
    without_wrapper = FIRST_THING_WRAPPER_RE.sub("", text).strip()
    if without_wrapper != text:
        stripped.append(text[len(without_wrapper) :].strip())
        text = without_wrapper
    without_prefix = FIRST_THING_PREFIX_RE.sub("", text).strip()
    if without_prefix != text:
        stripped.append("First Thing")
        text = without_prefix

    def drop_newsletter_sentence(match: re.Match[str]) -> str:
        fragment = match.group(1).strip()
        if fragment:
            stripped.append(fragment)
        return " "

    text = NEWSLETTER_SENTENCE_RE.sub(drop_newsletter_sentence, text)
    text = text.replace("?.", "?").replace("!.", "!")
    text = WHITESPACE_RE.sub(" ", text).strip(" \t\r\n|;,-")
    return text, _unique(stripped)


def sanitize_gaza_public_records(
    stories: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    stripped: list[str] = []
    clean_stories = deepcopy(stories)
    clean_sources = deepcopy(sources)
    for story in clean_stories:
        for field in ("title", "summary", "public_summary", "social_summary"):
            if field not in story:
                continue
            story[field], removed = sanitize_gaza_public_text(story.get(field))
            stripped.extend(removed)
        for record in story.get("source_records") or []:
            if not isinstance(record, dict):
                continue
            for field in ("title", "summary_or_snippet"):
                if field not in record:
                    continue
                record[field], removed = sanitize_gaza_public_text(record.get(field))
                stripped.extend(removed)
    for source in clean_sources:
        for field in ("title", "summary_or_snippet"):
            if field not in source:
                continue
            source[field], removed = sanitize_gaza_public_text(source.get(field))
            stripped.extend(removed)
    return clean_stories, clean_sources, _unique(stripped)


def _public_values(stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in [*stories, *sources]:
        if not isinstance(row, dict):
            continue
        for field in ("title", "summary", "public_summary", "social_summary", "summary_or_snippet"):
            value = str(row.get(field) or "").strip()
            if value:
                values.append(value)
    return values


def find_forbidden_public_fragments(values: Iterable[str]) -> list[str]:
    corpus = "\n".join(str(value or "") for value in values).casefold()
    found = [fragment for fragment in FORBIDDEN_PUBLIC_FRAGMENTS if fragment in corpus]
    if re.search(r"(?:^|[|:\-]\s*)first thing(?:\s*$|\s*[|:\-])", corpus, re.MULTILINE):
        found.append("First Thing")
    return _unique(found)


def find_public_punctuation_errors(values: Iterable[str]) -> list[str]:
    corpus = "\n".join(str(value or "") for value in values)
    return [token for token in PUBLIC_PROSE_PUNCTUATION_ERRORS if token in corpus]


def build_gaza_public_quality_report(
    *,
    edition_date: str,
    stories_before_dedupe: list[dict[str, Any]],
    stories_after_dedupe: list[dict[str, Any]],
    public_sources: list[dict[str, Any]],
    duplicate_story_groups: list[dict[str, Any]],
    stripped_newsletter_fragments: list[str],
    rendered_html: str = "",
) -> dict[str, Any]:
    values = _public_values(stories_after_dedupe, public_sources)
    if rendered_html:
        values.append(html.unescape(TAG_RE.sub(" ", rendered_html)))
    forbidden = find_forbidden_public_fragments(values)
    punctuation = find_public_punctuation_errors(values)
    errors = [f"forbidden public fragment remains: {item}" for item in forbidden]
    errors.extend(f"public prose contains malformed punctuation: {item}" for item in punctuation)
    warnings = []
    if stripped_newsletter_fragments:
        warnings.append(f"stripped {len(stripped_newsletter_fragments)} newsletter or wrapper fragment(s)")
    if duplicate_story_groups:
        warnings.append(f"collapsed {len(duplicate_story_groups)} duplicate public story group(s)")
    return {
        "edition_date": edition_date,
        "public_content_quality_ok": not errors,
        "public_content_quality_errors": errors,
        "public_content_quality_warnings": warnings,
        "duplicate_story_groups": duplicate_story_groups,
        "stripped_newsletter_fragments": _unique(stripped_newsletter_fragments),
        "public_story_count_before_dedupe": len(stories_before_dedupe),
        "public_story_count_after_dedupe": len(stories_after_dedupe),
        "forbidden_public_fragments_found": forbidden,
        "public_prose_punctuation_errors": punctuation,
    }


def public_quality_manifest_fields(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "public_content_quality_ok",
        "public_content_quality_errors",
        "public_content_quality_warnings",
        "duplicate_story_groups",
        "stripped_newsletter_fragments",
        "public_story_count_before_dedupe",
        "public_story_count_after_dedupe",
        "forbidden_public_fragments_found",
        "public_prose_punctuation_errors",
    )
    return {key: report.get(key) for key in keys}
