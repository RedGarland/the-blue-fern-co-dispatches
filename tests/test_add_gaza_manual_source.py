from __future__ import annotations

import codecs
import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.add_gaza_manual_source as helper
import scripts.run_daily_gaza as daily


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "manual-source-helper"
    root.mkdir(parents=True)
    return root


def make_record(edition_date: str, source_record_id: str = "gaza-2026-07-03-test-001") -> dict[str, str]:
    published_at = f"{edition_date}T04:28:00+02:00"
    return {
        "source_record_id": source_record_id,
        "title": "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
        "url": "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
        "publisher": "EL PAIS English",
        "published_at": published_at,
        "retrieved_at": published_at,
        "summary_or_snippet": "A factual source-backed summary.",
        "source_type": "manual",
        "provider_id": "manual-supplement",
        "region_scope": "Gaza",
        "category_hint": "humanitarian_conditions",
        "reliability_tier": "reported-public-source",
        "attribution_mode": "reported_public_source",
        "claim_status": "reported_public_source",
        "traceability_note": "Traceable to the listed public URL, title, publisher, and published_at retained in this manual source record.",
    }


def write_payload(path: Path, payload: object, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    if bom:
        path.write_bytes(codecs.BOM_UTF8 + text.encode("utf-8"))
    else:
        path.write_text(text, encoding="utf-8")


@pytest.fixture()
def isolated(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    root = make_root(repo)
    monkeypatch.setattr(helper, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_add_manual_source_creates_utf8_no_bom(isolated, capsys):
    root = isolated
    code = helper.main(
        [
            "--date",
            "2026-07-03",
            "--source-record-id",
            "gaza-2026-07-03-elpais-heatwave-water-displacement",
            "--title",
            "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
            "--url",
            "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
            "--publisher",
            "EL PAIS English",
            "--published-at",
            "2026-07-03T04:28:00+02:00",
            "--summary",
            "A factual source-backed summary.",
            "--category-hint",
            "humanitarian_conditions",
        ]
    )

    captured = capsys.readouterr()
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    assert code == 0
    assert captured.err == ""
    assert path.read_bytes()[:3] != codecs.BOM_UTF8
    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["source_record_id"] == "gaza-2026-07-03-elpais-heatwave-water-displacement"
    assert records[0]["traceability_note"]
    _, errors = daily.validate_source_file(path, min_sources=1)
    assert errors == []


def test_add_manual_source_preserves_existing_records_and_upserts_by_source_record_id(isolated, capsys):
    root = isolated
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    existing = [
        make_record("2026-07-03", "gaza-2026-07-03-old-001"),
        make_record("2026-07-03", "gaza-2026-07-03-replace-me"),
    ]
    existing[1]["title"] = "Old title"
    write_payload(path, existing)

    code = helper.main(
        [
            "--date",
            "2026-07-03",
            "--source-record-id",
            "gaza-2026-07-03-replace-me",
            "--title",
            "IDF probes soldiers after video shows blindfolded Palestinian tied to broomstick in Gaza",
            "--url",
            "https://www.jpost.com/israel-news/article-901263",
            "--publisher",
            "The Jerusalem Post",
            "--published-at",
            "2026-07-02T18:05:00+03:00",
            "--summary",
            "A factual source-backed summary.",
            "--category-hint",
            "military_conduct_accountability",
        ]
    )

    captured = capsys.readouterr()
    records = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0
    assert captured.err == ""
    assert len(records) == 2
    assert any(record["source_record_id"] == "gaza-2026-07-03-old-001" for record in records)
    replaced = next(record for record in records if record["source_record_id"] == "gaza-2026-07-03-replace-me")
    assert replaced["title"].startswith("IDF probes soldiers")
    assert replaced["publisher"] == "The Jerusalem Post"


def test_add_manual_source_accepts_bom_input_and_rewrites_no_bom(isolated, capsys):
    root = isolated
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    write_payload(path, [make_record("2026-07-03")], bom=True)

    code = helper.main(
        [
            "--date",
            "2026-07-03",
            "--source-record-id",
            "gaza-2026-07-03-bbc-medical-evacuation-delays",
            "--title",
            "'Two weeks after her death I got a call': Gaza patients face agonising delays for evacuation",
            "--url",
            "https://www.bbc.com/news/articles/cn75ex1dv61o",
            "--publisher",
            "BBC News",
            "--published-at",
            "2026-07-02T11:53:00Z",
            "--summary",
            "A factual source-backed summary.",
            "--category-hint",
            "medical_evacuation_health_system",
            "--replace-file",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert path.read_bytes()[:3] != codecs.BOM_UTF8
    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["source_record_id"] == "gaza-2026-07-03-bbc-medical-evacuation-delays"


def test_add_manual_source_rejects_malformed_existing_object_without_replace_file(isolated, capsys):
    root = isolated
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    write_payload(path, {"unexpected": "object"})

    code = helper.main(
        [
            "--date",
            "2026-07-03",
            "--source-record-id",
            "gaza-2026-07-03-jpost-idf-palestinian-broomstick-video",
            "--title",
            "IDF probes soldiers after video shows blindfolded Palestinian tied to broomstick in Gaza",
            "--url",
            "https://www.jpost.com/israel-news/article-901263",
            "--publisher",
            "The Jerusalem Post",
            "--published-at",
            "2026-07-02T18:05:00+03:00",
            "--summary",
            "A factual source-backed summary.",
            "--category-hint",
            "military_conduct_accountability",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "must be replaced with --replace-file" in captured.err
    assert json.loads(path.read_text(encoding="utf-8")) == {"unexpected": "object"}


def test_validate_only_returns_non_zero_for_malformed_manual_source_file(isolated, capsys):
    root = isolated
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    write_payload(
        path,
        [
            {
                "source_record_id": "gaza-2026-07-03-bad",
                "title": "",
                "url": "https://example.test/bad",
                "publisher": "",
                "published_at": "2026-07-03T00:00:00Z",
                "retrieved_at": "2026-07-03T00:00:00Z",
                "summary_or_snippet": "Bad record.",
                "source_type": "manual",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "humanitarian_conditions",
                "reliability_tier": "reported-public-source",
                "attribution_mode": "reported_public_source",
                "claim_status": "reported_public_source",
            }
        ]
    )

    code = helper.main(["--date", "2026-07-03", "--validate-only"])

    captured = capsys.readouterr()
    assert code == 1
    assert "manual_sources.json is invalid" in captured.err
    assert "missing required fields" in captured.err


def test_helper_output_is_accepted_by_daily_runner_validation(isolated, capsys):
    root = isolated
    path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-07-03" / "manual_sources.json"
    helper.main(
        [
            "--date",
            "2026-07-03",
            "--replace-file",
            "--source-record-id",
            "gaza-2026-07-03-elpais-heatwave-water-displacement",
            "--title",
            "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
            "--url",
            "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
            "--publisher",
            "EL PAIS English",
            "--published-at",
            "2026-07-03T04:28:00+02:00",
            "--summary",
            "A factual source-backed summary.",
            "--category-hint",
            "humanitarian_conditions",
        ]
    )

    _, errors = daily.validate_source_file(path, min_sources=1)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert errors == []
