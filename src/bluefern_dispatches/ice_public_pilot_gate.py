from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

APPROVAL_SCHEMA = "bluefern.ice.public_pilot_approval.v1"
ALLOWED_ROOT_FILES = {"index.html", "archive.html", "rss.xml", "archive_entries.json"}
ALLOWED_EDITION_FILES = {"index.html", "manifest.json", "map_payload.json"}


class IcePublicPilotGateError(ValueError):
    pass


@dataclass(frozen=True)
class IcePublicPilotGateResult:
    ok: bool
    edition_date: str
    bundle_id: str
    approved_event_count: int
    allowed_public_paths: tuple[str, ...]
    social_authorized: bool
    publication_authorized: bool
    pages_authorized: bool


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IcePublicPilotGateError(message)


def _as_tuple(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _expected_public_paths(edition_date: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            [
                *(f"ice/{name}" for name in ALLOWED_ROOT_FILES),
                *(f"ice/editions/{edition_date}/{name}" for name in ALLOWED_EDITION_FILES),
            ]
        )
    )


def _staged_paths(staging_root: Path, edition_date: str) -> tuple[str, ...]:
    ice_root = staging_root / "ice"
    if not ice_root.is_dir():
        return ()
    paths = []
    for path in ice_root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(staging_root).as_posix()
            paths.append(rel)
    return tuple(sorted(paths))


def _validate_bundle_and_candidates(root: Path, manifest: dict[str, Any]) -> None:
    _require(manifest.get("schema_version") == "bluefern.ice.monitor.release_bundle.v1", "unsupported ICE release bundle schema")
    _require(manifest.get("publication_authorized") is False, "private ICE bundle must not itself carry publication authority")
    _require(manifest.get("pages_authorized") is False, "private ICE bundle must not itself carry Pages authority")
    _require(manifest.get("social_authorized") is False, "private ICE bundle must not itself carry social authority")
    _require(manifest.get("edition_candidate_input_ready") is True, "ICE bundle is not candidate-input ready")
    event_fingerprints = _as_tuple(manifest.get("event_fingerprints") or [])
    candidate_refs = _as_tuple(manifest.get("release_candidate_refs") or [])
    decision_ids = _as_tuple(manifest.get("review_decision_ids") or [])
    _require(bool(event_fingerprints), "ICE public pilot requires at least one reviewed event")
    _require(len(candidate_refs) == len(event_fingerprints), "ICE release candidate count does not match bundle event count")
    _require(len(decision_ids) == len(event_fingerprints), "ICE review decision count does not match bundle event count")

    for ref, fingerprint, decision_id in zip(candidate_refs, event_fingerprints, decision_ids):
        candidate_path = (root / ref).resolve()
        allowed_root = (root / "data/dispatches/ice/monitor/release-candidates").resolve()
        _require(allowed_root == candidate_path.parent or allowed_root in candidate_path.parents, "ICE release candidate ref escapes release-candidates root")
        _require(candidate_path.is_file(), f"ICE release candidate missing: {ref}")
        candidate = _load_json(candidate_path)
        _require(candidate.get("schema_version") == "bluefern.ice.monitor.release_candidate.v1", "unsupported ICE release candidate schema")
        _require(candidate.get("event_fingerprint") == fingerprint, "ICE release candidate fingerprint mismatch")
        _require(candidate.get("review_decision_id") == decision_id, "ICE release candidate review decision mismatch")
        _require(candidate.get("release_review_ready") is True, "ICE release candidate is not release-review ready")
        _require(candidate.get("corroboration_status") == "corroborated", "ICE release candidate still needs corroboration")
        _require(candidate.get("review_decision_id"), "ICE release candidate lacks review decision")
        _require(candidate.get("publication_authorized") is False, "ICE release candidate must not self-authorize publication")
        _require(candidate.get("pages_authorized") is False, "ICE release candidate must not self-authorize Pages")
        _require(candidate.get("social_authorized") is False, "ICE release candidate must not self-authorize social")


def _validate_approval(manifest: dict[str, Any], approval: dict[str, Any], *, source_head: str | None, social_requested: bool) -> None:
    _require(approval.get("schema_version") == APPROVAL_SCHEMA, "missing explicit ICE public pilot approval artifact")
    _require(approval.get("dispatch") == "ice", "ICE approval dispatch mismatch")
    _require(approval.get("approval_type") == "ice_public_pilot", "ICE approval type mismatch")
    _require(approval.get("bundle_id") == manifest.get("bundle_id"), "ICE approval bundle_id mismatch")
    _require(approval.get("edition_date") == manifest.get("edition_date"), "ICE approval edition_date mismatch")
    _require(approval.get("queue_sha256") == manifest.get("queue_sha256"), "ICE approval queue hash mismatch")
    _require(approval.get("reviewed_events_sha256") == manifest.get("reviewed_events_sha256"), "ICE approval reviewed-events hash mismatch")
    _require(_as_tuple(approval.get("event_fingerprints") or []) == _as_tuple(manifest.get("event_fingerprints") or []), "ICE approval event set mismatch")
    if source_head:
        _require(approval.get("source_head") == source_head, "ICE approval source_head mismatch")
    _require(approval.get("publication_authorized") is True, "ICE public pilot requires publication_authorized=true")
    _require(approval.get("pages_authorized") is True, "ICE public pilot requires pages_authorized=true")
    if social_requested:
        _require(approval.get("social_authorized") is True, "ICE social post requires separate social_authorized=true")


def validate_ice_public_pilot_gate(
    *,
    repo_root: Path,
    bundle_manifest_path: Path,
    staging_root: Path,
    approval_path: Path | None,
    source_head: str | None = None,
    social_requested: bool = False,
) -> IcePublicPilotGateResult:
    root = repo_root.resolve()
    manifest = _load_json(bundle_manifest_path)
    _validate_bundle_and_candidates(root, manifest)

    edition_date = str(manifest.get("edition_date") or "")
    _require(bool(edition_date), "ICE bundle missing edition_date")
    expected_paths = _expected_public_paths(edition_date)
    staged = _staged_paths(staging_root.resolve(), edition_date)
    _require(bool(staged), "ICE staging root has no staged files")
    unexpected = sorted(set(staged) - set(expected_paths))
    missing = sorted(set(expected_paths) - set(staged))
    _require(not unexpected, "ICE public output scope is not bounded: " + ", ".join(unexpected))
    _require(not missing, "ICE staging output is incomplete: " + ", ".join(missing))

    if approval_path is None:
        raise IcePublicPilotGateError("missing explicit ICE public pilot approval artifact")
    approval = _load_json(approval_path)
    _validate_approval(manifest, approval, source_head=source_head, social_requested=social_requested)

    return IcePublicPilotGateResult(
        ok=True,
        edition_date=edition_date,
        bundle_id=str(manifest["bundle_id"]),
        approved_event_count=len(manifest.get("event_fingerprints") or []),
        allowed_public_paths=expected_paths,
        social_authorized=approval.get("social_authorized") is True,
        publication_authorized=True,
        pages_authorized=True,
    )

