from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches import care_line_bluesky as bluesky
from bluefern_dispatches.care_line_approved_release import build_approved_release_artifacts
from bluefern_dispatches.care_line_release_render import load_approved_release
from scripts import run_care_line_publication_runner as publication_runner


EDITION_DATE = "2026-05-23"
REVIEWED_FIXTURE_PAYLOAD: dict[str, object] = {
    "schema_version": "bluefern.care_line.reviewed_records.v1",
    "records": [
        {
            "producer_record_id": "care-line-2026-05-23-heraldstandard-hospital-funding",
            "source_url": "https://www.heraldstandard.com/news/local-news/2026/may/07/medicaid-cuts-threaten-hundreds-of-hospitals-new-report-finds/",
            "source_title": "Medicaid cuts threaten hundreds of hospitals, new report finds",
            "source_publisher": "Herald-Standard",
            "raw_payload_hash": "care-line-handoff-heraldstandard-20260523",
            "review_status": "reviewed",
            "public_status": "public_approved",
            "care_line_public_eligible": True,
            "record_status": "care_line_only",
            "source_publication_date": "2026-05-07T00:00:00-04:00",
            "announcement_date": "2026-05-07T00:00:00-04:00",
            "state": "PA",
            "event_type": "financial_context",
            "service_line": "",
            "claim_summary": "Medicaid cuts could threaten hospital access and stability.",
            "supporting_passage": "The article describes Medicaid cut risk as a threat to hospital stability and access.",
            "metadata": {},
            "updated_at": "2026-05-23T12:00:00Z",
            "review_date": "2026-05-23T12:00:00Z",
            "evidence_level": "article_excerpt",
        },
        {
            "producer_record_id": "care-line-2026-05-23-kcrg-centerville-clinic-closure",
            "source_url": "https://www.kcrg.com/2026/05/13/river-hills-community-health-center-announces-closure-centerville-clinic/",
            "source_title": "River Hills Community Health Center announces closure of Centerville clinic",
            "source_publisher": "KCRG",
            "raw_payload_hash": "care-line-handoff-kcrg-20260523",
            "review_status": "approved",
            "public_status": "public_approved",
            "care_line_public_eligible": True,
            "record_status": "care_line_only",
            "source_publication_date": "2026-05-12T21:45:00-05:00",
            "announcement_date": "2026-05-12T21:45:00-05:00",
            "state": "IA",
            "event_type": "facility_closure",
            "service_line": "",
            "claim_summary": "A local clinic closure reduces care access in Centerville.",
            "supporting_passage": "The article reports a clinic closure that changes access for local patients.",
            "metadata": {},
            "updated_at": "2026-05-23T12:00:00Z",
            "review_date": "2026-05-23T12:00:00Z",
            "evidence_level": "article_excerpt",
        },
        {
            "producer_record_id": "care-line-2026-05-23-searchlightnm-labor-delivery-halt",
            "source_url": "https://searchlightnm.org/patients-worry-after-los-alamos-medical-center-halts-labor-delivery-services/",
            "source_title": "Patients worry after Los Alamos Medical Center halts labor, delivery services",
            "source_publisher": "Searchlight New Mexico",
            "raw_payload_hash": "care-line-handoff-searchlightnm-20260523",
            "review_status": "approved",
            "public_status": "public_approved",
            "care_line_public_eligible": True,
            "record_status": "care_line_only",
            "source_publication_date": "2026-05-17T00:00:00-06:00",
            "announcement_date": "2026-05-17T00:00:00-06:00",
            "state": "NM",
            "event_type": "service_suspension",
            "service_line": "labor_and_delivery",
            "claim_summary": "Labor and delivery service reductions create a direct maternity access problem.",
            "supporting_passage": "The article describes halted labor and delivery services and the resulting travel burden for patients.",
            "metadata": {},
            "updated_at": "2026-05-23T12:00:00Z",
            "review_date": "2026-05-23T12:00:00Z",
            "evidence_level": "article_excerpt",
        },
    ],
}


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _init_repo(root: Path, branch: str, *, empty_commit: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "tests@example.test")
    _run_git(root, "config", "user.name", "Tests")
    if empty_commit:
        _run_git(root, "commit", "--allow-empty", "-m", "initial")
    else:
        (root / "README.md").write_text("repo", encoding="utf-8")
        _run_git(root, "add", "README.md")
        _run_git(root, "commit", "-m", "initial")
    _run_git(root, "checkout", "-b", branch)
    return root


def _copy_assets(repo_root: Path, work_root: Path) -> None:
    (work_root / "assets").mkdir(parents=True, exist_ok=True)
    for name in ("care-line-dispatch-social.png", "care-line-logo.png", "care-line-mark.png", "bluefern.png"):
        source = repo_root / "assets" / name
        if source.exists():
            shutil.copy2(source, work_root / "assets" / name)


def _write_reviewed_fixture(work_root: Path, payload: dict[str, object]) -> None:
    reviewed_root = work_root / "data" / "dispatches" / "care-line" / "reviewed" / EDITION_DATE
    reviewed_root.mkdir(parents=True, exist_ok=True)
    (reviewed_root / "reviewed_records.json").write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _write_manifest(work_root: Path, *, public_signal_count: int, summary: str, source_label: str) -> None:
    edition_root = work_root / "output" / "site" / "care-line" / "editions" / EDITION_DATE
    edition_root.mkdir(parents=True, exist_ok=True)
    (edition_root / "edition_manifest.json").write_text(
        json.dumps(
            {
                "edition_date": EDITION_DATE,
                "public_url": bluesky.public_url_for_edition(EDITION_DATE),
                "public_rendered": True,
                "public_signal_count": public_signal_count,
                "edition_mode": "current_update",
                "validation_status": "ok",
                "public_summary": summary,
                "source_adequacy_label": source_label,
                "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_handoff_writes_release_artifacts_from_real_reviewed_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    pages = _init_repo(tmp_path / "pages", "gh-pages", empty_commit=True)
    _copy_assets(repo_root, source)
    reviewed_payload = REVIEWED_FIXTURE_PAYLOAD
    _write_reviewed_fixture(source, reviewed_payload)
    _run_git(source, "add", "assets", "data/dispatches/care-line/reviewed")
    _run_git(source, "commit", "-m", "tracked reviewed care line release source")

    result = build_approved_release_artifacts(source, EDITION_DATE)

    assert result["ok"] is True
    assert result["release_ready"] is True
    assert result["approved_item_count"] >= 1
    assert result["proposal_path"]
    assert result["review_snapshot_path"]
    assert Path(result["proposal_path"]).exists()
    assert Path(result["review_snapshot_path"]).exists()

    bundle = load_approved_release(source, EDITION_DATE)
    assert bundle is not None
    assert len(bundle.approved_items) == result["approved_item_count"]
    assert bundle.proposal["release_ready"] is True
    assert bundle.review_snapshot["release_ready"] is True

    _run_git(source, "add", "data/dispatches/care-line/review/proposed-editions", "data/dispatches/care-line/review/signal-reviews")
    _run_git(source, "commit", "-m", "tracked care line approved release artifacts")
    assert _git_output(source, "status", "--short") == ""

    monkeypatch.setattr(publication_runner, "publish_pages", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publish_pages must not run in check-only")))
    monkeypatch.setattr(
        publication_runner,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "public_url": bluesky.public_url_for_edition(EDITION_DATE),
            "public_rendered": True,
            "public_signal_count": result["approved_item_count"],
            "bluesky_post_text": str(result["proposal"]["edition_summary"]),
        },
    )
    runner_result = publication_runner._run_publish_flow(
        repo_root=source,
        pages_repo=pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        edition_date=EDITION_DATE,
        check_only=True,
        dry_run_full=False,
        publish=False,
        push=False,
        post_bluesky=False,
    )
    assert runner_result["ok"] is True
    assert runner_result["status"] == "check_only_ready"
    assert runner_result["release_ready"] is True

    _write_manifest(
        source,
        public_signal_count=result["approved_item_count"],
        summary=str(result["proposal"]["edition_summary"]),
        source_label=str(result["proposal"]["source_adequacy_label"]),
    )
    preview = bluesky.build_care_line_bluesky_preview(source, EDITION_DATE)
    assert preview["dispatch_slug"] == "care-line"
    assert preview["card_image_path"] == "assets/care-line-dispatch-social.png"
    assert preview["card_title"] == "Care Line — May 23, 2026"
    assert preview["post_text"].startswith("Care Line Dispatch — May 23, 2026")
    assert preview["public_url"] == bluesky.public_url_for_edition(EDITION_DATE)


def test_handoff_excludes_pending_and_rejected_reviews(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    reviewed_payload = REVIEWED_FIXTURE_PAYLOAD
    records = list(reviewed_payload["records"])  # type: ignore[index]
    assert len(records) >= 2
    records = [
        {**row, "review_status": "needs_review", "public_status": "not_public", "care_line_public_eligible": False}
        for row in records
    ]
    reviewed_payload = {**reviewed_payload, "records": records}
    _write_reviewed_fixture(source, reviewed_payload)

    result = build_approved_release_artifacts(source, EDITION_DATE)

    assert result["ok"] is True
    assert result["release_ready"] is False
    assert result["approved_item_count"] == 0
    assert result["proposal_path"] is None
    assert result["review_snapshot_path"] is None
    assert not (source / "data" / "dispatches" / "care-line" / "review" / "proposed-editions" / f"{EDITION_DATE}.json").exists()
    assert not (source / "data" / "dispatches" / "care-line" / "review" / "signal-reviews" / f"{EDITION_DATE}.json").exists()


def test_handoff_dedupes_duplicate_approved_signals(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    reviewed_payload = REVIEWED_FIXTURE_PAYLOAD
    records = list(reviewed_payload["records"])  # type: ignore[index]
    approved_records = [
        row
        for row in records
        if isinstance(row, dict) and row.get("review_status") in {"approved", "reviewed", "corrected"} and row.get("public_status") == "public_approved"
    ]
    assert approved_records
    records.append(dict(approved_records[0]))
    reviewed_payload = {**reviewed_payload, "records": records}
    _write_reviewed_fixture(source, reviewed_payload)

    result = build_approved_release_artifacts(source, EDITION_DATE)

    assert result["ok"] is True
    assert result["release_ready"] is True
    assert result["approved_item_count"] == len(approved_records)
    assert len(result["approved_signal_ids"]) == len(set(result["approved_signal_ids"]))
