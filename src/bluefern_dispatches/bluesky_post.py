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
from bluefern_dispatches.gaza_audio import select_gaza_audio_stories
from bluefern_dispatches.public_prose import sanitize_public_prose

BLUESKY_API_BASE = "https://bsky.social/xrpc"
BLUESKY_MAX_POST_LENGTH = 300
BLUESKY_CARD_MAX_DESCRIPTION_LENGTH = 240
BLUESKY_CARD_FALLBACK_DESCRIPTION = "Source-backed daily Gaza briefing from The Blue Fern Co."
BLUESKY_GAZA_POST_FALLBACK = "The latest Gaza briefing is live.\n\nFull briefing:"
SOURCE_ID_RE = re.compile(r"\b(?:source[_-]?record[_-]?id[:\s]*)?[a-z0-9]+-src-[a-z0-9-]+\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
JWT_FIELD_RE = re.compile(r'(?i)"(accessJwt|refreshJwt)"\s*:\s*"[^"]*"')
MALFORMED_ENTITY_PERIOD_RE = re.compile(r"\b(by|of|to|from|against|gave|with|for|allows)\.\s+([A-Z][A-Za-z0-9'/-]*)")
WEAK_TRAILING_FRAGMENT_RE = re.compile(r"\b(?:a|an|the|to|of|by|for|with|against|from)\.?$", re.IGNORECASE)
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


def _derive_gaza_focus_topics(project_root: Path, edition_date: str, max_topics: int = 5) -> list[str]:
    candidates: list[str] = []
    paths = (
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "curation_manifest.json",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "edition_manifest.json",
        project_root / "data" / "dispatches" / "gaza" / "editions" / edition_date / "run_manifest.json",
    )
    for path in paths:
        payload = _read_json(path)
        if payload is None:
            continue
        rows = _collect_story_like_records(payload)
        if rows:
            for row in rows:
                candidates.extend(_topic_text_fragments_from_record(row))
        elif isinstance(payload, dict):
            candidates.extend(_topic_text_fragments_from_record(payload))
    stories: list[dict[str, Any]] = []
    curated = _read_json(project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "curation_manifest.json")
    stories.extend(select_gaza_audio_stories(_collect_story_like_records(curated)))
    if not stories:
        for path in paths:
            payload = _read_json(path)
            if payload is None:
                continue
            rows = _collect_story_like_records(payload)
            if rows:
                stories.extend(rows)
            elif isinstance(payload, dict):
                stories.append(payload)
    topics: list[str] = []
    seen: set[str] = set()
    for story in stories:
        for topic in _record_topics(story):
            key = topic.lower()
            if key in seen:
                continue
            seen.add(key)
            topics.append(topic)
            if len(topics) >= max_topics:
                return topics
    for text in candidates:
        for pattern, topic in TOPIC_REWRITE_PATTERNS:
            if pattern.search(text):
                key = topic.lower()
                if key in seen:
                    continue
                seen.add(key)
                topics.append(topic)
                if len(topics) >= max_topics:
                    return topics
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


def build_gaza_bluesky_post_text(edition_date: str, public_url: str, project_root: Path | None = None) -> str:
    _ = public_url
    root = project_root or Path.cwd()
    clean_date = str(edition_date or "").strip()
    topics = _derive_gaza_focus_topics(root, clean_date, max_topics=3)
    if not topics:
        return BLUESKY_GAZA_POST_FALLBACK
    intro = _compose_reader_line(topics, clean_date)
    body = f"{intro}\n\n{_compose_cta(clean_date)}"
    body = URL_RE.sub(" ", body)
    body = WHITESPACE_RE.sub(" ", body.replace("\n\n", "<<<BLANK>>>")).replace("<<<BLANK>>>", "\n\n").strip()
    if len(body) <= BLUESKY_MAX_POST_LENGTH:
        return body
    shortened = _compose_reader_line(topics[:2], clean_date)
    compact = f"{shortened}\n\n{_compose_cta(clean_date)}"
    if len(compact) <= BLUESKY_MAX_POST_LENGTH:
        return compact
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
    return candidate


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
    run_manifest_path = project_root / "data" / "dispatches" / "gaza" / "editions" / date_text / "run_manifest.json"
    edition_manifest_path = project_root / "output" / "dispatches" / "gaza" / "editions" / date_text / "edition_manifest.json"
    for payload in (_read_json(run_manifest_path), _read_json(edition_manifest_path)):
        cleaned = _prefer_dispatch_level_summary(payload, max_length)
        if cleaned:
            return cleaned
    curated = _extract_top_story_summary(project_root, date_text, max_length)
    if curated:
        return curated
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


def maybe_post_gaza_dispatch_to_bluesky(
    *,
    edition_date: str,
    public_url: str | None,
    run_succeeded: bool,
    post_requested: bool,
    project_root: Path | None = None,
    force_post: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "skipped",
        "post_uri": None,
        "reason": None,
        "embed_type": None,
        "card_title": None,
        "card_description": None,
        "thumb_status": "not_attempted",
        "compressed_thumb": False,
        "original_thumb_bytes": None,
        "uploaded_thumb_bytes": None,
        "error_type": None,
        "error_message": None,
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
    text = build_gaza_bluesky_post_text(edition_date, public_url, project_root=root)
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
                "thumb_status": receipt.get("thumb_status") or "not_attempted",
                "compressed_thumb": False,
                "original_thumb_bytes": receipt.get("original_thumb_bytes"),
                "uploaded_thumb_bytes": receipt.get("uploaded_thumb_bytes"),
                "error_type": None,
                "error_message": None,
            }
    try:
        session = _post_json(
            f"{BLUESKY_API_BASE}/com.atproto.server.createSession",
            {"identifier": handle, "password": app_password},
        )
        access_jwt = str(session.get("accessJwt") or "")
        did = str(session.get("did") or "")
        if not access_jwt or not did:
            return {"status": "failure", "post_uri": None, "reason": "invalid_session_response", "error_type": None, "error_message": None}
        card_title = _build_gaza_card_title(edition_date)
        card_description = build_gaza_card_description(edition_date, root, max_length=BLUESKY_CARD_MAX_DESCRIPTION_LENGTH)
        thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes = _upload_card_thumb(access_jwt, root, edition_date)
        external: dict[str, Any] = {
            "$type": "app.bsky.embed.external",
            "external": {"uri": str(public_url), "title": card_title, "description": card_description},
        }
        if thumb_blob:
            external["external"]["thumb"] = thumb_blob
        record_payload = {
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "embed": external,
            },
        }
        req = _build_auth_request(f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord", record_payload, access_jwt)
        with request.urlopen(req, timeout=20.0) as resp:
            body = resp.read().decode("utf-8")
        payload = json.loads(body) if body else {}
        post_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
        if not post_uri:
            return {"status": "failure", "post_uri": None, "reason": "missing_post_uri", "error_type": None, "error_message": None}
        _write_success_receipt(
            project_root=root,
            edition_date=edition_date,
            public_url=str(public_url),
            post_uri=post_uri,
            post_text=text,
            card_title=card_title,
            card_description=card_description,
            embed_type="app.bsky.embed.external",
            thumb_status=thumb_status,
            original_thumb_bytes=original_thumb_bytes,
            uploaded_thumb_bytes=uploaded_thumb_bytes,
        )
        return {
            "status": "success",
            "post_uri": post_uri,
            "reason": None,
            "embed_type": "app.bsky.embed.external",
            "card_title": card_title,
            "card_description": card_description,
            "thumb_status": thumb_status,
            "compressed_thumb": compressed_thumb,
            "original_thumb_bytes": original_thumb_bytes,
            "uploaded_thumb_bytes": uploaded_thumb_bytes,
            "error_type": None,
            "error_message": None,
        }
    except error.HTTPError as exc:
        reason, err_type, err_message = _safe_http_error(exc, app_password)
        return {"status": "failure", "post_uri": None, "reason": reason, "error_type": err_type, "error_message": err_message}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failure", "post_uri": None, "reason": _safe_error(str(exc), app_password), "error_type": None, "error_message": None}
