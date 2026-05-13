import json
import shutil
import uuid
from pathlib import Path

import scripts.backfill_american_pressure as backfill


def _valid_record(date_str: str, suffix: str) -> dict:
    return {
        "source_record_id": f"ap-{date_str}-{suffix}",
        "source_id": f"source-{suffix}",
        "title": f"Title {suffix}",
        "url": f"https://example.org/{date_str}/{suffix}",
        "publisher": "Publisher",
        "published_at": f"{date_str}T00:00:00Z",
        "retrieved_at": f"{date_str}T12:00:00Z",
        "summary_or_snippet": f"Summary {suffix}",
        "source_type": "official_report_page",
        "region_scope": "United States",
        "category_hint": "food_pressure",
        "reliability_tier": "official_primary",
    }


def _write_manual(root: Path, date_str: str, rows: list[dict]) -> None:
    path = root / "data" / "dispatches" / "american-pressure" / "sources" / date_str / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _make_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "american-pressure-backfill"
    shutil.copytree(repo / "assets", root / "assets")
    return root


def test_backfill_succeeds_for_valid_dates():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        _write_manual(root, "2026-05-12", [_valid_record("2026-05-12", "b")])
        report = backfill.run_backfill(
            root,
            ["2026-05-05", "2026-05-12"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert report["ok"] is True
        assert report["completed_dates"] == ["2026-05-05", "2026-05-12"]
        assert report["failed_dates"] == []
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_fails_missing_manual_file():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        report = backfill.run_backfill(
            root,
            ["2026-05-05", "2026-05-12"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert report["ok"] is False
        assert "2026-05-12" in report["failed_dates"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_fails_invalid_records():
    root = _make_root()
    try:
        bad = _valid_record("2026-05-05", "a")
        bad.pop("url")
        _write_manual(root, "2026-05-05", [bad])
        report = backfill.run_backfill(
            root,
            ["2026-05-05"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert report["ok"] is False
        assert report["failed_dates"] == ["2026-05-05"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_does_not_use_registry_as_public_stories():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        registry = root / "data" / "dispatches" / "american-pressure" / "source_registry.yml"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            "sources:\n"
            "  - source_id: registry-only\n"
            "    name: Registry Only Title\n"
            "    url: https://example.com\n"
            "    publisher: Registry\n"
            "    pillar: food_pressure\n"
            "    geography: US\n"
            "    source_type: official_report_page\n"
            "    reliability_tier: official_primary\n"
            "    update_frequency: monthly\n"
            "    enabled: true\n"
            "    notes: test\n",
            encoding="utf-8",
        )
        report = backfill.run_backfill(
            root,
            ["2026-05-05"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert report["ok"] is True
        html = (root / "output" / "site" / "american-pressure" / "editions" / "2026-05-05" / "index.html").read_text(encoding="utf-8")
        assert "Registry Only Title" not in html
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_does_not_reuse_old_fixture_date_automatically():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        report = backfill.run_backfill(
            root,
            ["2026-05-12"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert report["ok"] is False
        assert report["completed_dates"] == []
        assert report["failed_dates"] == ["2026-05-12"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_writes_report_outside_output_site():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        report = backfill.run_backfill(
            root,
            ["2026-05-05"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        report_path = Path(report["report_path"])
        assert report_path.exists()
        assert "output\\site\\" not in str(report_path).lower()
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_public_output_excludes_detail_and_paid():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        backfill.run_backfill(
            root,
            ["2026-05-05"],
            publish=True,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=False,
        )
        assert not (root / "output" / "site" / "detail").exists()
        assert not (root / "output" / "site" / "paid").exists()
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_allow_partial_marks_ok_when_some_dates_fail():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-05", [_valid_record("2026-05-05", "a")])
        report = backfill.run_backfill(
            root,
            ["2026-05-05", "2026-05-12"],
            publish=False,
            dry_run=False,
            from_manual_sources=True,
            allow_partial=True,
        )
        assert report["ok"] is True
        assert report["completed_dates"] == ["2026-05-05"]
        assert report["failed_dates"] == ["2026-05-12"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
