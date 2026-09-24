from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path

import pytest

from bluefern_dispatches import care_line_evidence_recovery as recovery


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _source(source_id: str = "test-source", *, allowed_hosts: list[str] | None = None, item_permalink_available: bool = True) -> dict:
    return {
        "source_id": source_id,
        "name": "Test Source",
        "publisher": "Test Source",
        "source_type": "local_publisher",
        "feed_url": "https://example.org/feed/",
        "homepage_url": "https://example.org/",
        "state": "PA",
        "geographic_scope": "state",
        "organization_type": "publisher",
        "care_line_topics": ["hospital closure"],
        "authority_level": "secondary",
        "expected_update_frequency": "daily",
        "enabled": True,
        "adapter_type": "rss",
        "requires_html_followup": False,
        "source_role": "healthcare_access_reporting",
        "historical_depth": "current feed",
        "created_at": "2026-09-23T00:00:00Z",
        "updated_at": "2026-09-23T00:00:00Z",
        "allowed_hosts": allowed_hosts or ["example.org"],
        "source_category": "local_health_reporting",
        "collection_method": "feed_polling",
        "searchability": "feed",
        "item_permalink_available": item_permalink_available,
    }


def _packet_record(
    producer_record_id: str = "care-line-raw-item_1",
    *,
    bucket: str = "additional_fetch_needed",
    url: str = "https://example.org/story",
    outcome: str = "PARTIAL_BODY",
    failed_gates: list[str] | None = None,
    source_id: str = "test-source",
) -> dict:
    return {
        "producer_record_id": producer_record_id,
        "raw_item_id": producer_record_id,
        "record_fingerprint": f"{producer_record_id}_fingerprint",
        "resolution_bucket": bucket,
        "canonical_source_url": url,
        "source_title": "Hospital will close emergency department in Erie",
        "source_metadata": {"source_id": source_id, "registry_entry_found": True},
        "source_publisher": "Test Source",
        "supporting_passage": "Hospital leaders said the emergency department will close in Erie, Pennsylvania.",
        "source_publication_date": "2026-09-23",
        "extraction_outcome": outcome,
        "exact_missing_requirements": failed_gates or ["missing_geography"],
        "source_artifact_paths": ["data/dispatches/care-line/collection-runs/test/test-source.raw-items.json"],
        "event_lead_id": "lead-1",
    }


def _repo(tmp_path: Path, *, records: list[dict] | None = None, sources: list[dict] | None = None) -> Path:
    root = tmp_path / "repo"
    _write_json(
        root / recovery.DEFAULT_REVIEW_PACKET_OUTPUT,
        {
            "schema_version": "bluefern.care_line.phase14b_evidence_review.v1",
            "packet_type": "current_care_line_evidence_review",
            "generator_version": "test",
            "source_review_root": "data/dispatches/care-line/review",
            "decision_policy": {},
            "records": records or [_packet_record()],
        },
    )
    _write_json(root / recovery.DEFAULT_REGISTRY_PATH, {"schema_version": "bluefern.care_line.source_registry.v1", "sources": sources or [_source()]})
    _write_json(root / "data/dispatches/care-line/reviewed/2026-09-23/reviewed_records.json", {"records": []})
    return root


def _packet(root: Path) -> dict:
    return json.loads((root / recovery.DEFAULT_REVIEW_PACKET_OUTPUT).read_text(encoding="utf-8"))


def _packet_fp(root: Path) -> str:
    return recovery.review_packet_fingerprint(_packet(root))


def _attempt(root: Path, *, packet_fingerprint: str | None = None, record_fingerprint: str = "care-line-raw-item_1_fingerprint", status: str = "QUALIFIED_PRIVATE_CANDIDATE", candidate: dict | None = None) -> dict:
    return {
        "schema_version": recovery.RECOVERY_SCHEMA_VERSION,
        "packet_fingerprint": packet_fingerprint or _packet_fp(root),
        "record_fingerprint": record_fingerprint,
        "producer_record_id": "care-line-raw-item_1",
        "result_status": status,
        "candidate": candidate or (_candidate() if status == "QUALIFIED_PRIVATE_CANDIDATE" else {}),
        "no_publication": True,
    }


def _candidate(candidate_id: str = "candidate-1") -> dict:
    return {
        "schema_version": "bluefern.care_line.national_pipeline.v2",
        "candidate_id": candidate_id,
        "source_id": "test-source",
        "source_name": "Test Source",
        "source_artifact_path": "data/dispatches/care-line/review/evidence-recovery/attempt.json",
        "collection_run_id": "care-line-evidence-recovery",
        "normalization_status": "normalized",
        "validation_errors": [],
        "duplicate_cluster_hints": {
            "cluster_id": "cluster-1",
            "dedupe_key": "dedupe-1",
            "same_system_cross_state_guard": "er|PA",
            "announcement_effective_link_key": "link-1",
        },
        "normalized_record": {
            "producer_record_id": "care-line-raw-item_1",
            "source_url": "https://example.org/story",
            "source_title": "Hospital will close emergency department in Erie",
            "source_publisher": "Test Source",
            "source_publication_date": "2026-09-23",
            "source_type": "local_publisher",
            "source_role": "healthcare_access_reporting",
            "supporting_passage": "Hospital leaders said the emergency department will close in Erie, Pennsylvania.",
            "effective_evidence_text": "Hospital leaders said the emergency department will close in Erie, Pennsylvania.",
            "raw_payload_hash": "hash-1",
            "event_type": "service_closure",
            "event_type_raw": "service_closure",
            "announcement_date": "2026-09-23",
            "service_line": "emergency_care",
            "service_line_raw": "emergency_care",
            "facility_name": "Test Hospital",
            "provider_name": "Test Hospital",
            "city": "Erie",
            "state": "PA",
            "jurisdiction_display": "Pennsylvania",
            "geographic_scope": "locality",
            "country_code": "US",
            "location_text": "Test Hospital, Erie, Pennsylvania",
            "claim_summary": "Emergency department will close.",
            "access_consequences": ["LOSS_OF_LOCAL_ACCESS"],
            "authority_level": "secondary",
            "review_status": "not_reviewed",
            "record_status": "needs_normalization_review",
            "public_status": "not_public",
            "universal_event_status": "needs_normalization_review",
            "evidence_level": "article_excerpt",
            "evidence_provenance_type": "source_explicit",
            "evidence_valid_for_universal_event": True,
            "metadata": {"qualification_status": "qualified"},
        },
        "qualification_result": {
            "qualification_status": "qualified",
            "review_priority_recommendation": "HIGH",
            "freshness_role": "CURRENT",
            "currentness_class": "CURRENT_EVENT",
            "editorial_outcome": "QUALIFIED",
        },
        "first_seen": "2026-09-23",
        "last_seen": "2026-09-23",
    }


def _stub_success(monkeypatch: pytest.MonkeyPatch, candidate: dict | None = None) -> None:
    monkeypatch.setattr(recovery, "fetch_url", lambda *args, **kwargs: (b"<article>Hospital will close emergency department in Erie, Pennsylvania.</article>", {"http_status": 200, "content_type": "text/html"}))
    monkeypatch.setattr(
        recovery,
        "_extract_article_content",
        lambda *args, **kwargs: {
            "title": "Hospital will close emergency department in Erie",
            "description": "",
            "text": "Hospital leaders said the emergency department will close in Erie, Pennsylvania.",
            "published_at": "2026-09-23",
            "extraction_outcome": "BODY_EXTRACTED",
            "content_hash": "article-hash",
        },
    )
    monkeypatch.setattr(recovery, "qualify_event_lead", lambda *args, **kwargs: ("qualified", candidate or _candidate()))


def test_additional_fetch_item_refetches_and_qualifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path)
    _stub_success(monkeypatch)

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["qualified_candidate_count"] == 1
    assert result["attempts"][0]["result_status"] == "QUALIFIED_PRIVATE_CANDIDATE"
    assert result["attempts"][0]["no_publication"] is True


def test_qualified_recovery_enters_candidate_registry_once(tmp_path: Path):
    root = _repo(tmp_path)
    result = {"attempts": [_attempt(root)]}

    first = recovery.apply_recovery(repo_root=root, recovery_result=result, edition_date="2026-09-23", expected_packet_fingerprint=_packet_fp(root))
    second = recovery.apply_recovery(repo_root=root, recovery_result=result, edition_date="2026-09-23", expected_packet_fingerprint=_packet_fp(root))

    registry = json.loads((root / recovery.DEFAULT_CANDIDATE_REGISTRY).read_text(encoding="utf-8"))
    assert first["created_this_run"] == 1
    assert second["registry_candidate_count"] == 1
    assert len(registry["candidates"]) == 1


def test_review_queue_rebuild_includes_recovered_candidate_once(tmp_path: Path):
    root = _repo(tmp_path)

    applied = recovery.apply_recovery(
        repo_root=root,
        recovery_result={"attempts": [_attempt(root)]},
        edition_date="2026-09-23",
        expected_packet_fingerprint=_packet_fp(root),
    )

    queue = json.loads((root / recovery.DEFAULT_REVIEW_QUEUE).read_text(encoding="utf-8"))
    assert applied["queue_item_count"] == 1
    assert [item["candidate_id"] for item in queue["items"]] == ["candidate-1"]


def test_repeated_recovery_is_idempotent_for_same_day_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path)
    calls = {"count": 0}

    def fetch_once(*args, **kwargs):
        calls["count"] += 1
        return b"<article>Hospital will close emergency department in Erie, Pennsylvania.</article>", {"http_status": 200, "content_type": "text/html"}

    _stub_success(monkeypatch)
    monkeypatch.setattr(recovery, "fetch_url", fetch_once)

    first = recovery.recover_records(repo_root=root, force=False)
    second = recovery.recover_records(repo_root=root, force=False)

    assert first["attempt_count"] == 1
    assert second["result_counts"] == {"skipped_existing_attempt": 1}
    assert calls["count"] == 1


def test_403_remains_unresolved_and_is_not_promoted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path, records=[_packet_record(outcome="PARTIAL_BODY")])

    def blocked(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.org/story", 403, "Forbidden", None, None)

    monkeypatch.setattr(recovery, "fetch_url", blocked)
    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["qualified_candidate_count"] == 0
    assert result["attempts"][0]["result_status"] == "FETCH_FAILED"
    assert result["attempts"][0]["http_failure_class"] == "HTTP_403"


def test_timeout_remains_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path, records=[_packet_record(outcome="PARTIAL_BODY")])
    monkeypatch.setattr(recovery, "fetch_url", lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout("timed out")))

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["qualified_candidate_count"] == 0
    assert result["attempts"][0]["result_status"] == "FETCH_FAILED"
    assert result["attempts"][0]["http_failure_class"] == "TIMEOUT"


def test_parse_failure_that_succeeds_on_retry_can_recover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path, records=[_packet_record(outcome="PARSE_FAILED")])
    _stub_success(monkeypatch)

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["route"] == "parser_extraction_retry"
    assert result["attempts"][0]["result_status"] == "QUALIFIED_PRIVATE_CANDIDATE"


def test_fetched_article_that_proves_non_care_becomes_deterministic_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(recovery, "fetch_url", lambda *args, **kwargs: (b"<article>Biography announcement.</article>", {"http_status": 200, "content_type": "text/html"}))
    monkeypatch.setattr(recovery, "_extract_article_content", lambda *args, **kwargs: {"text": "Biography announcement.", "published_at": "2026-09-23", "extraction_outcome": "BODY_EXTRACTED", "content_hash": "x"})
    monkeypatch.setattr(recovery, "qualify_event_lead", lambda *args, **kwargs: ("excluded", {"exclusion_reason": "hiring_or_biography"}))

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["result_status"] == "DETERMINISTIC_EXCLUSION"


def test_recovered_evidence_missing_geography_remains_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path)
    _stub_success(monkeypatch)
    monkeypatch.setattr(recovery, "qualify_event_lead", lambda *args, **kwargs: ("failed_extraction", {"exclusion_reason": "needs_geography", "failed_gates": ["missing_geography"]}))

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["result_status"] == "STILL_ADDITIONAL_FETCH_NEEDED"
    assert result["qualified_candidate_count"] == 0


def test_deterministic_source_date_resolution_works_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path, records=[_packet_record(bucket="deterministic_resolution", outcome="BODY_EXTRACTED")])
    monkeypatch.setattr(recovery, "fetch_url", lambda *args, **kwargs: pytest.fail("deterministic route must not fetch"))
    monkeypatch.setattr(recovery, "qualify_event_lead", lambda *args, **kwargs: ("qualified", _candidate()))

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["route"] == "deterministic_existing_evidence"
    assert result["attempts"][0]["result_status"] == "QUALIFIED_PRIVATE_CANDIDATE"


def test_stale_packet_or_record_fingerprint_fails_closed(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="stale packet fingerprint"):
        recovery.recover_records(repo_root=root, write_attempts=False, expected_packet_fingerprint="not-current")
    with pytest.raises(ValueError, match="stale record fingerprint"):
        recovery.recover_records(repo_root=root, write_attempts=False, expected_record_fingerprints=["not-current"])


def test_url_outside_permitted_care_host_fails_closed(tmp_path: Path):
    root = _repo(
        tmp_path,
        records=[_packet_record(url="https://outside.example/story")],
        sources=[_source(allowed_hosts=["example.org"])],
    )

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["route"] == "stale_or_invalid_url"
    assert result["attempts"][0]["result_status"] == "NOT_RECOVERABLE_WITH_CURRENT_SOURCE"


def test_no_publication_state_mutation(tmp_path: Path):
    root = _repo(tmp_path)
    publication = root / "data/universal_events/publication-state/care-line-reviewed-event-queue.json"
    _write_json(publication, {"sentinel": "do-not-touch"})
    before = publication.read_text(encoding="utf-8")

    applied = recovery.apply_recovery(
        repo_root=root,
        recovery_result={"attempts": [_attempt(root)]},
        edition_date="2026-09-23",
        expected_packet_fingerprint=_packet_fp(root),
    )

    assert applied["publication_state_mutated"] is False
    assert publication.read_text(encoding="utf-8") == before


def test_no_approval_or_universal_event_ready_mutation(tmp_path: Path):
    root = _repo(tmp_path)

    applied = recovery.apply_recovery(
        repo_root=root,
        recovery_result={"attempts": [_attempt(root)]},
        edition_date="2026-09-23",
        expected_packet_fingerprint=_packet_fp(root),
    )

    assert applied["approved_count"] == 0
    assert applied["universal_event_ready_count"] == 0


def test_current_packet_attempts_apply_normally(tmp_path: Path):
    root = _repo(tmp_path)

    result = recovery.apply_recovery(
        repo_root=root,
        recovery_result={"attempts": [_attempt(root)]},
        expected_packet_fingerprint=_packet_fp(root),
        edition_date="2026-09-23",
    )

    assert result["active_review_state_mutated"] is True
    assert result["candidate_count"] == 1


def test_old_packet_attempt_cannot_apply(tmp_path: Path):
    root = _repo(tmp_path)

    with pytest.raises(ValueError, match="mixed or stale"):
        recovery.apply_recovery(
            repo_root=root,
            recovery_result={"attempts": [_attempt(root, packet_fingerprint="old-packet")]},
            expected_packet_fingerprint=_packet_fp(root),
        )


def test_mixed_current_and_old_attempts_fail_closed(tmp_path: Path):
    root = _repo(tmp_path)

    with pytest.raises(ValueError, match="mixed or stale"):
        recovery.apply_recovery(
            repo_root=root,
            recovery_result={"attempts": [_attempt(root), _attempt(root, packet_fingerprint="old-packet")]},
            expected_packet_fingerprint=_packet_fp(root),
        )


def test_stale_record_fingerprint_cannot_apply(tmp_path: Path):
    root = _repo(tmp_path)

    with pytest.raises(ValueError, match="stale recovery attempt record fingerprint"):
        recovery.apply_recovery(
            repo_root=root,
            recovery_result={"attempts": [_attempt(root, record_fingerprint="old-record")]},
            expected_packet_fingerprint=_packet_fp(root),
        )


def test_apply_without_expected_current_packet_fingerprint_fails_closed(tmp_path: Path):
    root = _repo(tmp_path)

    with pytest.raises(ValueError, match="expected current packet fingerprint is required"):
        recovery.apply_recovery(repo_root=root, recovery_result={"attempts": [_attempt(root)]})


def test_cli_check_only_apply_fails_without_mutation(tmp_path: Path):
    root = _repo(tmp_path)

    assert recovery.main(["--repo-root", str(root), "--check-only", "--apply", "--expected-packet-fingerprint", _packet_fp(root)]) == 2
    assert not (root / recovery.DEFAULT_CANDIDATE_REGISTRY).exists()


def test_cli_recover_apply_fails_without_mutation(tmp_path: Path):
    root = _repo(tmp_path)

    assert recovery.main(["--repo-root", str(root), "--recover", "--apply", "--expected-packet-fingerprint", _packet_fp(root)]) == 2
    assert not (root / recovery.DEFAULT_CANDIDATE_REGISTRY).exists()


def test_zero_qualified_apply_leaves_active_review_state_unchanged(tmp_path: Path):
    root = _repo(tmp_path)
    paths = [
        root / recovery.DEFAULT_CANDIDATE_REGISTRY,
        root / recovery.DEFAULT_REVIEW_QUEUE,
        root / recovery.DEFAULT_REVIEW_BACKLOG,
        root / recovery.DEFAULT_REVIEW_DUPLICATES,
    ]
    for index, path in enumerate(paths):
        _write_json(path, {"sentinel": index})
    before = {path: path.read_bytes() for path in paths}

    result = recovery.apply_recovery(
        repo_root=root,
        recovery_result={"attempts": [_attempt(root, status="DETERMINISTIC_EXCLUSION")]},
        expected_packet_fingerprint=_packet_fp(root),
    )

    assert result["candidate_count"] == 0
    assert result["active_review_state_mutated"] is False
    assert {path: path.read_bytes() for path in paths} == before


def test_production_force_override_unavailable(tmp_path: Path):
    root = _repo(tmp_path)

    with pytest.raises(ValueError, match="forced retry is unavailable"):
        recovery.recover_records(repo_root=root, force=True)
    with pytest.raises(SystemExit) as excinfo:
        recovery.main(["--repo-root", str(root), "--recover", "--force"])
    assert excinfo.value.code == 2


def test_disabled_source_does_not_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = _source()
    source["enabled"] = False
    root = _repo(tmp_path, sources=[source])
    monkeypatch.setattr(recovery, "fetch_url", lambda *args, **kwargs: pytest.fail("disabled source must not fetch"))

    result = recovery.recover_records(repo_root=root, write_attempts=False)

    assert result["attempts"][0]["route"] == "not_recoverable_with_current_source"
    assert result["attempts"][0]["result_status"] == "NOT_RECOVERABLE_WITH_CURRENT_SOURCE"
