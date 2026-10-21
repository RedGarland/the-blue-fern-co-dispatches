from __future__ import annotations

import html
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
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


def _merge_history_entries(
    baseline: list[dict[str, Any]],
    staged: list[dict[str, Any]],
    *,
    candidate_date: str,
) -> list[dict[str, Any]]:
    by_date = {str(item["edition_date"]): dict(item) for item in baseline}
    for item in staged:
        date = str(item["edition_date"])
        if date in by_date and date != candidate_date and by_date[date] != item:
            raise ValueError(f"staged ICE history conflicts with protected baseline for {date}")
        by_date[date] = dict(item)
    return [by_date[date] for date in sorted(by_date, key=_plain_date_key, reverse=True)]


def _history_ice_root(history_baseline_root: Path) -> Path:
    return history_baseline_root if history_baseline_root.name == DISPATCH else history_baseline_root / DISPATCH


def _is_symlink_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        # Path.stat() follows directory junctions/reparse points on Windows;
        # lstat() inspects the directory entry itself before any traversal.
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise ValueError(f"cannot inspect path for symlink or reparse point: {path}") from exc
    return bool(attributes & 0x400)


def _reject_symlinks(root: Path, *, label: str) -> None:
    if _is_symlink_or_reparse_point(root):
        raise ValueError(f"{label} contains a symlink or reparse point: .")
    for path in root.rglob("*"):
        if _is_symlink_or_reparse_point(path):
            raise ValueError(
                f"{label} contains a symlink or reparse point: "
                f"{path.relative_to(root).as_posix()}"
            )


def _validated_history_entries(ice_root: Path, *, label: str) -> list[dict[str, Any]]:
    """Load a complete protected history tree, rejecting name-only baselines."""
    _reject_symlinks(ice_root, label=label)
    archive_path = ice_root / "archive_entries.json"
    if not archive_path.is_file():
        raise ValueError(f"{label} is missing archive_entries.json")
    try:
        entries = json.loads(archive_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} archive_entries.json is malformed") from exc
    entries = _validated_archive_entries(entries, label=label)
    if not entries:
        raise ValueError(f"{label} is empty; use explicit first_publication mode")
    dates = {str(item["edition_date"]) for item in entries}

    editions_root = ice_root / "editions"
    actual_dates = {
        path.name for path in editions_root.iterdir() if path.is_dir()
    } if editions_root.is_dir() else set()
    if actual_dates != dates:
        missing = sorted(dates - actual_dates)
        extra = sorted(actual_dates - dates)
        raise ValueError(
            f"{label} edition directories do not match archive_entries.json "
            f"(missing={missing}, extra={extra})"
        )

    for edition_date in sorted(dates):
        edition_dir = editions_root / edition_date
        for required_name in ("index.html", "map_payload.json", "manifest.json"):
            if not (edition_dir / required_name).is_file():
                raise ValueError(f"{label} is missing {required_name} for {edition_date}")
        manifest_path = edition_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} manifest is malformed for {edition_date}") from exc
        if not isinstance(manifest, dict) or manifest.get("edition_date") != edition_date:
            raise ValueError(f"{label} manifest does not match edition directory {edition_date}")
        if manifest.get("dispatch") != DISPATCH:
            raise ValueError(f"{label} manifest has the wrong dispatch for {edition_date}")
        counts = []
        for key in ("accepted_event_count", "rendered_event_count"):
            value = manifest.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} manifest has an invalid {key} for {edition_date}")
            counts.append(value)
        if counts[0] != counts[1]:
            raise ValueError(f"{label} manifest event counts disagree for {edition_date}")
        entry = next(item for item in entries if item["edition_date"] == edition_date)
        if entry.get("event_count") != counts[0]:
            raise ValueError(f"{label} archive event count disagrees with manifest for {edition_date}")
        try:
            map_payload = json.loads((edition_dir / "map_payload.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} map_payload.json is malformed for {edition_date}") from exc
        if not isinstance(map_payload, dict):
            raise ValueError(f"{label} map_payload.json is malformed for {edition_date}")
    return [dict(item) for item in entries]


def _validated_archive_entries(payload: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError(f"{label} archive_entries.json must contain a list")
    dates: set[str] = set()
    validated: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"{label} archive_entries.json contains a malformed entry")
        edition_date = item.get("edition_date")
        if not isinstance(edition_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition_date):
            raise ValueError(f"{label} archive_entries.json contains an invalid edition date")
        try:
            date.fromisoformat(edition_date)
        except ValueError as exc:
            raise ValueError(f"{label} archive_entries.json contains an invalid calendar date") from exc
        if edition_date in dates:
            raise ValueError(f"{label} archive_entries.json contains duplicate edition date {edition_date}")
        if item.get("href") != _edition_href(edition_date):
            raise ValueError(f"{label} archive entry has an unsafe or mismatched href for {edition_date}")
        if item.get("canonical_path") != f"/ice/{_edition_href(edition_date)}":
            raise ValueError(f"{label} archive entry has an unsafe or mismatched canonical path for {edition_date}")
        if "event_count" in item:
            event_count = item.get("event_count")
            if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count < 0:
                raise ValueError(f"{label} archive_entries.json contains an invalid event count")
        dates.add(edition_date)
        validated.append(dict(item))
    return validated


def _validate_staged_inventory(ice_root: Path, entries: list[dict[str, Any]]) -> None:
    if not ice_root.exists() or not tree_hashes(ice_root):
        return
    _reject_symlinks(ice_root, label="staged ICE tree")
    if not (ice_root / "archive_entries.json").is_file():
        raise ValueError("existing ICE staging tree is missing archive_entries.json")
    if not entries:
        raise ValueError("existing ICE staging tree has no archive entries")
    edition_root = ice_root / "editions"
    actual_dates = {path.name for path in edition_root.iterdir() if path.is_dir()} if edition_root.is_dir() else set()
    entry_dates = {str(item["edition_date"]) for item in entries}
    unexpected = sorted(actual_dates - entry_dates)
    if unexpected:
        raise ValueError(
            "staged ICE edition directories are absent from archive_entries.json: "
            + ", ".join(unexpected)
        )


def _validate_staged_overlay_against_baseline(
    ice_root: Path,
    baseline_ice: Path,
    *,
    edition_date: str,
) -> None:
    baseline_hashes = tree_hashes(baseline_ice)
    staged_hashes = tree_hashes(ice_root) if ice_root.exists() else {}
    regenerated = {"index.html", "archive.html", "rss.xml", "archive_entries.json"}
    candidate_paths = {
        f"editions/{edition_date}/index.html",
        f"editions/{edition_date}/map_payload.json",
        f"editions/{edition_date}/manifest.json",
    }
    for path, staged_hash in staged_hashes.items():
        if path in regenerated or path in candidate_paths:
            continue
        baseline_hash = baseline_hashes.get(path)
        if baseline_hash is None:
            raise ValueError(f"staged ICE path is outside the protected baseline: {path}")
        if staged_hash != baseline_hash:
            raise ValueError(f"staged ICE path conflicts with protected baseline: {path}")


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
    history_baseline_root: Path | None = None,
    first_publication: bool = False,
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
    if ice_root.exists():
        _reject_symlinks(ice_root, label="staged ICE tree")
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
        "history_baseline_root": str(history_baseline_root) if history_baseline_root else None,
        "first_publication": first_publication,
        "public_side_effects": False,
        "source_commit": source_commit,
        "generated_at": generated,
        "status": "staged" if should_stage else "no_op",
        "reason": effective_decision,
    }

    if not should_stage:
        _write_json(receipt_path, receipt)
        return StageResult("no_op", receipt_path, receipt, (), False)

    if ice_root.exists() and (ice_root / "archive_entries.json").is_file():
        try:
            staged_payload = _load_json(ice_root / "archive_entries.json", [])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("staged ICE archive_entries.json is malformed") from exc
        staged_entries = _validated_archive_entries(staged_payload, label="staged ICE")
    else:
        staged_entries = []
    _validate_staged_inventory(ice_root, staged_entries)
    if ice_root.exists() and tree_hashes(ice_root):
        if not (ice_root / "archive_entries.json").is_file():
            raise ValueError("existing ICE staging tree is missing archive_entries.json")
        if not staged_entries:
            raise ValueError("existing ICE staging tree has no archive entries")
        if history_baseline_root is None and staged_entries:
            _validated_history_entries(ice_root, label="existing ICE staging tree")
    if ice_root.exists() and staged_entries and history_baseline_root is None:
        staged_dates = {str(item.get("edition_date") or "") for item in staged_entries if isinstance(item, dict)}
        if staged_dates and staged_dates <= {edition_date}:
            raise ValueError(
                "protected ICE history baseline is required for a non-empty partial staging tree"
            )

    if should_stage:
        if history_baseline_root is not None and first_publication:
            raise ValueError("first_publication cannot be combined with a protected history baseline")
        if history_baseline_root is None and not first_publication:
            raise ValueError(
                "authoritative ICE history baseline or first_publication=True is required"
            )
        if first_publication and before_hashes:
            raise ValueError("first_publication requires an empty ICE staging tree")

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
    baseline_entries: list[dict[str, Any]] = []
    if history_baseline_root is not None:
        baseline_ice = _history_ice_root(history_baseline_root)
        if not baseline_ice.exists():
            raise ValueError(f"ICE history baseline not found: {baseline_ice}")
        baseline_entries = _validated_history_entries(
            baseline_ice,
            label="protected ICE history baseline",
        )
        _validate_staged_overlay_against_baseline(
            ice_root,
            baseline_ice,
            edition_date=edition_date,
        )
        staged_dates = {
            str(item.get("edition_date") or "")
            for item in staged_entries
            if isinstance(item, dict)
        }
        baseline_dates = {str(item["edition_date"]) for item in baseline_entries}
        unexpected_staged_dates = staged_dates - baseline_dates - {edition_date}
        if unexpected_staged_dates:
            raise ValueError(
                "staged ICE history contains dates absent from the protected baseline: "
                + ", ".join(sorted(unexpected_staged_dates))
            )
        for retained_date in sorted(staged_dates & baseline_dates - {edition_date}):
            staged_dir = ice_root / "editions" / retained_date
            baseline_dir = baseline_ice / "editions" / retained_date
            if staged_dir.exists() and tree_hashes(staged_dir) != tree_hashes(baseline_dir):
                raise ValueError(
                    f"staged ICE history conflicts with protected baseline assets for {retained_date}"
                )
        shutil.copytree(baseline_ice, temp_root)
    elif not ice_root.exists():
        temp_root.mkdir(parents=True)
    if ice_root.exists():
        shutil.copytree(ice_root, temp_root, dirs_exist_ok=True)

    try:
        existing_entries = _merge_history_entries(
            baseline_entries,
            staged_entries,
            candidate_date=edition_date,
        )
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
        try:
            temp_root.rename(ice_root)
        except Exception:
            if backup_root.exists() and not ice_root.exists():
                backup_root.rename(ice_root)
            raise
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
