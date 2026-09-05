import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

import scripts.run_food_line_dispatch as food_line


FOOD_LINE_ARCHIVE_DATES = [
    "2026-08-31",
    "2026-08-30",
    "2026-08-24",
    "2026-08-16",
    "2026-08-05",
    "2026-07-31",
    "2026-07-28",
    "2026-06-20",
    "2026-06-19",
    "2026-06-18",
    "2026-06-17",
    "2026-06-16",
    "2026-06-14",
    "2026-06-13",
    "2026-06-09",
    "2026-06-07",
    "2026-06-06",
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _legacy_archive(labels: dict[str, str]) -> str:
    entries = "".join(
        f'<li><a href="editions/{date}/">{date} — {labels[date]}</a></li>' for date in FOOD_LINE_ARCHIVE_DATES
    )
    return (
        '<html><body><h2>Latest edition</h2>'
        f'<p><a href="editions/{FOOD_LINE_ARCHIVE_DATES[0]}/">{FOOD_LINE_ARCHIVE_DATES[0]} — {labels[FOOD_LINE_ARCHIVE_DATES[0]]}</a></p>'
        f'<h2>Archive</h2><ul>{entries}</ul></body></html>'
    )


def _rss() -> str:
    items = "".join(
        "".join(
            [
                "<item>",
                f"<title>{date} Food Line</title>",
                f"<link>https://dispatches.thebluefernco.com/food-line/editions/{date}/</link>",
                f"<guid>https://dispatches.thebluefernco.com/food-line/editions/{date}/</guid>",
                "</item>",
            ]
        )
        for date in FOOD_LINE_ARCHIVE_DATES
    )
    return f"<rss><channel>{items}</channel></rss>"


def test_food_line_archive_refresh_matches_care_entry_structure_without_history_or_surface_changes(tmp_path: Path):
    source_root = tmp_path / "source"
    pages_root = tmp_path / "pages"
    labels = {date: f"Existing archive title {index}" for index, date in enumerate(FOOD_LINE_ARCHIVE_DATES)}
    labels["2026-08-31"] = "Phoenix food-box demand rose as Arizona SNAP access contracted"
    labels["2026-08-30"] = "Broken pipe closed St. Columba pantry for restoration"
    source_titles = {date: f"Existing sanctioned headline {index}" for index, date in enumerate(FOOD_LINE_ARCHIVE_DATES)}
    source_titles["2026-08-31"] = labels["2026-08-31"]
    source_titles["2026-08-30"] = labels["2026-08-30"]

    legacy_archive = _legacy_archive(labels)
    _write(source_root / "output/site/food-line/archive.html", legacy_archive)
    _write(source_root / "output/site/food-line/index.html", "food home sentinel")
    _write(source_root / "output/site/food-line/rss.xml", _rss())
    care_archive = (
        '<ul class="edition-list"><li><span class="edition-date">2026-08-20</span>'
        '<a href="editions/2026-08-20/">Care Line headline</a><br><small>Care Line summary.</small></li></ul>'
    )
    _write(source_root / "output/site/care-line/archive.html", care_archive)
    _write(source_root / "output/site/gaza/index.html", "gaza sentinel")
    _write(source_root / "output/site/cascadia/index.html", "cascadia sentinel")

    _write(pages_root / "food-line/archive.html", legacy_archive)
    _write(pages_root / "food-line/rss.xml", _rss())
    for date in FOOD_LINE_ARCHIVE_DATES:
        edition_dir = pages_root / "food-line/editions" / date
        _write(edition_dir / "index.html", f"edition {date}")
        _write(
            edition_dir / "edition_manifest.json",
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": date,
                    "public_rendered": True,
                    "public_signal_count": 1,
                    "lead_source_record_id": f"lead-{date}",
                }
            ),
        )
        _write(
            edition_dir / "sources_manifest.json",
            json.dumps(
                [
                    {
                        "source_record_id": f"lead-{date}",
                            "title": source_titles[date],
                        "pressure_summary": f"Existing sanctioned summary for {date}.",
                    }
                ]
            ),
        )
    _write(pages_root / "food-line/editions/2026-08-31/source_table.html", "source table")
    _write(pages_root / "food-line/editions/2026-08-31/claim_ledger.html", "claim ledger")

    source_before = _snapshot(source_root / "output/site")
    pages_before = _snapshot(pages_root)
    before_dates = re.findall(r"editions/(\d{4}-\d{2}-\d{2})/", legacy_archive)[1:]

    result = food_line.refresh_food_line_archive_from_public_state(source_root, pages_root)

    source_after = _snapshot(source_root / "output/site")
    pages_after = _snapshot(pages_root)
    changed_source_paths = sorted(
        path for path in set(source_before) | set(source_after) if source_before.get(path) != source_after.get(path)
    )
    assert result["entry_count"] == len(FOOD_LINE_ARCHIVE_DATES)
    assert result["edition_dates"] == FOOD_LINE_ARCHIVE_DATES
    assert changed_source_paths == ["food-line/archive.html"]
    assert pages_after == pages_before
    assert source_after["food-line/rss.xml"] == source_before["food-line/rss.xml"]
    assert source_after["care-line/archive.html"] == source_before["care-line/archive.html"]
    assert source_after["gaza/index.html"] == source_before["gaza/index.html"]
    assert source_after["cascadia/index.html"] == source_before["cascadia/index.html"]

    archive_html = (source_root / "output/site/food-line/archive.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(archive_html, "html.parser")
    archive_heading = soup.find("h2", string="Archive")
    archive_list = archive_heading.find_next("ul")
    entries = archive_list.find_all("li", recursive=False)
    after_dates = [entry.find("span", class_="edition-date").get_text(strip=True) for entry in entries]
    assert len(entries) == len(before_dates) == len(FOOD_LINE_ARCHIVE_DATES)
    assert before_dates == after_dates == FOOD_LINE_ARCHIVE_DATES
    assert len(after_dates) == len(set(after_dates))
    assert "2026-08-30" in after_dates
    assert "2026-08-31" in after_dates

    for entry, date in zip(entries, FOOD_LINE_ARCHIVE_DATES, strict=True):
        link = entry.find("a", recursive=False)
        summary = entry.find("small", recursive=False)
        assert link["href"] == f"editions/{date}/"
        assert link.get_text(strip=True) == source_titles[date]
        assert date not in link.get_text(strip=True)
        assert summary.get_text(strip=True) == f"Existing sanctioned summary for {date}."
        assert summary.find_parent("a") is None

    latest_heading = soup.find("h2", string="Latest edition")
    latest_block_links = [link for link in latest_heading.find_all_next("a") if link.find_previous("h2") == latest_heading]
    assert [(link.get_text(strip=True), link["href"]) for link in latest_block_links] == [
        ("Read the latest briefing", "editions/2026-08-31/"),
        ("Source table", "editions/2026-08-31/source_table.html"),
        ("Claim ledger", "editions/2026-08-31/claim_ledger.html"),
    ]
    assert soup.find("span", class_="edition-date").find_parent("a") is None
    assert "2026-08-20" in care_archive
    assert 'href="editions/2026-08-20/"' in care_archive


def test_structured_food_line_archive_labels_remain_stable_for_home_and_rss_fallbacks(tmp_path: Path):
    pages_root = tmp_path / "pages"
    _write(
        pages_root / "food-line/archive.html",
        "".join(
            [
                '<h2>Latest edition</h2><p><a href="editions/2026-08-31/">Read the latest briefing</a></p>',
                '<h2>Archive</h2><ul class="edition-list"><li>',
                '<span class="edition-date">2026-08-31</span>',
                '<a href="editions/2026-08-31/">Phoenix food-box demand rose as Arizona SNAP access contracted</a>',
                '<br><small>Existing summary.</small></li></ul>',
            ]
        ),
    )

    assert food_line._bound_pages_archive_labels(pages_root) == {
        "2026-08-31": "2026-08-31 — Phoenix food-box demand rose as Arizona SNAP access contracted"
    }
    assert food_line._bound_pages_archive_entries(pages_root)["2026-08-31"]["summary"] == "Existing summary."
