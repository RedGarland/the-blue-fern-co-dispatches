from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.cascadia_detention_watch import build_detention_watch
from bluefern_dispatches.cascadia_detention_watch_refresh import (
    FetchResult,
    load_registry,
    promote_candidates,
    render_review_dashboard,
    run_refresh,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_FIXTURE = ROOT / "data" / "dispatches" / "cascadia" / "detention_watch" / "baseline_2026-05-26.json"
REGISTRY_FIXTURE = ROOT / "data" / "dispatches" / "cascadia" / "detention_watch" / "source_registry.json"


def test_registry_schema_is_valid():
    registry = load_registry(REGISTRY_FIXTURE)
    assert validate_registry(registry) == []


def test_refresh_output_contains_metadata_not_full_body(tmp_path: Path):
    registry_target = tmp_path / "data" / "dispatches" / "cascadia" / "detention_watch" / "source_registry.json"
    registry_target.parent.mkdir(parents=True, exist_ok=True)
    registry_target.write_text(
        json.dumps(
            [
                {
                    "source_id": "reg-one",
                    "title": "Test source",
                    "url": "https://example.org/test",
                    "source_family": "local_media",
                    "check_frequency": "weekly",
                    "enabled": True,
                    "notes": "test",
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_fetch(url: str, title: str) -> FetchResult:
        return FetchResult(
            status_code=200,
            final_url=url,
            retrieved_at="2026-05-26T10:00:00+00:00",
            title=title,
            content_hash="hash-one",
            snippet="Short snippet only.",
            detected_dates=["2026-05-20"],
            changed=False,
            failed=False,
            notes="",
        )

    result = run_refresh(tmp_path, as_of="2026-05-26", fetcher=fake_fetch)
    assert result["ok"] is True
    payload = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
    source_row = payload["sources"][0]
    assert "status_code" in source_row
    assert "snippet" in source_row
    assert "content_hash" in source_row
    assert "body" not in source_row
    assert "html" not in source_row
    assert source_row["snippet"] == "Short snippet only."


def test_changed_source_creates_review_candidate_not_public_claim(tmp_path: Path):
    registry_target = tmp_path / "data" / "dispatches" / "cascadia" / "detention_watch" / "source_registry.json"
    registry_target.parent.mkdir(parents=True, exist_ok=True)
    registry_target.write_text(
        json.dumps(
            [
                {
                    "source_id": "reg-one",
                    "title": "Test source",
                    "url": "https://example.org/test",
                    "source_family": "local_media",
                    "check_frequency": "weekly",
                    "enabled": True,
                    "notes": "test",
                }
            ]
        ),
        encoding="utf-8",
    )
    review_dir = tmp_path / "output" / "review" / "cascadia" / "detention_watch"
    review_dir.mkdir(parents=True, exist_ok=True)
    previous = {
        "as_of_date": "2026-05-25",
        "sources": [{"source_id": "reg-one", "content_hash": "old-hash", "title": "Old title"}],
        "candidate_claims": [],
    }
    (review_dir / "source_refresh_2026-05-25.json").write_text(json.dumps(previous), encoding="utf-8")

    def fake_fetch(url: str, title: str) -> FetchResult:
        return FetchResult(
            status_code=200,
            final_url=url,
            retrieved_at="2026-05-26T11:00:00+00:00",
            title="New title",
            content_hash="new-hash",
            snippet="Update snippet.",
            detected_dates=[],
            changed=False,
            failed=False,
            notes="",
        )

    result = run_refresh(tmp_path, as_of="2026-05-26", fetcher=fake_fetch)
    payload = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
    assert len(payload["candidate_claims"]) == 1
    candidate = payload["candidate_claims"][0]
    assert candidate["review_status"] == "candidate"
    assert candidate["proposed_claim_text"] == ""
    assert "manual review required" in candidate["notes"].lower()


def test_promotion_refuses_empty_candidate_claim(tmp_path: Path):
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "sources": [{"source_id": "reg-one"}],
                "candidate_claims": [
                    {
                        "source_id": "reg-one",
                        "source_url": "https://example.org",
                        "source_title": "Example",
                        "retrieved_at": "2026-05-26T10:00:00+00:00",
                        "source_family": "local_media",
                        "proposed_claim_class": "reported",
                        "proposed_claim_text": "",
                        "review_status": "approved",
                        "confidence": "medium",
                        "notes": "n/a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="proposed_claim_text is empty"):
        promote_candidates(review_path, "2026-05-26", tmp_path / "update.json")


def test_promotion_refuses_unapproved_candidate(tmp_path: Path):
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "sources": [{"source_id": "reg-one"}],
                "candidate_claims": [
                    {
                        "source_id": "reg-one",
                        "source_url": "https://example.org",
                        "source_title": "Example",
                        "retrieved_at": "2026-05-26T10:00:00+00:00",
                        "source_family": "local_media",
                        "proposed_claim_class": "reported",
                        "proposed_claim_text": "A reviewed claim",
                        "review_status": "candidate",
                        "confidence": "medium",
                        "notes": "n/a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="review_status must be approved"):
        promote_candidates(review_path, "2026-05-26", tmp_path / "update.json")


def test_promotion_refuses_unknown_source_id(tmp_path: Path):
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "sources": [{"source_id": "reg-one"}],
                "candidate_claims": [
                    {
                        "source_id": "reg-two",
                        "source_url": "https://example.org",
                        "source_title": "Example",
                        "retrieved_at": "2026-05-26T10:00:00+00:00",
                        "source_family": "local_media",
                        "proposed_claim_class": "reported",
                        "proposed_claim_text": "A reviewed claim",
                        "review_status": "approved",
                        "confidence": "medium",
                        "notes": "n/a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing or unknown source_id"):
        promote_candidates(review_path, "2026-05-26", tmp_path / "update.json")


def test_existing_public_renderer_still_passes(tmp_path: Path):
    result = build_detention_watch(tmp_path, "2026-05-26", input_path=BASELINE_FIXTURE)
    assert result["ok"] is True
    html = (tmp_path / "output" / "site" / "cascadia" / "detention-watch" / "editions" / "2026-05-26" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "<h2>Sources</h2>" in html


def test_dashboard_html_generated_from_refresh_fixture(tmp_path: Path):
    review_dir = tmp_path / "output" / "review" / "cascadia" / "detention_watch"
    review_dir.mkdir(parents=True, exist_ok=True)
    refresh_path = review_dir / "source_refresh_2026-05-26.json"
    refresh_path.write_text(
        json.dumps(
            {
                "as_of_date": "2026-05-26",
                "sources": [
                    {
                        "source_id": "reg-one",
                        "source_family": "local_media",
                        "title": "Example source",
                        "status_code": 200,
                        "stale": True,
                        "stale_reasons": ["content_hash_changed"],
                        "retrieved_at": "2026-05-26T12:00:00+00:00",
                        "final_url": "https://example.org/item",
                    }
                ],
                "candidate_claims": [
                    {
                        "source_id": "reg-one",
                        "proposed_claim_class": "reported",
                        "proposed_claim_text": "",
                        "review_status": "candidate",
                        "confidence": "low",
                        "notes": "Source changed; manual review required.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dashboard_path = render_review_dashboard(refresh_path)
    assert dashboard_path.exists()
    html = dashboard_path.read_text(encoding="utf-8")
    assert "Total sources checked: 1" in html
    assert "Failed fetches: 0" in html
    assert "Changed sources: 1" in html
    assert "Stale sources: 1" in html
    assert "Candidate claims requiring review: 1" in html
    assert "Content changed (hash)" in html
    assert "changed" in html
    assert "reg-one" in html
    assert "candidate" in html
    assert "Local editorial review only - not for publication" in html


def test_dashboard_not_generated_under_output_site(tmp_path: Path):
    review_dir = tmp_path / "output" / "review" / "cascadia" / "detention_watch"
    review_dir.mkdir(parents=True, exist_ok=True)
    refresh_path = review_dir / "source_refresh_2026-05-26.json"
    refresh_path.write_text(
        json.dumps({"as_of_date": "2026-05-26", "sources": [], "candidate_claims": []}),
        encoding="utf-8",
    )
    dashboard_path = render_review_dashboard(refresh_path)
    assert "output/site" not in str(dashboard_path).replace("\\", "/")
    assert dashboard_path == review_dir / "review_dashboard_2026-05-26.html"
