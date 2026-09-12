from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .external_agent_handoff import OPERATOR_RECOVERY_CLASS
from .food_line_operator_recovery_publication_authorization import (
    PUBLICATION_ROOT,
    PUBLICATION_STATE,
    SCHEMA_VERSION as PUBLICATION_SCHEMA_VERSION,
    SUPPORTED_PLACEMENT_STRATEGIES,
    validate_publication_authorization,
)


PREVIEW_MANIFEST_VERSION = "food-line-recovery-preview-v1"
EXPECTED_ITEM_IDS = (
    "finding_0ca835b632814684d8f1f2ec",
    "finding_5a56d5dbd54a3ceca06f44d6",
    "finding_4341336d9c9a9cb00b9543ca",
    "finding_ba095f74fbb914bc56aeb120",
)
HOLD_ITEM_ID = "finding_1cae6cc95cacbe13fa5eb7f8"
REQUIRED_PLACEMENT = "recovery_disclosed_current_publication"
RECOVERY_DISCLOSURE = (
    "Recovery note: These items were recovered after the September 10 Food Line collection failed. "
    "They were reconstructed from separately preserved, source-backed evidence and subsequently "
    "human reviewed. They are not artifacts from the failed production run."
)
FORBIDDEN_OUTPUT_ROOTS = (
    Path("output"),
    Path("public"),
    Path("archive"),
    Path("bluefern-dispatches-pages"),
    Path("data") / "dispatches",
    Path("status"),
)
FORBIDDEN_PUBLIC_TERMS = (
    "operator_recovered_source_watch_evidence",
    "retained_for_review",
    "release_authorized",
    "publication_authorized",
    "publication_authorization",
    "release-prep",
    "correction overlay",
    "sha256",
    "finding_",
)
FALSE_AUTHORITY_FIELDS = (
    "public_generation_authorized",
    "pages_authorized",
    "pages_push_authorized",
    "public_artifacts_generated",
    "publication_performed",
    "eligible_for_automatic_publication",
)


class FoodLineOperatorRecoveryPublicGenerationError(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoodLineOperatorRecoveryPublicGenerationError(f"unable to read valid JSON: {path}") from exc


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _repo_relative(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if root == resolved or root not in resolved.parents:
        raise FoodLineOperatorRecoveryPublicGenerationError(f"{label} resolves outside repository")
    return resolved.relative_to(root)


def _assert_committed_at_head(root: Path, rel_path: Path, label: str) -> None:
    try:
        _git(root, "cat-file", "-e", f"HEAD:{rel_path.as_posix()}")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineOperatorRecoveryPublicGenerationError(f"{label} must be committed at HEAD") from exc


def _assert_output_directory(root: Path, output_dir: Path) -> Path:
    root = root.resolve(strict=True)
    output = output_dir.resolve()
    if root == output or root in output.parents:
        rel = output.relative_to(root)
        raise FoodLineOperatorRecoveryPublicGenerationError(f"private preview output must be outside the repository: {rel.as_posix()}")
    forbidden_names = {"gh-pages", "pages", "bluefern-dispatches-pages", "output", "archive", "public"}
    if any(part.lower() in forbidden_names for part in output.parts):
        raise FoodLineOperatorRecoveryPublicGenerationError("private preview output path resembles a public or Pages destination")
    return output


def _validate_publication(root: Path, publication_path: Path, expected_sha256: str | None) -> tuple[dict[str, Any], Path, str]:
    root = root.resolve(strict=True)
    path = publication_path if publication_path.is_absolute() else root / publication_path
    rel = _repo_relative(root, path, "publication authorization path")
    expected_root = PUBLICATION_ROOT
    if rel.parts[: len(expected_root.parts)] != expected_root.parts:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization is outside the operator-recovery owner")
    _assert_committed_at_head(root, rel, "publication authorization")
    actual_sha = _sha256_file(path)
    if expected_sha256 and expected_sha256.strip() and actual_sha != expected_sha256.strip():
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization hash mismatch")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization must be a JSON object")
    errors = validate_publication_authorization(payload)
    if errors:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization validation failed: " + "; ".join(errors))
    if payload.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization schema_version is invalid")
    if payload.get("publication_authorization_state") != PUBLICATION_STATE:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication authorization state is invalid")
    if payload.get("publication_authorized") is not True:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication_authorized must be true")
    for field in FALSE_AUTHORITY_FIELDS:
        if payload.get(field) is not False:
            raise FoodLineOperatorRecoveryPublicGenerationError(f"{field} must remain false for private preview generation")
    if payload.get("september_10_original_production_runtime_remains_failed") is not True:
        raise FoodLineOperatorRecoveryPublicGenerationError("September 10 failed-production distinction must be preserved")
    if payload.get("original_source_watch_lineage_fabricated") is not False:
        raise FoodLineOperatorRecoveryPublicGenerationError("original production Source Watch lineage must not be fabricated")
    items = payload.get("publication_items")
    if not isinstance(items, list) or payload.get("item_count") != len(items):
        raise FoodLineOperatorRecoveryPublicGenerationError("publication item count mismatch")
    ids = [str(item.get("item_id") or "") for item in items if isinstance(item, Mapping)]
    if tuple(ids) != EXPECTED_ITEM_IDS:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication item membership must match the four authorized items")
    if HOLD_ITEM_ID in ids:
        raise FoodLineOperatorRecoveryPublicGenerationError("HOLD item cannot render in operator-recovery preview")
    if len(set(ids)) != len(ids):
        raise FoodLineOperatorRecoveryPublicGenerationError("duplicate publication item IDs")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise FoodLineOperatorRecoveryPublicGenerationError(f"publication item {index} must be an object")
        _validate_item(item, index)
    return payload, rel, actual_sha


def _validate_item(item: Mapping[str, Any], index: int) -> None:
    snapshot = item.get("release_item_snapshot")
    if not isinstance(snapshot, Mapping):
        raise FoodLineOperatorRecoveryPublicGenerationError(f"publication item {index} missing release lineage snapshot")
    if item.get("publication_placement_strategy") != REQUIRED_PLACEMENT:
        raise FoodLineOperatorRecoveryPublicGenerationError("publication placement strategy is unsupported")
    if REQUIRED_PLACEMENT not in SUPPORTED_PLACEMENT_STRATEGIES:
        raise FoodLineOperatorRecoveryPublicGenerationError("operator-recovery publication owner no longer supports placement strategy")
    if item.get("provenance_class") != OPERATOR_RECOVERY_CLASS or snapshot.get("provenance_class") != OPERATOR_RECOVERY_CLASS:
        raise FoodLineOperatorRecoveryPublicGenerationError("operator-recovery provenance must be preserved")
    if item.get("failed_production_disclosure") != "preserve_failed_production_operator_recovery_distinction":
        raise FoodLineOperatorRecoveryPublicGenerationError("failed-production disclosure is required")
    if snapshot.get("production_collection_failed_before_discovery") is not True:
        raise FoodLineOperatorRecoveryPublicGenerationError("failed production discovery sequence must be preserved")
    if snapshot.get("failed_production_lineage_preserved") is not True:
        raise FoodLineOperatorRecoveryPublicGenerationError("failed production lineage must be preserved")
    if snapshot.get("original_source_watch_lineage_fabricated") is not False:
        raise FoodLineOperatorRecoveryPublicGenerationError("original production lineage must not be fabricated")
    if item.get("source_traceability_status") != snapshot.get("source_verification"):
        raise FoodLineOperatorRecoveryPublicGenerationError("source traceability must match release lineage")
    exact_fields = {
        "reviewed_headline": "reviewed_headline",
        "reviewed_summary": "reviewed_summary",
        "source_url": "source_url",
        "publisher": "publisher",
        "event_date": "event_date",
    }
    for field, snapshot_field in exact_fields.items():
        if item.get(field) != snapshot.get(snapshot_field):
            raise FoodLineOperatorRecoveryPublicGenerationError(f"publication item changed {field}")
        if not str(item.get(field) or "").strip():
            raise FoodLineOperatorRecoveryPublicGenerationError(f"publication item {index} missing {field}")
    if not str(item.get("public_edition_date_or_placement") or "").strip():
        raise FoodLineOperatorRecoveryPublicGenerationError("public edition date or placement is required")
    for field in FALSE_AUTHORITY_FIELDS:
        if item.get(field) is not False:
            raise FoodLineOperatorRecoveryPublicGenerationError(f"publication item {index} {field} must remain false")


def _source_link(url: str) -> str:
    escaped_url = html.escape(url, quote=True)
    return f'<a href="{escaped_url}" rel="nofollow noopener noreferrer">{html.escape(url)}</a>'


def _story_html(item: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            '<article class="story">',
            f"  <p class=\"date\">{html.escape(str(item['event_date']))}</p>",
            f"  <h2>{html.escape(str(item['reviewed_headline']))}</h2>",
            f"  <p>{html.escape(str(item['reviewed_summary']))}</p>",
            f"  <p class=\"source\">Source: {html.escape(str(item['publisher']))} - {_source_link(str(item['source_url']))}</p>",
            "</article>",
        ]
    )


def _render_html(publication: Mapping[str, Any]) -> str:
    stories = "\n".join(_story_html(item) for item in publication["publication_items"])
    page = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>Food Line recovery preview</title>",
            "  <style>",
            "    :root { color-scheme: light; --ink: #17201b; --muted: #52625a; --line: #d7dfda; --paper: #fbfcfa; --accent: #1c6b59; }",
            "    body { margin: 0; font-family: Georgia, 'Times New Roman', serif; color: var(--ink); background: var(--paper); }",
            "    main { width: min(920px, calc(100% - 32px)); margin: 0 auto; padding: 36px 0 48px; }",
            "    h1 { font-size: 2rem; line-height: 1.15; margin: 0 0 12px; }",
            "    .note { border-left: 4px solid var(--accent); padding: 12px 16px; background: #eef5f2; margin: 20px 0 28px; }",
            "    .story { border-top: 1px solid var(--line); padding: 22px 0; }",
            "    .date, .source { color: var(--muted); font-family: Arial, sans-serif; font-size: 0.95rem; }",
            "    h2 { font-size: 1.35rem; line-height: 1.2; margin: 6px 0 10px; }",
            "    p { font-size: 1.05rem; line-height: 1.58; }",
            "    a { color: #0b5e6f; overflow-wrap: anywhere; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            "    <h1>Food Line recovery preview</h1>",
            f"    <p class=\"note\">{html.escape(RECOVERY_DISCLOSURE)}</p>",
            f"    <p>Event dates: {html.escape(_event_date_range(publication))}.</p>",
            f"    <p>Placement: {html.escape(str(publication['publication_items'][0]['public_edition_date_or_placement']))}.</p>",
            stories,
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )
    _assert_reader_safe(page)
    return page


def _event_date_range(publication: Mapping[str, Any]) -> str:
    dates = [str(item.get("event_date") or "") for item in publication["publication_items"]]
    return f"{min(dates)} to {max(dates)}"


def _public_items(publication: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "headline": str(item["reviewed_headline"]),
            "summary": str(item["reviewed_summary"]),
            "publisher": str(item["publisher"]),
            "source_url": str(item["source_url"]),
            "event_date": str(item["event_date"]),
            "placement": str(item["public_edition_date_or_placement"]),
        }
        for item in publication["publication_items"]
    ]


def _assert_reader_safe(text: str) -> None:
    lowered = text.lower()
    leaked = [term for term in FORBIDDEN_PUBLIC_TERMS if term.lower() in lowered]
    if leaked:
        raise FoodLineOperatorRecoveryPublicGenerationError("preview exposes internal governance terms: " + ", ".join(leaked))
    if re.search(r"[A-Za-z]:\\", text) or "/tmp/" in text.lower():
        raise FoodLineOperatorRecoveryPublicGenerationError("preview exposes an absolute local path")
    if "september 10 normal production succeeded" in lowered or "successful normal production" in lowered:
        raise FoodLineOperatorRecoveryPublicGenerationError("preview implies September 10 normal production succeeded")
    if "Ã" in text or "â€" in text or "â€“" in text:
        raise FoodLineOperatorRecoveryPublicGenerationError("preview contains mojibake")


def _inspect_preview(output_dir: Path, publication: Mapping[str, Any]) -> dict[str, Any]:
    html_text = (output_dir / "index.html").read_text(encoding="utf-8")
    manifest = _read_json(output_dir / "preview-manifest.json")
    items = list(publication["publication_items"])
    _assert_reader_safe(html_text)
    if not isinstance(manifest, Mapping):
        raise FoodLineOperatorRecoveryPublicGenerationError("preview manifest must be a JSON object")
    if manifest.get("manifest_version") != PREVIEW_MANIFEST_VERSION:
        raise FoodLineOperatorRecoveryPublicGenerationError("preview manifest version is invalid")
    if html_text.count('<article class="story">') != len(items):
        raise FoodLineOperatorRecoveryPublicGenerationError("preview rendered story count mismatch")
    if HOLD_ITEM_ID in html_text:
        raise FoodLineOperatorRecoveryPublicGenerationError("HOLD item leaked into preview")
    seen_headlines: set[str] = set()
    for item in items:
        for field in ("reviewed_headline", "reviewed_summary", "publisher", "source_url", "event_date"):
            value = str(item[field])
            if html.escape(value, quote=True) not in html_text and html.escape(value) not in html_text:
                raise FoodLineOperatorRecoveryPublicGenerationError(f"preview lost {field}: {value}")
        headline = str(item["reviewed_headline"])
        if headline in seen_headlines:
            raise FoodLineOperatorRecoveryPublicGenerationError("preview contains duplicate headline")
        seen_headlines.add(headline)
    return {
        "ok": True,
        "story_count": len(items),
        "headlines": [str(item["reviewed_headline"]) for item in items],
        "recovery_disclosure": RECOVERY_DISCLOSURE,
        "hold_absent": HOLD_ITEM_ID not in html_text,
        "internal_governance_metadata_exposed": False,
    }


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def generate_private_preview(
    root: Path,
    publication_path: Path,
    output_dir: Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    output = _assert_output_directory(root, output_dir)
    publication, publication_rel, publication_sha = _validate_publication(root, publication_path, expected_sha256)
    page_html = _render_html(publication)
    public_items = _public_items(publication)
    _prepare_output_dir(output)
    _write_text(output / "index.html", page_html)
    manifest = {
        "manifest_version": PREVIEW_MANIFEST_VERSION,
        "dispatch": "food-line",
        "authorized_item_count": publication["item_count"],
        "rendered_item_count": len(public_items),
        "hold_item_excluded": True,
        "placement": publication["publication_items"][0]["public_edition_date_or_placement"],
        "recovery_disclosure": RECOVERY_DISCLOSURE,
        "stories": public_items,
    }
    _write_json(output / "preview-manifest.json", manifest)
    inspection = _inspect_preview(output, publication)
    return {
        "ok": True,
        "status": "private_preview_generated",
        "publication_authorization_path": publication_rel.as_posix(),
        "publication_authorization_sha256": publication_sha,
        "preview_directory": str(output),
        "preview_files": ["index.html", "preview-manifest.json"],
        "rendered_headlines": inspection["headlines"],
        "recovery_disclosure": RECOVERY_DISCLOSURE,
        "inspection": inspection,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a private Food Line operator-recovery preview.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--publication-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = generate_private_preview(
            args.repo_root,
            args.publication_path,
            args.output_dir,
            expected_sha256=args.expected_sha256,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
