"""Hash-bound Care Line social approval gate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APPROVAL_SCHEMA_VERSION = 1
APPROVAL_TYPE = "care_line_bluesky_private_preview"
APPROVAL_FILENAME = "bluesky_social_approval.json"
CARE_LINE_POSTING_MODEL = "care_line_public_edition_social_v1"


def approval_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / "care-line" / "editions" / edition_date / APPROVAL_FILENAME


def post_text_sha256(post_text: str) -> str:
    return hashlib.sha256(post_text.encode("utf-8")).hexdigest()


def preview_content_hash(
    *,
    edition_date: str,
    post_text: str,
    public_url: str,
    card_title: str,
    card_description: str,
    card_image_path: str,
    card_image_sha256: str,
) -> str:
    payload = {
        "approval_type": APPROVAL_TYPE,
        "posting_model": CARE_LINE_POSTING_MODEL,
        "edition_date": edition_date,
        "post_text_sha256": post_text_sha256(post_text),
        "public_url": public_url,
        "card_title": card_title,
        "card_description": card_description,
        "card_image_path": card_image_path,
        "card_image_sha256": card_image_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_pending_approval(project_root: Path, edition_date: str) -> dict[str, Any]:
    from bluefern_dispatches.care_line_bluesky import build_care_line_bluesky_preview

    preview = build_care_line_bluesky_preview(project_root, edition_date, refresh_card=False)
    post_text = str(preview.get("post_text") or "")
    public_url = str(preview.get("public_url") or "")
    card_title = str(preview.get("card_title") or "")
    card_description = str(preview.get("card_description") or "")
    card_image_path = str(preview.get("card_image_path") or "")
    card_image_sha256 = str(preview.get("card_image_sha256") or "")
    return {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approval_type": APPROVAL_TYPE,
        "posting_model": CARE_LINE_POSTING_MODEL,
        "dispatch_slug": "care-line",
        "edition_date": edition_date,
        "public_url": public_url,
        "post_text": post_text,
        "post_text_sha256": post_text_sha256(post_text),
        "card_title": card_title,
        "card_description": card_description,
        "card_image_path": card_image_path,
        "card_image_sha256": card_image_sha256,
        "preview_content_hash": preview_content_hash(
            edition_date=edition_date,
            post_text=post_text,
            public_url=public_url,
            card_title=card_title,
            card_description=card_description,
            card_image_path=card_image_path,
            card_image_sha256=card_image_sha256,
        ),
        "approval_status": "pending_review",
        "approved": False,
        "social_authorized": False,
        "approved_at": None,
        "approved_by": None,
        "approval_note": None,
    }


def write_approval(project_root: Path, payload: dict[str, Any]) -> Path:
    path = approval_path(project_root, str(payload["edition_date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def verify_approval(project_root: Path, edition_date: str | None) -> dict[str, Any]:
    if not str(edition_date or "").strip():
        return {"ok": False, "reason": "edition_date_missing", "edition_date": edition_date}
    try:
        expected = build_pending_approval(project_root, str(edition_date))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "preview_unavailable", "edition_date": edition_date, "error": str(exc)}
    path = approval_path(project_root, str(edition_date))
    approval = _load_json(path)
    if approval is None:
        return {"ok": False, "reason": "approval_missing", "approval_path": str(path), "edition_date": edition_date}
    reason = None
    if approval.get("approval_type") != APPROVAL_TYPE:
        reason = "approval_type_mismatch"
    elif approval.get("posting_model") != CARE_LINE_POSTING_MODEL:
        reason = "posting_model_mismatch"
    elif approval.get("approval_status") != "approved" or not bool(approval.get("approved")):
        reason = "approval_not_granted"
    elif not bool(approval.get("social_authorized")):
        reason = "social_not_authorized"
    elif str(approval.get("public_url") or "") != expected["public_url"]:
        reason = "public_url_mismatch"
    elif str(approval.get("post_text_sha256") or "") != expected["post_text_sha256"]:
        reason = "post_text_hash_mismatch"
    elif str(approval.get("card_title") or "") != expected["card_title"]:
        reason = "card_title_mismatch"
    elif str(approval.get("card_description") or "") != expected["card_description"]:
        reason = "card_description_mismatch"
    elif str(approval.get("card_image_path") or "") != expected["card_image_path"]:
        reason = "card_image_path_mismatch"
    elif str(approval.get("card_image_sha256") or "") != expected["card_image_sha256"]:
        reason = "card_image_hash_mismatch"
    elif str(approval.get("preview_content_hash") or "") != expected["preview_content_hash"]:
        reason = "preview_hash_mismatch"
    return {
        **expected,
        "ok": reason is None,
        "reason": reason,
        "approval_path": str(path),
        "approval": approval,
    }


def approve_preview(project_root: Path, edition_date: str, approved_by: str, approval_note: str | None = None) -> dict[str, Any]:
    if not str(approved_by or "").strip():
        raise ValueError("approved_by is required")
    payload = build_pending_approval(project_root, edition_date)
    payload.update(
        {
            "approval_status": "approved",
            "approved": True,
            "social_authorized": True,
            "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "approved_by": approved_by.strip(),
            "approval_note": approval_note,
        }
    )
    path = write_approval(project_root, payload)
    return {"ok": True, "reason": None, "approval_path": str(path), "approval": payload}


def prepare_post(project_root: Path, edition_date: str) -> dict[str, Any]:
    verification = verify_approval(project_root, edition_date)
    result = dict(verification)
    result["operation"] = "prepare-care-line-bluesky-post"
    result["sent"] = False
    if verification.get("ok"):
        result["post_text"] = verification.get("post_text")
        result["image_path"] = verification.get("card_image_path")
    return result
