from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.gaza_manual_source_repair as repair


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-manual-source-repair"
    root.mkdir(parents=True)
    return root


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = make_root(repo)
    monkeypatch.setattr(repair, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def write_payload(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sample_record(*, url: str, traceability_note: str = "", attribution_mode: str = "", claim_status: str = "") -> dict[str, str]:
    return {
        "source_record_id": "gaza-src-2026-07-05-001",
        "title": "Example Gaza source",
        "url": url,
        "publisher": "EL PAÍS English",
        "published_at": "2026-07-05T02:28:30+00:00",
        "retrieved_at": "2026-07-05T02:35:00+00:00",
        "summary_or_snippet": "Source-backed update.",
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
        "traceability_note": traceability_note,
        "attribution_mode": attribution_mode,
        "claim_status": claim_status,
    }


def test_check_detects_missing_fields_and_does_not_modify_file(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    payload = [sample_record(url="https://example.com/article"), sample_record(url="https://example.com/complete", traceability_note="Existing note", attribution_mode="reported_public_source", claim_status="reported_public_source")]
    write_payload(path, payload)
    before = path.read_text(encoding="utf-8")

    code = repair.main(["--date", "2026-07-05", "--check"])

    output = capsys.readouterr().out
    after = path.read_text(encoding="utf-8")
    assert code == 1
    assert before == after
    assert "Status: repair needed" in output
    assert "Record 1" in output
    assert "traceability_note, attribution_mode, claim_status" in output
    assert "Run with --apply to write missing fields." in output


def test_apply_fills_missing_fields_and_preserves_existing_values(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    payload = [
        sample_record(url="https://example.com/article", traceability_note="", attribution_mode="", claim_status=""),
        sample_record(
            url="https://example.com/complete",
            traceability_note="Keep me",
            attribution_mode="reported_public_source",
            claim_status="reported_public_source",
        ),
    ]
    write_payload(path, payload)

    code = repair.main(["--date", "2026-07-05", "--apply"])

    output = capsys.readouterr().out
    repaired = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0
    assert repaired[0]["traceability_note"]
    assert repaired[0]["attribution_mode"] == "reported_public_source"
    assert repaired[0]["claim_status"] == "reported_public_source"
    assert repaired[1]["traceability_note"] == "Keep me"
    assert repaired[1]["attribution_mode"] == "reported_public_source"
    assert repaired[1]["claim_status"] == "reported_public_source"
    assert "Status: repaired" in output
    assert "- traceability_note: 1" in output
    assert "python scripts\\gaza_operator_status.py --date 2026-07-05 --no-live" in output


def test_google_news_wrapper_note_is_labeled_as_wrapper(isolated: Path) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    write_payload(
        path,
        [
            sample_record(
                url="https://news.google.com/rss/articles/CBMiTWh0dHBzOi8vZXhhbXBsZS5jb20vZmFrZS1saW5r?oc=5",
            )
        ],
    )

    report = repair.build_report("2026-07-05", apply=False)
    note = report["proposed_traceability_notes"][1]
    assert "Google News RSS wrapper" in note
    assert "published_at" in note


def test_direct_publisher_url_note_is_labeled_as_direct_publisher_url(isolated: Path) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    write_payload(
        path,
        [
            sample_record(
                url="https://www.example.com/news/gaza-update",
            )
        ],
    )

    report = repair.build_report("2026-07-05", apply=False)
    note = report["proposed_traceability_notes"][1]
    assert "direct publisher URL" in note
    assert "Google News RSS wrapper" not in note


@pytest.mark.parametrize("container_key", ["list", "sources", "records"])
def test_supports_root_list_and_object_structures(isolated: Path, container_key: str) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    record = sample_record(url="https://www.example.com/news/gaza-update")
    if container_key == "list":
        payload: object = [record]
    elif container_key == "sources":
        payload = {"sources": [record], "meta": {"edition_date": "2026-07-05"}}
    else:
        payload = {"records": [record], "meta": {"edition_date": "2026-07-05"}}
    write_payload(path, payload)

    code = repair.main(["--date", "2026-07-05", "--apply"])

    repaired = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0
    if container_key == "list":
        assert isinstance(repaired, list)
    else:
        assert isinstance(repaired, dict)
        assert container_key in repaired
        assert repaired["meta"]["edition_date"] == "2026-07-05"
    assert str(repaired[0]["traceability_note"] if container_key == "list" else repaired[container_key][0]["traceability_note"]).strip()


@pytest.mark.parametrize("payload", [None, {"bad": "shape"}])
def test_invalid_or_missing_file_returns_actionable_failure(isolated: Path, capsys: pytest.CaptureFixture[str], payload: object) -> None:
    path = repair.manual_sources_path(isolated, "2026-07-05")
    if payload is not None:
        write_payload(path, payload)

    code = repair.main(["--date", "2026-07-05", "--check"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Status: missing" in output or "Status: invalid" in output
    assert "Next action:" in output
