"""Fail-closed guards for no-update Gaza Pages previews.

The October 20, 2026 incident showed that an exact path allow-list is not
enough for a no-update publication: the allowed HTML files can still rewrite
historical rows and remove formal correction notices.  These helpers are
deliberately pure so they can be used from previews and publisher preflight.
They allow exactly one approved no-update row (or an explicitly idempotent
retry) and compare the complete parsed document after removing that row.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from bs4 import BeautifulSoup


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CORRECTION_SELECTOR = ".formal-correction[id]"


class GazaPagesPreservationError(ValueError):
    """Raised when a no-update candidate rewrites protected Pages content."""


@dataclass(frozen=True)
class GazaPagePreservationReport:
    """Semantic inventory and preservation result for one Gaza HTML page."""

    page_path: str
    baseline_dates: tuple[str, ...]
    candidate_dates: tuple[str, ...]
    missing_dates: tuple[str, ...]
    changed_dates: tuple[str, ...]
    duplicate_dates: tuple[str, ...]
    unexpected_dates: tuple[str, ...]
    missing_correction_ids: tuple[str, ...]
    changed_correction_ids: tuple[str, ...]
    missing_protected_sections: tuple[str, ...]
    changed_protected_sections: tuple[str, ...]
    invalid_rows: tuple[str, ...] = ()
    duplicate_ids: tuple[str, ...] = ()
    baseline_empty: bool = False
    missing_new_date: bool = False
    new_date_count: int = 0
    whole_document_changed: bool = False
    idempotency_violation: bool = False
    idempotent: bool = False
    baseline_sha256: str = ""
    candidate_sha256: str = ""
    candidate_without_insertion_sha256: str = ""
    new_markup_mismatch: bool = False
    unsafe_new_markup: bool = False

    @property
    def ok(self) -> bool:
        return not any(
            (
                self.missing_dates,
                self.changed_dates,
                self.duplicate_dates,
                self.unexpected_dates,
                self.missing_correction_ids,
                self.changed_correction_ids,
                self.missing_protected_sections,
                self.changed_protected_sections,
                self.invalid_rows,
                self.duplicate_ids,
                self.baseline_empty,
                self.missing_new_date,
                self.whole_document_changed,
                self.idempotency_violation,
                self.new_markup_mismatch,
                self.unsafe_new_markup,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_path": self.page_path,
            "baseline_dates": list(self.baseline_dates),
            "candidate_dates": list(self.candidate_dates),
            "missing_dates": list(self.missing_dates),
            "changed_dates": list(self.changed_dates),
            "duplicate_dates": list(self.duplicate_dates),
            "unexpected_dates": list(self.unexpected_dates),
            "missing_correction_ids": list(self.missing_correction_ids),
            "changed_correction_ids": list(self.changed_correction_ids),
            "missing_protected_sections": list(self.missing_protected_sections),
            "changed_protected_sections": list(self.changed_protected_sections),
            "invalid_rows": list(self.invalid_rows),
            "duplicate_ids": list(self.duplicate_ids),
            "baseline_empty": self.baseline_empty,
            "missing_new_date": self.missing_new_date,
            "new_date_count": self.new_date_count,
            "whole_document_changed": self.whole_document_changed,
            "idempotency_violation": self.idempotency_violation,
            "idempotent": self.idempotent,
            "baseline_sha256": self.baseline_sha256,
            "candidate_sha256": self.candidate_sha256,
            "candidate_without_insertion_sha256": self.candidate_without_insertion_sha256,
            "new_markup_mismatch": self.new_markup_mismatch,
            "unsafe_new_markup": self.unsafe_new_markup,
            "ok": self.ok,
        }


def _page_row_selector(page_path: str) -> str:
    normalized = page_path.replace("\\", "/").lstrip("./")
    if normalized == "gaza/index.html":
        return ".briefing-row"
    if normalized == "gaza/archive.html":
        return ".archive-row"
    raise GazaPagesPreservationError(f"unsupported Gaza preservation page: {page_path}")


def _serialize(node: Any) -> str:
    # BeautifulSoup gives both baseline and candidate the same HTML
    # serialization while retaining the text and attributes that matter to a
    # row or correction notice.
    return str(node)


def _dated_nodes(soup: BeautifulSoup, selector: str, scope: str) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    rows: dict[str, str] = {}
    duplicates: list[str] = []
    invalid: list[str] = []
    for index, node in enumerate(soup.select(selector)):
        date_nodes = node.select(".edition-date")
        date_node = date_nodes[0] if len(date_nodes) == 1 else None
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        if not ISO_DATE_RE.fullmatch(date_text):
            invalid.append(f"{scope}[{index}]")
            continue
        if date_text in rows:
            duplicates.append(date_text)
        else:
            rows[date_text] = _serialize(node)
    return rows, tuple(sorted(duplicates)), tuple(invalid)


def _duplicate_ids(*soups: BeautifulSoup) -> tuple[str, ...]:
    duplicates: set[str] = set()
    for soup in soups:
        seen: set[str] = set()
        for node in soup.find_all(id=True):
            identifier = str(node.get("id") or "").strip()
            if not identifier:
                continue
            if identifier in seen:
                duplicates.add(identifier)
            seen.add(identifier)
    return tuple(sorted(duplicates))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_matches(actual: str, expected: str | None) -> bool:
    if expected is None or not str(expected).strip():
        return True
    return actual == str(expected).strip().removeprefix("sha256:")


def _has_unsafe_markup(node: Any, page_path: str) -> bool:
    """Allow only inert row markup with no presentation or navigation hooks."""

    normalized = page_path.replace("\\", "/")
    allowed_classes = (
        {"briefing-row", "briefing-row--no-update", "edition-date", "no-update-label", "briefing-row-meta"}
        if normalized == "gaza/index.html"
        else {"archive-row", "archive-row--no-update", "edition-date", "no-update-label", "archive-row-note"}
    )
    allowed_tags = {"li", "span"}
    for descendant in [node, *node.find_all(True)]:
        if str(descendant.name).lower() not in allowed_tags:
            return True
        if set(str(attribute).lower() for attribute in descendant.attrs) != {"class"}:
            return True
        classes = descendant.get("class")
        if not isinstance(classes, list) or not classes or not set(str(value) for value in classes).issubset(allowed_classes):
            return True
    return False


def _protected_sections(html: str, page_path: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    sections: dict[str, str] = {}
    for node in soup.select(CORRECTION_SELECTOR):
        section_id = str(node.get("id") or "").strip()
        if section_id:
            sections[f"correction:{section_id}"] = _serialize(node)

    latest_selector = ".gaza-latest-card" if page_path.replace("\\", "/").endswith("index.html") else ".archive-latest"
    for node in soup.select(latest_selector):
        date_node = node.select_one(".edition-date, .archive-latest-date")
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        if ISO_DATE_RE.fullmatch(date_text):
            sections[f"latest:{date_text}"] = _serialize(node)
    return sections


def check_no_update_page_preservation(
    *,
    page_path: str,
    baseline_html: str,
    candidate_html: str,
    edition_date: str,
    allow_idempotent: bool = False,
    expected_baseline_sha256: str | None = None,
    expected_candidate_sha256: str | None = None,
    expected_new_row_html: str | None = None,
) -> GazaPagePreservationReport:
    """Check that a no-update candidate only adds the requested edition row.

    Existing dated rows, their summaries/labels/links, formal correction
    sections, latest-update cards, scripts, styles, and ancestor context must
    remain equivalent after parser normalization.  When the baseline does not
    already contain ``edition_date``, the candidate must add exactly one row
    for that date; that row is removed before the whole-document comparison.
    If the baseline already contains the date, only an exact candidate retry
    is accepted when ``allow_idempotent`` is true.
    """

    if not ISO_DATE_RE.fullmatch(edition_date):
        raise GazaPagesPreservationError(f"invalid no-update edition date: {edition_date}")
    selector = _page_row_selector(page_path)
    baseline_soup = BeautifulSoup(baseline_html, "html.parser")
    candidate_soup = BeautifulSoup(candidate_html, "html.parser")
    baseline_rows, baseline_duplicates, baseline_invalid = _dated_nodes(baseline_soup, selector, "baseline")
    candidate_rows, candidate_duplicates, candidate_invalid = _dated_nodes(candidate_soup, selector, "candidate")
    baseline_sections = _protected_sections(baseline_html, page_path)
    candidate_sections = _protected_sections(candidate_html, page_path)

    baseline_dates = tuple(sorted(baseline_rows))
    candidate_dates = tuple(sorted(candidate_rows))
    baseline_set = set(baseline_rows)
    candidate_set = set(candidate_rows)
    missing_dates = tuple(sorted(baseline_set - candidate_set))
    changed_dates = tuple(sorted(date for date in baseline_set & candidate_set if baseline_rows[date] != candidate_rows[date]))
    unexpected_dates = tuple(sorted(candidate_set - baseline_set - {edition_date}))
    duplicate_dates = tuple(sorted(set(baseline_duplicates) | set(candidate_duplicates)))
    invalid_rows = tuple(sorted((*baseline_invalid, *candidate_invalid)))
    duplicate_ids = _duplicate_ids(baseline_soup, candidate_soup)
    baseline_empty = not baseline_rows

    candidate_new_nodes = [
        node
        for node in candidate_soup.select(selector)
        if len(node.select(".edition-date")) == 1
        and node.select_one(".edition-date").get_text(" ", strip=True) == edition_date
    ]
    new_date_count = len(candidate_new_nodes)
    missing_new_date = edition_date not in baseline_set and new_date_count != 1
    if expected_new_row_html is not None and (
        not isinstance(expected_new_row_html, str) or not expected_new_row_html.strip()
    ):
        raise GazaPagesPreservationError(
            f"{page_path}: expected_new_row_html must be a nonempty string"
        )
    unsafe_new_markup = any(_has_unsafe_markup(node, page_path) for node in candidate_new_nodes)
    new_markup_mismatch = (
        expected_new_row_html is not None
        and new_date_count == 1
        and _serialize(candidate_new_nodes[0]) != expected_new_row_html
    )

    baseline_document = str(baseline_soup)
    candidate_document = str(candidate_soup)
    idempotent = False
    idempotency_violation = False
    candidate_without_insertion = candidate_document
    if edition_date in baseline_set:
        if candidate_document == baseline_document and allow_idempotent:
            idempotent = True
        else:
            idempotency_violation = True
    elif new_date_count == 1:
        candidate_new_nodes[0].extract()
        candidate_without_insertion = str(candidate_soup)
    whole_document_changed = candidate_without_insertion != baseline_document

    baseline_corrections = {key.removeprefix("correction:"): value for key, value in baseline_sections.items() if key.startswith("correction:")}
    candidate_corrections = {key.removeprefix("correction:"): value for key, value in candidate_sections.items() if key.startswith("correction:")}
    missing_correction_ids = tuple(sorted(set(baseline_corrections) - set(candidate_corrections)))
    changed_correction_ids = tuple(
        sorted(
            section_id
            for section_id in set(baseline_corrections) & set(candidate_corrections)
            if baseline_corrections[section_id] != candidate_corrections[section_id]
        )
    )

    missing_protected_sections = tuple(sorted(set(baseline_sections) - set(candidate_sections)))
    changed_protected_sections = tuple(
        sorted(
            key
            for key in set(baseline_sections) & set(candidate_sections)
            if baseline_sections[key] != candidate_sections[key]
        )
    )

    report = GazaPagePreservationReport(
        page_path=page_path,
        baseline_dates=baseline_dates,
        candidate_dates=candidate_dates,
        missing_dates=missing_dates,
        changed_dates=changed_dates,
        duplicate_dates=duplicate_dates,
        unexpected_dates=unexpected_dates,
        missing_correction_ids=missing_correction_ids,
        changed_correction_ids=changed_correction_ids,
        missing_protected_sections=missing_protected_sections,
        changed_protected_sections=changed_protected_sections,
        invalid_rows=invalid_rows,
        duplicate_ids=duplicate_ids,
        baseline_empty=baseline_empty,
        missing_new_date=missing_new_date,
        new_date_count=new_date_count,
        whole_document_changed=whole_document_changed,
        idempotency_violation=idempotency_violation,
        idempotent=idempotent,
        baseline_sha256=_sha256_text(baseline_html),
        candidate_sha256=_sha256_text(candidate_html),
        candidate_without_insertion_sha256=_sha256_text(candidate_without_insertion),
        new_markup_mismatch=new_markup_mismatch,
        unsafe_new_markup=unsafe_new_markup,
    )
    if not _hash_matches(report.baseline_sha256, expected_baseline_sha256):
        raise GazaPagesPreservationError(
            f"{page_path}: baseline hash mismatch (expected {expected_baseline_sha256}, got {report.baseline_sha256})"
        )
    if not _hash_matches(report.candidate_sha256, expected_candidate_sha256):
        raise GazaPagesPreservationError(
            f"{page_path}: candidate hash mismatch (expected {expected_candidate_sha256}, got {report.candidate_sha256})"
        )
    return report


def assert_no_update_pages_preserve_baseline(
    *,
    baseline_pages: Mapping[str, str],
    candidate_pages: Mapping[str, str],
    edition_date: str,
    allow_idempotent: bool = False,
    expected_baseline_sha256: Mapping[str, str] | None = None,
    expected_candidate_sha256: Mapping[str, str] | None = None,
    expected_new_row_html: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fail closed if either approved no-update HTML page rewrites history."""

    expected_paths = ("gaza/index.html", "gaza/archive.html")
    missing_paths = [path for path in expected_paths if path not in baseline_pages or path not in candidate_pages]
    if missing_paths:
        raise GazaPagesPreservationError("missing required Gaza preservation page(s): " + ", ".join(missing_paths))
    if expected_new_row_html is not None:
        if set(expected_new_row_html) != set(expected_paths):
            raise GazaPagesPreservationError("expected_new_row_html must bind both Gaza preservation pages")
        invalid_bindings = [
            path
            for path in expected_paths
            if not isinstance(expected_new_row_html.get(path), str)
            or not str(expected_new_row_html.get(path)).strip()
        ]
        if invalid_bindings:
            raise GazaPagesPreservationError(
                "expected_new_row_html must contain nonempty strings: " + ", ".join(invalid_bindings)
            )

    reports = {
        path: check_no_update_page_preservation(
            page_path=path,
            baseline_html=baseline_pages[path],
            candidate_html=candidate_pages[path],
            edition_date=edition_date,
            allow_idempotent=allow_idempotent,
            expected_baseline_sha256=(expected_baseline_sha256 or {}).get(path),
            expected_candidate_sha256=(expected_candidate_sha256 or {}).get(path),
            expected_new_row_html=(expected_new_row_html or {}).get(path),
        )
        for path in expected_paths
    }
    failures = [report for report in reports.values() if not report.ok]
    if failures:
        details = "; ".join(
            f"{report.page_path}: {report.as_dict()}" for report in failures
        )
        raise GazaPagesPreservationError("no-update Pages preservation failed: " + details)
    return {path: report.as_dict() for path, report in reports.items()}


__all__ = [
    "GazaPagePreservationReport",
    "GazaPagesPreservationError",
    "assert_no_update_pages_preserve_baseline",
    "check_no_update_page_preservation",
]
