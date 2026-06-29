from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_food_line_dispatch import DISPATCH_SLUG, PAGES_REPO, validate_date

REQUIRED_FILES = (
    "review_render_manifest.json",
    "index.html",
    "source_table.html",
    "claim_ledger.html",
)
LEAK_NEEDLES = ("WPDE", "ABC 15", "Tulsa Flyer", "WKRN")
DEFAULT_EXPECTED_SOURCE_URLS = {
    "2026-06-12": "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children"
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _copytree_replace(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def validate_review_only_publish_dir(
    *,
    root: Path,
    date: str,
    review_render_dir: Path,
    allow_multiple_claims: bool = False,
    expected_source_url: str | None = None,
) -> dict[str, Any]:
    edition_date = validate_date(date)
    render_dir = review_render_dir.resolve()
    errors: list[str] = []
    checks: dict[str, Any] = {}
    if not render_dir.exists():
        raise ValueError(f"review render directory not found: {render_dir}")

    missing = [name for name in REQUIRED_FILES if not (render_dir / name).exists()]
    if missing:
        raise ValueError(f"review render directory is missing required files: {', '.join(missing)}")

    manifest_path = render_dir / "review_render_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"review render manifest must be an object: {manifest_path}")

    checks["manifest_ok"] = bool(manifest.get("ok"))
    if not checks["manifest_ok"]:
        errors.append("review render manifest ok must be true")
    checks["render_mode"] = str(manifest.get("render_mode") or "")
    if checks["render_mode"] != "review_only":
        errors.append(f"review render manifest render_mode must be review_only, got {checks['render_mode'] or 'empty'}")
    checks["manifest_edition_date"] = str(manifest.get("edition_date") or "")
    if checks["manifest_edition_date"] != edition_date:
        errors.append(f"review render manifest edition_date {checks['manifest_edition_date'] or 'empty'} does not match {edition_date}")
    checks["rendered_public_claim_count"] = int(manifest.get("rendered_public_claim_count") or 0)
    if checks["rendered_public_claim_count"] <= 0:
        errors.append("review render manifest rendered_public_claim_count must be at least 1")
    if checks["rendered_public_claim_count"] != 1 and not allow_multiple_claims:
        errors.append(
            f"review render manifest rendered_public_claim_count must be exactly 1 unless --allow-multiple-claims is supplied; got {checks['rendered_public_claim_count']}"
        )
    checks["source_count"] = int(manifest.get("source_count") or 0)
    if checks["source_count"] < 1:
        errors.append("review render manifest source_count must be at least 1")
    checks["production_output_mutated"] = bool(manifest.get("production_output_mutated"))
    if checks["production_output_mutated"]:
        errors.append("review render manifest reports production_output_mutated=true")
    checks["pages_repo_mutated"] = bool(manifest.get("pages_repo_mutated"))
    if checks["pages_repo_mutated"]:
        errors.append("review render manifest reports pages_repo_mutated=true")
    checks["source_table_exists"] = bool(manifest.get("source_table_exists"))
    if not checks["source_table_exists"]:
        errors.append("review render manifest source_table_exists must be true")
    checks["claim_ledger_exists"] = bool(manifest.get("claim_ledger_exists"))
    if not checks["claim_ledger_exists"]:
        errors.append("review render manifest claim_ledger_exists must be true")

    html_files = [render_dir / "index.html", render_dir / "source_table.html", render_dir / "claim_ledger.html"]
    html_texts = {path.name: path.read_text(encoding="utf-8") for path in html_files}
    combined_html = "\n".join(html_texts.values())
    resolved_expected_source_url = expected_source_url or DEFAULT_EXPECTED_SOURCE_URLS.get(edition_date) or str(manifest.get("lead_source_url") or "")
    checks["expected_source_url"] = resolved_expected_source_url
    if not resolved_expected_source_url:
        errors.append("no expected source URL available for review-only publish validation")
    else:
        checks["expected_source_url_in_all_rendered_files"] = all(resolved_expected_source_url in text for text in html_texts.values())
        if not checks["expected_source_url_in_all_rendered_files"]:
            errors.append(f"expected source URL not present in all rendered files: {resolved_expected_source_url}")
    leak_hits = [needle for needle in LEAK_NEEDLES if needle in combined_html]
    checks["leak_hits"] = leak_hits
    if leak_hits:
        errors.append(f"review render contains out-of-scope leak content: {', '.join(leak_hits)}")

    output_site_target = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    pages_target = root / "bluefern-dispatches-pages" / DISPATCH_SLUG / "editions" / edition_date
    checks["output_site_target"] = str(output_site_target)
    checks["pages_target"] = str(pages_target)
    checks["archive_homepage_rss_podcast_impacts"] = {
        "archive": "none",
        "homepage": "none",
        "rss": "none",
        "podcast": "none",
        "audio": "none",
        "bluesky": "none",
    }

    return {
        "ok": not errors,
        "edition_date": edition_date,
        "review_render_dir": str(render_dir),
        "manifest": manifest,
        "checks": checks,
        "errors": errors,
    }


def publish_review_only_render(
    *,
    root: Path,
    date: str,
    review_render_dir: Path,
    publish_to_output_site: bool = False,
    publish_to_pages: bool = False,
    allow_multiple_claims: bool = False,
    expected_source_url: str | None = None,
    pages_repo: Path | None = None,
) -> dict[str, Any]:
    validation = validate_review_only_publish_dir(
        root=root,
        date=date,
        review_render_dir=review_render_dir,
        allow_multiple_claims=allow_multiple_claims,
        expected_source_url=expected_source_url,
    )
    dry_run = not (publish_to_output_site or publish_to_pages)
    planned_targets: list[str] = []
    copied_targets: list[str] = []
    checks = dict(validation["checks"])
    render_dir = Path(validation["review_render_dir"])
    edition_date = str(validation["edition_date"])
    output_site_target = Path(checks["output_site_target"])
    effective_pages_repo = (pages_repo or PAGES_REPO).resolve()
    pages_target = effective_pages_repo / DISPATCH_SLUG / "editions" / edition_date
    checks["pages_repo_root"] = str(effective_pages_repo)
    checks["pages_target"] = str(pages_target)

    if publish_to_output_site:
        planned_targets.append(str(output_site_target))
    if publish_to_pages:
        planned_targets.append(str(pages_target))

    result = {
        "ok": bool(validation["ok"]),
        "dry_run": dry_run,
        "edition_date": edition_date,
        "review_render_dir": str(render_dir),
        "publish_to_output_site": bool(publish_to_output_site),
        "publish_to_pages": bool(publish_to_pages),
        "validation_checks": checks,
        "planned_targets": planned_targets,
        "copied_targets": copied_targets,
        "production_output_mutated": False,
        "pages_repo_mutated": False,
        "committed": False,
        "pushed": False,
        "errors": list(validation["errors"]),
    }
    if not validation["ok"]:
        return result

    if dry_run:
        return result

    if publish_to_output_site:
        _copytree_replace(render_dir, output_site_target)
        copied_targets.append(str(output_site_target))
        result["production_output_mutated"] = True
    if publish_to_pages:
        _copytree_replace(render_dir, pages_target)
        copied_targets.append(str(pages_target))
        result["pages_repo_mutated"] = True
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and optionally copy a Food Line review-only render into publish targets.")
    parser.add_argument("--date", required=True, help="Edition date YYYY-MM-DD")
    parser.add_argument("--review-render-dir", required=True, help="Path to the review-only rendered edition directory")
    parser.add_argument("--publish-to-output-site", action="store_true", help="Copy only the edition directory into output/site/food-line/editions/<date>/")
    parser.add_argument("--publish-to-pages", action="store_true", help="Copy only the edition directory into bluefern-dispatches-pages/food-line/editions/<date>/ without commit or push")
    parser.add_argument("--allow-multiple-claims", action="store_true", help="Allow rendered_public_claim_count values other than exactly 1")
    parser.add_argument("--expected-source-url", help="Override the expected source URL that must appear in the rendered files")
    parser.add_argument("--pages-repo", help="Optional override for the local Pages repo root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = publish_review_only_render(
            root=Path.cwd(),
            date=str(args.date),
            review_render_dir=Path(str(args.review_render_dir)),
            publish_to_output_site=bool(args.publish_to_output_site),
            publish_to_pages=bool(args.publish_to_pages),
            allow_multiple_claims=bool(args.allow_multiple_claims),
            expected_source_url=str(args.expected_source_url or "").strip() or None,
            pages_repo=Path(str(args.pages_repo)).resolve() if args.pages_repo else None,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
