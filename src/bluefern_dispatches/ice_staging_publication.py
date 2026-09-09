from __future__ import annotations

import html
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ice_dispatch import generate_ice_edition_candidate

DISPATCH = "ice"
NO_MUTATION_DECISIONS = {
    "NO_PUBLICATION_NEEDED",
    "INSUFFICIENT_VERIFIED_EVIDENCE",
    "COLLECTION_FAILED",
}
ALLOWED_COLLECTION_STATES = {"healthy", "degraded", "failed", "collection_failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, stable_json(payload))


def _edition_href(date: str) -> str:
    return f"editions/{date}/"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _plain_date_key(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)[:8]


def _entry_for_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    manifest = candidate["manifest"]
    items = candidate.get("items") or []
    edition_date = manifest["edition_date"]
    return {
        "edition_date": edition_date,
        "title": f"ICE Dispatch - {edition_date}",
        "href": _edition_href(edition_date),
        "canonical_path": f"/ice/editions/{edition_date}/",
        "event_count": len(items),
        "source_count": sum(int(item.get("source_count") or 0) for item in items),
        "highest_severity": next((item["severity"] for item in items), "none"),
        "editorial_decision": manifest["editorial_decision"],
        "collection_state": manifest["collection_state"],
        "guid": f"bluefern-ice-{edition_date}",
        "non_public": True,
    }


def _merged_entries(existing: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    by_date = {str(item["edition_date"]): dict(item) for item in existing}
    by_date[str(entry["edition_date"])] = dict(entry)
    return [by_date[date] for date in sorted(by_date, key=_plain_date_key, reverse=True)]


def _render_index(entries: list[dict[str, Any]]) -> str:
    latest = entries[0] if entries else None
    rows = "\n".join(
        f'<li><a href="{html.escape(entry["href"], quote=True)}">{html.escape(entry["edition_date"])}</a> '
        f'({html.escape(str(entry["event_count"]))} events)</li>'
        for entry in entries
    )
    latest_text = (
        f'<p class="latest">Latest staging edition: <a href="{html.escape(latest["href"], quote=True)}">'
        f'{html.escape(latest["edition_date"])}</a></p>'
        if latest
        else '<p class="latest">No staging editions.</p>'
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>ICE Dispatch Staging</title></head><body data-bluefern-staging=\"true\">"
        "<main><h1>ICE Dispatch Staging</h1>"
        "<p><strong>STAGING ONLY - DO NOT PUBLISH</strong></p>"
        f"{latest_text}<ul>{rows}</ul>"
        '<p><a href="archive.html">Archive</a> <a href="rss.xml">RSS</a></p>'
        "</main></body></html>\n"
    )


def _render_archive(entries: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f'<li data-edition-date="{html.escape(entry["edition_date"], quote=True)}">'
        f'<a href="{html.escape(entry["href"], quote=True)}">{html.escape(entry["title"])}</a> '
        f'- {html.escape(str(entry["event_count"]))} events</li>'
        for entry in entries
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>ICE Dispatch Staging Archive</title></head>"
        "<body data-bluefern-staging=\"true\"><main><h1>ICE Dispatch Staging Archive</h1>"
        f"<ul>{rows}</ul></main></body></html>\n"
    )


def _render_rss(entries: list[dict[str, Any]]) -> str:
    items = "\n".join(
        "<item>"
        f"<title>{html.escape(entry['title'])}</title>"
        f"<guid isPermaLink=\"false\">{html.escape(entry['guid'])}</guid>"
        f"<link>{html.escape(entry['canonical_path'])}</link>"
        f"<description>{html.escape(str(entry['event_count']))} reviewed ICE event(s)</description>"
        "</item>"
        for entry in entries
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<rss version=\"2.0\"><channel><title>ICE Dispatch Staging RSS</title>"
        f"{items}</channel></rss>\n"
    )


def _rewrite_candidate_links_for_staging(html_text: str, edition_date: str) -> str:
    return (
        html_text.replace(f"https://dispatches.thebluefernco.com/ice/editions/{edition_date}/", f"editions/{edition_date}/")
        + f'\n<p><a href="map_payload.json">Map payload</a></p>\n'
    )


def _validate_staging_links(ice_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(ice_root.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'href="([^"]+)"', text):
            href = html.unescape(match.group(1))
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            if href.startswith("/ice/"):
                errors.append(f"{path.relative_to(ice_root).as_posix()} points to live-style {href}")
                continue
            target = (path.parent / href.split("#", 1)[0]).resolve()
            if href.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{path.relative_to(ice_root).as_posix()} missing {href}")
    return errors


@dataclass(frozen=True)
class StageResult:
    status: str
    receipt_path: Path
    receipt: dict[str, Any]
    changed_paths: tuple[str, ...]
    public_side_effects: bool = False


def stage_ice_publication(
    reviewed_records: Iterable[dict[str, Any]],
    *,
    edition_date: str,
    collection_state: str,
    staging_root: Path,
    generated_at: str | None = None,
    source_commit: str | None = None,
    decision_override: str | None = None,
    simulate_failure_after_candidate: bool = False,
) -> StageResult:
    generated = generated_at or utc_now()
    collection = collection_state.strip().lower()
    if collection not in ALLOWED_COLLECTION_STATES:
        raise ValueError(f"unsupported ICE collection state for staging: {collection_state}")

    staging_root.mkdir(parents=True, exist_ok=True)
    receipts_root = staging_root / "_receipts" / DISPATCH
    receipts_root.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_root / f"{edition_date}-{generated.replace(':', '').replace('-', '')}.json"
    ice_root = staging_root / DISPATCH
    before_hashes = tree_hashes(ice_root)

    candidate = generate_ice_edition_candidate(
        reviewed_records,
        edition_date=edition_date,
        collection_state=collection,
        generated_at=generated,
    )
    editorial_decision = decision_override or candidate["manifest"]["editorial_decision"]
    effective_decision = "COLLECTION_FAILED" if collection in {"failed", "collection_failed"} else editorial_decision
    should_stage = effective_decision == "ELIGIBLE_FOR_EDITION"

    receipt = {
        "schema_version": "ice_staging_publication_receipt_v1",
        "run_id": f"ice-staging-{edition_date}-{sha256_bytes(stable_json(candidate['manifest']).encode('utf-8'))[:12]}",
        "edition_date": edition_date,
        "editorial_decision": editorial_decision,
        "effective_decision": effective_decision,
        "collection_state": collection,
        "event_count": len(candidate.get("items") or []),
        "source_count": sum(int(item.get("source_count") or 0) for item in candidate.get("items") or []),
        "archive_changed": False,
        "rss_changed": False,
        "index_changed": False,
        "map_payload_changed": False,
        "staging_path": str(staging_root),
        "public_side_effects": False,
        "source_commit": source_commit,
        "generated_at": generated,
        "status": "staged" if should_stage else "no_op",
        "reason": effective_decision,
    }

    if not should_stage:
        _write_json(receipt_path, receipt)
        return StageResult("no_op", receipt_path, receipt, (), False)

    if simulate_failure_after_candidate:
        receipt["status"] = "failed_before_promote"
        receipt["reason"] = "simulated_failure_after_candidate"
        _write_json(receipt_path, receipt)
        if before_hashes != tree_hashes(ice_root):
            raise RuntimeError("staging tree mutated before atomic promote")
        return StageResult("failed_before_promote", receipt_path, receipt, (), False)

    temp_root = staging_root / f".{DISPATCH}-staging-{os.getpid()}-{sha256_bytes(generated.encode())[:8]}"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    if ice_root.exists():
        shutil.copytree(ice_root, temp_root)
    else:
        temp_root.mkdir(parents=True)

    try:
        existing_entries = _load_json(temp_root / "archive_entries.json", [])
        if not isinstance(existing_entries, list):
            raise ValueError("archive_entries.json must contain a list")
        entries = _merged_entries(existing_entries, _entry_for_candidate(candidate))

        edition_dir = temp_root / "editions" / edition_date
        _write(edition_dir / "index.html", _rewrite_candidate_links_for_staging(candidate["html"], edition_date))
        _write_json(edition_dir / "map_payload.json", candidate["map_payload"])
        _write_json(edition_dir / "manifest.json", candidate["manifest"])
        _write_json(temp_root / "archive_entries.json", entries)
        _write(temp_root / "index.html", _render_index(entries))
        _write(temp_root / "archive.html", _render_archive(entries))
        _write(temp_root / "rss.xml", _render_rss(entries))

        link_errors = _validate_staging_links(temp_root)
        if link_errors:
            raise RuntimeError("ICE staging link validation failed: " + "; ".join(link_errors))

        after_candidate_hashes = tree_hashes(temp_root)
        backup_root = staging_root / f".{DISPATCH}-staging-backup-{os.getpid()}"
        if backup_root.exists():
            shutil.rmtree(backup_root)
        if ice_root.exists():
            ice_root.rename(backup_root)
        temp_root.rename(ice_root)
        if backup_root.exists():
            shutil.rmtree(backup_root)
    except Exception:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        raise

    after_hashes = tree_hashes(ice_root)
    changed = tuple(sorted(path for path in set(before_hashes) | set(after_hashes) if before_hashes.get(path) != after_hashes.get(path)))
    receipt.update({
        "archive_changed": before_hashes.get("archive.html") != after_hashes.get("archive.html"),
        "rss_changed": before_hashes.get("rss.xml") != after_hashes.get("rss.xml"),
        "index_changed": before_hashes.get("index.html") != after_hashes.get("index.html"),
        "map_payload_changed": before_hashes.get(f"editions/{edition_date}/map_payload.json") != after_hashes.get(f"editions/{edition_date}/map_payload.json"),
        "changed_paths": list(changed),
        "candidate_tree_file_count": len(after_candidate_hashes),
    })
    _write_json(receipt_path, receipt)
    return StageResult("staged", receipt_path, receipt, changed, False)
