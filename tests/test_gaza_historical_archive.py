import base64
import hashlib
import json
from pathlib import Path

import pytest

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


def gaza_prose_sidecar(root: Path, raw_path: Path, **extra) -> Path:
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    value = {
        "raw_sha256": digest,
        "raw_file": raw_path.relative_to(root).as_posix(),
        "domain": "gaza",
        "normalization_type": "prose_envelope_to_structured_findings",
        "reviewer": "fixture",
        "reviewed_at": "2026-07-30T00:00:00Z",
        "approved": True,
        "approval_scope": "historical_normalization_only",
        "publication_approval": False,
        "findings": [finding(
            "https://example.com/gaza-report?utm_source=agent",
            canonical_source_url="https://example.com/gaza-report",
            event_date="2026-07-25",
            source_published_at="2026-07-30",
            raw_finding_reference="development paragraph",
        )],
    }
    value.update(extra)
    path = raw_path.parent / "corrections" / "structured.json"
    write_json(path, value)
    return path


def test_structured_gaza_sidecar_normalizes_prose_without_mutation(tmp_path: Path, capsys):
    raw_path = tmp_path / "data/agent-history-staging/gaza/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw = (
        "UNRWA reported an injury in Gaza on July 25, 2026.\n"
        "https://example.com/gaza-report?utm_source=agent\n"
    ).encode()
    raw_path.write_bytes(raw)
    sidecar = gaza_prose_sidecar(tmp_path, raw_path)
    before = raw_path.read_bytes()
    assert main(["dry-run", "--domain", "gaza", "--input", str(raw_path), "--correction", str(sidecar), "--repo-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    record = result["gaza_findings"][0]
    assert result["normalization_method"] == "prose_envelope_to_structured_findings"
    assert result["outcomes"] == {"new_historical_candidate": 1}
    assert record["canonical_source_url"] == "https://example.com/gaza-report"
    assert record["event_date"] == "2026-07-25"
    assert record["source_published_at"] == "2026-07-30"
    assert record["candidate_created"] is True
    assert record["publication_eligible"] is False
    assert raw_path.read_bytes() == before
    assert not (tmp_path / "data/agent-history/gaza").exists()


def test_structured_gaza_sidecar_fails_closed_on_identity_or_approval(tmp_path: Path):
    raw_path = tmp_path / "data/agent-history-staging/gaza/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("https://example.com/gaza-report?utm_source=agent\n", encoding="utf-8")
    sidecar = gaza_prose_sidecar(tmp_path, raw_path, publication_approval=True)
    try:
        main(["dry-run", "--domain", "gaza", "--input", str(raw_path), "--correction", str(sidecar), "--repo-root", str(tmp_path)])
    except ValueError as exc:
        assert "cannot grant publication approval" in str(exc)
    else:
        raise AssertionError("Gaza normalization sidecar must fail closed on publication approval")


def test_structured_gaza_sidecar_import_is_idempotent(tmp_path: Path, capsys):
    staging = tmp_path / "data/agent-history-staging/gaza"
    raw_path = staging / "alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw = (
        "UNRWA reported an injury in Gaza on July 25, 2026.\n"
        "https://example.com/gaza-report?utm_source=agent\n"
    ).encode()
    raw_path.write_bytes(raw)
    gaza_prose_sidecar(tmp_path, raw_path)
    args = ["batch-import", "--domain", "gaza", "--input-dir", str(staging), "--repo-root", str(tmp_path)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    digest = hashlib.sha256(raw).hexdigest()
    stored = json.loads((tmp_path / f"data/agent-history/gaza/raw/{digest}.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / f"data/agent-history/gaza/normalized/{digest}.json").read_text(encoding="utf-8"))
    assert first["imported_files"] == 1
    assert second["idempotent_files"] == 1
    assert base64.b64decode(stored["raw_bytes_base64"]) == raw
    assert normalized["findings"][0]["normalization_sidecar"]["raw_sha256"] == digest
    assert not list((tmp_path / "data/agent-history/gaza/normalized").glob("revision-*.json"))
    history_index = json.loads((tmp_path / "data/agent-history/gaza/reports/history-index.json").read_text(encoding="utf-8"))
    assert history_index["raw_run_count"] == 1
    assert history_index["normalized_finding_count"] == 1
    assert history_index["historical_candidate_count"] == 1
    assert history_index["publication_ready_count"] == 0


def gaza_substantive_review_fixture(root: Path) -> tuple[list[str], dict[str, Path]]:
    raw_bytes = b"private historical Gaza fixture\n"
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    base = root / "data/agent-history/gaza"
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    review_path = base / "reviews" / f"{raw_sha}-substantive-review.json"
    source_url = "https://www.unrwa.org/resources/reports/situation-report-231"
    tracked_url = f"{source_url}?utm_source=fixture"
    restrictions = [
        "Attribute the incident directly to UNRWA and retain reportedly.",
        "Keep the July 25 event date distinct from the July 30 report date.",
        "Do not claim a facility strike, injury severity, hospitalization, operational interruption, independent verification, or additional casualties.",
        "Retrieve Situation Report #231 before future editorial use.",
    ]
    write_json(
        raw_path,
        {
            "domain": "gaza",
            "raw_sha256": raw_sha,
            "raw_bytes_base64": base64.b64encode(raw_bytes).decode("ascii"),
        },
    )
    write_json(
        normalized_path,
        {
            "schema_version": "historical_agent_normalized_v1",
            "domain": "gaza",
            "raw_sha256": raw_sha,
            "findings": [
                {
                    "finding_id": "gaza-unrwa-personnel-injury-fixture",
                    "historical_outcome": "new_historical_candidate",
                    "deduplication_outcome": "new_historical_candidate",
                    "candidate_created": True,
                    "review_status": "pending_review",
                    "source_url": tracked_url,
                    "canonical_source_url": source_url,
                    "source_published_at": "2026-07-30",
                    "event_date": "2026-07-25",
                    "title": "UNRWA reported a personnel injury near Khan Younis Camp",
                    "summary": "UNRWA reported that one of its personnel was reportedly injured in an Israeli airstrike in the Khan Younis Camp area on July 25.",
                    "exact_supporting_passage": "UNRWA Situation Report #231 says one of its personnel was reportedly injured in the Khan Younis Camp area on July 25.",
                    "event_type": "humanitarian_worker_injury",
                    "gaza_role": "humanitarian_operations_and_safety",
                    "source_role": "primary_un_humanitarian_report",
                    "evidence_level": "primary_report_qualified_incident",
                    "confidence": "moderate_high",
                    "operational_impact": "unknown",
                    "queue_action": None,
                    "matched_edition_date": "",
                    "matched_source_or_cluster_id": "",
                    "publication_eligible": False,
                    "publication_approval": False,
                }
            ],
        },
    )
    write_json(
        report_path,
        {
            "domain": "gaza",
            "input_sha256": raw_sha,
            "status": "imported",
        },
    )
    write_json(
        review_path,
        {
            "schema_version": "gaza_substantive_historical_review_v1",
            "domain": "gaza",
            "raw_sha256": raw_sha,
            "normalized_finding_id": "gaza-unrwa-personnel-injury-fixture",
            "review_type": "substantive_historical_review",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "archive_mutation_authorized": False,
            "edition_authorized": False,
            "publication_authorized": False,
            "current_review_status": "pending_review",
            "current_historical_outcome": "new_historical_candidate",
            "current_queue_action": "none",
            "current_publication_eligible": False,
            "current_publication_approval": False,
            "date_assessment": {
                "event_date": "2026-07-25",
                "report_publication_date": "2026-07-30",
            },
            "materiality_assessment": {
                "assessment": "operating_impact_unclear",
            },
            "operational_impact_assessment": {"assessment": "unknown"},
            "attribution_assessment": {
                "safe_future_wording": "UNRWA said one of its personnel was reportedly injured in an Israeli airstrike in the Khan Younis Camp area on July 25.",
            },
            "taxonomy_review": {
                "event_type": {"current_value": "humanitarian_worker_injury"},
                "gaza_role": {
                    "current_value": "humanitarian_operations_and_safety"
                },
                "source_role": {
                    "current_value": "primary_un_humanitarian_report"
                },
                "evidence_level": {
                    "current_value": "primary_report_qualified_incident"
                },
                "confidence": {"current_value": "moderate_high"},
                "operational_impact": {"current_value": "unknown"},
            },
            "duplicate_and_authoritative_match_check": {
                "historical_candidate_remains_distinct": True,
                "existing_edition_match": None,
                "existing_source_match": None,
                "existing_story_cluster_match": None,
                "existing_historical_match": None,
            },
            "editorial_restrictions": restrictions,
        },
    )
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    args = [
        "review",
        "--domain",
        "gaza",
        "--raw-sha",
        raw_sha,
        "--decision",
        "substantively-valid",
        "--review-artifact",
        str(review_path),
        "--review-artifact-sha256",
        review_sha,
        "--repo-root",
        str(root),
    ]
    return args, {
        "raw": raw_path,
        "normalized": normalized_path,
        "report": report_path,
        "review": review_path,
    }


def run_json(capsys, args: list[str]) -> tuple[int, dict]:
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def test_gaza_substantive_review_changes_only_status_and_is_idempotent(
    tmp_path: Path,
    capsys,
):
    authoritative_files = {
        tmp_path / "data/records/editions.json": [],
        tmp_path / "data/records/sources.json": [],
        tmp_path / "data/records/story_memory.json": [],
    }
    for path, value in authoritative_files.items():
        write_json(path, value)
    protected = [
        *authoritative_files,
        tmp_path / "data/dispatches/gaza/editions/marker.txt",
        tmp_path / "data/dispatches/gaza/sources/marker.txt",
        tmp_path / "output/dispatches/gaza/editions/marker.txt",
        tmp_path / "output/site/gaza/marker.txt",
        tmp_path / "output/dispatches/gaza/audio-index-marker.txt",
        tmp_path / "output/site/gaza/podcast-marker.txt",
        tmp_path / "bluefern-dispatches-pages/gaza/marker.txt",
        tmp_path / "data/bluesky/marker.txt",
        tmp_path / "schedules/marker.txt",
        tmp_path / "data/agent-history/food-line/marker.txt",
        tmp_path / "data/agent-history/care-line/marker.txt",
        tmp_path / "data/agent-history/ice/marker.txt",
    ]
    for path in protected:
        if path not in authoritative_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unchanged", encoding="utf-8")
    protected_bytes = {path: path.read_bytes() for path in protected}

    args, paths = gaza_substantive_review_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    before_normalized = json.loads(originals["normalized"].decode("utf-8"))

    code, accepted = run_json(capsys, args)
    assert code == 0
    assert accepted["status"] == "review_status_updated"
    assert accepted["previous_review_status"] == "pending_review"
    assert accepted["new_review_status"] == "substantively_reviewed"
    assert accepted["inventory_before"]["raw_run_count"] == 1
    assert accepted["inventory_before"]["normalized_finding_count"] == 1
    assert accepted["inventory_before"]["historical_candidate_count"] == 1
    assert accepted["inventory_before"]["pending_substantive_review"] == 1
    assert accepted["inventory_before"]["substantively_reviewed"] == 0
    assert accepted["inventory_after"]["pending_substantive_review"] == 0
    assert accepted["inventory_after"]["substantively_reviewed"] == 1
    assert accepted["inventory_after"]["queue_entries"] == 0
    assert accepted["inventory_after"]["publication_ready_count"] == 0

    after_normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(before_normalized))
    expected["findings"][0]["review_status"] = "substantively_reviewed"
    assert after_normalized == expected
    assert "reportedly" in after_normalized["findings"][0]["summary"]
    assert after_normalized["findings"][0]["event_date"] == "2026-07-25"
    assert after_normalized["findings"][0]["source_published_at"] == "2026-07-30"
    assert after_normalized["findings"][0]["operational_impact"] == "unknown"
    assert paths["raw"].read_bytes() == originals["raw"]
    assert paths["report"].read_bytes() == originals["report"]
    assert paths["review"].read_bytes() == originals["review"]
    assert all(path.read_bytes() == protected_bytes[path] for path in protected)

    audit_path = Path(accepted["decision_audit_path"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["domain"] == "gaza"
    assert audit["decision"] == "accept_substantively_valid_historical_candidate"
    assert audit["operator"] == "William Patton"
    assert audit["previous_review_status"] == "pending_review"
    assert audit["new_review_status"] == "substantively_reviewed"
    assert audit["historical_outcome"] == "new_historical_candidate"
    assert audit["event_type"] == "humanitarian_worker_injury"
    assert audit["gaza_role"] == "humanitarian_operations_and_safety"
    assert audit["event_date"] == "2026-07-25"
    assert audit["source_published_at"] == "2026-07-30"
    assert audit["materiality_assessment"] == "operating_impact_unclear"
    assert audit["operational_impact"] == "unknown"
    assert audit["publication_eligible"] is False
    assert audit["publication_approval"] is False
    assert audit["archive_content_change_authorized"] is False
    assert audit["edition_authorized"] is False
    assert audit["source_record_authorized"] is False
    assert audit["cluster_authorized"] is False
    assert audit["audio_authorized"] is False
    assert audit["publication_authorized"] is False
    assert audit["editorial_restrictions"] == json.loads(
        originals["review"].decode("utf-8")
    )["editorial_restrictions"]
    assert audit["changed_fields"] == ["findings[].review_status"]
    assert len(list(audit_path.parent.glob("*.json"))) == 1

    index_path = tmp_path / "data/agent-history/gaza/reports/history-index.json"
    no_op_hashes = {
        "normalized": hashlib.sha256(paths["normalized"].read_bytes()).hexdigest(),
        "audit": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "index": hashlib.sha256(index_path.read_bytes()).hexdigest(),
    }
    decided_at = audit["decided_at"]
    code, repeated = run_json(capsys, args)
    assert code == 0
    assert repeated["status"] == "idempotent_noop"
    assert repeated["inventory"]["pending_substantive_review"] == 0
    assert repeated["inventory"]["substantively_reviewed"] == 1
    assert repeated["inventory"]["queue_entries"] == 0
    assert repeated["inventory"]["publication_ready_count"] == 0
    assert hashlib.sha256(paths["normalized"].read_bytes()).hexdigest() == no_op_hashes["normalized"]
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == no_op_hashes["audit"]
    assert hashlib.sha256(index_path.read_bytes()).hexdigest() == no_op_hashes["index"]
    assert json.loads(audit_path.read_text(encoding="utf-8"))["decided_at"] == decided_at
    assert len(list(audit_path.parent.glob("*.json"))) == 1


def test_gaza_legacy_substantive_review_dry_run_writes_nothing(
    tmp_path: Path,
    capsys,
):
    args, paths = gaza_substantive_review_fixture(tmp_path)
    args.append("--dry-run")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths.values()
    }
    code, result = run_json(capsys, args)
    assert code == 0
    assert result["status"] == "dry_run_validated"
    assert result["persistent_mutation"] is False
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths.values()
    } == before
    assert not Path(result["decision_audit_path"]).exists()
    assert not (tmp_path / "data/agent-history/gaza/reports/history-index.json").exists()


def _set_nested(value: dict, path: tuple[str, ...], replacement: object) -> None:
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("normalized_finding_id",), "wrong-finding", "exactly one normalized finding"),
        (("recommended_disposition",), "retain_pending_review", "recommended_disposition"),
        (("edition_authorized",), True, "edition_authorized"),
        (("materiality_assessment", "assessment"), "context_only", "materiality_assessment"),
        (("operational_impact_assessment", "assessment"), "documented", "operational_impact"),
        (("date_assessment", "event_date"), "2026-07-30", "event_date"),
        (("date_assessment", "report_publication_date"), "2026-07-25", "source publication date"),
        (("attribution_assessment", "safe_future_wording"), "UNRWA confirmed an injury.", "qualified attribution"),
    ],
)
def test_gaza_substantive_review_fails_closed_on_review_mismatch(
    tmp_path: Path,
    path: tuple[str, ...],
    replacement: object,
    message: str,
):
    args, paths = gaza_substantive_review_fixture(tmp_path)
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    _set_nested(review, path, replacement)
    write_json(paths["review"], review)
    args[args.index("--review-artifact-sha256") + 1] = hashlib.sha256(
        paths["review"].read_bytes()
    ).hexdigest()
    normalized_before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match=message):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (tmp_path / "data/agent-history/gaza/reviews/decisions").exists()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("event_date", "2026-07-30", "event_date"),
        ("source_published_at", "2026-07-25", "source_published_at"),
        ("operational_impact", "documented", "operational_impact"),
    ],
)
def test_gaza_substantive_review_rejects_changed_incident_facts(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
):
    args, paths = gaza_substantive_review_fixture(tmp_path)
    normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    normalized["findings"][0][field] = replacement
    write_json(paths["normalized"], normalized)
    normalized_before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match=message):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (tmp_path / "data/agent-history/gaza/reviews/decisions").exists()


@pytest.mark.parametrize("match_type", ["source", "cluster"])
def test_gaza_substantive_review_rejects_new_authoritative_match(
    tmp_path: Path,
    match_type: str,
):
    args, paths = gaza_substantive_review_fixture(tmp_path)
    normalized_before = paths["normalized"].read_bytes()
    source_url = "https://www.unrwa.org/resources/reports/situation-report-231"
    if match_type == "source":
        write_json(
            tmp_path / "data/records/sources.json",
            [
                {
                    "dispatch_id": "dispatch-gaza",
                    "source_record_id": "gaza-source-231",
                    "url": source_url,
                }
            ],
        )
    else:
        write_json(
            tmp_path / "data/records/story_memory.json",
            [
                {
                    "dispatch_slug": "gaza",
                    "story_id": "gaza-story-231",
                    "source_urls": [source_url],
                }
            ],
        )

    with pytest.raises(ValueError, match="authoritative source or cluster"):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (tmp_path / "data/agent-history/gaza/reviews/decisions").exists()
