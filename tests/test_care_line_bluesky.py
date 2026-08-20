from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bluefern_dispatches import care_line_bluesky as bluesky


EDITION = "2026-08-09"


def _write_release_fixture(root: Path, *, item_count: int = 2) -> None:
    review_root = root / "data" / "dispatches" / "care-line" / "review"
    proposal_dir = review_root / "proposed-editions"
    snapshot_dir = review_root / "signal-reviews"
    edition_dir = root / "output" / "site" / "care-line" / "editions" / EDITION
    proposal_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    edition_dir.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    asset = Path(__file__).resolve().parents[1] / "assets" / "care-line-dispatch-social.png"
    (root / "assets" / "care-line-dispatch-social.png").write_bytes(asset.read_bytes())
    items = []
    approved_ids = []
    for index in range(item_count):
        candidate_id = f"care-line-candidate-{index + 1:03d}"
        approved_ids.append(candidate_id)
        items.append(
            {
                "candidate_id": candidate_id,
                "review_item_id": f"review-{index + 1:03d}",
                "source_name": "Example Health News",
                "source_title": f"Care Line approved headline {index + 1}",
                "source_url": f"https://example.test/care-line/{index + 1}",
                "source_date": EDITION,
                "reviewed_at": "2026-08-09T00:00:00Z",
                "approved_geography": "Example State",
                "approved_public_claim": f"Approved healthcare access claim {index + 1}.",
                "bounded_public_summary": f"Approved healthcare access summary {index + 1}.",
                "approved_service_line": "care_access",
                "approved_event_type": "service_line_closure",
                "approved_access_consequence": "reduced_access",
                "exact_supporting_passage": f"Exact supporting passage {index + 1}.",
                "evidence_level": "article_excerpt",
                "notes": f"note {index + 1}",
            }
        )
    proposal = {
        "schema_version": "bluefern.care_line.proposed_edition.v1",
        "edition_date": EDITION,
        "headline": "Care Line limited-source update",
        "edition_summary": "Approved healthcare access summary 1.",
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
        "source_adequacy_label": "Limited-source update",
        "approved_signal_ids": approved_ids,
    }
    snapshot = {
        "schema_version": "bluefern.care_line.review_snapshot.v2",
        "edition_date": EDITION,
        "reviewed_at": "2026-08-09T00:30:00Z",
        "review_payload": {"edition_date": EDITION, "items": items},
    }
    (proposal_dir / f"{EDITION}.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    (snapshot_dir / f"{EDITION}.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    manifest = {
        "edition_date": EDITION,
        "public_url": bluesky.public_url_for_edition(EDITION),
        "public_rendered": True,
        "public_signal_count": item_count,
        "edition_mode": "current_update",
        "validation_status": "ok",
        "public_summary": "Approved Care Line healthcare-access developments remain traceable and release-ready.",
        "source_adequacy_label": "Limited-source update",
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
    }
    (edition_dir / "edition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def test_preview_uses_care_line_asset_and_distinct_family(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    preview = bluesky.build_care_line_bluesky_preview(tmp_path, EDITION)
    asset = tmp_path / "assets" / "care-line-dispatch-social.png"
    assert preview["dispatch_slug"] == "care-line"
    assert preview["card_image_path"] == "assets/care-line-dispatch-social.png"
    assert preview["card_image_sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()
    assert preview["card_title"] == "Care Line — August 9, 2026"
    assert preview["card_description"] == "Read the source-backed U.S. healthcare access dispatch from The Blue Fern Co."
    assert "Care Line Dispatch" in preview["post_text"]
    assert preview["public_url"] == bluesky.public_url_for_edition(EDITION)


def test_preview_includes_also_covered_for_multiple_items(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path, item_count=2)
    preview = bluesky.build_care_line_bluesky_preview(tmp_path, EDITION)
    assert "Also covered:" in preview["post_text"]
    assert "Approved healthcare access summary 1." in preview["post_text"]
    assert "Approved healthcare access summary 2." in preview["post_text"]


def test_preview_write_is_deterministic(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    first = bluesky.write_care_line_bluesky_preview(tmp_path, EDITION)
    second = bluesky.write_care_line_bluesky_preview(tmp_path, EDITION)
    assert first["json_path"].read_text(encoding="utf-8") == second["json_path"].read_text(encoding="utf-8")
    assert first["preview"]["content_sha256"] == second["preview"]["content_sha256"]


def test_duplicate_receipt_blocks_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_release_fixture(tmp_path)
    public_url = bluesky.public_url_for_edition(EDITION)
    receipt = tmp_path / "data" / "dispatches" / "care-line" / "editions" / EDITION / "bluesky_post.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"status": "success", "public_url": public_url, "post_uri": "at://example/post"}), encoding="utf-8")
    monkeypatch.setenv("BLUESKY_HANDLE", "handle")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "password")
    monkeypatch.setattr(bluesky.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky.maybe_post_care_line_dispatch_to_bluesky(
        edition_date=EDITION,
        public_url=public_url,
        post_text="Care Line Dispatch — August 9, 2026",
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=1,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["reason"] == "skipped_existing_receipt"


def test_dry_run_writes_no_network_or_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_release_fixture(tmp_path)
    public_url = bluesky.public_url_for_edition(EDITION)
    monkeypatch.setenv("BLUESKY_HANDLE", "handle")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "password")
    monkeypatch.setattr(bluesky.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky.maybe_post_care_line_dispatch_to_bluesky(
        edition_date=EDITION,
        public_url=public_url,
        post_text="Care Line Dispatch — August 9, 2026",
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=1,
        post_requested=True,
        project_root=tmp_path,
        allow_publish=False,
        dry_run=True,
    )
    assert result["reason"] == "dry_run"
    assert not (tmp_path / "data" / "dispatches" / "care-line" / "editions" / EDITION / "bluesky_post.json").exists()


def test_no_public_signals_blocks_post_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_release_fixture(tmp_path)
    public_url = bluesky.public_url_for_edition(EDITION)
    monkeypatch.setenv("BLUESKY_HANDLE", "handle")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "password")
    monkeypatch.setattr(bluesky.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky.maybe_post_care_line_dispatch_to_bluesky(
        edition_date=EDITION,
        public_url=public_url,
        post_text="Care Line Dispatch — August 9, 2026",
        run_succeeded=True,
        public_rendered=True,
        public_signal_count=0,
        post_requested=True,
        project_root=tmp_path,
    )
    assert result["reason"] == "no_public_signals"
