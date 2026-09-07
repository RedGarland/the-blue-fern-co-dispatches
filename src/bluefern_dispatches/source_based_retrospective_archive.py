from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_RETROSPECTIVE_ARCHIVE_DISPATCHES = {"food-line", "care-line"}

INTERNAL_RETROSPECTIVE_ARCHIVE_TERMS = (
    "source-based retrospective publication",
    "approval batch",
    "release authorization",
    "retained_for_review",
    "mechanical recovery",
    "step 7",
    "step 8",
    "publication authorization",
)

_MONTH_NAMES = {
    "january": (1, "January"),
    "february": (2, "February"),
    "march": (3, "March"),
    "april": (4, "April"),
    "may": (5, "May"),
    "june": (6, "June"),
    "july": (7, "July"),
    "august": (8, "August"),
    "september": (9, "September"),
    "october": (10, "October"),
    "november": (11, "November"),
    "december": (12, "December"),
}

_RETROSPECTIVE_BATCH_RE = re.compile(
    r"^(?P<dispatch>food-line|care-line)-(?P<month>[a-z]+)-(?P<year>\d{4})-source-based-publication$"
)


class SourceBasedRetrospectiveArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class SourceBasedRetrospectiveArchiveEntry:
    dispatch: str
    batch_id: str
    href: str
    label: str
    summary: str
    item_count: int
    year: int
    month: int


def _read_items(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBasedRetrospectiveArchiveError(f"retrospective items metadata is not valid JSON: {path}") from exc
    if not isinstance(payload, list):
        raise SourceBasedRetrospectiveArchiveError(f"retrospective items metadata must be a list: {path}")
    items = [item for item in payload if isinstance(item, dict)]
    if len(items) != len(payload) or not items:
        raise SourceBasedRetrospectiveArchiveError(f"retrospective items metadata must contain public item objects: {path}")
    return items


def _entry_summary(dispatch: str) -> str:
    if dispatch == "food-line":
        return (
            "Recovered food-access disruptions, pantry and distribution interruptions, "
            "grocery-access losses, and related strain identified in later source review."
        )
    if dispatch == "care-line":
        return (
            "Recovered healthcare-access changes including maternity, NICU, emergency-care, "
            "and clinic service changes identified in later source review."
        )
    raise SourceBasedRetrospectiveArchiveError(f"unsupported retrospective archive dispatch: {dispatch}")


def _entry_from_deployed_directory(dispatch: str, directory: Path) -> SourceBasedRetrospectiveArchiveEntry | None:
    match = _RETROSPECTIVE_BATCH_RE.fullmatch(directory.name)
    if match is None or match.group("dispatch") != dispatch:
        return None
    month_key = match.group("month")
    month_value = _MONTH_NAMES.get(month_key)
    if month_value is None:
        return None
    index_path = directory / "index.html"
    items_path = directory / "items.json"
    if not index_path.is_file() and not items_path.exists():
        return None
    if not index_path.is_file() or not items_path.is_file():
        raise SourceBasedRetrospectiveArchiveError(
            f"retrospective public artifact is incomplete: {directory}"
        )
    items = _read_items(items_path)
    batch_id = directory.name
    month_number, month_label = month_value
    year = int(match.group("year"))
    return SourceBasedRetrospectiveArchiveEntry(
        dispatch=dispatch,
        batch_id=batch_id,
        href=f"source-based-retrospectives/{batch_id}/",
        label=f"{month_label} {year} retrospective — {len(items)} source-backed developments",
        summary=_entry_summary(dispatch),
        item_count=len(items),
        year=year,
        month=month_number,
    )


def discover_deployed_retrospective_archive_entries(
    pages_root: Path | None,
    dispatch: str,
) -> list[SourceBasedRetrospectiveArchiveEntry]:
    if dispatch not in SUPPORTED_RETROSPECTIVE_ARCHIVE_DISPATCHES:
        raise SourceBasedRetrospectiveArchiveError(f"unsupported retrospective archive dispatch: {dispatch}")
    if pages_root is None:
        return []
    retrospectives_root = pages_root / dispatch / "source-based-retrospectives"
    if not retrospectives_root.is_dir():
        return []
    entries: list[SourceBasedRetrospectiveArchiveEntry] = []
    for directory in sorted(path for path in retrospectives_root.iterdir() if path.is_dir()):
        entry = _entry_from_deployed_directory(dispatch, directory)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda entry: (entry.year, entry.month, entry.batch_id), reverse=True)


def render_retrospective_recoveries_section(entries: list[SourceBasedRetrospectiveArchiveEntry]) -> str:
    if not entries:
        return ""
    items = "".join(
        "".join(
            [
                '<li class="retrospective-recovery">',
                f'<a href="{html.escape(entry.href, quote=True)}">{html.escape(entry.label)}</a>',
                f'<br><small>{html.escape(entry.summary)}</small>',
                "</li>",
            ]
        )
        for entry in entries
    )
    visible_text = (
        "Retrospective recoveries "
        "Source-backed developments recovered through later review that were not included in the original daily archive. "
        + " ".join(entry.label + " " + entry.summary for entry in entries)
    ).lower()
    exposed_terms = [term for term in INTERNAL_RETROSPECTIVE_ARCHIVE_TERMS if term in visible_text]
    if exposed_terms:
        raise SourceBasedRetrospectiveArchiveError(
            "retrospective archive copy exposes internal terms: " + ", ".join(exposed_terms)
        )
    return (
        '    <section class="retrospective-recoveries">\n'
        "      <h2>Retrospective recoveries</h2>\n"
        "      <p>Source-backed developments recovered through later review that were not included in the original daily archive.</p>\n"
        f'      <ul class="edition-list">{items}</ul>\n'
        "    </section>\n"
    )
