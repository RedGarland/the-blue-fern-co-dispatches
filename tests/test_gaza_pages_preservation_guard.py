from __future__ import annotations

import pytest

from bluefern_dispatches.gaza_pages_preservation import (
    GazaPagesPreservationError,
    assert_no_update_pages_preserve_baseline,
    check_no_update_page_preservation,
)


CORRECTION_ID = "gaza-edition-correction-v1-fd0e2e8a1d6af65d6fc4"


BASE_INDEX = f"""<!doctype html><html><body>
<article class="gaza-latest-card"><p class="edition-date">2026-10-19</p><h3>WFP reports October cuts to Gaza food assistance</h3><p>WFP summary preserves the published figures and attribution.</p></article>
<ul>
<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-19</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-19/">WFP reports October cuts to Gaza food assistance</a><span class="briefing-row-summary">WFP summary preserves the published figures and attribution.</span><span class="briefing-row-meta">Limited-source update | 1 source</span></span><a class="briefing-row-cta" href="editions/2026-10-19/">Read briefing</a></li>
<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-14</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-14/">Correction to the October 14 Gaza edition</a><span class="briefing-row-meta">Correction - No qualified stories remain in the corrected saved-input selection.</span></span><a class="briefing-row-cta" href="editions/2026-10-14/">Read corrected edition</a></li>
<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-06</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-06/">Daily briefing</a><span class="briefing-row-summary">Historical summary that must remain unchanged.</span><span class="briefing-row-meta">Daily briefing</span></span><a class="briefing-row-cta" href="editions/2026-10-06/">Read briefing</a></li>
</ul>
<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction - October 14, 2026</h2><p>The three unsupported projections were removed and the saved evidence remains preserved.</p></section>
</body></html>"""

BASE_ARCHIVE = f"""<!doctype html><html><body>
<section class="archive-latest"><p class="archive-latest-date">2026-10-19</p><h2>WFP reports October cuts to Gaza food assistance</h2><p>WFP archive summary preserves the published figures and attribution.</p></section>
<section class="archive-month"><ul class="edition-list archive-list">
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-19</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-19/">WFP reports October cuts to Gaza food assistance</a><span class="archive-row-note">WFP archive summary preserves the published figures and attribution. | 1 source</span></span><a class="briefing-row-cta" href="editions/2026-10-19/">Read</a></li>
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-14</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-14/">Correction to the October 14 Gaza edition</a><span class="archive-row-note">Correction - No qualified stories remain in the corrected saved-input selection.</span></span><a class="briefing-row-cta" href="editions/2026-10-14/">Read corrected edition</a></li>
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-06</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-06/">Daily briefing</a><span class="archive-row-note">Historical archive summary that must remain unchanged.</span></span><a class="briefing-row-cta" href="editions/2026-10-06/">Read</a></li>
</ul></section>
<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction - October 14, 2026</h2><p>The three unsupported projections were removed and the saved evidence remains preserved.</p></section>
</body></html>"""


def _insert_index_update(html: str) -> str:
    row = '<li class="briefing-row briefing-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span><span class="briefing-row-meta">2 sources checked</span></li>'
    return html.replace("</ul>", row + "</ul>", 1)


def _insert_archive_update(html: str) -> str:
    row = '<li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span><span class="archive-row-note">2 sources checked</span></li>'
    return html.replace("</ul>", row + "</ul>", 1)


EXPECTED_INDEX_UPDATE = '<li class="briefing-row briefing-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span><span class="briefing-row-meta">2 sources checked</span></li>'
EXPECTED_ARCHIVE_UPDATE = '<li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-20</span><span class="no-update-label">No qualifying update</span><span class="archive-row-note">2 sources checked</span></li>'


def test_oct20_regression_fixture_reproduces_semantic_loss() -> None:
    # This models the faulty release: the allowed HTML paths remain the same,
    # but old rows, a summary, and the formal correction section are lost or
    # rewritten while the new no-update row is present.
    faulty_index = _insert_index_update(BASE_INDEX)
    faulty_index = faulty_index.replace(
        '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-06</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-06/">Daily briefing</a><span class="briefing-row-summary">Historical summary that must remain unchanged.</span><span class="briefing-row-meta">Daily briefing</span></span><a class="briefing-row-cta" href="editions/2026-10-06/">Read briefing</a></li>',
        "",
    ).replace("Historical summary that must remain unchanged.", "Rewritten historical summary.")
    faulty_index = faulty_index.replace(
        "Correction - No qualified stories remain in the corrected saved-input selection.",
        "Correction | 0 sources",
    )
    faulty_index = faulty_index.replace(f'<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction - October 14, 2026</h2><p>The three unsupported projections were removed and the saved evidence remains preserved.</p></section>', "")

    faulty_archive = _insert_archive_update(BASE_ARCHIVE)
    faulty_archive = faulty_archive.replace(
        '<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-06</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-06/">Daily briefing</a><span class="archive-row-note">Historical archive summary that must remain unchanged.</span></span><a class="briefing-row-cta" href="editions/2026-10-06/">Read</a></li>',
        "",
    ).replace("Historical archive summary that must remain unchanged.", "Rewritten historical archive summary.")
    faulty_archive = faulty_archive.replace(
        "Correction - No qualified stories remain in the corrected saved-input selection.",
        "Correction | 0 sources",
    )
    faulty_archive = faulty_archive.replace(f'<section class="formal-correction" id="{CORRECTION_ID}"><h2>Correction - October 14, 2026</h2><p>The three unsupported projections were removed and the saved evidence remains preserved.</p></section>', "")

    index_report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=faulty_index,
        edition_date="2026-10-20",
    )
    archive_report = check_no_update_page_preservation(
        page_path="gaza/archive.html",
        baseline_html=BASE_ARCHIVE,
        candidate_html=faulty_archive,
        edition_date="2026-10-20",
    )

    assert index_report.missing_dates == ("2026-10-06",)
    assert index_report.changed_dates == ("2026-10-14",)
    assert index_report.missing_correction_ids == (CORRECTION_ID,)
    assert archive_report.missing_dates == ("2026-10-06",)
    assert archive_report.changed_dates == ("2026-10-14",)
    assert archive_report.missing_correction_ids == (CORRECTION_ID,)
    with pytest.raises(GazaPagesPreservationError, match="no-update Pages preservation failed"):
        assert_no_update_pages_preserve_baseline(
            baseline_pages={"gaza/index.html": BASE_INDEX, "gaza/archive.html": BASE_ARCHIVE},
            candidate_pages={"gaza/index.html": faulty_index, "gaza/archive.html": faulty_archive},
            edition_date="2026-10-20",
        )


def test_minimal_no_update_insertion_preserves_rows_summaries_and_correction() -> None:
    reports = assert_no_update_pages_preserve_baseline(
        baseline_pages={"gaza/index.html": BASE_INDEX, "gaza/archive.html": BASE_ARCHIVE},
        candidate_pages={
            "gaza/index.html": _insert_index_update(BASE_INDEX),
            "gaza/archive.html": _insert_archive_update(BASE_ARCHIVE),
        },
        edition_date="2026-10-20",
    )

    assert reports["gaza/index.html"]["ok"] is True
    assert reports["gaza/archive.html"]["ok"] is True
    assert reports["gaza/index.html"]["missing_dates"] == []
    assert reports["gaza/archive.html"]["missing_correction_ids"] == []


def test_changed_latest_summary_is_rejected_even_when_all_dates_remain() -> None:
    rewritten = _insert_index_update(BASE_INDEX).replace(
        "WFP summary preserves the published figures and attribution.",
        "A rewritten latest summary with changed wording.",
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=rewritten,
        edition_date="2026-10-20",
    )

    assert report.missing_dates == ()
    assert report.changed_dates == ("2026-10-19",)
    assert report.changed_protected_sections == ("latest:2026-10-19",)
    assert not report.ok


def test_unexpected_second_new_date_is_rejected() -> None:
    candidate = _insert_index_update(BASE_INDEX).replace(
        "</ul>",
        '<li class="briefing-row briefing-row--no-update"><span class="edition-date">2026-10-21</span><span class="no-update-label">No qualifying update</span></li></ul>',
        1,
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=candidate,
        edition_date="2026-10-20",
    )
    assert report.unexpected_dates == ("2026-10-21",)
    assert not report.ok


def test_empty_baseline_is_rejected_fail_closed() -> None:
    candidate = _insert_index_update(BASE_INDEX)
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html="<html><body></body></html>",
        candidate_html=candidate,
        edition_date="2026-10-20",
    )
    assert report.baseline_empty is True
    assert not report.ok
    with pytest.raises(GazaPagesPreservationError, match="no-update Pages preservation failed"):
        assert_no_update_pages_preserve_baseline(
            baseline_pages={"gaza/index.html": "<html><body></body></html>", "gaza/archive.html": BASE_ARCHIVE},
            candidate_pages={"gaza/index.html": candidate, "gaza/archive.html": _insert_archive_update(BASE_ARCHIVE)},
            edition_date="2026-10-20",
        )


def test_malformed_row_is_rejected_instead_of_skipped() -> None:
    malformed = BASE_INDEX.replace("</ul>", '<li class="briefing-row"><span>missing date</span></li></ul>', 1)
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=malformed,
        candidate_html=_insert_index_update(malformed),
        edition_date="2026-10-20",
    )
    assert report.invalid_rows == ("baseline[3]", "candidate[3]")
    assert not report.ok


def test_duplicate_dates_and_ids_are_rejected() -> None:
    duplicate_date = _insert_index_update(BASE_INDEX).replace(
        "</ul>",
        '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-19</span></li></ul>',
        1,
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=duplicate_date,
        edition_date="2026-10-20",
    )
    assert report.duplicate_dates == ("2026-10-19",)
    assert not report.ok

    duplicate_id = _insert_index_update(BASE_INDEX).replace(
        'class="briefing-row briefing-row--no-update"',
        f'class="briefing-row briefing-row--no-update" id="{CORRECTION_ID}"',
        1,
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=duplicate_id,
        edition_date="2026-10-20",
    )
    assert report.duplicate_ids == (CORRECTION_ID,)
    assert not report.ok


def test_exactly_one_new_row_and_whole_document_order_are_required() -> None:
    no_new_row = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=BASE_INDEX,
        edition_date="2026-10-20",
    )
    assert no_new_row.missing_new_date is True
    assert no_new_row.new_date_count == 0
    assert not no_new_row.ok

    two_new_rows = _insert_index_update(_insert_index_update(BASE_INDEX))
    two_rows = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=two_new_rows,
        edition_date="2026-10-20",
    )
    assert two_rows.missing_new_date is True
    assert two_rows.new_date_count == 2
    assert not two_rows.ok

    first = '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-19</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-19/">WFP reports October cuts to Gaza food assistance</a><span class="briefing-row-summary">WFP summary preserves the published figures and attribution.</span><span class="briefing-row-meta">Limited-source update | 1 source</span></span><a class="briefing-row-cta" href="editions/2026-10-19/">Read briefing</a></li>'
    second = '<li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-14</span><span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-14/">Correction to the October 14 Gaza edition</a><span class="briefing-row-meta">Correction - No qualified stories remain in the corrected saved-input selection.</span></span><a class="briefing-row-cta" href="editions/2026-10-14/">Read corrected edition</a></li>'
    reordered = _insert_index_update(BASE_INDEX.replace(first + "\n" + second, second + "\n" + first))
    reordered_report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=reordered,
        edition_date="2026-10-20",
    )
    assert reordered_report.whole_document_changed is True
    assert not reordered_report.ok


def test_idempotent_retry_and_hash_bindings_are_explicit() -> None:
    candidate = _insert_index_update(BASE_INDEX)
    retry = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=candidate,
        candidate_html=candidate,
        edition_date="2026-10-20",
        allow_idempotent=True,
    )
    assert retry.idempotent is True
    assert retry.ok

    with pytest.raises(GazaPagesPreservationError, match="candidate hash mismatch"):
        check_no_update_page_preservation(
            page_path="gaza/index.html",
            baseline_html=BASE_INDEX,
            candidate_html=candidate,
            edition_date="2026-10-20",
            expected_candidate_sha256="0" * 64,
        )


def test_new_row_markup_is_bound_and_inert() -> None:
    styled = _insert_index_update(BASE_INDEX).replace(
        'class="briefing-row briefing-row--no-update"',
        'class="briefing-row briefing-row--no-update" style="display:none"',
        1,
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=styled,
        edition_date="2026-10-20",
        expected_new_row_html=EXPECTED_INDEX_UPDATE,
    )
    assert report.new_markup_mismatch is True
    assert report.unsafe_new_markup is True
    assert not report.ok

    scripted = _insert_index_update(BASE_INDEX).replace(
        '</li></ul>', '<script>alert(1)</script></li></ul>', 1
    )
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=scripted,
        edition_date="2026-10-20",
        expected_new_row_html=EXPECTED_INDEX_UPDATE,
    )
    assert report.new_markup_mismatch is True
    assert report.unsafe_new_markup is True
    assert not report.ok


def test_expected_row_bindings_must_be_nonempty_for_both_pages() -> None:
    with pytest.raises(GazaPagesPreservationError, match="expected_new_row_html must be a nonempty string"):
        check_no_update_page_preservation(
            page_path="gaza/index.html",
            baseline_html=BASE_INDEX,
            candidate_html=_insert_index_update(BASE_INDEX),
            edition_date="2026-10-20",
            expected_new_row_html="",
        )

    with pytest.raises(GazaPagesPreservationError, match="must contain nonempty strings"):
        assert_no_update_pages_preserve_baseline(
            baseline_pages={"gaza/index.html": BASE_INDEX, "gaza/archive.html": BASE_ARCHIVE},
            candidate_pages={
                "gaza/index.html": _insert_index_update(BASE_INDEX),
                "gaza/archive.html": _insert_archive_update(BASE_ARCHIVE),
            },
            edition_date="2026-10-20",
            expected_new_row_html={
                "gaza/index.html": EXPECTED_INDEX_UPDATE,
                "gaza/archive.html": None,
            },
        )


@pytest.mark.parametrize(
    "active_markup",
    (
        '<link rel="stylesheet" href="https://attacker.example/rewrites.css">',
        '<base href="https://attacker.example/">',
        '<a href="https://attacker.example/">active link</a>',
        '<img src="https://attacker.example/pixel">',
        '<iframe src="https://attacker.example/frame"></iframe>',
    ),
)
def test_new_row_rejects_nested_navigation_presentation_and_embedding_markup(active_markup: str) -> None:
    candidate = _insert_index_update(BASE_INDEX).replace("</li></ul>", active_markup + "</li></ul>", 1)
    report = check_no_update_page_preservation(
        page_path="gaza/index.html",
        baseline_html=BASE_INDEX,
        candidate_html=candidate,
        edition_date="2026-10-20",
    )
    assert report.unsafe_new_markup is True
    assert not report.ok
