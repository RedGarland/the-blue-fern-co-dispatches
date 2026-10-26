from __future__ import annotations

import json
import os
import re
import hashlib
import tempfile
from io import BytesIO
from html import unescape
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib import error, request
from urllib.parse import urlencode
from bluefern_dispatches.generator import BASE_URL
from bluefern_dispatches.food_line_bluesky_approval import verify_approval
from bluefern_dispatches.food_line_bluesky_preview import build_food_line_bluesky_preview
from bluefern_dispatches.gaza_audio import select_gaza_audio_stories
from bluefern_dispatches.public_prose import sanitize_public_prose
from bluefern_dispatches.story_dedupe import (
    GAZA_EVENT_STOPWORDS,
    normalize_text as dedupe_normalize_text,
    normalize_url as dedupe_normalize_url,
    similarity as dedupe_similarity,
    story_identity_urls,
)
from bluesky_card_contract_private import (
    CardContractError,
    ExternalCardContract,
    build_external_record,
    validate_create_response,
    validate_external_record,
)

BLUESKY_API_BASE = "https://bsky.social/xrpc"
BLUESKY_MAX_POST_LENGTH = 300
BLUESKY_CARD_MAX_DESCRIPTION_LENGTH = 240
BLUESKY_CARD_FALLBACK_DESCRIPTION = "Source-backed daily Gaza briefing from The Blue Fern Co."
BLUESKY_GAZA_POST_FALLBACK = "The latest Gaza briefing is live.\n\nFull briefing:"
BLUESKY_DUPLICATE_SCAN_MAX_PAGES = 1000
FOOD_LINE_DISPATCH_SLUG = "food-line"
FOOD_LINE_BLUESKY_POST_FALLBACK = "The latest Food Line briefing is live.\n\nFull briefing:"
FOOD_LINE_BLUESKY_POST_STATE_FILENAME = "bluesky_post.json"
FOOD_LINE_SOCIAL_IMAGE_PATH = "assets/food-line-dispatch-social.jpg"
FOOD_LINE_SOCIAL_IMAGE_URL = f"{BASE_URL}/food-line/assets/food-line-dispatch-social.jpg"
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
HTML_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_CANONICAL_RE = re.compile(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)
HTML_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
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
GENERIC_GAZA_LEAD_PHRASES = {
    "west bank developments",
    "regional developments",
    "diplomacy context",
    "accountability context",
}
GENERIC_TITLE_ENDINGS = (
    "update",
    "updates",
    "developments",
    "context",
    "overview",
    "briefing",
    "newsfeed",
)
SECONDARY_CLAUSE_PREFIXES = (
    "also covered:",
    "also:",
    "the update also covers",
    "the briefing also covers",
)
SUMMARY_RESTART_RE = re.compile(
    r"\b(Israeli forces|Israeli PM|Netanyahu|Medics|Witnesses|Officials|Mediators|The ICRC|Dr\s+[A-Z])\b"
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


def _correction_receipt_path(project_root: Path, edition_date: str) -> Path:
    return _receipt_path(project_root, edition_date).with_name("bluesky_card_correction_receipt.json")


def _load_correction_receipt(project_root: Path, edition_date: str) -> dict[str, Any] | None:
    path = _correction_receipt_path(project_root, edition_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"_receipt_error": "correction_receipt_unreadable"}
    if not isinstance(payload, dict) or not payload or not str(payload.get("status") or "").strip():
        return {"_receipt_error": "correction_receipt_invalid"}
    return payload


def _write_correction_receipt(
    *,
    project_root: Path,
    edition_date: str,
    public_url: str,
    post_text: str,
    old_uri: str,
    old_cid: str,
    old_engagement: dict[str, int],
    status: str,
    reason: str | None = None,
    new_uri: str | None = None,
    new_cid: str | None = None,
    card_contract: ExternalCardContract | None = None,
    link_facets_sha256: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "correction_type": "replace_exact_old_gaza_post",
        "edition_date": edition_date,
        "public_url": public_url,
        "post_text_sha256": hashlib.sha256(post_text.encode("utf-8")).hexdigest(),
        "old_uri": old_uri,
        "old_cid": old_cid,
        "old_engagement": dict(old_engagement),
        "status": status,
        "written_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if reason:
        payload["reason"] = reason
    if new_uri:
        payload["new_uri"] = new_uri
    if new_cid:
        payload["new_cid"] = new_cid
    if card_contract is not None:
        payload.update(
            {
                "card_mode": "external",
                "card_uri": card_contract.edition_url,
                "card_title": card_contract.title,
                "card_description": card_contract.description,
                "thumb_cid": card_contract.thumb_cid,
                "thumb_source_sha256": card_contract.thumb_sha256,
            }
        )
    if link_facets_sha256:
        payload["link_facets_sha256"] = link_facets_sha256
    if extra:
        payload.update(extra)
    _write_receipt_atomic(_correction_receipt_path(project_root, edition_date), payload)


def _claim_correction_pending_receipt(
    *,
    project_root: Path,
    edition_date: str,
    public_url: str,
    post_text: str,
    old_uri: str,
    old_cid: str,
    old_engagement: dict[str, int],
    card_contract: ExternalCardContract,
) -> bool:
    """Exclusively claim the correction before any createRecord request."""
    receipt_path = _correction_receipt_path(project_root, edition_date)
    lock_path = receipt_path.with_name(f"{receipt_path.name}.claim.lock")
    lock_created = False
    marker_written = False
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write("correction claim")
            lock_created = True
        except FileExistsError:
            return False
        existing = _load_correction_receipt(project_root, edition_date)
        if existing:
            # Pending, uncertain, completed, malformed, or differently bound
            # state is never silently stale-reclaimed by a second caller.
            return False
        _write_correction_receipt(
            project_root=project_root,
            edition_date=edition_date,
            public_url=public_url,
            post_text=post_text,
            old_uri=old_uri,
            old_cid=old_cid,
            old_engagement=old_engagement,
            status="pending_create",
            card_contract=card_contract,
        )
        marker_written = True
        return True
    finally:
        if lock_created and marker_written:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


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


def _public_url_base(value: Any) -> str:
    return str(value or "").split("?", 1)[0].rstrip("/") + "/"


def _validate_live_public_verification(
    verification: Any,
    *,
    edition_date: str,
    public_url: str,
) -> tuple[bool, str]:
    if not isinstance(verification, dict):
        return False, "missing_live_public_verification"
    if str(verification.get("status") or "") != "LIVE_OK":
        return False, "live_public_verification_not_ok"
    # The existing operator's "homepage" proof is the Gaza desk landing page
    # derived from the edition URL, not the global Dispatches homepage.
    for field in ("live_http_ok", "live_archive_ok", "live_homepage_ok"):
        if verification.get(field) is not True:
            return False, f"live_public_verification_{field}_missing"
    if _public_url_base(verification.get("edition_url")) != _public_url_base(public_url):
        return False, "live_public_edition_url_mismatch"
    if edition_date not in _public_url_base(verification.get("edition_url")):
        return False, "live_public_edition_date_mismatch"
    archive_base = _public_url_base(verification.get("archive_url"))
    if not archive_base.endswith("/gaza/archive.html/") and not archive_base.endswith("/gaza/archive.html"):
        return False, "live_public_archive_url_mismatch"
    return True, "passed"


def _load_exact_post_text(
    path_value: Any,
    expected_sha256: Any,
    *,
    edition_date: str,
    public_url: str,
) -> tuple[str | None, str | None, str | None]:
    path_text = str(path_value or "").strip()
    expected = str(expected_sha256 or "").strip().lower()
    if not path_text or not expected:
        return None, None, "exact_text_path_and_hash_required"
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return None, None, "exact_text_hash_invalid"
    path = Path(path_text)
    try:
        raw = path.read_bytes()
    except Exception:  # noqa: BLE001
        return None, None, "exact_text_file_unreadable"
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        return None, actual, "exact_text_hash_mismatch"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, actual, "exact_text_not_utf8"
    if not text or len(text) > BLUESKY_MAX_POST_LENGTH:
        return None, actual, "exact_text_length_invalid"
    if _public_url_base(public_url).rstrip("/") not in text or edition_date not in _public_url_base(public_url):
        return None, actual, "exact_text_public_url_binding_missing"
    return text, actual, None


def _load_receipt_for_edition_any_status(project_root: Path, edition_date: str) -> dict[str, Any] | None:
    path = _receipt_path(project_root, edition_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"_receipt_error": "local_post_receipt_unreadable"}
    if not isinstance(payload, dict) or not payload:
        return {"_receipt_error": "local_post_receipt_invalid"}
    if not str(payload.get("status") or "").strip():
        return {"_receipt_error": "local_post_receipt_invalid"}
    return payload


def _write_receipt_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish a small receipt atomically so readers never use a partial outcome."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _claim_pending_receipt(
    *,
    project_root: Path,
    edition_date: str,
    public_url: str,
    post_text: str,
    card_title: str,
    card_description: str,
    target_handle: str,
    target_did: str,
    approved_text_sha256: str,
) -> bool:
    """Atomically admit one exact-mode createRecord attempt.

    A durable pending receipt is intentionally left behind if the process dies
    after admission. A later invocation then fails closed and must reconcile
    the remote account instead of sending a second post.
    """
    path = _receipt_path(project_root, edition_date)
    lock_path = path.with_name(f"{path.name}.lock")
    lock_created = False
    pending_written = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock_path.open("x", encoding="utf-8") as lock:
                lock.write("pending receipt admission")
            lock_created = True
        except FileExistsError:
            return False

        existing = _load_receipt_for_edition_any_status(project_root, edition_date)
        if existing:
            if str(existing.get("_receipt_error") or ""):
                return False
            if str(existing.get("status") or "") != "failure":
                return False

        _write_success_receipt(
            project_root=project_root,
            edition_date=edition_date,
            public_url=public_url,
            post_uri="",
            post_text=post_text,
            card_title=card_title,
            card_description=card_description,
            embed_type="",
            thumb_status="not_attempted",
            original_thumb_bytes=None,
            uploaded_thumb_bytes=None,
            target_handle=target_handle,
            target_did=target_did,
            approved_text_sha256=approved_text_sha256,
            status="pending",
            reason="post_attempt_pending_reconcile_before_retry",
        )
        pending_written = True
        return True
    finally:
        # If writing the marker failed, retaining the lock is fail-closed. If
        # the marker was published, the marker itself is the durable guard.
        if lock_created and pending_written:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _exact_post_identity_valid(post_uri: Any, post_cid: Any, expected_did: str) -> bool:
    uri = str(post_uri or "").strip()
    cid = str(post_cid or "").strip()
    prefix = f"at://{str(expected_did).strip()}/app.bsky.feed.post/"
    return bool(uri.startswith(prefix) and uri[len(prefix) :].strip() and cid)


def _private_external_card_contract(
    expected_card: dict[str, Any] | None,
    *,
    edition_date: str,
    public_url: str,
    post_text: str,
) -> ExternalCardContract:
    if not isinstance(expected_card, dict):
        raise CardContractError("external-card contract is required")
    contract = ExternalCardContract(
        edition_date=expected_card.get("edition_date"),
        edition_url=expected_card.get("edition_url"),
        title=expected_card.get("title"),
        description=expected_card.get("description"),
        thumb_cid=expected_card.get("thumb_cid"),
        thumb_sha256=expected_card.get("thumb_sha256"),
    )
    contract.validate(post_text)
    if contract.edition_date != edition_date or contract.edition_url != public_url:
        raise CardContractError("external-card contract does not bind the requested edition")
    return contract


def _private_facets_sha256(record: dict[str, Any]) -> str:
    facets = record.get("facets")
    return hashlib.sha256(json.dumps(facets, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _exact_success_receipt_valid(
    payload: dict[str, Any],
    *,
    expected_text_sha256: str,
    expected_handle: str,
    expected_did: str,
    expected_card_contract: ExternalCardContract | None = None,
) -> bool:
    if str(payload.get("status") or "") != "success":
        return False
    if not _exact_post_identity_valid(payload.get("post_uri"), payload.get("post_cid"), expected_did):
        return False
    if str(payload.get("post_text_sha256") or "").strip().lower() != str(expected_text_sha256).strip().lower():
        return False
    if str(payload.get("target_handle") or "").strip() != str(expected_handle).strip():
        return False
    if str(payload.get("target_did") or "").strip() != str(expected_did).strip():
        return False
    if expected_card_contract is None:
        if "embed_type" not in payload or payload.get("embed_type") not in {None, ""}:
            return False
    else:
        if payload.get("embed_type") != "app.bsky.embed.external":
            return False
        if payload.get("card_uri") != expected_card_contract.edition_url:
            return False
        if payload.get("card_title") != expected_card_contract.title:
            return False
        if payload.get("card_description") != expected_card_contract.description:
            return False
        if payload.get("thumb_cid") != expected_card_contract.thumb_cid:
            return False
        if str(payload.get("thumb_source_sha256") or "").lower() != expected_card_contract.thumb_sha256.lower():
            return False
        if not str(payload.get("link_facets_sha256") or "").strip():
            return False
    return True


def _remote_duplicate_reconciliation(
    repo: str,
    public_url: str,
    post_text: str,
    expected_card_contract: ExternalCardContract | None = None,
    *,
    ignore_exact_uri: str | None = None,
    ignore_exact_cid: str | None = None,
) -> dict[str, Any]:
    public_url_base = _public_url_base(public_url).rstrip("/")
    malformed = False
    matches: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    approved_text_hash = hashlib.sha256(post_text.encode("utf-8")).hexdigest()
    cursor: str | None = None
    seen_cursors: set[str] = set()
    pages_scanned = 0
    ignored_exact_old_record = 0
    while True:
        params = {"repo": repo, "collection": "app.bsky.feed.post", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        query = urlencode(params)
        req = request.Request(
            f"{BLUESKY_API_BASE}/com.atproto.repo.listRecords?{query}",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=20.0) as resp:
                body = resp.read().decode("utf-8")
            payload = json.loads(body) if body else {}
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "uncertain",
                "reason": "remote_duplicate_check_failed",
                "error_type": type(exc).__name__,
                "pages_scanned": pages_scanned,
            }
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            return {
                "status": "uncertain",
                "reason": "remote_duplicate_response_invalid",
                "pages_scanned": pages_scanned,
            }
        pages_scanned += 1
        for row in payload["records"]:
            if not isinstance(row, dict):
                malformed = True
                continue
            value = row.get("value") if isinstance(row.get("value"), dict) else row.get("record")
            if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                malformed = True
                continue
            record_text = value["text"]
            post_uri = str(row.get("uri") or "").strip()
            if not _exact_post_identity_valid(post_uri, row.get("cid"), repo):
                malformed = True
                continue
            if ignore_exact_uri and post_uri == ignore_exact_uri:
                if str(row.get("cid") or "").strip() != str(ignore_exact_cid or "").strip():
                    return {
                        "status": "conflict",
                        "reason": "correction_old_record_cid_changed_in_duplicate_scan",
                        "pages_scanned": pages_scanned,
                        "post_uri": post_uri,
                        "post_cid": row.get("cid"),
                    }
                ignored_exact_old_record += 1
                continue
            embed = value.get("embed") if isinstance(value.get("embed"), dict) else {}
            external = embed.get("external") if isinstance(embed.get("external"), dict) else {}
            embedded_url = str(external.get("uri") or "").strip()
            embedded_url_base = _public_url_base(embedded_url).rstrip("/") if embedded_url else ""
            same_text = record_text == post_text
            item = {"post_uri": post_uri, "post_cid": row.get("cid")}
            if expected_card_contract is not None:
                try:
                    validate_external_record(record=value, post_text=post_text, contract=expected_card_contract)
                except CardContractError:
                    if same_text or public_url_base in record_text or embedded_url_base == public_url_base:
                        conflicts.append(item)
                else:
                    if same_text:
                        item["embed_type"] = "app.bsky.embed.external"
                        item["link_facets_sha256"] = _private_facets_sha256(value)
                        matches.append(item)
                    else:
                        conflicts.append(item)
            elif same_text or public_url_base in record_text or embedded_url_base == public_url_base:
                plain_shape = (
                    value.get("$type") in {None, "app.bsky.feed.post"}
                    and "embed" not in value
                    and "reply" not in value
                    and hashlib.sha256(record_text.encode("utf-8")).hexdigest() == approved_text_hash
                )
                if same_text and plain_shape:
                    matches.append(item)
                else:
                    conflicts.append(item)
        next_cursor = str(payload.get("cursor") or "").strip()
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            return {
                "status": "uncertain",
                "reason": "remote_duplicate_cursor_repeated",
                "pages_scanned": pages_scanned,
            }
        if pages_scanned >= BLUESKY_DUPLICATE_SCAN_MAX_PAGES:
            return {
                "status": "uncertain",
                "reason": "remote_duplicate_scan_limit_exceeded",
                "pages_scanned": pages_scanned,
            }
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if ignore_exact_uri and ignored_exact_old_record != 1:
        return {
            "status": "uncertain",
            "reason": "correction_old_record_not_seen_in_duplicate_scan",
            "pages_scanned": pages_scanned,
        }
    if malformed:
        return {"status": "uncertain", "reason": "remote_duplicate_record_invalid", "pages_scanned": pages_scanned}
    if conflicts or len(matches) > 1:
        item = (conflicts or matches)[0]
        return {"status": "conflict", "pages_scanned": pages_scanned, **item}
    if matches:
        return {"status": "match", "pages_scanned": pages_scanned, **matches[0]}
    return {"status": "none", "pages_scanned": pages_scanned}

def _gaza_bluesky_artifact_paths(project_root: Path, edition_date: str) -> list[Path]:
    site_root = project_root / "output" / "site" / "gaza" / "editions" / edition_date
    dispatch_root = project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date
    pages_root = project_root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date
    run_root = project_root / "data" / "dispatches" / "gaza" / "editions" / edition_date
    return [
        site_root / "curation_manifest.json",
        site_root / "edition_manifest.json",
        site_root / "index.html",
        pages_root / "curation_manifest.json",
        pages_root / "edition_manifest.json",
        pages_root / "index.html",
        dispatch_root / "curation_manifest.json",
        dispatch_root / "edition_manifest.json",
        dispatch_root / "index.html",
        run_root / "run_manifest.json",
    ]


def _gaza_dedupe_report_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / "gaza" / "editions" / edition_date / "dedupe_report.json"


def _gaza_social_candidate_identity_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("story_id", "source_record_id"):
        value = str(record.get(field) or "").strip()
        if value:
            keys.add(f"id:{_normalize_story_phrase(value)}")
    title = str(record.get("title") or "").strip()
    if title:
        keys.add(f"title:{_normalize_story_phrase(title)}")
    for field in ("canonical_url", "source_url", "url", "og_url", "public_url"):
        normalized = dedupe_normalize_url(record.get(field))
        if normalized:
            keys.add(f"url:{normalized}")
    return keys


def _gaza_suppressed_social_candidate_keys(project_root: Path, edition_date: str) -> tuple[set[str], Path | None]:
    path = _gaza_dedupe_report_path(project_root, edition_date)
    if not path.exists():
        return set(), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set(), path
    if not isinstance(payload, dict):
        return set(), path
    keys: set[str] = set()
    for row in payload.get("suppressed_candidates") or []:
        if isinstance(row, dict):
            keys.update(_gaza_social_candidate_identity_keys(row))
    return keys, path


def _gaza_filter_suppressed_social_rows(rows: list[dict[str, Any]], suppressed_keys: set[str]) -> list[dict[str, Any]]:
    if not suppressed_keys:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        row_keys = _gaza_social_candidate_identity_keys(row)
        if row_keys and row_keys & suppressed_keys:
            continue
        filtered.append(row)
    return filtered


def _gaza_rendered_social_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rendered = [
        row
        for row in rows
        if row.get("public_rendered") is True or str(row.get("include_decision") or "").strip().casefold() == "include"
    ]
    return rendered or rows


def _artifact_mentions_other_edition_date(text: str, edition_date: str) -> bool:
    for match in GAZA_EDITION_DATE_PATH_RE.finditer(text):
        if match.group(1) != edition_date:
            return True
    return False


def _extract_iso_date_from_text(text: str) -> str | None:
    cleaned = WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", str(text or "")))).strip()
    if not cleaned:
        return None
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
    if iso_match:
        return iso_match.group(1)
    month_match = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\b", cleaned)
    if not month_match:
        return None
    candidate = f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3)}"
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_html_field(html_text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(html_text or "")
    if not match:
        return ""
    return WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", match.group(1)))).strip()


def _extract_gaza_edition_identity(project_root: Path, edition_date: str, public_url: str | None) -> dict[str, Any]:
    manifest_candidates = [
        project_root / "output" / "site" / "gaza" / "editions" / edition_date / "edition_manifest.json",
        project_root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date / "edition_manifest.json",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "edition_manifest.json",
    ]
    page_candidates = [
        project_root / "output" / "site" / "gaza" / "editions" / edition_date / "index.html",
        project_root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date / "index.html",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "index.html",
    ]
    manifest_path = manifest_candidates[0]
    for candidate in manifest_candidates:
        if candidate.exists():
            manifest_path = candidate
            break
    page_path = page_candidates[0]
    for candidate in page_candidates:
        if candidate.exists():
            page_path = candidate
            break
    result: dict[str, Any] = {
        "requested_date": edition_date,
        "manifest_edition_date": None,
        "public_url": str(public_url or "").strip() or None,
        "canonical_url": None,
        "page_title": None,
        "page_heading": None,
        "mismatched_field": None,
        "date_issues": [],
        "verified": False,
        "manifest_path": str(manifest_path),
        "page_path": str(page_path),
    }
    issues: list[str] = []
    matched_fields: set[str] = set()

    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"manifest_edition_date could not be read from {manifest_path}: {exc}")
            result["mismatched_field"] = result["mismatched_field"] or "manifest_edition_date"
        else:
            if isinstance(payload, dict):
                manifest_value = str(payload.get("edition_date") or "").strip()
                result["manifest_edition_date"] = manifest_value or None
                if manifest_value:
                    if manifest_value == edition_date:
                        matched_fields.add("manifest_edition_date")
                    else:
                        issues.append(f"manifest_edition_date={manifest_value} does not match requested {edition_date}")
                        result["mismatched_field"] = result["mismatched_field"] or "manifest_edition_date"
            else:
                issues.append(f"manifest_edition_date could not be verified from {manifest_path}: expected a JSON object")
                result["mismatched_field"] = result["mismatched_field"] or "manifest_edition_date"
    else:
        result["mismatched_field"] = result["mismatched_field"] or "manifest_edition_date"

    public_url_value = str(public_url or "").strip()
    if public_url_value:
        result["public_url"] = public_url_value
        match = GAZA_EDITION_DATE_PATH_RE.search(public_url_value)
        public_url_date = match.group(1) if match else None
        if public_url_date == edition_date:
            matched_fields.add("public_url")
        elif public_url_date:
            issues.append(f"public_url={public_url_value} does not match requested {edition_date}")
            result["mismatched_field"] = result["mismatched_field"] or "public_url"
        else:
            issues.append(f"public_url could not be verified for requested {edition_date}")
            result["mismatched_field"] = result["mismatched_field"] or "public_url"
    else:
        issues.append(f"public_url missing for requested {edition_date}")
        result["mismatched_field"] = result["mismatched_field"] or "public_url"

    if page_path.exists():
        try:
            html_text = page_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            issues.append(f"page_title could not be read from {page_path}: {exc}")
            issues.append(f"canonical_url could not be read from {page_path}: {exc}")
            result["mismatched_field"] = result["mismatched_field"] or "page_title"
        else:
            page_title = _extract_html_field(html_text, HTML_TITLE_RE)
            page_heading = _extract_html_field(html_text, HTML_H1_RE)
            canonical_url = _extract_html_field(html_text, HTML_CANONICAL_RE)
            result["page_title"] = page_title or None
            result["page_heading"] = page_heading or None
            result["canonical_url"] = canonical_url or None
            for field_name, value in (("page_title", page_title), ("page_heading", page_heading)):
                if not value:
                    continue
                parsed = _extract_iso_date_from_text(value)
                if parsed == edition_date:
                    matched_fields.add(field_name)
                elif parsed:
                    issues.append(f"{field_name}={value} does not match requested {edition_date}")
                    result["mismatched_field"] = result["mismatched_field"] or field_name
            if canonical_url:
                canonical_match = GAZA_EDITION_DATE_PATH_RE.search(canonical_url)
                canonical_date = canonical_match.group(1) if canonical_match else None
                if canonical_date == edition_date:
                    matched_fields.add("canonical_url")
                elif canonical_date:
                    issues.append(f"canonical_url={canonical_url} does not match requested {edition_date}")
                    result["mismatched_field"] = result["mismatched_field"] or "canonical_url"
                else:
                    issues.append(f"canonical_url could not be verified for requested {edition_date}")
                    result["mismatched_field"] = result["mismatched_field"] or "canonical_url"
    if not matched_fields:
        if not result["mismatched_field"]:
            result["mismatched_field"] = "requested_date"
        issues.append(f"requested_date={edition_date} could not be verified from manifest, public_url, canonical_url, or page_title")

    result["verified"] = not issues
    result["date_issues"] = issues
    return result


def _gaza_archive_surface_contains_edition(project_root: Path, edition_date: str) -> dict[str, Any]:
    marker = f"editions/{edition_date}/"
    candidates = [
        project_root / "bluefern-dispatches-pages" / "gaza" / "archive.html",
        project_root / "output" / "site" / "gaza" / "archive.html",
    ]
    checked: list[str] = []
    for path in candidates:
        checked.append(str(path))
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if marker in text or f"/gaza/{marker}" in text:
            return {"ok": True, "path": str(path), "checked_paths": checked}
    return {"ok": False, "path": None, "checked_paths": checked}


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
    suppressed_keys, dedupe_report_path = _gaza_suppressed_social_candidate_keys(project_root, edition_date)
    if dedupe_report_path is not None:
        source_artifact_paths.append(str(dedupe_report_path))
    story_rows = _gaza_filter_suppressed_social_rows(story_rows, suppressed_keys)
    story_rows = _gaza_rendered_social_rows(story_rows)

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
    for row in sorted((row for row in selected_rows if isinstance(row, dict)), key=_story_priority):
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
    post_cid: str | None = None,
    target_handle: str | None = None,
    target_did: str | None = None,
    approved_text_sha256: str | None = None,
    card_mode: str | None = None,
    card_uri: str | None = None,
    thumb_cid: str | None = None,
    thumb_source_sha256: str | None = None,
    link_facets_sha256: str | None = None,
    status: str = "success",
    reason: str | None = None,
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
        "status": status,
    }
    if post_cid:
        payload["post_cid"] = post_cid
    if target_handle:
        payload["target_handle"] = target_handle
    if target_did:
        payload["target_did"] = target_did
    if approved_text_sha256:
        payload["approved_text_sha256"] = approved_text_sha256
    if card_mode:
        payload["card_mode"] = card_mode
    if card_uri:
        payload["card_uri"] = card_uri
    if thumb_cid:
        payload["thumb_cid"] = thumb_cid
    if thumb_source_sha256:
        payload["thumb_source_sha256"] = thumb_source_sha256
    if link_facets_sha256:
        payload["link_facets_sha256"] = link_facets_sha256
    if reason:
        payload["reason"] = reason
    _write_receipt_atomic(path, payload)


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


def _normalize_story_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" .,:;!?\"'")).casefold()


def _has_repeated_ngram(text: str, n: int = 3) -> bool:
    words = re.findall(r"[a-z0-9']+", str(text or "").lower())
    if len(words) < n * 2:
        return False
    seen: set[tuple[str, ...]] = set()
    for idx in range(len(words) - n + 1):
        gram = tuple(words[idx : idx + n])
        if gram in seen:
            return True
        seen.add(gram)
    return False


def _looks_like_generic_story_text(text: str) -> bool:
    normalized = _normalize_story_phrase(text)
    if not normalized:
        return True
    return normalized in GENERIC_GAZA_LEAD_PHRASES


def _looks_like_generic_title(title: str) -> bool:
    normalized = _normalize_story_phrase(title)
    return any(normalized.endswith(ending) for ending in GENERIC_TITLE_ENDINGS)


def _trim_restarted_summary(text: str) -> str:
    value = str(text or "").strip()
    if len(value) < 80:
        return value
    for match in SUMMARY_RESTART_RE.finditer(value):
        if match.start() < 60:
            continue
        prefix = value[: match.start()].rstrip(" ,;:-")
        if len(prefix) >= 40:
            return sanitize_public_prose(prefix)
    return value


def _story_priority(record: dict[str, Any]) -> tuple[int, int, int, int]:
    lead_score = int(record.get("score") or record.get("top_story_relevance_score") or 0)
    substantive = 1 if bool(record.get("substantive_ground")) else 0
    core_ground = 1 if bool(record.get("core_ground_development")) else 0
    core_scope = 1 if _normalize_story_phrase(record.get("story_scope") or record.get("region_scope") or "") == "core gaza" else 0
    return (-core_ground, -substantive, -core_scope, -lead_score)


def _story_text_quality(cleaned: str, *, field_name: str) -> int:
    if not cleaned or _looks_like_generic_story_text(cleaned):
        return -100
    normalized = _normalize_story_phrase(cleaned)
    score = 0
    if field_name == "title":
        score += 2
        if not _looks_like_generic_title(cleaned):
            score += 2
    elif field_name in {"social_summary", "public_summary"}:
        score += 6
    else:
        score += 5
    if len(cleaned) > 150:
        score -= 2
    if _has_repeated_ngram(cleaned):
        score -= 5
    if re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][A-Za-z'/-]+){1,3})\b.*\b\1\b", cleaned):
        score -= 4
    if normalized.endswith(" west bank") or normalized.endswith(" gaza"):
        score -= 1
    return score


def _best_story_reader_text(record: dict[str, Any], max_length: int) -> str:
    candidates: list[tuple[int, int, str]] = []
    for order, key in enumerate(("social_summary", "public_summary", "summary", "summary_or_snippet", "description", "title")):
        raw = record.get(key)
        raw_text = str(raw or "")
        raw_variants = [raw_text]
        if key != "title":
            trimmed_raw = _trim_restarted_summary(raw_text)
            if trimmed_raw and trimmed_raw != raw_text:
                raw_variants = [trimmed_raw]
        for raw_variant in raw_variants:
            variant = _clean_description_text(raw_variant, max_length)
            if not variant:
                continue
            quality = _story_text_quality(variant, field_name=key)
            if quality <= -100:
                continue
            candidates.append((quality, -order, variant))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][2]


def _story_post_snippet(record: dict[str, Any]) -> str:
    cleaned = _best_story_reader_text(record, 180)
    if not cleaned:
        return ""
    lowered = _record_text_blob(record)
    if "khan younis" in lowered and "strike" in lowered:
        return "Khan Younis strikes"
    if "civil defence" in lowered and "10 killed" in lowered:
        return "Gaza civil defence reported 10 killed"
    if "cairo" in lowered and "mediator" in lowered and ("ceasefire" in lowered or "talk" in lowered):
        return "Cairo ceasefire talks"
    if "flotilla" in lowered or "prison ship" in lowered:
        return "flotilla detention"
    return cleaned


def _gaza_story_duplicate_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("story_id", "source_record_id", "source_id"):
        value = str(record.get(field) or "").strip()
        if value:
            keys.add(f"id:{_normalize_story_phrase(value)}")
    for field in ("canonical_url", "source_url", "url", "og_url", "public_url"):
        normalized = dedupe_normalize_url(record.get(field))
        if normalized:
            keys.add(f"url:{normalized}")
    for url in story_identity_urls(record):
        normalized = dedupe_normalize_url(url)
        if normalized:
            keys.add(f"url:{normalized}")
    return keys


def _gaza_story_similarity_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("title") or ""),
        str(record.get("summary") or ""),
        str(record.get("public_summary") or ""),
        str(record.get("social_summary") or ""),
        str(record.get("summary_or_snippet") or ""),
        str(record.get("description") or ""),
        str(record.get("dek") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _gaza_similarity_token(value: Any) -> str:
    token = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if not token or len(token) <= 3:
        return ""
    if token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    return token


def _gaza_story_similarity_tokens(record: dict[str, Any]) -> set[str]:
    text = dedupe_normalize_text(_gaza_story_similarity_text(record))
    return {
        normalized
        for normalized in (_gaza_similarity_token(token) for token in text.split())
        if normalized and normalized not in GAZA_EVENT_STOPWORDS
    }


def _gaza_story_is_duplicate(candidate: dict[str, Any], prior_rows: list[dict[str, Any]]) -> bool:
    candidate_keys = _gaza_story_duplicate_keys(candidate)
    candidate_text = _gaza_story_similarity_text(candidate)
    candidate_title = str(candidate.get("title") or "")
    candidate_summary = str(candidate.get("summary") or "")
    candidate_tokens = _gaza_story_similarity_tokens(candidate)
    if not candidate_text and not candidate_keys:
        return False
    for prior in prior_rows:
        prior_keys = _gaza_story_duplicate_keys(prior)
        if candidate_keys and prior_keys and candidate_keys & prior_keys:
            return True
        prior_text = _gaza_story_similarity_text(prior)
        prior_title = str(prior.get("title") or "")
        prior_summary = str(prior.get("summary") or "")
        prior_tokens = _gaza_story_similarity_tokens(prior)
        shared_tokens = candidate_tokens & prior_tokens
        union_tokens = candidate_tokens | prior_tokens
        if shared_tokens and union_tokens:
            overlap_ratio = len(shared_tokens) / len(union_tokens)
            if len(shared_tokens) >= 5 and overlap_ratio >= 0.5 and dedupe_similarity(candidate_text, prior_text) >= 0.55:
                return True
        title_similarity = dedupe_similarity(candidate_title, prior_title)
        summary_similarity = dedupe_similarity(candidate_summary, prior_summary)
        if len(shared_tokens) >= 4 and title_similarity >= 0.8 and summary_similarity >= 0.7:
            return True
    return False


def _gaza_bluesky_story_topics(project_root: Path, edition_date: str, max_topics: int = 5) -> list[str]:
    context = _gaza_bluesky_context(project_root, edition_date)
    topics: list[str] = []
    selected_rows: list[dict[str, Any]] = []
    for row in sorted((row for row in (context.get("story_rows") or []) if isinstance(row, dict)), key=_story_priority):
        snippet = _story_post_snippet(row)
        if not snippet:
            continue
        if snippet.casefold() in {item.casefold() for item in topics}:
            continue
        if _gaza_story_is_duplicate(row, selected_rows):
            continue
        selected_rows.append(row)
        topics.append(snippet)
        if len(topics) >= max_topics:
            break
    return topics[:max_topics]


def _previous_gaza_edition_dates(project_root: Path, edition_date: str, limit: int = 3) -> list[str]:
    roots = [
        project_root / "data" / "dispatches" / "gaza" / "editions",
        project_root / "output" / "dispatches" / "gaza" / "editions",
        project_root / "output" / "site" / "gaza" / "editions",
        project_root / "bluefern-dispatches-pages" / "gaza" / "editions",
    ]
    dates: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.is_dir() and DATE_VALUE_RE.match(child.name) and child.name < edition_date:
                dates.add(child.name)
    return sorted(dates, reverse=True)[:limit]


def _topic_nearly_matches_any(topic: str, prior_topics: list[str]) -> bool:
    normalized = _normalize_story_phrase(topic)
    if not normalized:
        return False
    for prior in prior_topics:
        prior_normalized = _normalize_story_phrase(prior)
        if not prior_normalized:
            continue
        if normalized == prior_normalized or normalized in prior_normalized or prior_normalized in normalized:
            return True
        if dedupe_similarity(normalized, prior_normalized) >= 0.82:
            return True
    return False


def _prior_gaza_bluesky_topics(project_root: Path, edition_date: str, max_topics: int = 2) -> list[str]:
    for prior_date in _previous_gaza_edition_dates(project_root, edition_date):
        topics = _gaza_bluesky_story_topics(project_root, prior_date, max_topics=max_topics)
        if topics:
            return topics
    return []


def _prefer_fresh_lead_topic(topics: list[str], prior_topics: list[str]) -> tuple[list[str], bool]:
    if not topics or not prior_topics or not _topic_nearly_matches_any(topics[0], prior_topics):
        return topics, False
    fresh = [topic for topic in topics if not _topic_nearly_matches_any(topic, prior_topics)]
    repeated = [topic for topic in topics if _topic_nearly_matches_any(topic, prior_topics)]
    if not fresh:
        return topics, False
    return [*fresh, *repeated], True


def _derive_gaza_focus_topics(project_root: Path, edition_date: str, max_topics: int = 5) -> list[str]:
    return _gaza_bluesky_story_topics(project_root, edition_date, max_topics=max_topics)


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


def _gaza_bluesky_intro_label(context: dict[str, Any]) -> str:
    edition_manifest = context.get("edition_manifest")
    status = ""
    if isinstance(edition_manifest, dict):
        status = str(edition_manifest.get("source_adequacy_status") or "").strip().lower()
    return "Gaza source-backed update" if status == "limited_source_update" else "Gaza briefing"


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
        cleaned = _best_story_reader_text(row, max_length)
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
    topics = _gaza_bluesky_story_topics(root, clean_date, max_topics=2)
    topics, used_fresh_lead = _prefer_fresh_lead_topic(topics, _prior_gaza_bluesky_topics(root, clean_date, max_topics=2))
    public_summary = _gaza_public_summary_for_bluesky(root, clean_date, max_length=180)
    if not topics and not public_summary:
        return BLUESKY_GAZA_POST_FALLBACK
    date_text = _format_post_date(clean_date)
    intro_label = _gaza_bluesky_intro_label(context)
    url_suffix = f"\n\nPublic edition: {public_url}" if include_public_url and str(public_url or "").strip() else ""

    def _with_suffix(body: str) -> str:
        body = _normalize_public_post_text(body)
        if url_suffix and len(f"{body}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return f"{body}{url_suffix}"
        return body

    if topics:
        if len(topics) == 1:
            prefix = "New in" if used_fresh_lead else "In"
            intro = f"{prefix} the {date_text} {intro_label}: {topics[0]}."
        elif len(topics) == 2:
            prefix = "New in" if used_fresh_lead else "In"
            intro = f"{prefix} the {date_text} {intro_label}: {topics[0]}. Also covered: {topics[1]}."
        else:
            intro = f"In the {date_text} {intro_label}: {topics[0]}. Also covered: {topics[1]}."
        candidate = _with_suffix(intro)
        if len(candidate) <= BLUESKY_MAX_POST_LENGTH:
            return candidate
        if len(f"{intro}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return _normalize_public_post_text(f"{intro}{url_suffix}" if url_suffix else intro)
    if public_summary:
        prefix = f"In the {date_text} {intro_label}: "
        available = BLUESKY_MAX_POST_LENGTH - len(prefix) - len(url_suffix) - 2
        if available > 0:
            summary = _shorten_post_text_at_word_boundary(public_summary.rstrip(".") or public_summary, available).rstrip(".")
            candidate = f"{prefix}{summary}."
            candidate = _with_suffix(candidate)
            if len(candidate) <= BLUESKY_MAX_POST_LENGTH:
                return candidate
    if topics:
        compact_topics = topics[:2]
        if len(compact_topics) == 1:
            compact = f"In the {date_text} {intro_label}: {compact_topics[0]}."
        else:
            compact = f"In the {date_text} {intro_label}: {compact_topics[0]}. Also covered: {compact_topics[1]}."
        if len(f"{compact}{url_suffix}") <= BLUESKY_MAX_POST_LENGTH:
            return _normalize_public_post_text(f"{compact}{url_suffix}" if url_suffix else compact)
    return BLUESKY_GAZA_POST_FALLBACK[:BLUESKY_MAX_POST_LENGTH]


def _build_gaza_generic_card_description(edition_date: str, max_length: int, *, limited_source: bool = False) -> str:
    try:
        dt = datetime.strptime(edition_date, "%Y-%m-%d")
        date_text = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        short_date_text = f"{dt.strftime('%B')} {dt.day}"
    except ValueError:
        date_text = edition_date
        short_date_text = edition_date
    if limited_source:
        text = f"Read the {short_date_text} source-backed Gaza update from The Blue Fern Co."
    else:
        text = f"The Blue Fern Co. Gaza briefing for {date_text}."
    return _clean_description_text(text, max_length) or BLUESKY_CARD_FALLBACK_DESCRIPTION


def _primary_gaza_post_sentence(post_text: str) -> str:
    text = _normalize_public_post_text(post_text).split("\n\n", 1)[0].strip()
    if text.lower().startswith("in the ") and ": " in text:
        text = text.split(": ", 1)[1].strip()
    return _normalize_story_phrase(text)


def _description_duplicates_gaza_post(description: str, post_text: str) -> bool:
    desc_norm = _normalize_story_phrase(description)
    post_norm = _primary_gaza_post_sentence(post_text)
    if not desc_norm or not post_norm:
        return False
    return desc_norm == post_norm or desc_norm in post_norm or post_norm in desc_norm


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
    candidate_paths = [
        project_root / "output" / "site" / "gaza" / "editions" / edition_date / "curation_manifest.json",
        project_root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date / "curation_manifest.json",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "curation_manifest.json",
    ]
    payload = None
    for curated_path in candidate_paths:
        payload = _read_json(curated_path)
        if payload is not None:
            break
    preferred: list[str] = []
    fallback: list[str] = []
    if isinstance(payload, list):
        payload_rows = _gaza_rendered_social_rows([item for item in payload if isinstance(item, dict)])
        eligible_items = select_gaza_audio_stories(payload_rows)
        for item in eligible_items or payload_rows:
            if not isinstance(item, dict):
                continue
            cleaned = _best_story_reader_text(item, max_length)
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
    return _first_usable_field(payload, ("social_summary", "summary", "description", "dek", "public_summary", "title"), max_length)


def _extract_first_paragraph_from_html(project_root: Path, edition_date: str, max_length: int) -> str:
    candidate_paths = [
        project_root / "output" / "site" / "gaza" / "editions" / edition_date / "index.html",
        project_root / "bluefern-dispatches-pages" / "gaza" / "editions" / edition_date / "index.html",
        project_root / "output" / "dispatches" / "gaza" / "editions" / edition_date / "index.html",
    ]
    index_path = None
    for candidate in candidate_paths:
        if candidate.exists():
            index_path = candidate
            break
    if index_path is None:
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
    edition_manifest = context.get("edition_manifest")
    limited_source = False
    if isinstance(edition_manifest, dict):
        limited_source = str(edition_manifest.get("source_adequacy_status") or "").strip().lower() == "limited_source_update"
    for row in sorted((row for row in (context.get("story_rows") or []) if isinstance(row, dict)), key=_story_priority):
        cleaned = _best_story_reader_text(row, max_length)
        if cleaned:
            return cleaned
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
    return _build_gaza_generic_card_description(date_text, max_length, limited_source=limited_source)


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


def _upload_card_thumb(
    access_jwt: str,
    project_root: Path,
    edition_date: str,
    *,
    expected_source_sha256: str | None = None,
) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None, str | None]:
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
            source_sha256 = hashlib.sha256(data).hexdigest()
            if expected_source_sha256 and source_sha256.lower() != str(expected_source_sha256).strip().lower():
                continue
            if original_bytes <= BLUESKY_COMPRESS_TARGET_BYTES:
                blob = _upload_blob(access_jwt, data, mime)
                if blob:
                    return blob, "uploaded", False, original_bytes, original_bytes, source_sha256
                return None, "upload_failed", False, original_bytes, None, source_sha256
            compressed = _compress_thumb_to_jpeg(data)
            if not compressed:
                return None, "skipped_too_large", False, original_bytes, None, source_sha256
            if len(compressed) >= BLUESKY_BLOB_MAX_BYTES:
                return None, "skipped_too_large", False, original_bytes, None, source_sha256
            blob = _upload_blob(access_jwt, compressed, "image/jpeg")
            if blob:
                return blob, "uploaded_compressed", True, original_bytes, len(compressed), source_sha256
            return None, "upload_failed", True, original_bytes, len(compressed), source_sha256
        except Exception:  # noqa: BLE001
            return None, "upload_failed", False, None, None, None
    return None, "source_hash_mismatch" if expected_source_sha256 else "no_thumbnail", False, None, None, None


def _upload_food_line_card_thumb(
    access_jwt: str,
    project_root: Path,
    card_image_path: str,
) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None, Path | None]:
    rel_path = Path(card_image_path)
    path = project_root / rel_path
    if not path.exists():
        return None, "missing_edition_card", False, None, None, path
    mime = _guess_image_mime(path)
    if not mime:
        return None, "invalid_thumbnail", False, None, None, path
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
    preview = build_food_line_bluesky_preview(Path.cwd(), edition_date)
    return str(preview["card_title"])


def _normalize_sentence(text: str) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "").strip())
    if not sentence:
        return ""
    return sentence.rstrip(".") + "."


def _food_line_card_description(post_text: str, *, max_length: int = BLUESKY_CARD_MAX_DESCRIPTION_LENGTH) -> str:
    description = str(post_text or "").strip()
    if len(description) <= max_length:
        return description
    return description[: max_length - 1].rstrip() + "..." if description else FOOD_LINE_BLUESKY_POST_FALLBACK


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


def _auth_get_json(url: str, access_jwt: str, timeout: float = 20.0) -> dict[str, Any]:
    req = request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {access_jwt}"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body) if body else {}
    if not isinstance(parsed, dict):
        raise ValueError("Bluesky API response was not a JSON object")
    return parsed


def _is_exact_record_not_found(exc: error.HTTPError, expected_url: str) -> bool:
    if getattr(exc, "code", None) not in {400, 404}:
        return False
    if str(getattr(exc, "url", "") or "") != str(expected_url):
        return False
    try:
        body = exc.read().decode("utf-8")
        payload = json.loads(body) if body else {}
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and payload.get("error") == "RecordNotFound"


def _at_uri_rkey(uri: str, expected_did: str) -> str | None:
    prefix = f"at://{expected_did}/app.bsky.feed.post/"
    if not uri.startswith(prefix):
        return None
    rkey = uri[len(prefix) :].strip()
    return rkey or None


def _read_exact_old_post_state(
    *,
    access_jwt: str,
    old_uri: str,
    old_cid: str,
    expected_did: str,
    expected_text: str,
) -> dict[str, Any]:
    rkey = _at_uri_rkey(old_uri, expected_did)
    if not rkey:
        return {"status": "blocked", "reason": "correction_old_uri_invalid"}
    try:
        record_query = urlencode({"repo": expected_did, "collection": "app.bsky.feed.post", "rkey": rkey})
        record = _auth_get_json(
            f"{BLUESKY_API_BASE}/com.atproto.repo.getRecord?{record_query}",
            access_jwt,
        )
        value = record.get("value") if isinstance(record.get("value"), dict) else {}
        if (
            record.get("uri") != old_uri
            or record.get("cid") != old_cid
            or value.get("text") != expected_text
            or value.get("$type") not in {None, "app.bsky.feed.post"}
            or "embed" in value
            or "reply" in value
        ):
            return {"status": "blocked", "reason": "correction_old_record_binding_mismatch"}
        thread_query = urlencode({"uri": old_uri, "depth": "0", "parentHeight": "0"})
        thread = _auth_get_json(f"{BLUESKY_API_BASE}/app.bsky.feed.getPostThread?{thread_query}", access_jwt)
        post = ((thread.get("thread") or {}).get("post") or {}) if isinstance(thread.get("thread"), dict) else {}
        if post.get("uri") != old_uri or post.get("cid") != old_cid:
            return {"status": "blocked", "reason": "correction_old_engagement_identity_mismatch"}
        counts: dict[str, int] = {}
        for field in ("likeCount", "repostCount", "replyCount", "quoteCount"):
            value_count = post.get(field)
            if isinstance(value_count, bool) or not isinstance(value_count, int) or value_count < 0:
                return {"status": "uncertain", "reason": "correction_old_engagement_unreadable"}
            counts[field] = value_count
        if any(counts.values()):
            return {"status": "blocked", "reason": "correction_old_record_engagement_changed", "engagement": counts}
        return {"status": "verified", "uri": old_uri, "cid": old_cid, "engagement": counts, "value": value}
    except error.HTTPError as exc:
        return {"status": "uncertain", "reason": "correction_old_record_read_failed", "error_type": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        return {"status": "uncertain", "reason": "correction_old_record_read_failed", "error_type": type(exc).__name__}


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
    sentence_parts = [part.strip(" .") for part in re.split(r"(?<=[.!?])\s+", clause_text) if part.strip(" .")]
    clauses: list[str] = []
    for part in sentence_parts or [clause_text]:
        normalized_part = part.strip(" .")
        for prefix in SECONDARY_CLAUSE_PREFIXES:
            if normalized_part.startswith(prefix):
                normalized_part = normalized_part[len(prefix) :].strip(" .")
                break
        if "; " in normalized_part:
            clauses.extend(item.strip(" .") for item in normalized_part.split(";") if item.strip(" ."))
        elif normalized_part:
            clauses.append(normalized_part)
    expected_lookup = {snippet.casefold(): snippet for snippet in expected_snippets}
    for clause in clauses:
        clause = clause.removeprefix("and ").strip(" .")
        if not clause:
            continue
        if clause in {"full briefing", "limited-source update", "source-backed update"}:
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
    approved_text_path: Path | str | None = None,
    approved_text_sha256: str | None = None,
    expected_handle: str | None = None,
    expected_did: str | None = None,
    live_verification: dict[str, Any] | None = None,
    expected_card_mode: str | None = None,
    expected_external_card: dict[str, Any] | None = None,
    correction_old_uri: str | None = None,
    correction_old_cid: str | None = None,
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
        "approved_text_sha256": None,
        "target_handle": None,
        "target_did": None,
        "card_mode": None,
        "card_uri": None,
        "thumb_cid": None,
        "link_facets_sha256": None,
        "correction_mode": False,
        "correction_receipt_path": None,
    }
    correction_mode = correction_old_uri is not None or correction_old_cid is not None
    result["correction_mode"] = correction_mode
    if correction_mode and (not correction_old_uri or not correction_old_cid):
        result["status"] = "blocked"
        result["reason"] = "correction_old_uri_and_cid_required"
        return result
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

    exact_text_mode = any(value is not None for value in (approved_text_path, approved_text_sha256, expected_handle, expected_did))
    if correction_mode and not exact_text_mode:
        result["status"] = "blocked"
        result["reason"] = "correction_requires_exact_text_binding"
        return result
    if correction_mode and expected_card_mode != "external":
        result["status"] = "blocked"
        result["reason"] = "correction_requires_external_card_mode"
        return result
    handle = str(os.getenv("BLUESKY_HANDLE", "")).strip()
    if exact_text_mode and not expected_handle:
        result["status"] = "blocked"
        result["reason"] = "exact_target_handle_required"
        return result
    if exact_text_mode and handle != str(expected_handle).strip():
        result["status"] = "blocked"
        result["reason"] = "exact_target_handle_mismatch"
        return result
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
    exact_text: str | None = None
    expected_card_contract: ExternalCardContract | None = None
    if exact_text_mode:
        if not expected_did:
            result["status"] = "blocked"
            result["reason"] = "exact_target_did_required"
            return result
        live_ok, live_reason = _validate_live_public_verification(
            live_verification,
            edition_date=edition_date,
            public_url=str(public_url),
        )
        if not live_ok:
            result["status"] = "blocked"
            result["reason"] = live_reason
            return result
        exact_text, actual_hash, exact_error = _load_exact_post_text(
            approved_text_path,
            approved_text_sha256,
            edition_date=edition_date,
            public_url=str(public_url),
        )
        result["approved_text_sha256"] = actual_hash
        if exact_error or exact_text is None:
            result["status"] = "blocked"
            result["reason"] = exact_error or "exact_text_binding_failed"
            return result
        if expected_card_mode not in {"plain", "external"}:
            result["status"] = "blocked"
            result["reason"] = "exact_card_mode_required"
            return result
        if expected_card_mode == "external":
            try:
                expected_card_contract = _private_external_card_contract(
                    expected_external_card,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_text=exact_text,
                )
            except CardContractError as exc:
                result["status"] = "blocked"
                result["reason"] = "external_card_contract_invalid"
                result["error_message"] = str(exc)
                return result
            result["card_mode"] = "external"
            result["card_uri"] = expected_card_contract.edition_url
        else:
            if expected_external_card is not None:
                result["status"] = "blocked"
                result["reason"] = "plain_mode_cannot_carry_external_card"
                return result
            result["card_mode"] = "plain"
        result["edition_date_verified"] = True
        result["archive_surface_verified"] = True
        result["live_public_verification"] = {
            "status": str(live_verification.get("status") or "") if isinstance(live_verification, dict) else None,
            "edition_url": str(live_verification.get("edition_url") or "") if isinstance(live_verification, dict) else None,
            "archive_url": str(live_verification.get("archive_url") or "") if isinstance(live_verification, dict) else None,
        }
    else:
        exact_text = None
    verification = _extract_gaza_edition_identity(root, edition_date, public_url)
    if not exact_text_mode:
        result["edition_date_verified"] = bool(verification.get("verified"))
    result["requested_date"] = verification.get("requested_date")
    result["manifest_edition_date"] = verification.get("manifest_edition_date")
    result["public_url"] = verification.get("public_url")
    result["canonical_url"] = verification.get("canonical_url")
    result["page_title"] = verification.get("page_title")
    result["page_heading"] = verification.get("page_heading")
    result["mismatched_field"] = verification.get("mismatched_field")
    result["date_issues"] = list(verification.get("date_issues") or [])
    if not exact_text_mode and verification.get("date_issues"):
        result["status"] = "blocked"
        result["reason"] = "current-edition-date-mismatch"
        result["stale_content_guard_status"] = "blocked"
        return result
    archive_surface = _gaza_archive_surface_contains_edition(root, edition_date)
    if not exact_text_mode:
        result["archive_surface_verified"] = bool(archive_surface.get("ok"))
        result["archive_surface_path"] = archive_surface.get("path")
        result["archive_surface_checked_paths"] = list(archive_surface.get("checked_paths") or [])
    if not exact_text_mode and not archive_surface.get("ok") and allow_publish:
        result["status"] = "blocked"
        result["reason"] = "current-edition-missing-from-gaza-archive"
        result["stale_content_guard_status"] = "blocked"
        return result
    verification_fields = {
        "requested_date": result["requested_date"],
        "manifest_edition_date": result["manifest_edition_date"],
        "public_url": result["public_url"],
        "canonical_url": result["canonical_url"],
        "page_title": result["page_title"],
        "page_heading": result["page_heading"],
        "mismatched_field": result["mismatched_field"],
        "date_issues": result["date_issues"],
    }
    if exact_text_mode:
        text = exact_text
        card_title = expected_card_contract.title if expected_card_contract else _build_gaza_card_title(edition_date)
        card_description = expected_card_contract.description if expected_card_contract else BLUESKY_CARD_FALLBACK_DESCRIPTION
        use_external_embed = expected_card_contract is not None
        result["stale_content_guard_status"] = "exact_text_hash_bound"
    else:
        text = build_gaza_bluesky_post_text(
            edition_date,
            public_url,
            project_root=root,
            include_public_url=False,
        )
        card_title = _build_gaza_card_title(edition_date)
        card_description = build_gaza_card_description(edition_date, root, max_length=BLUESKY_CARD_MAX_DESCRIPTION_LENGTH)
        if _description_duplicates_gaza_post(card_description, text):
            limited_source = False
            edition_manifest = context.get("edition_manifest")
            if isinstance(edition_manifest, dict):
                limited_source = str(edition_manifest.get("source_adequacy_status") or "").strip().lower() == "limited_source_update"
            card_description = _build_gaza_generic_card_description(
                edition_date,
                BLUESKY_CARD_MAX_DESCRIPTION_LENGTH,
                limited_source=limited_source,
            )
        use_external_embed = card_description != BLUESKY_CARD_FALLBACK_DESCRIPTION
        if not use_external_embed:
            text = build_gaza_bluesky_post_text(
                edition_date,
                public_url,
                project_root=root,
                include_public_url=True,
            )
    result["post_text"] = text
    result["card_title"] = card_title
    result["card_description"] = card_description
    if not exact_text_mode and text == BLUESKY_GAZA_POST_FALLBACK:
        result["status"] = "blocked"
        result["reason"] = "current-edition-public-summary-unavailable"
        result["stale_content_guard_status"] = "blocked"
        return result
    if not exact_text_mode:
        ok, guard_status = _bluesky_stale_content_guard(text, context)
        result["stale_content_guard_status"] = guard_status
    else:
        ok = True
    if not ok:
        result["status"] = "blocked"
        result["reason"] = guard_status
        return result
    if not allow_publish:
        result["reason"] = "dry_run"
        return result
    if correction_mode:
        correction_receipt = _load_correction_receipt(root, edition_date)
        result["correction_receipt_path"] = str(_correction_receipt_path(root, edition_date))
        if correction_receipt:
            if correction_receipt.get("_receipt_error"):
                result["status"] = "blocked"
                result["reason"] = str(correction_receipt["_receipt_error"])
                return result
            if correction_receipt.get("status") != "failure":
                result["status"] = "blocked"
                result["reason"] = "correction_receipt_requires_reconciliation"
                return result
    elif exact_text_mode:
        prior = _load_receipt_for_edition_any_status(root, edition_date)
        if prior:
            receipt_error = str(prior.get("_receipt_error") or "")
            if receipt_error:
                result["status"] = "blocked"
                result["reason"] = receipt_error
                return result
            if str(prior.get("public_url") or "").strip() != str(public_url).strip():
                result["status"] = "blocked"
                result["reason"] = "local_receipt_public_url_mismatch"
                return result
            prior_status = str(prior.get("status") or "")
            if prior_status in {"pending", "uncertain"}:
                result["status"] = "blocked"
                result["reason"] = (
                    "post_attempt_pending_reconcile_before_retry"
                    if prior_status == "pending"
                    else "post_outcome_uncertain_reconcile_before_retry"
                )
                return result
            if prior_status == "success":
                same_text = str(prior.get("post_text_sha256") or "").strip().lower() == str(approved_text_sha256).strip().lower()
                same_did = str(prior.get("target_did") or "").strip() == str(expected_did).strip()
                same_handle = str(prior.get("target_handle") or "").strip() == handle
                if same_text and same_did and same_handle:
                    if not _exact_success_receipt_valid(
                        prior,
                        expected_text_sha256=str(approved_text_sha256).strip().lower(),
                        expected_handle=handle,
                        expected_did=str(expected_did).strip(),
                        expected_card_contract=expected_card_contract,
                    ):
                        result["status"] = "blocked"
                        result["reason"] = "local_receipt_invalid"
                        return result
                    return {
                        **result,
                        "status": "skipped",
                        "post_uri": str(prior.get("post_uri") or "").strip() or None,
                        "reason": "skipped_existing_exact_receipt",
                        "post_text": text,
                        "card_title": prior.get("card_title") or card_title,
                        "card_description": prior.get("card_description") or card_description,
                        "target_handle": handle,
                        "target_did": expected_did,
                    }
                result["status"] = "blocked"
                result["reason"] = "local_receipt_content_mismatch"
                return result
            if prior_status not in {"failure"}:
                result["status"] = "blocked"
                result["reason"] = "local_receipt_status_unrecognized"
                return result
    elif not force_post:
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
                **verification_fields,
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
                **verification_fields,
            }
        result["target_handle"] = handle
        result["target_did"] = did
        if exact_text_mode and did != str(expected_did).strip():
            result["status"] = "blocked"
            result["reason"] = "exact_target_did_mismatch"
            return result
        correction_old_state: dict[str, Any] | None = None
        if correction_mode:
            correction_old_state = _read_exact_old_post_state(
                access_jwt=access_jwt,
                old_uri=str(correction_old_uri),
                old_cid=str(correction_old_cid),
                expected_did=did,
                expected_text=text,
            )
            if correction_old_state.get("status") != "verified":
                result["status"] = str(correction_old_state.get("status") or "blocked")
                result["reason"] = str(correction_old_state.get("reason") or "correction_old_record_unverified")
                return result
        if exact_text_mode:
            duplicate = _remote_duplicate_reconciliation(
                did,
                str(public_url),
                text,
                expected_card_contract=expected_card_contract,
                ignore_exact_uri=str(correction_old_uri) if correction_mode else None,
                ignore_exact_cid=str(correction_old_cid) if correction_mode else None,
            )
            if duplicate.get("status") == "match":
                if not str(duplicate.get("post_uri") or "").strip():
                    result["status"] = "blocked"
                    result["reason"] = "remote_duplicate_record_invalid"
                    return result
                _write_success_receipt(
                    project_root=root,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_uri=str(duplicate.get("post_uri") or ""),
                    post_text=text,
                    card_title=card_title,
                    card_description=card_description,
                    embed_type=duplicate.get("embed_type") or ("app.bsky.embed.external" if expected_card_contract is not None else ""),
                    thumb_status="not_attempted",
                    original_thumb_bytes=None,
                    uploaded_thumb_bytes=None,
                    post_cid=str(duplicate.get("post_cid") or "") or None,
                    target_handle=handle,
                    target_did=did,
                    approved_text_sha256=str(approved_text_sha256).strip().lower(),
                    card_mode="external" if expected_card_contract is not None else "plain",
                    card_uri=expected_card_contract.edition_url if expected_card_contract is not None else None,
                    thumb_cid=expected_card_contract.thumb_cid if expected_card_contract is not None else None,
                    thumb_source_sha256=expected_card_contract.thumb_sha256 if expected_card_contract is not None else None,
                    link_facets_sha256=duplicate.get("link_facets_sha256") if expected_card_contract is not None else None,
                )
                return {
                    **result,
                    "status": "skipped",
                    "post_uri": duplicate.get("post_uri"),
                    "reason": "skipped_matching_remote_record",
                    "post_text": text,
                    "card_title": card_title,
                    "card_description": card_description,
                }
            if duplicate.get("status") == "conflict":
                result["status"] = "blocked"
                result["reason"] = "ambiguous_remote_duplicate"
                return result
            if duplicate.get("status") != "none":
                result["status"] = "blocked"
                result["reason"] = str(duplicate.get("reason") or "remote_duplicate_check_uncertain")
                return result
        thumb_blob = None
        thumb_status = "not_attempted"
        compressed_thumb = False
        original_thumb_bytes = None
        uploaded_thumb_bytes = None
        thumb_source_sha256 = None
        image_path = None
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record_payload = {
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": created_at,
            },
        }
        if use_external_embed:
            thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes, thumb_source_sha256 = _upload_card_thumb(
                access_jwt,
                root,
                edition_date,
                expected_source_sha256=(expected_card_contract.thumb_sha256 if expected_card_contract is not None else None),
            )
            if exact_text_mode and expected_card_contract is not None and not thumb_blob:
                result["status"] = "blocked"
                result["reason"] = "approved_card_thumbnail_unavailable"
                return result
            if expected_card_contract is not None and thumb_source_sha256 != expected_card_contract.thumb_sha256:
                result["status"] = "blocked"
                result["reason"] = "approved_card_thumbnail_source_mismatch"
                return result
            external: dict[str, Any] = {
                "$type": "app.bsky.embed.external",
                "external": {"uri": str(public_url), "title": card_title, "description": card_description},
            }
            if thumb_blob:
                external["external"]["thumb"] = thumb_blob
                image_path = FOOD_LINE_SOCIAL_IMAGE_PATH
            record_payload["record"]["embed"] = external
            if exact_text_mode and expected_card_contract is not None:
                try:
                    record_payload["record"] = build_external_record(
                        post_text=text,
                        contract=expected_card_contract,
                        thumb_blob=thumb_blob,
                        created_at=created_at,
                    )
                except CardContractError as exc:
                    result["status"] = "blocked"
                    result["reason"] = "approved_card_payload_invalid"
                    result["error_message"] = str(exc)
                    return result
        req = _build_auth_request(f"{BLUESKY_API_BASE}/com.atproto.repo.createRecord", record_payload, access_jwt)
        if correction_mode:
            claimed = _claim_correction_pending_receipt(
                    project_root=root,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_text=text,
                    old_uri=str(correction_old_uri),
                    old_cid=str(correction_old_cid),
                    old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                    card_contract=expected_card_contract,
                )
            if not claimed:
                result["status"] = "blocked"
                result["reason"] = "correction_claim_unavailable_or_already_claimed"
                return result
        if exact_text_mode and not correction_mode and not _claim_pending_receipt(
            project_root=root,
            edition_date=edition_date,
            public_url=str(public_url),
            post_text=text,
            card_title=card_title,
            card_description=card_description,
            target_handle=handle,
            target_did=did,
            approved_text_sha256=str(approved_text_sha256).strip().lower(),
        ):
            result["status"] = "blocked"
            result["reason"] = "post_attempt_admission_uncertain"
            return result
        post_request_started = True
        with request.urlopen(req, timeout=20.0) as resp:
            body = resp.read().decode("utf-8")
        payload = json.loads(body) if body else {}
        post_uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
        post_cid = str(payload.get("cid") or "").strip() if isinstance(payload, dict) else ""
        expected_post_uri_prefix = f"at://{did}/app.bsky.feed.post/"
        exact_response_valid = (
            bool(post_uri)
            and post_uri.startswith(expected_post_uri_prefix)
            and bool(post_uri[len(expected_post_uri_prefix) :].strip())
            and bool(post_cid)
        )
        if not post_uri or (exact_text_mode and not exact_response_valid):
            if exact_text_mode:
                _write_success_receipt(
                    project_root=root,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_uri="",
                    post_text=text,
                    card_title=card_title,
                    card_description=card_description,
                    embed_type="",
                    thumb_status="not_attempted",
                    original_thumb_bytes=None,
                    uploaded_thumb_bytes=None,
                    post_cid=post_cid or None,
                    target_handle=handle,
                    target_did=did,
                    approved_text_sha256=str(approved_text_sha256).strip().lower(),
                    status="uncertain",
                    reason="post_outcome_uncertain_reconcile_before_retry",
                )
                if correction_mode:
                    _write_correction_receipt(
                        project_root=root,
                        edition_date=edition_date,
                        public_url=str(public_url),
                        post_text=text,
                        old_uri=str(correction_old_uri),
                        old_cid=str(correction_old_cid),
                        old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                        status="uncertain",
                        reason="post_outcome_uncertain_reconcile_before_retry",
                        new_uri=post_uri or None,
                        new_cid=post_cid or None,
                        card_contract=expected_card_contract,
                    )
                return {
                    **result,
                    "status": "uncertain",
                    "post_uri": None,
                    "reason": "post_outcome_uncertain_reconcile_before_retry",
                    "post_text": text,
                    "card_title": card_title,
                    "card_description": card_description,
                    "error_type": "InvalidCreateRecordResponse",
                    "error_message": "createRecord response did not contain an expected post URI and CID",
                }
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
                **verification_fields,
            }
        if exact_text_mode and expected_card_contract is not None:
            rkey = post_uri.rsplit("/", 1)[-1]
            readback_query = urlencode({"repo": did, "collection": "app.bsky.feed.post", "rkey": rkey})
            readback_request = request.Request(
                f"{BLUESKY_API_BASE}/com.atproto.repo.getRecord?{readback_query}",
                method="GET",
                headers={"Accept": "application/json", "Authorization": f"Bearer {access_jwt}"},
            )
            try:
                with request.urlopen(readback_request, timeout=20.0) as resp:
                    readback_body = resp.read().decode("utf-8")
                readback = json.loads(readback_body) if readback_body else {}
                validate_create_response({"uri": post_uri, "cid": post_cid}, expected_did=did)
                if not isinstance(readback, dict) or readback.get("uri") != post_uri or readback.get("cid") != post_cid:
                    raise CardContractError("post readback identity mismatch")
                validate_external_record(record=readback.get("value"), post_text=text, contract=expected_card_contract)
            except Exception as exc:  # noqa: BLE001 - a created-but-unverified post is uncertain.
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
                    post_cid=post_cid or None,
                    target_handle=handle,
                    target_did=did,
                    approved_text_sha256=str(approved_text_sha256).strip().lower(),
                    card_mode="external",
                    card_uri=expected_card_contract.edition_url,
                    thumb_cid=expected_card_contract.thumb_cid,
                    thumb_source_sha256=expected_card_contract.thumb_sha256,
                    link_facets_sha256=_private_facets_sha256(record_payload["record"]),
                    status="uncertain",
                    reason="post_readback_card_verification_failed",
                )
                if correction_mode:
                    _write_correction_receipt(
                        project_root=root,
                        edition_date=edition_date,
                        public_url=str(public_url),
                        post_text=text,
                        old_uri=str(correction_old_uri),
                        old_cid=str(correction_old_cid),
                        old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                        status="uncertain",
                        reason="post_readback_card_verification_failed",
                        new_uri=post_uri,
                        new_cid=post_cid or None,
                        card_contract=expected_card_contract,
                        link_facets_sha256=_private_facets_sha256(record_payload["record"]),
                    )
                return {
                    **result,
                    "status": "uncertain",
                    "post_uri": post_uri,
                    "reason": "post_outcome_uncertain_reconcile_before_retry",
                    "post_text": text,
                    "card_title": card_title,
                    "card_description": card_description,
                    "error_type": type(exc).__name__,
                    "error_message": "created record did not verify as the approved card-bearing post",
                }
            result["thumb_cid"] = expected_card_contract.thumb_cid
            result["link_facets_sha256"] = _private_facets_sha256(record_payload["record"])
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
            post_cid=post_cid or None,
            target_handle=handle,
            target_did=did,
            approved_text_sha256=str(approved_text_sha256).strip().lower() if exact_text_mode else None,
            card_mode="external" if expected_card_contract is not None else ("plain" if exact_text_mode else None),
            card_uri=expected_card_contract.edition_url if expected_card_contract is not None else None,
            thumb_cid=expected_card_contract.thumb_cid if expected_card_contract is not None else None,
            thumb_source_sha256=expected_card_contract.thumb_sha256 if expected_card_contract is not None else None,
            link_facets_sha256=_private_facets_sha256(record_payload["record"]) if expected_card_contract is not None else None,
        )
        if correction_mode:
            _write_correction_receipt(
                project_root=root,
                edition_date=edition_date,
                public_url=str(public_url),
                post_text=text,
                old_uri=str(correction_old_uri),
                old_cid=str(correction_old_cid),
                old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                status="created_verified_pending_old_delete",
                new_uri=post_uri,
                new_cid=post_cid or None,
                card_contract=expected_card_contract,
                link_facets_sha256=_private_facets_sha256(record_payload["record"]) if expected_card_contract is not None else None,
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
            "approved_text_sha256": str(approved_text_sha256).strip().lower() if exact_text_mode else None,
            "correction_mode": correction_mode,
            "correction_receipt_path": result.get("correction_receipt_path"),
            **verification_fields,
        }
    except error.HTTPError as exc:
        reason, err_type, err_message = _safe_http_error(exc, app_password)
        if exact_text_mode and "post_request_started" in locals() and post_request_started:
            _write_success_receipt(
                project_root=root,
                edition_date=edition_date,
                public_url=str(public_url),
                post_uri="",
                post_text=text,
                card_title=card_title,
                card_description=card_description,
                embed_type="",
                thumb_status="not_attempted",
                original_thumb_bytes=None,
                uploaded_thumb_bytes=None,
                target_handle=handle,
                target_did=did,
                approved_text_sha256=str(approved_text_sha256).strip().lower(),
                status="uncertain",
                reason="post_request_http_error_outcome_uncertain",
            )
            if correction_mode:
                _write_correction_receipt(
                    project_root=root,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_text=text,
                    old_uri=str(correction_old_uri),
                    old_cid=str(correction_old_cid),
                    old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                    status="uncertain",
                    reason="post_request_http_error_outcome_uncertain",
                    card_contract=expected_card_contract,
                )
            return {
                **result,
                "status": "uncertain",
                "reason": "post_outcome_uncertain_reconcile_before_retry",
                "post_text": text,
                "card_title": card_title,
                "card_description": card_description,
                "target_handle": handle,
                "target_did": did,
                "error_type": err_type,
                "error_message": err_message,
            }
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
            **verification_fields,
        }
    except Exception as exc:  # noqa: BLE001
        if exact_text_mode and "post_request_started" in locals() and post_request_started:
            _write_success_receipt(
                project_root=root,
                edition_date=edition_date,
                public_url=str(public_url),
                post_uri="",
                post_text=text,
                card_title=card_title,
                card_description=card_description,
                embed_type="",
                thumb_status="not_attempted",
                original_thumb_bytes=None,
                uploaded_thumb_bytes=None,
                target_handle=handle,
                target_did=did,
                approved_text_sha256=str(approved_text_sha256).strip().lower(),
                status="uncertain",
                reason="post_request_outcome_uncertain",
            )
            if correction_mode:
                _write_correction_receipt(
                    project_root=root,
                    edition_date=edition_date,
                    public_url=str(public_url),
                    post_text=text,
                    old_uri=str(correction_old_uri),
                    old_cid=str(correction_old_cid),
                    old_engagement=dict((correction_old_state or {}).get("engagement") or {}),
                    status="uncertain",
                    reason="post_request_outcome_uncertain",
                    card_contract=expected_card_contract,
                )
            return {
                **result,
                "status": "uncertain",
                "reason": "post_outcome_uncertain_reconcile_before_retry",
                "post_text": text,
                "card_title": card_title,
                "card_description": card_description,
                "target_handle": handle,
                "target_did": did,
            }
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
            **verification_fields,
        }


def delete_verified_gaza_card_correction(
    *,
    access_jwt: str,
    project_root: Path,
    edition_date: str,
    public_url: str,
    post_text: str,
    expected_did: str,
    old_uri: str,
    old_cid: str,
    new_uri: str,
    new_cid: str,
    expected_card_contract: ExternalCardContract,
) -> dict[str, Any]:
    """Delete only the exact old post after a verified card-bearing replacement.

    This is intentionally separate from create. The old record is retained
    until the new record is read back and the old URI/CID/zero-engagement state
    is checked again immediately before deletion.
    """
    receipt = _load_correction_receipt(project_root, edition_date)
    if not receipt or receipt.get("status") != "created_verified_pending_old_delete":
        return {"status": "blocked", "reason": "correction_receipt_not_ready_for_delete"}
    if (
        receipt.get("old_uri") != old_uri
        or receipt.get("old_cid") != old_cid
        or receipt.get("new_uri") != new_uri
        or receipt.get("new_cid") != new_cid
    ):
        return {"status": "blocked", "reason": "correction_receipt_binding_mismatch"}
    if not _exact_post_identity_valid(new_uri, new_cid, expected_did):
        return {"status": "blocked", "reason": "correction_new_identity_invalid"}
    new_rkey = _at_uri_rkey(new_uri, expected_did)
    if not new_rkey:
        return {"status": "blocked", "reason": "correction_new_uri_invalid"}
    try:
        new_query = urlencode({"repo": expected_did, "collection": "app.bsky.feed.post", "rkey": new_rkey})
        new_record = _auth_get_json(
            f"{BLUESKY_API_BASE}/com.atproto.repo.getRecord?{new_query}",
            access_jwt,
        )
        if new_record.get("uri") != new_uri or new_record.get("cid") != new_cid:
            return {"status": "blocked", "reason": "correction_new_readback_identity_mismatch"}
        validate_external_record(record=new_record.get("value"), post_text=post_text, contract=expected_card_contract)
    except Exception as exc:  # noqa: BLE001 - no deletion without new proof.
        return {"status": "uncertain", "reason": "correction_new_readback_failed", "error_type": type(exc).__name__}

    old_state = _read_exact_old_post_state(
        access_jwt=access_jwt,
        old_uri=old_uri,
        old_cid=old_cid,
        expected_did=expected_did,
        expected_text=post_text,
    )
    if old_state.get("status") != "verified":
        return {"status": old_state.get("status") or "blocked", "reason": old_state.get("reason") or "correction_old_record_not_deleteable"}

    delete_started = False
    try:
        old_rkey = _at_uri_rkey(old_uri, expected_did)
        if not old_rkey:
            return {"status": "blocked", "reason": "correction_old_uri_invalid"}
        delete_payload = {
            "repo": expected_did,
            "collection": "app.bsky.feed.post",
            "rkey": old_rkey,
            "swapRecord": old_cid,
        }
        delete_request = _build_auth_request(
            f"{BLUESKY_API_BASE}/com.atproto.repo.deleteRecord",
            delete_payload,
            access_jwt,
        )
        delete_started = True
        with request.urlopen(delete_request, timeout=20.0) as response:
            response.read()
        old_absence_url = f"{BLUESKY_API_BASE}/com.atproto.repo.getRecord?{urlencode({'repo': expected_did, 'collection': 'app.bsky.feed.post', 'rkey': old_rkey})}"
        try:
            _auth_get_json(old_absence_url, access_jwt)
        except error.HTTPError as exc:
            if not _is_exact_record_not_found(exc, old_absence_url):
                raise
        else:
            _write_correction_receipt(
                project_root=project_root,
                edition_date=edition_date,
                public_url=public_url,
                post_text=post_text,
                old_uri=old_uri,
                old_cid=old_cid,
                old_engagement=dict(old_state.get("engagement") or {}),
                status="uncertain",
                reason="correction_old_record_still_present",
                new_uri=new_uri,
                new_cid=new_cid,
                card_contract=expected_card_contract,
                extra={"old_delete_status": "old_record_still_present", "new_readback_status": "verified"},
            )
            return {"status": "uncertain", "reason": "correction_old_record_still_present"}
        _write_correction_receipt(
            project_root=project_root,
            edition_date=edition_date,
            public_url=public_url,
            post_text=post_text,
            old_uri=old_uri,
            old_cid=old_cid,
            old_engagement=dict(old_state.get("engagement") or {}),
            status="completed",
            new_uri=new_uri,
            new_cid=new_cid,
            card_contract=expected_card_contract,
            extra={"old_delete_status": "verified_absent", "new_readback_status": "verified"},
        )
        return {"status": "completed", "old_uri": old_uri, "new_uri": new_uri, "new_cid": new_cid}
    except Exception as exc:  # noqa: BLE001 - deletion outcome is uncertain after request start.
        if delete_started:
            _write_correction_receipt(
                project_root=project_root,
                edition_date=edition_date,
                public_url=public_url,
                post_text=post_text,
                old_uri=old_uri,
                old_cid=old_cid,
                old_engagement=dict(old_state.get("engagement") or {}),
                status="uncertain",
                reason="correction_old_delete_outcome_uncertain",
                new_uri=new_uri,
                new_cid=new_cid,
                card_contract=expected_card_contract,
            )
            return {"status": "uncertain", "reason": "correction_old_delete_outcome_uncertain", "error_type": type(exc).__name__}
        return {"status": "failure", "reason": str(exc) or "correction_old_delete_failed", "error_type": type(exc).__name__}


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
    allow_archival_bluesky_post: bool = False,
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
        "archival_override": bool(allow_archival_bluesky_post),
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
        preview = build_food_line_bluesky_preview(root, edition_date)
        preview_post_text = str(preview["post_text"]).strip()
        if not preview_post_text:
            result["reason"] = "post_text_unavailable"
        else:
            result["post_text"] = preview_post_text
            if allow_archival_bluesky_post:
                result["post_text"] = f"[ARCHIVAL / RETROSPECTIVE] {preview_post_text}"
            result["card_title"] = str(preview["card_title"])
            result["card_description"] = str(preview["card_description"])
            result["image_path"] = str(preview["card_image_path"])
            result["edition_date_verified"] = True
            if len(str(result["post_text"] or "")) > BLUESKY_MAX_POST_LENGTH:
                result["status"] = "blocked"
                result["reason"] = "post_text_too_long"
                result["error_message"] = f"Food Line Bluesky post text exceeds {BLUESKY_MAX_POST_LENGTH} characters."
                state_payload = {
                    "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                    "edition_date": edition_date,
                    "public_url": str(public_url),
                    "post_text": result["post_text"],
                    "card_title": result["card_title"],
                    "card_description": result["card_description"],
                    "image_path": result["image_path"],
                    "image_alt": FOOD_LINE_SOCIAL_IMAGE_ALT,
                    "status": "blocked",
                    "skip_reason": "post_text_too_long",
                    "dry_run": bool(dry_run),
                    "forced_post": bool(force_post),
                    "post_uri": None,
                    "post_cid": None,
                    "embed_type": None,
                    "thumb_status": "not_attempted",
                    "posted_at": None,
                }
                if allow_publish and not dry_run:
                    _write_food_line_post_state(root, edition_date, state_payload)
                return result
            if allow_publish and not dry_run:
                approval = verify_approval(root, edition_date, allow_archival=allow_archival_bluesky_post)
                result["approval_status"] = approval
                if not approval.get("ok"):
                    result["reason"] = approval.get("reason")
                    return result
            receipt = _load_post_state_for_same_public_url(root, FOOD_LINE_DISPATCH_SLUG, edition_date, str(public_url))
            if receipt and not force_post:
                state_payload = {
                    "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                    "edition_date": edition_date,
                    "public_url": str(public_url),
                    "post_text": result["post_text"],
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
                    "post_text": result["post_text"],
                    "card_title": result["card_title"],
                    "card_description": result["card_description"],
                    "image_path": result["image_path"],
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
                return {
                    **result,
                    "status": "skipped",
                    "reason": "dry_run",
                    "image_path": result["image_path"],
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
        if allow_publish and not dry_run:
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
                    thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes, image_path = _upload_food_line_card_thumb(
                        access_jwt,
                        root,
                        str(result["image_path"] or ""),
                    )
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
                            "image_path": str(image_path) if image_path else None,
                        }
                    )
                    state_payload = {
                        "dispatch_slug": FOOD_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else None,
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
    if allow_publish and not dry_run:
        _write_food_line_post_state(root, edition_date, state_payload)
    return result


