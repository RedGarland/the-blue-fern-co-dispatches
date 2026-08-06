from __future__ import annotations

import argparse
import json
from pathlib import Path

from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_template,
    render_homepage_from_template,
    render_sitewide_homepage_from_template,
    select_effective_latest,
    select_homepage_cards,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the Blue Fern root homepage latest-release section from verified public edition manifests.")
    parser.add_argument("--public-inventory-root", required=True, help="Root directory containing public dispatch output, such as a Pages checkout.")
    parser.add_argument("--template-html", required=True, help="Existing homepage HTML file to use as the shell template.")
    parser.add_argument("--output-html", required=True, help="Path to write the refreshed homepage HTML.")
    parser.add_argument("--target-dispatch", help="Surgically refresh only this dispatch across the current root surfaces.")
    parser.add_argument("--directory-template-html", help="Existing dispatch-directory HTML shell template.")
    parser.add_argument("--directory-output-html", help="Path to write the refreshed dispatch directory.")
    parser.add_argument("--audit-json", help="Optional path to write the discovered release inventory and selected cards.")
    args = parser.parse_args(argv)

    public_root = Path(args.public_inventory_root)
    template_path = Path(args.template_html)
    output_path = Path(args.output_html)
    audit_path = Path(args.audit_json) if args.audit_json else None

    template_html = template_path.read_text(encoding="utf-8")
    releases = discover_public_releases(public_root, verify_root=public_root, homepage_html=template_html)
    cards = select_homepage_cards(releases)
    latest = select_effective_latest(releases)
    if args.target_dispatch:
        release = latest.get(args.target_dispatch)
        if release is None:
            raise ValueError(f"No eligible public release found for {args.target_dispatch}")
        refreshed = render_sitewide_homepage_from_template(template_html, release)
    else:
        refreshed = render_homepage_from_template(template_html, cards)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(refreshed, encoding="utf-8")

    if bool(args.directory_template_html) != bool(args.directory_output_html):
        raise ValueError("--directory-template-html and --directory-output-html must be supplied together")
    directory_output_path: Path | None = None
    if args.directory_template_html and args.directory_output_html:
        if not args.target_dispatch:
            raise ValueError("--target-dispatch is required when refreshing the dispatch directory")
        directory_template = Path(args.directory_template_html).read_text(encoding="utf-8")
        directory_output_path = Path(args.directory_output_html)
        directory_output_path.parent.mkdir(parents=True, exist_ok=True)
        directory_output_path.write_text(
            render_dispatch_directory_from_template(directory_template, latest[args.target_dispatch]),
            encoding="utf-8",
        )

    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(
                {
                    "public_inventory_root": str(public_root),
                    "template_html": str(template_path),
                    "output_html": str(output_path),
                    "directory_output_html": str(directory_output_path) if directory_output_path else None,
                    "target_dispatch": args.target_dispatch,
                    "release_count": len(releases),
                    "card_count": len(cards),
                    "releases": [release.__dict__ for release in releases],
                    "cards": [card.__dict__ for card in cards],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
