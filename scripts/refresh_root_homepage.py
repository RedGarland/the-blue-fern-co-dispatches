from __future__ import annotations

import argparse
import json
from pathlib import Path

from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_homepage_from_template,
    select_homepage_cards,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the Blue Fern root homepage latest-release section from verified public edition manifests.")
    parser.add_argument("--public-inventory-root", required=True, help="Root directory containing public dispatch output, such as a Pages checkout.")
    parser.add_argument("--template-html", required=True, help="Existing homepage HTML file to use as the shell template.")
    parser.add_argument("--output-html", required=True, help="Path to write the refreshed homepage HTML.")
    parser.add_argument("--audit-json", help="Optional path to write the discovered release inventory and selected cards.")
    args = parser.parse_args(argv)

    public_root = Path(args.public_inventory_root)
    template_path = Path(args.template_html)
    output_path = Path(args.output_html)
    audit_path = Path(args.audit_json) if args.audit_json else None

    template_html = template_path.read_text(encoding="utf-8")
    releases = discover_public_releases(public_root, verify_root=public_root, homepage_html=template_html)
    cards = select_homepage_cards(releases)
    refreshed = render_homepage_from_template(template_html, cards)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(refreshed, encoding="utf-8")

    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(
                {
                    "public_inventory_root": str(public_root),
                    "template_html": str(template_path),
                    "output_html": str(output_path),
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
