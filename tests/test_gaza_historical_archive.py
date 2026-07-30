import base64
import hashlib
import json
from pathlib import Path

from bluefern_dispatches.historical_agent_archive import normalize_records
from scripts.import_historical_agent_runs import main


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def finding(url: str = "https://example.com/gaza-story", **extra) -> dict:
    value = {
        "source_url": url,
        "title": "Gaza historical alert",
        "publisher": "Example News",
        "source_published_at": "2026-07-01T12:00:00Z",
        "event_date": "2026-07-01",
        "exact_supporting_passage": "The report documents a specific development affecting civilians in Gaza.",
        "gaza_role": "core_gaza",
        "source_role": "reported_public_source",
    }
    value.update(extra)
    return value


def envelope(*rows: dict) -> dict:
    return {
        "schema_version": 1,
        "agent_name": "Gaza Source Watch",
        "agent_run_id": "gaza-run-1",
        "started_at": "2026-07-02T00:00:00Z",
        "findings": list(rows),
    }


def normalize(root: Path, row: dict):
    return normalize_records(
        root,
        "gaza",
        envelope(row),
        raw_sha256="raw-sha",
        captured_at="2026-07-02T00:00:00Z",
    )


def test_published_edition_match_is_provenance_only(tmp_path: Path):
    write_json(tmp_path / "data/records/editions.json", [{
        "edition_id": "gaza-2026-07-01",
        "dispatch_id": "dispatch-gaza",
        "edition_date": "2026-07-01",
        "status": "public",
    }])
    write_json(tmp_path / "output/dispatches/gaza/editions/2026-07-01/sources_manifest.json", [{
        "source_record_id": "source-published",
        "url": "https://example.com/published",
        "title": "Published Gaza source",
        "publisher": "Example News",
        "published_at": "2026-07-01T12:00:00Z",
        "used_in_story_ids": ["story-published"],
    }])
    records, outcomes = normalize(tmp_path, finding("https://example.com/published"))
    record = records[0]
    assert outcomes == {"matched_published_edition": 1}
    assert record["matched_edition_date"] == "2026-07-01"
    assert record["matched_source_or_cluster_id"] == "source-published"
    assert record["provenance_only"] is True
    assert record["candidate_created"] is False
    assert record["publication_eligible"] is False


def test_existing_source_url_and_manual_identifier_create_no_story(tmp_path: Path):
    write_json(tmp_path / "data/dispatches/gaza/sources/2026-07-01/manual_sources.json", [{
        "source_record_id": "manual-source-1",
        "url": "https://example.com/manual",
        "title": "Manual Gaza source",
        "publisher": "Example News",
        "published_at": "2026-07-01T12:00:00Z",
        "region_scope": "Gaza",
    }])
    for row in (
        finding("https://example.com/manual"),
        finding("https://different.example/story", manual_source_identifier="manual-source-1"),
    ):
        record = normalize(tmp_path, row)[0][0]
        assert record["historical_outcome"] == "matched_existing_source"
        assert record["matched_source_or_cluster_id"] == "manual-source-1"
        assert record["candidate_created"] is False
        assert record["provenance_only"] is True


def test_conflicting_url_is_not_overridden_by_matching_headline(tmp_path: Path):
    write_json(tmp_path / "data/dispatches/gaza/sources/2026-07-01/manual_sources.json", [{
        "source_record_id": "manual-source-1",
        "url": "https://example.com/authoritative",
        "title": "Gaza historical alert",
        "publisher": "Example News",
        "published_at": "2026-07-01T12:00:00Z",
        "region_scope": "Gaza",
    }])
    record = normalize(tmp_path, finding("https://example.com/conflicting"))[0][0]
    assert record["historical_outcome"] == "new_historical_candidate"
    assert record["match_basis"] == "unmatched_traceable_finding"


def test_existing_event_cluster_matches_exact_identifier(tmp_path: Path):
    write_json(tmp_path / "data/records/story_memory.json", [{
        "dispatch_slug": "gaza",
        "story_id": "story-cluster-1",
        "topic_fingerprint": "cluster-fingerprint",
        "edition_date": "2026-07-01",
        "title": "Existing Gaza cluster",
        "publisher_names": ["Example News"],
        "source_urls": ["https://example.com/cluster-source"],
    }])
    record = normalize(tmp_path, finding("https://example.com/new-source", topic_fingerprint="cluster-fingerprint"))[0][0]
    assert record["historical_outcome"] == "matched_existing_cluster"
    assert record["match_basis"] == "cluster_identifier"
    assert record["matched_source_or_cluster_id"] in {"cluster-fingerprint", "story-cluster-1"}
    assert record["provenance_only"] is True
    assert record["candidate_created"] is False


def test_unmatched_traceable_finding_remains_private_candidate(tmp_path: Path):
    record = normalize(tmp_path, finding())[0][0]
    assert record["historical_outcome"] == "new_historical_candidate"
    assert record["candidate_created"] is True
    assert record["review_status"] == "pending_review"
    assert record["publication_eligible"] is False
    assert record["publication_approval"] is False


def test_west_bank_only_material_is_archived_context(tmp_path: Path):
    record = normalize(tmp_path, finding(region_scope="West Bank", gaza_role="context_only"))[0][0]
    assert record["historical_outcome"] == "archived_context"
    assert record["candidate_created"] is False
    assert record["review_status"] == "historical_context"
    assert record["publication_eligible"] is False


def test_weak_evidence_is_archived_invalid(tmp_path: Path):
    row = finding()
    row.pop("exact_supporting_passage")
    row["summary"] = "General historical background."
    record = normalize(tmp_path, row)[0][0]
    assert record["historical_outcome"] == "archived_invalid"
    assert record["candidate_created"] is False
    assert record["review_status"] == "excluded"
    assert record["exclusion_reason"] == "missing exact supporting evidence"


def test_repeat_import_is_idempotent_and_preserves_raw_bytes_and_dates(tmp_path: Path, capsys):
    source = tmp_path / "gaza-alert.json"
    raw = json.dumps(envelope(finding()), ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)
    argv = ["import", "--domain", "gaza", "--input", str(source), "--repo-root", str(tmp_path)]
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    digest = hashlib.sha256(raw).hexdigest()
    stored_raw = json.loads((tmp_path / f"data/agent-history/gaza/raw/{digest}.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / f"data/agent-history/gaza/normalized/{digest}.json").read_text(encoding="utf-8"))
    assert first["status"] == "imported"
    assert second["status"] == "idempotent_noop"
    assert base64.b64decode(stored_raw["raw_bytes_base64"]) == raw
    assert normalized["findings"][0]["source_published_at"] == "2026-07-01T12:00:00Z"
    assert normalized["findings"][0]["event_date"] == "2026-07-01"


def test_gaza_report_contract_is_explicit(tmp_path: Path, capsys):
    source = tmp_path / "gaza-alert.json"
    source.write_text(json.dumps(envelope(finding())), encoding="utf-8")
    assert main(["dry-run", "--domain", "gaza", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    report = result["gaza_findings"][0]
    for field in (
        "raw_sha256", "agent_name", "agent_run_id", "source_url", "publisher", "source_date",
        "event_date", "title", "gaza_role", "source_role", "matched_edition_date",
        "matched_source_or_cluster_id", "match_basis", "historical_outcome", "candidate_created",
        "provenance_only", "review_status", "publication_eligible", "exclusion_reason",
    ):
        assert field in report
    assert report["publication_eligible"] is False


def test_import_does_not_mutate_gaza_pipeline_or_public_artifacts(tmp_path: Path, capsys):
    protected = [
        tmp_path / "data/dispatches/gaza/editions/marker.txt",
        tmp_path / "output/dispatches/gaza/audio-marker.txt",
        tmp_path / "output/site/gaza/marker.txt",
        tmp_path / "bluefern-dispatches-pages/gaza/marker.txt",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")
    source = tmp_path / "gaza-alert.json"
    source.write_text(json.dumps(envelope(finding())), encoding="utf-8")
    assert main(["import", "--domain", "gaza", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert all(path.read_text(encoding="utf-8") == "unchanged" for path in protected)
    assert not (tmp_path / "data/dispatches/gaza/discovery").exists()
    assert not (tmp_path / "data/dispatches/gaza/publication-state").exists()


def test_batch_validate_and_dry_run_write_nothing(tmp_path: Path, capsys):
    staging = tmp_path / "staging"
    write_json(staging / "alert.json", envelope(finding()))
    for operation in ("batch-validate", "batch-dry-run"):
        assert main([operation, "--domain", "gaza", "--input-dir", str(staging), "--repo-root", str(tmp_path)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["valid_files"] == 1
        assert result["publication_ready_count"] == 0
        assert not (tmp_path / "data/agent-history").exists()
