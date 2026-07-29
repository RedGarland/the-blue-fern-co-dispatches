import base64, hashlib, json
from pathlib import Path

import pytest
import bluefern_dispatches.historical_agent_archive as archive
from bluefern_dispatches.historical_agent_archive import HistoricalEnvelopeError, archive_root, build_inventory, normalize_records, parse_historical_input, validate_input
from scripts.import_historical_agent_runs import main


def row(url="https://example.com/story", **extra):
    value = {"title": "Historical pressure", "publisher": "Example", "source_url": url, "source_published_at": "2026-07-20", "exact_supporting_passage": "Evidence passage", "summary": "Historical summary", "location_name": "Example City", "state": "NC"}
    value.update(extra); return value


def envelope(rows):
    return {"schema_version": "food_line_agent_run_v1", "agent_name": "historical-fixture", "agent_run_id": "run-1", "started_at": "2026-07-21T01:00:00Z", "findings": rows, "coverage_notes": "synthetic"}


def test_raw_bytes_hash_and_idempotent_import(tmp_path: Path):
    source = tmp_path / "export.json"; raw = json.dumps(envelope([row()]), ensure_ascii=False).encode(); source.write_bytes(raw)
    assert validate_input(source, domain="food-line")["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert main(["import", "--domain", "food-line", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    archive = archive_root(tmp_path, "food-line") / "raw" / (hashlib.sha256(raw).hexdigest() + ".json")
    stored = json.loads(archive.read_text(encoding="utf-8")); assert base64.b64decode(stored["raw_bytes_base64"]) == raw
    assert main(["import", "--domain", "food-line", "--input", str(source), "--repo-root", str(tmp_path)]) == 0


def test_dry_run_does_not_write_and_historical_dates_survive(tmp_path: Path):
    source = tmp_path / "export.md"; source.write_text("old alert", encoding="utf-8")
    assert main(["dry-run", "--domain", "ice", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    assert not (tmp_path / "data/agent-history").exists()
    normalized, outcomes = normalize_records(tmp_path, "food-line", envelope([row()]), raw_sha256="x", captured_at="2099-01-01T00:00:00Z")
    assert normalized[0]["source_published_date"] == "2026-07-20"
    assert normalized[0]["historical_backfill"] is True


def test_care_published_event_is_not_requeued(tmp_path: Path):
    queue = tmp_path / "data/universal_events/publication-state/care-line-reviewed-event-queue.json"; queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({"records": [{"event_id": "event-1", "state": "published"}]}), encoding="utf-8")
    records, outcomes = normalize_records(tmp_path, "care-line", [{"event_id": "event-1", "source_url": "https://example.com/care", "event_date": "2026-07-01", "evidence": "e"}], raw_sha256="x", captured_at="2026-07-28T00:00:00Z")
    assert records[0]["deduplication_outcome"] == "matched_existing"
    assert records[0]["review_status"] == "pending_review"


def test_inventory_counts_private_normalized_records(tmp_path: Path):
    normalized = archive_root(tmp_path, "ice") / "normalized" / "x.json"; normalized.parent.mkdir(parents=True)
    normalized.write_text(json.dumps({"findings": [{"event_date": "2026-01-01", "review_status": "pending_review", "deduplication_outcome": "needs_manual_review"}]}), encoding="utf-8")
    result = build_inventory(tmp_path)
    assert result["domains"]["ice"]["normalized_finding_count"] == 1
    assert result["domains"]["ice"]["pending_review_count"] == 1


def test_same_bytes_different_filenames_and_different_bytes_same_filename_are_safe(tmp_path: Path):
    first = tmp_path / "first.json"; second = tmp_path / "second.json"; payload = json.dumps(envelope([row()])).encode()
    first.write_bytes(payload); second.write_bytes(payload)
    assert main(["import", "--domain", "food-line", "--input", str(first), "--repo-root", str(tmp_path)]) == 0
    assert main(["import", "--domain", "food-line", "--input", str(second), "--repo-root", str(tmp_path)]) == 0
    first.write_bytes(json.dumps(envelope([row(title="Different historical alert")])).encode())
    assert main(["import", "--domain", "food-line", "--input", str(first), "--repo-root", str(tmp_path)]) == 0
    assert len(list((tmp_path / "data/agent-history/food-line/raw").glob("*.json"))) == 2


def test_multiple_findings_malformed_base64_invalid_domain_and_atomic_cleanup(tmp_path: Path, monkeypatch):
    path = tmp_path / "multi.json"; payload = envelope([row(), row(url="https://example.com/two", title="Second alert")]); path.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_input(path, domain="food-line")["finding_count"] == 2
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps({**payload, "raw_bytes_base64": "not-base64"}), encoding="utf-8")
    assert validate_input(bad, domain="food-line")["malformed_base64"] is True
    with pytest.raises(ValueError): archive.archive_root(tmp_path, "invalid")
    target = tmp_path / "atomic.json"
    def fail_replace(*args): raise OSError("interrupted")
    monkeypatch.setattr(archive.os, "replace", fail_replace)
    with pytest.raises(OSError): archive.atomic_json(target, {"x": 1})
    assert not target.exists() and not list(tmp_path.glob("atomic.json.*.tmp"))


def test_gaza_matches_existing_source_and_ice_stays_private(tmp_path: Path):
    source = tmp_path / "data/dispatches/gaza/sources/2026-01-01.json"; source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"url": "https://example.com/gaza"}), encoding="utf-8")
    records, _ = normalize_records(tmp_path, "gaza", [{"source_url": "https://example.com/gaza", "event_date": "2026-01-01", "evidence": "alert"}], raw_sha256="x", captured_at="2026-07-28T00:00:00Z")
    assert records[0]["deduplication_outcome"] == "matched_existing"
    ice = tmp_path / "ice.json"; ice.write_text("detention alert", encoding="utf-8")
    assert main(["import", "--domain", "ice", "--input", str(ice), "--repo-root", str(tmp_path)]) == 0
    assert not (tmp_path / "output/site").exists()


def _text_alert(payload: dict, *, label: str = "json", suffix: str = "") -> bytes:
    fence = f"```{label}\n" if label else "```\n"
    return ("Human summary\n\n" + fence + json.dumps(payload) + "\n```\n" + suffix).encode("utf-8")


def test_embedded_json_envelope_preserves_private_provenance_and_raw_bytes(tmp_path: Path):
    raw = _text_alert(envelope([row()]), suffix="Operator note")
    payload, metadata = parse_historical_input(raw)
    assert payload["findings"][0]["title"] == "Historical pressure"
    assert metadata["normalization_method"] == "embedded_json_envelope"
    assert metadata["private_text_provenance"]["before_fence"].startswith("Human summary")
    assert metadata["private_text_provenance"]["after_fence"] == "Operator note"
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(raw).hexdigest()


def test_unlabeled_embedded_json_envelope_is_supported(tmp_path: Path):
    payload, metadata = parse_historical_input(_text_alert(envelope([row()]), label=""))
    assert payload["findings"] and metadata["normalization_method"] == "embedded_json_envelope"


@pytest.mark.parametrize("raw", [
    b"```json\n{}\n```\n```json\n{}\n```",
    b"```json\nnot json\n```",
    b"```json\n{\"findings\": {}}\n```",
])
def test_malformed_or_conflicting_fenced_envelopes_fail_closed(raw: bytes):
    with pytest.raises(HistoricalEnvelopeError):
        parse_historical_input(raw)


def test_plain_text_retains_existing_text_envelope_behavior(tmp_path: Path):
    path = tmp_path / "plain.md"; path.write_text("old alert", encoding="utf-8")
    result = validate_input(path, domain="food-line")
    assert result["valid"] is True and result["normalization_method"] == "text_envelope"


def test_embedded_alerts_normalize_structured_findings_without_writes(tmp_path: Path):
    for name, raw in (("durham.md", _text_alert(envelope([row()]))), ("north.md", _text_alert(envelope([row(title="North Texas strain")])))):
        source = tmp_path / name; source.write_bytes(raw)
        assert main(["dry-run", "--domain", "food-line", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    assert not (tmp_path / "data/agent-history").exists()
