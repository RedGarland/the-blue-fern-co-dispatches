from pathlib import Path

from bluefern_dispatches import generator
from bluefern_dispatches.gaza_layout import apply_gaza_archive_layout, apply_gaza_home_layout


def _document(body: str) -> str:
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>fixture</title></head>
<body>{body}</body></html>
'''


def _legacy_home() -> str:
    return _document('''
<header class="site-header"><a class="brand" href="/">Old brand</a></header>
<main class="home"><article class="gaza-latest-card">
  <p class="edition-date">2026-10-27</p><h3>Approved briefing</h3>
  <p>Approved summary and source trace.</p><p class="topic-badge topic-badge--gaza">Limited-source update</p>
  <p><a href="editions/2026-10-27/">Read briefing</a></p>
</article>
<h2>Recent Checks</h2><ul class="edition-list gaza-check-list"><li class="no-update-check"><span class="edition-date">2026-10-26</span>Check</li></ul>
<h2>Readable Briefings</h2><ul class="edition-list gaza-readable-list"><li class="briefing-row"><span class="edition-date">2026-10-27</span><a href="editions/2026-10-27/">Approved briefing</a></li></ul>
<p><a href="/gaza/audio/index.html">Gaza audio and transcript archive</a></p></main>
<footer class="site-footer"><p>Footer</p></footer>
''')


def _legacy_archive() -> str:
    return _document('''
<header class="site-header"><a class="brand" href="/">Old brand</a></header>
<main class="archive archive--gaza">
<section class="archive-latest"><p class="archive-latest-date">2026-10-27</p><h2>Approved briefing</h2></section>
<p><a href="/gaza/audio/index.html">Gaza audio and transcript archive</a></p>
<section class="archive-month"><h2>October 2026</h2><ul class="edition-list archive-list">
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-27</span><a href="editions/2026-10-27/">Approved briefing</a></li>
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-14</span><span>Correction | corrected link</span></li>
<li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-13</span><span>No qualifying update</span></li>
</ul></section>
<section class="archive-month"><h2>September 2026</h2><ul class="edition-list archive-list">
<li class="archive-row archive-row--catchup"><span class="edition-date">2026-09-30</span><a href="catchups/gaza-historical-catchup-1/">Catch-up</a></li>
</ul></section></main>
<footer class="site-footer"><p>Footer</p></footer>
''')


def test_home_layout_preserves_generated_blocks_and_adds_concept_structure() -> None:
    rendered = apply_gaza_home_layout(_legacy_home())
    assert "Traceable sources. A lasting record." in rendered
    assert "Tuesday, October 27, 2026" in rendered
    assert "Approved summary and source trace." in rendered
    assert "2026-10-26" in rendered
    assert "Open the full archive" in rendered
    assert "Monitoring &amp; source notes" in rendered
    assert "/gaza/audio/index.html" in rendered
    assert 'class="is-active"' in rendered
    assert ">RSS</a>" in rendered
    assert "RSS ?" not in rendered
    assert "\u2197" not in rendered
    assert "\ufffd" not in rendered


def test_archive_layout_preserves_all_records_and_adds_filter_metadata() -> None:
    rendered = apply_gaza_archive_layout(_legacy_archive())
    assert rendered.count('archive-record archive-record--') == 4
    assert rendered.count('data-record-type="briefing"') == 2
    assert rendered.count('data-record-type="correction"') == 1
    assert rendered.count('data-record-type="monitoring"') == 1
    assert 'data-month="2026-10"' in rendered
    assert 'data-month="2026-09"' in rendered
    assert 'id="archive-month"' in rendered
    assert 'src="assets/archive-filters.js"' in rendered
    for marker in ("Approved briefing", "Correction | corrected link", "No qualifying update", "Catch-up"):
        assert marker in rendered
    assert ">RSS</a>" in rendered
    assert "RSS ?" not in rendered
    assert "\u2197" not in rendered
    assert "\ufffd" not in rendered


def test_generator_gaza_routes_apply_layout_transform(monkeypatch) -> None:
    dispatch = generator.DispatchConfig(
        slug="gaza",
        name="Dispatches From Gaza",
        edition_date="2026-10-27",
        tagline="Daily source-backed Gaza briefings.",
        logo="gaza-logo.png",
        sources=[],
        stories=[],
    )
    legacy = _legacy_home()
    calls: list[str] = []
    monkeypatch.setattr(generator, "page", lambda *args, **kwargs: legacy)
    monkeypatch.setattr(generator, "apply_gaza_home_layout", lambda value: calls.append(value) or "HOME-LAYOUT")
    monkeypatch.setattr(generator, "_gaza_no_update_supersedes_edition", lambda *args, **kwargs: False)
    monkeypatch.setattr(generator, "discover_gaza_historical_catchups", lambda *args, **kwargs: [])
    monkeypatch.setattr(generator, "_gaza_public_history_rows_with_no_updates", lambda *args, **kwargs: [("daily", "2026-10-27")])
    monkeypatch.setattr(generator, "_gaza_latest_readable_archive_row", lambda rows: rows[0])
    monkeypatch.setattr(generator, "_render_gaza_readable_history_list", lambda *args, **kwargs: "<li>briefing</li>")
    monkeypatch.setattr(generator, "_render_gaza_recent_checks_list", lambda *args, **kwargs: "<li>check</li>")
    monkeypatch.setattr(generator, "_gaza_landing_latest_card", lambda *args, **kwargs: '<article class="gaza-latest-card">latest</article>')
    assert generator.render_dispatch_index_for_dates(dispatch, ["2026-10-27"], Path(".")) == "HOME-LAYOUT"
    assert calls == [legacy]

    archive_calls: list[str] = []
    monkeypatch.setattr(generator, "page", lambda *args, **kwargs: legacy)
    monkeypatch.setattr(generator, "apply_gaza_archive_layout", lambda value: archive_calls.append(value) or "ARCHIVE-LAYOUT")
    monkeypatch.setattr(generator, "_render_gaza_archive_grouped_history", lambda *args, **kwargs: "<section class=archive-month>records</section>")
    assert generator.render_archive_for_dates(dispatch, ["2026-10-27"], Path(".")) == "ARCHIVE-LAYOUT"
    assert archive_calls == [legacy]


def test_filter_asset_is_present_and_contains_no_network_or_public_write() -> None:
    asset = Path(__file__).parents[1] / "assets" / "archive-filters.js"
    text = asset.read_text(encoding="utf-8")
    assert "fetch(" not in text
    assert "XMLHttpRequest" not in text
    assert "archive-record" in text
    assert "aria-pressed" in text
    assert '`${labels[kind]} ${kind === "all"' in text
