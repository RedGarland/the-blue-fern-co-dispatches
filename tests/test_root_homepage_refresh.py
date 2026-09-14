import json
import re
from datetime import date
from pathlib import Path

import pytest

from scripts import refresh_root_homepage
from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_releases,
    render_homepage_from_template,
    render_sitewide_homepage_from_template,
    select_effective_latest,
    select_homepage_cards,
)
from bluefern_dispatches.generator import refresh_shared_release_surfaces_from_pages_inventory


TEMPLATE_HTML = (
    '<!doctype html><html><body>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid"><article>stale</article></div></section>'
    '<section class="section-block section-block--quiet"><h2>Unrelated section</h2><p>Keep me stable.</p></section>'
    "</body></html>"
)

DIRECTORY_TEMPLATE = (
    '<!doctype html><html><body><main><div class="directory-list">'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/gaza/editions/2026-08-05/">Old Gaza</a></h3>'
    '<p class="date-line">Dispatches From Gaza &middot; August 5, 2026</p><h2>Dispatches From Gaza</h2>'
    '<a class="button" href="/gaza/editions/2026-08-05/">Read latest</a></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/food-line/editions/2026-08-05/">Old Food</a></h3>'
    '<p class="date-line">Food Line Dispatch &middot; August 5, 2026</p><h2>Food Line Dispatch</h2>'
    '<a class="button" href="/food-line/editions/2026-08-05/">Read latest</a></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/care-line/editions/2026-08-05/">Old Care</a></h3>'
    '<p class="date-line">Care Line &middot; August 5, 2026</p><h2>The Care Line Dispatch</h2>'
    '<a class="button" href="/care-line/editions/2026-08-05/">Read latest</a></article>'
    '</div></main><footer><a href="/methodology/">How we work</a> Ã‚Â· <a href="/about/">About this project</a></footer></body></html>'
)

SHARED_ROOT_TEMPLATE = DIRECTORY_TEMPLATE.replace(
    "<main>",
    '<main><section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid">'
    '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-08-05/">Old Gaza</a></h3>'
    '<p class="edition-source">Dispatches From Gaza &middot; August 5, 2026</p>'
    '<p class="edition-meta">1 public source</p></article></div></section>',
    1,
)

MOJIBAKE_SEPARATOR = "\u00c3\u201a\u00c2\u00b7"


def _dispatch_card(html: str, product_name: str) -> str:
    match = re.search(
        rf'<article class="dispatch-card dispatch-card--featured">(?:(?!</article>).)*?<h2>{re.escape(product_name)}</h2>(?:(?!</article>).)*?</article>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _edition_card(html: str, slug: str) -> str:
    match = re.search(rf'<article class="edition-card edition-card--{re.escape(slug)}">.*?</article>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


def _write_release(
    root: Path,
    slug: str,
    edition_date: str,
    *,
    title: str,
    source_count: int,
    public_release_status: str | None = "published",
    pages_release_status: str | None = "synced",
    time_key: str | None = None,
    time_value: str | None = None,
    archive_linked: bool = True,
    include_manifest: bool = True,
) -> None:
    edition_dir = root / slug / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    title_line = (
        f"<p><em>Source: <a href=\"https://example.com/{slug}/{edition_date}\">{title}</a> (Example)</em></p>"
        if slug == "american-pressure"
        else f"<article><h3>{title}</h3></article>"
    )
    (edition_dir / "index.html").write_text(f"<html><body>{title_line}</body></html>", encoding="utf-8")
    (edition_dir / "sources_manifest.json").write_text(json.dumps([{"title": title}] * source_count), encoding="utf-8")
    manifest = {
        "dispatch_slug": slug,
        "edition_date": edition_date,
        "public_url": f"https://dispatches.thebluefernco.com/{slug}/editions/{edition_date}/",
        "source_count": source_count,
        "public_archive_title": title if slug in {"care-line", "food-line"} else "",
    }
    if public_release_status is not None:
        manifest["public_release_status"] = public_release_status
    if pages_release_status is not None:
        manifest["pages_release_status"] = pages_release_status
    if time_key and time_value:
        manifest[time_key] = time_value
    if include_manifest:
        (edition_dir / "edition_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dispatch_root = root / slug
    dispatch_root.mkdir(parents=True, exist_ok=True)
    linked = f'<li><a href="editions/{edition_date}/">{title}</a></li>' if archive_linked else ""
    for filename, prefix, suffix in (
        ("archive.html", "<html><body>", "</body></html>"),
        ("index.html", "<html><body>", "</body></html>"),
        ("rss.xml", "<rss>", "</rss>"),
    ):
        path = dispatch_root / filename
        existing = path.read_text(encoding="utf-8") if path.exists() else prefix + suffix
        path.write_text(existing.replace(suffix, linked + suffix), encoding="utf-8")


def _write_recovery_shaped_food_release(root: Path, edition_date: str) -> None:
    edition_dir = root / "food-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text(
        """
        <html><body>
        <h1>Food Line recovery-disclosed publication</h1>
        <article class="story">
          <h2>Hawaii Island Salvation Army pantries report staple shortages after Lala</h2>
        </article>
        </body></html>
        """,
        encoding="utf-8",
    )
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": edition_date,
                "public_url": f"https://dispatches.thebluefernco.com/food-line/editions/{edition_date}/",
                "source_count": 4,
                "publication_status": "published_pending_live_verification",
            }
        ),
        encoding="utf-8",
    )
    (edition_dir / "sources_manifest.json").write_text(json.dumps([{"title": f"Source {i}"} for i in range(4)]), encoding="utf-8")
    dispatch_root = root / "food-line"
    dispatch_root.mkdir(parents=True, exist_ok=True)
    for filename, prefix, suffix in (
        ("archive.html", "<html><body>", "</body></html>"),
        ("index.html", "<html><body>", "</body></html>"),
        ("rss.xml", "<rss>", "</rss>"),
    ):
        (dispatch_root / filename).write_text(
            f'{prefix}<a href="editions/{edition_date}/">Recovery-disclosed Food Line publication</a>{suffix}',
            encoding="utf-8",
        )


def _write_html_backed_release(
    root: Path,
    slug: str,
    edition_date: str,
    *,
    body: str,
    source_count: int = 1,
    manifest_title: str = "",
) -> None:
    edition_dir = root / slug / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    (edition_dir / "sources_manifest.json").write_text(json.dumps([{"title": f"Source {i}"} for i in range(source_count)]), encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": slug,
                "edition_date": edition_date,
                "public_url": f"https://dispatches.thebluefernco.com/{slug}/editions/{edition_date}/",
                "source_count": source_count,
                "public_archive_title": manifest_title,
                "public_release_status": "published",
                "pages_release_status": "synced",
            }
        ),
        encoding="utf-8",
    )
    dispatch_root = root / slug
    dispatch_root.mkdir(parents=True, exist_ok=True)
    for filename, prefix, suffix in (
        ("archive.html", "<html><body>", "</body></html>"),
        ("index.html", "<html><body>", "</body></html>"),
        ("rss.xml", "<rss>", "</rss>"),
    ):
        (dispatch_root / filename).write_text(f'{prefix}<a href="editions/{edition_date}/">listed</a>{suffix}', encoding="utf-8")


def test_homepage_refresh_prefers_gaza_article_h3_over_generic_edition_h1(tmp_path):
    public_root = tmp_path / "pages"
    _write_html_backed_release(
        public_root,
        "gaza",
        "2026-09-14",
        body=(
            "<h1>Dispatches From Gaza</h1>"
            "<article class=\"edition-story\"><h3>Pregnant woman among more than 10 killed in Israeli attacks on Gaza</h3></article>"
        ),
        source_count=2,
    )

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 14), homepage_html=TEMPLATE_HTML)

    assert releases[0].title == "Pregnant woman among more than 10 killed in Israeli attacks on Gaza"
    assert releases[0].title != "Dispatches From Gaza"


def test_homepage_refresh_prefers_care_signal_h3_over_generic_edition_h1(tmp_path):
    public_root = tmp_path / "pages"
    _write_html_backed_release(
        public_root,
        "care-line",
        "2026-08-20",
        body=(
            "<h1>The Care Line Dispatch</h1>"
            "<article class=\"signal-card\"><h3><a href=\"https://example.com/care\">Methodist Hospitals Gary outage update</a></h3></article>"
        ),
        source_count=1,
        manifest_title="Limited-source update",
    )
    manifest_path = public_root / "care-line" / "editions" / "2026-08-20" / "edition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_adequacy_label"] = "Limited-source update"
    manifest["source_adequacy_status"] = "LIMITED_SOURCE_UPDATE"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 14), homepage_html=TEMPLATE_HTML)

    assert releases[0].title == "Methodist Hospitals Gary outage update"
    assert releases[0].title not in {"The Care Line Dispatch", "Limited-source update"}


def test_homepage_refresh_discovers_all_active_products_and_fills_extra_slots(tmp_path):
    public_root = tmp_path / "pages"
    homepage = TEMPLATE_HTML
    _write_release(public_root, "gaza", "2026-08-05", title="Gaza latest", source_count=7, time_key="actual_run_local_time", time_value="2026-08-05T06:00:42-07:00")
    _write_release(public_root, "gaza", "2026-08-04", title="Gaza prior", source_count=5, time_key="actual_run_local_time", time_value="2026-08-04T06:00:40-07:00")
    _write_release(public_root, "gaza", "2026-08-03", title="Gaza third", source_count=4, time_key="actual_run_local_time", time_value="2026-08-03T06:00:40-07:00")
    _write_release(public_root, "food-line", "2026-07-31", title="Superior food pantry closes after more than 30 years", source_count=1)
    _write_release(public_root, "care-line", "2026-08-05", title="Miles Hospital proposes closing its labor and delivery center", source_count=1)
    _write_release(public_root, "cascadia", "2026-05-03", title="Washington bridge inspection program flags transportation maintenance backlog", source_count=2)
    _write_release(public_root, "american-pressure", "2026-06-15", title="Arizona summer electricity bills to rise: Tips and financial aid options", source_count=10)

    releases = discover_public_releases(public_root, verify_root=None, as_of=date(2026, 8, 5), homepage_html=homepage)
    cards = select_homepage_cards(releases)
    rendered = render_homepage_from_template(homepage, cards)

    assert {card.slug for card in cards} == {"gaza", "food-line", "care-line"}
    assert len(cards) == 5
    assert [card.relative_url for card in cards] == [
        "/gaza/editions/2026-08-05/",
        "/care-line/editions/2026-08-05/",
        "/gaza/editions/2026-08-04/",
        "/gaza/editions/2026-08-03/",
        "/food-line/editions/2026-07-31/",
    ]
    assert "CARE LINE" in rendered
    assert "Miles Hospital proposes closing its labor and delivery center" in rendered
    assert "Care Line &middot; August 5, 2026" in rendered
    assert "1 public source" in rendered
    assert "Gaza latest" in rendered
    assert "Dispatches From Gaza &middot; August 5, 2026 &middot; 6:00 AM PT" in rendered
    assert "AMERICAN PRESSURE" not in rendered
    assert "CASCADIA" not in rendered
    assert "Unrelated section" in rendered
    assert "Keep me stable." in rendered


def test_homepage_refresh_excludes_future_unpublished_and_signal_wire_like_records(tmp_path):
    public_root = tmp_path / "pages"
    homepage = TEMPLATE_HTML
    _write_release(public_root, "gaza", "2026-08-05", title="Gaza latest", source_count=7)
    _write_release(public_root, "food-line", "2026-07-31", title="Food latest", source_count=1)
    _write_release(public_root, "care-line", "2026-08-06", title="Future care", source_count=1)
    _write_release(public_root, "american-pressure", "2026-06-15", title="AP latest", source_count=10, public_release_status="not_published", pages_release_status="not_synced")
    events_dir = public_root / "events" / "event_deadbeef"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "index.html").write_text("<html><body>signal wire event</body></html>", encoding="utf-8")

    releases = discover_public_releases(public_root, verify_root=None, as_of=date(2026, 8, 5), homepage_html=homepage)

    assert all(release.edition_date <= "2026-08-05" for release in releases)
    assert all(release.slug != "american-pressure" for release in releases)
    assert all("events/" not in release.relative_url for release in releases)


def test_homepage_refresh_uses_edition_h1_for_recovery_shaped_food_release(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(
        public_root,
        "food-line",
        "2026-08-31",
        title="Phoenix food-box demand rose as Arizona SNAP access contracted",
        source_count=3,
        public_release_status="approved_pending_pages_publication",
        pages_release_status="not_synced",
    )
    _write_recovery_shaped_food_release(public_root, "2026-09-12")

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)
    latest = select_effective_latest(releases)

    food = latest["food-line"]
    assert food.edition_date == "2026-09-12"
    assert food.title == "Food Line recovery-disclosed publication"
    assert food.source_count == 4
    assert "Hawaii Island Salvation Army pantries" not in food.title


def test_homepage_refresh_manifest_title_precedence_over_h1(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(public_root, "food-line", "2026-09-12", title="Manifest title wins", source_count=4)
    index_path = public_root / "food-line" / "editions" / "2026-09-12" / "index.html"
    index_path.write_text("<html><body><h1>Rendered title loses</h1><article><h3>Fallback loses</h3></article></body></html>", encoding="utf-8")

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)

    assert releases[0].title == "Manifest title wins"


def test_homepage_refresh_keeps_edition_h1_as_fallback_when_no_h3_exists(tmp_path):
    public_root = tmp_path / "pages"
    _write_html_backed_release(
        public_root,
        "food-line",
        "2026-09-12",
        body="<h1>Food Line recovery-disclosed publication</h1><article class=\"story\"><h2>Story heading is not a resolver h3</h2></article>",
        source_count=4,
    )

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)

    assert releases[0].title == "Food Line recovery-disclosed publication"


def test_homepage_refresh_keeps_existing_h3_and_list_fallbacks(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(public_root, "gaza", "2026-09-12", title="H3 fallback title", source_count=2)
    food_dir = public_root / "food-line" / "editions" / "2026-09-12"
    food_dir.mkdir(parents=True)
    (food_dir / "index.html").write_text('<html><body><li><a href="story.html">List fallback title</a></li></body></html>', encoding="utf-8")
    (food_dir / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "food-line", "edition_date": "2026-09-12", "source_count": 1, "public_release_status": "published"}),
        encoding="utf-8",
    )
    (food_dir / "sources_manifest.json").write_text(json.dumps([{"title": "Source"}]), encoding="utf-8")
    (public_root / "food-line" / "archive.html").parent.mkdir(parents=True, exist_ok=True)
    (public_root / "food-line" / "archive.html").write_text('<a href="editions/2026-09-12/">listed</a>', encoding="utf-8")

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)
    by_slug = {release.slug: release for release in releases}

    assert by_slug["gaza"].title == "H3 fallback title"
    assert by_slug["food-line"].title == "List fallback title"


def test_homepage_refresh_excludes_food_line_recovery_record_from_latest_selection() -> None:
    public_root = Path(__file__).resolve().parents[1] / "output" / "site"

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)
    latest = select_effective_latest(releases)

    assert ("food-line", "2026-09-10") not in {(release.slug, release.edition_date) for release in releases}
    assert latest["food-line"].edition_date == "2026-09-12"
    assert latest["food-line"].title == "Food Line recovery-disclosed publication"


def test_homepage_refresh_titleless_release_still_fails_closed(tmp_path):
    public_root = tmp_path / "pages"
    edition_dir = public_root / "food-line" / "editions" / "2026-09-12"
    edition_dir.mkdir(parents=True)
    (edition_dir / "index.html").write_text("<html><body><article><p>No title here</p></article></body></html>", encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "food-line", "edition_date": "2026-09-12", "source_count": 4, "public_release_status": "published"}),
        encoding="utf-8",
    )
    (edition_dir / "sources_manifest.json").write_text(json.dumps([{"title": "Source"}] * 4), encoding="utf-8")
    dispatch_root = public_root / "food-line"
    dispatch_root.mkdir(parents=True, exist_ok=True)
    (dispatch_root / "archive.html").write_text('<a href="editions/2026-09-12/">listed</a>', encoding="utf-8")

    assert discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML) == []


def test_homepage_card_rendering_uses_descriptive_story_titles_for_gaza_and_care(tmp_path):
    public_root = tmp_path / "pages"
    _write_html_backed_release(
        public_root,
        "gaza",
        "2026-09-14",
        body="<h1>Dispatches From Gaza</h1><article><h3>Pregnant woman among more than 10 killed in Israeli attacks on Gaza</h3></article>",
        source_count=2,
    )
    _write_html_backed_release(
        public_root,
        "food-line",
        "2026-09-12",
        body="<h1>Food Line recovery-disclosed publication</h1>",
        source_count=4,
    )
    _write_html_backed_release(
        public_root,
        "care-line",
        "2026-08-20",
        body="<h1>The Care Line Dispatch</h1><article class=\"signal-card\"><h3>Methodist Hospitals Gary outage update</h3></article>",
        source_count=1,
    )

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 14), homepage_html=SHARED_ROOT_TEMPLATE)
    latest = select_effective_latest(releases)
    rendered_root = render_homepage_from_template(TEMPLATE_HTML, select_homepage_cards(releases))
    rendered_directory = render_dispatch_directory_from_releases(DIRECTORY_TEMPLATE, latest)

    assert "Pregnant woman among more than 10 killed in Israeli attacks on Gaza" in _edition_card(rendered_root, "gaza")
    assert "Methodist Hospitals Gary outage update" in _edition_card(rendered_root, "care-line")
    assert "Food Line recovery-disclosed publication" in _edition_card(rendered_root, "food-line")
    assert "Pregnant woman among more than 10 killed in Israeli attacks on Gaza" in _dispatch_card(rendered_directory, "Dispatches From Gaza")
    assert "Methodist Hospitals Gary outage update" in _dispatch_card(rendered_directory, "The Care Line Dispatch")


def test_homepage_card_selection_preserves_represented_grid_slots_before_unrepresented_fillers(tmp_path):
    public_root = tmp_path / "pages"
    for edition_date in ("2026-09-14", "2026-09-13", "2026-09-12", "2026-09-11", "2026-09-10", "2026-09-09"):
        _write_release(public_root, "gaza", edition_date, title=f"Gaza {edition_date}", source_count=2)
    _write_recovery_shaped_food_release(public_root, "2026-09-12")
    _write_release(public_root, "care-line", "2026-08-20", title="Methodist Hospitals Gary outage update", source_count=1)
    homepage = (
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-09-14/">Old</a></h3></article>'
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-09-12/">Old</a></h3></article>'
        '<article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-09-12/">Old</a></h3></article>'
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-09-11/">Old</a></h3></article>'
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-09-10/">Old</a></h3></article>'
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-09-09/">Old</a></h3></article>'
        '<article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-20/">Old</a></h3></article>'
    )

    cards = select_homepage_cards(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 14), homepage_html=homepage)
    )

    assert [card.relative_url for card in cards] == [
        "/gaza/editions/2026-09-14/",
        "/gaza/editions/2026-09-12/",
        "/food-line/editions/2026-09-12/",
        "/gaza/editions/2026-09-11/",
        "/gaza/editions/2026-09-10/",
        "/gaza/editions/2026-09-09/",
        "/care-line/editions/2026-08-20/",
    ]
    assert "/gaza/editions/2026-09-13/" not in [card.relative_url for card in cards]


def test_homepage_refresh_supports_legacy_manifestless_release_when_listed_publicly(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(
        public_root,
        "cascadia",
        "2026-05-03",
        title="Legacy Cascadia release",
        source_count=2,
        public_release_status=None,
        pages_release_status=None,
        include_manifest=False,
    )

    releases = discover_public_releases(public_root, verify_root=None, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)

    assert releases == []


def test_homepage_refresh_requires_verification_for_source_only_not_published_release(tmp_path):
    source_root = tmp_path / "source_site"
    _write_release(
        source_root,
        "care-line",
        "2026-08-05",
        title="Source-only Care",
        source_count=1,
        public_release_status="not_published",
        pages_release_status="not_synced",
    )
    releases = discover_public_releases(source_root, verify_root=None, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)
    assert releases == []


def test_homepage_refresh_accepts_only_live_archive_listed_transitional_release(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(
        public_root,
        "food-line",
        "2026-08-31",
        title="Retrospective food release",
        source_count=3,
        public_release_status="approved_pending_pages_publication",
        pages_release_status="not_synced",
    )

    live = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)
    not_verified = discover_public_releases(public_root, verify_root=None, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)
    other_root = discover_public_releases(public_root, verify_root=tmp_path / "other", as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)

    assert [(item.slug, item.edition_date) for item in live] == [("food-line", "2026-08-31")]
    assert not_verified == []
    assert other_root == []


@pytest.mark.parametrize("terminal_status", ["rejected", "suppressed", "withdrawn", "withheld", "failed", "unpublished"])
def test_homepage_refresh_rejects_terminal_release_even_when_live_and_listed(tmp_path, terminal_status):
    public_root = tmp_path / "pages"
    _write_release(
        public_root,
        "food-line",
        "2026-08-31",
        title="Blocked food release",
        source_count=3,
        public_release_status=terminal_status,
        pages_release_status="not_synced",
    )

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)

    assert releases == []


def test_homepage_refresh_excludes_future_transitional_release(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(
        public_root,
        "food-line",
        "2026-09-05",
        title="Future food release",
        source_count=3,
        public_release_status="approved_pending_pages_publication",
        pages_release_status="not_synced",
    )

    assert discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML) == []


def _write_current_directory_inventory(public_root: Path) -> None:
    _write_release(public_root, "gaza", "2026-09-04", title="Current Gaza", source_count=7)
    _write_release(
        public_root,
        "food-line",
        "2026-08-31",
        title="Current Food",
        source_count=3,
        public_release_status="approved_pending_pages_publication",
        pages_release_status="not_synced",
    )
    _write_release(public_root, "care-line", "2026-08-20", title="Current Care", source_count=2)


def test_dispatch_directory_refreshes_all_active_products_and_is_byte_idempotent(tmp_path):
    public_root = tmp_path / "pages"
    _write_current_directory_inventory(public_root)
    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)
    latest = select_effective_latest(releases)

    rendered = render_dispatch_directory_from_releases(DIRECTORY_TEMPLATE, latest)

    assert "/gaza/editions/2026-09-04/" in rendered
    assert "/food-line/editions/2026-08-31/" in rendered
    assert "/care-line/editions/2026-08-20/" in rendered
    assert "Ã" not in rendered
    assert "�" not in rendered
    assert "How we work</a> &middot; <a href=\"/about/\">About this project" in rendered
    assert render_dispatch_directory_from_releases(rendered, latest) == rendered
    for link in re.findall(r'<a class="button" href="([^"]+)">Read latest</a>', rendered):
        assert (public_root / link.strip("/") / "index.html").exists()


def test_shared_root_render_normalizes_only_footer_separator_and_is_byte_idempotent(tmp_path):
    public_root = tmp_path / "pages"
    _write_current_directory_inventory(public_root)
    latest = select_effective_latest(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=SHARED_ROOT_TEMPLATE)
    )
    template = SHARED_ROOT_TEMPLATE.replace("</main>", f'<p id="unrelated">Keep {MOJIBAKE_SEPARATOR} unchanged.</p></main>')

    rendered = render_sitewide_homepage_from_template(template, latest["gaza"])

    assert 'How we work</a> &middot; <a href="/about/">About this project' in rendered
    assert f'Keep {MOJIBAKE_SEPARATOR} unchanged.' in rendered
    assert render_sitewide_homepage_from_template(rendered, latest["gaza"]) == rendered


def test_dispatch_directory_render_normalizes_footer_separator_and_preserves_correct_footer(tmp_path):
    public_root = tmp_path / "pages"
    _write_current_directory_inventory(public_root)
    latest = select_effective_latest(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=SHARED_ROOT_TEMPLATE)
    )

    rendered = render_dispatch_directory_from_releases(DIRECTORY_TEMPLATE, latest)
    already_correct = rendered.replace("</main>", f'<p id="unrelated-directory">Keep {MOJIBAKE_SEPARATOR} unchanged.</p></main>')

    assert 'How we work</a> &middot; <a href="/about/">About this project' in rendered
    assert render_dispatch_directory_from_releases(rendered, latest) == rendered
    assert f'Keep {MOJIBAKE_SEPARATOR} unchanged.' in already_correct
    assert render_dispatch_directory_from_releases(already_correct, latest) == already_correct


def test_shared_release_refresh_reports_exact_changed_surfaces(tmp_path):
    public_root = tmp_path / "pages"
    public_root.mkdir(parents=True)
    (public_root / "index.html").write_text(SHARED_ROOT_TEMPLATE, encoding="utf-8")
    (public_root / "dispatches").mkdir()
    (public_root / "dispatches" / "index.html").write_text(DIRECTORY_TEMPLATE, encoding="utf-8")
    _write_current_directory_inventory(public_root)

    result = refresh_shared_release_surfaces_from_pages_inventory(
        public_root,
        dry_run=False,
        target_dispatch="gaza",
    )

    assert result["ok"] is True
    assert result["changed_surfaces"] == ["index.html", "dispatches/index.html"]
    assert "/gaza/editions/2026-09-04/" in (public_root / "index.html").read_text(encoding="utf-8")
    directory = (public_root / "dispatches" / "index.html").read_text(encoding="utf-8")
    assert "/gaza/editions/2026-09-04/" in directory
    assert "/food-line/editions/2026-08-31/" in directory
    assert "/care-line/editions/2026-08-20/" in directory


def test_refresh_script_target_dispatch_updates_only_food_directory_and_root_cards(tmp_path):
    public_root = tmp_path / "pages"
    public_root.mkdir(parents=True)
    _write_current_directory_inventory(public_root)
    _write_recovery_shaped_food_release(public_root, "2026-09-12")
    latest = select_effective_latest(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=TEMPLATE_HTML)
    )
    baseline_directory = render_dispatch_directory_from_releases(DIRECTORY_TEMPLATE, latest)
    root_template = SHARED_ROOT_TEMPLATE.replace(MOJIBAKE_SEPARATOR, "&middot;").replace(
        '</article></div></section><div class="directory-list">',
        '</article><article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-08-31/">Current Food</a></h3>'
        '<p class="edition-source">Food Line Dispatch &middot; August 31, 2026</p>'
        '<p class="edition-meta">3 public sources</p></article></div></section><div class="directory-list">',
        1,
    )
    root_path = public_root / "index.html"
    directory_path = public_root / "dispatches" / "index.html"
    output_root = tmp_path / "out" / "index.html"
    output_directory = tmp_path / "out" / "dispatches.html"
    root_path.write_text(root_template, encoding="utf-8")
    directory_path.parent.mkdir(parents=True)
    directory_path.write_text(baseline_directory, encoding="utf-8")

    baseline_gaza_root_card = _dispatch_card(root_template, "Dispatches From Gaza")
    baseline_care_root_card = _dispatch_card(root_template, "The Care Line Dispatch")
    baseline_gaza_edition = _edition_card(root_template, "gaza")
    baseline_gaza_directory = _dispatch_card(baseline_directory, "Dispatches From Gaza")
    baseline_care_directory = _dispatch_card(baseline_directory, "The Care Line Dispatch")
    baseline_root_footer = root_template.split("<footer", 1)[1]
    baseline_directory_footer = baseline_directory.split("<footer", 1)[1]

    assert refresh_root_homepage.main(
        [
            "--public-inventory-root",
            str(public_root),
            "--template-html",
            str(root_path),
            "--output-html",
            str(output_root),
            "--target-dispatch",
            "food-line",
            "--directory-template-html",
            str(directory_path),
            "--directory-output-html",
            str(output_directory),
        ]
    ) == 0

    rendered_root = output_root.read_text(encoding="utf-8")
    rendered_directory = output_directory.read_text(encoding="utf-8")
    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 13), homepage_html=rendered_root)
    food = select_effective_latest(releases)["food-line"]

    assert food.edition_date == "2026-09-12"
    assert food.source_count == 4
    assert "/food-line/editions/2026-09-12/" in _dispatch_card(rendered_directory, "Food Line Dispatch")
    assert "4 public sources" in _edition_card(rendered_root, "food-line")
    assert _dispatch_card(rendered_directory, "Dispatches From Gaza") == baseline_gaza_directory
    assert _dispatch_card(rendered_directory, "The Care Line Dispatch") == baseline_care_directory
    assert _dispatch_card(rendered_root, "Dispatches From Gaza") == baseline_gaza_root_card
    assert _dispatch_card(rendered_root, "The Care Line Dispatch") == baseline_care_root_card
    assert _edition_card(rendered_root, "gaza") == baseline_gaza_edition
    assert rendered_root.split("<footer", 1)[1] == baseline_root_footer
    assert rendered_directory.split("<footer", 1)[1] == baseline_directory_footer


@pytest.mark.parametrize(
    ("slug", "new_date"),
    [("gaza", "2026-09-05"), ("food-line", "2026-09-01"), ("care-line", "2026-08-21")],
)
def test_targeted_release_refresh_does_not_regress_other_directory_cards(tmp_path, slug, new_date):
    public_root = tmp_path / "pages"
    _write_current_directory_inventory(public_root)
    baseline_latest = select_effective_latest(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 4), homepage_html=TEMPLATE_HTML)
    )
    baseline = render_dispatch_directory_from_releases(DIRECTORY_TEMPLATE, baseline_latest)
    _write_release(public_root, slug, new_date, title=f"New {slug}", source_count=4)
    refreshed_latest = select_effective_latest(
        discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 9, 5), homepage_html=TEMPLATE_HTML)
    )

    refreshed = render_dispatch_directory_from_releases(baseline, refreshed_latest)

    expected = {"gaza": "2026-09-04", "food-line": "2026-08-31", "care-line": "2026-08-20"}
    expected[slug] = new_date
    for product, edition_date in expected.items():
        assert f"/{product}/editions/{edition_date}/" in refreshed


def test_homepage_refresh_is_deterministic_and_does_not_invent_time(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(public_root, "care-line", "2026-08-05", title="Miles Hospital proposes closing its labor and delivery center", source_count=1)
    _write_release(public_root, "food-line", "2026-07-31", title="Food latest", source_count=1)
    _write_release(public_root, "gaza", "2026-08-05", title="Gaza latest", source_count=7, time_key="actual_run_local_time", time_value="2026-08-05T06:00:42-07:00")

    releases_one = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)
    releases_two = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)
    cards_one = select_homepage_cards(releases_one)
    cards_two = select_homepage_cards(releases_two)
    html_one = render_homepage_from_template(TEMPLATE_HTML, cards_one)
    html_two = render_homepage_from_template(TEMPLATE_HTML, cards_two)

    assert [card.relative_url for card in cards_one] == [card.relative_url for card in cards_two]
    assert html_one == html_two
    assert "Care Line &middot; August 5, 2026 &middot;" not in html_one
    assert "Dispatches From Gaza &middot; August 5, 2026 &middot; 6:00 AM PT" in html_one


def test_homepage_refresh_lifecycle_state_controls_eligibility_not_release_age(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(public_root, "gaza", "2026-08-05", title="Gaza latest", source_count=7)
    _write_release(public_root, "food-line", "2026-07-31", title="Food latest", source_count=1)
    _write_release(public_root, "care-line", "2026-06-19", title="Care older but active", source_count=1)
    _write_release(public_root, "cascadia", "2026-08-05", title="Future Cascadia latest", source_count=2)
    _write_release(public_root, "american-pressure", "2026-08-04", title="Future AP latest", source_count=2)

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)
    cards = select_homepage_cards(releases)

    assert ("care-line", "2026-06-19") in [(card.slug, card.edition_date) for card in cards]
    assert all(card.slug not in {"cascadia", "american-pressure"} for card in cards)


def test_homepage_refresh_does_not_modify_historical_future_dispatch_files(tmp_path):
    public_root = tmp_path / "pages"
    _write_release(public_root, "gaza", "2026-08-05", title="Gaza latest", source_count=7)
    _write_release(public_root, "cascadia", "2026-05-03", title="Legacy Cascadia release", source_count=2)
    _write_release(public_root, "american-pressure", "2026-06-15", title="Legacy AP release", source_count=10)

    protected = [
        public_root / "cascadia" / "editions" / "2026-05-03" / "index.html",
        public_root / "american-pressure" / "editions" / "2026-06-15" / "index.html",
    ]
    before = [path.read_text(encoding="utf-8") for path in protected]

    releases = discover_public_releases(public_root, verify_root=public_root, as_of=date(2026, 8, 5), homepage_html=TEMPLATE_HTML)
    cards = select_homepage_cards(releases)
    render_homepage_from_template(TEMPLATE_HTML, cards)

    after = [path.read_text(encoding="utf-8") for path in protected]
    assert before == after


def test_collection_only_scheduler_does_not_reference_homepage_refresh_tool():
    scheduler_script = Path(__file__).resolve().parents[1] / "scripts" / "care_line_collection_scheduler.py"
    windows_wrapper = Path(__file__).resolve().parents[1] / "scripts" / "windows" / "run_care_line_national_collection.ps1"
    scheduler_text = scheduler_script.read_text(encoding="utf-8")
    wrapper_text = windows_wrapper.read_text(encoding="utf-8")

    assert "refresh_root_homepage" not in scheduler_text
    assert "refresh_root_homepage" not in wrapper_text
