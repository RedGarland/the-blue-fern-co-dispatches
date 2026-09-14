from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_template,
    render_homepage_from_template,
    select_effective_latest,
    select_homepage_cards,
)
from scripts.run_food_line_dispatch import FOOD_LINE_PAGE_DESCRIPTION, _update_index_archive


ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = ROOT / "output" / "site"
SEP10 = SITE_ROOT / "food-line" / "editions" / "2026-09-10"
SEP12 = SITE_ROOT / "food-line" / "editions" / "2026-09-12"
HOLD_ITEM_URL = "https://www.aacpl.net/event/mobile-food-pantry-despensa-movil-de-alimentos-240256"
HOLD_ITEM_TITLE = "Mobile Food Pantry | Despensa Movil de Alimentos"
HOLD_ITEM_ID = "finding_1cae6cc95cacbe13fa5eb7f8"
HOLD_ITEM_LABEL = "Eastport-Annapolis Neck"
APPROVED_HEADLINES = [
    "Hawaii Island Salvation Army pantries report staple shortages after Lala",
    "Eastport's only full-size grocery store remains without a committed reopening",
    "Puerto Rico water shutoffs add food-preparation and prepared-food costs",
    "Texas SNAP redetermination delays interrupted benefits for an East Texas household",
]
SEP12_LIVE_HASHES = {
    "index.html": "571459e531aac35f22a051bdcdef9f3d1765d5fd139e89a2e4ddeed62d628f0c",
    "edition_manifest.json": "1838f1c1a37e6eb63ea306b4217ba80f5bc5abe9fc61a7c12d85d170d4ffc17c",
    "sources_manifest.json": "9130ca39bce625cb18a9133cddf64b14ca9a49bae5ebed20f478d24efca0e5dc",
    "curation_manifest.json": "f3690206378b10fe3dfd5f63be29ba340151c665819cae4eb9a63f7fc4b8e4ed",
    "source_table.html": "13262ad2046b9ddf11b55ef2e250fe5689fcc229089dfdafd8843b7883b45cd7",
    "claim_ledger.html": "cb89d84e846cd7caa6ed2d6cbffaf4f2d14d572d32b99f8ef131fdea518ee6f0",
}


TEMPLATE_HTML = (
    '<!doctype html><html><body>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid"><article>stale</article></div></section>'
    "</body></html>"
)

DIRECTORY_TEMPLATE = (
    '<!doctype html><html><body><main><div class="directory-list">'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/gaza/editions/2026-09-13/">Gaza</a></h3>'
    '<p class="date-line">Dispatches From Gaza &middot; September 13, 2026</p><h2>Dispatches From Gaza</h2>'
    '<a class="button" href="/gaza/editions/2026-09-13/">Read latest</a></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/food-line/editions/2026-08-31/">Old Food</a></h3>'
    '<p class="date-line">Food Line Dispatch &middot; August 31, 2026</p><h2>Food Line Dispatch</h2>'
    '<a class="button" href="/food-line/editions/2026-08-31/">Read latest</a></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/care-line/editions/2026-08-20/">Care</a></h3>'
    '<p class="date-line">Care Line &middot; August 20, 2026</p><h2>The Care Line Dispatch</h2>'
    '<a class="button" href="/care-line/editions/2026-08-20/">Read latest</a></article>'
    "</div></main></body></html>"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_september_10_recovery_record_exists_and_is_not_a_normal_edition() -> None:
    html = (SEP10 / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((SEP10 / "edition_manifest.json").read_text(encoding="utf-8"))

    assert "Food Line &mdash; September 10, 2026 recovery record" in html
    assert "not a normal September 10 production edition" in html
    assert "scheduled September 10 Food Line production collection failed" in html
    assert "Five source-backed findings were accepted as valid recovery evidence" in html
    assert "Four passed editorial review for release" in html
    assert "One finding remained on editorial hold" in html
    assert "/food-line/editions/2026-09-12/" in html
    assert html.count('<article class="food-line-source-card">') == 4
    for headline in APPROVED_HEADLINES:
        assert headline in html
    assert HOLD_ITEM_URL not in html
    assert HOLD_ITEM_TITLE not in html
    assert HOLD_ITEM_ID not in html
    assert HOLD_ITEM_LABEL not in html

    assert manifest["record_type"] == "operator_recovery_historical_record"
    assert manifest["edition_date"] == "2026-09-10"
    assert manifest["public_release_status"] == "not_published"
    assert manifest["original_production_status"] == "failed"
    assert manifest["recovery_record_public"] is True
    assert manifest["recovered_finding_count"] == 5
    assert manifest["published_item_count"] == 4
    assert manifest["held_item_count"] == 1
    assert manifest["source_count"] == 4
    assert manifest["homepage_eligible"] is False
    assert manifest["rss_eligible"] is False
    assert manifest["publication_placement"] == "/food-line/editions/2026-09-12/"
    assert manifest["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-09-10/"


def test_recovery_record_does_not_displace_food_line_latest_surfaces() -> None:
    releases = discover_public_releases(SITE_ROOT, verify_root=SITE_ROOT, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)
    latest = select_effective_latest(releases)

    assert ("food-line", "2026-09-10") not in {(release.slug, release.edition_date) for release in releases}
    assert latest["food-line"].edition_date == "2026-09-12"

    root = render_homepage_from_template(TEMPLATE_HTML, select_homepage_cards(releases))
    directory = render_dispatch_directory_from_template(DIRECTORY_TEMPLATE, latest["food-line"])
    assert "/food-line/editions/2026-09-12/" in root
    assert "/food-line/editions/2026-09-12/" in directory
    assert "/food-line/editions/2026-09-10/" not in root
    assert "/food-line/editions/2026-09-10/" not in directory


def test_archive_and_rss_keep_recovery_record_separate() -> None:
    archive = (SITE_ROOT / "food-line" / "archive.html").read_text(encoding="utf-8")
    rss = (SITE_ROOT / "food-line" / "rss.xml").read_text(encoding="utf-8")

    assert "<h2>Recovery records</h2>" in archive
    assert "September 10, 2026 - recovery record" in archive
    assert "editions/2026-09-10/" in archive
    assert "editions/2026-09-12/" in archive
    assert "https://dispatches.thebluefernco.com/food-line/editions/2026-09-10/" not in rss
    assert "2026-09-10" not in rss
    assert "https://dispatches.thebluefernco.com/food-line/editions/2026-09-12/" in rss


def test_september_12_artifacts_match_live_pages_bytes() -> None:
    for filename, expected in SEP12_LIVE_HASHES.items():
        assert _sha256(SEP12 / filename) == expected


def test_isolated_food_line_archive_generation_preserves_recovery_record(tmp_path: Path) -> None:
    isolated = tmp_path / "repo"
    (isolated / "output" / "site").mkdir(parents=True)
    shutil.copytree(SITE_ROOT / "food-line", isolated / "output" / "site" / "food-line")

    sep10_before = {path.relative_to(isolated): _sha256(path) for path in (isolated / "output/site/food-line/editions/2026-09-10").iterdir()}
    sep12_before = {path.relative_to(isolated): _sha256(path) for path in (isolated / "output/site/food-line/editions/2026-09-12").iterdir()}

    _update_index_archive(isolated, "2026-09-12", FOOD_LINE_PAGE_DESCRIPTION)

    sep10_after = {path.relative_to(isolated): _sha256(path) for path in (isolated / "output/site/food-line/editions/2026-09-10").iterdir()}
    sep12_after = {path.relative_to(isolated): _sha256(path) for path in (isolated / "output/site/food-line/editions/2026-09-12").iterdir()}
    archive = (isolated / "output/site/food-line/archive.html").read_text(encoding="utf-8")
    rss = (isolated / "output/site/food-line/rss.xml").read_text(encoding="utf-8")

    assert sep10_after == sep10_before
    assert sep12_after == sep12_before
    assert "<h2>Recovery records</h2>" in archive
    assert "editions/2026-09-10/" in archive
    assert "editions/2026-09-12/" in archive
    assert "2026-09-10" not in rss
