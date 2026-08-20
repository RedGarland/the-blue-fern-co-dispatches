from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib import error, request

from bluefern_dispatches.bluesky_post import (
    BLUESKY_API_BASE,
    BLUESKY_BLOB_MAX_BYTES,
    BLUESKY_COMPRESS_TARGET_BYTES,
    _build_auth_request,
    _compress_thumb_to_jpeg,
    _guess_image_mime,
    _post_json,
    _safe_error,
    _safe_http_error,
    _upload_blob,
)
from bluefern_dispatches.care_line_release_render import load_approved_release
from bluefern_dispatches.generator import BASE_URL


CARE_LINE_DISPATCH_SLUG = "care-line"
CARE_LINE_SOCIAL_IMAGE_PATH = "assets/care-line-dispatch-social.png"
CARE_LINE_SOCIAL_IMAGE_URL = f"{BASE_URL}/care-line/assets/care-line-dispatch-social.png"
CARE_LINE_SOCIAL_IMAGE_ALT = (
    "The Care Line Dispatch social card from The Blue Fern Co., with a healthcare-access motif "
    "and the subtitle Healthcare access dispatch."
)
CARE_LINE_BLUESKY_POST_STATE_FILENAME = "bluesky_post.json"
PREVIEW_DIR_NAME = "bluesky-preview"
PREVIEW_FILENAME = "care-line-bluesky-preview.json"
PREVIEW_HTML_FILENAME = "care-line-bluesky-preview.html"


def _human_date(edition_date: str) -> str:
    year, month, day = edition_date.split("-")
    month_name = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }[month]
    return f"{month_name} {int(day)}, {year}"


def public_url_for_edition(edition_date: str) -> str:
    return f"{BASE_URL}/care-line/editions/{edition_date}/"


def social_image_path(project_root: Path) -> Path:
    return project_root / CARE_LINE_SOCIAL_IMAGE_PATH


def social_image_sha256(project_root: Path) -> str:
    return hashlib.sha256(social_image_path(project_root).read_bytes()).hexdigest()


def _card_title(edition_date: str) -> str:
    return f"Care Line — {_human_date(edition_date)}"


def _card_description() -> str:
    return "Read the source-backed U.S. healthcare access dispatch from The Blue Fern Co."


def deterministic_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def _post_text(
    manifest: dict[str, Any],
    bundle_items: tuple[dict[str, Any], ...],
    proposal_summary: str = "",
) -> str:
    title = "Care Line Dispatch"
    edition_date = str(manifest.get("edition_date") or "").strip()
    lead_summary = str(
        proposal_summary
        or manifest.get("edition_summary")
        or manifest.get("public_summary")
        or manifest.get("public_archive_subtitle")
        or manifest.get("source_adequacy_label")
        or ""
    ).strip()
    if not lead_summary and bundle_items:
        lead_summary = str(bundle_items[0].get("bounded_public_summary") or bundle_items[0].get("approved_public_claim") or "").strip()
    body = lead_summary.rstrip(".") + "." if lead_summary else ""
    secondary_summary = ""
    for item in bundle_items[1:]:
        summary = str(item.get("bounded_public_summary") or item.get("approved_public_claim") or "").strip()
        if summary and summary != lead_summary:
            secondary_summary = summary
            break
    if secondary_summary:
        body = f"{body} Also covered: {secondary_summary.rstrip('.')}.".strip()
    if body:
        return f"{title} — {_human_date(edition_date)}\n\n{body}"
    return f"{title} — {_human_date(edition_date)}"


def _preview_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / "care-line" / "review" / PREVIEW_DIR_NAME / edition_date


def _load_manifest(project_root: Path, edition_date: str) -> dict[str, Any]:
    manifest_path = project_root / "output" / "site" / "care-line" / "editions" / edition_date / "edition_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {manifest_path}")
    return payload


def build_care_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest = _load_manifest(project_root, edition_date)
    bundle = load_approved_release(project_root, edition_date)
    if bundle is None:
        raise ValueError("Care Line edition is not release-ready")
    public_url = str(manifest.get("public_url") or public_url_for_edition(edition_date)).strip()
    post_text = _post_text(manifest, bundle.approved_items, str(bundle.proposal.get("edition_summary") or "").strip())
    card_title = _card_title(edition_date)
    card_description = _card_description()
    image_path = project_root / CARE_LINE_SOCIAL_IMAGE_PATH
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
    payload = {
        "schema_version": 1,
        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": public_url,
        "post_text": post_text,
        "card_title": card_title,
        "card_description": card_description,
        "card_image_path": CARE_LINE_SOCIAL_IMAGE_PATH,
        "card_image_sha256": image_hash,
        "embed": {
            "uri": public_url,
            "title": card_title,
            "description": card_description,
        },
        "source_provenance": {
            "proposal_sha256": bundle.proposal_sha256,
            "review_sha256": bundle.review_snapshot_sha256,
            "review_item_ids": [
                item.get("review_item_id")
                for item in bundle.approved_items
                if isinstance(item, dict) and item.get("review_item_id")
            ],
        },
    }
    payload["content_sha256"] = hashlib.sha256(deterministic_json(payload).encode("utf-8")).hexdigest()
    return payload


def write_care_line_bluesky_preview(project_root: Path, edition_date: str) -> dict[str, Any]:
    preview = build_care_line_bluesky_preview(project_root, edition_date)
    preview_dir = _preview_path(project_root, edition_date)
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / PREVIEW_FILENAME
    html_path = preview_dir / PREVIEW_HTML_FILENAME
    json_path.write_text(deterministic_json(preview) + "\n", encoding="utf-8")
    html_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '  <meta charset="utf-8">',
                f"  <title>Care Line Bluesky Preview - {edition_date}</title>",
                f'  <meta property="og:title" content="{escape(str(preview["card_title"]))}">',
                f'  <meta property="og:description" content="{escape(str(preview["card_description"]))}">',
                "</head>",
                "<body>",
                "  <h1>Care Line Bluesky Preview</h1>",
                f"  <p><strong>Edition:</strong> {escape(edition_date)}</p>",
                f"  <p><strong>Post text:</strong> {escape(str(preview['post_text'])).replace(chr(10), '<br>')}</p>",
                f"  <p><strong>Embed URI:</strong> {escape(str(preview['embed']['uri']))}</p>",
                f"  <p><strong>Card image:</strong> {escape(str(preview['card_image_path']))}</p>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )
    return {"preview": preview, "json_path": json_path, "html_path": html_path}


def _receipt_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / CARE_LINE_DISPATCH_SLUG / "editions" / edition_date / CARE_LINE_BLUESKY_POST_STATE_FILENAME


def _load_post_state_for_same_public_url(project_root: Path, edition_date: str, public_url: str) -> dict[str, Any] | None:
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


def _write_post_state(project_root: Path, edition_date: str, payload: dict[str, Any]) -> Path:
    path = _receipt_path(project_root, edition_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _thumbnail_candidates(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "assets" / "care-line-dispatch-social.png",
        project_root / "assets" / "care-line-logo.png",
        project_root / "assets" / "care-line-mark.png",
        project_root / "assets" / "bluefern.png",
    )


def _upload_care_line_card_thumb(
    access_jwt: str,
    project_root: Path,
) -> tuple[dict[str, Any] | None, str, bool, int | None, int | None, Path | None]:
    for path in _thumbnail_candidates(project_root):
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


def maybe_post_care_line_dispatch_to_bluesky(
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
        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
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
    state_path = _receipt_path(root, edition_date)
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
        try:
            preview = build_care_line_bluesky_preview(root, edition_date)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "blocked"
            result["reason"] = _safe_error(str(exc), None)
        else:
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
                receipt = _load_post_state_for_same_public_url(root, edition_date, str(public_url))
                if receipt and not force_post:
                    return {
                        **result,
                        "status": "skipped",
                        "reason": "skipped_existing_receipt",
                        "post_uri": receipt.get("post_uri"),
                        "post_cid": receipt.get("post_cid"),
                        "card_title": receipt.get("card_title") or result["card_title"],
                        "card_description": receipt.get("card_description") or result["card_description"],
                        "image_path": receipt.get("image_path"),
                        "image_alt": receipt.get("image_alt") or CARE_LINE_SOCIAL_IMAGE_ALT,
                        "thumb_status": receipt.get("thumb_status") or "not_attempted",
                        "compressed_thumb": False,
                        "original_thumb_bytes": receipt.get("original_thumb_bytes"),
                        "uploaded_thumb_bytes": receipt.get("uploaded_thumb_bytes"),
                        "state_path": str(state_path),
                    }
                if dry_run or not allow_publish:
                    return {
                        **result,
                        "status": "skipped",
                        "reason": "dry_run",
                        "thumb_status": "not_attempted",
                        "state_path": str(state_path),
                    }
    if result["reason"] is not None:
        state_payload = {
            "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
            "edition_date": edition_date,
            "public_url": str(public_url or ""),
            "post_text": result["post_text"],
            "card_title": result["card_title"],
            "card_description": result["card_description"],
            "image_path": None,
            "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
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
            _write_post_state(root, edition_date, state_payload)
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
                    thumb_blob, thumb_status, compressed_thumb, original_thumb_bytes, uploaded_thumb_bytes, image_path = _upload_care_line_card_thumb(access_jwt, root)
                except Exception as exc:  # noqa: BLE001
                    thumb_blob = None
                    thumb_status = "upload_failed"
                    result["error_type"] = exc.__class__.__name__
                    result["error_message"] = str(exc)
                if not thumb_blob and not allow_text_only:
                    result["status"] = "blocked"
                    result["reason"] = "card_image_unavailable"
                    state_payload = {
                        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else None,
                        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
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
                    _write_post_state(root, edition_date, state_payload)
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
                            "image_path": str(image_path) if image_path else (CARE_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        }
                    )
                    state_payload = {
                        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
                        "edition_date": edition_date,
                        "public_url": str(public_url),
                        "post_text": result["post_text"],
                        "card_title": result["card_title"],
                        "card_description": result["card_description"],
                        "image_path": str(image_path) if image_path else (CARE_LINE_SOCIAL_IMAGE_PATH if thumb_blob else None),
                        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
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
                    _write_post_state(root, edition_date, state_payload)
                    return result
        except error.HTTPError as exc:
            result["status"] = "failure"
            result["reason"], result["error_type"], result["error_message"] = _safe_http_error(exc, app_password)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failure"
            result["reason"] = _safe_error(str(exc), app_password)

    state_payload = {
        "dispatch_slug": CARE_LINE_DISPATCH_SLUG,
        "edition_date": edition_date,
        "public_url": str(public_url or ""),
        "post_text": result["post_text"],
        "card_title": result["card_title"],
        "card_description": result["card_description"],
        "image_path": result["image_path"],
        "image_alt": CARE_LINE_SOCIAL_IMAGE_ALT,
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
        _write_post_state(root, edition_date, state_payload)
    return result
