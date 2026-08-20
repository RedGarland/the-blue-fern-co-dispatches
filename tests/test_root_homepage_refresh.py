import json
from datetime import date
from pathlib import Path

from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_homepage_from_template,
    select_homepage_cards,
)


TEMPLATE_HTML = (
    '<!doctype html><html><body>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid"><article>stale</article></div></section>'
    '<section class="section-block section-block--quiet"><h2>Unrelated section</h2><p>Keep me stable.</p></section>'
    "</body></html>"
)


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
