import json
from datetime import date
from pathlib import Path

import pytest

from bluefern_dispatches import bluesky_post
from bluefern_dispatches import food_line_bluesky_approval as approval
from bluefern_dispatches.food_line_bluesky_preview import build_food_line_bluesky_preview


@pytest.fixture(autouse=True)
def _freeze_food_line_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval, "current_pacific_date", lambda: date(2026, 6, 20))


def _fixture(tmp_path: Path, *, date: str = "2026-06-17", signals: int = 1) -> tuple[str, str]:
    public_url = approval.public_url_for_edition(date)
    summary = "Central Illinois Food Bank says SNAP cuts are straining its ability to meet demand."
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "food-line-dispatch-social.png").write_bytes(b"stable-social-card")
    manifest_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "edition_date": date,
                "public_url": public_url,
                "public_rendered": True,
                "public_signal_count": signals,
                "edition_mode": "current_update",
                "validation_status": "ok",
                "public_summary": summary,
                "bluesky_post_ready": False,
                "bluesky_post_text": None,
            }
        ),
        encoding="utf-8",
    )
    review_path = tmp_path / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{date}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {
                "layout": {
                    "todays_read": [{"summary": summary}],
                    "core_food_pressure_signals": [{"summary": summary}],
                },
                "items": [{"review_item_id": f"{date}-001"}],
            }
        ),
        encoding="utf-8",
    )
    draft = build_food_line_bluesky_preview(tmp_path, date)["post_text"]
    return public_url, draft


def _approved(tmp_path: Path, date: str = "2026-06-17") -> dict:
    payload = approval.build_pending_approval(tmp_path, date)
    payload.update({"approved": True, "approved_at": "2026-07-27T00:00:00Z", "approved_by": "operator"})
    approval.write_approval(tmp_path, payload)
    return payload


def test_missing_approval_blocks_real_post(tmp_path: Path, monkeypatch) -> None:
    public_url, draft = _fixture(tmp_path)
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path,
    )
    assert result["reason"] == "approval_missing"
    assert not (tmp_path / "data" / "dispatches" / "food-line" / "editions" / "2026-06-17" / "bluesky_post.json").exists()


def test_approval_permits_prepare_only_and_sends_nothing(tmp_path: Path) -> None:
    public_url, draft = _fixture(tmp_path)
    _approved(tmp_path)
    result = approval.prepare_post(tmp_path, "2026-06-17")
    assert result["ok"] is True
    assert result["sent"] is False
    assert result["draft_text"] == draft
    assert result["public_url"] == public_url


def test_changed_draft_invalidates_approval(tmp_path: Path) -> None:
    _fixture(tmp_path)
    _approved(tmp_path)
    before = build_food_line_bluesky_preview(tmp_path, "2026-06-17")["post_text"]
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-17" / "edition_manifest.json").read_text())
    manifest["public_summary"] = "A materially different summary changes the canonical preview payload."
    (tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-17" / "edition_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    after = build_food_line_bluesky_preview(tmp_path, "2026-06-17")["post_text"]
    assert before != after
    assert approval.verify_approval(tmp_path, "2026-06-17")["reason"] == "draft_hash_mismatch"


def test_changed_url_invalidates_approval(tmp_path: Path) -> None:
    _fixture(tmp_path)
    payload = _approved(tmp_path)
    payload["public_url"] = "https://example.test/changed/"
    approval.write_approval(tmp_path, payload)
    assert approval.verify_approval(tmp_path, "2026-06-17")["reason"] == "public_url_mismatch"


def test_changed_social_card_invalidates_approval(tmp_path: Path) -> None:
    _fixture(tmp_path)
    _approved(tmp_path)
    (tmp_path / "assets" / "food-line-dispatch-social.png").write_bytes(b"changed-social-card")
    assert approval.verify_approval(tmp_path, "2026-06-17")["reason"] == "social_image_hash_mismatch"


def test_existing_successful_receipt_blocks_duplicate(tmp_path: Path) -> None:
    public_url, draft = _fixture(tmp_path)
    _approved(tmp_path)
    receipt = tmp_path / "data" / "dispatches" / "food-line" / "editions" / "2026-06-17" / "bluesky_post.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"status": "success", "public_url": public_url, "post_uri": "at://example/existing"}), encoding="utf-8")
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path,
    )
    assert result["reason"] == "skipped_existing_receipt"


def test_force_does_not_bypass_approval_integrity(tmp_path: Path) -> None:
    public_url, draft = _fixture(tmp_path)
    payload = _approved(tmp_path)
    payload["social_image_sha256"] = "wrong"
    approval.write_approval(tmp_path, payload)
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path, force_post=True,
    )
    assert result["reason"] == "social_image_hash_mismatch"


def test_no_public_signal_edition_cannot_be_approved(tmp_path: Path) -> None:
    _fixture(tmp_path, signals=0)
    result = approval.approve_draft(tmp_path, "2026-06-17", "operator")
    assert result["reason"] == "no_public_signals"


def test_current_update_manifest_without_legacy_bluesky_fields_still_builds_pending_approval(tmp_path: Path) -> None:
    public_url, draft = _fixture(tmp_path)
    payload = approval.build_pending_approval(tmp_path, "2026-06-17")
    assert payload["public_url"] == public_url
    assert payload["draft_text"] == draft
    assert payload["approved"] is False
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / "2026-06-17" / "edition_manifest.json").read_text())
    assert manifest["public_rendered"] is True
    assert manifest["public_signal_count"] == 1
    assert manifest["validation_status"] == "ok"
    assert manifest["bluesky_post_text"] is None
    assert manifest["bluesky_post_ready"] is False


def test_dry_run_writes_no_posting_or_approval_artifact(tmp_path: Path, monkeypatch) -> None:
    public_url, draft = _fixture(tmp_path)
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path,
        allow_publish=False, dry_run=True,
    )
    edition_dir = tmp_path / "data" / "dispatches" / "food-line" / "editions" / "2026-06-17"
    assert result["reason"] == "dry_run"
    assert not (edition_dir / "bluesky_post.json").exists()
    assert not (edition_dir / "bluesky_approval.json").exists()


def test_approval_serialization_is_deterministic_and_atomic(tmp_path: Path, monkeypatch) -> None:
    _fixture(tmp_path)
    payload = approval.build_pending_approval(tmp_path, "2026-06-17")
    calls = []
    original_replace = approval.os.replace
    monkeypatch.setattr(approval.os, "replace", lambda source, target: (calls.append((source, target)), original_replace(source, target))[1])
    path = approval.write_approval(tmp_path, payload)
    first = path.read_bytes()
    approval.write_approval(tmp_path, payload)
    assert first == path.read_bytes()
    assert calls


def test_pilot_metadata_and_shared_image_metadata_are_preserved() -> None:
    for date in ("2026-06-17", "2026-06-19"):
        html = (Path("output/site/food-line/editions") / date / "index.html").read_text(encoding="utf-8")
        assert 'property="og:title" content="Food Line Dispatch —' in html
        assert 'name="twitter:title" content="Food Line Dispatch —' in html
        assert 'property="og:image" content="https://dispatches.thebluefernco.com/food-line/assets/food-line-dispatch-social.png"' in html
        assert 'name="twitter:image" content="https://dispatches.thebluefernco.com/food-line/assets/food-line-dispatch-social.png"' in html
        assert 'name="twitter:card" content="summary_large_image"' in html


def test_both_pilot_drafts_are_traceable_and_within_limit() -> None:
    for date in ("2026-06-17", "2026-06-19"):
        manifest = json.loads((Path("output/site/food-line/editions") / date / "edition_manifest.json").read_text(encoding="utf-8"))
        artifact = json.loads((Path("data/dispatches/food-line/editions") / date / "bluesky_approval.json").read_text(encoding="utf-8"))
        assert manifest["validation_status"] == "ok"
        assert manifest["public_signal_count"] > 0
        assert manifest["bluesky_post_ready"] is True
        assert len(artifact["draft_text"]) <= 300
        assert artifact["public_url"] in artifact["draft_text"]
        assert artifact["approved"] is False
        assert artifact["posting_model"] == approval.FOOD_LINE_POSTING_MODEL


def test_freshness_boundary_is_three_calendar_days() -> None:
    assert approval.freshness_status("2026-07-24", today=date(2026, 7, 27))["ok"] is True
    old = approval.freshness_status("2026-07-23", today=date(2026, 7, 27))
    assert old["reason"] == "edition_too_old"
    assert old["age_days"] == 4


def test_missing_and_invalid_edition_dates_fail_closed(tmp_path: Path) -> None:
    assert approval.verify_approval(tmp_path, "")["reason"] == "edition_date_missing"
    assert approval.verify_approval(tmp_path, "2026-7-27")["reason"] == "edition_date_invalid"


def test_old_approved_draft_is_ineligible_for_verify_and_prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixture(tmp_path)
    _approved(tmp_path)
    monkeypatch.setattr(approval, "current_pacific_date", lambda: date(2026, 7, 27))
    assert approval.verify_approval(tmp_path, "2026-06-17")["reason"] == "edition_too_old"
    prepared = approval.prepare_post(tmp_path, "2026-06-17")
    assert prepared["reason"] == "edition_too_old"
    assert prepared["sent"] is False


def test_force_does_not_bypass_freshness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    public_url, draft = _fixture(tmp_path)
    _approved(tmp_path)
    monkeypatch.setattr(approval, "current_pacific_date", lambda: date(2026, 7, 27))
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path, force_post=True,
    )
    assert result["reason"] == "edition_too_old"


def test_archival_override_bypasses_age_only_and_labels_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixture(tmp_path)
    _approved(tmp_path)
    monkeypatch.setattr(approval, "current_pacific_date", lambda: date(2026, 7, 27))
    prepared = approval.prepare_post(tmp_path, "2026-06-17", allow_archival=True)
    assert prepared["ok"] is True
    assert prepared["post_classification"] == "archival / retrospective"
    assert prepared["post_text"].startswith("[ARCHIVAL / RETROSPECTIVE]")


def test_archival_override_does_not_bypass_hash_or_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    public_url, draft = _fixture(tmp_path)
    payload = _approved(tmp_path)
    payload["draft_content_hash"] = "wrong"
    approval.write_approval(tmp_path, payload)
    monkeypatch.setattr(approval, "current_pacific_date", lambda: date(2026, 7, 27))
    monkeypatch.setattr(bluesky_post.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")))
    result = bluesky_post.maybe_post_food_line_dispatch_to_bluesky(
        edition_date="2026-06-17", public_url=public_url, post_text=draft, run_succeeded=True,
        public_rendered=True, public_signal_count=1, post_requested=True, project_root=tmp_path,
        allow_archival_bluesky_post=True,
    )
    assert result["reason"] == "draft_hash_mismatch"
    assert not (tmp_path / "data" / "dispatches" / "food-line" / "editions" / "2026-06-17" / "bluesky_post.json").exists()


def test_current_pilots_are_expired_or_unapproved() -> None:
    june_17 = json.loads((Path("data/dispatches/food-line/editions/2026-06-17/bluesky_approval.json")).read_text(encoding="utf-8"))
    june_19 = json.loads((Path("data/dispatches/food-line/editions/2026-06-19/bluesky_approval.json")).read_text(encoding="utf-8"))
    assert june_17["approved"] is False
    assert june_17["approval_status"] == "expired_due_to_age"
    assert june_17["approved_by"] == "operator"
    assert june_19["approved"] is False
