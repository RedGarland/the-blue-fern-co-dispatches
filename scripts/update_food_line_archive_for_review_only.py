from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date as dt_date
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_food_line_dispatch import validate_date

DISPATCH_SLUG = "food-line"
ARCHIVE_RELATIVE_PATH = Path(DISPATCH_SLUG) / "archive.html"
EDITION_ROOT_RELATIVE_PATH = Path(DISPATCH_SLUG) / "editions"
EXPECTED_FRAC_SOURCE_URL = (
    "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children"
)


@dataclass(frozen=True)
class ArchiveEntry:
    date_text: str
    href: str
    title: str
    summary: str = ""


def _parse_date(date_text: str) -> dt_date:
    return dt_date.fromisoformat(validate_date(date_text))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalize_href(href: str) -> str:
    text = str(href or "").strip()
    while text.startswith("./"):
        text = text[2:]
    if text and not text.endswith("/"):
        text = f"{text}/"
    return text


def _canonical_edition_href(date_text: str) -> str:
    return f"editions/{validate_date(date_text)}/"


def _extract_date_from_label(label: str) -> str:
    text = str(label or "").strip()
    if len(text) < 10:
        raise ValueError(f"archive entry label does not start with a valid date: {text!r}")
    return validate_date(text[:10])


def _title_without_date(date_text: str, label: str) -> str:
    text = " ".join(str(label or "").split()).strip()
    for separator in (" — ", " - "):
        prefix = f"{date_text}{separator}"
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _collect_archive_entries(archive_list: Any) -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    for item in archive_list.find_all("li", recursive=False):
        link = item.find("a", recursive=False)
        if link is None:
            continue
        href = _normalize_href(link.get("href", ""))
        label = link.get_text(" ", strip=True)
        date_node = item.find("span", class_="edition-date", recursive=False)
        date_text = validate_date(date_node.get_text(" ", strip=True)) if date_node is not None else _extract_date_from_label(label)
        summary_node = item.find("small", recursive=False)
        summary = summary_node.get_text(" ", strip=True) if summary_node is not None else ""
        entries.append(ArchiveEntry(date_text=date_text, href=href, title=_title_without_date(date_text, label), summary=summary))
    return entries


def _ensure_expected_edition(edition_index_path: Path, expected_source_url: str) -> None:
    if not edition_index_path.exists():
        raise ValueError(f"review-only edition index not found: {edition_index_path}")
    edition_html = _read_text(edition_index_path)
    if expected_source_url not in edition_html:
        raise ValueError(f"review-only edition index does not contain expected source URL: {expected_source_url}")


def _find_archive_list(soup: BeautifulSoup) -> Any:
    archive_heading = soup.find("h2", string=lambda value: isinstance(value, str) and value.strip() == "Archive")
    if archive_heading is None:
        raise ValueError("Food Line archive page is missing the Archive heading")
    archive_list = archive_heading.find_next("ul")
    if archive_list is None:
        raise ValueError("Food Line archive page is missing the archive list")
    return archive_list


def _find_latest_paragraph(soup: BeautifulSoup) -> Any:
    latest_heading = soup.find("h2", string=lambda value: isinstance(value, str) and value.strip() == "Latest edition")
    if latest_heading is None:
        raise ValueError("Food Line archive page is missing the Latest edition heading")
    latest_paragraph = latest_heading.find_next("p")
    if latest_paragraph is None:
        raise ValueError("Food Line archive page is missing the latest edition paragraph")
    return latest_paragraph


def _build_entry_map(entries: list[ArchiveEntry]) -> dict[str, ArchiveEntry]:
    by_date: dict[str, ArchiveEntry] = {}
    for entry in entries:
        existing = by_date.get(entry.date_text)
        if existing is None:
            by_date[entry.date_text] = entry
            continue
        if existing.href != entry.href or existing.title != entry.title or existing.summary != entry.summary:
            raise ValueError(
                f"Food Line archive contains conflicting entries for {entry.date_text}: "
                f"{existing.href} / {existing.title!r} versus {entry.href} / {entry.title!r}"
            )
    return by_date


def _sorted_entries(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    return sorted(entries, key=lambda entry: _parse_date(entry.date_text), reverse=True)


def _set_archive_list(soup: BeautifulSoup, archive_list: Any, entries: list[ArchiveEntry]) -> None:
    archive_list.clear()
    archive_list["class"] = sorted(set(archive_list.get("class", [])) | {"edition-list"})
    for entry in entries:
        li = soup.new_tag("li")
        date_node = soup.new_tag("span")
        date_node["class"] = "edition-date"
        date_node.string = entry.date_text
        li.append(date_node)
        link = soup.new_tag("a", href=entry.href)
        link.string = entry.title
        li.append(link)
        if entry.summary:
            li.append(soup.new_tag("br"))
            summary = soup.new_tag("small")
            summary.string = entry.summary
            li.append(summary)
        archive_list.append(li)


def _set_latest_paragraph(soup: BeautifulSoup, latest_paragraph: Any, latest_entry: ArchiveEntry) -> None:
    latest_paragraph.clear()
    link = soup.new_tag("a", href=latest_entry.href)
    link.string = "Read the latest briefing"
    latest_paragraph.append(link)


def update_food_line_archive_for_review_only(
    *,
    date: str,
    title: str,
    edition_url: str,
    pages_repo: Path,
    apply: bool = False,
    expected_source_url: str = EXPECTED_FRAC_SOURCE_URL,
) -> dict[str, Any]:
    edition_date = validate_date(date)
    resolved_pages_repo = pages_repo.resolve()
    archive_path = resolved_pages_repo / ARCHIVE_RELATIVE_PATH
    canonical_href = _canonical_edition_href(edition_date)
    normalized_href = _normalize_href(edition_url)
    if normalized_href != canonical_href:
        raise ValueError(f"edition-url must resolve to {canonical_href}, got {edition_url}")
    if not archive_path.exists():
        raise ValueError(f"Food Line archive page not found: {archive_path}")

    edition_path = resolved_pages_repo / EDITION_ROOT_RELATIVE_PATH / edition_date / "index.html"
    _ensure_expected_edition(edition_path, expected_source_url)

    archive_html = _read_text(archive_path)
    soup = BeautifulSoup(archive_html, "html.parser")
    archive_list = _find_archive_list(soup)
    latest_paragraph = _find_latest_paragraph(soup)
    entries = _collect_archive_entries(archive_list)
    by_date = _build_entry_map(entries)

    new_entry = ArchiveEntry(date_text=edition_date, href=normalized_href, title=_title_without_date(edition_date, title))
    already_present = False
    entry_added = False

    existing = by_date.get(edition_date)
    if existing is not None:
        if existing.href != new_entry.href or existing.title != new_entry.title:
            raise ValueError(
                f"Food Line archive already contains {edition_date} with different content: "
                f"{existing.href} / {existing.title!r}"
            )
        already_present = True
    else:
        entries.append(new_entry)
        entry_added = True

    ordered_entries = _sorted_entries(entries)
    _set_archive_list(soup, archive_list, ordered_entries)
    _set_latest_paragraph(soup, latest_paragraph, ordered_entries[0])
    updated_html = str(soup)
    changed = updated_html != archive_html

    changed_files: list[str] = []
    pages_repo_mutated = False
    if apply and changed:
        archive_path.write_text(updated_html, encoding="utf-8")
        changed_files.append(str(archive_path))
        pages_repo_mutated = True

    return {
        "ok": True,
        "dry_run": not apply,
        "archive_path": str(archive_path),
        "edition_path": str(edition_path),
        "entry_added": entry_added,
        "already_present": already_present,
        "changed_files": changed_files,
        "pages_repo_mutated": pages_repo_mutated,
        "planned_entry": {
            "date": edition_date,
            "title": title,
            "label": new_entry.title,
            "href": edition_url,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely update the Food Line archive for a single review-only backfill edition.")
    parser.add_argument("--date", required=True, help="Edition date YYYY-MM-DD")
    parser.add_argument("--title", required=True, help="Archive entry title suffix without the leading date")
    parser.add_argument("--edition-url", required=True, help="Edition href, for example ./editions/2026-06-12/")
    parser.add_argument("--pages-repo", required=True, help="Path to the local Pages repo root")
    parser.add_argument("--expected-source-url", default=EXPECTED_FRAC_SOURCE_URL, help="Expected source URL that must appear in the edition page")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report only; this is the default mode")
    parser.add_argument("--apply", action="store_true", help="Write the updated Food Line archive into the Pages repo")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply and args.dry_run:
        print(json.dumps({"ok": False, "error": "choose either --apply or --dry-run, not both"}, indent=2))
        return 1
    try:
        result = update_food_line_archive_for_review_only(
            date=str(args.date),
            title=str(args.title),
            edition_url=str(args.edition_url),
            pages_repo=Path(str(args.pages_repo)),
            apply=bool(args.apply),
            expected_source_url=str(args.expected_source_url),
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
