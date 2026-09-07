from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from bluefern_dispatches.source_based_retrospective_publication import (
    PUBLICATION_ROOTS,
    SUPPORTED_DATE_BINDINGS,
    validate_publication,
)


MANIFEST_SCHEMA = "bluefern.source_based_retrospective_public_generation_manifest.v1"
GENERATION_MODE = "source_based_retrospective_public_generation"

OWNED_SITE_ROOT = Path("output") / "site"
OWNED_RECEIPT_ROOT = Path("data") / "dispatches"

DISPATCH_TITLES = {
    "food-line": "Food Line",
    "care-line": "Care Line",
}

PUBLIC_CHRONOLOGY_LABELS = {
    "august_event": "August event or access condition",
    "august_announcement_future_effect": "August announcement with later effective date",
    "august_restoration": "Restoration or reopening",
    "august_reporting_on_continuing_prior_loss": "August reporting on a continuing prior access loss",
    "september_effective_event_with_august_source": "August-to-September transition",
}

INTERNAL_PUBLIC_TERMS = (
    "approval-prep",
    "release authorization",
    "publication authorization",
    "mechanical qualification",
    "retained_for_review",
    "august_event",
    "august_announcement_future_effect",
    "august_restoration",
    "august_reporting_on_continuing_prior_loss",
    "september_effective_event_with_august_source",
)

DEPLOYMENT_AUTHORITY_FIELDS = (
    "pages_authorized",
    "pages_push_authorized",
    "social_authorized",
    "audio_authorized",
    "schedule_authorized",
    "scheduled_task_change_authorized",
    "public_generation_authorized",
)


class SourceBasedRetrospectivePublicGenerationError(ValueError):
    pass


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return "sha256:" + _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBasedRetrospectivePublicGenerationError(f"unable to read valid JSON: {path}") from exc


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _source_head(root: Path) -> str:
    try:
        return _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return ""


def _assert_clean_for_generation(root: Path) -> None:
    try:
        dirty = _git(root, "status", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceBasedRetrospectivePublicGenerationError("unable to inspect source working tree") from exc
    if dirty:
        raise SourceBasedRetrospectivePublicGenerationError("source working tree must be clean before public generation")


def _repo_relative(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if root == resolved or root not in resolved.parents:
        raise SourceBasedRetrospectivePublicGenerationError(f"{label} resolves outside repository")
    return resolved.relative_to(root)


def _safe_slug(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,120}", text):
        raise SourceBasedRetrospectivePublicGenerationError(f"{field} must be a lowercase slug")
    return text


def _load_publication(
    root: Path,
    publication_path: Path,
    *,
    dispatch: str | None = None,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], Path, str]:
    root = root.resolve(strict=True)
    path = publication_path if publication_path.is_absolute() else root / publication_path
    rel = _repo_relative(root, path, "publication authorization path")
    if rel.parts[:3] != ("publication-authorizations", str(dispatch or rel.parts[1] if len(rel.parts) > 1 else ""), "source-based-retrospectives"):
        if rel.parts[:1] != ("publication-authorizations",):
            raise SourceBasedRetrospectivePublicGenerationError("generation input must be a durable publication authorization")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SourceBasedRetrospectivePublicGenerationError("publication authorization must be a JSON object")
    actual_dispatch = str(payload.get("dispatch") or "")
    if dispatch is not None and actual_dispatch != dispatch:
        raise SourceBasedRetrospectivePublicGenerationError("publication authorization dispatch mismatch")
    if actual_dispatch not in PUBLICATION_ROOTS:
        raise SourceBasedRetrospectivePublicGenerationError("publication authorization dispatch must be food-line or care-line")
    expected_root = PUBLICATION_ROOTS[actual_dispatch]
    if rel.parts[: len(expected_root.parts)] != expected_root.parts:
        raise SourceBasedRetrospectivePublicGenerationError("generation input is outside the dispatch publication-authorization owner")
    actual_sha = _sha256_file(path)
    if expected_sha256 is not None and expected_sha256.strip() and actual_sha != expected_sha256.strip():
        raise SourceBasedRetrospectivePublicGenerationError("publication authorization hash mismatch")
    errors = validate_publication(payload, expected_dispatch=actual_dispatch)
    if errors:
        raise SourceBasedRetrospectivePublicGenerationError("publication authorization validation failed: " + "; ".join(errors))
    if payload.get("publication_authorized") is not True:
        raise SourceBasedRetrospectivePublicGenerationError("publication_authorized must be true")
    for field in DEPLOYMENT_AUTHORITY_FIELDS:
        if payload.get(field) is not False:
            raise SourceBasedRetrospectivePublicGenerationError(f"{field} must remain false for local generation")
    return payload, rel, actual_sha


def _nested_value(row: dict[str, Any], *path: str) -> str:
    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "").strip()


def _item_context(item: dict[str, Any]) -> dict[str, str]:
    release_snapshot = item.get("release_item_snapshot") if isinstance(item.get("release_item_snapshot"), dict) else {}
    readiness = release_snapshot.get("readiness_item_snapshot") if isinstance(release_snapshot.get("readiness_item_snapshot"), dict) else {}
    bounded = release_snapshot.get("bounded_item_snapshot") if isinstance(release_snapshot.get("bounded_item_snapshot"), dict) else {}
    context = {
        "source_url": str(readiness.get("source_url") or bounded.get("source_url") or item.get("source_url") or "").strip(),
        "publisher": str(readiness.get("publisher") or bounded.get("publisher") or item.get("publisher") or "").strip(),
        "location": str(readiness.get("location") or bounded.get("location") or item.get("location") or item.get("source_identifier") or "").strip(),
        "event": str(readiness.get("event") or bounded.get("title") or bounded.get("source_evidence_basis") or "").strip(),
        "state_or_territory": str(readiness.get("state_or_territory") or bounded.get("state") or bounded.get("state_or_territory") or "").strip(),
        "pressure_type": str(readiness.get("pressure_type") or bounded.get("pressure_type") or bounded.get("pressure_or_service_type") or "").strip(),
    }
    if not context["event"]:
        context["event"] = context["location"] or str(item.get("source_identifier") or "").strip()
    return context


def _public_path(dispatch: str, batch_id: str) -> str:
    return f"/{dispatch}/source-based-retrospectives/{batch_id}/"


def _source_link(url: str) -> str:
    escaped_url = html.escape(url, quote=True)
    escaped_text = html.escape(url)
    return f'<a href="{escaped_url}" rel="nofollow noopener">{escaped_text}</a>'


def _chronology_sentence(item: dict[str, Any], dispatch: str) -> str:
    chronology = str(item.get("chronology_classification") or "")
    event_binding = str(item.get("event_or_effective_date_or_range") or "").strip()
    placement = str(item.get("public_edition_date_or_placement") or "").strip()
    source_date = str(item.get("source_publication_date") or "").strip()
    if chronology == "august_announcement_future_effect":
        return (
            f"The August source reported an access change with an authorized effective or event binding of "
            f"{html.escape(event_binding)}. The source-publication context is {html.escape(source_date)}. "
            f"This record is placed at {html.escape(placement)} and does not describe a completed August closure unless the source says so."
        )
    if chronology == "august_restoration":
        return (
            f"The access change is a restoration or reopening with an authorized binding of {html.escape(event_binding)}. "
            f"The source-publication context is {html.escape(source_date)}."
        )
    if chronology == "august_reporting_on_continuing_prior_loss":
        return (
            f"The August record documents a continuing prior access loss with an authorized binding of {html.escape(event_binding)}. "
            f"The source-publication context is {html.escape(source_date)}. It is not rendered as a newly effective August closure."
        )
    if chronology == "september_effective_event_with_august_source":
        return (
            f"The authorized public placement preserves the August-to-September transition: {html.escape(event_binding)}. "
            f"The source-publication context is {html.escape(source_date)}."
        )
    return (
        f"The authorized event or access-condition binding is {html.escape(event_binding)}. "
        f"The source-publication date is {html.escape(source_date)} and public placement is {html.escape(placement)}."
    )


def _item_html(item: dict[str, Any], dispatch: str) -> str:
    context = _item_context(item)
    source_url = context["source_url"]
    publisher = context["publisher"]
    if not source_url:
        raise SourceBasedRetrospectivePublicGenerationError(f"source URL missing for {item.get('publication_item_id')}")
    if not publisher:
        raise SourceBasedRetrospectivePublicGenerationError(f"publisher missing for {item.get('publication_item_id')}")
    chronology = str(item.get("chronology_classification") or "")
    if chronology not in SUPPORTED_DATE_BINDINGS:
        raise SourceBasedRetrospectivePublicGenerationError(f"unsupported chronology for {item.get('publication_item_id')}")
    location = context["location"] or "Location not specified"
    event = context["event"] or location
    pressure = context["pressure_type"] or ("food access" if dispatch == "food-line" else "care access")
    state = f", {context['state_or_territory']}" if context["state_or_territory"] else ""
    wording = str(item.get("public_wording_constraints") or "").strip()
    if not wording:
        raise SourceBasedRetrospectivePublicGenerationError(f"wording constraints missing for {item.get('publication_item_id')}")
    return "\n".join(
        [
            '<article class="retrospective-item">',
            f"  <h2>{html.escape(location)}{html.escape(state)}</h2>",
            f"  <p><strong>Access signal:</strong> {html.escape(event)}</p>",
            f"  <p><strong>Pressure type:</strong> {html.escape(pressure.replace('_', ' '))}</p>",
            f"  <p><strong>Timing:</strong> {_chronology_sentence(item, dispatch)}</p>",
            f"  <p><strong>Known limits:</strong> {html.escape(wording)}</p>",
            f"  <p><strong>Source:</strong> {html.escape(publisher)} - {_source_link(source_url)}</p>",
            "</article>",
        ]
    )


def _render_page(publication: dict[str, Any]) -> str:
    dispatch = publication["dispatch"]
    title = f"{DISPATCH_TITLES[dispatch]} source-backed August 2026 retrospective"
    items_html = "\n".join(_item_html(item, dispatch) for item in publication["publication_items"])
    generated = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{html.escape(title)}</title>",
            '  <link rel="stylesheet" href="/assets/site.css">',
            "</head>",
            "<body>",
            '  <main class="dispatch-page retrospective-publication">',
            f"    <h1>{html.escape(title)}</h1>",
            "    <p>This page preserves source-backed August 2026 access reporting that received bounded human editorial placement after the original coverage window.</p>",
            f"    <p>Records rendered: {len(publication['publication_items'])}. No Pages deployment, social post, schedule change, or audio output is part of this local generation step.</p>",
            items_html,
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )
    lowered = generated.lower()
    leaked = [term for term in INTERNAL_PUBLIC_TERMS if term in lowered]
    if leaked:
        raise SourceBasedRetrospectivePublicGenerationError("reader-facing output includes internal workflow terms: " + ", ".join(leaked))
    return generated


def _public_items(publication: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in publication["publication_items"]:
        context = _item_context(item)
        if not context["source_url"]:
            raise SourceBasedRetrospectivePublicGenerationError(f"source URL missing for {item.get('publication_item_id')}")
        if not context["publisher"]:
            raise SourceBasedRetrospectivePublicGenerationError(f"publisher missing for {item.get('publication_item_id')}")
        rows.append(
            {
                "location": context["location"],
                "state_or_territory": context["state_or_territory"],
                "event": context["event"],
                "pressure_type": context["pressure_type"],
                "publisher": context["publisher"],
                "source_url": context["source_url"],
                "source_publication_date": item["source_publication_date"],
                "event_or_effective_date_or_range": item["event_or_effective_date_or_range"],
                "public_edition_date_or_placement": item["public_edition_date_or_placement"],
                "public_chronology": PUBLIC_CHRONOLOGY_LABELS[item["chronology_classification"]],
                "public_wording_constraints": item["public_wording_constraints"],
            }
        )
    return rows


def _owned_output_path(root: Path, relative: Path) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    if root not in target.parents:
        raise SourceBasedRetrospectivePublicGenerationError(f"attempted write outside repository: {relative}")
    if relative.parts[:2] == ("output", "site"):
        return target
    if len(relative.parts) >= 5 and relative.parts[:3] == ("data", "dispatches", relative.parts[2]) and relative.parts[3:5] == ("review", "source-based-retrospective-generations"):
        return target
    raise SourceBasedRetrospectivePublicGenerationError(f"attempted write outside owned generated roots: {relative.as_posix()}")


def _validate_rendered(publication: dict[str, Any], html_text: str, items: list[dict[str, Any]]) -> None:
    if len(items) != publication["item_count"]:
        raise SourceBasedRetrospectivePublicGenerationError("authorized/rendered count mismatch")
    if any(item.get("publication_authorized") is not True for item in publication["publication_items"]):
        raise SourceBasedRetrospectivePublicGenerationError("unauthorized item encountered")
    for item in publication["publication_items"]:
        for field in ("source_publication_date", "event_or_effective_date_or_range", "public_edition_date_or_placement", "public_wording_constraints"):
            value = str(item.get(field) or "").strip()
            if not value or html.escape(value) not in html_text:
                raise SourceBasedRetrospectivePublicGenerationError(f"chronology or wording loss for {item.get('publication_item_id')}: {field}")
        source_url = _item_context(item)["source_url"]
        publisher = _item_context(item)["publisher"]
        if html.escape(source_url) not in html_text or html.escape(publisher) not in html_text:
            raise SourceBasedRetrospectivePublicGenerationError(f"source traceability loss for {item.get('publication_item_id')}")
        chronology = item["chronology_classification"]
        rendered_lower = html_text.lower()
        if chronology == "august_restoration" and "new closure" in rendered_lower:
            raise SourceBasedRetrospectivePublicGenerationError("restoration rendered as closure")
        if chronology == "august_reporting_on_continuing_prior_loss" and "was a newly effective august closure" in rendered_lower:
            raise SourceBasedRetrospectivePublicGenerationError("continuing loss rendered as new closure")
        if chronology == "august_announcement_future_effect" and "was closed in august" in rendered_lower:
            raise SourceBasedRetrospectivePublicGenerationError("future-effective event rendered as already completed")


def generate_public_artifacts(
    root: Path,
    publication_path: Path,
    *,
    dispatch: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    _assert_clean_for_generation(root)
    publication, publication_rel, publication_sha = _load_publication(
        root,
        publication_path,
        dispatch=dispatch,
        expected_sha256=expected_sha256,
    )
    actual_dispatch = publication["dispatch"]
    batch_id = _safe_slug(publication["publication_batch_id"], "publication_batch_id")
    items = publication.get("publication_items")
    if not isinstance(items, list) or len(items) != publication.get("item_count"):
        raise SourceBasedRetrospectivePublicGenerationError("authorized/rendered count mismatch")
    if len({str(item.get("publication_item_id") or "") for item in items if isinstance(item, dict)}) != len(items):
        raise SourceBasedRetrospectivePublicGenerationError("duplicate item IDs")
    page_html = _render_page(publication)
    public_items = _public_items(publication)
    _validate_rendered(publication, page_html, public_items)
    public_root_rel = OWNED_SITE_ROOT / actual_dispatch / "source-based-retrospectives" / batch_id
    public_path = _public_path(actual_dispatch, batch_id)
    index_rel = public_root_rel / "index.html"
    items_rel = public_root_rel / "items.json"
    receipt_rel = OWNED_RECEIPT_ROOT / actual_dispatch / "review" / "source-based-retrospective-generations" / f"{batch_id}.json"
    _write_text(_owned_output_path(root, index_rel), page_html)
    _write_json(_owned_output_path(root, items_rel), public_items)
    artifact_paths = [index_rel, items_rel]
    receipt = {
        "schema_version": MANIFEST_SCHEMA,
        "generation_mode": GENERATION_MODE,
        "dispatch": actual_dispatch,
        "publication_authorization_path": publication_rel.as_posix(),
        "publication_authorization_sha256": publication_sha,
        "publication_batch_id": batch_id,
        "source_head": _source_head(root),
        "generated_at": publication.get("authorized_at"),
        "public_artifacts_generated": True,
        "pages_authorized": False,
        "pages_push_authorized": False,
        "social_authorized": False,
        "audio_authorized": False,
        "schedule_authorized": False,
        "scheduled_task_change_authorized": False,
        "public_path": public_path,
        "authorized_item_count": publication["item_count"],
        "rendered_item_count": len(public_items),
        "skipped_item_count": 0,
        "skipped_items": [],
        "unauthorized_item_count": 0,
        "generated_item_ids": [item["publication_item_id"] for item in publication["publication_items"]],
        "chronology_bindings": [
            {
                "publication_item_id": item["publication_item_id"],
                "chronology_classification": item["chronology_classification"],
                "source_publication_date": item["source_publication_date"],
                "event_or_effective_date_or_range": item["event_or_effective_date_or_range"],
                "public_edition_date_or_placement": item["public_edition_date_or_placement"],
            }
            for item in publication["publication_items"]
        ],
        "source_urls": [
            {
                "publication_item_id": item["publication_item_id"],
                "publisher": _item_context(item)["publisher"],
                "source_url": _item_context(item)["source_url"],
            }
            for item in publication["publication_items"]
        ],
        "generated_public_paths": [path.as_posix() for path in artifact_paths],
        "artifact_hashes": {
            path.as_posix(): _sha256_file(root / path)
            for path in artifact_paths
        },
    }
    if receipt["authorized_item_count"] != receipt["rendered_item_count"]:
        raise SourceBasedRetrospectivePublicGenerationError("authorized/rendered count mismatch")
    _write_json(_owned_output_path(root, receipt_rel), receipt)
    receipt["generation_receipt_path"] = receipt_rel.as_posix()
    receipt["generated_public_paths"].append(receipt_rel.as_posix())
    receipt["artifact_hashes"][receipt_rel.as_posix()] = _sha256_file(root / receipt_rel)
    return {"ok": True, "status": "public_artifacts_generated", "generation": receipt}


def validate_generation_manifest(root: Path, manifest_path: Path | str) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest_path = Path(manifest_path)
    path = manifest_path if manifest_path.is_absolute() else root / manifest_path
    rel = _repo_relative(root, path, "generation manifest path")
    if not (len(rel.parts) >= 5 and rel.parts[:2] == ("data", "dispatches") and rel.parts[3:5] == ("review", "source-based-retrospective-generations")):
        raise SourceBasedRetrospectivePublicGenerationError("generation manifest is outside the owned receipt root")
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        raise SourceBasedRetrospectivePublicGenerationError("generation manifest must be a JSON object")
    errors: list[str] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        errors.append("manifest schema_version is invalid")
    if manifest.get("generation_mode") != GENERATION_MODE:
        errors.append("manifest generation_mode is invalid")
    if manifest.get("public_artifacts_generated") is not True:
        errors.append("manifest must record public_artifacts_generated true")
    for field in ("pages_authorized", "pages_push_authorized", "social_authorized", "audio_authorized", "schedule_authorized", "scheduled_task_change_authorized"):
        if manifest.get(field) is not False:
            errors.append(f"{field} must be false")
    if manifest.get("authorized_item_count") != manifest.get("rendered_item_count"):
        errors.append("authorized/rendered count mismatch")
    if manifest.get("skipped_item_count") != 0 or manifest.get("unauthorized_item_count") != 0:
        errors.append("generation manifest records skipped or unauthorized items")
    for rel_text, expected_hash in (manifest.get("artifact_hashes") or {}).items():
        artifact = root / str(rel_text)
        if not artifact.exists():
            errors.append(f"artifact missing: {rel_text}")
        elif _sha256_file(artifact) != expected_hash:
            errors.append(f"artifact hash mismatch: {rel_text}")
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "generation_manifest_path": rel.as_posix()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local public artifacts from source-based retrospective publication authorizations.")
    sub = parser.add_subparsers(dest="operation", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--repo-root", type=Path, required=True)
    generate.add_argument("--publication-path", type=Path, required=True)
    generate.add_argument("--dispatch", choices=sorted(PUBLICATION_ROOTS))
    generate.add_argument("--expected-sha256")
    validate = sub.add_parser("validate")
    validate.add_argument("--repo-root", type=Path, required=True)
    validate.add_argument("--manifest-path", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.operation == "generate":
            result = generate_public_artifacts(
                args.repo_root,
                args.publication_path,
                dispatch=args.dispatch,
                expected_sha256=args.expected_sha256,
            )
        else:
            result = validate_generation_manifest(args.repo_root, args.manifest_path)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
