import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bluefern_dispatches.ice_staging_publication import (
    _reject_symlinks,
    stage_ice_publication,
    tree_hashes,
)
from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_releases,
    render_dispatch_directory_from_template,
    render_homepage_from_template,
    render_sitewide_homepage_from_template,
    select_current_desk_by_slug,
)


NOW = "2026-10-20T21:17:16Z"
ICE_TITLE = "Long Beach mayor seeks federal answers after disputed Oct. 17 ICE encounter"


ROOT_TEMPLATE = (
    '<!doctype html><html><body><main>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid">'
    '<article class="edition-card edition-card--ice-activity-and-consequences">'
    '<p class="topic-badge topic-badge--ice-activity-and-consequences">ICE</p>'
    '<p class="edition-type">Public pilot</p><h3><a href="/ice/">ICE Dispatch - 2026-10-11</a></h3>'
    '<p class="edition-source">ICE Dispatch &middot; October 11, 2026</p>'
    '<p class="edition-summary">Old ICE summary.</p><p class="edition-freshness">Public pilot, no social/audio.</p>'
    '<p class="edition-provenance">Based on reviewed source records</p><p class="edition-meta">3 reviewed events</p>'
    '</article></div></section>'
    '<div class="active-grid"><article class="dispatch-card dispatch-card--featured">'
    '<h3 class="latest-headline"><a href="/ice/">ICE Dispatch - 2026-10-11</a></h3>'
    '<p class="date-line">ICE Dispatch &middot; October 11, 2026</p><h2>ICE Dispatch</h2>'
    '<a class="button" href="/ice/">Read latest</a></article></div>'
    '</main></body></html>'
)


def _write_ice_release(root: Path) -> None:
    edition = root / "ice" / "editions" / "2026-10-20"
    edition.mkdir(parents=True)
    (edition / "index.html").write_text(
        f'<article class="ice-event-card">\n<h2>{ICE_TITLE}</h2><p>Reviewed event text.</p></article>',
        encoding="utf-8",
    )
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch": "ice",
                "edition_date": "2026-10-20",
                "public_archive_title": ICE_TITLE,
                "publication_status": "published",
                "rendered_event_count": 1,
                "accepted_event_count": 1,
                "public_rendered": True,
                "validation_status": "ok",
            }
        ),
        encoding="utf-8",
    )
    (root / "ice" / "archive.html").write_text(
        '<a href="editions/2026-10-20/">October 20</a>', encoding="utf-8"
    )
    (root / "ice" / "index.html").write_text(
        '<a href="editions/2026-10-20/">October 20</a>', encoding="utf-8"
    )


def _reviewed_event() -> dict:
    return {
        "review_status": "QUALIFIES",
        "event_id": "ice-regression-event",
        "event_date": "2026-09-09",
        "event_type": "ICE detention oversight",
        "primary_category": "detention",
        "severity": "high",
        "status": "reported",
        "location": {
            "state_or_territory": "CA",
            "county_or_equivalent": "Los Angeles County",
            "city": "Los Angeles",
            "facility_name": "Los Angeles Processing Center",
            "latitude": 34.0522,
            "longitude": -118.2437,
            "location_precision": "exact_facility",
            "geography_source": "reviewed fixture",
            "geography_provenance": "reviewed fixture",
        },
        "impact": {"detained_count": 42},
        "agencies": {"ice": True},
        "sources": [
            {
                "source_url": "https://official.example/ice/regression",
                "publisher": "Official Source",
                "source_type": "official",
                "tier": 1,
                "published_at": "2026-09-09T12:00:00Z",
                "modified_at": None,
                "date_source": "official_publication_date",
                "date_confidence": "high",
                "retrieved_at": "2026-09-09T16:30:00Z",
                "exact_supporting_passage": "Reviewed fixture evidence.",
            }
        ],
        "verification_status": "source_traceable",
        "corroboration_count": 1,
        "currentness_status": "current",
        "currentness_confidence": "high",
    }


def test_ice_release_is_discoverable_on_both_main_surfaces(tmp_path: Path) -> None:
    _write_ice_release(tmp_path)

    releases = discover_public_releases(
        tmp_path,
        verify_root=tmp_path,
        as_of=date(2026, 10, 20),
        homepage_html=ROOT_TEMPLATE,
    )
    ice = select_current_desk_by_slug(releases)["ice"]

    assert ice.relative_url == "/ice/"
    assert ice.type_label == "Public pilot"
    assert ice.source_count == 1
    assert ice.summary == ICE_TITLE

    root = render_sitewide_homepage_from_template(ROOT_TEMPLATE, ice)
    directory = render_dispatch_directory_from_template(ROOT_TEMPLATE, ice)
    assert ICE_TITLE in root
    assert "1 reviewed event" in root
    assert "October 20, 2026" in root
    assert ICE_TITLE in directory
    assert "Public pilot, no social/audio." in directory


def test_directory_refresh_requires_each_core_desk_but_allows_optional_ice() -> None:
    for missing in ("gaza", "food-line", "care-line"):
        latest = {slug: object() for slug in ("gaza", "food-line", "care-line") if slug != missing}
        latest["ice"] = object()
        with pytest.raises(ValueError, match=missing):
            render_dispatch_directory_from_releases(ROOT_TEMPLATE, latest)


def test_legacy_target_only_refresh_preserves_single_desk_exception(tmp_path: Path) -> None:
    root = tmp_path / "pages"
    _write_ice_release(root)
    ice = select_current_desk_by_slug(
        discover_public_releases(
            root,
            verify_root=root,
            as_of=date(2026, 10, 20),
            homepage_html=ROOT_TEMPLATE,
        )
    )["ice"]
    gaza = replace(
        ice,
        slug="gaza",
        title="Gaza legacy briefing",
        public_url="https://dispatches.thebluefernco.com/gaza/editions/2026-10-20/",
        relative_url="/gaza/editions/2026-10-20/",
        publication_name="Dispatches From Gaza",
        badge_label="GAZA",
        badge_class="gaza",
    )
    legacy = (
        '<html><body><h1>Dispatches From Gaza</h1><h2>Latest Briefing</h2>'
        '<p><a href="editions/2026-10-19/">Read the latest briefing</a></p>'
        '<ul class="edition-list"><li><a href="editions/2026-10-19/">2026-10-19</a></li></ul>'
        '<h2>Recent Editions</h2>'
        "</body></html>"
    )
    rendered = render_dispatch_directory_from_releases(
        legacy,
        {"gaza": gaza},
        target_dispatch="gaza",
    )
    assert "Gaza legacy briefing" in rendered


def test_partial_ice_staging_fails_closed_without_protected_history(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    ice = staging / "ice"
    ice.mkdir(parents=True)
    (ice / "archive_entries.json").write_text(
        json.dumps([{
            "edition_date": "2026-09-09",
            "href": "editions/2026-09-09/",
            "canonical_path": "/ice/editions/2026-09-09/",
        }]), encoding="utf-8"
    )
    before = tree_hashes(ice)

    with pytest.raises(ValueError, match="edition directories do not match"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-09",
            collection_state="healthy",
            staging_root=staging,
            generated_at=NOW,
        )

    assert tree_hashes(ice) == before


def test_partial_ice_staging_imports_and_preserves_protected_history(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-01",
        collection_state="healthy",
        staging_root=baseline,
        first_publication=True,
        generated_at=NOW,
    )
    old_bytes = (baseline / "ice" / "editions" / "2026-09-01" / "index.html").read_bytes()
    old_manifest = (baseline / "ice" / "editions" / "2026-09-01" / "manifest.json").read_bytes()

    staging = tmp_path / "staging"
    ice = staging / "ice"
    ice.mkdir(parents=True)
    (ice / "archive_entries.json").write_text(
        json.dumps([{
            "edition_date": "2026-09-09",
            "href": "editions/2026-09-09/",
            "canonical_path": "/ice/editions/2026-09-09/",
        }]), encoding="utf-8"
    )

    result = stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-09",
        collection_state="healthy",
        staging_root=staging,
        history_baseline_root=baseline,
        generated_at=NOW,
    )

    entries = json.loads((staging / "ice" / "archive_entries.json").read_text(encoding="utf-8"))
    assert [entry["edition_date"] for entry in entries] == ["2026-09-09", "2026-09-01"]
    assert (staging / "ice" / "editions" / "2026-09-01" / "index.html").read_bytes() == old_bytes
    assert (staging / "ice" / "editions" / "2026-09-01" / "manifest.json").read_bytes() == old_manifest
    assert result.receipt["history_baseline_root"] == str(baseline)


def test_candidate_plus_old_history_requires_binding_without_baseline(tmp_path: Path) -> None:
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-01",
        collection_state="healthy",
        staging_root=tmp_path,
        first_publication=True,
        generated_at=NOW,
    )
    before = tree_hashes(tmp_path / "ice")

    with pytest.raises(ValueError, match="authoritative ICE history baseline"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-09",
            collection_state="healthy",
            staging_root=tmp_path,
            generated_at=NOW,
        )

    assert tree_hashes(tmp_path / "ice") == before


def test_empty_staging_requires_first_publication_or_history_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="authoritative ICE history baseline"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-09",
            collection_state="healthy",
            staging_root=tmp_path,
            generated_at=NOW,
        )
    assert not (tmp_path / "ice").exists()


def test_empty_protected_baseline_requires_explicit_first_publication(tmp_path: Path) -> None:
    baseline = tmp_path / "empty-baseline"
    (baseline / "ice").mkdir(parents=True)
    (baseline / "ice" / "archive_entries.json").write_text("[]", encoding="utf-8")
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="is empty.*first_publication"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-09",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )
    assert not (staging / "ice").exists()


def _build_multi_date_baseline(root: Path) -> None:
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-01",
        collection_state="healthy",
        staging_root=root,
        first_publication=True,
        generated_at=NOW,
    )
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-10",
        collection_state="healthy",
        staging_root=root,
        history_baseline_root=root,
        generated_at=NOW,
    )
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-15",
        collection_state="healthy",
        staging_root=root,
        history_baseline_root=root,
        generated_at=NOW,
    )


def test_empty_staging_tree_with_existing_history_requires_baseline(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    old_edition = staging / "ice" / "editions" / "2026-09-01"
    old_edition.mkdir(parents=True)
    (old_edition / "index.html").write_text("unindexed historical asset", encoding="utf-8")
    before = tree_hashes(staging / "ice")

    with pytest.raises(ValueError, match="missing archive_entries"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            generated_at=NOW,
        )

    assert tree_hashes(staging / "ice") == before


def test_empty_archive_with_existing_history_requires_baseline(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    old_edition = staging / "ice" / "editions" / "2026-09-01"
    old_edition.mkdir(parents=True)
    (old_edition / "index.html").write_text("unindexed historical asset", encoding="utf-8")
    (staging / "ice" / "archive_entries.json").write_text("[]", encoding="utf-8")
    before = tree_hashes(staging / "ice")

    with pytest.raises(ValueError, match="has no archive entries"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            generated_at=NOW,
        )

    assert tree_hashes(staging / "ice") == before


def test_candidate_plus_one_old_date_imports_only_from_complete_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    baseline_entries = json.loads(
        (baseline / "ice" / "archive_entries.json").read_text(encoding="utf-8")
    )

    staging = tmp_path / "staging"
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-20",
        collection_state="healthy",
        staging_root=staging,
        first_publication=True,
        generated_at=NOW,
    )
    candidate_entries = json.loads(
        (staging / "ice" / "archive_entries.json").read_text(encoding="utf-8")
    )
    (staging / "ice" / "archive_entries.json").write_text(
        json.dumps(candidate_entries + [baseline_entries[-1]]), encoding="utf-8"
    )

    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-20",
        collection_state="healthy",
        staging_root=staging,
        history_baseline_root=baseline,
        generated_at=NOW,
    )

    entries = json.loads(
        (staging / "ice" / "archive_entries.json").read_text(encoding="utf-8")
    )
    assert [entry["edition_date"] for entry in entries] == [
        "2026-09-20",
        "2026-09-15",
        "2026-09-10",
        "2026-09-01",
    ]
    assert all(
        (staging / "ice" / "editions" / entry["edition_date"] / "manifest.json").exists()
        for entry in entries
    )


def test_unindexed_altered_historical_directory_fails_before_baseline_overlay(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    staging = tmp_path / "staging"
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-20",
        collection_state="healthy",
        staging_root=staging,
        first_publication=True,
        generated_at=NOW,
    )
    altered = staging / "ice" / "editions" / "2026-09-15"
    altered.mkdir(parents=True)
    (altered / "index.html").write_text("altered unindexed history", encoding="utf-8")
    before = tree_hashes(staging / "ice")
    baseline_before = tree_hashes(baseline / "ice")

    with pytest.raises(ValueError, match="absent from archive_entries"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )

    assert tree_hashes(staging / "ice") == before
    assert tree_hashes(baseline / "ice") == baseline_before


def test_conflicting_shared_asset_fails_before_baseline_overlay(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    (baseline / "ice" / "shared.css").write_text("protected shared asset", encoding="utf-8")
    staging = tmp_path / "staging"
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-20",
        collection_state="healthy",
        staging_root=staging,
        first_publication=True,
        generated_at=NOW,
    )
    (staging / "ice" / "shared.css").write_text("altered shared asset", encoding="utf-8")
    before = tree_hashes(staging / "ice")
    baseline_before = tree_hashes(baseline / "ice")

    with pytest.raises(ValueError, match="conflicts with protected baseline"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )

    assert tree_hashes(staging / "ice") == before
    assert tree_hashes(baseline / "ice") == baseline_before


@pytest.mark.parametrize("archive_case", ["missing", "malformed", "wrong_shape"])
def test_missing_or_malformed_protected_archive_fails_closed(
    tmp_path: Path, archive_case: str
) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    archive_path = baseline / "ice" / "archive_entries.json"
    if archive_case == "missing":
        archive_path.unlink()
    elif archive_case == "malformed":
        archive_path.write_text("{", encoding="utf-8")
    else:
        archive_path.write_text(json.dumps({"edition_date": "2026-09-15"}), encoding="utf-8")

    staging = tmp_path / "staging"
    before = tree_hashes(staging / "ice")
    with pytest.raises(ValueError, match="protected ICE history baseline"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )
    assert tree_hashes(staging / "ice") == before


def test_mismatched_baseline_manifest_fails_closed_before_copy(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    manifest = baseline / "ice" / "editions" / "2026-09-10" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["edition_date"] = "2026-09-11"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    staging = tmp_path / "staging"
    before = tree_hashes(staging / "ice")
    with pytest.raises(ValueError, match="does not match edition directory"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )
    assert tree_hashes(staging / "ice") == before


@pytest.mark.parametrize("baseline_case", ["dispatch", "counts", "map", "archive_count"])
def test_baseline_manifest_identity_counts_and_assets_fail_closed(
    tmp_path: Path, baseline_case: str
) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    edition = baseline / "ice" / "editions" / "2026-09-10"
    if baseline_case == "dispatch":
        manifest = json.loads((edition / "manifest.json").read_text(encoding="utf-8"))
        manifest["dispatch"] = "gaza"
        (edition / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        expected = "wrong dispatch"
    elif baseline_case == "counts":
        manifest = json.loads((edition / "manifest.json").read_text(encoding="utf-8"))
        manifest["rendered_event_count"] = 2
        (edition / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        expected = "event counts disagree"
    elif baseline_case == "map":
        (edition / "map_payload.json").unlink()
        expected = "missing map_payload.json"
    else:
        archive = baseline / "ice" / "archive_entries.json"
        entries = json.loads(archive.read_text(encoding="utf-8"))
        next(item for item in entries if item["edition_date"] == "2026-09-10")["event_count"] = 8
        archive.write_text(json.dumps(entries), encoding="utf-8")
        expected = "archive event count"

    staging = tmp_path / "staging"
    with pytest.raises(ValueError, match=expected):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )
    assert not (staging / "ice").exists()


def test_stale_baseline_omission_and_unsafe_archive_path_fail_closed(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    _build_multi_date_baseline(baseline)
    staging = tmp_path / "staging"
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-20",
        collection_state="healthy",
        staging_root=staging,
        first_publication=True,
        generated_at=NOW,
    )
    candidate_entries = json.loads(
        (staging / "ice" / "archive_entries.json").read_text(encoding="utf-8")
    )
    candidate_entries.append({
        "edition_date": "2026-08-01",
        "href": "editions/2026-08-01/",
        "canonical_path": "/ice/editions/2026-08-01/",
    })
    (staging / "ice" / "archive_entries.json").write_text(
        json.dumps(candidate_entries), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="absent from the protected baseline"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            history_baseline_root=baseline,
            generated_at=NOW,
        )

    unsafe_baseline = tmp_path / "unsafe-baseline"
    _build_multi_date_baseline(unsafe_baseline)
    archive_path = unsafe_baseline / "ice" / "archive_entries.json"
    entries = json.loads(archive_path.read_text(encoding="utf-8"))
    entries[0]["href"] = "../outside/"
    archive_path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe or mismatched href"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=tmp_path / "unsafe-staging",
            history_baseline_root=unsafe_baseline,
            generated_at=NOW,
        )


def test_symlink_in_staging_tree_fails_closed_without_copy(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    ice = staging / "ice"
    ice.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (ice / "editions").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this Windows test context")
    with pytest.raises(ValueError, match="contains a symlink"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-20",
            collection_state="healthy",
            staging_root=staging,
            generated_at=NOW,
        )


def test_root_symlink_or_reparse_check_is_fail_closed_with_mocked_windows_behavior(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "ice"
    root.mkdir()
    child = root / "shared-assets"
    child.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(
        Path,
        "stat",
        lambda path: type("Stat", (), {"st_file_attributes": 0})(),
    )
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: type(
            "LStat",
            (),
            {"st_file_attributes": 0x400 if path in {root, child} else 0},
        )(),
    )
    with pytest.raises(ValueError, match="symlink or reparse point"):
        _reject_symlinks(root, label="mocked ICE root")

    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: type(
            "LStat",
            (),
            {"st_file_attributes": 0x400 if path == child else 0},
        )(),
    )
    with pytest.raises(ValueError, match="shared-assets"):
        _reject_symlinks(root, label="mocked ICE descendant")


def test_archive_event_count_bool_and_invalid_calendar_date_fail_closed(tmp_path: Path) -> None:
    for case, value in (("bool", True), ("date", "2026-99-99")):
        baseline = tmp_path / f"baseline-{case}"
        _build_multi_date_baseline(baseline)
        archive = baseline / "ice" / "archive_entries.json"
        entries = json.loads(archive.read_text(encoding="utf-8"))
        if case == "bool":
            entries[0]["event_count"] = value
            expected = "invalid event count"
        else:
            entries[0]["edition_date"] = value
            expected = "invalid calendar date"
        archive.write_text(json.dumps(entries), encoding="utf-8")
        with pytest.raises(ValueError, match=expected):
            stage_ice_publication(
                [_reviewed_event()],
                edition_date="2026-09-20",
                collection_state="healthy",
                staging_root=tmp_path / f"staging-{case}",
                history_baseline_root=baseline,
                generated_at=NOW,
            )


def test_second_promotion_rename_failure_restores_original_tree(tmp_path: Path, monkeypatch) -> None:
    stage_ice_publication(
        [_reviewed_event()],
        edition_date="2026-09-01",
        collection_state="healthy",
        staging_root=tmp_path,
        first_publication=True,
        generated_at=NOW,
    )
    before = tree_hashes(tmp_path / "ice")
    original_rename = Path.rename
    calls = 0

    def fail_second_rename(self: Path, target: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second promotion rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_second_rename)
    with pytest.raises(OSError, match="injected second promotion rename failure"):
        stage_ice_publication(
            [_reviewed_event()],
            edition_date="2026-09-09",
            collection_state="healthy",
            staging_root=tmp_path,
            history_baseline_root=tmp_path,
            generated_at=NOW,
        )

    assert calls >= 3
    assert tree_hashes(tmp_path / "ice") == before
    assert not any(path.name.startswith(".ice-staging-") for path in tmp_path.iterdir())
    assert not any(path.name.startswith(".ice-staging-backup-") for path in tmp_path.iterdir())


def test_ice_draft_malformed_and_count_mismatch_never_become_cards(tmp_path: Path) -> None:
    cases = [
        {"publication_status": "draft"},
        {"publication_status": "unpublished"},
        {"validation_status": "failed"},
        {"validation_status": "validation_failed"},
        {"status": "withdrawn"},
        {"public_release_status": "published", "publication_status": "draft"},
        {"public_release_status": "published", "release_status": "draft"},
        {"validation_status": "ok", "status": "draft"},
        {"suppressed": True},
        {"edition_date": "2026-10-19"},
        {"rendered_event_count": 0, "accepted_event_count": 0},
        {"_remove": "rendered_event_count"},
        {"_remove": "accepted_event_count"},
        {"rendered_event_count": -1, "accepted_event_count": -1},
        {"rendered_event_count": "1", "accepted_event_count": 1},
        {"rendered_event_count": 1, "accepted_event_count": 2},
    ]
    for index, changes in enumerate(cases):
        root = tmp_path / f"case-{index}"
        _write_ice_release(root)
        manifest_path = root / "ice" / "editions" / "2026-10-20" / "edition_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        remove_key = changes.get("_remove")
        if remove_key:
            manifest.pop(remove_key, None)
        manifest.update({key: value for key, value in changes.items() if key != "_remove"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        releases = discover_public_releases(
            root,
            verify_root=root,
            as_of=date(2026, 10, 20),
            homepage_html=ROOT_TEMPLATE,
        )
        assert "ice" not in select_current_desk_by_slug(releases), (index, changes)

    malformed = tmp_path / "malformed"
    _write_ice_release(malformed)
    (malformed / "ice" / "editions" / "2026-10-20" / "edition_manifest.json").write_text(
        "{", encoding="utf-8"
    )
    assert "ice" not in select_current_desk_by_slug(
        discover_public_releases(
            malformed,
            verify_root=malformed,
            as_of=date(2026, 10, 20),
            homepage_html=ROOT_TEMPLATE,
        )
    )


def test_ice_missing_summary_or_fallback_heading_never_reuses_old_summary(tmp_path: Path) -> None:
    root = tmp_path / "empty-summary"
    _write_ice_release(root)
    manifest_path = root / "ice" / "editions" / "2026-10-20" / "edition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["public_archive_title"] = ICE_TITLE
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    index_path = root / "ice" / "editions" / "2026-10-20" / "index.html"
    index_path.write_text("<article class=\"ice-event-card\"><p>no heading</p></article>", encoding="utf-8")
    releases = discover_public_releases(
        root,
        verify_root=root,
        as_of=date(2026, 10, 20),
        homepage_html=ROOT_TEMPLATE,
    )
    assert "ice" not in select_current_desk_by_slug(releases)
