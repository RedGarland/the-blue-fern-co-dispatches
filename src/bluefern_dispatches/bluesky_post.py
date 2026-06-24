from __future__ import annotations

import json
import os
import re
import hashlib
from io import BytesIO
from html import unescape
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib import error, request
from bluefern_dispatches.generator import BASE_URL
from bluefern_dispatches.gaza_audio import select_gaza_audio_stories
from bluefern_dispatches.public_prose import sanitize_public_prose

BLUESKY_API_BASE = "https://bsky.social/xrpc"
BLUESKY_MAX_POST_LENGTH = 300
BLUESKY_CARD_MAX_DESCRIPTION_LENGTH = 240
BLUESKY_CARD_FALLBACK_DESCRIPTION = "Source-backed daily Gaza briefing from The Blue Fern Co."
BLUESKY_GAZA_POST_FALLBACK = "The latest Gaza briefing is live.\n\nFull briefing:"
FOOD_LINE_DISPATCH_SLUG = "food-line"
FOOD_LINE_BLUESKY_POST_FALLBACK = "The latest Food Line briefing is live.\n\nFull briefing:"
FOOD_LINE_BLUESKY_POST_STATE_FILENAME = "bluesky_post.json"
FOOD_LINE_SOCIAL_IMAGE_PATH = "assets/food-line-dispatch-social.png"
FOOD_LINE_SOCIAL_IMAGE_URL = f"{BASE_URL}/food-line/assets/food-line-dispatch-social.png"
FOOD_LINE_SOCIAL_IMAGE_ALT = "The Food Line Dispatch social card from The Blue Fern Co., with wheat, a U.S. map outline, and the subtitle Source-backed daily food-pressure briefing."
BLUESKY_STALE_SYNTHETIC_PHRASES: tuple[str, ...] = (
    "satellite imagery showing changes on the ground",
    "newly surfaced documentation tied to 1967 expulsions and killings",
)
SOURCE_ID_RE = re.compile(r"\b(?:source[_-]?record[_-]?id[:\s]*)?[a-z0-9]+-src-[a-z0-9-]+\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
GAZA_EDITION_DATE_PATH_RE = re.compile(r"/gaza/editions/(\d{4}-\d{2}-\d{2})/", re.IGNORECASE)
DATE_VALUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
JWT_FIELD_RE = re.compile(r'(?i)"(accessJwt|refreshJwt)"\s*:\s*"[^"]*"')
MALFORMED_ENTITY_PERIOD_RE = re.compile(r"\b(by|of|to|from|against|gave|with|for|allows)\.\s+([A-Z][A-Za-z0-9'/-]*)")
WEAK_TRAILING_FRAGMENT_RE = re.compile(r"\b(?:a|an|the|to|of|by|for|with|against|from)\.?$", re.IGNORECASE)
_PUBLIC_PUNCTUATION_FIX_RE = re.compile(r"([!?])\.")
MAX_HTTP_ERROR_DETAIL_LENGTH = 240
BLUESKY_BLOB_MAX_BYTES = 1_000_000
BLUESKY_COMPRESS_TARGET_BYTES = 950_000
BLUESKY_DISPATCH_SLUG = "gaza"
TOPIC_REWRITE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bairstrike|strike|drone\b", re.IGNORECASE), "Israeli strikes in Gaza"),
    (re.compile(r"\bdetention|detained|without charge|prison|red cross|icrc\b", re.IGNORECASE), "court action over Red Cross access to Palestinian detainees"),
    (re.compile(r"\baid\b|\bhumanitarian\b|\bconvoy\b|\bcheckpoint\b|\bborder\b|\baccess\b", re.IGNORECASE), "aid pressure around Gaza's borders"),
    (re.compile(r"\bsatellite\b|\bimagery\b|\bdestruction\b|\berasure\b|\bcontrol\b", re.IGNORECASE), "satellite imagery showing changes on the ground"),
    (re.compile(r"\bmilitary operation|weapons storage|idf said|idf says\b", re.IGNORECASE), "military-announced operations in Gaza"),
    (re.compile(r"\bcamp\b|\bdisplac\b|\brefugee\b|\bdaily life\b", re.IGNORECASE), "daily life in Gaza's camps"),
    (re.compile(r"\b1967\b|\bexpulsion|killings?\b", re.IGNORECASE), "newly surfaced documentation tied to 1967 expulsions and killings"),
)


def _env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip() == "1"


def _safe_error(message: str, secret: str | None) -> str:
    text = (message or "").strip() or "unknown error"
    if secret:
        text = text.replace(secret, "<redacted>")
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = JWT_FIELD_RE.sub(lambda m: f'"{m.group(1)}":"<redacted>"', text)
    return text


def _safe_http_error(exc: error.HTTPError, secret: str | None) -> tuple[str, str | None, str | None]:
    code = getattr(exc, "code", "unknown")
    body = ""
    try:
        raw = exc.read()
        body = raw.decode("utf-8", errors="replace") if raw else ""
    except Exception:  # noqa: BLE001
        body = ""
    if not body.strip():
        return f"http_error_{code}", None, None
    body = _safe_error(body, secret)
    parsed_type: str | None = None
    parsed_message: str | None = None
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        text = WHITESPACE_RE.sub(" ", body).strip()[:MAX_HTTP_ERROR_DETAIL_LENGTH].rstrip()
        return f"http_error_{code}: {text}", None, text
    if isinstance(payload, dict):
        parsed_type = str(payload.get("error") or payload.get("type") or "").strip() or None
        parsed_message = str(payload.get("message") or payload.get("error_description") or "").strip() or None
    if not parsed_type and not parsed_message:
        text = WHITESPACE_RE.sub(" ", body).strip()[:MAX_HTTP_ERROR_DETAIL_LENGTH].rstrip()
        return f"http_error_{code}: {text}", None, text
    detail = f"{parsed_type or 'error'}: {parsed_message or 'unknown error'}"
    detail = WHITESPACE_RE.sub(" ", detail).strip()[:MAX_HTTP_ERROR_DETAIL_LENGTH].rstrip()
    return f"http_error_{code}: {detail}", parsed_type, parsed_message


def _receipt_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / BLUESKY_DISPATCH_SLUG / "editions" / edition_date / "bluesky_post_receipt.json"


def _dispatch_post_state_path(project_root: Path, dispatch_slug: str, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / dispatch_slug / "editions" / edition_date / FOOD_LINE_BLUESKY_POST_STATE_FILENAME


def _load_post_state_for_same_public_url(project_root: Path, dispatch_slug: str, edition_date: str, public_url: str) -> dict[str, Any] | None:
    path = _dispatch_post_state_path(project_root, dispatch_slug, edition_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "") != "success":
        return None
    if not str(payload.get("post_uri") or "").strip():
        return None
    if str(payload.get("public_url") or "").strip() != str(public_url).strip():
        return None
    return payload


def _load_receipt_for_same_public_url(project_root: Path, edition_date: str, public_url: str) -> dict[str, Any] | None:
    path = _receipt_path(project_root, edition_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "") != "success":
        return None
    if not str(payload.get("post_uri") or "").strip():
        return None
    if str(payload.get("public_url") or "").strip() != str(public_url).strip():
        return None
    return payload


def _gaza_bluesky_artifact_paths(project_root: Path, edition_date: str) -> list[Path]:
    return [
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "curation_manifest.json",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "edition_manifest.json",
        project_root / "output" / "site" / "gaza" / "editions" / edition_date / "index.html",
    ]


def _artifact_mentions_other_edition_date(text: str, edition_date: str) -> bool:
    for match in GAZA_EDITION_DATE_PATH_RE.finditer(text):
        if match.group(1) != edition_date:
            return True
    return False


def _collect_artifact_date_issues(payload: Any, edition_date: str, *, artifact_path: Path | None = None) -> list[str]:
    issues: list[str] = []
    label = str(artifact_path) if artifact_path else "<artifact>"
    if isinstance(payload, dict):
        for key in ("edition_date", "editionDate", "run_date", "runDate", "date", "coverage_date", "publish_date", "generated_date"):
            value = payload.get(key)
            if isinstance(value, str) and DATE_VALUE_RE.match(value) and value != edition_date:
                issues.append(f"{label}: {key}={value} does not match requested {edition_date}")
        for key in ("story_id", "source_record_id", "source_id"):
            value = payload.get(key)
            if isinstance(value, str):
                match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
                if match and match.group(1) != edition_date:
                    issues.append(f"{label}: {key}={value} does not match requested {edition_date}")
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                issues.extend(_collect_artifact_date_issues(value, edition_date, artifact_path=artifact_path))
            elif isinstance(value, str) and key in {"canonical_url", "url", "public_url", "og_url"} and _artifact_mentions_other_edition_date(value, edition_date):
                issues.append(f"{label}: {key} references a different edition date")
    elif isinstance(payload, list):
        for item in payload:
            issues.extend(_collect_artifact_date_issues(item, edition_date, artifact_path=artifact_path))
    elif isinstance(payload, str) and artifact_path and artifact_path.suffix.lower() == ".html":
        match = GAZA_EDITION_DATE_PATH_RE.search(payload)
        if match and match.group(1) != edition_date:
            issues.append(f"{label}: html references {match.group(1)} instead of {edition_date}")
    return issues


def _read_gaza_bluesky_artifact(path: Path, edition_date: str) -> tuple[Any, list[str]]:
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.suffix.lower() == ".json" else path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return None, [f"could not read {path}: {exc}"]
    issues = _collect_artifact_date_issues(payload, edition_date, artifact_path=path)
    return payload, issues


def _gaza_bluesky_context(project_root: Path, edition_date: str) -> dict[str, Any]:
    paths = _gaza_bluesky_artifact_paths(project_root, edition_date)
    payloads: list[tuple[Path, Any]] = []
    issues: list[str] = []
    for path in paths:
        payload, path_issues = _read_gaza_bluesky_artifact(path, edition_date)
        issues.extend(path_issues)
        if payload is not None:
            payloads.append((path, payload))

    story_rows: list[dict[str, Any]] = []
    source_artifact_paths: list[str] = [str(path) for path, payload in payloads if payload is not None]
    for _path, payload in payloads:
        if isinstance(payload, list):
            story_rows.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            story_rows.extend(_collect_story_like_records(payload))

    curated_rows = [row for row in story_rows if row.get("included_in_public_summary") is not False]
    selected_rows = select_gaza_audio_stories(curated_rows) if curated_rows else []
    if not selected_rows:
        selected_rows = curated_rows or story_rows

    if not selected_rows:
        run_manifest_path = project_root / "data" / "dispatches" / "gaza" / "editions" / edition_date / "run_manifest.json"
        run_manifest, run_issues = _read_gaza_bluesky_artifact(run_manifest_path, edition_date)
        issues.extend(run_issues)
        if isinstance(run_manifest, dict):
            run_summary = _prefer_dispatch_level_summary(run_manifest, 180)
            if run_summary:
                selected_rows = [
                    {
                        "title": run_summary,
                        "summary": run_summary,
                        "public_summary": run_summary,
                        "social_summary": run_summary,
                        "included_in_public_summary": True,
                    }
                ]
                source_artifact_paths.append(str(run_manifest_path))

    edition_manifest = next((payload for path, payload in payloads if path.name == "edition_manifest.json" and isinstance(payload, dict)), {})
    site_index_html = next((payload for path, payload in payloads if path.name == "index.html" and isinstance(payload, str)), "")
    source_count = int(edition_manifest.get("source_count") or len(selected_rows) or 0) if isinstance(edition_manifest, dict) else len(selected_rows)
    publisher_count = int(edition_manifest.get("publisher_count") or 0) if isinstance(edition_manifest, dict) else 0
    if isinstance(edition_manifest, dict) and not publisher_count:
        publishers = edition_manifest.get("publishers")
        if isinstance(publishers, list):
            publisher_count = len([item for item in publishers if str(item).strip()])

    allowed_corpus_parts: list[str] = []
    for row in selected_rows:
        allowed_corpus_parts.extend(_topic_text_fragments_from_record(row))
    if isinstance(edition_manifest, dict):
        allowed_corpus_parts.extend(_topic_text_fragments_from_record(edition_manifest))
    if isinstance(site_index_html, str):
        allowed_corpus_parts.append(_clean_description_text(site_index_html, 2_000))

    expected_post_snippets: list[str] = []
    seen_snippets: set[str] = set()
    for row in selected_rows:
        snippet = _story_post_snippet(row)
        key = snippet.casefold().strip()
        if not key or key in seen_snippets:
            continue
        seen_snippets.add(key)
        expected_post_snippets.append(snippet)

    return {
        "source_artifact_paths": source_artifact_paths,
        "date_issues": issues,
        "story_rows": selected_rows,
        "edition_manifest": edition_manifest if isinstance(edition_manifest, dict) else {},
        "site_index_html": site_index_html if isinstance(site_index_html, str) else "",
        "source_count": source_count,
        "publisher_count": publisher_count,
        "allowed_corpus": " \n".join(part for part in allowed_corpus_parts if part).casefold(),
        "expected_post_snippets": expected_post_snippets,
    }


def _write_success_receipt(
    *,
    project_root: Path,
    edition_date: str,
    public_url: str,
    post_uri: str,
    post_text: str,
    card_title: str,
    card_description: str,
    embed_type: str,
    thumb_status: str,
    original_thumb_bytes: int | None,
    uploaded_thumb_bytes: int | None,
) -> None:
    path = _receipt_path(project_root, edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dispatch_slug": BLUESKY_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": public_url,
        "post_uri": post_uri,
        "posted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "post_text_sha256": hashlib.sha256(post_text.encode("utf-8")).hexdigest(),
        "card_title": card_title,
        "card_description": card_description,
        "embed_type": embed_type,
        "thumb_status": thumb_status,
        "original_thumb_bytes": original_thumb_bytes,
        "uploaded_thumb_bytes": uploaded_thumb_bytes,
        "status": "success",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _collect_story_like_records(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                rows.append(item)
    elif isinstance(payload, dict):
        for key in ("stories", "items", "curation", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _record_text_blob(record: dict[str, Any]) -> str:
    return " ".join(
        [
            str(record.get("title") or ""),
            str(record.get("summary") or ""),
            str(record.get("public_summary") or ""),
            str(record.get("summary_or_snippet") or ""),
            str(record.get("category") or ""),
            str(record.get("category_hint") or ""),
            str(record.get("section_label") or ""),
            str(record.get("attribution_mode") or ""),
            str(record.get("claim_status") or ""),
            str(record.get("region_scope") or ""),
        ]
    ).lower()


def _record_topics(record: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    text = _record_text_blob(record)
    attribution_mode = str(record.get("attribution_mode") or "").strip().lower()
    claim_status = str(record.get("claim_status") or "").strip().lower()
    if "1967" in text and ("expulsion" in text or "killing" in text):
        topics.append("newly surfaced documentation tied to 1967 expulsions and killings")
    if "red cross" in text or "icrc" in text or ("detention" in text and "palestin" in text):
        topics.append("court action over Red Cross access to Palestinian detainees")
    if "strike" in text and "gaza" in text:
        topics.append("Israeli strikes in Gaza")
    if attribution_mode == "gaza_adjacent_context" or claim_status == "gaza_adjacent_context" or "outside gaza" in text or "gaza-bound" in text:
        topics.append("aid pressure around Gaza's borders")
    if attribution_mode == "military_claim_reported" or claim_status == "military_claim_reported":
        topics.append("military-announced operations in Gaza")
    for pattern, topic in TOPIC_REWRITE_PATTERNS:
        if topic == "Israeli strikes in Gaza" and "gaza" not in text:
            continue
        if pattern.search(text):
            topics.append(topic)
    return topics


def _topic_text_fragments_from_record(record: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    seen: set[str] = set()
    for key in ("title", "summary", "public_summary", "social_summary", "description", "dek", "summary_or_snippet", "category", "category_hint", "section", "section_label"):
        raw = record.get(key)
        if raw is None:
            continue
        cleaned = _clean_description_text(str(raw), 220)
        if not cleaned:
            continue
        dedupe_key = cleaned.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        fragments.append(cleaned)
    return fragments


def _story_post_snippet(record: dict[str, Any]) -> str:
    text = _clean_description_text(
        str(
            record.get("social_summary")
            or record.get("public_summary")
            or record.get("summary")
            or record.get("summary_or_snippet")
            or record.get("description")
            or record.get("title")
            or ""
        ),
        180,
    )
    lowered = _record_text_blob(record)
    if not text:
        return ""
    if "khan younis" in lowered and "strike" in lowered:
        return "Khan Younis strikes"
    if "civil defence" in lowered and "10 killed" in lowered:
        return "Gaza civil defence reported 10 killed"
    if "cairo" in lowered and "mediator" in lowered and ("ceasefire" in lowered or "talk" in lowered):
        return "Cairo ceasefire talks"
    if "west bank" in lowered:
        return "West Bank developments"
    if "flotilla" in lowered or "prison ship" in lowered:
        return "flotilla detention"
    return text


def _derive_gaza_focus_topics(project_root: Path, edition_date: str, max_topics: int = 5) -> list[str]:
    context = _gaza_bluesky_context(project_root, edition_date)
    topics: list[str] = []
    for row in context.get("story_rows") or []:
        if not isinstance(row, dict):
            continue
        snippet = _story_post_snippet(row)
        if not snippet:
            continue
        if snippet.casefold() in {item.casefold() for item in topics}:
            continue
        topics.append(snippet)
        if len(topics) >= max_topics:
            break
    return topics[:max_topics]


def _format_post_date(edition_date: str) -> str:
    try:
        dt = datetime.strptime(edition_date, "%Y-%m-%d")
    except ValueError:
        return edition_date
    return f"{dt.strftime('%B')} {dt.day}"


def _compose_reader_line(topics: list[str], edition_date: str) -> str:
    if not topics:
        return BLUESKY_GAZA_POST_FALLBACK
    variant = datetime.strptime(edition_date, "%Y-%m-%d").day % 4 if re.match(r"^\d{4}-\d{2}-\d{2}$", edition_date) else 0
    date_text = _format_post_date(edition_date)
    if len(topics) == 1:
        return f"The {date_text} briefing focuses on {topics[0]}."
    if len(topics) == 2:
        if variant == 1:
            return f"In the {date_text} briefing: {topics[0]}. Also covered: {topics[1]}."
        return f"The latest Gaza briefing focuses on {topics[0]} and {topics[1]}."
    if variant == 2:
        return f"New in the {date_text} briefing: {topics[0]}; {topics[1]}; and {topics[2]}."
    if variant == 3:
        return f"In the {date_text} briefing: {topics[0]}. Also covered: {topics[1]} and {topics[2]}."
    return f"The {date_text} briefing leads with {topics[0]}, then turns to {topics[1]} and {topics[2]}."


def _compose_cta(edition_date: str) -> str:
    if re.match(r"^\d{4}-\d{2}-\d{2}$", edition_date):
        day_variant = datetime.strptime(edition_date, "%Y-%m-%d").day % 4
    else:
        day_variant = 0
    if day_variant == 0:
        return "Full briefing and source links:"
    if day_variant == 1:
        return "Read the full briefing:"
    if day_variant == 2:
        return f"Read the {_format_post_date(edition_date)} briefing:"
    return "Full briefing:"


def _shorten_post_text_at_word_boundary(text: str, max_len: int) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    if len(sentence) <= max_len:
        return sentence
    if max_len <= 3:
        return "..."[:max_len]
    trimmed = sentence[: max_len - 3].rstrip(" ,;:-.")
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-.")
    if not trimmed:
        return "..."
    return trimmed + "..."


def _gaza_public_summary_for_bluesky(project_root: Path, edition_date: str, max_length: int = 180) -> str:
    context = _gaza_bluesky_context(project_root, edition_date)
    edition_manifest = context.get("edition_manifest")
    cleaned = _prefer_dispatch_level_summary(edition_manifest, max_length)
    if cleaned:
        return cleaned
    cleaned = _extract_top_story_summary(project_root, edition_date, max_length)
    if cleaned:
        return cleaned
    cleaned = _extract_first_paragraph_from_html(project_root, edition_date, max_length)
    if cleaned:
        return cleaned
    for row in context.get("story_rows") or []:
        if not isinstance(row, dict):
            continue
        cleaned = _clean_description_text(
            str(row.get("social_summary") or row.get("public_summary") or row.get("summary") or row.get("summary_or_snippet") or row.get("title") or ""),
            max_length,
        )
        if cleaned:
            return cleaned
    return ""


def build_gaza_bluesky_post_text(
    edition_date: str,
    public_url: str,
    project_root: Path | None = None,
    *,
    include_public_url: bool = True,
) -> str:
    root = project_root or Path.cwd()
    clean_date = str(edition_date or "").strip()
    context = _gaza_bluesky_context(root, clean_date)
    topics = []
    for row in context.get("story_rows") or []:
        if not isinstance(row, dict):
            continue
        snippet = _story_post_snippet(row)
        if not snippet:
            continue
        if snippet.casefold() in {item.casefold() for item in topics}:
            continue
        topics.append(snippet)
        if len(topics) >= 5:
            break
    public_summary = _gaza_public_summary_for_bluesky(root, clean_date, max_length=180)
    if not topics and not public_summary:
        return BLUESKY_GAZA_POST_FALLBACK
    date_text = _format_post_date(clean_date)
    url_suffix = f"\n\nPublic edition: {public_url}" if include_public_url and str(public_url or "").strip() else ""
    footer = "Source-backed briefing from The Blue Fern Co."

    def _with_suffix(body: str) -> str:
        body = _normalize_public_post_text(body)
        if url_suffix and len(f"{body}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return f"{body}{url_suffix}"
        return body

    if topics:
        if len(topics) == 1:
            intro = f"In the {date_text} Gaza briefing: {topics[0]}."
        elif len(topics) == 2:
            intro = f"In the {date_text} Gaza briefing: {topics[0]}; and {topics[1]}."
        else:
            intro = f"In the {date_text} Gaza briefing: {'; '.join(topics[:-1])}; and {topics[-1]}."
        candidate = _with_suffix(f"{intro}\n\n{footer}")
        if len(candidate) <= BLUESKY_MAX_POST_LENGTH and candidate != _normalize_public_post_text(intro):
            return candidate
        if len(f"{intro}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return _normalize_public_post_text(f"{intro}{url_suffix}" if url_suffix else intro)
    if public_summary:
        prefix = f"In the {date_text} Gaza briefing: "
        available = BLUESKY_MAX_POST_LENGTH - len(prefix) - len(url_suffix) - len(footer) - 5
        if available > 0:
            summary = _shorten_post_text_at_word_boundary(public_summary.rstrip(".") or public_summary, available).rstrip(".")
            candidate = f"{prefix}{summary}.\n\n{footer}"
            candidate = _with_suffix(candidate)
            if len(candidate) <= BLUESKY_MAX_POST_LENGTH:
                return candidate
    if topics:
        compact_topics = topics[:2]
        if len(compact_topics) == 1:
            compact = f"In the {date_text} Gaza briefing: {compact_topics[0]}."
        else:
            compact = f"In the {date_text} Gaza briefing: {compact_topics[0]}; and {compact_topics[1]}."
        if len(f"{compact}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return _normalize_public_post_text(f"{compact}{url_suffix}" if url_suffix else compact)
    return BLUESKY_GAZA_POST_FALLBACK[:BLUESKY_MAX_POST_LENGTH]


def _clean_description_text(value: str, max_length: int) -> str:
    text = unescape(str(value or ""))
    text = TAG_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = SOURCE_ID_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if text:
        while True:
            fixed = MALFORMED_ENTITY_PERIOD_RE.sub(r"\1 \2", text)
            if fixed == text:
                break
            text = fixed
        lowered = text.lower()
        for phrase in ("in a move that threatens to torpedo an", "to torpedo an", "in a move that"):
            idx = lowered.find(phrase)
            if idx > 0:
                text = text[:idx].rstrip(" ,;:-")
                break
    text = sanitize_public_prose(text)
    if not text:
        return ""
    if len(text) <= max_length:
        candidate = text
    else:
        truncated = text[:max_length].rstrip()
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0].rstrip()
        candidate = truncated.rstrip(" ,;:-")
    if WEAK_TRAILING_FRAGMENT_RE.search(candidate):
        candidate = re.sub(WEAK_TRAILING_FRAGMENT_RE, "", candidate).rstrip(" ,;:-")
    candidate = _PUBLIC_PUNCTUATION_FIX_RE.sub(r"\1", candidate)
    return candidate


def _normalize_public_post_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    text = _PUBLIC_PUNCTUATION_FIX_RE.sub(r"\1", text)
    text = text.replace("..", ".")
    text = WHITESPACE_RE.sub(" ", text.replace("\n\n", "<<<BLANK>>>")).replace("<<<BLANK>>>", "\n\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def _first_usable_field(payload: Any, names: tuple[str, ...], max_length: int) -> str:
    if isinstance(payload, dict):
        for name in names:
            cleaned = _clean_description_text(str(payload.get(name) or ""), max_length)
            if cleaned:
                return cleaned
        for value in payload.values():
            cleaned = _first_usable_field(value, names, max_length)
            if cleaned:
                return cleaned
    if isinstance(payload, list):
        for item in payload:
            cleaned = _first_usable_field(item, names, max_length)
            if cleaned:
                return cleaned
    return ""


def _first_usable_direct_field(payload: Any, names: tuple[str, ...], max_length: int) -> str:
    if not isinstance(payload, dict):
        return ""
    for name in names:
        cleaned = _clean_description_text(str(payload.get(name) or ""), max_length)
        if cleaned:
            return cleaned
    return ""


def _prefer_dispatch_level_summary(payload: Any, max_length: int) -> str:
    if not isinstance(payload, dict):
        return ""
    keys = ("social_summary", "public_summary", "summary", "description", "dek")
    for container_key in ("dispatch", "dispatch_summary", "edition", "edition_summary", "public"):
        container = payload.get(container_key)
        direct = _first_usable_direct_field(container, keys, max_length)
        if direct:
            return direct
    direct = _first_usable_direct_field(payload, keys, max_length)
    if direct:
        return direct
    return ""


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _extract_top_story_summary(project_root: Path, edition_date: str, max_length: int) -> str:
    curated_path = project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "curation_manifest.json"
    payload = _read_json(curated_path)
    keys = ("social_summary", "summary", "description", "dek", "public_summary")
    preferred: list[str] = []
    fallback: list[str] = []
    if isinstance(payload, list):
        eligible_items = select_gaza_audio_stories([item for item in payload if isinstance(item, dict)])
        for item in eligible_items or payload:
            if not isinstance(item, dict):
                continue
            cleaned = _first_usable_direct_field(item, keys, max_length)
            if not cleaned:
                continue
            blob = json.dumps(item).lower()
            if "gaza" in blob:
                preferred.append(cleaned)
            else:
                fallback.append(cleaned)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return _first_usable_field(payload, keys, max_length)


def _extract_first_paragraph_from_html(project_root: Path, edition_date: str, max_length: int) -> str:
    index_path = project_root / "output" / "site" / "gaza" / "editions" / edition_date / "index.html"
    if not index_path.exists():
        return ""
    try:
        html = index_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    for tag in ("nav", "header", "footer", "aside"):
        html = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        if any(blocked in lowered for blocked in ("sources", "source notes", "source list", "jump to", "archive", "rss")):
            continue
        cleaned = _clean_description_text(paragraph, max_length)
        if cleaned:
            return cleaned
    return ""


def build_gaza_card_description(edition_date: str, project_root: Path, max_length: int = BLUESKY_CARD_MAX_DESCRIPTION_LENGTH) -> str:
    date_text = str(edition_date or "").strip()
    if not date_text:
        return BLUESKY_CARD_FALLBACK_DESCRIPTION
    context = _gaza_bluesky_context(project_root, date_text)
    for row in context.get("story_rows") or []:
        if not isinstance(row, dict):
            continue
        cleaned = _clean_description_text(
            str(row.get("social_summary") or row.get("public_summary") or row.get("summary") or row.get("summary_or_snippet") or row.get("title") or ""),
            max_length,
        )
        if cleaned:
            return cleaned
    edition_manifest = context.get("edition_manifest")
    cleaned = _prefer_dispatch_level_summary(edition_manifest, max_length)
    if cleaned:
        return cleaned
    run_manifest_path = project_root / "data" / "dispatches" / "gaza" / "editions" / date_text / "run_manifest.json"
    run_manifest, _issues = _read_gaza_bluesky_artifact(run_manifest_path, date_text)
    cleaned = _prefer_dispatch_level_summary(run_manifest, max_length)
    if cleaned:
        return cleaned
    from_html = _extract_first_paragraph_from_html(project_root, date_text, max_length)
    if from_html:
        return from_html
    return BLUESKY_CARD_FALLBACK_DESCRIPTION


def _build_gaza_card_title(edition_date: str) -> str:
    try:
        dt = datetime.strptime(edition_date, "%Y-%m-%d")
    except ValueError:
        return f"Dispatches from Gaza - {edition_date}"
    return f"Dispatches from Gaza - {dt.strftime('%B')} {dt.day}, {dt.year}"


def _build_auth_request(url: str, payload: dict[str, Any], access_jwt: str) -> request.Request:
    return request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_jwt}",
        },
    )


def _guess_image_mime(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return None


def _compress_thumb_to_jpeg(image_bytes: bytes) -> bytes | None:
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            converted = image.convert("RGB")
            converted.thumbnail((1200, 630))
            for quality in (85, 80, 75, 70, 65, 60, 55, 50):
                buffer = BytesIO()
                converted.save(buffer, format="JPEG", quality=quality, optimize=True)
                payload = buffer.getvalue()
                if len(payload) <= BLUESKY_COMPRESS_TARGET_BYTES:
                    return payload
            return None
    except Exception:  # noqa: BLE001
        return None


def _upload_blob(access_jwt: str, payload: bytes, mime: str) -> dict[str, Any] | None:
    req = request.Request(
        f"{BLUESKY_API_BASE}/com.atproto.repo.uploadBlob",
        data=payload,
        method="POST",
        headers={"Content-Type": mime, "Accept": "application/json", "Authorization": f"Bearer {access_jwt}"},
    )
    with request.urlopen(req, timeout=20.0) as resp:
        body = resp.read().decode("utf-8")
    response_payload = json.loads(body) if body else {}
    blob = response_payload.get("blob") if isinstance(response_payload, dict) else None
    return blob if isinstance(blob, dict) else None


def _thumbnail_candidates(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "assets" / "gaza-social-card.jpg",
        project_root / "assets" / "gaza-social-card.jpeg",
        project_root / "assets" / "gaza-social-card.png",
        project_root / "assets" / "dispatches-from-blue-fern-co.png",
        project_root / "assets" / "bluefern.png",
    )


def _upload_card_thumb(access_jwt: str, project_root: Path, edition_date: str) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None]:
    _ = edition_date
    candidates = _thumbnail_candidates(project_root)
    for path in candidates:
        if not path.exists():
            continue
        mime = _guess_image_mime(path)
        if not mime:
            continue
        try:
            data = path.read_bytes()
            original_bytes = len(data)
            if original_bytes <= BLUESKY_COMPRESS_TARGET_BYTES:
                blob = _upload_blob(access_jwt, data, mime)
                if blob:
                    return blob, "uploaded", False, original_bytes, original_bytes
                return None, "upload_failed", False, original_bytes, None
            compressed = _compress_thumb_to_jpeg(data)
            if not compressed:
                return None, "skipped_too_large", False, original_bytes, None
            if len(compressed) >= BLUESKY_BLOB_MAX_BYTES:
                return None, "skipped_too_large", False, original_bytes, None
            blob = _upload_blob(access_jwt, compressed, "image/jpeg")
            if blob:
                return blob, "uploaded_compressed", True, original_bytes, len(compressed)
            return None, "upload_failed", True, original_bytes, len(compressed)
        except Exception:  # noqa: BLE001
            return None, "upload_failed", False, None, None
    return None, "no_thumbnail", False, None, None


def _food_line_thumbnail_candidates(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "assets" / "food-line-dispatch-social.png",
        project_root / "assets" / "food-line-logo.png",
        project_root / "assets" / "bluefern.png",
    )


def _upload_food_line_card_thumb(
    access_jwt: str,
    project_root: Path,
) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None, Path | None]:
    for path in _food_line_thumbnail_candidates(project_root):
        if not path.exists():
            continue
        mime = _guess_image_mime(path)
        if not mime:
            continue
        try:
            data = path.read_bytes()
            original_bytes = len(data)
            if original_bytes <= BLUESKY_COMPRESS_TARGET_BYTES:
                blob = _upload_blob(access_jwt, data, mime)
                if blob:
                    return blob, "uploaded", False, original_bytes, original_bytes, path
                return None, "upload_failed", False, original_bytes, None, path
            compressed = _compress_thumb_to_jpeg(data)
            if not compressed:
                return None, "skipped_too_large", False, original_bytes, None, path
            if len(compressed) >= BLUESKY_BLOB_MAX_BYTES:
                return None, "skipped_too_large", False, original_bytes, None, path
            blob = _upload_blob(access_jwt, compressed, "image/jpeg")
            if blob:
                return blob, "uploaded_compressed", True, original_bytes, len(compressed), path
            return None, "upload_failed", True, original_bytes, len(compressed), path
        except Exception:  # noqa: BLE001
            return None, "upload_failed", False, None, None, path
    return None, "no_thumbnail", False, None, None, None


def _food_line_card_title(edition_date: str) -> str:
    try:
        dt = datetime.strptime(edition_date, "%Y-%m-%d")
    except ValueError:
        return f"The Food Line Dispatch - {edition_date}"
    return f"The Food Line Dispatch - {dt.strftime('%B')} {dt.day}, {dt.year}"


def _normalize_sentence(text: str) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    if not sentence:
        return ""
    return sentence.rstrip(".") + "."


def _food_line_card_description(post_text: str, *, max_length: int = BLUESKY_CARD_MAX_DESCRIPTION_LENGTH) -> str:
    body = " ".join(str(post_text or "").split())
    if not body:
        return FOOD_LINE_BLUESKY_POST_FALLBACK
    body = body.replace("Source-backed public briefing:", "").strip()
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", body) if part.strip()]
    parts = [part for part in parts if part and not part.startswith("https://dispatches.thebluefernco.com/food-line/")]
    if not parts:
        return FOOD_LINE_BLUESKY_POST_FALLBACK
    description = " ".join(parts[:2]).strip()
    if len(description) <= max_length:
        return description
    shortened = description[: max_length - 3].rstrip(" ,;:-.")
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0].rstrip(" ,;:-.")
    return (shortened or description[:max_length]).rstrip() + "..." if shortened else FOOD_LINE_BLUESKY_POST_FALLBACK


def _write_food_line_post_state(
    project_root: Path,
    edition_date: str,
    payload: dict[str, Any],
) -> Path:
    path = _dispatch_post_state_path(project_root, FOOD_LINE_DISPATCH_SLUG, edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _post_json(url: str, payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body) if body else {}
    if not isinstance(parsed, dict):
        raise ValueError("Bluesky API response was not a JSON object")
    return parsed


def _bluesky_stale_content_guard(post_text: str, context: dict[str, Any]) -> tuple[bool, str]:
    raw_text = str(post_text or "")
    lowered = " ".join(raw_text.split()).casefold()
    allowed_corpus = str(context.get("allowed_corpus") or "")
    expected_snippets = [str(item or "").strip() for item in (context.get("expected_post_snippets") or []) if str(item or "").strip()]
    if not lowered.strip():
        return False, "current-edition-summary-unavailable"
    if not list(context.get("story_rows") or []):
        return False, "current-edition-summary-unavailable"
    if "\n\n" in raw_text:
        raw_body = raw_text.split("\n\n", 1)[0]
    else:
        raw_body = raw_text
    body = " ".join(raw_body.split()).casefold()
    if ": " in body:
        body = body.split(": ", 1)[1]
    for phrase in BLUESKY_STALE_SYNTHETIC_PHRASES:
        if phrase in body and phrase not in allowed_corpus:
            return False, "stale-content-guard-failed"
    clause_text = body.rsplit(".", 1)[0] if "." in body else body
    clauses = [part.strip(" .") for part in clause_text.split(";") if part.strip(" .")]
    expected_lookup = {snippet.casefold(): snippet for snippet in expected_snippets}
    for clause in clauses:
        clause = clause.removeprefix("and ").strip(" .")
        if not clause:
            continue
        if clause in {"full briefing", "limited-source update"}:
            continue
        if clause.casefold() in expected_lookup:
            continue
        if clause.casefold() in allowed_corpus:
            continue
        return False, "stale-content-guard-failed"
    return True, "passed"


def maybe_post_gaza_dispatch_to_bluesky(
    *,
    edition_date: str,
    public_url: str | None,
    run_succeeded: bool,
    post_requested: bool,
    project_root: Path | None = None,
    force_post: bool = False,
    allow_publish: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "skipped",
        "post_uri": None,
        "reason": None,
        "embed_type": None,
        "card_title": None,
        "card_description": None,
        "post_text": None,
        "thumb_status": "not_attempted",
        "compressed_thumb": False,
        "original_thumb_bytes": None,
        "uploaded_thumb_bytes": None,
        "error_type": None,
        "error_message": None,
        "source_artifact_paths": [],
        "edition_date_verified": False,
        "stale_content_guard_status": "not_evaluated",
    }
    if not run_succeeded:
        result["reason"] = "run_failed"
        return result
    if not post_requested:
        result["reason"] = "disabled_by_config"
        return result
    if not public_url or not str(public_url).strip():
        result["reason"] = "missing_public_url"
        return result
    if not (_env_enabled("BLUESKY_ENABLED") and _env_enabled("BLUESKY_POST_AFTER_GAZA")):
        result["reason"] = "disabled_by_env"
        return result

    handle = str(os.getenv("BLUESKY_HANDLE", "")).strip()
    app_password = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle:
        result["reason"] = "missing_handle"
        return result
    if not app_password:
        result["reason"] = "missing_app_password"
        return result

    root = project_root or Path.cwd()
    context = _gaza_bluesky_context(root, edition_date)
    result["source_artifact_paths"] = list(context.get("source_artifact_paths") or [])
    result["edition_date_verified"] = not bool(context.get("date_issues"))
    if context.get("date_issues"):
        result["status"] = "blocked"
        result["reason"] = "current-edition-date-mismatch"
        result["stale_content_guard_status"] = "blocked"
        return result
    card_title = _build_gaza_card_title(edition_date)
    card_description = build_gaza_card_description(edition_date, root, max_length=BLUESKY_CARD_MAX_DESCRIPTION_LENGTH)
    use_external_embed = card_description != BLUESKY_CARD_FALLBACK_DESCRIPTION
    text = build_gaza_bluesky_post_text(
        edition_date,
        public_url,
        project_root=root,
        include_public_url=not use_external_embed,
    )
    result["post_text"] = text
    result["card_title"] = card_title
    result["card_description"] = card_description
    if text == BLUESKY_GAZA_POST_FALLBACK:
        result["status"] = "blocked"
        result["reason"] = "current-edition-public-summary-unavailable"
        result["stale_content_guard_status"] = "blocked"
        return result
    ok, guard_status = _bluesky_stale_content_guard(text, context)
    result["stale_content_guard_status"] = guard_status
    if not ok:
        result["status"] = "blocked"
        result["reason"] = guard_status
        return result
    if not allow_publish:
        result["reason"] = "dry_run"
        return result
    if not force_post:
        receipt = _load_receipt_for_same_public_url(root, edition_date, str(public_url))
        if receipt:
            return {
                "status": "skipped",
                "post_uri": str(receipt.get("post_uri") or "").strip() or None,
                "reason": "skipped_existing_receipt",
                "embed_type": receipt.get("embed_type"),
                "card_title": receipt.get("card_title"),
                "card_description": receipt.get("card_description"),
                "post_text": text,
                "thumb_status": receipt.get("thumb_status") or "not_attempted",
                "compressed_thumb": False,
                "original_thumb_bytes": receipt.get("original_thumb_bytes"),
                "uploaded_thumb_bytes": receipt.get("uploaded_thumb_bytes"),
                "error_type": None,
                "error_message": None,
                "source_artifact_paths": result["source_artifact_paths"],
                "edition_date_verified": result["edition_date_verified"],
                "stale_content_guard_status": result["stale_content_guard_status"],
            }
    try:
        session = _post_json(
            f"{BLUESKY_API_BASE}/com.atproto.server.createSession",
            {"identifier": handle, "password": app_password},
        )
        access_jwt = str(session.get("accessJwt") or "")
        did = str(session.get("did") or "")
        if not access_jwt or not did:
            return {
                "status": "failure",
                "post_uri": None,
                "reason": "invalid_session_response",
                "error_type": None,
                "error_message": None,
                "post_text": text,
                "card_title": card_title,
                "card_description": card_description,
                "source_artifact_paths": result["source_artifact_paths"],
                "edition_date_verified": result["edition_date_verified"],
                "stale_content_guard_status": result["stale_content_guard_status"],
            }
        thumb_blob = None
        thumb_status = "not_attempted"
        compressed_thumb = False
        original_thumb_bytes = None
        uploaded_thumb_bytes = None
        image_path = None
        record_payload = {
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        }
        if use_external_embed:
            thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes = _upload_card_thumb(access_jwt, root, edition_date)
            external: dict[str, Any] = {
                "$type": "app.bsky.embed.external",
                "external": {"uri": str(public_url), "title": card_title, "description": card_description},
            }
            if thumb_blob:
                external["external"]["thumb"] = thumb_blob
                image_path = FOOD_LINE_SOCIAL_IMAGE_PATH
            record_payload["record"]["embed"] = external
        req = _build_auth_request(f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord", record_payload, access_jwt)
        with request.urlopen(req, timeout=20.0) as resp:
            body = resp.read().decode("utf-8")
        payload = json.loads(body) if body else {}
        post_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
        if not post_uri:
            return {
                "status": "failure",
                "post_uri": None,
                "reason": "missing_post_uri",
                "error_type": None,
                "error_message": None,
                "post_text": text,
                "card_title": card_title,
                "card_description": card_description,
                "source_artifact_paths": result["source_artifact_paths"],
                "edition_date_verified": result["edition_date_verified"],
                "stale_content_guard_status": result["stale_content_guard_status"],
            }
        _write_success_receipt(
            project_root=root,
            edition_date=edition_date,
            public_url=str(public_url),
            post_uri=post_uri,
            post_text=text,
            card_title=card_title,
            card_description=card_description,
            embed_type="app.bsky.embed.external" if use_external_embed else None,
            thumb_status=thumb_status,
            original_thumb_bytes=original_thumb_bytes,
            uploaded_thumb_bytes=uploaded_thumb_bytes,
        )
        return {
            "status": "success",
            "post_uri": post_uri,
            "reason": None,
            "embed_type": "app.bsky.embed.external" if use_external_embed else None,
            "card_title": card_title,
            "card_description": card_description,
            "post_text": text,
            "thumb_status": thumb_status,
            "compressed_thumb": compressed_thumb,
            "original_thumb_bytes": original_thumb_bytes,
            "uploaded_thumb_bytes": uploaded_thumb_bytes,
            "image_path": image_path,
            "error_type": None,
            "error_message": None,
            "source_artifact_paths": result["source_artifact_paths"],
            "edition_date_verified": result["edition_date_verified"],
            "stale_content_guard_status": result["stale_content_guard_status"],
        }
    except error.HTTPError as exc:
        reason, err_type, err_message = _safe_http_error(exc, app_password)
        return {
            "status": "failure",
            "post_uri": None,
            "reason": reason,
            "error_type": err_type,
            "error_message": err_message,
            "post_text": text,
            "card_title": card_title,
            "card_description": card_description,
            "source_artifact_paths": result["source_artifact_paths"],
            "edition_date_verified": result["edition_date_verified"],
            "stale_content_guard_status": result["stale_content_guard_status"],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failure",
            "post_uri": None,
            "reason": _safe_error(str(exc), app_password),
            "error_type": None,
            "error_message": None,
            "post_text": text,
            "card_title": card_title,
            "card_description": card_description,
            "source_artifact_paths": result["source_artifact_paths"],
            "edition_date_verified": result["edition_date_verified"],
            "stale_content_guard_status": result["stale_content_guard_status"],
        }


def maybe_post_food_line_dispatch_to_bluesky(
    *,
    edition_date: str,
    public_url: str | None,
    post_text: str | None,
    run_succeeded: bool,
    public_rendered: bool,
    public_signal_count: int,
    post_requested: bool,
    project_root: Path | None = None,
    force_post: bool = False,
    allow_publish: bool = True,
    dry_run: bool = False,
    allow_text_only: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "skipped",
        "post_uri": None,
        "post_cid": None,
        "reason": None,
        "embed_type": None,
        "card_title": None,
        "card_description": None,
        "post_text": None,
        "image_path": None,
        "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
        "thumb_status": "not_attempted",
        "compressed_thumb": False,
        "original_thumb_bytes": None,
        "uploaded_thumb_bytes": None,
        "error_type": None,
        "error_message": None,
        "state_path": None,
        "edition_date_verified": False,
        "public_rendered": bool(public_rendered),
        "public_signal_count": int(public_signal_count or 0),
        "dry_run": bool(dry_run),
        "forced_post": bool(force_post),
    }
    root = project_root or Path.cwd()
    state_path = _dispatch_post_state_path(root, FOOD_LINE_DISPATCH_SLUG, edition_date)
    result["state_path"] = str(state_path)
    if not run_succeeded:
        result["reason"] = "run_failed"
    elif not post_requested:
        result["reason"] = "disabled_by_config"
    elif not public_rendered:
        result["reason"] = "not_public_rendered"
    elif int(public_signal_count or 0) <= 0:
        result["reason"] = "no_public_signals"
    elif not public_url or not str(public_url).strip():
        result["reason"] = "missing_public_url"
    else:
        cleaned_post_text = " ".join(str(post_text or "").split())
        if not cleaned_post_text:
            result["reason"] = "post_text_unavailable"
        else:
            result["post_text"] = cleaned_post_text
            result["card_title"] = _food_line_card_title(edition_date)
            result["card_description"] = _food_line_card_description(cleaned_post_text)
            result["edition_date_verified"] = True
            receipt = _load_post_state_for_same_public_url(root, FOOD_LINE_DISPATCH_SLUG, edition_date, str(public_url))
            if receipt and not force_post:
                state_payload = {
                    "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                    "edition_date": edition_date,
                    "public_url": str(public_url),
                    "post_text": cleaned_post_text,
                    "card_title": result["card_title"],
                    "card_description": result["card_description"],
                    "image_path": receipt.get("image_path"),
                    "image_alt": receipt.get("image_alt") or FOOD_LINE_SOCIAL_IMAGE_ALT,
                    "status": "skipped",
                    "skip_reason": "skipped_existing_receipt",
                    "dry_run": False,
                    "forced_post": False,
                    "post_uri": receipt.get("post_uri"),
                    "post_cid": receipt.get("post_cid"),
                    "embed_type": receipt.get("embed_type"),
                    "thumb_status": receipt.get("thumb_status"),
                    "posted_at": receipt.get("posted_at"),
                }
                _write_food_line_post_state(root, edition_date, state_payload)
                return {
                    **result,
                    "status": "skipped",
                    "reason": "skipped_existing_receipt",
                    "post_uri": receipt.get("post_uri"),
                    "post_cid": receipt.get("post_cid"),
                    "embed_type": receipt.get("embed_type"),
                    "card_title": receipt.get("card_title") or result["card_title"],
                    "card_description": receipt.get("card_description") or result["card_description"],
                    "image_path": receipt.get("image_path"),
                    "image_alt": receipt.get("image_alt") or FOOD_LINE_SOCIAL_IMAGE_ALT,
                    "thumb_status": receipt.get("thumb_status") or "not_attempted",
                    "compressed_thumb": False,
                    "original_thumb_bytes": receipt.get("original_thumb_bytes"),
                    "uploaded_thumb_bytes": receipt.get("uploaded_thumb_bytes"),
                    "state_path": str(state_path),
                }
            if dry_run or not allow_publish:
                state_payload = {
                    "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                    "edition_date": edition_date,
                    "public_url": str(public_url),
                    "post_text": cleaned_post_text,
                    "card_title": result["card_title"],
                    "card_description": result["card_description"],
                    "image_path": FOOD_LINE_SOCIAL_IMAGE_PATH,
                    "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
                    "status": "dry_run",
                    "skip_reason": "dry_run",
                    "dry_run": True,
                    "forced_post": bool(force_post),
                    "post_uri": None,
                    "post_cid": None,
                    "embed_type": None,
                    "thumb_status": "not_attempted",
                    "posted_at": None,
                }
                _write_food_line_post_state(root, edition_date, state_payload)
                return {
                    **result,
                    "status": "skipped",
                    "reason": "dry_run",
                    "image_path": FOOD_LINE_SOCIAL_IMAGE_PATH,
                    "thumb_status": "not_attempted",
                    "state_path": str(state_path),
                }
    if result["reason"] is not None:
        post_text_state = result["post_text"]
        if not post_text_state and "cleaned_post_text" in locals():
            post_text_state = cleaned_post_text
        state_payload = {
            "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
            "edition_date": edition_date,
            "public_url": str(public_url or ""),
            "post_text": post_text_state,
            "card_title": result["card_title"],
            "card_description": result["card_description"],
            "image_path": None,
            "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
            "status": "skipped",
            "skip_reason": result["reason"],
            "dry_run": bool(dry_run),
            "forced_post": bool(force_post),
            "post_uri": None,
            "post_cid": None,
            "embed_type": None,
            "thumb_status": "not_attempted",
            "posted_at": None,
        }
        _write_food_line_post_state(root, edition_date, state_payload)
        return result

    handle = str(os.getenv("BLUESKY_HANDLE", "")).strip()
    app_password = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle:
        result["reason"] = "missing_handle"
    elif not app_password:
        result["reason"] = "missing_app_password"
    else:
        try:
            session = _post_json(
                f"{BLUESKY_API_BASE}/com.atproto.server.createSession",
                {"identifier": handle, "password": app_password},
            )
            access_jwt = str(session.get("accessJwt") or "")
            did = str(session.get("did") or "")
            if not access_jwt or not did:
                result["status"] = "failure"
                result["reason"] = "invalid_session_response"
            else:
                thumb_blob = None
                thumb_status = "no_thumbnail"
                compressed_thumb = False
                original_thumb_bytes = None
                uploaded_thumb_bytes = None
                image_path = None
                try:
                    thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes, image_path = _upload_food_line_card_thumb(access_jwt, root)
                except Exception as exc:  # noqa: BLE001
                    thumb_blob = None
                    thumb_status = "upload_failed"
                    result["error_type"] = exc.__class__.__name__
                    result["error_message"] = str(exc)
                if not thumb_blob and not allow_text_only:
                    result["status"] = "blocked"
                    result["reason"] = "card_image_unavailable"
                    state_payload = {
                        "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else None,
                        "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
                        "status": "blocked",
                        "skip_reason": "card_image_unavailable",
                        "dry_run": False,
                        "forced_post": bool(force_post),
                        "post_uri": None,
                        "post_cid": None,
                        "embed_type": None,
                        "thumb_status": thumb_status,
                        "posted_at": None,
                    }
                    _write_food_line_post_state(root, edition_date, state_payload)
                    return result
                external: dict[str, Any] = {
                    "$type": "app.bsky.embed.external",
                    "external": {"uri": str(public_url), "title": result["card_title"], "description": result["card_description"]},
                }
                if thumb_blob:
                    external["external"]["thumb"] = thumb_blob
                record_payload = {
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": result["post_text"],
                        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "embed": external,
                    },
                }
                req = _build_auth_request(f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord", record_payload, access_jwt)
                with request.urlopen(req, timeout=20.0) as resp:
                    body = resp.read().decode("utf-8")
                payload = json.loads(body) if body else {}
                post_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
                post_cid = str(payload.get("cid") or "").strip() if isinstance(payload, dict) else ""
                if not post_uri:
                    result["status"] = "failure"
                    result["reason"] = "missing_post_uri"
                else:
                    result.update(
                        {
                            "status": "success",
                            "reason": None,
                            "post_uri": post_uri,
                            "post_cid": post_cid or None,
                            "embed_type": "app.bsky.embed.external",
                            "thumb_status": thumb_status,
                            "compressed_thumb": compressed_thumb,
                            "original_thumb_bytes": original_thumb_bytes,
                            "uploaded_thumb_bytes": uploaded_thumb_bytes,
                            "image_path": str(image_path) if image_path else (FOOD_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        }
                    )
                    state_payload = {
                        "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else (FOOD_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
                        "status": "success",
                        "skip_reason": None,
                        "dry_run": False,
                        "forced_post": bool(force_post),
                        "post_uri": post_uri,
                        "post_cid": post_cid or None,
                        "embed_type": "app.bsky.embed.external",
                        "thumb_status": thumb_status,
                        "compressed_thumb": compressed_thumb,
                        "original_thumb_bytes": original_thumb_bytes,
                        "uploaded_thumb_bytes": uploaded_thumb_bytes,
                        "posted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
                    _write_food_line_post_state(root, edition_date, state_payload)
                    return result
        except error.HTTPError as exc:
            result["status"] = "failure"
            result["reason"], result["error_type"], result["error_message"] = _safe_http_error(exc, app_password)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failure"
            result["reason"] = _safe_error(str(exc), app_password)

    state_payload = {
        "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": str(public_url or ""),
        "post_text": result["post_text"],
        "card_title": result["card_title"],
        "card_description": result["card_description"],
        "image_path": result["image_path"],
        "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
        "status": result["status"],
        "skip_reason": result["reason"],
        "dry_run": bool(dry_run),
        "forced_post": bool(force_post),
        "post_uri": result["post_uri"],
        "post_cid": result["post_cid"],
        "embed_type": result["embed_type"],
        "thumb_status": result["thumb_status"],
        "compressed_thumb": result["compressed_thumb"],
        "original_thumb_bytes": result["original_thumb_bytes"],
        "uploaded_thumb_bytes": result["uploaded_thumb_bytes"],
        "posted_at": None,
    }
    _write_food_line_post_state(root, edition_date, state_payload)
    return result
