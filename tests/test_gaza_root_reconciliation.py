import json
import re
from datetime import date
from pathlib import Path

import pytest

from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_template,
    render_sitewide_homepage_from_template,
    select_effective_latest,
)


HEADLINE = "Mass funeral held in Gaza for victims of 2023 Israeli strike"
EDITION_URL = "/gaza/editions/2026-08-05/"

FOOD_ROOT_CARD = (
    '<article class="dispatch-card dispatch-card--featured"><p class="status">Active</p>'
    '<p class="latest-label">Latest public development</p><h3 class="latest-headline">'
    '<a href="/food-line/editions/2026-07-31/">Superior food pantry closes after more than 30 years</a></h3>'
    '<p class="date-line">Food Line Dispatch &middot; July 31, 2026</p><h2>Food Line Dispatch</h2>'
    '<div class="card-actions"><a class="button" href="/food-line/editions/2026-07-31/">Read latest</a></div></article>'
)
CARE_ROOT_CARD = (
    '<article class="dispatch-card dispatch-card--featured"><p class="status">Active</p>'
    '<p class="latest-label">Latest public development</p><h3 class="latest-headline">'
    '<a href="/care-line/editions/2026-08-05/">Miles Hospital proposes closing its labor and delivery center</a></h3>'
    '<p class="date-line">Care Line &middot; August 5, 2026</p><h2>The Care Line Dispatch</h2>'
    '<div class="card-actions"><a class="button" href="/care-line/editions/2026-08-05/">Read latest</a></div></article>'
)
GAZA_STALE_CARD = (
    '<article class="dispatch-card dispatch-card--featured"><p class="status">Active</p>'
    '<p class="latest-label">Latest public development</p><h3 class="latest-headline">'
    '<a href="/gaza/editions/2026-08-03/">Stale Gaza headline</a></h3>'
    '<p class="date-line">Dispatches From Gaza &middot; Aug 3, 2026 &middot; 6:00 AM PT</p>'
    '<h2>Dispatches From Gaza</h2><div class="card-actions">'
    '<a class="button" href="/gaza/editions/2026-08-03/">Read latest</a></div></article>'
)

ROOT_TEMPLATE = (
    '<!doctype html><html><body><header class="site-header"><nav aria-label="Primary">'
    '<a href="/">Home</a><a href="/dispatches/">Dispatches</a><a href="/methodology/">Methodology</a>'
    '<a href="/about/">About</a></nav></header><main><section class="section-block">'
    '<div class="edition-grid"><article class="edition-card edition-card--gaza">'
    '<p class="topic-badge topic-badge--gaza">GAZA</p><h3><a href="/gaza/editions/2026-08-05/">'
    f'{HEADLINE}</a></h3><p class="edition-source">Dispatches From Gaza &middot; August 5, 2026 &middot; 6:00 AM PT</p>'
    '<p class="edition-provenance">Based on public source reporting</p><p class="edition-meta">7 public sources</p></article>'
    '<article class="edition-card edition-card--food-line"><h3>Food unchanged</h3></article>'
    '<article class="edition-card edition-card--care-line"><h3>Care unchanged</h3></article></div></section>'
    f'<section class="section-block"><div class="active-grid">{GAZA_STALE_CARD}{FOOD_ROOT_CARD}{CARE_ROOT_CARD}'
    '</div></section></main></body></html>'
)

DIRECTORY_TEMPLATE = (
    '<!doctype html><html><body><header class="site-header"><nav aria-label="Primary">'
    '<a href="/">Home</a><a href="/dispatches/">Dispatches</a><a href="/methodology/">Methodology</a>'
    '<a href="/about/">About</a></nav></header><main><div class="directory-list">'
    f'{GAZA_STALE_CARD}{FOOD_ROOT_CARD}{CARE_ROOT_CARD}</div></main></body></html>'
)


def _write_gaza_release(
    root: Path,
    edition_date: str,
    *,
    title: str,
    public_release_status: str = "published",
    pages_release_status: str = "synced",
    archive_linked: bool = True,
    source_count: int = 7,
    selected_supporting_source_count: int = 2,
) -> None:
    edition_dir = root / "gaza" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text(f"<html><body><article><h3>{title}</h3></article></body></html>", encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "gaza",
                "edition_date": edition_date,
                "public_url": f"https://dispatches.thebluefernco.com/gaza/editions/{edition_date}/",
                "source_count": source_count,
                "raw_candidate_count": 4,
                "selected_supporting_source_count": selected_supporting_source_count,
                "public_release_status": public_release_status,
                "pages_release_status": pages_release_status,
                "actual_run_local_time": f"{edition_date}T06:00:00-07:00",
            }
        ),
        encoding="utf-8",
    )
    (edition_dir / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "public_rendered": True,
                    "source_record_ids": ["guardian", "bbc"],
                    "publisher_names": ["The Guardian", "BBC News"],
                }
            ]
        ),
        encoding="utf-8",
    )
    dispatch_root = root / "gaza"
    dispatch_root.mkdir(parents=True, exist_ok=True)
    link = f'<a href="editions/{edition_date}/">{title}</a>' if archive_linked else ""
    archive = dispatch_root / "archive.html"
    existing = archive.read_text(encoding="utf-8") if archive.exists() else ""
    archive.write_text(existing + link, encoding="utf-8")


def _gaza_release(root: Path):
    releases = discover_public_releases(root, verify_root=root, as_of=date(2026, 8, 6), homepage_html=ROOT_TEMPLATE)
    return select_effective_latest(releases)["gaza"]


def _without_gaza_articles(value: str) -> str:
    return re.sub(
        r'<article\b(?:(?!</article>).)*(?:edition-card--gaza|Dispatches From Gaza)(?:(?!</article>).)*</article>',
        "<GAZA_ARTICLE/>",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )


@pytest.mark.parametrize("status", ["withheld", "suppressed", "failed", "unpublished"])
def test_effective_latest_excludes_invalid_august_6_statuses(tmp_path, status):
    _write_gaza_release(tmp_path, "2026-08-05", title=HEADLINE)
    _write_gaza_release(tmp_path, "2026-08-06", title="Invalid August 6", public_release_status=status)

    release = _gaza_release(tmp_path)

    assert release.edition_date == "2026-08-05"
    assert release.title == HEADLINE


def test_effective_latest_excludes_date_removed_from_public_archive(tmp_path):
    _write_gaza_release(tmp_path, "2026-08-05", title=HEADLINE)
    _write_gaza_release(tmp_path, "2026-08-06", title="Unlisted August 6", archive_linked=False)
    stale_link = '<a href="editions/2026-08-06/">Stale non-archive listing</a>'
    (tmp_path / "gaza" / "index.html").write_text(stale_link, encoding="utf-8")
    (tmp_path / "gaza" / "rss.xml").write_text(f"<rss>{stale_link}</rss>", encoding="utf-8")

    release = _gaza_release(tmp_path)

    assert release.edition_date == "2026-08-05"


def test_gaza_root_source_count_uses_selected_supporting_sources(tmp_path):
    _write_gaza_release(tmp_path, "2026-08-05", title=HEADLINE, source_count=7, selected_supporting_source_count=2)
    manifest_path = tmp_path / "gaza" / "editions" / "2026-08-05" / "edition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["actual_run_local_time"] = "2026-08-06T12:39:39-07:00"
    manifest["scheduled_run_local_time"] = "2026-08-05T06:00:00-07:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    release = _gaza_release(tmp_path)
    rendered = render_sitewide_homepage_from_template(ROOT_TEMPLATE, release)

    assert release.source_count == 2
    assert "2 public sources" in rendered
    assert "4 public sources" not in rendered
    assert "7 public sources" not in rendered
    assert "August 5, 2026 &middot; 6:00 AM PT" in rendered
    assert "12:39 PM PT" not in rendered


def test_root_surfaces_reconcile_gaza_and_preserve_every_non_gaza_byte(tmp_path):
    _write_gaza_release(tmp_path, "2026-08-05", title=HEADLINE)
    _write_gaza_release(tmp_path, "2026-08-06", title="Withheld August 6", public_release_status="withheld")
    release = _gaza_release(tmp_path)

    homepage = render_sitewide_homepage_from_template(ROOT_TEMPLATE, release)
    directory = render_dispatch_directory_from_template(DIRECTORY_TEMPLATE, release)

    for rendered in (homepage, directory):
        assert HEADLINE in rendered
        assert EDITION_URL in rendered
        assert "August 5, 2026" in rendered
        assert "Stale Gaza headline" not in rendered
        assert "2026-08-03" not in rendered
        assert "2026-08-06" not in rendered
        assert "Withheld August 6" not in rendered
        assert "American Pressure" not in rendered
        assert '<nav aria-label="Primary">' in rendered
        assert 'class="dispatch-card dispatch-card--featured"' in rendered

    assert "2 public sources" in homepage
    assert _without_gaza_articles(homepage) == _without_gaza_articles(ROOT_TEMPLATE)
    assert _without_gaza_articles(directory) == _without_gaza_articles(DIRECTORY_TEMPLATE)
    assert FOOD_ROOT_CARD in homepage and FOOD_ROOT_CARD in directory
    assert CARE_ROOT_CARD in homepage and CARE_ROOT_CARD in directory
    assert sorted(re.findall(r'class="([^"]+)"', homepage)) == sorted(re.findall(r'class="([^"]+)"', ROOT_TEMPLATE))
    assert sorted(re.findall(r'class="([^"]+)"', directory)) == sorted(re.findall(r'class="([^"]+)"', DIRECTORY_TEMPLATE))
