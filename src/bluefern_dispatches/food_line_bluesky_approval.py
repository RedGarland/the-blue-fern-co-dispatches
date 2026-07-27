"""Explicit, hash-bound approval for Food Line edition posts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FOOD_LINE_POSTING_MODEL = "food_line_daily_edition_v1"
FOOD_LINE_SOCIAL_IMAGE_PATH = "assets/food-line-dispatch-social.png"
FOOD_LINE_SOCIAL_IMAGE_FILENAME = "food-line-dispatch-social.png"
APPROVAL_FILENAME = "bluesky_approval.json"
BASE_URL = "https://dispatches.thebluefernco.com"


def public_url_for_edition(edition_date: str) -> str:
    return f"{BASE_URL}/food-line/editions/{edition_date}/"


def approval_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "data" / "dispatches" / "food-line" / "editions" / edition_date / APPROVAL_FILENAME


def _manifest_path(project_root: Path, edition_date: str) -> Path:
    return project_root / "output" / "site" / "food-line" / "editions" / edition_date / "edition_manifest.json"


def social_image_path(project_root: Path) -> Path:
    return project_root / "assets" / FOOD_LINE_SOCIAL_IMAGE_FILENAME


def social_image_sha256(project_root: Path) -> str:
    return hashlib.sha256(social_image_path(project_root).read_bytes()).hexdigest()


def _content_payload(*, edition_date: str, draft_text: str, public_url: str, social_image_hash: str) -> dict[str, str]:
    return {
        "posting_model": FOOD_LINE_POSTING_MODEL,
        "edition_date": edition_date,
        "draft_text": draft_text,
        "public_url": public_url,
        "social_image_sha256": social_image_hash,
    }


def draft_content_hash(*, edition_date: str, draft_text: str, public_url: str, social_image_hash: str) -> str:
    canonical = json.dumps(
        _content_payload(
            edition_date=edition_date,
            draft_text=draft_text,
            public_url=public_url,
            social_image_hash=social_image_hash,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _current_draft(project_root: Path, edition_date: str) -> dict[str, Any]:
    manifest = _load_json(_manifest_path(project_root, edition_date))
    if not manifest:
        return {"ok": False, "reason": "edition_not_bluesky_ready", "manifest": {}}
    public_url = str(manifest.get("public_url") or public_url_for_edition(edition_date)).strip()
    draft_text = str(manifest.get("bluesky_post_text") or "").strip()
    public_signal_count = int(manifest.get("public_signal_count") or 0)
    if public_signal_count <= 0 or not bool(manifest.get("public_rendered")) or str(manifest.get("edition_mode") or "") == "no_current_update":
        return {"ok": False, "reason": "no_public_signals", "manifest": manifest, "public_url": public_url, "draft_text": draft_text}
    if str(manifest.get("validation_status") or "") != "ok" or not bool(manifest.get("bluesky_post_ready")):
        return {"ok": False, "reason": "edition_not_bluesky_ready", "manifest": manifest, "public_url": public_url, "draft_text": draft_text}
    if not draft_text:
        return {"ok": False, "reason": "edition_not_bluesky_ready", "manifest": manifest, "public_url": public_url, "draft_text": draft_text}
    return {"ok": True, "reason": None, "manifest": manifest, "public_url": public_url, "draft_text": draft_text}


def build_pending_approval(project_root: Path, edition_date: str) -> dict[str, Any]:
    current = _current_draft(project_root, edition_date)
    if not current.get("draft_text"):
        raise ValueError(str(current.get("reason") or "edition_not_bluesky_ready"))
    image_hash = social_image_sha256(project_root)
    public_url = str(current["public_url"])
    draft_text = str(current["draft_text"])
    return {
        "schema_version": 1,
        "edition_date": edition_date,
        "public_url": public_url,
        "draft_text": draft_text,
        "draft_content_hash": draft_content_hash(
            edition_date=edition_date,
            draft_text=draft_text,
            public_url=public_url,
            social_image_hash=image_hash,
        ),
        "social_image_path": FOOD_LINE_SOCIAL_IMAGE_PATH,
        "social_image_sha256": image_hash,
        "posting_model": FOOD_LINE_POSTING_MODEL,
        "approved": False,
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


def verify_approval(project_root: Path, edition_date: str) -> dict[str, Any]:
    current = _current_draft(project_root, edition_date)
    if not current.get("ok"):
        return {"ok": False, "reason": current.get("reason"), "edition_date": edition_date, "public_url": current.get("public_url")}
    path = approval_path(project_root, edition_date)
    approval = _load_json(path)
    if approval is None:
        return {"ok": False, "reason": "approval_missing", "approval_path": str(path), "edition_date": edition_date}
    public_url = str(current["public_url"])
    draft_text = str(current["draft_text"])
    image_hash = social_image_sha256(project_root)
    expected_hash = draft_content_hash(
        edition_date=edition_date,
        draft_text=draft_text,
        public_url=public_url,
        social_image_hash=image_hash,
    )
    if not bool(approval.get("approved")):
        reason = "approval_not_granted"
    elif str(approval.get("public_url") or "") != public_url:
        reason = "public_url_mismatch"
    elif str(approval.get("social_image_sha256") or "") != image_hash:
        reason = "social_image_hash_mismatch"
    elif str(approval.get("draft_text") or "") != draft_text or str(approval.get("draft_content_hash") or "") != expected_hash:
        reason = "draft_hash_mismatch"
    elif str(approval.get("posting_model") or "") != FOOD_LINE_POSTING_MODEL:
        reason = "draft_hash_mismatch"
    else:
        reason = None
    return {
        "ok": reason is None,
        "reason": reason,
        "approval_path": str(path),
        "approval": approval,
        "edition_date": edition_date,
        "public_url": public_url,
        "draft_text": draft_text,
        "draft_content_hash": expected_hash,
        "social_image_sha256": image_hash,
        "social_image_path": FOOD_LINE_SOCIAL_IMAGE_PATH,
        "posting_model": FOOD_LINE_POSTING_MODEL,
    }


def approve_draft(project_root: Path, edition_date: str, approved_by: str, approval_note: str | None = None) -> dict[str, Any]:
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    current = _current_draft(project_root, edition_date)
    if not current.get("ok"):
        return {"ok": False, "reason": current.get("reason"), "edition_date": edition_date}
    payload = build_pending_approval(project_root, edition_date)
    payload.update(
        {
            "approved": True,
            "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "approved_by": approved_by.strip(),
            "approval_note": approval_note,
        }
    )
    path = write_approval(project_root, payload)
    return {"ok": True, "reason": None, "approval_path": str(path), "approval": payload}


def revoke_approval(project_root: Path, edition_date: str, approval_note: str | None = None) -> dict[str, Any]:
    payload = build_pending_approval(project_root, edition_date)
    payload["approval_note"] = approval_note
    path = write_approval(project_root, payload)
    return {"ok": True, "reason": "approval_revoked", "approval_path": str(path), "approval": payload}


def inspect_draft(project_root: Path, edition_date: str) -> dict[str, Any]:
    current = _current_draft(project_root, edition_date)
    result = {
        "ok": bool(current.get("draft_text")),
        "edition_date": edition_date,
        "public_url": current.get("public_url") or public_url_for_edition(edition_date),
        "draft_text": current.get("draft_text"),
        "draft_length": len(str(current.get("draft_text") or "")),
        "edition_status": current.get("reason") or "ready",
        "social_image_path": FOOD_LINE_SOCIAL_IMAGE_PATH,
        "social_image_sha256": social_image_sha256(project_root) if social_image_path(project_root).exists() else None,
        "approval": verify_approval(project_root, edition_date),
    }
    return result


def prepare_post(project_root: Path, edition_date: str) -> dict[str, Any]:
    verification = verify_approval(project_root, edition_date)
    result = dict(verification)
    result["operation"] = "prepare-bluesky-post"
    result["sent"] = False
    result["image_path"] = verification.get("social_image_path")
    return result
