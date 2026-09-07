from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from bluefern_dispatches.care_line_sources import (
    DISPATCH_NAME as CARE_LINE_DISPATCH_NAME,
    DISPATCH_SLUG as CARE_LINE_DISPATCH_SLUG,
    DISPATCH_TAGLINE as CARE_LINE_DISPATCH_TAGLINE,
)
from bluefern_dispatches.generator import DispatchConfig, render_archive_for_dates
from bluefern_dispatches.source_based_retrospective_archive import (
    SourceBasedRetrospectiveArchiveError,
    discover_deployed_retrospective_archive_entries,
)
from scripts.run_food_line_dispatch import refresh_food_line_archive_from_public_state
from scripts.update_source_based_retrospective_archive_links import refresh_archives


INTERNAL_TERMS = (
    "source-based retrospective publication",
    "approval batch",
    "release authorization",
    "retained_for_review",
    "mechanical recovery",
    "step 7",
    "step 8",
    "publication authorization",
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, indent=2, sort_keys=True))


def _food_line_pages_archive(dates: list[str]) -> str:
    rows = "".join(
        (
            f'<li><span class="edition-date">{date}</span>'
            f'<a href="editions/{date}/">Food Line headline for {date}</a>'
            f'<br><small>Food Line summary for {date}.</small></li>'
        )
        for date in dates
    )
    return f"<h2>Archive</h2><ul class=\"edition-list\">{rows}</ul>"


def _food_line_rss(dates: list[str]) -> str:
    items = "".join(
        (
            "<item>"
            f"<title>{date} Food Line</title>"
            f"<link>https://dispatches.thebluefernco.com/food-line/editions/{date}/</link>"
            f"<guid>https://dispatches.thebluefernco.com/food-line/editions/{date}/</guid>"
            "</item>"
        )
        for date in dates
    )
    return f"<rss><channel>{items}</channel></rss>"


def _food_line_public_edition(pages_root: Path, date: str) -> None:
    edition_dir = pages_root / "food-line" / "editions" / date
    _write(edition_dir / "index.html", f"Food Line {date}")
    _write_json(
        edition_dir / "edition_manifest.json",
        {
            "dispatch_slug": "food-line",
            "edition_date": date,
            "public_rendered": True,
            "public_signal_count": 1,
            "lead_source_record_id": f"lead-{date}",
        },
    )
    _write_json(
        edition_dir / "sources_manifest.json",
        [
            {
                "source_record_id": f"lead-{date}",
                "title": f"Food Line headline for {date}",
                "pressure_summary": f"Food Line summary for {date}.",
            }
        ],
    )


def _retrospective_items(count: int, *, state: str = "IOWA") -> list[dict[str, str]]:
    return [
        {
            "event": f"Recovered access signal {index}",
            "event_or_effective_date_or_range": "2026-08-13",
            "location": f"Recovered location {index}",
            "publisher": "Public source",
            "source_url": f"https://example.test/source/{index}",
            "state_or_territory": state,
        }
        for index in range(1, count + 1)
    ]


def _published_retrospective(pages_root: Path, dispatch: str, count: int) -> Path:
    batch_id = f"{dispatch}-august-2026-source-based-publication"
    directory = pages_root / dispatch / "source-based-retrospectives" / batch_id
    _write(directory / "index.html", f"<h1>{dispatch} August recovery</h1>")
    _write_json(directory / "items.json", _retrospective_items(count))
    return directory


def _care_dispatch() -> DispatchConfig:
    return DispatchConfig(
        slug=CARE_LINE_DISPATCH_SLUG,
        name=CARE_LINE_DISPATCH_NAME,
        edition_date="2026-08-20",
        tagline=CARE_LINE_DISPATCH_TAGLINE,
        logo="care-line-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )


def _visible_text(html_text: str) -> str:
    return BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True).lower()


def test_food_archive_lists_deployed_retrospective_without_changing_chronology(tmp_path: Path):
    source_root = tmp_path / "source"
    pages_root = tmp_path / "pages"
    dates = ["2026-08-31", "2026-08-20", "2026-08-19"]
    _write(source_root / "output" / "site" / "food-line" / "archive.html", _food_line_pages_archive(dates))
    _write(pages_root / "food-line" / "archive.html", _food_line_pages_archive(dates))
    _write(pages_root / "food-line" / "rss.xml", _food_line_rss(dates))
    for date in dates:
        _food_line_public_edition(pages_root, date)
    _published_retrospective(pages_root, "food-line", 16)

    first = refresh_food_line_archive_from_public_state(source_root, pages_root)
    second = refresh_food_line_archive_from_public_state(source_root, pages_root)

    assert first == second
    archive_html = (source_root / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(archive_html, "html.parser")
    archive_heading = soup.find("h2", string="Archive")
    chronology = [
        item.find("a")["href"]
        for item in archive_heading.find_next("ul", class_="edition-list").find_all("li", recursive=False)
    ]
    assert chronology == [f"editions/{date}/" for date in dates]
    assert all("source-based-retrospectives" not in href for href in chronology)
    assert archive_html.count("food-line-august-2026-source-based-publication/") == 1
    assert "August 2026 retrospective — 16 source-backed developments" in archive_html
    assert "Alabama" not in archive_html
    visible = _visible_text(archive_html)
    assert "retrospective recoveries" in visible
    assert not any(term in visible for term in INTERNAL_TERMS)


def test_care_archive_lists_deployed_retrospective_without_changing_chronology(tmp_path: Path):
    site_root = tmp_path / "source" / "output" / "site"
    pages_root = tmp_path / "pages"
    dates = ["2026-08-20", "2026-08-19", "2026-08-05"]
    _published_retrospective(pages_root, "care-line", 4)

    first = render_archive_for_dates(_care_dispatch(), dates, site_root, retrospective_pages_root=pages_root)
    second = render_archive_for_dates(_care_dispatch(), dates, site_root, retrospective_pages_root=pages_root)

    assert first == second
    soup = BeautifulSoup(first, "html.parser")
    chronology = [
        item.find("a")["href"]
        for item in soup.find("ul", class_="edition-list").find_all("li", recursive=False)
    ]
    assert chronology == [f"editions/{date}/" for date in dates]
    assert all("source-based-retrospectives" not in href for href in chronology)
    assert first.count("care-line-august-2026-source-based-publication/") == 1
    assert "August 2026 retrospective — 4 source-backed developments" in first
    visible = _visible_text(first)
    assert "retrospective recoveries" in visible
    assert not any(term in visible for term in INTERNAL_TERMS)


def test_unpublished_or_generated_only_retrospective_does_not_render(tmp_path: Path):
    source_root = tmp_path / "source"
    pages_root = tmp_path / "pages"
    _write_json(
        source_root
        / "publication-authorizations"
        / "food-line"
        / "source-based-retrospectives"
        / "food-line-august-2026-source-based-publication.json",
        {"publication_authorized": True},
    )
    _published_retrospective(source_root / "output" / "site", "food-line", 16)
    _write(pages_root / "food-line" / "archive.html", _food_line_pages_archive(["2026-08-31"]))
    _write(pages_root / "food-line" / "rss.xml", _food_line_rss(["2026-08-31"]))
    _food_line_public_edition(pages_root, "2026-08-31")

    refresh_food_line_archive_from_public_state(source_root, pages_root)

    archive_html = (source_root / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    assert "Retrospective recoveries" not in archive_html
    assert "August 2026 retrospective" not in archive_html


def test_wrong_dispatch_retrospective_cannot_leak(tmp_path: Path):
    pages_root = tmp_path / "pages"
    _published_retrospective(pages_root, "care-line", 4)

    assert discover_deployed_retrospective_archive_entries(pages_root, "food-line") == []


def test_malformed_or_partial_deployed_retrospective_fails_closed(tmp_path: Path):
    pages_root = tmp_path / "pages"
    directory = pages_root / "food-line" / "source-based-retrospectives" / "food-line-august-2026-source-based-publication"
    _write(directory / "index.html", "<h1>Broken deployed artifact</h1>")

    with pytest.raises(SourceBasedRetrospectiveArchiveError, match="incomplete"):
        discover_deployed_retrospective_archive_entries(pages_root, "food-line")


def test_archive_refresh_command_updates_only_food_and_care_archives(tmp_path: Path):
    source_root = tmp_path / "source"
    pages_root = tmp_path / "pages"
    _write(source_root / "output" / "site" / "food-line" / "archive.html", _food_line_pages_archive(["2026-08-31"]))
    _write(source_root / "output" / "site" / "care-line" / "archive.html", "care archive sentinel")
    _write(source_root / "output" / "site" / "gaza" / "index.html", "gaza sentinel")
    _write(source_root / "output" / "site" / "cascadia" / "index.html", "cascadia sentinel")
    _write(pages_root / "food-line" / "archive.html", _food_line_pages_archive(["2026-08-31"]))
    _write(pages_root / "food-line" / "rss.xml", _food_line_rss(["2026-08-31"]))
    _food_line_public_edition(pages_root, "2026-08-31")
    _published_retrospective(pages_root, "food-line", 16)
    _published_retrospective(pages_root, "care-line", 4)
    for date in ("2026-08-20", "2026-08-19"):
        edition_dir = pages_root / "care-line" / "editions" / date
        _write(edition_dir / "index.html", f"Care {date}")
        _write_json(
            edition_dir / "edition_manifest.json",
            {
                "dispatch_slug": "care-line",
                "edition_date": date,
                "public_rendered": True,
                "qualified_public_claim_count": 1,
                "story_count": 1,
                "source_count": 1,
            },
        )
        _write_json(edition_dir / "sources_manifest.json", [{"source_record_id": f"care-{date}"}])
        _write_json(edition_dir / "curation_manifest.json", [{"story_id": f"care-{date}"}])

    result = refresh_archives(source_root, pages_root, "both")

    assert sorted(result) == ["care-line", "food-line"]
    changed_files = sorted(
        path.relative_to(source_root / "output" / "site").as_posix()
        for path in (source_root / "output" / "site").rglob("*")
        if path.is_file() and path.read_text(encoding="utf-8") != "gaza sentinel" and path.name == "archive.html"
    )
    assert changed_files == ["care-line/archive.html", "food-line/archive.html"]
    assert (source_root / "output" / "site" / "gaza" / "index.html").read_text(encoding="utf-8") == "gaza sentinel"
    assert "food-line-august-2026-source-based-publication/" in (
        source_root / "output" / "site" / "food-line" / "archive.html"
    ).read_text(encoding="utf-8")
    assert "care-line-august-2026-source-based-publication/" in (
        source_root / "output" / "site" / "care-line" / "archive.html"
    ).read_text(encoding="utf-8")
