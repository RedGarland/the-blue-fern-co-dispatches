from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.generator import build_site
from bluefern_dispatches.universal_events.care_line_signal_wire import (
    EXPECTED_EVENT_IDS,
    READY_RECORD_IDS,
    build_care_line_signal_wire_publication,
)


REPO = Path(__file__).resolve().parents[1]


def _copy_assets(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "assets", work / "assets")


def _copy_care_line_data(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "data" / "dispatches" / "care-line", work / "data" / "dispatches" / "care-line")


def _copy_phase14e(repo: Path, work: Path) -> None:
    shutil.copytree(
        repo / "data" / "universal_events" / "shadow" / "care-line" / "phase14e-universal-events",
        work / "data" / "universal_events" / "shadow" / "care-line" / "phase14e-universal-events",
    )


def _prepare_work_root(tmp_path: Path) -> Path:
    work = tmp_path / "repo"
    _copy_assets(REPO, work)
    _copy_care_line_data(REPO, work)
    _copy_phase14e(REPO, work)
    return work


def test_phase14f_publication_renders_two_events_and_feeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare_work_root(tmp_path)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    result = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )

    assert result["ok"] is True
    assert all(url.startswith("https://dispatches.thebluefernco.com/") for url in result["public_urls"])
    assert "https://dispatches.thebluefernco.com/signals/" in result["public_urls"]
    assert "https://dispatches.thebluefernco.com/signals/feed.xml" in result["public_urls"]
    assert "https://dispatches.thebluefernco.com/care-line/signals/feed.xml" in result["public_urls"]

    site_root = work / "output" / "site"
    signals_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    signals_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    care_line_feed = (site_root / "care-line" / "signals" / "feed.xml").read_text(encoding="utf-8")
    first_event = site_root / "events" / "event_3b4ad4e528e48744" / "index.html"
    second_event = site_root / "events" / "event_a12dae614b86cfa9" / "index.html"

    assert first_event.exists()
    assert second_event.exists()
    assert "Care Line Signal Wire" in signals_index
    assert "event_3b4ad4e528e48744" in signals_index
    assert "event_a12dae614b86cfa9" in signals_index
    assert signals_feed.count("<item>") == 2
    assert care_line_feed.count("<item>") == 2
    assert signals_feed.index("event_3b4ad4e528e48744") < signals_feed.index("event_a12dae614b86cfa9")
    assert "UCSF Benioff Children" in first_event.read_text(encoding="utf-8")
    assert "ECU Health" in second_event.read_text(encoding="utf-8")

    manifest_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json"
    lineage_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-source-to-event-lineage-report.json"
    duplicate_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-duplicate-idempotency-report.json"
    drafts_path = work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-bluesky-drafts.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    duplicate = json.loads(duplicate_path.read_text(encoding="utf-8"))
    drafts = json.loads(drafts_path.read_text(encoding="utf-8"))

    assert set(manifest["selected_record_ids"]) == READY_RECORD_IDS
    assert set(manifest["event_ids"]) == EXPECTED_EVENT_IDS
    assert sorted(manifest["deferred_record_ids"]) == [
        "care-line-direct-discovery-196621161639f9f2",
        "care-line-direct-discovery-9543c43464dbd7d4",
    ]
    assert len(lineage["events"]) == 2
    assert duplicate["event_ids_unique"] is True
    assert duplicate["rerun_idempotent"] is True
    assert len(drafts["drafts"]) == 2
    assert drafts["drafts"][0]["event_url"].startswith("https://dispatches.thebluefernco.com/events/")


def test_phase14f_publication_fails_closed_when_evidence_is_missing(tmp_path: Path) -> None:
    work = _prepare_work_root(tmp_path)
    reviewed_path = work / "data" / "dispatches" / "care-line" / "reviewed" / "2026-07-22" / "reviewed_records.json"
    payload = json.loads(reviewed_path.read_text(encoding="utf-8"))
    records = payload["records"]
    for row in records:
        if row["producer_record_id"] in READY_RECORD_IDS:
            row["supporting_passage"] = ""
            row["evidence_valid_for_universal_event"] = False
    reviewed_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing faithful evidence passage"):
        build_care_line_signal_wire_publication(work, generated_at="2026-07-24T00:00:00Z")


def test_phase14f_publication_is_deterministic_on_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _prepare_work_root(tmp_path)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-23")

    first = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    site_root = work / "output" / "site"
    first_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    first_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    first_manifest = (work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json").read_text(encoding="utf-8")
    second = build_site(
        work,
        dry_run=False,
        backup_root=work / "backup",
        dispatch_seed_dates={"care-line": "2026-05-23"},
        only_dispatches=("care-line",),
    )
    second_index = (site_root / "signals" / "index.html").read_text(encoding="utf-8")
    second_feed = (site_root / "signals" / "feed.xml").read_text(encoding="utf-8")
    second_manifest = (work / "data" / "universal_events" / "shadow" / "care-line" / "phase14f-signal-wire" / "phase14f-publication-manifest.json").read_text(encoding="utf-8")

    assert first["ok"] is True
    assert second["ok"] is True

    assert first_index == second_index
    assert first_feed == second_feed
    assert first_manifest == second_manifest
